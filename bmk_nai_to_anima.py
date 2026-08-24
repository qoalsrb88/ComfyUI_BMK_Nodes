"""BMK NAI To Anima — NovelAI V4/V5 프롬프트를 Anima용 본문 프롬프트로 정리.

배경
────
``bmk_prompt_from_image.py`` 로 뽑아낸 NAI 프롬프트를 Anima(Illustrious/NoobAI
계열, danbooru 태그 학습) i2i에 물릴 때, 그대로 넣으면 안 되는 구간이 앞뒤로
붙어 있다.

    1.0::artist:kim hyung tae ::, 0.5::granblue fantasy ::,   ← ① 화풍 prefix
    1girl, knight, solo, full body, character image, ...      ← 본문
    high complexity, depthness,                               ← ② NAI 전용 메타
    -1::simple illustration ::, -6::flat color ::,            ← ③ 음수 가중치
    year 2025, masterpiece, best quality, very aesthetic      ← ④ NAI 품질

①은 Anima에서 통하지 않고, ②는 danbooru 어휘가 아니라 CLIP이 서브워드로
쪼개 통제 불가능한 편향만 남기며, ③은 positive에서 표현할 수단이 없고,
④는 Anima 전용 품질 블록과 중복된다. 이 노드는 넷을 걷어내고, 덤으로 NAI가
danbooru 태그를 자체 표기로 바꿔 쓴 것들(``character image`` → ``tachi-e``
등)을 원래 표기로 되돌린다.

문법 변환(``가중치::내용::`` → ``(내용:가중치)``)은 하지 않는다. 단일 책임 —
그건 prompt_converter.py 가 정본이며, 이 노드 뒤에 체이닝한다.

    BMK Prompt From Image ─ positive
        └→ BMK NAI To Anima
            └→ Prompt Converter (NovelAI → ComfyUI)
                └→ Prompt Converter (ComfyUI → Anima)
                    └→ (Anima 품질 프롬프트를 앞에 concat) → CLIPTextEncode

옵션
────
- strip_prefix : 인원수/구도 앵커 태그(1girl, 2girls, 6+girls, multiple boys,
  no humans, solo, full body, character image, tachi-e …) 중 **처음 등장하는
  것** 앞을 전부 잘라낸다. 앵커가 가중치 블록 안에 있으면(``1.2::1girl ::``)
  블록 시작점까지 자른다.
- prefix_fallback_tags : 앵커가 하나도 없을 때만 쓰는 보험. 앞에서 이 개수만큼
  태그를 잘라낸다. 단 전체 태그가 (이 값 + 3)개 이상일 때만 발동하므로 짧은
  프롬프트를 통째로 날리지 않는다. 0 이면 보험 자체를 끈다.
- drop_negative_blocks : 음수 가중치 블록(``-1::내용 ::``)을 통째로 제거.
  태그 이름이 아니라 부호를 조건으로 하므로, 같은 태그라도 양수 가중치
  (``0.5::simple illustration ::``)면 살아남는다.
- strip_quality_tags : NAI 품질/메타 태그 제거(masterpiece, best quality,
  very aesthetic, year 2025, absurdres …). rating 태그(general / sensitive /
  questionable / explicit)는 **일부러 제외** — Illustrious·NoobAI 계열이 실제로
  학습한 태그라 Anima에서 유효하게 동작한다.
- drop_nai_style_meta : NAI 전용 화풍 메타 제거((low|high|ultra) complexity,
  depthness). danbooru 어휘가 아니다.
- restore_danbooru_syntax : NAI 표기 → danbooru 표기 역변환.
  peace sign→v, double peace→double v, bar eyes→|_|, open \\m/→\\||/,
  neutral face→:|, neco-arc eyes→<|> <|>, square bikini→eyepatch bikini,
  character image→tachi-e. 가중치로 감싸인 태그(``0.5::peace sign ::``)와
  복합 태그(``peace sign over eye`` → ``v over eye``)도 처리한다.
  ※ neutral face 는 NAI에서 ``:|`` 와 ``;|`` 양쪽이 합쳐진 표기라 역변환이
    1:1이 아니다. ``:|`` 로 고정한다.
- extra_remove_tags : 위 목록으로 안 잡히는 태그를 직접 지정(콤마/줄바꿈 구분).
  가중치 래퍼를 벗긴 내용과 대소문자·언더바 무시하고 정확히 비교한다.
- join_lines : True 면 전체를 ", " 한 줄로 접는다. 기본 False — 원본의 줄
  단위 의미 그룹(인물/의상/배경 …)을 유지하는 편이 읽기 좋고, 뒤에 오는
  Prompt Converter 도 줄 구조를 보존한다.

출력
────
- text   : 정리된 프롬프트.
- report : 무엇을 얼마나 걷어냈는지 항목별 요약. 앵커를 못 찾아 보험 절단이
  돌았는지, 의도치 않은 태그가 지워졌는지 확인하는 용도.

버전 이력
─────────
v1 (2026-08) : 최초. prefix 절단 / 음수 블록 / 품질·메타 / 표기 역변환.
"""

