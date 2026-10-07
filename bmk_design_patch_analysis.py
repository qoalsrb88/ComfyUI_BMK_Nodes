"""BMK Design Patch Analysis — 후보 배치 리사이즈, 가드 정합, 색/톤 보정장, ΔE 자동 마스크, 게이트, z순서, IoU.

배경
----
Design Patch 는 GPT 편집 결과(후보)를 원본 크롭 위에 다시 얹는다. 이 모듈은 그 사이의 무료 분석을 맡는다.
노드가 없는 순수 함수 모듈이며 numpy / PIL / cv2 / scipy 만 쓴다(torch, comfy 금지).
수치 근거는 `_proto/reports/measure.md` §1–5 (05 PSD 의 22개 GPT 레이어 실측).

좌표·값 규약
------------
- 이미지: HxWx3 uint8(0..255) 또는 float32(0..1). base 는 HxWx4 도 받는다 — 알파 < 0.99 픽셀은
  "무효"(캔버스 밖 0 패딩 등)로 보고 정합 점수, ΔE, 톤 추정, 게이트에서 뺀다.
- 마스크: HxW float32 0..1, 1 = 보임(레이어 적용). ComfyUI MASK 와 반대이므로 노드 경계에서만 뒤집는다.
- rect / roi: [x0, y0, x1, y1] 끝 배타적, 정수 px. roi 는 크롭 로컬 좌표.
- 픽셀 좌표는 cv2 규약(정수 = 픽셀 중심). quad(SO transform 8코너)만 픽셀 가장자리 좌표다.

정합 행렬 방향 (중요)
---------------------
register() 의 matrix 는 2x3 정방향 변환 M: **cand 픽셀 (u, v) → base 좌표 (x, y) = M·[u, v, 1]**.
- 정렬된 후보 = cv2.warpAffine(cand, M, (w, h))  (WARP_INVERSE_MAP 없이) = warp_affine(cand, M).
- 예) cand 안의 내용이 base 보다 오른쪽 +2.5, 위쪽 −1.25 에 그려졌으면 dx ≈ −2.5, dy ≈ +1.25
  (cand 를 왼쪽·아래로 되돌린다). 테스트의 부호 검사가 이 관계를 고정한다.
- cv2.findTransformECC / measure.md 표의 값(sy 0.966, ty +33.7 등)은 반대 방향(base → cand, 역사상)이다.
  같은 001 을 이 모듈 규약으로 쓰면 sy ≈ 1.035, dy ≈ −35 이다.
- sx, sy, rot_deg, shear 는 M 의 선형부 A = R(rot) · diag(sx, sy) · [[1, shear], [0, 1]] 분해값.
- quad_from_matrix / matrix_from_quad 가 이 행렬과 SO quad(캔버스 px, 가장자리 좌표) 사이를 변환한다.

함수 요약
---------
- place(src, size_wh)              PIL LANCZOS 리사이즈 — PS 배치 재현(실측 MAE 0.07–0.23).
- register(base, cand, mode, guard, *, exclude_mask=None)
                                    가드 정합. translation: identity / 위상상관 초기값 → 가장 덜 변한 60% 픽셀 ECC
                                    (+ 전체 픽셀 ECC).
                                    affine: 광류(DIS)+RANSAC, 자동 마스크 밖 맥락 ECC 후보 중 최선.
                                    exclude_mask(타깃, 예: 손 마스크)를 주면 그 밖에서만 ECC translation → affine.
                                    단계마다 median|diff| 가 guard(2%) 이상 줄 때만 채택하고, 발산 한계(배율 0.9–1.1,
                                    회전 ≤5°, shear ≤0.1, 코너 이동 ≤ 긴 변의 10%) 밖 후보는 버린다.
                                    해석은 긴 변 ≤512 축소본(INTER_AREA), σ1 회색.
- decompose(M) / warp_affine(img, M, size_wh, inverse) / quad_from_matrix(M, rect) / matrix_from_quad(quad, rect)
                                    행렬 분해, cand → base 워프(inverse=True 면 base → cand, 톤 보정장 되돌리기용),
                                    SO quad 변환.
- rgb_to_lab / lab_to_rgb           sRGB(D65) ↔ Lab (skimage 와 같은 식).
- color_match(base, cand, sample)   Lab 채널별 평균/표준편차 이전(가장 덜 변한 60% 픽셀 또는 sample_mask).
- delta_e(base, cand, blur)         σ blur 한 Lab 의 ΔE76.
- auto_mask(base, cand, ...)        (정합) → (색 맞춤) → ΔE > dE → open → close → 작은 조각 제거 → 구멍 채움
                                    → roi → grow → 가우시안 페더. 실측 최적 전역값: dE 8, blur 1.5, open 2,
                                    close 8, grow 8–10, feather σ3–4.
- tone_field(base, cand, exclude)   정규화 컨볼루션 저주파 차이장(0..255 단위, ±clamp). exclude_mask ≥0.5 픽셀은
                                    추정에서 빼고, 빈 곳은 4σ 장 → 전역 평균 순으로 메운다.
- apply_delta(rgb, delta, out_size) 차이장을 풀해상도로 올려 더한다(SO 임베드용 톤 보정 원본).
- gates(base, cand, mask, reg)      마스크 밖 MAD·큰 차이 비율·검은 띠·reframed. 하드 실패만 fails 에 넣는다.
- zorder(entries)                   실효 마스크 면적 큰 것 아래, 작은 것 위(겹침 가중 97.3% 일치).
- soft_iou / recall / precision     소프트 마스크 지표(테스트용).

참조 데이터 회귀 (05 PSD 22+1 레이어, tools/test_bmk_design_patch.py R5–R7)
--------------------------------------------------------------
- 정합: 마스크 없이도 001(sy 1.034, 중심 오차 0.6px)·007-왼쪽(회전 −0.9°)은 적용, 나머지는 identity 이거나
  중심 이동 <1px. 030 은 마스크 없이는 일부만(배율 1.008) — 다시 그린 매듭(타깃)이 크롭 대부분이라 맥락과
  구분되지 않는다. exclude_mask=손 마스크를 주면 030 도 sy 1.034(사용자 수동 보정 1.035). 발산 0건.
- 자동 마스크 soft IoU 평균(기본값): roi 없음 0.64, roi = 손 마스크 bbox+10% 0.69 (시제품 0.65 / 0.68).
- 톤 보정장 σ16 체커보드 홀드아웃: 마스크 밖 MAE 5.00 → 3.44 (0.69배, clamp 12. clamp 없이 3.25 = 시제품 3.24).

v1 (2026-10) M1 — `_proto/work_measure` 시제품(automask_lib, automask, reg, reg_nested, corr, feather) 이식.
   시제품 대비: 마스크 없는 affine 단계(광류+RANSAC, 자동 마스크 맥락 ECC) + 발산 한계 + exclude_mask,
   Lab 변환 자체 구현(skimage 의존 제거), 톤 보정장의 빈 영역 채움(4σ → 전역),
   roi 는 감지 단계와 최종 마스크 모두에 적용, auto_mask 의 정합은 guarded_affine.
"""

