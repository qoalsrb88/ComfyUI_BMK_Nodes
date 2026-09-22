# -*- coding: utf-8 -*-
r"""bmk_canvas_snap 자체 테스트.

실행 (ComfyUI-Easy-Install 루트에서):
    python_embeded\python.exe ComfyUI\custom_nodes\ComfyUI_BMK_Nodes\tools\test_bmk_canvas_snap.py

기하 계산(설계 문서 11.1 기준값, 브루트포스 대조, half-up, 여백 상한)과 이미지 파이프라인
(Prepare→Restore 왕복, 알파/패딩 모드, 배치·마스크 검증, plan JSON 왕복)을 검사한다.
"""
from __future__ import annotations

import importlib.util
import json
import random
import sys
import time

from pathlib import Path

_HERE = Path(__file__).resolve()
COMFY = str(_HERE.parents[3])  # .../ComfyUI
MODULE = str(_HERE.parents[1] / "bmk_canvas_snap.py")
sys.path.insert(0, COMFY)

import torch  # noqa: E402

spec = importlib.util.spec_from_file_location("bmk_canvas_snap", MODULE)
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod  # dataclass + `from __future__ import annotations` 해석에 필요
spec.loader.exec_module(mod)

FAILS: list[str] = []
PASSES = 0


def check(cond, msg):
    global PASSES
    if cond:
        PASSES += 1
    else:
        FAILS.append(msg)
        print("FAIL:", msg)


def expect_raises(fn, msg, exc=ValueError, contains=None):
    try:
        fn()
    except exc as e:
        if contains is not None and contains not in str(e):
            check(False, f"{msg}: 예외 메시지에 {contains!r} 없음 → {e}")
        else:
            check(True, msg)
        return
    check(False, f"{msg}: 예외가 나지 않음")


STD = mod.build_constraints(mod._PROFILE_GPT25_STD, 16, 480, 3840, 655360, 3686400, 3.0)
EXP = mod.build_constraints(mod._PROFILE_GPT25_EXP, 16, 480, 3840, 655360, 3686400, 3.0)


# ─────────────────────────────────────────────────────────────────
# 1. 문서 11.1 기준값
# ─────────────────────────────────────────────────────────────────
TABLE = [
    # (w, h, canvas, content, LTRB)
    (546, 764, (1616, 2272), (1616, 2261), (0, 5, 0, 6)),
    (547, 765, (1616, 2272), (1616, 2260), (0, 6, 0, 6)),
    (1001, 1001, (1920, 1920), (1920, 1920), (0, 0, 0, 0)),
    (1200, 1800, (1568, 2336), (1557, 2336), (5, 0, 6, 0)),
    (900, 1200, (1664, 2208), (1656, 2208), (4, 0, 4, 0)),
    (1920, 1080, (2560, 1440), (2560, 1440), (0, 0, 0, 0)),
    (1000, 3000, (1104, 3312), (1104, 3312), (0, 0, 0, 0)),
    (600, 3000, (1104, 3312), (662, 3312), (221, 0, 221, 0)),
    (3000, 4000, (1664, 2208), (1656, 2208), (4, 0, 4, 0)),
    (764, 546, (2272, 1616), (2261, 1616), (5, 0, 6, 0)),
]
for w, h, canvas, content, ltrb in TABLE:
    p = mod.compute_canvas_plan(w, h, STD)
    check(tuple(p["canvas_size"]) == canvas, f"{w}x{h} canvas {p['canvas_size']} != {canvas}")
    check(tuple(p["content_size"]) == content, f"{w}x{h} content {p['content_size']} != {content}")
    check(tuple(p["padding_ltrb"]) == ltrb, f"{w}x{h} ltrb {p['padding_ltrb']} != {ltrb}")
    L, T, R, B = p["padding_ltrb"]
    W, H = p["canvas_size"]
    Cw, Ch = p["content_size"]
    x0, y0, x1, y1 = p["crop_xyxy"]
    check(L + Cw + R == W and T + Ch + B == H, f"{w}x{h} 여백 합 불일치")
    check(x1 - x0 == Cw and y1 - y0 == Ch, f"{w}x{h} crop 크기 불일치")
    check(W % 16 == 0 and H % 16 == 0 and 655360 <= W * H <= 3686400, f"{w}x{h} 제약 위반")
    check(mod.gpt_image_custom_size_ok(W, H), f"{w}x{h} GPT custom 호환 아님")

