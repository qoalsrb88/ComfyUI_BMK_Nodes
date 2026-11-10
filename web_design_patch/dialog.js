// BMK Design Patch Studio — 자체 대화 상자(DPDialog). Forge iframe 은 sandbox 에 allow-modals 가 없어 네이티브 confirm()·alert() 가
// 뜨지 않고 바로 반환한다 → 확인 창을 페이지 안 상자로 띄운다(로컬판도 같은 상자 — 코드 한 벌).
// 계약: _proto/STUDIO_FORGE_F1_SPEC.md §3.4. 모든 문자열은 textContent 로만 넣는다.
"use strict";

window.DPDialog = (() => {
  const text = (t) => (Array.isArray(t) ? t.join("\n") : String(t));
  let active = null;
  const blocked = () => [...document.body.children].some((child) => child.classList.contains("dp-env-block"));
  function capture(e) {
    if (!active) return;
    if (e.type !== "keyup") e.stopImmediatePropagation();
    // 유료 상자의 버튼 활성화 키는 보조키가 붙어도 소유자의 승인 차단을 거친다.
    if (active.keys && ["Enter", " ", "Spacebar"].includes(e.key)) { active.keys(e); return; }
    if (e.ctrlKey || e.metaKey || e.altKey || /^F(?:[1-9]|1[0-2])$/.test(e.key)) return;
    if (["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "PageUp", "PageDown", "Home", "End", "Backspace"].includes(e.key)) e.preventDefault();
    // 기존 mock·Claude 상자의 키 동작은 소유자가 맡는다(키보드 승인 차단·Esc 취소).
    if (active.keys) { active.keys(e); return; }
    if (["Tab", "Enter", " ", "Spacebar", "Escape"].includes(e.key)) e.preventDefault();
    if (e.type !== "keydown" || !e.isTrusted || !active.finish) return;
    const buttons = active.buttons, index = buttons.indexOf(document.activeElement);
    if (e.key === "Escape") active.finish(false);
    else if (e.key === "Tab") buttons[(index + (e.shiftKey ? buttons.length - 1 : 1)) % buttons.length].focus();
    else if (e.key === "Enter" || e.key === " " || e.key === "Spacebar") {
      if (index >= 0) active.finish(index === 0);
    }
  }
  function lock(options = {}) {
    if (active || blocked()) return false;
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
    if (saved.keepInert || blocked()) return;
    for (const [child, inert] of saved.inert) child.inert = inert;
    if (saved.before && saved.before.isConnected !== false) saved.before.focus();
  }
  function cancel() {
    if (!active) return;
    if (active.finish) active.finish(false);
    else active.keepInert = true; // 외부 상자의 닫기는 소유자가 맡되 세션 차단은 풀지 않는다.
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
    lock, unlock, cancel,
    get open() { return active !== null; },
  };
})();
