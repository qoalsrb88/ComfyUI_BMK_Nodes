"""BMK Tag Organizer Core — 분류 팩으로 프롬프트를 정렬·구조화·필터한다.

ComfyUI 노드, BMK AI Studio, CLI가 함께 쓰는 단일 파일 코어다. 표준 라이브러리만
쓰고 ComfyUI를 import하지 않으므로 다른 저장소에 이 파일을 그대로 복사(vendor)해
쓸 수 있다. 데이터는 빌드 도구가 만든 태그 팩(tagpack.json.gz)에서 읽는다.

처리 순서:
    parse    입력 문법(comfyui / nai / anima)에 맞춰 토큰으로 나눈다
    resolve  소문자·밑줄 정규화 후 이름 → 별칭 순으로 찾는다
    guess    (guess 옵션) 팩에 없는 토큰의 분류를 패턴·끝말로 추정한다
    override (framing·view 옵션) 프롬프트의 크롭·시점 태그를 고른 태그로 바꾼다
    dedupe   같은 태그가 다시 나오면 첫 번째만 남긴다
    filter   카테고리 제외(프리셋 + exclude) → 등급(max_rating)
    rules    구도 규칙(크롭·시점·명시 규칙)
    place    배열 세팅(arrangement)의 청크, 또는 레이아웃 프리셋의 섹션에 배치(숨긴 청크는 뺀다)
    format   출력 문법으로 직렬화

원칙:
- 입력 문자열은 고치지 않고 새 문자열을 만든다.
- DB에 없는 토큰(unknown)과 아직 분류하지 않은 태그(etc)는 조용히 버리지 않는다.
- BREAK 로 나뉜 구간은 각각 따로 정리하고 BREAK 를 그대로 둔다.

배열 세팅(arrangement)은 다른 툴(ComfyUI JS 편집기, Studio, 사내 툴)과 같이 쓰는 JSON 이다.
형식은 docs/arrangement.md, 편집기가 쓰는 카테고리·색·퀵 목록은 palette(), 레이아웃에서 시작할 때는
arrangement_from_layout(). 결과의 report["arranged"] 는 줄 → 청크 → 토큰(뺀 것 포함) 구조라
미리보기 화면이 뺀 태그를 제자리에 흐리게 그릴 수 있다.

버전 이력:
- v1: M0 — 최초 구현.
- v1.1: 명시 규칙에 multi_subject_exempt(여러 명이면 적용 안 함) 추가.
- v1.2: 배열 세팅(arrangement)·크롭/시점 덮어쓰기(framing·view)·팩에 없는 토큰 추정(guess),
        report 에 arranged(줄·청크 구조)와 토큰별 out(출력 문법 글자)·guess 추가.
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
TYPE_GUESS = -2   # 추정한 토큰에 붙이는 임시 정보(팩에는 없음)

ARRANGEMENT_FORMAT = "tagorg-arrangement"
ARRANGEMENT_VERSION = 1
PALETTE_FORMAT = "tagorg-palette"
OVERRIDE_TARGETS = {"framing": "composition/framing", "view": "composition/angle"}
REST_CHUNK = "rest"
DEFAULT_GUESS_HEADS = ("composition", "action", "clothes", "body", "object", "effect", "background")


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
        self.guess: Dict[str, Any] = dict(data.get("guess") or {})
        self.kaomoji = frozenset(data.get("kaomoji", ()))
        self._guess_rules: Optional[List["GuessRule"]] = None
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
    arrangement: Any = None             # 배열 세팅(dict 또는 JSON 글). 주면 layout 대신 쓴다
    framing: str = ""                   # 크롭 덮어쓰기: composition/framing 태그(쉼표로 여럿). 비우면 프롬프트 그대로
    view: str = ""                      # 시점 덮어쓰기: composition/angle 태그
    guess: bool = False                 # 팩에 없는 토큰의 분류를 패턴·끝말로 추정한다(임시 편입)

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
    section: str = ""              # 배치된 청크(배열) 또는 섹션(레이아웃) id. 뺀 토큰도 갈 자리를 적는다
    chunk: int = 0                 # BREAK 구간 번호
    order: int = 0
    guess: str = ""                # 추정 근거: pattern:<패턴> / head:<끝말 태그>
    injected: str = ""             # 덮어쓰기로 넣은 태그: framing / view
    slot: int = -1                 # 배치된 청크의 순번(줄 순서대로 펼친 번호)
    member: int = 0                # 청크 안 멤버 순번(서브 순서)
    index: int = 0                 # report["tokens"] 의 위치

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


# ─── 추정(guess) ─────────────────────────────────────────────────

@dataclass(frozen=True)
class GuessRule:
    """팩에 없는 토큰을 이 분류로 임시 편입하는 패턴. * 는 한 글자 이상(단어 여럿), re: 는 정규식."""

    text: str
    regex: Any
    primary: str
    regions: Tuple[str, ...] = ()
    rating: str = ""
    identity: bool = False


def compile_pattern(pattern: str) -> Any:
    """와일드카드(holding *, * eyes) 또는 re:정규식을 정규화한 이름(소문자·공백)에 전체 일치하는 식으로."""
    p = str(pattern).strip()
    if p.lower().startswith("re:"):
        return re.compile(p[3:].strip(), re.IGNORECASE)
    key = normalize_key(p)
    if not key.replace("*", "").strip():
        raise ValueError(f"패턴에 글자가 없습니다: {pattern}")
    return re.compile(".+".join(re.escape(part) for part in key.split("*")))


def _category_error(primary: str, categories: Mapping[str, Set[str]]) -> Optional[str]:
    main, _, sub = primary.partition("/")
    if main not in categories or main in SYSTEM_CATEGORIES:
        return f"알 수 없는 카테고리: {primary}"
    if sub and sub not in categories[main]:
        return f"알 수 없는 서브 카테고리: {primary}"
    return None


def compile_guess_rules(items: Iterable[Any], pack: "TagPack", where: str = "guess") -> List[GuessRule]:
    """{"pattern", "primary", "regions"?, "rating"?, "identity"?} 목록. 잘못된 것이 있으면 ValueError."""
    if not isinstance(items, (list, tuple)):
        raise ValueError(f"{where} 는 규칙 목록이어야 합니다")
    rules: List[GuessRule] = []
    errors: List[str] = []
    vocab = set((pack.rules or {}).get("regions", ()))
    for i, item in enumerate(items):
        if not isinstance(item, Mapping):
            errors.append(f"{where}[{i}] 는 {{pattern, primary}} 여야 합니다")
            continue
        text = str(item.get("pattern", "")).strip()
        primary = str(item.get("primary", "")).strip().lower()
        err = _category_error(primary, pack.categories) if primary else "primary 가 없습니다"
        if err:
            errors.append(f"{where}[{i}] {err}")
            continue
        try:
            regex = compile_pattern(text)
        except (re.error, ValueError) as e:
            errors.append(f"{where}[{i}] 패턴이 잘못됐습니다: {text} ({e})")
            continue
        regions = tuple(_str_list(item.get("regions")))
        bad = [r for r in regions if vocab and r not in vocab]
        if bad:
            errors.append(f"{where}[{i}] 없는 부위입니다: {', '.join(bad)}")
        rating = str(item.get("rating", "") or "")
        if rating and rating not in RATINGS:
            errors.append(f"{where}[{i}] rating 은 g/s/q/e 중 하나여야 합니다: {rating}")
        rules.append(GuessRule(text, regex, primary, regions, rating, bool(item.get("identity"))))
    if errors:
        raise ValueError("; ".join(errors))
    return rules


def _pack_guess_rules(pack: "TagPack") -> List[GuessRule]:
    if pack._guess_rules is None:
        pack._guess_rules = compile_guess_rules(pack.guess.get("patterns", ()), pack, "pack.guess")
    return pack._guess_rules


def _guess(tokens: List[Token], pack: TagPack, rules: Sequence[GuessRule], heads: Set[str]) -> None:
    """팩에 없는 토큰을 패턴(위에서부터) → 끝말(가장 긴 뒷부분이 팩의 general·커스텀 태그) 순으로 추정한다.

    추정한 토큰은 kind=unknown 그대로(출력은 입력 글자 그대로)지만 분류·부위·등급을 얻어 제외·구도 규칙·배치에
    쓰인다. 구도 규칙의 기준(gate) 태그로는 쓰지 않는다.
    """
    for t in tokens:
        if t.kind != "unknown" or t.opaque or not t.key:
            continue
        hit: Optional[Tuple[str, Tuple[str, ...], str, bool, str]] = None
        for rule in rules:
            if rule.regex.fullmatch(t.key):
                hit = (rule.primary, rule.regions, rule.rating, rule.identity, "pattern:" + rule.text)
                break
        if hit is None:
            words = t.key.split(" ")
            for i in range(1, len(words)):
                info, _ = pack.resolve(" ".join(words[i:]))
                if info is not None and info.type in (0, TYPE_CUSTOM) and info.main in heads:
                    hit = (info.primary, info.regions, info.rating, info.identity, "head:" + info.key)
                    break
        if hit is None:
            continue
        primary, regions, rating, identity, why = hit
        t.primary, t.guess = primary, why
        t.info = TagInfo(key=t.key, type=TYPE_GUESS, count=0, primary=primary, rating=rating, secondary=(),
                         regions=tuple(regions), identity=identity, status="guess", display=t.core or t.text)


# ─── 크롭·시점 덮어쓰기 ───────────────────────────────────────────

def _overrides(pack: TagPack, opts: Options) -> List[Tuple[str, str, List[TagInfo]]]:
    """(그룹, 대상 서브 카테고리, 넣을 태그들). 대상 서브 카테고리의 태그만 받는다."""
    out = []
    for group, target in OVERRIDE_TARGETS.items():
        infos: List[TagInfo] = []
        for name in re.split(r"[,\n]", getattr(opts, group) or ""):
            if not name.strip():
                continue
            info, _ = pack.resolve(normalize_key(name, pack.kaomoji))
            if info is None or info.primary != target:
                raise ValueError(f"{group} 덮어쓰기는 {target} 태그여야 합니다: {name.strip()}")
            if info.key not in {i.key for i in infos}:
                infos.append(info)
        if infos:
            out.append((group, target, infos))
    return out


def _apply_overrides(tokens: List[Token], overrides, inject: bool) -> List[Token]:
    """대상 서브 카테고리 토큰을 빼고(override:<그룹>) 고른 태그를 그 자리에 넣는다. 고른 태그가 이미 있으면 둔다.

    BREAK 첫 구간(inject)에는 늘 넣고, 다른 구간에는 그 구간에 대상 태그가 있었을 때만 바꾼다.
    """
    for group, target, infos in overrides:
        keys = {i.key for i in infos}
        pos: Optional[int] = None
        for idx, t in enumerate(tokens):
            if t.removed or t.kind == "special" or t.primary != target:
                continue
            pos = idx
            if not (t.kind == "tag" and t.key in keys):
                t.removed = f"override:{group}"
        if pos is None and not inject:
            continue
        have = {t.key for t in tokens if t.kind == "tag" and not t.removed}
        new = [Token(text=i.display, kind="tag", core=i.display, key=i.key, info=i, primary=i.primary, injected=group)
               for i in infos if i.key not in have]
        at = len(tokens) if pos is None else pos + 1
        tokens = tokens[:at] + new + tokens[at:]
    return tokens


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
        if multi and rule.get("multi_subject_exempt"):   # 여러 명이면 누구에게 해당하는지 알 수 없다
            continue
        targets = set(rule.get("remove", ()))
        sels = parse_selectors(rule.get("remove_selectors", ()))
        for t in alive:
            if t.removed or (t.kind != "tag" and not t.guess):
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


# ─── 배열 세팅·배치 ───────────────────────────────────────────────

_COLOR = re.compile(r"#[0-9a-fA-F]{6}")
_CHUNK_ID = re.compile(r"[\w.\-]+")


@dataclass
class Chunk:
    """출력 한 줄 안의 묶음. 배열 세팅의 청크, 또는 레이아웃의 섹션(except 가 있는 옛 방식)."""

    id: str
    label: str
    color: str
    members: List[Selector] = field(default_factory=list)   # 서브 순서대로
    tags: Set[str] = field(default_factory=set)             # 커스텀: 정규화한 태그 이름
    patterns: List[Any] = field(default_factory=list)        # 커스텀: 와일드카드·정규식
    hidden: bool = False                                     # 이 청크의 태그를 뺀다(hidden:<id>)
    excepts: List[Selector] = field(default_factory=list)    # 레이아웃 섹션의 except


@dataclass
class Plan:
    """배치 계획: 줄 목록(줄마다 청크 목록)과 맞추는 방식. 어디에도 맞지 않는 토큰은 맨 끝 rest 로."""

    rows: List[List[Chunk]]
    rest: Chunk
    label: str = ""
    first_match: bool = False   # 레이아웃: 처음 맞는 섹션 / 배열: 가장 구체적인 멤버
    sub_order: bool = False     # 청크 안을 멤버(서브) 순서로 정렬
    guess: List[GuessRule] = field(default_factory=list)


def _str_list(value: Any) -> List[str]:
    """"a, b" 글·목록 → 글자 항목. 다른 값 하나는 그 값의 글자(다른 툴이 숫자 등을 넣어도 검사 오류로 끝나게)."""
    if isinstance(value, str):
        items: List[Any] = re.split(r"[,\n]", value)
    elif isinstance(value, (list, tuple, set)):
        items = list(value)
    else:
        items = [] if value is None else [value]
    return [str(v).strip() for v in items if str(v).strip()]


def _category_meta(taxonomy: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    meta: Dict[str, Dict[str, Any]] = {}
    for c in taxonomy.get("categories", ()):
        meta[c["id"]] = {"label": c.get("label", c["id"]), "color": c.get("color", ""),
                         "subs": {s["id"]: s.get("label", s["id"]) for s in c.get("subs", ())}}
    for s in taxonomy.get("system", ()):
        meta.setdefault(s["id"], {"label": s.get("label", s["id"]), "color": s.get("color", ""), "subs": {}})
    return meta


def selector_label(sel: str, taxonomy: Mapping[str, Any]) -> str:
    """선택자의 표시 이름: main/sub → 서브 이름, main → 메인 이름, * → 전부."""
    s = Selector.parse(sel)
    meta = _category_meta(taxonomy).get(s.main)
    if s.main == "*":
        label = "전부"
    elif meta is None:
        label = s.text
    elif s.sub:
        label = meta["subs"].get(s.sub, s.sub)
    else:
        label = meta["label"]
    return label + (" (고유 외형)" if s.identity else "")


def _default_label(members: Sequence[Selector], taxonomy: Mapping[str, Any]) -> str:
    if not members:
        return ""
    if len(members) == 1:
        return selector_label(members[0].text, taxonomy)
    meta = _category_meta(taxonomy)
    mains = list(dict.fromkeys(m.main for m in members))
    return "·".join("전부" if m == "*" else meta.get(m, {}).get("label", m) for m in mains[:2])


def _default_color(members: Sequence[Selector], taxonomy: Mapping[str, Any]) -> str:
    meta = _category_meta(taxonomy)
    for m in members:
        if meta.get(m.main, {}).get("color"):
            return meta[m.main]["color"]
    return meta.get("etc", {}).get("color", "")


def chunk_label(spec: Mapping[str, Any], taxonomy: Mapping[str, Any]) -> str:
    """청크의 표시 이름: label, 없으면 멤버에서(하나면 그 서브·메인 이름, 여럿이면 메인 이름 둘까지 ·로)."""
    return str(spec.get("label", "") or "") or _default_label(parse_selectors(spec.get("members", ())), taxonomy)


def chunk_color(spec: Mapping[str, Any], taxonomy: Mapping[str, Any]) -> str:
    """청크의 색: color(#rrggbb), 없으면 첫 멤버 메인 카테고리의 색, 그것도 없으면 미분류(etc) 색."""
    color = str(spec.get("color", "") or "")
    return color if _COLOR.fullmatch(color) else _default_color(parse_selectors(spec.get("members", ())), taxonomy)


def _rest_chunk(taxonomy: Mapping[str, Any], cid: str = REST_CHUNK) -> Chunk:
    return Chunk(cid, "나머지", _category_meta(taxonomy).get("etc", {}).get("color", ""))


def _has_arrangement(value: Any) -> bool:
    return bool(value.strip()) if isinstance(value, str) else bool(value)


def load_arrangement(data: Any, pack: TagPack) -> Plan:
    """배열 세팅 JSON(dict 또는 글)을 검사해 배치 계획으로 바꾼다. 잘못됐으면 ValueError(문제를 한꺼번에)."""
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError as e:
            raise ValueError(f"배열 세팅 JSON 을 읽지 못했습니다: {e}") from None
    if not isinstance(data, Mapping):
        raise ValueError("배열 세팅은 JSON 객체여야 합니다.")
    if data.get("format", ARRANGEMENT_FORMAT) != ARRANGEMENT_FORMAT:
        raise ValueError(f"배열 세팅 형식이 아닙니다 (format != {ARRANGEMENT_FORMAT}).")
    if data.get("version", ARRANGEMENT_VERSION) != ARRANGEMENT_VERSION:
        raise ValueError(f"지원하지 않는 배열 세팅 버전입니다: {data.get('version')} (지원: {ARRANGEMENT_VERSION})")
    specs = data.get("chunks") or {}
    if not isinstance(specs, Mapping):
        raise ValueError("배열 세팅의 chunks 는 {id: 청크} 객체여야 합니다.")
    rows_data = data.get("rows") or []
    if not isinstance(rows_data, (list, tuple)):
        raise ValueError("배열 세팅의 rows 는 줄 목록이어야 합니다.")
    errors: List[str] = []
    rows: List[List[Chunk]] = []
    placed: Set[str] = set()
    for ri, row in enumerate(rows_data):
        if not isinstance(row, (str, list, tuple)):
            errors.append(f"rows[{ri}] 는 청크 id 목록이어야 합니다")
            continue
        out: List[Chunk] = []
        for cid in ([row] if isinstance(row, str) else list(row)):
            cid = str(cid)
            if cid not in specs:
                errors.append(f"rows[{ri}] 에 정의하지 않은 청크가 있습니다: {cid}")
            elif cid in placed:
                errors.append(f"청크를 두 번 배치했습니다: {cid}")
            else:
                placed.add(cid)
                out.append(_load_chunk(cid, specs[cid], pack, errors))
        rows.append(out)
    guess: List[GuessRule] = []
    try:
        guess = compile_guess_rules(data.get("guess") or [], pack, "guess")
    except ValueError as e:
        errors.append(str(e))
    if errors:
        raise ValueError("배열 세팅이 잘못됐습니다: " + "; ".join(errors))
    rest = _rest_chunk(pack.taxonomy, REST_CHUNK if REST_CHUNK not in placed else "_" + REST_CHUNK)
    return Plan(rows, rest, str(data.get("label", "") or ""), first_match=False,
                sub_order=bool(data.get("sub_order", True)), guess=guess)


def _load_chunk(cid: str, spec: Any, pack: TagPack, errors: List[str]) -> Chunk:
    where = f"chunks[{cid}]"
    if not _CHUNK_ID.fullmatch(cid):
        errors.append(f"{where} id 는 글자·숫자·_ . - 만 씁니다")
    if not isinstance(spec, Mapping):
        errors.append(f"{where} 는 객체여야 합니다")
        return Chunk(cid, cid, "")
    members = parse_selectors(_str_list(spec.get("members")))
    for sel in members:
        err = sel.validate(pack.categories)
        if err:
            errors.append(f"{where} {err}")
    tags = {normalize_key(t, pack.kaomoji) for t in _str_list(spec.get("tags"))}
    patterns = []
    for p in _str_list(spec.get("patterns")):
        try:
            patterns.append(compile_pattern(p))
        except (re.error, ValueError) as e:
            errors.append(f"{where} 패턴이 잘못됐습니다: {p} ({e})")
    color = str(spec.get("color", "") or "")
    if color and not _COLOR.fullmatch(color):
        errors.append(f"{where} color 는 #rrggbb 여야 합니다: {color}")
    label = str(spec.get("label", "") or "") or _default_label(members, pack.taxonomy) or cid
    return Chunk(cid, label, color or _default_color(members, pack.taxonomy), members, tags, patterns,
                 bool(spec.get("hidden")))


def _layout_plan(pack: TagPack, name: str) -> Plan:
    rows = []
    for sec in pack.layouts[name]["sections"]:
        inc, exc = parse_selectors(sec.get("include", ())), parse_selectors(sec.get("except", ()))
        rows.append([Chunk(sec["id"], sec.get("label", sec["id"]), _default_color(inc, pack.taxonomy), inc,
                           excepts=exc)])
    return Plan(rows, _rest_chunk(pack.taxonomy), pack.layouts[name].get("label", name), first_match=True)


def _specificity(sel: Selector) -> float:
    return (0 if sel.main == "*" else 2 if sel.sub else 1) + (0.5 if sel.identity else 0)


def _place(tokens: List[Token], plan: Plan, pack: TagPack) -> None:
    """토큰마다 청크를 정한다(뺀 토큰도 갈 자리를 적는다). 숨긴 청크의 태그는 hidden:<id> 로 뺀다.

    배열: 커스텀 청크의 태그 이름 → 커스텀 패턴 → 가장 구체적인 멤버(main/sub@identity > main/sub >
    main@identity > main > *) 순이고, 같으면 앞 줄·앞 청크·앞 멤버. 레이아웃: include 가 맞고 except 가 안 맞는
    첫 섹션. 어디에도 맞지 않으면 맨 끝 rest.
    """
    flat = [c for row in plan.rows for c in row]
    custom = [(i, c) for i, c in enumerate(flat) if c.tags or c.patterns]
    for t in tokens:
        slot, member = -1, 0
        names = {n for n in (t.key, t.alias_of, normalize_key(t.core or t.text, pack.kaomoji)) if n}
        for i, c in custom:                                  # 이름으로 적은 태그가 어느 청크의 패턴보다 먼저
            if names & c.tags:
                slot = i
                break
        if slot < 0:
            for i, c in custom:
                if any(p.fullmatch(n) for p in c.patterns for n in names):
                    slot = i
                    break
        if slot < 0 and plan.first_match:
            for i, c in enumerate(flat):
                if any(s.matches(t.primary, t.identity) for s in c.members) and not any(
                        s.matches(t.primary, t.identity) for s in c.excepts):
                    slot = i
                    break
        elif slot < 0:
            best: Optional[Tuple[Tuple[float, int, int], int, int]] = None
            for i, c in enumerate(flat):
                for m, s in enumerate(c.members):
                    if s.matches(t.primary, t.identity):
                        score = (_specificity(s), -i, -m)
                        if best is None or score > best[0]:
                            best = (score, i, m)
            if best is not None:
                slot, member = best[1], best[2]
        t.slot, t.member = slot, member
        chunk = flat[slot] if slot >= 0 else plan.rest
        t.section = chunk.id
        if chunk.hidden and not t.removed:
            t.removed = f"hidden:{chunk.id}"


def _sorted_chunk(items: List[Token], plan: Plan, opts: Options) -> List[Token]:
    if opts.order == "popular":
        items = sorted(items, key=lambda x: -(x.info.count if x.info else -1))
    elif opts.order == "alpha":
        items = sorted(items, key=lambda x: _display(x, opts))
    if plan.sub_order:
        items = sorted(items, key=lambda x: x.member)      # 안정 정렬: 같은 서브 안은 위 순서 그대로
    return items


def _render(tokens: List[Token], plan: Plan, opts: Options, out_dialect: str
            ) -> Tuple[List[str], List[List[Dict[str, Any]]]]:
    """(출력 줄, 줄 → 청크 구조). 줄 안의 청크는 ', ' 로 잇고 빈 청크·빈 줄은 건너뛴다. 구조에는 뺀 토큰도 넣는다."""
    by_slot: Dict[int, List[Token]] = {}
    for t in tokens:
        by_slot.setdefault(t.slot, []).append(t)
    rows: List[List[Tuple[int, Chunk]]] = []
    start = 0
    for row in plan.rows:
        rows.append([(start + k, c) for k, c in enumerate(row)])
        start += len(row)
    rows.append([(-1, plan.rest)])
    lines: List[str] = []
    arranged: List[List[Dict[str, Any]]] = []
    for row in rows:
        parts: List[str] = []
        chunks: List[Dict[str, Any]] = []
        for slot, c in row:
            items = by_slot.get(slot)
            if not items:
                continue
            items = _sorted_chunk(items, plan, opts)
            kept = [t for t in items if not t.removed]
            if kept:
                parts.append(", ".join(_format_token(t, opts, out_dialect) for t in kept))
            chunks.append({"id": c.id, "label": c.label, "color": c.color, "hidden": c.hidden,
                           "custom": bool(c.tags or c.patterns), "tokens": [t.index for t in items]})
        if parts:
            lines.append(", ".join(parts))
        if chunks:
            arranged.append(chunks)
    return lines, arranged


def organize(text: str, pack: Optional[TagPack] = None, options: Any = None) -> Result:
    """프롬프트를 정리한다. options 는 Options 또는 같은 키의 dict."""
    pack = pack if pack is not None else load_pack()
    opts = Options.coerce(options)
    sels = opts.validate(pack)
    if _has_arrangement(opts.arrangement):
        plan = load_arrangement(opts.arrangement, pack)
    else:
        plan = _layout_plan(pack, opts.layout)
    overrides = _overrides(pack, opts)
    guess_rules = plan.guess + _pack_guess_rules(pack) if opts.guess else []
    heads = set(pack.guess.get("head_categories", DEFAULT_GUESS_HEADS))
    out_dialect = opts.output_dialect or opts.input_dialect

    all_tokens: List[Token] = []
    chunk_texts: List[str] = []
    arranged: List[Dict[str, Any]] = []
    for ci, chunk in enumerate(_BREAK.split(text.replace("\r\n", "\n"))):
        tokens = parse_prompt(chunk, opts.input_dialect, pack)
        _classify(tokens, pack, opts)
        if opts.guess:
            _guess(tokens, pack, guess_rules, heads)
        if overrides:
            tokens = _apply_overrides(tokens, overrides, inject=ci == 0)
        for i, t in enumerate(tokens):
            t.chunk, t.order, t.index = ci, len(all_tokens) + i, len(all_tokens) + i
        if opts.dedupe:
            _dedupe(tokens)
        _apply_filters(tokens, sels, opts)
        _apply_rules(tokens, pack, opts)
        _place(tokens, plan, pack)
        lines, rows = _render(tokens, plan, opts, out_dialect)
        if lines:
            sep = (",\n" if opts.trailing_comma else "\n") if opts.line_per_section else ", "
            chunk_texts.append(sep.join(lines))
        arranged.append({"rows": rows})
        all_tokens += tokens

    joiner = "\nBREAK\n" if opts.line_per_section else " BREAK "
    removed = [t for t in all_tokens if t.removed]
    removed_text = "\n".join(f"{_display(t, opts)}  # {t.removed}" for t in removed)
    report = _report(all_tokens, pack, opts, out_dialect)
    report["arrangement"] = plan.label if _has_arrangement(opts.arrangement) else ""
    report["arranged"] = arranged
    return Result(joiner.join(chunk_texts), removed_text, report, all_tokens)


def _report(tokens: List[Token], pack: TagPack, opts: Options, out_dialect: str) -> Dict[str, Any]:
    rows = []
    for t in tokens:
        row: Dict[str, Any] = {
            "text": _display(t, opts),
            "kind": t.kind,
            "primary": t.primary,
            "section": t.section,
            "out": _format_token(t, opts, out_dialect),
        }
        if t.info is not None and not t.guess:
            row.update(key=t.key, rating=t.info.rating, status=t.info.status, count=t.info.count)
        if abs(t.weight - 1.0) > 1e-9:
            row["weight"] = round(t.weight, 4)
        for name in ("prefix", "alias_of", "removed", "flagged", "guess", "injected"):
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
            "guessed": sum(1 for t in tokens if t.guess),
        },
        "unknown": unknown,
        "unclassified": unclassified,
        "guessed": [{"tag": r["text"], "primary": r["primary"], "why": r["guess"]} for r in rows if "guess" in r],
        "removed": [{"tag": r["text"], "reason": r["removed"]} for r in rows if "removed" in r],
        "flagged": [{"tag": r["text"], "reason": r["flagged"]} for r in rows if "flagged" in r],
        "tokens": rows,
    }


