"""BMK Design Patch PSD — Design Patch 의 PSD 읽기(크롭 rect·베이스·크롭 소스·결과 수확) / 쓰기(PSDWriter) 모듈.

ComfyUI 비의존 모듈이다(numpy / PIL / cv2 / psd_tools 만 사용, torch·comfy 금지). 노드는 bmk_design_patch.py 에 있다.

배경
----
Design Patch(구 가제 Multi Layer Crop Edit)는 Photoshop 에서 손으로 하던
"크롭 → GPT 편집 → 원위치 SO 배치 → 마스크" 작업을 BMK 노드로 옮긴다. 이 모듈은 그중 PSD 입출력만 맡는다.
측정 근거는 `_proto/reports/photoshop.md`, `final.md §1`, 시제품은 `_proto/work_photoshop/*.py`,
`_proto/so_fix/so_synth_v2.py`(SO 수정본), `_proto/so_fix/psdwalk.py`(원시 검증기).

좌표·배열 규약
--------------
- rect = [x0, y0, x1, y1] (끝 배타적, 캔버스 px, int). quad = 8 floats (TL, TR, BR, BL 의 x, y — 연속 좌표, 픽셀 모서리 기준).
- 이미지 = HxWxC uint8 (RGB / RGBA). 마스크 = HxW uint8 0..255, 255 = 보임(레이어 적용) — PS 마스크와 같은 의미.

읽기
----
- open_psd(path)                      PSDImage (lazy, 큰 파일도 0.2s 내외).
- read_crop_rects(psd, group="auto") shape 레이어(하위 그룹 안 포함)의 vector path knot(정규화×캔버스) → 정수 rect.
                                      vector 가 없으면 live-shape origination bbox, 둘 다 없으면 layer.bbox(경고).
                                      layer.bbox 는 4px inner stroke 안티앨리어싱 때문에 1–2px 크고 비대칭이라 최후 수단.
- read_base(psd, ...)                 (RGB 풀캔버스, info). 클린플레이트 픽셀 레이어를 I2I_base(SO 렌더) 위에 합성.
                                      work_rect = 베이스 bbox ∩ 캔버스. 베이스 레이어가 없으면 보이는 레이어를 수동 합성
                                      (shape·크롭 그룹 제외 — 병합 이미지 psd.topil() 에는 크롭 박스 획이 구워져 있어 쓰지 않음).
  read_base_rgba(psd, ...)            같은 결과의 RGBA 판(알파 = 베이스가 실제로 덮는 영역). crop_sources 에 넘기면
                                      캔버스 여백(작업영역 밖)을 정확히 투명 처리한다.
- crop_sources(psd, crops, base, ...) 크롭 이미지 그룹 안의 같은 이름 그룹/레이어를 수동 합성기(normal blend, opacity,
                                      레이어/그룹 마스크 + background_color, 자기 visible 만, 숨김 부모 무시)로 rect 에 합성해
                                      베이스 위에 올린다. group.composite(force=True) 는 숨김 부모 아래에서 완전 투명을 돌려주므로
                                      쓰지 않는다. 비-normal blend / clipping / fx / 조정·채우기 / shape 레이어는 경고.
                                      이름 중복(03 PSD 의 017)은 범위가 rect 와 가장 잘 맞는(IoU 최대) 노드를 쓴다.
                                      이름에 '가이드'/'guide' 가 들어간 하위 그룹은 크롭 소스에서 제외(경고).
                                      반환은 crops 와 같은 순서의 list(같은 이름의 크롭이 여럿이어도 각자 자기 rect).
- harvest_results(psd, "03.수정", crops)  그룹 아래 SO 레이어마다 임베드 원본 bytes·quad·마스크·crop 매칭(crop_index).
                                      중첩 PSB/PSD SO 는 저장된 병합 이미지(topil)를 PNG 로 바꿔 돌려준다.
                                      원근 quad·SO 워프(customEnvelopeWarp)는 경고만 한다(쓰기 쪽은 affine 만 지원).

쓰기 — PSDWriter
----------------
8-bit RGB 문서, 레이어는 아래→위 순서로 추가한다(psd_tools index 0 = 맨 아래).
- 이름은 생성 후 `layer.name = name` setter 로 넣는다(frompil/Group.new 의 name 인자는 pascal 이름에 그대로 써서
  한글이면 저장 시 macroman UnicodeEncodeError). setter 는 pascal 이름을 '?' 로 두고 luni 블록을 쓴다.
- 픽셀은 RGB + create_mask 로 넣는다. RGBA 로 frompil 하면 알파가 user mask 로 한 번 더 들어가 PS 에서 α² 가 된다.
  (frompil 은 RGB 문서에서 알파를 버리므로 투명도가 필요하면 이 모듈이 transparency 채널(-1)을 직접 채운다.)
- 그룹 마스크: psd_tools 1.14 Group.new 는 그룹 레코드와 '</Layer group>' 경계 레코드가 채널 리스트를 공유해서,
  그룹에 마스크를 만들면 마스크 채널 데이터가 두 번 써지고 파일이 깨진다. 그룹마다 경계 채널 리스트를 분리하고
  save() 에서 레코드 트리를 다시 만든다(_detach_bounding_channels — 인스턴스 단위, 전역 패치 아님).
- Smart Object: psd_tools 에는 SO 생성 API 가 없어 05 PSD 의 템플릿 레이어(`2026-09-11_16-59-23_edit_2`)에서 뽑은
  SoLd / PlLd / open_file 디스크립터를 복제하고 uuid·Trnf·nonAffineTransform·warp bounds·Sz·파일 데이터를 교체한다.
  템플릿은 아래 base64 상수로 내장(재현 스크립트: tools/extract_design_patch_so_template.py, uuid 는 0 으로 정규화).
- lnk2 v8 꼬리(2026-10-07 원인 규명): Photoshop 2026 은 lnk2 'liFD' 항목을 version 8 로 쓰고, v8 항목 끝에
  디스크립터(version 16, {contentID: 고유 uuid}, 117 바이트)를 하나 더 붙인다. psd_tools 1.14 는 이 꼬리를 읽지도 쓰지도
  않아, 복제한 v8 항목이 117 바이트 짧아지고 PS 가 "호환되지 않는 파일"로 거부한다. 이 모듈은 꼬리를
  `LinkedLayer` 의 attrs 서브클래스(_LinkedLayerV8, `tail: bytes`)로 쓴다. 전역 LinkedLayer.write 교체(몽키패치)는 하지 않는다.
  so_synth_v2 의 v8+고유 contentID 출력과 psd_tools 픽셀 PSD 는 사용자가 Photoshop 2026 에서 열림을 확인했다(2026-10-07).
- SO 레이어의 픽셀 캐시(PS 가 다시 렌더하기 전 보이는 픽셀)는 quad bbox 크기. 정수 축정렬 rect 이면
  PIL LANCZOS 리사이즈(PS 배치 재현 MAE 0.07–0.23), affine quad(정합 보정)면 LANCZOS 선축소 후 잔여 affine 를
  premultiplied 로 워프하고 quad 밖은 투명.
- save(path, composite=None) 는 tmp → os.replace 원자 저장. 병합 프리뷰는 RLE. composite(호출자가 만든 평탄화
  HxWx3)를 넘기면 그대로 쓰고, 없으면 psd_tools 가 풀캔버스를 다시 합성한다(큰 문서는 수십 초·수 GB — 작은 문서/테스트용).
  저장한 파일이 2^31-1 바이트를 넘으면 지우고 ValueError(PSD v1 한계, M1 은 PSB 미지원).
  verify_saved(path) 는 psdwalk 와 같은 원시 검사(블록 서명·길이, lnk2 항목 분할·꼬리 117B·contentID 고유, 파일 크기)
  + psd_tools 재열기(SO ↔ lnk2 uuid 연결, 임베드 디코드)로 문제 목록을 돌려준다.

v1 (2026-10, M1)
"""
from __future__ import annotations

import base64
import copy
import functools
import io
import logging
import math
import os
import re
import struct
import time
import uuid as _uuid
from typing import Any, Iterable

import cv2
import numpy as np
from attrs import define
from PIL import Image
from psd_tools import PSDImage
from psd_tools.api.layers import Group, PixelLayer
from psd_tools.constants import BlendMode, ChannelID, Compression, LinkedLayerType, SheetColorType, Tag
from psd_tools.psd.bin_utils import write_bytes
from psd_tools.psd.descriptor import DescriptorBlock
from psd_tools.psd.layer_and_mask import ChannelData, ChannelDataList
from psd_tools.psd.linked_layer import LinkedLayer, LinkedLayers
from psd_tools.psd.tagged_blocks import PlacedLayerData, SmartObjectLayerData, TaggedBlock, TaggedBlocks

logger = logging.getLogger(__name__)
_TAG = "[ComfyUI_BMK_Nodes::DesignPatch]"

# ─────────────────────────────────────────────────────────────────
# 이름 규칙 (auto 탐색)
# ─────────────────────────────────────────────────────────────────
CROP_GROUP_NAMES = ("02.크롭영역", "크롭영역")
CROP_PIXELS_GROUP_NAMES = ("크롭이미지", "01.크롭")
CLEAN_PLATE_NAMES = ("non-gpt 1차수정", "00.Base_마스크용")
BASE_LAYER_NAMES = ("I2I_base",)
HARVEST_GROUP = "03.수정"
_GUIDE_RE = re.compile(r"가이드|guide", re.IGNORECASE)
_GENERATIVE_RE = re.compile(r"^(생성형 채우기|generative fill)", re.IGNORECASE)

LNK2_TAIL_LEN = 117
_ZERO_UUID = "00000000-0000-0000-0000-000000000000"
_LABELS = {
    "red": SheetColorType.RED,
    "orange": SheetColorType.ORANGE,
    "yellow": SheetColorType.YELLOW,
    "green": SheetColorType.GREEN,
    "blue": SheetColorType.BLUE,
    "violet": SheetColorType.VIOLET,
    "gray": SheetColorType.GRAY,
    "grey": SheetColorType.GRAY,
}
_MAX_EMBED_BYTES = 1_900_000_000  # 임베드만으로 확실히 넘는 경우의 조기 거절(파일 한계 검사는 save 가 함)
_PSD_MAX_BYTES = 2 ** 31 - 1  # PSD(v1) 파일 한계 — 넘으면 PSB 필요(M1 은 PSB 쓰기 미지원)

