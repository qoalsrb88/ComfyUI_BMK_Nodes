// BMK Design Patch Studio v2 — 앱 부트스트랩 · 라우팅 · 전역 단축키 · 붙여넣기/드롭 처리.
"use strict";

(() => {
  const Data = window.DPV2Data;
  const Api = window.DPV2Api;
  const Store = window.DPV2Store;
  const Canvas = window.DPV2Canvas;
  const Inspector = window.DPV2Inspector;
  const Overlays = window.DPV2Overlays;

  function $(id) { return document.getElementById(id); }

  function isTypingTarget(el) {
    if (!el || !el.tagName) return false;
    const tag = el.tagName.toUpperCase();
    return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || el.isContentEditable;
  }

  function wireTopbarAndGlobalEvents() {
    $("btn-back-projects").addEventListener("click", () => {
      const url = new URL(location.href);
      url.searchParams.delete("root");
      url.searchParams.set("ui", "v2");
      location.assign(url.pathname + url.search);
    });

    // 계약 §7.4 D2: 같은 프레임 안에서만 v1 ↔ v2 전환, root 등 기존 쿼리 유지
    $("btn-switch-v1").addEventListener("click", () => {
      location.assign(Data.buildSwitchUrl(false));
    });

    $("btn-undo").addEventListener("click", () => Store.undoHistory());
    $("btn-redo").addEventListener("click", () => Store.redoHistory());

    // Before (Q) 버튼 홀드
    const holdBtn = $("btn-hold-before");
    const setBefore = (on) => {
      if (Store.state.viewFlags.beforeHeld === on) return;
      Store.state.viewFlags.beforeHeld = on;
      holdBtn.classList.toggle("on", on);
      Store.emit("view");
    };
    holdBtn.addEventListener("pointerdown", () => setBefore(true));
    holdBtn.addEventListener("pointerup", () => setBefore(false));
    holdBtn.addEventListener("pointerleave", () => setBefore(false));

    $("btn-split-compare").addEventListener("click", () => {
      Store.state.viewFlags.splitOn = !Store.state.viewFlags.splitOn;
      $("btn-split-compare").classList.toggle("on", Store.state.viewFlags.splitOn);
      Store.emit("view");
    });

    $("btn-toggle-seams").addEventListener("click", () => {
      Store.state.viewFlags.seamsOn = !Store.state.viewFlags.seamsOn;
      $("btn-toggle-seams").classList.toggle("on", Store.state.viewFlags.seamsOn);
      Store.emit("view");
    });

    $("btn-drain").addEventListener("click", async () => {
      if (!(await Data.askDialogConfirm("진행 중인 호출 이후 새 생성 호출을 멈출까요? 이미 보낸 호출은 끝까지 저장됩니다.", { ok: "새 호출 멈춤" }))) return;
      try {
        await Api.drainRun(Store.state.root);
        Store.toast("새 호출 멈춤을 요청했습니다.", "warn");
        await Store.refreshAll(true);
      } catch (err) {
        Store.toast(`멈춤 요청 실패: ${err.message}`, "err");
      }
    });

    // 전역 키보드 단축키 (포토샵 기준 일관 맵)
    document.addEventListener("keydown", async (e) => {
      if (window.DPDialog && window.DPDialog.open) return;
      if (Overlays.handleComparerKey(e)) return;

      if (e.key === "Escape") {
        for (const id of ["sheet-overlay", "settings-modal", "export-modal", "new-project-modal", "model-popover"]) {
          const node = $(id);
          if (node && !node.classList.contains("hidden")) {
            node.classList.add("hidden");
            return;
          }
        }
        return;
      }

      if (isTypingTarget(e.target)) return;

      if ((e.ctrlKey || e.metaKey) && !e.shiftKey && e.code === "KeyZ") {
        e.preventDefault();
        Store.undoHistory();
        return;
      }
      if ((e.ctrlKey || e.metaKey) && (e.code === "KeyY" || (e.shiftKey && e.code === "KeyZ"))) {
        e.preventDefault();
        Store.redoHistory();
        return;
      }
      if ((e.ctrlKey || e.metaKey) && e.code === "KeyE") {
        e.preventDefault();
        Overlays.openExportModal();
        return;
      }
      if (e.ctrlKey || e.metaKey || e.altKey) return;

      switch (e.code) {
        case "Space":
          e.preventDefault();
          Canvas.setSpaceHeld(true);
          break;
        case "KeyV":
          Canvas.setTool("select");
          break;
        case "KeyM":
          Canvas.setTool("rect");
          break;
        case "KeyB":
          Canvas.setTool("brush");
          break;
        case "KeyE":
          Canvas.setTool("eraser");
          break;
        case "KeyQ":
          if (!e.repeat) setBefore(true);
          break;
        case "KeyS":
          if (!e.repeat) $("btn-split-compare").click();
          break;
        case "KeyN":
          if (!e.repeat) $("btn-toggle-seams").click();
          break;
        case "KeyH":
          if (!e.repeat) Overlays.toggleHistoryDrawer();
          break;
        case "KeyF":
          if (!e.repeat) Canvas.fitCrop();
          break;
        case "Digit0":
          if (!e.repeat) Canvas.fitAll(true);
          break;
        case "Digit1":
          if (!e.repeat) Canvas.zoomTo(1);
          break;
        case "KeyL":
          if (!e.repeat) $("btn-fb-lock").click();
          break;
        case "Delete":
          if (!e.repeat) $("btn-fb-delete").click();
          break;
        default:
          break;
      }
    });

    document.addEventListener("keyup", (e) => {
      if (e.code === "Space") Canvas.setSpaceHeld(false);
      if (e.code === "KeyQ") setBefore(false);
    });
    window.addEventListener("blur", () => {
      Canvas.setSpaceHeld(false);
      setBefore(false);
    });

    // 전역 파일 드래그 앤 드롭 (스튜디오 화면에서 바로 레퍼런스 시트 올리기)
    let dragDepth = 0;
    const dropOverlay = $("global-drop-overlay");
    window.addEventListener("dragenter", (e) => {
      if (!Store.state.root || !e.dataTransfer || !e.dataTransfer.types.includes("Files")) return;
      if (!$("new-project-modal").classList.contains("hidden")) return;
      dragDepth += 1;
      dropOverlay.classList.remove("hidden");
    });
    window.addEventListener("dragover", (e) => {
      if (!Store.state.root || !e.dataTransfer || !e.dataTransfer.types.includes("Files")) return;
      if (!$("new-project-modal").classList.contains("hidden")) return;
      e.preventDefault();
    });
    window.addEventListener("dragleave", () => {
      dragDepth = Math.max(0, dragDepth - 1);
      if (dragDepth === 0) dropOverlay.classList.add("hidden");
    });
    window.addEventListener("drop", async (e) => {
      dragDepth = 0;
      dropOverlay.classList.add("hidden");
      if (!Store.state.root || !$("new-project-modal").classList.contains("hidden")) return;
      const files = [...((e.dataTransfer && e.dataTransfer.files) || [])];
      if (!files.length) return;
      e.preventDefault();
      for (const f of files) {
        try {
          await Api.uploadSheet(Store.state.root, f, Store.state.R && Store.state.R.rev);
          Store.toast(`레퍼런스 시트 "${f.name}" 올리기 완료`, "ok");
        } catch (err) {
          Store.toast(`${f.name} 올리기 실패: ${err.message}`, "err");
        }
      }
      Inspector.switchLeftTab("refs");
      await Store.refreshAll(true);
    });

    // 계약 §7.1 A5: 샌드박스 호환을 위해 표준 paste 이벤트로 클립보드 그림 올리기
    window.addEventListener("paste", async (e) => {
      if (!Store.state.root || isTypingTarget(e.target)) return;
      const items = [...((e.clipboardData && e.clipboardData.items) || [])];
      const imgItem = items.find((it) => it.kind === "file" && /^image\//.test(it.type));
      if (!imgItem) return;
      const blob = imgItem.getAsFile();
      if (!blob) return;
      e.preventDefault();
      const file = new File([blob], `clipboard_${Date.now()}.png`, { type: blob.type || "image/png" });
      try {
        await Api.uploadSheet(Store.state.root, file, Store.state.R && Store.state.R.rev);
        Store.toast("클립보드 이미지를 레퍼런스 시트에 추가했습니다.", "ok");
        Inspector.switchLeftTab("refs");
        await Store.refreshAll(true);
      } catch (err) {
        Store.toast(`클립보드 추가 실패: ${err.message}`, "err");
      }
    });
  }

  async function main() {
    if (window.DPEnv && window.DPEnv.ready) {
      await window.DPEnv.ready;
    }
    Store.loadPrefs();
    Store.applyPrefsToDom();
    Store.initForgeThemeListener();

    Canvas.init();
    Inspector.init();
    Overlays.init();

    const params = new URLSearchParams(location.search);
    const root = params.get("root") || "";

    if (!root) {
      await Overlays.mountProjectsHome();
      return;
    }

    if (!/^[0-9a-f]{12}$/.test(root)) {
      await Data.showDialogNotice("프로젝트 식별자(root) 형식이 올바르지 않습니다. 프로젝트 목록으로 이동합니다.");
      const url = new URL(location.href);
      url.searchParams.delete("root");
      location.assign(url.pathname + url.search);
      return;
    }

    Store.state.root = root;
    $("projects-view").classList.add("hidden");
    $("studio-view").classList.remove("hidden");
    wireTopbarAndGlobalEvents();

    await Store.refreshAll(true);
    Store.startPolling();
  }

  main();
})();
