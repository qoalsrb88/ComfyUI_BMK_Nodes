"""BMK Flexible Tile SEGS.

SEGS 영역을 직관적인 옵션으로 타일 분할하는 노드. Make Tile SEGS 의 crop_factor(float)
중심 방식 대신, 픽셀 단위 컨트롤을 제공한다.

모드
----
- "grid"      : 대상 영역을 columns × rows 로 등분.
- "tile_size" : tile_width × tile_height(px) 고정 크기로, overlap(px)만큼 겹치며 분할.

공통 옵션
---------
- crop_pixel  : 각 타일 bbox 바깥으로 줄 컨텍스트 여유(px). (crop_factor 대체)
- overlap     : 타일 간 겹침(px). grid 모드에서는 각 타일 bbox 를 사방으로 overlap 만큼
                확장하여 겹치게 한다(0이면 정확히 등분/맞붙음).

대상 영역(region_source)
------------------------
- "segs_union_bbox"  : 입력 SEGS 의 모든 bbox 합집합(예: MASK to SEGS 의 인물 영역). [기본]
- "full_image"       : segs[0] 가 가리키는 전체 이미지 영역.
- "each_segment_bbox": 입력 SEGS 의 각 원소(bbox)를 개별 영역으로 보고 각각 타일 분할.
                       예) 얼굴 3개가 감지되면 합친 면적이 아니라 얼굴마다 columns×rows
                       (또는 tile_size) 로 나눈다. 결과는 하나의 SEGS 로 합쳐 출력.
                       실루엣 교집합은 해당 세그먼트(또는 연결된 mask) 기준으로 적용.

경계 처리(boundary_mode)
------------------------
- "snap_inside"    : [기존 동작] 마지막 타일을 이미지 가장자리에 스냅하고, crop_region 을
                     이미지 안으로 클램프한다. 이미지 크기가 타일 크기의 배수가 아니면
                     타일(코어)끼리 숨은 겹침이 생기고 crop 크기가 제각각이 된다.
- "virtual_canvas" : [신규] 영역 좌상단(기준점)에서 고정 스트라이드로 타일을 깔고,
                     이미지 밖까지 가상 캔버스를 확장한다. 모든 타일이 동일한
                     (tile + 2*crop_pixel) 크기를 유지하며, 코어는 overlap=0 일 때
                     한 픽셀도 겹치지 않는 완전 분할이 된다.
                     * 이 모드에서는 image 입력을 반드시 연결해야 하며, 노드가 패딩된
                       이미지(image 출력)와 pad_info 를 함께 내보낸다.
                     * Detailer (SEGS) 에는 반드시 이 노드의 image 출력(패딩본)을 연결하고,
                       Detailer 완료 후 "BMK Virtual Canvas Crop" 노드에 pad_info 를 물려
                       원본 크기로 복원한다.
                     * Per-SEGS Hook 이 참조 이미지를 crop_region 으로 자르는 구조라면,
                       훅에도 패딩본을 연결해야 좌표계가 일치한다.

출력
----
Make Tile SEGS 와 동일한 SEG 규약(cropped_image=None, cropped_mask=crop_region 크기의
2D float32, crop_region/bbox=(x1,y1,x2,y2))으로 SEGS 를 생성하므로 Detailer (SEGS),
SEGSPreview, BMK Anima LLLite Per-SEGS Hook 와 그대로 호환된다.
virtual_canvas 모드에서는 SEGS 의 shape 및 모든 좌표가 "패딩된 캔버스" 기준이다.
"""

from __future__ import annotations

import importlib
import logging
import math
import sys
from collections import namedtuple

import numpy as np
try:
    import torch
    import torch.nn.functional as F
except Exception:  # torch 는 ComfyUI 에 항상 존재. 부재 시에도 numpy 경로는 동작.
    torch = None
    F = None

logger = logging.getLogger(__name__)

_TAG = "[ComfyUI_BMK_Nodes::FlexibleTileSEGS]"

_SEG_FIELDS = (
    "cropped_image", "cropped_mask", "confidence",
    "crop_region", "bbox", "label", "control_net_wrapper",
)