# ─────────────────────────────────────────────────────────────────
# SO 템플릿 상수 — tools/extract_design_patch_so_template.py 가 생성 (손으로 고치지 말 것)
# 출처: 05.디자인보정 결과.psd 레이어 '2026-09-11_16-59-23_edit_2' (Photoshop 2026, lnk2 v8).
# uuid(Idnt / placed / PlLd uuid / contentID)는 0 으로 정규화. 경로·파일명·개인정보 없음(추출 스크립트가 검사).
# >>> SO_TEMPLATE_BEGIN
SO_TEMPLATE_SOURCE = '05 디자인보정 결과 PSD (Photoshop 2026), SO layer 2026-09-11_16-59-23_edit_2, lnk2 v8'
SO_TEMPLATE_SOLD_B64 = (
    "c29MRAAAAAQAAAAQAAAAAQAAAAAAAG51bGwAAAASAAAAAElkbnRURVhUAAAAJQAwADAAMAAwADAAMAAwADAALQAwADAAMAAw"
    "AC0AMAAwADAAMAAtADAAMAAwADAALQAwADAAMAAwADAAMAAwADAAMAAwADAAMAAAAAAABnBsYWNlZFRFWFQAAAAlADAAMAAw"
    "ADAAMAAwADAAMAAtADAAMAAwADAALQAwADAAMAAwAC0AMAAwADAAMAAtADAAMAAwADAAMAAwADAAMAAwADAAMAAwAAAAAAAA"
    "UGdObWxvbmcAAAABAAAACnRvdGFsUGFnZXNsb25nAAAAAQAAAABDcm9wbG9uZwAAAAEAAAAJZnJhbWVTdGVwT2JqYwAAAAEA"
    "AAAAAABudWxsAAAAAgAAAAludW1lcmF0b3Jsb25nAAAAAAAAAAtkZW5vbWluYXRvcmxvbmcAAAJYAAAACGR1cmF0aW9uT2Jq"
    "YwAAAAEAAAAAAABudWxsAAAAAgAAAAludW1lcmF0b3Jsb25nAAAAAAAAAAtkZW5vbWluYXRvcmxvbmcAAAJYAAAACmZyYW1l"
    "Q291bnRsb25nAAAAAQAAAABBbm50bG9uZwAAABAAAAAAVHlwZWxvbmcAAAACAAAAAFRybmZWbExzAAAACGRvdWJAl5QAAAAA"
    "AGRvdWJAow4AAAAAAGRvdWJAn5QAAAAAAGRvdWJAow4AAAAAAGRvdWJAn5QAAAAAAGRvdWJApw4AAAAAAGRvdWJAl5QAAAAA"
    "AGRvdWJApw4AAAAAAAAAABJub25BZmZpbmVUcmFuc2Zvcm1WbExzAAAACGRvdWJAl5QAAAAAAGRvdWJAow4AAAAAAGRvdWJA"
    "n5QAAAAAAGRvdWJAow4AAAAAAGRvdWJAn5QAAAAAAGRvdWJApw4AAAAAAGRvdWJAl5QAAAAAAGRvdWJApw4AAAAAAAAAAAR3"
    "YXJwT2JqYwAAAAEAAAAAAAR3YXJwAAAACAAAAAl3YXJwU3R5bGVlbnVtAAAACXdhcnBTdHlsZQAAAAh3YXJwTm9uZQAAAAl3"
    "YXJwVmFsdWVkb3ViAAAAAAAAAAAAAAAPd2FycFBlcnNwZWN0aXZlZG91YgAAAAAAAAAAAAAAFHdhcnBQZXJzcGVjdGl2ZU90"
    "aGVyZG91YgAAAAAAAAAAAAAACndhcnBSb3RhdGVlbnVtAAAAAE9ybnQAAAAASHJ6bgAAAAZib3VuZHNPYmpjAAAAAQAAAAAA"
    "DmNsYXNzRmxvYXRSZWN0AAAABAAAAABUb3AgZG91YgAAAAAAAAAAAAAAAExlZnRkb3ViAAAAAAAAAAAAAAAAQnRvbWRvdWJA"
    "oAAAAAAAAAAAAABSZ2h0ZG91YkCgAAAAAAAAAAAABnVPcmRlcmxvbmcAAAAEAAAABnZPcmRlcmxvbmcAAAAEAAAAAFN6ICBP"
    "YmpjAAAAAQAAAAAAAFBudCAAAAACAAAAAFdkdGhkb3ViQKAAAAAAAAAAAAAASGdodGRvdWJAoAAAAAAAAAAAAABSc2x0VW50"
    "RiNSc2xAUgAAAAAAAAAAAABjb21wbG9uZ/////8AAAAIY29tcEluZm9PYmpjAAAAAQAAAAAAAG51bGwAAAACAAAABmNvbXBJ"
    "RGxvbmf/////AAAADm9yaWdpbmFsQ29tcElEbG9uZ/////8AAAAAQ2xNZ09iamMAAAABAAAAAAAAQ2xNZwAAAAEAAAAZcGxh"
    "Y2VkTGF5ZXJPQ0lPQ29udmVyc2lvbmVudW0AAAAZcGxhY2VkTGF5ZXJPQ0lPQ29udmVyc2lvbgAAAB5wbGFjZWRMYXllck9D"
    "SU9Db252ZXJ0RW1iZWRkZWQAAAA="
)
SO_TEMPLATE_PLLD_B64 = (
    "cGxjTAAAAAMkMDAwMDAwMDAtMDAwMC0wMDAwLTAwMDAtMDAwMDAwMDAwMDAwAAAAAQAAAAEAAAAQAAAAAkCXlAAAAAAAQKMO"
    "AAAAAABAn5QAAAAAAECjDgAAAAAAQJ+UAAAAAABApw4AAAAAAECXlAAAAAAAQKcOAAAAAAAAAAAAAAAAEAAAAAEAAAAAAAR3"
    "YXJwAAAACAAAAAl3YXJwU3R5bGVlbnVtAAAACXdhcnBTdHlsZQAAAAh3YXJwTm9uZQAAAAl3YXJwVmFsdWVkb3ViAAAAAAAA"
    "AAAAAAAPd2FycFBlcnNwZWN0aXZlZG91YgAAAAAAAAAAAAAAFHdhcnBQZXJzcGVjdGl2ZU90aGVyZG91YgAAAAAAAAAAAAAA"
    "CndhcnBSb3RhdGVlbnVtAAAAAE9ybnQAAAAASHJ6bgAAAAZib3VuZHNPYmpjAAAAAQAAAAAADmNsYXNzRmxvYXRSZWN0AAAA"
    "BAAAAABUb3AgZG91YgAAAAAAAAAAAAAAAExlZnRkb3ViAAAAAAAAAAAAAAAAQnRvbWRvdWJAoAAAAAAAAAAAAABSZ2h0ZG91"
    "YkCgAAAAAAAAAAAABnVPcmRlcmxvbmcAAAAEAAAABnZPcmRlcmxvbmcAAAAEAAAA"
)
SO_TEMPLATE_OPEN_FILE_B64 = (
    "AAAAEAAAAAEAAAAAAABudWxsAAAAAQAAAAhjb21wSW5mb09iamMAAAABAAAAAAAAbnVsbAAAAAIAAAAGY29tcElEbG9uZ///"
    "//8AAAAOb3JpZ2luYWxDb21wSURsb25n/////w=="
)
SO_TEMPLATE_TAIL_B64 = (
    "AAAAEAAAAAEAAAAAAABudWxsAAAAAQAAAAljb250ZW50SURURVhUAAAAJQAwADAAMAAwADAAMAAwADAALQAwADAAMAAwAC0A"
    "MAAwADAAMAAtADAAMAAwADAALQAwADAAMAAwADAAMAAwADAAMAAwADAAMAAA"
)
SO_TEMPLATE_ITEM_FIELDS = {'version': 8, 'filetype': 'png ', 'creator_hex': '00000000', 'child_id': '\x00', 'mod_time': 0.0, 'lock_state': 0}
# <<< SO_TEMPLATE_END


# ═════════════════════════════════════════════════════════════════
# 공통 도우미
# ═════════════════════════════════════════════════════════════════
def _half_up(v: float) -> int:
    return int(math.floor(float(v) + 0.5))


def _rect_iou(r1, r2) -> float:
    ix0, iy0 = max(r1[0], r2[0]), max(r1[1], r2[1])
    ix1, iy1 = min(r1[2], r2[2]), min(r1[3], r2[3])
    inter = max(0, ix1 - ix0) * max(0, iy1 - iy0)
    a1 = max(0, r1[2] - r1[0]) * max(0, r1[3] - r1[1])
    a2 = max(0, r2[2] - r2[0]) * max(0, r2[3] - r2[1])
    union = a1 + a2 - inter
    return float(inter) / union if union > 0 else 0.0


def _check_rect(rect, what="rect") -> list[int]:
    r = [int(v) for v in rect]
    if len(r) != 4 or r[2] <= r[0] or r[3] <= r[1]:
        raise ValueError(f"{what} 는 [x0, y0, x1, y1] (x1>x0, y1>y0) 이어야 합니다: {rect!r}")
    return r


def quad_from_rect(rect, matrix=None) -> list[float]:
    """rect → quad 8 floats (TL, TR, BR, BL).

    matrix(2x3, 선택)는 rect 로컬 **픽셀 중심 좌표**(cv2 규약)에서 cand 픽셀을 base 쪽으로 옮기는 정방향 affine.
    quad 는 연속(모서리) 좌표이므로 u_px = u_c − 0.5 로 바꿔 적용한 뒤 +0.5 로 되돌린다.
    """
    x0, y0, x1, y1 = _check_rect(rect)
    w, h = x1 - x0, y1 - y0
    corners = [(0.0, 0.0), (float(w), 0.0), (float(w), float(h)), (0.0, float(h))]
    out: list[float] = []
    if matrix is None:
        for cx, cy in corners:
            out += [x0 + cx, y0 + cy]
        return out
    m = np.asarray(matrix, dtype=np.float64).reshape(2, 3)
    for cx, cy in corners:
        ux, uy = cx - 0.5, cy - 0.5
        px = m[0, 0] * ux + m[0, 1] * uy + m[0, 2] + 0.5
        py = m[1, 0] * ux + m[1, 1] * uy + m[1, 2] + 0.5
        out += [x0 + float(px), y0 + float(py)]
    return out


def _check_quad(quad) -> list[float]:
    if quad is None:
        raise ValueError("quad(8 floats: TL, TR, BR, BL 의 x,y)가 필요합니다")
    q = [float(v) for v in quad]
    if len(q) != 8 or not all(math.isfinite(v) for v in q):
        raise ValueError(f"quad 는 유한한 float 8개여야 합니다: {quad!r}")
    return q


def quad_bbox(quad) -> list[int]:
    """quad 를 덮는 정수 bbox [floor(min), floor(min), ceil(max), ceil(max)] (1e-6 허용)."""
    q = _check_quad(quad)
    xs, ys = q[0::2], q[1::2]
    eps = 1e-6
    return [int(math.floor(min(xs) + eps)), int(math.floor(min(ys) + eps)),
            int(math.ceil(max(xs) - eps)), int(math.ceil(max(ys) - eps))]


def quad_kind(quad, tol: float = 1e-3) -> str:
    """'rect_int'(정수 축정렬) | 'parallelogram'(affine) | 'perspective'(미지원)."""
    q = _check_quad(quad)
    (ax, ay), (bx, by), (cx, cy), (dx_, dy_) = (q[0], q[1]), (q[2], q[3]), (q[4], q[5]), (q[6], q[7])
    if (abs(ay - by) < 1e-6 and abs(cy - dy_) < 1e-6 and abs(ax - dx_) < 1e-6 and abs(bx - cx) < 1e-6
            and all(abs(v - round(v)) < 1e-6 for v in q) and bx > ax and dy_ > ay):
        return "rect_int"
    if abs((bx + dx_ - ax) - cx) <= tol and abs((by + dy_ - ay) - cy) <= tol:
        return "parallelogram"
    return "perspective"


