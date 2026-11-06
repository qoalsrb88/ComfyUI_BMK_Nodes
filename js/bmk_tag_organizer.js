// bmk_tag_organizer.js — BMKTagOrganizer 짝 JS 확장 (v10)
//
// 배열 세팅(arrangement) 청크 편집기를 노드에 붙이는 ComfyUI 어댑터. 편집기 자체는 TagOrganizer 저장소의
// extension/editor/ 가 정본이고(크롬 확장 패널과 같은 코드), js/tag_organizer/ 는 그 저장소의
// `python tagorg.py sync` 가 복사한 파일이다. 여기서 직접 고치면 다음 동기화가 멈춘다. 조작은 editor.js 머리 주석 참고.
// 칩 그림(glyphs.js), 시선 격자·포즈 묶음, 퀵 세팅 그림(stage.js)의 모양은 NovelAI 미리보기 패널
// (extension/nai/panel.js·panel.css)과 같다.
//
// 이 파일이 맡는 것(editor.js 의 host):
// - 결과는 숨긴 STRING 위젯 arrangement 에 JSON 으로 쓴다(widgets_values·API 프롬프트로 그대로 간다).
//   위젯이 비어 있으면 노드는 layout 을 쓰고, 편집기는 그 레이아웃과 같은 배열에서 시작한다(손대기 전엔 비운 채).
//   되돌리기는 워크플로 Ctrl+Z(편집마다 changeTracker 에 기록).
// - 미리보기: 지금 위젯 값으로 서버 /bmk/tag_organizer/preview 를 부른다(실행과 같은 옵션 함수).
//   text 를 상류에 연결했으면 마지막 실행의 입력(ui.bmk_tag_input).
// - 분류·색·레이아웃별 시작 배열은 /bmk/tag_organizer/palette 에서 한 번 받는다(⋯ → 팩 다시 읽기).
// - 시점·시선·포즈 칩: 편집기 맨 위 세 줄. 여러 개를 고르고 결과는 숨긴 STRING 위젯 view·gaze·pose 에 "a, b" 로 쓴다.
//   시점은 켠 칩과 부딪치는 칩이 꺼지고(까닭을 줄 끝에 잠시 보임), multiple views 칩을 켜거나 프롬프트에 형식 태그가
//   있으면(미리보기의 view_lifted) 함께 고른다. v5 까지 콤보가 저장한 값 하나(from behind, auto)는 그대로 읽는다.
//   시선은 지금 크롭·시점과 맞지 않는 칩을 미리보기 결과(gaze_blocked)를 따라 숨기되 선택은 남긴다. 코어가 막힌 선택을
//   넣지 않으므로 시점을 되돌리면 다시 든다. text 가 연결돼 있으면 한 번 실행한 뒤부터 숨긴다.
//   포즈도 같은 방식이다(미리보기의 pose_blocked). 몸이 동시에 할 수 없는 칩이 꺼지고, 함의 관계(lying + on back)는 함께 고른다.
// - 칩 모양: 태그 칩에 그림을 붙인다. palette 의 gaze_grid 가 있으면 시선은 방향(열) × looking·facing(행) 격자로, 자동 칩은
//   격자 왼쪽 위 모서리에, 고른 태그 이름은 격자 옆에 글로 둔다. pose_groups 가 있으면 포즈는 자세 묶음(이름표 + 칩)으로 둔다.
//   격자·묶음에 없는 퀵 태그는 뒤에 보통 칩으로 둔다. 옛 팩처럼 두 목록이 비면 예전처럼 한 줄로 늘어놓는다.
// - 퀵 세팅 그림: 시점 줄 끝의 그림 단추로 켜고 끈다(켬 상태는 node.properties.bmk_to_stage). 켜면 시점 줄 옆에 크롭 틀과
//   방향·높이 다이얼이 나오고, 그림이 고른 값은 framing 콤보와 숨긴 view 위젯에 쓴다. 다이얼이 맡는 시점 칩은 숨기고 다이얼 밖
//   태그(straight-on, pov 등)만 칩으로 남긴다. 점선 '지금 값'은 미리보기 토큰 중 덮어쓰기로 넣지 않은 태그다. 끄는 동안의 중간
//   값은 되돌리기 기록에 남기지 않고 손을 뗄 때 한 번 남긴다. palette 에 framing_frame·view_dial 이 없으면(옛 팩) 단추가 없다.
//
// 상태는 ArrangementEditor·host 객체에 두고 위젯에는 자체 필드를 얹지 않는다(프론트 1.53 이 위젯을
// BaseWidget 으로 흡수하며 충돌할 수 있음 — bmk_sam3_mask_layers.js 와 같은 이유).

