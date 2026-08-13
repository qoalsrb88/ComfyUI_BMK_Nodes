"""BMK Anima LLLite Per-SEGS Hook  (v5)

Detailer (SEGS) 의 ``detailer_hook`` 입력에 연결하여, 세그먼트(타일)마다
① 서로 다른 컨트롤 이미지를 Anima ControlNet-LLLite 로 적용하고,
② (선택) [LAB] 타일 프롬프트를 Artist Mixer 방식으로 인코딩해 positive
   conditioning 을 타일별로 교체하는 노드.

배경
----
stock 'Apply Anima ControlNet-LLLite' (ComfyUI-Anima-LLLite/nodes.py) 는 컨트롤
이미지를 ``set_model_unet_function_wrapper`` 의 클로저에 단 한 번 캡처(src_image)
하기 때문에, Detailer (SEGS) 가 세그먼트를 순차 처리할 때 모든 세그먼트가 동일한
컨트롤 이미지를 공유한다. (배치를 넣어도 forward 의 배치/시퀀스 정합성 가드 때문에
첫 이미지만 먹거나 identity 로 빠진다.)

이 노드는 Impact Pack 의 훅에서 세그먼트마다 LLLite 래퍼를 새로 설치하여,
각 타일이 자기 자신의 크롭(또는 사용자가 지정한 IMAGE 리스트)을 컨트롤로
사용하도록 만든다. 래퍼 본체 로직은 원 노드와 동일하다(해상도별 cond 리사이즈,
스텝 범위 게이트, apply/restore 스코핑).

v2 변경 (라이브 crop_region 매칭 — virtual_canvas 대응)
-------------------------------------------------------
v1 은 "몇 번째 pre_ksample 호출인가"를 세는 내부 카운터로 타일↔컨트롤을
매칭했다. 이 방식은 다음 상황에서 조용히 어긋난다:

  * Detailer 가 세그먼트를 스킵할 때(force_inpaint off 등) → 이후 전부 한 칸 밀림
  * ComfyUI 노드 캐시로 훅 객체가 다음 실행에 재사용될 때, 직전 실행이
    중간에 취소(interrupt)되었으면 카운터가 어긋난 채 유지 → 이후 모든 실행에서
    모든 타일의 컨트롤이 밀림 (이미지가 통째로 뒤섞인 결과)
  * 훅의 segs 입력이 Detailer 의 segs 와 다른 노드 출력일 때

v2 는 Impact Pack 이 세그먼트 처리 시점마다 넘겨주는 ``post_crop_region``
콜백으로 "지금 처리 중인 타일의 crop_region"을 직접 받아, 그 좌표로 image 를
라이브 크롭한다. 순서·스킵·재실행·cycle 에 완전히 비의존적이다.

v3 변경 (하이브리드 매칭 — DetailerForEach 는 post_crop_region 을 호출하지 않음)
--------------------------------------------------------------------------------
실측 결과 ``post_crop_region`` 은 DetailerForEach(Detailer (SEGS))가 아니라
디텍터(detect) 단계에서만 호출되는 콜백이다. 즉 이 훅을 Detailer 에 연결하는
표준 배선에서는 v2 의 라이브 매칭이 한 번도 발동하지 않고, 모든 타일이
무패치(컨트롤 없음)로 샘플링된다.

v3 는 하이브리드로 동작한다:
  1) post_crop_region 이 실제로 호출되면(디텍터 경유 등) 그 좌표를 사용 (라이브)
  2) 호출되지 않으면 "유효 세그 순서"(빈 마스크 세그 제외) 기반 매칭으로 폴백

순서 기반 폴백의 v1 취약점은 다음으로 차단한다:
  * (v3~v4) IS_CHANGED = NaN 으로 훅 객체를 매 실행 새로 만들어 카운터 오염을
    차단했으나, always-dirty 가 하류 캐시를 전부 무효화하는 부작용이 있어
    v5 에서 prompt id 경계 리셋 방식으로 대체됨 (아래 v5 참조)
  * 빈 마스크 세그(Detailer 가 스킵)를 제외한 처리 순서를 사전 계산
  * Detailer 의 ``force_inpaint`` 는 활성화할 것 ("enough big" 스킵 방지 —
    스킵이 발생하면 순서 매칭이 한 칸씩 밀린다)
  * Detailer 가 세그를 SEGS 순서대로 처리한다는 것은 [LAB] 와일드카드의
    타일별 CLIP 로그 순서로 실증됨

또한 v1 의 "segs 좌표계와 image 해상도가 다르면 비례 스케일" 폴백을 제거했다.
virtual_canvas(패딩 캔버스) 좌표계에서 비패딩 원본이 연결되면 이 폴백이
패딩 오프셋과 전혀 다른 비례 매핑을 조용히 수행하여, 타일마다 어긋난 컨트롤이
들어가 결과물이 통째로 틀어진다. v2 는 image 가 crop_region 을 덮지 못하면
빌드 시점(전수 검사) 또는 타일 시점에 명확히 실패/경고한다.

v4 변경 (타일별 Artist Mixer conditioning 통합 — Detailer wildcard 의 positive 대체 문제)
---------------------------------------------------------------------------------------
Detailer (SEGS) 의 wildcard([LAB]) 경로는 각 세그마다 타일 텍스트를 Detailer 의
clip 으로 "단일 프롬프트"로 새로 인코딩하여 positive 를 통째로 대체한다. 따라서
Anima Artist Mixer(granatta000/anima-artist-mixer)처럼 텍스트로 환원 불가능한
혼합 CONDITIONING 을 positive 에 연결해도 타일 단계에서 전부 무효화되고,
작가 혼합이 prompt-나열 수준으로 회귀한다(그림체 밍숭맹숭 현상).

v4 는 mixer 의 average 시맨틱스를 이 훅에 통합해 해결한다:
  * (선택 입력) clip + wildcard_text([LAB] 타일 프롬프트) + artist_text 를 연결하면,
    빌드 시점에 타일마다 — 작가별 "타일텍스트, @작가" 독립 인코딩 → 최장 길이로
    zero-pad → 정규화 가중 평균(cond/pooled) — 으로 conditioning 을 사전 계산한다.
    (= AnimaArtistMixerTextBlend 의 average 모드와 동일 연산을 타일 문맥에서 수행)
  * pre_ksample 에서 현재 타일의 positive 인자를 사전 계산본으로 교체한다. 세그
    식별은 v3 하이브리드 매칭을 LLLite 컨트롤과 '공유'(단일 카운터)하므로 두 기능이
    서로 다른 타일로 배정될 수 없다.
  * 혼합 cond 의 meta 는 "최장 길이를 정의한 인코딩"의 meta 를 재사용한다
    (원 mixer 의 composite 재인코딩 방식 대체 — seq 길이 의존 meta 필드와 혼합
    텐서 길이의 불일치 방지 + 타일당 인코딩 1회 절약).
  * blend_mode: average(기본) / exact(작가별 항목 + strength, 스텝당 forward N배)
    / prompt(가중 태그 나열 단일 인코딩 — 대조군). 어떤 인코딩이 스케줄드
    다항목을 반환하면 average 는 exact 로 폴백한다(경고 로그).
  * Detailer 의 wildcard 입력은 반드시 비울 것. 비우지 않으면 ① 세그마다 낭비
    인코딩 + <lora:...> 사이드이펙트가 생기고 ② [SKIP] 항목이 세그를 통째로
    스킵시켜 순서 매칭이 어긋난다. 같은 이유로 이 훅의 wildcard_text 에서도
    [SKIP] 은 지원하지 않고 빌드 에러로 알린다.
  * 라벨 전수 검증: segs 의 라벨 중 [LAB] 항목과 매칭되지 않는 것이 있으면 빌드
    시점 에러(strict_labels=False 로 완화 가능 — 미매칭 타일은 Detailer 의
    positive 를 그대로 사용하고 경고만 남긴다). "[LAB] 라벨 오염 → 조용한 base
    폴백" 사고의 재발 방지 장치.

v5 변경 (캐시 친화 — IS_CHANGED NaN 제거)
------------------------------------------
v3 의 IS_CHANGED = NaN 은 카운터 오염을 막는 대신 이 노드를 always-dirty 로
만들었다. ComfyUI 캐시 무효화는 하류로 전파되므로, detailer_hook 을 받는
Detailer 와 그 하류 전체(웨이블릿 복원·저장 등)가 입력이 완전히 동일해도
매 큐마다 재실행되는 원인이었다 (동일 조건 재큐 시 즉시 완료가 안 되는 문제.
실측: 큐 직후 첫 실행 노드(초록 테두리)가 항상 이 노드였음).

v5 는 캐시를 되살리되, NaN 이 막아주던 문제를 훅 내부에서 해결한다:
  * IS_CHANGED 는 LLLite 가중치 파일의 mtime/size 해시만 반환 (안정값).
    나머지 입력(segs/clip/텍스트/strength 등)의 변화는 ComfyUI 의 입력
    시그니처 캐시가 자동으로 감지하므로 여기서 다룰 필요가 없다.
    ※ IS_CHANGED 가 예외를 던지면 ComfyUI 가 NaN 처리(=always-dirty)하므로
      이 함수는 어떤 입력에서도 예외를 던지지 않아야 한다.
  * per-run 상태(_tick / _crop_region)는 prompt id 경계 감지로 리셋한다:
    캐시로 재사용된 훅 객체가 새 실행에서 첫 콜백을 받으면
    PromptServer.last_prompt_id 가 저장값과 달라지므로 그 시점에 카운터를
    0 으로 되돌린다. 직전 실행이 interrupt 로 중간에 끝났어도 새 실행은
    새 id 를 받으므로 오염이 이어지지 않는다 (v3 이 NaN 을 택한 바로 그
    시나리오가 이 경로로 해결됨).
  * post_detection 이 호출되는 Impact 버전에서는 그 시점에도 리셋한다
    (실행 시작 1회 호출 — prompt id 경로와 독립적인 이중 안전장치).
  * LLLite 모듈은 "어떤 dit 에 대해 빌드했는지"를 약참조로 기억하여,
    캐시된 훅이 다른 모델과 만나면 재빌드하고 같은 모델이면 재사용한다
    (모델 교체 시 stale LLLite 방지 + 매 실행 84 모듈 재생성/가중치 재로드
    비용 제거).
  * 효과: 시드·입력이 완전히 동일한 재큐는 이 노드와 Detailer 를 포함해
    전부 캐시 히트 → "Prompt executed in 0.0x seconds" 로 즉시 완료.

요구사항
--------
- ComfyUI-Impact-Pack  : Detailer (SEGS), DetailerHook 베이스
- ComfyUI-Anima-LLLite : control_net_lllite_anima.py (ControlNetLLLiteDiT 등)

배선 (BMK Flexible Tile SEGS · virtual_canvas · mixer 통합 기준)
----------------------------------------------------------------
    BMK Flexible Tile SEGS ─ segs → (BMK SEGS Core Mask 등) → SEGS Assign (label)
    SEGS Assign (label) ─ segs ───┬─→ Detailer (SEGS).segs
                                  └─→ (이 노드).segs      ← 라벨 매칭에 사용!
    BMK Flexible Tile SEGS ─ image ───┬─→ Detailer (SEGS).image
                                      └─→ (이 노드).image      ← 반드시 패딩본!
    BMK Flexible Tile SEGS ─ pad_info ──→ (이 노드).pad_info   ← 권장(배선 검증용)
    Wildcard Prompt from String ─ wildcard ────→ (이 노드).wildcard_text
                                └ segs_labels ─→ SEGS Assign (label).labels (기존 유지)
    (작가태그 추출 STRING) ─────────────────────→ (이 노드).artist_text
    CLIP ───────────────────────────────────────→ (이 노드).clip
    (이 노드) ─ DETAILER_HOOK ─→ Detailer (SEGS).detailer_hook

    * Detailer 의 wildcard 입력은 반드시 비운다 (이 노드의 wildcard_text 가 대체).
    * mixer 를 쓰지 않으면 clip/wildcard_text/artist_text 를 비워두면 된다
      (v3 과 동일하게 LLLite 컨트롤만 수행).
    * image 에는 Detailer.image 와 '완전히 동일한' 이미지를 연결한다.
      virtual_canvas 모드라면 그것은 타일 노드의 image 출력(패딩본)이다.
      비패딩 원본을 연결하면 빌드 시점에 에러로 즉시 알려준다.
    * Detailer 의 model 입력에는 LLLite 가 적용되지 않은 '깨끗한' Anima 모델을 연결.
      (LLLite 패치는 이 훅이 세그먼트별로 직접 수행한다.)

모드
----
- control_images 미연결 (권장)  : 각 타일의 crop_region 으로 image 를 라이브
  크롭하여 그 타일의 컨트롤로 사용 → 타일 구조 보존(= 변형 방지)에 최적.
- control_images 연결           : 사용자가 지정한 IMAGE(배치)를 사용. 타일과의
  대응은 crop_region ↔ (이 노드의) segs 인덱스 매칭으로 결정하므로, 이 경우
  segs 입력을 Detailer 와 '같은 노드 출력'으로 연결해야 한다.
- clip + wildcard_text 연결      : 타일별 mixer conditioning 교체 활성.
  artist_text 가 비어 있으면 타일 텍스트를 단일 인코딩(= Detailer wildcard 와
  동등한 동작을 이 훅 경로로 수행).
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import math
import os
import re
import sys
import time
import weakref
from typing import Optional

import numpy as np
import torch
import torch.nn.functional as F

import folder_paths

logger = logging.getLogger(__name__)

_TAG = "[ComfyUI_BMK_Nodes::AnimaLLLiteSEGSHook]"


def _current_prompt_id():
    """지금 실행 중인 프롬프트(큐 항목)의 id 를 반환 (실패 시 None).

    PromptExecutor.execute() 가 실행 시작 시 server.last_prompt_id 에 기록하므로
    이 값이 바뀌었다 = 새 실행이 시작되었다 (중간 취소(interrupt)로 끝난 실행
    직후의 새 실행도 새 id 를 받는다). v5 의 per-run 상태 리셋 기준값.
    """
    try:
        from server import PromptServer
        return getattr(PromptServer.instance, "last_prompt_id", None)
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# 의존 모듈 동적 로드 (custom_nodes 폴더명/로드순서에 의존하지 않도록 방어적으로)
# ─────────────────────────────────────────────────────────────────────────────
def _is_valid_anima_module(mod) -> bool:
    """control_net_lllite_anima 모듈이 맞는지 '타입/callable'까지 엄격히 검증.

    torch 내부 모듈 등은 __getattr__ 로 임의 이름에 대해 _OpNamespace 같은 placeholder
    객체를 만들어 돌려주므로, 단순 'None 이 아님' 체크는 거짓 매칭된다. 실제 클래스/함수
    타입인지 확인해야 한다.
    """
    if mod is None:
        return False
    try:
        cls = getattr(mod, "ControlNetLLLiteDiT", None)
        load_fn = getattr(mod, "load_lllite_weights", None)
        meta_fn = getattr(mod, "read_lllite_metadata", None)
        aspp = getattr(mod, "ASPP_DEFAULT_DILATIONS", None)
    except Exception:
        return False
    return (
        isinstance(cls, type)
        and callable(load_fn)
        and callable(meta_fn)
        and isinstance(aspp, tuple)
    )


def _load_anima_lllite():
    """ComfyUI-Anima-LLLite 의 control_net_lllite_anima 모듈을 찾아 반환."""
    # 1) 모듈 이름으로 매칭 (가장 안전 — 거짓 매칭 방지)
    for name, mod in list(sys.modules.items()):
        if mod is None:
            continue
        if name.endswith("control_net_lllite_anima") and _is_valid_anima_module(mod):
            return mod

    # 2) custom_nodes 하위에서 파일 경로로 직접 로드
    base = getattr(folder_paths, "base_path", None)
    if base:
        root = os.path.join(base, "custom_nodes")
        if os.path.isdir(root):
            for entry in os.listdir(root):
                cand = os.path.join(root, entry, "control_net_lllite_anima.py")
                if os.path.isfile(cand):
                    try:
                        spec = importlib.util.spec_from_file_location(
                            f"_bmk_anima_lllite_{entry}", cand
                        )
                        module = importlib.util.module_from_spec(spec)
                        spec.loader.exec_module(module)  # type: ignore[union-attr]
                        if _is_valid_anima_module(module):
                            return module
                    except Exception:
                        logger.debug("%s failed to import %s", _TAG, cand, exc_info=True)

    # 3) 최후 수단: 엄격 검증을 통과하는 모듈만 attribute 스캔
    for mod in list(sys.modules.values()):
        if _is_valid_anima_module(mod):
            return mod

    raise ImportError(
        f"{_TAG} ComfyUI-Anima-LLLite 의 control_net_lllite_anima 모듈을 찾지 못했습니다. "
        "custom_nodes 에 정상 설치되어 있는지 확인하세요."
    )


def _load_detailer_hook_base():
    """Impact Pack 의 DetailerHook 베이스 클래스를 반환."""
    # 1) 표준 경로
    for name in ("impact.hooks", "impact_pack.hooks"):
        try:
            mod = importlib.import_module(name)
            base = getattr(mod, "DetailerHook", None)
            if isinstance(base, type):
                return base
        except Exception:
            pass
    # 2) 이미 로드된 모듈에서 탐색
    for mod in list(sys.modules.values()):
        if mod is None:
            continue
        try:
            base = getattr(mod, "DetailerHook", None)
        except Exception:
            base = None
        if isinstance(base, type):
            return base
    raise ImportError(
        f"{_TAG} Impact Pack 의 DetailerHook 베이스 클래스를 찾지 못했습니다. "
        "ComfyUI-Impact-Pack 설치를 확인하세요."
    )


# ─────────────────────────────────────────────────────────────────────────────
# LLLite 래퍼 헬퍼 (ComfyUI-Anima-LLLite/nodes.py 의 로직을 그대로 재사용)
# ─────────────────────────────────────────────────────────────────────────────
def _get_inner_dit(model) -> torch.nn.Module:
    """ComfyUI ModelPatcher 에서 내부 Anima DiT(nn.Module)를 꺼낸다."""
    inner = getattr(model, "model", None)
    if inner is None:
        raise RuntimeError("Input MODEL has no .model attribute (not a ModelPatcher?)")
    dit = getattr(inner, "diffusion_model", None)
    if dit is None:
        raise RuntimeError("MODEL.model has no .diffusion_model — not a UNet/DiT model?")
    return dit


def _prepare_cond_image(image: torch.Tensor, latent_h: int, latent_w: int,
                        device: torch.device, dtype: torch.dtype,
                        patch_spatial: int = 2) -> torch.Tensor:
    """ComfyUI IMAGE (B,H,W,3) [0,1] → (1,3,H*8,W*8) [-1,1].

    LLLite conditioning1 의 stride 가 16 이라 cond 이미지는 latent_HW*8 픽셀로
    맞춰야 한다. DiT 가 latent 를 patch_spatial 배수로 패딩하므로 그 반올림을
    동일하게 반영한다(원 노드와 동일). 안 맞추면 토큰 수 불일치로 LLLite 가 조용히
    바이패스된다.
    """
    if image.ndim == 4 and image.shape[-1] == 3:
        img = image.permute(0, 3, 1, 2).contiguous()
    else:
        raise ValueError(f"Unexpected cond image shape: {tuple(image.shape)} (expected B,H,W,3)")

    img = img[:1]  # 첫 프레임만 사용

    padded_h = ((latent_h + patch_spatial - 1) // patch_spatial) * patch_spatial
    padded_w = ((latent_w + patch_spatial - 1) // patch_spatial) * patch_spatial
    target_h = padded_h * 8
    target_w = padded_w * 8

    if img.shape[-2] != target_h or img.shape[-1] != target_w:
        img = F.interpolate(img, size=(target_h, target_w), mode="bicubic", align_corners=False)

    img = img.clamp(0.0, 1.0)
    img = img * 2.0 - 1.0
    return img.to(device=device, dtype=dtype)


def _build_lllite(anima_mod, dit, weights_path: str, strength: float):
    """가중치 메타데이터로 ControlNetLLLiteDiT 를 구성하고 로드한다(원 노드와 동일)."""
    meta = anima_mod.read_lllite_metadata(weights_path)
    ce_dim = int(meta.get("lllite.cond_emb_dim", 32))
    m_dim = int(meta.get("lllite.mlp_dim", 64))
    tl = meta.get("lllite.target_atomics", meta.get("lllite.target_layers", "self_attn_q"))
    cond_dim = int(meta.get("lllite.cond_dim", 64))
    cond_resblocks = int(meta.get("lllite.cond_resblocks", 1))
    use_aspp = str(meta.get("lllite.use_aspp", "false")).lower() == "true"
    aspp_meta = meta.get("lllite.aspp_dilations")
    if use_aspp and aspp_meta:
        aspp = tuple(int(d) for d in aspp_meta.split(",") if d.strip())
    else:
        aspp = anima_mod.ASPP_DEFAULT_DILATIONS

    lllite = anima_mod.ControlNetLLLiteDiT(
        dit,
        cond_emb_dim=ce_dim,
        mlp_dim=m_dim,
        target_layers=tl,
        multiplier=strength,
        cond_dim=cond_dim,
        cond_resblocks=cond_resblocks,
        use_aspp=use_aspp,
        aspp_dilations=aspp,
    )
    anima_mod.load_lllite_weights(lllite, weights_path, strict=False)
    lllite.eval().requires_grad_(False)
    return lllite


def _install_lllite_wrapper(model, lllite, src_image: torch.Tensor, strength: float,
                            sigma_start: float, sigma_end: float, patch_spatial: int):
    """주어진 모델 클론에 단일 컨트롤 이미지용 unet_function_wrapper 를 설치하고 반환.

    원 노드 AnimaLLLiteApply.apply 의 wrapper 와 동일한 동작 (해상도별 cond 캐시,
    sigma 범위 게이트, apply_to/restore 스코핑). 세그먼트마다 src_image 만 달라진다.
    """
    src_image = src_image.detach().clone()
    cache = {"cond_image_pp": None, "key": None, "lllite_loaded_to": None}

    def wrapper(apply_model, args):
        input_x = args["input"]
        timestep = args["timestep"]
        c = args["c"]

        # 스텝 범위 게이트: 현재 sigma 가 [sigma_end, sigma_start] 밖이면 LLLite 미적용.
        sigma = float(timestep.max().item())
        if not (sigma_end <= sigma <= sigma_start):
            return apply_model(input_x, timestep, **c)

        latent_h, latent_w = int(input_x.shape[-2]), int(input_x.shape[-1])
        device = input_x.device
        dtype = input_x.dtype

        tag = (device, dtype)
        if cache["lllite_loaded_to"] != tag:
            lllite.to(device=device, dtype=dtype)
            cache["lllite_loaded_to"] = tag
            cache["cond_image_pp"] = None

        key = (latent_h, latent_w, device, dtype)
        if cache["key"] != key or cache["cond_image_pp"] is None:
            cache["cond_image_pp"] = _prepare_cond_image(
                src_image, latent_h, latent_w, device, dtype, patch_spatial
            )
            cache["key"] = key

        lllite.set_multiplier(strength)
        lllite.set_cond_image(cache["cond_image_pp"])
        lllite.apply_to()
        try:
            return apply_model(input_x, timestep, **c)
        finally:
            lllite.restore()
            lllite.clear_cond_image()

    m = model.clone()
    m.set_model_unet_function_wrapper(wrapper)
    return m


def _crop_from_image(image: torch.Tensor, crop_region) -> Optional[torch.Tensor]:
    """image(B,H,W,3)에서 crop_region 을 '그대로' 잘라 컨트롤 이미지로 반환.

    v2: 좌표 비례 스케일 폴백 제거. crop_region 은 Detailer 가 실제 처리 중인
    좌표(=Detailer.image 좌표계)이므로, image 가 그 좌표계와 동일해야 한다.
    image 가 crop_region 을 덮지 못하면 None 을 반환한다(호출부에서 경고).
    """
    if not torch.is_tensor(image) or image.ndim != 4 or image.shape[-1] != 3:
        return None
    if crop_region is None or len(crop_region) < 4:
        return None

    x1, y1, x2, y2 = (int(crop_region[0]), int(crop_region[1]),
                      int(crop_region[2]), int(crop_region[3]))
    _, H, W, _ = image.shape
    if x1 < 0 or y1 < 0 or x2 > W or y2 > H or x2 <= x1 or y2 <= y1:
        return None

    return image[:1, y1:y2, x1:x2, :].contiguous()


def _seg_crop_to_image(seg) -> Optional[torch.Tensor]:
    """Impact Pack SEG.cropped_image → ComfyUI IMAGE 텐서 (1,H,W,3) [0,1]."""
    crop = getattr(seg, "cropped_image", None)
    if crop is None:
        return None
    if isinstance(crop, np.ndarray):
        t = torch.from_numpy(crop)
    elif torch.is_tensor(crop):
        t = crop
    else:
        return None

    t = t.float()
    if t.ndim == 3:                       # (H,W,3) 또는 (3,H,W)
        if t.shape[0] == 3 and t.shape[-1] != 3:
            t = t.permute(1, 2, 0)
        t = t.unsqueeze(0)
    elif t.ndim == 4:                     # (B,H,W,3) 또는 (B,3,H,W)
        if t.shape[1] == 3 and t.shape[-1] != 3:
            t = t.permute(0, 2, 3, 1)
    else:
        return None

    if t.numel() and float(t.max()) > 1.5:  # 0..255 로 저장된 경우 정규화
        t = t / 255.0
    return t.clamp(0.0, 1.0).contiguous()


def _crop_region_key(cr) -> Optional[tuple]:
    if cr is None or len(cr) < 4:
        return None
    return (int(cr[0]), int(cr[1]), int(cr[2]), int(cr[3]))


def _mask_is_empty(m) -> bool:
    """Detailer 의 'segment skip [empty mask]' 조건을 미러링 (np/torch 겸용)."""
    try:
        if m is None:
            return False
        if isinstance(m, np.ndarray):
            return not bool(np.any(m))
        if torch.is_tensor(m):
            return not bool(m.any())
    except Exception:
        pass
    return False


# ─────────────────────────────────────────────────────────────────────────────
# v4: Artist Mixer 시맨틱스 (granatta000/anima-artist-mixer 의 average 모드 재현)
# ─────────────────────────────────────────────────────────────────────────────
# mixer 의 WEIGHTED_TAG_RE 와 동일: 비탐욕 tag + 마지막 ":숫자)" 앵커라
# '@goddess of victory: nikke' 처럼 콜론을 포함한 태그도 올바르게 파싱된다.
_WEIGHTED_TAG_RE = re.compile(
    r"^\(\s*(?P<tag>.*?)\s*:\s*(?P<weight>[+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*\)$"
)

# Impact Pack wildcards.process_wildcard_for_segs 의 LAB 라벨 charset 과 동일.
_LAB_SPLIT_RE = re.compile(r"\[([A-Za-z0-9_. ]+)\]")


def _split_prompt_tags(text):
    """depth-0 콤마로만 분리 — '(tag:1.2)' 내부 콤마 보호 (mixer 미러)."""
    tags = []
    current = []
    depth = 0
    for char in text or "":
        if char == "(":
            depth += 1
        elif char == ")" and depth > 0:
            depth -= 1
        if char == "," and depth == 0:
            tag = "".join(current).strip()
            if tag:
                tags.append(tag)
            current = []
            continue
        current.append(char)
    tag = "".join(current).strip()
    if tag:
        tags.append(tag)
    return tags


def _parse_artists(text):
    """artist_text → [(tag, weight)]  (mixer 의 parse_prompt_artists 미러).

    무가중 태그는 1.0, weight ≤ 0 / 비유한값은 드롭. 가중치는 이후 정규화되므로
    절대값이 아니라 '비율'만 의미가 있다 (mixer average/exact 와 동일 의미론).
    """
    artists = []
    for raw in _split_prompt_tags(text):
        raw = raw.strip()
        m = _WEIGHTED_TAG_RE.match(raw)
        if m:
            tag = m.group("tag").strip()
            try:
                weight = float(m.group("weight"))
            except ValueError:
                continue
        elif raw.startswith("(") and raw.endswith(")"):
            tag = raw[1:-1].strip()
            weight = 1.0
        else:
            tag = raw
            weight = 1.0
        if tag and math.isfinite(weight) and weight > 0:
            artists.append((tag, weight))
    return artists


def _format_weighted_tag(tag, weight):
    if math.isclose(weight, 1.0):
        return tag
    return f"({tag}:{weight:g})"


def _append_prompt(base, extra):
    base = (base or "").strip()
    extra = extra.strip()
    if not base:
        return extra
    return f"{base}, {extra}"


def _encode(clip, text):
    tokens = clip.tokenize(text)
    return clip.encode_from_tokens_scheduled(tokens)


def _pad_cond(t, target_len):
    """cond 텐서 (B,L,D) 를 target_len 으로 zero-pad (mixer 미러)."""
    if t.shape[1] >= target_len:
        return t[:, :target_len]
    pad = torch.zeros(
        (t.shape[0], target_len - t.shape[1], t.shape[2]),
        dtype=t.dtype, device=t.device,
    )
    return torch.cat([t, pad], dim=1)


def _blend_tile_conditioning(clip, tile_text, artists, blend_mode):
    """타일 텍스트 1개에 대한 conditioning 생성. 반환: (conditioning, used_mode).

    - artists 비어있음      : tile_text 단일 인코딩                      ("plain")
    - blend_mode="prompt"  : 가중 태그 나열 단일 인코딩                  ("prompt")
    - blend_mode="average" : 작가별 독립 인코딩 → zero-pad → 정규화 가중 평균
                             ("average"; 스케줄드 다항목 발생 시 exact 폴백)
    - blend_mode="exact"   : 작가별 항목 + 정규화 strength               ("exact")

    average 의 meta 는 최장 길이를 정의한 인코딩의 meta 를 재사용한다 —
    seq 길이 의존적인 meta 필드(예: attention mask 류)와 혼합 텐서 길이가
    일치하도록. (원 mixer 의 composite 재인코딩 대체)
    """
    if not artists:
        return _encode(clip, tile_text), "plain"

    if blend_mode == "prompt":
        tags = ", ".join(_format_weighted_tag(t, w) for t, w in artists)
        return _encode(clip, _append_prompt(tile_text, tags)), "prompt"

    encoded = []
    scheduled = False
    for tag, w in artists:
        c = _encode(clip, _append_prompt(tile_text, tag))
        if len(c) != 1:
            scheduled = True
        encoded.append((w, c))

    total = sum(w for w, _ in encoded)
    if total <= 0:
        return _encode(clip, tile_text), "plain"

    if blend_mode == "exact" or scheduled:
        out = []
        for w, c in encoded:
            s = w / total
            for cond, meta in c:
                m = dict(meta)
                m["strength"] = s  # 원 mixer 와 동일: 기존 strength 가 있어도 덮어씀
                out.append([cond, m])
        return out, ("exact" if blend_mode == "exact" else "exact_fallback")

    # ── average: zero-pad 후 정규화 가중 평균 (cond / pooled 각각) ──
    max_len = max(int(c[0][0].shape[1]) for _, c in encoded)
    mixed = None
    mixed_pooled = None
    meta_src = None
    for w, c in encoded:
        cond, meta = c[0]
        s = w / total
        padded = _pad_cond(cond, max_len)
        mixed = padded * s if mixed is None else mixed + padded * s
        pooled = meta.get("pooled_output")
        if pooled is not None:
            mixed_pooled = pooled * s if mixed_pooled is None else mixed_pooled + pooled * s
        if meta_src is None and int(cond.shape[1]) == max_len:
            meta_src = meta

    out_meta = dict(meta_src if meta_src is not None else encoded[0][1][0][1])
    out_meta.pop("strength", None)
    if mixed_pooled is not None:
        out_meta["pooled_output"] = mixed_pooled
    else:
        out_meta.pop("pooled_output", None)
    return [[mixed, out_meta]], "average"


def _parse_lab_wildcard(text):
    """[LAB] 와일드카드 → {label: content}. [LAB] 로 시작하지 않으면 None.

    Impact Pack wildcards.process_wildcard_for_segs 의 LAB 파싱을 미러링한다
    (라벨 charset [A-Za-z0-9_. ] 동일 → Detailer 와 파싱 결과가 항상 일치).
    """
    body = text.strip()
    if not body.startswith("[LAB]"):
        return None
    body = body[len("[LAB]"):]
    raw = _LAB_SPLIT_RE.split(body)
    if raw and raw[0].strip():
        logger.warning("%s [LAB] 직후 라벨 없는 텍스트는 무시됩니다: %r",
                       _TAG, raw[0].strip()[:80])
    out = {}
    for i in range(1, len(raw), 2):
        label = raw[i].strip()
        content = raw[i + 1].strip() if i + 1 < len(raw) else ""
        if not content:
            continue
        if label in out:
            logger.warning("%s [LAB] 라벨 중복: [%s] — 마지막 항목을 사용합니다.",
                           _TAG, label)
        out[label] = content
    return out


def _build_cond_map(clip, segs, wildcard_text, artist_text, blend_mode, strict_labels):
    """빌드 시점: seg index → 사전 인코딩된 conditioning 맵 생성.

    [LAB] 라벨 ↔ segs 라벨 전수 검증 포함. 동일 텍스트는 1회만 인코딩(dedupe).
    """
    seg_list = segs[1] if segs and len(segs) > 1 and segs[1] else []
    if not seg_list:
        raise ValueError(f"{_TAG} segs 가 비어 있어 타일별 conditioning 을 만들 수 없습니다.")

    # [SKIP] 은 파싱 '전' 원문에서 차단해야 한다: LAB 라벨 regex 가 [SKIP] 을
    # 라벨로 소비하므로 파싱 후에는 콘텐츠로 남지 않는다 (Impact 파서도 동일).
    if "[SKIP]" in wildcard_text:
        raise ValueError(
            f"{_TAG} wildcard_text 에 [SKIP] 이 포함되어 있습니다. 이 훅 경로에서는 "
            "[SKIP] 을 지원하지 않습니다 (Detailer 의 wildcard 를 비우는 배선이라 스킵이 "
            "발생하지 않고, 스킵이 발생하면 순서 매칭이 어긋납니다). 해당 타일을 "
            "빼려면 SEGS 단계에서 제외하세요.")

    artists = _parse_artists(artist_text)
    if artist_text.strip() and not artists:
        logger.warning("%s artist_text 파싱 결과가 비어 있습니다 (형식: "
                       "'(@tag:1.2), @tag2'). 타일 텍스트만 단일 인코딩합니다.", _TAG)

    lab = _parse_lab_wildcard(wildcard_text)

    per_index = {}  # seg index → (label|None, text)
    if lab is None:
        t = wildcard_text.strip()
        if t.startswith("[") and not t.startswith("[LAB]"):
            raise ValueError(
                f"{_TAG} 지원하지 않는 와일드카드 디렉티브입니다: {t[:16]}… "
                "(이 훅은 [LAB] 또는 일반 텍스트만 지원합니다. [ASC]/[DSC] 등은 "
                "'SEGS Assign (label) + [LAB]' 조합으로 대체하세요.)")
        # 디렉티브 없는 일반 텍스트 → 모든 타일에 동일 프롬프트
        for i in range(len(seg_list)):
            per_index[i] = (None, t)
    else:
        if not lab:
            raise ValueError(
                f"{_TAG} [LAB] 뒤에 '[라벨] 내용' 항목이 하나도 없습니다. "
                "Wildcard Prompt from String 의 wildcard 출력을 연결했는지 확인하세요.")
        missing = []
        for i, s in enumerate(seg_list):
            label = str(getattr(s, "label", "") or "").strip()
            content = lab.get(label)
            if content is None:
                missing.append(label if label else f"<빈 라벨: seg #{i}>")
            else:
                per_index[i] = (label, content)
        if missing:
            msg = (f"{_TAG} {len(missing)}개 세그의 라벨이 [LAB] 항목과 매칭되지 "
                   f"않습니다: {missing[:8]}{' …' if len(missing) > 8 else ''} / "
                   f"[LAB] 라벨: {sorted(lab.keys())[:12]}. 이 훅의 segs 입력은 "
                   "Detailer 와 동일한 'SEGS Assign (label) 이후' 출력이어야 합니다.")
            if strict_labels:
                raise ValueError(
                    msg + " (의도적 부분 매칭이라면 strict_labels 를 끄세요 — "
                          "미매칭 타일은 Detailer 의 positive 를 그대로 사용)")
            logger.warning("%s → 미매칭 타일은 Detailer positive 로 진행", msg)

    t0 = time.perf_counter()
    cache = {}       # text → (conditioning, used_mode)
    cond_map = {}
    mode_counts = {}
    for i in sorted(per_index):
        _, t = per_index[i]
        if t not in cache:
            cache[t] = _blend_tile_conditioning(clip, t, artists, blend_mode)
        cond, used = cache[t]
        cond_map[i] = cond
        mode_counts[used] = mode_counts.get(used, 0) + 1
    dt = time.perf_counter() - t0

    enc_per_text = max(1, len(artists)) if (artists and blend_mode in ("average", "exact")) else 1
    logger.info("%s 타일별 mixer conditioning 준비: %d/%d tile(s), 고유 텍스트 %d개, "
                "작가 %d명, blend=%s (적용: %s), 인코딩 %d회, %.2fs",
                _TAG, len(cond_map), len(seg_list), len(cache), len(artists),
                blend_mode, ", ".join(f"{k}×{v}" for k, v in sorted(mode_counts.items())),
                len(cache) * enc_per_text, dt)
    if "exact_fallback" in mode_counts:
        logger.warning("%s 일부 인코딩이 스케줄드 다항목을 반환하여 average 대신 exact 로 "
                       "폴백했습니다 → positive 항목 수 증가로 해당 타일의 스텝당 forward 가 "
                       "작가 수만큼 늘어납니다.", _TAG)
    return cond_map


def _looks_like_conditioning(x) -> bool:
    """ComfyUI CONDITIONING 형태([[tensor, dict], ...]) 여부 판정."""
    if not isinstance(x, list) or not x:
        return False
    first = x[0]
    return (
        isinstance(first, (list, tuple))
        and len(first) >= 2
        and torch.is_tensor(first[0])
        and isinstance(first[1], dict)
    )


def _find_positive_slot(args) -> Optional[int]:
    """pre_ksample 의 *args 에서 positive conditioning 의 위치를 찾는다.

    표준 시그니처 (seed, steps, cfg, sampler_name, scheduler, positive, negative,
    upscaled_latent, denoise) 에서는 5. Impact Pack 버전 변화에 대비해 5 가
    아니면 '첫 번째 conditioning 형태 인자'로 폴백한다 (positive 가 negative 보다
    항상 앞이므로 첫 매칭 = positive).
    """
    if len(args) > 5 and _looks_like_conditioning(args[5]):
        return 5
    for i, a in enumerate(args):
        if _looks_like_conditioning(a):
            return i
    return None


# ─────────────────────────────────────────────────────────────────────────────
# DetailerHook 서브클래스 (베이스를 런타임에 로드하므로 동적 생성 + 캐시)
# ─────────────────────────────────────────────────────────────────────────────
_HOOK_CLASS_CACHE: dict = {}


def _get_or_make_hook_class(base):
    cached = _HOOK_CLASS_CACHE.get(base)
    if cached is not None:
        return cached

    class _AnimaLLLitePerSEGSHook(base):  # type: ignore[misc, valid-type]
        def __init__(self, segs, anima_mod, weights_path, strength,
                     start_percent, end_percent, cycle, control_images, image,
                     cond_map=None):
            try:
                super().__init__()
            except Exception:
                pass
            self.segs = segs
            self.anima_mod = anima_mod
            self.weights_path = weights_path
            self.strength = float(strength)
            self.start_percent = float(start_percent)
            self.end_percent = float(end_percent)
            self.control_images = control_images
            self.image = image  # Detailer.image 와 동일한 이미지 (crop_region 으로 잘라 씀)

            # v4: 타일별 mixer conditioning (seg index → CONDITIONING). None 이면 비활성.
            # LLLite 컨트롤과 '같은' 세그 식별(_current_seg)을 공유하므로 어긋날 수 없다.
            self.cond_map = cond_map

            # v3: 하이브리드 매칭 상태.
            # cycle 은 순서 폴백에서 pre_ksample 호출 수를 세그 인덱스로 환산할 때 사용
            # (Detailer 의 cycle 과 동일하게 맞출 것, 기본 1).
            self.cycle = max(1, int(cycle))
            self._crop_region = None          # 라이브 경로 (post_crop_region 수신 시)
            self._tick = 0                    # 폴백 경로: pre_ksample 호출 카운터
            # v5: 실행(run) 경계 감지 — 캐시로 재사용된 훅 객체가 새 실행에
            # 진입하면 per-run 상태를 리셋하기 위한 기준값.
            self._run_id = _current_prompt_id()
            # v5: LLLite 를 어떤 dit 에 대해 빌드했는지 (모델 교체 감지, 약참조 —
            # 강참조로 들고 있으면 캐시된 훅이 이전 모델을 메모리에 붙잡아 둔다)
            self._built_dit_ref = None
            self._fallback_logged = False
            self._warned_bounds = False
            self._warned_no_pos_slot = False
            self._cond_replace_logged = False

            # control_images 모드용: crop_region → segs 인덱스 매핑 (순서 비의존)
            self._cr_to_index = {}
            # 순서 폴백용: Detailer 가 실제로 처리할 "유효 세그" 인덱스 순서
            # (빈 마스크 세그는 Detailer 가 스킵하므로 제외하여 동기화 유지)
            self._proc_order = []
            try:
                seg_list = segs[1] if segs and len(segs) > 1 and segs[1] else []
                for i, s in enumerate(seg_list):
                    key = _crop_region_key(getattr(s, "crop_region", None))
                    if key is not None:
                        self._cr_to_index[key] = i
                    if not _mask_is_empty(getattr(s, "cropped_mask", None)):
                        self._proc_order.append(i)
            except Exception:
                pass

            self.lllite = None
            self.patch_spatial = 2
            self.sigma_start = None
            self.sigma_end = None

        # 첫 pre_ksample 시점에 런타임 모델의 dit 로부터 LLLite 를 1회 구성.
        # v5: 훅 객체가 캐시로 재사용될 수 있으므로 "같은 dit" 이면 재사용하고
        # (매 실행 84 모듈 재생성 + 가중치 재로드 제거), dit 가 바뀌었으면
        # (모델 교체) stale LLLite 를 쓰지 않도록 재빌드한다.
        def _ensure_built(self, model):
            dit = _get_inner_dit(model)
            built_for = self._built_dit_ref() if self._built_dit_ref is not None else None
            if self.lllite is not None and built_for is dit:
                return
            self.patch_spatial = int(getattr(dit, "patch_spatial", 2))
            self.lllite = _build_lllite(self.anima_mod, dit, self.weights_path, self.strength)
            ms = model.get_model_object("model_sampling")
            self.sigma_start = float(ms.percent_to_sigma(self.start_percent))
            self.sigma_end = float(ms.percent_to_sigma(self.end_percent))
            self._built_dit_ref = weakref.ref(dit)

        # ── v5: 실행 경계 감지 / per-run 상태 리셋 ──────────────────────────
        def _reset_run_state(self):
            self._tick = 0
            self._crop_region = None

        def _check_run_boundary(self):
            """캐시로 재사용된 훅 객체가 '새 실행'에 진입했는지 감지하고 리셋.

            prompt id 는 큐 항목마다 새로 발급되므로, 직전 실행이 interrupt 로
            중간에 끝났어도(카운터가 어중간한 값으로 남아 있어도) 새 실행의 첫
            콜백에서 반드시 리셋된다 — v3 이 IS_CHANGED=NaN 으로 막던 시나리오.
            """
            rid = _current_prompt_id()
            if rid is not None and rid != self._run_id:
                self._run_id = rid
                self._reset_run_state()

        # Impact Pack 버전에 따라 Detailer 실행 시작 시 1회 호출됨. 호출되는
        # 버전에서는 prompt id 경로와 독립적인 확실한 리셋 지점이 된다
        # (이중 안전장치 — 호출되지 않는 버전에서도 _check_run_boundary 로 충분).
        def post_detection(self, segs):
            self._reset_run_state()
            self._run_id = _current_prompt_id()
            try:
                return super().post_detection(segs)
            except Exception:
                return segs

        # Impact Pack 이 각 세그먼트의 crop 단계에서 호출 → "지금 처리 중인 타일"의
        # 좌표를 직접 받는다. 순서/스킵/재실행/cycle 에 비의존적인 유일한 정보원.
        def post_crop_region(self, w, h, item_bbox, crop_region):
            self._check_run_boundary()  # v5: 캐시 재사용 훅의 새 실행 진입 감지
            self._crop_region = _crop_region_key(crop_region)
            try:
                return super().post_crop_region(w, h, item_bbox, crop_region)
            except Exception:
                return crop_region

        def _current_seg(self):
            """지금 처리 중인 타일 식별. (crop_region_key|None, seg_index|None, 실패사유|None)

            1) 라이브: post_crop_region 으로 좌표를 받았다면 그것을 사용(소비형 —
               읽은 뒤 비워서, 디텍터 단계의 일괄 호출 잔재가 재사용되지 않게 함).
            2) 폴백: 유효 세그 순서 + pre_ksample 호출 카운터로 현재 인덱스 계산.

            v4: 호출부(pre_ksample)에서 '한 번만' 호출하고 그 결과를 LLLite 컨트롤과
            conditioning 교체가 공유한다 (라이브 경로가 소비형이므로 이중 호출 금지).
            """
            cr_live = self._crop_region
            self._crop_region = None
            if cr_live is not None:
                return cr_live, self._cr_to_index.get(cr_live), None

            order = self._proc_order
            if not order:
                return None, None, "유효한(마스크가 비어있지 않은) 세그가 없습니다."

            if not self._fallback_logged:
                logger.info(
                    "%s 이 Impact Pack 버전의 Detailer 는 post_crop_region 을 호출하지 "
                    "않으므로 순서 기반 매칭으로 동작합니다 (정상 동작 — Detailer 는 "
                    "세그를 SEGS 순서대로 처리). force_inpaint 는 활성 상태를 유지하세요.",
                    _TAG)
                self._fallback_logged = True

            k = (self._tick // self.cycle) % len(order)
            idx = order[k]
            try:
                cr = _crop_region_key(getattr(self.segs[1][idx], "crop_region", None))
            except Exception:
                cr = None
            if cr is None:
                return None, idx, f"seg #{idx} 에 crop_region 이 없습니다."
            return cr, idx, None

        def _resolve_control_image(self, cr, idx, err):
            """(control_image | None, 실패 사유 | None) 반환.

            v4: 세그 식별 결과(cr/idx/err)를 인자로 받는다 — pre_ksample 에서
            _current_seg() 를 1회만 호출해 conditioning 교체와 공유하기 위함.
            """
            if err is not None:
                return None, err

            # 1) 사용자가 명시한 control_images 우선: 세그 인덱스로 대응
            if self.control_images is not None:
                if idx is None:
                    return None, (f"crop_region {cr} 이 이 훅의 segs 와 매칭되지 않습니다. "
                                  "훅의 segs 입력을 Detailer 와 같은 노드 출력으로 연결하세요.")
                ci = self.control_images
                bi = idx % int(ci.shape[0])
                return ci[bi:bi + 1], None

            # 2) image 라이브 크롭 (기본 경로)
            if self.image is not None:
                cropped = _crop_from_image(self.image, cr)
                if cropped is not None:
                    return cropped, None
                _, H, W, _ = self.image.shape
                return None, (f"image({W}x{H})가 crop_region {cr} 를 덮지 못합니다. "
                              "virtual_canvas 사용 시 BMK Flexible Tile SEGS 의 image "
                              "출력(패딩본)을 이 훅의 image 에 연결하세요. "
                              "(비패딩 원본 연결 금지 — 좌표계가 다릅니다.)")

            # 3) 혹시 SEGS 가 cropped_image 를 직접 들고 있으면 사용
            if idx is not None:
                try:
                    t = _seg_crop_to_image(self.segs[1][idx])
                    if t is not None:
                        return t, None
                except Exception:
                    pass
            return None, ("컨트롤 소스가 없습니다 (image 미연결, SEGS 에 cropped_image 없음). "
                          "Detailer.image 와 동일한 이미지를 'image' 에 연결하세요.")

        # Impact Pack 이 세그먼트(×cycle)마다 호출. model(그리고 v4 부터는 필요 시
        # positive)을 교체해서 반환. *args 로 뒤 인자들을 그대로 받아 부모 패스스루로
        # 동일 형태로 되돌린다 (Impact Pack 버전별 pre_ksample 시그니처 변화에 견고).
        # 컨트롤/컨디셔닝 해석 성공/실패와 무관하게 매 호출마다 카운터를 증가시켜
        # Detailer 의 진행과 동기화를 유지한다.
        def pre_ksample(self, model, *args):
            self._check_run_boundary()  # v5: 캐시 재사용 훅의 새 실행 진입 감지
            patched = model
            out_args = args

            # ── 0) 현재 타일 식별 (LLLite 와 conditioning 이 공유 — 1회만 호출) ──
            try:
                cr, idx, err = self._current_seg()
            except Exception:
                logger.exception("%s 세그 식별 실패", _TAG)
                cr, idx, err = None, None, "세그 식별 중 예외"

            # ── 1) LLLite 타일별 컨트롤 패치 ──
            try:
                self._ensure_built(model)
                img, reason = self._resolve_control_image(cr, idx, err)
                if img is None:
                    if not self._warned_bounds:
                        logger.warning("%s 컨트롤 이미지 없음 → 무패치 진행: %s",
                                       _TAG, reason)
                        self._warned_bounds = True
                else:
                    patched = _install_lllite_wrapper(
                        model, self.lllite, img, self.strength,
                        self.sigma_start, self.sigma_end, self.patch_spatial,
                    )
            except Exception:
                logger.exception("%s LLLite 패치 실패 → 컨트롤 없이 진행", _TAG)

            # ── 2) 타일별 positive conditioning 교체 (v4 mixer) ──
            # LLLite 실패와 격리 — 한쪽 실패가 다른 쪽을 죽이지 않게 별도 try.
            try:
                if self.cond_map and idx is not None:
                    cond = self.cond_map.get(idx)
                    # cond 없음 = 비엄격 모드의 미매칭 타일 → Detailer positive 유지
                    if cond is not None:
                        slot = _find_positive_slot(args)
                        if slot is None:
                            if not self._warned_no_pos_slot:
                                logger.warning(
                                    "%s pre_ksample 인자에서 positive conditioning 을 "
                                    "찾지 못했습니다 (Impact Pack 시그니처 변화?) → "
                                    "conditioning 교체 생략", _TAG)
                                self._warned_no_pos_slot = True
                        else:
                            out_args = list(args)
                            out_args[slot] = cond
                            if not self._cond_replace_logged:
                                logger.info(
                                    "%s 타일별 mixer conditioning 교체 활성 "
                                    "(첫 교체: seg #%s, positive slot=%d)",
                                    _TAG, idx, slot)
                                self._cond_replace_logged = True
            except Exception:
                logger.exception("%s conditioning 교체 실패 → Detailer positive 유지", _TAG)

            self._tick += 1
            return super().pre_ksample(patched, *out_args)

    _HOOK_CLASS_CACHE[base] = _AnimaLLLitePerSEGSHook
    return _AnimaLLLitePerSEGSHook


# ─────────────────────────────────────────────────────────────────────────────
# ComfyUI 노드
# ─────────────────────────────────────────────────────────────────────────────
class BMKAnimaLLLiteSEGSHook:
    DESCRIPTION = (
        "Detailer (SEGS) 의 detailer_hook 에 연결해, 타일(세그)마다 ① 해당 타일 크롭을 "
        "Anima ControlNet-LLLite 컨트롤로 적용하고 ② (선택) [LAB] 타일 프롬프트를 Artist "
        "Mixer(average) 방식으로 인코딩해 positive conditioning 을 타일별로 교체합니다. "
        "Detailer 의 wildcard 가 mixer CONDITIONING 을 무효화하는 문제의 해결용 — 사용 시 "
        "Detailer 의 wildcard 는 비우세요."
    )
    SEARCH_ALIASES = [
        "lllite segs hook", "per tile controlnet", "artist mixer hook",
        "tile conditioning", "wildcard conditioning", "타일별 컨트롤넷",
        "타일 프롬프트", "작가 혼합 훅", "디테일러 훅",
    ]

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "segs": ("SEGS",),
                "lllite_name": (folder_paths.get_filename_list("controlnet"),),
                "strength": ("FLOAT", {"default": 1.0, "min": -10.0, "max": 10.0, "step": 0.01}),
                "start_percent": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 1.0, "step": 0.001}),
                "end_percent": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 1.0, "step": 0.001}),
                "cycle": ("INT", {"default": 1, "min": 1, "max": 100,
                                  "tooltip": "순서 폴백 매칭 시 Detailer 의 cycle 값과 "
                                             "동일하게 맞출 것 (기본 1)"}),
            },
            "optional": {
                # 필수 권장: Detailer.image 와 '완전히 동일한' 이미지를 연결.
                # virtual_canvas 사용 시 = BMK Flexible Tile SEGS 의 image 출력(패딩본).
                "image": ("IMAGE",),
                # 타일별 컨트롤을 직접 지정하고 싶을 때만 사용.
                # 타일 대응은 crop_region ↔ segs 인덱스 매칭 (연결 시 image 보다 우선).
                "control_images": ("IMAGE",),
                # BMK Flexible Tile SEGS 의 pad_info 를 연결하면 빌드 시점에
                # image 가 패딩본인지 검증한다 (배선 실수 조기 발견용, 선택).
                "pad_info": ("BMK_PAD_INFO",),
                # ── v4: 타일별 Artist Mixer conditioning ──
                "clip": ("CLIP", {
                    "tooltip": "타일별 mixer conditioning 인코딩용 CLIP. "
                               "wildcard_text 사용 시 필수"}),
                "wildcard_text": ("STRING", {
                    "forceInput": True,
                    "tooltip": "[LAB] 타일별 프롬프트 (Wildcard Prompt from String 의 "
                               "wildcard 출력을 연결). 연결 시 Detailer 의 wildcard 는 "
                               "반드시 비울 것. segs 입력은 SEGS Assign (label) 이후 "
                               "출력이어야 라벨 매칭이 됩니다"}),
                "artist_text": ("STRING", {
                    "multiline": True, "default": "", "dynamicPrompts": False,
                    "tooltip": "혼합할 작가 태그: '(@tag:1.2), (@tag2:0.8), @tag3'. "
                               "가중치는 정규화되어 '비율'만 의미 있음 (mixer 와 동일). "
                               "비우면 타일 텍스트만 단일 인코딩"}),
                "blend_mode": (["average", "exact", "prompt"], {
                    "default": "average",
                    "tooltip": "average: 작가별 독립 인코딩의 가중 평균(권장, mixer 동일). "
                               "exact: 작가별 conditioning 항목 + strength (스텝당 forward "
                               "가 작가 수만큼 증가). prompt: 가중 태그 나열 단일 인코딩 "
                               "(대조군 — 문맥 희석 재발)"}),
                "strict_labels": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "True: segs 라벨이 [LAB] 항목과 하나라도 미매칭이면 빌드 "
                               "에러 (조용한 base 폴백 방지). False: 경고만 하고 미매칭 "
                               "타일은 Detailer 의 positive 를 그대로 사용"}),
            },
        }

    RETURN_TYPES = ("DETAILER_HOOK",)
    RETURN_NAMES = ("detailer_hook",)
    FUNCTION = "build"
    CATEGORY = "BMK/Anima"

    @classmethod
    def IS_CHANGED(cls, lllite_name=None, **kwargs):
        # v5: 캐시 친화. 입력(segs/clip/텍스트/strength 등)의 변화는 ComfyUI 의
        # 입력 시그니처 캐시가 자동 비교하므로, 여기서는 "입력 밖에서 출력에
        # 영향을 주는 값" — LLLite 가중치 파일의 실체 — 만 해시한다 (규약 3항).
        # 파일을 같은 이름으로 교체하면 mtime/size 가 바뀌어 캐시가 무효화된다.
        #
        # 주의 1: 예외를 던지면 ComfyUI 가 이 노드를 NaN(always-dirty) 처리하여
        #         v4 의 캐시 무효화 문제로 회귀한다 → 어떤 입력에서도 예외 금지.
        # 주의 2: IS_CHANGED 는 링크 연결 입력이 채워지지 않은 채 호출될 수 있으므로
        #         (WidgetToString 의 'missing keyword-only argument' 경고가 그 사례)
        #         모든 파라미터는 기본값 + **kwargs 로 받아야 한다.
        try:
            path = folder_paths.get_full_path("controlnet", lllite_name)
            if path and os.path.isfile(path):
                st = os.stat(path)
                return f"{path}|{st.st_mtime_ns}|{st.st_size}"
        except Exception:
            pass
        return ""

    def build(self, segs, lllite_name, strength, start_percent, end_percent,
              cycle, image=None, control_images=None, pad_info=None,
              clip=None, wildcard_text="", artist_text="", blend_mode="average",
              strict_labels=True):
        weights_path = folder_paths.get_full_path("controlnet", lllite_name)
        if weights_path is None or not os.path.isfile(weights_path):
            raise FileNotFoundError(f"{_TAG} LLLite weights not found: {lllite_name}")

        if image is None and control_images is None:
            logger.warning(
                "%s image / control_images 둘 다 미연결 → 세그먼트 컨트롤 소스가 없어 "
                "무패치로 진행될 수 있습니다. Detailer.image 와 동일한 이미지를 'image' 에 연결하세요.",
                _TAG,
            )

        # ── 빌드 시점 배선 검증 ──────────────────────────────────────────────
        if image is not None:
            ih, iw = int(image.shape[1]), int(image.shape[2])

            # 1) pad_info 연결 시: 패딩 캔버스 크기와 정확히 일치해야 함
            if isinstance(pad_info, dict):
                pw = int(pad_info.get("padded_width", 0))
                ph = int(pad_info.get("padded_height", 0))
                if pw > 0 and ph > 0 and (iw, ih) != (pw, ph):
                    raise ValueError(
                        f"{_TAG} image({iw}x{ih})가 pad_info 의 패딩 캔버스({pw}x{ph})와 "
                        "다릅니다. BMK Flexible Tile SEGS 의 image 출력(패딩본)을 이 훅의 "
                        "image 에 연결하세요. (원본/비패딩 이미지가 연결된 것으로 보입니다.)")

            # 2) 모든 타일의 crop_region 이 image 안에 들어오는지 전수 검사
            #    (비패딩 원본을 연결한 배선 실수를 실행 시작 시점에 잡는다)
            try:
                seg_list = segs[1] if segs and len(segs) > 1 and segs[1] else []
            except Exception:
                seg_list = []
            bad = []
            for s in seg_list:
                cr = _crop_region_key(getattr(s, "crop_region", None))
                if cr is None:
                    continue
                if cr[0] < 0 or cr[1] < 0 or cr[2] > iw or cr[3] > ih:
                    bad.append(cr)
            if bad:
                raise ValueError(
                    f"{_TAG} image({iw}x{ih})가 {len(bad)}개 타일의 crop_region 을 덮지 "
                    f"못합니다 (예: {bad[0]}). virtual_canvas 사용 시 BMK Flexible Tile "
                    "SEGS 의 image 출력(패딩본)을 이 훅의 image 에 연결해야 합니다. "
                    "(v1 의 비례 스케일 폴백은 어긋난 컨트롤을 만들기 때문에 제거되었습니다.)")

        # ── v4: 타일별 mixer conditioning 사전 인코딩 ────────────────────────
        mixer_requested = wildcard_text is not None and str(wildcard_text).strip() != ""
        cond_map = None
        if mixer_requested:
            if clip is None:
                raise ValueError(
                    f"{_TAG} wildcard_text 가 연결되었지만 clip 이 연결되지 않았습니다. "
                    "타일별 conditioning 인코딩에는 clip 이 필요합니다.")
            cond_map = _build_cond_map(
                clip=clip,
                segs=segs,
                wildcard_text=str(wildcard_text),
                artist_text=str(artist_text or ""),
                blend_mode=blend_mode,
                strict_labels=bool(strict_labels),
            )
        elif clip is not None:
            logger.info("%s clip 은 연결됐지만 wildcard_text 가 비어 있어 타일별 "
                        "conditioning 교체는 비활성화됩니다 (LLLite 컨트롤만 수행).", _TAG)

        anima_mod = _load_anima_lllite()
        base = _load_detailer_hook_base()
        hook_cls = _get_or_make_hook_class(base)

        hook = hook_cls(
            segs=segs,
            anima_mod=anima_mod,
            weights_path=weights_path,
            strength=strength,
            start_percent=start_percent,
            end_percent=end_percent,
            cycle=cycle,
            control_images=control_images,
            image=image,
            cond_map=cond_map,
        )

        n = len(segs[1]) if segs and len(segs) > 1 else 0
        logger.info("%s built hook (v5: cache-friendly, live crop_region → ordered fallback%s) for "
                    "%d segment(s), lllite=%s, cycle=%d",
                    _TAG,
                    " + per-tile mixer conditioning" if cond_map else "",
                    n, lllite_name, int(cycle))
        return (hook,)


NODE_CLASS_MAPPINGS = {
    "BMKAnimaLLLiteSEGSHook": BMKAnimaLLLiteSEGSHook,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "BMKAnimaLLLiteSEGSHook": "BMK Anima LLLite Per-SEGS Hook",
}
