"""BMK Wavelet Tone Restore.

타일 기반 부분 디노이즈(디테일러) 업스케일에서 손실되는 저주파 계조 —
대규모 명암 그라데이션(예: 적란운의 풍부한 음영)과 전역 색 분포 — 를
원본(reference) 이미지로부터 wavelet 주파수 분해로 이식하여 복원하는
후처리 노드. ColorMatch(Reinhard) 단계를 대체한다.

배경
----
denoise 0.35~0.5 의 타일 재샘플링은 고주파 디테일을 늘리는 대신, 모델
prior 의 평균 회귀로 저·중주파 계조를 중간톤 쪽으로 수축시킨다. 각 타일이
이미지 전체의 조명 구배를 볼 수 없어 타일 로컬 통계로 톤이 재정규화되는
효과도 겹친다. 전역 통계(평균/표준편차)만 되돌리는 Reinhard ColorMatch 는
이 손실을 공간 정보 없이 전역 스트레치로 가리기 때문에, 이미 좁게 수축된
하이라이트 클러스터가 255 밖으로 밀려 클리핑된다(히스토그램 우단 스파이크).

이 노드는 통계가 아니라 "저주파 필드 자체"를 옮긴다:

    출력 = image 의 고주파 (디테일러가 만든 선명한 디테일)
         + reference 의 저주파 (원본의 톤·색·대규모 그라데이션)

저주파 정보는 원본에 온전히 존재하고 확대(bicubic)로도 사실상 손실 없이
전달되므로, 이 방식은 해당 손실에 대해 실용적으로 완전한 복원이 된다.
전역 스트레치가 없어 하이라이트 클리핑도 발생하지 않는다.

구현은 StableSR 의 wavelet color fix 와 동일 계열: 3x3 가우시안 커널을
dilation=2^i 로 levels 회 반복 적용해 저주파를 분리한다. 다단계 분해라
단일 대형 가우시안 블러 스왑 대비 고대비 경계의 헤일로가 크게 완화된다.

옵션
----
- mode
  * "rgb"            : 색+계조를 모두 이식. ColorMatch 완전 대체. [기본]
  * "luminance_only" : 휘도(Y, BT.709) 저주파만 이식하고 색은 image 것을
                       유지. 디테일러가 만든 색감을 살리고 싶을 때.
- levels : wavelet 분해 단계 수 = 저주파/고주파 컷오프. 0 이면 해상도
  기반 자동(= round(log2(min(H,W)/16)); 512→5, 1024→6, 2048~2496→7,
  4096→8). 클수록 더 넓은 대역(더 미세한 구조까지)을 reference 가 지배
  → 원본 톤에 충실하지만, 과하면 디테일러의 중간 스케일 개선까지 덮는다.
  작으면 가장 굵은 조명 구배만 이식된다.
- strength : 저주파 이식 강도. 1.0 = 완전 이식, 0.0 = 통과.
  1.0 초과는 외삽(원본보다 계조를 더 벌림) — 과용 주의.

출력
----
- image         : 복원 결과.
- delta_preview : 이식된 저주파 차이의 시각화(중간 회색 = 변화 없음,
  밝음 = reference 가 더 밝음, 게인 4: ±0.125 → 흑~백). levels 튜닝용.

배선
----
    [원본 T2I 출력 (예: 1248x1824)] ─────────────→ reference
        (해상도가 다르면 노드가 자동으로 bicubic 리사이즈)
    [디테일러 최종 출력 (예: 2496x3648)] ────────→ image
    → 기존 ColorMatch(Reinhard) 노드는 제거한다.
    virtual_canvas 파이프라인이라면 BMK Virtual Canvas Crop (Restore)
    "이후"(원본 좌표계 복원 후)에 연결한다.

v1: 최초 구현 — rgb / luminance_only 모드, 해상도 기반 자동 levels,
    strength 블렌드, reference 자동 리사이즈(배치 포함), 알파 채널 통과,
    GPU OOM 시 CPU 폴백, delta_preview 진단 출력.
"""

from __future__ import annotations

import logging
import math

import torch
import torch.nn.functional as F

logger = logging.getLogger(__name__)

_TAG = "[ComfyUI_BMK_Nodes::WaveletToneRestore]"

# 3x3 가우시안 근사 커널 ([1,2,1]⊗[1,2,1] / 16) — StableSR wavelet blur 와 동일.
_KERNEL_3X3 = torch.tensor(
    [
        [0.0625, 0.125, 0.0625],
        [0.125, 0.25, 0.125],
        [0.0625, 0.125, 0.0625],
    ],
    dtype=torch.float32,
)


# ─────────────────────────────────────────────────────────────────────────────
# 디바이스 / 헬퍼
# ─────────────────────────────────────────────────────────────────────────────
def _pick_device() -> torch.device:
    """ComfyUI 연산 디바이스. comfy 부재(단위 테스트 등) 시 cuda/cpu 자동."""
    try:
        from comfy import model_management  # ComfyUI 코어 (항상 존재)

        return model_management.get_torch_device()
    except Exception:
        return torch.device("cuda") if torch.cuda.is_available() else torch.device("cpu")


