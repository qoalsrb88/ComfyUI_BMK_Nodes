// 퀵 칩(크롭·시점·시선·포즈)에 붙이는 작은 그림. 모양은 선·원·경로 데이터로 두고 drawGlyph 하나가 SVG 로 그린다.
// 색은 currentColor 라 칩의 글자색(눌림·흐림)을 그대로 따른다. import 만으로는 아무 일도 하지 않는다.
//
// 모양 하나: {box: [w, h], w: 선 굵기, parts: [...], clip?: {y: [위, 아래], parts}}
//   part: {l: [x, y, x, y, ...]} 꺾은선 · {c: [cx, cy, r]} 원 · {d: "..."} 경로 · {r: [x, y, w, h]} 사각형
//   옵션: fill(채움), faint(흐리게), w(선 굵기)

const SVG_NS = "http://www.w3.org/2000/svg";
const FAINT = "0.35";

// ─── 크롭: 정면 인형 위에 틀. 틀 안만 진하게 ─────────────────────

const FIGURE = [
  { c: [8, 3.2, 2.1], fill: true },
  { l: [8, 5.6, 8, 11.6] },
  { l: [5, 6.4, 11, 6.4] },
  { l: [5, 6.4, 4.3, 11] },
  { l: [11, 6.4, 11.7, 11] },
  { l: [8, 11.6, 6.6, 15.4, 6.4, 19] },
  { l: [8, 11.6, 9.4, 15.4, 9.6, 19] },
];

// 틀의 위·아래(그림 높이 20 기준). 머리 0~5.3, 어깨 6.4, 허리 11.6, 무릎 15.4, 발 19
const CROPS = {
  "portrait": [0.4, 8.8],
  "close-up": [0.4, 6],
  "upper body": [0.4, 12.2],
  "cowboy shot": [0.4, 15.8],
  "full body": [0.4, 19.6],
  "lower body": [11.2, 19.6],
  "feet out of frame": [0.4, 17.2],
  "head out of frame": [5.6, 19.6],
};

const framing = (top, bottom) => ({
  box: [16, 20], w: 1.5,
  parts: [...FIGURE.map((p) => ({ ...p, faint: true })), { r: [1, top, 14, bottom - top], w: 0.9 }],
  clip: { y: [top, bottom], parts: FIGURE },
});

// ─── 시점: 위에서 본 방향(아래가 앞) · 옆에서 본 높이 · 나머지 ───────

const orbit = (angle) => {
  const t = (angle * Math.PI) / 180;
  const at = (r) => [10 + r * Math.sin(t), 10 + r * Math.cos(t)].map((v) => Math.round(v * 100) / 100);
  return {
    box: [20, 20], w: 1.4,
    parts: [
      { c: [10, 10, 8.2], faint: true, w: 1 },
      { c: [10, 10, 3.2] },
      { d: "M8.4 12.6 L10 14.8 L11.6 12.6" },
      { c: [...at(8.2), 2], fill: true },
    ],
  };
};

const SIDE_FIGURE = [{ c: [14, 5, 1.9], fill: true }, { l: [14, 7, 14, 13] }, { l: [14, 13, 12.8, 18.6] }, { l: [14, 13, 15.2, 18.6] }];
const height = (camera, target) => ({
  box: [20, 20], w: 1.4,
  parts: [...SIDE_FIGURE, { c: [...camera, 1.9], fill: true }, { l: [...camera, ...target], faint: true, w: 1.1 }],
});

const VIEWS = {
  "front view": orbit(0),
  "three quarter view": orbit(40),
  "from side": orbit(90),
  "from behind": orbit(180),
  "straight-on": height([3.4, 8], [11.4, 8]),
  "from above": height([3.4, 2.4], [11.6, 7.6]),
  "from below": height([3.4, 17.6], [11.6, 10.4]),
  "profile": { box: [20, 20], w: 1.4, parts: [
    { c: [9.4, 10, 6.2] }, { d: "M15.2 8.4 L17.6 11 L15.4 11.8" }, { c: [12.8, 8.6, 1], fill: true },
  ] },
  "pov": { box: [20, 20], w: 1.4, parts: [
    { d: "M2.4 10 Q10 3.4 17.6 10 Q10 16.6 2.4 10 Z" }, { c: [10, 10, 2.4], fill: true },
  ] },
  "dutch angle": { box: [20, 20], w: 1.4, parts: [
    { l: [5.02, 3.49, 17.57, 6.85, 14.98, 16.51, 2.43, 13.15, 5.02, 3.49] }, { l: [1.5, 10, 18.5, 10], faint: true, w: 1 },
  ] },
  "multiple views": { box: [20, 20], w: 1.3, parts: [
    { r: [1.2, 4, 7.6, 12] }, { r: [11.2, 4, 7.6, 12] },
    { c: [5, 7.4, 1.4], fill: true }, { l: [5, 9, 5, 13.6] },
    { c: [15, 7.4, 1.4] }, { l: [15, 9, 15, 13.6] },
  ] },
};

