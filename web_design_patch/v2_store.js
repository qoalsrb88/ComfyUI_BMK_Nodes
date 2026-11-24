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
    home: { pinnedIds: [], viewMode: "grid", sortBy: "recent" },
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
    CG: null,        // GET /crop_gen (V2-B #C19)
    cropGenById: new Map(),
    cropGenSeen: null,
    cropGenError: null,
    cropGenBusy: false,
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
    prefs: Object.assign({}, DEFAULT_PREFS, {
      historyPanel: Object.assign({}, DEFAULT_PREFS.historyPanel),
      modelPopoverSize: Object.assign({}, DEFAULT_PREFS.modelPopoverSize),
      home: { pinnedIds: [], viewMode: "grid", sortBy: "recent" },
    }),
    patchMeta: new Map(),
    history: [],
    histIndex: -1,
    assistBusy: false,
    runFlowBusy: false,
    pickBusyKeys: new Set(),
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
        onClick: () => {
          if (action.onClick() !== false) item.remove();
        },
      }));
    }
    host.append(item);
    setTimeout(() => { if (item.parentNode) item.remove(); }, kind === "err" ? 5400 : 3400);
  }

  // ── 설정 불러오기 · 저장 · DOM 적용 ──
  const PREFS_CHANNEL_NAME = "bmk-dp-v2-prefs";
  let syncedPrefsDelta = {};
  let prefsChannel = null;

  function currentAccountTag() {
    return String((window.DPEnv && window.DPEnv.account && window.DPEnv.account.tag) || "");
  }

  function mergePrefsObjects(base, patch) {
    const out = Object.assign({}, base && typeof base === "object" ? base : {});
    if (!patch || typeof patch !== "object") return out;
    for (const [k, v] of Object.entries(patch)) {
      if ((k === "historyPanel" || k === "home") && v && typeof v === "object" && !Array.isArray(v)) {
        out[k] = Object.assign({}, out[k] && typeof out[k] === "object" ? out[k] : {}, v);
      } else {
        out[k] = v;
      }
    }
    return out;
  }

  function ensurePrefsSyncChannel() {
    if (prefsChannel || typeof BroadcastChannel !== "function") return prefsChannel;
    try {
      prefsChannel = new BroadcastChannel(PREFS_CHANNEL_NAME);
      prefsChannel.onmessage = (ev) => {
        const msg = ev && ev.data;
        if (!msg || typeof msg !== "object" || !msg.patch || typeof msg.patch !== "object") return;
        // 서버 모드에서 계정 태그가 다르면 다른 계정의 설정을 섞지 않는다
        if (String(msg.accountTag || "") !== currentAccountTag()) return;
        syncedPrefsDelta = mergePrefsObjects(syncedPrefsDelta, msg.patch);
        if (msg.patch.historyPanel && typeof msg.patch.historyPanel === "object") {
          state.prefs.historyPanel = Object.assign({}, state.prefs.historyPanel || DEFAULT_PREFS.historyPanel, msg.patch.historyPanel);
        }
        if (msg.patch.home && typeof msg.patch.home === "object") {
          state.prefs.home = normalizeHomePrefs(Object.assign({}, getHomePrefs(), msg.patch.home));
        }
      };
    } catch (_) { prefsChannel = null; }
    return prefsChannel;
  }

  function broadcastPrefsDelta(patch) {
    if (!patch || typeof patch !== "object") return;
    const ch = ensurePrefsSyncChannel();
    if (!ch) return;
    try {
      ch.postMessage({ accountTag: currentAccountTag(), patch });
    } catch (_) { /* 채널 차단 환경 무시 */ }
  }

  function storageObj() {
    if (window.DPEnv && window.DPEnv.store) return window.DPEnv.store;
    try { return window.localStorage; } catch (_) { return null; }
  }

  function readStoredPrefsRaw(st) {
    if (!st) return null;
    try {
      const raw = st.getItem(PREF_KEY);
      if (!raw) return null;
      const parsed = JSON.parse(raw);
      return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : null;
    } catch (_) {
      return null;
    }
  }

  function readMergedStoredPrefs(st) {
    const raw = readStoredPrefsRaw(st);
    const hasDelta = Object.keys(syncedPrefsDelta).length > 0;
    if (!raw && !hasDelta) return null;
    return mergePrefsObjects(raw || {}, syncedPrefsDelta);
  }

  function normalizeHomePrefs(rawHome) {
    const h = (rawHome && typeof rawHome === "object") ? rawHome : {};
    return {
      pinnedIds: Array.isArray(h.pinnedIds)
        ? h.pinnedIds.filter((id) => typeof id === "string" && id).slice(0, 100)
        : [],
      viewMode: h.viewMode === "list" ? "list" : "grid",
      sortBy: ["recent", "name", "stars", "crops"].includes(h.sortBy) ? h.sortBy : "recent",
    };
  }

  function loadPrefs() {
    ensurePrefsSyncChannel();
    const st = storageObj();
    if (!st) return;
    try {
      const parsed = readMergedStoredPrefs(st);
      if (parsed) {
        Object.assign(state.prefs, DEFAULT_PREFS, parsed);
        // 기존 snapGrid(boolean)만 있던 설정을 snapStep으로 호환 마이그레이션
        if (parsed.snapStep === undefined && parsed.snapGrid === false) {
          state.prefs.snapStep = 1;
        }
        if (![1, 16, 32, 64, 128].includes(Number(state.prefs.snapStep))) {
          state.prefs.snapStep = 128;
        }
        state.prefs.historyPanel = Object.assign({}, DEFAULT_PREFS.historyPanel, parsed.historyPanel || {});
        state.prefs.modelPopoverSize = Object.assign({}, DEFAULT_PREFS.modelPopoverSize, parsed.modelPopoverSize || {});
        state.prefs.home = normalizeHomePrefs(parsed.home);
      } else {
        state.prefs.home = normalizeHomePrefs(null);
      }
    } catch (_) { /* 손상된 설정은 기본값 사용 */ }
  }

  function getHomePrefs() {
    if (!state.prefs.home || typeof state.prefs.home !== "object") {
      state.prefs.home = normalizeHomePrefs(null);
    }
    return state.prefs.home;
  }

  function latestHomePrefs() {
    const latest = readMergedStoredPrefs(storageObj());
    if (latest && latest.home && typeof latest.home === "object") {
      return normalizeHomePrefs(latest.home);
    }
    return normalizeHomePrefs(getHomePrefs());
  }

  function saveHomePrefs(patch) {
    // #C36-7: 저장 직전 최신 저장소의 home 값을 읽어 바뀐 항목만 합친다
    const cur = latestHomePrefs();
    const homeDelta = {};
    if (Array.isArray(patch && patch.pinnedIds)) {
      homeDelta.pinnedIds = patch.pinnedIds.filter((id) => typeof id === "string" && id).slice(0, 100);
    }
    if (patch && patch.viewMode !== undefined) {
      homeDelta.viewMode = patch.viewMode === "list" ? "list" : "grid";
    }
    if (patch && patch.sortBy !== undefined && ["recent", "name", "stars", "crops"].includes(patch.sortBy)) {
      homeDelta.sortBy = patch.sortBy;
    }
    const next = Object.assign({}, cur, homeDelta);
    savePrefs({ home: next }, { broadcastPatch: { home: homeDelta } });
    return next;
  }

  // #C40-6: 히스토리 패널 도킹/위치 변경 시 다른 탭이 저장한 크기(w/h) 등을 덮어쓰지 않도록 최신 저장 값에 변경분만 합친다
  function saveHistoryPanelPrefs(patch) {
    const latest = readMergedStoredPrefs(storageObj());
    const base = Object.assign(
      {},
      DEFAULT_PREFS.historyPanel,
      (latest && latest.historyPanel && typeof latest.historyPanel === "object")
        ? latest.historyPanel
        : (state.prefs.historyPanel || {})
    );
    const delta = (patch && typeof patch === "object") ? patch : {};
    const next = Object.assign({}, base, delta);
    savePrefs({ historyPanel: next }, { broadcastPatch: { historyPanel: delta } });
    return next;
  }

  function togglePinnedProject(projectId) {
    const id = String(projectId || "");
    if (!id) return false;
    const cur = latestHomePrefs();
    const exists = cur.pinnedIds.includes(id);
    const nextIds = exists
      ? cur.pinnedIds.filter((x) => x !== id)
      : [id, ...cur.pinnedIds].slice(0, 100);
    saveHomePrefs({ pinnedIds: nextIds });
    return !exists;
  }

  function savePrefs(patch, opts = {}) {
    const delta = (patch && typeof patch === "object") ? patch : {};
    Object.assign(state.prefs, delta);
    applyPrefsToDom();
    const st = storageObj();
    if (st) {
      try {
        // #C36-7 · #C40-6: 오래 열어 둔 탭이 다른 탭의 설정(테마·포인트·히스토리 크기 등)을 통째로 덮어쓰지 않도록 최신 저장·동기화 값에 변경분만 합친다
        const latest = readMergedStoredPrefs(st);
        const toSave = latest ? Object.assign({}, latest, delta) : Object.assign({}, state.prefs);
        st.setItem(PREF_KEY, JSON.stringify(toSave));
        syncedPrefsDelta = {};
      } catch (_) { /* 저장소 차단 무시 */ }
    }
    broadcastPrefsDelta((opts && opts.broadcastPatch) || delta);
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
        rawNeedsConfirm: false,
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
        filtersByCand: Object.create(null),
      };
      state.patchMeta.set(cropId, m);
    }
    return m;
  }

  // #C13-6②: 후보정 슬라이더 초안과 _touched 를 영역 단위가 아니라 후보 key 단위로 분리한다
  function getCandidateFilters(patch, cand = null) {
    if (!patch || !patch.meta) return defaultFilters();
    const c = cand || currentCandidate(patch);
    const key = c && c.key ? c.key : "";
    if (!key) return patch.meta.filters;
    if (!patch.meta.filtersByCand) patch.meta.filtersByCand = Object.create(null);
    if (!patch.meta.filtersByCand[key]) {
      patch.meta.filtersByCand[key] = defaultFilters();
    }
    return patch.meta.filtersByCand[key];
  }

  // #C11-3 · #C13-6② · #C15-6 · #C18-6②: 서버 adjust 응답을 해당 후보 key 의 슬라이더 초안에 동기화한다
  // - forceOrSent === false: 미수정 축만 동기화
  // - forceOrSent === true: 모든 축을 서버 확정값으로 맞춤
  // - forceOrSent === 객체(보낼 때 스냅샷): 응답 대기 중 만지지 않아 보낼 때 값·눈 토글과 같은 축만 맞추고 _touched 해제
  function syncCandidateFiltersFromAdjust(cropId, candKey, adjust, forceOrSent = false) {
    if (!cropId || !candKey || !adjust || typeof adjust !== "object") return null;
    const pm = ensurePatchMeta(cropId);
    if (!pm.filtersByCand) pm.filtersByCand = Object.create(null);
    if (!pm.filtersByCand[candKey]) pm.filtersByCand[candKey] = defaultFilters();
    const f = pm.filtersByCand[candKey];
    const isSentObj = !!(forceOrSent && typeof forceOrSent === "object");
    const forceAll = forceOrSent === true;
    const t = f._touched || {};

    const canSyncFeather = forceAll
      || (isSentObj ? (f.feather === forceOrSent.feather && f.edgeEnabled === forceOrSent.edgeEnabled) : !t.feather);
    const canSyncDx = forceAll
      || (isSentObj ? (f.dx === forceOrSent.dx && f.transformEnabled === forceOrSent.transformEnabled) : !t.dx);
    const canSyncDy = forceAll
      || (isSentObj ? (f.dy === forceOrSent.dy && f.transformEnabled === forceOrSent.transformEnabled) : !t.dy);
    const canSyncRot = forceAll
      || (isSentObj ? (f.rot === forceOrSent.rot && f.transformEnabled === forceOrSent.transformEnabled) : !t.rot);
    const canSyncScale = forceAll
      || (isSentObj ? (f.scale === forceOrSent.scale && f.transformEnabled === forceOrSent.transformEnabled) : !t.scale);

    if (canSyncFeather && typeof adjust.blur === "number") {
      f.feather = Math.round(adjust.blur * 10) / 10;
    }
    if (canSyncDx && typeof adjust.dx === "number") f.dx = adjust.dx;
    if (canSyncDy && typeof adjust.dy === "number") f.dy = adjust.dy;
    if (canSyncRot && typeof adjust.rot === "number") f.rot = adjust.rot;
    if (canSyncScale && typeof adjust.sx === "number" && typeof adjust.sy === "number"
        && Math.abs(adjust.sx - adjust.sy) < 0.001) {
      f.scale = Math.round(adjust.sx * 100);
    }

    if (forceAll) {
      f._touched = {};
      f._tfTouched = false;
      f._edgeTouched = false;
    } else if (isSentObj) {
      const nextTouched = Object.assign({}, t);
      if (canSyncFeather) {
        delete nextTouched.feather;
        f._edgeTouched = false;
      }
      if (canSyncDx) delete nextTouched.dx;
      if (canSyncDy) delete nextTouched.dy;
      if (canSyncRot) delete nextTouched.rot;
      if (canSyncScale) delete nextTouched.scale;
      if (canSyncDx && canSyncDy && canSyncRot && canSyncScale) f._tfTouched = false;
      f._touched = nextTouched;
    }
    return f;
  }

  function markFieldDirty(meta, field) {
    if (!meta) return;
    if (!meta.dirtyFields) {
      meta.dirtyFields = { purpose: false, instruction: false, aiAuto: false, raw: false };
    }
    if (!meta._dirtyVer || typeof meta._dirtyVer !== "object") {
      meta._dirtyVer = { purpose: 0, instruction: 0, aiAuto: 0, raw: 0 };
    }
    // #C11-2: 원문/지시 편집 시작 시점의 seen 스냅샷을 기억해 두 창 간 레퍼런스 변경 충돌을 서버 409로 안전하게 감지한다
    if (field === "raw" && !meta.editBaseSeen && meta.specLoaded && meta.seen && meta.seen !== "null") {
      meta.editBaseSeen = meta.seen;
    }
    if (field in meta.dirtyFields) meta.dirtyFields[field] = true;
    if (field in meta._dirtyVer) meta._dirtyVer[field] = (meta._dirtyVer[field] || 0) + 1;
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
    const sentFields = opts.sentFields || null;
    const seenSent = opts.seenSent || null;

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
    // #C11-2 · #C18-2: 미저장 원문(rawDirty) 편집 중에는 editBaseSeen 을 보존해 다른 창의 레퍼런스 변경을 409 로 감지한다.
    //                  단 자기 부분 저장(!includedRaw)이 editBaseSeen 과 같은 seen 으로 통과했다면 레퍼런스가 그대로임이
    //                  검증된 것이므로 editBaseSeen 도 새 스펙 지문으로 함께 갱신한다.
    if ("seen" in res) {
      const incomingSeen = res.seen ?? "null";
      if (fromSave) {
        if (!includedRaw && m.editBaseSeen && seenSent && seenSent === m.editBaseSeen) {
          m.editBaseSeen = incomingSeen;
        }
        m.seen = incomingSeen;
      } else if (!wasLoaded || !dirtyRaw || !m.editBaseSeen) {
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

    const sentPurpose = fromSave && (!sentFields || sentFields.has("purpose"));
    const sentInst = fromSave && (!sentFields || sentFields.has("instruction"));
    const sentAiAuto = fromSave && (!sentFields || sentFields.has("aiAuto"));
    const sentRaw = fromSave && includedRaw;
    const directValues = !!opts.directFieldValues;
    const preValues = opts.preSendValues || null;
    const preVer = opts.preSendDirtyVer || null;
    const curVer = m._dirtyVer || {};
    // #C23-6: 실행 취소(fieldValues) 응답은 전송 직전 화면 값·편집 버전과 현재가 모두 같을 때만 반영해
    // 응답 대기 중 사용자가 새로 고친 값과 dirty 상태를 덮어쓰지 않는다
    const sameDirect = (fieldKey, curVal, preVal) => {
      if (!preValues) return true;
      const valSame = curVal === preVal;
      const verSame = !preVer || ((curVer[fieldKey] || 0) === (preVer[fieldKey] || 0));
      return valSame && verSame;
    };

    const purposeSameAsSent = sentPurpose && (directValues
      ? sameDirect("purpose", m.purpose, preValues ? preValues.purpose : undefined)
      : (!sent || m.purpose === sent.purpose));
    const instSameAsSent = sentInst && (directValues
      ? sameDirect("instruction", m.user_instruction_ko || "", preValues ? preValues.user_instruction_ko : undefined)
      : (!sent || (m.user_instruction_ko || "") === (sent.user_instruction_ko || "")));
    const aiAutoSameAsSent = sentAiAuto && (directValues
      ? sameDirect("aiAuto", !!m.aiAuto, preValues ? !!preValues.aiAuto : undefined)
      : (!sent || !!m.aiAuto === !!sent.aiAuto));
    const rawSameAsSent = sentRaw && (directValues
      ? sameDirect("raw", m.body_en || "", preValues ? preValues.body_en : undefined)
      : (!sent || (m.body_en || "") === (sent.body_en || "")));

    const canOverwritePurpose = (directValues && sentPurpose) ? purposeSameAsSent : (!dirtyPurpose || purposeSameAsSent);
    const canOverwriteInst = (directValues && sentInst) ? instSameAsSent : (!dirtyInstruction || instSameAsSent);
    const canOverwriteAiAuto = (directValues && sentAiAuto) ? aiAutoSameAsSent : (!dirtyAiAuto || aiAutoSameAsSent);
    const canOverwriteRaw = (directValues && sentRaw) ? rawSameAsSent : (!dirtyRaw || rawSameAsSent);

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
      if (!m.rawNeedsConfirm || opts.confirmStaleRaw) {
        m.rawNeedsConfirm = false;
      }
    }
    // 레거시 dirtySpec 단독 플래그 호환 갱신
    if (fromSave && !sentFields && purposeSameAsSent && instSameAsSent && aiAutoSameAsSent) {
      m.dirtySpec = false;
    } else {
      m.dirtySpec = !!(df.purpose || df.instruction || df.aiAuto || (!hasFieldFlag && m.dirtySpec && (!fromSave || !!sentFields)));
    }
    m.rawDirty = !!(df.raw || (!rawSameAsSent && m.rawDirty));
  }

  const inflightSpecLoads = new Map();

  function invalidateAllSpecs() {
    for (const m of state.patchMeta.values()) {
      m.specLoaded = false;
    }
  }

  async function loadSpec(cropId, force = false) {
    if (!state.root || !cropId || !Api.can("v2_spec_edit")) return null;
    const m = ensurePatchMeta(cropId);
    // #C30-2: 직전 조회가 실패(_lastLoadOk === false)했으면 캐시된 specLoaded 로 건너뛰지 않고 다시 읽는다
    if (m.specLoaded && m._lastLoadOk !== false && !force) {
      m._lastLoadOk = true;
      m._lastLoadErr = null;
      return m;
    }
    if (!force && inflightSpecLoads.has(cropId)) return inflightSpecLoads.get(cropId);
    // 비강제 조회 시 매니페스트(state.S.crops)에 아직 없는 새 크롭은 불필요한 GET /spec 409 왕복을 생략한다
    // (force=true 인 savePatchSpec·명시적 갱신은 항상 실제 서버에 조회)
    if (!force && state.S && Array.isArray(state.S.crops) && !state.S.crops.some((c) => c.id === cropId)) {
      m.needsApplyForSpec = true;
      m.specLoaded = false;
      m._lastLoadOk = false;
      m._lastLoadErr = "apply_needed";
      emit("selection");
      return m;
    }
    const run = (async () => {
      try {
        const res = await Api.getSpec(state.root, cropId);
        if (res) {
          applySpecResponse(cropId, res);
          m._lastLoadOk = true;
          m._lastLoadErr = null;
          emit("selection");
        } else {
          m._lastLoadOk = false;
          m._lastLoadErr = "empty_response";
        }
        return m;
      } catch (err) {
        m._lastLoadOk = false;
        m._lastLoadErr = (err && err.message) || "load_failed";
        if (err && err.status === 409 && err.data && err.data.reason === "apply_needed") {
          m.needsApplyForSpec = true;
          m.specLoaded = false;
          emit("selection");
        }
        return m;
      }
    })();
    inflightSpecLoads.set(cropId, run);
    try {
      return await run;
    } finally {
      if (inflightSpecLoads.get(cropId) === run) inflightSpecLoads.delete(cropId);
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
    const hasFieldsFilter = Array.isArray(opts.fields) && opts.fields.length > 0;
    const fieldsSet = hasFieldsFilter ? new Set(opts.fields) : null;
    const wantPurpose = !fieldsSet || fieldsSet.has("purpose");
    const wantInst = !fieldsSet || fieldsSet.has("instruction");
    const wantAiAuto = !fieldsSet || fieldsSet.has("aiAuto");
    const includeRaw = hasFieldsFilter
      ? fieldsSet.has("raw")
      : !!(
        opts.includeRaw
        || m.rawDirty
        || (opts.confirmStaleRaw && m.rawNeedsConfirm)
        || (opts.reconfirmBlockedManual && m.promptSource === "manual" && m.specState === "blocked" && !!(m.body_en || "").trim())
      );

    // #C18-5: 레퍼런스 변경 409 충돌 뒤 미저장 원문은 그 영역을 단일 선택해 확인·저장(confirmStaleRaw)하기 전까지 보내지 않는다
    if (includeRaw && m.rawNeedsConfirm && !opts.confirmStaleRaw) {
      const msg = "레퍼런스가 바뀐 뒤 원문 프롬프트 확인이 필요합니다 — 이 영역을 선택해 그림 번호를 확인하고 [저장]해 주세요.";
      throw Object.assign(new Error(msg), { staleRaw: true, rawNeedsConfirm: true, userMessage: msg });
    }

    const dfBefore = m.dirtyFields || {};
    const savingChangedPurpose = wantPurpose && !!(dfBefore.purpose || (m.dirtySpec && !dfBefore.instruction && !dfBefore.aiAuto));
    const savingChangedAiAuto = wantAiAuto && !!(dfBefore.aiAuto || (m.dirtySpec && !dfBefore.purpose && !dfBefore.instruction));

    const seenToSend = (includeRaw && m.editBaseSeen) ? m.editBaseSeen : m.seen;
    const fieldValues = (opts.fieldValues && typeof opts.fieldValues === "object") ? opts.fieldValues : null;
    const preSendValues = fieldValues ? {
      purpose: m.purpose,
      user_instruction_ko: m.user_instruction_ko || "",
      aiAuto: !!m.aiAuto,
      body_en: m.body_en || "",
    } : null;
    const preSendDirtyVer = fieldValues ? Object.assign(
      { purpose: 0, instruction: 0, aiAuto: 0, raw: 0 },
      m._dirtyVer || {}
    ) : null;
    const sent = {};
    const payload = {
      crop_id: patch.id,
      seen: seenToSend,
      crop_sig: m.crop_sig,
    };
    if (wantPurpose) {
      sent.purpose = (fieldValues && "purpose" in fieldValues) ? fieldValues.purpose : m.purpose;
      payload.purpose = sent.purpose;
    }
    if (wantInst) {
      sent.user_instruction_ko = (fieldValues && "instruction" in fieldValues) ? (fieldValues.instruction || "") : (m.user_instruction_ko || "");
      payload.user_instruction_ko = sent.user_instruction_ko;
    }
    if (wantAiAuto) {
      sent.aiAuto = (fieldValues && "aiAuto" in fieldValues) ? !!fieldValues.aiAuto : !!m.aiAuto;
      payload.ai_auto = sent.aiAuto;
    }
    if (includeRaw) {
      sent.body_en = (fieldValues && "raw" in fieldValues) ? (fieldValues.raw || "") : (m.body_en || "");
      payload.raw_prompt_override = sent.body_en;
    }
    try {
      const res = await Api.saveSpec(state.root, payload);
      if (res && !res.unsupported) {
        applySpecResponse(patch.id, res, {
          fromSave: true,
          includedRaw: includeRaw,
          sent,
          sentFields: fieldsSet,
          seenSent: seenToSend,
          confirmStaleRaw: !!opts.confirmStaleRaw,
          directFieldValues: !!fieldValues,
          preSendValues,
          preSendDirtyVer,
        });
        // #C18-4: 목적·AI 자동이 즉시 저장이 아닌 다른 길(다중 저장 등)로 바뀌어 저장되면 낡은 실행 취소 기록을 정리한다
        if (!opts.fromQuickSpec) {
          if (savingChangedPurpose) invalidateSpecHistory(patch.id, "purpose");
          if (savingChangedAiAuto) invalidateSpecHistory(patch.id, "aiAuto");
        }
      }
      emit("selection");
      return res;
    } catch (err) {
      if (err && err.status === 409 && err.data && err.data.reason === "stale") {
        const hadRawConflict = !!(includeRaw || m.rawDirty);
        m.editBaseSeen = null;
        if (hadRawConflict) {
          m.rawNeedsConfirm = true;
        }
        await loadSpec(patch.id, true);
        // #C15-4 · #C18-5: 원문 전송 중 409(stale) 가 나면 충돌한 영역을 단일 선택 중일 때만 원문 칸을 펼치고 확인을 안내한다
        if (includeRaw) {
          if (state.primaryId === patch.id && state.selectedIds.size === 1) {
            const det = document.getElementById("raw-prompt-details");
            if (det) det.open = true;
          }
          err.staleRaw = true;
          err.rawNeedsConfirm = true;
          err.userMessage = "레퍼런스가 바뀌었습니다 — 그림 번호를 확인한 뒤 다시 [저장]해 주세요";
        }
      }
      throw err;
    }
  }

  // #C21-3② · #C23-5 · #C30-4: 읽지 않은 영역의 목적·AI 자동을 누르고 응답 전에 생성·작성을 눌러도 빠지지 않도록 추적하며,
  // 서로 다른 두 칸을 잇달아 바꿔도 앞의 것이 취소되거나 같은 seen 으로 409 나지 않게 크롭별 직렬 큐 + 칸별 시퀀스로 처리하고
  // 선행 조회·저장이 실패로 끝난 크롭 ID 를 호출자에게 돌려줘 옛 값으로 작성이 나가지 않게 막는다
  const pendingQuickSpecPromises = new Set();
  const quickSpecQueues = new Map();

  async function awaitPendingQuickSpecs() {
    const failedCropIds = new Set();
    while (pendingQuickSpecPromises.size > 0) {
      const batch = [...pendingQuickSpecPromises];
      await Promise.allSettled(batch.map((entry) => entry.promise));
      for (const entry of batch) {
        if (entry.failed) failedCropIds.add(entry.cropId);
      }
    }
    return { failedCropIds: [...failedCropIds] };
  }

  // #C13-11 · #C18-2 · #C18-3 · #C18-4 · #C18-6① · #C21-2 · #C21-3② · #C21-4 · #C23-5 · #C30-4:
  // 단일 선택에서 목적·AI 자동을 바꿀 때 그 칸만 즉시 저장하고 정확한 이전 값 기준으로 토스트 [되돌리기] + Ctrl+Z 실행 취소를 제공한다
  function applyQuickSpecChange(patch, field, nextVal) {
    if (!patch || !patch.meta) return Promise.resolve(null);
    if (field !== "purpose" && field !== "aiAuto") return Promise.resolve(null);
    if (state.runFlowBusy || state.assistBusy) return Promise.resolve(null);
    const cropId = patch.id;
    const m = patch.meta;
    const normNext = field === "purpose" ? nextVal : !!nextVal;
    if (!m._quickSpecSeqByField || typeof m._quickSpecSeqByField !== "object") {
      m._quickSpecSeqByField = {};
    }
    const seq = (m._quickSpecSeqByField[field] = (m._quickSpecSeqByField[field] || 0) + 1);
    const entry = { promise: null, cropId, failed: false };

    const prevChain = quickSpecQueues.get(cropId) || Promise.resolve();
    const task = prevChain.catch(() => null).then(async () => {
      if (m._quickSpecSeqByField[field] !== seq) return null;
      // #C18-6① · #C21-3② · #C23-5 · #C26-4: 이미 접수된 즉시 저장은 뒤이어 생성/AI 작성이 눌려도
      // awaitPendingQuickSpecs() 가 끝날 때까지 기다려 주므로 assistBusy 로 취소하지 않고 끝까지 완료한다
      if (Api.can("v2_spec_edit") && !m.specLoaded && !m.needsApplyForSpec) {
        await loadSpec(cropId, false);
        if (m._quickSpecSeqByField[field] !== seq) return null;
        if (!m.specLoaded && !m.needsApplyForSpec) {
          await loadSpec(cropId, true);
          if (m._quickSpecSeqByField[field] !== seq) return null;
        }
        if (!m.specLoaded && !m.needsApplyForSpec) {
          entry.failed = true;
          toast("서버에서 기존 스펙을 읽지 못했습니다 — 잠시 후 다시 시도해 주세요.", "err");
          // #C33-1: 브라우저가 이미 뒤집어 둔 체크박스(#chk-ai-auto)와 목적 단추를 실제 값으로 되돌린다
          emit("selection");
          return null;
        }
      }

      const prevVal = field === "purpose" ? m.purpose : !!m.aiAuto;
      const wasDirty = !!(m.dirtyFields && m.dirtyFields[field]);
      if (prevVal === normNext && !wasDirty) return null;

      m[field] = normNext;
      markFieldDirty(m, field);
      emit("selection");
      if (!Api.can("v2_spec_edit")) return null;

      try {
        const res = await savePatchSpec(patch, {
          fields: [field],
          fromQuickSpec: true,
        });
        if (!res || res.unsupported) return res;
        if (prevVal !== normNext) {
          const patchName = patch.name;
          const restoreField = async (targetVal, expectedCurVal) => {
            const curPatch = getPatch(cropId);
            if (!curPatch) throw new Error("해당 수정 영역을 찾을 수 없습니다.");
            // #C21-4: 강제 GET /spec 의 실제 서버 응답 값과 기대값을 직접 대조해 다른 창의 변경을 놓치지 않는다
            if (Api.can("v2_spec_edit")) {
              const freshRes = await Api.getSpec(state.root, cropId);
              if (!freshRes) throw new Error("서버에서 최신 스펙을 읽지 못했습니다.");
              applySpecResponse(cropId, freshRes);
              const sv2 = (freshRes.v2 && typeof freshRes.v2 === "object") ? freshRes.v2 : null;
              const serverVal = field === "purpose"
                ? ((sv2 && sv2.purpose) || "correct")
                : (sv2 && typeof sv2.ai_auto === "boolean" ? sv2.ai_auto : true);
              if (serverVal !== expectedCurVal) {
                throw Object.assign(new Error("이미 다른 값으로 변경되어 실행 취소를 건너뛰었습니다."), {
                  skippedStaleHistory: true,
                });
              }
            }
            const localDirty = !!(curPatch.meta.dirtyFields && curPatch.meta.dirtyFields[field]);
            const localVal = field === "purpose" ? curPatch.meta.purpose : !!curPatch.meta.aiAuto;
            if (localDirty && localVal !== expectedCurVal) {
              throw Object.assign(new Error("이미 다른 값으로 변경되어 실행 취소를 건너뛰었습니다."), {
                skippedStaleHistory: true,
              });
            }
            // #C21-2 · #C23-6: 서버 저장이 성공한 뒤에만 화면 값이 바뀌도록 fieldValues 로 전송하고 저장 전에는 화면/dirty 를 건드리지 않는다
            await savePatchSpec(curPatch, {
              fields: [field],
              fieldValues: { [field]: targetVal },
              fromQuickSpec: true,
            });
            emit("selection");
          };
          const label = field === "purpose"
            ? `목적 변경 (${patchName}: ${Data.purposeInfo(prevVal).short} → ${Data.purposeInfo(normNext).short})`
            : `AI 자동 ${normNext ? "켜기" : "끄기"} (${patchName})`;
          pushHistory(
            label,
            () => restoreField(prevVal, normNext),
            () => restoreField(normNext, prevVal),
            { kind: "spec-field", cropId, field, beforeVal: prevVal, afterVal: normNext }
          );
          const histEntryId = state.history[state.histIndex] && state.history[state.histIndex].id;
          const msg = field === "purpose"
            ? `"${patchName}" 목적을 「${Data.purposeInfo(normNext).label}」(으)로 저장했습니다.`
            : `"${patchName}" AI 자동 최적화를 ${normNext ? "켰습니다" : "껐습니다"}.`;
          toast(msg, "ok", {
            label: "되돌리기",
            onClick: () => {
              if (state.runFlowBusy || state.assistBusy) {
                toast(state.assistBusy ? "AI 프롬프트 작성이 끝난 뒤 되돌려 주세요." : "생성 흐름이 진행 중일 때는 되돌릴 수 없습니다.");
                return false;
              }
              const top = state.history[state.histIndex];
              if (top && top.id === histEntryId) undoHistory();
              else toast("이후 다른 변경이 있어 히스토리 패널에서 되돌려 주세요.");
            },
          });
        }
        return res;
      } catch (err) {
        if (m._quickSpecSeqByField[field] === seq && !m.needsApplyForSpec) {
          entry.failed = true;
        }
        toast(`지시문 저장 실패: ${(err && (err.userMessage || err.message)) || "저장 실패"}`, "err");
        emit("selection");
        return null;
      }
    });

    const tracked = task.finally(() => {
      pendingQuickSpecPromises.delete(entry);
      if (quickSpecQueues.get(cropId) === tracked) quickSpecQueues.delete(cropId);
    });
    entry.promise = tracked;
    quickSpecQueues.set(cropId, tracked);
    pendingQuickSpecPromises.add(entry);
    return tracked;
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
    const canCropModel = Api.can("v2_crop_model");

    return sourceList.map((raw, idx) => {
      const sc = stateMap.get(raw.id) || raw;
      const rc = refMap.get(raw.id) || null;
      const cropTargets = targets.filter((t) => t.crop_id === raw.id);
      const chosenKey = state.selectedTargetByCrop.get(raw.id);
      const activeTarget = (chosenKey && cropTargets.find((t) => t.key === chosenKey)) || cropTargets[0] || null;
      const meta = ensurePatchMeta(raw.id, raw);
      const cg = canCropModel ? (state.cropGenById.get(raw.id) || null) : null;
      const eff = (cg && cg.effective && typeof cg.effective === "object") ? cg.effective : null;
      const cropGenBroken = !!(cg && cg.effective === null && cg.error);
      const effectiveModel = eff ? eff.model : (cropGenBroken ? "" : prep.model);
      const modelInfo = effectiveModel ? Data.findModelExact(effectiveModel) : null;
      const effectiveQuality = eff ? eff.quality : (cropGenBroken ? "" : prep.quality);
      const effectiveCount = eff ? eff.n : (cropGenBroken ? 0 : prep.n);
      const effectiveOpts = eff ? (eff.opts || {}) : (cropGenBroken ? {} : (prep.opts || {}));
      const effectiveComplexity = eff ? (eff.complexity ?? null) : (cropGenBroken ? null : (prep.complexity ?? null));
      const effectiveInherit = (eff && eff.inherit && typeof eff.inherit === "object")
        ? { model: !!eff.inherit.model, n: !!eff.inherit.n }
        : { model: !cropGenBroken, n: true };
      const effectiveClamped = (eff && eff.clamped !== undefined && eff.clamped !== null) ? eff.clamped : null;
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
        effectiveQuality,
        effectiveCount,
        effectiveOpts,
        effectiveComplexity,
        effectiveInherit,
        effectiveClamped,
        cropGen: cg,
        cropGenApplied: cg ? !!cg.applied : true,
        cropGenRatio: cg ? (cg.ratio || null) : null,
        cropGenAvailable: cg ? cg.available !== false : (modelInfo ? modelInfo.available !== false : true),
        cropGenWhy: cg ? (cg.why || null) : (modelInfo ? modelInfo.why : null),
        cropGenPromptStale: cg ? !!cg.prompt_stale : false,
        cropGenError: cg ? (cg.error || null) : null,
        cropGenBroken,
      };
    });
  }

  let maskSwitchAsking = false;

  function closeMaskSession() {
    if (!state.mask) return;
    state.mask = null;
    emit("mask");
  }

  function restoreSelectionToMask(ms) {
    if (!ms || !ms.key) return false;
    for (const p of getPatches()) {
      for (const tg of (p.targets || [])) {
        if ((tg.candidates || []).some((c) => c.key === ms.key)) {
          state.selectedIds = new Set([p.id]);
          state.primaryId = p.id;
          state.selectedTargetByCrop.set(p.id, tg.key);
          state.selectedCandByTarget.set(tg.key, ms.key);
          emit("selection");
          return true;
        }
      }
    }
    return false;
  }

  function askUnsavedMaskChoice() {
    return Data.askDialogChoice(
      "이전 후보에 저장하지 않은 마스크 붓 편집이 있습니다. 어떻게 할까요?",
      [
        { id: "save", label: "저장하기", primary: true },
        { id: "discard", label: "버리기", danger: true },
        { id: "cancel", label: "계속 편집" },
      ],
      { title: "마스크 편집 확인", cancelId: "cancel", focusId: "save" }
    );
  }

  function syncMaskWithActiveCandidate(opts = {}) {
    if (!state.mask) return;
    const cand = currentCandidate();
    if (cand && cand.key === state.mask.key) return;
    // #C13-7 · #C15-5: 미저장 획이 없으면 즉시 닫고, 있으면 명시적 「버리기」나 저장 성공 전까지 편집 판을 유지한다
    //                  Esc/닫기는 「계속 편집(cancel)」으로 처리해 획을 절대 버리지 않는다
    if (!state.mask.dirty) {
      closeMaskSession();
      return;
    }
    if (opts.fromPoll || maskSwitchAsking) return;
    const pendingMask = state.mask;
    maskSwitchAsking = true;
    askUnsavedMaskChoice().then(async (choice) => {
      if (choice === "discard") {
        if (state.mask === pendingMask) closeMaskSession();
        return;
      }
      if (choice !== "save") {
        if (state.mask === pendingMask) {
          restoreSelectionToMask(pendingMask);
          toast("마스크 편집을 유지합니다 — 저장하거나 [버리기]를 선택해 주세요.");
        }
        return;
      }
      const res = await saveMaskSession(pendingMask);
      // 저장이 실제로 성공(dirty 해제)했을 때만 닫는다 — 409 거절·실패 시 편집 판과 획을 보존
      if (res && res.ok && !res.conflict && !pendingMask.dirty) {
        const latest = currentCandidate();
        if (state.mask === pendingMask && (!latest || latest.key !== pendingMask.key)) {
          closeMaskSession();
        }
      }
    }).finally(() => {
      maskSwitchAsking = false;
      if (state.mask && !state.mask.dirty) {
        const latest = currentCandidate();
        if (!latest || latest.key !== state.mask.key) closeMaskSession();
      }
    });
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
  let historyBusy = false;

  function pushHistory(label, undoFn, redoFn, meta = null) {
    const entry = {
      id: Date.now() + "-" + Math.random().toString(36).slice(2, 6),
      label,
      time: new Date().toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }),
      undoFn,
      redoFn,
      meta: meta || null,
    };
    if (state.histIndex < state.history.length - 1) {
      state.history = state.history.slice(0, state.histIndex + 1);
    }
    state.history.push(entry);
    if (state.history.length > MAX_HISTORY) state.history.shift();
    state.histIndex = state.history.length - 1;
    emit("history");
  }

  // #C18-4: 같은 영역·칸이 다른 길(다중 저장 등)로 저장되면 그 칸에 대한 낡은 실행 취소 기록을 제거한다
  function invalidateSpecHistory(cropId, field) {
    if (!cropId || !field || !state.history.length) return;
    let nextIdx = state.histIndex;
    const kept = [];
    for (let i = 0; i < state.history.length; i++) {
      const h = state.history[i];
      const match = !!(h && h.meta && h.meta.kind === "spec-field"
        && h.meta.cropId === cropId && h.meta.field === field);
      if (match) {
        if (i <= state.histIndex) nextIdx -= 1;
      } else {
        kept.push(h);
      }
    }
    if (kept.length !== state.history.length) {
      state.history = kept;
      state.histIndex = Math.max(-1, Math.min(kept.length - 1, nextIdx));
      emit("history");
    }
  }

  async function undoHistory() {
    // #C18-3 · R2-4: 생성 흐름·AI 프롬프트 작성·모델 설정 저장 진행 중에는 실행 취소를 잠근다
    if (state.runFlowBusy || state.assistBusy || state.cropGenBusy) {
      toast(
        state.cropGenBusy
          ? "모델 설정 저장이 끝난 뒤 실행 취소해 주세요."
          : state.assistBusy ? "AI 프롬프트 작성이 끝난 뒤 실행 취소해 주세요." : "생성 흐름이 진행 중일 때는 실행 취소할 수 없습니다."
      );
      return false;
    }
    if (historyBusy || state.histIndex < 0) return false;
    historyBusy = true;
    const entry = state.history[state.histIndex];
    try {
      // #C21-2: 서버 저장이 성공한 뒤에만 기록 위치(histIndex)를 옮긴다
      if (typeof entry.undoFn === "function") await entry.undoFn();
      state.histIndex -= 1;
      toast(`실행 취소: ${entry.label}`);
      return true;
    } catch (err) {
      if (err && err.skippedStaleHistory) {
        state.histIndex -= 1;
        toast(err.message);
      } else {
        toast(`실행 취소 실패: ${err.message}`, "err");
      }
      return false;
    } finally {
      historyBusy = false;
      emit("history");
    }
  }

  async function redoHistory() {
    // #C18-3 · R2-4: 생성 흐름·AI 프롬프트 작성·모델 설정 저장 진행 중에는 다시 실행을 잠근다
    if (state.runFlowBusy || state.assistBusy || state.cropGenBusy) {
      toast(
        state.cropGenBusy
          ? "모델 설정 저장이 끝난 뒤 다시 실행해 주세요."
          : state.assistBusy ? "AI 프롬프트 작성이 끝난 뒤 다시 실행해 주세요." : "생성 흐름이 진행 중일 때는 다시 실행할 수 없습니다."
      );
      return false;
    }
    if (historyBusy || state.histIndex >= state.history.length - 1) return false;
    historyBusy = true;
    const entry = state.history[state.histIndex + 1];
    try {
      // #C21-2: 서버 저장이 성공한 뒤에만 기록 위치(histIndex)를 옮긴다
      if (typeof entry.redoFn === "function") await entry.redoFn();
      state.histIndex += 1;
      toast(`다시 실행: ${entry.label}`);
      return true;
    } catch (err) {
      if (err && err.skippedStaleHistory) {
        state.histIndex += 1;
        toast(err.message);
      } else {
        toast(`다시 실행 실패: ${err.message}`, "err");
      }
      return false;
    } finally {
      historyBusy = false;
      emit("history");
    }
  }

  async function jumpHistory(targetIdx) {
    if (state.runFlowBusy || state.assistBusy || state.cropGenBusy) {
      toast(
        state.cropGenBusy
          ? "모델 설정 저장이 끝난 뒤 히스토리를 이동해 주세요."
          : state.assistBusy ? "AI 프롬프트 작성이 끝난 뒤 히스토리를 이동해 주세요." : "생성 흐름이 진행 중일 때는 히스토리를 이동할 수 없습니다."
      );
      return false;
    }
    while (state.histIndex > targetIdx) {
      const ok = await undoHistory();
      if (!ok) break;
    }
    while (state.histIndex < targetIdx) {
      const ok = await redoHistory();
      if (!ok) break;
    }
    return true;
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

  // #C30-3 · #C33-2 · #C33-3 · #C33-4:
  // - 첫 레퍼런스 쓰기 전부터 진행 중 표지(activeRefWritePromises)와 쓰기 세대(refsWriteEpoch)를 올려 다중 연결 도중 생성 클릭을 대기시킨다
  // - 조회 순번(refsLoadSeq)과 세대 번호로 쓰기 전에 떠났거나 더 최신 조회보다 늦게 온 GET /refs 응답을 폐기한다
  // - 첫 프로젝트 진입에서 /refs 조회가 실패해 state.R 이 비어 있으면 생성 전에 다시 읽고 실패 시 중단한다
  let refsDirtyAfterWrite = false;
  let refsWriteEpoch = 0;
  let refsLoadSeq = 0;
  let refsLoadedOnce = false;
  const activeRefWritePromises = new Set();
  const pendingRefRefreshPromises = new Set();

  function trackRefWrite(promise) {
    refsWriteEpoch += 1;
    refsDirtyAfterWrite = true;
    const p = Promise.resolve(promise).finally(() => {
      // 쓰기 도중 출발한 GET /refs 가 쓰기 종료(또는 응답 유실) 직후 도착해 옛 스냅샷으로 dirty 를 지우지 못하게 종료 시점에도 세대를 올린다
      refsWriteEpoch += 1;
      refsDirtyAfterWrite = true;
      activeRefWritePromises.delete(p);
    });
    activeRefWritePromises.add(p);
    return p;
  }

  async function loadRefsDetailed(force = false) {
    if (!state.root) return { ok: false, superseded: false };
    const startEpoch = refsWriteEpoch;
    const mySeq = ++refsLoadSeq;
    let ok = false;
    try {
      const d = await Api.getRefs(state.root, force ? undefined : (state.R && state.R.rev));
      if (startEpoch !== refsWriteEpoch || mySeq !== refsLoadSeq) {
        return { ok: false, superseded: true };
      }
      if (d && !d.unchanged) state.R = d;
      if (d) {
        ok = true;
        if (state.R) refsLoadedOnce = true;
        if ((force || !d.unchanged) && activeRefWritePromises.size === 0) {
          refsDirtyAfterWrite = false;
        }
      }
    } catch (_) { /* 무시 — refsDirtyAfterWrite 는 유지되어 생성 전 재시도됨 */ }
    try {
      const sh = await Api.listSheets(state.root);
      if (startEpoch === refsWriteEpoch && mySeq === refsLoadSeq) {
        // #C07-1: GET /refs/sheets 는 배열을 그대로 반환 (테스트/호환용 { sheets: [...] } 도 함께 수용)
        if (Array.isArray(sh)) state.sheets = sh;
        else if (sh && Array.isArray(sh.sheets)) state.sheets = sh.sheets;
      }
    } catch (_) { /* 무시 */ }
    const superseded = startEpoch !== refsWriteEpoch || mySeq !== refsLoadSeq;
    return { ok: ok && !superseded, superseded };
  }

  async function loadRefs(force = false) {
    return (await loadRefsDetailed(force)).ok;
  }

  function refreshAfterRefWrite() {
    refsWriteEpoch += 1;
    refsDirtyAfterWrite = true;
    const p = refreshAll(true).finally(() => {
      pendingRefRefreshPromises.delete(p);
    });
    pendingRefRefreshPromises.add(p);
    return p;
  }

  async function ensureFreshRefsAfterWrite() {
    for (let attempt = 0; attempt < 4; attempt += 1) {
      while (activeRefWritePromises.size > 0 || pendingRefRefreshPromises.size > 0) {
        const batch = [...activeRefWritePromises, ...pendingRefRefreshPromises];
        await Promise.allSettled(batch);
      }
      if (!refsDirtyAfterWrite && refsLoadedOnce && state.R) return true;
      const res = await loadRefsDetailed(true);
      if (res.ok && !refsDirtyAfterWrite && refsLoadedOnce && state.R) return true;
      if (!res.superseded && activeRefWritePromises.size === 0 && pendingRefRefreshPromises.size === 0) {
        return false;
      }
    }
    return !refsDirtyAfterWrite && refsLoadedOnce && !!state.R;
  }

  // ── V2-B 패치별 생성 설정(crop_gen · STUDIO_UX_V2B_SPEC §10.3 · #C19) ──
  // R1-5: 뒤늦게 도착한 GET /crop_gen 응답이 더 최신 쓰기/조회 응답을 덮어쓰지 않도록 세대 번호로 보호한다
  let cropGenWriteEpoch = 0;
  let cropGenGetSeq = 0;

  function applyCropGenResponse(res, opts = {}) {
    if (!res || typeof res !== "object") return null;
    if (opts.fromWrite) cropGenWriteEpoch += 1;
    state.CG = res;
    if (Number.isInteger(res.seen)) state.cropGenSeen = res.seen;
    state.cropGenError = null;
    const map = new Map();
    if (Array.isArray(res.crops)) {
      for (const row of res.crops) {
        if (row && typeof row.crop_id === "string") map.set(row.crop_id, row);
      }
    }
    state.cropGenById = map;
    if (state.S && Number.isInteger(res.seen)) {
      state.S.crop_gen_rev = res.seen;
    }
    return res;
  }

  async function loadCropGen() {
    if (!state.root || !Api.can("v2_crop_model")) return null;
    const startEpoch = cropGenWriteEpoch;
    const mySeq = ++cropGenGetSeq;
    try {
      const res = await Api.getCropGen(state.root);
      if (startEpoch !== cropGenWriteEpoch || mySeq !== cropGenGetSeq) {
        return state.CG;
      }
      if (res && typeof res === "object") {
        applyCropGenResponse(res);
        return res;
      }
      return null;
    } catch (err) {
      if (startEpoch !== cropGenWriteEpoch || mySeq !== cropGenGetSeq) {
        return state.CG;
      }
      state.cropGenError = (err && err.message) || "패치별 모델 설정을 읽지 못했습니다";
      return null;
    }
  }

  function cloneGenEntry(gen) {
    if (!gen || typeof gen !== "object") return null;
    const out = {};
    if (typeof gen.model === "string") out.model = gen.model;
    if (typeof gen.quality === "string") out.quality = gen.quality;
    if (gen.opts && typeof gen.opts === "object") out.opts = Object.assign({}, gen.opts);
    if (typeof gen.complexity === "string") out.complexity = gen.complexity;
    if (Number.isInteger(gen.n)) out.n = gen.n;
    return Object.keys(out).length ? out : null;
  }

  function canonicalGenKey(gen) {
    const g = cloneGenEntry(gen);
    return g ? canonicalPatchKey(g) : "null";
  }

  function buildCropGenModelPatch(modelId, overrides = {}, baseEffective = null) {
    const patch = { model_id: modelId };
    const m = Data.findModelExact(modelId);
    if (!m) {
      if (overrides.quality !== undefined) patch.quality = overrides.quality;
      if (overrides.opts !== undefined) patch.opts = overrides.opts;
      if (overrides.complexity !== undefined) patch.complexity = overrides.complexity;
      return patch;
    }
    const sameModel = !!(baseEffective && baseEffective.model === modelId);
    if (m.supports_quality && Array.isArray(m.qualities) && m.qualities.length) {
      const candQ = overrides.quality !== undefined
        ? overrides.quality
        : (sameModel && m.qualities.includes(baseEffective.quality) ? baseEffective.quality : (m.default_quality || m.qualities[0]));
      patch.quality = m.qualities.includes(candQ) ? candQ : (m.default_quality || m.qualities[0]);
    }
    const optKeys = m.opts && typeof m.opts === "object"
      ? Object.keys(m.opts).filter((k) => k !== "aspect_ratio" && Array.isArray(m.opts[k]) && m.opts[k].length)
      : [];
    if (optKeys.length) {
      const nextOpts = {};
      const ovOpts = (overrides.opts && typeof overrides.opts === "object") ? overrides.opts : {};
      const baseOpts = (sameModel && baseEffective && baseEffective.opts && typeof baseEffective.opts === "object")
        ? baseEffective.opts : {};
      for (const k of optKeys) {
        const choices = m.opts[k];
        const v = ovOpts[k] !== undefined
          ? ovOpts[k]
          : (baseOpts[k] !== undefined ? baseOpts[k] : ((m.opt_defaults && m.opt_defaults[k]) || choices[0]));
        nextOpts[k] = choices.includes(v) ? v : ((m.opt_defaults && m.opt_defaults[k]) || choices[0]);
      }
      patch.opts = nextOpts;
    }
    if (Array.isArray(m.complexities) && m.complexities.length) {
      const candC = overrides.complexity !== undefined
        ? overrides.complexity
        : (sameModel && m.complexities.includes(baseEffective.complexity) ? baseEffective.complexity : "high");
      patch.complexity = m.complexities.includes(candC) ? candC : (m.complexities.includes("high") ? "high" : m.complexities[0]);
    }
    return patch;
  }

  // R1-2 · R1-3: 변경한 묶음(모델 묶음 / 장수 묶음)만 복원 패치에 담아
  // (1) 장수 일괄 변경이 20개 묶음 상한에 걸려 히스토리에서 빠지지 않게 하고
  // (2) 모델만 바꿔 서버가 장수 상한으로 낮춘(clamped) 패치를 다시 실행(Redo)할 때 원래 큰 n 을 count 로 보내 400 나는 결함을 막는다
  function genEntryToPatch(entry, opts = null) {
    const includeModel = !opts || opts.includeModel !== false;
    const includeCount = !opts || opts.includeCount !== false;
    const patch = {};
    if (includeModel) {
      if (entry && typeof entry === "object" && typeof entry.model === "string" && entry.model) {
        patch.model_id = entry.model;
        if (typeof entry.quality === "string") patch.quality = entry.quality;
        if (entry.opts && typeof entry.opts === "object") {
          const cleanOpts = {};
          for (const [k, v] of Object.entries(entry.opts)) {
            if (k !== "aspect_ratio") cleanOpts[k] = v;
          }
          if (Object.keys(cleanOpts).length) patch.opts = cleanOpts;
        }
        if (typeof entry.complexity === "string") patch.complexity = entry.complexity;
      } else {
        patch.inherit_model = true;
      }
    }
    if (includeCount) {
      patch.count = (entry && typeof entry === "object" && Number.isInteger(entry.n)) ? entry.n : null;
    }
    return patch;
  }

  function canonicalPatchKey(patch) {
    const keys = Object.keys(patch || {}).sort();
    const norm = {};
    for (const k of keys) {
      const v = patch[k];
      if (v && typeof v === "object" && !Array.isArray(v)) {
        const inner = {};
        for (const ik of Object.keys(v).sort()) inner[ik] = v[ik];
        norm[k] = inner;
      } else {
        norm[k] = v;
      }
    }
    return JSON.stringify(norm);
  }

  function buildGroupedChanges(pairs) {
    const byId = new Map();
    for (const [cid, patch] of (pairs || [])) {
      if (typeof cid === "string" && cid && patch && typeof patch === "object") {
        byId.set(cid, patch);
      }
    }
    const groups = new Map();
    for (const [cid, patch] of byId.entries()) {
      const key = canonicalPatchKey(patch);
      let g = groups.get(key);
      if (!g) {
        g = { crop_ids: [], patch };
        groups.set(key, g);
      }
      g.crop_ids.push(cid);
    }
    return [...groups.values()];
  }

  async function updateCropGen(changes, opts = {}) {
    const {
      historyLabel = "",
      skipHistory = false,
      throwOnError = false,
      expectedSeen = null,
      expectedGenById = null,
    } = opts || {};
    if (!state.root || !Api.can("v2_crop_model")) {
      return { ok: false, unsupported: true, error: "패치별 모델 선택 기능이 아직 켜져 있지 않습니다." };
    }
    if (state.cropGenBusy) {
      const msg = "이전 모델 설정을 저장하는 중입니다 — 잠시 후 다시 시도해 주세요.";
      if (!throwOnError) toast(msg);
      if (throwOnError) throw new Error(msg);
      return { ok: false, busy: true, error: msg };
    }
    if (!Array.isArray(changes) || !changes.length) {
      return { ok: true, changed: false, staleCount: 0 };
    }
    if (changes.length > 20) {
      const msg = "한 번에 최대 20개 변경 묶음까지만 저장할 수 있습니다 — 나눠서 적용해 주세요.";
      if (!throwOnError) toast(msg, "err");
      if (throwOnError) throw new Error(msg);
      return { ok: false, error: msg };
    }
    const changedIds = [];
    const seenIds = new Set();
    const touchedById = new Map();
    for (const ch of changes) {
      if (!ch || !Array.isArray(ch.crop_ids) || !ch.crop_ids.length || !ch.patch || typeof ch.patch !== "object") {
        const msg = "패치별 생성 설정 요청 형식이 올바르지 않습니다.";
        if (!throwOnError) toast(msg, "err");
        if (throwOnError) throw new Error(msg);
        return { ok: false, error: msg };
      }
      const incM = ("model_id" in ch.patch) || ("inherit_model" in ch.patch);
      const incN = ("count" in ch.patch);
      for (const cid of ch.crop_ids) {
        if (seenIds.has(cid)) {
          const msg = `같은 영역이 중복 지정되었습니다: ${cid}`;
          if (!throwOnError) toast(msg, "err");
          if (throwOnError) throw new Error(msg);
          return { ok: false, error: msg };
        }
        seenIds.add(cid);
        changedIds.push(cid);
        touchedById.set(cid, { includeModel: incM, includeCount: incN });
      }
    }
    if (changedIds.length > 200) {
      const msg = "한 번에 최대 200개 영역까지만 변경할 수 있습니다 — 나눠서 선택해 주세요.";
      if (!throwOnError) toast(msg, "err");
      if (throwOnError) throw new Error(msg);
      return { ok: false, error: msg };
    }

    // R2-2: 확인 상자를 열어 둔 사이 다른 창이 저장해 폴링으로 판이 바뀌었으면 저장 전에 중단한다
    if (Number.isInteger(expectedSeen) && Number.isInteger(state.cropGenSeen) && state.cropGenSeen !== expectedSeen) {
      const msg = "다른 창에서 패치별 모델 설정이 변경되어 저장을 중단했습니다 — 최신 값을 확인한 뒤 다시 시도해 주세요.";
      if (!throwOnError) toast(msg, "err");
      if (throwOnError) throw new Error(msg);
      return { ok: false, stale: true, error: msg };
    }

    // R1-2: 변경한 필드 기준 복원 묶음이 20개를 넘으면(서로 다른 모델 조합 21개+ 동시 덮어쓰기) POST 전에 확인을 받는다
    if (!skipHistory && historyLabel && Number.isInteger(state.cropGenSeen)) {
      const preCheckPairs = changedIds.map((cid) => {
        const row = state.cropGenById.get(cid);
        const gen = (row && !row.error) ? cloneGenEntry(row.gen) : null;
        return [cid, genEntryToPatch(gen, touchedById.get(cid))];
      });
      const preGroups = buildGroupedChanges(preCheckPairs);
      if (preGroups.length > 20) {
        const okProceed = await Data.askDialogConfirm(
          `선택한 영역의 기존 모델 조합이 ${preGroups.length}종(20종 초과)이라 저장 후 실행 취소(Ctrl+Z) 한 번으로 되돌릴 수 없습니다.\n그래도 일괄 적용할까요?`,
          { ok: "일괄 적용", cancel: "취소" }
        );
        if (!okProceed) return { ok: false, cancelled: true };
        // 확인 창 대기 중 다른 창이 수정했는지 다시 대조
        if (Number.isInteger(expectedSeen) && state.cropGenSeen !== expectedSeen) {
          const msg = "다른 창에서 패치별 모델 설정이 변경되어 저장을 중단했습니다 — 최신 값을 확인한 뒤 다시 시도해 주세요.";
          if (!throwOnError) toast(msg, "err");
          return { ok: false, stale: true, error: msg };
        }
      }
    }

    state.cropGenBusy = true;
    emit("selection");
    try {
      if (!Number.isInteger(state.cropGenSeen)) {
        await loadCropGen();
        if (!Number.isInteger(state.cropGenSeen)) {
          const msg = state.cropGenError || "서버에서 패치별 모델 설정을 읽지 못했습니다 — 잠시 후 다시 시도해 주세요.";
          if (!throwOnError) toast(msg, "err");
          if (throwOnError) throw new Error(msg);
          return { ok: false, error: msg };
        }
      }

      // R1-1: 실행 취소·다시 실행 시 현재 서버/스토어 gen 이 기록이 기대하는 상태와 다르면 다른 창의 수정을 덮어쓰지 않고 중단(histIndex 유지)
      if (expectedGenById && typeof expectedGenById === "object") {
        for (const cid of changedIds) {
          const row = state.cropGenById.get(cid);
          const curKey = canonicalGenKey(row && !row.error ? row.gen : null);
          const expKey = expectedGenById[cid] !== undefined ? expectedGenById[cid] : "null";
          if (!row || row.error || curKey !== expKey) {
            throw new Error("다른 창이나 이후 작업에서 패치별 모델 설정이 변경되어 되돌릴 수 없습니다.");
          }
        }
      }

      const beforeGenMap = Object.create(null);
      const beforeGenPairs = changedIds.map((cid) => {
        const row = state.cropGenById.get(cid);
        const gen = (row && !row.error) ? cloneGenEntry(row.gen) : null;
        beforeGenMap[cid] = canonicalGenKey(gen);
        return [cid, genEntryToPatch(gen, touchedById.get(cid))];
      });
      const beforeStaleIds = new Set(
        changedIds.filter((cid) => {
          const row = state.cropGenById.get(cid);
          return !!(row && row.prompt_stale);
        })
      );

      const seenToSend = Number.isInteger(expectedSeen) ? expectedSeen : state.cropGenSeen;
      const res = await Api.postCropGen(state.root, seenToSend, changes);
      if (res && res.unsupported) {
        if (!throwOnError) toast(res.error || "패치별 모델 선택 기능이 아직 켜져 있지 않습니다.", "err");
        if (throwOnError) throw new Error(res.error || "unsupported");
        return res;
      }
      applyCropGenResponse(res, { fromWrite: true });

      let staleCount = 0;
      if (res && res.changed) {
        const afterGenMap = Object.create(null);
        const afterGenPairs = changedIds.map((cid) => {
          const row = state.cropGenById.get(cid);
          const gen = (row && !row.error) ? cloneGenEntry(row.gen) : null;
          afterGenMap[cid] = canonicalGenKey(gen);
          return [cid, genEntryToPatch(gen, touchedById.get(cid))];
        });
        // R1-7: 이번 변경 전에는 prompt_stale=false 였고 응답에서 새로 true 가 된 패치만 집계한다
        const staleIds = changedIds.filter((cid) => {
          const row = state.cropGenById.get(cid);
          return !!(row && row.prompt_stale && !beforeStaleIds.has(cid));
        });
        staleCount = staleIds.length;

        invalidateAllSpecs();
        if (!skipHistory && historyLabel) {
          const undoChanges = buildGroupedChanges(beforeGenPairs);
          const redoChanges = buildGroupedChanges(afterGenPairs);
          const diffFromBefore = JSON.stringify(undoChanges) !== JSON.stringify(redoChanges);
          if (diffFromBefore && undoChanges.length >= 1 && undoChanges.length <= 20 && redoChanges.length >= 1 && redoChanges.length <= 20) {
            pushHistory(
              historyLabel,
              () => updateCropGen(undoChanges, { skipHistory: true, throwOnError: true, expectedGenById: afterGenMap }),
              () => updateCropGen(redoChanges, { skipHistory: true, throwOnError: true, expectedGenById: beforeGenMap }),
              { kind: "crop-gen", cropIds: changedIds.slice() }
            );
          }
        }
        if (staleCount > 0 && !skipHistory) {
          toast(`AI 작성본 ${staleCount}개가 다시 작성됩니다(경량 LLM 호출)`);
        }
        // R2-4: 최신 스펙과 계획을 받은 뒤 busy 를 풀어 직후 생성/AI 작성이 옛 모델 계획을 보지 않게 한다
        const syncTasks = [loadPipelineAndPlan()];
        if (state.primaryId && seenIds.has(state.primaryId) && Api.can("v2_spec_edit")) {
          syncTasks.push(loadSpec(state.primaryId, true));
        }
        await Promise.all(syncTasks);
      }
      emit("selection");
      return { ok: true, res, changed: !!(res && res.changed), staleCount };
    } catch (err) {
      if (err && err.status === 409 && err.data && err.data.reason === "stale") {
        // R1-4: 409 충돌에서 최신 crop_gen 판을 수용할 때 스펙 캐시·활성 스펙·계획도 함께 갱신한다
        if (Array.isArray(err.data.crops)) {
          applyCropGenResponse(err.data, { fromWrite: true });
          invalidateAllSpecs();
          const syncTasks = [loadPipelineAndPlan()];
          if (state.primaryId && Api.can("v2_spec_edit")) {
            syncTasks.push(loadSpec(state.primaryId, true));
          }
          await Promise.all(syncTasks);
        }
        const msg = "다른 창에서 패치별 모델 설정이 변경되어 최신 값으로 다시 불러왔습니다 — 확인 후 다시 시도해 주세요.";
        if (!throwOnError) toast(msg, "err");
        emit("selection");
        if (throwOnError) throw new Error(msg);
        return { ok: false, stale: true, error: msg };
      }
      if (err && err.status === 400 && err.data && Array.isArray(err.data.rejected) && err.data.rejected.length) {
        const rej = err.data.rejected;
        const fmt = rej.slice(0, 3).map((item) => {
          const p = getPatch(item.crop_id);
          const pName = p ? p.name : item.crop_id;
          return `${pName}(${item.key}): ${item.error}`;
        }).join(" · ");
        const suffix = rej.length > 3 ? ` 외 ${rej.length - 3}건` : "";
        const msg = `${fmt}${suffix}`;
        if (!throwOnError) toast(`모델 설정 거절(저장되지 않음): ${msg}`, "err");
        emit("selection");
        if (throwOnError) throw new Error(msg);
        return { ok: false, rejected: rej, reason: err.data.reason || null, error: msg };
      }
      if (err && err.status === 422) {
        state.cropGenError = err.message || "패치별 생성 설정 파일이 손상되었습니다";
      } else if (err && err.status === 404) {
        loadCropGen();
        refreshAll(true);
      }
      const msg = (err && err.message) || "패치별 모델 설정을 저장하지 못했습니다.";
      if (!throwOnError) toast(`모델 설정 저장 실패: ${msg}`, "err");
      emit("selection");
      if (throwOnError) throw new Error(msg);
      return { ok: false, error: msg };
    } finally {
      state.cropGenBusy = false;
      emit("selection");
    }
  }

  // R2-7: 개별 모델·개별 장수·「모델만 개별이고 장수는 상속」 영역 수를 구분 집계한다
  function countCropGenOverrides() {
    const out = { customModel: 0, customN: 0, customModelInheritN: 0 };
    if (!Api.can("v2_crop_model")) return out;
    for (const row of state.cropGenById.values()) {
      const inh = row && row.effective && row.effective.inherit;
      if (!inh) continue;
      if (inh.model === false) {
        out.customModel += 1;
        if (inh.n === true) out.customModelInheritN += 1;
      }
      if (inh.n === false) out.customN += 1;
    }
    return out;
  }

  function countCustomModelOverrides() {
    return countCropGenOverrides().customModel;
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
    if (!state.root) return false;
    if (state.polling) {
      if (force) {
        queuedForceRefresh = true;
        if (activeRefreshPromise) {
          await activeRefreshPromise;
          if (queuedForceRefresh) {
            queuedForceRefresh = false;
            return refreshAll(true);
          }
          if (activeRefreshPromise) {
            return !!(await activeRefreshPromise);
          }
        }
      }
      return state.syncState !== "err";
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
    let refreshedOk = false;
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
        const prevTitle = prev.title;
        const prevCropGenRev = prev.crop_gen_rev;
        prev.running = d.running;
        prev.drain = d.drain;
        prev.drafting = d.drafting;
        if ("title" in d) prev.title = d.title;
        if ("crop_gen_rev" in d) prev.crop_gen_rev = d.crop_gen_rev;
        if ("export_state" in d) {
          prev.export_state = d.export_state;
          prev.export_last = d.export_last;
        }
        statusChanged = (!!prev.running !== prevRunning)
          || (!!prev.drain !== prevDrain)
          || (!!prev.drafting !== prevDrafting)
          || (prev.export_state !== prevExport)
          || (prev.title !== prevTitle);
        if (d.layout_rev !== prev.layout_rev || d.refs_rev !== prev.refs_rev
            || d.refs_files_v !== prev.refs_files_v || d.pipeline_rev !== prev.pipeline_rev
            || d.plan_v !== prev.plan_v) {
          fullReload = true;
        } else if (Api.can("v2_crop_model") && ("crop_gen_rev" in d)) {
          // #C19-2 · R1-6: unchanged 응답의 crop_gen_rev 가 직전 seen 과 다르거나,
          // 손상(null)에서 정수로 복구(같은 판 번호 포함)되었을 때만 GET /crop_gen 을 다시 읽는다(주기적 호출 금지)
          const recoveredFromNull = (prevCropGenRev === null && d.crop_gen_rev !== null);
          const revDiff = d.crop_gen_rev !== null
            ? (d.crop_gen_rev !== state.cropGenSeen || recoveredFromNull || !!state.cropGenError)
            : prevCropGenRev !== null;
          if (revDiff) {
            await loadCropGen();
            if (prevCropGenRev !== d.crop_gen_rev || recoveredFromNull) {
              invalidateAllSpecs();
              if (state.primaryId && Api.can("v2_spec_edit")) {
                await loadSpec(state.primaryId, true);
              }
            }
            await loadPipelineAndPlan();
            statusChanged = true;
          }
        }
      }
      if (fullReload) {
        const prevRefsRev = prev ? prev.refs_rev : undefined;
        const prevPipeRev = prev ? prev.pipeline_rev : undefined;
        const prevCropGenRev = prev ? prev.crop_gen_rev : undefined;
        state.S = d.unchanged ? await Api.getState(state.root) : d;
        // #C08-2 · #C30-3 · #C19-2: 모델·레퍼런스·패치별 설정 판이 바뀌면 스펙 캐시를 무효화한다
        if (prevRefsRev !== undefined && state.S.refs_rev !== prevRefsRev) {
          refsDirtyAfterWrite = true;
        }
        if ((prevRefsRev !== undefined && state.S.refs_rev !== prevRefsRev)
            || (prevPipeRev !== undefined && state.S.pipeline_rev !== prevPipeRev)
            || (prevCropGenRev !== undefined && state.S.crop_gen_rev !== prevCropGenRev)) {
          invalidateAllSpecs();
        }
        await Promise.all([
          loadLayout(),
          loadRefs(true),
          loadPipelineAndPlan(),
          Api.can("v2_crop_model") ? loadCropGen() : Promise.resolve(),
        ]);
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
        syncMaskWithActiveCandidate({ fromPoll: !force });
        if (state.primaryId && Api.can("v2_spec_edit")) {
          const primInManifest = !!(state.S && Array.isArray(state.S.crops) && state.S.crops.some((c) => c.id === state.primaryId));
          await loadSpec(state.primaryId, primInManifest);
        }
      } else if (state.S && state.S.running) {
        try { state.progress = await Api.getProgress(state.root); statusChanged = true; } catch (_) {}
      }
      state.syncState = "idle";
      state.syncMsg = state.S ? "동기화됨" : "연결됨";
      refreshedOk = true;
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
      if (resolveRun) resolveRun(refreshedOk);
    }
    return refreshedOk;
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
      // #C11-5② · #C13-8①: 자기 쓰기 응답 rev 와 다시 읽은 rev 가 다르면(그 사이 다른 창이 고침) 되돌리기를 등록하지 않는다
      const writeRev = res && res.rev !== undefined ? res.rev : null;
      if (writeRev !== null) lastOwnLayoutRev = writeRev;
      await loadLayout();
      const layoutChangedExternally = writeRev !== null && state.L && state.L.rev !== undefined && state.L.rev !== writeRev;
      if (res.crop && res.crop.id) {
        const ref = { id: res.crop.id };
        selectPatch(ref.id);
        if (!skipHistory && !layoutChangedExternally) {
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
      const writeRev = res && res.rev !== undefined ? res.rev : null;
      if (writeRev !== null) lastOwnLayoutRev = writeRev;
      await loadLayout();
      const layoutChangedExternally = writeRev !== null && state.L && state.L.rev !== undefined && state.L.rev !== writeRev;
      if (!skipHistory && historyLabel && !layoutChangedExternally) {
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
      const writeRev = res && res.rev !== undefined ? res.rev : null;
      if (writeRev !== null) lastOwnLayoutRev = writeRev;
      await loadLayout();
      const layoutChangedExternally = writeRev !== null && state.L && state.L.rev !== undefined && state.L.rev !== writeRev;
      state.selectedIds.delete(id);
      if (state.primaryId === id) {
        state.primaryId = state.selectedIds.size ? [...state.selectedIds][0] : null;
      }
      syncMaskWithActiveCandidate();
      if (!skipHistory && !layoutChangedExternally) {
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
    if (!state.root || !candKey) {
      if (!state.mask || !state.mask.dirty) closeMaskSession();
      return null;
    }
    // #C13-7 · #C15-5: 다른 후보로 마스크를 열 때 앞 미저장 획이 있으면 저장 성공 또는 명시적 「버리기」 전까지 덮어쓰지 않는다
    if (state.mask && state.mask.dirty && state.mask.key !== candKey) {
      if (maskSwitchAsking) {
        toast("이전 마스크 저장 확인이 끝난 뒤 다시 시도해 주세요.");
        return null;
      }
      const pendingMask = state.mask;
      maskSwitchAsking = true;
      try {
        const choice = await askUnsavedMaskChoice();
        if (choice === "save") {
          const saved = await saveMaskSession(pendingMask);
          if (!saved || !saved.ok || saved.conflict || pendingMask.dirty) {
            return null;
          }
          if (state.mask === pendingMask) closeMaskSession();
        } else if (choice === "discard") {
          if (state.mask === pendingMask) closeMaskSession();
        } else {
          toast("마스크 편집을 유지합니다 — 저장하거나 [버리기]를 선택해 주세요.");
          return null;
        }
      } finally {
        maskSwitchAsking = false;
      }
    }
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
      // #C11-3 · #C13-6② · #C15-6: 서버 adjust 값이 있으면 해당 후보 key 의 미수정 슬라이더만 서버 보정값으로 동기화한다
      if (info && info.adjust && info.crop_id) {
        syncCandidateFiltersFromAdjust(info.crop_id, candKey, info.adjust, false);
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
    // #C07-5 · #C13-7: 현재 열린 세션 저장 시 활성 후보와 다르면 다른 후보에 잘못 저장하지 않되 미저장 획은 닫지 않고 보존한다
    if (!targetMask) {
      const activeCand = currentCandidate();
      if (activeCand && activeCand.key !== ms.key) {
        toast("현재 선택한 후보와 마스크 편집 대상이 다릅니다 — 원래 후보를 다시 선택해 저장해 주세요.", "err");
        if (!ms.dirty) closeMaskSession();
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

  // #C21-6: 채택(POST /pick) 요청이 도는 동안 같은 후보의 중복 클릭을 잠근다
  function pickBusyKey(cropId, tid, key) {
    return `${cropId || ""}|${tid || ""}|${key || ""}`;
  }

  function isPickBusy(cropId, tid, key) {
    return state.pickBusyKeys.has(pickBusyKey(cropId, tid, key));
  }

  async function pickCandidateGuarded({ crop_id, tid, key, mode, selectTargetKey = null, successToast = "" }) {
    if (!state.root || !crop_id || !tid || !key) return { ok: false, reason: "invalid" };
    const bk = pickBusyKey(crop_id, tid, key);
    if (state.pickBusyKeys.has(bk)) return { ok: false, busy: true };
    state.pickBusyKeys.add(bk);
    emit("selection");
    try {
      const res = await Api.pickCandidate(state.root, { crop_id, tid, key, mode });
      if (selectTargetKey) selectCandidate(selectTargetKey, key);
      if (successToast) toast(successToast, "ok");
      await refreshAll(true);
      return { ok: true, res };
    } catch (err) {
      toast(`채택 실패: ${(err && err.message) || "요청 실패"}`, "err");
      return { ok: false, error: err && err.message };
    } finally {
      state.pickBusyKeys.delete(bk);
      emit("selection");
    }
  }

  return {
    state, subscribe, emit, toast,
    loadPrefs, savePrefs, applyPrefsToDom, activeTheme, activeAccentHex, initForgeThemeListener,
    getHomePrefs, saveHomePrefs, saveHistoryPanelPrefs, togglePinnedProject,
    ensurePatchMeta, getCandidateFilters, syncCandidateFiltersFromAdjust, markFieldDirty, loadSpec, savePatchSpec, applyQuickSpecChange, awaitPendingQuickSpecs, applySpecResponse, invalidateAllSpecs, projectPrepareRaw, projectPrepare, buildPreparePayload,
    isPatchCheckedForRun, setPatchCheckedForRun, setAllCheckedForRun,
    getPatches, getPatch, primaryPatch, selectedPatches, clearSelection,
    currentCandidate, selectCandidate, selectTargetForCrop, moveCropZ, isPickBusy, pickCandidateGuarded,
    selectPatch, selectAllPatches, getCommonOrMixed,
    pushHistory, undoHistory, redoHistory, jumpHistory,
    loadLayout, loadRefs, trackRefWrite, refreshAfterRefWrite, ensureFreshRefsAfterWrite, loadPipelineAndPlan, loadSeams, refreshAll,
    applyCropGenResponse, loadCropGen, buildCropGenModelPatch, genEntryToPatch, buildGroupedChanges, updateCropGen, countCropGenOverrides, countCustomModelOverrides,
    startPolling, stopPolling,
    addCrop, updateCrop, deleteCrop,
    openMaskSession, closeMaskSession, saveMaskSession,
  };
})();
