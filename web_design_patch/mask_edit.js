// BMK Design Patch Studio — S4 마스크 편집 모드(K — STUDIO_S4_SPEC §5–§7). board.js 가 켜고 끈다(검토·E·T·G 와 배타).
// ★ 후보의 최종 마스크(= Compose·Export PSD 그룹 마스크·★ 합성이 함께 쓰는 마스크)를 브라우저가 편집 창(그 ★ 의 실효 quad bbox) 해상도로 칠하고 —
// 붓 셋·경계 페이드·전체 페더(§3.5 식 — 공용 표 tools/fixtures/mask_rules.json 을 J-K1 이 본다) — [적용] 때 창 크기 L 8비트 PNG 를
// POST /mask/save 로 올린다(서버는 검사·저장만 — D2. 서버가 모드 L 만 받고 canvas.toBlob 은 RGBA 라 PNG 는 pngL 이 직접 만든다).
// 미리보기 = 서버가 주는 영역 3층(아래 ★ 합성·이 패치·위 ★ 합성 — GET /mask layers)에 지금 마스크로 합성(§2.4 공식 compose)한 캔버스를 뷰어
// 겹침 자리에(viewer.setMaskLayer), 붓 입력·붓 원은 뷰어 편집기 훅(setEditor). 붓질은 바뀐 사각형만 requestAnimationFrame 한 번에 다시 합성.
// 세션(적용 안 한 획)은 이 창에만 있고 모드를 나가도 남는다(진행(G) ★ 점검 줄) — 다른 후보로 바꿀 때만 확인 뒤 버린다.
// 응답 모양은 tools/fixtures/mask_api.json(정본). 모든 문자열은 textContent 로만 넣는다. 서버 URL 은 opts.apiUrl(상대 경로)로만 연다.
"use strict";