def _wavelet_blur(x: torch.Tensor, radius: int) -> torch.Tensor:
    """(B,C,H,W) 텐서에 dilation=radius 의 3x3 depthwise 가우시안 적용."""
    c = int(x.shape[1])
    k = _KERNEL_3X3.to(device=x.device, dtype=x.dtype)
    k = k[None, None].repeat(c, 1, 1, 1)  # (C,1,3,3) depthwise
    x = F.pad(x, (radius, radius, radius, radius), mode="replicate")
    return F.conv2d(x, k, groups=c, dilation=radius)


def _low_frequency(x: torch.Tensor, levels: int) -> torch.Tensor:
    """dilation 1,2,4,…,2^(levels-1) 순차 블러 → 최종 저주파 성분."""
    for i in range(levels):
        x = _wavelet_blur(x, 2 ** i)
    return x


def _rgb_to_ycbcr(x: torch.Tensor):
    """(B,3,H,W) [0,1] → (Y, Cb, Cr)  — BT.709."""
    r, g, b = x[:, 0:1], x[:, 1:2], x[:, 2:3]
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    cb = (b - y) / 1.8556
    cr = (r - y) / 1.5748
    return y, cb, cr


def _ycbcr_to_rgb(y: torch.Tensor, cb: torch.Tensor, cr: torch.Tensor) -> torch.Tensor:
    r = y + 1.5748 * cr
    b = y + 1.8556 * cb
    g = (y - 0.2126 * r - 0.0722 * b) / 0.7152
    return torch.cat([r, g, b], dim=1)


def _auto_levels(h: int, w: int) -> int:
    """해상도 기반 기본 levels: round(log2(min(H,W)/16)), [3,10] 클램프.

    512→5, 1024→6, 2048~2496→7, 4096→8. (StableSR 기본 5 는 512px 기준이라
    고해상도에서는 컷오프가 너무 좁아 구름 스케일 그라데이션이 이식되지 않음.)
    """
    return int(max(3, min(10, round(math.log2(max(16, min(h, w)) / 16.0)))))


def _max_levels_for(h: int, w: int) -> int:
    """replicate 패딩 제약(패딩 < 해당 축 길이)을 만족하는 최대 levels."""
    m = min(h, w)
    if m <= 2:
        return 1
    return int(math.floor(math.log2(m - 1))) + 1  # 최대 dilation 2^(lv-1) < m


def _nhwc_to_nchw(x: torch.Tensor) -> torch.Tensor:
    return x.permute(0, 3, 1, 2).contiguous()


def _nchw_to_nhwc(x: torch.Tensor) -> torch.Tensor:
    return x.permute(0, 2, 3, 1).contiguous()


# ─────────────────────────────────────────────────────────────────────────────
# 핵심 연산
# ─────────────────────────────────────────────────────────────────────────────
def _restore(content: torch.Tensor, ref: torch.Tensor, levels: int, mode: str,
             strength: float, device: torch.device):
    """content/ref: (B,3,H,W) float32 [0,1], 동일 크기.

    반환: (out, preview, (mean|Δ|, max|Δ|))  — out/preview 는 CPU 텐서.
    항등식: out = content + strength * (low(ref) - low(content))
          = content 의 고주파 + [low(content)↔low(ref) 를 strength 로 보간]
    """
    content = content.to(device)
    ref = ref.to(device)

    if mode == "luminance_only":
        y_c, cb, cr = _rgb_to_ycbcr(content)
        y_r, _, _ = _rgb_to_ycbcr(ref)
        delta = _low_frequency(y_r, levels) - _low_frequency(y_c, levels)
        y_out = y_c + strength * delta
        out = _ycbcr_to_rgb(y_out, cb, cr)
        delta_vis = delta.repeat(1, 3, 1, 1)
    else:  # "rgb"
        delta = _low_frequency(ref, levels) - _low_frequency(content, levels)
        out = content + strength * delta
        delta_vis = delta

    stats = (float(delta.abs().mean()), float(delta.abs().max()))
    out = out.clamp(0.0, 1.0).cpu()
    preview = (0.5 + delta_vis * 4.0).clamp(0.0, 1.0).cpu()
    return out, preview, stats


