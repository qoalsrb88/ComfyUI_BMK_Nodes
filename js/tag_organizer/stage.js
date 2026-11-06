// 퀵 세팅의 그림 조작: 크롭 틀(위아래 손잡이)과 방향·높이 다이얼. 칩과 같은 덮어쓰기 값을 다룬다.
// 그림은 고른 값을 갖지 않고 update 로 받은 값을 그린다. 끄는 동안의 각도만 잠깐 들고 있다가, 태그가 바뀔 때만
// onFraming(tag)·onView(tags) 로 알린다(빈 값은 자동). 어떤 태그를 맡는지는 palette 의 framing_frame·view_dial 이 정한다.
// import 만으로는 아무 일도 하지 않는다.

import { BONES, HEAD_R, bodySamples, cameraBasis, cameraFrom, frameAxis, frameBox, gazeFrom, nearestBox, posedJoints, postureCamera,
  projector, resolvePose } from "./figure.js";
import { viewToggle } from "./model.js";

const SVG_NS = "http://www.w3.org/2000/svg";
const AXES = ["direction", "height"];
const round = (v) => Math.round(v * 100) / 100;

// ─── 계산(DOM 없음) ───────────────────────────────────────────────

/** 그림이 맡는 태그: {framing: 틀 범위가 있는 크롭 태그, view: 다이얼 구간의 시점 태그}. 퀵 목록 순서. */
export function stageCovers(palette) {
  const frames = palette.framing_frame ?? {};
  const dialTags = new Set(AXES.flatMap((axis) => (palette.view_dial?.[axis] ?? []).map((zone) => zone.tag)));
  return {
    framing: (palette.quick?.framing ?? []).filter((tag) => frames[tag]),
    view: (palette.quick?.view ?? []).filter((tag) => dialTags.has(tag)),
  };
}

/** 각도가 든 다이얼 구간의 태그. 빈 구간이면 null. 방향은 좌우가 같은 태그라 절댓값으로 본다. */
export function dialZone(zones, angle, mirrored = false) {
  const a = mirrored ? Math.min(Math.abs(angle), 180) : angle;
  return zones.find((zone) => a >= zone.from && a <= zone.to)?.tag ?? null;
}

/**
 * 다이얼로 한 축(axisTags)을 tag 로 바꾼 새 시점 선택. 다른 축과 다이얼 밖 시점은 그대로 둔다.
 * add 면 그 축의 다른 태그를 두고 tag 만 켜거나 끈다(Ctrl·Shift 를 누르고 고르기). 부딪치는 시점은 칩과 같이 꺼진다.
 */
export function viewWithZone(palette, selected, axisTags, tag, { add = false, lifted = false } = {}) {
  if (add) return tag ? viewToggle(palette, selected, tag, lifted) : selected;
  const base = selected.filter((t) => !axisTags.includes(t));
  return tag ? viewToggle(palette, base, tag, lifted) : base;
}

// ─── 그리기 ───────────────────────────────────────────────────────

const svgEl = (doc, name, attrs = {}) => {
  const node = doc.createElementNS(SVG_NS, name);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, String(value));
  return node;
};

const htmlEl = (doc, name, className, text = "") => {
  const node = doc.createElement(name);
  node.className = className;
  node.textContent = text;
  return node;
};

// a: 0 이 아래(피사체 정면), 오른쪽으로 돌수록 커진다
const polar = (cx, cy, r, a) => {
  const t = (a * Math.PI) / 180;
  return [round(cx + r * Math.sin(t)), round(cy + r * Math.cos(t))];
};

function sectorPath(cx, cy, r0, r1, a0, a1) {
  const [x0, y0] = polar(cx, cy, r1, a0);
  const [x1, y1] = polar(cx, cy, r1, a1);
  const [x2, y2] = polar(cx, cy, r0, a1);
  const [x3, y3] = polar(cx, cy, r0, a0);
  const large = a1 - a0 > 180 ? 1 : 0;
  return `M${x0} ${y0}A${r1} ${r1} 0 ${large} 0 ${x1} ${y1}L${x2} ${y2}A${r0} ${r0} 0 ${large} 1 ${x3} ${y3}Z`;
}

