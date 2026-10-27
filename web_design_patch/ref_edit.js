// BMK Design Patch Studio — S2 레퍼런스 편집기(STUDIO_S2_SPEC §4). board.js 가 레퍼런스 모드(T)에서 켜고 끈다.
// 가운데 = 시트 뷰어(DPViewer 두 번째 인스턴스, viewer.js setSheet·setEditor 훅) — 영역·SAM 점·다각형·컷 결과를 그린다. 왼쪽 = 크롭 목록,
// 오른쪽 = 선택 크롭의 레퍼런스 관리(위)와 컷·변환·저장(아래), 머리줄 = 판·미반영·반영(큐). 서버 = refs*(§5), 무료 범위만(D7 — 스펙을 쓰지 않음).
// 가운데 탭(다듬기 3-4): [시트] = 시트 목록 ↔ 시트 뷰어, [그리드] = 크롭마다 카드(크롭 소스 · 대표 · 보조) — 카드는 고르기만(편집은 오른쪽 패널).
// SAM3 컷은 라우트가 요청 파일만 쓰고 보드가 ComfyUI 공개 API 로 무료 노드 하나를 대기열 앞에 넣는다(D2·§3.1 — 큐 본문은 promptBody 하나).
// 이름·설명·변환 크기 규칙은 공용 표 tools/fixtures/refs_rules.json 과 같은 값(J-R1·J-R3). 모든 문자열은 textContent 로만 넣는다.
// 서버 URL 은 opts.apiUrl(보드 라우트)·opts.comfyUrl(ComfyUI /api) 상대 경로로만 연다.
"use strict";