import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { ArrangementEditor, el } from "./tag_organizer/editor.js";
import { glyph } from "./tag_organizer/glyphs.js";
import { gazeToggle, poseToggle, splitItems, viewToggle } from "./tag_organizer/model.js";
import { QuickStage, stageCovers } from "./tag_organizer/stage.js";

const NODE_ID = "BMKTagOrganizer";
const ARR = "arrangement";
const GAZE = "gaze";
const VIEW = "view";
const POSE = "pose";
const FRAMING = "framing";   // 크롭은 노드의 콤보 위젯(auto + 퀵 크롭 태그)
const STAGE_PROP = "bmk_to_stage";
const STAGE_KEYS = new Set(["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Home", "End", "Delete", "Backspace"]);
const AUTO = "auto";         // v5 까지의 view 콤보가 저장한 값. 고른 것이 없다는 뜻
const EDITOR_TYPE = "BMK_TAG_ARRANGEMENT";
const PALETTE_URL = "/bmk/tag_organizer/palette";
const PREVIEW_URL = "/bmk/tag_organizer/preview";
const OPTION_KEYS = [
  "preset", "exclude", "max_rating", "rules", "layout", "input_dialect", "output_dialect",
  "line_per_section", "trailing_comma", "dedupe", "framing", VIEW, "guess",
  "dialect_priority", "comments", "sentences", GAZE, POSE,
];
const MIN_WIDTH = 560;
const ADD_HEIGHT = 640;      // 처음 만들 때 편집기 몫으로 늘리는 높이
const EDITOR_MIN_H = 520;    // 칩 세 줄(시선 격자 포함)을 빼고도 청크·미리보기가 남는 높이
const STAGE_MIN_H = 140;     // 그림을 켜면 더하는 높이(그림 높이에서 옆으로 옮긴 시점 줄 몫을 뺀 것)
const MARGIN = 6;
const NOTE_MS = 8000;        // 칩이 꺼진 까닭을 보이는 시간
const GLYPH_SIZE = { view: 16, gaze: 16, pose: 18 };   // 칩 안 그림 높이(px)
const CELL_GLYPH = 18;                                  // 시선 격자 칸의 그림 높이
const GAZE_ROWS = [["looking", "looking: 눈이 보일 때"], ["facing", "facing: 눈을 감거나 가렸을 때"]];
const CHIP_STYLE = `
.bmk-to-chips{flex:0 0 auto;display:flex;flex-wrap:wrap;align-items:center;gap:3px}
.bmk-to-chips[hidden]{display:none}
.bmk-to-chips>span{margin-right:3px;color:var(--descrip-text,#999)}
.bmk-to-chips>span.note{flex:1 1 100%;color:#d4a017}
.bmk-to-chips button{height:20px;padding:0 7px;border:1px solid var(--border-color,#4e4e4e);border-radius:10px;background:var(--comfy-input-bg,#222);color:var(--input-text,#ddd);font:inherit;font-size:11px;line-height:18px;cursor:pointer}
.bmk-to-chips button:hover{border-color:var(--input-text,#ddd)}
.bmk-to-chips button[aria-pressed=true]{border-color:var(--p-primary-color,#5b8def);background:var(--p-primary-color,#5b8def);color:#fff}
.bmk-to-chips button.tag{display:inline-flex;align-items:center;gap:2px;padding:0 6px}
.bmk-to-chips button.tag:has(.glyph){padding-left:2px}
.bmk-to-chips .glyph{flex:0 0 auto}
.bmk-to-chips button.tag:not(:has(.glyph))[aria-pressed=true]::before{content:"✓ "}
.bmk-to-chips>span.bmk-to-grp{display:contents}
.bmk-to-grp-head{display:inline-flex;align-items:center;gap:3px}
.bmk-to-grp-label{margin-left:3px;padding-left:5px;border-left:1px solid var(--border-color,#4e4e4e);color:var(--descrip-text,#999);font-size:10px;white-space:nowrap}
.bmk-to-chips>span.bmk-to-picked{flex:1 1 80px;margin-left:4px;font-size:11px;overflow-wrap:anywhere}
.bmk-to-gaze{flex:0 1 auto;min-width:0;display:grid;grid-template-columns:max-content repeat(var(--cols),minmax(24px,34px));gap:2px 3px;align-items:center}
.bmk-to-gaze-corner{padding-right:4px}
.bmk-to-gaze-head{color:var(--descrip-text,#999);font-size:10px;white-space:nowrap}
.bmk-to-gaze-head.col{display:flex;justify-content:center;overflow:visible}
.bmk-to-gaze-head.row{padding-right:4px;cursor:help}
.bmk-to-chips button.bmk-to-cell{display:flex;align-items:center;justify-content:center;height:22px;padding:0;border-radius:6px}
.bmk-to-gaze-empty{height:22px}
.bmk-to-chips button.bmk-to-stage-btn{margin-left:auto}
.bmk-to-quick{flex:0 0 auto;display:flex;flex-wrap:wrap;align-items:flex-start;gap:4px 12px}
.bmk-to-quick>.bmk-to-chips{flex:1 1 240px;min-width:0}
`;
// 퀵 세팅 그림(stage.js). 규칙은 panel.css 의 .qs-* 와 같고, 색 이름을 ComfyUI 변수에 잇는다.
// 점선은 프롬프트에 든 지금 값, 채움은 덮어쓰기
const STAGE_STYLE = `
.bmk-to-stage{flex:0 0 auto;--muted:var(--descrip-text,#999);--bg:var(--comfy-input-bg,#222);--fg:var(--input-text,#ddd);--accent:var(--p-primary-color,#5b8def);--accent-fg:#fff;--line:var(--border-color,#4e4e4e);--line-strong:color-mix(in srgb,var(--input-text,#ddd) 32%,transparent)}
.bmk-to-stage[hidden]{display:none}
.bmk-to-stage .qs{display:flex;gap:12px}
.bmk-to-stage .qs-col{display:flex;flex-direction:column;align-items:center;gap:2px}
.bmk-to-stage .qs-head{display:flex;align-items:center;gap:4px;min-height:20px;color:var(--muted);font-size:11px}
.bmk-to-stage button.qs-auto{height:20px;padding:0 8px;border:1px solid var(--line);border-radius:10px;background:var(--bg);color:var(--fg);font:inherit;font-size:11px;line-height:18px;cursor:pointer}
.bmk-to-stage button.qs-auto:hover{border-color:var(--fg)}
.bmk-to-stage button.qs-auto[aria-pressed=true]{border-color:var(--accent);background:var(--accent);color:var(--accent-fg)}
.bmk-to-stage .qs-dial-row{display:flex;gap:4px}
.bmk-to-stage .qs-svg{display:block;overflow:visible;touch-action:none;cursor:grab;border-radius:4px}
.bmk-to-stage .qs-crop-svg{cursor:ns-resize;background:var(--bg)}
.bmk-to-stage .qs-crop-svg.qs-axis-x{cursor:ew-resize}
.bmk-to-stage .qs-svg:focus-visible{outline:2px solid var(--accent);outline-offset:1px}
.bmk-to-stage .qs-read{max-width:136px;color:var(--muted);font-size:11px;line-height:1.3;text-align:center;overflow-wrap:anywhere}
.bmk-to-stage .qs-crop .qs-read{max-width:100px}
.bmk-to-stage .qs-read.on{color:var(--accent)}
.bmk-to-stage .hidden{display:none}
.bmk-to-stage .qs-figure{fill:none;stroke:var(--muted);stroke-width:4.5;stroke-linecap:round}
.bmk-to-stage .qs-head-shape{fill:var(--bg);stroke-width:2}
.bmk-to-stage .qs-head-shape.back{fill:var(--muted);fill-opacity:.55}
.bmk-to-stage .qs-eye{fill:var(--fg);stroke:none}
.bmk-to-stage .qs-eye.contact{fill:var(--accent)}
.bmk-to-stage .qs-lid{stroke:var(--fg);stroke-width:1.2}
.bmk-to-stage .qs-ray{stroke:var(--accent);stroke-width:1.2;stroke-dasharray:2 2}
.bmk-to-stage .qs-dim{fill:var(--bg);opacity:.72}
.bmk-to-stage .qs-frame{fill:none;stroke:var(--muted);stroke-width:1.6;stroke-dasharray:4 3}
.bmk-to-stage .qs-frame.on{stroke:var(--accent);stroke-dasharray:none}
.bmk-to-stage .qs-ghost{fill:none;stroke:var(--muted);stroke-width:1;stroke-dasharray:2 2}
.bmk-to-stage .qs-handle{fill:var(--muted)}
.bmk-to-stage .qs-handle:not(.framed){opacity:.3}
.bmk-to-stage .qs-crop-svg:hover .qs-handle,.bmk-to-stage .qs-crop-svg:focus-visible .qs-handle{opacity:1}
.bmk-to-stage .qs-handle.on{fill:var(--accent)}
.bmk-to-stage .qs-track{fill:none;stroke:var(--line);stroke-width:1}
.bmk-to-stage .qs-zone{fill:transparent;stroke:var(--line-strong);stroke-width:.8}
.bmk-to-stage .qs-zone:hover{fill:color-mix(in srgb,var(--accent) 14%,transparent)}
.bmk-to-stage .qs-zone.on{fill:color-mix(in srgb,var(--accent) 50%,transparent);stroke:var(--accent)}
.bmk-to-stage .qs-zone.det{fill:color-mix(in srgb,var(--muted) 18%,transparent);stroke:var(--muted);stroke-dasharray:3 2}
.bmk-to-stage .qs-zone.prompt{stroke:var(--muted);stroke-dasharray:2 2}
.bmk-to-stage .qs-subject{fill:none;stroke:var(--muted);stroke-width:1.6;stroke-linecap:round;stroke-linejoin:round}
.bmk-to-stage .qs-cam{fill:var(--bg);stroke:var(--muted);stroke-width:1.6;stroke-dasharray:2 2}
.bmk-to-stage .qs-cam.on{fill:var(--accent);stroke:var(--accent);stroke-dasharray:none}
.bmk-to-stage .qs-col.bmk-to-off{opacity:.4;pointer-events:none}
`;

