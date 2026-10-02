// bmk_sam3_mask_layers.js — BMKSAM3MaskLayers 짝 JS 확장 (v1)
//
// layers 입력(BMK_SAM3_LAYERS)을 행 편집기 DOM 위젯으로 그린다. 값은 JSON 문자열 하나
// (행 = {id, op, prompt, threshold, grow, blur, on})이고 그대로 widgets_values·API 프롬프트로 간다.
//
//    #  ⠿  ±  prompt                    threshold  grow  blur  on  ×
//
// - ± 클릭 = 더하기/빼기 전환(행 색 초록/빨강). on 해제 = 건너뜀(흐리게).
// - ⠿ 드래그 = 순서 변경. 레이어는 위→아래 순서로 적용되므로 결과가 바뀐다.
// - 숫자 칸: ◀ ▶ = 한 단계, 값 위 가로 드래그 = 연속 조절, 클릭 = 직접 입력(Enter 확정 / Esc 취소).
// - 실행 후 행마다 감지 개수를 붙이고(prompt·threshold 를 고치면 지움), 기본 이미지 프리뷰에서
//   레이어 장을 보고 있으면 그 행을 강조한다(node.imageIndex → 결과의 레이어 id).
// - 높이: 행 수에 맞춘 고정 높이(getMinHeight = getMaxHeight). 남는 공간은 기본 이미지 프리뷰가 쓴다.
//   행을 추가/삭제하면 노드 높이를 같은 만큼 늘리고 줄인다.
//
// 상태는 LayerEditor(클로저 대신 별도 객체)에 두고 위젯에는 자체 필드를 얹지 않는다
// (프론트 1.53 이 위젯을 BaseWidget 으로 흡수하며 _state 등과 충돌할 수 있음).

import { app } from "../../scripts/app.js";

const NODE_ID = "BMKSAM3MaskLayers";
const WIDGET_TYPE = "BMK_SAM3_LAYERS";
const MARGIN = 6;
const MIN_WIDTH = 500;

// CSS 와 같은 값 — 높이 계산용
const PAD = 4;
const HEAD_H = 14;
const ROW_H = 26;
const GAP = 3;
const ADD_H = 22;

const FIELDS = {
  threshold: { min: 0, max: 1, step: 0.01, decimals: 2, dragPx: 3 },
  grow: { min: -64, max: 64, step: 1, decimals: 0, dragPx: 6 },
  blur: { min: 0, max: 64, step: 1, decimals: 0, dragPx: 6 },
};