// 크롭 칸: 고른 포즈·시선의 인형을 고른 카메라에서 본 모습. 키 1 을 scale px 로, 칸에 맞게 줄인다
const CROP = { w: 100, h: 140, scale: 118, fitW: 90, fitH: 128, pad: 3 };
const pathBox = ([x0, y0, x1, y1]) => `M${round(x0)} ${round(y0)}H${round(x1)}V${round(y1)}H${round(x0)}Z`;
const padBox = ([x0, y0, x1, y1], p) => [Math.max(1, x0 - p), Math.max(1, y0 - p), Math.min(CROP.w - 1, x1 + p), Math.min(CROP.h - 1, y1 + p)];

// 방향(위에서 본 원, 아래가 정면)·높이(옆에서 본 반원, 오른쪽이 카메라)
const DIR = { cx: 42, cy: 42, r0: 12, r1: 30, cam: 37 };
const HEIGHT = { cx: 8, cy: 42, r0: 12, r1: 30, cam: 36 };

export class QuickStage {
  constructor({ palette, onFraming, onView, doc = globalThis.document }) {
    this.doc = doc;
    this.palette = palette;
    this.onFraming = onFraming;
    this.onView = onView;
    this.frames = palette.framing_frame ?? {};
    this.dial = Object.fromEntries(AXES.map((axis) => [axis, [...(palette.view_dial?.[axis] ?? [])].sort((a, b) => a.from - b.from)]));
    this.axisTags = Object.fromEntries(AXES.map((axis) => [axis, this.dial[axis].map((zone) => zone.tag)]));
    this.covers = stageCovers(palette);
    this.state = { framing: "", view: [], pose: [], gaze: [], present: new Set(), lifted: false };
    this.drag = null;          // 끄는 중 {kind, angle}
    this.side = 1;             // 방향 카메라를 놓는 쪽(오른쪽 1·왼쪽 -1). 마지막으로 끈 쪽을 따른다
    this.layout = null;        // 마지막으로 그린 크롭 칸 {axis, boxes, full}(끌기에 쓴다)
    this.root = this.build();
  }

  /**
   * framing: 고른 크롭("" 은 자동), view·pose·gaze: 고른 태그들([] 은 자동), present: 프롬프트에 든 태그,
   * lifted: 시점이 부딪치지 않음. 자동인 값은 프롬프트에 든 태그로 그린다.
   */
  update({ framing = "", view = [], pose = [], gaze = [], present = new Set(), lifted = false } = {}) {
    this.state = { framing, view: [...view], pose: [...pose], gaze: [...gaze], present, lifted };
    this.paint();
  }

  // ─── 값 ─────────────────────────────────────────────────────────

  promptFraming() {
    return this.covers.framing.find((tag) => this.state.present.has(tag)) ?? "";
  }

  promptTags(group) {
    return (this.palette.quick?.[group] ?? []).filter((tag) => this.state.present.has(tag));
  }

  promptView() {
    return this.promptTags("view");
  }

  // 그림에서 바꾸기 시작할 때의 시점: 고른 것이 없으면 프롬프트 값에서 시작한다(다른 축이 사라지지 않게)
  viewBase() {
    return this.state.view.length ? this.state.view : this.promptView();
  }

  emitView(next) {
    const now = this.state.view;
    if (next.length === now.length && next.every((t, i) => t === now[i])) return;
    this.onView?.(next);
  }

  setZone(axis, tag, add = false) {
    this.emitView(viewWithZone(this.palette, this.viewBase(), this.axisTags[axis], tag, { add, lifted: this.state.lifted }));
  }

  setFraming(tag) {
    if (tag !== this.state.framing) this.onFraming?.(tag);
  }

  // ─── 만들기 ─────────────────────────────────────────────────────