from __future__ import annotations

import logging
import math

import cv2
import numpy as np
from PIL import Image
from scipy import ndimage as ndi

logger = logging.getLogger(__name__)
_TAG = "[ComfyUI_BMK_Nodes::DesignPatch]"

_ANA_LONG = 512  # register() 해석 해상도(긴 변)
_ECC_CRIT = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 100, 1e-5)
_KEEP_PCT = 60  # "가장 덜 변한" 픽셀 비율(%)
_LIMITS = {"scale": (0.9, 1.1), "rot_deg": 5.0, "shear": 0.1, "disp_frac": 0.1}


# ─────────────────────────────────────────────────────────────────
# 공통
# ─────────────────────────────────────────────────────────────────
def _split(img, name="image"):
    """(rgb float32 0..1, valid bool HxW | None). 4채널이면 알파 > 0.99 를 유효 픽셀로 본다."""
    a = np.asarray(img)
    if a.ndim != 3 or a.shape[2] not in (3, 4):
        raise ValueError(f"{name}: HxWx3 또는 HxWx4 배열이어야 합니다 (받은 shape {a.shape})")
    f = a.astype(np.float32) / 255.0 if a.dtype == np.uint8 else a.astype(np.float32, copy=False)
    valid = f[..., 3] > 0.99 if a.shape[2] == 4 else None
    return f[..., :3], valid


def _same_size(b, c):
    if b.shape[:2] != c.shape[:2]:
        raise ValueError(f"base {b.shape[1]}x{b.shape[0]} 와 cand {c.shape[1]}x{c.shape[0]} 크기가 다릅니다. "
                         "후보는 place() 로 크롭 크기에 먼저 맞추세요.")


def _like(src, f):
    """float 0..1 결과를 src 의 dtype 규약(uint8 / float32)으로 돌려준다."""
    if np.asarray(src).dtype == np.uint8:
        return np.clip(np.rint(f * 255.0), 0, 255).astype(np.uint8)
    return np.clip(f, 0.0, 1.0).astype(np.float32)


def _gray(rgb):
    return (0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]).astype(np.float32)


def _disk(r):
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))


# sRGB(D65, 2°) <-> CIE Lab. skimage.color.rgb2lab / lab2rgb 와 같은 상수(시제품 측정과 같은 값을 내도록).
_XYZ_FROM_RGB = np.array([[0.412453, 0.357580, 0.180423],
                          [0.212671, 0.715160, 0.072169],
                          [0.019334, 0.119193, 0.950227]], np.float64)
_RGB_FROM_XYZ = np.linalg.inv(_XYZ_FROM_RGB)
_WHITE = np.array([0.95047, 1.0, 1.08883], np.float64)


def rgb_to_lab(rgb):
    """float32 0..1 RGB → Lab (L 0..100). skimage.color.rgb2lab 와 같은 식."""
    c = np.asarray(rgb, np.float32)
    lin = np.where(c > 0.04045, ((c + 0.055) / 1.055) ** 2.4, c / 12.92)
    xyz = lin @ (_XYZ_FROM_RGB / _WHITE[:, None]).T.astype(np.float32)  # 백색점으로 나눈 XYZ
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16.0 / 116.0)
    L = 116.0 * f[..., 1] - 16.0
    a = 500.0 * (f[..., 0] - f[..., 1])
    b = 200.0 * (f[..., 1] - f[..., 2])
    return np.stack([L, a, b], -1).astype(np.float32)


