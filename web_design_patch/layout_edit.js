// BMK Design Patch Studio — S1 레이아웃 편집기(STUDIO_S1_SPEC §3). board.js 가 편집 모드(E)에서 켜고 끈다.
// 뷰어(viewer.js setEditor) 위에 편집 층을 얹고, 왼쪽 레이아웃 크롭 목록·오른쪽 속성 패널·머리줄을 그린다. 서버 = layout*(§4).
// 끄는 동안은 클라이언트만 바뀌고 놓을 때 POST 한 번. 쓰기는 큐로 하나씩(rev 차례). 실행 취소 = 세션 안 스택, 한 단계 = 역연산 POST 하나
// (409 이거나 다른 창의 쓰기를 받으면 비움).
// 기하·이름 규칙은 layout_geom.js(DPLayoutGeom). 모든 문자열은 textContent 로만 넣는다. 서버 URL 은 opts.apiUrl(상대 경로)로만 연다.
// 이름 바꾸기 미리보기(GET layout/refs)는 크롭 id 를 함께 보낸다 — 이름 바꾸기가 레퍼런스를 먼저 고정하므로(S2 §2.6) 바꾼 뒤에도 연결 유지.
"use strict";

window.DPLayoutEdit = (() => {
  const G = window.DPLayoutGeom;
  const CLICK_PX = 4;
  const HANDLE_PX = 6; // 핸들을 잡는 거리(화면 px)
  const NUDGE_MS = 400; // 화살표 미세 이동: 멈춘 뒤 이만큼 지나 POST 한 번(한 단계)
  const REFS_MS = 300; // 이름을 바꾸는 중: 입력이 멈춘 뒤 이만큼 지나 GET layout/refs 한 번
  const HANDLES = ["nw", "ne", "se", "sw", "n", "e", "s", "w"]; // 모서리 먼저(작은 크롭에서 모서리가 이김)
  const CURSORS = { n: "ns-resize", s: "ns-resize", e: "ew-resize", w: "ew-resize", ne: "nesw-resize", sw: "nesw-resize",
    nw: "nwse-resize", se: "nwse-resize" };
  const BOX_COLORS = ["rgb(255, 60, 60)", "rgb(40, 220, 90)", "rgb(70, 130, 255)", "rgb(255, 160, 40)"]; // _BOX_COLORS A·B·C·D
  const STATUS = { new: "새", changed: "바뀜" };
  const FIELDS = { rect: "사각형", name: "이름", targets: "대상", layer_name: "대상" }; // layer_name = PSD shape 레이어명(→ 대상)
  const LEVELS = { error: "오류", warn: "경고", info: "정보" };
  const SERVER_CODES = /^[RDH]-/; // 서버만 아는 검사(레퍼런스·PSD·옛 기록) — 끄는 동안에도 서버 값 그대로
  const RECT_MSGS = { error: "사각형이 비었거나 잘못되었습니다", outside: "사각형이 캔버스 밖으로 나갑니다", ratio: "사각형 비가 3:1 을 넘습니다" };
  // 반영(큐) 확인 창의 정보 줄(S3 §3.2 — 무료 반영은 Spec Draft 를 돌리지 않는다)
  const APPLY_NOTE = "원장에만 남은 초안(받았지만 스펙에 못 쓴 것)은 이 반영이 병합하지 않음 — 진행(G) 초안 줄의 '못 쓴 스펙'으로 보이고, "
    + "그래프 Spec Draft 를 승인 없이 한 번 큐하면 병합";

  function sameRect(a, b) {
    return a.every((v, i) => v === b[i]);
  }

  function boxColor(tid, i) {
    return BOX_COLORS["ABCD".indexOf(tid)] || BOX_COLORS[i % BOX_COLORS.length];
  }

  function plainTargets(c) {
    // 레거시(빈 목록)는 A 하나로 — 대상을 고치면 그 목록을 통째로 보낸다(§4.4)
    return c.targets.length ? c.targets.map((t) => ({ tid: t.tid, text: t.text, box: t.box })) : [{ tid: "A", text: "", box: null }];
  }

  function fieldNames(fs) {
    return fs.map((f) => FIELDS[f] || f).join("·");
  }

  function reloadKind(seen, rev, st, seenLr) {
    // 보드 state 를 받았을 때 /layout 을 다시 받을지. seen = 마지막으로 맞춘 매니페스트 rev(null = 처음·읽기 실패 뒤), rev = 지금 가진 판,
    // seenLr = 직전 state 의 layout_rev. "layout" = 레이아웃 판이 지금 가진 판과도 직전 state 와도 다름(다른 창이 씀·이력 파일로 되돌려
    // 판이 낮아짐 — 이 창의 쓰기 직후에 오는 낡은 state 는 직전 state 와 같아 다시 받지 않음)·처음·실패 뒤,
    // "manifest" = 매니페스트 rev 만 바뀜(반영 상태·기록 — 입력 중이면 패널은 나중에), null = 그대로
    const lr = st.layout_rev;
    if (seen === null || (lr !== rev && lr !== seenLr)) return "layout";
    return st.rev !== seen ? "manifest" : null;
  }

  function renameNote(cur, after, byFile) {
    // 이름을 바꿀 때 다음 Run 이 새 셀인지(§5): 바꾼 뒤 레퍼런스(개수·첫 파일)가 다르면 확정, 같으면(또는 아직 모름) 부위 라벨(문구 없는
    // 타깃)이 프롬프트에 쓰일 때만. 폴더·PSD 수확 후보는 파일 이름이 옛 이름이라 이어지지 않는다
    const moved = !!(cur && after) && (cur.n !== after.n || cur.first !== after.first);
    return (moved ? "레퍼런스가 바뀌어 다음 Run 은 새 셀(초안 갱신 대기)" : "부위 라벨이 프롬프트에 쓰이면 다음 Run 은 새 셀일 수 있음")
      + (byFile ? ` · 폴더·PSD 수확 후보(${byFile})는 옛 이름 파일 — mapping_json 에 ID 로` : "") + " — Enter 로 적용";
  }

  // ── 서버 세션: 마지막 GET /layout(L)과 실행 취소 스택. DOM 을 쓰지 않는다(J-L2 가 node 에서 가짜 fetch 로 돈다) ──
  function session(apiUrl, root, fetchFn) {
    // ownRev = 이 창이 맞춘 마지막 판(쓰기 응답·첫 읽기·다른 창의 쓰기를 받은 읽기), notice = 그 때문에 기록을 비웠다는 알림(화면이 띄움)
    // stale = 쓰기 뒤 다시 읽기에 실패함(L 은 낡고 rev 만 앞섬) — 다시 읽을 때까지 다음 쓰기를 보내지 않는다
    // remote = 다른 창(또는 이력 복구)의 판을 받은 횟수 — 끌기·미세 이동은 시작할 때 값을 들고 있다가 바뀌었으면 보내지 않는다
    const s = { L: null, err: null, busy: false, undo: [], redo: [], ownRev: null, notice: null, stale: false, remote: 0 };
    let tail = Promise.resolve(); // 쓰기 큐의 끝
    let queued = 0;
    let writes = 0; // 이 창의 쓰기 성공 수 — 읽기가 도는 사이 쓰기가 끝나면 그 읽기는 낡았을 수 있다
    let reads = 0; // 보낸 읽기 수(요청 순서) — 늦게 보낸 읽기를 먼저 받았으면 그보다 먼저 보낸 읽기의 응답은 버린다
    let readDone = 0; // 받아들인 읽기 중 가장 늦게 보낸 것의 번호

    async function call(path, body) {
      const r = await fetchFn(apiUrl(path), body === undefined ? { cache: "no-store" } : {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(Object.assign({ root }, body)),
      });
      let data = null;
      try {
        data = await r.json();
      } catch (e) {
        // JSON 이 아닌 응답(라우트가 없는 옛 서버의 404 등) — 아래에서 상태 코드로 알린다
      }
      if (!r.ok || !data) {
        const msg = data && data.error ? data.error
          : r.status === 404 ? "레이아웃 라우트가 없습니다 — ComfyUI 를 재시작해야 레이아웃 편집을 쓸 수 있습니다"
            : `응답을 읽을 수 없습니다 (HTTP ${r.status})`;
        throw Object.assign(new Error(msg), { status: r.status });
      }
      return data;
    }

    s.load = async () => {
      const gen = writes;
      const seq = ++reads;
      let L;
      try {
        L = await call("layout?" + new URLSearchParams({ root }).toString());
      } catch (e) {
        s.err = e.message;
        return;
      }
      s.err = null;
      // 이 창의 쓰기보다 먼저 보낸 읽기·더 늦게 보낸 읽기를 이미 받은 읽기 — 버림(쓰기 뒤에는 run 이 다시 읽는다).
      // rev 크기로 거르지 않는다: 이력 파일로 되돌리면 판이 낮아진다
      if (gen !== writes || seq < readDone) return;
      readDone = seq;
      s.L = L;
      s.stale = false;
      if (L.rev === s.ownRev) return;
      // 이 창의 마지막 쓰기 뒤 다른 창이 썼다 — 남은 역연산이 그 위를 덮지 않게 기록을 비운다(첫 읽기는 기준만 잡음)
      if (s.undo.length || s.redo.length) s.notice = "다른 창에서 레이아웃이 바뀌어 실행 취소 기록을 비웠습니다";
      s.undo = [];
      s.redo = [];
      s.ownRev = L.rev;
      s.remote += 1;
    };

    s.crop = (id) => (s.L && s.L.crops.find((c) => c.id === id)) || null;

    s.ownWrite = (from, to) => {
      // 같은 창 진행(G) [되돌리기]의 쓰기(from → to) — 이 창이 맞춘 판(from)에서 쓴 것이면 이 창의 쓰기로(실행 취소 기록을 남김). 쓰기 세대를 올려
      // 그 전에 보낸 읽기(옛 판)의 늦은 응답이 기록을 비우지 않게(load 가 버림)
      if (s.ownRev !== from) return;
      s.ownRev = to;
      writes += 1;
    };

    function run(fn) {
      // 쓰기는 큐로 하나씩 — 각 작업은 실행할 때의 rev 로 POST 하고, 끝나면 GET 으로 다시 맞춘다. 409 = 다른 창에서 바뀜 → 스택을 비운다
      queued += 1;
      s.busy = true;
      const p = tail.then(async () => {
        try {
          // 대상은 목록을 통째로 보내므로, 낡은 L 로 만든 쓰기는 앞 쓰기를 되돌린다(rev 는 맞아 서버도 못 막음)
          if (s.stale) throw new Error("레이아웃을 다시 읽지 못해 이 편집을 보내지 않았습니다 — 새로고침");
          return await fn();
        } catch (e) {
          if (e.status === 409) {
            s.undo = [];
            s.redo = [];
            e.message += " (실행 취소 기록을 비웠습니다)";
          }
          throw e;
        } finally {
          await s.load();
          if (s.err) s.stale = true;
          queued -= 1;
          s.busy = queued > 0;
        }
      });
      tail = p.catch(() => {}); // 앞 작업의 실패가 뒤 작업을 막지 않게
      return p;
    }

    async function post(body) {
      const r = await call("layout/crop", Object.assign({ rev: s.L.rev }, body));
      s.L.rev = s.ownRev = r.rev;
      writes += 1;
      return r;
    }

    function record(fwd, inv) {
      s.undo.push({ fwd, inv });
      s.redo = [];
    }

    s.add = (fields) => run(async () => {
      const r = await post(Object.assign({ op: "add" }, fields));
      // crop null = 쓰기는 됐지만 서버가 응답을 만들지 못함(warning) — 새 ID 를 몰라 이 추가는 실행 취소 기록에 넣지 않는다
      if (r.crop) record(Object.assign({ op: "add", id: r.crop.id }, fields), { op: "delete", id: r.crop.id });
      return r;
    });

    s.update = (id, changes, since) => run(async () => {
      // since = 편집을 시작할 때의 remote(끌기·미세 이동·제안 — 옛 사각형 기준 값). 그 사이 다른 창이 썼으면 덮지 않고 409
      if (since !== undefined && since !== s.remote) {
        throw Object.assign(new Error("다른 창에서 레이아웃이 바뀌어 이 사각형 편집을 보내지 않았습니다 — 다시 하세요"), { status: 409 });
      }
      const c = s.crop(id);
      // 함수면 큐에서 실행할 때의 최신 크롭으로 바꿀 값을 만든다 — targets 는 통째로 보내므로, 앞에 기다리던 대상 쓰기를 덮지 않게.
      // null = 최신 값과 같음(보내지 않음 — 바뀌었는지는 그린 때의 크롭이 아니라 여기서 본다)
      if (typeof changes === "function") {
        changes = changes(c);
        if (!changes) return { ok: true, skipped: true };
      }
      // restore_rev = 되돌릴 값이 맞던 판 — 서버가 그 판과 같은 값은 규칙 검사를 건너뛴다(이관된 규칙 밖 값도 되돌림)
      const before = { restore_rev: s.L.rev };
      for (const k of Object.keys(changes)) before[k] = k === "targets" ? plainTargets(c) : c[k];
      const r = await post(Object.assign({ op: "update", id }, changes));
      record(Object.assign({ op: "update", id }, changes), Object.assign({ op: "update", id }, before));
      return r;
    });

    s.remove = (id) => run(async () => {
      const c = s.crop(id);
      const at = { restore_rev: s.L.rev, index: s.L.crops.indexOf(c) };
      const r = await post({ op: "delete", id });
      // 되살리기 = add 에 id·psd_name(같은 ID·사전 편집 이름)·restore_rev·index(그 판의 값·원래 자리)를 싣는다. 그 ID 를 다른 크롭이
      // 쓰는 중이면 서버가 새 ID 를 주고 step 이 그 단계와 남은 단계를 그 ID 로 고친다
      record({ op: "delete", id }, Object.assign({ op: "add", id, name: c.name, rect: c.rect, targets: plainTargets(c), note: c.note,
        psd_name: c.psd_name }, at));
      return r;
    });

    function remap(from, to) {
      for (const e of s.undo.concat(s.redo)) {
        for (const b of [e.fwd, e.inv]) if (b.id === from) b.id = to;
      }
    }

    function step(back) {
      // 한 단계 = POST 하나(rev = 이 창의 판 — 사이에 다른 창이 썼으면 409 로 두 스택을 비움). 다른 실패면 그 단계를 스택에 되돌려 둔다.
      // 스택은 실행할 때 고른다(큐에서 기다리는 사이 비워졌을 수 있음)
      return run(async () => {
        const [from, to, side] = back ? [s.undo, s.redo, "inv"] : [s.redo, s.undo, "fwd"];
        const e = from.pop();
        if (!e) return null;
        let r;
        try {
          r = await post(Object.assign({ rev: s.ownRev }, e[side]));
        } catch (err) {
          if (err.status !== 409) from.push(e);
          throw err;
        }
        to.push(e);
        if (e[side].op === "add" && r.crop && r.crop.id !== e[side].id) remap(e[side].id, r.crop.id); // 되살린 크롭이 새 ID — 이 단계까지 고침
        return e;
      });
    }

    s.undoStep = () => step(true);
    s.redoStep = () => step(false);

    s.start = () => run(() => call("layout/start", {}));

    s.psd = (op, names, seen) => run(async () => {
      // 본 스냅숏(seen = 확인 창을 띄울 때의 psd_diff, 없으면 지금 것)의 token 을 싣는다 — 큐에서 기다리는 사이 Import 가
      // 스냅숏을 바꿨으면 서버가 409(새로고침)
      const d = seen || s.L.psd_diff;
      if (!d) throw new Error("PSD 갈라짐이 없습니다(새로고침됨)");
      const r = await call("layout/psd", { rev: s.L.rev, op, names, snapshot: { token: d.token } }); // 지문 숫자는 JS 에서 반올림됨 — 문자열 token
      s.ownRev = r.rev;
      writes += 1;
      // PSD 값 가져오기는 되돌리지 않는다 — 앞 단계의 역연산이 그 위를 덮지 않게 스택을 비운다
      s.undo = [];
      s.redo = [];
      return r;
    });

    // id = 이름을 바꾸는 크롭 — 고정된 크롭(또는 이름 바꾸기가 먼저 고정할 크롭)은 이름과 상관없이 지금 레퍼런스(연결 유지, S2 §2.6)
    s.refs = (name, id) => call("layout/refs?" + new URLSearchParams({ root, name, id }).toString());

    s.idle = async () => {
      // 쓰기 큐가 빌 때까지(기다리는 사이 더 들어온 쓰기 포함) — 반영(큐) 전에 저장 중인 편집이 디스크에 닿게
      while (queued) await tail;
    };

    return s;
  }

  // ── 화면 ──
  function create(hosts, opts) {
    const { list, panel, head } = hosts;
    const viewer = opts.viewer;
    const svg = window.DPViewer.svg;
    const S = session(opts.apiUrl, opts.root, (u, o) => fetch(u, o));
    let on = false;
    let state = null; // 보드 state(캔버스·매니페스트 크롭·rev·layout_rev)
    let seen = null; // /layout 을 마지막으로 맞춘 매니페스트 rev(null = 처음·읽기 실패 뒤 — 다음 state 에서 다시 받음)
    let seenLr = null; // 직전 state 의 layout_rev(reloadKind — 이 창의 쓰기 직후에 오는 낡은 state 를 거름)
    let seenRf = null; // 직전 state 의 [refs_rev, refs_files_v] — S2 연결·바깥 파일 변경은 두 판을 그대로 두지만 크롭 행의 레퍼런스 줄을 바꿈
    let sel = null; // 선택 크롭 id
    let drag = null; // {kind: move|resize|new|box, id, handle, tid, start, x, y, rect0, rect, moved, next}
    let draft = null; // 새 크롭 초안 {rect} — 이름을 넣고 만들기(Enter) = add 한 번
    let boxTid = null; // 상자를 그릴 대상(P·상자 버튼)
    let proposal = null; // 상자에 맞춘 크롭 제안 {id, rect} — Enter 적용
    let nudge = null; // 화살표 미세 이동 {id, rect, timer}
    let sent = null; // 놓은 뒤 응답 전까지 보이는 {id, rect} — 그것을 놓은 쓰기가 끝날 때만 지운다
    let edits = {}; // 속성 패널에서 입력 중인 값(data-k → 값) — 다시 그려도 남는다
    let live = null; // 끄는 동안 고치는 패널 칸 {gpt, checks}
    let composing = false; // 패널 입력칸에서 한글 조합 중
    let pendingPanel = false; // 입력 중이라 미룬 패널 다시 그리기(폴링·저장 응답)
    let refsAt = { name: null, id: null, refs: undefined, timer: null }; // 바꾸는 중인 이름의 레퍼런스(GET layout/refs)
    let nameShow = null; // 지금 패널 이름 칸의 다시 표시(레퍼런스 응답이 오면 부름)

    function el(tag, cls, text) {
      const e = document.createElement(tag);
      if (cls) e.className = cls;
      if (text !== undefined && text !== null) e.textContent = String(text);
      return e;
    }

    function chip(text, cls) {
      return el("span", "chip " + (cls || ""), text);
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

    function row(key, text, cls) {
      const r = el("div", "lp-row " + (cls || ""));
      const v = el("span", "", text);
      r.append(el("span", "k", key), v);
      panel.append(r);
      return v;
    }

    function input(k, value, placeholder, cls) {
      const i = el("input", cls || "");
      i.type = "text";
      i.dataset.k = k;
      i.value = k in edits ? edits[k] : value;
      i.placeholder = placeholder;
      i.spellcheck = false;
      return i;
    }

    function textField(e) {
      return !!e && ((e.tagName === "INPUT" && e.type === "text") || e.tagName === "TEXTAREA");
    }

    function typingInPanel() {
      return composing || (panel.contains(document.activeElement) && textField(document.activeElement));
    }

    // ── 상태 ──
    function editable() {
      return on && !!state && !!state.canvas && !!S.L && S.L.exists && S.L.editable;
    }

    function rectOf(c) {
      // 끄는 중·미세 이동·응답 대기 사각형이 서버 값보다 먼저
      if (drag && drag.id === c.id && drag.rect && drag.kind !== "box") return drag.rect;
      if (nudge && nudge.id === c.id) return nudge.rect;
      if (sent && sent.id === c.id) return sent.rect;
      return c.rect;
    }

    function overridden() {
      return !!((drag && drag.rect && (drag.kind === "move" || drag.kind === "resize")) || nudge || sent);
    }

    function byName(a, b) {
      return a.name < b.name ? -1 : a.name > b.name ? 1 : 0;
    }

    function psdShapes(kinds) {
      // PSD 기준점과 갈라진 shape 이름(layout.psd 대 지금 스냅숏)
      const d = S.L.psd_diff;
      return new Set(d ? kinds.flatMap((k) => d[k] || []).map((x) => x.name) : []);
    }

    function currentChecks(c) {
      if (!overridden()) return c.checks || [];
      const lay = { canvas: S.L.canvas, crops: S.L.crops.map((o) => Object.assign({}, o, { rect: rectOf(o) })) };
      return G.geomChecks(lay)[c.id].concat((c.checks || []).filter((x) => SERVER_CODES.test(x.code)));
    }

    function sizeText(r) {
      // 끄는 동안 사각형 아래·속성 패널: "512×384 · GPT 1024×768(k 2) · 비 1.33"(GPT = store.gpt_output_size user_k)
      const w = r[2] - r[0];
      const h = r[3] - r[1];
      const g = G.gptSize(w, h);
      return `${w}×${h} · GPT ${g.size[0]}×${g.size[1]}(k ${Number(g.k.toFixed(3))}) · 비 ${(Math.max(w, h) / Math.min(w, h)).toFixed(2)}`;
    }

    function nextNnn() {
      const used = S.L.crops.map((c) => Number(G.splitNnn(c.name)[0]) || 0);
      const n = used.length ? Math.max(...used) + 1 : 1;
      return String(n <= 999 ? n : 1).padStart(3, "0");
    }

    // ── 이름 칸: 번호 · 부위 · 좌우 → NNN_부위[_좌우] ──
    function nameFields(c) {
      // 지금 이름을 칸으로 나눈다. token = 원래 좌우 글자(옛 _left/_right 는 좌우를 바꿀 때만 한글로)
      const name = c ? c.name : `${nextNnn()}_`;
      const [nnn, rest] = G.splitNnn(name);
      const side = G.nameSide(name) || "";
      const i = rest.lastIndexOf("_");
      return { nnn, part: side ? rest.slice(0, Math.max(i, 0)) : rest, side, token: side ? rest.slice(i + 1) : "" };
    }

    function composeName(c) {
      const f = nameFields(c);
      const nnn = "nnn" in edits ? edits.nnn : f.nnn;
      const part = "part" in edits ? edits.part : f.part;
      const side = "side" in edits ? edits.side : f.side;
      const token = side === f.side ? f.token : { left: "왼쪽", right: "오른쪽" }[side] || "";
      return [nnn, part, token].filter(Boolean).join("_");
    }

    function nameIssue(name, selfId) {
      // 이름 규칙(G.checkName) + 레이아웃 안 유일(NFC·대소문자 무시 — 서버가 최종 판정)
      const r = G.checkName(name);
      if (r.err) return { msg: G.NAME_MSGS[r.err] };
      const dup = S.L.crops.find((o) => o.id !== selfId && G.fold(o.name) === G.fold(r.name));
      return dup ? { msg: `같은 이름의 크롭이 이미 있습니다: ${dup.name}` } : { name: r.name };
    }

    // ── 그리기(뷰어 svg, 캔버스 좌표) ──
    function draw(root, z) {
      const L = S.L;
      if (!on || !L || !L.exists) return;
      const u = 1 / z; // 화면 1px 의 캔버스 길이
      const add = (tag, attrs, text) => {
        const e = svg(tag, attrs);
        if (text !== undefined) e.textContent = text;
        root.append(e);
        return e;
      };
      const box = (r, cls, extra) => add("rect", Object.assign({ x: r[0], y: r[1], width: r[2] - r[0], height: r[3] - r[1], class: cls }, extra));
      const label = (x, y, text, cls) => add("text", { x, y, class: cls, "font-size": 11 * u, "stroke-width": 3 * u }, text);
      const defs = svg("defs");
      const pat = svg("pattern", { id: "dp-hatch", patternUnits: "userSpaceOnUse", width: 8 * u, height: 8 * u, patternTransform: "rotate(45)" });
      pat.append(svg("rect", { width: 3 * u, height: 8 * u, class: "hatch" }));
      defs.append(pat);
      root.append(defs);
      const crops = L.crops.map((c) => ({ c, r: rectOf(c) }));
      // 반영 안 된 매니페스트 사각형(유령)
      for (const { c, r } of crops) if (c.manifest && !sameRect(c.manifest.rect, r)) box(c.manifest.rect, "ghost");
      for (const g of L.removed) box(g.rect, "ghost gone");
      // 겹침 빗금(선택 크롭과 겹친 것만 진하게)
      crops.forEach((a, i) => {
        for (const b of crops.slice(i + 1)) {
          const o = [Math.max(a.r[0], b.r[0]), Math.max(a.r[1], b.r[1]), Math.min(a.r[2], b.r[2]), Math.min(a.r[3], b.r[3])];
          if (o[2] > o[0] && o[3] > o[1]) box(o, "ovl" + (a.c.id === sel || b.c.id === sel ? " on" : ""));
        }
      });
      const named = viewer.flags().rectsOn;
      for (const { c, r } of crops) {
        const bad = (c.checks || []).some((x) => x.code === "L-ratio" || x.code === "L-outside");
        box(r, "lc" + (c.id === sel ? " sel" : "") + (c.status === "new" ? " new" : c.status === "changed" ? " changed" : "") + (bad ? " bad" : ""));
        if (named && (r[2] - r[0]) * z >= 18) label(r[0] + 2 * u, r[1] - 4 * u, c.name, "lname" + (c.id === sel ? " sel" : ""));
      }
      const cur = sel && S.crop(sel);
      if (cur) {
        const r = rectOf(cur);
        const gx = (r[2] - r[0]) * 0.15;
        const gy = (r[3] - r[1]) * 0.15;
        box([r[0] + gx, r[1] + gy, r[2] - gx, r[3] - gy], "guide"); // 70% 가이드: 부품이 이 안에 들어오게
        cur.targets.forEach((t, i) => {
          if (!t.box) return;
          box(t.box, "pbox", { stroke: boxColor(t.tid, i) });
          label(t.box[0] + 2 * u, t.box[1] + 12 * u, t.tid, "ptid");
        });
        if (!drag || drag.kind === "box") {
          const pts = handlePoints(r);
          for (const h of HANDLES) box([pts[h][0] - 4 * u, pts[h][1] - 4 * u, pts[h][0] + 4 * u, pts[h][1] + 4 * u], "handle");
        }
      }
      if (proposal) {
        box(proposal.rect, "prop");
        label(proposal.rect[0], proposal.rect[3] + 14 * u, `제안 ${sizeText(proposal.rect)} — Enter 적용 · Esc 취소`, "linfo");
      }
      if (draft) box(draft.rect, "draft");
      if (drag && drag.rect && drag.kind === "box") box(drag.rect, "pbox", { stroke: boxColor(drag.tid, 0) });
      const moving = drag && drag.rect && drag.kind !== "box" ? drag.rect : nudge ? nudge.rect : null;
      if (moving) {
        if (drag && drag.kind === "new") box(moving, "draft");
        label(moving[0], moving[3] + 14 * u, sizeText(moving), "linfo");
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

    function hits(pt) {
      const area = (r) => (r[2] - r[0]) * (r[3] - r[1]);
      return S.L.crops.filter((c) => {
        const r = rectOf(c);
        return pt[0] >= r[0] && pt[0] < r[2] && pt[1] >= r[1] && pt[1] < r[3];
      }).sort((a, b) => area(rectOf(a)) - area(rectOf(b)));
    }

    // ── 마우스(뷰어가 왼쪽 버튼을 먼저 넘김, true = 소비) ──
    function pointerdown(e) {
      if (!editable()) return false;
      flushNudge();
      if (S.busy) return true; // 응답 전에는 다음 끌기를 막는다(쓰기는 rev 순서대로 하나씩)
      const pt = viewer.canvasPoint(e);
      const base = { start: pt, x: e.clientX, y: e.clientY, rect: null, moved: false, since: S.remote };
      const cur = sel && S.crop(sel);
      if (boxTid && cur) {
        drag = Object.assign(base, { kind: "box", id: cur.id, tid: boxTid });
        return true;
      }
      const h = cur && handleAt(pt, cur.rect);
      if (h) {
        drag = Object.assign(base, { kind: "resize", id: cur.id, handle: h, rect0: cur.rect });
        return true;
      }
      const hs = hits(pt);
      if (hs.length) {
        // 겹친 크롭: 선택한 크롭을 다시 누르면(끌지 않고 놓으면) 다음으로 작은 크롭
        const i = hs.findIndex((c) => c.id === sel);
        const c = i >= 0 ? hs[i] : hs[0];
        if (i < 0) select(c.id, false);
        drag = Object.assign(base, { kind: "move", id: c.id, rect0: c.rect, next: i >= 0 ? hs[(i + 1) % hs.length].id : null });
        return true;
      }
      drag = Object.assign(base, { kind: "new" });
      return true;
    }

    function pointermove(e) {
      if (!editable()) return false;
      const pt = viewer.canvasPoint(e);
      if (!drag) {
        const cur = sel && S.crop(sel);
        const h = cur && !boxTid && handleAt(pt, cur.rect);
        viewer.setCursor(h ? CURSORS[h] : !boxTid && hits(pt).length ? "move" : "");
        return false;
      }
      if (!drag.moved && Math.hypot(e.clientX - drag.x, e.clientY - drag.y) < CLICK_PX) return true;
      drag.moved = true;
      const dx = pt[0] - drag.start[0];
      const dy = pt[1] - drag.start[1];
      const snap = !(e.ctrlKey || e.metaKey); // Ctrl = 스냅 없음(1px)
      const C = S.L.canvas;
      if (drag.kind === "move") drag.rect = G.move(drag.rect0, dx, dy, C, snap);
      else if (drag.kind === "resize") drag.rect = G.resize(drag.rect0, drag.handle, dx, dy, C, snap, e.shiftKey);
      else if (drag.kind === "new") drag.rect = G.newRect(drag.start[0], drag.start[1], dx, dy, C, snap, e.shiftKey);
      else {
        const cl = (v, m) => Math.min(Math.max(G.rnd(v, 1), 0), m);
        drag.rect = [cl(Math.min(drag.start[0], pt[0]), C[0]), cl(Math.min(drag.start[1], pt[1]), C[1]),
          cl(Math.max(drag.start[0], pt[0]), C[0]), cl(Math.max(drag.start[1], pt[1]), C[1])];
      }
      liveUpdate();
      viewer.redraw();
      return true;
    }

    function pointerup() {
      const d = drag;
      drag = null;
      if (!d) return;
      if (!d.moved) {
        if (d.kind === "move" && d.next) select(d.next, false);
        else if (d.kind === "new") select(null, false);
        else viewer.redraw();
        return;
      }
      if (d.kind === "new") {
        if (badRect(d.rect)) {
          render();
          return;
        }
        draft = { rect: d.rect };
        select(null, false);
        const part = panel.querySelector('[data-k="part"]');
        if (part) part.focus();
        return;
      }
      if (d.kind === "box") {
        boxTid = null;
        if (d.rect[2] - d.rect[0] >= 2 && d.rect[3] - d.rect[1] >= 2) setBox(S.crop(d.id), d.tid, d.rect);
        else render();
        return;
      }
      const c = S.crop(d.id);
      if (!c || sameRect(d.rect, c.rect)) {
        render();
        return;
      }
      sendRect(c, d.rect, d.since);
    }

    function cancel() {
      drag = null;
      liveUpdate();
      viewer.redraw();
    }

    // ── 쓰기 ──
    async function act(p, mine) {
      // mine = 이 쓰기가 놓은 sent — 큐에서 앞 쓰기가 먼저 끝나도 뒤 쓰기의 사각형은 남긴다
      renderHead(); // 저장 중 표시
      try {
        const r = await p;
        if (r && r.warning) opts.toast(r.warning, "warn"); // 쓰기는 됨 — 서버가 응답(크롭 행·검사)을 만들지 못함
        return r;
      } catch (e) {
        opts.toast(e.message, "err");
        return null;
      } finally {
        if (sent === mine) sent = null;
        if (S.err) seen = null; // 다시 읽기 실패 — 다음 state 에서 다시 받는다
        showNotice();
        syncSel(false);
        renderKeepTyping();
      }
    }

    function renderKeepTyping() {
      // 저장 응답 뒤: 패널 입력칸에서 입력·한글 조합 중이면 패널은 입력이 끝난 뒤(flushPanel) 그린다 — 다른 칸의 조합·커서를 끊지 않게
      if (!typingInPanel()) {
        render();
      } else if (on) {
        pendingPanel = true;
        renderHead();
        renderList();
        viewer.redraw();
      }
    }

    function showNotice() {
      if (S.notice) opts.toast(S.notice, "warn");
      S.notice = null;
    }

    function badRect(rect) {
      // 서버 check_layout_rect 와 같은 규칙(G5)으로 보내기 전에 막는다
      const p = G.rectProblem(rect, S.L.canvas);
      if (p) opts.toast(RECT_MSGS[p], "err");
      return !!p;
    }

    function sendRect(c, rect, since) {
      // 사각형 update 하나 — 응답 전까지 놓은 사각형을 보인다
      if (badRect(rect)) {
        render();
        return null;
      }
      sent = { id: c.id, rect };
      return act(S.update(c.id, { rect }, since), sent);
    }

    async function commitTargets(c, mutate) {
      // mutate(ts) 는 큐에서 실행할 때의 최신 대상 목록에 적용한다(저장 응답 전에 다른 대상을 고쳐도 앞 변경이 남음)
      await flushNudge();
      return act(S.update(c.id, (cur) => {
        const ts = plainTargets(cur);
        mutate(ts, cur);
        return JSON.stringify(ts) === JSON.stringify(plainTargets(cur)) ? null : { targets: ts };
      }));
    }

    function setBox(c, tid, b) {
      return commitTargets(c, (ts) => {
        ts.find((t) => t.tid === tid).box = b;
      });
    }

    async function commitName(c) {
      // 보낼 이름과 그 입력값을 기다리기 전에 잡는다 — 미세 이동 저장·이름 응답을 기다리는 사이 다른 크롭을 골라 쓰거나 같은 칸을
      // 더 고쳐도(공유 edits) 섞이거나 지워지지 않게
      const name = composeName(c);
      const sent = {};
      for (const k of ["nnn", "part", "side"]) if (k in edits) sent[k] = edits[k];
      await flushNudge();
      const issue = nameIssue(name, c.id);
      if (issue.msg) {
        opts.toast(issue.msg, "err");
        return;
      }
      if (!(await act(S.update(c.id, (cur) => (cur.name === issue.name ? null : { name: issue.name }))))) return;
      if (sel === c.id) for (const [k, v] of Object.entries(sent)) if (edits[k] === v) delete edits[k]; // 보낸 뒤 바뀌지 않은 칸만
      renderKeepTyping();
    }

    async function commitText(c, tid, raw) {
      const k = "t-" + tid;
      if (!(k in edits)) return; // Esc 로 되돌렸거나 이미 보낸 칸
      const r = G.checkText(raw);
      if (r.err) {
        opts.toast(G.TEXT_MSGS[r.err], "err");
        return;
      }
      delete edits[k]; // 보내는 중 — 응답 전에 크롭을 바꿔도(select) 다시 보내지 않게
      if (!(await commitTargets(c, (ts) => { ts.find((t) => t.tid === tid).text = r.text; })) && sel === c.id) {
        edits[k] = raw; // 실패 — 입력값을 되살림
      }
      renderKeepTyping();
    }

    async function commitNote(c, value) {
      if (!("note" in edits)) return;
      delete edits.note;
      await flushNudge();
      if (!(await act(S.update(c.id, (cur) => (cur.note === value ? null : { note: value })))) && sel === c.id) edits.note = value;
      renderKeepTyping();
    }

    function commitPending() {
      // 다른 크롭으로 옮기기 전: 입력 중이던 대상 문구·메모를 그 크롭에 저장한다(쓰기 큐). 늦게 오는 change 는 edits 에 키가 없어 끝난다
      const c = sel && S.crop(sel);
      if (!c) return;
      const texts = {}; // tid → 입력 중이던 문구(검사 통과 — 바뀌었는지는 큐에서 최신 크롭과 비교)
      for (const t of plainTargets(c)) {
        const k = "t-" + t.tid;
        if (!(k in edits)) continue;
        const r = G.checkText(edits[k]);
        if (r.err) opts.toast(`${c.name} ${t.tid}: ${G.TEXT_MSGS[r.err]} — 저장하지 않음`, "err");
        else texts[t.tid] = r.text;
      }
      if (Object.keys(texts).length) {
        commitTargets(c, (ts) => {
          for (const t of ts) if (t.tid in texts) t.text = texts[t.tid];
        });
      }
      if ("note" in edits) {
        const value = edits.note;
        act(S.update(c.id, (cur) => (cur.note === value ? null : { note: value })));
      }
    }

    function addTarget(c) {
      // 다음 빈 글자 — 채택 문구만 있는 글자(adopted_only)는 건너뜀(그 글자로 더하면 빈 레이아웃 문구가 채택 문구를 덮음)
      // 글자는 큐에서 실행할 때 고른다(연달아 누르면 앞 추가가 반영된 목록에서 다음 글자)
      return commitTargets(c, (ts, cur) => {
        const used = new Set(ts.concat(cur.adopted_only).map((t) => t.tid));
        const tid = [..."ABCDEFGHIJKLMNOPQRSTUVWXYZ"].find((x) => !used.has(x));
        if (!tid) throw new Error("대상은 26개(A–Z)까지입니다");
        ts.push({ tid, text: "", box: null });
      });
    }

    async function createDraft() {
      const d = draft;
      const name = composeName(null); // 기다리기 전에 — 그 사이 다른 크롭을 골라 쓴 이름(공유 edits)으로 만들지 않게
      await flushNudge();
      const issue = nameIssue(name, null);
      if (issue.msg) {
        opts.toast(issue.msg, "err");
        return;
      }
      if (badRect(d.rect)) return;
      const r = await act(S.add({ name: issue.name, rect: d.rect }));
      if (!r) return;
      draft = null;
      if (!r.crop) {
        render();
        return;
      }
      select(r.crop.id, false);
      const orphan = (r.crop.checks || []).find((x) => x.code === "H-orphan");
      opts.toast(`새 크롭 ${r.crop.name}` + (orphan ? ` — ${orphan.msg}` : ""), orphan ? "warn" : "");
    }

    function againText(c) {
      // 지운 뒤 같은 이름으로 다시 만들면 이어지는가(서버 same_name_continues = 이름 규칙·유일 통과 + 그 이름의 ID 가 지금 ID)
      if (nameIssue(c.name, c.id).msg) return "파일은 남습니다. 이 이름은 규칙 밖이거나 다른 크롭과 겹쳐 같은 이름으로 다시 만들 수 없음 — 되살리려면 Ctrl+Z(이 창에서만).";
      if (c.same_name_continues) return "파일은 남고, 같은 이름으로 다시 만들면 이어집니다.";
      return `파일은 남습니다. 이 크롭은 ID 가 처음 이름 기준(${c.id})이라 같은 이름으로 다시 만들어도 새 ID — 후보·★·스펙이 이어지지 않습니다. `
        + "되살리려면 Ctrl+Z(이 창에서만).";
    }

    async function removeSel() {
      await flushNudge();
      const c = sel && S.crop(sel);
      if (!c) return;
      const h = c.history || {};
      const what = [`후보 ${h.candidates || 0}`, `★ ${h.picks || 0}`].concat(h.spec ? ["스펙 있음"] : []).join(" · ");
      if (!window.confirm(`${c.name} 크롭을 레이아웃에서 지웁니다.\n${what} — ${againText(c)}\n`
        + "매니페스트에는 다음 큐의 Import 에서 반영됩니다. 계속할까요?")) return;
      if (await act(S.remove(c.id))) opts.toast(`지움: ${c.name} (Ctrl+Z 로 되살림)`);
    }

    function flushNudge() {
      // 대기 중인 미세 이동을 지금 보낸다(쓰기 큐 — 뒤따르는 쓰기보다 먼저) → act 의 promise, 보낼 것이 없으면 null
      const n = nudge;
      if (!n) return null;
      clearTimeout(n.timer);
      nudge = null;
      const c = S.crop(n.id);
      if (!c || sameRect(c.rect, n.rect)) {
        render();
        return null;
      }
      return sendRect(c, n.rect, n.since);
    }

    function dropNudge() {
      clearTimeout(nudge.timer);
      nudge = null;
      liveUpdate();
      viewer.redraw();
    }

    function nudgeBy(e) {
      // 화살표 16px · Shift 128px · Ctrl 1px(스냅 없음). 연달아 누르면 멈춘 뒤 POST 한 번
      const c = sel && S.crop(sel);
      if (!c || S.busy) return;
      const step = e.ctrlKey || e.metaKey ? 1 : e.shiftKey ? 128 : G.POS;
      const d = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] }[e.code];
      const rect = G.move(nudge ? nudge.rect : c.rect, d[0], d[1], S.L.canvas, step !== 1);
      if (nudge) clearTimeout(nudge.timer);
      nudge = { id: c.id, rect, timer: setTimeout(flushNudge, NUDGE_MS), since: nudge ? nudge.since : S.remote };
      if (proposal) {
        proposal = null; // 옛 사각형 기준 제안
        render();
      } else {
        liveUpdate();
        viewer.redraw();
      }
    }

    async function undo() {
      if (nudge) {
        // 아직 보내지 않은 미세 이동이 이 Ctrl+Z 한 단계(서버 쓰기 없음)
        dropNudge();
        opts.toast("미세 이동 취소");
        return;
      }
      if (!S.undo.length) {
        opts.toast("되돌릴 편집이 없습니다", "warn");
        return;
      }
      if (await act(S.undoStep())) opts.toast(`실행 취소 (남은 ${S.undo.length} · 다시 ${S.redo.length})`);
    }

    async function redo() {
      await flushNudge();
      if (!S.redo.length) {
        opts.toast("다시 할 편집이 없습니다", "warn");
        return;
      }
      if (await act(S.redoStep())) opts.toast(`다시 실행 (남은 ${S.redo.length})`);
    }

    function propose() {
      const c = S.crop(sel);
      const boxes = c.targets.filter((t) => t.box).map((t) => t.box);
      if (!boxes.length) {
        opts.toast("부품 상자가 없습니다 — 대상 줄의 '상자'(P)로 먼저 그리세요", "warn");
        return;
      }
      proposal = { id: c.id, rect: G.suggest(boxes, S.L.canvas, true), since: S.remote };
      render();
    }

    async function applyProposal() {
      const p = proposal;
      proposal = null;
      await flushNudge();
      const c = S.crop(p.id);
      if (!c || sameRect(c.rect, p.rect)) {
        render();
        return;
      }
      sendRect(c, p.rect, p.since);
    }

    function armBox(tid) {
      const c = sel && S.crop(sel);
      if (!c) {
        opts.toast("크롭을 먼저 고르세요", "warn");
        return;
      }
      const ts = plainTargets(c);
      boxTid = tid || (ts.find((t) => !t.box) || ts[0]).tid;
      proposal = null;
      opts.toast(`${boxTid} 부품 상자: 크롭 안에서 끄세요 (Esc 취소)`);
      render();
    }

    function partial() {
      // 서버가 S1-a(기준점을 고른 이름만 갱신)인가 — 재시작 전 옛 서버는 take·ack 가 기준점을 지금 PSD 로 통째 바꿔 나머지 갈라짐 경고도 꺼진다
      return !opts.psdPartial || opts.psdPartial();
    }

    function psdSplit(names) {
      // take(names) 가 가져올 shape 와 나머지 — S1-a: 기준점은 고른 이름만 지금 PSD 값으로 바뀌어(같은 이름의 사라진 shape 도 경고만 꺼짐)
      // 나머지 갈라짐은 그대로 경고(옛 서버: 나머지는 가져오지 않고 경고만 꺼짐). 확인 창 줄
      const d = S.L.psd_diff;
      const part = partial();
      const take = [];
      const rest = [];
      for (const x of d.changed) {
        if (names.includes(x.name)) take.push(`${x.name} — ${fieldNames(x.fields)}`);
        else rest.push(`${x.name} — 바뀜(${fieldNames(x.fields)})`);
      }
      for (const x of d.added) {
        if (names.includes(x.name)) take.push(`${x.name} — PSD 에만 있는 shape 는 새 크롭으로 더함`);
        else rest.push(`${x.name} — 새 shape`);
      }
      for (const x of d.removed) {
        if (part && names.includes(x.name)) take.push(`${x.name} — 사라진 shape: 레이아웃 그대로, 경고만 꺼짐`);
        else rest.push(`${x.name} — 사라진 shape` + (part ? "(경고 끄기로)" : ": 레이아웃 그대로, 경고만 꺼짐"));
      }
      return { take, rest };
    }

    async function takePsd(names) {
      await flushNudge();
      const seen = S.L.psd_diff; // 미세 이동을 보낸 뒤 다시 읽은 갈라짐 — 확인 창과 서버에 보낼 스냅숏이 이것(그 사이 사라졌으면 끝)
      if (!seen) return;
      const { take, rest } = psdSplit(names);
      const lines = ["PSD 값 가져오기(실행 취소로 되돌릴 수 없음):", ...take.map((x) => ` · ${x}`)];
      if (rest.length) {
        lines.push(partial() ? `나머지 갈라짐 ${rest.length}건은 가져오지 않고 그대로 경고:`
          : `나머지 갈라짐 ${rest.length}건은 가져오지 않고 경고만 꺼집니다(기준점이 지금 PSD 로 바뀜 — ComfyUI 재시작 전 서버):`, ...rest.map((x) => ` · ${x}`));
      }
      if (!window.confirm(lines.concat("계속할까요?").join("\n"))) return;
      const r = await act(S.psd("take", names, seen));
      if (!r) return;
      const added = r.added.map((id) => (S.crop(id) || { name: id }).name);
      opts.toast([`PSD 값 ${r.taken.length}` + r.taken.map((x, i) => `${i ? ", " : ": "}${x.name}(${fieldNames(x.fields)})`).join(""),
        `새 크롭 ${added.length}` + (added.length ? `: ${added.join(", ")}` : ""),
        `건너뜀 ${r.skipped.length}` + (r.skipped.length ? `: ${r.skipped.join(", ")} — 레이아웃에서 지운 크롭이라 건너뜀` : "")].join(" · "),
      r.skipped.length ? "warn" : "");
    }

    async function ackPsd(names) {
      // names 없음 = 갈라짐 전부의 경고를 끔(기준점 = 지금 PSD 통째), names = 그 shape 들만(S1-a — 나머지는 그대로 경고)
      await flushNudge();
      const seen = S.L.psd_diff;
      if (!seen) return;
      const rest = names ? psdSplit(names).rest.length : 0;
      if (!window.confirm(names ? `${names.join(", ")} 의 갈라짐 경고만 끕니다(레이아웃 그대로, 그 shape 의 PSD 기준점만 지금 PSD 로)`
        + (rest ? ` — 나머지 갈라짐 ${rest}건은 그대로 경고` : "") + ". 계속할까요?"
        : "레이아웃은 그대로 두고 PSD 기준점만 지금 PSD 로 바꿉니다(갈라짐 경고를 모두 끔). 계속할까요?")) return;
      if (await act(S.psd("ack", names, seen))) opts.toast(names ? `PSD 기준점 갱신: ${names.join(", ")}` : "PSD 기준점을 갱신했습니다");
    }

    async function applyQueue() {
      await flushNudge();
      await S.idle(); // 저장 중인 이름·대상 편집이 끝난 뒤 — 그 전에 큐에 넣으면 Import 가 옛 판을 반영한다
      const n = S.L.pending_rerolls || 0;
      // 이 반영에 들어가는 크롭별 바뀜(실수로 끈 크롭이 확인 창에서 보이게 — 진행(G) 반영 칸에도 크롭별 줄·[되돌리기])
      const ch = S.L.crops.filter((c) => STATUS[c.status]).map((c) => `${c.name}(${c.status === "new" ? "새 크롭" : fieldNames(c.changed)})`)
        .concat(S.L.removed.map((c) => `${c.name || c.id}(지움)`));
      const lines = (opts.applyWarn ? opts.applyWarn() : [])
        .concat(ch.length ? [`이 반영에 들어가는 레이아웃 바뀜 ${ch.length}건: ${ch.join(" · ")} — 실수면 취소 → Ctrl+Z(이 창의 편집) 또는 진행(G) 반영 칸 [되돌리기]`] : [],
          "반영(큐): 무료 반영(Import → Prepare → Analyze → Review 템플릿, 탭 불필요)으로 레이아웃을 매니페스트에 반영합니다.");
      if (n) lines.push(`재굴림 대기 ${n}건(사전 승인)은 이 반영에서 돌지 않음 — R·ComfyUI 큐·그래프 Run 에서 과금`);
      if (!window.confirm(lines.concat(APPLY_NOTE, "계속할까요?").join("\n"))) return;
      opts.queue(true);
    }

    async function start() {
      await flushNudge();
      const r = await act(S.start());
      if (!r) return;
      const w = r.warnings || [];
      opts.toast(`레이아웃 rev ${r.rev} · 크롭 ${r.crops}` + (w.length ? ` — 경고 ${w.length}: ${w.join(" / ")}` : ""), w.length ? "warn" : "");
    }

    // ── 선택 ──
    function syncSel(follow) {
      // 뷰어의 선택(F = 선택 크롭 맞춤)을 지금 레이아웃 사각형으로. 사라진 크롭이면 선택 해제. 검토 모드의 선택은 건드리지 않는다
      if (!on) return;
      if (sel && !S.crop(sel)) sel = null;
      const c = sel && S.crop(sel);
      viewer.setSelection(c ? { crop: { id: c.id, rect: c.rect } } : null, follow);
    }

    function select(id, follow) {
      flushNudge();
      if (id !== sel) {
        commitPending();
        edits = {};
      }
      sel = id;
      boxTid = null;
      proposal = null;
      if (id) draft = null;
      syncSel(follow);
      render();
    }

    function cycle(d) {
      const ids = S.L.crops.slice().sort(byName).map((c) => c.id);
      if (!ids.length) return;
      const i = ids.indexOf(sel);
      select(ids[i < 0 ? (d > 0 ? 0 : ids.length - 1) : (i + d + ids.length) % ids.length], true);
    }

    function escape() {
      // Esc 차례: 끌기 취소 → 상자·제안·초안·미세 이동 취소 → 선택 해제 → 편집 모드 나가기
      if (drag) {
        cancel();
      } else if (boxTid || proposal || draft) {
        boxTid = proposal = draft = null;
        render();
      } else if (nudge) {
        dropNudge();
      } else if (sel) {
        select(null, false);
      } else {
        opts.exit();
      }
    }

    function onKey(e) {
      // board.js 가 편집 모드에서 먼저 넘긴다(입력칸 포커스는 board.js 가 이미 거름). true = 소비
      if (!on) return false;
      const mod = e.ctrlKey || e.metaKey;
      if (mod && (e.code === "KeyZ" || e.code === "KeyY")) {
        if (!e.repeat) (e.code === "KeyY" || e.shiftKey ? redo : undo)();
        return true;
      }
      if (e.code === "Escape") {
        escape();
        return true;
      }
      if (!editable() || e.altKey) return false;
      if (e.code.startsWith("Arrow")) {
        nudgeBy(e);
        return true;
      }
      if (mod) return false;
      switch (e.code) {
        case "Tab":
          cycle(e.shiftKey ? -1 : 1);
          return true;
        case "Delete":
        case "Backspace":
          if (!e.repeat) removeSel();
          return true;
        case "Enter":
          if (draft) createDraft();
          else if (proposal) applyProposal();
          else return false;
          return true;
        case "KeyP":
          if (!e.repeat) armBox(null);
          return true;
        default:
          return false;
      }
    }

    // ── 머리줄·목록·속성 패널 ──
    function render() {
      if (!on) return;
      renderHead();
      renderList();
      renderPanel();
      viewer.redraw();
    }

    function renderHead() {
      head.replaceChildren();
      const L = S.L;
      if (!L || !L.exists) {
        head.append(el("span", "lh-title", "레이아웃 편집"));
      } else if (L.error) {
        // 깨진 layout.json: 판·반영·실행 취소가 없다(이유는 속성 패널)
        const b = el("span", "lh-bad", "layout.json 오류");
        b.title = L.error;
        head.append(el("span", "lh-title", "레이아웃 편집"), b);
      } else {
        const k = L.crops.filter((c) => c.status !== "same").length + L.removed.length;
        // 반영됨 = 마지막 Import 가 읽은 판의 sig 가 지금과 같음(서버 applied — 메모·상자만 고친 판도 반영된 것), 옛 서버는 rev 비교
        const applied = "applied" in L ? L.applied : L.applied_rev === L.rev;
        head.append(el("span", "lh-title", `레이아웃 rev ${L.rev}`));
        const st = el("span", applied ? "lh-ok" : "lh-wait", applied ? "반영됨" : `미반영 ${k}`);
        st.title = applied ? "마지막 Import 가 이 판을 읽었습니다" : `매니페스트 반영 rev ${L.applied_rev ?? "없음"} — 다음 큐의 Import 에서 반영`;
        head.append(st, button("반영(큐)", "무료 반영(Import → Prepare → Analyze → Review 템플릿, 탭 불필요) — 레이아웃을 매니페스트에 반영",
          applyQueue));
        const d = L.psd_diff;
        const nd = d ? d.changed.length + d.added.length + d.removed.length : 0;
        if (nd) {
          const p = el("span", "lh-bad", `PSD 갈라짐 ${nd}`);
          p.title = [...d.changed.map((x) => `바뀜 ${x.name} [${x.from.rect}]→[${x.to.rect}]`), ...d.added.map((x) => `새 shape ${x.name}`),
            ...d.removed.map((x) => `사라진 shape ${x.name}`)].join("\n");
          head.append(p);
          const names = [...psdShapes(["changed", "added"])];
          if (names.length) {
            head.append(button("PSD 값 가져오기", "갈라진 shape 의 PSD 값(바뀐 필드만)을 레이아웃으로 · 새 shape 는 새 크롭 — 사라진 shape 는 "
              + (partial() ? "경고 끄기로" : "경고만 꺼짐"), () => takePsd(names)));
          }
          head.append(button("경고 끄기", "레이아웃은 그대로, PSD 기준점만 지금 PSD 로", ackPsd));
        }
        const ub = button("되돌리기", "실행 취소 (Ctrl+Z)", undo);
        ub.disabled = !S.undo.length;
        const rb = button("다시", "다시 실행 (Ctrl+Y · Ctrl+Shift+Z)", redo);
        rb.disabled = !S.redo.length;
        head.append(ub, rb);
      }
      if (S.busy) head.append(el("span", "lh-wait", "저장 중…"));
      if (S.err && L) head.append(el("span", "lh-bad", `읽기 오류: ${S.err}`));
    }

    function renderList() {
      list.replaceChildren();
      const L = S.L;
      if (!L || !L.exists) return;
      const split = psdShapes(["changed", "removed"]);
      for (const c of L.crops.slice().sort(byName)) {
        const li = el("li", "lcrop" + (c.id === sel ? " sel" : ""));
        li.append(el("div", "tname", c.name));
        const ch = el("div", "chips");
        if (STATUS[c.status]) {
          const x = chip(STATUS[c.status] + (c.changed && c.changed.length ? " " + fieldNames(c.changed) : ""), "b-wait");
          x.title = "매니페스트와 다름 — 다음 큐의 Import 에서 반영";
          ch.append(x);
        }
        if (split.has(c.psd_name || c.name)) ch.append(chip("PSD 갈라짐", "b-bad"));
        for (const [lv, cls] of [["error", "b-bad"], ["warn", "b-wait"]]) {
          const rows = (c.checks || []).filter((x) => x.level === lv);
          if (rows.length) {
            const x = chip(`${LEVELS[lv]} ${rows.length}`, cls);
            x.title = rows.map((r) => r.msg).join("\n");
            ch.append(x);
          }
        }
        const h = c.history;
        if (h && (h.candidates || h.picks)) ch.append(chip(`후보 ${h.candidates}` + (h.picks ? ` · ★ ${h.picks}` : ""), h.picks ? "b-star" : ""));
        li.append(ch);
        li.addEventListener("click", () => select(c.id, true));
        list.append(li);
      }
      for (const g of L.removed) {
        const li = el("li", "lcrop gone");
        li.append(el("div", "tname", g.name));
        const ch = el("div", "chips");
        ch.append(chip("지움(미반영)", "b-dim"));
        if (g.candidates || g.picks) ch.append(chip(`후보 ${g.candidates} · ★ ${g.picks}`, "b-dim"));
        li.append(ch);
        list.append(li);
      }
      const cur = list.querySelector("li.sel");
      if (cur) cur.scrollIntoView({ block: "nearest" });
    }

    function blocked() {
      if (!state || !state.canvas) return "베이스가 없습니다 — Import PSD 를 먼저 실행하세요(레이아웃 편집 불가).";
      if (!S.L) return S.err ? `레이아웃을 읽지 못했습니다: ${S.err}` : "불러오는 중…";
      if (S.L.error) return `layout.json 을 읽을 수 없어 편집할 수 없습니다: ${S.L.error}`;
      if (S.L.exists && !S.L.editable) return "이 레이아웃은 편집할 수 없습니다(베이스·캔버스 확인).";
      return null;
    }

    function renderPanel() {
      // 다시 그린 뒤 포커스를 돌려주는 것은 글자 입력칸만 — 좌우 select 에 돌려주면 화살표가 크롭 대신 좌우를 바꾼다
      const a = document.activeElement;
      const fk = panel.contains(a) && textField(a) ? a.dataset.k : null;
      pendingPanel = false;
      panel.replaceChildren();
      live = null;
      nameShow = null;
      const msg = blocked();
      if (msg) panel.append(el("p", "lp-msg", msg));
      else if (!S.L.exists) renderStart();
      else if (draft) renderDraft();
      else if (sel && S.crop(sel)) renderCrop(S.crop(sel));
      else renderNone();
      const f = fk && panel.querySelector(`[data-k="${fk}"]`);
      if (f) f.focus();
    }

    function renderStart() {
      // start_blocked = 서버가 이관을 거절하는 이유(레이아웃 모드 결과 매니페스트에 믿을 수 있는 PSD 스냅숏이 없음 등)
      const why = S.L.start_blocked;
      panel.append(el("h3", "lp-title", "레이아웃 편집 시작"));
      if (why) panel.append(el("p", "warn", why));
      else panel.append(el("p", "", `매니페스트 크롭 ${state.crops.length}개(마지막 PSD 가져오기 결과)를 layout.json 으로 옮깁니다 — ID·사각형·이름·대상 문구 그대로.`));
      panel.append(el("p", "warn", "이후 크롭의 사각형·이름·대상은 Studio 가 원천입니다. PS 에서 사각형을 옮겨도 반영되지 않고 경고만 납니다('PSD 값 가져오기'로 맞춤)."));
      panel.append(el("p", "dim", "PS 만 쓰려면 시작하지 마세요 — 지금처럼 동작합니다. 크롭이 없어도(빈 시작) 여기서 새로 그릴 수 있습니다."));
      const b = el("div", "lp-btns");
      const go = button("레이아웃 편집 시작", "POST layout/start — 매니페스트 크롭을 layout.json 으로 이관", start, "primary");
      go.disabled = !!why;
      b.append(go);
      panel.append(b);
    }

    function renderNone() {
      const L = S.L;
      panel.append(el("h3", "lp-title", "레이아웃"));
      row("크롭", `${L.crops.length}개 · 캔버스 ${L.canvas[0]}×${L.canvas[1]}`);
      const all = L.crops.flatMap((c) => c.checks || []);
      row("검사", ["error", "warn", "info"].map((lv) => `${LEVELS[lv]} ${all.filter((x) => x.level === lv).length}`).join(" · "));
      for (const t of ["크롭 누르기 = 선택(겹치면 다시 눌러 다음으로 작은 크롭) · Tab / Shift+Tab = 다음·이전",
        "빈 곳에서 끌기 = 새 크롭 · 몸통 끌기 = 이동 · 핸들 = 크기(Shift = 비율 유지)",
        "Ctrl 을 누른 채 끌기 = 스냅 없음(1px) · 크기 128 배수·위치 16px 격자 · 비 3:1 이하·캔버스 안",
        "Space 를 누른 채 끌기·가운데 버튼 = 화면 이동 · 화살표 16px · Shift 128px · Ctrl 1px",
        "Delete = 지우기 · Ctrl+Z / Ctrl+Y = 실행 취소·다시 · Esc = 취소 → 선택 해제 → 편집 모드 나가기"]) {
        panel.append(el("p", "dim", t));
      }
    }

    function nameBlock(c) {
      const f = nameFields(c);
      const nnn = input("nnn", f.nnn, "번호", "nnn");
      nnn.maxLength = 3;
      nnn.inputMode = "numeric";
      const part = input("part", f.part, "부위(예: 모자본체_휘장)", "part");
      const side = el("select");
      side.dataset.k = "side";
      side.tabIndex = -1;
      for (const [v, t] of [["", "좌우 없음"], ["left", "왼쪽"], ["right", "오른쪽"]]) {
        const o = el("option", "", t);
        o.value = v;
        side.append(o);
      }
      side.value = "side" in edits ? edits.side : f.side;
      const prev = el("div", "lp-preview");
      const note = el("div", "lp-note");
      const after = el("div", "lp-row"); // 바꾼 뒤 레퍼런스 — renderCrop 이 레퍼런스 줄 아래에 붙인다
      const av = el("span");
      after.append(el("span", "k", "바꾼 뒤"), av);
      const show = () => {
        const name = composeName(c);
        const issue = nameIssue(name, c ? c.id : null);
        prev.textContent = name || "—";
        prev.classList.toggle("bad", !!issue.msg);
        nnn.classList.toggle("bad", !/^[0-9]{3}$/.test(nnn.value));
        part.classList.toggle("bad", !part.value || G.checkName(`000_${part.value}`).err === "char"); // 금지 문자는 입력 즉시
        const renamed = !!c && !issue.msg && name !== c.name;
        const refs = renamed && S.L.refs_dir ? refsAfter(name, c.id) : null;
        after.hidden = !refs;
        after.classList.toggle("warn", !!refs && !refs.n);
        if (refs) av.textContent = (refs.n ? `${refs.n}개 · 첫 ${refs.first}` : "없음") + (refs.fixed ? " · 연결 유지(ID)" : "");
        note.className = "lp-note " + (issue.msg ? "bad" : "warn");
        note.textContent = issue.msg || (renamed ? renameNote(c.refs, refs, c.history.by_file) : "");
      };
      nameShow = show;
      for (const [i, k] of [[nnn, "nnn"], [part, "part"]]) {
        i.addEventListener("input", () => {
          edits[k] = i.value;
          show();
        });
        i.addEventListener("keydown", (e) => {
          // Enter = 적용, Esc = 되돌림. 둘 다 입력칸을 떠난다(다시 그려도 포커스를 돌려주지 않게) → 캔버스 단축키가 바로 듦.
          // 한글 조합을 확정하는 Enter(isComposing)는 조합만 끝낸다
          if (e.isComposing || e.keyCode === 229) return;
          if (e.key === "Enter") {
            e.preventDefault();
            i.blur();
            if (c) commitName(c);
            else createDraft();
          } else if (e.key === "Escape") {
            e.preventDefault();
            i.blur();
            if (c) for (const x of ["nnn", "part", "side"]) delete edits[x];
            else draft = null;
            render();
          }
        });
      }
      side.addEventListener("change", () => {
        side.blur(); // 포커스가 select 에 남으면 화살표가 크롭 대신 좌우를 바꾼다
        edits.side = side.value;
        show();
        if (c) commitName(c);
      });
      const r = el("div", "lp-name");
      r.append(nnn, el("span", "dim", "_"), part, side);
      panel.append(el("div", "lp-k", "이름 (번호 · 부위 · 좌우)"), r, prev, note);
      show();
      return after;
    }

    function refsAfter(name, id) {
      // 바꾸는 중인 이름의 레퍼런스: 입력이 멈추면 GET layout/refs 한 번(크롭·이름마다). 받기 전 = undefined, 못 받음 = null
      if (refsAt.name === name && refsAt.id === id) return refsAt.refs;
      clearTimeout(refsAt.timer);
      const at = { name, id, refs: undefined, timer: null };
      at.timer = setTimeout(async () => {
        try {
          at.refs = await S.refs(name, id);
        } catch (e) {
          at.refs = null;
        }
        if (refsAt === at && nameShow) nameShow();
      }, REFS_MS);
      refsAt = at;
      return undefined;
    }

    function renderDraft() {
      panel.append(el("h3", "lp-title", "새 크롭"));
      nameBlock(null);
      row("크기·GPT", sizeText(draft.rect));
      const b = el("div", "lp-btns");
      b.append(button("만들기 (Enter)", "이름으로 크롭을 만듦(add)", createDraft, "primary"), button("취소 (Esc)", "", () => {
        draft = null;
        render();
      }));
      panel.append(b);
    }

    function renderCrop(c) {
      const title = el("h3", "lp-title", c.name);
      title.title = `ID ${c.id} — 이름을 바꿔도 유지(스펙·후보·★ 가 이어짐). 다른 부품으로 바꾸려면 지우고 새로 만드세요.`;
      panel.append(title);
      const after = nameBlock(c);
      const id = row("ID", c.id, "dim");
      id.title = "이름을 바꿔도 유지";
      if (S.L.refs_dir) {
        const f = c.refs || { n: 0 };
        row("레퍼런스", f.n ? `${f.n}개 · 첫 ${f.first}` : "없음", f.n ? "" : "warn");
        panel.append(after);
      }
      if (c.manifest && c.status === "changed") row("매니페스트", `${c.manifest.name} [${c.manifest.rect.join(", ")}]`, "dim");
      targetsBlock(c);
      const note = el("textarea", "lp-memo");
      note.dataset.k = "note";
      note.maxLength = 500;
      note.rows = 2;
      note.placeholder = "메모(파이프라인은 쓰지 않음)";
      note.value = "note" in edits ? edits.note : c.note || "";
      note.addEventListener("input", () => {
        edits.note = note.value;
      });
      note.addEventListener("change", () => commitNote(c, note.value));
      panel.append(el("div", "lp-k", "메모"), note);
      const gpt = row("크기·GPT", sizeText(rectOf(c)));
      const checks = el("ul", "lp-checks");
      panel.append(el("div", "lp-k", "검사"), checks);
      live = { gpt, checks };
      checks.append(...checkRows(c));
      if (proposal && proposal.id === c.id) row("제안", `[${proposal.rect.join(", ")}] ${sizeText(proposal.rect)} — Enter 적용 · Esc 취소`, "warn");
      const b = el("div", "lp-btns");
      b.append(button("상자에 맞춤", "부품 상자 합집합 ÷ 0.7 → 128 배수 → 비 ≤ 3 → 캔버스 안(미리보기, Enter 적용)", propose));
      if (proposal && proposal.id === c.id) b.append(button("제안 적용", "Enter", applyProposal, "primary"));
      const pn = c.psd_name || c.name;
      const part = partial();
      if (psdShapes(["changed"]).has(pn)) {
        // S1-a: 이 이름의 기준점만 지금 PSD 값으로 — 다른 갈라짐은 그대로 경고. 옛 서버는 기준점이 통째로 바뀌어 다른 갈라짐 경고도 꺼짐(라벨에 그 수)
        const rest = part ? 0 : psdSplit([pn]).rest.length;
        b.append(button(rest ? `PSD 값으로(나머지 ${rest}건 경고 끔)` : "PSD 값으로", "이 크롭의 PSD 에서 바뀐 필드(사각형·대상)를 지금 PSD 값으로 — "
          + (part ? "다른 갈라짐은 그대로 경고" : rest ? `나머지 갈라짐 ${rest}건은 가져오지 않고 경고만 꺼짐(ComfyUI 재시작 전 서버)` : "갈라짐은 이것뿐"),
        () => takePsd([pn])));
      }
      if (part && psdShapes(["changed", "removed"]).has(pn)) {
        b.append(button("이 shape 경고 끄기", "레이아웃은 그대로, 이 shape 의 PSD 기준점만 지금 PSD 로(다른 갈라짐은 그대로 경고)",
          () => ackPsd([pn])));
      }
      b.append(button("지우기", "Delete", removeSel, "danger"));
      panel.append(b);
    }

    function targetsBlock(c) {
      const ts = plainTargets(c);
      const adopted = Object.fromEntries(c.targets.map((t) => [t.tid, t.adopted]));
      const tb = el("div", "lp-targets");
      ts.forEach((t, i) => {
        const k = "t-" + t.tid;
        const r = el("div", "lt");
        const tag = el("span", "lt-tid", t.tid);
        tag.style.borderColor = boxColor(t.tid, i);
        const inp = input(k, t.text, adopted[t.tid] ? `채택: ${adopted[t.tid]}` : "문구(영문 name_en 또는 한글 라벨)");
        const mark = () => {
          const v = G.checkText(inp.value);
          inp.classList.toggle("bad", !!v.err);
          inp.title = v.err ? G.TEXT_MSGS[v.err] : "";
        };
        mark();
        inp.addEventListener("input", () => {
          edits[k] = inp.value;
          mark();
        });
        inp.addEventListener("change", () => commitText(c, t.tid, inp.value));
        inp.addEventListener("keydown", (e) => {
          if (e.isComposing || e.keyCode === 229) return; // 한글 조합 확정 Enter 는 조합만
          if (e.key === "Enter") {
            e.preventDefault();
            inp.blur();
          } else if (e.key === "Escape") {
            e.preventDefault();
            delete edits[k];
            inp.blur();
            render();
          }
        });
        r.append(tag, inp);
        const bx = t.box ? `상자 ${t.box[2] - t.box[0]}×${t.box[3] - t.box[1]}` : "상자";
        r.append(button(bx, "부품 상자 그리기 — 크롭 안에서 끌기 (P)", () => armBox(t.tid), boxTid === t.tid ? "on" : ""));
        if (t.box) r.append(button("✕", `${t.tid} 상자 지우기`, () => setBox(c, t.tid, null)));
        if (adopted[t.tid]) {
          const a = chip("채택", "b-dim");
          a.title = `스펙 _layer_targets 채택 문구: ${adopted[t.tid]} — 여기 문구를 쓰면 이것이 이김`;
          r.append(a);
        }
        if (ts.length > 1) {
          r.append(button("−", `${t.tid} 대상 빼기`, () => commitTargets(c, (x) => {
            // tid 로 찾는다 — 앞서 기다리던 빼기가 반영된 목록에서는 그릴 때의 자리(i)가 다른 대상이다
            const at = x.findIndex((y) => y.tid === t.tid);
            if (at < 0) throw new Error(`대상 ${t.tid} 는 이미 빠졌습니다`);
            x.splice(at, 1);
          })));
        }
        tb.append(r);
      });
      for (const a of c.adopted_only || []) {
        // 레이아웃 대상에 없는 tid 의 채택 문구 — Import 가 스펙 _layer_targets 에서 더한다
        const r = el("div", "lt adopted");
        r.append(el("span", "lt-tid", a.tid), el("span", "dim", `채택: ${a.text}`));
        r.title = "스펙 _layer_targets 채택 문구(레이아웃 대상 아님) — 같은 글자로 대상을 더하면 레이아웃 문구가 이김";
        tb.append(r);
      }
      panel.append(el("div", "lp-k", "대상 (문구 · 부품 상자 · 채택)"), tb);
      const b = el("div", "lp-btns");
      b.append(button("+ 대상", "다음 빈 글자(채택된 글자 제외)로 대상을 더함", () => addTarget(c)));
      const d = c.draft_boxes;
      const n = d && d.trusted ? Object.keys(d.boxes).length : 0;
      if (n) {
        b.append(button(`초안 상자 가져오기 (${n})`, "Spec Draft 초안의 부품 bbox(지금 크롭과 같은 화면에서 만든 것)", () => commitTargets(c, (x) => {
          for (const t of x) if (d.boxes[t.tid]) t.box = d.boxes[t.tid];
        })));
      } else if (d && d.n) {
        b.append(el("span", "dim", `초안 상자 ${d.n}: 초안이 지금 크롭과 다른 화면에서 만들어져 가져오지 않음`));
      }
      panel.append(b);
    }

    function checkRows(c) {
      const rows = currentChecks(c);
      if (!rows.length) return [el("li", "ok", "문제 없음")];
      return rows.map((x) => {
        const li = el("li", x.level, `${LEVELS[x.level] || x.level} · ${x.msg}`);
        li.title = x.code;
        if (x.other && S.crop(x.other)) {
          li.classList.add("link");
          li.addEventListener("click", () => select(x.other, true));
        }
        return li;
      });
    }

    function liveUpdate() {
      // 끄는 동안: 선택 크롭의 크기·GPT·검사(G7 로컬 계산)만 고친다
      const c = live && sel && S.crop(sel);
      if (!c) return;
      live.gpt.textContent = sizeText(rectOf(c));
      live.checks.replaceChildren(...checkRows(c));
    }

    function flushPanel() {
      if (on && pendingPanel && !typingInPanel()) renderPanel();
    }

    // 폴링이 미룬 패널은 입력이 끝나면(포커스가 패널 입력칸을 떠남·조합 끝) 그린다. focusout 은 포커스가 옮겨 간 뒤에 판정
    panel.addEventListener("compositionstart", () => {
      composing = true;
    });
    panel.addEventListener("compositionend", () => {
      composing = false;
      flushPanel();
    });
    panel.addEventListener("focusout", () => setTimeout(flushPanel, 0));

    // ── board.js 와의 경계 ──
    async function reload(soft) {
      // soft = 매니페스트 rev 만 바뀜: 패널 입력칸에서 입력·조합 중이면 패널은 입력이 끝난 뒤 그린다(커서·한글 조합 유지).
      // seen 은 읽기에 성공했을 때만 — 실패하면 다음 state 에서 다시 받는다
      const rev = state.rev;
      await S.load();
      seen = S.err ? null : rev;
      showNotice();
      syncSel(false);
      if (soft) renderKeepTyping();
      else render();
    }

    function enter(st) {
      on = true;
      state = st;
      seenRf = [st.refs_rev, st.refs_files_v];
      viewer.setEditor({ pointerdown, pointermove, pointerup, cancel, draw });
      syncSel(false);
      render();
      reload(false);
    }

    function leave() {
      flushNudge();
      on = false;
      drag = draft = boxTid = proposal = null;
      viewer.setEditor(null);
    }

    function stateChanged(st) {
      // 레이아웃 쓰기는 매니페스트 rev 를 올리지 않는다 — 다시 받을지는 reloadKind(레이아웃 판은 지금 가진 판·지난 state 와 비교)
      state = st;
      const prevLr = seenLr;
      const prevRf = seenRf;
      seenLr = st.layout_rev;
      seenRf = [st.refs_rev, st.refs_files_v];
      if (!on || S.busy) return;
      // 레퍼런스 연결(refs_rev)·refs_dir 파일(refs_files_v)만 바뀜 = 매니페스트 rev 만 바뀐 것처럼 입력을 지키며 다시 받음(codex10)
      const kind = reloadKind(seen, S.L ? S.L.rev : null, st, prevLr)
        || (prevRf && (prevRf[0] !== seenRf[0] || prevRf[1] !== seenRf[1]) ? "manifest" : null);
      if (kind) reload(kind === "manifest");
    }

    // 진행(G) 반영 칸 [되돌리기]가 쓴 판 — 다른 창의 쓰기로 보지 않아 실행 취소 기록을 남긴다(지운 크롭을 같은 ID 로 되살리는 길은 Ctrl+Z 뿐).
    // 그 사이 다른 창이 썼으면(from ≠ ownRev) 다음 읽기가 기록을 비운다
    return { enter, leave, onKey, stateChanged, ownWrite: S.ownWrite };
  }

  return { create, session, reloadKind, renameNote };
})();
