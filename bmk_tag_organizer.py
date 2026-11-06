"""BMK Tag Organizer — Danbooru 태그를 카테고리별 줄로 정리하고 걸러내는 노드.

배경:
  태거 출력처럼 뒤섞인 Danbooru 프롬프트를 카테고리(인원·캐릭터·구도·행동·의상·
  신체·사물·이펙트·배경 등)별 줄로 정렬하고, 프리셋·등급·구도 규칙으로 필요 없는
  태그를 뺀다. 예: 미쿠 일러스트를 태깅한 프롬프트에서 캐릭터 고유 외형(머리·눈)만
  빼고 포즈·구도·배경은 남겨 다른 캐릭터에 쓰기.

  분류 데이터와 처리 로직은 TagOrganizer 저장소(정본)에서 온다.
  bmk_tag_organizer_core.py, tag_organizer_data/, js/tag_organizer/(청크 편집기) 는 그 저장소의
  `python tagorg.py sync` 가 복사하는 파일이므로 이 폴더에서 직접 고치지 않는다.

옵션:
  - preset         : 필터 프리셋(정렬만 / 캐릭터 교체 / 의상 교체 / 포즈만 참고 …).
  - exclude        : 프리셋에 더해 뺄 카테고리. 쉼표로 구분.
                     예) "subject/character, clothes/top, *@identity"
                     (main, main/sub, 뒤에 @identity = 캐릭터 고유 외형만)
  - max_rating     : 이 등급을 넘는 태그를 뺀다. e = 거르지 않음, g = 일반만.
  - rules          : 구도 규칙(upper body 에서 신발 빼기, from behind 에서 배꼽 빼기 등).
                     remove = 빼기, report = 빼지 않고 report 에만 표시, off = 끄기.
  - layout         : 줄(섹션) 순서 프리셋. 배열 세팅(arrangement)이 있으면 쓰지 않는다.
  - input_dialect  : 입력 문법(comfyui / nai / anima / auto). auto = 프롬프트의 표기로 알아낸다
                     (NAI 1.2::x ::·{x}, ComfyUI (x:1.2)·\\(·<lora:>, Anima @작가).
  - output_dialect : 출력 문법. same = 입력과 같음(auto 면 알아낸 문법). NAI 숫자 강조는 `1.1::x ::` 처럼
                     닫는 :: 앞을 띄운다.
  - line_per_section / trailing_comma / dedupe : 출력 모양과 중복 제거.
  - framing        : 크롭 덮어쓰기. 프롬프트의 크롭 태그를 모두 빼고 고른 태그를 넣어, 구도 규칙이 바로 그
                     크롭으로 돈다. auto = 프롬프트 그대로.
  - view           : 시점 덮어쓰기(숨김 위젯, 쉼표로 여럿). 편집기 위의 시점 칩이 쓴다. 방식은 framing 과 같고,
                     부딪치는 시점은 칩을 켜는 순간 꺼지며 multiple views 칩을 켜면 함께 고른다. multiple views 만
                     고르면 프롬프트의 시점을 지우지 않는다. 비우거나 auto(v5 까지의 콤보 값) = 프롬프트 그대로.
  - gaze           : 시선 덮어쓰기(숨김 위젯, 쉼표로 여럿). 편집기 위의 시선 칩이 쓴다. 프롬프트의 시선 태그를
                     고른 태그들로 바꾸되, 크롭·시점과 함께 그릴 수 없는 태그(straight-on 의 looking back 등)는
                     넣지 않는다. 비우면 프롬프트 그대로.
  - pose           : 포즈 덮어쓰기(숨김 위젯, 쉼표로 여럿). 편집기 위의 포즈 칩이 쓴다. 프롬프트의 자세 태그를 고른
                     태그들로 바꾸되 장소(on bed)·구속(bound) 태그는 남긴다. 몸이 동시에 할 수 없는 자세는 칩을 켜는 순간
                     꺼지고, portrait·close-up 에서는 넣지 않는다. 비우면 프롬프트 그대로.
  - guess          : 팩에 없는 토큰(자연어 구절 등)의 분류를 패턴·끝말로 추정해 임시로 넣는다.
  - arrangement    : 배열 세팅 JSON(숨김 위젯). 줄 → 청크 → 멤버(서브) 순서를 정한다.
                     JS 편집기(js/bmk_tag_organizer.js + js/tag_organizer/)가 쓰고, TagOrganizer 관리 툴에서 내보낸
                     파일을 가져올 수 있다. 비우면 layout. 형식은 TagOrganizer docs/arrangement.md.
  - dialect_priority : auto 의 우선순위. NAI 표기와 ComfyUI·Anima 표기가 함께 있을 때, 그리고 표기가 없을 때
                     앞의 문법을 쓴다. Anima 는 ComfyUI 의 상위 호환이라 @작가가 있으면 anima.
  - comments       : // 주석(줄 처음이거나 앞이 빈칸·쉼표일 때 줄 끝까지). remove = 뺌(removed 에 comment),
                     keep = 구간 맨 위에 한 줄씩. .hack//sign 처럼 // 가 든 태그는 주석이 아니다.
  - sentences      : 문장 끝 마침표(". ")를 쉼표처럼 나누고, 마침표로 끝나는 문장은 "자연어 문장" 줄에
                     입력 그대로 둔다. st. louis·monkey d. luffy 처럼 마침표가 든 태그는 그대로.

동작 세부:
  - 입력은 고치지 않고 정리한 새 문자열을 낸다. removed 출력에 뺀 태그와 이유가 줄마다 나온다.
  - DB에 없는 토큰(자연어·LoRA 트리거 등)과 아직 분류하지 않은 태그는 버리지 않고 마지막 줄에 둔다.
  - BREAK 로 나뉜 구간은 각각 따로 정리한다.
  - 태그 팩은 처음 실행할 때 한 번 읽고(약 1초) 파일이 바뀔 때까지 재사용한다.
    콤보 목록은 팩 옆 pack_info.json 에서 읽어 시작할 때 팩을 읽지 않는다.
  - 서버 경로(JS 편집기용): GET /bmk/tag_organizer/palette(분류·색·퀵 목록·레이아웃별 배열),
    POST /bmk/tag_organizer/preview(위젯 값 → 정리 결과와 청크 구조, 지금 고를 수 없는 시선·포즈, 시점 충돌이 풀렸는지).
    실행과 같은 옵션 함수를 쓴다.
  - 실행하면 입력 글을 ui(bmk_tag_input)로 돌려준다 — text 를 상류에 연결했을 때 편집기 미리보기용.

버전 이력:
  - v1: 최초 구현(표준 위젯, TagOrganizer M1 데이터).
  - v2: 코어 v1.2. 배열 세팅(arrangement)과 JS 청크 편집기, 크롭·시점 덮어쓰기, 추정(guess),
        편집기용 서버 경로. 새 입력은 optional 이라 v1 워크플로·API 프롬프트는 결과가 같다.
  - v3: 코어 v1.5. 입력 문법 auto 와 우선순위(dialect_priority), // 주석(comments), 마침표 문장(sentences).
        새 입력은 optional 맨 뒤라 v2 워크플로가 그대로 열리고, 빠진 값은 코어 기본값(주석 뺌·문장 나누기 켬)이다.
        편집기 미리보기에 자동 인식 결과(문법·근거·섞임)와 입력 문법 경고(warning_text)를 보인다.
  - v4: 청크 편집기 JS 를 TagOrganizer 의 모듈(js/tag_organizer/, sync 가 복사)로 합치고 js/bmk_tag_organizer.js 는
        ComfyUI 어댑터만 남겼다. 추정 규칙 메뉴 추가, 배열의 description·다른 툴 키 보존, 놓친 끌기 취소.
        노드 입력·서버 경로·저장 형식은 그대로라 v3 워크플로가 그대로 열린다.
  - v5: 코어 v1.6. 시선 입력(gaze)과 편집기 위의 시선 칩(여러 개 선택, 함께 못 쓰는 태그는 켜는 순간 꺼짐,
        크롭·시점이 막는 태그는 숨김). 구도 규칙이 크롭·시점과 함께 그릴 수 없는 시선을 프롬프트에서도 뺀다(1인 그림만).
        facing 태그는 이제 시선으로 분류된다. 새 입력은 optional 맨 뒤라 v4 워크플로가 그대로 열린다.
  - v6: 코어 v1.7. 시점(view)을 콤보에서 숨긴 STRING 위젯과 칩 줄로 바꿔 여러 개를 고른다(부딪치면 꺼지고 multiple views
        를 켜면 함께). 칩에 front view·three quarter view·multiple views 가 더해졌다. 위젯 이름·자리는 그대로이고
        콤보가 저장한 값 하나(from behind, auto 등)는 뜻이 같아 v5 워크플로·API 프롬프트가 그대로 돈다.
        편집기의 빈 줄(gap_after)과 앞면·뒷면 부위 규칙은 코어·팩·편집기 동기화로 들어온다.
  - v7: 코어 v1.8. facing 시선이면 눈이 떠 있어야 성립하는 태그(yellow eyes, tsurime 등)를 구도 규칙으로 뺀다.
        closed eyes, blindfold 와 눈썹은 남고, looking 시선을 함께 고르면 빼지 않는다. 노드 입력은 그대로라
        v6 워크플로가 그대로 열린다(facing 과 눈 태그를 함께 쓰던 워크플로만 결과가 달라진다. rules=off 면 그대로).
  - v8: 코어 v1.9. 포즈 입력(pose)과 편집기 위의 포즈 칩(여러 개 선택, 몸이 동시에 할 수 없는 자세는 켜는 순간 꺼짐,
        portrait·close-up 에서는 숨김). 장소·구속 태그는 프롬프트에 남는다. 구도 규칙이 portrait·close-up 과 함께 쓴 포즈
        칩의 태그를 프롬프트에서도 뺀다(1인 그림만). 새 입력은 optional 맨 뒤라 v7 워크플로가 그대로 열린다
        (portrait·close-up 과 포즈 태그를 함께 쓰던 워크플로만 결과가 달라진다. rules=off 면 그대로).
  - v9: 코어 v1.10. 시점·시선·포즈 칩에 그림을 붙이고(js/tag_organizer/glyphs.js, sync 가 복사), 시선은 방향 ×
        looking·facing 격자로, 포즈는 자세 묶음으로 놓는다(palette 의 gaze_grid·pose_groups). 정렬 결과와 노드 입력·
        저장 형식은 그대로라 v8 워크플로가 그대로 열린다. 칩 줄이 높아져 편집기 최소 높이를 520, 처음 높이를 640 으로 늘린다.
  - v10: 코어 v1.11. 퀵 세팅 그림(js/tag_organizer/stage.js·figure.js, sync 가 복사): 시점 줄의 그림 단추로 크롭 틀과
        방향·높이 다이얼을 켜고, 그림이 고른 값을 framing 콤보와 view 위젯에 쓴다(palette 의 framing_frame·view_dial,
        없으면 단추 없음). 켬 상태는 노드 properties 에 남는다. 노드 입력·저장 형식은 그대로라 v9 워크플로가 그대로 열리고,
        그림을 켜면 편집기 최소 높이가 140 늘어난다.
"""

