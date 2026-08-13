// BMK Tabbed Notes — frontend widget (v7, Notes 마크다운 프리뷰)
//
// 모든 노트는 서버의 노트 폴더(ComfyUI/user/bmk_notes — 중첩 카테고리
// 폴더/.../탭.json)에 저장되고, 프론트는 경로를 모른 채 HTTP API로만 접근한다.
// 캔버스의 모든 BMK Tabbed Notes 노드가 하나의 공유 스토어를 본다.
// 워크플로우에는 선택 상태(활성 카테고리/탭, 사이드바 너비)만 저장된다.
//
//  - v7: Notes 마크다운 프리뷰 — 섹션 헤더의 👁 토글로 편집(기본) ↔ 렌더
//    보기를 전환한다. 렌더러는 의존성 0의 자체 미니 구현(MD-RENDER 블록):
//    헤딩/굵게·기울임·취소선·인라인코드/링크(새 탭)/GFM 표(정렬)/중첩 리스트/
//    인용/수평선/코드펜스. 원문 HTML은 전부 이스케이프(태그 주입 불가).
//    프리뷰 상태는 노트별 doc.ui.md({key:true})에 저장돼 같은 노트를 보는
//    모든 노드가 상태를 공유한다. 프리뷰는 높이 auto(내용 흐름) — 긴 노트는
//    섹션 패널 스크롤로 읽는다. 복사 버튼은 계속 "원문(마크다운 소스)"을 복사.
//    ※ 백엔드 v8과 짝 배포 필수 — 구버전 py의 _normalize_ui가 ui.md를
//    깎아낸다(프리뷰 상태만 소실, 문서 내용은 안전).
//  - v6: UI 정리 — ① 유선 출력 포트 삭제(무선 전송으로 대체됨. 구버전
//    워크플로우가 직렬화한 출력 슬롯도 로드 시 자동 제거). ② 전송/Params
//    시스템 메시지를 브레드크럼 줄 오른쪽 통합 상태줄로 이동 — 레이아웃이
//    들썩이지 않는다. ③ Params 헤더 툴바 1줄 통합(좁으면 flex-wrap).
//    ④ 파라미터 행 3줄 → 2줄: "→ 대상 노드" 캡션을 1행 제목 오른쪽으로.
//    ⑤ 행의 전송/가져오기 버튼을 SVG 아이콘으로 교체. ⑥ 새 탭은
//    prompt→negative→loras→params→notes 순서(ui.order, 서버가 기록).
//    ⑦ 수정: rgthree Fast Muter/Bypasser 토글에 bool 전송 시 값이 고정되지
//    않고 매번 반전되던 문제 — 해당 위젯의 callback은 인자를 무시하는
//    "클릭 토글"이라, doModeChange(force)를 직접 호출하도록 변경.
//    ※ 백엔드 v7과 짝 배포 필수.
//  - v5: Params 섹션 — 노트별 doc.params에 워크플로우 파라미터
//    {label, node, widget, type, value, enabled, hint}를 기록해
//    개별(↑)·일괄(⚡, 체크된 행만) 전송하거나, 현재 워크플로우 값을
//    역으로 가져온다(↓). 행 추가는 노드/위젯 피커(#ID → 위젯 목록 선택,
//    타입·현재값·타이틀 자동 캡처)가 기본이며 수동 빈 행도 가능.
//    체크 해제 행은 일괄 전송·복사에서 제외(개별 전송은 가능).
//    프리셋: 서버(노트 루트의 .bmk_param_presets.json)에 저장/삭제하고 모든 노드가
//    트리 응답의 param_presets로 자동 동기화. 불러오기는 "교체"가 기본(확인창).
//    탭 간 이동은 복사/붙여넣기(페이지 전역 인메모리 클립보드, enabled 행만).
//    ※ 백엔드 v6과 짝 배포 필수 — 구버전 py는 params를 깎아낸다.
//
//  - v4: 카테고리는 무제한 중첩. 식별자는 이름이 아니라 루트 기준 경로("a/b/c").
//    헤더의 폴더 버튼으로 하위 카테고리 생성. 카테고리 드래그 시 헤더 3분할:
//    상단¼=위 형제 / 하단¼=아래 형제 / 중앙½=그 카테고리 "안"으로 중첩.
//    상위 카테고리 rename/move 시 서버가 트리에 동봉한 {old,new}로
//    activeCategory·접힘 키의 경로 prefix를 재매핑한다.
//  - 노드에서의 모든 편집은 HTTP API로 즉시(텍스트는 디바운스) 서버에 기록
//  - 같은 페이지의 다른 노드 인스턴스에는 즉시 반영
//  - 외부(탐색기/메모장) 수정은 ↻ 버튼, R키(Refresh Node Definitions),
//    또는 큐 실행 시점(서버가 디스크 직독)에 반영
//  - 구버전(내장형) 데이터가 감지되면 공유 폴더로 가져오기 안내
import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { ComfyWidgets } from "../../scripts/widgets.js";

const NODE_TYPE = "BMKTabbedNotes";
const MIN_NODE_W = 300;
const MIN_NODE_H = 220;
const MIN_SIDEBAR = 64;
const MIN_EDITOR = 110;

// ─── 섹션 헤더 색상 프리셋 (ComfyUI 노드 색 계열) ───────────────
// 섹션 key별 헤더 배경 hex. 내부 텍스트칸 색은 건드리지 않는다.
// 여기 hex만 바꾸면 전체 BMK 노드 헤더 색이 일괄 변경된다.
const SECTION_HEADER_COLORS = {
  prompt: "#2e4d2e",   // 초록 (positive)
  negative: "#4d2e2e", // 빨강 (negative)
  loras: "#2e3b5c",    // 파랑 (lora)
  notes: "#5c4d2a",    // 노랑 (notes)
  params: "#46305c",   // 보라 (workflow params)
};

/* ---------------------------------- utils ---------------------------------- */

const clamp = (v, lo, hi) => Math.min(Math.max(v, lo), hi);

function dedupeName(base, existing) {
  const low = new Set([...existing].map((s) => String(s).toLowerCase()));
  let name = base;
  for (let i = 2; low.has(name.toLowerCase()); i++) name = `${base} (${i})`;
  return name;
}

function sanitizeName(s, fallback) {
  s = String(s ?? "")
    .replace(/[\\/:*?"<>|\x00-\x1F]/g, "_")
    .trim()
    .replace(/^\.+/, "")
    .replace(/[. ]+$/, "")
    .trim();
  return s || fallback;
}

/* ------------------------------ document model ------------------------------ */
// 한 탭(=문서)은 하나의 구조화 객체. 섹션이 없으면 빈 값으로 채워진다.
//   { prompt, negative, loras:[{name, weight, enabled}], notes }

function emptyDoc() {
  return { prompt: "", negative: "", loras: [], notes: "" };
}

// 섹션 표시 메타(ui)에서 다루는 섹션 key의 정본 순서. 출력 포트에는 영향 없음.
// v5: "params" 추가 — 구버전 ui.order(4키)는 누락 보충 규칙으로 params가 맨 뒤에 붙는다.
const SECTION_KEYS = ["prompt", "negative", "loras", "notes", "params"];

// ui(섹션 순서/접힘/프리뷰)를 표준화. 유효 정보가 없으면 undefined.
//  - order: 섹션 key의 순열(중복/미지 제거 후 누락분을 기본순서로 보충). 기본순서면 생략.
//  - collapsed: 알려진 key 중 접힌 것만 {key:true}.
//  - md: 마크다운 프리뷰로 볼 섹션만 {key:true} (v7 — 백엔드 v8이 보존).
function normalizeUi(ui) {
  if (!ui || typeof ui !== "object" || Array.isArray(ui)) return undefined;
  const out = {};
  if (Array.isArray(ui.order)) {
    const seen = [];
    for (const k of ui.order) if (SECTION_KEYS.includes(k) && !seen.includes(k)) seen.push(k);
    const order = seen.concat(SECTION_KEYS.filter((k) => !seen.includes(k)));
    if (order.join(",") !== SECTION_KEYS.join(",")) out.order = order;
  }
  if (ui.collapsed && typeof ui.collapsed === "object" && !Array.isArray(ui.collapsed)) {
    const collapsed = {};
    for (const k of SECTION_KEYS) if (ui.collapsed[k]) collapsed[k] = true;
    if (Object.keys(collapsed).length) out.collapsed = collapsed;
  }
  if (ui.md && typeof ui.md === "object" && !Array.isArray(ui.md)) {
    const md = {};
    for (const k of SECTION_KEYS) if (ui.md[k]) md[k] = true;
    if (Object.keys(md).length) out.md = md;
  }
  return Object.keys(out).length ? out : undefined;
}

// v5: 워크플로우 파라미터 목록 표준화 (Python _normalize_params와 동일 규칙).
//  - node는 문자열로 보관(숫자 입력도 str화), value는 JSON 스칼라만 허용.
//  - 편집 중인 미완성 행도 보존(디바운스 저장 중 행 소실 방지),
//    모든 식별 정보가 빈 완전 빈 행만 버린다.
function normalizeParams(params) {
  if (!Array.isArray(params)) return [];
  const out = [];
  for (const it of params) {
    if (!it || typeof it !== "object" || Array.isArray(it)) continue;
    const label = typeof it.label === "string" ? it.label : "";
    let node = "";
    if (typeof it.node === "string") node = it.node.trim();
    else if (typeof it.node === "number" && Number.isFinite(it.node)) node = String(it.node);
    const widget = typeof it.widget === "string" ? it.widget.trim() : "";
    const type = (typeof it.type === "string" ? it.type.trim().toLowerCase() : "") || "string";
    let value = it.value;
    if (typeof value === "boolean") { /* keep */ }
    else if (typeof value === "number") { if (!Number.isFinite(value)) value = ""; }
    else if (typeof value !== "string") value = "";
    if (!(label.trim() || node || widget || value !== "")) continue; // 완전 빈 행
    out.push({
      label, node, widget, type, value,
      enabled: it.enabled !== false,
      hint: typeof it.hint === "string" ? it.hint : "",
    });
  }
  return out;
}

function normalizeDoc(d) {
  d = d && typeof d === "object" && !Array.isArray(d) ? d : {};
  const loras = Array.isArray(d.loras)
    ? d.loras
        .map((it) => {
          const w = +(it?.weight);
          return {
            name: String(it?.name ?? "").trim(),
            weight: Number.isFinite(w) ? w : 1.0,
            enabled: it?.enabled !== false,
          };
        })
        .filter((it) => it.name)
    : [];
  const out = {
    prompt: typeof d.prompt === "string" ? d.prompt : "",
    negative: typeof d.negative === "string" ? d.negative : "",
    notes: typeof d.notes === "string" ? d.notes : "",
    loras,
  };
  const ui = normalizeUi(d.ui);
  if (ui) out.ui = ui; // UI 표시 메타(순서/접힘) — 있을 때만 보존
  const params = normalizeParams(d.params);
  if (params.length) out.params = params; // v5: 워크플로우 파라미터 — 있을 때만 보존
  return out;
}

function docIsEmpty(doc) {
  if (!doc) return true;
  return !(
    doc.prompt ||
    doc.negative ||
    doc.notes ||
    (Array.isArray(doc.loras) && doc.loras.length) ||
    (Array.isArray(doc.params) && doc.params.length)
  );
}

// 서버 트리(탭에 doc 포함)를 정규화해 항상 일관된 형태로 보관.
// v4: children으로 재귀. 식별자는 루트 기준 경로(path, 예: "a/b/c").
// 서버(v5)가 path를 내려주지만, 없으면 부모 경로로부터 계산한다(방어).
function ingestTree(cats, parentPath = "") {
  return (Array.isArray(cats) ? cats : []).map((c) => {
    const path = typeof c.path === "string" && c.path
      ? c.path
      : (parentPath ? parentPath + "/" + c.name : String(c.name ?? ""));
    return {
      name: c.name,
      path,
      collapsed: !!c.collapsed,
      tabs: (c.tabs || []).map((t) => ({ name: t.name, doc: normalizeDoc(t.doc) })),
      children: ingestTree(c.children, path),
    };
  });
}

function fmtWeight(w) {
  const n = Number.isFinite(+w) ? +w : 1.0;
  return n.toFixed(2);
}

// "<lora:이름:가중치>" 구문 또는 콤마/줄바꿈 구분 이름 목록을 파싱.
function parseLoraSyntax(text) {
  const out = [];
  const re = /<lora:([^:>]+?)(?::(-?\d*\.?\d+))?>/gi;
  let m;
  let found = false;
  while ((m = re.exec(String(text)))) {
    found = true;
    out.push({ name: m[1].trim(), weight: m[2] != null ? +m[2] : 1.0, enabled: true });
  }
  if (!found) {
    for (const part of String(text).split(/[,\n]/)) {
      const name = part.trim();
      if (name) out.push({ name, weight: 1.0, enabled: true });
    }
  }
  return out.filter((x) => x.name);
}

// 활성 로라만 모아 LoraManager 붙여넣기용 문자열로 컴파일 (포트 출력과 동일 규칙).
// LoraManager는 loras 폴더의 서브폴더를 구분하지 않으므로,
// 'Anima\NikkeB1' 같은 경로에서 파일명만 남긴다 → 'NikkeB1'.
// (저장/표시는 전체 경로 유지, 컴파일 출력만 basename)
function loraBasename(name) {
  const parts = String(name).split(/[\\/]/);
  return parts[parts.length - 1] || String(name);
}

function compileLoras(loras) {
  return (loras || [])
    .filter((l) => l.enabled && l.name)
    .map((l) => `<lora:${loraBasename(l.name)}:${fmtWeight(l.weight)}>`)
    .join(", ");
}

/* ------------------------- workflow params (v5) 유틸 ------------------------- */
// 체크된(enabled) 파라미터만 사람용 텍스트로 컴파일 (섹션 복사 버튼용).
// txt 미러 [Params]와 같은 행 형식이되, 결정 사항대로 체크 해제 행은 제외한다.
function compileParams(params) {
  return (params || [])
    .filter((p) => p && p.enabled !== false)
    .map((p) => {
      let v = p.value;
      if (typeof v === "boolean") v = v ? "true" : "false";
      const target = (p.node || p.widget)
        ? ` \u2192 #${p.node || "?"}.${p.widget || "?"}` : "";
      return `${p.label || p.widget || "param"}: ${v ?? ""}${target}`;
    })
    .join("\n");
}

// 콤보 위젯의 옵션 목록. 신형 프론트는 options.values가 함수일 수 있다.
// 형태를 알 수 없으면 null(검증 생략) — 잘못된 차단보다 통과가 안전.
function comboValues(w) {
  let v = w?.options?.values;
  if (typeof v === "function") {
    try { v = v(w); } catch (e) { v = null; }
  }
  return Array.isArray(v) ? v : null;
}

// 위젯의 값 형식 추정 (피커의 타입 자동 감지 + 전송 시 강제 변환 기준).
function detectWidgetType(w) {
  if (!w) return "string";
  if (typeof w.value === "boolean" || w.type === "toggle") return "bool";
  if (w.type === "combo" || comboValues(w)) return "combo";
  if (typeof w.value === "number" || w.type === "number" || w.type === "slider") {
    const prec = w.options?.precision;
    if (prec === 0) return "int";
    if (typeof prec === "number" && prec > 0) return "float";
    return Number.isInteger(w.value) ? "int" : "float";
  }
  return "string";
}

// 위젯 값을 JSON 스칼라로 캡처 (객체 등 비스칼라는 문자열화).
function captureScalar(v) {
  if (typeof v === "boolean" || typeof v === "string") return v;
  if (typeof v === "number" && Number.isFinite(v)) return v;
  return String(v ?? "");
}

// 서버 트리 응답의 param_presets 표준화 (Python _load_param_presets와 동일 규칙:
// 이름 trim, 동명(대소문자 무시) 중복은 앞선 항목만, params는 normalizeParams).
function normalizeParamPresets(list) {
  const out = [];
  const seen = new Set();
  for (const pr of Array.isArray(list) ? list : []) {
    if (!pr || typeof pr !== "object" || Array.isArray(pr)) continue;
    const name = typeof pr.name === "string" ? pr.name.trim() : "";
    if (!name || seen.has(name.toLowerCase())) continue;
    seen.add(name.toLowerCase());
    out.push({ name, params: normalizeParams(pr.params) });
  }
  return out;
}

// 탭 간 파라미터 복사/붙여넣기용 페이지 전역 클립보드.
// 인메모리(모듈 변수)라 같은 페이지의 모든 노드가 공유하고, 새로고침 시 사라진다.
let paramClipboard = null; // 정규화된 행 배열의 깊은 복사본 | null

/* ------------------------------ 주석(//) 처리 ------------------------------ */
// 줄의 첫 비공백 문자가 "//"인 줄(=주석/라벨)을 통째로 제거한다.
//  - ComfyUI Text Multiline의 "// → 다음 단계에서 비움" 동작과 같되,
//    줄 자체를 지우므로 빈 줄이 남지 않는다(요청: 비어버린 줄까지 제거).
//  - 인라인 "//"(줄 중간)는 건드리지 않는다 → URL(https://...), 이스케이프,
//    <lora:...>, (text:1.2), \(armor\) 등 정상 토큰을 깨뜨리지 않기 위함.
//  - 저장 원문은 그대로 두고, 포트 출력/전송 시점에만 적용한다(라벨 보존).
//  - 사용자가 직접 넣은 빈 줄은 보존된다.
function stripComments(text) {
  return String(text ?? "")
    .split(/\r\n|\r|\n/)
    .filter((ln) => !/^\s*\/\//.test(ln))
    .join("\n");
}

/* --------------------------- 마크다운 렌더러 (v7) --------------------------- */
/* MD-RENDER-START */
// Notes 섹션 프리뷰용 자체 미니 렌더러 (의존성 0 — 외부 라이브러리/CDN 불필요).
// 지원 문법: 헤딩(#~######) · 굵게(**)·기울임(*)·취소선(~~)·인라인코드(`) ·
// 링크([t](http(s)://…) — 새 탭) · GFM 표(| 구분, :--- 정렬) · 리스트(-/*/+,
// 1. — 들여쓰기 중첩) · 인용(>) · 수평선(---) · 코드펜스(```).
// 보안: 원문 HTML은 전부 이스케이프(태그 주입 불가), 링크 href는 http(s)만.
// 미지원(의도): 이미지, 각주, 중첩 인용, _밑줄강조_(snake_case 오인 방지).
// 줄바꿈은 <br>로 처리(GFM soft-break 대신 — 줄 단위 메모에 더 자연스러움).

function mdEscapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// 인라인 문법 렌더 (입력은 이미 escape된 텍스트).
// 인라인 코드/링크는 먼저 치환해 보호(stash) — 내부의 *·| 등이 재해석되지 않게.
function mdInline(text) {
  const stash = [];
  const keep = (html) => `\u0000${stash.push(html) - 1}\u0000`;
  let t = text;
  t = t.replace(/`([^`\n]+)`/g, (m, c) => keep(`<code>${c}</code>`));
  t = t.replace(/\[([^\]\n]+)\]\((https?:\/\/[^)\s]+)\)/g,
    (m, a, u) => keep(`<a href="${u}" target="_blank" rel="noopener">${mdInline(a)}</a>`));
  t = t.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
  t = t.replace(/(^|[^*\w])\*([^*\n]+)\*(?!\w)/g, "$1<em>$2</em>");
  t = t.replace(/~~([^~\n]+)~~/g, "<del>$1</del>");
  t = t.replace(/\u0000(\d+)\u0000/g, (m, i) => stash[+i]);
  return t;
}

const MD_RE_HEAD = /^(#{1,6})\s+(.*)$/;
const MD_RE_HR = /^\s*(?:-{3,}|\*{3,}|_{3,})\s*$/;
const MD_RE_UL = /^(\s*)[-*+]\s+(.*)$/;
const MD_RE_OL = /^(\s*)\d+[.)]\s+(.*)$/;
const MD_RE_QUOTE = /^\s*>\s?(.*)$/;
const MD_RE_FENCE = /^\s*```/;
const MD_RE_TABLE_SEP = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$/;

// 표 한 줄 → 셀 배열 (양끝 | 허용)
function mdSplitRow(line) {
  let s = line.trim();
  if (s.startsWith("|")) s = s.slice(1);
  if (s.endsWith("|")) s = s.slice(0, -1);
  return s.split("|").map((c) => c.trim());
}

// 마크다운 원문 → HTML 문자열. 생성 태그는 화이트리스트 고정이며 원문은 전부
// 이스케이프되므로 innerHTML 주입에 안전하다.
function renderMarkdown(src) {
  const lines = String(src ?? "").replace(/\r\n?/g, "\n").split("\n");
  const out = [];
  let para = [];        // 진행 중 문단 (escape된 라인)
  let quote = [];       // 진행 중 인용 (escape된 라인)
  let inFence = false;  // 코드펜스 내부 여부
  let fence = [];
  const listStack = []; // [{indent, tag}] — 들여쓰기 기반 중첩 리스트

  const flushPara = () => {
    if (para.length) { out.push(`<p>${para.map(mdInline).join("<br>")}</p>`); para = []; }
  };
  const flushQuote = () => {
    if (quote.length) { out.push(`<blockquote>${quote.map(mdInline).join("<br>")}</blockquote>`); quote = []; }
  };
  const closeListsTo = (indent) => {
    while (listStack.length && listStack[listStack.length - 1].indent >= indent) {
      out.push(`</${listStack.pop().tag}>`);
    }
  };
  const flushAll = () => { flushPara(); flushQuote(); closeListsTo(-1); };

  for (let i = 0; i < lines.length; i++) {
    const raw = mdEscapeHtml(lines[i]);

    if (MD_RE_FENCE.test(lines[i])) {  // ``` 코드펜스 토글
      if (inFence) { out.push(`<pre><code>${fence.join("\n")}</code></pre>`); fence = []; inFence = false; }
      else { flushAll(); inFence = true; }
      continue;
    }
    if (inFence) { fence.push(raw); continue; }

    if (!lines[i].trim()) { flushAll(); continue; }  // 빈 줄 = 블록 경계

    // 표: 현재 줄에 |가 있고 다음 줄이 구분행(|---|)이면 표 시작
    if (lines[i].includes("|") && i + 1 < lines.length && MD_RE_TABLE_SEP.test(lines[i + 1])) {
      flushAll();
      const head = mdSplitRow(raw);
      const aligns = mdSplitRow(lines[i + 1]).map((c) => {
        const l = c.startsWith(":"), r = c.endsWith(":");
        return l && r ? "center" : r ? "right" : "";
      });
      const att = (k) => (aligns[k] ? ` style="text-align:${aligns[k]}"` : "");
      let html = "<table><thead><tr>" +
        head.map((c, k) => `<th${att(k)}>${mdInline(c)}</th>`).join("") +
        "</tr></thead><tbody>";
      i += 1; // 구분행 스킵
      while (i + 1 < lines.length && lines[i + 1].includes("|") && lines[i + 1].trim()) {
        i += 1;
        const cells = mdSplitRow(mdEscapeHtml(lines[i]));
        html += "<tr>" + head.map((_, k) => `<td${att(k)}>${mdInline(cells[k] ?? "")}</td>`).join("") + "</tr>";
      }
      out.push(html + "</tbody></table>");
      continue;
    }

    let m = lines[i].match(MD_RE_HEAD);
    if (m) {
      flushAll();
      out.push(`<h${m[1].length}>${mdInline(mdEscapeHtml(m[2]))}</h${m[1].length}>`);
      continue;
    }
    if (MD_RE_HR.test(lines[i])) { flushAll(); out.push("<hr>"); continue; }

    m = lines[i].match(MD_RE_QUOTE);
    if (m) { flushPara(); closeListsTo(-1); quote.push(mdEscapeHtml(m[1])); continue; }
    flushQuote();

    const ul = lines[i].match(MD_RE_UL);
    const ol = ul ? null : lines[i].match(MD_RE_OL);
    if (ul || ol) {
      flushPara();
      const indent = (ul || ol)[1].length;
      const tag = ul ? "ul" : "ol";
      const top = listStack[listStack.length - 1];
      if (!top || indent > top.indent) {
        listStack.push({ indent, tag });   // 더 깊은 들여쓰기 → 중첩 리스트 시작
        out.push(`<${tag}>`);
      } else {
        if (indent < top.indent) closeListsTo(indent + 1);  // 얕아짐 → 내부 닫기
        const cur = listStack[listStack.length - 1];
        if (!cur || cur.tag !== tag) {     // 같은 깊이에서 ul↔ol 전환
          if (cur && cur.indent === indent) out.push(`</${listStack.pop().tag}>`);
          listStack.push({ indent, tag });
          out.push(`<${tag}>`);
        }
      }
      out.push(`<li>${mdInline(mdEscapeHtml((ul || ol)[2]))}</li>`);
      continue;
    }
    closeListsTo(-1);

    para.push(raw);  // 일반 문단
  }
  if (inFence) out.push(`<pre><code>${fence.join("\n")}</code></pre>`);  // 미닫힘 펜스
  flushAll();
  return out.join("\n");
}
/* MD-RENDER-END */

/* ----------------------- Ctrl+↑/↓ 가중치 편집 (editAttention) ----------------------- */
// ComfyUI 코어의 Ctrl+위/아래 가중치 조절과 동일한 동작을 직접 구현.
// (BMK textarea는 e.stopPropagation()으로 캔버스로의 키 전파를 막아 코어 핸들러가
//  닿지 않으므로 자체 구현한다. 증감 폭은 아래 WEIGHT_STEP로 고정.)
const WEIGHT_STEP = 0.1; // 요청값: 0.1 (바꾸려면 이 숫자만 수정)

function _incWeight(weight, delta) {
  const f = parseFloat(weight);
  if (!Number.isFinite(f)) return weight;
  const n = Math.round((f + delta) * 100) / 100; // 부동소수 오차 제거(0.01 격자)
  return Number.isInteger(n) ? n.toFixed(1) : String(n); // 1 → "1.0"
}

// 커서를 감싸는 가장 가까운 (괄호) 범위 탐색 (선택이 없을 때 보조)
function _findEnclosure(text, pos) {
  let start = pos, end = pos, open = 0, close = 0;
  while (start >= 0) {
    start--;
    if (text[start] === "(" && open === close) break;
    if (text[start] === "(") open++;
    else if (text[start] === ")") close++;
  }
  if (start < 0) return null;
  open = 0; close = 0;
  while (end < text.length) {
    if (text[end] === ")" && open === close) break;
    if (text[end] === "(") open++;
    else if (text[end] === ")") close++;
    end++;
  }
  if (end >= text.length) return null;
  return { start: start + 1, end };
}

// 선택 텍스트를 (text:weight)로 감싸고 weight를 delta만큼 증감. 선택 유지.
function editAttention(ta, delta) {
  const text = ta.value;
  let start = ta.selectionStart;
  let end = ta.selectionEnd;
  let sel = text.substring(start, end);

  // 선택이 없으면: 감싸는 괄호 우선, 없으면 커서가 놓인 단어를 선택
  if (!sel) {
    const enc = _findEnclosure(text, start);
    if (enc) {
      start = enc.start; end = enc.end;
    } else {
      const delim = " .,\\/!?%^*;:{}=-_`~()\r\n\t";
      while (start > 0 && !delim.includes(text[start - 1])) start--;
      while (end < text.length && !delim.includes(text[end])) end++;
    }
    sel = text.substring(start, end);
    if (!sel) return;
  }

  // 끝의 공백 한 칸 제거
  if (sel.endsWith(" ")) { sel = sel.slice(0, -1); end--; }

  // 좌우가 괄호면 그 괄호까지 선택에 포함
  if (text[start - 1] === "(" && text[end] === ")") {
    start--; end++;
    sel = text.substring(start, end);
  }

  // 괄호로 감싸여 있지 않으면 감싼다
  if (!(sel.startsWith("(") && sel.endsWith(")"))) sel = `(${sel})`;

  // 가중치(:n)가 없으면 1.0 부여
  const wRe = /:([+-]?(?:\d+(?:\.\d+)?|\.\d+))\)$/;
  if (!wRe.test(sel)) sel = sel.replace(/\)$/, ":1.0)");

  // 증감 적용
  const next = sel.replace(wRe, (m, w) => `:${_incWeight(w, delta)})`);

  ta.setRangeText(next, start, end, "select");
  ta.dispatchEvent(new Event("input", { bubbles: true })); // 저장 로직 트리거
}

/* ----------------------------- installed LoRA list ----------------------------- */
// 설치된 LoRA 목록(코어 LoraLoader의 lora_name 콤보)을 1회 조회해 캐시.
// 페이지 내 모든 BMK 노드가 공유한다.
function stripLoraExt(name) {
  return String(name).replace(/\.(safetensors|ckpt|pt|pth|bin|lora)$/i, "");
}

const loraList = { items: null, loading: null };

async function ensureLoraList() {
  if (loraList.items) return loraList.items;
  if (loraList.loading) return loraList.loading;
  loraList.loading = (async () => {
    try {
      const r = await api.fetchApi("/object_info/LoraLoader");
      const j = await r.json();
      const def = j?.LoraLoader?.input;
      const arr = def?.required?.lora_name?.[0] || def?.optional?.lora_name?.[0] || [];
      loraList.items = (Array.isArray(arr) ? arr : []).map(stripLoraExt);
    } catch (e) {
      console.warn("[BMK Notes] LoRA 목록 로드 실패:", e);
      loraList.items = [];
    } finally {
      loraList.loading = null;
    }
    return loraList.items;
  })();
  return loraList.loading;
}

/* ------------------------------- shared store ------------------------------- */
// 페이지 내 모든 노드 인스턴스가 공유하는 단일 트리.
// tree: [{ name, collapsed, tabs: [{ name, text }] }] | null(로딩 전)

const store = {
  tree: null,
  paramPresets: [],  // v5: 서버 공유 파라미터 프리셋 [{name, params}]
  loading: null,
  listeners: new Set(),

  subscribe(fn) {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  },

  emit(evt) {
    for (const fn of [...this.listeners]) {
      try { fn(evt); } catch (e) { console.error("[BMK Notes]", e); }
    }
  },

  async refresh() {
    if (this.loading) return this.loading;
    this.loading = (async () => {
      try {
        const r = await api.fetchApi("/bmk/notes/tree");
        if (!r.ok) throw new Error("트리 로드 실패: " + r.status);
        const j = await r.json();
        this.tree = ingestTree(j.categories);
        this.paramPresets = normalizeParamPresets(j.param_presets);
        this.emit({ type: "tree" });
      } finally {
        this.loading = null;
      }
    })();
    return this.loading;
  },

  async op(op, params = {}) {
    const r = await api.fetchApi("/bmk/notes/op", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ op, ...params }),
    });
    let j = {};
    try { j = await r.json(); } catch (e) { /* noop */ }
    if (!r.ok) throw new Error(j.error || "요청 실패: " + r.status);
    if (Array.isArray(j.categories)) {
      this.tree = ingestTree(j.categories);
      this.paramPresets = normalizeParamPresets(j.param_presets);
      this.emit({
        type: "tree",
        op: { op, ...params },
        // 서버(v5)가 rename/move_category 시 동봉하는 {old,new} — 경로 prefix 재매핑용
        remap: j.renamed_category || j.moved_category || null,
      });
    }
    return j;
  },

  _findCat(list, path) {
    for (const c of list || []) {
      if (c.path === path) return c;
      const hit = this._findCat(c.children, path);
      if (hit) return hit;
    }
    return null;
  },
  getCat(path) {
    return this.tree ? this._findCat(this.tree, path) : null;
  },
  getTab(cat, tab) {
    return this.getCat(cat)?.tabs.find((t) => t.name === tab) ?? null;
  },
  firstTab() {
    const dfs = (list) => {
      for (const c of list || []) {
        if (c.tabs.length) return { cat: c.path, tab: c.tabs[0].name };
        const hit = dfs(c.children);
        if (hit) return hit;
      }
      return null;
    };
    return this.tree ? dfs(this.tree) : null;
  },
  getDoc(cat, tab) {
    const t = this.getTab(cat, tab);
    if (!t) return null;
    if (!t.doc) t.doc = emptyDoc();
    return t.doc;
  },
  // 문서 변경을 같은 페이지의 다른 노드 인스턴스에 알림 (저장은 호출부에서 디바운스).
  touchDoc(cat, tab, source) {
    if (this.getTab(cat, tab)) this.emit({ type: "doc", cat, tab, source });
  },
};

