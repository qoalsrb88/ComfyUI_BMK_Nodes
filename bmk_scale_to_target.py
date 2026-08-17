"""BMK Scale To Target — "원본 대비 N배"로 최종 크기를 지정하는 리사이즈 노드.

배경
----
PiD 업스케일처럼 배율이 모델에 고정된 파이프라인에서는, 최종 저장 배율을
`ImageScaleBy` 의 `scale_by` 로 직접 지정하기가 불편하다. 원본 대비 2배를 원하면
PiD 가 4배를 내놓으므로 0.5 를 넣어야 하는데, 이 0.5 라는 숫자는

  * "PiD 출력이 정확히 원본의 4배"라는 가정에 의존하고,
  * 중간에 리사이즈가 하나 끼거나 배율이 다른 체크포인트로 바꾸면 조용히 어긋나며,
  * 사람이 매번 나눗셈을 해야 한다.

이 노드는 원본과 업스케일 결과의 **실제 크기를 재서** 배율을 역산한다. 가정이
없으므로 파이프라인이 바뀌어도 따라간다.

    실측 배율   = upscaled.width / source.width
    유효 배율   = min(target_scale, max_scale)
    출력 크기   = round(source 크기 × 유효 배율)
    scale_by    = 유효 배율 / 실측 배율

`max_scale` 은 사용자가 지정한다. PiD 계열이면 4.0(모델 네이티브 배율)이 자연스럽고,
그 위는 업스케일이 아니라 보간 확대라 정보량이 늘지 않는다. 다른 파이프라인
(ESRGAN 2배, SeedVR2 등)에서는 그 배율에 맞춰 바꾸면 된다.

동일 크기 바이패스
------------------
목표 크기가 이미 입력 크기와 같으면(예: target 4.0 = PiD 네이티브 배율) 리사이즈를
건너뛰고 입력 텐서를 그대로 내보낸다. ComfyUI 의 `ImageScaleBy` 는 `scale_by=1.0`
이어도 lanczos 리샘플을 수행해서, 16384² 같은 크기에서는 시간이 낭비되고 결과가
미세하게 소프트해진다. `passthrough_px` 로 허용 오차(px)를 준다 — 반올림으로 ±1px
차이가 나는 경우까지 통과시키기 위한 것이며, 0 으로 두면 정확히 일치할 때만 통과한다.

source_image 미연결 시
----------------------
`target_scale` 이 **입력 이미지 자신** 기준 배율이 된다(= 캡이 붙은 ImageScaleBy).
연결 시에는 **원본** 기준 배율이다.

출력
----
- image     : 리사이즈(또는 바이패스) 결과
- scale_by  : 입력 이미지에 실제로 적용된 배율. 진단·기록용
- width     : 출력 폭
- height    : 출력 높이
- info      : 한 줄 요약. 실측 배율이 예상과 다를 때 바로 눈에 띈다

v1 (2026-08)
- 최초 구현. 실측 배율 역산, max_scale 사용자 지정, 동일 크기 바이패스,
  가로/세로 실측 배율 불일치 경고, GPU OOM 시 CPU 폴백.
"""

from __future__ import annotations

import logging

import torch

import comfy.utils

try:
    import comfy.model_management as mm
except Exception:  # pragma: no cover
    mm = None

logger = logging.getLogger(__name__)

_TAG = "[ComfyUI_BMK_Nodes::ScaleToTarget]"

_METHODS = ["lanczos", "bicubic", "bilinear", "area", "nearest-exact"]


def _resize(image, width, height, method):
    """[B,H,W,C] 리사이즈. GPU OOM 이면 CPU 로 폴백한다."""
    def run(x):
        return comfy.utils.common_upscale(
            x.movedim(-1, 1), width, height, method, "disabled").movedim(1, -1)

    try:
        return run(image)
    except Exception as exc:
        oom = mm.OOM_EXCEPTION if mm is not None else torch.cuda.OutOfMemoryError
        if not isinstance(exc, oom):
            raise
        logger.warning("%s 리사이즈 중 OOM → CPU 로 폴백합니다.", _TAG)
        if mm is not None:
            mm.soft_empty_cache()
        return run(image.cpu())


