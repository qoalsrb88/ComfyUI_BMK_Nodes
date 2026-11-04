// bmk_tag_organizer.js — BMKTagOrganizer 짝 JS 확장 (v8)
//
// 배열 세팅(arrangement) 청크 편집기를 노드에 붙이는 ComfyUI 어댑터. 편집기 자체는 TagOrganizer 저장소의
// extension/editor/ 가 정본이고(크롬 확장 패널과 같은 코드), js/tag_organizer/ 는 그 저장소의
// `python tagorg.py sync` 가 복사한 파일이다. 여기서 직접 고치면 다음 동기화가 멈춘다. 조작은 editor.js 머리 주석 참고.
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
//
// 상태는 ArrangementEditor·host 객체에 두고 위젯에는 자체 필드를 얹지 않는다(프론트 1.53 이 위젯을
// BaseWidget 으로 흡수하며 충돌할 수 있음 — bmk_sam3_mask_layers.js 와 같은 이유).

import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { ArrangementEditor, el } from "./tag_organizer/editor.js";
import { gazeToggle, poseToggle, splitItems, viewToggle } from "./tag_organizer/model.js";

const NODE_ID = "BMKTagOrganizer";
const ARR = "arrangement";
const GAZE = "gaze";
const VIEW = "view";
const POSE = "pose";
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
const ADD_HEIGHT = 600;      // 처음 만들 때 편집기 몫으로 늘리는 높이
const EDITOR_MIN_H = 480;
const MARGIN = 6;
const NOTE_MS = 8000;        // 칩이 꺼진 까닭을 보이는 시간
const CHIP_STYLE = `
.bmk-to-chips{flex:0 0 auto;display:flex;flex-wrap:wrap;align-items:center;gap:3px}
.bmk-to-chips[hidden]{display:none}
.bmk-to-chips>span{margin-right:3px;color:var(--descrip-text,#999)}
.bmk-to-chips>span.note{flex:1 1 100%;color:#d4a017}
.bmk-to-chips button{height:18px;padding:0 7px;border:1px solid var(--border-color,#4e4e4e);border-radius:9px;background:var(--comfy-input-bg,#222);color:var(--input-text,#ddd);font:inherit;font-size:11px;line-height:16px;cursor:pointer}
.bmk-to-chips button:hover{border-color:var(--input-text,#ddd)}
.bmk-to-chips button[aria-pressed=true]{border-color:var(--p-primary-color,#5b8def);background:var(--p-primary-color,#5b8def);color:#fff}
.bmk-to-chips button.tag[aria-pressed=true]::before{content:"✓ "}
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
  const style = el("style", "", CHIP_STYLE);
  style.id = "bmk-to-chips-style";
  document.head.appendChild(style);
}

// 칩 한 줄. spec: name(위젯 이름·퀵 그룹), label, title(palette, tag), toggle(palette, on, tag) → 새 선택,
// dropNote?(palette, dropped) → 칩이 꺼진 까닭
function chipRow(node, onChange, spec) {
  injectChipStyle();
  const widget = () => node.widgets?.find((w) => w.name === spec.name);
  const selected = () => splitItems(widget()?.value).filter((tag) => tag !== AUTO);
  const row = el("div", "bmk-to-chips");
  let palette = null;
  let blocked = [];
  let note = "";
  let noteTimer = 0;

  function chip(label, cls, pressed, title, onClick) {
    const button = el("button", cls, label);
    button.type = "button";
    button.title = title;
    button.setAttribute("aria-pressed", String(pressed));
    button.addEventListener("click", onClick);
    return button;
  }

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
    row.replaceChildren(
      el("span", "", spec.label),
      chip("자동", "", !on.some((tag) => !blocked.includes(tag)), "프롬프트 그대로", () => set([])), // 고른 것이 모두 막혀도 프롬프트 그대로
      ...tags.filter((tag) => !blocked.includes(tag)).map((tag) => chip(tag, "tag", on.includes(tag), spec.title(palette, tag), () => {
        const next = spec.toggle(palette, on, tag);
        set(next, spec.dropNote?.(palette, on.filter((t) => t !== tag && !next.includes(t))));
      })),
      ...(note ? [el("span", "note", note)] : []),
    );
  }

  return {
    row,
    render,
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
  });
}

function poseChips(node, onChange) {
  return chipRow(node, onChange, {
    name: POSE,
    label: "포즈",
    title: () => "프롬프트의 자세 태그를 고른 태그들로 바꿉니다(장소·구속 태그는 남김). 여러 개 고를 수 있습니다",
    toggle: poseToggle,
  });
}

function viewChips(node, onChange) {
  let lifted = false;
  const lifts = (palette) => palette.view_lift ?? [];
  const row = chipRow(node, onChange, {
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

// ─── 편집기 ↔ 노드 ───

function nodeHost(node) {
  const widget = (name) => node.widgets?.find((w) => w.name === name);
  const linked = (name) => node.inputs?.find((i) => i.name === name)?.link != null;
  const view = viewChips(node, () => host.changed());
  const gaze = gazeChips(node, () => host.changed());
  const pose = poseChips(node, () => host.changed());
  let previewSeq = 0;
  const host = {
    view,
    gaze,
    pose,
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
      checkState();
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
      editor.root.prepend(editor.host.view.row, editor.host.gaze.row, editor.host.pose.row);
      this.__bmkTagArr = editor;
      const widget = this.addDOMWidget("arrangement_editor", EDITOR_TYPE, editor.root, {
        serialize: false,
        hideOnZoom: true,
        margin: MARGIN,
        getValue: () => "",
        setValue: () => {},
        getMinHeight: () => EDITOR_MIN_H,
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
        editor.host.view.render();
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
