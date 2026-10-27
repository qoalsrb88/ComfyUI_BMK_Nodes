// bmk_tag_organizer.js — BMKTagOrganizer 짝 JS 확장 (v1)
//
// 배열 세팅(arrangement) 청크 편집기. TagOrganizer 관리 툴 미리보기 탭의 청크 편집기(gui/chunks.py)와
// 같은 조작을 DOM 위젯으로 한다. 형식·배치 규칙의 정본은 TagOrganizer docs/arrangement.md 이고,
// 조작 규칙(Model)은 gui/arrangement.py 를 그대로 옮겼다(바꾸면 두 곳을 같이 고친다).
//
//   1 | [구도 3 ▸] [시선 1 ▸]
//   2 | [★ 내 묘사 2] [의상 5 ◂ (상의)(하의)(의상 그 밖)]
//
// - 한 줄 = 출력 한 줄, 줄 안의 청크는 ", " 로 이어진다. 색 = 분류 색(또는 청크 color), 숫자 = 남는 태그 수.
// - 청크를 끌면 순서·줄이 바뀐다(줄 사이·맨 아래에 놓으면 새 줄). ▸ 또는 더블클릭으로 펼치면 서브(멤버)가
//   보이고, 서브를 끌어 순서를 바꾸거나(서브 순서가 켜짐) 다른 청크에 넣거나 청크 밖에 놓아 새 청크로 뺀다.
// - 우클릭: 줄바꿈·앞 줄과 합치기·숨기기(그 청크의 태그를 뺌)·이름/색·다른 청크에 합치기·커스텀 청크
//   (태그·패턴) 편집. 툴바: 서브 순서, 새 커스텀 청크, 가져오기/내보내기(관리 툴과 같은 JSON 파일), ⋯ 메뉴.
// - 결과는 숨긴 STRING 위젯 arrangement 에 JSON 으로 쓴다(widgets_values·API 프롬프트로 그대로 간다).
//   위젯이 비어 있으면 노드는 layout 을 쓰고, 편집기는 그 레이아웃과 같은 배열에서 시작한다(손대기 전엔 비운 채).
//   되돌리기는 워크플로 Ctrl+Z(편집마다 changeTracker 에 기록).
// - 미리보기: 지금 위젯 값으로 서버 /bmk/tag_organizer/preview 를 불러(실행과 같은 옵션 함수) 남는 태그는
//   밝게, 빼는 태그는 제자리에 흐리게 그린다. text 를 상류에 연결했으면 마지막 실행의 입력(ui.bmk_tag_input).
// - 분류·색·레이아웃별 시작 배열은 /bmk/tag_organizer/palette 에서 한 번 받는다(⋯ → 팩 다시 읽기).
//
// 상태는 ArrangementEditor 객체에 두고 위젯에는 자체 필드를 얹지 않는다(프론트 1.53 이 위젯을
// BaseWidget 으로 흡수하며 충돌할 수 있음 — bmk_sam3_mask_layers.js 와 같은 이유).

import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const NODE_ID = "BMKTagOrganizer";
const ARR = "arrangement";
const EDITOR_TYPE = "BMK_TAG_ARRANGEMENT";
const PALETTE_URL = "/bmk/tag_organizer/palette";
const PREVIEW_URL = "/bmk/tag_organizer/preview";
const FORMAT = "tagorg-arrangement";
const VERSION = 1;
const SYSTEM = ["etc", "unknown", "special"];
const OPTION_KEYS = [
  "preset", "exclude", "max_rating", "rules", "layout", "input_dialect", "output_dialect",
  "line_per_section", "trailing_comma", "dedupe", "framing", "view", "guess",
];
const MIN_WIDTH = 560;
const ADD_HEIGHT = 420;      // 처음 만들 때 편집기 몫으로 늘리는 높이
const EDITOR_MIN_H = 300;
const MARGIN = 6;
const POLL_MS = 400;         // 위젯 값(입력·옵션·undo 로 바뀐 배열) 변화 감지
const PREVIEW_DELAY = 250;
const EDGE = 5;              // 줄 위·아래 가장자리: 여기에 놓으면 새 줄
const ID_TRIM = /[^A-Za-z0-9_.\-]+/g;
const COLOR = /^#[0-9a-fA-F]{6}$/;

const STYLE = `
.bmk-to-root{box-sizing:border-box;height:100%;display:flex;flex-direction:column;gap:4px;padding:2px;font:12px/1.35 Inter,Arial,sans-serif;color:var(--input-text,#ddd);overflow:hidden;user-select:none}
.bmk-to-bar{flex:0 0 auto;display:flex;flex-wrap:wrap;align-items:center;gap:4px}
.bmk-to-state{flex:1 1 150px;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--descrip-text,#999)}
.bmk-to-state b{color:var(--input-text,#ddd);font-weight:600}
.bmk-to-btn{height:20px;padding:0 7px;border:1px solid var(--border-color,#4e4e4e);border-radius:5px;background:var(--comfy-input-bg,#222);color:inherit;font:inherit;cursor:pointer;white-space:nowrap}
.bmk-to-btn:hover{border-color:var(--input-text,#ddd)}
.bmk-to-check{display:inline-flex;align-items:center;gap:3px;cursor:pointer;white-space:nowrap}
.bmk-to-check input{margin:0;accent-color:var(--p-primary-color,#5b8def)}
.bmk-to-warn{flex:0 0 auto;padding:3px 6px;border-radius:5px;background:color-mix(in srgb,#d4a017 20%,transparent)}
.bmk-to-warn.err{background:color-mix(in srgb,#e05555 25%,transparent)}
.bmk-to-warn a{color:var(--p-primary-color,#5b8def);cursor:pointer;text-decoration:underline}
.bmk-to-scroll{overflow:auto;min-height:40px;border:1px solid var(--border-color,#4e4e4e);border-radius:6px;background:color-mix(in srgb,var(--comfy-input-bg,#222) 70%,transparent)}
.bmk-to-canvas{padding:6px 6px 16px 0}
.bmk-to-row{display:flex;align-items:flex-start;margin-bottom:8px}
.bmk-to-gutter{flex:0 0 24px;box-sizing:border-box;padding-right:6px;text-align:right;line-height:24px;color:var(--descrip-text,#999);font-variant-numeric:tabular-nums}
.bmk-to-chunks{flex:1 1 auto;min-width:0;display:flex;flex-wrap:wrap;align-items:flex-start;gap:5px}
.bmk-to-chunk{display:inline-flex;flex-wrap:wrap;align-items:center;gap:4px;max-width:100%;box-sizing:border-box;min-height:24px;padding:2px 8px;border:1px solid color-mix(in srgb,var(--c) 65%,transparent);border-radius:6px;background:color-mix(in srgb,var(--c) 18%,var(--comfy-input-bg,#222));cursor:grab}
.bmk-to-chunk.off{background:transparent;border-style:dashed;color:var(--descrip-text,#999)}
.bmk-to-chunk.off .bmk-to-label{text-decoration:line-through}
.bmk-to-chunk.hl{box-shadow:0 0 0 2px color-mix(in srgb,var(--c) 85%,transparent)}
.bmk-to-chunk.dragging,.bmk-to-member.dragging{opacity:.35}
.bmk-to-head{display:inline-flex;align-items:center;gap:5px;line-height:18px;white-space:nowrap}
.bmk-to-badge{padding:0 5px;border-radius:8px;font-size:10px;line-height:14px;background:rgba(255,255,255,.14)}
.bmk-to-badge.zero{opacity:.5}
.bmk-to-arrow{width:12px;text-align:center;cursor:pointer;opacity:.75}
.bmk-to-arrow:hover{opacity:1}
.bmk-to-members{display:flex;flex-wrap:wrap;gap:3px}
.bmk-to-member{padding:0 7px;border:1px solid color-mix(in srgb,var(--c) 65%,transparent);border-radius:9px;background:color-mix(in srgb,var(--c) 32%,var(--comfy-input-bg,#222));font-size:11px;line-height:17px;white-space:nowrap;cursor:grab}
.bmk-to-note{padding:10px;color:var(--descrip-text,#999);text-align:center}
.bmk-to-split{flex:0 0 5px;margin:-2px 0;border-radius:3px;cursor:ns-resize}
.bmk-to-split:hover{background:var(--border-color,#4e4e4e)}
.bmk-to-preview{padding:5px 7px;line-height:1.65;user-select:text;word-break:break-word}
.bmk-to-line{margin:0}
.bmk-to-line.brk{color:var(--descrip-text,#999);font-weight:600}
.bmk-to-grp{padding:0 1px;border-radius:3px;background:color-mix(in srgb,var(--c) 16%,transparent);-webkit-box-decoration-break:clone;box-decoration-break:clone}
.bmk-to-grp.hl{background:color-mix(in srgb,var(--c) 40%,transparent)}
.bmk-to-tok.rm{opacity:.38;text-decoration:line-through}
.bmk-to-tok.unk{color:#ff7b7b}
.bmk-to-tok.gs{font-style:italic}
.bmk-to-tok.inj{font-weight:700}
.bmk-to-tok.fl{text-decoration:underline wavy #d4a017}
.bmk-to-status{flex:0 0 auto;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--descrip-text,#999)}
.bmk-to-ghost{position:fixed;z-index:10000;pointer-events:none;padding:1px 8px;border:1px solid var(--c);border-radius:6px;background:color-mix(in srgb,var(--c) 35%,#222);color:#eee;font:12px/18px Inter,Arial,sans-serif;white-space:nowrap;opacity:.92}
.bmk-to-drop{position:fixed;z-index:9999;pointer-events:none;border-radius:2px;background:var(--p-primary-color,#5b8def)}
.bmk-to-dropbox{position:fixed;z-index:9999;pointer-events:none;box-sizing:border-box;border:2px solid var(--p-primary-color,#5b8def);border-radius:7px}
.bmk-to-menu{position:fixed;z-index:10001;min-width:170px;max-height:70vh;overflow:auto;padding:4px 0;border:1px solid var(--border-color,#4e4e4e);border-radius:6px;background:var(--comfy-menu-bg,#353535);color:var(--input-text,#ddd);font:12px/1.4 Inter,Arial,sans-serif;box-shadow:0 4px 14px rgba(0,0,0,.45);user-select:none}
.bmk-to-menu .it{padding:4px 14px;white-space:nowrap;cursor:pointer}
.bmk-to-menu .it:hover,.bmk-to-menu .it.open{background:var(--comfy-input-bg,#222)}
.bmk-to-menu .it.has-sub::after{content:"▸";float:right;margin-left:14px;opacity:.7}
.bmk-to-menu hr{margin:4px 0;border:0;border-top:1px solid var(--border-color,#4e4e4e)}
.bmk-to-overlay{position:fixed;inset:0;z-index:10002;display:flex;align-items:center;justify-content:center;background:rgba(0,0,0,.45)}
.bmk-to-dialog{box-sizing:border-box;width:min(480px,92vw);max-height:88vh;overflow:auto;display:flex;flex-direction:column;gap:8px;padding:14px 16px;border:1px solid var(--border-color,#4e4e4e);border-radius:8px;background:var(--comfy-menu-bg,#353535);color:var(--input-text,#ddd);font:13px/1.45 Inter,Arial,sans-serif}
.bmk-to-dialog h3{margin:0 0 2px;font-size:14px}
.bmk-to-dialog .fld{display:flex;flex-direction:column;gap:3px;font-size:12px;color:var(--descrip-text,#999)}
.bmk-to-dialog input[type=text],.bmk-to-dialog textarea{box-sizing:border-box;width:100%;padding:4px 6px;border:1px solid var(--border-color,#4e4e4e);border-radius:5px;background:var(--comfy-input-bg,#222);color:var(--input-text,#ddd);font:12px/1.4 ui-monospace,Consolas,monospace}
.bmk-to-dialog textarea{min-height:84px;resize:vertical}
.bmk-to-dialog .inline{display:flex;align-items:center;gap:8px;color:var(--input-text,#ddd)}
.bmk-to-dialog input[type=color]{width:42px;height:24px;padding:0;border:0;background:none}
.bmk-to-dialog .hint{font-size:11px;color:var(--descrip-text,#999)}
.bmk-to-dialog .err{min-height:1em;font-size:12px;color:#ff7b7b}
.bmk-to-dialog .btns{display:flex;justify-content:flex-end;gap:6px}
`;

