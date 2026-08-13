from __future__ import annotations

# ─── BMK Upscale Image (using Model) - Tiled ────────────────────────────────
# 스톡 ComfyUI 의 ImageUpscaleWithModel(comfy_extras/nodes_upscale_model.py)을
# 그대로 미러링하되, 내부에 하드코딩되어 있던 tile=512 / overlap=32 를
# 사용자가 직접 지정할 수 있도록 tile_x / tile_y / overlap 위젯으로 노출하고,
# 추가로 입력 이미지 해상도 + 실시간 free VRAM 으로부터 가장 효율적인 타일을
# 자동 계산하는 auto_tile 모드를 내장한 버전.
#
# auto_tile 계산 원리:
#   1) 스톡의 메모리 추정식을 역산해 현재 free VRAM 으로 감당 가능한
#      "최대 입력 타일 면적 A"(픽셀)를 구한다.
#         A ≈ (free * safety - image_bytes) / (3 * element_size * scale * 384)
#   2) 짧은 축은 통짜로 두고(해당 축 분할 0, 이음새 0),
#      긴 축만 균일하게 k 분할한다. k 는 short * T_long(k) <= A 를
#      만족하는 최소값.
#         T_long(k) = overlap + ceil((long - overlap) / k)
#   3) 이미지가 통째로 A 안에 들어오면 1타일(분할 없음).
#   => 정사각형 타일을 강제하지 않고 이미지 종횡비에 맞춘 strip 형태로
#      중복 연산을 최소화한다. ESRGAN 류는 fully-conv 라 비정방 타일도
#      품질 영향이 없다.
#
# ※ auto_tile 은 고사양(RTX 4090 이상) 전제. free VRAM 을 적극 활용해
#   큰 타일을 쓰므로 저사양에서는 의도대로 동작하지 않을 수 있다.
#   (그래도 OOM 시 타일 절반 폴백이 있어 안전하게 떨어진다.)
#
# 표준 core API(comfy.utils.tiled_scale / comfy.model_management)만 사용하는
# 순수 Python 노드이므로 portable / Easy-Install / 일반 install,
# 그리고 4090 / 5090 (cu128+) 환경에서 동작이 동일하다.
# ────────────────────────────────────────────────────────────────────────────

import inspect
import math

import torch

import comfy.utils
from comfy import model_management


# tiled_scale 가 미래 ComfyUI 업데이트에서 시그니처가 확장될 가능성에 대비해
# 지원되는 인자만 추려서 전달하기 위한 헬퍼 (방어적 호출).
_TILED_SCALE_PARAMS = set(inspect.signature(comfy.utils.tiled_scale).parameters.keys())


def _call_tiled_scale(in_img, function, *, tile_x, tile_y, overlap, upscale_amount, pbar):
    kwargs = dict(
        tile_x=tile_x,
        tile_y=tile_y,
        overlap=overlap,
        upscale_amount=upscale_amount,
        pbar=pbar,
    )
    kwargs = {k: v for k, v in kwargs.items() if k in _TILED_SCALE_PARAMS}
    return comfy.utils.tiled_scale(in_img, function, **kwargs)


def _grid_count(length: int, tile: int, overlap: int) -> int:
    """한 축이 몇 개 타일로 쪼개지는지 (comfy.utils.get_tiled_scale_steps 와 동일 로직)."""
    if length <= tile:
        return 1
    return max(1, math.ceil((length - overlap) / max(1, (tile - overlap))))


