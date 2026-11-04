// 배열 세팅 편집 규칙: 선택자·표시 이름(코어 Selector·chunk_label·chunk_color)과 배열 모델(gui/arrangement.py 의 Model).
// DOM 을 쓰지 않아 node 로 시험한다. tests/test_extension_core.py 가 같은 조작을 Python Model 과 비교한다.
// 노드 팩의 js/tag_organizer/ 로 복사되므로 import 만으로 아무 일도 하지 않아야 한다(editor.js 머리 주석).

export const FORMAT = "tagorg-arrangement";
export const VERSION = 1;
const SYSTEM = ["etc", "unknown", "special", "natural"];   // 코어 SYSTEM_CATEGORIES
const KNOWN_KEYS = new Set(["format", "version", "label", "description", "sub_order", "rows", "chunks", "guess"]);
const ID_TRIM = /[^A-Za-z0-9_.\-]+/g;
export const COLOR = /^#[0-9a-fA-F]{6}$/;

export function clone(v) {
  return v == null ? v : JSON.parse(JSON.stringify(v));
}

// "a, b" 글이나 목록 → 항목 목록(코어가 받는 두 형식 그대로)
export function splitItems(value) {
  const items = typeof value === "string" ? value.split(/[,\n]/) : Array.isArray(value) ? value : value == null ? [] : [value];
  return items.map((v) => String(v).trim()).filter(Boolean);
}

// 시선 칩을 눌렀을 때의 새 선택(퀵 목록 순서). 켜면 함께 쓸 수 없는 태그(gaze_conflicts, 양방향)를 끈다.
export function gazeToggle(paletteData, selected, tag) {
  let on = new Set(selected);
  if (on.has(tag)) {
    on.delete(tag);
  } else {
    const conflicts = paletteData.gaze_conflicts || {};
    const off = new Set(conflicts[tag] || []);
    for (const [other, tags] of Object.entries(conflicts)) if (tags.includes(tag)) off.add(other);
    on = new Set([...on].filter((t) => !off.has(t)));
    on.add(tag);
  }
  const order = paletteData.quick.gaze;
  const rank = (t) => (order.includes(t) ? order.indexOf(t) : order.length);
  return [...on].sort((a, b) => rank(a) - rank(b));
}

// 시점 칩을 눌렀을 때의 새 선택(퀵 목록 순서). 켠 태그와 부딪치는 태그(view_conflicts, 양방향)를 끈다.
// view_lift 태그가 켜져 있거나 lifted(report 의 view_lifted)면 부딪치지 않는다. lift 를 끄면 부딪치는 태그 중 목록의 앞쪽만 남긴다.
export function viewToggle(paletteData, selected, tag, lifted = false) {
  const conflicts = paletteData.view_conflicts || {};
  const order = paletteData.quick.view;
  const rank = (t) => (order.includes(t) ? order.indexOf(t) : order.length);
  const clash = (a, b) => (conflicts[a] || []).includes(b) || (conflicts[b] || []).includes(a);
  const adding = !selected.includes(tag);
  let on = adding ? [...new Set([...selected, tag])] : selected.filter((t) => t !== tag);
  on.sort((a, b) => rank(a) - rank(b));
  if (lifted || on.some((t) => (paletteData.view_lift || []).includes(t))) return on;
  if (adding) on = on.filter((t) => t === tag || !clash(t, tag));
  const kept = [];
  for (const t of on) if (!kept.some((k) => clash(t, k))) kept.push(t);
  return kept;
}

export function lines(text) {
  return String(text).split("\n").map((s) => s.trim()).filter(Boolean);
}

export function slug(label) {
  return String(label).trim().replace(/[^\p{L}\p{N}_\-]+/gu, "_").replace(/^_+|_+$/g, "") || "arrangement";
}

// ─── 선택자·표시 이름 (코어 Selector·chunk_label·chunk_color 와 같은 규칙) ───