function checkState() {
  try {
    app.extensionManager?.workflow?.activeWorkflow?.changeTracker?.checkState?.();
  } catch {
    // undo 기록은 선택 사항
  }
}

// ─── 서버 ───

let palettePromise = null;

function loadPalette(force = false) {
  if (!palettePromise || force) {
    palettePromise = api
      .fetchApi(PALETTE_URL)
      .then(async (res) => {
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data?.error || `HTTP ${res.status}`);
        return data;
      })
      .catch((e) => {
        palettePromise = null;
        throw e;
      });
  }
  return palettePromise;
}

async function postPreview(body) {
  const res = await api.fetchApi(PREVIEW_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data?.error || `HTTP ${res.status}`);
  return data;
}

// 배열 JSON 을 코어로 검사한다(패턴 정규식까지). 문제가 없으면 null
async function validateArrangement(json) {
  try {
    await postPreview({ text: "", arrangement: json });
    return null;
  } catch (e) {
    return String(e.message || e);
  }
}

// ─── 시점·시선·포즈 칩 ───

function injectChipStyle() {
  if (document.getElementById("bmk-to-chips-style")) return;
  const style = el("style", "", CHIP_STYLE + STAGE_STYLE);
  style.id = "bmk-to-chips-style";
  document.head.appendChild(style);
}