_PAD_INFO_TYPE = "BMK_PAD_INFO"


# ─────────────────────────────────────────────────────────────────────────────
# Impact Pack 의 SEG namedtuple 확보 (없으면 동일 규약으로 폴백 정의)
# ─────────────────────────────────────────────────────────────────────────────
def _seg_is_valid(seg) -> bool:
    try:
        return isinstance(seg, type) and tuple(getattr(seg, "_fields", ())) == _SEG_FIELDS
    except Exception:
        return False


def _load_impact_seg():
    # 1) 표준 경로
    for name in ("impact.core", "impact_pack.core"):
        try:
            mod = importlib.import_module(name)
            seg = getattr(mod, "SEG", None)
            if _seg_is_valid(seg):
                return seg
        except Exception:
            pass
    # 2) 이미 로드된 모듈에서 탐색 (타입/필드 검증으로 거짓 매칭 방지)
    for mod in list(sys.modules.values()):
        if mod is None:
            continue
        try:
            seg = getattr(mod, "SEG", None)
        except Exception:
            seg = None
        if _seg_is_valid(seg):
            return seg
    # 3) 폴백: 동일 규약 namedtuple (Detailer 는 속성 접근만 하므로 호환됨)
    logger.warning("%s Impact Pack 의 SEG 를 찾지 못해 호환 namedtuple 로 폴백합니다.", _TAG)
    return namedtuple("SEG", list(_SEG_FIELDS), defaults=[None])


# ─────────────────────────────────────────────────────────────────────────────
# 영역 / 타일 계산 헬퍼
# ─────────────────────────────────────────────────────────────────────────────
def _image_hw(segs):
    """segs[0] (shape) 에서 (H, W) 추출."""
    try:
        shape = segs[0]
        return int(shape[0]), int(shape[1])
    except Exception:
        return 0, 0


def _union_bbox(seg_list, W, H):
    xs1, ys1, xs2, ys2 = [], [], [], []
    for s in seg_list:
        bb = getattr(s, "bbox", None)
        if bb is None or len(bb) < 4:
            continue
        xs1.append(int(bb[0])); ys1.append(int(bb[1]))
        xs2.append(int(bb[2])); ys2.append(int(bb[3]))
    if not xs1:
        return (0, 0, W, H)
    return (
        max(0, min(xs1)), max(0, min(ys1)),
        min(W, max(xs2)), min(H, max(ys2)),
    )


def _grid_tiles(region, columns, rows):
    rx1, ry1, rx2, ry2 = region
    rw, rh = rx2 - rx1, ry2 - ry1
    xs = [rx1 + int(round(rw * c / columns)) for c in range(columns + 1)]
    ys = [ry1 + int(round(rh * r / rows)) for r in range(rows + 1)]
    tiles = []
    for ri in range(rows):
        for ci in range(columns):
            bbox = (xs[ci], ys[ri], xs[ci + 1], ys[ri + 1])
            if bbox[2] > bbox[0] and bbox[3] > bbox[1]:
                tiles.append((bbox, f"tile_r{ri}_c{ci}"))
    return tiles


def _axis_positions(start, length, tile, overlap):
    """[snap_inside] 한 축에서 타일 시작 좌표 리스트 (마지막은 가장자리에 스냅).

    주의: length 가 stride 의 배수가 아니면 마지막 타일 스냅으로 인해 인접 코어끼리
    숨은 겹침이 생긴다. (예: W=2304, tile=1024 → x=[0,1024,1280], 2·3열 코어 768px 겹침)
    이 겹침이 순차 Detailing 시 이중 샘플링(색 진해짐)과 경계 불일치(밀림처럼 보임)의
    원인이 될 수 있다. 균일 타일이 필요하면 boundary_mode="virtual_canvas" 사용.
    """
    if tile >= length:
        return [start]
    step = max(1, tile - overlap)
    last_start = start + length - tile
    pos = []
    p = start
    while p < last_start:
        pos.append(p)
        p += step
    pos.append(last_start)  # 항상 끝을 가장자리에 맞춤
    # 중복 제거(순서 유지)
    seen = set()
    out = []
    for v in pos:
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out


