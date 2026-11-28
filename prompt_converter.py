from __future__ import annotations

from decimal import Decimal, InvalidOperation, localcontext
import math
import re
from typing import Callable, List, Optional


ARTIST_SENTINEL = "__artist__"
_WEIGHT_SUFFIX_RE = re.compile(r":([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*$")
_ARTIST_PREFIX_RE = re.compile(
    r"(^|[,(]\s*|::\s*)artist:(?!:|[+-]?(?:\d+(?:\.\d*)?|\.\d+)\s*\))"
)


def _protect_artist(text: str) -> str:
    return text.replace("artist:", ARTIST_SENTINEL)


def _restore_artist(text: str) -> str:
    return text.replace(ARTIST_SENTINEL, "artist:")


def _protect_artist_with_sentinel(text: str) -> tuple[str, str]:
    """태그 시작 위치의 작가 접두사 'artist:'만 입력에 없는 고유 표식으로 보호한다."""
    if not _ARTIST_PREFIX_RE.search(text):
        return text, ""
    sentinel = "\x00BMK_ARTIST\x00"
    idx = 0
    while sentinel in text:
        idx += 1
        sentinel = f"\x00BMK_ARTIST_{idx}\x00"
    return _ARTIST_PREFIX_RE.sub(lambda m: m.group(1) + sentinel, text), sentinel


def _restore_artist_with_sentinel(text: str, sentinel: str) -> str:
    if not sentinel:
        return text
    return text.replace(sentinel, "artist:")


def _unescape_literal_parens(text: str) -> str:
    return text.replace(r"\(", "(").replace(r"\)", ")")


def _format_decimal(value: Decimal, *, anima: bool) -> str:
    if value == 0:
        return "0.0" if anima else "0"
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if anima and "." not in text:
        return f"{text}.0"
    return text


def _format_weight(weight: float) -> str:
    if weight == 0:
        weight = 0.0
    if weight.is_integer():
        return str(int(weight))
    return f"{weight:.6f}".rstrip("0").rstrip(".")


def _format_weight_anima(weight: float) -> str:
    """Anima 표기용 가중치 포맷터. 정수도 '1.0' 처럼 소수점 한 자리를 유지한다."""
    if weight == 0:
        weight = 0.0
    if weight.is_integer():
        return f"{weight:.1f}"
    return f"{weight:.6f}".rstrip("0").rstrip(".")


def _scale_decimal_weight(weight_raw: str, multiplier: float, *, inverse: bool) -> str:
    dec_w = _parse_finite_decimal(weight_raw)
    if dec_w is None:
        return weight_raw
    mult_str = str(multiplier)
    with localcontext() as ctx:
        ctx.prec = max(64, len(weight_raw) + len(mult_str) + 32)
        mult_dec = Decimal(mult_str)
        if inverse:
            scaled = dec_w / mult_dec if mult_dec > 0 else dec_w
        else:
            scaled = dec_w * mult_dec
    return _format_decimal(scaled, anima=True)


def _parse_finite_decimal(raw: str) -> Optional[Decimal]:
    try:
        dec = Decimal(raw)
    except InvalidOperation:
        return None
    return dec if dec.is_finite() else None


# ─── ComfyUI ↔ NovelAI (기존 로직 그대로 보존) ───────────────────