# ─────────────────────────────────────────────────────────────────────────────
# ComfyUI 노드
# ─────────────────────────────────────────────────────────────────────────────
class BMKWaveletToneRestore:
    DESCRIPTION = (
        "타일 디테일링(부분 디노이즈)으로 손실된 저주파 계조·색 분포를 원본"
        "(reference)에서 wavelet 분해로 이식해 복원합니다. ColorMatch(Reinhard) "
        "대체용 — 전역 스트레치가 없어 하이라이트 클리핑이 생기지 않습니다."
    )
    SEARCH_ALIASES = [
        "wavelet color fix", "frequency separation", "tone restore",
        "color match", "계조 복원", "저주파 이식", "색 보정", "톤 복원",
    ]

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE", {
                    "tooltip": "고주파 소스 = 디테일러 최종 출력(업스케일 결과). "
                               "virtual_canvas 사용 시 BMK Virtual Canvas Crop 이후를 연결"}),
                "reference": ("IMAGE", {
                    "tooltip": "저주파 소스 = 원본(T2I) 이미지. 해상도가 다르면 "
                               "자동으로 bicubic 리사이즈됨"}),
                "mode": (["rgb", "luminance_only"], {
                    "default": "rgb",
                    "tooltip": "rgb: 색+계조 모두 이식(ColorMatch 완전 대체). "
                               "luminance_only: 휘도(Y) 계조만 이식, 색은 image 유지"}),
                "levels": ("INT", {"default": 0, "min": 0, "max": 12,
                    "tooltip": "wavelet 분해 단계 = 저/고주파 컷오프. 0 = 해상도 자동 "
                               "(512→5, 1024→6, 2048~2496→7, 4096→8). 클수록 더 미세한 "
                               "구조까지 reference 톤이 지배"}),
                "strength": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 2.0, "step": 0.05,
                    "tooltip": "저주파 이식 강도. 1.0 = 완전 이식, 0 = 통과. "
                               ">1.0 은 외삽(계조 과장) — 과용 주의"}),
            },
        }

    RETURN_TYPES = ("IMAGE", "IMAGE")
    RETURN_NAMES = ("image", "delta_preview")
    OUTPUT_TOOLTIPS = (
        "복원 결과 (image 의 고주파 + reference 의 저주파)",
        "이식된 저주파 차이 시각화 (중간 회색 = 변화 없음, 게인 4). levels 튜닝용",
    )
    FUNCTION = "doit"
    CATEGORY = "BMK/Image"

    def doit(self, image, reference, mode, levels, strength):
        image = torch.as_tensor(image)
        reference = torch.as_tensor(reference)
        if image.ndim != 4 or image.shape[-1] < 3:
            raise ValueError(f"{_TAG} image 는 (B,H,W,3+) IMAGE 여야 합니다: "
                             f"{tuple(image.shape)}")
        if reference.ndim != 4 or reference.shape[-1] < 3:
            raise ValueError(f"{_TAG} reference 는 (B,H,W,3+) IMAGE 여야 합니다: "
                             f"{tuple(reference.shape)}")

        B, H, W, C = (int(v) for v in image.shape)

        # RGB 3채널만 처리, 알파(있다면)는 통과.
        content = image[..., :3].float()
        alpha = image[..., 3:4].float() if C > 3 else None
        ref = reference[..., :3].float()

        # 배치 정합 (reference 부족 시 반복, 초과 시 절단)
        rb = int(ref.shape[0])
        if rb != B:
            if rb > B:
                ref = ref[:B]
            else:
                ref = ref.repeat(math.ceil(B / rb), 1, 1, 1)[:B]
            logger.info("%s reference 배치 %d → image 배치 %d 에 맞춤", _TAG, rb, B)

        content = _nhwc_to_nchw(content)
        ref = _nhwc_to_nchw(ref)

        # 해상도 정합: reference 를 image 크기로 (저주파 소스이므로 bicubic 충분)
        rh, rw = int(ref.shape[-2]), int(ref.shape[-1])
        if (rh, rw) != (H, W):
            logger.info("%s reference %dx%d → %dx%d bicubic 리사이즈", _TAG, rw, rh, W, H)
            ref = F.interpolate(ref, size=(H, W), mode="bicubic",
                                align_corners=False).clamp(0.0, 1.0)

        # levels 결정 (0 = 자동) + replicate 패딩 제약 클램프
        lv = int(levels) if int(levels) > 0 else _auto_levels(H, W)
        if int(levels) <= 0:
            logger.info("%s levels 자동 결정: %d (min(H,W)=%d)", _TAG, lv, min(H, W))
        max_lv = _max_levels_for(H, W)
        if lv > max_lv:
            logger.warning("%s levels=%d 는 이미지 크기 대비 과대 → %d 로 클램프 "
                           "(최대 dilation 이 이미지보다 커질 수 없음)", _TAG, lv, max_lv)
            lv = max_lv

        device = _pick_device()
        try:
            out, preview, stats = _restore(content, ref, lv, mode, float(strength), device)
        except RuntimeError as e:
            if "out of memory" not in str(e).lower() or getattr(device, "type", "") == "cpu":
                raise
            logger.warning("%s GPU 메모리 부족 → CPU 로 폴백합니다.", _TAG)
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            out, preview, stats = _restore(content, ref, lv, mode, float(strength),
                                           torch.device("cpu"))

        out = _nchw_to_nhwc(out)
        preview = _nchw_to_nhwc(preview)
        if alpha is not None:
            out = torch.cat([out, alpha.cpu()], dim=-1)

        logger.info("%s mode=%s levels=%d strength=%.2f → 저주파 Δ mean=%.4f max=%.4f "
                    "(%dx%d, batch %d)", _TAG, mode, lv, float(strength),
                    stats[0], stats[1], W, H, B)
        return (out, preview)


NODE_CLASS_MAPPINGS = {
    "BMKWaveletToneRestore": BMKWaveletToneRestore,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "BMKWaveletToneRestore": "BMK Wavelet Tone Restore",
}
