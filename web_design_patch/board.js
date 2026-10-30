// BMK Design Patch Studio(Review Board) — vanilla JS, 빌드·외부 리소스 없음.
// 서버: bmk_design_patch_board.py (/bmk/design_patch/*). 모든 문자열(PSD 레이어명 등)은 textContent 로만 넣는다.
// 보드는 매니페스트(★·slice·탈락·재굴림 의도)만 고친다. 유료 호출은 ComfyUI 탭이 큐에 넣을 때만 — 유료 승인을 받는 탭(S3b)이면 승인 템플릿
// (pipeline.js — 재굴림은 reroll stage), 옛 확장의 opener 탭이면 Review 노드 부분 실행.
// 캔버스 뷰어는 viewer.js(DPViewer) — 이 파일이 선택·키 입력을 넘긴다. 레이아웃 편집 모드(E)는 layout_edit.js(DPLayoutEdit),
// 레퍼런스 모드(T)는 ref_edit.js(DPRefEdit — 시트 뷰어 = DPViewer 두 번째 인스턴스), 진행 모드(G)는 pipeline.js(DPPipeline — 실행 설정·
// 무료 반영·계획/비용·채택·★ 점검·Export, 머리줄 한 줄·ComfyUI 탭 hello 표시), 마스크 모드(K)는 mask_edit.js(DPMaskEdit — ★ 후보의 최종 마스크
// 손질·이음매 페이드·다른 후보 표, 세션은 모드를 나가도 이 창에 남음). 네 모드와 검토는 서로 배타.
// 편집기의 "반영(큐)"는 무료 반영(apply 템플릿 — 탭 불필요)이고, 옛 서버(재시작 전)면 ComfyUI 탭의 Review 부분 실행으로 대신한다.
"use strict";