  build() {
    const doc = this.doc;
    const root = htmlEl(doc, "div", "qs");
    root.setAttribute("role", "group");
    root.setAttribute("aria-label", "크롭·시점 그림");

    const crop = htmlEl(doc, "div", "qs-col qs-crop");
    const cropHead = htmlEl(doc, "div", "qs-head");
    this.cropAuto = htmlEl(doc, "button", "chip auto qs-auto", "자동");
    this.cropAuto.type = "button";
    this.cropAuto.addEventListener("click", () => this.setFraming(""));
    cropHead.append(htmlEl(doc, "span", "qs-label", "크롭"), this.cropAuto);
    this.cropSvg = svgEl(doc, "svg", { viewBox: `0 0 ${CROP.w} ${CROP.h}`, width: CROP.w, height: CROP.h, class: "qs-svg qs-crop-svg",
      role: "slider", tabindex: 0, "aria-label": "크롭", "aria-valuemin": 0, "aria-valuemax": Math.max(0, this.covers.framing.length - 1) });
    const title = svgEl(doc, "title");
    title.textContent = "틀의 손잡이를 끌어 크롭을 고릅니다. 인형은 고른 포즈·시선을 고른 방향·높이에서 본 모습입니다.";
    // 인형: 뼈·머리는 그릴 때마다 깊이 순서로 다시 늘어놓는다(먼 쪽이 먼저)
    this.figure = svgEl(doc, "g", { class: "qs-figure" });
    this.boneEls = BONES.map(() => svgEl(doc, "line", { class: "qs-bone" }));
    this.neckEl = svgEl(doc, "line", { class: "qs-bone" });
    this.headEl = svgEl(doc, "circle", { class: "qs-head-shape" });
    this.face = svgEl(doc, "g", { class: "qs-face" });
    this.eyeEls = [0, 1].map(() => svgEl(doc, "circle", { class: "qs-eye", r: 1.3 }));
    this.lidEls = [0, 1].map(() => svgEl(doc, "line", { class: "qs-lid" }));
    this.rayEl = svgEl(doc, "line", { class: "qs-ray" });
    this.face.append(...this.eyeEls, ...this.lidEls, this.rayEl);
    this.dim = svgEl(doc, "path", { class: "qs-dim", "fill-rule": "evenodd" });
    this.cropGhost = svgEl(doc, "rect", { class: "qs-ghost", rx: 2 });
    this.cropFrame = svgEl(doc, "rect", { class: "qs-frame", rx: 2 });
    this.handles = ["lo", "hi"].map((edge) => svgEl(doc, "rect", { class: "qs-handle", rx: 3, "data-edge": edge }));
    this.cropSvg.append(title, this.figure, this.dim, this.cropGhost, this.cropFrame, ...this.handles);
    this.cropRead = htmlEl(doc, "div", "qs-read");
    crop.append(cropHead, this.cropSvg, this.cropRead);

    const dials = htmlEl(doc, "div", "qs-col qs-dials");
    const dialHead = htmlEl(doc, "div", "qs-head");
    dialHead.append(htmlEl(doc, "span", "qs-label", "방향 · 높이"));
    dials.append(dialHead);
    const row = htmlEl(doc, "div", "qs-dial-row");
    this.dirSvg = this.buildDirection();
    this.heightSvg = this.buildHeight();
    row.append(this.dirSvg, this.heightSvg);
    this.dirRead = htmlEl(doc, "div", "qs-read");
    this.heightRead = htmlEl(doc, "div", "qs-read");
    dials.append(row, this.dirRead, this.heightRead);

    root.append(crop, dials);
    this.listen();
    return root;
  }

  buildDirection() {
    const doc = this.doc;
    const { cx, cy, r0, r1 } = DIR;
    const svg = svgEl(doc, "svg", { viewBox: "0 0 84 84", width: 84, height: 84, class: "qs-svg qs-dir-svg", role: "slider", tabindex: 0,
      "aria-label": "방향", "aria-valuemin": 0, "aria-valuemax": Math.max(0, this.dial.direction.length - 1) });
    const title = svgEl(doc, "title");
    title.textContent = "끌어서 방향을 고릅니다. 아래가 정면이고 좌우는 같은 태그입니다. Ctrl·Shift 를 누르고 누르면 방향을 더하거나 뺍니다.";
    svg.append(title, svgEl(doc, "circle", { class: "qs-track", cx, cy, r: DIR.cam }));
    this.dirZones = [];
    for (const zone of this.dial.direction) {
      const spans = zone.from === 0 ? [[-zone.to, zone.to]] : zone.to === 180 ? [[zone.from, 360 - zone.from]]
        : [[zone.from, zone.to], [-zone.to, -zone.from]];
      for (const [a0, a1] of spans) {
        const path = svgEl(doc, "path", { class: "qs-zone", d: sectorPath(cx, cy, r0, r1, a0, a1), "data-tag": zone.tag });
        svg.append(path);
        this.dirZones.push(path);
      }
    }
    svg.append(svgEl(doc, "circle", { class: "qs-subject", cx, cy, r: 6 }),
      svgEl(doc, "path", { class: "qs-subject", d: `M${cx - 3} ${cy + 5} L${cx} ${cy + 9} L${cx + 3} ${cy + 5}` }));
    this.dirMarks = this.dial.direction.map(() => {
      const mark = svgEl(doc, "circle", { class: "qs-cam", r: 4.5 });
      svg.append(mark);
      return mark;
    });
    return svg;
  }

