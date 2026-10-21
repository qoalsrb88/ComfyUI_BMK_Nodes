// BMK Design Patch Studio — 캔버스 뷰어(전체 캔버스 이동·확대, 크롭 사각형, 선택 크롭 원해상도 겹침, 이음매).
// board.js 가 DPViewer.create(host, opts) 로 만들고 상태·선택을 넘긴다. 좌표: 캔버스 px, 화면 = 캔버스 × z + (tx, ty).
// 모든 문자열은 textContent 로만 넣는다. 서버 URL 은 opts.apiUrl(상대 경로) 로만 연다.
// 편집기 훅(S1, layout_edit.js): setEditor(ed) — ed.pointerdown/move/up(ev) 이 왼쪽 버튼 입력을 먼저 받고 true 면 소비,
// ed.draw(svg, z) 가 사각형·이름을 그린다(뷰어는 크롭 사각형·이름을 그리지 않음). 화면 이동 = Space 를 누른 채 끌기·가운데 버튼 끌기.
// 시트 원천 훅(S2, ref_edit.js — 두 번째 인스턴스): setSheet(sh) — 바탕 = 시트 미리보기를 시트 원크기로 늘여 놓음(크롭·이음매·★ 합성 없음),
// 확대가 미리보기 배율의 1.5배를 넘으면 멈춘 뒤 보이는 영역 + 25% 를 원해상도로 받아(sh.region) 겹친다.
// 마스크 겹침 훅(S4, mask_edit.js): setMaskLayer({canvas, box}) — 영역 box 에 3층 실시간 합성 캔버스(선택 크롭 겹침 대신), 붓은 setEditor 그대로.
// showSeam(seg) = 그 이음매로 2:1(N 의 자리·강조도 — 편집기의 선택 구간), curSeam() = N 이 가리키는 이음매, N 은 onPick 에 그 이음매 구간도 넘긴다(편집 대상·선택 구간).
"use strict";