def _find_group(root, names: Iterable[str]):
    names = tuple(names)
    for lyr in root.descendants():
        if lyr.is_group() and lyr.name in names:
            return lyr
    return None


def _find_layer(root, names: Iterable[str], kinds: Iterable[str]):
    names, kinds = tuple(names), tuple(kinds)
    for lyr in root.descendants():
        if lyr.name in names and lyr.kind in kinds:
            return lyr
    return None


def _is_auto(v) -> bool:
    return v is None or (isinstance(v, str) and v.strip().lower() in ("", "auto"))


def _is_none(v) -> bool:
    return isinstance(v, str) and v.strip().lower() in ("none", "off", "-")


def open_psd(path: str) -> PSDImage:
    """PSD/PSB 열기(lazy). 파일이 없으면 ValueError."""
    if not path or not os.path.isfile(path):
        raise ValueError(f"PSD 파일을 찾을 수 없습니다: {path!r}")
    return PSDImage.open(path)


# ═════════════════════════════════════════════════════════════════
# 읽기 — 크롭 rect
# ═════════════════════════════════════════════════════════════════
def _vector_rect(layer, W: int, H: int):
    vm = layer.vector_mask
    if vm is None:
        return None, 0
    xs, ys = [], []
    for sub in vm.paths:
        for knot in sub:
            ay, ax = knot.anchor  # psd_tools: (y, x) 정규화 좌표
            xs.append(float(ax) * W)
            ys.append(float(ay) * H)
    if not xs:
        return None, 0
    return (min(xs), min(ys), max(xs), max(ys)), len(xs)


def _origination_rect(layer):
    """Rectangle / RoundedRectangle live shape 의 keyOriginShapeBBox (캔버스 px)."""
    for o in layer.origination or []:
        if o.invalidated or int(o.origin_type) not in (1, 2):
            continue
        bb = o.bbox
        if bb[2] > bb[0] and bb[3] > bb[1]:
            return tuple(float(v) for v in bb)
    return None


def read_crop_rects(psd, group="auto") -> list[dict]:
    """크롭 영역 그룹의 shape 레이어 → [{"name","rect","rect_source","layer_index","visible","warnings"}].

    group auto = 이름이 '02.크롭영역' 또는 '크롭영역' 인 그룹(중첩 포함, descendants 순서 첫 일치).
    layer_index = 그룹 descendants 순서 index(0 = 맨 아래, 하위 그룹 안 shape 포함). shape 가 아닌 레이어는 건너뛴다.
    """
    names = CROP_GROUP_NAMES if _is_auto(group) else (str(group),)
    grp = _find_group(psd, names)
    if grp is None:
        raise ValueError(f"크롭 영역 그룹을 찾을 수 없습니다: {' / '.join(names)}")
    W, H = psd.size
    out = []
    for idx, lyr in enumerate(grp.descendants()):
        if lyr.kind != "shape":
            if not lyr.is_group():
                logger.debug("%s read_crop_rects: shape 아님 건너뜀 %s (%s)", _TAG, lyr.name, lyr.kind)
            continue
        warns: list[str] = []
        vrect, nknots = _vector_rect(lyr, W, H)
        orect = _origination_rect(lyr)
        if vrect is not None:
            rect_f, src = vrect, "vector"
            if nknots != 4:
                warns.append(f"vector path knot 가 {nknots}개(사각형 아님) - knot bbox 사용")
        elif orect is not None:
            rect_f, src = orect, "origination"
        else:
            rect_f, src = tuple(float(v) for v in lyr.bbox), "bbox"
            warns.append("vector path / origination 없음 → layer.bbox 사용(stroke 때문에 1-2px 클 수 있음)")
        rect = [_half_up(v) for v in rect_f]
        if any(abs(v - r) > 0.01 for v, r in zip(rect_f, rect)):
            warns.append("rect 가 정수가 아님: " + ", ".join("%.3f" % v for v in rect_f))
        if vrect is not None and orect is not None:
            orr = [_half_up(v) for v in orect]
            if orr != rect:
                warns.append(f"vector rect {rect} != origination rect {orr} (vector 사용)")
        if rect[2] <= rect[0] or rect[3] <= rect[1]:
            warns.append("빈 rect - 건너뜀")
            logger.warning("%s 크롭 rect 가 비어 건너뜀: %s %s", _TAG, lyr.name, rect)
            continue
        for wmsg in warns:
            logger.warning("%s 크롭 %s: %s", _TAG, lyr.name, wmsg)
        out.append({"name": lyr.name, "rect": rect, "rect_source": src, "layer_index": idx,
                    "visible": bool(lyr.visible), "warnings": warns})
    return out


# ═════════════════════════════════════════════════════════════════
# 읽기 — 수동 합성기 (normal blend 전용)
# ═════════════════════════════════════════════════════════════════
def _mask_window(layer, rect) -> np.ndarray | None:
    """레이어(또는 그룹)의 user mask 를 rect 창으로 (uint16 0..255). 마스크 없음/비활성 → None. 밖 = background_color."""
    if not layer.has_mask():
        return None
    mk = layer.mask
    if mk is None or mk.disabled:
        return None
    x0, y0, x1, y1 = rect
    win = Image.new("L", (x1 - x0, y1 - y0), int(mk.background_color))
    mi = mk.topil()
    if mi is not None:
        win.paste(mi.convert("L"), (mk.left - x0, mk.top - y0))
    return np.asarray(win).astype(np.uint16)


def _layer_window(layer, rect) -> np.ndarray | None:
    """비그룹 레이어의 픽셀을 rect 창 RGBA uint8 로. 마스크/불투명도는 알파에 곱한다(정수, 시제품 group_comp2 와 동일)."""
    x0, y0, x1, y1 = rect
    im = layer.topil()
    if im is None:
        return None
    win = Image.new("RGBA", (x1 - x0, y1 - y0), (0, 0, 0, 0))
    win.paste(im.convert("RGBA"), (layer.left - x0, layer.top - y0))
    return _apply_node_alpha(layer, np.array(win), rect)


def _apply_node_alpha(node, rgba: np.ndarray, rect) -> np.ndarray:
    """레이어/그룹의 opacity 와 user mask 를 알파에 곱한다(제자리 수정 후 반환)."""
    alpha = rgba[..., 3].astype(np.uint16)
    if node.opacity < 255:
        alpha = (alpha * int(node.opacity) + 127) // 255
    m = _mask_window(node, rect)
    if m is not None:
        alpha = alpha * m // 255
    rgba[..., 3] = alpha.astype(np.uint8)
    return rgba


_RENDER_KINDS = ("pixel", "smartobject", "type")


def _composite_children(group, rect, warns: list, path: str = "", skip=()) -> np.ndarray:
    """group 의 보이는 자식을 rect 창에 normal 합성(RGBA uint8). skip = 통째로 뺄 노드들."""
    x0, y0, x1, y1 = rect
    acc = Image.new("RGBA", (x1 - x0, y1 - y0), (0, 0, 0, 0))
    for child in group:  # 아래 → 위
        if not child.visible or any(child is s for s in skip):
            continue
        label = (path + "/" + child.name) if path else child.name
        if child.is_group():
            if _GUIDE_RE.search(child.name or ""):
                warns.append(f"가이드 그룹 제외: {label}")
                continue
            if child.blend_mode not in (BlendMode.PASS_THROUGH, BlendMode.NORMAL):
                warns.append(f"그룹 blend {child.blend_mode.name} 무시(normal 로 합성): {label}")
            sub = _composite_children(child, rect, warns, label, skip)
            sub = _apply_node_alpha(child, sub, rect)
            acc = Image.alpha_composite(acc, Image.fromarray(sub, "RGBA"))
            continue
        kind = child.kind
        if kind == "shape":
            warns.append(f"shape 레이어 제외(벡터 렌더 불가): {label}")
            continue
        if kind not in _RENDER_KINDS:
            warns.append(f"{kind} 레이어 제외(조정/채우기 미지원): {label}")
            continue
        if child.blend_mode != BlendMode.NORMAL:
            warns.append(f"blend {child.blend_mode.name} → normal 로 합성: {label}")
        if child.clipping:
            warns.append(f"clipping 레이어(클리핑 무시): {label}")
        if child.has_effects():
            warns.append(f"레이어 효과(fx) 무시: {label}")
        arr = _layer_window(child, rect)
        if arr is None:
            continue
        acc = Image.alpha_composite(acc, Image.fromarray(arr, "RGBA"))
    return np.array(acc)


def _node_extent(node):
    """크롭 이미지 노드의 범위: 마스크 bbox 우선, 그룹이면 보이는 자식 bbox 합, 레이어면 bbox."""
    if node.has_mask() and not node.mask.disabled:
        return list(node.mask.bbox)
    if node.is_group():
        boxes = [c.bbox for c in node.descendants() if not c.is_group() and c.visible and c.bbox[2] > c.bbox[0]]
        if not boxes:
            return [0, 0, 0, 0]
        return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]
    return list(node.bbox)


# ═════════════════════════════════════════════════════════════════
# 읽기 — 베이스
# ═════════════════════════════════════════════════════════════════
def _auto_base_layer(psd):
    return _find_layer(psd, BASE_LAYER_NAMES, ("smartobject", "pixel"))