/* ---------------------------------- styles ---------------------------------- */

function injectStyles() {
  if (document.getElementById("bmk-tabbed-notes-style")) return;
  const style = document.createElement("style");
  style.id = "bmk-tabbed-notes-style";
  style.textContent = `
  .bmk-notes {
    display: flex; flex-direction: row; align-items: stretch;
    box-sizing: border-box; overflow: hidden;
    background: var(--comfy-input-bg, #222);
    border: 1px solid var(--border-color, #4e4e4e);
    border-radius: 6px;
    font-family: var(--comfy-font-family, sans-serif);
    font-size: 12px;
    color: var(--input-text, #ddd);
  }
  .bmk-notes, .bmk-notes * { box-sizing: border-box; }

  .bmk-side {
    display: flex; flex-direction: column;
    flex: 0 0 auto; min-width: ${MIN_SIDEBAR}px;
    background: var(--comfy-menu-bg, #1b1b1b);
    user-select: none; overflow: hidden;
  }
  .bmk-cat-list { flex: 1 1 auto; overflow-y: auto; overflow-x: auto; padding: 3px 2px; }
  .bmk-cat-list::-webkit-scrollbar { width: 6px; height: 6px; }
  .bmk-cat-list::-webkit-scrollbar-thumb { background: var(--border-color, #555); border-radius: 3px; }
  .bmk-cat-list::-webkit-scrollbar-track { background: transparent; }
  .bmk-loading { padding: 8px 6px; color: var(--descrip-text, #888); font-size: 11px; }

  .bmk-cat { margin-bottom: 2px; }
  /* 중첩 컨테이너(하위 카테고리 + 탭) — 레벨당 들여쓰기.
     깊어져 폭이 부족하면 min-width 덕분에 .bmk-cat-list에 가로 스크롤이 생긴다. */
  .bmk-body { margin-left: 9px; }
  .bmk-cat-h, .bmk-tab { min-width: 88px; }
  /* 카테고리 폴더(접힘=닫힘/펼침=열림) 아이콘, 탭 문서 아이콘 */
  .bmk-cat-ico {
    flex: 0 0 auto; display: inline-flex; align-items: center;
    opacity: .85;
  }
  .bmk-tab-ico {
    flex: 0 0 auto; display: inline-flex; align-items: center;
    color: var(--descrip-text, #999); opacity: .7;
  }
  .bmk-tab.active .bmk-tab-ico { color: var(--input-text, #ddd); opacity: .9; }
  .bmk-cat-h {
    display: flex; align-items: center; gap: 3px;
    padding: 3px 4px; border-radius: 4px; cursor: pointer;
    color: var(--descrip-text, #999);
    font-weight: 600; font-size: 11px;
  }
  .bmk-cat-h:hover { background: rgba(255,255,255,0.06); }
  .bmk-cat-h .bmk-arrow { width: 12px; flex: 0 0 auto; text-align: center; font-size: 9px; opacity: .8; }
  .bmk-cat-h .bmk-name { flex: 1 1 auto; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

  .bmk-tab {
    display: flex; align-items: center; gap: 4px;
    margin: 1px 2px 1px 1px; padding: 3px 4px 3px 6px;
    border-radius: 4px; cursor: pointer;
    border: 1px solid transparent;
  }
  .bmk-tab:hover { background: rgba(255,255,255,0.06); }
  .bmk-tab.active {
    background: rgba(100, 150, 230, 0.18);
    border-color: rgba(100, 150, 230, 0.45);
  }
  .bmk-tab .bmk-name { flex: 1 1 auto; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

  .bmk-btn {
    flex: 0 0 auto; width: 14px; height: 14px; line-height: 13px;
    text-align: center; border-radius: 3px; font-size: 11px;
    color: var(--descrip-text, #999); visibility: hidden;
  }
  .bmk-cat-h:hover .bmk-btn, .bmk-tab:hover .bmk-btn { visibility: visible; }
  .bmk-btn:hover { background: rgba(255,255,255,0.15); color: var(--input-text, #fff); }
  .bmk-btn.bmk-del:hover { background: rgba(220, 70, 70, 0.5); }
  .bmk-btn.bmk-dup { display: inline-flex; align-items: center; justify-content: center; }
  .bmk-btn.bmk-dup svg { display: block; }
  .bmk-btn.bmk-dup:hover { background: rgba(100, 150, 230, 0.45); color: var(--input-text, #fff); }
  .bmk-btn.bmk-sub { display: inline-flex; align-items: center; justify-content: center; }
  .bmk-btn.bmk-sub svg { display: block; }
  .bmk-btn.bmk-sub:hover { background: rgba(100, 150, 230, 0.45); color: var(--input-text, #fff); }

  .bmk-foot { flex: 0 0 auto; display: flex; align-items: stretch; gap: 2px; padding: 2px; }
  .bmk-add-cat {
    flex: 1 1 auto; padding: 3px 6px;
    border-radius: 4px; cursor: pointer; text-align: left;
    color: var(--descrip-text, #888); font-size: 11px;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .bmk-add-cat:hover { background: rgba(255,255,255,0.07); color: var(--input-text, #ddd); }
  .bmk-ico {
    flex: 0 0 auto; display: flex; align-items: center;
    padding: 3px 6px; border-radius: 4px; cursor: pointer;
    color: var(--descrip-text, #888);
  }
  .bmk-ico:hover { background: rgba(255,255,255,0.07); color: var(--input-text, #ddd); }
  .bmk-ico.spin svg { animation: bmk-spin .7s linear infinite; }
  @keyframes bmk-spin { to { transform: rotate(360deg); } }

  .bmk-tab.drop-above { box-shadow: inset 0 2px 0 0 #6496e6; }
  .bmk-tab.drop-below { box-shadow: inset 0 -2px 0 0 #6496e6; }
  .bmk-cat-h.drop-above { box-shadow: inset 0 2px 0 0 #6496e6; }
  .bmk-cat-h.drop-below { box-shadow: inset 0 -2px 0 0 #6496e6; }
  .bmk-cat-h.drop-into { background: rgba(100, 150, 230, 0.22); }
  .bmk-dragging { opacity: 0.45; }

  .bmk-split {
    flex: 0 0 5px; cursor: col-resize;
    background: var(--border-color, #444);
    opacity: .55; transition: opacity .12s;
  }
  .bmk-split:hover, .bmk-split.dragging { opacity: 1; background: #6496e6; }

  .bmk-editor { flex: 1 1 auto; display: flex; flex-direction: column; min-width: ${MIN_EDITOR}px; overflow: hidden; }
  .bmk-crumb {
    flex: 0 0 auto; display: flex; align-items: center; gap: 4px;
    padding: 2px 4px 2px 8px;
    border-bottom: 1px solid var(--border-color, #3a3a3a);
    user-select: none;
  }
  .bmk-crumb-text {
    flex: 0 1 auto; min-width: 60px; font-size: 10px; color: var(--descrip-text, #888);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  /* 통합 상태줄: 전송 패널/Params의 일시 메시지가 여기 표시된다.
     브레드크럼 줄의 남는 오른쪽 공간을 쓰므로 레이아웃이 들썩이지 않는다. */
  .bmk-status {
    flex: 1 1 auto; min-width: 0; text-align: right; font-size: 10px;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .bmk-status.ok { color: #6fd66f; }
  .bmk-status.err { color: #e6794b; }
  .bmk-copy {
    flex: 0 0 auto; display: flex; align-items: center; justify-content: center;
    width: 20px; height: 18px; border-radius: 4px; cursor: pointer;
    color: var(--descrip-text, #999);
  }
  .bmk-copy:hover { background: rgba(255,255,255,0.1); color: var(--input-text, #fff); }
  .bmk-copy.copied { color: #6fd66f; }
  .bmk-copy.disabled { opacity: .35; cursor: default; pointer-events: none; }
  .bmk-text {
    flex: 1 1 auto !important; width: 100% !important; height: auto !important;
    min-height: 0 !important; resize: none !important;
    position: static !important; inset: auto !important;
    background: var(--comfy-input-bg, #222); color: var(--input-text, #ddd);
    border: none !important; outline: none !important; padding: 6px 8px !important;
    margin: 0 !important; box-shadow: none !important;
    font-family: var(--comfy-font-family, sans-serif);
    font-size: 12px; line-height: 1.45;
  }
  .bmk-text::-webkit-scrollbar { width: 8px; }
  .bmk-text::-webkit-scrollbar-thumb { background: var(--border-color, #555); border-radius: 4px; }

  .bmk-rename {
    flex: 1 1 auto; min-width: 0; width: 100%;
    background: var(--comfy-input-bg, #111);
    color: var(--input-text, #eee);
    border: 1px solid #6496e6; border-radius: 3px;
    font-size: 11px; padding: 1px 3px; outline: none;
  }

  /* ----- 아코디언 섹션 ----- */
  .bmk-sections { flex: 1 1 auto; overflow-y: auto; overflow-x: hidden; padding: 2px; }
  .bmk-sections::-webkit-scrollbar { width: 8px; }
  .bmk-sections::-webkit-scrollbar-thumb { background: var(--border-color, #555); border-radius: 4px; }
  .bmk-sections::-webkit-scrollbar-track { background: transparent; }
  .bmk-sec {
    border: 1px solid var(--border-color, #3a3a3a);
    border-radius: 5px; margin: 3px;
  }
  .bmk-sec-h {
    display: flex; align-items: center; gap: 5px;
    padding: 4px 6px; cursor: pointer; user-select: none;
    background: var(--bmk-sec-h-bg, var(--comfy-menu-bg, #1b1b1b));
    color: var(--input-text, #ddd); font-weight: 600; font-size: 11px;
    border-radius: 4px 4px 0 0;
  }
  .bmk-sec-h:hover { filter: brightness(1.18); }
  .bmk-sec-grip {
    flex: 0 0 auto; width: 11px; text-align: center;
    font-size: 11px; line-height: 1; opacity: .35; cursor: grab;
    letter-spacing: -1px;
  }
  .bmk-sec-h:hover .bmk-sec-grip { opacity: .75; }
  .bmk-sec.bmk-sec-dragging { opacity: 0.45; }
  .bmk-sec.drop-above { box-shadow: inset 0 3px 0 0 #6496e6; }
  .bmk-sec.drop-below { box-shadow: inset 0 -3px 0 0 #6496e6; }
  .bmk-sec-arrow { width: 10px; flex: 0 0 auto; text-align: center; font-size: 9px; opacity: .8; }
  .bmk-sec-title { flex: 0 0 auto; letter-spacing: .02em; }
  .bmk-sec-spacer { flex: 1 1 auto; }
  .bmk-sec-copy {
    flex: 0 0 auto; display: flex; align-items: center; justify-content: center;
    width: 20px; height: 18px; border-radius: 4px;
    color: var(--descrip-text, #999); visibility: hidden;
  }
  .bmk-sec-h:hover .bmk-sec-copy { visibility: visible; }
  .bmk-sec-copy:hover { background: rgba(255,255,255,0.12); color: var(--input-text, #fff); }
  .bmk-sec-copy.copied { color: #6fd66f; visibility: visible; }
  .bmk-sec-b { padding: 5px 6px; background: var(--comfy-input-bg, #222); }

  .bmk-sec-text {
    width: 100%; box-sizing: border-box; resize: vertical; display: block;
    background: var(--comfy-input-bg, #1a1a1a); color: var(--input-text, #ddd);
    border: 1px solid var(--border-color, #3a3a3a); border-radius: 4px;
    padding: 5px 7px; margin: 0; outline: none;
    font-family: var(--comfy-font-family, sans-serif); font-size: 12px; line-height: 1.45;
  }
  .bmk-sec-text:focus { border-color: #6496e6; }
  .bmk-sec-text::-webkit-scrollbar { width: 8px; }
  .bmk-sec-text::-webkit-scrollbar-thumb { background: var(--border-color, #555); border-radius: 4px; }

  /* ----- 마크다운 프리뷰 (v7, Notes) ----- */
  .bmk-sec-mdbtn {
    flex: 0 0 auto; display: flex; align-items: center; justify-content: center;
    width: 20px; height: 18px; border-radius: 4px; cursor: pointer;
    color: var(--descrip-text, #999); visibility: hidden;
  }
  .bmk-sec-h:hover .bmk-sec-mdbtn { visibility: visible; }
  .bmk-sec-mdbtn:hover { background: rgba(255,255,255,0.12); color: var(--input-text, #fff); }
  .bmk-sec-mdbtn.on { visibility: visible; color: #6fa8ff; }
  /* 프리뷰 본문: 높이 auto(내용 흐름) — 긴 노트는 .bmk-sections 스크롤로 읽는다 */
  .bmk-md {
    box-sizing: border-box; width: 100%; cursor: text; user-select: text;
    background: var(--comfy-input-bg, #1a1a1a); color: var(--input-text, #ddd);
    border: 1px solid var(--border-color, #3a3a3a); border-radius: 4px;
    padding: 7px 10px; font-size: 12px; line-height: 1.55;
    overflow-wrap: anywhere;
  }
  .bmk-md-empty { color: var(--descrip-text, #777); font-style: italic; }
  .bmk-md > :first-child { margin-top: 0; }
  .bmk-md > :last-child { margin-bottom: 0; }
  .bmk-md h1, .bmk-md h2, .bmk-md h3, .bmk-md h4, .bmk-md h5, .bmk-md h6 {
    margin: 10px 0 5px; line-height: 1.3; color: #fff;
  }
  .bmk-md h1 { font-size: 16px; padding-bottom: 3px; border-bottom: 1px solid var(--border-color, #3a3a3a); }
  .bmk-md h2 { font-size: 14.5px; padding-bottom: 2px; border-bottom: 1px solid var(--border-color, #333); }
  .bmk-md h3 { font-size: 13px; }
  .bmk-md h4, .bmk-md h5, .bmk-md h6 { font-size: 12px; }
  .bmk-md p { margin: 4px 0 8px; }
  .bmk-md ul, .bmk-md ol { margin: 4px 0 8px; padding-left: 20px; }
  .bmk-md li { margin: 2px 0; }
  .bmk-md blockquote {
    margin: 6px 0 8px; padding: 4px 9px;
    border-left: 3px solid #6496e6; border-radius: 0 4px 4px 0;
    background: rgba(100,150,230,0.08);
  }
  .bmk-md code {
    background: rgba(255,255,255,0.09); border-radius: 3px;
    padding: 0.5px 4px; font-family: monospace; font-size: 11px;
  }
  .bmk-md pre {
    margin: 6px 0 8px; padding: 6px 9px; overflow-x: auto;
    background: rgba(0,0,0,0.35); border: 1px solid var(--border-color, #3a3a3a);
    border-radius: 4px;
  }
  .bmk-md pre code { display: block; background: none; padding: 0; white-space: pre; }
  .bmk-md pre::-webkit-scrollbar { height: 7px; }
  .bmk-md pre::-webkit-scrollbar-thumb { background: var(--border-color, #555); border-radius: 4px; }
  /* 표: 노드 폭보다 넓으면 표 자체에 가로 스크롤 */
  .bmk-md table {
    display: block; width: max-content; max-width: 100%; overflow-x: auto;
    border-collapse: collapse; margin: 6px 0 8px; font-size: 11px;
  }
  .bmk-md th, .bmk-md td {
    border: 1px solid var(--border-color, #444); padding: 3px 8px;
    text-align: left; vertical-align: top;
  }
  .bmk-md th { background: rgba(255,255,255,0.06); font-weight: 600; white-space: nowrap; }
  .bmk-md table::-webkit-scrollbar { height: 7px; }
  .bmk-md table::-webkit-scrollbar-thumb { background: var(--border-color, #555); border-radius: 4px; }
  .bmk-md hr { border: none; border-top: 1px solid var(--border-color, #444); margin: 9px 0; }
  .bmk-md a { color: #6fa8ff; text-decoration: none; }
  .bmk-md a:hover { text-decoration: underline; }

  /* ----- LoRA 섹션 ----- */
  .bmk-lora-list { display: flex; flex-direction: column; gap: 3px; }
  .bmk-lora-empty {
    padding: 4px 2px; color: var(--descrip-text, #777);
    font-style: italic; font-size: 11px;
  }
  .bmk-lora-row { display: flex; align-items: center; gap: 5px; padding: 2px 0; }
  .bmk-lora-row.off { opacity: .45; }
  .bmk-lora-chk {
    flex: 0 0 auto; width: 14px; height: 14px; margin: 0;
    cursor: pointer; accent-color: #6496e6;
  }
  .bmk-lora-name {
    flex: 1 1 auto; min-width: 40px;
    background: var(--comfy-input-bg, #1a1a1a); color: var(--input-text, #ddd);
    border: 1px solid var(--border-color, #3a3a3a); border-radius: 3px;
    padding: 2px 5px; outline: none; font-size: 11px;
  }
  .bmk-lora-name:focus { border-color: #6496e6; }
  .bmk-lora-slider { flex: 0 0 78px; height: 14px; cursor: pointer; accent-color: #6496e6; }
  .bmk-lora-num {
    flex: 0 0 56px; width: 56px;
    background: var(--comfy-input-bg, #1a1a1a); color: var(--input-text, #ddd);
    border: 1px solid var(--border-color, #3a3a3a); border-radius: 3px;
    padding: 2px 3px; outline: none; font-size: 11px; text-align: center;
  }
  .bmk-lora-num:focus { border-color: #6496e6; }
  .bmk-lora-del {
    flex: 0 0 auto; width: 16px; height: 16px; line-height: 15px; text-align: center;
    border-radius: 3px; color: var(--descrip-text, #999); cursor: pointer; font-size: 13px;
  }
  .bmk-lora-del:hover { background: rgba(220,70,70,0.5); color: #fff; }
  .bmk-lora-add { display: flex; align-items: center; gap: 4px; margin-top: 5px; position: relative; }
  .bmk-lora-addinput {
    flex: 1 1 auto; min-width: 40px;
    background: var(--comfy-input-bg, #1a1a1a); color: var(--input-text, #ddd);
    border: 1px solid var(--border-color, #3a3a3a); border-radius: 3px;
    padding: 3px 6px; outline: none; font-size: 11px;
  }
  .bmk-lora-addinput:focus { border-color: #6496e6; }
  .bmk-lora-addbtn {
    flex: 0 0 auto; width: 22px; height: 22px; line-height: 21px; text-align: center;
    border-radius: 3px; cursor: pointer; font-size: 15px;
    background: rgba(100,150,230,0.18); color: var(--input-text, #ddd);
  }
  .bmk-lora-addbtn:hover { background: rgba(100,150,230,0.4); }

  /* LoRA 이름 자동완성 드롭다운 */
  .bmk-ac {
    position: absolute; top: 100%; left: 0; right: 26px; z-index: 50;
    margin-top: 2px; max-height: 180px; overflow-y: auto;
    background: var(--comfy-menu-bg, #1b1b1b);
    border: 1px solid #6496e6; border-radius: 4px;
    box-shadow: 0 4px 12px rgba(0,0,0,0.5);
  }
  .bmk-ac::-webkit-scrollbar { width: 8px; }
  .bmk-ac::-webkit-scrollbar-thumb { background: var(--border-color, #555); border-radius: 4px; }
  .bmk-ac-item {
    padding: 3px 7px; font-size: 11px; cursor: pointer;
    color: var(--input-text, #ddd);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .bmk-ac-item:hover, .bmk-ac-item.active { background: rgba(100,150,230,0.3); }
  .bmk-ac-empty { padding: 4px 7px; font-size: 11px; color: var(--descrip-text, #888); font-style: italic; }

  /* ----- Params 섹션 (v5) ----- */
  .bmk-prm-bar { display: flex; align-items: center; gap: 5px; flex-wrap: wrap; margin-bottom: 4px; }
  .bmk-prm-tbtn {
    flex: 0 0 auto; padding: 2px 8px; border-radius: 3px; cursor: pointer;
    background: rgba(255,255,255,0.06); color: var(--input-text, #ddd);
    border: 1px solid var(--border-color, #444); white-space: nowrap; font-size: 11px;
  }
  .bmk-prm-tbtn:hover { background: rgba(100,150,230,0.3); border-color: rgba(100,150,230,0.5); }
  .bmk-prm-tbtn.bmk-prm-send {
    background: rgba(100,150,230,0.28); border-color: rgba(100,150,230,0.55); font-weight: 600;
  }
  .bmk-prm-tbtn.bmk-prm-send:hover { background: rgba(100,150,230,0.5); }
  .bmk-prm-divider {
    flex: 0 0 1px; width: 1px; align-self: stretch;
    background: var(--border-color, #444); margin: 1px 2px;
  }
  .bmk-prm-list { display: flex; flex-direction: column; gap: 4px; }
  .bmk-prm-row {
    display: flex; flex-direction: column; gap: 3px;
    border: 1px solid var(--border-color, #3a3a3a); border-radius: 4px;
    padding: 3px 4px; background: rgba(255,255,255,0.02);
  }
  .bmk-prm-row.off { opacity: .45; }
  .bmk-prm-row.bmk-prm-dragging { opacity: .4; }
  .bmk-prm-row.drop-above { box-shadow: inset 0 2px 0 0 #6496e6; }
  .bmk-prm-row.drop-below { box-shadow: inset 0 -2px 0 0 #6496e6; }
  .bmk-prm-l { display: flex; align-items: center; gap: 4px; min-width: 0; }
  .bmk-prm-grip {
    flex: 0 0 auto; width: 11px; text-align: center;
    font-size: 11px; line-height: 1; opacity: .35; cursor: grab; letter-spacing: -1px;
  }
  .bmk-prm-row:hover .bmk-prm-grip { opacity: .75; }
  .bmk-prm-label, .bmk-prm-widget {
    flex: 1 1 60px; min-width: 40px;
    background: var(--comfy-input-bg, #1a1a1a); color: var(--input-text, #ddd);
    border: 1px solid var(--border-color, #3a3a3a); border-radius: 3px;
    padding: 2px 5px; outline: none; font-size: 11px;
  }
  .bmk-prm-label:focus, .bmk-prm-widget:focus { border-color: #6496e6; }
  /* 1행의 제목칸: 캡션이 길면 제목칸이 먼저 수축한다(shrink 가중치 4배) */
  .bmk-prm-label { flex: 1 4 80px; min-width: 36px; }
  .bmk-prm-node {
    flex: 0 0 46px; width: 46px; text-align: center;
    background: var(--comfy-input-bg, #1a1a1a); color: var(--input-text, #ddd);
    border: 1px solid var(--border-color, #3a3a3a); border-radius: 3px;
    padding: 2px 3px; outline: none; font-size: 11px;
  }
  .bmk-prm-node:focus { border-color: #6496e6; }
  .bmk-prm-node.bad { border-color: rgba(230,121,75,0.8); color: #e6794b; }
  .bmk-prm-type {
    flex: 0 0 auto; width: 60px;
    background: var(--comfy-input-bg, #1a1a1a); color: var(--input-text, #ddd);
    border: 1px solid var(--border-color, #3a3a3a); border-radius: 3px;
    padding: 1px 2px; outline: none; font-size: 11px;
  }
  .bmk-prm-valhost { flex: 1.4 1 70px; min-width: 56px; display: flex; align-items: center; }
  .bmk-prm-val {
    width: 100%; min-width: 0;
    background: var(--comfy-input-bg, #1a1a1a); color: var(--input-text, #ddd);
    border: 1px solid var(--border-color, #3a3a3a); border-radius: 3px;
    padding: 2px 5px; outline: none; font-size: 11px;
  }
  .bmk-prm-val:focus { border-color: #6496e6; }
  .bmk-prm-bool { display: inline-flex; align-items: center; gap: 4px; font-size: 11px; cursor: pointer; }
  .bmk-prm-btn {
    flex: 0 0 auto; width: 18px; height: 16px;
    display: inline-flex; align-items: center; justify-content: center;
    border-radius: 3px; color: var(--descrip-text, #999); cursor: pointer;
  }
  .bmk-prm-btn svg { display: block; }
  .bmk-prm-btn:hover { background: rgba(100,150,230,0.45); color: #fff; }
  .bmk-prm-cap {
    flex: 0 1 auto; min-width: 0; font-size: 10px; color: var(--descrip-text, #888);
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .bmk-prm-cap.bad { color: #e6794b; }
  .bmk-prm-cap.warn { color: #e6b34b; }
  .bmk-prm-add { display: flex; align-items: center; gap: 4px; margin-top: 5px; position: relative; }
  .bmk-prm-add .bmk-ac { left: 0; right: 0; }
  .bmk-prm-preset {
    flex: 1 1 80px; min-width: 70px; max-width: 200px;
    background: var(--comfy-input-bg, #1a1a1a); color: var(--input-text, #ddd);
    border: 1px solid var(--border-color, #3a3a3a); border-radius: 3px;
    padding: 1px 2px; outline: none; font-size: 11px;
  }
  .bmk-prm-preset:focus { border-color: #6496e6; }

  /* 전역 전송 패널 (Prompt/Negative/LoRA → 대상 노드) */
  .bmk-send {
    position: relative; flex: 0 0 auto;
    padding: 4px 8px;
    border-bottom: 1px solid var(--border-color, #333);
    background: rgba(255,255,255,0.025);
    font-size: 11px; color: var(--descrip-text, #aaa);
  }
  /* 제목 + 3개 대상 그룹: 한 줄, 왼쪽 정렬, 부족하면 가로 스크롤 */
  .bmk-send-scroll {
    display: flex; align-items: center; gap: 10px;
    white-space: nowrap; overflow-x: auto; overflow-y: hidden;
    padding-right: 104px;  /* ⚡버튼이 덮는 우측 영역 확보 */
    scrollbar-width: thin;
  }
  .bmk-send-scroll::-webkit-scrollbar { height: 7px; }
  .bmk-send-scroll::-webkit-scrollbar-thumb { background: var(--border-color, #444); border-radius: 4px; }
  .bmk-send-scroll::-webkit-scrollbar-track { background: transparent; }
  .bmk-send-title { flex: 0 0 auto; font-weight: 600; color: var(--input-text, #ddd); }
  .bmk-send-grp { flex: 0 0 auto; display: inline-flex; align-items: center; gap: 4px; }
  .bmk-send-dot { flex: 0 0 auto; width: 8px; height: 8px; border-radius: 50%; background: #555; }
  .bmk-send-lbl { flex: 0 0 auto; }
  .bmk-send-hash { flex: 0 0 auto; color: var(--descrip-text, #888); }
  .bmk-send-id {
    flex: 0 0 50px; width: 50px;
    background: var(--comfy-input-bg, #1a1a1a); color: var(--input-text, #ddd);
    border: 1px solid var(--border-color, #3a3a3a); border-radius: 3px;
    padding: 2px 4px; outline: none; font-size: 11px; text-align: center;
  }
  .bmk-send-id:focus { border-color: #6496e6; }
  .bmk-send-one {
    flex: 0 0 auto; padding: 2px 9px; border-radius: 3px; cursor: pointer;
    background: rgba(255,255,255,0.06); color: var(--input-text, #ddd);
    border: 1px solid var(--border-color, #444); white-space: nowrap;
  }
  .bmk-send-one:hover { background: rgba(100,150,230,0.3); border-color: rgba(100,150,230,0.5); }
  /* ⚡ 모두 전송: 최상단 레이어, 우측 고정 (아래로 내용이 스크롤되어 지나감) */
  .bmk-send-all {
    position: absolute; right: 8px; top: 4px; z-index: 3;
    padding: 3px 10px; border-radius: 3px; cursor: pointer;
    background: rgba(100,150,230,0.28); color: var(--input-text, #eee);
    border: 1px solid rgba(100,150,230,0.55); white-space: nowrap; font-weight: 600;
    box-shadow: -10px 0 8px -4px var(--comfy-menu-bg, rgba(30,30,30,0.85));
  }
  .bmk-send-all:hover { background: rgba(100,150,230,0.5); }
  `;
  document.head.appendChild(style);
}