function injectStyle() {
  if (document.getElementById("bmk-to-style")) return;
  const el = document.createElement("style");
  el.id = "bmk-to-style";
  el.textContent = STYLE;
  document.head.appendChild(el);
}

function checkState() {
  try {
    app.extensionManager?.workflow?.activeWorkflow?.changeTracker?.checkState?.();
  } catch {
    // undo 기록은 선택 사항
  }
}

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}

function clone(v) {
  return v == null ? v : JSON.parse(JSON.stringify(v));
}

// "a, b" 글이나 목록 → 항목 목록(코어가 받는 두 형식 그대로)
function splitItems(value) {
  const items = typeof value === "string" ? value.split(/[,\n]/) : Array.isArray(value) ? value : value == null ? [] : [value];
  return items.map((v) => String(v).trim()).filter(Boolean);
}

function lines(text) {
  return String(text).split("\n").map((s) => s.trim()).filter(Boolean);
}

function slug(label) {
  return String(label).trim().replace(/[^\p{L}\p{N}_\-]+/gu, "_").replace(/^_+|_+$/g, "") || "arrangement";
}

// ─── 선택자·표시 이름 (코어 Selector·chunk_label·chunk_color 와 같은 규칙) ───

function parseSel(text) {
  const raw = String(text).trim().toLowerCase();
  let sel = raw;
  let identity = false;
  if (sel.endsWith("@identity")) {
    identity = true;
    sel = sel.slice(0, -"@identity".length);
  }
  const i = sel.indexOf("/");
  return {
    text: raw,
    main: (i < 0 ? sel : sel.slice(0, i)).trim(),
    sub: (i < 0 ? "" : sel.slice(i + 1)).trim(),
    identity,
  };
}

function selMatches(s, category, identity = false) {
  if (s.main === "*") return !s.identity || identity;
  const i = category.indexOf("/");
  const main = i < 0 ? category : category.slice(0, i);
  const sub = i < 0 ? "" : category.slice(i + 1);
  if (main !== s.main) return false;
  if (s.sub && sub !== s.sub) return false;
  return !s.identity || identity;
}

class Taxonomy {
  constructor(palette) {
    this.categories = palette?.categories ?? [];
    this.meta = new Map();
    for (const c of this.categories) {
      if (c.system && this.meta.has(c.id)) continue; // 코어 _category_meta 의 setdefault
      this.meta.set(c.id, {
        label: c.label || c.id,
        color: c.color || "",
        subs: new Map((c.subs ?? []).map((s) => [s.id, s.label || s.id])),
      });
    }
  }

  selectorLabel(text) {
    const s = parseSel(text);
    const meta = this.meta.get(s.main);
    let label;
    if (s.main === "*") label = "전부";
    else if (!meta) label = s.text;
    else if (s.sub) label = meta.subs.get(s.sub) ?? s.sub;
    else label = meta.label;
    return label + (s.identity ? " (고유 외형)" : "");
  }

  defaultLabel(sels) {
    if (!sels.length) return "";
    if (sels.length === 1) return this.selectorLabel(sels[0].text);
    const mains = [...new Set(sels.map((s) => s.main))];
    return mains.slice(0, 2).map((m) => (m === "*" ? "전부" : this.meta.get(m)?.label ?? m)).join("·");
  }

  defaultColor(sels) {
    for (const s of sels) {
      const c = this.meta.get(s.main)?.color;
      if (c) return c;
    }
    return this.meta.get("etc")?.color || "#888888";
  }

  chunkLabel(spec) {
    return String(spec?.label || "") || this.defaultLabel(splitItems(spec?.members).map(parseSel));
  }

  chunkColor(spec) {
    const c = String(spec?.color || "");
    return COLOR.test(c) ? c : this.defaultColor(splitItems(spec?.members).map(parseSel));
  }

  // 배치할 수 있는 칸 전부(코어 category_slots)
  slots() {
    const out = [];
    for (const c of this.categories) {
      if (c.system) continue;
      for (const s of c.subs ?? []) out.push(`${c.id}/${s.id}`);
      out.push(c.id);
    }
    return out.concat(SYSTEM);
  }

  // 어느 청크에도 맞지 않는 칸(그 태그는 맨 끝 rest 로 감) — 코어 uncovered_slots
  uncovered(model) {
    const sels = model.flat().flatMap((cid) => model.members(cid).map(parseSel)).filter((s) => !s.identity);
    return this.slots().filter((slot) => !sels.some((s) => selMatches(s, slot)));
  }
}

// ─── 배열 모델 (gui/arrangement.py Model 을 옮김. 바꾸는 메서드는 바뀌었으면 true 또는 새 id) ───

class Model {
  constructor(data) {
    this.load(data);
  }

