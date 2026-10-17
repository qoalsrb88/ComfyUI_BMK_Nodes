// BMK Design Patch Studio — S3a 파이프라인 조작(STUDIO_S3_SPEC §3·§5). board.js 가 진행 모드(G)에서 켜고 끈다(머리줄 한 줄은 모든 모드).
// 무료 조작만: 실행 설정(pipeline.json)·무료 템플릿 큐(반영 = apply, Export — 서버가 만든 템플릿을 ComfyUI /api/prompt 로 직접 넣음, 탭 불필요)·
// 계획/비용 미리보기(Run·초안 해시까지)·진행·★ 점검·Export 결과·제안 채택/수정. 유료 승인은 S3b — 승인 단추는 꺼 두고 해시 복사만 준다.
// /api/prompt 본문을 만드는 곳은 freeBody 하나(서버가 준 템플릿 prompt + front — 인증·탭 정보 없음)이고, 보내기 전에 checkPrompt 로 본다.
// 왼쪽 = 크롭 목록 + 선택 체크(only), 가운데 = 뷰어(그대로), 오른쪽 = 진행 패널(위: 설정·반영·초안·Run·★·Export, 아래: 선택 크롭의 채택).
// 응답 모양은 tools/fixtures/pipeline_api.json(정본). ComfyUI 탭 연결 표시(hello — D6)는 link. 모든 문자열은 textContent 로만 넣는다.
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
  const CHANNEL = "bmk-dp";
  const HELLO = "bmk-dp-hello";
  const JOB_KEY = "bmk-dp-job:"; // sessionStorage(+ 보드 키) — 다시 연 창이 이 Studio 가 낸 프롬프트를 이어서 폴링
  // apply = 짧고 사용자가 기다리는 중(대기열 앞), export = 수십 초(사용자 생성 작업을 앞지르지 않게 뒤)
  const STAGES = { apply: { front: true, label: "반영" }, export: { front: false, label: "Export" } };
  const PAID = ["BMKDesignPatchRun", "BMKDesignPatchSpecDraft"];
  const S3B = "S3b 전 — 그래프의 승인 버튼 사용";
  const END = { done: "끝", cancelled: "취소됨", error: "오류", gone: "사라짐" };
  const SECTIONS = { import: "Import PSD", cand: "Candidate In", prepare: "Prepare", draft: "Spec Draft", run: "Run", analyze: "Analyze",
    compose: "Compose", export: "Export" };
  const SECTION_WARN = {
    import: "다음 반영에서 크롭 소스·타깃·레퍼런스가 바뀌면 새 셀(유료 대상)",
    prepare: "다음 반영에서 jobs 가 바뀌어 새 셀이 될 수 있음(유료 대상)",
    draft: "초안 키가 바뀜 — fill 은 갱신 가능 표시만, refresh 면 전 크롭 재초안(유료)",
  };
  const DRIFT = {
    cell: "그래프와 Studio 를 번갈아 돌리면 셀이 바뀌어 다시 과금 대상",
    calls: "셀은 그대로지만 완료된 셀마다 새 호출이 생겨 다시 과금 대상(Run 승인 필요)",
    cost: "셀은 그대로, 대기 호출의 장 수·비용·승인 해시가 바뀜",
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
  const DRAFT_NOTE = "원장에만 남은 초안(받았지만 스펙에 못 쓴 것)은 이 반영이 병합하지 않음 — 진행(G) 초안 줄의 '못 쓴 스펙'으로 보이고, "
    + "그래프 Spec Draft 를 승인 없이 한 번 큐하면 병합";
  const STYLE_UNIT = "_style"; // 초안 계획의 화풍 단위 crop_id(vlm.STYLE_ID — "_project" 는 화풍 스펙 파일 이름일 뿐)
  // 유도값 출처(GET /pipeline derived.from) → [매니페스트 값으로 다시] 확인 창 문구
  const FROM = { manifest: "매니페스트 기록", jobs: "jobs 에서 추정", spec_draft: "스펙 초안 출처에서 추정", default: "기록 없음 — 노드 기본값" };

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
    // §3.3 Run 줄 → {line, rows, dim}. 승인 필요 = 선택(only) 계획(C13 — 전체 수치와 가르게 선택 크롭 수를 붙임), 재굴림 = 사전 승인
    // (Run 실행 때 선택과 무관하게 함께 과금). running = 실행 중(계획을 받은 뒤 그래프가 Run 을 시작하면 패널이 state running 을 넘김)
    if (run.error) return { line: `Run: 계산하지 못함 — ${run.error}`, rows: [], dim: true };
    const picked = Object.values(run.by_crop || {}).filter((x) => x.selected).length;
    const parts = [`Run: 승인 필요(선택 ${picked}크롭) ${run.need}건` + (run.need ? ` ≈ ${usd(run.est_usd)}(≈ ${span(run.credits)} 크레딧)` : "")];
    if (run.pre) parts.push(`재굴림(사전 승인) ${run.pre}건 ≈ ${usd(run.est_usd_pre)} 도 함께 과금(선택과 무관)`);
    parts.push(`복구 ${run.free}`);
    if (run.filtered) parts.push(`선택 밖 ${run.filtered}건`);
    parts.push(`해시 ${run.hash || "없음"}`);
    const line = parts.join(" · ") + (run.backend === "mock" ? "  [MOCK]" : "");
    return { line: running ? `Run 실행 중 — 끝난 뒤 다시 계산 (${line})` : line, rows: run.reasons.map((x) => `${x.reason} × ${x.n}`),
      dim: !!running };
  }

  function tally(ids, unit = "") {
    // 초안 계획의 crop_id 목록 → "n{unit}" · "n{unit} + 화풍" · "화풍" · "0"(화풍 단위는 크롭으로 세지 않는다 — §3.3·§4.4)
    const crops = ids.filter((id) => id !== STYLE_UNIT).length;
    const style = ids.length > crops;
    return crops ? `${crops}${unit}${style ? " + 화풍" : ""}` : style ? "화풍" : "0";
  }

  function draftText(d, running = d.running) {
    // §3.3 초안 줄 → {line, rows, dim, what}: 승인 필요 크롭 + 화풍(STYLE_UNIT 단위), 크레딧(openai)·분(local_qwen), 갱신 가능, 해시.
    // running = 실행 중(계획을 받은 뒤 그래프·다른 경로의 Spec Draft 가 시작하면 패널이 state drafting 을 넘김 — runText 와 같은 뜻)
    if (d.error) return { line: `초안: 계산하지 못함 — ${d.error}`, rows: [], dim: true, what: "" };
    if (running) return { line: "초안: Spec Draft 실행 중 — 끝난 뒤 다시 계산", rows: [], dim: true, what: "" };
    const min = Math.max(1, Math.round(d.est_seconds / 60));
    const what = tally(d.units.map((u) => u.crop_id), "크롭");
    let head = `초안: 승인 필요 ${what}`;
    if (d.units.length) {
      head += d.family === "local_qwen" ? ` · 로컬 Qwen 약 ${min}분(ComfyUI 모델을 VRAM 에서 내림 — 과금 없음)`
        : ` ≈ ${num(d.est_credits[0])}(최대 ${num(d.est_credits[1])}) 크레딧 · 약 ${min}분`;
    }
    const parts = [head];
    if (d.refresh_available.length) parts.push(`갱신 가능 ${tally(d.refresh_available.map((x) => x.crop_id))}`);
    if (d.filtered.length) parts.push(`선택 밖 ${tally(d.filtered)}`);
    parts.push(`해시 ${d.hash || "없음"}`);
    const name = (id) => (id === STYLE_UNIT ? "화풍" : id);
    const rows = d.units.map((u) => `${name(u.crop_id)}: ${u.reasons.join(", ") || u.state}`)
      .concat(d.skipped.map((x) => `${name(x.crop_id)}: 건너뜀 — ${x.reason}`));
    const w = d.warnings;
    if (w.unwritten.length) rows.push(`못 쓴 스펙 ${w.unwritten.length}(${w.unwritten.join(", ")}) — 그래프 Spec Draft 를 승인 없이 한 번 큐하면 병합`);
    if (w.mock_specs) rows.push(`mock 초안이 남은 스펙 ${w.mock_specs} — 실제 백엔드 Spec Draft 가 양보시킴`);
    return { line: parts.join(" · ") + (d.backend === "vlm_mock" ? "  [MOCK]" : ""), rows, dim: false, what };
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

  function starChecks(S, seams, pending) {
    // §3.6 Export 전 점검(정보·경고 — 막지 않음) → [{text, key}] key = 누르면 갈 타깃(없으면 null).
    // pending = 반영 필요 사유(export 템플릿에는 Import 가 없어 미반영은 반영 뒤 Export) — 크롭 줄로 나온 레이아웃 미반영은 다시 적지 않음
    const out = [];
    let lay = false;
    for (const t of S.targets) {
      const name = `${t.crop_name || t.crop_id} · ${t.tid}`;
      if (!t.pick_keys.length) out.push({ text: `${name} — ★ 없음`, key: t.key });
      else if (t.candidates.some((c) => c.picked && c.analysis === "none")) out.push({ text: `${name} — 분석 없는 ★(반영 필요 — Analyze)`, key: t.key });
    }
    for (const c of S.crops) {
      const t0 = S.targets.find((t) => t.crop_id === c.id);
      const key = t0 ? t0.key : null;
      const name = c.name || c.id;
      for (const d of c.dup_picks) out.push({ text: `${name} · ${d.tid} — 중복 ★(같은 이미지는 ${d.same_as} 에만)`, key: `${c.id}/${d.tid}` });
      if (c.stale_picked.length) out.push({ text: `${name} — 옛 크롭 ★ ${c.stale_picked.length}`, key });
      const sm = seams && seams.get(c.id);
      if (sm && sm.bad) out.push({ text: `${name} — 이음매 bad ${sm.bad}`, key });
      if (LAYOUT_PENDING.includes(c.layout_status)) {
        lay = true;
        out.push({ text: `${name} — ${LAYOUT_REASON}(반영 뒤 Export)`, key });
      }
    }
    for (const r of pending) if (!(lay && r.startsWith(LAYOUT_REASON))) out.push({ text: `반영 필요: ${r} — 반영 뒤 Export`, key: null });
    return out;
  }

  // ── 서버 세션: 설정(P)·계획·Studio 가 낸 무료 프롬프트(job)·채택. DOM 을 쓰지 않는다(J-P1·J-P2 가 node 에서 가짜 fetch 로 돈다) ──
  function session(apiUrl, comfyUrl, root, fetchFn, store, pollMs = POLL_MS) {
    // P = 마지막 GET /pipeline, plan = 받아들인 마지막 계획(planOnly·planStyle = 그 계획을 요청한 정규 only·화풍 — null·false = only 없음),
    // job = 이 Studio 가 낸 무료 프롬프트 {stage, pid, number, running, ahead, gone}, last = stage 마다 마지막 끝 {state, msg, at, export},
    // busy = 설정·채택 쓰기 중(하나씩)
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
          : r.status === 404 ? "파이프라인 라우트가 없습니다 — ComfyUI 를 재시작해야 진행(G)을 쓸 수 있습니다"
            : `응답을 읽을 수 없습니다 (HTTP ${r.status})`;
        throw Object.assign(new Error(msg), { status: r.status, data });
      }
      return data;
    }

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
      const waiting = pend.some((x) => x[1] === job.pid);
      job.ahead = job.running ? 0 : waiting ? run.length + pend.filter((x) => x[0] < job.number).length : null;
      job.gone = job.running || waiting ? 0 : job.gone + 1;
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

    function finish(job, res) {
      s.job = null;
      remember(null);
      s.last[job.stage] = Object.assign({ at: new Date().toLocaleTimeString("ko-KR", { hour12: false }) }, res);
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
          finish(job, endOf(h));
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
      // expect = 사용자가 확인 창에서 본 pipeline rev(반영) — 템플릿 sig('<pipeline rev>:<매니페스트 rev>')의 rev 가 다르면(확인 뒤 다른 창이 저장)
      // 보내지 않고 예외(rev null·0 = 깨짐·파일 없음은 서버가 409)
      if (!STAGES[stage]) throw new Error(`무료 stage 가 아닙니다: ${stage}`);
      if (s.job) throw new Error(`이 Studio 의 ${STAGES[s.job.stage].label}(${s.job.stage}) 실행이 끝난 뒤 다시 하세요`);
      if (s.sending) throw new Error("큐에 넣는 중입니다");
      s.sending = true;
      try {
        let t;
        try {
          t = await call("pipeline/prompt?" + qs({ stage }));
        } catch (e) {
          if (e.status === 404 && !e.data) return { old: true };
          throw e;
        }
        const bad = checkPrompt(t.prompt);
        if (bad) throw new Error(`큐에 넣지 않음 — ${bad}`);
        const got = String(t.sig).split(":")[0];
        if (expect && got !== String(expect)) {
          throw new Error(`큐에 넣지 않음 — 확인한 뒤 실행 설정이 바뀜(pipeline rev ${expect} → ${got}, 다른 창의 저장): 다시 [반영]해 새 설정으로 확인하세요`);
        }
        const r = await comfy("prompt", freeBody(t.prompt, stage));
        const d = await readJson(r);
        if (!r.ok || !d || !d.prompt_id) throw new Error(comfyRefusal(d, r.status));
        const job = { stage, pid: d.prompt_id, number: d.number, running: false, ahead: null, gone: 0 };
        s.job = job;
        remember(job);
        s.onChange();
        watch(job);
        return job;
      } finally {
        s.sending = false;
      }
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
        finish(job, endOf(h));
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

    return s;
  }

  // ── ComfyUI 탭 연결 표시(D6 — S3a 는 표시만, 유료 단추 활성은 S3b) ──
  function link(win, onChange, every = HELLO_MS, wait = REPLY_MS) {
    // every 마다 hello {type, id}: opener(Open Board 로 연 창)가 있으면 그 탭의 응답만, 없으면 BroadcastChannel("bmk-dp") 로 묻고 wait 동안 모은
    // 응답의 탭이 정확히 하나일 때만 연결. 같은 출처·이번 hello 의 id 만 받는다. state = wait(첫 판정 전)·ok·old(opener 가 답하지 않음 —
    // 옛 확장)·none·many. 응답 = {tab(탭 로드마다 새 ID), ext(확장 판 — 3 미만이면 유료 단추 끔), submit, busy}
    const st = { state: "wait", tab: null, ext: null, submit: null, busy: false, via: null };
    const Channel = win.BroadcastChannel;
    const ch = typeof Channel === "function" ? new Channel(CHANNEL) : null;
    let round = null;

    function take(d, via) {
      if (round && via === round.via && d && d.type === HELLO && d.id === round.id && typeof d.tab === "string") round.replies.set(d.tab, d);
    }

    win.addEventListener("message", (ev) => {
      if (ev.origin === win.location.origin && ev.source && ev.source === win.opener) take(ev.data, "opener");
    });
    if (ch) ch.onmessage = (ev) => take(ev.data, "channel");

    function settle(r) {
      const got = [...r.replies.values()];
      const d = got.length === 1 ? got[0] : null;
      const next = d ? { state: d.ext >= 2 ? "ok" : "old", tab: d.tab, ext: d.ext, submit: d.submit, busy: !!d.busy, via: r.via }
        : { state: r.via === "opener" ? "old" : got.length ? "many" : "none", tab: null, ext: null, submit: null, busy: false, via: r.via };
      const changed = Object.keys(next).some((k) => st[k] !== next[k]);
      Object.assign(st, next);
      if (changed) onChange(st);
    }

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
    // seams() → 크롭별 이음매, drain(), enter()(진행 모드로)
    const { list, panel, head } = hosts;
    let store = null;
    try {
      store = window.sessionStorage;
    } catch (e) {
      // 저장소 막힘 — 다시 연 창에서 이어 받지 못할 뿐
    }
    const PS = session(opts.apiUrl, opts.comfyUrl, opts.root, (u, o) => fetch(u, o), store);
    let on = false;
    let S = null; // 보드 state
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
    let form = {}; // 설정 패널에서 고치는 중인 값 {절: {키: 값}} — 다시 그려도 남는다
    // 다른 창의 저장 알아보기(state pipeline_rev 로 다시 읽으면 PS.P·rev 가 새 판이 되어 저장이 409 없이 통과): edits = 손댄 키를 처음 고칠 때 화면의 값
    // {절: {키: 값}}, drawn = 설정 패널을 마지막으로 그린(또는 저장한) 절 값 {절: 값}(입력 중이면 다시 그리기가 미뤄져 화면이 옛 값)
    let edits = {};
    let drawn = {};
    let composing = false;
    let pendingPanel = false; // 다시 그리기를 미룸(입력 중이면 패널만, 포인터가 눌린 동안이면 전부) — focusout·pointerup 뒤에 그림
    let held = false; // 포인터가 눌린 동안
    let seenLoad = null; // GET /pipeline 을 맞춘 매니페스트 rev — 그래프 Prepare·Import 는 pipeline_rev 를 두고 매니페스트(prepare_settings·source)만 바꿈
    let warned = []; // 마지막 확인 창에 보인 갈라짐 줄(applyWarn)
    let asking = false; // 확인 창을 만드는 동작(once — 반영·매니페스트 값으로 다시)이 도는 중(연타 막음)
    let retried; // 받은 계획이 이미 낡아 한 번 더 부른 state plan_v(planNow — 같은 값에 되풀이하지 않음)
    let doneReplan = false; // 이 Studio 의 무료 큐가 끝남 — 다음 state(반영이 크롭을 더하고 뺀 선택)를 받은 뒤 계획을 다시(§3.1)

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
        fn();
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
      // 해시를 흐리게(반영 뒤 다시 계산 — 복사 끔): 반영 필요(apply_pending — prepare_inputs 불일치 포함)·반영 상태 모름(초안 계획 실행 중·오류)·
      // 설정 확인 남음·Prepare 갈라짐·계획 notes(서버의 설정 쪽 '반영 뒤 다시 계산' 사유)
      const P0 = PS.P;
      return pendingApply().length > 0 || !!pendingUnknown() || confirmOf(P0).length > 0 || (!!P0 && P0.drift.some((x) => x.section === "prepare"))
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

    async function reload() {
      // GET /pipeline 을 다시 읽음 → 지금 PS.P 가 이 읽기 뒤의 값인가(늦게 보낸 읽기가 먼저 받아진 것도 됨). 읽지 못하면 토스트·false — 옛 PS.P 로
      // 확인 창을 만들거나 '새 갈라짐 없음'으로 판정하지 않게(반영·매니페스트 값으로 다시)
      if ((await PS.load()) || !PS.err) return true;
      opts.toast(`실행 설정(GET /pipeline)을 다시 읽지 못해 멈춤 — ${PS.err}`, "err");
      return false;
    }

    async function checkWarn() {
      // 확인 창을 띄운 뒤(편집기 확인 창은 이 창이 마지막으로 읽은 GET /pipeline 으로 만든다) 그래프 Prepare·Import 로 갈라짐이 새로 생겼으면
      // 큐에 넣기 전에 묻는다 — 서버 큐 게이트는 갈라짐을 보지 않는다. 묻는 동안 또 바뀔 수 있어 새 줄이 없을 때까지 다시 읽고 묻는다.
      // → 사용자가 마지막으로 확인한 pipeline rev(큐의 expect — 그 뒤 다른 창이 저장하면 큐가 거절) | null(거절·다시 읽지 못함)
      for (;;) {
        const shown = warned;
        if (!(await reload())) return null;
        const rev = PS.P.rev;
        const extra = applyWarn().filter((x) => !shown.includes(x));
        if (!extra.length) return { rev };
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

    function jobText(job) {
      return `${STAGES[job.stage].label}(${job.stage}) ${job.running ? "실행 중" : job.ahead === null ? "보냄" : `대기 — 앞 ${job.ahead}`}`;
    }

    // ── 동작 ──
    async function queue(stage, expect) {
      // 무료 큐 → {old: true}(옛 서버 — 알림·대신 반영은 호출자: board.js applyFree 는 탭 반영, 패널 단추는 queueHere) | undefined.
      // 결과·오류는 토스트, 설정 확인이 필요하면 설정 패널. expect = 확인한 pipeline rev(PS.queue). 앞 큐 요청이 아직 가는 중이면(두 번 누름)
      // 조용히 버린다 — 앞 요청의 결과 토스트가 나온다
      if (PS.sending) return undefined;
      try {
        const r = await PS.queue(stage, expect);
        if (r.old) return r;
        opts.toast(`${STAGES[stage].label}: ComfyUI 대기열 ${STAGES[stage].front ? "앞" : "뒤"}에 넣음(무료 템플릿 — Run·Spec Draft 없음)`);
      } catch (e) {
        opts.toast(e.message, "err");
        const reason = e.data && e.data.reason;
        if (reason === "confirm_settings" || reason === "pipeline_broken") openSettings();
      }
      render();
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
      return ok ? queue(stage, ok.rev) : undefined;
    }

    function once(fn) {
      // 확인 창을 만드는 동작(GET /pipeline 다시 읽기 → 확인 → 큐·저장)은 한 번에 하나 — 다시 읽는 사이의 연타가 확인 창을 둘 띄우지 않게
      return async () => {
        if (asking) return;
        asking = true;
        try {
          await fn();
        } finally {
          asking = false;
        }
      };
    }

    async function applyNow() {
      if (!(await reload())) return; // 확인 창을 만들기 전에(state 폴링이 그래프 Prepare 를 알기 전이어도 지금 갈라짐으로)
      const n = S ? S.pending_preapproved || 0 : 0;
      const lines = applyWarn().concat("무료 반영(Import → Prepare → Analyze → Review 템플릿, 탭 불필요)을 ComfyUI 대기열 앞에 넣습니다.");
      if (n) lines.push(`재굴림 대기 ${n}건(사전 승인)은 이 반영에서 돌지 않음 — R·ComfyUI 큐·그래프 Run 에서 과금`);
      if (!window.confirm(lines.concat(DRAFT_NOTE, "계속할까요?").join("\n"))) return;
      const ok = await checkWarn();
      if (ok) await queueHere("apply", ok.rev);
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

    function openSettings() {
      settingsOn = true;
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
      if (e.prompt_changed) out.push("다음 반영에서 이 크롭 새 셀 — Run 승인 대상(유료)");
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
      const lines = [`매니페스트 값으로 다시: ${secs.map((x) => SECTIONS[x]).join(", ")} 절을 유도값으로 저장합니다.`];
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
      if (!window.confirm(lines.concat("Prepare·Import 가 바뀌면 다음 반영에서 셀이 바뀌어 다시 과금 대상이 될 수 있습니다. 계속할까요?").join("\n"))) return;
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
            + (saved.length ? ` — 앞 절(${saved.join(", ")})은 저장됨, 남은 절은 [매니페스트 값으로 다시]로 다시 확인` : ""), "err");
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
      const st = el("span", pend.length || unk ? "lh-wait" : "lh-ok", pend.length ? `반영 필요 ${pend.length}` : unk ? "반영 상태 확인 못 함" : "반영됨");
      st.title = pend.concat(unk ? [`레퍼런스·채택·Import 설정 미반영 확인 못 함 — ${unk}`] : []).join("\n")
        || "레이아웃·레퍼런스·채택·Import 설정 미반영·Prepare 입력 바뀜 없음";
      head.append(st);
      if (confirmOf(PS.P).length) {
        // 빨강 = 반영·Export 를 막음(prepare.*·import.*·파일 없음·깨진 파일), 노랑 = 막지 않음(해시만 흐림 — 설정 칸 줄과 같은 판정)
        const cg = confirmGate(PS.P);
        const c = el("span", cg.gate.length || cg.noFile ? "lh-bad" : "lh-wait", "설정 확인 필요");
        c.title = confirmOf(PS.P).join("\n");
        head.append(c);
      }
      // Prepare 갈라짐(마지막 Prepare ≠ Studio 설정) — 검토 모드의 R·ComfyUI 큐는 그래프 Prepare 값으로 과금하므로 모든 모드에서 보임
      const pdr = PS.P && PS.P.drift ? PS.P.drift.filter((x) => x.section === "prepare") : [];
      if (pdr.length) {
        const c = el("span", pdr.some((x) => DRIFT_BILL.includes(x.kind)) ? "lh-bad" : "lh-wait", "Prepare 갈라짐");
        c.title = pdr.map(driftText).concat("R·ComfyUI 큐·그래프 Run 은 그래프 Prepare(그래프 위젯 값)로 jobs 를 다시 쓴 뒤 과금").join("\n");
        head.append(c);
      }
      if (p && p.run.by_crop) {
        // only 없는 계획 by_crop 의 합 — 선택과 무관한 전체(C13)
        const bc = Object.values(p.run.by_crop);
        const need = bc.reduce((a, x) => a + x.need, 0);
        const lo = bc.reduce((a, x) => a + x.est_usd[0], 0);
        head.append(el("span", "", need ? `Run 승인 필요(전체) ${need}건 $${lo.toFixed(1)}` : "Run 승인 필요(전체) 0"));
      }
      if (p && p.draft.by_crop) {
        const n = Object.values(p.draft.by_crop).filter((x) => x.wait.length).length;
        if (n) head.append(el("span", "", `초안 ${n}`));
      }
      const picked = S.targets.filter((t) => t.pick_keys.length).length;
      head.append(el("span", "", `★ ${picked}/${S.targets.length}`));
      const ex = PS.P && PS.P.exports && PS.P.exports[0];
      if (ex) head.append(el("span", "", `Export r${ex.rev}`));
      if (PS.job) {
        head.append(el("span", "lh-wait", `실행 중: ${jobText(PS.job)}`));
        if (PS.job.ahead !== null && !PS.job.running) head.append(button("취소", "대기 중인 이 Studio 의 프롬프트를 ComfyUI 대기열에서 지움", cancelJob));
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
        button("승인 필요만", "Run 승인 필요·초안 대기가 있는 크롭", () => setSel(needIds())));
      list.append(bar);
      for (const c of S.crops) {
        const li = el("li", c.id === cur ? "sel" : "");
        const top = el("div", "pl-top");
        const cb = el("input");
        cb.type = "checkbox";
        cb.checked = sel.has(c.id);
        cb.dataset.keys = "board";
        cb.title = "선택(only) — 계획·해시 복사의 대상";
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
        if (LAYOUT_PENDING.includes(c.layout_status)) ch.append(chip(LAYOUT_REASON, "b-wait"));
        if (r && r.need) ch.append(chip(`승인 필요 ${r.need}`, "b-wait"));
        if (r && r.pre) ch.append(chip(`재굴림 ${r.pre}`, "b-wait"));
        if (r && r.inflight) ch.append(chip(`진행 ${r.inflight}`, "b-run"));
        if (r && (r.failed || r.orphaned)) ch.append(chip(`실패 ${r.failed} · orphan ${r.orphaned}`, "b-bad"));
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

      // 설정
      let s = section("설정");
      if (!P0) line(s, PS.err ? `읽기 오류: ${PS.err}` : "불러오는 중…", PS.err ? "bad" : "dim");
      else {
        if (P0.error) line(s, `pipeline.json 오류: ${P0.error} — [설정]에서 다시 저장(덮어쓰기)`, "bad");
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
        if (cg.rest.length) line(s, `확인 필요: ${cg.rest.join(", ")} — 반영·Export 는 막지 않음(확인(저장) 전에는 계획 해시를 흐림)`, "warn");
        for (const d of P0.drift) line(s, driftText(d), DRIFT_BILL.includes(d.kind) ? "bad" : "warn");
        // 경로 경고는 모두 템플릿을 막는다(서버 prompt_gate 409): import.* = 반영·Export, cand.path = 반영만(export 템플릿에는 dp_cand 가 없음)
        for (const w of P0.warnings) {
          line(s, w.field.startsWith("cand.") ? `${w.msg} — 반영을 막음(Export 는 됨): [설정]에서 경로를 고치거나 후보 등록을 끔` : w.msg, "bad");
        }
      }
      btns(s, button("설정", "실행 설정(pipeline.json) — 노드 위젯 그대로, 절마다 저장", openSettings));

      // 반영
      s = section("반영");
      if (pend.length) line(s, pend.join(" · "), "warn");
      if (unsure) line(s, `레퍼런스·채택·Import 설정 미반영을 확인하지 못함(${unsure}) — 계획을 다시 계산`, "warn");
      else if (!pend.length) line(s, "반영됨 — 레이아웃·레퍼런스·채택·Import 설정 미반영·Prepare 입력 바뀜 없음", "ok");
      const la = PS.last.apply;
      if (la) line(s, `이 Studio 의 마지막 반영: ${la.at} ${END[la.state]}${la.msg ? ` — ${la.msg}` : ""}`, la.state === "done" ? "dim" : "bad");
      if (PS.job && PS.job.stage === "apply") line(s, jobText(PS.job), "warn");
      const ab = button("반영", "무료 반영 — Import → Prepare → Analyze → Review 템플릿을 ComfyUI 에 직접(탭 불필요)", once(applyNow), "primary");
      ab.disabled = !!PS.job;
      btns(s, ab);

      // 초안·Run(계획) — 흐림(planDim — 반영 뒤 다시 계산)·낡음(stale — 다시 받을 때까지)이면 해시 복사 끔(copyHashes)
      const dim = planDim();
      const hc = copyHashes();
      s = section("계획");
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
        const why = pend.concat(unk ? [`반영 상태 확인 못 함 — ${unk}`] : []);
        if (stale) line(s, "계획이 지금 파일 상태와 다름(다른 창·그래프의 쓰기·실행) — 다시 받기 전에는 해시 복사 끔", "warn");
        if (dim) line(s, `반영 뒤 다시 계산 — 지금 해시는 반영 뒤 달라질 수 있음${why.length ? `(${why.join(", ")})` : ""}`, "warn");
        const dt = draftText(p.draft, p.draft.running || !!(S && S.drafting)); // 실행 중에는 계획을 다시 받지 않으므로 state 로 흐림(Run 줄과 같이)
        line(s, dt.line, dt.dim || dim ? "dim" : "");
        for (const r of dt.rows) line(s, `· ${r}`, "dim");
        if ((p.draft.import_pending || []).length) line(s, `초안 해시는 반영 뒤 확정: ${p.draft.import_pending.join(", ")}`, "warn");
        btns(s, disabledApprove(`초안 승인 ${dt.what}`), hashCopy("draft", hc.draft));
        const rt = runText(p.run, p.run.running || !!(S && S.running));
        line(s, rt.line, rt.dim || dim ? "dim" : "");
        for (const r of rt.rows) line(s, `· ${r}`, "dim");
        btns(s, disabledApprove(`Run 승인 ${p.run.error ? "" : `${p.run.need}건${p.run.need ? ` ≈ ${usd(p.run.est_usd)}` : ""}`}`), hashCopy("run", hc.run));
        // Run 해시는 retry_failed·retry_orphans 로 승인 대상이 바뀜(그래프 Run 위젯 값). 그래프 Import PSD 는 같은 큐에서 Run·Draft 보다 먼저 돌아
        // 위젯 값으로 매니페스트 source 를 다시 씀 — Studio import 절과 다르면 초안 키·jobs 가 바뀌어 해시가 맞지 않음(Studio 는 그래프 위젯을 모름)
        const rc = P0 && P0.settings ? P0.settings.run : null;
        line(s, "해시는 그래프 Import PSD 위젯(같은 큐에서 먼저 돎)·Run·Draft 노드의 only·Prepare·초안 설정과 Run 노드의 retry_failed·retry_orphans"
          + `${rc ? `(Studio ${rc.retry_failed}·${rc.retry_orphans})` : ""} 가 Studio 와 같을 때만 같습니다 — 다르면 과금 없이 드라이런`, "dim");
      }
      if (p && p.run.by_crop) s.append(cropTable(p.run.by_crop));
      if (p && p.notes.length) for (const n of p.notes) line(s, n, "dim");
      const pg = PS.progress;
      if (pg) {
        const r = pg.run;
        line(s, `진행 — Run ${pg.running ? "실행 중" : "멈춤"}${pg.drain ? " · Drain 요청됨" : ""} · 완료 ${r.done} · 진행 ${r.inflight} · 실패 ${r.failed}`
          + ` · orphan ${r.orphaned} · 대기 승인분(전체) ${r.need} · 재굴림 ${r.pre}`, pg.running ? "warn" : "dim");
        if (pg.draft) {
          const d = pg.draft;
          line(s, `초안 진행 — 진행 ${d.inflight} · 받음 ${d.created} · 완료 ${d.done} · 실패 ${d.failed}`, "warn");
        }
      }
      const db = button("Drain", "진행 중인 Run 이 새 호출 발사를 멈춥니다(진행 중인 호출은 끝까지 받아 저장)", opts.drain);
      db.disabled = !(S && S.running);
      btns(s, db, recalcButton("지금 선택으로 계획을 다시 받습니다"));

      // ★ 점검
      s = section("★ 점검");
      const checks = starChecks(S, opts.seams(), pend);
      if (unsure) checks.push({ text: `반영 상태를 확인하지 못함(${unsure}) — 계획을 다시 계산한 뒤 Export`, key: null });
      if (!checks.length) line(s, "점검 통과 — 모든 타깃에 ★, 미반영 없음", "ok");
      for (const c of checks) {
        const r = line(s, c.text, "warn" + (c.key ? " link" : ""));
        if (c.key) r.addEventListener("click", () => opts.goTarget(c.key));
      }

      // Export
      s = section("Export");
      exportRows(s, P0);
      const eb = button("Export", "무료 Export 템플릿(Analyze → Compose → Export — Import 없음, ComfyUI 대기열 뒤)", () => queueHere("export"), "primary");
      eb.disabled = !!PS.job;
      btns(s, eb);

      renderAdopt();
    }

    function disabledApprove(label) {
      const b = button(label.trim(), S3B, () => {});
      b.disabled = true;
      return b;
    }

    function hashCopy(kind, [hash, only]) {
      // 해시 복사(§3.3): 그래프 Run·Draft 노드의 approve·only 칸에 넣을 값 한 줄. 누를 때 지금 판정(copyHashes)과 다르면(입력 중이라 미뤄 둔 패널의
      // 옛 단추 — 그 사이 계획이 낡거나 바뀜·Run 시작) 복사하지 않고 다시 그림
      const b = button("해시 복사", hash ? `approve: ${hash}  only: ${only}` : "복사할 해시 없음(승인할 것 없음·반영 필요·실행 중)", () => {
        if (!same(copyHashes()[kind], [hash, only])) {
          opts.toast("계획이 바뀜(다른 창·그래프의 쓰기·실행) — 해시를 복사하지 않음: 다시 그린 계획 칸을 보고 다시 누르세요", "warn");
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
      for (const h of ["크롭", "승인 필요", "재굴림", "복구", "완료", "실패", "orphan", "진행"]) hr.append(el("th", "", h));
      t.append(hr);
      const ids = S.crops.map((c) => c.id).filter((id) => bc[id]).concat("_stale" in bc ? ["_stale"] : []);
      for (const id of ids) {
        const v = bc[id];
        const c = S.crops.find((x) => x.id === id);
        const tr = el("tr", v.selected ? "sel" : "");
        tr.append(el("td", "", id === "_stale" ? "옛 셀" : (c && c.name) || id));
        for (const k of ["need", "pre", "free", "done", "failed", "orphaned", "inflight"]) tr.append(el("td", "", v[k] || ""));
        t.append(tr);
      }
      return t;
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
          line(s, `새 PSD r${o.rev}: ${o.psd}` + (e ? ` · 레이어 ${e.layers ?? "?"} · 문제 ${e.problems ?? "?"} · ${mb(e.bytes)}` : ""), "ok");
          btns(s, button("경로 복사", o.psd, () => copy(o.psd)));
        } else if (o.outcome === "unchanged") {
          line(s, `변경 없음 — 기존 파일 r${o.rev}: ${o.psd}`, "dim");
        } else {
          line(s, "PSD 를 쓰지 않음 — 넣을 후보 없음(★ 없음)", "warn");
        }
      }
      for (const x of (P0 && P0.exports) || []) {
        const r = line(s, `r${x.rev} · ${x.time} · 문제 ${x.problems ?? "?"} · ${mb(x.bytes)}`, "dim link");
        r.title = `${x.psd} — 눌러 경로 복사`;
        r.addEventListener("click", () => copy(x.psd));
      }
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
      top.append(el("span", "lp-title", "실행 설정 — pipeline.json"), button("← 진행", "설정 닫기", () => {
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
      if (!P0.saved) line(panel, "파일 없음 — 지금 값은 매니페스트에서 유도한 값입니다(첫 저장이 파일을 만듦)", "warn");
      btns(panel, button("매니페스트 값으로 다시", "지금 유도값(마지막 Import·Prepare·Export·초안 출처 — 다시 읽음)을 절마다 저장 — 확인 필요 항목도 확인됨으로",
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
      const r = el("div", "lp-row pp-field" + (confirm ? " bad" : moved ? " warn" : ""));
      const from = PS.P.derived.from[`${sec}.${k}`];
      const lab = el("span", "k", k + (confirm ? " (확인 필요)" : ""));
      if (from) lab.title = `처음 값 출처: ${from}`;
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

    PS.onChange = render;
    PS.onWrite = () => opts.refresh();
    PS.onDone = (job, res) => {
      const label = STAGES[job.stage].label;
      const o = res.export;
      if (res.state !== "done") opts.toast(`${label}: ${END[res.state]}${res.msg ? ` — ${res.msg}` : ""}`, res.state === "cancelled" ? "warn" : "err");
      else if (job.stage !== "export") opts.toast(`${label} 끝 — 새 상태·계획을 다시 받습니다`);
      else if (!o) opts.toast("Export 끝 — 결과 판정 없음(노드가 옛 버전 — ComfyUI 재시작)", "warn");
      else opts.toast(o.outcome === "new" ? `Export: 새 PSD r${o.rev} — ${o.psd}` : o.outcome === "unchanged" ? `Export: 변경 없음 — 기존 파일 ${o.psd}`
        : "Export: PSD 를 쓰지 않음 — 넣을 후보 없음(★ 없음)", o.outcome === "empty" ? "warn" : "");
      // 계획은 다시 받은 state 뒤에(§3.1): 반영이 지운 크롭이 아직 선택(only)에 남아 서버가 400('매니페스트에 없는 크롭')을 내지 않게
      doneReplan = true;
      opts.refresh();
      PS.load().then(render);
    };

    function stateChanged(st) {
      // 보드 state 를 받을 때마다(2초 폴링 — unchanged 응답이면 같은 S). 다시 그리기는 state·진행 수가 바뀐 때만(누르는 중인 단추를 갈지 않게)
      const k = [st.rev, st.running, st.drain, st.pipeline_rev, st.plan_v].join("|");
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
        seenLoad = st.rev;
        const loaded = PS.load();
        if (first) {
          loaded.then(render);
          PS.resume();
          planNow(); // Studio 를 연 뒤 한 번(진행 모드가 아니면 only 없음 — 검토 모드 칩용)
        } else {
          loaded.then(planNow); // 설정이 저장됨(다른 창 포함) — 새 설정(초안 apply·화풍 포함)으로 계획을 다시
        }
      } else if (st.rev !== seenLoad) {
        // 매니페스트가 바뀜(그래프 Prepare·Import 는 pipeline.json 을 두고 prepare_settings·source 만 바꿈) — 갈라짐·유도값·Export 목록을 다시
        // (확인 창 맨 앞 줄·[매니페스트 값으로 다시]·설정 칸이 이 값을 쓴다. 편집기 확인 창도 — 진행 모드가 아니어도)
        seenLoad = st.rev;
        PS.load().then(render);
      } else if (PS.err) {
        // 앞 GET /pipeline 이 실패함(잠김·재시작 중) — rev 가 그대로여도 폴링마다 다시(머리줄 '설정 확인 필요'·'Prepare 갈라짐'·설정 칸이
        // '읽기 오류'·옛 값에 멈추지 않게)
        PS.load().then((ok) => ok && render());
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
      }
      if (changed) render();
    }

    function enter(st, cropId) {
      // 진행 모드 진입: 채택 패널 = 보드가 고른 크롭, 계획(지금 선택)·진행 수를 받는다
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
    }

    function leave() {
      on = false;
      settingsOn = false;
      clearTimeout(planTimer);
      closeEdit();
      list.replaceChildren();
      panel.replaceChildren();
    }

    return { stateChanged, enter, leave, select, queue: boardQueue, applyWarn, recalc: planNow, openSettings };
  }

  return { create, session, link, checkPrompt, freeBody, endOf, adoptState, runText, draftText, starChecks };
})();
