"""
BMK SEGS Core Mask
==================

타일 SEGS 의 crop_region(생성용 패딩 영역)은 그대로 두고, cropped_mask(페이스트
영역)만 "코어 영역 + 얇은 feather" 로 교체한다.

목적
----
DetailerForEach 는 crop_region 으로 잘라 생성하고, cropped_mask 로 페이스트한다.
tile_mask=full_rectangle 처럼 마스크가 패딩 전체이면, 오버랩 밴드에서 인접 두 타일이
같은 픽셀을 각각 재생성한 뒤 넓게 블렌딩 -> 고주파 디테일(피부 음영 등)이 중첩되어
"멍"/과채도 seam 이 생긴다.

이 노드는:
  - crop_region(생성 컨텍스트)는 유지  -> 이웃 컨텍스트 보존, 경계 일관성 유지
  - cropped_mask 는 코어만 칠하도록 축소 -> 각 픽셀은 단 한 타일에서만 페이스트
=> 오버랩의 이중 처리(중첩)를 제거하면서, 생성 시 패딩 컨텍스트는 그대로 활용.

이미지 경계에 접한 변(crop_region 이 0 또는 W/H 에 닿는 변)은 축소하지 않아
가장자리에 빈 픽셀(gap)이 생기지 않는다.

튜닝
----
- border : 안쪽으로 잘라낼 px. 이웃 코어끼리 딱 맞닿게 하려면
           대략 (overlap/2 + crop_pixel) 부근에서 시작해 조정.
           멍이 남으면 늘리고, seam 에 빈틈/티가 보이면 줄인다.
- feather: seam 전이 폭(px). 8~16 권장. 작을수록 중첩이 적다.
- DetailerForEach 의 자체 feather 는 이 마스크와 겹쳐 전이를 다시 넓히므로
  코어 마스크 사용 시 4~8 정도로 낮추는 걸 권장.
"""

import numpy as np
import torch


def _axis_weight(length, lo_cut, hi_cut, feather):
    """1D 가중치: 코어=1, 잘라낸 변=0, 경계는 feather px 로 선형 전이."""
    x = np.arange(length, dtype=np.float32)

    if lo_cut > 0:
        if feather > 0:
            left = np.clip((x - lo_cut) / feather, 0.0, 1.0)
        else:
            left = (x >= lo_cut).astype(np.float32)
    else:
        left = np.ones(length, dtype=np.float32)   # 이미지 경계변: 끝까지 칠함

    if hi_cut > 0:
        right_edge = length - hi_cut
        if feather > 0:
            right = np.clip((right_edge - 1 - x) / feather, 0.0, 1.0)
        else:
            right = (x < right_edge).astype(np.float32)
    else:
        right = np.ones(length, dtype=np.float32)

    return (left * right).astype(np.float32)


class BMKSEGSCoreMask:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "segs": ("SEGS",),
                "border": ("INT", {"default": 128, "min": 0, "max": 4096, "step": 1}),
                "feather": ("INT", {"default": 12, "min": 0, "max": 512, "step": 1}),
            },
            "optional": {
                # True 면 기존 cropped_mask(실루엣 등)와 곱해 교집합 유지
                "intersect_existing": ("BOOLEAN", {"default": True}),
            },
        }

    RETURN_TYPES = ("SEGS",)
    RETURN_NAMES = ("segs",)
    FUNCTION = "apply"
    CATEGORY = "BMK/segs"
    DESCRIPTION = (
        "타일 SEGS의 crop_region(생성 컨텍스트)은 유지한 채 cropped_mask만 코어 영역 + "
        "얇은 feather로 축소합니다. 각 픽셀이 단 한 타일에서만 페이스트되므로, "
        "오버랩 밴드의 이중 처리로 생기는 멍/과채도 seam이 사라집니다. "
        "이미지 경계에 닿은 변은 축소하지 않아 가장자리에 빈 픽셀이 생기지 않습니다."
    )
    SEARCH_ALIASES = [
        "segs core mask", "core mask", "tile seam", "overlap", "seam",
        "이음새", "오버랩", "타일 마스크",
    ]

    def apply(self, segs, border, feather, intersect_existing=True):
        size, seg_list = segs[0], segs[1]
        H, W = int(size[0]), int(size[1])

        new_list = []
        for seg in seg_list:
            x1, y1, x2, y2 = (int(v) for v in seg.crop_region)
            w = max(1, x2 - x1)
            h = max(1, y2 - y1)

            # 이미지 경계에 닿지 않은 변만 축소
            lo_x = border if x1 > 0 else 0
            hi_x = border if x2 < W else 0
            lo_y = border if y1 > 0 else 0
            hi_y = border if y2 < H else 0

            # 코어가 사라지지 않도록 클램프
            if lo_x + hi_x >= w:
                lo_x = hi_x = max(0, (w - 1) // 2)
            if lo_y + hi_y >= h:
                lo_y = hi_y = max(0, (h - 1) // 2)

            wx = _axis_weight(w, lo_x, hi_x, feather)
            wy = _axis_weight(h, lo_y, hi_y, feather)
            core = np.outer(wy, wx).astype(np.float32)   # [h, w]

            old = seg.cropped_mask
            is_tensor = torch.is_tensor(old)
            if intersect_existing and old is not None:
                old_np = old.detach().cpu().numpy() if is_tensor else np.asarray(old, dtype=np.float32)
                old_np = old_np.astype(np.float32)
                if old_np.max() > 1.0:      # 0..255 정규화
                    old_np = old_np / 255.0
                if old_np.shape == core.shape:
                    core = core * old_np

            new_mask = torch.from_numpy(core).to(old) if is_tensor else core
            new_list.append(seg._replace(cropped_mask=new_mask))

        return ((size, new_list),)


NODE_CLASS_MAPPINGS = {
    "BMKSEGSCoreMask": BMKSEGSCoreMask,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "BMKSEGSCoreMask": "BMK SEGS Core Mask",
}