// ─── 시선: looking 은 뜬 눈(눈동자 방향), facing 은 감은 눈(얼굴 방향) ───

const HEAD = { c: [10, 10, 7.5] };
const BACK_LEFT = { d: "M10 2.5 A7.5 7.5 0 0 0 10 17.5 A4.2 7.5 0 0 1 10 2.5 Z", fill: true, faint: true };    // 고개를 오른쪽으로 돌린 뒤통수
const BACK_TOP = { d: "M2.5 10 A7.5 7.5 0 0 1 17.5 10 A7.5 4 0 0 0 2.5 10 Z", fill: true, faint: true };       // 숙인 머리 정수리
const BACK_FULL = { c: [10, 10, 7.5], fill: true, faint: true };
const round = (v) => Math.round(v * 100) / 100;

// 눈 두 개의 자리(얼굴 가운데 cx, 눈높이 cy)
const eyes = (cx, cy) => [[cx - 2.7, cy], [cx + 2.7, cy]];
const openEyes = (cx, cy, dx = 0, dy = 0, r = 1.3) =>
  eyes(cx, cy).map(([x, y]) => ({ c: [round(x + dx * 1.6), round(y + dy * 1.6), r], fill: true }));
const closedEyes = (cx, cy) => eyes(cx, cy).map(([x, y]) => ({ d: `M${round(x - 1.5)} ${y} Q${x} ${round(y + 1.4)} ${round(x + 1.5)} ${y}`, w: 1.2 }));
const mouth = (cx, cy) => ({ l: [round(cx - 1.4), cy, round(cx + 1.4), cy], w: 1.1 });
const face = (parts) => ({ box: [20, 20], w: 1.3, parts: [HEAD, ...parts] });

const GAZES = {
  "looking at viewer": face([...openEyes(10, 9.6, 0, 0, 1.5), mouth(10, 13.6)]),
  "looking to the side": face([...openEyes(10, 9.6, 1, 0), mouth(10, 13.6)]),
  "sideways glance": face([...openEyes(10, 9.8, 1.1, 0.1), { l: [5.9, 8.6, 8.9, 8.6], w: 1.1 }, { l: [11.3, 8.6, 14.3, 8.6], w: 1.1 }, mouth(10, 13.6)]),
  "looking back": face([{ d: "M10 2.5 A7.5 7.5 0 0 0 10 17.5 A2 7.5 0 0 0 10 2.5 Z", fill: true, faint: true }, { c: [14.6, 9.4, 1.3], fill: true }]),
  "looking ahead": face([BACK_LEFT, ...openEyes(12, 9.6, 0.8, 0), mouth(12.4, 13.6)]),
  "looking up": face([...openEyes(10, 9.6, 0, -1), mouth(10, 13.6)]),
  "looking down": face([...openEyes(10, 9.6, 0, 1), mouth(10, 13.6)]),
  "looking afar": face([...openEyes(10, 9.6, 0.7, -0.7, 0.9), mouth(10, 13.6)]),
  "facing viewer": face([...closedEyes(10, 9.6), mouth(10, 13.6)]),
  "facing to the side": face([BACK_LEFT, ...closedEyes(13, 9.6), mouth(13.6, 13.6)]),
  "facing back": face([{ d: "M10 2.5 A7.5 7.5 0 0 0 10 17.5 A2 7.5 0 0 0 10 2.5 Z", fill: true, faint: true }, { d: "M13.2 9.4 Q14.6 10.8 16 9.4", w: 1.2 }]),
  "facing ahead": face([BACK_LEFT, ...closedEyes(12, 9.6), mouth(12.4, 13.6)]),
  "facing up": face([...closedEyes(10, 7.4), mouth(10, 11.2)]),
  "facing down": face([BACK_TOP, ...closedEyes(10, 11.8), mouth(10, 15)]),
  "facing away": face([BACK_FULL]),
};

// ─── 포즈: 옆에서 본 목각 인형(28 칸, 바닥 26.8) ────────────────

