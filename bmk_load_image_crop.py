"""BMK Load Image (Crop)

이미지를 불러와 회전 및 종횡비 고정 크롭을 적용해 내보내는 노드.
크롭 편집 UI는 순수 프론트엔드(./js/bmk_load_image_crop.js)에서 동작하므로
큐 실행 중에도 자유롭게 조작할 수 있으며, 편집 결과는 이 노드의
rotation / crop_x / crop_y / crop_width / crop_height 위젯 값으로만 기록되고
실제 회전·크롭은 실행 시점에 PIL 로 수행된다(비파괴적 — 원본 파일 유지).

좌표 규약
---------
- rotation 은 시계 방향(도 단위, 0/90/180/270).
- 크롭 좌표는 "회전이 적용된 후"의 이미지 좌표계 기준. (JS 에디터와 동일)
- crop_width 또는 crop_height 가 0 이면 크롭 없음(전체 이미지).
- 이미지 파일이 다른 해상도로 교체되어도 좌표를 경계 안으로 클램프하여
  실행이 실패하지 않는다.

v3 (2026-07)
------------
- crop_info(BMK_CROP_INFO) 출력 추가: 실행 시점의 "실효(클램프된)" 크롭 박스,
  회전각, 원본/회전 좌표계 크기, 원본 이미지 텐서를 담아 BMK Crop Stitch
  (bmk_crop_stitch.py)로 전달한다. dict 스키마(version=1):
    { "version": 1, "rotation": int, "box": (x, y, w, h) | None,
      "original_size": (W, H), "rotated_size": (rw, rh),
      "original_image": IMAGE 텐서 }
  box 좌표는 회전 후 좌표계 기준(본 문서 상단 좌표 규약과 동일).
  crop_info 는 dict 와 완전 호환되는 컨테이너로, 텍스트 프리뷰 등에서
  문자열화될 때 원본 텐서를 덤프하지 않고 한 줄 요약만 표시한다.

v2 (2026-07)
------------
- 출력 분리: image_original(원본 그대로) / image_crop(회전+크롭 적용).
  MASK 는 image_crop 과 동일 좌표계(알파 채널 기반, 없으면 0 마스크).
- width / height 출력 제거 (Get Image Size 노드 사용 전제).
- 향후 v3 예정인 "크롭 영역 → 원본 재합성(stitch)" 노드 연동을 위해
  크롭 정보(위젯 4종 + rotation)의 좌표 규약을 본 문서에 고정해 둔다.
- (프론트) 노드 내 프리뷰에 크롭 영역 오버레이 + 원본/크롭 토글,
  노드 버튼으로 Reset Crop, 업로드 버튼 위치 정렬(기본 LoadImage 와 통일),
  에디터에 스냅(divide-by)·종횡비 선택 유지·수치 미세 입력 추가.

v1 (2026-07)
------------
- 최초 구현: LoadImage 기반 회전/크롭, IMAGE/MASK/width/height 출력.
"""

from __future__ import annotations

import hashlib
import logging
import os

import numpy as np
import torch
from PIL import Image, ImageOps, ImageSequence

import folder_paths
import node_helpers

try:
    from nodes import MAX_RESOLUTION
except Exception:  # pragma: no cover - 구버전 대비 안전장치
    MAX_RESOLUTION = 16384

logger = logging.getLogger(__name__)

_TAG = "[ComfyUI_BMK_Nodes::LoadImageCrop]"


# PIL 의 Transpose.ROTATE_* 는 반시계 방향이므로,
# "시계 방향 회전" 규약에 맞게 매핑을 뒤집어 둔다.
_CW_ROTATION_TO_TRANSPOSE = {
    90: Image.Transpose.ROTATE_270,
    180: Image.Transpose.ROTATE_180,
    270: Image.Transpose.ROTATE_90,
}


def _apply_rotation(img: Image.Image, rotation: int) -> Image.Image:
    transpose = _CW_ROTATION_TO_TRANSPOSE.get(rotation % 360)
    if transpose is None:
        return img
    return img.transpose(transpose)


