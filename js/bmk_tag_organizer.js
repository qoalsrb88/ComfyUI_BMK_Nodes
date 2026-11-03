// bmk_tag_organizer.js — BMKTagOrganizer 짝 JS 확장 (v4)
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
//
// 상태는 ArrangementEditor·host 객체에 두고 위젯에는 자체 필드를 얹지 않는다(프론트 1.53 이 위젯을
// BaseWidget 으로 흡수하며 충돌할 수 있음 — bmk_sam3_mask_layers.js 와 같은 이유).

import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { ArrangementEditor, el } from "./tag_organizer/editor.js";

const NODE_ID = "BMKTagOrganizer";
const ARR = "arrangement";
const EDITOR_TYPE = "BMK_TAG_ARRANGEMENT";
const PALETTE_URL = "/bmk/tag_organizer/palette";
const PREVIEW_URL = "/bmk/tag_organizer/preview";
const OPTION_KEYS = [
  "preset", "exclude", "max_rating", "rules", "layout", "input_dialect", "output_dialect",
  "line_per_section", "trailing_comma", "dedupe", "framing", "view", "guess",
  "dialect_priority", "comments", "sentences",
];
const MIN_WIDTH = 560;
const ADD_HEIGHT = 420;      // 처음 만들 때 편집기 몫으로 늘리는 높이
const EDITOR_MIN_H = 300;
const MARGIN = 6;

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

// ─── 편집기 ↔ 노드 ───

function nodeHost(node) {
  const widget = (name) => node.widgets?.find((w) => w.name === name);
  const linked = (name) => node.inputs?.find((i) => i.name === name)?.link != null;
  const host = {
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
    loadPalette,
    preview: postPreview,
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
      const arr = this.widgets?.find((w) => w.name === ARR);
      if (arr) arr.hidden = true; // 입력 옵션 hidden 을 모르는 프론트에서도 숨긴다
      const editor = new ArrangementEditor(nodeHost(this));
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