class BMKScaleToTarget:
    """원본 대비 목표 배율로 리사이즈. 배율은 실측으로 역산하고 상한을 씌운다."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE", {
                    "tooltip": "리사이즈할 이미지(업스케일 결과)."}),
                "target_scale": ("FLOAT", {
                    "default": 2.0, "min": 0.01, "max": 64.0, "step": 0.05,
                    "tooltip": "원본 대비 최종 배율. source_image 를 연결하지 않으면 "
                               "입력 이미지 자신 기준 배율이 됩니다."}),
                "max_scale": ("FLOAT", {
                    "default": 4.0, "min": 0.01, "max": 64.0, "step": 0.05,
                    "tooltip": "배율 상한. 업스케일러의 네이티브 배율에 맞추세요"
                               "(PiD = 4.0). 그 위는 업스케일이 아니라 보간 확대라 "
                               "정보량이 늘지 않습니다."}),
                "upscale_method": (_METHODS, {
                    "default": "lanczos",
                    "tooltip": "축소에는 lanczos 가 무난합니다."}),
                "passthrough_px": ("INT", {
                    "default": 1, "min": 0, "max": 64,
                    "tooltip": "목표 크기가 입력 크기와 이 오차(px) 안이면 리사이즈를 "
                               "건너뛰고 입력을 그대로 내보냅니다. 0 = 정확히 일치할 "
                               "때만 통과. ImageScaleBy 는 scale_by=1.0 에서도 "
                               "리샘플을 수행해 시간이 낭비되고 미세하게 소프트해집니다."}),
            },
            "optional": {
                "source_image": ("IMAGE", {
                    "tooltip": "배율 기준이 되는 원본. 연결하면 실측 배율"
                               "(입력 폭 ÷ 원본 폭)을 재서 역산하므로 "
                               "'업스케일러가 몇 배인지'를 가정하지 않습니다."}),
            },
        }

    RETURN_TYPES = ("IMAGE", "FLOAT", "INT", "INT", "STRING")
    RETURN_NAMES = ("image", "scale_by", "width", "height", "info")
    FUNCTION = "doit"
    CATEGORY = "BMK/Image"
    DESCRIPTION = (
        "원본 대비 목표 배율로 리사이즈합니다. 업스케일러의 배율을 가정하지 않고 "
        "원본과 입력의 실제 크기를 재서 역산하며, max_scale 로 상한을 씌웁니다. "
        "목표 크기가 이미 입력과 같으면 리사이즈를 건너뜁니다."
    )
    SEARCH_ALIASES = [
        "scale to target", "target scale", "resize by ratio", "scale cap",
        "목표 배율", "배율 지정", "리사이즈", "축소", "스케일 상한",
    ]

    def doit(self, image, target_scale, max_scale, upscale_method, passthrough_px,
             source_image=None):
        in_h = int(image.shape[1])
        in_w = int(image.shape[2])

        # ── 기준 크기와 실측 배율 ──
        if source_image is not None:
            src_h = int(source_image.shape[1])
            src_w = int(source_image.shape[2])
            ratio_w = in_w / src_w
            ratio_h = in_h / src_h
            if abs(ratio_w - ratio_h) > 0.01:
                logger.warning(
                    "%s 가로/세로 실측 배율이 다릅니다 (x%.4f vs x%.4f). 중간 어딘가에서 "
                    "종횡비가 바뀌었을 수 있습니다. 가로 기준으로 계산합니다.",
                    _TAG, ratio_w, ratio_h)
            measured = ratio_w
        else:
            src_h, src_w = in_h, in_w
            measured = 1.0

        # ── 유효 배율과 목표 크기 ──
        eff = min(float(target_scale), float(max_scale))
        capped = eff < float(target_scale) - 1e-9

        out_w = max(1, int(round(src_w * eff)))
        out_h = max(1, int(round(src_h * eff)))
        scale_by = out_w / in_w if in_w else 1.0

        base = (f"src {src_w}x{src_h} → in {in_w}x{in_h} (실측 x{measured:.4f}) | "
                f"target x{float(target_scale):.4f}"
                + (f" → cap x{eff:.4f}" if capped else "")
                + f" | scale_by {scale_by:.4f} → out {out_w}x{out_h}")

        # ── 동일 크기 바이패스 ──
        tol = int(passthrough_px)
        if abs(out_w - in_w) <= tol and abs(out_h - in_h) <= tol:
            info = base + f"  [bypass: 입력과 동일 크기, 오차 {tol}px 이내]"
            logger.info("%s %s", _TAG, info)
            return (image, 1.0, in_w, in_h, info)

        out = _resize(image, out_w, out_h, upscale_method)
        info = base + f"  [{upscale_method}]"
        logger.info("%s %s", _TAG, info)
        return (out, scale_by, out_w, out_h, info)


NODE_CLASS_MAPPINGS = {
    "BMKScaleToTarget": BMKScaleToTarget,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "BMKScaleToTarget": "BMK Scale To Target",
}