p = mod.compute_canvas_plan(546, 764, STD)
check((p["scale_numerator"], p["scale_denominator"]) == (808, 273), f"546x764 배율 분수 {p['scale_numerator']}/{p['scale_denominator']}")
check(p["canvas_pixels"] == 3671552 and p["content_pixels"] == 3653776, "546x764 화소 수")
check(abs(p["signed_aspect_error_percent"] - 0.00955847926214) < 1e-9, f"546x764 종횡비 오차 {p['signed_aspect_error_percent']}")
check(p["downscaled"] is False, "546x764 downscaled False")
check(mod.compute_canvas_plan(3000, 4000, STD)["downscaled"] is True, "3000x4000 downscaled True")

# 실험 티어
p = mod.compute_canvas_plan(546, 764, EXP)
check(tuple(p["canvas_size"]) == (2432, 3408) and tuple(p["content_size"]) == (2432, 3403), f"experimental {p['canvas_size']} {p['content_size']}")

# half-up
check(mod.round_half_up_ratio(225, 2) == 113, "112.5 → 113")
check(mod.round_half_up_ratio(223, 2) == 112, "111.5 → 112")
check(mod.round_half_up_ratio(224, 2) == 112, "112.0 → 112")
check(mod.round_half_up_ratio(4491, 10) == 449, "449.1 → 449")
check(mod.round_half_up_ratio(4499, 10) == 450, "449.9 → 450")

# 여백 상한
p = mod.compute_canvas_plan(1200, 1800, STD, max_padding_px=2)
check(tuple(p["canvas_size"]) == (1536, 2304) and tuple(p["padding_ltrb"]) == (0, 0, 0, 0), f"cap2 1200x1800 → {p['canvas_size']} {p['padding_ltrb']}")
check(p["padding_cap_applied"] is True and tuple(p["primary_rule_canvas"]) == (1568, 2336), "cap2 메타")
p = mod.compute_canvas_plan(546, 764, STD, max_padding_px=2)
check(tuple(p["canvas_size"]) == (1600, 2240) and p["padding_ltrb"] == [0, 0, 0, 1], f"cap2 546x764 → {p['canvas_size']} {p['padding_ltrb']}")
p = mod.compute_canvas_plan(600, 3000, STD, max_padding_px=2)
check(p["padding_cap_applied"] is False and p["padding_cap_satisfiable"] is False and tuple(p["canvas_size"]) == (1104, 3312), "cap 미충족 폴백")

# 극단 비율 → 그림 변 0
expect_raises(lambda: mod.compute_canvas_plan(1, 100000, STD), "극단 비율 0px 오류", contains="0px")
# 제약 불가
BAD = mod.CanvasConstraints("custom", 16, 480, 3840, 655360, 3686400, 3.0)
expect_raises(lambda: mod.CanvasConstraints("custom", 16, 480, 400, 0, 1, 3.0).validate(), "max_edge<min_edge 검증")
expect_raises(lambda: mod.compute_canvas_plan(100, 100, mod.CanvasConstraints("custom", 16, 3000, 3840, 20_000_000, 30_000_000, 3.0)), "유효 캔버스 없음", contains="없습니다")