def _convert_comfyui_to_nai_body(comfyui_prompt: str) -> str:
    text, sentinel = _protect_artist_with_sentinel(comfyui_prompt)
    parts: List[str] = []
    current: List[str] = []
    i = 0

    while i < len(text):
        char = text[i]

        if char == "\\" and i + 1 < len(text) and text[i + 1] in ("(", ")"):
            # ComfyUI 리터럴 괄호 이스케이프(\( \))만 언이스케이프하고 다른 백슬래시(\||/ 등)는 보존한다.
            current.append(text[i + 1])
            i += 2
            continue

        if char == "(":
            i += 1
            balance = 1
            content_chars: List[str] = []

            while i < len(text) and balance > 0:
                inner = text[i]
                if inner == "\\" and i + 1 < len(text) and text[i + 1] in ("(", ")"):
                    content_chars.append(inner)
                    content_chars.append(text[i + 1])
                    i += 2
                    continue
                if inner == "(":
                    balance += 1
                elif inner == ")":
                    balance -= 1

                if balance > 0:
                    content_chars.append(inner)
                i += 1

            content = "".join(content_chars)

            current_str = "".join(current).strip()
            if current_str:
                parts.append(current_str)
                current = []

            if content:
                weight = 1.1
                weight_dec: Optional[Decimal] = None
                tags_str = content
                weight_match = _WEIGHT_SUFFIX_RE.search(content)
                if weight_match:
                    raw_weight = weight_match.group(1)
                    parsed_weight = float(raw_weight)
                    if math.isfinite(parsed_weight):
                        weight = parsed_weight
                        tags_str = content[: weight_match.start()].strip()
                    else:
                        dec_weight = _parse_finite_decimal(raw_weight)
                        if dec_weight is not None:
                            weight_dec = dec_weight
                            tags_str = content[: weight_match.start()].strip()

                tags_str = _unescape_literal_parens(tags_str)
                if tags_str:
                    w_str = (
                        _format_decimal(weight_dec, anima=False)
                        if weight_dec is not None
                        else _format_weight(weight)
                    )
                    parts.append(f"{w_str}::{tags_str}::")
            continue

        if char == ",":
            current_str = "".join(current).strip()
            if current_str:
                parts.append(current_str)
                current = []
            i += 1
            continue

        current.append(char)
        i += 1

    current_str = "".join(current).strip()
    if current_str:
        parts.append(current_str)

    result = ", ".join(parts)
    return _restore_artist_with_sentinel(result, sentinel)


def _tokenize_nai_prompt(nai_prompt: str) -> List[str]:
    text, sentinel = _protect_artist_with_sentinel(nai_prompt)
    tokens: List[str] = []
    current: List[str] = []
    i = 0

    while i < len(text):
        weight_match = re.match(r"\s*([+-]?(?:\d+(?:\.\d+)?|\.\d+))::", text[i:])
        if weight_match:
            current_str = "".join(current).strip()
            if current_str:
                tokens.append(current_str)
                current = []

            prefix_len = weight_match.end()
            block_start = i + prefix_len
            block_end = text.find("::", block_start)
            if block_end != -1:
                tokens.append(text[i:block_end + 2].strip())
                i = block_end + 2
                while i < len(text) and text[i].isspace():
                    i += 1
                if i < len(text) and text[i] == ",":
                    i += 1
                while i < len(text) and text[i].isspace():
                    i += 1
                continue

        if text[i] == ",":
            current_str = "".join(current).strip()
            if current_str:
                tokens.append(current_str)
                current = []
            i += 1
            continue

        current.append(text[i])
        i += 1

    current_str = "".join(current).strip()
    if current_str:
        tokens.append(current_str)

    return [_restore_artist_with_sentinel(token, sentinel) for token in tokens]


def _escape_literal_parens(text: str) -> str:
    return text.replace("(", r"\(").replace(")", r"\)")


def _convert_nai_to_comfyui_body(novelai_prompt: str) -> str:
    tokens = _tokenize_nai_prompt(novelai_prompt)
    comfyui_parts: List[str] = []

    for token in tokens:
        match = re.fullmatch(r"([+-]?(?:\d+(?:\.\d+)?|\.\d+))::(.*?)::", token)
        if match:
            weight = match.group(1)
            tags = _escape_literal_parens(match.group(2).strip())
            comfyui_parts.append(f"({tags}:{weight})")
        else:
            comfyui_parts.append(_escape_literal_parens(token.strip()))

    return ", ".join(part for part in comfyui_parts if part)


# ─── ComfyUI ↔ Anima (신규) ──────────────────────────────────────
#
# SDXL/ComfyUI 표기와 Anima 표기의 차이는 두 가지로 요약된다.
#   1) 작가 prefix:  artist:name   ↔   @name
#   2) 가중치 스케일: Anima는 SDXL보다 더 강한 가중치가 필요해
#      ComfyUI → Anima 변환 시 weight × multiplier 를 적용한다.
#      Anima → ComfyUI 변환은 그 역으로 weight / multiplier 를 적용한다.
#
# 단, 가중치가 명시되지 않은 태그( `tag` 또는 `(content)` )는 그대로 둔다.
# 작가 가중치(가중치 그룹 콘텐츠가 단일 작가 태그)는 옵션에 따라
# 배율 적용 여부를 결정한다.