function chipButton(cls, pressed, title, onClick) {
  const button = el("button", cls);
  button.type = "button";
  button.title = title;
  button.setAttribute("aria-pressed", String(pressed));
  button.addEventListener("click", onClick);
  return button;
}

// 시선 격자: 열은 방향, 행은 looking·facing. 칸에는 그림만 있으니 고른 태그 이름을 격자 옆에 글로 보인다.
// 막힌 태그 칸은 비워 자리만 지킨다. 격자가 없으면(옛 팩) 한 줄 칩.
function gazeLayout({ palette, tags, on, shown, press, chipFor, autoChip }) {
  const grid = palette?.gaze_grid ?? [];
  const placed = new Set(grid.flatMap((col) => [col.looking, col.facing]).filter(Boolean));
  const rest = tags.filter((tag) => !placed.has(tag) && shown(tag)).map(chipFor);
  if (!grid.length) return [autoChip, ...rest];
  const table = el("div", "bmk-to-gaze");
  table.style.setProperty("--cols", String(grid.length));
  const corner = el("span", "bmk-to-gaze-corner");
  corner.append(autoChip);
  table.append(corner, ...grid.map((col) => el("span", "bmk-to-gaze-head col", col.label)));
  for (const [name, title] of GAZE_ROWS) {
    const head = el("span", "bmk-to-gaze-head row", name);
    head.title = title;
    table.append(head);
    for (const col of grid) {
      const tag = col[name];
      if (!tag || !tags.includes(tag) || !shown(tag)) {
        table.append(el("span", "bmk-to-gaze-empty"));
        continue;
      }
      const cell = chipButton("bmk-to-cell", on.includes(tag), `${tag} (${col.label})`, () => press(tag));
      cell.setAttribute("aria-label", tag);
      cell.append(glyph("gaze", tag, { size: CELL_GLYPH }) ?? tag);
      table.append(cell);
    }
  }
  return [table, el("span", "bmk-to-picked", on.filter(shown).join(", ")), ...rest];
}

