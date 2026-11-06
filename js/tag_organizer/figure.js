// 퀵 세팅 그림의 목각 인형: 포즈 태그 → 관절 자리(3D), 시점 태그 → 카메라, 시선 태그 → 고개·눈 방향, 그리고 화면 투영.
// DOM 을 쓰지 않는 계산만 둔다(그리기는 stage.js). import 만으로는 아무 일도 하지 않는다.
//
// 좌표: 키 1 인 사람, 발바닥 y=0. x 는 정면 카메라에서 본 오른쪽, y 위, z 앞(정면 카메라 쪽).
// 관절마다 '선 자세에서 정수리부터 잰 키의 몇 분의 몇'(FRACTION)이 정해져 있어, 크롭 태그의 범위(palette.framing_frame)를
// 어떤 자세에서든 몸의 같은 부위로 옮길 수 있다.

export const HEAD_R = 0.065;

export const FRACTION = {
  head: 0.065, neck: 0.16, pelvis: 0.48, ls: 0.19, rs: 0.19, le: 0.36, re: 0.36, lh: 0.52, rh: 0.52,
  lhip: 0.5, rhip: 0.5, lk: 0.73, rk: 0.73, lf: 0.98, rf: 0.98,
};

export const BONES = [
  ["neck", "pelvis"], ["ls", "neck"], ["neck", "rs"], ["ls", "le"], ["le", "lh"], ["rs", "re"], ["re", "rh"],
  ["lhip", "rhip"], ["lhip", "lk"], ["lk", "lf"], ["rhip", "rk"], ["rk", "rf"],
];

// ─── 벡터 ─────────────────────────────────────────────────────────

const add = (a, b) => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const mul = (a, k) => [a[0] * k, a[1] * k, a[2] * k];
const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const unit = (a) => {
  const n = Math.hypot(...a);
  return n > 1e-9 ? mul(a, 1 / n) : [0, 0, 1];
};
const rad = (d) => (d * Math.PI) / 180;

// from 을 to 쪽으로 최대 limit 도까지 돌린 방향(고개가 몸을 넘어 다 돌지는 않는다)
function turnToward(from, to, limit) {
  const a = unit(from);
  const b = unit(to);
  const angle = Math.acos(Math.max(-1, Math.min(1, dot(a, b))));
  if (angle < 1e-6) return a;
  const t = Math.min(1, rad(limit) / angle);
  // 거의 정반대면 옆으로 돌아서 간다
  const side = angle > Math.PI - 1e-3 ? unit(cross(a, [0, 1, 0])) : unit(sub(b, mul(a, dot(a, b))));
  const theta = angle * t;
  return unit(add(mul(a, Math.cos(theta)), mul(side, Math.sin(theta))));
}

// ─── 자세 ─────────────────────────────────────────────────────────

const pair = (name, x, y, z, xr = -x) => ({ [`l${name}`]: [x, y, z], [`r${name}`]: [xr, y, z] });
const mirror = (name, x, y, z) => pair(name, -x, y, z, x);

const STANDING = {
  head: [0, 0.935, 0], neck: [0, 0.84, 0], pelvis: [0, 0.52, 0],
  ...mirror("s", 0.11, 0.81, 0), ...mirror("e", 0.135, 0.64, -0.01), ...mirror("h", 0.145, 0.48, 0.03),
  ...mirror("hip", 0.06, 0.5, 0), ...mirror("k", 0.065, 0.27, 0.015), ...mirror("f", 0.07, 0.02, 0.04),
};

// 선 자세의 위쪽(머리·팔)을 dy 만큼 내린다(앉기·무릎 꿇기의 상체)
const upper = (dy, dz = 0) => Object.fromEntries(["head", "neck", "ls", "rs", "le", "re", "lh", "rh"]
  .map((k) => [k, [STANDING[k][0], STANDING[k][1] - dy, STANDING[k][2] + dz]]));

// 선 자세 좌표를 누운 자세로 옮긴다: 머리가 -X, 키 방향 → X. front 는 몸 앞이 향하는 축
function laid(joints, front) {
  const map = {
    up: ([x, y, z]) => [0.5 - y, 0.07 + z, -x],          // 바로 누움: 몸 앞이 위
    side: ([x, y, z]) => [0.5 - y, 0.16 + x, z],         // 왼쪽으로 누움: 몸 앞이 카메라
    down: ([x, y, z]) => [0.5 - y, 0.07 - z, x],         // 엎드림: 몸 앞이 바닥
  }[front];
  return Object.fromEntries(Object.entries(joints).map(([k, p]) => [k, map(p)]));
}