def read_base_rgba(psd, clean_plate_layer="auto", base_layer="auto") -> tuple[np.ndarray, dict]:
    """read_base 의 RGBA 판. 알파 = 베이스(+클린플레이트)가 실제로 덮는 영역(여백은 0).

    clean_plate_layer / base_layer: "auto" | 레이어 이름 | "none"(사용 안 함).
    auto: 클린플레이트 = 픽셀 레이어 'non-gpt 1차수정' 또는 '00.Base_마스크용', 베이스 = 'I2I_base'(SO 렌더 = topil).
    work_rect = 베이스 레이어 bbox 를 캔버스로 자른 것(캔버스 밖으로 나간 픽셀이 있으면 경고).
    베이스 레이어가 없으면 보이는 레이어를 수동 합성한다(method "composite": shape 레이어·크롭 영역 그룹·크롭 이미지
    그룹 제외, work_rect = 캔버스 전체 — 여백도 불투명). 저장된 병합 이미지(psd.topil)는 보이는 크롭 박스 획이
    구워져 있어 쓰지 않는다.
    """
    W, H = psd.size
    full = (0, 0, W, H)
    warns: list[str] = []
    if _is_none(base_layer):
        base = None
    elif _is_auto(base_layer):
        base = _auto_base_layer(psd)
    else:
        base = _find_layer(psd, (str(base_layer),), ("smartobject", "pixel"))
        if base is None:
            raise ValueError(f"베이스 레이어를 찾을 수 없습니다: {base_layer!r}")
    if _is_none(clean_plate_layer):
        clean = None
    elif _is_auto(clean_plate_layer):
        clean = _find_layer(psd, CLEAN_PLATE_NAMES, ("pixel",))
    else:
        clean = _find_layer(psd, (str(clean_plate_layer),), ("pixel", "smartobject"))
        if clean is None:
            raise ValueError(f"클린플레이트 레이어를 찾을 수 없습니다: {clean_plate_layer!r}")

    info: dict[str, Any] = {"canvas": [W, H], "base_layer": None, "clean_plate_layer": None,
                            "method": "", "work_rect": [0, 0, W, H], "warnings": warns}
    if base is None:
        sink: list[str] = []
        skip = [g for g in (_find_group(psd, CROP_GROUP_NAMES), _find_group(psd, CROP_PIXELS_GROUP_NAMES)) if g is not None]
        rgba = _composite_children(psd, full, sink, skip=skip)
        n_shape = sum(1 for w in sink if w.startswith("shape "))
        warns.append(f"베이스 레이어 없음 → 보이는 레이어 수동 합성(shape {n_shape}개·크롭 그룹 제외, "
                     f"work_rect = 캔버스 전체: 여백도 불투명)")
        warns.extend(w for w in sink if not w.startswith("shape "))
        info["method"] = "composite"
    else:
        arr = _layer_window(base, full)
        if arr is None:
            raise ValueError(f"베이스 레이어 픽셀을 읽을 수 없습니다: {base.name}")
        acc = Image.fromarray(arr, "RGBA")
        l, t, r, b = (int(v) for v in base.bbox)
        wr_ = [max(0, l), max(0, t), min(W, r), min(H, b)]
        if wr_[2] <= wr_[0] or wr_[3] <= wr_[1]:
            raise ValueError(f"베이스 레이어가 캔버스 밖에 있습니다: {base.name} bbox {[l, t, r, b]}")
        if wr_ != [l, t, r, b]:
            warns.append(f"베이스 bbox {[l, t, r, b]} 가 캔버스를 넘음 → work_rect {wr_} 로 자름")
        info["base_layer"] = base.name
        info["work_rect"] = wr_
        info["method"] = "base"
        if clean is not None:
            carr = _layer_window(clean, full)
            if carr is not None:
                acc = Image.alpha_composite(acc, Image.fromarray(carr, "RGBA"))
                info["clean_plate_layer"] = clean.name
                info["method"] = "base+clean"
        elif not _is_none(clean_plate_layer):
            warns.append("클린플레이트 레이어 없음 → 베이스만 사용")
        rgba = np.array(acc)
    for wmsg in warns:
        logger.info("%s read_base: %s", _TAG, wmsg)
    return rgba, info


def read_base(psd, clean_plate_layer="auto", base_layer="auto") -> tuple[np.ndarray, dict]:
    """(RGB HxWx3 uint8 풀캔버스, info). 덮이지 않은 여백 픽셀은 0(검정). work_rect = 베이스 레이어 bbox ∩ 캔버스."""
    rgba, info = read_base_rgba(psd, clean_plate_layer, base_layer)
    return np.ascontiguousarray(rgba[..., :3]), info


# ═════════════════════════════════════════════════════════════════
# 읽기 — 크롭 소스
# ═════════════════════════════════════════════════════════════════
def _base_window(base: np.ndarray, base_alpha: np.ndarray, rect) -> np.ndarray:
    x0, y0, x1, y1 = rect
    H, W = base.shape[:2]
    out = np.zeros((y1 - y0, x1 - x0, 4), np.uint8)
    sx0, sy0, sx1, sy1 = max(0, x0), max(0, y0), min(W, x1), min(H, y1)
    if sx1 > sx0 and sy1 > sy0:
        out[sy0 - y0:sy1 - y0, sx0 - x0:sx1 - x0, :3] = base[sy0:sy1, sx0:sx1, :3]
        out[sy0 - y0:sy1 - y0, sx0 - x0:sx1 - x0, 3] = base_alpha[sy0:sy1, sx0:sx1]
    return out


def crop_sources(psd, crops, base_rgb, crop_pixels_group="auto", *, work_rect=None) -> list[dict]:
    """크롭별 소스 RGBA → crops 와 같은 순서의 [{"name","rgba","kind","warnings","alpha_frac","rect","node"}].

    같은 이름의 크롭이 여럿이어도 각 항목은 자기 rect 로 만든다(이름으로 찾지 말고 위치로 짝지을 것).
    base_rgb: read_base 의 RGB(HxWx3) 또는 read_base_rgba 의 RGBA(HxWx4).
      RGB 이면 work_rect(인자, 없으면 베이스 레이어 bbox, 그것도 없으면 캔버스 전체) 밖을 알파 0 으로 본다.
    kind: "group_composite"(같은 이름 노드를 수동 합성해 베이스 위에 올림) | "base_crop"(베이스 그대로).
    rect 가 캔버스 밖이면 0 패딩 + 알파 0. alpha_frac = 알파 < 255 픽셀 비율.
    """
    base = np.asarray(base_rgb)
    if base.ndim != 3 or base.shape[2] not in (3, 4) or base.dtype != np.uint8:
        raise ValueError("base_rgb 는 HxWx3 또는 HxWx4 uint8 이어야 합니다")
    W, H = psd.size
    if base.shape[0] != H or base.shape[1] != W:
        raise ValueError(f"base 크기 {base.shape[1]}x{base.shape[0]} != 캔버스 {W}x{H}")
    if base.shape[2] == 4:
        base_alpha = base[..., 3]
    else:
        if work_rect is None:
            bl = _auto_base_layer(psd)
            work_rect = list(bl.bbox) if bl is not None else [0, 0, W, H]
        wr = _check_rect(work_rect, "work_rect")
        base_alpha = np.zeros((H, W), np.uint8)
        base_alpha[max(0, wr[1]):min(H, wr[3]), max(0, wr[0]):min(W, wr[2])] = 255

    grp = None
    if not _is_none(crop_pixels_group):
        names = CROP_PIXELS_GROUP_NAMES if _is_auto(crop_pixels_group) else (str(crop_pixels_group),)
        grp = _find_group(psd, names)
        if grp is None and not _is_auto(crop_pixels_group):
            raise ValueError(f"크롭 이미지 그룹을 찾을 수 없습니다: {crop_pixels_group!r}")
    children = list(grp) if grp is not None else []

    out: list[dict] = []
    for c in crops:
        name = c["name"]
        rect = _check_rect(c["rect"], f"crop {name} rect")
        warns: list[str] = []
        under = _base_window(base, base_alpha, rect)
        cands = [n for n in children if n.name == name]
        kind, node_desc = "base_crop", None
        result = under
        if cands:
            if len(cands) > 1:
                scored = sorted(((_rect_iou(_node_extent(n), rect), i, n) for i, n in enumerate(cands)),
                                key=lambda t: (-t[0], t[1]))
                node = scored[0][2]
                warns.append(f"같은 이름 노드 {len(cands)}개 → rect IoU 최대({scored[0][0]:.2f}) {node.kind} 사용")
            else:
                node = cands[0]
            content = None
            if node.is_group():
                content = _composite_children(node, rect, warns, node.name)
                content = _apply_node_alpha(node, content, rect)
            elif node.kind in _RENDER_KINDS:
                if node.blend_mode != BlendMode.NORMAL:
                    warns.append(f"blend {node.blend_mode.name} → normal 로 합성: {node.name}")
                content = _layer_window(node, rect)
            else:
                warns.append(f"{node.kind} 노드는 합성 불가 → 베이스 사용")
            if content is not None:
                result = np.array(Image.alpha_composite(Image.fromarray(under, "RGBA"),
                                                        Image.fromarray(content, "RGBA")))
                kind = "group_composite"
                node_desc = f"{node.kind}:{node.name}"
        oob = rect[0] < 0 or rect[1] < 0 or rect[2] > W or rect[3] > H
        if oob:
            warns.append("rect 가 캔버스 밖으로 나감 → 0 패딩(알파 0)")
        alpha_frac = float((result[..., 3] < 255).mean())
        for wmsg in warns:
            logger.info("%s crop_sources %s: %s", _TAG, name, wmsg)
        out.append({"name": name, "rgba": result, "kind": kind, "warnings": warns, "alpha_frac": alpha_frac,
                    "rect": rect, "node": node_desc})
    return out


# ═════════════════════════════════════════════════════════════════
# 읽기 — 결과 수확(harvest)
# ═════════════════════════════════════════════════════════════════
def _png_bytes(im: Image.Image) -> bytes:
    buf = io.BytesIO()
    im.save(buf, "PNG", compress_level=6)
    return buf.getvalue()


def _rasterize_nested(raw: bytes) -> Image.Image:
    sub = PSDImage.open(io.BytesIO(raw))
    im = sub.topil()
    if im is None:
        im = sub.composite()
    if im is None:
        raise ValueError("병합 이미지 없음")
    return im


def _warp_mesh_dev(wp, quad) -> float | None:
    """warpCustom 메시가 항등 격자(bounds 의 linspace)에서 벗어난 최대 거리(캔버스 px). 정사각 메시가 아니면 None."""
    if b"customEnvelopeWarp" not in wp:
        return None
    mp = wp[b"customEnvelopeWarp"][b"meshPoints"]
    hx = np.asarray(mp[b"Hrzn"].values, np.float64)
    vy = np.asarray(mp[b"Vrtc"].values, np.float64)
    n = int(round(hx.size ** 0.5))
    if n < 2 or n * n != hx.size or vy.size != hx.size:
        return None
    b = wp[b"bounds"]
    t, l, bt, r = (float(b[k]) for k in (b"Top ", b"Left", b"Btom", b"Rght"))
    gx, gy = np.meshgrid(np.linspace(l, r, n), np.linspace(t, bt, n))
    s = max(math.hypot(quad[2] - quad[0], quad[3] - quad[1]) / max(r - l, 1e-9),
            math.hypot(quad[6] - quad[0], quad[7] - quad[1]) / max(bt - t, 1e-9))
    return float(np.hypot(hx.reshape(n, n) - gx, vy.reshape(n, n) - gy).max() * s)