def lab_to_rgb(lab):
    """Lab → float32 RGB (0..1 로 클립)."""
    lab = np.asarray(lab, np.float32)
    fy = (lab[..., 0] + 16.0) / 116.0
    fx = fy + lab[..., 1] / 500.0
    fz = fy - lab[..., 2] / 200.0
    f = np.stack([fx, fy, np.maximum(fz, 0.0)], -1)
    xyz = np.where(f > 0.2068966, f ** 3, (f - 16.0 / 116.0) / 7.787) * _WHITE.astype(np.float32)
    lin = xyz @ _RGB_FROM_XYZ.T.astype(np.float32)
    lin = np.maximum(lin, 0.0)
    c = np.where(lin > 0.0031308, 1.055 * np.power(lin, 1.0 / 2.4) - 0.055, 12.92 * lin)
    return np.clip(c, 0.0, 1.0).astype(np.float32)


# ─────────────────────────────────────────────────────────────────
# 배치
# ─────────────────────────────────────────────────────────────────
def place(src_rgb, size_wh):
    """후보 원본을 크롭 크기 size_wh=(w, h) 로 PIL LANCZOS 리사이즈(PS 배치 재현). uint8 → uint8, float → float32."""
    w, h = int(size_wh[0]), int(size_wh[1])
    a = np.asarray(src_rgb)
    if a.ndim != 3 or a.shape[2] not in (3, 4):
        raise ValueError(f"place: HxWx3/4 배열이어야 합니다 (받은 shape {a.shape})")
    if a.shape[1] == w and a.shape[0] == h:
        return a.copy()
    if a.dtype == np.uint8:
        return np.asarray(Image.fromarray(a).resize((w, h), Image.LANCZOS))
    f = a.astype(np.float32)
    return np.stack([np.asarray(Image.fromarray(np.ascontiguousarray(f[..., k])).resize((w, h), Image.LANCZOS))
                     for k in range(f.shape[2])], -1).astype(np.float32)


# ─────────────────────────────────────────────────────────────────
# 정합
# ─────────────────────────────────────────────────────────────────
def decompose(matrix):
    """2x3 행렬 → {"dx","dy","sx","sy","rot_deg","shear"} (A = R(rot)·diag(sx, sy)·[[1, shear], [0, 1]])."""
    m = np.asarray(matrix, np.float64)
    a, b, c, d = m[0, 0], m[0, 1], m[1, 0], m[1, 1]
    sx = math.hypot(a, c)
    sy = (a * d - b * c) / sx
    return {"dx": float(m[0, 2]), "dy": float(m[1, 2]), "sx": float(sx), "sy": float(sy),
            "rot_deg": float(math.degrees(math.atan2(c, a))), "shear": float((a * b + c * d) / (sx * sx))}


def _max_disp(matrix, w, h):
    """크롭 네 코너(픽셀 중심)가 행렬로 움직이는 최대 거리(px)."""
    m = np.asarray(matrix, np.float64)
    pts = np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]], np.float64)
    moved = pts @ m[:, :2].T + m[:, 2]
    return float(np.hypot(*(moved - pts).T).max())


def _sane(matrix, w, h):
    p = decompose(matrix)
    lo, hi = _LIMITS["scale"]
    return (lo <= p["sx"] <= hi and lo <= p["sy"] <= hi and abs(p["rot_deg"]) <= _LIMITS["rot_deg"]
            and abs(p["shear"]) <= _LIMITS["shear"] and _max_disp(matrix, w, h) <= _LIMITS["disp_frac"] * max(w, h))


def _h3(m):
    return np.vstack([np.asarray(m, np.float64), [0.0, 0.0, 1.0]])


def warp_affine(img, matrix, size_wh=None, inverse=False):
    """정방향 행렬(cand → base)로 img 를 base 좌표로 옮긴다. inverse=True 면 base 좌표의 img 를 cand 좌표로.

    size_wh 생략 시 입력 크기. INTER_CUBIC + BORDER_REFLECT. uint8 은 포화, float 은 값 범위를 건드리지 않는다
    (톤 보정장처럼 0..1 밖 값도 그대로 옮기기 위해).
    """
    a = np.asarray(img)
    w, h = (a.shape[1], a.shape[0]) if size_wh is None else (int(size_wh[0]), int(size_wh[1]))
    m = np.asarray(matrix, np.float64)
    if inverse:
        m = np.linalg.inv(_h3(m))[:2]
    if a.dtype != np.uint8:
        a = a.astype(np.float32, copy=False)
    return cv2.warpAffine(a, m, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REFLECT)


def quad_from_matrix(matrix, rect):
    """정방향 행렬 + 크롭 rect → SO quad 8 floats (캔버스 px, 픽셀 가장자리 좌표, TL·TR·BR·BL).

    후보가 rect 크기로 배치되었다는 전제(place). identity 면 rect 의 네 코너 그대로.
    """
    x0, y0, x1, y1 = rect
    w, h = x1 - x0, y1 - y0
    m = np.asarray(matrix, np.float64)
    out = []
    for u, v in ((0, 0), (w, 0), (w, h), (0, h)):
        p = m[:, :2] @ np.array([u - 0.5, v - 0.5]) + m[:, 2] + 0.5
        out += [float(p[0] + x0), float(p[1] + y0)]
    return out