# ─── 편집기용 ─────────────────────────────────────────────────────

def palette(pack: Any) -> Dict[str, Any]:
    """편집기(관리 툴·ComfyUI JS·Studio)가 쓰는 카테고리·서브·색, 크롭/시점 퀵 목록, 레이아웃 이름(JSON 그대로).

    pack 은 TagPack 이나 taxonomy·rules·layouts 속성을 가진 객체.
    """
    tax = pack.taxonomy
    cats = [{"id": c["id"], "label": c.get("label", c["id"]), "color": c.get("color", ""),
             "subs": [{"id": s["id"], "label": s.get("label", s["id"])} for s in c.get("subs", ())]}
            for c in tax.get("categories", ())]
    cats += [{"id": s["id"], "label": s.get("label", s["id"]), "color": s.get("color", ""), "subs": [],
              "system": True} for s in tax.get("system", ())]
    quick = (pack.rules or {}).get("quick") or {}
    return {"format": PALETTE_FORMAT, "categories": cats,
            "quick": {group: list(quick.get(group, ())) for group in OVERRIDE_TARGETS},
            "layouts": {lid: lay.get("label", lid) for lid, lay in pack.layouts.items()}}


def category_slots(taxonomy: Mapping[str, Any]) -> List[str]:
    """배치할 수 있는 칸 전부: 메인/서브마다 하나, 서브 없이 메인만 붙은 태그용 메인 하나, 시스템 카테고리."""
    out: List[str] = []
    for c in taxonomy.get("categories", ()):
        out += [f"{c['id']}/{s['id']}" for s in c.get("subs", ())]
        out.append(c["id"])
    return out + list(SYSTEM_CATEGORIES)