window.DPMaskEdit = (() => {
  const UNDO_MAX = 50; // 세션 실행 취소 단계(획 하나·한 번에 하는 연산 하나) — 넘으면 오래된 것부터 버림(경고 없음, 적용 요약에는 남김)
  const WIDTHS = [32, 64, 128]; // 경계 페이드 폭 — 기본 64(U0 §2.2: 32 는 끊김이 짧은 띠로 남고 128 은 넓은 띠에서 이중 윤곽)
  const SIZE = [4, 512]; // 붓 크기(지름 — 크롭 px, 도장 반지름 R = 크기 / 2)
  const FLOW = [0.05, 1]; // 붓 세기
  const PREVIEW = 72; // 붓 미리보기 캔버스 한 변(px)
  const SIGMA = [1, 32]; // 전체 페더 σ
  const OPS_MAX = 200; // 기록 ops(서버 검사 — 200자 한 줄)
  const BUSY_MS = 1000; // GET /mask/alts 202(다른 합성 계산 중) 뒤 다시 묻는 간격
  const ALT_SEC = 2.5; // 대안 표 후보당 합성 시간(U0 §2.3 — 기다리는 동안의 어림)
  const TILE = 64; // 부드럽게 붓의 흐림 사본을 이 칸 단위로 필요할 때만 계산
  const LOCKED = ["apply", "edit", "pick"]; // 대상 바꾸기·나가기를 거절하는 busy — 끝나면 세션이 바뀜(나갈 때 진행 ★ 점검에 넘기는 획 수가 낡지 않게)
  const SIDES = { top: "위", bottom: "아래", left: "왼", right: "오른" };
  const TOOLS = { erase: "지우기", revive: "살리기", soften: "흐리기" };
  const SRC = { auto: "자동 마스크", hand: "손 마스크", none: "마스크 없음(창 전체)" };

  // ── 식(§3.5 — 마스크 m ∈ [0, 1], 크롭 해상도. J-K1 이 mask_rules.json 의 모든 행과 맞춰 본다) ──
  function smooth(t) {
    const c = Math.min(1, Math.max(0, t));
    return c * c * (3 - 2 * c);
  }

  function fade(side, rect, w, x, y, a = null, b = null) {
    // 경계 페이드 배수 f = 1 − (1 − smoothstep((d + 0.5) / w))·t. rect = 크롭 rect ∩ 캔버스, (x, y) = 캔버스 픽셀, d = 변에서 안쪽 px(밖이면 음수),
    // p = 변 좌표(rect 의 x0·y0 기준 — 이음매 구간 a·b 와 같은 축), t = [a, b] 안 1·밖으로 w 동안 1 → 0(a null = 변 전체)
    const [x0, y0, x1, y1] = rect;
    const [d, p] = side === "top" ? [y - y0, x - x0] : side === "bottom" ? [y1 - 1 - y, x - x0] : side === "left" ? [x - x0, y - y0] : [x1 - 1 - x, y - y0];
    const t = a === null ? 1 : Math.min(1, Math.max(0, Math.min((p - (a - w)) / w, (b + w - p) / w)));
    return 1 - (1 - smooth((d + 0.5) / w)) * t;
  }

  function kernel(sigma) {
    // 전체 페더의 1D 가우시안: 반경 ceil(3σ), 합 1
    const r = Math.ceil(3 * sigma);
    const k = new Float64Array(2 * r + 1);
    let sum = 0;
    for (let i = -r; i <= r; i++) sum += k[i + r] = Math.exp(-(i * i) / (2 * sigma * sigma));
    return k.map((v) => v / sum);
  }

  // 분리 가우시안(가로 → 세로, 창 밖은 가장자리 복제). 커널이 대칭이라 양쪽 탭을 더해 한 번 곱한다
  function hrow(src, w, k, y, x0, x1, pad, dst) {
    // 가로 탭: src 의 y 줄 [x0, x1) → dst 같은 자리. pad = 길이 ≥ x1 − x0 + 2·반경 작업 칸
    const r = (k.length - 1) >> 1;
    const n = x1 - x0;
    const o = y * w;
    for (let j = 0; j < n + 2 * r; j++) pad[j] = src[o + Math.min(w - 1, Math.max(0, x0 - r + j))];
    for (let x = 0; x < n; x++) {
      let s = k[r] * pad[x + r];
      for (let i = 1; i <= r; i++) s += k[r + i] * (pad[x + r - i] + pad[x + r + i]);
      dst[o + x0 + x] = s;
    }
  }

  function vrows(src, w, h, k, x0, y0, x1, y1, acc, dst) {
    // 세로 탭: 가로 패스 결과 src 의 [x0, x1) × [y0, y1) → dst. 탭마다 줄을 차례로 훑어 acc(길이 ≥ x1 − x0)에 더함(열을 건너뛰며 읽지 않게)
    const r = (k.length - 1) >> 1;
    const n = x1 - x0;
    for (let y = y0; y < y1; y++) {
      const c = y * w + x0;
      for (let x = 0; x < n; x++) acc[x] = k[r] * src[c + x];
      for (let i = 1; i <= r; i++) {
        const a = Math.max(0, y - i) * w + x0;
        const b = Math.min(h - 1, y + i) * w + x0;
        const ki = k[r + i];
        for (let x = 0; x < n; x++) acc[x] += ki * (src[a + x] + src[b + x]);
      }
      for (let x = 0; x < n; x++) dst[c + x] = acc[x];
    }
  }

  function feather(m, w, h, sigma) {
    const k = kernel(sigma);
    const tmp = new Float64Array(w * h);
    const pad = new Float64Array(w + k.length - 1);
    for (let y = 0; y < h; y++) hrow(m, w, k, y, 0, w, pad, tmp);
    const out = new m.constructor(m.length);
    vrows(tmp, w, h, k, 0, 0, w, h, new Float64Array(w), out);
    return out;
  }

  function stamp(R, hard, r) {
    // 붓 도장 세기: r ≤ R·경도 → 1, r ≥ R → 0, 그 사이 1 − smoothstep. r = 도장 중심 ~ 픽셀 중심 거리, R = 반지름
    if (r <= R * hard) return 1;
    if (r >= R) return 0;
    return 1 - smooth((r - R * hard) / (R - R * hard));
  }

  function op(kind, m, s, k, blur) {
    // 도장 한 번(k = 세기): 지우기 m·(1 − s·k), 살리기 m + (1 − m)·s·k, 부드럽게 m + (blur − m)·s·k(blur = 획 시작 때 σ = R/4 사본)
    if (kind === "erase") return m * (1 - s * k);
    if (kind === "revive") return m + (1 - m) * s * k;
    return m + (blur - m) * s * k;
  }

  function quant(m) {
    // PNG 값 = floor(clip(m)·255 + 0.5)(store._to_pil 과 같은 반올림)
    return Math.floor(Math.min(1, Math.max(0, m)) * 255 + 0.5);
  }

  function compose(L, win, mask, base, out, x0, y0, x1, y1, view) {
    // §2.4: out = over.rgb·over.a + (under + (patch.rgb − under)·patch.a·mask)·(1 − over.a). L = 영역 3층 {box, under, patch, over}(RGBA 바이트,
    // box 크기 — under 는 a 255), win = 마스크 창(캔버스), mask·base = 창 크기 세션·기준 마스크 — 올리는 8비트 값으로 합성(보이는 것 = 저장되는 것),
    // 창 밖 0. [x0, x1) × [y0, y1) = 다시 그릴 곳(box 좌표). view = {base: Q(기준으로), split: 이 box x 왼쪽은 기준(S) | null, mark: M(빨강 = 보이는
    // 패치, 회색 빗금 = 기준보다 지운 곳), orig: W(이 패치 없이 — F35)}
    const bw = L.box[2] - L.box[0];
    const ww = win[2] - win[0];
    const wh = win[3] - win[1];
    const dx = L.box[0] - win[0];
    const dy = L.box[1] - win[1];
    const { under: u, patch: p, over: o } = L;
    for (let y = y0; y < y1; y++) {
      const wy = y + dy;
      for (let x = x0; x < x1; x++) {
        const i = (y * bw + x) * 4;
        const wx = x + dx;
        const j = wy * ww + wx;
        const inWin = wx >= 0 && wx < ww && wy >= 0 && wy < wh;
        const mv = !inWin ? 0 : quant(view.base || (view.split !== null && x < view.split) ? base[j] : mask[j]) / 255;
        const pa = view.orig ? 0 : (p[i + 3] / 255) * mv; // orig(W) = 이 패치 없이(아래·위 ★ 만 — 고치기 전 그림)
        const oa = o[i + 3] / 255;
        let r = o[i] * oa + (u[i] + (p[i] - u[i]) * pa) * (1 - oa);
        let g = o[i + 1] * oa + (u[i + 1] + (p[i + 1] - u[i + 1]) * pa) * (1 - oa);
        let b = o[i + 2] * oa + (u[i + 2] + (p[i + 2] - u[i + 2]) * pa) * (1 - oa);
        if (view.mark && !view.orig && p[i + 3]) { // 원본 보기(W) 동안은 원본 그대로(빗금 없이 — Codex 4차)
          const a = 0.5 * pa;
          r = r * (1 - a) + 255 * a;
          g *= 1 - a;
          b *= 1 - a;
          if (inWin && quant(base[j]) / 255 - mv > 0.5 / 255 && ((x + y) & 7) < 3) {
            r = r * 0.3 + 105;
            g = g * 0.3 + 105;
            b = b * 0.3 + 105;
          }
        }
        out[i] = r;
        out[i + 1] = g;
        out[i + 2] = b;
        out[i + 3] = 255;
      }
    }
  }

  // ── L 8비트 PNG(서버 validate_png 는 모드 L·투명 없음만 받음): 줄 필터 Up, zlib = CompressionStream("deflate") ──
  const CRC = new Uint32Array(256).map((_, n) => {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    return c >>> 0;
  });

  function crc32(b) {
    let c = 0xffffffff;
    for (let i = 0; i < b.length; i++) c = CRC[(c ^ b[i]) & 255] ^ (c >>> 8);
    return (c ^ 0xffffffff) >>> 0;
  }

  function chunk(type, data) {
    const out = new Uint8Array(12 + data.length);
    const dv = new DataView(out.buffer);
    dv.setUint32(0, data.length);
    for (let i = 0; i < 4; i++) out[4 + i] = type.charCodeAt(i);
    out.set(data, 8);
    dv.setUint32(8 + data.length, crc32(out.subarray(4, 8 + data.length)));
    return out;
  }

  async function pngL(px, w, h) {
    // px = 창 크기 8비트 값(Uint8Array, 줄 차례) → image/png Blob
    const raw = new Uint8Array((w + 1) * h);
    for (let y = 0; y < h; y++) {
      const o = y * (w + 1);
      raw[o] = 2;
      for (let x = 0; x < w; x++) raw[o + 1 + x] = px[y * w + x] - (y ? px[(y - 1) * w + x] : 0);
    }
    const z = new Uint8Array(await new Response(new Blob([raw]).stream().pipeThrough(new CompressionStream("deflate"))).arrayBuffer());
    const head = new Uint8Array(13);
    const dv = new DataView(head.buffer);
    dv.setUint32(0, w);
    dv.setUint32(4, h);
    head[8] = 8; // 비트 깊이 8 · 색 형식 0(회색조) · 압축·필터·인터레이스 0
    return new Blob([new Uint8Array([137, 80, 78, 71, 13, 10, 26, 10]), chunk("IHDR", head), chunk("IDAT", z), chunk("IEND", new Uint8Array(0))],
      { type: "image/png" });
  }

  async function decode(blob) {
    // 서버 PNG → RGBA 바이트(색 변환 없음 — L·RGB 는 a 255)
    const bmp = await createImageBitmap(blob, { colorSpaceConversion: "none", premultiplyAlpha: "none" });
    const c = document.createElement("canvas");
    c.width = bmp.width;
    c.height = bmp.height;
    const g = c.getContext("2d", { willReadFrequently: true });
    g.drawImage(bmp, 0, 0);
    bmp.close();
    return { w: c.width, h: c.height, data: g.getImageData(0, 0, c.width, c.height).data };
  }

  function union(a, b) {
    if (!a) return b;
    if (!b) return a;
    return [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[2], b[2]), Math.max(a[3], b[3])];
  }

  // ── 세션(DOM 없음 — J-K2 가 node 에서 가짜 fetch·decode 로 돈다). decode(blob) → {w, h, data: RGBA 바이트} ──
  function session(apiUrl, root, fetchFn, decodeFn, busyMs = BUSY_MS) {
    // info = 마지막 GET /mask(err = 받지 못한 이유), key = 편집 대상 ★ 후보, win = 세션 창(연 때의 rect — 그 뒤 창이 바뀌어도 세션은 그 창, 적용하면 409),
    // base·m·start = 창 크기 기준 마스크·세션 마스크·세션을 연 판(Float32, 0–1), file·sig = 연 판의 기록 file(seen — 없으면 "")·기준 서명,
    // undo·redo = 단계 [{r: 창 좌표 사각형, v: 그 사각형의 다른 쪽 값, label}], dropped = UNDO_MAX 를 넘어 버린 단계 이름(적용 요약에는 남김),
    // layers = 영역 3층 {box, sig, under, patch, over}, conflict = 마지막 적용·되돌리기의 409 본문(세션은 그대로), busy = "" | load | apply | edit
    // | pick(화면이 보드 ★ 쓰기 동안 잠금 — starAlt),
    // seq = 마지막 열기의 차례(A → B → A 처럼 같은 대상을 다시 열면 key 로는 앞 응답을 가르지 못함), rseq = 마지막 다시 받기의 차례(적용 성공도 올림 —
    // 그 전에 나간 GET /mask 는 옛 판)
    const s = { info: null, err: null, key: null, win: null, base: null, m: null, start: null, file: "", sig: null, undo: [], redo: [], dropped: [],
      layers: null, conflict: null, busy: "", stroke: null, seq: 0, rseq: 0 };
    const qs = (o) => new URLSearchParams(Object.assign({ root }, o)).toString();
    const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

    async function readJson(r) {
      try {
        return await r.json();
      } catch (e) {
        return null; // JSON 이 아닌 응답(라우트가 없는 옛 서버의 404)
      }
    }

    function failure(r, d) {
      const msg = d && d.error ? d.error : r.status === 404 ? "마스크 라우트가 없습니다 — ComfyUI 를 재시작해야 마스크 편집(K)을 쓸 수 있습니다"
        : `응답을 읽을 수 없습니다 (HTTP ${r.status})`;
      return Object.assign(new Error(msg), { status: r.status, data: d });
    }

    async function call(path, body) {
      const r = await fetchFn(apiUrl(path), body === undefined ? { cache: "no-store" }
        : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(Object.assign({ root }, body)) });
      const d = await readJson(r);
      if (!r.ok || !d) throw failure(r, d);
      return d;
    }

    async function image(rel, w, h) {
      // GET /mask/img → RGBA 바이트(크기가 기대와 다르면 오류 — 그 사이 바뀐 판)
      const r = await fetchFn(apiUrl(rel), {});
      if (!r.ok) throw failure(r, await readJson(r));
      const d = await decodeFn(await r.blob());
      if (d.w !== w || d.h !== h) throw new Error(`이미지 크기 ${d.w}×${d.h} ≠ ${w}×${h} — 다시 받기`);
      return d.data;
    }

    async function raster(rel, rect) {
      const px = await image(rel, rect[2] - rect[0], rect[3] - rect[1]);
      const out = new Float32Array(px.length / 4);
      for (let i = 0; i < out.length; i++) out[i] = px[i * 4] / 255;
      return out;
    }

    function size() {
      return [s.win[2] - s.win[0], s.win[3] - s.win[1]];
    }

    function grab(src, r) {
      const w = size()[0];
      const rw = r[2] - r[0];
      const v = new Float32Array(rw * (r[3] - r[1]));
      for (let y = r[1]; y < r[3]; y++) v.set(src.subarray(y * w + r[0], y * w + r[2]), (y - r[1]) * rw);
      return v;
    }

    function put(r, v) {
      const w = size()[0];
      const rw = r[2] - r[0];
      for (let y = r[1]; y < r[3]; y++) s.m.set(v.subarray((y - r[1]) * rw, (y - r[1] + 1) * rw), y * w + r[0]);
    }

    function onCanvas(r) {
      return [r[0] + s.win[0], r[1] + s.win[1], r[2] + s.win[0], r[3] + s.win[1]];
    }

    function push(r, v, label) {
      s.undo.push({ r, v, label });
      s.redo = [];
      if (s.undo.length > UNDO_MAX) s.dropped.push(s.undo.shift().label);
    }

    function whole(label, r, fn) {
      // 한 번에 하는 연산 = 단계 하나: r(창 좌표) 안의 이전 값을 쌓고 fn 으로 바꿈 → 바뀐 사각형(캔버스 좌표) | null
      if (!s.m || s.busy || s.stroke || r[2] <= r[0] || r[3] <= r[1]) return null;
      push(r, grab(s.m, r), label);
      fn();
      return onCanvas(r);
    }

    s.pending = () => s.dropped.length + s.undo.length;

    // 세션을 연 판(기록 file·기준 서명·창) ≠ 마지막 GET /mask — 그 뒤 다른 창·노드가 쓴 것을 적용 안 한 획 때문에 다시 열지 않음(file·창이 다르면 적용 409,
    // 기준 서명만 다르면 서버가 지금 기준으로 받음 — 패널 세션 칸이 갈래별로 알림)
    s.behind = () => !!s.m && ((s.info.edit ? s.info.edit.file : "") !== s.file || s.info.base.sig !== s.sig || s.info.rect.join() !== s.win.join());

    s.open = async (key) => {
      // 편집 대상을 (다시) 연다: GET /mask → 기준·편집 래스터(둘 다 지금 편집 창 크기 — 편집은 서버가 기록 창에서 옮겨 줌) → 세션 = 편집(없으면 기준),
      // 단계 비움. 3층은 따로(loadLayers). → 열었는가. 래스터를 받지 못해도(409 stale·404 — 그 사이 바뀐 판) info·seen 은 남긴다(세션만 없음 —
      // 편집 기준 바뀜 줄·[기준으로 다시 시작]·[이전 판으로]·[편집 지우기]는 그대로, [다시 받기]·다음 state 가 다시 연다). 끌던 획은 버림(옛 창 좌표·사본).
      // 단계는 받기 전에 비운다 — 부르는 쪽이 이미 버리기를 확인했다(받는 동안 pending 이 버린 획을 새 대상 이름으로 세지 않게: 확인 창·★ 점검 줄)
      const seq = ++s.seq;
      if (key !== s.key) s.layers = null;
      Object.assign(s, { key, busy: "load", conflict: null, undo: [], redo: [], dropped: [], stroke: null });
      const clear = { win: null, base: null, m: null, start: null, file: "", sig: null };
      let info = null;
      try {
        info = await call("mask?" + qs({ key }));
        const base = await raster(info.base.url, info.rect);
        const ed = info.edit ? await raster(info.edit.url, info.rect) : null;
        if (s.seq !== seq) return false;
        Object.assign(s, clear, { info, err: null, win: info.rect.slice(), base, m: (ed || base).slice(), start: (ed || base).slice(),
          file: info.edit ? info.edit.file : "", sig: info.base.sig });
        return true;
      } catch (e) {
        if (s.seq === seq) Object.assign(s, clear, { info, err: e.message, file: info && info.edit ? info.edit.file : "" });
        return false;
      } finally {
        if (s.seq === seq) s.busy = "";
      }
    };

    s.loadLayers = async () => {
      // 영역 3층(GET /mask/img under·patch·over — v = layers.sig). 같은 서명이면 그대로 → 바꿨는가. 받는 사이 대상이나 3층 서명이 바뀌면 버림(앞 대상의
      // 늦은 3층이 새 대상의 겹침이 되지 않게 — 새 대상의 GET /mask 가 오기 전이면 s.info 는 아직 앞 대상). 409 stale 등은 던짐(다시 받기)
      const key = s.key;
      const L = s.info && s.info.key === key && s.info.layers;
      if (!L || (s.layers && s.layers.sig === L.sig)) return false;
      const [w, h] = [L.box[2] - L.box[0], L.box[3] - L.box[1]];
      const [under, patch, over] = await Promise.all([L.under_url, L.patch_url, L.over_url].map((u) => image(u, w, h)));
      if (s.key !== key || !s.info || s.info.key !== key || s.info.layers.sig !== L.sig) return false;
      s.layers = { box: L.box.slice(), sig: L.sig, under, patch, over };
      return true;
    };

    s.refresh = async () => {
      // 다시 받기(state rev 가 바뀜 — 다른 창·노드의 쓰기, 이 창의 적용 뒤). 세션이 비었으면 서버 판(기록 file·기준·창)이 연 판과 다를 때 래스터를
      // 다시 열고, 적용 안 한 획이 있으면 세션은 그대로(behind — 그 판·창으로 적용하면 409, 사람이 정함). 세션이 없으면(받지 못함) 다시 연다. → 다시 열었는가.
      // 응답 전에 열기·다른 다시 받기·적용이 있었으면 이 응답은 버림(옛 판으로 info 를 덮어 behind 가 거짓으로 켜지지 않게)
      if (!s.m) return s.open(s.key);
      const [seq, rseq] = [s.seq, ++s.rseq];
      const late = () => s.seq !== seq || s.rseq !== rseq;
      let info;
      try {
        info = await call("mask?" + qs({ key: s.key }));
      } catch (e) {
        if (!late()) s.err = e.message;
        return false;
      }
      if (late()) return false;
      Object.assign(s, { info, err: null });
      if (!s.behind() || s.pending() || s.stroke || s.busy) return false;
      return s.open(s.key);
    };

    // 붓: 획 시작 때 창 전체 사본(끝에서 바뀐 사각형만 실행 취소 값으로), 부드럽게 = 그 사본의 σ = R/4 흐림(칸 단위로 필요할 때 — hb = 가로 패스,
    // hdone = 줄 × 칸 열마다 가로 패스를 했는가: 위아래 칸이 ± 반경 줄을 함께 써 한 번만, done = 칸마다 세로 패스를 했는가)
    s.begin = (kind, pt, R, hard, flow) => {
      if (!s.m || s.busy || s.stroke) return null;
      const [w, h] = size();
      const k = kind === "soften" ? kernel(R / 4) : null;
      const tw = Math.ceil(w / TILE);
      s.stroke = { kind, R, hard, flow, snap: s.m.slice(), k, blur: k ? new Float32Array(w * h) : null, hb: k ? new Float32Array(w * h) : null,
        hdone: k ? new Uint8Array(tw * h) : null, done: k ? new Uint8Array(tw * Math.ceil(h / TILE)) : null,
        pad: k ? new Float64Array(TILE + k.length - 1) : null, acc: k ? new Float64Array(TILE) : null, last: null, carry: 0, box: null };
      return s.move(pt);
    };

    function blurTiles(r) {
      const st = s.stroke;
      const [w, h] = size();
      const tw = Math.ceil(w / TILE);
      const rad = (st.k.length - 1) >> 1;
      for (let ty = Math.floor(r[1] / TILE); ty * TILE < r[3]; ty++) {
        for (let tx = Math.floor(r[0] / TILE); tx * TILE < r[2]; tx++) {
          if (st.done[ty * tw + tx]) continue;
          st.done[ty * tw + tx] = 1;
          const [x0, y0, x1, y1] = [tx * TILE, ty * TILE, Math.min(w, (tx + 1) * TILE), Math.min(h, (ty + 1) * TILE)];
          for (let y = Math.max(0, y0 - rad); y < Math.min(h, y1 + rad); y++) {
            if (st.hdone[y * tw + tx]) continue;
            st.hdone[y * tw + tx] = 1;
            hrow(st.snap, w, st.k, y, x0, x1, st.pad, st.hb);
          }
          vrows(st.hb, w, h, st.k, x0, y0, x1, y1, st.acc, st.blur);
        }
      }
    }

    function dab(cx, cy) {
      // 도장 하나(창 좌표 중심) → 바뀐 사각형(창 좌표) | null
      const st = s.stroke;
      const [w, h] = size();
      const R = st.R;
      const r = [Math.max(0, Math.floor(cx - R)), Math.max(0, Math.floor(cy - R)), Math.min(w, Math.ceil(cx + R)), Math.min(h, Math.ceil(cy + R))];
      if (r[2] <= r[0] || r[3] <= r[1]) return null;
      if (st.blur) blurTiles(r);
      for (let y = r[1]; y < r[3]; y++) {
        for (let x = r[0]; x < r[2]; x++) {
          const v = stamp(R, st.hard, Math.hypot(x + 0.5 - cx, y + 0.5 - cy));
          if (!v) continue;
          const i = y * w + x;
          s.m[i] = op(st.kind, s.m[i], v, st.flow, st.blur ? st.blur[i] : 0);
        }
      }
      st.box = union(st.box, r);
      return r;
    }

    s.move = (pt) => {
      // 마우스 사이를 R/4 간격 도장으로 잇는다(carry = 마지막 도장 뒤 간 거리) → 이번에 바뀐 사각형(캔버스 좌표) | null
      const st = s.stroke;
      if (!st) return null;
      const x = pt[0] - s.win[0];
      const y = pt[1] - s.win[1];
      let r = null;
      if (!st.last) r = dab(x, y);
      else {
        const [lx, ly] = st.last;
        const d = Math.hypot(x - lx, y - ly);
        const gap = st.R / 4;
        let t = gap - st.carry;
        for (; t <= d; t += gap) r = union(r, dab(lx + ((x - lx) * t) / d, ly + ((y - ly) * t) / d));
        st.carry = d - (t - gap);
      }
      st.last = [x, y];
      return r && onCanvas(r);
    };

    s.end = () => {
      const st = s.stroke;
      s.stroke = null;
      if (st && st.box) push(st.box, grab(st.snap, st.box), "붓");
    };

    s.fade = (side, w, crop, a = null, b = null) => {
      // 경계 페이드(변 전체 / 구간 [a, b] ± w 테이퍼). crop = 크롭 rect ∩ 캔버스. 바뀌는 곳(f < 1 — 변 쪽 안쪽 w 와 창 끝까지 바깥 × 구간 ± w)만 단계로
      if (!s.m) return null;
      const [x0, y0, x1, y1] = crop;
      const lo = a === null ? -Infinity : a - w;
      const hi = a === null ? Infinity : b + w + 1;
      const c = side === "top" ? [x0 + lo, -Infinity, x0 + hi, y0 + w] : side === "bottom" ? [x0 + lo, y1 - w, x0 + hi, Infinity]
        : side === "left" ? [-Infinity, y0 + lo, x0 + w, y0 + hi] : [x1 - w, y0 + lo, Infinity, y0 + hi];
      const [W, H] = size();
      const r = [Math.max(0, c[0] - s.win[0]), Math.max(0, c[1] - s.win[1]), Math.min(W, c[2] - s.win[0]), Math.min(H, c[3] - s.win[1])];
      return whole(a === null ? `가장자리 페이드 ${side} ${w}` : `구간 페이드 ${side} ${a}–${b} ${w}`, r, () => {
        for (let y = r[1]; y < r[3]; y++) for (let x = r[0]; x < r[2]; x++) s.m[y * W + x] *= fade(side, crop, w, x + s.win[0], y + s.win[1], a, b);
      });
    };

    s.feather = (sigma) => {
      if (!s.m) return null;
      const [w, h] = size();
      return whole(`페더 ${sigma}px`, [0, 0, w, h], () => s.m.set(feather(s.m, w, h, sigma)));
    };

    s.toBase = () => {
      if (!s.m) return null;
      const [w, h] = size();
      return whole("처음 마스크로", [0, 0, w, h], () => s.m.set(s.base));
    };

    s.step = (back) => {
      // 세션 실행 취소·다시(Ctrl+Z·Y) → 바뀐 사각형(캔버스 좌표) | null
      const from = back ? s.undo : s.redo;
      if (!s.m || s.busy || s.stroke || !from.length) return null;
      const e = from.pop();
      const cur = grab(s.m, e.r);
      put(e.r, e.v);
      e.v = cur;
      (back ? s.redo : s.undo).push(e);
      return onCanvas(e.r);
    };

    s.discard = () => {
      if (!s.m || s.busy || s.stroke) return null;
      s.m.set(s.start);
      Object.assign(s, { undo: [], redo: [], dropped: [] });
      return s.win.slice();
    };

    s.ops = () => {
      // 적용 기록 ops(사람이 읽는 요약 — 200자 한 줄): 단계 이름을 차례로, 이어진 붓은 '붓 n획'
      const parts = [];
      for (const l of s.dropped.concat(s.undo.map((e) => e.label))) {
        const last = parts[parts.length - 1];
        if (l === "붓" && last && last.n) last.n += 1;
        else parts.push(l === "붓" ? { n: 1 } : { l });
      }
      const t = parts.map((x) => (x.n ? `붓 ${x.n}획` : x.l)).join(" · ");
      return t.length > OPS_MAX ? t.slice(0, OPS_MAX - 1) + "…" : t;
    };

    s.apply = async () => {
      // [적용]: 세션 마스크를 8비트로(quant) 창 크기 L PNG → POST /mask/save(seen = 연 판, ops, rect = 세션 창 — 같은 크기로 옮겨진 창은 크기로 못 잡아
      // 서버가 rect 로 409 rect_changed). 409 = 세션을 그대로 두고 conflict(사람이 다시 받기), 성공 = 올린 값이 새 출발(단계 비움).
      // → 응답 본문(409 포함) | null(할 것 없음) — 그 밖 실패는 던짐
      if (!s.m || s.busy || s.stroke || !s.pending()) return null;
      s.busy = "apply";
      s.conflict = null;
      try {
        const [w, h] = size();
        const px = new Uint8Array(w * h);
        for (let i = 0; i < px.length; i++) px[i] = quant(s.m[i]);
        const r = await fetchFn(apiUrl("mask/save?" + qs({ key: s.key, seen: s.file, ops: s.ops(), rect: s.win.join() })),
          { method: "POST", headers: { "Content-Type": "image/png" }, body: await pngL(px, w, h) });
        const d = await readJson(r);
        if (r.status === 409 && d) {
          s.conflict = d;
          return d;
        }
        if (!r.ok || !d) throw failure(r, d);
        for (let i = 0; i < px.length; i++) s.m[i] = px[i] / 255;
        Object.assign(s, { start: s.m.slice(), file: d.edit.file, undo: [], redo: [], dropped: [], rseq: s.rseq + 1 });
        s.info = Object.assign({}, s.info, { edit: d.edit, composite_sig: d.composite_sig, seams: null });
        return d;
      } finally {
        s.busy = "";
      }
    };

    s.edit = async (kind) => {
      // [이전 판으로](undo — hist 첫 판, 비면 기록 지움)·[편집 지우기](reset): 세션을 버리고 그 판으로 다시 연다. → 응답(409 = conflict 본문)
      if (!s.key || s.busy || s.stroke) return null;
      s.busy = "edit";
      s.conflict = null;
      let d;
      try {
        d = await call("mask/edit", { key: s.key, seen: s.file, op: kind });
      } catch (e) {
        if (e.status !== 409 || !e.data) throw e;
        s.conflict = e.data;
        return e.data;
      } finally {
        s.busy = "";
      }
      await s.open(s.key);
      return d;
    };

    s.alts = async (key) => {
      // [다른 후보로](§6.2): GET /mask/alts — 202(다른 합성 계산 중)면 busyMs 뒤 다시(그 사이 대상이 바뀌면 null)
      for (;;) {
        const r = await fetchFn(apiUrl("mask/alts?" + qs({ key })), { cache: "no-store" });
        const d = await readJson(r);
        if (r.status !== 202) {
          if (!r.ok || !d) throw failure(r, d);
          return d;
        }
        await sleep(busyMs);
        if (s.key !== key) return null;
      }
    };

    return s;
  }

  // ── 화면: 왼쪽 크롭 목록·오른쪽 패널·뷰어 겹침 캔버스와 붓 ──
  function create(hosts, opts) {
    // hosts: {list, panel}. opts: apiUrl·root·viewer·toast, exit()(K 모드 나가기 — board setMask(false)), refresh()(보드 state 다시),
    // select(key, follow)(보드 선택 = 편집 대상 — 뷰어 선택 크롭·F·1·2 의 초점), pick(t, key)(검토의 Space 와 같은 ★ 쓰기 → 결과 | null),
    // seams()(뷰어가 받은 ★ 합성 이음매 {sig, segments} | null), ranText(r)(후보 완료 시각·종류 — board.js ranText)
    const { list, panel } = hosts;
    const viewer = opts.viewer;
    const svg = window.DPViewer.svg;
    const M = session(opts.apiUrl, opts.root, (u, o) => fetch(u, o), decode);
    let on = false;
    let S = null;
    let seenRev = null; // 마지막으로 GET /mask 를 맞춘 state 판(stateVer — rev·pref_v)
    let again = false; // 받는 중·획 중에 state 가 바뀜 — 끝나면 다시 받기
    let tool = "erase";
    let size = 48;
    let hard = 0.5;
    let flow = 1;
    let width = 64;
    let sigma = 4;
    let seam = null; // 선택 이음매 구간(목록·N) — 구간 페이드의 a·b
    const view = { base: false, split: null, mark: false, orig: false };
    let slider = false;
    let preview = null; // 붓 미리보기 캔버스(패널을 다시 그릴 때마다 새로)
    let origKey = false; // view.orig 를 W 를 누른 동안만 켰는가(놓으면 끔) — [원본 보기] 단추로 켠 것은 W 를 놓아도 그대로
    let cursor = null; // 붓 원 자리(캔버스) | null
    let soft = false; // 붓 원 = 부드럽게(Shift)
    let dirty = null; // 다시 그릴 곳(box 좌표)
    let raf = 0;
    let layerErr = null;
    let alts = null; // [다른 후보로] {key, n, data | null, err}
    let applied = null; // 적용 전후 {key, from·sig: 적용 전·뒤 ★ 합성 서명, before: 그때 이 크롭 이음매} — 합성이 둘 다 아니게 바뀌면 지움
    // 포인터가 눌린 동안은 목록·패널을 다시 만들지 않는다(pipeline.js 와 같음 — mousedown 과 mouseup 사이에 누른 단추·줄이 빠지면 Chromium 은 click 을
    // 보내지 않고, 끌던 슬라이더는 손에서 빠짐) — later = 미룬 다시 그리기(놓은 뒤)
    let held = false;
    let later = false;
    const cv = document.createElement("canvas");
    const g2 = cv.getContext("2d");
    let img = null;
    const ring = svg("circle", { class: "mbrush" }); // 붓 원(F33): 지우기 빨강 · 살리기 초록(revive) · 부드럽게 흰 점선(soft)

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
        fn(e);
      });
      return b;
    }

    function off(b, dis) {
      b.disabled = !!dis;
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

    function pad(n) {
      return String(n).padStart(2, "0");
    }

    function when(at) {
      const m = /^\d{4}-(\d\d)-(\d\d) (\d\d:\d\d)/.exec(at || "");
      return m ? `${m[1]}/${m[2]} ${m[3]}` : at || "";
    }

    // ── state 조회 ──
    function candOf(key) {
      for (const t of (S && S.targets) || []) {
        const c = t.candidates.find((x) => x.key === key);
        if (c) return { t, c };
      }
      return null;
    }

    function cropOf(id) {
      return S ? S.crops.find((c) => c.id === id) || null : null;
    }

    function cur() {
      // 편집 대상의 GET /mask — 다른 대상을 여는 동안은 null(M.info 는 아직 앞 대상: 새 이름에 옛 창·z·편집을 섞지 않게)
      return M.info && M.info.key === M.key ? M.info : null;
    }

    function cropRect() {
      // 페이드 rect = 크롭 rect ∩ 캔버스(이음매 검사와 같은 rect)
      const c = M.info && cropOf(M.info.crop_id);
      if (!c || !S.canvas) return null;
      const [W, H] = S.canvas.size;
      return [Math.max(0, c.rect[0]), Math.max(0, c.rect[1]), Math.min(W, c.rect[2]), Math.min(H, c.rect[3])];
    }

    function label(key) {
      const x = candOf(key);
      return x ? `${x.t.crop_name || x.t.crop_id} · ${x.t.tid} · c${pad(x.c.n)}` : key || "";
    }

    function along(x) {
      return x.side === "top" || x.side === "bottom" ? [x.x0, x.x1] : [x.y0, x.y1];
    }

    function seamName(x) {
      return `${x.side} ${along(x).join("–")}`;
    }

    function cropSeams() {
      // 이 크롭의 이음매(지금 ★ 합성): 뷰어가 받은 것이 지금 state 서명이면 그것, 아니면 GET /mask 의 seams(같은 서명일 때) → {sig, list} | null(계산 중)
      const sig = S && S.canvas ? S.canvas.composite_sig : null;
      const v = opts.seams();
      const info = cur();
      if (!info) return null;
      if (v && v.sig === sig && v.segments) return { sig, list: v.segments.filter((x) => x.crop_id === info.crop_id) };
      if (info.seams && info.composite_sig === sig) return { sig, list: info.seams };
      return null;
    }

    // ── 겹침 캔버스(§2.4) ──
    function attach() {
      const L = M.layers;
      if (!on || !L) return;
      if (!M.m) {
        // 세션이 없으면(래스터를 받지 못함 — 다시 받기·되돌리기 뒤 다시 열기 실패 포함) 겹침을 뗌(옛 세션 합성이 남지 않게) — 다시 열면 layers() 가 붙임
        if (img) viewer.setMaskLayer(null);
        img = null;
        return;
      }
      const [w, h] = [L.box[2] - L.box[0], L.box[3] - L.box[1]];
      if (cv.width !== w || cv.height !== h) {
        cv.width = w;
        cv.height = h;
      }
      img = g2.createImageData(w, h);
      viewer.setMaskLayer({ canvas: cv, box: L.box });
      repaintAll();
    }

    function repaint(r) {
      // r = 바뀐 사각형(캔버스 좌표) | null(바뀐 것 없음) — box 로 잘라 모았다가 프레임마다 한 번 합성
      const L = M.layers;
      if (!r || !L || !img) return;
      const [bx0, by0, bx1, by1] = L.box;
      const q = [Math.max(r[0], bx0) - bx0, Math.max(r[1], by0) - by0, Math.min(r[2], bx1) - bx0, Math.min(r[3], by1) - by0];
      if (q[2] <= q[0] || q[3] <= q[1]) return;
      dirty = union(dirty, q);
      if (!raf) raf = requestAnimationFrame(flush);
    }

    function flush() {
      raf = 0;
      const d = dirty;
      dirty = null;
      if (!d || !M.layers || !img || !M.m) return;
      compose(M.layers, M.win, M.m, M.base, img.data, d[0], d[1], d[2], d[3],
        { base: view.base, split: slider ? view.split : null, mark: view.mark, orig: view.orig });
      g2.putImageData(img, 0, 0, d[0], d[1], d[2] - d[0], d[3] - d[1]);
    }

    function repaintAll() {
      repaint([-Infinity, -Infinity, Infinity, Infinity]);
    }

    async function layers() {
      // 3층이 바뀐 때만 받는다. 실패(409 stale — 그 사이 ★ 합성이 바뀜: 다음 state 가 다시 받기를 부름)는 패널 줄
      try {
        layerErr = null;
        if (await M.loadLayers() || !img || !M.m) attach();
      } catch (e) {
        layerErr = e.message;
      }
      if (on) renderPanel();
      if (again) refreshInfo();
    }

    // ── 대상 ──
    async function open(key, sg, follow) {
      // 편집 대상을 연다(세션 버림 — 확인은 go): 래스터 → 3층(따로) → 다시 그림
      alts = null;
      applied = null;
      seam = sg || null;
      img = null;
      viewer.setMaskLayer(null);
      seenRev = stateVer(S);
      again = false;
      opts.select(key, follow);
      const p = M.open(key);
      render();
      await p;
      if (M.key !== key) return;
      render();
      await layers();
    }

    function go(to, follow = false) {
      // 편집 대상·선택 이음매(목록·N·진행 ★ 점검·이음매 목록): 다른 후보면 적용 안 한 획 확인 뒤 연다. → 그 대상인가
      if (to.key !== M.key && LOCKED.includes(M.busy)) {
        opts.toast("적용·되돌리기·★ 바꾸기를 처리하는 중 — 끝난 뒤 바꾸세요", "warn");
        return false;
      }
      // N 은 끄는 중에도 온다(뷰어 포인터 캡처는 클릭만 막음) — 획을 끝낸다: 다른 후보면 확인 창에 세고(새 대상에 옛 창 좌표로 이어 칠하지 않게),
      // 같은 ★ 의 구간이면 2:1 로 옮긴 화면에서 커서가 가리키는 먼 점과 앞 점을 도장으로 잇지 않게
      pointerup();
      if (to.key !== M.key) {
        const n = M.pending();
        const x = candOf(M.key);
        const gone = x && (!x.c.picked || x.c.rejected) ? " — 이제 ★ 가 아니라 적용할 수 없음" : "";
        if (n && !window.confirm(`적용 안 한 획 ${n}(${label(M.key)}${gone})을 버리고 ${label(to.key)} 를 편집할까요?`)) return false;
        open(to.key, to.seam, follow && !to.seam);
      } else if (to.seam) {
        seam = to.seam;
      }
      if (to.seam) viewer.showSeam(to.seam);
      render();
      return true;
    }

    function reload() {
      // [다시 받기](409·읽기 실패): 서버 판으로 다시 연다 — 적용 안 한 획은 확인 뒤 버림
      const n = M.pending();
      if (n && !window.confirm(`적용 안 한 획 ${n} 을 버리고 서버 버전을 다시 받을까요?`)) return;
      open(M.key, seam, false);
    }

    async function refreshInfo() {
      if (!M.key || M.busy || M.stroke) {
        again = true;
        return;
      }
      again = false;
      seenRev = S ? stateVer(S) : seenRev;
      if (await M.refresh()) repaintAll();
      render();
      await layers();
    }

    // ── 붓(뷰어가 왼쪽 버튼을 먼저 넘김 — true = 소비) ──
    function pointerdown(e) {
      if (!M.m || M.busy) return false; // 받는 중·적용 중 — 왼쪽 끌기는 화면 이동
      const pt = viewer.canvasPoint(e);
      soft = e.shiftKey;
      cursor = pt;
      repaint(M.begin(soft ? "soften" : tool, pt, size / 2, hard, flow));
      moveRing();
      return true;
    }

    function pointermove(e) {
      const pt = viewer.canvasPoint(e);
      cursor = pt;
      if (M.stroke) repaint(M.move(pt));
      else {
        soft = e.shiftKey;
        if (slider && M.layers) {
          view.split = Math.round(pt[0] - M.layers.box[0]);
          repaintAll();
        }
      }
      moveRing();
      return true;
    }

    function pointerup() {
      if (!M.stroke) return;
      M.end();
      renderPanel();
      if (again) refreshInfo();
    }

    function moveRing() {
      if (!cursor) return;
      ring.setAttribute("cx", cursor[0]);
      ring.setAttribute("cy", cursor[1]);
      ring.setAttribute("r", size / 2);
      ring.setAttribute("class", "mbrush" + (soft || tool === "soften" ? " soft" : tool === "revive" ? " revive" : ""));
      if (!ring.parentNode) viewer.redraw();
    }

    function draw(root) {
      // 뷰어 marks(크롭 사각형·이름은 뷰어가 그리지 않음 — 편집기 모드): 편집 창(점선)·이 크롭·선택 이음매·붓 원(화면 px 선, 크기 = 크롭 px).
      // 다른 대상을 받는 동안은 창·크롭을 그리지 않음(패널 대상 칸과 같음 — 앞 대상의 창이 새 대상의 '이 크롭'처럼 보이지 않게)
      if (!on) return;
      const box = (r, cls) => root.append(svg("rect", { x: r[0], y: r[1], width: r[2] - r[0], height: r[3] - r[1], class: cls }));
      const info = cur();
      const crop = info && cropOf(info.crop_id);
      if (viewer.flags().rectsOn) { // 뷰어 [사각형](O)으로 숨김 — 경계를 선 없이 눈으로 볼 때(F37)
        if (info && M.win) box(M.win, "mwin");
        if (crop) box(crop.rect, "mcrop");
      }
      if (seam) root.append(svg("line", { x1: seam.x0, y1: seam.y0, x2: seam.x1, y2: seam.y1, class: "mseam" }));
      if (cursor) root.append(ring);
    }

    // ── 연산 ──
    function once(r) {
      if (!r) return;
      repaint(r);
      renderPanel();
    }

    function rangeFade() {
      // 선택 이음매 구간 ± 폭 테이퍼 — a·b = 구간 끝 − 크롭 rect 의 x0(위·아래 변)·y0(왼·오른 변)
      const rect = cropRect();
      if (!rect || !seam) return;
      const o = seam.side === "top" || seam.side === "bottom" ? rect[0] : rect[1];
      const [a, b] = along(seam);
      once(M.fade(seam.side, width, rect, a - o, b - o));
    }

    function step(back) {
      once(M.step(back));
    }

    function discard() {
      const n = M.pending();
      if (!n || !window.confirm(`적용 안 한 획 ${n} 을 모두 버릴까요?`)) return;
      once(M.discard());
      if (M.behind()) refreshInfo(); // 획이 있는 동안 다른 창·노드가 새 판을 씀 — 버렸으니 서버 판으로 다시 연다
    }

    async function apply() {
      // [적용](Enter): 적용 중 단추 끔, 409 = 세션을 남기고 '다른 창에서 바뀜 — 다시 받기', 성공 = 보드 state 다시(★ 합성·이음매가 새 서명으로)
      if (!M.pending() || M.busy || M.stroke) return;
      const before = cropSeams();
      const key = M.key;
      const from = S && S.canvas ? S.canvas.composite_sig : null;
      const p = M.apply();
      render();
      let d = null;
      try {
        d = await p;
      } catch (e) {
        opts.toast(`적용하지 못함: ${e.message}`, "err");
      }
      if (d && d.ok) {
        applied = { key, from, sig: d.composite_sig, before: before ? before.list : null };
        opts.toast(`마스크 편집 적용 — ${label(key)} · 이음매를 다시 잽니다`);
        repaintAll();
        opts.refresh();
      } else if (d) {
        opts.toast(`적용 안 됨 — ${d.error}`, "warn");
      }
      render();
      if (again) refreshInfo();
    }

    async function serverEdit(kind) {
      const n = M.pending();
      const ed = M.info && M.info.edit;
      const ask = kind === "reset"
        ? `${label(M.key)} 의 마스크 편집 기록(이전 버전 ${ed ? ed.hist : 0} 포함)을 지우고 처음 마스크로 돌아갑니다.` + (n ? ` 적용 안 한 획 ${n} 도 버립니다.` : "")
          + " 계속할까요?"
        : n ? `적용 안 한 획 ${n} 을 버리고 이전 버전으로 돌아갈까요?` : "";
      if (ask && !window.confirm(ask)) return;
      const p = M.edit(kind);
      render();
      let d = null;
      try {
        d = await p;
      } catch (e) {
        opts.toast(e.message, "err");
      }
      if (d && d.ok) {
        applied = null;
        opts.toast(kind === "reset" ? `마스크 편집 지움 — ${label(M.key)} 은 처음 마스크로` : `이전 버전으로 — ${label(M.key)}`);
        repaintAll();
        opts.refresh();
      } else if (d) {
        opts.toast(`되돌리지 못함 — ${d.error}`, "warn");
      }
      render();
      await layers();
    }

    function altSet(key) {
      // 대안 표의 후보 집합(그 타깃의 탈락 아닌 다른 후보 key + 그 후보의 저장된 편집 판 — 다시 ★ 하면 쓰이는 편집이라 가정 합성의 재료) — ★ 합성
      // 서명이 그대로여도 Run 의 새 후보·다른 창의 탈락·대안 편집 되돌림·지움이면 표가 낡음
      const x = candOf(key);
      const ed = (c) => (c.mask_edit ? `${c.mask_edit.at}${c.mask_edit.stale ? "!" : ""}` : "");
      return x ? x.t.candidates.filter((c) => c.key !== key && !c.rejected).map((c) => `${c.key}@${ed(c)}`) : [];
    }

    async function loadAlts() {
      const key = M.key;
      const set = altSet(key);
      alts = { key, set: set.join(","), n: set.length, data: null, err: null };
      renderPanel();
      try {
        const d = await M.alts(key);
        if (alts && alts.key === key) alts.data = d;
      } catch (e) {
        if (alts && alts.key === key) alts.err = e.message;
      }
      if (on) renderPanel();
    }

    async function starAlt(r) {
      // [이 후보를 ★] = 검토의 Space(POST /pick set) — 이 크롭 편집은 옛 ★ 에 남는다. 새 ★ 가 예전 ★ 일 때 남긴 편집 기록이 있으면 그 편집이 바로
      // 다시 쓰이고(§3.1 다시 ★ 하면 살아남 — 표 값도 그 편집으로 계산), 없으면 기준 마스크로 시작
      const x = candOf(M.key);
      if (!x) return;
      const y = candOf(r.key);
      const ed = y && y.c.mask_edit;
      const n = M.pending();
      if (!window.confirm(`c${pad(r.c)} 를 ${x.t.crop_name || x.t.crop_id} · ${x.t.tid} 의 ★ 로 바꿉니다(검토의 Space 와 같음).\n`
        + `이 크롭의 마스크 편집은 옛 ★ c${pad(x.c.n)} 에 남고(다시 ★ 하면 살아남) `
        + (ed ? `c${pad(r.c)} 에 남아 있던 편집(${when(ed.at)}${ed.stale ? " · 처음 마스크가 바뀜" : ""})이 다시 쓰입니다(표 값도 그 편집으로 계산 — `
          + "지우려면 ★ 뒤 [편집 모두 지우기])." : "새 ★ 는 편집 없이 처음 마스크로 시작합니다.")
        + (n ? `\n적용 안 한 획 ${n} 은 버립니다.` : "") + "\n계속할까요?")) return;
      // 보드 ★ 쓰기(POST /pick + state 다시 — 수백 ms) 동안 세션을 잠근다(붓·Enter·실행 취소·대상 바꾸기·나가기 거절): 확인한 뒤의 획·적용이 버리기로 한
      // 옛 ★ 에 들어가거나 새 대상 세션에 남지 않게. 쓰기가 실패하면(보드가 다른 쓰기 중·409·네트워크 — null) 대상·세션 그대로
      M.busy = "pick";
      render();
      const ok = await opts.pick(x.t, r.key);
      M.busy = "";
      if (ok) open(r.key, null, false);
      else {
        render();
        if (again) refreshInfo();
      }
    }

    function setTool(k) {
      tool = k;
      moveRing();
      renderPanel();
    }

    function nudge(e) {
      // [ ] = 크기 ÷× 1.2, Shift+[ ] = 경도 ∓ 0.1
      const up = e.code === "BracketRight";
      if (e.shiftKey) hard = Math.round(Math.min(1, Math.max(0, hard + (up ? 0.1 : -0.1))) * 100) / 100;
      else size = Math.min(SIZE[1], Math.max(SIZE[0], Math.round(up ? size * 1.2 : size / 1.2)));
      moveRing();
      renderPanel();
    }

    function setView(k) {
      if (k === "slider") {
        slider = !slider;
        view.split = slider && cursor && M.layers ? Math.round(cursor[0] - M.layers.box[0]) : null;
      } else view[k] = !view[k];
      repaintAll();
      renderPanel();
    }

    // ── 그리기 ──
    function render() {
      if (!on) return;
      renderList();
      renderPanel();
      viewer.redraw();
    }

    function renderList() {
      // 왼쪽: 크롭(칩 편집 n·편집 기준 바뀜·이음매 n) — 누르면 그 크롭 첫 타깃 ★. ★ 가 둘 이상이면(타깃 여럿·slice) 줄마다
      if (held) {
        later = true;
        return;
      }
      list.replaceChildren();
      if (!S) return;
      const segs = (opts.seams() && opts.seams().segments) || [];
      const curCrop = cur() ? cur().crop_id : (candOf(M.key) || { t: {} }).t.crop_id;
      for (const c of S.crops) {
        const stars = S.targets.filter((t) => t.crop_id === c.id)
          .flatMap((t) => t.pick_keys.map((k) => ({ t, c: t.candidates.find((x) => x.key === k) })).filter((x) => x.c));
        const li = el("li", (c.id === curCrop ? "sel" : "") + (stars.length ? "" : " empty"));
        li.append(el("div", "tname", c.name || c.id));
        const ch = el("div", "chips");
        if (c.mask_edits) ch.append(chip(`편집 ${c.mask_edits}`, "b-reg"));
        if (c.mask_stale) ch.append(chip("처음 마스크가 바뀜", "b-bad"));
        const mine = segs.filter((x) => x.crop_id === c.id);
        if (mine.length) ch.append(chip(`이음매 ${mine.length}`, mine.some((x) => x.severity === "bad") ? "b-bad" : "b-wait"));
        if (!stars.length) ch.append(chip("★ 없음", "b-dim"));
        if (ch.childElementCount) li.append(ch);
        li.addEventListener("click", () => (stars.length ? go({ key: stars[0].c.key }, true) : opts.toast("이 크롭은 ★ 가 없습니다 — 검토에서 ★ 를 먼저 고르세요", "warn")));
        list.append(li);
        if (stars.length < 2) continue;
        for (const x of stars) {
          const sub = el("li", "sub" + (x.c.key === M.key ? " sel" : ""));
          sub.append(el("div", "tname", `${x.t.tid} · c${pad(x.c.n)} ${x.c.slice_index > 1 ? `조각 ${x.c.slice_index}` : "★"}`));
          if (x.c.mask_edit) sub.append(chip(x.c.mask_edit.stale ? "처음 마스크가 바뀜" : "편집", x.c.mask_edit.stale ? "b-bad" : "b-reg"));
          sub.addEventListener("click", (e) => {
            e.stopPropagation();
            go({ key: x.c.key }, true);
          });
          list.append(sub);
        }
      }
    }

    function renderPanel() {
      if (held) {
        later = true;
        return;
      }
      panel.replaceChildren();
      if (!on) return;
      const info = cur();
      const x = candOf(M.key);
      const busy = !!M.busy || !!M.stroke;

      // 1 대상
      let s = section("대상");
      if (info) {
        const [w, h] = [info.rect[2] - info.rect[0], info.rect[3] - info.rect[1]];
        const star = x && x.c.slice_index > 1 ? `조각 ${x.c.slice_index}` : "★";
        const r = line(s, `${x ? x.t.crop_name || x.t.crop_id : info.crop_id} · ${info.tid} · c${pad(info.c)} ${star} — 처음 마스크 ${SRC[info.base.src] || info.base.src} `
          + `${w}×${h} (z: 아래 ${info.z.below.length} · 위 ${info.z.above.length})`);
        r.title = `아래: ${info.z.below.join(", ") || "없음"}\n위: ${info.z.above.join(", ") || "없음"}`;
        if (info.base.src === "auto") {
          // 자동 마스크는 대상을 알아보는 것이 아님(10/21 실사용 — '프롬프트로 대상을 찾은 영역'으로 읽힘, U-2)
          line(s, "자동 마스크 = Analyze 가 후보와 원본 크롭의 색 차이(ΔE)로 만든 영역 — 대상인지와 무관: 엉뚱하게 바뀐 곳은 [지우기], 대상 안 구멍은 [살리기]", "dim");
        }
        const ed = info.edit;
        if (!ed) line(s, "편집 없음 — 처음 마스크 그대로", "dim");
        else {
          // url = 기준 url 이면 _plan 이 편집 파일을 쓰지 못함(없음·깨짐 — 경고 줄) — state 칩('편집 기준 바뀜')처럼 초록으로 두지 않음
          line(s, `편집 ${when(ed.at)} · ${ed.ops || "요약 없음"}`, ed.stale || ed.url === info.base.url ? "" : "ok");
          if (ed.stale) {
            line(s, "처음 마스크가 바뀜 — 처음 마스크(재분석·마스크 출처)나 편집 창(위치 맞춤 켬/끔·quad)이 편집을 시작한 때와 다름(편집은 그대로 쓰임 — "
              + "패치와 어긋났을 수 있음) ", "bad")
              .append(off(button("처음 마스크로 다시 시작", "편집(이전 버전 포함)을 지우고 지금 처음 마스크로 — 확인 창", () => serverEdit("reset"), "inline"), busy));
          }
        }
        for (const wn of info.warnings) line(s, wn, "warn");
      } else if (M.busy === "load") {
        line(s, `${label(M.key)} 불러오는 중…`, "dim");
      }
      if (x && (!x.c.picked || x.c.rejected)) {
        // 검토(다른 창 포함)에서 ★ 해제·탈락 — 서버가 409 not_picked 로 거절하고 [다시 받기]도 같은 거절이라 나갈 길을 적음
        line(s, "이 후보는 이제 ★ 가 아님(검토에서 ★ 해제·제외) — 적용할 수 없음: 왼쪽 목록에서 크롭을 누르면 지금 ★ 로(적용 안 한 획은 확인 뒤 버림)", "bad");
      }
      if (M.err) line(s, `${M.err} `, "bad").append(off(button("다시 받기", "GET /mask 다시", reload, "inline"), busy));
      if (layerErr) line(s, `미리보기 3층을 받지 못함: ${layerErr} `, "warn").append(off(button("다시 받기", "GET /mask·3층 다시", refreshInfo, "inline"), busy));
      line(s, `보기: ${view.orig ? "원본 — 이 패치 없이(W)" : view.base ? "처음 마스크(Q 누르는 중)" : slider ? "처음|편집 슬라이더(S — 왼쪽 = 처음 마스크)" : "편집"}`
        + `${view.mark ? " · 마스크(M)" : ""}${viewer.flags().rectsOn ? "" : " · 사각형 숨김(O)"}`, "dim");
      const ob = button("원본 보기 (W)", "이 패치를 뺀 그림(아래·위 ★ 만 — 고치기 전) ↔ 지금 결과. W 는 누르는 동안만", () => {
        origKey = false;
        setView("orig");
      });
      ob.classList.toggle("on", view.orig);
      const mb = button("마스크 (M)", "빨강 = 보이는 패치, 회색 빗금 = 처음 마스크보다 지운 곳", () => setView("mark"));
      mb.classList.toggle("on", view.mark);
      const rb = button("선 숨기기 (O)", "크롭·편집 창 사각형 선을 숨김/보임 — 경계를 선 없이 눈으로 볼 때(뷰어 [사각형]과 같음)", () => {
        viewer.toggleRects();
        renderPanel();
      });
      rb.classList.toggle("on", !viewer.flags().rectsOn);
      btns(s, ob, mb, rb);

      // 2 붓
      s = section("붓 (왼쪽 끌기 · Space+끌기 = 화면 이동)");
      btns(s, ...Object.keys(TOOLS).map((k) => {
        const b = button(TOOLS[k], k === "soften" ? "흐리기 — Shift 를 누른 채 끌어도(붓 원 흰 점선)" : `X: 지우기 ↔ 살리기(붓 원 ${k === "erase" ? "빨강" : "초록"})`,
          () => setTool(k), `mt-${k}`);
        b.classList.toggle("on", tool === k);
        return b;
      }));
      slide(s, "크기", SIZE[0], SIZE[1], 1, size, (v) => `${v}px`, (v) => {
        size = v;
        moveRing();
        paintPreview();
      });
      slide(s, "경도", 0, 1, 0.05, hard, (v) => v.toFixed(2), (v) => {
        hard = v;
        paintPreview();
      });
      slide(s, "흐름", FLOW[0], FLOW[1], 0.05, flow, (v) => v.toFixed(2), (v) => {
        flow = v;
        paintPreview();
      });
      preview = el("canvas", "mpreview");
      preview.width = preview.height = PREVIEW;
      s.append(preview);
      paintPreview();

      // 3 한 번에
      s = section("자동 손질");
      const can = !!M.m && !busy;
      const rect = cropRect();
      btns(s, ...Object.keys(SIDES).map((k) => off(button(`${SIDES[k]} 가장자리 페이드`, `크롭 ${k} 가장자리 전체 — 경계 0 → 안쪽 ${width}px 에서 1(smoothstep)`,
        () => once(M.fade(k, width, rect))), !can || !rect)));
      const sg = seam && info && seam.crop_id === info.crop_id ? seam : null;
      btns(s, off(button("이음매 구간 페이드", sg ? `${seamName(sg)} ± ${width}px 테이퍼` : "아래 이음매 목록이나 N 으로 구간을 고르세요", rangeFade),
        !can || !rect || !sg), ...WIDTHS.map((v) => {
        const b = button(`폭 ${v}`, "경계 페이드 폭(32 = 짧은 띠 · 64 = 기본 · 128 = 넓은 띠에서 이중 윤곽 주의)", () => {
          width = v;
          renderPanel();
        });
        b.classList.toggle("on", width === v);
        return b;
      }));
      slide(s, "페더 (px)", SIGMA[0], SIGMA[1], 1, sigma, (v) => `${v}`, (v) => { sigma = v; });
      btns(s, off(button("전체 페더", "창 전체를 가우시안 σ 로 흐림(한 단계)", () => once(M.feather(sigma))), !can),
        off(button("처음 마스크로", "편집 중 마스크를 처음 마스크로(한 단계 — Ctrl+Z 로 되돌림)", () => once(M.toBase())), !can));

      // 4 세션
      s = section("적용 전 획");
      const n = M.pending();
      line(s, M.busy === "apply" ? `적용 중… (획 ${n})` : n ? `적용 안 한 획 ${n} — ${M.ops()}` : "적용 안 한 획 0", n ? "warn" : "dim");
      btns(s, off(button("실행 취소", "Ctrl+Z — 획 단위(최근 50)", () => step(true)), busy || !M.undo.length),
        off(button("다시", "Ctrl+Y · Ctrl+Shift+Z", () => step(false)), busy || !M.redo.length),
        off(button("적용", "Enter — 편집 창 크기 8비트 PNG 저장(무료) · Compose·Export·★ 합성이 이 마스크를 씀", apply, "primary"), busy || !n),
        off(button("버리기", "적용 안 한 획을 모두 버림(확인 창)", discard), busy || !n));
      if (M.conflict) {
        line(s, `${M.conflict.error} — 적용 전 획은 남김(새 버전 위에 다시 할지 정하세요) `, "bad")
          .append(off(button("다시 받기", "서버 버전으로 다시 열기 — 적용 안 한 획은 버림(확인 창)", reload, "inline"), busy));
      } else if (info && M.behind()) {
        // 실행 취소로 0 이 돼도 세션(다시 Ctrl+Y)은 연 판 그대로 — 미리보기 ≠ Compose·Export 임을 알림. 기록 file·창이 바뀌었으면 서버가 거절(409 changed·
        // rect_changed), 기준 서명만 바뀌었으면(같은 창·같은 기록 — 재분석·마스크 출처) 받아서 지금 기준의 편집으로 저장한다
        const refused = (info.edit ? info.edit.file : "") !== M.file || info.rect.join() !== M.win.join();
        line(s, refused ? "서버 버전이 바뀜(다른 창·노드) — 적용 전 획·미리보기는 연 버전(적용하면 거절) "
          : "처음 마스크가 바뀜(재분석·마스크 출처) — 적용 전 획·미리보기는 연 버전의 처음 마스크(적용하면 지금 처음 마스크의 편집으로 저장 — '처음 마스크가 바뀜' 표시 사라짐) ",
        "warn").append(off(button("다시 받기", "서버 버전으로 다시 열기 — 적용 안 한 획은 버림(확인 창)", reload, "inline"), busy));
      }

      // 5 저장된 판
      s = section("저장된 버전");
      const ed = info && info.edit;
      line(s, ed ? `이전 버전 ${ed.hist}` : "저장된 편집 없음", "dim");
      btns(s, off(button("이전 버전으로", "기록의 이전 버전으로(없으면 기록 지움 = 처음 마스크로)", () => serverEdit("undo")), busy || !ed),
        off(button("편집 모두 지우기", "기록(이전 버전 포함)을 지우고 처음 마스크로 — 확인 창", () => serverEdit("reset"), "danger"), busy || !ed));

      // 6 이 크롭 이음매
      s = section("이 크롭 이음매");
      const cs = cropSeams();
      const now = S && S.canvas ? S.canvas.composite_sig : null;
      if (applied && now !== applied.from && now !== applied.sig) applied = null; // 그 뒤 다른 쓰기(다른 창·노드)로 합성이 바뀜
      if (!cs) line(s, "계산 중…", "dim");
      else if (!cs.list.length) line(s, "이음매 없음", "ok");
      for (const y of (cs && cs.list) || []) {
        const r = line(s, `${seamName(y)} · ${y.length}px · p90 ${y.diff_p90} ${y.severity}` + (y.key !== M.key ? ` · ${y.tid} ★` : ""),
          (y.severity === "bad" ? "bad" : "warn") + " link" + (seam && seam.key === y.key && seamName(seam) === seamName(y) ? " sel" : ""));
        r.addEventListener("click", () => go({ key: y.key, seam: y }));
      }
      if (applied && applied.key === M.key && applied.before) {
        if (!cs || cs.sig !== applied.sig) line(s, "적용 뒤 이음매 다시 계산 중…", "dim");
        else for (const t of diffLines(applied.before, cs.list)) line(s, `적용 전후 ${t}`, "dim");
      }
      line(s, "검사는 경계에서 잘린 곳만 — 페이드 띠 안의 겹침은 눈으로(Q·S·M)", "dim");

      // 7 다른 후보로
      s = section("다른 후보로");
      // 표를 계산한 뒤 ★ 합성이 바뀜(적용·되돌리기·다른 창) 또는 후보가 바뀜(Run 의 새 후보·다른 창의 탈락·대안 편집 — 탈락 행의 [이 후보를 ★]가
      // 탈락을 풀지 않게, 낡은 수치로 고르지 않게)
      const mine = !!alts && alts.key === M.key && !!alts.data;
      const old = mine && alts.data.sig !== now ? "★ 합성이" : mine && alts.set !== altSet(M.key).join(",") ? "후보가(새 후보·제외·편집)" : "";
      if (!alts || alts.key !== M.key || old) {
        if (old) line(s, `${old} 바뀌어 표가 낡음 — 다시 계산하세요`, "dim");
        btns(s, off(button("다른 후보로", "같은 타깃의 다른 후보를 ★ 로 바꾸면 이음매가 어떻게 되나(후보당 ≈ 2.5초 — 겹친 크롭 영향 포함)", loadAlts), !info));
      } else if (alts.err) {
        line(s, `계산하지 못함: ${alts.err}`, "bad");
        btns(s, button("다시", "다시 계산", loadAlts));
      } else if (!alts.data) {
        line(s, `계산 중 — 후보 ${alts.n} × ≈ ${ALT_SEC}초(한 번에 하나 — 다른 합성이 돌면 기다림)`, "dim");
      } else {
        altsTable(s, alts.data);
      }
    }

    function paintPreview() {
      // 붓 미리보기(F36 — 포토샵 브러시 칸처럼): 도장 한 번에 바뀌는 양(흰 = 많이) — 원 = 크기(크롭 px 1:1, 68 까지), 안쪽 단단한 곳 = 경도, 밝기 = 세기
      if (!preview || !preview.isConnected) return;
      const g = preview.getContext("2d");
      const im = g.createImageData(PREVIEW, PREVIEW);
      const R = Math.max(1.5, Math.min(PREVIEW / 2 - 2, size / 2)); // 붓 크기 그대로(지름 68px 까지 — 더 크면 칸에 꽉, Codex 4차)
      for (let y = 0; y < PREVIEW; y++) {
        for (let x = 0; x < PREVIEW; x++) {
          const i = (y * PREVIEW + x) * 4;
          im.data[i] = im.data[i + 1] = im.data[i + 2] = Math.round(stamp(R, hard, Math.hypot(x + 0.5 - PREVIEW / 2, y + 0.5 - PREVIEW / 2)) * flow * 255);
          im.data[i + 3] = 255;
        }
      }
      g.putImageData(im, 0, 0);
      preview.title = `크기 ${size}px${size > PREVIEW - 4 ? `(칸보다 커서 ${PREVIEW - 4}px 로 줄여 보임)` : "(실제 크기)"} · 경도 ${hard.toFixed(2)} · 흐름 ${flow.toFixed(2)}`
        + " — 흰 = 한 번 찍을 때 바뀌는 양(같은 곳을 여러 번 지나가면 쌓임)";
    }

    function slide(s, k, min, max, stepV, val, fmt, set) {
      const row = el("div", "rf-slider");
      const inp = el("input");
      Object.assign(inp, { type: "range", min, max, step: stepV, value: val });
      inp.dataset.keys = "board";
      const v = el("span", "rf-val", fmt(val));
      inp.addEventListener("input", () => {
        set(Number(inp.value));
        v.textContent = fmt(Number(inp.value));
      });
      inp.addEventListener("change", () => inp.blur());
      row.append(el("span", "k", k), inp, v);
      s.append(row);
    }

    function diffLines(before, after) {
      // 적용 전후 — 같은 변·같은 ★·구간이 겹치는 것끼리: 'top 575–1337 p90 43.3 → 없음', 새로 생긴 구간도 한 줄
      const meet = (a, b) => a.side === b.side && a.key === b.key && along(a)[0] <= along(b)[1] && along(b)[0] <= along(a)[1];
      const out = [`bad ${before.filter((b) => b.severity === "bad").length} → ${after.filter((a) => a.severity === "bad").length}`];
      for (const b of before) {
        const hit = after.filter((a) => meet(a, b));
        out.push(`${seamName(b)} p90 ${b.diff_p90} → ${hit.length ? hit.map((a) => `${a.diff_p90} ${a.severity}`).join(", ") : "없음"}`);
      }
      for (const a of after) if (!before.some((b) => meet(a, b))) out.push(`${seamName(a)} p90 ${a.diff_p90} ${a.severity} — 새로 생김`);
      return out;
    }

    function altsTable(s, d) {
      const x = candOf(M.key);
      line(s, `지금 c${pad(x ? x.c.n : 0)} ★ · 이 크롭 bad ${d.now.bad} · 최악 p90 ${d.now.worst ?? "없음"}`);
      if (!d.rows.length) {
        line(s, "다른 후보 없음(제외한 후보는 빼고)", "dim");
        return;
      }
      // 단추가 첫 열 — 패널이 좁아 표가 가로로 넘쳐도 보이게(완료·바뀌는 크롭은 줄바꿈). 후보 칸 칩 = 예전 ★ 때 남긴 편집(★ 하면 다시 쓰임)
      const t = el("table", "pp-table");
      const hr = el("tr");
      for (const h of ["", "후보", "완료", "bad", "weak", "최악 p90", "바뀌는 크롭"]) hr.append(el("th", "", h));
      t.append(hr);
      for (const r of d.rows) {
        const tr = el("tr");
        const td = el("td");
        td.append(off(button("이 후보를 ★", "검토의 Space 와 같음(확인 창) — 이 크롭 편집은 옛 ★ 에 남음", () => starAlt(r), "inline"), !!M.busy));
        const c = el("td", "", `c${pad(r.c)}`);
        const ed = (candOf(r.key) || { c: {} }).c.mask_edit;
        if (ed) c.append(chip(ed.stale ? "처음 마스크가 바뀜" : "편집", ed.stale ? "b-bad" : "b-reg"));
        const changed = r.changed_crops.map((id) => (cropOf(id) || { name: id }).name || id).join(", ");
        tr.append(td, c, el("td", "wrap", r.ran ? opts.ranText(r.ran) : ""), ...[r.bad, r.weak, r.worst_p90 ?? ""].map((v) => el("td", "", v)),
          el("td", "wrap", changed));
        t.append(tr);
      }
      s.append(t);
      line(s, "표는 이음매만 봅니다 — 디자인(레퍼런스 반영)은 검토 화면에서", "dim");
    }

    // ── 키(board.js 가 K 모드에서 먼저 넘김 — 입력칸 포커스는 board.js 가 이미 거름). true = 소비 ──
    function onKey(e) {
      if (!on) return false;
      const mod = e.ctrlKey || e.metaKey;
      if (mod && (e.code === "KeyZ" || e.code === "KeyY")) {
        if (!e.repeat) step(e.code === "KeyZ" && !e.shiftKey);
        return true;
      }
      if (mod || e.altKey) return false;
      switch (e.code) {
        case "Escape":
          opts.exit();
          return true;
        case "Enter":
          if (!e.repeat) apply();
          return true;
        case "KeyX":
          if (!e.repeat) setTool(tool === "erase" ? "revive" : "erase");
          return true;
        case "BracketLeft":
        case "BracketRight":
          nudge(e);
          return true;
        case "KeyQ":
          if (!view.base) setView("base");
          return true;
        case "KeyS":
          if (!e.repeat) setView("slider");
          return true;
        case "KeyM":
          if (!e.repeat) setView("mark");
          return true;
        case "KeyW":
          if (!view.orig) {
            origKey = true;
            setView("orig");
          }
          return true;
        case "KeyF":
        case "Digit0":
        case "Digit1":
        case "Digit2":
          pointerup(); // 뷰어 맞춤·1:1·2:1(board 가 이어서 — 커서 기준이 아님): 끌던 획을 먼저 끝냄(옮긴 화면의 먼 커서 점과 앞 점을 잇지 않게)
          return false;
        default:
          return false;
      }
    }

    function onKeyUp(e) {
      if (e.code === "KeyQ" && view.base) setView("base");
      if (e.code === "KeyW" && view.orig && origKey) {
        origKey = false;
        setView("orig");
      }
    }

    window.addEventListener("blur", () => {
      onKeyUp({ code: "KeyQ" });
      onKeyUp({ code: "KeyW" });
    });

    function flushLater() {
      if (!later || held) return;
      later = false;
      render();
    }

    const release = () => {
      held = false;
      setTimeout(flushLater, 0); // click 처리 뒤
    };
    document.addEventListener("pointerdown", () => {
      held = true;
    }, true);
    document.addEventListener("pointerup", release, true);
    document.addEventListener("pointercancel", release, true);
    // pointerup 을 놓친 경우(창 밖에서 놓음): 버튼 없이 움직이면 놓은 것으로
    document.addEventListener("pointermove", (e) => {
      if (held && !e.buttons) release();
    }, true);

    // ── board.js ──
    function enter(st, to) {
      // K 모드 진입: to = {key, seam}. 세션이 있는 같은 후보면 이어서(선택 구간만), 다른 후보면 go(적용 안 한 획 확인 — 아니오면 세션 후보로 이어서)
      on = true;
      S = st;
      viewer.setEditor({ pointerdown, pointermove, pointerup, cancel: pointerup, draw });
      if (to.key !== M.key && go(to, true)) return;
      if (to.key === M.key && to.seam) go(to);
      opts.select(M.key, !(to.key === M.key && to.seam));
      if (M.layers) attach();
      refreshInfo();
    }

    function leave() {
      // K 모드 나가기 — 적용·되돌리기·★ 바꾸기 중이면 거절(go 와 같음 — 나갈 때 board 가 진행 ★ 점검에 넘기는 적용 안 한 획 수가 응답 뒤 낡음), 끌던 획은 먼저
      // 끝냄(setEditor(null) 뒤엔 pointerup 이 오지 않음 — 확인 창·★ 점검의 획 수에 들어가게), 적용 안 한 획이 있으면 확인(세션은 이 창에 남음). → 나갔는가
      if (LOCKED.includes(M.busy)) {
        opts.toast("적용·되돌리기·★ 바꾸기를 처리하는 중 — 끝난 뒤 나가세요", "warn");
        return false;
      }
      pointerup();
      const n = M.pending();
      if (n && !window.confirm(`적용 안 한 획 ${n}(${label(M.key)})은 이 창에만 남습니다 — Export·다른 창에는 들어가지 않음(K 로 돌아오면 이어서). 나갈까요?`)) {
        return false;
      }
      on = false;
      view.base = view.mark = view.orig = slider = false;
      view.split = cursor = null;
      cancelAnimationFrame(raf);
      raf = 0;
      dirty = null;
      viewer.setEditor(null);
      viewer.setMaskLayer(null);
      list.replaceChildren();
      panel.replaceChildren();
      return true;
    }

    function stateVer(st) {
      // 편집 창·기준·★ 합성의 재료 판: 매니페스트 rev + board_pref.json 지문(분석·Export 설정 — 바뀌어도 rev 는 그대로)
      return st ? `${st.rev}|${st.pref_v}` : null;
    }

    function stateChanged(st) {
      // 새 state(보드 renderAll — 매니페스트 rev 또는 pref_v 가 바뀜): 편집 대상 정보를 다시 받는다(받는 중·획 중이면 끝난 뒤)
      S = st;
      if (!on) return;
      if (stateVer(st) !== seenRev) refreshInfo();
      render();
    }

    function seamsChanged() {
      if (!on) return;
      renderList();
      renderPanel();
    }

    function pending() {
      // 이 창의 적용 안 한 마스크 편집(진행(G) ★ 점검 줄) → {key, n, name} | null
      const n = M.pending();
      const x = n ? candOf(M.key) : null;
      return n ? { key: M.key, n, name: x ? `${x.t.crop_name || x.t.crop_id} · ${x.t.tid}` : M.key } : null;
    }

    return { enter, leave, go, onKey, onKeyUp, stateChanged, seamsChanged, pending };
  }

  return { create, session, smooth, fade, kernel, feather, stamp, op, quant, compose, pngL };
})();