def matrix_from_quad(quad, rect):
    """SO quad(8 floats, 캔버스 px) + 크롭 rect → 정방향 2x3 행렬(크롭 로컬, 픽셀 중심 규약). 네 코너 최소제곱."""
    x0, y0, x1, y1 = rect
    w, h = x1 - x0, y1 - y0
    src = np.array([[0, 0], [w, 0], [w, h], [0, h]], np.float64)
    dst = np.asarray(quad, np.float64).reshape(4, 2) - [x0, y0]
    X = np.hstack([src, np.ones((4, 1))])
    sol, *_ = np.linalg.lstsq(X, dst, rcond=None)  # dst = src @ A.T + t_e
    A, te = sol[:2].T, sol[2]
    t = te + A @ np.array([0.5, 0.5]) - 0.5
    return np.hstack([A, t[:, None]]).tolist()


def _ana(img_f, k_wh):
    return cv2.resize(img_f, k_wh, interpolation=cv2.INTER_AREA) if k_wh != (img_f.shape[1], img_f.shape[0]) else img_f


def _iwarp(img, W, wh):
    """ECC 규약(역사상, base → cand) 으로 cand 를 base 좌표에 놓는다."""
    return cv2.warpAffine(img, W, wh, flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP, borderMode=cv2.BORDER_REFLECT)


def _flow_affine(Bs, Gs, vm):
    """DIS 광류(base → cand) + RANSAC affine. 질감 상위 40% 픽셀에서 가장 큰 합의 운동 = 역사상 W. 실패 시 None."""
    gm = np.hypot(cv2.Sobel(Bs, cv2.CV_32F, 1, 0), cv2.Sobel(Bs, cv2.CV_32F, 0, 1))
    tex = vm & (gm > np.percentile(gm[vm], _KEEP_PCT))
    yy, xx = np.nonzero(tex)
    if len(xx) < 50:
        return None
    dis = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM)
    fl = dis.calc(np.clip(Bs * 255, 0, 255).astype(np.uint8), np.clip(Gs * 255, 0, 255).astype(np.uint8), None)
    if len(xx) > 20000:
        pick = np.random.default_rng(0).choice(len(xx), 20000, replace=False)
        yy, xx = yy[pick], xx[pick]
    src = np.stack([xx, yy], 1).astype(np.float32)
    A, _inl = cv2.estimateAffine2D(src, src + fl[yy, xx], method=cv2.RANSAC, ransacReprojThreshold=1.0,
                                   maxIters=2000, confidence=0.995)
    return None if A is None else A.astype(np.float32)