from __future__ import annotations

import logging
import re
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)
_TAG = "[ComfyUI_BMK_Nodes::NaiToAnima]"


# ─── 상수 테이블 ────────────────────────────────────────────────

# 본문 시작점으로 인정할 앵커. 관습적으로 인원수 → 구도 순으로 적으므로
# 처음 등장하는 것을 본문 시작으로 본다.
_ANCHOR_RE = re.compile(
    r"\b(?:"
    r"(?:[1-9]\d*\+?|multiple\s+)(?:girl|boy|other)s?"
    r"|no\s+humans?"
    r"|no\s+people"
    r"|solo"
    r"|full\s+body"
    r"|character\s+image"
    r"|tachi-?e"
    r")\b",
    re.IGNORECASE,
)

# NAI 가중치 블록. 내용에 ':' 단독은 허용하되(artist: 등) '::' 는 종료로 본다.
_BLOCK_BODY = r"(?:[^:]|:(?!:))*?"
_WEIGHT_BLOCK_RE = re.compile(r"[+-]?\d+(?:\.\d+)?\s*::" + _BLOCK_BODY + r"::")
_NEG_BLOCK_RE = re.compile(r"[ \t]*-\s*\d+(?:\.\d+)?\s*::" + _BLOCK_BODY + r"::[ \t]*,?")

# 태그 하나에서 가중치 래퍼를 벗겨낸다: "0.5::toned::" → ("0.5::", "toned", "::")
_WRAP_RE = re.compile(r"\A([+-]?\d+(?:\.\d+)?\s*::\s*)(.*?)(\s*::)\Z", re.DOTALL)

# NAI 품질/메타 태그. rating(general/sensitive/questionable/explicit)은 제외 —
# Illustrious·NoobAI 계열이 학습한 태그라 Anima에서 유효하다.
_QUALITY_EXACT = frozenset(
    {
        "masterpiece",
        "best quality",
        "amazing quality",
        "great quality",
        "good quality",
        "normal quality",
        "high quality",
        "low quality",
        "worst quality",
        "distinct image",
        "aesthetic",
        "very aesthetic",
        "very awa",
        "absurdres",
        "incredibly absurdres",
        "highres",
        "lowres",
        "no text",
        "newest",
        "recent",
        "early",
        "oldest",
        "location",
    }
)
_QUALITY_RE = re.compile(r"\Ayear\s*\d{4}\Z", re.IGNORECASE)

# NAI 전용 화풍 메타. danbooru 어휘가 아니라 CLIP이 서브워드로 쪼갠다.
_STYLE_META_RE = re.compile(
    r"\A(?:(?:very\s+)?(?:low|medium|high|ultra|extreme)\s+complexity|depth\s?ness)\Z",
    re.IGNORECASE,
)

# NAI 표기 → danbooru 표기. 긴 것부터 적용해야 double peace 가 peace sign 규칙에
# 먼저 먹히지 않는다.
_SYNTAX_MAP: Tuple[Tuple[str, str], ...] = (
    ("double peace sign", "double v"),
    ("double peace", "double v"),
    ("peace sign", "v"),
    ("bar eyes", "|_|"),
    (r"open \m/", r"\||/"),
    ("neutral face", ":|"),
    ("neco-arc eyes", "<|> <|>"),
    ("neco arc eyes", "<|> <|>"),
    ("square bikini", "eyepatch bikini"),
    ("character image", "tachi-e"),
)

# 태그 경계: 앞뒤가 단어문자/하이픈이 아니어야 한다. 고정폭 1이라 Python re의
# lookbehind 제약에 걸리지 않고, 문자열 시작에서도 성립한다.
_SYNTAX_RULES: Tuple[Tuple[re.Pattern, str], ...] = tuple(
    (re.compile(r"(?<![\w-])" + re.escape(src) + r"(?![\w-])", re.IGNORECASE), dst)
    for src, dst in _SYNTAX_MAP
)


# ─── 순수 로직 ──────────────────────────────────────────────────