  buildHeight() {
    const doc = this.doc;
    const { cx, cy, r0, r1 } = HEIGHT;
    const svg = svgEl(doc, "svg", { viewBox: "0 0 48 84", width: 48, height: 84, class: "qs-svg qs-height-svg", role: "slider", tabindex: 0,
      "aria-label": "높이" });
    const title = svgEl(doc, "title");
    title.textContent = "끌어서 카메라 높이를 고릅니다. 가운데는 수평(태그 없음)입니다.";
    svg.append(title);
    // 다이얼 구간 사이의 빈 곳은 수평
    const zones = [];
    let from = -90;
    for (const zone of this.dial.height) {
      if (zone.from > from) zones.push({ tag: null, from, to: zone.from });
      zones.push(zone);
      from = Math.max(from, zone.to);
    }
    if (from < 90) zones.push({ tag: null, from, to: 90 });
    this.heightSteps = zones;
    this.heightZones = zones.map((zone) => {
      const path = svgEl(doc, "path", { class: "qs-zone", d: sectorPath(cx, cy, r0, r1, 90 + zone.from, 90 + zone.to), "data-tag": zone.tag ?? "" });
      svg.append(path);
      return path;
    });
    svg.setAttribute("aria-valuemin", 0);
    svg.setAttribute("aria-valuemax", Math.max(0, zones.length - 1));
    svg.append(svgEl(doc, "circle", { class: "qs-subject", cx, cy: cy - 7, r: 3 }),
      svgEl(doc, "polyline", { class: "qs-subject", points: `${cx},${cy - 4} ${cx},${cy + 10}` }));
    this.heightMark = svgEl(doc, "circle", { class: "qs-cam", r: 4.5 });
    svg.append(this.heightMark);
    return svg;
  }

  // ─── 조작 ───────────────────────────────────────────────────────

  point(svg, event) {
    const ctm = svg.getScreenCTM();
    if (!ctm) return null;
    const p = svg.createSVGPoint();
    p.x = event.clientX;
    p.y = event.clientY;
    return p.matrixTransform(ctm.inverse());
  }

  listen() {
    const start = (svg, kind) => (event) => {
      if (event.button !== 0) return;
      const p = this.point(svg, event);
      if (!p) return;
      event.preventDefault();
      svg.focus({ preventScroll: true });
      if (kind === "direction" && (event.ctrlKey || event.shiftKey || event.metaKey)) {
        const angle = Math.atan2(p.x - DIR.cx, p.y - DIR.cy) * 180 / Math.PI;
        if (Math.abs(angle) > 1) this.side = Math.sign(angle);
        const tag = dialZone(this.dial.direction, angle, true);
        if (tag) this.setZone("direction", tag, true);
        this.paint();
        return;
      }
      let what = kind;
      if (kind === "crop") {
        // 누른 곳에 가까운 쪽 손잡이를 끈다(인형의 긴 쪽 축을 따라)
        if (!this.layout) return;
        const [lo, hi] = this.cropSpan();
        const v = this.layout.axis === "x" ? p.x : p.y;
        what = Math.abs(v - lo) <= Math.abs(v - hi) ? "lo" : "hi";
      }
      this.drag = { kind: what, svg };
      svg.setPointerCapture(event.pointerId);
      this.move(p);
    };
    const move = (event) => {
      if (!this.drag) return;
      const p = this.point(this.drag.svg, event);
      if (p) this.move(p);
    };
    const end = () => {
      if (!this.drag) return;
      this.drag = null;
      this.paint();
    };
    for (const [svg, kind] of [[this.cropSvg, "crop"], [this.dirSvg, "direction"], [this.heightSvg, "height"]]) {
      svg.addEventListener("pointerdown", start(svg, kind));
      svg.addEventListener("pointermove", move);
      svg.addEventListener("pointerup", end);
      svg.addEventListener("pointercancel", end);
      svg.addEventListener("keydown", (event) => this.key(kind, event));
    }
  }