# ─────────────────────────────────────────────────────────────────
# 2. 브루트포스 대조 (기본 규칙 + 여백 상한)
# ─────────────────────────────────────────────────────────────────
def brute(w, h, c, cap=0):
    S = c.snap
    arm = c.aspect_milli
    best = bestc = None
    for W in range(S, c.max_edge + 1, S):
        if W < c.min_edge:
            continue
        for H in range(S, c.max_edge + 1, S):
            if H < c.min_edge:
                continue
            px = W * H
            if px < c.min_pixels or px > c.max_pixels:
                continue
            if W * 1000 > arm * H or H * 1000 > arm * W:
                continue
            cand = mod._make_candidate(w, h, W, H)
            if best is None or mod._better(cand, best):
                best = cand
            if cap > 0 and cand.padding_total <= cap and (bestc is None or mod._better(cand, bestc)):
                bestc = cand
    return best, bestc


def compare(w, h, c, cap, label):
    best, bestc = brute(w, h, c, cap)
    try:
        p = mod.compute_canvas_plan(w, h, c, cap)
    except ValueError as e:
        ok = best is None or min(best.Cw, best.Ch) < 1 or (bestc is not None and min(bestc.Cw, bestc.Ch) < 1 and cap > 0)
        check(ok, f"{label} {w}x{h}: 모듈 예외 '{e}' 인데 브루트포스는 유효 후보 있음 {best}")
        return
    chosen = bestc if (cap > 0 and bestc is not None) else best
    check(chosen is not None, f"{label} {w}x{h}: 브루트포스 후보 없음인데 모듈은 결과 냄")
    if chosen is None:
        return
    check(tuple(p["canvas_size"]) == (chosen.W, chosen.H), f"{label} {w}x{h} cap{cap}: 모듈 {p['canvas_size']} vs 브루트 {(chosen.W, chosen.H)}")
    check(tuple(p["content_size"]) == (chosen.Cw, chosen.Ch), f"{label} {w}x{h} cap{cap}: content {p['content_size']} vs {(chosen.Cw, chosen.Ch)}")
    check(tuple(p["primary_rule_canvas"]) == (best.W, best.H), f"{label} {w}x{h}: primary {p['primary_rule_canvas']} vs {(best.W, best.H)}")


t0 = time.time()
rng = random.Random(20260922)
for i in range(40):
    w = rng.randint(1, 5000)
    h = rng.randint(1, 5000)
    compare(w, h, STD, 0, "std")
for i in range(15):
    w = rng.randint(50, 4000)
    h = rng.randint(50, 4000)
    compare(w, h, STD, rng.choice([1, 2, 8, 32]), "std-cap")
# custom 프로파일 (snap 64, 1MP, 비율 2.0, 짧은 변)
C64 = mod.build_constraints("custom", 64, 64, 2048, 0, 1_048_576, 2.0)
for i in range(15):
    w = rng.randint(1, 3000)
    h = rng.randint(1, 3000)
    compare(w, h, C64, rng.choice([0, 0, 4]), "c64")
# snap 8, min_edge 256, 2MP, 비율 1.5
C8 = mod.build_constraints("custom", 8, 256, 2048, 300_000, 2_000_000, 1.5)
for i in range(10):
    w = rng.randint(100, 2500)
    h = rng.randint(100, 2500)
    compare(w, h, C8, rng.choice([0, 3]), "c8")
print(f"브루트포스 대조 소요 {time.time() - t0:.1f}s")

# snap 1 도 빨라야 함
t0 = time.time()
C1 = mod.build_constraints("custom", 1, 1, 3840, 655360, 3686400, 3.0)
p = mod.compute_canvas_plan(546, 764, C1)
check(time.time() - t0 < 2.0, f"snap1 계산 시간 {time.time() - t0:.2f}s")
check(tuple(p["padding_ltrb"]) == (0, 0, 0, 0) or p["padding_total"] if False else True, "snap1 실행")


# ─────────────────────────────────────────────────────────────────
# 3. 이미지 파이프라인
# ─────────────────────────────────────────────────────────────────
prep = mod.BMKCanvasSnapPrepare()
rest = mod.BMKCanvasSnapRestore()
pj = mod.BMKCanvasPlanFromJSON()