def arrangement_from_layout(pack: Any, layout: str = "default", sub_order: bool = False) -> Dict[str, Any]:
    """레이아웃 프리셋을 같은 결과를 내는 배열 세팅으로 바꾼다(편집을 시작할 때).

    섹션마다 한 줄이고, 섹션의 선택자를 칸(category_slots) 단위로 펼친다(앞 섹션이 가져간 칸과 except 는 뺀다).
    칸마다 고유 외형(identity) 태그와 나머지를 따로 센다: 섹션이 고유 외형만 가져가면 `칸@identity` 멤버가 되고,
    더 구체적이라 뒤 청크의 `칸` 보다 먼저 받는다. sub_order=False 면 출력이 레이아웃과 같다(다른 섹션이 받지 않는
    고유 외형을 except 로만 뺀 경우는 그 태그가 rest 대신 그 섹션으로 간다).
    """
    spec = pack.layouts[layout]
    slots = category_slots(pack.taxonomy)
    claimed: Set[Tuple[str, bool]] = set()
    rows: List[List[str]] = []
    chunks: Dict[str, Any] = {}
    for sec in spec["sections"]:
        exc = parse_selectors(sec.get("except", ()))
        got: Dict[str, Set[bool]] = {}                       # 이 섹션이 가져간 칸 → (나머지 False, 고유 외형 True)
        for sel in parse_selectors(sec.get("include", ())):
            for slot in slots:
                for ident in (False, True):
                    if (slot, ident) in claimed or not sel.matches(slot, ident) or any(
                            e.matches(slot, ident) for e in exc):
                        continue
                    claimed.add((slot, ident))
                    got.setdefault(slot, set()).add(ident)
        members = [slot if False in idents else f"{slot}@identity" for slot, idents in got.items()]
        if members:
            chunks[sec["id"]] = {"label": sec.get("label", sec["id"]), "members": members}
            rows.append([sec["id"]])
    return {"format": ARRANGEMENT_FORMAT, "version": ARRANGEMENT_VERSION, "label": spec.get("label", layout),
            "sub_order": sub_order, "rows": rows, "chunks": chunks}


def uncovered_slots(arrangement: Mapping[str, Any], taxonomy: Mapping[str, Any]) -> List[str]:
    """배치한 청크의 멤버가 하나도 맞지 않는 칸(그 태그는 맨 끝 rest 로 간다). 편집기 점검용."""
    specs = arrangement.get("chunks") or {}
    placed = [c for row in arrangement.get("rows") or () for c in ([row] if isinstance(row, str) else row)]
    sels = [s for cid in placed for s in parse_selectors((specs.get(cid) or {}).get("members", ()))
            if not s.identity]
    return [slot for slot in category_slots(taxonomy) if not any(s.matches(slot) for s in sels)]