export const POSTURES = {
  "standing": STANDING,
  "standing on one leg": { ...STANDING, rk: [0.07, 0.36, 0.17], rf: [0.07, 0.15, 0.1],
    le: [-0.24, 0.74, 0], lh: [-0.36, 0.78, 0], re: [0.24, 0.74, 0], rh: [0.36, 0.78, 0] },
  "contrapposto": { ...STANDING, head: [-0.01, 0.935, 0], pelvis: [-0.01, 0.51, 0], ls: [-0.11, 0.8, 0], rs: [0.11, 0.825, 0],
    lhip: [-0.06, 0.51, 0], rhip: [0.065, 0.48, 0], lk: [-0.055, 0.27, 0], lf: [-0.045, 0.02, 0.02],
    rk: [0.09, 0.25, 0.05], rf: [0.13, 0.03, 0.06], re: [0.2, 0.67, -0.03], rh: [0.09, 0.53, 0.02] },
  "sitting": { ...upper(0.22, -0.03), pelvis: [0, 0.3, -0.02], ...mirror("hip", 0.06, 0.29, 0), ...mirror("k", 0.07, 0.3, 0.24),
    ...mirror("f", 0.07, 0.02, 0.24), ...mirror("e", 0.14, 0.43, 0.02), ...mirror("h", 0.1, 0.32, 0.14) },
  "wariza": { ...upper(0.46), pelvis: [0, 0.06, 0], ...mirror("hip", 0.06, 0.05, 0), ...mirror("k", 0.05, 0.03, 0.2),
    ...mirror("f", 0.2, 0.02, -0.02), ...mirror("e", 0.13, 0.2, 0.06), ...mirror("h", 0.08, 0.06, 0.14) },
  "seiza": { ...upper(0.38, -0.03), pelvis: [0, 0.14, -0.04], ...mirror("hip", 0.06, 0.13, -0.03), ...mirror("k", 0.06, 0.03, 0.2),
    ...mirror("f", 0.05, 0.02, -0.08), ...mirror("e", 0.13, 0.28, 0.04), ...mirror("h", 0.08, 0.16, 0.15) },
  "lying": laid(STANDING, "up"),
  "on back": laid({ ...STANDING, rk: [0.065, 0.36, 0.2], rf: [0.07, 0.1, 0.12] }, "up"),
  "on side": laid({ ...STANDING, lk: [-0.065, 0.27, 0.09], lf: [-0.07, 0.02, 0.06], rk: [0.065, 0.28, 0.1], rf: [0.07, 0.03, 0.04],
    le: [-0.13, 0.97, 0.05], lh: [-0.05, 0.99, 0.02] }, "side"),
  "on stomach": laid({ ...STANDING, head: [0, 0.935, -0.03], le: [-0.13, 0.86, 0.07], lh: [-0.08, 0.98, 0.07],
    re: [0.13, 0.86, 0.07], rh: [0.08, 0.98, 0.07], rf: [0.07, 0.15, -0.2] }, "down"),
  "kneeling": { ...upper(0.27), pelvis: [0, 0.25, 0], ...mirror("hip", 0.06, 0.25, 0), ...mirror("k", 0.065, 0.03, 0.02),
    ...mirror("f", 0.07, 0.02, -0.22) },
  "on one knee": { ...upper(0.25), pelvis: [0, 0.27, 0], lhip: [-0.06, 0.26, 0], lk: [-0.065, 0.03, -0.02], lf: [-0.07, 0.02, -0.24],
    rhip: [0.06, 0.26, 0], rk: [0.07, 0.27, 0.22], rf: [0.07, 0.02, 0.24], re: [0.15, 0.42, 0.1], rh: [0.1, 0.3, 0.2] },
  "squatting": { head: [0, 0.57, 0.06], neck: [0, 0.48, 0.03], pelvis: [0, 0.18, -0.08], ...mirror("s", 0.11, 0.46, 0.03),
    ...mirror("e", 0.14, 0.32, 0.12), ...mirror("h", 0.08, 0.26, 0.22), ...mirror("hip", 0.07, 0.18, -0.07),
    ...mirror("k", 0.12, 0.28, 0.14), ...mirror("f", 0.09, 0.02, 0.06) },
  "all fours": { head: [0, 0.36, 0.24], neck: [0, 0.3, 0.15], pelvis: [0, 0.27, -0.18], ...mirror("s", 0.11, 0.3, 0.13),
    ...mirror("e", 0.11, 0.15, 0.14), ...mirror("h", 0.11, 0.01, 0.15), ...mirror("hip", 0.07, 0.27, -0.18),
    ...mirror("k", 0.07, 0.03, -0.16), ...mirror("f", 0.07, 0.02, -0.4) },
};

// 상체를 골반 둘레로 앞(+)·뒤(-)로 기울이는 태그. 몸이 바닥에 붙은 자세에는 쓰지 않는다
export const LEANS = { "leaning forward": 30, "bent over": 82, "leaning back": -20 };
const UPRIGHT = new Set(["standing", "standing on one leg", "contrapposto", "sitting", "wariza", "seiza", "kneeling", "on one knee", "squatting"]);

