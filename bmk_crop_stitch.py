"""BMK Crop Stitch

BMK Load Image (Crop) 의 crop_info 를 받아, 워크플로우에서 처리된 크롭
이미지(image)를 원본 이미지의 동일 위치에 재합성(stitch)하는 노드.
Image Composite Masked 처럼 좌표를 수동 입력하거나 Inpaint Crop/Stitch
파이프를 별도로 구성할 필요 없이, crop_info 한 줄 연결로 좌표·회전·크기
정합이 자동으로 맞춰진다.

동작 규약
---------
- crop_info 의 box 는 "회전 후 좌표계" 기준(로더와 동일). 본 노드는
  처리된 이미지를 역회전(반시계)한 뒤 원본 좌표계로 역매핑해 붙인다.
- 처리된 이미지 해상도가 크롭 크기와 달라도(예: 샘플링 후 업스케일)
  목표 영역 크기로 자동 리샘플된다.
- 원본은 기본적으로 crop_info 에 실려 온 텐서를 사용하며,
  image_original 입력으로 교체할 수 있다. 교체본의 해상도가 원본과
  다르면(예: 업스케일본) 크롭 좌표를 비례 스케일해 같은 위치에 붙인다.
- blend_pixels: 붙이는 영역 가장자리를 선형 페더로 블렌드. 원본 이미지
  경계와 맞닿은 변에는 페더를 적용하지 않는다(비접경 변만 부드럽게).
- mask(옵션): 크롭 좌표계의 마스크(예: 로더 mask 출력, 인페인트 마스크)로
  붙일 영역을 제한한다. 1=처리 결과, 0=원본 유지.
- crop_info 의 box 가 None(크롭 없음)이면 회전 좌표계 전체를 대상으로
  동작한다(역회전 + 전체 교체 — 회전만 쓴 파이프라인도 정상 왕복).

출력
----
- image: 재합성된 원본 크기 이미지 (배치 크기 = 처리된 이미지 배치)
- mask:  실제로 붙은 영역의 블렌드 마스크(원본 좌표계) — 후속 합성용

v3 (2026-07)
------------
- 채널 자동 정합: image_crop 과 원본의 채널 수가 다르면(예: GLSL Shader 등
  일부 노드가 알파를 붙여 RGBA 로 내보내는 경우) 원본 기준으로 자동 변환
  (RGBA→RGB 알파 제거 / RGB→RGBA 불투명 알파 추가). 변환 시 로그를 남긴다.
  알파에 합성 의도가 담긴 경우(누끼 등)는 자동 제거 대상이므로,
  대신 mask 입력으로 연결할 것.

v2 (2026-07)
------------
- 입력 포트를 로더 출력과 미러로 통일: image_original / image_crop /
  mask / crop_info 순. ComfyUI 는 required 소켓을 optional 위에 그리므로,
  이 표시 순서를 보장하기 위해 네 포트를 모두 optional 그룹으로 선언하고
  필수 입력(crop_info, image_crop) 누락은 실행 시점에 검증한다.
- "image" 입력 명칭을 "image_crop" 으로 변경 (처리된 크롭 이미지).

v1 (2026-07)
------------
- 최초 구현. BMK Load Image (Crop) v3 의 crop_info(version=1) 스키마 사용.
"""

from __future__ import annotations

import logging

import torch

import comfy.utils

logger = logging.getLogger(__name__)

_TAG = "[ComfyUI_BMK_Nodes::CropStitch]"

_RESIZE_ALGOS = ["lanczos", "bicubic", "bilinear", "area", "nearest-exact"]


def _rect_rotated_to_original(x, y, w, h, W, H, rot):
    """회전 후 좌표계 사각형 → 원본(비회전) 좌표계 사각형.

    W, H = 원본 이미지 크기. rot = 시계 방향 회전각(0/90/180/270).
    (JS 프리뷰의 rectRotatedToOriginal 과 동일 산식 — PIL 교차검증 완료)
    """
    rot %= 360
    if rot == 90:
        return (y, H - x - w, h, w)
    if rot == 180:
        return (W - x - w, H - y - h, w, h)
    if rot == 270:
        return (W - y - h, x, h, w)
    return (x, y, w, h)


