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

  function isDefaultUiV2() {
    // #C27-3: ui_default 는 불리언이 아닌 문자열("v1" | "v2")이므로 DPEnv.can() 이 아니라 값으로 읽는다
    return !!(window.DPEnv && window.DPEnv.caps && window.DPEnv.caps.ui_default === "v2");
  }

  function syncDefaultUiState() {
    const defV2 = isDefaultUiV2();
    const betaPill = $("home-beta-pill");
    if (betaPill) betaPill.classList.toggle("hidden", defV2);
    if (!Store.state.root) {
      document.title = defV2 ? "Design Patch Studio" : "Design Patch Studio (v2 시험판)";
    }
  }

  function hydrateTopbarIcons() {
    const map = [
      ["btn-undo", "undo", 15],
      ["btn-redo", "redo", 15],
      ["btn-history-panel", "history", 15],
      ["btn-layout-cycle", "layout", 15],
      ["btn-settings", "settings", 15],
      ["btn-shortcuts", "help", 15],
      ["btn-home-refresh", "refresh", 15],
      ["home-drop-icon", "upload", 20],
      ["home-search-ico", "search", 14],
      ["home-view-grid", "gridView", 14],
      ["home-view-list", "listView", 14],
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
    if (nameEl && S) {
      // #C31-14: 서버가 title 을 주면 표시 이름 우선(플래그 무관), 없으면 폴더명(project)
      const nextName = S.title || S.project || Store.state.root;
      if (nameEl.textContent !== nextName) nameEl.textContent = nextName;
      nameEl.title = (S.title && S.project && S.title !== S.project)
        ? `${S.title} (폴더: ${S.project})`
        : nextName;
      if (Store.state.root) {
        const nextDocTitle = `${nextName} — Design Patch Studio`;
        if (document.title !== nextDocTitle) document.title = nextDocTitle;
      }
    }

    const pill = $("status-pill");
    if (pill) {
      const running = !!(S && S.running);
      const nextPill = running ? "생성 중…" : "작업 중";
      if (pill.textContent !== nextPill) pill.textContent = nextPill;
      pill.classList.toggle("running", running);
    }

    const syncPill = $("autosync-status");
    const syncTxt = $("autosync-text");
    if (syncPill) {
      syncPill.classList.toggle("syncing", Store.state.syncState === "syncing");
      syncPill.classList.toggle("err", Store.state.syncState === "err");
    }
    if (syncTxt) {
      const nextMsg = Store.state.syncMsg || "동기화됨";
      if (syncTxt.textContent !== nextMsg) syncTxt.textContent = nextMsg;
    }

    // #C18-3 · R2-4: 생성 흐름·AI 프롬프트 작성·모델 설정 저장 진행 중에는 상단 실행 취소/다시 실행 버튼도 잠근다
    const histLocked = !!(Store.state.runFlowBusy || Store.state.assistBusy || Store.state.cropGenBusy);
    const btnUndo = $("btn-undo"), btnRedo = $("btn-redo");
    if (btnUndo) btnUndo.disabled = histLocked || Store.state.histIndex < 0;
    if (btnRedo) btnRedo.disabled = histLocked || Store.state.histIndex >= Store.state.history.length - 1;

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
      Overlays.openNewProjectModal(list[0], list.slice(1));
      return;
    }
    const doUploads = async () => {
      for (const f of list) {
        try {
          await Api.uploadSheet(Store.state.root, f, Store.state.R?.rev ?? null);
          Store.toast(`"${f.name}" 레퍼런스 시트를 추가했습니다.`, "ok");
        } catch (err) {
          Store.toast(`업로드 실패: ${err.message}`, "err");
        }
      }
    };
    if (typeof Store.trackRefWrite === "function") await Store.trackRefWrite(doUploads());
    else await doUploads();
    await (typeof Store.refreshAfterRefWrite === "function" ? Store.refreshAfterRefWrite() : Store.refreshAll(true));
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

    // 톱바 버튼 (#C27-2: v2 내부 목록 이동 시 ui=v2 유지)
    const btnProj = $("btn-projects");
    if (btnProj) btnProj.addEventListener("click", () => {
      const u = new URL(location.href);
      u.searchParams.delete("root");
      u.searchParams.set("ui", "v2");
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

    // 페이지 전역 파일 드롭 가드 (모달·Forge 자료함·대화상자·홈 빠른 드롭존 안에서는 전역 드롭이 가로채지 않는다 — #C40-2)
    let dragCounter = 0;
    const dropOv = $("global-drop-overlay");
    const isExternalOverlayOpen = () => !!(
      (window.DPDialog && window.DPDialog.open)
      || Array.from(document.querySelectorAll("body > .overlay:not(.hidden), [aria-modal='true']:not(.hidden)"))
        .some((el) => !el.closest || !el.closest("#modal-backdrop"))
    );
    const isModalOpen = () => {
      const bd = $("modal-backdrop");
      if (bd && !bd.classList.contains("hidden")) return true;
      return isExternalOverlayOpen();
    };
    window.addEventListener("dragenter", (e) => {
      if (e.dataTransfer?.types?.includes("Files")) {
        e.preventDefault();
        if (isModalOpen()) return;
        if (!Store.state.root && e.target && e.target.closest && e.target.closest("#home-quick-drop")) return;
        dragCounter += 1;
        if (dropOv) {
          dropOv.classList.toggle("home-mode", !Store.state.root);
          dropOv.classList.remove("hidden");
        }
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
        // #C40-2: 모달 또는 Forge 자료함/대화상자 오버레이가 떠 있을 때는 전역 드롭이 열려 있는 프로젝트에 시트를 올리지 않게 차단한다
        if (isModalOpen()) return;
        if (!Store.state.root && e.target && e.target.closest && e.target.closest("#home-quick-drop")) return;
        const zone = e.target && e.target.closest ? e.target.closest(".drop-zone") : null;
        const role = !Store.state.root ? "target" : (zone ? zone.dataset.dropRole : "ref");
        handleUploadFiles(e.dataTransfer.files, role);
      }
    });
    document.querySelectorAll(".drop-zone").forEach((z) => {
      z.addEventListener("dragenter", () => z.classList.add("over"));
      z.addEventListener("dragleave", () => z.classList.remove("over"));
      z.addEventListener("drop", () => z.classList.remove("over"));
    });

    // 클립보드 붙여넣기 (홈: 새 프로젝트 타깃 · 작업 화면: 레퍼런스 시트 추가)
    window.addEventListener("paste", (e) => {
      if (isTyping(document.activeElement) || isModalOpen()) return;
      const items = Array.from(e.clipboardData?.items || []);
      const imgItem = items.find((it) => it.type && it.type.startsWith("image/"));
      if (imgItem) {
        const f = imgItem.getAsFile();
        if (f) handleUploadFiles([f], Store.state.root ? "ref" : "target");
      }
    });

    // 전역 포토샵 표준 단축키
    window.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        // Forge 자료함·공용 확인 상자(body > .overlay)가 떠 있으면 각 오버레이 자체 Esc 처리에 맡긴다
        if (isExternalOverlayOpen()) return;
        const bd = $("modal-backdrop");
        if (bd && !bd.classList.contains("hidden")) { Overlays.closeModal(); return; }
        if (Overlays.cmpState.open) { Overlays.closeCompareOverlay(); return; }
        if (Overlays.sheetState.open) { Overlays.closeSheetOverlay(); return; }
        const pop = $("model-popover");
        if (pop && !pop.classList.contains("hidden")) { pop.classList.add("hidden"); return; }
        const spop = $("snap-popover");
        if (spop && !spop.classList.contains("hidden")) { spop.classList.add("hidden"); return; }
        // 버그 2 영구 해결: Esc 로 선택 완전 해제 → 밝은 원본 화면 즉시 확인!
        if (Store.state.selectedIds.size > 0 || Store.state.primaryId) {
          Store.clearSelection();
          return;
        }
        return;
      }

      // #C40-1: 모달·Forge 자료함·대화상자가 열려 있는 동안에는 배경 전역 단축키(?, Ctrl+Z, 도구 전환 등)를 모두 차단한다
      if (isModalOpen()) return;

      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") {
        if (Store.state.root && !isTyping(document.activeElement)) {
          e.preventDefault();
          if (e.shiftKey) Store.redoHistory();
          else Store.undoHistory();
        }
        return;
      }

      if (isTyping(document.activeElement)) return;

      // 프로젝트 홈 전용 단축키: '/' 로 검색창 포커스 (작업 화면 단축키는 홈에서 비활성)
      if (!Store.state.root) {
        if (e.key === "/" && !e.ctrlKey && !e.metaKey && !e.altKey) {
          const inp = $("home-search-input");
          if (inp && !isModalOpen()) {
            e.preventDefault();
            inp.focus();
            inp.select();
          }
        }
        return;
      }

      // 레퍼런스 시트 오리기 오버레이 열림 시: 방향키 미세 이동 + Space 팬
      if (Overlays.sheetState.open) {
        if (e.key === " ") {
          e.preventDefault();
          Canvas.view.spaceDown = true;
          return;
        }
        const arrowDelta = {
          ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1],
        }[e.key];
        if (arrowDelta) {
          e.preventDefault();
          const step = e.shiftKey ? 10 : 1;
          Overlays.nudgeSheetCut(arrowDelta[0] * step, arrowDelta[1] * step);
        }
        return;
      }

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

      // 메인 캔버스 선택 영역 방향키 미세 이동 (현재 snapStep 연동 · Shift 큰 단위 · Ctrl 1px)
      const mainArrow = {
        ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1],
      }[e.key];
      if (mainArrow) {
        const activeStep = Canvas.getActiveSnapStep(e);
        const delta = activeStep <= 1
          ? (e.shiftKey ? 16 : 1)
          : (e.shiftKey ? activeStep : Math.min(16, activeStep));
        if (Canvas.nudgeSelectedPatch(mainArrow[0] * delta, mainArrow[1] * delta, activeStep)) {
          e.preventDefault();
          return;
        }
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

  // #C36-6: 새 프로젝트 생성 응답의 notes(예: PSD 투명 영역 변환 알림)를 v1 과 같은 sessionStorage 키로 소비해 작업 화면에 표시
  function consumeNewProjectNotes(root) {
    if (!root) return [];
    const url = new URL(location.href);
    if (url.searchParams.get("new") !== "1") return [];
    url.searchParams.delete("new");
    try { window.history.replaceState(null, "", url.pathname + url.search + url.hash); } catch (_) {}
    let notes = [];
    try {
      const key = "bmk-dp-new:" + root;
      const raw = window.sessionStorage.getItem(key);
      if (raw) {
        notes = JSON.parse(raw);
        window.sessionStorage.removeItem(key);
      }
    } catch (_) { /* 저장소 차단 시 무시 */ }
    return Array.isArray(notes) ? notes.filter((n) => typeof n === "string" && n.trim()) : [];
  }

  function showStudioNotesBanner(notes) {
    const banner = $("studio-notes-banner");
    const txt = $("studio-notes-text");
    const closeBtn = $("btn-studio-notes-close");
    if (!banner || !txt) return;
    const list = Array.isArray(notes) ? notes.filter((n) => typeof n === "string" && n.trim()) : [];
    if (!list.length) {
      banner.classList.add("hidden");
      txt.textContent = "";
      return;
    }
    txt.textContent = list.join(" · ");
    banner.classList.remove("hidden");
    if (closeBtn && !closeBtn.dataset.boundNotes) {
      closeBtn.dataset.boundNotes = "1";
      closeBtn.addEventListener("click", () => {
        banner.classList.add("hidden");
        txt.textContent = "";
      });
    }
  }

  async function boot() {
    const bootFb = $("v2-boot-fallback");
    const bootMsg = $("v2-boot-fallback-msg");
    try {
      // #C36-7: env.js 가 로드되지 않았거나 실행 환경 판정이 완료되지 않았으면 비상 탈출 상자를 유지하고 목록/작업 화면을 열지 않는다
      if (!window.DPEnv || !window.DPEnv.ready) {
        if (bootMsg) bootMsg.textContent = "환경 설정(env.js)을 불러오지 못했습니다. 새로고침하거나 기존 화면으로 이동해 주세요.";
        if (bootFb) bootFb.classList.remove("hidden");
        return;
      }
      await window.DPEnv.ready;
      if (typeof window.DPEnv.serverMode !== "boolean") {
        if (bootMsg) bootMsg.textContent = "실행 환경을 확인할 수 없습니다. 새로고침하거나 기존 화면으로 이동해 주세요.";
        if (bootFb) bootFb.classList.remove("hidden");
        return;
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
      syncDefaultUiState();

      if (!root) {
        $("projects-view").classList.remove("hidden");
        $("studio-view").classList.add("hidden");
        if (bootFb) bootFb.classList.add("hidden");
        await Overlays.renderProjectsHome();
      } else {
        $("projects-view").classList.add("hidden");
        $("studio-view").classList.remove("hidden");
        if (bootFb) bootFb.classList.add("hidden");
        const newNotes = consumeNewProjectNotes(root);
        showStudioNotesBanner(newNotes);
        await Store.refreshAll(true);
        // #C26-5 · #C30-5: 새 프로젝트 첫 진입(가져오기 전 — S.canvas 없음) 초기 반영도 공용 모듈 Api.applyChecked 로 통일하고,
        // 계획 잠금 경합(busy)으로 끝나며 캔버스가 여전히 없으면 딱 1회 더 재시도한다
        if (Store.state.S && !Store.state.S.canvas && !Store.state.S.running && Api.submitAdapter()) {
          Store.toast("새 프로젝트 원화를 불러오는 중입니다 (무료 초기 반영)…");
          try {
            let applied = await Api.applyChecked(root);
            await Store.refreshAll(true);
            if (applied && !applied.ok && applied.reason === "busy"
                && Store.state.S && !Store.state.S.canvas && !Store.state.S.running) {
              Store.toast("서버가 바빠 초기 반영을 한 번 더 시도합니다…");
              await new Promise((r) => setTimeout(r, 500));
              // #C33-5: 500ms 대기 중 다른 창이 초기 반영을 끝냈을 수 있으므로 /state 를 먼저 다시 읽어 성공 응답에서 확인한다
              const stateRefreshed = await Store.refreshAll(true);
              if (stateRefreshed && Store.state.S && !Store.state.S.canvas && !Store.state.S.running) {
                applied = await Api.applyChecked(root);
                await Store.refreshAll(true);
              }
            }
            if (applied && !applied.ok && !(Store.state.S && (Store.state.S.canvas || Store.state.S.running))) {
              if (applied.reason === "cancelled") {
                Store.toast(applied.error || "초기 반영을 취소했습니다.");
              } else {
                Store.toast(
                  applied.error || applied.message || "초기 반영 작업을 완료하지 못했습니다 — [합성 적용]으로 다시 확인해 주세요.",
                  "err"
                );
              }
            }
          } catch (err) {
            Store.toast(`초기 반영 실패: ${(err && err.message) || "알 수 없는 오류"}`, "err");
          }
        }
        Store.startPolling();
        updateTopbarState();
      }
    } catch (err) {
      console.error("v2 boot error:", err);
      if (bootMsg) bootMsg.textContent = "화면을 여는 중 오류가 발생했습니다.";
      if (bootFb) bootFb.classList.remove("hidden");
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