  // 지금 틀(없으면 온몸)의 끄는 축 구간
  cropSpan() {
    const { axis, boxes, full } = this.layout;
    const box = boxes[this.state.framing] ?? boxes[this.promptFraming()] ?? full;
    return axis === "x" ? [box[0], box[2]] : [box[1], box[3]];
  }

  move(p) {
    const drag = this.drag;
    if (drag.kind === "lo" || drag.kind === "hi") {
      const { axis, boxes } = this.layout;
      const v = axis === "x" ? p.x : p.y;
      const [lo, hi] = this.cropSpan();
      const tag = nearestBox(boxes, axis, drag.kind === "lo" ? Math.min(v, hi) : lo, drag.kind === "hi" ? Math.max(v, lo) : hi);
      if (tag) this.setFraming(tag);
    } else if (drag.kind === "direction") {
      drag.angle = Math.atan2(p.x - DIR.cx, p.y - DIR.cy) * 180 / Math.PI;
      if (Math.abs(drag.angle) > 1) this.side = Math.sign(drag.angle);
      this.setZone("direction", dialZone(this.dial.direction, drag.angle, true));
    } else {
      drag.angle = Math.max(-90, Math.min(90, Math.atan2(-(p.y - HEIGHT.cy), p.x - HEIGHT.cx) * 180 / Math.PI));
      this.setZone("height", dialZone(this.dial.height, drag.angle));
    }
    this.paint();
  }

  key(kind, event) {
    const step = { ArrowUp: -1, ArrowLeft: -1, ArrowDown: 1, ArrowRight: 1 }[event.key];
    const clear = event.key === "Delete" || event.key === "Backspace";
    if (step === undefined && !clear && event.key !== "Home" && event.key !== "End") return;
    event.preventDefault();
    if (kind === "crop") {
      const list = this.covers.framing;
      if (clear) return this.setFraming("");
      const now = list.indexOf(this.state.framing || this.promptFraming());
      const at = event.key === "Home" ? 0 : event.key === "End" ? list.length - 1 : now < 0 ? 0 : now + step;
      if (list[at]) this.setFraming(list[at]);
    } else if (kind === "direction") {
      const tags = this.axisTags.direction;
      if (clear) return this.setZone("direction", null);
      const now = tags.findIndex((tag) => this.viewBase().includes(tag));
      const at = event.key === "Home" ? 0 : event.key === "End" ? tags.length - 1 : now < 0 ? 0 : now + step;
      if (tags[at]) this.setZone("direction", tags[at]);
    } else {
      // 위 화살표가 높은 쪽
      const steps = this.heightSteps;
      if (clear) return this.setZone("height", null);
      const now = steps.findIndex((zone) => zone.tag && this.viewBase().includes(zone.tag));
      const level = steps.findIndex((zone) => !zone.tag);
      const from = now < 0 ? level : now;
      const at = event.key === "Home" ? steps.length - 1 : event.key === "End" ? 0 : from - step;
      if (steps[at]) this.setZone("height", steps[at].tag);
    }
  }

  // ─── 칠하기 ─────────────────────────────────────────────────────

  // 크롭 칸에 그릴 카메라: 끄는 중이면 그 각도를 바로 쓴다
  camera(viewTags) {
    const camera = cameraFrom(viewTags, this.dial, this.side);
    if (this.drag?.kind === "direction" && this.drag.angle !== undefined) camera.azimuth = this.drag.angle;
    if (this.drag?.kind === "height" && this.drag.angle !== undefined) camera.elevation = this.drag.angle;
    return camera;
  }