def harvest_results(psd, group=HARVEST_GROUP, crops=None, *, warnings=None) -> list[dict]:
    """group 아래(재귀) SO 레이어마다 결과 dict. 순서 = 아래→위(z_index 0 = 맨 아래).

    {"layer","group_path","data","filetype","filename","src_size","quad","bbox","visible_effective",
     "mask": None|{"arr": HxW uint8 (rect 창, 밖 = background_color),"rect","owner":"layer|group","bg","mask_bbox","owner_name"},
     "z_index","crop_index","crop_name","crop_match":"group_name|iou|None","crop_iou","category","filetype_orig",
     "rasterized","warnings"}
    - 마스크: 레이어 마스크, 없으면 가장 가까운 부모 그룹 마스크(group 자신과 그 위는 제외).
      mask.rect = quad bbox ∪ 매칭된 crop rect ∪ 마스크 데이터 bbox (창 밖 = background_color).
    - png/jpg 는 임베드 bytes 그대로. psb/psd 는 병합 이미지(topil)를 PNG 로 바꿔 data 로(실패 시 건너뛰고 경고).
    - category: png | jpg | nested_psd | generative_fill(PS 생성형 채우기, 파일 generative.psb) | other.
    - crop 매칭: 부모 그룹 이름 == crop name 우선(같은 이름 크롭이 여럿이면 quad bbox IoU 최대, 경고), 아니면
      quad bbox 와 crop rect IoU 최대(≥0.5), 없으면 None + 경고. crop_index = crops 안 위치(이름보다 이것으로 짝지을 것).
    - 원근 quad, 항등이 아닌 SO 워프(warpCustom 메시 / 프리셋 워프 값)는 경고만 한다(합성·출력은 affine).
    warnings(list, 선택): 건너뛴 항목 등 문서 단위 경고를 여기에 추가한다.
    """
    sink = warnings if warnings is not None else []
    if group in (None, ""):
        root = psd
    else:
        root = _find_group(psd, (str(group),))
        if root is None:
            raise ValueError(f"결과 그룹을 찾을 수 없습니다: {group!r}")
    crop_list = [(c["name"], _check_rect(c["rect"], "crop rect")) for c in (crops or [])]
    out: list[dict] = []
    def skip(msg: str) -> None:
        logger.warning("%s harvest: %s", _TAG, msg)
        sink.append(msg)

    z = 0
    for layer in root.descendants():
        if layer.kind != "smartobject":
            continue
        warns: list[str] = []
        so = layer.smart_object
        try:
            raw = so.data
        except (ValueError, OSError) as ex:  # lnk2 항목 없음 / 링크 파일 없음
            skip(f"SO 데이터 없음, 건너뜀: {layer.name} ({ex})")
            continue
        ftl = so.filetype.strip().lower()
        fname = so.filename
        rasterized = False
        if ftl == "png":
            data, ft_out, category = raw, "png", "png"
        elif ftl in ("jpg", "jpeg"):
            data, ft_out, category = raw, "jpg", "jpg"
        elif ftl in ("8bpb", "8bps", "psb", "psd") or raw[:4] == b"8BPS":
            category = "generative_fill" if (fname.lower() == "generative.psb"
                                             or _GENERATIVE_RE.search(layer.name or "")) else "nested_psd"
            try:
                im = _rasterize_nested(raw)
            except Exception as ex:  # psd_tools 파서/합성 오류는 종류가 다양 — 그 SO 만 건너뜀
                skip(f"중첩 PSB 합성 실패, 건너뜀: {layer.name} ({type(ex).__name__}: {ex})")
                continue
            data, ft_out, rasterized = _png_bytes(im), "png", True
        else:
            category = "other"
            try:
                im = Image.open(io.BytesIO(raw))
                im.load()
            except OSError as ex:
                skip(f"알 수 없는 SO 형식({ftl}), 건너뜀: {layer.name} ({ex})")
                continue
            data, ft_out, rasterized = _png_bytes(im), "png", True
            warns.append(f"SO 형식 {ftl} → PNG 로 변환")
        try:
            src_size = list(Image.open(io.BytesIO(data)).size)
        except OSError as ex:
            skip(f"임베드 이미지 디코드 실패, 건너뜀: {layer.name} ({ex})")
            continue
        tb = so.transform_box
        if tb is None:
            l0, t0, r0, b0 = layer.bbox
            quad = [float(v) for v in (l0, t0, r0, t0, r0, b0, l0, b0)]
            warns.append("transform_box 없음 → layer.bbox 를 quad 로 사용")
        else:
            quad = [float(v) for v in tb]
        qbox = quad_bbox(quad)
        if quad_kind(quad) == "perspective":
            warns.append("quad 가 평행사변형이 아님(원근 변형) → 합성·출력은 가장 가까운 평행사변형으로 근사")
        try:
            wp = so.warp
        except ValueError:  # SoLd 없는(PlLd 만 있는) 옛 SO
            wp = None
        style = getattr(wp.get(b"warpStyle"), "enum", b"warpNone") if wp is not None else b"warpNone"
        if style == b"warpCustom":
            dev = _warp_mesh_dev(wp, quad)
            if dev is None or dev > 0.5:
                warns.append("SO 워프 변형은 합성·출력에서 무시됨" if dev is None
                             else f"SO 워프 변형(최대 {dev:.1f}px)은 합성·출력에서 무시됨")
        elif style != b"warpNone" and any(float(wp.get(k) or 0) != 0
                                          for k in (b"warpValue", b"warpPerspective", b"warpPerspectiveOther")):
            warns.append(f"SO 워프 변형({style.decode(errors='replace')})은 합성·출력에서 무시됨")
        if layer.opacity < 255:
            warns.append(f"레이어 불투명도 {layer.opacity}/255")
        if layer.blend_mode != BlendMode.NORMAL:
            warns.append(f"blend {layer.blend_mode.name}")

        # 그룹 경로 (root 포함, root 가 psd 면 제외)
        chain = []
        p = layer.parent
        while p is not None and p is not psd:
            chain.append(p)
            if p is root:
                break
            p = p.parent
        group_path = [g.name for g in reversed(chain)]

        # crop 매칭 (이름이 같은 크롭이 여럿일 수 있어 위치 index 로 기록)
        crop_idx, how, iou = None, None, 0.0
        p = layer.parent
        while p is not None and p is not psd:
            same = [i for i, (n, _) in enumerate(crop_list) if n == p.name]
            if same:
                crop_idx, how = max(same, key=lambda i: (_rect_iou(qbox, crop_list[i][1]), -i)), "group_name"
                if len(same) > 1:
                    warns.append(f"그룹 이름 {p.name} 인 크롭이 {len(same)}개 → quad 와 IoU 최대인 것으로 매칭")
                break
            if p is root:
                break
            p = p.parent
        if crop_idx is not None:
            iou = _rect_iou(qbox, crop_list[crop_idx][1])
        elif crop_list:
            best = max(((_rect_iou(qbox, r), i) for i, (_n, r) in enumerate(crop_list)), key=lambda t: (t[0], -t[1]))
            if best[0] >= 0.5:
                crop_idx, how, iou = best[1], "iou", best[0]
            else:
                warns.append(f"crop 매칭 실패(최대 IoU {best[0]:.2f})")

        # 마스크: 레이어 → 가장 가까운 부모 그룹(root 제외). 창 = quad bbox ∪ 매칭된 crop rect ∪ 마스크 데이터 bbox
        # (사용자가 옮긴 SO 도 crop 로컬 손 마스크를 그대로 잘라 쓰고, 정합으로 quad 가 움직여도 마스크를 잃지 않게)
        msrc, owner = None, None
        if layer.has_mask() and not layer.mask.disabled:
            msrc, owner = layer, "layer"
        elif layer.has_mask():
            warns.append("레이어 마스크 비활성 → 무시")
        parent_masked = None
        p = layer.parent
        while p is not None and p is not root and p is not psd:
            if p.has_mask() and not p.mask.disabled:
                parent_masked = p
                break
            p = p.parent
        if msrc is None and parent_masked is not None:
            msrc, owner = parent_masked, "group"
        elif msrc is not None and parent_masked is not None:
            warns.append(f"부모 그룹 마스크도 있음({parent_masked.name}) - 레이어 마스크만 기록")
        mask = None
        if msrc is not None:
            boxes = [list(qbox)]
            if crop_idx is not None:
                boxes.append(crop_list[crop_idx][1])
            mb = [int(v) for v in msrc.mask.bbox]
            if mb[2] > mb[0] and mb[3] > mb[1]:
                boxes.append(mb)
            mrect = [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]
            arr = _mask_window(msrc, mrect)
            mask = {"arr": arr.astype(np.uint8), "rect": mrect, "owner": owner,
                    "bg": int(msrc.mask.background_color), "mask_bbox": [int(v) for v in msrc.mask.bbox],
                    "owner_name": msrc.name}
        for wmsg in warns:
            logger.info("%s harvest %s: %s", _TAG, layer.name, wmsg)
        out.append({
            "layer": layer.name, "group_path": group_path, "data": data, "filetype": ft_out,
            "filename": fname, "src_size": src_size, "quad": quad, "bbox": [int(v) for v in layer.bbox],
            "visible_effective": bool(layer.is_visible()), "mask": mask, "z_index": z, "crop_index": crop_idx,
            "crop_name": None if crop_idx is None else crop_list[crop_idx][0], "crop_match": how,
            "crop_iou": round(float(iou), 4),
            "category": category, "filetype_orig": ftl, "rasterized": rasterized, "warnings": warns,
        })
        z += 1
    return out


# ═════════════════════════════════════════════════════════════════
# 원시 검사기 (psdwalk 이식) — 블록 서명/길이, lnk2 항목 분할
# ═════════════════════════════════════════════════════════════════
# PSB(version 2)에서 길이 필드가 8 바이트인 키
_PSB_LONG_KEYS = {b"LMsk", b"Lr16", b"Lr32", b"Layr", b"Mt16", b"Mt32", b"Mtrn", b"Alph", b"FMsk", b"lnk2",
                  b"FEid", b"FXid", b"PxSD", b"lnkE", b"pths", b"cinf"}


class _RawReader:
    def __init__(self, fp):
        self.fp = fp

    def tell(self) -> int:
        return self.fp.tell()

    def seek(self, pos: int) -> None:
        self.fp.seek(pos)

    def read(self, n: int) -> bytes:
        b = self.fp.read(n)
        if len(b) != n:
            raise EOFError("short read at %d" % self.fp.tell())
        return b

    def u8(self) -> int:
        return struct.unpack(">B", self.read(1))[0]

    def u16(self) -> int:
        return struct.unpack(">H", self.read(2))[0]

    def i16(self) -> int:
        return struct.unpack(">h", self.read(2))[0]

    def u32(self) -> int:
        return struct.unpack(">I", self.read(4))[0]

    def u64(self) -> int:
        return struct.unpack(">Q", self.read(8))[0]


