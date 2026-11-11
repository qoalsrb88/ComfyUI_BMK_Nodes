// 공통 레이어 행. 이름·칩의 DOM 순서는 유지하고 끝에 한 덩어리만 붙인다.
"use strict";

window.DPRows = (() => {
  const NS = "http://www.w3.org/2000/svg";
  /* 아이콘 경로: Lucide 1.72.0 (ISC License, Copyright (c) 2026 Lucide Icons and Contributors).
     일부는 Feather(MIT License, Copyright (c) 2013-present Cole Bemis)에서 온 것.
     전문: web_design_patch/LICENSE-lucide.txt */
  const paths = {
    locked: [
      ["rect", { width: "18", height: "11", x: "3", y: "11", rx: "2", ry: "2" }],
      ["path", { d: "M7 11V7a5 5 0 0 1 10 0v4" }],
    ],
    unlocked: [
      ["rect", { width: "18", height: "11", x: "3", y: "11", rx: "2", ry: "2" }],
      ["path", { d: "M7 11V7a5 5 0 0 1 9.9-1" }],
    ],
    trash: [
      ["path", { d: "M10 11v6" }],
      ["path", { d: "M14 11v6" }],
      ["path", { d: "M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6" }],
      ["path", { d: "M3 6h18" }],
      ["path", { d: "M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" }],
    ],
    grip: [
      ["circle", { cx: "9", cy: "12", r: "1" }],
      ["circle", { cx: "9", cy: "5", r: "1" }],
      ["circle", { cx: "9", cy: "19", r: "1" }],
      ["circle", { cx: "15", cy: "12", r: "1" }],
      ["circle", { cx: "15", cy: "5", r: "1" }],
      ["circle", { cx: "15", cy: "19", r: "1" }],
    ],
    up: [["path", { d: "m18 15-6-6-6 6" }]],
    down: [["path", { d: "m6 9 6 6 6-6" }]],
    chevron: [["path", { d: "m9 18 6-6-6-6" }]],
    viewer: [
      ["rect", { width: "18", height: "18", x: "3", y: "3", rx: "2" }],
      ["path", { d: "M12 3v18" }],
    ],
    keys: [
      ["path", { d: "M10 8h.01" }],
      ["path", { d: "M12 12h.01" }],
      ["path", { d: "M14 8h.01" }],
      ["path", { d: "M16 12h.01" }],
      ["path", { d: "M18 8h.01" }],
      ["path", { d: "M6 8h.01" }],
      ["path", { d: "M7 16h10" }],
      ["path", { d: "M8 12h.01" }],
      ["rect", { width: "20", height: "16", x: "2", y: "4", rx: "2" }],
    ],
    undo: [
      ["path", { d: "M9 14 4 9l5-5" }],
      ["path", { d: "M4 9h10.5a5.5 5.5 0 0 1 5.5 5.5a5.5 5.5 0 0 1-5.5 5.5H11" }],
    ],
    redo: [
      ["path", { d: "m15 14 5-5-5-5" }],
      ["path", { d: "M20 9H9.5A5.5 5.5 0 0 0 4 14.5A5.5 5.5 0 0 0 9.5 20H13" }],
    ],
    clear: [
      ["path", { d: "M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" }],
      ["path", { d: "M3 3v5h5" }],
    ],
    xform: [
      ["path", { d: "M12 2v20" }],
      ["path", { d: "m15 19-3 3-3-3" }],
      ["path", { d: "m19 9 3 3-3 3" }],
      ["path", { d: "M2 12h20" }],
      ["path", { d: "m5 9-3 3 3 3" }],
      ["path", { d: "m9 5 3-3 3 3" }],
    ],
    invert: [
      ["circle", { cx: "12", cy: "12", r: "10" }],
      ["path", { d: "M12 18a6 6 0 0 0 0-12v12z" }],
    ],
    fade: [
      ["circle", { cx: "15", cy: "9", r: "7" }],
      ["circle", { cx: "9", cy: "15", r: "7" }],
    ],
    brush: [
      ["path", { d: "m11 10 3 3" }],
      ["path", { d: "M6.5 21A3.5 3.5 0 1 0 3 17.5a2.62 2.62 0 0 1-.708 1.792A1 1 0 0 0 3 21z" }],
      ["path", { d: "M9.969 17.031 21.378 5.624a1 1 0 0 0-3.002-3.002L6.967 14.031" }],
    ],
    dot: [["circle", { cx: "12", cy: "12", r: "1" }]],
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
    for (const [k, v] of Object.entries({ viewBox: "0 0 24 24", width: 16, height: 16, fill: "none", stroke: "currentColor",
      "stroke-width": 1.5, "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true", class: "dp-ico", "data-icon": name })) s.setAttribute(k, v);
    const elements = Object.prototype.hasOwnProperty.call(paths, name) ? paths[name] : [];
    for (const [tag, attrs] of elements) {
      const p = document.createElementNS(NS, tag);
      for (const [k, v] of Object.entries(attrs)) p.setAttribute(k, v);
      s.append(p);
    }
    return s;
  }

  if (typeof document.querySelector === "function") {
    for (const [selector, name] of [["#btn-viewer", "viewer"], ["#btn-keys", "keys"], ["#export-menu > summary", "down"]]) {
      const b = document.querySelector(selector);
      if (b && !b.querySelector("svg")) b.prepend(icon(name));
    }
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
    if (moving || blocked() || !config.move || !anchor || (side !== "step" && item === anchor)) return;
    moving = true;
    try {
      const st = config.state();
      const d = await config.move({ scope: "crops", op: "move", item, [side]: anchor, seen: st.rev });
      if (!d || !d.ok) return;
      st.z = { crops: d.effective, manual: !!(d.order && d.order.length) };
      movedZ = { rev: d.rev, pref: st.pref_v, z: st.z };
      if (d.moved !== false) tell(d.changed === false ? "겹치지 않는 크롭끼리는 결과가 같아 순서를 저장하지 않음(나중에 겹쳐도 이 순서는 기억 안 됨)" : "겹침 순서 적용 — 이음매를 다시 잽니다");
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
    const i = ids.indexOf(c.id);
    const canRemove = !opts.remove && config.remove && opts.canLock !== false;
    if (!canRemove && i < 0) return;
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
    if (canRemove) add("크롭 편집에서 지우기", () => config.remove(c.id), blocked() || c.locked);
    if (i >= 0) {
      add("위로", () => move(c.id, "up", "step"), blocked() || i === ids.length - 1);
      add("아래로", () => move(c.id, "down", "step"), blocked() || i === 0);
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
        const request = b.lockRequest = (b.lockRequest || 0) + 1;
        b.setAttribute("aria-pressed", String(next));
        b.replaceChildren(icon(next ? "locked" : "unlocked"));
        try { await (opts.lock || config.lock)([c.id], next); }
        catch (err) {
          if (b.lockRequest === request) {
            b.setAttribute("aria-pressed", String(!next));
            b.replaceChildren(icon(next ? "unlocked" : "locked"));
          }
          throw err;
        }
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
      controls.append(grip, button("up", "위로", () => move(c.id, "up", "step"), at === ids.length - 1),
        button("down", "아래로", () => move(c.id, "down", "step"), at === 0));
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
  document.addEventListener("keydown", (e) => {
    if (!menu || e.code !== "Escape") return;
    e.stopPropagation();
    e.preventDefault();
    closeMenu();
  }, true);
  window.addEventListener("blur", () => { closeMenu(); release(); });
  return { tail, configure, order, defer, ensureZ, blocked, icon };
})();