  paintFigure(viewTags) {
    const { pose, gaze } = this.state;
    const poseTags = pose.length ? pose : this.promptTags("pose");
    const gazeTags = gaze.length ? gaze : this.promptTags("gaze");
    const camera = postureCamera(this.camera(viewTags), resolvePose(poseTags).posture);
    // facing away 는 보통 등을 보인 구도다. 방향 태그가 없으면 뒤에서 본다
    const hasDirection = viewTags.some((tag) => this.axisTags.direction.includes(tag)) || this.drag?.kind === "direction";
    if (!hasDirection && gazeTags.includes("facing away")) camera.azimuth = 180;
    const joints = posedJoints(poseTags);
    const look = gazeFrom(gazeTags, joints, camera);
    const project = projector(camera);
    const samples = bodySamples(joints).map((s) => ({ ...s, s: project(s.p) }));
    const headS = project(joints.head);
    // 온몸이 칸에 들게 키를 맞추고 가운데에 놓는다
    const full = frameBox(samples, [0, 1], headS, HEAD_R);
    const scale = Math.min(CROP.scale, CROP.fitW / (full[2] - full[0]), CROP.fitH / (full[3] - full[1]));
    const ox = CROP.w / 2 - ((full[0] + full[2]) / 2) * scale;
    const oy = CROP.h / 2 - ((full[1] + full[3]) / 2) * scale;
    const px = ([x, y]) => [round(ox + x * scale), round(oy + y * scale)];
    const pxBox = (box) => box && [...px([box[0], box[1]]), ...px([box[2], box[3]])];

    // 뼈와 머리를 먼 쪽부터 그린다. 가까울수록 진하게
    const depthOpacity = (d) => String(round(0.4 + 0.45 * Math.min(1, Math.max(0, (d + 0.35) / 0.7))));
    const items = BONES.map(([a, b], i) => {
      const [sa, sb] = [project(joints[a]), project(joints[b])];
      return { el: this.boneEls[i], depth: (sa[2] + sb[2]) / 2, a: px(sa), b: px(sb) };
    });
    const neckS = project(joints.neck);
    items.push({ el: this.neckEl, depth: (neckS[2] + headS[2]) / 2, a: px(neckS), b: px(headS) });
    for (const item of items) {
      item.el.setAttribute("x1", String(item.a[0]));
      item.el.setAttribute("y1", String(item.a[1]));
      item.el.setAttribute("x2", String(item.b[0]));
      item.el.setAttribute("y2", String(item.b[1]));
      item.el.setAttribute("opacity", depthOpacity(item.depth));
    }
    const headPx = px(headS);
    const headR = round(HEAD_R * scale);
    this.headEl.setAttribute("cx", String(headPx[0]));
    this.headEl.setAttribute("cy", String(headPx[1]));
    this.headEl.setAttribute("r", String(headR));
    items.push({ el: this.headEl, depth: headS[2] + 0.02 });
    items.push({ el: this.face, depth: headS[2] + 0.03 });
    items.sort((x, y) => x.depth - y.depth);
    this.figure.replaceChildren(...items.map((item) => item.el));
    this.paintFace(joints, look, camera, project, px, headR);

    // 크롭 태그마다 몸의 그 부위를 감싸는 상자(px). 끄는 축은 온몸의 긴 쪽
    const boxes = Object.fromEntries(this.covers.framing.map((tag) => [tag, pxBox(frameBox(samples, this.frames[tag], headS, HEAD_R))]));
    this.layout = { axis: frameAxis(full), boxes, full: pxBox(full) };
  }

