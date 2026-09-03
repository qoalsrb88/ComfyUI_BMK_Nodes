// bmk_text_viewer_tabs.js — BMKTextViewerTabs 짝 JS 확장 (v1)
//
// 여러 STRING 입력을 높이가 고정된 DOM 위젯 하나에서 탭으로 돌려 본다.
//   [헤더: 탭 바 | 줄바꿈 · 복사 버튼]  /  [본문: 현재 탭 텍스트]  /  [푸터: 위치 · 글자 수]
//
// 배치 — 입력 슬롯을 뷰어 "왼쪽 열"에 두기
//  - node.widgets_start_y = 0 으로 DOM 위젯을 본체 최상단부터 채운다(litegraph 가
//    computeSize 에서 max(슬롯 높이, 위젯 높이) 를 쓰므로 포트가 늘어도 노드가 길어지지 않는다).
//  - 위젯 루트의 왼쪽 띠(PORT_COL)를 투명 + pointer-events:none 으로 비워, 그 아래 캔버스에
//    그려지는 입력 슬롯이 그대로 보이고 링크 드래그도 된다. 프론트엔드(1.49.6)가 래퍼
//    (.dom-widget)에 pointer-events:auto 를 인라인으로 박기 때문에 :has() + !important 로
//    래퍼도 none 으로 눕히고, 실제 상호작용은 .bmk-tvt-panel 이 받는다.
//
// 표시 — 코어 멀티라인 textarea(.comfy-multiline-input) 와 같은 글꼴/크기/색
//    (body 글꼴 상속, --comfy-textarea-font-size, --input-text, --comfy-input-bg).
//    줄바꿈 기본 ON(textarea 와 동일), 버튼으로 토글.
//
// 탭 배치 모드(우클릭 메뉴 "Tab layout") — 이름이 길거나 노드가 좁을 때
//    fit    : 한 줄에 전부 보이도록 균등 축소 + 말줄임. 활성 탭은 축소하지 않음(기본).
//    wrap   : 노드 폭에 맞춰 여러 줄로 줄바꿈(헤더가 커지는 만큼 본문이 줄어듦).
//    scroll : 한 줄 가로 스크롤(휠로 탭 전환).
//
// 이름 매칭 — 파이썬은 "text_1" 을 보내고 프론트 슬롯 이름은 "texts.text_1" 이므로
//    endsWith("." + name) 으로도 찾는다. 탭 라벨은 연결된 소스 노드 제목이며,
//    "Save_01.PIDUpscale" 처럼 점 앞 접두어가 있으면 떼어 "PIDUpscale" 만 쓴다.
//
// 저장 — 표시 텍스트는 widgets_values 에 넣지 않고(serialize:false),
//    "Remember last result"(기본 ON) 이면 node.properties 에 마지막 결과를 남겨 복원한다.
//
// 알려진 한계 — Vue 노드 렌더링 모드(Comfy.VueNodes.Enabled)에서는 슬롯이 DOM 으로
//    그려져 왼쪽 열 겹침 배치가 적용되지 않는다(뷰어는 슬롯 아래에 정상 표시).

import { app } from "../../scripts/app.js";

const NODE_ID = "BMKTextViewerTabs";
const WIDGET_NAME = "bmk_tvt_viewer";
const PROP_STATE = "bmk_tvt_last_result";
const PROP_WRAP = "bmk_tvt_word_wrap";
const PROP_REMEMBER = "bmk_tvt_remember_result";
const PROP_TAB_LAYOUT = "bmk_tvt_tab_layout";
const TAB_LAYOUTS = ["fit", "wrap", "scroll"];
const PORT_COL = 84; // 노드 왼쪽 가장자리부터 입력 슬롯 열의 폭(px). "text_10" 라벨 오른끝 ≈ 68px
const WIDGET_MARGIN = 6; // DOM 위젯 바깥 여백(프론트 기본 10)
const MIN_WIDTH = 440;
const MIN_HEIGHT = 240;
const VIEWER_MIN_HEIGHT = 150;
const FEEDBACK_MS = 1000;