def _normalize(text: str) -> str:
    """비교용 정규화 — 소문자, 언더바→공백, 연속 공백 축약."""
    return re.sub(r"\s+", " ", text.replace("_", " ")).strip().lower()


def _unwrap(tag: str) -> Tuple[str, str, str]:
    """가중치 래퍼를 벗겨 (접두, 내용, 접미) 로 나눈다. 래퍼가 없으면 접두/접미가 빈 문자열."""
    m = _WRAP_RE.match(tag)
    if m:
        return m.group(1), m.group(2), m.group(3)
    return "", tag, ""


def _find_body_start(text: str) -> Optional[int]:
    """본문(앵커)이 시작하는 인덱스. 못 찾으면 None.

    앵커가 가중치 블록 안에 있으면(예: ``1.2::1girl ::``) 블록 통째로 살려야
    하므로 블록 시작 인덱스를 돌려준다.
    """
    m = _ANCHOR_RE.search(text)
    if m is None:
        return None

    for block in _WEIGHT_BLOCK_RE.finditer(text):
        if block.start() <= m.start() < block.end():
            return block.start()
        if block.start() > m.start():
            break
    return m.start()


def _fallback_cut(text: str, keep_after: int) -> Optional[int]:
    """앞에서 keep_after 개 태그를 버릴 때의 절단 인덱스.

    전체 태그가 (keep_after + 3) 개 미만이면 절단하지 않는다(None) —
    짧은 프롬프트를 통째로 날리는 사고 방지.
    """
    if keep_after <= 0:
        return None

    # 콤마/줄바꿈 위치를 태그 경계로 본다.
    bounds = [m.end() for m in re.finditer(r"[,\n]\s*", text)]
    if len(bounds) < keep_after + 3:
        return None
    return bounds[keep_after - 1]


def _strip_negative_blocks(text: str) -> Tuple[str, List[str]]:
    removed: List[str] = []

    def _sub(m: re.Match) -> str:
        removed.append(m.group(0).strip().rstrip(",").strip())
        return ""

    return _NEG_BLOCK_RE.sub(_sub, text), removed


def _restore_syntax(inner: str) -> str:
    for pattern, replacement in _SYNTAX_RULES:
        # 치환문에 백슬래시(\||/)가 들어가므로 함수 형태로 넘겨 이스케이프를 피한다.
        inner = pattern.sub(lambda _m, r=replacement: r, inner)
    return inner


def convert(
    text: str,
    strip_prefix: bool = True,
    prefix_fallback_tags: int = 9,
    drop_negative_blocks: bool = True,
    strip_quality_tags: bool = True,
    drop_nai_style_meta: bool = True,
    restore_danbooru_syntax: bool = True,
    extra_remove_tags: str = "",
    join_lines: bool = False,
) -> Tuple[str, Dict[str, List[str]]]:
    """NAI 프롬프트를 Anima용 본문으로 정리한다.

    Returns:
        (결과 문자열, 항목별 제거/변환 내역)
    """
    log: Dict[str, List[str]] = {
        "prefix": [],
        "negative_blocks": [],
        "quality": [],
        "style_meta": [],
        "extra": [],
        "syntax": [],
        "notes": [],
    }

    result = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not result.strip():
        return "", log

    # 1) 음수 가중치 블록 — prefix 절단보다 먼저. 블록 안의 단어가 앵커로
    #    오인되는 것(예: -1::no humans ::)을 원천 차단한다.
    if drop_negative_blocks:
        result, log["negative_blocks"] = _strip_negative_blocks(result)

    # 2) prefix 절단
    if strip_prefix:
        cut = _find_body_start(result)
        if cut is None:
            log["notes"].append("앵커 태그를 찾지 못했습니다.")
            cut = _fallback_cut(result, prefix_fallback_tags)
            if cut is None:
                log["notes"].append("보험 절단 조건 미달 — prefix를 자르지 않았습니다.")
            else:
                log["notes"].append(f"보험 절단: 앞 {prefix_fallback_tags}개 태그 제거.")
        if cut:
            dropped = result[:cut]
            log["prefix"] = [t.strip() for t in re.split(r"[,\n]", dropped) if t.strip()]
            result = result[cut:]

    # 3) 태그 단위 처리 — 줄 구조와 줄 끝 콤마를 보존한다.
    extra_set = {
        _normalize(t) for t in re.split(r"[,\n]", extra_remove_tags or "") if t.strip()
    }

    out_lines: List[str] = []
    for line in result.split("\n"):
        if not line.strip():
            out_lines.append(line)
            continue

        had_trailing_comma = line.rstrip().endswith(",")
        surviving: List[str] = []

        for token in line.split(","):
            tag = token.strip()
            if not tag:
                continue

            head, inner, tail = _unwrap(tag)
            key = _normalize(inner)

            if strip_quality_tags and (key in _QUALITY_EXACT or _QUALITY_RE.match(key)):
                log["quality"].append(tag)
                continue
            if drop_nai_style_meta and _STYLE_META_RE.match(key):
                log["style_meta"].append(tag)
                continue
            if extra_set and key in extra_set:
                log["extra"].append(tag)
                continue

            if restore_danbooru_syntax:
                converted = _restore_syntax(inner)
                if converted != inner:
                    log["syntax"].append(f"{inner} → {converted}")
                    tag = f"{head}{converted}{tail}"

            surviving.append(tag)

        if not surviving:
            continue

        joined = ", ".join(surviving)
        if had_trailing_comma:
            joined += ","
        out_lines.append(joined)

    # 앞뒤 빈 줄 정리
    while out_lines and not out_lines[0].strip():
        out_lines.pop(0)
    while out_lines and not out_lines[-1].strip():
        out_lines.pop()

    if join_lines:
        flat = [ln.rstrip().rstrip(",").strip() for ln in out_lines if ln.strip()]
        result = ", ".join(p for p in flat if p)
    else:
        result = "\n".join(out_lines)
        result = re.sub(r"[ \t]*,[ \t]*\Z", "", result)

    return result, log