def _convert_sdxl_to_anima_artist_prefix(text: str) -> str:
    """텍스트 내의 'artist:' 작가 prefix를 Anima의 '@'로 치환한다.
    콤마로 구분된 각 태그의 선행 공백을 보존한 채 변환한다."""
    if not text:
        return text
    parts = text.split(",")
    converted: List[str] = []
    for p in parts:
        stripped = p.lstrip()
        leading_ws = p[: len(p) - len(stripped)]
        if stripped.startswith("artist:"):
            converted.append(leading_ws + "@" + stripped[len("artist:"):])
        else:
            converted.append(p)
    return ",".join(converted)


def _convert_anima_to_sdxl_artist_prefix(text: str) -> str:
    """텍스트 내의 '@' 작가 prefix를 SDXL의 'artist:'로 치환한다."""
    if not text:
        return text
    parts = text.split(",")
    converted: List[str] = []
    for p in parts:
        stripped = p.lstrip()
        leading_ws = p[: len(p) - len(stripped)]
        if stripped.startswith("@"):
            converted.append(leading_ws + "artist:" + stripped[1:])
        else:
            converted.append(p)
    return ",".join(converted)


def _is_single_artist_group(content: str, prefix: str) -> bool:
    """가중치 그룹 콘텐츠가 단일 작가 태그(prefix로 시작, 콤마 없음)인지 확인.
    여러 태그가 한 가중치 그룹에 묶여 있으면 일반 가중치 그룹으로 간주한다."""
    stripped = content.strip()
    if not stripped:
        return False
    if "," in stripped:
        return False
    return stripped.startswith(prefix)


def _scale_weight(
    weight: float,
    multiplier: float,
    *,
    inverse: bool,
) -> float:
    """가중치에 배율 적용. inverse=True면 역방향(나눗셈)."""
    if inverse:
        if multiplier > 0:
            return weight / multiplier
        return weight
    return weight * multiplier


def _convert_comfyui_to_anima_body(
    comfyui_prompt: str,
    weight_multiplier: float,
    apply_artist_weight_multiplier: bool,
) -> str:
    """ComfyUI/SDXL 문법을 Anima 문법으로 변환.

    - artist:name   →   @name
    - (tag:weight)  →   (tag : weight × multiplier)
    - 가중치가 없는 태그/그룹은 그대로 유지.
    - 단일 작가 가중치 그룹은 apply_artist_weight_multiplier=False 일 때 원본 보존.
    - 이스케이프된 괄호 \\(...\\) 는 그대로 유지 (Anima도 ComfyUI식 이스케이프 사용).
    """
    return _convert_comfyui_anima_core(
        comfyui_prompt,
        weight_multiplier=weight_multiplier,
        apply_artist_weight_multiplier=apply_artist_weight_multiplier,
        direction="sdxl_to_anima",
    )


def _convert_anima_to_comfyui_body(
    anima_prompt: str,
    weight_multiplier: float,
    apply_artist_weight_multiplier: bool,
) -> str:
    """Anima 문법을 ComfyUI/SDXL 문법으로 변환.

    - @name         →   artist:name
    - (tag:weight)  →   (tag : weight ÷ multiplier)
    - 그 외 규칙은 정방향과 동일.
    """
    return _convert_comfyui_anima_core(
        anima_prompt,
        weight_multiplier=weight_multiplier,
        apply_artist_weight_multiplier=apply_artist_weight_multiplier,
        direction="anima_to_sdxl",
    )