const SVG_OPEN =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">';
const ICON = {
  copy: `${SVG_OPEN}<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/></svg>`,
  check: `${SVG_OPEN}<path d="M5 12l5 5L20 7"/></svg>`,
  fail: `${SVG_OPEN}<path d="M18 6L6 18M6 6l12 12"/></svg>`,
  wrap: `${SVG_OPEN}<path d="M4 6h16"/><path d="M4 18h5"/><path d="M4 12h13a3 3 0 0 1 0 6h-4l2-2m0 4l-2-2"/></svg>`,
};

const STYLE = `
.dom-widget:has(> .bmk-tvt-root){pointer-events:none!important}
.bmk-tvt-root{position:relative;height:100%;box-sizing:border-box;pointer-events:none;background:transparent}
.bmk-tvt-panel{pointer-events:auto;position:absolute;top:0;bottom:0;right:0;left:var(--bmk-tvt-port-col,78px);display:flex;flex-direction:column;box-sizing:border-box;background:var(--comfy-input-bg,#222);color:var(--input-text,#ddd);border:1px solid var(--border-color,#4e4e4e);border-radius:8px;overflow:hidden;font-size:12px;outline:none}
.bmk-tvt-panel:focus-visible{border-color:var(--p-primary-color,#5b8def)}
.bmk-tvt-header{display:flex;align-items:flex-start;gap:4px;padding:4px 6px;border-bottom:1px solid var(--border-color,#4e4e4e);flex:0 0 auto}
.bmk-tvt-tabs{display:flex;gap:2px;flex:1 1 auto;min-width:0}
.bmk-tvt-tabs.scroll{overflow-x:auto;scrollbar-width:none}
.bmk-tvt-tabs.scroll::-webkit-scrollbar{display:none}
.bmk-tvt-tabs.scroll .bmk-tvt-tab{flex:0 0 auto;max-width:170px}
.bmk-tvt-tabs.fit .bmk-tvt-tab{flex:1 1 0;min-width:0}
.bmk-tvt-tabs.fit .bmk-tvt-tab.active{flex:0 0 auto;max-width:60%}
.bmk-tvt-tabs.wrap{flex-wrap:wrap}
.bmk-tvt-tabs.wrap .bmk-tvt-tab{flex:0 1 auto;max-width:100%}
.bmk-tvt-tab{display:inline-flex;align-items:center;gap:5px;padding:3px 8px;border-radius:6px;color:var(--descrip-text,#999);cursor:pointer;user-select:none;white-space:nowrap;line-height:1.3;overflow:hidden;box-sizing:border-box}
.bmk-tvt-tab>span{flex:0 1 auto;min-width:0;overflow:hidden;text-overflow:ellipsis}
.bmk-tvt-tab:hover{color:var(--input-text,#ddd);background:color-mix(in srgb,currentColor 10%,transparent)}
.bmk-tvt-tab.active{color:var(--input-text,#ddd);font-weight:600;background:color-mix(in srgb,var(--p-primary-color,#5b8def) 26%,transparent)}
.bmk-tvt-tab.empty{opacity:.45;font-style:italic}
.bmk-tvt-dot{width:6px;height:6px;border-radius:50%;background:#e0a83a;flex:0 0 auto}
.bmk-tvt-actions{display:flex;gap:2px;flex:0 0 auto}
.bmk-tvt-btn{width:24px;height:24px;padding:0;display:inline-flex;align-items:center;justify-content:center;background:transparent;border:1px solid transparent;border-radius:6px;color:var(--descrip-text,#999);cursor:pointer}
.bmk-tvt-btn svg{width:15px;height:15px}
.bmk-tvt-btn:hover{color:var(--input-text,#ddd);border-color:var(--border-color,#4e4e4e)}
.bmk-tvt-btn.on{color:var(--p-primary-color,#5b8def)}
.bmk-tvt-btn.ok{color:#4caf78}
.bmk-tvt-btn.fail{color:#e05555}
.bmk-tvt-body{flex:1 1 auto;min-height:0;overflow:auto}
.bmk-tvt-text{margin:0;padding:3px 4px;font-family:inherit;font-size:var(--comfy-textarea-font-size,10px);line-height:normal;letter-spacing:normal;color:var(--input-text,#ddd);white-space:pre-wrap;overflow-wrap:break-word;user-select:text;cursor:text;box-sizing:border-box;min-width:100%}
.bmk-tvt-panel.nowrap .bmk-tvt-text{white-space:pre;overflow-wrap:normal;width:max-content}
.bmk-tvt-text.placeholder{color:var(--descrip-text,#999);font-style:italic;white-space:pre-wrap;width:auto}
.bmk-tvt-footer{display:flex;justify-content:space-between;gap:8px;padding:2px 8px;border-top:1px solid var(--border-color,#4e4e4e);color:var(--descrip-text,#999);font-size:11px;flex:0 0 auto;user-select:none;white-space:nowrap}
.bmk-tvt-footer span{overflow:hidden;text-overflow:ellipsis}
`;