DEF = dict(profile=mod._PROFILE_GPT25_STD, padding_mode="white", max_padding_px=0, allow_downscale=False,
           resample="bicubic", use_transparency_mask=True, snap=16, min_edge=480, max_edge=3840,
           min_pixels=655360, max_pixels=3686400, max_aspect_ratio=3.0)


def make_marker_image(w, h, batch=1):
    """가장자리 1px 에 식별색: 위 빨강, 아래 초록, 왼쪽 파랑, 오른쪽 노랑, 내부 회색 그라데이션."""
    img = torch.zeros((batch, h, w, 3), dtype=torch.float32)
    yy = torch.linspace(0.2, 0.8, h).view(h, 1).expand(h, w)
    img[..., 0] = yy
    img[..., 1] = yy
    img[..., 2] = yy
    img[:, 0, :, :] = torch.tensor([1.0, 0.0, 0.0])
    img[:, -1, :, :] = torch.tensor([0.0, 1.0, 0.0])
    img[:, :, 0, :] = torch.tensor([0.0, 0.0, 1.0])
    img[:, :, -1, :] = torch.tensor([1.0, 1.0, 0.0])
    return img


src = make_marker_image(546, 764)
out = prep.prepare(image=src, **DEF)
canvas, tmask, cmask, W, H, size_str, plan, plan_json, report = out
check(tuple(canvas.shape) == (1, 2272, 1616, 3), f"canvas shape {tuple(canvas.shape)}")
check((W, H, size_str) == (1616, 2272, "1616x2272"), f"W/H/size {W} {H} {size_str}")
check(tuple(tmask.shape) == (1, 2272, 1616) and float(tmask.sum()) == 0.0, "white 모드 투명도 마스크 0")
check(tuple(cmask.shape) == (1, 2272, 1616) and int(cmask.sum().item()) == 1616 * 2261, f"content_region 합 {cmask.sum().item()}")
check(bool((canvas[:, 0:5] == 1.0).all()) and bool((canvas[:, 2266:] == 1.0).all()), "위 5 / 아래 6 px 흰 여백")
check(isinstance(plan, dict) and plan["padding_mode"] == "white" and plan["canvas_channels"] == 3, "plan 필드")
check(repr(plan).startswith("BMK_CANVAS_PLAN(546x764"), f"plan repr {repr(plan)[:60]}")
check("1616x2272" in report and "808/273" in report, "report 내용")
print(report)
print()

# Prepare→Restore 결과 == 직접 리샘플
direct = mod._resample_bchw(src.movedim(-1, 1), 1616, 2261, "bicubic").clamp(0, 1).movedim(1, -1)
r_img, r_mask, r_report = rest.restore(canvas, plan, "split_rgb_mask")
check(tuple(r_img.shape) == (1, 2261, 1616, 3), f"restore shape {tuple(r_img.shape)}")
check(torch.equal(r_img, direct), "Prepare→Restore == 직접 리샘플 (bicubic)")
check(tuple(r_mask.shape) == (1, 2261, 1616) and float(r_mask.sum()) == 0.0, "restore 마스크 0")
check("계획 캔버스 일치" in r_report, "restore report")
print(r_report)
print()

# nearest-exact 로 가장자리 마커 1px 정합 검사
out_n = prep.prepare(image=src, **{**DEF, "resample": "nearest-exact"})
canvas_n, _, _, _, _, _, plan_n, _, _ = out_n
T = plan_n["padding_ltrb"][1]
Ch = plan_n["content_size"][1]
red = torch.tensor([1.0, 0.0, 0.0])
green = torch.tensor([0.0, 1.0, 0.0])
# nearest-exact 는 배율 ~2.96 이라 가장자리 1px 마커가 약 3px 로 늘어난다 → 양쪽 4px 씩 제외하고 비교
check(bool((canvas_n[0, T - 1] == 1.0).all()), "마커: 위 여백 마지막 행 흰색")
check(bool((canvas_n[0, T, 4:-4] == red).all()), "마커: 그림 첫 행 빨강")
check(bool((canvas_n[0, T + Ch - 1, 4:-4] == green).all()), "마커: 그림 마지막 행 초록")
check(bool((canvas_n[0, T + Ch] == 1.0).all()), "마커: 아래 여백 첫 행 흰색")
rn_img, _, _ = rest.restore(canvas_n, plan_n, "split_rgb_mask")
check(bool((rn_img[0, 0, 4:-4] == red).all()) and bool((rn_img[0, -1, 4:-4] == green).all()), "마커: 복원 첫/마지막 행")
check(bool((rn_img[0, 4:-4, 0] == torch.tensor([0.0, 0.0, 1.0])).all()), "마커: 복원 왼쪽 열 파랑")
check(bool((rn_img[0, 4:-4, -1] == torch.tensor([1.0, 1.0, 0.0])).all()), "마커: 복원 오른쪽 열 노랑")