def _axis_positions_virtual(start, length, tile, overlap):
    """[virtual_canvas] 한 축에서 고정 스트라이드 타일 시작 좌표 리스트.

    좌상단(start)을 기준점으로 stride = tile - overlap 간격으로 배치하고,
    마지막 타일은 영역 끝에 스냅하지 않는다(영역 밖으로 넘칠 수 있음).
    따라서 overlap=0 이면 모든 코어가 정확히 맞붙는 완전 분할이 된다.
    """
    stride = max(1, tile - overlap)
    if length <= tile:
        return [start]
    n = math.ceil((length - tile) / stride) + 1
    return [start + i * stride for i in range(n)]


def _tile_size_tiles(region, tile_w, tile_h, overlap):
    rx1, ry1, rx2, ry2 = region
    rw, rh = rx2 - rx1, ry2 - ry1
    xs = _axis_positions(rx1, rw, tile_w, overlap)
    ys = _axis_positions(ry1, rh, tile_h, overlap)
    tiles = []
    for ri, y in enumerate(ys):
        for ci, x in enumerate(xs):
            bbox = (x, y, min(x + tile_w, rx2), min(y + tile_h, ry2))
            if bbox[2] > bbox[0] and bbox[3] > bbox[1]:
                tiles.append((bbox, f"tile_r{ri}_c{ci}"))
    return tiles


def _tile_size_tiles_virtual(region, tile_w, tile_h, overlap):
    """[virtual_canvas] 코어를 클램프하지 않고 항상 tile_w × tile_h 를 유지."""
    rx1, ry1, rx2, ry2 = region
    xs = _axis_positions_virtual(rx1, rx2 - rx1, tile_w, overlap)
    ys = _axis_positions_virtual(ry1, ry2 - ry1, tile_h, overlap)
    tiles = []
    for ri, y in enumerate(ys):
        for ci, x in enumerate(xs):
            tiles.append(((x, y, x + tile_w, y + tile_h), f"tile_r{ri}_c{ci}"))
    return tiles


def _expand_bbox(bbox, region, overlap):
    if overlap <= 0:
        return bbox
    rx1, ry1, rx2, ry2 = region
    bx1, by1, bx2, by2 = bbox
    return (
        max(rx1, bx1 - overlap), max(ry1, by1 - overlap),
        min(rx2, bx2 + overlap), min(ry2, by2 + overlap),
    )


def _gen_base(region, mode, columns, rows, tile_width, tile_height, overlap,
              virtual=False):
    """region 하나를 mode 에 따라 타일 bbox 리스트 [(bbox, label), ...] 로 분할."""
    if mode == "grid":
        base = _grid_tiles(region, int(columns), int(rows))
        if overlap > 0:
            base = [(_expand_bbox(bb, region, int(overlap)), lb) for bb, lb in base]
    elif virtual:  # tile_size + virtual_canvas
        base = _tile_size_tiles_virtual(region, int(tile_width), int(tile_height), int(overlap))
    else:  # tile_size + snap_inside
        base = _tile_size_tiles(region, int(tile_width), int(tile_height), int(overlap))
    return base


def _to_2d_np(m):
    """torch/np 마스크를 2D float32 numpy (H,W) 로 정규화."""
    if m is None:
        return None
    if torch is not None and torch.is_tensor(m):
        a = m.detach().cpu().float().numpy()
    elif isinstance(m, np.ndarray):
        a = m.astype(np.float32)
    else:
        return None
    while a.ndim > 2:        # (B,H,W) / (1,1,H,W) → (H,W)
        a = a[0]
    if a.ndim != 2:
        return None
    return a


def _resize_np(m, th, tw):
    if m.shape == (th, tw) or th <= 0 or tw <= 0:
        return m
    if torch is not None:
        t = torch.from_numpy(m)[None, None]
        t = F.interpolate(t, size=(th, tw), mode="bilinear", align_corners=False)
        return t[0, 0].numpy()
    # torch 부재 시 numpy nearest 폴백
    ys = np.linspace(0, m.shape[0] - 1, th).round().astype(int)
    xs = np.linspace(0, m.shape[1] - 1, tw).round().astype(int)
    return m[ys][:, xs]