const STYLE = `
.bmk-sml-root{box-sizing:border-box;height:100%;padding:${PAD}px 2px;display:flex;flex-direction:column;gap:${GAP}px;font-size:12px;color:var(--input-text,#ddd);overflow:hidden;user-select:none}
.bmk-sml-grid{display:grid;grid-template-columns:20px 12px 22px minmax(60px,1fr) 74px 58px 58px 16px 14px;column-gap:4px;align-items:center}
.bmk-sml-head{flex:0 0 ${HEAD_H}px;padding:0 4px;font-size:10px;line-height:${HEAD_H}px;color:var(--descrip-text,#999);text-align:center;white-space:nowrap}
.bmk-sml-head span:nth-child(4){text-align:left;padding-left:6px}
.bmk-sml-rows{display:flex;flex-direction:column;gap:${GAP}px}
.bmk-sml-row{height:${ROW_H}px;padding:0 4px;box-sizing:border-box;border-radius:6px;background:color-mix(in srgb,#5c8f4c 55%,var(--comfy-input-bg,#222))}
.bmk-sml-row.minus{background:color-mix(in srgb,#a24848 55%,var(--comfy-input-bg,#222))}
.bmk-sml-row.off{opacity:.45}
.bmk-sml-row.active{box-shadow:inset 0 0 0 2px var(--p-primary-color,#5b8def)}
.bmk-sml-row.dragging{opacity:.75;box-shadow:0 2px 8px rgba(0,0,0,.5)}
.bmk-sml-idx{text-align:center;font-variant-numeric:tabular-nums}
.bmk-sml-handle{cursor:grab;text-align:center;font-size:13px;line-height:${ROW_H}px;opacity:.75;touch-action:none}
.bmk-sml-op{width:22px;height:20px;padding:0;border:0;border-radius:5px;background:rgba(0,0,0,.3);color:inherit;font:700 14px/20px Inter,Arial,sans-serif;cursor:pointer}
.bmk-sml-field{height:20px;min-width:0;display:flex;align-items:center;border-radius:5px;background:rgba(0,0,0,.3)}
.bmk-sml-field input{min-width:0;height:100%;padding:0;border:0;outline:none;background:transparent;color:inherit;font:inherit}
.bmk-sml-prompt input{flex:1;padding:0 6px;user-select:text}
.bmk-sml-count{flex:0 0 auto;margin-right:4px;padding:0 5px;border-radius:8px;font-size:10px;line-height:14px;background:rgba(255,255,255,.16)}
.bmk-sml-count.zero{background:rgba(224,85,85,.6)}
.bmk-sml-num button{flex:0 0 14px;height:100%;padding:0;border:0;background:transparent;color:inherit;font-size:9px;opacity:.8;cursor:pointer}
.bmk-sml-num button:hover{opacity:1}
.bmk-sml-num input{flex:1;width:100%;text-align:center;font-variant-numeric:tabular-nums;cursor:ew-resize}
.bmk-sml-num input:focus{cursor:text;user-select:text}
.bmk-sml-on{width:14px;height:14px;margin:0;accent-color:var(--p-primary-color,#5b8def);cursor:pointer}
.bmk-sml-del{padding:0;border:0;background:transparent;color:inherit;font-size:13px;opacity:0;cursor:pointer}
.bmk-sml-row:hover .bmk-sml-del{opacity:.7}
.bmk-sml-del:hover{opacity:1!important}
.bmk-sml-add{flex:0 0 ${ADD_H}px;border:1px dashed var(--border-color,#4e4e4e);border-radius:6px;background:transparent;color:var(--descrip-text,#999);font:inherit;cursor:pointer}
.bmk-sml-add:hover{color:var(--input-text,#ddd);border-color:currentColor}
`;

function injectStyle() {
  if (document.getElementById("bmk-sml-style")) return;
  const el = document.createElement("style");
  el.id = "bmk-sml-style";
  el.textContent = STYLE;
  document.head.appendChild(el);
}

function newId() {
  return Math.random().toString(36).slice(2, 8);
}

function newRow() {
  return { id: newId(), op: "+", prompt: "", threshold: 0.3, grow: 0, blur: 0, on: true };
}

function clamp(v, min, max) {
  return Math.min(max, Math.max(min, v));
}

function normalizeRow(r) {
  const num = (v, key) => {
    const f = FIELDS[key];
    const n = Number(v);
    return Number.isFinite(n) ? clamp(n, f.min, f.max) : newRow()[key];
  };
  return {
    id: typeof r?.id === "string" && r.id ? r.id : newId(),
    op: r?.op === "-" ? "-" : "+",
    prompt: typeof r?.prompt === "string" ? r.prompt : "",
    threshold: num(r?.threshold, "threshold"),
    grow: Math.round(num(r?.grow, "grow")),
    blur: Math.round(num(r?.blur, "blur")),
    on: r?.on !== false,
  };
}

function checkState() {
  try {
    app.extensionManager?.workflow?.activeWorkflow?.changeTracker?.checkState?.();
  } catch {
    // undo 기록은 선택 사항
  }
}

function stop(e) {
  e.stopPropagation();
}

