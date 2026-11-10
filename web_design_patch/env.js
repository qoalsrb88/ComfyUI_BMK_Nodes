// BMK Design Patch Studio — 실행 환경(DPEnv). 로컬판(ComfyUI 커스텀 노드)과 서버 모드(Forge iframe — 독립 서버)를 한 코드로 가른다.
// 계약: _proto/STUDIO_FORGE_F1_SPEC.md §3.4 · 응답 모양 tools/fixtures/server_api.json.
// 이 파일은 다른 스크립트보다 먼저 실행된다(board.html 의 첫 defer). 모든 문자열은 textContent 로만 넣는다.
// 공용 자리(감독관) — 지금은 로컬 동작만: ready 는 곧바로 끝나고 store 는 localStorage 그대로. 서버 모드 구현은 F1 갈래 W.
"use strict";

window.DPEnv = (() => {
  // 로컬판의 기능 표(bmk_design_patch_board.LOCAL_CAPS 와 같음 — GET ../env 가 404 인 옛 로컬 서버도 이 표). 서버 전용 둘은 false
  const LOCAL_CAPS = { jobs: false, export_download: false, sam3: true, local_qwen: true, external: true, open_folder: true,
    path_input: true, ps_mask: true, agent_approve: true };
  const mem = new Map(); // localStorage 를 못 쓸 때(막힘·없음)의 이 창 한정 저장

  const local = {
    // localStorage 와 같은 세 메서드(값은 문자열). 저장소가 막히면 이 창에서만 기억한다
    getItem(key) {
      try {
        return window.localStorage.getItem(key);
      } catch (e) {
        return mem.has(key) ? mem.get(key) : null;
      }
    },
    setItem(key, value) {
      try {
        window.localStorage.setItem(key, String(value));
      } catch (e) {
        mem.set(key, String(value));
      }
    },
    removeItem(key) {
      try {
        window.localStorage.removeItem(key);
      } catch (e) {
        mem.delete(key);
      }
    },
  };

  const env = {
    serverMode: false, // GET ../env 의 server_mode
    caps: Object.assign({}, LOCAL_CAPS), // 기능 표 — 로컬은 LOCAL_CAPS, 서버 모드는 서버가 준 caps
    account: null, // 서버 모드 {name, tag}
    backend: null, // 서버 모드 생성 백엔드 이름("mock" …)
    store: local, // 화면 설정 저장소(로컬 = localStorage, 서버 모드 = 계정별 서버 저장)
    ready: Promise.resolve(), // 환경을 다 읽은 뒤 끝남(서버 모드에서 세션이 없으면 끝나지 않고 안내를 띄움)
    can(name) {
      // 그 기능을 쓸 수 있나 — caps[name] 이 true 일 때만
      return env.caps[name] === true;
    },
  };
  return env;
})();
