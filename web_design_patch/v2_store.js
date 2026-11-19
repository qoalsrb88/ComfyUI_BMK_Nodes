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

  // 계약 §7.1 A4: forge-theme-sync 는 반드시 DPEnv.parent_origins 에 속한 부모 출처만 수용
  function initForgeThemeListener() {
    window.addEventListener("message", (event) => {
      if (!state.prefs.forgeThemeSync) return;
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

  function applySpecResponse(cropId, res) {
    if (!res || typeof res !== "object") return;
    const m = ensurePatchMeta(cropId);
    m.specLoaded = true;
    m.needsApplyForSpec = false;
    if ("seen" in res) m.seen = res.seen ?? "null";
    if ("crop_sig" in res) m.crop_sig = res.crop_sig ?? "";
    if ("state" in res) m.specState = res.state || "template";
    if ("why" in res) m.specWhy = res.why || null;
    if ("prompt_en" in res) m.prompt_en = res.prompt_en || "";
    if ("input_hash" in res) m.specInputHash = res.input_hash || "";
    if ("model" in res && typeof res.model === "string") m.specModel = res.model;
    if (Array.isArray(res.images)) m.specImages = res.images;
    if (res.v2 && typeof res.v2 === "object") {
      m.purpose = res.v2.purpose || "correct";
      m.user_instruction_ko = typeof res.v2.user_instruction_ko === "string" ? res.v2.user_instruction_ko : "";
      m.aiAuto = typeof res.v2.ai_auto === "boolean" ? res.v2.ai_auto : true;
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
    } else if (res.v2 === null) {
      m.purpose = "correct";
      m.user_instruction_ko = "";
      m.aiAuto = true;
      m.promptSource = "";
      m.body_en = "";
      m.summary_ko = "";
      m.promptInputHash = "";
      m.skill_ids = [];
    }
    m.dirtySpec = false;
    m.rawDirty = false;
  }

  async function loadSpec(cropId, force = false) {
    if (!state.root || !cropId || !Api.can("v2_spec_edit")) return null;
    const m = ensurePatchMeta(cropId);
    if (m.specLoaded && !force) return m;
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
    if (!m.specLoaded) {
      await loadSpec(patch.id, true);
      if (m.needsApplyForSpec) {
        throw Object.assign(new Error("새로 추가한 영역입니다 — 먼저 무료 [합성 적용] 후 지시문을 저장할 수 있습니다."), {
          status: 409,
          data: { reason: "apply_needed" },
        });
      }
    }
    const payload = {
      crop_id: patch.id,
      seen: m.seen,
      crop_sig: m.crop_sig,
      purpose: m.purpose,
      user_instruction_ko: m.user_instruction_ko || "",
      ai_auto: !!m.aiAuto,
    };
    if (opts.includeRaw || m.rawDirty) {
      payload.raw_prompt_override = m.body_en || "";
    }
    try {
      const res = await Api.saveSpec(state.root, payload);
      if (res && !res.unsupported) {
        applySpecResponse(patch.id, res);
      }
      emit("selection");
      return res;
    } catch (err) {
      if (err && err.status === 409 && err.data && err.data.reason === "stale") {
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
    const m = Data.findModel(next.model || projectPrepare().model);
    if (m) {
      if (Array.isArray(m.qualities) && m.qualities.length && !m.qualities.includes(next.quality)) {
        next.quality = m.default_quality || m.qualities[0];
      }
      if (Number(next.n) > m.max_n) next.n = m.max_n;
      for (const optKey of ["image_size", "thinking_level"]) {
        const choices = m.opts && m.opts[optKey];
        if (choices && choices.length) {
          if (!choices.includes(next[optKey])) {
            next[optKey] = (m.opt_defaults && m.opt_defaults[optKey]) || choices[0];
          }
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
      const pickedCand = activeTarget && activeTarget.pick_keys && activeTarget.pick_keys.length
        ? (activeTarget.candidates || []).find((c) => c.key === activeTarget.pick_keys[0]) || null : null;
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

  function selectTargetForCrop(cropId, targetKey) {
    if (!cropId || !targetKey) return;
    state.selectedTargetByCrop.set(cropId, targetKey);
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
        emit("selection");
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
      emit("selection");
      return;
    }
    state.selectedIds = new Set([id]);
    state.primaryId = id;
    state.lastClickedId = id;
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

  async function loadLayout() {
    if (!state.root) return;
    try {
      state.L = await Api.getLayout(state.root);
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
      if (Array.isArray(sh.sheets)) state.sheets = sh.sheets;
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

  async function refreshAll(force = false) {
    if (!state.root || state.polling) return;
    state.polling = true;
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
        state.S = d.unchanged ? await Api.getState(state.root) : d;
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
        if (state.primaryId && Api.can("v2_spec_edit")) {
          await loadSpec(state.primaryId, true);
        }
      } else if (state.S && state.S.running) {
        try { state.progress = await Api.getProgress(state.root); statusChanged = true; } catch (_) {}
      }
      state.syncState = "idle";
      state.syncMsg = state.S ? `rev ${state.S.rev} · 동기화됨` : "연결됨";
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
    }
  }

  // 계약 §7.3 C3: 탭이 숨으면 폴링 완전 정지, 돌아오면 즉시 1회 갱신 후 재개
  function startPolling() {
    stopPolling();
    const tick = async () => {
      if (document.hidden || !state.root) return;
      await refreshAll(false);
      if (!document.hidden && state.root) {
        state.pollTimer = setTimeout(tick, POLL_MS);
      }
    };
    state.pollTimer = setTimeout(tick, POLL_MS);
  }

  function stopPolling() {
    if (state.pollTimer) {
      clearTimeout(state.pollTimer);
      state.pollTimer = null;
    }
  }

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      stopPolling();
    } else if (state.root) {
      refreshAll(false).finally(() => startPolling());
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

  function addCrop(rect, customName = null, skipHistory = false) {
    return enqueueLayout(async () => {
      await ensureLayoutStarted();
      const name = customName || nextCropName();
      const res = await Api.mutateCrop(state.root, state.L.rev, {
        op: "add",
        name,
        rect: rect.map((v) => Math.round(v)),
        targets: [{ tid: "A", text: "", box: null }],
        note: "",
      });
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

  function updateCrop(id, changes, historyLabel = null, skipHistory = false) {
    return enqueueLayout(async () => {
      await ensureLayoutStarted();
      const prev = state.L && (state.L.crops || []).find((c) => c.id === id);
      if (!prev) return null;
      const before = {};
      for (const k of Object.keys(changes)) before[k] = prev[k];
      const payload = Object.assign({ op: "update", id }, changes);
      const res = await Api.mutateCrop(state.root, state.L.rev, payload);
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
      const prev = state.L && (state.L.crops || []).find((c) => c.id === id);
      if (!prev) return null;
      const res = await Api.mutateCrop(state.root, state.L.rev, { op: "delete", id });
      await loadLayout();
      state.selectedIds.delete(id);
      if (state.primaryId === id) {
        state.primaryId = state.selectedIds.size ? [...state.selectedIds][0] : null;
      }
      if (!skipHistory) {
        const ref = { id };
        pushHistory(`영역 삭제 (${prev.name || id})`,
          async () => {
            const r = await addCrop(prev.rect, prev.name, true);
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
      emit("mask");
      return state.mask;
    } catch (err) {
      toast(`마스크를 열 수 없습니다: ${err.message}`, "err");
      return null;
    }
  }

  async function saveMaskSession() {
    const ms = state.mask;
    if (!ms || !state.root) return null;
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
    ensurePatchMeta, loadSpec, savePatchSpec, applySpecResponse, projectPrepareRaw, projectPrepare, buildPreparePayload,
    isPatchCheckedForRun, setPatchCheckedForRun, setAllCheckedForRun,
    getPatches, getPatch, primaryPatch, selectedPatches, clearSelection,
    currentCandidate, selectCandidate, selectTargetForCrop, moveCropZ,
    selectPatch, selectAllPatches, getCommonOrMixed,
    pushHistory, undoHistory, redoHistory, jumpHistory,
    loadLayout, loadRefs, loadPipelineAndPlan, loadSeams, refreshAll,
    startPolling, stopPolling,
    addCrop, updateCrop, deleteCrop,
    openMaskSession, saveMaskSession,
  };
})();