  paintFace(joints, look, camera, project, px, headR) {
    const { toCamera } = cameraBasis(camera);
    const hd = look.head;
    const up0 = [joints.head[0] - joints.neck[0], joints.head[1] - joints.neck[1], joints.head[2] - joints.neck[2]];
    const k = up0[0] * hd[0] + up0[1] * hd[1] + up0[2] * hd[2];
    const upRaw = [up0[0] - hd[0] * k, up0[1] - hd[1] * k, up0[2] - hd[2] * k];
    const n = Math.hypot(...upRaw) || 1;
    const upH = upRaw.map((v) => v / n);
    const rightH = [upH[1] * hd[2] - upH[2] * hd[1], upH[2] * hd[0] - upH[0] * hd[2], upH[0] * hd[1] - upH[1] * hd[0]];
    const at = (a, b, c) => joints.head.map((v, i) => v + HEAD_R * (a * hd[i] + b * rightH[i] + c * upH[i]));
    const facing = hd[0] * toCamera[0] + hd[1] * toCamera[1] + hd[2] * toCamera[2];
    [-1, 1].forEach((sideK, i) => {
      const eye = at(0.8, 0.38 * sideK, 0.12);
      // 눈알 쪽이 카메라에서 보일 때만. 뜬 눈은 보는 쪽으로 조금 밀고, 감은 눈은 짧은 선
      const dir = [eye[0] - joints.head[0], eye[1] - joints.head[1], eye[2] - joints.head[2]];
      const seen = (dir[0] * toCamera[0] + dir[1] * toCamera[1] + dir[2] * toCamera[2]) / HEAD_R > 0.15;
      const pupil = px(project(eye.map((v, j) => v + HEAD_R * 0.18 * look.eyes[j])));
      this.eyeEls[i].setAttribute("cx", String(pupil[0]));
      this.eyeEls[i].setAttribute("cy", String(pupil[1]));
      this.eyeEls[i].classList.toggle("hidden", !seen || look.closed);
      this.eyeEls[i].classList.toggle("contact", look.contact);
      const a = px(project(eye.map((v, j) => v - HEAD_R * 0.16 * rightH[j])));
      const b = px(project(eye.map((v, j) => v + HEAD_R * 0.16 * rightH[j])));
      this.lidEls[i].setAttribute("x1", String(a[0]));
      this.lidEls[i].setAttribute("y1", String(a[1]));
      this.lidEls[i].setAttribute("x2", String(b[0]));
      this.lidEls[i].setAttribute("y2", String(b[1]));
      this.lidEls[i].classList.toggle("hidden", !seen || !look.closed);
    });
    // 보는 쪽을 가리키는 점선(카메라를 똑바로 보면 화면에서 짧아지므로 그리지 않는다)
    const from = joints.head.map((v, i) => v + HEAD_R * hd[i]);
    const to = from.map((v, i) => v + 0.2 * look.eyes[i]);
    const [fx, fy] = px(project(from));
    const [tx, ty] = px(project(to));
    const showRay = !look.closed && this.hasLook() && Math.hypot(tx - fx, ty - fy) > headR * 0.9;
    this.rayEl.setAttribute("x1", String(fx));
    this.rayEl.setAttribute("y1", String(fy));
    this.rayEl.setAttribute("x2", String(tx));
    this.rayEl.setAttribute("y2", String(ty));
    this.rayEl.classList.toggle("hidden", !showRay);
    // 고개가 카메라 반대쪽이면 뒤통수(채운 머리)로 보인다
    this.headEl.classList.toggle("back", facing < -0.25);
  }

  hasLook() {
    const tags = this.state.gaze.length ? this.state.gaze : this.promptTags("gaze");
    return tags.some((t) => t.startsWith("looking") || t === "sideways glance");
  }

  paintCrop() {
    const { framing } = this.state;
    const { boxes, axis, full } = this.layout;
    const pickedFrame = framing && boxes[framing] ? framing : "";
    const promptFrame = this.promptFraming();
    const shown = pickedFrame || promptFrame;
    const frame = shown && boxes[shown] ? padBox(boxes[shown], CROP.pad) : null;
    this.dim.setAttribute("d", frame ? `${pathBox([0, 0, CROP.w, CROP.h])} ${pathBox(frame)}` : "");
    this.cropFrame.classList.toggle("hidden", !frame);
    this.cropFrame.classList.toggle("on", Boolean(pickedFrame));
    const edge = frame ?? padBox(full, CROP.pad);
    for (const [name, value] of [["x", edge[0]], ["y", edge[1]], ["width", edge[2] - edge[0]], ["height", edge[3] - edge[1]]]) {
      this.cropFrame.setAttribute(name, String(round(value)));
    }
    const ghostBox = pickedFrame && promptFrame && promptFrame !== pickedFrame && boxes[promptFrame] ? padBox(boxes[promptFrame], CROP.pad - 1) : null;
    this.cropGhost.classList.toggle("hidden", !ghostBox);
    if (ghostBox) {
      for (const [name, value] of [["x", ghostBox[0]], ["y", ghostBox[1]], ["width", ghostBox[2] - ghostBox[0]], ["height", ghostBox[3] - ghostBox[1]]]) {
        this.cropGhost.setAttribute(name, String(round(value)));
      }
    }
    // 손잡이는 끄는 축의 두 끝
    const cx = (edge[0] + edge[2]) / 2;
    const cy = (edge[1] + edge[3]) / 2;
    const spots = axis === "x" ? [[edge[0], cy], [edge[2], cy]] : [[cx, edge[1]], [cx, edge[3]]];
    const [w, h] = axis === "x" ? [7, 22] : [22, 7];
    this.handles.forEach((handle, i) => {
      handle.setAttribute("x", String(round(spots[i][0] - w / 2)));
      handle.setAttribute("y", String(round(spots[i][1] - h / 2)));
      handle.setAttribute("width", String(w));
      handle.setAttribute("height", String(h));
      handle.classList.toggle("on", Boolean(pickedFrame));
      handle.classList.toggle("framed", Boolean(frame));
    });
    this.cropSvg.classList.toggle("qs-axis-x", axis === "x");
    this.cropAuto.setAttribute("aria-pressed", String(!framing));
    this.setRead(this.cropRead, pickedFrame || (promptFrame ? promptFrame : "자동"), Boolean(pickedFrame));
    this.cropSvg.setAttribute("aria-valuenow", String(Math.max(0, this.covers.framing.indexOf(shown))));
    this.cropSvg.setAttribute("aria-valuetext", pickedFrame || (promptFrame ? `자동(${promptFrame})` : "자동"));
  }