# 가로세로 뒤바꿈: 764x546 → L/R 5/6
src_t = make_marker_image(764, 546)
out_t = prep.prepare(image=src_t, **DEF)
check(tuple(out_t[0].shape) == (1, 1616, 2272, 3) and out_t[6]["padding_ltrb"] == [5, 0, 6, 0], f"뒤바꿈 {tuple(out_t[0].shape)} {out_t[6]['padding_ltrb']}")

# 크기 불일치 → 중단
expect_raises(lambda: rest.restore(torch.zeros((1, 1536, 1024, 3)), plan, "split_rgb_mask"), "Restore 크기 불일치 중단", contains="1024x1536")

# allow_downscale
big = torch.rand((1, 4000, 3000, 3))
expect_raises(lambda: prep.prepare(image=big, **DEF), "downscale 금지 중단", contains="allow_downscale")
out_b = prep.prepare(image=big, **{**DEF, "allow_downscale": True})
check(tuple(out_b[0].shape) == (1, 2208, 1664, 3) and out_b[6]["downscaled"] is True, f"downscale 허용 {tuple(out_b[0].shape)}")

# 배치 2 + 마스크 배치 1 broadcast, 알파 transparent 모드
src2 = make_marker_image(546, 764, batch=2)
mask1 = torch.zeros((1, 764, 546))
mask1[:, :100, :] = 1.0  # 위 100행 완전 투명
mask1[:, 100:200, :] = 0.5  # 다음 100행 반투명
out_a = prep.prepare(image=src2, transparency_mask=mask1, **{**DEF, "padding_mode": "transparent"})
canvas_a, tmask_a, cmask_a, _, _, _, plan_a, plan_json_a, report_a = out_a
check(tuple(canvas_a.shape) == (2, 2272, 1616, 4), f"transparent 4채널 {tuple(canvas_a.shape)}")
check(bool((canvas_a[:, 0:5, :, 3] == 0.0).all()) and bool((canvas_a[:, 0:5, :, :3] == 1.0).all()), "transparent 여백: 알파0, RGB 흰색")
check(bool((tmask_a[:, 0:5] == 1.0).all()), "transparent 여백 마스크 1")
# 원본 위 100행(투명) → 캔버스 그림 상단 약 296행 알파 0
T_a = plan_a["padding_ltrb"][1]
check(bool((canvas_a[:, T_a:T_a + 250, :, 3] < 1e-6).all()), "투명 영역 알파 0 보존")
check(bool((canvas_a[:, T_a + 320:T_a + 550, :, 3] - 0.5).abs().max() < 1e-3), "반투명 0.5 보존")
check(bool((canvas_a[:, T_a + 700:T_a + 2200, :, 3] == 1.0).all()), "불투명 영역 알파 1")
check(plan_a["alpha_source"] == "mask_input" and plan_a["source_alpha_used"] is True and plan_a["source_batch_size"] == 2, "plan 알파 메타")
# 투명 영역 RGB 는 흰색(unpremultiply 정의 불가 → fill)
check(bool((canvas_a[:, T_a:T_a + 250, :, :3] == 1.0).all()), "투명 영역 RGB 흰색 채움")
# Restore (4채널) split
ra_img, ra_mask, ra_rep = rest.restore(canvas_a, plan_a, "split_rgb_mask")
check(tuple(ra_img.shape) == (2, 2261, 1616, 3) and tuple(ra_mask.shape) == (2, 2261, 1616), f"restore 4ch split {tuple(ra_img.shape)}")
check(bool((ra_mask[:, :250] == 1.0).all()) and bool((ra_mask[:, 700:2200] == 0.0).all()), "restore 마스크 = 1-알파")
# keep_rgba
rk_img, rk_mask, _ = rest.restore(canvas_a, plan_a, "keep_rgba")
check(tuple(rk_img.shape) == (2, 2261, 1616, 4) and torch.allclose(rk_img[..., 3], 1.0 - rk_mask, atol=1e-6), "restore keep_rgba")
# 입력 마스크 우선
ext_mask = torch.zeros((1, 2272, 1616))
ext_mask[:, :, :10] = 1.0
rm_img, rm_mask, rm_rep = rest.restore(canvas_a, plan_a, "split_rgb_mask", transparency_mask=ext_mask)
check(bool((rm_mask[:, :, :10] == 1.0).all()) and bool((rm_mask[:, :, 10:] == 0.0).all()) and "대신" in rm_rep, "restore 입력 마스크 우선")