const ICON_COPY =
  '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/></svg>';
const ICON_CHECK =
  '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M4 12l5 5L20 7"/></svg>';
const ICON_EXPORT =
  '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v12"/><path d="M7 10l5 5 5-5"/><path d="M4 19h16"/></svg>';
const ICON_REFRESH =
  '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 1 1-2.64-6.36"/><path d="M21 3v6h-6"/></svg>';
const ICON_DUPLICATE =
  '<svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="8" y="8" width="12" height="12" rx="2"/><path d="M4 16V6a2 2 0 0 1 2-2h10"/></svg>';
const ICON_FOLDER_PLUS =
  '<svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 8a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M12 12v5"/><path d="M9.5 14.5h5"/></svg>';
const ICON_FOLDER =
  '<svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>';
const ICON_FOLDER_OPEN =
  '<svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m6 14 1.45-2.9A2 2 0 0 1 9.24 10H20a2 2 0 0 1 1.94 2.5l-1.55 6a2 2 0 0 1-1.94 1.5H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h3.9a2 2 0 0 1 1.69.9l.81 1.2a2 2 0 0 0 1.67.9H18a2 2 0 0 1 2 2v2"/></svg>';
const ICON_TAB =
  '<svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M9 13h6"/><path d="M9 17h4"/></svg>';
// 파라미터 행 전송(위 화살표+트레이)/가져오기(아래 화살표+트레이) — 채움형 굵은 디자인
const ICON_PARAM_SEND =
  '<svg viewBox="0 0 24 24" width="13" height="13" fill="currentColor" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"><path d="M12 2 L18.5 9.5 H14.5 V15 H9.5 V9.5 H5.5 Z"/><path d="M2.5 13.5 h3 v4 h13 v-4 h3 v4.5 a2.5 2.5 0 0 1 -2.5 2.5 h-14 a2.5 2.5 0 0 1 -2.5 -2.5 z"/></svg>';
const ICON_PARAM_FETCH =
  '<svg viewBox="0 0 24 24" width="13" height="13" fill="currentColor" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"><path d="M9.5 2.5 H14.5 V9 H18.5 L12 15.5 L5.5 9 H9.5 Z"/><path d="M2.5 13.5 h3 v4 h13 v-4 h3 v4.5 a2.5 2.5 0 0 1 -2.5 2.5 h-14 a2.5 2.5 0 0 1 -2.5 -2.5 z"/></svg>';
// v7: Notes 마크다운 프리뷰 토글 (눈 = 프리뷰로 전환 / 연필 = 편집으로 복귀)
const ICON_EYE =
  '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></svg>';
const ICON_PENCIL =
  '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 3a2.83 2.83 0 0 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/></svg>';