/** 포즈 태그들 → {posture, lean}. 더 구체적인(퀵 목록에서 뒤쪽) 자세를 쓰고, 기울기는 따로 더한다. 모르는 태그는 건너뛴다. */
export function resolvePose(tags) {
  let posture = "standing";
  let lean = 0;
  for (const tag of tags) {
    if (POSTURES[tag]) posture = tag;
    else if (tag in LEANS) lean = LEANS[tag];
  }
  return { posture, lean: UPRIGHT.has(posture) ? lean : 0 };
}

/** 포즈 태그들의 관절 자리. */
export function posedJoints(tags) {
  const { posture, lean } = resolvePose(tags);
  const joints = Object.fromEntries(Object.entries(POSTURES[posture]).map(([k, p]) => [k, [...p]]));
  if (!lean) return joints;
  const pivot = joints.pelvis;
  const t = rad(lean);
  for (const k of ["head", "neck", "ls", "rs", "le", "re", "lh", "rh"]) {
    const [x, y, z] = sub(joints[k], pivot);
    joints[k] = add(pivot, [x, y * Math.cos(t) - z * Math.sin(t), y * Math.sin(t) + z * Math.cos(t)]);
  }
  if (lean > 60) {
    // 허리를 굽히면 팔은 바닥 쪽으로 늘어진다
    for (const s of ["l", "r"]) {
      joints[`${s}e`] = add(joints[`${s}s`], [0, -0.16, 0.02]);
      joints[`${s}h`] = add(joints[`${s}s`], [0, -0.31, 0.04]);
    }
  }
  return joints;
}

// ─── 카메라 ───────────────────────────────────────────────────────

/** 시점 태그 → {azimuth, elevation, roll}(도). 방향은 다이얼 구간의 at 평균(side 쪽), 높이는 at, dutch angle 이면 기울임. */
export function cameraFrom(viewTags, dial = {}, side = 1) {
  const dirs = (dial.direction ?? []).filter((zone) => viewTags.includes(zone.tag));
  const height = (dial.height ?? []).find((zone) => viewTags.includes(zone.tag));
  const azimuth = dirs.length ? dirs.reduce((sum, zone) => sum + zone.at, 0) / dirs.length : 0;
  return { azimuth: azimuth % 180 ? azimuth * side : azimuth, elevation: height?.at ?? 0, roll: viewTags.includes("dutch angle") ? 12 : 0 };
}

// 누운 자세는 키가 x 축이라 정면 카메라가 몸 옆모습을 본다. from side 도 옆모습이 되게 옆 구간의 방향을 90° 돌린다
export const LYING = new Set(["lying", "on back", "on side", "on stomach"]);

/** 자세에 맞춘 카메라(누운 자세의 옆 방향만 바꾼다). */
export function postureCamera(camera, posture) {
  const az = camera.azimuth;
  if (!LYING.has(posture) || Math.abs(Math.abs(az) - 90) >= 30) return camera;
  return { ...camera, azimuth: az - Math.sign(az) * 90 };
}

/** 피사체에서 카메라 쪽을 가리키는 단위 벡터와 화면 기준(오른쪽·위). */
export function cameraBasis({ azimuth, elevation }) {
  const a = rad(azimuth);
  const e = rad(elevation);
  const toCamera = [Math.sin(a) * Math.cos(e), Math.sin(e), Math.cos(a) * Math.cos(e)];
  const right = unit(Math.abs(toCamera[1]) > 0.999 ? cross(toCamera, [0, 0, -1]) : cross([0, 1, 0], toCamera));
  return { toCamera, right, up: cross(toCamera, right) };
}

/** 3D 점 → 화면 [x, y(아래로), 깊이(카메라 쪽이 큼)], 키 1 단위. */
export function projector(camera) {
  const { toCamera, right, up } = cameraBasis(camera);
  const r = rad(camera.roll ?? 0);
  return (p) => {
    const x = dot(p, right);
    const y = -dot(p, up);
    return [x * Math.cos(r) - y * Math.sin(r), x * Math.sin(r) + y * Math.cos(r), dot(p, toCamera)];
  };
}

// ─── 시선 ─────────────────────────────────────────────────────────

const HEAD_TURN = 100;   // 고개가 몸 앞에서 돌아가는 최대 각도

/**
 * 시선 태그 → {head: 고개가 향하는 방향, eyes: 눈이 보는 방향, closed: 눈 감음(facing), contact: 카메라를 봄}.
 * facing 은 고개(눈은 감거나 가림), looking 은 눈. facing 이 없으면 고개는 보는 쪽으로 절반쯤 따라간다.
 */
