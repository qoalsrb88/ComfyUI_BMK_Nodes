// bmk_save_meta.js — BMKSaveMeta refs 행 편집기
//
// refs 텍스트만 저장한다. 원문·주석·빈 줄·줄바꿈은 고친 행 외에는 그대로 보존하고,
// DOM 편집기와 미리보기/실행 결과는 직렬화하지 않는다. 텍스트 모드도 원래 위젯을 쓴다.
// ◎/KSampler 선택은 현재 그래프의 로컬 ID를 사용하며 임시 리스너를 끝날 때 해제한다.
// 미리보기는 로컬 resolve API에만 요청하고, 변경 전 응답은 버린다.

import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const NODE_ID = "BMKSaveMeta";
const KEYS = ["ksampler", "positive", "negative", "model", "loras", "te", "vae",
  "seed", "steps", "cfg", "sampler", "scheduler", "denoise"];
const RESERVED = new Set(["time", "width", "height", "postfix"]);
const ALIASES = { prompt: "positive", lora: "loras", text_encoder: "te" };
const ID_ONLY = /^\p{Decimal_Number}+(?::\p{Decimal_Number}+)*$/u;
const ID_SPEC = /^(\p{Decimal_Number}+(?::\p{Decimal_Number}+)*)(?:\.(.+))?$/u;
const LINE_END = /\r\n|[\n\r\v\f\x1c-\x1e\u0085\u2028\u2029]/g;
const MARGIN = 6;
const editors = new WeakMap();
let nextEditorId = 0;
let activePicker = null;

const STYLE = `
.bmk-svm-root{height:100%;box-sizing:border-box;padding:4px;display:flex;flex-direction:column;gap:4px;color:var(--input-text,#ddd);font:12px/1.4 sans-serif;overflow:auto}
.bmk-svm-root [hidden]{display:none!important}
.bmk-svm-head{display:flex;gap:4px;align-items:center;min-height:28px;flex-shrink:0;white-space:nowrap}
.bmk-svm-root button,.bmk-svm-root input,.bmk-svm-root select{box-sizing:border-box;min-width:0;height:24px;border:1px solid var(--border-color,#555);border-radius:4px;background:var(--comfy-input-bg,#252525);color:inherit;font:inherit;padding:2px 4px}
.bmk-svm-root button{cursor:pointer;flex-shrink:0}
.bmk-svm-root button:hover{border-color:var(--p-primary-color,#79aaff)}
.bmk-svm-root input{width:100%}
.bmk-svm-status{margin-left:auto;min-width:48px;max-width:240px;overflow:hidden;text-overflow:ellipsis;color:var(--descrip-text,#aaa)}
.bmk-svm-help{height:18px;flex-shrink:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--descrip-text,#aaa)}
.bmk-svm-list{display:flex;flex-direction:column;gap:4px}
.bmk-svm-row{padding:4px;display:flex;flex-direction:column;gap:3px;border:1px solid var(--border-color,#555);border-radius:5px;box-sizing:border-box}
.bmk-svm-controls{display:flex;align-items:center;gap:4px;min-height:24px}
.bmk-svm-key{flex:0 1 104px;width:104px!important}
.bmk-svm-specs{display:flex;flex:1;min-width:0;flex-direction:column;gap:3px}
.bmk-svm-spec{display:flex;gap:3px;min-width:0}
.bmk-svm-id{flex:1 1 62px;width:62px!important}
.bmk-svm-name{flex:1 1 86px;width:86px}
.bmk-svm-value{flex:1;min-width:0}
.bmk-svm-kind{flex:0 0 34px;color:var(--descrip-text,#aaa)}
.bmk-svm-results{height:18px;display:flex;gap:8px;min-width:0}
.bmk-svm-preview,.bmk-svm-executed{flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.bmk-svm-executed{color:#9ad6ae}
.bmk-svm-stale{opacity:.45}
.bmk-svm-warning{border-color:#b87949;color:#efbb82}
.bmk-svm-error{color:#f28d8d}
.bmk-svm-muted{color:var(--descrip-text,#aaa)}
.bmk-svm-selected{outline:2px solid var(--p-primary-color,#79aaff)}
.bmk-svm-picking,.bmk-svm-picking *{cursor:crosshair!important}
.bmk-svm-pick-hint{position:fixed;top:64px;left:50%;transform:translateX(-50%);z-index:10000;padding:8px 16px;border:1px solid #79aaff;border-radius:6px;background:#202938;color:#fff;font:13px sans-serif;pointer-events:none}
`;