// 포즈 묶음: 자세별로 칩을 모은다. 이름표는 첫 칩과 붙여 줄 끝에 혼자 남지 않게 한다.
// 묶음 안이 모두 막히면 묶음째 숨긴다. 묶음이 없으면(옛 팩) 한 줄 칩.
function poseLayout({ palette, tags, shown, chipFor, autoChip }) {
  const groups = palette?.pose_groups ?? [];
  const placed = new Set(groups.flatMap((g) => g.tags));
  const out = [autoChip];
  for (const g of groups) {
    const [first, ...chips] = g.tags.filter((tag) => tags.includes(tag) && shown(tag)).map(chipFor);
    if (!first) continue;
    const box = el("span", "bmk-to-grp");
    box.setAttribute("role", "group");
    box.setAttribute("aria-label", g.label);
    const head = el("span", "bmk-to-grp-head");
    const label = el("span", "bmk-to-grp-label", g.label);
    label.title = g.label;
    head.append(label, first);
    box.append(head, ...chips);
    out.push(box);
  }
  return [...out, ...tags.filter((tag) => !placed.has(tag) && shown(tag)).map(chipFor)];
}

// 칩 한 줄. spec: name(위젯 이름·퀵 그룹), label, title(palette, tag), toggle(palette, on, tag) → 새 선택,
// dropNote?(palette, dropped) → 칩이 꺼진 까닭, layout?(ctx) → 이름표와 알림 사이에 놓을 것(없으면 자동 + 칩 한 줄),
// unlisted?(tag) → 다른 곳(그림)이 맡아 칩을 두지 않을 태그, tail?() → 줄 끝에 둘 것
function chipRow(node, onChange, spec) {
  injectChipStyle();
  const widget = () => node.widgets?.find((w) => w.name === spec.name);
  const selected = () => splitItems(widget()?.value).filter((tag) => tag !== AUTO);
  const row = el("div", "bmk-to-chips");
  let palette = null;
  let blocked = [];
  let note = "";
  let noteTimer = 0;

  function set(tags, noteText = "") {
    const w = widget();
    const value = tags.join(", ");
    // 저장된 값과 뜻이 같으면(auto → 빈 값 등) 쓰지 않아 되돌리기 기록을 남기지 않는다
    if (w && selected().join(", ") !== value) {
      w.value = value;
      w.callback?.(value);
      onChange();
    }
    clearTimeout(noteTimer);
    note = noteText;
    if (note) {
      noteTimer = setTimeout(() => {
        note = "";
        render();
      }, NOTE_MS);
    }
    render();
  }

  function render() {
    const tags = palette?.quick?.[spec.name] ?? [];
    const linked = node.inputs?.find((i) => i.name === spec.name)?.link != null;
    row.hidden = !tags.length || !widget() || linked;
    const on = selected();
    const shown = (tag) => !blocked.includes(tag);
    const press = (tag) => {
      const next = spec.toggle(palette, on, tag);
      set(next, spec.dropNote?.(palette, on.filter((t) => t !== tag && !next.includes(t))));
    };
    const chipFor = (tag) => {
      const button = chipButton("tag", on.includes(tag), spec.title(palette, tag), () => press(tag));
      const icon = glyph(spec.name, tag, { size: GLYPH_SIZE[spec.name] ?? 16 });
      if (icon) button.append(icon);
      button.append(tag);
      return button;
    };
    // 고른 것이 모두 막혀도 프롬프트 그대로
    const autoChip = chipButton("", !on.some(shown), "프롬프트 그대로", () => set([]));
    autoChip.append("자동");
    const ctx = { palette, tags, on, shown, press, chipFor, autoChip };
    const listed = (tag) => shown(tag) && !spec.unlisted?.(tag);
    row.replaceChildren(
      el("span", "", spec.label),
      ...(spec.layout ? spec.layout(ctx) : [autoChip, ...tags.filter(listed).map(chipFor)]),
      ...(spec.tail?.() ?? []),
      ...(note ? [el("span", "note", note)] : []),
    );
  }

  return {
    row,
    render,
    set,
    setPalette(value) {
      palette = value;
      render();
    },
    setBlocked(value) {
      if (value.join() === blocked.join()) return;
      blocked = value;
      render();
    },
  };
}