function injectStyle() {
  if (document.getElementById("bmk-tvt-style")) return;
  const el = document.createElement("style");
  el.id = "bmk-tvt-style";
  el.textContent = STYLE;
  document.head.appendChild(el);
}

function toArray(v) {
  if (Array.isArray(v)) return v;
  return v == null ? [] : [v];
}

function toText(v) {
  if (typeof v === "string") return v;
  if (v == null) return "";
  if (typeof v === "object") {
    try {
      return JSON.stringify(v, null, 2);
    } catch {
      return String(v);
    }
  }
  return String(v);
}

// "Save_01.PIDUpscale" → "PIDUpscale". 점 접두어가 없으면 그대로.
function cleanTitle(title) {
  let t = (title || "").trim();
  const m = t.match(/^[A-Za-z][\w-]{0,39}\.([A-Za-z].*)$/);
  if (m) t = m[1].trim();
  return t;
}

// 파이썬은 "text_1", 프론트 슬롯은 "texts.text_1".
function findInputIndex(node, inputName) {
  const inputs = node.inputs ?? [];
  let idx = inputs.findIndex((i) => i.name === inputName);
  if (idx < 0) idx = inputs.findIndex((i) => i.name?.endsWith(`.${inputName}`));
  return idx;
}

function sourceLabel(node, inputName) {
  const idx = findInputIndex(node, inputName);
  if (idx < 0) return inputName;
  let origin = null;
  try {
    origin = node.getInputNode?.(idx) ?? null;
    let guard = 0;
    while (origin && origin.type === "Reroute" && guard++ < 16) {
      origin = origin.getInputNode?.(0) ?? null;
    }
  } catch {
    origin = null;
  }
  if (!origin) return inputName;
  return cleanTitle(origin.title || origin.type) || inputName;
}