def _segs_to_full_mask(seg_list, H, W):
    """입력 SEGS 의 각 cropped_mask 를 crop_region 위치에 합성해 (H,W) 실루엣 복원."""
    canvas = np.zeros((H, W), dtype=np.float32)
    found = False
    for s in seg_list:
        cm = _to_2d_np(getattr(s, "cropped_mask", None))
        cr = getattr(s, "crop_region", None)
        if cm is None or cr is None or len(cr) < 4:
            continue
        x1, y1, x2, y2 = [int(v) for v in cr]
        x1 = max(0, min(x1, W)); x2 = max(0, min(x2, W))
        y1 = max(0, min(y1, H)); y2 = max(0, min(y2, H))
        if x2 <= x1 or y2 <= y1:
            continue
        m = _resize_np(cm, y2 - y1, x2 - x1)
        canvas[y1:y2, x1:x2] = np.maximum(canvas[y1:y2, x1:x2], m)
        found = True
    return canvas if found else None


def _mask_input_to_full(mask, H, W):
    m = _to_2d_np(mask)
    if m is None:
        return None
    return _resize_np(m, H, W)


def _make_tile_seg(SEG, bbox, crop_pixel, W, H, label, person_full=None, empty_eps=1e-4):
    bx1, by1, bx2, by2 = [int(v) for v in bbox]
    cx1 = max(0, bx1 - crop_pixel)
    cy1 = max(0, by1 - crop_pixel)
    cx2 = min(W, bx2 + crop_pixel)
    cy2 = min(H, by2 + crop_pixel)
    cw, ch = cx2 - cx1, cy2 - cy1
    if cw <= 0 or ch <= 0:
        return None

    # cropped_mask: crop_region 크기, bbox 영역만 1.0 (Make Tile SEGS 규약)
    mask = np.zeros((ch, cw), dtype=np.float32)
    mask[by1 - cy1: by2 - cy1, bx1 - cx1: bx2 - cx1] = 1.0

    # 인물 실루엣과 교집합 → 배경 제외. 결과가 비면 타일 자체를 버린다.
    if person_full is not None:
        mask = mask * person_full[cy1:cy2, cx1:cx2]
        if float(mask.sum()) <= empty_eps:
            return None

    crop_region = (cx1, cy1, cx2, cy2)
    bbox_t = (bx1, by1, bx2, by2)
    return SEG(None, mask, 1.0, crop_region, bbox_t, label, None)


# ─────────────────────────────────────────────────────────────────────────────
# virtual_canvas: 이미지 패딩 헬퍼
# ─────────────────────────────────────────────────────────────────────────────
def _pad_nhwc_image(image, pl, pr, pt, pb, fill):
    """ComfyUI IMAGE (B,H,W,C) 를 사방으로 패딩한다. torch/np 모두 지원.

    fill:
      - "reflect": 경계 기준 미러링(권장). 패딩 폭이 원본보다 커도 np 다중 반사로 동작.
      - "edge"   : 가장자리 픽셀 반복(replicate).
      - "gray"/"black"/"white": 단색 채움.
    """
    if pl == pr == pt == pb == 0:
        return image

    is_torch = torch is not None and torch.is_tensor(image)
    if is_torch:
        arr = image.detach().cpu().numpy()
    else:
        arr = np.asarray(image)

    pad_spec = ((0, 0), (int(pt), int(pb)), (int(pl), int(pr)), (0, 0))
    if fill in ("reflect", "edge"):
        try:
            out = np.pad(arr, pad_spec, mode=fill)
        except ValueError:
            logger.warning("%s '%s' 패딩 실패 → 'edge' 로 폴백합니다.", _TAG, fill)
            out = np.pad(arr, pad_spec, mode="edge")
    else:
        val = {"black": 0.0, "gray": 0.5, "white": 1.0}.get(fill, 0.5)
        out = np.pad(arr, pad_spec, mode="constant", constant_values=val)

    if is_torch:
        return torch.from_numpy(np.ascontiguousarray(out)).to(dtype=image.dtype)
    return out