function gazeChips(node, onChange) {
  return chipRow(node, onChange, {
    name: GAZE,
    label: "시선",
    title: (_palette, tag) => `${tag.startsWith("facing") ? "눈을 감거나 가렸을 때" : "눈이 보일 때"} — 프롬프트의 시선 태그를 고른 태그들로 바꿉니다. 여러 개 고를 수 있습니다`,
    toggle: gazeToggle,
    layout: gazeLayout,
  });
}

function poseChips(node, onChange) {
  return chipRow(node, onChange, {
    name: POSE,
    label: "포즈",
    title: () => "프롬프트의 자세 태그를 고른 태그들로 바꿉니다(장소·구속 태그는 남김). 여러 개 고를 수 있습니다",
    toggle: poseToggle,
    layout: poseLayout,
  });
}

// stage: 그림이 맡는 칩을 빼고(unlisted) 줄 끝에 그림 단추를 둔다(tail)
function viewChips(node, onChange, stage) {
  let lifted = false;
  const lifts = (palette) => palette.view_lift ?? [];
  const row = chipRow(node, onChange, {
    unlisted: (tag) => stage.covers(tag),
    tail: () => (stage.available() ? [stage.toggle] : []),
    name: VIEW,
    label: "시점",
    title: (palette, tag) => (lifts(palette).includes(tag)
      ? "부딪치는 시점도 함께 고를 수 있게 합니다(크롭·시점 규칙을 끔)"
      : "프롬프트의 시점 태그를 고른 태그들로 바꿉니다. 여러 개 고를 수 있습니다"),
    toggle: (palette, on, tag) => viewToggle(palette, on, tag, lifted),
    dropNote: (palette, dropped) => (dropped.length
      ? `${dropped.join(", ")}: 고른 시점과 부딪쳐 껐습니다. ${lifts(palette).join(", ")} 를 켜면 함께 고를 수 있습니다.`
      : ""),
  });
  return {
    ...row,
    setLifted(value) {
      lifted = value;
    },
  };
}

// ─── 퀵 세팅 그림 ───

// 크롭 틀·방향·높이 그림(QuickStage)을 노드 위젯에 잇는다. 그림은 값을 갖지 않고 render 때마다 위젯 값을 받아 그린다.
// setView(tags): 시점 위젯에 쓰기(시점 칩 줄의 set), onChange(): 위젯을 바꾼 뒤, onToggle(): 켜고 끈 뒤(노드 크기)
function stageControl(node, { setView, onChange, onToggle }) {
  injectChipStyle();
  const widget = (name) => node.widgets?.find((w) => w.name === name);
  const linked = (name) => node.inputs?.find((i) => i.name === name)?.link != null;
  const picked = (name) => splitItems(widget(name)?.value).filter((tag) => tag !== AUTO);
  const box = el("div", "bmk-to-stage");
  box.hidden = true;
  let stage = null;
  let covered = new Set();     // 다이얼이 맡는 시점 태그
  let present = new Set();     // 프롬프트에 원래 든 태그(덮어쓰기로 넣은 것 제외)
  let lifted = false;
  let on = false;

  const setFraming = (tag) => {
    const w = widget(FRAMING);
    const value = tag || AUTO;
    if (!w || w.value === value) return;
    w.value = value;
    w.callback?.(value);
    onChange();
  };
  // 끄는 동안은 onChange 가 되돌리기 기록을 건너뛰므로 손을 뗄 때 남긴다
  for (const type of ["pointerup", "pointercancel"]) box.addEventListener(type, () => checkState());
  // 그림이 쓰는 키는 ComfyUI 단축키로 넘기지 않는다(Delete·Backspace 가 선택한 노드를 지우지 않게)
  box.addEventListener("keydown", (e) => {
    if (STAGE_KEYS.has(e.key) && e.target.closest?.(".qs-svg")) e.stopPropagation();
  });

  const toggle = chipButton("bmk-to-stage-btn", false,
    "크롭과 시점의 방향·높이를 그림으로 고릅니다. 켜면 다이얼이 맡는 시점 칩은 숨깁니다", () => ctl.setOn(!on));
  toggle.append("그림");

  const ctl = {
    box,
    toggle,
    available: () => Boolean(stage),
    // 그림이 보이는가: 단추가 있는 시점 줄이 숨으면(view 연결) 그림도 숨긴다
    shown: () => Boolean(stage) && on && Boolean(widget(VIEW)) && !linked(VIEW),
    covers: (tag) => ctl.shown() && covered.has(tag),
    dragging: () => Boolean(stage?.drag),
    setPalette(palette) {
      const covers = stageCovers(palette ?? {});
      covered = new Set(covers.view);
      // 옛 팩처럼 그림이 맡는 태그가 없으면 그림 단추를 두지 않는다
      stage = covers.framing.length || covers.view.length
        ? new QuickStage({ palette, onFraming: setFraming, onView: (tags) => setView(tags) })
        : null;
      box.replaceChildren(...(stage ? [stage.root] : []));
      ctl.render();
      if (on) onToggle();   // 켠 채 저장한 워크플로는 팩을 받은 뒤에야 그림이 생긴다
    },
    setResult(data) {
      present = new Set((data?.tokens ?? []).filter((t) => t.kind === "tag" && t.key && !t.injected).map((t) => t.key));
      lifted = Boolean(data?.view_lifted);
      ctl.render();
    },
    // save=false: 워크플로를 읽을 때(properties 에서 온 값)
    setOn(value, save = true) {
      on = Boolean(value);
      if (save) {
        node.properties ??= {};
        node.properties[STAGE_PROP] = on;
      }
      ctl.render();
      onToggle();
    },
    render() {
      toggle.setAttribute("aria-pressed", String(on));
      box.hidden = !ctl.shown();
      if (box.hidden) return;
      const crop = stage.root.querySelector(".qs-crop");
      const cropOff = !widget(FRAMING) || linked(FRAMING);
      crop?.classList.toggle("bmk-to-off", cropOff);
      if (crop) crop.title = cropOff ? "framing 입력이 연결돼 있어 그림으로 고를 수 없습니다" : "";
      const framing = widget(FRAMING)?.value;
      stage.update({
        framing: typeof framing === "string" && framing !== AUTO ? framing : "",
        view: picked(VIEW),
        pose: picked(POSE),
        gaze: picked(GAZE),
        present,
        lifted,
      });
    },
  };
  return ctl;
}

