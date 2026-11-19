// BMK Design Patch Studio v2 — 인터랙티브 라이브 캔버스(Focus Dim · 8핸들/회전 커서 · 전/후 비교 · 마스크 브러시 · 자동 맞춤).
"use strict";

window.DPV2Canvas = (() => {
  const Data = window.DPV2Data;
  const Api = window.DPV2Api;
  const Store = window.DPV2Store;

  const CURSORS = {
    n: "ns-resize", s: "ns-resize", e: "ew-resize", w: "ew-resize",
    ne: "nesw-resize", sw: "nesw-resize", nw: "nwse-resize", se: "nwse-resize",
  };
  const HANDLES = ["nw", "n", "ne", "e", "se", "s", "sw", "w"];

  let vp, world, imgBase, imgComp, candOv, imgCandLive, splitClip, imgSplitBefore, splitBar, maskCanvas, svg;
  let cam = { z: 1, tx: 0, ty: 0 };
  let canvasSize = [1024, 1024];
  let userAdjustedView = false;
  let spaceHeld = false;
  let drag = null;
  let draftRect = null;
  let cursorPt = null;
  let lastBaseUrl = "";
  let lastCompUrl = "";
  let lastCandLiveUrl = "";

  function $(id) { return document.getElementById(id); }

  function init() {
    vp = $("canvas-viewport");
    world = $("canvas-world");
    imgBase = $("img-base");
    imgComp = $("img-composite");
    candOv = $("cand-live-overlay");
    imgCandLive = $("img-cand-live");
    splitClip = $("canvas-split-clip");
    imgSplitBefore = $("img-split-before");
    splitBar = $("canvas-split-bar");
    maskCanvas = $("mask-live-canvas");
    svg = $("canvas-svg");

    wireViewportEvents();
    wireToolRailAndFooter();

    // 최초 뷰포트 크기 확정 시 캔버스 중앙 자동 맞춤 (사용자 조작 이후엔 덮어쓰지 않음)
    if (window.ResizeObserver) {
      new ResizeObserver(() => {
        if (!userAdjustedView && vp.clientWidth > 40 && vp.clientHeight > 40) fitAll(false);
      }).observe(vp);
    }

    Store.subscribe((reason) => {
      if (reason === "state" || reason === "selection" || reason === "prefs" || reason === "mask" || reason === "view" || reason === "tool") {
        render();
      }
    });
  }

  function screenToCanvas(clientX, clientY) {
    const r = vp.getBoundingClientRect();
    return [(clientX - r.left - cam.tx) / cam.z, (clientY - r.top - cam.ty) / cam.z];
  }

  function applyTransform() {
    world.style.width = `${canvasSize[0]}px`;
    world.style.height = `${canvasSize[1]}px`;
    world.style.transform = `translate(${cam.tx}px, ${cam.ty}px) scale(${cam.z})`;
    const zEl = $("zoom-readout");
    if (zEl) zEl.textContent = `${Math.round(cam.z * 100)}%`;
    updateSplitPosition();
  }

  function fitAll(markUser = true) {
    if (!vp || !vp.clientWidth || !vp.clientHeight) return;
    if (markUser) userAdjustedView = true;
    const pad = 64;
    const zx = (vp.clientWidth - pad * 2) / Math.max(1, canvasSize[0]);
    const zy = (vp.clientHeight - pad * 2) / Math.max(1, canvasSize[1]);
    cam.z = Math.max(0.05, Math.min(2, Math.min(zx, zy)));
    cam.tx = (vp.clientWidth - canvasSize[0] * cam.z) / 2;
    cam.ty = (vp.clientHeight - canvasSize[1] * cam.z) / 2;
    applyTransform();
    renderSvg();
  }

  function fitCrop(id) {
    const p = id ? Store.getPatch(id) : Store.primaryPatch();
    if (!p || !vp.clientWidth || !vp.clientHeight) return fitAll(true);
    userAdjustedView = true;
    const [x0, y0, x1, y1] = p.rect;
    const cw = Math.max(64, x1 - x0), ch = Math.max(64, y1 - y0);
    cam.z = Math.max(0.1, Math.min(4, Math.min((vp.clientWidth - 140) / cw, (vp.clientHeight - 160) / ch)));
    cam.tx = vp.clientWidth / 2 - ((x0 + x1) / 2) * cam.z;
    cam.ty = vp.clientHeight / 2 - ((y0 + y1) / 2) * cam.z;
    applyTransform();
    renderSvg();
  }

  function zoomTo(targetZ, anchorClientX = null, anchorClientY = null) {
    userAdjustedView = true;
    const nextZ = Math.max(0.08, Math.min(12, targetZ));
    const r = vp.getBoundingClientRect();
    const ax = anchorClientX !== null ? (anchorClientX - r.left) : r.width / 2;
    const ay = anchorClientY !== null ? (anchorClientY - r.top) : r.height / 2;
    const cx = (ax - cam.tx) / cam.z;
    const cy = (ay - cam.ty) / cam.z;
    cam.z = nextZ;
    cam.tx = ax - cx * cam.z;
    cam.ty = ay - cy * cam.z;
    applyTransform();
    renderSvg();
  }

  function updateSplitPosition() {
    const on = Store.state.viewFlags.splitOn;
    splitClip.classList.toggle("hidden", !on);
    splitBar.classList.toggle("hidden", !on);
    if (!on) return;
    const ratio = Store.state.viewFlags.splitRatio;
    splitClip.style.width = `${Math.round(canvasSize[0] * ratio)}px`;
    const barLeft = cam.tx + canvasSize[0] * ratio * cam.z;
    splitBar.style.left = `${barLeft}px`;
  }

  function render() {
    const S = Store.state.S;
    const emptyEl = $("canvas-empty");
    const emptyTitle = $("canvas-empty-title");
    const emptySub = $("canvas-empty-sub");
    const applyFirstBtn = $("btn-canvas-apply-first");

    if (!S || !S.canvas) {
      emptyEl.classList.remove("hidden");
      if (S && !S.canvas) {
        emptyTitle.textContent = "아직 원화를 불러오지 않았습니다";
        emptySub.textContent = "무료 반영을 한 번 실행하면 원화 캔버스와 크롭 레이어가 준비됩니다.";
        applyFirstBtn.classList.remove("hidden");
      } else {
        emptyTitle.textContent = "캔버스를 불러오는 중…";
        emptySub.textContent = "";
        applyFirstBtn.classList.add("hidden");
      }
      renderFloatingBar();
      return;
    }

    emptyEl.classList.add("hidden");
    const [w, h] = S.canvas.size || [1024, 1024];
    const sizeChanged = canvasSize[0] !== w || canvasSize[1] !== h;
    canvasSize = [w, h];
    svg.setAttribute("viewBox", `0 0 ${w} ${h}`);

    const baseRel = S.canvas.base_url || `canvas?root=${encodeURIComponent(Store.state.root)}&kind=base`;
    const compRel = S.canvas.composite_url || `canvas?root=${encodeURIComponent(Store.state.root)}&kind=composite&v=${encodeURIComponent(S.canvas.composite_sig || "")}`;
    const baseUrl = Api.apiUrl(baseRel);
    const compUrl = Api.apiUrl(compRel);
    if (baseUrl && lastBaseUrl !== baseUrl) {
      lastBaseUrl = baseUrl;
      imgBase.src = baseUrl;
      imgSplitBefore.src = baseUrl;
    }
    if (compUrl && lastCompUrl !== compUrl) {
      lastCompUrl = compUrl;
      imgComp.src = compUrl;
    }

    // Before (Q) 누르는 동안은 합성·후보 겹침 모두 숨기고 원본만 표시
    imgComp.classList.toggle("hidden", Store.state.viewFlags.beforeHeld);

    if (sizeChanged && !userAdjustedView) fitAll(false);
    else applyTransform();

    renderCandidateOverlay();
    renderMaskLive();
    renderSvg();
    renderFloatingBar();
    updateCursorClasses();
    updateBrushHud();
  }

  // 선택 후보 실시간 캔버스 겹침 계산 (viewer.js drawOverlay 와 같은 규칙: pick_url + region 우선, 없으면 rect + big_url 폴백)
  function resolveCandidateOverlay(patch, cand) {
    if (!patch || !cand || patch.meta.hidden) return null;
    if (Store.state.viewFlags.beforeHeld) return null;
    const mk = Store.state.mask;
    if (mk && mk.layers && (Store.state.rightTab === "retouch" || Store.state.tool === "brush" || Store.state.tool === "eraser")) {
      return null;
    }
    const sc = patch.stateCrop || {};
    const hasRegion = Array.isArray(sc.region) && sc.region.length === 4;
    // 미채택 후보: pick_url(인접 패치까지 포함한 합성 미리보기) 우선 → 없으면 후보 원화/큰 썸네일을 rect 자리에 겹침
    if (!cand.picked) {
      if (cand.pick_url && hasRegion) return { rect: sc.region, rel: cand.pick_url, previewing: true };
      const fallbackRel = cand.full_url || cand.big_url || cand.thumb_url;
      if (fallbackRel) return { rect: patch.rect, rel: fallbackRel, previewing: true };
      return null;
    }
    // ★ 채택 후보: region_url 이 있으면 원해상도 겹침으로 선명하게 표시
    if (hasRegion && sc.region_url) {
      return { rect: sc.region, rel: sc.region_url, previewing: false };
    }
    return null;
  }

  function renderCandidateOverlay() {
    if (!candOv || !imgCandLive) return;
    const p = Store.primaryPatch();
    const c = Store.currentCandidate(p);
    const ov = resolveCandidateOverlay(p, c);
    if (!ov) {
      candOv.classList.add("hidden");
      return;
    }
    const url = Api.apiUrl(ov.rel);
    if (!url) {
      candOv.classList.add("hidden");
      return;
    }
    const [x0, y0, x1, y1] = ov.rect;
    candOv.classList.remove("hidden");
    candOv.style.left = `${x0}px`;
    candOv.style.top = `${y0}px`;
    candOv.style.width = `${Math.max(1, x1 - x0)}px`;
    candOv.style.height = `${Math.max(1, y1 - y0)}px`;
    if (lastCandLiveUrl !== url) {
      lastCandLiveUrl = url;
      candOv.classList.add("loading");
      imgCandLive.onload = imgCandLive.onerror = () => {
        if (lastCandLiveUrl === url) candOv.classList.remove("loading");
      };
      imgCandLive.src = url;
      if (imgCandLive.complete) candOv.classList.remove("loading");
    }
  }

  function updateCursorClasses() {
    vp.classList.remove("mode-pan", "mode-panning", "mode-rect", "mode-brush", "mode-eraser");
    const tool = spaceHeld ? "pan" : Store.state.tool;
    if (drag && drag.kind === "pan") vp.classList.add("mode-panning");
    else if (tool === "pan") vp.classList.add("mode-pan");
    else if (tool === "rect") vp.classList.add("mode-rect");
    else if (tool === "brush") vp.classList.add("mode-brush");
    else if (tool === "eraser") vp.classList.add("mode-eraser");
  }

  function updateBrushHud() {
    const hud = $("brush-hud");
    const isBrush = Store.state.tool === "brush" || Store.state.tool === "eraser";
    hud.classList.toggle("hidden", !isBrush);
    if (!isBrush) return;
    $("brush-hud-label").textContent = Store.state.tool === "eraser" ? "마스크 지우개 (E)" : "마스크 브러시 (B)";
    const mk = Store.state.mask;
    if (mk) {
      $("hud-brush-size").value = String(mk.brushSize);
      $("hud-brush-size-val").textContent = `${mk.brushSize}px`;
      $("hud-brush-hard").value = String(Math.round(mk.brushHard * 100));
      $("hud-brush-hard-val").textContent = `${Math.round(mk.brushHard * 100)}%`;
      $("hud-brush-flow").value = String(Math.round(mk.brushFlow * 100));
      $("hud-brush-flow-val").textContent = `${Math.round(mk.brushFlow * 100)}%`;
      $("btn-hud-apply-mask").disabled = !mk.dirty;
    }
  }

  // ── 실시간 마스크 3층 합성 캔버스 (브러시/지우개 사용 시) ──
  function renderMaskLive() {
    const mk = Store.state.mask;
    const show = !!(mk && mk.layers && (Store.state.rightTab === "retouch" || Store.state.tool === "brush" || Store.state.tool === "eraser"));
    maskCanvas.classList.toggle("hidden", !show);
    if (!show) return;
    const [bx0, by0, bx1, by1] = mk.layers.box;
    const bw = bx1 - bx0, bh = by1 - by0;
    if (maskCanvas.width !== bw || maskCanvas.height !== bh) {
      maskCanvas.width = bw;
      maskCanvas.height = bh;
    }
    maskCanvas.style.left = `${bx0}px`;
    maskCanvas.style.top = `${by0}px`;
    maskCanvas.style.width = `${bw}px`;
    maskCanvas.style.height = `${bh}px`;

    const ctx = maskCanvas.getContext("2d");
    const imgData = ctx.createImageData(bw, bh);
    const out = imgData.data;
    const { under: u, patch: p, over: o } = mk.layers;
    const [wx0, wy0] = mk.win;
    const dx = bx0 - wx0, dy = by0 - wy0;
    const ww = mk.w, wh = mk.h;

    for (let y = 0; y < bh; y++) {
      const wy = y + dy;
      for (let x = 0; x < bw; x++) {
        const i = (y * bw + x) * 4;
        const wx = x + dx;
        const inWin = wx >= 0 && wx < ww && wy >= 0 && wy < wh;
        const mv = inWin ? mk.m[wy * ww + wx] : 0;
        const pa = (p[i + 3] / 255) * mv;
        const oa = o[i + 3] / 255;
        out[i] = o[i] * oa + (u[i] + (p[i] - u[i]) * pa) * (1 - oa);
        out[i + 1] = o[i + 1] * oa + (u[i + 1] + (p[i + 1] - u[i + 1]) * pa) * (1 - oa);
        out[i + 2] = o[i + 2] * oa + (u[i + 2] + (p[i + 2] - u[i + 2]) * pa) * (1 - oa);
        out[i + 3] = 255;
      }
    }
    ctx.putImageData(imgData, 0, 0);
  }

  function stampMaskAt(cx, cy) {
    const mk = Store.state.mask;
    if (!mk) return;
    const lx = cx - mk.win[0], ly = cy - mk.win[1];
    const R = Math.max(2, mk.brushSize / 2);
    const x0 = Math.max(0, Math.floor(lx - R));
    const y0 = Math.max(0, Math.floor(ly - R));
    const x1 = Math.min(mk.w, Math.ceil(lx + R));
    const y1 = Math.min(mk.h, Math.ceil(ly + R));
    const erase = Store.state.tool === "eraser";
    const hard = mk.brushHard;
    const flow = mk.brushFlow;
    for (let y = y0; y < y1; y++) {
      for (let x = x0; x < x1; x++) {
        const dist = Math.hypot(x + 0.5 - lx, y + 0.5 - ly);
        if (dist >= R) continue;
        let s = 1;
        if (dist > R * hard) {
          const t = (dist - R * hard) / Math.max(0.001, R - R * hard);
          s = 1 - t * t * (3 - 2 * t);
        }
        const idx = y * mk.w + x;
        const cur = mk.m[idx];
        mk.m[idx] = erase ? cur * (1 - s * flow) : cur + (1 - cur) * s * flow;
      }
    }
    mk.dirty = true;
    renderMaskLive();
  }

  // ── SVG 오버레이: Focus Dim (피드백 #8) + 패치 바운딩박스 + 8핸들/회전 + 이음매 ──
  function renderSvg() {
    svg.replaceChildren();
    const [W, H] = canvasSize;
    const patches = Store.getPatches().filter((p) => !p.meta.hidden);
    const selPatches = Store.selectedPatches().filter((p) => !p.meta.hidden);
    const dimAlpha = (Store.state.prefs.focusDimAlpha ?? 42) / 100;

    // 1. 선택 영역 바깥만 어둡게 하는 SVG evenodd Focus Dim (선택 영역 내부 노란 틴트 완전 제거!)
    if (dimAlpha > 0 && selPatches.length && !Store.state.viewFlags.beforeHeld) {
      let d = `M0 0 H${W} V${H} H0 Z`;
      for (const p of selPatches) {
        const [x0, y0, x1, y1] = (drag && drag.id === p.id && drag.previewRect) ? drag.previewRect : p.rect;
        d += ` M${x0} ${y0} H${x1} V${y1} H${x0} Z`;
      }
      svg.append(Data.svgEl("path", {
        d,
        fill: "#000000",
        "fill-opacity": dimAlpha.toFixed(2),
        "fill-rule": "evenodd",
        "pointer-events": "none",
      }));
    }

    const invZ = 1 / Math.max(0.05, cam.z);

    // 2. 이음매 세그먼트 표시 (N 토글 — 서버 실제 좌표 x0/y0/x1/y1 과 line 둘 다 수용)
    if (Store.state.viewFlags.seamsOn && Store.state.seams && Array.isArray(Store.state.seams.segments)) {
      for (const seg of Store.state.seams.segments) {
        const coords = (seg.x0 !== undefined && seg.y0 !== undefined && seg.x1 !== undefined && seg.y1 !== undefined)
          ? [seg.x0, seg.y0, seg.x1, seg.y1]
          : (Array.isArray(seg.line) && seg.line.length === 4 ? seg.line : null);
        if (!coords) continue;
        svg.append(Data.svgEl("line", {
          x1: coords[0], y1: coords[1], x2: coords[2], y2: coords[3],
          stroke: seg.severity === "bad" ? "#ef4444" : "#f59e0b",
          "stroke-width": 4 * invZ,
          "stroke-linecap": "round",
          "pointer-events": "none",
        }));
      }
    }

    // 3. 패치 사각형 & 핸들
    const accent = getComputedStyle(document.documentElement).getPropertyValue("--accent").trim() || "#dfe104";
    for (const p of patches) {
      const isPrimary = p.id === (Store.primaryPatch() && Store.primaryPatch().id);
      const isSelected = Store.state.selectedIds.has(p.id) || isPrimary;
      const rect = (drag && drag.id === p.id && drag.previewRect) ? drag.previewRect : p.rect;
      const [x0, y0, x1, y1] = rect;
      const rw = Math.max(1, x1 - x0), rh = Math.max(1, y1 - y0);
      const rot = (drag && drag.id === p.id && drag.previewRot !== undefined) ? drag.previewRot : (p.meta.rotDeg || 0);
      const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;

      const g = Data.svgEl("g", rot ? { transform: `rotate(${rot} ${cx} ${cy})` } : {});

      // AI 자동 크롭 경계 점선 미리보기
      if (Store.state.prefs.showAutoCrop && isSelected && window.DPLayoutGeom) {
        const ac = window.DPLayoutGeom.autoCrop(rect, canvasSize);
        if (ac) {
          g.append(Data.svgEl("rect", {
            x: ac[0], y: ac[1], width: ac[2] - ac[0], height: ac[3] - ac[1],
            fill: "none", stroke: "#38bdf8", "stroke-width": 1.5 * invZ,
            "stroke-dasharray": `${6 * invZ} ${4 * invZ}`, "pointer-events": "none",
          }));
        }
      }

      // 선택 영역 본체 (fill="none" — 원본 색감 왜곡 0%!)
      const box = Data.svgEl("rect", {
        x: x0, y: y0, width: rw, height: rh,
        fill: "transparent",
        stroke: isSelected ? accent : "rgba(228,228,231,0.58)",
        "stroke-width": (isPrimary ? 2.4 : isSelected ? 1.8 : 1.3) * invZ,
        "stroke-dasharray": p.locked ? `${5 * invZ} ${3 * invZ}` : "",
        style: `cursor:${Store.state.tool === "select" && !p.locked ? "move" : "pointer"}`,
        "data-crop-id": p.id,
        "data-hit": "body",
      });
      g.append(box);

      // 상단 인덱스/이름 배지
      const labelH = 19 * invZ;
      const labelText = `${p.index}. ${p.name}${p.locked ? " 🔒" : ""}`;
      const approxW = Math.max(68, labelText.length * 7.8 + 14) * invZ;
      const ly = y0 - labelH - 2 * invZ < 0 ? y0 + 2 * invZ : y0 - labelH - 2 * invZ;
      g.append(Data.svgEl("rect", {
        x: x0, y: ly, width: approxW, height: labelH, rx: 3 * invZ,
        fill: isSelected ? accent : "rgba(15,15,18,0.86)",
        "pointer-events": "none",
      }));
      const txt = Data.svgEl("text", {
        x: x0 + 6 * invZ, y: ly + 13.5 * invZ,
        fill: isSelected ? "#09090b" : "#f4f4f5",
        "font-size": 11 * invZ,
        "font-weight": "700",
        "pointer-events": "none",
      });
      txt.textContent = labelText;
      g.append(txt);

      // 다중 타깃(A·B·C) focus_box 서브 프레임 표시 (선택 패치에서 클릭으로 타깃 전환)
      if (isPrimary && p.targets && p.targets.length > 1) {
        const boxPalette = ["#ff5d5d", "#46d17e", "#4da3ff", "#ffa24d"];
        p.targets.forEach((tg, ti) => {
          const fb = Array.isArray(tg.focus_box) && tg.focus_box.length === 4 ? tg.focus_box : null;
          if (!fb) return;
          const activeTg = p.target && p.target.key === tg.key;
          const col = boxPalette[ti % boxPalette.length];
          g.append(Data.svgEl("rect", {
            x: x0 + fb[0], y: y0 + fb[1],
            width: Math.max(1, fb[2] - fb[0]), height: Math.max(1, fb[3] - fb[1]),
            fill: "transparent",
            stroke: col,
            "stroke-width": (activeTg ? 2.2 : 1.3) * invZ,
            "stroke-dasharray": activeTg ? "" : `${4 * invZ} ${3 * invZ}`,
            style: "cursor:pointer",
            "data-crop-id": p.id, "data-target-key": tg.key, "data-hit": "target",
          }));
          const tlbl = Data.svgEl("text", {
            x: x0 + fb[0] + 4 * invZ, y: y0 + fb[1] + 13 * invZ,
            fill: col, "font-size": 11 * invZ, "font-weight": "700", "pointer-events": "none",
          });
          tlbl.textContent = tg.tid;
          g.append(tlbl);
        });
      }

      // 주 선택 패치 & 잠금 해제 & select 도구: 8개 리사이즈 핸들 + 4개 바깥 회전 구역 + 상단 회전 노브
      if (isPrimary && !p.locked && Store.state.tool === "select") {
        const hs = 8 * invZ;
        const rotZone = 14 * invZ;
        const outerCorners = [
          [x0 - rotZone, y0 - rotZone],
          [x1, y0 - rotZone],
          [x1, y1],
          [x0 - rotZone, y1],
        ];
        for (const [rx, ry] of outerCorners) {
          g.append(Data.svgEl("rect", {
            x: rx, y: ry, width: rotZone, height: rotZone,
            fill: "transparent", class: "cursor-rotate",
            "data-crop-id": p.id, "data-hit": "rotate",
          }));
        }

        // 상단 회전 연결선 & 핸들
        const rotY = y0 - 22 * invZ;
        g.append(Data.svgEl("line", {
          x1: cx, y1: y0, x2: cx, y2: rotY,
          stroke: accent, "stroke-width": 1.4 * invZ, "pointer-events": "none",
        }));
        g.append(Data.svgEl("circle", {
          cx, cy: rotY, r: 5.5 * invZ,
          fill: "#09090b", stroke: accent, "stroke-width": 1.8 * invZ,
          class: "cursor-rotate", "data-crop-id": p.id, "data-hit": "rotate",
        }));

        // 8개 리사이즈 핸들
        const pts = {
          nw: [x0, y0], n: [cx, y0], ne: [x1, y0], e: [x1, cy],
          se: [x1, y1], s: [cx, y1], sw: [x0, y1], w: [x0, cy],
        };
        for (const hKey of HANDLES) {
          const [hx, hy] = pts[hKey];
          g.append(Data.svgEl("rect", {
            x: hx - hs / 2, y: hy - hs / 2, width: hs, height: hs,
            fill: "#ffffff", stroke: "#09090b", "stroke-width": 1.4 * invZ,
            style: `cursor:${CURSORS[hKey]}`,
            "data-crop-id": p.id, "data-hit": "resize", "data-handle": hKey,
          }));
        }
      }
      svg.append(g);
    }

    // 4. 새 영역 드래그 미리보기 (M 도구)
    if (draftRect) {
      const [dx0, dy0, dx1, dy1] = draftRect;
      svg.append(Data.svgEl("rect", {
        x: dx0, y: dy0, width: Math.max(1, dx1 - dx0), height: Math.max(1, dy1 - dy0),
        fill: "none", stroke: accent, "stroke-width": 2 * invZ,
        "stroke-dasharray": `${6 * invZ} ${4 * invZ}`, "pointer-events": "none",
      }));
    }

    // 5. 브러시/지우개 커서 원
    if (cursorPt && (Store.state.tool === "brush" || Store.state.tool === "eraser")) {
      const r = ((Store.state.mask && Store.state.mask.brushSize) || 48) / 2;
      svg.append(Data.svgEl("circle", {
        cx: cursorPt[0], cy: cursorPt[1], r,
        fill: "none",
        stroke: Store.state.tool === "eraser" ? "#ef4444" : "#22c55e",
        "stroke-width": 1.6 * invZ,
        "pointer-events": "none",
      }));
    }
  }

  // ── 하단 도킹 컨텍스트 바 ──
  function renderFloatingBar() {
    const bar = $("floating-bar");
    const p = Store.primaryPatch();
    if (!p || !Store.state.S || !Store.state.S.canvas) {
      bar.classList.add("hidden");
      return;
    }
    bar.classList.remove("hidden");
    const [x0, y0, x1, y1] = p.rect;
    const cand = Store.currentCandidate(p);
    const tgLabel = p.targets && p.targets.length > 1 && p.target ? ` · ${p.target.tid}` : "";
    const prevLabel = cand && !cand.picked ? ` [미리보기 c${String(cand.n).padStart(2, "0")}]` : "";
    $("fb-name").textContent = `${p.name}${tgLabel}${prevLabel}`;
    $("fb-dims").textContent = `${x1 - x0}×${y1 - y0}`;

    const grp = $("fb-purpose-group");
    grp.replaceChildren();
    for (const purp of Data.PURPOSES) {
      const btn = Data.el("button", {
        type: "button",
        className: "mini-btn" + (p.meta.purpose === purp.id ? " on" : ""),
        text: purp.short,
        title: purp.desc,
        onClick: () => {
          p.meta.purpose = purp.id;
          Store.emit("selection");
        },
      });
      grp.append(btn);
    }

    // 비율 고정 모델(나노바나나 등)일 때 [비율 맞추기] 원클릭 버튼 표시
    const ratioBtn = $("btn-fb-ratio-fit");
    const hasRatios = p.modelInfo && p.modelInfo.ratios && p.modelInfo.ratios.length > 0 && window.DPLayoutGeom;
    const matchedRatio = hasRatios ? window.DPLayoutGeom.ratioMatch(x1 - x0, y1 - y0, p.modelInfo.ratios, 0.01, Data.RATIO_SIZES) : "ok";
    ratioBtn.classList.toggle("hidden", !hasRatios || !!matchedRatio);

    $("btn-fb-compare").classList.toggle("hidden", !p.candidates.length);
    $("btn-fb-lock").textContent = p.locked ? "잠금 해제" : "잠금";
  }

  // ── 포인터 & 휠 & 도구 이벤트 ──
  function wireViewportEvents() {
    vp.addEventListener("wheel", (e) => {
      e.preventDefault();
      const factor = Math.exp(-e.deltaY * 0.0014);
      zoomTo(cam.z * factor, e.clientX, e.clientY);
    }, { passive: false });

    vp.addEventListener("pointerdown", (e) => {
      if (e.button === 1 || spaceHeld || Store.state.tool === "pan") {
        e.preventDefault();
        userAdjustedView = true;
        drag = { kind: "pan", startX: e.clientX, startY: e.clientY, tx0: cam.tx, ty0: cam.ty, pid: e.pointerId };
        vp.setPointerCapture(e.pointerId);
        updateCursorClasses();
        return;
      }
      if (e.button !== 0) return;

      const pt = screenToCanvas(e.clientX, e.clientY);

      if (Store.state.tool === "brush" || Store.state.tool === "eraser") {
        if (!Store.state.mask) {
          const p = Store.primaryPatch();
          if (p && p.pickedCand) {
            Store.openMaskSession(p.pickedCand.key).then(() => Store.toast("마스크 브러시가 준비되었습니다 — 다시 칠해 주세요."));
          } else {
            Store.toast("먼저 후보 비교 탭에서 ★ 후보를 하나 채택하면 마스크 브러시를 쓸 수 있습니다.", "warn");
          }
          return;
        }
        e.preventDefault();
        drag = { kind: "brush", pid: e.pointerId };
        vp.setPointerCapture(e.pointerId);
        stampMaskAt(pt[0], pt[1]);
        return;
      }

      if (Store.state.tool === "rect") {
        e.preventDefault();
        drag = { kind: "newRect", startPt: pt, pid: e.pointerId };
        vp.setPointerCapture(e.pointerId);
        return;
      }

      const hit = e.target && e.target.getAttribute ? e.target.getAttribute("data-hit") : null;
      const cropId = e.target && e.target.getAttribute ? e.target.getAttribute("data-crop-id") : null;
      if (hit === "target" && cropId) {
        const tgKey = e.target.getAttribute("data-target-key");
        if (tgKey) Store.selectTargetForCrop(cropId, tgKey);
        return;
      }
      if (hit && cropId) {
        const p = Store.getPatch(cropId);
        if (!p) return;
        Store.selectPatch(cropId, { shift: e.shiftKey, ctrl: e.ctrlKey || e.metaKey });
        if (p.locked) {
          Store.toast("잠긴 패치입니다 — 잠금을 해제한 뒤 이동·크기 조절하세요.", "warn");
          return;
        }
        e.preventDefault();
        if (hit === "body") {
          drag = { kind: "move", id: cropId, startPt: pt, rect0: p.rect.slice(), previewRect: p.rect.slice(), moved: false, pid: e.pointerId };
        } else if (hit === "resize") {
          const handle = e.target.getAttribute("data-handle");
          drag = { kind: "resize", id: cropId, handle, startPt: pt, rect0: p.rect.slice(), previewRect: p.rect.slice(), moved: false, pid: e.pointerId };
        } else if (hit === "rotate") {
          const cx = (p.rect[0] + p.rect[2]) / 2, cy = (p.rect[1] + p.rect[3]) / 2;
          const a0 = Math.atan2(pt[1] - cy, pt[0] - cx);
          drag = { kind: "rotate", id: cropId, cx, cy, a0, rot0: p.meta.rotDeg || 0, previewRot: p.meta.rotDeg || 0, moved: false, pid: e.pointerId };
        }
        vp.setPointerCapture(e.pointerId);
      }
    });

    vp.addEventListener("pointermove", (e) => {
      const pt = screenToCanvas(e.clientX, e.clientY);
      cursorPt = pt;
      const coords = $("canvas-coords");
      if (coords) coords.textContent = `${Math.round(pt[0])}, ${Math.round(pt[1])}`;
      if (Store.state.tool === "brush" || Store.state.tool === "eraser") renderSvg();

      if (!drag || drag.pid !== e.pointerId) return;
      e.preventDefault();
      if (drag.kind === "pan") {
        cam.tx = drag.tx0 + (e.clientX - drag.startX);
        cam.ty = drag.ty0 + (e.clientY - drag.startY);
        applyTransform();
        return;
      }
      if (drag.kind === "brush") {
        stampMaskAt(pt[0], pt[1]);
        return;
      }
      const dx = pt[0] - drag.startPt[0], dy = pt[1] - drag.startPt[1];
      const snap = !e.altKey;
      const G = window.DPLayoutGeom;
      if (drag.kind === "newRect") {
        draftRect = G ? G.newRect(drag.startPt[0], drag.startPt[1], dx, dy, canvasSize, snap, e.shiftKey)
          : [Math.min(drag.startPt[0], pt[0]), Math.min(drag.startPt[1], pt[1]), Math.max(drag.startPt[0], pt[0]), Math.max(drag.startPt[1], pt[1])];
        renderSvg();
        return;
      }
      if (drag.kind === "move") {
        if (Math.hypot(dx, dy) > 2) drag.moved = true;
        drag.previewRect = G ? G.move(drag.rect0, dx, dy, canvasSize, snap) : drag.rect0.slice();
        renderSvg();
        return;
      }
      if (drag.kind === "resize") {
        drag.moved = true;
        drag.previewRect = G ? G.resize(drag.rect0, drag.handle, dx, dy, canvasSize, snap, e.shiftKey) : drag.rect0.slice();
        renderSvg();
        return;
      }
      if (drag.kind === "rotate") {
        drag.moved = true;
        const a = Math.atan2(pt[1] - drag.cy, pt[0] - drag.cx);
        let deg = drag.rot0 + (a - drag.a0) * 180 / Math.PI;
        deg = e.shiftKey ? Math.round(deg / 15) * 15 : Math.round(deg);
        drag.previewRot = ((deg + 180) % 360 + 360) % 360 - 180;
        renderSvg();
      }
    });

    const endDrag = async (e) => {
      if (!drag || drag.pid !== e.pointerId) return;
      const d = drag;
      drag = null;
      updateCursorClasses();
      if (d.kind === "brush" && Store.state.mask) {
        Store.state.mask.ops.push(Store.state.tool === "eraser" ? "지우개" : "브러시");
        Store.emit("mask");
        return;
      }
      if (d.kind === "newRect" && draftRect) {
        const rect = draftRect;
        draftRect = null;
        if (rect[2] - rect[0] >= 32 && rect[3] - rect[1] >= 32) {
          try {
            await Store.addCrop(rect);
            Store.state.tool = "select";
            Store.emit("tool");
            Store.toast("새 수정 영역을 추가했습니다.", "ok");
          } catch (err) {
            Store.toast(`영역 추가 실패: ${err.message}`, "err");
          }
        }
        renderSvg();
        return;
      }
      if ((d.kind === "move" || d.kind === "resize") && d.moved && d.previewRect) {
        const changed = d.previewRect.some((v, i) => v !== d.rect0[i]);
        if (changed) {
          try {
            await Store.updateCrop(d.id, { rect: d.previewRect }, d.kind === "move" ? "영역 이동" : "영역 크기 조절");
          } catch (err) {
            Store.toast(`영역 저장 실패: ${err.message}`, "err");
          }
        }
        renderSvg();
        return;
      }
      if (d.kind === "rotate" && d.moved) {
        const p = Store.getPatch(d.id);
        if (p) {
          p.meta.rotDeg = d.previewRot;
          Store.toast(`회전 각도 ${d.previewRot}° 적용`, "ok");
          // 후보정 마스크 세션이 열려 있고 ★ 후보가 같으면 서버 변형에도 반영
          if (Store.state.mask && p.pickedCand && Store.state.mask.key === p.pickedCand.key) {
            const seen = Store.state.mask.info && Store.state.mask.info.adjust ? Store.state.mask.info.adjust.at : "";
            Api.adjustMask(Store.state.root, { key: p.pickedCand.key, op: "set", seen, rot: d.previewRot }).catch(() => {});
          }
          Store.emit("selection");
        }
      }
    };
    vp.addEventListener("pointerup", endDrag);
    vp.addEventListener("pointercancel", endDrag);
    vp.addEventListener("pointerleave", () => { cursorPt = null; if (Store.state.tool === "brush" || Store.state.tool === "eraser") renderSvg(); });

    // 캔버스 전/후 분할 드래그 바
    let splitDrag = null;
    splitBar.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      splitDrag = { pid: e.pointerId };
      splitBar.setPointerCapture(e.pointerId);
    });
    splitBar.addEventListener("pointermove", (e) => {
      if (!splitDrag || splitDrag.pid !== e.pointerId) return;
      const pt = screenToCanvas(e.clientX, e.clientY);
      Store.state.viewFlags.splitRatio = Math.max(0.02, Math.min(0.98, pt[0] / Math.max(1, canvasSize[0])));
      updateSplitPosition();
    });
    splitBar.addEventListener("pointerup", () => { splitDrag = null; });
  }

  function wireToolRailAndFooter() {
    document.querySelectorAll(".tool-rail .tool-btn[data-tool]").forEach((btn) => {
      btn.addEventListener("click", () => setTool(btn.dataset.tool));
    });
    $("btn-toggle-autocrop").addEventListener("click", () => {
      Store.savePrefs({ showAutoCrop: !Store.state.prefs.showAutoCrop });
      $("btn-toggle-autocrop").classList.toggle("active", !!Store.state.prefs.showAutoCrop);
    });
    $("btn-fit-crop").addEventListener("click", () => fitCrop());
    $("btn-zoom-out").addEventListener("click", () => zoomTo(cam.z / 1.25));
    $("btn-zoom-in").addEventListener("click", () => zoomTo(cam.z * 1.25));
    $("btn-zoom-fit").addEventListener("click", () => fitAll(true));
    $("btn-zoom-100").addEventListener("click", () => zoomTo(1));
    $("input-focus-dim").addEventListener("input", (e) => {
      Store.savePrefs({ focusDimAlpha: Number(e.target.value) });
    });

    // 브러시 HUD
    $("hud-brush-size").addEventListener("input", (e) => {
      if (Store.state.mask) Store.state.mask.brushSize = Number(e.target.value);
      $("hud-brush-size-val").textContent = `${e.target.value}px`;
    });
    $("hud-brush-hard").addEventListener("input", (e) => {
      if (Store.state.mask) Store.state.mask.brushHard = Number(e.target.value) / 100;
      $("hud-brush-hard-val").textContent = `${e.target.value}%`;
    });
    $("hud-brush-flow").addEventListener("input", (e) => {
      if (Store.state.mask) Store.state.mask.brushFlow = Number(e.target.value) / 100;
      $("hud-brush-flow-val").textContent = `${e.target.value}%`;
    });
    $("btn-hud-apply-mask").addEventListener("click", () => Store.saveMaskSession());

    // 하단 플로팅 컨텍스트 바 버튼
    $("btn-fb-ratio-fit").addEventListener("click", async () => {
      const p = Store.primaryPatch();
      if (!p || !p.modelInfo || !window.DPLayoutGeom) return;
      const fitted = window.DPLayoutGeom.ratioFitRect(p.rect, canvasSize, p.modelInfo.ratios, 0.01, Data.RATIO_SIZES);
      if (!fitted) {
        Store.toast("이 영역을 모델 허용 비율로 넓힐 수 없습니다 — 직접 크기를 조절해 주세요.", "warn");
        return;
      }
      await Store.updateCrop(p.id, { rect: fitted.rect }, `비율 맞춤 (${fitted.ratio})`);
      Store.toast(`${p.modelInfo.label} 비율(${fitted.ratio})에 맞게 영역을 조정했습니다.`, "ok");
    });

    $("btn-fb-compare").addEventListener("click", () => {
      if (window.DPV2Overlays) window.DPV2Overlays.openComparer();
    });

    $("btn-fb-lock").addEventListener("click", async () => {
      const p = Store.primaryPatch();
      if (!p) return;
      await Store.updateCrop(p.id, { locked: !p.locked }, p.locked ? "잠금 해제" : "잠금");
    });

    $("btn-fb-delete").addEventListener("click", async () => {
      const p = Store.primaryPatch();
      if (!p) return;
      if (!(await Data.askDialogConfirm(`패치 "${p.name}" 영역을 삭제할까요?`, { ok: "삭제", danger: true }))) return;
      await Store.deleteCrop(p.id);
    });
  }

  function setTool(tool) {
    Store.state.tool = tool;
    document.querySelectorAll(".tool-rail .tool-btn[data-tool]").forEach((b) => {
      b.classList.toggle("active", b.dataset.tool === tool);
    });
    if ((tool === "brush" || tool === "eraser") && !Store.state.mask) {
      const p = Store.primaryPatch();
      if (p && p.pickedCand) Store.openMaskSession(p.pickedCand.key);
    }
    Store.emit("tool");
  }

  function setSpaceHeld(held) {
    if (spaceHeld === held) return;
    spaceHeld = held;
    updateCursorClasses();
  }

  function getCanvasSize() { return canvasSize.slice(); }

  return { init, render, fitAll, fitCrop, zoomTo, setTool, setSpaceHeld, getCanvasSize, resolveCandidateOverlay };
})();