export function parseSel(text) {
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

export function selMatches(s, category, identity = false) {
  if (s.main === "*") return !s.identity || identity;
  const i = category.indexOf("/");
  const main = i < 0 ? category : category.slice(0, i);
  const sub = i < 0 ? "" : category.slice(i + 1);
  if (main !== s.main) return false;
  if (s.sub && sub !== s.sub) return false;
  return !s.identity || identity;
}

export class Taxonomy {
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

export class Model {
  constructor(data) {
    this.load(data);
  }

  load(data) {
    const d = clone(data ?? {}) ?? {};
    this.label = d.label ? String(d.label) : "";
    this.description = d.description ? String(d.description) : "";
    this.extra = Object.fromEntries(Object.entries(d).filter(([k]) => !KNOWN_KEYS.has(k)));
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
      ...(this.description ? { description: this.description } : {}),
      sub_order: this.subOrder,
      rows: this.rows.map((r) => [...r]),
      chunks: Object.fromEntries([...this.chunks].map(([k, v]) => [k, clone(v)])),
    };
    if (this.guess.length) out.guess = clone(this.guess);
    return { ...out, ...clone(this.extra) };
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

  // rows[row] 를 통째로 rows[to] 앞으로(옮기기 전 기준, to == 줄 수면 맨 끝). 빈 줄 표시는 청크를 따라간다
  moveRow(row, to) {
    if (!(row >= 0 && row < this.rows.length) || !(to >= 0 && to <= this.rows.length) || to === row || to === row + 1) return false;
    const [moved] = this.rows.splice(row, 1);
    this.rows.splice(row < to ? to - 1 : to, 0, moved);
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

  toggleGapAfter(cid) {
    const spec = this.chunks.get(cid);
    if (spec.gap_after) delete spec.gap_after;
    else {
      spec.gap_after = true;
      this.breakAfter(cid);
    }
    return true;
  }

  toggleGapBefore(cid) {
    const flat = this.flat();
    const i = flat.indexOf(cid);
    return i > 0 ? this.toggleGapAfter(flat[i - 1]) : false;
  }

  hasGapBefore(cid) {
    const flat = this.flat();
    const i = flat.indexOf(cid);
    return i > 0 && Boolean(this.chunks.get(flat[i - 1]).gap_after);
  }

  // rows[row] 위에 빈 줄을 놓는다(row == 줄 수면 맨 아래). 맨 위는 앞 청크가 없어 둘 수 없다
  addGapAt(row) {
    if (!(row >= 1 && row <= this.rows.length)) return false;
    const spec = this.chunks.get(this.rows[row - 1].at(-1));
    if (spec.gap_after) return false;
    spec.gap_after = true;
    return true;
  }

  // 아직 빈 줄이 없는 가장 아래 자리에 빈 줄을 놓고 그 자리를 돌려준다(둘 곳이 없으면 null)
  addGapBottom() {
    for (let row = this.rows.length; row > 0; row--) if (this.addGapAt(row)) return row;
    return null;
  }

  // rows[row] 아래의 빈 줄을 rows[to] 위로. 거기에 이미 빈 줄이 있으면 그대로 둔다
  moveGap(row, to) {
    if (!(row >= 0 && row < this.rows.length)) return false;
    const spec = this.chunks.get(this.rows[row].at(-1));
    if (!spec.gap_after || !this.addGapAt(to)) return false;
    delete spec.gap_after;
    return true;
  }

  joinPrevious(cid) {
    const [r] = this.where(cid);
    if (r === 0) return false;
    delete this.chunks.get(this.rows[r - 1].at(-1)).gap_after;
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

// ─── 추정 규칙 글 (한 줄에 `패턴 => 분류 | 부위 부위 | identity | rating=s`, gui/arrange_panel.py 와 같은 형식) ───

export function formatGuessRule(rule) {
  let text = `${rule.pattern ?? ""} => ${rule.primary ?? ""}`;
  const regions = splitItems(rule.regions);   // 코어처럼 "a, b" 글도 받는다
  if (regions.length) text += ` | ${regions.join(" ")}`;
  if (rule.identity) text += " | identity";
  if (rule.rating) text += ` | rating=${rule.rating}`;
  return text;
}

export function parseGuessRules(text) {
  const rules = [];
  String(text).split(/\r?\n/).forEach((raw, index) => {
    const line = raw.trim();
    if (!line || line.startsWith("#")) return;
    const at = line.indexOf("=>");
    if (at < 0) throw new Error(`${index + 1}번째 줄: '패턴 => 분류' 형식이 아닙니다: ${line}`);
    const parts = line.slice(at + 2).split("|").map((p) => p.trim());
    const rule = { pattern: line.slice(0, at).trim(), primary: parts[0] };
    for (const extra of parts.slice(1)) {
      const words = extra.replace(/,/g, " ").split(/\s+/).filter(Boolean);
      if (words.length === 1 && words[0] === "identity") rule.identity = true;
      else if (words.length === 1 && words[0].startsWith("rating=")) rule.rating = words[0].slice("rating=".length);
      else if (words.length) rule.regions = words;
    }
    rules.push(rule);
  });
  return rules;
}