def raw_walk(path: str, want_raw: Iterable[bytes] = ()) -> dict:
    """PSD/PSB 원시 순회: 헤더, 섹션, 레이어 레코드의 tagged block, 전역 tagged block.

    want_raw 에 든 키는 블록 bytes 를 'raw' 로 담는다. 구조 이상은 out['problems'] 에 문자열로.
    """
    want = set(want_raw)
    out: dict[str, Any] = {"path": path, "problems": []}
    with open(path, "rb") as fh:
        r = _RawReader(fh)
        sig = r.read(4)
        ver = r.u16()
        r.read(6)
        ch, hgt, wid, depth, mode = r.u16(), r.u32(), r.u32(), r.u16(), r.u16()
        out["header"] = dict(sig=sig, ver=ver, ch=ch, h=hgt, w=wid, depth=depth, mode=mode)
        if sig != b"8BPS" or ver not in (1, 2):
            out["problems"].append("bad header %r v%d" % (sig, ver))
            return out
        L = r.u64 if ver == 2 else r.u32
        cm = r.u32()
        r.seek(r.tell() + cm)
        ir = r.u32()
        r.seek(r.tell() + ir)
        lmi_len = L()
        lmi_start = r.tell()
        lmi_end = lmi_start + lmi_len
        out["sections"] = dict(colormode=cm, image_resources=ir, lmi=(lmi_start, lmi_len))
        li_len = L()
        li_start = r.tell()
        li_end = li_start + li_len
        layers = []
        if li_len:
            n = r.i16()
            for i in range(abs(n)):
                rec: dict[str, Any] = {"i": i, "start": r.tell()}
                t, l, b, rr = struct.unpack(">4i", r.read(16))
                rec["rect"] = (l, t, rr, b)
                nch = r.u16()
                chans = []
                for _ in range(nch):
                    cid = r.i16()
                    clen = L()
                    chans.append((cid, clen))
                rec["channels"] = chans
                bsig = r.read(4)
                bkey = r.read(4)
                opa, clip, flags, filler = struct.unpack(">4B", r.read(4))
                rec.update(blend=bkey, opacity=opa, clipping=clip, flags=flags, bsig=bsig)
                if bsig != b"8BIM":
                    out["problems"].append("layer %d: bad blend sig %r" % (i, bsig))
                ex_len = r.u32()
                ex_start = r.tell()
                ex_end = ex_start + ex_len
                mlen = r.u32()
                rec["mask_len"] = mlen
                r.seek(r.tell() + mlen)
                blen = r.u32()
                r.seek(r.tell() + blen)
                nlen = r.u8()
                rec["pascal_name"] = r.read(nlen)
                r.read((4 - ((nlen + 1) % 4)) % 4)
                blocks = []
                while r.tell() + 12 <= ex_end:
                    bstart = r.tell()
                    s = r.read(4)
                    if s not in (b"8BIM", b"8B64"):
                        out["problems"].append("layer %d: bad block sig %r at %d" % (i, s, bstart))
                        break
                    k = r.read(4)
                    ln = r.u64() if (ver == 2 and k in _PSB_LONG_KEYS) else r.u32()
                    if r.tell() + ln > ex_end:
                        out["problems"].append("layer %d: block %r overruns record" % (i, k))
                        break
                    blk: dict[str, Any] = {"key": k, "len": ln, "start": bstart}
                    if k in want:
                        blk["raw"] = r.read(ln)
                    else:
                        r.seek(r.tell() + ln)
                    blocks.append(blk)
                    p = r.tell()
                    if p < ex_end and p % 2:
                        r.read(1)
                if r.tell() != ex_end:
                    out["problems"].append("layer %d: extra data ends at %d, expected %d" % (i, r.tell(), ex_end))
                    r.seek(ex_end)
                rec["blocks"] = blocks
                layers.append(rec)
            ch_total = sum(c[1] for rec in layers for c in rec["channels"])
            if r.tell() + ch_total > li_end:
                out["problems"].append("channel data (%d B) overruns layer info" % ch_total)
            else:
                # 채널 이미지 데이터 정렬 검사: 레코드가 선언한 길이대로 걸으며 각 채널의 압축 코드(0..3)를 확인하고,
                # 색/투명도 채널(id >= -1)은 RAW 크기 / RLE 행 길이 합이 선언 길이와 맞는지 본다(어긋나면 파일 깨짐).
                pos = r.tell()
                bad = 0
                bpp = depth // 8 if depth >= 8 else 0
                cnt = 4 if ver == 2 else 2
                for rec in layers:
                    l_, t_, r_, b_ = rec["rect"]
                    cw, chh = max(0, r_ - l_), max(0, b_ - t_)
                    for cid, clen in rec["channels"]:
                        msg = None
                        if clen >= 2:
                            r.seek(pos)
                            comp = r.u16()
                            if comp > 3:
                                msg = "bad compression %d" % comp
                            elif cid >= -1 and bpp and cw and chh:
                                if comp == 0 and clen - 2 != cw * chh * bpp:
                                    msg = "RAW size %d != %d" % (clen - 2, cw * chh * bpp)
                                elif comp == 1:
                                    rows = r.read(cnt * chh)
                                    fmt = ">%d%s" % (chh, "I" if cnt == 4 else "H")
                                    if 2 + cnt * chh + sum(struct.unpack(fmt, rows)) != clen:
                                        msg = "RLE row counts != declared length %d" % clen
                            elif cid >= -1 and (cw == 0 or chh == 0) and clen != 2 and comp in (0, 1):
                                msg = "empty channel with %d bytes" % clen
                        if msg:
                            bad += 1
                            if bad <= 3:
                                out["problems"].append("layer %d channel %d: %s at %d (채널 데이터 어긋남)"
                                                       % (rec["i"], cid, msg, pos))
                        pos += clen
                if bad > 3:
                    out["problems"].append("... channel data problems total %d" % bad)
            out["channel_bytes"] = ch_total
            r.seek(li_end)
        out["layers"] = layers
        gm_len = r.u32()
        r.seek(r.tell() + gm_len)
        gblocks = []
        while r.tell() + 12 <= lmi_end:
            bstart = r.tell()
            s = r.read(4)
            if s not in (b"8BIM", b"8B64"):
                out["problems"].append("global: bad block sig %r at %d (lmi_end %d)" % (s, bstart, lmi_end))
                break
            k = r.read(4)
            ln = r.u64() if (ver == 2 and k in _PSB_LONG_KEYS) else r.u32()
            if r.tell() + ln > lmi_end:
                out["problems"].append("global: block %r overruns section" % (k,))
                break
            blk = {"key": k, "len": ln, "start": bstart}
            if k in want:
                blk["raw"] = r.read(ln)
            else:
                r.seek(r.tell() + ln)
            gblocks.append(blk)
            padn = (4 - (r.tell() - lmi_start) % 4) % 4
            if padn and r.tell() + padn <= lmi_end:
                r.read(padn)
        if r.tell() != lmi_end:
            out["problems"].append("global blocks end at %d, lmi_end %d" % (r.tell(), lmi_end))
        out["global"] = gblocks
        r.seek(lmi_end)
        out["image_data_compression"] = r.u16()
    return out


def lnk2_items(raw: bytes) -> tuple[list[dict], int]:
    """lnk2 블록 payload → ([{len, kind, ver, uuid, body}], 소비 바이트). 항목은 8B 길이 + 본문 + 4 정렬 패딩."""
    items = []
    p = 0
    while p + 8 <= len(raw):
        ln = struct.unpack(">Q", raw[p:p + 8])[0]
        body = raw[p + 8:p + 8 + ln]
        kind = body[:4]
        ver = struct.unpack(">I", body[4:8])[0] if len(body) >= 8 else -1
        ul = body[8] if len(body) > 8 else 0
        uid = body[9:9 + ul]
        items.append({"len": ln, "kind": kind, "ver": ver, "uuid": uid, "body": body})
        p += 8 + ln
        p += (4 - ln % 4) % 4
    return items, p


def lnk2_item_tail(body: bytes) -> bytes:
    """lnk2 항목 본문에서 psd_tools 가 읽지 않는 꼬리(v8 contentID 디스크립터)를 잘라 돌려준다."""
    li = LinkedLayer.frombytes(body)
    return body[len(li.tobytes()):]


# ═════════════════════════════════════════════════════════════════
# 쓰기 — SO 템플릿
# ═════════════════════════════════════════════════════════════════
@define(repr=False)
class _LinkedLayerV8(LinkedLayer):
    """psd_tools LinkedLayer + Photoshop 2026 의 v8 꼬리 디스크립터(contentID, 117B).

    전역 LinkedLayer.write 를 바꾸지 않고 이 서브클래스 인스턴스만 꼬리를 쓴다(다른 노드의 psd_tools 사용에 영향 없음).
    """

    tail: bytes = b""

    def write(self, fp, padding: int = 1, **kwargs: Any) -> int:
        written = LinkedLayer.write(self, fp, padding=1, **kwargs)  # padding=1 → 패딩 없음(so_synth_v2 와 동일)
        if self.tail:
            written += write_bytes(fp, self.tail)
        return written


class _SoTemplate:
    __slots__ = ("sold", "plld", "open_file", "tail", "fields")

    def __init__(self, sold, plld, open_file, tail, fields):
        self.sold = sold
        self.plld = plld
        self.open_file = open_file
        self.tail = tail
        self.fields = fields


@functools.lru_cache(maxsize=1)
def _so_template() -> _SoTemplate:
    if not (SO_TEMPLATE_SOLD_B64 and SO_TEMPLATE_PLLD_B64 and SO_TEMPLATE_OPEN_FILE_B64 and SO_TEMPLATE_TAIL_B64):
        raise ValueError("SO 템플릿 상수가 비어 있습니다 - tools/extract_design_patch_so_template.py --write 로 생성하세요")
    sold = SmartObjectLayerData.frombytes(base64.b64decode(SO_TEMPLATE_SOLD_B64))
    plld = PlacedLayerData.frombytes(base64.b64decode(SO_TEMPLATE_PLLD_B64))
    open_file = DescriptorBlock.frombytes(base64.b64decode(SO_TEMPLATE_OPEN_FILE_B64), padding=1)
    tail = base64.b64decode(SO_TEMPLATE_TAIL_B64)
    if len(tail) != LNK2_TAIL_LEN or tail.count(_ZERO_UUID.encode("utf-16-be")) != 1 or b"contentID" not in tail:
        raise ValueError("SO 템플릿 꼬리 상수가 올바르지 않습니다")
    return _SoTemplate(sold, plld, open_file, tail, dict(SO_TEMPLATE_ITEM_FIELDS))


def _make_lnk2_tail() -> bytes:
    """새 uuid4 contentID 를 넣은 v8 꼬리(117B). 항목마다 고유해야 한다."""
    cid = str(_uuid.uuid4())
    return _so_template().tail.replace(_ZERO_UUID.encode("utf-16-be"), cid.encode("utf-16-be"))


def _build_so_blocks(file_uid: str, placed_uid: str, quad, src_size):
    """템플릿을 복제해 (SoLd, PlLd) 데이터 객체를 만든다(so_synth_v2.build 와 같은 필드 교체)."""
    tpl = _so_template()
    q = _check_quad(quad)
    sw, sh = int(src_size[0]), int(src_size[1])
    sold = copy.deepcopy(tpl.sold)
    dd = sold.data
    dd[b"Idnt"].value = file_uid + "\x00"
    dd[b"placed"].value = placed_uid + "\x00"
    for i, v in enumerate(q):
        dd[b"Trnf"][i].value = v
        dd[b"nonAffineTransform"][i].value = v
    dd[b"warp"][b"bounds"][b"Btom"].value = float(sh)
    dd[b"warp"][b"bounds"][b"Rght"].value = float(sw)
    dd[b"Sz  "][b"Wdth"].value = float(sw)
    dd[b"Sz  "][b"Hght"].value = float(sh)
    plld = copy.deepcopy(tpl.plld)
    plld.uuid = file_uid.encode("ascii")
    plld.transform = tuple(q)
    plld.warp[b"bounds"][b"Btom"].value = float(sh)
    plld.warp[b"bounds"][b"Rght"].value = float(sw)
    return sold, plld


def _build_lnk2_item(file_uid: str, filename: str, data: bytes) -> _LinkedLayerV8:
    """임베드 PNG 의 lnk2 항목(v8 + 고유 contentID 꼬리). 스칼라 필드는 템플릿 항목 그대로."""
    tpl = _so_template()
    fld = tpl.fields
    return _LinkedLayerV8(
        kind=LinkedLayerType.DATA, version=fld["version"], uuid=file_uid, filename=filename + "\x00",
        filetype=fld["filetype"].encode("latin-1"), creator=bytes.fromhex(fld["creator_hex"]), filesize=None,
        open_file=copy.deepcopy(tpl.open_file), linked_file=None, timestamp=None, data=data,
        child_id=fld["child_id"], mod_time=fld["mod_time"], lock_state=fld["lock_state"], tail=_make_lnk2_tail(),
    )