def register(base_rgb, cand_rgb, mode="guarded_affine", guard=0.02, *, exclude_mask=None):
    """가드 정합. 반환 dict 의 matrix 는 cand → base 정방향 2x3 (모듈 docstring 참조).

    mode: "guarded_affine" | "translation" | "off".
    exclude_mask(선택, HxW): ≥0.01 인 픽셀(타깃 = 바뀌어야 할 곳, 예: 손 마스크)을 추정·점수에서 뺀다.
      주면 남은 영역(6px 침식)에서 ECC translation → affine, 점수도 그 영역의 median|diff|.
      남는 영역이 200px(해석 해상도) 미만이면 identity.
      없으면 마스크 없이: translation 후보는 가장 덜 변한 60% 픽셀 ECC 와 전체 픽셀 ECC(각각 identity /
      위상상관 초기값), affine 후보는 광류+RANSAC 과, 자동 마스크(ΔE) 밖 영역 ECC(현 단계·RANSAC 에서 출발).
    각 단계는 점수(median|diff|, 회색 σ1)가 직전 채택값보다 guard 이상 줄고 발산 한계 안일 때만 채택.
    030 처럼 다시 그린 타깃이 크롭 대부분이면 마스크 없이는 맥락과 구분이 안 되므로 손 마스크 등이 있으면 넘길 것.
    반환: {"matrix","applied","kind"(identity|translation|affine),"dx","dy","sx","sy","rot_deg","shear",
           "med_before","med_after"(0..255),"improve"(1 − after/before),"disp"(최대 코너 이동 px),"masked"(bool)}
    """
    if mode not in ("guarded_affine", "translation", "off"):
        raise ValueError(f"register: mode 는 guarded_affine / translation / off 중 하나여야 합니다 (받은 값 {mode!r})")
    b, valid = _split(base_rgb, "base")
    c, _ = _split(cand_rgb, "cand")
    _same_size(b, c)
    h, w = b.shape[:2]
    ident = np.eye(2, 3, dtype=np.float32)

    k = min(1.0, _ANA_LONG / max(w, h))
    aw, ah = max(8, round(w * k)), max(8, round(h * k))
    Ba, Ga = _ana(b, (aw, ah)), _ana(c, (aw, ah))
    Bg, Gg = _gray(Ba), _gray(Ga)
    vm = np.ones((ah, aw), bool) if valid is None else _ana(valid.astype(np.float32), (aw, ah)) > 0.99
    Bs, Gs = cv2.GaussianBlur(Bg, (0, 0), 1.0), cv2.GaussianBlur(Gg, (0, 0), 1.0)
    region = vm
    if exclude_mask is not None:
        ex = np.asarray(exclude_mask, np.float32)
        if ex.shape != (h, w):
            raise ValueError(f"register: exclude_mask 크기 {ex.shape[::-1]} 가 이미지 {w}x{h} 와 다릅니다")
        keep = (_ana(ex, (aw, ah)) < 0.01) & vm
        region = cv2.erode(keep.astype(np.uint8), _disk(max(1, round(6 * k)))) > 0

    # 해석 좌표 p_a = S·p + o (픽셀 중심 정렬). 역사상 W_a → 원본 정방향 M = inv(P⁻¹·W_a·P)
    kx, ky = aw / w, ah / h
    P = np.array([[kx, 0, 0.5 * kx - 0.5], [0, ky, 0.5 * ky - 0.5], [0, 0, 1]], np.float64)
    Pinv = np.linalg.inv(P)

    def fwd(W):
        return np.linalg.inv(Pinv @ _h3(W) @ P)[:2]

    def ok(W):
        return W is not None and bool(np.all(np.isfinite(W))) and _sane(fwd(W), w, h)

    def score(W):
        return float(np.median(np.abs(_iwarp(Gs, W, (aw, ah)) - Bs)[region]))

    def ecc(W0, motion, sel):
        try:
            _, W = cv2.findTransformECC(Bs, Gs, W0.astype(np.float32).copy(), motion, _ECC_CRIT,
                                        sel.astype(np.uint8) * 255, 5)
        except cv2.error:
            return None
        return W if ok(W) else None

    def ecc_least_changed(W0, motion):
        """가장 덜 변한 60% 픽셀로 ECC 2라운드. 라운드가 실패하면 직전 성공값(없으면 None)."""
        W, good = W0, None
        for _ in range(2):
            d = np.abs(_iwarp(Gs, W, (aw, ah)) - Bs)
            W = ecc(W, motion, (d < np.percentile(d[vm], _KEEP_PCT)) & vm)
            if W is None:
                break
            good = W
        return good

    def ecc_context(W0, rounds=2):
        """자동 마스크(ΔE) 밖을 맥락으로 보고 ECC translation → affine. 라운드마다 맥락을 다시 잡는다."""
        W, best = W0, None
        Bv = np.dstack([Ba, vm.astype(np.float32)])
        for _ in range(rounds):
            Gw = np.clip(_iwarp(Ga, W, (aw, ah)), 0.0, 1.0)
            am = auto_mask(Bv, Gw, blur=1.5 * k, open_r=max(1, round(2 * k)), close_r=max(1, round(8 * k)),
                           grow=max(1, round(9 * k)), feather_sigma=0, align=False)
            ctx = (am["mask"] < 0.5) & vm
            if ctx.sum() < 200:
                break
            W = ecc(W, cv2.MOTION_TRANSLATION, ctx)
            W = ecc(W, cv2.MOTION_AFFINE, ctx) if W is not None else None
            if W is None:
                break
            s = score(W)
            if best is None or s < best[0]:
                best = (s, W)
        return best

    def pick(cands):
        cands = [x for x in cands if x is not None]
        return min(cands, key=lambda x: x[0]) if cands else None

    usable = region.sum() >= 200
    s0 = score(ident) if usable else 0.0
    cur, cur_s, kind = ident, s0, "identity"
    if mode != "off" and usable and s0 > 0:
        if exclude_mask is not None:
            Wt = ecc(ident, cv2.MOTION_TRANSLATION, region)
            best_t = None if Wt is None else (score(Wt), Wt)
        else:
            win = cv2.createHanningWindow((aw, ah), cv2.CV_64F)
            (px, py), _resp = cv2.phaseCorrelate(Bg.astype(np.float64), Gg.astype(np.float64), win)
            inits = [ident]
            if np.isfinite(px) and np.isfinite(py) and (px, py) != (0.0, 0.0):
                inits.append(np.float32([[1, 0, px], [0, 1, py]]))
            tries = [ecc_least_changed(i, cv2.MOTION_TRANSLATION) for i in inits]
            tries += [ecc(i, cv2.MOTION_TRANSLATION, vm) for i in inits]  # 전체 유효 픽셀(평탄 영역만 남는 경우 대비)
            best_t = pick([(score(W), W) for W in tries if W is not None])
        if best_t is not None and best_t[0] < cur_s * (1.0 - guard):
            cur_s, cur, kind = best_t[0], best_t[1], "translation"
        if mode == "guarded_affine":
            if exclude_mask is not None:
                Wa = ecc(cur, cv2.MOTION_AFFINE, region)
                best_a = None if Wa is None else (score(Wa), Wa)
            else:
                Wr = _flow_affine(Bs, Gs, vm)
                Wr = Wr if ok(Wr) else None
                best_a = pick([None if Wr is None else (score(Wr), Wr), ecc_context(cur),
                               None if Wr is None else ecc_context(Wr)])
            if best_a is not None and best_a[0] < cur_s * (1.0 - guard):
                cur_s, cur, kind = best_a[0], best_a[1], "affine"

    M = fwd(cur) if kind != "identity" else np.eye(2, 3)
    out = {"matrix": [[float(v) for v in row] for row in M], "applied": kind != "identity", "kind": kind}
    out.update(decompose(M))
    out.update({"med_before": round(s0 * 255.0, 4), "med_after": round(cur_s * 255.0, 4),
                "improve": round(1.0 - cur_s / s0, 4) if s0 > 0 else 0.0, "disp": round(_max_disp(M, w, h), 3),
                "masked": exclude_mask is not None})
    logger.debug("%s register %dx%d %s: %s improve %.3f disp %.2f", _TAG, w, h, mode, kind, out["improve"], out["disp"])
    return out