window.DPRefEdit = (() => {
  const G = window.DPLayoutGeom;
  const DESC_MAX = 24;
  const TRIM_PAD = 8; // refs.TRIM_PAD — 저장 때 알파 bbox 둘레 여백
  const MIN_SIDE = 16; // 컷 선택의 짧은 변 하한(refs._check_cut)
  const MAX_POINTS = 32;
  const MAX_VERTS = 256;
  const SAM_SIDE = 1008; // SAM3 입력 한 변(코어 nodes_sam3)
  const SAM_PAD = 0.12; // refs.SAM_DEFAULTS.pad — SAM 창 = 선택 + 변마다 12%
  const MAX_SHOWN = 6; // vlm.MAX_REFS_SHOWN — Spec Draft 가 크롭당 보는 레퍼런스
  const AUX_CELLS = 4; // 그리드 카드의 보조 칸(2×2) — 넘치면 마지막 칸이 +k
  const CHECK_LINES = 3; // 그리드 카드의 검사 줄 — 넘치면 '…'(전체는 마우스 설명)
  const BIG_SIDE = 4096; // 이보다 크면 축소 권장(D4, R-big)
  const HELD_DIR = "_보류"; // refs.HELD_DIR — explicit 행의 보류 파일 = "_보류/<이름>"
  const HOLD_SUFFIX_RE = /__[0-9]{6}-[0-9]{6}(?:-[0-9]+)?(?=\.[^.]*$)/; // refs._HOLD_SUFFIX_RE(확장자 앞) — 옛 서버(보류 행 restore_as 없음)만: 되살릴 이름 = 이것을 뗀 것
  const POLL_MS = 1000;
  const GONE_POLLS = 3; // 대기열에서 사라졌는데 결과 파일이 없는 폴링 수 → 실패로 봄
  const FORGET_POLLS = 5; // 끝난 컷의 실행 기록을 지우기 전에 대기열에서 빠지기를 기다리는 폴링 수(넘으면 그냥 보냄)
  const CLICK_PX = 4;
  const HANDLE_PX = 6;
  const DBL_MS = 400; // 다각형: 이 안에 같은 자리를 다시 누르면 닫기(더블클릭)
  const PREVIEW_MAX = 768; // 변환 미리보기 캔버스 긴 변(화면용 — 저장 크기는 따로 계산)
  const STRIP_PX = 1 << 20; // 컷 미리보기 알파를 원크기로 읽을 때 띠 하나의 픽셀 수(메모리 묶음)
  const HANDLES = ["nw", "ne", "se", "sw", "n", "e", "s", "w"];
  const CURSORS = { n: "ns-resize", s: "ns-resize", e: "ew-resize", w: "ew-resize", ne: "nesw-resize", sw: "nesw-resize",
    nw: "nwse-resize", se: "nwse-resize" };
  // Python str.strip 의 공백(layout_geom.js 와 같은 집합 — 그 파일은 strip 을 내보내지 않음)
  const WS = "\\t\\n\\v\\f\\r\\x1c-\\x1f \\x85\\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000";
  const STRIP_RE = new RegExp(`^[${WS}]+|[${WS}]+$`, "g");
  // store._REF_T_RE = _t(?![A-Za-z]) IGNORECASE — Python 은 [A-Za-z] 를 대소문자 무시로 İ ı ſ K(켈빈) 에도 맞춘다
  const T_RE = /_t(?![A-Za-zİıſK])/i;
  const LN_RE = /^[\p{L}\p{N}]$/u;
  const GLOSS_RE = /[\s_\-·・.]+/gu; // store._GLOSS_STRIP_RE
  const SIDE_WORDS = new Map([["right", "right"], ["오른쪽", "right"], ["left", "left"], ["왼쪽", "left"]]);
  // refs._DESC_PROBLEMS 와 같은 문구 + 크롭 이름 쪽
  const NAME_ERRS = {
    role: "역할은 대표 또는 보조입니다",
    crop: "크롭 이름이 이름 규칙 밖이라 레퍼런스 이름을 만들 수 없습니다 — S1 에서 이름을 바꾸세요",
    len: `설명은 1–${DESC_MAX}자여야 합니다`,
    char: "설명에는 글자·숫자·- 만 쓸 수 있고 글자·숫자가 하나는 있어야 합니다(_ 공백 . 등 금지)",
    tmark: "설명이 t·t2·tㄱ 꼴로 시작하면 파일 이름에 '_t' 표지가 생겨 이름 매칭이 설명 앞을 부위로 읽지 못합니다(top·tassel 은 됨)",
    rematch: "크롭 이름에 '_t' 표지 꼴이 있어 이 파일이 이름 매칭에서 이 크롭의 부위로 읽히지 않습니다 — S1 에서 크롭 이름을 바꾸세요",
  };
  const METHODS = [
    ["rect", "사각형", "선택 안의 시트 알파 그대로 자르기(기본) — 떨어진 부품은 이것으로 끝, 붙은 부품은 다각형으로 다듬기"],
    ["sam3", "자동 오리기(SAM3)", "붙은 장식용: 창 = 선택 + 12% · 상자 + 자동 양점 — ComfyUI 대기열 앞에서 돎. 첫 컷은 SAM3 를 VRAM 에 올려 "
      + "ComfyUI 가 다른 모델을 내릴 수 있음(다음 생성에서 그 모델을 다시 올리는 시간)"],
    ["poly", "다각형", "클릭 = 꼭짓점 · 더블클릭·Enter = 닫기 · Shift = 앞 결과에 더하기 · Alt = 빼기"],
  ];
  const MATTES = [["white", "흰"], ["gray", "#808080"], ["check", "체크"]];
  const LINK_DONE = { fix: "고정(지금 이름 매칭 결과 그대로)", none: "레퍼런스 없이 고치기로 고정", unfix: "고정 풀기 — 이름 매칭으로",
    add: "보조로 연결", main: "대표로", move: "순서 바꿈", remove: "연결 끊음(파일은 그대로)", hold: "_보류 로 옮김", restore: "되살려 보조로 연결",
    relink: "다른 크롭에 다시 연결" };
  // 이름으로 가리키는 곳(explicit 행 where): 스펙·매니페스트 타깃(D5) · 다른 크롭의 고정 연결 · 다른 크롭의 이름 매칭
  const WHERE = { spec: "스펙", manifest: "프로젝트 기록", link: "연결", match: "이름 매칭" };
  // 반영(큐) 확인 창의 정보 줄(S3 §3.2 — 무료 반영은 Spec Draft 를 돌리지 않는다)
  const APPLY_NOTE = "원장에만 남은 초안(받았지만 스펙에 못 쓴 것)은 이 반영이 병합하지 않음 — 실행(G) 초안 줄의 '못 쓴 스펙'으로 보이고, "
    + "그래프 Spec Draft 를 승인 없이 한 번 큐하면 병합";

  // ── 이름·변환 규칙(공용 표 refs_rules.json — refs.check_desc·ref_file_name·transform_rgba 크기) ──
  function strip(s) {
    return s.replace(STRIP_RE, "");
  }

  function checkDesc(raw) {
    // → {desc} | {err: "len"|"char"|"tmark"}: NFC → strip → 1–24 코드 포인트 → L*·N*·'-'(‘-’ 만은 안 됨) → "_"+설명에 _t 표지 없음
    const d = strip(String(raw).normalize("NFC"));
    const cps = [...d];
    if (cps.length < 1 || cps.length > DESC_MAX) return { err: "len" };
    if (cps.every((c) => c === "-") || cps.some((c) => c !== "-" && !LN_RE.test(c))) return { err: "char" };
    if (T_RE.test("_" + d)) return { err: "tmark" };
    return { desc: d };
  }

  function glossNorm(s) {
    return G.fold(s).replace(GLOSS_RE, "");
  }

  function partRank(fn, part) {
    // match_refs 의 부위 순위(refs._part_rank): 'NNN_' 뒤 첫 _t 표지 앞까지가 부위와 같으면 0, 부위로 시작하면 1, 아니면 2
    const np = part ? glossNorm(part) : "";
    if (!np) return 0;
    const stem = fn.normalize("NFC").replace(/\.[^.]*$/, "");
    const after = stem.slice(stem.indexOf("_") + 1);
    const m = T_RE.exec(after);
    const rest = glossNorm(m ? after.slice(0, m.index) : after);
    return rest === np ? 0 : rest.startsWith(np) ? 1 : 2;
  }

  function refFileName(crop, role, desc) {
    // → {name} | {err}: 대표 "<크롭>_t.png", 보조 "<크롭>_<설명>_t.png". 크롭 이름은 S1 규칙을 이미 통과한 꼴(NFC·strip 그대로)이어야 하고,
    // 결과가 이름 매칭으로 그 크롭에 다시 잡혀야 한다(대표 순위 0, 보조 1)
    if (role !== "main" && role !== "aux") return { err: "role" };
    const c = G.checkName(crop);
    if (c.err || c.name !== crop) return { err: "crop" };
    let name = `${crop}_t.png`;
    if (role === "aux") {
      const d = checkDesc(desc === null || desc === undefined ? "" : desc);
      if (d.err) return d;
      name = `${crop}_${d.desc}_t.png`;
    }
    return partRank(name, c.part) === (role === "main" ? 0 : 1) ? { name } : { err: "rematch" };
  }

  function transformSize(w, h, t) {
    // 불투명 w×h 를 refs.transform_rgba(회전 → 반전 → 배율 → 알파 bbox + pad) 한 크기 [W, H] — 공용 표 transform_size(±2px)
    let W = w;
    let H = h;
    if (t.rotate % 90 === 0) {
      if ((Math.abs(t.rotate) / 90) % 2) [W, H] = [h, w];
    } else {
      const a = (t.rotate * Math.PI) / 180;
      const c = Math.abs(Math.cos(a));
      const s = Math.abs(Math.sin(a));
      W = Math.ceil(w * c + h * s - 1e-6);
      H = Math.ceil(w * s + h * c - 1e-6);
    }
    if (t.scale < 1) {
      W = Math.max(1, Math.floor(W * t.scale + 0.5));
      H = Math.max(1, Math.floor(H * t.scale + 0.5));
    }
    return [W + 2 * t.pad, H + 2 * t.pad];
  }

  function sideOf(name) {
    // vlm.side_of: 토큰 경계의 right·오른쪽 / left·왼쪽(보는 사람 기준), 둘 다 있으면 null
    const found = new Set();
    for (const t of String(name).normalize("NFC").toLowerCase().split(/[^0-9a-z가-힣]+/)) {
      if (SIDE_WORDS.has(t)) found.add(SIDE_WORDS.get(t));
    }
    return found.size === 1 ? [...found][0] : null;
  }

  function hintText(t) {
    // _hint 제안(D7 — 보드는 스펙을 쓰지 않고 복사만): 양수 회전 = 반시계(refs 규칙 메모)
    const did = [];
    if (t.rotate) did.push(`rotated ${Math.abs(t.rotate)}° ${t.rotate > 0 ? "counter-clockwise" : "clockwise"}`);
    if (t.flip_h) did.push("mirrored horizontally");
    return did.length ? `Reference cutout was ${did.join(" and then ")} to match the canvas angle.` : "";
  }

  // ── ComfyUI 큐 본문(I1): 레퍼런스 편집기가 /api/prompt 로 보내는 것은 이것뿐(Studio 의 무료 템플릿 큐는 pipeline.js freeBody 하나) ──
  function promptBody(key, req) {
    // 무료 노드 하나(Ref Cut — 프로젝트 레지스트리 키와 요청 ID 만), 대기열 앞. 노드 키는 숫자가 아님 — ComfyUI 탭이 실행 중 노드를
    // 자기 그래프의 같은 번호 노드로 착각해 강조하지 않게. 인증·탭 정보는 싣지 않는다(무료 노드만 돌 수 있는 경로)
    return { prompt: { dp_refcut: { class_type: "BMKDesignPatchRefCut", inputs: { root: key, req } } }, front: true };
  }

  // ── 서버 세션: 마지막 GET /refs(R)·컷 작업. DOM 을 쓰지 않는다(J-R2 가 node 에서 가짜 fetch 로 돈다) ──
  function session(apiUrl, comfyUrl, root, fetchFn, pollMs) {
    // busy = refs.json 쓰기(저장·연결) 중 — 한 번에 하나(rev = 지금 가진 판), job = ComfyUI 에서 도는 SAM3 컷 {req, pid, number, running, ahead}
    const s = { R: null, err: null, busy: false, job: null };
    let writes = 0; // 이 창의 쓰기 성공 수 — 읽기가 도는 사이 쓰기가 끝나면 그 읽기는 낡았을 수 있다
    let reads = 0; // 보낸 읽기 수(요청 순서)
    let readDone = 0; // 받아들인 읽기 중 가장 늦게 보낸 것의 번호
    let cutSeq = 0; // 컷 차례 — 더 새 컷이 시작됐거나 취소했으면 앞 컷의 응답은 버린다
    const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
    const postOpts = (body) => ({ method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

    async function readJson(r) {
      try {
        return await r.json();
      } catch (e) {
        return null; // JSON 이 아닌 응답(옛 서버의 404·ComfyUI 의 빈 200)
      }
    }

    async function call(path, body) {
      const r = await fetchFn(apiUrl(path), body === undefined ? { cache: "no-store" } : postOpts(Object.assign({ root }, body)));
      const data = await readJson(r);
      if (!r.ok || !data) {
        const msg = data && data.error ? data.error
          : r.status === 404 ? "레퍼런스 라우트가 없습니다 — ComfyUI 를 재시작해야 레퍼런스 편집을 쓸 수 있습니다"
            : `응답을 읽을 수 없습니다 (HTTP ${r.status})`;
        throw Object.assign(new Error(msg), { status: r.status, data });
      }
      return data;
    }

    function comfy(path, body) {
      return fetchFn(comfyUrl(path), body === undefined ? { cache: "no-store" } : postOpts(body));
    }

    s.load = async (force) => {
      // GET /refs. force 가 아니면 since = 지금 판(같으면 unchanged). true = 새 R 을 받음
      const gen = writes;
      const seq = ++reads;
      const p = new URLSearchParams({ root });
      if (!force && s.R && s.R.rev !== null) p.set("since", String(s.R.rev));
      let d;
      try {
        d = await call("refs?" + p.toString());
      } catch (e) {
        s.err = e.message;
        return false;
      }
      // 이 창의 쓰기보다 먼저 보낸 읽기·더 늦게 보낸 읽기를 이미 받은 읽기 — 버림(쓰기 뒤에는 write 가 다시 읽는다)
      if (gen !== writes || seq < readDone) return false;
      s.err = null;
      // unchanged = refs.json rev 만 같다는 뜻 — 앞서 보낸 강제 읽기(레이아웃 이름·바깥 파일 변경은 rev 를 올리지 않음)를 버리게 하지 않는다(codex8)
      if (d.unchanged) return false;
      readDone = seq;
      s.R = d;
      return true;
    };

    s.sheets = () => call("refs/sheets?" + new URLSearchParams({ root }).toString());

    s.sheetUrl = (sh, kind, rect, max) => {
      // 시트 미리보기·원해상도 창 URL(상대) — v = 목록이 준 시트 서명(있으면 오래 캐시)
      const p = new URLSearchParams({ root, sheet: sh.path, kind });
      if (rect) {
        p.set("rect", rect.join(","));
        p.set("max", String(max));
      }
      if (sh.v) p.set("v", sh.v);
      return "refs/sheet?" + p.toString();
    };

    async function write(path, body) {
      // refs.json 쓰기 하나 — 응답 전 다음 쓰기는 보내지 않는다(409 를 부르지 않게). 성공·실패 모두 끝나면 다시 읽는다
      if (s.busy) throw new Error("앞 요청을 처리하는 중입니다 — 끝난 뒤 다시 하세요");
      s.busy = true;
      try {
        const r = await call(path, Object.assign({ rev: s.R ? s.R.rev : null }, body));
        writes += 1;
        return r;
      } finally {
        await s.load(true);
        s.busy = false;
      }
    }

    s.save = (body) => write("refs/save", body);
    s.link = (body) => write("refs/link", body);

    async function enqueue(req) {
      const r = await comfy("prompt", promptBody(root, req));
      const d = await readJson(r);
      if (r.ok && d && d.prompt_id) return d;
      const e = d && d.error;
      if (e && e.type === "missing_node_type") throw new Error("ComfyUI 를 재시작해야 Ref Cut 노드가 생깁니다");
      throw new Error(`ComfyUI 가 SAM3 컷을 큐에 받지 않았습니다: ${(e && e.message) || `HTTP ${r.status}`}`);
    }

    async function place(job) {
      // 대기열에서 이 작업의 자리: running = 실행 중, ahead = 앞 작업 수(실행 중 포함, 대기열 앞 = 번호가 작음), gone = 대기열에 없는 폴링 수.
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

    async function forget(job) {
      // 사용자 실행 기록(/api/history)에서 지움. 기록은 노드가 결과를 쓴 뒤 ComfyUI 가 실행을 마칠 때(task_done) 생기므로 대기열에서
      // 빠진 것을 본 뒤에 보낸다(FORGET_POLLS 번 기다려도 남아 있으면 그냥). 지우지 못해도 컷 결과와는 무관
      try {
        for (let i = 0; i < FORGET_POLLS; i++) {
          if ((await place(job)) && job.gone) break;
          await sleep(pollMs);
        }
        await comfy("history", { delete: [job.pid] });
      } catch (e) {
        // ComfyUI 에 닿지 않음 — 기록만 남는다
      }
    }

    s.cut = async (body, onWait) => {
      // 컷 하나 → 결과 {req, window, mask_url, preview_url, area_frac, n_parts, sec, warnings} | null(더 새 컷이 시작됨·취소).
      // 사각형·다각형·다듬기는 라우트가 바로 계산. sam3 = 라우트는 요청 파일만 쓰고(client_queue) 보드가 Ref Cut 노드를 ComfyUI 대기열 앞에
      // 넣어 GET refs/cut 를 폴링한다(앞 작업 수 = /api/queue). 끝나면 사용자 실행 기록(/api/history)에서 지운다
      if (s.job) throw new Error("SAM3 컷이 ComfyUI 에서 도는 중입니다 — 끝나거나 취소한 뒤 다시 하세요");
      const seq = ++cutSeq;
      const r = await call("refs/cut", body);
      if (seq !== cutSeq) return null;
      if (r.state === "done") return Object.assign({ req: r.req }, r.result);
      const job = { req: r.req, pid: null, number: null, running: false, ahead: null, gone: 0 };
      s.job = job;
      try {
        const q = await enqueue(r.req);
        job.pid = q.prompt_id;
        job.number = q.number;
        if (onWait) onWait(job);
        for (;;) {
          await sleep(pollMs);
          if (seq !== cutSeq) return null;
          const g = await call("refs/cut?" + new URLSearchParams({ root, req: r.req }).toString());
          if (seq !== cutSeq) return null;
          if (g.state === "done") return Object.assign({ req: r.req }, g.result);
          if (g.state === "error") throw new Error(`SAM3 컷 실패: ${g.error}`);
          await place(job);
          if (job.gone >= GONE_POLLS) throw new Error("Ref Cut 노드가 결과를 쓰지 않고 끝났습니다 — ComfyUI 콘솔을 확인하세요");
          if (onWait) onWait(job);
        }
      } finally {
        if (s.job === job) s.job = null;
        if (job.pid) forget(job);
      }
    };

    s.cutResult = async (req) => {
      // 끝난 컷 하나를 다시 받는다(다시 열기 — 기록의 마지막 다듬기 요청) → 결과 | null(대기 중·실패 결과). 요청이 정리됐으면 404 예외
      const g = await call("refs/cut?" + new URLSearchParams({ root, req }).toString());
      return g.state === "done" ? Object.assign({ req }, g.result) : null;
    };

    s.cancel = async () => {
      // 취소 = 대기 중일 때만(실행 중인 노드는 1초 안팎에 끝남 — ComfyUI 의 Cancel 은 사용자 작업까지 멈출 수 있어 쓰지 않는다).
      // 마지막 폴링 뒤 실행이 시작됐을 수 있고 ComfyUI 의 queue delete 는 대기 중 항목만 지우며 늘 200 이라, 지우기 전후로 대기열을 다시 본다.
      // false = 취소하지 않음(실행 중·이미 끝남·대기열을 읽지 못함 — 컷은 결과까지 기다린다)
      const job = s.job;
      if (!job || !job.pid) return false;
      if (!(await place(job)) || job.ahead === null || job.running) return false;
      await comfy("queue", { delete: [job.pid] });
      if (!(await place(job)) || job.ahead !== null || s.job !== job) return false;
      cutSeq += 1;
      s.job = null;
      return true;
    };

    return s;
  }

  // ── 화면 ──
  function create(hosts, opts) {
    const { list, panel, head, tabs: tabHost, sheets: sheetHost, view: viewHost, grid: gridHost } = hosts;
    const viewer = opts.viewer;
    const svg = window.DPViewer.svg;
    const S = session(opts.apiUrl, opts.comfyUrl, opts.root, (u, o) => fetch(u, o), POLL_MS);
    let on = false;
    let state = null; // 보드 state(매니페스트 rev·refs_rev·타깃 Before)
    let seen = null; // /refs 를 마지막으로 맞춘 state 의 [rev, refs_rev, layout_rev, refs_files_v](null = 처음·읽기 실패 뒤)
    let sel = null; // 선택 크롭 id · 고아 연결은 "orphan:<옛 id>"
    let scrolledTo = null; // 목록을 마지막으로 스크롤해 보인 선택
    let sheetList = null; // GET refs/sheets(null = 아직, {error} = 실패)
    let sheet = null; // 연 시트 {path, size, kind, v}
    let tab = opts.tab === "grid" ? "grid" : "sheets"; // 가운데 탭(3-4) — board.js 가 이 브라우저에 기억한 것(없으면 시트)
    let gridOf = null; // 그리드 카드를 마지막으로 만든 [S.R, state] — 같으면 카드는 그대로 두고 선택 표시만
    let gridScrolledTo = null; // 그리드를 마지막으로 스크롤해 보인 선택
    const gridCards = new Map(); // 선택 id(고아 연결 = "orphan:<옛 id>") → 카드
    let rect = null; // 선택 영역(시트 px, 끝 배타)
    let method = "rect";
    let points = []; // SAM 점 [x, y, 1|0]
    let poly = []; // 그리는 중인 다각형 꼭짓점 [x, y]
    let lastClick = null; // 다각형 더블클릭 판정 {t, x, y}(화면 px)
    let drag = null; // {kind: new|resize, handle, start, rect0, x, y, moved}
    let result = null; // 끝난 컷 {req, rect, method, window, mask_url, preview_url, area_frac, n_parts, sec, warnings, auto_point?(sam3)}
    let inflight = 0; // 응답을 기다리는 컷 요청 수
    let epoch = 0; // 편집 세대 — 결과를 버릴 때마다(dropResult: 다시 열기·시트 열기·Esc·새 영역) 올림, 앞 세대의 컷·다시 열기 응답은 버린다
    let opens = 0; // 시트 열기 차례(목록 클릭·다시 열기) — 목록을 다시 읽는 사이 더 늦은 열기가 있으면 앞 것은 그만둠
    let queued = []; // 도는 컷을 기다리는 Shift/Alt 다각형 {op, pts} — 끝나면 그 결과에 차례로 잇는다(catchUp), 새 선택이면 버림
    let replay = null; // 되살리지 못한 기록 다시 열기 {at: epoch, rect, method, steps: step_polys} — 그 세대·영역·방법의 Enter = 기록대로 다시 컷
    let grow = 0;
    let feather = 0;
    let text = ""; // SAM3 텍스트(선택, 기본값 없음)
    let ckpt = null; // SAM3 체크포인트(null = 노드 기본 — 이름에 sam3 가 든 첫 항목)
    let T = { rotate: 0, flip_h: false, scale: 1 };
    let role = "main";
    let desc = "";
    let matte = "gray";
    let asym = false; // "글자·비대칭 있음"(반전 경고)
    let overlay = null; // 겹쳐 보기 {alpha, x, y, k} — 화면용, 저장 안 함
    let cutImg = null; // 결과 미리보기 Image(변환 미리보기 원본)
    let cutBox = null; // 그 알파 bbox {x, y, w, h}(원본 px) — null = 아직·모두 투명
    let composing = false; // 패널 입력칸에서 한글 조합 중
    let pendingPanel = false; // 입력 중이라 미룬 패널 다시 그리기
    let live = {}; // 패널을 다시 만들지 않고 고치는 칸
    // 다시 그려도 남는 비교 칸(크롭 Before · 변환 미리보기)
    const cmp = el("div", "rf-cmp");
    const bfBox = el("div", "rf-bf");
    const bfImg = el("img", "rf-bfimg");
    const ctBox = el("div", "rf-ct");
    const cutCanvas = el("canvas", "rf-cut");
    bfImg.alt = "";
    bfImg.draggable = false;
    bfBox.append(bfImg);
    // 가운데 탭 줄(3-4 — F22): [시트] = 시트 목록 ↔ 시트 뷰어 · [그리드] = 크롭마다 카드
    const tabBtns = [["sheets", "시트", "시트 목록 ↔ 시트 뷰어 — 시트에서 부품을 오려 저장"],
      ["grid", "그리드", "크롭마다 크롭 소스 · 대표 · 보조 — 매칭을 한눈에(카드를 누르면 오른쪽 관리 패널)"]].map(([t, label, title]) => {
      const b = button(label, title, () => {
        setTab(t);
        render();
      }, "tab");
      b.setAttribute("role", "tab");
      b.dataset.tab = t;
      return b;
    });
    tabHost.append(...tabBtns);

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

    function row(key, txt, cls) {
      const r = el("div", "lp-row " + (cls || ""));
      r.append(el("span", "k", key), el("span", "", txt));
      panel.append(r);
      return r;
    }

    function textField(e) {
      return !!e && ((e.tagName === "INPUT" && e.type === "text") || e.tagName === "TEXTAREA");
    }

    function typingInPanel() {
      return composing || (panel.contains(document.activeElement) && textField(document.activeElement));
    }

    function sizeMb(bytes) {
      return bytes >= 1048576 ? `${(bytes / 1048576).toFixed(1)}MB` : `${Math.max(1, Math.round(bytes / 1024))}KB`;
    }

    function sameRect(a, b) {
      return !!a && !!b && a.every((v, i) => v === b[i]);
    }

    function byName(a, b) {
      return a.name < b.name ? -1 : a.name > b.name ? 1 : 0;
    }

    // ── 상태 ──
    function crops() {
      return S.R ? S.R.crops : [];
    }

    function cur() {
      return (sel && crops().find((c) => c.id === sel)) || null;
    }

    function curOrphan() {
      const id = sel && sel.startsWith("orphan:") ? sel.slice(7) : null;
      return (id && S.R && S.R.orphans.find((o) => o.id === id)) || null;
    }

    function ckpts() {
      return S.R ? S.R.sam3.ckpts : [];
    }

    function refsBlock() {
      // 저장·연결을 막는 이유(§1.1) — null = 됨. 보드는 Import 위젯을 바꿀 수 없다
      const R = S.R;
      if (!R) return S.err ? `레퍼런스를 읽지 못했습니다: ${S.err}` : "불러오는 중…";
      if (!R.refs_dir) return "Import PSD 의 refs_dir 가 비었습니다 — <프로젝트>\\refs 를 넣고 큐에 넣으면 저장·연결을 쓸 수 있습니다(시트 보기·컷은 됨).";
      if (!R.refs_dir_ok) return `refs_dir 폴더가 없습니다: ${R.refs_dir} — Import PSD 의 refs_dir 를 확인하세요.`;
      if (R.error) return `refs.json 을 읽을 수 없어 저장·연결을 막았습니다(크롭은 이름 매칭으로 보임): ${R.error}`;
      if (R.doc_dir_match === false) {
        // 서버는 이때 모든 쓰기를 409(_DIR_MISMATCH)로 막는다
        return `refs.json 의 refs_dir(${R.doc_refs_dir})가 지금 refs_dir(${R.refs_dir})와 달라 연결을 무시하고 이름 매칭 중입니다(R-dir) — `
          + "Import PSD 의 refs_dir 를 되돌리거나 refs.json 을 치운 뒤 저장·연결을 쓸 수 있습니다(시트 보기·컷은 됨).";
      }
      return null;
    }

    function whereText(x) {
      return `${WHERE[x.where] || x.where} ${x.crop_id}${x.tid ? ` ${x.tid}` : ""}`;
    }

    function beforeUrl(c) {
      const t = state && c ? state.targets.find((x) => x.crop_id === c.id) : null;
      return t ? t.before_full_url : null;
    }

    function sizeText(r) {
      // 선택 아래·패널: "771×847 · SAM 1008² (×1.19)" — SAM 이 선택의 긴 변을 1008 로 늘이거나 줄여 봄
      const w = r[2] - r[0];
      const h = r[3] - r[1];
      return `${w}×${h} · SAM ${SAM_SIDE}² (×${(SAM_SIDE / Math.max(w, h)).toFixed(2)})`;
    }

    function samWindow(r) {
      // refs.cut_window: 선택 + 변마다 폭·높이 × 12%(반올림), 시트 안
      const [W, H] = sheet.size;
      const px = Math.round((r[2] - r[0]) * SAM_PAD);
      const py = Math.round((r[3] - r[1]) * SAM_PAD);
      return [Math.max(0, r[0] - px), Math.max(0, r[1] - py), Math.min(W, r[2] + px), Math.min(H, r[3] + py)];
    }

    function clampPt(pt) {
      const [W, H] = sheet.size;
      return [Math.min(Math.max(Math.round(pt[0]), 0), W), Math.min(Math.max(Math.round(pt[1]), 0), H)];
    }

    function inside(pt, r) {
      return pt[0] >= r[0] && pt[0] <= r[2] && pt[1] >= r[1] && pt[1] <= r[3];
    }

    function boxOf(a, b) {
      const p = clampPt(a);
      const q = clampPt(b);
      return [Math.min(p[0], q[0]), Math.min(p[1], q[1]), Math.max(p[0], q[0]), Math.max(p[1], q[1])];
    }

    function polyRect(pts) {
      // 새 다각형 선택의 사각형 = 꼭짓점 bbox(정수, 시트 안), 짧은 변이 16 보다 작으면 가운데 기준으로 넓힘
      const [W, H] = sheet.size;
      const xs = pts.map((p) => p[0]);
      const ys = pts.map((p) => p[1]);
      const grow1 = (a, b, m) => {
        const need = Math.max(0, MIN_SIDE - (b - a));
        const lo = Math.max(0, Math.min(a - Math.ceil(need / 2), m - MIN_SIDE));
        return [lo, Math.min(m, Math.max(b, lo + MIN_SIDE))];
      };
      const [x0, x1] = grow1(Math.floor(Math.min(...xs)), Math.ceil(Math.max(...xs)), W);
      const [y0, y1] = grow1(Math.floor(Math.min(...ys)), Math.ceil(Math.max(...ys)), H);
      return [x0, y0, x1, y1];
    }

    // ── 그리기(시트 뷰어 svg, 시트 px) ──
    function draw(root, z) {
      if (!on || !sheet) return;
      const u = 1 / z; // 화면 1px 의 시트 길이
      const add = (tag, attrs, txt) => {
        const e = svg(tag, attrs);
        if (txt !== undefined) e.textContent = txt;
        root.append(e);
        return e;
      };
      const box = (r, cls) => add("rect", { x: r[0], y: r[1], width: r[2] - r[0], height: r[3] - r[1], class: cls });
      const href = result ? opts.apiUrl(result.preview_url) : null;
      if (href) {
        // 결과: 바깥을 어둡게, 오린 그림(창 크기 RGBA) 위에 청록 외곽선(알파 팽창 − 원본)
        const defs = svg("defs");
        const f = svg("filter", { id: "dp-cut-line", x: "-1%", y: "-1%", width: "102%", height: "102%" });
        f.append(svg("feMorphology", { in: "SourceAlpha", operator: "dilate", radius: 1.5 * u, result: "d" }),
          svg("feFlood", { "flood-color": "#4dd6e8", result: "c" }), svg("feComposite", { in: "c", in2: "d", operator: "in", result: "o" }));
        const mg = svg("feMerge");
        mg.append(svg("feMergeNode", { in: "o" }), svg("feMergeNode", { in: "SourceGraphic" }));
        f.append(mg);
        defs.append(f);
        root.append(defs);
        box([0, 0, sheet.size[0], sheet.size[1]], "rdim");
        const [x0, y0, x1, y1] = result.window;
        add("image", { href, x: x0, y: y0, width: x1 - x0, height: y1 - y0, preserveAspectRatio: "none", filter: "url(#dp-cut-line)" });
      }
      if (rect) {
        if (method === "sam3") box(samWindow(rect), "rwin");
        box(rect, "rsel");
        if (method !== "poly" && !drag) {
          const pts = handlePoints(rect);
          for (const h of HANDLES) box([pts[h][0] - 4 * u, pts[h][1] - 4 * u, pts[h][0] + 4 * u, pts[h][1] + 4 * u], "handle");
        }
        const big = Math.max(rect[2] - rect[0], rect[3] - rect[1]) > 2 * SAM_SIDE;
        add("text", { x: rect[0], y: rect[3] + 14 * u, class: big ? "linfo bad" : "linfo", "font-size": 11 * u, "stroke-width": 3 * u },
          sizeText(rect));
      }
      if (method === "sam3") {
        for (const p of points) add("circle", { cx: p[0], cy: p[1], r: 5 * u, class: "rpt " + (p[2] ? "pos" : "neg") });
        // 자동 양점 = 노드가 상자 가운데에 가장 가까운 불투명 픽셀(시트 알파 — 미리보기는 흰 바탕 JPEG 라 보드는 모름). SAM3 결과가
        // auto_point(시트 좌표, null = 선택 안이 모두 투명)를 실으면 그 자리, 그 전에는 상자 가운데에 '대략'으로
        const known = !!result && result.method === "sam3" && result.auto_point !== undefined;
        const ap = known ? result.auto_point : rect && [(rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2];
        if (ap) {
          const c = add("circle", { cx: ap[0], cy: ap[1], r: 5 * u, class: known ? "rpt auto" : "rpt auto approx" });
          const tip = svg("title");
          tip.textContent = known ? "자동 양점 — 노드가 이 컷에 맨 앞에 넣은 점(상자 가운데에 가장 가까운 불투명 픽셀, 찍은 점은 그 뒤에)"
            : "자동 양점(대략 — 상자 가운데) — 실제 점은 노드가 상자 가운데에 가장 가까운 불투명 픽셀로 정해 늘 맨 앞에 씀(찍은 점은 그 뒤에)";
          c.append(tip);
        }
      }
      if (poly.length) {
        add("polyline", { points: poly.map((p) => p.join(",")).join(" "), class: "rpoly" });
        for (const p of poly) box([p[0] - 3 * u, p[1] - 3 * u, p[0] + 3 * u, p[1] + 3 * u], "rvtx");
      }
    }

    function handlePoints(r) {
      const [x0, y0, x1, y1] = r;
      const mx = (x0 + x1) / 2;
      const my = (y0 + y1) / 2;
      return { nw: [x0, y0], n: [mx, y0], ne: [x1, y0], e: [x1, my], se: [x1, y1], s: [mx, y1], sw: [x0, y1], w: [x0, my] };
    }

    function handleAt(pt, r) {
      const d = HANDLE_PX / viewer.scale();
      const pts = handlePoints(r);
      return HANDLES.find((h) => Math.abs(pt[0] - pts[h][0]) <= d && Math.abs(pt[1] - pts[h][1]) <= d) || null;
    }

    function resized(r0, handle, pt) {
      const p = clampPt(pt);
      const r = r0.slice();
      if (handle.includes("w")) r[0] = p[0];
      if (handle.includes("e")) r[2] = p[0];
      if (handle.includes("n")) r[1] = p[1];
      if (handle.includes("s")) r[3] = p[1];
      return [Math.min(r[0], r[2]), Math.min(r[1], r[3]), Math.max(r[0], r[2]), Math.max(r[1], r[3])];
    }

    // ── 마우스(뷰어가 왼쪽 버튼을 먼저 넘김, true = 소비) ──
    function pointerdown(e) {
      if (!on || !sheet) return false;
      const pt = viewer.canvasPoint(e);
      const h = method !== "poly" && rect && handleAt(pt, rect);
      drag = { kind: h ? "resize" : "new", handle: h, start: pt, rect0: rect, x: e.clientX, y: e.clientY, moved: false };
      return true;
    }

    function pointermove(e) {
      if (!on || !sheet) return false;
      const pt = viewer.canvasPoint(e);
      if (!drag) {
        const h = method !== "poly" && rect && handleAt(pt, rect);
        viewer.setCursor(h ? CURSORS[h] : method === "poly" || (method === "sam3" && rect && inside(pt, rect)) ? "crosshair" : "");
        return false;
      }
      if (!drag.moved && Math.hypot(e.clientX - drag.x, e.clientY - drag.y) < CLICK_PX) return true;
      if (method === "poly" && drag.kind === "new") return true; // 다각형 모드의 끌기는 꼭짓점 하나(놓을 때)
      drag.moved = true;
      rect = drag.kind === "resize" ? resized(drag.rect0, drag.handle, pt) : boxOf(drag.start, pt);
      viewer.redraw();
      return true;
    }

    function pointerup(e) {
      const d = drag;
      drag = null;
      if (!d) return;
      if (!d.moved) {
        click(viewer.canvasPoint(e), e);
        return;
      }
      if (Math.min(rect[2] - rect[0], rect[3] - rect[1]) < MIN_SIDE) {
        opts.toast(`선택의 짧은 변은 ${MIN_SIDE}px 이상이어야 합니다`, "err");
        rect = d.rect0;
      } else {
        // 새 영역 = 점·결과를 버림, 핸들로 고침 = 영역 안 점만 남김(결과는 그 영역 것이 아님)
        points = d.kind === "new" ? [] : points.filter((p) => inside(p, rect));
        dropResult();
      }
      render();
    }

    function cancel() {
      if (drag && drag.moved) rect = drag.rect0;
      drag = null;
      viewer.redraw();
    }

    function click(pt, e) {
      if (method === "poly") {
        addVertex(pt, e);
      } else if (method === "sam3" && rect && inside(pt, rect)) {
        if (points.length >= MAX_POINTS) {
          opts.toast(`점은 ${MAX_POINTS}개까지입니다`, "warn");
          return;
        }
        const p = clampPt(pt);
        points.push([p[0], p[1], e.altKey ? 0 : 1]);
        render();
      }
    }

    function addVertex(pt, e) {
      const now = Date.now();
      const again = lastClick && now - lastClick.t < DBL_MS && Math.hypot(e.clientX - lastClick.x, e.clientY - lastClick.y) < 2 * CLICK_PX;
      lastClick = { t: now, x: e.clientX, y: e.clientY };
      if (again && poly.length >= 3) {
        lastClick = null;
        closePoly(e);
        return;
      }
      if (poly.length >= MAX_VERTS) {
        opts.toast(`꼭짓점은 ${MAX_VERTS}개까지입니다`, "warn");
        return;
      }
      poly.push(clampPt(pt));
      render();
    }

    function closePoly(mods) {
      // Shift = 앞 결과에 더하기, Alt = 빼기(base = 앞 컷, 그 사각형·창 그대로), 그 밖 = 새 다각형 선택(사각형 = 꼭짓점 bbox)
      if (poly.length < 3) {
        opts.toast("다각형은 꼭짓점 3개 이상입니다 — 클릭으로 꼭짓점, 더블클릭·Enter 로 닫기", "warn");
        return;
      }
      const op = mods.shiftKey ? "add" : mods.altKey ? "sub" : null;
      const pts = poly;
      poly = [];
      if (op && result) {
        // 컷이 도는 중이면 그 결과에 잇는다 — 지금 보내면 base 가 낡은 결과라 도는 컷의 응답을 밀어냄(cutSeq)
        if (inflight) {
          queued.push({ op, pts });
          render();
        } else {
          runCut({ method: "poly", rect: result.rect, base: result.req, polys: [{ op, pts }] });
        }
        return;
      }
      if (op) opts.toast("앞 결과가 없어 새 다각형 선택으로 오립니다", "warn");
      rect = polyRect(pts);
      points = [];
      dropResult();
      runCut({ method: "poly", rect, polys: [{ op: "add", pts }] });
    }

    // ── 컷 ──
    function dropResult() {
      result = null;
      queued = [];
      cutImg = null;
      cutBox = null;
      epoch += 1;
    }

    async function runCut(fields) {
      // sheet_v = 연 시트의 서명 — 바깥에서 저장됐으면 서버가 409 sheet_changed(옛 미리보기로 고른 영역을 지금 파일에서 오리지 않게)
      const body = Object.assign({ sheet: sheet.path, sheet_v: sheet.v, grow, feather }, fields);
      const at = epoch;
      const prev = result; // 다듬기(base)의 앞 결과 — 연쇄의 방법·자동 양점을 잇는다
      let got = null;
      replay = null;
      if (!body.base) queued = []; // 새 선택 = 앞 연쇄에 이을 다각형을 버림(도는 컷의 응답도 cutSeq 가 버림)
      inflight += 1;
      render();
      try {
        const r = await S.cut(body, showJob); // SAM3 대기 폴링(1초마다)은 대기 표시만 고친다 — 패널을 다시 만들면 끌기·select 가 끊긴다
        if (!r) return; // 더 새 컷이 시작됨·취소
        // 같은 시트·같은 영역을 다시 열었어도 앞 세대 응답(도는 다듬기·다각형)은 되살린 결과를 덮지 않게
        if (at !== epoch || !sameRect(body.rect, rect)) {
          opts.toast("기다리는 사이 시트·선택이 바뀌어 이 컷 결과를 버렸습니다", "warn");
          return;
        }
        // 다듬기(grow·feather·Shift/Alt 다각형 — base = 앞 결과)는 SAM 을 다시 돌리지 않는다: 방법은 연쇄의 첫 방법(SAM3 시도 그대로),
        // 자동 양점은 서버가 base 결과의 auto_point 를 이어 싣고(board.ref_cut), 옛 서버면 앞 결과의 것을 잇는다(F22 — 상자 가운데 '대략'으로
        // 돌아가지 않게). grow·feather = 이 결과를 만든 값(catchUp — 화면 슬라이더와 다르면 이어서 다듬음)
        const chain = !!body.base && !!prev && prev.req === body.base;
        result = Object.assign({ rect: body.rect, method: chain ? prev.method : body.method, grow: body.grow, feather: body.feather },
          chain && "auto_point" in prev ? { auto_point: prev.auto_point } : {}, r);
        got = result;
        loadCutImg();
        if (r.warnings && r.warnings.length) opts.toast(r.warnings.join(" · "), "warn");
      } catch (e) {
        if (!(e.status === 409 && e.data && e.data.sheet_changed)) opts.toast(e.message, "err");
        else if (sheet && sheet.path === body.sheet && sheet.v === body.sheet_v) {
          dropResult(); // 옛 시트의 결과·기다리는 다각형은 새 그림에 잇지 않는다(아래 catchUp 도)
          reloadSheet(sheet);
        }
      } finally {
        inflight -= 1;
        renderSoon();
        catchUp(got); // 버린 응답·취소(null)여도 — 기다리던 다각형이 남지 않게
      }
    }

    function catchUp(got) {
      // 도는 컷이 모두 끝나면 기다리는 사이 미룬 '앞 결과에 이어 하는 컷'을 지금 결과에 차례로 잇는다: Shift/Alt 다각형(queued — 하나씩,
      // 그때 슬라이더의 grow·feather 로) → 슬라이더로 바꾼 grow·feather(미룬 다듬기 · 다시 열기 응답 전이라 건너뛴 다듬기) — 화면의 다듬기
      // 값 = 결과·저장되는 값(F06). 다듬기는 방금 끝난 컷의 결과에만(got — 실패한 다듬기를 되풀이하지 않게, 남은 차이는 저장이 막음)
      if (inflight || !result) return;
      if (queued.length) runCut({ method: "poly", rect: result.rect, base: result.req, polys: [queued.shift()] });
      else if (got && result === got && (got.grow !== grow || got.feather !== feather)) refine();
    }

    function doCut() {
      if (!sheet) {
        opts.toast("가운데 목록에서 시트를 먼저 여세요", "warn");
      } else if (replay && replay.at === epoch && replay.method === method && sameRect(rect, replay.rect) && !poly.length) {
        // 기록대로 다시 컷: 첫 컷(+ 그 polys) 뒤 다듬기 단계의 다각형을 하나씩 잇는다(queued → catchUp). 다각형을 하나씩 이어도 차례대로
        // 더하기/빼기라 단계마다 한 번에 보낸 것과 같은 마스크, grow·feather 는 되살린 슬라이더 값
        const [first, ...rest] = replay.steps;
        runCut(Object.assign(method === "sam3" ? Object.assign({ points, text: text.trim() }, ckpt ? { ckpt } : {}) : {}, { method, rect, polys: first }));
        queued = rest.flat();
      } else if (method === "poly") {
        closePoly({});
      } else if (!rect) {
        opts.toast("영역을 먼저 고르세요 — 시트의 빈 곳에서 끌기", "warn");
      } else if (method === "rect") {
        runCut({ method: "rect", rect });
      } else {
        runCut(Object.assign({ method: "sam3", rect, points, text: text.trim() }, ckpt ? { ckpt } : {}));
      }
    }

    function refine() {
      // 다듬기(grow·feather) = 같은 선택 마스크를 base 로 다시 — 라우트 계산, SAM 다시 안 돎. 컷이 도는 중이면 그 결과에(catchUp)
      if (result && !inflight) runCut({ method: "poly", rect: result.rect, base: result.req, polys: [] });
    }

    async function cancelCut() {
      if (live.cancel) live.cancel.disabled = true; // 대기열을 다시 보는 동안 두 번 누르지 않게
      if (await S.cancel()) opts.toast("SAM3 컷 취소 — ComfyUI 대기열에서 지움");
      else opts.toast("이미 실행 중이거나 끝나 취소하지 않았습니다 — 결과를 기다립니다", "warn");
      render();
    }

    function setMethod(m) {
      method = m;
      poly = [];
      render();
    }

    // ── 변환 미리보기(클라이언트 캔버스 — 저장 결과와 ±2px) ──
    function loadCutImg() {
      const img = new Image();
      cutImg = img;
      cutBox = null;
      img.onload = () => {
        if (cutImg !== img) return;
        cutBox = alphaBox(img);
        drawPreview();
        renderSoon();
      };
      img.src = opts.apiUrl(result.preview_url);
    }

    function alphaBox(img) {
      // 오린 그림의 알파 > 0 모양(원본 px, 원크기 — 띠로 나눠 읽음): bbox {x, y, w, h} + rows = 줄마다 [y, 왼쪽 끝, 오른쪽 끝 + 1].
      // 볼록 껍질은 줄 끝 픽셀의 모서리들 안에 있어 변환 뒤 bbox 를 그것으로 잰다(shapeBox)
      const W = img.naturalWidth;
      const H = img.naturalHeight;
      const n = Math.max(1, Math.min(H, Math.floor(STRIP_PX / Math.max(1, W))));
      const c = document.createElement("canvas");
      c.width = W;
      c.height = n;
      const ctx = c.getContext("2d");
      const rows = [];
      for (let y0 = 0; y0 < H; y0 += n) {
        const m = Math.min(n, H - y0);
        ctx.clearRect(0, 0, W, n);
        ctx.drawImage(img, 0, y0, W, m, 0, 0, W, m);
        const a = ctx.getImageData(0, 0, W, m).data;
        for (let y = 0; y < m; y++) {
          const o = y * W * 4 + 3;
          let l = 0;
          while (l < W && !a[o + 4 * l]) l++;
          if (l === W) continue;
          let r = W;
          while (!a[o + 4 * (r - 1)]) r--;
          rows.push([y0 + y, l, r]);
        }
      }
      if (!rows.length) return null;
      let x0 = W;
      let x1 = 0;
      for (const [, l, r] of rows) {
        x0 = Math.min(x0, l);
        x1 = Math.max(x1, r);
      }
      const y0 = rows[0][0];
      return { x: x0, y: y0, w: x1 - x0, h: rows[rows.length - 1][0] + 1 - y0, rows };
    }

    function shapeBox(t) {
      // 회전한 알파 모양의 bbox: {w, h}(원본 px, 배율 전)와 그 가운데 m(cutBox 가운데 기준, 돌린 축). refs.transform_rgba 는 창 전체를 돌린 뒤
      // 알파 bbox 로 자르므로 원·대각선 띠처럼 사각형이 아닌 모양은 돌린 cutBox 보다 작다. 돌림 = 캔버스 rotate(−θ)(cv2 와 같은 쪽)
      const a = (t.rotate * Math.PI) / 180;
      const cs = Math.cos(a);
      const sn = Math.sin(a);
      const b = cutBox;
      const ox = b.x + b.w / 2;
      const oy = b.y + b.h / 2;
      let x0 = Infinity;
      let x1 = -Infinity;
      let y0 = Infinity;
      let y1 = -Infinity;
      for (const [y, l, r] of b.rows) {
        for (const px of [l - ox, r - ox]) {
          for (const py of [y - oy, y + 1 - oy]) {
            const X = cs * px + sn * py;
            const Y = cs * py - sn * px;
            x0 = Math.min(x0, X);
            x1 = Math.max(x1, X);
            y0 = Math.min(y0, Y);
            y1 = Math.max(y1, Y);
          }
        }
      }
      return { w: x1 - x0, h: y1 - y0, m: [(x0 + x1) / 2, (y0 + y1) / 2] };
    }

    function savedSize() {
      // 저장 크기 = 돌린 모양의 bbox(정수 올림)에 배율·pad(transformSize 의 회전 0 경로)
      if (!cutBox) return null;
      const b = shapeBox(T);
      return transformSize(Math.ceil(b.w - 1e-6), Math.ceil(b.h - 1e-6), Object.assign({}, T, { rotate: 0, pad: TRIM_PAD }));
    }

    function drawPreview() {
      const ctx = cutCanvas.getContext("2d");
      const size = savedSize();
      if (!size || !ctx) {
        cutCanvas.width = 0;
        cutCanvas.height = 0;
        return;
      }
      const k = Math.min(1, PREVIEW_MAX / Math.max(size[0], size[1]));
      cutCanvas.width = Math.max(1, Math.round(size[0] * k));
      cutCanvas.height = Math.max(1, Math.round(size[1] * k));
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(0, 0, cutCanvas.width, cutCanvas.height);
      // 저장 순서 = 회전(양수 반시계) → 좌우 반전 → 배율. 캔버스 rotate 는 y 가 아래라 양수가 시계 방향.
      // 돌린 모양의 bbox 가운데(m)를 캔버스 가운데에
      const m = shapeBox(T).m;
      ctx.translate(cutCanvas.width / 2, cutCanvas.height / 2);
      ctx.scale(k * T.scale * (T.flip_h ? -1 : 1), k * T.scale);
      ctx.translate(-m[0], -m[1]);
      ctx.rotate((-T.rotate * Math.PI) / 180);
      const b = cutBox;
      ctx.drawImage(cutImg, b.x, b.y, b.w, b.h, -b.w / 2, -b.h / 2, b.w, b.h);
      placeOverlay();
      if (live.size) live.size.textContent = sizeLine();
    }

    function sizeLine() {
      const s = savedSize();
      if (!s) return cutImg ? "미리보기 불러오는 중…" : "—";
      return `저장 크기 약 ${s[0]}×${s[1]}` + (Math.max(s[0], s[1]) > BIG_SIDE ? ` — 긴 변 > ${BIG_SIDE}: 축소 권장(배율)` : "");
    }

    function placeOverlay() {
      if (!overlay) return;
      Object.assign(cutCanvas.style, { left: `${overlay.x}px`, top: `${overlay.y}px`, opacity: String(overlay.alpha),
        width: `${cutCanvas.width * overlay.k}px`, height: `${cutCanvas.height * overlay.k}px` });
    }

    function toggleOverlay() {
      overlay = overlay ? null : { alpha: 0.5, x: 0, y: 0, k: Math.min(1, (bfImg.clientWidth || 320) / Math.max(1, cutCanvas.width)) };
      if (!overlay) Object.assign(cutCanvas.style, { left: "", top: "", opacity: "", width: "", height: "" });
      render();
    }

    let ovDrag = null;
    cutCanvas.addEventListener("pointerdown", (e) => {
      if (!overlay) return;
      ovDrag = { x: e.clientX, y: e.clientY, ox: overlay.x, oy: overlay.y, id: e.pointerId };
      cutCanvas.setPointerCapture(e.pointerId);
    });
    cutCanvas.addEventListener("pointermove", (e) => {
      if (!overlay || !ovDrag || ovDrag.id !== e.pointerId) return;
      overlay.x = ovDrag.ox + e.clientX - ovDrag.x;
      overlay.y = ovDrag.oy + e.clientY - ovDrag.y;
      placeOverlay();
    });
    cutCanvas.addEventListener("pointerup", () => {
      ovDrag = null;
    });
    bfBox.addEventListener("wheel", (e) => {
      if (!overlay) return;
      e.preventDefault();
      overlay.k = Math.min(8, Math.max(0.05, overlay.k * Math.exp(-e.deltaY * 0.0015)));
      placeOverlay();
    }, { passive: false });

    function setT(next) {
      T = Object.assign({}, T, next);
      drawPreview();
      if (live.hint) live.hint.textContent = hintText(T) || "회전·반전 없음 — 제안할 _hint 없음";
    }

    function rotateBy(d) {
      // −180…180 안으로 돌림(180 = −180), 0.5° 단위
      const r = ((((T.rotate + d + 180) % 360) + 360) % 360) - 180;
      setT({ rotate: Math.round(r * 2) / 2 });
      render();
    }

    // ── 쓰기 ──
    function explicitOf(e) {
      return e.status === 409 && e.data && Array.isArray(e.data.explicit) ? e.data.explicit : null;
    }

    function explicitText(e, ex) {
      // 이름으로 직접 가리키는 파일을 옮기거나 그 이름에 새 그림을 씀(D5) — 무엇이 바뀌는지
      const lines = ex.map((x) => ` · ${whereText(x)} → ${x.file}`);
      return [e.message, ...lines, "이 이름을 쓰는 곳은 새 그림으로 바뀌거나 파일이 옮겨집니다(스펙·프로젝트 기록 타깃은 Prepare 가 연결 대신 이 이름을 씀).",
        ...(S.R && S.R.outside_project ? ["refs_dir 가 프로젝트 밖 — 다른 프로젝트에도 보입니다."] : []), "계속할까요?"].join("\n");
    }

    function sameNamed(c, name) {
      const k = G.fold(name);
      return c.files.concat(c.unlinked).find((f) => G.fold(f.name) === k) || null;
    }

    function saveProblem(c, nm) {
      if (!c) return "크롭을 고르세요";
      const block = refsBlock();
      if (block) return block;
      if (!result) return "컷을 먼저 하세요";
      // 다듬기·다시 컷이 도는 중 — 저장은 지금 결과(req)를 보내는데 곧 새 결과가 화면을 바꾼다
      if (inflight) return "컷·다듬기를 마치는 중 — 결과가 나온 뒤 저장하세요";
      // 슬라이더의 grow·feather 가 결과를 만든 값과 다름(이어 다듬기가 실패함) — 저장은 결과(req)의 값이라 화면과 다른 그림이 됨
      if (result.grow !== undefined && (result.grow !== grow || result.feather !== feather)) {
        return `grow·feather 가 결과(${result.grow}px · σ ${result.feather})와 다름(이어 다듬기 실패) — 슬라이더를 움직여 다시 다듬은 뒤 저장하세요`;
      }
      if (nm.err) return NAME_ERRS[nm.err];
      if (role === "aux" && sameNamed(c, nm.name)) return `같은 설명이 있음(${nm.name}) — 다른 설명을 쓰세요`;
      if (S.busy) return "저장 중…";
      return null;
    }

    function auxShift(c) {
      // 파일 하나를 보조로 더할 때(저장·연결·되살리기) Prepare 선택이 바뀌는가. 있는 대표가 있으면 그대로(서버 _add_name 은 대표를 두고 끝에
      // 더함) → null. 쓰는 파일이 하나도 없으면 이것이 Prepare 가 고르는 첫 레퍼런스 → "first"(새 셀), 대표만 사라졌으면 이름 규칙이
      // 이것을 고를 수 있음 → "maybe"
      if (c.files.some((f) => f.role === "main" && !f.missing)) return null;
      return c.files.some((f) => !f.missing) ? "maybe" : "first";
    }

    function asWhat(c) {
      // 더한 파일의 자리(서버 _add_name): 비었던 연결만 그 파일이 대표(main), 사라진 대표가 있으면 대표 칸은 그대로 두고 보조 끝에
      return c.files.length ? "보조로" : "대표로";
    }

    function auxLine(c) {
      // 보조 하나를 더하는 영향 줄 [cls, 문구](§4.5·I5 — 보조만 더하면 셀 그대로, 단 지금 레퍼런스가 없으면 그 파일이 골라짐)
      const k = auxShift(c);
      if (k === "first") {
        return ["warn", c.files.length
          ? "다음 Run 에서 이 크롭은 새 생성 조건(유료 대상) — 연결 파일이 모두 사라져 Prepare 가 이 파일을 고름(대표 칸은 사라진 파일 그대로)"
          : "다음 Run 에서 이 크롭은 새 생성 조건(유료 대상) — 지금 레퍼런스가 없어 이 파일이 대표가 됨(Prepare 가 고름)"];
      }
      if (k === "maybe") return ["warn", "대표 파일이 없어 Prepare 가 이름 규칙으로 고름 — 이 파일이 골라지면 다음 Run 에서 새 생성 조건(유료 대상)"];
      return ["info", "Prepare 선택 그대로(cell_key 그대로), Spec Draft 는 refresh 가능"];
    }

    function siblingLine(c, name) {
      // 새 이름을 refs_dir 에 들이면(저장·되살리기) 서버는 그것을 이름 매칭('NNN_' 접두)으로 받게 될 고정 안 된 같은 번호 크롭을 그 순간의
      // 레퍼런스로 함께 고정한다(board._match_seeds — 그 크롭들의 선택·셀·초안 키 그대로, I5) → 영향 줄 문구 | null
      const n = name.normalize("NFC");
      const sib = crops().filter((o) => {
        const nnn = G.splitNnn(o.name)[0];
        return o.id !== c.id && !o.fixed && nnn && n.startsWith(`${nnn}_`);
      });
      if (!sib.length) return null;
      const kept = sib.filter((o) => o.files.length);
      const empty = sib.filter((o) => !o.files.length); // 빈 seed — '레퍼런스 없음'으로 고정(자동, board fixed.auto)
      return [kept.length ? `같은 번호 크롭 ${kept.map((o) => o.name).join(", ")} 는 지금 레퍼런스로 고정됨(ID — 이 파일을 이름 매칭으로 받지 않게, 선택·생성 조건 그대로)` : null,
        empty.length ? `같은 번호 크롭 ${empty.map((o) => o.name).join(", ")} 는 레퍼런스 없음으로 고정됨(이 파일을 이름 매칭으로 받지 않게 — 생성 조건 그대로)` : null]
        .filter(Boolean).join(" · ") + " — 이후 PS 로 넣는 같은 번호 새 파일은 그 크롭에 [연결]해야 쓰임";
    }

    function holderLines(c, name) {
      // name 을 연결해 두었는데 파일이 없던(사라짐 — R-missing) 다른 고정 크롭: 새 그림이 그 크롭에 들어간다(서버도 409 확인 where link —
      // board._missing_holders, 같은 이름 = casefold)
      const k = G.fold(name);
      return crops().filter((o) => o.id !== c.id && o.fixed && o.files.some((f) => f.missing && G.fold(f.name) === k))
        .map((o) => `크롭 ${o.name} 가 연결해 두었는데 없던 ${name} — 새 그림이 그 크롭에 들어감(그 크롭 레퍼런스가 바뀌어 다음 Run 에서 새 생성 조건일 수 있음)`);
    }

    function impactLines(c, name) {
      // 영향 줄(§4.5) — 대표 = 새 셀(유료 대상), 보조 = 셀 그대로(초안 키만 — 지금 레퍼런스가 없으면 새 셀), 함께 고정되는 같은 번호 크롭(새 이름일
      // 때 — 같은 이름이 있으면 그 파일을 쓰는 크롭은 서버 409 확인), 이름으로 직접 가리키는 곳(D5)
      const out = [];
      const same = sameNamed(c, name);
      if (role === "main") {
        const m = c.files.find((f) => f.role === "main");
        out.push(["warn", "다음 Run 에서 이 크롭은 새 생성 조건(유료 대상)"
          + (same ? ` — 같은 이름의 지금 파일 ${same.name} 은 _보류 로` : m ? ` — 지금 대표 ${m.name} 은 보조로` : "")]);
        if (same && S.R.outside_project) out.push(["warn", "refs_dir 가 프로젝트 밖 — 다른 프로젝트에도 보입니다"]);
      } else {
        out.push(auxLine(c));
        if (c.files.length + 1 > MAX_SHOWN) out.push(["info", `대표 포함 ${c.files.length + 1}장 — 초안이 보는 것은 ${MAX_SHOWN}장까지`]);
      }
      const sib = same ? null : siblingLine(c, name);
      if (sib) out.push(["info", sib]);
      for (const t of holderLines(c, name)) out.push(["warn", t]);
      const k = G.fold(name);
      for (const x of c.explicit) {
        if (G.fold(x.file) === k) out.push(["warn", `${whereText(x)} 가 ${x.file} 를 이름으로 가리킴 — 새 그림으로 바뀜`]);
      }
      return out;
    }

    async function save(again) {
      // again = explicit 409 를 확인한 처음 본문 + confirm — 응답을 기다리는 사이 바뀐 선택·컷·변환·설명으로 다시 만들지 않는다
      let body = again;
      if (!body) {
        const c = cur();
        const nm = c ? refFileName(c.name, role, role === "aux" ? desc : null) : { err: "crop" };
        const why = saveProblem(c, nm);
        if (why) {
          opts.toast(why, "err");
          return;
        }
        body = { req: result.req, crop_id: c.id, role, transform: { rotate: T.rotate, flip_h: T.flip_h, scale: T.scale } };
        if (role === "aux") body.desc = desc;
      }
      try {
        const p = S.save(body);
        renderSoon(); // busy 가 선 뒤 — 머리줄 '저장 중…'·쓰기 단추 끔
        const r = await p;
        opts.toast(`저장: ${r.file} · ${r.size[0]}×${r.size[1]} · ${sizeMb(r.bytes)}` + (r.held ? ` · 옛 파일 → _보류/${r.held}` : ""));
        if (body.role === "aux" && sel === body.crop_id) desc = ""; // 다른 크롭으로 옮겼으면 그 크롭의 설명은 그대로
      } catch (e) {
        const ex = explicitOf(e);
        if (!ex) opts.toast(e.message, "err");
        else if (window.confirm(explicitText(e, ex))) return save(Object.assign({}, body, { confirm: ex.map((x) => x.file) }));
      } finally {
        renderSoon();
      }
    }

    async function link(op, fields, ask, confirm) {
      // POST /refs/link 한 번(§4.6). ask = 먼저 띄울 확인 창(영향 줄 문구), confirm = explicit 409 뒤 확인한 이름
      const block = refsBlock();
      if (block) {
        opts.toast(block, "err");
        return;
      }
      if (ask && !window.confirm(ask)) return;
      try {
        const p = S.link(Object.assign({ op }, fields, confirm ? { confirm } : {}));
        renderSoon(); // busy 가 선 뒤 — 머리줄 '저장 중…'·쓰기 단추 끔
        const r = await p;
        // add·restore 는 비었던 연결이면 그 파일이 대표가 된다(_add_name), 대표 파일이 사라진 고정이면 대표 칸은 그대로지만 Prepare 가
        // 그것을 고를 수 있다(pick) — 응답의 크롭 행으로 알림(확인 창·단추의 '대표로'·'보조로'·영향 줄과 같은 말)
        const added = r.file || fields.name;
        const adds = (op === "add" || op === "restore") && !!r.crop;
        const asMain = adds && r.crop.files.some((f) => f.role === "main" && f.name === added);
        const picked = adds && !asMain && r.crop.pick === added;
        opts.toast((asMain ? (op === "add" ? "대표로 연결(첫 레퍼런스)" : "되살려 대표로 연결(첫 레퍼런스)")
          : picked ? `${op === "add" ? "보조로 연결" : "되살려 보조로 연결"} — 대표 파일이 없어 Prepare 가 이것을 고름(새 생성 조건)` : LINK_DONE[op])
          + (r.held ? `: _보류/${r.held}` : r.file ? `: ${r.file}` : ""));
      } catch (e) {
        const ex = explicitOf(e);
        if (!ex) opts.toast(e.message, "err");
        else if (window.confirm(explicitText(e, ex))) return link(op, fields, null, ex.map((x) => x.file));
      } finally {
        renderSoon();
      }
    }

    function outsideLine() {
      return S.R.outside_project ? "\nrefs_dir 가 프로젝트 밖 — 다른 프로젝트에도 보입니다." : "";
    }

    function dropLine(c, f) {
      // 이 크롭 연결에서 f 를 빼면(보류·연결 끊기) Prepare 선택이 어떻게 되나 → [바뀜, 문구]. 서버(_drop_name): 마지막이면 files = [](레퍼런스
      // 없음 고정 — 이름 매칭으로 돌아가지 않음), 대표를 빼면 남은 것 중 이름 규칙. Prepare = 있는 파일 중 대표, 대표 파일이 없으면 이름 규칙
      // (c.pick = 지금 고르는 파일 — 대표가 사라진 고정(R-main)에서는 보조일 수 있음)
      const left = c.files.filter((x) => x !== f);
      if (!left.length) {
        return [true, "마지막 레퍼런스 — 이 크롭은 레퍼런스 없이 고치기로 고정됩니다. 다음 Run 에서 새 생성 조건(유료 대상). "
          + "같은 번호의 새 파일은 [연결]해야 쓰입니다(이름 매칭으로 돌아가려면 고정 풀기)."];
      }
      if (!f.missing && !left.some((x) => !x.missing)) {
        return [true, `남는 연결 파일(${left.map((x) => x.name).join(", ")})이 모두 사라져 Prepare 는 레퍼런스 없이 합니다(self_restore) — `
          + "다음 Run 에서 새 생성 조건(유료 대상)."];
      }
      if (f.role === "main" && !f.missing) return [true, "대표가 없어져 Prepare 가 이름 규칙으로 고릅니다 — 다음 Run 에서 새 생성 조건일 수 있음(유료 대상)."];
      if (f.name === c.pick) {
        return [true, "대표 파일이 사라져 Prepare 가 지금 이 파일을 고릅니다 — 빼면 남은 것 중 이름 규칙으로 다시 골라 다음 Run 에서 새 생성 조건일 수 "
          + "있음(유료 대상)."];
      }
      return [false, "Prepare 선택 그대로(cell_key 그대로), Spec Draft 는 refresh 가능."];
    }

    function holdAsk(c, f) {
      return `${f.name} 을 _보류 로 옮깁니다(모든 크롭 연결에서 뺌 — 되살리기 가능).\n${dropLine(c, f)[1]}${outsideLine()}\n계속할까요?`;
    }

    function removeAsk(c, f) {
      // 연결 끊기 확인 창 — Prepare 선택이 바뀔 때만(그대로면 바로 끊음)
      const [shift, line] = dropLine(c, f);
      return shift ? `${f.name} 의 연결을 끊습니다(파일은 그대로).\n${line}\n계속할까요?` : null;
    }

    function heldNamed(c, f) {
      // 보류 파일 f 를 "_보류/<이름>" 으로 직접 가리키는 곳(스펙·매니페스트 — Prepare 는 그 경로를 그대로 읽는다)
      const k = G.fold(`${HELD_DIR}/${f.name}`);
      return c.explicit.filter((x) => G.fold(x.file) === k);
    }

    function restoreName(f) {
      // 서버가 되살릴 이름 = 보류 행 restore_as(refs.json held 기록의 원래 이름 — 접미 꼴 원래 이름도 그대로, 기록 없으면 이름 그대로).
      // 옛 서버는 그 필드가 없다 — 보류 접미를 뗀 것으로 짐작
      return "restore_as" in f ? f.restore_as : f.name.replace(HOLD_SUFFIX_RE, "");
    }

    function restoreAsk(c, f) {
      // 되살리기 확인 창(§5.7): 되살릴 이름(restoreName)·Prepare 선택(auxLine)·이 보류 파일이나 되살릴 이름을 이름으로 가리키는 곳·함께 고정되는
      // 같은 번호 크롭. 서버는 스펙·매니페스트 행을 다시 409 로 확인한다
      const back = restoreName(f);
      const k = G.fold(back);
      const lines = [`${f.name} 을 refs 로 되살려${back !== f.name ? `(이름 ${back})` : ""} ${asWhat(c)} 연결합니다.`,
        `${auxLine(c)[1]}.`,
        ...heldNamed(c, f).map((x) => `${whereText(x)} 가 ${x.file} 를 이름으로 가리킴 — 되살리면 그 타깃은 파일을 못 읽어 레퍼런스 없이(self_restore — 새 생성 조건)`),
        ...c.explicit.filter((x) => G.fold(x.file) === k).map((x) => `${whereText(x)} 가 ${x.file} 를 이름으로 가리킴 — 되살린 그림이 들어감`)];
      const sib = siblingLine(c, back);
      if (sib) lines.push(sib);
      lines.push(...holderLines(c, back));
      return lines.join("\n") + outsideLine() + "\n계속할까요?";
    }

    function unfixAsk(c) {
      // 고정 풀기 확인 창(§5.7 — 바뀌는 선택을 보임). 고정된 크롭 행의 match_files·match_pick = 고정을 풀면 쓰일 이름 매칭 결과·그 대표
      // (서버가 실을 때만 — 없으면 일반 문구)
      const head = `고정을 풀고 이름 매칭으로 돌아갑니다 — 지금 연결(${c.files.map((f) => f.name).join(", ") || "없음"}) 대신 이름 매칭 결과를 씀.\n`;
      if (!("match_pick" in c)) return `${head}대표가 바뀌면 다음 Run 에서 새 생성 조건(유료 대상). 계속할까요?`;
      const now = c.pick || "없음(레퍼런스 없이)";
      return `${head}이름 매칭 결과: ${c.match_files.join(", ") || "없음"}\n`
        + (c.match_pick === c.pick ? `Prepare 선택 그대로(대표 ${now} — cell_key 그대로), Spec Draft 는 refresh 가능.`
          : `대표 ${now} → ${c.match_pick || "없음(레퍼런스 없이)"} — 다음 Run 에서 이 크롭은 새 생성 조건(유료 대상).`) + "\n계속할까요?";
    }

    async function reopen(f) {
      // S2 기록(시트·영역·컷·변환)으로 다시 열기(§4.6). 저장한 컷 = 기록 steps 의 마지막 요청(다듬기 연쇄의 끝 — 없으면 mask 경로의 요청 ID)을
      // GET refs/cut 으로 되살려 곧바로 변환·저장·이어 다듬기. 방법·점·텍스트·체크포인트는 연쇄의 첫 컷 것. 시트가 그때와 다르면(서버
      // record.sheet.changed — 지문이 다르거나 없음) 서버가 그 컷을 받지 않으므로 되살리지 않고 경고(다시 컷) — 시트 행은 목록을 다시 읽어(새 미리보기)
      // 되살리지 못하면(시트가 바뀜·결과 없음·실패) Enter = 기록대로 다시 컷(step_polys — 다각형 방법은 그리던 다각형이 없어 Enter 가 아무것도 못 하고,
      // 다른 방법은 첫 컷만 다시 해 다듬기 다각형이 빠짐). sheet.changed 는 GET /refs 때 값 — 모드를 연 채 포토샵이 시트를 저장해도 state 는
      // 그대로라 낡으므로 먼저 다시 읽은 행의 기록으로(codex9). 기다리는 사이 더 늦은 열기·새 영역·컷(세대)·다른 크롭 선택이 있으면 그만둠(codex11)
      const seq = ++opens;
      const owner = sel;
      const at0 = epoch;
      const stale = () => seq !== opens || at0 !== epoch || sel !== owner;
      await S.load(true);
      if (stale()) return;
      const c = owner ? crops().find((x) => x.id === owner) : null;
      const rec = ((c && c.files.find((x) => x.name === f.name && x.record)) || f).record;
      const cut = rec.cut;
      const steps = Array.isArray(cut.step_polys) && cut.step_polys.length ? cut.step_polys : null;
      const again = steps ? "Enter = 기록대로 다시 컷" : "다시 컷";
      const arm = () => {
        replay = steps ? { at: epoch, rect: rec.rect.slice(), method: cut.method, steps } : null;
      };
      const it = await freshSheet(rec.sheet.path);
      if (stale()) return;
      if (rec.sheet.changed) opts.toast(`시트가 그때와 다릅니다(바뀌었거나 없음) — 영역을 확인하고 ${again}`, "warn");
      else if (!it) opts.toast(`기록의 시트가 목록에 없습니다: ${rec.sheet.path}`, "warn");
      openSheet(it || { path: rec.sheet.path, size: rec.sheet.size, kind: /\.ps[db]$/i.test(rec.sheet.path) ? "psd" : "image" });
      rect = rec.rect.slice();
      method = cut.method;
      points = (cut.points || []).map((p) => p.slice());
      text = cut.text || "";
      grow = cut.grow || 0;
      feather = cut.feather || 0;
      ckpt = cut.ckpt && cut.ckpt !== ckpts()[0] && ckpts().includes(cut.ckpt) ? cut.ckpt : null; // 목록에 없는 체크포인트는 보내면 400
      T = { rotate: rec.transform.rotate, flip_h: rec.transform.flip_h, scale: rec.transform.scale };
      const req = rec.sheet.changed ? null : (cut.steps || []).at(-1) || (/[0-9a-f]{32}/.exec(cut.mask || "") || [])[0];
      if (!req) {
        arm();
        render();
        return;
      }
      const at = epoch;
      let got = null;
      inflight += 1;
      render();
      try {
        const r = await S.cutResult(req);
        if (at !== epoch || !sameRect(rect, rec.rect) || result) return; // 기다리는 사이 시트·영역을 바꿨거나 새 컷이 끝남
        if (!r) {
          arm();
          opts.toast(`기록의 컷을 되살리지 못했습니다(끝나지 않았거나 실패한 컷) — ${again}`, "warn");
          return;
        }
        result = Object.assign({ rect: rec.rect.slice(), method: cut.method, grow: cut.grow || 0, feather: cut.feather || 0 }, r);
        got = result;
        loadCutImg();
      } catch (e) {
        if (at === epoch) {
          arm();
          opts.toast(`기록의 컷을 되살리지 못했습니다 — ${again}: ${e.message}`, "warn");
        }
      } finally {
        inflight -= 1;
        renderSoon();
        catchUp(got); // 응답을 기다리는 사이 바꾼 grow·feather · 그사이 끝난 컷에 이을 다각형
      }
    }

    // ── 시트 ──
    async function loadSheets() {
      sheetList = null;
      renderSheets();
      try {
        sheetList = await S.sheets();
      } catch (e) {
        sheetList = { error: e.message };
      }
      renderSheets();
    }

    async function freshSheet(path) {
      // 목록을 다시 읽은 path 의 행(못 읽으면 가진 목록에서, 없으면 null). 목록은 들어올 때 한 번 읽어 두므로 그 뒤 바깥에서 저장된 시트를 옛 행으로
      // 열면 옛 서명(v)의 미리보기(브라우저가 오래 캐시)·크기를 보이는데 컷은 지금 파일을 읽어 다른 부분을 오린다. 새 v = 새 미리보기 URL
      try {
        sheetList = await S.sheets();
      } catch (e) {
        // 서버에 닿지 않음 — 가진 목록으로(미리보기·컷도 실패함)
      }
      return (sheetList && !sheetList.error ? sheetList : []).find((x) => x.path === path) || null;
    }

    async function pickSheet(path) {
      const seq = ++opens;
      const it = await freshSheet(path);
      if (seq !== opens) return;
      if (it) {
        openSheet(it);
      } else {
        opts.toast(`시트가 목록에서 사라졌습니다: ${path}`, "warn");
        renderSheets();
      }
    }

    async function reloadSheet(sh) {
      // 연 시트 sh 가 바깥(포토샵)에서 저장됨(컷 409 sheet_changed · GET /refs 최근 시트 행의 v 가 다름): 다시 읽은 행(새 v 미리보기·크기)으로
      // 다시 연다 — 결과·대기열은 버리고(새 세대) 영역·점은 새 시트 안이면 남겨 새 그림에서 영역을 확인하고 다시 컷. 기다리는 사이 더 늦은
      // 열기·다른 시트·이미 다시 연 시트면 그만둠, 서명이 그대로면(늦게 온 GET /refs) 아무것도 하지 않음
      const seq = opens;
      const it = await freshSheet(sh.path);
      if (seq !== opens || sheet !== sh || (it && it.v === sh.v)) return;
      if (!it) {
        closeSheet();
        opts.toast(`시트가 목록에서 사라졌습니다: ${sh.path}`, "warn");
        return;
      }
      const keep = rect && rect[2] <= it.size[0] && rect[3] <= it.size[1] ? [rect, points] : null;
      openSheet(it, true);
      if (keep) {
        [rect, points] = keep;
        render();
      }
      opts.toast("시트가 바깥에서 바뀌어 다시 열었습니다 — 새 그림에서 영역을 확인하고 다시 컷", "warn");
    }

    function openSheet(it, auto = false) {
      // auto = 바깥 저장으로 다시 엶(reloadSheet) — 시트 탭으로 바꾸되 기억하지 않음
      const sh = { path: it.path, size: it.size, kind: it.kind, v: it.v };
      sheet = sh;
      rect = null;
      points = [];
      poly = [];
      dropResult();
      setTab("sheets", !auto); // 뷰어 칸을 먼저 보인다(숨은 칸에서는 맞춤 크기가 0) — 그리드 탭에서 [다시 열기]·바깥 저장으로 다시 열어도 시트 탭으로
      const rec = S.R ? S.R.sheets.find((x) => x.path === it.path) : null;
      viewer.setSheet({ size: it.size, kind: it.kind, preview_url: S.sheetUrl(sh, "preview"),
        region: (r, max) => S.sheetUrl(sh, "region", r, max), view: rec ? rec.view : null });
      render();
    }

    function closeSheet() {
      sheet = null;
      rect = null;
      poly = [];
      points = [];
      dropResult();
      viewer.setSheet(null);
      renderSheets();
      if (sheetList === null || sheetList.error) loadSheets();
      render();
    }

    function setTab(t, keep = true) {
      // 가운데 탭: 시트를 연 채 그리드로 갔다 와도 시트·영역·컷은 그대로(숨기기만). 바뀌면 board.js 에 알림(단축키 칩 줄 — keep = 이 브라우저에
      // 기억, 바깥 저장으로 다시 열 때처럼 사용자가 고르지 않은 전환은 기억하지 않음)
      if (t !== tab) {
        tab = t;
        opts.tabChanged(t, keep);
      }
      renderSheets();
    }

    function renderSheets() {
      // 가운데 칸: 탭 줄 · [그리드] 면 그리드만 · [시트] 면 시트 목록(시트를 열면 뷰어)
      const g = tab === "grid";
      for (const b of tabBtns) {
        b.classList.toggle("on", b.dataset.tab === tab);
        b.setAttribute("aria-selected", String(b.dataset.tab === tab));
      }
      gridHost.hidden = !g;
      sheetHost.hidden = g || !!sheet;
      viewHost.hidden = g || !sheet;
      sheetHost.replaceChildren();
      if (sheet || g) return;
      const top = el("div", "rs-head");
      top.append(el("h3", "", "시트 열기"), button("새로고침", "시트 목록 다시 읽기", loadSheets));
      sheetHost.append(top, el("p", "dim", "프로젝트 폴더 안의 png·jpg·webp·psd·psb(파이프라인 폴더·refs 제외, 짧은 변 ≥ 256) — PSD 는 보이는 레이어 합성"));
      if (sheetList === null) {
        sheetHost.append(el("p", "dim", "불러오는 중…"));
        return;
      }
      if (sheetList.error) {
        sheetHost.append(el("p", "warn", `시트 목록을 읽지 못했습니다: ${sheetList.error}`));
        return;
      }
      if (!sheetList.length) {
        sheetHost.append(el("p", "warn", "시트가 없습니다 — 받은 시트를 <프로젝트>\\00_원본\\ 에 두고 새로고침(매뉴얼)"));
        return;
      }
      const recent = new Set(S.R ? S.R.sheets.map((x) => x.path) : []);
      const ol = el("ol", "rs-list");
      for (const it of sheetList.slice().sort((a, b) => Number(recent.has(b.path)) - Number(recent.has(a.path)))) {
        const li = el("li", "");
        li.append(el("div", "rs-path", it.path));
        const meta = el("div", "chips");
        meta.append(chip(`${it.size[0]}×${it.size[1]}`), chip(it.kind === "psd" ? "PSD" : "이미지"), chip(sizeMb(it.bytes), "b-dim"));
        if (recent.has(it.path)) meta.append(chip("최근", "b-reg"));
        li.append(meta);
        li.addEventListener("click", () => pickSheet(it.path));
        ol.append(li);
      }
      sheetHost.append(ol);
    }

    // ── 선택 ──
    function select(id) {
      if (id !== sel) {
        desc = "";
        text = ""; // SAM3 텍스트는 그 부품의 것 — 다른 크롭에 남으면 엉뚱한 부위를 잡음(10/21 실사용: 002 'Bangle' 이 003 부츠에, U-4)
        overlay = null;
        Object.assign(cutCanvas.style, { left: "", top: "", opacity: "", width: "", height: "" });
      }
      sel = id;
      render();
    }

    function syncSel() {
      // 뷰어의 선택 = 지금 영역(F = 영역 맞춤, 1:1·2:1 = 영역 가운데 기준)
      if (on && sheet) viewer.setSelection(rect ? { crop: { id: "ref-sel", rect } } : null, false);
    }

    function escape() {
      // Esc 차례: 그리기 취소(끌기·다각형) → 영역·점·결과 해제 → 크롭 선택 해제 → 모드 나가기. 그리드 탭은 숨은 시트의 다각형·영역·점·컷 결과를
      // 건너뜀(그대로 — [시트]로 돌아와 이어서)
      if (drag) {
        cancel();
        return;
      }
      const g = tab === "grid";
      if (!g && poly.length) poly = [];
      else if (!g && (rect || points.length || result)) {
        rect = null;
        points = [];
        dropResult();
      } else if (sel) sel = null;
      else {
        opts.exit();
        return;
      }
      render();
    }

    function onKey(e) {
      // board.js 가 레퍼런스 모드에서 먼저 넘긴다(입력칸 포커스는 board.js 가 이미 거름). true = 소비
      if (!on) return false;
      const mod = e.ctrlKey || e.metaKey;
      switch (e.code) {
        case "Escape":
          escape();
          return true;
        case "Enter":
          // 그리드 탭: 시트 뷰어가 숨음 — 그 영역·점·컷 결과·화면 키(Enter·Backspace·[ ]·H·0·1·2·F)는 삼키기만(숨은 시트를 컷·회전하지 않고
          // 검토 키로도 넘기지 않음)
          if (e.repeat || tab === "grid") return true;
          if (poly.length) closePoly(e);
          else doCut();
          return true;
        case "Backspace":
          if (tab === "grid") return true;
          if (poly.length) poly.pop();
          else if (method === "sam3") points.pop();
          render();
          return true;
        case "BracketLeft":
        case "BracketRight":
          if (result && tab !== "grid") rotateBy((e.code === "BracketLeft" ? -1 : 1) * (mod ? 90 : e.shiftKey ? 5 : 0.5));
          return true;
        case "KeyH":
          if (mod) return false;
          if (!e.repeat && result && tab !== "grid") {
            setT({ flip_h: !T.flip_h });
            render();
          }
          return true;
        case "Digit0":
        case "Digit1":
        case "Digit2":
        case "KeyF":
          if (mod) return false;
          if (tab === "grid") return true;
          if (e.code === "Digit0") viewer.fitCanvas();
          else if (e.code === "KeyF") viewer.fitCrop();
          else viewer.zoomTo(e.code === "Digit1" ? 1 : 2);
          return true;
        default:
          return false;
      }
    }

    // ── 머리줄·목록·패널 ──
    function render() {
      if (!on) return;
      renderHead();
      renderList();
      renderGrid();
      renderPanel();
      syncSel(); // 뷰어 선택을 넘기며 다시 그린다
    }

    function renderSoon() {
      // 비동기 응답 뒤: 패널 입력칸에서 입력·한글 조합 중이면 패널은 입력이 끝난 뒤(flushPanel) 그린다
      if (!on) return;
      if (!typingInPanel()) {
        render();
        return;
      }
      pendingPanel = true;
      renderHead();
      renderList();
      renderGrid();
      viewer.redraw();
    }

    function flushPanel() {
      if (on && pendingPanel && !typingInPanel()) renderPanel();
    }

    panel.addEventListener("compositionstart", () => {
      composing = true;
    });
    panel.addEventListener("compositionend", () => {
      composing = false;
      flushPanel();
    });
    panel.addEventListener("focusout", () => setTimeout(flushPanel, 0));

    function applyQueue() {
      if (S.busy) {
        opts.toast("저장 중입니다 — 끝난 뒤 반영하세요", "warn");
        return;
      }
      const n = S.R ? S.R.pending_rerolls || 0 : 0;
      const lines = (opts.applyWarn ? opts.applyWarn() : [])
        .concat("반영 (무료): 무료 반영(Import → Prepare → Analyze → Review 템플릿, 탭 불필요)으로 레퍼런스 연결을 프로젝트 기록에 반영합니다.");
      if (n) lines.push(`다시 생성 대기 ${n}건(사전 승인)은 이 반영에서 돌지 않음 — R·[다시 생성 보내기]·그래프 Run 에서 과금`);
      if (!window.confirm(lines.concat(APPLY_NOTE, "계속할까요?").join("\n"))) return;
      opts.queue(true);
    }

    function renderHead() {
      head.replaceChildren();
      const R = S.R;
      head.append(el("span", "lh-title", R && R.rev !== null ? `레퍼런스 rev ${R.rev}` : "레퍼런스")); // 깨진 refs.json = exists·rev null
      if (R) {
        const k = R.crops.filter((c) => c.status !== "same").length;
        const st = el("span", k ? "lh-wait" : "lh-ok", k ? `미반영 ${k}` : "반영됨");
        st.title = k ? "프로젝트 기록 refs_auto·refs_main 이 지금 연결과 다름 — 다음 큐의 Import 에서 반영" : "프로젝트 기록이 지금 연결과 같음";
        head.append(st, button("반영 (무료)", "무료 반영(Import → Prepare → Analyze → Review 템플릿, 탭 불필요) — 연결을 프로젝트 기록에 반영", applyQueue));
        if (R.doc_dir_match === false) {
          const b = el("span", "lh-bad", "연결 무시 중");
          b.title = "refs.json 의 refs_dir 가 지금 Import 의 refs_dir 와 다름(R-dir) — 이름 매칭으로 동작";
          head.append(b);
        }
        if (R.error) {
          const b = el("span", "lh-bad", "refs.json 오류");
          b.title = `${R.error} — refs_history/r<rev>.json 으로 되돌릴 수 있음`;
          head.append(b);
        }
      }
      if (S.busy) head.append(el("span", "lh-wait", "저장 중…"));
      else if (inflight) head.append(el("span", "lh-wait", S.job ? jobText(S.job) : "컷 중…"));
      if (S.err && R) head.append(el("span", "lh-bad", `읽기 오류: ${S.err}`));
    }

    function jobLine() {
      // 패널의 컷 대기 줄(live.job·live.cancel) — 패널을 다시 만들지 않고 고친다
      if (!live.job) return;
      const j = S.job;
      live.job.textContent = j ? jobText(j) : "컷 중…";
      live.cancel.hidden = !j;
      live.cancel.disabled = !j || !j.pid || j.running;
    }

    function showJob() {
      // SAM3 대기 폴링마다: 머리줄과 대기 줄만
      if (!on) return;
      renderHead();
      jobLine();
    }

    function jobText(j) {
      if (!j.pid) return "SAM3: ComfyUI 큐에 넣는 중…";
      if (j.running) return "SAM3: ComfyUI 실행 중";
      return j.ahead === null ? "SAM3: ComfyUI 대기 중" : `SAM3: ComfyUI 실행 중 — 앞에 ${j.ahead}개`;
    }

    function refChips(c) {
      // 크롭 칩 — 왼쪽 목록과 그리드 카드 머리가 같이 씀: 대표(없음·고정) · 보조 n · 고정(ID)/이름 매칭 · 미반영 · 경고 n
      const main = c.files.find((f) => f.role === "main");
      const aux = c.files.length - (main ? 1 : 0);
      const out = [];
      if (c.fixed && !c.files.length) out.push(c.auto ? chip("없음(자동 고정)", "b-wait") : chip("없음(고정)", "b-dim"));
      else out.push(chip(main ? "대표" : "대표 없음", main ? "b-star" : "b-wait"));
      if (aux) out.push(chip(`보조 ${aux}`));
      out.push(chip(c.fixed ? "고정(ID)" : "이름 매칭", c.fixed ? "b-reg" : "b-dim"));
      if (c.status !== "same") out.push(chip("미반영", "b-wait")); // pending · new(매니페스트에 아직 없는 레이아웃 크롭)
      const warns = c.checks.filter((x) => x.level === "warn");
      if (warns.length) {
        const w = chip(`경고 ${warns.length}`, "b-bad");
        w.title = warns.map((x) => x.msg).join("\n");
        out.push(w);
      }
      return out;
    }

    function orphanChips(o) {
      return [chip("크롭 없는 연결", "b-dim"), chip(`파일 ${o.files.length}`, "b-dim")];
    }

    function renderList() {
      list.replaceChildren();
      const R = S.R;
      if (!R) return;
      for (const c of R.crops.slice().sort(byName)) {
        const li = el("li", "rcrop" + (c.id === sel ? " sel" : ""));
        li.append(el("div", "tname", c.name));
        const ch = el("div", "chips");
        ch.append(...refChips(c));
        li.append(ch);
        li.addEventListener("click", () => select(c.id));
        list.append(li);
      }
      for (const o of R.orphans) {
        const li = el("li", "rcrop gone" + (sel === `orphan:${o.id}` ? " sel" : ""));
        li.append(el("div", "tname", o.name || o.id));
        li.title = `옛 크롭 ID ${o.id}`;
        const ch = el("div", "chips");
        ch.append(...orphanChips(o));
        li.append(ch);
        li.addEventListener("click", () => select(`orphan:${o.id}`));
        list.append(li);
      }
      // 선택이 바뀐 때만 보이게 — 다시 그릴 때마다 부르면 사용자가 내린 목록이 선택으로 튄다
      const s = list.querySelector("li.sel");
      if (s && sel !== scrolledTo) s.scrollIntoView({ block: "nearest" });
      scrolledTo = sel;
    }

    // ── 그리드(3-4 — F22): 매칭이 끝난 레퍼런스와 고칠 크롭을 한눈에. 카드 = 고르기만(편집은 오른쪽 패널) ──
    function renderGrid() {
      // 카드 = 왼쪽 목록 순서, 고아 연결은 맨 뒤 묶음. R·state 가 그대로면 카드는 두고 선택 표시만(크롭이 많아도 다시 그리기가 가볍게)
      if (tab !== "grid") return;
      const R = S.R;
      if (!R || !gridOf || gridOf[0] !== R || gridOf[1] !== state) {
        gridOf = [R, state];
        gridCards.clear();
        if (!R) {
          gridHost.replaceChildren(el("p", "dim", refsBlock()));
        } else if (!R.crops.length && !R.orphans.length) {
          gridHost.replaceChildren(el("p", "dim", "크롭이 없습니다(Import 전) — [시트] 에서 시트 보기·컷만 됩니다"));
        } else {
          const cards = el("div", "rg-cards");
          cards.append(...R.crops.slice().sort(byName).map(cropCard));
          if (R.orphans.length) cards.append(el("div", "rg-group", `크롭 없는 연결 ${R.orphans.length}`), ...R.orphans.map(orphanCard));
          gridHost.replaceChildren(cards);
        }
      }
      for (const [id, card] of gridCards) card.classList.toggle("sel", id === sel);
      const s = gridCards.get(sel);
      if (s && sel !== gridScrolledTo) s.scrollIntoView({ block: "nearest" });
      gridScrolledTo = sel;
    }

    function cropCard(c) {
      // 머리 = 이름 + 목록과 같은 칩, 몸 = 크롭 소스 · 대표 · 보조 n, 아래 = 검사 줄. 테두리: 경고·대표 없음(자동 고정 포함) = 빨강, 연결 안 된 파일 = 주황.
      // 이름 매칭은 강조하지 않음(목록 칩처럼 흐림 — 대표가 있으면 Prepare 가 그대로 씀)
      const main = c.files.find((f) => f.role === "main");
      const aux = c.files.filter((f) => f !== main);
      const noneFixed = c.fixed && !c.files.length && !c.auto; // 의도한 '레퍼런스 없이 고치기' — 대표 없음이 아님
      const bad = c.checks.some((x) => x.level === "warn") || (!main && !noneFixed);
      const card = el("div", "rg-card" + (bad ? " bad" : c.unlinked.length ? " warn" : ""));
      const top = el("div", "rg-head");
      const ch = el("div", "chips");
      ch.append(...refChips(c));
      top.append(el("div", "tname", c.name), ch);
      const body = el("div", "rg-body");
      body.append(srcFig(c), mainFig(main, noneFixed), auxFig(aux));
      card.append(top, body);
      if (c.checks.length) card.append(checkLines(c.checks));
      card.addEventListener("click", () => select(c.id));
      gridCards.set(c.id, card);
      return card;
    }

    function orphanCard(o) {
      const id = `orphan:${o.id}`;
      const card = el("div", "rg-card gone");
      card.title = `옛 크롭 ID ${o.id}`;
      const top = el("div", "rg-head");
      const ch = el("div", "chips");
      ch.append(...orphanChips(o));
      top.append(el("div", "tname", o.name || o.id), ch);
      card.append(top, el("div", "rg-files dim", o.files.join(" · ")));
      card.addEventListener("click", () => select(id));
      gridCards.set(id, card);
      return card;
    }

    function gridFig(label, kids, cls) {
      const f = el("figure", "rg-fig");
      const fr = el("div", "rg-frame" + (cls ? ` ${cls}` : ""));
      fr.append(...kids);
      f.append(el("figcaption", "", label), fr);
      return f;
    }

    function gridImg(rel, title) {
      const img = el("img", "rg-img");
      img.alt = "";
      img.loading = "lazy";
      img.decoding = "async";
      img.draggable = false;
      img.src = opts.apiUrl(rel);
      if (title) img.title = title;
      return img;
    }

    function noPic(txt, cls, title) {
      const d = el("div", "rg-none" + (cls ? ` ${cls}` : ""), txt);
      if (title) d.title = title;
      return d;
    }

    function srcFig(c) {
      // 크롭 소스 = 그 크롭 첫 타깃의 Before — 크롭 source(PSD 선편집 크롭이미지·01.크롭 이 합성된 생성 입력)라 캔버스 미리보기(base)로 대신하지
      // 않는다(Codex 3b 1차). Analyze 뒤 Before(512)는 타깃 초점 박스(자동 마스크 둘레 + 10%)로 잘려 있어 초점 박스가 크롭 전체일 때만(옛 서버 —
      // focus_box 없음 — 도) 512, 아니면 원해상도 Before(초점 자르기 없음). 타깃이 없으면(레이아웃에만 있는 새 크롭·대상 없는 크롭), 또 레이아웃에서
      // 옮겼고 초점 Before 뿐이면 캔버스 미리보기를 rect 로 잘라 보임(저해상 — 새 자리). Before 는 마지막 반영 자리 — 옮기고 반영 전이면 '미반영 (옛 자리)'
      const t = state.targets.find((x) => x.crop_id === c.id);
      const sc = state.crops.find((x) => x.id === c.id);
      const added = (state.layout_added || []).find((x) => x.id === c.id); // 레이아웃이 없으면 null
      const moved = sc && sc.layout_diff && sc.layout_diff.fields.includes("rect") ? sc.layout_diff.rect : null;
      const rect = added ? added.rect : moved || (sc ? sc.rect : null);
      const fb = t && t.focus_box;
      const whole = t && (!fb || (fb[0] === 0 && fb[1] === 0 && fb[2] === t.size[0] && fb[3] === t.size[1]));
      const cut = state.canvas && rect && (!t || (moved && !whole));
      const pic = cut ? canvasCut(state.canvas, rect)
        : t ? gridImg(whole ? t.before_url : t.before_full_url || t.before_url,
          whole ? "" : `첫 타깃 ${t.tid} 의 Before 원해상도 — 크롭 전체(512 는 초점 박스로 잘림)`)
          : noPic("그림 없음", "", "타깃 Before 도 캔버스 미리보기도 없음");
      const label = "크롭 소스";
      if (!added && !moved) return gridFig(label, [pic]);
      const old = t && !cut; // 그림 = 타깃 Before(마지막 반영 자리)
      const tag = el("span", "rg-tag", old ? "미반영 (옛 자리)" : added ? "미반영 (새 크롭)" : "미반영 (새 자리)");
      tag.title = old ? "레이아웃에서 옮긴 크롭 — 그림은 마지막 반영 자리, [반영 (무료)] 뒤 새 자리"
        : `${added ? "레이아웃에만 있는 새 크롭" : "레이아웃에서 옮긴 크롭"} — 반영 전이라 캔버스 미리보기를 잘라 보임(저해상)`;
      return gridFig(label, [pic, tag]);
    }

    function canvasCut(cv, r) {
      // 캔버스 미리보기(그림 전체)를 rect 만 보이게 배경으로: 칸 비율 = rect, 배경 크기 = 캔버스/rect 배, 위치 = rect 시작/(캔버스 − rect)
      // (배경 % 위치의 뜻 — rect 가 캔버스 끝에 닿으면 0·100%). 너비 = 칸 높이(--rg-h)에 맞춘 비율, 칸이 좁으면 칸 너비
      const [W, H] = cv.size;
      const w = r[2] - r[0];
      const h = r[3] - r[1];
      const d = el("div", "rg-cut");
      d.title = "캔버스 미리보기를 잘라 보임(저해상)";
      Object.assign(d.style, { backgroundImage: `url("${opts.apiUrl(cv.base_url)}")`, backgroundSize: `${(100 * W) / w}% ${(100 * H) / h}%`,
        backgroundPosition: `${W > w ? (100 * r[0]) / (W - w) : 0}% ${H > h ? (100 * r[1]) / (H - h) : 0}%`,
        aspectRatio: `${w} / ${h}`, width: `min(100%, calc(var(--rg-h) * ${w / h}))` });
      return d;
    }

    function mainFig(main, noneFixed) {
      // 대표 = GPT 에 보내는 1장 — 없으면 빨간 칸(의도한 '레퍼런스 없이' 고정은 흐린 칸), 사라짐·그림 아님은 이름만
      const pic = main && main.thumb_url ? gridImg(main.thumb_url, main.name)
        : main ? noPic(main.missing ? "사라짐" : "미리보기 없음", "bad", main.name)
          : noneFixed ? noPic("레퍼런스 없이(고정)") : noPic("대표 없음", "bad");
      return gridFig("대표", [pic], !main && !noneFixed ? "miss" : "");
    }

    function auxFig(aux) {
      // 보조 = AI 초안만 봄 — 작은 그림 2×2(1·2장이면 칸을 나눠 크게, c1·c2), 넘치면 마지막 칸이 +k
      if (!aux.length) return gridFig("보조 0", [noPic("없음")]);
      const n = aux.length > AUX_CELLS ? AUX_CELLS - 1 : aux.length;
      const box = el("div", `rg-aux c${Math.min(aux.length, AUX_CELLS)}`);
      box.append(...aux.slice(0, n).map((f) => (f.thumb_url ? gridImg(f.thumb_url, f.name) : noPic(f.missing ? "사라짐" : "미리보기 없음", "", f.name))));
      if (n < aux.length) box.append(el("div", "rg-more", `+${aux.length - n}`));
      return gridFig(`보조 ${aux.length}`, [box]);
    }

    function checkLines(checks) {
      // 검사 줄: 경고(강조) 먼저 · 정보(흐리게), 앞 CHECK_LINES 줄 + '…'(줄마다 넘치면 말줄임) — 전체는 마우스 설명
      const all = checks.filter((x) => x.level === "warn").concat(checks.filter((x) => x.level !== "warn"));
      const box = el("div", "rg-checks");
      box.title = all.map((x) => x.msg).join("\n");
      box.append(...all.slice(0, CHECK_LINES).map((x) => el("div", x.level === "warn" ? "warn" : "dim", x.msg)));
      if (all.length > CHECK_LINES) box.append(el("div", "dim", `… 외 ${all.length - CHECK_LINES}`));
      return box;
    }

    function renderPanel() {
      const a = document.activeElement;
      const fk = panel.contains(a) && textField(a) ? a.dataset.k : null;
      pendingPanel = false;
      panel.replaceChildren();
      live = {};
      const R = S.R;
      if (!R) {
        panel.append(el("p", "lp-msg", refsBlock()));
        return;
      }
      const block = refsBlock();
      if (block) dirBlock(block);
      const o = curOrphan();
      const c = cur();
      if (o) orphanBlock(o);
      else if (c) manageBlock(c);
      else {
        panel.append(el("p", "lp-msg", R.crops.length ? "왼쪽에서 크롭을 고르세요 — 시트 보기·컷은 크롭 없이도 됩니다"
          : "크롭이 없습니다(Import 전) — 시트 보기·컷만 됩니다"));
      }
      cutBlock(c);
      const f = fk && panel.querySelector(`[data-k="${fk}"]`);
      if (f) f.focus();
    }

    function dirBlock(msg) {
      panel.append(el("p", "warn", msg));
      const R = S.R;
      if (R.refs_dir || !R.suggest_dir) return;
      const path = R.suggest_dir; // <project>/refs — Import PSD 의 refs_dir 에 넣을 값
      const r = row("경로", path, "dim");
      r.append(button("복사", "refs_dir 에 넣을 경로를 클립보드로", () => {
        navigator.clipboard.writeText(path).then(() => opts.toast("경로를 복사했습니다"), (e) => opts.toast(`복사하지 못했습니다: ${e.message}`, "err"));
      }, "inline"));
    }

    function fileRow(f, tags, actions) {
      const r = el("div", "rf-file");
      const img = el("img", "rf-thumb");
      img.alt = "";
      img.loading = "lazy";
      const u = f.thumb_url ? opts.apiUrl(f.thumb_url) : null;
      if (u) img.src = u;
      else img.hidden = true;
      const info = el("div", "rf-info");
      info.append(el("div", "rf-name", f.name));
      const meta = [tags, f.missing ? "사라짐" : null, f.size ? `${f.size[0]}×${f.size[1]}` : null, f.bytes ? sizeMb(f.bytes) : null,
        f.origin === "studio" ? "S2 기록" : f.origin === "ps" ? "PS" : null, f.outside_edit ? "바깥에서 바뀜(기록과 픽셀이 다름)" : null]
        .filter(Boolean).join(" · ");
      if (meta) info.append(el("div", "dim", meta));
      const acts = el("div", "rf-acts");
      const off = !!refsBlock() || S.busy;
      for (const [label, title, fn, write] of actions) {
        const b = button(label, title, fn, "inline");
        if (write && off) b.disabled = true;
        acts.append(b);
      }
      info.append(acts);
      r.append(img, info);
      panel.append(r);
    }

    function manageBlock(c) {
      const title = el("h3", "lp-title", c.name);
      title.title = `ID ${c.id} — 연결은 크롭 ID 로(이름을 바꿔도 유지)`;
      panel.append(title);
      const files = c.files;
      const off = !!refsBlock() || S.busy;
      const noneFixed = c.fixed && !files.length && !c.auto; // 의도한 '없음' 고정
      if (!c.fixed) {
        const r = row("이름 매칭 중", `지금 결과: ${files.length ? files.map((f) => f.name).join(", ") : "없음"}`, "warn");
        const fx = button("고정", "지금 결과 그대로 크롭 ID 에 고정(선택·생성 조건 그대로)", () => link("fix", { crop_id: c.id }), "inline");
        fx.disabled = off;
        r.append(fx);
      }
      const main = files.find((f) => f.role === "main");
      panel.append(el("div", "lp-k", "대표 ★ (GPT 에 보내는 1장)"));
      if (main) {
        fileRow(main, "★ 대표", [
          ...(main.record ? [["다시 열기", "S2 기록(시트·영역·컷·변환)으로 다시 열기", () => reopen(main), false]] : []),
          ["보류로", "_보류 로 옮김(모든 크롭 연결에서 뺌)", () => link("hold", { crop_id: c.id, name: main.name }, holdAsk(c, main)), true]]);
      } else {
        // c.auto = 다른 크롭의 저장·보류나 이름 바꾸기가 함께 고정한 연결(board._auto_fixed) — 빈 files 여도 이 크롭이 고른 '없음'이 아님
        panel.append(el("p", noneFixed ? "dim" : "warn", noneFixed ? "레퍼런스 없이 고치기로 고정"
          : c.fixed && !files.length ? "레퍼런스 없음 — 다른 크롭의 동작이 함께 '없음'으로 고정함(의도한 '레퍼런스 없이 고치기' 아님). 같은 번호 파일은 [연결]해야 쓰임"
            : files.length ? "대표 파일 없음 — Prepare 가 이름 규칙으로 고름(R-main)" : "레퍼런스 없음(R-none)"));
      }
      const aux = files.filter((f) => f !== main);
      if (aux.length) panel.append(el("div", "lp-k", "보조 (AI 초안만 봄)"));
      // ↑↓ = 보이는 보조 목록의 이웃과 자리 바꿈(to = 그 이웃의 files 색인 — 대표는 files 어디에 있어도 건너뜀)
      aux.forEach((f, k) => {
        fileRow(f, null, [
          ["대표로", "이 파일을 대표로(맨 앞)", () => link("main", { crop_id: c.id, name: f.name },
            `대표를 ${f.name} 으로 바꿉니다 — 다음 Run 에서 이 크롭은 새 생성 조건(유료 대상).\n계속할까요?`), true],
          ...(k > 0 ? [["↑", "앞으로", () => link("move", { crop_id: c.id, name: f.name, to: files.indexOf(aux[k - 1]) }), true]] : []),
          ...(k < aux.length - 1 ? [["↓", "뒤로", () => link("move", { crop_id: c.id, name: f.name, to: files.indexOf(aux[k + 1]) }), true]] : []),
          ["연결 끊기", "이 크롭 연결에서만 뺌(파일은 그대로)", () => link("remove", { crop_id: c.id, name: f.name }, removeAsk(c, f)), true],
          ["보류로", "_보류 로 옮김(모든 크롭 연결에서 뺌)", () => link("hold", { crop_id: c.id, name: f.name }, holdAsk(c, f)), true],
          ...(f.record ? [["다시 열기", "S2 기록으로 다시 열기", () => reopen(f), false]] : [])]);
      });
      if (files.length > MAX_SHOWN) panel.append(el("p", "dim", `대표 포함 ${files.length}장 — 초안이 보는 것은 ${MAX_SHOWN}장까지`));
      // 연결·되살리기 = 보조로 더함. 지금 레퍼런스가 없거나 대표가 사라졌으면 Prepare 선택이 바뀔 수 있어 확인 창(영향 줄 문구)
      const shift = auxShift(c);
      const addAsk = (what) => (shift ? `${what}\n${auxLine(c)[1]}.\n계속할까요?` : null);
      const as = asWhat(c);
      if (c.unlinked.length) panel.append(el("div", "lp-k", "연결 안 된 파일 (같은 번호 — refs 폴더)"));
      for (const f of c.unlinked) {
        fileRow(f, null, [["연결", `${as} 연결`, () => link("add", { crop_id: c.id, name: f.name }, addAsk(`${f.name} 을 ${as} 연결합니다.`)), true]]);
      }
      // 보류는 이름 매칭·연결이 보지 않는다 — 단 스펙·매니페스트가 "_보류/<이름>" 으로 가리키면 Prepare 가 그 파일을 읽는다
      if (c.held.length) {
        panel.append(el("div", "lp-k", c.held.some((f) => heldNamed(c, f).length)
          ? "보류 (_보류 — 이름 매칭·연결은 보지 않음, 스펙·프로젝트 기록이 이름으로 가리키는 파일은 Prepare 가 읽음)" : "보류 (_보류 — 파이프라인이 보지 않음)"));
      }
      for (const f of c.held) {
        const named = heldNamed(c, f);
        const tags = [f.image ? null : "PSD — 이름만", named.length ? `${named.map(whereText).join(", ")} 가 가리킴` : null].filter(Boolean).join(" · ");
        const back = restoreName(f);
        fileRow(f, tags || null, f.image ? [["되살리기", `refs 로 되살려${back !== f.name ? `(이름 ${back})` : ""} ${as} 연결(같은 이름이 있으면 409)`,
          () => link("restore", { crop_id: c.id, name: f.name }, restoreAsk(c, f)), true]] : []);
      }
      if (c.explicit.length) {
        panel.append(el("div", "lp-k", "스펙이 이름으로 지정 (그 대상은 연결 대신 이 파일)"));
        for (const x of c.explicit) panel.append(el("p", "dim", `${whereText(x)} → ${x.file}`));
      }
      const b = el("div", "lp-btns");
      if (!noneFixed) {
        b.append(button("레퍼런스 없이 고치기로 고정", "files = [] — Prepare 가 레퍼런스 없이", () => link("none", { crop_id: c.id },
          "레퍼런스 없이 고치기로 고정합니다 — 다음 Run 에서 이 크롭은 새 생성 조건(유료 대상)일 수 있음.\n계속할까요?")));
      }
      if (c.fixed) b.append(button("고정 풀기", "이름 매칭으로 돌아감", () => link("unfix", { crop_id: c.id }, unfixAsk(c))));
      for (const x of b.children) x.disabled = off;
      panel.append(b);
      panel.append(el("div", "lp-k", "검사"));
      const ul = el("ul", "lp-checks");
      if (!c.checks.length) ul.append(el("li", "ok", "문제 없음"));
      for (const x of c.checks) {
        const li = el("li", x.level, `${x.level === "warn" ? "경고" : "정보"} · ${x.msg}`);
        li.title = x.code;
        ul.append(li);
      }
      panel.append(ul);
    }

    function orphanBlock(o) {
      panel.append(el("h3", "lp-title", `크롭 없는 연결 ${o.name || o.id}`));
      panel.append(el("p", "dim", `크롭이 사라진 연결(PS 로 이름을 바꿔 ID 가 바뀜 등 — R-orphan): ${o.files.join(", ") || "파일 없음"}`));
      const free = crops().filter((c) => !c.fixed).sort(byName);
      if (!free.length) {
        panel.append(el("p", "dim", "다시 연결할 수 있는 크롭(고정 안 된 크롭)이 없습니다"));
        return;
      }
      const pick = el("select", "rf-pick");
      pick.tabIndex = -1;
      pick.dataset.keys = "board";
      for (const c of free) {
        const op = el("option", "", c.name);
        op.value = c.id;
        pick.append(op);
      }
      pick.addEventListener("change", () => pick.blur());
      const b = el("div", "lp-btns");
      const go = button("이 크롭에 다시 연결", "relink — 고르는 크롭이 고정돼 있으면 409", () => {
        const to = crops().find((c) => c.id === pick.value);
        link("relink", { crop_id: o.id, to: pick.value },
          `${o.id} 의 연결(${o.files.join(", ")})을 ${to ? to.name : pick.value} 에 다시 연결합니다 — 그 크롭의 레퍼런스가 바뀌면 다음 Run 에서 새 생성 조건.\n계속할까요?`);
      }, "primary");
      go.disabled = !!refsBlock() || S.busy;
      b.append(pick, go);
      panel.append(b);
    }

    function slider(label, value, min, max, step, fmt, set) {
      // 끄는 동안(input) = 값·미리보기만, 놓으면(change) = 입력칸을 떠나고 set(값, true) — 포커스가 남으면 보드 키가 무시된다
      const r = el("div", "rf-slider");
      const i = el("input");
      i.type = "range";
      i.min = String(min);
      i.max = String(max);
      i.step = String(step);
      i.value = String(value);
      i.tabIndex = -1;
      i.dataset.keys = "board"; // 값 변화 없이 놓으면 change(blur)가 없어 포커스가 남는다 — 보드 키는 그대로 받음(board.js typing)
      const v = el("span", "rf-val", fmt(value));
      i.addEventListener("input", () => {
        v.textContent = fmt(Number(i.value));
        set(Number(i.value), false);
      });
      i.addEventListener("change", () => {
        i.blur();
        set(Number(i.value), true);
      });
      r.append(el("span", "k", label), i, v);
      panel.append(r);
    }

    function cutBlock(c) {
      panel.append(el("h3", "lp-title rf-sec", "컷 · 변환 · 저장"));
      if (!sheet) {
        panel.append(el("p", "lp-msg", "가운데 목록에서 시트를 여세요"));
        return;
      }
      const sr = row("시트", `${sheet.path} · ${sheet.size[0]}×${sheet.size[1]}`);
      sr.append(button("목록", "시트 목록으로", closeSheet, "inline"));
      panel.append(el("p", "dim", "선택은 부품에 바짝 — 넉넉하면 옆 부품이 들어옴(5% 넉넉 → IoU 0.83)"));
      const mb = el("div", "lp-btns");
      for (const [m, label, title] of METHODS) {
        const b = button(label, title, () => setMethod(m), method === m ? "on" : "");
        if (m === "sam3" && !ckpts().length) {
          b.disabled = true;
          b.title = "SAM3 체크포인트가 없습니다 — models/checkpoints 에 이름에 sam3 가 든 파일(ComfyUI 재시작 뒤 보임)";
        }
        mb.append(b);
      }
      panel.append(mb);
      // 그리드 탭(3-4): 시트가 숨어 끌기·점·시트 키가 꺼짐 — 키 안내 대신 [시트] 안내, 컷 단추에 (Enter) 를 달지 않음
      const g = tab === "grid";
      panel.append(el("p", "dim", g ? "그리드 탭 — 영역·점·Enter 컷은 [시트] 탭에서(그리드에서는 시트 키가 꺼짐)" : {
        rect: "빈 곳에서 끌기 = 영역 · 핸들 = 고치기 · Enter = 컷",
        sam3: "클릭 = 양(+) · Alt+클릭 = 음(−) · Backspace = 마지막 점 · Enter = 컷(다시 컷) · 자동 양점(회색)은 늘 함께",
        poly: "클릭 = 꼭짓점 · 더블클릭·Enter = 닫기(새 선택) · Shift+닫기 = 앞 결과에 더하기 · Alt+닫기 = 빼기 · Backspace = 마지막 꼭짓점",
      }[method]));
      if (method === "sam3") samFields();
      if (rect) {
        row("영역", sizeText(rect));
        if (Math.max(rect[2] - rect[0], rect[3] - rect[1]) > 2 * SAM_SIDE && method === "sam3") {
          panel.append(el("p", "warn", "SAM 이 줄여 봄 — 경계가 거칠 수 있음, 영역을 좁히거나 다각형으로 다듬기"));
        }
      } else {
        row("영역", "없음 — 시트의 빈 곳에서 끌기", "dim");
      }
      if (method === "sam3" && points.length) row("점", `양 ${points.filter((p) => p[2]).length} · 음 ${points.filter((p) => !p[2]).length}`);
      if (method === "poly" && poly.length) row("다각형", `꼭짓점 ${poly.length}`);
      const cb = el("div", "lp-btns");
      cb.append(button((method === "poly" ? "닫기·컷" : "컷") + (g ? "" : " (Enter)"), "선택 영역을 지금 방법으로 컷(SAM3 는 ComfyUI 대기열 앞에서 돎)", doCut,
        "primary"));
      if (method === "poly" && result) {
        cb.append(button("닫기·더하기", "Shift+닫기 — 앞 결과에 더하기", () => closePoly({ shiftKey: true })),
          button("닫기·빼기", "Alt+닫기 — 앞 결과에서 빼기", () => closePoly({ altKey: true })));
      }
      if (inflight) {
        live.cancel = button("취소", "ComfyUI 대기열에서 지움(대기 중일 때만)", cancelCut);
        live.job = el("span", "lh-wait");
        cb.append(live.cancel, live.job);
      }
      panel.append(cb);
      jobLine();
      slider("grow", grow, -32, 32, 1, (v) => `${v}px`, (v, done) => {
        grow = v;
        if (done) refine();
      });
      slider("feather", feather, 0, 16, 0.5, (v) => `σ ${v}`, (v, done) => {
        feather = v;
        if (done) refine();
      });
      if (!result) {
        panel.append(el("p", "lp-msg", "컷을 하면 결과·변환·저장이 보입니다"));
        return;
      }
      row("결과", `면적 ${(result.area_frac * 100).toFixed(1)}% · 조각 ${result.n_parts}` + (result.sec !== undefined ? ` · ${result.sec}s` : ""));
      const mt = el("div", "lp-btns");
      mt.append(el("span", "dim", "바닥"));
      for (const [m, label] of MATTES) {
        mt.append(button(label, m === "gray" ? "GPT 가 받는 매트(#808080)" : "", () => {
          matte = m;
          render();
        }, matte === m ? "on" : ""));
      }
      panel.append(mt);
      transformBlock(c);
      saveBlock(c);
    }

    function samFields() {
      const r = el("div", "lp-row");
      const t = el("input", "rf-text");
      t.type = "text";
      t.dataset.k = "text";
      t.value = text;
      t.maxLength = 80;
      t.placeholder = "텍스트(선택, 영문) — 기본은 비움(긴 이름은 다른 부위를 잡음)";
      t.spellcheck = false;
      t.addEventListener("input", () => {
        text = t.value;
      });
      t.addEventListener("keydown", (e) => {
        if (e.isComposing || e.keyCode === 229) return;
        if (e.key === "Enter" || e.key === "Escape") {
          e.preventDefault();
          t.blur();
        }
      });
      r.append(el("span", "k", "텍스트"), t);
      panel.append(r);
      const cs = ckpts();
      if (cs.length > 1) {
        const s = el("select", "rf-pick");
        s.tabIndex = -1;
        s.dataset.keys = "board";
        for (const n of cs) {
          const o = el("option", "", n);
          o.value = n;
          s.append(o);
        }
        s.value = ckpt || cs[0];
        s.addEventListener("change", () => {
          s.blur();
          ckpt = s.value === cs[0] ? null : s.value;
        });
        const cr = el("div", "lp-row");
        cr.append(el("span", "k", "체크포인트"), s);
        panel.append(cr);
      }
    }

    function transformBlock(c) {
      panel.append(el("div", "lp-k", "변환 (회전 → 반전 → 배율 — 확대 없음)"));
      slider("회전", T.rotate, -180, 180, 0.5, (v) => `${v}°`, (v, done) => {
        setT({ rotate: v });
        if (done) render();
      });
      slider("배율", T.scale, 0.25, 1, 0.05, (v) => `×${v}`, (v, done) => {
        setT({ scale: v });
        if (done) render();
      });
      const fb = el("div", "lp-btns");
      fb.append(button(T.flip_h ? "좌우 반전 켬 (H)" : "좌우 반전 (H)", "회전 뒤 좌우 — 대칭 부품만(매뉴얼)", () => {
        setT({ flip_h: !T.flip_h });
        render();
      }, T.flip_h ? "on" : ""), button("−90°", "Ctrl+[", () => rotateBy(-90)), button("+90°", "Ctrl+]", () => rotateBy(90)));
      const ab = el("label", "rf-check");
      const cbx = el("input");
      cbx.type = "checkbox";
      cbx.tabIndex = -1;
      cbx.dataset.keys = "board";
      cbx.checked = asym;
      cbx.addEventListener("change", () => {
        cbx.blur();
        asym = cbx.checked;
        render();
      });
      ab.append(cbx, document.createTextNode(" 글자·비대칭 있음"));
      fb.append(ab);
      panel.append(fb);
      if (T.flip_h && ((c && sideOf(c.name)) || asym)) panel.append(el("p", "warn", "대칭 부품만 반전(매뉴얼) — 크롭 이름에 좌우가 있거나 글자·비대칭이 있음"));
      live.size = el("div", "lp-row", sizeLine());
      panel.append(live.size);
      // 비교: 크롭 Before | 변환한 컷. 겹쳐 보기 = Before 위에 반투명(끌기 = 위치, 휠 = 화면 배율 — 각도 판단용, 저장 안 함)
      const url = c ? beforeUrl(c) : null;
      const bu = url ? opts.apiUrl(url) : null;
      if (bu) {
        if (bfImg.getAttribute("src") !== bu) bfImg.src = bu;
        bfImg.hidden = false;
      } else {
        bfImg.removeAttribute("src");
        bfImg.hidden = true;
      }
      cutCanvas.className = "rf-cut" + (overlay ? " over" : ` m-${matte}`);
      ctBox.hidden = !!overlay;
      if (overlay) bfBox.append(cutCanvas);
      else ctBox.append(cutCanvas);
      cmp.replaceChildren(bfBox, ctBox);
      cmp.classList.toggle("over", !!overlay);
      panel.append(cmp);
      const ob = el("div", "lp-btns");
      const ovb = button(overlay ? "나란히" : "겹쳐 보기", "크롭 Before 위에 반투명으로(끌기 = 위치, 휠 = 화면 배율)", toggleOverlay, overlay ? "on" : "");
      ovb.disabled = !bu;
      ob.append(ovb);
      if (!bu) ob.append(el("span", "dim", c ? "이 크롭의 Before 가 없습니다(Prepare 전)" : "크롭을 고르면 Before 와 비교"));
      panel.append(ob);
      if (overlay) {
        slider("투명도", overlay.alpha, 0.1, 1, 0.05, (v) => `${Math.round(v * 100)}%`, (v) => {
          overlay.alpha = v;
          placeOverlay();
        });
      }
      drawPreview();
      const hr = el("div", "lp-row");
      live.hint = el("span", "rf-hint", hintText(T) || "회전·반전 없음 — 제안할 _hint 없음");
      hr.append(el("span", "k", "_hint 제안"), live.hint);
      hr.append(button("복사", "스펙 _hint 에 붙일 문구(보드는 스펙을 쓰지 않음 — S3)", () => {
        const h = hintText(T);
        if (!h) return;
        navigator.clipboard.writeText(h).then(() => opts.toast("_hint 제안을 복사했습니다"), (e) => opts.toast(`복사하지 못했습니다: ${e.message}`, "err"));
      }, "inline"));
      panel.append(hr);
    }

    function saveBlock(c) {
      panel.append(el("div", "lp-k", "저장 (역할 · 설명 · 이름 — 덮어쓰지 않음)"));
      if (!c) {
        panel.append(el("p", "lp-msg", "크롭을 고르면 저장할 수 있습니다"));
        return;
      }
      const rb = el("div", "lp-btns");
      rb.append(button("대표", "<크롭 이름>_t.png — GPT 에 보내는 1장", () => {
        role = "main";
        render();
      }, role === "main" ? "on" : ""), button("보조", "<크롭 이름>_<설명>_t.png — Spec Draft 가 함께 봄", () => {
        role = "aux";
        render();
      }, role === "aux" ? "on" : ""));
      panel.append(rb);
      if (role === "aux") {
        const i = el("input", "rf-desc");
        i.type = "text";
        i.dataset.k = "desc";
        i.value = desc;
        i.maxLength = 48;
        i.placeholder = `설명(예: 측면) — 1–${DESC_MAX}자, 글자·숫자·-`;
        i.spellcheck = false;
        i.addEventListener("input", () => {
          desc = i.value;
          showName(c);
        });
        i.addEventListener("keydown", (e) => {
          // Enter = 저장(패널 포커스), Esc = 입력칸을 떠남. 한글 조합을 확정하는 Enter 는 조합만
          if (e.isComposing || e.keyCode === 229) return;
          if (e.key === "Enter") {
            e.preventDefault();
            i.blur();
            save();
          } else if (e.key === "Escape") {
            e.preventDefault();
            i.blur();
          }
        });
        panel.append(i);
      }
      live.name = el("div", "lp-preview");
      live.impact = el("ul", "lp-checks");
      const sb = el("div", "lp-btns");
      // Enter 로 저장하는 곳은 보조의 설명 칸뿐 — 대표 역할에는 입력칸이 없어 Enter 는 컷(onKey)
      live.save = button(role === "aux" ? "저장 (Enter)" : "저장", "새 파일로 저장하고 크롭을 크롭 ID 로 고정 — 덮어쓰지 않음", () => save(), "primary");
      sb.append(live.save);
      panel.append(live.name, live.impact, sb);
      showName(c);
    }

    function showName(c) {
      const nm = refFileName(c.name, role, role === "aux" ? desc : null);
      live.name.textContent = nm.name || NAME_ERRS[nm.err];
      live.name.classList.toggle("bad", !nm.name);
      live.impact.replaceChildren(...(nm.name ? impactLines(c, nm.name) : []).map(([cls, t]) => el("li", cls, t)));
      const why = saveProblem(c, nm);
      live.save.disabled = !!why;
      live.save.title = why || "새 파일로 저장하고 크롭을 크롭 ID 로 고정 — 덮어쓰지 않음";
    }

    // ── board.js 와의 경계 ──
    async function reload(force) {
      if (S.busy) {
        // 쓰기 뒤 다시 읽기는 이 변경보다 먼저 서버에 닿았을 수 있다 — 다음 state 에서 처음부터 다시
        seen = null;
        return;
      }
      const got = await S.load(force);
      if (S.err) seen = null; // 읽기 실패 — 다음 state 에서 다시
      if (got && sel && !cur() && !curOrphan()) sel = null;
      const rec = got && sheet && sheet.v ? S.R.sheets.find((x) => x.path === sheet.path && !x.layer) : null;
      if (rec && rec.v && rec.v !== sheet.v) reloadSheet(sheet); // 최근 시트 행의 지금 서명이 연 시트와 다름 = 바깥에서 저장됨
      renderSoon();
    }

    function enter(st) {
      on = true;
      state = st;
      seen = [st.rev, st.refs_rev, st.layout_rev, st.refs_files_v];
      scrolledTo = null;
      viewer.setEditor({ pointerdown, pointermove, pointerup, cancel, draw });
      renderSheets();
      render();
      reload(true);
      if (sheetList === null || sheetList.error) loadSheets();
    }

    function leave() {
      on = false;
      if (drag && drag.moved) rect = drag.rect0; // 놓기 전에 나감 = 끌기 취소(cancel 과 같음 — 놓지 않은 영역 변경은 결과를 버리지 않았음, codex10)
      drag = null;
      poly = [];
      viewer.setEditor(null);
    }

    function stateChanged(st) {
      // 매니페스트 rev 가 바뀜 = 미반영 상태가 바뀜, layout_rev 가 바뀜 = 크롭 이름·목록이 바뀜(refs rev 는 그대로라 since 가 unchanged 를
      // 줌), refs_files_v 가 바뀜 = refs_dir 파일이 바깥(PS·탐색기)에서 바뀜(refs rev 그대로) → 다시 받음. refs_rev 만 = since 로(같은
      // 판이면 unchanged). 옛 서버는 refs_files_v 가 없다(undefined — 늘 같음)
      state = st;
      if (!on) return;
      const prev = seen;
      seen = [st.rev, st.refs_rev, st.layout_rev, st.refs_files_v];
      if (prev && prev.every((v, i) => v === seen[i])) {
        renderGrid(); // 그리드 크롭 소스는 state 를 씀 — 같은 판이라도 새 state 면(Before·캔버스 미리보기 주소) 다시
        return;
      }
      reload(!prev || prev[0] !== st.rev || prev[2] !== st.layout_rev || prev[3] !== st.refs_files_v);
    }

    return { enter, leave, onKey, stateChanged, select: (cropId) => {
      // 처음 진입해 /refs 를 읽는 중에도 보드에 있는 크롭은 선택해 둔다. reload 가 사라진 선택을 정리한다.
      if ((state && state.crops || crops()).some((c) => c.id === cropId)) select(cropId);
    } };
  }

  return { create, session, checkDesc, refFileName, transformSize, sideOf, hintText };
})();