def _clamp_crop_box(img_w, img_h, crop_x, crop_y, crop_w, crop_h):
    """크롭 박스를 이미지 경계 안으로 클램프. 유효 영역이 없으면 None."""
    if crop_w <= 0 or crop_h <= 0:
        return None

    x0 = max(0, min(crop_x, img_w - 1))
    y0 = max(0, min(crop_y, img_h - 1))
    x1 = max(x0 + 1, min(crop_x + crop_w, img_w))
    y1 = max(y0 + 1, min(crop_y + crop_h, img_h))

    if x1 - x0 < 1 or y1 - y0 < 1:
        return None
    return (x0, y0, x1, y1)


def _to_tensor(rgb: Image.Image) -> torch.Tensor:
    arr = np.asarray(rgb, dtype=np.float32) / 255.0
    return torch.from_numpy(arr)[None,]


class _BMKCropInfo(dict):
    """crop_info 컨테이너 (dict 완전 호환).

    original_image 텐서를 그대로 담고 있으므로, 일반 dict 라면 텍스트
    프리뷰 노드 등에서 str()/repr() 시 수백만 개의 픽셀 값이 덤프된다.
    이를 방지하기 위해 문자열화 시 한 줄 요약만 표시한다.
    """

    def __repr__(self):
        img = self.get("original_image")
        shape = (
            "x".join(str(int(s)) for s in tuple(img.shape))
            if img is not None and hasattr(img, "shape")
            else "None"
        )
        return (
            "BMK_CROP_INFO("
            f"version={self.get('version')}, "
            f"rotation={self.get('rotation')}, "
            f"box={self.get('box')}, "
            f"original_size={self.get('original_size')}, "
            f"rotated_size={self.get('rotated_size')}, "
            f"original_image=[{shape}])"
        )

    __str__ = __repr__