  load(data) {
    const d = clone(data ?? {}) ?? {};
    this.label = d.label ? String(d.label) : "";
    this.subOrder = d.sub_order === undefined ? true : Boolean(d.sub_order);
    this.guess = Array.isArray(d.guess) ? d.guess.map((g) => ({ ...g })) : [];
    this.chunks = new Map();
    for (const [cid, spec0] of Object.entries(d.chunks && typeof d.chunks === "object" ? d.chunks : {})) {
      const spec = { ...(spec0 && typeof spec0 === "object" ? spec0 : {}) };
      for (const key of ["members", "tags", "patterns"]) if (key in spec) spec[key] = splitItems(spec[key]);
      this.chunks.set(String(cid), spec);
    }
    this.rows = (Array.isArray(d.rows) ? d.rows : []).map((r) =>
      (typeof r === "string" ? [r] : Array.isArray(r) ? r : []).map(String),
    );
    const placed = new Set(this.flat());
    for (const cid of this.chunks.keys()) if (!placed.has(cid)) this.rows.push([cid]);
    this.tidy();
  }

  toJSON() {
    const out = {
      format: FORMAT,
      version: VERSION,
      label: this.label,
      sub_order: this.subOrder,
      rows: this.rows.map((r) => [...r]),
      chunks: Object.fromEntries([...this.chunks].map(([k, v]) => [k, clone(v)])),
    };
    if (this.guess.length) out.guess = clone(this.guess);
    return out;
  }

  dumps() {
    return JSON.stringify(this.toJSON());
  }

  flat() {
    return this.rows.flat();
  }

  where(cid) {
    for (let r = 0; r < this.rows.length; r++) {
      const p = this.rows[r].indexOf(cid);
      if (p >= 0) return [r, p];
    }
    throw new Error(`청크가 없습니다: ${cid}`);
  }

  members(cid) {
    return [...(this.chunks.get(cid)?.members ?? [])];
  }

  isCustom(cid) {
    const spec = this.chunks.get(cid) ?? {};
    return Boolean(spec.custom || spec.tags?.length || spec.patterns?.length);
  }

  // ---- 청크 옮기기·줄바꿈 ----

  moveChunk(cid, row, pos) {
    const [r0, p0] = this.where(cid);
    if (r0 === row && (pos === p0 || pos === p0 + 1)) return false;
    if (r0 === row && p0 < pos) pos -= 1;
    this.rows[r0].splice(p0, 1);
    this.rows[row].splice(pos, 0, cid);
    this.tidy();
    return true;
  }

  moveChunkNewRow(cid, row) {
    const [r0, p0] = this.where(cid);
    if (this.rows[r0].length === 1 && (row === r0 || row === r0 + 1)) return false;
    this.rows[r0].splice(p0, 1);
    this.rows.splice(row, 0, [cid]);
    this.tidy();
    return true;
  }

  breakBefore(cid) {
    const [r, p] = this.where(cid);
    if (p === 0) return false;
    this.rows.splice(r, 1, this.rows[r].slice(0, p), this.rows[r].slice(p));
    return true;
  }

  breakAfter(cid) {
    const [r, p] = this.where(cid);
    if (p === this.rows[r].length - 1) return false;
    this.rows.splice(r, 1, this.rows[r].slice(0, p + 1), this.rows[r].slice(p + 1));
    return true;
  }

  joinPrevious(cid) {
    const [r] = this.where(cid);
    if (r === 0) return false;
    this.rows[r - 1].push(...this.rows.splice(r, 1)[0]);
    return true;
  }

  // ---- 멤버(서브) ----

  moveMember(src, mi, dst, di) {
    if (src === dst && (di === mi || di === mi + 1)) return false;
    const member = this.chunks.get(src).members.splice(mi, 1)[0];
    if (src === dst && mi < di) di -= 1;
    const d = this.chunks.get(dst);
    d.members ??= [];
    d.members.splice(di, 0, member);
    if (src === dst) this.subOrder = true;
    this.tidy();
    return true;
  }

