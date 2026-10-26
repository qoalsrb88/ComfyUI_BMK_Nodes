"""BMK Tag Organizer — Danbooru 태그를 카테고리별 줄로 정리하고 걸러내는 노드.

배경:
  태거 출력처럼 뒤섞인 Danbooru 프롬프트를 카테고리(인원·캐릭터·구도·행동·의상·
  신체·사물·이펙트·배경 등)별 줄로 정렬하고, 프리셋·등급·구도 규칙으로 필요 없는
  태그를 뺀다. 예: 미쿠 일러스트를 태깅한 프롬프트에서 캐릭터 고유 외형(머리·눈)만
  빼고 포즈·구도·배경은 남겨 다른 캐릭터에 쓰기.

  분류 데이터와 처리 로직은 TagOrganizer 저장소(정본)에서 온다.
  bmk_tag_organizer_core.py 와 tag_organizer_data/ 는 그 저장소의
  `python tagorg.py sync` 가 복사하는 파일이므로 이 폴더에서 직접 고치지 않는다.

옵션:
  - preset         : 필터 프리셋(정렬만 / 캐릭터 교체 / 의상 교체 / 포즈만 참고 …).
  - exclude        : 프리셋에 더해 뺄 카테고리. 쉼표로 구분.
                     예) "subject/character, clothes/top, *@identity"
                     (main, main/sub, 뒤에 @identity = 캐릭터 고유 외형만)
  - max_rating     : 이 등급을 넘는 태그를 뺀다. e = 거르지 않음, g = 일반만.
  - rules          : 구도 규칙(upper body 에서 신발 빼기, from behind 에서 배꼽 빼기 등).
                     remove = 빼기, report = 빼지 않고 report 에만 표시, off = 끄기.
  - layout         : 줄(섹션) 순서 프리셋.
  - input_dialect  : 입력 문법(comfyui / nai / anima).
  - output_dialect : 출력 문법. same = 입력과 같음.
  - line_per_section / trailing_comma / dedupe : 출력 모양과 중복 제거.

동작 세부:
  - 입력은 고치지 않고 정리한 새 문자열을 낸다. removed 출력에 뺀 태그와 이유가 줄마다 나온다.
  - DB에 없는 토큰(자연어·LoRA 트리거 등)과 아직 분류하지 않은 태그는 버리지 않고 마지막 줄에 둔다.
  - BREAK 로 나뉜 구간은 각각 따로 정리한다.
  - 태그 팩은 처음 실행할 때 한 번 읽고(약 0.2초) 파일이 바뀔 때까지 재사용한다.

버전 이력:
  - v1: 최초 구현(표준 위젯, TagOrganizer M1 데이터).
"""

from __future__ import annotations

import importlib
import json
import logging
import os
from types import ModuleType
from typing import Any, Dict, List

logger = logging.getLogger(__name__)
_TAG = "[ComfyUI_BMK_Nodes::TagOrganizer]"

_CORE_MODULE = "bmk_tag_organizer_core"
_PACK_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tag_organizer_data", "tagpack.json.gz")
_SYNC_HINT = "TagOrganizer 저장소에서 `python tagorg.py sync --to <이 노드 팩 폴더>` 를 실행하세요."

_RATINGS = ["e", "q", "s", "g"]
_RULE_MODES = ["remove", "report", "off"]
_DIALECTS = ["comfyui", "nai", "anima"]


def _core() -> ModuleType:
    """코어 모듈을 실행 시점에 불러온다. 코어가 없어도 노드 등록은 막지 않는다.

    패키지 안에서는 같은 폴더의 코어만 쓴다(다른 경로의 같은 이름 모듈을 잡지 않게).
    """
    try:
        if __package__:
            return importlib.import_module(f".{_CORE_MODULE}", __package__)
        return importlib.import_module(_CORE_MODULE)   # 패키지 밖에서 단독으로 불러올 때(점검용)
    except ImportError as e:
        raise RuntimeError(f"{_TAG} 코어 파일({_CORE_MODULE}.py)이 없습니다. {_SYNC_HINT}") from e


def _pack_path() -> str:
    return os.environ.get("TAGORG_PACK") or _PACK_PATH


def _as_labels(value: Any) -> Dict[str, str]:
    return {str(k): str(v) for k, v in value.items()} if isinstance(value, dict) else {}


def _read_info() -> Dict[str, Dict[str, str]]:
    """콤보 목록(presets·layouts → 표시명). 실제로 쓸 팩 옆의 pack_info.json 을 읽고,
    없거나 깨졌으면 팩에서 직접 읽는다. 둘 다 안 되면 빈 목록(노드 로딩은 막지 않는다)."""
    pack_path = _pack_path()
    info: Dict[str, Any] = {}
    try:
        with open(os.path.join(os.path.dirname(pack_path), "pack_info.json"), encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, dict):
            info = raw
    except (OSError, ValueError):
        pass
    labels = {key: _as_labels(info.get(key)) for key in ("presets", "layouts")}
    if not (labels["presets"] and labels["layouts"]) and os.path.isfile(pack_path):
        try:
            pack = _core().load_pack(pack_path)
            labels = {
                "presets": {k: str(v.get("label", k)) for k, v in pack.presets.items()},
                "layouts": {k: str(v.get("label", k)) for k, v in pack.layouts.items()},
            }
        except Exception:  # 콤보 목록 때문에 노드 정의가 깨지지 않게 기본값으로 둔다
            logger.warning("%s 팩에서 콤보 목록을 읽지 못했습니다: %s", _TAG, pack_path, exc_info=True)
    return labels


