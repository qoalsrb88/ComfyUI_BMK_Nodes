// BMK Design Patch Studio — 실행 환경(DPEnv). 로컬판(ComfyUI 커스텀 노드)과 서버 모드(Forge iframe — 독립 서버)를 한 코드로 가른다.
// 계약: _proto/STUDIO_FORGE_F1_SPEC.md §3.4 · 응답 모양 tools/fixtures/server_api.json.
// 이 파일은 다른 스크립트보다 먼저 실행된다(board.html 의 첫 defer). 모든 문자열은 textContent 로만 넣는다.
"use strict";

window.DPEnv = (() => {
  // 로컬판의 기능 표(bmk_design_patch_board.LOCAL_CAPS 와 같음 — GET ../env 가 404 인 옛 로컬 서버도 이 표). 서버 전용 둘은 false
  const LOCAL_CAPS = { jobs: false, export_download: false, sam3: true, local_qwen: true, external: true, open_folder: true,
    path_input: true, ps_mask: true, agent_approve: true };
  // 해시를 다른 모듈·요청이 보기 전에 지운다. 토큰은 세션 교환 뒤 버린다.
  const hash = new URLSearchParams(location.hash.slice(1));
  let token = hash.get("token"), organization = hash.get("organization_id") || "";
  if (hash.has("token")) history.replaceState(null, "", location.pathname + location.search);
  hash.delete("token");
  const API = new URL("../", location.href);
  const memory = new Map();
  let local;
  try { local = window.localStorage; } catch (e) { /* 막힌 브라우저 저장소는 메모리로 */ }
  if (!local) local = {
    getItem: (key) => memory.has(String(key)) ? memory.get(String(key)) : null,
    setItem: (key, value) => memory.set(String(key), String(value)),
    removeItem: (key) => memory.delete(String(key)),
  };

  const env = {
    serverMode: null, // 판정 실패를 로컬로 취급하지 않는다. ready 전에는 보드를 시작하지 않음
    caps: {},
    account: null, // 서버 모드 {name, tag}
    backend: null, // 서버 모드 생성 백엔드 이름("mock" …)
    store: local, // 화면 설정 저장소(로컬 = localStorage, 서버 모드 = 계정별 서버 저장)
    ready: null,
    can(name) {
      // 그 기능을 쓸 수 있나 — caps[name] 이 true 일 때만
      return env.caps[name] === true;
    },
  };

  const AGAIN = "Forge에서 Design Patch Studio를 다시 열어 주세요.";
  let blocker = null, changed = false;
  const blockedInert = new Map();
  function clearBlock() {
    if (blocker) blocker.remove();
    blocker = null;
    for (const [child, inert] of blockedInert) child.inert = inert;
    blockedInert.clear();
  }
  function block(message, error, retry = false) {
    if (changed && message !== "다른 계정으로 바뀌었습니다 — " + AGAIN) return;
    if (message.startsWith("다른 계정")) changed = true;
    if (!document.body) {
      document.addEventListener("DOMContentLoaded", () => block(message, error, retry), { once: true });
      return;
    }
    if (window.DPDialog) window.DPDialog.cancel();
    if (blocker) blocker.remove();
    blocker = document.createElement("div");
    blocker.className = "overlay dp-env-block";
    blocker.setAttribute("role", "alertdialog");
    blocker.setAttribute("aria-modal", "true");
    const sheet = document.createElement("div"), p = document.createElement("p");
    sheet.className = "sheet"; p.textContent = message; sheet.appendChild(p);
    if (error && String(error) !== message) {
      const small = document.createElement("p");
      small.className = "muted"; small.textContent = String(error); sheet.appendChild(small);
    }
    if (retry) {
      const button = document.createElement("button");
      button.textContent = "다시 시도"; button.onclick = () => { clearBlock(); initialize(); }; sheet.appendChild(button);
    }
    blocker.appendChild(sheet);
    for (const child of document.body.children) if (child !== blocker) {
      if (!blockedInert.has(child)) blockedInert.set(child, child.inert);
      child.inert = true;
    }
    document.body.appendChild(blocker);
  }

  // 다른 모듈이 fetch 를 잡기 전에 설치. 로컬 요청은 인자·반환 약속도 그대로 둔다.
  const originalFetch = window.fetch;
  window.fetch = function (...args) {
    if (!env.serverMode) return originalFetch.apply(this, args);
    const [input, init] = args, options = init || {};
    const url = new URL(typeof input === "string" || input instanceof URL ? input : input.url, location.href);
    if (url.origin !== location.origin) return originalFetch.apply(this, args);
    const headers = new Headers(input && input.headers);
    new Headers(options.headers).forEach((value, key) => headers.set(key, value));
    if (env.account) headers.set("X-DesignPatch-Account", env.account.tag);
    const method = String(options.method || (input && input.method) || "GET").toUpperCase();
    if (method !== "GET" && method !== "HEAD") headers.set("X-DesignPatch-Request", "1");
    return originalFetch.call(this, input, Object.assign({}, options, { headers })).then(async (response) => {
      if (response.status === 401 || response.status === 409) {
        try {
          const data = await response.clone().json();
          if (response.status === 401 && data.reason === "no_session") block(AGAIN, data.error);
          if (response.status === 409 && data.reason === "session_changed") block("다른 계정으로 바뀌었습니다 — " + AGAIN, data.error);
        } catch (e) { /* 본문을 읽지 못해도 원 응답은 호출부로 */ }
      }
      return response;
    });
  };

  async function read(url, init) {
    const controller = new AbortController();
    let timer;
    try {
      return await Promise.race([
        (async () => {
          const response = await window.fetch(url, Object.assign({ cache: "no-store" }, init, { signal: controller.signal }));
          const data = response.status === 404 ? null : await response.json();
          return { response, data };
        })(),
        new Promise((resolve, reject) => {
          timer = setTimeout(() => { controller.abort(); reject(new Error("timeout")); }, 8000);
        }),
      ]);
    } finally { clearTimeout(timer); }
  }
  const object = (value) => value && typeof value === "object" && !Array.isArray(value);
  function endpoint(value) {
    if (typeof value !== "string" || !value) throw new Error("url");
    const url = new URL(value, location.href);
    if (url.origin !== location.origin) throw new Error("origin");
    return url.href;
  }

  function serverStore(url, values) {
    const mem = new Map(Object.entries(values)), dirty = new Map();
    let timer = null, saving = false, again = false;
    const later = () => { clearTimeout(timer); timer = setTimeout(() => flush(false), 300); };
    async function flush(keepalive) {
      clearTimeout(timer); timer = null;
      if (!dirty.size) return;
      if (saving && !keepalive) { again = true; return; }
      const batch = new Map(dirty), set = Object.create(null), remove = [];
      for (const [key, change] of batch) {
        if (change.value === null) remove.push(key); else set[key] = change.value;
      }
      if (!keepalive) saving = true;
      try {
        const response = await window.fetch(url, { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ set, remove }), keepalive });
        if (response.ok) for (const [key, change] of batch) if (dirty.get(key) === change) dirty.delete(key);
      } catch (e) { /* 다음 변경 때 남은 키도 함께 보낸다 */ }
      finally {
        if (!keepalive) { saving = false; if (again) { again = false; later(); } }
      }
    }
    window.addEventListener("pagehide", () => flush(true));
    return {
      getItem(key) { key = String(key); return mem.has(key) ? mem.get(key) : null; },
      setItem(key, value) {
        key = String(key); value = String(value); mem.set(key, value); dirty.set(key, { value }); later();
      },
      removeItem(key) { key = String(key); mem.delete(key); dirty.set(key, { value: null }); later(); },
    };
  }

  let resolveReady, initializing = false;
  env.ready = new Promise((resolve) => { resolveReady = resolve; });
  window.addEventListener("pagehide", () => { token = null; organization = ""; });
  async function initialize() {
    if (initializing || changed) return;
    initializing = true;
    let data;
    try {
      const result = await read(new URL("env", API));
      if (result.response.status === 404) {
        env.serverMode = false; env.caps = Object.assign({}, LOCAL_CAPS); token = null; organization = ""; resolveReady(); return;
      }
      data = result.data;
      if (result.response.status !== 200 || !object(data) || typeof data.server_mode !== "boolean"
          || !object(data.caps) || !Object.keys(LOCAL_CAPS).every((key) => typeof data.caps[key] === "boolean")) throw new Error("env");
      const sessionUrl = data.server_mode ? endpoint(data.session_url) : null;
      const prefsUrl = data.server_mode ? endpoint(data.prefs_url) : null;
      if (data.server_mode && typeof data.backend !== "string") throw new Error("backend");
      env.serverMode = data.server_mode;
      env.caps = data.caps;
      if (!env.serverMode) { token = null; organization = ""; resolveReady(); return; }
      let session;
      try {
        session = await read(sessionUrl, token ? { method: "POST",
          headers: { "Content-Type": "application/json", "X-DesignPatch-Request": "1" },
          body: JSON.stringify({ token, organization_id: organization }) } : undefined);
      } catch (e) {
        block(AGAIN); return;
      } finally { token = null; organization = ""; }
      const account = session.data && session.data.account;
      if (session.response.status !== 200 || !object(session.data) || session.data.ok !== true || !object(account)
          || typeof account.name !== "string" || typeof account.tag !== "string" || !account.tag) {
        block(AGAIN, session.data && session.data.error); return;
      }
      env.account = account;
      const prefs = await read(prefsUrl);
      if (!prefs.response.ok || !object(prefs.data) || !object(prefs.data.values)
          || !Object.values(prefs.data.values).every((value) => typeof value === "string")) throw new Error("prefs");
      const store = serverStore(prefsUrl, prefs.data.values);
      try {
        Object.defineProperty(window, "localStorage", { configurable: true, value: store });
        if (window.localStorage !== store) throw new Error("storage");
        env.store = store;
      } catch (e) {
        console.warn("화면 설정을 브라우저에만 저장합니다 — 서버 저장소를 연결하지 못했습니다.");
        env.store = local;
      }
      env.backend = data.backend;
      resolveReady();
    } catch (e) {
      block("서버에 연결하지 못했습니다", null, true);
    } finally { initializing = false; }
  }
  initialize();
  return env;
})();