from __future__ import annotations

import asyncio
import importlib
import itertools
import json
import logging
import os
import sys
from types import ModuleType
from typing import Any, Dict, List, Mapping

logger = logging.getLogger(__name__)
_TAG = "[ComfyUI_BMK_Nodes::TagOrganizer]"

_CORE_MODULE = "bmk_tag_organizer_core"
_PACK_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tag_organizer_data", "tagpack.json.gz")
_SYNC_HINT = "TagOrganizer 저장소에서 `python tagorg.py sync --to <이 노드 팩 폴더>` 를 실행하세요."

_RATINGS = ["e", "q", "s", "g"]
_RULE_MODES = ["remove", "report", "off"]
_DIALECTS = ["comfyui", "nai", "anima"]
_QUICK_GROUPS = ("framing",)   # 콤보로 고르는 퀵 그룹. 시점·시선·포즈는 칩이 palette 에서 받는다
_AUTO = "auto"
_UI_INPUT = "bmk_tag_input"
_COMMENT_MODES = ["remove", "keep"]
# auto 의 우선순위 콤보: 세 문법의 순서 6가지. 처음 = 코어 기본(DIALECT_PRIORITY). 코어는 "a > b > c" 를 그대로 읽는다
_DIALECT_PRIORITY = ("nai", "comfyui", "anima")
_PRIORITIES = [" > ".join(o) for o in (
    [_DIALECT_PRIORITY] + [o for o in itertools.permutations(_DIALECT_PRIORITY) if o != _DIALECT_PRIORITY])]

