// BMK Design Patch Studio v2 — 프로젝트 홈 · 새 프로젝트 · 시트 오려내기/4점 원근 · 풀스크린 비교기 · 히스토리 · 설정 · 패널 드래그 도킹.
"use strict";

window.DPV2Overlays = (() => {
  const Data = window.DPV2Data;
  const Api = window.DPV2Api;
  const Store = window.DPV2Store;

  function $(id) { return document.getElementById(id); }

  function init() {
    wireNewProjectModal();
    wireSheetCutOverlay();
    wireComparerOverlay();
    wireHistoryDrawer();
    wireSettingsModal();
    wireExportModal();
    wirePanelDragDocking();

    Store.subscribe((reason) => {
      if (reason === "history") renderHistoryDrawer();
      if (reason === "prefs") renderSettingsSelections();
    });
  }

  // ==================== 1. 프로젝트 홈 & 새 프로젝트 모달 ====================
  async function mountProjectsHome() {
    $("projects-view").classList.remove("hidden");
    $("studio-view").classList.add("hidden");
    if (window.DPEnv && window.DPEnv.account && window.DPEnv.account.name) {
      $("home-account").textContent = window.DPEnv.account.name;
      $("home-account").classList.remove("hidden");
    }
    $("btn-home-v1").onclick = () => location.assign(Data.buildSwitchUrl(false));
    $("btn-home-settings").onclick = openSettings;
    $("btn-home-new").onclick = openNewProjectModal;
    $("btn-home-reload").onclick = loadProjectsGrid;
    $("home-search").oninput = renderProjectsGrid;
    await loadProjectsGrid();
  }

  async function loadProjectsGrid() {
    $("home-status").textContent = "프로젝트를 불러오는 중…";
    try {
      const res = await Api.listProjects();
      Store.state.projects = Array.isArray(res.projects) ? res.projects : [];
      renderProjectsGrid();
    } catch (err) {
      $("home-status").textContent = `불러오기 실패: ${err.message}`;
    }
  }

  function renderProjectsGrid() {
    const grid = $("home-grid");
    grid.replaceChildren();
    const q = ($("home-search").value || "").trim().toLowerCase();
    const rows = Store.state.projects.filter((r) => !q || String(r.name || "").toLowerCase().includes(q));
    $("home-status").textContent = rows.length ? `${rows.length}개 프로젝트` : (q ? "검색 결과가 없습니다." : "아직 프로젝트가 없습니다. [+ 새 프로젝트]로 원화를 불러오세요.");

    for (const r of rows) {
      const thumbUrl = r.thumb_url ? Api.apiUrl(r.thumb_url) : null;
      const thumbBox = Data.el("div", { className: "proj-card-thumb" },
        thumbUrl ? Data.el("img", { src: thumbUrl, alt: `${r.name} 미리보기`, loading: "lazy" })
          : Data.el("span", { text: r.before_import ? "가져오기 전" : "미리보기 없음" }),
      );

      const openBtn = Data.el("button", {
        type: "button", className: "btn-primary full-w",
        disabled: !r.openable,
        text: r.openable ? "스튜디오 열기" : "열 수 없음",
        onClick: async () => {
          openBtn.disabled = true;
          openBtn.textContent = "여는 중…";
          try {
            const res = await Api.openProject(r.id);
            if (!res.ok || !res.key) throw new Error(res.error || "프로젝트를 열지 못했습니다");
            const url = new URL(location.href);
            url.searchParams.set("root", res.key);
            url.searchParams.set("ui", "v2");
            location.assign(url.pathname + url.search);
          } catch (err) {
            openBtn.disabled = false;
            openBtn.textContent = "스튜디오 열기";
            Store.toast(err.message, "err");
          }
        },
      });

      const card = Data.el("article", { className: "proj-card" },
        thumbBox,
        Data.el("div", { className: "proj-card-body" },
          Data.el("h2", { className: "proj-card-title", text: r.name, title: r.name }),
          Data.el("div", { className: "proj-card-meta" },
            Data.el("span", { text: `패치 ${r.crops ?? 0}` }),
            Data.el("span", { text: `★ ${r.stars ?? 0}` }),
            Data.el("span", { text: `후보 ${r.candidates ?? 0}` }),
          ),
          Data.el("p", { className: "muted-text", text: r.openable ? (r.modified ? `수정 ${r.modified}` : "") : (r.error || "오류") }),
        ),
        Data.el("div", { className: "proj-card-foot" }, openBtn),
      );
      grid.append(card);
    }
  }

  let npTargetFile = null;
  let npRefFiles = [];

  function openNewProjectModal() {
    npTargetFile = null;
    npRefFiles = [];
    $("np-name").value = "";
    $("np-name-err").textContent = "";
    $("np-path-input").value = "";
    $("np-status").textContent = "";
    $("np-path-wrap").classList.toggle("hidden", !Api.can("path_input"));
    $("btn-np-forge").classList.toggle("hidden", !Api.can("forge_import"));
    syncNewProjectUI();
    $("new-project-modal").classList.remove("hidden");
    $("np-name").focus();
  }

  function syncNewProjectUI() {
    const nameErr = Data.validateProjectName($("np-name").value);
    $("np-name-err").textContent = $("np-name").value ? nameErr : "";
    $("np-target-preview").classList.toggle("hidden", !npTargetFile);
    if (npTargetFile) $("np-target-filename").textContent = npTargetFile.name;

    const listHost = $("np-refs-list");
    listHost.replaceChildren();
    npRefFiles.forEach((f, idx) => {
      listHost.append(Data.el("div", { className: "selected-file-chip" },
        Data.el("span", { className: "file-chip-name", text: f.name }),
        Data.el("button", {
          type: "button", className: "icon-btn-sm", text: "×", title: "제거",
          onClick: () => { npRefFiles.splice(idx, 1); syncNewProjectUI(); },
        }),
      ));
    });

    const hasSource = !!npTargetFile || !!$("np-path-input").value.trim();
    $("btn-np-forge").disabled = !!nameErr;
    $("btn-submit-new-project").disabled = !!nameErr || !hasSource;
  }

  function wireNewProjectModal() {
    const close = () => $("new-project-modal").classList.add("hidden");
    $("btn-close-new-project").addEventListener("click", close);
    $("btn-cancel-new-project").addEventListener("click", close);
    $("np-name").addEventListener("input", syncNewProjectUI);
    $("np-path-input").addEventListener("input", syncNewProjectUI);

    const tDrop = $("np-target-drop");
    tDrop.addEventListener("click", () => $("np-target-file").click());
    tDrop.addEventListener("dragover", (e) => { e.preventDefault(); tDrop.classList.add("drag-over"); });
    tDrop.addEventListener("dragleave", () => tDrop.classList.remove("drag-over"));
    tDrop.addEventListener("drop", (e) => {
      e.preventDefault();
      tDrop.classList.remove("drag-over");
      const file = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
      if (file) {
        npTargetFile = file;
        if (!$("np-name").value.trim()) $("np-name").value = file.name.replace(/\.[^.]+$/, "").replace(/[^\p{L}\p{N}_.-]+/gu, "_");
        syncNewProjectUI();
      }
    });
    $("np-target-file").addEventListener("change", (e) => {
      const file = e.target.files && e.target.files[0];
      e.target.value = "";
      if (file) {
        npTargetFile = file;
        if (!$("np-name").value.trim()) $("np-name").value = file.name.replace(/\.[^.]+$/, "").replace(/[^\p{L}\p{N}_.-]+/gu, "_");
        syncNewProjectUI();
      }
    });
    $("btn-np-remove-target").addEventListener("click", () => { npTargetFile = null; syncNewProjectUI(); });

    const rDrop = $("np-refs-drop");
    rDrop.addEventListener("click", () => $("np-refs-files").click());
    rDrop.addEventListener("dragover", (e) => { e.preventDefault(); rDrop.classList.add("drag-over"); });
    rDrop.addEventListener("dragleave", () => rDrop.classList.remove("drag-over"));
    rDrop.addEventListener("drop", (e) => {
      e.preventDefault();
      rDrop.classList.remove("drag-over");
      for (const f of (e.dataTransfer && e.dataTransfer.files) || []) npRefFiles.push(f);
      syncNewProjectUI();
    });
    $("np-refs-files").addEventListener("change", (e) => {
      for (const f of (e.target.files || [])) npRefFiles.push(f);
      e.target.value = "";
      syncNewProjectUI();
    });

    $("btn-np-forge").addEventListener("click", () => {
      const name = $("np-name").value.trim();
      if (Data.validateProjectName(name) || !window.DPForgeImport) return;
      window.DPForgeImport.open({
        into: "target",
        name,
        onDone: (res) => finishNewProject(res),
      });
    });

    $("btn-submit-new-project").addEventListener("click", async () => {
      const name = $("np-name").value.trim();
      if (Data.validateProjectName(name)) return;
      $("btn-submit-new-project").disabled = true;
      $("np-status").textContent = "프로젝트를 만드는 중…";
      try {
        const res = npTargetFile
          ? await Api.createProjectFile(name, npTargetFile)
          : await Api.createProjectPath(name, $("np-path-input").value.trim().replace(/^(["'])(.*)\1$/, "$2"));
        if (npRefFiles.length && res.key) {
          $("np-status").textContent = `레퍼런스 시트 ${npRefFiles.length}장 올리는 중…`;
          for (const rf of npRefFiles) {
            try { await Api.uploadSheet(res.key, rf, null); } catch (_) {}
          }
        }
        finishNewProject(res);
      } catch (err) {
        $("np-status").textContent = err.message;
        syncNewProjectUI();
      }
    });
  }

  function finishNewProject(res) {
    if (!res || !/^[0-9a-f]{12}$/.test(res.key || "")) return Store.toast("프로젝트 생성 결과를 확인하지 못했습니다.", "err");
    const url = new URL(location.href);
    url.searchParams.set("root", res.key);
    url.searchParams.set("ui", "v2");
    location.assign(url.pathname + url.search);
  }

  // ==================== 2. 레퍼런스 시트 오려내기 & 4점 원근 (피드백 #4: 1:1/최대화/휠줌/오버스크롤) ====================
  const shState = {
    sheet: null,
    imgW: 1024, imgH: 1024,
    cam: { z: 1, tx: 0, ty: 0 },
    mode: "rect", // rect | poly
    rect: null,
    poly: [],
    cutResult: null,
    cutImg: null,
    rotate: 0,
    scale: 1,
    flipH: false,
    corners: [[0, 0], [1, 0], [1, 1], [0, 1]],
    drag: null,
    perspDrag: null,
  };

  function openSheetCut(sheetPath = null) {
    $("sheet-overlay").classList.remove("hidden");
    $("btn-sheet-forge").classList.toggle("hidden", !Api.can("forge_import"));
    populateSheetSelectors(sheetPath);
    if (shState.sheet) loadSheetImage(shState.sheet);
  }

  function populateSheetSelectors(preferPath) {
    const sel = $("sheet-select");
    sel.replaceChildren();
    const sheets = Store.state.sheets || [];
    for (const s of sheets) {
      sel.append(Data.el("option", { value: s.path, text: s.name || s.path }));
    }
    const target = sheets.find((s) => s.path === preferPath) || sheets[0] || null;
    shState.sheet = target;
    if (target) sel.value = target.path;
    $("sheet-empty").classList.toggle("hidden", !!target);

    const cropSel = $("cut-target-crop");
    cropSel.replaceChildren();
    const patches = Store.getPatches();
    for (const p of patches) cropSel.append(Data.el("option", { value: p.id, text: `${p.index}. ${p.name}` }));
    const cur = Store.primaryPatch();
    if (cur) cropSel.value = cur.id;
  }

  function loadSheetImage(sh) {
    const url = Api.sheetUrl(Store.state.root, sh, "preview");
    const img = $("sheet-img");
    img.onload = () => {
      shState.imgW = (sh.size && sh.size[0]) || img.naturalWidth || 1024;
      shState.imgH = (sh.size && sh.size[1]) || img.naturalHeight || 1024;
      img.style.width = `${shState.imgW}px`;
      img.style.height = `${shState.imgH}px`;
      $("sheet-stage").style.width = `${shState.imgW}px`;
      $("sheet-stage").style.height = `${shState.imgH}px`;
      $("sheet-svg").setAttribute("viewBox", `0 0 ${shState.imgW} ${shState.imgH}`);
      fitSheetView();
    };
    if (url) img.src = url;
    shState.rect = null;
    shState.poly = [];
    shState.cutResult = null;
    $("btn-sheet-cut-run").disabled = true;
    $("btn-cut-save").disabled = true;
    renderSheetSvg();
  }

  function applySheetTransform() {
    $("sheet-stage").style.transform = `translate(${shState.cam.tx}px, ${shState.cam.ty}px) scale(${shState.cam.z})`;
    $("sheet-zoom-val").textContent = `${Math.round(shState.cam.z * 100)}%`;
    renderSheetSvg();
  }

  function fitSheetView() {
    const wrap = $("sheet-canvas-wrap");
    if (!wrap.clientWidth || !wrap.clientHeight) return;
    const pad = 40;
    shState.cam.z = Math.max(0.15, Math.min(2, Math.min((wrap.clientWidth - pad * 2) / shState.imgW, (wrap.clientHeight - pad * 2) / shState.imgH)));
    shState.cam.tx = (wrap.clientWidth - shState.imgW * shState.cam.z) / 2;
    shState.cam.ty = (wrap.clientHeight - shState.imgH * shState.cam.z) / 2;
    applySheetTransform();
  }

  function zoomSheet(nextZ, cx = null, cy = null) {
    const wrap = $("sheet-canvas-wrap");
    const r = wrap.getBoundingClientRect();
    const ax = cx !== null ? cx - r.left : r.width / 2;
    const ay = cy !== null ? cy - r.top : r.height / 2;
    const sx = (ax - shState.cam.tx) / shState.cam.z;
    const sy = (ay - shState.cam.ty) / shState.cam.z;
    shState.cam.z = Math.max(0.15, Math.min(6, nextZ));
    shState.cam.tx = ax - sx * shState.cam.z;
    shState.cam.ty = ay - sy * shState.cam.z;
    applySheetTransform();
  }

  function sheetPt(clientX, clientY) {
    const r = $("sheet-canvas-wrap").getBoundingClientRect();
    return [(clientX - r.left - shState.cam.tx) / shState.cam.z, (clientY - r.top - shState.cam.ty) / shState.cam.z];
  }

  function renderSheetSvg() {
    const sSvg = $("sheet-svg");
    sSvg.replaceChildren();
    const invZ = 1 / Math.max(0.1, shState.cam.z);
    const accent = getComputedStyle(document.documentElement).getPropertyValue("--accent").trim() || "#dfe104";
    if (shState.rect) {
      const [x0, y0, x1, y1] = shState.rect;
      sSvg.append(Data.svgEl("rect", {
        x: x0, y: y0, width: Math.max(1, x1 - x0), height: Math.max(1, y1 - y0),
        fill: "none", stroke: accent, "stroke-width": 2.2 * invZ,
      }));
    }
    if (shState.poly.length) {
      const pts = shState.poly.map((p) => p.join(",")).join(" ");
      sSvg.append(Data.svgEl("polygon", { points: pts, fill: "rgba(223,225,4,0.12)", stroke: accent, "stroke-width": 2 * invZ }));
      for (const [px, py] of shState.poly) {
        sSvg.append(Data.svgEl("circle", { cx: px, cy: py, r: 4.5 * invZ, fill: accent, stroke: "#000", "stroke-width": 1 * invZ }));
      }
    }
  }

  function drawCutPreview() {
    const cv = $("cut-preview-canvas");
    const ctx = cv.getContext("2d");
    const empty = $("cut-preview-empty");
    if (!shState.cutImg) {
      cv.width = 1; cv.height = 1;
      empty.classList.remove("hidden");
      $("cut-persp-svg").replaceChildren();
      return;
    }
    empty.classList.add("hidden");
    const size = 260;
    cv.width = size; cv.height = size;
    ctx.clearRect(0, 0, size, size);
    ctx.save();
    ctx.translate(size / 2, size / 2);
    ctx.rotate((-shState.rotate * Math.PI) / 180);
    if (shState.flipH) ctx.scale(-1, 1);
    const fit = Math.min((size - 32) / shState.cutImg.width, (size - 32) / shState.cutImg.height) * shState.scale;
    const dw = shState.cutImg.width * fit, dh = shState.cutImg.height * fit;
    ctx.drawImage(shState.cutImg, -dw / 2, -dh / 2, dw, dh);
    ctx.restore();

    // 4점 원근 꼭짓점 핸들 오버레이
    const pSvg = $("cut-persp-svg");
    pSvg.replaceChildren();
    pSvg.setAttribute("viewBox", "0 0 100 100");
    const pts = shState.corners.map(([u, v]) => [15 + u * 70, 15 + v * 70]);
    pSvg.append(Data.svgEl("polygon", {
      points: pts.map((p) => p.join(",")).join(" "),
      fill: "none", stroke: "#dfe104", "stroke-width": "1.2", "stroke-dasharray": "3 2",
    }));
    pts.forEach(([px, py], i) => {
      pSvg.append(Data.svgEl("circle", {
        cx: px, cy: py, r: 4, fill: "#dfe104", stroke: "#09090b", "stroke-width": "1.2",
        style: "cursor:move", "data-persp-idx": i,
      }));
    });
    $("cut-size-readout").textContent = `${Math.round(shState.cutImg.width * shState.scale)}×${Math.round(shState.cutImg.height * shState.scale)}px`;
  }

  function wireSheetCutOverlay() {
    $("btn-close-sheet").addEventListener("click", () => $("sheet-overlay").classList.add("hidden"));
    $("sheet-select").addEventListener("change", (e) => {
      const sh = (Store.state.sheets || []).find((s) => s.path === e.target.value);
      if (sh) { shState.sheet = sh; loadSheetImage(sh); }
    });
    $("btn-sheet-mode-rect").addEventListener("click", () => {
      shState.mode = "rect"; shState.poly = [];
      $("btn-sheet-mode-rect").classList.add("on"); $("btn-sheet-mode-poly").classList.remove("on");
      renderSheetSvg();
    });
    $("btn-sheet-mode-poly").addEventListener("click", () => {
      shState.mode = "poly"; shState.rect = null;
      $("btn-sheet-mode-poly").classList.add("on"); $("btn-sheet-mode-rect").classList.remove("on");
      renderSheetSvg();
    });
    $("btn-sheet-zoom-out").addEventListener("click", () => zoomSheet(shState.cam.z / 1.25));
    $("btn-sheet-zoom-in").addEventListener("click", () => zoomSheet(shState.cam.z * 1.25));
    $("btn-sheet-zoom-100").addEventListener("click", () => zoomSheet(1));
    $("btn-sheet-zoom-fit").addEventListener("click", fitSheetView);
    $("btn-sheet-maximize").addEventListener("click", () => {
      const body = $("sheet-body");
      const max = body.classList.toggle("maximized");
      $("btn-sheet-maximize").classList.toggle("on", max);
      setTimeout(fitSheetView, 30);
    });
    $("btn-sheet-upload").addEventListener("click", () => $("input-sheet-upload").click());
    $("input-sheet-upload").addEventListener("change", async (e) => {
      const file = e.target.files && e.target.files[0];
      e.target.value = "";
      if (!file) return;
      try {
        await Api.uploadSheet(Store.state.root, file, Store.state.R && Store.state.R.rev);
        await Store.loadRefs(true);
        populateSheetSelectors();
        if (shState.sheet) loadSheetImage(shState.sheet);
        Store.toast(`시트 "${file.name}" 올리기 완료`, "ok");
      } catch (err) {
        Store.toast(err.message, "err");
      }
    });
    $("btn-sheet-forge").addEventListener("click", () => {
      if (!window.DPForgeImport) return;
      window.DPForgeImport.open({
        root: Store.state.root, into: "refs",
        onDone: async () => { await Store.loadRefs(true); populateSheetSelectors(); },
      });
    });

    // 시트 휠 줌 & 오버스크롤 패닝 + 영역 선택
    const wrap = $("sheet-canvas-wrap");
    wrap.addEventListener("wheel", (e) => {
      e.preventDefault();
      zoomSheet(shState.cam.z * Math.exp(-e.deltaY * 0.0014), e.clientX, e.clientY);
    }, { passive: false });

    wrap.addEventListener("pointerdown", (e) => {
      if (!shState.sheet) return;
      if (e.button === 1 || e.altKey || e.shiftKey) {
        e.preventDefault();
        shState.drag = { kind: "pan", sx: e.clientX, sy: e.clientY, tx0: shState.cam.tx, ty0: shState.cam.ty, pid: e.pointerId };
        wrap.setPointerCapture(e.pointerId);
        return;
      }
      if (e.button !== 0) return;
      const pt = sheetPt(e.clientX, e.clientY);
      const clamped = [Math.max(0, Math.min(shState.imgW, pt[0])), Math.max(0, Math.min(shState.imgH, pt[1]))];
      if (shState.mode === "poly") {
        shState.poly.push(clamped.map((v) => Math.round(v)));
        $("btn-sheet-cut-run").disabled = shState.poly.length < 3;
        renderSheetSvg();
        return;
      }
      shState.drag = { kind: "rect", start: clamped, pid: e.pointerId };
      wrap.setPointerCapture(e.pointerId);
    });

    wrap.addEventListener("pointermove", (e) => {
      if (!shState.drag || shState.drag.pid !== e.pointerId) return;
      if (shState.drag.kind === "pan") {
        shState.cam.tx = shState.drag.tx0 + (e.clientX - shState.drag.sx);
        shState.cam.ty = shState.drag.ty0 + (e.clientY - shState.drag.sy);
        applySheetTransform();
        return;
      }
      const pt = sheetPt(e.clientX, e.clientY);
      const x0 = Math.max(0, Math.min(shState.drag.start[0], pt[0]));
      const y0 = Math.max(0, Math.min(shState.drag.start[1], pt[1]));
      const x1 = Math.min(shState.imgW, Math.max(shState.drag.start[0], pt[0]));
      const y1 = Math.min(shState.imgH, Math.max(shState.drag.start[1], pt[1]));
      shState.rect = [Math.round(x0), Math.round(y0), Math.round(x1), Math.round(y1)];
      $("btn-sheet-cut-run").disabled = (x1 - x0 < 16 || y1 - y0 < 16);
      renderSheetSvg();
    });
    wrap.addEventListener("pointerup", () => { shState.drag = null; });

    // 오려내기 실행 (/refs/cut)
    $("btn-sheet-cut-run").addEventListener("click", async () => {
      if (!shState.sheet) return;
      try {
        let body;
        if (shState.mode === "poly" && shState.poly.length >= 3) {
          const xs = shState.poly.map((p) => p[0]), ys = shState.poly.map((p) => p[1]);
          body = { sheet: shState.sheet.path, method: "poly", rect: [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)], poly: shState.poly };
        } else if (shState.rect) {
          body = { sheet: shState.sheet.path, method: "rect", rect: shState.rect };
        } else return;
        const res = await Api.cutRef(Store.state.root, body);
        shState.cutResult = Object.assign({ req: res.req }, res.result || res);
        const img = new Image();
        img.onload = () => {
          shState.cutImg = img;
          shState.corners = [[0, 0], [1, 0], [1, 1], [0, 1]];
          $("btn-cut-save").disabled = false;
          drawCutPreview();
        };
        img.src = Api.apiUrl(shState.cutResult.preview_url);
        Store.toast("영역을 오려냈습니다. 각도·원근을 조정한 뒤 저장하세요.", "ok");
      } catch (err) {
        Store.toast(`오려내기 실패: ${err.message}`, "err");
      }
    });

    // 4점 원근 핸들 드래그
    const pSvg = $("cut-persp-svg");
    pSvg.addEventListener("pointerdown", (e) => {
      const idx = e.target && e.target.getAttribute ? e.target.getAttribute("data-persp-idx") : null;
      if (idx === null) return;
      e.preventDefault();
      shState.perspDrag = { idx: Number(idx), pid: e.pointerId };
      pSvg.setPointerCapture(e.pointerId);
    });
    pSvg.addEventListener("pointermove", (e) => {
      if (!shState.perspDrag || shState.perspDrag.pid !== e.pointerId) return;
      const r = pSvg.getBoundingClientRect();
      const u = ((e.clientX - r.left) / r.width * 100 - 15) / 70;
      const v = ((e.clientY - r.top) / r.height * 100 - 15) / 70;
      shState.corners[shState.perspDrag.idx] = [
        Math.round(Math.max(-0.5, Math.min(1.5, u)) * 1000) / 1000,
        Math.round(Math.max(-0.5, Math.min(1.5, v)) * 1000) / 1000,
      ];
      drawCutPreview();
    });
    pSvg.addEventListener("pointerup", () => { shState.perspDrag = null; });

    $("btn-persp-reset").addEventListener("click", () => {
      shState.corners = [[0, 0], [1, 0], [1, 1], [0, 1]];
      drawCutPreview();
    });
    $("cut-rotate").addEventListener("input", (e) => {
      shState.rotate = Number(e.target.value);
      $("cut-rotate-val").textContent = `${shState.rotate}°`;
      drawCutPreview();
    });
    $("cut-scale").addEventListener("input", (e) => {
      shState.scale = Number(e.target.value) / 100;
      $("cut-scale-val").textContent = `${e.target.value}%`;
      drawCutPreview();
    });
    $("btn-cut-flip").addEventListener("click", () => {
      shState.flipH = !shState.flipH;
      $("btn-cut-flip").classList.toggle("on", shState.flipH);
      drawCutPreview();
    });
    $("cut-role").addEventListener("change", (e) => {
      $("cut-desc-wrap").classList.toggle("hidden", e.target.value !== "aux");
    });

    $("btn-cut-save").addEventListener("click", async () => {
      if (!shState.cutResult || !shState.cutResult.req) return;
      const cropId = $("cut-target-crop").value;
      const role = $("cut-role").value;
      const desc = $("cut-desc").value.trim();
      const identity = shState.corners.every((pt, i) => pt[0] === [[0, 0], [1, 0], [1, 1], [0, 1]][i][0] && pt[1] === [[0, 0], [1, 0], [1, 1], [0, 1]][i][1]);
      const transform = { rotate: shState.rotate, flip_h: shState.flipH, scale: shState.scale };
      if (!identity) transform.corners = shState.corners;
      const body = { req: shState.cutResult.req, crop_id: cropId, role, transform };
      if (role === "aux") body.desc = desc || "보조1";
      try {
        const rev = Store.state.R ? Store.state.R.rev : null;
        const r = await Api.saveRef(Store.state.root, rev, body);
        Store.toast(`레퍼런스 저장 완료: ${r.file || "연결됨"}`, "ok");
        await Store.refreshAll(true);
        $("sheet-overlay").classList.add("hidden");
      } catch (err) {
        Store.toast(`레퍼런스 저장 실패: ${err.message}`, "err");
      }
    });
  }

  // ==================== 3. 풀스크린 후보 Before/After 비교기 (피드백 #7: 넓은 46px + 박스 전체 드래그) ====================
  const coState = { open: false, ratio: 0.5, blinkBefore: false, zoom: 1, dragging: false };

  function openComparer() {
    const p = Store.primaryPatch();
    if (!p || !p.candidates.length) return Store.toast("비교할 후보가 없습니다.", "warn");
    coState.open = true;
    coState.ratio = 0.5;
    coState.blinkBefore = false;
    coState.zoom = 1;
    $("compare-overlay").classList.remove("hidden");
    renderComparer();
  }

  function closeComparer() {
    coState.open = false;
    $("compare-overlay").classList.add("hidden");
  }

  function renderComparer() {
    if (!coState.open) return;
    const p = Store.primaryPatch();
    if (!p) return closeComparer();
    const cand = Store.currentCandidate(p);
    if (!cand) return closeComparer();

    $("co-title").textContent = `${p.name} — 후보 c${String(cand.n).padStart(2, "0")}`;
    $("co-badge").textContent = cand.picked ? "★ 채택됨" : (cand.rejected ? "제외됨" : (cand.variant || ""));
    $("btn-co-star").textContent = cand.picked ? "★ 채택 해제 (Space)" : "★ 이 후보 채택 (Space)";
    $("btn-co-reject").textContent = cand.rejected ? "제외 해제 (X)" : "제외 (X)";
    $("btn-co-blink").classList.toggle("on", coState.blinkBefore);
    $("co-zoom-val").textContent = `${Math.round(coState.zoom * 100)}%`;

    const beforeUrl = p.target && p.target.before_url ? Api.apiUrl(p.target.before_url) : "";
    const afterUrl = (cand.big_url || cand.thumb_url) ? Api.apiUrl(cand.big_url || cand.thumb_url) : "";
    if (beforeUrl) $("co-img-before").src = beforeUrl;
    if (afterUrl) $("co-img-after").src = afterUrl;

    const box = $("co-compare-box");
    box.style.transform = `scale(${coState.zoom})`;
    updateComparerSplit();

    const rail = $("co-rail");
    rail.replaceChildren();
    p.candidates.forEach((c, idx) => {
      const url = c.thumb_url ? Api.apiUrl(c.thumb_url) : "";
      rail.append(Data.el("button", {
        type: "button",
        className: "co-rail-item" + (c.key === cand.key ? " active" : ""),
        title: `후보 ${idx + 1} (${c.picked ? "★" : ""})`,
        onClick: () => { Store.selectCandidate(p.target.key, c.key); renderComparer(); },
      },
      url ? Data.el("img", { src: url, alt: `c${c.n}` }) : null,
      Data.el("span", { className: "co-rail-idx", text: `${idx + 1}${c.picked ? " ★" : ""}` })));
    });
  }

  function updateComparerSplit() {
    const pct = coState.blinkBefore ? 100 : Math.round(coState.ratio * 1000) / 10;
    $("co-before-clip").style.clipPath = `inset(0 ${100 - pct}% 0 0)`;
    $("co-divider").style.left = `${pct}%`;
    $("co-divider").classList.toggle("hidden", coState.blinkBefore);
  }

  function wireComparerOverlay() {
    $("btn-close-compare").addEventListener("click", closeComparer);
    $("btn-co-blink").addEventListener("click", () => {
      coState.blinkBefore = !coState.blinkBefore;
      renderComparer();
    });
    $("btn-co-zoom-fit").addEventListener("click", () => { coState.zoom = 1; renderComparer(); });
    $("btn-co-zoom-100").addEventListener("click", () => { coState.zoom = 1.5; renderComparer(); });

    const box = $("co-compare-box");
    const updateFromClientX = (clientX) => {
      const r = box.getBoundingClientRect();
      coState.blinkBefore = false;
      coState.ratio = Math.max(0.01, Math.min(0.99, (clientX - r.left) / Math.max(1, r.width)));
      updateComparerSplit();
    };
    box.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      coState.dragging = true;
      box.setPointerCapture(e.pointerId);
      updateFromClientX(e.clientX);
    });
    box.addEventListener("pointermove", (e) => {
      if (!coState.dragging) return;
      updateFromClientX(e.clientX);
    });
    box.addEventListener("pointerup", () => { coState.dragging = false; });

    $("co-stage").addEventListener("wheel", (e) => {
      e.preventDefault();
      coState.zoom = Math.max(0.5, Math.min(4, coState.zoom * Math.exp(-e.deltaY * 0.0012)));
      renderComparer();
    }, { passive: false });

    $("btn-co-star").addEventListener("click", async () => {
      const p = Store.primaryPatch();
      const cand = Store.currentCandidate(p);
      if (!p || !cand) return;
      await Api.pickCandidate(Store.state.root, { crop_id: p.id, tid: p.target.tid, key: cand.key, mode: cand.picked ? "remove" : "set" });
      await Store.refreshAll(true);
      renderComparer();
    });
    $("btn-co-reject").addEventListener("click", async () => {
      const p = Store.primaryPatch();
      const cand = Store.currentCandidate(p);
      if (!cand) return;
      await Api.rejectCandidate(Store.state.root, cand.key, !cand.rejected);
      await Store.refreshAll(true);
      renderComparer();
    });
  }

  function handleComparerKey(e) {
    if (!coState.open) return false;
    if (e.key === "Escape") { closeComparer(); return true; }
    if (e.key === "Tab") { e.preventDefault(); coState.blinkBefore = !coState.blinkBefore; renderComparer(); return true; }
    if (e.code === "Space") { e.preventDefault(); $("btn-co-star").click(); return true; }
    if (e.code === "KeyX") { e.preventDefault(); $("btn-co-reject").click(); return true; }
    if (/^[1-9]$/.test(e.key)) {
      const p = Store.primaryPatch();
      const idx = Number(e.key) - 1;
      if (p && p.candidates[idx]) {
        Store.selectCandidate(p.target.key, p.candidates[idx].key);
        renderComparer();
        return true;
      }
    }
    return false;
  }

  // ==================== 4. 포토샵식 히스토리 드로어 (피드백 #6) ====================
  function toggleHistoryDrawer() {
    const d = $("history-drawer");
    d.classList.toggle("hidden");
    $("btn-history").classList.toggle("on", !d.classList.contains("hidden"));
    renderHistoryDrawer();
  }

  function renderHistoryDrawer() {
    const list = $("history-list");
    list.replaceChildren();
    if (!Store.state.history.length) {
      list.append(Data.el("p", { className: "muted-text", text: "아직 기록된 편집 작업이 없습니다." }));
      return;
    }
    Store.state.history.forEach((h, idx) => {
      const cls = ["hist-item"];
      if (idx === Store.state.histIndex) cls.push("current");
      else if (idx > Store.state.histIndex) cls.push("future");
      list.append(Data.el("button", {
        type: "button", className: cls.join(" "),
        onClick: () => Store.jumpHistory(idx),
      },
      Data.el("span", { text: `${idx + 1}. ${h.label}` }),
      Data.el("span", { className: "hist-time", text: h.time })));
    });
  }

  function wireHistoryDrawer() {
    $("btn-history").addEventListener("click", toggleHistoryDrawer);
    $("btn-close-history").addEventListener("click", toggleHistoryDrawer);
    $("btn-hist-undo").addEventListener("click", () => Store.undoHistory());
    $("btn-hist-redo").addEventListener("click", () => Store.redoHistory());
  }

  // ==================== 5. 작업공간 · Forge 6종 테마 · UI 스케일 · 배치 프리셋 (피드백 #1, #2, #5) ====================
  function openSettings() {
    renderSettingsSelections();
    $("settings-modal").classList.remove("hidden");
  }

  function renderSettingsSelections() {
    const tGrid = $("settings-theme-grid");
    tGrid.replaceChildren();
    for (const t of Data.THEMES) {
      tGrid.append(Data.el("button", {
        type: "button",
        className: "theme-card" + (Store.state.prefs.themeId === t.id ? " active" : ""),
        onClick: () => Store.savePrefs({ themeId: t.id }),
      },
      Data.el("div", { className: "theme-swatch-bar" },
        Data.el("span", { style: `background:${t.vars["--bg"]}` }),
        Data.el("span", { style: `background:${t.vars["--panel"]}` }),
        Data.el("span", { style: `background:${t.vars["--panel3"]}` }),
      ),
      Data.el("span", { className: "theme-card-name", text: `${t.name} (${t.mode === "dark" ? "다크" : "라이트"})` }),
      Data.el("span", { className: "theme-card-desc", text: t.desc })));
    }

    const pRow = $("settings-point-row");
    pRow.replaceChildren();
    for (const pc of Data.POINT_COLORS) {
      pRow.append(Data.el("button", {
        type: "button",
        className: "point-btn" + (Store.state.prefs.pointColor === pc.id ? " active" : ""),
        title: pc.name,
        style: `background:${pc.hex}`,
        onClick: () => Store.savePrefs({ pointColor: pc.id }),
      }));
    }

    $("chk-forge-theme-sync").checked = !!Store.state.prefs.forgeThemeSync;
    $("input-ui-scale").value = String(Store.state.prefs.uiScale || 100);
    $("val-ui-scale").textContent = `${Store.state.prefs.uiScale || 100}%`;

    const lGrid = $("settings-layout-grid");
    lGrid.replaceChildren();
    for (const lp of Data.LAYOUT_PRESETS) {
      lGrid.append(Data.el("button", {
        type: "button",
        className: "layout-card" + (Store.state.prefs.layoutPreset === lp.id ? " active" : ""),
        onClick: () => Store.savePrefs({ layoutPreset: lp.id }),
      },
      Data.el("strong", { text: lp.name }),
      Data.el("span", { className: "muted-text", text: lp.desc })));
    }
  }

  function wireSettingsModal() {
    const close = () => $("settings-modal").classList.add("hidden");
    $("btn-settings").addEventListener("click", openSettings);
    $("btn-close-settings").addEventListener("click", close);
    $("btn-done-settings").addEventListener("click", close);
    $("chk-forge-theme-sync").addEventListener("change", (e) => Store.savePrefs({ forgeThemeSync: e.target.checked }));
    $("input-ui-scale").addEventListener("input", (e) => {
      const v = Number(e.target.value);
      $("val-ui-scale").textContent = `${v}%`;
      Store.savePrefs({ uiScale: v });
    });
    $("btn-ui-scale-reset").addEventListener("click", () => Store.savePrefs({ uiScale: 100 }));
  }

  // ==================== 6. PSD 내보내기 미리보기 모달 ====================
  function openExportModal() {
    const body = $("export-modal-body");
    body.replaceChildren();
    const patches = Store.getPatches();
    const picked = patches.filter((p) => p.pickedCand);
    const missing = patches.filter((p) => !p.pickedCand);
    const S = Store.state.S;

    body.append(
      Data.el("p", { text: `전체 패치 ${patches.length}개 중 ★ 채택 ${picked.length}개가 마스크·스마트오브젝트 그룹 레이어로 PSD에 포함됩니다.` }),
      missing.length ? Data.el("p", { className: "muted-text", text: `미채택 패치 ${missing.length}개(${missing.map((m) => m.name).join(", ")})는 내보내기에서 건너뜁니다.` }) : null,
      Data.el("div", { className: "insp-card" },
        Data.el("strong", { text: "출력 PSD 레이어 구성 미리보기" }),
        Data.el("div", { className: "muted-text", text: "📁 Base_Original (원본 잠금 레이어)" }),
        ...picked.map((p) => Data.el("div", { className: "muted-text", text: `  └ 📁 ${p.name} [그룹 마스크 + 후보 c${String(p.pickedCand.n).padStart(2, "0")}]` })),
      ),
      S && S.export_last ? Data.el("p", { className: "muted-text", text: `마지막 Export: rev ${S.export_last.rev ?? "?"} · ${S.export_last.time || ""}` }) : null,
    );

    const dl = $("btn-export-download");
    const canDownload = Api.can("export_download") && S && S.export_last;
    dl.classList.toggle("hidden", !canDownload);
    if (canDownload) dl.href = Api.exportDownloadUrl(Store.state.root);

    $("btn-export-forge-save").classList.toggle("hidden", !(Api.can("forge_save") && S && S.export_last));
    $("export-modal").classList.remove("hidden");
  }

  function wireExportModal() {
    $("btn-export-psd").addEventListener("click", openExportModal);
    $("btn-close-export").addEventListener("click", () => $("export-modal").classList.add("hidden"));
    $("btn-export-run").addEventListener("click", async () => {
      const res = await Api.submitStage("export", { root: Store.state.root });
      if (res && res.pendingIntegration) {
        await Data.showDialogNotice(res.message);
      } else {
        Store.toast("PSD 내보내기 작업을 시작했습니다.", "ok");
        $("export-modal").classList.add("hidden");
        await Store.refreshAll(true);
      }
    });
    $("btn-export-forge-save").addEventListener("click", async () => {
      try {
        const res = await Api.saveToForge(Store.state.root);
        Store.toast(`Forge 자료에 저장되었습니다: ${(res.asset && res.asset.name) || "완료"}`, "ok");
      } catch (err) {
        Store.toast(`Forge 저장 실패: ${err.message}`, "err");
      }
    });
  }

  // ==================== 7. 패널 헤더 ⋮⋮ 드래그 도킹 (피드백 #5) ====================
  function wirePanelDragDocking() {
    let draggedPanel = null;
    document.querySelectorAll(".panel-drag-handle").forEach((h) => {
      h.addEventListener("dragstart", (e) => {
        draggedPanel = h.dataset.dragPanel;
        e.dataTransfer.setData("text/bmk-panel", draggedPanel);
      });
      h.addEventListener("dragend", () => {
        draggedPanel = null;
        document.querySelectorAll(".dock-drop-hover").forEach((n) => n.classList.remove("dock-drop-hover"));
      });
    });

    for (const id of ["left-dock", "right-dock"]) {
      const dock = $(id);
      dock.addEventListener("dragover", (e) => {
        if (!draggedPanel || dock.dataset.panel === draggedPanel) return;
        e.preventDefault();
        dock.classList.add("dock-drop-hover");
      });
      dock.addEventListener("dragleave", () => dock.classList.remove("dock-drop-hover"));
      dock.addEventListener("drop", (e) => {
        dock.classList.remove("dock-drop-hover");
        if (!draggedPanel || dock.dataset.panel === draggedPanel) return;
        e.preventDefault();
        const nextPreset = dock.dataset.panel === "right" ? "right-stack" : "left-stack";
        Store.savePrefs({ layoutPreset: nextPreset });
        Store.toast(`패널을 ${nextPreset === "right-stack" ? "우측으로 통합" : "좌측으로 통합"}했습니다. (설정에서 기본 3단 복원 가능)`, "ok");
      });
    }
  }

  return {
    init, mountProjectsHome, openSheetCut, openComparer, closeComparer,
    handleComparerKey, toggleHistoryDrawer, openSettings, openExportModal,
  };
})();
