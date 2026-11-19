// BMK Design Patch Studio v2 — 중앙 상태 저장소 · 설정 동기화 · 멀티선택 · 히스토리 · 가시성 인식 폴링.
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
    focusDimAlpha: 42,
    forgeThemeSync: true,
    showAutoCrop: false,
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
    mask: null,      // 활성 마스크 편집 세션 { key, info, win, base, m, start, layers, dirty, undo, redo, file, tool, size, hard, flow }
    selectedIds: new Set(),
    primaryId: null,
    lastClickedId: null,
    selectedTargetByCrop: new Map(), // crop_id -> target.key (다중 타깃 A/B/C 지원)
    selectedCandByTarget: new Map(), // target.key -> cand.key
    leftTab: "patches",              // patches | refs
    rightTab: "instruct",            // instruct | review | retouch
    tool: "select",                  // select | rect | brush | eraser | pan
    viewFlags: {
      beforeHeld: false,
      splitOn: false,
      splitRatio: 0.5,
      seamsOn: false,
    },
    prefs: Object.assign({}, DEFAULT_PREFS),
    patchMeta: new Map(), // crop_id -> { purpose, direction_ko, aiAuto, summary_ko, prompt_en, skill_ids, modelOverride, quality, count, opts, hidden, rotDeg, filters }
    history: [],          // 포토샵식 히스토리 항목 [{ id, label, time, undoOp, redoOp }]
    histIndex: -1,
    assistBusy: false,
    syncState: "idle",    // idle | syncing | err
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

  // ── 토스트 알림 ──
  function toast(message, kind = "") {
    const host = document.getElementById("toast-host");
    if (!host) return;
    const item = Data.el("div", { className: `toast-item ${kind}`.trim(), text: message });
    host.append(item);
    setTimeout(() => item.remove(), kind === "err" ? 5200 : 3200);
  }

  // ── 설정 불러오기 · 저장 · 적용 (서버 모드에서는 DPEnv.store 로 자동 계정 동기화) ──
  function storageObj() {
    if (window.DPEnv && window.DPEnv.store) return window.DPEnv.store;
    try { return window.localStorage; } catch (_) { return null; }
  }

  function loadPrefs() {
    const st = storageObj();
    if (!st) return;
    try {
      const raw = st.getItem(PREF_KEY);
      if (raw) Object.assign(state.prefs, DEFAULT_PREFS, JSON.parse(raw));
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

  function applyPrefsToDom() {
    const rootEl = document.documentElement;
    const theme = Data.THEMES.find((t) => t.id === state.prefs.themeId) || Data.THEMES[0];
    const point = Data.POINT_COLORS.find((p) => p.id === state.prefs.pointColor) || Data.POINT_COLORS[0];
    rootEl.dataset.theme = theme.mode;
    for (const [k, v] of Object.entries(theme.vars)) rootEl.style.setProperty(k, v);
    rootEl.style.setProperty("--accent", point.hex);
    rootEl.style.setProperty("--accent-hover", point.hover);
    rootEl.style.setProperty("--on-accent", point.on);
    rootEl.style.setProperty("--accent-soft", Data.hexToRgba(point.hex, theme.mode === "light" ? 0.16 : 0.12));
    rootEl.style.setProperty("--accent-border", Data.hexToRgba(point.hex, theme.mode === "light" ? 0.55 : 0.45));
    const scale = Math.min(140, Math.max(85, Number(state.prefs.uiScale) || 100));
    rootEl.style.setProperty("--ui-scale", String(scale / 100));
    const ws = document.getElementById("workspace");
    if (ws) {
      ws.classList.remove("layout-standard", "layout-right-stack", "layout-left-stack", "layout-focus");
      ws.classList.add("layout-" + (state.prefs.layoutPreset || "standard"));
    }
    const dimInput = document.getElementById("input-focus-dim");
    const dimVal = document.getElementById("val-focus-dim");
    if (dimInput) dimInput.value = String(state.prefs.focusDimAlpha ?? 42);
    if (dimVal) dimVal.textContent = `${state.prefs.focusDimAlpha ?? 42}%`;
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

  // ── 패치 메타 · 스펙 동기화(§3.1–§3.3) · 통합 뷰 ──
  function ensurePatchMeta(cropId, cropObj) {
    let m = state.patchMeta.get(cropId);
    if (!m) {
      const targetText = (cropObj && Array.isArray(cropObj.targets) && cropObj.targets[0] && cropObj.targets[0].text)
        || (cropObj && cropObj.note) || "";
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
        filters: { brightness: 0, contrast: 0, saturation: 0, feather: 0, edgeFade: 0 },
      };
      state.patchMeta.set(cropId, m);
    }
    return m;
  }

  function applySpecResponse(cropId, res) {
    if (!res || typeof res !== "object") return;
    const m = ensurePatchMeta(cropId);
    m.specLoaded = true;
    if ("seen" in res) m.seen = res.seen ?? "null";
    if ("crop_sig" in res) m.crop_sig = res.crop_sig ?? "";
    if ("state" in res) m.specState = res.state || "template";
    if ("why" in res) m.specWhy = res.why || null;
    if ("prompt_en" in res) m.prompt_en = res.prompt_en || "";
    if ("input_hash" in res) m.specInputHash = res.input_hash || "";
    if (Array.isArray(res.images)) m.specImages = res.images;
    if (res.v2 && typeof res.v2 === "object") {
      if (res.v2.purpose) m.purpose = res.v2.purpose;
      if (typeof res.v2.user_instruction_ko === "string") m.user_instruction_ko = res.v2.user_instruction_ko;
      if (typeof res.v2.ai_auto === "boolean") m.aiAuto = res.v2.ai_auto;
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
    } catch (_) {
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
    const res = await Api.saveSpec(state.root, payload);
    if (res && !res.unsupported) {
      applySpecResponse(patch.id, res);
    }
    emit("selection");
    return res;
  }

  function projectPrepare() {
    const p = state.P && state.P.values && state.P.values.prepare;
    const catalog = Data.modelCatalog();
    const defModel = (p && p.model) || (catalog[0] && catalog[0].name) || "gpt-image-2.6-sunburst";
    const info = Data.findModel(defModel);
    return {
      model: defModel,
      quality: (p && p.quality) || (info && info.default_quality) || "max",
      n: Number((p && p.n) || 4),
      complexity: (p && p.complexity) || "high",
      opts: Object.assign({}, info ? info.opt_defaults : {}, (p && p.opts) || {}),
    };
  }

  function getPatches() {
    const layoutCrops = (state.L && Array.isArray(state.L.crops)) ? state.L.crops : [];
    const stateCrops = (state.S && Array.isArray(state.S.crops)) ? state.S.crops : [];
    const targets = (state.S && Array.isArray(state.S.targets)) ? state.S.targets : [];
    const refCrops = (state.R && Array.isArray(state.R.crops)) ? state.R.crops : [];

    const stateMap = new Map(stateCrops.map((c) => [c.id, c]));
    const refMap = new Map(refCrops.map((c) => [c.id, c]));
    let sourceList = layoutCrops.length ? layoutCrops.slice() : stateCrops.slice();

    // S.z.crops 기준 위→아래(Z-order 높은 레이어가 목록 위쪽, rows.js:order 와 동일)
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

  function primaryPatch() {
    const list = getPatches();
    if (!list.length) return null;
    if (state.primaryId) {
      const found = list.find((p) => p.id === state.primaryId);
      if (found) return found;
    }
    return list[0];
  }

  function selectedPatches() {
    const list = getPatches();
    if (!state.selectedIds.size) {
      const p = primaryPatch();
      return p ? [p] : [];
    }
    const picked = list.filter((p) => state.selectedIds.has(p.id));
    return picked.length ? picked : (list[0] ? [list[0]] : []);
  }

  function currentCandidate(patch) {
    const p = patch || primaryPatch();
    if (!p || !p.target || !p.candidates.length) return null;
    const key = state.selectedCandByTarget.get(p.target.key);
    return p.candidates.find((c) => c.key === key) || p.pickedCand || p.candidates[0] || null;
  }

  function selectCandidate(targetKey, candKey) {
    state.selectedCandByTarget.set(targetKey, candKey);
    emit("selection");
  }

  // ── 유니티식 다중 선택 (Shift 범위 · Ctrl 토글) ──
  function selectPatch(id, opts = {}) {
    const list = getPatches();
    if (!list.some((p) => p.id === id)) return;
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
      if (state.selectedIds.has(id) && state.selectedIds.size > 1) {
        state.selectedIds.delete(id);
        if (state.primaryId === id) state.primaryId = [...state.selectedIds][0];
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
      const onlyIds = [...state.selectedIds];
      const only = onlyIds.length && onlyIds.length < getPatches().length ? onlyIds.join(",") : null;
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
    state.syncState = "syncing";
    state.syncMsg = "동기화 중…";
    emit("sync");
    try {
      const prev = state.S;
      const d = await Api.getState(state.root, force || !prev ? undefined : prev.rev);
      let fullReload = !d.unchanged;
      if (d.unchanged && prev) {
        prev.running = d.running;
        prev.drain = d.drain;
        prev.drafting = d.drafting;
        if ("export_state" in d) {
          prev.export_state = d.export_state;
          prev.export_last = d.export_last;
        }
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
        if (patches.length && (!state.primaryId || !patches.some((p) => p.id === state.primaryId))) {
          state.primaryId = patches[0].id;
          state.selectedIds = new Set([patches[0].id]);
        }
        if (state.primaryId && Api.can("v2_spec_edit")) {
          await loadSpec(state.primaryId, true);
        }
      } else if (state.S && state.S.running) {
        try { state.progress = await Api.getProgress(state.root); } catch (_) {}
      }
      state.syncState = "idle";
      state.syncMsg = state.S ? `rev ${state.S.rev} · 동기화됨` : "연결됨";
      emit("state");
    } catch (err) {
      state.syncState = "err";
      state.syncMsg = err.message || "연결 오류";
      emit("sync");
    } finally {
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
        const rest = getPatches();
        state.primaryId = rest[0] ? rest[0].id : null;
        if (state.primaryId) state.selectedIds.add(state.primaryId);
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
        layers,
        dirty: false,
        ops: [],
        brushSize: 48,
        brushHard: 0.65,
        brushFlow: 0.8,
      };
      emit("mask");
      return state.mask;
    } catch (err) {
      toast(`마스크 불러오기 실패: ${err.message}`, "err");
      state.mask = null;
      emit("mask");
      return null;
    }
  }

  async function saveMaskSession() {
    const mk = state.mask;
    if (!mk || !mk.dirty) return false;
    const px = new Uint8Array(mk.w * mk.h);
    for (let i = 0; i < px.length; i++) px[i] = Math.floor(Math.min(1, Math.max(0, mk.m[i])) * 255 + 0.5);
    const opsText = (mk.ops.slice(-8).join(" · ") || "브러시 수정").slice(0, 190);
    const res = await Api.saveMaskPng(state.root, {
      key: mk.key, seen: mk.file, ops: opsText, rect: mk.win.join(","),
    }, px, mk.w, mk.h);
    if (res.conflict) {
      toast("다른 곳에서 마스크가 변경되었습니다 — 다시 불러옵니다.", "warn");
      await openMaskSession(mk.key);
      return false;
    }
    mk.file = (res.edit && res.edit.file) || mk.file;
    mk.start = mk.m.slice();
    mk.dirty = false;
    mk.ops = [];
    toast("마스크가 저장되었습니다.", "ok");
    await refreshAll(true);
    emit("mask");
    return true;
  }

  return {
    state, subscribe, emit, toast,
    loadPrefs, savePrefs, applyPrefsToDom, initForgeThemeListener,
    ensurePatchMeta, loadSpec, savePatchSpec, projectPrepare, getPatches, getPatch, primaryPatch, selectedPatches,
    currentCandidate, selectCandidate, selectTargetForCrop, moveCropZ, selectPatch, selectAllPatches, getCommonOrMixed,
    pushHistory, undoHistory, redoHistory, jumpHistory,
    refreshAll, loadLayout, loadRefs, loadPipelineAndPlan, loadSeams, startPolling, stopPolling,
    addCrop, updateCrop, deleteCrop, openMaskSession, saveMaskSession,
  };
})();
