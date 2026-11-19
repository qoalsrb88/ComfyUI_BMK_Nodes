// BMK Design Patch Studio v2 — 좌측 패치/레퍼런스 독 · 우측 3탭 인스펙터 · 유니티 복수선택 · 모델 가이드 · 실행 독.
"use strict";

window.DPV2Inspector = (() => {
  const Data = window.DPV2Data;
  const Api = window.DPV2Api;
  const Store = window.DPV2Store;

  const MAX_ASSIST_BATCH = 8; // 계약 §7.3 C1: 클릭당 최대 8패치, 동시 1건, 자동 재시도 없음
  let modelPopoverMode = "single"; // single | project | batch

  function $(id) { return document.getElementById(id); }

  function init() {
    wireLeftDock();
    wireRightTabsAndDock();
    wireModelPopover();

    Store.subscribe((reason) => {
      if (["state", "selection", "prefs", "mask", "sync", "tool"].includes(reason)) {
        renderAll();
      }
    });
  }

  function renderAll() {
    renderHeaderSync();
    renderLeftPatches();
    renderLeftRefs();
    renderMultiBanner();
    renderTabInstruct();
    renderTabReview();
    renderTabRetouch();
    renderRunDock();
  }

  function renderHeaderSync() {
    const S = Store.state.S;
    if (S) {
      $("project-title").textContent = S.project || Store.state.root;
      $("project-rev-pill").textContent = `rev ${S.rev ?? "-"}`;
      document.title = `${S.project || "프로젝트"} · 디자인 패치 스튜디오 v2`;
      $("btn-drain").classList.toggle("hidden", !S.running);
    }
    const syncWrap = $("autosync-status");
    syncWrap.className = "autosync-status" + (Store.state.syncState === "syncing" ? " syncing" : Store.state.syncState === "err" ? " err" : "");
    $("autosync-text").textContent = Store.state.syncMsg;
    $("btn-undo").disabled = Store.state.histIndex < 0;
    $("btn-redo").disabled = Store.state.histIndex >= Store.state.history.length - 1;
    const jobPill = $("canvas-job-pill");
    if (S && S.running) {
      jobPill.classList.remove("hidden");
      const pr = Store.state.progress;
      const q = pr && pr.forge && pr.forge.queue;
      jobPill.textContent = q ? `대기열 ${q.position}/${q.total}` : "생성 진행 중…";
    } else {
      jobPill.classList.add("hidden");
    }
  }

  // ==================== 좌측: 패치 목록 ====================
  function renderLeftPatches() {
    const list = Store.getPatches();
    $("badge-patch-count").textContent = String(list.length);
    const host = $("patch-list");
    host.replaceChildren();

    if (!list.length) {
      host.append(Data.el("div", { className: "insp-empty", text: "아직 패치 영역이 없습니다. 상단 [+ 새 영역] 또는 캔버스에서 M 도구로 드래그하세요." }));
      $("patch-summary-line").textContent = "패치 0 · ★ 0";
      return;
    }

    const primary = Store.primaryPatch();
    let stars = 0;
    for (const p of list) {
      if (p.pickedCand) stars += 1;
      const isPrimary = primary && primary.id === p.id;
      const isSelected = Store.state.selectedIds.has(p.id);
      const cls = ["patch-card"];
      if (isPrimary) cls.push("selected");
      else if (isSelected) cls.push("multi-sub");
      if (p.meta.hidden) cls.push("hidden-layer");

      const purp = Data.purposeInfo(p.meta.purpose);
      const tl = Data.trafficLight(p.pickedCand || p.candidates[0], p.seam);
      const [x0, y0, x1, y1] = p.rect;

      const visBtn = Data.el("button", {
        type: "button",
        className: "pc-icon-btn" + (p.meta.hidden ? "" : " on"),
        text: p.meta.hidden ? "◌" : "👁",
        title: p.meta.hidden ? "캔버스에 표시" : "캔버스에서 숨기기",
        onClick: (e) => {
          e.stopPropagation();
          p.meta.hidden = !p.meta.hidden;
          Store.emit("selection");
        },
      });
      const lockBtn = Data.el("button", {
        type: "button",
        className: "pc-icon-btn" + (p.locked ? " on" : ""),
        text: p.locked ? "🔒" : "🔓",
        title: p.locked ? "잠금 해제" : "잠그기",
        onClick: async (e) => {
          e.stopPropagation();
          await Store.updateCrop(p.id, { locked: !p.locked }, p.locked ? "잠금 해제" : "잠금");
        },
      });

      const card = Data.el("div", {
        className: cls.join(" "),
        role: "option",
        "aria-selected": String(isSelected || isPrimary),
        onClick: (e) => {
          Store.selectPatch(p.id, { shift: e.shiftKey, ctrl: e.ctrlKey || e.metaKey });
        },
        onDblclick: () => {
          if (window.DPV2Canvas) window.DPV2Canvas.fitCrop(p.id);
        },
      },
      Data.el("div", { className: "pc-row-top" },
        Data.el("span", { className: "pc-idx", text: String(p.index) }),
        Data.el("span", { className: "pc-name", text: p.name, title: p.name }),
        Data.el("div", { className: "pc-actions" }, visBtn, lockBtn),
      ),
      Data.el("div", { className: "pc-row-bot" },
        Data.el("span", { className: "tag-chip accent", text: purp.short }),
        Data.el("span", { className: "tag-chip", text: `${x1 - x0}×${y1 - y0}` }),
        p.pickedCand ? Data.el("span", { className: "tag-chip ok", text: `★ ${tl.icon}` })
          : p.candidates.length ? Data.el("span", { className: "tag-chip", text: `후보 ${p.candidates.length}` })
          : Data.el("span", { className: "tag-chip", text: "생성 전" }),
        p.meta.modelOverride ? Data.el("span", { className: "tag-chip warn", text: p.modelInfo.label }) : null,
      ));

      // 레퍼런스 카드 드래그 앤 드롭 연결
      card.addEventListener("dragover", (e) => {
        if (e.dataTransfer.types.includes("text/bmk-ref-file")) {
          e.preventDefault();
          card.classList.add("drag-over-ref");
        }
      });
      card.addEventListener("dragleave", () => card.classList.remove("drag-over-ref"));
      card.addEventListener("drop", async (e) => {
        card.classList.remove("drag-over-ref");
        const file = e.dataTransfer.getData("text/bmk-ref-file");
        if (!file) return;
        e.preventDefault();
        await linkRefFileToPatches([p], file);
      });

      host.append(card);
    }
    $("patch-summary-line").textContent = `패치 ${list.length} · 선택 ${Store.selectedPatches().length} · ★ ${stars}`;
  }

  // ==================== 좌측: 레퍼런스 보관함 ====================
  function renderLeftRefs() {
    const sheets = Store.state.sheets || [];
    const R = Store.state.R;
    const primary = Store.primaryPatch();
    const linkedSet = new Set((primary && primary.refCrop && primary.refCrop.files || []).map((f) => f.name));

    $("btn-forge-ref").classList.toggle("hidden", !Api.can("forge_import"));

    const sheetHost = $("sheet-thumb-list");
    sheetHost.replaceChildren();
    if (!sheets.length) {
      sheetHost.append(Data.el("p", { className: "muted-text", text: "올린 시트가 없습니다." }));
    } else {
      for (const sh of sheets) {
        const imgUrl = Api.sheetUrl(Store.state.root, sh, "preview");
        const card = Data.el("div", {
          className: "ref-card",
          title: `${sh.name || sh.path} — 클릭해 시트 오려내기`,
          onClick: () => {
            if (window.DPV2Overlays) window.DPV2Overlays.openSheetCut(sh.path);
          },
        },
        Data.el("div", { className: "ref-card-img" },
          imgUrl ? Data.el("img", { src: imgUrl, alt: sh.name || "", loading: "lazy" }) : null,
        ),
        Data.el("div", { className: "ref-card-name", text: sh.name || sh.path }));
        sheetHost.append(card);
      }
    }

    // 연결 가능한 레퍼런스 풀 목록 집계
    const seenFiles = new Map();
    if (R) {
      for (const f of (R.pool || [])) seenFiles.set(f.name, f);
      for (const c of (R.crops || [])) {
        for (const f of (c.files || []).concat(c.unlinked || [])) {
          if (!f.missing && !seenFiles.has(f.name)) seenFiles.set(f.name, f);
        }
      }
    }
    const allRefs = [...seenFiles.values()];
    $("badge-ref-count").textContent = String(allRefs.length + sheets.length);

    const grid = $("ref-vault-grid");
    grid.replaceChildren();
    if (!allRefs.length) {
      grid.append(Data.el("p", { className: "muted-text", text: "시트에서 부품을 오려내거나 파일을 올리면 여기에 표시됩니다." }));
      return;
    }

    for (const ref of allRefs) {
      const isLinked = linkedSet.has(ref.name);
      const thumbUrl = ref.thumb_url ? Api.apiUrl(ref.thumb_url) : null;
      const card = Data.el("div", {
        className: "ref-card" + (isLinked ? " linked" : ""),
        draggable: true,
        title: `${ref.name} — 클릭해 선택 패치에 연결 · 드래그 가능`,
        onClick: async () => {
          const targets = Store.selectedPatches();
          if (!targets.length) return Store.toast("먼저 연결할 패치를 선택해 주세요.", "warn");
          await linkRefFileToPatches(targets, ref.name);
        },
      },
      Data.el("div", { className: "ref-card-img" },
        thumbUrl ? Data.el("img", { src: thumbUrl, alt: ref.name, loading: "lazy" }) : null,
        isLinked ? Data.el("span", { className: "ref-card-badge", text: "연결됨" }) : null,
      ),
      Data.el("div", { className: "ref-card-name", text: ref.name }));

      card.addEventListener("dragstart", (e) => {
        e.dataTransfer.setData("text/bmk-ref-file", ref.name);
      });
      grid.append(card);
    }
  }

  async function linkRefFileToPatches(patches, filename) {
    try {
      for (const p of patches) {
        const rev = Store.state.R ? Store.state.R.rev : null;
        await Api.linkRef(Store.state.root, rev, { crop_id: p.id, op: "add", file: filename });
        await Store.loadRefs(true);
      }
      Store.toast(`"${filename}"을(를) ${patches.length}개 패치에 연결했습니다.`, "ok");
      await Store.refreshAll(true);
    } catch (err) {
      Store.toast(`레퍼런스 연결 실패: ${err.message}`, "err");
    }
  }

  // ==================== 우측: 유니티 복수 선택 일괄 수정 배너 ====================
  function renderMultiBanner() {
    const banner = $("multi-select-banner");
    const sel = Store.selectedPatches();
    if (sel.length < 2) {
      banner.classList.add("hidden");
      return;
    }
    banner.classList.remove("hidden");
    banner.replaceChildren();

    const purpCommon = Store.getCommonOrMixed(sel, (p) => p.meta.purpose);
    const modelCommon = Store.getCommonOrMixed(sel, (p) => p.effectiveModel);
    const aiCommon = Store.getCommonOrMixed(sel, (p) => p.meta.aiAuto);

    const head = Data.el("div", { className: "multi-banner-head" },
      Data.el("span", { className: "multi-banner-title", text: `다중 선택 · ${sel.length}개 패치 일괄 수정` }),
      Data.el("button", {
        type: "button", className: "mini-btn", text: "단일 선택으로",
        onClick: () => {
          const p = Store.primaryPatch();
          if (p) Store.selectPatch(p.id);
        },
      }),
    );

    const purpSel = Data.el("select", { "aria-label": "일괄 목적 변경" },
      purpCommon.mixed ? Data.el("option", { value: "", text: "목적: 혼합됨" }) : null,
      ...Data.PURPOSES.map((x) => Data.el("option", { value: x.id, text: `목적: ${x.label}` })),
    );
    purpSel.value = purpCommon.mixed ? "" : (purpCommon.value || "correct");
    purpSel.addEventListener("change", () => {
      if (!purpSel.value) return;
      for (const p of sel) p.meta.purpose = purpSel.value;
      applyBatchPersist(sel, { purpose: purpSel.value }, "목적 일괄 변경");
    });

    const modelBtn = Data.el("button", {
      type: "button", className: "btn-secondary-sm",
      text: modelCommon.mixed ? "모델: 혼합됨 (변경)" : `모델: ${Data.findModel(modelCommon.value)?.label || modelCommon.value}`,
      onClick: (e) => openModelPopover(e.currentTarget, "batch"),
    });

    const aiBtn = Data.el("button", {
      type: "button", className: "btn-secondary-sm",
      text: aiCommon.mixed ? "AI 최적화: 혼합됨" : (aiCommon.value ? "AI 최적화: 켜짐" : "AI 최적화: 꺼짐"),
      onClick: () => {
        const next = aiCommon.mixed ? true : !aiCommon.value;
        for (const p of sel) p.meta.aiAuto = next;
        Store.emit("selection");
        Store.toast(`${sel.length}개 패치 AI 자동 최적화를 ${next ? "켰습니다" : "껐습니다"}.`, "ok");
      },
    });

    const assistBatchBtn = Data.el("button", {
      type: "button", className: "btn-primary-sm",
      disabled: Store.state.assistBusy,
      text: Store.state.assistBusy ? "AI 작성 중…" : `AI 프롬프트 일괄 작성 (${Math.min(sel.length, MAX_ASSIST_BATCH)}건)`,
      title: `계약 C1: 한 번의 클릭당 최대 ${MAX_ASSIST_BATCH}패치까지 순차 작성합니다`,
      onClick: () => runAssistSequence(sel),
    });

    const lockAllBtn = Data.el("button", {
      type: "button", className: "mini-btn", text: "일괄 잠금/해제",
      onClick: async () => {
        const anyUnlocked = sel.some((p) => !p.locked);
        for (const p of sel) await Store.updateCrop(p.id, { locked: anyUnlocked }, null, true);
        Store.toast(`${sel.length}개 패치를 ${anyUnlocked ? "잠갔습니다" : "잠금 해제했습니다"}.`, "ok");
      },
    });

    const delAllBtn = Data.el("button", {
      type: "button", className: "mini-btn danger-text", text: "선택 일괄 삭제",
      onClick: async () => {
        if (!(await Data.askDialogConfirm(`선택한 ${sel.length}개 패치를 모두 삭제할까요?`, { ok: "삭제", danger: true }))) return;
        for (const p of sel) await Store.deleteCrop(p.id, true);
        Store.toast(`${sel.length}개 패치를 삭제했습니다.`, "ok");
      },
    });

    banner.append(
      head,
      Data.el("div", { className: "multi-grid" }, purpSel, modelBtn, aiBtn, assistBatchBtn),
      Data.el("div", { className: "inline-row" }, lockAllBtn, delAllBtn),
    );
  }

  async function applyBatchPersist(patches, fields, label) {
    Store.emit("selection");
    if (Api.can("v2_crop_model")) {
      try {
        const seen = Store.state.L ? Store.state.L.rev : 0;
        await Api.batchUpdateCrops(Store.state.root, seen, Object.assign({ crop_ids: patches.map((p) => p.id) }, fields));
        await Store.refreshAll(true);
      } catch (err) {
        Store.toast(`${label} 서버 반영 실패: ${err.message}`, "err");
        return;
      }
    }
    Store.toast(`${patches.length}개 패치에 ${label}을 적용했습니다.`, "ok");
  }

  // 계약 §7.3 C1: 클릭 시에만 실행 · 동시 1건 순차 · 최대 8패치 상한 · 429/5xx/timeout 시 즉시 중단(재시도 없음)
  async function runAssistSequence(patches) {
    if (Store.state.assistBusy || !patches.length) return;
    const targets = patches.slice(0, MAX_ASSIST_BATCH);
    if (patches.length > MAX_ASSIST_BATCH) {
      Store.toast(`안정성을 위해 한 번에 최대 ${MAX_ASSIST_BATCH}개 패치만 작성합니다. 나머지는 다시 눌러 주세요.`, "warn");
    }
    Store.state.assistBusy = true;
    Store.emit("selection");
    let done = 0;
    try {
      for (const p of targets) {
        const res = await Api.assistPrompt(Store.state.root, {
          crop_id: p.id,
          purpose: p.meta.purpose,
          direction_ko: p.meta.direction_ko || p.note || p.name,
          model: p.effectiveModel,
          refs_mode: "auto",
        });
        if (res.status === "unsupported") {
          Store.toast(res.reason || "AI 프롬프트 브릿지(V2-A) 연결 대기 중입니다.", "warn");
          break;
        }
        if (res.status !== "written") {
          Store.toast(`${p.name}: AI 작성 실패(${res.reason || "알 수 없는 오류"}) — 남은 작업을 멈춥니다.`, "err");
          break;
        }
        p.meta.summary_ko = res.summary_ko || "";
        p.meta.prompt_en = res.prompt_en || "";
        p.meta.skill_ids = Array.isArray(res.skill_ids) ? res.skill_ids : [];
        done += 1;
        Store.emit("selection");
      }
      if (done > 0) Store.toast(`AI 프롬프트 ${done}건 작성 완료`, "ok");
    } catch (err) {
      Store.toast(`AI 작성 중단: ${err.message}`, "err");
    } finally {
      Store.state.assistBusy = false;
      Store.emit("selection");
    }
  }

  // ==================== 우측 탭 1: 지시 · 레퍼런스 · 모델 ====================
  function renderTabInstruct() {
    const host = $("right-tab-instruct");
    host.replaceChildren();
    const p = Store.primaryPatch();
    if (!p) {
      host.append(Data.el("div", { className: "insp-empty", text: "수정할 영역을 선택하거나 새 패치 영역을 그려 주세요." }));
      return;
    }

    // 1) 패치 이름 & 수정 목적 5종
    const nameInput = Data.el("input", { type: "text", value: p.name, title: "Enter로 이름 저장" });
    nameInput.addEventListener("keydown", async (e) => {
      if (e.key !== "Enter" || e.isComposing) return;
      e.preventDefault();
      const val = nameInput.value.trim();
      if (!val || val === p.name) return;
      try {
        await Store.updateCrop(p.id, { name: val }, `이름 변경 (${val})`);
        Store.toast("패치 이름을 저장했습니다.", "ok");
      } catch (err) {
        Store.toast(err.message, "err");
        nameInput.value = p.name;
      }
    });

    const purposeRow = Data.el("div", { className: "purpose-row" },
      ...Data.PURPOSES.map((purp) => Data.el("button", {
        type: "button",
        className: "purpose-btn" + (p.meta.purpose === purp.id ? " active" : ""),
        text: purp.label,
        title: purp.desc,
        onClick: () => {
          p.meta.purpose = purp.id;
          Store.emit("selection");
        },
      })),
    );

    const cardBasic = Data.el("div", { className: "insp-card" },
      Data.el("div", { className: "insp-card-head" },
        Data.el("h3", { className: "insp-title", text: "패치 이름 · 수정 목적" }),
        Data.el("span", { className: "tag-chip", text: `${p.rect[2] - p.rect[0]}×${p.rect[3] - p.rect[1]}` }),
      ),
      nameInput,
      purposeRow,
      Data.el("p", { className: "muted-text", text: Data.purposeInfo(p.meta.purpose).desc }),
    );

    // 2) 한국어 한 줄 지시 + @참조 멘션 바 + AI 스마트 작성
    const refFiles = (p.refCrop && p.refCrop.files || []).filter((f) => !f.missing);
    const dirArea = Data.el("textarea", {
      className: "direction-textarea",
      placeholder: "예: @참조1 의 금속 브로치 질감으로 교체하고 기존 그림자 방향 유지",
    });
    dirArea.value = p.meta.direction_ko || "";
    dirArea.addEventListener("input", () => { p.meta.direction_ko = dirArea.value; });

    const insertMention = (token) => {
      const start = dirArea.selectionStart ?? dirArea.value.length;
      const end = dirArea.selectionEnd ?? dirArea.value.length;
      dirArea.value = dirArea.value.slice(0, start) + token + " " + dirArea.value.slice(end);
      p.meta.direction_ko = dirArea.value;
      dirArea.focus();
    };

    const mentionBar = Data.el("div", { className: "mention-bar" },
      Data.el("button", { type: "button", className: "mention-chip", text: "@원본", onClick: () => insertMention("@원본") }),
      ...refFiles.map((rf, i) => {
        const thumb = rf.thumb_url ? Api.apiUrl(rf.thumb_url) : null;
        return Data.el("button", {
          type: "button", className: "mention-chip",
          title: `${rf.name} 멘션 삽입`,
          onClick: () => insertMention(`@참조${i + 1}`),
        },
        thumb ? Data.el("img", { src: thumb, alt: "" }) : null,
        Data.el("span", { text: `@참조${i + 1}` }));
      }),
    );

    const aiChk = Data.el("input", { type: "checkbox", checked: p.meta.aiAuto });
    aiChk.addEventListener("change", () => { p.meta.aiAuto = aiChk.checked; Store.emit("selection"); });

    const rawArea = Data.el("textarea", { className: "raw-prompt-area", placeholder: "AI가 최적화한 영문 프롬프트 또는 직접 입력 프롬프트" });
    rawArea.value = p.meta.prompt_en || "";
    rawArea.addEventListener("input", () => { p.meta.prompt_en = rawArea.value; });

    const aiBox = Data.el("div", { className: "ai-box" },
      Data.el("div", { className: "insp-card-head" },
        Data.el("label", { className: "check-row" }, aiChk, Data.el("span", { text: "AI 프롬프트 자동 최적화" })),
        Data.el("button", {
          type: "button", className: "mini-btn accent-outline",
          disabled: Store.state.assistBusy,
          text: Store.state.assistBusy ? "작성 중…" : "AI 작성 실행",
          title: "클릭할 때만 서버 브릿지로 영문 프롬프트를 생성합니다",
          onClick: () => runAssistSequence([p]),
        }),
      ),
      p.meta.summary_ko ? Data.el("p", { className: "ai-summary-ko selectable-text", text: p.meta.summary_ko }) : null,
      p.meta.skill_ids && p.meta.skill_ids.length
        ? Data.el("div", { className: "inline-row" }, ...p.meta.skill_ids.map((s) => Data.el("span", { className: "tag-chip accent", text: s })))
        : null,
      Data.el("details", {},
        Data.el("summary", { className: "muted-text", text: "영문 프롬프트 보기 · 직접 편집" }),
        rawArea,
      ),
    );

    const saveSpecBtn = Data.el("button", {
      type: "button", className: "btn-secondary-sm full-w", text: "이 패치 지시문 저장",
      onClick: async () => {
        try {
          await Api.saveSpec(Store.state.root, {
            crop_id: p.id,
            purpose: p.meta.purpose,
            direction_ko: p.meta.direction_ko,
            prompt_en: p.meta.prompt_en,
            prompt_source: p.meta.prompt_en ? (p.meta.aiAuto ? "assist" : "manual") : "template",
          });
          Store.toast("지시문을 저장했습니다.", "ok");
          await Store.refreshAll(true);
        } catch (err) {
          Store.toast(`지시문 저장 실패: ${err.message}`, "err");
        }
      },
    });

    const cardInstruct = Data.el("div", { className: "insp-card" },
      Data.el("div", { className: "insp-card-head" },
        Data.el("h3", { className: "insp-title", text: "수정 지시 & AI 프롬프트" }),
        Data.el("button", {
          type: "button", className: "mini-btn", text: "레퍼런스 추가",
          onClick: () => switchLeftTab("refs"),
        }),
      ),
      mentionBar,
      dirArea,
      aiBox,
      saveSpecBtn,
    );

    // 3) 연결된 레퍼런스 요약
    const cardRefs = Data.el("div", { className: "insp-card" },
      Data.el("div", { className: "insp-card-head" },
        Data.el("h3", { className: "insp-title", text: `연결된 레퍼런스 (${refFiles.length})` }),
        Data.el("button", {
          type: "button", className: "mini-btn accent-outline", text: "시트 오려내기",
          onClick: () => { if (window.DPV2Overlays) window.DPV2Overlays.openSheetCut(); },
        }),
      ),
      refFiles.length ? Data.el("div", { className: "inline-row" },
        ...refFiles.map((rf) => Data.el("div", { className: "selected-file-chip" },
          Data.el("span", { className: "file-chip-name", text: `${rf.role === "main" ? "★ " : ""}${rf.name}` }),
          Data.el("button", {
            type: "button", className: "icon-btn-sm", title: "연결 끊기", text: "×",
            onClick: async () => {
              const rev = Store.state.R ? Store.state.R.rev : null;
              await Api.linkRef(Store.state.root, rev, { crop_id: p.id, op: "remove", file: rf.name });
              await Store.refreshAll(true);
            },
          }),
        )),
      ) : Data.el("p", { className: "muted-text", text: "연결된 레퍼런스가 없습니다. 좌측 [레퍼런스] 탭에서 클릭해 연결하세요." }),
    );

    // 4) 생성 모델 & 옵션 카드 (라이브 DPEnv.caps.models 동적 옵션)
    const mInfo = p.modelInfo || Data.modelCatalog()[0];
    const prep = Store.projectPrepare();
    const isInherited = !p.meta.modelOverride;

    const inheritChk = Data.el("input", { type: "checkbox", checked: isInherited });
    inheritChk.addEventListener("change", () => {
      p.meta.modelOverride = inheritChk.checked ? "" : prep.model;
      Store.emit("selection");
    });

    const modelRow = Data.el("div", {
      className: "model-card-row",
      onClick: (e) => openModelPopover(e.currentTarget, isInherited ? "project" : "single"),
    },
    Data.el("div", { className: "model-card-info" },
      Data.el("div", { className: "model-card-name", text: mInfo ? mInfo.label : p.effectiveModel }),
      Data.el("div", { className: "model-card-sub", text: mInfo ? `${mInfo.badge} · ${mInfo.summary}` : "" }),
    ),
    Data.el("span", { className: "mini-btn", text: "변경 ▾" }));

    const optsGrid = Data.el("div", { className: "opts-grid" });
    if (mInfo && mInfo.qualities.length) {
      const qSel = Data.el("select", {},
        ...mInfo.qualities.map((q) => Data.el("option", { value: q, text: q })),
      );
      qSel.value = mInfo.qualities.includes(p.effectiveQuality) ? p.effectiveQuality : mInfo.default_quality;
      qSel.addEventListener("change", () => updateModelOption(p, isInherited, { quality: qSel.value }));
      optsGrid.append(Data.el("label", { className: "opt-field" }, Data.el("span", { text: "품질" }), qSel));
    }

    const nSel = Data.el("select", {},
      ...[1, 2, 3, 4, 6, 8].filter((n) => n <= (mInfo ? mInfo.max_n : 8)).map((n) => Data.el("option", { value: String(n), text: `${n}장` })),
    );
    nSel.value = String(Math.min(p.effectiveCount, mInfo ? mInfo.max_n : 8));
    nSel.addEventListener("change", () => updateModelOption(p, isInherited, { n: Number(nSel.value) }));
    optsGrid.append(Data.el("label", { className: "opt-field" }, Data.el("span", { text: "후보 장 수" }), nSel));

    if (mInfo && mInfo.opts && Array.isArray(mInfo.opts.image_size)) {
      const sSel = Data.el("select", {},
        ...mInfo.opts.image_size.map((v) => Data.el("option", { value: v, text: v })),
      );
      sSel.value = (p.meta.opts.image_size || prep.opts.image_size || mInfo.opt_defaults.image_size || "2K");
      sSel.addEventListener("change", () => updateModelOption(p, isInherited, { opts: { image_size: sSel.value } }));
      optsGrid.append(Data.el("label", { className: "opt-field" }, Data.el("span", { text: "해상도 단계" }), sSel));
    }
    if (mInfo && mInfo.opts && Array.isArray(mInfo.opts.thinking_level)) {
      const tSel = Data.el("select", {},
        ...mInfo.opts.thinking_level.map((v) => Data.el("option", { value: v, text: Data.THINKING_LABELS[v] || v })),
      );
      tSel.value = (p.meta.opts.thinking_level || prep.opts.thinking_level || mInfo.opt_defaults.thinking_level || "medium");
      tSel.addEventListener("change", () => updateModelOption(p, isInherited, { opts: { thinking_level: tSel.value } }));
      optsGrid.append(Data.el("label", { className: "opt-field" }, Data.el("span", { text: "추론 깊이" }), tSel));
    }

    const cardModel = Data.el("div", { className: "insp-card" },
      Data.el("div", { className: "insp-card-head" },
        Data.el("h3", { className: "insp-title", text: "생성 모델 · 옵션" }),
        Data.el("label", { className: "check-row" }, inheritChk, Data.el("span", { text: "프로젝트 기본 따름" })),
      ),
      modelRow,
      optsGrid,
    );

    host.append(cardBasic, cardInstruct, cardRefs, cardModel);
  }

  async function updateModelOption(patch, isProjectDefault, changes) {
    if (isProjectDefault && Store.state.P) {
      const curPrep = Object.assign({}, Store.state.P.values.prepare, changes);
      if (changes.opts) curPrep.opts = Object.assign({}, (Store.state.P.values.prepare && Store.state.P.values.prepare.opts) || {}, changes.opts);
      try {
        await Api.savePipelineSettings(Store.state.root, Store.state.P.rev, "prepare", curPrep);
        await Store.refreshAll(true);
        Store.toast("프로젝트 기본 생성 설정을 저장했습니다.", "ok");
      } catch (err) {
        Store.toast(`설정 저장 실패: ${err.message}`, "err");
      }
      return;
    }
    if (changes.model) patch.meta.modelOverride = changes.model;
    if (changes.quality) patch.meta.quality = changes.quality;
    if (changes.n) patch.meta.count = changes.n;
    if (changes.opts) Object.assign(patch.meta.opts, changes.opts);
    Store.emit("selection");
    if (Api.can("v2_crop_model")) {
      applyBatchPersist([patch], changes, "패치 모델 설정");
    }
  }

  // ==================== 우측 탭 2: 후보 비교 · 신호등 품질 ====================
  function renderTabReview() {
    const host = $("right-tab-review");
    host.replaceChildren();
    const p = Store.primaryPatch();
    const totalCands = Store.getPatches().reduce((acc, x) => acc + x.candidates.length, 0);
    $("badge-cand-count").textContent = String(totalCands);

    if (!p) {
      host.append(Data.el("div", { className: "insp-empty", text: "후보를 비교할 패치를 선택해 주세요." }));
      return;
    }
    if (!p.candidates.length) {
      host.append(Data.el("div", { className: "insp-empty", text: "아직 생성된 후보가 없습니다. 하단 [생성하기]로 후보를 만들어 보세요." }));
      return;
    }

    const cand = Store.currentCandidate(p);
    const tl = Data.trafficLight(cand, p.seam);

    const trafficCard = Data.el("div", { className: `traffic-card ${tl.level}` },
      Data.el("span", { className: "traffic-icon", text: tl.icon }),
      Data.el("div", { className: "traffic-body" },
        Data.el("div", { className: "traffic-label", text: tl.label }),
        Data.el("div", { className: "traffic-detail", text: tl.detail }),
      ),
    );

    const actionRow = Data.el("div", { className: "inline-row" },
      Data.el("button", {
        type: "button", className: "btn-primary-sm",
        text: cand && cand.picked ? "★ 채택 해제" : "★ 이 후보 채택",
        onClick: () => pickCurrentCandidate(p, cand),
      }),
      Data.el("button", {
        type: "button", className: "btn-secondary-sm", text: "크게 비교",
        onClick: () => { if (window.DPV2Overlays) window.DPV2Overlays.openComparer(); },
      }),
      Data.el("button", {
        type: "button", className: "mini-btn",
        text: cand && cand.rejected ? "제외 해제" : "제외",
        onClick: () => rejectCurrentCandidate(cand),
      }),
      Data.el("button", {
        type: "button", className: "mini-btn accent-outline", text: "다시 생성 +1",
        onClick: () => requestReroll(p, cand),
      }),
    );

    const grid = Data.el("div", { className: "cand-grid" });
    for (const c of p.candidates) {
      const cls = ["cand-tile"];
      if (cand && cand.key === c.key) cls.push("selected");
      if (c.picked) cls.push("picked");
      if (c.rejected) cls.push("rejected");
      const thumb = c.thumb_url ? Api.apiUrl(c.thumb_url) : null;
      const tile = Data.el("div", {
        className: cls.join(" "),
        onClick: () => Store.selectCandidate(p.target.key, c.key),
        onDblclick: () => pickCurrentCandidate(p, c),
      },
      Data.el("div", { className: "cand-thumb" },
        thumb ? Data.el("img", { src: thumb, alt: `c${c.n}`, loading: "lazy" }) : null,
        Data.el("div", { className: "cand-badges" },
          c.picked ? Data.el("span", { className: "cand-b star", text: "★" }) : null,
          c.gates && c.gates.fails && c.gates.fails.length ? Data.el("span", { className: "cand-b bad", text: "주의" }) : null,
          c.rejected ? Data.el("span", { className: "cand-b", text: "제외" }) : null,
        ),
      ),
      Data.el("div", { className: "cand-meta" },
        Data.el("span", { text: `c${String(c.n).padStart(2, "0")} · ${c.variant || c.origin || ""}` }),
        Data.el("span", { text: Data.formatElapsed(c.took) }),
      ));
      grid.append(tile);
    }

    const techDetails = cand ? Data.el("details", { className: "insp-card" },
      Data.el("summary", { className: "muted-text", text: "상세 분석 수치 보기" }),
      Data.el("p", {
        className: "muted-text",
        text: [
          cand.model ? `모델: ${cand.model}` : "",
          cand.gates ? `배경 변화 MAD ${cand.gates.outside_mad ?? "-"} · >24 ${cand.gates.outside_frac_gt24 ?? "-"}` : "",
          cand.reg && cand.reg.applied ? `위치 맞춤 ${Number(cand.reg.disp || 0).toFixed(1)}px` : "",
          cand.done ? `완료: ${cand.done}` : "",
        ].filter(Boolean).join(" · ") || "추가 지표 없음",
      }),
    ) : null;

    host.append(
      Data.el("div", { className: "insp-card" }, trafficCard, actionRow),
      grid,
      techDetails,
    );
  }

  async function pickCurrentCandidate(patch, cand) {
    if (!patch || !patch.target || !cand) return;
    try {
      const mode = cand.picked ? "remove" : "set";
      await Api.pickCandidate(Store.state.root, {
        crop_id: patch.id, tid: patch.target.tid, key: cand.key, mode,
      });
      Store.toast(mode === "set" ? `후보 c${String(cand.n).padStart(2, "0")}을(를) ★ 채택했습니다.` : "★ 채택을 해제했습니다.", "ok");
      await Store.refreshAll(true);
    } catch (err) {
      Store.toast(`채택 실패: ${err.message}`, "err");
    }
  }

  async function rejectCurrentCandidate(cand) {
    if (!cand) return;
    try {
      await Api.rejectCandidate(Store.state.root, cand.key, !cand.rejected);
      await Store.refreshAll(true);
    } catch (err) {
      Store.toast(`제외 처리 실패: ${err.message}`, "err");
    }
  }

  async function requestReroll(patch, cand) {
    const cells = (patch.target && patch.target.cells) || [];
    const cell = (cand && cells.find((x) => x.cell_key === cand.cell_key)) || cells[0];
    if (!cell) return Store.toast("먼저 무료 반영 후 다시 생성을 요청할 수 있습니다.", "warn");
    const ok = await Data.askDialogConfirm(
      `이 패치의 조건(${cell.variant || cell.cell_key.slice(0, 8)})으로 후보를 +1건 추가 생성 예약할까요?`,
      { ok: "다시 생성 예약" },
    );
    if (!ok) return;
    try {
      await Api.rerollCell(Store.state.root, { cell_key: cell.cell_key, count: 1 });
      Store.toast("다시 생성 +1건이 예약되었습니다.", "ok");
      await Store.refreshAll(true);
    } catch (err) {
      Store.toast(`다시 생성 예약 실패: ${err.message}`, "err");
    }
  }

  // ==================== 우측 탭 3: 비파괴 후보정 · 마스크 · 변형 ====================
  function renderTabRetouch() {
    const host = $("right-tab-retouch");
    host.replaceChildren();
    const p = Store.primaryPatch();
    if (!p || !p.pickedCand) {
      host.append(Data.el("div", { className: "insp-empty", text: "② 후보 비교 탭에서 ★ 후보를 하나 채택하면 경계 마스크 손질과 후보정을 할 수 있습니다." }));
      return;
    }

    const mk = Store.state.mask && Store.state.mask.key === p.pickedCand.key ? Store.state.mask : null;

    // 1) 마스크 브러시 · 경계 페이드 카드
    const maskCard = Data.el("div", { className: "insp-card" },
      Data.el("div", { className: "insp-card-head" },
        Data.el("h3", { className: "insp-title", text: "마스크 경계 손질 (PSD 그룹 마스크)" }),
        mk && mk.dirty ? Data.el("span", { className: "tag-chip warn", text: "미저장 변경" }) : null,
      ),
    );

    if (!mk) {
      maskCard.append(
        Data.el("p", { className: "muted-text", text: "★ 채택 후보의 마스크를 불러와 브러시·지우개·경계 페이드로 자연스럽게 다듬습니다." }),
        Data.el("button", {
          type: "button", className: "btn-primary-sm full-w", text: "마스크 편집 시작",
          onClick: async () => {
            await Store.openMaskSession(p.pickedCand.key);
            if (window.DPV2Canvas) window.DPV2Canvas.setTool("eraser");
          },
        }),
      );
    } else {
      const fadeEdge = (side) => {
        const width = 64;
        for (let y = 0; y < mk.h; y++) {
          for (let x = 0; x < mk.w; x++) {
            const d = side === "top" ? y : side === "bottom" ? (mk.h - 1 - y) : side === "left" ? x : (mk.w - 1 - x);
            if (d >= width) continue;
            const t = Math.max(0, Math.min(1, (d + 0.5) / width));
            const s = t * t * (3 - 2 * t);
            mk.m[y * mk.w + x] *= s;
          }
        }
        mk.dirty = true;
        mk.ops.push(`경계 페이드 ${side}`);
        Store.emit("mask");
      };

      maskCard.append(
        Data.el("div", { className: "inline-row" },
          Data.el("button", {
            type: "button", className: "mini-btn" + (Store.state.tool === "brush" ? " on" : ""),
            text: "브러시 (B)", onClick: () => window.DPV2Canvas && window.DPV2Canvas.setTool("brush"),
          }),
          Data.el("button", {
            type: "button", className: "mini-btn" + (Store.state.tool === "eraser" ? " on" : ""),
            text: "지우개 (E)", onClick: () => window.DPV2Canvas && window.DPV2Canvas.setTool("eraser"),
          }),
          Data.el("button", {
            type: "button", className: "mini-btn", text: "자동 마스크 복원",
            onClick: () => { mk.m.set(mk.base); mk.dirty = true; mk.ops.push("초기화"); Store.emit("mask"); },
          }),
        ),
        Data.el("div", { className: "inline-row" },
          Data.el("span", { className: "muted-text", text: "가장자리 페이드:" }),
          ...[["top", "위"], ["bottom", "아래"], ["left", "왼"], ["right", "오른"]].map(([s, lbl]) =>
            Data.el("button", { type: "button", className: "mini-btn", text: lbl, onClick: () => fadeEdge(s) })),
        ),
        Data.el("div", { className: "inline-row" },
          Data.el("button", {
            type: "button", className: "btn-primary-sm", disabled: !mk.dirty, text: "마스크 저장",
            onClick: () => Store.saveMaskSession(),
          }),
          Data.el("button", {
            type: "button", className: "mini-btn", text: "서버 이전 판",
            onClick: async () => {
              await Api.editMask(Store.state.root, { key: mk.key, seen: mk.file, op: "undo" });
              await Store.openMaskSession(mk.key);
              Store.toast("이전 마스크 판으로 되돌렸습니다.", "ok");
            },
          }),
        ),
      );
    }

    // 2) 패치 미세 위치 · 회전 · 크기 보정 (/mask/adjust)
    const adj = (mk && mk.info && mk.info.adjust) || { dx: 0, dy: 0, rot: p.meta.rotDeg || 0, sx: 1, sy: 1 };
    const dxIn = Data.el("input", { type: "number", step: "1", value: String(adj.dx || 0) });
    const dyIn = Data.el("input", { type: "number", step: "1", value: String(adj.dy || 0) });
    const rotIn = Data.el("input", { type: "number", step: "0.5", value: String(adj.rot || 0) });
    const scaleIn = Data.el("input", { type: "number", step: "0.01", min: "0.1", max: "4", value: String(adj.sx || 1) });

    const xformCard = Data.el("div", { className: "insp-card" },
      Data.el("h3", { className: "insp-title", text: "위치 · 회전 · 배율 정합" }),
      Data.el("div", { className: "opts-grid" },
        Data.el("label", { className: "opt-field" }, Data.el("span", { text: "X 이동 (px)" }), dxIn),
        Data.el("label", { className: "opt-field" }, Data.el("span", { text: "Y 이동 (px)" }), dyIn),
        Data.el("label", { className: "opt-field" }, Data.el("span", { text: "회전 (°)" }), rotIn),
        Data.el("label", { className: "opt-field" }, Data.el("span", { text: "배율" }), scaleIn),
      ),
      Data.el("button", {
        type: "button", className: "btn-secondary-sm full-w", text: "변형 보정 서버에 저장",
        onClick: async () => {
          try {
            const seen = (mk && mk.info && mk.info.adjust && mk.info.adjust.at) || "";
            const sc = Math.max(0.1, Math.min(4, Number(scaleIn.value) || 1));
            await Api.adjustMask(Store.state.root, {
              key: p.pickedCand.key, op: "set", seen,
              dx: Number(dxIn.value) || 0, dy: Number(dyIn.value) || 0,
              rot: Number(rotIn.value) || 0, sx: sc, sy: sc,
            });
            p.meta.rotDeg = Number(rotIn.value) || 0;
            Store.toast("패치 변형 보정을 저장했습니다.", "ok");
            await Store.refreshAll(true);
          } catch (err) {
            Store.toast(`변형 저장 실패: ${err.message}`, "err");
          }
        },
      }),
    );

    // 3) Forge 연동 AI 후보정 도구 (V2-C 계약: caps.v2_postprocess 게이트 + DPSubmit 위임)
    const postReady = Api.can("v2_postprocess");
    const postTools = [
      ["upscale", "AI 업스케일 (2×)", "채택 패치 해상도·선명도 향상"],
      ["bg_remove", "배경 제거 (누끼)", "패치 객체만 깔끔하게 분리"],
      ["layer_split", "레이어 분리", "전경·배경 요소별 후편집 분리"],
    ];
    const postCard = Data.el("div", { className: "insp-card" },
      Data.el("div", { className: "insp-card-head" },
        Data.el("h3", { className: "insp-title", text: "Forge AI 후보정 도구" }),
        Data.el("span", { className: "tag-chip", text: postReady ? "사용 가능" : "Phase V2-C 예정" }),
      ),
      ...postTools.map(([op, title, desc]) => Data.el("div", { className: "post-tool-card" },
        Data.el("div", {},
          Data.el("strong", { text: title }),
          Data.el("p", { className: "muted-text", text: desc }),
        ),
        Data.el("button", {
          type: "button", className: "mini-btn accent-outline",
          disabled: !postReady,
          text: "실행",
          onClick: async () => {
            const res = await Api.submitStage("postprocess", { root: Store.state.root, crop_id: p.id, key: p.pickedCand.key, op });
            if (res && res.pendingIntegration) await Data.showDialogNotice(res.message);
          },
        }),
      )),
    );

    host.append(maskCard, xformCard, postCard);
  }

  // ==================== 우측 하단 고정 실행 독 (D4·D5 준수) ====================
  function renderRunDock() {
    const S = Store.state.S;
    const plan = Store.state.plan;
    const sumEl = $("run-dock-summary");
    const applyBtn = $("btn-apply-free");
    const runBtn = $("btn-run-generate");

    const needApply = !!(S && (!S.canvas || (S.crops || []).some((c) => c.layout_status === "changed" || c.layout_status === "removed") || (S.layout_added || []).length));
    applyBtn.classList.toggle("hidden", !needApply);

    const runNeed = plan && plan.run && typeof plan.run.need === "number" ? plan.run.need : null;
    const selCount = Store.selectedPatches().length;
    if (S && S.running) {
      sumEl.textContent = "현재 서버에서 이미지 생성이 진행 중입니다…";
      runBtn.disabled = true;
      runBtn.textContent = "생성 진행 중…";
    } else if (runNeed !== null) {
      sumEl.textContent = `선택 ${selCount}개 패치 · 생성 필요 ${runNeed}건` + (needApply ? " · 무료 반영 필요" : "");
      runBtn.disabled = false;
      runBtn.textContent = runNeed > 0 ? `생성 승인 (${runNeed}건)` : "선택 패치 생성하기";
    } else {
      sumEl.textContent = `선택 ${selCount}개 패치` + (needApply ? " · 변경된 영역 반영 필요" : " · 준비됨");
      runBtn.disabled = false;
      runBtn.textContent = "선택 패치 생성하기";
    }
  }

  // ==================== 검색형 모델 가이드 팝오버 ====================
  function wireModelPopover() {
    const pop = $("model-popover");
    const search = $("mp-search");
    $("btn-close-mp").addEventListener("click", () => pop.classList.add("hidden"));
    search.addEventListener("input", () => renderModelPopoverList(search.value));
    document.addEventListener("pointerdown", (e) => {
      if (!pop.classList.contains("hidden") && !pop.contains(e.target) && !e.target.closest(".model-card-row")) {
        pop.classList.add("hidden");
      }
    });
  }

  function openModelPopover(anchorEl, mode) {
    modelPopoverMode = mode;
    const pop = $("model-popover");
    const r = anchorEl.getBoundingClientRect();
    pop.classList.remove("hidden");
    const left = Math.max(12, Math.min(window.innerWidth - 386, r.left - 20));
    const top = Math.max(56, Math.min(window.innerHeight - 440, r.bottom + 6));
    pop.style.left = `${left}px`;
    pop.style.top = `${top}px`;
    $("mp-search").value = "";
    renderModelPopoverList("");
    $("mp-search").focus();
  }

  function renderModelPopoverList(query) {
    const listHost = $("mp-list");
    listHost.replaceChildren();
    const q = String(query || "").trim().toLowerCase();
    const p = Store.primaryPatch();
    const currentName = p ? p.effectiveModel : Store.projectPrepare().model;

    const models = Data.modelCatalog().filter((m) => {
      if (!q) return true;
      return `${m.label} ${m.name} ${m.badge} ${m.summary} ${(m.strengths || []).join(" ")}`.toLowerCase().includes(q);
    });

    for (const m of models) {
      const item = Data.el("button", {
        type: "button",
        className: "mp-item" + (m.name === currentName ? " active" : ""),
        onClick: async () => {
          $("model-popover").classList.add("hidden");
          if (modelPopoverMode === "batch") {
            const sel = Store.selectedPatches();
            for (const x of sel) x.meta.modelOverride = m.name;
            await applyBatchPersist(sel, { model: m.name }, `모델(${m.label}) 일괄 변경`);
          } else if (modelPopoverMode === "project") {
            await updateModelOption(p, true, { model: m.name, quality: m.default_quality });
          } else if (p) {
            await updateModelOption(p, false, { model: m.name, quality: m.default_quality });
          }
        },
      },
      Data.el("div", { className: "mp-item-top" },
        Data.el("span", { className: "mp-item-name", text: m.label }),
        Data.el("span", { className: "tag-chip accent", text: m.badge }),
      ),
      Data.el("div", { className: "mp-item-desc", text: m.summary }),
      Data.el("div", { className: "mp-tags" },
        Data.el("span", { className: "tag-chip", text: `속도: ${m.speed}` }),
        Data.el("span", { className: "tag-chip", text: `최대 ${m.max_n}장` }),
        m.ratios && m.ratios.length ? Data.el("span", { className: "tag-chip warn", text: "비율 맞춤 지원" }) : null,
      ));
      listHost.append(item);
    }
  }

  // ==================== 이벤트 연결 ====================
  function switchLeftTab(tab) {
    Store.state.leftTab = tab;
    document.querySelectorAll(".left-dock .dock-tab").forEach((b) => b.classList.toggle("active", b.dataset.leftTab === tab));
    $("left-panel-patches").classList.toggle("hidden", tab !== "patches");
    $("left-panel-refs").classList.toggle("hidden", tab !== "refs");
  }

  function switchRightTab(tab) {
    Store.state.rightTab = tab;
    document.querySelectorAll(".right-dock .dock-tab").forEach((b) => b.classList.toggle("active", b.dataset.rightTab === tab));
    $("right-tab-instruct").classList.toggle("hidden", tab !== "instruct");
    $("right-tab-review").classList.toggle("hidden", tab !== "review");
    $("right-tab-retouch").classList.toggle("hidden", tab !== "retouch");
    Store.emit("selection");
  }

  function wireLeftDock() {
    document.querySelectorAll(".left-dock .dock-tab").forEach((b) => {
      b.addEventListener("click", () => switchLeftTab(b.dataset.leftTab));
    });
    $("btn-add-patch").addEventListener("click", () => {
      if (window.DPV2Canvas) window.DPV2Canvas.setTool("rect");
      Store.toast("캔버스에서 원하는 영역을 드래그해 새 패치를 만드세요.", "ok");
    });
    $("btn-add-full-patch").addEventListener("click", async () => {
      const sz = window.DPV2Canvas ? window.DPV2Canvas.getCanvasSize() : [1024, 1024];
      try {
        await Store.addCrop([0, 0, sz[0], sz[1]]);
        Store.toast("전체 화면 영역 패치를 추가했습니다.", "ok");
      } catch (err) {
        Store.toast(`전체 화면 패치 추가 실패: ${err.message}`, "err");
      }
    });
    $("btn-select-all-patches").addEventListener("click", () => Store.selectAllPatches());
    $("btn-open-sheet-cut").addEventListener("click", () => {
      if (window.DPV2Overlays) window.DPV2Overlays.openSheetCut();
    });
    $("btn-upload-ref").addEventListener("click", () => $("input-upload-ref").click());
    $("input-upload-ref").addEventListener("change", async (e) => {
      const files = [...(e.target.files || [])];
      e.target.value = "";
      for (const f of files) {
        try {
          await Api.uploadSheet(Store.state.root, f, Store.state.R && Store.state.R.rev);
          Store.toast(`레퍼런스 시트 "${f.name}" 올리기 완료`, "ok");
        } catch (err) {
          Store.toast(`${f.name} 올리기 실패: ${err.message}`, "err");
        }
      }
      await Store.refreshAll(true);
    });
    $("btn-forge-ref").addEventListener("click", () => {
      if (!window.DPForgeImport) return;
      window.DPForgeImport.open({
        root: Store.state.root,
        into: "refs",
        onDone: async () => {
          Store.toast("Forge 자료를 레퍼런스 보관함에 가져왔습니다.", "ok");
          await Store.refreshAll(true);
        },
      });
    });
  }

  function wireRightTabsAndDock() {
    document.querySelectorAll(".right-dock .dock-tab").forEach((b) => {
      b.addEventListener("click", () => switchRightTab(b.dataset.rightTab));
    });

    const handleFreeApply = async () => {
      const res = await Api.submitStage("apply", { root: Store.state.root });
      if (res && res.pendingIntegration) {
        await Data.showDialogNotice(res.message);
      } else {
        Store.toast("무료 반영 요청을 보냈습니다.", "ok");
        await Store.refreshAll(true);
      }
    };
    $("btn-apply-free").addEventListener("click", handleFreeApply);
    $("btn-canvas-apply-first").addEventListener("click", handleFreeApply);

    $("btn-run-generate").addEventListener("click", async () => {
      const selIds = Store.selectedPatches().map((p) => p.id);
      const res = await Api.submitStage("run", {
        root: Store.state.root,
        only: selIds.join(","),
        plan: Store.state.plan,
      });
      if (res && res.pendingIntegration) {
        await Data.showDialogNotice(res.message);
      } else {
        await Store.refreshAll(true);
      }
    });
  }

  return { init, renderAll, switchLeftTab, switchRightTab };
})();
