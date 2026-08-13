"""ComfyUI_BMK_Nodes — 통합 로더 + 패키지 규약(정본).

이 파일이 패키지의 유일한 규약 정본입니다. 새 노드 제작·수정을 요청할 때
(사람이든 LLM이든) 이 파일 하나만 첨부하면 아래 규약에 맞춰 작업할 수 있도록
필요한 규칙을 전부 여기에 담습니다. 규약을 바꾸면 이 docstring 을 갱신하세요.

════════════════════════════════════════════════════════════════
패키지 규약 v2  (2026-07 — templete.txt 대체)
════════════════════════════════════════════════════════════════

1. 파일 / 모듈 구성
   - 1 기능 = 1 모듈(.py). 파일명은 bmk_<snake_case>.py
     (보조 클래스는 같은 모듈에 함께 두어도 됨. 예: bmk_flexible_tile_segs)
   - 모듈 최상단 docstring: 첫 줄 "BMK <노드명>" + 한 줄 요약,
     이어서 배경 / 옵션 설명 / 버전 이력(v2, v3 …)을 한국어로 서술.
   - NODE_CLASS_MAPPINGS / NODE_DISPLAY_NAME_MAPPINGS 는 파일 맨 아래.
   - 완성 후 이 파일의 _NODE_MODULES 에 모듈명 추가(알파벳 순).

2. 노드 클래스 속성
   - 클래스명(=노드 ID, 매핑 키):  BMK<PascalCase>        예) BMKTabbedNotes
   - 표시 이름:                    "BMK <Title Case>"      (필요 시 이모지 접미)
   - CATEGORY:                     "BMK/<Sub>"
       현재 사용 중인 Sub: SEGS, Anima, Image, Text, Utils
       → 새 Sub 를 만들기 전에 기존 것 재사용을 먼저 검토.
   - DESCRIPTION (필수):           한국어 1~3문장. 프론트엔드 툴팁에 노출되고
                                   노드 검색에도 활용됨.
   - SEARCH_ALIASES (권장):        기능 동의어 + 한국어 용어 리스트.
       예) ["tabbed notes", "memo", "메모", "탭 노트"]
       ※ "bmk" 자체는 넣지 않음 — 표시 이름 접두어로 이미 검색됨.

3. 코드 스타일 (기존 모듈들과 통일)
   - from __future__ import annotations 로 시작.
   - 로깅: logger = logging.getLogger(__name__)
           _TAG = "[ComfyUI_BMK_Nodes::<NodeName>]"  접두 패턴 사용.
   - 외부 파일/상태에 의존하는 노드는 IS_CHANGED 를 정의하되,
     "출력에 영향을 주는 값만" 해시할 것 (불필요한 캐시 무효화 방지).
   - 다른 커스텀팩(Impact Pack 등) 의존 import 는 try/except 로 격리하고,
     실패 시 명확한 에러 메시지를 남길 것.

4. JS 프론트엔드 확장이 필요한 경우
   - 특정 BMK 노드와 짝이면 ./js/<모듈명과 동일>.js 로 명명.
     (독립 패치성 확장은 자유 이름. 예: wildcard_resize_patch.js)
   - 아래 "JS 확장 노출" 주석 블록에 한 줄 설명 추가.

5. 로더 동작 (이 파일이 보장하는 것)
   - 모듈 import 실패는 격리 — 다른 노드 로딩에 영향 없음.
   - 규약 위반은 시작 로그에 경고만 출력, 로딩은 차단하지 않음.
     (BMK_CONVENTION_CHECK=0 으로 점검 비활성화 가능)
   - 노드 ID 중복만은 즉시 RuntimeError.
   - _LEGACY_MODULES 목록은 규약 점검에서 제외
     (BMK 접두어 도입 이전에 만든 노드들 — 점진적으로 이관).
════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import importlib
import os
import traceback
from typing import Dict, List, Type


NODE_CLASS_MAPPINGS: Dict[str, Type] = {}
NODE_DISPLAY_NAME_MAPPINGS: Dict[str, str] = {}


# 새 노드 모듈은 여기에 추가 (알파벳 순).
# 각 모듈은 NODE_CLASS_MAPPINGS / NODE_DISPLAY_NAME_MAPPINGS 를 노출해야 함.
_NODE_MODULES = (
    "bmk_anima_lllite_segs_hook",
    "bmk_context_anima",
    "bmk_crop_stitch",
    "bmk_cyclic_seed",
    "bmk_flexible_tile_segs",
    "bmk_klein_reference_segs_hook",
    "bmk_load_image_crop",
    "bmk_run_batch_grid",
    "bmk_run_cycle",
    "bmk_segs_core_mask",
    "bmk_tabbed_notes",
    "bmk_tag_subtractor",
    "bmk_upscale_with_model_tiled",
    "bmk_wavelet_tone_restore",
    "bmk_wildcard_prompt",
    # ── legacy (BMK 접두어 규약 이전) ──
    "novelai_metadata",
    "prompt_converter",
    "xy_plot",
)

# 규약 점검 제외 대상 (이관 완료 시 여기서 제거)
_LEGACY_MODULES = frozenset(
    {
        "novelai_metadata",
        "prompt_converter",
        "xy_plot",
    }
)

_convention_warnings: List[str] = []


def _check_conventions(
    module_name: str,
    class_mappings: Dict[str, Type],
    display_mappings: Dict[str, str],
) -> None:
    """규약 v2 준수 여부를 점검하고 위반 사항을 경고 목록에 수집.

    로딩을 절대 차단하지 않는다 — 시작 로그에서 드리프트를 눈에 띄게
    만드는 것이 목적. (BMK_CONVENTION_CHECK=0 으로 비활성화)
    """
    for node_id, cls in class_mappings.items():
        where = f"{module_name}.{node_id}"

        if not node_id.startswith("BMK"):
            _convention_warnings.append(
                f'{where}: 노드 ID가 "BMK" 접두어가 아님'
            )

        category = getattr(cls, "CATEGORY", "")
        if not str(category).startswith("BMK/"):
            _convention_warnings.append(
                f'{where}: CATEGORY "{category}" → 권장 "BMK/<Sub>"'
            )

        display = display_mappings.get(node_id, "")
        if display and not display.startswith("BMK "):
            _convention_warnings.append(
                f'{where}: 표시 이름 "{display}" → 권장 "BMK <Title Case>"'
            )

        if not getattr(cls, "DESCRIPTION", None):
            _convention_warnings.append(f"{where}: DESCRIPTION 누락(필수)")

        if not getattr(cls, "SEARCH_ALIASES", None):
            _convention_warnings.append(f"{where}: SEARCH_ALIASES 누락(권장)")


def _register_nodes_from_module(module_name: str) -> None:
    try:
        module = importlib.import_module(f".{module_name}", package=__name__)
    except Exception:
        print(f"[ComfyUI_BMK_Nodes] Failed to import module: {module_name}")
        traceback.print_exc()
        return

    class_mappings = getattr(module, "NODE_CLASS_MAPPINGS", {})
    display_mappings = getattr(module, "NODE_DISPLAY_NAME_MAPPINGS", {})

    if not isinstance(class_mappings, dict):
        print(
            f"[ComfyUI_BMK_Nodes] Ignored invalid NODE_CLASS_MAPPINGS "
            f"from module: {module_name}"
        )
        return

    if not isinstance(display_mappings, dict):
        print(
            f"[ComfyUI_BMK_Nodes] Ignored invalid NODE_DISPLAY_NAME_MAPPINGS "
            f"from module: {module_name}"
        )
        display_mappings = {}

    duplicate_ids = set(NODE_CLASS_MAPPINGS).intersection(class_mappings)
    if duplicate_ids:
        raise RuntimeError(
            "[ComfyUI_BMK_Nodes] Duplicate node class id(s): "
            + ", ".join(sorted(duplicate_ids))
        )

    if (
        module_name not in _LEGACY_MODULES
        and os.environ.get("BMK_CONVENTION_CHECK", "1") != "0"
    ):
        _check_conventions(module_name, class_mappings, display_mappings)

    NODE_CLASS_MAPPINGS.update(class_mappings)
    NODE_DISPLAY_NAME_MAPPINGS.update(display_mappings)


for _module_name in _NODE_MODULES:
    _register_nodes_from_module(_module_name)


# ─── JS 확장 노출 ───────────────────────────────────────────────
# ./js 폴더 내의 모든 *.js 파일이 ComfyUI 프론트엔드에 자동 로드됩니다.
# 현재 포함된 JS 확장:
#   - wildcard_resize_patch.js
#       Impact Pack의 ImpactWildcardProcessor / ImpactWildcardEncode 노드의
#       두 텍스트 영역(wildcard_text, populated_text) 사이 높이 비율을
#       슬라이더로 조절할 수 있게 해주는 프론트엔드 전용 패치.
#   - bmk_load_image_crop.js
#       BMKLoadImageCrop 노드용 확장. 종횡비 고정 드래그 크롭 + 90도 회전
#       편집 다이얼로그(스냅/수치 입력/설정 유지), 노드 내 원본↔크롭 프리뷰
#       토글, Reset Crop 버튼, 위젯 순서 정렬을 제공. 결과는 노드 위젯
#       (rotation/crop_*)에만 기록됩니다(비파괴적).
#       Free 모드는 극단 AR(1:4~4:1 초과)을 자동 클램프.
#   - node_badge_scale_patch.js
#       노드 상단 오버레이(ID/출처 뱃지 + 실행시간 표시)의 렌더링 크기를 설정
#       슬라이더(10~100%)로 일괄 축소하는 프론트엔드 전용 패치. 밀집 배치 시
#       위쪽 노드를 가리는 문제 완화. drawBadges / drawNode 래핑, 렌더링 외
#       영향 없음. 오버레이 적용 범위는 auto/all/off 설정으로 조절.
#   - bmk_context_anima.js
#       BMKContextAnima용 확장. 출력 라벨을 공백으로 바꿔 노드 가로폭을
#       절반 수준으로 축소하고, properties["bmk_ctx_schema"]에 스키마
#       버전을 각인 (훗날 포트 재배열 마이그레이션 기준값).
# 새 JS 확장을 추가할 때는 ./js 폴더에 파일만 넣으면 됩니다.
WEB_DIRECTORY = "./js"


# ─── 와일드카드 자동 리로드 ──────────────────────────────────────
# Impact Pack 와일드카드 파일(txt/yaml)을 감시하여, 변경 시 자동으로
# wildcard_load()를 호출합니다("Impact: Refresh Wildcard" 버튼과 동일).
# 부가 기능이므로 초기화 실패가 노드 등록에 영향을 주지 않도록 격리하고,
# 환경변수 BMK_WILDCARD_AUTORELOAD=0 으로 비활성화할 수 있습니다.
if os.environ.get("BMK_WILDCARD_AUTORELOAD", "1") != "0":
    try:
        from . import bmk_wildcard_autoreload

        bmk_wildcard_autoreload.start(interval=1.0)
    except Exception:
        print(
            "[ComfyUI_BMK_Nodes] Failed to start wildcard autoreload "
            "(node loading unaffected)."
        )
        traceback.print_exc()


# ─── 로드 결과 안내 로그 ─────────────────────────────────────────
print(
    f"[ComfyUI_BMK_Nodes] Loaded {len(NODE_CLASS_MAPPINGS)} node(s): "
    + ", ".join(sorted(NODE_CLASS_MAPPINGS.keys()))
)

_js_dir = os.path.join(os.path.dirname(__file__), "js")
if os.path.isdir(_js_dir):
    _js_files = sorted(f for f in os.listdir(_js_dir) if f.endswith(".js"))
    if _js_files:
        print(
            f"[ComfyUI_BMK_Nodes] Loaded {len(_js_files)} JS extension(s): "
            + ", ".join(_js_files)
        )

if _convention_warnings:
    print(
        f"[ComfyUI_BMK_Nodes] 규약 점검: 경고 {len(_convention_warnings)}건 "
        "(로딩에는 영향 없음, BMK_CONVENTION_CHECK=0 으로 끌 수 있음)"
    )
    for _w in _convention_warnings:
        print(f"[ComfyUI_BMK_Nodes]   - {_w}")


__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