async function writeClipboard(text) {
  try {
    if (window.isSecureContext && navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // http://<lan-ip> 등 비보안 컨텍스트 → 아래 구식 경로로
  }
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.setAttribute("readonly", "");
  ta.style.position = "fixed";
  ta.style.top = "-1000px";
  ta.style.opacity = "0";
  document.body.appendChild(ta);
  ta.select();
  let ok = false;
  try {
    ok = document.execCommand("copy");
  } catch {
    ok = false;
  }
  ta.remove();
  return ok;
}

function toast(severity, summary, detail) {
  try {
    app.extensionManager?.toast?.add?.({ severity, summary, detail, life: 1500 });
  } catch {
    // toast 는 선택 사항
  }
}

function tabLayoutOf(node) {
  const v = node.properties?.[PROP_TAB_LAYOUT];
  return TAB_LAYOUTS.includes(v) ? v : "fit";
}

class TextViewer {
  constructor(node) {
    this.node = node;
    this.entries = []; // [{ name, text }]
    this.active = 0;
    this.changed = new Set(); // 직전 결과와 텍스트가 달라진 name
    this.feedbackTimer = null;
    this.build();
  }

  build() {
    const root = document.createElement("div");
    root.className = "bmk-tvt-root";
    root.style.setProperty("--bmk-tvt-port-col", `${PORT_COL - WIDGET_MARGIN}px`);
    root.innerHTML = `
      <div class="bmk-tvt-panel" tabindex="0">
        <div class="bmk-tvt-header">
          <div class="bmk-tvt-tabs"></div>
          <div class="bmk-tvt-actions">
            <button class="bmk-tvt-btn bmk-tvt-wrap" type="button" title="Toggle word wrap">${ICON.wrap}</button>
            <button class="bmk-tvt-btn bmk-tvt-copy" type="button" title="Copy current tab (Shift+click / right-click: copy all)">${ICON.copy}</button>
          </div>
        </div>
        <div class="bmk-tvt-body"><pre class="bmk-tvt-text"></pre></div>
        <div class="bmk-tvt-footer"><span class="bmk-tvt-pos"></span><span class="bmk-tvt-count"></span></div>
      </div>`;

    this.root = root;
    this.panel = root.querySelector(".bmk-tvt-panel");
    this.header = root.querySelector(".bmk-tvt-header");
    this.tabsEl = root.querySelector(".bmk-tvt-tabs");
    this.body = root.querySelector(".bmk-tvt-body");
    this.textEl = root.querySelector(".bmk-tvt-text");
    this.footer = root.querySelector(".bmk-tvt-footer");
    this.posEl = root.querySelector(".bmk-tvt-pos");
    this.countEl = root.querySelector(".bmk-tvt-count");
    this.copyBtn = root.querySelector(".bmk-tvt-copy");
    this.wrapBtn = root.querySelector(".bmk-tvt-wrap");

    this.copyBtn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      this.copy(e.shiftKey);
    });
    this.copyBtn.addEventListener("contextmenu", (e) => {
      e.preventDefault();
      e.stopPropagation();
      this.copy(true);
    });
    this.wrapBtn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      this.setWrap(!this.node.properties[PROP_WRAP]);
    });

    // 헤더/푸터 위 휠 = 탭 전환
    const cycle = (e) => {
      if (!this.entries.length) return;
      e.preventDefault();
      e.stopPropagation();
      const delta = Math.abs(e.deltaY) >= Math.abs(e.deltaX) ? e.deltaY : e.deltaX;
      this.step(delta > 0 ? 1 : -1);
    };
    this.header.addEventListener("wheel", cycle, { passive: false });
    this.footer.addEventListener("wheel", cycle, { passive: false });

    // 본문 위 휠 = 텍스트 스크롤. 스크롤할 게 없거나 Ctrl 이면 캔버스 줌으로 넘긴다.
    this.body.addEventListener(
      "wheel",
      (e) => {
        const scrollable =
          this.body.scrollHeight > this.body.clientHeight || this.body.scrollWidth > this.body.clientWidth;
        if (e.ctrlKey || !scrollable) {
          e.preventDefault();
          e.stopPropagation();
          app.canvas?.processMouseWheel?.(e);
          return;
        }
        e.stopPropagation();
      },
      { passive: false },
    );

    this.panel.addEventListener("keydown", (e) => {
      if (e.ctrlKey || e.metaKey || e.altKey || !this.entries.length) return;
      let handled = true;
      switch (e.key) {
        case "ArrowLeft":
          this.step(-1);
          break;
        case "ArrowRight":
          this.step(1);
          break;
        case "Home":
          this.select(0);
          break;
        case "End":
          this.select(this.entries.length - 1);
          break;
        default:
          handled = false;
      }
      if (handled) {
        e.preventDefault();
        e.stopPropagation();
      }
    });

    this.render();
  }

  setWrap(on) {
    this.node.properties[PROP_WRAP] = !!on;
    this.applyWrap();
    app.graph?.setDirtyCanvas(true, false);
  }

  applyWrap() {
    const on = this.node.properties[PROP_WRAP] !== false;
    this.panel.classList.toggle("nowrap", !on);
    this.wrapBtn.classList.toggle("on", on);
  }

  setTabLayout(mode) {
    this.node.properties[PROP_TAB_LAYOUT] = TAB_LAYOUTS.includes(mode) ? mode : "fit";
    this.render();
    app.graph?.setDirtyCanvas(true, false);
  }

  applyTabLayout() {
    const mode = tabLayoutOf(this.node);
    for (const m of TAB_LAYOUTS) this.tabsEl.classList.toggle(m, m === mode);
  }

  setEntries(names, texts, { markChanged = true } = {}) {
    names = toArray(names);
    texts = toArray(texts).map(toText);
    if (names.length !== texts.length) names = texts.map((_, i) => `text_${i + 1}`);

    const previous = new Map(this.entries.map((e) => [e.name, e.text]));
    const hadPrevious = this.entries.length > 0;
    const entries = names.map((name, i) => ({ name: String(name), text: texts[i] }));

    this.changed = new Set();
    if (markChanged && hadPrevious) {
      for (const e of entries) {
        if (previous.has(e.name) && previous.get(e.name) !== e.text) this.changed.add(e.name);
      }
    }

    const activeName = this.entries[this.active]?.name;
    this.entries = entries;
    const keep = entries.findIndex((e) => e.name === activeName);
    this.active = keep >= 0 ? keep : 0;
    this.render();
  }

  clear() {
    this.entries = [];
    this.active = 0;
    this.changed.clear();
    this.render();
  }

  labels() {
    const labels = this.entries.map((e) => sourceLabel(this.node, e.name));
    const seen = new Map();
    for (const l of labels) seen.set(l, (seen.get(l) ?? 0) + 1);
    return labels.map((l, i) => (seen.get(l) > 1 ? `${l} (${this.entries[i].name})` : l));
  }

  render() {
    const n = this.entries.length;
    this.applyWrap();
    this.applyTabLayout();
    this.tabsEl.replaceChildren();

    if (!n) {
      this.textEl.classList.add("placeholder");
      this.textEl.textContent = "Connect text inputs and run the queue.";
      this.posEl.textContent = "0 / 0";
      this.countEl.textContent = "";
      return;
    }

    this.active = Math.min(Math.max(this.active, 0), n - 1);
    const labels = this.labels();
    this.entries.forEach((entry, i) => {
      const tab = document.createElement("div");
      tab.className = "bmk-tvt-tab";
      if (i === this.active) tab.classList.add("active");
      if (!entry.text.trim()) tab.classList.add("empty");
      tab.title = `${entry.name}  ←  ${labels[i]}`;
      const span = document.createElement("span");
      span.textContent = labels[i];
      tab.appendChild(span);
      if (this.changed.has(entry.name)) {
        const dot = document.createElement("i");
        dot.className = "bmk-tvt-dot";
        dot.title = "Changed since previous result";
        tab.appendChild(dot);
      }
      tab.addEventListener("pointerdown", (e) => e.stopPropagation());
      tab.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        this.select(i);
        this.panel.focus({ preventScroll: true });
      });
      this.tabsEl.appendChild(tab);
    });

    const current = this.entries[this.active];
    this.textEl.classList.remove("placeholder");
    this.textEl.textContent = current.text;
    this.body.scrollTop = 0;
    this.body.scrollLeft = 0;
    this.posEl.textContent = `${this.active + 1} / ${n}  ·  ← → / wheel`;
    this.countEl.textContent = `${current.text.length.toLocaleString()} chars`;

    if (tabLayoutOf(this.node) === "scroll") {
      this.tabsEl.children[this.active]?.scrollIntoView?.({ inline: "nearest", block: "nearest" });
    }
  }

  select(i) {
    if (!this.entries.length) return;
    const n = this.entries.length;
    this.active = ((i % n) + n) % n;
    this.changed.delete(this.entries[this.active].name); // 본 탭은 변경 표시 해제
    this.render();
  }

  step(delta) {
    this.select(this.active + delta);
  }

  currentText() {
    return this.entries[this.active]?.text ?? "";
  }

  allText() {
    const labels = this.labels();
    return this.entries.map((e, i) => `=== ${labels[i]} (${e.name}) ===\n${e.text}`).join("\n\n");
  }

  async copy(all) {
    if (!this.entries.length) {
      toast("warn", "Nothing to copy", "Run the queue first.");
      return;
    }
    const text = all ? this.allText() : this.currentText();
    const ok = await writeClipboard(text);
    this.feedback(ok);
    if (ok) {
      toast("success", all ? `Copied ${this.entries.length} tabs` : "Copied", `${text.length.toLocaleString()} chars`);
    } else {
      toast("error", "Copy failed", "Clipboard is unavailable in this browser context.");
    }
  }

  feedback(ok) {
    clearTimeout(this.feedbackTimer);
    this.copyBtn.innerHTML = ok ? ICON.check : ICON.fail;
    this.copyBtn.classList.toggle("ok", ok);
    this.copyBtn.classList.toggle("fail", !ok);
    this.feedbackTimer = setTimeout(() => {
      this.copyBtn.innerHTML = ICON.copy;
      this.copyBtn.classList.remove("ok", "fail");
    }, FEEDBACK_MS);
  }
}

