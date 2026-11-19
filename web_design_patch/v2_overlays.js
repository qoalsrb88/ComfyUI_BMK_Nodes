// BMK Design Patch Studio v2 — 50/50 시트 오리기 & 4점 원근 · 풀스크린 비교 · 히스토리 · 설정/PSD/단축키/프로젝트 홈.
"use strict";

window.DPV2Overlays = (() => {
  const Data = window.DPV2Data;
  const Api = window.DPV2Api;
  const Store = window.DPV2Store;
  const Canvas = window.DPV2Canvas;

  const $ = (id) => document.getElementById(id);

  // ── 공용 모달 헬퍼 ──
  function openModal({ title, bodyNodes = [], actions = [] }) {
    const bd = $("modal-backdrop");
    if (!bd) return;
    $("modal-title").textContent = title || "";
    const body = $("modal-body");
    body.innerHTML = "";
    for (const n of bodyNodes) if (n) body.append(n);
    const foot = $("modal-foot");
    foot.innerHTML = "";
    for (const a of actions) {
      foot.append(Data.el("button", {
        type: "button",
        className: a.cls || "btn-secondary",
        text: a.label,
        onClick: a.onClick,
      }));
    }
    bd.classList.remove("hidden");
  }

  function closeModal() {
    const bd = $("modal-backdrop");
    if (bd) bd.classList.add("hidden");
  }

  function loadImage(src) {
    return new Promise((resolve, reject) => {
      const img = new Image();
      img.onload = () => resolve(img);
      img.onerror = (err) => reject(err);
      img.src = src;
    });
  }

  // ==================== 1. 50/50 분할 레퍼런스 시트 오리기 & 4점 원근 (버그 1 영구 해결) ====================
  const sheetState = {
    open: false,
    sheetPath: "",
    sheetImg: null,
    cutRect: { x: 0.18, y: 0.16, w: 0.36, h: 0.42 }, // 정규화 0~1
    quad: [
      { x: 0.16, y: 0.16 },
      { x: 0.84, y: 0.16 },
      { x: 0.84, y: 0.84 },
      { x: 0.16, y: 0.84 },
    ],
    flipX: false,
    alpha: 0.65,
    zoom: 1,
    panX: 0,
    panY: 0,
    maximized: false,
    drag: null,
    lastCutReq: null,
  };

  function defaultQuad() {
    return [
      { x: 0.16, y: 0.16 },
      { x: 0.84, y: 0.16 },
      { x: 0.84, y: 0.84 },
      { x: 0.16, y: 0.84 },
    ];
  }

  function applySheetTransform() {
    const tf = $("sheet-source-transform");
    const val = $("sheet-zoom-val");
    if (tf) tf.style.transform = `translate(${Math.round(sheetState.panX)}px, ${Math.round(sheetState.panY)}px) scale(${sheetState.zoom})`;
    if (val) val.textContent = `${Math.round(sheetState.zoom * 100)}%`;
  }

  function fitSheetToView() {
    const img = sheetState.sheetImg;
    const wrap = $("sheet-source-wrap");
    if (!img || !wrap) return;
    const vw = wrap.clientWidth || 560;
    const vh = wrap.clientHeight || 560;
    const pad = 44;
    const s = Math.min((vw - pad) / img.naturalWidth, (vh - pad) / img.naturalHeight, 1.0);
    sheetState.zoom = Math.max(0.15, s);
    sheetState.panX = Math.round((vw - img.naturalWidth * sheetState.zoom) / 2);
    sheetState.panY = Math.round((vh - img.naturalHeight * sheetState.zoom) / 2);
    applySheetTransform();
  }

  function setSheetZoomAt(nextZoom, anchorX = null, anchorY = null) {
    const img = sheetState.sheetImg;
    const wrap = $("sheet-source-wrap");
    if (!img || !wrap) return;
    const vw = wrap.clientWidth || 560;
    const vh = wrap.clientHeight || 560;
    const ax = anchorX ?? vw / 2;
    const ay = anchorY ?? vh / 2;
    const clamped = Math.max(0.15, Math.min(6.0, nextZoom));
    sheetState.panX = ax - ((ax - sheetState.panX) / sheetState.zoom) * clamped;
    sheetState.panY = ay - ((ay - sheetState.panY) / sheetState.zoom) * clamped;
    sheetState.zoom = clamped;
    applySheetTransform();
    drawSheetSource();
  }

  function drawSheetSource() {
    const cv = $("sheet-source-canvas");
    const emptyEl = $("sheet-source-empty");
    const img = sheetState.sheetImg;
    if (!cv) return;
    if (!img) {
      cv.width = 0; cv.height = 0;
      if (emptyEl) emptyEl.classList.remove("hidden");
      return;
    }
    if (emptyEl) emptyEl.classList.add("hidden");
    if (cv.width !== img.naturalWidth || cv.height !== img.naturalHeight) {
      cv.width = img.naturalWidth;
      cv.height = img.naturalHeight;
    }
    const ctx = cv.getContext("2d");
    ctx.clearRect(0, 0, cv.width, cv.height);
    ctx.drawImage(img, 0, 0);

    const r = sheetState.cutRect;
    const rx = r.x * cv.width, ry = r.y * cv.height;
    const rw = r.w * cv.width, rh = r.h * cv.height;

    ctx.fillStyle = "rgba(0, 0, 0, 0.52)";
    ctx.fillRect(0, 0, cv.width, ry);
    ctx.fillRect(0, ry + rh, cv.width, cv.height - (ry + rh));
    ctx.fillRect(0, ry, rx, rh);
    ctx.fillRect(rx + rw, ry, cv.width - (rx + rw), rh);

    ctx.strokeStyle = Store.activeAccentHex();
    ctx.lineWidth = Math.max(2, 2.2 / Math.max(0.2, sheetState.zoom));
    ctx.strokeRect(rx, ry, rw, rh);

    const szEl = $("sheet-cut-size");
    if (szEl) {
      szEl.textContent = `선택 영역: ${Math.round(r.w * img.naturalWidth)} × ${Math.round(r.h * img.naturalHeight)} px`;
    }
  }

  function drawTexturedTriangle(ctx, img, u0, v0, u1, v1, u2, v2, x0, y0, x1, y1, x2, y2) {
    ctx.save();
    ctx.beginPath();
    ctx.moveTo(x0, y0);
    ctx.lineTo(x1, y1);
    ctx.lineTo(x2, y2);
    ctx.closePath();
    ctx.clip();

    const denom = u0 * (v1 - v2) + u1 * (v2 - v0) + u2 * (v0 - v1);
    if (Math.abs(denom) > 0.0001) {
      const m11 = (x0 * (v1 - v2) + x1 * (v2 - v0) + x2 * (v0 - v1)) / denom;
      const m12 = (x0 * (u2 - u1) + x1 * (u0 - u2) + x2 * (u1 - u0)) / denom;
      const m13 = (x0 * (u1 * v2 - u2 * v1) + x1 * (u2 * v0 - u0 * v2) + x2 * (u0 * v1 - u1 * v0)) / denom;
      const m21 = (y0 * (v1 - v2) + y1 * (v2 - v0) + y2 * (v0 - v1)) / denom;
      const m22 = (y0 * (u2 - u1) + y1 * (u0 - u2) + y2 * (u1 - u0)) / denom;
      const m23 = (y0 * (u1 * v2 - u2 * v1) + y1 * (u2 * v0 - u0 * v2) + y2 * (u0 * v1 - u1 * v0)) / denom;
      ctx.transform(m11, m21, m12, m22, m13, m23);
      ctx.drawImage(img, 0, 0);
    }
    ctx.restore();
  }

  function drawSheetWarp() {
    const cv = $("sheet-warp-canvas");
    const emptyEl = $("sheet-warp-empty");
    if (!cv) return;
    const w = 480, h = 480;
    if (cv.width !== w || cv.height !== h) { cv.width = w; cv.height = h; }
    const ctx = cv.getContext("2d");
    ctx.fillStyle = "#0c0c10";
    ctx.fillRect(0, 0, w, h);

    const img = sheetState.sheetImg;
    if (!img) {
      if (emptyEl) emptyEl.classList.remove("hidden");
      return;
    }
    if (emptyEl) emptyEl.classList.add("hidden");

    // 1) 배경: 대상 패치 원화 크롭
    const cropSel = $("sheet-target-crop");
    const patch = (cropSel && Store.getPatch(cropSel.value)) || Store.primaryPatch() || Store.getPatches()[0];
    const baseImg = Canvas.getBaseImage();
    if (patch && baseImg && baseImg.naturalWidth && baseImg.naturalHeight) {
      const imgW = Canvas.view.imgW || baseImg.naturalWidth;
      const imgH = Canvas.view.imgH || baseImg.naturalHeight;
      const [x1, y1, x2, y2] = patch.rect;
      const sx = (x1 / imgW) * baseImg.naturalWidth;
      const sy = (y1 / imgH) * baseImg.naturalHeight;
      const sw = Math.max(8, ((x2 - x1) / imgW) * baseImg.naturalWidth);
      const sh = Math.max(8, ((y2 - y1) / imgH) * baseImg.naturalHeight);
      ctx.drawImage(baseImg, sx, sy, sw, sh, 24, 24, w - 48, h - 48);
    }

    // 2) 오려낸 시트 조각을 4점 사각형에 2삼각형 아핀 투영
    const r = sheetState.cutRect;
    const sx = r.x * img.naturalWidth;
    const sy = r.y * img.naturalHeight;
    const sw = Math.max(12, r.w * img.naturalWidth);
    const sh = Math.max(12, r.h * img.naturalHeight);

    const cutC = document.createElement("canvas");
    cutC.width = 256;
    cutC.height = 256;
    const cctx = cutC.getContext("2d");
    if (sheetState.flipX) {
      cctx.translate(256, 0);
      cctx.scale(-1, 1);
    }
    cctx.drawImage(img, sx, sy, sw, sh, 0, 0, 256, 256);

    const q = sheetState.quad.map((pt) => ({ x: pt.x * w, y: pt.y * h }));
    ctx.save();
    ctx.globalAlpha = sheetState.alpha;
    drawTexturedTriangle(ctx, cutC, 0, 0, 256, 0, 256, 256, q[0].x, q[0].y, q[1].x, q[1].y, q[2].x, q[2].y);
    drawTexturedTriangle(ctx, cutC, 0, 0, 256, 256, 0, 256, q[0].x, q[0].y, q[2].x, q[2].y, q[3].x, q[3].y);
    ctx.restore();

    // 3) 4점 와이어프레임 & 드래그 꼭짓점 핸들 (1~4)
    const accent = Store.activeAccentHex();
    ctx.strokeStyle = accent;
    ctx.lineWidth = 1.8;
    ctx.beginPath();
    ctx.moveTo(q[0].x, q[0].y);
    ctx.lineTo(q[1].x, q[1].y);
    ctx.lineTo(q[2].x, q[2].y);
    ctx.lineTo(q[3].x, q[3].y);
    ctx.closePath();
    ctx.stroke();

    q.forEach((pt, idx) => {
      ctx.fillStyle = accent;
      ctx.strokeStyle = "#09090b";
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.arc(pt.x, pt.y, 8, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();
      ctx.fillStyle = "#09090b";
      ctx.font = "bold 10px sans-serif";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(String(idx + 1), pt.x, pt.y + 0.5);
    });
  }

  async function loadSelectedSheet(sheetPath) {
    const sheets = Store.state.sheets || [];
    const sh = sheets.find((s) => s.path === sheetPath) || sheets[0] || null;
    if (!sh) {
      sheetState.sheetPath = "";
      sheetState.sheetImg = null;
      drawSheetSource();
      drawSheetWarp();
      return;
    }
    sheetState.sheetPath = sh.path;
    const sel = $("sheet-select");
    if (sel) sel.value = sh.path;
    try {
      const url = Api.sheetUrl(Store.state.root, sh, "preview");
      sheetState.sheetImg = await loadImage(url);
      fitSheetToView();
      drawSheetSource();
      drawSheetWarp();
    } catch (err) {
      Store.toast(`시트 이미지를 불러오지 못했습니다: ${err.message}`, "err");
    }
  }

  async function openSheetOverlay(preferredPath = "") {
    const ov = $("sheet-overlay");
    if (!ov) return;
    sheetState.open = true;
    sheetState.maximized = false;
    $("sheet-split-body").classList.remove("maximized");
    $("btn-sheet-max").textContent = "⤢ 최대화";
    ov.classList.remove("hidden");

    await Store.loadRefs(true);

    // 시트 드롭다운 채우기
    const shSel = $("sheet-select");
    if (shSel) {
      shSel.innerHTML = "";
      if (!Store.state.sheets.length) {
        shSel.append(Data.el("option", { value: "", text: "(시트 없음 · 업로드 필요)" }));
      } else {
        for (const s of Store.state.sheets) {
          shSel.append(Data.el("option", { value: s.path, text: s.name || s.path }));
        }
      }
    }

    // 대상 패치 드롭다운 채우기
    const cropSel = $("sheet-target-crop");
    if (cropSel) {
      cropSel.innerHTML = "";
      const patches = Store.getPatches();
      const prim = Store.primaryPatch();
      for (const p of patches) {
        const opt = Data.el("option", { value: p.id, text: `${p.index}. ${p.name}` });
        if (prim && prim.id === p.id) opt.selected = true;
        cropSel.append(opt);
      }
    }

    await loadSelectedSheet(preferredPath || sheetState.sheetPath);
    requestAnimationFrame(() => {
      fitSheetToView();
      drawSheetSource();
      drawSheetWarp();
    });
  }

  function closeSheetOverlay() {
    sheetState.open = false;
    const ov = $("sheet-overlay");
    if (ov) ov.classList.add("hidden");
  }

  // 우측 4점 캔버스 좌표(기본 틀 0.16..0.84)를 서버 check_corners 규격([[u,v]×4], 항등=[[0,0],[1,0],[1,1],[0,1]])로 변환
  function quadToServerCorners(quad) {
    if (!Array.isArray(quad) || quad.length !== 4) return { corners: null };
    const id = [[0, 0], [1, 0], [1, 1], [0, 1]];
    const pts = quad.map((pt) => [
      Math.floor(((pt.x - 0.16) / 0.68) * 10000 + 0.5) / 10000,
      Math.floor(((pt.y - 0.16) / 0.68) * 10000 + 0.5) / 10000,
    ]);
    if (pts.every((p, i) => Math.abs(p[0] - id[i][0]) < 0.0005 && Math.abs(p[1] - id[i][1]) < 0.0005)) {
      return { corners: null };
    }
    if (pts.some((p) => p[0] < -1 || p[0] > 2 || p[1] < -1 || p[1] > 2)) {
      return { error: "원근 꼭짓점이 허용 범위를 벗어났습니다 — 안쪽으로 조정해 주세요." };
    }
    for (let k = 0; k < 4; k++) {
      const a = pts[k], b = pts[(k + 1) % 4], c = pts[(k + 2) % 4];
      if ((b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0]) <= 0) {
        return { error: "원근 4점이 꼬여 있습니다 — 볼록한 사각형 형태로 맞춰 주세요." };
      }
    }
    const area = pts.reduce((s, p, k) => {
      const q = pts[(k + 1) % 4];
      return s + (p[0] * q[1] - p[1] * q[0]);
    }, 0) / 2;
    if (area < 0.02) {
      return { error: "원근 영역 넓이가 너무 작습니다 — 꼭짓점을 넓혀 주세요." };
    }
    return { corners: pts };
  }

  async function saveSheetCut(attachToCrop) {
    const img = sheetState.sheetImg;
    if (!img || !sheetState.sheetPath) {
      Store.toast("먼저 시트 이미지를 업로드해 주세요.");
      return;
    }
    const r = sheetState.cutRect;
    const rectPx = [
      Math.round(r.x * img.naturalWidth),
      Math.round(r.y * img.naturalHeight),
      Math.round((r.x + r.w) * img.naturalWidth),
      Math.round((r.y + r.h) * img.naturalHeight),
    ];
    if (Math.min(rectPx[2] - rectPx[0], rectPx[3] - rectPx[1]) < 16) {
      Store.toast("오릴 영역의 가로·세로는 최소 16px 이상이어야 합니다.", "err");
      return;
    }
    const cornerCheck = quadToServerCorners(sheetState.quad);
    if (cornerCheck.error) {
      Store.toast(cornerCheck.error, "err");
      return;
    }
    const cropSel = $("sheet-target-crop");
    const roleSel = $("sheet-role-select");
    const cropId = attachToCrop && cropSel ? cropSel.value : "";
    const rawRole = roleSel ? roleSel.value : "main";
    const role = rawRole === "aux" || rawRole === "sub" ? "aux" : "main";
    const curSheet = (Store.state.sheets || []).find((s) => s.path === sheetState.sheetPath);

    try {
      const cutBody = {
        sheet: sheetState.sheetPath,
        rect: rectPx,
        method: "rect",
      };
      if (curSheet && curSheet.v) cutBody.sheet_v = curSheet.v;
      const cutRes = await Api.cutRef(Store.state.root, cutBody);
      if (!cutRes || !cutRes.req) {
        throw new Error("시트 오리기 요청 ID를 받지 못했습니다.");
      }

      const transform = {
        rotate: 0,
        flip_h: !!sheetState.flipX,
        scale: 1.0,
      };
      if (cornerCheck.corners) transform.corners = cornerCheck.corners;

      const saveBody = { req: cutRes.req, transform };
      if (cropId) {
        saveBody.crop_id = cropId;
        saveBody.role = role;
        if (role === "aux") saveBody.desc = `cut_${Date.now().toString(36).slice(-4)}`;
      } else {
        saveBody.desc = "";
      }

      const sendSave = async (body) => {
        try {
          return await Api.saveRef(Store.state.root, Store.state.R?.rev ?? null, body);
        } catch (err) {
          const ex = err && err.status === 409 && err.data && Array.isArray(err.data.explicit) ? err.data.explicit : null;
          if (!ex || !ex.length) throw err;
          const ok = await Data.askDialogConfirm(
            `${err.message || "기존 레퍼런스를 교체합니다."}\n계속 저장할까요?`,
            { ok: "저장", cancel: "취소" }
          );
          if (!ok) return { cancelled: true };
          return Api.saveRef(Store.state.root, Store.state.R?.rev ?? null, Object.assign({}, body, { confirm: ex.map((x) => x.file) }));
        }
      };

      const saved = await sendSave(saveBody);
      if (saved && saved.cancelled) return;
      closeSheetOverlay();
      Store.toast(cropId ? "시트 컷을 저장하고 선택한 영역에 연결했습니다." : "시트 컷을 레퍼런스 보관함에 저장했습니다.", "ok");
      await Store.refreshAll(true);
    } catch (err) {
      Store.toast(`시트 컷 저장 실패: ${err.message}`, "err");
    }
  }

  // ==================== 2. 풀스크린 정밀 비교 (46px 확장 디바이더) ====================
  const cmpState = {
    open: false,
    candKey: "",
    split: 0.5,
    blinkOrig: false,
    zoom: 1,
    dragging: false,
  };

  async function openCompareOverlay(candKey = "") {
    const patch = Store.primaryPatch();
    if (!patch || !patch.candidates.length) {
      Store.toast("비교할 생성 후보가 아직 없습니다.");
      return;
    }
    const cur = Store.currentCandidate(patch);
    cmpState.open = true;
    cmpState.candKey = candKey || (cur && cur.key) || patch.candidates[0].key;
    cmpState.split = 0.5;
    cmpState.blinkOrig = false;
    cmpState.zoom = 1;
    $("compare-overlay").classList.remove("hidden");
    await renderCompareOverlay();
  }

  function closeCompareOverlay() {
    cmpState.open = false;
    $("compare-overlay").classList.add("hidden");
  }

  function updateCompareDivider() {
    const pct = cmpState.blinkOrig ? 100 : Math.round(cmpState.split * 1000) / 10;
    const clip = $("co-before-clip");
    const div = $("co-divider");
    if (clip) clip.style.clipPath = `inset(0 ${100 - pct}% 0 0)`;
    if (div) {
      div.style.left = `${pct}%`;
      div.classList.toggle("hidden", cmpState.blinkOrig);
    }
  }

  async function renderCompareOverlay() {
    const patch = Store.primaryPatch();
    if (!patch || !cmpState.open) return;
    const cand = patch.candidates.find((c) => c.key === cmpState.candKey) || patch.candidates[0];
    if (!cand) return;
    cmpState.candKey = cand.key;

    $("co-title").textContent = `${patch.name} — 후보 #${cand.n || 1} 정밀 비교`;
    $("co-label-after").textContent = `후보 #${cand.n || 1} (${cand.variant || "V1"})`;
    $("co-zoom-reset").textContent = `${Math.round(cmpState.zoom * 100)}%`;

    const beforeRel = (patch.target && patch.target.before_url) || (Store.state.S && Store.state.S.canvas && Store.state.S.canvas.base_url);
    const afterRel = cand.big_url || cand.pick_url || cand.thumb_url;
    const boxSize = Math.min(720, window.innerHeight - 160, window.innerWidth - 220);

    try {
      const [bImg, aImg] = await Promise.all([
        loadImage(Api.apiUrl(beforeRel)),
        loadImage(Api.apiUrl(afterRel)),
      ]);
      const aspect = (aImg.naturalWidth || 1) / Math.max(1, aImg.naturalHeight || 1);
      const cw = aspect >= 1 ? boxSize : Math.round(boxSize * aspect);
      const ch = aspect >= 1 ? Math.round(boxSize / aspect) : boxSize;

      const cBefore = $("co-canvas-before"), cAfter = $("co-canvas-after");
      cBefore.width = cw; cBefore.height = ch;
      cAfter.width = cw; cAfter.height = ch;
      cBefore.getContext("2d").drawImage(bImg, 0, 0, cw, ch);
      cAfter.getContext("2d").drawImage(cmpState.blinkOrig ? bImg : aImg, 0, 0, cw, ch);

      const cbox = $("co-compare-box");
      cbox.style.width = `${cw}px`;
      cbox.style.height = `${ch}px`;
      cbox.style.transform = `scale(${cmpState.zoom})`;
      updateCompareDivider();
    } catch (_) {}

    // 우측 후보 썸네일 레일
    const rail = $("co-rail");
    if (rail) {
      rail.innerHTML = "";
      patch.candidates.forEach((c, idx) => {
        const picked = !!(patch.pickedCand && patch.pickedCand.key === c.key);
        const thumb = Api.apiUrl(c.thumb_url || c.big_url || "");
        rail.append(Data.el("button", {
          type: "button",
          className: "co-rail-item" + (c.key === cand.key ? " on" : ""),
          onClick: () => { cmpState.candKey = c.key; renderCompareOverlay(); },
        },
          thumb ? Data.el("img", { src: thumb, alt: "" }) : null,
          Data.el("span", { text: `${idx + 1}. ${picked ? "★ " : ""}${c.variant || `후보 ${idx + 1}`}` }),
        ));
      });
    }
  }

  // ==================== 3. 히스토리 드로어 ====================
  function renderHistoryDrawer() {
    const list = $("history-list");
    if (!list) return;
    list.innerHTML = "";
    const hist = Store.state.history;
    if (!hist.length) {
      list.append(Data.el("li", { className: "vault-empty", text: "아직 기록된 편집 이력이 없습니다." }));
      return;
    }
    hist.forEach((h, idx) => {
      const cls = "history-item" + (idx === Store.state.histIndex ? " current" : idx > Store.state.histIndex ? " future" : "");
      list.append(Data.el("li", {
        className: cls,
        onClick: () => Store.jumpHistory(idx),
      },
        Data.el("span", { className: "history-step-num", text: `#${idx + 1}` }),
        Data.el("span", { className: "history-label", text: h.label }),
        Data.el("span", { className: "history-time", text: h.time }),
      ));
    });
  }

  function toggleHistoryDrawer() {
    const dr = $("history-drawer");
    if (!dr) return;
    dr.classList.toggle("hidden");
    const btn = $("btn-history-panel");
    if (btn) btn.classList.toggle("on", !dr.classList.contains("hidden"));
    renderHistoryDrawer();
  }

  // ==================== 4. 환경설정 모달 (6테마 × 7포인트컬러 + UI 크기 + 4배치 + 바깥어둡게) ====================
  function openSettingsModal() {
    const body = [];
    const curMode = Store.activeTheme().mode;

    // 1. 6종 배경 테마
    const themeSec = Data.el("div", { className: "settings-section" },
      Data.el("h4", {}, "테마 ", Data.el("small", { style: "font-weight:400;color:var(--dim);", text: "Forge 6종 호환 (다크 3 · 라이트 3)" })),
    );
    const themeGrid = Data.el("div", { className: "theme-grid-6" });
    for (const t of Data.THEMES) {
      const btn = Data.el("button", {
        type: "button",
        className: "theme-card-btn" + (Store.state.prefs.themeId === t.id ? " on" : ""),
        onClick: () => { Store.savePrefs({ themeId: t.id }); openSettingsModal(); },
      },
        Data.el("div", {
          className: "theme-swatch",
          style: `background:${t.vars["--bg"]};border-color:${t.vars["--line"]};color:${t.vars["--text"]};`,
        },
          Data.el("span", { text: "Aa" }),
          Data.el("span", { style: `margin-left:auto;width:10px;height:10px;background:${t.vars["--panel2"]};border:1px solid ${t.vars["--line2"]};` }),
        ),
        Data.el("span", { style: "font-size:11.5px;font-weight:600;" }, t.name, " ", Data.el("small", { style: "color:var(--muted);", text: `(${t.mode === "dark" ? "다크" : "라이트"})` })),
      );
      themeGrid.append(btn);
    }
    themeSec.append(themeGrid);
    body.push(themeSec);

    // 2. 7종 포인트 컬러
    const ptSec = Data.el("div", { className: "settings-section" },
      Data.el("h4", {}, "포인트 컬러 ", Data.el("small", { style: "font-weight:400;color:var(--dim);", text: "선택 영역 · 실행 버튼 강조색" })),
    );
    const ptRow = Data.el("div", { className: "point-colors-row" });
    for (const pt of Data.POINT_COLORS) {
      ptRow.append(Data.el("button", {
        type: "button",
        className: "point-btn" + (Store.state.prefs.pointColor === pt.id ? " on" : ""),
        onClick: () => { Store.savePrefs({ pointColor: pt.id }); openSettingsModal(); },
      }, Data.el("span", { className: "point-dot", style: `background:${pt[curMode] || pt.hex};` }), Data.el("span", { text: pt.name })));
    }
    ptSec.append(ptRow);
    body.push(ptSec);

    // 3. UI 크기 스케일
    const scaleVal = Data.el("span", { style: "color:var(--accent);", text: `${Store.state.prefs.uiScale}%` });
    const scaleRange = Data.el("input", {
      type: "range", min: "85", max: "140", step: "5", value: String(Store.state.prefs.uiScale),
      onInput: (e) => {
        const v = Number(e.target.value);
        scaleVal.textContent = `${v}%`;
        Store.savePrefs({ uiScale: v });
      },
    });
    const scaleRow = Data.el("div", { className: "scale-row" }, scaleRange);
    for (const v of [90, 100, 115, 130]) {
      scaleRow.append(Data.el("button", {
        type: "button",
        className: "mini-btn" + (Store.state.prefs.uiScale === v ? " accent-outline" : ""),
        text: `${v}%`,
        onClick: () => { Store.savePrefs({ uiScale: v }); openSettingsModal(); },
      }));
    }
    body.push(Data.el("div", { className: "settings-section" },
      Data.el("h4", {}, "UI 글씨 · 버튼 크기 ", scaleVal),
      scaleRow,
      Data.el("small", { style: "color:var(--muted);font-size:11px;", text: "메뉴와 버튼만 조절되며 중앙 원화 캔버스 해상도는 그대로 유지됩니다." }),
    ));

    // 4. 패널 배치 프리셋
    const layGrid = Data.el("div", { className: "layout-grid-2" });
    for (const lp of Data.LAYOUT_PRESETS) {
      layGrid.append(Data.el("button", {
        type: "button",
        className: "preset-card" + (Store.state.prefs.layoutPreset === lp.id ? " on" : ""),
        onClick: () => { Store.savePrefs({ layoutPreset: lp.id }); openSettingsModal(); },
      }, Data.el("strong", { text: lp.name }), Data.el("small", { text: lp.desc })));
    }
    body.push(Data.el("div", { className: "settings-section" },
      Data.el("h4", {}, "패널 배치 프리셋 ", Data.el("small", { style: "font-weight:400;color:var(--dim);", text: "상단 ⋮⋮ 바 드래그 이동 가능" })),
      layGrid,
    ));

    // 5. Forge 테마 연동
    const syncChk = Data.el("input", {
      type: "checkbox",
      onChange: (e) => Store.savePrefs({ forgeThemeSync: e.target.checked }),
    });
    syncChk.checked = !!Store.state.prefs.forgeThemeSync;
    body.push(Data.el("div", { className: "settings-section" },
      Data.el("label", { className: "chk-inline" }, syncChk, Data.el("span", { text: "Forge 부모 창 테마 변경 시 자동으로 함께 전환" })),
    ));

    openModal({
      title: "환경설정 — 테마 · 포인트 컬러 · UI 크기 · 패널 배치",
      bodyNodes: body,
      actions: [
        {
          label: "기본값 초기화", cls: "btn-secondary",
          onClick: () => {
            Store.savePrefs({ themeId: "graphite", pointColor: "lime", uiScale: 100, layoutPreset: "standard", focusDimAlpha: 36, forgeThemeSync: true });
            openSettingsModal();
          },
        },
        { label: "완료", cls: "btn-primary", onClick: closeModal },
      ],
    });
  }

  // ==================== 5. PSD 내보내기 · 프리셋 · 단축키 모달 ====================
  function openExportModal() {
    const patches = Store.getPatches();
    const picked = patches.filter((p) => p.anyPicked);
    const tree = Data.el("div", { className: "psd-tree" },
      Data.el("div", { className: "psd-row" }, Data.el("strong", { text: "03.수정" }), Data.el("span", { className: "psd-tag", text: `${picked.length}개 확정 패치 · 크롭 밖 무변형` })),
    );
    picked.forEach((p, idx) => {
      tree.append(Data.el("div", { className: "psd-row indent-1" },
        `${String(idx + 1).padStart(2, "0")}_${p.name}`,
        Data.el("span", { className: "psd-tag star", text: "그룹 마스크 · Smart Object" }),
      ));
    });
    tree.append(
      Data.el("div", { className: "psd-row" }, Data.el("strong", { text: "02.영역가이드" }), Data.el("span", { className: "psd-tag", text: "숨김 · 재편집용" })),
      Data.el("div", { className: "psd-row" }, Data.el("strong", { text: "00.원본_Base" }), Data.el("span", { className: "psd-tag", text: "원본 100% 보존" })),
    );

    const actions = [{ label: "닫기", cls: "btn-secondary", onClick: closeModal }];
    if (Api.can("forge_save")) {
      actions.push({
        label: "Forge 자료함에 저장", cls: "btn-secondary",
        onClick: async () => {
          closeModal();
          try {
            await Api.saveToForge(Store.state.root);
            Store.toast("합성 결과를 Forge 자료함에 저장했습니다.", "ok");
          } catch (err) {
            Store.toast(`Forge 저장 실패: ${err.message}`, "err");
          }
        },
      });
    }
    actions.push({
      label: "PSD 생성 · 내려받기", cls: "btn-primary",
      onClick: async () => {
        closeModal();
        const dlUrl = Api.exportDownloadUrl(Store.state.root);
        try {
          const res = await Api.submitStage("export", { root: Store.state.root });
          if (res && res.pendingIntegration) {
            if (dlUrl && Store.state.S && Store.state.S.export_state === "same") {
              const a = document.createElement("a");
              a.href = dlUrl;
              a.download = "";
              a.click();
              Store.toast("최신 PSD 파일 다운로드를 시작했습니다.", "ok");
            } else {
              Store.toast(res.message, "accent");
            }
            return;
          }
          if (res && res.ok) {
            Store.toast("PSD 파일을 생성하는 중입니다…");
            const waited = await Api.waitJob(Store.state.root, res.prompt_id);
            if (waited && waited.done && !waited.ok) {
              Store.toast("PSD 내보내기 작업이 실패했습니다.", "err");
              return;
            }
            await Store.refreshAll(true);
            if (dlUrl) {
              const a = document.createElement("a");
              a.href = dlUrl;
              a.download = "";
              a.click();
            }
            Store.toast("PSD 내보내기를 완료했습니다.", "ok");
          } else if (res) {
            Store.toast(res.error || res.message || res.reason || "PSD 내보내기를 시작하지 못했습니다.", "err");
          }
        } catch (err) {
          Store.toast(`PSD 내보내기 실패: ${err.message}`, "err");
        }
      },
    });

    openModal({
      title: "PSD 내보내기 — 레이어 구성 미리보기",
      bodyNodes: [
        Data.el("div", { className: "block-label", text: "출력될 포토샵 레이어 구조" }),
        tree,
      ],
      actions,
    });
  }

  function openPresetsModal() {
    const p = Store.primaryPatch();
    if (!p) return;
    const pr = Data.purposeInfo(p.meta.purpose);
    const wrap = Data.el("div", { style: "display:flex;flex-direction:column;gap:6px;" });
    for (const txt of (pr.presets || [])) {
      wrap.append(Data.el("button", {
        type: "button", className: "preset-card",
        onClick: () => {
          p.meta.user_instruction_ko = txt;
          p.meta.dirtySpec = true;
          closeModal();
          Store.emit("selection");
        },
      }, Data.el("strong", { style: "font-weight:500;", text: txt })));
    }
    openModal({
      title: `${pr.label} — 자주 쓰는 수정 지시 프리셋`,
      bodyNodes: [wrap],
      actions: [{ label: "닫기", cls: "btn-secondary", onClick: closeModal }],
    });
  }

  function openShortcutsModal() {
    const rows = [
      ["선택 · 이동 · 크기 · 모서리 회전", "V"],
      ["사각형 수정 영역 추가", "M"],
      ["마스크 살리기 브러시 / 지우개", "B / E"],
      ["레퍼런스 시트 오리기 · 4점 원근", "R"],
      ["선택 해제 (밝은 원본 전체 보기)", "Esc"],
      ["원본과 즉시 비교", "Q / Tab"],
      ["좌우 분할 비교", "S"],
      ["영역선 표시 / 경계 이음매", "O / N"],
      ["작업 히스토리 패널", "H"],
      ["화면 맞춤 (Fit)", "0"],
      ["여러 영역 다중 선택", "Shift / Ctrl + 클릭"],
      ["실행 취소 / 다시 실행", "Ctrl+Z / Ctrl+Shift+Z"],
    ];
    const grid = Data.el("div", { className: "shortcut-grid" });
    for (const [label, key] of rows) {
      grid.append(Data.el("div", { className: "sc-row" }, Data.el("span", { text: label }), Data.el("kbd", { text: key })));
    }
    openModal({
      title: "포토샵 표준 단축키 안내",
      bodyNodes: [grid],
      actions: [{ label: "닫기", cls: "btn-primary", onClick: closeModal }],
    });
  }

  // ==================== 6. 프로젝트 홈 & 새 프로젝트 모달 + Forge 가져오기 ====================
  async function renderProjectsHome() {
    const grid = $("projects-grid");
    if (!grid) return;
    grid.innerHTML = "";
    try {
      const res = await Api.listProjects();
      const list = Array.isArray(res.projects) ? res.projects : [];
      Store.state.projects = list;
      if (!list.length) {
        grid.append(Data.el("div", { className: "empty-card", style: "grid-column:1/-1;" },
          Data.el("p", { text: "아직 만들어진 프로젝트가 없습니다" }),
          Data.el("small", { text: "우측 상단 [+ 새 프로젝트]를 눌러 원화 이미지를 올려 시작하세요." }),
        ));
        return;
      }
      for (const proj of list) {
        const openable = proj.openable !== false;
        const thumb = openable ? Api.apiUrl(proj.thumb_url || "") : "";
        grid.append(Data.el("div", {
          className: "proj-card" + (openable ? "" : " disabled"),
          onClick: async () => {
            if (!openable) {
              Store.toast(proj.error || "이 프로젝트는 열 수 없습니다.", "err");
              return;
            }
            try {
              const r = await Api.openProject(proj.id);
              const key = (r && r.key) || proj.id;
              const u = new URL(location.href);
              u.searchParams.set("root", key);
              u.searchParams.set("ui", "v2");
              location.href = u.pathname + u.search;
            } catch (err) {
              Store.toast(`프로젝트를 열 수 없습니다: ${err.message}`, "err");
            }
          },
        },
          Data.el("div", { className: "proj-card-thumb" }, thumb ? Data.el("img", { src: thumb, alt: proj.name }) : null),
          Data.el("div", { className: "proj-card-body" },
            Data.el("strong", { text: proj.name || proj.id }),
            openable
              ? Data.el("div", { className: "proj-card-meta" },
                Data.el("span", { text: `영역 ${proj.crops || 0}개` }),
                Data.el("span", { text: `후보 ${proj.candidates || 0}장` }),
                Data.el("span", { text: `확정 ★ ${proj.stars || 0}` }),
              )
              : Data.el("span", { className: "proj-card-err", text: proj.error || "열 수 없음" }),
            Data.el("span", { className: "proj-card-time", text: proj.modified || "" }),
          ),
        ));
      }
    } catch (err) {
      grid.append(Data.el("div", { className: "empty-card", text: `프로젝트 목록을 불러오지 못했습니다: ${err.message}` }));
    }
  }

  function openNewProjectModal(initialFile = null) {
    let targetFile = initialFile;
    let targetForgeFile = null;
    const refFiles = [];

    const nameInput = Data.el("input", {
      type: "text", className: "np-text-input",
      placeholder: "예: 캐릭터_의상수정_01",
      value: initialFile ? initialFile.name.replace(/\.[^.]+$/, "").replace(/[^\p{L}\p{N}_.-]+/gu, "_") : "",
    });
    const errEl = Data.el("small", { style: "color:var(--err);min-height:16px;" });
    const pickedBox = Data.el("div", { style: "display:flex;flex-wrap:wrap;gap:6px;" });

    function refreshPicked() {
      pickedBox.innerHTML = "";
      if (targetFile) {
        const url = URL.createObjectURL(targetFile);
        pickedBox.append(Data.el("span", { className: "np-picked-chip" },
          Data.el("img", { src: url, alt: "" }),
          Data.el("span", { text: `타깃: ${targetFile.name}` }),
          Data.el("button", { type: "button", className: "row-icon-btn", text: "×", onClick: () => { targetFile = null; refreshPicked(); } }),
        ));
      } else if (targetForgeFile) {
        pickedBox.append(Data.el("span", { className: "np-picked-chip" },
          Data.el("span", { text: `Forge 타깃: ${targetForgeFile}` }),
          Data.el("button", { type: "button", className: "row-icon-btn", text: "×", onClick: () => { targetForgeFile = null; refreshPicked(); } }),
        ));
      }
      refFiles.forEach((rf, i) => {
        pickedBox.append(Data.el("span", { className: "np-picked-chip" },
          Data.el("span", { text: `참조: ${rf.name}` }),
          Data.el("button", { type: "button", className: "row-icon-btn", text: "×", onClick: () => { refFiles.splice(i, 1); refreshPicked(); } }),
        ));
      });
    }

    const dropBox = Data.el("div", { className: "np-drop" },
      Data.el("span", { text: "원화 이미지를 여기에 끌어다 놓거나 파일을 선택하세요" }),
      Data.el("div", { style: "display:flex;gap:6px;flex-wrap:wrap;justify-content:center;" },
        Data.el("button", {
          type: "button", className: "btn-secondary-sm", text: "내 PC에서 원화 선택",
          onClick: () => {
            const inp = Data.el("input", { type: "file", accept: "image/*" });
            inp.addEventListener("change", () => {
              if (inp.files && inp.files[0]) {
                targetFile = inp.files[0];
                targetForgeFile = null;
                if (!nameInput.value.trim()) nameInput.value = targetFile.name.replace(/\.[^.]+$/, "").replace(/[^\p{L}\p{N}_.-]+/gu, "_");
                refreshPicked();
              }
            });
            inp.click();
          },
        }),
        Api.can("forge_import") ? Data.el("button", {
          type: "button", className: "btn-secondary-sm accent-outline", text: "Forge에서 불러오기",
          onClick: () => {
            const name = nameInput.value.trim();
            const vErr = Data.validateProjectName(name);
            if (vErr) {
              errEl.textContent = vErr || "먼저 프로젝트 이름을 입력해 주세요.";
              nameInput.focus();
              return;
            }
            errEl.textContent = "";
            if (window.DPForgeImport && typeof window.DPForgeImport.open === "function") {
              const opened = window.DPForgeImport.open({
                into: "target",
                name,
                onDone: async (result) => {
                  if (!result || !/^[0-9a-f]{12}$/.test(result.key || "")) {
                    Store.toast((result && result.error) || "Forge에서 프로젝트를 만들지 못했습니다.", "err");
                    return;
                  }
                  for (const rf of refFiles) {
                    try { await Api.uploadSheet(result.key, rf, null); } catch (_) {}
                  }
                  closeModal();
                  const u = new URL(location.href);
                  u.searchParams.set("root", result.key);
                  u.searchParams.set("ui", "v2");
                  location.href = u.pathname + u.search;
                },
              });
              if (!opened) Store.toast("Forge 자료함을 열 수 없습니다 — 연결 상태를 확인해 주세요.", "err");
            } else {
              Store.toast("Forge 가져오기 모듈이 연결되면 바로 선택할 수 있습니다.");
            }
          },
        }) : null,
        Data.el("button", {
          type: "button", className: "btn-secondary-sm", text: "+ 레퍼런스 함께 추가",
          onClick: () => {
            const inp = Data.el("input", { type: "file", accept: "image/*", multiple: true });
            inp.addEventListener("change", () => {
              for (const f of Array.from(inp.files || [])) refFiles.push(f);
              refreshPicked();
            });
            inp.click();
          },
        }),
      ),
    );
    dropBox.addEventListener("dragover", (e) => { e.preventDefault(); dropBox.classList.add("over"); });
    dropBox.addEventListener("dragleave", () => dropBox.classList.remove("over"));
    dropBox.addEventListener("drop", (e) => {
      e.preventDefault();
      dropBox.classList.remove("over");
      const files = Array.from(e.dataTransfer?.files || []);
      if (files[0]) {
        targetFile = files[0];
        if (!nameInput.value.trim()) nameInput.value = targetFile.name.replace(/\.[^.]+$/, "").replace(/[^\p{L}\p{N}_.-]+/gu, "_");
        for (const extra of files.slice(1)) refFiles.push(extra);
        refreshPicked();
      }
    });
    refreshPicked();

    openModal({
      title: "새 프로젝트 만들기",
      bodyNodes: [
        Data.el("div", { className: "np-field" }, Data.el("label", { text: "프로젝트 이름" }), nameInput, errEl),
        dropBox,
        pickedBox,
      ],
      actions: [
        { label: "취소", cls: "btn-secondary", onClick: closeModal },
        {
          label: "프로젝트 생성 및 열기", cls: "btn-primary",
          onClick: async () => {
            const name = nameInput.value.trim();
            const vErr = Data.validateProjectName(name);
            if (vErr) { errEl.textContent = vErr; return; }
            if (!targetFile && !targetForgeFile) { errEl.textContent = "타깃 원화 이미지를 먼저 선택해 주세요."; return; }
            try {
              const res = targetFile
                ? await Api.createProjectFile(name, targetFile)
                : await Api.createProjectAdopt(name, targetForgeFile);
              for (const rf of refFiles) {
                try { await Api.uploadSheet(res.key, rf, null); } catch (_) {}
              }
              closeModal();
              const u = new URL(location.href);
              u.searchParams.set("root", res.key);
              u.searchParams.set("ui", "v2");
              location.href = u.pathname + u.search;
            } catch (err) {
              errEl.textContent = err.message;
            }
          },
        },
      ],
    });
  }

  function openForgeImportForRef() {
    if (!Api.can("forge_import")) {
      Store.toast("현재 환경에서는 Forge 자료함 가져오기가 비활성화되어 있습니다.");
      return;
    }
    if (window.DPForgeImport && typeof window.DPForgeImport.open === "function") {
      const opened = window.DPForgeImport.open({
        root: Store.state.root,
        into: "refs",
        onDone: async () => {
          Store.toast("Forge 자료함에서 레퍼런스를 가져왔습니다.", "ok");
          await Store.refreshAll(true);
        },
      });
      if (!opened) Store.toast("Forge 자료함을 열 수 없습니다.", "err");
    } else {
      Store.toast("Forge 자료함 연동이 활성화되면 여기에서 바로 불러올 수 있습니다.");
    }
  }

  // ==================== 이벤트 바인딩 ====================
  function bindEvents() {
    // 모달 닫기
    const mClose = $("modal-close");
    if (mClose) mClose.addEventListener("click", closeModal);
    const mBack = $("modal-backdrop");
    if (mBack) mBack.addEventListener("click", (e) => { if (e.target === mBack) closeModal(); });

    // 히스토리 드로어
    const btnCloseHist = $("btn-close-history");
    if (btnCloseHist) btnCloseHist.addEventListener("click", toggleHistoryDrawer);

    // 시트 오리기 & 4점 원근 이벤트
    const btnSheetClose = $("btn-sheet-close");
    if (btnSheetClose) btnSheetClose.addEventListener("click", closeSheetOverlay);
    const shSel = $("sheet-select");
    if (shSel) shSel.addEventListener("change", (e) => loadSelectedSheet(e.target.value));
    const cropSel = $("sheet-target-crop");
    if (cropSel) cropSel.addEventListener("change", drawSheetWarp);
    const btnShUp = $("btn-sheet-upload");
    if (btnShUp) btnShUp.addEventListener("click", () => $("hidden-file-sheet").click());
    const btnShForge = $("btn-sheet-forge");
    if (btnShForge) btnShForge.addEventListener("click", openForgeImportForRef);

    const btnZout = $("btn-sheet-zout"), btnZin = $("btn-sheet-zin"), btn1to1 = $("btn-sheet-1to1"), btnFit = $("btn-sheet-fit"), btnMax = $("btn-sheet-max");
    if (btnZout) btnZout.addEventListener("click", () => setSheetZoomAt(sheetState.zoom / 1.25));
    if (btnZin) btnZin.addEventListener("click", () => setSheetZoomAt(sheetState.zoom * 1.25));
    if (btn1to1) btn1to1.addEventListener("click", () => {
      const img = sheetState.sheetImg;
      const wrap = $("sheet-source-wrap");
      if (!img || !wrap) return;
      sheetState.zoom = 1.0;
      sheetState.panX = Math.round((wrap.clientWidth - img.naturalWidth) / 2);
      sheetState.panY = Math.round((wrap.clientHeight - img.naturalHeight) / 2);
      applySheetTransform();
      drawSheetSource();
    });
    if (btnFit) btnFit.addEventListener("click", () => { fitSheetToView(); drawSheetSource(); });
    if (btnMax) btnMax.addEventListener("click", () => {
      sheetState.maximized = !sheetState.maximized;
      $("sheet-split-body").classList.toggle("maximized", sheetState.maximized);
      btnMax.textContent = sheetState.maximized ? "⤡ 분할로" : "⤢ 최대화";
      requestAnimationFrame(() => { fitSheetToView(); drawSheetSource(); });
    });

    const alphaEl = $("sheet-overlay-alpha");
    if (alphaEl) alphaEl.addEventListener("input", (e) => { sheetState.alpha = Number(e.target.value) / 100; drawSheetWarp(); });
    const btnFlip = $("btn-sheet-flip");
    if (btnFlip) btnFlip.addEventListener("click", () => { sheetState.flipX = !sheetState.flipX; drawSheetWarp(); });
    const btnResetQ = $("btn-sheet-reset-quad");
    if (btnResetQ) btnResetQ.addEventListener("click", () => { sheetState.flipX = false; sheetState.quad = defaultQuad(); drawSheetWarp(); });
    const btnSaveVault = $("btn-sheet-save-vault");
    if (btnSaveVault) btnSaveVault.addEventListener("click", () => saveSheetCut(false));
    const btnAttach = $("btn-sheet-attach");
    if (btnAttach) btnAttach.addEventListener("click", () => saveSheetCut(true));

    // 좌측 시트 뷰어: 휠 커서 중심 줌 + 오버스크롤 패닝 + 드래그 오리기
    const srcWrap = $("sheet-source-wrap");
    const srcCanvas = $("sheet-source-canvas");
    if (srcWrap && srcCanvas) {
      srcWrap.addEventListener("contextmenu", (e) => e.preventDefault());
      srcWrap.addEventListener("wheel", (e) => {
        e.preventDefault();
        const b = srcWrap.getBoundingClientRect();
        setSheetZoomAt(sheetState.zoom * (e.deltaY < 0 ? 1.16 : 1 / 1.16), e.clientX - b.left, e.clientY - b.top);
      }, { passive: false });
      srcWrap.addEventListener("pointerdown", (e) => {
        if (!sheetState.sheetImg) return;
        if (e.button === 1 || e.button === 2 || Canvas.view.spaceDown) {
          sheetState.drag = { kind: "pan", sx: e.clientX, sy: e.clientY, panX: sheetState.panX, panY: sheetState.panY };
          srcWrap.style.cursor = "grabbing";
          srcWrap.setPointerCapture(e.pointerId);
          return;
        }
        if (e.button !== 0) return;
        const b = srcCanvas.getBoundingClientRect();
        if (!b.width || !b.height) return;
        const x = Math.max(0, Math.min(1, (e.clientX - b.left) / b.width));
        const y = Math.max(0, Math.min(1, (e.clientY - b.top) / b.height));
        sheetState.drag = { kind: "cut", x0: x, y0: y };
        srcWrap.setPointerCapture(e.pointerId);
      });
      srcWrap.addEventListener("pointermove", (e) => {
        if (!sheetState.drag) {
          srcWrap.style.cursor = Canvas.view.spaceDown ? "grab" : "crosshair";
          return;
        }
        if (sheetState.drag.kind === "pan") {
          sheetState.panX = sheetState.drag.panX + (e.clientX - sheetState.drag.sx);
          sheetState.panY = sheetState.drag.panY + (e.clientY - sheetState.drag.sy);
          applySheetTransform();
          return;
        }
        if (sheetState.drag.kind !== "cut") return;
        const b = srcCanvas.getBoundingClientRect();
        if (!b.width || !b.height) return;
        const x = Math.max(0, Math.min(1, (e.clientX - b.left) / b.width));
        const y = Math.max(0, Math.min(1, (e.clientY - b.top) / b.height));
        sheetState.cutRect = {
          x: Math.min(sheetState.drag.x0, x),
          y: Math.min(sheetState.drag.y0, y),
          w: Math.max(0.03, Math.abs(x - sheetState.drag.x0)),
          h: Math.max(0.03, Math.abs(y - sheetState.drag.y0)),
        };
        drawSheetSource();
        drawSheetWarp();
      });
      srcWrap.addEventListener("pointerup", () => {
        sheetState.drag = null;
        srcWrap.style.cursor = "crosshair";
      });
    }

    // 우측 4점 원근 꼭짓점 드래그
    const warpCanvas = $("sheet-warp-canvas");
    if (warpCanvas) {
      warpCanvas.addEventListener("pointerdown", (e) => {
        if (!sheetState.sheetImg) return;
        const b = warpCanvas.getBoundingClientRect();
        const x = (e.clientX - b.left) / b.width;
        const y = (e.clientY - b.top) / b.height;
        let nearest = -1, bestD = 0.09;
        sheetState.quad.forEach((pt, idx) => {
          const d = Math.hypot(pt.x - x, pt.y - y);
          if (d < bestD) { bestD = d; nearest = idx; }
        });
        if (nearest >= 0) {
          sheetState.drag = { kind: "quad", idx: nearest };
          warpCanvas.style.cursor = "grabbing";
          warpCanvas.setPointerCapture(e.pointerId);
        }
      });
      warpCanvas.addEventListener("pointermove", (e) => {
        const b = warpCanvas.getBoundingClientRect();
        const x = (e.clientX - b.left) / b.width;
        const y = (e.clientY - b.top) / b.height;
        if (!sheetState.drag) {
          const near = sheetState.quad.some((pt) => Math.hypot(pt.x - x, pt.y - y) < 0.08);
          warpCanvas.style.cursor = near ? "grab" : "default";
          return;
        }
        if (sheetState.drag.kind !== "quad") return;
        sheetState.quad[sheetState.drag.idx] = {
          x: Math.max(0.02, Math.min(0.98, x)),
          y: Math.max(0.02, Math.min(0.98, y)),
        };
        drawSheetWarp();
      });
      warpCanvas.addEventListener("pointerup", () => {
        sheetState.drag = null;
        warpCanvas.style.cursor = "default";
      });
    }

    // 풀스크린 정밀 비교 이벤트
    const coClose = $("co-close");
    if (coClose) coClose.addEventListener("click", closeCompareOverlay);
    const coBlink = $("co-toggle-blink");
    if (coBlink) coBlink.addEventListener("click", () => { cmpState.blinkOrig = !cmpState.blinkOrig; renderCompareOverlay(); });
    const coZoomReset = $("co-zoom-reset");
    if (coZoomReset) coZoomReset.addEventListener("click", () => { cmpState.zoom = 1; renderCompareOverlay(); });
    const coPick = $("co-pick");
    if (coPick) coPick.addEventListener("click", async () => {
      const p = Store.primaryPatch();
      if (!p || !p.target || !cmpState.candKey) return;
      try {
        await Api.pickCandidate(Store.state.root, { crop_id: p.id, tid: p.target.tid, key: cmpState.candKey, mode: "set" });
        closeCompareOverlay();
        Store.toast(`"${p.name}"에 선택한 후보를 확정 적용했습니다 ★`, "ok");
        await Store.refreshAll(true);
      } catch (err) {
        Store.toast(`채택 실패: ${err.message}`, "err");
      }
    });
    const coToRt = $("co-to-retouch");
    if (coToRt) coToRt.addEventListener("click", () => {
      const p = Store.primaryPatch();
      if (p && p.target) Store.selectCandidate(p.target.key, cmpState.candKey);
      closeCompareOverlay();
      if (window.DPV2Inspector) window.DPV2Inspector.setActiveTab("retouch");
    });
    const coReedit = $("co-reedit");
    if (coReedit) coReedit.addEventListener("click", () => {
      closeCompareOverlay();
      if (window.DPV2Inspector) window.DPV2Inspector.setActiveTab("instruct");
      const inp = $("inp-instruction");
      if (inp) inp.focus();
    });

    const cbox = $("co-compare-box");
    const updateCoSplit = (clientX) => {
      if (!cbox) return;
      const r = cbox.getBoundingClientRect();
      if (!r.width) return;
      cmpState.blinkOrig = false;
      cmpState.split = Math.max(0.02, Math.min(0.98, (clientX - r.left) / r.width));
      updateCompareDivider();
    };
    if (cbox) {
      cbox.addEventListener("pointerdown", (e) => {
        if (e.button !== 0) return;
        cmpState.dragging = true;
        updateCoSplit(e.clientX);
        cbox.setPointerCapture(e.pointerId);
      });
      window.addEventListener("pointermove", (e) => { if (cmpState.dragging) updateCoSplit(e.clientX); });
      window.addEventListener("pointerup", () => { cmpState.dragging = false; });
    }
    const coStage = $("co-stage");
    if (coStage) {
      coStage.addEventListener("wheel", (e) => {
        if (!cmpState.open) return;
        e.preventDefault();
        cmpState.zoom = Math.max(0.6, Math.min(5, cmpState.zoom * (e.deltaY < 0 ? 1.15 : 1 / 1.15)));
        if (cbox) cbox.style.transform = `scale(${cmpState.zoom})`;
        $("co-zoom-reset").textContent = `${Math.round(cmpState.zoom * 100)}%`;
      }, { passive: false });
    }

    // 히스토리 상태 구독
    Store.subscribe((reason) => {
      if (reason === "history") renderHistoryDrawer();
    });
  }

  return {
    sheetState,
    cmpState,
    openModal,
    closeModal,
    openSheetOverlay,
    closeSheetOverlay,
    openCompareOverlay,
    closeCompareOverlay,
    toggleHistoryDrawer,
    openSettingsModal,
    openExportModal,
    openPresetsModal,
    openShortcutsModal,
    renderProjectsHome,
    openNewProjectModal,
    openForgeImportForRef,
    bindEvents,
  };
})();