# ─────────────────────────────────────────────────────────────────
# 색 / ΔE
# ─────────────────────────────────────────────────────────────────
def _match_lab(lb, lc, valid, sample_mask=None):
    """Lab 채널별 평균/표준편차 이전. 표본 = sample_mask(≥0.5) 또는 ΔE 하위 60% 픽셀(유효 영역 안)."""
    v = np.ones(lb.shape[:2], bool) if valid is None else valid
    if sample_mask is None:
        d = np.sqrt(((lb - lc) ** 2).sum(-1))
        if not v.any():
            return lc
        sel = (d < np.percentile(d[v], _KEEP_PCT)) & v
    else:
        sel = (np.asarray(sample_mask, np.float32) >= 0.5) & v
    if sel.sum() < 64:
        return lc
    mb, sb = lb[sel].mean(0), lb[sel].std(0)
    mc, sc = lc[sel].mean(0), lc[sel].std(0)
    ratio = np.clip(sb / (sc + 1e-6), 0.5, 2.0)
    return ((lc - mc) * ratio + mb).astype(np.float32)


def color_match(base, cand, sample_mask=None):
    """cand 의 Lab 평균/표준편차를 base 에 맞춘 사본(자동 마스크 전처리용). 반환 dtype = cand 와 같음."""
    b, valid = _split(base, "base")
    c, _ = _split(cand, "cand")
    _same_size(b, c)
    return _like(cand, lab_to_rgb(_match_lab(rgb_to_lab(b), rgb_to_lab(c), valid, sample_mask)))


def _blur3(x, s):
    return cv2.GaussianBlur(x, (0, 0), s) if s > 0 else x


def delta_e(base, cand, blur=1.5):
    """σ=blur 로 흐린 Lab 의 ΔE76 (HxW float32). base 무효 픽셀은 0."""
    b, valid = _split(base, "base")
    c, _ = _split(cand, "cand")
    _same_size(b, c)
    d = np.sqrt(((_blur3(rgb_to_lab(b), blur) - _blur3(rgb_to_lab(c), blur)) ** 2).sum(-1)).astype(np.float32)
    if valid is not None:
        d[~valid] = 0.0
    return d


# ─────────────────────────────────────────────────────────────────
# 자동 마스크
# ─────────────────────────────────────────────────────────────────
def _roi_mask(roi, w, h):
    x0, y0, x1, y1 = (int(round(v)) for v in roi)
    m = np.zeros((h, w), np.uint8)
    m[max(0, y0):max(0, min(h, y1)), max(0, x0):max(0, min(w, x1))] = 1
    return m


def auto_mask(base, cand, *, dE=8.0, blur=1.5, open_r=2, close_r=8, min_area_frac=0.001, fill_holes=True, grow=9,
              feather_sigma=3.5, roi=None, align=True, color=True):
    """ΔE 기반 자동 마스크 초안. 모든 반경·σ 는 입력 배열 px(= 크롭 해상도면 캔버스 px).

    반환: {"mask": HxW float32 0..1 (1 = 후보 적용), "hard": HxW uint8 0/255 (정리된 감지, grow 전),
           "components": [{"id","area","bbox"}] (hard 의 연결 성분, 면적 내림차순), "params": {...}}
    align=True 면 내부에서 register(mode="guarded_affine") 결과를 적용한 뒤 비교한다(params["align_matrix"]) —
    mask 는 항상 base 좌표. 이미 정렬한 후보를 넘길 때는 align=False.
    roi=[x0,y0,x1,y1](크롭 로컬)이면 감지와 최종 마스크 모두 그 밖은 0.
    """
    b, valid = _split(base, "base")
    c, _ = _split(cand, "cand")
    _same_size(b, c)
    h, w = b.shape[:2]
    params = {"dE": dE, "blur": blur, "open_r": open_r, "close_r": close_r, "min_area_frac": min_area_frac,
              "fill_holes": fill_holes, "grow": grow, "feather_sigma": feather_sigma,
              "roi": None if roi is None else [int(round(v)) for v in roi], "align": align, "color": color}
    if align:
        reg = register(base, cand, mode="guarded_affine")
        params["align_matrix"] = reg["matrix"] if reg["applied"] else None
        if reg["applied"]:
            c = np.clip(warp_affine(c, reg["matrix"]), 0.0, 1.0)
    lb, lc = rgb_to_lab(b), rgb_to_lab(c)
    if color:
        lc = _match_lab(lb, lc, valid)
    D = np.sqrt(((_blur3(lb, blur) - _blur3(lc, blur)) ** 2).sum(-1))
    if valid is not None:
        D[~valid] = 0.0

    m = (D > dE).astype(np.uint8)
    if open_r > 0:
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN, _disk(int(open_r)))
    if close_r > 0:
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, _disk(int(close_r)))
    n, lab, st, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
    keep = np.zeros(n, np.uint8)
    keep[1:] = st[1:, cv2.CC_STAT_AREA] >= min_area_frac * w * h
    m = keep[lab]
    if fill_holes:
        m = ndi.binary_fill_holes(m).astype(np.uint8)
    roi_m = None if roi is None else _roi_mask(roi, w, h)
    if roi_m is not None:
        m &= roi_m

    n, lab, st, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
    comps = [{"id": i, "area": int(st[i, cv2.CC_STAT_AREA]),
              "bbox": [int(st[i, 0]), int(st[i, 1]), int(st[i, 0] + st[i, 2]), int(st[i, 1] + st[i, 3])]}
             for i in range(1, n)]
    comps.sort(key=lambda x: -x["area"])

    g = cv2.dilate(m, _disk(int(grow))) if grow > 0 else m
    soft = g.astype(np.float32)
    if feather_sigma > 0:
        soft = cv2.GaussianBlur(soft, (0, 0), float(feather_sigma))
    if roi_m is not None:
        soft *= roi_m
    return {"mask": np.clip(soft, 0.0, 1.0).astype(np.float32), "hard": (m * 255).astype(np.uint8),
            "components": comps, "params": params}