function injectStyle() {
  if (document.getElementById("bmk-svm-style")) return;
  const style = document.createElement("style");
  style.id = "bmk-svm-style";
  style.textContent = STYLE;
  document.head.appendChild(style);
}

function element(tag, className, text) {
  const el = document.createElement(tag);
  el.className = className;
  if (text != null) el.textContent = text;
  return el;
}

function button(text, title, action) {
  const el = element("button", "bmk-svm-button", text);
  el.type = "button";
  el.title = title;
  el.setAttribute("aria-label", title);
  el.addEventListener("click", action);
  return el;
}

function input(value, className, title, action) {
  const el = element("input", className);
  el.type = "text";
  el.spellcheck = false;
  el.value = value;
  el.title = title;
  el.setAttribute("aria-label", title);
  el.addEventListener("input", () => action(el.value));
  return el;
}

function canonicalKey(label) {
  const lower = label.toLowerCase();
  const key = Object.hasOwn(ALIASES, lower) ? ALIASES[lower] : lower;
  return KEYS.includes(key) ? key : label;
}

// Python str.strip()의 공백 집합 (JS trim은 BOM을 지우고 U+0085는 남긴다).
function strip(text) {
  return text.replace(/^[\p{White_Space}\u001c-\u001f]+|[\p{White_Space}\u001c-\u001f]+$/gu, "");
}