  // 멤버를 빼내 새 청크로. pos 가 null 이면 rows[row] 앞에 새 줄, 아니면 그 줄의 pos 자리
  detachMember(src, mi, row, pos = null) {
    const member = this.chunks.get(src).members[mi];
    const cid = this.newId(member.replace(/\//g, "."));
    this.chunks.set(cid, { members: [member] });
    if (pos == null) this.rows.splice(row, 0, [cid]);
    else this.rows[row].splice(pos, 0, cid);
    this.chunks.get(src).members.splice(mi, 1);
    this.tidy();
    return cid;
  }

  detachAfter(src, mi) {
    const [r, p] = this.where(src);
    return this.detachMember(src, mi, r, p + 1);
  }

  mergeInto(src, dst) {
    if (src === dst) return false;
    const a = this.chunks.get(src);
    const b = this.chunks.get(dst);
    b.members ??= [];
    for (const m of a.members ?? []) if (!b.members.includes(m)) b.members.push(m);
    for (const key of ["tags", "patterns"]) {
      if (a[key]?.length) {
        b[key] = [...new Set([...(b[key] ?? []), ...a[key]])];
        b.custom = true;
      }
    }
    this.remove(src);
    return true;
  }

  addSlots(slots) {
    if (!slots.length) return null;
    const cid = this.newId("more");
    this.chunks.set(cid, { label: "빠진 분류", members: [...slots] });
    this.rows.push([cid]);
    return cid;
  }

  // ---- 청크 속성 ----

  toggleHidden(cid) {
    const spec = this.chunks.get(cid);
    if (spec.hidden) delete spec.hidden;
    else spec.hidden = true;
    return true;
  }

  setLabel(cid, label) {
    const spec = this.chunks.get(cid);
    label = String(label).trim();
    if (label === (spec.label ?? "")) return false;
    if (label) spec.label = label;
    else delete spec.label;
    return true;
  }

  setColor(cid, color) {
    const spec = this.chunks.get(cid);
    if (color === (spec.color ?? "")) return false;
    if (color) spec.color = color;
    else delete spec.color;
    return true;
  }

  // 새 커스텀 청크를 혼자인 새 줄로(row 가 null 이면 맨 위 — 보통 품질·내 태그를 앞에 둔다)
  addCustom(label, color = "", tags = [], patterns = [], row = null) {
    const cid = this.newId("custom");
    this.chunks.set(cid, { custom: true });
    this.updateCustom(cid, label, color, tags, patterns);
    this.rows.splice(row ?? 0, 0, [cid]);
    return cid;
  }

  updateCustom(cid, label, color, tags, patterns) {
    const spec = this.chunks.get(cid);
    const before = JSON.stringify(spec);
    spec.custom = true;
    for (const [key, values] of [["tags", tags], ["patterns", patterns]]) {
      const v = [...new Set(values.map((s) => s.trim()).filter(Boolean))];
      if (v.length) spec[key] = v;
      else delete spec[key];
    }
    this.setLabel(cid, label);
    this.setColor(cid, color);
    return JSON.stringify(spec) !== before;
  }

  // 커스텀 청크를 지운다. 안에 옮겨 둔 멤버가 있으면 멤버만 남긴 일반 청크로 둔다
  deleteChunk(cid) {
    const spec = this.chunks.get(cid);
    if (!this.isCustom(cid)) return false;
    if (spec.members?.length) {
      for (const key of ["custom", "tags", "patterns"]) delete spec[key];
    } else {
      this.remove(cid);
    }
    return true;
  }

  // ---- 내부 ----

  remove(cid) {
    this.chunks.delete(cid);
    this.rows = this.rows.map((r) => r.filter((c) => c !== cid));
    this.tidy();
  }

  newId(base) {
    base = base.replace(ID_TRIM, "_").replace(/^[_.\-]+|[_.\-]+$/g, "") || "chunk";
    let cid = base;
    for (let n = 2; this.chunks.has(cid); n++) cid = `${base}${n}`;
    return cid;
  }

  // 없는 id·두 번 놓인 id·빈 줄을 치우고, 멤버가 없는 일반 청크는 지운다
  tidy() {
    for (const [cid, spec] of [...this.chunks]) if (!spec.members?.length && !this.isCustom(cid)) this.chunks.delete(cid);
    const seen = new Set();
    const rows = [];
    for (const row of this.rows) {
      const kept = row.filter((c) => this.chunks.has(c) && !seen.has(c) && seen.add(c));
      if (kept.length) rows.push(kept);
    }
    this.rows = rows;
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

// ─── 메뉴·대화상자 ───

let openMenus = [];

function closeMenus() {
  for (const m of openMenus) m.remove();
  openMenus = [];
  window.removeEventListener("pointerdown", onMenuOutside, true);
  window.removeEventListener("keydown", onMenuKey, true);
}

function onMenuOutside(e) {
  if (!openMenus.some((m) => m.contains(e.target))) closeMenus();
}

function onMenuKey(e) {
  if (e.key === "Escape") {
    e.stopPropagation();
    closeMenus();
  }
}

function placeMenu(menu, x, y) {
  document.body.appendChild(menu);
  const r = menu.getBoundingClientRect();
  menu.style.left = `${Math.max(4, Math.min(x, innerWidth - r.width - 4))}px`;
  menu.style.top = `${Math.max(4, Math.min(y, innerHeight - r.height - 4))}px`;
}

// items: {label, action} | {label, submenu: items} | "-"
function buildMenu(items, depth) {
  const menu = el("div", "bmk-to-menu");
  for (const item of items) {
    if (item === "-") {
      menu.appendChild(el("hr"));
      continue;
    }
    const row = el("div", "it", item.label);
    if (item.submenu) {
      row.classList.add("has-sub");
      const open = () => {
        for (const m of openMenus.splice(depth + 1)) m.remove();
        for (const c of menu.children) c.classList.toggle("open", c === row);
        if (!item.submenu.length) return;
        const sub = buildMenu(item.submenu, depth + 1);
        openMenus.push(sub);
        const r = row.getBoundingClientRect();
        placeMenu(sub, r.right - 2, r.top - 4);
      };
      row.addEventListener("pointerenter", open);
      row.addEventListener("click", open);
    } else {
      row.addEventListener("pointerenter", () => {
        for (const m of openMenus.splice(depth + 1)) m.remove();
        for (const c of menu.children) c.classList.remove("open");
      });
      row.addEventListener("click", () => {
        closeMenus();
        item.action();
      });
    }
    menu.appendChild(row);
  }
  return menu;
}

function showMenu(x, y, items) {
  closeMenus();
  const menu = buildMenu(items.filter(Boolean), 0);
  openMenus.push(menu);
  placeMenu(menu, x, y);
  window.addEventListener("pointerdown", onMenuOutside, true);
  window.addEventListener("keydown", onMenuKey, true);
}

// build(panel) 가 칸을 넣고 값 읽는 함수를 돌려준다. onOk(값) 은 오류 글(남기면 닫지 않음) 또는 null
function openDialog(title, build, onOk, okText = "확인") {
  const overlay = el("div", "bmk-to-overlay");
  const panel = el("div", "bmk-to-dialog");
  panel.appendChild(el("h3", "", title));
  const read = build(panel);
  const err = el("div", "err");
  const btns = el("div", "btns");
  const cancel = el("button", "bmk-to-btn", "취소");
  const ok = el("button", "bmk-to-btn", okText);
  btns.append(cancel, ok);
  panel.append(err, btns);
  overlay.appendChild(panel);
  const close = () => overlay.remove();
  const submit = async () => {
    ok.disabled = true;
    err.textContent = "";
    try {
      const message = await onOk(read());
      if (message) err.textContent = message;
      else close();
    } catch (e) {
      err.textContent = String(e.message || e);
    } finally {
      ok.disabled = false;
    }
  };
  cancel.addEventListener("click", close);
  ok.addEventListener("click", submit);
  overlay.addEventListener("pointerdown", (e) => {
    if (e.target === overlay) close();
  });
  // 대화상자 안의 키는 ComfyUI 단축키(Delete·Ctrl+Z 등)로 가지 않게 여기서 끊는다
  overlay.addEventListener("keydown", (e) => {
    e.stopPropagation();
    if (e.key === "Escape") close();
    else if (e.key === "Enter" && e.target.tagName === "INPUT") submit();
  });
  document.body.appendChild(overlay);
  panel.querySelector("input[type=text],textarea")?.focus();
}

function field(panel, label, input) {
  const box = el("label", "fld", label);
  box.appendChild(input);
  panel.appendChild(box);
  return input;
}

function textInput(value, placeholder = "") {
  const input = el("input");
  input.type = "text";
  input.spellcheck = false;
  input.value = value ?? "";
  input.placeholder = placeholder;
  return input;
}

function textArea(value, placeholder = "", rows = 4) {
  const area = el("textarea");
  area.spellcheck = false;
  area.rows = rows;
  area.value = value ?? "";
  area.placeholder = placeholder;
  return area;
}

// ─── 편집기 ───

class ArrangementEditor {
  constructor(node) {
    this.node = node;
    this.data = null;           // /palette 응답
    this.tax = null;
    this.model = null;
    this.fromLayout = true;     // 위젯이 비어 있다(노드는 layout 을 씀)
    this.loadError = "";
    this.paletteError = "";
    this.flash = "";
    this.expanded = new Set();
    this.counts = new Map();    // 청크 id → {kept, removed, names}
    this.report = null;
    this.previewError = "";
    this.lastInput = null;      // 마지막 실행의 입력(text 를 연결했을 때)
    this.lastArr = undefined;
    this.lastLayout = undefined;
    this.lastSig = "";
    this.seq = 0;
    this.timer = 0;
    this.flashTimer = 0;
    this.drag = null;
    this.split = Number(node.properties?.bmk_to_split) || 0.55;

    this.root = el("div", "bmk-to-root");
    this.root.innerHTML = `
      <div class="bmk-to-bar">
        <span class="bmk-to-state"></span>
        <label class="bmk-to-check" title="켜면 청크 안 태그를 펼친 서브 순서로 놓습니다. 끄면 입력 순서"><input type="checkbox" data-act="suborder">서브 순서</label>
        <button type="button" class="bmk-to-btn" data-act="custom" title="태그·패턴으로 고른 토큰을 모으는 청크">+ 커스텀</button>
        <button type="button" class="bmk-to-btn" data-act="import" title="관리 툴에서 내보낸 배열 세팅 JSON 파일">가져오기</button>
        <button type="button" class="bmk-to-btn" data-act="export" title="관리 툴·Studio·사내 툴에서 쓸 배열 세팅 JSON 파일로 저장">내보내기</button>
        <button type="button" class="bmk-to-btn" data-act="more" title="이름·JSON·레이아웃으로·펼치기">⋯</button>
      </div>
      <div class="bmk-to-warn" hidden></div>
      <div class="bmk-to-scroll bmk-to-canvas"></div>
      <div class="bmk-to-split" title="끌어서 청크·미리보기 높이 나누기"></div>
      <div class="bmk-to-scroll bmk-to-preview"></div>
      <div class="bmk-to-status"></div>`;
    this.stateEl = this.root.querySelector(".bmk-to-state");
    this.subOrderBox = this.root.querySelector("[data-act=suborder]");
    this.warnEl = this.root.querySelector(".bmk-to-warn");
    this.canvasEl = this.root.querySelector(".bmk-to-canvas");
    this.splitEl = this.root.querySelector(".bmk-to-split");
    this.previewEl = this.root.querySelector(".bmk-to-preview");
    this.statusEl = this.root.querySelector(".bmk-to-status");
    this.fileInput = el("input");
    this.fileInput.type = "file";
    this.fileInput.accept = ".json,application/json";
    this.fileInput.addEventListener("change", () => this.importFile());
    this.applySplit();
    this.bind();

    loadPalette().then(
      (data) => this.setPalette(data),
      (e) => {
        this.paletteError = `분류를 불러오지 못했습니다: ${e.message || e}`;
        this.render();
      },
    );
    this.render();
    this.poll = setInterval(() => this.tick(), POLL_MS);
  }

  destroy() {
    clearInterval(this.poll);
    clearTimeout(this.timer);
    clearTimeout(this.flashTimer);
    this.endDrag(false);
  }

  // ---- 노드 위젯 ----

  widget(name) {
    return this.node.widgets?.find((w) => w.name === name);
  }

  linked(name) {
    return this.node.inputs?.find((i) => i.name === name)?.link != null;
  }

  arrValue() {
    const v = this.widget(ARR)?.value;
    return typeof v === "string" ? v : "";
  }

  layoutId() {
    const ids = Object.keys(this.data?.layouts ?? {});
    const v = this.widget("layout")?.value;
    return ids.includes(v) ? v : ids[0];
  }

  values() {
    const out = {};
    for (const key of OPTION_KEYS) {
      const w = this.widget(key);
      if (w) out[key] = w.value;
    }
    return out;
  }

  inputText() {
    if (this.linked("text")) return this.lastInput;
    const v = this.widget("text")?.value;
    return typeof v === "string" ? v : "";
  }

  locked() {
    return this.linked(ARR);
  }

  // ---- 모델 ----

  setPalette(data) {
    this.data = data;
    this.tax = new Taxonomy(data.palette);
    this.paletteError = "";
    this.lastArr = undefined;
    this.syncFromWidget();
    this.lastSig = "";
    this.tick();
  }

  // 위젯 값(불러오기·Ctrl+Z·붙여넣기로 바뀐 배열, 비어 있을 때의 layout)에 모델을 맞춘다
  syncFromWidget(force = false) {
    if (!this.tax) return;
    const arr = this.arrValue();
    const layout = this.layoutId();
    if (!force && arr === this.lastArr && (arr.trim() || layout === this.lastLayout)) return;
    this.lastArr = arr;
    this.lastLayout = layout;
    this.loadError = "";
    if (!arr.trim()) {
      this.fromLayout = true;
      this.model = new Model(this.data.layouts?.[layout] ?? {});
    } else {
      this.fromLayout = false;
      try {
        const data = JSON.parse(arr);
        if (!data || typeof data !== "object" || Array.isArray(data)) throw new Error("JSON 객체가 아닙니다");
        this.model = new Model(data);
      } catch (e) {
        this.model = null;
        this.loadError = `배열 JSON 을 읽지 못했습니다: ${e.message || e}`;
      }
    }
    this.render();
  }

  setArr(value) {
    const w = this.widget(ARR);
    if (!w) return;
    this.lastArr = value;
    this.lastLayout = this.layoutId();
    if (w.value !== value) {
      w.value = value;
      w.callback?.(value);
    }
    this.fromLayout = !value.trim();
    this.node.setDirtyCanvas?.(true, true);
    checkState();
    this.schedulePreview();
  }

  // 모델을 바꾸고 위젯에 쓴다. 관리 툴 _apply 처럼 한 번 더 읽어 정리 규칙을 똑같이 적용한다
  edit(fn) {
    if (!this.model) return undefined;
    if (this.locked()) {
      this.showFlash("arrangement 입력이 연결돼 있어 편집기로 바꿀 수 없습니다.");
      return undefined;
    }
    const before = this.model.dumps();
    let result;
    try {
      result = fn(this.model);
    } catch (e) {
      console.warn("[BMK TagOrganizer] 배열 편집 실패:", e);
      this.model.load(JSON.parse(before));
      return undefined;
    }
    this.model.load(this.model.toJSON());
    if (this.model.dumps() !== before) this.setArr(JSON.stringify(this.model.toJSON()));
    this.render();
    return result;
  }

  resetToLayout() {
    if (this.locked()) return this.showFlash("arrangement 입력이 연결돼 있습니다.");
    this.setArr("");
    this.syncFromWidget(true);
  }

  // 관리 툴에서 내보낸 파일·붙여넣은 JSON. 코어로 검사한 뒤 관리 툴처럼 정리한 모델을 쓴다
  async applyJSON(text) {
    if (!text.trim()) {
      this.resetToLayout();
      return null;
    }
    let data;
    try {
      data = JSON.parse(text);
    } catch (e) {
      return `JSON 을 읽지 못했습니다: ${e.message}`;
    }
    if (!data || typeof data !== "object" || Array.isArray(data) || (data.format ?? FORMAT) !== FORMAT) {
      return `배열 세팅 파일이 아닙니다(format 이 ${FORMAT} 가 아님).`;
    }
    const model = new Model(data);
    const json = JSON.stringify(model.toJSON());
    const err = await validateArrangement(json);
    if (err) return err;
    if (this.locked()) return "arrangement 입력이 연결돼 있습니다.";
    this.model = model;
    this.setArr(json);
    this.render();
    return null;
  }

  async importFile() {
    const file = this.fileInput.files?.[0];
    this.fileInput.value = "";
    if (!file) return;
    const err = await this.applyJSON(await file.text());
    this.showFlash(err ? `가져오지 못했습니다: ${err}` : `가져옴: ${file.name}`, Boolean(err));
  }

  exportFile() {
    if (!this.model) return;
    const blob = new Blob([JSON.stringify(this.model.toJSON(), null, 2) + "\n"], { type: "application/json" });
    const a = el("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${slug(this.model.label || "arrangement")}.json`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  }

  // ---- 표시 이름 ----

  chunkLabel(cid) {
    const label = this.tax.chunkLabel(this.model.chunks.get(cid)) || cid;
    return this.model.isCustom(cid) ? `★ ${label}` : label;
  }

  memberLabel(cid, member) {
    let label = this.tax.selectorLabel(member);
    const s = parseSel(member);
    if (!s.sub && s.main !== "*" && this.model.members(cid).some((m) => {
      const o = parseSel(m);
      return o.main === s.main && o.sub;
    })) {
      label += " 그 밖"; // 서브 없이 메인만 붙은 태그(와 다른 청크에 없는 서브)
    }
    return label;
  }

  // ---- 미리보기 ----

  signature() {
    return JSON.stringify([this.inputText(), this.values(), this.arrValue()]);
  }

  tick() {
    if (!this.root.isConnected) return;
    this.syncFromWidget();
    const sig = this.signature();
    if (sig !== this.lastSig) {
      this.lastSig = sig;
      this.schedulePreview();
    }
    const locked = this.locked();
    if (locked !== this.wasLocked) {
      this.wasLocked = locked;
      this.render();
    }
  }

  schedulePreview() {
    clearTimeout(this.timer);
    this.timer = setTimeout(() => this.runPreview(), PREVIEW_DELAY);
  }

  async runPreview() {
    const seq = ++this.seq;
    const text = this.inputText();
    if (text == null) {
      this.report = null;
      this.previewError = "";
      this.counts = new Map();
      this.render();
      return;
    }
    let data;
    try {
      data = await postPreview({ text, ...this.values(), arrangement: this.arrValue() });
    } catch (e) {
      if (seq !== this.seq) return;
      this.report = null;
      this.counts = new Map();
      this.previewError = String(e.message || e);
      this.render();
      return;
    }
    if (seq !== this.seq) return;
    this.report = data;
    this.previewError = "";
    this.counts = new Map();
    for (const t of data.tokens ?? []) {
      const c = this.counts.get(t.section) ?? { kept: 0, removed: 0, names: [] };
      if (t.removed) c.removed++;
      else c.kept++;
      c.names.push((t.removed ? "× " : "") + t.text);
      this.counts.set(t.section, c);
    }
    this.render();
  }

  showFlash(text, error = true) {
    this.flash = text;
    this.flashError = error;
    clearTimeout(this.flashTimer);
    this.flashTimer = setTimeout(() => {
      this.flash = "";
      this.renderWarn();
    }, 6000);
    this.renderWarn();
  }

  // ---- 그리기 ----

  render() {
    this.renderBar();
    this.renderWarn();
    if (!this.drag) this.renderCanvas(); // 끄는 중에 온 미리보기는 놓은 뒤 그린다
    this.renderPreview();
    this.renderStatus();
  }

  renderBar() {
    const m = this.model;
    const layoutLabel = this.data?.palette?.layouts?.[this.layoutId()] ?? this.layoutId() ?? "";
    this.stateEl.replaceChildren();
    if (this.locked()) {
      this.stateEl.append("arrangement 입력 연결됨");
    } else if (this.fromLayout) {
      this.stateEl.append("레이아웃 ", el("b", "", layoutLabel), " 에서 시작 — 바꾸면 배열로 저장");
    } else {
      this.stateEl.append("배열 ", el("b", "", m?.label || "(이름 없음)"));
    }
    this.stateEl.title = this.fromLayout
      ? "arrangement 가 비어 있어 노드는 layout 을 씁니다. 청크를 바꾸면 그 배열이 arrangement 에 저장됩니다."
      : "arrangement 위젯의 배열 세팅을 씁니다(layout 은 쓰지 않음). ⋯ → 레이아웃으로 되돌리기";
    this.subOrderBox.checked = Boolean(m?.subOrder);
    this.subOrderBox.disabled = !m;
  }

  renderWarn() {
    let text = this.flash;
    let error = this.flashError;
    let action = null;
    if (!text) {
      error = true;
      text = this.paletteError || this.loadError;
      if (!text && this.locked()) {
        error = false;
        text = "arrangement 입력이 연결돼 있어 실행은 그 값을 씁니다(아래 청크는 위젯 값).";
      }
      if (!text && this.model && this.tax) {
        const missing = this.tax.uncovered(this.model);
        if (missing.length) {
          error = false;
          text = `어느 청크에도 없는 분류: ${missing.slice(0, 8).join(", ")}${missing.length > 8 ? " …" : ""} — 맨 끝 '나머지' 줄로 갑니다. `;
          action = ["새 청크로 넣기", () => this.edit((mm) => mm.addSlots(missing))];
        }
      }
    }
    const was = !this.warnEl.hidden;
    this.warnEl.hidden = !text;
    this.warnEl.classList.toggle("err", Boolean(error));
    this.warnEl.replaceChildren(text || "");
    if (action) {
      const a = el("a", "", action[0]);
      a.addEventListener("click", action[1]);
      this.warnEl.appendChild(a);
    }
    if (was !== Boolean(text)) this.node.setDirtyCanvas?.(true, true);
  }

  renderCanvas() {
    const box = this.canvasEl;
    if (!this.model || !this.tax) {
      box.replaceChildren(el("div", "bmk-to-note", this.paletteError ? "—" : this.loadError ? "배열을 읽지 못했습니다" : "분류를 불러오는 중…"));
      return;
    }
    for (const cid of [...this.expanded]) if (!this.model.chunks.has(cid)) this.expanded.delete(cid);
    if (!this.model.rows.length) {
      box.replaceChildren(el("div", "bmk-to-note", "청크가 없습니다 — 우클릭 → 새 커스텀 청크"));
      return;
    }
    const rows = this.model.rows.map((row, ri) => {
      const rowEl = el("div", "bmk-to-row");
      rowEl.dataset.row = ri;
      const chunks = el("div", "bmk-to-chunks");
      row.forEach((cid, pi) => chunks.appendChild(this.chunkEl(cid, ri, pi)));
      rowEl.append(el("span", "bmk-to-gutter", String(ri + 1)), chunks);
      return rowEl;
    });
    box.replaceChildren(...rows);
  }

  chunkEl(cid, ri, pi) {
    const spec = this.model.chunks.get(cid);
    const box = el("div", "bmk-to-chunk");
    box.dataset.cid = cid;
    box.dataset.row = ri;
    box.dataset.pos = pi;
    box.style.setProperty("--c", this.tax.chunkColor(spec));
    box.classList.toggle("off", Boolean(spec.hidden));
    const head = el("span", "bmk-to-head");
    head.appendChild(el("span", "bmk-to-label", this.chunkLabel(cid)));
    const count = this.counts.get(cid);
    if (count) {
      const badge = el("span", "bmk-to-badge", String(count.kept));
      badge.classList.toggle("zero", !count.kept);
      head.appendChild(badge);
    }
    const members = this.model.members(cid);
    const open = this.expanded.has(cid) && members.length > 0;
    if (members.length) {
      const arrow = el("span", "bmk-to-arrow", open ? "◂" : "▸");
      arrow.title = open ? "접기" : "펼치기 — 서브(멤버) 순서 바꾸기·빼내기";
      head.appendChild(arrow);
    }
    box.appendChild(head);
    if (open) {
      const list = el("div", "bmk-to-members");
      members.forEach((m, mi) => {
        const pill = el("span", "bmk-to-member", this.memberLabel(cid, m));
        pill.dataset.cid = cid;
        pill.dataset.mi = mi;
        pill.style.setProperty("--c", this.tax.chunkColor({ members: [m] }));
        pill.title = `${this.memberLabel(cid, m)} — ${m}\n끌어서 순서 바꾸기·다른 청크로·밖으로(새 청크)`;
        list.appendChild(pill);
      });
      box.appendChild(list);
    }
    const tip = [`${this.chunkLabel(cid)}  (${cid})`];
    if (members.length) tip.push("분류: " + members.join(", "));
    if (spec.tags?.length) tip.push("태그: " + spec.tags.slice(0, 12).join(", ") + (spec.tags.length > 12 ? " …" : ""));
    if (spec.patterns?.length) tip.push("패턴: " + spec.patterns.join(", "));
    if (spec.hidden) tip.push("숨김: 이 청크의 태그를 뺍니다");
    if (count) {
      tip.push(`이 프롬프트: 남음 ${count.kept} · 뺌 ${count.removed}`);
      tip.push(count.names.slice(0, 12).join(", ") + (count.names.length > 12 ? " …" : ""));
    }
    tip.push("끌어서 옮기기 · 더블클릭 펼치기 · 우클릭 메뉴");
    box.title = tip.join("\n");
    return box;
  }

  renderPreview() {
    const box = this.previewEl;
    const rep = this.report;
    if (!rep) {
      const note = this.linked("text") && this.lastInput == null
        ? "text 가 연결돼 있습니다 — 한 번 실행하면 그 입력으로 미리 봅니다."
        : this.previewError ? "" : "미리보기를 만드는 중…";
      box.replaceChildren(el("div", "bmk-to-note", note));
      return;
    }
    const tokens = rep.tokens ?? [];
    const out = [];
    (rep.arranged ?? []).forEach((section, si) => {
      if (si > 0) out.push(el("div", "bmk-to-line brk", "BREAK"));
      for (const row of section.rows ?? []) {
        const line = el("div", "bmk-to-line");
        row.forEach((chunk, ci) => {
          if (ci) line.append(", ");
          const grp = el("span", "bmk-to-grp");
          grp.dataset.cid = chunk.id;
          grp.style.setProperty("--c", COLOR.test(chunk.color || "") ? chunk.color : this.tax?.defaultColor([]) ?? "#888888");
          chunk.tokens.forEach((ti, k) => {
            const t = tokens[ti];
            if (!t) return;
            if (k) grp.append(", ");
            const tok = el("span", "bmk-to-tok", t.out || t.text);
            tok.classList.toggle("rm", Boolean(t.removed));
            tok.classList.toggle("fl", Boolean(t.flagged) && !t.removed);
            tok.classList.toggle("gs", Boolean(t.guess));
            tok.classList.toggle("inj", Boolean(t.injected));
            tok.classList.toggle("unk", t.kind === "unknown" && !t.guess && !chunk.custom);
            const tip = [t.text + (t.primary ? `  · ${t.primary}` : ""), `청크: ${chunk.label}`];
            if (t.removed) tip.push(`뺌: ${t.removed}`);
            if (t.flagged) tip.push(`표시: ${t.flagged}`);
            if (t.guess) tip.push(`추정: ${t.guess}`);
            if (t.injected) tip.push(`덮어씀: ${t.injected}`);
            if (t.kind === "unknown" && !t.guess) tip.push("팩에 없는 토큰");
            tok.title = tip.join("\n");
            grp.appendChild(tok);
          });
          line.appendChild(grp);
        });
        out.push(line);
      }
    });
    if (!out.length) out.push(el("div", "bmk-to-note", "(빈 프롬프트)"));
    box.replaceChildren(...out);
  }

  renderStatus() {
    const s = this.statusEl;
    if (this.previewError) {
      s.textContent = `미리보기 오류: ${this.previewError}`;
      s.title = this.previewError;
      s.style.color = "#ff7b7b";
      return;
    }
    s.style.color = "";
    const c = this.report?.counts;
    s.textContent = c
      ? `토큰 ${c.total} · 남음 ${c.kept} · 뺌 ${c.removed} · 표시 ${c.flagged} · 미분류 ${c.unclassified} · 팩에 없음 ${c.unknown}` +
        (this.widget("guess")?.value ? ` · 추정 ${c.guessed}` : "")
      : "";
    s.title = "미리보기: 밝게 = 남음 · 흐리게 = 뺌 · 빨강 = 팩에 없음 · 기울임 = 추정 · 굵게 = 덮어씀 · 물결 밑줄 = 표시만";
  }

  highlight(cid, on) {
    if (!cid) return;
    for (const e of this.root.querySelectorAll(`[data-cid="${CSS.escape(cid)}"]`)) {
      if (e.classList.contains("bmk-to-chunk") || e.classList.contains("bmk-to-grp")) e.classList.toggle("hl", on);
    }
  }

  applySplit() {
    this.canvasEl.style.flex = `${this.split} 1 0`;
    this.previewEl.style.flex = `${1 - this.split} 1 0`;
  }

  toggle(cid) {
    if (this.expanded.has(cid)) this.expanded.delete(cid);
    else if (this.model.members(cid).length) this.expanded.add(cid);
    this.renderCanvas();
  }

  setAllExpanded(on) {
    this.expanded = on ? new Set([...this.model.chunks.keys()].filter((c) => this.model.members(c).length)) : new Set();
    this.renderCanvas();
  }

  // ---- 이벤트 ----

  bind() {
    const root = this.root;
    // 편집기 안의 조작이 캔버스(노드 끌기·선택)로 가지 않게
    root.addEventListener("pointerdown", (e) => e.stopPropagation());
    // 스크롤할 수 있는 칸이면 그 칸을, 아니면 캔버스 줌/이동으로 넘긴다
    root.addEventListener(
      "wheel",
      (e) => {
        const sc = e.target.closest?.(".bmk-to-scroll");
        const can = sc && (e.deltaY < 0 ? sc.scrollTop > 0 : sc.scrollTop + sc.clientHeight < sc.scrollHeight - 1);
        if (can && !e.ctrlKey) {
          e.stopPropagation();
          return;
        }
        e.preventDefault();
        e.stopPropagation();
        app.canvas?.processMouseWheel?.(e);
      },
      { passive: false },
    );
    root.addEventListener("contextmenu", (e) => this.onContextMenu(e));

    this.root.querySelector(".bmk-to-bar").addEventListener("click", (e) => {
      const act = e.target.closest("[data-act]")?.dataset.act;
      if (act === "custom") this.chunkDialog(null);
      else if (act === "import") this.fileInput.click();
      else if (act === "export") this.exportFile();
      else if (act === "more") {
        const r = e.target.getBoundingClientRect();
        showMenu(r.left, r.bottom + 2, this.moreItems());
      }
    });
    this.subOrderBox.addEventListener("change", () => {
      const on = this.subOrderBox.checked;
      this.edit((m) => {
        m.subOrder = on;
      });
    });

    const c = this.canvasEl;
    c.addEventListener("pointerdown", (e) => this.onPointerDown(e));
    c.addEventListener("dblclick", (e) => {
      const chunk = e.target.closest(".bmk-to-chunk");
      if (!chunk || e.target.closest(".bmk-to-member") || !this.model) return;
      const cid = chunk.dataset.cid;
      if (!this.model.members(cid).length && this.model.isCustom(cid)) this.chunkDialog(cid);
      else this.toggle(cid);
    });
    const hover = (e, on) => {
      const t = e.target.closest?.(".bmk-to-chunk, .bmk-to-grp");
      if (t) this.highlight(t.dataset.cid, on);
    };
    for (const box of [c, this.previewEl]) {
      box.addEventListener("mouseover", (e) => hover(e, true));
      box.addEventListener("mouseout", (e) => hover(e, false));
    }

    this.splitEl.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      const y0 = e.clientY;
      const a = this.canvasEl.getBoundingClientRect().height;
      const b = this.previewEl.getBoundingClientRect().height;
      const s0 = this.split;
      const move = (ev) => {
        const total = a + b;
        if (total <= 0) return;
        this.split = Math.min(0.85, Math.max(0.15, s0 + (ev.clientY - y0) / total));
        this.applySplit();
      };
      const up = () => {
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", up);
        this.node.properties ??= {};
        this.node.properties.bmk_to_split = Number(this.split.toFixed(3));
      };
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", up);
    });
  }

  moreItems() {
    const m = this.model;
    return [
      m && { label: "배열 이름…", action: () => this.renameArrangement() },
      { label: "JSON 보기·붙여넣기…", action: () => this.jsonDialog() },
      !this.fromLayout && { label: "레이아웃으로 되돌리기 (arrangement 비우기)", action: () => this.resetToLayout() },
      "-",
      m && { label: "모두 펼치기", action: () => this.setAllExpanded(true) },
      m && { label: "모두 접기", action: () => this.setAllExpanded(false) },
      "-",
      {
        label: "팩 다시 읽기(분류·색)",
        action: () =>
          loadPalette(true).then(
            (d) => this.setPalette(d),
            (e) => this.showFlash(`분류를 불러오지 못했습니다: ${e.message || e}`),
          ),
      },
    ];
  }

  // ---- 끌기 ----

  onPointerDown(e) {
    if (e.button !== 0 || !this.model) return;
    const member = e.target.closest(".bmk-to-member");
    const chunk = e.target.closest(".bmk-to-chunk");
    if (!member && chunk && e.target.closest(".bmk-to-arrow")) {
      e.preventDefault();
      this.toggle(chunk.dataset.cid);
      return;
    }
    const src = member
      ? { cid: member.dataset.cid, mi: Number(member.dataset.mi), el: member }
      : chunk
        ? { cid: chunk.dataset.cid, mi: -1, el: chunk }
        : null;
    if (!src) return;
    e.preventDefault();
    const x0 = e.clientX;
    const y0 = e.clientY;
    let started = false;
    const move = (ev) => {
      if (!started) {
        if (Math.abs(ev.clientX - x0) + Math.abs(ev.clientY - y0) < 5) return;
        if (this.locked()) return;
        started = true;
        this.beginDrag(src);
      }
      this.dragMove(ev);
    };
    const finish = (apply) => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("keydown", key, true);
      if (started) this.endDrag(apply);
    };
    const up = () => finish(true);
    const key = (ev) => {
      if (ev.key === "Escape") {
        ev.stopPropagation();
        finish(false);
      }
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    window.addEventListener("keydown", key, true);
  }

  beginDrag(src) {
    src.el.classList.add("dragging");
    const label = src.mi < 0 ? this.chunkLabel(src.cid) : this.memberLabel(src.cid, this.model.members(src.cid)[src.mi]);
    const ghost = el("div", "bmk-to-ghost", label);
    ghost.style.setProperty("--c", src.el.style.getPropertyValue("--c"));
    const bar = el("div", "bmk-to-drop");
    const box = el("div", "bmk-to-dropbox");
    bar.hidden = box.hidden = true;
    document.body.append(ghost, bar, box);
    this.drag = { src, ghost, bar, box, target: null };
  }

  endDrag(apply) {
    const d = this.drag;
    if (!d) return;
    this.drag = null;
    d.ghost.remove();
    d.bar.remove();
    d.box.remove();
    d.src.el.classList.remove("dragging");
    if (apply && d.target) this.applyDrop(d.src, d.target);
  }

  dragMove(ev) {
    const d = this.drag;
    if (!d) return;
    d.ghost.style.left = `${ev.clientX + 10}px`;
    d.ghost.style.top = `${ev.clientY + 6}px`;
    d.target = this.dropTarget(ev.clientX, ev.clientY);
    this.showDrop(d.target);
    const r = this.canvasEl.getBoundingClientRect();
    if (ev.clientY < r.top + 20) this.canvasEl.scrollTop -= 12;
    else if (ev.clientY > r.bottom - 20) this.canvasEl.scrollTop += 12;
  }

  // 한 줄 안 끼울 자리: 점이 있는(가장 가까운) 시각 줄에서 가운데가 점보다 오른쪽인 첫 상자 앞
  static linePos(els, x, y, index, count) {
    if (!els.length) return count;
    const items = els.map((e) => ({ e, r: e.getBoundingClientRect() }));
    const lineOf = new Map();
    for (const it of items) {
      const top = Math.round(it.r.top);
      const line = lineOf.get(top) ?? { top, bottom: top, items: [] };
      line.bottom = Math.max(line.bottom, it.r.bottom);
      line.items.push(it);
      lineOf.set(top, line);
    }
    const dist = (l) => (y >= l.top && y <= l.bottom ? 0 : Math.min(Math.abs(y - l.top), Math.abs(y - l.bottom)));
    const line = [...lineOf.values()].reduce((a, b) => (dist(b) < dist(a) ? b : a));
    const same = line.items.sort((a, b) => a.r.left - b.r.left);
    for (const it of same) if (it.r.left + it.r.width / 2 > x) return index(it.e);
    return index(same[same.length - 1].e) + 1;
  }

  dropTarget(x, y) {
    const src = this.drag.src;
    if (src.mi >= 0) {
      const over = document.elementFromPoint(x, y)?.closest?.(".bmk-to-chunk");
      if (over && this.canvasEl.contains(over)) {
        const cid = over.dataset.cid;
        const pills = [...over.querySelectorAll(".bmk-to-member")];
        const n = this.model.members(cid).length;
        const di = pills.length ? ArrangementEditor.linePos(pills, x, y, (p) => Number(p.dataset.mi), n) : n;
        return { kind: "member", cid, di, el: over };
      }
    }
    const rows = [...this.canvasEl.querySelectorAll(".bmk-to-row")];
    const scale = this.root.getBoundingClientRect().width / (this.root.offsetWidth || 1) || 1;
    for (const [ri, rowEl] of rows.entries()) {
      const r = rowEl.getBoundingClientRect();
      const edge = Math.min(EDGE * scale, r.height / 4);
      if (y < r.top + edge) return { kind: "newrow", row: ri };
      if (y <= r.bottom - edge) {
        const chunks = [...rowEl.querySelectorAll(".bmk-to-chunks > .bmk-to-chunk")];
        return { kind: "row", row: ri, pos: ArrangementEditor.linePos(chunks, x, y, (c) => Number(c.dataset.pos), chunks.length) };
      }
    }
    return { kind: "newrow", row: rows.length };
  }

  showDrop(t) {
    const { bar, box } = this.drag;
    bar.hidden = box.hidden = true;
    if (!t) return;
    const place = (e, left, top, width, height) => {
      Object.assign(e.style, { left: `${left}px`, top: `${top}px`, width: `${width}px`, height: `${height}px` });
      e.hidden = false;
    };
    const vbar = (els, pos) => {
      if (!els.length) return;
      const r = (pos < els.length ? els[pos] : els[els.length - 1]).getBoundingClientRect();
      place(bar, (pos < els.length ? r.left - 3 : r.right + 1), r.top - 2, 2, r.height + 4);
    };
    if (t.kind === "member") {
      const r = t.el.getBoundingClientRect();
      place(box, r.left - 2, r.top - 2, r.width + 4, r.height + 4);
      vbar([...t.el.querySelectorAll(".bmk-to-member")].sort((a, b) => a.dataset.mi - b.dataset.mi), t.di);
      return;
    }
    const rows = [...this.canvasEl.querySelectorAll(".bmk-to-row")];
    if (t.kind === "row") {
      vbar([...rows[t.row].querySelectorAll(".bmk-to-chunks > .bmk-to-chunk")], t.pos);
      return;
    }
    const c = this.canvasEl.getBoundingClientRect();
    let y;
    if (!rows.length) y = c.top + 6;
    else if (t.row < rows.length) {
      const r = rows[t.row].getBoundingClientRect();
      const prev = t.row > 0 ? rows[t.row - 1].getBoundingClientRect().bottom : r.top - 6;
      y = (prev + r.top) / 2;
    } else y = rows[rows.length - 1].getBoundingClientRect().bottom + 3;
    place(bar, c.left + 24, y - 1, Math.max(20, c.width - 34), 2);
  }

  applyDrop(src, t) {
    if (src.mi < 0) {
      if (t.kind === "row") this.edit((m) => m.moveChunk(src.cid, t.row, t.pos));
      else this.edit((m) => m.moveChunkNewRow(src.cid, t.row));
    } else if (t.kind === "member") {
      this.edit((m) => m.moveMember(src.cid, src.mi, t.cid, t.di));
    } else {
      this.edit((m) => m.detachMember(src.cid, src.mi, t.row, t.kind === "row" ? t.pos : null));
    }
  }

  // ---- 메뉴 ----

  onContextMenu(e) {
    e.preventDefault();
    e.stopPropagation();
    if (!this.model) return;
    const member = e.target.closest(".bmk-to-member");
    const chunk = e.target.closest(".bmk-to-chunk");
    const m = this.model;
    const items = [];
    if (member) {
      const cid = member.dataset.cid;
      const mi = Number(member.dataset.mi);
      items.push(
        { label: "새 청크로 분리", action: () => this.edit((mm) => mm.detachAfter(cid, mi)) },
        {
          label: "다른 청크로 옮기기",
          submenu: m.flat().filter((o) => o !== cid).map((o) => ({
            label: this.chunkLabel(o),
            action: () => this.edit((mm) => mm.moveMember(cid, mi, o, mm.members(o).length)),
          })),
        },
      );
    } else if (chunk) {
      const cid = chunk.dataset.cid;
      const spec = m.chunks.get(cid);
      if (m.members(cid).length) {
        items.push({ label: this.expanded.has(cid) ? "접기" : "펼치기", action: () => this.toggle(cid) });
      }
      items.push(
        { label: "앞에서 줄바꿈", action: () => this.edit((mm) => mm.breakBefore(cid)) },
        { label: "뒤에서 줄바꿈", action: () => this.edit((mm) => mm.breakAfter(cid)) },
        { label: "앞 줄과 합치기", action: () => this.edit((mm) => mm.joinPrevious(cid)) },
        "-",
        { label: spec.hidden ? "보이기" : "숨기기 — 이 청크의 태그를 뺌", action: () => this.edit((mm) => mm.toggleHidden(cid)) },
        { label: m.isCustom(cid) ? "커스텀 청크 편집…" : "이름·색 바꾸기…", action: () => this.chunkDialog(cid) },
      );
      if (spec.color) items.push({ label: "분류 색으로", action: () => this.edit((mm) => mm.setColor(cid, "")) });
      items.push({
        label: "다른 청크에 합치기",
        submenu: m.flat().filter((o) => o !== cid).map((o) => ({
          label: this.chunkLabel(o),
          action: () => this.edit((mm) => mm.mergeInto(cid, o)),
        })),
      });
      if (m.isCustom(cid)) items.push({ label: "커스텀 청크 지우기", action: () => this.edit((mm) => mm.deleteChunk(cid)) });
      items.push("-", { label: "새 커스텀 청크…", action: () => this.chunkDialog(null) });
    } else {
      items.push({ label: "새 커스텀 청크…", action: () => this.chunkDialog(null) }, "-", ...this.moreItems());
    }
    showMenu(e.clientX, e.clientY, items);
  }

  // ---- 대화상자 ----

  // cid 가 null 이면 새 커스텀 청크. 일반 청크는 이름·색만, 커스텀 청크는 태그·패턴도
  chunkDialog(cid) {
    if (!this.model || !this.tax) return;
    if (this.locked()) return this.showFlash("arrangement 입력이 연결돼 있습니다.");
    const m = this.model;
    const isNew = cid == null;
    const spec = isNew ? { custom: true } : m.chunks.get(cid);
    const custom = isNew || m.isCustom(cid);
    const defLabel = isNew ? "" : this.tax.chunkLabel({ members: m.members(cid) });
    const own = COLOR.test(spec.color || "") ? spec.color : "";
    const title = isNew ? "새 커스텀 청크" : custom ? "커스텀 청크" : "청크 이름·색";
    openDialog(
      title,
      (panel) => {
        const name = field(panel, "이름", textInput(spec.label ?? "", defLabel || "예: 내 의상 묘사"));
        const colorRow = el("div", "inline");
        const color = el("input");
        color.type = "color";
        color.value = own || this.tax.chunkColor(isNew ? { members: [] } : { members: m.members(cid) });
        const useDefault = el("input");
        useDefault.type = "checkbox";
        useDefault.checked = !own;
        const defBox = el("label", "inline");
        defBox.append(useDefault, "분류 색");
        color.addEventListener("input", () => {
          useDefault.checked = false;
        });
        colorRow.append(el("span", "", "색"), color, defBox);
        panel.appendChild(colorRow);
        let tags = null;
        let patterns = null;
        if (custom) {
          tags = field(panel, "태그", textArea((spec.tags ?? []).join("\n"),
            "한 줄에 하나(쉼표로 여럿도 됨)\nintricate detailed clothes\nhigh-tech material", 5));
          patterns = field(panel, "패턴", textArea((spec.patterns ?? []).join("\n"), "* armor\nre:^cyber.*", 3));
          panel.appendChild(el("div", "hint",
            "태그와 패턴에 맞는 토큰은 분류와 상관없이 이 청크로 갑니다(팩에 없는 토큰도). " +
            "* 는 한 글자 이상, re: 로 시작하면 정규식(소문자·공백으로 정규화한 이름에 전체 일치)."));
        }
        return () => ({
          label: name.value.trim(),
          color: useDefault.checked ? "" : color.value,
          tags: tags ? splitItems(tags.value) : [],
          patterns: patterns ? lines(patterns.value) : [],
        });
      },
      async (v) => {
        if (custom && !v.tags.length && !v.patterns.length && !v.label) return "이름이나 태그를 적으세요.";
        const trial = new Model(m.toJSON());
        if (isNew) trial.addCustom(v.label || "내 태그", v.color, v.tags, v.patterns);
        else if (custom) trial.updateCustom(cid, v.label, v.color, v.tags, v.patterns);
        else {
          trial.setLabel(cid, v.label === defLabel ? "" : v.label);
          trial.setColor(cid, v.color);
        }
        const err = await validateArrangement(JSON.stringify(trial.toJSON()));
        if (err) return err;
        this.edit((mm) => mm.load(trial.toJSON()));
        return null;
      },
    );
  }

  renameArrangement() {
    if (this.locked()) return this.showFlash("arrangement 입력이 연결돼 있습니다.");
    openDialog(
      "배열 이름",
      (panel) => {
        const name = field(panel, "이름(report 의 arrangement·내보낼 파일 이름)", textInput(this.model.label, "내 배열"));
        return () => name.value.trim();
      },
      async (label) => {
        this.edit((mm) => {
          mm.label = label;
        });
        return null;
      },
    );
  }

  jsonDialog() {
    const current = this.model ? JSON.stringify(this.model.toJSON(), null, 2) : this.arrValue();
    openDialog(
      "배열 세팅 JSON",
      (panel) => {
        const area = field(panel, "관리 툴에서 내보낸 JSON 을 붙여 넣을 수 있습니다. 비우고 적용하면 레이아웃으로 돌아갑니다.",
          textArea(current, "", 16));
        area.style.minHeight = "260px";
        return () => area.value;
      },
      (text) => this.applyJSON(text),
      "적용",
    );
  }
}

app.registerExtension({
  name: "BMK.TagOrganizer",

  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData.name !== NODE_ID) return;

    const onNodeCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const r = onNodeCreated?.apply(this, arguments);
      injectStyle();
      const arr = this.widgets?.find((w) => w.name === ARR);
      if (arr) arr.hidden = true; // 입력 옵션 hidden 을 모르는 프론트에서도 숨긴다
      const editor = new ArrangementEditor(this);
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
        editor.lastInput = text;
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
