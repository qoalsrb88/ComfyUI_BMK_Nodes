"""BMK Tag Organizer Core — 분류 팩으로 프롬프트를 정렬·구조화·필터한다.

ComfyUI 노드, BMK AI Studio, CLI가 함께 쓰는 단일 파일 코어다. 표준 라이브러리만
쓰고 ComfyUI를 import하지 않으므로 다른 저장소에 이 파일을 그대로 복사(vendor)해
쓸 수 있다. 데이터는 빌드 도구가 만든 태그 팩(tagpack.json.gz)에서 읽는다.

처리 순서:
    parse    입력 문법(comfyui / nai / anima)에 맞춰 토큰으로 나눈다
    resolve  소문자·밑줄 정규화 후 이름 → 별칭 순으로 찾는다
    dedupe   같은 태그가 다시 나오면 첫 번째만 남긴다
    filter   카테고리 제외(프리셋 + exclude) → 등급(max_rating)
    rules    구도 규칙(크롭·시점·명시 규칙)
    layout   레이아웃 프리셋의 섹션 순서로 배치
    format   출력 문법으로 직렬화

원칙:
- 입력 문자열은 고치지 않고 새 문자열을 만든다.
- DB에 없는 토큰(unknown)과 아직 분류하지 않은 태그(etc)는 조용히 버리지 않는다.
- BREAK 로 나뉜 구간은 각각 따로 정리하고 BREAK 를 그대로 둔다.

버전 이력:
- v1: M0 — 최초 구현.
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import re
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

logger = logging.getLogger(__name__)
_TAG = "[TagOrganizer]"

PACK_FORMAT = "tagorg-pack"
PACK_SCHEMA = 1
COLUMNS = ("key", "type", "count", "primary", "rating", "secondary", "regions", "identity", "status", "display")
_DEFAULTS = (None, 0, 0, "etc", "", (), (), 0, "", None)

RATINGS = ("g", "s", "q", "e")
DIALECTS = ("comfyui", "nai", "anima")
RULE_GROUPS = ("framing", "view", "explicit")
RULE_MODES = ("off", "report", "remove")
ORDERS = ("input", "popular", "alpha")
SYSTEM_CATEGORIES = ("etc", "unknown", "special")

TYPE_CUSTOM = -1  # 팩의 커스텀 태그(danbooru.csv에 없는 NAI·모델 전용 태그)


# ─── 정규화 ──────────────────────────────────────────────────────

_WS = re.compile(r"\s+")
_ALNUM_RUN = re.compile(r"[a-z0-9]{2,}")


def is_kaomoji_name(name: str) -> bool:
    """밑줄을 공백으로 바꾸면 안 되는 이모티콘형 이름인지 판단한다(^_^, >_<, o_o).

    빌드 도구가 general 태그에만 적용한다. 영숫자 2글자 이상이 이어지지 않고
    괄호 한정자도 없는 이름이 대상이다.
    """
    return "_" in name and "(" not in name and not _ALNUM_RUN.search(name)


def normalize_key(text: str, kaomoji: Iterable[str] = ()) -> str:
    """조회용 키: 소문자, 밑줄→공백(이모티콘 제외), 공백 정리."""
    t = text.strip().lower()
    if t in kaomoji:
        return t
    return _WS.sub(" ", t.replace("_", " ")).strip()


# ─── 태그 팩 ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class TagInfo:
    key: str
    type: int
    count: int
    primary: str
    rating: str
    secondary: Tuple[str, ...]
    regions: Tuple[str, ...]
    identity: bool
    status: str
    display: str

    @property
    def main(self) -> str:
        return self.primary.partition("/")[0]


class TagPack:
    """빌드된 태그 팩. load_pack()으로 읽으면 경로별로 캐시된다."""

    def __init__(self, data: Mapping[str, Any], path: str = "") -> None:
        if data.get("format") != PACK_FORMAT:
            raise ValueError("태그 팩 형식이 아닙니다 (format != tagorg-pack).")
        if data.get("schema") != PACK_SCHEMA:
            raise ValueError(f"지원하지 않는 태그 팩 스키마입니다: {data.get('schema')} (지원: {PACK_SCHEMA})")
        if tuple(data.get("columns", ())) != COLUMNS:
            raise ValueError("태그 팩의 columns 정의가 코어와 맞지 않습니다. 팩을 다시 빌드하세요.")
        self.path = path
        self.version: str = data.get("version", "")
        self.source: Dict[str, Any] = dict(data.get("source", {}))
        self.taxonomy: Dict[str, Any] = data["taxonomy"]
        self.rules: Dict[str, Any] = data.get("rules", {})
        self.layouts: Dict[str, Any] = data["layouts"]
        self.presets: Dict[str, Any] = data["presets"]
        self.kaomoji = frozenset(data.get("kaomoji", ()))
        self._rows: Dict[str, Sequence[Any]] = {row[0]: row for row in data["tags"]}
        self._aliases: Dict[str, str] = dict(data.get("aliases", {}))
        self._infos: Dict[str, TagInfo] = {}
        self.categories: Dict[str, Set[str]] = {
            c["id"]: {s["id"] for s in c.get("subs", ())} for c in self.taxonomy["categories"]
        }
        for sys_cat in SYSTEM_CATEGORIES:
            self.categories.setdefault(sys_cat, set())

    def __len__(self) -> int:
        return len(self._rows)

    def get(self, key: str) -> Optional[TagInfo]:
        info = self._infos.get(key)
        if info is not None:
            return info
        row = self._rows.get(key)
        if row is None:
            return None
        vals = list(row) + list(_DEFAULTS[len(row):])
        info = TagInfo(
            key=vals[0],
            type=int(vals[1]),
            count=int(vals[2]),
            primary=vals[3] or "etc",
            rating=vals[4] or "",
            secondary=tuple(vals[5] or ()),
            regions=tuple(vals[6] or ()),
            identity=bool(vals[7]),
            status=vals[8] or "",
            display=vals[9] or vals[0],
        )
        self._infos[key] = info
        return info

    def resolve(self, key: str) -> Tuple[Optional[TagInfo], bool]:
        """(태그 정보, 별칭으로 찾았는지). 이름이 우선이고 없으면 별칭을 본다."""
        info = self.get(key)
        if info is not None:
            return info, False
        target = self._aliases.get(key)
        if target is not None:
            return self.get(target), True
        return None, False

    def is_known(self, key: str) -> bool:
        return key in self._rows or key in self._aliases


_CACHE: Dict[str, Tuple[Tuple[int, int], TagPack]] = {}
_CACHE_LOCK = threading.Lock()


def default_pack_path() -> str:
    """TAGORG_PACK 환경 변수 → 이 파일 옆 data/ → build/ 순으로 찾는다."""
    env = os.environ.get("TAGORG_PACK")
    if env:
        return env
    here = os.path.dirname(os.path.abspath(__file__))
    for rel in ("data/tagpack.json.gz", "build/tagpack.json.gz", "tagpack.json.gz"):
        candidate = os.path.join(here, rel)
        if os.path.isfile(candidate):
            return candidate
    raise FileNotFoundError(
        "태그 팩(tagpack.json.gz)을 찾을 수 없습니다. 빌드 도구로 만들거나 TAGORG_PACK 환경 변수로 경로를 지정하세요."
    )


def read_pack_data(path: str) -> Dict[str, Any]:
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def load_pack(path: Optional[str] = None) -> TagPack:
    """팩을 읽어 캐시한다. 파일이 바뀌면(mtime·크기) 다시 읽는다."""
    path = os.path.abspath(path or default_pack_path())
    st = os.stat(path)
    sig = (st.st_mtime_ns, st.st_size)
    with _CACHE_LOCK:
        hit = _CACHE.get(path)
        if hit is not None and hit[0] == sig:
            return hit[1]
    pack = TagPack(read_pack_data(path), path)
    with _CACHE_LOCK:
        _CACHE[path] = (sig, pack)
    logger.debug("%s loaded pack %s (%d tags) from %s", _TAG, pack.version, len(pack), path)
    return pack


# ─── 선택자 ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class Selector:
    """main, main/sub, 뒤에 @identity, 또는 * (전부)."""

    text: str
    main: str
    sub: str
    identity: bool

    @classmethod
    def parse(cls, text: str) -> "Selector":
        raw = text.strip().lower()
        sel = raw
        identity = False
        if sel.endswith("@identity"):
            identity = True
            sel = sel[: -len("@identity")]
        main, _, sub = sel.partition("/")
        return cls(raw, main.strip(), sub.strip(), identity)

    def matches(self, category: str, identity: bool = False) -> bool:
        if self.main == "*":
            return not self.identity or identity
        main, _, sub = category.partition("/")
        if main != self.main:
            return False
        if self.sub and sub != self.sub:
            return False
        return not self.identity or identity

    def validate(self, categories: Mapping[str, Set[str]]) -> Optional[str]:
        if self.main == "*":
            return None
        if self.main not in categories:
            return f"알 수 없는 카테고리: {self.text}"
        if self.sub and self.sub not in categories[self.main]:
            return f"알 수 없는 서브 카테고리: {self.text}"
        return None


def parse_selectors(value: Any) -> List[Selector]:
    if not value:
        return []
    items = re.split(r"[,\n]", value) if isinstance(value, str) else list(value)
    return [Selector.parse(s) for s in items if str(s).strip()]


# ─── 옵션과 결과 ──────────────────────────────────────────────────

@dataclass
class Options:
    preset: str = "sort_only"
    exclude: Any = ()                   # "a, b" 문자열 또는 선택자 목록
    max_rating: str = "e"               # 이 등급을 넘는 태그를 뺀다
    rules: Any = "remove"               # 모든 그룹에 같은 모드, 또는 {그룹: 모드}
    rule_overrides: Dict[str, str] = field(default_factory=dict)  # 명시 규칙 id → 모드
    layout: str = "default"
    input_dialect: str = "comfyui"
    output_dialect: str = ""            # 비우면 입력과 같음
    line_per_section: bool = True
    trailing_comma: bool = True
    dedupe: bool = True
    resolve_aliases: bool = True        # 별칭을 대표 이름으로 바꿔 출력
    match_secondary: bool = False       # 제외 판정에 보조 카테고리도 쓴다
    order: str = "input"                # 섹션 안 순서: input / popular / alpha

    @classmethod
    def coerce(cls, value: Any) -> "Options":
        if value is None:
            return cls()
        if isinstance(value, Options):
            return value
        if isinstance(value, Mapping):
            known = {k: v for k, v in value.items() if k in cls.__dataclass_fields__}
            unknown = set(value) - set(known)
            if unknown:
                raise ValueError("알 수 없는 옵션: " + ", ".join(sorted(unknown)))
            return cls(**known)
        raise TypeError("options 는 Options 또는 dict 여야 합니다.")

    def rule_modes(self) -> Dict[str, str]:
        if isinstance(self.rules, str):
            return {g: self.rules for g in RULE_GROUPS}
        modes = {g: "remove" for g in RULE_GROUPS}
        modes.update(dict(self.rules or {}))
        return modes

    def validate(self, pack: TagPack) -> List[Selector]:
        """옵션을 검사하고 제외 선택자 목록(프리셋 + exclude)을 돌려준다."""
        if self.preset not in pack.presets:
            raise ValueError(f"알 수 없는 프리셋: {self.preset} (가능: {', '.join(pack.presets)})")
        if self.layout not in pack.layouts:
            raise ValueError(f"알 수 없는 레이아웃: {self.layout} (가능: {', '.join(pack.layouts)})")
        if self.max_rating not in RATINGS:
            raise ValueError(f"max_rating 은 {'/'.join(RATINGS)} 중 하나여야 합니다.")
        for d in (self.input_dialect, self.output_dialect or self.input_dialect):
            if d not in DIALECTS:
                raise ValueError(f"알 수 없는 문법: {d} (가능: {', '.join(DIALECTS)})")
        if self.order not in ORDERS:
            raise ValueError(f"order 는 {'/'.join(ORDERS)} 중 하나여야 합니다.")
        for group, mode in self.rule_modes().items():
            if group not in RULE_GROUPS or mode not in RULE_MODES:
                raise ValueError(f"규칙 설정이 잘못됐습니다: {group}={mode}")
        for rid, mode in self.rule_overrides.items():
            if mode not in RULE_MODES:
                raise ValueError(f"규칙 설정이 잘못됐습니다: {rid}={mode}")
        sels = parse_selectors(pack.presets[self.preset].get("exclude", ())) + parse_selectors(self.exclude)
        errors = [e for e in (s.validate(pack.categories) for s in sels) if e]
        if errors:
            raise ValueError("; ".join(errors))
        return sels


@dataclass
class Token:
    text: str                      # 가중치를 뗀 본문(이스케이프 해제)
    weight: float = 1.0
    verbatim: Optional[str] = None  # 입력에 적힌 그대로(혼자인 세그먼트일 때만)
    kind: str = "tag"              # tag / unknown / special
    opaque: bool = False           # 해석하지 않고 통째로 보존(문장 중간 강조 등)
    prefix: str = ""               # artist / source# / target# / mutual#
    core: str = ""                 # 접두를 뗀 본문
    key: str = ""
    info: Optional[TagInfo] = None
    alias_of: str = ""             # 별칭으로 찾았을 때 입력 키
    primary: str = ""
    removed: str = ""
    flagged: str = ""
    section: str = ""
    chunk: int = 0
    order: int = 0

    @property
    def identity(self) -> bool:
        return bool(self.info and self.info.identity)


@dataclass
class Result:
    text: str
    removed_text: str
    report: Dict[str, Any]
    tokens: List[Token]


# ─── 파싱 ────────────────────────────────────────────────────────

_ESC_OPEN = "\x01"
_ESC_CLOSE = "\x02"
_BREAK = re.compile(r"\bBREAK\b")
_EXTRA_NET = re.compile(r"<\w+:[^<>]+>")          # <lora:name:0.8> 같은 확장 토큰
_WILDCARD = re.compile(r"__[\w./*\-]+__")
_NAI_NUM = re.compile(r"(-?\d+(?:\.\d+)?)::")
_ACTION_PREFIX = re.compile(r"^(source|target|mutual)#", re.IGNORECASE)


def _protect(s: str) -> str:
    return s.replace("\\(", _ESC_OPEN).replace("\\)", _ESC_CLOSE)


def _restore(s: str) -> str:
    return s.replace(_ESC_OPEN, "\\(").replace(_ESC_CLOSE, "\\)")


def _unescape(s: str) -> str:
    return s.replace(_ESC_OPEN, "(").replace(_ESC_CLOSE, ")").replace("\\(", "(").replace("\\)", ")")


def _escape_parens(s: str) -> str:
    return s.replace("(", "\\(").replace(")", "\\)")


def _split_segments(text: str, dialect: str) -> List[str]:
    """괄호 밖의 쉼표·줄바꿈으로 나눈다. 괄호가 안 맞으면 단순 분할로 물러난다."""
    out: List[str] = []
    buf: List[str] = []
    depth = 0
    num_open = 0
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if dialect == "nai":
            if num_open and text.startswith("::", i):
                num_open -= 1
                buf.append("::")
                i += 2
                continue
            m = _NAI_NUM.match(text, i)
            if m and (i == 0 or text[i - 1] in " ,\n{["):
                num_open += 1
                buf.append(m.group(0))
                i = m.end()
                continue
        if ch in "([{":
            depth += 1
        elif ch in ")]}" and depth > 0:
            depth -= 1
        if ch in ",\n" and depth == 0 and num_open == 0:
            out.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
        i += 1
    out.append("".join(buf))
    if depth or num_open:
        out = re.split(r"[,\n]", text)
    return [s.strip() for s in out if s.strip()]


def _comfy_groups(s: str) -> List[str]:
    """ComfyUI parse_parentheses 와 같은 방식으로 최상위 괄호 묶음을 나눈다."""
    result: List[str] = []
    current = ""
    nesting = 0
    for ch in s:
        if ch == "(":
            if nesting == 0:
                if current:
                    result.append(current)
                current = "("
            else:
                current += ch
            nesting += 1
        elif ch == ")":
            nesting -= 1
            if nesting == 0:
                result.append(current + ")")
                current = ""
            else:
                current += ch
        else:
            current += ch
    if current:
        result.append(current)
    return result


def _comfy_weights(s: str, weight: float = 1.0, grouped: bool = False) -> List[Tuple[str, float, bool]]:
    """ComfyUI token_weights 와 같은 규칙: 맨괄호 ×1.1, (x:w)는 w로 바꾼다."""
    out: List[Tuple[str, float, bool]] = []
    for x in _comfy_groups(s):
        if len(x) >= 2 and x[0] == "(" and x[-1] == ")":
            inner = x[1:-1]
            w = weight * 1.1
            colon = inner.rfind(":")
            if colon > 0:
                try:
                    w = float(inner[colon + 1:])
                    inner = inner[:colon]
                except ValueError:
                    pass
            out += _comfy_weights(inner, w, True)
        else:
            out.append((x, weight, grouped))
    return out


def _nai_weights(s: str) -> List[Tuple[str, float, bool]]:
    """NovelAI: {x} ×1.05, [x] ÷1.05, 1.2::x:: 숫자 강조. 괄호 ()는 글자 그대로."""
    out: List[Tuple[str, float, bool]] = []
    stack: List[Tuple[str, float]] = []
    buf: List[str] = []

    def flush() -> None:
        if buf:
            w = 1.0
            for _, m in stack:
                w *= m
            out.append(("".join(buf), w, bool(stack)))
            buf.clear()

    i, n = 0, len(s)
    while i < n:
        if stack and stack[-1][0] == "num" and s.startswith("::", i):
            flush()
            stack.pop()
            i += 2
            continue
        m = _NAI_NUM.match(s, i)
        if m and (i == 0 or s[i - 1] in " ,{["):
            flush()
            stack.append(("num", float(m.group(1))))
            i = m.end()
            continue
        ch = s[i]
        if ch == "{":
            flush()
            stack.append(("{", 1.05))
        elif ch == "[":
            flush()
            stack.append(("[", 1 / 1.05))
        elif ch == "}" and stack and stack[-1][0] == "{":
            flush()
            stack.pop()
        elif ch == "]" and stack and stack[-1][0] == "[":
            flush()
            stack.pop()
        else:
            buf.append(ch)
        i += 1
    flush()
    return out


def _is_special(seg: str, dialect: str) -> bool:
    s = seg.strip()
    low = s.lower()
    if _EXTRA_NET.fullmatch(s) or _WILDCARD.fullmatch(s) or low.startswith("embedding:"):
        return True
    if dialect != "nai" and "{" in s and "|" in s:   # 동적 프롬프트 {a|b}
        return True
    if dialect == "nai" and low.startswith("text:"):  # NAI 글자 렌더링
        return True
    return False


def _split_prefix(text: str, dialect: str, kaomoji: Iterable[str] = ()) -> Tuple[str, str]:
    t = text.strip()
    if t.lower().startswith("artist:"):
        return "artist", t[len("artist:"):].strip()
    if dialect == "anima" and t.startswith("@") and len(t) > 1 and t.lower() not in kaomoji:
        return "artist", t[1:].strip()
    m = _ACTION_PREFIX.match(t)
    if m:
        return m.group(1).lower() + "#", t[m.end():].strip()
    return "", t


def parse_prompt(text: str, dialect: str = "comfyui", pack: Optional[TagPack] = None) -> List[Token]:
    """한 구간(BREAK 없음)을 토큰으로 나눈다. pack 이 있으면 괄호가 이름의 일부인 태그를 알아본다."""
    kaomoji = pack.kaomoji if pack is not None else frozenset()
    text = _EXTRA_NET.sub(lambda m: "," + m.group(0) + ",", text)
    text = _protect(text)
    tokens: List[Token] = []
    for seg in _split_segments(text, dialect):
        raw = _restore(seg)
        if _is_special(raw, dialect):
            tokens.append(Token(text=raw, verbatim=raw, kind="special"))
            continue
        if dialect != "nai" and pack is not None:
            literal = _unescape(seg)
            _, core = _split_prefix(literal, dialect, kaomoji)
            if pack.is_known(normalize_key(core, kaomoji)):
                tokens.append(Token(text=literal, verbatim=raw))
                continue
        pieces = _nai_weights(seg) if dialect == "nai" else _comfy_weights(seg)
        grouped = any(g for _, _, g in pieces)
        plain = any(p.strip() for p, _, g in pieces if not g)
        if grouped and plain:
            # 문장 중간 강조(예: "a cat (sleeping:1.2)")는 해석하지 않고 그대로 둔다
            tokens.append(Token(text=_unescape(seg), verbatim=raw, kind="unknown", opaque=True))
            continue
        sub: List[Token] = []
        for ptext, weight, _ in pieces:
            for part in ptext.split(","):
                part = _unescape(part).strip()
                if part:
                    sub.append(Token(text=part, weight=weight))
        if len(sub) == 1 and not grouped:
            sub[0].verbatim = raw
        tokens += sub
    return tokens


# ─── 분류·필터·규칙 ───────────────────────────────────────────────

def _classify(tokens: List[Token], pack: TagPack, opts: Options) -> None:
    for t in tokens:
        if t.kind == "special":
            t.primary = "special"
            continue
        if t.opaque:
            t.primary = "unknown"
            continue
        t.prefix, t.core = _split_prefix(t.text, opts.input_dialect, pack.kaomoji)
        key = normalize_key(t.core, pack.kaomoji)
        info, via_alias = pack.resolve(key)
        t.info = info
        t.key = info.key if info is not None else key
        if via_alias:
            t.alias_of = key
        if info is not None:
            t.kind = "tag"
            t.primary = info.primary
        if t.prefix == "artist" and (info is None or info.main == "etc"):
            t.kind = "tag"
            t.primary = "style/artist"
        elif info is None:
            t.kind = "unknown"
            t.primary = "unknown"


def _dedupe(tokens: List[Token]) -> None:
    seen: Set[Tuple[str, str]] = set()
    for t in tokens:
        if t.kind == "special":
            continue
        ident = (t.prefix, t.key) if t.kind == "tag" else ("?", t.text.strip().lower())
        if ident in seen:
            t.removed = "duplicate"
        else:
            seen.add(ident)


def _apply_filters(tokens: List[Token], sels: List[Selector], opts: Options) -> None:
    limit = RATINGS.index(opts.max_rating)
    for t in tokens:
        if t.removed:
            continue
        cats = [t.primary]
        if opts.match_secondary and t.info is not None:
            cats += list(t.info.secondary)
        for sel in sels:
            if any(sel.matches(c, t.identity) for c in cats):
                t.removed = f"exclude:{sel.text}"
                break
        if t.removed:
            continue
        rating = t.info.rating if t.info is not None else ""
        if rating in RATINGS and RATINGS.index(rating) > limit:
            t.removed = f"rating:{rating}>{opts.max_rating}"


def _act(t: Token, mode: str, reason: str) -> None:
    if mode == "remove":
        t.removed = reason
    elif mode == "report" and not t.flagged:
        t.flagged = reason


def _is_multi_subject(present: Set[str], rules: Mapping[str, Any]) -> bool:
    cfg = rules.get("multi_subject") or {}
    if present & set(cfg.get("any", ())):
        return True
    return len(present & set(cfg.get("count_single", ()))) >= 2


def _apply_rules(tokens: List[Token], pack: TagPack, opts: Options) -> None:
    rules = pack.rules
    modes = opts.rule_modes()
    alive = [t for t in tokens if not t.removed]
    present = {t.key for t in alive if t.kind == "tag"}
    multi = _is_multi_subject(present, rules)

    # 부위 기반 규칙: 가려진 부위 → (그룹, 모드, 기준 태그)
    hidden: Dict[str, Tuple[str, str, str]] = {}
    for group in ("framing", "view"):
        mode = modes[group]
        cfg = rules.get(group) or {}
        if mode == "off" or present & set(cfg.get("exempt", ())):
            continue
        if group == "view" and multi and cfg.get("multi_subject_exempt", True):
            continue
        active = [g for g in cfg.get("gates", ()) if present & set(g["tags"])]
        if not active:
            continue
        # 기준 태그가 여럿이면(예: upper body + full body) 모두가 가리는 부위만 가린다
        regions = set.intersection(*(set(g.get("hide", ())) for g in active))
        for g in active:
            for tag, revealed in (g.get("reveal") or {}).items():
                if tag in present:
                    regions -= set(revealed)
        gates = ", ".join(sorted(present & {tag for g in active for tag in g["tags"]}))
        for region in regions:
            hidden.setdefault(region, (group, mode, gates))

    if hidden:
        for t in alive:
            if t.info is None or not t.info.regions:
                continue
            regs = sorted(set(t.info.regions))
            if not all(r in hidden for r in regs):
                continue
            entries = sorted({hidden[r] for r in regs})
            mode = "remove" if all(e[1] == "remove" for e in entries) else "report"
            _act(t, mode, "rule:" + "; ".join(f"{g}({gates})" for g, _, gates in entries))

    # 명시 규칙
    for rule in rules.get("explicit", ()):
        rid = rule.get("id", "")
        mode = opts.rule_overrides.get(rid) or (modes["explicit"] if rule.get("default", "on") != "off" else "off")
        if mode == "off":
            continue
        if not present & set(rule.get("when_any", ())) or present & set(rule.get("unless_any", ())):
            continue
        targets = set(rule.get("remove", ()))
        sels = parse_selectors(rule.get("remove_selectors", ()))
        for t in alive:
            if t.removed or t.kind != "tag":
                continue
            if t.key in targets or any(s.matches(t.primary, t.identity) for s in sels):
                _act(t, mode, f"rule:{rid}")


# ─── 배치·출력 ────────────────────────────────────────────────────

def _fmt_weight(w: float) -> str:
    return f"{w:.3f}".rstrip("0").rstrip(".")


def _nai_emphasis(body: str, w: float) -> str:
    """1.05 거듭제곱이면 {…}/[…] 로, 아니면 숫자 강조(1.2::…::)로 쓴다."""
    for k in range(1, 6):
        if abs(w - 1.05 ** k) < 1e-6:
            return "{" * k + body + "}" * k
        if abs(w - 1.05 ** -k) < 1e-6:
            return "[" * k + body + "]" * k
    return f"{_fmt_weight(w)}::{body}::"


def _display(t: Token, opts: Options) -> str:
    if t.kind == "special" or t.opaque:
        return t.verbatim or t.text
    if t.info is not None:
        if t.alias_of and not opts.resolve_aliases:
            return t.alias_of
        return t.info.display
    if t.kind == "tag":           # DB에 없는 작가(artist: 접두)
        return normalize_key(t.core)
    return t.core or t.text


def _format_token(t: Token, opts: Options, out_dialect: str) -> str:
    if t.kind == "special" or t.opaque:
        return t.verbatim or t.text
    if t.kind == "unknown" and t.verbatim is not None and out_dialect == opts.input_dialect:
        return t.verbatim
    body = _display(t, opts)
    if t.prefix == "artist":
        body = ("@" if out_dialect == "anima" else "artist:") + body
    elif t.prefix:
        body = t.prefix + body
    if out_dialect != "nai":
        body = _escape_parens(body)
    if abs(t.weight - 1.0) > 1e-9:
        if out_dialect == "nai":
            return _nai_emphasis(body, t.weight)
        return f"({body}:{_fmt_weight(t.weight)})"
    return body


def _layout_sections(pack: TagPack, name: str) -> List[Tuple[str, List[Selector], List[Selector]]]:
    return [
        (sec["id"], parse_selectors(sec.get("include", ())), parse_selectors(sec.get("except", ())))
        for sec in pack.layouts[name]["sections"]
    ]


def _render(tokens: List[Token], pack: TagPack, opts: Options, out_dialect: str) -> List[str]:
    sections = _layout_sections(pack, opts.layout)
    buckets: List[List[Token]] = [[] for _ in sections]
    rest: List[Token] = []
    for t in tokens:
        if t.removed:
            continue
        for idx, (sid, inc, exc) in enumerate(sections):
            if any(s.matches(t.primary, t.identity) for s in inc) and not any(
                s.matches(t.primary, t.identity) for s in exc
            ):
                t.section = sid
                buckets[idx].append(t)
                break
        else:
            t.section = "rest"
            rest.append(t)
    lines = []
    for bucket in buckets + [rest]:
        if not bucket:
            continue
        if opts.order == "popular":
            bucket = sorted(bucket, key=lambda x: -(x.info.count if x.info else -1))
        elif opts.order == "alpha":
            bucket = sorted(bucket, key=lambda x: _display(x, opts))
        lines.append(", ".join(_format_token(t, opts, out_dialect) for t in bucket))
    return lines


def organize(text: str, pack: Optional[TagPack] = None, options: Any = None) -> Result:
    """프롬프트를 정리한다. options 는 Options 또는 같은 키의 dict."""
    pack = pack if pack is not None else load_pack()
    opts = Options.coerce(options)
    sels = opts.validate(pack)
    out_dialect = opts.output_dialect or opts.input_dialect

    all_tokens: List[Token] = []
    chunk_texts: List[str] = []
    order = 0
    for ci, chunk in enumerate(_BREAK.split(text.replace("\r\n", "\n"))):
        tokens = parse_prompt(chunk, opts.input_dialect, pack)
        for t in tokens:
            t.chunk, t.order = ci, order
            order += 1
        _classify(tokens, pack, opts)
        if opts.dedupe:
            _dedupe(tokens)
        _apply_filters(tokens, sels, opts)
        _apply_rules(tokens, pack, opts)
        lines = _render(tokens, pack, opts, out_dialect)
        if lines:
            sep = (",\n" if opts.trailing_comma else "\n") if opts.line_per_section else ", "
            chunk_texts.append(sep.join(lines))
        all_tokens += tokens

    joiner = "\nBREAK\n" if opts.line_per_section else " BREAK "
    removed = [t for t in all_tokens if t.removed]
    removed_text = "\n".join(f"{_display(t, opts)}  # {t.removed}" for t in removed)
    report = _report(all_tokens, pack, opts, out_dialect)
    return Result(joiner.join(chunk_texts), removed_text, report, all_tokens)


def _report(tokens: List[Token], pack: TagPack, opts: Options, out_dialect: str) -> Dict[str, Any]:
    rows = []
    for t in tokens:
        row: Dict[str, Any] = {
            "text": _display(t, opts),
            "kind": t.kind,
            "primary": t.primary,
            "section": t.section,
        }
        if t.info is not None:
            row.update(key=t.key, rating=t.info.rating, status=t.info.status, count=t.info.count)
        if abs(t.weight - 1.0) > 1e-9:
            row["weight"] = round(t.weight, 4)
        for name in ("prefix", "alias_of", "removed", "flagged"):
            if getattr(t, name):
                row[name] = getattr(t, name)
        rows.append(row)
    unknown = [r["text"] for r in rows if r["kind"] == "unknown"]
    unclassified = [r["text"] for r in rows if r["primary"] == "etc"]
    return {
        "pack_version": pack.version,
        "preset": opts.preset,
        "layout": opts.layout,
        "dialect": {"input": opts.input_dialect, "output": out_dialect},
        "counts": {
            "total": len(rows),
            "kept": sum(1 for t in tokens if not t.removed),
            "removed": sum(1 for t in tokens if t.removed),
            "flagged": sum(1 for t in tokens if t.flagged),
            "unknown": len(unknown),
            "unclassified": len(unclassified),
        },
        "unknown": unknown,
        "unclassified": unclassified,
        "removed": [{"tag": r["text"], "reason": r["removed"]} for r in rows if "removed" in r],
        "flagged": [{"tag": r["text"], "reason": r["flagged"]} for r in rows if "flagged" in r],
        "tokens": rows,
    }