class BMKLoadImageCrop:
    """이미지 로드 + 노드 위 'Crop Editor' 로 회전/종횡비 크롭을 미리 지정."""

    @classmethod
    def INPUT_TYPES(cls):
        input_dir = folder_paths.get_input_directory()
        files = sorted(
            f
            for f in os.listdir(input_dir)
            if os.path.isfile(os.path.join(input_dir, f))
        )
        return {
            "required": {
                "image": (files, {"image_upload": True}),
                # 시계 방향 회전(도). JS 에디터가 0/90/180/270 만 기록한다.
                "rotation": (
                    "INT",
                    {"default": 0, "min": 0, "max": 270, "step": 90},
                ),
                "crop_x": (
                    "INT",
                    {"default": 0, "min": 0, "max": MAX_RESOLUTION},
                ),
                "crop_y": (
                    "INT",
                    {"default": 0, "min": 0, "max": MAX_RESOLUTION},
                ),
                # 0 = 크롭 없음(전체 이미지)
                "crop_width": (
                    "INT",
                    {"default": 0, "min": 0, "max": MAX_RESOLUTION},
                ),
                "crop_height": (
                    "INT",
                    {"default": 0, "min": 0, "max": MAX_RESOLUTION},
                ),
            }
        }

    RETURN_TYPES = ("IMAGE", "IMAGE", "MASK", "BMK_CROP_INFO")
    RETURN_NAMES = ("image_original", "image_crop", "mask", "crop_info")
    FUNCTION = "load_image"
    CATEGORY = "BMK/Image"
    DESCRIPTION = (
        "이미지를 불러와 회전·종횡비 고정 크롭을 적용해 내보냅니다. "
        "노드의 Crop Editor 버튼으로 큐 실행 여부와 무관하게 크롭 영역을 "
        "편집할 수 있으며(비파괴적), image_original(원본)과 image_crop"
        "(편집 결과)을 각각 출력합니다. MASK 는 image_crop 좌표계입니다. "
        "crop_info 를 BMK Crop Stitch 에 연결하면 처리된 크롭 이미지를 "
        "원본의 동일 위치에 자동으로 재합성할 수 있습니다."
    )
    SEARCH_ALIASES = [
        "load image",
        "crop",
        "aspect crop",
        "drag crop",
        "이미지 로드",
        "크롭",
        "자르기",
    ]

    def load_image(self, image, rotation, crop_x, crop_y, crop_width, crop_height):
        image_path = folder_paths.get_annotated_filepath(image)
        img = node_helpers.pillow(Image.open, image_path)

        orig_images = []
        crop_images = []
        crop_masks = []
        orig_size = None  # (w, h) — 원본 스트림 첫 프레임 기준
        crop_size = None  # (w, h) — 크롭 스트림 첫 프레임 기준
        rotated_size = None  # (w, h) — 회전 후·크롭 전 좌표계 크기
        effective_box = None  # (x, y, w, h) — 클램프된 실효 크롭 박스
        excluded_formats = ("MPO",)

        for frame in ImageSequence.Iterator(img):
            frame = node_helpers.pillow(ImageOps.exif_transpose, frame)

            if frame.mode == "I":
                frame = frame.point(lambda i: i * (1 / 255))

            has_alpha = "A" in frame.getbands()
            rgba = frame.convert("RGBA") if has_alpha else None
            rgb = frame.convert("RGB")

            # ── 원본 스트림 (회전/크롭 미적용) ──
            if orig_size is None:
                orig_size = (rgb.width, rgb.height)
            if (rgb.width, rgb.height) == orig_size:
                orig_images.append(_to_tensor(rgb))

            # ── 크롭 스트림 (회전 → 크롭) ──
            rgb_c = _apply_rotation(rgb, rotation)
            rgba_c = _apply_rotation(rgba, rotation) if rgba is not None else None

            box = _clamp_crop_box(
                rgb_c.width, rgb_c.height, crop_x, crop_y, crop_width, crop_height
            )
            if rotated_size is None:
                rotated_size = (rgb_c.width, rgb_c.height)
                if box is not None:
                    effective_box = (
                        box[0],
                        box[1],
                        box[2] - box[0],
                        box[3] - box[1],
                    )
            if box is not None:
                rgb_c = rgb_c.crop(box)
                if rgba_c is not None:
                    rgba_c = rgba_c.crop(box)

            if crop_size is None:
                crop_size = (rgb_c.width, rgb_c.height)
            if (rgb_c.width, rgb_c.height) != crop_size:
                continue

            crop_images.append(_to_tensor(rgb_c))

            if rgba_c is not None:
                alpha = np.asarray(rgba_c.getchannel("A"), dtype=np.float32) / 255.0
                mask = 1.0 - torch.from_numpy(alpha)
            else:
                mask = torch.zeros(
                    (crop_size[1], crop_size[0]), dtype=torch.float32
                )
            crop_masks.append(mask.unsqueeze(0))

        multi_ok = img.format not in excluded_formats

        if len(orig_images) > 1 and multi_ok:
            image_original = torch.cat(orig_images, dim=0)
        else:
            image_original = orig_images[0]

        if len(crop_images) > 1 and multi_ok:
            image_crop = torch.cat(crop_images, dim=0)
            mask_out = torch.cat(crop_masks, dim=0)
        else:
            image_crop = crop_images[0]
            mask_out = crop_masks[0]

        crop_info = _BMKCropInfo(
            version=1,
            rotation=rotation % 360,
            box=effective_box,  # None 이면 크롭 없음(회전 좌표계 전체)
            original_size=orig_size,
            rotated_size=rotated_size,
            original_image=image_original,
        )

        return (image_original, image_crop, mask_out, crop_info)

    @classmethod
    def IS_CHANGED(cls, image, **kwargs):
        # 파일 내용만 해시한다 — rotation/crop_* 은 일반 입력이므로
        # 값이 바뀌면 ComfyUI 캐시가 알아서 무효화된다.
        image_path = folder_paths.get_annotated_filepath(image)
        m = hashlib.sha256()
        with open(image_path, "rb") as f:
            m.update(f.read())
        return m.digest().hex()

    @classmethod
    def VALIDATE_INPUTS(cls, image, **kwargs):
        if not folder_paths.exists_annotated_filepath(image):
            return f"Invalid image file: {image}"
        return True


NODE_CLASS_MAPPINGS = {
    "BMKLoadImageCrop": BMKLoadImageCrop,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "BMKLoadImageCrop": "BMK Load Image (Crop)",
}