// ◀ 값 ▶ 숫자 칸. 값 위 가로 드래그 = 연속 조절, 움직이지 않고 떼면 직접 입력.
function numberField(row, key, onChange) {
  const f = FIELDS[key];
  const el = document.createElement("div");
  el.className = "bmk-sml-field bmk-sml-num";
  el.innerHTML = `<button type="button" tabindex="-1">◀</button><input type="text" inputmode="decimal" spellcheck="false"><button type="button" tabindex="-1">▶</button>`;
  const [dec, input, inc] = el.children;
  const show = () => {
    input.value = row[key].toFixed(f.decimals);
  };
  const set = (v, commit) => {
    const q = Math.round(clamp(v, f.min, f.max) / f.step) * f.step;
    const next = Number(q.toFixed(f.decimals));
    const changed = next !== row[key];
    row[key] = next;
    show();
    if (changed || commit) onChange(key, commit);
  };

  dec.addEventListener("pointerdown", stop);
  inc.addEventListener("pointerdown", stop);
  dec.addEventListener("click", () => set(row[key] - f.step, true));
  inc.addEventListener("click", () => set(row[key] + f.step, true));

  input.addEventListener("pointerdown", (e) => {
    e.stopPropagation();
    if (document.activeElement === input || e.button !== 0) return;
    e.preventDefault(); // 드래그 판정 전에는 포커스(편집)로 들어가지 않는다
    const x0 = e.clientX;
    const v0 = row[key];
    let dragged = false;
    const move = (ev) => {
      const dx = ev.clientX - x0;
      if (!dragged && Math.abs(dx) < 3) return;
      dragged = true;
      set(v0 + Math.round(dx / f.dragPx) * f.step, false);
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      if (dragged) {
        onChange(key, true);
      } else {
        input.focus();
        input.select();
      }
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") input.blur();
    if (e.key === "Escape") {
      show();
      input.blur();
    }
  });
  input.addEventListener("change", () => {
    const n = parseFloat(input.value);
    if (Number.isFinite(n)) set(n, true);
    else show();
  });
  input.addEventListener("blur", show);

  show();
  return el;
}

class LayerEditor {
  constructor(node) {
    this.node = node;
    this.rows = [newRow()];
    this.counts = new Map(); // id → 마지막 실행의 감지 개수
    this.pageIds = []; // 프리뷰 장 번호 → 레이어 id
    this.activeId = null;

    this.root = document.createElement("div");
    this.root.className = "bmk-sml-root";
    this.root.innerHTML = `
      <div class="bmk-sml-head bmk-sml-grid">
        <span>#</span><span></span><span title="+ 더하기 / − 빼기">±</span><span>prompt</span>
        <span>threshold</span><span>grow</span><span>blur</span><span>on</span><span></span>
      </div>
      <div class="bmk-sml-rows"></div>
      <button type="button" class="bmk-sml-add">+ add layer</button>`;
    this.list = this.root.querySelector(".bmk-sml-rows");
    const add = this.root.querySelector(".bmk-sml-add");
    add.addEventListener("pointerdown", stop);
    add.addEventListener("click", () => this.addRow());

    // 편집기 위 휠은 캔버스 줌/이동으로 넘긴다(편집기 자체는 스크롤하지 않음).
    this.root.addEventListener(
      "wheel",
      (e) => {
        e.preventDefault();
        e.stopPropagation();
        app.canvas?.processMouseWheel?.(e);
      },
      { passive: false },
    );

    this.render();
  }

  contentHeight() {
    const n = this.rows.length;
    return PAD * 2 + HEAD_H + GAP + n * ROW_H + Math.max(0, n - 1) * GAP + GAP + ADD_H;
  }

  widgetHeight() {
    return this.contentHeight() + MARGIN * 2;
  }

  serialize() {
    return JSON.stringify(this.rows);
  }

  load(value) {
    let rows;
    try {
      rows = JSON.parse(typeof value === "string" ? value : "[]");
    } catch {
      rows = [];
    }
    this.rows = Array.isArray(rows) ? rows.map(normalizeRow) : [];
    this.render();
  }

  setResults(list) {
    this.counts = new Map();
    this.pageIds = [];
    for (const r of Array.isArray(list) ? list : []) {
      this.counts.set(String(r.id), Number(r.count) || 0);
      this.pageIds.push(String(r.id));
    }
    this.render();
  }

  setActive(id) {
    if (id === this.activeId) return;
    this.activeId = id;
    for (const el of this.list.children) el.classList.toggle("active", el.dataset.id === id);
  }

  // 사용자 편집 확정: 위젯 값 → undo 기록
  commit() {
    this.node.setDirtyCanvas?.(true, true);
    checkState();
  }

  resizeBy(oldHeight) {
    const delta = this.widgetHeight() - oldHeight;
    const node = this.node;
    if (delta && node.size) node.setSize([node.size[0], Math.max(node.size[1] + delta, node.computeSize()[1])]);
  }

  addRow() {
    const before = this.widgetHeight();
    const last = this.rows[this.rows.length - 1];
    const row = newRow();
    if (last) row.threshold = last.threshold;
    this.rows.push(row);
    this.render();
    this.resizeBy(before);
    this.commit();
    this.list.lastElementChild?.querySelector(".bmk-sml-prompt input")?.focus();
  }

  removeRow(id) {
    const before = this.widgetHeight();
    this.rows = this.rows.filter((r) => r.id !== id);
    this.render();
    this.resizeBy(before);
    this.commit();
  }

  render() {
    this.list.replaceChildren(...this.rows.map((row, i) => this.renderRow(row, i)));
    if (this.activeId) this.setActive(this.activeId);
  }

  renderRow(row, i) {
    const el = document.createElement("div");
    el.className = "bmk-sml-row bmk-sml-grid";
    el.dataset.id = row.id;
    const paint = () => {
      el.classList.toggle("minus", row.op === "-");
      el.classList.toggle("off", !row.on);
      el.classList.toggle("active", row.id === this.activeId);
    };

    const idx = document.createElement("span");
    idx.className = "bmk-sml-idx";
    idx.textContent = String(i + 1).padStart(2, "0");

    const handle = document.createElement("span");
    handle.className = "bmk-sml-handle";
    handle.textContent = "⠿";
    handle.title = "드래그해서 순서 변경";
    handle.addEventListener("pointerdown", (e) => this.startDrag(e, el));

    const op = document.createElement("button");
    op.type = "button";
    op.className = "bmk-sml-op";
    const showOp = () => {
      op.textContent = row.op === "-" ? "−" : "+";
      op.title = row.op === "-" ? "빼기 (클릭: 더하기로)" : "더하기 (클릭: 빼기로)";
    };
    showOp();
    op.addEventListener("pointerdown", stop);
    op.addEventListener("click", () => {
      row.op = row.op === "-" ? "+" : "-";
      showOp();
      paint();
      this.commit();
    });

    const promptBox = document.createElement("div");
    promptBox.className = "bmk-sml-field bmk-sml-prompt";
    const prompt = document.createElement("input");
    prompt.type = "text";
    prompt.spellcheck = false;
    prompt.placeholder = "prompt  (예: thigh skin:6, arm)";
    prompt.value = row.prompt;
    promptBox.appendChild(prompt);
    const count = document.createElement("span");
    count.className = "bmk-sml-count";
    const showCount = () => {
      const c = this.counts.get(row.id);
      count.hidden = c == null;
      count.textContent = c == null ? "" : String(c);
      count.classList.toggle("zero", c === 0);
      count.title = c == null ? "" : `마지막 실행에서 감지된 개수: ${c}`;
    };
    showCount();
    promptBox.appendChild(count);
    prompt.addEventListener("pointerdown", stop);
    prompt.addEventListener("input", () => {
      row.prompt = prompt.value;
      if (this.counts.delete(row.id)) showCount();
    });
    prompt.addEventListener("change", () => this.commit());
    prompt.addEventListener("keydown", (e) => {
      if (e.key === "Enter") prompt.blur();
    });

    const onNumber = (key, commit) => {
      if (key === "threshold" && this.counts.delete(row.id)) showCount();
      if (commit) this.commit();
    };

    const on = document.createElement("input");
    on.type = "checkbox";
    on.className = "bmk-sml-on";
    on.checked = row.on;
    on.title = "끄면 이 레이어를 건너뜀";
    on.addEventListener("pointerdown", stop);
    on.addEventListener("change", () => {
      row.on = on.checked;
      paint();
      this.commit();
    });

    const del = document.createElement("button");
    del.type = "button";
    del.className = "bmk-sml-del";
    del.textContent = "×";
    del.title = "레이어 삭제";
    del.addEventListener("pointerdown", stop);
    del.addEventListener("click", () => this.removeRow(row.id));

    el.append(
      idx,
      handle,
      op,
      promptBox,
      numberField(row, "threshold", onNumber),
      numberField(row, "grow", onNumber),
      numberField(row, "blur", onNumber),
      on,
      del,
    );
    paint();
    return el;
  }

  // ⠿ 드래그: 행 요소를 DOM 안에서 바로 옮기고, 놓으면 DOM 순서로 rows 를 다시 맞춘다.
  // 합성 PointerEvent 에서 setPointerCapture 가 던지므로 window 리스너로 받는다.
  startDrag(e, el) {
    if (e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    el.classList.add("dragging");
    const before = this.serialize();
    const move = (ev) => {
      const others = [...this.list.children].filter((c) => c !== el);
      const next = others.find((c) => {
        const r = c.getBoundingClientRect();
        return ev.clientY < r.top + r.height / 2;
      });
      if (next) {
        if (next !== el.nextElementSibling) this.list.insertBefore(el, next);
      } else if (this.list.lastElementChild !== el) {
        this.list.appendChild(el);
      }
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      el.classList.remove("dragging");
      const order = [...this.list.children].map((c) => c.dataset.id);
      const byId = new Map(this.rows.map((r) => [r.id, r]));
      this.rows = order.map((id) => byId.get(id)).filter(Boolean);
      this.render();
      if (this.serialize() !== before) this.commit();
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }
}

app.registerExtension({
  name: "BMK.SAM3MaskLayers",

  getCustomWidgets() {
    return {
      [WIDGET_TYPE](node, inputName, inputData) {
        injectStyle();
        const editor = new LayerEditor(node);
        const initial = inputData?.[1]?.default;
        if (typeof initial === "string" && initial !== "[]") editor.load(initial);
        node.__bmkSml = editor;
        const widget = node.addDOMWidget(inputName, WIDGET_TYPE, editor.root, {
          getValue: () => editor.serialize(),
          setValue: (v) => editor.load(v),
          getMinHeight: () => editor.widgetHeight(),
          getMaxHeight: () => editor.widgetHeight(),
          hideOnZoom: true,
          margin: MARGIN,
        });
        return { widget };
      },
    };
  },

  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData.name !== NODE_ID) return;

    const onNodeCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const r = onNodeCreated?.apply(this, arguments);
      const sz = this.computeSize?.() ?? this.size;
      this.setSize([Math.max(sz[0], this.size[0], MIN_WIDTH), Math.max(sz[1], this.size[1])]);
      return r;
    };

    const onExecuted = nodeType.prototype.onExecuted;
    nodeType.prototype.onExecuted = function (message) {
      onExecuted?.apply(this, arguments);
      this.__bmkSml?.setResults(message?.bmk_sam3_layers);
    };

    // 기본 이미지 프리뷰에서 레이어 장을 보고 있으면 그 행을 강조(격자 보기·최종 장이면 해제).
    const onDrawForeground = nodeType.prototype.onDrawForeground;
    nodeType.prototype.onDrawForeground = function () {
      const r = onDrawForeground?.apply(this, arguments);
      const editor = this.__bmkSml;
      if (editor) {
        const i = this.imgs?.length ? this.imageIndex : null;
        editor.setActive(i == null ? null : (editor.pageIds[i] ?? null));
      }
      return r;
    };
  },
});
