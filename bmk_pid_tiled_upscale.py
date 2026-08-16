"""BMK PiD Tiled Upscale — NVIDIA PiD(PixelDiT) 4배 업스케일을 타일 루프로 수행.

PiD 체크포인트(`pid_*_1024_to_4096_*`)는 네이티브 4배 고정이며, 이름의 "1024" 는
학습 시 입력 규모(≈1MP)를 뜻한다. 이 규모를 크게 벗어난 입력을 한 번에 밀어 넣으면
(a) 픽셀 공간 텐서가 폭발하고 (b) 학습 분포 밖 형상이라 색이 튄다. 코어 노드의
ContextWindows 로 캔버스를 나눠 먹이는 방식은 높이 축만 자르고 조건(lq_latent)은
창별로 분할되지 않아, 폭이 큰 이미지에서 중앙부 과채도/보라 편이가 생긴다.

이 모듈은 그 대신 **명시적 타일 루프**를 돌린다. 모든 타일이 예외 없이 정확히
`pid_input_size` 정사각으로 PiD 에 들어가므로 항상 학습 분포 정중앙이고, VRAM 피크는
타일 하나(= pid_input_size × 4)로 고정되어 입력 해상도와 무관해진다. 누적 캔버스는
CPU 에 두므로 4K 이상 입력도 처리된다.

타일 기하 (핵심 규약)
---------------------
사용자는 "PiD 가 실제로 보는 크기"를 지정하고, 코어는 거기서 역산된다.

    core   = pid_input_size - 2 × context_pixel
    stride = core - core_overlap

    ┌───────────────── pid_input_size (예: 1024) ─────────────────┐
    │ context │              core (예: 768)              │ context │
    └─────────┴──────────────────────────────────────────┴─────────┘
                ↑ 생성에만 쓰이고 페이스트에서 제외 ↑

- context_pixel : 이웃 컨텍스트를 주기 위한 여유. 생성에는 쓰되 결과에서는 버린다.
- core_overlap  : 코어끼리 겹치는 폭. 이 구간에서만 선형 feather 로 블렌드한다.
                  0 이면 코어가 정확히 맞붙는 완전 분할(이중 처리 전무).

경계 처리는 BMK Flexible Tile SEGS 의 virtual_canvas 와 같은 사고방식이다. 좌상단
기준 고정 스트라이드로 코어를 깔고, 모자란 부분은 이미지 밖으로 가상 캔버스를 확장해
`padding_fill` 로 채운다. 마지막 타일을 가장자리에 스냅하지 않으므로 타일 크기가
끝까지 균일하고, 숨은 겹침이 생기지 않는다. 최종 출력에서 패딩분은 ×4 비례로
잘라낸다(BMK Virtual Canvas Crop 과 동일 산식, 노드 내부에서 처리).

과채도 억제
-----------
1. `degrade_sigma` 기본 0.06 — PiD 를 "재생성"이 아니라 "충실한 디코더"로 쓴다.
2. 코어 페이스트 — 각 픽셀은 단 한 타일에서만 온다(겹침 밴드 이중 처리 제거).
   BMK SEGS Core Mask 와 같은 원리를 타일 합성 단계에 내재화했다.
3. per-tile color match — 타일 결과를 자기 소스 타일의 색 통계로 되돌린다.
   BMK Klein Reference Hook 의 `_match_color` 와 같은 계열이되, 출력을 소스
   해상도로 area 다운샘플한 뒤 통계를 비교한다. PiD 가 새로 만든 고주파 디테일이
   통계에 섞여 억제되는 것을 막기 위함이다(주파수 공정 비교).
4. 전역 톤은 BMK Wavelet Tone Restore 에 맡긴다(이 노드에 내장하지 않음).

입력 경로
---------
- `latent` 연결 : 이미 인코딩된 것을 그대로 타일링. VAE 왕복이 없어 가장 정확·빠름.
                  패딩은 latent 도메인에서 replicate/reflect.
- `image` 연결  : 이미지 단계에서 패딩한 뒤 타일별로 `vae_encode` 인코딩.
                  KSampler 직결이 아닌 임의 이미지 업스케일용.
둘 다 연결되면 `latent` 가 우선한다.

버전 이력
---------
v1 (2026-08)
- 최초 구현. BMKPiDLoader(BMK_PID_CTX) + BMKPiDTiledUpscale 2노드 구성.
- 로더를 분리해 ComfyUI 표준 노드 캐시가 모델·프롬프트 재사용을 처리하도록 했다
  (모듈 전역 캐시 불필요, 타일 파라미터만 바꿔도 모델 재로드 없음).
- latent_format 별 공간 배율(flux2 = 16×, 그 외 8×)을 분리하고 채널 수를 교차
  검증한다. 채널만 맞으면 조용히 망가지던 사고를 실행 시점에 잡는다.
"""

