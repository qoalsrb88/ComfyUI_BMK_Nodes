// BMK Design Patch Studio — 자체 대화 상자(DPDialog). Forge iframe 은 sandbox 에 allow-modals 가 없어 네이티브 confirm()·alert() 가
// 뜨지 않고 바로 반환한다 → 확인 창을 페이지 안 상자로 띄운다(로컬판도 같은 상자 — 코드 한 벌).
// 계약: _proto/STUDIO_FORGE_F1_SPEC.md §3.4. 모든 문자열은 textContent 로만 넣는다.
// 공용 자리(감독관) — 지금은 네이티브 창으로 넘기는 최소 구현. 페이지 상자 구현은 F1 갈래 W.
"use strict";

window.DPDialog = (() => {
  const text = (t) => (Array.isArray(t) ? t.join("\n") : String(t));

  return {
    // 확인 창 → Promise<boolean>. t = 문자열 또는 줄 목록
    confirm(t) {
      return Promise.resolve(window.confirm(text(t)));
    },
    // 알림 창 → Promise<void>
    alert(t) {
      window.alert(text(t));
      return Promise.resolve();
    },
    // 네이티브 confirm 그대로(동기) — 로컬판의 유료 동의 자리만 쓴다(D328: 로컬판 유료 경로 불변)
    native(t) {
      return window.confirm(text(t));
    },
  };
})();
