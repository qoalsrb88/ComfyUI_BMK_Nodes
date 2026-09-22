"""BMK Canvas Snap — 임의 해상도 원본을 잘라내지 않고 16px 배수·화소 예산 캔버스에 맞춘 뒤, 편집 결과에서 여백만 되돌리는 노드 묶음.

배경
----
GPT Image 2.5 편집 API 의 출력 크기에는 제약이 있다: 각 변 16의 배수·최대 3,840px,
종횡비 1:3~3:1, 총화소 655,360~8,294,400 (3,686,400 초과는 실험적 해상도).
원본이 홀수 크기이거나 16 배수가 아니면 흔히 원본을 잘라 맞추지만, 이 노드는 잘라내지 않는다.

    1) 원본 전체가 들어가는 캔버스 W×H 를 제약 안에서 고른다.
       우선순위: 공통 배율 s = min(W/w, H/h) 최대 → 같으면 캔버스 면적 최소 → W 작은 것 → H 작은 것.
    2) 원본을 Cw×Ch (= w·s, h·s 를 half-up 반올림) 로 리샘플해 캔버스 중앙에 두고 나머지는 여백.
    3) 편집 결과가 돌아오면 기록한 좌표(crop_xyxy)로 여백만 잘라 Cw×Ch 로 돌려놓는다.

    예) 546×764 → 캔버스 1616×2272, 그림 1616×2261, 여백 위 5 / 아래 6px, 배율 808/273 ≈ 2.9597.

노드 구성
---------
- BMK Canvas Snap Prepare   : 계산 + 리샘플 + 여백. width/height(INT) 를 내장 "OpenAI GPT Image 2.5"
                              노드의 size=Custom 상태에서 model.custom_width / model.custom_height 에 직접 연결.
- BMK Canvas Snap Restore   : canvas_plan 좌표로 여백만 크롭. 결과 크기가 계획과 다르면 중단(자동 보정 없음).
- BMK Canvas Plan From JSON : Prepare 의 plan_json 문자열을 canvas_plan 으로 복원(결과를 저장해 두고
                              다른 세션에서 크롭할 때).

옵션 설명 (Prepare)
-------------------
- profile           : 제약 프로파일. gpt-image-2.5 standard(≤3,686,400px) / experimental(≤8,294,400px) /
                      custom(snap·min_edge·max_edge·min_pixels·max_pixels·max_aspect_ratio 위젯 사용).
                      프로파일이 custom 이 아니면 그 여섯 위젯은 무시된다.
- padding_mode      : white / black 은 3채널 불투명 캔버스(알파는 해당 색 위에 합성).
                      transparent 는 4채널 RGBA 캔버스(여백 알파 0, 여백 RGB 는 흰색).
                      내장 GPT 노드는 4채널 텐서를 RGBA PNG 로 그대로 전송한다(3채널+MASK 로는 알파 미전송).
- max_padding_px    : 0 이면 끔. N>0 이면 여백 총량(가로+세로) ≤ N px 인 후보 중 배율 최대를 고르고,
                      없으면 기본 규칙으로 폴백. 흰 띠가 적을수록 모델이 여백을 다시 그리거나 구도를
                      옮길 위험이 줄어든다. 예) 1200×1800: 기본 1568×2336(여백 11px) → N=2 에서
                      1536×2304(여백 0), 배율 손실 1.4%.
- allow_downscale   : 배율 s<1 이 필요한 큰 원본을 허용할지. 기본 False(계산 크기를 보여주고 중단).
- resample          : 리샘플 방법. lanczos 는 채널별 float 리사이즈(8bit 양자화·알파 전처리 없음).
- use_transparency_mask : False 면 transparency_mask 입력과 이미지 알파를 모두 무시하고 불투명 처리.
- transparency_mask : 선택. 1=투명 / 0=불투명 (Load Image 의 MASK 와 같은 의미).
                      미연결이고 이미지가 4채널이면 그 알파를 사용한다.

출력 (Prepare)
--------------
- canvas_image             : 여백 포함 캔버스. white/black 3채널, transparent 4채널.
- canvas_transparency_mask : 캔버스 투명도(1=투명). ※ GPT 노드 mask 에 그대로 꽂으면 여백만 재생성된다.
- content_region_mask      : 1=그림 영역 / 0=여백. GPT 노드 mask(1=편집 영역) 에 연결하면 여백을 보호.
- width / height / size_string : 생성 요청 크기. 위젯 값 복사가 아니라 INT 링크로 넘길 것.
- canvas_plan / plan_json  : Restore 용 좌표 계약(텐서 없음, JSON 직렬화 가능).
- report                   : 한 줄 요약 + 상세(배율 분수, 오차, 여백, 예산 사용률, 경고).

좌표·마스크 규약
----------------
- 크기 배열은 항상 [width, height]. crop_xyxy 는 왼쪽 위 (0,0) 기준, 오른쪽·아래 끝 미포함.
- 여백 총량이 홀수면 오른쪽/아래에 1px 더 배분한다.
- 반올림은 양수 half-up: floor((2·x·n + d) / (2·d)). 파이썬 round() 의 ties-to-even 을 쓰지 않는다.
- 배율은 정수 분수 n/d 로 비교·저장한다(부동소수점 동률 판정 금지). plan 에는 약분한 값을 넣는다.
- 알파가 있는 리샘플은 RGB·A 를 premultiplied 로 함께 보간한 뒤 필요 시 unpremultiply 한다.

내장 GPT Image 2.5 노드(ComfyUI 0.37.0, comfy_api_nodes/nodes_openai.py) 와 맞물리는 사실
-----------------------------------------------------------------------------------------
- size=Custom 이면 model.custom_width / model.custom_height(INT, 480~3840, step 16) 링크를 받는다.
  백엔드 검증: 16배수, 최대변 3840, 비율 ≤3, 총화소 655,360~8,294,400.
- 결과 IMAGE 는 항상 RGBA 4채널 텐서. Restore 는 3/4채널 모두 받고 알파에서 투명도를 뽑는다.
- 전송 전 입력을 총 4,194,304px(2048²) 이하로 축소한다. experimental 프로파일에서 그 이상 캔버스를
  만들면 축소 전송되고 출력만 요청 크기로 나온다(report 에 경고).
- mask 입력은 1=재생성 영역. 결과 n 장은 첫 장 크기로 강제 스택되므로 Restore 의 엄격한 크기 검사가 유효.

v1 (2026-09)
------------
- 최초 구현. 설계 문서: G:\\AI_output\\Workflow\\[o]Comfyui_Mockup\\260922_GPTImage_CanvasSnap\\
  ComfyUI_GPTImage_CanvasSnap_Handoff_2026-09-22.md (문서 11.1 기준값 9행 전부 일치 확인).
  제약 프로파일 + custom 오버라이드, max_padding_px, white/black/transparent 패딩,
  content_region_mask, RGBA 분리/유지, plan JSON 재적재 노드.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from typing import Any, Mapping

import numpy as np
import torch
from PIL import Image

import comfy.utils

logger = logging.getLogger(__name__)

_TAG_PREPARE = "[ComfyUI_BMK_Nodes::CanvasSnapPrepare]"
_TAG_RESTORE = "[ComfyUI_BMK_Nodes::CanvasSnapRestore]"
_TAG_PLAN = "[ComfyUI_BMK_Nodes::CanvasPlanFromJSON]"

PLAN_TYPE = "BMK_CANVAS_PLAN"
SCHEMA_VERSION = 1
ALGORITHM = "max_uniform_scale_then_min_canvas_v1"
COORD_CONVENTION = "xyxy_end_exclusive"

_PROFILE_GPT25_STD = "gpt-image-2.5 standard"
_PROFILE_GPT25_EXP = "gpt-image-2.5 experimental"
_PROFILE_CUSTOM = "custom"
_PROFILE_NAMES = [_PROFILE_GPT25_STD, _PROFILE_GPT25_EXP, _PROFILE_CUSTOM]

# 내장 OpenAI GPT Image 노드가 전송 전 입력 이미지를 축소하는 총화소 상한 (2048*2048).
_API_NODE_INPUT_PIXEL_CAP = 2048 * 2048

_PADDING_MODES = ["white", "black", "transparent"]
_RESAMPLE_METHODS = ["bicubic", "bilinear", "lanczos", "area", "nearest-exact"]
_RGBA_OUTPUT_MODES = ["split_rgb_mask", "keep_rgba"]
_ALPHA_EPS = 1e-4


# ══════════════════════════════════════════════════════════════════════
# 제약 프로파일
# ══════════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class CanvasConstraints:
    """캔버스 후보가 만족해야 하는 제약. 모두 출력(생성 요청) 캔버스에 대한 조건이다."""

    profile: str
    snap: int
    min_edge: int
    max_edge: int
    min_pixels: int
    max_pixels: int
    max_aspect_ratio: float

    def validate(self) -> None:
        if self.snap < 1:
            raise ValueError(f"snap 은 1 이상이어야 합니다: {self.snap}")
        if self.min_edge < 1:
            raise ValueError(f"min_edge 는 1 이상이어야 합니다: {self.min_edge}")
        if self.max_edge < self.min_edge:
            raise ValueError(
                f"max_edge({self.max_edge}) 가 min_edge({self.min_edge}) 보다 작습니다"
            )
        if self.min_pixels < 0:
            raise ValueError(f"min_pixels 는 0 이상이어야 합니다: {self.min_pixels}")
        if self.max_pixels < max(1, self.min_pixels):
            raise ValueError(
                f"max_pixels({self.max_pixels:,}) 가 min_pixels({self.min_pixels:,}) 보다 작습니다"
            )
        if not (self.max_aspect_ratio >= 1.0):
            raise ValueError(
                f"max_aspect_ratio 는 1.0 이상이어야 합니다: {self.max_aspect_ratio}"
            )

    @property
    def aspect_milli(self) -> int:
        """비율 비교를 정수로 하기 위한 1/1000 단위 정수. 3.0 → 3000 (W ≤ 3H 와 정확히 같음)."""
        return int(round(self.max_aspect_ratio * 1000))

    def as_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "snap": self.snap,
            "min_edge": self.min_edge,
            "max_edge": self.max_edge,
            "min_pixels": self.min_pixels,
            "max_pixels": self.max_pixels,
            "max_aspect_ratio": self.max_aspect_ratio,
        }

    def describe(self) -> str:
        return (
            f"{self.profile} (snap {self.snap}, 변 {self.min_edge}~{self.max_edge}, "
            f"화소 {self.min_pixels:,}~{self.max_pixels:,}, 비율 ≤{self.max_aspect_ratio:g})"
        )


_PROFILES: dict[str, dict[str, Any]] = {
    _PROFILE_GPT25_STD: dict(
        snap=16, min_edge=480, max_edge=3840,
        min_pixels=655_360, max_pixels=3_686_400, max_aspect_ratio=3.0,
    ),
    _PROFILE_GPT25_EXP: dict(
        snap=16, min_edge=480, max_edge=3840,
        min_pixels=655_360, max_pixels=8_294_400, max_aspect_ratio=3.0,
    ),
}


def build_constraints(
    profile: str,
    snap: int,
    min_edge: int,
    max_edge: int,
    min_pixels: int,
    max_pixels: int,
    max_aspect_ratio: float,
) -> CanvasConstraints:
    """프로파일 이름과 custom 위젯 값으로 제약을 만든다. custom 이 아니면 위젯 값은 무시된다."""
    if profile in _PROFILES:
        c = CanvasConstraints(profile=profile, **_PROFILES[profile])
    elif profile == _PROFILE_CUSTOM:
        c = CanvasConstraints(
            profile=_PROFILE_CUSTOM,
            snap=int(snap),
            min_edge=int(min_edge),
            max_edge=int(max_edge),
            min_pixels=int(min_pixels),
            max_pixels=int(max_pixels),
            max_aspect_ratio=float(max_aspect_ratio),
        )
    else:
        raise ValueError(f"알 수 없는 profile: {profile!r}")
    c.validate()
    return c


def gpt_image_custom_size_ok(width: int, height: int) -> bool:
    """내장 OpenAI GPT Image 노드의 size=Custom 백엔드 검증과 같은 조건."""
    if width % 16 or height % 16:
        return False
    if not (480 <= width <= 3840 and 480 <= height <= 3840):
        return False
    if max(width, height) > 3 * min(width, height):
        return False
    return 655_360 <= width * height <= 8_294_400


# ══════════════════════════════════════════════════════════════════════
# 순수 정수 기하 계산
# ══════════════════════════════════════════════════════════════════════
def _ceil_div(a: int, b: int) -> int:
    return -(-a // b)


def _snap_up(v: int, s: int) -> int:
    return _ceil_div(v, s) * s


def _snap_down(v: int, s: int) -> int:
    return (v // s) * s


def round_half_up_ratio(num: int, den: int) -> int:
    """양수 분수 num/den 을 half-up 으로 반올림한 정수. 예) 225/2 = 112.5 → 113.

    파이썬 round() 는 ties-to-even 이라 112.5 → 112 가 되므로 쓰지 않는다.
    """
    if den <= 0:
        raise ValueError("den 은 양수여야 합니다")
    return (2 * num + den) // (2 * den)


@dataclass(frozen=True)
class _Candidate:
    W: int
    H: int
    n: int  # 배율 분자 (약분 전)
    d: int  # 배율 분모 (약분 전)
    Cw: int
    Ch: int

    @property
    def area(self) -> int:
        return self.W * self.H

    @property
    def padding_total(self) -> int:
        return (self.W - self.Cw) + (self.H - self.Ch)


def _make_candidate(src_w: int, src_h: int, W: int, H: int) -> _Candidate:
    # s = min(W/w, H/h) 를 정수 분수로. W·h ≤ H·w 이면 가로가 제한 축.
    if W * src_h <= H * src_w:
        n, d = W, src_w
    else:
        n, d = H, src_h
    return _Candidate(
        W=W, H=H, n=n, d=d,
        Cw=round_half_up_ratio(src_w * n, d),
        Ch=round_half_up_ratio(src_h * n, d),
    )


def _better(a: _Candidate, b: _Candidate) -> bool:
    """a 가 b 보다 우선인가: 배율 큼 → 면적 작음 → W 작음 → H 작음. 배율 비교는 정수 교차곱."""
    lhs = a.n * b.d
    rhs = b.n * a.d
    if lhs != rhs:
        return lhs > rhs
    return (a.area, a.W, a.H) < (b.area, b.W, b.H)


def compute_canvas_plan(
    src_w: int,
    src_h: int,
    constraints: CanvasConstraints,
    max_padding_px: int = 0,
) -> dict[str, Any]:
    """원본 w×h 에 대한 캔버스·그림·여백·크롭 좌표를 계산한다 (텐서 없음, 정수 계산).

    후보 탐색은 W 마다 두 개의 H 만 본다. 고정 W 에서 H 가 커질수록 s 는 H·w ≥ W·h 가 되는
    지점까지 증가하고 그 뒤로는 W/w 로 고정되어 면적·여백만 늘어난다. 따라서 그 경계 바로
    아래의 H(H_lo)와 바로 위의 H(H_hi)만이 '배율 최대 → 면적 최소' 와 '여백 상한' 두 목표
    모두에서 다른 H 를 지배한다. 브루트포스(모든 W×H)와 결과가 같음을 테스트로 확인했다.
    """
    src_w = int(src_w)
    src_h = int(src_h)
    if src_w < 1 or src_h < 1:
        raise ValueError(f"원본 크기가 잘못되었습니다: {src_w}x{src_h}")
    constraints.validate()
    c = constraints
    S = c.snap
    arm = c.aspect_milli
    cap = int(max_padding_px)
    if cap < 0:
        raise ValueError(f"max_padding_px 는 0 이상이어야 합니다: {cap}")

    best: _Candidate | None = None
    best_capped: _Candidate | None = None

    w_lo = _snap_up(max(S, c.min_edge), S)
    w_hi = _snap_down(c.max_edge, S)
    for W in range(w_lo, w_hi + 1, S):
        # 이 W 에서 허용되는 H 구간 (화소 범위 · 비율 · 변 길이의 교집합)
        h_lo = max(c.min_edge, S, _ceil_div(c.min_pixels, W), _ceil_div(W * 1000, arm))
        h_hi = min(c.max_edge, c.max_pixels // W, (W * arm) // 1000)
        h_lo = _snap_up(h_lo, S)
        h_hi = _snap_down(h_hi, S)
        if h_lo > h_hi:
            continue

        heights: list[int] = []
        # H·w ≥ W·h 가 되는 가장 작은 유효 H  (s = W/w 영역의 최소 면적)
        h_hi_cand = max(h_lo, _snap_up(_ceil_div(W * src_h, src_w), S))
        if h_hi_cand <= h_hi:
            heights.append(h_hi_cand)
        # H·w < W·h 인 가장 큰 유효 H  (s = H/h 영역의 최대 배율)
        h_lo_cand = min(h_hi, _snap_down((W * src_h - 1) // src_w, S))
        if h_lo_cand >= h_lo and h_lo_cand not in heights:
            heights.append(h_lo_cand)

        for H in heights:
            cand = _make_candidate(src_w, src_h, W, H)
            if best is None or _better(cand, best):
                best = cand
            if cap > 0 and cand.padding_total <= cap:
                if best_capped is None or _better(cand, best_capped):
                    best_capped = cand

    if best is None:
        raise ValueError(f"제약을 만족하는 캔버스가 없습니다: {c.describe()}")

    cap_applied = cap > 0 and best_capped is not None
    chosen = best_capped if cap_applied else best

    W, H, Cw, Ch = chosen.W, chosen.H, chosen.Cw, chosen.Ch
    if Cw < 1 or Ch < 1:
        raise ValueError(
            f"극단적인 종횡비: 원본 {src_w}x{src_h} 을 캔버스 {W}x{H} 에 담으면 그림의 한 변이 "
            f"0px 로 반올림됩니다({Cw}x{Ch}). 묵시적으로 1px 로 보정하지 않습니다."
        )
    g = math.gcd(chosen.n, chosen.d)
    n, d = chosen.n // g, chosen.d // g
    scale = n / d

    L = (W - Cw) // 2
    T = (H - Ch) // 2
    R = W - Cw - L
    B = H - Ch - T

    return {
        "schema_version": SCHEMA_VERSION,
        "algorithm": ALGORITHM,
        "source_size": [src_w, src_h],
        "canvas_size": [W, H],
        "content_size": [Cw, Ch],
        "padding_ltrb": [L, T, R, B],
        "crop_xyxy": [L, T, L + Cw, T + Ch],
        "coordinate_convention": COORD_CONVENTION,
        "scale_numerator": n,
        "scale_denominator": d,
        "rounding": "half_up_positive",
        "anchor": "center_extra_right_bottom",
        "constraints": c.as_dict(),
        "downscaled": n < d,
        "canvas_pixels": W * H,
        "content_pixels": Cw * Ch,
        "signed_aspect_error_percent": 100.0 * ((Cw * src_h) / (Ch * src_w) - 1.0),
        "width_rounding_error_px": Cw - src_w * scale,
        "height_rounding_error_px": Ch - src_h * scale,
        "max_padding_px": cap,
        "padding_cap_applied": cap_applied,
        "padding_cap_satisfiable": (best_capped is not None) if cap > 0 else None,
        "primary_rule_canvas": [best.W, best.H],
        "primary_rule_content": [best.Cw, best.Ch],
        "primary_rule_padding_px": best.padding_total,
        "geometry_only": True,
    }


# ══════════════════════════════════════════════════════════════════════
# plan 컨테이너 · 검증
# ══════════════════════════════════════════════════════════════════════
class BMKCanvasPlan(dict):
    """canvas_plan 컨테이너 (dict 완전 호환, JSON 직렬화 가능). 문자열화 시 한 줄 요약만 표시."""

    def __repr__(self) -> str:
        try:
            sw, sh = self["source_size"]
            W, H = self["canvas_size"]
            Cw, Ch = self["content_size"]
            return (
                f"{PLAN_TYPE}({sw}x{sh} → canvas {W}x{H}, content {Cw}x{Ch}, "
                f"crop {list(self['crop_xyxy'])}, padding {self.get('padding_mode', '?')})"
            )
        except Exception:
            return f"{PLAN_TYPE}(invalid: {dict.__repr__(self)[:160]})"

    __str__ = __repr__


def _as_int_list(value: Any, count: int, name: str, tag: str) -> list[int]:
    if not isinstance(value, (list, tuple)) or len(value) != count:
        raise ValueError(f"{tag} plan.{name} 은 길이 {count} 의 정수 배열이어야 합니다: {value!r}")
    out: list[int] = []
    for v in value:
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ValueError(f"{tag} plan.{name} 에 정수가 아닌 값이 있습니다: {value!r}")
        if isinstance(v, float) and not v.is_integer():
            raise ValueError(f"{tag} plan.{name} 에 정수가 아닌 값이 있습니다: {value!r}")
        out.append(int(v))
    return out


def validate_plan(plan: Any, tag: str) -> dict[str, int]:
    """plan 의 스키마·좌표 규약·일관성을 검증하고 정수 기하값을 돌려준다."""
    if not isinstance(plan, Mapping):
        raise ValueError(
            f"{tag} canvas_plan 이 dict 형식이 아닙니다: {type(plan).__name__}. "
            "BMK Canvas Snap Prepare 의 canvas_plan 출력이나 BMK Canvas Plan From JSON 을 연결하세요."
        )
    if plan.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(
            f"{tag} 지원하지 않는 plan schema_version: {plan.get('schema_version')!r} "
            f"(지원: {SCHEMA_VERSION})"
        )
    if plan.get("coordinate_convention") != COORD_CONVENTION:
        raise ValueError(
            f"{tag} 지원하지 않는 좌표 규약: {plan.get('coordinate_convention')!r} "
            f"(지원: {COORD_CONVENTION})"
        )
    W, H = _as_int_list(plan.get("canvas_size"), 2, "canvas_size", tag)
    Cw, Ch = _as_int_list(plan.get("content_size"), 2, "content_size", tag)
    L, T, R, B = _as_int_list(plan.get("padding_ltrb"), 4, "padding_ltrb", tag)
    x0, y0, x1, y1 = _as_int_list(plan.get("crop_xyxy"), 4, "crop_xyxy", tag)

    if min(W, H, Cw, Ch) < 1:
        raise ValueError(f"{tag} plan 크기는 양의 정수여야 합니다: canvas {W}x{H}, content {Cw}x{Ch}")
    if min(L, T, R, B) < 0:
        raise ValueError(f"{tag} plan 여백은 0 이상이어야 합니다: {[L, T, R, B]}")
    if L + Cw + R != W or T + Ch + B != H:
        raise ValueError(
            f"{tag} plan 여백과 크기가 맞지 않습니다: L+Cw+R={L + Cw + R} vs W={W}, "
            f"T+Ch+B={T + Ch + B} vs H={H}"
        )
    if (x0, y0, x1, y1) != (L, T, L + Cw, T + Ch):
        raise ValueError(
            f"{tag} plan crop_xyxy {[x0, y0, x1, y1]} 가 여백/그림 크기에서 계산한 "
            f"{[L, T, L + Cw, T + Ch]} 와 다릅니다"
        )
    if x1 > W or y1 > H:
        raise ValueError(f"{tag} plan crop_xyxy {[x0, y0, x1, y1]} 가 캔버스 {W}x{H} 를 벗어납니다")
    return {
        "W": W, "H": H, "Cw": Cw, "Ch": Ch,
        "L": L, "T": T, "R": R, "B": B,
        "x0": x0, "y0": y0, "x1": x1, "y1": y1,
    }


# ══════════════════════════════════════════════════════════════════════
# 텐서 헬퍼
# ══════════════════════════════════════════════════════════════════════
def _check_image(image: Any, tag: str) -> tuple[int, int, int, int]:
    """IMAGE [B,H,W,C] (C=3|4) 검증. (B, H, W, C) 반환."""
    if not torch.is_tensor(image) or image.ndim != 4:
        shape = tuple(image.shape) if torch.is_tensor(image) else type(image).__name__
        raise ValueError(f"{tag} IMAGE 는 [B,H,W,C] 텐서여야 합니다: {shape}")
    B, H, W, C = (int(v) for v in image.shape)
    if C not in (3, 4):
        raise ValueError(f"{tag} IMAGE 채널 수는 3(RGB) 또는 4(RGBA) 여야 합니다: C={C}")
    if B < 1 or H < 1 or W < 1:
        raise ValueError(f"{tag} IMAGE 크기가 비어 있습니다: {tuple(image.shape)}")
    if not bool(torch.isfinite(image).all()):
        raise ValueError(f"{tag} IMAGE 에 NaN/Inf 가 있습니다")
    return B, H, W, C


def _normalize_mask(
    mask: Any, batch: int, height: int, width: int, tag: str, what: str
) -> torch.Tensor | None:
    """MASK → [B,H,W] float32 [0,1]. None 이면 '64x64 전부 0 기본 마스크' 로 판단해 무시한 것."""
    if not torch.is_tensor(mask):
        raise ValueError(f"{tag} {what} 은 MASK 텐서여야 합니다: {type(mask).__name__}")
    if mask.ndim == 2:
        mask = mask.unsqueeze(0)
    elif mask.ndim == 4 and int(mask.shape[-1]) == 1:
        mask = mask[..., 0]
    if mask.ndim != 3:
        raise ValueError(f"{tag} {what} 차원을 지원하지 않습니다: {tuple(mask.shape)} (기대: [B,H,W])")
    if not bool(torch.isfinite(mask).all()):
        raise ValueError(f"{tag} {what} 에 NaN/Inf 가 있습니다")
    mb, mh, mw = (int(v) for v in mask.shape)
    if (mh, mw) != (height, width):
        if (mh, mw) == (64, 64) and not bool(mask.any()):
            return None
        raise ValueError(
            f"{tag} {what} 크기 {mw}x{mh} 가 이미지 {width}x{height} 와 다릅니다. "
            "입력 실수를 숨기지 않기 위해 임의로 리사이즈하지 않습니다."
        )
    if mb == 1 and batch > 1:
        mask = mask.expand(batch, -1, -1)
    elif mb != batch:
        raise ValueError(
            f"{tag} {what} 배치 {mb} 가 이미지 배치 {batch} 와 다릅니다 (1 이면 broadcast)."
        )
    return mask.to(torch.float32).clamp(0.0, 1.0)


def _resample_bchw(x: torch.Tensor, width: int, height: int, method: str) -> torch.Tensor:
    """[B,C,h,w] float32 → [B,C,height,width]. lanczos 는 채널별 PIL 'F' 리사이즈(양자화 없음)."""
    if int(x.shape[-1]) == width and int(x.shape[-2]) == height:
        return x.clone()
    if method == "lanczos":
        out = torch.empty((int(x.shape[0]), int(x.shape[1]), height, width), dtype=torch.float32)
        for b in range(int(x.shape[0])):
            for ch in range(int(x.shape[1])):
                arr = np.ascontiguousarray(x[b, ch].detach().to("cpu", torch.float32).numpy())
                im = Image.fromarray(arr)  # float32 2D → mode 'F'
                im = im.resize((width, height), resample=Image.Resampling.LANCZOS)
                out[b, ch] = torch.from_numpy(np.array(im, dtype=np.float32))
        return out.to(x.device)
    if method not in _RESAMPLE_METHODS:
        raise ValueError(f"지원하지 않는 resample: {method!r}")
    return comfy.utils.common_upscale(x, width, height, method, "disabled")


def _snap_alpha(a: torch.Tensor) -> torch.Tensor:
    """보간 overshoot 를 [0,1] 로 자르고, 1·0 에 _ALPHA_EPS 이내로 붙은 값은 정확히 1·0 으로 맞춘다.

    bicubic 가중치 합의 float32 오차로 완전 불투명 영역이 0.9999999 가 되면 투명도 마스크가
    0 이 아니게 되어 후속 노드의 '마스크 비어 있음' 판정이 어긋난다. 8bit 정밀도(1/255) 보다
    훨씬 작은 범위만 손댄다.
    """
    a = a.clamp(0.0, 1.0)
    a = torch.where(a > 1.0 - _ALPHA_EPS, torch.ones_like(a), a)
    a = torch.where(a < _ALPHA_EPS, torch.zeros_like(a), a)
    return a


_ALPHA_DESC = {
    "none": "없음(불투명 원본)",
    "disabled": "사용 안 함(불투명 처리)",
    "mask_input": "transparency_mask 입력",
    "image_alpha": "이미지 알파 채널",
    "default_mask_ignored": "64x64 빈 기본 마스크 무시(불투명 처리)",
}


def _resolve_source_alpha(
    image: torch.Tensor,
    mask: Any,
    use_mask: bool,
    batch: int,
    height: int,
    width: int,
    channels: int,
    tag: str,
) -> tuple[torch.Tensor | None, str, list[str]]:
    """원본 알파 [B,H,W] (1=불투명) 와 출처, 참고 메모를 결정한다."""
    notes: list[str] = []
    if not use_mask:
        if mask is not None or channels == 4:
            notes.append("use_transparency_mask=False: 연결된 마스크/이미지 알파를 무시하고 불투명으로 처리")
        return None, "disabled", notes
    if mask is not None:
        m = _normalize_mask(mask, batch, height, width, tag, "transparency_mask")
        if m is None:
            notes.append("transparency_mask 가 64x64 전부 0 인 Load Image 기본 출력이라 불투명으로 처리")
            if channels == 4:
                notes.append("이미지 알파 채널은 transparency_mask 가 연결되어 있어 사용하지 않음")
            return None, "default_mask_ignored", notes
        if channels == 4:
            notes.append("이미지 알파 채널 대신 transparency_mask 입력을 사용")
        return (1.0 - m).to(image.device), "mask_input", notes
    if channels == 4:
        return image[..., 3].to(torch.float32).clamp(0.0, 1.0), "image_alpha", notes
    return None, "none", notes


def _fmt_pct(v: float) -> str:
    return f"{v:.2f}%"


# ══════════════════════════════════════════════════════════════════════
# BMK Canvas Snap Prepare
# ══════════════════════════════════════════════════════════════════════
class BMKCanvasSnapPrepare:
    """캔버스 계산 + 전체 리샘플 + 여백. width/height 는 GPT 노드의 custom 크기에 링크로 넘긴다."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE", {
                    "tooltip": "원본 이미지 전체. 3채널(RGB) 또는 4채널(RGBA). 잘라내지 않습니다."}),
                "profile": (_PROFILE_NAMES, {
                    "default": _PROFILE_GPT25_STD,
                    "tooltip": "제약 프로파일. standard = 총화소 ≤3,686,400 (비실험 구간), "
                               "experimental = ≤8,294,400. 두 프리셋은 16배수·변 480~3840·비율 ≤3:1. "
                               "custom 이면 아래 snap/min_edge/max_edge/min_pixels/max_pixels/"
                               "max_aspect_ratio 위젯을 사용합니다(프리셋에서는 무시)."}),
                "padding_mode": (_PADDING_MODES, {
                    "default": "white",
                    "tooltip": "white/black: 3채널 불투명 캔버스(원본 알파는 그 색 위에 합성). "
                               "transparent: 4채널 RGBA 캔버스(여백 알파 0). 내장 GPT 노드는 "
                               "4채널 텐서를 RGBA PNG 로 그대로 전송합니다."}),
                "max_padding_px": ("INT", {
                    "default": 0, "min": 0, "max": 4096,
                    "tooltip": "0 = 끔(배율 최대 규칙만). N>0 이면 여백 총량(가로+세로) ≤ N px 인 "
                               "후보 중 배율 최대를 고르고, 없으면 기본 규칙으로 폴백합니다. "
                               "흰 띠가 적을수록 모델이 여백을 다시 그리거나 구도를 옮길 위험이 줄어듭니다."}),
                "allow_downscale": ("BOOLEAN", {
                    "default": False,
                    "label_on": "축소 허용",
                    "label_off": "축소 금지(중단)",
                    "tooltip": "제약 안에서 확대할 수 없는 큰 원본을 축소해서라도 진행할지. "
                               "False 면 계산된 크기를 표시하고 중단합니다. 배율 1.0 은 축소가 아닙니다."}),
                "resample": (_RESAMPLE_METHODS, {
                    "default": "bicubic",
                    "tooltip": "원본 → 그림 크기 리샘플 방법. lanczos 는 채널별 float 리사이즈."}),
                "use_transparency_mask": ("BOOLEAN", {
                    "default": True,
                    "label_on": "알파 사용",
                    "label_off": "불투명 처리",
                    "tooltip": "False 면 transparency_mask 입력과 이미지 알파를 모두 무시하고 "
                               "불투명으로 처리합니다(포트를 끊지 않고 A/B 가능)."}),
                "snap": ("INT", {
                    "default": 16, "min": 1, "max": 512,
                    "tooltip": "[custom 전용] 캔버스 변의 배수 단위."}),
                "min_edge": ("INT", {
                    "default": 480, "min": 1, "max": 16384,
                    "tooltip": "[custom 전용] 캔버스 변 최소 길이."}),
                "max_edge": ("INT", {
                    "default": 3840, "min": 1, "max": 16384,
                    "tooltip": "[custom 전용] 캔버스 변 최대 길이."}),
                "min_pixels": ("INT", {
                    "default": 655_360, "min": 0, "max": 268_435_456,
                    "tooltip": "[custom 전용] 캔버스 총화소 하한."}),
                "max_pixels": ("INT", {
                    "default": 3_686_400, "min": 1, "max": 268_435_456,
                    "tooltip": "[custom 전용] 캔버스 총화소 상한(예산). 여백 포함 캔버스가 이 값을 넘지 않습니다."}),
                "max_aspect_ratio": ("FLOAT", {
                    "default": 3.0, "min": 1.0, "max": 64.0, "step": 0.01,
                    "tooltip": "[custom 전용] 캔버스 장단비 상한. 3.0 = 1:3 ~ 3:1. "
                               "원본 비율은 제한하지 않으며 벗어나면 레터박스로 담습니다."}),
            },
            "optional": {
                "transparency_mask": ("MASK", {
                    "tooltip": "원본 투명도. 1=투명 / 0=불투명 (Load Image 의 MASK 와 같은 의미). "
                               "미연결이고 이미지가 4채널이면 그 알파를 사용합니다. "
                               "편집 허용 영역 마스크가 아닙니다."}),
            },
        }

    RETURN_TYPES = ("IMAGE", "MASK", "MASK", "INT", "INT", "STRING", PLAN_TYPE, "STRING", "STRING")
    RETURN_NAMES = (
        "canvas_image", "canvas_transparency_mask", "content_region_mask",
        "width", "height", "size_string", "canvas_plan", "plan_json", "report",
    )
    OUTPUT_TOOLTIPS = (
        "여백 포함 캔버스. white/black 3채널, transparent 4채널(RGBA). GPT 노드 image 입력에 연결.",
        "캔버스 투명도(1=투명, 0=불투명). GPT 노드 mask 에 그대로 꽂으면 여백만 재생성되니 주의.",
        "1=그림 영역 / 0=여백. GPT 노드 mask(1=편집 영역) 에 연결하면 여백을 보호합니다(이미지 1장일 때만).",
        "캔버스 너비. GPT 노드 size=Custom 의 custom_width 에 링크.",
        "캔버스 높이. GPT 노드 size=Custom 의 custom_height 에 링크.",
        "예: 1616x2272",
        "Restore 에 연결할 좌표 계약(텐서 없음).",
        "canvas_plan 의 JSON. 저장해 두면 BMK Canvas Plan From JSON 으로 복원 가능.",
        "한 줄 요약 + 상세.",
    )
    FUNCTION = "prepare"
    CATEGORY = "BMK/Image"
    DESCRIPTION = (
        "임의 해상도 원본을 잘라내지 않고, 배수·화소 예산·비율 제약 안에서 공통 배율이 최대가 되는 "
        "캔버스를 골라 전체 리샘플 + 여백 추가합니다. width/height 를 GPT Image 2.5 노드의 Custom 크기에 "
        "링크로 넘기고, canvas_plan 을 BMK Canvas Snap Restore 에 연결해 여백만 되돌립니다."
    )
    SEARCH_ALIASES = [
        "canvas snap", "letterbox", "pad to multiple", "gpt image size", "pixel budget",
        "aspect canvas", "snap 16", "캔버스 스냅", "여백 추가", "패딩", "16배수", "해상도 맞춤",
        "화소 예산", "GPT 이미지 크기",
    ]

    def prepare(
        self,
        image,
        profile,
        padding_mode,
        max_padding_px,
        allow_downscale,
        resample,
        use_transparency_mask,
        snap,
        min_edge,
        max_edge,
        min_pixels,
        max_pixels,
        max_aspect_ratio,
        transparency_mask=None,
    ):
        tag = _TAG_PREPARE
        B, src_h, src_w, C = _check_image(image, tag)
        if padding_mode not in _PADDING_MODES:
            raise ValueError(f"{tag} 지원하지 않는 padding_mode: {padding_mode!r}")
        if resample not in _RESAMPLE_METHODS:
            raise ValueError(f"{tag} 지원하지 않는 resample: {resample!r}")

        try:
            constraints = build_constraints(
                profile, snap, min_edge, max_edge, min_pixels, max_pixels, max_aspect_ratio
            )
        except ValueError as exc:
            raise ValueError(f"{tag} 제약 설정 오류: {exc}") from exc

        notes: list[str] = []
        if constraints.profile != _PROFILE_CUSTOM:
            notes.append("프리셋 프로파일: snap/min_edge/max_edge/min_pixels/max_pixels/max_aspect_ratio 위젯 값은 무시")

        try:
            geo = compute_canvas_plan(src_w, src_h, constraints, int(max_padding_px))
        except ValueError as exc:
            raise ValueError(f"{tag} {exc}") from exc

        W, H = geo["canvas_size"]
        Cw, Ch = geo["content_size"]
        L, T, R, Bm = geo["padding_ltrb"]
        n, d = geo["scale_numerator"], geo["scale_denominator"]

        if geo["downscaled"] and not bool(allow_downscale):
            raise ValueError(
                f"{tag} 원본 {src_w}x{src_h} 은(는) 제약 {constraints.describe()} 안에서 확대할 수 없어 "
                f"축소가 필요합니다: 배율 {n}/{d} ≈ {n / d:.4f}, 캔버스 {W}x{H}, 그림 {Cw}x{Ch}. "
                "축소를 원하면 allow_downscale 을 켜세요."
            )
        if geo["downscaled"]:
            notes.append(f"축소 적용(allow_downscale): 배율 {n}/{d} ≈ {n / d:.4f}")

        # ── 여백 상한 결과 메모 ──
        cap = int(max_padding_px)
        if cap > 0:
            pW, pH = geo["primary_rule_canvas"]
            if not geo["padding_cap_satisfiable"]:
                notes.append(f"여백 상한 {cap}px 를 만족하는 후보가 없어 기본 규칙 {pW}x{pH} 로 선택")
            elif (pW, pH) != (W, H):
                pCw, pCh = geo["primary_rule_content"]
                loss = 100.0 * (1.0 - (Cw * Ch) / (pCw * pCh)) if pCw * pCh else 0.0
                notes.append(
                    f"여백 상한 {cap}px 적용: 기본 규칙 {pW}x{pH}(여백 {geo['primary_rule_padding_px']}px) "
                    f"대신 선택, 그림 화소 손실 {loss:.2f}%"
                )

        # ── 원본 비율이 캔버스 비율 한계 밖이면 큰 레터박스 ──
        arm = constraints.aspect_milli
        if src_w * 1000 > arm * src_h or src_h * 1000 > arm * src_w:
            notes.append(
                f"원본 비율 {src_w}:{src_h} 이 캔버스 비율 한계(≤{constraints.max_aspect_ratio:g}:1) 밖이라 "
                f"큰 레터박스가 생깁니다(여백 {L}/{T}/{R}/{Bm})"
            )

        # ── 내장 GPT 노드 호환 경고 ──
        if W * H > _API_NODE_INPUT_PIXEL_CAP:
            notes.append(
                f"캔버스 {W * H:,}px 가 내장 OpenAI GPT Image 노드의 전송 상한 "
                f"{_API_NODE_INPUT_PIXEL_CAP:,}px 를 넘어 입력이 축소 전송됩니다(출력만 요청 크기)"
            )
        if constraints.profile == _PROFILE_CUSTOM and not gpt_image_custom_size_ok(W, H):
            notes.append(
                f"{W}x{H} 는 내장 GPT Image 노드의 Custom 제약(16배수, 480~3840, 비율≤3, "
                "655,360~8,294,400px) 밖입니다. 다른 모델용이 아니면 확인하세요"
            )

        # ── 알파 결정 ──
        alpha, alpha_source, alpha_notes = _resolve_source_alpha(
            image, transparency_mask, bool(use_transparency_mask), B, src_h, src_w, C, tag
        )
        notes.extend(alpha_notes)

        # ── 리샘플 (전체 원본 → Cw×Ch) ──
        dev = image.device
        rgb = image[..., :3].movedim(-1, 1).to(torch.float32)  # [B,3,h,w]
        if alpha is None:
            content_rgb = _resample_bchw(rgb, Cw, Ch, resample).clamp(0.0, 1.0)
            content_a = None
        else:
            a = alpha.unsqueeze(1).to(torch.float32)  # [B,1,h,w]
            stacked = torch.cat([rgb * a, a], dim=1)  # premultiplied RGB + A
            r = _resample_bchw(stacked, Cw, Ch, resample)
            content_a = _snap_alpha(r[:, 3:4])
            content_pm = torch.minimum(r[:, :3].clamp(0.0, 1.0), content_a)

        # ── 합성 / 캔버스 ──
        if padding_mode == "white":
            channels = 3
            fill = 1.0
            content = content_rgb if content_a is None else (content_pm + (1.0 - content_a)).clamp(0.0, 1.0)
        elif padding_mode == "black":
            channels = 3
            fill = 0.0
            content = content_rgb if content_a is None else content_pm
        else:  # transparent
            channels = 4
            fill = 1.0  # 여백 RGB 는 흰색, 알파는 0
            if content_a is None:
                content = content_rgb
                content_alpha_out = torch.ones((B, 1, Ch, Cw), dtype=torch.float32, device=dev)
            else:
                straight = content_pm / content_a.clamp_min(_ALPHA_EPS)
                content = torch.where(content_a > _ALPHA_EPS, straight, torch.ones_like(straight)).clamp(0.0, 1.0)
                content_alpha_out = content_a

        canvas = torch.full((B, H, W, channels), fill, dtype=torch.float32, device=dev)
        if channels == 4:
            canvas[..., 3] = 0.0
        canvas[:, T:T + Ch, L:L + Cw, :3] = content.movedim(1, -1).to(dev)
        if channels == 4:
            canvas[:, T:T + Ch, L:L + Cw, 3] = content_alpha_out[:, 0].to(dev)

        if channels == 4:
            transparency_out = (1.0 - canvas[..., 3]).contiguous()
        else:
            transparency_out = torch.zeros((B, H, W), dtype=torch.float32, device=dev)
        content_region = torch.zeros((B, H, W), dtype=torch.float32, device=dev)
        content_region[:, T:T + Ch, L:L + Cw] = 1.0

        # ── plan / report ──
        plan = BMKCanvasPlan(geo)
        plan.update({
            "source_batch_size": B,
            "source_channels": C,
            "padding_mode": padding_mode,
            "resample": resample,
            "canvas_channels": channels,
            "transparency_semantics": "one_is_transparent",
            "alpha_source": alpha_source,
            "source_alpha_used": alpha is not None,
            "notes": list(notes),
        })
        plan_json = json.dumps(plan, ensure_ascii=False, indent=2)

        transparent_pct = None
        if channels == 4:
            transparent_pct = float((canvas[..., 3] < 0.999).float().mean().item() * 100.0)
        report = _build_prepare_report(plan, constraints, notes, transparent_pct)
        logger.info("%s %s", tag, report.splitlines()[0])

        return (
            canvas,
            transparency_out,
            content_region,
            int(W),
            int(H),
            f"{W}x{H}",
            plan,
            plan_json,
            report,
        )