def _blank_image(H, W):
    """image 미연결 시 크기 참조용 검은 이미지 (경고용 폴백)."""
    if torch is not None:
        return torch.zeros((1, int(H), int(W), 3), dtype=torch.float32)
    return np.zeros((1, int(H), int(W), 3), dtype=np.float32)


def _image_spatial(image):
    try:
        return int(image.shape[1]), int(image.shape[2])
    except Exception:
        return -1, -1


# ─────────────────────────────────────────────────────────────────────────────
# ComfyUI 노드
# ─────────────────────────────────────────────────────────────────────────────
class BMKFlexibleTileSEGS:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "segs": ("SEGS",),
                "region_source": (["segs_union_bbox", "full_image", "each_segment_bbox"], {
                    "tooltip": "분할 대상 영역. segs_union_bbox: 모든 bbox 합집합 1개. "
                               "full_image: 전체 이미지. each_segment_bbox: 입력 SEGS 의 "
                               "각 원소(예: 얼굴마다)를 따로 columns×rows / tile_size 로 분할"}),
                "mode": (["grid", "tile_size"],),
                "columns": ("INT", {"default": 1, "min": 1, "max": 64,
                                    "tooltip": "grid 모드: 가로 분할 수"}),
                "rows": ("INT", {"default": 3, "min": 1, "max": 64,
                                 "tooltip": "grid 모드: 세로 분할 수"}),
                "tile_width": ("INT", {"default": 1024, "min": 64, "max": 8192, "step": 8,
                                       "tooltip": "tile_size 모드: 타일 가로(px)"}),
                "tile_height": ("INT", {"default": 1024, "min": 64, "max": 8192, "step": 8,
                                        "tooltip": "tile_size 모드: 타일 세로(px)"}),
                "overlap": ("INT", {"default": 0, "min": 0, "max": 4096, "step": 1,
                                    "tooltip": "타일 간 겹침(px). grid 모드에서는 각 타일을 "
                                               "사방으로 확장하여 겹치게 함. virtual_canvas "
                                               "에서 0이면 코어가 한 픽셀도 겹치지 않음"}),
                "crop_pixel": ("INT", {"default": 64, "min": 0, "max": 4096, "step": 1,
                                       "tooltip": "각 타일 bbox 바깥 컨텍스트 여유(px). "
                                                  "crop_factor 대체"}),
                "tile_mask": (["segs_silhouette", "full_rectangle"], {
                    "tooltip": "segs_silhouette: 입력 SEGS(또는 mask)의 인물 실루엣과 교집합 "
                               "→ 배경 제외(투명). full_rectangle: 타일 사각형 전체"}),
                "boundary_mode": (["snap_inside", "virtual_canvas"], {
                    "default": "snap_inside",
                    "tooltip": "snap_inside: 기존 동작(마지막 타일 가장자리 스냅 + crop 클램프. "
                               "배수가 아니면 코어 숨은 겹침/크롭 크기 제각각). "
                               "virtual_canvas: 좌상단 기준 고정 스트라이드 + 이미지 밖 가상 "
                               "캔버스 패딩 → 모든 타일이 동일 크기 (tile+2*crop). "
                               "image 입력 연결 필수, image/pad_info 출력 사용"}),
                "padding_fill": (["reflect", "edge", "gray", "black", "white"], {
                    "default": "reflect",
                    "tooltip": "virtual_canvas 의 가상 영역 채움 방식. reflect(미러링) 권장 "
                               "→ 경계 타일의 컨텍스트가 자연스럽게 이어짐"}),
            },
            "optional": {
                # 명시 마스크(MASK). 미연결 시 입력 SEGS 의 cropped_mask 로 실루엣 복원.
                "mask": ("MASK",),
                # virtual_canvas 모드에서 필수. 패딩되어 image 출력으로 나간다.
                "image": ("IMAGE",),
            },
        }

    RETURN_TYPES = ("SEGS", "IMAGE", _PAD_INFO_TYPE)
    RETURN_NAMES = ("segs", "image", "pad_info")
    OUTPUT_TOOLTIPS = (
        "타일 SEGS. virtual_canvas 모드에서는 패딩된 캔버스 좌표계 기준",
        "virtual_canvas: 패딩된 이미지(Detailer 에 이것을 연결). snap_inside: 입력 그대로 통과",
        "패딩 정보. Detailer 완료 후 'BMK Virtual Canvas Crop' 에 연결하여 원본 크기 복원",
    )
    FUNCTION = "doit"
    CATEGORY = "BMK/SEGS"
    DESCRIPTION = (
        "SEGS 영역을 grid(columns×rows) 또는 tile_size(px 고정 + overlap) 방식으로 "
        "타일 분할합니다. Make Tile SEGS의 crop_factor(배율) 대신 crop_pixel/overlap을 "
        "픽셀 단위로 직접 지정합니다. 대상 영역은 SEGS 합집합 bbox / 전체 이미지 / "
        "각 세그먼트 개별 중에서 고를 수 있습니다."
    )
    SEARCH_ALIASES = [
        "flexible tile segs", "tile segs", "make tile", "tiling", "grid segs",
        "타일 분할", "타일", "업스케일 타일",
    ]

    def doit(self, segs, region_source, mode, columns, rows,
             tile_width, tile_height, overlap, crop_pixel, tile_mask,
             boundary_mode="snap_inside", padding_fill="reflect",
             mask=None, image=None):
        SEG = _load_impact_seg()

        H, W = _image_hw(segs)
        seg_list_in = list(segs[1]) if segs and len(segs) > 1 and segs[1] else []
        if W <= 0 or H <= 0:
            raise ValueError(f"{_TAG} 유효하지 않은 SEGS shape: {segs[0] if segs else None}")

        virtual = (boundary_mode == "virtual_canvas")
        crop = int(crop_pixel)

        if virtual and image is None:
            raise ValueError(
                f"{_TAG} boundary_mode=virtual_canvas 는 image 입력이 필수입니다. "
                "(노드가 이미지를 패딩해서 내보내야 Detailer 가 이미지 밖 crop_region 을 "
                "처리할 수 있습니다. Detailer 에는 이 노드의 image 출력을 연결하세요.)")
        if image is not None:
            ih, iw = _image_spatial(image)
            if (ih, iw) != (H, W):
                raise ValueError(
                    f"{_TAG} image 크기({iw}x{ih})가 SEGS shape({W}x{H})와 다릅니다. "
                    "업스케일된 동일 해상도의 이미지를 연결하세요.")

        want_silhouette = (tile_mask == "segs_silhouette")
        # 명시 mask(MASK) 가 연결돼 있으면 전 경로 공통으로 우선 사용.
        mask_full = _mask_input_to_full(mask, H, W) if (want_silhouette and mask is not None) else None

        # ── 1단계: (bbox, label, person_full) 수집 (원본 좌표계, 클램프 없음) ──
        entries = []          # [(bbox, label, person_full-or-None), ...]
        used_silhouette = False

        if region_source == "each_segment_bbox":
            # ── 세그먼트별 분할: 각 입력 SEG 의 bbox 를 개별 영역으로 타일링 ──
            if not seg_list_in:
                raise ValueError(f"{_TAG} each_segment_bbox 모드인데 입력 SEGS 가 비었습니다.")
            for i, s in enumerate(seg_list_in):
                bb = getattr(s, "bbox", None)
                if bb is None or len(bb) < 4:
                    continue
                region = (max(0, int(bb[0])), max(0, int(bb[1])),
                          min(W, int(bb[2])), min(H, int(bb[3])))
                if region[2] <= region[0] or region[3] <= region[1]:
                    continue

                # 실루엣: 연결된 mask 우선, 없으면 이 SEG 자체의 cropped_mask 로 복원.
                person_full = None
                if want_silhouette:
                    person_full = mask_full if mask_full is not None else _segs_to_full_mask([s], H, W)
                if person_full is not None:
                    used_silhouette = True

                base = _gen_base(region, mode, columns, rows, tile_width, tile_height,
                                 overlap, virtual=virtual)
                for bbox, label in base:
                    entries.append((bbox, f"seg{i}_{label}", person_full))
        else:
            # ── 단일 영역(union / full_image) 분할 ──
            person_full = None
            if want_silhouette:
                person_full = mask_full if mask_full is not None else _segs_to_full_mask(seg_list_in, H, W)
                if person_full is None:
                    logger.warning(
                        "%s 실루엣 마스크를 만들 소스가 없습니다(입력 SEGS 에 cropped_mask 없음, "
                        "mask 미연결) → full_rectangle 로 진행", _TAG)
            used_silhouette = person_full is not None

            if region_source == "full_image":
                region = (0, 0, W, H)
            else:
                region = _union_bbox(seg_list_in, W, H)
            rx1, ry1, rx2, ry2 = region
            if rx2 <= rx1 or ry2 <= ry1:
                raise ValueError(f"{_TAG} 대상 영역이 비었습니다: {region}")

            base = _gen_base(region, mode, columns, rows, tile_width, tile_height,
                             overlap, virtual=virtual)
            for bbox, label in base:
                entries.append((bbox, label, person_full))

        total_base = len(entries)
        if total_base == 0:
            raise ValueError(f"{_TAG} 생성된 타일이 없습니다. 옵션/마스크를 확인하세요.")

        # ── 2단계: SEG 생성 ──
        out_segs = []

        if not virtual:
            # [기존 동작] crop_region 을 이미지 안으로 클램프.
            for bbox, label, person_full in entries:
                seg = _make_tile_seg(SEG, bbox, crop, W, H, label, person_full=person_full)
                if seg is not None:
                    out_segs.append(seg)

            out_H, out_W = H, W
            out_image = image if image is not None else _blank_image(H, W)
            pad_info = {
                "pad_left": 0, "pad_top": 0, "pad_right": 0, "pad_bottom": 0,
                "width": W, "height": H, "padded_width": W, "padded_height": H,
                "boundary_mode": boundary_mode,
            }
        else:
            # [virtual_canvas] 필요 패딩 계산: 모든 crop_region(비클램프)을 덮도록.
            pl = pt = pr = pb = 0
            for bbox, _, _ in entries:
                pl = max(pl, crop - int(bbox[0]))
                pt = max(pt, crop - int(bbox[1]))
                pr = max(pr, int(bbox[2]) + crop - W)
                pb = max(pb, int(bbox[3]) + crop - H)
            pl = max(0, pl); pt = max(0, pt); pr = max(0, pr); pb = max(0, pb)

            out_W, out_H = W + pl + pr, H + pt + pb

            # 실루엣 마스크는 가상 영역을 0 으로 패딩 (배경/가상 공간은 detail 대상 아님).
            padded_pf_cache = {}

            def _padded_pf(pf):
                if pf is None:
                    return None
                key = id(pf)
                if key not in padded_pf_cache:
                    canvas = np.zeros((out_H, out_W), dtype=np.float32)
                    canvas[pt:pt + H, pl:pl + W] = pf
                    padded_pf_cache[key] = canvas
                return padded_pf_cache[key]

            for bbox, label, person_full in entries:
                shifted = (int(bbox[0]) + pl, int(bbox[1]) + pt,
                           int(bbox[2]) + pl, int(bbox[3]) + pt)
                seg = _make_tile_seg(SEG, shifted, crop, out_W, out_H, label,
                                     person_full=_padded_pf(person_full))
                if seg is not None:
                    out_segs.append(seg)

            out_image = _pad_nhwc_image(image, pl, pr, pt, pb, padding_fill)
            pad_info = {
                "pad_left": pl, "pad_top": pt, "pad_right": pr, "pad_bottom": pb,
                "width": W, "height": H, "padded_width": out_W, "padded_height": out_H,
                "boundary_mode": boundary_mode,
            }

            # 정렬/설정 힌트
            if mode == "tile_size":
                cw, ch = int(tile_width) + 2 * crop, int(tile_height) + 2 * crop
                if cw % 16 or ch % 16:
                    logger.warning(
                        "%s crop 포함 타일 크기 %dx%d 가 16 배수가 아닙니다. VAE/패치 정렬을 "
                        "위해 (tile + 2*crop_pixel) 을 16 배수로 맞추는 것을 권장합니다.",
                        _TAG, cw, ch)
                if int(overlap) > 0:
                    logger.info(
                        "%s virtual_canvas + overlap>0: 코어가 겹치므로 겹침 구간이 두 번 "
                        "디테일링될 수 있습니다(색 진해짐 주의). 균일 완전 분할은 overlap=0.",
                        _TAG)

        if not out_segs:
            raise ValueError(f"{_TAG} 생성된 타일이 없습니다. 옵션/마스크를 확인하세요.")

        dropped = total_base - len(out_segs)
        logger.info("%s mode=%s region_source=%s boundary=%s → %d tile(s) (dropped %d, "
                    "canvas %dx%d%s, crop_pixel=%d, overlap=%d, mask=%s)",
                    _TAG, mode, region_source, boundary_mode, len(out_segs), dropped,
                    out_W, out_H,
                    (f", pad L{pad_info['pad_left']} T{pad_info['pad_top']} "
                     f"R{pad_info['pad_right']} B{pad_info['pad_bottom']}") if virtual else "",
                    crop, int(overlap),
                    "silhouette" if used_silhouette else "rectangle")

        new_segs = ((out_H, out_W), out_segs)
        return (new_segs, out_image, pad_info)