from __future__ import annotations

import logging
import math
import os

import numpy as np
import torch
import torch.nn.functional as F

import folder_paths
import node_helpers
import comfy.latent_formats
import comfy.model_management as mm

import nodes as core_nodes
from comfy_extras import nodes_custom_sampler

try:
    from comfy.utils import ProgressBar
except Exception:  # pragma: no cover
    ProgressBar = None

logger = logging.getLogger(__name__)

_TAG_LOADER = "[ComfyUI_BMK_Nodes::PiDLoader]"
_TAG_UPSCALE = "[ComfyUI_BMK_Nodes::PiDTiledUpscale]"

_CTX_TYPE = "BMK_PID_CTX"

# PiD 는 네이티브 4배 고정 (릴리즈된 모든 체크포인트 공통)
_PID_SCALE = 4

_DEFAULT_PID_MODEL = "pid_1.5_qwenimage_1024_to_4096_4step_bf16.safetensors"
_DEFAULT_CLIP = "gemma_2_2b_it_elm_bf16.safetensors"
_DEFAULT_PID_VAE = "pixel_space"


# ─────────────────────────────────────────────────────────────────────────────
# 공용 헬퍼
# ─────────────────────────────────────────────────────────────────────────────
def _resolve_default(file_list, name):
    """file_list 에서 basename 이 name 인 항목을 찾아 반환(하위 폴더 대응)."""
    for f in file_list:
        if f.replace("\\", "/").split("/")[-1] == name:
            return f
    return file_list[0] if file_list else name


def _file_list(category, node_cls=None, input_key=None):
    """노드 클래스의 INPUT_TYPES 목록을 우선 사용하고, 실패 시 folder_paths 폴백."""
    if node_cls is not None and input_key is not None:
        try:
            return list(node_cls.INPUT_TYPES()["required"][input_key][0])
        except Exception:
            pass
    try:
        return list(folder_paths.get_filename_list(category))
    except Exception:
        return []


def _latent_spec(latent_format: str):
    """(latent_format 클래스, 공간 배율, 허용 채널 수) 를 반환.

    공간 배율 = latent 1 픽셀이 대응하는 이미지 픽셀 수. flux2(Klein) 계열만 16 이고
    나머지는 8 이다. 이 값을 8 로 고정하면 flux2 에서 목표 크기가 절반이 된다.
    """
    lf = str(latent_format)
    if lf == "qwenimage":
        cls = getattr(comfy.latent_formats, "Wan21", None)
        return cls, 8, (16,)
    if lf == "flux":
        return getattr(comfy.latent_formats, "Flux", None), 8, (16,)
    if lf == "flux2":
        return getattr(comfy.latent_formats, "Flux2", None), 16, (128,)
    if lf == "sd3":
        return getattr(comfy.latent_formats, "SD3", None), 8, (16,)
    if lf == "sdxl":
        return getattr(comfy.latent_formats, "SDXL", None), 8, (4,)
    raise ValueError(f"{_TAG_UPSCALE} 알 수 없는 latent_format: {latent_format}")


def _pid_conditioning(positive, samples, fmt_cls, degrade_sigma):
    """코어 PiDConditioning 과 동등: lq_latent / degrade_sigma 를 조건에 실어준다."""
    lq = fmt_cls().process_in(samples)
    if lq.ndim == 5:
        lq = lq[:, :, 0]
    sigma_t = torch.tensor([float(degrade_sigma)], dtype=torch.float32)
    return node_helpers.conditioning_set_values(
        positive, {"lq_latent": lq, "degrade_sigma": sigma_t}
    )


def _zero_out(conditioning):
    """ConditioningZeroOut 과 동등."""
    out = []
    for t in conditioning:
        d = t[1].copy()
        pooled = d.get("pooled_output")
        if pooled is not None:
            d["pooled_output"] = torch.zeros_like(pooled)
        out.append([torch.zeros_like(t[0]), d])
    return out


