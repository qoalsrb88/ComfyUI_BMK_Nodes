// 퀵 칩(크롭·시점·시선·포즈)에 붙이는 작은 그림. 모양 데이터는 glyphs.json(관리 툴도 같은 파일을 읽는다)에 두고
// drawGlyph 하나가 SVG 로 그린다. 색은 currentColor 라 칩의 글자색(눌림·흐림)을 그대로 따른다.
// import 만으로는 아무 일도 하지 않는다.
//
// 모양 하나: {box: [w, h], w: 선 굵기, parts: [...], clip?: {y: [위, 아래], parts}}
//   part: {l: [x, y, x, y, ...]} 꺾은선 · {c: [cx, cy, r]} 원 · {d: "..."} 경로 · {r: [x, y, w, h]} 사각형
//   옵션: fill(채움), faint(흐리게), w(선 굵기)

import SPECS from "./glyphs.json" with { type: "json" };

const SVG_NS = "http://www.w3.org/2000/svg";
const FAINT = "0.35";
const GROUPS = new Set(["framing", "view", "gaze", "pose"]);

/** 퀵 그룹(framing·view·gaze·pose)의 태그 그림 데이터. 없으면 null. */
export function glyphSpec(group, tag) {
  const specs = GROUPS.has(group) ? SPECS[group] : null;
  return specs && Object.hasOwn(specs, tag) ? specs[tag] : null;
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