const pose = (head, lines, extra = []) => ({
  box: [28, 28], w: 2.3,
  parts: [{ l: [2, 26.8, 26, 26.8], faint: true, w: 0.8 }, ...extra, ...lines.map((l) => ({ l })), { c: [...head, 2.8], fill: true }],
});

const POSES = {
  "standing": pose([14, 4.8], [[14, 7.8, 14, 15.6], [14, 15.6, 13, 20.8, 13, 25.6], [14, 15.6, 15, 20.8, 15, 25.6], [14, 9.6, 12, 13.6, 12, 17], [14, 9.6, 16, 13.6, 16, 17]]),
  "standing on one leg": pose([14, 4.8], [[14, 7.8, 14, 15.6], [14, 15.6, 14, 20.8, 14, 25.6], [14, 15.6, 18, 18, 16.8, 22.4], [14, 9.6, 10.5, 11.5, 8.5, 9.5], [14, 9.6, 17.5, 11.5, 19.5, 9.5]]),
  "contrapposto": pose([12.8, 4.8], [[13, 7.8, 14.6, 15.6], [14.6, 15.6, 15, 20.8, 15, 25.6], [14.6, 15.6, 12.6, 20.8, 11.4, 25.4], [13.2, 9.6, 11, 13.4, 11.2, 16.8], [13.2, 9.6, 17.4, 12, 15.2, 15.2]]),
  "sitting": pose([9.4, 6.4], [[9.6, 9.4, 10.4, 17.6], [10.4, 17.6, 18.4, 17.6, 18.4, 25.6], [9.8, 11, 12.6, 15, 15.6, 16.6]], [{ l: [5, 19.4, 17, 19.4], faint: true, w: 1.2 }]),
  "wariza": pose([14, 7.4], [[14, 10.4, 14, 18.4], [14, 18.4, 11, 24.6, 5.4, 22.6], [14, 18.4, 17, 24.6, 22.6, 22.6], [14, 12, 10.6, 16.4, 10.8, 20.4], [14, 12, 17.4, 16.4, 17.2, 20.4]]),
  "seiza": pose([11, 7], [[11, 10, 11, 20], [11, 20, 19, 22.8], [19, 24.4, 9.6, 25.4], [11, 11.6, 14.4, 16, 17, 19.6]]),
  "lying": pose([5, 22.6], [[8, 23.6, 16, 23.6], [16, 23.6, 21, 23.6, 26, 23.6], [9, 23.6, 13, 24.6, 16, 24.8]]),
  "on back": pose([5, 22.6], [[8, 23.6, 15.6, 23.6], [15.6, 23.6, 20.6, 24.2, 25.8, 24.2], [15.6, 23.6, 19.6, 18.4, 23.2, 24], [9, 23.4, 11.4, 19.4, 10.6, 16]]),
  "on side": pose([5.2, 18.4], [[8, 20.6, 16, 23.6], [16, 23.6, 21, 22.8, 25.8, 24.4], [16, 23.6, 20.6, 24.6, 24.6, 25.6], [8.2, 21, 6.4, 25, 4.2, 21.2]]),
  "on stomach": pose([6.4, 20.6], [[8.8, 22.8, 16.4, 24], [16.4, 24, 21.4, 24, 24.4, 18.8], [16.4, 24, 21.6, 24.8, 26, 24.8], [9.2, 22.6, 7.4, 25.4, 4, 24.2]]),
  "kneeling": pose([13, 4.4], [[13, 7.4, 13, 15.2], [13, 15.2, 14, 24.2, 21.2, 25.2], [13, 15.2, 12.2, 24.2, 19.4, 25.4], [13, 9.2, 12, 13.2, 12, 16.4]]),
  "on one knee": pose([11, 4.6], [[11, 7.6, 11, 15.4], [11, 15.4, 18, 15.8, 18, 25.4], [11, 15.4, 11.2, 24.4, 4.4, 25.2], [11, 9.4, 15, 12.4, 17.4, 15]]),
  "squatting": pose([12.4, 8.4], [[13, 11.4, 10.6, 19.4], [10.6, 19.4, 17, 16.8, 16.4, 25.4], [10.6, 19.4, 16, 18.4, 14.6, 25.4], [12.6, 12.6, 16, 15.6, 18.4, 16.4]]),
  "all fours": pose([22.2, 10.6], [[19.6, 13, 8.2, 13.8], [19.6, 13, 19.6, 25.4], [19, 13.4, 20.8, 25.4], [8.2, 13.8, 8.4, 24.6, 2.4, 25.4], [8.2, 13.8, 9.8, 24.6, 3.8, 25.6]]),
  "leaning forward": pose([19, 6], [[17.4, 8.8, 13, 15.6], [13, 15.6, 13, 20.8, 13, 25.6], [13, 15.6, 15, 20.8, 15, 25.6], [17, 9.8, 19, 13.6, 18.4, 17]]),
  "bent over": pose([23.2, 15.4], [[20.6, 14, 11, 15.4], [11, 15.4, 11, 20.8, 11, 25.6], [11, 15.4, 13, 20.8, 13, 25.6], [19.6, 14.4, 20.2, 19, 20.4, 22.4]]),
  "leaning back": pose([9.6, 5], [[10.8, 8, 14, 15.6], [14, 15.6, 14, 20.8, 14, 25.6], [14, 15.6, 16, 20.8, 16, 25.6], [11, 9.8, 8.6, 13, 8.4, 16.4]]),
};

