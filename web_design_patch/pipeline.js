// BMK Design Patch Studio — 파이프라인 조작(STUDIO_S3_SPEC §3–§5). board.js 가 진행 모드(G)에서 켜고 끈다(머리줄 한 줄은 모든 모드).
// 무료(S3a): 실행 설정(pipeline.json)·무료 템플릿 큐(반영 = apply, Export — 서버가 만든 템플릿을 ComfyUI /api/prompt 로 직접 넣음, 탭 불필요)·
// 계획/비용 미리보기(Run·초안 해시까지)·진행·★ 점검·Export 결과·제안 채택/수정.
// 유료(S3b — 계약 _proto/s3b/S3B_CONTRACT.md §5): 초안·Run 승인·재굴림은 ComfyUI 탭에 승인 메시지(stage·hash·only)만 보내고 탭이 서버 템플릿을
// app.queuePrompt 로 낸다(토큰은 탭에서만 — D5). 초안·Run 승인은 사용자 클릭(isTrusted) + 확인 창(mock = 페이지 상자, 아니면 네이티브 confirm — D7),
// 재굴림(reroll — board.js queueReroll)은 확인 창·isTrusted 없음: 동의는 R 의 확인(사전 승인 — 그 재굴림·복구만 과금)과 머리줄 클릭.
// /api/prompt 본문을 만드는 곳은 freeBody 하나(서버가 준 템플릿 prompt + front — 인증·탭 정보 없음)이고, 보내기 전에 checkPrompt 로 본다.
// 왼쪽 = 크롭 목록 + 선택 체크(only), 가운데 = 뷰어(그대로), 오른쪽 = 진행 패널(위: 설정·반영·초안·Run·★·Export, 아래: 선택 크롭의 채택).
// 응답·메시지 모양은 tools/fixtures/pipeline_api.json(정본). ComfyUI 탭 연결(hello — D6)·승인 메시지는 link. 모든 문자열은 textContent 로만 넣는다.
// 서버 URL 은 opts.apiUrl(보드 라우트)·opts.comfyUrl(ComfyUI /api) 상대 경로로만 연다.
"use strict";