def _format_report(log: Dict[str, List[str]]) -> str:
    labels = (
        ("prefix", "화풍 prefix 제거"),
        ("negative_blocks", "음수 가중치 블록 제거"),
        ("quality", "NAI 품질 태그 제거"),
        ("style_meta", "NAI 화풍 메타 제거"),
        ("extra", "추가 지정 태그 제거"),
        ("syntax", "danbooru 표기 복원"),
    )
    lines: List[str] = []
    for key, label in labels:
        items = log.get(key) or []
        if items:
            lines.append(f"{label}: {len(items)}건")
            lines.extend(f"  - {item}" for item in items)
    for note in log.get("notes") or []:
        lines.append(f"[!] {note}")
    return "\n".join(lines) if lines else "변경 없음."


# ─── 노드 클래스 ────────────────────────────────────────────────

class BMKNaiToAnima:
    TITLE = "BMK NAI To Anima"
    CATEGORY = "BMK/Text"
    FUNCTION = "run"
    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("text", "report")
    DESCRIPTION = (
        "NovelAI V4/V5 프롬프트에서 Anima와 호환되지 않는 구간을 걷어냅니다. "
        "앞쪽 화풍 prefix, 음수 가중치 블록, NAI 품질/화풍 메타 태그를 제거하고 "
        "NAI 전용 표기(character image, peace sign …)를 danbooru 표기로 되돌립니다. "
        "'가중치::내용::' 문법 변환은 하지 않으니 뒤에 Prompt Converter를 체이닝하세요."
    )
    OUTPUT_TOOLTIPS = (
        "정리된 프롬프트. NAI 가중치 문법은 그대로이므로 Prompt Converter로 넘기세요.",
        "무엇을 얼마나 걷어냈는지 항목별 요약. 보험 절단이 돌았는지 여기서 확인합니다.",
    )
    SEARCH_ALIASES = [
        "nai to anima",
        "novelai to anima",
        "prompt cleanup",
        "strip artist prefix",
        "strip quality tags",
        "danbooru tags",
        "tachi-e",
        "프롬프트 정리",
        "작가 프롬프트 제거",
        "품질 태그 제거",
        "노벨ai",
        "아니마",
    ]

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "text": (
                    "STRING",
                    {
                        "multiline": True,
                        "dynamicPrompts": False,
                        "tooltip": "NovelAI V4/V5 프롬프트 원문.",
                    },
                ),
                "strip_prefix": (
                    "BOOLEAN",
                    {
                        "default": True,
                        "tooltip": (
                            "인원수/구도 앵커(1girl, 2girls, 6+girls, multiple boys, "
                            "no humans, solo, full body, character image, tachi-e) 중 "
                            "처음 등장하는 것 앞을 전부 제거합니다.\n"
                            "앵커가 가중치 블록 안에 있으면 블록 시작점까지만 자릅니다."
                        ),
                    },
                ),
                "prefix_fallback_tags": (
                    "INT",
                    {
                        "default": 9,
                        "min": 0,
                        "max": 64,
                        "step": 1,
                        "tooltip": (
                            "앵커를 하나도 못 찾았을 때만 쓰는 보험. 앞에서 이 개수만큼 "
                            "태그를 잘라냅니다.\n"
                            "전체 태그가 (이 값 + 3)개 이상일 때만 발동하므로 짧은 "
                            "프롬프트가 통째로 날아가지 않습니다.\n"
                            "0 = 보험 끄기(앵커가 없으면 prefix를 건드리지 않음)."
                        ),
                    },
                ),
                "drop_negative_blocks": (
                    "BOOLEAN",
                    {
                        "default": True,
                        "tooltip": (
                            "음수 가중치 블록(-1::simple illustration ::)을 통째로 "
                            "제거합니다.\n"
                            "태그 이름이 아니라 부호가 조건이므로 양수 가중치 "
                            "(0.5::simple illustration ::)는 그대로 남습니다."
                        ),
                    },
                ),
                "strip_quality_tags": (
                    "BOOLEAN",
                    {
                        "default": True,
                        "tooltip": (
                            "NAI 품질/메타 태그 제거: masterpiece, best quality, "
                            "amazing quality, distinct image, very aesthetic, very awa, "
                            "absurdres, highres, no text, year 2025, newest/oldest, "
                            "location 등.\n"
                            "rating 태그(general / sensitive / questionable / explicit)는 "
                            "Anima 계열에서도 유효하므로 제거하지 않습니다."
                        ),
                    },
                ),
                "drop_nai_style_meta": (
                    "BOOLEAN",
                    {
                        "default": True,
                        "tooltip": (
                            "NAI 전용 화풍 메타 제거: (low|medium|high|ultra|extreme) "
                            "complexity, depthness.\n"
                            "danbooru 어휘가 아니라 CLIP이 서브워드로 쪼개므로 통제 "
                            "불가능한 편향만 남습니다."
                        ),
                    },
                ),
                "restore_danbooru_syntax": (
                    "BOOLEAN",
                    {
                        "default": True,
                        "tooltip": (
                            "NAI 표기를 danbooru 표기로 되돌립니다.\n"
                            "peace sign→v, double peace→double v, bar eyes→|_|, "
                            "open \\m/→\\||/, neutral face→:|, neco-arc eyes→<|> <|>, "
                            "square bikini→eyepatch bikini, character image→tachi-e\n"
                            "※ neutral face 는 :| 와 ;| 가 합쳐진 표기라 :| 로 고정됩니다."
                        ),
                    },
                ),
                "join_lines": (
                    "BOOLEAN",
                    {
                        "default": False,
                        "tooltip": (
                            "True면 전체를 ', ' 한 줄로 접습니다. 기본 False — 원본의 "
                            "줄 단위 의미 그룹을 유지하며, 뒤의 Prompt Converter도 줄 "
                            "구조를 보존합니다."
                        ),
                    },
                ),
            },
            "optional": {
                "extra_remove_tags": (
                    "STRING",
                    {
                        "default": "",
                        "multiline": True,
                        "dynamicPrompts": False,
                        "tooltip": (
                            "기본 목록으로 안 잡히는 태그를 직접 지정(콤마 또는 줄바꿈 "
                            "구분). 가중치 래퍼를 벗긴 내용과 대소문자·언더바를 무시하고 "
                            "정확히 일치하는 태그만 제거합니다."
                        ),
                    },
                ),
            },
        }

    def run(
        self,
        text: str,
        strip_prefix: bool,
        prefix_fallback_tags: int,
        drop_negative_blocks: bool,
        strip_quality_tags: bool,
        drop_nai_style_meta: bool,
        restore_danbooru_syntax: bool,
        join_lines: bool,
        extra_remove_tags: str = "",
    ):
        result, log = convert(
            text=text,
            strip_prefix=strip_prefix,
            prefix_fallback_tags=prefix_fallback_tags,
            drop_negative_blocks=drop_negative_blocks,
            strip_quality_tags=strip_quality_tags,
            drop_nai_style_meta=drop_nai_style_meta,
            restore_danbooru_syntax=restore_danbooru_syntax,
            extra_remove_tags=extra_remove_tags,
            join_lines=join_lines,
        )

        for note in log.get("notes") or []:
            logger.warning("%s %s", _TAG, note)

        return (result, _format_report(log))


NODE_CLASS_MAPPINGS = {
    "BMKNaiToAnima": BMKNaiToAnima,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "BMKNaiToAnima": "BMK NAI To Anima",
}
