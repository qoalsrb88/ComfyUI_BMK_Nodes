// BMK Design Patch Studio(Review Board) — vanilla JS, 빌드·외부 리소스 없음.
// 서버: bmk_design_patch_board.py (/bmk/design_patch/*). 모든 문자열(PSD 레이어명 등)은 textContent 로만 넣는다.
// 보드는 매니페스트(★·slice·탈락·재굴림 의도)만 고친다. 유료 호출은 opener(ComfyUI 탭)가 Review 노드를 큐에 넣을 때만.
// 캔버스 뷰어는 viewer.js(DPViewer) — 이 파일이 선택·키 입력을 넘긴다. 레이아웃 편집 모드(E)는 layout_edit.js(DPLayoutEdit).
"use strict";

(() => {
  const API = new URL("../", location.href); // /bmk/design_patch/ (또는 /api/bmk/design_patch/)
  const ROOT = new URLSearchParams(location.search).get("root") || "";
  const POLL_MS = 2000;
  const POLL_HIDDEN_MS = 10000;
  const REROLL_CONFIRM_MS = 2500;
  const GATE_NAMES = { outside_mad: "마스크 밖 MAD", outside_frac_gt24: "마스크 밖 큰 차이", black_band: "검은 띠" };
  const PREF_KEY = "bmk-dp-studio";
  // 편집 모드에서 끄는 검토 키: ★(Space)·탈락(X)·재굴림(R, 유료)·타깃/후보 이동·비교 보기. Space 는 viewer.js 가 화면 이동에 쓴다
  const EDIT_OFF = new Set(["Space", "KeyX", "KeyR", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "KeyQ", "KeyD", "KeyC",
    "KeyS", "KeyM", "KeyV"]);
  const LAYOUT_STATUS = { changed: "레이아웃 바뀜(미반영)", removed: "레이아웃에서 지움(미반영)" }; // state 크롭의 layout_status

  let S = null; // 마지막 전체 상태
  let tKey = null; // 선택한 타깃 "crop_id/tid"
  let cKey = null; // 선택한 후보 key
  let view = "cand"; // cand | delta | context
  let qHeld = false;
  let refIdx = 0;
  let busy = false;
  let polling = false;
  let pollAgain = false;
  let pollTimer = null;
  let rerollArm = null; // {cell, until}
  let toastTimer = null;
  let viewer = null;
  let slider = false;
  let raw = false;
  let seamsByCrop = new Map(); // crop_id → {n, bad}
  let editor = null; // DPLayoutEdit — 옛 서버(ComfyUI 재시작 전)는 layout_*.js 를 주지 않아 null
  let editing = false;

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

  function setImg(img, rel) {
    const u = rel ? apiUrl(rel) : null;
    if (u) {
      if (img.getAttribute("src") !== u) img.src = u;
      img.hidden = false;
    } else {
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

  async function refresh(force = false) {
    if (polling) {
      pollAgain = pollAgain || force;
      return;
    }
    polling = true;
    try {
      const d = await getState(force);
      if (d.unchanged && S) {
        S.running = d.running;
        S.drain = d.drain;
        // 레이아웃 쓰기는 매니페스트 rev 를 올리지 않는다 — layout_rev 가 바뀌면 크롭 layout_status 를 새로 받는다
        if ("layout_rev" in d && d.layout_rev !== S.layout_rev) pollAgain = true;
        renderHeader();
      } else if (!d.unchanged) {
        S = d;
        reconcile();
        renderAll(false);
      }
      if (editing && S) editor.stateChanged(S);
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
    if (refIdx >= t.refs.length) refIdx = 0;
  }

  function selectTarget(key) {
    if (key === tKey) return;
    tKey = key;
    cKey = defaultCand(curTarget());
    refIdx = 0;
    rerollArm = null;
    renderAll(true);
  }

  function selectCand(key) {
    cKey = key;
    rerollArm = null;
    renderTargets();
    renderCompare();
    renderDetail();
    renderGrid();
    syncViewer(false);
  }

  function pickCrop(cropId, tid, candKey) {
    // 캔버스에서 크롭을 누름(tid 없음): 같은 크롭이면 다음 타깃, 아니면 첫 타깃. 이음매 이동은 tid·그 ★ 후보 키를 준다.
    if (!S) return;
    const ts = S.targets.filter((t) => t.crop_id === cropId);
    if (!ts.length) return;
    let t = tid ? ts.find((x) => x.tid === tid) : null;
    if (!t) {
      const i = ts.findIndex((x) => x.key === tKey);
      t = ts[i >= 0 ? (i + 1) % ts.length : 0];
    }
    selectTarget(t.key);
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
      mode === "add" ? `slice 추가 c${pad(c.n)}` : `pick 에서 뺌 c${pad(c.n)}`);
  }

  function toggleReject() {
    const c = curCand();
    if (!c) return;
    write("reject", { key: c.key, value: !c.rejected },
      (r) => (r.rejected ? `탈락 c${pad(c.n)}` + (r.unpicked ? " (pick 에서도 뺌)" : "") : `탈락 해제 c${pad(c.n)}`));
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

  async function reroll(fromButton) {
    const cell = rerollCell();
    if (!cell) {
      toast("이 타깃의 작업(jobs)이 없습니다 — Prepare 를 먼저 실행하세요", "err");
      return;
    }
    const what = `재굴림 +1 호출 (${cell.variant || cell.cell_key.slice(0, 8)}${usd(cell)})`;
    if (fromButton) {
      if (!window.confirm(`${what}\n유료 호출이 사전 승인되고 ComfyUI 탭에 큐 요청을 보냅니다. 계속할까요?`)) return;
    } else {
      const now = Date.now();
      if (!rerollArm || rerollArm.cell !== cell.cell_key || now > rerollArm.until) {
        rerollArm = { cell: cell.cell_key, until: now + REROLL_CONFIRM_MS };
        toast(`R 을 한 번 더 누르면 ${what} — 유료`, "warn");
        return;
      }
    }
    rerollArm = null;
    // 미반영 레이아웃 편집은 이 큐의 Import 가 함께 반영한다 — 이 크롭이 그중 하나면 셀이 바뀔 수 있고(다른 대상 문구만 바뀌면 그대로)
    // 바뀌면 이 재굴림은 돌지 않는다
    const pend = S.crops.filter((c) => LAYOUT_STATUS[c.layout_status]);
    const mine = pend.some((c) => c.id === curTarget().crop_id);
    if (mine && !window.confirm(`이 크롭은 레이아웃이 바뀌어(미반영) 이 큐에서 셀이 바뀌면 재굴림이 실행되지 않습니다. `
      + `미반영 레이아웃 ${pend.length}건도 이 큐로 반영됩니다. 계속할까요?`)) return;
    const r = await write("reroll", { cell_key: cell.cell_key, count: 1 },
      (x) => `재굴림 +1 → ${x.variant || ""} 셀 대기 ${x.cell_pending} · 전체 대기 ${x.pending_calls}`
        + (pend.length && !mine ? ` · 미반영 레이아웃 ${pend.length}건 함께 반영` : ""));
    if (r) setTimeout(queueInComfy, 600);
  }

  function comfyConnected() {
    const op = window.opener;
    return !!op && !op.closed;
  }

  function queueInComfy(layout = false) {
    // layout = 편집기 "반영(큐)" — ComfyUI 탭은 Review 상류에 Import PSD 가 있을 때만 큐에 넣는다(아니면 그 탭이 알림)
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

  function drain() {
    if (!window.confirm("Drain: 진행 중인 Run 이 새 호출 발사를 멈춥니다.\n진행 중인 호출은 끝까지 받아 저장합니다. 계속할까요?")) return;
    write("drain", {}, (r) => (r.drain
      ? "Drain 요청 — 진행 중인 호출만 마저 받고 멈춥니다"
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

  function layoutReady() {
    // 서버가 S1 이전 버전이면 state 에 layout_rev 가 없다(라우트도 없음) — 편집 버튼을 끄고 재시작 안내
    return !!editor && !!S && "layout_rev" in S;
  }

  function setEditing(on) {
    if (on && !layoutReady()) {
      toast("ComfyUI 를 재시작해야 레이아웃 편집을 쓸 수 있습니다(서버가 옛 버전)", "warn");
      return;
    }
    editing = on;
    document.body.classList.toggle("editing", on);
    $("btn-edit").classList.toggle("on", on);
    if (on) {
      editor.enter(S);
    } else {
      editor.leave();
      syncViewer(false);
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
    if (S.drain) b.append(el("span", "badge b-drain", "Drain 요청됨"));
    if (S.pending_calls) {
      const pre = S.pending_preapproved ? ` (재굴림 승인 ${S.pending_preapproved})` : "";
      b.append(el("span", "badge b-wait", `대기 호출 ${S.pending_calls}${pre}`));
    }
    if (S.cost.calls) {
      const cb = el("span", "badge", `Run ${credits(S.cost.credits, S.cost.unknown)} · 호출 ${S.cost.calls}`);
      cb.title = `원장 calls 합계(완료 ${S.cost.done}) — Spec Draft 비용은 들어 있지 않음`;
      b.append(cb);
    }
    if (S.rerolls_total) b.append(el("span", "badge", `재굴림 누적 ${S.rerolls_total}`));
    if (S.orphan_candidates) b.append(el("span", "badge b-bad", `크롭 없는 후보 ${S.orphan_candidates}`));
    const on = comfyConnected();
    const cn = el("span", "badge " + (on ? "b-run" : "b-dim"), on ? "ComfyUI 탭 연결" : "ComfyUI 탭 없음");
    cn.title = on ? "재굴림 큐 요청을 이 창을 연 ComfyUI 탭으로 보냅니다"
      : "Review 노드의 Open Board 로 열지 않았거나 그 탭이 닫혔습니다 — 재굴림은 ComfyUI 탭에서 직접 큐에 넣어야 합니다";
    b.append(cn);
    $("btn-drain").disabled = !S.running;
    const eb = $("btn-edit");
    eb.disabled = !layoutReady();
    eb.title = layoutReady() ? "레이아웃 편집 (E) — 크롭 사각형·이름·대상을 Studio 에서"
      : "ComfyUI 를 재시작해야 레이아웃 편집을 쓸 수 있습니다(서버가 옛 버전)";
  }

  function targetTitle(t) {
    const label = t.label && t.label !== t.part ? t.label : "";
    return `${t.crop_name || t.crop_id} · ${t.tid}` + (label ? ` ${label}` : "");
  }

  function targetChips(ch, t) {
    const k = t.chips;
    ch.append(chip(`후보 ${k.n}`));
    if (k.picked) ch.append(chip(`★ ${k.picked}`, "b-star"));
    if (k.gate_fail) ch.append(chip(`게이트 ✕${k.gate_fail}`, "b-bad"));
    if (k.reframed) ch.append(chip(`reframed ${k.reframed}`, "b-reg"));
    if (k.mock) ch.append(chip(`MOCK ${k.mock}`, "b-mock"));
    if (t.pending_calls) ch.append(chip(`대기 ${t.pending_calls}`, "b-wait"));
    if (k.rejected) ch.append(chip(`탈락 ${k.rejected}`, "b-rej"));
  }

  function cropChips(ch, c) {
    if (LAYOUT_STATUS[c.layout_status]) ch.append(chip(LAYOUT_STATUS[c.layout_status], "b-wait"));
    if (c.calls) ch.append(chip(credits(c.credits, c.credits_unknown), "b-cost"));
    if (c.dup_picks.length) ch.append(chip(`중복 ★ ${c.dup_picks.length}`, "b-bad"));
    if (c.stale_picked.length) ch.append(chip("옛 크롭 ★", "b-bad"));
    const s = seamsByCrop.get(c.id);
    if (s) ch.append(chip(`이음매 ${s.n}`, s.bad ? "b-bad" : "b-wait"));
    if (c.warnings.length) {
      const w = chip(`경고 ${c.warnings.length}`, "b-bad");
      w.title = c.warnings.join("\n");
      ch.append(w);
    }
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
        if (single) cropChips(ch, c);
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
      caption = `후보 c${pad(c.n)}` + (c.picked ? (c.slice_index === 1 ? " ★" : ` slice ${c.slice_index}`) : "")
        + (c.rejected ? " · 탈락" : "") + (c.mock ? " · MOCK" : "") + (c.stale_rect ? " · 옛 크롭" : "");
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
    row(d, "작업", cells.length ? cells.join(" | ") : "없음 (Prepare 전 — 재굴림 불가)", cells.length ? "" : "warn");
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
    row(d, `c${pad(c.n)}`, `${c.label} · ${src}` + (c.src_size ? ` · ${c.src_size[0]}×${c.src_size[1]}` : ""));
    if (c.joint && c.joint.first && c.joint.first !== t.tid) {
      const r = row(d, "공동", `${c.joint.tids.join("·")} 를 한 번에 고친 결과 — ★ 는 ${c.joint.first} 에만 `, "warn");
      if (c.joint.first_key) rowButton(r, `${c.joint.first} 에 ★`, starOnFirst);
    }
    if (c.stale_rect) row(d, "옛 크롭", "크롭 rect 를 옮기기 전에 만든 후보 — 지금 크롭과 위치·크기가 다름", "warn");
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
      an += ` · 정합 ${c.reg.kind} ${Number(c.reg.disp || 0).toFixed(1)}px rot ${Number(c.reg.rot_deg || 0).toFixed(2)}°`;
    }
    row(d, "분석", an, cls);
  }

  function tileBadges(c) {
    const b = el("div", "tb");
    if (c.picked) b.append(el("span", "badge b-star", c.slice_index === 1 ? "★" : `S${c.slice_index}`));
    if (c.mock) b.append(el("span", "badge b-mock", "MOCK"));
    if (c.gates && c.gates.fails.length) b.append(el("span", "badge b-bad", "게이트"));
    if (c.analysis === "nosrc" && !c.hand_mask) b.append(el("span", "badge b-bad", "마스크 없음"));
    if (c.stale_rect) b.append(el("span", "badge b-bad", "옛 크롭"));
    if (c.reframed) b.append(el("span", "badge b-reg", "reframed"));
    if (c.rejected) b.append(el("span", "badge b-rej", "탈락"));
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
      tile.append(el("div", "cap", `c${pad(c.n)} · ${meta} · ${c.label}`));
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
      seamsByCrop.set(s.crop_id, v);
    }
    renderTargets();
  }

  function renderAll(follow) {
    renderHeader();
    renderTargets();
    renderCompare();
    renderDetail();
    renderGrid();
    if (viewer) viewer.setState(S);
    syncViewer(follow);
    setConn("");
  }

  // ── 입력 ────────────────────────────────────────────
  function typing(t) {
    return t instanceof Element && !!t.closest("input, textarea, select, [contenteditable]");
  }

  function onKeyDown(e) {
    if (typing(e.target)) return; // 입력칸(편집기 속성 패널) 포커스: 보드 단축키를 모두 무시 — Enter·Esc 는 입력칸이 처리
    const helpOpen = !$("help").hidden;
    if (editing && !helpOpen && editor.onKey(e)) {
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
    switch (e.code) {
      case "KeyE":
        if (!e.repeat) setEditing(!editing);
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
        if (!e.repeat) reroll(false);
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
    if (e.code === "KeyQ" && qHeld) {
      qHeld = false;
      renderCompare();
      setViewFlags({});
    }
  }

  function wireButtons() {
    const on = (id, fn) => $(id).addEventListener("click", (e) => {
      e.currentTarget.blur();
      fn();
    });
    on("btn-star", setStar);
    on("btn-slice", toggleSlice);
    on("btn-reject", toggleReject);
    on("btn-reroll", () => reroll(true));
    on("btn-queue", queueInComfy);
    on("btn-drain", drain);
    on("btn-viewer", () => toggleViewer());
    on("btn-help", () => help());
    on("btn-edit", () => setEditing(!editing));
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
    viewer = window.DPViewer.create($("viewer"), { apiUrl, onPick: pickCrop, onSeams });
    if (window.DPLayoutEdit) {
      editor = window.DPLayoutEdit.create({ list: $("layout-list"), panel: $("layout-panel"), head: $("layout-head") },
        { apiUrl, root: ROOT, viewer, toast, queue: queueInComfy, exit: () => setEditing(false) });
    }
    wireButtons();
    document.addEventListener("keydown", onKeyDown);
    document.addEventListener("keyup", onKeyUp);
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
    schedule();
  }

  main();
})();