# 위젯 이름 → 기본값(INPUT_TYPES 와 같음). 미리보기 경로가 빠진 값을 채울 때 쓴다.
_DEFAULTS: Dict[str, Any] = {
    "preset": "sort_only",
    "exclude": "",
    "max_rating": "e",
    "rules": "remove",
    "layout": "default",
    "input_dialect": "comfyui",
    "output_dialect": "same",
    "line_per_section": True,
    "trailing_comma": True,
    "dedupe": True,
    "framing": _AUTO,
    "view": "",
    "guess": False,
    "arrangement": "",
    "dialect_priority": _PRIORITIES[0],
    "comments": "remove",
    "sentences": True,
    "gaze": "",
    "pose": "",
}


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


def _load_pack() -> Any:
    core = _core()
    path = _pack_path()
    if not os.path.isfile(path):
        raise RuntimeError(f"{_TAG} 태그 팩이 없습니다: {path}\n{_SYNC_HINT}")
    return core.load_pack(path)


def _as_labels(value: Any) -> Dict[str, str]:
    return {str(k): str(v) for k, v in value.items()} if isinstance(value, dict) else {}


def _as_quick(value: Any) -> Dict[str, List[str]]:
    if not isinstance(value, dict):
        return {}
    return {g: [str(t) for t in value.get(g) or () if str(t)] for g in _QUICK_GROUPS if isinstance(value.get(g), list)}


