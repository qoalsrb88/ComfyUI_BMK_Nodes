// BMK Design Patch Studio — 자체 대화 상자(DPDialog). Forge iframe 은 sandbox 에 allow-modals 가 없어 네이티브 confirm()·alert() 가
// 뜨지 않고 바로 반환한다 → 확인 창을 페이지 안 상자로 띄운다(로컬판도 같은 상자 — 코드 한 벌).
// 계약: _proto/STUDIO_FORGE_F1_SPEC.md §3.4. 모든 문자열은 textContent 로만 넣는다.
"use strict";

window.DPDialog = (() => {
  const text = (t) => (Array.isArray(t) ? t.join("\n") : String(t));
  let active = null;
  function capture(e) {
    if (!active) return;
    e.stopImmediatePropagation();
    // 기존 mock·Claude 상자의 키 동작은 전달받은 처리기가 맡는다.
    if (active.keys) { active.keys(e); return; }
    e.preventDefault();
    if (e.type !== "keydown" || !e.isTrusted || !active.finish) return;
    const buttons = active.buttons, index = buttons.indexOf(document.activeElement);
    if (e.key === "Escape") active.finish(false);
    else if (e.key === "Tab") buttons[(index + (e.shiftKey ? buttons.length - 1 : 1)) % buttons.length].focus();
    else if (e.key === "Enter" || e.key === " " || e.key === "Spacebar") {
      if (index >= 0) active.finish(index === 0);
    }
  }
  function lock(options = {}) {
    if (active) return false;
    active = { before: document.activeElement, inert: [], keys: options.keys, finish: null, buttons: [] };
    for (const child of document.body.children) {
      active.inert.push([child, child.inert]); child.inert = true;
    }
    for (const type of ["keydown", "keyup", "keypress"]) window.addEventListener(type, capture, true);
    return true;
  }
  function unlock() {
    if (!active) return;
    const saved = active; active = null;
    for (const type of ["keydown", "keyup", "keypress"]) window.removeEventListener(type, capture, true);
    for (const [child, inert] of saved.inert) child.inert = inert;
    if (saved.before && saved.before.isConnected !== false) saved.before.focus();
  }
  function show(t, options, alert) {
    if (!lock()) return Promise.resolve(false);
    return new Promise((resolve) => {
      const overlay = document.createElement("div"), sheet = document.createElement("div"), row = document.createElement("div");
      overlay.className = "overlay dp-dialog"; sheet.className = "sheet"; row.className = "lp-btns";
      sheet.setAttribute("role", alert ? "alertdialog" : "dialog"); sheet.setAttribute("aria-modal", "true");
      sheet.setAttribute("aria-label", alert ? "알림" : "확인");
      for (const line of text(t).split("\n")) {
        const p = document.createElement("p"); p.textContent = line || "\u00a0"; sheet.appendChild(p);
      }
      const finish = (ok) => { overlay.remove(); unlock(); resolve(ok); };
      active.finish = finish;
      for (const [label, value] of [[options.ok || "확인", true], ...(alert ? [] : [[options.cancel || "취소", false]])]) {
        const button = document.createElement("button"); button.textContent = label;
        if (value && options.danger) button.className = "danger";
        button.onclick = (e) => { if (e.isTrusted) finish(value); };
        row.appendChild(button); active.buttons.push(button);
      }
      sheet.appendChild(row); overlay.appendChild(sheet); document.body.appendChild(overlay);
      active.buttons[options.danger && !alert ? 1 : 0].focus();
    });
  }

  return {
    // 확인 창 → Promise<boolean>. t = 문자열 또는 줄 목록
    confirm(t, options = {}) {
      return show(t, options, false);
    },
    // 알림 창 → Promise<void>
    alert(t) {
      return show(t, {}, true).then(() => {});
    },
    // 네이티브 confirm 그대로(동기) — 로컬판의 유료 동의 자리만 쓴다(D328: 로컬판 유료 경로 불변)
    native(t) {
      return window.confirm(text(t));
    },
    lock, unlock,
    get open() { return active !== null; },
  };
})();