def _choices(labels: Dict[str, str], default: str) -> List[str]:
    return [default] + [k for k in labels if k != default]


def _tooltip(labels: Dict[str, str]) -> str:
    return "\n".join(f"  {k} = {v}" for k, v in labels.items())


class BMKTagOrganizer:
    TITLE = "BMK Tag Organizer"
    CATEGORY = "BMK/Text"
    FUNCTION = "organize"
    RETURN_TYPES = ("STRING", "STRING", "STRING")
    RETURN_NAMES = ("text", "removed", "report")
    DESCRIPTION = (
        "Danbooru 태그를 카테고리별 줄로 정렬하고, 프리셋·등급·구도 규칙으로 필요 없는 "
        "태그를 걸러냅니다. 입력은 고치지 않고 정리한 새 문자열과 뺀 태그 목록을 출력합니다."
    )
    SEARCH_ALIASES = [
        "tag organizer",
        "tag sorter",
        "prompt sorter",
        "prompt organizer",
        "danbooru",
        "tag filter",
        "태그 정리",
        "태그 정렬",
        "태그 분류",
        "태그 필터",
        "프롬프트 정리",
    ]

    @classmethod
    def INPUT_TYPES(cls):
        info = _read_info()
        presets = _choices(info["presets"], "sort_only")
        layouts = _choices(info["layouts"], "default")
        return {
            "required": {
                "text": (
                    "STRING",
                    {
                        "multiline": True,
                        "dynamicPrompts": False,
                        "tooltip": "정리할 프롬프트. 태거 출력 등 상류 노드를 연결해서 쓸 수 있습니다.",
                    },
                ),
                "preset": (
                    presets,
                    {"default": "sort_only", "tooltip": "필터 프리셋\n" + _tooltip(info["presets"])},
                ),
                "exclude": (
                    "STRING",
                    {
                        "default": "",
                        "multiline": False,
                        "tooltip": (
                            "프리셋에 더해 뺄 카테고리(쉼표로 구분).\n"
                            "예) subject/character, clothes/top, body/hair, *@identity\n"
                            "main = 메인 전체, main/sub = 서브만, @identity = 캐릭터 고유 외형만"
                        ),
                    },
                ),
                "max_rating": (
                    _RATINGS,
                    {
                        "default": "e",
                        "tooltip": "이 등급을 넘는 태그를 뺍니다. e = 거르지 않음, q, s, g = 일반만",
                    },
                ),
                "rules": (
                    _RULE_MODES,
                    {
                        "default": "remove",
                        "tooltip": (
                            "구도 규칙(upper body 에서 신발, from behind 에서 배꼽 등)\n"
                            "remove = 빼기, report = report 출력에만 표시, off = 끄기"
                        ),
                    },
                ),
                "layout": (
                    layouts,
                    {"default": "default", "tooltip": "줄(섹션) 순서\n" + _tooltip(info["layouts"])},
                ),
                "input_dialect": (_DIALECTS, {"default": "comfyui", "tooltip": "입력 프롬프트 문법"}),
                "output_dialect": (
                    ["same"] + _DIALECTS,
                    {"default": "same", "tooltip": "출력 문법. same = 입력과 같음"},
                ),
                "line_per_section": (
                    "BOOLEAN",
                    {"default": True, "tooltip": "카테고리(섹션)마다 줄을 바꿉니다. 끄면 한 줄로 냅니다."},
                ),
                "trailing_comma": (
                    "BOOLEAN",
                    {"default": True, "tooltip": "줄 끝에 쉼표를 붙입니다(마지막 줄 제외)."},
                ),
                "dedupe": ("BOOLEAN", {"default": True, "tooltip": "같은 태그가 다시 나오면 첫 번째만 남깁니다."}),
            }
        }

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        # 입력값은 ComfyUI 캐시 키에 이미 들어가므로, 출력에 영향을 주는 외부 상태인 팩 파일만 본다
        try:
            st = os.stat(_pack_path())
            return f"{st.st_mtime_ns}:{st.st_size}"
        except OSError:
            return "missing"

    def organize(
        self,
        text: str,
        preset: str,
        exclude: str,
        max_rating: str,
        rules: str,
        layout: str,
        input_dialect: str,
        output_dialect: str,
        line_per_section: bool,
        trailing_comma: bool,
        dedupe: bool,
    ):
        core = _core()
        path = _pack_path()
        if not os.path.isfile(path):
            raise RuntimeError(f"{_TAG} 태그 팩이 없습니다: {path}\n{_SYNC_HINT}")
        pack = core.load_pack(path)
        options = core.Options(
            preset=preset,
            exclude=exclude,
            max_rating=max_rating,
            rules=rules,
            layout=layout,
            input_dialect=input_dialect,
            output_dialect="" if output_dialect == "same" else output_dialect,
            line_per_section=line_per_section,
            trailing_comma=trailing_comma,
            dedupe=dedupe,
        )
        try:
            result = core.organize(text, pack, options)
        except ValueError as e:
            raise ValueError(f"{_TAG} {e}") from e
        counts = result.report["counts"]
        logger.debug(
            "%s pack=%s total=%d removed=%d unknown=%d unclassified=%d",
            _TAG, pack.version, counts["total"], counts["removed"], counts["unknown"], counts["unclassified"],
        )
        return (result.text, result.removed_text, json.dumps(result.report, ensure_ascii=False, indent=2))


NODE_CLASS_MAPPINGS = {
    "BMKTagOrganizer": BMKTagOrganizer,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "BMKTagOrganizer": "BMK Tag Organizer",
}
