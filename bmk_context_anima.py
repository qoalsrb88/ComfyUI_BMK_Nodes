"""BMK Context Anima — Anima 워크플로우 전용 컨텍스트 허브 노드.

rgthree Context Big을 대체하는 Anima 전용 컨텍스트. subgraph 안으로 컨텍스트
라인 하나만 넘겨 내부 배선을 깔끔하게 유지하는 것이 목적. 범용성 대신 Anima
워크플로우(모델 3단 라인, 분해 프롬프트, 업스케일/디테일 파라미터, 단계별
이미지 슬롯)에 최적화되어 있다.

동작 규칙 (rgthree new_context와 동일):
  - 각 입력이 연결되어 있으면 그 값이 컨텍스트에 기록되고, 비어 있으면
    base_ctx에서 상속, base_ctx에도 없으면 None.
  - base_ctx의 "모르는 키"도 보존한다 — 미래 스키마의 컨텍스트가 이 버전의
    노드를 통과해도 정보가 유실되지 않는다 (rgthree가 못 하는 부분).
  - 출력은 [CONTEXT, 이후 키 순서대로] — 입력 행과 출력 행이 1:1 대응.

포트 순서 규약 (중요):
  - 링크는 슬롯 인덱스로 저장되므로 포트 순서 변경은 기존 워크플로우를
    파괴한다. 신규 포트는 반드시 _CTX_ENTRIES 맨 끝에만 추가(append-only).
  - 재배열이 불가피해지면 SCHEMA_VERSION을 올리고, JS 쪽에서
    properties["bmk_ctx_schema"] 기준 링크 리매핑 마이그레이션을 함께 작성.

컨텍스트 타입은 BMK_CTX_ANIMA로 독립 — rgthree RGTHREE_CONTEXT와 직접
연결되지 않는다 (변형 컨텍스트 간 오연결을 타입 시스템이 차단).
Flux2Klein / Krea2 변형이 실제로 필요해지면 _CTX_ENTRIES 테이블만 교체한
클래스를 추가하고, 그 시점에 공용 베이스로 리팩토링한다.

짝 JS: js/bmk_context_anima.js
  - 출력 라벨을 공백으로 바꿔 노드 가로폭을 절반 수준으로 축소 (cosmetic 전용)
  - properties["bmk_ctx_schema"] 스키마 버전 각인

주의: 이 노드를 XY Plot 체인 리플레이가 통과하려면 xy_plot.py의
_CONTEXT_PASSTHROUGH_TYPES에 "BMKContextAnima"를 추가해야 한다.

버전 이력:
  v1   (2026-07): 최초 작성. 39포트, append-only 규약, 스키마 버전 1.
  v1.1 (2026-07): lora_triggers / lora_tags 포트를 wildcard 뒤에 삽입 (41포트).
                  릴리스 전 파괴적 변경 — 이후로는 append-only 엄수.
                  스키마 버전 1 유지 (배포된 워크플로우 없음).
  v1.2 (2026-07): LLLite 파라미터 6종(str/end × 기본·ups·detail)을 맨 끝에
                  append (47포트). 첫 append-only 적용 사례 — 기존 링크
                  인덱스 불변. _SCHEMA_LOCK (append-only 가드) 도입.
  v1.3 (2026-07): img_preups 를 맨 끝에 append (48포트). 업스케일러 모델로
                  해상도만 확장한 중간 이미지 — img_basic/img_input 에서
                  받아 img_ups / img_detail 두 갈래로 분기하고 이후
                  img_merge 로 합쳐지는 파이프라인의 분기점.
                  개념상 img_basic 뒤가 자연스럽지만 append-only 규약에
                  따라 테이블 끝에 배치 (표시 순서 < 링크 안정성).
  v1.4 (2026-08): img_tag / use_tagger 를 맨 끝에 append (50포트).
                  img_tag 는 태거(WD Timm Tagger) 전용 축소본 슬롯으로,
                  목표 해상도 체인과 의도적으로 분리했다 — 겸용하면 업스케일
                  배율을 바꿀 때마다 태거가 재실행되어 프롬프트·컨디셔닝
                  체인 전체의 캐시가 무효화된다. 분리하면 크롭 이미지가
                  바뀔 때만 태거가 돈다.
                  use_tagger 는 태그 추출 트리(태거 + Exclude Tag Set 전체)를
                  전이적으로 게이팅하는 불리언.
  v1.5 (2026-08): skip_basic 을 맨 끝에 append (51포트). 기본 샘플링을
                  건너뛰고 img_input 을 그대로 img_basic 으로 통과시키는
                  I2I 전용 토글.
                  Group_01 로컬 불리언이던 것을 컨텍스트로 올린 이유는,
                  이 값이 Group_01 밖에서 is_i2i 와 AND 되어야 하는 자리가
                  이미 여러 곳이기 때문이다:
                    - 00-2 의 latent 쓰기 게이팅 (샘플링을 건너뛰면
                      VAEEncode 결과가 소비되지 않는데, 컨텍스트 LATENT
                      입력은 지연 평가가 아니라 무조건 해결된다)
                    - Save 단계 메타데이터에 "샘플링을 실제로 했는지" 기록
                    - 프리 업스케일 단계의 소스 판정
                  T2I 에서는 무시된다 — 판정은 노드가 아니라 Group_01 의
                  중첩 LazySwitch(바깥 switch = is_i2i)가 담당하며, 이
                  슬롯은 값 전달만 한다.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

import comfy.samplers

logger = logging.getLogger(__name__)
_TAG = "[ComfyUI_BMK_Nodes::ContextAnima]"

# 독립 컨텍스트 타입 — 변형(Flux2Klein/Krea2)은 각자 다른 타입을 쓴다.
CTX_TYPE = "BMK_CTX_ANIMA"

# 스키마 버전 — 포트 구성이 append 이외의 방식으로 바뀔 때만 올린다.
# js/bmk_context_anima.js 의 SCHEMA_VERSION과 항상 일치시킬 것.
SCHEMA_VERSION = 1

_SAMPLERS = comfy.samplers.KSampler.SAMPLERS
_SCHEDULERS = comfy.samplers.KSampler.SCHEDULERS


# ─── 단일 정본: 컨텍스트 키 테이블 ─────────────────────────────
# (key, comfy_type, tooltip)
#   입력 이름 = key (snake_case), 출력 이름 = key.upper()
#   ※ 이 lower/upper 1:1 대응은 xy_plot.py의 _resolve_external_value가
#     slot_name.lower()로 입력을 찾는 규칙과 맞물려 있으므로 유지할 것.
#   ※ append-only — 중간 삽입/삭제/재배열 금지 (모듈 docstring 참고)
_CTX_ENTRIES: Tuple[Tuple[str, Any, str], ...] = (
    ("base_ctx",       CTX_TYPE,        "상위 BMK Context Anima 컨텍스트 (체이닝용)"),
    ("model_raw",      "MODEL",         "순정 모델 (LoRA/패치 적용 전)"),
    ("model_lora",     "MODEL",         "모델 + LoRA 적용 라인"),
    ("model",          "MODEL",         "최종 모델 (모델+LoRA+패칭). 다운스트림 기본 사용 라인"),
    ("clip",           "CLIP",          ""),
    ("vae",            "VAE",           ""),
    ("positive",       "CONDITIONING",  ""),
    ("negative",       "CONDITIONING",  ""),
    ("pos_all",        "STRING",        "전체 positive 프롬프트"),
    ("pos_artist",     "STRING",        "아티스트 태그만 분리한 프롬프트 (Artist Mixer용)"),
    ("pos_base",       "STRING",        "아티스트 태그를 제외한 베이스 프롬프트 (Artist Mixer용)"),
    ("pos_pre",        "STRING",        "퀄리티~객체 프리픽스 프롬프트"),
    ("pos_post",       "STRING",        "포스트픽스 프롬프트"),
    ("neg_all",        "STRING",        "전체 negative 프롬프트"),
    ("wildcard",       "STRING",        "와일드카드 프롬프트"),
    ("lora_triggers",  "STRING",        "LoRA 트리거 워드 문자열 (LoraManager trigger_words)"),
    ("lora_tags",      "STRING",        "적용된 LoRA 태그 문자열 <lora:이름:강도> (LoraManager loaded_loras)"),
    ("latent",         "LATENT",        ""),
    ("seed",           "INT",           ""),
    ("steps",          "INT",           ""),
    ("steps_r",        "INT",           "리파이너 단계 steps (구 step_refiner)"),
    ("cfg",            "FLOAT",         ""),
    ("shift",          "FLOAT",         "ModelSamplingAuraFlow shift"),
    ("cfg_norm",       "FLOAT",         "CFGNorm strength"),
    ("denoise",        "FLOAT",         "기본 샘플링 denoise"),
    ("denoise_ups",    "FLOAT",         "업스케일 단계 denoise"),
    ("denoise_detail", "FLOAT",         "디테일러 단계 denoise"),
    ("sampler",        _SAMPLERS,       "샘플러 이름 (KSampler/Detailer combo 호환)"),
    ("scheduler",      _SCHEDULERS,     "스케줄러 이름 (KSampler/Detailer combo 호환)"),
    ("is_i2i",         "BOOLEAN",       "True=Image to Image, False=Text to Image"),
    ("upscaler",       "UPSCALE_MODEL", "업스케일러 모델"),
    ("upscale_by",     "FLOAT",         "업스케일 배율"),
    ("divide_by",      "INT",           "분할 값 (INT)"),
    ("img_input",      "IMAGE",         "i2i 입력 이미지"),
    ("img_basic",      "IMAGE",         "기본 샘플링 결과 이미지"),
    ("img_ups",        "IMAGE",         "업스케일 결과 이미지"),
    ("img_detail",     "IMAGE",         "디테일러 결과 이미지"),
    ("img_merge",      "IMAGE",         "업스케일+디테일 병합 결과 이미지"),
    ("img_post",       "IMAGE",         "포스트프로세싱 최종 결과 이미지"),
    ("mask",           "MASK",          ""),
    ("control_net",    "CONTROL_NET",   ""),
    # ── v1.2 append ──
    ("lllite_str",        "FLOAT",      "LLLite strength — 기본 샘플링 단계"),
    ("lllite_str_ups",    "FLOAT",      "LLLite strength — 업스케일 단계"),
    ("lllite_str_detail", "FLOAT",      "LLLite strength — 디테일러 단계"),
    ("lllite_end",        "FLOAT",      "LLLite end_percent — 기본 샘플링 단계 (start=0.0 고정 전제)"),
    ("lllite_end_ups",    "FLOAT",      "LLLite end_percent — 업스케일 단계"),
    ("lllite_end_detail", "FLOAT",      "LLLite end_percent — 디테일러 단계"),
    # ── v1.3 append ──
    ("img_preups",     "IMAGE",         "업스케일러 모델로 해상도만 확장한 이미지 "
                                        "(basic/input → preups → ups·detail 분기 전)"),
    # ── v1.4 append ──
    ("img_tag",        "IMAGE",         "태그 추출(WD Timm Tagger) 전용 소스 "
                                        "— 목표 해상도와 무관한 축소본"),
    ("use_tagger",     "BOOLEAN",       "태그 추출 사용 여부 (T2I/I2I 공통)"),
    # ── v1.5 append ──
    ("skip_basic",     "BOOLEAN",       "기본 샘플링 건너뛰기 — True면 img_input을 "
                                        "그대로 img_basic으로 통과 (I2I 전용, T2I에서는 무시)"),
)

# base_ctx를 제외한 데이터 키 (컨텍스트 dict의 실제 키 집합)
_KEYS_NO_BASE: Tuple[str, ...] = tuple(
    k for k, _t, _tt in _CTX_ENTRIES if k != "base_ctx"
)


# ─── 스키마 잠금 (append-only 가드) ─────────────────────────────
# 실사용에 들어간 키 순서의 스냅샷. _CTX_ENTRIES 는 반드시 이 목록으로
# "시작"해야 한다 — 뒤에 붙이는 것만 허용. import 시점에 위반을 감지해
# 경고를 남긴다 (패키지 규약대로 로딩은 차단하지 않음).
#
# 유지 규칙:
#   - 새 포트를 append 했다면, 실사용 워크플로우에 반영된 뒤 이 목록
#     끝에도 같은 키를 추가해 잠근다 (깜빡해도 무해 — 검사는 선두
#     일치만 보므로 잘못된 경고는 나지 않는다).
#   - 중간 삽입/재배열이 정말 필요하면: SCHEMA_VERSION 승격 + JS 링크
#     마이그레이션 작성 + 이 목록 재작성이 한 세트다.
_SCHEMA_LOCK: Tuple[str, ...] = (
    "base_ctx",
    "model_raw", "model_lora", "model",
    "clip", "vae",
    "positive", "negative",
    "pos_all", "pos_artist", "pos_base", "pos_pre", "pos_post",
    "neg_all", "wildcard",
    "lora_triggers", "lora_tags",
    "latent",
    "seed", "steps", "steps_r",
    "cfg", "shift", "cfg_norm",
    "denoise", "denoise_ups", "denoise_detail",
    "sampler", "scheduler",
    "is_i2i",
    "upscaler", "upscale_by", "divide_by",
    "img_input", "img_basic", "img_ups", "img_detail", "img_merge", "img_post",
    "mask", "control_net",
    # v1.2
    "lllite_str", "lllite_str_ups", "lllite_str_detail",
    "lllite_end", "lllite_end_ups", "lllite_end_detail",
    # v1.3
    "img_preups",
    # v1.4
    "img_tag",
    "use_tagger",
    # v1.5
    "skip_basic",
)


def _check_schema_lock() -> None:
    current = tuple(k for k, _t, _tt in _CTX_ENTRIES)
    if current[: len(_SCHEMA_LOCK)] != _SCHEMA_LOCK:
        logger.warning(
            "%s append-only 위반 감지: _CTX_ENTRIES 선두가 스키마 잠금 목록과 "
            "다릅니다. 기존 워크플로우의 링크가 어긋날 수 있습니다 "
            "(재배열이 의도라면 SCHEMA_VERSION 승격 + JS 마이그레이션 + "
            "_SCHEMA_LOCK 재작성이 필요).",
            _TAG,
        )


_check_schema_lock()


def _merge_context(
    base_ctx: Optional[Dict[str, Any]],
    overrides: Dict[str, Any],
) -> Dict[str, Any]:
    """base_ctx 위에 연결된 입력 값을 덮어쓴 새 컨텍스트 dict를 만든다.

    rgthree new_context와 동일 규칙: 입력이 None이 아니면 그 값, None이면
    base_ctx에서 상속, 둘 다 없으면 None. base_ctx의 알 수 없는 키도
    보존한다 (미래 스키마 컨텍스트가 구버전 노드를 통과해도 무손실).
    """
    if base_ctx is not None and not isinstance(base_ctx, dict):
        logger.warning(
            "%s base_ctx is not a dict (%s); treating as empty.",
            _TAG, type(base_ctx).__name__,
        )
        base_ctx = None

    ctx: Dict[str, Any] = dict(base_ctx) if base_ctx else {}
    for key in _KEYS_NO_BASE:
        value = overrides.get(key)
        if value is not None:
            ctx[key] = value
        elif key not in ctx:
            ctx[key] = None
    return ctx


class BMKContextAnima:
    TITLE = "BMK Context Anima"
    CATEGORY = "BMK/Anima"
    FUNCTION = "convert"

    RETURN_TYPES = tuple(t for _k, t, _tt in _CTX_ENTRIES)
    RETURN_NAMES = ("CONTEXT",) + tuple(k.upper() for k in _KEYS_NO_BASE)
    OUTPUT_TOOLTIPS = tuple(tt for _k, _t, tt in _CTX_ENTRIES)

    DESCRIPTION = (
        "rgthree Context Big을 대체하는 Anima 워크플로우 전용 컨텍스트 허브. "
        "모델 3단 라인(raw/lora/최종), 분해 프롬프트, 업스케일·디테일 파라미터, "
        "단계별 이미지 슬롯을 하나의 컨텍스트로 묶어 subgraph 배선을 단순화합니다."
    )
    SEARCH_ALIASES = [
        "context", "context anima", "context big", "ctx", "pipe", "hub",
        "컨텍스트", "아니마 컨텍스트", "파이프", "허브",
    ]

    @classmethod
    def INPUT_TYPES(cls):
        optional: Dict[str, Any] = {}
        for key, ctype, tooltip in _CTX_ENTRIES:
            # forceInput: INT/FLOAT/BOOLEAN/STRING/combo가 위젯이 아니라
            # 입력 소켓으로 생성되도록 강제. 소켓 전용 타입에는 무해.
            opts: Dict[str, Any] = {"forceInput": True}
            if tooltip:
                opts["tooltip"] = tooltip
            optional[key] = (ctype, opts)
        return {"required": {}, "optional": optional}

    def convert(self, **kwargs):
        base_ctx = kwargs.pop("base_ctx", None)
        ctx = _merge_context(base_ctx, kwargs)
        return (ctx,) + tuple(ctx.get(k) for k in _KEYS_NO_BASE)


NODE_CLASS_MAPPINGS = {
    "BMKContextAnima": BMKContextAnima,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "BMKContextAnima": "BMK Context Anima",
}