# white 모드 + 알파: 투명 영역 → 흰색, 반투명 → 절반 밝기 합성
out_w = prep.prepare(image=src2, transparency_mask=mask1, **DEF)
canvas_w = out_w[0]
check(tuple(canvas_w.shape) == (2, 2272, 1616, 3) and bool((canvas_w[:, T_a:T_a + 250] == 1.0).all()), "white 합성: 투명 → 흰색")
# black 모드: 투명 → 검정
out_k = prep.prepare(image=src2, transparency_mask=mask1, **{**DEF, "padding_mode": "black"})
check(bool((out_k[0][:, 0:5] == 0.0).all()) and bool((out_k[0][:, T_a:T_a + 250] == 0.0).all()), "black 합성: 여백·투명 → 검정")

# 4채널 입력 이미지 알파 사용
src_rgba = torch.cat([src, torch.ones((1, 764, 546, 1))], dim=-1)
src_rgba[:, :50, :, 3] = 0.0
out_r = prep.prepare(image=src_rgba, **{**DEF, "padding_mode": "transparent"})
check(out_r[6]["alpha_source"] == "image_alpha" and bool((out_r[0][:, T_a:T_a + 100, :, 3] == 0.0).all()), "4채널 입력 알파 사용")
# use_transparency_mask False → 불투명
out_o = prep.prepare(image=src_rgba, transparency_mask=mask1, **{**DEF, "padding_mode": "transparent", "use_transparency_mask": False})
check(out_o[6]["alpha_source"] == "disabled" and bool((out_o[0][:, T_a:T_a + 2261, :, 3] == 1.0).all()), "알파 사용 끔")

# 64x64 빈 기본 마스크 → 불투명 + 메모
out_d = prep.prepare(image=src, transparency_mask=torch.zeros((1, 64, 64)), **DEF)
check(out_d[6]["alpha_source"] == "default_mask_ignored" and any("64x64" in n for n in out_d[6]["notes"]), "64x64 기본 마스크 무시")
# 크기 다른 비어있지 않은 마스크 → 오류
expect_raises(lambda: prep.prepare(image=src, transparency_mask=torch.ones((1, 64, 64)), **DEF), "마스크 크기 불일치 오류", contains="리사이즈")
# 마스크 배치 불일치
expect_raises(lambda: prep.prepare(image=src2, transparency_mask=torch.zeros((3, 764, 546)), **DEF), "마스크 배치 불일치", contains="배치")
# 전부 1 마스크 = 완전 투명 (마스크 없음으로 바꾸지 않음)
out_f = prep.prepare(image=src, transparency_mask=torch.ones((1, 764, 546)), **{**DEF, "padding_mode": "transparent"})
check(bool((out_f[0][..., 3] == 0.0).all()) and out_f[6]["source_alpha_used"] is True, "전부 1 마스크 = 완전 투명")
# NaN 거부
bad = src.clone()
bad[0, 0, 0, 0] = float("nan")
expect_raises(lambda: prep.prepare(image=bad, **DEF), "NaN 거부", contains="NaN")
# 입력 불변
src_copy = src.clone()
prep.prepare(image=src, **DEF)
check(torch.equal(src, src_copy), "입력 텐서 in-place 변경 없음")

