// BMK Design Patch Studio v2 — 캔버스 뷰포트 · 줌/팬 · Focus Dim · 8핸들/회전 · 2단 플로팅 바 · 실시간 후보/마스크 합성.
"use strict";

window.DPV2Canvas = (() => {
  const Data = window.DPV2Data;
  const Api = window.DPV2Api;
  const Store = window.DPV2Store;

  const view = {
    imgW: 0,
    imgH: 0,
    zoom: 1,
    panX: 0,
    panY: 0,
    fitted: false,
    spaceDown: false,
    drag: null, // { type, ... }
    hoverRect: null,
    baseUrlLoaded: "",
    compUrlLoaded: "",
    lastOverlayUrl: "",
  };

  const $ = (id) => document.getElementById(id);

  // 선택된 후보를 메인 캔버스 위 실시간 오버레이로 투영할 주소·영역 계산
  function resolveCandidateOverlay(patch, cand) {
    if (!patch || !cand) return null;
    if (Store.state.viewFlags.beforeHeld) return null;
    const sc = patch.stateCrop || {};
    const region = Array.isArray(sc.region) && sc.region.length === 4 ? sc.region : patch.rect;
    if (!Array.isArray(region) || region.length !== 4) return null;
    const isPicked = !!(patch.pickedCand && patch.pickedCand.key === cand.key);
    const rel = isPicked
      ? (sc.region_url || cand.pick_url || cand.big_url || "")
      : (cand.pick_url || cand.big_url || "");
    if (!rel) return null;
    return { rel, rect: region, previewing: !isPicked };
  }

  function screenToImage(clientX, clientY) {
    const vp = $("canvas-viewport");
    if (!vp) return { x: 0, y: 0 };
    const b = vp.getBoundingClientRect();
    return {
      x: (clientX - b.left - view.panX) / Math.max(0.05, view.zoom),
      y: (clientY - b.top - view.panY) / Math.max(0.05, view.zoom),
    };
  }

  function applyTransform() {
    const tf = $("canvas-transform");
    if (!tf) return;
    tf.style.width = `${view.imgW}px`;
    tf.style.height = `${view.imgH}px`;
    tf.style.transform = `translate(${Math.round(view.panX)}px, ${Math.round(view.panY)}px) scale(${view.zoom})`;
    const zBtn = $("btn-zoom-fit");
    if (zBtn) zBtn.textContent = `${Math.round(view.zoom * 100)}%`;
    renderSplitView();
    renderOverlaySvg();
  }

  function fitToViewport() {
    const vp = $("canvas-viewport");
    if (!vp || !view.imgW || !view.imgH) return;
    const vw = vp.clientWidth || 800;
    const vh = vp.clientHeight || 600;
    const topSafe = 32;
    const bottomSafe = 114; // 2단 하단 플로팅 바 여백
    const sideSafe = 66;    // 좌측 툴 레일 여백
    const s = Math.min((vw - sideSafe * 2) / view.imgW, (vh - topSafe - bottomSafe) / view.imgH, 1.0);
    view.zoom = Math.max(0.12, Math.min(4, s));
    view.panX = Math.round((vw - view.imgW * view.zoom) / 2);
    view.panY = Math.round(topSafe + (vh - topSafe - bottomSafe - view.imgH * view.zoom) / 2);
    view.fitted = true;
    applyTransform();
  }

  function zoomBy(factor, cx = null, cy = null) {
    const vp = $("canvas-viewport");
    if (!vp) return;
    const b = vp.getBoundingClientRect();
    const mx = cx === null ? b.width / 2 : cx - b.left;
    const my = cy === null ? b.height / 2 : cy - b.top;
    const next = Math.max(0.15, Math.min(6, view.zoom * factor));
    view.panX = mx - ((mx - view.panX) / view.zoom) * next;
    view.panY = my - ((my - view.panY) / view.zoom) * next;
    view.zoom = next;
    applyTransform();
  }

  function getBaseImage() {
    const img = $("canvas-base");
    return img && img.complete && img.naturalWidth ? img : null;
  }

  // ── 캔버스 베이스 / 합성 / 후보 라이브 오버레이 동기화 ──
  function syncCanvasImages() {
    const S = Store.state.S;
    if (!S || !S.canvas) return;
    const [w, h] = S.canvas.size || [0, 0];
    if (w && h && (w !== view.imgW || h !== view.imgH)) {
      view.imgW = w;
      view.imgH = h;
      const resEl = $("canvas-res-label");
      if (resEl) resEl.textContent = `${w} × ${h} px`;
      view.fitted = false;
    }

    const baseEl = $("canvas-base");
    const compEl = $("canvas-composite");
    const baseUrl = Api.apiUrl(S.canvas.base_url);
    const compUrl = Api.apiUrl(S.canvas.composite_url || S.canvas.base_url);
    if (baseEl && baseUrl && view.baseUrlLoaded !== baseUrl) {
      view.baseUrlLoaded = baseUrl;
      baseEl.onload = () => {
        if (!view.fitted) fitToViewport();
        Store.emit("base-image-loaded");
      };
      baseEl.src = baseUrl;
    }
    if (compEl && compUrl && view.compUrlLoaded !== compUrl) {
      view.compUrlLoaded = compUrl;
      compEl.src = compUrl;
    }

    if (!view.fitted && view.imgW && view.imgH) {
      requestAnimationFrame(() => fitToViewport());
    } else {
      applyTransform();
    }
    renderSplitView();
    renderCandidateOverlay();
    renderMaskPreview();
    renderFloatingBar();
    updateFooterInfo();
  }

  function renderSplitView() {
    const compEl = $("canvas-composite");
    const splitLine = $("split-line");
    if (!compEl || !splitLine) return;
    const vf = Store.state.viewFlags;
    if (vf.beforeHeld) {
      compEl.style.opacity = "0";
      compEl.style.clipPath = "";
      splitLine.classList.add("hidden");
      return;
    }
    compEl.style.opacity = "1";
    if (vf.splitOn) {
      const pct = Math.round(vf.splitRatio * 1000) / 10;
      compEl.style.clipPath = `inset(0 0 0 ${pct}%)`;
      const vp = $("canvas-viewport");
      if (vp) {
        const xScreen = view.panX + view.imgW * view.zoom * vf.splitRatio;
        splitLine.style.left = `${Math.round(xScreen)}px`;
      }
      splitLine.classList.remove("hidden");
    } else {
      compEl.style.clipPath = "";
      splitLine.classList.add("hidden");
    }
  }

  function renderCandidateOverlay() {
    const ovImg = $("cand-live-overlay");
    const badge = $("live-preview-badge");
    const badgeTxt = $("live-preview-text");
    if (!ovImg) return;

    const patch = Store.primaryPatch();
    const cand = Store.currentCandidate(patch);
    const info = resolveCandidateOverlay(patch, cand);

    if (!info || Store.state.viewFlags.splitOn) {
      ovImg.classList.add("hidden");
      if (badge) badge.classList.add("hidden");
      return;
    }

    const url = Api.apiUrl(info.rel);
    if (!url) {
      ovImg.classList.add("hidden");
      if (badge) badge.classList.add("hidden");
      return;
    }

    const [x1, y1, x2, y2] = info.rect;
    ovImg.style.left = `${x1}px`;
    ovImg.style.top = `${y1}px`;
    ovImg.style.width = `${Math.max(1, x2 - x1)}px`;
    ovImg.style.height = `${Math.max(1, y2 - y1)}px`;

    // 비파괴 스마트 필터 실시간 프리뷰 적용
    const f = patch.meta.filters;
    const filters = [];
    if (f.colorEnabled) {
      if (f.bright !== 0) filters.push(`brightness(${1 + f.bright / 100})`);
      if (f.sat !== 0) filters.push(`saturate(${1 + f.sat / 100})`);
      ovImg.style.opacity = String((f.opacity ?? 100) / 100);
    } else {
      ovImg.style.opacity = "1";
    }
    ovImg.style.filter = filters.length ? filters.join(" ") : "";

    if (f.transformEnabled && (f.dx || f.dy || f.rot || (f.scale && f.scale !== 100))) {
      ovImg.style.transform = `translate(${f.dx || 0}px, ${f.dy || 0}px) rotate(${f.rot || 0}deg) scale(${(f.scale || 100) / 100})`;
    } else {
      ovImg.style.transform = "";
    }

    if (view.lastOverlayUrl !== url) {
      view.lastOverlayUrl = url;
      ovImg.src = url;
    }
    ovImg.classList.remove("hidden");

    if (badge) {
      if (info.previewing) {
        badge.classList.remove("hidden");
        if (badgeTxt) badgeTxt.textContent = `${patch.name} · 후보 #${cand.n || 1} 미리보기 중`;
      } else {
        badge.classList.add("hidden");
      }
    }
  }

  // ── SVG 오버레이 (Focus Dim · 8핸들 · 자동 크롭 틀 · 멀티타깃 focus_box · 이음새) ──
  function getHandles(rect) {
    const [x1, y1, x2, y2] = rect;
    const mx = (x1 + x2) / 2, my = (y1 + y2) / 2;
    return [
      { id: "nw", x: x1, y: y1 }, { id: "n", x: mx, y: y1 }, { id: "ne", x: x2, y: y1 },
      { id: "e", x: x2, y: my }, { id: "se", x: x2, y: y2 }, { id: "s", x: mx, y: y2 },
      { id: "sw", x: x1, y: y2 }, { id: "w", x: x1, y: my },
    ];
  }

  function renderOverlaySvg() {
    const svg = $("overlay-svg");
    if (!svg || !view.imgW || !view.imgH) return;
    svg.setAttribute("viewBox", `0 0 ${view.imgW} ${view.imgH}`);
    svg.innerHTML = "";
    if (!Store.state.viewFlags.guidesOn) return;

    const inv = 1 / Math.max(0.2, view.zoom);
    const strokeW = 1.8 * inv;
    const handleSz = 9 * inv;
    const fontSz = Math.round(12 * inv);
    const accent = Store.activeAccentHex();
    const isLight = Store.activeTheme().mode === "light";
    const onAccent = isLight ? "#ffffff" : "#09090b";

    const patches = Store.getPatches();
    const selIds = Store.state.selectedIds;
    const primary = Store.primaryPatch();

    // Focus Dim: 선택된 영역이 1개 이상일 때만 바깥쪽 어둡게! 선택 해제 시 0% (밝은 원본 전체 표시)
    const activeSel = patches.filter((p) => selIds.has(p.id) && !p.meta.hidden);
    const dimAlpha = (Store.state.prefs.focusDimAlpha ?? 36) / 100;
    if (activeSel.length > 0 && dimAlpha > 0) {
      let d = `M 0 0 H ${view.imgW} V ${view.imgH} H 0 Z`;
      for (const p of activeSel) {
        const [x1, y1, x2, y2] = p.rect;
        d += ` M ${x1} ${y1} H ${x2} V ${y2} H ${x1} Z`;
      }
      svg.append(Data.svgEl("path", {
        d,
        fill: `rgba(0, 0, 0, ${dimAlpha})`,
        "fill-rule": "evenodd",
      }));
    }

    // 실제 이음새 선분 (x0/y0/x1/y1)
    if (Store.state.viewFlags.seamsOn && Store.state.seams && Array.isArray(Store.state.seams.segments)) {
      for (const seg of Store.state.seams.segments) {
        const col = seg.severity === "bad" ? "#ef4444" : "#f59e0b";
        svg.append(Data.svgEl("line", {
          x1: seg.x0, y1: seg.y0, x2: seg.x1, y2: seg.y1,
          stroke: col, "stroke-width": strokeW * 2.2, "stroke-linecap": "round",
        }));
      }
    }

    // 각 수정 영역 박스 · 라벨 · 자동 크롭 틀 · 멀티타깃 focus_box · 8방향 핸들
    for (const p of patches) {
      if (p.meta.hidden) continue;
      const isSel = selIds.has(p.id);
      const isPrimary = !!(primary && primary.id === p.id);
      const [x1, y1, x2, y2] = p.rect;
      const rw = Math.max(1, x2 - x1), rh = Math.max(1, y2 - y1);
      const col = isSel ? accent : (p.anyPicked ? "#4ade80" : "#a1a1aa");

      // AI 자동 크롭 확장 가이드
      if (isSel && Store.state.prefs.showAutoCrop) {
        const af = Data.computeAutoCropFrame(p.rect, view.imgW, view.imgH);
        svg.append(Data.svgEl("rect", {
          x: af.x, y: af.y, width: af.w, height: af.h,
          fill: "none", stroke: accent, "stroke-opacity": 0.45,
          "stroke-width": strokeW * 0.8, "stroke-dasharray": `${6 * inv} ${4 * inv}`,
        }));
      }

      // 주 박스 (내부는 항상 fill="none" — 원본 색감 무왜곡!)
      svg.append(Data.svgEl("rect", {
        x: x1, y: y1, width: rw, height: rh,
        fill: "none", stroke: col,
        "stroke-width": isSel ? strokeW * 1.35 : strokeW * 0.9,
      }));

      // 멀티타깃 focus_box 표시
      if (isPrimary && p.target && Array.isArray(p.target.focus_box) && p.target.focus_box.length === 4) {
        const [fx1, fy1, fx2, fy2] = p.target.focus_box;
        svg.append(Data.svgEl("rect", {
          x: x1 + fx1, y: y1 + fy1, width: Math.max(4, fx2 - fx1), height: Math.max(4, fy2 - fy1),
          fill: "none", stroke: "#38bdf8",
          "stroke-width": strokeW * 1.1, "stroke-dasharray": `${4 * inv} ${3 * inv}`,
        }));
      }

      // 상단 라벨 태그
      const purp = Data.purposeInfo(p.meta.purpose);
      const labelText = `${p.index}. [${purp.short}] ${p.name}${p.locked ? " (잠금)" : ""}`;
      const padX = 7 * inv;
      const labelH = 21 * inv;
      const labelW = Math.max(80 * inv, (labelText.length * 8.4 + 16) * inv);
      const ly = Math.max(0, y1 - labelH - 2 * inv);

      svg.append(Data.svgEl("rect", {
        x: x1, y: ly, width: labelW, height: labelH, rx: 2 * inv,
        fill: isSel ? accent : "rgba(9, 9, 11, 0.86)",
        stroke: col, "stroke-width": strokeW * 0.6,
      }));
      const txt = Data.svgEl("text", {
        x: x1 + padX, y: ly + labelH * 0.72,
        fill: isSel ? onAccent : "#f4f4f5",
        "font-size": fontSz, "font-weight": "700",
      });
      txt.textContent = labelText;
      svg.append(txt);

      // 주 선택 영역 8방향 리사이즈 핸들
      if (isPrimary && !p.locked) {
        for (const hp of getHandles(p.rect)) {
          svg.append(Data.svgEl("rect", {
            x: hp.x - handleSz / 2, y: hp.y - handleSz / 2,
            width: handleSz, height: handleSz,
            fill: accent, stroke: onAccent, "stroke-width": strokeW * 0.75,
          }));
        }
      }
    }

    // 드로잉 중인 새 사각형
    if (view.drag && view.drag.type === "draw-rect" && view.drag.tempRect) {
      const [x1, y1, x2, y2] = view.drag.tempRect;
      svg.append(Data.svgEl("rect", {
        x: x1, y: y1, width: Math.max(1, x2 - x1), height: Math.max(1, y2 - y1),
        fill: "none", stroke: accent,
        "stroke-width": strokeW * 1.4, "stroke-dasharray": `${5 * inv} ${4 * inv}`,
      }));
    }
  }

  // ── 2단 리치 플로팅 컨텍스트 바 (목업 100% 복원) ──
  function renderFloatingBar() {
    const fb = $("floating-bar");
    if (!fb) return;
    const patch = Store.primaryPatch();
    const tool = Store.state.tool;
    if (!patch || tool === "brush" || tool === "eraser") {
      fb.classList.add("hidden");
      return;
    }
    fb.classList.remove("hidden");

    const selList = Store.selectedPatches();
    const isMulti = selList.length > 1;
    const purpHost = $("fb-purposes");
    if (purpHost) {
      purpHost.innerHTML = "";
      const commonPurp = Store.getCommonOrMixed(selList, (p) => p.meta.purpose);
      for (const pr of Data.PURPOSES) {
        const b = Data.el("button", {
          type: "button",
          className: "fb-purpose-chip" + (!commonPurp.mixed && commonPurp.value === pr.id ? " on" : ""),
          title: pr.hint,
          onClick: () => {
            for (const sp of selList) {
              sp.meta.purpose = pr.id;
              sp.meta.dirtySpec = true;
            }
            Store.emit("selection");
            if (!isMulti && Api.can("v2_spec_edit")) Store.savePatchSpec(patch);
          },
        }, Data.icon(pr.icon, 12), pr.short);
        purpHost.append(b);
      }
    }

    // 부착된 레퍼런스 미니 칩 + '+ 참조' 버튼
    const refsHost = $("fb-refs");
    if (refsHost) {
      refsHost.innerHTML = "";
      const files = (patch.refCrop && Array.isArray(patch.refCrop.files)) ? patch.refCrop.files : [];
      files.slice(0, 3).forEach((rf, i) => {
        const thumb = Api.apiUrl(rf.thumb_url || "");
        const chip = Data.el("span", { className: "fb-ref-chip", title: rf.name },
          thumb ? Data.el("img", { src: thumb, alt: "" }) : null,
          Data.el("span", { text: `@참조${i + 1}` }),
          Data.el("button", {
            type: "button", text: "×", title: "이 영역에서 연결 해제",
            onClick: async () => {
              try {
                await Api.linkRef(Store.state.root, Store.state.R?.rev ?? null, {
                  op: "unlink", crop_id: patch.id, file: rf.name,
                });
                Store.toast(`"${rf.name}" 연결을 해제했습니다.`);
                await Store.refreshAll(true);
              } catch (err) {
                Store.toast(`연결 해제 실패: ${err.message}`, "err");
              }
            },
          }),
        );
        refsHost.append(chip);
      });
      refsHost.append(Data.el("button", {
        type: "button", className: "fb-mini-btn", title: "시트에서 레퍼런스 오려내 연결 (R)",
        onClick: () => window.DPV2Overlays && window.DPV2Overlays.openSheetOverlay(),
      }, Data.icon("scissors", 11), "+ 참조"));
    }

    // 우측 미니 액션 버튼들
    const cmpBtn = $("fb-compare-btn");
    if (cmpBtn) cmpBtn.classList.toggle("hidden", !patch.candidates.length);

    const ratioBtn = $("fb-ratio-btn");
    if (ratioBtn) {
      const ratios = (patch.modelInfo && patch.modelInfo.ratios) || [];
      ratioBtn.classList.toggle("hidden", isMulti || !ratios.length || patch.locked);
    }

    const lockBtn = $("fb-lock-btn");
    if (lockBtn) {
      lockBtn.innerHTML = "";
      lockBtn.append(Data.icon(patch.locked ? "lock" : "unlock", 12));
      lockBtn.title = patch.locked ? "잠금 해제" : "위치 잠금";
    }
    const delBtn = $("fb-del-btn");
    if (delBtn) {
      delBtn.innerHTML = "";
      delBtn.append(Data.icon("trash", 12));
    }
    const deselBtn = $("fb-deselect-btn");
    if (deselBtn) {
      deselBtn.innerHTML = "";
      deselBtn.append(Data.icon("x", 12));
    }

    // Row 2: 지시문 · AI 작성 · 모델 · 생성
    const inp = $("fb-instruction");
    const purp = Data.purposeInfo(patch.meta.purpose);
    if (inp) {
      if (document.activeElement !== inp) {
        inp.value = isMulti ? "" : (patch.meta.user_instruction_ko || "");
      }
      inp.placeholder = isMulti
        ? `${selList.length}개 영역 동시 선택 중 — 목적·모델 일괄 변경 또는 선택 생성`
        : purp.placeholder;
    }

    const aiBtn = $("fb-ai-btn");
    if (aiBtn) {
      aiBtn.innerHTML = "";
      aiBtn.append(Data.icon("sparkles", 13), Store.state.assistBusy ? "작성 중…" : "AI 작성");
      aiBtn.disabled = !!Store.state.assistBusy;
    }

    const mLabel = $("fb-model-label");
    if (mLabel) {
      const commonM = Store.getCommonOrMixed(selList, (p) => p.effectiveModel);
      mLabel.textContent = commonM.mixed
        ? `혼합 (${new Set(selList.map((p) => p.effectiveModel)).size}종)`
        : (patch.modelInfo ? patch.modelInfo.shortName : patch.effectiveModel);
    }
    const runBtn = $("fb-run-btn");
    if (runBtn) {
      runBtn.textContent = isMulti ? `${selList.length}개 생성` : "생성";
      runBtn.disabled = !!(Store.state.S && Store.state.S.running);
    }
  }

  // ── 마스크 브러시 실시간 캔버스 프리뷰 ──
  function renderMaskPreview() {
    const mc = $("mask-canvas");
    if (!mc) return;
    const ms = Store.state.mask;
    if (!ms || !ms.layers || (Store.state.tool !== "brush" && Store.state.tool !== "eraser")) {
      mc.classList.add("hidden");
      return;
    }
    mc.classList.remove("hidden");
    const [bx1, by1, bx2, by2] = ms.layers.box;
    const bw = bx2 - bx1, bh = by2 - by1;
    if (mc.width !== view.imgW || mc.height !== view.imgH) {
      mc.width = view.imgW;
      mc.height = view.imgH;
    }
    const ctx = mc.getContext("2d");
    ctx.clearRect(0, 0, mc.width, mc.height);
    const out = ctx.createImageData(bw, bh);
    const { under, patch, over } = ms.layers;
    const [wx1, wy1] = ms.win;
    for (let y = 0; y < bh; y++) {
      for (let x = 0; x < bw; x++) {
        const i4 = (y * bw + x) * 4;
        const mx = (bx1 + x) - wx1, my = (by1 + y) - wy1;
        let a = 0;
        if (mx >= 0 && mx < ms.w && my >= 0 && my < ms.h) a = ms.m[my * ms.w + mx];
        const oa = over[i4 + 3] / 255;
        for (let c = 0; c < 3; c++) {
          const blend = under[i4 + c] * (1 - a) + patch[i4 + c] * a;
          out.data[i4 + c] = Math.round(blend * (1 - oa) + over[i4 + c] * oa);
        }
        out.data[i4 + 3] = 255;
      }
    }
    ctx.putImageData(out, bx1, by1);
  }

  function paintMaskAt(pt, isErase) {
    const ms = Store.state.mask;
    if (!ms) return;
    const [wx1, wy1] = ms.win;
    const cx = pt.x - wx1, cy = pt.y - wy1;
    const rad = Math.max(2, ms.brushSize / 2);
    const hard = ms.brushHard ?? 0.65;
    const flow = ms.brushFlow ?? 1;
    const x0 = Math.max(0, Math.floor(cx - rad));
    const y0 = Math.max(0, Math.floor(cy - rad));
    const x1 = Math.min(ms.w - 1, Math.ceil(cx + rad));
    const y1 = Math.min(ms.h - 1, Math.ceil(cy + rad));
    for (let y = y0; y <= y1; y++) {
      for (let x = x0; x <= x1; x++) {
        const d = Math.hypot(x - cx, y - cy) / rad;
        if (d > 1) continue;
        const falloff = d <= hard ? 1 : (1 - (d - hard) / Math.max(0.01, 1 - hard));
        const idx = y * ms.w + x;
        const w = falloff * flow;
        ms.m[idx] = isErase
          ? Math.max(0, ms.m[idx] * (1 - w))
          : Math.min(1, ms.m[idx] + (1 - ms.m[idx]) * w);
      }
    }
    ms.dirty = true;
    renderMaskPreview();
  }

  // ── 히트 테스트 (8핸들 · 모서리 바깥 회전 · 영역 본체) ──
  const HANDLE_CURSORS = {
    n: "ns-resize", s: "ns-resize", e: "ew-resize", w: "ew-resize",
    nw: "nwse-resize", se: "nwse-resize", ne: "nesw-resize", sw: "nesw-resize",
  };

  function hitTest(pt) {
    const inv = 1 / Math.max(0.2, view.zoom);
    const sel = Store.primaryPatch();
    if (sel && !sel.meta.hidden && !sel.locked) {
      for (const hp of getHandles(sel.rect)) {
        if (Math.abs(pt.x - hp.x) <= 12 * inv && Math.abs(pt.y - hp.y) <= 12 * inv) {
          return { type: "handle", patch: sel, handle: hp.id };
        }
      }
      const [x1, y1, x2, y2] = sel.rect;
      const outside = pt.x < x1 || pt.x > x2 || pt.y < y1 || pt.y > y2;
      if (outside) {
        const corners = [{ x: x1, y: y1 }, { x: x2, y: y1 }, { x: x2, y: y2 }, { x: x1, y: y2 }];
        for (const c of corners) {
          const dist = Math.hypot(pt.x - c.x, pt.y - c.y);
          if (dist > 11 * inv && dist <= 32 * inv) {
            return { type: "rotate", patch: sel };
          }
        }
      }
    }
    for (const p of Store.getPatches()) {
      if (p.meta.hidden) continue;
      const [x1, y1, x2, y2] = p.rect;
      if (pt.x >= x1 && pt.x <= x2 && pt.y >= y1 && pt.y <= y2) {
        return { type: "body", patch: p };
      }
    }
    return null;
  }

  function updateHoverCursor(pt) {
    const vp = $("canvas-viewport");
    if (!vp) return;
    vp.classList.remove("cursor-rotate");
    if (Store.state.tool !== "select" || view.spaceDown) {
      vp.style.cursor = "";
      return;
    }
    const hit = hitTest(pt);
    if (!hit) vp.style.cursor = "default";
    else if (hit.type === "handle") vp.style.cursor = HANDLE_CURSORS[hit.handle] || "pointer";
    else if (hit.type === "rotate") { vp.style.cursor = ""; vp.classList.add("cursor-rotate"); }
    else if (hit.patch.locked) vp.style.cursor = "pointer";
    else vp.style.cursor = "move";
  }

  function updateFooterInfo(pt = null) {
    const infoEl = $("canvas-cursor-info");
    if (infoEl) {
      const selList = Store.selectedPatches();
      const sel = Store.primaryPatch();
      if (selList.length > 1) {
        infoEl.textContent = `${selList.length}개 영역 동시 선택 중 (일괄 편집 가능 · Esc 선택 해제)`;
      } else if (sel) {
        const [x1, y1, x2, y2] = sel.rect;
        infoEl.textContent = `선택: ${sel.name} (${x2 - x1}×${y2 - y1}px · 빈 배경 클릭 또는 Esc로 해제)`;
      } else {
        infoEl.textContent = "선택 해제됨 (원본 전체 밝게 보기) · 클릭해 선택하거나 M으로 추가";
      }
    }
    const coordEl = $("canvas-coords");
    if (coordEl && pt) {
      const cx = Math.max(0, Math.min(view.imgW || 9999, Math.round(pt.x)));
      const cy = Math.max(0, Math.min(view.imgH || 9999, Math.round(pt.y)));
      coordEl.textContent = `${cx}, ${cy}`;
    }
  }

  function setTool(toolName) {
    if (toolName === "sheet") {
      if (window.DPV2Overlays) window.DPV2Overlays.openSheetOverlay();
      return;
    }
    Store.state.tool = toolName;
    document.querySelectorAll(".tool-btn").forEach((b) => b.classList.toggle("on", b.dataset.tool === toolName));
    const vp = $("canvas-viewport");
    if (vp) vp.className = `canvas-viewport mode-${toolName}`;
    const showHud = toolName === "brush" || toolName === "eraser";
    const hud = $("brush-hud");
    if (hud) hud.classList.toggle("hidden", !showHud);
    const lbl = $("brush-mode-label");
    if (lbl) lbl.textContent = toolName === "eraser" ? "마스크 지우개" : "마스크 살리기 브러시";
    if (showHud && !Store.state.mask) {
      const p = Store.primaryPatch();
      const cand = Store.currentCandidate(p);
      if (cand) Store.openMaskSession(cand.key);
      else Store.toast("먼저 생성된 후보가 있는 영역을 선택해 주세요.");
    }
    renderFloatingBar();
    renderMaskPreview();
    Store.emit("tool");
  }

  function bindEvents() {
    const vp = $("canvas-viewport");
    if (!vp) return;

    vp.addEventListener("pointerdown", (e) => {
      if (e.button === 1 || Store.state.tool === "pan" || view.spaceDown) {
        view.drag = { type: "pan", sx: e.clientX, sy: e.clientY, panX: view.panX, panY: view.panY };
        vp.classList.add("panning");
        vp.setPointerCapture(e.pointerId);
        return;
      }
      if (e.button !== 0) return;

      const pt = screenToImage(e.clientX, e.clientY);

      if (Store.state.tool === "rect") {
        view.drag = { type: "draw-rect", x0: pt.x, y0: pt.y, tempRect: [pt.x, pt.y, pt.x + 1, pt.y + 1] };
        vp.setPointerCapture(e.pointerId);
        return;
      }

      if (Store.state.tool === "brush" || Store.state.tool === "eraser") {
        if (!Store.state.mask) {
          Store.toast("후보가 생성된 영역을 먼저 선택한 뒤 브러시를 사용해 주세요.");
          return;
        }
        view.drag = { type: "mask" };
        paintMaskAt(pt, Store.state.tool === "eraser");
        vp.setPointerCapture(e.pointerId);
        return;
      }

      // 선택 · 이동 · 크기 · 회전 도구 (V)
      const hit = hitTest(pt);
      if (!hit) {
        // 버그 2 영구 해결: 빈 배경 클릭 시 선택 완전 해제 → Focus Dim 꺼지고 밝은 원본 표시!
        Store.clearSelection();
        return;
      }

      if (e.shiftKey || e.ctrlKey || e.metaKey) {
        Store.selectPatch(hit.patch.id, { shift: e.shiftKey, ctrl: e.ctrlKey || e.metaKey });
        return;
      }
      if (!Store.state.selectedIds.has(hit.patch.id) || Store.state.selectedIds.size > 1) {
        Store.selectPatch(hit.patch.id);
      }
      if (hit.patch.locked) return;

      if (hit.type === "rotate") {
        const [x1, y1, x2, y2] = hit.patch.rect;
        const cx = (x1 + x2) / 2, cy = (y1 + y2) / 2;
        view.drag = {
          type: "rotate",
          patch: hit.patch,
          startAngle: Math.atan2(pt.y - cy, pt.x - cx),
          origRot: hit.patch.meta.filters.rot || 0,
        };
        hit.patch.meta.filters.transformEnabled = true;
        vp.setPointerCapture(e.pointerId);
        return;
      }

      view.drag = {
        type: hit.type === "handle" ? "resize" : "move",
        patch: hit.patch,
        handle: hit.handle || null,
        startPt: pt,
        origRect: hit.patch.rect.slice(),
        moved: false,
      };
      vp.setPointerCapture(e.pointerId);
    });

    vp.addEventListener("pointermove", (e) => {
      const pt = screenToImage(e.clientX, e.clientY);
      updateFooterInfo(pt);

      if (!view.drag) {
        updateHoverCursor(pt);
        return;
      }
      const dg = view.drag;

      if (dg.type === "pan") {
        view.panX = dg.panX + (e.clientX - dg.sx);
        view.panY = dg.panY + (e.clientY - dg.sy);
        applyTransform();
        return;
      }

      if (dg.type === "draw-rect") {
        const x1 = Math.max(0, Math.min(view.imgW, dg.x0));
        const y1 = Math.max(0, Math.min(view.imgH, dg.y0));
        const x2 = Math.max(0, Math.min(view.imgW, pt.x));
        const y2 = Math.max(0, Math.min(view.imgH, pt.y));
        dg.tempRect = [Math.min(x1, x2), Math.min(y1, y2), Math.max(x1, x2), Math.max(y1, y2)];
        renderOverlaySvg();
        return;
      }

      if (dg.type === "mask") {
        paintMaskAt(pt, Store.state.tool === "eraser");
        return;
      }

      if (dg.type === "rotate" && dg.patch) {
        const [x1, y1, x2, y2] = dg.patch.rect;
        const cx = (x1 + x2) / 2, cy = (y1 + y2) / 2;
        const deg = ((Math.atan2(pt.y - cy, pt.x - cx) - dg.startAngle) * 180) / Math.PI;
        dg.patch.meta.filters.rot = Math.max(-45, Math.min(45, Math.round((dg.origRot + deg) * 2) / 2));
        renderCandidateOverlay();
        Store.emit("filters");
        return;
      }

      if ((dg.type === "move" || dg.type === "resize") && dg.patch) {
        const dx = pt.x - dg.startPt.x;
        const dy = pt.y - dg.startPt.y;
        const [ox1, oy1, ox2, oy2] = dg.origRect;
        const ow = ox2 - ox1, oh = oy2 - oy1;
        let nx1 = ox1, ny1 = oy1, nx2 = ox2, ny2 = oy2;
        if (dg.type === "move") {
          nx1 = Math.max(0, Math.min(view.imgW - ow, Math.round(ox1 + dx)));
          ny1 = Math.max(0, Math.min(view.imgH - oh, Math.round(oy1 + dy)));
          nx2 = nx1 + ow; ny2 = ny1 + oh;
        } else {
          const h = dg.handle;
          if (h.includes("w")) nx1 = Math.max(0, Math.min(ox2 - 48, Math.round(ox1 + dx)));
          if (h.includes("e")) nx2 = Math.min(view.imgW, Math.max(ox1 + 48, Math.round(ox2 + dx)));
          if (h.includes("n")) ny1 = Math.max(0, Math.min(oy2 - 48, Math.round(oy1 + dy)));
          if (h.includes("s")) ny2 = Math.min(view.imgH, Math.max(oy1 + 48, Math.round(oy2 + dy)));
        }
        dg.patch.rect[0] = nx1; dg.patch.rect[1] = ny1; dg.patch.rect[2] = nx2; dg.patch.rect[3] = ny2;
        dg.moved = true;
        renderOverlaySvg();
      }
    });

    vp.addEventListener("pointerup", async (e) => {
      if (!view.drag) return;
      const dg = view.drag;
      view.drag = null;
      vp.classList.remove("panning");
      try { vp.releasePointerCapture(e.pointerId); } catch (_) {}

      if (dg.type === "draw-rect" && dg.tempRect) {
        const [x1, y1, x2, y2] = dg.tempRect;
        renderOverlaySvg();
        if (x2 - x1 >= 24 && y2 - y1 >= 24) {
          await Store.addCrop([x1, y1, x2, y2]);
          setTool("select");
        }
        return;
      }

      if ((dg.type === "move" || dg.type === "resize") && dg.moved && dg.patch) {
        const label = dg.type === "move" ? `영역 이동 (${dg.patch.name})` : `영역 크기 조절 (${dg.patch.name})`;
        await Store.updateCrop(dg.patch.id, { rect: dg.patch.rect.slice() }, label);
      }
    });

    // 커서 중심 휠 줌
    vp.addEventListener("wheel", (e) => {
      e.preventDefault();
      zoomBy(e.deltaY < 0 ? 1.12 : 1 / 1.12, e.clientX, e.clientY);
    }, { passive: false });

    // 분할 비교선 드래그
    const splitLine = $("split-line");
    if (splitLine) {
      let spDrag = false;
      splitLine.addEventListener("pointerdown", (e) => {
        spDrag = true;
        splitLine.setPointerCapture(e.pointerId);
        e.stopPropagation();
      });
      splitLine.addEventListener("pointermove", (e) => {
        if (!spDrag) return;
        const pt = screenToImage(e.clientX, e.clientY);
        Store.state.viewFlags.splitRatio = Math.max(0.02, Math.min(0.98, pt.x / Math.max(1, view.imgW)));
        renderSplitView();
      });
      splitLine.addEventListener("pointerup", () => { spDrag = false; });
    }

    // 툴 레일 아이콘 채우기 & 클릭 바인딩
    const toolIcons = {
      select: ["cursor", "V"],
      rect: ["rectTool", "M"],
      brush: ["brush", "B"],
      eraser: ["eraser", "E"],
      pan: ["hand", "H"],
      sheet: ["scissors", "R"],
    };
    document.querySelectorAll(".tool-btn").forEach((b) => {
      const def = toolIcons[b.dataset.tool];
      if (def) {
        b.innerHTML = "";
        b.append(Data.icon(def[0], 16), Data.el("kbd", { text: def[1] }));
      }
      b.addEventListener("click", () => setTool(b.dataset.tool));
    });

    // 줌 버튼
    const zIn = $("btn-zoom-in"), zOut = $("btn-zoom-out"), zFit = $("btn-zoom-fit");
    if (zIn) zIn.addEventListener("click", () => zoomBy(1.2));
    if (zOut) zOut.addEventListener("click", () => zoomBy(1 / 1.2));
    if (zFit) zFit.addEventListener("click", fitToViewport);

    // 브러시 HUD
    const hudSz = $("hud-brush-size"), hudHd = $("hud-brush-hard");
    if (hudSz) hudSz.addEventListener("input", (e) => {
      const v = Number(e.target.value);
      $("hud-brush-size-val").textContent = `${v}px`;
      if (Store.state.mask) Store.state.mask.brushSize = v;
      const rtSz = $("rt-brush-size");
      if (rtSz) { rtSz.value = String(v); $("rt-brush-size-val").textContent = `${v}px`; }
    });
    if (hudHd) hudHd.addEventListener("input", (e) => {
      const v = Number(e.target.value);
      $("hud-brush-hard-val").textContent = `${v}%`;
      if (Store.state.mask) Store.state.mask.brushHard = v / 100;
    });
    const hudSave = $("btn-hud-save-mask");
    if (hudSave) hudSave.addEventListener("click", () => Store.saveMaskSession());
    const hudDone = $("btn-hud-done-mask");
    if (hudDone) hudDone.addEventListener("click", () => setTool("select"));

    // 라이브 후보 프리뷰 즉시 채택 버튼
    const livePick = $("btn-live-pick");
    if (livePick) livePick.addEventListener("click", async () => {
      const patch = Store.primaryPatch();
      const cand = Store.currentCandidate(patch);
      if (!patch || !patch.target || !cand) return;
      try {
        await Api.pickCandidate(Store.state.root, {
          crop_id: patch.id, tid: patch.target.tid, key: cand.key, mode: "set",
        });
        Store.toast(`${patch.name}에 후보 #${cand.n || 1}을 확정 적용했습니다 ★`, "ok");
        await Store.refreshAll(true);
      } catch (err) {
        Store.toast(`채택 실패: ${err.message}`, "err");
      }
    });

    // 플로팅 바 컨트롤 바인딩
    const fbInp = $("fb-instruction");
    if (fbInp) fbInp.addEventListener("input", (e) => {
      const p = Store.primaryPatch();
      if (!p) return;
      p.meta.user_instruction_ko = e.target.value;
      p.meta.dirtySpec = true;
      const rt = $("inp-instruction");
      if (rt && document.activeElement !== rt) rt.value = e.target.value;
    });
    const fbAi = $("fb-ai-btn");
    if (fbAi) fbAi.addEventListener("click", () => window.DPV2Inspector && window.DPV2Inspector.runAssistForSelection());
    const fbModel = $("fb-model-btn");
    if (fbModel) fbModel.addEventListener("click", (e) => window.DPV2Inspector && window.DPV2Inspector.openModelPopover(e.currentTarget));
    const fbRun = $("fb-run-btn");
    if (fbRun) fbRun.addEventListener("click", () => window.DPV2Inspector && window.DPV2Inspector.runSelectedGeneration(true));
    const fbCmp = $("fb-compare-btn");
    if (fbCmp) fbCmp.addEventListener("click", () => window.DPV2Overlays && window.DPV2Overlays.openCompareOverlay());
    const fbRatio = $("fb-ratio-btn");
    if (fbRatio) fbRatio.addEventListener("click", async () => {
      const p = Store.primaryPatch();
      if (!p || !window.DPLayoutGeom || !p.modelInfo || !p.modelInfo.ratios.length) return;
      const best = window.DPLayoutGeom.ratioFitRect(p.rect, view.imgW, view.imgH, p.modelInfo.ratios, Data.RATIO_SIZES);
      if (best) {
        await Store.updateCrop(p.id, { rect: best }, `비율 맞춤 (${p.name})`);
        Store.toast("선택 모델 비율에 맞춰 영역 틀을 조정했습니다.", "ok");
      }
    });
    const fbLock = $("fb-lock-btn");
    if (fbLock) fbLock.addEventListener("click", async () => {
      const p = Store.primaryPatch();
      if (!p) return;
      await Store.updateCrop(p.id, { locked: !p.locked }, p.locked ? `잠금 해제 (${p.name})` : `잠금 (${p.name})`);
    });
    const fbDel = $("fb-del-btn");
    if (fbDel) fbDel.addEventListener("click", async () => {
      const p = Store.primaryPatch();
      if (!p) return;
      const ok = await Data.askDialogConfirm(`"${p.name}" 수정 영역을 삭제할까요?`, { okText: "삭제", cancelText: "취소" });
      if (ok) await Store.deleteCrop(p.id);
    });
    const fbDesel = $("fb-deselect-btn");
    if (fbDesel) fbDesel.addEventListener("click", () => Store.clearSelection());

    // 플로팅 바에 레퍼런스 드롭 허용
    const fb = $("floating-bar");
    if (fb) {
      fb.addEventListener("dragover", (e) => {
        if (e.dataTransfer?.types?.includes("text/dps-ref-file")) {
          e.preventDefault();
          fb.classList.add("drop-hover");
        }
      });
      fb.addEventListener("dragleave", () => fb.classList.remove("drop-hover"));
      fb.addEventListener("drop", async (e) => {
        fb.classList.remove("drop-hover");
        const refName = e.dataTransfer?.getData("text/dps-ref-file");
        const p = Store.primaryPatch();
        if (refName && p && window.DPV2Inspector) {
          e.preventDefault();
          await window.DPV2Inspector.linkRefToPatches(refName, Store.selectedPatches());
        }
      });
    }

    // 바깥 어둡게 슬라이더
    const dimSlider = $("input-focus-dim");
    if (dimSlider) {
      dimSlider.addEventListener("input", (e) => {
        const val = Number(e.target.value);
        Store.savePrefs({ focusDimAlpha: val });
        renderOverlaySvg();
      });
    }

    // 리사이즈 대응
    if (typeof ResizeObserver !== "undefined") {
      new ResizeObserver(() => {
        if (view.imgW && view.imgH) applyTransform();
      }).observe(vp);
    }

    Store.subscribe((reason) => {
      if (reason === "state") syncCanvasImages();
      else if (reason === "selection" || reason === "prefs" || reason === "filters") {
        renderOverlaySvg();
        renderCandidateOverlay();
        renderSplitView();
        renderFloatingBar();
        updateFooterInfo();
      } else if (reason === "mask") {
        renderMaskPreview();
      }
    });
  }

  return {
    view,
    resolveCandidateOverlay,
    fitToViewport,
    zoomBy,
    setTool,
    getBaseImage,
    syncCanvasImages,
    renderOverlaySvg,
    renderSplitView,
    renderCandidateOverlay,
    renderFloatingBar,
    bindEvents,
  };
})();