def _build_prepare_report(
    plan: Mapping[str, Any],
    constraints: CanvasConstraints,
    notes: list[str],
    transparent_pct: float | None,
) -> str:
    sw, sh = plan["source_size"]
    W, H = plan["canvas_size"]
    Cw, Ch = plan["content_size"]
    L, T, R, B = plan["padding_ltrb"]
    n, d = plan["scale_numerator"], plan["scale_denominator"]
    scale = n / d
    usage = 100.0 * plan["canvas_pixels"] / constraints.max_pixels
    pad_ratio = 100.0 * (1.0 - plan["content_pixels"] / plan["canvas_pixels"])
    cap = int(plan.get("max_padding_px", 0))
    if cap == 0:
        cap_desc = "사용 안 함"
    elif plan.get("padding_cap_applied"):
        cap_desc = f"{cap}px 적용"
    else:
        cap_desc = f"{cap}px 미충족 → 기본 규칙"
    alpha_desc = _ALPHA_DESC.get(str(plan.get("alpha_source")), str(plan.get("alpha_source")))
    if transparent_pct is not None:
        alpha_desc += f", 캔버스 투명 픽셀 {_fmt_pct(transparent_pct)}"

    lines = [
        f"{sw}x{sh} → 캔버스 {W}x{H} | 그림 {Cw}x{Ch} | 여백 L{L} T{T} R{R} B{B} | "
        f"배율 x{scale:.4f} | 예산 {usage:.2f}%",
        f"프로파일: {constraints.describe()}",
        f"배율 분수 {n}/{d} | 종횡비 오차 {plan['signed_aspect_error_percent']:+.4f}% | "
        f"반올림 오차 w {plan['width_rounding_error_px']:+.2f}px / h {plan['height_rounding_error_px']:+.2f}px",
        f"캔버스 화소 {plan['canvas_pixels']:,} | 그림 화소 {plan['content_pixels']:,} | 여백 비율 {_fmt_pct(pad_ratio)}",
        f"패딩 {plan['padding_mode']} ({plan['canvas_channels']}채널) | 리샘플 {plan['resample']} | "
        f"알파: {alpha_desc} | 배치 {plan['source_batch_size']} | "
        f"{'축소 적용' if plan['downscaled'] else '확대 또는 동일 배율'}",
        f"여백 상한: {cap_desc}",
        f"크롭 xyxy {list(plan['crop_xyxy'])} (끝 미포함) | GPT 노드: size=Custom, "
        f"custom_width←{W}, custom_height←{H}",
    ]
    lines.extend(f"주의: {note}" for note in notes)
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════
# BMK Canvas Snap Restore
# ══════════════════════════════════════════════════════════════════════
class BMKCanvasSnapRestore:
    """canvas_plan 의 좌표로 여백만 크롭. 크기가 계획과 다르면 자동 보정 없이 중단."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE", {
                    "tooltip": "편집 결과(또는 Prepare 의 canvas_image 직접 연결). 3채널/4채널 모두 가능. "
                               "크기가 canvas_plan 의 캔버스와 정확히 같아야 합니다."}),
                "canvas_plan": (PLAN_TYPE, {
                    "tooltip": "이 이미지를 준비한 BMK Canvas Snap Prepare 의 canvas_plan "
                               "(또는 BMK Canvas Plan From JSON)."}),
                "rgba_output": (_RGBA_OUTPUT_MODES, {
                    "default": "split_rgb_mask",
                    "tooltip": "결과가 4채널(RGBA)일 때: split_rgb_mask = RGB 3채널 + 투명도 MASK 로 분리, "
                               "keep_rgba = 4채널 그대로 내보내고 MASK 도 함께 출력."}),
            },
            "optional": {
                "transparency_mask": ("MASK", {
                    "tooltip": "결과 이미지의 투명도(1=투명). 연결하면 이미지 알파 채널보다 우선합니다. "
                               "원본 마스크를 결과 알파처럼 넣지 마세요."}),
            },
        }

    RETURN_TYPES = ("IMAGE", "MASK", "STRING")
    RETURN_NAMES = ("image", "transparency_mask", "report")
    OUTPUT_TOOLTIPS = (
        "여백을 제거한 이미지(그림 크기 Cw×Ch). 16배수가 아닐 수 있으며 정상입니다.",
        "같은 좌표로 자른 투명도(1=투명). 결과에 알파가 없으면 전부 0.",
        "크기 검사 결과, 크롭 크기, 알파 상태.",
    )
    FUNCTION = "restore"
    CATEGORY = "BMK/Image"
    DESCRIPTION = (
        "BMK Canvas Snap Prepare 의 canvas_plan 좌표로 편집 결과에서 추가한 여백만 잘라냅니다. "
        "결과 크기가 계획과 다르면 자동 보정 없이 중단하고, 4채널 결과는 알파를 투명도 MASK 로 분리합니다."
    )
    SEARCH_ALIASES = [
        "canvas restore", "remove padding", "crop padding", "unletterbox", "여백 제거",
        "여백 복원", "패딩 제거", "캔버스 복원", "크롭 백",
    ]

    def restore(self, image, canvas_plan, rgba_output, transparency_mask=None):
        tag = _TAG_RESTORE
        g = validate_plan(canvas_plan, tag)
        B, H, W, C = _check_image(image, tag)
        if rgba_output not in _RGBA_OUTPUT_MODES:
            raise ValueError(f"{tag} 지원하지 않는 rgba_output: {rgba_output!r}")

        if (W, H) != (g["W"], g["H"]):
            sw, sh = canvas_plan.get("source_size", ["?", "?"])
            raise ValueError(
                f"{tag} 결과 크기 {W}x{H} 가 계획 캔버스 {g['W']}x{g['H']} 와 다릅니다. "
                f"배율/패딩/크롭 중 무엇이 바뀌었는지 알 수 없으므로 자동 보정하지 않고 중단합니다. "
                f"(계획: 원본 {sw}x{sh} → 그림 {g['Cw']}x{g['Ch']})"
            )

        notes: list[str] = []
        alpha: torch.Tensor | None = None
        if transparency_mask is not None:
            m = _normalize_mask(transparency_mask, B, H, W, tag, "transparency_mask")
            if m is None:
                notes.append("transparency_mask 가 64x64 전부 0 인 기본 마스크라 무시")
                if C == 4:
                    alpha = image[..., 3].to(torch.float32).clamp(0.0, 1.0)
                    alpha_source = "image_alpha"
                else:
                    alpha_source = "none"
            else:
                alpha = (1.0 - m).to(image.device)
                alpha_source = "mask_input"
                if C == 4:
                    notes.append("결과 이미지의 알파 채널 대신 transparency_mask 입력을 사용")
        elif C == 4:
            alpha = image[..., 3].to(torch.float32).clamp(0.0, 1.0)
            alpha_source = "image_alpha"
        else:
            alpha_source = "none"

        x0, y0, x1, y1 = g["x0"], g["y0"], g["x1"], g["y1"]
        cropped = image[:, y0:y1, x0:x1, :]
        if C == 4 and rgba_output == "split_rgb_mask":
            out = cropped[..., :3].contiguous()
            out_desc = "RGB(알파 분리) + 마스크"
        elif C == 4:
            out = cropped.contiguous()
            out_desc = "RGBA 유지 + 마스크"
        else:
            out = cropped.contiguous()
            out_desc = "RGB + 마스크"

        if alpha is not None:
            mask_out = (1.0 - alpha[:, y0:y1, x0:x1]).contiguous()
            transparent_pct = float((alpha[:, y0:y1, x0:x1] < 0.999).float().mean().item() * 100.0)
            alpha_desc = f"{_ALPHA_DESC.get(alpha_source, alpha_source)} (크롭 후 투명 픽셀 {_fmt_pct(transparent_pct)})"
        else:
            mask_out = torch.zeros((B, g["Ch"], g["Cw"]), dtype=torch.float32, device=image.device)
            alpha_desc = "없음 → 불투명 마스크"
            if str(canvas_plan.get("padding_mode")) == "transparent":
                notes.append("계획은 transparent 였지만 결과에 알파가 없어 투명도 보존을 확인할 수 없음")

        report_lines = [
            f"결과 {W}x{H} = 계획 캔버스 일치 | 크롭 {[x0, y0, x1, y1]} → {g['Cw']}x{g['Ch']} | "
            f"배치 {B} | 알파: {alpha_desc} | 출력 {out_desc}",
        ]
        src = canvas_plan.get("source_size")
        if src:
            report_lines.append(
                f"계획: 원본 {src[0]}x{src[1]}, 패딩 {canvas_plan.get('padding_mode', '?')}, "
                f"여백 L/T/R/B {list(canvas_plan.get('padding_ltrb', []))}"
            )
        report_lines.extend(f"주의: {note}" for note in notes)
        report = "\n".join(report_lines)
        logger.info("%s %s", tag, report_lines[0])
        return (out, mask_out, report)


# ══════════════════════════════════════════════════════════════════════
# BMK Canvas Plan From JSON
# ══════════════════════════════════════════════════════════════════════
class BMKCanvasPlanFromJSON:
    """plan_json 문자열 → canvas_plan. 저장해 둔 결과를 나중에 크롭할 때 사용."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "plan_json": ("STRING", {
                    "multiline": True, "default": "",
                    "tooltip": "BMK Canvas Snap Prepare 의 plan_json 출력 내용을 붙여 넣거나 링크로 연결."}),
            },
        }

    RETURN_TYPES = (PLAN_TYPE, "STRING")
    RETURN_NAMES = ("canvas_plan", "report")
    FUNCTION = "load"
    CATEGORY = "BMK/Image"
    DESCRIPTION = (
        "BMK Canvas Snap Prepare 가 내보낸 plan_json 문자열을 검증해 canvas_plan 으로 복원합니다. "
        "편집 결과를 저장해 두고 다른 세션에서 BMK Canvas Snap Restore 로 여백을 제거할 때 사용합니다."
    )
    SEARCH_ALIASES = [
        "canvas plan", "plan json", "load plan", "캔버스 계획", "플랜 복원", "JSON 복원",
    ]

    def load(self, plan_json):
        tag = _TAG_PLAN
        text = (plan_json or "").strip()
        if not text:
            raise ValueError(f"{tag} plan_json 이 비어 있습니다.")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{tag} JSON 파싱 실패: {exc}") from exc
        validate_plan(data, tag)
        plan = BMKCanvasPlan(data)
        report = repr(plan)
        logger.info("%s %s", tag, report)
        return (plan, report)


NODE_CLASS_MAPPINGS = {
    "BMKCanvasSnapPrepare": BMKCanvasSnapPrepare,
    "BMKCanvasSnapRestore": BMKCanvasSnapRestore,
    "BMKCanvasPlanFromJSON": BMKCanvasPlanFromJSON,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "BMKCanvasSnapPrepare": "BMK Canvas Snap Prepare",
    "BMKCanvasSnapRestore": "BMK Canvas Snap Restore",
    "BMKCanvasPlanFromJSON": "BMK Canvas Plan From JSON",
}