class BMKVirtualCanvasCrop:
    """virtual_canvas 패딩을 잘라 원본 크기로 복원.

    Detailer (SEGS) 가 출력한 (패딩된) 이미지를 pad_info 기준으로 크롭한다.
    입력 이미지가 패딩 캔버스 대비 배율이 다르면(예: Detailer 이후 추가 업스케일)
    배율에 맞춰 비례 크롭한다.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "pad_info": (_PAD_INFO_TYPE,),
            },
        }

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("image",)
    FUNCTION = "doit"
    CATEGORY = "BMK/SEGS"
    DESCRIPTION = (
        "BMK Flexible Tile SEGS의 virtual_canvas 패딩을 pad_info 기준으로 잘라 "
        "원본 크기로 복원합니다. Detailer 이후 추가 업스케일로 배율이 달라졌으면 "
        "그 배율에 맞춰 비례 크롭합니다."
    )
    SEARCH_ALIASES = [
        "virtual canvas crop", "canvas crop", "unpad", "restore size",
        "패딩 제거", "원본 복원", "크롭",
    ]

    def doit(self, image, pad_info):
        d = pad_info if isinstance(pad_info, dict) else {}
        Hc, Wc = _image_spatial(image)

        pw = int(d.get("padded_width") or Wc)
        ph = int(d.get("padded_height") or Hc)
        pl = int(d.get("pad_left", 0))
        pt = int(d.get("pad_top", 0))
        ow = int(d.get("width", pw - pl - int(d.get("pad_right", 0))))
        oh = int(d.get("height", ph - pt - int(d.get("pad_bottom", 0))))

        if pl == pt == 0 and (ow, oh) == (pw, ph):
            return (image,)  # 패딩 없음 → 통과

        sx = Wc / pw if pw > 0 else 1.0
        sy = Hc / ph if ph > 0 else 1.0
        if abs(sx - 1.0) > 1e-6 or abs(sy - 1.0) > 1e-6:
            logger.info("%s 크롭 배율 보정: x%.4f / y%.4f (입력 %dx%d, 캔버스 %dx%d)",
                        _TAG, sx, sy, Wc, Hc, pw, ph)

        x1 = int(round(pl * sx)); y1 = int(round(pt * sy))
        x2 = int(round((pl + ow) * sx)); y2 = int(round((pt + oh) * sy))
        x1 = max(0, min(x1, Wc - 1)); y1 = max(0, min(y1, Hc - 1))
        x2 = max(x1 + 1, min(x2, Wc)); y2 = max(y1 + 1, min(y2, Hc))

        return (image[:, y1:y2, x1:x2, :],)


NODE_CLASS_MAPPINGS = {
    "BMKFlexibleTileSEGS": BMKFlexibleTileSEGS,
    "BMKVirtualCanvasCrop": BMKVirtualCanvasCrop,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "BMKFlexibleTileSEGS": "BMK Flexible Tile SEGS",
    "BMKVirtualCanvasCrop": "BMK Virtual Canvas Crop (Restore)",
}