class BMKUpscaleImageWithModelTiled:
    """tile_x / tile_y / overlap 직접 지정 + 해상도·VRAM 기반 자동 타일 계산(auto_tile)."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "upscale_model": ("UPSCALE_MODEL",),
                "image": ("IMAGE",),
                "auto_tile": (
                    "BOOLEAN",
                    {"default": False, "label_on": "auto (HIGH-VRAM)", "label_off": "manual",
                     "tooltip": "켜면 입력 해상도 + 실시간 free VRAM 으로 최적 타일을 "
                                "자동 계산하고 tile_x/tile_y 값을 무시한다. "
                                "RTX 4090 이상 고사양 전용 옵션."},
                ),
                "tile_x": (
                    "INT",
                    {"default": 1024, "min": 128, "max": 8192, "step": 64,
                     "tooltip": "[manual 전용] 타일 가로(px). 입력 폭 이상이면 가로 분할 없음."},
                ),
                "tile_y": (
                    "INT",
                    {"default": 1024, "min": 128, "max": 8192, "step": 64,
                     "tooltip": "[manual 전용] 타일 세로(px). 입력 높이 이상이면 세로 분할 없음."},
                ),
                "overlap": (
                    "INT",
                    {"default": 32, "min": 0, "max": 512, "step": 8,
                     "tooltip": "타일 간 겹침(px). 경계 이음새 완화. auto/manual 모두 적용."},
                ),
                "vram_safety": (
                    "FLOAT",
                    {"default": 0.75, "min": 0.30, "max": 0.95, "step": 0.05,
                     "tooltip": "[auto 전용] free VRAM 중 타일에 쓸 비율. "
                                "낮출수록 안전(타일↓·이음새↑), 높일수록 공격적."},
                ),
            }
        }

    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "upscale"
    CATEGORY = "BMK/image/upscaling"
    DESCRIPTION = (
        "Stock 'Upscale Image (using Model)' 미러링 + tile_x/tile_y/overlap 노출 + "
        "해상도·VRAM 기반 auto_tile 자동 계산(고사양 전용). OOM 시 타일 절반 자동 폴백."
    )
    SEARCH_ALIASES = [
        "upscale with model", "tiled upscale", "esrgan", "auto tile",
        "업스케일", "타일 업스케일", "모델 업스케일",
    ]

    # ── 자동 타일 계산 ─────────────────────────────────────────────
    def _auto_tiles(self, image, upscale_model, device, overlap, safety):
        H = int(image.shape[1])
        W = int(image.shape[2])

        # 모델이 device 에 올라온 상태에서 free VRAM 실측 → 최대 입력 타일 면적 A 역산.
        upscale_model.to(device)
        free = float(model_management.get_free_memory(device))
        elem = image.element_size()
        scale = max(float(upscale_model.scale), 1.0)
        image_bytes = image.nelement() * elem

        # 스톡 추정식의 타일당 입력픽셀당 비용: 3(ch) * elem * scale * 384(fudge)
        per_pixel = 3.0 * elem * scale * 384.0
        budget = free * float(safety) - image_bytes
        A = int(max(128 * 128, budget / per_pixel))

        short = min(W, H)
        long = max(W, H)

        # 1) 통째로 들어오면 분할 없음
        if W * H <= A:
            return W, H, A

        # 2) 짧은 축을 통짜로 유지 가능한지 (긴 축 최소 타일 128 기준)
        if short * 128 <= A:
            k = max(1, math.ceil((short * long) / A))
            while True:
                t_long = overlap + math.ceil((long - overlap) / k)
                if short * t_long <= A or t_long <= 128:
                    break
                k += 1
            t_long = max(128, t_long)
            t_short = short
        else:
            # 3) 짧은 축조차 너무 넓음 → 2D 정사각 분할로 폴백
            side = max(128, math.isqrt(A))
            t_short = min(short, side)
            t_long = min(long, side)

        # 방향 매핑: short = min(W,H)
        if W <= H:          # 폭이 짧은 축
            return t_short, t_long, A
        else:               # 높이가 짧은 축
            return t_long, t_short, A

    def upscale(self, upscale_model, image, auto_tile, tile_x, tile_y, overlap, vram_safety):
        device = model_management.get_torch_device()

        if auto_tile:
            tile_x, tile_y, A = self._auto_tiles(image, upscale_model, device, overlap, vram_safety)
            H = int(image.shape[1]); W = int(image.shape[2])
            cols = _grid_count(W, tile_x, overlap)
            rows = _grid_count(H, tile_y, overlap)
            print(
                "[ComfyUI_BMK_Nodes] auto_tile -> "
                f"{tile_x}x{tile_y} (overlap {overlap}), grid {cols}x{rows} = "
                f"{cols * rows} tile(s), max_tile_area≈{A:,}px"
            )

        # overlap 은 타일보다 작아야 함. 절반 미만으로 클램프.
        max_overlap = max(0, (min(tile_x, tile_y) // 2) - 8)
        overlap = max(0, min(int(overlap), max_overlap))

        # VRAM 확보 추정치(스톡 휴리스틱을 실제 타일 크기로 스케일).
        memory_required = model_management.module_size(upscale_model.model)
        memory_required += (
            (tile_x * tile_y * 3)
            * image.element_size()
            * max(upscale_model.scale, 1.0)
            * 384.0
        )
        memory_required += image.nelement() * image.element_size()
        model_management.free_memory(memory_required, device)

        upscale_model.to(device)
        in_img = image.movedim(-1, -3).to(device)

        cur_x = int(tile_x)
        cur_y = int(tile_y)
        cur_overlap = int(overlap)

        oom = True
        s = None
        while oom:
            try:
                steps = in_img.shape[0] * comfy.utils.get_tiled_scale_steps(
                    in_img.shape[3], in_img.shape[2],
                    tile_x=cur_x, tile_y=cur_y, overlap=cur_overlap,
                )
                pbar = comfy.utils.ProgressBar(steps)
                s = _call_tiled_scale(
                    in_img,
                    lambda a: upscale_model(a),
                    tile_x=cur_x,
                    tile_y=cur_y,
                    overlap=cur_overlap,
                    upscale_amount=upscale_model.scale,
                    pbar=pbar,
                )
                oom = False
            except model_management.OOM_EXCEPTION as e:
                cur_x //= 2
                cur_y //= 2
                if min(cur_x, cur_y) < 128:
                    upscale_model.to("cpu")
                    raise e
                cur_overlap = min(cur_overlap, max(0, (min(cur_x, cur_y) // 2) - 8))
                print(
                    "[ComfyUI_BMK_Nodes] Upscale OOM - retrying with smaller tile: "
                    f"{cur_x}x{cur_y} (overlap {cur_overlap})"
                )

        upscale_model.to("cpu")
        s = torch.clamp(s.movedim(-3, -1), min=0, max=1.0)
        return (s,)


NODE_CLASS_MAPPINGS = {
    "BMKUpscaleImageWithModelTiled": BMKUpscaleImageWithModelTiled,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "BMKUpscaleImageWithModelTiled": "BMK Upscale Image (using Model, Tiled)",
}