def _apply_model_shift(model, shift):
    """ModelSamplingSD3(shift) 적용. shift<=0 이면 통과."""
    if not shift or float(shift) <= 0.0:
        return model
    try:
        from comfy_extras.nodes_model_advanced import ModelSamplingSD3
    except Exception:
        logger.warning("%s ModelSamplingSD3 를 찾지 못해 model_shift 를 건너뜁니다.",
                       _TAG_UPSCALE)
        return model
    return ModelSamplingSD3().patch(model, float(shift))[0]


def _pad_nchw(x, pl, pr, pt, pb, mode):
    """[B,C,H,W] 패딩. reflect 는 크기 제약이 있어 초과 시 replicate 로 폴백."""
    if pl == pr == pt == pb == 0:
        return x
    if mode == "reflect":
        if pl < x.shape[3] and pr < x.shape[3] and pt < x.shape[2] and pb < x.shape[2]:
            return F.pad(x, (pl, pr, pt, pb), mode="reflect")
        logger.info("%s reflect 패딩 크기 제약 초과 → replicate 로 폴백", _TAG_UPSCALE)
        return F.pad(x, (pl, pr, pt, pb), mode="replicate")
    if mode == "edge":
        return F.pad(x, (pl, pr, pt, pb), mode="replicate")
    value = {"gray": 0.5, "black": 0.0, "white": 1.0}.get(mode, 0.0)
    return F.pad(x, (pl, pr, pt, pb), mode="constant", value=value)


def _pad_nhwc(img, pl, pr, pt, pb, mode):
    if pl == pr == pt == pb == 0:
        return img
    return _pad_nchw(img.movedim(-1, 1), pl, pr, pt, pb, mode).movedim(1, -1)


def _axis_starts(length, core, stride):
    """좌상단 기준 고정 스트라이드 코어 시작 좌표. 마지막 타일을 스냅하지 않는다."""
    if length <= core:
        return [0]
    n = math.ceil((length - core) / stride) + 1
    return [i * stride for i in range(n)]


def _axis_weight(length, lo, hi):
    """1D feather 가중치. lo/hi 는 각 끝에서 선형 전이시킬 폭(px)."""
    w = np.ones(length, dtype=np.float32)
    if lo > 0:
        lo = min(int(lo), length)
        w[:lo] *= (np.arange(lo, dtype=np.float32) + 1.0) / (lo + 1.0)
    if hi > 0:
        hi = min(int(hi), length)
        w[length - hi:] *= (np.arange(hi, 0, -1, dtype=np.float32)) / (hi + 1.0)
    return w


def _color_match(out_core, src_core, mode, strength):
    """타일 출력을 소스 타일의 색 통계로 되돌린다.

    출력을 소스 해상도로 area 다운샘플한 뒤 통계를 비교하므로, PiD 가 새로 만든
    고주파 디테일이 std 에 섞여 눌리는 일이 없다(주파수 공정 비교).

    - "mean"     : 채널별 평균만 맞춘다. 색 편이(보라 등)를 잡되 대비는 건드리지 않음.
    - "mean_std" : Reinhard 계열 전체 매칭. 채도 폭주가 심할 때.
    """
    if mode == "off" or float(strength) <= 0.0:
        return out_core

    o = out_core[..., :3]
    s = src_core[..., :3].to(o.device, dtype=o.dtype)

    o_ds = F.interpolate(
        o.movedim(-1, 1), size=(int(s.shape[1]), int(s.shape[2])), mode="area"
    ).movedim(1, -1)

    om = o_ds.mean(dim=(1, 2), keepdim=True)
    sm = s.mean(dim=(1, 2), keepdim=True)

    if mode == "mean_std":
        eps = 1e-5
        osd = o_ds.std(dim=(1, 2), keepdim=True)
        ssd = s.std(dim=(1, 2), keepdim=True)
        corrected = (o - om) * ((ssd + eps) / (osd + eps)) + sm
    else:
        corrected = o + (sm - om)

    st = float(strength)
    blended = (o * (1.0 - st) + corrected * st).clamp(0.0, 1.0)

    if out_core.shape[-1] > 3:
        return torch.cat([blended, out_core[..., 3:]], dim=-1)
    return blended


class _PiDContext(dict):
    """BMK_PID_CTX 컨테이너. 텍스트 프리뷰에서 텐서를 덤프하지 않는다."""

    def __repr__(self):  # pragma: no cover - 표시 전용
        return (f"<BMK_PID_CTX model={self.get('model_name')!r} "
                f"clip={self.get('clip_name')!r} prompt={self.get('prompt')!r}>")

    __str__ = __repr__