/* ------------------------------- minimal zip ------------------------------- */
/* ZIP-UTILS-START */
let CRC_TABLE = null;
function crc32(u8) {
  if (!CRC_TABLE) {
    CRC_TABLE = new Uint32Array(256);
    for (let n = 0; n < 256; n++) {
      let c = n;
      for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
      CRC_TABLE[n] = c >>> 0;
    }
  }
  let c = 0xffffffff;
  for (let i = 0; i < u8.length; i++) c = CRC_TABLE[(c ^ u8[i]) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

function dosDateTime(d) {
  const time = (d.getHours() << 11) | (d.getMinutes() << 5) | (d.getSeconds() >> 1);
  const date = ((((d.getFullYear() - 1980) & 0x7f) << 9) | ((d.getMonth() + 1) << 5) | d.getDate());
  return { time, date };
}

// 압축 없는(store) 최소 구현. UTF-8 파일명 플래그(bit 11) 설정으로 한글 경로 보존.
function makeZip(files /* [{ path, data: Uint8Array }] */) {
  const enc = new TextEncoder();
  const now = dosDateTime(new Date());
  const chunks = [];
  const central = [];
  let offset = 0;

  for (const f of files) {
    const nameBytes = enc.encode(f.path);
    const crc = crc32(f.data);
    const lh = new DataView(new ArrayBuffer(30));
    lh.setUint32(0, 0x04034b50, true);
    lh.setUint16(4, 20, true);
    lh.setUint16(6, 0x0800, true);
    lh.setUint16(8, 0, true);
    lh.setUint16(10, now.time, true);
    lh.setUint16(12, now.date, true);
    lh.setUint32(14, crc, true);
    lh.setUint32(18, f.data.length, true);
    lh.setUint32(22, f.data.length, true);
    lh.setUint16(26, nameBytes.length, true);
    lh.setUint16(28, 0, true);
    chunks.push(new Uint8Array(lh.buffer), nameBytes, f.data);
    central.push({ nameBytes, crc, size: f.data.length, offset });
    offset += 30 + nameBytes.length + f.data.length;
  }

  const cdStart = offset;
  for (const c of central) {
    const ch = new DataView(new ArrayBuffer(46));
    ch.setUint32(0, 0x02014b50, true);
    ch.setUint16(4, 20, true);
    ch.setUint16(6, 20, true);
    ch.setUint16(8, 0x0800, true);
    ch.setUint16(10, 0, true);
    ch.setUint16(12, now.time, true);
    ch.setUint16(14, now.date, true);
    ch.setUint32(16, c.crc, true);
    ch.setUint32(20, c.size, true);
    ch.setUint32(24, c.size, true);
    ch.setUint16(28, c.nameBytes.length, true);
    ch.setUint32(42, c.offset, true);
    chunks.push(new Uint8Array(ch.buffer), c.nameBytes);
    offset += 46 + c.nameBytes.length;
  }

  const eocd = new DataView(new ArrayBuffer(22));
  eocd.setUint32(0, 0x06054b50, true);
  eocd.setUint16(8, central.length, true);
  eocd.setUint16(10, central.length, true);
  eocd.setUint32(12, offset - cdStart, true);
  eocd.setUint32(16, cdStart, true);
  chunks.push(new Uint8Array(eocd.buffer));

  return new Blob(chunks, { type: "application/zip" });
}
/* ZIP-UTILS-END */

/* ------------------------- autocomplete-enabled textarea ------------------------- */
// 태그 자동완성 확장(comfy-ex-tagcomplete, pythongosssss 등)은 ComfyWidgets.STRING이
// 만든 textarea에만 자동완성을 부착한다. 그래서 우리가 직접 createElement한 textarea는
// 자동완성이 안 붙는다. 해결책: ComfyWidgets.STRING으로 임시 멀티라인 위젯을 만들게 한 뒤,
// 자동완성이 이미 부착된 그 textarea(inputEl)만 떼어내 우리 에디터에 재사용한다.
// 폴더명/내부 클래스명에 의존하지 않으므로 "STRING 위젯을 패치하는" 모든 확장과 호환된다.
function createAutocompleteTextarea(node) {
  let inputEl = null;
  const beforeNames = new Set((node.widgets || []).map((w) => w));
  try {
    const res = ComfyWidgets.STRING(
      node,
      "__bmk_tmp_text__",
      ["STRING", { multiline: true }],
      app
    );
    const w = res?.widget;
    inputEl = w?.inputEl || w?.element?.querySelector?.("textarea") || null;

    if (w) {
      // 1) textarea(자동완성 인스턴스 부착됨)를 DOM에서 분리해 보존.
      //    ComfyUI가 만든 래퍼/컨테이너에서 떼어내 우리 에디터에 다시 붙일 것이다.
      if (inputEl && inputEl.parentElement) {
        inputEl.parentElement.removeChild(inputEl);
      }
      // 2) DOM 위젯이 남긴 래퍼 요소 제거
      w.element?.remove?.();
      if (w.inputEl && w.inputEl !== inputEl) w.inputEl.remove?.();
      // 3) ComfyUI가 이 위젯을 다시 그리거나 크기 계산에 넣지 못하도록 중화.
      //    (splice만으로는 프론트엔드 내부 추적에서 빠지지 않아 노드 상단에 떠버림)
      w.draw = () => {};
      w.computeSize = () => [0, -4];
      w.type = "converted-widget";
      w.hidden = true;
      // 4) onRemove를 호출해 ComfyUI 측 정리 로직을 태운 뒤 배열에서 제거
      try { w.onRemove?.(); } catch (e) { /* noop */ }
    }
  } catch (e) {
    console.warn("[BMK Notes] 자동완성 textarea 생성 실패, 기본 textarea로 대체:", e);
    inputEl = null;
  }

  // ComfyWidgets.STRING 호출로 새로 추가된 위젯을 모두 제거 (이름 매칭에 의존하지 않음)
  if (node.widgets) {
    node.widgets = node.widgets.filter((w) => beforeNames.has(w));
  }

  if (!inputEl) {
    inputEl = document.createElement("textarea");
  }
  // ComfyWidgets가 남긴 인라인 스타일(절대배치 등) 제거 — .bmk-text 레이아웃이 적용되도록
  inputEl.removeAttribute("style");
  inputEl.readOnly = false;
  inputEl.hidden = false;
  inputEl.style.display = "";
  return inputEl;
}

/* ---------------------------------- widget ---------------------------------- */

function setupNode(node) {
  injectStyles();

  // 자동 생성된 notes_data STRING 위젯 제거 (DOM 위젯이 이름 승계)
  let initialValue = "{}";
  const orig = node.widgets?.find((w) => w.name === "notes_data");
  if (orig) {
    initialValue = orig.value;
    const idx = node.widgets.indexOf(orig);
    if (idx >= 0) node.widgets.splice(idx, 1);
    orig.onRemove?.();
    orig.inputEl?.remove?.();
    orig.element?.remove?.();
  }

  // 노드별 선택 상태 (워크플로우에 저장되는 유일한 데이터)
  // collapsed: 카테고리 접힘 상태 — 노드마다 개별 (접힌 것만 키로 보관)
  const state = {
    activeCategory: null, activeTab: null, sidebarWidth: 110,
    collapsed: {},
    targets: { prompt: "", negative: "", lora: "" }, // 전송 대상 노드 ID (노드별 영구)
    // 섹션 접힘/순서는 이제 "노트별"(doc.ui)에 저장된다 → 여기엔 없음.
    sectionHeights: {},    // 섹션 key별 텍스트칸 높이(px) (노드별 영구)
  };
  // 신규 생성 노드: 첫 트리 로드 시 모든 카테고리를 접어 시작 (applyValue에서 판정)
  let collapseAllPending = false;
  let pendingLegacy = null;   // 구버전(내장형) 데이터 — 가져오기 대기
  let importPrompted = false;
  let pendingRename = null;   // {kind:"tab"|"cat", cat?, name} 렌더 직후 이름 입력 모드
  let renameOpen = false;     // 이름 입력창이 열려 있는 동안 재렌더 보류
  let renderQueued = false;

  // 모든 카테고리를 접힌 상태로 설정 (신규 생성 노드의 초기 뷰 — 필요한 것만 펼쳐 쓰는 의도).
  // 트리가 아직 로드 전이면 false를 반환하고, 첫 tree 이벤트에서 재시도한다.
  function collapseAllCategories() {
    if (!store.tree) return false;
    const map = {};
    const walk = (list) => {
      for (const c of list || []) { map[c.path] = true; walk(c.children); }
    };
    walk(store.tree);
    state.collapsed = map;
    return true;
  }

  function applyValue(v) {
    let d = null;
    try { d = typeof v === "string" ? JSON.parse(v) : v; } catch (e) { /* noop */ }
    // 신규 생성 노드 감지: notes_data 기본값 "{}" (mode·activeCategory·categories 전부 없음).
    // 저장/복제된 노드는 getValue가 항상 mode:2를 쓰고, 구버전(내장형)은 categories 배열을 가진다.
    const isFresh = !!d && typeof d === "object" && !Array.isArray(d) &&
      !Array.isArray(d.categories) && d.mode === undefined &&
      d.activeCategory === undefined && d.activeTab === undefined;
    if (isFresh) {
      collapseAllPending = !collapseAllCategories();
      if (!collapseAllPending) markChanged();
    } else {
      collapseAllPending = false; // 저장된 상태 복원 → 접힘 맵도 저장분을 따른다
    }
    if (d && Array.isArray(d.categories)) {
      // 구버전(내장형) 데이터 감지
      pendingLegacy = d;
      state.sidebarWidth = clamp(Number(d.sidebarWidth) || 110, MIN_SIDEBAR, 600);
      const collapsedMap = {};
      for (const c of d.categories) if (c?.collapsed && c?.name) collapsedMap[c.name] = true;
      state.collapsed = collapsedMap;
      const active = d.activeTabId;
      outer: for (const c of d.categories) {
        for (const t of c?.tabs ?? []) {
          if (t?.id === active) {
            state.activeCategory = c.name ?? null;
            state.activeTab = t.name ?? null;
            break outer;
          }
        }
      }
      maybePromptImport();
    } else if (d) {
      state.activeCategory = d.activeCategory ?? state.activeCategory;
      state.activeTab = d.activeTab ?? state.activeTab;
      state.sidebarWidth = clamp(Number(d.sidebarWidth) || state.sidebarWidth, MIN_SIDEBAR, 600);
      if (d.collapsed && typeof d.collapsed === "object" && !Array.isArray(d.collapsed)) {
        state.collapsed = { ...d.collapsed };
      }
      // 전송 대상 ID 복원 (+ 구버전 loraTargetId → targets.lora 마이그레이션)
      if (d.targets && typeof d.targets === "object" && !Array.isArray(d.targets)) {
        for (const k of ["prompt", "negative", "lora"]) {
          const v = d.targets[k];
          if (typeof v === "string" || typeof v === "number") state.targets[k] = String(v).trim();
        }
      }
      if (!state.targets.lora && (typeof d.loraTargetId === "string" || typeof d.loraTargetId === "number")) {
        state.targets.lora = String(d.loraTargetId).trim();
      }
      // (구버전 d.sectionsCollapsed는 더 이상 사용하지 않음 — 섹션 접힘은 노트별 doc.ui로 이동)
      if (d.sectionHeights && typeof d.sectionHeights === "object" && !Array.isArray(d.sectionHeights)) {
        const sh = {};
        for (const k of Object.keys(d.sectionHeights)) {
          const v = Number(d.sectionHeights[k]);
          if (Number.isFinite(v) && v > 0) sh[k] = v;
        }
        state.sectionHeights = sh;
      }
    }
    for (const k of ["prompt", "negative", "lora"]) {
      if (sendIdInputs[k]) sendIdInputs[k].value = state.targets[k] ?? "";
    }
    applySectionLayout();
    ensureActiveValid();
    render();
    syncEditor();
  }

  /* ---------- change notification ---------- */
  let changeTimer = null;
  const markChanged = () => {
    clearTimeout(changeTimer);
    changeTimer = setTimeout(() => {
      try { app.graph?.change?.(); } catch (e) { /* noop */ }
    }, 300);
  };

  /* ---------- DOM skeleton ---------- */
  const container = document.createElement("div");
  container.className = "bmk-notes";

  const side = document.createElement("div");
  side.className = "bmk-side";
  const catList = document.createElement("div");
  catList.className = "bmk-cat-list";

  const foot = document.createElement("div");
  foot.className = "bmk-foot";
  const addCatBtn = document.createElement("div");
  addCatBtn.className = "bmk-add-cat";
  addCatBtn.textContent = "+ 카테고리";
  addCatBtn.title = "새 카테고리 추가";
  const refreshBtn = document.createElement("div");
  refreshBtn.className = "bmk-ico";
  refreshBtn.innerHTML = ICON_REFRESH;
  refreshBtn.title = "공유 폴더와 동기화 (외부에서 수정한 파일 반영)";
  const exportBtn = document.createElement("div");
  exportBtn.className = "bmk-ico";
  exportBtn.innerHTML = ICON_EXPORT;
  exportBtn.title = "노트 전체 내보내기 (루트/카테고리/탭.json)";
  foot.append(addCatBtn, refreshBtn, exportBtn);
  side.append(catList, foot);

  const split = document.createElement("div");
  split.className = "bmk-split";
  split.title = "드래그하여 너비 조절";

  const sendIdInputs = {};   // 전역 전송 패널의 대상 ID 입력칸 refs { prompt, negative, lora }

  const editor = document.createElement("div");
  editor.className = "bmk-editor";

  const crumb = document.createElement("div");
  crumb.className = "bmk-crumb";
  const crumbText = document.createElement("span");
  crumbText.className = "bmk-crumb-text";
  // 통합 상태줄: 전송 패널/Params의 일시 메시지가 여기 표시된다(4초 후 자동 소거).
  // 브레드크럼 줄의 남는 오른쪽 공간을 재활용하므로 레이아웃이 들썩이지 않는다.
  const statusEl = document.createElement("span");
  statusEl.className = "bmk-status";
  crumb.append(crumbText, statusEl);
  let statusTimer = null;
  const showStatus = (text, kind) => {
    statusEl.textContent = text;
    statusEl.title = text; // 잘렸을 때 전체 내용 툴팁
    statusEl.className = "bmk-status " + (kind || "");
    clearTimeout(statusTimer);
    statusTimer = setTimeout(() => {
      statusEl.textContent = "";
      statusEl.removeAttribute("title");
      statusEl.className = "bmk-status";
    }, 4000);
  };

  const sectionsEl = document.createElement("div");
  sectionsEl.className = "bmk-sections";

  /* ---------- 전역 전송 패널 (Prompt/Negative/LoRA → 대상 노드) ---------- */
  const SEND_DEFS = [
    { key: "prompt", label: "Prompt", color: SECTION_HEADER_COLORS.prompt, btn: "전송", ph: "66" },
    { key: "negative", label: "Negative", color: SECTION_HEADER_COLORS.negative, btn: "전송", ph: "67" },
    { key: "lora", label: "LoRA", color: SECTION_HEADER_COLORS.loras, btn: "적용", ph: "54" },
  ];

  // 한 대상으로 전송 (key별 적절한 주입 함수 사용)
  // prompt/negative는 전송 직전 //-주석 줄을 제거(stripComments)해서 보낸다.
  // (노트 원문의 //라벨은 화면·파일에 그대로 보존됨)
  const sendOne = (key) => {
    const id = state.targets[key];
    if (key === "lora") return injectLorasToNode(id);
    const t = currentTab();
    const text = key === "prompt" ? (t?.doc?.prompt ?? "") : (t?.doc?.negative ?? "");
    return injectTextToNode(id, stripComments(text));
  };
  const fmtSend = (key, res) => {
    if (!res.ok) return res.message;
    const where = res.label ? ` (${res.label})` : "";
    return key === "lora" ? `#${res.id} 적용(${res.count})${where}` : `#${res.id} 전송됨${where}`;
  };

  const sendPanel = document.createElement("div");
  sendPanel.className = "bmk-send";

  // 가로 스크롤 영역: 제목 + 3개 대상 그룹 (왼쪽 정렬, 한 줄)
  const sendScroll = document.createElement("div");
  sendScroll.className = "bmk-send-scroll";
  const sendTitle = document.createElement("span");
  sendTitle.className = "bmk-send-title";
  sendTitle.textContent = "노드로 전송";
  sendScroll.append(sendTitle);

  // ⚡ 모두 전송 — 최상단 레이어, 오른쪽 고정
  const sendAllBtn = document.createElement("div");
  sendAllBtn.className = "bmk-send-all";
  sendAllBtn.textContent = "\u26A1 모두 전송";
  sendAllBtn.title = "ID가 지정된 대상에 일괄 전송";

  // v6: 전송 결과 메시지는 브레드크럼 줄의 통합 상태줄로 표시한다.
  const showSendMsg = showStatus;

  for (const def of SEND_DEFS) {
    const grp = document.createElement("span");
    grp.className = "bmk-send-grp";
    const dot = document.createElement("span");
    dot.className = "bmk-send-dot";
    if (def.color) dot.style.background = def.color;
    const lbl = document.createElement("span");
    lbl.className = "bmk-send-lbl";
    lbl.textContent = def.label;
    const hash = document.createElement("span");
    hash.className = "bmk-send-hash";
    hash.textContent = "#";
    const idInput = document.createElement("input");
    idInput.type = "text";
    idInput.className = "bmk-send-id";
    idInput.placeholder = def.ph;
    idInput.value = state.targets[def.key] ?? "";
    idInput.title = `${def.label} 전송 대상 노드 ID`;
    idInput.addEventListener("keydown", (e) => e.stopPropagation());
    idInput.addEventListener("input", () => {
      state.targets[def.key] = idInput.value.trim();
      markChanged();
    });
    sendIdInputs[def.key] = idInput;
    const oneBtn = document.createElement("div");
    oneBtn.className = "bmk-send-one";
    oneBtn.textContent = def.btn;
    oneBtn.title = `${def.label}만 전송`;
    oneBtn.addEventListener("click", () => {
      const res = sendOne(def.key);
      showSendMsg(`${def.label}: ${fmtSend(def.key, res)}`, res.ok ? "ok" : "err");
    });
    grp.append(dot, lbl, hash, idInput, oneBtn);
    sendScroll.append(grp);
  }

  sendAllBtn.addEventListener("click", () => {
    const parts = [];
    let any = false, fail = false;
    for (const def of SEND_DEFS) {
      if (!state.targets[def.key]) continue;
      any = true;
      const res = sendOne(def.key);
      if (!res.ok) fail = true;
      parts.push(`${def.label} ${res.ok ? "\u2713" : "\u2717"}`);
    }
    if (!any) showSendMsg("전송할 대상 ID가 없습니다", "err");
    else showSendMsg(parts.join(" · "), fail ? "err" : "ok");
  });

  sendPanel.append(sendScroll, sendAllBtn);

  editor.append(crumb, sendPanel, sectionsEl);
  container.append(side, split, editor);

  // 마우스 휠 (WAS 'text multiline'식 — 스크롤바 유무에 따라 휠 줌 ↔ 스크롤 가변):
  //  - 커서 아래에 실제 스크롤바가 있으면 그 스크롤을 우선하고 캔버스 줌은 막는다.
  //  - 스크롤할 게 없으면 합성 WheelEvent를 ComfyUI 캔버스(<canvas>)로 직접 forward한다.
  // DOM 위젯은 <canvas> 위에 떠 있는 별개 요소라, stopPropagation을 "안 하는" 것만으로는
  // 캔버스의 줌 핸들러(LGraphCanvas.processMouseWheel)까지 이벤트가 도달하지 않기 때문이다.
  const wheelTargetCanScroll = (start) => {
    let el = start;
    while (el && el !== container) {
      if (el.nodeType === 1) {
        const oy = getComputedStyle(el).overflowY;
        const scrollable = oy === "auto" || oy === "scroll" || el.tagName === "TEXTAREA";
        if (scrollable && el.scrollHeight - el.clientHeight > 1) return true;
      }
      el = el.parentElement;
    }
    return false;
  };
  const getCanvasEl = () =>
    app.canvas?.canvas ||
    app.canvasEl ||
    document.querySelector("canvas#graph-canvas") ||
    document.querySelector("canvas.litegraph");
  container.addEventListener(
    "wheel",
    (e) => {
      if (wheelTargetCanScroll(e.target)) {
        e.stopPropagation();           // 스크롤 우선, 캔버스 줌 차단
        return;
      }
      // 스크롤할 게 없음 → 캔버스로 휠을 forward해 줌이 되게 한다.
      const cv = getCanvasEl();
      if (!cv) return;                 // 캔버스를 못 찾으면 기본 동작에 맡김
      e.stopPropagation();
      e.preventDefault();
      cv.dispatchEvent(new WheelEvent("wheel", {
        bubbles: true,
        cancelable: true,
        clientX: e.clientX,
        clientY: e.clientY,
        deltaX: e.deltaX,
        deltaY: e.deltaY,
        deltaZ: e.deltaZ,
        deltaMode: e.deltaMode,
      }));
    },
    { passive: false }
  );
  // 가운데(휠) 버튼 드래그 → ComfyUI 캔버스 패닝.
  // DOM 위젯은 <canvas> 위에 떠 있어 가운데버튼 pointerdown이 캔버스에 직접 닿지 않으므로,
  // 실제 pointerId를 그대로 실어 합성 pointerdown을 캔버스로 forward한다. 그러면 LiteGraph가
  // 그 포인터를 캡처(또는 document 리스너로 추적)해 이후 실제 이동으로 패닝을 이어간다.
  const forwardMiddleDownToCanvas = (e) => {
    const cv = getCanvasEl();
    if (!cv) return;
    cv.dispatchEvent(new PointerEvent("pointerdown", {
      pointerId: e.pointerId,
      pointerType: e.pointerType || "mouse",
      isPrimary: e.isPrimary,
      button: 1,    // 가운데 버튼
      buttons: 4,   // 가운데 버튼 눌림 비트
      clientX: e.clientX,
      clientY: e.clientY,
      bubbles: true,
      cancelable: true,
      view: window,
    }));
  };
  container.addEventListener("pointerdown", (e) => {
    if (e.button === 1) {            // 가운데(휠) 버튼 → 캔버스 패닝
      e.preventDefault();
      e.stopPropagation();
      forwardMiddleDownToCanvas(e);
      return;
    }
    e.stopPropagation();             // 좌/우 버튼은 기존대로 (노드 드래그·선택·텍스트 편집 보호)
  });
  // 가운데버튼 클릭 시 브라우저 자동 스크롤(autoscroll) 방지
  container.addEventListener("mousedown", (e) => {
    if (e.button === 1) { e.preventDefault(); e.stopPropagation(); }
  });

  // Ctrl(또는 ⌘)+↑/↓ → 선택 텍스트 가중치 조절 (prompt/negative/notes 텍스트칸).
  // 캡처 단계에서 가로채는 이유: 자동완성 확장이 textarea에 먼저 붙인 keydown보다
  // 앞서 처리하고, 동시에 캔버스로의 키 전파(노드 삭제 등)도 막기 위함이다.
  // ctrl/⌘ + ↑/↓ 외의 키는 건드리지 않아 자동완성·일반 입력에 영향 없음.
  container.addEventListener(
    "keydown",
    (e) => {
      if (!(e.ctrlKey || e.metaKey)) return;
      if (e.key !== "ArrowUp" && e.key !== "ArrowDown") return;
      const ta = e.target;
      if (!(ta instanceof HTMLTextAreaElement) ||
          !ta.classList.contains("bmk-sec-text")) return;
      e.preventDefault();
      e.stopPropagation();
      editAttention(ta, e.key === "ArrowUp" ? WEIGHT_STEP : -WEIGHT_STEP);
    },
    true // 캡처 단계
  );

  /* ---------- 섹션 구성 ---------- */
  // 텍스트 섹션은 doc[key]에 1:1 바인딩. LoRA 섹션은 doc.loras(구조화)에 바인딩.
  const SECTION_DEFS = [
    { key: "prompt",   label: "Prompt",   kind: "text", autocomplete: true,  placeholder: "긍정 프롬프트…", minH: 84 },
    { key: "negative", label: "Negative", kind: "text", autocomplete: true,  placeholder: "네거티브 프롬프트…", minH: 56 },
    { key: "loras",    label: "LoRA",     kind: "lora" },
    { key: "notes",    label: "Notes",    kind: "text", autocomplete: false, markdown: true, placeholder: "메모 / 번역 / 생성 정보… (헤더 👁 = 마크다운 프리뷰)", minH: 56 },
    { key: "params",   label: "Params",   kind: "params" },
  ];

  const sectionRefs = [];    // { key, sec, head, arrow, body, applyCollapse } — 레이아웃/재정렬용
  const resizeObservers = []; // 텍스트칸 높이 감시자 (dispose 시 정리)
  const textSections = [];   // { key, el(textarea), minH }
  let loraSection = null;    // { body, listEl, addInput }
  let paramSection = null;   // { body, listEl, msg, idInput, acEl, ... } — v5 Params 섹션
  let applyingLayout = true; // 높이 프로그램 적용/초기 로드 중엔 ResizeObserver 저장 억제

  /* ---------- 섹션 순서/접힘/프리뷰: 노트별(doc.ui) ---------- */
  // 섹션 순서·접힘·마크다운 프리뷰는 활성 노트(doc)의 ui에 저장된다.
  function docOrder(doc) {
    const o = doc && doc.ui && doc.ui.order;
    if (Array.isArray(o)) {
      const seen = [];
      for (const k of o) if (SECTION_KEYS.includes(k) && !seen.includes(k)) seen.push(k);
      return seen.concat(SECTION_KEYS.filter((k) => !seen.includes(k)));
    }
    return SECTION_KEYS.slice();
  }
  function docCollapsed(doc, key) {
    return !!(doc && doc.ui && doc.ui.collapsed && doc.ui.collapsed[key]);
  }
  function setDocCollapsed(doc, key, collapsed) {
    if (!doc.ui) doc.ui = {};
    if (!doc.ui.collapsed) doc.ui.collapsed = {};
    if (collapsed) doc.ui.collapsed[key] = true;
    else delete doc.ui.collapsed[key];
    pruneUi(doc);
  }
  // v7: 마크다운 프리뷰 상태 (노트별 doc.ui.md — 백엔드 v8이 보존)
  function docMd(doc, key) {
    return !!(doc && doc.ui && doc.ui.md && doc.ui.md[key]);
  }
  function setDocMd(doc, key, on) {
    if (!doc.ui) doc.ui = {};
    if (!doc.ui.md) doc.ui.md = {};
    if (on) doc.ui.md[key] = true;
    else delete doc.ui.md[key];
    pruneUi(doc);
  }
  function setDocOrder(doc, order) {
    if (!doc.ui) doc.ui = {};
    if (order.join(",") === SECTION_KEYS.join(",")) delete doc.ui.order;
    else doc.ui.order = order.slice();
    pruneUi(doc);
  }
  function pruneUi(doc) {
    const u = doc.ui;
    if (!u) return;
    if (u.collapsed && !Object.keys(u.collapsed).length) delete u.collapsed;
    if (u.md && !Object.keys(u.md).length) delete u.md;
    if (Array.isArray(u.order) && u.order.join(",") === SECTION_KEYS.join(",")) delete u.order;
    if (!u.order && !u.collapsed && !u.md) delete doc.ui;
  }

  // v7: 마크다운 프리뷰 표시 적용 (markdown 지원 텍스트 섹션 전용 — 현재 Notes).
  // on이면 textarea를 숨기고 렌더 결과(div.bmk-md)를 보인다. 렌더는 원문이
  // 바뀌었을 때만 수행(s.mdSrc 캐시). 프리뷰 높이는 auto(내용 흐름) —
  // 긴 노트는 .bmk-sections 스크롤로 읽고, 섹션 높이 저장(state.sectionHeights)은
  // 편집 textarea에만 적용된다(프리뷰 auto 높이가 저장을 오염시키지 않도록).
  function applyMdView(s, doc) {
    if (!s.mdEl) return;
    const on = docMd(doc, s.key);
    if (on) {
      const src = doc && typeof doc[s.key] === "string" ? doc[s.key] : "";
      if (s.mdSrc !== src) {
        s.mdEl.innerHTML = renderMarkdown(src) ||
          '<p class="bmk-md-empty">내용 없음 — ✎로 편집 모드 전환 후 작성</p>';
        s.mdSrc = src;
      }
      s.mdEl.style.display = "";
      s.el.style.display = "none";
    } else {
      s.mdEl.style.display = "none";
      s.el.style.display = "";
    }
    if (s.mdBtn) {
      s.mdBtn.classList.toggle("on", on);
      s.mdBtn.innerHTML = on ? ICON_PENCIL : ICON_EYE;
      s.mdBtn.title = on ? "편집으로 전환" : "마크다운 프리뷰로 전환";
    }
  }

  // 주어진 순서대로 섹션 DOM(.bmk-sec)을 재배치. appendChild는 기존 노드를 "이동"시키므로
  // (제거가 아니라) 본문 textarea의 포커스/자동완성 인스턴스는 보존된다.
  let appliedOrderKey = "";
  function applyOrder(order, force) {
    const key = order.join(",");
    if (!force && key === appliedOrderKey) return;
    for (const k of order) {
      const ref = sectionRefs.find((r) => r.key === k);
      if (ref) sectionsEl.appendChild(ref.sec);
    }
    appliedOrderKey = key;
  }

  let secDrag = null; // 드래그 중인 섹션 key
  const clearSecDropMarks = () => {
    for (const r of sectionRefs) r.sec.classList.remove("drop-above", "drop-below");
  };

  function buildSection(def) {
    const sec = document.createElement("div");
    sec.className = "bmk-sec";

    const head = document.createElement("div");
    head.className = "bmk-sec-h";
    head.draggable = true;
    head.title = "드래그: 섹션 순서 변경 · 클릭: 접기/펼치기";
    const hbg = SECTION_HEADER_COLORS[def.key];
    if (hbg) head.style.setProperty("--bmk-sec-h-bg", hbg);
    const grip = document.createElement("span");
    grip.className = "bmk-sec-grip";
    grip.textContent = "\u2807\u2807"; // ⠇⠇ 드래그 그립
    const arrow = document.createElement("span");
    arrow.className = "bmk-sec-arrow";
    arrow.textContent = "\u25BE";
    const title = document.createElement("span");
    title.className = "bmk-sec-title";
    title.textContent = def.label;
    const spacer = document.createElement("span");
    spacer.className = "bmk-sec-spacer";
    const copy = document.createElement("span");
    copy.className = "bmk-sec-copy";
    copy.innerHTML = ICON_COPY;
    copy.title = def.kind === "lora"
      ? "이 LoRA 구문을 클립보드에 복사"
      : "이 섹션 내용을 클립보드에 복사";
    // v7: 마크다운 프리뷰 토글 (markdown: true 섹션 — 현재 Notes 전용).
    // 상태는 노트별 doc.ui.md에 저장돼 같은 노트를 보는 모든 노드가 공유한다.
    // 프리뷰 중에도 복사 버튼은 "원문(마크다운 소스)"을 복사한다.
    let mdBtn = null;
    if (def.markdown) {
      mdBtn = document.createElement("span");
      mdBtn.className = "bmk-sec-mdbtn";
      mdBtn.innerHTML = ICON_EYE;
      mdBtn.title = "마크다운 프리뷰로 전환";
      mdBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        const t = currentTab();
        if (!t) return;
        if (!t.doc) t.doc = emptyDoc();
        const next = !docMd(t.doc, def.key);
        setDocMd(t.doc, def.key, next);
        const s = textSections.find((x) => x.key === def.key);
        if (s) applyMdView(s, t.doc);
        store.touchDoc(state.activeCategory, state.activeTab, node);
        scheduleSave();
      });
    }
    if (mdBtn) head.append(grip, arrow, title, spacer, mdBtn, copy);
    else head.append(grip, arrow, title, spacer, copy);

    const body = document.createElement("div");
    body.className = "bmk-sec-b";

    const applyCollapse = (collapsed) => {
      arrow.textContent = collapsed ? "\u25B8" : "\u25BE";
      body.style.display = collapsed ? "none" : "";
    };
    applyCollapse(false); // 초기엔 펼침 — 실제 상태는 syncEditor가 노트별로 적용
    sectionRefs.push({ key: def.key, sec, head, arrow, body, applyCollapse });

    // 접기/펼치기 — 노트별(doc.ui.collapsed)에 저장
    head.addEventListener("click", (e) => {
      if (e.target === copy || copy.contains(e.target)) return;
      if (mdBtn && (e.target === mdBtn || mdBtn.contains(e.target))) return;
      const t = currentTab();
      if (!t) return;
      if (!t.doc) t.doc = emptyDoc();
      const next = !docCollapsed(t.doc, def.key);
      setDocCollapsed(t.doc, def.key, next);
      applyCollapse(next);
      store.touchDoc(state.activeCategory, state.activeTab, node);
      scheduleSave();
    });

    // 섹션 순서 드래그&드롭 — 노트별(doc.ui.order)에 저장
    head.addEventListener("dragstart", (e) => {
      secDrag = def.key;
      try {
        e.dataTransfer.effectAllowed = "move";
        e.dataTransfer.setData("text/plain", def.key);
      } catch (err) { /* noop */ }
      sec.classList.add("bmk-sec-dragging");
    });
    head.addEventListener("dragend", () => {
      secDrag = null; clearSecDropMarks(); sec.classList.remove("bmk-sec-dragging");
    });
    head.addEventListener("dragover", (e) => {
      if (!secDrag || secDrag === def.key) return;
      e.preventDefault();
      clearSecDropMarks();
      const r = sec.getBoundingClientRect();
      sec.classList.add(e.clientY < r.top + r.height / 2 ? "drop-above" : "drop-below");
    });
    head.addEventListener("drop", (e) => {
      if (!secDrag || secDrag === def.key) return;
      e.preventDefault();
      e.stopPropagation();
      const dragged = secDrag;
      secDrag = null;
      clearSecDropMarks();
      const t = currentTab();
      if (!t) return;
      if (!t.doc) t.doc = emptyDoc();
      const order = docOrder(t.doc);
      const from = order.indexOf(dragged);
      if (from < 0) return;
      order.splice(from, 1);
      let to = order.indexOf(def.key);
      const r = sec.getBoundingClientRect();
      if (e.clientY >= r.top + r.height / 2) to += 1;
      order.splice(to, 0, dragged);
      setDocOrder(t.doc, order);
      applyOrder(order, true);
      store.touchDoc(state.activeCategory, state.activeTab, node);
      scheduleSave();
    });

    let getText; // 복사용 텍스트 공급자

    if (def.kind === "text") {
      const ta = def.autocomplete
        ? createAutocompleteTextarea(node)
        : document.createElement("textarea");
      ta.className = "bmk-sec-text";
      ta.spellcheck = false;
      ta.placeholder = def.placeholder || "";
      const minH = def.minH || 56;
      ta.style.minHeight = minH + "px";
      // 저장된 높이가 "정상값(최소높이 이상)"일 때만 복원. 비정상(작은/0) 값은
      // 무시하고 min-height로 둬서 입력칸이 쪼그라들지 않게 한다(망가진 노드 자동 복구).
      const savedH = state.sectionHeights[def.key];
      ta.style.height = (Number.isFinite(savedH) && savedH >= minH) ? savedH + "px" : "";
      ta.value = "";
      ta.addEventListener("keydown", (e) => e.stopPropagation());
      ta.addEventListener("input", () => {
        const t = currentTab();
        if (!t) return;
        if (!t.doc) t.doc = emptyDoc();
        t.doc[def.key] = ta.value;
        store.touchDoc(state.activeCategory, state.activeTab, node);
        scheduleSave();
      });
      ta.addEventListener("blur", flushSave);
      // v7: 마크다운 프리뷰 뷰어 — 편집 textarea와 상호 배타 표시 (applyMdView)
      let mdEl = null;
      if (def.markdown) {
        mdEl = document.createElement("div");
        mdEl.className = "bmk-md";
        mdEl.style.display = "none";
        mdEl.style.minHeight = minH + "px";
      }
      body.append(ta);
      if (mdEl) body.append(mdEl);
      textSections.push({ key: def.key, el: ta, minH, body, mdEl, mdBtn });
      getText = () => ta.value ?? "";

      // 세로 리사이즈(드래그 핸들) 감지 → 높이 영구 저장.
      // - applyingLayout: 프로그램이 높이를 적용하는 동안의 변경은 무시(우리가 만든 변경).
      // - h < minH: 로드/레이아웃 미완 상태의 비정상값 → 무시(이게 쪼그라듦 버그의 핵심 원인).
      // - 기본 최소높이(미설정 상태)는 저장하지 않아 로드 시 불필요한 dirty 방지.
      if (typeof ResizeObserver !== "undefined") {
        const ro = new ResizeObserver(() => {
          if (applyingLayout) return;
          const h = ta.offsetHeight;
          if (h < minH) return;
          const cur = state.sectionHeights[def.key];
          if (cur === undefined && h <= minH + 1) return;
          if (h === cur) return;
          state.sectionHeights[def.key] = h;
          markChanged();
        });
        ro.observe(ta);
        resizeObservers.push(ro);
      }
    } else if (def.kind === "lora") {
      // LoRA 섹션
      const listEl = document.createElement("div");
      listEl.className = "bmk-lora-list";

      const addRow = document.createElement("div");
      addRow.className = "bmk-lora-add";
      const addInput = document.createElement("input");
      addInput.type = "text";
      addInput.className = "bmk-lora-addinput";
      addInput.placeholder = "이름 입력(자동완성) 또는 <lora:이름:1.00> 붙여넣기 후 Enter";

      const commitAdd = () => {
        const parsed = parseLoraSyntax(addInput.value);
        if (!parsed.length) return;
        const t = currentTab();
        if (!t) return;
        if (!t.doc) t.doc = emptyDoc();
        if (!Array.isArray(t.doc.loras)) t.doc.loras = [];
        for (const p of parsed) {
          const exist = t.doc.loras.find(
            (l) => l.name.toLowerCase() === p.name.toLowerCase()
          );
          if (exist) {
            exist.weight = p.weight;
            exist.enabled = true;
          } else {
            t.doc.loras.push(p);
          }
        }
        addInput.value = "";
        renderLoras(t.doc.loras);
        store.touchDoc(state.activeCategory, state.activeTab, node);
        scheduleSave();
      };

      /* ----- 이름 자동완성 드롭다운 ----- */
      const acEl = document.createElement("div");
      acEl.className = "bmk-ac";
      acEl.style.display = "none";
      let acItems = [];   // 현재 표시 중인 이름들
      let acIndex = -1;   // 하이라이트 인덱스

      const hideAc = () => { acEl.style.display = "none"; acItems = []; acIndex = -1; };

      const renderAc = () => {
        acEl.textContent = "";
        if (!acItems.length) {
          const empty = document.createElement("div");
          empty.className = "bmk-ac-empty";
          empty.textContent = "일치하는 LoRA 없음";
          acEl.appendChild(empty);
          acEl.style.display = "";
          return;
        }
        acItems.forEach((name, i) => {
          const it = document.createElement("div");
          it.className = "bmk-ac-item" + (i === acIndex ? " active" : "");
          it.textContent = name;
          it.title = name;
          // mousedown(클릭 전 blur 방지) 으로 선택 처리
          it.addEventListener("mousedown", (e) => { e.preventDefault(); selectAc(name); });
          acEl.appendChild(it);
        });
        acEl.style.display = "";
      };

      const selectAc = (name) => {
        addInput.value = name;
        commitAdd();      // 이름 단독 → 가중치 1.00으로 추가
        hideAc();
        addInput.focus();
      };

      const refreshAc = async () => {
        const q = addInput.value.trim();
        // 빈 값이거나 lora 구문을 붙여넣는 중이면 자동완성 끔
        if (!q || q.startsWith("<")) { hideAc(); return; }
        const all = await ensureLoraList();
        const ql = q.toLowerCase();
        acItems = all.filter((n) => n.toLowerCase().includes(ql)).slice(0, 40);
        acIndex = acItems.length ? 0 : -1;
        renderAc();
      };

      const moveAc = (delta) => {
        if (!acItems.length) return;
        acIndex = (acIndex + delta + acItems.length) % acItems.length;
        renderAc();
        const active = acEl.querySelector(".bmk-ac-item.active");
        active?.scrollIntoView({ block: "nearest" });
      };

      addInput.addEventListener("input", refreshAc);
      addInput.addEventListener("focus", () => { if (addInput.value.trim()) refreshAc(); });
      addInput.addEventListener("blur", () => setTimeout(hideAc, 120));
      addInput.addEventListener("keydown", (e) => {
        e.stopPropagation();
        const open = acEl.style.display !== "none";
        if (e.key === "ArrowDown") { e.preventDefault(); if (open) moveAc(1); else refreshAc(); }
        else if (e.key === "ArrowUp") { e.preventDefault(); moveAc(-1); }
        else if (e.key === "Escape") { if (open) { e.preventDefault(); hideAc(); } }
        else if (e.key === "Enter") {
          e.preventDefault();
          if (open && acIndex >= 0 && acItems[acIndex]) selectAc(acItems[acIndex]);
          else commitAdd();
        }
      });

      const addBtn = document.createElement("div");
      addBtn.className = "bmk-lora-addbtn";
      addBtn.textContent = "+";
      addBtn.title = "LoRA 추가";
      addBtn.addEventListener("click", commitAdd);
      addRow.append(addInput, addBtn, acEl);

      body.append(listEl, addRow);
      loraSection = { body, listEl, addInput };
      getText = () => compileLoras(currentTab()?.doc?.loras || []);
      ensureLoraList(); // 캐시 워밍 (fire-and-forget)
    } else {
      // ── Params 섹션 (v5): 워크플로우 파라미터 ──
      // 툴바 한 줄(전송/가져오기/복사/붙여넣기 ｜ 프리셋 — 좁으면 flex-wrap으로
      // 자동 줄바꿈) + 행 목록 + 피커. 상태 메시지는 통합 상태줄(showStatus)로.
      // 이벤트 배선과 렌더 로직은 아래 "Params 섹션 로직" 블록에서 담당.
      const bar = document.createElement("div");
      bar.className = "bmk-prm-bar";
      const sendAllP = document.createElement("div");
      sendAllP.className = "bmk-prm-tbtn bmk-prm-send";
      sendAllP.textContent = "\u26A1 모두 전송";
      sendAllP.title = "체크된 파라미터를 각 대상 노드에 일괄 전송";
      const fetchAllP = document.createElement("div");
      fetchAllP.className = "bmk-prm-tbtn";
      fetchAllP.textContent = "\u2193 모두 가져오기";
      fetchAllP.title = "체크된 파라미터의 현재 워크플로우 값을 읽어와 노트에 갱신";
      const copyBtn = document.createElement("div");
      copyBtn.className = "bmk-prm-tbtn";
      copyBtn.textContent = "복사";
      copyBtn.title = "체크된 파라미터를 클립보드로 복사 (다른 탭에서 붙여넣기 — 새로고침 시 소멸)";
      const pasteBtn = document.createElement("div");
      pasteBtn.className = "bmk-prm-tbtn";
      pasteBtn.textContent = "붙여넣기";
      pasteBtn.title = "복사해 둔 파라미터를 현재 탭 맨 뒤에 추가";
      // 프리셋 그룹 (서버 공유 — 모든 노드·워크플로우에서 같은 목록을 본다)
      // v6: 별도 줄이 아니라 같은 툴바에 세로 구분선으로 이어 붙인다.
      const presetSel = document.createElement("select");
      presetSel.className = "bmk-prm-preset";
      presetSel.title = "파라미터 프리셋 (서버 공유)";
      const presetLoadBtn = document.createElement("div");
      presetLoadBtn.className = "bmk-prm-tbtn";
      presetLoadBtn.textContent = "적용";
      presetLoadBtn.title = "선택한 프리셋으로 현재 탭 파라미터를 교체 (합치려면 복사/붙여넣기)";
      const presetSaveBtn = document.createElement("div");
      presetSaveBtn.className = "bmk-prm-tbtn";
      presetSaveBtn.textContent = "저장";
      presetSaveBtn.title = "현재 탭 파라미터 전체(체크 상태 포함)를 프리셋으로 저장 — 동명이면 덮어씀";
      const presetDelBtn = document.createElement("div");
      presetDelBtn.className = "bmk-prm-tbtn";
      presetDelBtn.textContent = "삭제";
      presetDelBtn.title = "선택한 프리셋 삭제";
      const barDiv = document.createElement("span");
      barDiv.className = "bmk-prm-divider";
      bar.append(sendAllP, fetchAllP, copyBtn, pasteBtn, barDiv,
                 presetSel, presetLoadBtn, presetSaveBtn, presetDelBtn);

      const listEl = document.createElement("div");
      listEl.className = "bmk-prm-list";

      // 노드/위젯 피커: #노드ID 조회 → 위젯 목록에서 골라 행으로 추가
      const addRow = document.createElement("div");
      addRow.className = "bmk-prm-add";
      const hashEl = document.createElement("span");
      hashEl.className = "bmk-send-hash";
      hashEl.textContent = "#";
      const idInput = document.createElement("input");
      idInput.type = "text";
      idInput.className = "bmk-prm-node";
      idInput.placeholder = "노드ID";
      idInput.title = "파라미터를 가져올 노드 ID (Enter = 위젯 조회)";
      const pickBtn = document.createElement("div");
      pickBtn.className = "bmk-prm-tbtn";
      pickBtn.textContent = "위젯 조회";
      pickBtn.title = "해당 노드의 위젯 목록에서 골라 파라미터로 추가 (타입·현재값 자동 캡처)";
      const blankBtn = document.createElement("div");
      blankBtn.className = "bmk-prm-tbtn";
      blankBtn.textContent = "+ 빈 행";
      blankBtn.title = "빈 파라미터 행 추가 (다른 워크플로우용 수동 기입)";
      const acEl = document.createElement("div");
      acEl.className = "bmk-ac bmk-prm-ac";
      acEl.style.display = "none";
      addRow.append(hashEl, idInput, pickBtn, blankBtn, acEl);

      body.append(bar, listEl, addRow);
      paramSection = { body, listEl, idInput, acEl, sendAllP, fetchAllP,
                       copyBtn, pasteBtn, presetSel, presetLoadBtn, presetSaveBtn, presetDelBtn,
                       pickBtn, blankBtn };
      getText = () => compileParams(currentTab()?.doc?.params || []);
    }

    /* 섹션별 복사 (요청 1-1: 헤더 구석 복사 아이콘) */
    let copyTimer = null;
    copy.addEventListener("click", async (e) => {
      e.stopPropagation();
      const text = getText();
      let ok = false;
      try {
        await navigator.clipboard.writeText(text);
        ok = true;
      } catch (err) {
        /* clipboard 권한 없을 때는 조용히 무시 */
      }
      if (ok) {
        copy.classList.add("copied");
        copy.innerHTML = ICON_CHECK;
        clearTimeout(copyTimer);
        copyTimer = setTimeout(() => {
          copy.classList.remove("copied");
          copy.innerHTML = ICON_COPY;
        }, 900);
      }
    });

    sec.append(head, body);
    sectionsEl.appendChild(sec);
  }

  for (const def of SECTION_DEFS) buildSection(def);

  // --- 자동완성 textarea 본문 복귀 방어 ---
  // ComfyUI(프론트 1.44+)의 DOM 위젯 스토어가 STRING 위젯 출신 textarea를 페이지 재로드 시
  // 자기 .dom-widget 레이어로 텔레포트시켜 본문(.bmk-sec-b)이 빈 껍데기가 되는 문제 우회.
  // 본문에서 빠져나가면 즉시 본문으로 되돌리고 스타일을 재적용한다(자동완성 인스턴스는 유지됨).
  const bodyObservers = [];
  const reclaimOne = (s) => {
    if (!s.el || !s.body || s.el.parentElement === s.body) return;
    const wrap = s.el.closest(".dom-widget");
    s.body.appendChild(s.el);
    if (wrap && !wrap.querySelector("textarea, input")) wrap.style.display = "none";
    s.el.style.minHeight = s.minH + "px";
    const h = state.sectionHeights[s.key];
    s.el.style.height = (Number.isFinite(h) && h >= s.minH) ? h + "px" : "";
  };
  const reclaimAll = () => { for (const s of textSections) reclaimOne(s); };
  const scheduleReclaims = () => {
    reclaimAll();
    if (typeof requestAnimationFrame === "function") {
      requestAnimationFrame(() => requestAnimationFrame(reclaimAll));
    }
    [40, 150, 400, 1000, 2500].forEach((ms) => setTimeout(reclaimAll, ms));
  };
  if (typeof MutationObserver !== "undefined") {
    for (const s of textSections) {
      if (!s.body) continue;
      const mo = new MutationObserver(() => {
        if (s.el && s.el.parentElement !== s.body) reclaimOne(s);
      });
      mo.observe(s.body, { childList: true });
      bodyObservers.push(mo);
    }
  }

  function setSectionsEnabled(on) {
    sectionsEl.style.opacity = on ? "" : "0.5";
    sectionsEl.style.pointerEvents = on ? "" : "none";
  }

  // 저장된 섹션 높이(노드별)를 이미 만들어진 섹션 DOM에 다시 적용.
  // 접힘/순서는 노트별(doc.ui)이라 syncEditor에서 적용한다.
  // 워크플로우 로드(applyValue/reload) 시 호출된다.
  function applySectionLayout() {
    applyingLayout = true; // 적용 중 발생하는 ResizeObserver 저장 억제
    for (const s of textSections) {
      const h = state.sectionHeights[s.key];
      if (Number.isFinite(h) && h >= s.minH) {
        s.el.style.height = h + "px";
      } else {
        // 비정상/미설정 값: inline height 제거(→ min-height 복귀) 및 state에서 정리(garbage 재저장 방지)
        s.el.style.height = "";
        if (s.key in state.sectionHeights) delete state.sectionHeights[s.key];
      }
    }
    // ResizeObserver는 레이아웃 후 비동기로 발화하므로, 다음 프레임까지 가드를 유지했다가 해제.
    if (typeof requestAnimationFrame === "function") {
      requestAnimationFrame(() => requestAnimationFrame(() => { applyingLayout = false; }));
    } else {
      applyingLayout = false;
    }
  }

  /* ---------- LoRA 행 렌더 ---------- */
  function renderLoras(loras) {
    if (!loraSection) return;
    const list = loraSection.listEl;
    list.textContent = "";
    if (!loras || !loras.length) {
      const empty = document.createElement("div");
      empty.className = "bmk-lora-empty";
      empty.textContent = "LoRA 없음";
      list.appendChild(empty);
      return;
    }
    for (const item of loras) list.appendChild(buildLoraRow(item, loras));
  }

  function buildLoraRow(item, loras) {
    const row = document.createElement("div");
    row.className = "bmk-lora-row" + (item.enabled ? "" : " off");

    const chk = document.createElement("input");
    chk.type = "checkbox";
    chk.className = "bmk-lora-chk";
    chk.checked = item.enabled;
    chk.title = "활성/비활성";
    chk.addEventListener("change", () => {
      item.enabled = chk.checked;
      row.classList.toggle("off", !chk.checked);
      store.touchDoc(state.activeCategory, state.activeTab, node);
      scheduleSave();
    });

    const name = document.createElement("input");
    name.type = "text";
    name.className = "bmk-lora-name";
    name.value = item.name;
    name.title = item.name;
    name.addEventListener("keydown", (e) => e.stopPropagation());
    name.addEventListener("input", () => {
      item.name = name.value.trim();
      name.title = item.name;
      store.touchDoc(state.activeCategory, state.activeTab, node);
      scheduleSave();
    });

    const slider = document.createElement("input");
    slider.type = "range";
    slider.className = "bmk-lora-slider";
    slider.min = "-1";
    slider.max = "2";
    slider.step = "0.05";
    slider.value = String(item.weight);
    slider.title = "가중치";

    const num = document.createElement("input");
    num.type = "number";
    num.className = "bmk-lora-num";
    num.step = "0.05";
    num.value = fmtWeight(item.weight);

    const setWeight = (w, fromSlider) => {
      if (!Number.isFinite(w)) return;
      item.weight = w;
      if (fromSlider) num.value = fmtWeight(w);
      else slider.value = String(w);
      store.touchDoc(state.activeCategory, state.activeTab, node);
      scheduleSave();
    };
    slider.addEventListener("input", () => setWeight(+slider.value, true));
    num.addEventListener("keydown", (e) => e.stopPropagation());
    num.addEventListener("input", () => setWeight(parseFloat(num.value), false));

    const del = document.createElement("div");
    del.className = "bmk-lora-del";
    del.textContent = "\u00D7";
    del.title = "삭제";
    del.addEventListener("click", () => {
      const i = loras.indexOf(item);
      if (i >= 0) loras.splice(i, 1);
      renderLoras(loras);
      store.touchDoc(state.activeCategory, state.activeTab, node);
      scheduleSave();
    });

    row.append(chk, name, slider, num, del);
    return row;
  }

  /* ---------- Params 섹션 로직 (v5) ---------- */
  // 워크플로우 파라미터: 대상 노드/위젯을 기억해뒀다가 값을 전송(↑)하거나
  // 현재 워크플로우 값을 역으로 가져와(↓) 노트에 기록한다.
  // 노드 ID는 워크플로우별이므로, 캡션에 현재 해석된 노드 타이틀을 항상 표시하고
  // 추가 당시 타이틀(hint)과 다르면 주황 경고로 불일치를 전송 전에 알린다.

  let prmDrag = null;      // 드래그 중인 param 항목 (참조)
  let prmDlSeq = 0;        // combo datalist 고유 id 시퀀스

  // v6: Params 결과 메시지도 브레드크럼 줄의 통합 상태줄로 표시한다.
  const showPrmMsg = showStatus;

  const clearPrmDropMarks = () => {
    paramSection?.listEl.querySelectorAll(".drop-above, .drop-below")
      .forEach((el) => el.classList.remove("drop-above", "drop-below"));
  };

  // 현재 탭의 params 배열 (없으면 생성). 저장 시 빈 배열은 정규화가 키를 생략한다.
  function paramsOf() {
    const t = currentTab();
    if (!t) return null;
    if (!t.doc) t.doc = emptyDoc();
    if (!Array.isArray(t.doc.params)) t.doc.params = [];
    return t.doc.params;
  }

  const touchAndSaveParams = () => {
    store.touchDoc(state.activeCategory, state.activeTab, node);
    scheduleSave();
  };

  // 대상 노드+위젯 해석. 실패 시 {err, neutral?} — neutral은 "미지정" 안내(회색 캡션).
  function resolveParamWidget(item) {
    const raw = String(item.node ?? "").trim();
    if (!raw) return { err: "대상 노드 미지정", neutral: true };
    const id = Number(raw);
    if (!Number.isFinite(id)) return { err: `잘못된 노드 ID: ${raw}` };
    const found = findNodeById(id);
    if (!found) return { err: `#${id} 노드를 찾을 수 없음` };
    if (!String(item.widget || "").trim())
      return { node: found, err: "위젯 이름 미지정", neutral: true };
    const w = (found.widgets || []).find((x) => x?.name === item.widget);
    if (!w) return { node: found, err: `#${id} ${nodeLabel(found)}: 위젯 "${item.widget}" 없음` };
    if (w.type === "converted-widget" || w.hidden)
      return { node: found, err: `위젯 "${item.widget}"이(가) 입력 포트로 전환되어 있음` };
    return { node: found, widget: w };
  }

  // 노트 값 → 워크플로우 위젯. 실제 위젯의 형식 기준으로 강제 변환하고
  // (노트의 type은 표시용일 뿐), combo는 옵션 목록을 검증해 오타 주입을 막는다.
  // rgthree Fast Muter/Bypasser(+Fast Groups) 계열 토글은 특수 취급한다.
  function injectParamToNode(item) {
    const r = resolveParamWidget(item);
    if (r.err) return { ok: false, message: r.err };
    const w = r.widget;
    const kind = detectWidgetType(w);
    let v = item.value;
    if (kind === "bool") {
      v = v === true || v === "true" || v === 1 || v === "1";
      // rgthree Fast Muter/Bypasser 계열 토글: callback이 전달 인자를 무시하고
      // "클릭 = 토글"(doModeChange() — force 없음 = 현재 모드 기준 반전)로 동작해,
      // 일반 경로(value 대입 + callback)로는 보낼 때마다 값이 뒤집힌다.
      // 위젯이 노출하는 doModeChange(force)에 원하는 값을 직접 지정하면
      // 연결 노드의 mode와 위젯 표시가 함께 그 값으로 고정된다.
      // (두 번째 인자 skipOtherNodeCheck는 생략 — "max one"/"always one"
      //  제약이 수동 클릭과 동일하게 적용되도록 둔다)
      if (typeof w.doModeChange === "function") {
        try {
          w.doModeChange(v);
        } catch (e) {
          console.warn("[BMK Notes] 파라미터 주입 오류:", e);
          return { ok: false, message: "주입 오류 (콘솔 확인)" };
        }
        markInjectDirty(r.node);
        return { ok: true, id: r.node.id, label: nodeLabel(r.node) };
      }
    } else if (kind === "int" || kind === "float") {
      v = Number(String(v).trim());
      if (String(item.value).trim() === "" || !Number.isFinite(v))
        return { ok: false, message: `숫자가 아닌 값: ${item.value === "" ? "(빈 값)" : item.value}` };
      if (kind === "int") v = Math.round(v);
      const o = w.options || {};
      if (typeof o.min === "number" && v < o.min) v = o.min;
      if (typeof o.max === "number" && v > o.max) v = o.max;
    } else if (kind === "combo") {
      v = String(v ?? "");
      const opts = comboValues(w);
      if (opts && !opts.some((x) => String(x) === v))
        return { ok: false, message: `콤보 옵션에 없는 값: ${v || "(빈 값)"}` };
    } else {
      v = String(v ?? "");
    }
    try {
      w.value = v;
      if (typeof w.callback === "function") w.callback(v);
    } catch (e) {
      console.warn("[BMK Notes] 파라미터 주입 오류:", e);
      return { ok: false, message: "주입 오류 (콘솔 확인)" };
    }
    markInjectDirty(r.node);
    return { ok: true, id: r.node.id, label: nodeLabel(r.node) };
  }

  // 워크플로우 위젯 → 노트 값 (값·타입·hint 재캡처). 렌더/저장은 호출부에서.
  function fetchParamFromNode(item) {
    const r = resolveParamWidget(item);
    if (r.err) return { ok: false, message: r.err };
    item.value = captureScalar(r.widget.value);
    item.type = detectWidgetType(r.widget);
    item.hint = nodeLabel(r.node);
    return { ok: true, id: r.node.id, label: nodeLabel(r.node) };
  }

  function renderParams(params) {
    if (!paramSection) return;
    const list = paramSection.listEl;
    list.textContent = "";
    if (!params || !params.length) {
      const empty = document.createElement("div");
      empty.className = "bmk-lora-empty";
      empty.textContent = "파라미터 없음 — 아래에서 #노드ID로 추가";
      list.appendChild(empty);
      return;
    }
    for (const item of params) list.appendChild(buildParamRow(item, params));
  }

  // 타입별 값 입력 컨트롤을 host에 (재)구성. combo는 대상 위젯에서 옵션을 읽어
  // datalist(브라우저 자동완성)로 제공한다 — 포커스 시점에 실시간 조회.
  function buildParamValueControl(item, host) {
    host.textContent = "";
    const type = item.type || "string";
    if (type === "bool") {
      const wrap = document.createElement("label");
      wrap.className = "bmk-prm-bool";
      const chk = document.createElement("input");
      chk.type = "checkbox";
      chk.className = "bmk-lora-chk";
      chk.checked = item.value === true || item.value === "true" || item.value === 1;
      const txt = document.createElement("span");
      txt.textContent = chk.checked ? "true" : "false";
      chk.addEventListener("change", () => {
        item.value = chk.checked;
        txt.textContent = chk.checked ? "true" : "false";
        touchAndSaveParams();
      });
      wrap.append(chk, txt);
      host.appendChild(wrap);
      return;
    }
    const input = document.createElement("input");
    input.className = "bmk-prm-val";
    input.title = "값";
    input.addEventListener("keydown", (e) => e.stopPropagation());
    if (type === "int" || type === "float") {
      input.type = "number";
      input.step = type === "int" ? "1" : "any";
      input.value = item.value === "" || item.value == null ? "" : String(item.value);
      input.addEventListener("input", () => {
        const n = Number(input.value);
        item.value = input.value !== "" && Number.isFinite(n) ? n : "";
        touchAndSaveParams();
      });
    } else {
      input.type = "text";
      input.value = item.value == null ? "" : String(item.value);
      input.addEventListener("input", () => {
        item.value = input.value;
        touchAndSaveParams();
      });
      if (type === "combo") {
        const dl = document.createElement("datalist");
        dl.id = `bmk-prm-dl-${node.id}-${prmDlSeq++}`;
        input.setAttribute("list", dl.id);
        input.addEventListener("focus", () => {
          const r = resolveParamWidget(item);
          const opts = r.widget ? comboValues(r.widget) : null;
          dl.textContent = "";
          for (const o of opts || []) {
            const op = document.createElement("option");
            op.value = String(o);
            dl.appendChild(op);
          }
        });
        host.appendChild(dl);
      }
    }
    host.appendChild(input);
  }

  function buildParamRow(item, params) {
    const row = document.createElement("div");
    row.className = "bmk-prm-row" + (item.enabled !== false ? "" : " off");

    /* 1행: 그립 · enabled · 제목 · 캡션(→ 대상 노드) · 전송 · 가져오기 · × */
    const l1 = document.createElement("div");
    l1.className = "bmk-prm-l";
    const grip = document.createElement("span");
    grip.className = "bmk-prm-grip";
    grip.textContent = "\u2807\u2807";
    grip.title = "드래그: 순서 변경";
    const chk = document.createElement("input");
    chk.type = "checkbox";
    chk.className = "bmk-lora-chk";
    chk.checked = item.enabled !== false;
    chk.title = "체크 해제 시 '모두 전송/가져오기'와 복사에서 제외 (개별 전송은 가능)";
    const label = document.createElement("input");
    label.type = "text";
    label.className = "bmk-prm-label";
    label.placeholder = "제목";
    label.value = item.label || "";
    label.title = "파라미터 제목 (사람용)";
    const sendB = document.createElement("div");
    sendB.className = "bmk-prm-btn";
    sendB.innerHTML = ICON_PARAM_SEND;
    sendB.title = "이 값을 대상 노드에 전송";
    const fetchB = document.createElement("div");
    fetchB.className = "bmk-prm-btn";
    fetchB.innerHTML = ICON_PARAM_FETCH;
    fetchB.title = "대상 노드의 현재 값을 읽어와 노트에 갱신";
    const del = document.createElement("div");
    del.className = "bmk-lora-del";
    del.textContent = "\u00D7";
    del.title = "삭제";
    // 캡션(해석된 대상 노드 타이틀)은 제목 오른쪽에 인라인 — 길면 제목칸이 먼저 수축
    const cap = document.createElement("span");
    cap.className = "bmk-prm-cap";
    l1.append(grip, chk, label, cap, sendB, fetchB, del);

    /* 2행: 타입 · #노드ID · 위젯 name · 값 */
    const l2 = document.createElement("div");
    l2.className = "bmk-prm-l";
    const typeSel = document.createElement("select");
    typeSel.className = "bmk-prm-type";
    typeSel.title = "값 입력 형식 (전송 시엔 실제 위젯 형식으로 재검증됨)";
    for (const t of ["combo", "int", "float", "bool", "string"]) {
      const op = document.createElement("option");
      op.value = t;
      op.textContent = t;
      typeSel.appendChild(op);
    }
    typeSel.value = ["combo", "int", "float", "bool"].includes(item.type) ? item.type : "string";
    const hash = document.createElement("span");
    hash.className = "bmk-send-hash";
    hash.textContent = "#";
    const nodeIn = document.createElement("input");
    nodeIn.type = "text";
    nodeIn.className = "bmk-prm-node";
    nodeIn.placeholder = "ID";
    nodeIn.value = item.node || "";
    nodeIn.title = "대상 노드 ID";
    const widgetIn = document.createElement("input");
    widgetIn.type = "text";
    widgetIn.className = "bmk-prm-widget";
    widgetIn.placeholder = "widget name";
    widgetIn.value = item.widget || "";
    widgetIn.title = "대상 위젯 name";
    const valHost = document.createElement("span");
    valHost.className = "bmk-prm-valhost";
    buildParamValueControl(item, valHost);
    l2.append(typeSel, hash, nodeIn, widgetIn, valHost);

    /* 캡션 갱신: 해석 결과 (미해석=빨강 / 미지정=회색 / hint 불일치=주황) */
    const updateCaption = () => {
      const r = resolveParamWidget(item);
      nodeIn.classList.toggle("bad", !!r.err && !r.neutral);
      if (r.err) {
        cap.textContent = r.err;
        cap.className = "bmk-prm-cap" + (r.neutral ? "" : " bad");
      } else {
        const lbl = nodeLabel(r.node);
        const mismatch = item.hint && item.hint !== lbl;
        cap.textContent = "\u2192 " + lbl + (mismatch ? ` (추가 당시: ${item.hint})` : "");
        cap.className = "bmk-prm-cap" + (mismatch ? " warn" : "");
      }
      cap.title = cap.textContent;
    };
    updateCaption();

    /* 이벤트 */
    chk.addEventListener("change", () => {
      item.enabled = chk.checked;
      row.classList.toggle("off", !chk.checked);
      touchAndSaveParams();
    });
    label.addEventListener("keydown", (e) => e.stopPropagation());
    label.addEventListener("input", () => {
      item.label = label.value;
      touchAndSaveParams();
    });
    nodeIn.addEventListener("keydown", (e) => e.stopPropagation());
    nodeIn.addEventListener("input", () => {
      item.node = nodeIn.value.trim();
      updateCaption();
      touchAndSaveParams();
    });
    widgetIn.addEventListener("keydown", (e) => e.stopPropagation());
    widgetIn.addEventListener("input", () => {
      item.widget = widgetIn.value.trim();
      updateCaption();
      touchAndSaveParams();
    });
    typeSel.addEventListener("change", () => {
      const t = typeSel.value;
      item.type = t;
      // 기존 값을 새 형식으로 최대한 변환
      if (t === "bool") {
        item.value = item.value === true || item.value === "true" || item.value === 1;
      } else if (t === "int" || t === "float") {
        const n = Number(item.value);
        item.value = Number.isFinite(n) && String(item.value).trim() !== ""
          ? (t === "int" ? Math.round(n) : n) : "";
      } else if (typeof item.value !== "string") {
        item.value = typeof item.value === "boolean"
          ? (item.value ? "true" : "false") : String(item.value ?? "");
      }
      buildParamValueControl(item, valHost);
      touchAndSaveParams();
    });
    sendB.addEventListener("click", () => {
      const r = injectParamToNode(item);
      showPrmMsg(
        `${item.label || item.widget || "?"}: ${r.ok ? `#${r.id} 전송됨 (${r.label})` : r.message}`,
        r.ok ? "ok" : "err");
    });
    fetchB.addEventListener("click", () => {
      const r = fetchParamFromNode(item);
      if (r.ok) {
        renderParams(params);
        touchAndSaveParams();
      }
      showPrmMsg(
        `${item.label || item.widget || "?"}: ${r.ok ? `#${r.id} 값 가져옴 (${r.label})` : r.message}`,
        r.ok ? "ok" : "err");
    });
    del.addEventListener("click", () => {
      const i = params.indexOf(item);
      if (i >= 0) params.splice(i, 1);
      renderParams(params);
      touchAndSaveParams();
    });

    /* 그립으로만 행 드래그 시작 (입력칸 텍스트 선택 드래그와 충돌 방지) */
    grip.addEventListener("pointerdown", () => { row.draggable = true; });
    row.addEventListener("pointerup", () => { row.draggable = false; });
    row.addEventListener("dragstart", (e) => {
      prmDrag = item;
      try {
        e.dataTransfer.effectAllowed = "move";
        e.dataTransfer.setData("text/plain", item.label || "");
      } catch (err) { /* noop */ }
      row.classList.add("bmk-prm-dragging");
    });
    row.addEventListener("dragend", () => {
      prmDrag = null;
      row.draggable = false;
      clearPrmDropMarks();
      row.classList.remove("bmk-prm-dragging");
    });
    row.addEventListener("dragover", (e) => {
      if (!prmDrag || prmDrag === item) return;
      e.preventDefault();
      clearPrmDropMarks();
      const r = row.getBoundingClientRect();
      row.classList.add(e.clientY < r.top + r.height / 2 ? "drop-above" : "drop-below");
    });
    row.addEventListener("drop", (e) => {
      if (!prmDrag || prmDrag === item) return;
      e.preventDefault();
      e.stopPropagation();
      const dragged = prmDrag;
      prmDrag = null;
      const rc = row.getBoundingClientRect();
      const below = e.clientY >= rc.top + rc.height / 2;
      clearPrmDropMarks();
      const from = params.indexOf(dragged);
      if (from < 0) return;
      params.splice(from, 1);
      let to = params.indexOf(item);
      if (below) to += 1;
      params.splice(to, 0, dragged);
      renderParams(params);
      touchAndSaveParams();
    });

    row.append(l1, l2);
    return row;
  }

  function sendAllParams() {
    const rows = (currentTab()?.doc?.params || []).filter((p) => p.enabled !== false);
    if (!rows.length) {
      showPrmMsg("전송할 파라미터가 없습니다 (체크 상태 확인)", "err");
      return;
    }
    let fail = false;
    let firstErr = "";
    const parts = rows.map((p) => {
      const r = injectParamToNode(p);
      if (!r.ok) {
        fail = true;
        if (!firstErr) firstErr = `${p.label || p.widget || "?"}: ${r.message}`;
      }
      return `${p.label || p.widget || "?"} ${r.ok ? "\u2713" : "\u2717"}`;
    });
    showPrmMsg(parts.join(" \u00B7 ") + (firstErr ? ` \u2014 ${firstErr}` : ""), fail ? "err" : "ok");
  }

  function fetchAllParams() {
    const params = currentTab()?.doc?.params || [];
    const rows = params.filter((p) => p.enabled !== false);
    if (!rows.length) {
      showPrmMsg("가져올 파라미터가 없습니다 (체크 상태 확인)", "err");
      return;
    }
    let okCount = 0;
    let fail = false;
    const parts = rows.map((p) => {
      const r = fetchParamFromNode(p);
      if (r.ok) okCount++;
      else fail = true;
      return `${p.label || p.widget || "?"} ${r.ok ? "\u2713" : "\u2717"}`;
    });
    if (okCount) {
      renderParams(params);
      touchAndSaveParams();
    }
    showPrmMsg(parts.join(" \u00B7 "), fail ? "err" : "ok");
  }

  function hideParamPicker() {
    if (paramSection) paramSection.acEl.style.display = "none";
  }

  function openParamPicker() {
    if (!paramSection) return;
    if (!currentTab()) {
      showPrmMsg("활성 탭이 없습니다", "err");
      return;
    }
    const ac = paramSection.acEl;
    ac.textContent = "";
    const raw = paramSection.idInput.value.trim();
    const id = Number(raw);
    if (!raw || !Number.isFinite(id)) {
      showPrmMsg("노드 ID를 입력하세요", "err");
      return;
    }
    const found = findNodeById(id);
    if (!found) {
      showPrmMsg(`#${id} 노드를 찾을 수 없음`, "err");
      return;
    }
    const ws = (found.widgets || []).filter((w) =>
      w && !w.hidden && w.type !== "converted-widget" &&
      (typeof w.value === "string" || typeof w.value === "number" || typeof w.value === "boolean"));
    const head = document.createElement("div");
    head.className = "bmk-ac-empty";
    head.textContent = ws.length
      ? `#${id} ${nodeLabel(found)} \u2014 추가할 위젯 선택 (여러 개 가능)`
      : `#${id} ${nodeLabel(found)}: 추가할 수 있는 위젯 없음`;
    ac.appendChild(head);
    for (const w of ws) {
      const it = document.createElement("div");
      it.className = "bmk-ac-item";
      const vs = String(w.value);
      it.textContent = `${w.name} (${detectWidgetType(w)}) = ${vs.length > 42 ? vs.slice(0, 42) + "\u2026" : vs}`;
      it.title = `${w.name} = ${vs}`;
      // mousedown(preventDefault)으로 처리 — idInput blur가 피커를 닫기 전에 추가
      it.addEventListener("mousedown", (e) => {
        e.preventDefault();
        addParamFromWidget(found, w);
      });
      ac.appendChild(it);
    }
    ac.style.display = "";
  }

  function addParamFromWidget(found, w) {
    const params = paramsOf();
    if (!params) return;
    params.push({
      label: w.name,
      node: String(found.id),
      widget: w.name,
      type: detectWidgetType(w),
      value: captureScalar(w.value),
      enabled: true,
      hint: nodeLabel(found),
    });
    renderParams(params);
    touchAndSaveParams();
    showPrmMsg(`추가됨: #${found.id}.${w.name}`, "ok");
  }

  function addBlankParam() {
    const params = paramsOf();
    if (!params) {
      showPrmMsg("활성 탭이 없습니다", "err");
      return;
    }
    // label 기본값을 줘서 "완전 빈 행"으로 판정돼 저장 시 제거되는 것을 방지
    params.push({ label: "새 파라미터", node: "", widget: "", type: "string",
                  value: "", enabled: true, hint: "" });
    renderParams(params);
    touchAndSaveParams();
  }

  /* ----- 탭 간 복사/붙여넣기 (페이지 전역 인메모리 클립보드) ----- */
  function copyParamsToClipboard() {
    const rows = (currentTab()?.doc?.params || []).filter((p) => p.enabled !== false);
    if (!rows.length) {
      showPrmMsg("복사할 파라미터가 없습니다 (체크 상태 확인)", "err");
      return;
    }
    // 정규화 + JSON 왕복으로 원본과 완전 분리된 사본을 보관
    paramClipboard = JSON.parse(JSON.stringify(normalizeParams(rows)));
    showPrmMsg(`${paramClipboard.length}행 복사됨 — 다른 탭에서 붙여넣기`, "ok");
  }

  function pasteParamsFromClipboard() {
    if (!paramClipboard || !paramClipboard.length) {
      showPrmMsg("복사된 파라미터가 없습니다", "err");
      return;
    }
    const params = paramsOf();
    if (!params) {
      showPrmMsg("활성 탭이 없습니다", "err");
      return;
    }
    for (const p of JSON.parse(JSON.stringify(paramClipboard))) params.push(p);
    renderParams(params);
    touchAndSaveParams();
    showPrmMsg(`${paramClipboard.length}행 붙여넣음`, "ok");
  }

  /* ----- 프리셋 (서버 공유 — 노트 루트의 .bmk_param_presets.json) ----- */
  let presetNamesKey = null; // 드롭다운 불필요 재구성 방지 (열려 있는 중 교란 최소화)

  function renderPresetOptions() {
    if (!paramSection) return;
    const sel = paramSection.presetSel;
    const names = (store.paramPresets || []).map((p) => p.name);
    const key = names.join("\n");
    if (key === presetNamesKey) return;
    presetNamesKey = key;
    const cur = sel.value;
    sel.textContent = "";
    const ph = document.createElement("option");
    ph.value = "";
    ph.textContent = names.length ? "프리셋…" : "프리셋 없음";
    sel.appendChild(ph);
    for (const n of names) {
      const op = document.createElement("option");
      op.value = n;
      op.textContent = n;
      sel.appendChild(op);
    }
    if (cur && names.includes(cur)) sel.value = cur;
  }

  function getParamPreset(name) {
    return (store.paramPresets || []).find((p) => p.name === name) || null;
  }

  function loadSelectedPreset() {
    const name = paramSection?.presetSel.value;
    if (!name) {
      showPrmMsg("적용할 프리셋을 선택하세요", "err");
      return;
    }
    const pr = getParamPreset(name);
    if (!pr) {
      showPrmMsg(`프리셋 "${name}"을(를) 찾을 수 없음`, "err");
      return;
    }
    const t = currentTab();
    if (!t) {
      showPrmMsg("활성 탭이 없습니다", "err");
      return;
    }
    if (!t.doc) t.doc = emptyDoc();
    const curLen = Array.isArray(t.doc.params) ? t.doc.params.length : 0;
    if (curLen &&
        !confirm(`현재 탭의 파라미터 ${curLen}행을 프리셋 "${name}"(${pr.params.length}행)으로 교체할까요?\n(합치려면 교체 대신 복사/붙여넣기를 사용하세요)`)) return;
    t.doc.params = JSON.parse(JSON.stringify(pr.params));
    renderParams(t.doc.params);
    touchAndSaveParams();
    showPrmMsg(`프리셋 "${name}" 적용됨 (${pr.params.length}행)`, "ok");
  }

  async function saveCurrentAsPreset() {
    const rows = normalizeParams(currentTab()?.doc?.params || []);
    if (!rows.length) {
      showPrmMsg("저장할 파라미터가 없습니다", "err");
      return;
    }
    const cur = paramSection?.presetSel.value || "";
    const name = (window.prompt("프리셋 이름 (동명이면 덮어씀):", cur) || "").trim();
    if (!name) return;
    try {
      await store.op("save_param_preset", { name, params: rows });
      // 트리 응답의 param_presets가 store 구독(tree 이벤트)에서 이미 반영됨
      if (paramSection) paramSection.presetSel.value = name;
      showPrmMsg(`프리셋 "${name}" 저장됨 (${rows.length}행)`, "ok");
    } catch (e) {
      showPrmMsg("프리셋 저장 실패: " + e.message, "err");
    }
  }

  async function deleteSelectedPreset() {
    const name = paramSection?.presetSel.value;
    if (!name) {
      showPrmMsg("삭제할 프리셋을 선택하세요", "err");
      return;
    }
    if (!confirm(`프리셋 "${name}"을(를) 삭제할까요?\n(모든 노드·워크플로우에서 함께 사라집니다)`)) return;
    try {
      await store.op("delete_param_preset", { name });
      showPrmMsg(`프리셋 "${name}" 삭제됨`, "ok");
    } catch (e) {
      showPrmMsg("프리셋 삭제 실패: " + e.message, "err");
    }
  }

  if (paramSection) {
    paramSection.sendAllP.addEventListener("click", sendAllParams);
    paramSection.fetchAllP.addEventListener("click", fetchAllParams);
    paramSection.copyBtn.addEventListener("click", copyParamsToClipboard);
    paramSection.pasteBtn.addEventListener("click", pasteParamsFromClipboard);
    paramSection.presetSel.addEventListener("keydown", (e) => e.stopPropagation());
    paramSection.presetLoadBtn.addEventListener("click", loadSelectedPreset);
    paramSection.presetSaveBtn.addEventListener("click", saveCurrentAsPreset);
    paramSection.presetDelBtn.addEventListener("click", deleteSelectedPreset);
    renderPresetOptions(); // 초기 상태 ("프리셋 없음" 자리표시 포함)
    // pickBtn/blankBtn은 mousedown preventDefault — idInput blur로 피커가 닫히는 것 방지
    paramSection.pickBtn.addEventListener("mousedown", (e) => e.preventDefault());
    paramSection.pickBtn.addEventListener("click", openParamPicker);
    paramSection.blankBtn.addEventListener("mousedown", (e) => e.preventDefault());
    paramSection.blankBtn.addEventListener("click", addBlankParam);
    const idIn = paramSection.idInput;
    idIn.addEventListener("keydown", (e) => {
      e.stopPropagation();
      if (e.key === "Enter") {
        e.preventDefault();
        openParamPicker();
      } else if (e.key === "Escape") {
        hideParamPicker();
      }
    });
    idIn.addEventListener("blur", () => setTimeout(hideParamPicker, 150));
  }

  node.addDOMWidget("notes_data", "BMKNOTES", container, {
    getValue: () =>
      JSON.stringify({
        mode: 2,
        activeCategory: state.activeCategory,
        activeTab: state.activeTab,
        sidebarWidth: state.sidebarWidth,
        collapsed: state.collapsed,
        targets: state.targets,
        sectionHeights: state.sectionHeights,
      }),
    setValue: (v) => applyValue(v),
    hideOnZoom: true,
    getMinHeight: () => 220,
  });

  /* ---------- helpers ---------- */
  function ensureActiveValid() {
    if (!store.tree) return;
    if (state.activeCategory && state.activeTab &&
        store.getTab(state.activeCategory, state.activeTab)) return;
    const first = store.firstTab();
    state.activeCategory = first?.cat ?? null;
    state.activeTab = first?.tab ?? null;
  }

  /* ---------- LoRA 주입 (Lora Loader (LoraManager) 등으로) ---------- */
  // LoraManager가 자체적으로 쓰는 updateNodeLoraCode 방식과 동일:
  // 대상 노드의 inputWidget(=widgets[0]) 값을 세팅하고 callback을 호출하면
  // mergeLoras()가 시각 위젯(슬라이더/토글)을 자동 갱신한다.
  const LM_CLASSES = [
    "Lora Loader (LoraManager)",
    "Lora Stacker (LoraManager)",
    "WanVideo Lora Select (LoraManager)",
  ];

  /* ---------- 그래프/서브그래프 탐색 ---------- */
  // ComfyUI 서브그래프 대응: 노드 ID는 그래프마다 독립이므로 루트만 봐서는
  // 서브그래프 내부 노드를 찾을 수 없다. 모든 그래프를 큐 방식으로 수집해
  // 순서대로 찾는다. 우선순위: 현재 보고 있는 그래프 → 이 노드의 그래프 → 루트
  // → 서브그래프 정의/인스턴스들. (같은 ID가 여러 그래프에 있으면 앞선 그래프가
  // 이긴다 — 전송 메시지에 대상 노드명을 함께 표시해 확인할 수 있게 한다)
  function collectGraphs() {
    const seen = new Set();
    const out = [];
    const push = (g) => { if (g && !seen.has(g)) { seen.add(g); out.push(g); } };
    push(app.canvas?.graph);
    push(node.graph);
    push(app.graph);
    const defs = app.graph?.subgraphs; // 신형 프론트: 루트에 등록된 서브그래프 정의 Map
    if (defs && typeof defs.values === "function") {
      try { for (const sg of defs.values()) push(sg); } catch (e) { /* noop */ }
    }
    for (let i = 0; i < out.length; i++) { // 큐: 새로 push된 그래프도 이어서 순회
      for (const n of out[i]?._nodes || []) if (n?.subgraph) push(n.subgraph);
    }
    return out;
  }

  function findNodeById(id) {
    for (const g of collectGraphs()) {
      const n = g?.getNodeById?.(id) ?? g?._nodes_by_id?.[id];
      if (n) return n;
    }
    return null;
  }

  const isSubgraphNode = (n) => !!n?.subgraph;
  const nodeLabel = (n) => String(n?.title || n?.comfyClass || n?.type || "").trim();

  // "진짜 텍스트칸"으로 볼 수 있는 위젯 판정 (서브그래프 내부 자동 탐색용 — 보수적).
  // 콤보(sampler_name 등)도 값이 문자열이라, 내부 스캔은 멀티라인/알려진 이름만 허용.
  // 입력 포트로 전환된 위젯(converted-widget)·숨김 위젯은 제외 — 포트 연결방식에서는
  // 서브그래프 노드 자신의 위젯이 정본이므로 내부에 쓰면 안 된다.
  const TEXT_WIDGET_NAMES = ["text", "string", "prompt", "text_g", "text_l",
    "wildcard_text", "populated_text", "positive", "negative", "Text", "STRING"];
  const isTextishWidget = (w) =>
    !!w && typeof w.value === "string" && !w.hidden && w.type !== "converted-widget" &&
    (w.type === "customtext" ||
     (w.inputEl && w.inputEl.tagName === "TEXTAREA") ||
     TEXT_WIDGET_NAMES.includes(w.name));

  // 서브그래프 내부에서 텍스트 위젯을 가진 첫 노드를 탐색 (직계 우선, 중첩 4단계까지)
  function findTextWidgetInSubgraph(sgNode, depth) {
    const sg = sgNode?.subgraph;
    if (!sg || depth > 4) return null;
    for (const n of sg._nodes || []) {
      const w = (n.widgets || []).find(isTextishWidget);
      if (w) return { node: n, widget: w };
    }
    for (const n of sg._nodes || []) {
      if (n?.subgraph) {
        const hit = findTextWidgetInSubgraph(n, depth + 1);
        if (hit) return hit;
      }
    }
    return null;
  }

  // 서브그래프 내부에서 LoraManager 계열 노드를 탐색 (직계 우선, 중첩 4단계까지)
  function findLoraNodeInSubgraph(sgNode, depth) {
    const sg = sgNode?.subgraph;
    if (!sg || depth > 4) return null;
    for (const n of sg._nodes || []) {
      if (LM_CLASSES.includes(n.comfyClass) || n.inputWidget) return n;
    }
    for (const n of sg._nodes || []) {
      if (n?.subgraph) {
        const hit = findLoraNodeInSubgraph(n, depth + 1);
        if (hit) return hit;
      }
    }
    return null;
  }

  const markInjectDirty = (target) => {
    target.setDirtyCanvas?.(true, true);
    target.graph?.setDirtyCanvas?.(true, true);
    app.graph?.setDirtyCanvas?.(true, true);
    app.canvas?.setDirty?.(true, true);
  };

  function injectLorasToNode(rawId, mode = "replace") {
    const id = Number(String(rawId ?? "").trim());
    if (!Number.isFinite(id)) return { ok: false, message: "노드 ID를 입력하세요" };
    const found = findNodeById(id);
    if (!found) return { ok: false, message: `#${id} 노드를 찾을 수 없음` };
    // 서브그래프 노드를 지정했고 자신이 LoraManager가 아니면 내부에서 찾는다
    let target = found;
    let via = "";
    if (isSubgraphNode(found) && !LM_CLASSES.includes(found.comfyClass) && !found.inputWidget) {
      const inner = findLoraNodeInSubgraph(found, 0);
      if (inner) { target = inner; via = `내부#${inner.id} `; }
    }
    const w = target.inputWidget || target.widgets?.[0];
    const looksLM = LM_CLASSES.includes(target.comfyClass) || !!target.inputWidget;
    if (!w || !looksLM) {
      return { ok: false, message: `#${id}는 Lora Loader가 아님` };
    }
    const loras = currentTab()?.doc?.loras || [];
    const code = compileLoras(loras);
    const count = loras.filter((l) => l.enabled && l.name).length;

    // 값 세팅 + 콜백 호출(= LoraManager의 mergeLoras 동기화 트리거)
    const setVal = (v) => {
      w.value = v;
      try {
        if (typeof w.callback === "function") w.callback(v);
      } catch (e) {
        console.warn("[BMK Notes] LoRA 주입 callback 오류:", e);
      }
    };

    if (mode === "replace") {
      // LoraManager의 mergeLoras는 "이미 슬라이더에 있는 로라"의 기존 가중치를
      // 텍스트 값보다 우선한다. 그래서 먼저 빈 값으로 슬라이더를 비운 뒤(=baseline []),
      // 실제 값을 보내야 주입한 가중치/활성상태가 그대로 반영된다.
      setVal("");
      setVal(code);
    } else {
      const cur = (w.value || "").trim();
      setVal(cur ? `${cur} ${code}` : code);
    }

    markInjectDirty(target);
    return { ok: true, id, count, label: via + nodeLabel(target) };
  }

  // 대상 노드에서 텍스트(문자열) 위젯을 탐색. 위젯 이름/타입이 노드마다 달라
  // 우선순위 기반으로 가장 적절한 STRING 위젯을 찾는다.
  function findTextWidget(node) {
    const ws = node?.widgets;
    if (!Array.isArray(ws) || !ws.length) return null;
    const isStr = (w) => typeof w?.value === "string";
    // 1) 이름이 정확히 "text" (CLIP Text Encode, WAS Text Multiline 등 대다수)
    let w = ws.find((x) => x.name === "text" && isStr(x));
    if (w) return w;
    // 2) 흔한 문자열 위젯 이름
    const NAMES = ["string", "value", "prompt", "text_g", "text_l", "wildcard_text", "populated_text", "positive", "negative", "Text", "STRING"];
    w = ws.find((x) => NAMES.includes(x.name) && isStr(x));
    if (w) return w;
    // 3) 문자열형 위젯 타입
    w = ws.find((x) => (x.type === "customtext" || x.type === "string" || x.type === "text") && isStr(x));
    if (w) return w;
    // 4) textarea/input(inputEl)을 가진 위젯
    w = ws.find((x) => x.inputEl && (x.inputEl.tagName === "TEXTAREA" || x.inputEl.tagName === "INPUT"));
    if (w) return w;
    // 5) 값이 문자열인 첫 위젯
    w = ws.find(isStr);
    return w || null;
  }

  // 대상 노드의 텍스트 위젯에 문자열을 주입(전송).
  // 서브그래프 노드를 지정한 경우:
  //  - "Shown on node"(승격) 방식: 겉면 위젯은 프록시라 값을 써도 내부 정본에
  //    반영되지 않을 수 있다 → 내부의 실제 텍스트 위젯에 먼저 쓰고(정본),
  //    겉면 프록시는 표시만 동기화한다(best-effort).
  //  - 포트 연결방식: 내부 위젯이 입력 포트로 전환돼 내부 스캔에서 제외되므로,
  //    기존처럼 서브그래프 노드 자신의 위젯(정본)에 주입된다.
  function injectTextToNode(rawId, text) {
    const id = Number(String(rawId ?? "").trim());
    if (!Number.isFinite(id)) return { ok: false, message: "노드 ID를 입력하세요" };
    const found = findNodeById(id);
    if (!found) return { ok: false, message: `#${id} 노드를 찾을 수 없음` };
    let target = found;
    let w = null;
    let via = "";
    if (isSubgraphNode(found)) {
      const hit = findTextWidgetInSubgraph(found, 0);
      if (hit) {
        target = hit.node;
        w = hit.widget;
        via = `내부#${hit.node.id} `;
      }
    }
    if (!w) w = findTextWidget(target);
    if (!w) return { ok: false, message: `#${id}에 텍스트 위젯 없음` };
    const val = text ?? "";
    try {
      w.value = val;
      if (w.inputEl && "value" in w.inputEl) w.inputEl.value = val; // multiline textarea 표시 갱신
      if (typeof w.callback === "function") w.callback(val);
      // 내부에 썼다면 서브그래프 노드 겉면의 승격(proxy) 위젯도 표시 동기화
      if (target !== found) {
        const pw = findTextWidget(found);
        if (pw && pw !== w) {
          try {
            pw.value = val;
            if (pw.inputEl && "value" in pw.inputEl) pw.inputEl.value = val;
          } catch (e) { /* noop */ }
        }
      }
    } catch (e) {
      console.warn("[BMK Notes] 텍스트 주입 오류:", e);
      return { ok: false, message: `#${id} 주입 오류` };
    }
    markInjectDirty(target);
    return { ok: true, id, label: via + nodeLabel(target) };
  }

  /* ---------- 현재 탭 / 문서 저장 (디바운스 + 플러시) ---------- */
  let pendingSave = null; // { cat, tab }
  let saveTimer = null;

  function currentTab() {
    return state.activeCategory && state.activeTab
      ? store.getTab(state.activeCategory, state.activeTab) : null;
  }

  function scheduleSave() {
    if (!state.activeCategory || !state.activeTab) return;
    pendingSave = { cat: state.activeCategory, tab: state.activeTab };
    clearTimeout(saveTimer);
    saveTimer = setTimeout(flushSave, 500);
  }

  async function flushSave() {
    clearTimeout(saveTimer);
    if (!pendingSave) return;
    const p = pendingSave;
    pendingSave = null;
    const t = store.getTab(p.cat, p.tab);
    if (!t) return;
    try {
      await store.op("save_doc", { category: p.cat, tab: p.tab, doc: t.doc || emptyDoc() });
    } catch (e) {
      console.warn("[BMK Notes] 저장 실패:", e);
      alert("노트 저장 실패: " + e.message);
    }
  }

  /* ---------- 에디터 동기화 (활성 문서 → 섹션 위젯) ---------- */
  function syncEditor(preserveFocused = false) {
    const t = currentTab();
    if (!t) {
      crumbText.textContent = store.tree ? "탭이 없습니다" : "동기화 중…";
      setSectionsEnabled(false);
      for (const s of textSections) { s.el.value = ""; applyMdView(s, null); }
      renderLoras([]);
      renderParams([]);
      return;
    }
    if (!t.doc) t.doc = emptyDoc();
    crumbText.textContent = `${state.activeCategory} / ${state.activeTab}`;
    setSectionsEnabled(true);
    // 섹션 순서·접힘을 현재 노트(doc.ui)에 맞춰 적용
    applyOrder(docOrder(t.doc));
    for (const ref of sectionRefs) ref.applyCollapse(docCollapsed(t.doc, ref.key));
    for (const s of textSections) {
      const focusedHere = document.activeElement === s.el;
      const v = t.doc[s.key] ?? "";
      if (!(preserveFocused && focusedHere) && s.el.value !== v) s.el.value = v;
      // v7: 노트별 마크다운 프리뷰 적용. 이 노드에서 편집 중(포커스)일 땐
      // 다른 노드발 토글로 화면이 전환되지 않게 보호(다음 동기화 때 반영).
      if (!(preserveFocused && focusedHere)) applyMdView(s, t.doc);
    }
    if (loraSection) {
      const focusedInLora = loraSection.body.contains(document.activeElement);
      if (!(preserveFocused && focusedInLora)) renderLoras(t.doc.loras || []);
    }
    if (paramSection) {
      const focusedInPrm = paramSection.body.contains(document.activeElement);
      if (!(preserveFocused && focusedInPrm)) renderParams(t.doc.params || []);
    }
  }

  /* ---------- export (공유 트리 전체) ---------- */
  async function exportAll() {
    await flushSave();
    if (!store.tree) { alert("아직 동기화되지 않았습니다."); return; }
    const d = new Date();
    const pad = (n) => String(n).padStart(2, "0");
    const root =
      `BMK_Notes_${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}` +
      `_${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}`;

    const enc = new TextEncoder();
    const files = []; // { parts: ["카테고리","하위",...], name: "탭.json", data }
    const walkExport = (cats, parentParts) => {
      const used = new Set();
      for (const cat of cats || []) {
        const base = sanitizeName(cat.name, "category");
        let cname = base;
        for (let i = 2; used.has(cname.toLowerCase()); i++) cname = `${base} (${i})`;
        used.add(cname.toLowerCase());
        const parts = [...parentParts, cname];
        const usedTab = new Set();
        for (const tab of cat.tabs) {
          const tbase = sanitizeName(tab.name, "note");
          let tname = tbase;
          for (let j = 2; usedTab.has(tname.toLowerCase()); j++) tname = `${tbase} (${j})`;
          usedTab.add(tname.toLowerCase());
          files.push({ parts, name: tname + ".json", data: enc.encode(JSON.stringify(normalizeDoc(tab.doc), null, 1)) });
        }
        walkExport(cat.children, parts); // 중첩 카테고리 재귀
      }
    };
    walkExport(store.tree, []);
    if (!files.length) { alert("내보낼 탭이 없습니다."); return; }

    if (window.showDirectoryPicker) {
      try {
        const dir = await window.showDirectoryPicker({ mode: "readwrite" });
        const rootDir = await dir.getDirectoryHandle(root, { create: true });
        for (const f of files) {
          let dir = rootDir;
          for (const seg of f.parts) dir = await dir.getDirectoryHandle(seg, { create: true });
          const fh = await dir.getFileHandle(f.name, { create: true });
          const w = await fh.createWritable();
          await w.write(f.data);
          await w.close();
        }
        alert(`내보내기 완료: ${root}\n파일 ${files.length}개`);
        return;
      } catch (err) {
        if (err?.name === "AbortError") return;
        console.warn("[BMK Notes] 폴더 내보내기 실패, ZIP으로 폴백:", err);
      }
    }
    const blob = makeZip(files.map((f) => ({ path: `${root}/${f.parts.join("/")}/${f.name}`, data: f.data })));
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = root + ".zip";
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 10000);
  }
  exportBtn.addEventListener("click", (e) => { e.stopPropagation(); exportAll(); });

  /* ---------- refresh ---------- */
  async function doRefresh() {
    await flushSave();
    refreshBtn.classList.add("spin");
    try {
      await store.refresh();
    } catch (e) {
      alert("동기화 실패: " + e.message);
    } finally {
      refreshBtn.classList.remove("spin");
    }
  }
  refreshBtn.addEventListener("click", (e) => { e.stopPropagation(); doRefresh(); });

  /* ---------- legacy import ---------- */
  function maybePromptImport() {
    if (!pendingLegacy || importPrompted || !store.tree) return;
    importPrompted = true;
    setTimeout(() => {
      if (confirm(
        "이 노드에 구버전(내장형) 노트 데이터가 있습니다.\n" +
        "공유 노트 폴더로 가져올까요?\n" +
        "(취소해도 노드 우클릭 메뉴에서 나중에 가져올 수 있습니다)"
      )) importLegacy();
    }, 50);
  }

  async function importLegacy() {
    if (!pendingLegacy) return;
    try {
      await store.op("import_embedded", { data: pendingLegacy });
      pendingLegacy = null;
      ensureActiveValid();
      markChanged();
      alert("가져오기 완료. 노트가 공유 폴더에 병합되었습니다.");
    } catch (e) {
      alert("가져오기 실패: " + e.message);
    }
  }

  /* ---------- rename helper ---------- */
  function startRename(nameSpan, current, onCommit) {
    renameOpen = true;
    const input = document.createElement("input");
    input.className = "bmk-rename";
    input.value = current;
    input.addEventListener("keydown", (e) => {
      e.stopPropagation();
      if (e.key === "Enter") input.blur();
      else if (e.key === "Escape") { input.value = current; input.blur(); }
    });
    input.addEventListener("pointerdown", (e) => e.stopPropagation());
    input.addEventListener("blur", () => {
      const v = sanitizeName(input.value, "");
      renameOpen = false;
      if (renderQueued) {
        renderQueued = false;
        render();
        syncEditor(true);
      }
      onCommit(v || current);
    });
    nameSpan.replaceWith(input);
    input.focus();
    input.select();
  }

  /* ---------- drag state ---------- */
  let drag = null; // { kind: "tab", cat(경로), name } | { kind: "cat", path, name }
  const clearDropMarks = () => {
    catList.querySelectorAll(".drop-above, .drop-below, .drop-into")
      .forEach((el) => el.classList.remove("drop-above", "drop-below", "drop-into"));
  };

  /* ---------- render ---------- */
  // v4: 카테고리를 재귀 렌더한다. 식별자는 경로(cat.path, 예: "a/b/c").
  //  - 하위 카테고리 블록 먼저, 그 아래 탭 블록 (탐색기 관례)
  //  - 들여쓰기는 .bmk-body(레벨당 9px). 깊어지면 목록에 가로 스크롤이 생긴다.
  //  - 카테고리 드래그 시 헤더 3분할: 상단¼=위 형제 / 하단¼=아래 형제 / 중앙½=안으로 중첩

  function countTree(cat) {
    let tabs = cat.tabs.length;
    let cats = 0;
    for (const c of cat.children) {
      const r = countTree(c);
      tabs += r.tabs;
      cats += 1 + r.cats;
    }
    return { tabs, cats };
  }

  function buildTabItem(cat, tab) {
    const item = document.createElement("div");
    const isActive = cat.path === state.activeCategory && tab.name === state.activeTab;
    item.className = "bmk-tab" + (isActive ? " active" : "");
    item.draggable = true;
    item.dataset.cat = cat.path;
    item.dataset.name = tab.name;

    const tIco = document.createElement("span");
    tIco.className = "bmk-tab-ico";
    tIco.innerHTML = ICON_TAB;

    const tName = document.createElement("span");
    tName.className = "bmk-name";
    tName.textContent = tab.name;
    tName.title = tab.name + " (더블클릭: 이름 변경)";

    const tDup = document.createElement("span");
    tDup.className = "bmk-btn bmk-dup";
    tDup.innerHTML = ICON_DUPLICATE;
    tDup.title = "탭 복제 (바로 아래에 사본 생성)";

    const tDel = document.createElement("span");
    tDel.className = "bmk-btn bmk-del";
    tDel.textContent = "\u00D7";
    tDel.title = "탭 삭제 (.trash로 이동)";

    item.append(tIco, tName, tDup, tDel);

    item.addEventListener("click", async () => {
      if (state.activeCategory === cat.path && state.activeTab === tab.name) return;
      await flushSave();
      state.activeCategory = cat.path;
      state.activeTab = tab.name;
      catList.querySelectorAll(".bmk-tab.active").forEach((el) => el.classList.remove("active"));
      item.classList.add("active");
      syncEditor();
      markChanged();
    });
    tName.addEventListener("dblclick", (e) => {
      e.stopPropagation();
      item.draggable = false;
      startRename(tName, tab.name, async (v) => {
        if (v === tab.name) { render(); return; }
        try {
          await store.op("rename_tab", { category: cat.path, old: tab.name, new: v });
        } catch (err) { alert(err.message); render(); }
      });
    });
    tDel.addEventListener("click", async (e) => {
      e.stopPropagation();
      if (!docIsEmpty(tab.doc) &&
          !confirm(`탭 "${tab.name}"을(를) 삭제할까요?\n(노트 폴더의 .trash로 이동됩니다)`)) return;
      try {
        await store.op("delete_tab", { category: cat.path, tab: tab.name });
      } catch (err) { alert(err.message); }
    });
    tDup.addEventListener("click", async (e) => {
      e.stopPropagation();
      try {
        await flushSave(); // 원본의 미저장 편집을 먼저 디스크에 반영
        const res = await store.op("duplicate_tab", { category: cat.path, tab: tab.name });
        const newName = res && res.new_tab;
        if (newName) {
          state.activeCategory = cat.path;
          state.activeTab = newName;
          markChanged();
          render();
          syncEditor();
        }
      } catch (err) { alert(err.message); }
    });

    item.addEventListener("dragstart", (e) => {
      drag = { kind: "tab", cat: cat.path, name: tab.name };
      e.dataTransfer.effectAllowed = "move";
      e.dataTransfer.setData("text/plain", tab.name);
      item.classList.add("bmk-dragging");
    });
    item.addEventListener("dragend", () => { drag = null; clearDropMarks(); item.classList.remove("bmk-dragging"); });
    item.addEventListener("dragover", (e) => {
      if (!drag || drag.kind !== "tab") return;
      if (drag.cat === cat.path && drag.name === tab.name) return;
      e.preventDefault();
      clearDropMarks();
      const r = item.getBoundingClientRect();
      item.classList.add(e.clientY < r.top + r.height / 2 ? "drop-above" : "drop-below");
    });
    item.addEventListener("drop", async (e) => {
      if (!drag || drag.kind !== "tab") return;
      if (drag.cat === cat.path && drag.name === tab.name) return;
      e.preventDefault();
      e.stopPropagation();
      const dragged = drag;
      drag = null;
      clearDropMarks();
      // index = 원본 제거 후 대상 목록에서의 최종 삽입 위치
      const targetTabs = cat.tabs
        .filter((t) => !(dragged.cat === cat.path && t.name === dragged.name))
        .map((t) => t.name);
      let idx = targetTabs.indexOf(tab.name);
      const r = item.getBoundingClientRect();
      if (e.clientY >= r.top + r.height / 2) idx += 1;
      try {
        await store.op("move_tab", {
          from_category: dragged.cat, tab: dragged.name,
          to_category: cat.path, index: idx,
        });
      } catch (err) { alert(err.message); }
    });

    return item;
  }

  // cat: 렌더할 카테고리, siblings: 같은 레벨의 형제 배열, parentPath: 부모 경로("" = 루트)
  function renderCat(cat, siblings, parentPath) {
    const catEl = document.createElement("div");
    catEl.className = "bmk-cat";

    const head = document.createElement("div");
    head.className = "bmk-cat-h";
    head.draggable = true;
    head.dataset.path = cat.path;

    const isCollapsed = !!state.collapsed[cat.path];

    const arrow = document.createElement("span");
    arrow.className = "bmk-arrow";
    arrow.textContent = isCollapsed ? "\u25B8" : "\u25BE";

    const folder = document.createElement("span");
    folder.className = "bmk-cat-ico";
    folder.innerHTML = isCollapsed ? ICON_FOLDER : ICON_FOLDER_OPEN;

    const name = document.createElement("span");
    name.className = "bmk-name";
    name.textContent = cat.name;
    name.title = cat.path + " (더블클릭: 이름 변경)";

    const subBtn = document.createElement("span");
    subBtn.className = "bmk-btn bmk-sub";
    subBtn.innerHTML = ICON_FOLDER_PLUS;
    subBtn.title = "하위 카테고리 추가";

    const addBtn = document.createElement("span");
    addBtn.className = "bmk-btn";
    addBtn.textContent = "+";
    addBtn.title = "탭 추가";

    const delBtn = document.createElement("span");
    delBtn.className = "bmk-btn bmk-del";
    delBtn.textContent = "\u00D7";
    delBtn.title = "카테고리 삭제 (하위 포함 .trash로 이동)";

    head.append(arrow, folder, name, subBtn, addBtn, delBtn);

    // 하위 카테고리 + 탭 컨테이너 (접힘 시 통째로 숨김, 레벨당 들여쓰기)
    const body = document.createElement("div");
    body.className = "bmk-body";
    if (isCollapsed) body.style.display = "none";

    // 더블클릭(이름 변경)과 충돌하지 않도록 토글을 짧게 지연 후 수행.
    // 접힘 상태는 노드별 개별 상태(경로 키) — 서버/다른 노드에 전파하지 않는다.
    let collapseTimer = null;
    const applyCollapse = (collapsed) => {
      arrow.textContent = collapsed ? "\u25B8" : "\u25BE";
      folder.innerHTML = collapsed ? ICON_FOLDER : ICON_FOLDER_OPEN;
      body.style.display = collapsed ? "none" : "";
    };
    head.addEventListener("click", () => {
      clearTimeout(collapseTimer);
      collapseTimer = setTimeout(() => {
        const next = !state.collapsed[cat.path];
        if (next) state.collapsed[cat.path] = true;
        else delete state.collapsed[cat.path];
        applyCollapse(next);
        markChanged();
      }, 220);
    });
    head.addEventListener("dblclick", () => clearTimeout(collapseTimer));
    name.addEventListener("dblclick", (e) => {
      e.stopPropagation();
      clearTimeout(collapseTimer);
      head.draggable = false;
      startRename(name, cat.name, async (v) => {
        if (v === cat.name) { render(); return; }
        try {
          // old = 경로, new = 새 이름(마지막 세그먼트) — 자손 경로 재매핑은 서버 remap으로
          await store.op("rename_category", { old: cat.path, new: v });
        } catch (err) { alert(err.message); render(); }
      });
    });
    subBtn.addEventListener("click", async (e) => {
      e.stopPropagation();
      clearTimeout(collapseTimer);
      const cname = dedupeName("새 카테고리", cat.children.map((c) => c.name));
      pendingRename = { kind: "cat", path: cat.path + "/" + cname };
      if (state.collapsed[cat.path]) { delete state.collapsed[cat.path]; markChanged(); } // 새 항목이 보이게 펼침
      try {
        await store.op("create_category", { name: cname, parent: cat.path });
      } catch (err) { pendingRename = null; alert(err.message); }
    });
    addBtn.addEventListener("click", async (e) => {
      e.stopPropagation();
      clearTimeout(collapseTimer);
      const tname = dedupeName("새 탭", cat.tabs.map((t) => t.name));
      pendingRename = { kind: "tab", cat: cat.path, name: tname };
      if (state.collapsed[cat.path]) { delete state.collapsed[cat.path]; markChanged(); } // 새 탭이 보이게 펼침
      try {
        await store.op("create_tab", { category: cat.path, name: tname });
        state.activeCategory = cat.path;
        state.activeTab = tname;
        markChanged();
        render();
        syncEditor();
      } catch (err) { pendingRename = null; alert(err.message); }
    });
    delBtn.addEventListener("click", async (e) => {
      e.stopPropagation();
      const n = countTree(cat);
      if ((n.tabs > 0 || n.cats > 0) &&
          !confirm(`카테고리 "${cat.path}"을(를) 삭제할까요?\n(하위 카테고리 ${n.cats}개, 탭 ${n.tabs}개 포함 — .trash로 이동)`)) return;
      try {
        await store.op("delete_category", { name: cat.path });
      } catch (err) { alert(err.message); }
    });

    head.addEventListener("dragstart", (e) => {
      drag = { kind: "cat", path: cat.path, name: cat.name };
      e.dataTransfer.effectAllowed = "move";
      e.dataTransfer.setData("text/plain", cat.path);
      head.classList.add("bmk-dragging");
    });
    head.addEventListener("dragend", () => { drag = null; clearDropMarks(); head.classList.remove("bmk-dragging"); });
    head.addEventListener("dragover", (e) => {
      if (!drag) return;
      if (drag.kind === "cat") {
        // 자기 자신/자손으로는 드롭 불가 (순환 — 서버도 재차 검증함)
        if (drag.path === cat.path || cat.path.startsWith(drag.path + "/")) return;
        e.preventDefault();
        clearDropMarks();
        const r = head.getBoundingClientRect();
        const y = e.clientY - r.top;
        if (y < r.height * 0.25) head.classList.add("drop-above");
        else if (y > r.height * 0.75) head.classList.add("drop-below");
        else head.classList.add("drop-into"); // 안으로 중첩
      } else { // tab → 이 카테고리 안으로
        e.preventDefault();
        clearDropMarks();
        head.classList.add("drop-into");
      }
    });
    head.addEventListener("drop", async (e) => {
      if (!drag) return;
      e.preventDefault();
      e.stopPropagation();
      const dragged = drag;
      drag = null;
      const r = head.getBoundingClientRect();
      const y = e.clientY - r.top;
      clearDropMarks();
      try {
        if (dragged.kind === "tab") {
          if (dragged.cat === cat.path) return;
          await store.op("move_tab", {
            from_category: dragged.cat, tab: dragged.name,
            to_category: cat.path, index: cat.tabs.length,
          });
        } else {
          if (dragged.path === cat.path || cat.path.startsWith(dragged.path + "/")) return;
          if (y >= r.height * 0.25 && y <= r.height * 0.75) {
            // 중앙 드롭: 이 카테고리 "안"의 맨 뒤로 중첩
            await store.op("move_category", {
              name: dragged.path, to_parent: cat.path, index: cat.children.length,
            });
          } else {
            // 상/하단 드롭: 이 카테고리의 "형제"로 이동 (부모가 달라도 됨)
            // index = 원본 제거 후 대상 형제 목록에서의 최종 삽입 위치
            const rest = siblings.filter((c) => c.path !== dragged.path).map((c) => c.path);
            let idx = rest.indexOf(cat.path);
            if (y > r.height * 0.75) idx += 1;
            await store.op("move_category", {
              name: dragged.path, to_parent: parentPath, index: idx,
            });
          }
        }
      } catch (err) { alert(err.message); }
    });

    catEl.appendChild(head);
    for (const child of cat.children) body.appendChild(renderCat(child, cat.children, cat.path));
    for (const tab of cat.tabs) body.appendChild(buildTabItem(cat, tab));
    catEl.appendChild(body);
    return catEl;
  }

  function render() {
    if (renameOpen) { renderQueued = true; return; }
    catList.textContent = "";
    side.style.width = state.sidebarWidth + "px";

    if (!store.tree) {
      const loading = document.createElement("div");
      loading.className = "bmk-loading";
      loading.textContent = "동기화 중…";
      catList.appendChild(loading);
      return;
    }

    for (const cat of store.tree) catList.appendChild(renderCat(cat, store.tree, ""));

    // 새로 만든 탭/카테고리는 곧바로 이름 입력 모드로
    if (pendingRename) {
      const sel = pendingRename;
      pendingRename = null;
      let span = null;
      if (sel.kind === "tab") {
        for (const el of catList.querySelectorAll(".bmk-tab")) {
          if (el.dataset.cat === sel.cat && el.dataset.name === sel.name) {
            span = el.querySelector(".bmk-name");
            break;
          }
        }
      } else {
        for (const el of catList.querySelectorAll(".bmk-cat-h")) {
          if (el.dataset.path === sel.path) {
            span = el.querySelector(".bmk-name");
            break;
          }
        }
      }
      span?.dispatchEvent(new MouseEvent("dblclick", { bubbles: false }));
    }
  }

  addCatBtn.addEventListener("click", async () => {
    if (!store.tree) return;
    const cname = dedupeName("새 카테고리", store.tree.map((c) => c.name));
    pendingRename = { kind: "cat", path: cname }; // 루트 경로 = 이름
    try {
      await store.op("create_category", { name: cname });
    } catch (err) { pendingRename = null; alert(err.message); }
  });

  /* ---------- splitter ---------- */
  split.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    e.stopPropagation();
    split.classList.add("dragging");
    const startX = e.clientX;
    const startW = state.sidebarWidth;
    const scale = app.canvas?.ds?.scale || 1;

    const onMove = (ev) => {
      const dx = (ev.clientX - startX) / scale;
      const max = Math.max(MIN_SIDEBAR, container.clientWidth - MIN_EDITOR - 5);
      state.sidebarWidth = Math.round(clamp(startW + dx, MIN_SIDEBAR, max));
      side.style.width = state.sidebarWidth + "px";
    };
    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      split.classList.remove("dragging");
      markChanged();
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  });

  /* ---------- store subscription (다른 노드/새로고침과 동기화) ---------- */
  // v4: 카테고리 식별자가 경로이므로, 상위 카테고리의 rename/move는 자손 경로
  // 전체를 바꾼다 → 서버가 동봉한 {old,new}로 prefix 치환해 포인터를 이전한다.
  const remapPath = (p, oldPfx, newPfx) => {
    if (p === oldPfx) return newPfx;
    if (typeof p === "string" && p.startsWith(oldPfx + "/")) return newPfx + p.slice(oldPfx.length);
    return p;
  };

  function remapPaths(oldPath, newPath) {
    if (!oldPath || !newPath || oldPath === newPath) return;
    let changed = false;
    const next = {};
    for (const k of Object.keys(state.collapsed)) {
      const nk = remapPath(k, oldPath, newPath);
      if (nk !== k) changed = true;
      if (state.collapsed[k]) next[nk] = true;
    }
    state.collapsed = next;
    const na = remapPath(state.activeCategory, oldPath, newPath);
    if (na !== state.activeCategory) { state.activeCategory = na; changed = true; }
    if (changed) markChanged();
  }

  function dropPaths(oldPath) {
    // delete_category: 자기 + 자손의 접힘 키 정리 (activeCategory는 ensureActiveValid가 복구)
    let changed = false;
    for (const k of Object.keys(state.collapsed)) {
      if (k === oldPath || k.startsWith(oldPath + "/")) { delete state.collapsed[k]; changed = true; }
    }
    if (changed) markChanged();
  }

  function remapPointers(op, remap) {
    // remap = 서버가 트리에 동봉한 {old,new} (rename_category / move_category 시)
    if (remap && remap.old && remap.new) remapPaths(remap.old, remap.new);
    if (!op) return;
    if (op.op === "delete_category") {
      dropPaths(op.name);
    } else if (op.op === "rename_tab" && state.activeCategory === op.category && state.activeTab === op.old) {
      state.activeTab = op.new;
      markChanged();
    } else if (op.op === "move_tab" && state.activeCategory === op.from_category && state.activeTab === op.tab) {
      state.activeCategory = op.to_category;
      markChanged();
    }
  }

  const unsubscribe = store.subscribe((evt) => {
    if (evt.type === "tree") {
      // 신규 노드의 "전체 접힘" 초기화가 트리 로드 전에 예약된 경우 여기서 1회 수행
      if (collapseAllPending && collapseAllCategories()) {
        collapseAllPending = false;
        markChanged();
      }
      remapPointers(evt.op, evt.remap);
      ensureActiveValid();
      renderPresetOptions(); // 프리셋 목록 변경(다른 노드의 저장/삭제 포함) 반영
      render();
      syncEditor(true);
      maybePromptImport();
    } else if (evt.type === "doc") {
      if (evt.source !== node &&
          evt.cat === state.activeCategory && evt.tab === state.activeTab) {
        syncEditor(true);
      }
    }
  });

  /* ---------- public API ---------- */
  node.bmkNotes = {
    reload() { applySectionLayout(); scheduleReclaims(); ensureActiveValid(); render(); syncEditor(); },
    refresh: doRefresh,
    exportAll,
    importLegacy,
    hasLegacy: () => !!pendingLegacy,
    dispose() {
      flushSave();
      unsubscribe();
      resizeObservers.forEach((ro) => { try { ro.disconnect(); } catch (e) { /* noop */ } });
      bodyObservers.forEach((mo) => { try { mo.disconnect(); } catch (e) { /* noop */ } });
    },
  };

  applyValue(initialValue);
  scheduleReclaims();
  if (!store.tree) {
    store.refresh().catch((e) => {
      console.error("[BMK Notes] 초기 동기화 실패:", e);
      crumbText.textContent = "동기화 실패 — ↻로 재시도";
    });
  } else {
    ensureActiveValid();
    render();
    syncEditor();
  }

  requestAnimationFrame(() => {
    node.setSize([Math.max(node.size[0], 460), Math.max(node.size[1], 380)]);
    app.graph?.setDirtyCanvas(true, true);
  });
}