// Python _parse_refs/_parse_specs와 동일하게 리터럴을 먼저 검사한다.
// 따옴표 안의 #, 쉼표, 백슬래시는 이스케이프하거나 분해하지 않는다.
function parseLine(raw, eol = "") {
  const row = { raw, eol, kind: "raw", key: null };
  const line = strip(raw);
  if (!line) return { ...row, kind: "blank" };
  if (line.startsWith("#")) return { ...row, kind: "comment" };
  const colon = line.indexOf(":");
  if (colon < 0) return row;
  const label = strip(line.slice(0, colon));
  const body = strip(line.slice(colon + 1));
  const key = canonicalKey(label);
  if (!label || !body || RESERVED.has(key.toLowerCase())) return { ...row, key };
  const literal = body.match(/^"(.*)"[\p{White_Space}\u001c-\u001f]*(?:#.*)?$/u);
  if (literal) {
    return { ...row, kind: "literal", label, key, literal: literal[1],
      comment: body.slice(literal[1].length + 2) };
  }
  const hash = body.indexOf("#");
  const specText = hash < 0 ? body : strip(body.slice(0, hash));
  const parts = key === "loras" ? specText.split(",") : [specText];
  const specs = parts.map((part) => {
    const match = strip(part).match(ID_SPEC);
    return match ? { id: match[1], name: match[2] || "" } : null;
  });
  if (specs.some((spec) => !spec)) return { ...row, key };
  return { ...row, kind: "ref", label, key, specs,
    comment: hash < 0 ? "" : ` ${body.slice(hash)}` };
}

function parseText(text) {
  const rows = [];
  let start = 0;
  for (const match of text.matchAll(LINE_END)) {
    rows.push(parseLine(text.slice(start, match.index), match[0]));
    start = match.index + match[0].length;
  }
  if (start < text.length) rows.push(parseLine(text.slice(start)));
  return rows;
}

function serializeRows(rows) {
  return rows.map((row) => row.raw + row.eol).join("");
}

function graphNodes(graph) {
  return graph?._nodes || [];
}

function nodeInGraph(graph, id) {
  return graph?.getNodeById?.(id) || graphNodes(graph).find((n) => String(n.id) === id) || null;
}

function nodeAtPath(graph, id) {
  const parts = id.split(":");
  let found = null;
  for (let i = 0; i < parts.length; i++) {
    found = nodeInGraph(graph, parts[i]);
    if (!found) return null;
    if (i < parts.length - 1) {
      if (!found.isSubgraphNode?.()) return null;
      graph = found.subgraph;
    }
  }
  return found;
}

function referencedNode(node, id) {
  if (!ID_ONLY.test(id)) return null;
  return nodeAtPath(node.graph, id) || nodeAtPath(app.graph, id);
}

// 비공개 실행 ID 헬퍼를 import하지 않고 루트에서 소속 그래프를 찾는다.
function executionId(node, root = app.graph) {
  function visit(graph, path, ancestors) {
    if (!graph || ancestors.has(graph)) return null;
    if (graph === node.graph) return [...path, String(node.id)].join(":");
    const seen = new Set(ancestors).add(graph);
    for (const child of graphNodes(graph)) {
      if (!child.isSubgraphNode?.() || !child.subgraph) continue;
      const id = visit(child.subgraph, [...path, String(child.id)], seen);
      if (id != null) return id;
    }
    return null;
  }
  return visit(root, [], new Set());
}

function valueText(value) {
  if (typeof value === "string") return value;
  if (value == null) return String(value);
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}

function previewText(result) {
  if (!result) return "";
  if (result.status === "missing") return "못 찾음 — 노드 ID·이름 확인";
  if (result.status === "wired") return "유선 연결이 우선";
  if (result.status === "invalid") return "문법 오류";
  const groups = new Map();
  for (const field of Array.isArray(result.fields) ? result.fields : []) {
    if (!field || typeof field !== "object") continue;
    const key = String(field.key);
    const part = field.part && field.part !== "value" ? `${field.part} ` : "";
    let text;
    if (field.kind === "link") {
      const source = field.title || field.class_type || "";
      text = `${part}← #${field.node} ${source}${field.output != null ? `.${field.output}` : ""} (실행 때 결정)`;
    } else {
      text = `${part}= ${valueText(field.value)}`;
    }
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(text);
  }
  return [...groups].map(([key, parts]) => `${key} ${parts.join(" / ")}`).join(" · ") || "해석된 값 없음";
}

class RefsEditor {
  constructor(node, refs) {
    this.node = node;
    this.refs = refs;
    this.rows = [];
    this.views = new Map();
    this.nameSignatures = new WeakMap();
    this.text = null;
    this.textMode = false;
    this.disposed = false;
    this.writing = false;
    this.revision = 0;
    this.timer = null;
    this.controller = null;
    this.minWidth = node.size?.[0] || 300;
    this.originalHidden = refs.hidden;
    this.originalCallback = refs.callback;
    this.listId = `bmk-svm-keys-${++nextEditorId}`;
    this.build();
    this.syncFromWidget(false);
    refs.hidden = true;
    this.widget = node.addDOMWidget("bmk_svm_editor", "BMK_SAVE_META_EDITOR", this.root, {
      serialize: false,
      getValue: () => "",
      getMinHeight: () => this.widgetHeight(),
      getMaxHeight: () => this.widgetHeight(),
      hideOnZoom: true,
      margin: MARGIN,
    });
    this.widget.serialize = false;
    // refs 자리에 그린다. 직렬화하지 않는 위젯이라 순서를 바꿔도 widgets_values 위치는 그대로다.
    const widgets = node.widgets;
    widgets.splice(widgets.indexOf(this.widget), 1);
    widgets.splice(widgets.indexOf(refs) + 1, 0, this.widget);
    const editor = this;
    this.refsCallback = function () {
      const result = editor.originalCallback?.apply(this, arguments);
      if (!editor.writing && !editor.disposed) editor.syncFromWidget();
      return result;
    };
    refs.callback = this.refsCallback;
    this.textarea = refs.inputEl || refs.element?.querySelector?.("textarea") || refs.element;
    this.onTextInput = () => queueMicrotask(() => {
      if (!this.disposed) this.syncFromWidget();
    });
    this.textarea?.addEventListener?.("input", this.onTextInput);
    this.ensureSize();
  }

  build() {
    this.root = element("div", "bmk-svm-root");
    // 편집기 안의 입력이 캔버스 단축키/드래그로 전달되지 않도록 한다.
    for (const type of ["pointerdown", "mousedown", "keydown", "keyup", "wheel"]) {
      this.root.addEventListener(type, (event) => event.stopPropagation());
    }
    const head = element("div", "bmk-svm-head");
    this.addButton = button("＋ 행", "참조 행 추가", () => this.addRow());
    this.samplerButton = button("KSampler로 채우기", "캔버스에서 KSampler 선택", () => this.startPick(null, null, this.samplerButton));
    this.toggleButton = button("텍스트", "원래 refs 텍스트 편집기로 전환", () => this.toggleMode());
    this.status = element("span", "bmk-svm-status", "미리보기 대기");
    this.status.setAttribute("role", "status");
    head.append(this.addButton, this.samplerButton, this.toggleButton,
      button("↻ 미리보기", "현재 refs 미리보기 새로고침", () => this.schedulePreview(0)), this.status);
    this.help = element("div", "bmk-svm-help", "키 직접 입력 가능 · time·width·height·postfix는 예약어");
    this.help.title = this.help.textContent;
    this.list = element("div", "bmk-svm-list");
    const choices = element("datalist", "bmk-svm-keys");
    choices.id = this.listId;
    for (const key of KEYS) {
      const option = document.createElement("option");
      option.value = key;
      choices.appendChild(option);
    }
    this.root.append(head, this.help, this.list, choices);
  }

  widgetHeight() {
    const rowsHeight = this.textMode ? 0 : this.rows.reduce((sum, row) =>
      sum + 62 + Math.max(0, (row.specs?.length || 1) - 1) * 27, 0);
    return 64 + rowsHeight + MARGIN * 2;
  }

  ensureSize(before) {
    if (!this.widget || !this.node.size) return;
    const minimum = this.node.computeSize?.() || this.node.size;
    const height = Math.max(minimum[1], this.node.size[1] + (before == null ? 0 : minimum[1] - before));
    const width = Math.max(this.minWidth, this.node.size[0], minimum[0]);
    if (height !== this.node.size[1] || width !== this.node.size[0]) this.node.setSize?.([width, height]);
  }

  syncFromWidget(preview = true) {
    if (this.disposed) return false;
    const text = typeof this.refs.value === "string" ? this.refs.value : "";
    if (text === this.text) return false;
    const before = this.widget ? this.node.computeSize?.()[1] : undefined;
    const oldRows = this.rows;
    const byRaw = new Map();
    for (const row of oldRows) {
      if (!byRaw.has(row.raw)) byRaw.set(row.raw, []);
      byRaw.get(row.raw).push(row);
    }
    this.cancelPick();
    this.rows = parseText(text).map((row, index) => {
      const old = byRaw.get(row.raw)?.shift();
      if (old) row.executed = old.executed;
      else if (row.key && row.key === oldRows[index]?.key && oldRows[index]?.executed) {
        row.executed = { ...oldRows[index].executed, stale: true };
      }
      return row;
    });
    this.text = text;
    this.render();
    this.ensureSize(before);
    if (preview) this.schedulePreview();
    return true;
  }

  toggleMode() {
    this.cancelPick();
    this.syncFromWidget();
    const before = this.node.computeSize?.()[1];
    this.textMode = !this.textMode;
    this.refs.hidden = this.textMode ? this.originalHidden : true;
    this.list.hidden = this.textMode;
    this.toggleButton.textContent = this.textMode ? "행 편집" : "텍스트";
    this.toggleButton.title = this.textMode ? "refs를 다시 읽어 행 편집기로 전환" : "원래 refs 텍스트 편집기로 전환";
    this.ensureSize(before);
    this.node.setDirtyCanvas?.(true, true);
  }

  setStatus(text, failed = false) {
    this.status.textContent = text;
    this.status.title = text;
    this.status.classList.toggle("bmk-svm-error", failed);
  }

  commit() {
    const text = serializeRows(this.rows);
    if (text === this.refs.value) return;
    this.text = text;
    this.writing = true;
    try {
      this.refs.value = text;
      this.refs.callback?.(text);
    } finally {
      this.writing = false;
    }
    this.node.setDirtyCanvas?.(true, true);
    try {
      app.extensionManager?.workflow?.activeWorkflow?.changeTracker?.checkState?.();
    } catch { /* undo 기록은 선택 사항 */ }
    this.schedulePreview();
  }

  editRow(row) {
    const body = row.kind === "literal" ? `"${row.literal}"` : row.specs
      .map((spec) => spec.id + (spec.name ? `.${spec.name}` : "")).join(", ");
    const raw = `${row.label}: ${body}${row.comment || ""}`;
    if (raw === row.raw) return;
    row.raw = raw;
    row.key = canonicalKey(strip(row.label));
    if (row.executed) row.executed.stale = true;
    this.commit();
    this.refreshResults();
  }

  appendRow(row) {
    const last = this.rows[this.rows.length - 1];
    if (last && !last.eol) last.eol = this.rows.find((r) => r.eol)?.eol || "\n";
    this.rows.push(row);
  }

  addRow() {
    this.syncFromWidget();
    if (this.textMode) this.toggleMode();
    const before = this.node.computeSize?.()[1];
    const row = { raw: "seed: ", eol: "", kind: "ref", label: "seed", key: "seed",
      specs: [{ id: "", name: "" }], comment: "" };
    this.appendRow(row);
    this.render();
    this.ensureSize(before);
    this.commit();
    this.views.get(row)?.root.querySelector(".bmk-svm-id")?.focus();
  }

  removeRow(row) {
    this.cancelPick();
    const before = this.node.computeSize?.()[1];
    this.rows = this.rows.filter((item) => item !== row);
    this.render();
    this.ensureSize(before);
    this.commit();
  }

  render() {
    this.cancelPick();
    this.views.clear();
    this.list.replaceChildren(...this.rows.map((row) => this.renderRow(row)));
    this.list.hidden = this.textMode;
    this.refreshResults();
  }

  renderRow(row) {
    const root = element("div", "bmk-svm-row");
    const controls = element("div", "bmk-svm-controls");
    const view = { root, specs: [] };
    this.views.set(row, view);
    if (row.kind === "ref" || row.kind === "literal") {
      const key = input(row.label, "bmk-svm-key", "표준 키 또는 커스텀 키", (value) => {
        if (value === row.label) return;
        row.label = value;
        this.editRow(row);
      });
      key.setAttribute("list", this.listId);
      controls.appendChild(key);
      if (row.kind === "literal") {
        controls.appendChild(input(row.literal, "bmk-svm-value", "리터럴 값 (따옴표 안의 내용)", (value) => {
          if (value === row.literal) return;
          row.literal = value;
          this.editRow(row);
        }));
      } else {
        const specs = element("div", "bmk-svm-specs");
        for (const spec of row.specs) specs.appendChild(this.renderSpec(row, spec, view));
        controls.appendChild(specs);
        view.addSpec = button("＋", "LoRA 참조 추가", () => {
          this.cancelPick();
          const before = this.node.computeSize?.()[1];
          row.specs.push({ id: "", name: "" });
          this.editRow(row);
          this.render();
          this.ensureSize(before);
        });
        view.addSpec.hidden = row.key !== "loras";
        controls.appendChild(view.addSpec);
      }
    } else {
      const label = row.kind === "comment" ? "주석" : row.kind === "blank" ? "빈 줄" : "원문";
      controls.appendChild(element("span", "bmk-svm-kind", label));
      const raw = input(row.raw, "bmk-svm-value", "원문 그대로 보존 (텍스트로 수정 가능)", (value) => {
        if (value === row.raw) return;
        row.raw = value;
        if (row.executed) row.executed.stale = true;
        this.commit();
        this.refreshResults();
      });
      // 원문 입력 도중에는 DOM을 교체하지 않는다 (blur 직후 버튼 클릭 보존).
      controls.appendChild(raw);
    }
    controls.appendChild(button("×", "이 행 삭제", () => this.removeRow(row)));
    const results = element("div", "bmk-svm-results");
    view.preview = element("span", "bmk-svm-preview");
    view.executed = element("span", "bmk-svm-executed");
    results.append(view.preview, view.executed);
    root.append(controls, results);
    return root;
  }

  renderSpec(row, spec, view) {
    const root = element("div", "bmk-svm-spec");
    const id = input(spec.id, "bmk-svm-id", "노드 ID (예: 34 또는 61:66)", (value) => {
      if (value === spec.id) return;
      spec.id = value;
      this.editRow(row);
    });
    id.placeholder = "#노드ID";
    const pick = button("◎", "다음에 클릭한 노드의 ID 사용 (Esc로 취소)", () => this.startPick(row, spec, pick));
    const names = element("select", "bmk-svm-name");
    names.setAttribute("aria-label", "위젯 또는 출력 이름");
    names.addEventListener("focus", () => this.fillNames(spec, names));
    names.addEventListener("change", () => {
      if (spec.name === names.value) return;
      spec.name = names.value;
      this.editRow(row);
    });
    view.specs.push({ spec, names });
    this.fillNames(spec, names);
    root.append(id, pick, names);
    if (row.specs.length > 1) root.appendChild(button("−", "이 참조만 삭제", () => {
      this.cancelPick();
      const before = this.node.computeSize?.()[1];
      row.specs = row.specs.filter((item) => item !== spec);
      this.editRow(row);
      this.render();
      this.ensureSize(before);
    }));
    return root;
  }

  fillNames(spec, select) {
    const found = referencedNode(this.node, strip(spec.id));
    const options = new Map([["", "자동"]]);
    if (found) {
      for (const widget of found.widgets || []) {
        if (widget.name) options.set(widget.name, `${widget.name} (위젯)`);
      }
      for (const output of found.outputs || []) {
        if (output.name) options.set(output.name, `${output.label || output.name} (출력: ${output.name})`);
      }
      if (spec.name && !options.has(spec.name)) options.set(spec.name, `${spec.name} (현재 이름·확인 필요)`);
    }
    const signature = JSON.stringify([[...options], spec.name, !!found]);
    if (this.nameSignatures.get(select) === signature) return;
    this.nameSignatures.set(select, signature);
    select.replaceChildren(...[...options].map(([value, label]) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = label;
      return option;
    }));
    select.value = found ? spec.name : "";
    select.title = !found && spec.name ? `노드를 못 찾음 — 저장된 이름: ${spec.name}` : "자동 또는 위젯·출력 이름";
  }

  refreshResults() {
    for (const row of this.rows) {
      const view = this.views.get(row);
      if (!view) continue;
      const parsed = parseLine(row.raw);
      const invalid = parsed.kind === "raw";
      const missing = parsed.kind === "ref" && parsed.specs.some((spec) => !referencedNode(this.node, spec.id));
      const reserved = parsed.key && RESERVED.has(parsed.key.toLowerCase());
      if (view.addSpec) view.addSpec.hidden = row.key !== "loras";
      // 서버 미리보기가 오면 그쪽 판정만 보인다(같은 경고를 두 번 쓰지 않게).
      const local = reserved ? "예약어는 사용할 수 없음: time·width·height·postfix" :
        invalid ? "문법 오류 — 원문 보존됨" : missing && !row.preview ? "노드를 못 찾음 — ID 확인" : "";
      const resolved = previewText(row.preview);
      view.preview.textContent = [local, resolved].filter(Boolean).join(" · ") ||
        (["comment", "blank"].includes(parsed.kind) ? "그대로 보존" : "미리보기 대기");
      view.root.classList.toggle("bmk-svm-warning", !!(invalid || missing));
      view.preview.classList.toggle("bmk-svm-error", invalid || ["missing", "invalid"].includes(row.preview?.status));
      view.preview.classList.toggle("bmk-svm-muted", row.preview?.status === "wired");
      // 좁은 노드에서 둘 다 잘리지 않게, 새 실행 값이 있으면 그 줄을 다 쓰고 미리보기는 툴팁으로 보인다.
      const fresh = row.executed && !row.executed.stale;
      view.executed.textContent = row.executed ? `실행${row.executed.stale ? " (오래됨)" : ""}: ${row.executed.text}` : "";
      view.executed.title = fresh ? `${view.executed.textContent}\n미리보기: ${view.preview.textContent}` : view.executed.textContent;
      view.preview.title = view.preview.textContent;
      view.executed.hidden = !row.executed;
      view.preview.hidden = !!fresh;
      view.executed.classList.toggle("bmk-svm-stale", !!row.executed?.stale);
      for (const { spec, names } of view.specs) this.fillNames(spec, names);
    }
  }

  setExecuted(message) {
    if (this.disposed || !message || typeof message !== "object") return;
    this.syncFromWidget(false);
    const values = message.values || {};
    const missing = new Set((Array.isArray(message.missing) ? message.missing : []).map((line) => strip(String(line))));
    for (const row of this.rows) {
      const parsed = parseLine(row.raw);
      const keys = parsed.key === "ksampler" ? KEYS.filter((key) => key !== "ksampler") : [parsed.key];
      const fields = keys.filter((key) => key && Object.hasOwn(values, key) && values[key] !== "")
        .map((key) => `${key} = ${valueText(values[key])}`);
      if (missing.has(strip(row.raw))) fields.push("못 찾음");
      row.executed = fields.length ? { text: fields.join(" · "), stale: false } : null;
    }
    this.refreshResults();
  }

  schedulePreview(delay = 400) {
    if (this.disposed) return;
    this.syncFromWidget(false);
    clearTimeout(this.timer);
    this.controller?.abort();
    const revision = ++this.revision;
    for (const row of this.rows) row.preview = null;
    this.refreshResults();
    this.setStatus("미리보기 대기…");
    this.timer = setTimeout(() => this.preview(revision), delay);
  }

  async preview(revision) {
    const refs = this.refs.value;
    const graph = this.node.graph;
    const rootGraph = app.graph;
    const current = () => !this.disposed && revision === this.revision && refs === this.refs.value &&
      graph === this.node.graph && rootGraph === app.graph;
    if (!current()) return;
    this.controller = new AbortController();
    const controller = this.controller;
    try {
      this.setStatus("미리보기 중…");
      const { output } = await app.graphToPrompt();
      if (!current()) return;
      const node_id = executionId(this.node);
      if (node_id == null || !output || !Object.hasOwn(output, node_id)) {
        this.setStatus("미리보기: 이 노드가 큐 대상이 아님(뮤트·바이패스?)");
        return;
      }
      const response = await api.fetchApi("/bmk/save_meta/resolve", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt: output, node_id, refs }),
        signal: controller.signal,
      });
      if (!current()) return;
      if (!response.ok) throw new Error(response.status === 404 ? "서버 라우트 없음 (404)" : `HTTP ${response.status}`);
      const data = await response.json();
      if (!current()) return;
      if (!Array.isArray(data?.lines)) throw new Error("응답 형식 오류");
      if (!data.node_found) throw new Error("서버에서 Meta 노드를 찾지 못함");
      const rows = this.rows.filter((row) => strip(row.raw) && !strip(row.raw).startsWith("#"));
      let mismatches = Math.abs(rows.length - data.lines.length);
      rows.forEach((row, index) => {
        const result = data.lines[index];
        if (typeof result?.line !== "string" || strip(result.line) !== strip(row.raw)) {
          mismatches++;
          return;
        }
        row.preview = result;
      });
      this.refreshResults();
      this.setStatus(mismatches ? "미리보기: 응답 행 불일치 — 다시 시도" : `미리보기 완료 ${new Date().toLocaleTimeString("ko-KR")}`, !!mismatches);
    } catch (error) {
      if (current() && error?.name !== "AbortError") this.setStatus(`미리보기 실패: ${error?.message || String(error)}`, true);
    } finally {
      if (this.controller === controller) this.controller = null;
    }
  }

  cancelPick() {
    if (activePicker?.editor === this) activePicker.cancel();
  }

  startPick(row, spec, trigger) {
    if (activePicker?.editor === this && activePicker.trigger === trigger) {
      activePicker.cancel();
      return;
    }
    activePicker?.cancel();
    const canvas = app.canvas;
    const graph = canvas?.graph;
    const target = canvas?.canvas;
    if (!target || !graph || graph !== this.node.graph) {
      this.setStatus("노드 고르기: 이 Meta가 있는 그래프를 열어 주세요", true);
      return;
    }
    const hint = element("div", "bmk-svm-pick-hint", "노드 고르기: 같은 그래프의 노드를 클릭 · Esc 또는 ◎ 다시 누르기로 취소");
    document.body.appendChild(hint);
    const surface = target.parentElement || target;
    surface.classList.add("bmk-svm-picking");
    trigger.classList.add("bmk-svm-selected");
    const cancel = () => {
      window.removeEventListener("pointerdown", onPointer, true);
      window.removeEventListener("keydown", onKey, true);
      window.removeEventListener("blur", cancel);
      clearInterval(watch);
      surface.classList.remove("bmk-svm-picking");
      trigger.classList.remove("bmk-svm-selected");
      hint.remove();
      if (activePicker?.cancel === cancel) activePicker = null;
    };
    const onKey = (event) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      event.stopImmediatePropagation();
      cancel();
    };
    const onPointer = (event) => {
      if (event.button !== 0 || this.root.contains(event.target)) return;
      if (app.canvas !== canvas || canvas.graph !== graph || this.node.graph !== graph) {
        cancel();
        return;
      }
      if (!surface.contains(event.target)) return;
      const rect = target.getBoundingClientRect();
      if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) return;
      const [x, y] = canvas.convertEventToCanvasOffset(event);
      const found = graph.getNodeOnPos?.(x, y);
      if (!found || !/^\d+$/.test(String(found.id))) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      cancel();
      this.applyPick(row, spec, String(found.id));
    };
    const watch = setInterval(() => {
      if (this.disposed || app.canvas !== canvas || canvas.graph !== graph || this.node.graph !== graph || !target.isConnected) cancel();
    }, 150);
    window.addEventListener("pointerdown", onPointer, true);
    window.addEventListener("keydown", onKey, true);
    window.addEventListener("blur", cancel);
    activePicker = { editor: this, trigger, cancel };
  }

  applyPick(row, spec, id) {
    const before = this.node.computeSize?.()[1];
    if (row) {
      if (!this.rows.includes(row) || !row.specs.includes(spec)) return;
      if (spec.id !== id) {
        spec.id = id;
        this.editRow(row);
      }
    } else {
      this.syncFromWidget();
      row = this.rows.find((item) => parseLine(item.raw).key === "ksampler");
      if (row) {
        // 기존 행 주석과 위치는 유지하고 KSampler 참조만 교체한다.
        const parsed = parseLine(row.raw, row.eol);
        if (parsed.kind !== "ref" || parsed.specs.length !== 1 || parsed.specs[0].id !== id || parsed.specs[0].name) {
          Object.assign(row, { kind: "ref", label: parsed.label || "ksampler", key: "ksampler",
            specs: [{ id, name: "" }], comment: parsed.comment || "" });
          this.editRow(row);
        }
      } else {
        row = parseLine(`ksampler: ${id}`);
        this.appendRow(row);
        this.commit();
      }
    }
    this.render();
    this.ensureSize(before);
  }

  dispose() {
    this.disposed = true;
    this.revision++;
    clearTimeout(this.timer);
    this.controller?.abort();
    this.cancelPick();
    this.textarea?.removeEventListener?.("input", this.onTextInput);
    if (this.refs.callback === this.refsCallback) this.refs.callback = this.originalCallback;
    this.refs.hidden = this.originalHidden;
    this.root.remove();
    // 삭제 후 동일 객체가 다시 추가되는 경우(서브그래프 이동 등)에도 재사용한다.
    // WeakMap이므로 버려진 노드를 붙잡지 않는다.
  }

  resume() {
    if (!this.disposed) return;
    this.disposed = false;
    this.refs.hidden = this.textMode ? this.originalHidden : true;
    if (this.refs.callback === this.originalCallback) this.refs.callback = this.refsCallback;
    this.textarea?.addEventListener?.("input", this.onTextInput);
  }
}