# ─────────────────────────────────────────────────────────────────────────────
# 로더
# ─────────────────────────────────────────────────────────────────────────────
class BMKPiDLoader:
    """PiD UNET + 텍스트 인코더 + pixel_space VAE 를 한 번에 준비해 CTX 로 내보낸다."""

    @classmethod
    def INPUT_TYPES(cls):
        unet_list = _file_list("diffusion_models", core_nodes.UNETLoader, "unet_name")
        clip_list = _file_list("text_encoders", core_nodes.CLIPLoader, "clip_name")
        vae_list = _file_list("vae", core_nodes.VAELoader, "vae_name") or [_DEFAULT_PID_VAE]

        return {
            "required": {
                "pid_model": (unet_list, {
                    "default": _resolve_default(unet_list, _DEFAULT_PID_MODEL),
                    "tooltip": "PiD 체크포인트. 생성에 쓴 백본의 latent 계열과 반드시 일치해야 "
                               "합니다(qwenimage / flux1 / flux2 / sd3 / sdxl)."}),
                "clip_name": (clip_list, {
                    "default": _resolve_default(clip_list, _DEFAULT_CLIP),
                    "tooltip": "PiD 전용 텍스트 인코더 (gemma_2_2b_it_elm)."}),
                "pid_vae": (vae_list, {
                    "default": _resolve_default(vae_list, _DEFAULT_PID_VAE),
                    "tooltip": "PiD 디코드용 VAE. pixel_space 만 동작합니다. "
                               "(입력 인코딩용 VAE 는 업스케일 노드 쪽에 연결하세요.)"}),
                "prompt": ("STRING", {
                    "default": "best quality", "multiline": True,
                    "tooltip": "업스케일 보조 프롬프트. 기본값 그대로 두어도 됩니다."}),
                "weight_dtype": (["default", "fp8_e4m3fn", "fp8_e4m3fn_fast", "fp8_e5m2"], {
                    "default": "default"}),
            }
        }

    RETURN_TYPES = (_CTX_TYPE,)
    RETURN_NAMES = ("pid_ctx",)
    FUNCTION = "doit"
    CATEGORY = "BMK/Image"
    DESCRIPTION = (
        "PiD 업스케일에 필요한 UNET·텍스트 인코더·pixel_space VAE 와 프롬프트 조건을 "
        "한 번에 준비해 BMK PiD Tiled Upscale 로 넘깁니다. 로더를 분리해 두었으므로 "
        "타일 파라미터만 바꿔 재실행할 때 모델이 다시 로드되지 않습니다."
    )
    SEARCH_ALIASES = [
        "pid loader", "pixeldit loader", "pid model", "pid context",
        "PiD 로더", "픽셀디트", "업스케일 로더",
    ]

    @classmethod
    def IS_CHANGED(cls, pid_model, clip_name, pid_vae, prompt, weight_dtype):
        """디스크의 파일이 조용히 교체된 경우에만 캐시를 무효화한다."""
        parts = []
        for category, name in (("diffusion_models", pid_model),
                               ("text_encoders", clip_name),
                               ("vae", pid_vae)):
            try:
                path = folder_paths.get_full_path(category, name)
                parts.append(str(os.path.getmtime(path)) if path else "-")
            except Exception:
                parts.append("-")
        return "|".join(parts) + f"|{prompt}|{weight_dtype}"

    def doit(self, pid_model, clip_name, pid_vae, prompt, weight_dtype):
        if "pixel_space" not in str(pid_vae):
            raise ValueError(
                f"{_TAG_LOADER} pid_vae 는 pixel_space 여야 합니다 (선택됨: {pid_vae}). "
                "PiD 는 픽셀 공간에서 디코드하는 모델이라 일반 VAE 를 고르면 채널/구조 "
                "불일치로 실패하거나 결과가 망가집니다. 입력 인코딩용 VAE(qwen_image_vae "
                "등)는 BMK PiD Tiled Upscale 의 vae_encode 입력에 연결하세요.")

        model = core_nodes.UNETLoader().load_unet(pid_model, weight_dtype)[0]
        clip = core_nodes.CLIPLoader().load_clip(clip_name, "pixeldit", "default")[0]
        vae = core_nodes.VAELoader().load_vae(pid_vae)[0]

        tokens = clip.tokenize(prompt)
        if hasattr(clip, "encode_from_tokens_scheduled"):
            positive = clip.encode_from_tokens_scheduled(tokens)
        else:  # 구버전 CLIP API 폴백
            cond, pooled = clip.encode_from_tokens(tokens, return_pooled=True)
            positive = [[cond, {"pooled_output": pooled}]]
        negative = _zero_out(positive)

        logger.info("%s ready: model=%s clip=%s vae=%s", _TAG_LOADER,
                    pid_model, clip_name, pid_vae)

        return (_PiDContext(
            model=model, vae=vae, positive=positive, negative=negative,
            model_name=pid_model, clip_name=clip_name, vae_name=pid_vae,
            prompt=prompt,
        ),)