def _convert_comfyui_anima_core(
    prompt: str,
    *,
    weight_multiplier: float,
    apply_artist_weight_multiplier: bool,
    direction: str,
) -> str:
    """ComfyUI ↔ Anima 변환의 공통 파싱/생성 코어.

    direction 값에 따라 작가 prefix 치환 방향과 가중치 적용 방향(곱/나눗셈)이 결정된다.
    """
    is_sdxl_to_anima = direction == "sdxl_to_anima"

    if is_sdxl_to_anima:
        text, sentinel = _protect_artist_with_sentinel(prompt)
        artist_prefix_in_source = "artist:"  # _restore_artist_with_sentinel 이후 기준
        prefix_converter = _convert_sdxl_to_anima_artist_prefix
        inverse_weight = False
    else:
        # Anima 쪽의 '@'는 가중치 콜론과 충돌하지 않아 sentinel 보호가 필요 없다.
        text, sentinel = prompt, ""
        artist_prefix_in_source = "@"
        prefix_converter = _convert_anima_to_sdxl_artist_prefix
        inverse_weight = True

    def _convert_loose(raw: str) -> str:
        """가중치 그룹 바깥의 텍스트 조각을 작가 prefix 치환 후 반환."""
        if is_sdxl_to_anima:
            return prefix_converter(_restore_artist_with_sentinel(raw, sentinel))
        return prefix_converter(raw)

    parts: List[str] = []
    current: List[str] = []
    i = 0

    while i < len(text):
        char = text[i]

        if char == "\\" and i + 1 < len(text) and text[i + 1] in ("(", ")"):
            # \( \) 리터럴 괄호는 Anima에서도 이스케이프 그대로 보존한다.
            current.append(char)
            current.append(text[i + 1])
            i += 2
            continue

        if char == "(":
            i += 1
            balance = 1
            content_chars: List[str] = []

            while i < len(text) and balance > 0:
                inner = text[i]
                if inner == "\\" and i + 1 < len(text) and text[i + 1] in ("(", ")"):
                    content_chars.append(inner)
                    content_chars.append(text[i + 1])
                    i += 2
                    continue
                if inner == "(":
                    balance += 1
                elif inner == ")":
                    balance -= 1

                if balance > 0:
                    content_chars.append(inner)
                i += 1

            content = "".join(content_chars)

            current_str = "".join(current).strip()
            if current_str:
                parts.append(_convert_loose(current_str))
                current = []

            if content:
                weight: Optional[float] = None
                weight_raw: Optional[str] = None
                tags_str = content
                weight_match = _WEIGHT_SUFFIX_RE.search(content)
                if weight_match:
                    raw_weight = weight_match.group(1)
                    parsed_weight = float(raw_weight)
                    if math.isfinite(parsed_weight):
                        weight = parsed_weight
                        weight_raw = raw_weight
                        tags_str = content[: weight_match.start()].strip()
                    elif _parse_finite_decimal(raw_weight) is not None:
                        weight_raw = raw_weight
                        tags_str = content[: weight_match.start()].strip()

                if tags_str:
                    # 작가 판정용으로는 prefix 치환 전(원본 표기) 기준이 명확하다.
                    source_tags = (
                        _restore_artist_with_sentinel(tags_str, sentinel)
                        if is_sdxl_to_anima
                        else tags_str
                    )
                    converted_tags = (
                        prefix_converter(source_tags)
                        if is_sdxl_to_anima
                        else prefix_converter(tags_str)
                    )

                    if weight_raw is not None:
                        is_artist_only = _is_single_artist_group(
                            source_tags, artist_prefix_in_source
                        )
                        if weight is not None:
                            if is_artist_only and not apply_artist_weight_multiplier:
                                final_weight = weight
                            else:
                                final_weight = _scale_weight(
                                    weight,
                                    weight_multiplier,
                                    inverse=inverse_weight,
                                )
                            if math.isfinite(final_weight):
                                w_str = _format_weight_anima(final_weight)
                            else:
                                w_str = _scale_decimal_weight(
                                    weight_raw, weight_multiplier, inverse=inverse_weight
                                )
                        else:
                            dec_w = _parse_finite_decimal(weight_raw)
                            assert dec_w is not None
                            if is_artist_only and not apply_artist_weight_multiplier:
                                w_str = _format_decimal(dec_w, anima=True)
                            else:
                                w_str = _scale_decimal_weight(
                                    weight_raw, weight_multiplier, inverse=inverse_weight
                                )
                        parts.append(f"({converted_tags}:{w_str})")
                    else:
                        parts.append(f"({converted_tags})")
            continue

        if char == ",":
            current_str = "".join(current).strip()
            if current_str:
                parts.append(_convert_loose(current_str))
                current = []
            i += 1
            continue

        current.append(char)
        i += 1

    current_str = "".join(current).strip()
    if current_str:
        parts.append(_convert_loose(current_str))

    return ", ".join(parts)


# ─── 라인 브레이크 보존 (기존) ──────────────────────────────────

