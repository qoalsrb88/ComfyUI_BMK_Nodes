// BMK Design Patch Studio — 레이아웃 편집기 기하·이름 규칙(순수 함수, DOM 없음). STUDIO_S1_SPEC §3.2·§3.6·§3.7, 계약 G1–G7.
// Python 기준(tools/test_bmk_design_patch.py 의 _layout_* 오라클 · bmk_design_patch_store 의 이름·문구·검사 규칙)과
// 공용 표 tools/fixtures/layout_rules.json 의 모든 행에서 같은 값을 내야 한다(layout_js_unit J-L1).
// 반올림은 rnd 하나(Math.round 금지 — 언어 차이), 좌표는 정수, rect = [x0, y0, x1, y1] 끝 배타.
"use strict";

window.DPLayoutGeom = (() => {
  const SIZE = 128; // 크기 스냅 단위 = 최소 변
  const POS = 16; // 위치 격자
  const RATIO = 3; // 긴 변 / 짧은 변 최대
  const SMALL = 384; // 짧은 변 정보 기준(L-small)
  const COORD_MAX = 65536;
  const TEXT_MAX = 120;
  // Python str.isspace 와 정규식 \s 의 문자(JS \s 와 달리 ﻿ 없음, \x1c-\x1f·\x85 있음)
  const WS = "\\t\\n\\v\\f\\r\\x1c-\\x1f \\x85\\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000";
  const STRIP_RE = new RegExp(`^[${WS}]+|[${WS}]+$`, "g");
  const NNN_RE = new RegExp(`^[${WS}]*([0-9\\uFF10-\\uFF19]+)[${WS}_\\-.]*([^]*)$`);
  const NAME_CHAR_RE = /^[\p{L}\p{N}_-]$/u;
  const TEXT_BAD_RE = /[\p{Cc}\u2028\u2029|\uFF5C\u2502\u00A6;\uFF1B]/u;
  // 레이어명 문법이 "…, B: …" 를 다른 대상으로 읽는 꼴(store._TARGET_SPLIT_RE 의 쉼표 갈래)
  const TEXT_SPLIT_RE = new RegExp(`[,\\uFF0C\\u3001](?=[${WS}]*[A-Z][${WS}]*[:\\uFF1A])`);
  const SIDES = { "왼쪽": "left", left: "left", "오른쪽": "right", right: "right" };

  // store._NAME_PROBLEMS · _TEXT_PROBLEMS 와 같은 문구
  const NAME_MSGS = {
    len: "이름은 5–64자여야 합니다",
    prefix: "이름은 세 자리 숫자와 '_' 로 시작해야 합니다(예: 009_모자)",
    char: "이름에는 글자·숫자·_·- 만 쓸 수 있습니다(공백·| ; : , / \\ < > \" ' ? * 금지)",
  };
  const TEXT_MSGS = {
    char: "대상 문구에 줄바꿈·제어 문자나 | ｜ │ ¦ ; ； 를 쓸 수 없습니다",
    len: `대상 문구는 ${TEXT_MAX}자 이하여야 합니다`,
    roundtrip: "대상 문구가 레이어명 문법과 부딪힙니다(', B: …' 처럼 쉼표 뒤 대문자 라벨 꼴은 다른 대상으로 읽힘)",
  };

  // GPT Image Custom 크기 제약(store.gpt_image_custom_size_ok)
  const GPT_MIN_EDGE = 480;
  const GPT_MAX_EDGE = 3840;
  const GPT_MIN_PX = 655360;
  const GPT_MAX_PX = 8294400;

  function rnd(v, q) {
    return Math.floor(v / q + 0.5) * q;
  }

  function strip(s) {
    return s.replace(STRIP_RE, "");
  }

  // ── G1–G4 사각형 ──
  function size(raw, snap) {
    // 앵커에서 움직인 변까지의 거리 → 크기(앵커를 넘으면 최소)
    return Math.max(SIZE, snap ? rnd(raw, SIZE) : rnd(raw, 1));
  }

  function move(rect, dx, dy, canvas, snap) {
    const [x0, y0, x1, y1] = rect;
    const q = snap ? POS : 1;
    const nx = Math.min(Math.max(rnd(x0 + dx, q), 0), Math.max(0, canvas[0] - (x1 - x0)));
    const ny = Math.min(Math.max(rnd(y0 + dy, q), 0), Math.max(0, canvas[1] - (y1 - y0)));
    return [nx, ny, nx + x1 - x0, ny + y1 - y0];
  }

  function place(ax, ay, mx, my, w, h, canvas, snap) {
    // resize·new 공용(표의 place): 비 > 3 자르기 → 앵커에서 사각형 → 움직이는 변만 캔버스로 자름 → 다시 비 > 3 이면 긴 축(앵커 고정).
    // 스냅 내림이 0 이면 1px 내림(16×48 같은 이관 크롭의 변 핸들 — 폭·높이 0 이 되지 않게)
    const down = snap ? (v) => Math.floor(v / SIZE) * SIZE || Math.floor(v) : Math.floor;
    const up = snap ? (v) => Math.ceil(v / SIZE) * SIZE : Math.ceil;
    if (Math.max(w, h) > RATIO * Math.min(w, h)) {
      if (mx && my) {
        if (w > h) w = down(RATIO * h);
        else h = down(RATIO * w);
      } else if (mx) {
        w = w > h ? down(RATIO * h) : up(h / RATIO);
      } else {
        h = h > w ? down(RATIO * w) : up(w / RATIO);
      }
    }
    let [x0, x1] = mx < 0 ? [ax - w, ax] : [ax, ax + w];
    let [y0, y1] = my < 0 ? [ay - h, ay] : [ay, ay + h];
    if (mx > 0) x1 = Math.min(x1, canvas[0]);
    else if (mx < 0) x0 = Math.max(x0, 0);
    if (my > 0) y1 = Math.min(y1, canvas[1]);
    else if (my < 0) y0 = Math.max(y0, 0);
    if (x1 - x0 > RATIO * (y1 - y0)) {
      if (mx < 0) x0 = x1 - RATIO * (y1 - y0);
      else x1 = x0 + RATIO * (y1 - y0);
    } else if (y1 - y0 > RATIO * (x1 - x0)) {
      if (my < 0) y0 = y1 - RATIO * (x1 - x0);
      else y1 = y0 + RATIO * (x1 - x0);
    }
    return [x0, y0, x1, y1];
  }

  function resize(rect, handle, dx, dy, canvas, snap, keep) {
    // G2 핸들 8개: 반대쪽 변이 앵커. keep(Shift)은 모서리만 — 상대 변화가 큰 축이 기준(같으면 x)
    const [x0, y0, x1, y1] = rect;
    const w0 = x1 - x0;
    const h0 = y1 - y0;
    const mx = handle.includes("e") ? 1 : handle.includes("w") ? -1 : 0;
    const my = handle.includes("s") ? 1 : handle.includes("n") ? -1 : 0;
    let w = mx ? size(w0 + mx * dx, snap) : w0;
    let h = my ? size(h0 + my * dy, snap) : h0;
    if (keep && mx && my) {
      if (Math.abs(w - w0) * h0 >= Math.abs(h - h0) * w0) h = size(w * h0 / w0, snap);
      else w = size(h * w0 / h0, snap);
    }
    return place(mx < 0 ? x1 : x0, my < 0 ? y1 : y0, mx, my, w, h, canvas, snap);
  }

  function grow(rect, sides, canvas, snap) {
    // 변마다 SIZE 만큼 끌되, 격자·비율 보정으로 줄어드는 결과는 건너뛴다. 다른 변이 넓어진 뒤 한 번 재시도.
    const contains = (a, b) => a[0] <= b[0] && a[1] <= b[1] && a[2] >= b[2] && a[3] >= b[3];
    let next = rect.slice();
    let pending = [["left", "w", -SIZE, 0], ["right", "e", SIZE, 0], ["top", "n", 0, -SIZE], ["bottom", "s", 0, SIZE]]
      .filter(([side]) => sides.includes(side)); // 입력 순서·중복 구간과 무관하게 같은 순서로, 받은 변은 한 번만
    for (let pass = 0; pass < 2; pass++) {
      const retry = [];
      for (const step of pending) {
        const [, handle, dx, dy] = step;
        const candidate = resize(next, handle, dx, dy, canvas, snap);
        if (contains(candidate, next) && candidate.some((v, i) => v !== next[i])) next = candidate;
        else retry.push(step);
      }
      pending = retry;
    }
    return contains(next, rect) && next.some((v, i) => v !== rect[i]) ? next : null;
  }

  function newRect(sx, sy, dx, dy, canvas, snap, keep) {
    // G3 빈 곳에서 끌기: 시작점 격자 → 캔버스 안 → 끈 방향 모서리(keep = 큰 쪽 정사각) → 끄는 쪽 캔버스 변까지 여유가 128 미만인 축은
    // 방향을 안쪽으로 뒤집음(변에서 바깥으로 끌어도 폭 0·아주 작은 사각형이 아님)
    const q = snap ? POS : 1;
    const ax = Math.min(Math.max(rnd(sx, q), 0), canvas[0]);
    const ay = Math.min(Math.max(rnd(sy, q), 0), canvas[1]);
    let mx = dx >= 0 ? 1 : -1;
    let my = dy >= 0 ? 1 : -1;
    let w = size(mx * dx, snap);
    let h = size(my * dy, snap);
    if (keep) w = h = Math.max(w, h);
    if ((mx > 0 ? canvas[0] - ax : ax) < SIZE) mx = -mx;
    if ((my > 0 ? canvas[1] - ay : ay) < SIZE) my = -my;
    return place(ax, ay, mx, my, w, h, canvas, snap);
  }

  function suggest(boxes, canvas, snap) {
    // G4 상자에 맞춘 크롭: 합집합 ÷ 0.7(정수 식 ×10/7) → 128 올림 → 비 ≤ 3 → 캔버스보다 크면 그 축 = 캔버스 → 그래서 비 > 3 이면
    // (4096×1000 같은 넓은 캔버스) 긴 축 = down(3·짧은)(S1-b — place 의 down 과 같음: 128 내림, 0 이면 1px 내림) → 줄인 크기로
    // 가운데 정렬(격자) → 캔버스 안
    const up = snap ? (v) => Math.ceil(v / SIZE) * SIZE : Math.ceil;
    const down = snap ? (v) => Math.floor(v / SIZE) * SIZE || Math.floor(v) : Math.floor;
    const ux0 = Math.min(...boxes.map((b) => b[0]));
    const uy0 = Math.min(...boxes.map((b) => b[1]));
    const ux1 = Math.max(...boxes.map((b) => b[2]));
    const uy1 = Math.max(...boxes.map((b) => b[3]));
    let w = Math.max(SIZE, up(Math.ceil((ux1 - ux0) * 10 / 7)));
    let h = Math.max(SIZE, up(Math.ceil((uy1 - uy0) * 10 / 7)));
    if (w > RATIO * h) h = up(w / RATIO);
    else if (h > RATIO * w) w = up(h / RATIO);
    w = Math.min(w, canvas[0]);
    h = Math.min(h, canvas[1]);
    if (w > RATIO * h) w = down(RATIO * h);
    else if (h > RATIO * w) h = down(RATIO * w);
    // 위치: 축마다 상자 여백이 15%(20m ≥ 3·크기 — 검사 P-70 과 같음)인 격자 값 중 가운데에 가장 가까운 것. 상자에서 캔버스 끝까지가 15% 보다
    // 가까운 쪽은 크롭 변을 캔버스 끝에 붙임(그 쪽 여백은 검사가 보지 않음 — P-canvas). 반올림으로 그런 값이 없으면 그 축만 한 칸(스냅 128,
    // 끔 1px) 키워 두 번까지(캔버스·비 3 안), 그래도 없으면 가운데
    const q = snap ? POS : 1;
    const step = snap ? SIZE : 1;
    const center = (u0, u1, s, lim) => Math.min(Math.max(rnd((u0 + u1) / 2 - s / 2, q), 0), lim - s);
    const fit = (u0, u1, s, lim) => {
      const need = Math.ceil(3 * s / 20);
      const lo = Math.max(u1 + need > lim ? lim - s : u1 + need - s, 0);
      const hi = Math.min(u0 - need < 0 ? 0 : u0 - need, lim - s);
      const c = center(u0, u1, s, lim);
      if (c >= lo && c <= hi) return c; // 예전 가운데 자리(캔버스 끝에 밀린 격자 밖 값 포함)가 여백을 지키면 그대로
      const a = Math.ceil(lo / q) * q;
      const b = Math.floor(hi / q) * q;
      if (a <= b) return Math.min(Math.max(c, a), b);
      return lim - s >= lo && lim - s <= hi ? lim - s : null; // 16 배수가 아닌 캔버스의 끝 자리(격자 밖)도 받음
    };
    let x0 = fit(ux0, ux1, w, canvas[0]);
    let y0 = fit(uy0, uy1, h, canvas[1]);
    for (let k = 0; k < 2 && (x0 === null || y0 === null); k++) {
      const w2 = x0 === null ? w + step : w;
      const h2 = y0 === null ? h + step : h;
      if (w2 > canvas[0] || h2 > canvas[1] || Math.max(w2, h2) > RATIO * Math.min(w2, h2)) break;
      [w, h] = [w2, h2];
      x0 = fit(ux0, ux1, w, canvas[0]);
      y0 = fit(uy0, uy1, h, canvas[1]);
    }
    if (x0 === null) x0 = center(ux0, ux1, w, canvas[0]);
    if (y0 === null) y0 = center(uy0, uy1, h, canvas[1]);
    return [x0, y0, x0 + w, y0 + h];
  }

  function rectProblem(rect, canvas) {
    // G5: 형식 오류 "error" → 캔버스 밖 "outside" → 비 > 3 "ratio" → null
    if (!Array.isArray(rect) || rect.length !== 4 || !rect.every((v) => Number.isInteger(v) && Math.abs(v) <= COORD_MAX)
        || rect[2] <= rect[0] || rect[3] <= rect[1]) {
      return "error";
    }
    const [x0, y0, x1, y1] = rect;
    if (x0 < 0 || y0 < 0 || x1 > canvas[0] || y1 > canvas[1]) return "outside";
    if (Math.max(x1 - x0, y1 - y0) > RATIO * Math.min(x1 - x0, y1 - y0)) return "ratio";
    return null;
  }

  // ── G6 GPT 출력 크기(store.gpt_output_size(w, h, "user_k")) ──
  function gptOk(W, H) {
    return W % 16 === 0 && H % 16 === 0 && W >= GPT_MIN_EDGE && W <= GPT_MAX_EDGE && H >= GPT_MIN_EDGE && H <= GPT_MAX_EDGE
      && Math.max(W, H) <= 3 * Math.min(W, H) && W * H >= GPT_MIN_PX && W * H <= GPT_MAX_PX;
  }

  function withinLimits(W, H) {
    return Math.max(W, H) <= GPT_MAX_EDGE && W * H <= GPT_MAX_PX;
  }

  let grid = null;
  function validGrid() {
    if (!grid) {
      grid = [];
      for (let W = GPT_MIN_EDGE; W <= GPT_MAX_EDGE; W += 16) {
        for (let H = GPT_MIN_EDGE; H <= GPT_MAX_EDGE; H += 16) {
          if (Math.max(W, H) <= 3 * Math.min(W, H) && W * H >= GPT_MIN_PX && W * H <= GPT_MAX_PX) grid.push([W, H]);
        }
      }
    }
    return grid;
  }

  function rint12(x) {
    // numpy round(x, 12) 의 비교용 정수: rint(x × 1e12), 반은 짝수로
    const y = x * 1e12;
    const f = Math.floor(y);
    const d = y - f;
    return d > 0.5 || (d === 0.5 && f % 2 !== 0) ? f + 1 : f;
  }

  function nearest(w, h, target) {
    // 제약을 만족하는 16 배수 크기 중 |log 종횡 오차| → |log 면적 비| → 작은 W → 작은 H
    let best = null;
    for (const [W, H] of validGrid()) {
      const ae = rint12(Math.abs(Math.log((W / H) / (w / h))));
      const ar = rint12(Math.abs(Math.log((W * H) / Math.max(target, 1))));
      if (!best || ae < best[0] || (ae === best[0] && ar < best[1])) best = [ae, ar, W, H]; // 같으면 먼저 본 것(작은 W·H)
    }
    return [best[2], best[3]];
  }

  function gptSize(w, h) {
    // → {size: [W, H], k(소수 6자리), method}. k = kn / kd(분수 그대로 — store 의 Fraction)
    let W = 0;
    let H = 0;
    let method = "";
    let kn;
    let kd;
    if (w === h) {
      W = H = 2048;
      [kn, kd] = [2048, w];
      method = "square_preset";
    } else {
      const m = Math.min(w, h);
      [kn, kd] = m < 512 ? [1024, m] : [2, 1];
      if ((w * kn) % kd === 0 && (h * kn) % kd === 0 && gptOk(w * kn / kd, h * kn / kd)) {
        [W, H, method] = [w * kn / kd, h * kn / kd, "k"];
      } else {
        let kp = Math.ceil(kn / kd);
        while (kp >= 1 && !withinLimits(w * kp, h * kp)) kp -= 1;
        if (kp >= 1 && gptOk(w * kp, h * kp)) [W, H, method, kn, kd] = [w * kp, h * kp, "int_k", kp, 1];
      }
    }
    if (!method) {
      // 폴백: 목표 배율을 상한 안으로 줄여 16 배수 내림 → 그래도 제약 밖이면 최근접 유효 크기
      const s = Math.min(kn / kd, GPT_MAX_EDGE / Math.max(w, h), Math.sqrt(GPT_MAX_PX / (w * h)));
      [W, H] = [Math.floor(w * s / 16 + 1e-9) * 16, Math.floor(h * s / 16 + 1e-9) * 16];
      method = "floor16";
      if (!(W >= 16 && H >= 16 && gptOk(W, H))) {
        [W, H] = nearest(w, h, (w * s) * (h * s));
        method = "nearest";
      }
    }
    return { size: [W, H], k: Math.floor(kn * 1e6 / kd + 0.5) / 1e6, method };
  }

  // ── 이름·대상 문구 규칙(store.check_crop_name · check_target_text) ──
  function asciiDigits(s) {
    return s.replace(/[\uFF10-\uFF19]/g, (c) => String.fromCharCode(c.charCodeAt(0) - 0xFF10 + 0x30));
  }

  function splitNnn(base) {
    // store._split_nnn: 앞자리 숫자(전각 → ASCII)와 나머지(앞의 공백·_·-·. 를 뗌)
    const m = NNN_RE.exec(base);
    return m ? [asciiDigits(m[1]), strip(m[2])] : ["", base];
  }

  function nameSide(name) {
    // 이름 끝 토큰('_' 뒤): 왼쪽/left → "left", 오른쪽/right → "right"(보는 사람 기준)
    return SIDES[name.slice(name.lastIndexOf("_") + 1).toLowerCase()] || null;
  }

  function nameProblem(n) {
    const cps = [...n];
    if (cps.length < 5 || cps.length > 64) return "len";
    if (!/^[0-9]{3}_/.test(n)) return "prefix";
    if (cps.slice(4).some((c) => !NAME_CHAR_RE.test(c))) return "char";
    return null;
  }

  function checkName(raw) {
    // → {name, nnn, part, side} | {err: "len"|"prefix"|"char"}. 레이아웃 안 유일성은 fold 로 따로(서버가 최종)
    const n = strip(raw.normalize("NFC"));
    const err = nameProblem(n);
    if (err) return { err };
    const [nnn, part] = splitNnn(n);
    return { name: n, nnn, part, side: nameSide(n) };
  }

  function fold(s) {
    // Python str.casefold 와 같은 동치(이름 중복 판정): 코드 포인트마다 대문자 → 소문자(문맥 규칙 없이 — Σ·ς → σ), ß → ss, ı 는 그대로.
    // 전 코드 포인트 대조(node 22 = Unicode 16 · Python 3.12 = Unicode 15): 다른 것은 체로키(Python 은 대문자 쪽으로 모음 — 같은 동치)와
    // Python 3.12 가 모르는 Unicode 16 글자(U+1C89·A7CB·A7CC·A7DA·A7DC·10D50–10D65)뿐
    return [...s.normalize("NFC")].map((c) => (c === "\u0131" ? c : c.toUpperCase().toLowerCase())).join("").replace(/\u00DF/g, "ss");
  }

  function textProblem(t) {
    if (TEXT_BAD_RE.test(t)) return "char";
    if ([...t].length > TEXT_MAX) return "len";
    if (TEXT_SPLIT_RE.test(t)) return "roundtrip";
    return null;
  }

  function checkText(raw) {
    // → {text} | {err: "char"|"len"|"roundtrip"}. 빈 문구 허용
    const t = strip(raw.normalize("NFC"));
    const err = textProblem(t);
    return err ? { err } : { text: t };
  }

  // ── G7 검사(store.layout_geom_checks 와 같은 (crop_id, code) 집합) ──
  function geomChecks(lay) {
    const [W, H] = lay.canvas;
    const crops = lay.crops;
    const keys = crops.map((c) => fold(c.name));
    const nnns = crops.map((c) => splitNnn(c.name)[0]);
    const out = {};
    crops.forEach((c, i) => {
      const rows = [];
      const [x0, y0, x1, y1] = c.rect;
      const w = x1 - x0;
      const h = y1 - y0;
      const long = Math.max(w, h);
      const short = Math.min(w, h);
      if (long > RATIO * short) rows.push({ code: "L-ratio", level: "error", msg: `비 ${(long / short).toFixed(2)}:1 — 3:1 이하로 줄이세요` });
      if (x0 < 0 || y0 < 0 || x1 > W || y1 > H) rows.push({ code: "L-outside", level: "warn", msg: `캔버스 ${W}×${H} 밖으로 나감` });
      if (w % SIZE || h % SIZE) rows.push({ code: "L-128", level: "info", msg: `크기 ${w}×${h} 가 128 배수가 아님` });
      if (short < SMALL) rows.push({ code: "L-small", level: "info", msg: `짧은 변 ${short} < ${SMALL}` });
      const [gw, gh] = gptSize(w, h).size;
      if (gw < 2 * w || gh < 2 * h) {
        rows.push({ code: "L-gptk", level: "info", msg: `GPT 배율 ${Math.min(gw / w, gh / h).toFixed(2)} < 2 (${gw}×${gh})` });
      }
      crops.forEach((o, j) => {
        if (j === i) return;
        if (o.rect.every((v, k) => v === c.rect[k])) {
          rows.push({ code: "L-same", level: "warn", msg: `${o.name} 와 사각형이 같음`, other: o.id });
          return;
        }
        const ow = Math.min(x1, o.rect[2]) - Math.max(x0, o.rect[0]);
        const oh = Math.min(y1, o.rect[3]) - Math.max(y0, o.rect[1]);
        if (ow > 0 && oh > 0) rows.push({ code: "L-overlap", level: "info", msg: `겹침: ${o.name} (${ow}×${oh})`, other: o.id });
      });
      const problem = nameProblem(c.name);
      if (problem) rows.push({ code: "N-name", level: "error", msg: NAME_MSGS[problem] });
      crops.forEach((o, j) => {
        if (j !== i && keys[j] === keys[i]) rows.push({ code: "N-dup", level: "error", msg: `이름 중복: ${o.id}`, other: o.id });
      });
      const sameNnn = nnns.filter((n, j) => j !== i && n && n === nnns[i]).length;
      if (sameNnn) rows.push({ code: "N-nnn", level: "info", msg: `같은 번호 ${sameNnn + 1}개 — 레퍼런스는 부위 이름으로 고름` });
      const side = nameSide(c.name);
      if (side === "left" && 3 * (x0 + x1) > 4 * W) {
        rows.push({ code: "N-side", level: "info", msg: "이름은 왼쪽인데 크롭이 화면 오른쪽 1/3 에 있음(보는 사람 기준)" });
      } else if (side === "right" && 3 * (x0 + x1) < 2 * W) {
        rows.push({ code: "N-side", level: "info", msg: "이름은 오른쪽인데 크롭이 화면 왼쪽 1/3 에 있음(보는 사람 기준)" });
      }
      for (const t of c.targets) {
        if (!t.box) continue;
        const [bx0, by0, bx1, by1] = t.box;
        if (bx0 < x0 || by0 < y0 || bx1 > x1 || by1 > y1) {
          rows.push({ code: "P-out", level: "warn", msg: `${t.tid} 상자가 크롭 밖으로 나감`, tid: t.tid });
          continue;
        }
        // 크롭 변이 캔버스 끝인 쪽은 여백을 만들 수 없음 — 경고에서 빼고, 거기만 좁으면 정보 P-canvas(store 와 같음)
        const sides = [[bx0 - x0, w, x0 <= 0], [x1 - bx1, w, x1 >= W], [by0 - y0, h, y0 <= 0], [y1 - by1, h, y1 >= H]];
        const margins = sides.filter(([, , edge]) => !edge);
        if (margins.some(([m, s]) => m < 8 || 50 * m < s)) {
          rows.push({ code: "P-edge", level: "warn", msg: `${t.tid} 부품이 경계에 닿음`, tid: t.tid });
        } else if (margins.some(([m, s]) => 20 * m < 3 * s)) {
          rows.push({ code: "P-70", level: "warn", msg: `${t.tid} 상자가 70% 가이드 밖 — 크롭을 키우세요(여백 15%)`, tid: t.tid });
        } else if (sides.some(([m, s, edge]) => edge && 20 * m < 3 * s)) {
          rows.push({ code: "P-canvas", level: "info", msg: `${t.tid} 부품이 그림 끝에 닿음 — 크롭으로 피할 수 없음`, tid: t.tid });
        }
      }
      out[c.id] = rows;
    });
    return out;
  }

  return { SIZE, POS, RATIO, NAME_MSGS, TEXT_MSGS, rnd, move, resize, grow, newRect, suggest, rectProblem, gptSize,
    splitNnn, nameSide, checkName, checkText, fold, geomChecks };
})();
