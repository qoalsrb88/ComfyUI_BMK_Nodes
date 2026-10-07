// BMK Design Patch Review Board — vanilla JS, 빌드·외부 리소스 없음.
// 서버: bmk_design_patch_board.py (/bmk/design_patch/*). 모든 문자열(PSD 레이어명 등)은 textContent 로만 넣는다.
// 보드는 매니페스트(★·slice·탈락·재굴림 의도)만 고친다. 유료 호출은 opener(ComfyUI 탭)가 Review 노드를 큐에 넣을 때만.
"use strict";

(() => {
  const API = new URL("../", location.href); // /bmk/design_patch/ (또는 /api/bmk/design_patch/)
  const ROOT = new URLSearchParams(location.search).get("root") || "";
  const POLL_MS = 2000;
  const POLL_HIDDEN_MS = 10000;
  const REROLL_CONFIRM_MS = 2500;
  const GATE_NAMES = { outside_mad: "마스크 밖 MAD", outside_frac_gt24: "마스크 밖 큰 차이", black_band: "검은 띠" };

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
        renderHeader();
      } else if (!d.unchanged) {
        S = d;
        reconcile();
        renderAll();
      }
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
    renderAll();
  }

  function selectCand(key) {
    cKey = key;
    rerollArm = null;
    renderTargets();
    renderCompare();
    renderDetail();
    renderGrid();
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
    write("pick", { crop_id: t.crop_id, tid: t.tid, key: c.key, mode: "set" }, `★ c${pad(c.n)} — ${t.key}`);
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
    const r = await write("reroll", { cell_key: cell.cell_key, count: 1 },
      (x) => `재굴림 +1 → ${x.variant || ""} 셀 대기 ${x.cell_pending} · 전체 대기 ${x.pending_calls}`);
    if (r) setTimeout(queueInComfy, 600);
  }

  function queueInComfy() {
    const op = window.opener;
    if (op && !op.closed) {
      try {
        op.postMessage({ type: "bmk-dp-queue", rootKey: ROOT }, location.origin);
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
    document.title = `${S.project} · Review Board`;
    $("proj").textContent = `${S.project} · 타깃 ${S.counts.targets} · 후보 ${S.counts.candidates} · pick ${S.counts.picks}`;
    const b = $("badges");
    b.replaceChildren();
    if (S.backend_mock) b.append(el("span", "badge b-mock", "MOCK 백엔드 (과금 없음)"));
    if (S.running) b.append(el("span", "badge b-run", "Run 진행 중"));
    if (S.drain) b.append(el("span", "badge b-drain", "Drain 요청됨"));
    if (S.pending_calls) {
      const pre = S.pending_preapproved ? ` (재굴림 승인 ${S.pending_preapproved})` : "";
      b.append(el("span", "badge b-wait", `대기 호출 ${S.pending_calls}${pre}`));
    }
    if (S.rerolls_total) b.append(el("span", "badge", `재굴림 누적 ${S.rerolls_total}`));
    if (S.orphan_candidates) b.append(el("span", "badge b-bad", `크롭 없는 후보 ${S.orphan_candidates}`));
    $("btn-drain").disabled = !S.running;
  }

  function targetTitle(t) {
    const label = t.label && t.label !== t.part ? t.label : "";
    return `${t.crop_name || t.crop_id} · ${t.tid}` + (label ? ` ${label}` : "");
  }

  function renderTargets() {
    const ol = $("target-list");
    ol.replaceChildren();
    if (!S) return;
    for (const t of S.targets) {
      const li = el("li", (t.key === tKey ? "sel " : "") + (t.candidates.length ? "" : "empty"));
      li.append(el("div", "tname", targetTitle(t)));
      if (t.name_en) li.append(el("div", "tsub", t.name_en));
      const ch = el("div", "chips");
      const k = t.chips;
      ch.append(chip(`후보 ${k.n}`));
      if (k.picked) ch.append(chip(`★ ${k.picked}`, "b-star"));
      if (k.gate_fail) ch.append(chip(`게이트 ✕${k.gate_fail}`, "b-bad"));
      if (k.reframed) ch.append(chip(`reframed ${k.reframed}`, "b-reg"));
      if (k.mock) ch.append(chip(`MOCK ${k.mock}`, "b-mock"));
      if (t.pending_calls) ch.append(chip(`대기 ${t.pending_calls}`, "b-wait"));
      if (k.rejected) ch.append(chip(`탈락 ${k.rejected}`, "b-rej"));
      li.append(ch);
      li.addEventListener("click", () => selectTarget(t.key));
      ol.append(li);
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
        + (c.rejected ? " · 탈락" : "") + (c.mock ? " · MOCK" : "");
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
    const [w, h] = t.size;
    row(d, "타깃", `${targetTitle(t)} · ${w}×${h}` + (t.name_en ? ` · ${t.name_en}` : "")
      + ` · 초점 [${t.focus_box.join(", ")}]`);
    const cells = t.cells.map((x) => `${x.variant || "?"} ${x.reps}+${x.rerolls}회 대기 ${x.pending}${usd(x)}`);
    row(d, "작업", cells.length ? cells.join(" | ") : "없음 (Prepare 전 — 재굴림 불가)", cells.length ? "" : "warn");
    if (!c) {
      row(d, "후보", "없음");
      return;
    }
    const src = [c.origin, c.backend, c.variant, c.rep !== null && c.rep !== undefined ? `r${c.rep}` : null]
      .filter((x) => x !== null && x !== undefined && x !== "").join(" · ");
    row(d, `c${pad(c.n)}`, `${c.label} · ${src}` + (c.src_size ? ` · ${c.src_size[0]}×${c.src_size[1]}` : ""));
    let an = "분석 없음 (Analyze 전)";
    let cls = "";
    if (c.analysis === "skipped") an = "분석 생략(부분 패치)";
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

  function renderAll() {
    renderHeader();
    renderTargets();
    renderCompare();
    renderDetail();
    renderGrid();
    setConn("");
  }

  // ── 입력 ────────────────────────────────────────────
  function onKeyDown(e) {
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    const helpOpen = !$("help").hidden;
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
    switch (e.code) {
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
      case "Escape":
        view = "cand";
        renderCompare();
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
    on("btn-help", () => help());
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
    wireButtons();
    document.addEventListener("keydown", onKeyDown);
    document.addEventListener("keyup", onKeyUp);
    window.addEventListener("blur", () => {
      if (qHeld) {
        qHeld = false;
        renderCompare();
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