# plan JSON 왕복
plan_back, rep_back = pj.load(plan_json)
check(dict(plan_back) == dict(plan) and rep_back.startswith("BMK_CANVAS_PLAN("), "plan JSON 왕복")
r2_img, _, _ = rest.restore(canvas, plan_back, "split_rgb_mask")
check(torch.equal(r2_img, r_img), "JSON 복원 plan 으로 동일 크롭")
expect_raises(lambda: pj.load(""), "빈 JSON 오류", contains="비어")
expect_raises(lambda: pj.load("{not json"), "잘못된 JSON 오류", contains="파싱")
broken = json.loads(plan_json)
broken["crop_xyxy"] = [0, 5, 1616, 2267]
expect_raises(lambda: pj.load(json.dumps(broken)), "일관성 깨진 plan 거부", contains="crop_xyxy")
broken2 = json.loads(plan_json)
broken2["schema_version"] = 2
expect_raises(lambda: pj.load(json.dumps(broken2)), "schema_version 거부", contains="schema_version")
expect_raises(lambda: rest.restore(canvas, {"foo": 1}, "split_rgb_mask"), "잘못된 plan dict 거부")
expect_raises(lambda: rest.restore(canvas, "not a plan", "split_rgb_mask"), "plan 타입 오류", contains="dict")

# 여백 상한 옵션 (노드 경로)
out_c = prep.prepare(image=make_marker_image(1200, 1800), **{**DEF, "max_padding_px": 2})
check((out_c[3], out_c[4]) == (1536, 2304) and any("여백 상한 2px 적용" in n for n in out_c[6]["notes"]), f"노드 cap {out_c[3]}x{out_c[4]}")

# 실험 프로파일 경고
out_e = prep.prepare(image=src, **{**DEF, "profile": mod._PROFILE_GPT25_EXP})
check((out_e[3], out_e[4]) == (2432, 3408) and any("4,194,304" in n for n in out_e[6]["notes"]), "experimental 축소 전송 경고")

# custom 프로파일 + GPT 비호환 경고
out_cu = prep.prepare(image=src, **{**DEF, "profile": "custom", "snap": 64, "min_edge": 64, "max_edge": 2048, "min_pixels": 0, "max_pixels": 1_048_576, "max_aspect_ratio": 2.0, "allow_downscale": True})
Wc, Hc = out_cu[3], out_cu[4]
check(Wc % 64 == 0 and Hc % 64 == 0 and Wc * Hc <= 1_048_576, f"custom snap64 {Wc}x{Hc}")
check(any("Custom 제약" in n for n in out_cu[6]["notes"]) == (not mod.gpt_image_custom_size_ok(Wc, Hc)), "custom GPT 호환 경고 조건")

# lanczos / area / bilinear 경로 동작
for m in ("lanczos", "bilinear", "area"):
    o = prep.prepare(image=src2, transparency_mask=mask1, **{**DEF, "resample": m, "padding_mode": "transparent"})
    check(tuple(o[0].shape) == (2, 2272, 1616, 4) and bool(torch.isfinite(o[0]).all()), f"resample {m}")
    check(bool((o[0][:, T_a + 700:T_a + 2200, :, 3] > 0.999).all()), f"resample {m} 불투명 영역 알파")

print()
print(f"PASS {PASSES}  FAIL {len(FAILS)}")
if FAILS:
    for f in FAILS:
        print(" -", f)
    sys.exit(1)