  paint() {
    const { view } = this.state;
    this.paintFigure(view.length ? view : this.promptView());
    this.paintCrop();

    const picked = view.length > 0;
    const tags = picked ? view : this.promptView();
    const promptTags = this.promptView();
    for (const path of [...this.dirZones, ...this.heightZones]) {
      const tag = path.getAttribute("data-tag");
      const on = tag ? tags.includes(tag) : !tags.some((t) => this.axisTags.height.includes(t)) && path.ownerSVGElement === this.heightSvg;
      path.classList.toggle("on", on && picked);
      path.classList.toggle("det", on && !picked);
      path.classList.toggle("prompt", picked && Boolean(tag) && promptTags.includes(tag) && !tags.includes(tag));
    }
    const dirTags = this.dial.direction.filter((zone) => tags.includes(zone.tag));
    this.dirMarks.forEach((mark, i) => {
      const dragging = this.drag?.kind === "direction" && this.drag.angle !== undefined;
      const zone = dirTags[i];
      const show = dragging ? i === 0 : Boolean(zone);
      mark.classList.toggle("hidden", !show);
      if (!show) return;
      const [x, y] = polar(DIR.cx, DIR.cy, DIR.cam, dragging ? this.drag.angle : zone.at * (zone.at % 180 ? this.side : 1));
      mark.setAttribute("cx", String(x));
      mark.setAttribute("cy", String(y));
      mark.classList.toggle("on", picked || dragging);
    });
    const heightZone = this.dial.height.find((zone) => tags.includes(zone.tag));
    const e = this.drag?.kind === "height" && this.drag.angle !== undefined ? this.drag.angle : heightZone?.at ?? 0;
    const [hx, hy] = polar(HEIGHT.cx, HEIGHT.cy, HEIGHT.cam, 90 + e);
    this.heightMark.setAttribute("cx", String(hx));
    this.heightMark.setAttribute("cy", String(hy));
    this.heightMark.classList.toggle("on", picked);

    const dirText = dirTags.map((zone) => zone.tag).join(" + ");
    this.setRead(this.dirRead, dirText || (picked ? "방향 태그 없음" : "자동"), picked);
    this.setRead(this.heightRead, heightZone?.tag || (picked || tags.length ? "수평" : "자동"), picked);
    const dirIndex = this.dial.direction.findIndex((zone) => tags.includes(zone.tag));
    this.dirSvg.setAttribute("aria-valuenow", String(Math.max(0, dirIndex)));
    this.dirSvg.setAttribute("aria-valuetext", dirText || "없음");
    const heightIndex = this.heightSteps.findIndex((zone) => (heightZone ? zone.tag === heightZone.tag : !zone.tag));
    this.heightSvg.setAttribute("aria-valuenow", String(Math.max(0, heightIndex)));
    this.heightSvg.setAttribute("aria-valuetext", heightZone?.tag || "수평");
  }

  setRead(node, text, picked) {
    node.textContent = text;
    node.classList.toggle("on", picked);
  }
}
