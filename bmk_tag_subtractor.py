"""BMK Tag Subtractor — reference에 있는 태그를 target에서 최대 n회까지 제거.

A1111식 품질 태그 프리픽스처럼 target 프롬프트에 reference와 동일한 태그
블록이 중복 포함될 때, 그 중복만 걷어내기 위한 노드. reference의 각 태그를
target 등장 순서대로 최대 n회까지 제거하며, 그 이후 등장하는 동일 태그
(예: 본문 뒤쪽의 masterpiece)는 보존한다.

옵션:
- max_removals_per_tag : 태그별 최대 제거 횟수.
    0 = 무제한(모든 등장 제거), 1 = 최초 1회만(기본), n = 최초 n회.
    카운트는 줄 경계와 무관하게 target 전체 순서로 누적된다.
- separator : 태그 구분자(기본 ","). 매칭 전 각 태그 양쪽 공백을 trim.

동작 세부:
- target의 줄바꿈과 줄 끝 콤마는 보존한다.
- 한 줄의 모든 태그가 제거되면 그 줄 자체를 출력에서 생략한다.
- 빈 줄(공백만 있는 줄 포함)은 그대로 보존한다.
- reference의 줄바꿈은 의미가 없으며 고유 태그 집합으로 평탄화된다.

버전 이력:
- v1: prompt_tag_subtractor.py — 최초 구현
      (최초 1회 제거 + n회 옵션 + 줄바꿈/줄 끝 콤마 보존).
- v2: 규약 v2 이관 — bmk_ 파일명, CATEGORY "BMK/Text", 한국어 DESCRIPTION,
      SEARCH_ALIASES 정비("bmk" 제거·한국어 추가), 로깅 패턴 적용.
      노드 ID(BMKPromptTagSubtractor)는 기존 워크플로 호환을 위해 유지.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Set, Tuple

logger = logging.getLogger(__name__)
_TAG = "[ComfyUI_BMK_Nodes::TagSubtractor]"


# ─── 핵심 로직 ────────────────────────────────────────────────────

def _parse_reference_tags(reference: str, separator: str) -> Set[str]:
    """reference 문자열을 고유 태그 집합으로 평탄화한다.

    reference의 줄바꿈은 의미가 없으므로 전부 무시하고 태그만 추출한다.
    """
    ref_tags: Set[str] = set()
    for line in reference.split("\n"):
        for token in line.split(separator):
            stripped = token.strip()
            if stripped:
                ref_tags.add(stripped)
    return ref_tags


def _subtract_reference_tags(
    target: str,
    reference: str,
    max_removals_per_tag: int,
    separator: str,
) -> Tuple[str, Dict[str, int]]:
    """target에서 reference에 등장하는 태그를 태그별 최대 n회까지 제거한다.

    - max_removals_per_tag == 0 : 무제한 (해당 태그의 모든 등장 제거)
    - max_removals_per_tag == 1 : 최초 1회만 제거 (기본값)
    - max_removals_per_tag == n : 최초 n회까지 제거
    카운트는 줄 경계를 가로질러 전체 문서 순서로 누적된다.
    target의 줄바꿈, 줄 끝 콤마는 보존한다.
    한 줄의 모든 태그가 제거된 경우 해당 줄은 출력에서 생략한다.

    Returns:
        (결과 문자열, 태그별 제거 횟수 딕셔너리)
    """
    ref_tags = _parse_reference_tags(reference, separator)
    if not ref_tags:
        return target, {}

    unlimited = (max_removals_per_tag == 0)
    removal_counts: Dict[str, int] = {}

    normalized = target.replace("\r\n", "\n")
    output_lines: List[str] = []

    for line in normalized.split("\n"):
        # 완전히 빈 줄(공백만 있는 줄 포함)은 그대로 보존
        if not line.strip():
            output_lines.append(line)
            continue

        # 줄 끝 콤마 여부 기록 (보존용)
        had_trailing_separator = line.rstrip().endswith(separator)

        surviving: List[str] = []
        for token in line.split(separator):
            stripped = token.strip()
            if not stripped:
                continue
            if stripped in ref_tags:
                count = removal_counts.get(stripped, 0)
                if unlimited or count < max_removals_per_tag:
                    removal_counts[stripped] = count + 1
                    continue  # 이 태그는 제거
            surviving.append(stripped)

        # 줄의 모든 태그가 제거되었으면 그 줄 자체를 생략
        if not surviving:
            continue

        joined = ", ".join(surviving)
        if had_trailing_separator:
            joined += separator
        output_lines.append(joined)

    return "\n".join(output_lines), removal_counts


# ─── 노드 클래스 ──────────────────────────────────────────────────

class BMKPromptTagSubtractor:
    TITLE = "BMK Tag Subtractor"
    CATEGORY = "BMK/Text"
    FUNCTION = "subtract"
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("text",)
    DESCRIPTION = (
        "reference 프롬프트에 존재하는 태그를 target 프롬프트에서 태그별 최대 "
        "n회까지 제거합니다(0=무제한, 1=최초 1회). target의 줄바꿈과 줄 끝 "
        "콤마는 보존되며, 모든 태그가 제거된 줄은 출력에서 생략됩니다."
    )
    SEARCH_ALIASES = [
        "tag subtractor",
        "prompt tag subtractor",
        "remove tags",
        "remove duplicate tags",
        "dedupe",
        "subtract",
        "태그 제거",
        "태그 빼기",
        "중복 태그 제거",
        "프롬프트 중복 제거",
    ]

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "target": (
                    "STRING",
                    {
                        "multiline": True,
                        "dynamicPrompts": False,
                        "tooltip": (
                            "Target prompt. Tags listed in 'reference' will be "
                            "removed from this text. Line breaks and trailing "
                            "commas are preserved."
                        ),
                    },
                ),
                "reference": (
                    "STRING",
                    {
                        "multiline": True,
                        "dynamicPrompts": False,
                        "tooltip": (
                            "Reference prompt. Tags found here will be removed "
                            "from the target. Line breaks in the reference are "
                            "ignored — only the flat set of unique tags is used."
                        ),
                    },
                ),
                "max_removals_per_tag": (
                    "INT",
                    {
                        "default": 1,
                        "min": 0,
                        "max": 9999,
                        "step": 1,
                        "tooltip": (
                            "Maximum number of times each reference tag can be "
                            "removed from the target.\n"
                            "  0 = unlimited (remove every occurrence)\n"
                            "  1 = remove only the first occurrence (default)\n"
                            "  n = remove the first n occurrences\n"
                            "Counting is global across the entire target, "
                            "not per line."
                        ),
                    },
                ),
                "separator": (
                    "STRING",
                    {
                        "default": ",",
                        "tooltip": (
                            "Tag separator. Usually a comma. Whitespace around "
                            "each tag is trimmed before matching."
                        ),
                    },
                ),
            }
        }

    def subtract(
        self,
        target: str,
        reference: str,
        max_removals_per_tag: int,
        separator: str,
    ):
        result, removal_counts = _subtract_reference_tags(
            target=target,
            reference=reference,
            max_removals_per_tag=max_removals_per_tag,
            separator=separator,
        )
        if removal_counts:
            logger.debug(
                "%s removed %d tag occurrence(s): %s",
                _TAG,
                sum(removal_counts.values()),
                removal_counts,
            )
        return (result,)


NODE_CLASS_MAPPINGS = {
    "BMKPromptTagSubtractor": BMKPromptTagSubtractor,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "BMKPromptTagSubtractor": "BMK Tag Subtractor",
}