app.registerExtension({
  name: "BMK.SaveMeta",
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData.name !== NODE_ID) return;
    const onNodeCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const result = onNodeCreated?.apply(this, arguments);
      const refs = this.widgets?.find((widget) => widget.name === "refs");
      if (refs && !editors.has(this)) {
        injectStyle();
        editors.set(this, new RefsEditor(this, refs));
      }
      return result;
    };
    for (const hook of ["onConfigure", "onAdded"]) {
      const original = nodeType.prototype[hook];
      nodeType.prototype[hook] = function () {
        const result = original?.apply(this, arguments);
        // configure/서브그래프 복원이 끝난 뒤 refs와 소속 그래프를 읽는다.
        queueMicrotask(() => {
          const editor = editors.get(this);
          if (!editor || !this.graph) return;
          if (hook === "onAdded") editor.resume();
          if (editor.disposed) return;
          editor.syncFromWidget(false);
          editor.ensureSize();
          editor.schedulePreview();
        });
        return result;
      };
    }
    const onDrawForeground = nodeType.prototype.onDrawForeground;
    nodeType.prototype.onDrawForeground = function () {
      const result = onDrawForeground?.apply(this, arguments);
      editors.get(this)?.syncFromWidget();
      return result;
    };
    const onExecuted = nodeType.prototype.onExecuted;
    nodeType.prototype.onExecuted = function (message) {
      const result = onExecuted?.apply(this, arguments);
      editors.get(this)?.setExecuted(message?.bmk_save_meta?.[0]);
      return result;
    };
    const onRemoved = nodeType.prototype.onRemoved;
    nodeType.prototype.onRemoved = function () {
      editors.get(this)?.dispose();
      return onRemoved?.apply(this, arguments);
    };
  },
});