window.DPViewer = (() => {
  const SVGNS = "http://www.w3.org/2000/svg";
  const ZMIN = 0.02;
  const ZMAX = 16;
  const CLICK_PX = 4;
  const SHEET_PREVIEW = 2048; // refs.SHEET_PREVIEW — 시트 미리보기 긴 변
  const HI_MAX = 2048; // 원해상도 창 긴 변 상한(/refs/sheet?kind=region max)
  const HI_AREA = 8192 * 8192; // 원해상도 창 넓이 상한(refs.MAX_RECT_AREA)
  const HI_MS = 250;

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = String(text);
    return e;
  }

  function svg(tag, attrs) {
    const e = document.createElementNS(SVGNS, tag);
    for (const [k, v] of Object.entries(attrs || {})) e.setAttribute(k, String(v));
    return e;
  }

  function create(host, opts) {
    const apiUrl = opts.apiUrl;
    const bar = el("div", "vbar");
    const btn = (label, title, fn) => {
      const b = el("button", "", label);
      b.type = "button";
      b.tabIndex = -1;
      b.title = title;
      b.addEventListener("click", (e) => {
        e.currentTarget.blur();
        fn();
      });
      bar.append(b);
      return b;
    };
    const bBg = btn("★ 합성", "바탕: 베이스 / 현재 ★ 합성 (B)", () => setBg(bg === "base" ? "composite" : "base"));
    const bRects = btn("사각형", "크롭 사각형·이름 (O)", () => toggleRects());
    btn("전체", "캔버스 전체 맞춤 (0)", () => fitCanvas());
    btn("크롭", "선택 크롭 맞춤 (F)", () => fitCrop());
    btn("1:1", "원해상도 — 캔버스 1px = 화면 1px (1)", () => zoomTo(1));
    btn("2:1", "원해상도 2배 (2)", () => zoomTo(2));
    const bSeam = btn("이음매 -", "★ 합성에서 마스크가 크롭 경계에 닿아 잘린 곳 — 클릭: 다음 이음매로 (N)", () => nextSeam());
    const zoomLabel = el("span", "vzoom");
    const busy = el("span", "vbusy", "후보 불러오는 중");
    busy.hidden = true; // 선택 크롭 영역(ov)을 새로 받는 동안(F29)
    const hint = el("span", "vhint");
    const msg = el("span", "vmsg");
    bar.append(zoomLabel, busy, hint, msg);

    const port = el("div", "vport");
    const stage = el("div", "stage");
    const imgBg = el("img", "bg");
    imgBg.alt = "";
    imgBg.draggable = false;
    const imgHi = el("img", "hi"); // 시트 원해상도 창(setSheet)
    imgHi.alt = "";
    imgHi.draggable = false;
    imgHi.hidden = true;
    // 선택 크롭 겹침: ov = 크롭 주변 영역(원해상도 ★ 합성), 그 안의 ovCrop = 크롭 rect(Before·후보)
    const ov = el("div", "ov");
    const ovComp = el("img", "ov-comp");
    const ovCrop = el("div", "ov-crop");
    const ovBefore = el("img", "ov-before");
    const ovAfter = el("img", "ov-after");
    for (const i of [ovComp, ovBefore, ovAfter]) {
      i.alt = "";
      i.draggable = false;
    }
    const split = el("div", "ov-split");
    ovCrop.append(ovBefore, ovAfter, split);
    ov.append(ovComp, ovCrop);
    const marks = svg("svg", { class: "marks" });
    stage.append(imgBg, imgHi, ov, marks);
    const labels = el("div", "labels");
    const empty = el("div", "vempty", "불러오는 중…");
    port.append(stage, labels, empty);
    host.replaceChildren(bar, port);

    let canvas = null; // state.canvas
    let crops = []; // state.crops
    let bg = "composite";
    let rectsOn = true;
    let z = 1;
    let tx = 0;
    let ty = 0;
    let fitMode = null; // canvas | crop = 창 크기가 바뀌면 다시 맞춤, manual = 사용자가 확대·이동함
    let sel = null; // {crop, target, cand}
    let ovCropId = null; // 지금 겹침 이미지가 속한 크롭
    let raw = false;
    let qHeld = false;
    let slider = false;
    let splitAt = 0.5;
    let seams = null; // seams 응답
    let seamSig = null;
    let seamIdx = -1;
    let drag = null;
    let editor = null; // 레이아웃·마스크 편집기(setEditor)
    let edit = null; // 편집기가 잡은 pointerId
    let space = false; // Space 를 누르고 있음(편집 모드 화면 이동)
    let sheet = null; // 시트 원천(setSheet) {size, kind, preview_url, region(rect, max), view}
    let hi = null; // 지금 겹친 원해상도 창 {rect, res(시트 px 당 창 px)}
    let hiTimer = null;
    let maskCv = null; // 마스크 편집 3층 합성 캔버스(setMaskLayer)
    const loadTok = new WeakMap();

    // ── 이미지: 다 받은 뒤 바꿔 끼워 깜빡이지 않게 ──
    function loadInto(img, rel, onDone) {
      const u = rel ? apiUrl(rel) : null;
      loadTok.set(img, u); // 먼저 받던 다른 이미지가 늦게 끝나도 바꿔 끼우지 않게(지금 것으로 되돌아온 경우 포함)
      if (!u) {
        img.removeAttribute("src");
        img.hidden = true;
        if (onDone) onDone(false);
        return;
      }
      if (img.getAttribute("src") === u) {
        img.hidden = false;
        if (onDone) onDone(true);
        return;
      }
      const pre = new Image();
      pre.onload = () => {
        if (loadTok.get(img) !== u) return;
        img.src = u;
        img.hidden = false;
        if (onDone) onDone(true);
      };
      pre.onerror = () => {
        if (loadTok.get(img) !== u) return;
        img.hidden = true;
        if (onDone) onDone(false);
      };
      pre.src = u;
    }

    // ── 변환 ──
    function dpr() {
      return window.devicePixelRatio || 1;
    }

    function apply() {
      stage.style.transform = `translate(${tx}px, ${ty}px) scale(${z})`;
      stage.classList.toggle("px", z * dpr() >= 2);
      zoomLabel.textContent = `${Math.round(z * dpr() * 100)}%`;
      drawLabels();
      if (editor) drawMarks(); // 핸들·이름 글자 크기는 화면 px 기준
      if (sheet) {
        clearTimeout(hiTimer);
        hiTimer = setTimeout(loadHi, HI_MS);
      }
    }

    function view() {
      return { w: port.clientWidth, h: port.clientHeight };
    }

    function setView(nz, cx, cy) {
      // 캔버스 점 (cx, cy) 를 뷰포트 가운데로
      const v = view();
      z = Math.min(ZMAX, Math.max(ZMIN, nz));
      tx = v.w / 2 - cx * z;
      ty = v.h / 2 - cy * z;
      apply();
    }

    function fitBox(x0, y0, x1, y1, margin) {
      const v = view();
      if (!v.w || !v.h) return;
      const w = (x1 - x0) * (1 + 2 * margin);
      const h = (y1 - y0) * (1 + 2 * margin);
      setView(Math.min(v.w / w, v.h / h), (x0 + x1) / 2, (y0 + y1) / 2);
    }

    function fitCanvas() {
      if (!canvas) return;
      fitBox(0, 0, canvas.size[0], canvas.size[1], 0.01);
      fitMode = "canvas";
    }

    function selRect() {
      return sel && sel.crop ? sel.crop.rect : null;
    }

    function fitCrop() {
      const r = selRect();
      if (!r) return;
      fitBox(r[0], r[1], r[2], r[3], 0.08);
      fitMode = "crop";
    }

    function focusCenter() {
      const r = selRect();
      if (!r) return null;
      const f = sel.target && sel.target.focus_box;
      if (f) return [r[0] + (f[0] + f[2]) / 2, r[1] + (f[1] + f[3]) / 2];
      return [(r[0] + r[2]) / 2, (r[1] + r[3]) / 2];
    }

    function zoomTo(k) {
      const v = view();
      const c = focusCenter() || [(v.w / 2 - tx) / z, (v.h / 2 - ty) / z];
      setView(k / dpr(), c[0], c[1]);
      fitMode = "manual";
    }

    function toCanvas(ev) {
      const b = port.getBoundingClientRect();
      return [(ev.clientX - b.left - tx) / z, (ev.clientY - b.top - ty) / z];
    }

    // ── 그리기 ──
    function cropClass(c) {
      if (sel && sel.crop && sel.crop.id === c.id) return "sel";
      if (c.problems) return "bad";
      if (c.picked_targets) return "star";
      if (c.pending) return "wait";
      return "";
    }

    function drawMarks() {
      marks.replaceChildren();
      if (!canvas) return;
      const [W, H] = canvas.size;
      marks.setAttribute("viewBox", `0 0 ${W} ${H}`);
      marks.setAttribute("width", W);
      marks.setAttribute("height", H);
      if (rectsOn && !editor) {
        for (const c of crops) {
          const [x0, y0, x1, y1] = c.rect;
          marks.append(svg("rect", { x: x0, y: y0, width: x1 - x0, height: y1 - y0, class: "crop " + cropClass(c) }));
        }
      }
      for (const [i, s] of ((seams && seams.segments) || []).entries()) {
        const cls = `seam ${s.severity}` + (i === seamIdx ? " cur" : "");
        marks.append(svg("line", { x1: s.x0, y1: s.y0, x2: s.x1, y2: s.y1, class: cls }));
      }
      if (editor) editor.draw(marks, z);
    }

    function drawLabels() {
      labels.replaceChildren();
      if (!canvas || !rectsOn || editor) return;
      for (const c of crops) {
        const [x0, y0, x1] = c.rect;
        const sw = (x1 - x0) * z;
        if (sw < 18) continue;
        const d = el("div", "clabel " + cropClass(c), sw < 90 ? c.nnn || c.name : c.name);
        d.style.left = `${x0 * z + tx}px`;
        d.style.top = `${y0 * z + ty}px`;
        d.style.maxWidth = `${Math.max(18, sw)}px`;
        labels.append(d);
      }
    }

    function drawOverlay() {
      // 아래: 크롭 주변을 원해상도로 ★ 합성한 영역 — 후보를 고르면 "이 후보를 ★ 하면"(그 타깃의 ★ 를 이 후보로 바꾼 Compose
      // 결과, 같은 크롭·겹친 크롭의 다른 ★ 포함). 위(크롭 rect): Before(Q·슬라이더 왼쪽), M = 지금 ★ 영역 위에 마스크 없는 원본.
      const crop = sel && sel.crop;
      const t = sel && sel.target;
      const c = sel && sel.cand;
      if (maskCv || !crop || !t || !crop.region) {
        ov.hidden = true;
        busy.hidden = true;
        return;
      }
      if (ovCropId !== crop.id) { // 다른 크롭: 이전 크롭 이미지를 새 자리·크기로 늘여 보이지 않게 비운다
        for (const i of [ovComp, ovBefore, ovAfter]) loadInto(i, null);
        ovCropId = crop.id;
      }
      const [rx0, ry0, rx1, ry1] = crop.region;
      const [x0, y0, x1, y1] = crop.rect;
      ov.hidden = false;
      Object.assign(ov.style, { left: `${rx0}px`, top: `${ry0}px`, width: `${rx1 - rx0}px`, height: `${ry1 - ry0}px` });
      Object.assign(ovCrop.style, { left: `${x0 - rx0}px`, top: `${y0 - ry0}px`, width: `${x1 - x0}px`, height: `${y1 - y0}px` });
      ov.classList.add("loading"); // 후보를 바꾸는 동안 이전 영역을 숨기고 툴바에 로딩 표시(새 영역을 다 받으면 해제)
      busy.hidden = false;
      // 이미 ★·slice 인 후보는 지금 합성 그대로(교체 가정은 그 타깃을 후보 하나로 만들어 다른 slice 를 뺀다)
      loadInto(ovComp, c && !raw && !c.picked ? c.pick_url : crop.region_url, () => {
        ov.classList.remove("loading");
        busy.hidden = true;
      });
      loadInto(ovBefore, t.before_full_url);
      loadInto(ovAfter, c && raw ? c.full_url : null);
      const cut = `${(splitAt * 100).toFixed(2)}%`;
      const split2 = !!c && slider && !qHeld;
      ovBefore.style.visibility = qHeld || split2 ? "" : "hidden";
      ovBefore.style.clipPath = split2 ? `inset(0 calc(100% - ${cut}) 0 0)` : "";
      ovAfter.style.visibility = raw && !qHeld ? "" : "hidden";
      ovAfter.style.clipPath = split2 ? `inset(0 0 0 ${cut})` : "";
      split.hidden = !split2;
      split.style.left = cut;
    }

    function setMsg(text, cls) {
      msg.textContent = text || "";
      msg.className = "vmsg " + (cls || "");
    }

    function drawSeamButton() {
      const n = seams && seams.segments ? seams.segments.length : null;
      const bad = n ? seams.segments.filter((s) => s.severity === "bad").length : 0;
      bSeam.textContent = n === null ? "이음매 …" : `이음매 ${n}` + (bad ? ` (✕${bad})` : "");
      bSeam.classList.toggle("bad", bad > 0);
      bSeam.disabled = !n;
    }

    // ── 합성·이음매 ──
    function loadBg() {
      if (!canvas) return;
      const rel = bg === "composite" ? canvas.composite_url : canvas.base_url;
      if (bg === "composite") setMsg("★ 합성 중…", "busy");
      loadInto(imgBg, rel, (ok) => {
        if (bg !== "composite") return;
        setMsg(ok ? "" : "합성을 불러오지 못했습니다", ok ? "" : "err");
        if (ok) describeSeams();
      });
      bBg.textContent = bg === "composite" ? "★ 합성" : "베이스";
      bBg.classList.toggle("on", bg === "composite");
    }

    async function loadSeams() {
      if (!canvas || seamSig === canvas.composite_sig) return;
      const sig = canvas.composite_sig;
      seamSig = sig;
      seams = null;
      seamIdx = -1;
      drawSeamButton();
      try {
        const r = await fetch(apiUrl(canvas.seams_url), { cache: "no-store" });
        const d = await r.json();
        if (seamSig !== sig) return;
        if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);
        seams = d;
      } catch (e) {
        if (seamSig !== sig) return;
        seams = { segments: [], error: e.message };
        seamSig = null; // 다음 상태 갱신 때 다시 시도
      }
      drawSeamButton();
      drawMarks();
      describeSeams();
      if (opts.onSeams) opts.onSeams(seams);
    }

    function describeSeams() {
      if (!seams || bg !== "composite") return;
      if (seams.error) {
        setMsg(`이음매 검사 실패: ${seams.error}`, "err");
        return;
      }
      const notes = [];
      if (seams.unanalyzed && seams.unanalyzed.length) notes.push(`분석 없는 ★ ${seams.unanalyzed.length}(마스크 없이 합성)`);
      setMsg(notes.join(" · "), notes.length ? "warn" : "");
    }

    function nextSeam() {
      const list = (seams && seams.segments) || [];
      if (!list.length) return;
      const prev = seamIdx;
      seamIdx = (seamIdx + 1) % list.length;
      const s = list[seamIdx];
      // 이음매를 만든 ★ 후보로 — 겹침이 지금 ★ 합성과 같게(마스크 모드 = 편집 대상·선택 구간). false = 마스크 편집기가 거절(적용 안 한 획 확인 취소·
      // 적용 중) — 자리·강조·메시지를 편집 대상 그대로 둠
      if (opts.onPick && opts.onPick(s.crop_id, s.tid, s.key, s) === false) {
        seamIdx = prev;
        return;
      }
      showSeam(s);
    }

    function showSeam(s) {
      // 이음매 s 로 2:1. 지금 목록의 같은 구간이면 N 의 자리·강조·메시지도 그 구간(다음 N 은 그다음) — 마스크 편집기가 고른 구간(패널 이음매 줄·
      // 진행 ★ 점검)도 이리로 와서 편집기의 선택 구간과 뷰어 N 위치가 따로 놀지 않게. 목록에 없으면(GET /mask 의 이음매 — 뷰어가 다시 재는 중) 자리 없음
      const list = (seams && seams.segments) || [];
      seamIdx = list.findIndex((x) => x.key === s.key && x.side === s.side && x.x0 === s.x0 && x.y0 === s.y0 && x.x1 === s.x1 && x.y1 === s.y1);
      setView(2 / dpr(), (s.x0 + s.x1) / 2, (s.y0 + s.y1) / 2);
      fitMode = "manual";
      drawMarks();
      if (seamIdx < 0) describeSeams();
      else {
        setMsg(`이음매 ${seamIdx + 1}/${list.length} · ${s.crop_id} ${s.tid} ${s.side} · ${s.length}px · 색차 p90 ${s.diff_p90}`,
          s.severity === "bad" ? "err" : "warn");
      }
    }

    // ── 시트(S2) ──
    function loadHi() {
      // 확대가 미리보기 배율의 1.5배를 넘으면 보이는 영역 + 25%(변마다 1/8)를 원해상도 창으로(긴 변 ≤ 2048). 지금 창이 보이는 영역을 덮고
      // 해상도도 충분하면 다시 받지 않는다. 낡은 응답은 loadInto 가 버린다(받은 창의 자리는 그 이미지가 들어올 때 옮김)
      if (!sheet) return;
      const [W, H] = sheet.size;
      const px = z * dpr();
      if (px <= 1.5 * Math.min(1, SHEET_PREVIEW / Math.max(W, H))) return;
      const v = view();
      const vis = [Math.max(0, -tx / z), Math.max(0, -ty / z), Math.min(W, (v.w - tx) / z), Math.min(H, (v.h - ty) / z)];
      if (vis[2] <= vis[0] || vis[3] <= vis[1]) return;
      const mx = (vis[2] - vis[0]) / 8;
      const my = (vis[3] - vis[1]) / 8;
      const rect = [Math.max(0, Math.floor(vis[0] - mx)), Math.max(0, Math.floor(vis[1] - my)),
        Math.min(W, Math.ceil(vis[2] + mx)), Math.min(H, Math.ceil(vis[3] + my))];
      const long = Math.max(rect[2] - rect[0], rect[3] - rect[1]);
      if ((rect[2] - rect[0]) * (rect[3] - rect[1]) > HI_AREA) return;
      const max = Math.min(HI_MAX, Math.ceil(long * px));
      const res = Math.min(1, max / long);
      if (hi && hi.rect[0] <= vis[0] && hi.rect[1] <= vis[1] && hi.rect[2] >= vis[2] && hi.rect[3] >= vis[3]
          && hi.res >= 0.9 * Math.min(1, px, HI_MAX / long)) return;
      const sh = sheet;
      loadInto(imgHi, sh.region(rect, max), (ok) => {
        // 같은 시트를 다시 열면(setSheet same) 객체는 새것 — 미리보기로 같은 시트인지 본다(낡은 창 응답은 loadInto 가 이미 버림)
        if (!sheet || sheet.preview_url !== sh.preview_url) return;
        hi = ok ? { rect, res } : null;
        if (ok) Object.assign(imgHi.style, { left: `${rect[0]}px`, top: `${rect[1]}px`, width: `${rect[2] - rect[0]}px`, height: `${rect[3] - rect[1]}px` });
      });
    }

    function setSheet(sh) {
      // S2 레퍼런스 편집기: sh = {size, kind, preview_url, region(rect, max) → 상대 URL, view?: [x0, y0, x1, y1]} | null(닫기).
      // 같은 미리보기면 보기 위치를 그대로 둔다. 크롭 사각형·이음매·★ 합성 단추는 숨긴다
      const same = !!sheet && !!sh && sheet.preview_url === sh.preview_url;
      sheet = sh;
      canvas = sh ? { size: sh.size } : null;
      crops = [];
      seams = null;
      for (const b of [bBg, bRects, bSeam]) b.hidden = true;
      if (!same) { // 같은 시트면 확대·이동 뒤 걸어 둔 원해상도 창 받기(hiTimer)를 그대로 둔다 — 보기 위치가 같아 apply() 가 다시 걸지 않음
        clearTimeout(hiTimer);
        hi = null;
        loadInto(imgHi, null);
      }
      empty.hidden = !!sh;
      stage.hidden = !sh;
      if (!sh) {
        empty.textContent = "시트를 고르세요";
        loadInto(imgBg, null);
        return;
      }
      for (const e of [stage, imgBg]) Object.assign(e.style, { width: `${sh.size[0]}px`, height: `${sh.size[1]}px` });
      if (!same) {
        fitMode = null;
        setMsg(sh.kind === "psd" ? "PSD 합성 중…(첫 열기만 — 캐시)" : "불러오는 중…", "busy");
        loadInto(imgBg, sh.preview_url, (ok) => {
          if (sheet && sheet.preview_url === sh.preview_url) setMsg(ok ? "" : "시트를 불러오지 못했습니다", ok ? "" : "err");
        });
      }
      if (!fitMode && sh.view) {
        fitBox(sh.view[0], sh.view[1], sh.view[2], sh.view[3], 0);
        fitMode = "manual";
      } else if (!fitMode) {
        fitCanvas();
      }
      drawMarks();
      drawOverlay();
    }

    // ── 공개 ──
    function setState(st) {
      const was = canvas;
      canvas = st.canvas || null;
      crops = st.crops || [];
      empty.hidden = !!canvas;
      if (!canvas) {
        empty.textContent = "베이스가 없습니다 — Import PSD 를 먼저 실행하세요";
        stage.hidden = true;
        return;
      }
      stage.hidden = false;
      stage.style.width = `${canvas.size[0]}px`;
      stage.style.height = `${canvas.size[1]}px`;
      imgBg.style.width = `${canvas.size[0]}px`;
      imgBg.style.height = `${canvas.size[1]}px`;
      if (!was || was.composite_url !== canvas.composite_url || was.base_url !== canvas.base_url) loadBg();
      loadSeams();
      if (!fitMode) fitCanvas();
      drawMarks();
      drawLabels();
      drawOverlay();
    }

    function setSelection(next, follow) {
      const prevCrop = sel && sel.crop ? sel.crop.id : null;
      sel = next;
      if (follow && sel && sel.crop && sel.crop.id !== prevCrop) {
        const px = z * dpr();
        if (px >= 0.99) zoomTo(Math.round(px));
        else fitCrop();
      }
      drawMarks();
      drawLabels();
      drawOverlay();
    }

    function setBg(mode) {
      bg = mode;
      loadBg();
      if (bg === "base") setMsg("");
    }

    function toggleRects() {
      rectsOn = !rectsOn;
      bRects.classList.toggle("on", rectsOn);
      drawMarks();
      drawLabels();
    }

    function setFlags(f) {
      if ("raw" in f) raw = f.raw;
      if ("qHeld" in f) qHeld = f.qHeld;
      if ("slider" in f) slider = f.slider;
      hint.textContent = [qHeld && "Before (Q)", slider && "슬라이더 (S) — 마우스 왼쪽 = Before",
        raw && "후보 원본 (M) — 마스크 없음"].filter(Boolean).join(" · ");
      drawOverlay();
    }

    function flags() {
      return { raw, qHeld, slider, bg, rectsOn };
    }

    function setEditor(ed) {
      editor = ed;
      edit = null;
      port.classList.toggle("edit", !!ed);
      port.style.cursor = "";
      drawMarks();
      drawLabels();
    }

    function setCursor(c) {
      if (!space) port.style.cursor = c || "";
    }

    function setMaskLayer(layer) {
      // S4 마스크 편집: layer = {canvas, box(캔버스 px)} — 그 자리에 늘여 둔다(확대하면 px 단위로 보이게). null = 뗌
      if (maskCv && (!layer || layer.canvas !== maskCv)) maskCv.remove();
      maskCv = layer ? layer.canvas : null;
      if (maskCv) {
        const [x0, y0, x1, y1] = layer.box;
        maskCv.className = "ov-mask";
        Object.assign(maskCv.style, { left: `${x0}px`, top: `${y0}px`, width: `${x1 - x0}px`, height: `${y1 - y0}px` });
        if (maskCv.parentNode !== stage) stage.insertBefore(maskCv, marks);
      }
      drawOverlay();
    }

    // ── 입력 ──
    port.addEventListener("wheel", (e) => {
      if (!canvas) return;
      e.preventDefault();
      const b = port.getBoundingClientRect();
      const mx = e.clientX - b.left;
      const my = e.clientY - b.top;
      const nz = Math.min(ZMAX, Math.max(ZMIN, z * Math.exp(-e.deltaY * 0.0015)));
      tx = mx - ((mx - tx) / z) * nz;
      ty = my - ((my - ty) / z) * nz;
      z = nz;
      fitMode = "manual";
      apply();
    }, { passive: false });

    port.addEventListener("pointerdown", (e) => {
      if (!canvas || e.button > 1) return;
      if (e.button === 1) e.preventDefault(); // 가운데 버튼 = 화면 이동(자동 스크롤 막음)
      if (editor && e.button === 0 && !space && editor.pointerdown(e)) {
        edit = e.pointerId;
        port.setPointerCapture(e.pointerId);
        return;
      }
      drag = { x: e.clientX, y: e.clientY, tx, ty, moved: false, id: e.pointerId };
      port.setPointerCapture(e.pointerId);
      port.classList.add("grab");
    });

    port.addEventListener("pointermove", (e) => {
      if (edit === e.pointerId) {
        editor.pointermove(e);
        return;
      }
      if (drag && drag.id === e.pointerId) {
        const dx = e.clientX - drag.x;
        const dy = e.clientY - drag.y;
        if (!drag.moved && Math.hypot(dx, dy) < CLICK_PX) return;
        drag.moved = true;
        tx = drag.tx + dx;
        ty = drag.ty + dy;
        fitMode = "manual";
        apply();
        return;
      }
      if (editor && editor.pointermove(e)) return; // 누르지 않고 움직임(핸들 커서 등)
      const r = selRect();
      if (slider && r) {
        const [cx] = toCanvas(e);
        splitAt = Math.min(1, Math.max(0, (cx - r[0]) / (r[2] - r[0])));
        drawOverlay();
      }
    });

    function endDrag(e) {
      if (edit === e.pointerId) {
        edit = null;
        editor.pointerup(e);
        return;
      }
      if (!drag || drag.id !== e.pointerId) return;
      const click = !drag.moved;
      drag = null;
      port.classList.remove("grab");
      if (!click || !opts.onPick || editor) return;
      const [cx, cy] = toCanvas(e);
      const hits = crops.filter((c) => cx >= c.rect[0] && cx < c.rect[2] && cy >= c.rect[1] && cy < c.rect[3]);
      if (!hits.length) return;
      hits.sort((a, b) => (a.rect[2] - a.rect[0]) * (a.rect[3] - a.rect[1]) - (b.rect[2] - b.rect[0]) * (b.rect[3] - b.rect[1]));
      // 겹친 크롭: 선택한 크롭을 다시 누르면 다음으로 작은 크롭
      const cur = sel && sel.crop ? hits.findIndex((c) => c.id === sel.crop.id) : -1;
      opts.onPick(hits[cur >= 0 ? (cur + 1) % hits.length : 0].id, null);
    }
    port.addEventListener("pointerup", endDrag);
    port.addEventListener("pointercancel", (e) => {
      if (edit === e.pointerId) {
        edit = null;
        editor.cancel();
      }
      if (drag && drag.id === e.pointerId) {
        drag = null;
        port.classList.remove("grab");
      }
    });

    // Space 를 누른 채 끌기 = 화면 이동(편집 모드에서 왼쪽 끌기는 편집). 입력칸에서 친 Space 는 보지 않는다 — 단 data-keys="board"
    // (레퍼런스 패널의 슬라이더·select·체크박스 — 놓은 뒤 포커스가 남음)는 보드 키를 받는 칸이라 화면 이동도 된다(board.js typing 과 같은 판정)
    document.addEventListener("keydown", (e) => {
      if (e.code !== "Space" || space
          || (e.target.closest("input, textarea, select, [contenteditable]") && !e.target.closest('[data-keys="board"]'))) return;
      space = true;
      port.classList.add("space");
      port.style.cursor = "";
    });
    document.addEventListener("keyup", (e) => {
      if (e.code === "Space") {
        space = false;
        port.classList.remove("space");
      }
    });
    window.addEventListener("blur", () => {
      space = false;
      port.classList.remove("space");
    });

    new ResizeObserver(() => {
      if (!canvas) return;
      if (fitMode === "crop") fitCrop();
      else if (fitMode !== "manual") fitCanvas();
      else drawLabels();
    }).observe(port);

    bRects.classList.add("on");
    drawSeamButton();
    return { setState, setSelection, setFlags, flags, setBg, toggleRects, fitCanvas, fitCrop, zoomTo, nextSeam,
      seams: () => seams, setEditor, setCursor, canvasPoint: toCanvas, scale: () => z, redraw: drawMarks, setSheet, setMaskLayer,
      showSeam, curSeam: () => (seamIdx >= 0 ? seams.segments[seamIdx] : null) };
  }

  return { create, svg };
})();
