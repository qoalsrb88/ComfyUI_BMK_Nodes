// BMK Design Patch Studio v2 — 앱 진입점 · 톱바 아이콘/제어 · 전역 단축키 · 파일 드롭/붙여넣기 · 부트스트랩.
"use strict";

(() => {
  const Data = window.DPV2Data;
  const Api = window.DPV2Api;
  const Store = window.DPV2Store;
  const Canvas = window.DPV2Canvas;
  const Inspector = window.DPV2Inspector;
  const Overlays = window.DPV2Overlays;

  const $ = (id) => document.getElementById(id);

  function isTyping(el) {
    if (!el) return false;
    const t = el.tagName;
    return t === "INPUT" || t === "TEXTAREA" || t === "SELECT" || el.isContentEditable;
  }

  function hydrateTopbarIcons() {
    const map = [
      ["btn-undo", "undo", 15],
      ["btn-redo", "redo", 15],
      ["btn-history-panel", "history", 15],
      ["btn-layout-cycle", "layout", 15],
      ["btn-settings", "settings", 15],
      ["btn-shortcuts", "help", 15],
    ];
    for (const [id, ico, sz] of map) {
      const btn = $(id);
      if (btn) {
        btn.innerHTML = "";
        btn.append(Data.icon(ico, sz));
      }
    }
  }

  function updateTopbarState() {
    const S = Store.state.S;
    const nameEl = $("project-name-label");
    if (nameEl && S) nameEl.textContent = S.project || Store.state.root;

    const pill = $("status-pill");
    if (pill) {
      const running = !!(S && S.running);
      pill.textContent = running ? "생성 중…" : "작업 중";
      pill.classList.toggle("running", running);
    }

    const syncPill = $("autosync-status");
    const syncTxt = $("autosync-text");
    if (syncPill) {
      syncPill.classList.toggle("syncing", Store.state.syncState === "syncing");
      syncPill.classList.toggle("err", Store.state.syncState === "err");
    }
    if (syncTxt) syncTxt.textContent = Store.state.syncMsg || "동기화됨";

    const btnUndo = $("btn-undo"), btnRedo = $("btn-redo");
    if (btnUndo) btnUndo.disabled = Store.state.histIndex < 0;
    if (btnRedo) btnRedo.disabled = Store.state.histIndex >= Store.state.history.length - 1;

    const vf = Store.state.viewFlags;
    const bOrig = $("btn-view-original");
    const bSplit = $("btn-view-split");
    const bGuides = $("btn-view-guides");
    const bSeams = $("btn-view-seams");
    if (bOrig) bOrig.classList.toggle("on", !!vf.beforeHeld);
    if (bSplit) bSplit.classList.toggle("on", !!vf.splitOn);
    if (bGuides) bGuides.classList.toggle("on", !!vf.guidesOn);
    if (bSeams) bSeams.classList.toggle("on", !!vf.seamsOn);
  }

  function cycleLayoutPreset() {
    const idx = Data.LAYOUT_PRESETS.findIndex((p) => p.id === Store.state.prefs.layoutPreset);
    const next = Data.LAYOUT_PRESETS[(idx + 1) % Data.LAYOUT_PRESETS.length];
    Store.savePrefs({ layoutPreset: next.id });
    Store.toast(`패널 배치: ${next.name}`);
  }

  async function handleUploadFiles(files, role) {
    const list = Array.from(files || []);
    if (!list.length) return;
    if (role === "target" || !Store.state.root) {
      Overlays.openNewProjectModal(list[0]);
      return;
    }
    for (const f of list) {
      try {
        await Api.uploadSheet(Store.state.root, f, Store.state.R?.rev ?? null);
        Store.toast(`"${f.name}" 레퍼런스 시트를 추가했습니다.`, "ok");
      } catch (err) {
        Store.toast(`업로드 실패: ${err.message}`, "err");
      }
    }
    await Store.refreshAll(true);
    if (Overlays.sheetState.open) {
      Overlays.openSheetOverlay();
    }
  }

  function bindGlobalEvents() {
    // 홈 화면 버튼
    const btnHomeNew = $("btn-home-new");
    if (btnHomeNew) btnHomeNew.addEventListener("click", () => Overlays.openNewProjectModal());
    const btnHomeSet = $("btn-home-settings");
    if (btnHomeSet) btnHomeSet.addEventListener("click", Overlays.openSettingsModal);
    const btnHomeV1 = $("btn-home-switch-v1");
    if (btnHomeV1) btnHomeV1.addEventListener("click", () => { location.href = Data.buildSwitchUrl(false); });

    // 톱바 버튼
    const btnProj = $("btn-projects");
    if (btnProj) btnProj.addEventListener("click", () => {
      const u = new URL(location.href);
      u.searchParams.delete("root");
      location.href = u.pathname + u.search;
    });
    const btnUndo = $("btn-undo");
    if (btnUndo) btnUndo.addEventListener("click", () => Store.undoHistory());
    const btnRedo = $("btn-redo");
    if (btnRedo) btnRedo.addEventListener("click", () => Store.redoHistory());
    const btnHist = $("btn-history-panel");
    if (btnHist) btnHist.addEventListener("click", Overlays.toggleHistoryDrawer);

    const btnOrig = $("btn-view-original");
    if (btnOrig) btnOrig.addEventListener("click", () => {
      Store.state.viewFlags.beforeHeld = !Store.state.viewFlags.beforeHeld;
      updateTopbarState();
      Canvas.renderSplitView();
      Canvas.renderCandidateOverlay();
    });
    const btnSplit = $("btn-view-split");
    if (btnSplit) btnSplit.addEventListener("click", () => {
      Store.state.viewFlags.splitOn = !Store.state.viewFlags.splitOn;
      Store.state.viewFlags.beforeHeld = false;
      updateTopbarState();
      Canvas.renderSplitView();
      Canvas.renderCandidateOverlay();
    });
    const btnGuides = $("btn-view-guides");
    if (btnGuides) btnGuides.addEventListener("click", () => {
      Store.state.viewFlags.guidesOn = !Store.state.viewFlags.guidesOn;
      updateTopbarState();
      Canvas.renderOverlaySvg();
    });
    const btnSeams = $("btn-view-seams");
    if (btnSeams) btnSeams.addEventListener("click", () => {
      Store.state.viewFlags.seamsOn = !Store.state.viewFlags.seamsOn;
      updateTopbarState();
      Canvas.renderOverlaySvg();
    });

    const btnLay = $("btn-layout-cycle");
    if (btnLay) btnLay.addEventListener("click", cycleLayoutPreset);
    const btnSet = $("btn-settings");
    if (btnSet) btnSet.addEventListener("click", Overlays.openSettingsModal);
    const btnSc = $("btn-shortcuts");
    if (btnSc) btnSc.addEventListener("click", Overlays.openShortcutsModal);
    const btnV1 = $("btn-switch-v1");
    if (btnV1) btnV1.addEventListener("click", () => { location.href = Data.buildSwitchUrl(false); });
    const btnExport = $("btn-export-psd");
    if (btnExport) btnExport.addEventListener("click", Overlays.openExportModal);

    // 숨김 파일 입력 핸들러
    const fTarget = $("hidden-file-target");
    if (fTarget) fTarget.addEventListener("change", (e) => { handleUploadFiles(e.target.files, "target"); e.target.value = ""; });
    const fRef = $("hidden-file-ref");
    if (fRef) fRef.addEventListener("change", (e) => { handleUploadFiles(e.target.files, "ref"); e.target.value = ""; });
    const fSheet = $("hidden-file-sheet");
    if (fSheet) fSheet.addEventListener("change", (e) => { handleUploadFiles(e.target.files, "ref"); e.target.value = ""; });

    // 페이지 전역 파일 드롭 가드
    let dragCounter = 0;
    const dropOv = $("global-drop-overlay");
    window.addEventListener("dragenter", (e) => {
      if (e.dataTransfer?.types?.includes("Files")) {
        e.preventDefault();
        dragCounter += 1;
        if (dropOv) dropOv.classList.remove("hidden");
      }
    });
    window.addEventListener("dragover", (e) => {
      if (e.dataTransfer?.types?.includes("Files")) e.preventDefault();
    });
    window.addEventListener("dragleave", (e) => {
      if (e.dataTransfer?.types?.includes("Files")) {
        dragCounter = Math.max(0, dragCounter - 1);
        if (dragCounter === 0 && dropOv) dropOv.classList.add("hidden");
      }
    });
    window.addEventListener("drop", (e) => {
      if (e.dataTransfer?.types?.includes("Files")) {
        e.preventDefault();
        dragCounter = 0;
        if (dropOv) dropOv.classList.add("hidden");
        const zone = e.target.closest(".drop-zone");
        const role = zone ? zone.dataset.dropRole : "ref";
        handleUploadFiles(e.dataTransfer.files, role);
      }
    });
    document.querySelectorAll(".drop-zone").forEach((z) => {
      z.addEventListener("dragenter", () => z.classList.add("over"));
      z.addEventListener("dragleave", () => z.classList.remove("over"));
      z.addEventListener("drop", () => z.classList.remove("over"));
    });

    // 클립보드 붙여넣기 (이미지 → 레퍼런스 시트 추가)
    window.addEventListener("paste", (e) => {
      if (isTyping(document.activeElement)) return;
      const items = Array.from(e.clipboardData?.items || []);
      const imgItem = items.find((it) => it.type && it.type.startsWith("image/"));
      if (imgItem) {
        const f = imgItem.getAsFile();
        if (f) handleUploadFiles([f], "ref");
      }
    });

    // 전역 포토샵 표준 단축키
    window.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        const bd = $("modal-backdrop");
        if (bd && !bd.classList.contains("hidden")) { Overlays.closeModal(); return; }
        if (Overlays.cmpState.open) { Overlays.closeCompareOverlay(); return; }
        if (Overlays.sheetState.open) { Overlays.closeSheetOverlay(); return; }
        const pop = $("model-popover");
        if (pop && !pop.classList.contains("hidden")) { pop.classList.add("hidden"); return; }
        // 버그 2 영구 해결: Esc 로 선택 완전 해제 → 밝은 원본 화면 즉시 확인!
        if (Store.state.selectedIds.size > 0 || Store.state.primaryId) {
          Store.clearSelection();
          return;
        }
        return;
      }

      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") {
        if (!isTyping(document.activeElement)) {
          e.preventDefault();
          if (e.shiftKey) Store.redoHistory();
          else Store.undoHistory();
        }
        return;
      }

      if (isTyping(document.activeElement)) return;

      // 풀스크린 정밀 비교 단축키
      if (Overlays.cmpState.open) {
        if (e.key === "Tab") {
          e.preventDefault();
          $("co-toggle-blink").click();
        } else if (["1", "2", "3", "4"].includes(e.key)) {
          const p = Store.primaryPatch();
          const c = p && p.candidates[Number(e.key) - 1];
          if (c) Overlays.openCompareOverlay(c.key);
        }
        return;
      }

      const k = e.key.toLowerCase();
      if (e.key === " ") {
        e.preventDefault();
        Canvas.view.spaceDown = true;
        const vp = $("canvas-viewport");
        if (vp) vp.classList.add("mode-pan");
      } else if (k === "q" || e.key === "Tab") {
        e.preventDefault();
        $("btn-view-original").click();
      } else if (k === "s") {
        $("btn-view-split").click();
      } else if (k === "o") {
        $("btn-view-guides").click();
      } else if (k === "n") {
        $("btn-view-seams").click();
      } else if (k === "h") {
        Overlays.toggleHistoryDrawer();
      } else if (k === "v") {
        Canvas.setTool("select");
      } else if (k === "m") {
        Canvas.setTool("rect");
      } else if (k === "b") {
        Canvas.setTool("brush");
      } else if (k === "e") {
        Canvas.setTool("eraser");
      } else if (k === "r") {
        Overlays.openSheetOverlay();
      } else if (k === "0") {
        Canvas.fitToViewport();
      } else if (k === "?" || (e.shiftKey && e.key === "/")) {
        Overlays.openShortcutsModal();
      } else if (k === "a" || k === "d") {
        const list = Store.getPatches();
        if (!list.length) return;
        const idx = list.findIndex((p) => p.id === Store.state.primaryId);
        const next = (idx + (k === "d" ? 1 : -1) + list.length) % list.length;
        Store.selectPatch(list[next].id);
      }
    });

    window.addEventListener("keyup", (e) => {
      if (e.key === " ") {
        Canvas.view.spaceDown = false;
        const vp = $("canvas-viewport");
        if (vp) vp.className = `canvas-viewport mode-${Store.state.tool}`;
      }
    });

    Store.subscribe(() => updateTopbarState());
  }

  async function boot() {
    if (window.DPEnv && window.DPEnv.ready) {
      try { await window.DPEnv.ready; } catch (_) {}
    }

    Store.loadPrefs();
    Store.applyPrefsToDom();
    Store.initForgeThemeListener();

    hydrateTopbarIcons();
    Canvas.bindEvents();
    Inspector.bindEvents();
    Overlays.bindEvents();
    bindGlobalEvents();

    const params = new URLSearchParams(location.search);
    const root = params.get("root") || "";
    Store.state.root = root;

    if (!root) {
      $("projects-view").classList.remove("hidden");
      $("studio-view").classList.add("hidden");
      await Overlays.renderProjectsHome();
    } else {
      $("projects-view").classList.add("hidden");
      $("studio-view").classList.remove("hidden");
      await Store.refreshAll(true);
      Store.startPolling();
      updateTopbarState();
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