def _convert_preserve_line_breaks(converter: Callable[[str], str], text: str) -> str:
    normalized = text.replace("\r\n", "\n")
    lines = normalized.split("\n")
    output_lines: List[str] = []

    for line in lines:
        if not line.strip():
            output_lines.append(line)
            continue

        comma_match = re.match(r"^(\s*)(.*?)(\s*),(\s*)$", line)
        if comma_match:
            leading = comma_match.group(1)
            body = comma_match.group(2)
            suffix = f"{comma_match.group(3)},{comma_match.group(4)}"
        else:
            leading_match = re.match(r"^\s*", line)
            leading = leading_match.group(0) if leading_match else ""
            body = line[len(leading):]
            suffix = ""

        if not body.strip():
            output_lines.append(line)
            continue

        converted = converter(body)
        output_lines.append(f"{leading}{converted}{suffix}")

    return "\n".join(output_lines)


# ─── 노드 클래스 ────────────────────────────────────────────────

class BMKPromptSyntaxConverter:
    TITLE = "Prompt Converter"
    CATEGORY = "BMK Nodes/Prompt"
    FUNCTION = "convert"
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("text",)
    DESCRIPTION = (
        "Converts prompt syntax between ComfyUI, NovelAI V4, and Anima. "
        "Anima modes support a configurable weight multiplier and an "
        "artist-weight scaling toggle."
    )
    SEARCH_ALIASES = [
        "bmk",
        "prompt converter",
        "prompt",
        "converter",
        "syntax",
        "novelai",
        "novel ai",
        "comfyui",
        "nai",
        "anima",
        "text utility",
    ]

    MODES = [
        "ComfyUI → NovelAI",
        "NovelAI → ComfyUI",
        "ComfyUI → Anima",
        "Anima → ComfyUI",
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
                        "tooltip": "Prompt text to convert.",
                    },
                ),
                "mode": (
                    cls.MODES,
                    {
                        "tooltip": "Select the conversion direction.",
                    },
                ),
                "weight_multiplier": (
                    "FLOAT",
                    {
                        "default": 1.5,
                        "min": 0.1,
                        "max": 5.0,
                        "step": 0.05,
                        "tooltip": (
                            "Weight scale factor used by Anima conversion modes only.\n"
                            "ComfyUI → Anima : new = original × multiplier\n"
                            "Anima → ComfyUI : new = original ÷ multiplier\n"
                            "Tags without an explicit weight are left untouched.\n"
                            "Ignored in NovelAI modes."
                        ),
                    },
                ),
                "apply_artist_weight_multiplier": (
                    "BOOLEAN",
                    {
                        "default": False,
                        "tooltip": (
                            "If enabled, the weight multiplier is also applied to "
                            "single-artist weight groups such as (artist:naga u:0.5) "
                            "or (@naga u:0.5). If disabled, those artist weights are "
                            "preserved as-is. Artist tags without an explicit weight "
                            "are always left untouched, regardless of this toggle."
                        ),
                    },
                ),
            }
        }

    def convert(
        self,
        text: str,
        mode: str,
        weight_multiplier: float,
        apply_artist_weight_multiplier: bool,
    ):
        if mode == "ComfyUI → NovelAI":
            result = _convert_preserve_line_breaks(
                _convert_comfyui_to_nai_body, text
            )
        elif mode == "NovelAI → ComfyUI":
            result = _convert_preserve_line_breaks(
                _convert_nai_to_comfyui_body, text
            )
        elif mode == "ComfyUI → Anima":
            result = _convert_preserve_line_breaks(
                lambda body: _convert_comfyui_to_anima_body(
                    body,
                    weight_multiplier,
                    apply_artist_weight_multiplier,
                ),
                text,
            )
        elif mode == "Anima → ComfyUI":
            result = _convert_preserve_line_breaks(
                lambda body: _convert_anima_to_comfyui_body(
                    body,
                    weight_multiplier,
                    apply_artist_weight_multiplier,
                ),
                text,
            )
        else:
            raise ValueError(f"Unsupported conversion mode: {mode}")

        return (result,)


NODE_CLASS_MAPPINGS = {
    "BMKPromptSyntaxConverter": BMKPromptSyntaxConverter,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "BMKPromptSyntaxConverter": "Prompt Converter",
}