// ─── 편집기 ↔ 노드 ───

function nodeHost(node) {
  const widget = (name) => node.widgets?.find((w) => w.name === name);
  const linked = (name) => node.inputs?.find((i) => i.name === name)?.link != null;
  const stage = stageControl(node, {
    setView: (tags) => view.set(tags),
    onChange: () => host.changed(),
    onToggle: () => {
      view.render();
      // 켜서 편집기 최소 높이가 늘면 노드를 늘린다(줄이지는 않는다)
      const sz = node.computeSize?.();
      if (sz && node.size[1] < sz[1]) node.setSize([node.size[0], sz[1]]);
      node.setDirtyCanvas?.(true, true);
    },
  });
  const view = viewChips(node, () => host.changed(), stage);
  const gaze = gazeChips(node, () => host.changed());
  const pose = poseChips(node, () => host.changed());
  const quick = el("div", "bmk-to-quick");
  quick.append(stage.box, view.row);
  let previewSeq = 0;
  const host = {
    view,
    gaze,
    pose,
    stage,
    quick,
    lastInput: null,      // 마지막 실행의 입력(text 를 연결했을 때)
    split: node.properties?.bmk_to_split,
    canReset: true,
    canReload: true,
    arrValue: () => widget(ARR)?.value,
    setArrValue(value) {
      const w = widget(ARR);
      if (w && w.value !== value) {
        w.value = value;
        w.callback?.(value);
      }
    },
    layoutId: () => widget("layout")?.value,
    values() {
      const out = {};
      for (const key of OPTION_KEYS) {
        const w = widget(key);
        if (w) out[key] = w.value;
      }
      return out;
    },
    inputText() {
      if (linked("text")) return host.lastInput;
      const v = widget("text")?.value;
      return typeof v === "string" ? v : "";
    },
    locked: () => linked(ARR),
    loadPalette: (force) => loadPalette(force).then((data) => {
      stage.setPalette(data.palette);
      view.setPalette(data.palette);
      gaze.setPalette(data.palette);
      pose.setPalette(data.palette);
      return data;
    }),
    async preview(body) {
      const seq = ++previewSeq;
      const data = await postPreview(body);
      if (seq === previewSeq) {
        view.setLifted(Boolean(data.view_lifted));
        gaze.setBlocked(data.gaze_blocked ?? []);
        pose.setBlocked(data.pose_blocked ?? []);
        stage.setResult(data);
      }
      return data;
    },
    validate: validateArrangement,
    renderState(box, { model, fromLayout, locked, layoutLabel }) {
      box.replaceChildren();
      if (locked) {
        box.append("arrangement 입력 연결됨");
      } else if (fromLayout) {
        box.append("레이아웃 ", el("b", "", layoutLabel), " 에서 시작 — 바꾸면 배열로 저장");
      } else {
        box.append("배열 ", el("b", "", model?.label || "(이름 없음)"));
      }
      box.title = fromLayout
        ? "arrangement 가 비어 있어 노드는 layout 을 씁니다. 청크를 바꾸면 그 배열이 arrangement 에 저장됩니다."
        : "arrangement 위젯의 배열 세팅을 씁니다(layout 은 쓰지 않음). ⋯ → 레이아웃으로 되돌리기";
    },
    changed() {
      node.setDirtyCanvas?.(true, true);
      if (!stage.dragging()) checkState();
      stage.render();
    },
    layoutChanged: () => node.setDirtyCanvas?.(true, true),
    previewNote: () => (linked("text") && host.lastInput == null
      ? "text 가 연결돼 있습니다 — 한 번 실행하면 그 입력으로 미리 봅니다."
      : ""),
    saveSplit(value) {
      node.properties ??= {};
      node.properties.bmk_to_split = value;
    },
    wheel: (e) => app.canvas?.processMouseWheel?.(e),
  };
  return host;
}