export function gazeFrom(tags, joints, camera) {
  const { toCamera, right: screenRight } = cameraBasis(camera);
  const bodyUp = unit(sub(joints.neck, joints.pelvis));
  const bodyRight = unit(sub(joints.rs, joints.ls));
  const forward = unit(cross(bodyRight, bodyUp));
  const headUp = unit(sub(joints.head, joints.neck));
  const target = (word) => ({
    "viewer": toCamera, "back": toCamera, "to the side": screenRight, "glance": screenRight, "ahead": forward,
    "up": add(mul(forward, Math.cos(rad(50))), mul(headUp, Math.sin(rad(50)))),
    "down": add(mul(forward, Math.cos(rad(50))), mul(headUp, -Math.sin(rad(50)))),
    "afar": add(mul(forward, Math.cos(rad(12))), mul(headUp, Math.sin(rad(12)))),
    "away": mul(toCamera, -1),
  })[word];
  const word = (tag) => tag.replace(/^(looking at |looking |facing )/, "").replace(/^sideways /, "");
  const looks = tags.filter((t) => t.startsWith("looking") || t === "sideways glance").map((t) => target(word(t))).filter(Boolean);
  const faces = tags.filter((t) => t.startsWith("facing")).map((t) => target(word(t))).filter(Boolean);
  const mean = (vs) => unit(vs.reduce((s, v) => add(s, unit(v)), [0, 0, 0]));
  const eyes = looks.length ? mean(looks) : null;
  const glanceOnly = tags.includes("sideways glance") && looks.length === 1;
  // 고개는 보는 쪽으로 절반쯤. 등 뒤를 보면(앞과 정반대) 절반이 사라지므로 보는 쪽을 그대로 겨눈다(돌 수 있는 만큼만 돈다)
  const half = eyes ? add(forward, eyes) : null;
  const follow = half && Math.hypot(...half) > 0.3 ? unit(half) : eyes;
  const wanted = faces.length ? mean(faces) : eyes && !glanceOnly ? follow : forward;
  const head = turnToward(forward, wanted, HEAD_TURN);
  return { head, eyes: eyes ?? head, closed: faces.length > 0 && !looks.length, contact: Boolean(eyes) && dot(eyes, toCamera) > 0.9 };
}

// ─── 크롭 ─────────────────────────────────────────────────────────

/** 뼈를 따라 나눈 점들 [{p, f}](f: 선 자세 키의 몇 분의 몇). 머리는 정수리·턱도 넣는다. */
export function bodySamples(joints, steps = 8) {
  const out = [];
  for (const [a, b] of BONES) {
    for (let i = 0; i <= steps; i++) {
      const t = i / steps;
      out.push({ p: add(joints[a], mul(sub(joints[b], joints[a]), t)), f: FRACTION[a] + (FRACTION[b] - FRACTION[a]) * t });
    }
  }
  const up = unit(sub(joints.head, joints.neck));
  for (const [k, f] of [[1, 0], [0, FRACTION.head], [-1, 0.13]]) out.push({ p: add(joints.head, mul(up, HEAD_R * k)), f, head: true });
  return out;
}

/** 화면 점들 중 크롭 범위 [top, bottom] 안의 것을 감싸는 상자 [x0, y0, x1, y1]. 머리가 들면 머리 둘레까지. 없으면 null. */
export function frameBox(projected, [top, bottom], headScreen = null, headR = 0) {
  let box = null;
  const grow = (x0, y0, x1, y1) => {
    box = box ? [Math.min(box[0], x0), Math.min(box[1], y0), Math.max(box[2], x1), Math.max(box[3], y1)] : [x0, y0, x1, y1];
  };
  for (const { s, f, head } of projected) {
    if (f >= top - 1e-9 && f <= bottom + 1e-9) grow(s[0], s[1], s[0], s[1]);
    if (head && headScreen && f === FRACTION.head && top <= 0.09) {
      grow(headScreen[0] - headR, headScreen[1] - headR, headScreen[0] + headR, headScreen[1] + headR);
    }
  }
  return box;
}

/** 크롭 틀을 끄는 축: 온몸 상자의 긴 쪽(누운 자세면 가로). */
export const frameAxis = (box) => (box && box[2] - box[0] > box[3] - box[1] ? "x" : "y");

/** 틀의 그 축 구간에 가장 가까운 태그. boxes: {태그: 상자}. */
export function nearestBox(boxes, axis, from, to) {
  const [i, j] = axis === "x" ? [0, 2] : [1, 3];
  let best = null;
  let score = Infinity;
  for (const [tag, box] of Object.entries(boxes)) {
    if (!box) continue;
    const s = Math.abs(box[i] - from) + Math.abs(box[j] - to);
    if (s < score - 1e-9) {
      best = tag;
      score = s;
    }
  }
  return best;
}
