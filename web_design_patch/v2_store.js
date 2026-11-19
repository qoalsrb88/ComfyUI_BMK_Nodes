// BMK Design Patch Studio v2 — 중앙 상태 저장소 · 설정 동기화 · 선택/해제 · 멀티선택 · 히스토리 · 가시성 인식 폴링.
"use strict";

window.DPV2Store = (() => {
  const Data = window.DPV2Data;
  const Api = window.DPV2Api;
  const PREF_KEY = "bmk-dp-v2-prefs";
  const POLL_MS = 2500; // 계약 §7.3 C3: v1(2000ms)보다 길거나 같고, 탭이 숨으면 중단
  const MAX_HISTORY = 40;

  const DEFAULT_PREFS = {
    themeId: "graphite",
    pointColor: "lime",
    uiScale: 100,
    layoutPreset: "standard",
    focusDimAlpha: 36,
    forgeThemeSync: true,
    showAutoCrop: false,
    snapGrid: true,
    snapStep: 128,
    snapRatio: true,
    historyPanel: { mode: "floating", x: null, y: null, w: 300, h: 420 },
    modelPopoverSize: { w: 640, h: 490 },
  };

  const listeners = new Set();

  const state = {
    root: "",
    projects: [],
    S: null,         // GET /state
    L: null,         // GET /layout
    R: null,         // GET /refs
    sheets: [],      // GET /refs/sheets
    P: null,         // GET /pipeline
    plan: null,      // GET /pipeline/plan
    progress: null,  // GET /pipeline/progress
    seams: null,     // GET /seams
    seamsByCrop: new Map(),
    mask: null,      // 활성 마스크 편집 세션
    selectedIds: new Set(),
    primaryId: null,
    lastClickedId: null,
    initialSelectionDone: false,     // 최초 로드 1회만 자동 선택 (이후 선택 해제 시 강제 재선택 금지)
    runUncheckedIds: new Set(),      // 생성 대상에서 명시적으로 체크 해제한 crop_id 집합
    selectedTargetByCrop: new Map(), // crop_id -> target.key (다중 타깃 A/B/C 지원)
    selectedCandByTarget: new Map(), // target.key -> cand.key
    rightTab: "instruct",            // instruct | compare | retouch
    tool: "select",                  // select | rect | brush | eraser | pan
    viewFlags: {
      beforeHeld: false,
      splitOn: false,
      splitRatio: 0.5,
      guidesOn: true,
      seamsOn: false,
    },
    prefs: Object.assign({}, DEFAULT_PREFS),
    patchMeta: new Map(),
    history: [],
    histIndex: -1,
    assistBusy: false,
    syncState: "idle", // idle | syncing | err
    syncMsg: "준비 중",
    pollTimer: null,
    polling: false,
    layoutQueue: Promise.resolve(),
  };

  function emit(reason = "update") {
    for (const fn of listeners) {
      try { fn(reason, state); } catch (e) { console.error("v2 store listener error:", e); }
    }
  }

  function subscribe(fn) {
    listeners.add(fn);
    return () => listeners.delete(fn);
  }

  // ── 토스트 알림 (선택적 액션 버튼 지원) ──
  function toast(message, kind = "", action = null) {
    const host = document.getElementById("toast-host");
    if (!host) return;
    const item = Data.el("div", { className: `toast-item ${kind}`.trim() },
      Data.el("span", { text: message }),
    );
    if (action && action.label && typeof action.onClick === "function") {
      item.append(Data.el("button", {
        type: "button",
        text: action.label,
        onClick: () => { action.onClick(); item.remove(); },
      }));
    }
    host.append(item);
    setTimeout(() => { if (item.parentNode) item.remove(); }, kind === "err" ? 5400 : 3400);
  }

  // ── 설정 불러오기 · 저장 · DOM 적용 ──
  function storageObj() {
    if (window.DPEnv && window.DPEnv.store) return window.DPEnv.store;
    try { return window.localStorage; } catch (_) { return null; }
  }

  function loadPrefs() {
    const st = storageObj();
    if (!st) return;
    try {
      const raw = st.getItem(PREF_KEY);
      if (raw) {
        const parsed = JSON.parse(raw);
        Object.assign(state.prefs, DEFAULT_PREFS, parsed);
        // 기존 snapGrid(boolean)만 있던 설정을 snapStep으로 호환 마이그레이션
        if (parsed && parsed.snapStep === undefined && parsed.snapGrid === false) {
          state.prefs.snapStep = 1;
        }
        if (![1, 16, 32, 64, 128].includes(Number(state.prefs.snapStep))) {
          state.prefs.snapStep = 128;
        }
        state.prefs.historyPanel = Object.assign({}, DEFAULT_PREFS.historyPanel, (parsed && parsed.historyPanel) || {});
        state.prefs.modelPopoverSize = Object.assign({}, DEFAULT_PREFS.modelPopoverSize, (parsed && parsed.modelPopoverSize) || {});
      }
    } catch (_) { /* 손상된 설정은 기본값 사용 */ }
  }

  function savePrefs(patch) {
    Object.assign(state.prefs, patch || {});
    applyPrefsToDom();
    const st = storageObj();
    if (st) {
      try { st.setItem(PREF_KEY, JSON.stringify(state.prefs)); } catch (_) { /* 저장소 차단 무시 */ }
    }
    emit("prefs");
  }

  function activeTheme() {
    return Data.THEMES.find((t) => t.id === state.prefs.themeId) || Data.THEMES[0];
  }

  function activeAccentHex() {
    const theme = activeTheme();
    const point = Data.POINT_COLORS.find((p) => p.id === state.prefs.pointColor) || Data.POINT_COLORS[0];
    return point[theme.mode] || point.hex;
  }

  function applyPrefsToDom() {
    const rootEl = document.documentElement;
    const theme = activeTheme();
    const isLight = theme.mode === "light";
    const accent = activeAccentHex();
    const bg = theme.vars["--bg"] || (isLight ? "#f4f6f9" : "#09090b");

    rootEl.dataset.theme = theme.mode;
    rootEl.dataset.themeId = theme.id;
    rootEl.dataset.pointColor = state.prefs.pointColor || "lime";

    for (const [k, v] of Object.entries(theme.vars)) rootEl.style.setProperty(k, v);
    rootEl.style.setProperty("--accent", accent);
    rootEl.style.setProperty("--accent-hover", Data.hexMix(accent, isLight ? "#000000" : "#ffffff", 0.14));
    rootEl.style.setProperty("--accent-soft", isLight ? Data.hexMix("#ffffff", accent, 0.13) : Data.hexMix(bg, accent, 0.14));
    rootEl.style.setProperty("--accent-border", Data.hexMix(bg, accent, 0.48));
    rootEl.style.setProperty("--on-accent", isLight ? "#ffffff" : "#09090b");

    const scale = Math.min(140, Math.max(85, Number(state.prefs.uiScale) || 100));
    rootEl.style.setProperty("--ui-scale", String(scale / 100));

    const layoutCls = "layout-" + (state.prefs.layoutPreset || "standard");
    const ws = document.getElementById("workspace");
    if (ws) {
      ws.classList.remove("layout-standard", "layout-right-stack", "layout-left-stack", "layout-focus");
      ws.classList.add(layoutCls);
    }
    if (document.body && document.body.classList) {
      document.body.classList.remove("layout-standard", "layout-right-stack", "layout-left-stack", "layout-focus");
      document.body.classList.add(layoutCls);
    }

    const dimValNum = state.prefs.focusDimAlpha ?? 36;
    const dimInput = document.getElementById("input-focus-dim");
    const dimVal = document.getElementById("val-focus-dim");
    if (dimInput) dimInput.value = String(dimValNum);
    if (dimVal) dimVal.textContent = `${dimValNum}%`;
  }

  // 계약 §7.1 A4: forge-theme-sync 는 반드시 부모 창(event.source)이자 DPEnv.parent_origins 에 속한 출처만 수용
  function initForgeThemeListener() {
    window.addEventListener("message", (event) => {
      if (!state.prefs.forgeThemeSync) return;
      if (window.parent && window.parent !== window && event.source !== window.parent) return;
      const allowed = window.DPEnv && Array.isArray(window.DPEnv.parent_origins) ? window.DPEnv.parent_origins : [];
      if (!allowed.includes(event.origin)) return;
      const d = event.data;
      if (!d || typeof d !== "object" || d.type !== "forge-theme-sync") return;
      const patch = {};
      if (typeof d.themeId === "string" && Data.THEMES.some((t) => t.id === d.themeId)) patch.themeId = d.themeId;
      else if (d.mode === "light" || d.mode === "dark") {
        patch.themeId = d.mode === "light" ? "cloud" : "graphite";
      }
      if (typeof d.pointColor === "string" && Data.POINT_COLORS.some((p) => p.id === d.pointColor)) {
        patch.pointColor = d.pointColor;
      }
      if (Object.keys(patch).length) savePrefs(patch);
    });
  }

  // ── 패치 메타 · 스펙 동기화(§3.1–§3.3) ──
  function defaultFilters() {
    return {
      brushEnabled: true,
      edgeEnabled: true,
      feather: 0,
      fadeTop: 0,
      fadeBottom: 0,
      fadeLeft: 0,
      fadeRight: 0,
      transformEnabled: true,
      dx: 0,
      dy: 0,
      scale: 100,
      rot: 0,
      colorEnabled: true,
      opacity: 100,
      bright: 0,
      sat: 0,
    };
  }

  function ensurePatchMeta(cropId, cropObj) {
    let m = state.patchMeta.get(cropId);
    if (!m) {
      const targetText = !Api.can("v2_spec_edit")
        ? ((cropObj && Array.isArray(cropObj.targets) && cropObj.targets[0] && cropObj.targets[0].text)
          || (cropObj && cropObj.note) || "")
        : "";
      m = {
        purpose: "correct",
        user_instruction_ko: targetText,
        aiAuto: true,
        summary_ko: "",
        body_en: "",
        prompt_en: "",
        promptSource: "",
        skill_ids: [],
        seen: "null",
        crop_sig: "",
        specLoaded: false,
        needsApplyForSpec: false,
        specModel: "",
        specState: "template", // template | body | blocked
        specWhy: null,
        specInputHash: "",
        promptInputHash: "",
        specImages: [],
        dirtySpec: false,
        rawDirty: false,
        dirtyFields: { purpose: false, instruction: false, aiAuto: false, raw: false },
        editBaseSeen: null,
        forgeCalls: 0,
        cached: false,
        modelOverride: "", // "" = 프로젝트 기본 모델 상속
        quality: "",
        count: 0,
        opts: {},
        hidden: false,
        rotDeg: 0,
        filters: defaultFilters(),
      };
      state.patchMeta.set(cropId, m);
    }
    return m;
  }

  function markFieldDirty(meta, field) {
    if (!meta) return;
    if (!meta.dirtyFields) {
      meta.dirtyFields = { purpose: false, instruction: false, aiAuto: false, raw: false };
    }
    // #C11-2: 원문/지시 편집 시작 시점의 seen 스냅샷을 기억해 두 창 간 레퍼런스 변경 충돌을 서버 409로 안전하게 감지한다
    if (field === "raw" && !meta.editBaseSeen && meta.specLoaded && meta.seen && meta.seen !== "null") {
      meta.editBaseSeen = meta.seen;
    }
    if (field in meta.dirtyFields) meta.dirtyFields[field] = true;
    meta.dirtySpec = !!(meta.dirtyFields.purpose || meta.dirtyFields.instruction || meta.dirtyFields.aiAuto);
    meta.rawDirty = !!meta.dirtyFields.raw;
  }

  function applySpecResponse(cropId, res, opts = {}) {
    if (!res || typeof res !== "object") return;
    const m = ensurePatchMeta(cropId);
    if (!m.dirtyFields) {
      m.dirtyFields = { purpose: false, instruction: false, aiAuto: false, raw: false };
    }
    const wasLoaded = !!m.specLoaded;
    const fromSave = !!opts.fromSave;
    const includedRaw = !!opts.includedRaw;
    const sent = opts.sent || null;

    // #C09-1: 칸별 dirty 플래그 판정 (레거시 코드/테스트가 dirtySpec만 단독 세팅한 경우도 호환)
    const df = m.dirtyFields;
    const hasFieldFlag = !!(df.purpose || df.instruction || df.aiAuto);
    const dirtyPurpose = df.purpose || (m.dirtySpec && !hasFieldFlag);
    const dirtyInstruction = df.instruction || (m.dirtySpec && !hasFieldFlag);
    const dirtyAiAuto = df.aiAuto || (m.dirtySpec && !hasFieldFlag);
    const dirtyRaw = !!(df.raw || m.rawDirty);

    // 서버 메타(동시성 토큰·판정 상태·이미지 목록) 갱신
    m.specLoaded = true;
    m.needsApplyForSpec = false;
    // #C11-2: 미저장 원문(rawDirty) 편집 중이고 최초 로드가 끝난 상태라면 editBaseSeen을 보존해
    //         다른 창이 레퍼런스를 바꿨을 때 새 seen으로 조용히 덮어쓰지 않고 서버 409 충돌로 감지한다
    if ("seen" in res) {
      const incomingSeen = res.seen ?? "null";
      if (fromSave || !wasLoaded || !dirtyRaw || !m.editBaseSeen) {
        m.seen = incomingSeen;
      }
    }
    if ("crop_sig" in res) m.crop_sig = res.crop_sig ?? "";
    if ("state" in res) m.specState = res.state || "template";
    if ("why" in res) m.specWhy = res.why || null;
    if ("prompt_en" in res) m.prompt_en = res.prompt_en || "";
    if ("input_hash" in res) m.specInputHash = res.input_hash || "";
    if ("model" in res && typeof res.model === "string") m.specModel = res.model;
    if (Array.isArray(res.images)) m.specImages = res.images;

    const purposeSameAsSent = fromSave && (!sent || m.purpose === sent.purpose);
    const instSameAsSent = fromSave && (!sent || (m.user_instruction_ko || "") === (sent.user_instruction_ko || ""));
    const aiAutoSameAsSent = fromSave && (!sent || !!m.aiAuto === !!sent.aiAuto);
    const rawSameAsSent = fromSave && includedRaw && (!sent || (m.body_en || "") === (sent.body_en || ""));

    const canOverwritePurpose = !dirtyPurpose || purposeSameAsSent;
    const canOverwriteInst = !dirtyInstruction || instSameAsSent;
    const canOverwriteAiAuto = !dirtyAiAuto || aiAutoSameAsSent;
    const canOverwriteRaw = !dirtyRaw || rawSameAsSent;

    if (res.v2 && typeof res.v2 === "object") {
      if (canOverwritePurpose) m.purpose = res.v2.purpose || "correct";
      if (canOverwriteInst) m.user_instruction_ko = typeof res.v2.user_instruction_ko === "string" ? res.v2.user_instruction_ko : "";
      if (canOverwriteAiAuto) m.aiAuto = typeof res.v2.ai_auto === "boolean" ? res.v2.ai_auto : true;
      if (canOverwriteRaw) {
        const pr = res.v2.prompt;
        if (pr && typeof pr === "object") {
          m.promptSource = pr.source || "";
          m.body_en = pr.body_en || "";
          m.summary_ko = pr.summary_ko || "";
          m.promptInputHash = pr.input_hash || "";
          m.skill_ids = Array.isArray(pr.skill_ids) ? pr.skill_ids : [];
        } else {
          m.promptSource = "";
          m.body_en = "";
          m.summary_ko = "";
          m.promptInputHash = "";
          m.skill_ids = [];
        }
      }
    } else if (res.v2 === null) {
      if (canOverwritePurpose) m.purpose = "correct";
      if (canOverwriteInst) m.user_instruction_ko = "";
      if (canOverwriteAiAuto) m.aiAuto = true;
      if (canOverwriteRaw) {
        m.promptSource = "";
        m.body_en = "";
        m.summary_ko = "";
        m.promptInputHash = "";
        m.skill_ids = [];
      }
    }

    if (purposeSameAsSent) df.purpose = false;
    if (instSameAsSent) df.instruction = false;
    if (aiAutoSameAsSent) df.aiAuto = false;
    if (rawSameAsSent) {
      df.raw = false;
      m.editBaseSeen = null;
    }
    // 레거시 dirtySpec 단독 플래그 호환 갱신
    if (fromSave && purposeSameAsSent && instSameAsSent && aiAutoSameAsSent) {
      m.dirtySpec = false;
    } else {
      m.dirtySpec = !!(df.purpose || df.instruction || df.aiAuto || (!hasFieldFlag && m.dirtySpec && !fromSave));
    }
    m.rawDirty = !!(df.raw || (!rawSameAsSent && m.rawDirty));
  }

  function invalidateAllSpecs() {
    for (const m of state.patchMeta.values()) {
      m.specLoaded = false;
    }
  }

  async function loadSpec(cropId, force = false) {
    if (!state.root || !cropId || !Api.can("v2_spec_edit")) return null;
    const m = ensurePatchMeta(cropId);
    if (m.specLoaded && !force) return m;
    // 비강제 조회 시 매니페스트(state.S.crops)에 아직 없는 새 크롭은 불필요한 GET /spec 409 왕복을 생략한다
    // (force=true 인 savePatchSpec·명시적 갱신은 항상 실제 서버에 조회)
    if (!force && state.S && Array.isArray(state.S.crops) && !state.S.crops.some((c) => c.id === cropId)) {
      m.needsApplyForSpec = true;
      m.specLoaded = false;
      emit("selection");
      return m;
    }
    try {
      const res = await Api.getSpec(state.root, cropId);
      if (res) {
        applySpecResponse(cropId, res);
        emit("selection");
      }
      return m;
    } catch (err) {
      if (err && err.status === 409 && err.data && err.data.reason === "apply_needed") {
        m.needsApplyForSpec = true;
        m.specLoaded = false;
        emit("selection");
      }
      return m;
    }
  }

  async function savePatchSpec(patch, opts = {}) {
    if (!state.root || !patch) return null;
    if (!Api.can("v2_spec_edit")) {
      return { ok: false, unsupported: true, reason: "지시문 저장은 아직 연결되지 않았습니다." };
    }
    const m = patch.meta;
    // #C09-1: 읽지 않은 패치는 반드시 GET /spec 으로 나머지 칸(지시문·목적·AI 자동)을 채운 뒤 저장한다
    if (!m.specLoaded) {
      await loadSpec(patch.id, true);
      if (m.needsApplyForSpec) {
        throw Object.assign(new Error("새로 추가한 영역입니다 — 먼저 무료 [합성 적용] 후 지시문을 저장할 수 있습니다."), {
          status: 409,
          data: { reason: "apply_needed" },
        });
      }
      if (!m.specLoaded) {
        throw new Error("서버에서 기존 스펙을 읽지 못했습니다 — 잠시 후 다시 시도해 주세요.");
      }
    }
    const includeRaw = !!(opts.includeRaw || m.rawDirty);
    const sent = {
      purpose: m.purpose,
      user_instruction_ko: m.user_instruction_ko || "",
      aiAuto: !!m.aiAuto,
      body_en: m.body_en || "",
    };
    const seenToSend = (includeRaw && m.editBaseSeen) ? m.editBaseSeen : m.seen;
    const payload = {
      crop_id: patch.id,
      seen: seenToSend,
      crop_sig: m.crop_sig,
      purpose: sent.purpose,
      user_instruction_ko: sent.user_instruction_ko,
      ai_auto: sent.aiAuto,
    };
    if (includeRaw) {
      payload.raw_prompt_override = sent.body_en;
    }
    try {
      const res = await Api.saveSpec(state.root, payload);
      if (res && !res.unsupported) {
        applySpecResponse(patch.id, res, { fromSave: true, includedRaw: includeRaw, sent });
      }
      emit("selection");
      return res;
    } catch (err) {
      if (err && err.status === 409 && err.data && err.data.reason === "stale") {
        m.editBaseSeen = null;
        await loadSpec(patch.id, true);
      }
      throw err;
    }
  }

  function projectPrepareRaw() {
    const P = state.P;
    if (!P) return {};
    return (P.settings && P.settings.prepare)
      || (P.derived && P.derived.settings && P.derived.settings.prepare)
      || (P.values && P.values.prepare)
      || {};
  }

  function projectPrepare() {
    const p = projectPrepareRaw();
    const catalog = Data.modelCatalog();
    const defModel = (p && p.model) || (catalog[0] && catalog[0].name) || "gpt-image-2.6-sunburst";
    const info = Data.findModel(defModel);
    const mergedOpts = Object.assign({}, info ? info.opt_defaults : {}, (p && p.opts) || {});
    if (p && p.image_size !== undefined) mergedOpts.image_size = p.image_size;
    if (p && p.thinking_level !== undefined) mergedOpts.thinking_level = p.thinking_level;
    return {
      model: defModel,
      quality: (p && p.quality) || (info && info.default_quality) || "max",
      n: Number((p && p.n) || 4),
      complexity: (p && p.complexity) || "high",
      opts: mergedOpts,
    };
  }

  function buildPreparePayload(patch = {}) {
    const base = Object.assign({}, projectPrepareRaw());
    const next = Object.assign({}, base, patch);
    delete next.opts;
    const modelChanged = ("model" in patch) && patch.model !== base.model;
    const m = Data.findModel(next.model || projectPrepare().model);
    if (m) {
      if ((modelChanged || ("quality" in patch)) && Array.isArray(m.qualities) && m.qualities.length && !m.qualities.includes(next.quality)) {
        next.quality = m.default_quality || m.qualities[0];
      }
      if ((modelChanged || ("n" in patch)) && Number(next.n) > m.max_n) {
        next.n = m.max_n;
      }
      // #C07-8: 사용자가 바꾸지 않은 다른 모델 전용 옵션(image_size·thinking_level)은 기존 저장값을 보존한다
      for (const optKey of ["image_size", "thinking_level"]) {
        if (!modelChanged && !(optKey in patch)) continue;
        const choices = m.opts && m.opts[optKey];
        if (choices && choices.length && !choices.includes(next[optKey])) {
          next[optKey] = (m.opt_defaults && m.opt_defaults[optKey]) || choices[0];
        }
      }
    }
    return next;
  }

  function isPatchCheckedForRun(cropId) {
    return !state.runUncheckedIds.has(cropId);
  }

  function setPatchCheckedForRun(cropId, on) {
    if (on) state.runUncheckedIds.delete(cropId);
    else state.runUncheckedIds.add(cropId);
    emit("selection");
  }

  function setAllCheckedForRun(on) {
    state.runUncheckedIds.clear();
    if (!on) {
      for (const p of getPatches()) state.runUncheckedIds.add(p.id);
    }
    emit("selection");
  }

  function getPatches() {
    const layoutCrops = (state.L && Array.isArray(state.L.crops)) ? state.L.crops : [];
    const stateCrops = (state.S && Array.isArray(state.S.crops)) ? state.S.crops : [];
    const targets = (state.S && Array.isArray(state.S.targets)) ? state.S.targets : [];
    const refCrops = (state.R && Array.isArray(state.R.crops)) ? state.R.crops : [];

    const stateMap = new Map(stateCrops.map((c) => [c.id, c]));
    const refMap = new Map(refCrops.map((c) => [c.id, c]));
    let sourceList = layoutCrops.length ? layoutCrops.slice() : stateCrops.slice();

    // S.z.crops 기준 위→아래 (Z-order 높은 레이어가 목록 위쪽)
    const zIds = state.S && state.S.z && Array.isArray(state.S.z.crops) ? state.S.z.crops : null;
    if (zIds && zIds.length) {
      const ranks = new Map(zIds.map((id, i) => [id, i]));
      sourceList.sort((a, b) => {
        const az = ranks.get(a.id), bz = ranks.get(b.id);
        if (az !== undefined || bz !== undefined) return (bz ?? -1) - (az ?? -1);
        return (a.name || a.id).localeCompare(b.name || b.id, undefined, { numeric: true });
      });
    }

    const prep = projectPrepare();

    return sourceList.map((raw, idx) => {
      const sc = stateMap.get(raw.id) || raw;
      const rc = refMap.get(raw.id) || null;
      const cropTargets = targets.filter((t) => t.crop_id === raw.id);
      const chosenKey = state.selectedTargetByCrop.get(raw.id);
      const activeTarget = (chosenKey && cropTargets.find((t) => t.key === chosenKey)) || cropTargets[0] || null;
      const meta = ensurePatchMeta(raw.id, raw);
      const effectiveModel = meta.modelOverride || prep.model;
      const modelInfo = Data.findModel(effectiveModel);
      const rect = Array.isArray(raw.rect) ? raw.rect : (Array.isArray(sc.rect) ? sc.rect : [0, 0, 512, 512]);
      const pickedKeys = activeTarget && Array.isArray(activeTarget.pick_keys) ? activeTarget.pick_keys : [];
      const pickedCand = pickedKeys.length
        ? (activeTarget.candidates || []).find((c) => c.key === pickedKeys[0]) || null : null;
      const anyPicked = cropTargets.some((t) => Array.isArray(t.pick_keys) && t.pick_keys.length > 0);
      return {
        index: idx + 1,
        id: raw.id,
        name: raw.name || sc.name || raw.id,
        rect,
        locked: !!(raw.locked ?? sc.locked),
        note: raw.note ?? sc.note ?? "",
        layoutTargets: Array.isArray(raw.targets) ? raw.targets : [],
        stateCrop: sc,
        refCrop: rc,
        targets: cropTargets,
        target: activeTarget,
        candidates: activeTarget ? (activeTarget.candidates || []) : [],
        pickedKeys,
        pickedCand,
        anyPicked,
        seam: state.seamsByCrop.get(raw.id) || null,
        meta,
        selectedForRun: isPatchCheckedForRun(raw.id),
        effectiveModel,
        modelInfo,
        effectiveQuality: meta.quality || prep.quality,
        effectiveCount: meta.count || prep.n,
      };
    });
  }

  let maskSwitchAsking = false;

  function closeMaskSession() {
    if (!state.mask) return;
    state.mask = null;
    emit("mask");
  }

  function syncMaskWithActiveCandidate() {
    if (!state.mask) return;
    const cand = currentCandidate();
    if (!cand || cand.key !== state.mask.key) {
      const oldMask = state.mask;
      closeMaskSession();
      // #C11-4: 저장하지 않은 붓 획이 있으면 말없이 버리지 않고 저장 여부를 묻는다
      if (oldMask.dirty && !maskSwitchAsking) {
        maskSwitchAsking = true;
        Data.askDialogConfirm(
          "이전 후보에 저장하지 않은 마스크 붓 편집이 있습니다. 지금 저장할까요?",
          { ok: "저장하기", cancel: "버리기" }
        ).then((ok) => {
          if (ok) return saveMaskSession(oldMask);
        }).finally(() => {
          maskSwitchAsking = false;
        });
      }
    }
  }

  function selectTargetForCrop(cropId, targetKey) {
    if (!cropId || !targetKey) return;
    state.selectedTargetByCrop.set(cropId, targetKey);
    syncMaskWithActiveCandidate();
    emit("selection");
  }

  async function moveCropZ(cropId, side, anchor, skipHistory = false) {
    if (!state.root || !state.S || !cropId || !anchor) return null;
    if (side !== "step" && cropId === anchor) return null;
    try {
      const res = await Api.reorderZ(state.root, {
        scope: "crops", op: "move", item: cropId, [side]: anchor, seen: state.S.rev,
      });
      if (!res || !res.ok) return null;
      if (Array.isArray(res.effective)) {
        state.S.z = { crops: res.effective, manual: !!(res.order && res.order.length) };
      }
      if (!skipHistory && side === "step" && (anchor === "up" || anchor === "down") && res.changed !== false) {
        const inv = anchor === "up" ? "down" : "up";
        pushHistory(`레이어 순서 ${anchor === "up" ? "위로" : "아래로"}`,
          () => moveCropZ(cropId, "step", inv, true),
          () => moveCropZ(cropId, "step", anchor, true));
      }
      if (res.moved !== false) {
        toast(res.changed === false
          ? "겹치지 않는 패치끼리는 합성 결과가 같아 순서를 바꾸지 않았습니다."
          : "패치 겹침 순서를 적용했습니다.", res.changed === false ? "" : "ok");
      }
      await refreshAll(true);
      return res;
    } catch (err) {
      toast(`레이어 순서 변경 실패: ${err.message}`, "err");
      await refreshAll(true);
      return null;
    }
  }

  function getPatch(id) {
    return getPatches().find((p) => p.id === id) || null;
  }

  // 버그 2 영구 수정: 선택 해제(primaryId === null) 시 절대 list[0]을 강제 반환하지 않음!
  function primaryPatch() {
    if (!state.primaryId) return null;
    return getPatches().find((p) => p.id === state.primaryId) || null;
  }

  function selectedPatches() {
    if (!state.selectedIds.size) return [];
    const list = getPatches();
    return list.filter((p) => state.selectedIds.has(p.id));
  }

  function clearSelection() {
    state.primaryId = null;
    state.selectedIds.clear();
    state.initialSelectionDone = true;
    syncMaskWithActiveCandidate();
    emit("selection");
  }

  function currentCandidate(patch) {
    const p = patch === undefined ? primaryPatch() : patch;
    if (!p || !p.target || !p.candidates.length) return null;
    const key = state.selectedCandByTarget.get(p.target.key);
    return p.candidates.find((c) => c.key === key) || p.pickedCand || p.candidates[0] || null;
  }

  function selectCandidate(targetKey, candKey) {
    state.selectedCandByTarget.set(targetKey, candKey);
    syncMaskWithActiveCandidate();
    emit("selection");
  }

  // ── 유니티식 다중 선택 (Shift 범위 · Ctrl 토글 · 마지막 1개 Ctrl 클릭 시 선택 해제) ──
  function selectPatch(id, opts = {}) {
    if (!id) {
      clearSelection();
      return;
    }
    const list = getPatches();
    if (!list.some((p) => p.id === id)) return;
    state.initialSelectionDone = true;

    if (opts.shift && state.lastClickedId) {
      const a = list.findIndex((p) => p.id === state.lastClickedId);
      const b = list.findIndex((p) => p.id === id);
      if (a >= 0 && b >= 0) {
        const [lo, hi] = a <= b ? [a, b] : [b, a];
        if (!opts.ctrl) state.selectedIds.clear();
        for (let i = lo; i <= hi; i++) state.selectedIds.add(list[i].id);
        state.primaryId = id;
        syncMaskWithActiveCandidate();
        emit("selection");
        if (Api.can("v2_spec_edit")) {
          for (const sid of state.selectedIds) loadSpec(sid);
        }
        return;
      }
    }
    if (opts.ctrl) {
      if (state.selectedIds.has(id)) {
        state.selectedIds.delete(id);
        if (state.primaryId === id) {
          state.primaryId = state.selectedIds.size ? [...state.selectedIds][0] : null;
        }
      } else {
        state.selectedIds.add(id);
        state.primaryId = id;
      }
      state.lastClickedId = id;
      syncMaskWithActiveCandidate();
      emit("selection");
      if (Api.can("v2_spec_edit")) {
        for (const sid of state.selectedIds) loadSpec(sid);
      }
      return;
    }
    state.selectedIds = new Set([id]);
    state.primaryId = id;
    state.lastClickedId = id;
    syncMaskWithActiveCandidate();
    emit("selection");
    if (Api.can("v2_spec_edit")) loadSpec(id);
  }

  function selectAllPatches() {
    const list = getPatches();
    state.initialSelectionDone = true;
    state.selectedIds = new Set(list.map((p) => p.id));
    if (!state.primaryId && list[0]) state.primaryId = list[0].id;
    emit("selection");
  }

  function getCommonOrMixed(patches, getter) {
    if (!patches.length) return { mixed: false, value: null };
    const first = getter(patches[0]);
    for (let i = 1; i < patches.length; i++) {
      if (getter(patches[i]) !== first) return { mixed: true, value: null };
    }
    return { mixed: false, value: first };
  }

  // ── 포토샵식 히스토리 타임라인 ──
  function pushHistory(label, undoFn, redoFn) {
    const entry = {
      id: Date.now() + "-" + Math.random().toString(36).slice(2, 6),
      label,
      time: new Date().toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }),
      undoFn,
      redoFn,
    };
    if (state.histIndex < state.history.length - 1) {
      state.history = state.history.slice(0, state.histIndex + 1);
    }
    state.history.push(entry);
    if (state.history.length > MAX_HISTORY) state.history.shift();
    state.histIndex = state.history.length - 1;
    emit("history");
  }

  async function undoHistory() {
    if (state.histIndex < 0) return;
    const entry = state.history[state.histIndex];
    state.histIndex -= 1;
    try {
      if (typeof entry.undoFn === "function") await entry.undoFn();
      toast(`실행 취소: ${entry.label}`);
    } catch (err) {
      toast(`실행 취소 실패: ${err.message}`, "err");
    }
    emit("history");
  }

  async function redoHistory() {
    if (state.histIndex >= state.history.length - 1) return;
    const entry = state.history[state.histIndex + 1];
    state.histIndex += 1;
    try {
      if (typeof entry.redoFn === "function") await entry.redoFn();
      toast(`다시 실행: ${entry.label}`);
    } catch (err) {
      toast(`다시 실행 실패: ${err.message}`, "err");
    }
    emit("history");
  }

  async function jumpHistory(targetIdx) {
    while (state.histIndex > targetIdx) await undoHistory();
    while (state.histIndex < targetIdx) await redoHistory();
  }

  // ── 서버 상태 동기화 & 레이아웃 쓰기 큐 ──
  function enqueueLayout(fn) {
    const run = state.layoutQueue.then(fn);
    state.layoutQueue = run.catch(() => {});
    return run;
  }

  let lastKnownLayoutRev = null;
  let lastOwnLayoutRev = null;
  let pollGen = 0;

  async function loadLayout() {
    if (!state.root) return;
    try {
      const nextL = await Api.getLayout(state.root);
      const nextRev = nextL && nextL.rev !== undefined ? nextL.rev : null;
      // #C07-7: 바깥(다른 탭·v1 등)에서 레이아웃 판이 바뀌었으면 오래된 실행 취소 기록을 비운다
      if (lastKnownLayoutRev !== null && nextRev !== lastKnownLayoutRev && nextRev !== lastOwnLayoutRev) {
        if (state.history.length) {
          state.history = [];
          state.histIndex = -1;
          emit("history");
        }
      }
      lastKnownLayoutRev = nextRev;
      state.L = nextL;
    } catch (_) { /* layout 라우트 없는 환경이면 state.S.crops 로 유지 */ }
  }

  async function loadRefs(force = false) {
    if (!state.root) return;
    try {
      const d = await Api.getRefs(state.root, force ? undefined : (state.R && state.R.rev));
      if (!d.unchanged) state.R = d;
    } catch (_) { /* 무시 */ }
    try {
      const sh = await Api.listSheets(state.root);
      // #C07-1: GET /refs/sheets 는 배열을 그대로 반환 (테스트/호환용 { sheets: [...] } 도 함께 수용)
      if (Array.isArray(sh)) state.sheets = sh;
      else if (sh && Array.isArray(sh.sheets)) state.sheets = sh.sheets;
    } catch (_) { /* 무시 */ }
  }

  async function loadPipelineAndPlan() {
    if (!state.root) return;
    try {
      state.P = await Api.getPipeline(state.root);
    } catch (_) { /* 무시 */ }
    try {
      const patches = getPatches();
      const checked = patches.filter((p) => p.selectedForRun).map((p) => p.id);
      const only = checked.length && checked.length < patches.length ? checked.join(",") : null;
      const plan = await Api.getPlan(state.root, only, false);
      if (plan && !plan.busy) state.plan = plan;
    } catch (_) { /* 무시 */ }
    try {
      state.progress = await Api.getProgress(state.root);
    } catch (_) { /* 무시 */ }
  }

  async function loadSeams() {
    if (!state.root || !state.S || !state.S.canvas) return;
    const rel = state.S.canvas.seams_url;
    const sig = state.S.canvas.composite_sig;
    if (!rel && !sig) return;
    try {
      let res;
      if (rel) {
        const u = Api.apiUrl(rel);
        const r = u ? await fetch(u, { cache: "no-store" }) : null;
        if (!r || !r.ok) return;
        res = await r.json();
      } else {
        res = await Api.getSeams(state.root, sig);
      }
      state.seams = res;
      const map = new Map();
      for (const seg of (res && res.segments) || []) {
        const cur = map.get(seg.crop_id) || { n: 0, bad: 0, segments: [] };
        cur.n += 1;
        if (seg.severity === "bad") cur.bad += 1;
        cur.segments.push(seg);
        map.set(seg.crop_id, cur);
      }
      state.seamsByCrop = map;
    } catch (_) { /* 이음매 계산 전/미지원 무시 */ }
  }

  let activeRefreshPromise = null;
  let queuedForceRefresh = false;

  async function refreshAll(force = false) {
    if (!state.root) return;
    if (state.polling) {
      if (force) {
        queuedForceRefresh = true;
        if (activeRefreshPromise) {
          await activeRefreshPromise;
          if (queuedForceRefresh) {
            queuedForceRefresh = false;
            return refreshAll(true);
          }
        }
      }
      return;
    }
    state.polling = true;
    let resolveRun;
    activeRefreshPromise = new Promise((r) => { resolveRun = r; });
    const isInitial = !state.S;
    let slowTimer = null;
    if (isInitial) {
      state.syncState = "syncing";
      state.syncMsg = "불러오는 중";
      emit("sync");
    } else if (force) {
      // 빠른 응답(450ms 이하)에서는 헤더 점멸 없이 rev 숫자만 매끄럽게 갱신
      slowTimer = setTimeout(() => {
        if (state.polling) {
          state.syncState = "syncing";
          emit("sync");
        }
      }, 450);
    }
    try {
      const prev = state.S;
      const prevRunning = !!(prev && prev.running);
      const prevDrain = !!(prev && prev.drain);
      const prevDrafting = !!(prev && prev.drafting);
      const prevExport = prev ? prev.export_state : undefined;
      const prevSyncState = state.syncState;
      const prevSyncMsg = state.syncMsg;
      const d = await Api.getState(state.root, force || !prev ? undefined : prev.rev);
      let fullReload = !d.unchanged;
      let statusChanged = false;
      if (d.unchanged && prev) {
        prev.running = d.running;
        prev.drain = d.drain;
        prev.drafting = d.drafting;
        if ("export_state" in d) {
          prev.export_state = d.export_state;
          prev.export_last = d.export_last;
        }
        statusChanged = (!!prev.running !== prevRunning)
          || (!!prev.drain !== prevDrain)
          || (!!prev.drafting !== prevDrafting)
          || (prev.export_state !== prevExport);
        if (d.layout_rev !== prev.layout_rev || d.refs_rev !== prev.refs_rev
            || d.refs_files_v !== prev.refs_files_v || d.pipeline_rev !== prev.pipeline_rev
            || d.plan_v !== prev.plan_v) {
          fullReload = true;
        }
      }
      if (fullReload) {
        const prevRefsRev = prev ? prev.refs_rev : undefined;
        const prevPipeRev = prev ? prev.pipeline_rev : undefined;
        state.S = d.unchanged ? await Api.getState(state.root) : d;
        // #C08-2: 모델·레퍼런스 판이 바뀌면 모든 패치의 스펙 캐시를 무효화해 생성 시 최신 판정을 읽도록 한다
        if ((prevRefsRev !== undefined && state.S.refs_rev !== prevRefsRev)
            || (prevPipeRev !== undefined && state.S.pipeline_rev !== prevPipeRev)) {
          invalidateAllSpecs();
        }
        await Promise.all([loadLayout(), loadRefs(true), loadPipelineAndPlan()]);
        await loadSeams();
        const patches = getPatches();
        const validIds = new Set(patches.map((p) => p.id));
        // 삭제된 id 만 선택 집합에서 정리
        for (const sid of [...state.selectedIds]) {
          if (!validIds.has(sid)) state.selectedIds.delete(sid);
        }
        if (state.primaryId && !validIds.has(state.primaryId)) {
          state.primaryId = state.selectedIds.size ? [...state.selectedIds][0] : null;
        }
        // 최초 로드 1회만 기본 선택 (사용자가 선택 해제한 뒤에는 절대 강제 재선택하지 않음!)
        if (!state.initialSelectionDone && patches.length) {
          state.primaryId = patches[0].id;
          state.selectedIds = new Set([patches[0].id]);
          state.initialSelectionDone = true;
        }
        syncMaskWithActiveCandidate();
        if (state.primaryId && Api.can("v2_spec_edit")) {
          const primInManifest = !!(state.S && Array.isArray(state.S.crops) && state.S.crops.some((c) => c.id === state.primaryId));
          await loadSpec(state.primaryId, primInManifest);
        }
      } else if (state.S && state.S.running) {
        try { state.progress = await Api.getProgress(state.root); statusChanged = true; } catch (_) {}
      }
      state.syncState = "idle";
      state.syncMsg = state.S ? "동기화됨" : "연결됨";
      if (fullReload || statusChanged || isInitial) {
        emit("state");
      } else if (prevSyncState !== "idle" || prevSyncMsg !== state.syncMsg) {
        emit("sync");
      }
    } catch (err) {
      state.syncState = "err";
      state.syncMsg = err.message || "연결 오류";
      emit("sync");
    } finally {
      if (slowTimer) clearTimeout(slowTimer);
      state.polling = false;
      activeRefreshPromise = null;
      if (resolveRun) resolveRun();
    }
  }

  // 계약 §7.3 C3 · #C08-6: 탭이 숨으면 폴링 완전 정지, 세대 번호로 중복 타이머 방지
  function startPolling() {
    stopPolling();
    const myGen = ++pollGen;
    const tick = async () => {
      if (myGen !== pollGen || document.hidden || !state.root) return;
      await refreshAll(false);
      if (myGen === pollGen && !document.hidden && state.root) {
        state.pollTimer = setTimeout(tick, POLL_MS);
      }
    };
    state.pollTimer = setTimeout(tick, POLL_MS);
  }

  function stopPolling() {
    pollGen += 1;
    if (state.pollTimer) {
      clearTimeout(state.pollTimer);
      state.pollTimer = null;
    }
  }

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      stopPolling();
    } else if (state.root) {
      const myGen = ++pollGen;
      refreshAll(false).finally(() => {
        if (myGen === pollGen && !document.hidden && state.root) startPolling();
      });
    }
  });

  // ── 레이아웃 CRUD (자동 /layout/start 투명 호출 + 히스토리 기록) ──
  async function ensureLayoutStarted() {
    if (!state.L) await loadLayout();
    if (!state.L || state.L.rev === 0 || state.L.rev === null) {
      await Api.startLayout(state.root);
      await loadLayout();
    }
  }

  function nextCropName() {
    const list = getPatches();
    let maxN = 0;
    for (const p of list) {
      const m = /^(\d{3})_/.exec(p.name || "");
      if (m) maxN = Math.max(maxN, Number(m[1]));
    }
    const nnn = String(maxN + 1).padStart(3, "0");
    return `${nnn}_패치`;
  }

  function plainTargets(crop) {
    const list = crop && Array.isArray(crop.targets) ? crop.targets : [];
    return list.map((t) => ({
      tid: t.tid || "A",
      text: t.text || "",
      box: Array.isArray(t.box) ? t.box.slice() : null,
    }));
  }

  function addCrop(rect, customName = null, skipHistory = false, extraFields = null) {
    return enqueueLayout(async () => {
      await ensureLayoutStarted();
      const name = customName || nextCropName();
      const body = Object.assign({
        op: "add",
        name,
        rect: rect.map((v) => Math.round(v)),
        targets: [{ tid: "A", text: "", box: null }],
        note: "",
      }, extraFields || {});
      const res = await Api.mutateCrop(state.root, state.L.rev, body);
      // #C11-5②: 자기 쓰기 응답의 rev 만 기록(뒤따른 loadLayout 이 다른 창의 rev 를 받아도 자기 쓰기로 오인하지 않게)
      if (res && res.rev !== undefined) lastOwnLayoutRev = res.rev;
      await loadLayout();
      if (res.crop && res.crop.id) {
        const ref = { id: res.crop.id };
        selectPatch(ref.id);
        if (!skipHistory) {
          pushHistory(`영역 추가 (${name})`,
            () => deleteCrop(ref.id, true),
            async () => {
              const r = await addCrop(rect, name, true);
              if (r && r.crop && r.crop.id) ref.id = r.crop.id;
            });
        }
      }
      await refreshAll(true);
      return res;
    });
  }

  function updateCrop(id, changes, historyLabel = null, skipHistory = false, beforeOverride = null) {
    return enqueueLayout(async () => {
      await ensureLayoutStarted();
      const prev = state.L && (state.L.crops || []).find((c) => c.id === id);
      if (!prev) return null;
      const before = ("locked" in changes && Object.keys(changes).length === 1) ? {} : { restore_rev: state.L.rev };
      for (const k of Object.keys(changes)) {
        // #C11-5①: 방향키 이동 등 제자리 수정 후 커밋할 때는 beforeOverride 의 원래 좌표를 실행 취소 기준값으로 기록한다
        if (beforeOverride && k in beforeOverride) {
          before[k] = Array.isArray(beforeOverride[k]) ? beforeOverride[k].slice() : beforeOverride[k];
        } else {
          before[k] = k === "targets" ? plainTargets(prev) : k === "locked" ? !!prev.locked : (Array.isArray(prev[k]) ? prev[k].slice() : prev[k]);
        }
      }
      const payload = Object.assign({ op: "update", id }, changes);
      const res = await Api.mutateCrop(state.root, state.L.rev, payload);
      if (res && res.rev !== undefined) lastOwnLayoutRev = res.rev;
      await loadLayout();
      if (!skipHistory && historyLabel) {
        pushHistory(historyLabel,
          () => updateCrop(id, before, null, true),
          () => updateCrop(id, changes, null, true));
      }
      await refreshAll(true);
      return res;
    });
  }

  function deleteCrop(id, skipHistory = false) {
    return enqueueLayout(async () => {
      await ensureLayoutStarted();
      const cropsArr = (state.L && Array.isArray(state.L.crops)) ? state.L.crops : [];
      const prevIdx = cropsArr.findIndex((c) => c.id === id);
      const prev = prevIdx >= 0 ? cropsArr[prevIdx] : null;
      if (!prev) return null;
      const delRev = state.L.rev;
      // #C07-7: 되살리기 시 원래 ID·타깃·메모·psd_name·restore_rev·index 를 모두 보존한다
      const restoreExtra = {
        id: prev.id,
        targets: plainTargets(prev),
        note: prev.note || "",
        restore_rev: delRev,
        index: prevIdx,
      };
      if (prev.psd_name !== undefined) restoreExtra.psd_name = prev.psd_name;
      const prevRect = Array.isArray(prev.rect) ? prev.rect.slice() : [0, 0, 256, 256];
      const prevName = prev.name || id;
      const res = await Api.mutateCrop(state.root, state.L.rev, { op: "delete", id });
      if (res && res.rev !== undefined) lastOwnLayoutRev = res.rev;
      await loadLayout();
      state.selectedIds.delete(id);
      if (state.primaryId === id) {
        state.primaryId = state.selectedIds.size ? [...state.selectedIds][0] : null;
      }
      syncMaskWithActiveCandidate();
      if (!skipHistory) {
        const ref = { id };
        pushHistory(`영역 삭제 (${prevName})`,
          async () => {
            const r = await addCrop(prevRect, prevName, true, Object.assign({}, restoreExtra, { id: ref.id }));
            if (r && r.crop && r.crop.id) ref.id = r.crop.id;
          },
          () => deleteCrop(ref.id, true));
      }
      await refreshAll(true);
      return res;
    });
  }

  // ── 마스크 편집 세션 열기/저장 ──
  async function openMaskSession(candKey) {
    if (!state.root || !candKey) { state.mask = null; emit("mask"); return null; }
    try {
      const info = await Api.getMask(state.root, candKey);
      const baseImg = await Api.fetchImageRgba(info.base.url);
      const edImg = info.edit ? await Api.fetchImageRgba(info.edit.url) : null;
      const w = info.rect[2] - info.rect[0];
      const h = info.rect[3] - info.rect[1];
      const toF32 = (img) => {
        const out = new Float32Array(w * h);
        for (let i = 0; i < out.length; i++) out[i] = img.data[i * 4] / 255;
        return out;
      };
      const base = toF32(baseImg);
      const cur = edImg ? toF32(edImg) : base.slice();
      let layers = null;
      if (info.layers) {
        const [under, patch, over] = await Promise.all([
          Api.fetchImageRgba(info.layers.under_url),
          Api.fetchImageRgba(info.layers.patch_url),
          Api.fetchImageRgba(info.layers.over_url),
        ]);
        layers = { box: info.layers.box, under: under.data, patch: patch.data, over: over.data };
      }
      state.mask = {
        key: candKey,
        info,
        win: info.rect.slice(),
        w, h,
        base,
        m: cur,
        start: cur.slice(),
        file: info.edit ? info.edit.file : "",
        adjust: info.adjust || null,
        layers,
        dirty: false,
        ops: [],
        brushSize: 48,
        brushHard: 0.65,
        brushFlow: 1,
      };
      // #C11-3: 서버 adjust 값이 있으면 미수정 슬라이더를 서버 보정값으로 동기화한다
      if (info && info.adjust && info.crop_id) {
        const pm = ensurePatchMeta(info.crop_id);
        const f = pm.filters;
        const t = f._touched || {};
        if (!t.feather && typeof info.adjust.blur === "number") f.feather = info.adjust.blur;
        if (!t.dx && typeof info.adjust.dx === "number") f.dx = info.adjust.dx;
        if (!t.dy && typeof info.adjust.dy === "number") f.dy = info.adjust.dy;
        if (!t.rot && typeof info.adjust.rot === "number") f.rot = info.adjust.rot;
        if (!t.scale && typeof info.adjust.sx === "number" && typeof info.adjust.sy === "number"
            && Math.abs(info.adjust.sx - info.adjust.sy) < 0.001) {
          f.scale = Math.round(info.adjust.sx * 100);
        }
      }
      emit("mask");
      return state.mask;
    } catch (err) {
      toast(`마스크를 열 수 없습니다: ${err.message}`, "err");
      return null;
    }
  }

  async function saveMaskSession(targetMask = null) {
    const ms = targetMask || state.mask;
    if (!ms || !state.root) return null;
    // #C07-5: 현재 열린 세션 저장 시 활성 후보와 다르면 다른 후보에 잘못 저장하지 않는다
    if (!targetMask) {
      const activeCand = currentCandidate();
      if (activeCand && activeCand.key !== ms.key) {
        toast("현재 선택한 후보와 마스크 편집 대상이 다릅니다 — 브러시 도구를 다시 열어 주세요.", "err");
        closeMaskSession();
        return null;
      }
    }
    const px = new Uint8Array(ms.w * ms.h);
    for (let i = 0; i < px.length; i++) px[i] = Math.max(0, Math.min(255, Math.round(ms.m[i] * 255)));
    try {
      const opsText = (ms.ops && ms.ops.length ? ms.ops.join(" · ") : "붓 편집").slice(0, 200);
      const res = await Api.saveMaskPng(state.root, {
        key: ms.key,
        seen: ms.file || "",
        ops: opsText,
        rect: ms.win.join(","),
      }, px, ms.w, ms.h);
      if (res && res.conflict) {
        toast(res.error || "다른 창에서 마스크가 변경되었습니다 — 최신 상태로 다시 열어주세요.", "err");
        return res;
      }
      ms.dirty = false;
      ms.start = ms.m.slice();
      ms.ops = [];
      if (res && res.edit && res.edit.file) {
        ms.file = res.edit.file;
        if (ms.info) ms.info.edit = res.edit;
      }
      toast("마스크를 저장했습니다.", "ok");
      await refreshAll(true);
      return res;
    } catch (err) {
      toast(`마스크 저장 실패: ${err.message}`, "err");
      return null;
    }
  }

  return {
    state, subscribe, emit, toast,
    loadPrefs, savePrefs, applyPrefsToDom, activeTheme, activeAccentHex, initForgeThemeListener,
    ensurePatchMeta, markFieldDirty, loadSpec, savePatchSpec, applySpecResponse, invalidateAllSpecs, projectPrepareRaw, projectPrepare, buildPreparePayload,
    isPatchCheckedForRun, setPatchCheckedForRun, setAllCheckedForRun,
    getPatches, getPatch, primaryPatch, selectedPatches, clearSelection,
    currentCandidate, selectCandidate, selectTargetForCrop, moveCropZ,
    selectPatch, selectAllPatches, getCommonOrMixed,
    pushHistory, undoHistory, redoHistory, jumpHistory,
    loadLayout, loadRefs, loadPipelineAndPlan, loadSeams, refreshAll,
    startPolling, stopPolling,
    addCrop, updateCrop, deleteCrop,
    openMaskSession, closeMaskSession, saveMaskSession,
  };
})();