# ═════════════════════════════════════════════════════════════════
# 쓰기 — SO 픽셀 캐시
# ═════════════════════════════════════════════════════════════════
def _as_pil(img) -> Image.Image:
    if isinstance(img, Image.Image):
        return img
    arr = np.asarray(img)
    if arr.dtype != np.uint8:
        raise ValueError("이미지 배열은 uint8 이어야 합니다")
    if arr.ndim == 2:
        return Image.fromarray(arr, "L")
    if arr.ndim == 3 and arr.shape[2] == 3:
        return Image.fromarray(arr, "RGB")
    if arr.ndim == 3 and arr.shape[2] == 4:
        return Image.fromarray(arr, "RGBA")
    raise ValueError(f"지원하지 않는 이미지 모양: {arr.shape}")


def _has_partial_alpha(im: Image.Image) -> bool:
    if "A" not in im.getbands():
        return False
    lo, _ = im.getchannel("A").getextrema()
    return lo < 255


def render_so_cache(src, quad) -> tuple[np.ndarray, np.ndarray | None, int, int]:
    """SO 원본을 quad 에 배치한 픽셀 캐시 → (rgb HxWx3 uint8, alpha HxW uint8 | None, left, top).

    정수 축정렬 rect 이면 PIL LANCZOS 리사이즈(PS 재현). affine(평행사변형)이면 LANCZOS 로 변 길이까지 선축소 후
    잔여 affine 를 premultiplied float 로 cv2 워프(INTER_CUBIC), quad 밖은 알파 0. 원근(비평행) quad 는 ValueError.
    """
    im = _as_pil(src)
    im.load()
    q = _check_quad(quad)
    kind = quad_kind(q)
    if kind == "perspective":
        raise ValueError("평행사변형이 아닌 quad(원근 변형)는 지원하지 않습니다")
    alpha_src = _has_partial_alpha(im)
    if kind == "rect_int":
        l, t, r, b = int(round(q[0])), int(round(q[1])), int(round(q[4])), int(round(q[5]))
        if alpha_src:
            rs = np.array(im.convert("RGBA").resize((r - l, b - t), Image.LANCZOS))
            return np.ascontiguousarray(rs[..., :3]), np.ascontiguousarray(rs[..., 3]), l, t
        rs = np.array(im.convert("RGB").resize((r - l, b - t), Image.LANCZOS))
        return rs, None, l, t

    bx0, by0, bx1, by1 = quad_bbox(q)
    bw, bh = max(1, bx1 - bx0), max(1, by1 - by0)
    p0 = np.array([q[0], q[1]], np.float64)
    ex_v = np.array([q[2] - q[0], q[3] - q[1]], np.float64)   # 원본 가로 방향
    ey_v = np.array([q[6] - q[0], q[7] - q[1]], np.float64)   # 원본 세로 방향
    tw = max(1, int(round(float(np.hypot(*ex_v)))))
    th = max(1, int(round(float(np.hypot(*ey_v)))))
    pre = np.asarray(im.convert("RGBA").resize((tw, th), Image.LANCZOS), dtype=np.float32) / 255.0
    a = pre[..., 3:4]
    prem = np.concatenate([pre[..., :3] * a, a], axis=2)
    A = np.stack([ex_v / tw, ey_v / th], axis=1)  # 2x2, 연속 좌표 u → x
    off = p0 - np.array([bx0, by0], np.float64)
    tvec = A @ np.array([0.5, 0.5]) + off - 0.5      # 픽셀 중심 규약으로 변환
    M = np.hstack([A, tvec[:, None]]).astype(np.float64)
    warped = cv2.warpAffine(prem, M, (bw, bh), flags=cv2.INTER_CUBIC,
                            borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))
    alpha = np.clip(warped[..., 3], 0.0, 1.0)
    rgb = warped[..., :3] / np.maximum(alpha, 1e-6)[..., None]
    rgb = np.clip(rgb * 255.0 + 0.5, 0, 255).astype(np.uint8)
    rgb[alpha <= 1e-6] = 0
    alpha8 = np.clip(alpha * 255.0 + 0.5, 0, 255).astype(np.uint8)
    return rgb, alpha8, bx0, by0


# ═════════════════════════════════════════════════════════════════
# 쓰기 — PSDWriter
# ═════════════════════════════════════════════════════════════════
_BAD_FN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _safe_filename(name: str, ext: str) -> str:
    stem = _BAD_FN.sub("_", name or "").strip(" .") or "layer"
    return stem[:100] + "." + ext


def _label_value(label):
    if label is None or label == "":
        return None
    if isinstance(label, SheetColorType):
        return label
    key = str(label).strip().lower()
    if key in ("none", "no_color"):
        return SheetColorType.NO_COLOR
    if key not in _LABELS:
        raise ValueError(f"label 은 {sorted(_LABELS)} 중 하나여야 합니다: {label!r}")
    return _LABELS[key]


def _detach_bounding_channels(grp) -> None:
    """psd_tools 1.14 Group.new 는 그룹 레코드와 '</Layer group>' 경계 레코드가 같은 ChannelDataList 를 공유한다.

    그대로 그룹 마스크를 만들면 마스크 채널 데이터가 경계 레코드에도 한 번 더 써져(채널 정보에는 없음) 이후 채널
    데이터가 어긋나고 파일이 깨진다(재열기 시 'not a valid Compression'). 이 그룹 인스턴스의 경계 레코드에만
    빈 채널 리스트를 따로 준다(전역 패치 아님). 레코드 트리는 save() 에서 다시 만든다.
    """
    if getattr(grp, "_bounding_channels", None) is grp._channels:
        n = len(grp._bounding_record.channel_info)
        fresh = ChannelDataList([ChannelData(compression=Compression.RAW, data=b"") for _ in range(n)])
        grp._set_bounding_records(grp._bounding_record, fresh)


def _set_transparency(layer, alpha: np.ndarray) -> None:
    """frompil(RGB) 로 만든 레이어의 transparency 채널(-1)을 alpha 로 교체(user mask 와 별개)."""
    h, w = alpha.shape
    version = layer._psd._record.header.version
    for i, ci in enumerate(layer._record.channel_info):
        if ci.id == ChannelID.TRANSPARENCY_MASK:
            cd = layer._channels[i]
            cd.set_data(np.ascontiguousarray(alpha, dtype=np.uint8).tobytes(), w, h, 8, version)
            ci.length = cd._length
            return
    raise ValueError("transparency 채널을 찾을 수 없습니다")