# ─────────────────────────────────────────────────────────────────
# 톤 보정장
# ─────────────────────────────────────────────────────────────────
def tone_field(base, cand, exclude_mask, sigma=16, clamp=12.0):
    """마스크 밖 저주파 차이장. delta(HxWx3 float32, 0..255 단위) 를 cand 에 더하면 base 색에 가까워진다.

    exclude_mask: HxW, ≥0.5 픽셀은 추정에서 뺀다(보통 자동 마스크를 grow 한 것). base 무효 픽셀도 뺀다.
    정규화 컨볼루션 num/den(σ) — 표본이 드문 곳(den < 0.1)은 4σ 장, 그것도 없으면 전역 평균으로 섞어 메운다.
    계산은 σ/4 배 축소 격자에서 하고 선형 보간으로 올린다(σ≥8 일 때; 저주파라 손실 없음). 결과는 ±clamp.
    delta 는 cand 와 같은 좌표다. 정합을 적용한 cand 로 구했다면 원본 좌표로는 warp_affine(delta, M, inverse=True).
    """
    b, valid = _split(base, "base")
    c, _ = _split(cand, "cand")
    _same_size(b, c)
    h, w = b.shape[:2]
    ex = np.asarray(exclude_mask, np.float32)
    if ex.shape != (h, w):
        raise ValueError(f"tone_field: exclude_mask 크기 {ex.shape[::-1]} 가 이미지 {w}x{h} 와 다릅니다")
    if sigma <= 0:
        raise ValueError(f"tone_field: sigma 는 0 보다 커야 합니다 (받은 값 {sigma})")
    wt = (ex < 0.5).astype(np.float32)
    if valid is not None:
        wt *= valid
    stats = {"sigma": float(sigma), "clamp": float(clamp), "coverage": round(float(wt.mean()), 4)}
    if wt.sum() < 64:
        stats.update({"mean_rgb": [0.0, 0.0, 0.0], "abs_mean": 0.0, "max_abs": 0.0, "clamped_frac": 0.0,
                      "note": "표본 부족 — 보정 없음"})
        return {"delta": np.zeros((h, w, 3), np.float32), "stats": stats}

    res = (b - c) * 255.0 * wt[..., None]
    g = res.reshape(-1, 3).sum(0) / wt.sum()
    f = max(1, int(sigma // 4))
    sw, sh = max(1, round(w / f)), max(1, round(h / f))
    rs = cv2.resize(res, (sw, sh), interpolation=cv2.INTER_AREA) if f > 1 else res
    ws = cv2.resize(wt, (sw, sh), interpolation=cv2.INTER_AREA) if f > 1 else wt

    def nc(s):
        num = cv2.GaussianBlur(rs, (0, 0), s / f)
        den = cv2.GaussianBlur(ws, (0, 0), s / f)[..., None]
        return num / np.maximum(den, 1e-6), np.clip(den / 0.1, 0.0, 1.0)

    d4, a4 = nc(4.0 * sigma)
    d1, a1 = nc(float(sigma))
    d = a1 * d1 + (1.0 - a1) * (a4 * d4 + (1.0 - a4) * g)
    if f > 1:
        d = cv2.resize(d, (w, h), interpolation=cv2.INTER_LINEAR)
    clamped = np.abs(d) > clamp
    d = np.clip(d, -clamp, clamp).astype(np.float32)
    stats.update({"mean_rgb": [round(float(v), 3) for v in g], "abs_mean": round(float(np.abs(d).mean()), 3),
                  "max_abs": round(float(np.abs(d).max()), 3), "clamped_frac": round(float(clamped.mean()), 4)})
    return {"delta": d, "stats": stats}


def apply_delta(rgb, delta, out_size=None):
    """차이장 delta(0..255 단위)를 rgb 에 더한다. out_size=(W, H) 면 rgb 를 그 크기로(LANCZOS) 맞추고
    delta 를 선형 보간으로 올린다(풀해상도 SO 임베드용). 4채널이면 알파는 그대로. 반환 dtype = rgb 와 같음."""
    a = np.asarray(rgb)
    W, H = (a.shape[1], a.shape[0]) if out_size is None else (int(out_size[0]), int(out_size[1]))
    if (a.shape[1], a.shape[0]) != (W, H):
        a = place(a, (W, H))
    d = np.asarray(delta, np.float32)
    if d.shape[:2] != (H, W):
        d = cv2.resize(d, (W, H), interpolation=cv2.INTER_LINEAR)
    out = a.copy()
    if a.dtype == np.uint8:
        out[..., :3] = np.clip(np.rint(a[..., :3].astype(np.float32) + d), 0, 255).astype(np.uint8)
    else:
        out = out.astype(np.float32)
        out[..., :3] = np.clip(out[..., :3] + d / 255.0, 0.0, 1.0)
    return out


# ─────────────────────────────────────────────────────────────────
# 게이트 / z순서 / 지표
# ─────────────────────────────────────────────────────────────────
def _band(bad, from_end, axis):
    """가장자리부터 연속으로 bad 비율 ≥0.5 인 행(axis=0)/열(axis=1) 수."""
    frac = bad.mean(axis=1 - axis)
    if from_end:
        frac = frac[::-1]
    run = 0
    for v in frac:
        if v < 0.5:
            break
        run += 1
    return run


def gates(base, cand, mask, reg, *, mad_fail=16.0, frac24_fail=0.4, band_px=2):
    """무료 하드 게이트. cand 는 합성될 상태(정합 적용 후), mask 는 최종 마스크(1 = 후보 보임).

    반환: {"outside_mad"(마스크<0.01 영역 평균 |diff|, 0..255), "outside_frac_gt24"(그 영역 중 채널 최대
           |diff| > 24 비율), "black_band"(bool), "black_edges"({"top","bottom","left","right"}: 줄 수),
           "reframed"(reg.applied), "fails": [...]}
    black_band: 원본에서 근흑(<8)이 아니던(무효 픽셀 포함) 픽셀이 후보에서 근흑인 줄이 가장자리에 ≥band_px 연속.
    fails 는 하드 실패만: outside_mad > mad_fail, outside_frac_gt24 > frac24_fail, black_band.
    """
    b, valid = _split(base, "base")
    c, _ = _split(cand, "cand")
    _same_size(b, c)
    m = np.asarray(mask, np.float32)
    v = np.ones(b.shape[:2], bool) if valid is None else valid
    out = (m < 0.01) & v
    diff = np.abs(c - b) * 255.0
    if out.any():
        mad = float(diff.mean(-1)[out].mean())
        frac = float((diff.max(-1) > 24.0)[out].mean())
    else:
        mad, frac = 0.0, 0.0
    bad = (c.max(-1) < 8 / 255.0) & ~((b.max(-1) < 8 / 255.0) & v)
    edges = {"top": _band(bad, False, 0), "bottom": _band(bad, True, 0),
             "left": _band(bad, False, 1), "right": _band(bad, True, 1)}
    black = max(edges.values()) >= band_px
    fails = [name for name, hit in (("outside_mad", mad > mad_fail), ("outside_frac_gt24", frac > frac24_fail),
                                    ("black_band", black)) if hit]
    return {"outside_mad": round(mad, 3), "outside_frac_gt24": round(frac, 4), "black_band": bool(black),
            "black_edges": edges, "reframed": bool(reg.get("applied")) if reg else False, "fails": fails}


def zorder(entries):
    """[{"key","area"}] → key 목록(아래 → 위). 실효 마스크 면적이 큰 것이 아래, 같으면 입력 순서."""
    idx = sorted(range(len(entries)), key=lambda i: (-float(entries[i]["area"]), i))
    return [entries[i]["key"] for i in idx]


def _pair(a, b, valid):
    a = np.asarray(a, np.float32)
    b = np.asarray(b, np.float32)
    if valid is not None:
        v = np.asarray(valid, bool)
        a, b = a[v], b[v]
    return a, b


def soft_iou(a, b, valid=None):
    """Σmin(a,b) / Σmax(a,b). 둘 다 비면 1.0."""
    a, b = _pair(a, b, valid)
    mx = float(np.maximum(a, b).sum())
    return float(np.minimum(a, b).sum()) / mx if mx > 0 else 1.0


def recall(pred, gt, valid=None):
    """Σmin(pred,gt) / Σgt. gt 가 비면 1.0."""
    p, g = _pair(pred, gt, valid)
    s = float(g.sum())
    return float(np.minimum(p, g).sum()) / s if s > 0 else 1.0


def precision(pred, gt, valid=None):
    """Σmin(pred,gt) / Σpred. pred 가 비면 1.0."""
    p, g = _pair(pred, gt, valid)
    s = float(p.sum())
    return float(np.minimum(p, g).sum()) / s if s > 0 else 1.0