window.DPPipeline = (() => {
  const POLL_MS = 1000; // Studio 가 낸 프롬프트 폴링(/api/history·/api/queue)
  const GONE_POLLS = 3; // 대기열에도 기록에도 없는 폴링이 이만큼 이어지면 사라짐(ComfyUI 재시작·큐에서 지움)
  const PLAN_MS = 300; // 크롭 선택을 바꾼 뒤 계획 요청까지
  const BUSY_MS = 500; // 계획 202(다른 계산 중) 뒤 다시 묻는 간격
  const BUSY_TRIES = 6; // 계획 요청 하나가 202 를 받고 다시 묻는 최대 횟수(첫 요청 포함 — 약 3초)
  const PREVIEW_MS = 400; // [수정] 입력이 멈춘 뒤 미리보기 검사
  const PROGRESS_EVERY = 5; // 아무것도 돌지 않을 때 진행 수를 다시 읽는 state 폴링 간격(그래프가 낸 실행도 알아채게)
  const HELLO_MS = 2000; // 탭 hello 주기(탭 새로고침·닫힘 감지)
  const REPLY_MS = 1000; // hello 응답을 모으는 시간
  const ACK_MS = 5000; // 승인 메시지를 보낸 뒤 탭의 ack 를 기다리는 시간
  const END_MS = 60000; // 승인 메시지를 보낸 뒤 결과(approved)를 기다리는 시간(템플릿 계산·토큰 얻기 — 계약 D-S3b §4.2)
  const CHANNEL = "bmk-dp";
  const HELLO = "bmk-dp-hello";
  const APPROVE = "bmk-dp-approve";
  const ACK = "bmk-dp-ack";
  const APPROVED = "bmk-dp-approved";
  const WAVE = "bmk-dp-wave";
  const JOB_KEY = "bmk-dp-job:"; // sessionStorage(+ 보드 키) — 다시 연 창이 이 Studio 가 낸 프롬프트를 이어서 폴링
  // 무료: apply = 짧고 사용자가 기다리는 중(대기열 앞), export = 수십 초(사용자 생성 작업을 앞지르지 않게 뒤).
  // 유료(paid): 탭이 승인 템플릿으로 낸 프롬프트 — Studio 는 그 prompt_id 를 무료 큐와 같이 이어 받고 끝을 판정한다
  const STAGES = { apply: { front: true, label: "반영" }, export: { front: false, label: "Export" },
    run: { paid: true, label: "Run" }, draft: { paid: true, label: "Spec Draft" }, reroll: { paid: true, label: "다시 생성" } };
  const PAID = ["BMKDesignPatchRun", "BMKDesignPatchSpecDraft"];
  // 승인 메시지가 계획을 다시 받아야 하는 결과(서버 409 사유 + 결과를 모름 — 이미 큐에 들어갔을 수 있음)
  const REPLAN = ["stale", "apply_needed", "nothing_to_approve", "running", "stale_settings", "timeout", "tab_changed", "unknown"];
  // 결과를 모르고 끝난 사유(이미 큐에 들어갔을 수 있음 — link 가 끝냄 no_ack·timeout·tab_changed, 탭이 제출한 뒤 응답을 잃음 unknown): 토스트 머리
  // '결과 모름'·같은 해시 재승인 확인 창 줄·진행 패널 줄
  const UNKNOWN = ["no_ack", "timeout", "tab_changed", "unknown"];
  // 승인 제출의 사용자 그래프 부수 효과(§10.7 보정 4) — 유료 확인 창·mock 상자·유료 탭으로 가는 R 확인 창 끝 줄. 그래프를 실행하지 않아도 제출이
  // 받아들여지면 탭의 다른 확장이 사용자 그래프의 '다음 값'을 소비한다(Pixaroma Dropdown 감싸개 commitPick·Prompt pick_commit — 로드 뒤 첫 실행은 겉으로 같음)
  const SIDE = "승인 제출은 ComfyUI 탭에 열린 그래프에서 실행마다 바뀌는 값을 한 번 진행시킵니다(그래프는 실행하지 않음) — 시드 제어(randomize·increment)와 "
    + "Pixaroma Dropdown·Prompt 의 순서·무작위 선택이 다음 값으로 넘어가고(Prompt 순서는 설정에 저장됨), BMK Cyclic Seed 의 batch_count 를 1 로·"
    + "자동 난수를 새로 뽑으며, 워크플로가 '수정됨'이 되고, 이 실행이 그 워크플로의 실행으로 큐·진행 표시에 보입니다.";
  // 노드 보고의 인증 중지(r1-15 — Run ui stopped_reason "auth"·Spec Draft ui auth_held): 승인 해시가 맞아도 호출하지 않음. 할 일은 단계마다 —
  // 재굴림은 원장이 되돌려져 사전 승인분이 남으므로 '다시 승인'(= R 재누름 +1)이 아니라 머리줄 'ComfyUI 큐'(paidReport)
  const LOGIN = "comfy.org 로그인 필요 — 로그인·API 키가 없거나 인증이 거부돼 남은 호출을 보내지 않음(그 호출은 과금 없음)";
  const END = { done: "끝", cancelled: "취소됨", error: "오류", gone: "사라짐" };
  const SECTIONS = { import: "Import PSD", cand: "Candidate In", prepare: "Prepare", draft: "Spec Draft", run: "Run", analyze: "Analyze",
    compose: "Compose", export: "Export" };
  const SECTION_WARN = {
    import: "다음 반영에서 크롭 소스·타깃·레퍼런스가 바뀌면 새 생성 조건(유료 대상)",
    prepare: "다음 반영에서 jobs 가 바뀌어 새 생성 조건이 될 수 있음(유료 대상)",
    draft: "초안 키가 바뀜 — fill 은 갱신 가능 표시만, refresh 면 전 크롭 재초안(유료)",
  };
  const DRIFT = {
    cell: "그래프와 Studio 를 번갈아 돌리면 생성 조건이 바뀌어 다시 과금 대상",
    calls: "생성 조건은 그대로지만 완료된 생성 조건마다 새 호출이 생겨 다시 과금 대상(생성 승인 필요)",
    cost: "생성 조건은 그대로, 대기 호출의 장 수·비용·승인 코드가 바뀜",
    import: "마지막 Import 와 다름 — 다음 반영이 크롭 소스·레퍼런스를 바꿀 수 있음",
  };
  // 다시 과금 대상인 Prepare 갈라짐(GET /pipeline drift kind): cell = 셀 설정이 다름, calls = calls_per_cell 을 늘림(완료된 셀에도 빠진 rep 이
  // 새 유료 호출 — pipeline.drift). 확인 창 맨 앞·머리줄 배지 빨강·설정 칸 줄 'bad'
  const DRIFT_BILL = ["cell", "calls"];
  // 확인 필요가 남으면 반영·Export 를 막는 절(서버 prompt_gate 409 confirm_settings — 추정한 Prepare·Import 설정으로 첫 반영이 셀을 뒤집지
  // 않게). 다른 절의 확인 필요는 막지 않고 계획 해시만 흐린다(§2.1·§3.3)
  const GATE_SECTIONS = ["prepare", "import"];
  const OWNER = { node: "노드 값(채택)", human: "사람 값", deleted: "해제함" };
  const ORIGIN = { psd: "PSD", layout: "레이아웃", adopt: "채택" };
  // state 크롭 layout_status 중 미반영인 값(board.js LAYOUT_STATUS 와 같은 판정 — "same" = 반영됨, null = layout.json 없음)
  const LAYOUT_PENDING = ["changed", "removed"];
  // 레이아웃 미반영 사유의 머리말 — 계획 apply_pending·import_pending 의 레이아웃 사유('레이아웃 미반영(rev N)')도 이 말로 시작한다
  const LAYOUT_REASON = "레이아웃 미반영";
  const DRAFT_NOTE = "원장에만 남은 초안(받았지만 스펙에 못 쓴 것)은 이 반영이 병합하지 않음 — 실행(G) 초안 줄의 '못 쓴 스펙'으로 보이고, "
    + "그래프 Spec Draft 를 승인 없이 한 번 큐하면 병합";
  const STYLE_UNIT = "_style"; // 초안 계획의 화풍 단위 crop_id(vlm.STYLE_ID — "_project" 는 화풍 스펙 파일 이름일 뿐)
  // 유도값 출처(GET /pipeline derived.from) → [매니페스트 값으로 다시] 확인 창 문구
  const FROM = { manifest: "프로젝트 기록", jobs: "jobs 에서 추정", spec_draft: "스펙 초안 출처에서 추정", default: "기록 없음 — 노드 기본값" };

  // ── 순수 판정·문구(J-P1·J-P2 가 node 에서 돈다) ──
  function checkPrompt(p) {
    // 보내기 전 검사(T1·T4): 노드 키 모두 dp_*, class_type BMKDesignPatch* 이고 Run·Spec Draft(유료 노드) 아님 → "" | 보내지 않는 이유
    if (!p || typeof p !== "object" || Array.isArray(p) || !Object.keys(p).length) return "템플릿이 비었습니다";
    for (const [k, n] of Object.entries(p)) {
      const cls = n && typeof n === "object" ? n.class_type : null;
      if (!/^dp_[a-z]+$/.test(k) || typeof cls !== "string" || !cls.startsWith("BMKDesignPatch")) return `규칙 밖 노드 ${k}`;
      if (PAID.includes(cls)) return `무료 템플릿에 유료 노드 ${k}(${cls})`;
    }
    return "";
  }

  function freeBody(prompt, stage) {
    // ComfyUI /api/prompt 본문(이 파일에서 하나): 서버가 준 무료 템플릿 + front. 인증·클라이언트 ID·탭 정보는 싣지 않는다
    return { prompt, front: STAGES[stage].front };
  }

  function comfyRefusal(d, status) {
    // /api/prompt 거절: 노드 없음 = 새 노드·입력이 없는 옛 ComfyUI(재시작 전), 검증 실패는 메시지 그대로
    const e = d && d.error;
    if (e && e.type === "missing_node_type") return `ComfyUI 재시작 필요 — ${e.message || "노드가 없습니다"}`;
    const rows = Object.entries((d && d.node_errors) || {})
      .flatMap(([k, v]) => ((v && v.errors) || []).map((x) => `${k}: ${x.message}${x.details ? ` (${x.details})` : ""}`));
    return `ComfyUI 가 큐에 받지 않음: ${(e && e.message) || `HTTP ${status}`}` + (rows.length ? ` — ${rows.join(" / ")}` : "");
  }

  function endOf(h) {
    // /api/history 항목 → {state: done|cancelled|error, msg, export}. success = 끝(execution_cached 만이어도), 다음 취소, 그 밖 오류(메시지 그대로).
    // export = Export ui 의 bmk_dp_export[0] {outcome, psd, rev}(export 템플릿 — 노드가 옛 버전이면 null)
    const st = h.status || {};
    const msgs = st.messages || [];
    const out = ((h.outputs || {}).dp_export || {}).bmk_dp_export;
    if (st.status_str === "success") return { state: "done", msg: "", export: Array.isArray(out) && out.length ? out[0] : null };
    if (msgs.some((m) => m[0] === "execution_interrupted")) return { state: "cancelled", msg: "취소됨", export: null };
    const err = msgs.find((m) => m[0] === "execution_error");
    const d = (err && err[1]) || {};
    return { state: "error", export: null,
      msg: err ? `${d.node_type || d.node_id || "노드"}: ${String(d.exception_message || "오류").trim()}` : `끝 상태 ${st.status_str || "없음"}` };
  }

  function adoptState(dv) {
    // 채택 단추(§3.4 표) → {can, why}: kind add 이고 거절 이유가 없을 때만. why = 거절 이유 그대로 · kind 별 안내
    const a = dv.adopt;
    if (dv.spec_error) return { can: false, why: a.reason || "스펙 JSON 오류 — 파일을 고친 뒤 다시" };
    if (dv.unwritten) return { can: false, why: "원장 초안이 아직 스펙에 안 쓰임 — 그래프 Spec Draft 를 한 번 큐(0 호출)" };
    if (a.reason) return { can: false, why: a.reason };
    if (a.kind === "add") return { can: true, why: "" };
    if (a.kind === "keep_human") return { can: false, why: "사람 값 있음 — [수정]" };
    if (a.kind === "keep_deleted") return { can: false, why: "해제함 — 다시 쓰려면 [수정]" };
    if (!a.value) return { can: false, why: "채택할 제안 없음" };
    return { can: false, why: dv.layer_targets.value === a.value ? "이미 채택" : "노드 값 그대로 — 바꾸려면 [수정]" };
  }

  function num(v) {
    return Math.round(Number(v) || 0).toLocaleString("ko-KR");
  }

  function usd(r) {
    return r[0] === r[1] ? `$${r[0].toFixed(2)}` : `$${r[0].toFixed(2)}–${r[1].toFixed(2)}`;
  }

  function span(r) {
    return r[0] === r[1] ? num(r[0]) : `${num(r[0])}–${num(r[1])}`;
  }

  function runText(run, running = run.running) {
    // §3.3 Run 줄 → {line, rows, dim, label}. 승인 필요 = 선택(only) 계획(C13 — 전체 수치와 가르게 선택 크롭 수를 붙임), 재굴림 = 사전 승인
    // (Run 실행 때 선택과 무관하게 함께 과금). running = 실행 중(계획을 받은 뒤 그래프가 Run 을 시작하면 패널이 state running 을 넘김).
    // label = 승인 단추(계약 §5 'Run 승인 9건 ≈ $5.40–7.10')
    if (run.error) return { line: `Run: 계산하지 못함 — ${run.error}`, rows: [], dim: true, label: "생성 승인" };
    const picked = Object.values(run.by_crop || {}).filter((x) => x.selected).length;
    const parts = [`Run: 승인 필요(선택 ${picked}크롭) ${run.need}건` + (run.need ? ` ≈ ${usd(run.est_usd)}(≈ ${span(run.credits)} 크레딧)` : "")];
    if (run.pre) parts.push(`다시 생성(사전 승인) ${run.pre}건 ≈ ${usd(run.est_usd_pre)} 도 함께 과금(선택과 무관)`);
    parts.push(`복구 ${run.free}`);
    if (run.filtered) parts.push(`선택 밖 ${run.filtered}건`);
    parts.push(`승인 코드 ${run.hash || "없음"}`);
    const line = parts.join(" · ") + (run.backend === "mock" ? "  [MOCK]" : "");
    return { line: running ? `Run 실행 중 — 끝난 뒤 다시 계산 (${line})` : line, rows: run.reasons.map((x) => `${x.reason} × ${x.n}`),
      dim: !!running, label: `생성 승인 ${run.need}건` + (run.need ? ` ≈ ${usd(run.est_usd)}` : "") + (run.backend === "mock" ? " [MOCK]" : "") };
  }

  function tally(ids, unit = "") {
    // 초안 계획의 crop_id 목록 → "n{unit}" · "n{unit} + 화풍" · "화풍" · "0"(화풍 단위는 크롭으로 세지 않는다 — §3.3·§4.4)
    const crops = ids.filter((id) => id !== STYLE_UNIT).length;
    const style = ids.length > crops;
    return crops ? `${crops}${unit}${style ? " + 화풍" : ""}` : style ? "화풍" : "0";
  }

  function minutes(d) {
    // 초안 예상 분(초안 줄·승인 단추 라벨·확인 창): 노드 확인 창(js draftMinutes)·노드 UI 와 같은 올림 — §4.4 같은 계획이면 같은 분
    return Math.max(1, Math.ceil(d.est_seconds / 60));
  }

  function draftText(d, running = d.running) {
    // §3.3 초안 줄 → {line, rows, dim, what, label}: 승인 필요 크롭 + 화풍(STYLE_UNIT 단위), 크레딧(openai)·분(local_qwen), 갱신 가능, 해시.
    // running = 실행 중(계획을 받은 뒤 그래프·다른 경로의 Spec Draft 가 시작하면 패널이 state drafting 을 넘김 — runText 와 같은 뜻).
    // label = 승인 단추(계약 §5 '초안 승인 3크롭 + 화풍 ≈ 81(최대 140) 크레딧', local_qwen '로컬 n건 ≈ m분')
    if (d.error) return { line: `초안: 계산하지 못함 — ${d.error}`, rows: [], dim: true, what: "", label: "초안 승인" };
    if (running) return { line: "초안: Spec Draft 실행 중 — 끝난 뒤 다시 계산", rows: [], dim: true, what: "", label: "초안 승인" };
    const min = minutes(d);
    const what = tally(d.units.map((u) => u.crop_id), "크롭");
    const qwen = d.family === "local_qwen";
    const mock = d.backend === "vlm_mock"; // 크레딧 추정은 openai 만(mock 은 늘 0 — 노드 단추처럼 건수만)
    let head = `초안: 승인 필요 ${what}`;
    let cost = "";
    if (d.units.length) {
      head += qwen ? ` · 로컬 Qwen 약 ${min}분(ComfyUI 모델을 VRAM 에서 내림 — 과금 없음)`
        : mock ? ` · 약 ${min}분` : ` ≈ ${num(d.est_credits[0])}(최대 ${num(d.est_credits[1])}) 크레딧 · 약 ${min}분`;
      cost = qwen ? ` ≈ ${min}분` : mock ? "" : ` ≈ ${num(d.est_credits[0])}(최대 ${num(d.est_credits[1])}) 크레딧`;
    }
    const label = `초안 승인 ${qwen ? `로컬 ${d.units.length}건` : what}${cost}${mock ? " [MOCK]" : ""}`;
    const parts = [head];
    if (d.refresh_available.length) parts.push(`갱신 가능 ${tally(d.refresh_available.map((x) => x.crop_id))}`);
    if (d.filtered.length) parts.push(`선택 밖 ${tally(d.filtered)}`);
    parts.push(`승인 코드 ${d.hash || "없음"}`);
    const name = (id) => (id === STYLE_UNIT ? "화풍" : id);
    const rows = d.units.map((u) => `${name(u.crop_id)}: ${u.reasons.join(", ") || u.state}`)
      .concat(d.skipped.map((x) => `${name(x.crop_id)}: 건너뜀 — ${x.reason}`));
    const w = d.warnings;
    if (w.unwritten.length) rows.push(`못 쓴 스펙 ${w.unwritten.length}(${w.unwritten.join(", ")}) — 그래프 Spec Draft 를 승인 없이 한 번 큐하면 병합`);
    if (w.mock_specs) rows.push(`mock 초안이 남은 스펙 ${w.mock_specs} — 실제 백엔드 Spec Draft 가 양보시킴`);
    return { line: parts.join(" · ") + (d.backend === "vlm_mock" ? "  [MOCK]" : ""), rows, dim: false, what, label };
  }

  function approveText(kind, p, ids, style, full) {
    // 유료 확인 창 문구(§4.4 — 노드 JS 확인 창과 같은 말, 계약 §5 경고 줄) → 줄 목록(끝 = 부수 효과 줄 SIDE). kind = run·draft, p = 지금 계획,
    // ids = 선택 crop_id, style = 화풍 단위를 골랐나(only 에 style), full = 초안 apply 가 full(화풍을 안 골랐으면 화풍 초안은 only 로 보류)
    const d = p.draft;
    const out = [];
    if (kind === "draft" && d.family === "local_qwen") {
      out.push(`로컬 Qwen 으로 ${d.units.length}건(≈ ${minutes(d)}분). ComfyUI 모델을 VRAM 에서 내립니다(과금 없음).`);
    } else if (kind === "draft") {
      out.push(`VLM 초안 ${tally(d.units.map((u) => u.crop_id), "크롭")}(보통 ≈ ${num(d.est_credits[0])}, 최대 ${num(d.est_credits[1])} 크레딧)을 승인하고 `
        + "과금합니다." + (d.orphan_retries ? ` 결과 못 받음 재시도 ${d.orphan_retries}건 포함(과금됐을 수 있음).` : ""));
      if (d.reasons.length) out.push(`사유: ${d.reasons.map((x) => `${x.reason} × ${x.n}`).join(", ")}`);
      out.push("드라이런 이후 대기 목록이 바뀌었으면 서버가 호출 없이 드라이런만 다시 합니다.");
    } else {
      const r = p.run;
      out.push(`GPT Image 호출 ${r.need}건(예상 ${usd(r.est_usd)} ≈ ${span(r.credits)} 크레딧)을 승인하고 과금합니다. 대상: ${ids.join(", ")}`);
      if (r.pre) out.push(`다시 생성(사전 승인) ${r.pre}건 ≈ ${usd(r.est_usd_pre)} 도 함께 과금 — 선택과 무관(다른 크롭 포함).`);
      out.push("드라이런 이후 대기 목록이 바뀌었으면 서버가 과금 없이 드라이런만 다시 합니다.");
      // 초안 대기: 선택 크롭(by_crop wait — only 없는 판정)과 화풍(pending_style)을 따로. 화풍을 고르지 않은 apply=full 이면 화풍 초안은 보류
      const wait = ids.filter((id) => d.by_crop[id] && d.by_crop[id].wait.length).length;
      const sw = !!d.pending_style && style;
      if (wait || sw) {
        out.push(`주의: 스펙 초안 대기 ${[wait ? `${wait}크롭` : "", sw ? "화풍" : ""].filter(Boolean).join(" + ")}(Spec Draft 미승인) — `
          + "지금 승인하면 초안 없는 프롬프트로 과금합니다.");
      }
      if (d.pending_style && full && !style) {
        out.push("주의: 화풍 단위를 고르지 않아(「비용·승인」 칸 '화풍 단위 포함' 끔) 화풍 초안 대기가 보류됨 — 지금 승인하면 화풍 초안 없는 프롬프트로 과금합니다.");
      }
      const unw = d.warnings.unwritten.length;
      if (unw) out.push(`주의: 스펙 파일 ${unw}개에 초안·화풍 변경을 아직 못 씀(잠김) — 지금 승인하면 그 변경 없이 과금합니다.`);
      const mk = r.backend === "mock" ? 0 : d.warnings.mock_specs;
      if (mk) out.push(`주의: 스펙 ${mk}개가 mock 초안(자리표시 문구) — 지금 승인하면 그 문구로 과금합니다.`);
    }
    return out.concat(SIDE);
  }

  function paidReport(stage, h) {
    // 유료 실행 끝 한 줄(계약 §5): /api/history 항목의 Run 보고 dp_run.bmk_dp_run[0](run·reroll)·Spec Draft 보고 dp_draft.bmk_dp_draft[0](draft) —
    // approved_now(승인 해시로 실행했나 — 아니면 실행 전 계획이 바뀌어 드라이런)·pending(남은 승인 필요), 인증 중지(stopped_reason "auth" — Run 은
    // 로그인 정보 없음·401/402/403 부터 멈춤, Spec Draft 는 실행 중 401/402/403, Spec Draft auth_held — 로그인 정보가 없어 보내지 않은 단위 수:
    // 승인 해시가 맞아도 호출하지 않음 — LOGIN).
    // 보고가 없으면 성공 = 노드가 옛 버전, 오류·취소 = Run·Spec Draft 전에 멈춤("" — 끝 줄은 오류 문구만)
    const out = (h && h.outputs) || {};
    const ui = stage === "draft" ? (out.dp_draft || {}).bmk_dp_draft : (out.dp_run || {}).bmk_dp_run;
    const e = Array.isArray(ui) && ui.length ? ui[0] : null;
    if (!e) return endOf(h).state === "done" ? "보고 없음(노드가 옛 버전 — ComfyUI 재시작)" : "";
    const mock = e.backend === "mock" || e.backend === "vlm_mock" ? " [MOCK]" : "";
    const login = e.stopped_reason === "auth" || (stage === "draft" && e.auth_held > 0);
    if (stage === "reroll") {
      return `${login ? `다시 생성 중지 — ${LOGIN}: ComfyUI 탭에서 로그인한 뒤 머리줄 [다시 생성 보내기](사전 승인한 다시 생성은 예약된 채 남음 — R 을 다시 누르면 `
        + "+1 호출이 더 승인됨)" : "다시 생성(사전 승인)·복구만 실행"} · 승인 필요 ${e.pending}건 남음${mock}`;
    }
    const ran = !e.approved_now ? "승인 안 됨 — 승인 코드가 실행 전 계획과 달라 드라이런(승인 필요분 과금 없음)"
      : login ? `승인했지만 중지 — ${LOGIN}: ComfyUI 탭에서 로그인한 뒤 다시 승인` : "승인분 실행";
    return stage === "draft" ? `${ran} · 남은 초안 대기 ${e.pending}건${mock}` : `${ran} · 남은 승인 필요 ${e.pending}건${mock}`;
  }

  function shown(x) {
    // 설정 값 한 칸(문구용): 문자열은 그대로(빈 문자열 = ""), 그 밖은 JSON
    return typeof x === "string" ? x || '""' : JSON.stringify(x);
  }

  function same(a, b) {
    return JSON.stringify(a) === JSON.stringify(b);
  }

  function driftText(d) {
    return `${d.section}.${d.field}: 마지막 ${d.kind === "import" ? "Import" : "Prepare"} ${shown(d.manifest)} · Studio ${shown(d.settings)} — ${DRIFT[d.kind] || d.kind}`;
  }

  function jointFirst(S, t) {
    // ★ 없는 타깃이 공동 결과(여러 타깃을 한 번에 고친 후보)만 가졌고 그 첫 타깃(보통 A)이 그 공동 결과(first_key)를 ★ 했으면 그 첫 tid —
    // ★ 는 첫 타깃에만이라 이 타깃의 ★ 없음은 정상(S0 중복 ★). 첫 타깃이 따로 고친 후보를 ★ 했으면 이 타깃의 결과는 합성에 없음 → null(경고)
    const live = t.candidates.filter((c) => !c.rejected);
    if (!live.length || !live.every((c) => c.joint && c.joint.first && c.joint.first !== t.tid)) return null;
    const first = live[0].joint.first;
    const ft = S.targets.find((x) => x.crop_id === t.crop_id && x.tid === first);
    return ft && live.some((c) => ft.pick_keys.includes(c.joint.first_key)) ? first : null;
  }

  function starChecks(S, seams, pending, maskLeft = null) {
    // §3.6 Export 전 점검(정보·경고 — 막지 않음) → [{text, key, mask, info}] key = 누르면 갈 타깃(없으면 null), mask = 누르면 갈 마스크 모드(K) 자리,
    // info = 할 일이 아닌 정보 줄(공동 결과의 뒤 타깃 ★ 없음 — 10/21 실사용에서 경고색 줄을 할 일로 읽고 중복 ★ 를 만듦)
    // (S4 §7 — {key: ★ 후보} | {crop: 그 크롭의 가장 나쁜 이음매}, 없으면 검토로). pending = 반영 필요 사유(export 템플릿에는 Import 가 없어 미반영은
    // 반영 뒤 Export) — 크롭 줄로 나온 레이아웃 미반영은 다시 적지 않음. maskLeft = 이 창의 적용 안 한 마스크 편집 {key, n, name} | null(board.js —
    // 다른 창의 세션은 모름). 이음매는 편집 뒤 다시 잰 값(지금 ★ 합성) — seams null = 다시 재는 중(board.js — 옛 bad 수 대신 그 한 줄)
    const out = [];
    let lay = false;
    for (const t of S.targets) {
      const name = `${t.crop_name || t.crop_id} · ${t.tid}`;
      const jf = t.pick_keys.length ? null : jointFirst(S, t);
      if (jf) out.push({ text: `${name} — ★ 없음(공동 결과 — ★ 는 ${jf} 에만, 정상)`, key: null, info: true });
      else if (!t.pick_keys.length) out.push({ text: `${name} — ★ 없음`, key: t.key });
      else if (t.candidates.some((c) => c.picked && c.analysis === "none")) out.push({ text: `${name} — 분석 없는 ★(미반영 — Analyze)`, key: t.key });
      for (const c of t.candidates) {
        if (c.picked && c.mask_edit && c.mask_edit.stale) {
          out.push({ text: `${name} — 처음 마스크가 바뀜(c${String(c.n).padStart(2, "0")} — 처음 마스크로 다시 시작 또는 그대로)`, key: t.key, mask: { key: c.key } });
        }
      }
    }
    if (maskLeft) {
      // 세션 후보가 그 뒤 검토에서 ★ 해제·탈락되면 그 획은 적용할 수 없다(409) — K 대신 그 타깃을 검토로(다시 ★ 하면 K 에서 이어서, K 는 새 ★ 로 — 버릴지 확인)
      const t = S.targets.find((x) => x.candidates.some((c) => c.key === maskLeft.key));
      const c = t ? t.candidates.find((x) => x.key === maskLeft.key) : null;
      if (c && c.picked && !c.rejected) {
        out.push({ text: `${maskLeft.name} — 적용 안 한 마스크 편집 ${maskLeft.n}획(이 창 — Export 에 들어가지 않음)`, key: null, mask: { key: maskLeft.key } });
      } else {
        out.push({ text: `${maskLeft.name} — 적용 안 한 마스크 편집 ${maskLeft.n}획(이 창 — 편집하던 후보가 이제 ★ 가 아니라 적용할 수 없음: 다시 ★ 하거나 K 에서 버리기)`,
          key: t ? t.key : null });
      }
    }
    if (!seams) out.push({ text: "이음매 재는 중(★ 합성이 바뀜) — 끝나면 이 점검에 새 bad 수", key: null });
    for (const c of S.crops) {
      const t0 = S.targets.find((t) => t.crop_id === c.id);
      const key = t0 ? t0.key : null;
      const name = c.name || c.id;
      for (const d of c.dup_picks) out.push({ text: `${name} · ${d.tid} — 중복 ★(같은 이미지는 ${d.same_as} 에만)`, key: `${c.id}/${d.tid}` });
      if (c.stale_picked.length) out.push({ text: `${name} — 옛 크롭 ★ ${c.stale_picked.length}`, key });
      const sm = seams && seams.get(c.id);
      if (sm && (sm.bad || (sm.growSides && sm.growSides.length))) {
        const row = { text: `${name} — 이음매 ${sm.bad ? `bad ${sm.bad}` : `weak ${sm.n}`}`, key, mask: { crop: c.id } };
        if (!sm.bad) row.info = true;
        if (sm.growSides && sm.growSides.length) row.grow = { crop: c.id, sides: sm.growSides };
        out.push(row);
      }
      if (LAYOUT_PENDING.includes(c.layout_status)) {
        lay = true;
        out.push({ text: `${name} — ${LAYOUT_REASON}(반영 뒤 Export)`, key });
      }
    }
    for (const r of pending) if (!(lay && r.startsWith(LAYOUT_REASON))) out.push({ text: `미반영: ${r} — 반영 뒤 Export`, key: null });
    return out;
  }

  function rectText(a, b) {
    // 크롭 사각형 [x0, y0, x1, y1] 마지막 반영 a → 레이아웃 b: 값 + 이동(오른쪽·아래가 +)·크기 바뀜
    const [dx, dy] = [b[0] - a[0], b[1] - a[1]];
    const mv = [dx ? `${dx > 0 ? "오른쪽" : "왼쪽"} ${Math.abs(dx)}` : "", dy ? `${dy > 0 ? "아래" : "위"} ${Math.abs(dy)}` : ""].filter(Boolean).join("·");
    const [wa, ha, wb, hb] = [a[2] - a[0], a[3] - a[1], b[2] - b[0], b[3] - b[1]];
    const sz = wa !== wb || ha !== hb ? `크기 ${wa}×${ha} → ${wb}×${hb}` : "";
    const why = [mv ? `이동 ${mv}` : "", sz].filter(Boolean).join(" · ");
    return `위치 [${a.join(", ")}] → [${b.join(", ")}]` + (why ? ` (${why})` : "");
  }

  function layoutLines(S) {
    // 레이아웃 미반영 크롭별 줄(진행(G) 반영 칸·반영 확인 창·머리줄 제목) → [{id, text, back}]. state 크롭 요약의 name·rect = 마지막 반영(매니페스트),
    // layout_diff = 바뀐 크롭의 레이아웃 값, layout_added = 레이아웃에만 있는 크롭. back = [되돌리기]가 보낼 update 값(바뀐 이름·사각형을 마지막
    // 반영 값으로) — 대상 문구만 바뀐 크롭·지운 크롭·새 크롭은 null(편집(E)에서). layout_diff 가 없는 옛 서버는 사유만
    const out = [];
    for (const c of S.crops) {
      const name = c.name || c.id;
      if (c.layout_status === "removed") {
        out.push({ id: c.id, text: `${name} — 레이아웃에서 지움(후보 ${c.candidates} · ★ ${c.picked_targets}) — 다음 반영에서 빠짐`, back: null });
      }
      if (c.layout_status !== "changed") continue;
      const d = c.layout_diff;
      if (!d) {
        out.push({ id: c.id, text: `${name} — ${LAYOUT_REASON}`, back: null });
        continue;
      }
      const parts = [];
      const back = {};
      if (d.fields.includes("name")) {
        parts.push(`이름 → ${d.name}`);
        back.name = c.name;
      }
      if (d.fields.includes("rect")) {
        parts.push(rectText(c.rect, d.rect));
        back.rect = c.rect;
      }
      if (d.fields.includes("targets")) {
        const now = (d.targets || []).join("; "); // 마지막 반영 문구 → 새 문구(targets_was 없는 판은 새 문구만)
        parts.push(d.targets_was ? `대상 문구 ${d.targets_was.join("; ")} → ${now}` : `대상 문구 바뀜(${now})`);
      }
      // rest = [되돌리기](이름·사각형)가 되돌리지 않는 이 크롭의 바뀜(대상 문구)이 남는가
      out.push({ id: c.id, text: `${name} — ${parts.join(" · ")}`, back: Object.keys(back).length ? back : null, rest: d.fields.includes("targets") });
    }
    for (const a of S.layout_added || []) {
      out.push({ id: a.id, text: `${a.name} — 새 크롭 [${a.rect.join(", ")}] — 다음 반영에서 생김(초안·생성 승인 대상)`, back: null });
    }
    return out;
  }

  // ── 서버 세션: 설정(P)·계획·Studio 가 낸 프롬프트(job)·채택. DOM 을 쓰지 않는다(J-P1·J-P2 가 node 에서 가짜 fetch 로 돈다) ──
  function session(apiUrl, comfyUrl, root, fetchFn, store, pollMs = POLL_MS) {
    // P = 마지막 GET /pipeline, plan = 받아들인 마지막 계획(planOnly·planStyle = 그 계획을 요청한 정규 only·화풍 — null·false = only 없음),
    // job = 이 Studio 가 낸 프롬프트 {stage, pid, number, running, ahead, gone} — 무료 큐 또는 탭이 승인 템플릿으로 낸 유료 프롬프트(track),
    // last = stage 마다 마지막 끝 {state, msg, at, pid, export, report(유료 — paidReport)}, busy = 설정·채택 쓰기 중(하나씩)
    const s = { P: null, err: null, plan: null, planOnly: null, planStyle: false, planErr: null, job: null, sending: false, busy: false,
      last: {}, progress: null, onChange() {}, onDone() {}, onWrite() {} };
    let reads = 0; // 보낸 GET /pipeline 수(요청 순서)
    let readDone = 0; // 받아들인 읽기 중 가장 늦게 보낸 것의 번호
    let planSeq = 0; // 계획 요청 세대 — 더 새 요청이 있으면 앞 응답은 버린다
    let planReq = null; // 진행 중인 계획 요청 {key: only|style, p, seq: 요청 세대, next: 이 요청 뒤에 한 번 더 묻는 부름(loadPlan)}
    const key = JOB_KEY + root;
    const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
    const qs = (o) => new URLSearchParams(Object.assign({ root }, o)).toString();
    const postOpts = (body) => ({ method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

    async function readJson(r) {
      try {
        return await r.json();
      } catch (e) {
        return null; // JSON 이 아닌 응답(라우트가 없는 옛 서버의 404·ComfyUI 의 빈 200)
      }
    }

    async function call(path, body) {
      const r = await fetchFn(apiUrl(path), body === undefined ? { cache: "no-store" } : postOpts(Object.assign({ root }, body)));
      const data = await readJson(r);
      if (!r.ok || !data) {
        const msg = data && data.error ? data.error
          : r.status === 404 ? "파이프라인 라우트가 없습니다 — ComfyUI 를 재시작해야 실행(G)을 쓸 수 있습니다"
            : `응답을 읽을 수 없습니다 (HTTP ${r.status})`;
        throw Object.assign(new Error(msg), { status: r.status, data });
      }
      return data;
    }

    s.openFolder = () => call("open_folder", {}); // Export 출력 폴더를 탐색기로(배치 3-5a — 서버 PC)
    s.psList = () => call("mask/ps_list?" + qs({}));
    s.psImport = (psd, dry_run, seen) => call("mask/ps_import", { psd, dry_run, seen });

    function comfy(path, body) {
      return fetchFn(comfyUrl(path), body === undefined ? { cache: "no-store" } : postOpts(body));
    }

    function remember(job) {
      // 이 창이 다시 열려도 이어서 폴링하게(sessionStorage). 저장소를 못 쓰면 이 창에서만
      try {
        if (job) store.setItem(key, JSON.stringify({ stage: job.stage, pid: job.pid, number: job.number }));
        else store.removeItem(key);
      } catch (e) {
        // 저장소 없음·막힘
      }
    }

    s.load = async () => {
      // GET /pipeline(파일을 쓰지 않음) — 늦게 보낸 읽기를 먼저 받았으면 그보다 먼저 보낸 응답은 버린다
      const seq = ++reads;
      let d;
      try {
        d = await call("pipeline?" + qs({}));
      } catch (e) {
        if (seq > readDone) s.err = e.message;
        return false;
      }
      if (seq < readDone) return false;
      readDone = seq;
      s.err = null;
      s.P = d;
      return true;
    };

    s.save = async (section, values, rev = s.P.rev) => {
      // 절 하나를 통째로(POST /pipeline/settings — rev = 가진 판: 파일 없음 0·깨짐 null, 여러 절을 잇따라 저장하면 앞 저장 응답의 rev). 성공이든
      // 409(다른 창이 씀)든 끝나면 다시 읽는다
      if (s.busy) throw new Error("앞 저장을 처리하는 중입니다 — 끝난 뒤 다시 하세요");
      s.busy = true;
      try {
        const r = await call("pipeline/settings", { rev, section, values });
        s.onWrite();
        return r;
      } finally {
        await s.load();
        s.busy = false;
      }
    };

    async function place(job) {
      // 대기열에서 이 프롬프트의 자리(S2 Ref Cut 과 같은 꼴): running, ahead = 앞 작업 수(실행 중 포함), gone = 대기열에 없는 폴링 수.
      // false = 대기열을 읽지 못함(job 그대로)
      const r = await comfy("queue");
      const d = r.ok ? await readJson(r) : null;
      if (!d) return false;
      const run = d.queue_running || [];
      const pend = d.queue_pending || [];
      job.running = run.some((x) => x[1] === job.pid);
      const mine = pend.find((x) => x[1] === job.pid); // 대기열 항목 [번호, prompt_id, …] — 탭이 낸 유료 프롬프트는 번호를 대기열에서만 앎
      job.ahead = job.running ? 0 : mine ? run.length + pend.filter((x) => x[0] < mine[0]).length : null;
      job.gone = job.running || mine ? 0 : job.gone + 1;
      return true;
    }

    async function record(pid) {
      // /api/history/<id> 의 그 항목 — null = 아직 없음, undefined = 읽지 못함
      try {
        const r = await comfy(`history/${encodeURIComponent(pid)}`);
        const d = r.ok ? await readJson(r) : null;
        return d ? d[pid] || null : undefined;
      } catch (e) {
        return undefined;
      }
    }

    function finish(job, res, h) {
      // h = 끝을 판정한 /api/history 항목(사라짐·대기 중 취소는 없음) — 유료 실행이면 그 Run·Spec Draft 보고 한 줄(report)
      s.job = null;
      remember(null);
      s.last[job.stage] = Object.assign({ at: new Date().toLocaleTimeString("ko-KR", { hour12: false }), pid: job.pid }, res);
      if (STAGES[job.stage].paid && h) s.last[job.stage].report = paidReport(job.stage, h);
      s.onDone(job, res);
      s.onChange();
    }

    async function watch(job) {
      // 끝 판정(§3.1): 기록에 있으면 끝(endOf), 대기열에도 기록에도 없는 폴링이 GONE_POLLS 번 이어지면 사라짐. 다시 그리기는 자리(실행 중·
      // 앞 작업 수)가 바뀐 때만 — 매 폴링마다 그리면 누르는 중인 단추(취소·Export)가 갈린다
      for (;;) {
        await sleep(pollMs);
        if (s.job !== job) return;
        const h = await record(job.pid);
        if (s.job !== job) return;
        if (h) {
          finish(job, endOf(h), h);
          return;
        }
        const was = [job.running, job.ahead].join("|");
        try {
          await place(job);
        } catch (e) {
          // ComfyUI 에 닿지 않음(재시작 중) — 다음 폴링
        }
        if (s.job !== job) return;
        if (job.running) job.deleted = false; // 실행을 시작함 — 앞서 보낸 delete 는 듣지 않았다(cancel 이 지운 뒤 대기열을 읽지 못해 남긴 표시)
        if (h === null && job.gone >= GONE_POLLS) {
          // 이 창이 대기 중에 지운 프롬프트(cancel 이 기록을 읽지 못해 판정을 넘김)는 ComfyUI 가 기록을 남기지 않는다 — 사라짐이 아니라 취소됨
          finish(job, job.deleted ? { state: "cancelled", msg: "대기 중에 취소함", export: null }
            : { state: "gone", msg: "ComfyUI 재시작 또는 큐에서 지움", export: null });
          return;
        }
        if ([job.running, job.ahead].join("|") !== was) s.onChange();
      }
    }

    s.queue = async (stage, expect) => {
      // 무료 템플릿 큐(§3.1): GET /pipeline/prompt → 보내기 전 검사 → POST /api/prompt → 폴링. → job | {old: true}(라우트 없는 옛 서버 —
      // 호출자가 탭 반영으로 대신). 409(설정 확인 필요·PSD 없음·경로 없음·깨진 설정 — e.data.reason)·검사 실패·ComfyUI 거절은 예외.
      // expect = 마지막 갈라짐 점검을 통과한 '<pipeline rev>:<매니페스트 rev>'(반영 — checkWarn) — 서버가 지금 값과 다르면 409 stale_settings(확인 뒤 다른
      // 창의 저장·그래프 Prepare·Import), 옛 서버(S3a — expect 를 보지 않음)는 템플릿 sig 의 pipeline rev 를 여기서 대조. 둘 다 보내지 않고 예외
      // (rev null·0 = 깨짐·파일 없음은 서버가 409)
      if (!STAGES[stage] || STAGES[stage].paid) throw new Error(`무료 stage 가 아닙니다: ${stage}`);
      if (s.job) throw new Error(`이 Studio 의 ${STAGES[s.job.stage].label}(${s.job.stage}) 실행이 끝난 뒤 다시 하세요`);
      if (s.sending) throw new Error("큐에 넣는 중입니다");
      s.sending = true;
      try {
        let t;
        try {
          t = await call("pipeline/prompt?" + qs(expect ? { stage, expect } : { stage }));
        } catch (e) {
          if (e.status === 404 && !e.data) return { old: true };
          if (e.data && e.data.reason === "stale_settings") {
            e.message = `큐에 넣지 않음 — 확인한 뒤 실행 설정·프로젝트 기록이 바뀜(${expect} → ${e.data.sig}, 다른 창의 저장·그래프 실행): 다시 [반영 (무료)]해 새 설정으로 확인하세요`;
          }
          throw e;
        }
        const bad = checkPrompt(t.prompt);
        if (bad) throw new Error(`큐에 넣지 않음 — ${bad}`);
        const want = expect && expect.split(":")[0];
        const got = String(t.sig).split(":")[0];
        if (want && got !== want) {
          throw new Error(`큐에 넣지 않음 — 확인한 뒤 실행 설정이 바뀜(pipeline rev ${want} → ${got}, 다른 창의 저장): 다시 [반영 (무료)]해 새 설정으로 확인하세요`);
        }
        const r = await comfy("prompt", freeBody(t.prompt, stage));
        const d = await readJson(r);
        if (!r.ok || !d || !d.prompt_id) throw new Error(comfyRefusal(d, r.status));
        return begin({ stage, pid: d.prompt_id, number: d.number, running: false, ahead: null, gone: 0 });
      } finally {
        s.sending = false;
      }
    };

    function begin(job) {
      s.job = job;
      remember(job);
      s.onChange();
      watch(job);
      return job;
    }

    s.track = (stage, pid) => {
      // 탭이 승인 템플릿으로 낸 유료 프롬프트(approved 의 prompt_id)를 이 Studio 의 실행으로 — 무료 큐와 같은 이어 받기·끝 판정·진행 줄.
      // → job | null(이 Studio 가 다른 실행을 보는 중 — 호출자가 알림)
      return s.job ? null : begin({ stage, pid, number: null, running: false, ahead: null, gone: 0 });
    };

    s.resume = () => {
      // 이 창이 앞서 낸 프롬프트(sessionStorage)를 이어서 폴링
      if (s.job) return false;
      let j = null;
      try {
        j = JSON.parse(store.getItem(key) || "null");
      } catch (e) {
        j = null;
      }
      if (!j || !STAGES[j.stage] || typeof j.pid !== "string") return false;
      const job = { stage: j.stage, pid: j.pid, number: j.number, running: false, ahead: null, gone: 0 };
      s.job = job;
      watch(job);
      return true;
    };

    s.cancel = async () => {
      // 대기 중일 때만(POST /api/queue delete — 실행 중이면 끝까지 받는다). ComfyUI 의 delete 는 대기 중 항목만 지우고 늘 200 이라
      // 지우기 전후로 대기열을 다시 본다. 지운 뒤 대기열에 없어도 그 사이 실행을 시작해 끝났을 수 있어 기록을 본다(있으면 그 끝 — §3.1).
      // → true = 취소됨, false = 취소하지 않음(실행 중·이미 끝남·지우기 전 대기열을 읽지 못함), null = 지운 뒤 대기열·기록을 읽지 못해 끝을 모름
      // (폴링이 판정 — 지운 프롬프트는 기록이 남지 않으므로 기록 없이 사라지면 취소됨: job.deleted)
      const job = s.job;
      if (!job) return false;
      if (!(await place(job)) || job.ahead === null || job.running) return false;
      await comfy("queue", { delete: [job.pid] });
      job.deleted = true;
      const seen = await place(job);
      if (s.job !== job) return false;
      if (!seen) return null;
      if (job.ahead !== null) {
        job.deleted = false; // 지우기 직전에 실행을 시작함(delete 는 대기 중 항목만 지움) — 끝까지 받는다
        return false;
      }
      const h = await record(job.pid);
      if (s.job !== job) return false;
      if (h === undefined) return null;
      if (h) {
        finish(job, endOf(h), h);
        return false;
      }
      finish(job, { state: "cancelled", msg: "대기 중에 취소함", export: null });
      return true;
    };

    s.loadPlan = (only, style) => {
      // GET /pipeline/plan(only = 정규 only 문자열, null = only 없는 계획 — 칩용). 이 Studio 가 낸 프롬프트가 도는 중이면 부르지 않는다(Import 뒤·
      // Prepare 전의 반쪽 상태) → 받아들였으면 true. 같은 only·style 요청이 아직 진행 중이면 겹쳐 묻지 않고 그 응답을 받은 뒤 한 번 더 묻는다 —
      // 겹쳐 물으면 뒤 요청이 앞 요청의 계산을 202 로 기다리다 지치거나 앞 정상 응답을 버리고(설정 저장 한 번에 나가는 두 요청), 앞 응답만 함께
      // 받으면 그 서명이 이 부름의 이유(설정·스펙 쓰기)보다 먼저 잡혔을 수 있다(낡은 계획 — state plan_v 가 null 로 남아 폴링도 다시 부르지
      // 않음). 기다리는 부름은 모두 같은 다음 요청을 받고, 그 사이 다른 선택의 요청이 나갔으면 다시 묻지 않는다(false)
      if (s.job) return Promise.resolve(false);
      const k = `${only || ""}|${!!only && !!style}`;
      if (planReq && planReq.key === k) {
        const prev = planReq;
        prev.next = prev.next || prev.p.then(() => (planSeq === prev.seq ? s.loadPlan(only, style) : false));
        return prev.next;
      }
      const p = requestPlan(only, style); // 요청 세대(planSeq)를 올림
      const req = { key: k, p, seq: planSeq, next: null };
      planReq = req;
      req.p.then(() => {
        if (planReq === req) planReq = null;
      });
      return req.p;
    };

    async function requestPlan(only, style) {
      // 요청 세대로 낡은 응답 버림, 202(다른 계산 중 — 다른 창, 또는 이 창이 앞서 보내고 버린 다른 선택의 계산: 재시작 직후 첫 계산은 1–2초) =
      // BUSY_MS 간격으로 다시(요청 BUSY_TRIES 번까지)
      const seq = ++planSeq;
      const q = only ? { only, style: style ? "1" : "0" } : {};
      for (let i = 1; ; i++) {
        let d;
        try {
          d = await call("pipeline/plan?" + qs(q));
        } catch (e) {
          if (seq === planSeq) s.planErr = e.message;
          return false;
        }
        if (seq !== planSeq) return false;
        if (!d.busy) {
          s.plan = d;
          s.planOnly = only || null;
          s.planStyle = !!only && !!style;
          s.planErr = null;
          return true;
        }
        if (i >= BUSY_TRIES) {
          s.planErr = "다른 계획 계산이 끝나지 않았습니다 — 다시 계산";
          return false;
        }
        await sleep(BUSY_MS);
        if (seq !== planSeq || s.job) return false; // 기다리는 사이 이 Studio 의 무료 큐가 시작됨 — 반쪽 상태 계획을 받지 않음(끝나면 onDone 이 다시)
      }
    }

    s.loadProgress = async () => {
      // → 진행 수가 바뀌었는가(같으면 화면이 다시 그리지 않는다 — 폴링마다 그리면 누르는 중인 단추가 갈린다)
      let d = null;
      try {
        d = await call("pipeline/progress?" + qs({}));
      } catch (e) {
        d = null;
      }
      const changed = JSON.stringify(d) !== JSON.stringify(s.progress);
      s.progress = d;
      return changed;
    };

    s.draft = (crop) => call("draft?" + qs({ crop }));

    s.preview = (dv, text) => call("draft/adopt", { crop_id: dv.id, op: "set", text, seen: dv.seen, crop_sig: dv.crop_sig, preview: true });

    s.adopt = async (dv, op, text, ok) => {
      // 채택·수정·해제(§3.4·§2.5): 미리보기(preview — 쓰지 않음) → ok(미리보기 응답) 가 참이면 쓰기. seen·crop_sig 는 GET /draft 가 준 그대로.
      // → 쓰기 응답 | null(확인에서 멈춤). 400·409 는 예외 — 409(스펙·크롭·채택 값이 바뀜)면 다시 읽은 GET /draft 를 e.view 에 싣는다
      if (s.busy) throw new Error("앞 저장을 처리하는 중입니다 — 끝난 뒤 다시 하세요");
      const body = { crop_id: dv.id, op, seen: dv.seen, crop_sig: dv.crop_sig };
      if (op === "adopt") body.value = dv.adopt.value;
      if (op === "set") body.text = text;
      s.busy = true;
      try {
        const pv = await call("draft/adopt", Object.assign({ preview: true }, body));
        if (!ok(pv)) return null;
        const r = await call("draft/adopt", Object.assign({ preview: false }, body));
        s.onWrite();
        return r;
      } catch (e) {
        if (e.status === 409) {
          try {
            e.view = await s.draft(dv.id);
          } catch (e2) {
            e.view = null;
          }
        }
        throw e;
      } finally {
        s.busy = false;
      }
    };

    s.layoutBack = async (rev, id, back) => {
      // 반영 칸 [되돌리기]: 그 크롭의 바뀐 이름·사각형(back)을 마지막 반영 값으로 — 레이아웃 쓰기(POST /layout/crop update + restore_manifest:
      // 매니페스트 값과 같은 이름·사각형은 규칙 검사를 건너뜀). rev = state layout_rev(그 사이 다른 창·편집기가 쓰면 409)
      if (s.busy) throw new Error("앞 저장을 처리하는 중입니다 — 끝난 뒤 다시 하세요");
      s.busy = true;
      try {
        const r = await call("layout/crop", Object.assign({ rev, op: "update", id, restore_manifest: true }, back));
        s.onWrite();
        return r;
      } finally {
        s.busy = false;
      }
    };

    return s;
  }

  // ── ComfyUI 탭 연결(D6)·유료 승인 메시지(S3b 계약 §3·§5) ──
  function link(win, onChange, every = HELLO_MS, wait = REPLY_MS, ackMs = ACK_MS, endMs = END_MS) {
    // every 마다 hello {type, id}: opener(Open Board 또는 메뉴 BMK → Design Patch Studio 로 연 창)가 있으면 그 탭의 응답만, 없으면 BroadcastChannel("bmk-dp") 로 묻고 wait 동안 모은
    // 응답의 탭이 정확히 하나일 때만 연결. 같은 출처·이번 hello 의 id 만 받는다. state = wait(첫 판정 전)·ok·old(opener 가 답하지 않음 —
    // 옛 확장)·none·many. 응답 = {tab(탭 로드마다 새 ID), ext(확장 판), submit(승인 제출 가능 ok|unsupported), busy(탭이 큐 처리 중)}.
    // paidWhy() = 유료 승인을 못 하는 이유("" = 유료 가능: 연결·ext 3 이상·submit ok·busy 아님 — 머리줄 배지 제목(board.js tabTitle)도 이 문장).
    // send(msg, late, sent) = 지금 연결된 길(opener·채널)로 msg + {id, tab} → 결과 promise: 탭의 approved 그대로, 또는 Studio 가 끝낸 {ok: false, reason, error}
    // (no_ack = ackMs 안에 ack 없음 · timeout = endMs 안에 결과 없음 · tab_changed = 기다리는 사이 탭이 바뀜·끊김 · unsent = 보내지 못함).
    // 답은 같은 id·같은 tab·같은 길에서 온 것만. Studio 가 끝낸 요청에 늦게 온 approved 는 late(답) 으로 넘긴다(이미 큐에 들어간 실행을 놓치지 않게).
    // sent({id, tab, via}) = 보낸 직후(호출자가 창을 새로 열어도 이어 받게 남김), adopt({id, tab, via}, late) = 앞 창이 보내고 결과를 모르는 요청의
    // 늦은 approved 를 이 창에서 late 로 받음. onWave(fn) = 연결된 탭(tab 일치)이 보낸 wave
    const st = { state: "wait", tab: null, ext: null, submit: null, busy: false, via: null, reviewRoots: null };
    const Channel = win.BroadcastChannel;
    const ch = typeof Channel === "function" ? new Channel(CHANNEL) : null;
    let round = null;
    const asks = new Map(); // 보낸 승인 id → {id, tab, via, acked, done, resolve, late, timers}
    let wave = () => {};

    function take(d, via) {
      if (!d || typeof d !== "object") return;
      if (d.type === HELLO) {
        if (round && via === round.via && d.id === round.id && typeof d.tab === "string") round.replies.set(d.tab, d);
      } else if (d.type === WAVE) {
        if (st.state === "ok" && d.tab === st.tab) wave(d);
      } else if (d.type === ACK || d.type === APPROVED) {
        const a = asks.get(d.id);
        if (!a || d.tab !== a.tab || via !== a.via) return;
        if (d.type === ACK) a.acked = true;
        else if (!a.done) end(a, d);
        else {
          asks.delete(a.id);
          if (a.late) a.late(d);
        }
      }
    }

    function end(a, res) {
      // 요청 하나를 한 번만 끝냄. 탭의 답(approved)으로 끝나면 잊고, Studio 가 끝낸 것(시간·탭 바뀜)은 늦게 올 approved 를 위해 남긴다
      if (a.done) return;
      a.done = true;
      for (const t of a.timers) clearTimeout(t);
      if (res.type === APPROVED) asks.delete(a.id);
      a.resolve(res);
    }

    win.addEventListener("message", (ev) => {
      if (ev.origin === win.location.origin && ev.source && ev.source === win.opener) take(ev.data, "opener");
    });
    if (ch) ch.onmessage = (ev) => take(ev.data, "channel");

    function settle(r) {
      const got = [...r.replies.values()];
      const d = got.length === 1 ? got[0] : null;
      const reviewRoots = d && Array.isArray(d.reviewRoots) ? [...new Set(d.reviewRoots.filter((k) => typeof k === "string" && /^[0-9a-f]{12}$/.test(k)))].sort() : null;
      const next = d ? { state: d.ext >= 2 ? "ok" : "old", tab: d.tab, ext: d.ext, submit: d.submit, busy: !!d.busy, via: r.via, reviewRoots }
        : { state: r.via === "opener" ? "old" : got.length ? "many" : "none", tab: null, ext: null, submit: null, busy: false, via: r.via, reviewRoots: null };
      const changed = Object.keys(next).some((k) => k === "reviewRoots" ? JSON.stringify(st[k]) !== JSON.stringify(next[k]) : st[k] !== next[k]);
      Object.assign(st, next);
      // 기다리던 요청의 탭이 아니게 됨(새로고침 = 새 tab ID·닫힘·끊김) — 결과를 모름(이미 큐에 들어갔을 수 있음)
      for (const a of asks.values()) {
        if (!a.done && a.tab !== st.tab) end(a, { ok: false, reason: "tab_changed", error: "ComfyUI 탭이 바뀜(새로고침·닫힘·연결 끊김) — 결과 모름: ComfyUI 큐를 확인" });
      }
      if (changed) onChange(st);
    }

    st.paidWhy = () => {
      // old = opener 가 있는데 답하지 않음(ext null — 옛 확장·새로고침 중·다른 주소로 옮겨 감) 또는 확장 판 < 2
      if (st.state === "old") {
        return st.ext === null ? "이 창을 연 ComfyUI 탭이 hello 에 답하지 않음(확장이 옛 판·새로고침 중, 또는 그 탭이 다른 주소·페이지로 옮겨 감) — "
          + "그 탭을 새로고침하거나 Open Board 또는 메뉴 BMK → Design Patch Studio 로 다시 열기"
          : `이 창을 연 ComfyUI 탭의 확장이 옛 판(${st.ext}) — 그 탭을 새로고침한 뒤 유료 승인`;
      }
      if (st.state !== "ok") return "ComfyUI 탭 연결 없음 — Review 노드의 Open Board 또는 메뉴 BMK → Design Patch Studio 로 열거나 ComfyUI 탭을 하나만 열어 두기";
      if (st.ext < 3) return `ComfyUI 탭의 확장이 옛 판(S3a — 판 ${st.ext}) — 그 탭을 새로고침한 뒤 유료 승인`;
      if (st.submit !== "ok") return "ComfyUI 탭이 승인 제출을 못 함(submit unsupported — ComfyUI 프론트가 바뀜) — 그래프의 승인 버튼 사용";
      // 할 일('잠시 뒤 다시')은 붙이지 않음 — 재굴림 확인 창·예약 토스트에도 실려 R 재누름(+1 사전 승인)으로 읽히지 않게(단추는 끝나면 저절로 켜짐)
      if (st.busy) return "ComfyUI 탭이 큐를 처리하는 중";
      return "";
    };

    st.send = (msg, late, sent) => new Promise((resolve) => {
      const a = { id: Math.random().toString(36).slice(2), tab: st.tab, via: st.via, acked: false, done: false, resolve, late, timers: [] };
      const m = Object.assign({ id: a.id, tab: a.tab }, msg);
      asks.set(a.id, a); // 보내기 전에 — 답이 postMessage 안에서 바로 와도 받게
      try {
        if (a.via === "opener") win.opener.postMessage(m, win.location.origin);
        else ch.postMessage(m);
      } catch (e) {
        asks.delete(a.id);
        resolve({ ok: false, reason: "unsent", error: `ComfyUI 탭에 보내지 못함(${e.message}) — 보내지 않음(과금 없음)` });
        return;
      }
      if (sent) sent({ id: a.id, tab: a.tab, via: a.via });
      a.timers = [
        setTimeout(() => a.acked || end(a, { ok: false, reason: "no_ack", error: "ComfyUI 탭 응답 없음 — ComfyUI 탭 새로고침(결과가 늦게 오면 이어서 봄)" }), ackMs),
        setTimeout(() => end(a, { ok: false, reason: "timeout",
          error: "ComfyUI 탭에서 결과를 받지 못함 — 이미 큐에 들어갔을 수 있으니 ComfyUI 큐를 확인(다시 보내지 않음)" }), endMs),
      ];
    });

    st.onWave = (fn) => {
      wave = fn;
    };

    st.adopt = (a, late) => {
      // 끝난 요청으로 등록(타이머·탭 바뀜 판정 없음) — 같은 id·tab·길의 approved 가 오면 late 로
      asks.set(a.id, { id: a.id, tab: a.tab, via: a.via, acked: true, done: true, resolve() {}, late, timers: [] });
    };

    function ping() {
      const op = win.opener && !win.opener.closed ? win.opener : null;
      const r = { id: Math.random().toString(36).slice(2), via: op ? "opener" : ch ? "channel" : null, replies: new Map() };
      round = r;
      try {
        if (op) op.postMessage({ type: HELLO, id: r.id }, win.location.origin);
        else if (ch) ch.postMessage({ type: HELLO, id: r.id });
      } catch (e) {
        // opener 가 다른 출처로 바뀜 — 응답 없음으로
      }
      setTimeout(() => settle(r), wait);
    }

    ping();
    const timer = setInterval(ping, every);
    st.stop = () => {
      clearInterval(timer);
      if (ch) ch.close();
    };
    return st;
  }

  // ── 화면 ──
  function create(hosts, opts) {
    // opts: apiUrl·comfyUrl·root·toast, refresh(보드 state 전체 다시), select(crop_id — 뷰어 맞춤), goTarget(타깃 키 — 검토로),
    // goMask(mask, 타깃 키)(★ 점검 S4 줄 — 마스크 모드 그 자리), seams() → 크롭별 이음매, drain(), enter()(진행 모드로),
    // link(이 창의 link — 유료 승인 메시지·wave. 없으면 유료 단추 끔)
    const { list, panel, head } = hosts;
    const L = opts.link || null;
    let store = null;
    try {
      store = window.sessionStorage;
    } catch (e) {
      // 저장소 막힘 — 다시 연 창에서 이어 받지 못할 뿐
    }
    const PS = session(opts.apiUrl, opts.comfyUrl, opts.root, (u, o) => fetch(u, o), store);
    let on = false;
    let S = null; // 보드 state
    let stateReads = 0; // refresh 가 오류를 삼켜도 새 state 수신 여부로 확인(unchanged 응답도 stateChanged 를 부름)
    let seenPl; // 마지막으로 맞춘 state pipeline_rev(undefined = 아직)
    let seenRev = null; // 채택 패널을 맞춘 매니페스트 rev
    let seenKey = null; // 마지막으로 받은 state 의 rev·실행 중·Drain·설정 rev·계획 서명
    let seenPlanKey = null; // 진행 모드에서 마지막으로 본 state plan_v·실행 중(busyNow) — 바뀐 때만 낡은 계획을 다시 받는다
    let ticks = 0; // 진행 모드에서 받은 state 수(진행 수 다시 읽기 간격)
    const sel = new Set(); // 선택(only) crop_id — 기본 = 전부(새로 보이는 크롭은 더함)
    const known = new Set();
    let style = false; // 초안 화풍 단위 체크 — 계획에 싣는 값은 styleNow()(apply = full 일 때만)
    let cur = null; // 채택 패널 크롭
    let dv = null; // GET /draft(cur)
    let dvErr = null;
    let dvSeq = 0;
    let text = null; // [수정] 입력 중인 값(null = 입력칸 닫힘)
    let pv = null; // [수정] 미리보기 {r} | {err}
    let pvSeq = 0;
    let pvTimer = null;
    let planTimer = null;
    let settingsOn = false;
    let setupPending = null; // null = 첫 진입 전, true = 판정 가능할 때까지 기다림, false = 판정했거나 이미 물음
    let setupChecking = false; // 자동 사전 읽기만 겹침 방지 — 사용자 동작의 asking·그리기는 건드리지 않음
    let setupInputWait = false; // 입력·포인터 동작 때문에 보류한 질문은 다음 enter 에서만 재개
    let setupLastTry = -Infinity; // rev 가 그대로인 폴링에서 보류 사유를 다시 확인하는 간격(10초)
    let form = {}; // 설정 패널에서 고치는 중인 값 {절: {키: 값}} — 다시 그려도 남는다
    // 다른 창의 저장 알아보기(state pipeline_rev 로 다시 읽으면 PS.P·rev 가 새 판이 되어 저장이 409 없이 통과): edits = 손댄 키를 처음 고칠 때 화면의 값
    // {절: {키: 값}}, drawn = 설정 패널을 마지막으로 그린(또는 저장한) 절 값 {절: 값}(입력 중이면 다시 그리기가 미뤄져 화면이 옛 값)
    let edits = {};
    let drawn = {};
    let composing = false;
    let pendingPanel = false; // 다시 그리기를 미룸(입력 중이면 패널만, 포인터가 눌린 동안이면 전부) — focusout·pointerup 뒤에 그림
    let held = false; // 포인터가 눌린 동안
    let seenLoad = null; // GET /pipeline 을 맞춘 판(loadKey — 매니페스트 rev·pref_v) — 그래프 Prepare·Import 는 pipeline_rev 를 두고 매니페스트(prepare_settings·source)만 바꿈
    let warned = []; // 마지막 확인 창에 보인 갈라짐 줄(applyWarn)
    let asking = false; // 확인 창을 만드는 동작(once — 반영·매니페스트 값으로 다시)이 도는 중(연타 막음)
    let retried; // 받은 계획이 이미 낡아 한 번 더 부른 state plan_v(planNow — 같은 값에 되풀이하지 않음)
    let doneReplan = false; // 이 Studio 의 큐가 끝남 — 다음 state(반영이 크롭을 더하고 뺀 선택)를 받은 뒤 계획을 다시(§3.1)
    let paying = null; // 확인·결과를 기다리는 유료 승인 stage(run·draft·reroll) — 이 Studio 는 유료 요청을 하나씩만
    // 이 Studio 의 유료 Run·재굴림 실행이 wave 로 멈춤 {stage, pid, remaining} — 끝난 뒤 Run 줄(D9: run = '이어서 승인' — 클릭만, reroll = 사전 승인분이라
    // 'ComfyUI 큐'로 이어서). 다음 Run·재굴림 승인을 보낼 때 지움(Run 템플릿은 사전 승인 재굴림도 함께 쏨)
    let wave = null;
    let replanned = null; // 결과가 '계획 다시'(REPLAN)였던 승인 때의 계획 — 다시 받은 계획(새 객체)이 올 때까지 그 해시로 승인 끔
    // 보내고 결과를 아직 모르는 승인(stage → [{id, tab, via, hash, lost}] 보낸 차례 — sessionStorage 에도: 기다리는 사이·결과 모름으로 끝난 뒤 이 창을
    // 새로 열어도(새로고침) 결과 모름 표시·늦은 approved 이어 받기가 남게). lost = 결과를 모르고 끝남(UNKNOWN — 이미 큐에 들어갔을 수 있음): 같은 해시를
    // 다시 승인하면 확인 창에 한 줄, 진행 패널에 줄. 그 요청의 결과를 알면 지우고(answered — 늦게 온 unknown 은 아직 모름), 제출(ok)이면 그보다 먼저 보낸
    // 것까지(대기열은 차례대로라 그 실행이 끝날 때 앞 요청도 끝남). 다른 요청의 거절은 앞 요청에 대해 아무것도 말하지 않아 둔다
    const asked = {};
    const ASK_KEY = "bmk-dp-ask:" + opts.root;
    let ask = null; // mock 확인 상자 {box, close}(열려 있을 때)
    let maskLeft = null; // 이 창의 적용 안 한 마스크 편집(board.js 가 K 모드를 나올 때 setMaskLeft — ★ 점검 줄)

    function el(tag, cls, txt) {
      const e = document.createElement(tag);
      if (cls) e.className = cls;
      if (txt !== undefined && txt !== null) e.textContent = String(txt);
      return e;
    }

    function chip(txt, cls) {
      return el("span", "chip " + (cls || ""), txt);
    }

    function button(label, title, fn, cls) {
      const b = el("button", cls || "", label);
      b.type = "button";
      b.tabIndex = -1;
      if (title) b.title = title;
      b.addEventListener("click", (e) => {
        e.currentTarget.blur();
        fn(e); // 유료 승인 단추는 이벤트로 사용자 클릭(isTrusted)을 가린다
      });
      return b;
    }

    function line(parent, txt, cls) {
      const r = el("div", "pp-row " + (cls || ""), txt);
      parent.append(r);
      return r;
    }

    function btns(parent, ...bs) {
      const b = el("div", "lp-btns");
      b.append(...bs);
      parent.append(b);
      return b;
    }

    function section(title) {
      const s = el("div", "pp-sec");
      s.append(el("div", "lp-k", title));
      panel.append(s);
      return s;
    }

    async function copy(txt) {
      try {
        await navigator.clipboard.writeText(txt);
        opts.toast(`복사: ${txt}`);
      } catch (e) {
        opts.toast(`복사하지 못함 — 직접 복사: ${txt}`, "warn");
      }
    }

    function onlyNow() {
      // 정규 only(pipeline.only_string — 정렬한 crop_id 쉼표 목록). 선택 0개 = null(빈 only 는 '전부'라 계획·승인을 끔)
      return sel.size ? [...sel].sort().join(",") : null;
    }

    function styleNow() {
      // only 에 style 토큰을 더하는가(§2.3 — 화풍 체크이고 초안 apply = full 일 때만, 서버도 그 밖에는 무시)
      return style && !!(PS.P && PS.P.settings && PS.P.settings.draft.apply === "full");
    }

    function planNow() {
      // 지금 선택의 계획 — 진행 모드가 아니면 only 없는 계획(칩용). 진행 모드에서 받은 계획이 서버가 기억한 계획(state plan_v)과 이미 다르면(계산 중
      // Run·Spec Draft 가 시작했다 끝남 — 서버는 그 계획을 기억하지 않고 '실행 중' 서명을 줌, plan_v 는 그대로라 폴링이 다시 부르지 않음) 쉬는 중이면
      // 한 번 더 — 같은 plan_v 에 한 번만. plan_v null(기억 없음·계획 오류)은 다음 폴링의 plan_v 가 정한다
      return PS.loadPlan(on ? onlyNow() : null, styleNow()).then((ok) => {
        if (ok && on) {
          if (!planStale()) retried = undefined;
          else if (S.plan_v && !busyNow() && retried !== S.plan_v) {
            retried = S.plan_v;
            schedulePlan();
          }
        }
        render();
      });
    }

    function schedulePlan() {
      clearTimeout(planTimer);
      planTimer = setTimeout(planNow, PLAN_MS);
    }

    function pendingApply() {
      // 반영 필요 사유: 레이아웃 미반영(state — 편집 즉시 보임) + 계획의 apply_pending(서버의 '반영 필요' 판정 하나 — Import 미반영·Prepare
      // 입력 지문 불일치) + draft.import_pending(같은 Import 사유). state 가 미반영 크롭을 세면 계획의 레이아웃 사유는 같은 원인이라 한 번만
      // 센다(계획 쪽은 layout.json 에만 있는 새 크롭도 봄)
      const out = [];
      const lay = S ? S.crops.filter((c) => LAYOUT_PENDING.includes(c.layout_status)).length : 0;
      if (lay) out.push(`${LAYOUT_REASON} ${lay}`);
      const p = PS.plan;
      const from = ((p && p.apply_pending) || []).concat((p && p.draft && p.draft.import_pending) || []);
      for (const r of from) if (!out.includes(r) && !(lay && r.startsWith(LAYOUT_REASON))) out.push(r);
      return out;
    }

    function pendingUnknown() {
      // 계획의 import_pending(레퍼런스·채택·Import 설정 미반영)을 모르는 이유 — "" = 앎. 계획을 아직 못 받았거나 초안 계획이 실행 중·오류면
      // (정본 alt draft_running·draft_error·draft_qwen_nodir 에는 import_pending 키가 없다) '미반영 없음'으로 읽지 않는다
      const d = PS.plan && PS.plan.draft;
      if (!d) return PS.planErr ? `계획 오류 — ${PS.planErr}` : "계획을 받는 중";
      if (Array.isArray(d.import_pending)) return "";
      return d.running ? "Spec Draft 실행 중" : `초안 계획 오류 — ${d.error || "반영 판정 없음"}`;
    }

    function planOkNow() {
      // 지금 선택·화풍으로 받은 계획이고 마지막 요청이 실패하지 않았음 — 계획 줄·해시를 보임(실패한 다시 계산 옆에 옛 해시를 두지 않음)
      return !!PS.plan && !PS.planErr && PS.planOnly === onlyNow() && PS.planStyle === styleNow();
    }

    function planDim() {
      // 해시를 흐리게(반영 뒤 다시 계산 — 복사·승인 끔): 실행 설정 모름(GET /pipeline 을 아직 못 읽음·마지막 읽기 실패 — 확인 남음·갈라짐을 모름)·
      // 반영 필요(apply_pending — prepare_inputs 불일치 포함)·반영 상태 모름(초안 계획 실행 중·오류)·설정 확인 남음·Prepare 갈라짐·계획 notes(서버의
      // 설정 쪽 '반영 뒤 다시 계산' 사유)
      const P0 = PS.P;
      return !P0 || !!PS.err || pendingApply().length > 0 || !!pendingUnknown() || confirmOf(P0).length > 0 || P0.drift.some((x) => x.section === "prepare")
        || (!!PS.plan && PS.plan.notes.length > 0);
    }

    function copyHashes() {
      // [해시 복사] 두 단추가 지금 복사할 값 {draft, run} = [해시("" = 끔), only] — 지금 선택의 계획이고 흐림·낡음(stale)·실행 중이 아닐 때만.
      // 그릴 때와 누를 때 같은 판정
      const p = PS.plan;
      const ok = planOkNow() && !!onlyNow() && !planDim() && !planStale();
      const dHash = ok && !draftText(p.draft, p.draft.running || !!(S && S.drafting)).dim && !(p.draft.import_pending || []).length ? p.draft.hash : "";
      const rHash = ok && !runText(p.run, p.run.running || !!(S && S.running)).dim ? p.run.hash : "";
      return { draft: [dHash || "", PS.planStyle ? `${PS.planOnly},style` : PS.planOnly], run: [rHash || "", onlyNow()] };
    }

    function planStale() {
      // 받은 계획이 지금 파일 상태와 다름(다른 창·그래프의 쓰기·실행) — state plan_v(only 없는 계획 서명 'r:…|d:…') ≠ 계획 sig 의 '|o:' 앞
      return !!PS.plan && !!S && S.plan_v !== PS.plan.sig.split("|o:")[0];
    }

    function unsureWhy() {
      // 머리줄·반영 칸·★ 점검이 '미반영 없음'이라 말하지 못하는 이유("" = 말할 수 있음): 반영 판정을 모름, 또는 받은 계획이 지금 파일과 다름
      return pendingUnknown() || (planStale() ? "계획이 지금 파일 상태와 다름" : "");
    }

    function applyWarn() {
      // 반영 확인 창 맨 앞 줄(§3.2): Prepare 설정 갈라짐(§2.2 — 다시 과금 대상(셀이 바뀜·셀당 호출을 늘림) 먼저), 그 뒤 Import 갈라짐(다음 반영이
      // 크롭 소스·레퍼런스를 바꿈 — cell_key 가 바뀔 수 있음). 보인 줄을 기억한다(편집기 확인 창은 이 값을 동기로 쓴다 — 큐 직전 checkWarn 이 새 줄만
      // 한 번 더 묻는다)
      const d = PS.P && PS.P.drift ? PS.P.drift : [];
      const pre = d.filter((x) => x.section === "prepare");
      warned = pre.filter((x) => DRIFT_BILL.includes(x.kind)).concat(pre.filter((x) => !DRIFT_BILL.includes(x.kind)), d.filter((x) => x.kind === "import"))
        .map(driftText);
      return warned;
    }

    async function reload(quiet = false) {
      // GET /pipeline 을 다시 읽음 → 지금 PS.P 가 이 읽기 뒤의 값인가(늦게 보낸 읽기가 먼저 받아진 것도 됨). 읽지 못하면 토스트·false — 옛 PS.P 로
      // 확인 창을 만들거나 '새 갈라짐 없음'으로 판정하지 않게(반영·매니페스트 값으로 다시)
      if ((await PS.load()) || !PS.err) return true;
      if (!quiet) opts.toast(`실행 설정(GET /pipeline)을 다시 읽지 못해 멈춤 — ${PS.err}`, "err");
      return false;
    }

    async function checkWarn() {
      // 확인 창을 띄운 뒤(편집기 확인 창은 이 창이 마지막으로 읽은 GET /pipeline 으로 만든다) 그래프 Prepare·Import 로 갈라짐이 새로 생겼으면
      // 큐에 넣기 전에 묻는다 — 서버 큐 게이트는 갈라짐을 보지 않는다. 묻는 동안 또 바뀔 수 있어 새 줄이 없을 때까지 다시 읽고 묻는다.
      // → {sig: 마지막 갈라짐 점검을 통과한 '<pipeline rev>:<매니페스트 rev>'}(큐의 expect — 그 뒤 다른 창의 저장·그래프 실행이 바꾸면 큐가 거절) |
      // null(거절·다시 읽지 못함)
      for (;;) {
        const shown = warned;
        if (!(await reload())) return null;
        const sig = `${PS.P.rev}:${PS.P.manifest_rev}`;
        const extra = applyWarn().filter((x) => !shown.includes(x));
        if (!extra.length) return { sig };
        if (!window.confirm(["확인 창을 띄운 뒤 설정 갈라짐이 새로 생겼습니다(그래프 Prepare·Import):", ...extra, "그래도 반영할까요?"].join("\n"))) return null;
      }
    }

    function confirmOf(P0) {
      // 확인 필요 항목. 깨진 pipeline.json(confirm null)은 덮어쓸 때 서버가 싣는 derived.confirm(§2.1 — 저장한 절만 지움)이라 그 값으로 보인다
      // (설정 칸 표시·'확인·저장'·[매니페스트 값으로 다시] 확인 창이 추정값을 추정값으로)
      if (!P0) return [];
      return (P0.error ? P0.derived.confirm : P0.confirm) || [];
    }

    function confirmGate(P0) {
      // 확인 필요를 서버 prompt_gate 판정대로 가름 → {gate: 반영·Export 를 막는 항목, rest: 막지 않는 항목(해시만 흐림), noFile: 파일 없음(그것만으로
      // 막음)}. 깨진 파일은 pipeline_broken 으로 전부 막고, 아니면 prepare.*·import.* 만 막는다 — 늘 남는 draft.apply·기록 없는 analyze·
      // compose·export 를 '막음'이라 하지 않게
      const conf = confirmOf(P0);
      const gate = P0 && P0.error ? conf : conf.filter((c) => GATE_SECTIONS.includes(c.split(".")[0]));
      return { gate, rest: conf.filter((c) => !gate.includes(c)), noFile: !!P0 && !P0.error && !P0.saved };
    }

    function busyNow() {
      // Spec Draft 실행 = state drafting(폴링마다 — unchanged 응답에도) 또는 progress.draft(옛 서버 — 쉬는 중엔 state 5번에 1번 읽음)
      const p = PS.progress;
      return !!((S && (S.running || S.drafting)) || PS.job || (p && (p.running || p.draft)));
    }

    function setupBusy() {
      return busyNow() || paying || PS.busy || PS.sending || (L && L.busy);
    }

    async function setupQueueIdle() {
      // 세션의 comfy("queue")·place 와 같은 경로. Run 이 아닌 Import·Prepare 그래프도 대기열로 확인한다.
      try {
        const r = await fetch(opts.comfyUrl("queue"), { cache: "no-store" });
        const q = r.ok ? await r.json() : null;
        return !!q && Array.isArray(q.queue_running) && Array.isArray(q.queue_pending)
          && !q.queue_running.length && !q.queue_pending.length;
      } catch (e) {
        return false; // 읽지 못함 = 아직 판정할 수 없음
      }
    }

    function setupReady(P0) {
      return on && !settingsOn && P0 && !PS.err && !P0.error && !P0.saved && !confirmGate(P0).gate.length
        && !P0.warnings.length && !setupBusy();
    }

    async function firstSetup() {
      if (!setupPending || !on || setupChecking) return;
      if (document.hidden) {
        setupLastTry = -Infinity;
        return;
      }
      setupLastTry = Date.now();
      if (!PS.P) return;
      if (PS.P.saved || settingsOn) {
        setupPending = false;
        return;
      }
      // 검토 모드의 옛 gate·경고·읽기 오류로 기회를 소진하지 않음 — 파일 있음과 이 페이지의 동작 상태만 먼저 본다.
      if (held || panelBusy()) setupInputWait = true;
      if (setupInputWait || asking || paying || PS.busy || PS.sending || PS.job || (L && L.busy)) return;
      setupChecking = true;
      const errorBefore = PS.err, progressBefore = JSON.stringify(PS.progress);
      let readyToAsk = false;
      try {
        // 바쁜 큐는 가벼운 읽기만으로 보류. 비었을 때만 깊은 유도(GET /pipeline)·progress 를 새로 받아 판정한다.
        const idle = await setupQueueIdle();
        if (held || panelBusy()) setupInputWait = true;
        if (!setupPending || !on || !idle || setupInputWait) return;
        const [loaded] = await Promise.all([reload(true), PS.loadProgress()]);
        if (held || panelBusy()) setupInputWait = true;
        if (document.hidden) {
          setupLastTry = -Infinity;
          return;
        }
        if (!setupPending || !on || !loaded || PS.err || !PS.progress || setupBusy() || setupInputWait || asking) return;
        if (!setupReady(PS.P)) {
          setupPending = false; // 파일·gate·경고 판정은 기회 소진
          return;
        }
        readyToAsk = true;
      } finally {
        setupChecking = false;
        // 질문은 once 가 다시 그림. 보류·판정 종료는 오류 회복·진행 변경이 있을 때만 낡은 표시를 갱신한다.
        if (!readyToAsk && ((errorBefore && !PS.err) || progressBefore !== JSON.stringify(PS.progress))) render();
      }
      return once(async () => {
        setupPending = false; // 실제 질문부터 사용자 동작을 잠그고 이 페이지에서 다시 묻지 않음
        const P0 = PS.P;
        // 저장 중 다시 읽기·다른 창의 변경과 무관하게 확인 창에 보인 값만 보냄(파일 없음의 settings = 유도값).
        const values = { import: Object.assign({}, P0.settings.import), prepare: Object.assign({}, P0.settings.prepare) };
        const lines = ["그래프에서 마지막으로 돈 설정으로 시작할까요?"];
        for (const sec of ["prepare", "import"]) {
          lines.push(`${SECTIONS[sec]}:`);
          for (const [k, v] of Object.entries(values[sec])) {
            const from = P0.derived.from[`${sec}.${k}`];
            lines.push(` · ${sec}.${k} = ${shown(v)} (${FROM[from] || from || "출처 모름"})`);
          }
        }
        const rest = confirmGate(P0).rest;
        if (rest.length) lines.push(`확인 필요로 남음(반영·Export 는 막지 않음): ${rest.join(", ")}`);
        lines.push("저장하면 pipeline.json 이 만들어져 [반영 (무료)]·Export 를 쓸 수 있습니다. 나중에 [실행 설정 열기]에서 바꿀 수 있습니다.");
        if (!window.confirm(lines.join("\n"))) return;
        const notSaved = "실행 설정을 저장하지 않았습니다 — [실행 설정 열기]에서 확인";
        const before = stateReads;
        let refreshed = false;
        try {
          await opts.refresh();
          refreshed = stateReads !== before;
        } catch (e) {
          // 보드 state 를 못 읽으면 아래에서 큐·진행 읽기 실패와 같은 안내
        }
        const [, stillIdle] = await Promise.all([PS.loadProgress(), setupQueueIdle()]);
        if (!on || settingsOn) {
          opts.toast(notSaved, "warn");
          return;
        }
        if (!refreshed || !PS.progress || !stillIdle || setupBusy()) {
          opts.toast("ComfyUI 가 큐를 처리 중이거나 상태를 읽지 못해 저장하지 않았습니다 — 끝난 뒤 [실행 설정 열기]에서 확인", "warn");
          return;
        }
        // confirm 동안 그래프 Import·Prepare 가 돈 경우 rev 0 은 그대로여도 유도값은 달라진다.
        if (!(await reload())) return;
        const current = PS.P;
        let stopped = "";
        if (!on || settingsOn) stopped = notSaved;
        else if (current.error) stopped = `실행 설정 파일이 깨져 저장하지 않았습니다 — ${current.error}`;
        else if (current.saved) stopped = "다른 창이 먼저 실행 설정을 저장했습니다 — 저장하지 않음";
        else if (current.warnings.length || confirmGate(current).gate.length) {
          const reasons = current.warnings.map((w) => w.msg);
          const gate = confirmGate(current).gate;
          if (gate.length) reasons.push(`확인 필요: ${gate.join(", ")}`);
          stopped = `실행 설정을 저장하지 않았습니다 — ${reasons.join("; ")} — [실행 설정 열기]에서 확인`;
        } else if (!same(current.settings.import, values.import) || !same(current.settings.prepare, values.prepare)) {
          stopped = "확인 창을 띄운 동안 그래프 기록이 바뀌어 저장하지 않았습니다 — [실행 설정 열기]에서 확인";
        } else if (!setupReady(current)) stopped = notSaved;
        if (stopped) {
          opts.toast(stopped, "warn");
          return;
        }
        let rev = 0;
        const saved = [];
        for (const sec of ["import", "prepare"]) {
          try {
            const r = await PS.save(sec, values[sec], rev);
            rev = r.rev; // PS.save 뒤 GET /pipeline 의 rev 가 아니라 이 창의 저장 응답 rev
            saved.push(SECTIONS[sec]);
            // rev 0 저장 뒤 그래프 Import·Prepare 도 다시 대조 — 그 사이 끝난 그래프와 갈라진 채 다음 절을 쓰지 않음.
            if (sec === "import" && (PS.err || !PS.P || PS.P.error || !PS.P.saved || !same(PS.P.settings.prepare, values.prepare)
              || PS.P.drift.some((d) => (d.kind === "import" || d.section === "prepare") && !P0.drift.some((before) => same(before, d)))
              || !same(PS.P.derived.settings.import, values.import)
              || !same(PS.P.derived.settings.prepare, values.prepare) || confirmGate(PS.P).gate.length)) {
              opts.toast("Import·Prepare: 확인 창의 값과 지금 설정이 다르거나 다시 읽지 못해 멈춤 — 앞 절(Import PSD)만 저장됨, [실행 설정 열기]에서 확인", "warn");
              planNow();
              return;
            }
          } catch (e) {
            const importLost = sec === "import" && (!e.status || e.status >= 500);
            const importCreated = importLost && !PS.err && PS.P && PS.P.saved;
            opts.toast(`${SECTIONS[sec]}: ${e.message}`
              + (saved.length ? ` — 앞 절(${saved.join(", ")})은 저장됨, 남은 절은 [실행 설정 열기]에서 다시 확인` : "")
              + (importCreated ? " — Import 저장 응답을 받지 못했지만 pipeline.json 이 만들어짐 — [실행 설정 열기]에서 확인"
                : importLost && PS.err ? " — Import 저장 응답을 받지 못함 — pipeline.json 이 만들어졌을 수 있음, [실행 설정 열기]에서 확인" : ""), "err");
            if (saved.length || importCreated) planNow();
            return;
          }
        }
        opts.toast(`실행 설정을 저장했습니다(pipeline.json rev ${rev}) — [반영 (무료)]을 쓸 수 있습니다`);
        planNow();
        render();
      })();
    }

    function pipelineLoaded() {
      render();
      firstSetup();
    }

    function jobText(job) {
      return `${STAGES[job.stage].label}(${job.stage}) ${job.running ? "실행 중" : job.ahead === null ? "보냄" : `대기 — 앞 ${job.ahead}`}`;
    }

    // ── 동작 ──
    async function queue(stage, expect) {
      // 무료 큐 → {old: true}(옛 서버 — 알림·대신 반영은 호출자: board.js applyFree 는 탭 반영, 패널 단추는 queueHere) | undefined.
      // 결과·오류는 토스트, 설정 확인이 필요하면 설정 패널. expect = 확인한 '<pipeline rev>:<매니페스트 rev>'(PS.queue). 앞 큐 요청이 아직 가는 중이면(두 번 누름)
      // 조용히 버린다 — 앞 요청의 결과 토스트가 나온다. 유료 승인 결과를 기다리는 중이면 보내지 않는다(board.js 편집기 반영(큐)도 이 길 — 무료 큐가
      // 이 Studio 의 실행 자리를 먼저 차지하면 탭이 돌려준 prompt_id 를 이어 받지 못함)
      if (PS.sending) return undefined;
      if (paying) {
        opts.toast(`${STAGES[stage].label}: ${STAGES[paying].label} 승인 응답을 기다리는 중 — 끝난 뒤 다시`, "warn");
        return undefined;
      }
      const q = PS.queue(stage, expect);
      render(); // 템플릿 요청 동안(PS.sending) 승인 단추를 끈 모양으로 — Export 등 once 밖의 길도
      try {
        const r = await q;
        if (r.old) return r;
        opts.toast(`${STAGES[stage].label}: ComfyUI 대기열 ${STAGES[stage].front ? "앞" : "뒤"}에 넣음(무료 템플릿 — Run·Spec Draft 없음)`);
      } catch (e) {
        opts.toast(e.message, "err");
        const reason = e.data && e.data.reason;
        if (reason === "confirm_settings" || reason === "pipeline_broken") openSettings();
      } finally {
        render();
      }
      return undefined;
    }

    async function queueHere(stage, expect) {
      // 패널 단추(반영·Export)의 무료 큐 — 옛 서버면 여기서 한 번 알림(대신 반영할 탭 길은 board.js 에만 있음)
      if (await queue(stage, expect)) opts.toast("ComfyUI 재시작 필요 — 서버에 무료 템플릿 라우트가 없습니다(옛 버전)", "warn");
    }

    async function boardQueue(stage) {
      // board.js(편집기 반영(큐) → applyFree)가 부르는 큐 — 편집기가 이미 물은 뒤라 새 갈라짐만 한 번 더 묻는다(거절 = undefined: 호출자는 끝냄)
      if (stage !== "apply") return queue(stage);
      const ok = await checkWarn();
      return ok ? queue(stage, ok.sig) : undefined;
    }

    function once(fn) {
      // 확인 창을 만드는 동작(GET /pipeline 다시 읽기 → 확인 → 큐·저장)은 한 번에 하나 — 다시 읽는 사이의 연타가 확인 창을 둘 띄우지 않게.
      // 그동안 승인 단추는 끈 모양으로(paidLock — 켜진 채 남으면 누를 때 '계획·탭 상태가 바뀜'이 뜸), 끝나면 다시 그림(취소해도)
      return async () => {
        if (asking) return;
        asking = true;
        render();
        try {
          await fn();
        } finally {
          asking = false;
          render();
        }
      };
    }

    async function applyNow() {
      await opts.refresh(); // 크롭별 레이아웃 줄을 지금 state 로(편집(E) 직후 G 로 와 다음 폴링 전이어도 — 보드 refresh 는 도는 폴링을 기다림)
      if (!(await reload())) return; // 확인 창을 만들기 전에(state 폴링이 그래프 Prepare 를 알기 전이어도 지금 갈라짐으로)
      const n = S ? S.pending_preapproved || 0 : 0;
      const lays = S ? layoutLines(S) : [];
      const back = lays.some((x) => x.back) ? "반영 칸 [되돌리기] 또는 " : "";
      const lines = applyWarn().concat(lays.length ? [`${LAYOUT_REASON} ${lays.length}건 — 이 반영에 들어감(아니면 취소 → ${back}크롭 편집(E)):`,
        ...lays.map((x) => ` · ${x.text}`)] : [], "무료 반영(Import → Prepare → Analyze → Review 템플릿, 탭 불필요)을 ComfyUI 대기열 앞에 넣습니다.");
      if (n) lines.push(`다시 생성 대기 ${n}건(사전 승인)은 이 반영에서 돌지 않음 — R·[다시 생성 보내기]·그래프 Run 에서 과금`);
      if (!window.confirm(lines.concat(DRAFT_NOTE, "계속할까요?").join("\n"))) return;
      const ok = await checkWarn();
      if (ok) await queueHere("apply", ok.sig);
    }

    async function layoutBack(x, rev) {
      // 반영 칸 [되돌리기](layoutLines 의 back 이 있는 줄): 바뀐 이름·사각형을 마지막 반영 값으로 — 레이아웃만 쓰고 큐는 없음. rev = 그 줄을 그린 state 의
      // layout_rev(누르는 동안·입력 중 다시 그리기가 미뤄져 줄이 낡았으면 서버가 409 — 지금 rev 로 보내면 그 사이 편집을 덮음)
      const to = [x.back.name ? `이름 ${x.back.name}` : "", x.back.rect ? `위치 [${x.back.rect.join(", ")}]` : ""].filter(Boolean).join(" · ");
      // layout.json 을 다시 쓰므로 Prepare 입력 지문(파일 mtime)이 바뀌어 '반영 필요(Prepare 입력이 바뀜)'는 남는다 — 무료 반영 한 번. 이 크롭의 대상 문구
      // 바뀜(x.rest)은 되돌리지 않으므로 그때는 새 셀이 될 수 있다고(그 밖의 미반영 — 다른 크롭·채택 등 — 은 이 단추와 무관)
      const after = x.rest ? "이 크롭의 대상 문구 바뀜은 남음 — 반영하면 새 생성 조건(생성 승인 대상)이 될 수 있음(대상 문구는 크롭 편집(E)에서)."
        : "이 크롭의 이름·위치는 마지막 반영 그대로라 그것 때문에 새 생성 조건(생성 승인)이 생기지는 않음.";
      if (!window.confirm(`${x.text}\n\n${to} 으로 되돌립니다(마지막 반영 값 — 레이아웃만 쓰고 무료). ${after} 되돌린 뒤에도 '미반영(Prepare 입력이 바뀜)'은 `
        + "남으니 [반영 (무료)]을 한 번. 계속할까요?")) return;
      try {
        const r = await PS.layoutBack(rev, x.id, x.back);
        if (opts.layoutWrote) opts.layoutWrote(rev, r.rev); // 같은 창 편집기(E)의 Ctrl+Z 기록을 남김
        opts.toast(`되돌림: ${x.text.split(" — ")[0]} → ${to}`);
      } catch (e) {
        opts.toast(e.status === 409 ? `${e.message} — 다시 그린 줄에서 다시` : e.message, "err");
        opts.refresh();
      }
    }

    async function cancelJob() {
      try {
        // 그 사이 끝났으면(job 없음) 끝 알림(onDone — Export 결과 등)을 덮지 않는다. 끝을 모름(null)은 둘 — 지운 뒤 대기열을 읽지 못함(job 자리
        // 그대로 — 그 사이 실행을 시작했을 수 있음)·대기열에서 빠졌는데 기록을 읽지 못함
        const r = await PS.cancel();
        const j = PS.job;
        if (r === null && j) {
          opts.toast(j.ahead === null ? "취소 요청 보냄 — 대기열에서 빠짐, 기록을 읽지 못해 끝을 확인하는 중"
            : "취소 요청 보냄 — 대기열을 다시 읽지 못해 취소됐는지 확인하는 중", "warn");
        } else if (!r && j) opts.toast("취소하지 않음 — 이미 실행 중이거나 끝났습니다(실행 중이면 끝까지 받음)", "warn");
      } catch (e) {
        opts.toast(e.message, "err");
      }
      render();
    }

    function paidLock() {
      // 이 Studio 가 지금 유료 요청을 보내지 않는 이유("" = 보냄 — 유료 요청·실행·무료 큐는 하나씩): 승인 응답 기다림, 이 Studio 의 실행(무료 큐 포함),
      // 무료 큐를 만드는 중(반영 확인 창·템플릿 요청 — 그 큐가 실행 자리를 먼저 차지하면 탭이 돌려준 prompt_id 를 이어 받지 못함)
      if (paying) return paying === "reroll" ? "다시 생성 큐 요청을 처리하는 중" : `${STAGES[paying].label} 승인 요청을 처리하는 중`;
      if (PS.job) return `이 Studio 의 ${jobText(PS.job)} 이 끝난 뒤`;
      return asking || PS.sending ? "반영 확인·큐 요청을 처리하는 중" : "";
    }

    function serverWhy() {
      // 서버가 유료 템플릿(GET /pipeline/prompt)을 내주지 않을 이유("" = 낼 수 있음 — 마지막 GET /pipeline 으로 본 prompt_gate, stage 무관): 옛 서버
      // (S3a 판은 유료 stage 400 — GET /pipeline 에 manifest_rev 없음)·실행 설정을 읽지 못함·깨진 파일·확인 필요(prepare.*·import.*)·파일 없음·경로 경고
      const P0 = PS.P;
      if (S && !("pipeline_rev" in S)) return "ComfyUI 재시작 필요 — 서버가 옛 판(유료 템플릿 없음)";
      if (!P0 || PS.err) return `실행 설정을 읽지 못함(${PS.err || "읽는 중"})`;
      if (!("manifest_rev" in P0)) return "ComfyUI 재시작 필요 — 서버가 S3a 판(유료 템플릿 없음)";
      if (P0.error) return "실행 설정 파일이 깨짐 — 실행(G) [실행 설정 열기]에서 다시 저장";
      const cg = confirmGate(P0);
      if (cg.gate.length) return `실행 설정 확인 필요(${cg.gate.join(", ")}) — 실행(G) [실행 설정 열기]에서 확인·저장`;
      if (cg.noFile) return "실행 설정(pipeline.json) 없음 — 실행(G) [실행 설정 열기]에서 한 번 저장";
      return P0.warnings.length ? `${P0.warnings.map((w) => w.msg).join("; ")} — 실행(G) [실행 설정 열기]에서 경로를 고침` : "";
    }

    function paidWhy() {
      // 유료 승인 메시지를 보내도 템플릿이 나오지 않을 이유("" = 보낼 수 있음): 탭(link.paidWhy — 연결·확장 판·제출 가능·큐 처리 중), 그다음 서버
      // (serverWhy). 승인 단추와 board.js tabWay(R·머리줄 'ComfyUI 큐'의 유료 탭 길 — 막히면 opener·예약만)·R 확인 문구가 같은 판정
      return L ? L.paidWhy() || serverWhy() : "ComfyUI 탭 연결 없음";
    }

    function approveWhy(hash) {
      // 초안·Run 승인 단추가 꺼진 이유("" = 켬 — 계약 §5): 이 Studio 의 유료 요청·실행·무료 큐(paidLock), 승인할 해시 없음(copyHashes — 선택 없음·
      // 흐림·낡음·실행 중·승인할 것 없음), 앞 승인이 '계획 다시'로 끝나 계획을 다시 받는 중, 탭·서버(paidWhy)
      const lock = paidLock();
      if (lock) return lock;
      if (!hash) return "승인 코드 없음(승인할 것 없음·미반영·흐림·낡음·실행 중)";
      if (PS.plan === replanned) return "앞 승인 결과로 계획을 다시 받는 중";
      return paidWhy();
    }

    async function approve(e, kind, label, pair) {
      // 유료 승인(§4.4·계약 §5). 사용자 클릭(isTrusted)이 아니면 아무것도 안 함. 누를 때 지금 판정(해시·only·탭)이 그린 값과 다르면 보내지 않고
      // 다시 그림(r2-07 — 입력 중이라 미뤄 둔 옛 단추). 확인 = mock 백엔드면 페이지 상자, 아니면 네이티브 confirm(Claude 브라우저 패널에서는 늘
      // false — 유료 승인을 누를 수 없는 장치, D7). 상자를 기다리는 사이 바뀌었을 수 있어 확인 뒤 한 번 더 대조
      if (!e || !e.isTrusted || paying) return;
      const now = () => same(copyHashes()[kind], pair) && !approveWhy(pair[0]);
      const moved = () => {
        opts.toast("계획·탭 상태가 바뀜 — 승인하지 않음: 다시 그린 「비용·승인」 칸을 보고 다시 누르세요", "warn");
        render();
      };
      if (!now()) {
        moved();
        return;
      }
      const p = PS.plan;
      const full = !!(PS.P && PS.P.settings && PS.P.settings.draft.apply === "full");
      const mock = kind === "run" ? p.run.backend === "mock" : p.draft.backend === "vlm_mock";
      // 앞서 같은 해시로 보낸 승인의 결과를 모름(서버 해시·실행 중 판정은 대기열에 있는 첫 프롬프트를 모름) — 첫 승인이 큐에 있고 승인분을 소화하면 둘째는
      // 해시가 바뀌어 드라이런, 큐에 없거나 Run 앞에서 멈추면 둘째가 과금
      const again = (asked[kind] || []).some((a) => a.lost && a.hash === pair[0])
        ? [`앞서 보낸 같은 승인(승인 코드 ${pair[0]})의 결과를 모름 — 첫 승인이 ComfyUI 큐에 있으면 둘째는 그 실행이 승인분을 소화한 뒤라 과금 없이 드라이런, `
          + "큐에 없으면(또는 첫 실행이 Run 앞에서 멈추면) 이 승인이 과금합니다 — 보내기 전에 ComfyUI 큐 확인."] : [];
      paying = kind;
      render();
      let ok = false;
      try {
        ok = mock ? await mockAsk(label, kind === "draft" && !!p.draft.mock_forced)
          : window.confirm(approveText(kind, p, pair[1].split(","), PS.planStyle, full).concat(again, "계속할까요?").join("\n"));
      } catch (err) {
        // 확인 창 문구를 만들지 못함 = 계획에 확인 창 필드가 없음(새 JS + 재시작 전 옛 서버 — §1.3: S3a 초안 계획에는 reasons 등이 없음).
        // 보내지 않고 아래에서 잠금(paying)을 푼다 — 남으면 창을 새로 열 때까지 승인·반영·Export·무료 큐가 꺼진 채
        opts.toast(`${STAGES[kind].label} 승인 안 됨 — 확인 창을 만들지 못함(${err.message}): 서버가 옛 판이면 ComfyUI 재시작`, "err");
      }
      paying = null;
      if (!ok) {
        render();
        return;
      }
      if (!now()) {
        moved();
        return;
      }
      await sendPaid(kind, pair[0], pair[1], label);
    }

    function mockAsk(label, forced) {
      // mock 백엔드 확인 = 페이지 안 상자(해시가 mock 에 묶여 유료 실행을 승인할 수 없음 — §4.4). [승인]·[취소]도 사용자 클릭만, Esc = 취소.
      // 제목 = 단추 라벨에서 금액·끝 [MOCK] 을 뗀 이름(과금 없음 옆에 달러 추정·태그 두 번을 두지 않음)
      return new Promise((resolve) => {
        const box = el("div", "overlay");
        const sheet = el("div", "sheet");
        const close = (ok) => {
          ask = null;
          box.remove();
          resolve(ok);
        };
        sheet.append(el("h2", "", `[MOCK] 과금 없음 — ${label.replace(/ ≈ .*| \[MOCK\]$/, "")}`),
          el("p", "", "mock 백엔드 — 승인 코드가 mock 에 묶여 있어 유료 실행을 승인하지 못합니다."
            + (forced ? " 실행 설정의 초안 vlm 은 openai 지만 mock 스위치(_MOCK_API)로 mock 입니다." : "")),
          el("p", "dim", SIDE));
        btns(sheet, button("승인", "mock 승인 — 과금 없음", (e) => e.isTrusted && close(true), "primary"),
          button("취소", "Esc", (e) => e.isTrusted && close(false)));
        box.append(sheet);
        document.body.append(box);
        ask = { box, close };
      });
    }

    async function sendPaid(stage, hash, only, what) {
      // 승인 메시지(계약 §3 bmk-dp-approve) — 보내는 곳은 여기 하나. hash·only = 그린 계획 값(reroll 은 둘 다 ""), what = 탭 토스트용 설명
      paying = stage;
      if (stage !== "draft") wave = null;
      render();
      let id = null;
      const r = await L.send({ type: APPROVE, rootKey: opts.root, stage, hash, only, what }, (d) => {
        answered(stage, id, d);
        paidResult(stage, d, true);
        render();
      }, (a) => {
        id = a.id;
        (asked[stage] = asked[stage] || []).push(Object.assign({ hash, lost: false }, a));
        saveAsks();
      });
      paying = null;
      answered(stage, id, r);
      paidResult(stage, r, false);
      render();
    }

    function answered(stage, id, d) {
      // 요청 id 의 결과 d(바로 온 것·늦게 온 것)를 asked 에: 결과를 모르면(UNKNOWN — Studio 가 끝냄·탭이 응답을 잃음) 결과 모름으로 남기고, 알면 그 기록을
      // 지움 — 제출됨이면 같은 단계에서 그보다 먼저 보낸 기록도(대기열은 차례대로 — 다른 요청의 거절로는 지우지 않음)
      const xs = asked[stage] || [];
      const i = xs.findIndex((a) => a.id === id);
      if (i < 0) return;
      if (UNKNOWN.includes(d.reason)) {
        xs[i].lost = true;
        return;
      }
      asked[stage] = submitted(d) ? xs.slice(i + 1) : xs.filter((a) => a.id !== id);
      saveAsks();
    }

    function saveAsks() {
      // asked → sessionStorage {stage: [{hash, id, tab, via}]}(빈 단계는 빼고 다 비면 지움) — 저장소가 없거나 막히면 이 창에서만
      const m = {};
      for (const [stage, xs] of Object.entries(asked)) {
        if (xs.length) m[stage] = xs.map((a) => ({ hash: a.hash, id: a.id, tab: a.tab, via: a.via }));
      }
      try {
        if (Object.keys(m).length) store.setItem(ASK_KEY, JSON.stringify(m));
        else store.removeItem(ASK_KEY);
      } catch (e) {
        // 저장소 없음·막힘 — 새로 연 창에서 이어 받지 못할 뿐
      }
    }

    function resumeAsks() {
      // 앞 창(새로고침 전)이 보내고 결과를 모르는 승인 → 결과 모름(lost — 같은 해시 재승인 확인 창 줄·진행 패널 줄) + 그 id 의 늦은 approved 이어 받기
      let m = null;
      try {
        m = JSON.parse(store.getItem(ASK_KEY) || "{}");
      } catch (e) {
        // 저장소 없음·막힘·깨진 값 — 이어 받을 것 없음
      }
      for (const [stage, xs] of Object.entries(m && typeof m === "object" ? m : {})) {
        if (!STAGES[stage] || !STAGES[stage].paid || !Array.isArray(xs)) continue;
        asked[stage] = xs.filter((a) => a && typeof a.id === "string")
          .map((a) => ({ hash: String(a.hash || ""), id: a.id, tab: a.tab, via: a.via, lost: true }));
        if (!L) continue;
        for (const a of asked[stage]) {
          L.adopt(a, (d) => {
            answered(stage, a.id, d);
            paidResult(stage, d, true);
            render();
          });
        }
      }
    }

    function paidResult(stage, d, late) {
      // ok = 탭이 낸 prompt_id 를 이 Studio 의 실행으로(무료 큐와 같은 이어 받기·끝 판정·진행 줄). 실패 = 사유별 토스트, 계획을 다시 받아야 하는
      // 사유(REPLAN — 결과를 모르는 것 포함)면 계획 다시(받을 때까지 지금 계획의 해시로 승인 끔). late = Studio 가 시간·탭 바뀜으로 끝낸 뒤 온 결과
      // (이미 큐에 들어간 실행을 놓치지 않게)
      const label = STAGES[stage].label;
      const pre = late ? "늦게 온 결과 — " : "";
      if (submitted(d)) {
        if (PS.track(stage, d.prompt_id)) opts.toast(`${pre}${label} 큐에 들어감 — ComfyUI 탭이 승인 템플릿을 제출함(진행은 머리줄)`);
        else opts.toast(`${pre}${label} 큐에 들어감(prompt ${d.prompt_id}) — 이 Studio 가 다른 실행을 보는 중이라 진행은 ComfyUI 큐에서 확인`, "warn");
        return;
      }
      // 머리: 결과를 모르고 끝남(UNKNOWN — Studio 가 끝냄·탭이 응답을 잃음) = '결과 모름'(이미 큐에 들어갔을 수 있음), 재굴림 = '큐 안 됨'(R 이 이미 사전 승인 — 예약된 채 남아 다음 Run·
      // ComfyUI 큐 때 과금, R 을 다시 누르면 +1 이 더 승인됨), 그 밖 = 서버·탭이 확정한 거절 '승인 안 됨'
      const unknown = UNKNOWN.includes(d.reason);
      const head = unknown ? `${label} 결과 모름` : stage === "reroll" ? "다시 생성 큐 안 됨" : `${label} 승인 안 됨`;
      const kept = stage !== "reroll" ? "" : ` · ${unknown ? "큐에 들어가지 않았으면 " : ""}사전 승인한 다시 생성은 예약된 채 남음(다음 Run·[다시 생성 보내기] 때 `
        + "선택과 무관하게 과금): 지금 보내려면 머리줄 [다시 생성 보내기] — R 을 다시 누르면 +1 호출이 더 승인됨";
      opts.toast(`${pre}${head} — ${refusal(d, stage)}${kept}`, unknown || (stage !== "reroll" && d.reason === "busy") ? "warn" : "err");
      if (REPLAN.includes(d.reason)) {
        replanned = PS.plan;
        planNow();
      }
    }

    function submitted(d) {
      // 탭의 approved 가 제출 성공(계약 §4 6 — ok true 이고 prompt_id 가 비지 않은 문자열)
      return d.ok === true && typeof d.prompt_id === "string" && !!d.prompt_id;
    }

    function refusal(d, stage) {
      // 승인 실패 문구: 반영 필요는 '먼저 [반영]'(사유), 낡은 해시는 지금 해시 — 그 밖(서버 409·탭·Studio 사유)은 받은 error 그대로. 탭 error 는
      // 이유와 할 일을 함께 싣는다(큐 처리 중 = 잠시 뒤 다시 승인, 자동 큐 = Run 단추 모드를 바꾼 뒤 다시 승인, 제출 안 됨 = 승인은 Studio 에서 다시 등) —
      // 재굴림은 그 할 일이 R 재누름(+1 사전 승인)으로 읽히지 않게 빼거나 머리줄 'ComfyUI 큐'로(머리줄 큐 안내가 뒤에 붙음)
      if (d.reason === "apply_needed") return `미반영 — 먼저 [반영 (무료)]${Array.isArray(d.pending) && d.pending.length ? `(${d.pending.join(", ")})` : ""}`;
      if (d.reason === "stale") return `계획이 바뀜(지금 승인 코드 ${d.hash || "?"}) — 다시 받은 계획을 보고 다시 승인`;
      const why = String(d.error || (d.reason === "busy" ? "탭이 바쁨 — 잠시 뒤 다시 승인" : "ComfyUI 탭 응답 모양이 잘못됨"));
      if (stage !== "reroll") return why;
      // 탭 not_submitted 의 꼬리(남은 승인 항목·무장 중 쌓인 큐 항목을 뺌 — 하나 또는 둘)는 떼고 할 일을 바꾼 뒤 다시 붙임
      const tail = /( — 남은 승인 항목은 큐에서 뺌| — 무장 중 쌓여 남은 큐 항목\(승인 항목인지 모름\)은 큐에서 뺌)+$/.exec(why);
      const head = tail ? why.slice(0, tail.index) : why;
      return head.replace(/ — (잠시 뒤 다시 승인|승인은 Studio 에서 다시)$/, "").replace(/(Studio 에서 )?다시 승인$/, "머리줄 [다시 생성 보내기]")
        + (tail ? tail[0] : "");
    }

    function reroll() {
      // board.js queueReroll(R 뒤·머리줄 'ComfyUI 큐' — 유료 가능한 탭일 때): reroll stage(approve·only 없음 — Run 은 사전 승인 재굴림·복구만 과금).
      // 확인 창 없음(R 의 확인이 동의). 이 Studio 가 유료 요청을 보내지 않는 중(paidLock)이면 보내지 않음(재굴림은 이미 사전 승인 — 머리줄 큐·다음 Run 때
      // 과금 — R 확인 문구도 이 판정으로 '지금은 예약만')
      const lock = paidLock();
      if (lock) {
        opts.toast(`다시 생성 예약됨 — 지금 보내지 않음(${lock}): 머리줄 [다시 생성 보내기]로 보내거나 다음 Run 때 과금`, "warn");
        return;
      }
      const n = S ? S.pending_preapproved || 0 : 0;
      sendPaid("reroll", "", "", n ? `다시 생성(사전 승인) ${n}건` : "[다시 생성 보내기](다시 생성 대기 없음 — 반영·복구만)");
    }

    function openSettings() {
      settingsOn = true;
      setupPending = false; // 수동 설정으로 진입하면 닫은 뒤에도 자동 제안을 다시 하지 않음
      if (on) render();
      else opts.enter();
    }

    function setSel(ids) {
      sel.clear();
      for (const id of ids) sel.add(id);
      render();
      schedulePlan();
    }

    function needIds() {
      // 승인 필요만: Run 승인 필요 또는 초안 대기가 있는 크롭(only 없는 계획 by_crop)
      const p = PS.plan;
      if (!p || !S) return [];
      const rb = p.run.by_crop || {};
      const db = p.draft.by_crop || {};
      return S.crops.map((c) => c.id).filter((id) => (rb[id] && rb[id].need) || (db[id] && db[id].wait.length));
    }

    function pick(id) {
      opts.select(id);
      select(id);
    }

    function select(id) {
      if (id === cur) {
        if (on && dvErr) loadDraft(); // 읽지 못한 크롭 줄을 다시 누름 = 다시 읽기
        return;
      }
      cur = id;
      dv = null;
      dvErr = null;
      closeEdit();
      if (on) loadDraft();
      render();
    }

    function closeEdit() {
      // [수정] 입력칸을 닫는다 — 남은 미리보기 타이머와 이미 보낸 미리보기의 응답도 버린다(닫은 뒤 text null 로 미리보기를 보내지 않게)
      text = null;
      pv = null;
      pvSeq += 1;
      clearTimeout(pvTimer);
    }

    async function loadDraft() {
      // 읽지 못하면(500·잠김·재시작 중) 이 크롭의 옛 GET /draft 는 두고(입력 중인 [수정]·단추가 남음 — 쓰기는 seen·crop_sig 로 409) 오류만 싣는다(404 = 크롭 없음은 비움).
      // 다시 읽기 = [다시 읽기]·같은 크롭 줄·다음 state 변화(rev·plan_v — [수정] 입력 중이어도)
      if (!cur) return;
      const seq = ++dvSeq;
      seenRev = S ? S.rev : null;
      try {
        const d = await PS.draft(cur);
        if (seq !== dvSeq) return;
        dv = d;
        dvErr = null;
      } catch (e) {
        if (seq !== dvSeq) return;
        if (e.status === 404) {
          // 크롭이 매니페스트에 없음(반영·그래프 Import 가 지움) — 옛 값·단추·[수정] 입력을 남기지 않음
          dv = null;
          closeEdit();
        }
        dvErr = e.message;
      }
      render();
    }

    function errText(e) {
      // 채택 쓰기·미리보기 거절 문구 + 내용 보호 400 의 rows(§2.5 ⑥ — 채택 외에 바뀌는 필드: apply 차이·화풍 차이를 가름)
      const rows = e.data && Array.isArray(e.data.rows) ? e.data.rows : [];
      if (!rows.length) return e.message;
      return `${e.message} — 바뀌는 필드: ${rows.slice(0, 4).join(", ")}${rows.length > 4 ? ` 외 ${rows.length - 4}` : ""}`;
    }

    function effectLines(r) {
      // 미리보기 응답(§2.5 effects) → 확인 창·영향 줄. layout_wins 의 '레이아웃 문구가 이김' 줄은 서버가 warnings 에 이미 싣는다
      const e = r.effects;
      const out = [`다음 반영 뒤 타깃: ${e.targets_after.map((t) => `${t.tid} ${t.label}`).join(" · ") || "없음"}`];
      if (e.prompt_changed) out.push("다음 반영에서 이 크롭 새 생성 조건 — 생성 승인 대상(유료)");
      const b = e.draft_wait.before;
      const a = e.draft_wait.after;
      out.push(`초안 대기: ${b.join(", ") || "없음"} → ${a.join(", ") || "없음"}` + (a.length ? " — 유료 초안 대기" : ""));
      return out.concat(r.warnings);
    }

    async function writeAdopt(op) {
      // d0 = 쓰는 크롭의 GET /draft — 응답을 기다리는 사이 다른 크롭을 고르면 cur·dv 가 바뀌므로 그 크롭 것만 패널에 되돌린다.
      // 앞 쓰기(채택·설정)를 처리하는 중이면 두 번 누름이라 조용히 버린다(앞 쓰기의 결과 토스트가 나온다). 읽은 GET /draft 가 없으면(크롭이
      // 사라짐 — 입력 중이라 미뤄 둔 옛 입력칸의 Enter) 쓰지 않는다 — 다시 그린 패널이 오류를 보인다
      if (PS.busy || !dv) return;
      const what = { adopt: "채택", set: "수정", clear: "해제" }[op];
      const d0 = dv;
      const sent = text; // [수정] 으로 보낸 문구 — 미리보기·쓰기를 기다리는 사이 입력칸에 더 친 문구는 보내지 않은 것
      if (op === "set" && !String(text || "").trim()) {
        opts.toast("수정 문구가 비었습니다 — 해제를 쓰세요", "err");
        return;
      }
      try {
        const r = await PS.adopt(d0, op, text, (p) => window.confirm([`${what}(${d0.name}): 스펙 _layer_targets 를 씁니다(반영은 다음 무료 반영).`,
          ...effectLines(p), "계속할까요?"].join("\n")));
        if (!r) {
          render(); // 확인에서 멈춤 — 미리보기를 기다리는 사이(busy) 다시 그린 단추를 다시 켬
          return;
        }
        // 기다리는 사이 더 친 문구(kept)는 저장되지 않았다(쓴 값 = 확인 창의 영향 줄 그대로) — 입력칸을 닫지 않고 남겨 새 seen 으로 다시 미리보기
        const kept = op === "set" && cur === d0.id && text !== null && text !== sent;
        // 해제: 노드 값(owner deleted — keep_deleted)은 [수정]으로만 다시, 사람 값은 키가 없어져 제안이 있으면 다시 [채택](§3.4·§2.5)
        opts.toast(op === "clear" ? `해제 — ${d0.name}: 다음 반영에서 ${r.layer_targets.owner === "deleted" ? "채택 타깃이 빠짐(다시 쓰려면 [수정])"
          : "사람 값 타깃이 빠짐(제안이 있으면 다시 [채택])"}`
          : `${what} — ${d0.name}: ${r.layer_targets.value || "(없음)"} · 다음 반영에서 타깃에 들어감`
            + (kept ? " · 그 사이 더 친 문구는 저장 안 함(입력칸에 남김 — 다시 [저장])" : ""), kept ? "warn" : "");
        if (cur === d0.id && !kept) {
          closeEdit();
          // Enter 로 저장하면 입력칸에 포커스가 남아 패널 다시 그리기가 미뤄진다(옛 입력칸·[저장]이 남아 다시 누르면 '문구가 비었습니다') — Esc 처럼 놓음
          const a = document.activeElement;
          if (a && panel.contains(a)) a.blur();
        }
        await loadDraft();
        if (kept && cur === d0.id && text !== null) {
          pv = null;
          schedulePreview();
        }
        planNow();
      } catch (e) {
        opts.toast(e.status === 409 ? `${e.message} — 다시 읽음` : errText(e), "err");
        if (e.status === 409 && e.view && e.view.id === cur) {
          dv = e.view;
          dvErr = null;
          pv = null;
        }
        render(); // 쓰는 중(busy)에 그린 단추를 다시 켬
      }
    }

    function schedulePreview(again = false) {
      // [수정] 입력이 멈추면 미리보기 검사(쓰지 않음). 409(다른 창이 스펙을 씀·크롭이 바뀜)면 GET /draft 를 다시 읽고(입력 중인 문구는
      // 그대로) 새 seen 으로 한 번 더 — 다시 409 면 오류만 보이고 [저장]은 켜 둔다(저장 경로가 409 뒤 다시 읽음)
      clearTimeout(pvTimer);
      if (composing || text === null) return;
      pvTimer = setTimeout(async () => {
        const t = text;
        const seq = ++pvSeq;
        if (!dv || !String(t).trim()) {
          pv = null;
          render();
          return;
        }
        try {
          const r = await PS.preview(dv, t);
          if (seq === pvSeq) pv = { r };
        } catch (e) {
          if (seq !== pvSeq) return;
          if (e.status === 409 && !again) {
            await loadDraft();
            if (seq === pvSeq) schedulePreview(true);
            return;
          }
          pv = { err: errText(e), status: e.status };
        }
        if (seq === pvSeq) render();
      }, again ? 0 : PREVIEW_MS);
    }

    async function saveSection(sec) {
      if (PS.busy) return; // 앞 쓰기를 처리하는 중(두 번 누름) — 조용히 버림
      const w = PS.P.widgets[sec];
      const base = PS.P.settings ? PS.P.settings[sec] : PS.P.derived.settings[sec];
      const sent = Object.assign({}, form[sec]); // 보낸 편집값 — 응답을 기다리는 사이 같은 절에 친 값은 남긴다
      const raw = Object.assign({}, base, sent);
      const values = {};
      for (const [k, spec] of Object.entries(w)) values[k] = typed(spec, raw[k]);
      // 편집을 시작한 뒤·화면을 그린 뒤 다른 창이 이 절을 저장했으면 말없이 덮지 않고 묻는다(S1 편집기 since 와 같은 뜻): 손댄 키 = 다른 창 값을
      // 이 창 값으로 덮음, 손대지 않은 키 = 화면에 없던 다른 창 값이 이 저장(확인)에 그대로 실림
      const e0 = edits[sec] || {};
      const seen = drawn[sec] || base;
      const over = Object.keys(sent).filter((k) => k in e0 && !same(base[k], e0[k]) && !same(base[k], values[k]));
      const unseen = Object.keys(w).filter((k) => !(k in sent) && !same(base[k], seen[k]));
      if ((over.length || unseen.length) && !window.confirm([`${SECTIONS[sec]}: 편집을 시작한 뒤 다른 창이 이 절을 저장했습니다(지금 rev ${PS.P.rev}).`,
        ...over.map((k) => ` · ${sec}.${k}: 다른 창이 쓴 ${shown(base[k])} 을 이 창 값 ${shown(values[k])} 로 덮어씀`),
        ...unseen.map((k) => ` · ${sec}.${k}: ${shown(seen[k])} → ${shown(base[k])} (화면에 없던 다른 창 값 — 그대로 저장)`),
        "이대로 저장할까요?"].join("\n"))) {
        render(); // 다른 창 값·충돌 표시로 다시 그림
        return;
      }
      try {
        const r = await PS.save(sec, values);
        // 이 창이 저장한 값 = 서버가 쓴 절(import 경로는 정규화 — 따옴표·공백 없이 '/'). 입력 중이라 다시 그리기가 미뤄져도 다음 저장·칸 옆 표시가
        // 제 값을 '다른 창 값'으로 보지 않게: 화면 기준값(drawn)과, 응답을 기다리는 사이 같은 칸에 더 친 키의 첫 편집 기준값(edits)을 이 값으로
        const saved = r.settings[sec];
        const f = form[sec] || {};
        for (const k of Object.keys(f)) {
          if (!(k in sent)) continue;
          if (f[k] === sent[k]) {
            delete f[k];
            delete e0[k];
          } else e0[k] = saved[k];
        }
        if (!Object.keys(f).length) {
          delete form[sec];
          delete edits[sec];
        }
        drawn[sec] = saved;
        opts.toast(`${SECTIONS[sec]} 설정 저장 — rev ${r.rev}`);
        planNow();
      } catch (e) {
        opts.toast(e.status === 409 ? `${e.message} — 다시 읽음` : e.message, "err");
      }
      render();
    }

    function typed(spec, v) {
      // 입력칸 값 → 위젯 JSON 타입(INT·FLOAT 숫자, BOOLEAN 불리언). 숫자가 아니면 그대로 보내 서버가 400 으로 알린다
      if (spec.type === "BOOLEAN") return !!v;
      if (spec.type === "INT" || spec.type === "FLOAT") {
        const n = typeof v === "number" ? v : String(v).trim() === "" ? NaN : Number(v);
        return Number.isFinite(n) && (spec.type === "FLOAT" || Number.isInteger(n)) ? n : v;
      }
      return v === null || v === undefined ? "" : String(v);
    }

    async function resetDerived() {
      // [매니페스트 값으로 다시] = 지금 GET /pipeline 의 유도값(derived.settings — 다시 읽은 것)을 절마다 저장(다른 절·확인 필요 항목만). 저장한 절의
      // 확인 필요 항목은 확인된 것이 되므로(§2.1) 확인 창에 그 값·출처(추정·노드 기본값)와 저장값에서 바뀌는 키를 적는다. 보낸 값 = 확인 창의 값
      if (!(await reload()) || !PS.P) return;
      const P0 = PS.P;
      const D = P0.derived.settings;
      const secs = Object.keys(P0.widgets).filter((sec) => !P0.settings || JSON.stringify(P0.settings[sec]) !== JSON.stringify(D[sec])
        || confirmOf(P0).some((c) => c.startsWith(sec + ".")));
      if (!secs.length) {
        opts.toast("유도값과 같습니다(확인 필요 없음)");
        return;
      }
      const lines = [`마지막 실행 값으로 채우기: ${secs.map((x) => SECTIONS[x]).join(", ")} 절을 유도값으로 저장합니다.`];
      const conf = confirmOf(P0).filter((c) => secs.includes(c.split(".")[0]));
      if (conf.length) {
        lines.push("확인 필요 항목을 아래 값으로 확인한 것으로 저장합니다 — 그래프 노드 위젯 값과 같은지 확인:", ...conf.map((c) => {
          const [sec, k] = c.split(".");
          return ` · ${c} = ${shown(D[sec][k])} (${FROM[P0.derived.from[c]] || P0.derived.from[c] || "출처 모름"})`;
        }));
      }
      const diff = P0.settings ? secs.flatMap((sec) => Object.keys(D[sec]).filter((k) => JSON.stringify(P0.settings[sec][k]) !== JSON.stringify(D[sec][k]))
        .map((k) => ` · ${sec}.${k}: ${shown(P0.settings[sec][k])} → ${shown(D[sec][k])}`)) : [];
      if (diff.length) lines.push("저장값에서 바뀌는 값:", ...diff);
      if (!window.confirm(lines.concat("Prepare·Import 가 바뀌면 다음 반영에서 생성 조건이 바뀌어 다시 과금 대상이 될 수 있습니다. 계속할까요?").join("\n"))) return;
      // 확인 창을 닫은 때의 편집값 — 리셋이 덮는 것은 이 값뿐. 저장을 기다리는 사이 칸은 켜져 있어 그 뒤에 친 값(바꾼 키·새 키)은 보내지 않은
      // 새 편집이라 남긴다(saveSection 과 같은 뜻 — 지우면 칸이 blur 뒤 저장값으로 돌아가고, 바로 누른 [저장]은 친 값 대신 저장값을 '저장 성공'으로)
      const f0 = Object.fromEntries(secs.map((sec) => [sec, Object.assign({}, form[sec])]));
      // 판 = 확인 창의 판, 다음 절부터는 이 창의 앞 저장 응답 rev — 저장마다 다시 읽은 GET /pipeline 의 rev 를 쓰면 그 사이 다른 창의 저장이
      // 409 없이 확인 창 값(D)에 덮인다. 409 면 멈춘다(앞 절은 저장됨)
      let rev = P0.rev;
      const saved = [];
      for (const sec of secs) {
        try {
          const r = await PS.save(sec, D[sec], rev);
          rev = r.rev;
          saved.push(SECTIONS[sec]);
          const sv = r.settings[sec];
          const f = form[sec] || {};
          const e0 = edits[sec] || (edits[sec] = {});
          for (const k of Object.keys(f)) {
            if (k in f0[sec] && f[k] === f0[sec][k]) {
              delete f[k];
              delete e0[k];
            } else e0[k] = sv[k]; // 남긴 편집의 기준값 = 이 창이 방금 저장한 값(다른 창 값으로 묻지 않게)
          }
          if (!Object.keys(f).length) {
            delete form[sec];
            delete edits[sec];
          }
          drawn[sec] = sv; // 서버가 쓴 값(saveSection 과 같은 뜻 — 제 저장을 다른 창 값으로 묻지 않게)
        } catch (e) {
          opts.toast(`${SECTIONS[sec]}: ${e.message}`
            + (saved.length ? ` — 앞 절(${saved.join(", ")})은 저장됨, 남은 절은 [마지막 실행 값으로 채우기]로 다시 확인` : ""), "err");
          break;
        }
      }
      planNow();
      render();
    }

    // ── 그리기 ──
    function panelBusy() {
      // 입력 중(설정·[수정] 입력칸에 포커스·한글 조합)이면 패널을 다시 만들지 않는다 — 입력이 끝나면(focusout) 그림
      const a = document.activeElement;
      return composing || (!!a && panel.contains(a) && /^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName) && a.type !== "checkbox");
    }

    function render() {
      // 포인터가 눌린 동안은 아무것도 다시 만들지 않는다 — mousedown 과 mouseup 사이에 누른 단추·줄이 빠지면 Chromium 은 click 을 보내지 않는다
      // (입력칸에서 단추로 옮겨 누르면 입력칸 focusout 이 그 사이에 온다)
      if (held) {
        pendingPanel = true;
        return;
      }
      pendingPanel = false;
      renderHead();
      if (!on) return;
      renderList();
      if (panelBusy()) {
        pendingPanel = true;
        return;
      }
      renderPanel();
    }

    function flush() {
      if (pendingPanel && !held) render();
    }

    function renderHead() {
      // 머리줄 한 줄(모든 모드): 반영 상태 · Run 승인 필요(only 없는 계획) · 초안 대기 · ★ · Export, Studio 가 낸 실행
      head.replaceChildren();
      if (!S || !("pipeline_rev" in S)) return;
      const p = PS.plan;
      const pend = pendingApply();
      const unk = unsureWhy();
      const st = el("span", pend.length || unk ? "lh-wait" : "lh-ok", pend.length ? `미반영 ${pend.length}` : unk ? "반영 상태 확인 못 함" : "반영됨");
      // 크롭별 레이아웃 줄은 레이아웃 사유 바로 아래(없으면 끝)
      const lays = layoutLines(S).map((x) => ` · ${x.text}`);
      const at = pend.findIndex((r) => r.startsWith(LAYOUT_REASON)) + 1 || pend.length;
      st.title = pend.slice(0, at).concat(lays, pend.slice(at), unk ? [`레퍼런스·채택·Import 설정 미반영 확인 못 함 — ${unk}`] : []).join("\n")
        || "레이아웃·레퍼런스·채택·Import 설정 미반영·Prepare 입력 바뀜 없음";
      head.append(st);
      if (confirmOf(PS.P).length) {
        // 빨강 = 반영·Export 를 막음(prepare.*·import.*·파일 없음·깨진 파일), 노랑 = 막지 않음(해시만 흐림 — 설정 칸 줄과 같은 판정)
        const cg = confirmGate(PS.P);
        const c = el("span", cg.gate.length || cg.noFile ? "lh-bad" : "lh-wait", "설정 확인 필요");
        c.title = confirmOf(PS.P).join("\n");
        head.append(c);
      }
      // Prepare 갈라짐(마지막 Prepare ≠ Studio 설정) — 검토 모드의 R·ComfyUI 큐도 Prepare 를 다시 돈 뒤 과금하므로 모든 모드에서 보임(유료 가능한 탭의
      // 재굴림 템플릿 = Studio 설정 — 이 갈라짐이 반영됨, 옛 탭의 Review 부분 실행·그래프 Run = 그래프 위젯 값)
      const pdr = PS.P && PS.P.drift ? PS.P.drift.filter((x) => x.section === "prepare") : [];
      if (pdr.length) {
        const c = el("span", pdr.some((x) => DRIFT_BILL.includes(x.kind)) ? "lh-bad" : "lh-wait", "Prepare 갈라짐");
        c.title = pdr.map(driftText).concat("R·[다시 생성 보내기](유료 승인 탭 — 다시 생성 템플릿)는 Studio 실행 설정으로 Prepare 를 다시 돌려 이 갈라짐을 반영한 뒤 과금"
          + "(생성 조건이 바뀌면 다시 생성이 미뤄짐), 그래프 Run·옛 탭의 Review 부분 실행은 그래프 Prepare(그래프 위젯 값)로 jobs 를 다시 쓴 뒤 과금").join("\n");
        head.append(c);
      }
      if (p && p.run.by_crop) {
        // only 없는 계획 by_crop 의 합 — 선택과 무관한 전체(C13)
        const bc = Object.values(p.run.by_crop);
        const need = bc.reduce((a, x) => a + x.need, 0);
        const lo = bc.reduce((a, x) => a + x.est_usd[0], 0);
        head.append(el("span", "", need ? `생성 승인 필요(전체) ${need}건 $${lo.toFixed(1)}` : "생성 승인 필요(전체) 0"));
      }
      if (p && p.draft.by_crop) {
        const n = Object.values(p.draft.by_crop).filter((x) => x.wait.length).length;
        if (n) head.append(el("span", "", `초안 ${n}`));
      }
      // 공동 결과의 뒤 타깃(★ 는 첫 타깃에만 — 정상)은 분모에서 빼고 따로
      const picked = S.targets.filter((t) => t.pick_keys.length).length;
      const joint = S.targets.filter((t) => !t.pick_keys.length && jointFirst(S, t)).length;
      const hs = el("span", "", `★ ${picked}/${S.targets.length - joint}` + (joint ? ` (+공동 ${joint})` : ""));
      if (joint) hs.title = `공동 결과의 뒤 타깃 ${joint}개 — ★ 는 첫 타깃(보통 A)에만이라 ★ 없음이 정상`;
      head.append(hs);
      const ex = PS.P && PS.P.exports && PS.P.exports[0];
      if (ex) head.append(el("span", "", `Export r${ex.rev}`));
      if (PS.job) {
        head.append(el("span", "lh-wait", `실행 중: ${jobText(PS.job)}`));
        if (PS.job.ahead !== null && !PS.job.running) head.append(button("취소", "대기 중인 이 Studio 의 프롬프트를 ComfyUI 대기열에서 지움", cancelJob));
      }
      // 재굴림 큐는 확인 창 없음(R 의 확인이 동의 — 사전 승인분 제출)이라 '승인 — 확인' 이 아님
      if (paying) {
        head.append(el("span", "lh-wait", paying === "reroll" ? "다시 생성 큐 — ComfyUI 탭 응답 기다리는 중"
          : `${STAGES[paying].label} 승인 — 확인·ComfyUI 탭 응답 기다리는 중`));
      }
      // 낡은 계획, 또는 계획을 한 번도 받지 못함(Studio 를 연 뒤 첫 계획이 202 두 번·409) — 검토·E·T 모드는 스스로 다시 받지 않으므로 여기서
      if (planStale() || (!p && PS.planErr)) {
        head.append(recalcButton(p ? "계획이 지금 파일 상태와 다름(칩이 흐림) — 다시 계산" : `계획을 받지 못함: ${PS.planErr}`));
      }
    }

    function renderList() {
      list.replaceChildren();
      if (!S) return;
      const p = PS.plan;
      const bar = el("li", "pl-bar");
      bar.append(el("span", "dim", `선택 ${sel.size}/${S.crops.length}`),
        button("전체", "모든 크롭(only = 명시 목록)", () => setSel(S.crops.map((c) => c.id))),
        button("없음", "선택 해제 — 계획·승인 끔", () => setSel([])),
        button("승인 필요만", "생성 승인 필요·초안 대기가 있는 크롭", () => setSel(needIds())));
      list.append(bar);
      const lays = new Map(layoutLines(S).map((x) => [x.id, x.text]));
      for (const c of S.crops) {
        const li = el("li", c.id === cur ? "sel" : "");
        const top = el("div", "pl-top");
        const cb = el("input");
        cb.type = "checkbox";
        cb.checked = sel.has(c.id);
        cb.dataset.keys = "board";
        cb.title = "선택(only) — 계획·승인 코드 복사의 대상";
        cb.addEventListener("click", (e) => e.stopPropagation());
        cb.addEventListener("change", () => {
          if (cb.checked) sel.add(c.id);
          else sel.delete(c.id);
          cb.blur();
          render();
          schedulePlan();
        });
        top.append(cb, el("span", "tname", c.name || c.id));
        li.append(top);
        const ch = el("div", "chips");
        const r = p && p.run.by_crop ? p.run.by_crop[c.id] : null;
        const d = p && p.draft.by_crop ? p.draft.by_crop[c.id] : null;
        if (LAYOUT_PENDING.includes(c.layout_status)) {
          const lc = chip(LAYOUT_REASON, "b-wait");
          lc.title = lays.get(c.id) || "";
          ch.append(lc);
        }
        if (r && r.need) ch.append(chip(`승인 필요 ${r.need}`, "b-wait"));
        if (r && r.pre) ch.append(chip(`다시 생성 ${r.pre}`, "b-wait"));
        if (r && r.inflight) ch.append(chip(`진행 ${r.inflight}`, "b-run"));
        if (r && (r.failed || r.orphaned)) ch.append(chip(`실패 ${r.failed} · 결과 못 받음 ${r.orphaned}`, "b-bad"));
        if (r && r.done) ch.append(chip(`완료 ${r.done}`, "b-cost"));
        if (d && d.wait.length) {
          const w = chip("초안 대기", "b-wait");
          w.title = d.wait.join("\n");
          ch.append(w);
        }
        if (d && d.refresh) ch.append(chip("초안 갱신 가능"));
        if (d && d.adopt === "add") ch.append(chip("채택 가능", "b-reg"));
        if (ch.childElementCount) li.append(ch);
        li.addEventListener("click", () => pick(c.id));
        list.append(li);
      }
    }

    function renderPanel() {
      panel.replaceChildren();
      if (settingsOn) {
        renderSettings();
        return;
      }
      const P0 = PS.P;
      const p = PS.plan;
      const only = onlyNow();
      const planOk = planOkNow();
      const pend = pendingApply();
      const unk = pendingUnknown();
      const stale = planStale();
      const unsure = unsureWhy();

      if (window.DPChecklist) {
        const host = el("div");
        panel.append(host);
        const prepareDrift = P0 && !P0.error ? (P0.drift || []).filter((x) => x.section === "prepare") : [];
        const settingsBlock = PS.err ? "읽기 오류" : !P0 ? "불러오는 중" : P0.error ? "pipeline.json 오류"
          : !P0.saved ? "파일 없음" : confirmGate(P0).gate.length ? "필수 항목 미확인" : "";
        window.DPChecklist.render(host, window.DPChecklist.rows({ S, P0, plan: p, pending: pend, prepareDrift, settingsBlock, unsure,
          planOk, planErr: PS.planErr, stale, selectedCount: sel.size, maskLeft, progress: PS.progress }), {
          memory: panel, goMode: opts.goMode, goTarget: opts.goTarget, goMask: opts.goMask,
          goSection: (title) => {
            const sec = [...panel.querySelectorAll(".pp-sec")].find((x) => x.firstElementChild && x.firstElementChild.textContent === title);
            if (sec) sec.scrollIntoView({ block: "start", behavior: "smooth" });
          },
        });
      }

      // 설정
      let s = section("실행 설정");
      if (!P0) line(s, PS.err ? `읽기 오류: ${PS.err}` : "불러오는 중…", PS.err ? "bad" : "dim");
      else {
        if (P0.error) line(s, `pipeline.json 오류: ${P0.error} — [실행 설정 열기]에서 다시 저장(덮어쓰기)`, "bad");
        else {
          const pr = P0.settings.prepare;
          const dr = P0.settings.draft;
          line(s, `${P0.saved ? `rev ${P0.rev}` : "파일 없음(유도값)"} · Prepare ${pr.model} · ${pr.quality} · ${pr.size_rule} · ${pr.variants} · n ${pr.n} · `
            + `${pr.ref_style} · ${pr.calls_per_cell} · 초안 ${dr.vlm} · ${dr.effort} · ${dr.write_mode} · ${dr.apply}`);
        }
        // 확인 필요 = 서버 prompt_gate 판정대로 두 줄: 막는 항목(prepare.*·import.* — 깨진 파일은 전부)은 빨강, 그 밖(늘 남는 draft.apply·기록 없는
        // analyze·compose·export 등)은 반영·Export 를 막지 않고 계획 해시만 흐림(노랑). 파일 없음은 항목이 없어도 막는다(첫 저장이 파일을 만듦)
        const cg = confirmGate(P0);
        if (cg.gate.length) line(s, `확인 필요: ${cg.gate.join(", ")} — 확인(저장) 전에는 반영·Export 를 막음`, "bad");
        else if (cg.noFile) line(s, "pipeline.json 없음 — 한 번 저장하기 전에는 반영·Export 를 막음", "bad");
        if (cg.rest.length) line(s, `확인 필요: ${cg.rest.join(", ")} — 반영·Export 는 막지 않음(확인(저장) 전에는 승인 코드를 흐림)`, "warn");
        for (const d of P0.drift) line(s, driftText(d), DRIFT_BILL.includes(d.kind) ? "bad" : "warn");
        // 경로 경고는 모두 템플릿을 막는다(서버 prompt_gate 409): import.* = 반영·Export, cand.path = 반영만(export 템플릿에는 dp_cand 가 없음)
        for (const w of P0.warnings) {
          line(s, w.field.startsWith("cand.") ? `${w.msg} — 반영을 막음(Export 는 됨): [실행 설정 열기]에서 경로를 고치거나 후보 등록을 끔` : w.msg, "bad");
        }
      }
      btns(s, button("실행 설정 열기", "실행 설정(pipeline.json) — 노드 위젯 그대로, 절마다 저장", openSettings));

      // 반영
      s = section("반영");
      if (pend.length) line(s, pend.join(" · "), "warn");
      // 레이아웃 미반영 크롭별 줄(편집(E)의 실수 끌기가 여기서 보이게) — 이름·사각형이 바뀐 줄은 [되돌리기](마지막 반영 값으로)
      const lays = S ? layoutLines(S) : [];
      const layRev = S ? S.layout_rev : null;
      for (const x of lays) {
        const r = line(s, ` · ${x.text}`, "warn");
        if (!x.back) continue;
        // 이 Studio 의 실행(반영 큐 — Import 가 layout.json 을 읽음)·유료 요청 중에는 끔(반영 단추와 같은 판정)
        const b = button("되돌리기", "이 크롭의 바뀐 이름·위치를 마지막 반영 값으로(레이아웃만 — 무료)", once(() => layoutBack(x, layRev)), "inline");
        // 잠긴 크롭(배치 3-3 — state 크롭 요약 locked)도 끔 — 서버도 423
        const locked = S.crops.some((c) => c.id === x.id && c.locked);
        b.disabled = !!PS.job || !!paying || locked;
        if (locked) b.title = "잠긴 크롭 — 크롭 편집(E)에서 잠금을 풀고";
        r.append(b);
      }
      if (lays.length) {
        line(s, "반영하면 이 크롭들은 새 생성 조건(생성 승인 대상)이 될 수 있음. 실수면 "
          + (lays.some((x) => x.back) ? "이름·위치는 [되돌리기], " : "") + "대상 문구·지움·새 크롭은 크롭 편집(E)에서 Ctrl+Z(그 창의 편집 기록) 또는 직접", "dim");
      }
      if (unsure) line(s, `레퍼런스·채택·Import 설정 미반영을 확인하지 못함(${unsure}) — 계획을 다시 계산`, "warn");
      else if (!pend.length) line(s, "반영됨 — 레이아웃·레퍼런스·채택·Import 설정 미반영·Prepare 입력 바뀜 없음", "ok");
      lastRow(s, "apply");
      if (PS.job && PS.job.stage === "apply") line(s, jobText(PS.job), "warn");
      const ab = button("반영 (무료)", "무료 반영 — Import → Prepare → Analyze → Review 템플릿을 ComfyUI 에 직접(탭 불필요)", once(applyNow), "primary");
      ab.disabled = !!PS.job || !!paying;
      btns(s, ab);

      // 초안·Run(계획) — 흐림(planDim — 반영 뒤 다시 계산)·낡음(stale — 다시 받을 때까지)이면 해시 복사 끔(copyHashes)
      const dim = planDim();
      const hc = copyHashes();
      s = section("비용·승인");
      if (!only) line(s, "선택한 크롭 없음 — 계획·승인을 끕니다(빈 only 는 '전부')", "warn");
      else if (PS.planErr) line(s, `계획: ${PS.planErr}`, "bad");
      else if (!planOk) line(s, PS.job ? `계산 대기 — 이 Studio 의 ${jobText(PS.job)} 이 끝난 뒤 계산(그동안 계획을 부르지 않음)` : "계산 중…", "dim");
      if (only && P0 && P0.settings && P0.settings.draft.apply === "full") {
        // 계획을 받는 중·실패해도 남김(체크를 풀 수 있게)
        const lb = el("label", "rf-check");
        const cb = el("input");
        cb.type = "checkbox";
        cb.checked = style;
        cb.dataset.keys = "board";
        cb.addEventListener("change", () => {
          style = cb.checked;
          cb.blur();
          planNow();
        });
        lb.append(cb, document.createTextNode(" 화풍 단위 포함(apply = full)"));
        s.append(lb);
      }
      if (planOk && only) {
        const why = pend.concat(unk ? [`반영 상태 확인 못 함 — ${unk}`] : [], !P0 || PS.err ? [`실행 설정을 읽지 못함 — ${PS.err || "불러오는 중"}`] : []);
        if (stale) line(s, "계획이 지금 파일 상태와 다름(다른 창·그래프의 쓰기·실행) — 다시 받기 전에는 승인 코드 복사 끔", "warn");
        if (dim) line(s, `반영 뒤 다시 계산 — 지금 승인 코드는 반영 뒤 달라질 수 있음${why.length ? `(${why.join(", ")})` : ""}`, "warn");
        const dt = draftText(p.draft, p.draft.running || !!(S && S.drafting)); // 실행 중에는 계획을 다시 받지 않으므로 state 로 흐림(Run 줄과 같이)
        line(s, dt.line, dt.dim || dim ? "dim" : "");
        for (const r of dt.rows) line(s, `· ${r}`, "dim");
        if ((p.draft.import_pending || []).length) line(s, `초안 승인 코드는 반영 뒤 확정: ${p.draft.import_pending.join(", ")}`, "warn");
        btns(s, approveButton("draft", dt.label, hc.draft), hashCopy("draft", hc.draft));
        lastRow(s, "draft");
        const rt = runText(p.run, p.run.running || !!(S && S.running));
        line(s, rt.line, rt.dim || dim ? "dim" : "");
        for (const r of rt.rows) line(s, `· ${r}`, "dim");
        // D9: 이 Studio 의 유료 Run 이 wave 로 멈춤 — 끝난 뒤 새 계획·새 해시로 '이어서 승인'(확인 창 — 클릭만, 자동 없음). 재굴림 실행의 wave 는
        // 남은 것이 사전 승인분이라 승인 없이 'ComfyUI 큐'로. 줄의 수 = wave 가 멈출 때 남은 호출, 단추의 수 = 새 계획의 승인 필요(다를 수 있음)
        const cont = !!wave && wave.stage === "run" && !PS.job && p.run.need > 0;
        if (wave && !PS.job) {
          line(s, wave.stage === "run" ? `시간 상한으로 멈춤 — 멈출 때 남은 ${wave.remaining}건: 새 계획(단추의 건수)으로 이어서 승인(클릭만 — 자동 이어가기 없음)`
            : `다시 생성 시간 상한으로 멈춤 — 남은 ${wave.remaining}건: 사전 승인분이라 머리줄 [다시 생성 보내기](또는 다음 Run)로 이어서`, "warn");
        }
        btns(s, approveButton("run", cont ? `남은 ${p.run.need}건 이어서 승인 ≈ ${usd(p.run.est_usd)}` + (p.run.backend === "mock" ? " [MOCK]" : "") : rt.label, hc.run),
          hashCopy("run", hc.run));
        lastRow(s, "run");
        lastRow(s, "reroll");
        // Run 해시는 retry_failed·retry_orphans 로 승인 대상이 바뀜(그래프 Run 위젯 값). 그래프 Import PSD 는 같은 큐에서 Run·Draft 보다 먼저 돌아
        // 위젯 값으로 매니페스트 source 를 다시 씀 — Studio import 절과 다르면 초안 키·jobs 가 바뀌어 해시가 맞지 않음(Studio 는 그래프 위젯을 모름)
        const rc = P0 && P0.settings ? P0.settings.run : null;
        line(s, "승인 코드는 그래프 Import PSD 위젯(같은 큐에서 먼저 돎)·Run·Draft 노드의 only·Prepare·초안 설정과 Run 노드의 retry_failed·retry_orphans"
          + `${rc ? `(Studio ${rc.retry_failed}·${rc.retry_orphans})` : ""} 가 Studio 와 같을 때만 같습니다 — 다르면 과금 없이 드라이런`, "dim");
      }
      if (p && p.run.by_crop) s.append(cropTable(p.run.by_crop));
      if (p && p.notes.length) for (const n of p.notes) line(s, n, "dim");
      const pg = PS.progress;
      if (pg) {
        const r = pg.run;
        line(s, `진행 — Run ${pg.running ? "실행 중" : "멈춤"}${pg.drain ? " · 새 호출 멈춤 요청됨" : ""} · 완료 ${r.done} · 진행 ${r.inflight} · 실패 ${r.failed}`
          + ` · 결과 못 받음 ${r.orphaned} · 대기 승인분(전체) ${r.need} · 다시 생성 ${r.pre}`, pg.running ? "warn" : "dim");
        if (pg.draft) {
          const d = pg.draft;
          line(s, `초안 진행 — 진행 ${d.inflight} · 받음 ${d.created} · 완료 ${d.done} · 실패 ${d.failed}`, "warn");
        }
      }
      const db = button("새 호출 멈춤", "진행 중인 Run 이 새 호출 발사를 멈춥니다(진행 중인 호출은 끝까지 받아 저장) — Drain", opts.drain);
      db.disabled = !(S && S.running);
      btns(s, db, recalcButton("지금 선택으로 계획을 다시 받습니다"));

      // ★ 점검
      s = section("★ 점검");
      const checks = starChecks(S, opts.seams(), pend, maskLeft);
      if (unsure) checks.push({ text: `반영 상태를 확인하지 못함(${unsure}) — 계획을 다시 계산한 뒤 Export`, key: null });
      if (!checks.some((c) => !c.info)) line(s, "점검 통과 — 모든 타깃에 ★, 미반영 없음", "ok");
      for (const c of checks) {
        const r = line(s, c.text, (c.info ? "dim" : "warn") + (c.key || c.mask ? " link" : ""));
        if (c.mask) r.addEventListener("click", () => opts.goMask(c.mask, c.key));
        else if (c.key) r.addEventListener("click", () => opts.goTarget(c.key));
        if (c.grow && opts.goGrow) r.append(button("크롭 넓히기 제안", "크롭 편집에서 넓힐 영역 미리보기 — Enter 적용", (e) => {
          e.stopPropagation(); // 같은 줄의 이음매 클릭(마스크 모드)과 분리
          if (setupBusy()) opts.toast("앞 요청을 처리하는 중입니다 — 끝난 뒤 다시 하세요", "warn");
          else opts.goGrow(c.grow.crop, c.grow.sides);
        }));
      }

      // Export
      s = section("Export");
      exportRows(s, P0);
      const alt = P0 && P0.settings && !P0.error ? P0.settings.export.alternates : null;
      if (alt) {
        // F27: ★ 아닌 후보 레이어를 넣을지 = Export 노드 alternates(hidden_all ↔ none) — 실행 설정 Export 절에 저장
        const lb = el("label", "pp-opt");
        const cb = el("input");
        cb.type = "checkbox";
        cb.checked = alt !== "none";
        cb.disabled = !!PS.job || !!paying || PS.busy;
        cb.dataset.keys = "board";
        cb.addEventListener("change", once(async () => {
          cb.blur();
          try {
            // 누를 때의 실행 설정(그린 뒤 폴링이 다시 읽었을 수 있음) — 다른 Export 칸 값을 옛 값으로 덮지 않게
            await PS.save("export", Object.assign({}, PS.P.settings.export, { alternates: cb.checked ? "hidden_all" : "none" }));
          } catch (e) {
            opts.toast(`Export 설정을 저장하지 못함 — ${e.message}`, "bad");
          }
        }));
        lb.append(cb, document.createTextNode(" ★ 아닌 후보 레이어도 넣기(숨김·빨강 라벨 — 끄면 ★ 만)"));
        lb.title = `Export 노드 alternates — 켬 = hidden_all(★ 아닌 후보도 숨김·빨강 라벨로), 끔 = none(★ 만). 제외(X) 후보는 어느 쪽이든 넣지 않음. `
          + `지금 ${alt} · 실행 설정(pipeline.json) Export 절에 저장`;
        s.append(lb);
      }
      const eb = button("Export", "무료 Export 템플릿(Analyze → Compose → Export — Import 없음, ComfyUI 대기열 뒤)", () => {
        // 위 체크(alternates)를 저장하는 동안은 내지 않음 — 옛 설정으로 큐되지 않게(Codex 2차). 단추도 그동안 끔
        if (asking || PS.busy) return;
        // ★ 가 하나도 없으면 숨김 후보만 담긴 PSD(또는 ★ 만이면 PSD 없음)가 되므로 한 번 묻는다(10/21 실사용 r14)
        if (!S.targets.some((t) => t.pick_keys.length) && !window.confirm(`★ 가 하나도 없습니다 — ${alt === "none" ? "넣을 후보가 없어 PSD 를 쓰지 않습니다"
          : "★ 없이 숨김 후보 레이어만 담긴 PSD 가 만들어집니다"}.\n그래도 Export 할까요?`)) return;
        queueHere("export");
      }, "primary");
      eb.disabled = !!PS.job || !!paying || asking || PS.busy;
      const ob = button("출력 폴더 열기", "탐색기로 Export 출력 폴더를 엶 — 마지막 Export PSD 를 고른 채(ComfyUI 가 도는 PC). "
        + `다음 Export 위치: ComfyUI ${exportDir(P0)}([실행 설정 열기] Export 절 '출력 하위 폴더' — 비우면 프로젝트 폴더 이름)`,
        async () => {
          try {
            const d = await PS.openFolder();
            opts.toast(`출력 폴더를 열었습니다${d.selected ? "(마지막 Export PSD 고름)" : ""}: ${d.path}`);
          } catch (e) {
            opts.toast(e.message, "err");
          }
        });
      btns(s, eb, ob);
      psMaskPanel(s);

      renderAdopt();
    }

    function lastRow(s, stage) {
      // 이 Studio 가 낸 그 stage 의 마지막 끝 — 오류·취소 문구, 유료면 그 뒤에 Run·Spec Draft 보고 한 줄(paidReport — Run 뒤 노드에서 멈춰도 보고는 있음),
      // 인증 중지(LOGIN)는 끝이어도 경고. 그 단계 승인의 결과를 모르면(asked 의 lost — 이 창·새로고침 전 창이 보낸 것) 해시마다 그 줄
      const x = PS.last[stage];
      const why = x ? [x.msg, x.report].filter(Boolean).join(" · ") : "";
      if (x) {
        line(s, `이 Studio 의 마지막 ${STAGES[stage].label}: ${x.at} ${END[x.state]}${why ? ` — ${why}` : ""}`,
          x.state !== "done" ? "bad" : (x.report || "").includes(LOGIN) ? "warn" : "dim");
      }
      for (const h of new Set((asked[stage] || []).filter((a) => a.lost).map((a) => a.hash))) {
        line(s, `앞서 보낸 ${STAGES[stage].label} 승인${h ? `(승인 코드 ${h})` : ""}의 결과를 모름 — 이미 ComfyUI 큐에 들어갔을 수 있음: `
          + "다시 보내기 전에 ComfyUI 큐 확인", "warn");
      }
    }

    function approveButton(kind, label, pair) {
      // 초안·Run 승인 단추(pair = copyHashes 의 [해시, only] — 그린 값). 꺼진 이유는 제목에
      const why = approveWhy(pair[0]);
      const b = button(label, why || `확인 창 뒤 ComfyUI 탭이 승인 템플릿을 큐에 넣음 — approve: ${pair[0]}  only: ${pair[1]}`,
        (e) => approve(e, kind, label, pair), "primary");
      b.disabled = !!why;
      return b;
    }

    function hashCopy(kind, [hash, only]) {
      // 해시 복사(§3.3): 그래프 Run·Draft 노드의 approve·only 칸에 넣을 값 한 줄. 누를 때 지금 판정(copyHashes)과 다르면(입력 중이라 미뤄 둔 패널의
      // 옛 단추 — 그 사이 계획이 낡거나 바뀜·Run 시작) 복사하지 않고 다시 그림
      const b = button("승인 코드 복사", hash ? `approve: ${hash}  only: ${only}` : "복사할 승인 코드 없음(승인할 것 없음·미반영·실행 중)", () => {
        if (!same(copyHashes()[kind], [hash, only])) {
          opts.toast("계획이 바뀜(다른 창·그래프의 쓰기·실행) — 승인 코드를 복사하지 않음: 다시 그린 「비용·승인」 칸을 보고 다시 누르세요", "warn");
          render();
          return;
        }
        copy(`approve: ${hash}  only: ${only}`);
      });
      b.disabled = !hash;
      return b;
    }

    function recalcButton(title) {
      // [다시 계산] — 이 Studio 의 무료 프롬프트가 대기·실행 중이면 계획을 부르지 않으므로(loadPlan) 끄고 이유를 제목에
      const b = button("다시 계산", PS.job ? `이 Studio 의 ${jobText(PS.job)} 이 끝난 뒤 계산(끝나면 저절로)` : title, planNow);
      b.disabled = !!PS.job;
      return b;
    }

    function cropTable(bc) {
      const t = el("table", "pp-table");
      const hr = el("tr");
      for (const h of ["크롭", "승인 필요", "다시 생성", "복구", "완료", "실패", "결과 못 받음", "진행"]) hr.append(el("th", "", h));
      t.append(hr);
      const ids = S.crops.map((c) => c.id).filter((id) => bc[id]).concat("_stale" in bc ? ["_stale"] : []);
      for (const id of ids) {
        const v = bc[id];
        const c = S.crops.find((x) => x.id === id);
        const tr = el("tr", v.selected ? "sel" : "");
        tr.append(el("td", "", id === "_stale" ? "옛 조건" : (c && c.name) || id));
        for (const k of ["need", "pre", "free", "done", "failed", "orphaned", "inflight"]) tr.append(el("td", "", v[k] || ""));
        t.append(tr);
      }
      return t;
    }

    let psMask = null; // Export 절의 선택·미리보기만 이 창에서 유지(폴링으로 패널을 다시 그려도 보존)

    async function psMaskResult(result) {
      const imported = result.imported || [];
      if (imported.length) {
        opts.toast(`PS 마스크 ${imported.length}개를 마스크 편집으로 가져왔습니다 — 마스크 모드에서 확인·되돌리기`, (result.failed || []).length ? "warn" : "");
        await opts.refresh();
      } else {
        const reasons = [...(result.failed || []), ...(result.skipped || [])].map((r) => r.reason).filter(Boolean);
        opts.toast(`PS 마스크 가져오기 실패 — ${[...new Set(reasons)].slice(0, 3).join(" · ") || result.error || "가져온 마스크가 없습니다 — 다시 미리 보기"}`, "err");
      }
    }

    async function psMaskAction(kind) {
      if (psMask && psMask.busy) return;
      if (kind === "open") psMask = { rows: [], psd: "", preview: null, result: null, error: null, busy: false };
      const state = psMask;
      if (!state) return;
      if (kind === "import" && (!state.preview || !state.preview.changed.length || !window.confirm(
        `다음 그룹의 PS 마스크를 마스크 편집 새 판으로 가져옵니다(무료).\n${state.preview.changed.map((r) => r.label).join("\n")}\n마스크 모드에서 되돌릴 수 있습니다. 계속할까요?`))) return;
      state.busy = true;
      state.error = null;
      state.result = null;
      render();
      try {
        if (kind === "open") {
          state.rows = await PS.psList();
          state.psd = state.rows.length ? state.rows[0].psd : "";
        } else if (kind === "preview") {
          state.preview = null;
          state.preview = await PS.psImport(state.psd, true, null);
        } else {
          const result = await PS.psImport(state.psd, false, state.preview.seen);
          state.result = result;
          state.preview = null;
          await psMaskResult(result);
        }
      } catch (e) {
        state.error = e.message;
        state.preview = null;
        if (e.data && e.data.imported) {
          state.result = e.data;
          await psMaskResult(e.data);
        }
      } finally {
        state.busy = false;
        render();
      }
    }

    function psMaskPanel(s) {
      const open = button("PS 마스크 가져오기", "이 프로젝트에서 Export 한 PSD의 그룹 마스크를 마스크 편집으로 가져옴(무료)", () => psMaskAction("open"));
      open.disabled = !!(psMask && psMask.busy);
      btns(s, open);
      if (!psMask) return;
      const state = psMask;
      line(s, "PS 에서 같은 이름으로 덮어 저장하거나, 같은 폴더에 원래 이름으로 시작하는 다른 이름(예: 원래이름_ps.psd)으로 저장하면 목록에 나옵니다.", "dim");
      line(s, "같은 이름으로 덮어쓰면 다음 Export에서 바뀐 것이 없을 때 그 파일을 '변경 없음'으로 돌려줍니다.", "dim");
      line(s, "그룹 마스크만 가져옵니다. SO 레이어 마스크·벡터 마스크는 그룹 마스크로 옮겨 저장하세요.", "dim");
      if (state.error) line(s, state.error, "bad");
      if (state.busy) line(s, "PS 마스크를 읽고 있습니다…", "dim");
      if (!state.rows.length) {
        if (!state.busy && !state.error) line(s, "가져올 Export PSD가 없습니다(PSD와 옆 사이드카 필요)", "dim");
        return;
      }
      const pick = el("select", "pp-psmask-picker");
      pick.title = "가져올 Export PSD(최근 것부터)";
      pick.dataset.keys = "board";
      let originalShown = null;
      for (const row of state.rows) {
        if (row.copy_of && originalShown !== row.copy_of.toLowerCase()) {
          const missing = el("option", "", `r${row.rev} · ${row.time} · ${row.copy_of} (원본 파일 없음)`);
          missing.disabled = true;
          pick.append(missing);
        }
        originalShown = (row.copy_of || row.psd).toLowerCase();
        const option = el("option", "", `${row.copy_of ? "\u00a0\u00a0↳ 사본 · " : ""}r${row.rev} · ${row.time}${row.modified ? " · PS 에서 고친 듯" : ""} · ${row.psd}`);
        option.value = row.psd;
        if (row.copy_of) option.title = `원래 Export PSD: ${row.copy_of}`;
        pick.append(option);
      }
      pick.value = state.psd;
      pick.disabled = state.busy;
      pick.addEventListener("change", () => {
        state.psd = pick.value;
        state.preview = state.result = state.error = null;
        pick.blur();
        render();
      });
      s.append(pick);
      const preview = button("미리 보기", "저장하지 않고 바뀐 그룹과 건너뛸 이유 확인", () => psMaskAction("preview"));
      const take = button("가져오기 (무료)", "미리 본 그룹을 마스크 편집 새 판으로 저장", () => psMaskAction("import"));
      preview.disabled = state.busy || !state.psd;
      take.disabled = state.busy || !state.preview || !state.preview.changed.length;
      btns(s, preview, take);
      const result = state.preview || state.result;
      if (!result) return;
      line(s, `바뀐 그룹 ${result.changed.length} · 건너뜀 ${result.skipped.length}`
        + (result.imported ? ` · 가져옴 ${result.imported.length} · 실패 ${result.failed.length}` : ""));
      const table = el("table", "pp-table pp-psmask");
      const row = (values, tag = "td") => {
        const tr = el("tr");
        for (const value of values) tr.append(el(tag, "", value));
        table.append(tr);
      };
      row(["그룹", "결과·이유"], "th");
      for (const r of result.changed) row([r.label, `차이 ${r.diff_px} px · 창 밖 ${r.outside_px} px`]);
      for (const r of result.skipped) row([r.name, `건너뜀 — ${r.reason}`]);
      for (const r of result.failed || []) row([r.key, `실패 — ${r.reason}`]);
      s.append(table);
      for (const warning of result.warnings) line(s, warning, "warn");
    }

    function exportRows(s, P0) {
      const ex = PS.last.export;
      if (PS.job && PS.job.stage === "export") line(s, jobText(PS.job), "warn");
      if (ex) {
        const o = ex.export;
        if (ex.state !== "done") line(s, `이 Studio 의 마지막 Export: ${ex.at} ${END[ex.state]}${ex.msg ? ` — ${ex.msg}` : ""}`, "bad");
        else if (!o) line(s, `이 Studio 의 마지막 Export: ${ex.at} 끝 — 결과 판정 없음(노드가 옛 버전 — ComfyUI 재시작)`, "warn");
        else if (o.outcome === "new") {
          const e = P0 && P0.exports.find((x) => x.rev === o.rev);
          line(s, `새 PSD r${o.rev}: ${o.psd}` + (e ? ` · 레이어 ${e.layers ?? "?"} · 그룹 마스크 ${e.mask_groups ?? "?"} (편집 ${e.mask_edits ?? "?"})`
            + ` · 문제 ${e.problems ?? "?"} · ${mb(e.bytes)}` : ""), "ok");
          btns(s, button("경로 복사", o.psd, () => copy(o.psd)));
        } else if (o.outcome === "unchanged") {
          line(s, `변경 없음 — 기존 파일 r${o.rev}: ${o.psd}`, "dim");
        } else {
          line(s, "PSD 를 쓰지 않음 — 넣을 후보 없음(★ 도 숨김 후보도 없음)", "warn");
        }
      }
      for (const x of (P0 && P0.exports) || []) {
        // 편집 수는 S4 Export 기록만(그 전 기록 null — 편집이 없던 때라 줄에서 뺌)
        const k = x.mask_edits ?? null;
        const r = line(s, `r${x.rev} · ${x.time}` + (k === null ? "" : ` · 편집 ${k}`) + ` · 문제 ${x.problems ?? "?"} · ${mb(x.bytes)}`, "dim link");
        r.title = `${x.psd} — 눌러 경로 복사`;
        r.addEventListener("click", () => copy(x.psd));
      }
    }

    function exportDir(P0, sub) {
      // Export 출력 폴더(노드 _output_dir — 배치 3-5b): sub = 입력 중인 칸 값(없으면 실행 설정 값), 비면 프로젝트 폴더 이름(GET /pipeline folder)
      const saved = P0 && P0.settings ? P0.settings.export.subfolder : "";
      const v = String(sub ?? saved ?? "").trim(); // saved 없음 = 옛 서버
      return `output/design_patch/${v || (P0 && P0.folder) || "<프로젝트 폴더>"}`;
    }

    function mb(bytes) {
      // 노드 Export 보고서와 같은 식(바이트 / 1e6, 소수 한 자리 — 같은 PSD 가 두 숫자로 보이지 않게)
      return bytes === null || bytes === undefined ? "크기 ?" : `${(bytes / 1e6).toFixed(1)}MB`;
    }

    function renderAdopt() {
      const c = cur && S ? S.crops.find((x) => x.id === cur) : null;
      const s = section(`채택 — ${c ? c.name || c.id : "크롭 없음"}`);
      if (!cur) {
        line(s, "왼쪽에서 크롭을 고르세요", "dim");
        return;
      }
      if (dvErr) line(s, dvErr, "bad");
      const reread = () => button("다시 읽기", "이 크롭의 스펙·초안을 다시 읽음", loadDraft);
      if (!dv) {
        if (dvErr) btns(s, reread());
        else line(s, "불러오는 중…", "dim");
        return;
      }
      if (dv.spec_error) line(s, dv.spec_error, "bad");
      line(s, "지금 타깃: " + (dv.targets.map((t) => `${t.tid} ${t.label}${t.name_en && t.name_en !== t.label ? `(${t.name_en})` : ""} · ${ORIGIN[t.origin] || t.origin}`)
        .join(" | ") || "없음"));
      if (dv.draft) line(s, `초안 ${dv.draft.backend}${dv.draft.mock ? " [MOCK]" : ""} · ${dv.draft.drafted || "시각 없음"}`, "dim");
      for (const g of dv.suggestions) {
        const r = line(s, `제안 ${g.tid}: ${g.text}` + (g.reason ? ` — ${g.reason}` : ""), "dim");
        if (g.bbox) r.title = `bbox [${g.bbox.join(", ")}]`;
      }
      const a = adoptState(dv);
      if (a.why) line(s, a.why, a.can ? "" : "warn");
      const lt = dv.layer_targets;
      line(s, `_layer_targets: ${lt.value === null ? "(없음)" : lt.value} · ${OWNER[lt.owner] || "기록 없음"}`);
      // layout.json 에 문구가 있는 tid(정보) — 채택·수정 값의 같은 tid 문구와 다를 때 실제로 이기는 경고는 미리보기 warnings(layout_wins)가 싣는다
      const lay = Object.keys(dv.layout_text);
      if (lay.length) line(s, `레이아웃 문구 있음(${lay.join(", ")}) — 같은 tid 는 채택·수정 값보다 레이아웃 문구가 이김(미리보기 경고 참고)`, "dim");
      const ab = button("채택", a.can ? `제안 그대로: ${dv.adopt.value}` : a.why, () => writeAdopt("adopt"), "primary");
      ab.disabled = !a.can || PS.busy;
      const eb = button("수정", "_layer_targets 를 사람 값으로(\"A: …; B: …\")", () => {
        text = lt.value || dv.adopt.value || "";
        pv = null;
        render();
        schedulePreview();
      });
      eb.disabled = !!dv.spec_error || text !== null;
      const cb = button("해제", "_layer_targets 키 삭제(노드 값을 해제하면 다시 채택은 [수정]으로만)", () => writeAdopt("clear"), "danger");
      cb.disabled = !!dv.spec_error || lt.value === null || PS.busy;
      const bs = [ab, eb, cb];
      // 스펙을 읽지 못함(잠김 — 풀려도 파일 stat 이 그대로라 rev·plan_v 가 안 바뀌어 저절로 다시 읽지 않음)·JSON 오류·GET /draft 실패(옛 값을 보임):
      // 손으로 다시 읽기
      if (dv.spec_error || dvErr) bs.push(reread());
      btns(s, ...bs);
      if (text !== null) {
        const inp = el("input", "pp-text");
        inp.type = "text";
        inp.value = text;
        inp.placeholder = "A: …; B: …";
        inp.addEventListener("input", () => {
          text = inp.value;
          schedulePreview();
        });
        inp.addEventListener("keydown", (e) => {
          if (e.isComposing || e.keyCode === 229) return;
          if (e.key === "Enter" && !e.repeat) writeAdopt("set"); // 누르고 있는 Enter 의 반복은 버림
          else if (e.key === "Escape") {
            closeEdit();
            inp.blur();
            render();
          }
        });
        s.append(inp);
        const sb = button("저장", "미리보기 → 확인 → 쓰기", () => writeAdopt("set"), "primary");
        sb.disabled = PS.busy || !!(pv && pv.err && pv.status === 400); // 문법·렌더 점검 거절만 끔(409 는 저장 경로가 다시 읽음)
        btns(s, sb, button("취소", "Esc", () => {
          closeEdit();
          render();
        }));
        if (pv && pv.err) line(s, pv.err, "bad");
        if (pv && pv.r) for (const x of effectLines(pv.r)) line(s, x, "warn");
      }
    }

    function renderSettings() {
      const P0 = PS.P;
      const top = el("div", "lp-btns");
      top.append(el("span", "lp-title", "실행 설정 — pipeline.json"), button("← 실행", "설정 닫기", () => {
        settingsOn = false;
        render();
      }));
      panel.append(top);
      if (!P0) {
        line(panel, PS.err ? `읽기 오류: ${PS.err}` : "불러오는 중…", PS.err ? "bad" : "dim");
        return;
      }
      if (P0.error) line(panel, `파일이 깨짐: ${P0.error} — 절을 저장하면 덮어씁니다`, "bad");
      line(panel, "노드 위젯 이름·값 그대로 — 절마다 통째로 저장합니다(rev). pipeline.json 은 노드가 읽지 않고 Studio 템플릿에만 쓰입니다.", "dim");
      if (!P0.saved) line(panel, "파일 없음 — 지금 값은 프로젝트 기록에서 유도한 값입니다(첫 저장이 파일을 만듦)", "warn");
      btns(panel, button("마지막 실행 값으로 채우기", "지금 유도값(마지막 Import·Prepare·Export·초안 출처 — 다시 읽음)을 절마다 저장 — 확인 필요 항목도 확인됨으로",
        once(resetDerived)));
      for (const [sec, ws] of Object.entries(P0.widgets)) {
        const base = P0.settings ? P0.settings[sec] : P0.derived.settings[sec];
        drawn[sec] = base;
        const box = el("div", "pp-sec");
        box.append(el("div", "lp-k", SECTIONS[sec] || sec));
        if (SECTION_WARN[sec]) line(box, SECTION_WARN[sec], "dim");
        for (const d of P0.drift.filter((x) => x.section === sec)) line(box, driftText(d), DRIFT_BILL.includes(d.kind) ? "bad" : "warn");
        const need = confirmOf(P0).filter((c) => c.startsWith(sec + "."));
        for (const [k, spec] of Object.entries(ws)) box.append(field(sec, k, spec, base[k], need.includes(`${sec}.${k}`)));
        const sb = button(need.length ? "확인·저장" : "저장", need.length ? `확인 필요: ${need.join(", ")} — 이 값으로 저장하면 확인됨` : "이 절을 저장",
          () => saveSection(sec), need.length ? "primary" : "");
        sb.disabled = PS.busy;
        btns(box, sb);
        panel.append(box);
      }
    }

    function field(sec, k, spec, value, confirm) {
      // 위젯 하나(GET /pipeline widgets — 노드 선택지·범위 그대로, JS 에 표를 두지 않음)
      // 손댄 칸인데 편집을 시작한 뒤 다른 창이 그 키를 저장함(moved) = 그 값을 칸 옆에 — 저장하면 확인 창이 덮어쓸지 묻는다
      const mine = !!form[sec] && k in form[sec];
      const moved = mine && !!edits[sec] && k in edits[sec] && !same(edits[sec][k], value);
      // Export 하위 폴더(배치 3-5b)만 위젯 이름 대신 뜻을 라벨로 + 칸 아래 실제 출력 폴더(지금 칸 값)
      const sub = sec === "export" && k === "subfolder";
      const r = el("div", "lp-row pp-field" + (sub ? " pp-sub" : "") + (confirm ? " bad" : moved ? " warn" : ""));
      const from = PS.P.derived.from[`${sec}.${k}`];
      const lab = el("span", "k", (sub ? "출력 하위 폴더" : k) + (confirm ? " (확인 필요)" : ""));
      if (from) lab.title = `처음 값 출처: ${from}`;
      if (sub) lab.title = `Export 노드 subfolder${from ? ` · 처음 값 출처: ${from}` : ""}`;
      const cur0 = mine ? form[sec][k] : value;
      const set = (v) => {
        // 첫 편집 기준값 = 지금 화면 기준값(drawn — 이 창이 저장한 뒤 다시 그리기가 미뤄졌으면 그 저장값, 그리던 때의 value 가 아님)
        const e = (edits[sec] = edits[sec] || {});
        if (!(k in e)) e[k] = drawn[sec][k];
        (form[sec] = form[sec] || {})[k] = v;
      };
      let inp;
      if (spec.type === "COMBO") {
        inp = el("select");
        for (const c of spec.choices.includes(cur0) ? spec.choices : [cur0, ...spec.choices]) {
          const o = el("option", "", c);
          o.value = c;
          inp.append(o);
        }
        inp.value = cur0;
        inp.addEventListener("change", () => set(inp.value));
      } else if (spec.type === "BOOLEAN") {
        inp = el("input");
        inp.type = "checkbox";
        inp.checked = !!cur0;
        inp.addEventListener("change", () => set(inp.checked));
      } else {
        inp = el("input");
        if (spec.type === "INT" || spec.type === "FLOAT") {
          inp.type = "number";
          if (spec.min !== undefined) inp.min = String(spec.min);
          if (spec.max !== undefined) inp.max = String(spec.max);
          inp.step = spec.type === "INT" ? "1" : String(spec.step || "any");
        } else {
          inp.type = "text";
        }
        inp.value = cur0 === null || cur0 === undefined ? "" : String(cur0);
        inp.addEventListener("input", () => set(inp.value));
      }
      inp.disabled = !!spec.readonly;
      if (spec.readonly) inp.title = "읽기 전용(그래프에서 바꿈)";
      r.append(lab, inp);
      if (sub) {
        // 이름 검사는 저장할 때 서버(400 문구 그대로 토스트) — 여기는 앞뒤 공백만 떼어 보임(서버와 같음). 경로 구분자·'..' 는
        // 하위 폴더처럼 보이는 경로를 미리 보이지 않고 저장 안 됨을 먼저 알림(8189 — 'a/b' 가 output/design_patch/a/b 로 보였음)
        const hint = el("span", "dim pp-hint");
        const show = () => {
          hint.textContent = /[\\/:]|\.\./.test(inp.value) ? "폴더 이름 하나만 — 경로 구분자(/ \\ :)·.. 는 저장되지 않음"
            : `비우면 프로젝트 폴더 이름 — ${exportDir(PS.P, inp.value)}`;
        };
        show();
        inp.addEventListener("input", show);
        r.append(hint);
      }
      if (moved) r.append(el("span", "dim", `다른 창이 ${shown(value)}(으)로 바꿈`));
      return r;
    }

    panel.addEventListener("compositionstart", () => {
      composing = true;
    });
    panel.addEventListener("compositionend", () => {
      composing = false;
      schedulePreview();
    });
    panel.addEventListener("focusout", () => setTimeout(flush, 0));
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) setupLastTry = -Infinity; // 보드의 visibilitychange → refresh 에서 바로 재판정
    });
    const release = () => {
      held = false;
      setTimeout(flush, 0); // click 처리 뒤
    };
    document.addEventListener("pointerdown", () => {
      held = true;
    }, true);
    document.addEventListener("pointerup", release, true);
    document.addEventListener("pointercancel", release, true);
    // pointerup 을 놓친 경우(스크롤 막대를 끌다 놓음·창 밖에서 놓음): 버튼 없이 움직이면 놓은 것으로
    document.addEventListener("pointermove", (e) => {
      if (held && !e.buttons) release();
    }, true);
    // mock 확인 상자가 열린 동안 보드 키를 막고 Esc = 취소
    document.addEventListener("keydown", (e) => {
      if (!ask) return;
      e.stopPropagation();
      if (e.key === "Escape") {
        e.preventDefault();
        ask.close(false);
      }
    }, true);
    if (L) {
      L.onWave((d) => {
        // D9: 이 Studio 의 유료 Run·재굴림(실행 중이거나 방금 끝남 — 그 prompt_id)이 wave 로 멈춤 → 끝난 뒤 Run 줄 '남은 N건'(Run 은 '이어서 승인' — 클릭만)
        if (d.rootKey !== opts.root) return;
        const stage = ["run", "reroll"].find((k) => {
          const j = PS.job && PS.job.stage === k ? PS.job : PS.last[k];
          return !!j && j.pid === d.prompt_id;
        });
        if (!stage) return;
        wave = { stage, pid: d.prompt_id, remaining: d.remaining };
        render();
      });
    }
    resumeAsks();

    PS.onChange = render;
    PS.onWrite = () => opts.refresh();
    PS.onDone = (job, res) => {
      const label = STAGES[job.stage].label;
      const o = res.export;
      const w = wave && wave.pid === job.pid
        ? ` · 시간 상한으로 멈춤(남은 ${wave.remaining}건) — ${wave.stage === "run" ? "Run 줄에서 이어서 승인" : "머리줄 [다시 생성 보내기]로 이어서"}` : "";
      if (res.state !== "done") opts.toast(`${label}: ${END[res.state]}${res.msg ? ` — ${res.msg}` : ""}`, res.state === "cancelled" ? "warn" : "err");
      else if (STAGES[job.stage].paid) opts.toast(`${label} 끝 — ${PS.last[job.stage].report}${w}`, w || PS.last[job.stage].report.includes(LOGIN) ? "warn" : "");
      else if (job.stage !== "export") opts.toast(`${label} 끝 — 새 상태·계획을 다시 받습니다`);
      else if (!o) opts.toast("Export 끝 — 결과 판정 없음(노드가 옛 버전 — ComfyUI 재시작)", "warn");
      else opts.toast(o.outcome === "new" ? `Export: 새 PSD r${o.rev} — ${o.psd}` : o.outcome === "unchanged" ? `Export: 변경 없음 — 기존 파일 ${o.psd}`
        : "Export: PSD 를 쓰지 않음 — 넣을 후보 없음(★ 도 숨김 후보도 없음)", o.outcome === "empty" ? "warn" : "");
      // 계획은 다시 받은 state 뒤에(§3.1): 반영이 지운 크롭이 아직 선택(only)에 남아 서버가 400('매니페스트에 없는 크롭')을 내지 않게
      doneReplan = true;
      opts.refresh();
      PS.load().then(pipelineLoaded);
    };

    function setMaskLeft(v) {
      maskLeft = v;
      if (on) render();
    }

    function loadKey(st) {
      // GET /pipeline 을 다시 받을 판: 매니페스트 rev + board_pref.json 지문(Studio 처음 값이 마지막 Export·분석 설정을 따름 — store.last_export)
      return `${st.rev}|${st.pref_v}`;
    }

    function stateChanged(st) {
      stateReads += 1;
      // 보드 state 를 받을 때마다(2초 폴링 — unchanged 응답이면 같은 S). 다시 그리기는 state·진행 수가 바뀐 때만(누르는 중인 단추를 갈지 않게)
      const k = [st.rev, st.running, st.drain, st.pipeline_rev, st.plan_v, st.pref_v].join("|");
      const changed = st !== S || k !== seenKey;
      seenKey = k;
      S = st;
      if (!("pipeline_rev" in st)) {
        if (changed) render(); // 옛 서버(재시작 전) — 머리줄 없음
        return;
      }
      const was = onlyNow(); // 새 크롭 더함·사라진 크롭 뺌(반영·그래프 Import 뒤)이면 선택이 바뀜
      for (const c of st.crops) {
        if (known.has(c.id)) continue;
        known.add(c.id);
        sel.add(c.id);
      }
      for (const id of [...sel]) if (!st.crops.some((c) => c.id === id)) sel.delete(id);
      if (st.pipeline_rev !== seenPl) {
        const first = seenPl === undefined;
        seenPl = st.pipeline_rev;
        seenLoad = loadKey(st);
        const loaded = PS.load();
        if (first) {
          loaded.then(pipelineLoaded);
          PS.resume();
          planNow(); // Studio 를 연 뒤 한 번(진행 모드가 아니면 only 없음 — 검토 모드 칩용)
        } else {
          loaded.then(() => { firstSetup(); planNow(); }); // 설정이 저장됨(다른 창 포함) — 새 설정(초안 apply·화풍 포함)으로 계획을 다시
        }
      } else if (loadKey(st) !== seenLoad) {
        // 매니페스트가 바뀜(그래프 Prepare·Import 는 pipeline.json 을 두고 prepare_settings·source 만 바꿈) 또는 board_pref.json(pref_v — 분석·Export
        // 설정, rev 그대로)이 바뀜 — 갈라짐·유도값·Export 목록을 다시(확인 창 맨 앞 줄·[매니페스트 값으로 다시]·설정 칸이 이 값을 쓴다. 편집기 확인 창도 —
        // 진행 모드가 아니어도)
        seenLoad = loadKey(st);
        PS.load().then(pipelineLoaded);
      } else if (PS.err || (PS.P && !("manifest_rev" in PS.P))) {
        // 앞 GET /pipeline 이 실패함(잠김·재시작 중) 또는 옛 서버(S3a 판 — manifest_rev 없음: 새 JS 를 받은 뒤 ComfyUI 재시작 전) — rev 가 그대로여도
        // 폴링마다 다시(머리줄 '설정 확인 필요'·'Prepare 갈라짐'·설정 칸이 '읽기 오류'·옛 값에, 승인 단추·R 길이 '재시작 필요'에 멈추지 않게 — 재시작해도
        // pipeline_rev·매니페스트 rev 는 그대로). 다시 그리기는 읽기가 돌아왔거나 서버가 새 판이 된 때만
        const failed = !!PS.err;
        PS.load().then((ok) => ok && (failed || "manifest_rev" in PS.P) && pipelineLoaded());
      }
      // 계획을 한 번 다시(서버는 같은 서명·선택이면 계산 없이 기억값): 진행 모드에서 선택이 바뀜, 쉬는 중에 계획이 지금 파일과 달라짐(다른 창·그래프의
      // 쓰기 — plan_v)·실행이 끝남, 또는 계획을 한 번도 받지 못했는데(202·오류) 서버에 새 계획이 생김(모든 모드 — 머리줄). 실행 중에는 원장·초안 원장이
      // 계속 바뀌어 서명이 폴링마다 달라지므로 끝날 때까지 미룬다(Run 줄은 S.running 으로 흐림)
      const busy = busyNow();
      const pk = [st.plan_v, busy].join("|");
      const moved = !busy && pk !== seenPlanKey;
      seenPlanKey = pk;
      // 이 Studio 의 무료 큐가 끝난 뒤 받은 state 도 — 끝 직후 폴링이 하나 더 오면(onDone 의 refresh) 디바운스가 그 선택으로 모은다
      if (doneReplan || (on && onlyNow() !== was) || (moved && ((on && planStale()) || (!PS.plan && !!PS.planErr)))) schedulePlan();
      doneReplan = false;
      if (on) {
        // 채택 패널: 매니페스트 rev, 또는 스펙만 바뀐 쓰기(그래프 Spec Draft·다른 창의 채택 — rev 그대로, plan_v 만 바뀜)면 GET /draft 다시.
        // [수정] 입력 중에는 미루되 앞 읽기가 실패했으면 다시
        if (cur && (text === null || dvErr) && (st.rev !== seenRev || moved)) loadDraft();
        ticks += 1;
        if (busy || ticks % PROGRESS_EVERY === 0) PS.loadProgress().then((ch) => ch && render());
        if (setupPending && !setupInputWait && !asking && Date.now() - setupLastTry >= 10000) firstSetup();
      }
      if (changed) render();
    }

    function enter(st, cropId) {
      // 진행 모드 진입: 채택 패널 = 보드가 고른 크롭, 계획(지금 선택)·진행 수를 받는다
      if (setupPending === null) setupPending = !settingsOn;
      setupInputWait = false;
      on = true;
      ticks = 0;
      S = st;
      if (cropId && cropId !== cur) {
        cur = cropId;
        dv = null;
        dvErr = null;
        closeEdit();
      }
      loadDraft();
      stateChanged(st);
      render();
      PS.loadProgress().then(render);
      planNow();
      clearTimeout(planTimer); // stateChanged 가 예약한 계획 요청 — 바로 위에서 지금 선택으로 받음
      firstSetup();
    }

    function leave() {
      on = false;
      settingsOn = false;
      clearTimeout(planTimer);
      closeEdit();
      list.replaceChildren();
      panel.replaceChildren();
    }

    // 배치 7 공용 자리(감독관 — _proto/STUDIO_BATCH7_SPEC.md D70): 본문은 7-B 가 채운다. exportNow·exportWhy·openFolder = 상단 [Export](D58),
    // steps = 단계 줄 상태(D55), canLeave = [프로젝트] 이동 가드(D48 — 7-A 의 board.js goProjects 가 부름)
    function exportNow() { return false; }
    function exportWhy() { return ""; }
    function openFolder() { return false; }
    function steps() { return null; }
    function canLeave() { return { ok: true }; }

    // tabChanged = link 상태가 바뀜(board.js 가 부름 — 승인 단추 켜짐·제목), reroll = board.js queueReroll 의 유료 가능 탭 갈래, paidWhy·paidLock·side =
    // board.js tabWay·R 확인 문구(유료 탭 길을 쓰나·지금은 예약만인가·그 길의 부수 효과 줄), setMaskLeft = 적용 안 한 마스크 편집(★ 점검 줄)
    return { stateChanged, enter, leave, select, queue: boardQueue, applyWarn, recalc: planNow, openSettings, reroll, tabChanged: render, paidWhy, paidLock,
      side: SIDE, setMaskLeft, exportNow, exportWhy, openFolder, steps, canLeave };
  }

  return { create, session, link, checkPrompt, freeBody, endOf, adoptState, runText, draftText, approveText, paidReport, starChecks, layoutLines };
})();