# ─────────────────────────────────────────────────────────────────────────────
# 타일 업스케일
# ─────────────────────────────────────────────────────────────────────────────
class BMKPiDTiledUpscale:
    """PiD 4배 업스케일을 타일 루프로 수행. 입력 해상도 제한 없음."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "pid_ctx": (_CTX_TYPE, {
                    "tooltip": "BMK PiD Loader 출력"}),
                "latent_format": (["qwenimage", "flux", "flux2", "sd3", "sdxl"], {
                    "default": "qwenimage",
                    "tooltip": "입력 latent 의 계열. 생성 모델이 아니라 '인코딩에 쓴 VAE' 를 "
                               "따릅니다. 채널 수가 맞지 않으면 실행 시점에 막습니다."}),
                "pid_input_size": ("INT", {
                    "default": 1024, "min": 256, "max": 4096, "step": 16,
                    "tooltip": "PiD 에 실제로 들어가는 타일 크기(px). 체크포인트 학습 크기와 "
                               "맞추세요(1024_to_4096 → 1024). 코어는 여기서 역산됩니다."}),
                "context_pixel": ("INT", {
                    "default": 128, "min": 0, "max": 1024, "step": 16,
                    "tooltip": "코어 바깥 컨텍스트(px). 생성에는 쓰이고 결과에서는 버려집니다. "
                               "core = pid_input_size − 2 × context_pixel."}),
                "core_overlap": ("INT", {
                    "default": 64, "min": 0, "max": 512, "step": 16,
                    "tooltip": "코어끼리 겹치는 폭(px). 이 구간에서만 feather 블렌드. "
                               "0 = 완전 분할(이중 처리 전무, 대신 seam 이 보일 수 있음)."}),
                "padding_fill": (["reflect", "edge", "gray", "black", "white"], {
                    "default": "reflect",
                    "tooltip": "가상 캔버스(이미지 밖) 채움 방식. reflect 권장."}),
                "degrade_sigma": ("FLOAT", {
                    "default": 0.06, "min": 0.0, "max": 1.0, "step": 0.01,
                    "tooltip": "낮을수록 원본 충실(=색 편이 억제). 0.05~0.10 권장. "
                               "높이면 보정은 강해지지만 타일별 색 드리프트가 커집니다."}),
                "steps": ("INT", {"default": 4, "min": 1, "max": 20}),
                "seed": ("INT", {
                    "default": 42, "min": 0, "max": 0xffffffffffffffff,
                    "control_after_generate": True}),
                "seed_mode": (["same", "per_tile"], {
                    "default": "same",
                    "tooltip": "same: 모든 타일이 같은 시드(타일 간 일관성 우수, 권장). "
                               "per_tile: 타일마다 시드를 바꿔 반복 텍스처를 피함."}),
                "color_match": (["mean", "mean_std", "off"], {
                    "default": "mean",
                    "tooltip": "타일 출력을 자기 소스 타일의 색 통계로 되돌립니다. "
                               "mean: 색 편이만 보정(디테일 대비 보존). "
                               "mean_std: 채도 폭주까지 억제(대비가 약간 눌릴 수 있음)."}),
                "color_match_strength": ("FLOAT", {
                    "default": 1.0, "min": 0.0, "max": 1.0, "step": 0.05}),
                "model_shift": ("FLOAT", {
                    "default": 0.0, "min": 0.0, "max": 10.0, "step": 0.1,
                    "tooltip": "ModelSamplingSD3 shift. 0 = 미적용. PiD v1 워크플로우는 "
                               "1.5 를 썼습니다. 체크포인트에 따라 다르니 실측 권장."}),
                "accum_dtype": (["fp32", "fp16"], {
                    "default": "fp32",
                    "tooltip": "CPU 누적 캔버스 dtype. 초대형 입력에서 RAM 이 빠듯하면 fp16."}),
            },
            "optional": {
                "latent": ("LATENT", {
                    "tooltip": "KSampler 직결. 연결되면 image 보다 우선하며 VAE 왕복이 없습니다."}),
                "image": ("IMAGE", {
                    "tooltip": "임의 이미지 업스케일용. vae_encode 연결 필수."}),
                "vae_encode": ("VAE", {
                    "tooltip": "입력 인코딩용 VAE(생성 백본과 같은 것. 예: qwen_image_vae). "
                               "PiD 디코드용 pixel_space VAE 와 다릅니다."}),
            },
        }

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("image",)
    FUNCTION = "doit"
    CATEGORY = "BMK/Image"
    DESCRIPTION = (
        "PiD 업스케일을 타일 루프로 수행해 입력 해상도 제한 없이 4배 확대합니다. "
        "모든 타일이 정확히 pid_input_size 정사각으로 들어가 학습 분포를 벗어나지 않고, "
        "가상 캔버스 패딩 + 코어 페이스트 + per-tile color match 로 타일 경계의 "
        "과채도/이음새를 억제합니다. VRAM 피크는 타일 하나로 고정됩니다."
    )
    SEARCH_ALIASES = [
        "pid upscale", "pid tiled", "pixeldit upscale", "tiled 4x", "super resolution",
        "PiD 업스케일", "타일 업스케일", "4배 업스케일", "고해상도",
    ]

    # ── 기하 계산 ────────────────────────────────────────────────────────────
    @staticmethod
    def _geometry(pid_input_size, context_pixel, core_overlap, unit):
        s = max(unit * 2, (int(pid_input_size) // unit) * unit)
        c = max(0, (int(context_pixel) // unit) * unit)
        if s - 2 * c < unit:
            c = max(0, ((s - unit) // 2 // unit) * unit)
            logger.warning("%s context_pixel 이 과대하여 %dpx 로 축소했습니다.",
                           _TAG_UPSCALE, c)
        core = s - 2 * c
        ov = max(0, (int(core_overlap) // unit) * unit)
        if ov >= core:
            ov = max(0, core - unit)
            logger.warning("%s core_overlap 이 코어 이상이라 %dpx 로 축소했습니다.",
                           _TAG_UPSCALE, ov)
        return s, c, core, ov, core - ov

    # ── 타일 1장 처리 ────────────────────────────────────────────────────────
    def _sample_tile(self, ctx, model, sampler, sigmas, tile_lat, fmt_cls,
                     degrade_sigma, seed, out_side):
        positive = _pid_conditioning(ctx["positive"], tile_lat, fmt_cls, degrade_sigma)
        empty = torch.zeros((tile_lat.shape[0], 3, out_side, out_side),
                            device=mm.intermediate_device())
        out = nodes_custom_sampler.SamplerCustom().sample(
            model, True, int(seed), 1.0, positive, ctx["negative"],
            sampler, sigmas, {"samples": empty})[0]
        img = ctx["vae"].decode(out["samples"])
        if img.ndim == 5:
            img = img.reshape(-1, img.shape[-3], img.shape[-2], img.shape[-1])
        return img.clamp(0.0, 1.0)

    # ── 본체 ─────────────────────────────────────────────────────────────────
    def doit(self, pid_ctx, latent_format, pid_input_size, context_pixel, core_overlap,
             padding_fill, degrade_sigma, steps, seed, seed_mode,
             color_match, color_match_strength, model_shift, accum_dtype,
             latent=None, image=None, vae_encode=None):

        if not isinstance(pid_ctx, dict) or "model" not in pid_ctx:
            raise ValueError(f"{_TAG_UPSCALE} pid_ctx 가 유효하지 않습니다. "
                             "BMK PiD Loader 의 출력을 연결하세요.")

        fmt_cls, lf_factor, allowed_ch = _latent_spec(latent_format)
        if fmt_cls is None:
            raise ValueError(
                f"{_TAG_UPSCALE} 이 ComfyUI 버전에는 '{latent_format}' latent format 이 "
                "없습니다. ComfyUI 를 업데이트하거나 다른 형식을 고르세요.")

        unit = max(16, lf_factor)
        S, C, core, ov, stride = self._geometry(
            pid_input_size, context_pixel, core_overlap, unit)

        # ── 입력 정규화: latent 우선, 없으면 image → 타일별 인코딩 ──
        use_latent = latent is not None
        if use_latent:
            src_lat = latent["samples"]
            if src_lat.ndim == 5:
                src_lat = src_lat[:, :, 0]
            channels = int(src_lat.shape[1])
            if channels not in allowed_ch:
                raise ValueError(
                    f"{_TAG_UPSCALE} latent_format='{latent_format}' 은 채널 "
                    f"{allowed_ch} 를 기대하는데 입력은 {channels}채널입니다. "
                    "인코딩에 쓴 VAE 와 latent_format 을 맞추세요.")
            batch = int(src_lat.shape[0])
            src_h = int(src_lat.shape[-2]) * lf_factor
            src_w = int(src_lat.shape[-1]) * lf_factor
            src_img = None
        else:
            if image is None:
                raise ValueError(
                    f"{_TAG_UPSCALE} latent 또는 image 중 하나는 연결해야 합니다.")
            if vae_encode is None:
                raise ValueError(
                    f"{_TAG_UPSCALE} image 경로에는 vae_encode(입력 인코딩용 VAE) 가 "
                    "필요합니다. 생성 백본과 같은 계열의 VAE 를 연결하세요.")
            src_lat = None
            batch = int(image.shape[0])
            src_h = int(image.shape[1])
            src_w = int(image.shape[2])
            src_img = image

        # ── 타일 배치 + 가상 캔버스 패딩량 ──
        xs = _axis_starts(src_w, core, stride)
        ys = _axis_starts(src_h, core, stride)
        pad_l = pad_t = C
        pad_r = max(0, xs[-1] + core + C - src_w)
        pad_b = max(0, ys[-1] + core + C - src_h)
        pad_w = src_w + pad_l + pad_r
        pad_h = src_h + pad_t + pad_b

        out_side = S * _PID_SCALE
        canvas_w = pad_w * _PID_SCALE
        canvas_h = pad_h * _PID_SCALE
        n_tiles = len(xs) * len(ys)

        logger.info(
            "%s src %dx%d (%s, %dch/%dx) → tiles %dx%d = %d "
            "[pid_input %d, core %d, context %d, overlap %d, stride %d] "
            "→ out %dx%d",
            _TAG_UPSCALE, src_w, src_h, latent_format,
            allowed_ch[0], lf_factor, len(xs), len(ys), n_tiles,
            S, core, C, ov, stride, src_w * _PID_SCALE, src_h * _PID_SCALE)

        # ── 모델/샘플러 준비 (루프 밖에서 1회) ──
        model = _apply_model_shift(pid_ctx["model"], model_shift)
        sigmas = nodes_custom_sampler.BasicScheduler().get_sigmas(
            model, "sgm_uniform", int(steps), 1.0)[0]
        sampler = nodes_custom_sampler.KSamplerSelect().get_sampler("lcm")[0]

        accum_torch_dtype = torch.float16 if accum_dtype == "fp16" else torch.float32
        use_weight = ov > 0
        pbar = ProgressBar(n_tiles * batch) if ProgressBar is not None else None

        results = []
        for b in range(batch):
            # 배치 1장 분량의 패딩된 소스 준비
            if use_latent:
                lat_b = src_lat[b:b + 1]
                padded_lat = _pad_nchw(
                    lat_b, pad_l // lf_factor, pad_r // lf_factor,
                    pad_t // lf_factor, pad_b // lf_factor, padding_fill)
                padded_img = None
            else:
                padded_img = _pad_nhwc(
                    src_img[b:b + 1], pad_l, pad_r, pad_t, pad_b, padding_fill)
                padded_lat = None

            canvas = torch.zeros((canvas_h, canvas_w, 3), dtype=accum_torch_dtype)
            weight = (torch.zeros((canvas_h, canvas_w, 1), dtype=torch.float32)
                      if use_weight else None)

            for ri, y in enumerate(ys):
                for ci, x in enumerate(xs):
                    mm.throw_exception_if_processing_interrupted()

                    # 패딩 좌표계에서 타일 crop 시작 = 코어 시작(pad_l == C 이므로)
                    if use_latent:
                        ls = lf_factor
                        tile_lat = padded_lat[
                            :, :, y // ls: y // ls + S // ls,
                            x // ls: x // ls + S // ls]
                    else:
                        tile_img = padded_img[:, y: y + S, x: x + S, :]
                        tile_lat = vae_encode.encode(tile_img[..., :3])

                    tile_seed = int(seed) + (ri * len(xs) + ci if seed_mode == "per_tile" else 0)

                    try:
                        out_tile = self._sample_tile(
                            pid_ctx, model, sampler, sigmas, tile_lat, fmt_cls,
                            degrade_sigma, tile_seed, out_side)
                    except mm.OOM_EXCEPTION:
                        logger.warning("%s 타일 r%dc%d OOM → 캐시 정리 후 1회 재시도",
                                       _TAG_UPSCALE, ri, ci)
                        mm.soft_empty_cache()
                        try:
                            out_tile = self._sample_tile(
                                pid_ctx, model, sampler, sigmas, tile_lat, fmt_cls,
                                degrade_sigma, tile_seed, out_side)
                        except mm.OOM_EXCEPTION as exc:
                            raise RuntimeError(
                                f"{_TAG_UPSCALE} 타일 하나({S}→{out_side}px)조차 VRAM 에 "
                                f"들어가지 않습니다. pid_input_size 를 낮추세요"
                                f"(예: 768 또는 512). 단, 체크포인트 학습 크기에서 "
                                f"멀어질수록 품질이 떨어집니다.") from exc

                    # 코어 영역만 추출 (컨텍스트는 버림)
                    cs = C * _PID_SCALE
                    ce = (C + core) * _PID_SCALE
                    core_out = out_tile[:, cs:ce, cs:ce, :]

                    # per-tile color match
                    if color_match != "off":
                        if use_latent:
                            src_core = vae_encode.decode(tile_lat) if vae_encode is not None else None
                            if src_core is not None:
                                if src_core.ndim == 5:
                                    src_core = src_core.reshape(
                                        -1, src_core.shape[-3], src_core.shape[-2],
                                        src_core.shape[-1])
                                src_core = src_core[:, C:C + core, C:C + core, :]
                        else:
                            src_core = tile_img[:, C:C + core, C:C + core, :]
                        if src_core is None:
                            if ri == 0 and ci == 0 and b == 0:
                                logger.warning(
                                    "%s color_match 를 쓰려면 latent 경로에서도 vae_encode "
                                    "연결이 필요합니다(소스 타일 디코드용). 건너뜁니다.",
                                    _TAG_UPSCALE)
                        else:
                            core_out = _color_match(
                                core_out, src_core, color_match, color_match_strength)

                    # 코어 페이스트 (겹침 밴드에만 feather)
                    ch = core * _PID_SCALE
                    oy = (y + C) * _PID_SCALE
                    ox = (x + C) * _PID_SCALE
                    patch = core_out[0, :, :, :3].to("cpu", dtype=accum_torch_dtype)

                    if use_weight:
                        fw = ov * _PID_SCALE
                        wy = _axis_weight(ch, fw if ri > 0 else 0,
                                          fw if ri < len(ys) - 1 else 0)
                        wx = _axis_weight(ch, fw if ci > 0 else 0,
                                          fw if ci < len(xs) - 1 else 0)
                        w2d = torch.from_numpy(np.outer(wy, wx)).unsqueeze(-1)
                        canvas[oy:oy + ch, ox:ox + ch, :] += (
                            patch * w2d.to(accum_torch_dtype))
                        weight[oy:oy + ch, ox:ox + ch, :] += w2d
                    else:
                        canvas[oy:oy + ch, ox:ox + ch, :] = patch

                    del out_tile, core_out, patch
                    if pbar is not None:
                        pbar.update(1)

            if use_weight:
                weight.clamp_(min=1e-6)
                canvas.div_(weight.to(canvas.dtype))
                del weight

            # 가상 캔버스 패딩 제거 (×4 비례)
            y0 = pad_t * _PID_SCALE
            x0 = pad_l * _PID_SCALE
            final = canvas[y0: y0 + src_h * _PID_SCALE,
                           x0: x0 + src_w * _PID_SCALE, :]
            results.append(final.float().clamp(0.0, 1.0).unsqueeze(0))
            del canvas

        return (torch.cat(results, dim=0),)


NODE_CLASS_MAPPINGS = {
    "BMKPiDLoader": BMKPiDLoader,
    "BMKPiDTiledUpscale": BMKPiDTiledUpscale,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "BMKPiDLoader": "BMK PiD Loader",
    "BMKPiDTiledUpscale": "BMK PiD Tiled Upscale",
}
