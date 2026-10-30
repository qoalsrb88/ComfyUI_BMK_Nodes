// 공통 레이어 행. 이름·칩의 DOM 순서는 유지하고 끝에 한 덩어리만 붙인다.
"use strict";

window.DPRows = (() => {
  const NS = "http://www.w3.org/2000/svg";
  const paths = {
    locked: "M6 10V7a6 6 0 0 1 12 0v3M4 10h16v12H4zM12 14v4",
    unlocked: "M6 10V7a6 6 0 0 1 11-3M4 10h16v12H4zM12 14v4",
    trash: "M3 5h18M9 5V2h6v3M6 5l1 17h10l1-17M10 9v9M14 9v9",
    grip: "M8 4h1M15 4h1M8 9h1M15 9h1M8 14h1M15 14h1M8 19h1M15 19h1",
    up: "M5 15l7-7 7 7", down: "M5 9l7 7 7-7",
  };
  let config = {};
  let dragging = null;
  let moving = false;
  let menu = null;
  const deferred = new Map();
  let zRefresh = { sig: null, time: 0 };
  let movedZ = null;
  let watch = null;

  function el(tag, cls) {
    const e = document.createElement(tag);
    e.className = cls || "";
    return e;
  }

  function icon(name) {
    const s = document.createElementNS(NS, "svg");
    for (const [k, v] of Object.entries({ viewBox: "0 0 24 24", width: 16, height: 16, fill: "none", stroke: "currentColor", "stroke-width": 1.7, "aria-hidden": "true" })) s.setAttribute(k, v);
    const p = document.createElementNS(NS, "path");
    p.setAttribute("d", paths[name]);
    s.append(p);
    return s;
  }

  function blocked() { return !!(config.busy && config.busy()); }
  function tell(text, kind) { if (config.toast) config.toast(text, kind); }

  function button(name, title, fn, disabled) {
    const b = el("button", "dr-button");
    b.type = "button";
    b.title = title;
    b.setAttribute("aria-label", title);
    b.dataset.fixedDisabled = disabled ? "1" : "";
    b.disabled = !!disabled || blocked();
    b.append(icon(name));
    b.addEventListener("click", (e) => {
      e.stopPropagation();
      if (blocked()) return;
      Promise.resolve().then(fn).catch((err) => tell(err.message, "err"));
    });
    return b;
  }

  function defer(list, render) {
    if (!dragging && !moving) return false;
    deferred.set(list, render);
    return true;
  }

  function release() {
    dragging = null;
    if (moving) return;
    const renders = [...deferred.values()];
    deferred.clear();
    for (const render of renders) render();
  }

  function order(st) {
    const cs = st.crops || [];
    if (!st.z && movedZ && movedZ.rev === st.rev && movedZ.pref === st.pref_v) st.z = movedZ.z;
    if (!st.z || !Array.isArray(st.z.crops)) return cs;
    const ranks = new Map(st.z.crops.map((id, i) => [id, i]));
    return cs.slice().sort((a, b) => {
      const az = ranks.get(a.id), bz = ranks.get(b.id);
      if (az !== undefined || bz !== undefined) return (bz ?? -1) - (az ?? -1);
      return (a.name || a.id).localeCompare(b.name || b.id, undefined, { numeric: true });
    });
  }

  function ensureZ(st) {
    // 합성 완료 콜백 뒤에는 전체 state 를 다시 받아 새 JSON 캐시를 반영한다.
    const sig = st.canvas && st.canvas.composite_sig;
    if (!st.z && config.refresh && (sig !== zRefresh.sig || Date.now() - zRefresh.time > 1500)) {
      zRefresh = { sig, time: Date.now() };
      config.refresh();
    }
  }

  async function move(item, anchor, side) {
    if (moving || blocked() || !config.move || !anchor || item === anchor) return;
    moving = true;
    try {
      const st = config.state();
      const d = await config.move({ scope: "crops", op: "move", item, [side]: anchor, seen: st.rev });
      if (!d || !d.ok) return;
      st.z = { crops: d.effective, manual: !!(d.order && d.order.length) };
      st.rev = d.rev;
      movedZ = { rev: d.rev, pref: st.pref_v, z: st.z };
      tell(d.changed === false ? "겹치지 않는 크롭끼리는 결과가 같아 순서를 저장하지 않음(나중에 겹쳐도 이 순서는 기억 안 됨)" : "겹침 순서 적용 — 이음매를 다시 잽니다");
      await config.refresh();
    } catch (err) {
      tell(err.message, "err");
      if (config.refresh) await config.refresh();
    } finally {
      moving = false;
      release();
    }
  }

  function closeMenu() { if (menu) menu.remove(); menu = null; }

  function context(e, c, opts, ids) {
    e.preventDefault();
    e.stopPropagation();
    closeMenu();
    menu = el("div", "dr-menu");
    menu.setAttribute("role", "menu");
    const add = (label, fn, disabled) => {
      const b = el("button");
      b.type = "button";
      b.textContent = label;
      b.disabled = !!disabled;
      b.addEventListener("click", () => {
        closeMenu();
        if (!blocked()) Promise.resolve().then(fn).catch((err) => tell(err.message, "err"));
      });
      menu.append(b);
    };
    if (!opts.remove && config.remove && opts.canLock !== false) add("크롭 편집에서 지우기", () => config.remove(c.id), blocked() || c.locked);
    const i = ids.indexOf(c.id);
    if (i >= 0) {
      add("위로", () => move(c.id, ids[i + 1], "after"), blocked() || i === ids.length - 1);
      add("아래로", () => move(c.id, ids[i - 1], "before"), blocked() || i === 0);
      add("맨 위로", () => move(c.id, ids[ids.length - 1], "after"), blocked() || i === ids.length - 1);
      add("맨 아래로", () => move(c.id, ids[0], "before"), blocked() || i === 0);
    }
    Object.assign(menu.style, { left: `${e.clientX}px`, top: `${e.clientY}px` });
    document.body.append(menu);
    const r = menu.getBoundingClientRect();
    menu.style.left = `${Math.max(0, Math.min(e.clientX, window.innerWidth - r.width))}px`;
    menu.style.top = `${Math.max(0, Math.min(e.clientY, window.innerHeight - r.height))}px`;
  }

  function tail(li, opts) {
    const st = opts.state || (config.state && config.state()) || {};
    const c = opts.crop;
    const stored = (st.crops || []).concat(st.layout_added || []).find((x) => x.id === c.id) || c;
    const locked = opts.locked === undefined ? !!stored.locked : !!opts.locked;
    const available = opts.canLock === undefined ? st.layout_rev != null && stored.layout_status !== "removed" : opts.canLock;
    const ids = opts.z && st.z ? st.z.crops : [];
    const at = ids.indexOf(c.id);
    const wrap = el("span", "dr-tail");
    const thumb = el("span", "dr-thumb");
    const cv = st.canvas, r = c.rect || (stored.layout_diff && stored.layout_diff.rect) || stored.rect;
    if (cv && r && r[2] > r[0] && r[3] > r[1]) {
      const [W, H] = cv.size, w = r[2] - r[0], h = r[3] - r[1];
      const api = opts.apiUrl || config.apiUrl;
      Object.assign(thumb.style, { backgroundImage: `url("${api(cv.base_url)}")`, backgroundSize: `${100 * W / w}% ${100 * H / h}%`,
        width: `${Math.min(40, 40 * w / h)}px`, height: `${Math.min(40, 40 * h / w)}px`,
        backgroundPosition: `${W > w ? 100 * r[0] / (W - w) : 0}% ${H > h ? 100 * r[1] / (H - h) : 0}%` });
    }
    const controls = el("span", "dr-controls");
    if (available && (opts.lock || config.lock)) controls.append(button(locked ? "locked" : "unlocked", locked ? "잠금 풀기 — 레이아웃만" : "잠금 — 레이아웃만",
      async () => {
        const b = controls.children[0];
        const next = b.getAttribute("aria-pressed") !== "true";
        b.setAttribute("aria-pressed", String(next));
        b.replaceChildren(icon(next ? "locked" : "unlocked"));
        try { await (opts.lock || config.lock)([c.id], next); }
        finally { if (config.refresh) await config.refresh(); }
      }, false));
    if (available && controls.children[0]) controls.children[0].setAttribute("aria-pressed", String(locked));
    if (opts.remove) controls.append(button("trash", "크롭 지우기", opts.remove, locked));
    if (at >= 0) {
      const grip = button("grip", "겹침 순서 끌기 — 위가 앞", () => {}, false);
      grip.draggable = true;
      grip.addEventListener("dragstart", (e) => {
        if (blocked() || moving) { e.preventDefault(); return; }
        dragging = { id: c.id, list: li.parentNode };
        e.dataTransfer.setData("text/plain", c.id);
        e.dataTransfer.effectAllowed = "move";
      });
      grip.addEventListener("dragend", release);
      li.addEventListener("dragover", (e) => {
        if (dragging && dragging.list === li.parentNode && dragging.id !== c.id) e.preventDefault();
      });
      li.addEventListener("drop", (e) => {
        if (!dragging || dragging.list !== li.parentNode) return;
        e.preventDefault();
        e.stopPropagation();
        const r = li.getBoundingClientRect();
        move(dragging.id, c.id, e.clientY < r.top + r.height / 2 ? "after" : "before");
        release();
      });
      controls.append(grip, button("up", "위로", () => move(c.id, ids[at + 1], "after"), at === ids.length - 1),
        button("down", "아래로", () => move(c.id, ids[at - 1], "before"), at === 0));
    }
    if (opts.z && !st.z) {
      const note = el("span", "dr-unknown");
      note.textContent = "겹침 순서 계산 전";
      controls.append(note);
    }
    wrap.append(thumb, controls);
    li.classList.add("dr-row");
    li.append(wrap);
    li.addEventListener("contextmenu", (e) => context(e, Object.assign({}, c, { locked }), Object.assign({}, opts, { canLock: available }), ids));
  }

  function configure(opts) {
    config = opts;
    // job·승인 대기는 매니페스트 rev 없이도 바뀌므로 DOM 교체 없이 단추만 갱신한다.
    if (watch !== null) clearInterval(watch);
    watch = setInterval(() => {
      const off = blocked();
      for (const b of document.querySelectorAll(".dr-button")) b.disabled = off || b.dataset.fixedDisabled === "1";
    }, 250);
  }
  document.addEventListener("pointerdown", (e) => { if (menu && !menu.contains(e.target)) closeMenu(); });
  document.addEventListener("keydown", (e) => { if (e.code === "Escape") closeMenu(); });
  window.addEventListener("blur", () => { closeMenu(); release(); });
  return { tail, configure, order, defer, ensureZ, blocked };
})();