function getViewer(node) {
  return node.__bmkTvt ?? null;
}

function persist(node) {
  const viewer = getViewer(node);
  if (!viewer) return;
  if (node.properties[PROP_REMEMBER]) {
    node.properties[PROP_STATE] = {
      names: viewer.entries.map((e) => e.name),
      texts: viewer.entries.map((e) => e.text),
    };
  } else {
    delete node.properties[PROP_STATE];
  }
}

app.registerExtension({
  name: "BMK.TextViewerTabs",

  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData.name !== NODE_ID) return;
    injectStyle();

    const onNodeCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      onNodeCreated?.apply(this, arguments);
      this.properties ??= {};
      this.properties[PROP_WRAP] ??= true;
      this.properties[PROP_REMEMBER] ??= true;
      this.properties[PROP_TAB_LAYOUT] ??= "fit";

      const viewer = new TextViewer(this);
      this.__bmkTvt = viewer;

      const widget = this.addDOMWidget(WIDGET_NAME, "BMK_TEXT_VIEWER_TABS", viewer.root, {
        serialize: false,
        hideOnZoom: true,
        margin: WIDGET_MARGIN,
        getMinHeight: () => VIEWER_MIN_HEIGHT,
        getValue: () => "",
        setValue: () => {},
      });
      widget.serialize = false;
      if (widget.options) widget.options.serialize = false;
      widget.onRemove = () => clearTimeout(viewer.feedbackTimer);

      // 위젯을 본체 최상단부터 채워 입력 슬롯과 나란히 놓는다(왼쪽 띠는 투명).
      this.widgets_start_y = 0;

      const sz = this.computeSize?.() ?? this.size;
      this.setSize([Math.max(sz[0], this.size[0], MIN_WIDTH), Math.max(sz[1], this.size[1], MIN_HEIGHT)]);
    };

    const onExecuted = nodeType.prototype.onExecuted;
    nodeType.prototype.onExecuted = function (message) {
      onExecuted?.apply(this, arguments);
      const viewer = getViewer(this);
      if (!viewer) return;
      viewer.setEntries(message?.names, message?.text, { markChanged: true });
      persist(this);
    };

    const onConfigure = nodeType.prototype.onConfigure;
    nodeType.prototype.onConfigure = function () {
      onConfigure?.apply(this, arguments);
      const restore = () => {
        const viewer = getViewer(this);
        if (!viewer) return;
        this.widgets_start_y = 0;
        const state = this.properties?.[PROP_STATE];
        if (this.properties[PROP_REMEMBER] && state && Array.isArray(state.texts)) {
          viewer.setEntries(state.names, state.texts, { markChanged: false });
        } else {
          viewer.render();
        }
      };
      if (getViewer(this)) restore();
      else requestAnimationFrame(restore);
    };

    // 소스 노드 이름 변경/재연결 시 탭 라벨 갱신
    const onConnectionsChange = nodeType.prototype.onConnectionsChange;
    nodeType.prototype.onConnectionsChange = function () {
      onConnectionsChange?.apply(this, arguments);
      getViewer(this)?.render();
    };

    const getExtraMenuOptions = nodeType.prototype.getExtraMenuOptions;
    nodeType.prototype.getExtraMenuOptions = function (canvas, options) {
      const result = getExtraMenuOptions?.apply(this, arguments);
      const viewer = getViewer(this);
      if (!viewer) return result;
      const remember = !!this.properties[PROP_REMEMBER];
      const wrap = this.properties[PROP_WRAP] !== false;
      const layout = tabLayoutOf(this);
      options.push(
        null,
        { content: "Copy current tab", callback: () => viewer.copy(false) },
        { content: "Copy all tabs", callback: () => viewer.copy(true) },
        { content: `${wrap ? "✓ " : ""}Word wrap`, callback: () => viewer.setWrap(!wrap) },
        null,
        ...TAB_LAYOUTS.map((m) => ({
          content: `${layout === m ? "✓ " : ""}Tab layout: ${m}`,
          callback: () => viewer.setTabLayout(m),
        })),
        null,
        {
          content: `${remember ? "✓ " : ""}Remember last result`,
          callback: () => {
            this.properties[PROP_REMEMBER] = !remember;
            persist(this);
          },
        },
        {
          content: "Clear viewer",
          callback: () => {
            viewer.clear();
            persist(this);
          },
        },
      );
      return result;
    };
  },
});