(() => {
  const API = new URL("../", location.href); // /bmk/design_patch/ (또는 /api/bmk/design_patch/)
  const COMFY = new URL(API.pathname.replace(/(?:api\/)?bmk\/design_patch\/$/, "api/"), API); // ComfyUI 공개 API /api/(SAM3 컷 큐)
  const ROOT = new URLSearchParams(location.search).get("root") || "";
  const POLL_MS = 2000;
  const POLL_HIDDEN_MS = 10000;
  const REROLL_CONFIRM_MS = 2500;
  const GATE_NAMES = { outside_mad: "마스크 밖 MAD", outside_frac_gt24: "마스크 밖 큰 차이", black_band: "검은 띠" };
  const PREF_KEY = "bmk-dp-studio";
  const SEEN_KEY = "bmk-dp-seen:"; // + 보드 키 — 이 브라우저에서 고른 적 있는 후보 key(그 밖 = 안 본 후보 '새')
  const VIA = { run: "Run", reroll: "다시 생성", retry: "재시도" }; // 후보를 만든 호출의 승인 종류(state via — 원장 approved_by)
  // 편집 모드에서 끄는 검토 키: ★(Space)·탈락(X)·재굴림(R, 유료)·타깃/후보 이동·비교 보기. Space 는 viewer.js 가 화면 이동에 쓴다
  const EDIT_OFF = new Set(["Space", "KeyX", "KeyR", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "KeyQ", "KeyD", "KeyC",
    "KeyS", "KeyM", "KeyV"]);
  // 레퍼런스 모드에서 끄는 키: 검토 키 + 편집(E) + 숨은 캔버스 뷰어 키(B·O·N). 0·1·2·F 는 ref_edit.js 가 시트 뷰어에 쓴다
  const REFS_OFF = new Set([...EDIT_OFF, "KeyE", "KeyB", "KeyO", "KeyN"]);
  // 진행 모드에서 끄는 키: 검토 키 + 편집(E)·레퍼런스(T). 뷰어 키(B·O·N·0·F·1·2)는 그대로(가운데 = 캔버스 뷰어)
  const PIPE_OFF = new Set([...EDIT_OFF, "KeyE", "KeyT"]);
  // 마스크 모드에서 끄는 키: 검토 키 + E·T·G(G 로 바로 가지 않게 — 적용 안 한 획 확인은 K·Esc·머리줄 단추가 함). X·Q·S·M 은 mask_edit.js 가 먼저 받는다
  const MASK_OFF = new Set([...EDIT_OFF, "KeyE", "KeyT", "KeyG"]);
  const LAYOUT_STATUS = { changed: "레이아웃 바뀜(미반영)", removed: "레이아웃에서 지움(미반영)" }; // state 크롭의 layout_status
  // ComfyUI 탭 hello(DPPipeline.link) 상태 → 배지. 키 = tabKey: 유료 승인을 받는 탭(확장 판 3 이상·submit ok)은 길마다 ok(opener)·channel —
  // 승인·재굴림·ComfyUI 큐가 그 탭으로(계약 §5). 판 2(S3a 확장)는 s3a, 판 3 인데 제출을 못 함은 nosubmit — 유료 승인 끔(R·ComfyUI 큐는 opener 만).
  // old 중 응답이 아예 없으면 silent(옛 확장인지 다른 주소로 옮겼는지 모름)
  const TAB_BADGE = {
    ok: ["b-run", "ComfyUI 탭 연결"],
    channel: ["b-run", "ComfyUI 탭 연결(채널)"],
    s3a: ["b-wait", "탭 확장이 옛 판(S3a) — 유료 승인은 ComfyUI 탭 새로고침 뒤"],
    nosubmit: ["b-wait", "ComfyUI 탭이 승인 제출을 못 함 — 유료 승인 끔"],
    old: ["b-wait", "탭 확장이 옛 판 — ComfyUI 탭 새로고침"],
    silent: ["b-wait", "ComfyUI 탭 응답 없음 — 탭 새로고침 또는 Open Board 로 다시 열기"],
    none: ["b-dim", "ComfyUI 탭 없음"],
    many: ["b-wait", "ComfyUI 탭 여러 개 — Open Board 로 다시 여세요"],
    wait: ["b-dim", "ComfyUI 탭 확인 중"],
  };

  let S = null; // 마지막 전체 상태
  let tKey = null; // 선택한 타깃 "crop_id/tid"
  let cKey = null; // 선택한 후보 key
  let view = "cand"; // cand | delta | context
  let qHeld = false;
  let refIdx = 0;
  let busy = false;
  let polling = false;
  let pollAgain = false;
  let cycle = null; // 도는 refresh 의 약속(뒤따르는 다시 받기 포함)
  let pollTimer = null;
  let rerollArm = null; // {cell, way, until} — way = 첫 R 토스트를 만든 판정(tabWay)
  let rerollSent = null; // {cell, rev} — 마지막 재굴림 쓰기(응답 매니페스트 rev) — rerollHold
  let toastTimer = null;
  let viewer = null;
  let slider = false;
  let raw = false;
  let seamsByCrop = new Map(); // crop_id → {n, bad, growSides?}(beside 없는 옛 캐시는 후보 없음)
  let editor = null; // DPLayoutEdit — 옛 서버(ComfyUI 재시작 전)는 layout_*.js 를 주지 않아 null
  let editing = false;
  let refEd = null; // DPRefEdit — 옛 서버는 ref_edit.js 를 주지 않아 null
  let refsOn = false;
  let refTab = "sheets"; // 레퍼런스 가운데 탭(3-4 — ref_edit.js 가 바꿀 때 알림, 단축키 칩 줄이 따름)
  let pipe = null; // DPPipeline 패널 — 옛 서버는 pipeline.js 를 주지 않아 null
  let pipeOn = false;
  let tabLink = null; // DPPipeline.link — ComfyUI 탭 hello 상태
  let maskEd = null; // DPMaskEdit — 옛 서버는 mask_edit.js 를 주지 않아 null
  let maskOn = false;
  let seen = null; // Set — 본 후보 key(syncSeen 이 첫 state 에서 채움)

  const $ = (id) => document.getElementById(id);

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = String(text);
    return e;
  }

  function apiUrl(rel) {
    const u = new URL(rel, API);
    return u.origin === location.origin ? u.href : null;
  }

  function comfyUrl(rel) {
    const u = new URL(rel, COMFY);
    return u.origin === location.origin ? u.href : null;
  }

  function setImg(img, rel) {
    // 새 그림을 다 받기 전에는 칸(.frame)에 로딩 표시만 — 덜 받은 그림·앞 그림이 겹쳐 보이지 않게(F29)
    const u = rel ? apiUrl(rel) : null;
    const fr = img.closest(".frame");
    if (u) {
      if (img.getAttribute("src") !== u) {
        if (fr) fr.classList.add("loading");
        img.onload = img.onerror = () => {
          if (fr && img.getAttribute("src") === u) fr.classList.remove("loading");
        };
        img.src = u;
        if (img.complete && fr) fr.classList.remove("loading");
      }
      img.hidden = false;
    } else {
      if (fr) fr.classList.remove("loading");
      img.removeAttribute("src");
      img.hidden = true;
    }
  }

  function toast(msg, kind = "") {
    const t = $("toast");
    t.textContent = msg;
    t.className = "show " + kind;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { t.className = kind; }, kind === "err" ? 6000 : 3200);
  }

  function chip(text, cls = "") {
    return el("span", "chip " + cls, text);
  }

  function usd(cell) {
    if (!cell || !cell.est_usd) return "";
    const [lo, hi] = cell.est_usd;
    return lo === hi ? ` ≈ $${lo.toFixed(2)}` : ` ≈ $${lo.toFixed(2)}–${hi.toFixed(2)}`;
  }

  function credits(v, unknown = 0) {
    // unknown = 크레딧 조회가 안 된 완료 호출 수(0 이 아님) — 그때 합은 하한
    const s = `${Number(v || 0).toLocaleString("ko-KR", { maximumFractionDigits: 1 })} cr`;
    return unknown ? `≥ ${s} (미상 ${unknown})` : s;
  }

  function loadPref() {
    try {
      return JSON.parse(localStorage.getItem(PREF_KEY) || "{}") || {};
    } catch (e) {
      return {};
    }
  }

  function savePref(p) {
    try {
      localStorage.setItem(PREF_KEY, JSON.stringify(Object.assign(loadPref(), p)));
    } catch (e) {
      // 저장소를 못 쓰면 이번 창에서만
    }
  }

  // ── 서버 ────────────────────────────────────────────
  async function readJson(r) {
    try {
      return await r.json();
    } catch (e) {
      return { error: `응답을 읽을 수 없습니다 (HTTP ${r.status})` };
    }
  }

  async function getState(force) {
    const p = new URLSearchParams({ root: ROOT });
    if (!force && S) p.set("since_rev", String(S.rev));
    const r = await fetch(apiUrl("state?" + p.toString()), { cache: "no-store" });
    const data = await readJson(r);
    if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
    return data;
  }

  async function post(path, body) {
    const r = await fetch(apiUrl(path), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(Object.assign({ root: ROOT }, body)),
    });
    const data = await readJson(r);
    if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
    return data;
  }

  function refresh(force = false) {
    // 도는 폴링이 있으면 다시 받기(force)만 걸고 그 폴링 — 뒤따르는 다시 받기까지 — 이 끝날 때 끝나는 약속을 돌려준다. 쓰기 뒤 refresh(true)를
    // 기다리는 곳(write — busy 가 풀리기 전에 그 쓰기를 반영한 S: 키보드 R 거절의 셀 pre, 진행 [반영] 확인 창의 크롭별 줄)이 낡은 S 로 넘어가지 않게
    if (polling) {
      pollAgain = pollAgain || force;
      return cycle;
    }
    cycle = pollState(force);
    return cycle;
  }

  async function pollState(force) {
    polling = true;
    try {
      const d = await getState(force);
      if (d.unchanged && S) {
        S.running = d.running;
        S.drain = d.drain;
        S.drafting = d.drafting; // S3: Spec Draft 실행 중(옛 서버 = undefined)
        if ("export_state" in d) {
          S.export_state = d.export_state;
          S.export_last = d.export_last;
        }
        // 레이아웃·레퍼런스 쓰기는 매니페스트 rev 를 올리지 않는다 — layout_rev·refs_rev 가 바뀌면 크롭 요약을 새로 받는다.
        // refs_files_v = 바깥(PS·탐색기)에서 refs_dir 파일이 바뀜 — 새 S 가 있어야 크롭 요약 칩·레퍼런스 편집기(since 없이 GET /refs)가 따라온다
        if ("layout_rev" in d && d.layout_rev !== S.layout_rev) pollAgain = true;
        if ("refs_rev" in d && d.refs_rev !== S.refs_rev) pollAgain = true;
        if ("refs_files_v" in d && d.refs_files_v !== S.refs_files_v) pollAgain = true;
        // S3: 실행 설정(pipeline.json)·기억한 계획의 서명이 바뀜 — 크롭 요약(초안 대기 칩)·진행 패널이 새 S 로 따라온다
        if ("pipeline_rev" in d && d.pipeline_rev !== S.pipeline_rev) pollAgain = true;
        // 서버가 옛 판으로 재시작됨(unchanged 에 pipeline_rev 가 없어짐 — 매니페스트 rev 는 그대로): 전체 state 를 받아 S 에서도 빼야
        // applyFree·진행 패널이 옛 서버로 판정(탭 반영 대체)
        if (!("pipeline_rev" in d) && "pipeline_rev" in S) pollAgain = true;
        if ("plan_v" in d && d.plan_v !== S.plan_v) pollAgain = true;
        // S4: 분석·Export 설정(board_pref.json)이 바뀜 — 매니페스트 rev 는 그대로라 편집 창·★ 합성·stale 을 새 S 로
        if ("pref_v" in d && d.pref_v !== S.pref_v) pollAgain = true;
        renderHeader();
      } else if (!d.unchanged) {
        S = d;
        reconcile();
        renderAll(false);
      }
      if (editing && S) editor.stateChanged(S);
      if (refsOn && S) refEd.stateChanged(S);
      if (pipe && S) pipe.stateChanged(S); // 머리줄 한 줄·첫 계획(칩)·이어 받기는 모든 모드
      setConn("");
    } catch (e) {
      setConn(e.message);
    } finally {
      polling = false;
    }
    if (pollAgain) {
      pollAgain = false;
      await refresh(true);
    }
  }

  function schedule() {
    clearTimeout(pollTimer);
    pollTimer = setTimeout(async () => {
      await refresh(false);
      schedule();
    }, document.hidden ? POLL_HIDDEN_MS : POLL_MS);
  }

  async function write(path, body, okMsg) {
    if (busy) return null;
    busy = true;
    document.body.classList.add("busy");
    try {
      const r = await post(path, body);
      if (okMsg) toast(typeof okMsg === "function" ? okMsg(r) : okMsg);
      await refresh(true);
      return r;
    } catch (e) {
      toast(e.message, "err");
      return null;
    } finally {
      busy = false;
      document.body.classList.remove("busy");
    }
  }

  // ── 선택 ────────────────────────────────────────────
  function curTarget() {
    return S ? S.targets.find((t) => t.key === tKey) || null : null;
  }

  function curCand() {
    const t = curTarget();
    return t ? t.candidates.find((c) => c.key === cKey) || null : null;
  }

  function cropOf(t) {
    return S && t ? S.crops.find((c) => c.id === t.crop_id) || null : null;
  }

  function defaultCand(t) {
    if (!t || !t.candidates.length) return null;
    const first = t.pick_keys.length ? t.candidates.find((c) => c.key === t.pick_keys[0]) : null;
    return (first || t.candidates[0]).key;
  }

  function reconcile() {
    syncSeen();
    if (!S.targets.length) {
      tKey = cKey = null;
      return;
    }
    let t = curTarget();
    if (!t) {
      t = S.targets[0];
      tKey = t.key;
      cKey = null;
    }
    if (!t.candidates.some((c) => c.key === cKey)) cKey = defaultCand(t);
    markSeen(cKey);
    if (refIdx >= t.refs.length) refIdx = 0;
  }

  function syncSeen() {
    // 처음 받은 state: 이 브라우저에 저장한 본 후보가 없으면(처음 연 프로젝트·저장소 막힘) 지금 후보를 모두 본 것으로 — 그 뒤 생기는 후보가 '새'
    if (seen) return;
    let saved = null;
    try {
      saved = JSON.parse(localStorage.getItem(SEEN_KEY + ROOT) || "null");
    } catch (e) {
      saved = null;
    }
    seen = new Set(Array.isArray(saved) ? saved : S.targets.flatMap((t) => t.candidates.map((c) => c.key)));
    saveSeen(true); // 처음이면 기준을, 아니면 지금 없는 후보를 뺀 목록을 저장
  }

  function saveSeen(prune = false) {
    // 다른 창이 그 사이 본 후보와 합쳐 저장(못 쓰면 이번 창에서만). prune = 창을 연 첫 state 일 때만 지금 없는 후보를 뺌(지운 후보·같은 경로에
    // 다시 만든 프로젝트의 옛 키가 쌓이지 않게) — 그 뒤의 S 는 폴링이 늦을 수 있어, 다른 창이 본 더 새 후보를 지우지 않게 빼지 않는다
    try {
      const old = JSON.parse(localStorage.getItem(SEEN_KEY + ROOT) || "[]");
      for (const k of Array.isArray(old) ? old : []) seen.add(k);
      const now = prune ? new Set(S.targets.flatMap((t) => t.candidates.map((c) => c.key))) : null;
      localStorage.setItem(SEEN_KEY + ROOT, JSON.stringify(now ? [...seen].filter((k) => now.has(k)) : [...seen]));
    } catch (e) {
      // 저장소를 못 씀 — 이번 창에서만
    }
  }

  function onSeenStorage(e) {
    // 다른 창(같은 프로젝트 Studio)이 본 후보 — 이 창의 '새' 표시에서도 뺀다
    if (e.key !== SEEN_KEY + ROOT || !seen || !e.newValue) return;
    let keys = null;
    try {
      keys = JSON.parse(e.newValue);
    } catch (x) {
      return;
    }
    if (!Array.isArray(keys)) return;
    for (const k of keys) seen.add(k);
    renderTargets();
    renderGrid();
  }

  function markSeen(key) {
    // 검토 화면(후보 큰 화면·그리드)이 보일 때만 — 진행(G)·편집(E)·레퍼런스(T)·마스크(K)의 크롭 선택은 후보를 보여 주지 않는다(모드를 나올 때 그 후보를 표시).
    // → 새로 본 것으로 표시했는가
    if (!seen || !key || seen.has(key) || editing || refsOn || pipeOn || maskOn) return false;
    seen.add(key);
    saveSeen();
    return true;
  }

  function seeCurrent() {
    // 모드를 나와 검토 화면이 다시 보임 — 지금 후보를 본 것으로 하고 '새' 표시를 다시 그림
    if (markSeen(cKey)) {
      renderTargets();
      renderGrid();
    }
  }

  function isNew(c) {
    return !!seen && !seen.has(c.key);
  }

  function selectTarget(key) {
    if (key === tKey) return;
    if (pipeOn && pipe.select(S.targets.find((t) => t.key === key).crop_id) === false) return false;
    tKey = key;
    cKey = defaultCand(curTarget());
    markSeen(cKey);
    refIdx = 0;
    rerollArm = null;
    renderAll(true);
  }

  function selectCand(key) {
    cKey = key;
    markSeen(key);
    rerollArm = null;
    renderTargets();
    renderCompare();
    renderDetail();
    renderGrid();
    syncViewer(false);
  }

  function pickCrop(cropId, tid, candKey, seam) {
    // 캔버스에서 크롭을 누름(tid 없음): 같은 크롭이면 다음 타깃, 아니면 첫 타깃. 이음매 이동은 tid·그 ★ 후보 키·구간을 준다 —
    // 마스크 모드면 그 ★ 후보가 편집 대상(적용 안 한 획 확인)·그 구간이 선택 구간 → 편집기가 거절(확인 취소·적용 중)하면 false(뷰어 N 이 제자리)
    if (!S) return;
    if (maskOn) return candKey ? maskEd.go({ key: candKey, seam: seam || null }) : undefined;
    const ts = S.targets.filter((t) => t.crop_id === cropId);
    if (!ts.length) return;
    let t = tid ? ts.find((x) => x.tid === tid) : null;
    if (!t) {
      const i = ts.findIndex((x) => x.key === tKey);
      t = ts[i >= 0 ? (i + 1) % ts.length : 0];
    }
    if (selectTarget(t.key) === false) return false;
    if (candKey && candKey !== cKey && t.candidates.some((c) => c.key === candKey)) selectCand(candKey);
    const li = document.querySelector("#target-list li.sel");
    if (li) li.scrollIntoView({ block: "nearest" });
  }

  function moveTarget(d) {
    if (!S || !S.targets.length) return;
    const i = Math.max(0, S.targets.findIndex((t) => t.key === tKey));
    selectTarget(S.targets[Math.min(S.targets.length - 1, Math.max(0, i + d))].key);
    const li = document.querySelector("#target-list li.sel");
    if (li) li.scrollIntoView({ block: "nearest" });
  }

  function moveCand(d) {
    const t = curTarget();
    if (!t || !t.candidates.length) return;
    const i = Math.max(0, t.candidates.findIndex((c) => c.key === cKey));
    selectCand(t.candidates[Math.min(t.candidates.length - 1, Math.max(0, i + d))].key);
    preloadAround();
  }

  function preloadAround() {
    const t = curTarget();
    if (!t) return;
    const i = t.candidates.findIndex((c) => c.key === cKey);
    for (const j of [i - 1, i + 1]) {
      const c = t.candidates[j];
      const u = c ? apiUrl(c.big_url) : null;
      if (u) new Image().src = u;
    }
  }

  // ── 동작 ────────────────────────────────────────────
  function setStar() {
    const t = curTarget();
    const c = curCand();
    if (!t || !c) return;
    if (c.picked) {
      // 이미 ★(또는 slice)인 후보에서 한 번 더 = 해제(F30 — 토글). 다른 slice 는 그대로
      write("pick", { crop_id: t.crop_id, tid: t.tid, key: c.key, mode: "remove" }, `★ 해제 c${pad(c.n)} — ${t.key}`);
      return;
    }
    const j = c.joint;
    const warn = j && j.first && j.first !== t.tid
      ? ` — 공동 결과(${j.tids.join("·")})는 보통 ${j.first} 에만 ★ (상세의 "${j.first} 에 ★")` : "";
    write("pick", { crop_id: t.crop_id, tid: t.tid, key: c.key, mode: "set" },
      `★ c${pad(c.n)} — ${t.key}${warn}${c.stale_rect ? " — 옛 크롭 후보(크롭 rect 를 옮기기 전)" : ""}`);
  }

  function toggleSlice() {
    const t = curTarget();
    const c = curCand();
    if (!t || !c) return;
    const mode = c.picked ? "remove" : "add";
    write("pick", { crop_id: t.crop_id, tid: t.tid, key: c.key, mode },
      mode === "add" ? `함께 쓰기 추가 c${pad(c.n)}` : `pick 에서 뺌 c${pad(c.n)}`);
  }

  function toggleReject() {
    const c = curCand();
    if (!c) return;
    write("reject", { key: c.key, value: !c.rejected },
      (r) => (r.rejected ? `제외 c${pad(c.n)}` + (r.unpicked ? " (pick 에서도 뺌)" : "") : `제외 해제 c${pad(c.n)}`));
  }

  async function starOnFirst() {
    // 공동 결과 후보를 첫 타깃에 ★ 하고, 지금 타깃에 같은 이미지가 ★ 이면 뺀다
    const t = curTarget();
    const c = curCand();
    if (!t || !c || !c.joint || !c.joint.first_key) return;
    const j = c.joint;
    const r = await write("pick", { crop_id: t.crop_id, tid: j.first, key: j.first_key, mode: "set" },
      `${j.first} 에 ★ — ${t.crop_name || t.crop_id}`);
    if (r && c.picked) await write("pick", { crop_id: t.crop_id, tid: t.tid, key: c.key, mode: "remove" }, null);
  }

  async function clearDupPicks(crop) {
    const dups = crop.dup_picks.slice();
    let n = 0;
    for (const d of dups) {
      if (await write("pick", { crop_id: crop.id, tid: d.tid, key: d.key, mode: "remove" }, null)) n += 1;
    }
    toast(`중복 ★ ${n}/${dups.length} 정리 — ${crop.name || crop.id} (같은 이미지는 ${dups[0].same_as} 에만)`);
  }

  function rerollCell() {
    const t = curTarget();
    if (!t) return null;
    const c = curCand();
    const own = c && c.cell_key ? t.cells.find((x) => x.cell_key === c.cell_key) : null;
    return own || t.cells[0] || null;
  }

  async function reroll(fromButton, scripted) {
    const cell = rerollCell();
    if (!cell) {
      toast("이 타깃의 작업(jobs)이 없습니다 — Prepare 를 먼저 실행하세요", "err");
      return;
    }
    const what = `다시 생성 +1 호출 (${cell.variant || cell.cell_key.slice(0, 8)}${usd(cell)})`;
    // 큐 요청은 queueReroll 의 길(유료 승인을 받는 탭 — 채널 포함 · 옛 확장의 opener 탭)로 — 둘 다 없으면 예약만(다음 Run 때 선택과 무관하게 과금).
    // 0.6초 뒤 큐는 이 판정(way·lock)을 넘겨받는다 — 그 사이 길이 바뀌면(busy 풀림·잡힘 등) 확인한 문구의 길이 아니므로 보내지 않음(예약만).
    // 탭 길이어도 이 Studio 가 유료 요청·실행·무료 큐 중이면(lock — 길과 무관: 자기 승인을 처리하는 탭은 hello busy 라 opener 길이 됨) 지금은
    // 예약만이라고 쓰고, 그 뒤 lock 이 풀려도 보내지 않음(동의한 것은 예약만 — 부수 효과 줄도 보이지 않았음). 키보드 R 도 첫 R 토스트(문구)를 만든
    // 판정과 다르면 다시 무장. scripted = 스크립트 키(합성 KeyboardEvent — isTrusted 아님): 사전 승인(예약)만 — 확인 창 없이 지금 보내는 큐(유료 탭
    // 길의 승인 제출·opener 의 Review 부분 실행)는 사용자 키 입력에서만(D7 — 스크립트가 바로 과금하지 않게)
    const way = scripted ? "" : tabWay();
    const tab = !!way;
    const lock = tab && pipe ? pipe.paidLock() : "";
    const why = tab ? "" : (pipe && pipe.paidWhy()) || "이 창을 연 ComfyUI 탭 없음";
    // 셀 가격(est_usd)은 마지막 Prepare 의 jobs 값 — 큐는 Prepare 를 다시 돈 뒤 과금한다. 유료 가능한 탭의 재굴림 템플릿은 Studio 실행 설정(반영과
    // 같음 — 반영 확인 창처럼 Prepare·Import 갈라짐 줄을 앞에), 옛 탭의 Review 부분 실행·다음 그래프 Run 은 그래프 위젯 값
    const graphPrep = way === "paid" ? pipe.applyWarn().concat("금액은 지금 jobs(마지막 Prepare) 기준: 다시 생성 템플릿은 Studio 실행 설정(pipeline.json)으로 "
      + "Import·Prepare 를 다시 돈 뒤 과금하므로 Prepare 갈라짐이 있으면 장 수·비용이 바뀌거나(n·생성 조건마다 호출) 생성 조건이 바뀌어 이 다시 생성이 미뤄짐(모델·품질·변형 등)")
      .join("\n")
      : "금액은 지금 jobs(마지막 Prepare — Studio 반영 포함) 기준: 큐·Run 은 그래프 Prepare(그래프 위젯 값)를 다시 돌린 뒤 과금하므로 "
        + "그래프 Prepare 값이 Studio 설정과 다르면 장 수·비용이 바뀌거나(n·생성 조건마다 호출) 생성 조건이 바뀌어 이 다시 생성이 미뤄짐(모델·품질·변형 등)";
    if (fromButton) {
      // 유료 탭 길은 승인 제출이라 사용자 그래프 부수 효과 줄(pipe.side — §10.7 보정 4)을 붙임(지금 보내지 않으면 없음). 이 셀에 아직 돌지 않은
      // 사전 승인 재굴림이 있으면 누적 건수·금액을 맨 앞에(키보드 R 은 그때 거절 — rerollHold)
      const pre = cellPre(cell);
      const stack = pre ? `이 생성 조건 다시 생성 대기 ${pre}건(사전 승인) → ${pre + 1}건${usd(cell.est_usd ? { est_usd: cell.est_usd.map((v) => v * (pre + 1)) } : null)}\n` : "";
      if (!window.confirm(`${what}\n${stack}${graphPrep}\n` + (lock ? `유료 호출이 사전 승인됩니다 — 지금은 큐를 보내지 않음(${lock}): 다시 생성은 예약만 되고 `
        + "머리줄 [다시 생성 보내기]로 보내거나 다음 Run 때(선택과 무관) 함께 과금됩니다. 계속할까요?"
        : tab ? (way === "paid" ? `${pipe.side}\n` : "") + "유료 호출이 사전 승인되고 ComfyUI 탭에 큐 요청을 보냅니다. 계속할까요?"
          : `유료 호출이 사전 승인됩니다 — 지금 큐를 보내지 않음(${why}): 다시 생성은 예약만 되고 다음 Run 때(선택과 무관) 함께 과금됩니다. 계속할까요?`))) {
        return;
      }
    } else {
      // 키보드 R 은 R R 마다 +1 — 앞 R R 이 아직 처리·실행 중이면 무장하지 않는다(rerollHold — 첫 R·둘째 R 모두). 그래서 여기서 lock 은 늘 ""
      const hold = rerollHold(cell);
      if (hold) {
        rerollArm = null;
        toast(`R 무시 — ${hold}. 더 넣으려면 [다시 생성 +1] 단추(확인 창에 누적 건수)`, "warn");
        return;
      }
      const now = Date.now();
      if (!rerollArm || rerollArm.cell !== cell.cell_key || rerollArm.way !== way || now > rerollArm.until) {
        rerollArm = { cell: cell.cell_key, way, until: now + REROLL_CONFIRM_MS };
        // 유료 탭 길로 지금 보내면 승인 제출 — 버튼 R 확인 창의 부수 효과 줄(pipe.side)을 짧게
        toast(`R 을 한 번 더 누르면 ${what} — 유료`
          + (tab ? "" : ` · 지금 큐를 보내지 않음(${scripted ? "스크립트 키 — 큐는 사용자 키 입력만, " : ""}예약만 — 다음 Run 때 과금)`)
          + (way === "paid" ? " · 승인 제출이라 ComfyUI 탭 그래프의 시드 제어 등 실행마다 바뀌는 값이 한 번 진행됨(그래프는 실행 안 함)" : "")
          + (way === "paid" ? " · 금액은 지금 jobs 기준(Prepare 갈라짐 — Studio 실행 설정이 마지막 Prepare 와 다르면 바뀜)"
            : " · 금액은 지금 jobs 기준(그래프 Prepare 값이 Studio 와 다르면 바뀜)"), "warn");
        return;
      }
    }
    rerollArm = null;
    // 미반영 레이아웃 편집은 이 큐의 Import 가 함께 반영한다 — 이 크롭이 그중 하나면 셀이 바뀔 수 있고(다른 대상 문구만 바뀌면 그대로)
    // 바뀌면 이 재굴림은 돌지 않는다
    const pend = S.crops.filter((c) => LAYOUT_STATUS[c.layout_status]);
    const mine = pend.some((c) => c.id === curTarget().crop_id);
    if (mine && !window.confirm(`이 크롭은 레이아웃이 바뀌어(미반영) 이 큐에서 생성 조건이 바뀌면 다시 생성이 실행되지 않습니다. `
      + `미반영 레이아웃 ${pend.length}건도 이 큐로 반영됩니다. 계속할까요?`)) return;
    const r = await write("reroll", { cell_key: cell.cell_key, count: 1 },
      (x) => `다시 생성 +1 → ${x.variant || ""} 생성 조건 대기 ${x.cell_pending} · 전체 대기 ${x.pending_calls}`
        + (pend.length && !mine ? ` · 미반영 레이아웃 ${pend.length}건 함께 반영` : ""));
    // write 가 돌려준 뒤 다른 키 입력보다 먼저(같은 마이크로태스크 사슬) — 다시 받기가 실패해 S 가 낡아도 이 셀 R 은 그 rev 를 받을 때까지 거절
    if (r) rerollSent = { cell: cell.cell_key, rev: r.rev };
    if (r && !scripted) setTimeout(() => queueReroll(way, !!lock), 600);
  }

  function cellPre(cell) {
    // 그 셀의 아직 돌지 않은 사전 승인 재굴림 수(state 셀 pre). 옛 서버(키 없음)는 서버 원장 대체 계산과 같은 min(누적 재굴림, 대기) — 넘쳐 세면 R 만 막힘
    return "pre" in cell ? cell.pre : Math.min(cell.rerolls || 0, cell.pending || 0);
  }

  function rerollHold(cell) {
    // 키보드 R 을 무장하지 않는 이유("" = 무장 — 10/19 스모크에서 R 연타가 의도 1건을 2건으로 넣음): 앞 쓰기 처리 중, 이 셀의 마지막 재굴림 쓰기를
    // state 가 아직 담지 않음(rerollSent — 다시 받기가 실패해 S 가 낡은 채 busy 가 풀린 경우), 이 셀의 사전 승인 재굴림이 아직 돌지 않음,
    // Run(재굴림 템플릿 포함) 실행 중, 이 Studio 의 유료 요청·실행·무료 큐 중(paidLock)
    if (busy) return "앞 요청을 처리하는 중";
    if (rerollSent && rerollSent.cell === cell.cell_key && S.rev < rerollSent.rev) return "앞 다시 생성 쓰기를 아직 state 로 받지 못함";
    const pre = cellPre(cell);
    if (pre) return `이 생성 조건 다시 생성 대기 ${pre}건(사전 승인)이 아직 돌지 않음`;
    if (S.running) return "Run(다시 생성 포함) 실행 중";
    return pipe ? pipe.paidLock() : "";
  }

  function tabWay() {
    // R·머리줄 'ComfyUI 큐'가 갈 길: "paid" = 유료 승인을 받는 ComfyUI 탭(opener·채널)이고 서버가 재굴림 템플릿을 낼 수 있음(pipe.paidWhy — 탭 판정 +
    // 마지막 GET /pipeline 의 템플릿 게이트: 실행 설정 미저장·확인 필요·경로 경고·옛 서버면 늘 409·400 이라 S3a 처럼 opener 로), "opener" = opener 탭
    // (Review 부분 실행 — 옛 확장·유료 탭 길이 막힘), "" = 없음
    if (pipe && !pipe.paidWhy()) return "paid";
    return comfyConnected() ? "opener" : "";
  }

  function queueReroll(planned, held) {
    // 재굴림 큐(계약 §5): 유료 가능한 탭 = reroll stage 승인 메시지(템플릿 Run approve "" — 사전 승인 재굴림·복구만 과금, 확인 창 없음 — R 의
    // 확인이 동의), opener = 지금의 Review 부분 실행(옛 확장 대체), 없음 = 예약만(유료 탭 길을 못 쓰는 이유 — pipe.paidWhy 와 함께).
    // planned = R 확인 때의 판정(reroll) — "" 이면 '예약만'에 동의했으므로 그 사이 탭이 생겨도 보내지 않음, paid·opener 면 지금 판정이 같을 때만
    // (확인 문구의 과금 기준이 길마다 다름 — paid = Studio 실행 설정의 템플릿, opener = 그래프 위젯 값의 Review 부분 실행. 그 사이 탭이 큐 처리·
    // 이 Studio 의 유료 승인 처리로 바빠지거나 풀리면 길이 바뀜). held = R 확인 때 이 Studio 가 바빠(lock) '지금은 예약만'에 동의함 — 그 사이 풀려도
    // 보내지 않음. 길과 무관하게 지금 이 Studio 가 유료 요청·실행·무료 큐 중(paidLock)이면 보내지 않음(pipe.reroll 과 같은 예약 — 자기 승인을 처리하는
    // 탭은 hello busy 라 opener 길이 되므로 그 탭의 Review 부분 실행으로 새지 않게). 머리줄 'ComfyUI 큐'는 지금 판정
    const way = tabWay();
    const lock = way && pipe ? pipe.paidLock() : "";
    if (planned === "" && way) toast("다시 생성 예약됨 — R 확인 때 큐를 보낼 탭이 없어 지금 보내지 않음: 머리줄 [다시 생성 보내기]로 보내거나 다음 Run 때 과금", "warn");
    else if (planned && way !== planned) {
      const why = way === "paid" ? "탭이 이제 유료 승인을 받음 — 확인한 Review 부분 실행과 과금 기준이 다름"
        : (pipe && pipe.paidWhy()) || "이 창을 연 ComfyUI 탭 없음";
      toast(`다시 생성 예약됨 — R 확인 뒤 ComfyUI 탭 상태가 바뀌어 지금 보내지 않음(${why}): 머리줄 [다시 생성 보내기]로 보내거나 다음 Run 때 과금`, "warn");
    } else if (held || lock) {
      toast(`다시 생성 예약됨 — 지금 보내지 않음(${lock || "R 확인 때 '지금은 예약만'에 동의함"}): 머리줄 [다시 생성 보내기]로 보내거나 다음 Run 때 과금`, "warn");
    } else if (way === "paid") pipe.reroll();
    else if (way === "opener") queueInComfy();
    else toast(`다시 생성 예약됨 — Run 실행 때 과금(${(pipe && pipe.paidWhy()) || "큐를 보낼 ComfyUI 탭 없음"})`, "warn");
  }

  function comfyConnected() {
    const op = window.opener;
    return !!op && !op.closed;
  }

  function queueInComfy(layout = false) {
    // layout = 옛 서버 대체(재시작 전 — applyFree) 탭 layout 요청 — ComfyUI 탭은 Review 상류에 Import PSD 가 있을 때만 큐에 넣는다(아니면 그 탭이 알림)
    const op = window.opener;
    if (op && !op.closed) {
      try {
        op.postMessage({ type: "bmk-dp-queue", rootKey: ROOT, layout }, location.origin);
        toast("ComfyUI 탭에 큐 요청을 보냈습니다 (Review 노드 부분 실행)");
        return true;
      } catch (e) {
        // opener 가 다른 출처로 바뀜 → 아래 안내
      }
    }
    toast("ComfyUI 탭에서 큐에 넣으세요 — 이 창은 Review 노드의 Open Board 로 열리지 않았습니다", "warn");
    return false;
  }

  async function applyFree() {
    // 편집기 "반영(큐)"(opts.queue) = 무료 반영(서버 apply 템플릿을 /api/prompt 로 — 탭 불필요, 재굴림은 돌지 않음). 옛 서버(재시작 전 —
    // state 에 pipeline_rev 없음·pipeline.js 없음·/pipeline/prompt 404)면 지금까지의 반영(큐)(ComfyUI 탭의 Review 부분 실행)으로 대신한다.
    // 그 길은 대기 재굴림까지 과금하므로(편집기 확인 창은 무료 반영 문구) 대기 재굴림이 있으면 다시 묻는다
    if (pipe && S && "pipeline_rev" in S) {
      const r = await pipe.queue("apply");
      if (!r) return;
    }
    // 서버는 S3 인데 이 창에 pipeline.js 가 없음 = 이 창이 ComfyUI 재시작 전에 열림(다시 재시작해도 그대로 — 창 새로고침), 그 밖 = 서버가 옛 버전
    const stale = !pipe && !!S && "pipeline_rev" in S;
    const why = stale ? "Studio 창 새로고침 필요(이 창이 ComfyUI 재시작 전에 열려 무료 반영 모듈이 없음)" : "ComfyUI 재시작 필요(서버가 옛 버전)";
    const n = S ? S.pending_preapproved || 0 : 0;
    if (n && !window.confirm(`${why} — 무료 반영 대신 ComfyUI 탭의 Review 부분 실행으로 반영합니다. 대기 다시 생성 ${n}건(사전 승인)도 함께 과금됩니다.`
      + (stale ? " 창을 새로고침하면 무료 반영(다시 생성 과금 없음)을 씁니다." : "") + " 계속할까요?")) return;
    toast(queueInComfy(true) ? `${why} — 이번 반영은 ComfyUI 탭의 Review 부분 실행으로 보냈습니다`
      : `${why} — 무료 반영을 쓸 수 없어 ComfyUI 탭에서 큐에 넣어야 반영됩니다`, "warn");
  }

  function maskReady() {
    // 서버가 S4 이전 버전이면 state 크롭 요약에 mask_edits 가 없다(마스크 라우트도 없음)
    return !!maskEd && !!S && S.crops.some((c) => "mask_edits" in c);
  }

  function maskWhy() {
    // notReady 와 같은 갈래 — S4 표지는 state 최상위가 아니라 크롭 요약에 있다(크롭이 없으면 서버 버전을 알 수 없음 — Import 가 먼저)
    if (S && !S.crops.length) return "Import PSD 로 크롭이 생기고 ★ 를 고른 뒤 마스크 편집(K)을 쓸 수 있습니다";
    return S && S.crops.some((c) => "mask_edits" in c) && !maskEd
      ? "Studio 창을 새로고침해야 마스크 편집(K)을 쓸 수 있습니다(이 창이 ComfyUI 재시작 전에 열려 모듈이 없음)"
      : "ComfyUI 를 재시작해야 마스크 편집(K)을 쓸 수 있습니다(서버가 옛 버전)";
  }

  function setMask(on, to) {
    // 마스크 모드(K). to = {key, seam} — 편집 대상 ★ 후보·선택 이음매(없으면 이 창의 적용 안 한 세션 후보가 아직 ★ 면 그것, 아니면 검토에서 고른 타깃의 ★ —
    // 고른 후보가 ★·slice 면 그것. 세션 후보가 검토에서 ★ 해제·탈락되면 그 획은 적용할 수 없어(409) 새 ★ 로 — 버릴지는 편집기 확인 창. 그 타깃에 ★ 가
    // 남지 않았으면 세션으로 — 패널이 '이제 ★ 가 아님' 줄과 [버리기]를 보임: 진행 ★ 점검 줄의 'K 에서 버리기'). 선택 이음매가 없으면 검토의 N 이 가리키는
    // 이음매(그 ★ 의 것이면). 나갈 때 적용 안 한 획이 있으면 확인(세션은 이 창에 남음 — 진행(G) ★ 점검 줄). → 바꿨는가
    if (!on) {
      if (!maskEd.leave()) return false;
      maskOn = false;
      document.body.classList.remove("mask");
      $("mask-panel").hidden = true; // onKeyDown 의 마스크 모드 판정(진행 패널과 같은 꼴)
      $("btn-mask").classList.remove("on");
      if (pipe) pipe.setMaskLeft(maskEd.pending());
      renderAll(false);
      setTimeout(seeCurrent);
      return true;
    }
    if (!maskReady()) {
      toast(maskWhy(), "warn");
      return false;
    }
    const left = maskEd.pending();
    const t = curTarget();
    const c = curCand();
    const star = (k) => S.targets.some((x) => x.candidates.some((y) => y.key === k && y.picked && !y.rejected));
    const key = to ? to.key : left && star(left.key) ? left.key : c && c.picked ? c.key : t && t.pick_keys.length ? t.pick_keys[0]
      : left && t && t.candidates.some((x) => x.key === left.key) ? left.key : null;
    if (!key) {
      toast("★ 를 먼저 고르세요 — 마스크 편집(K)은 지금 ★ 후보의 최종 마스크(Export 그룹 마스크)를 손질합니다", "warn");
      return false;
    }
    if (editing) setEditing(false);
    if (refsOn) setRefs(false);
    if (pipeOn) setPipe(false);
    setViewFlags({ slider: false, raw: false }); // 검토의 S·M 보기(뷰어 머리줄 안내) — K 에서는 같은 글자가 편집기 보기라 검토 쪽은 끈다(검토 Esc 와 같음)
    maskOn = true;
    document.body.classList.add("mask");
    $("mask-panel").hidden = false;
    $("btn-mask").classList.add("on");
    const vs = !to && viewer ? viewer.curSeam() : null;
    maskEd.enter(S, { key, seam: to ? to.seam : vs && vs.key === key ? vs : null });
    return true;
  }

  function curMode() {
    return maskOn ? "mask" : editing ? "edit" : refsOn ? "refs" : pipeOn ? "pipe" : null;
  }

  function goMode(m) {
    // 탭: 그 모드로(null = 검토). 마스크 모드는 먼저 나감(적용 안 한 획 확인), 켜는 쪽 set* 이 다른 모드를 끈다
    if (typeof renderMode === "function") renderMode.initial = false;
    if (["prepare", "prompt", "generate"].includes(m)) {
      if (!leaveMask()) return;
      if (!pipeOn) setPipe(true);
      if (pipeOn) pipe.showTab(m);
      return;
    }
    if (m === curMode()) return;
    if (m === "mask") {
      setMask(true);
      return;
    }
    if (!leaveMask()) return;
    if (m === "edit") setEditing(true);
    else if (m === "refs") setRefs(true);
    else if (m === "pipe") setPipe(true);
    else {
      if (editing) setEditing(false);
      if (refsOn) setRefs(false);
      if (pipeOn) setPipe(false);
    }
  }

  function leaveMask() {
    // 머리줄 편집·레퍼런스·진행 단추·진행 설정 열기 앞 — 마스크 모드면 먼저 나감(적용 안 한 획 확인). → 계속해도 되는가
    return !maskOn || setMask(false);
  }

  function onUnload(e) {
    // 새로고침·닫기·다른 주소 — 적용 안 한 마스크 획은 이 창에만 있다(모드를 나가도 남음): 브라우저 확인 창
    if (maskEd && maskEd.pending()) e.preventDefault();
  }

  function maskSelect(key, follow) {
    // 마스크 편집 대상 = 보드 선택(뷰어 선택 크롭·F·1·2 의 초점). K 모드는 검토 화면을 보이지 않아 '새' 표시는 나올 때(seeCurrent).
    // 타깃이 바뀌면 selectTarget 처럼 레퍼런스 번호·R 연타 대기를 처음으로(K 를 나온 검토 화면이 앞 타깃의 번호를 쓰지 않게)
    const t = S ? S.targets.find((x) => x.candidates.some((c) => c.key === key)) : null;
    if (!t) return;
    if (t.key !== tKey) {
      refIdx = 0;
      rerollArm = null;
    }
    tKey = t.key;
    cKey = key;
    syncViewer(follow);
  }

  function maskPick(t, key) {
    // 마스크 편집기 대안 표 [이 후보를 ★] = 검토의 Space(그 타깃의 ★ 를 이 후보 하나로 — 표가 잰 합성과 같은 교체)
    return write("pick", { crop_id: t.crop_id, tid: t.tid, key, mode: "set" }, `★ 바꿈 — ${t.crop_name || t.crop_id} · ${t.tid}`);
  }

  function goMask(m, tkey) {
    // 진행(G) ★ 점검의 S4 줄 → 마스크 모드: m.key = 그 ★ 후보, m.crop = 그 크롭의 가장 나쁜 이음매(뷰어 목록은 bad 먼저·p90 큰 순)를 만든 ★ 후보와
    // 그 구간 — 이음매를 다시 받는 중(적용 직후 ≈ 2.5초)이면 그 크롭 첫 ★(구간 없이). 옛 서버·창이면 그 타깃을 검토에서
    const segs = (viewer && viewer.seams() && viewer.seams().segments) || [];
    const sg = m.crop ? segs.find((s) => s.crop_id === m.crop) || null : null;
    const t0 = m.crop && !sg && S ? S.targets.find((t) => t.crop_id === m.crop && t.pick_keys.length) : null;
    const key = m.key || (sg && sg.key) || (t0 && t0.pick_keys[0]);
    if (!maskReady() || !key) {
      goTarget(tkey);
      return;
    }
    setMask(true, { key, seam: sg });
  }

  function renderMaskButton() {
    const b = $("btn-mask");
    b.disabled = !maskReady();
    b.title = maskReady() ? "마스크 (K) — ★ 후보의 최종 마스크(Export 그룹 마스크) 손질·이음매 페이드·다른 후보 비교(무료)" : maskWhy();
  }

  async function goGrow(cropId, sides) {
    if (busy || (pipe && pipe.paidLock())) {
      toast("앞 요청을 처리하는 중입니다 — 끝난 뒤 다시 하세요", "warn");
      return;
    }
    if (maskEd && maskEd.pending()) {
      toast("적용 안 한 마스크 획이 있습니다 — 적용하거나 버린 뒤 크롭을 넓히세요", "warn");
      return;
    }
    if (!layoutReady() || !editor.proposeGrow) {
      toast(notReady("크롭 편집", "layout_rev", editor), "warn");
      return;
    }
    goMode("edit");
    if (editing) await editor.proposeGrow(cropId, sides);
  }

  function goTarget(key) {
    // 진행 패널 ★ 점검의 줄 → 그 타깃을 검토 모드에서
    if (!S || !S.targets.some((t) => t.key === key)) return;
    setPipe(false);
    selectTarget(key);
  }

  function drain() {
    if (!window.confirm("새 호출 멈춤(Drain): 진행 중인 Run 이 새 호출 발사를 멈춥니다.\n진행 중인 호출은 끝까지 받아 저장합니다. 계속할까요?")) return;
    write("drain", {}, (r) => (r.drain
      ? "새 호출 멈춤 요청 — 진행 중인 호출만 마저 받고 멈춥니다"
      : "진행 중인 Run 이 없습니다 (아무것도 하지 않음)"));
  }

  function help(show) {
    const h = $("help");
    h.hidden = show === undefined ? !h.hidden : !show;
  }

  function toggleViewer(show) {
    const on = show === undefined ? document.body.classList.contains("no-viewer") : show;
    document.body.classList.toggle("no-viewer", !on);
    savePref({ viewer: on });
  }

  function notReady(what, key, mod) {
    // 모드를 못 쓰는 이유: state 에 key 가 있는데 모듈 JS 가 없음 = 이 창이 ComfyUI 재시작 전에 열려 그 파일이 404 였음(창 새로고침 — 다시 재시작해도
    // 그대로), 아니면 서버가 옛 버전(ComfyUI 재시작)
    return S && key in S && !mod ? `Studio 창을 새로고침해야 ${what}을 쓸 수 있습니다(이 창이 ComfyUI 재시작 전에 열려 모듈이 없음)`
      : `ComfyUI 를 재시작해야 ${what}을 쓸 수 있습니다(서버가 옛 버전)`;
  }

  function layoutReady() {
    // 서버가 S1 이전 버전이면 state 에 layout_rev 가 없다(라우트도 없음) — 편집 버튼을 끄고 재시작 안내
    return !!editor && !!S && "layout_rev" in S;
  }

  function setEditing(on) {
    if (on && !layoutReady()) {
      toast(notReady("크롭 편집", "layout_rev", editor), "warn");
      return;
    }
    if (on && refsOn) setRefs(false);
    if (on && pipeOn) setPipe(false);
    editing = on;
    document.body.classList.toggle("editing", on);
    $("btn-edit").classList.toggle("on", on);
    if (on) {
      editor.enter(S);
    } else {
      editor.leave();
      syncViewer(false);
      setTimeout(seeCurrent); // 다른 모드로 바꾸는 중이면(그 모드가 켜진 뒤라) 표시하지 않음
    }
  }

  function refsReady() {
    // 서버가 S2 이전 버전이면 state 에 refs_rev 가 없다(레퍼런스 라우트도 없음)
    return !!refEd && !!S && "refs_rev" in S;
  }

  function setRefs(on) {
    if (on && !refsReady()) {
      toast(notReady("레퍼런스 편집", "refs_rev", refEd), "warn");
      return;
    }
    if (on && editing) setEditing(false);
    if (on && pipeOn) setPipe(false);
    refsOn = on;
    document.body.classList.toggle("refs", on);
    $("btn-refs").classList.toggle("on", on);
    if (on) {
      refEd.enter(S);
    } else {
      refEd.leave();
      syncViewer(false);
      setTimeout(seeCurrent);
    }
  }

  function pipeReady() {
    // 서버가 S3 이전 버전이면 state 에 pipeline_rev 가 없다(파이프라인 라우트도 없음)
    return !!pipe && !!S && "pipeline_rev" in S;
  }

  function setPipe(on) {
    if (on && !pipeReady()) {
      toast(notReady("실행(G)", "pipeline_rev", pipe), "warn");
      return;
    }
    if (on && editing) setEditing(false);
    if (on && refsOn) setRefs(false);
    pipeOn = on;
    document.body.classList.toggle("pipe", on);
    $("pipe-panel").hidden = !on; // onKeyDown 의 진행 모드 판정(도움말과 같은 꼴)
    $("btn-pipe").classList.toggle("on", on);
    if (on) {
      const t = curTarget();
      pipe.enter(S, t ? t.crop_id : null);
    } else {
      pipe.leave();
      syncViewer(false);
      setTimeout(seeCurrent);
    }
  }

  function setViewFlags(f) {
    if ("slider" in f) slider = f.slider;
    if ("raw" in f) raw = f.raw;
    if (viewer) viewer.setFlags({ slider, raw, qHeld });
  }

  // ── 그리기 ──────────────────────────────────────────
  function pad(n) {
    return String(n).padStart(2, "0");
  }

  function setConn(err) {
    const s = $("status");
    if (err) {
      s.textContent = `연결 오류: ${err}`;
      s.className = "status err";
    } else if (S) {
      s.textContent = `rev ${S.rev} · ${S.time} 갱신`;
      s.className = "status";
    }
  }

  function renderHeader() {
    if (!S) return;
    document.title = `${S.project} · Design Patch Studio`;
    $("proj").textContent = `${S.project} · 크롭 ${S.crops.length} · 타깃 ${S.counts.targets} · 후보 ${S.counts.candidates} · pick ${S.counts.picks}`;
    const b = $("badges");
    b.replaceChildren();
    if (S.backend_mock) b.append(el("span", "badge b-mock", "MOCK 백엔드 (과금 없음)"));
    if (S.running) b.append(el("span", "badge b-run", "Run 진행 중"));
    if (S.drain) b.append(el("span", "badge b-drain", "새 호출 멈춤 요청됨"));
    if (S.pending_calls) {
      const pre = S.pending_preapproved ? ` (다시 생성 승인 ${S.pending_preapproved})` : "";
      b.append(el("span", "badge b-wait", `대기 호출 ${S.pending_calls}${pre}`));
    }
    if (S.cost.calls) {
      const cb = el("span", "badge", `Run ${credits(S.cost.credits, S.cost.unknown)} · 호출 ${S.cost.calls}`);
      cb.title = `원장 calls 합계(완료 ${S.cost.done}) — Spec Draft 비용은 들어 있지 않음`;
      b.append(cb);
    }
    if (S.rerolls_total) b.append(el("span", "badge", `다시 생성 누적 ${S.rerolls_total}`));
    if (S.orphan_candidates) b.append(el("span", "badge b-bad", `크롭 없는 후보 ${S.orphan_candidates}`));
    if (tabLink) {
      const [cls, label] = TAB_BADGE[tabKey(tabLink)];
      const cn = el("span", "badge " + cls, label);
      cn.title = tabTitle(tabLink);
      b.append(cn);
    } else {
      const on = comfyConnected();
      const cn = el("span", "badge " + (on ? "b-run" : "b-dim"), on ? "ComfyUI 탭 연결" : "ComfyUI 탭 없음");
      cn.title = on ? "다시 생성 큐 요청을 이 창을 연 ComfyUI 탭으로 보냅니다"
        : "Review 노드의 Open Board 로 열지 않았거나 그 탭이 닫혔습니다 — 다시 생성은 ComfyUI 탭에서 직접 큐에 넣어야 합니다";
      b.append(cn);
    }
    $("btn-drain").disabled = !S.running;
    const pb = $("btn-pipe");
    pb.disabled = !pipeReady();
    pb.title = pipeReady() ? "준비 (G) — 실행 설정·무료 반영·초안 승인·생성 승인·채택·★ 점검·Export"
      : notReady("진행", "pipeline_rev", pipe);
    const eb = $("btn-edit");
    eb.disabled = !layoutReady();
    eb.title = layoutReady() ? "크롭 편집 (E) — 크롭 사각형·이름·대상을 Studio 에서" : notReady("크롭 편집", "layout_rev", editor);
    const rb = $("btn-refs");
    rb.disabled = !refsReady();
    rb.title = refsReady() ? "레퍼런스 (T) — 시트에서 오려 refs 에 저장·크롭 ID 로 연결(무료)" : notReady("레퍼런스 편집", "refs_rev", refEd);
  }

  function tabKey(k) {
    // TAB_BADGE 키(link 상태 + 확장 판·제출 가능 + 길): opener 가 hello 에 아예 답하지 않으면 silent
    if (k.state === "ok" && k.ext < 3) return "s3a";
    if (k.state === "ok" && k.submit !== "ok") return "nosubmit";
    if (k.state === "ok") return k.via === "opener" ? "ok" : "channel";
    if (k.state === "old" && k.ext === null) return "silent";
    return k.state;
  }

  function tabTitle(k) {
    // 유료 승인을 못 받는 이유는 한 곳의 문장(승인 단추 제목·R 확인 문구와 같은 말 — 연결된 탭은 tabWay 와 같은 pipe.paidWhy: 탭 + 서버 템플릿
    // 게이트, 그 밖은 link.paidWhy) — 여기는 탭(길·확장 판)과 그동안 R·ComfyUI 큐의 길만
    const why = k.state === "ok" && pipe ? pipe.paidWhy() : k.paidWhy();
    if (k.state === "ok") {
      const tab = `${k.via === "opener" ? "Open Board 로 연" : "채널로 찾은"} ComfyUI 탭(확장 판 ${k.ext})`;
      // 그 길이 있어도 이 Studio 가 유료 요청·실행·무료 큐 중이면(paidLock — queueReroll 처럼 길보다 먼저: 자기 승인을 처리하는 탭은 hello busy 라
      // opener 길이 됨) R·ComfyUI 큐는 지금은 예약만
      const lock = pipe && (!why || k.via === "opener") ? pipe.paidLock() : "";
      const held = `다시 생성(R)·[다시 생성 보내기]는 지금은 예약만(${lock} — 머리줄 [다시 생성 보내기] 또는 다음 Run 때 과금)`;
      if (!why) return `${tab} — 초안·생성 승인·다시 생성(R)·[다시 생성 보내기]가 이 탭으로(탭이 서버 템플릿을 큐에 넣음)` + (lock ? `. ${held}` : "");
      // 유료 탭 길이 막힘(S3a 판·제출 못 함·큐 처리 중·서버가 템플릿을 못 냄 — tabWay): R·ComfyUI 큐는 opener 면 그 탭의 Review 부분 실행(그래프 위젯
      // 값으로 과금), 채널로만 찾았으면 그 탭에서 직접
      return `${tab}: ${why}. ` + (lock ? held : k.via === "opener" ? "다시 생성(R)·[다시 생성 보내기]는 이 탭의 Review 부분 실행으로(그래프 Prepare 위젯 값으로 과금)"
        : "이 창은 Open Board 로 열리지 않아 다시 생성(R)·[다시 생성 보내기]는 그 탭에서 직접(R 은 사전 승인만 되고 다음 Run 때 과금)");
    }
    if (k.state === "old") return why;
    if (k.state === "many") return "응답한 ComfyUI 탭이 둘 이상이라 어느 탭인지 정할 수 없음 — Review 노드의 Open Board 로 다시 열면 그 탭";
    if (k.state === "none") return "응답하는 ComfyUI 탭이 없음 — 반영·Export 는 탭 없이 됨, 다시 생성은 ComfyUI 탭에서 큐에 넣어야 함";
    return "ComfyUI 탭에 hello 를 보내는 중";
  }

  function targetTitle(t) {
    const label = t.label && t.label !== t.part ? t.label : "";
    return `${t.crop_name || t.crop_id} · ${t.tid}` + (label ? ` ${label}` : "");
  }

  function targetChips(ch, t) {
    const k = t.chips;
    ch.append(chip(`후보 ${k.n}`));
    const fresh = t.candidates.filter(isNew).length;
    if (fresh) ch.append(chip(`새 ${fresh}`, "b-new"));
    if (k.picked) ch.append(chip(`★ ${k.picked}`, "b-star"));
    if (k.gate_fail) ch.append(chip(`게이트 ✕${k.gate_fail}`, "b-bad"));
    if (k.reframed) ch.append(chip(`reframed ${k.reframed}`, "b-reg"));
    if (k.mock) ch.append(chip(`MOCK ${k.mock}`, "b-mock"));
    if (t.pending_calls) ch.append(chip(`대기 ${t.pending_calls}`, "b-wait"));
    if (k.rejected) ch.append(chip(`제외 ${k.rejected}`, "b-rej"));
  }

  function cropChips(ch, c) {
    if (LAYOUT_STATUS[c.layout_status]) ch.append(chip(LAYOUT_STATUS[c.layout_status], "b-wait"));
    // 크롭 요약 refs {n, main, fixed, none, auto}(S2 §5.8 — refs_dir 가 없으면 null): 지금 연결·이름 매칭으로 레퍼런스가 없으면 칩.
    // 의도한 '없음 고정'(none = 연결 files 가 빔)만 중립, 연결한 파일이 모두 사라진 고정(none false — R-missing)은 경고
    // auto = 다른 크롭의 동작이 함께 '없음'으로 고정함(의도한 자체 복원 아님 — 레퍼런스가 아직 필요할 수 있음)
    if (c.refs && !c.refs.n) {
      const lost = c.refs.fixed && c.refs.none === false && !c.refs.auto;
      ch.append(lost ? chip("레퍼런스 없음(연결 파일 사라짐)", "b-bad") : c.refs.auto ? chip("레퍼런스 없음(자동 고정)", "b-wait")
        : chip(c.refs.fixed ? "레퍼런스 없이(고정)" : "레퍼런스 없음", c.refs.fixed ? "" : "b-wait"));
    }
    if (c.calls) ch.append(chip(credits(c.credits, c.credits_unknown), "b-cost"));
    if (c.dup_picks.length) ch.append(chip(`중복 ★ ${c.dup_picks.length}`, "b-bad"));
    if (c.stale_picked.length) ch.append(chip("옛 크롭 ★", "b-bad"));
    // S4 마스크 편집: 이 크롭의 지금 ★ 가운데 편집이 있는 수·그 가운데 기준이 바뀐 것(state 크롭 요약 — 옛 서버엔 키 없음)
    if (c.mask_edits) ch.append(chip(`편집 ${c.mask_edits}`, "b-reg"));
    if (c.mask_stale) ch.append(chip("처음 마스크가 바뀜", "b-bad"));
    const s = seamsByCrop.get(c.id);
    if (s) ch.append(chip(`이음매 ${s.n}`, s.bad ? "b-bad" : "b-wait"));
    if (c.warnings.length) {
      const w = chip(`경고 ${c.warnings.length}`, "b-bad");
      w.title = c.warnings.join("\n");
      ch.append(w);
    }
  }

  function draftChips(ch, c) {
    // §3.7 초안 대기 칩: state 크롭 요약 draft_chip = 보드가 기억한 only 없는 계획(없으면 null — 옛 서버엔 키 없음).
    // 그 계획의 서명이 지금과 다르면(plan_stale) 흐리게 — 다시 계산은 머리줄
    const d = c.draft_chip;
    if (!d) return;
    const st = c.plan_stale ? " stale" : "";
    if (d.running) ch.append(chip("실행 중", "b-run" + st));
    if (d.wait.length) {
      const w = chip("초안 대기", "b-wait" + st);
      w.title = d.wait.join("\n");
      ch.append(w);
    }
    if (d.refresh) ch.append(chip("초안 갱신 가능", st));
    if (d.adopt === "add") ch.append(chip("채택 가능", "b-reg" + st));
    if (d.run_need) ch.append(chip(`승인 필요 ${d.run_need}`, "b-wait" + st));
  }

  function renderTargets() {
    // 크롭마다 묶음: 타깃 하나면 한 줄(크롭 이름 + 타깃·크롭 칩), 여럿이면 크롭 머리 줄 + 타깃 줄
    const ol = $("target-list");
    ol.replaceChildren();
    if (!S) return;
    const byCrop = new Map();
    for (const t of S.targets) {
      if (!byCrop.has(t.crop_id)) byCrop.set(t.crop_id, []);
      byCrop.get(t.crop_id).push(t);
    }
    const cur = curTarget();
    for (const c of S.crops) {
      const ts = byCrop.get(c.id) || [];
      if (ts.length > 1) {
        const head = el("li", "crop" + (cur && cur.crop_id === c.id ? " cur" : ""));
        head.append(el("div", "tname", c.name || c.id));
        const ch = el("div", "chips");
        if (c.pending) ch.append(chip(`대기 ${c.pending}`, "b-wait"));
        cropChips(ch, c);
        draftChips(ch, c);
        if (ch.childElementCount) head.append(ch);
        head.addEventListener("click", () => selectTarget(ts[0].key));
        ol.append(head);
      }
      for (const t of ts) {
        const single = ts.length === 1;
        const li = el("li", "tgt" + (single ? " single" : " sub") + (t.key === tKey ? " sel" : "")
          + (t.candidates.length ? "" : " empty"));
        if (single) {
          li.append(el("div", "tname", targetTitle(t)));
        } else {
          const label = t.label && t.label !== t.part ? ` ${t.label}` : "";
          li.append(el("div", "tname", `${t.tid}${label}`));
        }
        if (t.name_en && t.name_en !== t.label) li.append(el("div", "tsub", t.name_en));
        const ch = el("div", "chips");
        targetChips(ch, t);
        if (single) {
          cropChips(ch, c);
          draftChips(ch, c);
        }
        li.append(ch);
        li.addEventListener("click", () => selectTarget(t.key));
        ol.append(li);
      }
    }
  }

  function renderCompare() {
    const t = curTarget();
    const c = curCand();
    setImg($("img-before"), t ? t.before_url : null);
    const refs = t ? t.refs : [];
    const ref = refs[refIdx] || null;
    setImg($("img-ref"), ref ? ref.thumb_url : null);
    $("ref-empty").hidden = !!ref;
    $("ref-count").textContent = refs.length > 1 ? `${refIdx + 1}/${refs.length}` : "";

    const panel = $("p-cand");
    let url = null;
    let caption = "후보";
    let mode = "cand";
    if (t && qHeld) {
      url = t.before_url;
      caption = "Before (Q)";
      mode = "before";
    } else if (c && view === "delta") {
      url = c.delta_url;
      caption = `ΔE 히트맵 c${pad(c.n)} (D)`;
      mode = "delta";
    } else if (c && view === "context") {
      url = c.context_url;
      caption = `컨텍스트 1.5× c${pad(c.n)} (C)`;
      mode = "context";
    } else if (c) {
      url = c.big_url;
      caption = `후보 c${pad(c.n)}` + (c.picked ? (c.slice_index === 1 ? " ★" : ` 조각 ${c.slice_index}`) : "")
        + (c.rejected ? " · 제외" : "") + (c.mock ? " · MOCK" : "") + (c.stale_rect ? " · 옛 크롭" : "") + (oldCell(c) ? " · 옛 조건" : "")
        + (ranText(c) ? ` · ${ranText(c)}` : "");
    }
    panel.className = "panel mode-" + mode;
    $("cand-caption").textContent = caption;
    setImg($("img-cand"), url);
    $("cand-empty").hidden = !!url;
  }

  function row(parent, key, text, cls = "") {
    const r = el("div", "row " + cls);
    r.append(el("span", "k", key), document.createTextNode(text));
    parent.append(r);
    return r;
  }

  function rowButton(r, label, fn) {
    const b = el("button", "inline", label);
    b.type = "button";
    b.tabIndex = -1;
    b.addEventListener("click", (e) => {
      e.currentTarget.blur();
      fn();
    });
    r.append(b);
  }

  function renderDetail() {
    const d = $("detail");
    d.replaceChildren();
    const sb = $("btn-star");
    const on = !!(curCand() && curCand().picked);
    sb.textContent = on ? "★ 해제" : "★ 선택";
    sb.title = on ? "Space — 이 후보의 ★ 를 풂(다시 누르면 ★)" : "Space — 이 후보 하나로 ★(그 타깃의 ★ 를 바꿈)";
    const t = curTarget();
    if (!t) {
      d.append(el("div", "row", S ? "타깃이 없습니다 — Import PSD 를 먼저 실행하세요" : "불러오는 중…"));
      return;
    }
    const c = curCand();
    const crop = cropOf(t);
    const [w, h] = t.size;
    row(d, "타깃", `${targetTitle(t)} · ${w}×${h}` + (t.name_en ? ` · ${t.name_en}` : "")
      + ` · 초점 [${t.focus_box.join(", ")}]`);
    const cells = t.cells.map((x) => `${x.variant || "?"} ${x.reps}+${x.rerolls}회 대기 ${x.pending}${usd(x)}`);
    row(d, "작업", cells.length ? cells.join(" | ") : "없음 (Prepare 전 — 다시 생성 불가)", cells.length ? "" : "warn");
    if (crop && crop.calls) {
      row(d, "비용", `이 크롭 Run ${credits(crop.credits, crop.credits_unknown)} · 호출 ${crop.calls}(완료 ${crop.calls_done})`
        + (crop.pending ? ` · 대기 ${crop.pending}` : ""));
    }
    if (crop && crop.dup_picks.length) {
      const names = crop.dup_picks.map((x) => `${x.tid}(= ${x.same_as})`).join(", ");
      const r = row(d, "주의", `같은 이미지가 여러 타깃에 ★: ${names} — 공동 결과는 첫 타깃 하나면 됩니다 `, "bad");
      rowButton(r, "중복 ★ 정리", () => clearDupPicks(crop));
    }
    if (crop && crop.stale_picked.length) {
      row(d, "주의", `★ 가 옛 크롭 후보(rect 를 옮기기 전): ${crop.stale_picked.join(", ")} — 새 크롭 후보를 ★ 하세요`, "bad");
    }
    if (!c) {
      row(d, "후보", "없음");
      return;
    }
    const src = [c.origin, c.backend, c.variant, c.rep !== null && c.rep !== undefined ? `r${c.rep}` : null]
      .filter((x) => x !== null && x !== undefined && x !== "").join(" · ");
    row(d, `c${pad(c.n)}`, `${c.label} · ${src}` + (c.src_size ? ` · ${c.src_size[0]}×${c.src_size[1]}` : "")
      + (ranText(c) ? ` · ${ranText(c)} 완료` : ""));
    if (c.joint && c.joint.first && c.joint.first !== t.tid) {
      const r = row(d, "공동", `${c.joint.tids.join("·")} 를 한 번에 고친 결과 — ★ 는 ${c.joint.first} 에만 `, "warn");
      if (c.joint.first_key) rowButton(r, `${c.joint.first} 에 ★`, starOnFirst);
    }
    if (c.stale_rect) row(d, "옛 크롭", "크롭 rect 를 옮기기 전에 만든 후보 — 지금 크롭과 위치·크기가 다름", "warn");
    if (oldCell(c)) row(d, "옛 조건", "지금 jobs 에 없는 생성 조건(Prepare 설정·입력이 바뀌기 전)으로 만든 후보 — 다시 생성(R)은 지금 생성 조건으로", "warn");
    let an = "분석 없음 (Analyze 전)";
    let cls = "";
    if (c.analysis === "skipped") an = "분석 생략(부분 패치)";
    if (c.analysis === "nosrc") {
      an = "자동 마스크 없음 — 크롭 rect 를 옮기기 전 후보인데 만들 때의 크롭 소스를 찾지 못함"
        + (c.hand_mask ? "" : " (Compose/Export 가 SO 전체를 합성)");
      cls = "bad";
    }
    if (c.gates) {
      const g = c.gates;
      const fails = g.fails.map((f) => GATE_NAMES[f] || f);
      an = (fails.length ? `게이트 실패: ${fails.join(", ")}` : "게이트 통과")
        + ` · MAD ${g.outside_mad ?? "-"} · >24 ${g.outside_frac_gt24 ?? "-"}`;
      if (fails.length) cls = "bad";
    }
    if (c.reg && c.reg.applied) {
      an += ` · 위치 맞춤 ${c.reg.kind} ${Number(c.reg.disp || 0).toFixed(1)}px rot ${Number(c.reg.rot_deg || 0).toFixed(2)}°`;
    }
    row(d, "분석", an, cls);
  }

  function ranText(c) {
    // 후보를 만든 호출의 완료 시각(state done = 원장 calls finished "YYYY-MM-DD HH:MM:SS" → "MM/DD HH:MM")·승인 종류 — 원장에 없는 후보는 ""
    if (!c.done) return "";
    const m = /^\d{4}-(\d\d)-(\d\d) (\d\d:\d\d)/.exec(c.done);
    return (m ? `${m[1]}/${m[2]} ${m[3]}` : c.done) + (VIA[c.via] ? ` ${VIA[c.via]}` : "");
  }

  function oldCell(c) {
    // 지금 jobs 에 없는 셀의 후보(Prepare 설정·입력이 바뀌기 전) — 옛 크롭(rect 를 옮기기 전)이면 그쪽 표시만
    return !!c.old_cell && !c.stale_rect;
  }

  function tileBadges(c) {
    const b = el("div", "tb");
    if (isNew(c)) b.append(el("span", "badge b-new", "새"));
    if (c.picked) b.append(el("span", "badge b-star", c.slice_index === 1 ? "★" : `S${c.slice_index}`));
    if (c.mock) b.append(el("span", "badge b-mock", "MOCK"));
    if (c.gates && c.gates.fails.length) b.append(el("span", "badge b-bad", "게이트"));
    if (c.analysis === "nosrc" && !c.hand_mask) b.append(el("span", "badge b-bad", "마스크 없음"));
    if (c.stale_rect) b.append(el("span", "badge b-bad", "옛 크롭"));
    if (oldCell(c)) b.append(el("span", "badge b-wait", "옛 조건"));
    if (c.reframed) b.append(el("span", "badge b-reg", "reframed"));
    if (c.rejected) b.append(el("span", "badge b-rej", "제외"));
    if (c.mask_edit) b.append(el("span", "badge " + (c.mask_edit.stale ? "b-bad" : "b-reg"), c.mask_edit.stale ? "처음 마스크가 바뀜" : "편집"));
    return b;
  }

  function renderGrid() {
    const g = $("grid");
    g.replaceChildren();
    const t = curTarget();
    if (!t) return;
    if (!t.candidates.length) {
      g.append(el("div", "empty", t.pending_calls ? `후보 없음 — 대기 호출 ${t.pending_calls}` : "후보 없음"));
      return;
    }
    for (const c of t.candidates) {
      const cls = ["tile"];
      if (c.picked) cls.push(c.slice_index === 1 ? "picked" : "slice");
      if (c.rejected) cls.push("rejected");
      if (c.key === cKey) cls.push("sel");
      const tile = el("div", cls.join(" "));
      const box = el("div", "imgbox");
      const img = el("img");
      img.loading = "lazy";
      img.alt = `c${pad(c.n)}`;
      setImg(img, c.thumb_url);
      box.append(img);
      tile.append(box, tileBadges(c));
      const meta = [c.origin === "run" ? c.variant : c.origin, c.rep !== null && c.rep !== undefined ? `r${c.rep}` : null]
        .filter(Boolean).join(" · ");
      // 원장 호출의 후보는 레이어명(셀 키) 대신 완료 시각·종류 — 레이어명은 제목·상세 줄에
      const cap = el("div", "cap", `c${pad(c.n)} · ${meta} · ${ranText(c) || c.label}`);
      cap.title = [c.label, ranText(c) && `${ranText(c)} 완료`].filter(Boolean).join(" · ");
      tile.append(cap);
      tile.addEventListener("click", () => selectCand(c.key));
      tile.addEventListener("dblclick", () => {
        selectCand(c.key);
        setStar();
      });
      g.append(tile);
    }
    const sel = g.querySelector(".tile.sel");
    if (sel) sel.scrollIntoView({ block: "nearest" });
  }

  function syncViewer(follow) {
    if (!viewer || editing) return; // 편집 모드의 선택은 편집기가 뷰어에 넘긴다
    const t = curTarget();
    viewer.setSelection(t ? { crop: cropOf(t), target: t, cand: curCand() } : null, follow);
  }

  function onSeams(seams) {
    seamsByCrop = new Map();
    for (const s of (seams && seams.segments) || []) {
      const v = seamsByCrop.get(s.crop_id) || { n: 0, bad: 0 };
      v.n += 1;
      v.bad += s.severity === "bad" ? 1 : 0;
      if (s.beside === null) {
        if (!v.growSides) v.growSides = [];
        if (!v.growSides.includes(s.side)) v.growSides.push(s.side);
      }
      seamsByCrop.set(s.crop_id, v);
    }
    renderTargets();
    if (maskOn) maskEd.seamsChanged();
    if (pipeOn) pipe.tabChanged(); // ★ 점검의 '이음매 bad n' — 진행 패널은 state 가 바뀐 때만 다시 그려 새 이음매(적용·반영 뒤 ≈ 2.5초)를 놓친다
  }

  function renderAll(follow) {
    renderHeader();
    renderMaskButton();
    renderTargets();
    renderCompare();
    renderDetail();
    renderGrid();
    if (viewer) viewer.setState(S);
    syncViewer(follow);
    if (maskOn) maskEd.stateChanged(S); // 새 state 만(편집 대상 정보를 다시 받음) — 매니페스트 rev 가 그대로인 폴링은 바뀔 것이 없다
    setConn("");
  }

  // ── 입력 ────────────────────────────────────────────
  function typing(t) {
    // data-keys="board"(레퍼런스 패널의 슬라이더·select·체크박스)는 빼고 — 값 변화 없이 놓거나 닫으면 change·blur 가 없어 포커스가 남는다.
    // 그 칸에서도 보드 키가 들고, 화살표 등은 REFS_OFF 가 기본 동작까지 막아 값을 말없이 바꾸지 않는다
    return t instanceof Element && !!t.closest("input, textarea, select, [contenteditable]") && !t.closest('[data-keys="board"]');
  }

  function onKeyDown(e) {
    if (typing(e.target)) return; // 입력칸(편집기 속성 패널) 포커스: 보드 단축키를 모두 무시 — Enter·Esc 는 입력칸이 처리
    if (typeof renderMode === "function" && ["KeyE", "KeyT", "KeyG", "KeyK"].includes(e.code)) renderMode.initial = false;
    // 포커스가 남은 data-keys="board" 칸은 키가 값을 바꾸지 않게 기본 동작을 막는다(Home·End·PageUp/Down·화살표·select 글자 찾기 — 바뀌면
    // change 로 회전 점프·다듬기 컷 요청이 말없이 감). 보드·레퍼런스 키 처리는 아래에서 그대로
    if (e.target instanceof Element && e.target.closest('[data-keys="board"]') && !e.ctrlKey && !e.metaKey && e.key !== "Tab") e.preventDefault();
    const helpOpen = !$("help").hidden;
    const pipeShown = !$("pipe-panel").hidden; // 진행 모드(G) — setPipe 가 패널의 hidden 을 함께 바꾼다
    const maskShown = !$("mask-panel").hidden; // 마스크 모드(K) — setMask 가 패널의 hidden 을 함께 바꾼다
    if (editing && !helpOpen && editor.onKey(e)) {
      e.preventDefault();
      return;
    }
    if (refsOn && !helpOpen && refEd.onKey(e)) { // Esc·Enter·[ ]·H·Backspace·0·1·2·F(시트 뷰어)
      e.preventDefault();
      return;
    }
    if (maskShown && !helpOpen && maskEd.onKey(e)) { // Esc·Enter·X·[ ]·Q·S·M·Ctrl+Z/Y
      e.preventDefault();
      return;
    }
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    const isHelpKey = (e.code === "Slash" && e.shiftKey) || e.key === "?";
    if (helpOpen) {
      if (e.code === "Escape" || isHelpKey) {
        help(false);
        e.preventDefault();
      }
      return;
    }
    if (isHelpKey) {
      help(true);
      e.preventDefault();
      return;
    }
    if (editing && EDIT_OFF.has(e.code)) {
      e.preventDefault();
      return;
    }
    if (refsOn && REFS_OFF.has(e.code)) {
      e.preventDefault();
      return;
    }
    if (pipeShown && PIPE_OFF.has(e.code)) {
      e.preventDefault();
      return;
    }
    if (maskShown && MASK_OFF.has(e.code)) {
      e.preventDefault();
      return;
    }
    switch (e.code) {
      case "KeyE":
        if (!e.repeat) setEditing(!editing);
        break;
      case "KeyT":
        if (!e.repeat) setRefs(!refsOn);
        break;
      case "KeyG":
        if (!e.repeat) setPipe(!pipeShown);
        break;
      case "KeyK":
        if (!e.repeat) setMask(!maskShown);
        break;
      case "ArrowUp": moveTarget(-1); break;
      case "ArrowDown": moveTarget(1); break;
      case "ArrowLeft": moveCand(-1); break;
      case "ArrowRight": moveCand(1); break;
      case "Space":
        if (!e.repeat) (e.shiftKey ? toggleSlice : setStar)();
        break;
      case "KeyX":
        if (!e.repeat) toggleReject();
        break;
      case "KeyQ":
        if (!qHeld) {
          qHeld = true;
          renderCompare();
          setViewFlags({});
        }
        break;
      case "KeyD":
        if (!e.repeat) {
          view = view === "delta" ? "cand" : "delta";
          renderCompare();
        }
        break;
      case "KeyC":
        if (!e.repeat) {
          view = view === "context" ? "cand" : "context";
          renderCompare();
        }
        break;
      case "KeyR":
        if (!e.repeat) reroll(false, !e.isTrusted);
        break;
      case "KeyB":
        if (!e.repeat && viewer) viewer.setBg(viewer.flags().bg === "base" ? "composite" : "base");
        break;
      case "KeyO":
        if (!e.repeat && viewer) viewer.toggleRects();
        break;
      case "Digit0":
        if (viewer) viewer.fitCanvas();
        break;
      case "KeyF":
        if (viewer) viewer.fitCrop();
        break;
      case "Digit1":
        if (viewer) viewer.zoomTo(1);
        break;
      case "Digit2":
        if (viewer) viewer.zoomTo(2);
        break;
      case "KeyS":
        if (!e.repeat) setViewFlags({ slider: !slider });
        break;
      case "KeyM":
        if (!e.repeat) setViewFlags({ raw: !raw });
        break;
      case "KeyN":
        if (!e.repeat && viewer) viewer.nextSeam();
        break;
      case "KeyV":
        if (!e.repeat) toggleViewer();
        break;
      case "Escape":
        view = "cand";
        renderCompare();
        setViewFlags({ slider: false, raw: false });
        break;
      default:
        return;
    }
    e.preventDefault();
  }

  function onKeyUp(e) {
    if (maskOn) maskEd.onKeyUp(e);
    if (e.code === "KeyQ" && qHeld) {
      qHeld = false;
      renderCompare();
      setViewFlags({});
    }
  }

  function goProjects() {
    // 배치 7 공용 자리(감독관 — _proto/STUDIO_BATCH7_SPEC.md D48): 같은 창에서 프로젝트 목록으로. 본문은 7-A 가 채운다(pipe.canLeave 가드).
  }

  function wireButtons() {
    const on = (id, fn) => $(id).addEventListener("click", (e) => {
      e.currentTarget.blur();
      fn(e);
    });
    on("btn-star", setStar);
    on("btn-slice", toggleSlice);
    on("btn-reject", toggleReject);
    on("btn-reroll", () => reroll(true));
    // 머리줄 'ComfyUI 큐'는 확인 창 없이 지금 큐를 보냄(사전 승인분 과금) — 사용자 클릭만(D7: 스크립트 클릭이 바로 과금하지 않게)
    on("btn-queue", (e) => (e.isTrusted ? queueReroll() : toast("머리줄 [다시 생성 보내기]는 사용자 클릭만 — 스크립트 클릭은 큐를 보내지 않음", "warn")));
    on("btn-drain", drain);
    on("btn-viewer", () => toggleViewer());
    on("btn-help", () => help());
    on("btn-projects", () => goProjects());
    // 모드 탭(배치 2a): 누른 모드로 — 이미 그 모드면 그대로(예전 단추는 다시 누르면 꺼져 검토로 돌아갔음). 키 E·T·G·K 는 그대로 켜고 끄기
    on("btn-review", () => goMode(null));
    on("btn-edit", () => goMode("edit"));
    on("btn-refs", () => goMode("refs"));
    on("btn-pipe", () => goMode("prepare"));
    on("btn-prompt", () => goMode("prompt"));
    on("btn-generate", () => goMode("generate"));
    on("btn-mask", () => goMode("mask"));
    on("btn-export", () => pipe && pipe.exportNow());
    on("btn-export-folder", () => pipe && pipe.openFolder());
    on("btn-export-settings", () => pipe && pipe.goSection({ tab: "generate", section: "Export" }));
    on("btn-keys", () => {
      const off = !document.body.classList.contains("no-keys");
      document.body.classList.toggle("no-keys", off);
      savePref({ keys: !off });
    });
    $("ref-frame").addEventListener("click", () => {
      const t = curTarget();
      if (t && t.refs.length > 1) {
        refIdx = (refIdx + 1) % t.refs.length;
        renderCompare();
      }
    });
    $("help").addEventListener("click", (e) => {
      if (e.target === $("help")) help(false);
    });
  }

  // 머리줄의 지금 모드 이름 + 한 줄 설명(F12) — 모드는 body 클래스(setMask·setPipe·setRefs·setEditing)로 정해지므로 그것을 지켜봄
  const MODES = [["mask", "마스크", "★ 후보의 최종 마스크(Export 그룹 마스크)를 붓·페이드로 손질"],
    ["pipe", "실행", "실행 설정 · 무료 반영 · 계획·비용 · 초안·생성 승인 · ★ 점검 · Export"],
    ["refs", "레퍼런스", "시트에서 부품을 오려 refs 에 저장하고 크롭에 연결 · [그리드] = 크롭별 매칭을 한눈에"],
    ["editing", "크롭 편집", "크롭 사각형·이름·대상 문구·부품 상자"]];

  // 이 모드의 단축키 칩(F13·F14): [키, 하는 일, (선택) {code, shift, ctrl, hold}] — 칩을 누르면 그 키를 누른 것과 같음(hold = 누르는 동안).
  // code 없는 줄은 설명만(끌기 등). R(다시 생성)은 넣지 않음 — 스크립트 키는 예약만이라 칩으로는 큐가 가지 않아 헷갈림([재굴림 +1] 단추가 그 길)
  const VIEW_KEYS = [["N", "이음매", { code: "KeyN" }], ["F", "크롭 맞춤", { code: "KeyF" }], ["0", "전체", { code: "Digit0" }], ["1", "1:1", { code: "Digit1" }],
    ["2", "2:1", { code: "Digit2" }]];
  const KEYS = {
    review: [["Space", "★ 선택·해제", { code: "Space" }], ["⇧Space", "함께 쓰기 ±", { code: "Space", shift: true }], ["X", "제외", { code: "KeyX" }],
      ["←", "후보", { code: "ArrowLeft" }], ["→", "후보", { code: "ArrowRight" }], ["↑", "타깃", { code: "ArrowUp" }], ["↓", "타깃", { code: "ArrowDown" }],
      ["Q", "Before(누름)", { code: "KeyQ", hold: true }], ["S", "전/후 슬라이더", { code: "KeyS" }], ["M", "마스크 없이", { code: "KeyM" }],
      ["D", "ΔE 히트맵", { code: "KeyD" }], ["C", "컨텍스트", { code: "KeyC" }], ["Esc", "보기 해제", { code: "Escape" }], ["B", "바탕", { code: "KeyB" }],
      ["O", "사각형", { code: "KeyO" }], ...VIEW_KEYS, ["?", "도움말", { code: "Slash", shift: true }]],
    edit: [["끌기", "새 크롭·이동·크기"], ["P", "부품 상자", { code: "KeyP" }], ["Enter", "만들기·제안 적용", { code: "Enter" }],
      ["Tab", "다음 크롭", { code: "Tab" }], ["⇧Tab", "이전 크롭", { code: "Tab", shift: true }], ["←→↑↓", "미세 이동"],
      ["Del", "크롭 지우기", { code: "Delete" }], ["L", "잠금", { code: "KeyL" }],
      ["Ctrl+Z", "되돌리기", { code: "KeyZ", ctrl: true }], ["Ctrl+Y", "다시", { code: "KeyY", ctrl: true }], ["Esc", "취소·나가기", { code: "Escape" }],
      ["Space+끌기", "화면 이동"], ["F", "크롭 맞춤", { code: "KeyF" }], ["0", "전체", { code: "Digit0" }], ["E", "나가기", { code: "KeyE" }]],
    refs: [["끌기", "영역"], ["Enter", "컷", { code: "Enter" }], ["T", "나가기", { code: "KeyT" }], ["[", "회전 −0.5°", { code: "BracketLeft" }], ["]", "회전 +0.5°", { code: "BracketRight" }],
      ["H", "좌우 반전", { code: "KeyH" }], ["⌫", "마지막 점 지우기", { code: "Backspace" }], ["F", "영역 맞춤", { code: "KeyF" }], ["0", "시트 전체", { code: "Digit0" }],
      ["Esc", "취소·나가기", { code: "Escape" }]],
    // 레퍼런스 [그리드] 탭(3-4): 시트가 숨어 시트 키가 꺼짐 — 카드 고르기와 나가기만
    refsGrid: [["카드 클릭", "크롭 고르기"], ["T", "나가기", { code: "KeyT" }], ["Esc", "선택 해제·나가기", { code: "Escape" }]],
    pipe: [["B", "바탕", { code: "KeyB" }], ["O", "사각형", { code: "KeyO" }], ...VIEW_KEYS, ["G", "나가기", { code: "KeyG" }]],
    mask: [["V", "붓 ↔ 변형", { code: "KeyV" }], ["D", "원본과 차이(변형)", { code: "KeyD" }], ["끌기", "붓·변형"], ["⇧끌기", "흐리기·비율/15°"], ["X", "지우기 ↔ 살리기", { code: "KeyX" }], ["[", "붓 작게", { code: "BracketLeft" }], ["]", "붓 크게", { code: "BracketRight" }],
      ["⇧[", "경도 −", { code: "BracketLeft", shift: true }], ["⇧]", "경도 +", { code: "BracketRight", shift: true }], ["Q", "처음 마스크(누름)", { code: "KeyQ", hold: true }],
      ["W", "원본(누름)", { code: "KeyW", hold: true }], ["S", "처음/편집 슬라이더", { code: "KeyS" }], ["M", "마스크 보기", { code: "KeyM" }],
      ["O", "선 숨기기", { code: "KeyO" }], ["←→↑↓", "패치 이동"], ["⇧←→↑↓", "10px"],
      ["N", "이음매", { code: "KeyN" }], ["Enter", "적용", { code: "Enter" }], ["Ctrl+Z", "되돌리기", { code: "KeyZ", ctrl: true }], ["Ctrl+Y", "다시", { code: "KeyY", ctrl: true }],
      ["Esc", "나가기", { code: "Escape" }]],
  };

  const CHIP_KEY = { Slash: "?", Space: " ", BracketLeft: "[", BracketRight: "]" };
  let chipUp = null; // 누르고 있는 칩(Q·W)의 떼기 — 그 채로 모드가 바뀌어 칩 줄을 다시 그리면 지운 칩은 pointerup 을 못 받으니 먼저 뗀다

  function sendKey(type, k) {
    // 칩 = 그 키: body 에서 키 이벤트(문서로 올라가 보드·편집기 처리기가 받음 — 처리기는 e.code 를 봄. target 이 요소여야 viewer.js 의
    // 입력칸 판정(closest)이 됨). key 는 참고용(글자 키 소문자, ? = Shift+/)
    const key = CHIP_KEY[k.code] || (k.code.startsWith("Key") ? k.code.slice(3).toLowerCase() : k.code.startsWith("Digit") ? k.code.slice(5) : k.code);
    document.body.dispatchEvent(new KeyboardEvent(type, { code: k.code, key, shiftKey: !!k.shift, ctrlKey: !!k.ctrl, bubbles: true, cancelable: true }));
  }

  function renderKeys(mode) {
    if (chipUp) chipUp();
    const bar = $("keybar");
    bar.replaceChildren();
    for (const [label, what, k] of KEYS[mode] || []) {
      if (!k) {
        bar.append(el("span", "kinfo", `${label} ${what}`));
        continue;
      }
      const b = el("button", "kchip");
      b.type = "button";
      b.tabIndex = -1;
      b.append(el("kbd", "", label), document.createTextNode(what));
      b.title = `${label} — ${what}${k.hold ? " — 누르고 있는 동안만" : ""}`;
      if (k.hold) {
        // 누르는 동안 키(Q·W): 누르면 keydown, 떼거나 칩 밖으로 나가면 keyup
        let down = false;
        const up = () => {
          if (!down) return;
          down = false;
          chipUp = null;
          b.classList.remove("held");
          sendKey("keyup", k);
        };
        b.addEventListener("pointerdown", (e) => {
          e.preventDefault();
          down = true;
          chipUp = up;
          b.classList.add("held");
          sendKey("keydown", k);
        });
        b.addEventListener("pointerup", up);
        b.addEventListener("pointerleave", up);
      } else {
        b.addEventListener("click", (e) => {
          e.currentTarget.blur();
          sendKey("keydown", k);
          sendKey("keyup", k);
        });
      }
      bar.append(b);
    }
  }

  function renderMode() {
    const m = MODES.find(([cls]) => document.body.classList.contains(cls)) || [null, "검토", "후보를 원해상도로 비교하고 ★ 를 고름 · 이음매 확인"];
    const d = $("mode-title");
    d.textContent = m[2]; // 이름은 탭이 보임(배치 2a)
    d.title = `${m[1]} — ${m[2]}`;
    const sub = pipe && pipe.tab ? pipe.tab() : "prepare";
    const tab = { mask: "btn-mask", pipe: { prompt: "btn-prompt", generate: "btn-generate" }[sub] || "btn-pipe", refs: "btn-refs", editing: "btn-edit" }[m[0]] || "btn-review";
    const ids = ["btn-pipe", "btn-edit", "btn-refs", "btn-prompt", "btn-generate", "btn-review", "btn-mask"];
    const steps = pipe && pipe.steps ? pipe.steps() : null;
    for (const id of ids) {
      $(id).classList.toggle("on", id === tab);
      $(id).setAttribute("aria-selected", String(id === tab));
      const state = steps && steps.stages[ids.indexOf(id)];
      const dot = $(id).querySelector(".step-state");
      if (dot) {
        const n = state && state.value.match(/(?:필요|대기|진행) (\d+)/);
        dot.textContent = !state ? "?" : state.state === "done" ? "✓" : state.state === "warn" ? "경고"
          : state.state === "unknown" ? "?" : state.state === "optional" ? "선택" : n ? `대기 ${n[1]}` : "대기";
        dot.className = `step-state ${state ? state.state : "unknown"}`;
        dot.title = state ? `전체 기준 · ${state.value}` : "전체 기준 · 계획을 받는 중";
      }
    }
    for (const id of ["btn-prompt", "btn-generate"]) {
      $(id).disabled = !pipeReady();
      $(id).title = pipeReady() ? `${id === "btn-prompt" ? "프롬프트" : "생성"} — 실행 하위 탭` : notReady("실행", "pipeline_rev", pipe);
    }
    const ex = $("btn-export");
    const why = pipe && pipe.exportWhy ? pipe.exportWhy() : "실행 설정을 받는 중";
    ex.disabled = pipe && pipe.exportLocked ? pipe.exportLocked() : !!why;
    ex.classList.toggle("export-settings-needed", !!why && !ex.disabled);
    ex.title = why || "무료 Export — 마지막 반영으로 PSD 출력";
    const last = S && S.export_last;
    $("export-state").textContent = last ? `r${last.rev} · ${S.export_state === "same" ? "변경 없음" : "다시 Export 필요"}`
      : steps && steps.export.state !== "todo" ? steps.export.value : "Export 없음";
    const key = m[0] === "refs" && refTab === "grid" ? "refsGrid" : { mask: "mask", pipe: "pipe", refs: "refs", editing: "edit" }[m[0]] || "review";
    if (renderMode.key !== key) {
      renderMode.key = key;
      renderKeys(key);
      $("keybar").prepend(d);
    }
    if (renderMode.initial && curMode() !== null) renderMode.initial = false;
    if (renderMode.initial && steps && steps.ready) {
      renderMode.initial = false;
      const first = steps.stages.slice(0, 3).findIndex((s) => s.state === "todo" || s.state === "warn");
      if (first >= 0) goMode(["prepare", "edit", "refs"][first]);
    }
  }

  function fatal(msg) {
    const app = $("app");
    app.replaceChildren(el("p", "fatal", msg));
  }

  async function main() {
    if (!/^[0-9a-f]{12}$/.test(ROOT)) {
      fatal("root 파라미터가 없거나 잘못되었습니다 — ComfyUI 의 BMK Design Patch Review 노드에서 Open Board 로 여세요.");
      return;
    }
    if (loadPref().viewer === false) document.body.classList.add("no-viewer");
    if (loadPref().keys === false) document.body.classList.add("no-keys");
    renderMode();
    new MutationObserver(renderMode).observe(document.body, { attributes: true, attributeFilter: ["class"] });
    viewer = window.DPViewer.create($("viewer"), { apiUrl, onPick: pickCrop, onSeams });
    // 편집기 "반영(큐)" = 무료 반영(applyFree), 확인 창 맨 앞 줄 = Prepare 설정 갈라짐(진행 패널이 읽은 GET /pipeline)
    const applyWarn = () => (pipe ? pipe.applyWarn() : []);
    if (window.DPLayoutEdit) {
      // psdPartial = 서버가 S1-a(PSD 기준점을 고른 이름만 갱신)인가 — S3 서버(state 에 pipeline_rev)와 함께 들어옴. 재시작 전 옛 서버는 take·ack 가
      // 기준점을 통째로 바꿔 나머지 갈라짐 경고도 꺼진다(편집기가 옛 문구로)
      editor = window.DPLayoutEdit.create({ list: $("layout-list"), panel: $("layout-panel"), head: $("layout-head") },
        { apiUrl, root: ROOT, viewer, toast, queue: applyFree, applyWarn, psdPartial: () => !!S && "pipeline_rev" in S, exit: () => setEditing(false) });
    }
    if (window.DPRefEdit) {
      const sheetViewer = window.DPViewer.create($("ref-viewer"), { apiUrl });
      refTab = loadPref().refTab === "grid" ? "grid" : "sheets";
      // 가운데 탭 [시트]/[그리드](3-4)는 이 브라우저에 기억(refTab — 저장소를 못 쓰면 이번 창만, 처음은 시트), 바뀌면 단축키 칩 줄도
      refEd = window.DPRefEdit.create({ list: $("ref-list"), panel: $("ref-panel"), head: $("ref-head"), tabs: $("ref-tabs"), sheets: $("ref-sheets"),
        view: $("ref-viewer"), grid: $("ref-grid") }, { apiUrl, comfyUrl, root: ROOT, viewer: sheetViewer, toast, queue: applyFree, applyWarn,
        tab: refTab, tabChanged: (t, keep) => {
          refTab = t;
          if (keep) savePref({ refTab: t });
          renderMode();
        }, exit: () => setRefs(false) });
    }
    if (window.DPPipeline) {
      // 탭 연결이 바뀌면 머리줄 배지와 진행 패널의 승인 단추를 다시 그림(첫 판정은 hello 응답을 모은 뒤 — 그때는 pipe 가 있음)
      tabLink = window.DPPipeline.link(window, () => {
        renderHeader();
        pipe.tabChanged();
      });
      pipe = window.DPPipeline.create({ list: $("pipe-list"), panel: $("pipe-panel"), head: $("pipe-head") }, {
        apiUrl, comfyUrl, root: ROOT, toast, drain, goTarget, goMask, goGrow, link: tabLink,
        goMode: (mode, cropId) => {
          const t = S && cropId ? S.targets.find((x) => x.crop_id === cropId) : null;
          if (t) selectTarget(t.key);
          goMode(mode);
          if (mode === "refs" && refsOn && cropId) refEd.select(cropId);
        },
        refresh: () => refresh(true),
        stepsChanged: renderMode,
        maskPending: () => maskEd && maskEd.pending(),
        editPending: () => !!S && window.DPPipeline.layoutLines(S).length > 0,
        select: (id) => {
          const t = S ? S.targets.find((x) => x.crop_id === id) : null;
          if (t) selectTarget(t.key);
        },
        seams: () => (S && S.canvas && !viewer.seams() ? null : seamsByCrop), // 이음매를 다시 재는 중이면 null(★ 점검이 옛 bad 수 대신 '재는 중')
        enter: () => leaveMask() && setPipe(true),
        layoutWrote: (from, to) => editor && editor.ownWrite(from, to), // 반영 칸 [되돌리기] — 이 창 편집기의 Ctrl+Z 기록을 남김
      });
    }
    if (window.DPMaskEdit) {
      maskEd = window.DPMaskEdit.create({ list: $("mask-list"), panel: $("mask-panel") }, {
        apiUrl, root: ROOT, viewer, toast, ranText, goGrow,
        exit: () => setMask(false),
        refresh: () => refresh(true),
        seams: () => viewer.seams(),
        select: maskSelect,
        pick: maskPick,
      });
    }
    wireButtons();
    document.addEventListener("keydown", onKeyDown);
    document.addEventListener("keyup", onKeyUp);
    window.addEventListener("storage", onSeenStorage);
    window.addEventListener("beforeunload", onUnload);
    window.addEventListener("blur", () => {
      if (qHeld) {
        qHeld = false;
        renderCompare();
        setViewFlags({});
      }
    });
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) {
        refresh(false);
        schedule();
      }
    });
    await refresh(true);
    if (!S) {
      const s = $("status");
      fatal(s.textContent || "상태를 불러오지 못했습니다");
      return;
    }
    const requested = new URLSearchParams(location.search).get("mode");
    if (["prepare", "prompt", "generate", "edit", "refs", "pipe", "mask", "review"].includes(requested)) goMode(requested === "review" ? null : requested);
    else if (!S.targets.some((t) => (t.candidates || []).length)) {
      renderMode.initial = true;
      renderMode();
    }
    schedule();
  }

  main();
})();