def _read_info() -> Dict[str, Any]:
    """콤보 목록(presets·layouts → 표시명, 크롭 퀵 태그). 실제로 쓸 팩 옆의 pack_info.json 을 읽고,
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
    labels: Dict[str, Any] = {key: _as_labels(info.get(key)) for key in ("presets", "layouts")}
    labels["quick"] = _as_quick(info.get("quick"))
    complete = labels["presets"] and labels["layouts"] and set(labels["quick"]) == set(_QUICK_GROUPS)
    if not complete and os.path.isfile(pack_path):
        try:
            pack = _core().load_pack(pack_path)
            labels = {
                "presets": {k: str(v.get("label", k)) for k, v in pack.presets.items()},
                "layouts": {k: str(v.get("label", k)) for k, v in pack.layouts.items()},
                "quick": _as_quick(_core().palette(pack)["quick"]),
            }
        except Exception:  # 콤보 목록 때문에 노드 정의가 깨지지 않게 기본값으로 둔다
            logger.warning("%s 팩에서 콤보 목록을 읽지 못했습니다: %s", _TAG, pack_path, exc_info=True)
    return labels


def _choices(labels: Dict[str, str], default: str) -> List[str]:
    return [default] + [k for k in labels if k != default]


def _tooltip(labels: Dict[str, str]) -> str:
    return "\n".join(f"  {k} = {v}" for k, v in labels.items())


def _options(core: ModuleType, values: Mapping[str, Any]) -> Any:
    """위젯 값 → core.Options. 노드 실행과 미리보기 경로가 같은 함수를 써서 결과가 같다."""
    v = {**_DEFAULTS, **values}
    return core.Options(
        preset=v["preset"],
        exclude=v["exclude"],
        max_rating=v["max_rating"],
        rules=v["rules"],
        layout=v["layout"],
        input_dialect=v["input_dialect"],
        output_dialect="" if v["output_dialect"] == "same" else v["output_dialect"],
        dialect_priority=v["dialect_priority"],
        comments=v["comments"],
        sentences=v["sentences"],
        line_per_section=v["line_per_section"],
        trailing_comma=v["trailing_comma"],
        dedupe=v["dedupe"],
        arrangement=v["arrangement"] or None,
        framing="" if v["framing"] == _AUTO else v["framing"],
        view="" if v["view"] == _AUTO else v["view"],
        gaze=v["gaze"],
        pose=v["pose"],
        guess=v["guess"],
    )


def _organize(text: str, values: Mapping[str, Any]) -> Any:
    core = _core()
    pack = _load_pack()
    try:
        return core.organize(text, pack, _options(core, values))
    except ValueError as e:
        raise ValueError(f"{_TAG} {e}") from e


# ─── 편집기용 서버 경로 ──────────────────────────────────────────


def _palette_payload() -> Dict[str, Any]:
    """분류·색·퀵 목록과 레이아웃별 시작 배열(관리 툴이 레이아웃에서 편집을 시작할 때와 같음)."""
    core = _core()
    pack = _load_pack()
    return {
        "pack_version": pack.version,
        "palette": core.palette(pack),
        "layouts": {lid: core.arrangement_from_layout(pack, lid) for lid in pack.layouts},
    }


def _request_values(body: Mapping[str, Any]) -> Dict[str, Any]:
    """미리보기 요청 본문 → 위젯 값. 모르는 키는 버리고, 형식이 다른 값은 기본값으로 둔다."""
    values: Dict[str, Any] = {}
    for key, default in _DEFAULTS.items():
        value = body.get(key, default)
        values[key] = value if isinstance(value, type(default)) else default
    return values


def _preview_payload(body: Mapping[str, Any]) -> Dict[str, Any]:
    text = body.get("text")
    result = _organize(text if isinstance(text, str) else "", _request_values(body))
    report = result.report
    core = _core()
    return {
        "text": result.text,
        "removed": result.removed_text,
        "arrangement": report.get("arrangement", ""),
        "counts": report["counts"],
        "arranged": report["arranged"],
        "tokens": report["tokens"],
        # 자동 인식 결과(input·output·auto, 자동이면 evidence·counts·conflict)와 입력 문법 경고(설명 글 포함)
        "dialect": report["dialect"],
        "warnings": [{**w, "text": core.warning_text(w["reason"])} for w in report["warnings"]],
        "gaze_blocked": report["gaze_blocked"],     # 시선 칩에서 숨길 태그(지금 크롭·시점과 맞지 않음)
        "pose_blocked": report["pose_blocked"],     # 포즈 칩에서 숨길 태그(지금 크롭과 맞지 않음)
        "view_lifted": report["view_lifted"],       # 프롬프트에 직접 쓴 형식 태그로 시점 칩의 충돌이 풀림
    }


def _register_routes() -> None:
    # 실행 중인 ComfyUI 의 PromptServer 만 쓴다. 단독 import·테스트면 server 모듈을 새로 불러오지 않는다
    server = getattr(getattr(sys.modules.get("server"), "PromptServer", None), "instance", None)
    routes = getattr(server, "routes", None)
    if routes is None or getattr(server, "_bmk_tag_organizer_routes", False):
        return
    try:
        from aiohttp import web
    except Exception:
        return
    server._bmk_tag_organizer_routes = True

    async def _run(fn, *args):
        # 팩을 처음 읽을 때 1초쯤 걸리므로 이벤트 루프를 막지 않는다
        return await asyncio.get_running_loop().run_in_executor(None, fn, *args)

    @routes.get("/bmk/tag_organizer/palette")
    async def bmk_tag_organizer_palette(_request):
        try:
            return web.json_response(await _run(_palette_payload))
        except Exception as e:
            logger.warning("%s palette 실패: %s", _TAG, e)
            return web.json_response({"error": str(e)}, status=500)

    @routes.post("/bmk/tag_organizer/preview")
    async def bmk_tag_organizer_preview(request):
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "잘못된 요청 본문"}, status=400)
        if not isinstance(body, dict):
            return web.json_response({"error": "잘못된 요청 본문"}, status=400)
        try:
            return web.json_response(await _run(_preview_payload, body))
        except ValueError as e:
            return web.json_response({"error": str(e).replace(_TAG + " ", "")}, status=400)
        except Exception as e:
            logger.warning("%s preview 실패: %s", _TAG, e)
            return web.json_response({"error": str(e)}, status=500)


_register_routes()


# ─── 노드 ─────────────────────────────────────────────────────────


class BMKTagOrganizer:
    TITLE = "BMK Tag Organizer"
    CATEGORY = "BMK/Text"
    FUNCTION = "organize"
    RETURN_TYPES = ("STRING", "STRING", "STRING")
    RETURN_NAMES = ("text", "removed", "report")
    DESCRIPTION = (
        "Danbooru 태그를 카테고리별 줄로 정렬하고, 프리셋·등급·구도 규칙으로 필요 없는 "
        "태그를 걸러냅니다. 청크 편집기로 줄·순서를 정한 배열 세팅을 쓸 수 있고, "
        "입력은 고치지 않고 정리한 새 문자열과 뺀 태그 목록을 출력합니다."
    )
    SEARCH_ALIASES = [
        "tag organizer",
        "tag sorter",
        "prompt sorter",
        "prompt organizer",
        "danbooru",
        "tag filter",
        "tag arrangement",
        "태그 정리",
        "태그 정렬",
        "태그 분류",
        "태그 필터",
        "태그 배열",
        "프롬프트 정리",
    ]

    @classmethod
    def INPUT_TYPES(cls):
        info = _read_info()
        presets = _choices(info["presets"], "sort_only")
        layouts = _choices(info["layouts"], "default")
        quick = info["quick"]
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
                    {
                        "default": "default",
                        "tooltip": "줄(섹션) 순서. 배열 세팅이 있으면 쓰지 않습니다.\n" + _tooltip(info["layouts"]),
                    },
                ),
                "input_dialect": (
                    _DIALECTS + [_AUTO],
                    {
                        "default": "comfyui",
                        "tooltip": (
                            "입력 프롬프트 문법. auto = 표기로 알아냄 — NAI 1.2::x ::·{x}, ComfyUI (x:1.2)·\\(·<lora:>,\n"
                            "Anima @작가. 섞였거나 없으면 dialect_priority 순서로. 결과는 편집기 상태 줄과 report.dialect\n"
                            "auto 에 output_dialect=same 이면 알아낸 문법으로 내므로, ComfyUI 인코더로 보낼 땐 출력 comfyui"
                        ),
                    },
                ),
                "output_dialect": (
                    ["same"] + _DIALECTS,
                    {
                        "default": "same",
                        "tooltip": (
                            "출력 문법. same = 입력과 같음(auto 면 알아낸 문법)\n"
                            "NAI 숫자 강조는 1.1::x :: 처럼 닫는 :: 앞을 띄웁니다(붙이면 x 끝의 숫자를 가중치로 읽음)"
                        ),
                    },
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
            },
            # v2 입력. optional 이고 맨 뒤라 v1 워크플로(widgets_values)·API 프롬프트가 그대로 돈다.
            "optional": {
                "framing": (
                    [_AUTO] + quick.get("framing", []),
                    {
                        "default": _AUTO,
                        "tooltip": (
                            "크롭 덮어쓰기. 프롬프트의 크롭 태그를 모두 빼고 이 태그를 넣어\n"
                            "구도 규칙이 바로 이 크롭으로 돕니다. auto = 프롬프트 그대로"
                        ),
                    },
                ),
                # v5 까지 콤보였다. 이름·자리가 같고 저장된 값 하나(from behind, auto 등)를 그대로 읽는다
                "view": (
                    "STRING",
                    {
                        "default": "",
                        "multiline": False,
                        "hidden": True,
                        "tooltip": "시점 덮어쓰기(쉼표로 여럿, 시점 칩이 씀). 방식은 framing 과 같음. 비우거나 auto = 프롬프트 그대로",
                    },
                ),
                "guess": (
                    "BOOLEAN",
                    {
                        "default": False,
                        "tooltip": (
                            "팩에 없는 토큰(holding chainsword, sharp crimson eyes 같은 구절)의 분류를\n"
                            "패턴·끝말로 추정해 임시로 넣습니다. 출력 글자는 입력 그대로입니다."
                        ),
                    },
                ),
                "arrangement": (
                    "STRING",
                    {
                        "default": "",
                        "multiline": False,
                        "hidden": True,
                        "tooltip": "배열 세팅 JSON(청크 편집기가 씀). 비우면 layout 을 씁니다.",
                    },
                ),
                # v3 입력. arrangement 뒤에 붙여 v2 워크플로의 widgets_values 가 밀리지 않는다
                "dialect_priority": (
                    _PRIORITIES,
                    {
                        "default": _PRIORITIES[0],
                        "tooltip": (
                            "input_dialect=auto 의 우선순위. NAI 표기와 ComfyUI·Anima 표기가 함께 있을 때,\n"
                            "그리고 표기가 없을 때 앞의 문법을 씁니다. Anima 는 ComfyUI 의 상위 호환이라\n"
                            "@작가가 있으면 Anima 입니다. auto 가 아니면 쓰지 않습니다."
                        ),
                    },
                ),
                "comments": (
                    _COMMENT_MODES,
                    {
                        "default": "remove",
                        "tooltip": (
                            "// 부터 줄 끝까지의 주석(줄 처음이거나 앞이 빈칸·쉼표일 때)\n"
                            "remove = 뺌(removed 에 comment), keep = 구간 맨 위에 한 줄씩\n"
                            "(같은 줄에 두면 뒤 태그까지 주석이 됨). .hack//sign 같은 태그는 주석이 아닙니다."
                        ),
                    },
                ),
                "sentences": (
                    "BOOLEAN",
                    {
                        "default": True,
                        "tooltip": (
                            "문장 끝 마침표(. 뒤 빈칸)를 쉼표처럼 나누고, 마침표로 끝나는 문장은 '자연어 문장' 줄에\n"
                            "입력 그대로 둡니다. st. louis·monkey d. luffy 처럼 마침표가 든 태그는 그대로입니다."
                        ),
                    },
                ),
                # v5 입력. 맨 뒤에 붙여 v4 워크플로의 widgets_values 가 밀리지 않는다
                "gaze": (
                    "STRING",
                    {
                        "default": "",
                        "multiline": False,
                        "hidden": True,
                        "tooltip": "시선 덮어쓰기(쉼표로 여럿, 시선 칩이 씀). 크롭·시점이 막는 태그는 넣지 않습니다. 비우면 프롬프트 그대로",
                    },
                ),
                # v8 입력. 맨 뒤에 붙여 v7 워크플로의 widgets_values 가 밀리지 않는다
                "pose": (
                    "STRING",
                    {
                        "default": "",
                        "multiline": False,
                        "hidden": True,
                        "tooltip": "포즈 덮어쓰기(쉼표로 여럿, 포즈 칩이 씀). 장소·구속 태그는 남고 크롭이 막는 태그는 넣지 않습니다. 비우면 프롬프트 그대로",
                    },
                ),
            },
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
        framing: str = _AUTO,
        view: str = "",
        guess: bool = False,
        arrangement: str = "",
        dialect_priority: str = _PRIORITIES[0],
        comments: str = "remove",
        sentences: bool = True,
        gaze: str = "",
        pose: str = "",
    ):
        result = _organize(
            text,
            {
                "preset": preset,
                "exclude": exclude,
                "max_rating": max_rating,
                "rules": rules,
                "layout": layout,
                "input_dialect": input_dialect,
                "output_dialect": output_dialect,
                "line_per_section": line_per_section,
                "trailing_comma": trailing_comma,
                "dedupe": dedupe,
                "framing": framing,
                "view": view,
                "guess": guess,
                "arrangement": arrangement,
                "dialect_priority": dialect_priority,
                "comments": comments,
                "sentences": sentences,
                "gaze": gaze,
                "pose": pose,
            },
        )
        counts = result.report["counts"]
        logger.debug(
            "%s pack=%s total=%d removed=%d unknown=%d unclassified=%d arrangement=%s dialect=%s warnings=%d",
            _TAG, result.report["pack_version"], counts["total"], counts["removed"], counts["unknown"],
            counts["unclassified"], result.report.get("arrangement") or "-", result.report["dialect"]["input"],
            counts["warnings"],
        )
        return {
            "ui": {_UI_INPUT: [text]},
            "result": (result.text, result.removed_text, json.dumps(result.report, ensure_ascii=False, indent=2)),
        }


NODE_CLASS_MAPPINGS = {
    "BMKTagOrganizer": BMKTagOrganizer,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "BMKTagOrganizer": "BMK Tag Organizer",
}