class BMKCropStitch:
    """crop_info 기반으로 처리된 크롭 이미지를 원본 동일 위치에 재합성."""

    @classmethod
    def INPUT_TYPES(cls):
        # 소켓 표시 순서를 로더 출력과 미러로 맞추기 위해 전부 optional 로
        # 선언한다 (required 는 항상 optional 위에 그려지므로 순서 제어 불가).
        # crop_info / image_crop 누락은 stitch() 에서 명시적으로 검증한다.
        return {
            "required": {
                "resize_algorithm": (_RESIZE_ALGOS, {"default": "lanczos"}),
                "blend_pixels": (
                    "INT",
                    {"default": 16, "min": 0, "max": 256},
                ),
            },
            "optional": {
                "image_original": ("IMAGE",),
                "image_crop": ("IMAGE",),
                "mask": ("MASK",),
                "crop_info": ("BMK_CROP_INFO",),
            },
        }

    RETURN_TYPES = ("IMAGE", "MASK")
    RETURN_NAMES = ("image", "mask")
    FUNCTION = "stitch"
    CATEGORY = "BMK/Image"
    DESCRIPTION = (
        "BMK Load Image (Crop) 의 crop_info 를 받아, 처리된 크롭 이미지를 "
        "원본의 동일 위치에 크기·회전을 자동 정합하여 재합성합니다. "
        "가장자리 페더 블렌드와 마스크 제한을 지원하며, 채널 수(RGB/RGBA)가 "
        "달라도 원본 기준으로 자동 정합합니다. image_original "
        "입력으로 업스케일본 등 다른 해상도의 원본에도 비례 위치로 "
        "붙일 수 있습니다."
    )
    SEARCH_ALIASES = [
        "stitch",
        "paste back",
        "composite",
        "recomposite",
        "스티치",
        "재합성",
        "붙여넣기",
    ]

    def stitch(
        self,
        resize_algorithm,
        blend_pixels,
        image_original=None,
        image_crop=None,
        mask=None,
        crop_info=None,
    ):
        if crop_info is None:
            raise ValueError(
                f"{_TAG} crop_info 입력이 연결되지 않았습니다. "
                "BMK Load Image (Crop) 의 crop_info 출력을 연결하세요."
            )
        if image_crop is None:
            raise ValueError(
                f"{_TAG} image_crop 입력이 연결되지 않았습니다. "
                "재합성할(처리된) 크롭 이미지를 연결하세요."
            )
        if not isinstance(crop_info, dict) or crop_info.get("version") != 1:
            raise ValueError(
                f"{_TAG} 지원하지 않는 crop_info 형식입니다. "
                "BMK Load Image (Crop) v3 이상의 crop_info 출력을 연결하세요."
            )

        rotation = int(crop_info.get("rotation", 0)) % 360
        W, H = crop_info["original_size"]
        rw, rh = crop_info["rotated_size"]
        box = crop_info.get("box")
        if box is None:
            box = (0, 0, rw, rh)  # 크롭 없음 → 회전 좌표계 전체
        bx, by, bw, bh = (int(v) for v in box)

        base = image_original if image_original is not None else crop_info.get(
            "original_image"
        )
        if base is None:
            raise ValueError(
                f"{_TAG} 원본 이미지가 없습니다. crop_info 가 손상되었거나 "
                "image_original 입력이 필요합니다."
            )

        # 채널 자동 정합: 원본 채널 수 기준으로 image_crop 을 맞춘다.
        # (예: GLSL Shader 는 vec4 출력이라 RGBA[B,H,W,4]로 들어올 수 있음)
        # 회전/리샘플 전에 처리해 불필요한 4채널 리샘플 비용도 줄인다.
        base_c = int(base.shape[3])
        patch_c = int(image_crop.shape[3])
        if patch_c != base_c:
            if patch_c == 4 and base_c == 3:
                image_crop = image_crop[..., :3]
                logger.info(
                    "%s image_crop 의 알파 채널을 제거했습니다 (RGBA→RGB). "
                    "알파를 합성에 쓰려면 mask 입력으로 연결하세요.",
                    _TAG,
                )
            elif patch_c == 3 and base_c == 4:
                image_crop = torch.cat(
                    [image_crop, torch.ones_like(image_crop[..., :1])], dim=-1
                )
                logger.info(
                    "%s image_crop 에 불투명 알파를 추가했습니다 (RGB→RGBA).",
                    _TAG,
                )
            else:
                raise ValueError(
                    f"{_TAG} 지원하지 않는 채널 조합입니다: "
                    f"image_crop={patch_c}ch, 원본={base_c}ch"
                )

        # 회전 좌표계 박스 → 원본 좌표계 사각형
        ox, oy, ow, oh = _rect_rotated_to_original(bx, by, bw, bh, W, H, rotation)

        # image_original 교체본이 원본과 다른 해상도면 좌표 비례 스케일
        base_b = base.shape[0]
        base_h = int(base.shape[1])
        base_w = int(base.shape[2])
        sx = base_w / W
        sy = base_h / H
        tx = int(round(ox * sx))
        ty = int(round(oy * sy))
        tw = max(1, int(round(ow * sx)))
        th = max(1, int(round(oh * sy)))
        tx = max(0, min(tx, base_w - 1))
        ty = max(0, min(ty, base_h - 1))
        tw = min(tw, base_w - tx)
        th = min(th, base_h - ty)

        # 처리된 이미지: 역회전(반시계) → 목표 크기로 리샘플
        k = rotation // 90
        patch = image_crop
        if k:
            patch = torch.rot90(patch, k=k, dims=(1, 2))
        if int(patch.shape[1]) != th or int(patch.shape[2]) != tw:
            p = patch.movedim(-1, 1)  # BHWC → BCHW
            p = comfy.utils.common_upscale(p, tw, th, resize_algorithm, "disabled")
            patch = p.movedim(1, -1)

        # 옵션 마스크: 크롭 좌표계 → 역회전 → 목표 크기
        if mask is not None:
            mm = mask
            if k:
                mm = torch.rot90(mm, k=k, dims=(1, 2))
            if int(mm.shape[1]) != th or int(mm.shape[2]) != tw:
                mm = comfy.utils.common_upscale(
                    mm.unsqueeze(1), tw, th, resize_algorithm, "disabled"
                ).squeeze(1)
            mask = mm.clamp(0.0, 1.0)

        # 블렌드 마스크: 원본 경계와 접하지 않은 변에만 선형 페더
        m = torch.ones((th, tw), dtype=torch.float32)
        n = int(blend_pixels)
        ny = min(n, th // 2)
        nx = min(n, tw // 2)
        if ny > 0:
            up = (torch.arange(ny, dtype=torch.float32) + 1.0) * (1.0 / (ny + 1))
            down = (ny - torch.arange(ny, dtype=torch.float32)) * (1.0 / (ny + 1))
            if ty > 0:
                m[:ny, :] = m[:ny, :] * up.unsqueeze(1)
            if ty + th < base_h:
                m[th - ny :, :] = m[th - ny :, :] * down.unsqueeze(1)
        if nx > 0:
            left = (torch.arange(nx, dtype=torch.float32) + 1.0) * (1.0 / (nx + 1))
            right = (nx - torch.arange(nx, dtype=torch.float32)) * (1.0 / (nx + 1))
            if tx > 0:
                m[:, :nx] = m[:, :nx] * left.unsqueeze(0)
            if tx + tw < base_w:
                m[:, tw - nx :] = m[:, tw - nx :] * right.unsqueeze(0)

        # 프레임별 재합성 (배치 크기가 다르면 원본/마스크를 순환 사용)
        out_frames = []
        mask_frames = []
        patch_b = patch.shape[0]
        for i in range(patch_b):
            frame = base[i % base_b].clone()  # [H, W, C]
            mi = m
            if mask is not None:
                mi = m * mask[i % mask.shape[0]]
            region = frame[ty : ty + th, tx : tx + tw, :]
            blended = patch[i] * mi.unsqueeze(-1) + region * (1.0 - mi).unsqueeze(-1)
            frame[ty : ty + th, tx : tx + tw, :] = blended
            out_frames.append(frame.unsqueeze(0))

            full = torch.zeros((base_h, base_w), dtype=torch.float32)
            full[ty : ty + th, tx : tx + tw] = mi
            mask_frames.append(full.unsqueeze(0))

        return (
            torch.cat(out_frames, dim=0),
            torch.cat(mask_frames, dim=0),
        )


NODE_CLASS_MAPPINGS = {
    "BMKCropStitch": BMKCropStitch,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "BMKCropStitch": "BMK Crop Stitch",
}
