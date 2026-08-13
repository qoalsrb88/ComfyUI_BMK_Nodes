from __future__ import annotations

import re
from typing import Callable, List, Optional


ARTIST_SENTINEL = "__artist__"


def _protect_artist(text: str) -> str:
    return text.replace("artist:", ARTIST_SENTINEL)


def _restore_artist(text: str) -> str:
    return text.replace(ARTIST_SENTINEL, "artist:")


def _format_weight(weight: float) -> str:
    if weight.is_integer():
        return str(int(weight))
    return f"{weight:.6f}".rstrip("0").rstrip(".")


def _format_weight_anima(weight: float) -> str:
    """Anima 표기용 가중치 포맷터. 정수도 '1.0' 처럼 소수점 한 자리를 유지한다."""
    if weight.is_integer():
        return f"{weight:.1f}"
    return f"{weight:.6f}".rstrip("0").rstrip(".")


# ─── ComfyUI ↔ NovelAI (기존 로직 그대로 보존) ───────────────────

def _convert_comfyui_to_nai_body(comfyui_prompt: str) -> str:
    text = _protect_artist(comfyui_prompt)
    parts: List[str] = []
    current: List[str] = []
    i = 0

    while i < len(text):
        char = text[i]

        if char == "(":
            is_escaped_paren = i > 0 and text[i - 1] == "\\"

            i += 1
            balance = 1
            content_chars: List[str] = []

            while i < len(text) and balance > 0:
                inner = text[i]
                if inner == "(":
                    balance += 1
                elif inner == ")":
                    balance -= 1

                if balance > 0:
                    content_chars.append(inner)
                i += 1

            content = "".join(content_chars)

            if is_escaped_paren:
                if current and current[-1] == "\\":
                    current.pop()
                current.append(f"({content})")
                continue

            current_str = "".join(current).strip()
            if current_str:
                parts.append(current_str)
                current = []

            if content:
                weight = 1.1
                tags_str = content
                weight_match = re.search(r":([0-9.-]+)\s*$", content)
                if weight_match:
                    parsed_weight = float(weight_match.group(1))
                    if -5 <= parsed_weight <= 5:
                        weight = parsed_weight
                    tags_str = content[: weight_match.start()].strip()

                if tags_str:
                    parts.append(f"{_format_weight(weight)}::{tags_str}::")
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

    result = ", ".join(parts).replace("\\", "")
    return _restore_artist(result)


def _tokenize_nai_prompt(nai_prompt: str) -> List[str]:
    text = _protect_artist(nai_prompt)
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

    return [_restore_artist(token) for token in tokens]


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
        text = _protect_artist(prompt)
        artist_prefix_in_source = "artist:"  # _restore_artist 이후 기준
        prefix_converter = _convert_sdxl_to_anima_artist_prefix
        inverse_weight = False
    else:
        # Anima 쪽의 '@'는 가중치 콜론과 충돌하지 않아 sentinel 보호가 필요 없다.
        text = prompt
        artist_prefix_in_source = "@"
        prefix_converter = _convert_anima_to_sdxl_artist_prefix
        inverse_weight = True

    def _convert_loose(raw: str) -> str:
        """가중치 그룹 바깥의 텍스트 조각을 작가 prefix 치환 후 반환."""
        if is_sdxl_to_anima:
            return prefix_converter(_restore_artist(raw))
        return prefix_converter(raw)

    parts: List[str] = []
    current: List[str] = []
    i = 0

    while i < len(text):
        char = text[i]

        if char == "(":
            is_escaped_paren = i > 0 and text[i - 1] == "\\"

            i += 1
            balance = 1
            content_chars: List[str] = []

            while i < len(text) and balance > 0:
                inner = text[i]
                if inner == "(":
                    balance += 1
                elif inner == ")":
                    balance -= 1

                if balance > 0:
                    content_chars.append(inner)
                i += 1

            content = "".join(content_chars)

            if is_escaped_paren:
                # \(...\) 리터럴 괄호는 그대로 보존. current의 마지막 '\\'와
                # content 끝의 '\\'는 그대로 두고 닫는 ')'만 합쳐서 추가한다.
                current.append(f"({content})")
                continue

            current_str = "".join(current).strip()
            if current_str:
                parts.append(_convert_loose(current_str))
                current = []

            if content:
                weight: Optional[float] = None
                tags_str = content
                weight_match = re.search(
                    r":([+-]?(?:\d+(?:\.\d+)?|\.\d+))\s*$", content
                )
                if weight_match:
                    parsed_weight = float(weight_match.group(1))
                    if -10 <= parsed_weight <= 10:
                        weight = parsed_weight
                        tags_str = content[: weight_match.start()].strip()

                if tags_str:
                    # 작가 판정용으로는 prefix 치환 전(원본 표기) 기준이 명확하다.
                    source_tags = (
                        _restore_artist(tags_str) if is_sdxl_to_anima else tags_str
                    )
                    converted_tags = (
                        prefix_converter(source_tags)
                        if is_sdxl_to_anima
                        else prefix_converter(tags_str)
                    )

                    if weight is not None:
                        is_artist_only = _is_single_artist_group(
                            source_tags, artist_prefix_in_source
                        )
                        if is_artist_only and not apply_artist_weight_multiplier:
                            final_weight = weight
                        else:
                            final_weight = _scale_weight(
                                weight,
                                weight_multiplier,
                                inverse=inverse_weight,
                            )
                        parts.append(
                            f"({converted_tags}:{_format_weight_anima(final_weight)})"
                        )
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
