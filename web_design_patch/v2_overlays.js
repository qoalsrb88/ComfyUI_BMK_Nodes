// BMK Design Patch Studio v2 — 50/50 시트 오리기 & 4점 원근 · 풀스크린 비교 · 히스토리 · 설정/PSD/단축키/프로젝트 홈.
"use strict";

window.DPV2Overlays = (() => {
  const Data = window.DPV2Data;
  const Api = window.DPV2Api;
  const Store = window.DPV2Store;
  const Canvas = window.DPV2Canvas;

  const $ = (id) => document.getElementById(id);

  // ── 공용 모달 헬퍼 ──
  let modalOnClose = null;
  let modalCanClose = null;
  let modalPrevFocus = null;
  let modalBgLocked = false;
  let inertReconcileObs = null;
  let inertReconcileTimer = null;
  let inertReconcileFocus = null;

  function isCommonModalOpen() {
    const bd = $("modal-backdrop");
    return !!(bd && !bd.classList.contains("hidden"));
  }

  function hasExternalInertLock() {
    if (window.DPDialog && window.DPDialog.open) return true;
    const kids = (document.body && document.body.children) || [];
    for (let i = 0; i < kids.length; i++) {
      const ch = kids[i];
      if (ch && ch.classList && ch.classList.contains("overlay") && !ch.classList.contains("hidden")) {
        return true;
      }
    }
    return false;
  }

  function stopInertReconcile() {
    if (inertReconcileObs) {
      try { inertReconcileObs.disconnect(); } catch (_) {}
      inertReconcileObs = null;
    }
    if (inertReconcileTimer) {
      clearInterval(inertReconcileTimer);
      inertReconcileTimer = null;
    }
    inertReconcileFocus = null;
  }

  function restoreModalFocus(prev) {
    if (canFocusSafely() && prev && prev.isConnected !== false && typeof prev.focus === "function") {
      try { prev.focus(); } catch (_) {}
    }
  }

  function clearBackgroundViewsInert() {
    for (const bgId of ["projects-view", "studio-view"]) {
      const bg = $(bgId);
      if (bg && bg.inert) {
        try { bg.inert = false; } catch (_) {}
      }
    }
  }

  // #C46-1: 모달과 외부 잠금(DPDialog · 세션 차단막 등)이 엇갈려 닫힐 때
  // ① 모달이 먼저 닫히고 외부 잠금이 나중에 닫히면 inert=false 로 복구하고,
  // ② 외부 잠금이 먼저 닫히고 모달이 아직 열려 있으면 inert=true 를 다시 적용한다
  function reconcileBackgroundInertAfterExternalUnlock() {
    if (hasExternalInertLock()) return;
    if (isCommonModalOpen()) {
      stopInertReconcile();
      for (const bgId of ["projects-view", "studio-view"]) {
        const bg = $(bgId);
        if (bg) bg.inert = true;
      }
      modalBgLocked = true;
      return;
    }
    const prev = inertReconcileFocus;
    stopInertReconcile();
    clearBackgroundViewsInert();
    restoreModalFocus(prev);
  }

  function scheduleInertReconcile(prevFocus) {
    stopInertReconcile();
    inertReconcileFocus = prevFocus || null;
    if (typeof MutationObserver === "function" && document.body) {
      try {
        inertReconcileObs = new MutationObserver(() => {
          reconcileBackgroundInertAfterExternalUnlock();
        });
        inertReconcileObs.observe(document.body, { childList: true });
      } catch (_) {
        inertReconcileObs = null;
      }
    }
    inertReconcileTimer = setInterval(() => {
      reconcileBackgroundInertAfterExternalUnlock();
    }, 80);
    if (inertReconcileTimer && typeof inertReconcileTimer.unref === "function") {
      inertReconcileTimer.unref();
    }
  }

  function lockModalBackground() {
    stopInertReconcile();
    if (!modalBgLocked) {
      modalPrevFocus = document.activeElement || null;
      modalBgLocked = true;
    }
    for (const bgId of ["projects-view", "studio-view"]) {
      const bg = $(bgId);
      if (bg) bg.inert = true;
    }
    // #C46-1 보강: 외부 잠금이 떠 있는 상태에서 모달이 열린 경우, 외부 잠금이 먼저 닫히면서
    // inert=false 를 복원해도 모달 뒤 화면이 조작 가능해지지 않도록 즉시 재잠금 감시를 건다
    if (hasExternalInertLock()) {
      scheduleInertReconcile(null);
    }
  }

  function canFocusSafely() {
    return !navigator.userActivation || navigator.userActivation.isActive;
  }

  function unlockModalBackground() {
    const prev = modalPrevFocus;
    modalPrevFocus = null;
    modalBgLocked = false;
    // #C46-1: 외부 잠금(DPDialog · 세션 차단막)이 아직 떠 있으면 지금 inert 를 풀지 않고,
    // 외부 잠금이 걷힌 직후 inert=false 로 재정합하도록 감시한다.
    // 외부 잠금이 이미 걷혔으면 모달이 열릴 때의 이전 값(true)과 무관하게 즉시 false 로 풀어 고착을 막는다.
    if (hasExternalInertLock()) {
      scheduleInertReconcile(prev);
      return;
    }
    stopInertReconcile();
    clearBackgroundViewsInert();
    restoreModalFocus(prev);
  }

  function focusInsideModal() {
    // Forge iframe 의 focus-without-user-activation 정책 위반 콘솔 오류를 막기 위해 사용자 활성화 시에만 포커스를 이동한다
    if (!canFocusSafely()) return;
    const box = $("modal-box");
    if (!box) return;
    const first = box.querySelector("#modal-body input:not([disabled]):not(.hidden), #modal-body textarea:not([disabled]):not(.hidden), #modal-body select:not([disabled]):not(.hidden), #modal-body button:not([disabled]):not(.hidden), #modal-foot button:not([disabled]):not(.hidden), #modal-close:not([disabled]):not(.hidden)");
    if (first && typeof first.focus === "function") {
      try { first.focus(); return; } catch (_) {}
    }
    if (!box.hasAttribute || !box.hasAttribute("tabindex")) box.setAttribute("tabindex", "-1");
    if (typeof box.focus === "function") {
      try { box.focus(); } catch (_) {}
    }
  }

  function openModal({ title, bodyNodes = [], actions = [], onClose = null, canClose = null }) {
    const bd = $("modal-backdrop");
    if (!bd) return false;
    const wasOpen = !bd.classList.contains("hidden");
    // #C40-1: 현재 열린 모달이 닫기 잠금(canClose() === false) 상태면 다른 모달로 교체하거나 onClose() 를 부르지 않는다
    if (wasOpen && typeof modalCanClose === "function" && !modalCanClose()) {
      Store.toast("현재 작업이 완료될 때까지 다른 창을 열 수 없습니다.");
      return false;
    }
    if (typeof modalOnClose === "function") {
      const prevClose = modalOnClose;
      modalOnClose = null;
      try { prevClose(); } catch (_) {}
    }
    modalOnClose = typeof onClose === "function" ? onClose : null;
    modalCanClose = typeof canClose === "function" ? canClose : null;
    const mClose = $("modal-close");
    if (mClose) mClose.disabled = false;
    $("modal-title").textContent = title || "";
    const body = $("modal-body");
    body.innerHTML = "";
    for (const n of bodyNodes) if (n) body.append(n);
    const foot = $("modal-foot");
    foot.innerHTML = "";
    for (const a of actions) {
      const attrs = {
        type: "button",
        className: a.cls || "btn-secondary",
        text: a.label,
        disabled: !!a.disabled,
        title: a.title || "",
        onClick: a.onClick,
      };
      if (a.id) attrs.id = a.id;
      foot.append(Data.el("button", attrs));
    }
    const box = $("modal-box");
    if (box) {
      box.setAttribute("role", "dialog");
      box.setAttribute("aria-modal", "true");
    }
    lockModalBackground();
    bd.classList.remove("hidden");
    focusInsideModal();
    return true;
  }

  function closeModal(force = false) {
    // #C36-2: 이벤트 객체(PointerEvent 등)가 첫 인자로 들어와도 force 로 취급하지 않는다
    const isForce = force === true;
    if (!isForce && typeof modalCanClose === "function" && !modalCanClose()) return;
    const bd = $("modal-backdrop");
    if (bd) bd.classList.add("hidden");
    const box = $("modal-box");
    if (box && typeof box.removeAttribute === "function") {
      box.removeAttribute("aria-modal");
    }
    const mClose = $("modal-close");
    if (mClose) mClose.disabled = false;
    modalCanClose = null;
    if (typeof modalOnClose === "function") {
      const fn = modalOnClose;
      modalOnClose = null;
      try { fn(); } catch (_) {}
    }
    // #C36-7: 모달을 닫은 뒤 DOM 노드와 핸들러 클로저(선택한 File 참조 포함)를 즉시 해제한다
    const body = $("modal-body");
    if (body) body.innerHTML = "";
    const foot = $("modal-foot");
    if (foot) foot.innerHTML = "";
    unlockModalBackground();
  }

  function loadImage(src) {
    return new Promise((resolve, reject) => {
      const img = new Image();
      img.onload = () => resolve(img);
      img.onerror = (err) => reject(err);
      img.src = src;
    });
  }

  // ==================== 1. 50/50 분할 레퍼런스 시트 오리기 & 4점 원근 ====================
  const SHEET_HANDLE_CURSORS = {
    n: "ns-resize", s: "ns-resize", e: "ew-resize", w: "ew-resize",
    nw: "nwse-resize", se: "nwse-resize", ne: "nesw-resize", sw: "nesw-resize",
  };

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
    hoverHit: null,
    warpHover: null,
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

  function sheetHandlesPx(w, h) {
    const r = sheetState.cutRect;
    const x1 = r.x * w, y1 = r.y * h, x2 = (r.x + r.w) * w, y2 = (r.y + r.h) * h;
    const mx = (x1 + x2) / 2, my = (y1 + y2) / 2;
    return [
      { id: "nw", x: x1, y: y1, corner: true },
      { id: "n", x: mx, y: y1, corner: false },
      { id: "ne", x: x2, y: y1, corner: true },
      { id: "e", x: x2, y: my, corner: false },
      { id: "se", x: x2, y: y2, corner: true },
      { id: "s", x: mx, y: y2, corner: false },
      { id: "sw", x: x1, y: y2, corner: true },
      { id: "w", x: x1, y: my, corner: false },
    ];
  }

  function sheetHitTest(px, py) {
    const img = sheetState.sheetImg;
    if (!img) return null;
    const w = img.naturalWidth, h = img.naturalHeight;
    const inv = 1 / Math.max(0.15, sheetState.zoom);
    const tol = 11 * inv;
    for (const hp of sheetHandlesPx(w, h)) {
      if (Math.abs(px - hp.x) <= tol && Math.abs(py - hp.y) <= tol) {
        return { kind: "handle", handle: hp.id };
      }
    }
    const r = sheetState.cutRect;
    const x1 = r.x * w, y1 = r.y * h, x2 = (r.x + r.w) * w, y2 = (r.y + r.h) * h;
    if (px >= x1 && px <= x2 && py >= y1 && py <= y2) {
      return { kind: "body" };
    }
    return null;
  }

  function pointInQuad(pt, q) {
    let inside = false;
    for (let i = 0, j = q.length - 1; i < q.length; j = i++) {
      const xi = q[i].x, yi = q[i].y, xj = q[j].x, yj = q[j].y;
      const intersect = ((yi > pt.y) !== (yj > pt.y))
        && (pt.x < ((xj - xi) * (pt.y - yi)) / ((yj - yi) || 1e-9) + xi);
      if (intersect) inside = !inside;
    }
    return inside;
  }

  function distToSegment(pt, a, b) {
    const vx = b.x - a.x, vy = b.y - a.y;
    const len2 = vx * vx + vy * vy || 1e-9;
    const t = Math.max(0, Math.min(1, ((pt.x - a.x) * vx + (pt.y - a.y) * vy) / len2));
    return Math.hypot(pt.x - (a.x + t * vx), pt.y - (a.y + t * vy));
  }

  function warpHitTest(x, y) {
    const q = sheetState.quad;
    let nearest = -1, bestD = 0.055;
    q.forEach((pt, idx) => {
      const d = Math.hypot(pt.x - x, pt.y - y);
      if (d < bestD) { bestD = d; nearest = idx; }
    });
    if (nearest >= 0) return { kind: "corner", idx: nearest };
    let bestEdge = -1, bestEd = 0.032;
    for (let i = 0; i < 4; i++) {
      const d = distToSegment({ x, y }, q[i], q[(i + 1) % 4]);
      if (d < bestEd) { bestEd = d; bestEdge = i; }
    }
    if (bestEdge >= 0) return { kind: "edge", idx: bestEdge };
    if (pointInQuad({ x, y }, q)) return { kind: "body", idx: -1 };
    return null;
  }

  function isQuadConvex(q) {
    for (let k = 0; k < 4; k++) {
      const a = q[k], b = q[(k + 1) % 4], c = q[(k + 2) % 4];
      if ((b.x - a.x) * (c.y - b.y) - (b.y - a.y) * (c.x - b.x) <= 0) return false;
    }
    return true;
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
    const inv = 1 / Math.max(0.2, sheetState.zoom);
    const accent = Store.activeAccentHex();
    const isLight = Store.activeTheme().mode === "light";
    const onAccent = isLight ? "#ffffff" : "#09090b";

    // 바깥 어둡게 딤
    ctx.fillStyle = "rgba(0, 0, 0, 0.54)";
    ctx.fillRect(0, 0, cv.width, ry);
    ctx.fillRect(0, ry + rh, cv.width, cv.height - (ry + rh));
    ctx.fillRect(0, ry, rx, rh);
    ctx.fillRect(rx + rw, ry, cv.width - (rx + rw), rh);

    // 3분할 구도 가이드선 (드래그 중 선명, 평상시 은은하게)
    const isEditing = !!(sheetState.drag && sheetState.drag.kind !== "pan");
    ctx.save();
    ctx.strokeStyle = isEditing ? "rgba(255, 255, 255, 0.34)" : "rgba(255, 255, 255, 0.14)";
    ctx.lineWidth = Math.max(0.8, 1 * inv);
    ctx.setLineDash([4 * inv, 4 * inv]);
    ctx.beginPath();
    ctx.moveTo(rx + rw / 3, ry); ctx.lineTo(rx + rw / 3, ry + rh);
    ctx.moveTo(rx + (rw * 2) / 3, ry); ctx.lineTo(rx + (rw * 2) / 3, ry + rh);
    ctx.moveTo(rx, ry + rh / 3); ctx.lineTo(rx + rw, ry + rh / 3);
    ctx.moveTo(rx, ry + (rh * 2) / 3); ctx.lineTo(rx + rw, ry + (rh * 2) / 3);
    ctx.stroke();
    ctx.restore();

    // 주 선택 테두리
    ctx.strokeStyle = accent;
    ctx.lineWidth = Math.max(1.5, 2 * inv);
    ctx.strokeRect(rx, ry, rw, rh);

    // 8방향 리사이즈 핸들 (모서리 4개 + 변 중점 4개 · 줌 보정 크기)
    const activeHandle = (sheetState.drag && sheetState.drag.kind === "resize")
      ? sheetState.drag.handle
      : (sheetState.hoverHit && sheetState.hoverHit.kind === "handle" ? sheetState.hoverHit.handle : null);
    for (const hp of sheetHandlesPx(cv.width, cv.height)) {
      const isHot = activeHandle === hp.id;
      const baseSz = (hp.corner ? 9.5 : 8) * inv * (isHot ? 1.24 : 1);
      ctx.save();
      ctx.fillStyle = isHot ? onAccent : accent;
      ctx.strokeStyle = isHot ? accent : onAccent;
      ctx.lineWidth = Math.max(1.2, 1.8 * inv);
      if (hp.corner) {
        ctx.fillRect(hp.x - baseSz / 2, hp.y - baseSz / 2, baseSz, baseSz);
        ctx.strokeRect(hp.x - baseSz / 2, hp.y - baseSz / 2, baseSz, baseSz);
      } else {
        const horiz = hp.id === "n" || hp.id === "s";
        const hw = (horiz ? 14 : 6.5) * inv * (isHot ? 1.15 : 1);
        const hh = (horiz ? 6.5 : 14) * inv * (isHot ? 1.15 : 1);
        ctx.beginPath();
        if (ctx.roundRect) ctx.roundRect(hp.x - hw / 2, hp.y - hh / 2, hw, hh, 3 * inv);
        else ctx.rect(hp.x - hw / 2, hp.y - hh / 2, hw, hh);
        ctx.fill();
        ctx.stroke();
      }
      ctx.restore();
    }

    // 실시간 픽셀 치수 라벨 (#C07-6: 긴 변 2048 초과 큰 시트는 서버 원본 size 기준으로 표시)
    const curSheet = (Store.state.sheets || []).find((s) => s.path === sheetState.sheetPath);
    const origW = (curSheet && Array.isArray(curSheet.size) && curSheet.size[0]) || cv.width;
    const origH = (curSheet && Array.isArray(curSheet.size) && curSheet.size[1]) || cv.height;
    const pxW = Math.round(r.w * origW), pxH = Math.round(r.h * origH);
    const pxX = Math.round(r.x * origW), pxY = Math.round(r.y * origH);
    const label = `${pxW} × ${pxH} px`;
    ctx.save();
    const fsz = Math.max(10, Math.round(11.5 * inv));
    ctx.font = `600 ${fsz}px ui-monospace, SFMono-Regular, Menlo, Consolas, sans-serif`;
    const tw = ctx.measureText(label).width;
    const padX = 7 * inv, boxH = 20 * inv, boxW = tw + padX * 2;
    const bx = Math.max(2 * inv, Math.min(cv.width - boxW - 2 * inv, rx));
    const by = ry - boxH - 6 * inv >= 2 * inv ? ry - boxH - 6 * inv : ry + 6 * inv;
    ctx.fillStyle = "rgba(9, 9, 11, 0.88)";
    ctx.strokeStyle = accent;
    ctx.lineWidth = Math.max(1, 1.2 * inv);
    ctx.beginPath();
    if (ctx.roundRect) ctx.roundRect(bx, by, boxW, boxH, 4 * inv);
    else ctx.rect(bx, by, boxW, boxH);
    ctx.fill();
    ctx.stroke();
    ctx.fillStyle = "#fafafa";
    ctx.textAlign = "left";
    ctx.textBaseline = "middle";
    ctx.fillText(label, bx + padX, by + boxH / 2 + 0.5 * inv);
    ctx.restore();

    const szEl = $("sheet-cut-size");
    if (szEl) {
      szEl.textContent = `오리기 영역: ${pxW} × ${pxH} px (${pxX}, ${pxY})`;
    }
  }

  function nudgeSheetCut(dxPx, dyPx) {
    const img = sheetState.sheetImg;
    if (!sheetState.open || !img) return false;
    const w = img.naturalWidth || 1, h = img.naturalHeight || 1;
    const r = sheetState.cutRect;
    r.x = Math.max(0, Math.min(1 - r.w, r.x + dxPx / w));
    r.y = Math.max(0, Math.min(1 - r.h, r.y + dyPx / h));
    drawSheetSource();
    drawSheetWarp();
    return true;
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

    // 3) 4점 와이어프레임 · 변 중점 핸들 · 드래그 꼭짓점 핸들 (1~4)
    const accent = Store.activeAccentHex();
    const isLight = Store.activeTheme().mode === "light";
    const onAccent = isLight ? "#ffffff" : "#09090b";
    const convex = isQuadConvex(sheetState.quad);
    const wireCol = convex ? accent : "#ef4444";

    ctx.save();
    ctx.strokeStyle = wireCol;
    ctx.lineWidth = 2;
    if (!convex) ctx.setLineDash([6, 4]);
    ctx.beginPath();
    ctx.moveTo(q[0].x, q[0].y);
    ctx.lineTo(q[1].x, q[1].y);
    ctx.lineTo(q[2].x, q[2].y);
    ctx.lineTo(q[3].x, q[3].y);
    ctx.closePath();
    ctx.stroke();
    ctx.restore();

    // 호버/활성 변 강조 및 4변 중점 미니 핸들
    const activeEdge = (sheetState.drag && sheetState.drag.kind === "quad-edge")
      ? sheetState.drag.idx
      : (sheetState.warpHover && sheetState.warpHover.kind === "edge" ? sheetState.warpHover.idx : -1);
    for (let i = 0; i < 4; i++) {
      const a = q[i], b = q[(i + 1) % 4];
      const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
      const hot = activeEdge === i;
      if (hot) {
        ctx.save();
        ctx.strokeStyle = "#ffffff";
        ctx.lineWidth = 3;
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
        ctx.stroke();
        ctx.restore();
      }
      ctx.fillStyle = hot ? "#ffffff" : "rgba(18, 18, 22, 0.85)";
      ctx.strokeStyle = wireCol;
      ctx.lineWidth = 1.6;
      ctx.beginPath();
      ctx.arc(mx, my, hot ? 5.5 : 4, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();
    }

    const activeCorner = (sheetState.drag && sheetState.drag.kind === "quad-corner")
      ? sheetState.drag.idx
      : (sheetState.warpHover && sheetState.warpHover.kind === "corner" ? sheetState.warpHover.idx : -1);
    q.forEach((pt, idx) => {
      const hot = activeCorner === idx;
      const rad = hot ? 10 : 8.5;
      if (hot) {
        ctx.save();
        ctx.strokeStyle = wireCol;
        ctx.globalAlpha = 0.4;
        ctx.lineWidth = 4;
        ctx.beginPath();
        ctx.arc(pt.x, pt.y, rad + 3, 0, Math.PI * 2);
        ctx.stroke();
        ctx.restore();
      }
      ctx.fillStyle = wireCol;
      ctx.strokeStyle = onAccent;
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.arc(pt.x, pt.y, rad, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();
      ctx.fillStyle = onAccent;
      ctx.font = "700 10px sans-serif";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(String(idx + 1), pt.x, pt.y + 0.5);
    });
  }

  let sheetLoadSeq = 0;

  async function loadSelectedSheet(sheetPath) {
    const sheets = Store.state.sheets || [];
    const sh = sheets.find((s) => s.path === sheetPath) || sheets[0] || null;
    if (!sh) {
      sheetLoadSeq += 1;
      sheetState.loading = false;
      sheetState.sheetPath = "";
      sheetState.sheetImg = null;
      drawSheetSource();
      drawSheetWarp();
      return;
    }
    const seq = ++sheetLoadSeq;
    sheetState.loading = true;
    sheetState.sheetPath = sh.path;
    sheetState.sheetImg = null;
    const sel = $("sheet-select");
    if (sel) sel.value = sh.path;
    try {
      const url = Api.sheetUrl(Store.state.root, sh, "preview");
      const loaded = await loadImage(url);
      if (seq !== sheetLoadSeq) return;
      sheetState.sheetImg = loaded;
      sheetState.loading = false;
      fitSheetToView();
      drawSheetSource();
      drawSheetWarp();
    } catch (err) {
      if (seq === sheetLoadSeq) sheetState.loading = false;
      Store.toast(`시트 이미지를 불러오지 못했습니다: ${err.message}`, "err");
    }
  }

  async function openSheetOverlay(preferredPath = "") {
    const ov = $("sheet-overlay");
    if (!ov) return;
    // #C13-9: 보관함 카드로 열 때 목록 재조회 중 앞 시트 이미지로 저장되지 않도록 여는 즉시 잠그고 비운다
    const openSeq = ++sheetLoadSeq;
    sheetState.open = true;
    sheetState.loading = true;
    sheetState.sheetImg = null;
    if (preferredPath) sheetState.sheetPath = preferredPath;
    sheetState.maximized = false;
    $("sheet-split-body").classList.remove("maximized");
    $("btn-sheet-max").textContent = "⤢ 최대화";
    const bc = $("sheet-breadcrumb-proj");
    if (bc) bc.textContent = (Store.state.S && (Store.state.S.title || Store.state.S.project)) || Store.state.root || "프로젝트";
    drawSheetSource();
    drawSheetWarp();
    ov.classList.remove("hidden");

    await Store.loadRefs(true);
    if (!sheetState.open || openSeq !== sheetLoadSeq) return;

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
    sheetLoadSeq += 1;
    sheetState.open = false;
    sheetState.loading = false;
    sheetState.drag = null;
    sheetState.hoverHit = null;
    sheetState.warpHover = null;
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
    // #C11-6: 시트 교체로 새 미리보기가 뜨기 전에는 앞 시트 사각형이 새 시트 크기로 저장되지 않게 막는다
    if (sheetState.loading) {
      Store.toast("시트 이미지를 불러오는 중입니다 — 잠시 후 다시 저장해 주세요.");
      return;
    }
    const img = sheetState.sheetImg;
    if (!img || !sheetState.sheetPath) {
      Store.toast("먼저 시트 이미지를 업로드해 주세요.");
      return;
    }
    const curSheet = (Store.state.sheets || []).find((s) => s.path === sheetState.sheetPath);
    // #C07-6: /refs/cut 은 서버 원본 픽셀 좌표를 요구한다(미리보기 2048px 축소본과 큰 시트 원본 크기 분리)
    const origW = (curSheet && Array.isArray(curSheet.size) && curSheet.size[0]) || img.naturalWidth;
    const origH = (curSheet && Array.isArray(curSheet.size) && curSheet.size[1]) || img.naturalHeight;
    const r = sheetState.cutRect;
    const rectPx = [
      Math.round(r.x * origW),
      Math.round(r.y * origH),
      Math.round((r.x + r.w) * origW),
      Math.round((r.y + r.h) * origH),
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

    if (sheetState.saving) return;
    sheetState.saving = true;
    const btnAttach = $("btn-sheet-attach");
    const btnVault = $("btn-sheet-save-vault");
    if (btnAttach) btnAttach.disabled = true;
    if (btnVault) btnVault.disabled = true;

    let saveAttempted = false;
    try {
      const cutBody = {
        sheet: sheetState.sheetPath,
        rect: rectPx,
        method: "rect",
      };
      if (curSheet && curSheet.v) cutBody.sheet_v = curSheet.v;

      const sendSave = async (body) => {
        saveAttempted = true;
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

      // #C33-2: /refs/cut 출발 전부터 /refs/save 완료까지 하나의 레퍼런스 쓰기 작업으로 추적한다
      const runCutAndSave = async () => {
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
        return sendSave(saveBody);
      };

      const saved = await (typeof Store.trackRefWrite === "function"
        ? Store.trackRefWrite(runCutAndSave())
        : runCutAndSave());
      if (saved && saved.cancelled) return;
      Store.invalidateAllSpecs();
      const refSync = typeof Store.refreshAfterRefWrite === "function"
        ? Store.refreshAfterRefWrite()
        : Store.refreshAll(true);
      closeSheetOverlay();
      Store.toast(cropId ? "시트 컷을 저장하고 선택한 영역에 연결했습니다." : "시트 컷을 레퍼런스 보관함에 저장했습니다.", "ok");
      await refSync;
    } catch (err) {
      if (saveAttempted) {
        Store.invalidateAllSpecs();
        if (typeof Store.refreshAfterRefWrite === "function") Store.refreshAfterRefWrite();
        else Store.refreshAll(true);
      }
      Store.toast(`시트 컷 저장 실패: ${err.message}`, "err");
    } finally {
      sheetState.saving = false;
      if (btnAttach) btnAttach.disabled = false;
      if (btnVault) btnVault.disabled = false;
    }
  }

  // ==================== 2. 풀스크린 정밀 비교 (46px 확장 디바이더) ====================
  const cmpState = {
    open: false,
    candKey: "",
    loadedCandKey: "",
    split: 0.5,
    blinkOrig: false,
    zoom: 1,
    dragging: false,
    loading: false,
  };
  let cmpRenderSeq = 0;

  function syncComparePickBtn() {
    const coPick = $("co-pick");
    if (!coPick) return;
    const p = Store.primaryPatch();
    const busy = !!(p && p.target && cmpState.candKey && Store.isPickBusy(p.id, p.target.tid, cmpState.candKey));
    // #C43-10: 현재 후보 이미지가 실제로 로드 완료된 경우에만 채택 버튼을 연다
    const imageReady = !!(cmpState.candKey && cmpState.loadedCandKey === cmpState.candKey);
    coPick.disabled = !!(cmpState.loading || busy || !imageReady);
    coPick.title = cmpState.loading
      ? "후보 이미지를 불러오는 중입니다…"
      : (!imageReady ? "후보 이미지를 불러온 뒤 채택할 수 있습니다." : "현재 보고 있는 후보를 확정 적용합니다");
  }

  async function openCompareOverlay(candKey = "") {
    const patch = Store.primaryPatch();
    if (!patch || !patch.candidates.length) {
      Store.toast("비교할 생성 후보가 아직 없습니다.");
      return;
    }
    const cur = Store.currentCandidate(patch);
    cmpState.open = true;
    cmpState.candKey = candKey || (cur && cur.key) || patch.candidates[0].key;
    cmpState.loadedCandKey = "";
    cmpState.split = 0.5;
    cmpState.blinkOrig = false;
    cmpState.zoom = 1;
    $("compare-overlay").classList.remove("hidden");
    await renderCompareOverlay();
  }

  function closeCompareOverlay() {
    cmpRenderSeq += 1;
    cmpState.open = false;
    cmpState.loading = false;
    cmpState.loadedCandKey = "";
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
    const targetKey = cand.key;
    const seq = ++cmpRenderSeq;
    cmpState.loading = true;
    cmpState.loadedCandKey = "";
    syncComparePickBtn();

    const candTag = Data.formatCandidateModelTag(cand);
    $("co-title").textContent = `${patch.name} — 후보 #${cand.n || 1} 정밀 비교`;
    $("co-label-after").textContent = `후보 #${cand.n || 1} (${cand.variant || "V1"})`
      + (candTag ? ` · ${candTag}` : "")
      + (cand.old_cell ? " · 옛 조건" : "");
    $("co-zoom-reset").textContent = `${Math.round(cmpState.zoom * 100)}%`;

    // R2-5: 우측 후보 레일을 이미지 로드 전에 즉시 갱신해 선택 강조가 늦은 이미지 응답에 되돌려지지 않게 한다
    const rail = $("co-rail");
    if (rail) {
      rail.innerHTML = "";
      patch.candidates.forEach((c, idx) => {
        const picked = Array.isArray(patch.pickedKeys)
          ? patch.pickedKeys.includes(c.key)
          : !!(patch.pickedCand && patch.pickedCand.key === c.key);
        const thumb = Api.apiUrl(c.thumb_url || c.big_url || "");
        const cTag = Data.formatCandidateModelTag(c);
        const subChildren = [];
        if (cTag) subChildren.push(cTag);
        if (c.old_cell) subChildren.push(Data.el("span", { className: "co-rail-old", text: "옛 조건" }));
        rail.append(Data.el("button", {
          type: "button",
          className: "co-rail-item" + (c.key === cand.key ? " on" : ""),
          onClick: () => { cmpState.candKey = c.key; renderCompareOverlay(); },
        },
          thumb ? Data.el("img", { src: thumb, alt: "" }) : null,
          Data.el("span", {},
            `${idx + 1}. ${picked ? "★ " : ""}${c.variant || `후보 ${idx + 1}`}`,
            subChildren.length ? Data.el("small", { className: "co-rail-sub" }, ...subChildren) : null,
          ),
        ));
      });
    }

    const beforeRel = (patch.target && patch.target.before_url) || (Store.state.S && Store.state.S.canvas && Store.state.S.canvas.base_url);
    const afterRel = cand.big_url || cand.pick_url || cand.thumb_url;
    const boxSize = Math.min(720, window.innerHeight - 160, window.innerWidth - 220);

    try {
      const [bImg, aImg] = await Promise.all([
        loadImage(Api.apiUrl(beforeRel)),
        loadImage(Api.apiUrl(afterRel)),
      ]);
      // R2-5: 더 최신 후보 전환·닫기가 일어났으면 늦게 도착한 앞 후보 이미지를 캔버스에 그리지 않는다
      if (!cmpState.open || seq !== cmpRenderSeq || cmpState.candKey !== targetKey) return;
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
      cmpState.loadedCandKey = targetKey;
    } catch (_) {
      // #C43-10: 후보 이미지 로드 실패 시 직전 후보 캔버스를 비우고 오류를 표시하며 채택 버튼을 잠근다
      if (seq === cmpRenderSeq) {
        cmpState.loadedCandKey = "";
        const cAfter = $("co-canvas-after");
        if (cAfter && typeof cAfter.getContext === "function") {
          const ctx = cAfter.getContext("2d");
          if (ctx) ctx.clearRect(0, 0, cAfter.width || 0, cAfter.height || 0);
        }
        const lblAfter = $("co-label-after");
        if (lblAfter) {
          lblAfter.textContent = `후보 #${cand.n || 1} (${cand.variant || "V1"}) — 이미지를 불러오지 못했습니다`;
        }
        Store.toast("후보 이미지를 불러오지 못했습니다 — 이미지를 확인한 뒤 채택해 주세요.", "err");
      }
    } finally {
      if (seq === cmpRenderSeq) {
        cmpState.loading = false;
        syncComparePickBtn();
      }
    }
  }

  // ==================== 3. 히스토리 패널 (이동 · 리사이즈 · 좌/우 자석 스냅 Attach/Detach) ====================
  function getHistoryPrefs() {
    const cur = (Store.state.prefs && Store.state.prefs.historyPanel) || {};
    return {
      mode: ["floating", "dock-left", "dock-right"].includes(cur.mode) ? cur.mode : "floating",
      x: typeof cur.x === "number" ? cur.x : null,
      y: typeof cur.y === "number" ? cur.y : null,
      w: Math.max(240, Math.min(520, Number(cur.w) || 300)),
      h: Math.max(220, Math.min(Math.max(300, window.innerHeight - 80), Number(cur.h) || 420)),
    };
  }

  function clampFloatingBox(x, y, w, h) {
    const topMin = 50;
    const pad = 8;
    const maxW = Math.max(240, Math.min(520, window.innerWidth - pad * 2));
    const maxH = Math.max(220, window.innerHeight - topMin - pad);
    const cw = Math.max(240, Math.min(maxW, w));
    const ch = Math.max(220, Math.min(maxH, h));
    const cx = Math.max(pad, Math.min(window.innerWidth - cw - pad, x));
    const cy = Math.max(topMin, Math.min(window.innerHeight - ch - pad, y));
    return { x: Math.round(cx), y: Math.round(cy), w: Math.round(cw), h: Math.round(ch) };
  }

  function applyHistoryPanelLayout(override = null) {
    const dr = $("history-drawer");
    if (!dr) return;
    const hp = Object.assign(getHistoryPrefs(), override || {});
    dr.dataset.mode = hp.mode;
    dr.classList.remove("mode-floating", "mode-dock-left", "mode-dock-right");
    dr.classList.add(`mode-${hp.mode}`);

    if (hp.mode === "floating") {
      const rightDock = $("right-dock");
      const rDockW = rightDock && rightDock.offsetWidth ? rightDock.offsetWidth : 368;
      const defX = Math.max(16, window.innerWidth - rDockW - hp.w - 20);
      const defY = 58;
      const box = clampFloatingBox(hp.x ?? defX, hp.y ?? defY, hp.w, hp.h);
      dr.style.left = `${box.x}px`;
      dr.style.top = `${box.y}px`;
      dr.style.width = `${box.w}px`;
      dr.style.height = `${box.h}px`;
    } else {
      dr.style.left = "";
      dr.style.top = "";
      dr.style.width = "";
      dr.style.height = "";
    }

    const bL = $("btn-hist-dock-left");
    const bR = $("btn-hist-dock-right");
    const bF = $("btn-hist-float");
    if (bL) bL.classList.toggle("on", hp.mode === "dock-left");
    if (bR) bR.classList.toggle("on", hp.mode === "dock-right");
    if (bF) bF.classList.toggle("on", hp.mode === "floating");
  }

  function setHistoryPanelMode(mode, extra = {}) {
    // #C40-6: 바뀐 항목(mode · extra)만 최신 저장소의 historyPanel 에 합쳐 다른 탭의 크기(w/h) 등을 덮어쓰지 않는다
    const delta = Object.assign({}, extra, { mode });
    const next = typeof Store.saveHistoryPanelPrefs === "function"
      ? Store.saveHistoryPanelPrefs(delta)
      : (() => {
        const merged = Object.assign({}, getHistoryPrefs(), delta);
        Store.savePrefs({ historyPanel: merged });
        return merged;
      })();
    applyHistoryPanelLayout(next);
  }

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
    const willOpen = dr.classList.contains("hidden");
    dr.classList.toggle("hidden", !willOpen);
    const btn = $("btn-history-panel");
    if (btn) btn.classList.toggle("on", willOpen);
    if (willOpen) {
      applyHistoryPanelLayout();
      renderHistoryDrawer();
    }
  }

  // ==================== 4.5 프로젝트 기본 생성 설정 모달 (V2-B D564 · R2-2 · R2-7) ====================
  function openProjectDefaultsModal() {
    // #C43-2 · #C43-9 · #C46-1: 패치별 모델 설정 읽기 실패 상태 또는 설정 저장·합성 적용·생성/AI 작성 진행 중에는 프로젝트 기본 설정 모달을 열지 않는다
    if (typeof Store.isCropGenUnavailable === "function" && Store.isCropGenUnavailable()) {
      Store.toast("패치별 모델 설정을 읽지 못해 프로젝트 기본 설정을 열 수 없습니다 — 먼저 [다시 읽기]로 확인해 주세요.", "err");
      return;
    }
    if (Store.state.pipelineSettingsBusy || Store.state.cropGenBusy || Store.state.freeApplyBusy || Store.state.runFlowBusy || Store.state.assistBusy) {
      Store.toast(
        Store.state.freeApplyBusy
          ? "합성 적용이 진행 중입니다 — 끝난 뒤 프로젝트 기본 설정을 변경해 주세요."
          : (Store.state.runFlowBusy || Store.state.assistBusy)
            ? "생성 흐름이나 AI 프롬프트 작성이 진행 중입니다 — 끝난 뒤 다시 시도해 주세요."
            : "다른 설정 저장이 진행 중입니다 — 끝난 뒤 다시 시도해 주세요."
      );
      return;
    }
    const prep = Store.projectPrepare();
    const openedPipeRev = Store.state.P ? Store.state.P.rev : 0;
    const openedCropGenSeen = Store.state.cropGenSeen;
    const catalog = Data.modelCatalog();
    const curState = {
      model: prep.model,
      quality: prep.quality,
      n: Number(prep.n) || 4,
      complexity: prep.complexity || "high",
      image_size: (prep.opts && prep.opts.image_size) || "2K",
      thinking_level: (prep.opts && prep.opts.thinking_level) || "medium",
    };
    const ov = typeof Store.countCropGenOverrides === "function"
      ? Store.countCropGenOverrides()
      : { customModel: typeof Store.countCustomModelOverrides === "function" ? Store.countCustomModelOverrides() : 0, customN: 0, customModelInheritN: 0 };
    const customCount = ov.customModel;

    const wrap = Data.el("div", { className: "proj-def-grid" });
    const selModel = Data.el("select", { className: "select-input", id: "pd-model" });
    for (const m of catalog) {
      const label = m.available === false ? `${m.label} (사용 불가)` : `${m.label} · 최대 참조 ${m.max_refs}장`;
      const opt = Data.el("option", { value: m.name, text: label, disabled: m.available === false });
      if (m.name === curState.model) opt.selected = true;
      selModel.append(opt);
    }

    const rowQual = Data.el("div", { className: "proj-def-row" });
    const rowComp = Data.el("div", { className: "proj-def-row" });
    const rowOpts = Data.el("div", { className: "proj-def-row" });
    const rowCount = Data.el("div", { className: "proj-def-row" });

    const renderDynamic = () => {
      const m = Data.findModelExact(curState.model) || Data.findModel(curState.model);
      rowQual.innerHTML = "";
      if (m && m.supports_quality && m.qualities.length) {
        if (!m.qualities.includes(curState.quality)) curState.quality = m.default_quality || m.qualities[0];
        const selQ = Data.el("select", {
          className: "select-input",
          id: "pd-quality",
          onChange: (e) => { curState.quality = e.target.value; },
        });
        for (const q of m.qualities) {
          const o = Data.el("option", { value: q, text: q });
          if (q === curState.quality) o.selected = true;
          selQ.append(o);
        }
        rowQual.append(Data.el("label", { text: "기본 생성 품질" }), selQ);
        rowQual.classList.remove("hidden");
      } else {
        rowQual.classList.add("hidden");
      }

      rowComp.innerHTML = "";
      if (m && Array.isArray(m.complexities) && m.complexities.length) {
        if (!m.complexities.includes(curState.complexity)) curState.complexity = "high";
        const selC = Data.el("select", {
          className: "select-input",
          id: "pd-complexity",
          onChange: (e) => { curState.complexity = e.target.value; },
        });
        for (const c of m.complexities) {
          const o = Data.el("option", { value: c, text: (Data.COMPLEXITY_LABELS && Data.COMPLEXITY_LABELS[c]) || c });
          if (c === curState.complexity) o.selected = true;
          selC.append(o);
        }
        rowComp.append(Data.el("label", { text: "기본 지시 복잡도" }), selC);
        rowComp.classList.remove("hidden");
      } else {
        rowComp.classList.add("hidden");
      }

      rowOpts.innerHTML = "";
      const optEntries = m && m.opts ? Object.entries(m.opts).filter(([k, ch]) => k !== "aspect_ratio" && Array.isArray(ch) && ch.length) : [];
      if (optEntries.length) {
        for (const [k, choices] of optEntries) {
          if (!choices.includes(curState[k])) curState[k] = (m.opt_defaults && m.opt_defaults[k]) || choices[0];
          const label = k === "image_size" ? "기본 출력 해상도" : k === "thinking_level" ? "기본 추론 깊이" : k;
          const selO = Data.el("select", {
            className: "select-input",
            id: `pd-opt-${k}`,
            onChange: (e) => { curState[k] = e.target.value; },
          });
          for (const ch of choices) {
            const o = Data.el("option", { value: ch, text: k === "thinking_level" ? (Data.THINKING_LABELS[ch] || ch) : ch });
            if (ch === curState[k]) o.selected = true;
            selO.append(o);
          }
          rowOpts.append(Data.el("label", { text: label }), selO);
        }
        rowOpts.classList.remove("hidden");
      } else {
        rowOpts.classList.add("hidden");
      }

      rowCount.innerHTML = "";
      const maxN = (m && m.max_n) || 8;
      if (curState.n > maxN) curState.n = maxN;
      const selN = Data.el("select", {
        className: "select-input",
        id: "pd-count",
        onChange: (e) => { curState.n = Number(e.target.value); },
      });
      for (let i = 1; i <= maxN; i++) {
        const o = Data.el("option", { value: String(i), text: `${i}장` });
        if (i === curState.n) o.selected = true;
        selN.append(o);
      }
      rowCount.append(Data.el("label", { text: `기본 후보 장수 (최대 ${maxN}장)` }), selN);
    };

    selModel.addEventListener("change", (e) => {
      curState.model = e.target.value;
      renderDynamic();
    });
    renderDynamic();

    const noteLines = [
      "프로젝트 기본값은 「프로젝트 기본 모델」을 따르는 모든 영역에 적용됩니다.",
      customCount > 0 ? `따로 모델을 정한 영역 ${customCount}개는 기존 모델을 유지합니다.` : "현재 따로 모델을 정한 영역은 없습니다.",
      ...(ov.customModelInheritN > 0 ? [`단, 모델만 따로 정하고 장수는 기본값을 따르는 영역 ${ov.customModelInheritN}개는 기본 장수 변경 시 각 모델 상한 내에서 함께 바뀝니다.`] : []),
      ...(ov.customN > 0 ? [`장수를 따로 정한 영역 ${ov.customN}개는 기존 장수를 유지합니다.`] : []),
      "변경 후 다음 생성에서 반영 확인이 표시됩니다.",
    ];
    wrap.append(
      Data.el("div", { className: "proj-def-row" }, Data.el("label", { text: "프로젝트 기본 모델" }), selModel),
      rowQual,
      rowComp,
      rowOpts,
      rowCount,
      Data.el("div", { className: "proj-def-note", text: noteLines.join(" ") }),
    );

    let saving = false;
    let modalClosed = false;
    openModal({
      title: "프로젝트 기본 모델 · 옵션 변경",
      bodyNodes: [wrap],
      canClose: () => !saving,
      onClose: () => { modalClosed = true; },
      actions: [
        { label: "취소", cls: "btn-secondary", onClick: () => { if (!saving) closeModal(); } },
        {
          id: "btn-pd-save",
          label: "프로젝트 기본값으로 저장",
          cls: "btn-primary",
          onClick: async () => {
            if (saving || modalClosed) return;
            // R2-2 · #C43-3: 모달을 열어 둔 사이 다른 창이 프로젝트/패치 설정을 바꿨으면 옛 값으로 덮어쓰지 않고 최신 값으로 다시 연다
            if ((Store.state.P ? Store.state.P.rev : 0) !== openedPipeRev
                || (Api.can("v2_crop_model") && Store.state.cropGenSeen !== openedCropGenSeen)) {
              Store.toast("다른 창에서 설정이 변경되어 최신 값으로 다시 열었습니다 — 확인 후 저장해 주세요.", "err");
              openProjectDefaultsModal();
              return;
            }
            const m = Data.findModelExact(curState.model);
            if (m && m.available === false) {
              Store.toast(m.why || "이 모델은 현재 서버에서 사용할 수 없습니다.", "err");
              return;
            }
            const mSafe = m || Data.findModel(curState.model);
            const patch = { model: curState.model, n: curState.n };
            if (mSafe && mSafe.supports_quality) patch.quality = curState.quality;
            if (mSafe && mSafe.complexities && mSafe.complexities.length) patch.complexity = curState.complexity;
            if (mSafe && mSafe.opts && mSafe.opts.image_size) patch.image_size = curState.image_size;
            if (mSafe && mSafe.opts && mSafe.opts.thinking_level) patch.thinking_level = curState.thinking_level;
            const summary = Data.formatModelSummary({
              model: curState.model,
              quality: patch.quality,
              complexity: patch.complexity,
              opts: { image_size: patch.image_size, thinking_level: patch.thinking_level },
              n: curState.n,
            });
            const countChanged = Number(curState.n) !== Number(prep.n);
            const curOv = typeof Store.countCropGenOverrides === "function" ? Store.countCropGenOverrides() : ov;
            const msgLines = [
              `프로젝트 기본 설정을 "${summary}"(으)로 변경합니다.`,
              "기본값을 따르는 모든 영역에 적용되며, 다음 생성에서 반영 확인이 표시됩니다.",
              ...(curOv.customModel > 0 ? [`따로 모델을 정한 영역 ${curOv.customModel}개는 기존 모델을 유지합니다.`] : []),
              ...(countChanged && curOv.customModelInheritN > 0 ? [`단, 모델만 따로 정하고 장수는 기본값을 따르는 영역 ${curOv.customModelInheritN}개의 후보 장수는 ${curState.n}장(각 모델 상한 내)으로 함께 바뀝니다.`] : []),
              ...(countChanged && curOv.customN > 0 ? [`장수를 따로 정한 영역 ${curOv.customN}개는 기존 장수를 유지합니다.`] : []),
              "계속할까요?",
            ];
            const ok = await Data.askDialogConfirm(msgLines.join("\n"), { ok: "프로젝트 기본값 변경", cancel: "취소" });
            if (!ok || modalClosed) return;
            // #C43-9 보강: 확인 직후 재조회 구간부터 닫기·취소·모달 교체를 잠가 취소한 저장이 새 모달을 닫는 경합을 차단한다
            const btn = $("btn-pd-save");
            const mClose = $("modal-close");
            const setSavingLock = (locked) => {
              saving = locked;
              if (btn) btn.disabled = locked;
              if (mClose) mClose.disabled = locked;
            };
            setSavingLock(true);
            // R2-2 · #C43-3: 확인 창을 열어 둔 사이 다른 창이 저장했는지 경량 재조회 후 다시 대조한다
            if (Store.state.root && Store.state.S && Store.state.S.rev !== undefined) {
              const okFresh = await Store.refreshAll(false, { awaitInFlight: true });
              if (!okFresh || modalClosed) {
                setSavingLock(false);
                if (!okFresh) Store.toast("최신 설정 상태를 확인하지 못해 저장을 중단했습니다 — 잠시 후 다시 시도해 주세요.", "err");
                return;
              }
            }
            // #C43-3 보강: GET /state 의 crop_gen_rev·pipeline_rev 와 실제 읽은 값의 불일치·조회 실패까지 모두 차단한다
            const stateCgRev = Store.state.S && ("crop_gen_rev" in Store.state.S) ? Store.state.S.crop_gen_rev : undefined;
            const statePipeRev = Store.state.S && ("pipeline_rev" in Store.state.S) ? Store.state.S.pipeline_rev : undefined;
            const curPipeRev = Store.state.P ? Store.state.P.rev : 0;
            const cgUnavailAfter = typeof Store.isCropGenUnavailable === "function" && Store.isCropGenUnavailable();
            const cgRevMismatch = Api.can("v2_crop_model") && stateCgRev !== undefined && (stateCgRev === null || stateCgRev !== Store.state.cropGenSeen);
            const pipeRevMismatch = statePipeRev !== undefined && (statePipeRev === null || statePipeRev !== curPipeRev);
            if (cgUnavailAfter || cgRevMismatch || pipeRevMismatch) {
              setSavingLock(false);
              Store.toast(
                pipeRevMismatch
                  ? "최신 프로젝트 설정을 확인하지 못해 저장을 중단했습니다 — 잠시 후 다시 시도해 주세요."
                  : "패치별 모델 설정을 최신 상태로 확인하지 못해 저장을 중단했습니다 — [다시 읽기]로 확인해 주세요.",
                "err"
              );
              Store.emit("selection");
              return;
            }
            if (curPipeRev !== openedPipeRev
                || (statePipeRev !== undefined && statePipeRev !== openedPipeRev)
                || (Api.can("v2_crop_model") && Store.state.cropGenSeen !== openedCropGenSeen)) {
              setSavingLock(false);
              Store.toast("확인 창을 열어 둔 사이 다른 창에서 설정이 변경되었습니다 — 최신 값으로 다시 열었습니다.", "err");
              openProjectDefaultsModal();
              return;
            }
            if (Store.state.freeApplyBusy || Store.state.runFlowBusy || Store.state.assistBusy) {
              setSavingLock(false);
              Store.toast("합성 적용·생성 흐름이 진행 중이어서 프로젝트 기본 설정 저장을 중단했습니다.");
              return;
            }
            // #C43-9: POST /pipeline/settings 응답과 후속 refreshAll(true)이 끝날 때까지 생성·AI 작성·실행 취소를 잠근다
            const res = await Store.saveProjectPrepare(patch, {
              expectedPipeRev: openedPipeRev,
              successToast: `프로젝트 기본 설정을 ${summary}(으)로 저장했습니다.`,
              onBeforeRefresh: () => {
                setSavingLock(false);
                if (!modalClosed) closeModal(true);
              },
            });
            if (!res || !res.ok) {
              setSavingLock(false);
              if (res && (res.status === 409 || res.stale)) {
                await Store.refreshAll(true);
                if (!modalClosed) openProjectDefaultsModal();
              }
            }
          },
        },
      ],
    });
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
      const S = Store.state.S;
      const canSaveForge = !!(S && S.canvas && !S.running && S.export_state !== "none");
      actions.push({
        label: "Forge 자료함에 저장", cls: "btn-secondary",
        disabled: !canSaveForge,
        title: canSaveForge ? "합성 이미지를 Forge 내 자료에 저장" : "생성 중이거나 내보낼 수 있는 합성 결과가 없을 때는 저장할 수 없습니다.",
        onClick: async () => {
          // #C08-4: v1(forge_import.js saveButton)과 동일하게 저장 전 확인 상자를 띄운다
          const ok = await Data.askDialogConfirm(
            "합성 그림을 Forge 내 자료에 저장합니다. 계속할까요?",
            { ok: "저장", cancel: "취소" }
          );
          if (!ok) return;
          closeModal();
          try {
            const data = await Api.saveToForge(Store.state.root);
            const assetName = data && data.asset && data.asset.name ? `: ${data.asset.name}` : "";
            Store.toast((data && data.already ? "이미 저장한 그림입니다" : "Forge 내 자료에 저장했습니다") + assetName, "ok");
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
            // #C07-2: waited.done && waited.ok 일 때만 내려받기를 시작한다(옛 PSD 오다운로드 방지)
            if (!waited || !waited.done) {
              Store.toast(
                waited && waited.reason === "timeout"
                  ? "PSD 내보내기 대기 시간이 초과되었습니다 — 완료 후 다시 시도해 주세요."
                  : "PSD 내보내기 완료 여부를 확인하지 못했습니다.",
                "err"
              );
              return;
            }
            if (!waited.ok) {
              Store.toast("PSD 내보내기 작업이 실패했습니다.", "err");
              return;
            }
            // #C11-7: 내보낼 후보가 없어 outcome === "empty" 로 끝나면 옛 PSD 를 내려받지 않는다
            const expOut = waited.record && waited.record.outputs && waited.record.outputs.dp_export
              && waited.record.outputs.dp_export.bmk_dp_export;
            const expInfo = Array.isArray(expOut) && expOut.length ? expOut[0] : null;
            await Store.refreshAll(true);
            if (expInfo && expInfo.outcome === "empty") {
              Store.toast("PSD를 쓰지 않았습니다 — 내보낼 후보(★ 채택)가 없습니다.", "err");
              return;
            }
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
    if (Store.state.runFlowBusy || Store.state.assistBusy) return;
    const p = Store.primaryPatch();
    if (!p) return;
    const pr = Data.purposeInfo(p.meta.purpose);
    const wrap = Data.el("div", { style: "display:flex;flex-direction:column;gap:6px;" });
    for (const txt of (pr.presets || [])) {
      wrap.append(Data.el("button", {
        type: "button", className: "preset-card",
        onClick: () => {
          p.meta.user_instruction_ko = txt;
          Store.markFieldDirty(p.meta, "instruction");
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
      ["크기 128px · 위치 16px 스냅 임시 해제", "Ctrl + 드래그"],
      ["비율 유지 / 정사각형 그리기", "Shift + 드래그"],
      ["선택 영역 미세 이동 (16 / 128 / 1px)", "방향키 / Shift / Ctrl"],
      ["마스크 살리기 브러시 / 지우개", "B / E"],
      ["레퍼런스 시트 오리기 · 4점 원근", "R"],
      ["선택 해제 (밝은 원본 전체 보기)", "Esc"],
      ["원본과 즉시 비교", "Q / Tab"],
      ["좌우 분할 비교", "S"],
      ["영역선 표시 / 경계 이음매", "O / N"],
      ["작업 히스토리 패널 (이동·부착 가능)", "H"],
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
  const homeState = {
    query: "",
    filter: "all", // all | pinned | working | exported
    tab: "active", // active | archived (#C31 · caps.v2_project_home)
    loading: false,
    archivedLoading: false,
    archivedError: "",
    openingKey: "",
    metaBusyKey: "",
    loadError: "",
  };
  let archivedLoadSeq = 0;
  const archiveToastByKey = new Map();

  function dismissArchiveToast(projKey) {
    if (!projKey) return;
    const prev = archiveToastByKey.get(projKey);
    if (prev && prev.parentNode) prev.remove();
    archiveToastByKey.delete(projKey);
  }

  function canManageProjectHome() {
    return Api.can("v2_project_home");
  }

  function sortProjectsByModifiedDesc(arr) {
    return arr.slice().sort((a, b) => {
      const modCmp = String((b && b.modified) || "").localeCompare(String((a && a.modified) || ""));
      if (modCmp !== 0) return modCmp;
      return Data.projectDisplayName(a).localeCompare(Data.projectDisplayName(b), "ko");
    });
  }

  function applyProjectMetaUpdate(updatedProj, archivedCount) {
    if (typeof archivedCount === "number" && Number.isFinite(archivedCount)) {
      Store.state.archivedCount = Math.max(0, archivedCount);
    }
    if (updatedProj && updatedProj.id) {
      dismissArchiveToast(updatedProj.id);
      const isArchived = Boolean(updatedProj.archived_at);
      const activeList = Array.isArray(Store.state.projects) ? Store.state.projects : [];
      const archivedList = Array.isArray(Store.state.archivedProjects) ? Store.state.archivedProjects : [];
      if (isArchived) {
        Store.state.projects = activeList.filter((p) => p.id !== updatedProj.id);
        const restArch = archivedList.filter((p) => p.id !== updatedProj.id);
        Store.state.archivedProjects = [updatedProj, ...restArch];
      } else {
        Store.state.archivedProjects = archivedList.filter((p) => p.id !== updatedProj.id);
        const idx = activeList.findIndex((p) => p.id === updatedProj.id);
        if (idx >= 0) {
          const next = activeList.slice();
          next[idx] = updatedProj;
          Store.state.projects = next;
        } else {
          Store.state.projects = sortProjectsByModifiedDesc([updatedProj, ...activeList]);
        }
      }
    }
    drawProjectsHome();
  }

  function handleProjectMetaError(err, fallbackMsg) {
    const data = err && err.data && typeof err.data === "object" ? err.data : null;
    const reason = data && data.reason ? String(data.reason) : "";
    const msg = (data && data.error) || (err && err.message) || fallbackMsg || "요청을 처리하지 못했습니다.";
    if (reason === "stale" && data && data.project) {
      applyProjectMetaUpdate(data.project, data.archived_count);
      Store.toast(msg, "err");
      return { reason, msg };
    }
    if (reason === "not_found") {
      Store.toast(msg, "err");
      if (homeState.tab === "archived") loadArchivedProjects();
      else renderProjectsHome();
      return { reason, msg };
    }
    Store.toast(msg, "err");
    return { reason, msg };
  }

  async function loadArchivedProjects() {
    if (!canManageProjectHome()) return;
    const seq = ++archivedLoadSeq;
    homeState.archivedLoading = true;
    homeState.archivedError = "";
    const refreshBtn = $("btn-home-refresh");
    if (refreshBtn) refreshBtn.disabled = true;
    drawProjectsHome();
    try {
      const res = await Api.listArchivedProjects();
      if (seq !== archivedLoadSeq) return;
      const list = Array.isArray(res && res.projects) ? res.projects : [];
      Store.state.archivedProjects = list;
      if (typeof (res && res.archived_count) === "number" && Number.isFinite(res.archived_count)) {
        Store.state.archivedCount = Math.max(0, res.archived_count);
      } else {
        Store.state.archivedCount = list.length;
      }
    } catch (err) {
      if (seq !== archivedLoadSeq) return;
      homeState.archivedError = (err && err.message) || "알 수 없는 오류";
    } finally {
      if (seq === archivedLoadSeq) {
        homeState.archivedLoading = false;
        if (refreshBtn) refreshBtn.disabled = false;
        drawProjectsHome();
      }
    }
  }

  function openArchivedTab() {
    if (!canManageProjectHome()) return;
    homeState.tab = "archived";
    homeState.query = "";
    loadArchivedProjects();
  }

  function openActiveTab() {
    homeState.tab = "active";
    homeState.query = "";
    drawProjectsHome();
  }

  // #C31-9 · #C31-10 · #C31-11: 프로젝트 표시 이름 변경 모달 (폴더·키·Export 불변 · 빈 값 = 폴더 이름으로 복원)
  function openRenameProjectModal(proj) {
    if (!canManageProjectHome() || !proj || !proj.id) return;
    if (proj.openable === false || homeState.metaBusyKey || homeState.openingKey) return;

    let curProj = proj;
    let saving = false;
    let modalClosed = false;

    const folderNameEl = Data.el("strong", { text: curProj.name || curProj.id });
    const resetBtn = Data.el("button", {
      type: "button",
      id: "btn-rename-reset",
      className: "mini-btn",
      text: "폴더 이름으로 되돌리기",
      title: "표시 이름을 지우고 원래 폴더 이름으로 되돌립니다",
      onClick: () => submitRename(""),
    });

    const charCountEl = Data.el("span", { className: "rename-char-count", id: "rename-char-count" });
    const titleInput = Data.el("input", {
      id: "rename-title-input",
      type: "text",
      className: "np-text-input",
      autocomplete: "off",
      placeholder: "표시 이름 입력 (비워 두면 폴더 이름으로 돌아갑니다)",
      value: curProj.title || curProj.name || "",
    });
    const errEl = Data.el("small", { id: "rename-error", className: "np-error-text", role: "alert" });

    function updateCharCount() {
      const len = titleInput.value.trim().length;
      charCountEl.textContent = `${len} / 64`;
      charCountEl.classList.toggle("over", len > 64);
    }

    function syncResetBtn() {
      const hasCustom = Boolean(curProj && curProj.title);
      resetBtn.classList.toggle("hidden", !hasCustom);
      resetBtn.disabled = saving || !hasCustom;
    }

    function setSaving(flag) {
      saving = !!flag;
      titleInput.disabled = saving;
      syncResetBtn();
      const mClose = $("modal-close");
      if (mClose) mClose.disabled = saving;
      const btnCancel = $("btn-rename-cancel");
      if (btnCancel) btnCancel.disabled = saving;
      const btnSave = $("btn-rename-save");
      if (btnSave) {
        btnSave.disabled = saving;
        btnSave.textContent = saving ? "저장 중…" : "저장";
      }
    }

    async function submitRename(overrideTitle) {
      if (saving || modalClosed || homeState.metaBusyKey) return;
      const nextTitle = overrideTitle !== undefined ? String(overrideTitle) : String(titleInput.value || "");
      const seen = Number.isInteger(curProj.meta_rev) ? curProj.meta_rev : 0;
      errEl.textContent = "";
      homeState.metaBusyKey = curProj.id;
      setSaving(true);
      try {
        const res = await Api.updateProjectMeta({
          key: curProj.id,
          seen,
          op: "rename",
          title: nextTitle,
        });
        if (res && res.unsupported) {
          setSaving(false);
          errEl.textContent = res.error || "프로젝트 홈 관리 기능이 아직 켜져 있지 않습니다.";
          return;
        }
        const updated = (res && res.project) || curProj;
        applyProjectMetaUpdate(updated, res && res.archived_count);
        setSaving(false);
        if (!modalClosed) closeModal(true);
        if (res && res.changed === false) {
          Store.toast("변경된 내용이 없습니다.");
        } else if (updated && updated.title) {
          Store.toast(`표시 이름을 "${updated.title}"(으)로 변경했습니다.`, "ok");
        } else {
          Store.toast(`표시 이름을 지우고 폴더 이름("${(updated && updated.name) || curProj.name}")으로 되돌렸습니다.`, "ok");
        }
      } catch (err) {
        setSaving(false);
        const data = err && err.data && typeof err.data === "object" ? err.data : null;
        const reason = data && data.reason ? String(data.reason) : "";
        const msg = (data && data.error) || (err && err.message) || "이름 변경에 실패했습니다.";
        if (reason === "stale" && data && data.project) {
          applyProjectMetaUpdate(data.project, data.archived_count);
          if (data.project.archived_at) {
            if (!modalClosed) closeModal(true);
            Store.toast(msg, "err");
            return;
          }
          curProj = data.project;
          folderNameEl.textContent = curProj.name || curProj.id;
          titleInput.value = curProj.title || curProj.name || "";
          updateCharCount();
          syncResetBtn();
          errEl.textContent = `${msg} — 최신 이름으로 갱신했습니다. 확인 후 다시 저장해 주세요.`;
          if (!modalClosed && canFocusSafely()) {
            try { titleInput.focus(); titleInput.select(); } catch (_) {}
          }
          return;
        }
        if (reason === "not_found" || reason === "archived") {
          if (!modalClosed) closeModal(true);
          Store.toast(msg, "err");
          renderProjectsHome();
          return;
        }
        errEl.textContent = msg;
        if (reason === "meta_broken") Store.toast(msg, "err");
        if (!modalClosed && canFocusSafely()) {
          try { titleInput.focus(); } catch (_) {}
        }
      } finally {
        if (homeState.metaBusyKey === curProj.id) homeState.metaBusyKey = "";
        drawProjectsHome();
      }
    }

    titleInput.addEventListener("input", () => {
      updateCharCount();
      if (errEl.textContent) errEl.textContent = "";
    });
    titleInput.addEventListener("keydown", (e) => {
      if (e.key !== "Enter" || e.isComposing || e.keyCode === 229) return;
      e.preventDefault();
      submitRename();
    });

    updateCharCount();
    syncResetBtn();

    const bodyWrap = Data.el("div", { className: "rename-modal-body" },
      Data.el("div", { className: "rename-folder-row" },
        Data.el("span", {}, "폴더 이름(고정): ", folderNameEl),
        resetBtn,
      ),
      Data.el("div", { className: "np-field" },
        Data.el("div", { className: "rename-field-head" },
          Data.el("label", { for: "rename-title-input", text: "표시 이름" }),
          charCountEl,
        ),
        titleInput,
        errEl,
      ),
      Data.el("div", {
        className: "rename-meta-note",
        text: "화면의 표시 이름만 바뀌며 폴더 이름·프로젝트 키·Export 파일 이름은 그대로 유지됩니다. 문자·숫자·한글·_ - .·공백(최대 64자)을 쓸 수 있으며 비워 두고 저장하면 폴더 이름으로 돌아갑니다.",
      }),
    );

    openModal({
      title: "프로젝트 이름 변경",
      bodyNodes: [bodyWrap],
      canClose: () => !saving,
      onClose: () => { modalClosed = true; },
      actions: [
        { id: "btn-rename-cancel", label: "취소", cls: "btn-secondary", onClick: () => { if (!saving) closeModal(); } },
        { id: "btn-rename-save", label: "저장", cls: "btn-primary", onClick: () => submitRename() },
      ],
    });

    if (canFocusSafely()) {
      try { titleInput.focus(); titleInput.select(); } catch (_) {}
    }
  }

  // #C31-12 (D670): 보관 확인 상자 → POST projects/meta {op: "archive"} → [되돌리기] 토스트
  async function archiveProject(proj) {
    if (!canManageProjectHome() || !proj || !proj.id) return;
    if (proj.openable === false || homeState.metaBusyKey || homeState.openingKey) return;
    const dispName = Data.projectDisplayName(proj);
    const ok = await Data.askDialogConfirm(
      `"${dispName}" 프로젝트를 보관할까요?\n목록에서 숨깁니다. 파일은 지워지지 않고 [보관함]에서 되돌릴 수 있습니다. 서버 용량은 그대로 차지합니다.`,
      { title: "프로젝트 보관", ok: "보관", cancel: "취소" }
    );
    if (!ok) return;
    if (homeState.metaBusyKey || homeState.openingKey) return;

    const seen = Number.isInteger(proj.meta_rev) ? proj.meta_rev : 0;
    homeState.metaBusyKey = proj.id;
    drawProjectsHome();
    try {
      const res = await Api.updateProjectMeta({ key: proj.id, seen, op: "archive" });
      if (res && res.unsupported) {
        Store.toast(res.error || "프로젝트 홈 관리 기능이 아직 켜져 있지 않습니다.", "err");
        return;
      }
      const updated = (res && res.project) || Object.assign({}, proj, { archived_at: "방금" });
      applyProjectMetaUpdate(updated, res && res.archived_count);
      dismissArchiveToast(proj.id);
      const nextName = Data.projectDisplayName(updated);
      const toastEl = Store.toast(`"${nextName}" 프로젝트를 보관했습니다.`, "ok", {
        label: "되돌리기",
        onClick: () => {
          if (homeState.metaBusyKey || homeState.openingKey) return false;
          restoreProject(updated);
        },
      });
      if (toastEl) archiveToastByKey.set(proj.id, toastEl);
    } catch (err) {
      handleProjectMetaError(err, "프로젝트를 보관할 수 없습니다.");
    } finally {
      if (homeState.metaBusyKey === proj.id) homeState.metaBusyKey = "";
      drawProjectsHome();
    }
  }

  // #C31-8 · #C31-12: 보관함 카드 또는 보관 직후 토스트에서 즉시 되돌리기
  async function restoreProject(proj) {
    if (!canManageProjectHome() || !proj || !proj.id) return;
    if (homeState.metaBusyKey || homeState.openingKey) return;

    const seen = Number.isInteger(proj.meta_rev) ? proj.meta_rev : 0;
    homeState.metaBusyKey = proj.id;
    drawProjectsHome();
    try {
      const res = await Api.updateProjectMeta({ key: proj.id, seen, op: "restore" });
      if (res && res.unsupported) {
        Store.toast(res.error || "프로젝트 홈 관리 기능이 아직 켜져 있지 않습니다.", "err");
        return;
      }
      const updated = (res && res.project) || Object.assign({}, proj);
      if (updated && "archived_at" in updated && !(res && res.project && res.project.archived_at)) {
        delete updated.archived_at;
      }
      applyProjectMetaUpdate(updated, res && res.archived_count);
      dismissArchiveToast(proj.id);
      const nextName = Data.projectDisplayName(updated);
      Store.toast(`"${nextName}" 프로젝트를 목록으로 되돌렸습니다.`, "ok");
    } catch (err) {
      handleProjectMetaError(err, "프로젝트를 되돌릴 수 없습니다.");
    } finally {
      if (homeState.metaBusyKey === proj.id) homeState.metaBusyKey = "";
      drawProjectsHome();
    }
  }

  function syncHomeArchiveChrome() {
    const canHome = canManageProjectHome();
    const actionsEl = document.querySelector("#projects-view .projects-actions");
    const existingBtn = $("btn-home-archive");
    const archivedCount = Math.max(0, Number(Store.state.archivedCount) || 0);
    const inArchive = canHome && homeState.tab === "archived";
    const showBtn = canHome && (archivedCount > 0 || inArchive);

    if (!showBtn) {
      if (existingBtn) existingBtn.remove();
    } else if (actionsEl) {
      const btn = existingBtn || Data.el("button", {
        type: "button",
        id: "btn-home-archive",
        className: "btn-secondary-sm btn-home-archive",
        onClick: () => {
          if (homeState.tab === "archived") openActiveTab();
          else openArchivedTab();
        },
      });
      btn.classList.toggle("on", inArchive);
      btn.setAttribute("aria-pressed", inArchive ? "true" : "false");
      btn.title = inArchive ? "활성 프로젝트 목록으로 돌아가기" : `보관된 프로젝트 ${archivedCount}개 보기`;
      btn.innerHTML = "";
      btn.append(
        Data.icon("archive", 14),
        Data.el("span", { text: "보관함" }),
        Data.el("span", { className: "home-archive-badge", text: String(archivedCount) }),
      );
      if (!existingBtn) {
        const refNode = $("btn-home-settings") || actionsEl.firstChild;
        actionsEl.insertBefore(btn, refNode);
      }
    }

    const inner = document.querySelector("#projects-view .projects-inner");
    const existingBanner = $("home-archive-banner");
    if (!inArchive) {
      if (existingBanner) existingBanner.remove();
    } else if (inner) {
      const banner = existingBanner || Data.el("section", {
        id: "home-archive-banner",
        className: "home-archive-banner",
        role: "region",
        "aria-label": "보관함 안내",
      });
      banner.innerHTML = "";
      banner.append(
        Data.el("div", { className: "home-archive-banner-copy" },
          Data.el("strong", {},
            Data.icon("archive", 15),
            Data.el("span", { text: `보관함 (${archivedCount})` }),
          ),
          Data.el("span", { text: "보관된 프로젝트는 목록에서 숨겨져 있으며 파일과 서버 용량은 그대로 유지됩니다. 작업을 이어가려면 먼저 되돌려 주세요." }),
        ),
        Data.el("button", {
          type: "button",
          id: "btn-home-archive-back",
          className: "btn-secondary-sm",
          onClick: () => openActiveTab(),
        }, Data.icon("arrowLeft", 13), Data.el("span", { text: "프로젝트 목록으로" })),
      );
      if (!existingBanner) {
        const hero = $("home-hero");
        if (hero && hero.parentNode === inner) inner.insertBefore(banner, hero);
        else inner.append(banner);
      }
    }
  }

  function classifyProjectStatus(proj) {
    if (!proj || proj.openable === false) return { id: "err", label: "열 수 없음", tone: "err" };
    if (proj.before_import) return { id: "before", label: "가져오기 전", tone: "warn" };
    if (proj.export_state === "same") return { id: "exported", label: "Export 완료", tone: "ok" };
    if (proj.export_state === "changed") return { id: "changed", label: "수정 있음", tone: "accent" };
    if ((proj.crops || 0) > 0 || (proj.candidates || 0) > 0 || (proj.stars || 0) > 0) {
      return { id: "working", label: "작업 중", tone: "info" };
    }
    return { id: "ready", label: "준비됨", tone: "muted" };
  }

  function formatExportMeta(proj) {
    if (!proj || proj.openable === false) return "";
    const ex = proj.export_last;
    if (ex && ex.rev !== undefined && ex.rev !== null) {
      const suffix = proj.export_state === "same" ? "최신" : proj.export_state === "changed" ? "변경됨" : "";
      return suffix ? `Export r${ex.rev} · ${suffix}` : `Export r${ex.rev}`;
    }
    return proj.before_import ? "첫 열기 시 원화 반영" : "Export 전";
  }

  function buildThumbNode(proj, displayName) {
    const openable = proj.openable !== false;
    const thumbUrl = openable && proj.thumb_url ? Api.apiUrl(proj.thumb_url) : "";
    const wrap = Data.el("div", { className: "proj-card-thumb" });
    const makeEmpty = () => Data.el("div", { className: "proj-thumb-empty" },
      Data.icon("image", 22),
      Data.el("span", { text: !openable ? "열 수 없음" : proj.before_import ? "첫 열기 시 원화 반영" : "미리보기 없음" }),
    );
    if (thumbUrl) {
      const img = Data.el("img", { src: thumbUrl, alt: `${displayName} 미리보기`, loading: "lazy", draggable: "false" });
      img.addEventListener("error", () => {
        if (img.parentNode) img.replaceWith(makeEmpty());
      });
      wrap.append(img);
    } else {
      wrap.append(makeEmpty());
    }
    return wrap;
  }

  async function openProjectByKey(proj) {
    if (!proj || !proj.id) return;
    if (proj.openable === false) {
      Store.toast(proj.error || "이 프로젝트는 열 수 없습니다.", "err");
      return;
    }
    if (homeState.openingKey || homeState.metaBusyKey) return;
    homeState.openingKey = proj.id;
    drawProjectsHome();
    try {
      const r = await Api.openProject(proj.id);
      const key = (r && r.key) || proj.id;
      const u = new URL(location.href);
      u.searchParams.set("root", key);
      // #C40-5: 일부 실패 후 [닫기] 로 나갔던 새 프로젝트에 미소비 변환 알림(notes)이 남아 있으면 첫 열기에 new=1 을 붙여 배너를 보여 준다
      let hasPendingNotes = false;
      try {
        const rawNotes = window.sessionStorage.getItem("bmk-dp-new:" + key);
        if (rawNotes) {
          const parsed = JSON.parse(rawNotes);
          hasPendingNotes = Array.isArray(parsed) && parsed.some((n) => typeof n === "string" && n.trim());
        }
      } catch (_) { /* 저장소 차단 무시 */ }
      if (hasPendingNotes) u.searchParams.set("new", "1");
      u.searchParams.set("ui", "v2");
      location.href = u.pathname + u.search;
    } catch (err) {
      homeState.openingKey = "";
      drawProjectsHome();
      const data = err && err.data && typeof err.data === "object" ? err.data : null;
      const reason = data && data.reason ? String(data.reason) : "";
      Store.toast(`프로젝트를 열 수 없습니다: ${(err && err.message) || "알 수 없는 오류"}`, "err");
      if (reason === "archived" || reason === "not_found" || (err && err.status === 404)) {
        renderProjectsHome();
      }
    }
  }

  function renderHomeHero(list, pinnedSet) {
    const hero = $("home-hero");
    const resume = $("home-resume-card");
    const forgeBtn = $("btn-home-drop-forge");
    if (forgeBtn) forgeBtn.classList.toggle("hidden", !Api.can("forge_import"));
    if (!hero || !resume) return;

    const canHome = canManageProjectHome();
    if (canHome && homeState.tab === "archived") {
      hero.classList.add("hidden");
      return;
    }
    hero.classList.remove("hidden");

    // GET /projects 는 서버가 modified 내림차순으로 내려준다 — 가장 최근 수정된 열 수 있는 프로젝트를 스포트라이트
    const recent = list.find((p) => p.openable !== false) || null;
    if (!recent) {
      hero.classList.add("empty-only");
      resume.classList.add("hidden");
      resume.innerHTML = "";
      return;
    }

    hero.classList.remove("empty-only");
    resume.classList.remove("hidden");
    resume.innerHTML = "";

    const dispName = Data.projectDisplayName(recent);
    const hasCustomTitle = !!(recent.title && recent.name && recent.title !== recent.name);
    const st = classifyProjectStatus(recent);
    const isPinned = pinnedSet.has(recent.id);
    const isOpening = homeState.openingKey === recent.id;
    const isMetaBusy = homeState.metaBusyKey === recent.id;

    const thumbWrap = buildThumbNode(recent, dispName);
    thumbWrap.classList.add("home-resume-thumb");

    const pinBtn = Data.el("button", {
      type: "button",
      className: "proj-pin-btn home-resume-pin" + (isPinned ? " pinned" : ""),
      title: isPinned ? "상단 고정 해제" : "프로젝트 상단 고정",
      "aria-label": isPinned ? `${dispName} 상단 고정 해제` : `${dispName} 상단 고정`,
      onClick: (e) => {
        e.stopPropagation();
        const pinned = Store.togglePinnedProject(recent.id);
        Store.toast(pinned ? `"${dispName}" 프로젝트를 상단에 고정했습니다.` : `"${dispName}" 고정을 해제했습니다.`);
        drawProjectsHome();
      },
    }, Data.icon("pin", 14));

    const renameBtn = canHome ? Data.el("button", {
      type: "button",
      className: "btn-secondary-sm proj-rename-btn",
      dataset: { projAction: "rename", projId: recent.id },
      title: "표시 이름 변경 (폴더·키는 그대로 유지)",
      disabled: !!homeState.openingKey || !!homeState.metaBusyKey,
      onClick: (e) => {
        e.stopPropagation();
        openRenameProjectModal(recent);
      },
    }, Data.icon("edit", 13), Data.el("span", { text: "이름 변경" })) : null;

    const archiveBtn = canHome ? Data.el("button", {
      type: "button",
      className: "btn-secondary-sm proj-archive-btn",
      dataset: { projAction: "archive", projId: recent.id },
      title: "목록에서 숨기기 (파일·용량은 유지되며 보관함에서 되돌릴 수 있습니다)",
      disabled: !!homeState.openingKey || !!homeState.metaBusyKey,
      onClick: (e) => {
        e.stopPropagation();
        archiveProject(recent);
      },
    }, Data.icon("archive", 13), Data.el("span", { text: isMetaBusy ? "보관 중…" : "보관" })) : null;

    const openBtn = Data.el("button", {
      type: "button",
      id: "btn-home-resume",
      className: "btn-primary-sm home-resume-cta",
      disabled: !!homeState.openingKey || !!homeState.metaBusyKey,
      onClick: (e) => {
        e.stopPropagation();
        openProjectByKey(recent);
      },
    },
      Data.el("span", { text: isOpening ? "여는 중…" : "작업 이어하기" }),
      Data.icon("arrowRight", 14),
    );

    const body = Data.el("div", { className: "home-resume-body" },
      Data.el("div", { className: "home-resume-kicker-row" },
        Data.el("span", { className: "home-resume-kicker", text: "최근 작업 이어하기" }),
        Data.el("span", { className: `proj-status-badge tone-${st.tone}`, text: st.label }),
        isPinned ? Data.el("span", { className: "proj-status-badge tone-pin", text: "고정됨" }) : null,
        canHome && recent.meta_error ? Data.el("span", { className: "proj-status-badge tone-err", text: "설정 파일 오류", title: "프로젝트 설정 파일(project_meta.json)을 읽지 못했습니다 — 관리자에게 문의해 주세요" }) : null,
      ),
      Data.el("div", { className: "home-resume-title-row" },
        Data.el("h2", { text: dispName, title: dispName }),
        hasCustomTitle ? Data.el("span", { className: "proj-folder-sub", text: recent.name, title: `폴더 이름: ${recent.name}` }) : null,
      ),
      Data.el("div", { className: "home-resume-stats" },
        Data.el("span", { className: "proj-stat-pill", text: `영역 ${recent.crops || 0}개` }),
        Data.el("span", { className: "proj-stat-pill", text: `후보 ${recent.candidates || 0}장` }),
        Data.el("span", { className: "proj-stat-pill star", text: `확정 ★ ${recent.stars || 0}` }),
      ),
      Data.el("div", { className: "home-resume-foot" },
        Data.el("div", { className: "home-resume-meta" },
          Data.el("span", { text: formatExportMeta(recent) }),
          recent.modified ? Data.el("span", { className: "proj-card-time", text: Data.formatRelativeTime(recent.modified), title: `마지막 수정: ${recent.modified}` }) : null,
        ),
        Data.el("div", { className: "home-resume-actions" }, pinBtn, renameBtn, archiveBtn, openBtn),
      ),
    );

    resume.onclick = () => openProjectByKey(recent);
    resume.append(thumbWrap, body);
  }

  function drawArchivedProjectsView(grid, hp) {
    const list = Array.isArray(Store.state.archivedProjects) ? Store.state.archivedProjects : [];
    const toolbarEl = $("home-toolbar");
    const filterBar = $("home-filter-bar");
    const metaEl = $("home-section-meta");
    const countEl = $("home-result-count");
    const prefsHintEl = $("home-prefs-hint");
    const searchInp = $("home-search-input");
    const clearBtn = $("btn-home-search-clear");
    if (searchInp && searchInp.value !== homeState.query) searchInp.value = homeState.query;
    if (clearBtn) clearBtn.classList.toggle("hidden", !homeState.query);
    if (filterBar) filterBar.classList.add("hidden");

    const isEmptyArch = !list.length && !homeState.archivedLoading && !homeState.archivedError;
    if (toolbarEl) toolbarEl.classList.toggle("hidden", isEmptyArch);
    if (metaEl) metaEl.classList.remove("hidden");
    if (prefsHintEl) prefsHintEl.textContent = "보관된 프로젝트는 되돌린 뒤 열 수 있습니다";

    const q = homeState.query.trim().toLocaleLowerCase();
    const shown = list.filter((p) => {
      if (!q) return true;
      const hayTitle = String(p.title || "").toLocaleLowerCase();
      const hayName = String(p.name || "").toLocaleLowerCase();
      return hayTitle.includes(q) || hayName.includes(q);
    });

    shown.sort((a, b) => {
      if (hp.sortBy === "name") {
        const cmp = Data.projectDisplayName(a).localeCompare(Data.projectDisplayName(b), "ko");
        if (cmp !== 0) return cmp;
      } else if (hp.sortBy === "stars") {
        const d = (b.stars || 0) - (a.stars || 0);
        if (d !== 0) return d;
      } else if (hp.sortBy === "crops") {
        const d = (b.crops || 0) - (a.crops || 0);
        if (d !== 0) return d;
      }
      const arcCmp = String(b.archived_at || "").localeCompare(String(a.archived_at || ""));
      if (arcCmp !== 0) return arcCmp;
      return String(b.modified || "").localeCompare(String(a.modified || ""));
    });

    if (countEl) {
      if (homeState.archivedLoading) countEl.textContent = "보관함을 불러오는 중…";
      else if (homeState.archivedError) countEl.textContent = "보관함 조회 오류";
      else if (q) countEl.textContent = `보관함 표시 ${shown.length}개 / 전체 ${list.length}개`;
      else countEl.textContent = `보관된 프로젝트 ${list.length}개`;
    }

    const isList = hp.viewMode === "list";
    grid.classList.toggle("view-list", isList && !isEmptyArch);
    grid.innerHTML = "";

    if (homeState.archivedError) {
      grid.append(Data.el("div", { className: "empty-card home-empty-card" },
        Data.el("p", { text: `보관함을 불러오지 못했습니다: ${homeState.archivedError}` }),
        Data.el("div", { className: "empty-actions" },
          Data.el("button", { type: "button", className: "btn-secondary-sm", text: "다시 시도", onClick: () => loadArchivedProjects() }),
          Data.el("button", { type: "button", className: "btn-secondary-sm", text: "프로젝트 목록으로", onClick: () => openActiveTab() }),
        ),
      ));
      return;
    }

    if (homeState.archivedLoading && !list.length) return;

    if (!list.length) {
      grid.append(Data.el("div", { className: "empty-card home-empty-card" },
        Data.el("p", { text: "보관된 프로젝트가 없습니다" }),
        Data.el("small", { text: "프로젝트 카드의 [보관]을 누르면 파일은 그대로 둔 채 목록에서만 숨겨집니다." }),
        Data.el("div", { className: "empty-actions" },
          Data.el("button", { type: "button", className: "btn-primary-sm", text: "프로젝트 목록으로 돌아가기", onClick: () => openActiveTab() }),
        ),
      ));
      return;
    }

    if (!shown.length) {
      grid.append(Data.el("div", { className: "empty-card home-empty-card" },
        Data.el("p", { text: "검색어에 맞는 보관 프로젝트가 없습니다" }),
        Data.el("div", { className: "empty-actions" },
          Data.el("button", {
            type: "button",
            className: "btn-secondary-sm",
            text: "검색 초기화",
            onClick: () => { homeState.query = ""; drawProjectsHome(); },
          }),
        ),
      ));
      return;
    }

    for (const proj of shown) {
      const dispName = Data.projectDisplayName(proj);
      const hasCustomTitle = !!(proj.title && proj.name && proj.title !== proj.name);
      const isBusy = homeState.metaBusyKey === proj.id;
      const thumbWrap = buildThumbNode(proj, dispName);
      if (!isList) {
        thumbWrap.append(Data.el("span", { className: "proj-status-badge proj-thumb-badge tone-warn", text: "보관됨" }));
      }
      const titleRow = Data.el("div", { className: "proj-title-row" },
        Data.el("strong", { text: dispName, title: hasCustomTitle ? `${dispName} (폴더: ${proj.name})` : dispName }),
        hasCustomTitle ? Data.el("span", { className: "proj-folder-sub", text: proj.name, title: `폴더 이름: ${proj.name}` }) : null,
        proj.meta_error ? Data.el("span", { className: "proj-status-badge tone-err proj-meta-err-badge", text: "설정 파일 오류", title: "프로젝트 설정 파일(project_meta.json)을 읽지 못했습니다 — 관리자에게 문의해 주세요" }) : null,
      );
      const metaNode = proj.openable !== false
        ? Data.el("div", { className: "proj-card-meta" },
          Data.el("span", { text: `영역 ${proj.crops || 0}개` }),
          Data.el("span", { text: `후보 ${proj.candidates || 0}장` }),
          Data.el("span", { className: "proj-meta-star", text: `확정 ★ ${proj.stars || 0}` }),
        )
        : Data.el("span", { className: "proj-card-err", text: proj.error || "열 수 없음" });
      const archTime = proj.archived_at
        ? `보관: ${proj.archived_at}`
        : "보관됨";
      const footNode = Data.el("div", { className: "proj-card-foot" },
        Data.el("span", { className: "proj-card-export", text: archTime, title: archTime }),
        proj.modified ? Data.el("span", { className: "proj-card-time", text: Data.formatRelativeTime(proj.modified), title: `마지막 수정: ${proj.modified}` }) : null,
      );
      // #C31-8: 보관함 카드에는 [되돌리기] 만 두고 열기·핀·이름 변경은 두지 않는다
      const restoreBtn = Data.el("button", {
        type: "button",
        className: "btn-secondary-sm accent-outline proj-restore-btn",
        dataset: { projAction: "restore", projId: proj.id },
        disabled: !!homeState.metaBusyKey || !!homeState.openingKey,
        onClick: (e) => {
          e.stopPropagation();
          restoreProject(proj);
        },
      }, Data.icon("undo", 13), Data.el("span", { text: isBusy ? "되돌리는 중…" : "되돌리기" }));

      const cardClasses = [
        "proj-card",
        "archived",
        isList ? "proj-row" : "",
        isBusy ? "meta-busy" : "",
      ].filter(Boolean).join(" ");

      const cardChildren = isList
        ? [
          thumbWrap,
          Data.el("div", { className: "proj-card-body" },
            Data.el("div", { className: "proj-row-head" },
              titleRow,
              Data.el("span", { className: "proj-status-badge tone-warn", text: "보관됨" }),
            ),
            metaNode,
          ),
          Data.el("div", { className: "proj-row-right" }, footNode, restoreBtn),
        ]
        : [
          thumbWrap,
          Data.el("div", { className: "proj-card-body" },
            titleRow,
            metaNode,
            footNode,
            Data.el("div", { className: "proj-card-actions" }, restoreBtn),
          ),
        ];

      grid.append(Data.el("div", {
        className: cardClasses,
        dataset: { projId: proj.id, archived: "1" },
      }, ...cardChildren));
    }
  }

  function drawProjectsHome() {
    const grid = $("projects-grid");
    if (!grid) return;
    const canHome = canManageProjectHome();
    if (!canHome && homeState.tab === "archived") homeState.tab = "active";
    const list = Array.isArray(Store.state.projects) ? Store.state.projects : [];
    const hp = Store.getHomePrefs();
    const pinnedSet = new Set(hp.pinnedIds || []);

    syncHomeArchiveChrome();

    // 히어로(최근 작업 이어하기 + 빠른 드롭존) 갱신
    renderHomeHero(list, pinnedSet);

    const sortSel = $("home-sort-select");
    if (sortSel && sortSel.value !== hp.sortBy) sortSel.value = hp.sortBy;
    const btnGrid = $("home-view-grid");
    const btnList = $("home-view-list");
    if (btnGrid) btnGrid.classList.toggle("on", hp.viewMode !== "list");
    if (btnList) btnList.classList.toggle("on", hp.viewMode === "list");

    if (canHome && homeState.tab === "archived") {
      drawArchivedProjectsView(grid, hp);
      return;
    }

    // 툴바 상태 동기화
    const searchInp = $("home-search-input");
    const clearBtn = $("btn-home-search-clear");
    if (searchInp && searchInp.value !== homeState.query) searchInp.value = homeState.query;
    if (clearBtn) clearBtn.classList.toggle("hidden", !homeState.query);
    const prefsHintEl = $("home-prefs-hint");
    if (prefsHintEl) prefsHintEl.textContent = "고정·정렬·보기 설정은 이 환경의 화면 설정에 저장됩니다";

    const pinnedCount = list.filter((p) => pinnedSet.has(p.id)).length;
    const workingCount = list.filter((p) => p.openable !== false && p.export_state !== "same").length;
    const exportedCount = list.filter((p) => p.openable !== false && p.export_state === "same").length;

    const filterBar = $("home-filter-bar");
    if (filterBar) {
      filterBar.classList.remove("hidden");
      filterBar.innerHTML = "";
      const filters = [
        { id: "all", label: "전체", count: list.length },
        { id: "pinned", label: "고정됨", count: pinnedCount },
        { id: "working", label: "작업 중", count: workingCount },
        { id: "exported", label: "Export 완료", count: exportedCount },
      ];
      for (const f of filters) {
        const active = homeState.filter === f.id;
        filterBar.append(Data.el("button", {
          type: "button",
          className: "home-filter-chip" + (active ? " on" : ""),
          role: "tab",
          "aria-selected": active ? "true" : "false",
          dataset: { filter: f.id },
          onClick: () => {
            homeState.filter = f.id;
            drawProjectsHome();
          },
        },
          Data.el("span", { text: f.label }),
          Data.el("span", { className: "home-filter-count", text: String(f.count) }),
        ));
      }
    }

    // 검색 · 필터 · 정렬(고정 프로젝트 상단 우선)
    const q = homeState.query.trim().toLocaleLowerCase();
    let shown = list.filter((p) => {
      if (homeState.filter === "pinned" && !pinnedSet.has(p.id)) return false;
      if (homeState.filter === "working" && !(p.openable !== false && p.export_state !== "same")) return false;
      if (homeState.filter === "exported" && !(p.openable !== false && p.export_state === "same")) return false;
      if (q) {
        const hayTitle = String(p.title || "").toLocaleLowerCase();
        const hayName = String(p.name || "").toLocaleLowerCase();
        if (!hayTitle.includes(q) && !hayName.includes(q)) return false;
      }
      return true;
    });

    shown.sort((a, b) => {
      const pinDiff = (pinnedSet.has(b.id) ? 1 : 0) - (pinnedSet.has(a.id) ? 1 : 0);
      if (pinDiff !== 0) return pinDiff;
      if (hp.sortBy === "name") {
        const cmp = Data.projectDisplayName(a).localeCompare(Data.projectDisplayName(b), "ko");
        if (cmp !== 0) return cmp;
      } else if (hp.sortBy === "stars") {
        const d = (b.stars || 0) - (a.stars || 0);
        if (d !== 0) return d;
      } else if (hp.sortBy === "crops") {
        const d = (b.crops || 0) - (a.crops || 0);
        if (d !== 0) return d;
      }
      const modCmp = String(b.modified || "").localeCompare(String(a.modified || ""));
      if (modCmp !== 0) return modCmp;
      return Data.projectDisplayName(a).localeCompare(Data.projectDisplayName(b), "ko");
    });

    const countEl = $("home-result-count");
    if (countEl) {
      if (homeState.loading) {
        countEl.textContent = "프로젝트 목록을 불러오는 중…";
      } else if (homeState.loadError) {
        countEl.textContent = "목록 조회 오류";
      } else if (q || homeState.filter !== "all") {
        countEl.textContent = `표시 ${shown.length}개 / 전체 ${list.length}개`;
      } else {
        countEl.textContent = pinnedCount > 0
          ? `총 ${list.length}개 프로젝트 · 고정 ${pinnedCount}개`
          : `총 ${list.length}개 프로젝트`;
      }
    }

    const isEmptyHome = !list.length && !homeState.loading && !homeState.loadError;
    const toolbarEl = $("home-toolbar");
    const metaEl = $("home-section-meta");
    if (toolbarEl) toolbarEl.classList.toggle("hidden", isEmptyHome);
    if (metaEl) metaEl.classList.toggle("hidden", isEmptyHome);

    const isList = hp.viewMode === "list";
    grid.classList.toggle("view-list", isList && !isEmptyHome);
    grid.innerHTML = "";

    if (homeState.loadError) {
      grid.append(Data.el("div", { className: "empty-card home-empty-card" },
        Data.el("p", { text: `프로젝트 목록을 불러오지 못했습니다: ${homeState.loadError}` }),
        Data.el("div", { className: "empty-actions" },
          Data.el("button", { type: "button", className: "btn-secondary-sm", text: "다시 시도", onClick: () => renderProjectsHome() }),
        ),
      ));
      return;
    }

    if (!list.length) {
      if (homeState.loading) return;
      if (canHome && Store.state.archivedCount > 0) {
        grid.append(Data.el("div", { className: "empty-card home-empty-card" },
          Data.el("p", { text: `현재 활성 프로젝트가 없습니다 (보관함에 ${Store.state.archivedCount}개)` }),
          Data.el("small", { text: "보관된 프로젝트는 파일이 그대로 유지되어 있습니다. 보관함에서 언제든 되돌릴 수 있습니다." }),
          Data.el("div", { className: "empty-actions" },
            Data.el("button", { type: "button", className: "btn-primary-sm", text: `보관함 열기 (${Store.state.archivedCount})`, onClick: () => openArchivedTab() }),
          ),
        ));
      }
      const steps = [
        ["1", "원화 이미지로 시작", "위 영역에 PNG·JPG·PSD를 끌어 놓거나 PC·Forge 자료함에서 불러오면 새 프로젝트가 열립니다."],
        ["2", "수정 영역과 지시 작성", "원화 위에 수정할 영역을 그리고 한국어로 방향을 적으면 AI가 모델 맞춤 프롬프트를 작성합니다."],
        ["3", "후보 비교 · PSD 내보내기", "생성된 후보를 원본과 비교·채택하고 경계를 다듬은 뒤 크롭 밖 무변형 PSD로 내려받습니다."],
      ];
      const strip = Data.el("div", { className: "home-onboard-steps" });
      for (const [num, title, desc] of steps) {
        strip.append(Data.el("div", { className: "home-onboard-step" },
          Data.el("span", { className: "home-onboard-num", text: num }),
          Data.el("strong", { text: title }),
          Data.el("span", { text: desc }),
        ));
      }
      grid.append(strip);
      return;
    }

    if (!shown.length) {
      grid.append(Data.el("div", { className: "empty-card home-empty-card" },
        Data.el("p", { text: "조건에 맞는 프로젝트가 없습니다" }),
        Data.el("small", { text: q ? `"${homeState.query.trim()}" 검색어 또는 선택한 상태 필터에 해당하는 프로젝트가 없습니다.` : "선택한 상태 필터에 해당하는 프로젝트가 없습니다." }),
        Data.el("div", { className: "empty-actions" },
          Data.el("button", {
            type: "button",
            className: "btn-secondary-sm",
            text: "검색 · 필터 초기화",
            onClick: () => {
              homeState.query = "";
              homeState.filter = "all";
              drawProjectsHome();
            },
          }),
        ),
      ));
      return;
    }

    for (const proj of shown) {
      const openable = proj.openable !== false;
      const dispName = Data.projectDisplayName(proj);
      const hasCustomTitle = !!(proj.title && proj.name && proj.title !== proj.name);
      const st = classifyProjectStatus(proj);
      const isPinned = pinnedSet.has(proj.id);
      const isOpening = homeState.openingKey === proj.id;
      const isMetaBusy = homeState.metaBusyKey === proj.id;

      const thumbWrap = buildThumbNode(proj, dispName);
      const pinBtn = Data.el("button", {
        type: "button",
        className: "proj-pin-btn" + (isPinned ? " pinned" : ""),
        title: isPinned ? "상단 고정 해제" : "프로젝트 상단 고정",
        "aria-label": isPinned ? `${dispName} 상단 고정 해제` : `${dispName} 상단 고정`,
        onClick: (e) => {
          e.stopPropagation();
          const pinned = Store.togglePinnedProject(proj.id);
          Store.toast(pinned ? `"${dispName}" 프로젝트를 상단에 고정했습니다.` : `"${dispName}" 고정을 해제했습니다.`);
          drawProjectsHome();
        },
      }, Data.icon("pin", 13));

      if (!isList) {
        thumbWrap.append(
          Data.el("span", { className: `proj-status-badge proj-thumb-badge tone-${st.tone}`, text: st.label }),
          pinBtn,
        );
      }

      const titleRow = Data.el("div", { className: "proj-title-row" },
        Data.el("strong", { text: dispName, title: hasCustomTitle ? `${dispName} (폴더: ${proj.name})` : dispName }),
        hasCustomTitle ? Data.el("span", { className: "proj-folder-sub", text: proj.name, title: `폴더 이름: ${proj.name}` }) : null,
        canHome && proj.meta_error ? Data.el("span", { className: "proj-status-badge tone-err proj-meta-err-badge", text: "설정 파일 오류", title: "프로젝트 설정 파일(project_meta.json)을 읽지 못했습니다 — 관리자에게 문의해 주세요" }) : null,
      );

      const metaNode = openable
        ? Data.el("div", { className: "proj-card-meta" },
          Data.el("span", { text: `영역 ${proj.crops || 0}개` }),
          Data.el("span", { text: `후보 ${proj.candidates || 0}장` }),
          Data.el("span", { className: "proj-meta-star", text: `확정 ★ ${proj.stars || 0}` }),
        )
        : Data.el("span", { className: "proj-card-err", text: proj.error || "열 수 없음" });

      const footNode = Data.el("div", { className: "proj-card-foot" },
        Data.el("span", { className: "proj-card-export", text: formatExportMeta(proj) }),
        proj.modified ? Data.el("span", { className: "proj-card-time", text: Data.formatRelativeTime(proj.modified), title: `마지막 수정: ${proj.modified}` }) : null,
      );

      // #C31: caps.v2_project_home === true 일 때만 이름 변경·보관 버튼을 동적 생성한다(OFF 시 DOM 0건)
      const manageActions = (canHome && openable)
        ? Data.el("div", { className: "proj-card-actions" },
          Data.el("button", {
            type: "button",
            className: "mini-btn proj-rename-btn",
            dataset: { projAction: "rename", projId: proj.id },
            title: "표시 이름 변경 (폴더·키는 그대로 유지)",
            "aria-label": `${dispName} 이름 변경`,
            disabled: !!homeState.openingKey || !!homeState.metaBusyKey,
            onClick: (e) => {
              e.stopPropagation();
              openRenameProjectModal(proj);
            },
          }, Data.icon("edit", 12), Data.el("span", { text: "이름 변경" })),
          Data.el("button", {
            type: "button",
            className: "mini-btn proj-archive-btn",
            dataset: { projAction: "archive", projId: proj.id },
            title: "목록에서 숨기기 (파일·용량은 유지되며 보관함에서 되돌릴 수 있습니다)",
            "aria-label": `${dispName} 보관`,
            disabled: !!homeState.openingKey || !!homeState.metaBusyKey,
            onClick: (e) => {
              e.stopPropagation();
              archiveProject(proj);
            },
          }, Data.icon("archive", 12), Data.el("span", { text: isMetaBusy ? "보관 중…" : "보관" })),
        )
        : null;

      const cardClasses = [
        "proj-card",
        openable ? "" : "disabled",
        isPinned ? "pinned" : "",
        isList ? "proj-row" : "",
        isOpening ? "opening" : "",
        isMetaBusy ? "meta-busy" : "",
      ].filter(Boolean).join(" ");

      const cardChildren = isList
        ? [
          thumbWrap,
          Data.el("div", { className: "proj-card-body" },
            Data.el("div", { className: "proj-row-head" },
              titleRow,
              Data.el("span", { className: `proj-status-badge tone-${st.tone}`, text: st.label }),
            ),
            metaNode,
          ),
          Data.el("div", { className: "proj-row-right" },
            footNode,
            pinBtn,
            manageActions,
            Data.el("button", {
              type: "button",
              className: "btn-secondary-sm proj-row-open",
              disabled: !openable || !!homeState.openingKey || !!homeState.metaBusyKey,
              text: isOpening ? "여는 중…" : "열기",
              onClick: (e) => {
                e.stopPropagation();
                openProjectByKey(proj);
              },
            }),
          ),
        ]
        : [
          thumbWrap,
          Data.el("div", { className: "proj-card-body" }, titleRow, metaNode, footNode, manageActions),
        ];

      grid.append(Data.el("div", {
        className: cardClasses,
        dataset: { projId: proj.id },
        onClick: () => openProjectByKey(proj),
      }, ...cardChildren));
    }
  }

  async function renderProjectsHome() {
    const grid = $("projects-grid");
    if (!grid) return;
    homeState.loading = true;
    homeState.loadError = "";
    const refreshBtn = $("btn-home-refresh");
    if (refreshBtn) refreshBtn.disabled = true;
    const countEl = $("home-result-count");
    if (countEl) countEl.textContent = "프로젝트 목록을 불러오는 중…";
    try {
      const res = await Api.listProjects();
      const list = Array.isArray(res && res.projects) ? res.projects : [];
      Store.state.projects = list;
      if (canManageProjectHome() && typeof (res && res.archived_count) === "number" && Number.isFinite(res.archived_count)) {
        Store.state.archivedCount = Math.max(0, res.archived_count);
      } else {
        Store.state.archivedCount = 0;
      }
    } catch (err) {
      homeState.loadError = (err && err.message) || "알 수 없는 오류";
    } finally {
      homeState.loading = false;
      if (refreshBtn) refreshBtn.disabled = false;
      drawProjectsHome();
    }
  }

  function openNewProjectModal(initialFile = null, initialRefs = [], opts = {}) {
    let targetFile = initialFile || null;
    const refFiles = Array.isArray(initialRefs) ? initialRefs.slice() : [];
    const failedRefs = [];
    const uploadedSheetPaths = new Set();
    let creating = false;
    let createdProject = null; // { key, name, source, assetName, notes } — 생성이 끝난 뒤 레퍼런스만 재시도할 때 재사용
    let navigating = false;
    const blobUrls = [];
    const targetAccept = Data.TARGET_ACCEPT || ".psd,.psb,.png,.jpg,.jpeg,.webp";

    function normalizeNotes(raw) {
      return Array.isArray(raw) ? raw.filter((n) => typeof n === "string" && n.trim()) : [];
    }

    function persistNewProjectNotes(key, notes, writeEmpty = false) {
      if (!key) return;
      const cleanNotes = normalizeNotes(notes);
      if (!cleanNotes.length && !writeEmpty) return;
      // #C36-6 · #C40-5: 생성 직후 sessionStorage("bmk-dp-new:" + key) 에 기록해 일부 실패 후 [닫기] 뒤 목록에서 열어도 변환 알림이 살아남게 한다
      try {
        window.sessionStorage.setItem("bmk-dp-new:" + key, JSON.stringify(cleanNotes));
      } catch (_) { /* 저장소 차단 무시 */ }
    }

    function normSheetBase(pathStr) {
      if (typeof pathStr !== "string" || !pathStr.startsWith("sheets/")) return "";
      return pathStr.slice("sheets/".length).normalize("NFC").trim().toLowerCase();
    }

    function normFileName(file) {
      if (!file || typeof file.name !== "string") return "";
      return file.name.normalize("NFC").trim().toLowerCase();
    }

    function sameByteSize(sheetEntry, file) {
      const fSize = Number(file && file.size);
      const sBytes = Number(sheetEntry && sheetEntry.bytes);
      return Number.isFinite(fSize) && fSize > 0 && Number.isFinite(sBytes) && sBytes === fSize;
    }

    function sheetExactMatchesFile(sheetEntry, file) {
      const base = normSheetBase(sheetEntry && sheetEntry.path);
      const fname = normFileName(file);
      return !!(base && fname && base === fname && sameByteSize(sheetEntry, file));
    }

    function sheetSuffixMatchesFile(sheetEntry, file) {
      const base = normSheetBase(sheetEntry && sheetEntry.path);
      const fname = normFileName(file);
      if (!base || !fname || !sameByteSize(sheetEntry, file)) return false;
      const dot = fname.lastIndexOf(".");
      if (dot <= 0) return false;
      const stem = fname.slice(0, dot);
      const ext = fname.slice(dot);
      if (!base.startsWith(stem + "_") || !base.endsWith(ext)) return false;
      const suffix = base.slice(stem.length + 1, base.length - ext.length);
      return /^[2-9]\d*$|^1\d+$/.test(suffix);
    }

    function revokeBlobUrls() {
      while (blobUrls.length) {
        const u = blobUrls.pop();
        try { URL.revokeObjectURL(u); } catch (_) {}
      }
    }

    const nameInput = Data.el("input", {
      id: "np-name-input",
      type: "text",
      className: "np-text-input",
      placeholder: "예: 캐릭터_의상수정_01",
      value: targetFile ? Data.suggestProjectName(targetFile.name) : "",
    });
    const errEl = Data.el("small", { id: "np-error", className: "np-error-text" });
    const refFailBox = Data.el("div", { id: "np-ref-failures", className: "np-ref-failures hidden", role: "alert" });
    const pickedBox = Data.el("div", { id: "np-picked-box", className: "np-picked-box" });
    const dropLabel = Data.el("span", {
      id: "np-drop-label",
      text: "원화 이미지를 여기에 끌어다 놓거나 파일을 선택하세요",
    });

    // #C36-5: projects/new 서버 허용 확장자(.psd,.psb,.png,.jpg,.jpeg,.webp)와 일치시킨다
    const fileInput = Data.el("input", {
      id: "np-file-input",
      type: "file",
      accept: targetAccept,
      className: "hidden",
    });
    fileInput.addEventListener("change", () => {
      if (creating || createdProject) { fileInput.value = ""; return; }
      if (fileInput.files && fileInput.files[0]) {
        targetFile = fileInput.files[0];
        if (!nameInput.value.trim()) nameInput.value = Data.suggestProjectName(targetFile.name);
        const fErr = typeof Data.validateTargetFile === "function" ? Data.validateTargetFile(targetFile) : "";
        setErr(fErr, false);
        refreshPicked();
      }
      fileInput.value = "";
    });

    const refInput = Data.el("input", {
      id: "np-ref-input",
      type: "file",
      accept: "image/*",
      multiple: true,
      className: "hidden",
    });
    refInput.addEventListener("change", () => {
      if (creating) { refInput.value = ""; return; }
      for (const f of Array.from(refInput.files || [])) refFiles.push(f);
      refreshPicked();
      syncModalControls();
      refInput.value = "";
    });

    const btnPickTarget = Data.el("button", {
      type: "button",
      className: "btn-secondary-sm",
      id: "btn-np-pick-target",
      text: "내 PC에서 원화 선택",
      onClick: () => { if (!creating && !createdProject) fileInput.click(); },
    });

    const btnPickRefs = Data.el("button", {
      type: "button",
      className: "btn-secondary-sm",
      id: "btn-np-pick-refs",
      text: "+ 레퍼런스 함께 추가",
      onClick: () => { if (!creating) refInput.click(); },
    });

    let btnForgeImport = null;

    function setErr(msg, isHint = false) {
      errEl.textContent = msg || "";
      errEl.classList.toggle("hint", !!isHint && !!msg);
    }

    function renderRefFailures() {
      refFailBox.innerHTML = "";
      if (!failedRefs.length) {
        refFailBox.classList.add("hidden");
        return;
      }
      refFailBox.classList.remove("hidden");
      refFailBox.append(Data.el("strong", {
        text: `레퍼런스 ${failedRefs.length}건을 올리지 못했습니다 — 실패한 파일만 다시 올리거나 이대로 프로젝트를 열 수 있습니다`,
      }));
      const ul = Data.el("ul");
      for (const item of failedRefs) {
        ul.append(Data.el("li", {},
          Data.el("span", { className: "np-fail-name", text: item.name }),
          Data.el("span", { text: ` — ${item.error}` }),
        ));
      }
      refFailBox.append(ul);
    }

    function syncModalControls(busyText = "") {
      const lockTarget = creating || !!createdProject;
      nameInput.disabled = lockTarget;
      btnPickTarget.disabled = lockTarget;
      if (btnForgeImport) btnForgeImport.disabled = lockTarget;
      btnPickRefs.disabled = creating;
      // #C40-3: 프로젝트 생성 완료 후에는 드롭존 안내 문구도 레퍼런스 추가 동작에 맞춰 바꾼다
      dropLabel.textContent = createdProject
        ? "이 프로젝트에 추가할 레퍼런스를 여기에 끌어다 놓거나 선택하세요"
        : "원화 이미지를 여기에 끌어다 놓거나 파일을 선택하세요";
      const mClose = $("modal-close");
      if (mClose) mClose.disabled = creating;
      const cancelBtn = $("np-cancel-btn");
      if (cancelBtn) {
        cancelBtn.disabled = creating;
        cancelBtn.textContent = createdProject ? "닫기" : "취소";
      }
      const openAnywayBtn = $("np-open-anyway-btn");
      if (openAnywayBtn) {
        openAnywayBtn.classList.toggle("hidden", !createdProject);
        openAnywayBtn.disabled = creating;
      }
      const createBtn = $("np-create-btn");
      if (createBtn) {
        createBtn.disabled = creating;
        if (creating) createBtn.textContent = busyText || "프로젝트 생성 중…";
        else if (createdProject) {
          if (!refFiles.length) createBtn.textContent = "프로젝트 열기";
          else createBtn.textContent = failedRefs.length ? "실패한 레퍼런스 다시 올리기" : "레퍼런스 올리고 열기";
        } else createBtn.textContent = "프로젝트 생성 및 열기";
      }
      pickedBox.querySelectorAll("button").forEach((b) => { b.disabled = creating; });
    }

    function setCreating(flag, busyText = "") {
      creating = !!flag;
      syncModalControls(busyText);
    }

    function navigateToNewProject(key, notes) {
      navigating = true;
      // #C36-6 · #C40-5: v1(board.js)과 동일한 sessionStorage 키("bmk-dp-new:" + key) + new=1 로 변환 알림 전달
      persistNewProjectNotes(key, notes, true);
      closeModal(true);
      const u = new URL(location.href);
      u.searchParams.set("root", key);
      u.searchParams.set("new", "1");
      u.searchParams.set("ui", "v2");
      location.href = u.pathname + u.search;
    }

    function refreshPicked() {
      revokeBlobUrls();
      pickedBox.innerHTML = "";
      if (targetFile) {
        const isImg = /^image\/(png|jpe?g|webp)$/i.test(targetFile.type || "")
          || /\.(png|jpe?g|webp)$/i.test(targetFile.name || "");
        let thumbNode = null;
        if (isImg) {
          const url = URL.createObjectURL(targetFile);
          blobUrls.push(url);
          thumbNode = Data.el("img", { src: url, alt: "" });
        }
        pickedBox.append(Data.el("span", { className: "np-picked-chip" },
          thumbNode,
          Data.el("span", { text: createdProject ? `생성됨 · 타깃: ${targetFile.name}` : `타깃: ${targetFile.name}` }),
          createdProject ? null : Data.el("button", {
            type: "button",
            className: "row-icon-btn",
            text: "×",
            title: "타깃 파일 해제",
            disabled: creating,
            onClick: () => { if (!creating && !createdProject) { targetFile = null; setErr(""); refreshPicked(); } },
          }),
        ));
      } else if (createdProject) {
        const forgeLabel = createdProject.assetName
          ? `생성됨 · Forge 원화: ${createdProject.assetName}`
          : "생성됨 · Forge 원화";
        pickedBox.append(Data.el("span", { className: "np-picked-chip" },
          Data.el("span", { text: forgeLabel }),
        ));
      }
      refFiles.forEach((rf, i) => {
        const isFailed = failedRefs.some((f) => f.file === rf);
        pickedBox.append(Data.el("span", { className: "np-picked-chip ref" + (isFailed ? " ref-failed" : "") },
          Data.el("span", { text: `참조: ${rf.name}` }),
          Data.el("button", {
            type: "button",
            className: "row-icon-btn",
            text: "×",
            title: "레퍼런스 해제",
            disabled: creating,
            onClick: () => {
              if (creating) return;
              const removed = refFiles.splice(i, 1)[0];
              for (let k = failedRefs.length - 1; k >= 0; k -= 1) {
                if (failedRefs[k].file === removed) failedRefs.splice(k, 1);
              }
              if (createdProject && !failedRefs.length) setErr("");
              refreshPicked();
              renderRefFailures();
              syncModalControls();
            },
          }),
        ));
      });
    }

    // #C36-1 · #C40-4: 로컬 원화 생성 및 Forge 원화 생성 공통 — 레퍼런스 업로드 실패를 삼키지 않고 실패 파일명·서버 error 표시 + 재시도 전 이미 올라간 시트 대조
    async function finishAfterProjectCreated({ checkExisting = false } = {}) {
      if (!createdProject) return;
      const key = createdProject.key;
      const pending = refFiles.slice();
      const previouslyFailedFiles = new Set(failedRefs.map((f) => f.file));
      const matchedAlreadyUploaded = new Set();
      let listSheetsFailed = false;
      if (checkExisting && pending.length && previouslyFailedFiles.size && typeof Api.listSheets === "function") {
        try {
          const shRes = await Api.listSheets(key);
          const list = Array.isArray(shRes) ? shRes : (shRes && Array.isArray(shRes.sheets) ? shRes.sheets : []);
          const serverSheets = list.filter((s) => s && typeof s.path === "string" && s.path.startsWith("sheets/"));
          const allSheetNames = new Set(serverSheets.map((s) => normSheetBase(s.path)).filter(Boolean));
          const unaccountedSheets = serverSheets.filter((s) => !uploadedSheetPaths.has(s.path));
          const pendingExactNames = new Set(pending.map((rf) => normFileName(rf)).filter(Boolean));

          // 1단계: 정확한 파일명(NFC·소문자·바이트 일치) 우선 1:1 소비 (a.png 가 a_2.png 를 가로채지 않도록 보호)
          for (const rf of pending) {
            if (!previouslyFailedFiles.has(rf) || !unaccountedSheets.length) continue;
            const exactIdx = unaccountedSheets.findIndex((s) => sheetExactMatchesFile(s, rf));
            if (exactIdx >= 0) {
              const matched = unaccountedSheets.splice(exactIdx, 1)[0];
              uploadedSheetPaths.add(matched.path);
              matchedAlreadyUploaded.add(rf);
            }
          }
          // 2단계: 서버에 동일 기본 파일명(sheets/<fname>)이 이미 존재하고 다른 대기 파일의 정확명과 겹치지 않는 경우에 한해 _2/_3 접미사 매칭
          for (const rf of pending) {
            if (!previouslyFailedFiles.has(rf) || matchedAlreadyUploaded.has(rf) || !unaccountedSheets.length) continue;
            const fname = normFileName(rf);
            if (!fname || !allSheetNames.has(fname)) continue;
            const sufIdx = unaccountedSheets.findIndex((s) => {
              const base = normSheetBase(s.path);
              return !pendingExactNames.has(base) && sheetSuffixMatchesFile(s, rf);
            });
            if (sufIdx >= 0) {
              const matched = unaccountedSheets.splice(sufIdx, 1)[0];
              uploadedSheetPaths.add(matched.path);
              matchedAlreadyUploaded.add(rf);
            }
          }
        } catch (_) {
          // 목록 조회 자체에 실패하면 이미 저장됐을 수 있는 실패 건을 맹목 재전송하지 않고 보류한다
          listSheetsFailed = true;
        }
      }
      const stillFailed = [];
      const failures = [];
      for (const rf of pending) {
        if (matchedAlreadyUploaded.has(rf)) continue;
        if (listSheetsFailed && previouslyFailedFiles.has(rf)) {
          stillFailed.push(rf);
          failures.push({
            file: rf,
            name: (rf && rf.name) || "레퍼런스 파일",
            error: "이미 올라간 시트 목록을 확인하지 못해 중복 방지를 위해 재전송을 보류했습니다 — 잠시 후 다시 시도해 주세요.",
          });
          continue;
        }
        try {
          const upRes = await Api.uploadSheet(key, rf, null);
          if (upRes && typeof upRes.sheet === "string") uploadedSheetPaths.add(upRes.sheet);
        } catch (err) {
          stillFailed.push(rf);
          failures.push({
            file: rf,
            name: (rf && rf.name) || "레퍼런스 파일",
            error: (err && err.message) || "레퍼런스 업로드에 실패했습니다.",
          });
        }
      }
      refFiles.splice(0, refFiles.length, ...stillFailed);
      failedRefs.splice(0, failedRefs.length, ...failures);

      if (!failures.length) {
        setCreating(false);
        navigateToNewProject(key, createdProject.notes);
        return;
      }

      setCreating(false);
      refreshPicked();
      renderRefFailures();
      syncModalControls();
      setErr(`프로젝트는 생성되었지만 레퍼런스 ${failures.length}건을 올리지 못했습니다.`, false);
    }

    async function retryFailedRefs() {
      if (creating || !createdProject) return;
      if (!refFiles.length) {
        navigateToNewProject(createdProject.key, createdProject.notes);
        return;
      }
      setCreating(true, failedRefs.length ? "레퍼런스 다시 올리는 중…" : "레퍼런스 올리는 중…");
      setErr("");
      await finishAfterProjectCreated({ checkExisting: true });
    }

    async function submitNewProject() {
      if (creating) return;
      if (createdProject) {
        await retryFailedRefs();
        return;
      }
      const name = nameInput.value.trim();
      const vErr = Data.validateProjectName(name);
      if (vErr) {
        setErr(vErr, false);
        nameInput.focus();
        return;
      }
      const fErr = typeof Data.validateTargetFile === "function"
        ? Data.validateTargetFile(targetFile)
        : (!targetFile ? "타깃 원화 이미지를 먼저 선택하거나 끌어 놓아 주세요." : "");
      if (fErr) {
        setErr(fErr, false);
        return;
      }
      setCreating(true, "프로젝트 생성 중…");
      setErr("");
      try {
        const res = await Api.createProjectFile(name, targetFile);
        const projNotes = normalizeNotes(res.notes);
        createdProject = {
          key: res.key,
          name: (res && (res.title || res.name)) || name,
          source: "file",
          assetName: "",
          notes: projNotes,
        };
        persistNewProjectNotes(createdProject.key, projNotes);
        if (refFiles.length) syncModalControls("레퍼런스 올리는 중…");
        await finishAfterProjectCreated();
      } catch (err) {
        setCreating(false);
        // #C31-13: 409 archived_name / name_taken / 중복 폴더 등 서버 error 문구를 삼키지 않고 그대로 표시
        setErr((err && err.message) || "프로젝트 생성에 실패했습니다.", false);
      }
    }

    nameInput.addEventListener("input", () => {
      if (errEl.textContent) setErr("");
    });
    nameInput.addEventListener("keydown", (e) => {
      // #C36-3: Forge iframe 은 allow-forms 가 없으므로 폼 제출 없이 Enter 키로 실행하되 한글 IME 조합 중 Enter 는 무시한다
      if (e.key !== "Enter" || e.isComposing || e.keyCode === 229) return;
      e.preventDefault();
      submitNewProject();
    });

    if (Api.can("forge_import")) {
      btnForgeImport = Data.el("button", {
        type: "button",
        className: "btn-secondary-sm accent-outline",
        id: "btn-np-forge-import",
        text: "Forge에서 불러오기",
        onClick: () => {
          if (creating || createdProject) return;
          const name = nameInput.value.trim();
          const vErr = Data.validateProjectName(name);
          if (vErr) {
            setErr(vErr || "먼저 프로젝트 이름을 입력해 주세요.", false);
            nameInput.focus();
            return;
          }
          setErr("");
          if (window.DPForgeImport && typeof window.DPForgeImport.open === "function") {
            const opened = window.DPForgeImport.open({
              into: "target",
              name,
              onDone: async (result) => {
                if (!result || !/^[0-9a-f]{12}$/.test(result.key || "")) {
                  Store.toast((result && result.error) || "Forge에서 프로젝트를 만들지 못했습니다.", "err");
                  return;
                }
                // #C36-1 · #C36-6 · #C36-7 · #C40-5 · #C40-6a: Forge 원화 경로는 앞서 골랐던 PC targetFile 을 비우고 notes 를 즉시 보관한다
                const projNotes = normalizeNotes(result.notes);
                targetFile = null;
                createdProject = {
                  key: result.key,
                  name: (result && (result.title || result.name)) || name,
                  source: "forge",
                  assetName: (result && (result.asset_name || result.filename)) || "",
                  notes: projNotes,
                };
                persistNewProjectNotes(createdProject.key, projNotes);
                setErr("");
                refreshPicked();
                setCreating(true, refFiles.length ? "레퍼런스 올리는 중…" : "프로젝트 여는 중…");
                await finishAfterProjectCreated();
              },
            });
            if (!opened) Store.toast("Forge 자료함을 열 수 없습니다 — 연결 상태를 확인해 주세요.", "err");
          } else {
            Store.toast("Forge 가져오기 모듈이 연결되면 바로 선택할 수 있습니다.");
          }
        },
      });
    }

    const dropBox = Data.el("div", { className: "np-drop", id: "np-drop-zone" },
      dropLabel,
      Data.el("div", { style: "display:flex;gap:6px;flex-wrap:wrap;justify-content:center;" },
        btnPickTarget,
        btnForgeImport,
        btnPickRefs,
      ),
    );
    dropBox.addEventListener("dragover", (e) => { e.preventDefault(); e.stopPropagation(); if (!creating) dropBox.classList.add("over"); });
    dropBox.addEventListener("dragleave", () => dropBox.classList.remove("over"));
    dropBox.addEventListener("drop", (e) => {
      e.preventDefault();
      e.stopPropagation();
      dropBox.classList.remove("over");
      if (creating) return;
      const files = Array.from(e.dataTransfer?.files || []);
      if (!files.length) return;
      if (createdProject) {
        for (const extra of files) refFiles.push(extra);
        refreshPicked();
        syncModalControls();
        return;
      }
      targetFile = files[0];
      if (!nameInput.value.trim()) nameInput.value = Data.suggestProjectName(targetFile.name);
      for (const extra of files.slice(1)) refFiles.push(extra);
      const fErr = typeof Data.validateTargetFile === "function" ? Data.validateTargetFile(targetFile) : "";
      setErr(fErr, false);
      refreshPicked();
    });
    refreshPicked();

    if (targetFile && typeof Data.validateTargetFile === "function") {
      const initErr = Data.validateTargetFile(targetFile);
      if (initErr) setErr(initErr, false);
    } else if (opts && opts.hintForForge) {
      setErr("프로젝트 이름을 입력한 뒤 [Forge에서 불러오기]를 눌러 원화를 선택하세요.", true);
    }

    openModal({
      title: "새 프로젝트 만들기",
      bodyNodes: [
        Data.el("div", { className: "np-field" }, Data.el("label", { for: "np-name-input", text: "프로젝트 이름" }), nameInput, errEl),
        refFailBox,
        dropBox,
        pickedBox,
        fileInput,
        refInput,
      ],
      actions: [
        { id: "np-cancel-btn", label: "취소", cls: "btn-secondary", onClick: () => { if (!creating) closeModal(); } },
        {
          id: "np-open-anyway-btn",
          label: "이대로 프로젝트 열기",
          cls: "btn-secondary hidden",
          onClick: () => { if (!creating && createdProject) navigateToNewProject(createdProject.key, createdProject.notes); },
        },
        {
          id: "np-create-btn",
          label: "프로젝트 생성 및 열기",
          cls: "btn-primary",
          onClick: submitNewProject,
        },
      ],
      onClose: () => {
        // #C40-1: 생성/업로드 진행 중에는 비정상 경로로 onClose 가 호출되어도 대기 중 레퍼런스 목록을 비우지 않는다
        if (creating) return;
        // #C36-7: 모달을 닫을 때 Blob URL 및 선택한 File 참조를 전부 비운다
        revokeBlobUrls();
        targetFile = null;
        refFiles.length = 0;
        failedRefs.length = 0;
        fileInput.value = "";
        refInput.value = "";
        if (createdProject && !navigating) {
          // #C40-5: 이미 생성된 프로젝트를 열지 않고 닫았을 때 안내 토스트를 남기고 홈 목록을 갱신한다
          Store.toast(`"${createdProject.name || createdProject.key}" 프로젝트가 생성되었습니다 — 프로젝트 목록에서 열어 작업을 이어갈 수 있습니다.`);
          if (!Store.state.root) renderProjectsHome();
        }
      },
      canClose: () => !creating,
    });

    if (!navigator.userActivation || navigator.userActivation.isActive) {
      nameInput.focus();
    }
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
          Store.invalidateAllSpecs();
          const refSync = typeof Store.refreshAfterRefWrite === "function"
            ? Store.refreshAfterRefWrite()
            : Store.refreshAll(true);
          Store.toast("Forge 자료함에서 레퍼런스를 가져왔습니다.", "ok");
          await refSync;
        },
      });
      if (!opened) Store.toast("Forge 자료함을 열 수 없습니다.", "err");
    } else {
      Store.toast("Forge 자료함 연동이 활성화되면 여기에서 바로 불러올 수 있습니다.");
    }
  }

  // ==================== 이벤트 바인딩 ====================
  function bindEvents() {
    // 모달 닫기 (#C36-2: PointerEvent 가 closeModal(force) 에 들어가지 않도록 래핑)
    const mClose = $("modal-close");
    if (mClose) mClose.addEventListener("click", () => closeModal());
    const mBack = $("modal-backdrop");
    if (mBack) mBack.addEventListener("click", (e) => { if (e.target === mBack) closeModal(); });

    // 히스토리 패널 아이콘 · 도킹/분리 · 드래그 이동 · 자석 스냅 Attach · 리사이즈
    const histIcons = [
      ["history-grip-icon", "grip", 13],
      ["btn-hist-dock-left", "dockLeft", 13],
      ["btn-hist-dock-right", "dockRight", 13],
      ["btn-hist-float", "floatWin", 13],
      ["btn-close-history", "x", 13],
      ["sheet-back-icon", "arrowLeft", 14],
      ["sheet-close-icon", "x", 14],
    ];
    for (const [id, ico, sz] of histIcons) {
      const el = $(id);
      if (el) { el.innerHTML = ""; el.append(Data.icon(ico, sz)); }
    }

    const btnCloseHist = $("btn-close-history");
    if (btnCloseHist) btnCloseHist.addEventListener("click", toggleHistoryDrawer);
    const btnHistL = $("btn-hist-dock-left");
    if (btnHistL) btnHistL.addEventListener("click", () => {
      setHistoryPanelMode("dock-left");
      Store.toast("작업 히스토리를 좌측에 부착했습니다.");
    });
    const btnHistR = $("btn-hist-dock-right");
    if (btnHistR) btnHistR.addEventListener("click", () => {
      setHistoryPanelMode("dock-right");
      Store.toast("작업 히스토리를 우측에 부착했습니다.");
    });
    const btnHistF = $("btn-hist-float");
    if (btnHistF) btnHistF.addEventListener("click", () => {
      setHistoryPanelMode("floating");
      Store.toast("작업 히스토리를 플로팅 창으로 분리했습니다.");
    });

    const histHead = $("history-drag-head");
    const histDrawer = $("history-drawer");
    const dockPreview = $("history-dock-preview");
    if (histHead && histDrawer) {
      let hDrag = null;
      histHead.addEventListener("pointerdown", (e) => {
        if (e.button !== 0 || e.target.closest("button")) return;
        const rect = histDrawer.getBoundingClientRect();
        const hp = getHistoryPrefs();
        hDrag = {
          startMode: hp.mode,
          sx: e.clientX,
          sy: e.clientY,
          offX: e.clientX - rect.left,
          offY: e.clientY - rect.top,
          w: hp.w,
          h: hp.h,
          detached: hp.mode === "floating",
          snapZone: null,
          curX: rect.left,
          curY: rect.top,
        };
        try { histHead.setPointerCapture(e.pointerId); } catch (_) {}
        histDrawer.classList.add("dragging");
      });
      histHead.addEventListener("pointermove", (e) => {
        if (!hDrag) return;
        const dist = Math.hypot(e.clientX - hDrag.sx, e.clientY - hDrag.sy);
        if (!hDrag.detached) {
          if (dist < 12) return;
          hDrag.detached = true;
          hDrag.offX = Math.min(hDrag.w - 40, Math.max(24, hDrag.offX));
          hDrag.offY = Math.min(28, Math.max(10, hDrag.offY));
          histDrawer.dataset.mode = "floating";
          histDrawer.classList.remove("mode-dock-left", "mode-dock-right");
          histDrawer.classList.add("mode-floating");
          histDrawer.style.width = `${hDrag.w}px`;
          histDrawer.style.height = `${hDrag.h}px`;
        }
        const box = clampFloatingBox(e.clientX - hDrag.offX, e.clientY - hDrag.offY, hDrag.w, hDrag.h);
        hDrag.curX = box.x;
        hDrag.curY = box.y;
        histDrawer.style.left = `${box.x}px`;
        histDrawer.style.top = `${box.y}px`;

        // 좌/우 가장자리 48px 진입 시 자석 도킹 프리뷰
        let zone = null;
        if (e.clientX <= 48) zone = "dock-left";
        else if (e.clientX >= window.innerWidth - 48) zone = "dock-right";
        hDrag.snapZone = zone;
        if (dockPreview) {
          dockPreview.classList.toggle("hidden", !zone);
          dockPreview.classList.toggle("left", zone === "dock-left");
          dockPreview.classList.toggle("right", zone === "dock-right");
        }
      });
      const finishHistDrag = () => {
        if (!hDrag) return;
        const d = hDrag;
        hDrag = null;
        histDrawer.classList.remove("dragging");
        if (dockPreview) dockPreview.classList.add("hidden", "left", "right");
        if (!d.detached) return;
        if (d.snapZone) {
          // #C40-6b: 도킹/이동 시 다른 탭이 갱신한 w/h 를 옛 드래그 값으로 덮어쓰지 않고 변경된 축만 저장한다
          setHistoryPanelMode(d.snapZone);
          Store.toast(d.snapZone === "dock-left" ? "작업 히스토리를 좌측에 부착했습니다." : "작업 히스토리를 우측에 부착했습니다.");
        } else {
          setHistoryPanelMode("floating", { x: d.curX, y: d.curY });
        }
      };
      histHead.addEventListener("pointerup", finishHistDrag);
      histHead.addEventListener("pointercancel", finishHistDrag);
    }

    const histResize = $("history-resize-handle");
    if (histResize && histDrawer) {
      let rDrag = null;
      histResize.addEventListener("pointerdown", (e) => {
        if (e.button !== 0 || histDrawer.dataset.mode !== "floating") return;
        e.stopPropagation();
        const rect = histDrawer.getBoundingClientRect();
        rDrag = { sx: e.clientX, sy: e.clientY, x: rect.left, y: rect.top, w: rect.width, h: rect.height };
        try { histResize.setPointerCapture(e.pointerId); } catch (_) {}
      });
      histResize.addEventListener("pointermove", (e) => {
        if (!rDrag) return;
        const box = clampFloatingBox(rDrag.x, rDrag.y, rDrag.w + (e.clientX - rDrag.sx), rDrag.h + (e.clientY - rDrag.sy));
        rDrag.curBox = box;
        histDrawer.style.width = `${box.w}px`;
        histDrawer.style.height = `${box.h}px`;
      });
      const finishResize = () => {
        if (!rDrag) return;
        const box = rDrag.curBox;
        rDrag = null;
        if (box) setHistoryPanelMode("floating", box);
      };
      histResize.addEventListener("pointerup", finishResize);
      histResize.addEventListener("pointercancel", finishResize);
    }

    window.addEventListener("resize", () => {
      if (histDrawer && !histDrawer.classList.contains("hidden") && histDrawer.dataset.mode === "floating") {
        applyHistoryPanelLayout();
      }
    });

    // 시트 오리기 & 4점 원근 이벤트 (하위 뎁스 복귀 + 백드롭 닫기)
    const btnSheetBack = $("btn-sheet-back");
    if (btnSheetBack) btnSheetBack.addEventListener("click", closeSheetOverlay);
    const btnSheetClose = $("btn-sheet-close");
    if (btnSheetClose) btnSheetClose.addEventListener("click", closeSheetOverlay);
    const sheetOv = $("sheet-overlay");
    if (sheetOv) sheetOv.addEventListener("click", (e) => {
      if (e.target === sheetOv) closeSheetOverlay();
    });
    const shSel = $("sheet-select");
    if (shSel) shSel.addEventListener("change", (e) => loadSelectedSheet(e.target.value));
    const cropSel = $("sheet-target-crop");
    if (cropSel) cropSel.addEventListener("change", drawSheetWarp);
    const btnShUp = $("btn-sheet-upload");
    if (btnShUp) btnShUp.addEventListener("click", () => $("hidden-file-sheet").click());
    const btnShForge = $("btn-sheet-forge");
    if (btnShForge) btnShForge.addEventListener("click", openForgeImportForRef);

    // 빠른 선택: 전체 / 1:1 정사각
    const btnSelAll = $("btn-sheet-sel-all");
    if (btnSelAll) btnSelAll.addEventListener("click", () => {
      if (!sheetState.sheetImg) return;
      sheetState.cutRect = { x: 0, y: 0, w: 1, h: 1 };
      drawSheetSource();
      drawSheetWarp();
    });
    const btnSelSq = $("btn-sheet-sel-sq");
    if (btnSelSq) btnSelSq.addEventListener("click", () => {
      const img = sheetState.sheetImg;
      if (!img) return;
      const w = img.naturalWidth, h = img.naturalHeight;
      const r = sheetState.cutRect;
      const cx = (r.x + r.w / 2) * w, cy = (r.y + r.h / 2) * h;
      const side = Math.max(16, Math.min(w, h, Math.round((r.w * w + r.h * h) / 2)));
      const nx = Math.max(0, Math.min(w - side, Math.round(cx - side / 2)));
      const ny = Math.max(0, Math.min(h - side, Math.round(cy - side / 2)));
      sheetState.cutRect = { x: nx / w, y: ny / h, w: side / w, h: side / h };
      drawSheetSource();
      drawSheetWarp();
    });

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

    // 좌측 시트 뷰어: 휠 커서 중심 줌 + 오버스크롤 팬 + 8방향 핸들 리사이즈 + 내부 이동 + 바깥 새 영역 그리기
    const srcWrap = $("sheet-source-wrap");
    const srcCanvas = $("sheet-source-canvas");
    if (srcWrap && srcCanvas) {
      const clientToSheetPx = (clientX, clientY) => {
        const b = srcCanvas.getBoundingClientRect();
        const img = sheetState.sheetImg;
        if (!img || !b.width || !b.height) return null;
        return {
          x: ((clientX - b.left) / b.width) * img.naturalWidth,
          y: ((clientY - b.top) / b.height) * img.naturalHeight,
        };
      };

      const updateSheetCursor = (pxPt) => {
        if (Canvas.view.spaceDown) { srcWrap.style.cursor = "grab"; return; }
        if (!pxPt) { srcWrap.style.cursor = "crosshair"; return; }
        const hit = sheetHitTest(pxPt.x, pxPt.y);
        const prev = sheetState.hoverHit;
        const changed = (prev?.kind !== hit?.kind) || (prev?.handle !== hit?.handle);
        sheetState.hoverHit = hit;
        if (!hit) srcWrap.style.cursor = "crosshair";
        else if (hit.kind === "handle") srcWrap.style.cursor = SHEET_HANDLE_CURSORS[hit.handle] || "pointer";
        else srcWrap.style.cursor = "move";
        if (changed) drawSheetSource();
      };

      srcWrap.addEventListener("contextmenu", (e) => e.preventDefault());
      srcWrap.addEventListener("wheel", (e) => {
        e.preventDefault();
        const b = srcWrap.getBoundingClientRect();
        setSheetZoomAt(sheetState.zoom * (e.deltaY < 0 ? 1.16 : 1 / 1.16), e.clientX - b.left, e.clientY - b.top);
      }, { passive: false });
      srcWrap.addEventListener("pointerdown", (e) => {
        const img = sheetState.sheetImg;
        if (!img) return;
        if (e.button === 1 || e.button === 2 || Canvas.view.spaceDown) {
          sheetState.drag = { kind: "pan", sx: e.clientX, sy: e.clientY, panX: sheetState.panX, panY: sheetState.panY };
          srcWrap.style.cursor = "grabbing";
          try { srcWrap.setPointerCapture(e.pointerId); } catch (_) {}
          return;
        }
        if (e.button !== 0) return;
        const pt = clientToSheetPx(e.clientX, e.clientY);
        if (!pt) return;
        const hit = sheetHitTest(pt.x, pt.y);
        const r = sheetState.cutRect;
        const origPx = [
          r.x * img.naturalWidth,
          r.y * img.naturalHeight,
          (r.x + r.w) * img.naturalWidth,
          (r.y + r.h) * img.naturalHeight,
        ];
        if (hit && hit.kind === "handle") {
          sheetState.drag = { kind: "resize", handle: hit.handle, startPt: pt, origPx };
          srcWrap.style.cursor = SHEET_HANDLE_CURSORS[hit.handle] || "pointer";
        } else if (hit && hit.kind === "body") {
          sheetState.drag = { kind: "move", startPt: pt, origPx };
          srcWrap.style.cursor = "grabbing";
        } else {
          const cx = Math.max(0, Math.min(img.naturalWidth, pt.x));
          const cy = Math.max(0, Math.min(img.naturalHeight, pt.y));
          sheetState.drag = { kind: "new", x0: cx, y0: cy, sx: e.clientX, sy: e.clientY, active: false };
        }
        try { srcWrap.setPointerCapture(e.pointerId); } catch (_) {}
        drawSheetSource();
      });
      srcWrap.addEventListener("pointermove", (e) => {
        const img = sheetState.sheetImg;
        if (!img) return;
        const pt = clientToSheetPx(e.clientX, e.clientY);
        if (!sheetState.drag) {
          updateSheetCursor(pt);
          return;
        }
        const dg = sheetState.drag;
        if (dg.kind === "pan") {
          sheetState.panX = dg.panX + (e.clientX - dg.sx);
          sheetState.panY = dg.panY + (e.clientY - dg.sy);
          applySheetTransform();
          return;
        }
        if (!pt) return;
        const W = img.naturalWidth, H = img.naturalHeight;
        const MIN = Math.min(16, W, H);

        if (dg.kind === "move") {
          const [ox1, oy1, ox2, oy2] = dg.origPx;
          const rw = ox2 - ox1, rh = oy2 - oy1;
          const nx1 = Math.max(0, Math.min(W - rw, ox1 + (pt.x - dg.startPt.x)));
          const ny1 = Math.max(0, Math.min(H - rh, oy1 + (pt.y - dg.startPt.y)));
          sheetState.cutRect = { x: nx1 / W, y: ny1 / H, w: rw / W, h: rh / H };
        } else if (dg.kind === "resize") {
          const [ox1, oy1, ox2, oy2] = dg.origPx;
          const dx = pt.x - dg.startPt.x;
          const dy = pt.y - dg.startPt.y;
          const h = dg.handle;
          let x1 = ox1, y1 = oy1, x2 = ox2, y2 = oy2;
          if (h.includes("w")) x1 = Math.max(0, Math.min(ox2 - MIN, ox1 + dx));
          if (h.includes("e")) x2 = Math.min(W, Math.max(ox1 + MIN, ox2 + dx));
          if (h.includes("n")) y1 = Math.max(0, Math.min(oy2 - MIN, oy1 + dy));
          if (h.includes("s")) y2 = Math.min(H, Math.max(oy1 + MIN, oy2 + dy));

          // Shift + 모서리 드래그: 원래 종횡비 유지
          if (e.shiftKey && (h.includes("w") || h.includes("e")) && (h.includes("n") || h.includes("s"))) {
            const aspect = Math.max(0.05, (ox2 - ox1) / Math.max(1, oy2 - oy1));
            let nw = x2 - x1, nh = y2 - y1;
            if (nw / Math.max(1, nh) > aspect) nh = nw / aspect;
            else nw = nh * aspect;
            if (h.includes("w")) {
              x1 = Math.max(0, ox2 - nw);
              nw = ox2 - x1; nh = nw / aspect;
            } else {
              x2 = Math.min(W, ox1 + nw);
              nw = x2 - ox1; nh = nw / aspect;
            }
            if (h.includes("n")) {
              y1 = Math.max(0, oy2 - nh);
              nh = oy2 - y1; nw = nh * aspect;
              if (h.includes("w")) x1 = Math.max(0, ox2 - nw); else x2 = Math.min(W, ox1 + nw);
            } else {
              y2 = Math.min(H, oy1 + nh);
              nh = y2 - oy1; nw = nh * aspect;
              if (h.includes("w")) x1 = Math.max(0, ox2 - nw); else x2 = Math.min(W, ox1 + nw);
            }
          }
          sheetState.cutRect = {
            x: Math.max(0, x1) / W,
            y: Math.max(0, y1) / H,
            w: Math.max(MIN, x2 - x1) / W,
            h: Math.max(MIN, y2 - y1) / H,
          };
        } else if (dg.kind === "new") {
          if (!dg.active) {
            if (Math.hypot(e.clientX - dg.sx, e.clientY - dg.sy) < 5) return;
            dg.active = true;
          }
          let x2 = Math.max(0, Math.min(W, pt.x));
          let y2 = Math.max(0, Math.min(H, pt.y));
          if (e.shiftKey) {
            const side = Math.max(Math.abs(x2 - dg.x0), Math.abs(y2 - dg.y0));
            x2 = Math.max(0, Math.min(W, dg.x0 + (x2 >= dg.x0 ? side : -side)));
            y2 = Math.max(0, Math.min(H, dg.y0 + (y2 >= dg.y0 ? side : -side)));
          }
          const nx1 = Math.min(dg.x0, x2), ny1 = Math.min(dg.y0, y2);
          const nw = Math.max(MIN, Math.abs(x2 - dg.x0));
          const nh = Math.max(MIN, Math.abs(y2 - dg.y0));
          sheetState.cutRect = {
            x: Math.min(nx1, W - nw) / W,
            y: Math.min(ny1, H - nh) / H,
            w: Math.min(nw, W) / W,
            h: Math.min(nh, H) / H,
          };
        }
        drawSheetSource();
        drawSheetWarp();
      });
      const finishSheetDrag = (e) => {
        if (!sheetState.drag) return;
        sheetState.drag = null;
        const pt = e ? clientToSheetPx(e.clientX, e.clientY) : null;
        updateSheetCursor(pt);
        drawSheetSource();
      };
      srcWrap.addEventListener("pointerup", finishSheetDrag);
      srcWrap.addEventListener("pointercancel", finishSheetDrag);
      srcWrap.addEventListener("pointerleave", () => {
        if (!sheetState.drag && sheetState.hoverHit) {
          sheetState.hoverHit = null;
          drawSheetSource();
        }
      });
    }

    // 우측 4점 원근: 꼭짓점 개별 드래그 + 변 통째 드래그 + 사각형 내부 전체 평행 이동
    const warpCanvas = $("sheet-warp-canvas");
    if (warpCanvas) {
      const updateWarpCursor = (x, y) => {
        const hit = warpHitTest(x, y);
        const prev = sheetState.warpHover;
        const changed = (prev?.kind !== hit?.kind) || (prev?.idx !== hit?.idx);
        sheetState.warpHover = hit;
        if (!hit) warpCanvas.style.cursor = "default";
        else if (hit.kind === "corner") warpCanvas.style.cursor = "grab";
        else if (hit.kind === "edge") warpCanvas.style.cursor = "pointer";
        else warpCanvas.style.cursor = "move";
        if (changed) drawSheetWarp();
      };

      warpCanvas.addEventListener("pointerdown", (e) => {
        if (!sheetState.sheetImg || e.button !== 0) return;
        const b = warpCanvas.getBoundingClientRect();
        const x = (e.clientX - b.left) / b.width;
        const y = (e.clientY - b.top) / b.height;
        const hit = warpHitTest(x, y);
        if (!hit) return;
        const origQuad = sheetState.quad.map((pt) => ({ x: pt.x, y: pt.y }));
        if (hit.kind === "corner") {
          sheetState.drag = { kind: "quad-corner", idx: hit.idx, sx: x, sy: y, origQuad };
          warpCanvas.style.cursor = "grabbing";
        } else if (hit.kind === "edge") {
          sheetState.drag = { kind: "quad-edge", idx: hit.idx, sx: x, sy: y, origQuad };
          warpCanvas.style.cursor = "grabbing";
        } else {
          sheetState.drag = { kind: "quad-move", sx: x, sy: y, origQuad };
          warpCanvas.style.cursor = "grabbing";
        }
        try { warpCanvas.setPointerCapture(e.pointerId); } catch (_) {}
        drawSheetWarp();
      });
      warpCanvas.addEventListener("pointermove", (e) => {
        const b = warpCanvas.getBoundingClientRect();
        const x = (e.clientX - b.left) / b.width;
        const y = (e.clientY - b.top) / b.height;
        if (!sheetState.drag) {
          updateWarpCursor(x, y);
          return;
        }
        const dg = sheetState.drag;
        const clampQ = (v) => Math.max(0.02, Math.min(0.98, v));
        if (dg.kind === "quad-corner") {
          sheetState.quad[dg.idx] = { x: clampQ(x), y: clampQ(y) };
          drawSheetWarp();
        } else if (dg.kind === "quad-edge") {
          const dx = x - dg.sx, dy = y - dg.sy;
          const i1 = dg.idx, i2 = (dg.idx + 1) % 4;
          sheetState.quad[i1] = { x: clampQ(dg.origQuad[i1].x + dx), y: clampQ(dg.origQuad[i1].y + dy) };
          sheetState.quad[i2] = { x: clampQ(dg.origQuad[i2].x + dx), y: clampQ(dg.origQuad[i2].y + dy) };
          drawSheetWarp();
        } else if (dg.kind === "quad-move") {
          let dx = x - dg.sx, dy = y - dg.sy;
          const minX = Math.min(...dg.origQuad.map((p) => p.x));
          const maxX = Math.max(...dg.origQuad.map((p) => p.x));
          const minY = Math.min(...dg.origQuad.map((p) => p.y));
          const maxY = Math.max(...dg.origQuad.map((p) => p.y));
          dx = Math.max(0.02 - minX, Math.min(0.98 - maxX, dx));
          dy = Math.max(0.02 - minY, Math.min(0.98 - maxY, dy));
          sheetState.quad = dg.origQuad.map((p) => ({ x: p.x + dx, y: p.y + dy }));
          drawSheetWarp();
        }
      });
      const finishWarpDrag = (e) => {
        if (!sheetState.drag) return;
        sheetState.drag = null;
        if (e) {
          const b = warpCanvas.getBoundingClientRect();
          updateWarpCursor((e.clientX - b.left) / b.width, (e.clientY - b.top) / b.height);
        }
        drawSheetWarp();
      };
      warpCanvas.addEventListener("pointerup", finishWarpDrag);
      warpCanvas.addEventListener("pointercancel", finishWarpDrag);
      warpCanvas.addEventListener("pointerleave", () => {
        if (!sheetState.drag && sheetState.warpHover) {
          sheetState.warpHover = null;
          drawSheetWarp();
        }
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
      // #C43-10: 이미지 로드 실패 또는 로딩 중에는 채택 요청을 보내지 않는다
      if (cmpState.loading || !cmpState.candKey || cmpState.loadedCandKey !== cmpState.candKey) return;
      const p = Store.primaryPatch();
      if (!p || !p.target) return;
      if (Store.isPickBusy(p.id, p.target.tid, cmpState.candKey)) return;
      coPick.disabled = true;
      try {
        // #C07-4 · #C21-6 · R2-5: 다중 채택 보존 + 연속 클릭 중복 잠금 + 후보 이미지 로드 완료 후 채택
        const r = await Store.pickCandidateGuarded({
          crop_id: p.id,
          tid: p.target.tid,
          key: cmpState.candKey,
          mode: "add",
          successToast: `"${p.name}"에 선택한 후보를 확정 적용했습니다 ★`,
        });
        if (r && r.ok) closeCompareOverlay();
      } finally {
        syncComparePickBtn();
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

    // 프로젝트 홈 툴바 · 빠른 드롭존 이벤트 바인딩
    const btnRefresh = $("btn-home-refresh");
    if (btnRefresh) btnRefresh.addEventListener("click", () => {
      if (canManageProjectHome() && homeState.tab === "archived") loadArchivedProjects();
      else renderProjectsHome();
    });

    const homeSearch = $("home-search-input");
    if (homeSearch) {
      homeSearch.addEventListener("input", (e) => {
        homeState.query = e.target.value || "";
        drawProjectsHome();
      });
      homeSearch.addEventListener("keydown", (e) => {
        if (e.key === "Escape") {
          if (homeState.query) {
            homeState.query = "";
            homeSearch.value = "";
            drawProjectsHome();
          }
          homeSearch.blur();
        }
      });
    }
    const btnSearchClear = $("btn-home-search-clear");
    if (btnSearchClear) {
      btnSearchClear.addEventListener("click", () => {
        homeState.query = "";
        if (homeSearch) { homeSearch.value = ""; homeSearch.focus(); }
        drawProjectsHome();
      });
    }

    const homeSort = $("home-sort-select");
    if (homeSort) {
      homeSort.addEventListener("change", (e) => {
        Store.saveHomePrefs({ sortBy: e.target.value });
        drawProjectsHome();
      });
    }
    const btnViewGrid = $("home-view-grid");
    if (btnViewGrid) {
      btnViewGrid.addEventListener("click", () => {
        Store.saveHomePrefs({ viewMode: "grid" });
        drawProjectsHome();
      });
    }
    const btnViewList = $("home-view-list");
    if (btnViewList) {
      btnViewList.addEventListener("click", () => {
        Store.saveHomePrefs({ viewMode: "list" });
        drawProjectsHome();
      });
    }

    const quickDrop = $("home-quick-drop");
    if (quickDrop) {
      let qdDepth = 0;
      quickDrop.addEventListener("dragenter", (e) => {
        if (e.dataTransfer?.types?.includes("Files")) {
          e.preventDefault();
          e.stopPropagation();
          qdDepth += 1;
          quickDrop.classList.add("over");
          const gOv = $("global-drop-overlay");
          if (gOv) gOv.classList.add("hidden");
        }
      });
      quickDrop.addEventListener("dragover", (e) => {
        if (e.dataTransfer?.types?.includes("Files")) {
          e.preventDefault();
          e.stopPropagation();
          quickDrop.classList.add("over");
        }
      });
      quickDrop.addEventListener("dragleave", (e) => {
        if (e.dataTransfer?.types?.includes("Files")) {
          e.stopPropagation();
          qdDepth = Math.max(0, qdDepth - 1);
          if (qdDepth === 0) quickDrop.classList.remove("over");
        }
      });
      quickDrop.addEventListener("drop", (e) => {
        if (e.dataTransfer?.types?.includes("Files")) {
          e.preventDefault();
          e.stopPropagation();
          qdDepth = 0;
          quickDrop.classList.remove("over");
          const gOv = $("global-drop-overlay");
          if (gOv) gOv.classList.add("hidden");
          const files = Array.from(e.dataTransfer.files || []);
          if (files.length) openNewProjectModal(files[0], files.slice(1));
        }
      });
    }

    const btnDropPick = $("btn-home-drop-pick");
    if (btnDropPick) {
      btnDropPick.addEventListener("click", (e) => {
        e.stopPropagation();
        const inp = Data.el("input", { type: "file", accept: Data.TARGET_ACCEPT || ".psd,.psb,.png,.jpg,.jpeg,.webp", multiple: true });
        inp.addEventListener("change", () => {
          const files = Array.from(inp.files || []);
          if (files.length) openNewProjectModal(files[0], files.slice(1));
        });
        inp.click();
      });
    }
    const btnDropForge = $("btn-home-drop-forge");
    if (btnDropForge) {
      btnDropForge.addEventListener("click", (e) => {
        e.stopPropagation();
        openNewProjectModal(null, [], { hintForForge: true });
      });
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
    nudgeSheetCut,
    openCompareOverlay,
    closeCompareOverlay,
    toggleHistoryDrawer,
    setHistoryPanelMode,
    applyHistoryPanelLayout,
    openSettingsModal,
    openProjectDefaultsModal,
    openExportModal,
    openPresetsModal,
    openShortcutsModal,
    renderProjectsHome,
    drawProjectsHome,
    openArchivedTab,
    openActiveTab,
    openRenameProjectModal,
    archiveProject,
    restoreProject,
    openNewProjectModal,
    openForgeImportForRef,
    bindEvents,
  };
})();