const SPECS = {
  framing: Object.fromEntries(Object.entries(CROPS).map(([tag, [top, bottom]]) => [tag, framing(top, bottom)])),
  view: VIEWS,
  gaze: GAZES,
  pose: POSES,
};

/** 퀵 그룹(framing·view·gaze·pose)의 태그 그림 데이터. 없으면 null. */
export function glyphSpec(group, tag) {
  return SPECS[group]?.[tag] ?? null;
}

let clipSeq = 0;

function drawPart(doc, part, defaultWidth) {
  let node;
  if (part.l) {
    node = doc.createElementNS(SVG_NS, "polyline");
    node.setAttribute("points", part.l.join(" "));
  } else if (part.c) {
    node = doc.createElementNS(SVG_NS, "circle");
    const [cx, cy, r] = part.c;
    node.setAttribute("cx", String(cx));
    node.setAttribute("cy", String(cy));
    node.setAttribute("r", String(r));
  } else if (part.d) {
    node = doc.createElementNS(SVG_NS, "path");
    node.setAttribute("d", part.d);
  } else {
    node = doc.createElementNS(SVG_NS, "rect");
    const [x, y, w, h] = part.r;
    node.setAttribute("x", String(x));
    node.setAttribute("y", String(y));
    node.setAttribute("width", String(w));
    node.setAttribute("height", String(h));
    node.setAttribute("rx", "1");
  }
  if (part.fill) {
    node.setAttribute("fill", "currentColor");
    node.setAttribute("stroke", "none");
  }
  if (part.w && part.w !== defaultWidth) node.setAttribute("stroke-width", String(part.w));
  if (part.faint) node.setAttribute("opacity", FAINT);
  return node;
}

/** 그림 데이터 → <svg>(높이 size px, 글자색을 따름, 보조 기술에는 숨김). */
export function drawGlyph(spec, { size = 16, doc = globalThis.document } = {}) {
  const [w, h] = spec.box;
  const svg = doc.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
  svg.setAttribute("width", String(Math.round((size * w * 10) / h) / 10));
  svg.setAttribute("height", String(size));
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  svg.setAttribute("class", "glyph");
  const group = doc.createElementNS(SVG_NS, "g");
  for (const [name, value] of [["fill", "none"], ["stroke", "currentColor"], ["stroke-width", String(spec.w)],
    ["stroke-linecap", "round"], ["stroke-linejoin", "round"]]) group.setAttribute(name, value);
  for (const part of spec.parts) group.append(drawPart(doc, part, spec.w));
  if (spec.clip) {
    const id = `tagorg-glyph-${++clipSeq}`;
    const clip = doc.createElementNS(SVG_NS, "clipPath");
    clip.setAttribute("id", id);
    const [top, bottom] = spec.clip.y;
    clip.append(drawPart(doc, { r: [0, top, w, bottom - top] }, spec.w));
    const defs = doc.createElementNS(SVG_NS, "defs");
    defs.append(clip);
    svg.append(defs);
    const inside = doc.createElementNS(SVG_NS, "g");
    inside.setAttribute("clip-path", `url(#${id})`);
    for (const part of spec.clip.parts) inside.append(drawPart(doc, part, spec.w));
    group.append(inside);
  }
  svg.append(group);
  return svg;
}

/** 태그 그림 <svg>. 그림이 없는 태그는 null. */
export function glyph(group, tag, options = {}) {
  const spec = glyphSpec(group, tag);
  return spec ? drawGlyph(spec, options) : null;
}