/* ---------------------------------- register ---------------------------------- */

app.registerExtension({
  name: "bmk.tabbedNotes",

  // R키 (Refresh Node Definitions) 에 연동
  async refreshComboInNodes() {
    try { await store.refresh(); } catch (e) { console.warn("[BMK Notes] 새로고침 실패:", e); }
  },

  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData.name !== NODE_TYPE) return;

    // v6: 유선 출력 포트 삭제(백엔드 v7의 RETURN_TYPES=()와 짝).
    // 구버전 워크플로우가 직렬화해 둔 출력 슬롯은 LiteGraph configure가
    // 그대로 복원하므로, 생성/로드 양쪽에서 남은 출력을 걷어낸다.
    const stripOutputs = (node) => {
      try {
        while (node.outputs?.length) node.removeOutput(node.outputs.length - 1);
      } catch (e) {
        console.warn("[BMK Notes] 출력 슬롯 제거 실패 — 빈 배열로 대체:", e);
        node.outputs = [];
      }
    };

    const onNodeCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const r = onNodeCreated?.apply(this, arguments);
      stripOutputs(this);
      setupNode(this);
      return r;
    };

    const onResize = nodeType.prototype.onResize;
    nodeType.prototype.onResize = function (size) {
      size[0] = Math.max(size[0], MIN_NODE_W);
      size[1] = Math.max(size[1], MIN_NODE_H);
      return onResize?.apply(this, arguments);
    };

    const onConfigure = nodeType.prototype.onConfigure;
    nodeType.prototype.onConfigure = function () {
      const r = onConfigure?.apply(this, arguments);
      stripOutputs(this); // 구버전 워크플로우의 직렬화된 출력 슬롯 제거
      this.bmkNotes?.reload();
      return r;
    };

    const onRemoved = nodeType.prototype.onRemoved;
    nodeType.prototype.onRemoved = function () {
      this.bmkNotes?.dispose();
      return onRemoved?.apply(this, arguments);
    };

    const getExtraMenuOptions = nodeType.prototype.getExtraMenuOptions;
    nodeType.prototype.getExtraMenuOptions = function (canvas, options) {
      const r = getExtraMenuOptions?.apply(this, arguments);
      options.push({
        content: "\u21BB 공유 폴더와 동기화",
        callback: () => this.bmkNotes?.refresh?.(),
      });
      options.push({
        content: "\uD83D\uDCE4 노트 전체 내보내기 (폴더/ZIP)",
        callback: () => this.bmkNotes?.exportAll?.(),
      });
      if (this.bmkNotes?.hasLegacy?.()) {
        options.push({
          content: "\uD83D\uDCE5 임베디드 노트를 공유 폴더로 가져오기",
          callback: () => this.bmkNotes?.importLegacy?.(),
        });
      }
      return r;
    };
  },
});