app.registerExtension({
  name: "BMK.TagOrganizer",

  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData.name !== NODE_ID) return;

    const onNodeCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const r = onNodeCreated?.apply(this, arguments);
      for (const w of this.widgets ?? []) {
        if (w.name === ARR || w.name === VIEW || w.name === GAZE || w.name === POSE) w.hidden = true; // 입력 옵션 hidden 을 모르는 프론트에서도 숨긴다
      }
      const editor = new ArrangementEditor(nodeHost(this));
      editor.root.prepend(editor.host.quick, editor.host.gaze.row, editor.host.pose.row);
      this.__bmkTagArr = editor;
      const widget = this.addDOMWidget("arrangement_editor", EDITOR_TYPE, editor.root, {
        serialize: false,
        hideOnZoom: true,
        margin: MARGIN,
        getValue: () => "",
        setValue: () => {},
        getMinHeight: () => EDITOR_MIN_H + (editor.host.stage.shown() ? STAGE_MIN_H : 0),
      });
      widget.serialize = false;
      if (widget.options) widget.options.serialize = false;
      const sz = this.computeSize?.() ?? this.size;
      this.setSize([Math.max(sz[0], this.size[0], MIN_WIDTH), Math.max(sz[1], this.size[1] + ADD_HEIGHT)]);
      return r;
    };

    const onConfigure = nodeType.prototype.onConfigure;
    nodeType.prototype.onConfigure = function () {
      const r = onConfigure?.apply(this, arguments);
      const editor = this.__bmkTagArr;
      if (editor) {
        editor.split = Number(this.properties?.bmk_to_split) || editor.split;
        editor.applySplit();
        editor.syncFromWidget(true);
        editor.host.stage.setOn(this.properties?.[STAGE_PROP], false);   // 시점 줄도 다시 그린다
        editor.host.gaze.render();
        editor.host.pose.render();
      }
      // v1 워크플로의 노드는 편집기 자리가 없으므로 최소 크기까지 늘린다
      const sz = this.computeSize?.();
      if (sz && (this.size[0] < MIN_WIDTH || this.size[1] < sz[1])) {
        this.setSize([Math.max(this.size[0], MIN_WIDTH), Math.max(this.size[1], sz[1])]);
      }
      return r;
    };

    // 위젯 입력을 잇거나 끊으면 칩 줄·그림을 바로 다시 그린다(연결된 위젯은 칩·그림으로 고르지 않는다)
    const onConnectionsChange = nodeType.prototype.onConnectionsChange;
    nodeType.prototype.onConnectionsChange = function () {
      const r = onConnectionsChange?.apply(this, arguments);
      const host = this.__bmkTagArr?.host;
      if (host) {
        host.view.render();
        host.gaze.render();
        host.pose.render();
        host.stage.render();
      }
      return r;
    };

    const onExecuted = nodeType.prototype.onExecuted;
    nodeType.prototype.onExecuted = function (message) {
      onExecuted?.apply(this, arguments);
      const text = message?.bmk_tag_input?.[0];
      const editor = this.__bmkTagArr;
      if (editor && typeof text === "string") {
        editor.host.lastInput = text;
        editor.lastSig = ""; // 다음 tick 에 미리보기
      }
    };

    const onRemoved = nodeType.prototype.onRemoved;
    nodeType.prototype.onRemoved = function () {
      this.__bmkTagArr?.destroy();
      return onRemoved?.apply(this, arguments);
    };
  },
});