class PSDWriter:
    """psd_tools 기반 8-bit RGB PSD 작성기. 레이어는 아래→위 순서로 추가한다.

    mask 인자 = {"arr": HxW uint8 (255 = 보임), "left": int, "top": int, "bg": 0|255}.
    label = "green" | "red" | (기타 PS 라벨색) | None.
    """

    def __init__(self, size, depth: int = 8):
        if int(depth) != 8:
            raise ValueError(f"8-bit 문서만 지원합니다(psd_tools 는 16/32-bit 레이어 픽셀을 쓰지 못함): depth={depth}")
        try:
            W, H = int(size[0]), int(size[1])
        except Exception:
            raise ValueError(f"size 는 (width, height) 여야 합니다: {size!r}")
        if not (1 <= W <= 30000 and 1 <= H <= 30000):
            raise ValueError(f"PSD 크기는 1..30000 px 이어야 합니다: {W}x{H}")
        self.size = (W, H)
        self.psd = PSDImage.new("RGB", (W, H), color=(255, 255, 255))
        self._items: list[_LinkedLayerV8] = []
        self.warnings: list[str] = []

    # ── 내부 ──────────────────────────────────────────────────
    def _parent(self, parent):
        return self.psd if parent is None else parent

    def _set_name(self, layer, name) -> None:
        name = str(name)
        if len(name) >= 256:
            self.warnings.append(f"레이어 이름 255자 초과 → 잘라냄: {name[:40]}…")
            name = name[:255]
        layer.name = name  # setter: pascal '?' + luni (한글 안전)

    def _apply_mask(self, layer, mask) -> None:
        if mask is None:
            return
        arr = np.asarray(mask["arr"])
        left, top, bg = int(mask.get("left", 0)), int(mask.get("top", 0)), int(mask.get("bg", 0))
        if arr.ndim != 2 or arr.size == 0:
            raise ValueError(f"mask arr 는 HxW 여야 합니다: {arr.shape}")
        if np.issubdtype(arr.dtype, np.floating):  # 분석 모듈의 float 0..1 마스크
            arr = np.clip(arr * 255.0 + 0.5, 0, 255).astype(np.uint8)
        elif arr.dtype != np.uint8:
            raise ValueError(f"mask arr 는 uint8 또는 float 0..1 이어야 합니다: {arr.dtype}")
        if bg not in (0, 255):
            raise ValueError(f"mask bg 는 0 또는 255: {bg}")
        layer.create_mask(Image.fromarray(np.ascontiguousarray(arr), "L"), top=top, left=left)
        layer._record.mask_data.background_color = bg

    def _finish(self, layer, visible, label) -> None:
        if not visible:
            layer.visible = False
        lv = _label_value(label)
        if lv is not None:
            layer.sheet_color = lv

    def _new_pixel_layer(self, parent, name, rgb: np.ndarray, alpha, left: int, top: int):
        par = self._parent(parent)
        lyr = PixelLayer.frompil(Image.fromarray(np.ascontiguousarray(rgb), "RGB"), par, name="Layer",
                                 top=int(top), left=int(left))
        self._set_name(lyr, name)
        if alpha is not None and int(np.min(alpha)) < 255:
            _set_transparency(lyr, alpha)
        return lyr

    # ── 공개 API ──────────────────────────────────────────────
    def add_group(self, parent, name, visible=True, mask=None, label=None, open_folder=True):
        """그룹 추가(pass-through). parent=None 이면 문서 최상위."""
        par = self._parent(parent)
        if par is self.psd:
            grp = self.psd.create_group(name="Group", open_folder=bool(open_folder))
        else:
            grp = Group.new(par, name="Group", open_folder=bool(open_folder))
            grp.blend_mode = BlendMode.PASS_THROUGH
        _detach_bounding_channels(grp)
        self._set_name(grp, name)
        self._apply_mask(grp, mask)
        self._finish(grp, visible, label)
        return grp

    def add_pixel(self, parent, name, rgb, left, top, visible=True, mask=None, label=None):
        """픽셀 레이어. rgb = HxWx3 uint8 (HxWx4 면 4번째 채널을 transparency 채널로 — user mask 아님)."""
        arr = np.asarray(rgb)
        if arr.dtype != np.uint8 or arr.ndim != 3 or arr.shape[2] not in (3, 4) or arr.shape[0] < 1 or arr.shape[1] < 1:
            raise ValueError(f"rgb 는 HxWx3(또는 4) uint8 이어야 합니다: {arr.shape} {arr.dtype}")
        alpha = arr[..., 3] if arr.shape[2] == 4 else None
        lyr = self._new_pixel_layer(parent, name, arr[..., :3], alpha, left, top)
        self._apply_mask(lyr, mask)
        self._finish(lyr, visible, label)
        return lyr

    def add_smart_object(self, parent, name, data: bytes, filetype="png", src_size=None, quad=None,
                         visible=True, mask=None, label=None, cache_rgb=None, *, filename=None):
        """임베드 Smart Object 레이어(풀해상도 원본 bytes, lnk2 v8 + 고유 contentID 꼬리).

        filetype 이 png 가 아니면(jpg/webp 등) PNG 로 무손실 재인코딩해 임베드한다(PS 확인된 형식은 png 뿐, 경고).
        quad = 8 floats (affine 허용). cache_rgb = quad bbox 크기 HxWx3(또는 4) uint8, None 이면 render_so_cache.
        """
        if not isinstance(data, (bytes, bytearray)) or not data:
            raise ValueError("data 는 비어 있지 않은 bytes 여야 합니다")
        data = bytes(data)
        try:
            img = Image.open(io.BytesIO(data))
            img.load()
        except OSError as ex:
            raise ValueError(f"SO 데이터를 이미지로 열 수 없습니다: {name} ({ex})")
        ft = (filetype or "png").strip().lower()
        if ft != "png" or img.format != "PNG":
            self.warnings.append(f"{name}: {ft}/{img.format} → PNG 로 재인코딩해 임베드")
            data = _png_bytes(img)
            ft = "png"
        sw, sh = img.size
        if src_size is not None and (int(src_size[0]), int(src_size[1])) != (sw, sh):
            raise ValueError(f"src_size {tuple(src_size)} 가 실제 이미지 크기 {(sw, sh)} 와 다릅니다: {name}")
        q = _check_quad(quad)
        if quad_kind(q) == "perspective":
            raise ValueError(f"평행사변형이 아닌 quad 는 지원하지 않습니다: {name}")
        if cache_rgb is None:
            rgb, alpha, left, top = render_so_cache(img, q)
        else:
            bx0, by0, bx1, by1 = quad_bbox(q)
            arr = np.asarray(cache_rgb)
            if arr.dtype != np.uint8 or arr.ndim != 3 or arr.shape[2] not in (3, 4) \
                    or arr.shape[:2] != (by1 - by0, bx1 - bx0):
                raise ValueError(f"cache_rgb 는 quad bbox 크기 {(by1 - by0, bx1 - bx0)} 의 uint8 RGB 여야 합니다: {arr.shape}")
            rgb, alpha, left, top = arr[..., :3], (arr[..., 3] if arr.shape[2] == 4 else None), bx0, by0
        total = sum(len(it.data) for it in self._items) + len(data)
        if total > _MAX_EMBED_BYTES:
            raise ValueError(f"임베드 합계 {total / 1e9:.2f}GB - PSD 2GB 한계(M1 은 PSB 미지원)")

        lyr = self._new_pixel_layer(parent, name, rgb, alpha, left, top)
        self._apply_mask(lyr, mask)
        file_uid = str(_uuid.uuid4())
        sold, plld = _build_so_blocks(file_uid, str(_uuid.uuid4()), q, (sw, sh))
        tb = lyr._record.tagged_blocks
        tb[Tag.SMART_OBJECT_LAYER_DATA1] = TaggedBlock(key=Tag.SMART_OBJECT_LAYER_DATA1, data=sold)
        tb[Tag.PLACED_LAYER2] = TaggedBlock(key=Tag.PLACED_LAYER2, data=plld)
        fn = os.path.splitext(filename)[0] + "." + ft if filename else _safe_filename(name, ft)  # 재인코딩 후 확장자
        self._items.append(_build_lnk2_item(file_uid, fn, data))
        self._finish(lyr, visible, label)
        return lyr

    def save(self, path: str, verify: bool = False, composite=None) -> dict:
        """원자 저장(tmp → os.replace). 병합 프리뷰(image data)는 RLE 로 쓴다.

        composite = 병합 프리뷰로 쓸 HxWx3 uint8(호출자가 레이어와 같은 순서로 합성한 평탄화). None 이면 psd_tools 가
        풀캔버스를 다시 합성한다(3584x4608 문서에서 수십 초·수 GB — 작은 문서/테스트용).
        저장한 파일이 PSD 한계(2^31-1 바이트)를 넘으면 지우고 ValueError.
        반환 {"seconds","bytes","smart_objects","warnings"} (+ verify=True 면 "problems").
        """
        t0 = time.time()
        # 레코드 트리를 API 트리에서 다시 만든다(그룹 경계 채널 분리·마스크 추가 이후의 참조를 확정).
        self.psd._update_record()
        lmi = self.psd._record.layer_and_mask_information
        if lmi.tagged_blocks is None:
            lmi.tagged_blocks = TaggedBlocks()
        if self._items:
            lmi.tagged_blocks[Tag.LINKED_LAYER2] = TaggedBlock(key=Tag.LINKED_LAYER2, data=LinkedLayers(list(self._items)))
        idata = self.psd._record.image_data
        idata.compression = Compression.RLE  # PSDImage.new 의 RAW(W*H*3 그대로) 대신. set_data 는 이 값을 따른다
        if composite is not None:
            arr = np.asarray(composite)
            W, H = self.size
            if arr.dtype != np.uint8 or arr.shape != (H, W, 3):
                raise ValueError(f"composite 는 {(H, W, 3)} uint8 RGB 여야 합니다: {arr.shape} {arr.dtype}")
            idata.set_data([np.ascontiguousarray(arr[..., i]).tobytes() for i in range(3)], self.psd._record.header)
        out_dir = os.path.dirname(os.path.abspath(path))
        os.makedirs(out_dir, exist_ok=True)
        tmp = path + ".tmp"
        try:
            if composite is None:
                self.psd.save(tmp)
            else:
                with open(tmp, "wb") as fh:
                    self.psd._record.write(fh)  # PSDImage.save 와 같은 바이트, composite() 재합성만 뺌
            size = os.path.getsize(tmp)
            if size > _PSD_MAX_BYTES:
                raise ValueError(f"PSD {size / 1e9:.2f}GB - PSD 2GB 한계 초과(M1 은 PSB 미지원). "
                                 f"alternates=none 이나 layer_mode=pixel 을 쓰세요")
        except BaseException:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise
        os.replace(tmp, path)
        res: dict[str, Any] = {"seconds": round(time.time() - t0, 3), "bytes": os.path.getsize(path),
                               "smart_objects": len(self._items), "warnings": list(self.warnings)}
        if verify:
            res["problems"] = verify_saved(path)
        return res


# ═════════════════════════════════════════════════════════════════
# 저장 검증
# ═════════════════════════════════════════════════════════════════
def verify_saved(path: str, *, deep: bool = False) -> list[str]:
    """저장된 PSD 원시 검사 + 재열기. 문제 없으면 [].

    - 원시: 헤더(8-bit), PSD(v1) 파일 크기 ≤ 2^31-1, 레이어/전역 블록 서명·길이(psdwalk 와 동일 규칙),
      lnk2 항목 분할 소비량 == 블록 길이.
    - lnk2: v8 항목 꼬리 == 117B + 'contentID' 포함, contentID·uuid 고유, 임베드 png/jpg 디코드.
    - 재열기: SO 레이어마다 lnk2 항목 연결(Idnt == PlLd uuid == item uuid), transform_box 존재, 고아 항목 없음.
    - deep=True: 모든 레이어 topil()/mask.topil() 디코드.
    """
    problems: list[str] = []
    try:
        w = raw_walk(path, want_raw=(b"lnk2",))
    except (OSError, EOFError, struct.error) as ex:
        return [f"원시 순회 실패: {type(ex).__name__}: {ex}"]
    problems += list(w["problems"])
    hdr = w.get("header", {})
    if hdr.get("depth") != 8:
        problems.append(f"depth {hdr.get('depth')} != 8")
    if hdr.get("ver") == 1 and os.path.getsize(path) > _PSD_MAX_BYTES:
        problems.append(f"PSD 파일 {os.path.getsize(path)} B > 2^31-1 (PSB 필요)")
    gl = {b["key"]: b for b in w.get("global", [])}
    item_uuids: dict[str, int] = {}
    cids: set[str] = set()
    if b"lnk2" in gl:
        raw = gl[b"lnk2"]["raw"]
        items, consumed = lnk2_items(raw)
        if consumed != len(raw):
            problems.append(f"lnk2 소비 {consumed} != 블록 길이 {len(raw)}")
        for it in items:
            try:
                li = LinkedLayer.frombytes(it["body"])
            except Exception as ex:  # 검사기: 어떤 파싱 오류든 문제로 보고
                problems.append(f"lnk2 항목 파싱 실패: {type(ex).__name__}: {ex}")
                continue
            tail = it["body"][len(li.tobytes()):]
            uid = li.uuid
            item_uuids[uid] = item_uuids.get(uid, 0) + 1
            if li.version >= 8:
                if len(tail) != LNK2_TAIL_LEN or b"contentID" not in tail:
                    problems.append(f"lnk2 {uid}: v{li.version} 꼬리 {len(tail)}B (기대 {LNK2_TAIL_LEN}B contentID)")
                else:
                    cid = tail[-74:-2].decode("utf-16-be", "replace")
                    if cid in cids:
                        problems.append(f"lnk2 contentID 중복: {cid}")
                    cids.add(cid)
            elif tail:
                problems.append(f"lnk2 {uid}: v{li.version} 인데 꼬리 {len(tail)}B")
            if li.kind == LinkedLayerType.DATA:
                ftc = li.filetype.strip().lower()
                if ftc in (b"png", b"jpeg", b"jpg"):
                    try:
                        Image.open(io.BytesIO(li.data)).size
                    except OSError as ex:
                        problems.append(f"lnk2 {uid}: 임베드 이미지 디코드 실패 {ex}")
        for uid, n in item_uuids.items():
            if n > 1:
                problems.append(f"lnk2 uuid 중복 {uid} x{n}")
    try:
        q = PSDImage.open(path)
    except Exception as ex:  # 검사기: 재열기 실패 자체가 결과
        problems.append(f"psd_tools 재열기 실패: {type(ex).__name__}: {ex}")
        return problems
    used: dict[str, int] = {}
    for lyr in q.descendants():
        if deep and not lyr.is_group():
            try:
                lyr.topil()
                if lyr.has_mask():
                    lyr.mask.topil()
            except Exception as ex:  # 검사기: 채널 디코드 오류를 문제로 보고
                problems.append(f"레이어 디코드 실패 {lyr.name}: {type(ex).__name__}: {ex}")
        if lyr.kind != "smartobject":
            continue
        so = lyr.smart_object
        uid = so.unique_id
        used[uid] = used.get(uid, 0) + 1
        if so._data is None:
            problems.append(f"SO {lyr.name}: lnk2 항목 없음 (uuid {uid})")
        pl = so._placed_layer
        if pl is None or so.transform_box is None:
            problems.append(f"SO {lyr.name}: PlLd/transform 없음")
        else:
            pu = pl.uuid.decode("ascii", "replace") if isinstance(pl.uuid, bytes) else str(pl.uuid)
            if pu.strip("\x00") != uid:
                problems.append(f"SO {lyr.name}: PlLd uuid {pu} != Idnt {uid}")
    for uid in item_uuids:
        if uid not in used:
            problems.append(f"lnk2 고아 항목(참조하는 레이어 없음): {uid}")
    return problems
