# -*- coding: utf-8 -*-
r"""Design Patch 통합 테스트 (M1_SPEC §6, M2_SPEC §6): 보조 모듈 단위 테스트 + 참조 데이터 회귀 + 노드 통합.

실행 (ComfyUI-Easy-Install 루트에서):
    python_embeded\python.exe -X utf8 -W ignore ComfyUI\custom_nodes\ComfyUI_BMK_Nodes\tools\test_bmk_design_patch.py [--quick]

단위(합성 데이터, --quick 도 실행)
  S  store: 프로젝트 이름, 매니페스트 원자 저장, 내용 주소 저장, crop_id, 레이어명 파싱, 용어집, 레퍼런스 매칭,
     GPT 출력 크기(명세 검증표 + 전수/무작위), cell_key
  P  prompt: 렌더러 바이트 일치(rendered/*.txt), 시제품 대조, 변형, 평탄화, spec_from_manifest
  A  analysis: 배치, Lab, 정합 부호·가드·affine·exclude_mask, 색 맞춤, ΔE, 자동 마스크(합성 원), 톤 보정장, 게이트
  U  psd: quad 도우미, SO 캐시, PSDWriter 왕복(픽셀/그룹/마스크/SO/한글명/숨김/라벨, lnk2 꼬리 117B, 원시 검사 0)
  M2-S store 트랜잭션·병합·잠금·레지스트리·가격표 / M2-R runner(mock) / M2-B Review Board 라우트·썸네일
회귀·노드(--quick 이면 생략. comfy 는 CPU 로 import)
  M2-R C* runner ↔ comfy_api_nodes(내장 GPT 노드와 sync_op 인자 동등성 등, 가짜 sync_op — 유료 호출 없음)
  M2-N 노드(합성 프로젝트, mock): Prepare calls_per_cell·승인 해시, Run 드라이런/승인/재실행 0 호출/n 변경 재승인/wave
       이벤트·이어가기/인터럽트, Review, rejects 제외, _commit 병합·충돌 보고
  참조 폴더 H:\BmkNodeDesign\MultiLayerCropEdit 가 있을 때:
  R1 03 rect 22/22 · R2 크롭 소스 ≥15/19 비트 일치 · R3 05 harvest · R4 SO 배치 MAE ≤0.25 · RW 풀캔버스 SO PSD · RX 템플릿 상수
  R5 정합 기대값 · R6 자동 마스크 IoU · R7 톤 홀드아웃 · G 게이트   (05 PSD 를 psd 모듈로 읽어 입력 구성)
  N  노드: import(상대/단독)·규약·__init__ 등록, Project → Import PSD(03) → Candidate In(05) → Prepare → Analyze,
     Q  정합 quad vs 05 사용자 quad(001 / 007-왼쪽 / 030 코너 오차 px)
     R9 Compose(mask=hand, 05 quad) vs 05 psd.topil() 작업영역 MAE(목표 ≤1.5, 크롭별 표)
     R8 Export PSD 재열기(SO 개수, 임베드 크기, quad, 그룹 마스크 bbox, 꼬리) + 멱등(재실행 rev 불변) + 폴더 후보
  M2-C 노드 체인(실제 로더 nodes.load_custom_node + execution.PromptExecutor, 서버 포트 없음, mock):
     Project → Import(03) → Prepare → Run(드라이런 0 호출 → 승인 N 호출 → 재실행 0 호출) → Analyze → Review →
     보드 pick(라우트) → Compose → Export, 그리고 Run·Analyze 실행 중 보드 쓰기 경합(잃은 갱신 0)
출력물은 H:\BmkNodeDesign\MultiLayerCropEdit\_proto\m2_test_out\ 아래에만 쓴다(M1 회귀 결과를 읽기만 하는 곳은 m1_test_out).
"""
from __future__ import annotations

import ast
import asyncio
import base64
import contextlib
import copy
import hashlib
import importlib
import importlib.util
import inspect
import io
import json
import logging
import math
import os
import random
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import types
import unicodedata
from pathlib import Path

import cv2
import numpy as np
from PIL import Image
from scipy import ndimage as ndi

_HERE = Path(__file__).resolve()
PKG = _HERE.parents[1]
COMFY = _HERE.parents[3]
sys.path.insert(0, str(PKG))
sys.path.insert(0, str(_HERE.parent))  # extract_design_patch_so_template

import bmk_design_patch_analysis as an  # noqa: E402
import bmk_design_patch_prompt as P  # noqa: E402
import bmk_design_patch_psd as dpsd  # noqa: E402
import bmk_design_patch_store as store  # noqa: E402
from psd_tools import PSDImage  # noqa: E402
from psd_tools.constants import SheetColorType, Tag  # noqa: E402

REF = Path(r"H:\BmkNodeDesign\MultiLayerCropEdit")
OUT_ROOT = REF / "_proto" / "m2_test_out"
PSD03 = REF / "03.크롭영역+크롭이미지.psd"
PSD05 = REF / "05.디자인보정 결과.psd"
CROPS03_JSON = REF / "_proto" / "work_photoshop" / "crops_03.json"
CROP_PNG_DIR = REF / "03.크롭이미지"
GPT_DIR = REF / "04.디자인수정"
REFS_DIR = REF / "00.크롭레퍼런스"
QUICK = "--quick" in sys.argv
HAVE_REF = PSD03.exists() and PSD05.exists() and CROPS03_JSON.exists()

FAILS: list[str] = []
PASSES = 0
SKIPS: list[str] = []
MEASURED: list[str] = []


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
    except Exception as e:  # 다른 예외 형식
        check(False, f"{msg}: {exc.__name__} 가 아닌 {type(e).__name__}: {e}")
        return
    check(False, f"{msg}: 예외가 나지 않음")


def section(title):
    print(f"\n── {title}")


def skip(msg):
    SKIPS.append(msg)
    print("SKIP:", msg)


def note(msg):
    MEASURED.append(msg)
    print("  ", msg)


def png_bytes(arr) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, "PNG")
    return buf.getvalue()


def texture(h, w, seed):
    """여러 주파수 색 잡음 + 어두운 선(선화 흉내). uint8 RGB."""
    rng = np.random.default_rng(seed)
    img = np.zeros((h, w, 3), np.float32)
    for s, a in ((2.0, 0.35), (6.0, 0.4), (20.0, 0.5)):
        n = cv2.GaussianBlur(rng.random((h, w, 3)).astype(np.float32), (0, 0), s)
        n = (n - n.min()) / (n.max() - n.min() + 1e-6)
        img += a * n
    img = (img / img.max() * 220 + 20).astype(np.uint8)
    for _ in range(25):
        p0 = tuple(int(v) for v in rng.integers(0, [w, h]))
        p1 = tuple(int(v) for v in rng.integers(0, [w, h]))
        cv2.line(img, p0, p1, (20, 15, 30), 2, cv2.LINE_AA)
    return img


def mae(a, b, m=None):
    d = np.abs(np.asarray(a, np.float32) - np.asarray(b, np.float32))
    return float(d[m].mean() if m is not None else d.mean())


def corner_err(Ma, Mb, w, h):
    pts = np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]], np.float64)
    pa = pts @ np.asarray(Ma)[:, :2].T + np.asarray(Ma)[:, 2]
    pb = pts @ np.asarray(Mb)[:, :2].T + np.asarray(Mb)[:, 2]
    return float(np.hypot(*(pa - pb).T).max())


def center_disp(M, w, h):
    c = np.array([(w - 1) / 2, (h - 1) / 2])
    return np.asarray(M)[:, :2] @ c + np.asarray(M)[:, 2] - c


def crop_window(arr, left, top, bg, rect):
    """창 마스크(arr @ left,top, 밖 = bg)를 rect 로 잘라 float32 0..1."""
    x0, y0, x1, y1 = rect
    out = np.full((y1 - y0, x1 - x0), int(bg), np.uint8)
    ix0, iy0 = max(x0, left), max(y0, top)
    ix1, iy1 = min(x1, left + arr.shape[1]), min(y1, top + arr.shape[0])
    if ix1 > ix0 and iy1 > iy0:
        out[iy0 - y0:iy1 - y0, ix0 - x0:ix1 - x0] = arr[iy0 - top:iy1 - top, ix0 - left:ix1 - left]
    return out.astype(np.float32) / 255.0


_PSD_CACHE: dict = {}


def psd_cached(path):
    key = str(path)
    if key not in _PSD_CACHE:
        _PSD_CACHE[key] = dpsd.open_psd(key)
    return _PSD_CACHE[key]


# ═════════════════════════════════════════════════════════════════
# S. store 단위 (매니페스트·내용 주소·ID·이름 파싱·용어집·레퍼런스·출력 크기·cell_key)
# ═════════════════════════════════════════════════════════════════
def store_unit():
    CROPS_JSON = CROPS03_JSON
    OUT = OUT_ROOT / "_store_unit"
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)

    # ─────────────────────────────────────────────────────────────────
    # 0. 의존성: torch / comfy / folder_paths 를 끌어오지 않음
    # ─────────────────────────────────────────────────────────────────
    section("0. import 의존성")
    for banned in ("torch", "comfy", "folder_paths"):
        check(banned not in sys.modules, f"store import 가 {banned} 를 불러옴")
    check(store._TAG == "[ComfyUI_BMK_Nodes::DesignPatch]", "_TAG")
    check(store.SCHEMA == "bmk.design_patch/1", "SCHEMA")

    # ─────────────────────────────────────────────────────────────────
    # 1. project_root
    # ─────────────────────────────────────────────────────────────────
    section("1. project_root")
    base = str(OUT / "base")
    r = store.project_root(base, "jinsoi_A")
    check(r == os.path.normpath(os.path.join(base, "bmk_design_patch", "jinsoi_A")), f"project_root 경로 {r}")
    check(os.path.isabs(r), "project_root 절대 경로")
    check(not os.path.exists(r), "project_root 는 폴더를 만들지 않음")
    for ok_name in ("진소이_A", "proj-1.v2", "a", "Abc_한글-1.0"):
        check(store.project_root(base, ok_name).endswith(ok_name), f"허용 이름 {ok_name}")
    for bad in ("", "  ", "a/b", "a\\b", "..", "a..b", ".", "a b", "con", "NUL.txt", "abc.", "x:y", "a*b", None, 3):
        expect_raises(lambda b=bad: store.project_root(base, b), f"금지 이름 {bad!r}")
    expect_raises(lambda: store.project_root("", "abc"), "빈 base_dir")

    # ─────────────────────────────────────────────────────────────────
    # 2. 매니페스트
    # ─────────────────────────────────────────────────────────────────
    section("2. 매니페스트")
    SPEC_KEYS = {"schema", "project", "rev", "canvas", "work_rect", "source", "base", "crops",
                 "candidates", "analysis", "picks", "jobs", "exports"}
    m = store.new_manifest("jinsoi_A")
    check(set(m) == SPEC_KEYS, f"new_manifest 키 {sorted(set(m) ^ SPEC_KEYS)}")
    check(m["schema"] == "bmk.design_patch/1" and m["project"] == "jinsoi_A" and m["rev"] == 0, "new_manifest 기본값")
    check(set(m["source"]) == {"psd", "fingerprint", "crop_group", "clean_plate_layer", "base_layer", "crop_pixels_group"},
          "new_manifest source 키")
    check(store.new_manifest("jinsoi_A") is not m and store.new_manifest("x")["crops"] is not m["crops"], "골격은 매번 새 객체")
    expect_raises(lambda: store.new_manifest("a/b"), "new_manifest 이름 검증")

    root = store.project_root(base, "jinsoi_A")
    m0 = store.load_manifest(root)
    check(m0 == store.new_manifest("jinsoi_A"), "없는 매니페스트 → new_manifest(폴더명)")
    check(not os.path.exists(root), "load_manifest 는 폴더를 만들지 않음")

    m0["canvas"] = [3584, 4608]
    m0["work_rect"] = (256, 256, 3328, 4352)
    m0["crops"].append({"id": "002_1a2b3c", "name": "002_토스트-머리장식_right", "rect": [2204, 1292, 2716, 1804],
                        "alpha_frac": np.float32(0.25), "n": np.int64(7), "arr": np.array([1, 2, 3], dtype=np.uint16),
                        "nan": float("nan"), "inf": np.float64("inf"), "ok": np.bool_(True), "path": Path("a/b")})
    m0["extra_future_key"] = {"keep": "me"}
    rev1 = store.save_manifest(root, m0)
    check(rev1 == 1 and m0["rev"] == 1, f"save_manifest rev 1 → {rev1}")
    mp = os.path.join(root, "design_patch.json")
    check(os.path.isfile(mp), "design_patch.json 생성")
    raw = open(mp, "rb").read().decode("utf-8")
    check("토스트" in raw, "한글이 \\u 이스케이프 없이 저장")
    check("NaN" not in raw and "Infinity" not in raw, "NaN/inf 는 null 로")
    m1 = store.load_manifest(root)
    c0 = m1["crops"][0]
    check(m1["rev"] == 1 and m1["work_rect"] == [256, 256, 3328, 4352], "튜플 → 리스트 왕복")
    check(c0["alpha_frac"] == 0.25 and c0["n"] == 7 and c0["arr"] == [1, 2, 3] and c0["ok"] is True, "numpy 값 왕복")
    check(c0["nan"] is None and c0["inf"] is None and c0["path"] == "a/b", "NaN/inf/Path 변환")
    check(m1["extra_future_key"] == {"keep": "me"}, "추가 키 보존")
    rev2 = store.save_manifest(root, m1)
    rev3 = store.save_manifest(root, m1)
    check((rev2, rev3) == (2, 3) and store.load_manifest(root)["rev"] == 3, "rev 연속 증가")
    left = [f for f in os.listdir(root) if f.endswith(".tmp")]
    check(not left, f"임시 파일 잔존 {left}")
    store.save_manifest(root, store.load_manifest(root))
    check(store.save_manifest(root, store.load_manifest(root)) == 5, "load → save 반복 rev 5")
    # 저장 실패 시 rev 되돌림
    bad_m = store.load_manifest(root)
    bad_m["crops"].append({"obj": object()})
    expect_raises(lambda: store.save_manifest(root, bad_m), "직렬화 불가 값 → ValueError")
    check(bad_m["rev"] == 5 and store.load_manifest(root)["rev"] == 5, "직렬화 실패 시 rev 되돌림 + 디스크 불변")
    # 손상/스키마 오류
    root_bad = store.project_root(base, "broken")
    os.makedirs(root_bad)
    with open(os.path.join(root_bad, "design_patch.json"), "w", encoding="utf-8") as f:
        f.write("{not json")
    expect_raises(lambda: store.load_manifest(root_bad), "손상 JSON → ValueError", contains="JSON")
    with open(os.path.join(root_bad, "design_patch.json"), "w", encoding="utf-8") as f:
        json.dump({"schema": "other/1", "rev": 1}, f)
    expect_raises(lambda: store.load_manifest(root_bad), "schema 불일치 → ValueError", contains="schema")
    with open(os.path.join(root_bad, "design_patch.json"), "w", encoding="utf-8-sig") as f:  # BOM + 빠진 키
        json.dump({"schema": "bmk.design_patch/1", "project": "broken", "rev": 2, "crops": None}, f)
    mb = store.load_manifest(root_bad)
    check(set(mb) >= SPEC_KEYS and mb["crops"] == [] and mb["rev"] == 2, "BOM 허용 + 빠진 키 채움")
    expect_raises(lambda: store.save_manifest(root_bad, {"rev": 0}), "schema 없는 dict 저장 거부")

    # ─────────────────────────────────────────────────────────────────
    # 3. 내용 주소 저장
    # ─────────────────────────────────────────────────────────────────
    section("3. 내용 주소 저장")
    data = b"\x89PNG fake candidate bytes \x00\x01" * 100
    sha12 = hashlib.sha256(data).hexdigest()[:12]
    rel = store.save_bytes_ca(root, "cands", data, "png", prefix="h_")
    check(rel == f"cands/h_{sha12}.png", f"save_bytes_ca relpath {rel}")
    p = store.resolve_path(root, rel)
    check(open(p, "rb").read() == data, "save_bytes_ca 바이트 그대로")
    mt = os.stat(p).st_mtime_ns
    time.sleep(0.02)
    check(store.save_bytes_ca(root, "cands", data, ".PNG", prefix="h_") == rel, "확장자 정규화(.PNG → png)")
    check(os.stat(p).st_mtime_ns == mt, "이미 있으면 쓰지 않음(mtime 불변)")
    check(store.save_bytes_ca(root, "cands", data, "jpeg") == f"cands/{sha12}.jpg", "jpeg → jpg, prefix 없음")
    check(store.save_bytes_ca(root, "derived/thumbs", bytearray(b"abc"), "bin").startswith("derived/thumbs/"), "중첩 subdir")
    with open(p, "wb") as f:  # 깨진(잘린) 파일 → 다시 씀
        f.write(data[:10])
    store.save_bytes_ca(root, "cands", data, "png", prefix="h_")
    check(open(p, "rb").read() == data, "크기 불일치 파일은 다시 씀")
    for bad_sub in ("../x", "/abs", "C:/x", "a/../b", "한글", "", "a b"):
        expect_raises(lambda s=bad_sub: store.save_bytes_ca(root, s, data, "png"), f"subdir 거부 {bad_sub!r}")
    expect_raises(lambda: store.save_bytes_ca(root, "cands", data, "p/ng"), "ext 거부")
    expect_raises(lambda: store.save_bytes_ca(root, "cands", data, "png", prefix="한"), "prefix 거부")
    expect_raises(lambda: store.save_bytes_ca(root, "cands", "str", "png"), "str data 거부")
    expect_raises(lambda: store.resolve_path(root, "../design_patch.json"), "resolve_path .. 거부")
    check(store.resolve_path(root, "inputs\\a.png") == os.path.join(root, "inputs", "a.png"), "resolve_path 역슬래시 허용")

    rng = np.random.default_rng(7)
    rgb = rng.integers(0, 256, (37, 53, 3), dtype=np.uint8)
    relp = store.save_png_ca(root, "inputs", rgb)
    check(relp.startswith("inputs/") and relp.endswith(".png") and len(Path(relp).stem) == 12, f"save_png_ca relpath {relp}")
    check(Path(relp).stem == store.pixel_sha(rgb)[:12], "save_png_ca 파일명 = pixel_sha 앞 12자")
    back = np.asarray(Image.open(store.resolve_path(root, relp)))
    check(back.shape == rgb.shape and np.array_equal(back, rgb), "save_png_ca RGB 무손실 왕복")
    pp = store.resolve_path(root, relp)
    mt = os.stat(pp).st_mtime_ns
    time.sleep(0.02)
    check(store.save_png_ca(root, "inputs", Image.fromarray(rgb)) == relp, "PIL 입력 = 같은 배열 → 같은 경로")
    check(os.stat(pp).st_mtime_ns == mt, "save_png_ca 이미 있으면 쓰지 않음")
    check(store.save_png_ca(root, "inputs", rgb, prefix="c_") == f"inputs/c_{Path(relp).stem}.png", "save_png_ca prefix")
    rgba = np.dstack([rgb, rng.integers(0, 256, (37, 53), dtype=np.uint8)])
    rel_a = store.save_png_ca(root, "inputs", rgba)
    ia = Image.open(store.resolve_path(root, rel_a))
    check(ia.mode == "RGBA" and np.array_equal(np.asarray(ia), rgba), "RGBA 왕복")
    check(rel_a != relp, "RGB 와 RGBA 는 다른 파일")
    mask = np.zeros((20, 30), np.float32)
    mask[5:15, 10:20] = 1.0
    mask[0, 0] = 0.5
    rel_m = store.save_png_ca(root, "masks", mask)
    im = Image.open(store.resolve_path(root, rel_m))
    am = np.asarray(im)
    check(im.mode == "L" and am[10, 15] == 255 and am[0, 1] == 0 and am[0, 0] == 128, "float 마스크 → L (0.5 → 128)")
    check(store.pixel_sha(mask) == store.pixel_sha((mask * 255 + 0.5).astype(np.uint8)), "float/uint8 같은 픽셀 → 같은 sha")
    check(store.pixel_sha(rgb) != store.pixel_sha(rgb[:, :, ::-1].copy()), "다른 픽셀 → 다른 sha")
    check(store.pixel_sha(np.zeros((4, 6), np.uint8)) != store.pixel_sha(np.zeros((6, 4), np.uint8)), "크기 다르면 다른 sha")
    check(store.pixel_sha(np.ones((4, 4), bool)) == store.pixel_sha(np.full((4, 4), 255, np.uint8)), "bool 마스크")
    check(store.pixel_sha(rgb[:, :, :1]) == store.pixel_sha(rgb[:, :, 0]), "HxWx1 = HxW")
    expect_raises(lambda: store.save_png_ca(root, "inputs", np.zeros((4, 4, 5), np.uint8)), "채널 5 거부")
    expect_raises(lambda: store.save_png_ca(root, "inputs", np.zeros((4, 4), np.uint16)), "uint16 거부")
    expect_raises(lambda: store.save_png_ca(root, "inputs", "x"), "비이미지 거부")
    check(not [f for f in os.listdir(os.path.join(root, "inputs")) if f.endswith(".tmp")], "inputs 임시 파일 없음")

    check(store.file_fingerprint(mp) == [os.stat(mp).st_mtime_ns, os.stat(mp).st_size], "file_fingerprint")
    expect_raises(lambda: store.file_fingerprint(os.path.join(root, "nope.psd")), "file_fingerprint 없는 파일")
    check(store.load_spec_override(root, "002_1a2b3c") == {}, "스펙 오버라이드 없음 → {}")
    store.write_json_atomic(os.path.join(root, "specs", "002_1a2b3c.json"), {"style_note": "warm"})
    check(store.load_spec_override(root, "002_1a2b3c") == {"style_note": "warm"}, "스펙 오버라이드 읽기")
    expect_raises(lambda: store.load_spec_override(root, "../x"), "crop_id 경로 주입 거부")

    # ─────────────────────────────────────────────────────────────────
    # 4. crop_id_for
    # ─────────────────────────────────────────────────────────────────
    section("4. crop_id_for")
    crops03 = json.loads(CROPS_JSON.read_text(encoding="utf-8"))["crops"] if CROPS_JSON.exists() else []
    check(len(crops03) == 22, f"crops_03.json 22개 → {len(crops03)}")
    names03 = [c["name"] for c in crops03]
    nm = "002_토스트-머리장식_right"
    cid = store.crop_id_for(nm)
    exp = "002_" + hashlib.blake2b(nm.encode("utf-8"), digest_size=3).hexdigest()
    check(cid == exp, f"crop_id_for 값 {cid} != {exp}")
    check(store.crop_id_for(nm + " | A: toast hair clip; B: red hairpin bar") == cid, "'|' 뒤 문구는 ID 에 영향 없음")
    check(store.crop_id_for("  " + nm + "  ") == cid, "앞뒤 공백 무시")
    import unicodedata  # noqa: E402
    check(store.crop_id_for(unicodedata.normalize("NFD", nm)) == cid, "NFD 이름도 같은 ID(NFC 정규화)")
    check(store.crop_id_for("바보털").startswith("x_") and len(store.crop_id_for("바보털")) == 8, "숫자 없음 → x_<6hex>")
    check(store.crop_id_for("rotate001_바보털").startswith("x_"), "앞자리 숫자만 NNN")
    check(store.crop_id_for("００２_토스트").startswith("002_"), "전각 숫자 → ASCII")
    ids03 = [store.crop_id_for(n) for n in names03]
    import re  # noqa: E402
    check(all(re.fullmatch(r"\d{3}_[0-9a-f]{6}", i) and i.isascii() for i in ids03), "22개 ID 형식 NNN_6hex, ASCII")
    check(len(set(ids03)) == len(ids03), "22개 ID 고유")
    check(ids03 == [store.crop_id_for(n) for n in names03], "결정적")
    check(store.assign_crop_ids(["017_팔리본", "017_팔리본", "001_a", "017_팔리본"])
          == [store.crop_id_for("017_팔리본"), store.crop_id_for("017_팔리본") + "-2", store.crop_id_for("001_a"),
              store.crop_id_for("017_팔리본") + "-3"], "assign_crop_ids 중복 접미")
    expect_raises(lambda: store.crop_id_for(""), "빈 이름")
    expect_raises(lambda: store.crop_id_for(" | A: x"), "'|' 앞이 빈 이름")
    expect_raises(lambda: store.crop_id_for(None), "None 이름")

    # ─────────────────────────────────────────────────────────────────
    # 5. parse_crop_layer_name
    # ─────────────────────────────────────────────────────────────────
    section("5. parse_crop_layer_name")
    P = store.parse_crop_layer_name
    r = P("002_토스트-머리장식_right | A: toast hair clip; B: red hairpin bar")
    check(r["name"] == "002_토스트-머리장식_right" and r["nnn"] == "002" and r["part"] == "토스트-머리장식_right",
          f"명세 예시 name/nnn/part {r}")
    check(r["targets"] == [{"tid": "A", "text": "toast hair clip"}, {"tid": "B", "text": "red hairpin bar"}],
          f"명세 예시 타깃 {r['targets']}")
    check(r["warnings"] == [], "명세 예시 경고 없음")
    # 레거시 22개
    exp_part = {c["name"]: c["name"].split("_", 1)[1] for c in crops03}
    legacy_ok = 0
    for n in names03:
        q = P(n)
        good = (q["name"] == n and q["nnn"] == n[:3] and q["part"] == exp_part[n]
                and q["targets"] == [{"tid": "A", "text": ""}] and not q["warnings"])
        legacy_ok += good
        check(good, f"레거시 {n} → {q}")
    print(f"  레거시 이름 22개 파싱 일치: {legacy_ok}/{len(names03)}")
    # 관용 구분자
    VAR = [
        "002_토스트-머리장식_right｜A：toast hair clip；B：red hairpin bar",          # 전각
        "002_토스트-머리장식_right|A:toast hair clip;B:red hairpin bar",              # 공백 없음
        "  002_토스트-머리장식_right  |   A :  toast hair clip ;  B:red hairpin bar ;  ",  # 공백 과다 + 끝 ;
        "002_토스트-머리장식_right | a: toast hair clip; b: red hairpin bar",          # 소문자 라벨
        "002_토스트-머리장식_right | A: toast hair clip | B: red hairpin bar",         # | 반복
        "002_토스트-머리장식_right | A: toast hair clip, B: red hairpin bar",          # 쉼표 + 라벨
        "002_토스트-머리장식_right\u3000|\u3000A: toast hair clip;\u3000B: red hairpin bar",  # 전각 공백
        "002_토스트-머리장식_right │ A: toast hair clip; B: red hairpin bar",         # 상자 그리기 │
    ]
    for v in VAR:
        q = P(v)
        check(q["name"] == "002_토스트-머리장식_right" and q["nnn"] == "002"
              and q["targets"] == r["targets"], f"관용 구분자 {v!r} → {q}")
    q = P("010_손가락 | A: bandage on ring finger, white with red cross; B: plaster")
    check([t["text"] for t in q["targets"]] == ["bandage on ring finger, white with red cross", "plaster"],
          "쉼표 뒤가 라벨이 아니면 문구 유지")
    q = P("002_토스트 | toast hair clip; B: red pin")
    check(q["targets"] == [{"tid": "A", "text": "toast hair clip"}, {"tid": "B", "text": "red pin"}] and len(q["warnings"]) == 1,
          f"라벨 없는 조각 → 남은 글자 배정 + 경고 {q}")
    q = P("002_토스트 | B: red pin; toast clip")
    check([t["tid"] for t in q["targets"]] == ["B", "A"], "명시 라벨 B 를 피해 A 배정")
    q = P("002_토스트 |")
    check(q["targets"] == [{"tid": "A", "text": ""}] and q["warnings"], "'|' 뒤 빈 경우 → A + 경고")
    q = P("002_토스트 | A: ; B: pin")
    check(q["targets"] == [{"tid": "A", "text": ""}, {"tid": "B", "text": "pin"}], "빈 문구 타깃 허용")
    q = P("002_토스트 | Ａ: toast")
    check(q["targets"] == [{"tid": "A", "text": "toast"}], "전각 라벨 Ａ")
    q = P("002_토스트 | A: ratio 1:2 shape")
    check(q["targets"] == [{"tid": "A", "text": "ratio 1:2 shape"}], "문구 안의 콜론 유지")
    q = P("바보털")
    check(q["nnn"] == "" and q["part"] == "바보털" and q["warnings"], "NNN 없음 → 경고")
    q = P("002 토스트")
    check(q["nnn"] == "002" and q["part"] == "토스트", "NNN 뒤 공백 구분")
    check(P("002_토스트 | A: x")["name"] == P("002_토스트")["name"], "이름은 '|' 앞")
    expect_raises(lambda: P("002_x | A: one; A: two"), "중복 라벨 → ValueError", contains="두 번")
    expect_raises(lambda: P(""), "빈 레이어 이름")
    expect_raises(lambda: P("| A: x"), "크롭 부분 빈 이름")

    # ─────────────────────────────────────────────────────────────────
    # 6. 용어집
    # ─────────────────────────────────────────────────────────────────
    section("6. 용어집")
    gdir = OUT / "gloss"
    gdir.mkdir()
    g1 = gdir / "flat.json"
    g1.write_text(json.dumps({"_comment": "진소이", "토스트": "toast hair clip", "머리장식": "hair ornament",
                              "토스트-머리장식": "toast-shaped hair clip", "펜": "pen", "펜이름표": "pen name tag",
                              "손가락 붕대": "finger bandage", "  ": "x"}, ensure_ascii=False), encoding="utf-8")
    G = store.load_glossary(str(g1))
    check("_comment" not in G and "  " not in G and G["토스트"] == "toast hair clip", f"flat 용어집 {G}")
    g2 = gdir / "nested.json"
    g2.write_text(json.dumps({"schema": "x", "terms": {"토스트": "toast"}}, ensure_ascii=False), encoding="utf-8")
    check(store.load_glossary(str(g2)) == {"토스트": "toast"}, "terms 중첩 형식")
    g3 = gdir / "list.json"
    g3.write_text(json.dumps([["토스트", "toast"], {"ko": "펜", "en": "pen"}], ensure_ascii=False), encoding="utf-8")
    check(store.load_glossary(str(g3)) == {"토스트": "toast", "펜": "pen"}, "목록 형식")
    check(store.load_glossary("") == {} and store.load_glossary(None) == {}, "빈 경로 → {}")
    check(store.load_glossary(str(gdir / "none.json")) == {}, "없는 파일 → {}")
    g4 = gdir / "bad.json"
    g4.write_text("{bad", encoding="utf-8")
    expect_raises(lambda: store.load_glossary(str(g4)), "손상 용어집")
    g5 = gdir / "badval.json"
    g5.write_text(json.dumps({"토스트": 3}, ensure_ascii=False), encoding="utf-8")
    expect_raises(lambda: store.load_glossary(str(g5)), "문자열 아닌 값")
    T = store.translate
    check(T("토스트", G) == "toast hair clip", "완전 일치")
    check(T("토스트-머리장식", G) == "toast-shaped hair clip", "완전 일치(긴 키)")
    check(T("토스트 머리장식", G) == "toast-shaped hair clip", "완전 일치(구분자 무시)")
    check(T("토스트-머리장식_right", G) == "toast-shaped hair clip", "부분 일치 최장")
    check(T("펜이름표", G) == "pen name tag", "완전 일치가 부분보다 우선")
    check(T("가슴펜이름표장식", G) == "pen name tag", "부분 일치 최장(펜이름표 > 펜)")
    check(T("손가락붕대-왼쪽", G) == "finger bandage", "키의 공백 무시 부분 일치")
    check(T("코르셋", G) is None, "없음 → None")
    check(T("", G) is None and T("토스트", {}) is None, "빈 입력 → None")

    # ─────────────────────────────────────────────────────────────────
    # 7. match_refs
    # ─────────────────────────────────────────────────────────────────
    section("7. match_refs")
    rdir = OUT / "refs"
    rdir.mkdir()
    for fn in ("002_b.png", "002_a_t.png", "002_c.JPG", "002_d.webp", "002_e.psd", "002_f.txt", "0021_x.png",
               "rotate002_y.png", "002_z_t2.jpeg", "002_top.png", "001_q.png", "002_k_tㄱ.png"):
        (rdir / fn).write_bytes(b"x")
    (rdir / "002_sub").mkdir()
    (rdir / "sub").mkdir()
    (rdir / "sub" / "002_in_sub.png").write_bytes(b"x")
    got = store.match_refs(str(rdir), "002")
    check(got == ["002_a_t.png", "002_k_tㄱ.png", "002_z_t2.jpeg", "002_b.png", "002_c.JPG", "002_d.webp", "002_top.png"],
          f"합성 폴더 매칭/정렬 {got}")
    check(store.match_refs(str(rdir), 2) == got, "nnn 정수 → 3자리")
    check(store.match_refs(str(rdir), "００２") == got, "전각 nnn")
    check(store.match_refs(str(rdir), "") == [] and store.match_refs("", "002") == [], "빈 입력 → []")
    check(store.match_refs(str(rdir), "999") == [], "적중 없음 → []")
    expect_raises(lambda: store.match_refs(str(rdir / "nope"), "002"), "없는 폴더 → ValueError")
    got_p = store.match_refs(str(rdir), "002", part="z")
    check(got_p[0] == "002_z_t2.jpeg" and sorted(got_p) == sorted(got), f"part 우선 정렬 {got_p}")
    check(store.is_ref_cutout("001_바보털_tㄱ.png") and store.is_ref_cutout("x_T.png")
          and not store.is_ref_cutout("002_top.png") and not store.is_ref_cutout("012_허리태슬.png"), "is_ref_cutout")

    if REFS_DIR.is_dir() and crops03:
        total_hits = 0
        with_hits = 0
        print(f"  실폴더 {REFS_DIR} (항목 {len(os.listdir(REFS_DIR))}개)")
        print(f"  {'crop':28s} {'hits':>4s} {'_t':>3s}  first(spec)                        first(part=)")
        for c in crops03:
            q = P(c["name"])
            hits = store.match_refs(str(REFS_DIR), q["nnn"])
            hp = store.match_refs(str(REFS_DIR), q["nnn"], part=q["part"])
            nt = sum(store.is_ref_cutout(h) for h in hits)
            total_hits += len(hits)
            with_hits += bool(hits)
            check(all(h.startswith(q["nnn"] + "_") and os.path.splitext(h)[1].lower() in store.REF_EXTS for h in hits),
                  f"{c['name']} 적중 형식")
            check(all(not os.path.isabs(h) and "/" not in h and "\\" not in h for h in hits), f"{c['name']} 상대 경로")
            cuts = [store.is_ref_cutout(h) for h in hits]
            check(cuts == sorted(cuts, reverse=True), f"{c['name']} _t 우선")
            check(sorted(hp) == sorted(hits), f"{c['name']} part 정렬은 순서만 바꿈")
            print(f"  {c['name']:28s} {len(hits):4d} {nt:3d}  {(hits[0] if hits else '-'):34s} {hp[0] if hp else '-'}")
        print(f"  적중 크롭 {with_hits}/{len(crops03)}, 총 적중 {total_hits}")
        h1 = store.match_refs(str(REFS_DIR), "001")
        check(h1 == ["001_바보털_t.png", "001_바보털_tㄱ.png", "001_바보털.png"], f"001 실폴더 {h1}")
        h2 = store.match_refs(str(REFS_DIR), "002", part="토스트-머리장식_right")
        check(h2[0] == "002_토스트-머리장식_right_t.png" and len(h2) == 7, f"002 right part 우선 {h2}")
        h4 = store.match_refs(str(REFS_DIR), "004")
        check("004_가슴팔각단추-단일_t.psd" not in h4 and len(h4) == 3, f"004 psd 제외 {h4}")
        h7 = store.match_refs(str(REFS_DIR), "007", part="머리장식-오른쪽_펜")
        check(h7[0] == "007_머리장식-오른쪽펜.png", f"007 오른쪽_펜 part 정규화 매칭 {h7}")
        h7l = store.match_refs(str(REFS_DIR), "007", part="머리장식-왼쪽")
        check(h7l[:2] == ["007_머리장식-왼쪽.png", "007_머리장식-왼쪽-펜.png"], f"007 왼쪽 part 완전 일치 우선 {h7l}")
        h1p = store.match_refs(str(REFS_DIR), "001", part="바보털")
        check(h1p == h1, f"001 part 순서 = 기본 순서(_t 표지 앞 비교) {h1p}")
    else:
        print("  (레퍼런스 폴더 없음 — 실폴더 매칭 생략)")

    # ─────────────────────────────────────────────────────────────────
    # 8. gpt_output_size
    # ─────────────────────────────────────────────────────────────────
    section("8. gpt_output_size")
    G2 = store.gpt_output_size
    OK = store.gpt_image_custom_size_ok
    TABLE_USER_K = [
        ((512, 512), (2048, 2048)), ((384, 384), (2048, 2048)), ((256, 256), (2048, 2048)), ((768, 768), (2048, 2048)),
        ((512, 768), (1024, 1536)), ((512, 1024), (1024, 2048)), ((512, 1280), (1024, 2560)), ((256, 640), (1024, 2560)),
        ((768, 1280), (1536, 2560)), ((1024, 512), (2048, 1024)), ((1280, 768), (2560, 1536)), ((640, 768), (1280, 1536)),
        ((384, 512), (1152, 1536)),
    ]
    table_ok = 0
    for (w, h), exp_wh in TABLE_USER_K:
        W, H, info = G2(w, h)
        good = (W, H) == exp_wh and OK(W, H) and info["aspect_err"] == 0.0
        table_ok += good
        check(good, f"user_k {w}x{h} → {W}x{H} (기대 {exp_wh[0]}x{exp_wh[1]}) {info}")
    print(f"  명세 검증표(user_k) {table_ok}/{len(TABLE_USER_K)} 일치")
    check(G2(384, 512)[2]["method"] == "int_k" and G2(384, 512)[2]["k"] == 3.0, "384x512 → 정수 k'=3")
    check(G2(512, 768)[2]["method"] == "k" and G2(512, 768)[2]["k_str"] == "2", "512x768 → k=2 그대로")
    check(G2(320, 480)[:2] == (1024, 1536) and G2(320, 480)[2]["k_str"] == "16/5", "비정수 k 라도 16배수 정수면 그대로(320x480)")
    check(G2(512, 512)[2]["method"] == "square_preset", "정사각 프리셋")
    # int_k_2560
    TABLE_INT = [((512, 512), (2560, 2560)), ((768, 1280), (1536, 2560)), ((384, 512), (1920, 2560)),
                 ((384, 384), (2304, 2304)), ((1024, 1024), (2048, 2048)), ((256, 640), (1024, 2560)),
                 ((1280, 768), (2560, 1536)), ((1536, 1536), (1536, 1536)), ((128, 384), (768, 2304))]
    for (w, h), exp_wh in TABLE_INT:
        W, H, info = G2(w, h, "int_k_2560")
        check((W, H) == exp_wh and OK(W, H), f"int_k_2560 {w}x{h} → {W}x{H} (기대 {exp_wh}) {info}")
    # 폴백
    W, H, info = G2(520, 780)
    check((W, H) == (1040, 1552) and info["method"] == "floor16" and info["aspect_err"] != 0.0 and OK(W, H),
          f"16배수 아님 → 16배수 내림 + aspect_err {W}x{H} {info}")
    W, H, info = G2(2000, 3000)
    check(info["method"] == "floor16" and max(W, H) <= 3840 and W * H <= 8_294_400 and OK(W, H) and info["aspect_err"] != 0,
          f"상한 초과 + 16배수 아님 → floor16 {W}x{H} {info}")
    W, H, info = G2(256, 1024)
    check(info["method"] == "nearest" and OK(W, H) and abs(W / H - 1 / 3) < 1e-9, f"비 4:1 → 3:1 로 클램프 {W}x{H} {info}")
    W, H, info = G2(3000, 4000)
    check(OK(W, H) and abs(info["aspect_err"]) < 0.01, f"큰 원본 3000x4000 → {W}x{H} {info}")
    W, H, info = G2(1024, 3072)
    check((W, H) == (1024, 3072) and info["method"] == "int_k" and info["aspect_err"] == 0.0, f"1024x3072 → k' 감소 1 {W}x{H}")
    check(G2(4096, 4096)[:2] == (2048, 2048), "큰 정사각 → 2048²")
    for bad in ((0, 512), (-1, 512), (512.5, 512), ("512", 512), (True, 512), (None, 512)):
        expect_raises(lambda b=bad: G2(*b), f"잘못된 크기 {bad}")
    expect_raises(lambda: G2(512, 512, "max_k"), "알 수 없는 규칙")
    check(G2(np.int64(512), 768.0)[:2] == (1024, 1536), "numpy/float 정수값 허용")

    # 전수: 128 배수 128..4096 두 축 × 두 규칙 → 항상 유효
    t0 = time.time()
    grid = range(128, 4097, 128)
    bad_ok = []
    exact_dom = []
    for rule in store.GPT_SIZE_RULES:
        for w in grid:
            for h in grid:
                W, H, info = G2(w, h, rule)
                if not OK(W, H):
                    bad_ok.append((rule, w, h, W, H))
                # 크롭 실사용 영역(변 ≤1280, 비 ≤3): 종횡 정확
                if w <= 1280 and h <= 1280 and max(w, h) <= 3 * min(w, h):
                    good = info["aspect_err"] == 0.0 and W % 16 == 0 and H % 16 == 0
                    if rule == "user_k":
                        good = good and min(W, H) >= 1024
                    else:
                        good = good and max(W, H) <= 2560
                    if not good:
                        exact_dom.append((rule, w, h, W, H, info["method"]))
    check(not bad_ok, f"128배수 전수 중 제약 위반 {len(bad_ok)}건: {bad_ok[:5]}")
    check(not exact_dom, f"크롭 영역(≤1280, 비≤3) 종횡/짧은변 규칙 위반 {len(exact_dom)}건: {exact_dom[:5]}")
    print(f"  128배수 전수 {2 * len(grid) ** 2}건 제약 위반 {len(bad_ok)}, 크롭영역 규칙 위반 {len(exact_dom)} ({time.time() - t0:.2f}s)")
    # 무작위 임의 정수
    t0 = time.time()
    rnd = random.Random(1234)
    bad_rand = []
    methods = {}
    for _ in range(4000):
        w = rnd.randint(1, 8000)
        h = rnd.choice([w, rnd.randint(1, 8000), rnd.randint(max(1, w // 3), w * 3)])
        for rule in store.GPT_SIZE_RULES:
            W, H, info = G2(w, h, rule)
            methods[info["method"]] = methods.get(info["method"], 0) + 1
            if not OK(W, H) or not isinstance(W, int) or not isinstance(H, int):
                bad_rand.append((rule, w, h, W, H))
    check(not bad_rand, f"무작위 8000건 제약 위반 {len(bad_rand)}: {bad_rand[:5]}")
    print(f"  무작위 8000건 제약 위반 {len(bad_rand)}, method 분포 {dict(sorted(methods.items()))} ({time.time() - t0:.2f}s)")
    # canvas_snap 원본 함수와 같은 조건인지(가능하면 대조)
    try:
        import ast  # noqa: E402
        src = Path(PKG, "bmk_canvas_snap.py").read_text(encoding="utf-8")
        fn_src = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "gpt_image_custom_size_ok")
        ns: dict = {}
        exec(compile(ast.Module(body=[fn_src], type_ignores=[]), "canvas_snap_fn", "exec"), ns)
        ref_ok = ns["gpt_image_custom_size_ok"]
        diff = [(a, b) for a in range(0, 4200, 8) for b in range(0, 4200, 40) if ref_ok(a, b) != OK(a, b)]
        check(not diff, f"bmk_canvas_snap.gpt_image_custom_size_ok 와 불일치 {diff[:5]}")
    except StopIteration:
        check(False, "bmk_canvas_snap.gpt_image_custom_size_ok 를 찾지 못함")

    # ─────────────────────────────────────────────────────────────────
    # 9. cell_key
    # ─────────────────────────────────────────────────────────────────
    section("9. cell_key")
    CK = store.cell_key
    args = dict(model="gpt-image-2.5-sunburst", quality="max", size=[2048, 2048], background="opaque",
                prompt="Input images, in upload order:\n- @image1: 토스트", input_shas=["ab" * 32, "cd" * 32],
                template_version="dp-prompt/1", variant="V1")
    k0 = CK(**args)
    check(re.fullmatch(r"[0-9a-f]{24}", k0) is not None, f"24자 hex {k0}")
    check(k0 == CK(**args), "결정적")
    GOLDEN = "61b7df6e97f0c8f35ef7cfc5"  # 키 형식(CELL_KEY_VERSION) 을 바꿀 때만 갱신
    check(k0 == GOLDEN, f"골든 값 변경 {k0} != {GOLDEN}")
    print(f"  cell_key 골든 = {k0}")
    check(CK(**{**args, "size": (2048, 2048)}) == k0 and CK(**{**args, "size": "2048x2048"}) == k0
          and CK(**{**args, "size": [np.int64(2048), 2048]}) == k0, "size 표현 무관")
    check(CK(**{**args, "input_shas": ("AB" * 32, "CD" * 32)}) == k0, "sha 대소문자·튜플 무관")
    check(CK(*args.values()) == k0, "위치 인자 순서 = 명세 시그니처")
    variants = {
        "model": "gpt-image-2.5-flare", "quality": "high", "size": [2048, 1024], "background": "transparent",
        "prompt": args["prompt"] + " ", "input_shas": ["cd" * 32, "ab" * 32], "template_version": "dp-prompt/2",
        "variant": "V4",
    }
    for key, val in variants.items():
        check(CK(**{**args, key: val}) != k0, f"{key} 변경 → 다른 키")
    check(CK(**{**args, "background": None}) != k0, "background None 구분")
    check(CK(**{**args, "input_shas": ["ab" * 32]}) != k0, "입력 개수 변경 → 다른 키")
    import inspect  # noqa: E402
    check("n" not in inspect.signature(CK).parameters, "시그니처에 n 없음")
    expect_raises(lambda: CK(**{**args, "input_shas": "abcd"}), "input_shas 문자열 하나 거부")
    expect_raises(lambda: CK(**{**args, "size": [2048]}), "size 길이 거부")
    expect_raises(lambda: CK(**{**args, "size": "big"}), "size 문자열 형식 거부")
    shutil.rmtree(OUT, ignore_errors=True)


# ═════════════════════════════════════════════════════════════════
# P. prompt 단위 + 참조 렌더 일치
# ═════════════════════════════════════════════════════════════════
def prompt_unit():
    PROTO = REF / "_proto" / "work_prompts"
    RENDERED = PROTO / "rendered"
    CROPS = CROP_PNG_DIR
    REFS = REFS_DIR
    OUT = OUT_ROOT / "prompt"
    SPEC_NAMES = ["toast", "pen", "fingers", "toast_paste", "self_receipt"]
    HAVE_REF = RENDERED.is_dir() and CROPS.is_dir() and REFS.is_dir()
    if HAVE_REF:
        OUT.mkdir(parents=True, exist_ok=True)

    # ─────────────────────────────────────────────────────────────────
    # H1. 모듈 위생
    # ─────────────────────────────────────────────────────────────────
    check(P.TEMPLATE_VERSION == "dp-prompt/1", f"TEMPLATE_VERSION {P.TEMPLATE_VERSION!r}")
    check(P._TAG == "[ComfyUI_BMK_Nodes::DesignPatch]", "_TAG")
    _src = Path(P.__file__).read_text(encoding="utf-8")
    _tree = ast.parse(_src)
    _allowed = {"__future__", "copy", "json", "logging", "os", "re", "typing", "math", "hashlib",
                "numpy", "PIL", "scipy", "cv2", "psd_tools", "bmk_design_patch_store"}
    _imports = set()
    for node in ast.walk(_tree):
        if isinstance(node, ast.Import):
            _imports.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                _imports.add((node.module or "").split(".")[0])
            else:
                _imports.update(a.name for a in node.names)
    check(_imports <= _allowed, f"허용 밖 import: {sorted(_imports - _allowed)}")
    for banned in ("torch", "comfy", "folder_paths"):
        check(banned not in sys.modules, f"{banned} 가 import 됨")
    check(not any(ord(ch) >= 0x1F000 or ch == "\ufe0f" for ch in _src), "모듈 소스에 이모지")
    check(_src.lstrip().startswith('"""BMK Design Patch Prompt'), "모듈 docstring 첫 줄")
    check(isinstance(_tree.body[1], ast.ImportFrom) and _tree.body[1].module == "__future__", "docstring 다음이 from __future__")
    check(P.store.__name__.endswith("bmk_design_patch_store") and callable(P.store.translate), "store 상호 import")


    # ─────────────────────────────────────────────────────────────────
    # P1. 렌더러 바이트 일치 (rendered/*.spec.json → *.txt)
    # ─────────────────────────────────────────────────────────────────
    SPECS: dict[str, dict] = {}
    EXPECT: dict[str, tuple[str, list[str]]] = {}
    if not HAVE_REF:
        skip("참조 폴더 없음 — P1/P2/F2/F3/S1(참조) 생략")
    else:
        for name in SPEC_NAMES:
            sp = RENDERED / f"{name}.spec.json"
            tp = RENDERED / f"{name}.txt"
            spec = json.loads(sp.read_text(encoding="utf-8"))
            SPECS[name] = spec
            raw = tp.read_bytes()
            nl = "\r\n" if b"\r\n" in raw else "\n"
            text = raw.decode("utf-8").replace("\r\n", "\n")
            head, sep, tail = text.partition("\n\nIMAGES (upload order):\n")
            check(bool(sep), f"{name}.txt 에 IMAGES 구분자가 없음")
            exp_imgs = [ln.split(". ", 1)[1] for ln in tail.split("\n") if ln.strip()]
            EXPECT[name] = (head, exp_imgs)
            before = json.dumps(spec, ensure_ascii=False, sort_keys=True)
            res = P.render(spec)
            res2 = P.render(copy.deepcopy(spec))
            check(json.dumps(spec, ensure_ascii=False, sort_keys=True) == before, f"{name}: render 가 스펙을 바꿈")
            check(res["prompt"] == head, f"{name}: 프롬프트 텍스트 불일치")
            check(res["prompt"].encode("utf-8") == head.encode("utf-8"), f"{name}: 프롬프트 바이트 불일치")
            check(res["images"] == exp_imgs, f"{name}: images {res['images']} != {exp_imgs}")
            rebuilt = (res["prompt"] + "\n\nIMAGES (upload order):\n"
                       + "\n".join(f"{i + 1}. {p}" for i, p in enumerate(res["images"])))
            check(rebuilt.replace("\n", nl).encode("utf-8") == raw, f"{name}: .txt 파일 바이트 재구성 불일치")
            check(res == res2, f"{name}: 같은 스펙 두 번 렌더 결과가 다름")
            check("\r" not in res["prompt"], f"{name}: 프롬프트에 CR")
            lines = res["prompt"].split("\n")
            check(lines[1:1 + len(res["legend"])] == res["legend"], f"{name}: legend 가 프롬프트 범례 줄과 다름")
            check(len(res["legend"]) == len(res["images"]), f"{name}: legend 줄 수 != 이미지 수")
            for i in range(1, len(res["images"]) + 1):
                tok = f"Image {i}" if spec.get("ref_style") == "plain" else f"@image{i}"
                check(tok in res["prompt"], f"{name}: {tok} 가 프롬프트에 없음")
            nimg = len(res["images"])
            check(("@image%d" % (nimg + 1)) not in res["prompt"] and ("Image %d" % (nimg + 1)) not in res["prompt"],
                  f"{name}: 이미지 수를 넘는 토큰")
            note(f"P1 {name}: {len(res['prompt'].split())} words, {nimg} images, bytes={len(raw)} 일치")


    # ─────────────────────────────────────────────────────────────────
    # P2. 시제품 대조 (소스 exec, pyc 없음) + _all.txt
    # ─────────────────────────────────────────────────────────────────
    PROTO_NS = None
    if HAVE_REF and (PROTO / "gpt_edit_prompt.py").is_file():
        proto_path = PROTO / "gpt_edit_prompt.py"
        proto_src = proto_path.read_text(encoding="utf-8")
        PROTO_NS = {"__name__": "_dp_proto_gpt_edit_prompt", "__file__": str(proto_path)}
        exec(compile(proto_src, str(proto_path), "exec"), PROTO_NS)
        ptree = ast.parse(proto_src)

        def _top(tree, kind):
            out = {}
            for nd in tree.body:
                if kind == "def" and isinstance(nd, ast.FunctionDef):
                    out[nd.name] = nd
                elif kind == "assign" and isinstance(nd, ast.Assign) and isinstance(nd.targets[0], ast.Name):
                    out[nd.targets[0].id] = nd
            return out

        pf, mf = _top(ptree, "def"), _top(_tree, "def")
        pa, ma = _top(ptree, "assign"), _top(_tree, "assign")
        for fn in ("_join", "_plan_images", "render", "_target_line"):
            check(fn in mf and ast.dump(pf[fn]) == ast.dump(mf[fn]), f"이식 함수 {fn} 의 AST 가 시제품과 다름")
        for const in ("GUIDE_NOUN", "GUIDE_RULE", "SHAPE", "TEXT"):
            check(const in ma and ast.dump(pa[const]) == ast.dump(ma[const]), f"상수 {const} 가 시제품과 다름")
            check(getattr(P, const) == PROTO_NS[const], f"상수 {const} 값이 시제품과 다름")
        for name, spec in SPECS.items():
            pv = PROTO_NS["variants"](copy.deepcopy(spec))
            mv = P.variants(copy.deepcopy(spec))
            check(list(pv) == list(mv), f"{name}: 변형 이름/순서 {list(mv)} != 시제품 {list(pv)}")
            for k in pv:
                if k not in mv:
                    continue
                for key in ("prompt", "images", "legend"):
                    check(pv[k][key] == mv[k][key], f"{name}/{k}: {key} 가 시제품과 다름")
                check(P.render(mv[k]) == pv[k], f"{name}/{k}: render(variant) 가 시제품 결과와 다름")
            check(PROTO_NS["render"](copy.deepcopy(spec)) == P.render(copy.deepcopy(spec)), f"{name}: render 결과 dict 가 시제품과 다름")
        note(f"P2 시제품 대조: 함수 4개 AST 동일, 상수 4개 동일, 변형 {sum(len(P.variants(s)) for s in SPECS.values())}개 동일")
    elif HAVE_REF:
        skip("시제품 gpt_edit_prompt.py 없음 — AST/변형 대조 생략")

    if HAVE_REF and (RENDERED / "_all.txt").is_file():
        all_lines = (RENDERED / "_all.txt").read_bytes().decode("utf-8").replace("\r\n", "\n").split("\n")

        def _find(prefix, start=0):
            for i in range(start, len(all_lines)):
                if all_lines[i].startswith(prefix):
                    return i
            return -1

        for name, spec in SPECS.items():
            hi = _find(f"===== {name}:")
            check(hi >= 0, f"_all.txt 에 {name} 머리줄 없음")
            if hi < 0:
                continue
            hdr = all_lines[hi]
            words = int(hdr.split(":", 1)[1].split("words")[0].strip())
            nimg = int(hdr.split(",")[1].split("images")[0].strip())
            ii = _find("IMAGES: ", hi)
            block = "\n".join(all_lines[hi + 1:ii])
            res = P.render(spec)
            check(block == res["prompt"], f"_all.txt {name}: 프롬프트 불일치")
            check(len(res["prompt"].split()) == words and len(res["images"]) == nimg, f"_all.txt {name}: 단어/이미지 수")
            check(ast.literal_eval(all_lines[ii][len("IMAGES: "):]) == res["images"], f"_all.txt {name}: IMAGES")
            vi = _find("variants: ", ii)
            vw = ast.literal_eval(all_lines[vi][len("variants: "):])
            mv = P.variants(spec)
            check(vw == {k: len(v["prompt"].split()) for k, v in mv.items()}, f"_all.txt {name}: 변형 단어 수 {vw}")
            nxt = _find("===== ", hi + 1)
            end = nxt if nxt >= 0 else len(all_lines)
            if name == "fingers":
                ci = _find("----- V2_isolate_C -----", hi)
                cj = _find("IMAGES: ", ci)
                check(0 <= ci < end and "\n".join(all_lines[ci + 1:cj]) == mv["V2_isolate_C"]["prompt"],
                      "_all.txt fingers V2_isolate_C 프롬프트 불일치")
                check(ast.literal_eval(all_lines[cj][len("IMAGES: "):]) == mv["V2_isolate_C"]["images"],
                      "_all.txt fingers V2_isolate_C IMAGES 불일치")
            if name in ("toast", "fingers"):
                ki = _find("----- V4_checklist -----", hi)
                frag = mv["V4_checklist"]["prompt"].split("Targets (change only these):", 1)[1].split("Integration", 1)[0]
                check(0 <= ki < end and "\n".join(all_lines[ki + 1:end]) == frag, f"_all.txt {name} V4_checklist 조각 불일치")
        note("P2 _all.txt: 5개 프롬프트·단어 수·변형 단어 수, fingers V2_isolate_C, toast/fingers V4 조각 일치")


    # ─────────────────────────────────────────────────────────────────
    # P3. 변형 결정성 / select_variants / validate_spec / ref_style
    # ─────────────────────────────────────────────────────────────────
    def _mini_spec():
        return {"crop_id": "x", "canvas": "c.png", "ref_style": "at",
                "targets": [{"id": "A", "name": "toast hair clip", "mode": "ref_correct", "refs": ["r1.png"],
                             "detail": "a; b; c", "shape": "locked"},
                            {"id": "B", "name": "red bar", "mode": "ref_correct", "refs": ["r1.png", "r2.png"],
                             "detail": "thin red bar"}]}


    for name, spec in list(SPECS.items()) + [("mini", _mini_spec())]:
        before = json.dumps(spec, ensure_ascii=False, sort_keys=True)
        v1 = P.variants(spec)
        v2 = P.variants(copy.deepcopy(spec))
        check(json.dumps(v1, ensure_ascii=False, sort_keys=True) == json.dumps(v2, ensure_ascii=False, sort_keys=True),
              f"{name}: variants 비결정적")
        check(json.dumps(spec, ensure_ascii=False, sort_keys=True) == before, f"{name}: variants 가 원본 스펙을 바꿈")
        vs = P.variant_specs(spec)
        check(list(vs) == list(v1), f"{name}: variant_specs 이름 불일치")
        for k, v in v1.items():
            check(P.render(v) == {kk: v[kk] for kk in ("prompt", "images", "legend")}, f"{name}/{k}: render(variant) 불일치")
            check(P.render(vs[k]) == P.render(v), f"{name}/{k}: variant_specs 렌더 불일치")
            check(not ({"prompt", "images", "legend"} & set(vs[k])), f"{name}/{k}: variant_specs 에 렌더 키")
        check(v1["V1_standard"]["prompt"] == P.render(spec)["prompt"], f"{name}: V1 != render")
        check(list(v1)[0] == "V1_standard" and list(v1)[-1] == "V4_checklist", f"{name}: 변형 순서 {list(v1)}")

    mv = P.variants(_mini_spec())
    check(list(mv) == ["V1_standard", "V2_isolate_A", "V2_isolate_B", "V3_freer_shape", "V4_checklist"], f"mini 변형 {list(mv)}")
    check("(1) a; (2) b; (3) c" in mv["V4_checklist"]["prompt"], "V4 체크리스트 변환")
    check(mv["V2_isolate_B"]["images"] == ["c.png", "r1.png", "r2.png"], "V2_isolate_B 이미지")
    check(mv["V2_isolate_A"]["images"] == ["c.png", "r1.png"], "V2_isolate_A 이미지")
    check(" Its shape and proportions may change" in mv["V3_freer_shape"]["prompt"], "V3: moderate→free")
    check(" Change its outline only where" in mv["V3_freer_shape"]["prompt"], "V3: locked→moderate")
    sel = P.select_variants(mv, "V1,V4")
    check(list(sel) == ["V1_standard", "V4_checklist"], f"select V1,V4 → {list(sel)}")
    check(list(P.select_variants(mv, "v2")) == ["V2_isolate_A", "V2_isolate_B"], "select v2 → isolate 전부")
    check(list(P.select_variants(mv, ["V2_isolate_B", "V3"])) == ["V2_isolate_B", "V3_freer_shape"], "select 목록")
    check(list(P.select_variants(mv, "V4 V1")) == ["V1_standard", "V4_checklist"], "select 순서 = 원래 순서")
    check(P.select_variants(mv, "") == {}, "select 빈 문자열")
    check(P.select_variants(mv, "V9") == {}, "select 없는 변형")
    expect_raises(lambda: P.select_variants(mv, "X1"), "select 형식 오류 ValueError")
    self_only = {"crop_id": "s", "canvas": "c.png", "targets": [{"id": "A", "name": "receipts", "mode": "self_restore"}]}
    check(list(P.variants(self_only)) == ["V1_standard", "V4_checklist"], "self_restore 변형은 V1, V4 뿐")
    check(list(P.select_variants(P.variants(self_only), "V1,V3,V4")) == ["V1_standard", "V4_checklist"], "V3 없음 건너뜀")

    for name, spec in SPECS.items():
        probs = P.validate_spec(spec)
        check(probs == [], f"{name}: validate_spec 문제 {probs}")
    check(P.validate_spec(_mini_spec()) == [], f"mini validate {P.validate_spec(_mini_spec())}")
    bad = {"canvas": "", "ref_style": "x", "targets": [
        {"id": "A", "name": "", "mode": "ref_correct", "refs": [], "shape": "loose", "text": "?"},
        {"id": "A", "name": "b", "mode": "sketch_guide", "refs": ["r.png"], "detail": "d"},
        {"id": "C", "name": "c", "mode": "magic"}]}
    bp = P.validate_spec(bad)
    for frag in ("canvas", "ref_style", "중복", "name", "refs", "detail", "shape", "text", "guide", "mode"):
        check(any(frag in p for p in bp), f"validate_spec 가 {frag!r} 문제를 못 잡음: {bp}")
    check(P.validate_spec({"canvas": "c", "targets": []}) == ["targets 가 비어 있습니다"], "validate 빈 targets")
    check(P.validate_spec("x") == ["스펙이 dict 가 아닙니다"], "validate 비 dict")

    if "toast" in SPECS:
        s = copy.deepcopy(SPECS["toast"])
        s["ref_style"] = "both"
        r = P.render(s)
        check(r["legend"][0].startswith("- Image 1 (@image1): the canvas"), f"ref_style both 범례 {r['legend'][0]}")
        s["ref_style"] = "plain"
        r = P.render(s)
        check("@image" not in r["prompt"] and "Image 2" in r["prompt"], "ref_style plain 토큰")


    # ─────────────────────────────────────────────────────────────────
    # F1. flatten_canvas / flatten_ref — 합성 데이터
    # ─────────────────────────────────────────────────────────────────
    rng = np.random.default_rng(1234)
    img = rng.integers(0, 256, size=(4, 6, 4), dtype=np.uint8)
    img[..., 3] = 255
    img[:, 3:, 3] = 0                     # 오른쪽 3열 투명, 아래 RGB 는 쓰레기값
    img_before = img.copy()
    out, info = P.flatten_canvas(img)
    check(np.array_equal(img, img_before), "flatten_canvas 가 입력을 바꿈")
    check(out.shape == (4, 6, 3) and out.dtype == np.uint8, f"F1 모양 {out.shape} {out.dtype}")
    check(np.array_equal(out[:, :3], img[:, :3, :3]), "F1 불투명 영역 비트 보존")
    check(np.array_equal(out[:, 3:], np.repeat(img[:, 2:3, :3], 3, axis=1)), "F1 투명 열 = 가장자리 열 복제")
    check(np.array_equal(out, np.pad(img[:, :3, :3], ((0, 0), (0, 3), (0, 0)), mode="edge")), "F1 == np.pad edge")
    check(info["method"] == "edge_replicate" and info["filled_bbox"] == [3, 0, 6, 4], f"F1 info {info}")
    check(abs(info["alpha_frac"] - 0.5) < 1e-9 and abs(info["transparent_frac"] - 0.5) < 1e-9, "F1 alpha_frac")
    check(abs(info["max_fill_dist"] - 3.0) < 1e-9, f"F1 max_fill_dist {info['max_fill_dist']}")
    # 부분 알파: 자기 RGB 와 최근접 불투명 색의 알파 혼합
    img2 = img.copy()
    img2[1, 3, 3] = 128
    o2, i2 = P.flatten_canvas(img2)
    exp = (img2[1, 3, :3].astype(int) * 128 + img2[1, 2, :3].astype(int) * 127 + 127) // 255
    check(np.array_equal(o2[1, 3], exp.astype(np.uint8)), f"F1 부분 알파 혼합 {o2[1, 3]} != {exp}")
    check(abs(i2["alpha_frac"] - 0.5) < 1e-9 and abs(i2["transparent_frac"] - 11 / 24) < 1e-9, "F1 부분 알파 비율")
    check(np.array_equal(o2[1, 4], img2[1, 2, :3]), "F1 부분 알파 픽셀은 기준(seed)이 아님")
    # 내부 구멍: 최근접 이웃 중 하나
    ring = np.zeros((5, 5, 4), dtype=np.uint8)
    ring[..., :3] = rng.integers(0, 256, size=(5, 5, 3), dtype=np.uint8)
    ring[..., 3] = 255
    ring[2, 2, 3] = 0
    o3, _ = P.flatten_canvas(ring)
    nb = [tuple(ring[y, x, :3]) for y, x in ((1, 2), (3, 2), (2, 1), (2, 3))]
    check(tuple(o3[2, 2]) in nb, "F1 내부 구멍은 4-이웃 중 하나로")
    check(np.array_equal(o3, P.flatten_canvas(ring)[0]), "F1 결정성")
    # 알파 없음 / 전부 불투명 / 완전 불투명 픽셀 없음
    rgb3 = rng.integers(0, 256, size=(3, 3, 3), dtype=np.uint8)
    o4, i4 = P.flatten_canvas(rgb3)
    check(np.array_equal(o4, rgb3) and o4 is not rgb3 and i4["method"] == "none" and i4["filled_bbox"] is None, "F1 RGB 통과")
    full = np.concatenate([rgb3, np.full((3, 3, 1), 255, np.uint8)], axis=2)
    o5, i5 = P.flatten_canvas(full)
    check(np.array_equal(o5, rgb3) and i5["method"] == "none", "F1 전부 불투명")
    zero = np.zeros((3, 4, 4), np.uint8)
    expect_raises(lambda: P.flatten_canvas(zero), "F1 전부 투명 ValueError", contains="불투명")
    semi = full.copy()
    semi[..., 3] = 254
    expect_raises(lambda: P.flatten_canvas(semi), "F1 완전 불투명 픽셀 없음 ValueError")
    # float / PIL / 잘못된 입력
    o8, _ = P.flatten_canvas(img.astype(np.float32) / 255.0)
    check(np.array_equal(o8, out), "F1 float 0..1 입력 = uint8 입력")
    o9, _ = P.flatten_canvas(Image.fromarray(np.stack([img[..., 0], img[..., 3]], axis=2), "LA"))
    check(o9.shape == (4, 6, 3) and np.array_equal(o9[..., 0], o9[..., 1]), "F1 PIL LA 입력")
    o10, _ = P.flatten_canvas(Image.fromarray(img, "RGBA"))
    check(np.array_equal(o10, out), "F1 PIL RGBA 입력")
    o11, i11 = P.flatten_canvas(Image.fromarray(img[..., :3], "RGB"))
    check(np.array_equal(o11, img[..., :3]) and i11["method"] == "none", "F1 PIL RGB 입력")
    expect_raises(lambda: P.flatten_canvas(np.zeros((4, 4, 5), np.uint8)), "F1 5채널 ValueError")
    expect_raises(lambda: P.flatten_canvas(np.zeros((4, 4, 2), np.uint8)), "F1 2채널 배열 ValueError")
    expect_raises(lambda: P.flatten_canvas(np.zeros((4, 4), np.uint8)), "F1 2차원 배열 ValueError")
    expect_raises(lambda: P.flatten_canvas(np.zeros((4, 4, 4), np.uint16)), "F1 uint16 ValueError")
    expect_raises(lambda: P.flatten_canvas(np.zeros((0, 4, 4), np.uint8)), "F1 빈 이미지 ValueError")
    # flatten_ref 합성
    r1 = P.flatten_ref(img2)
    a = img2[..., 3:4].astype(int)
    expf = (img2[..., :3].astype(int) * a + 128 * (255 - a)) / 255.0
    check(r1.shape == (4, 6, 3) and np.abs(r1.astype(float) - expf).max() <= 0.5 + 1e-9, "F1 flatten_ref 반올림")
    check((r1[:, 4:] == 128).all() and np.array_equal(r1[:, :3], img2[:, :3, :3]), "F1 flatten_ref 매트/불투명")
    check((P.flatten_ref(zero, "#ff0000") == np.array([255, 0, 0], np.uint8)).all(), "F1 매트 hex")
    check((P.flatten_ref(zero, "808080") == 128).all(), "F1 매트 hex(# 없음)")
    check((P.flatten_ref(zero, [1, 2, 3]) == np.array([1, 2, 3], np.uint8)).all(), "F1 매트 목록")
    check(np.array_equal(P.flatten_ref(rgb3), rgb3), "F1 flatten_ref RGB 통과")
    expect_raises(lambda: P.flatten_ref(zero, "zzz"), "F1 매트 hex 오류")
    expect_raises(lambda: P.flatten_ref(zero, (1, 2)), "F1 매트 2채널 오류")
    expect_raises(lambda: P.flatten_ref(zero, (256, 0, 0)), "F1 매트 범위 오류")
    expect_raises(lambda: P.flatten_ref(zero, None), "F1 매트 None 오류")
    expect_raises(lambda: P.flatten_ref(zero, 7), "F1 매트 정수 오류")
    expect_raises(lambda: P.flatten_ref(zero, (1.5, 2, 3)), "F1 매트 실수 오류")
    # 큰 이미지 시간
    big = np.zeros((2048, 2048, 4), np.uint8)
    big[..., :3] = rng.integers(0, 256, size=(2048, 2048, 3), dtype=np.uint8)
    big[:, :1434, 3] = 255
    big[:600, :, 3] = 0
    t0 = time.time()
    ob, ib = P.flatten_canvas(big)
    dt = time.time() - t0
    check(dt < 10.0, f"F1 2048² 평탄화 {dt:.2f}s ≥ 10s")
    check(np.array_equal(ob[600:, :1434], big[600:, :1434, :3]), "F1 2048² 불투명 보존")
    note(f"F1 2048x2048 (투명 {ib['alpha_frac'] * 100:.1f}%) flatten_canvas {dt:.2f}s, max_fill_dist {ib['max_fill_dist']:.1f}px")
    del big, ob


    # ─────────────────────────────────────────────────────────────────
    # F2. 실제 투명 여백 크롭
    # ─────────────────────────────────────────────────────────────────
    def near_black_band(rgb, frac=0.5, thr=8):
        """가장자리에서 안쪽으로 연속된 '근흑(max 채널 < thr) 픽셀이 frac 이상'인 행/열 수 (변별 4방향)."""
        nb = rgb.max(axis=2) < thr
        rows, cols = nb.mean(axis=1) >= frac, nb.mean(axis=0) >= frac

        def run(v):
            n = 0
            for x in v:
                if not x:
                    break
                n += 1
            return n

        return {"top": run(rows), "bottom": run(rows[::-1]), "left": run(cols), "right": run(cols[::-1])}


    def edge_pad_expected(rgb, bbox):
        """투명 영역이 한 변에 붙은 직사각형 띠면 np.pad(edge) 결과, 아니면 None."""
        h, w = rgb.shape[:2]
        x0, y0, x1, y1 = bbox
        if y0 == 0 and y1 == h and x1 == w:
            return np.pad(rgb[:, :x0], ((0, 0), (0, w - x0), (0, 0)), mode="edge")
        if y0 == 0 and y1 == h and x0 == 0:
            return np.pad(rgb[:, x1:], ((0, 0), (x1, 0), (0, 0)), mode="edge")
        if x0 == 0 and x1 == w and y0 == 0:
            return np.pad(rgb[y1:], ((y1, 0), (0, 0), (0, 0)), mode="edge")
        if x0 == 0 and x1 == w and y1 == h:
            return np.pad(rgb[:y0], ((0, h - y0), (0, 0), (0, 0)), mode="edge")
        return None


    if HAVE_REF:
        for fname, exp_frac, side in (("007_머리장식-오른쪽.png", 0.1836, "right"), ("001_바보털.png", 0.0833, "top")):
            path = CROPS / fname
            if not path.is_file():
                skip(f"F2 {fname} 없음")
                continue
            pil = Image.open(path)
            arr = np.asarray(pil.convert("RGBA"))
            rgb_raw, al = arr[..., :3], arr[..., 3]
            t0 = time.time()
            out, info = P.flatten_canvas(arr)
            dt = time.time() - t0
            h, w = arr.shape[:2]
            check(out.shape == (h, w, 3) and out.dtype == np.uint8, f"F2 {fname}: 알파 채널이 남음 {out.shape}")
            check(abs(info["alpha_frac"] - exp_frac) < 0.002, f"F2 {fname}: alpha_frac {info['alpha_frac']:.4f}")
            check(np.array_equal(out[al == 255], rgb_raw[al == 255]), f"F2 {fname}: 불투명 영역이 바뀜")
            raw_band = near_black_band(rgb_raw)
            out_band = near_black_band(out)
            check(raw_band[side] >= 2, f"F2 {fname}: 알파를 버린 원본에 {side} 검정 띠가 없음(테스트 무효) {raw_band}")
            check(all(v == 0 for v in out_band.values()), f"F2 {fname}: 평탄화 후 근흑 띠 {out_band}")
            nb_new = int(((out.max(axis=2) < 8) & (al < 255)).sum())
            check(nb_new == 0, f"F2 {fname}: 채운 영역에 근흑 픽셀 {nb_new}")
            expp = edge_pad_expected(rgb_raw, info["filled_bbox"])
            check(expp is not None and np.array_equal(out, expp), f"F2 {fname}: np.pad(edge) 와 다름 (bbox {info['filled_bbox']})")
            out_pil, _ = P.flatten_canvas(pil)
            check(np.array_equal(out_pil, out), f"F2 {fname}: PIL 입력 결과 다름")
            Image.fromarray(out).save(OUT / f"flat_canvas_{fname[:3]}.png")
            note(f"F2 {fname} {w}x{h}: alpha<255 {info['alpha_frac'] * 100:.2f}% bbox {info['filled_bbox']}, "
                 f"원본 {side} 검정 띠 {raw_band[side]}px → 평탄화 후 {out_band}, max_fill_dist {info['max_fill_dist']:.0f}px, {dt * 1000:.0f}ms")
        # 03 크롭 전체: 알파가 있는 것은 평탄화 후 알파 없음 + 불투명 비트 보존
        n_alpha = n_all = 0
        for path in sorted(CROPS.glob("*.png")):
            n_all += 1
            im = Image.open(path)
            if "A" not in im.getbands() and not (im.mode == "P" and "transparency" in im.info):
                continue
            arr = np.asarray(im.convert("RGBA"))
            if (arr[..., 3] == 255).all():
                continue
            n_alpha += 1
            out, info = P.flatten_canvas(arr)
            op = arr[..., 3] == 255
            check(out.shape[2] == 3 and np.array_equal(out[op], arr[..., :3][op]), f"F2 {path.name}: 평탄화 실패")
            if op.any():
                raw_nb = (arr[..., :3].max(axis=2) < 8) & op
                if not raw_nb.any():
                    check(all(v == 0 for v in near_black_band(out).values()), f"F2 {path.name}: 근흑 띠")
        note(f"F2 03.크롭이미지 {n_all}개 중 알파<255 가 있는 {n_alpha}개 평탄화 확인")


    # ─────────────────────────────────────────────────────────────────
    # F3. flatten_ref — _t 컷아웃
    # ─────────────────────────────────────────────────────────────────
    if HAVE_REF:
        tfiles = sorted(p for p in REFS.glob("*.png") if "_t" in p.stem)
        check(len(tfiles) >= 10, f"F3 _t 컷아웃 {len(tfiles)}개")
        ghost_fixed = 0
        worst_pil = 0
        for i, path in enumerate(tfiles):
            arr = np.asarray(Image.open(path).convert("RGBA"))
            rgb, al = arr[..., :3], arr[..., 3]
            out = P.flatten_ref(arr)
            check(out.shape == arr.shape[:2] + (3,) and out.dtype == np.uint8, f"F3 {path.name}: 모양")
            z, o, pa = al == 0, al == 255, (al > 0) & (al < 255)
            check((out[z] == 128).all(), f"F3 {path.name}: 알파 0 이 매트색이 아님")
            check(np.array_equal(out[o], rgb[o]), f"F3 {path.name}: 불투명 영역이 바뀜")
            if pa.any():
                a = al[pa].astype(float)[:, None]
                expf = (rgb[pa].astype(float) * a + 128.0 * (255.0 - a)) / 255.0
                check(np.abs(out[pa].astype(float) - expf).max() <= 0.5 + 1e-9, f"F3 {path.name}: 부분 알파 반올림")
            if z.any() and rgb[z].std() > 0:
                ghost_fixed += 1
            bg = Image.new("RGBA", (arr.shape[1], arr.shape[0]), (128, 128, 128, 255))
            bg.alpha_composite(Image.fromarray(arr, "RGBA"))
            d = int(np.abs(np.asarray(bg.convert("RGB")).astype(int) - out.astype(int)).max())
            worst_pil = max(worst_pil, d)
            if i < 3:
                Image.fromarray(out).save(OUT / f"flat_ref_{i}.png")
            outw = P.flatten_ref(arr, "#ffffff")
            check((outw[z] == 255).all(), f"F3 {path.name}: 흰 매트")
        check(worst_pil <= 1, f"F3 PIL alpha_composite 대비 최대 차 {worst_pil} > 1")
        note(f"F3 _t 컷아웃 {len(tfiles)}개: 알파0=매트 128 정확, 불투명 보존, 투명 아래 RGB 잔상 있던 {ghost_fixed}개 제거, "
             f"PIL alpha_composite 대비 최대 차 {worst_pil}")


    # ─────────────────────────────────────────────────────────────────
    # S1. spec_from_manifest
    # ─────────────────────────────────────────────────────────────────
    def _refs_auto(nnn):
        names = sorted(p.name for p in REFS.iterdir()
                       if p.is_file() and p.name.startswith(f"{nnn}_") and p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"))
        return [n for n in names if "_t" in n] + [n for n in names if "_t" not in n]


    GLOSS = {"토스트-머리장식": "toast hair clip", "바보털": "ahoge hair strand"}
    crop002 = {"id": "002_abcdef", "name": "002_토스트-머리장식_right", "nnn": "002", "part": "토스트-머리장식_right",
               "rect": [2204, 1292, 2716, 1804], "source": "inputs/0123456789ab.png", "source_kind": "group_composite",
               "alpha_frac": 0.0,
               "targets": [{"tid": "A", "label": "토스트-머리장식_right", "name_en": "", "mode": "ref_correct", "refs": []}],
               "refs_auto": _refs_auto("002") if HAVE_REF else ["002_토스트-머리장식_left_t.png",
                                                                "002_토스트-머리장식_right_t.png",
                                                                "002_토스트-머리장식_right.png"],
               "warnings": []}
    crop_before = json.dumps(crop002, ensure_ascii=False, sort_keys=True)
    refs_dir = str(REFS) if HAVE_REF else ""
    sp = P.spec_from_manifest(crop002, None, refs_dir, GLOSS, None)
    check(json.dumps(crop002, ensure_ascii=False, sort_keys=True) == crop_before, "S1 crop 입력이 바뀜")
    check(sp["crop_id"] == "002_abcdef" and sp["canvas"] == "inputs/0123456789ab.png", "S1 crop_id/canvas")
    check(len(sp["targets"]) == 1 and sp["targets"][0]["name"] == "toast hair clip", f"S1 용어집 이름 {sp['targets']}")
    check(sp["targets"][0]["refs"] == [os.path.join(refs_dir, "002_토스트-머리장식_right_t.png")],
          f"S1 자동 레퍼런스 {sp['targets'][0]['refs']} (left 가 아니라 right_t)")
    check(sp["targets"][0]["mode"] == "ref_correct" and sp["targets"][0]["detail"] == P.DEFAULT_DETAIL, "S1 mode/detail 기본값")
    check(any("자동 선택" in w for w in sp["warnings"]) and any("detail" in w for w in sp["warnings"]), f"S1 경고 {sp['warnings']}")
    check(not any("name_en" in w for w in sp["warnings"]), "S1 용어집 번역 시 이름 경고 없음")
    check(P.validate_spec(sp) == [], f"S1 validate {P.validate_spec(sp)}")
    rs = P.render(sp)
    check("Correct the toast hair clip in @image1 to match the toast hair clip in @image2: "
          "its shape, colors, materials, and surface details." in rs["prompt"], "S1 렌더 문장")
    check(rs["images"] == [sp["canvas"]] + sp["targets"][0]["refs"], "S1 images")
    check(": ." not in rs["prompt"], "S1 빈 detail 렌더 없음")
    if HAVE_REF:
        (OUT / "spec_from_manifest_002.txt").write_text(rs["prompt"] + "\n\nIMAGES (upload order):\n"
                                                        + "\n".join(f"{i + 1}. {p}" for i, p in enumerate(rs["images"])),
                                                        encoding="utf-8")
    # store.match_refs 실제 결과(크롭 이름 무관 정렬: left_t 가 먼저)에서도 right_t 를 고르는지
    if HAVE_REF:
        ra = P.store.match_refs(str(REFS), "002")
        sp2 = P.spec_from_manifest(dict(crop002, refs_auto=ra), None, refs_dir, GLOSS, None)
        check(os.path.basename(sp2["targets"][0]["refs"][0]) == "002_토스트-머리장식_right_t.png",
              f"S1 store.match_refs 결과로 자동 선택 {sp2['targets'][0]['refs']}")
        note(f"S1 store.match_refs('002') {len(ra)}개(첫 항목 {ra[0]}) → 자동 선택 {os.path.basename(sp2['targets'][0]['refs'][0])}")
        for nnn, cname, want in (("001", "001_바보털", "001_바보털_t.png"),
                                 ("007", "007_머리장식-오른쪽", "007_머리장식-오른쪽.png"),
                                 ("010", "010_손가락붕대-왼쪽", "010_손가락붕대-왼쪽.png")):
            cc = {"id": nnn, "name": cname, "source": "inputs/x.png", "refs_auto": P.store.match_refs(str(REFS), nnn),
                  "targets": [{"tid": "A", "label": cname[4:], "name_en": "x", "mode": "ref_correct", "refs": []}]}
            got = os.path.basename(P.spec_from_manifest(cc, None, refs_dir, None, None)["targets"][0]["refs"][0])
            check(got == want, f"S1 {cname}: 자동 레퍼런스 {got} != {want}")
    # 용어집 = store.translate (정규화 비교: 공백·_·- 무시)
    sp = P.spec_from_manifest(crop002, None, "", {"토스트 머리장식": "toast hair clip"}, None)
    check(sp["targets"][0]["name"] == "toast hair clip", f"S1 store.translate 정규화 {sp['targets'][0]['name']}")

    # 용어집 없음 → 라벨 + 경고
    sp = P.spec_from_manifest(crop002, crop002["targets"][0], refs_dir, None, None)
    check(sp["targets"][0]["name"] == "토스트-머리장식_right" and any("name_en" in w for w in sp["warnings"]),
          "S1 용어집 없음 → 라벨 + 경고")
    check(P.render(sp)["prompt"].count("토스트-머리장식_right") >= 2, "S1 한글 라벨 렌더")
    # name_en 있음
    c = copy.deepcopy(crop002)
    c["targets"][0]["name_en"] = "toast clip"
    c["targets"][0]["refs"] = ["002_토스트-머리장식_right_t2.png"]
    sp = P.spec_from_manifest(c, c["targets"][0], refs_dir, GLOSS, {"targets": [{"id": "A", "detail": "white bread"}]})
    check(sp["targets"][0]["name"] == "toast clip" and sp["targets"][0]["detail"] == "white bread", "S1 name_en + 타깃 오버라이드")
    check(sp["targets"][0]["refs"] == [os.path.join(refs_dir, "002_토스트-머리장식_right_t2.png")], "S1 target.refs 우선")
    check(sp["warnings"] == [], f"S1 경고 없어야 함 {sp['warnings']}")
    # 레퍼런스 없음 → self_restore
    c = copy.deepcopy(crop002)
    c["refs_auto"] = []
    sp = P.spec_from_manifest(c, None, "", GLOSS, None)
    check(sp["targets"][0]["mode"] == "self_restore" and any("self_restore" in w for w in sp["warnings"]), "S1 refs 없음 → self_restore")
    check("(no reference)" in P.render(sp)["prompt"] and P.validate_spec(sp) == [], "S1 self_restore 렌더")
    # sketch_guide 인데 guide 없음 → ref_correct
    c = copy.deepcopy(crop002)
    c["targets"][0]["mode"] = "sketch_guide"
    sp = P.spec_from_manifest(c, None, "", GLOSS, {"targets": [{"id": "A", "action": "relocate"}]})
    check(sp["targets"][0]["mode"] == "ref_correct", "S1 guide 없음 → ref_correct")
    check(any("sketch_guide" in w for w in sp["warnings"]), "S1 sketch_guide 경고")
    check("None" not in P.render(sp)["prompt"], "S1 렌더에 None 없음")
    # 알 수 없는 키 / 잘못된 입력
    sp = P.spec_from_manifest(crop002, None, "", GLOSS, {"foo": 1, "_comment": "x", "targets": [{"id": "A", "bar": 2}]})
    check(sum("모르는 키" in w for w in sp["warnings"]) == 2, f"S1 모르는 키 경고 {sp['warnings']}")
    expect_raises(lambda: P.spec_from_manifest("x", None, "", None, None), "S1 crop 비 dict")
    expect_raises(lambda: P.spec_from_manifest(crop002, "A", "", None, None), "S1 target 문자열 ValueError")
    expect_raises(lambda: P.spec_from_manifest(dict(crop002, source=None), None, "", None, None), "S1 canvas 없음", contains="canvas")
    expect_raises(lambda: P.spec_from_manifest(crop002, None, "", None, {"targets": [{"id": "A", "mode": "magic"}]}),
                  "S1 잘못된 mode")
    expect_raises(lambda: P.spec_from_manifest(crop002, None, "", None, {"ref_style": "zz"}), "S1 잘못된 ref_style")
    expect_raises(lambda: P.spec_from_manifest(crop002, None, "", None, ["x"]), "S1 overrides 비 dict")
    expect_raises(lambda: P.spec_from_manifest(dict(crop002, targets=[]), None, "", GLOSS, None), "S1 타깃 없음 ValueError")

    # 오버라이드로 참조 스펙 5개 재현 (매니페스트 골격 + specs/<crop_id>.json 역할)
    n_repro = 0
    for name, spec in SPECS.items():
        crop = {"id": spec["crop_id"], "name": "000_테스트", "source": "inputs/placeholder.png",
                "targets": [{"tid": t["id"], "label": f"라벨{t['id']}", "name_en": "", "mode": "ref_correct", "refs": []}
                            for t in spec["targets"]],
                "refs_auto": ["000_무관_t.png"]}
        ov = {k: v for k, v in spec.items() if k != "crop_id"}
        ov_before = json.dumps(ov, ensure_ascii=False, sort_keys=True)
        sp = P.spec_from_manifest(crop, None, "", None, ov)
        check(json.dumps(ov, ensure_ascii=False, sort_keys=True) == ov_before, f"S1 {name}: overrides 가 바뀜")
        check(sp["warnings"] == [], f"S1 {name}: 경고 {sp['warnings']}")
        r = P.render(sp)
        check(r["prompt"] == EXPECT[name][0] and r["images"] == EXPECT[name][1], f"S1 {name}: 오버라이드 재현 렌더 불일치")
        for t_exp, t_got in zip(spec["targets"], sp["targets"]):
            # spec_from_manifest 는 refs 를 항상 목록으로 채운다(참조 스펙의 self_restore 타깃엔 refs 키가 없음)
            check(dict(t_exp, refs=t_exp.get("refs", [])) == t_got, f"S1 {name}/{t_exp['id']}: 타깃 dict 불일치")
        one = P.spec_from_manifest(crop, crop["targets"][-1], "", None, ov)
        t_last = dict(spec["targets"][-1], refs=spec["targets"][-1].get("refs", []))
        check(len(one["targets"]) == 1 and one["targets"][0] == t_last, f"S1 {name}: tid 단일 타깃")
        n_repro += not any(f.startswith(f"S1 {name}") for f in FAILS)
    if SPECS:
        note(f"S1 오버라이드로 참조 스펙 {n_repro}/{len(SPECS)}개 재현 → 렌더 바이트 일치")


# ═════════════════════════════════════════════════════════════════
# A. analysis 단위 (합성 데이터)
# ═════════════════════════════════════════════════════════════════
def analysis_unit():
    # ─────────────────────────────────────────────────────────────────
    # 1. place / Lab / 행렬 도우미
    # ─────────────────────────────────────────────────────────────────
    src = texture(256, 384, 1)
    p = an.place(src, (128, 96))
    check(p.shape == (96, 128, 3) and p.dtype == np.uint8, "place uint8 shape/dtype")
    check(np.array_equal(p, np.asarray(Image.fromarray(src).resize((128, 96), Image.LANCZOS))), "place == PIL LANCZOS")
    pf = an.place(src.astype(np.float32) / 255, (128, 96))
    check(pf.dtype == np.float32 and mae(pf * 255, p) < 0.6, f"place float 경로 ≈ uint8 경로 ({mae(pf * 255, p):.3f})")
    same = an.place(src, (384, 256))
    check(np.array_equal(same, src) and same is not src, "place 같은 크기 = 사본")
    expect_raises(lambda: an.place(src[..., 0], (10, 10)), "place 2D 입력 거부")

    lab = an.rgb_to_lab(np.array([[[1, 1, 1], [0, 0, 0], [1, 0, 0]]], np.float32))
    check(np.allclose(lab[0, 0], [100, 0, 0], atol=0.02) and np.allclose(lab[0, 1], [0, 0, 0], atol=0.02),
          f"Lab 흰/검 {lab[0, :2].tolist()}")
    check(np.allclose(lab[0, 2], [53.24, 80.09, 67.20], atol=0.05), f"Lab 빨강 {lab[0, 2].tolist()}")
    rnd = np.random.default_rng(3).random((64, 64, 3)).astype(np.float32)
    check(float(np.abs(an.lab_to_rgb(an.rgb_to_lab(rnd)) - rnd).max()) < 1e-3, "Lab 왕복 오차 < 1e-3")

    rect = [100, 200, 612, 968]
    check(an.quad_from_matrix(np.eye(2, 3), rect) == [100, 200, 612, 200, 612, 968, 100, 968], "identity quad = rect 코너")
    Mr = [[1.002, -0.01, 1.5], [0.012, 1.034, -21.0]]
    check(np.allclose(an.matrix_from_quad(an.quad_from_matrix(Mr, rect), rect), Mr, atol=1e-9), "quad ↔ matrix 왕복")
    uq = [1771.0, 159.0, 2283.0, 159.0, 2283.0, 949.0, 1771.0, 949.0]  # 05 PSD 001 사용자 배치
    um = an.matrix_from_quad(uq, [1771, 192, 2283, 960])
    dd = an.decompose(um)
    check(abs(dd["sy"] - 790 / 768) < 1e-9 and abs(dd["sx"] - 1) < 1e-9 and abs(dd["rot_deg"]) < 1e-9,
          f"matrix_from_quad 001 사용자 배치 sy {dd['sy']:.5f}")
    th = np.radians(2.0)
    Ab = np.array([[np.cos(th), -np.sin(th)], [np.sin(th), np.cos(th)]]) @ np.diag([1.1, 0.95]) @ np.array([[1, 0.05], [0, 1]])
    dm = an.decompose(np.hstack([Ab, [[3], [-4]]]))
    check(abs(dm["rot_deg"] - 2) < 1e-9 and abs(dm["sx"] - 1.1) < 1e-9 and abs(dm["sy"] - 0.95) < 1e-9
          and abs(dm["shear"] - 0.05) < 1e-9 and dm["dx"] == 3 and dm["dy"] == -4, f"decompose {dm}")

    dot = np.zeros((64, 64, 3), np.float32)
    dot[20, 10] = 1.0
    moved = an.warp_affine(dot, [[1, 0, 5], [0, 1, 7]])
    check(tuple(np.unravel_index(moved[..., 0].argmax(), moved.shape[:2])) == (27, 15), "warp_affine: cand (10,20) → base (15,27)")
    back = an.warp_affine(moved, [[1, 0, 5], [0, 1, 7]], inverse=True)
    check(tuple(np.unravel_index(back[..., 0].argmax(), back.shape[:2])) == (20, 10), "warp_affine inverse 되돌리기")

    # ─────────────────────────────────────────────────────────────────
    # 2. register — 부호 / 가드 / affine / 발산 / exclude_mask
    # ─────────────────────────────────────────────────────────────────
    base = texture(320, 320, 7)
    shift = np.float32([[1, 0, 2.5], [0, 1, -1.25]])  # 내용이 cand 에서 (+2.5, −1.25) 로 이동
    cand = cv2.warpAffine(base, shift, (320, 320), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REFLECT)
    inner = np.zeros((320, 320), bool)
    inner[16:-16, 16:-16] = True
    for mode in ("translation", "guarded_affine"):
        r = an.register(base, cand, mode=mode)
        check(r["applied"] and abs(r["dx"] + 2.5) < 0.15 and abs(r["dy"] - 1.25) < 0.15,
              f"부호 테스트 {mode}: dx {r['dx']:.3f} (기대 −2.5), dy {r['dy']:.3f} (기대 +1.25)")
        al = an.warp_affine(cand, r["matrix"])
        check(mae(al, base, inner) < 0.35 * mae(cand, base, inner),
              f"부호 테스트 {mode}: 정렬 후 MAE {mae(al, base, inner):.2f} < 원래 {mae(cand, base, inner):.2f}의 35%")
        check(set(r) >= {"matrix", "applied", "kind", "dx", "dy", "sx", "sy", "rot_deg", "shear", "med_before",
                         "med_after", "improve"}, f"register 반환 키 {mode}")
    r = an.register(base, cand, mode="off")
    check(not r["applied"] and r["kind"] == "identity" and r["matrix"] == [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], "mode off = identity")
    noisy = np.clip(base.astype(np.int16) + np.random.default_rng(1).integers(-3, 4, base.shape), 0, 255).astype(np.uint8)
    r = an.register(base, noisy)
    check(not r["applied"], f"잡음만 있는 후보는 identity (kind {r['kind']}, improve {r['improve']})")
    expect_raises(lambda: an.register(base, cand, mode="affine"), "register 잘못된 mode")
    expect_raises(lambda: an.register(base, cand[:300]), "register 크기 불일치")

    # affine(배율 1.03·회전 0.6°) + 가운데 25% 를 다른 내용으로 바꾼 후보 → 마스크 없이도 복원
    T = cv2.getRotationMatrix2D((160, 160), 0.6, 1.0)
    T = (np.vstack([T, [0, 0, 1]]) @ np.array([[1, 0, 0], [0, 1.03, -0.03 * 160], [0, 0, 1]]))[:2]
    cand_a = cv2.warpAffine(base, T, (320, 320), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REFLECT)
    yy, xx = np.mgrid[0:320, 0:320]
    disk = (xx - 160) ** 2 + (yy - 150) ** 2 < 90 ** 2
    cand_a[disk] = texture(320, 320, 99)[disk]
    expected = np.linalg.inv(np.vstack([T, [0, 0, 1]]))[:2]
    r = an.register(base, cand_a)
    ce = corner_err(r["matrix"], expected, 320, 320)
    check(r["applied"] and r["kind"] == "affine" and ce < 1.0,
          f"합성 affine + 내용 변경: kind {r['kind']}, 코너 오차 {ce:.2f}px (기대 <1), sy {r['sy']:.4f}")
    r = an.register(base, cand_a, exclude_mask=disk.astype(np.float32))
    ce = corner_err(r["matrix"], expected, 320, 320)
    check(r["applied"] and r["masked"] and ce < 1.0, f"합성 affine exclude_mask: 코너 오차 {ce:.2f}px")
    expect_raises(lambda: an.register(base, cand_a, exclude_mask=disk[:10]), "register exclude_mask 크기 불일치")

    # 맥락(테두리)은 (+4, 0) 이동, 가운데 타깃은 제자리 → exclude_mask 를 주면 맥락 이동을 따른다
    cand_t = cv2.warpAffine(base, np.float32([[1, 0, 4], [0, 1, 0]]), (320, 320), flags=cv2.INTER_CUBIC,
                            borderMode=cv2.BORDER_REFLECT)
    box = np.zeros((320, 320), np.float32)
    box[60:260, 60:260] = 1
    cand_t[box > 0] = base[box > 0]
    r = an.register(base, cand_t, exclude_mask=box)
    check(r["applied"] and abs(r["dx"] + 4) < 0.3 and abs(r["dy"]) < 0.3 and abs(r["sx"] - 1) < 0.01,
          f"exclude_mask: 맥락 이동 따라감 dx {r['dx']:.2f} dy {r['dy']:.2f}")

    # 무관한 그림 → 발산 금지(적용되더라도 한계 안)
    r = an.register(base, texture(320, 320, 12345))
    check((not r["applied"]) or (r["disp"] <= 32 and 0.9 <= r["sx"] <= 1.1 and 0.9 <= r["sy"] <= 1.1),
          f"무관한 후보 발산 금지: {r['kind']} disp {r['disp']}")
    # 알파 0 영역(캔버스 밖)은 점수에서 빠진다
    b4 = np.dstack([base, np.full((320, 320), 255, np.uint8)])
    b4[:, 260:, :3] = 0
    b4[:, 260:, 3] = 0
    r = an.register(b4, cand)
    check(r["applied"] and abs(r["dx"] + 2.5) < 0.2 and abs(r["dy"] - 1.25) < 0.2, f"RGBA base 무효 영역 제외 dx {r['dx']:.2f}")

    # ─────────────────────────────────────────────────────────────────
    # 3. color_match / delta_e
    # ─────────────────────────────────────────────────────────────────
    dark = np.clip(base.astype(np.float32) * [0.9, 0.92, 1.0] + [-4, -2, 6], 0, 255).astype(np.uint8)
    cm = an.color_match(base, dark)
    check(cm.dtype == np.uint8 and mae(cm, base) < 0.3 * mae(dark, base), f"color_match MAE {mae(dark, base):.2f} → {mae(cm, base):.2f}")
    cmf = an.color_match(base.astype(np.float32) / 255, dark.astype(np.float32) / 255)
    check(cmf.dtype == np.float32 and mae(cmf * 255, cm) < 0.6, "color_match float 경로")
    smask = np.zeros((320, 320), np.float32)
    smask[:, :160] = 1
    cms = an.color_match(base, dark, sample_mask=smask)
    check(mae(cms, base) < 0.4 * mae(dark, base), "color_match sample_mask")
    de = an.delta_e(base, base)
    check(de.shape == (320, 320) and de.dtype == np.float32 and float(de.max()) < 1e-4, "delta_e 동일 = 0")
    flat_a = np.full((40, 40, 3), [120, 80, 60], np.uint8)
    flat_b = np.full((40, 40, 3), [125, 80, 60], np.uint8)
    exp_de = float(np.linalg.norm(an.rgb_to_lab(flat_a[:1, :1] / 255.0)[0, 0] - an.rgb_to_lab(flat_b[:1, :1] / 255.0)[0, 0]))
    check(abs(float(an.delta_e(flat_a, flat_b)[20, 20]) - exp_de) < 1e-3, f"delta_e 평탄 색 = Lab 거리 {exp_de:.3f}")
    fa4 = np.dstack([flat_a, np.full((40, 40), 255, np.uint8)])
    fa4[:, :10, 3] = 0
    check(float(an.delta_e(fa4, flat_b)[:, :10].max()) == 0.0, "delta_e 무효 픽셀 0")

    # ─────────────────────────────────────────────────────────────────
    # 4. auto_mask — 합성 원
    # ─────────────────────────────────────────────────────────────────
    H = W = 400
    bg = texture(H, W, 21)
    yy, xx = np.mgrid[0:H, 0:W]
    dist = np.sqrt((xx - 210.0) ** 2 + (yy - 190.0) ** 2)
    circle = dist <= 60
    cand_c = np.clip(bg.astype(np.int16) + 3, 0, 255).astype(np.uint8)  # 전역 톤 +3
    cand_c[circle] = [200, 40, 160]
    cand_c[30:33, 30:33] = [0, 255, 0]  # 0.1% 미만 점 → 제거 대상
    am0 = an.auto_mask(bg, cand_c, grow=0, feather_sigma=0)
    gt = circle.astype(np.float32)
    check(an.soft_iou(am0["mask"], gt) > 0.9, f"auto_mask 원 IoU(grow 0) {an.soft_iou(am0['mask'], gt):.3f}")
    check(am0["hard"].dtype == np.uint8 and set(np.unique(am0["hard"])) <= {0, 255}, "hard 는 0/255 uint8")
    check(len(am0["components"]) == 1 and max(abs(a - b) for a, b in zip(am0["components"][0]["bbox"], [150, 130, 271, 251])) <= 3,
          f"성분 1개 + bbox ≈ 원 {am0['components']}")
    check(float(am0["mask"][28:36, 28:36].max()) == 0.0, "작은 점 제거")
    am = an.auto_mask(bg, cand_c)
    m = am["mask"]
    check(m.dtype == np.float32 and m.shape == (H, W) and float(m.min()) >= 0 and float(m.max()) <= 1, "mask float32 0..1")
    check(an.recall(m, gt) > 0.99, f"기본값 recall {an.recall(m, gt):.3f}")
    ring_in = (dist > 61) & (dist < 66)
    ring_far = (dist > 60 + 9 + 3 * 3.5 + 4) & (dist < 100)
    check(float(m[ring_in].min()) > 0.5, f"grow 9 안쪽 링 > 0.5 (min {float(m[ring_in].min()):.2f})")
    check(float(m[ring_far].max()) < 0.02, f"grow+3σ 바깥 ≈ 0 (max {float(m[ring_far].max()):.3f})")
    check(am["params"]["grow"] == 9 and am["params"]["feather_sigma"] == 3.5 and am["params"]["dE"] == 8.0, "params 기록")
    amr = an.auto_mask(bg, cand_c, roi=[0, 0, 210, 400])
    check(float(amr["mask"][:, 210:].max()) == 0.0 and float(amr["mask"][:, :200].max()) > 0.9, "roi 밖 0, 안 유지")
    amn = an.auto_mask(bg, cand_c, roi=[300, 300, 400, 400])
    check(float(amn["mask"].max()) == 0.0 and amn["components"] == [], "roi 가 원을 안 덮으면 빈 마스크")
    ring_cand = bg.copy()
    ring_cand[(dist <= 60) & (dist >= 45)] = [30, 200, 220]
    amh = an.auto_mask(bg, ring_cand, grow=0, feather_sigma=0)
    check(float(amh["mask"][190, 210]) == 1.0, "fill_holes: 고리 안쪽 채움")
    amh2 = an.auto_mask(bg, ring_cand, grow=0, feather_sigma=0, fill_holes=False)
    check(float(amh2["mask"][190, 210]) == 0.0, "fill_holes=False: 안쪽 비움")
    shifted_c = cv2.warpAffine(cand_c, np.float32([[1, 0, 3], [0, 1, -2]]), (W, H), flags=cv2.INTER_CUBIC,
                               borderMode=cv2.BORDER_REFLECT)
    ama = an.auto_mask(bg, shifted_c, grow=0, feather_sigma=0)
    check(an.soft_iou(ama["mask"], gt) > 0.9 and ama["params"]["align_matrix"] is not None,
          f"align: 이동된 후보도 원 IoU {an.soft_iou(ama['mask'], gt):.3f}")
    amx = an.auto_mask(bg, shifted_c, grow=0, feather_sigma=0, align=False)
    check(an.soft_iou(amx["mask"], gt) < an.soft_iou(ama["mask"], gt), "align=False 는 이동에 약함")

    # ─────────────────────────────────────────────────────────────────
    # 5. tone_field / apply_delta — 합성 그라데이션
    # ─────────────────────────────────────────────────────────────────
    grad = np.linspace(8, -8, W, dtype=np.float32)[None, :, None] * np.ones((H, 1, 3), np.float32)
    grad[..., 2] *= 0.5
    cand_g = np.clip(bg.astype(np.float32) + grad, 0, 255).astype(np.uint8)
    cand_g[circle] = [200, 40, 160]
    excl = (dist <= 72).astype(np.float32)
    tf = an.tone_field(bg, cand_g, excl)
    d = tf["delta"]
    check(d.shape == (H, W, 3) and d.dtype == np.float32, "tone_field delta shape/dtype")
    outside = (dist > 72)
    fixed = an.apply_delta(cand_g, d)
    check(mae(fixed, bg, outside) < 0.25 * mae(cand_g, bg, outside),
          f"tone_field 마스크 밖 MAE {mae(cand_g, bg, outside):.2f} → {mae(fixed, bg, outside):.2f}")
    inside_err = float(np.abs(d[circle] + grad[circle]).mean())
    check(inside_err < 2.0, f"제외 영역 안 보간 오차 {inside_err:.2f}")
    check(tf["stats"]["coverage"] > 0.8 and len(tf["stats"]["mean_rgb"]) == 3, f"stats {tf['stats']}")
    big = np.clip(bg.astype(np.float32) + grad * 3, 0, 255).astype(np.uint8)
    tfc = an.tone_field(bg, big, np.zeros((H, W), np.float32), clamp=12.0)
    check(float(np.abs(tfc["delta"]).max()) <= 12.0 + 1e-5 and tfc["stats"]["clamped_frac"] > 0, "clamp ±12")
    tf0 = an.tone_field(bg, cand_g, np.ones((H, W), np.float32))
    check(float(np.abs(tf0["delta"]).max()) == 0.0 and "note" in tf0["stats"], "전부 제외 → 보정 없음")
    expect_raises(lambda: an.tone_field(bg, cand_g, excl[:10]), "tone_field exclude 크기 불일치")
    expect_raises(lambda: an.tone_field(bg, cand_g, excl, sigma=0), "tone_field sigma 0 거부")
    up = an.apply_delta(cand_g, d, out_size=(W * 2, H * 2))
    check(up.shape == (H * 2, W * 2, 3) and up.dtype == np.uint8, "apply_delta out_size 업샘플")
    cand_big = an.place(cand_g, (W * 2, H * 2))
    up2 = an.apply_delta(cand_big, d)
    check(np.array_equal(up, up2), "apply_delta: 작은 원본+out_size == 큰 원본")
    check(mae(an.place(up, (W, H)), bg, outside) < 0.35 * mae(cand_g, bg, outside), "업샘플 보정본을 줄이면 base 와 가까움")
    rgba = np.dstack([cand_g, np.full((H, W), 77, np.uint8)])
    ra = an.apply_delta(rgba, d)
    check(ra.shape == (H, W, 4) and bool((ra[..., 3] == 77).all()), "apply_delta RGBA 알파 유지")
    fl = an.apply_delta(cand_g.astype(np.float32) / 255, d)
    check(fl.dtype == np.float32 and mae(fl * 255, fixed) < 0.6, "apply_delta float 경로")

    # ─────────────────────────────────────────────────────────────────
    # 6. gates / zorder / 지표
    # ─────────────────────────────────────────────────────────────────
    ident_reg = {"applied": False}
    g = an.gates(bg, bg, np.zeros((H, W), np.float32), ident_reg)
    check(g["outside_mad"] == 0 and g["fails"] == [] and not g["black_band"] and not g["reframed"], f"gates 동일 {g}")
    band = bg.copy()
    band[:, :3] = 2
    g = an.gates(bg, band, np.zeros((H, W), np.float32), {"applied": True})
    check(g["black_band"] and g["black_edges"]["left"] == 3 and "black_band" in g["fails"] and g["reframed"], f"검은 띠 {g}")
    bgb = bg.copy()
    bgb[:, :3] = 1
    check(not an.gates(bgb, band, np.zeros((H, W), np.float32), ident_reg)["black_band"], "원본도 검으면 띠 아님")
    band1 = bg.copy()
    band1[-1:, :] = 0
    check(not an.gates(bg, band1, np.zeros((H, W), np.float32), ident_reg)["black_band"], "1px 는 띠 아님(≥2)")
    bg4 = np.dstack([bg, np.full((H, W), 255, np.uint8)])
    bg4[:, -20:, 3] = 0
    blk = bg.copy()
    blk[:, -20:] = 0
    gi = an.gates(bg4, blk, np.zeros((H, W), np.float32), ident_reg)
    check(gi["black_band"] and gi["black_edges"]["right"] == 20, "원본 무효(투명) 자리에 검정 = 띠")
    g = an.gates(bg, texture(H, W, 5), circle.astype(np.float32), ident_reg)
    check("outside_mad" in g["fails"] and g["outside_mad"] > 16, f"전혀 다른 후보 = outside_mad 실패 ({g['outside_mad']})")
    g = an.gates(bg, cand_c, am["mask"], ident_reg)
    check(g["fails"] == [] and g["outside_mad"] < 5, f"원 후보 + 자동 마스크 = 통과 {g}")

    check(an.zorder([{"key": "a", "area": 10}, {"key": "b", "area": 30}, {"key": "c", "area": 10}]) == ["b", "a", "c"],
          "zorder 큰 것 아래, 동률은 입력 순서")
    check(an.zorder([]) == [], "zorder 빈 목록")
    A = np.array([[1, 0.5], [0, 0]], np.float32)
    B = np.array([[1, 1], [0, 0]], np.float32)
    check(abs(an.soft_iou(A, B) - 1.5 / 2) < 1e-6 and abs(an.recall(A, B) - 0.75) < 1e-6 and abs(an.precision(A, B) - 1) < 1e-6,
          "soft_iou / recall / precision 값")
    check(an.soft_iou(np.zeros((2, 2)), np.zeros((2, 2))) == 1.0 and an.recall(A, np.zeros((2, 2))) == 1.0, "빈 마스크 = 1.0")
    check(abs(an.soft_iou(A, B, valid=np.array([[True, False], [True, True]])) - 1.0) < 1e-6, "valid 로 제한")


# ═════════════════════════════════════════════════════════════════
# U. psd 단위 (quad, SO 캐시, PSDWriter 왕복, 읽기 함수)
# ═════════════════════════════════════════════════════════════════
def psd_unit():
    OUT = OUT_ROOT / "psd"
    OUT.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(1234)

    # ─────────────────────────────────────────────────────────────────
    # U1. quad 도우미
    # ─────────────────────────────────────────────────────────────────
    section("U1 quad helpers")
    q = dpsd.quad_from_rect([10, 20, 74, 148])
    check(q == [10, 20, 74, 20, 74, 148, 10, 148], f"quad_from_rect 기본 {q}")
    check(dpsd.quad_bbox(q) == [10, 20, 74, 148], "quad_bbox 정수")
    check(dpsd.quad_kind(q) == "rect_int", "quad_kind rect_int")
    qt = dpsd.quad_from_rect([10, 20, 74, 148], [[1, 0, 2.5], [0, 1, -1.25]])
    check(abs(qt[0] - 12.5) < 1e-9 and abs(qt[1] - 18.75) < 1e-9 and abs(qt[4] - 76.5) < 1e-9, f"평행이동 부호 {qt[:6]}")
    check(dpsd.quad_kind(qt) == "parallelogram", "평행이동 quad = parallelogram(비정수)")
    check(dpsd.quad_bbox(qt) == [12, 18, 77, 147], f"비정수 quad bbox {dpsd.quad_bbox(qt)}")
    # 중심 기준 2배 확대 행렬(픽셀 중심 규약): 64x128 rect 의 중심(31.5, 63.5) 고정
    qs = dpsd.quad_from_rect([0, 0, 64, 128], [[2, 0, -31.5], [0, 2, -63.5]])
    check([round(v, 6) for v in qs] == [-32, -64, 96, -64, 96, 192, -32, 192], f"확대 행렬 연속좌표 변환 {qs}")
    check(dpsd.quad_kind([0, 0, 10, 0, 12, 10, 0, 10]) == "perspective", "원근 quad 판정")
    expect_raises(lambda: dpsd.quad_bbox([1, 2, 3]), "quad 길이 오류")
    expect_raises(lambda: dpsd.quad_from_rect([5, 5, 5, 9]), "빈 rect")

    # ─────────────────────────────────────────────────────────────────
    # U2. SO 캐시 렌더
    # ─────────────────────────────────────────────────────────────────
    section("U2 render_so_cache")
    src = np.zeros((256, 256, 3), np.uint8)
    yy, xx = np.mgrid[0:256, 0:256]
    src[..., 0] = xx
    src[..., 1] = yy
    src[..., 2] = 128
    src_im = Image.fromarray(src)
    rgb, alpha, l0, t0 = dpsd.render_so_cache(src_im, dpsd.quad_from_rect([30, 40, 94, 104]))
    ref = np.asarray(src_im.resize((64, 64), Image.LANCZOS))
    check((l0, t0) == (30, 40) and alpha is None and np.array_equal(rgb, ref), "정수 rect = PIL LANCZOS 와 동일, 알파 없음")
    ang = math.radians(10.0)
    cx, cy, hw = 100.0, 100.0, 32.0
    corners = [(-hw, -hw), (hw, -hw), (hw, hw), (-hw, hw)]
    qr = []
    for px, py in corners:
        qr += [cx + px * math.cos(ang) - py * math.sin(ang), cy + px * math.sin(ang) + py * math.cos(ang)]
    rgb_r, alpha_r, lr, tr = dpsd.render_so_cache(src_im, qr)
    bb = dpsd.quad_bbox(qr)
    check(alpha_r is not None and rgb_r.shape[:2] == (bb[3] - bb[1], bb[2] - bb[0]) and (lr, tr) == (bb[0], bb[1]),
          f"회전 quad 캐시 크기/위치 {rgb_r.shape} {bb}")
    check(alpha_r[0, 0] == 0 and alpha_r[-1, -1] == 0 and alpha_r[alpha_r.shape[0] // 2, alpha_r.shape[1] // 2] == 255,
          "회전 quad: bbox 모서리 투명, 중심 불투명")
    cyy, cxx = alpha_r.shape[0] // 2, alpha_r.shape[1] // 2
    mid = rgb_r[cyy, cxx].astype(int)
    check(abs(mid[0] - 128) <= 6 and abs(mid[1] - 128) <= 6, f"회전 quad 중심 색 = 원본 중심 {mid}")
    qf = dpsd.quad_from_rect([30, 40, 94, 104], [[1, 0, 0.5], [0, 1, 0.0]])
    rgb_f, alpha_f, lf, tf = dpsd.render_so_cache(src_im, qf)
    check(alpha_f is not None and alpha_f.shape == (64, 65) and 0 < alpha_f[10, 0] < 255 and alpha_f[10, 30] == 255,
          f"0.5px 이동: 가장자리 부분 알파 {alpha_f.shape if alpha_f is not None else None}")
    expect_raises(lambda: dpsd.render_so_cache(src_im, [0, 0, 10, 0, 12, 10, 0, 10]), "원근 quad 렌더 거부")

    # ─────────────────────────────────────────────────────────────────
    # U3. PSDWriter 오류 경로
    # ─────────────────────────────────────────────────────────────────
    section("U3 PSDWriter errors")
    expect_raises(lambda: dpsd.PSDWriter((64, 64), depth=16), "16-bit 거부", contains="8-bit")
    expect_raises(lambda: dpsd.PSDWriter((0, 64)), "크기 0 거부")
    w_err = dpsd.PSDWriter((64, 64))
    small_png = png_bytes(src[:32, :32])
    expect_raises(lambda: w_err.add_pixel(None, "x", np.zeros((4, 4), np.uint8), 0, 0), "rgb 2D 거부")
    expect_raises(lambda: w_err.add_pixel(None, "x", np.zeros((4, 4, 3), np.uint8), 0, 0, label="pink"), "라벨 이름 오류")
    expect_raises(lambda: w_err.add_pixel(None, "x", np.zeros((4, 4, 3), np.uint8), 0, 0,
                                          mask={"arr": np.zeros((4, 4), np.uint8), "left": 0, "top": 0, "bg": 7}), "mask bg 오류")
    expect_raises(lambda: w_err.add_smart_object(None, "so", small_png, quad=[0, 0, 10, 0, 12, 10, 0, 10]), "원근 quad SO 거부")
    expect_raises(lambda: w_err.add_smart_object(None, "so", small_png, src_size=(33, 32), quad=dpsd.quad_from_rect([0, 0, 8, 8])),
                  "src_size 불일치 거부")
    expect_raises(lambda: w_err.add_smart_object(None, "so", small_png, quad=dpsd.quad_from_rect([0, 0, 8, 8]),
                                                 cache_rgb=np.zeros((7, 8, 3), np.uint8)), "cache_rgb 크기 불일치 거부")
    expect_raises(lambda: w_err.add_smart_object(None, "so", b"not an image", quad=dpsd.quad_from_rect([0, 0, 8, 8])),
                  "이미지 아닌 data 거부")
    expect_raises(lambda: w_err.add_smart_object(None, "so", small_png), "quad 누락 거부")

    # ─────────────────────────────────────────────────────────────────
    # U4. PSDWriter 왕복 (합성 문서) + 읽기 함수
    # ─────────────────────────────────────────────────────────────────
    section("U4 PSDWriter round-trip + read functions on synthetic PSD")
    W, H = 320, 256
    base = np.zeros((192, 256, 3), np.uint8)
    base[..., 0] = (np.arange(256)[None, :] % 256).astype(np.uint8)
    base[..., 1] = (np.arange(192)[:, None] + 40).astype(np.uint8)
    base[..., 2] = 77
    clean = np.zeros((40, 60, 4), np.uint8)
    clean[..., :3] = (10, 200, 30)
    clean[..., 3] = np.tile(np.linspace(0, 255, 60).astype(np.uint8), (40, 1))  # 부분 투명
    p1 = np.zeros((64, 64, 3), np.uint8)
    p1[...] = (220, 20, 20)
    p1_mask = np.zeros((64, 64), np.uint8)
    p1_mask[:, :32] = 255
    so_src1 = rng.integers(0, 256, (256, 256, 3), dtype=np.uint8)
    so_src2 = rng.integers(0, 256, (128, 128, 3), dtype=np.uint8)
    so_src3 = rng.integers(0, 256, (200, 300, 3), dtype=np.uint8)
    d1, d2, d3 = png_bytes(so_src1), png_bytes(so_src2), png_bytes(so_src3)
    gmask = np.zeros((64, 64), np.uint8)
    gmask[8:56, 8:56] = 200
    so2_mask = np.full((20, 20), 255, np.uint8)
    outline = np.zeros((32, 32, 4), np.uint8)
    outline[..., 1] = 255
    outline[[0, 1, -2, -1], :, 3] = 255
    outline[:, [0, 1, -2, -1], 3] = 255
    CROPS_SYN = [
        {"name": "A_크롭", "rect": [40, 40, 104, 104]},
        {"name": "B_크롭", "rect": [120, 40, 152, 72]},
        {"name": "C_dup", "rect": [200, 100, 232, 132]},
        {"name": "D_밖", "rect": [260, 200, 324, 264]},
        {"name": "E_없음", "rect": [60, 150, 92, 182]},
    ]
    qa = dpsd.quad_from_rect(CROPS_SYN[0]["rect"])
    qb = dpsd.quad_from_rect(CROPS_SYN[1]["rect"])
    q3 = []
    for px, py in [(-30, -20), (30, -20), (30, 20), (-30, 20)]:
        a3 = math.radians(-7.0)
        q3 += [216 + px * math.cos(a3) - py * math.sin(a3), 116 + px * math.sin(a3) + py * math.cos(a3)]

    t0 = time.time()
    wr = dpsd.PSDWriter((W, H))
    L_base = wr.add_pixel(None, "I2I_base", base, 32, 32)
    L_clean = wr.add_pixel(None, "non-gpt 1차수정", clean, 100, 100)
    g_ci = wr.add_group(None, "크롭이미지", visible=False)
    g_a = wr.add_group(g_ci, "A_크롭", visible=False)
    wr.add_pixel(g_a, "p1 레드", p1, 40, 40, mask={"arr": p1_mask, "left": 40, "top": 40, "bg": 0})
    wr.add_pixel(g_a, "숨김 블루", np.full((64, 64, 3), (0, 0, 255), np.uint8), 40, 40, visible=False)
    g_guide = wr.add_group(g_a, "영수증가이드")
    wr.add_pixel(g_guide, "가이드 획", np.full((10, 10, 3), (0, 255, 0), np.uint8), 50, 50)
    wr.add_pixel(g_ci, "B_크롭", np.full((32, 32, 3), (250, 240, 10), np.uint8), 120, 40)
    wr.add_pixel(g_ci, "C_dup", np.full((32, 32, 3), (255, 0, 255), np.uint8), 10, 10)
    wr.add_pixel(g_ci, "C_dup", np.full((32, 32, 3), (0, 255, 255), np.uint8), 200, 100)
    g_edit = wr.add_group(None, "03.수정")
    g_ca = wr.add_group(g_edit, "A_크롭", mask={"arr": gmask, "left": 40, "top": 40, "bg": 0}, open_folder=False)
    L_so1 = wr.add_smart_object(g_ca, "A_크롭 · 빵 · c01 ★", d1, quad=qa, label="green")
    L_so2 = wr.add_smart_object(g_edit, "B 후보 c02", d2, src_size=(128, 128), quad=qb, visible=False, label="red",
                                mask={"arr": so2_mask, "left": 126, "top": 46, "bg": 255})
    L_so3 = wr.add_smart_object(g_edit, "회전 후보 ☆", d3, quad=q3, label="yellow")
    L_out = wr.add_pixel(g_edit, "외곽선", outline, 120, 40, visible=False)
    syn_path = OUT / "unit_roundtrip.psd"
    res = wr.save(str(syn_path), verify=True)
    print("  save:", {k: v for k, v in res.items() if k != "warnings"}, "build+save %.2fs" % (time.time() - t0))
    check(res["problems"] == [], f"verify_saved problems {res['problems']}")
    check(res["smart_objects"] == 3 and res["bytes"] > 0, "save 반환값")
    check(not os.path.exists(str(syn_path) + ".tmp"), "tmp 파일 정리")
    deep = dpsd.verify_saved(str(syn_path), deep=True)
    check(deep == [], f"verify_saved deep {deep}")

    # 원시 검사: lnk2 꼬리
    rw = dpsd.raw_walk(str(syn_path), want_raw=(b"lnk2",))
    gl = {b["key"]: b for b in rw["global"]}
    items, consumed = dpsd.lnk2_items(gl[b"lnk2"]["raw"])
    tails = [dpsd.lnk2_item_tail(it["body"]) for it in items]
    cids = {t[-74:-2].decode("utf-16-be") for t in tails}
    check(len(items) == 3 and consumed == gl[b"lnk2"]["len"], f"lnk2 항목 3개, 소비량 일치 ({len(items)})")
    check(all(len(t) == 117 and b"contentID" in t for t in tails), f"lnk2 꼬리 117B {[len(t) for t in tails]}")
    check(len(cids) == 3 and dpsd._ZERO_UUID not in cids, "contentID 고유(템플릿 0-uuid 아님)")
    check(all(it["ver"] == 8 for it in items), "lnk2 항목 v8")

    # 재열기
    rq = PSDImage.open(str(syn_path))
    byname = {}
    for x in rq.descendants():
        byname.setdefault(x.name, []).append(x)
    check(rq.depth == 8 and rq.size == (W, H), "문서 8-bit / 크기")
    for nm in ("non-gpt 1차수정", "크롭이미지", "A_크롭", "03.수정", "A_크롭 · 빵 · c01 ★", "B 후보 c02", "회전 후보 ☆", "외곽선",
               "영수증가이드", "p1 레드", "숨김 블루"):
        check(nm in byname, f"한글/특수문자 이름 왕복: {nm}")
    sos = [x for x in rq.descendants() if x.kind == "smartobject"]
    check(len(sos) == 3, f"SO 3개 ({len(sos)})")
    so1 = byname["A_크롭 · 빵 · c01 ★"][0]
    so2 = byname["B 후보 c02"][0]
    so3 = byname["회전 후보 ☆"][0]
    check(so1.smart_object.data == d1 and so2.smart_object.data == d2 and so3.smart_object.data == d3, "임베드 원본 bytes 동일")
    check(so1.smart_object.filetype == "png" and so1.smart_object.filename.endswith(".png"), "filetype png / 파일명")
    check([float(v) for v in so1.smart_object.transform_box] == qa, "quad(transform_box) == 입력")
    check(all(abs(a - b) < 1e-9 for a, b in zip(so3.smart_object.transform_box, q3)), "affine quad 왕복")
    check(so1.visible and not so2.visible and not byname["외곽선"][0].visible and not byname["숨김 블루"][0].visible, "숨김 왕복")
    check(so1.sheet_color == SheetColorType.GREEN and so2.sheet_color == SheetColorType.RED
          and so3.sheet_color == SheetColorType.YELLOW, "라벨 왕복")
    check(not [x for x in byname["A_크롭"] if x.parent.name == "크롭이미지"][0].visible, "숨김 그룹 왕복")
    ga_edit = [x for x in byname["A_크롭"] if x.parent.name == "03.수정"][0]
    check(ga_edit.has_mask() and tuple(ga_edit.mask.bbox) == (40, 40, 104, 104)
          and np.array_equal(np.asarray(ga_edit.mask.topil()), gmask), "그룹 마스크 왕복(bbox, 픽셀)")
    check(so2.has_mask() and so2._record.mask_data.background_color == 255 and tuple(so2.mask.bbox) == (126, 46, 146, 66),
          "레이어 마스크 bg=255 왕복")
    lo = byname["외곽선"][0]
    check(np.array_equal(np.asarray(lo.topil().convert("RGBA"))[..., 3], outline[..., 3]) and not lo.has_mask(),
          "RGBA 픽셀: transparency 채널 왕복, user mask 없음(α² 방지)")
    check(not byname["I2I_base"][0].has_mask(), "RGB 픽셀 레이어 user mask 없음")
    c1 = np.asarray(so1.topil().convert("RGB"))
    check(np.array_equal(c1, np.asarray(Image.fromarray(so_src1).resize((64, 64), Image.LANCZOS))), "SO 픽셀 캐시 = LANCZOS")
    a3 = np.asarray(so3.topil().convert("RGBA"))[..., 3]
    check(a3[0, 0] == 0 and a3[a3.shape[0] // 2, a3.shape[1] // 2] == 255, "affine SO 캐시 투명 모서리")
    check(rq.topil() is not None and np.asarray(rq.topil().convert("RGB"))[5, 5].tolist() == [255, 255, 255],
          "병합 프리뷰(흰 배경) 저장")
    pl = so1.smart_object._placed_layer
    from psd_tools.psd.linked_layer import LinkedLayer as _LL  # noqa: E402
    check(_LL.write.__qualname__ == "LinkedLayer.write" and dpsd._LinkedLayerV8 is not _LL, "LinkedLayer.write 전역 교체 없음")
    _li = _LL.frombytes(items[0]["body"])
    check(len(_li.tobytes()) + 117 == len(items[0]["body"]), "기본 LinkedLayer 직렬화는 꼬리 없음(서브클래스만 꼬리)")
    check(pl.uuid.decode() == so1.smart_object.unique_id, "PlLd uuid == SoLd Idnt")

    # 읽기 함수 — 합성 문서
    rq_open = dpsd.open_psd(str(syn_path))
    expect_raises(lambda: dpsd.open_psd(str(OUT / "없는파일.psd")), "없는 PSD 거부")
    expect_raises(lambda: dpsd.read_crop_rects(rq_open), "크롭영역 그룹 없음 → ValueError")
    b_rgba, b_info = dpsd.read_base_rgba(rq_open)
    exp = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    exp.paste(Image.fromarray(base).convert("RGBA"), (32, 32))
    cl_win = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    cl_win.paste(Image.fromarray(clean, "RGBA"), (100, 100))
    exp = np.asarray(Image.alpha_composite(exp, cl_win))
    check(b_info["method"] == "base+clean" and b_info["work_rect"] == [32, 32, 288, 224], f"read_base info {b_info}")
    check(np.array_equal(b_rgba, exp), "read_base_rgba = 베이스 위 클린플레이트 alpha_composite")
    b_rgb, b_info2 = dpsd.read_base(rq_open)
    check(b_rgb.shape == (H, W, 3) and np.array_equal(b_rgb, exp[..., :3]), "read_base RGB")
    b_only, b_info3 = dpsd.read_base(rq_open, clean_plate_layer="none")
    check(b_info3["method"] == "base" and np.array_equal(b_only[32:224, 32:288], base), "clean_plate_layer=none")
    expect_raises(lambda: dpsd.read_base(rq_open, base_layer="없는레이어"), "없는 베이스 레이어 거부")

    cs_list = dpsd.crop_sources(rq_open, CROPS_SYN, b_rgb)
    check(isinstance(cs_list, list) and [s["name"] for s in cs_list] == [c["name"] for c in CROPS_SYN],
          "crop_sources: crops 와 같은 순서의 list")
    cs = {c["name"]: s for c, s in zip(CROPS_SYN, cs_list)}
    A = cs["A_크롭"]
    win = exp[40:104, 40:104].copy()
    exp_a = win.copy()
    exp_a[:, :32, :3] = (220, 20, 20)
    exp_a[:, :32, 3] = 255
    check(A["kind"] == "group_composite" and np.array_equal(A["rgba"][..., :3], exp_a[..., :3])
          and (A["rgba"][..., 3] == 255).all(), "A: 마스크 반쪽 레드 + 나머지 베이스, 숨김/가이드 제외")
    check(any("가이드" in s for s in A["warnings"]), f"A: 가이드 그룹 제외 경고 {A['warnings']}")
    check(cs["B_크롭"]["kind"] == "group_composite" and (cs["B_크롭"]["rgba"][..., :3] == (250, 240, 10)).all(), "B: 단일 레이어")
    C = cs["C_dup"]
    check((C["rgba"][..., :3] == (0, 255, 255)).all() and any("같은 이름" in s for s in C["warnings"]), "C: 이름 중복 → rect IoU 최대 노드")
    Dd = cs["D_밖"]
    opaque = (288 - 260) * (224 - 200)
    check(Dd["kind"] == "base_crop" and Dd["rgba"].shape == (64, 64, 4) and int((Dd["rgba"][..., 3] == 255).sum()) == opaque
          and abs(Dd["alpha_frac"] - (1 - opaque / 4096)) < 1e-9 and any("캔버스 밖" in s for s in Dd["warnings"]),
          f"D: 캔버스/작업영역 밖 알파 0 (alpha_frac {Dd['alpha_frac']:.4f})")
    check(cs["E_없음"]["kind"] == "base_crop" and np.array_equal(cs["E_없음"]["rgba"][..., :3], exp[150:182, 60:92, :3]),
          "E: 같은 이름 노드 없음 → base_crop")
    cs4 = dpsd.crop_sources(rq_open, CROPS_SYN, b_rgba)
    check(np.array_equal(cs4[0]["rgba"], A["rgba"]), "RGBA 베이스 입력도 같은 결과")
    # DP-1: 이름이 같은 크롭 둘(다른 rect) → 각자 자기 크기·자기 창의 소스(예전: dict[name] 이라 마지막 것을 둘 다 씀)
    dup = dpsd.crop_sources(rq_open, [{"name": "A_크롭", "rect": [40, 40, 104, 104]}, {"name": "A_크롭", "rect": [40, 40, 72, 72]}], b_rgb)
    check(len(dup) == 2 and dup[0]["rgba"].shape == (64, 64, 4) and dup[1]["rgba"].shape == (32, 32, 4)
          and np.array_equal(dup[1]["rgba"], dup[0]["rgba"][:32, :32]) and np.array_equal(dup[0]["rgba"], A["rgba"]),
          f"DP-1 같은 이름 크롭 2개 → 각자 rect 의 소스 {[d['rgba'].shape for d in dup]}")

    hv_warn: list = []
    hv = dpsd.harvest_results(rq_open, "03.수정", CROPS_SYN, warnings=hv_warn)
    check(len(hv) == 3 and [h["z_index"] for h in hv] == [0, 1, 2], f"harvest 3개 z순서 ({len(hv)})")
    h1, h2, h3 = hv
    check(h1["data"] == d1 and h1["filetype"] == "png" and h1["src_size"] == [256, 256] and h1["quad"] == qa, "harvest h1 data/quad")
    check(h1["crop_name"] == "A_크롭" and h1["crop_match"] == "group_name" and h1["group_path"] == ["03.수정", "A_크롭"],
          f"harvest h1 crop/group_path {h1['crop_name']} {h1['group_path']}")
    check(h1["mask"] is not None and h1["mask"]["owner"] == "group" and h1["mask"]["rect"] == [40, 40, 104, 104]
          and np.array_equal(h1["mask"]["arr"], gmask), "harvest h1 그룹 마스크 폴백")
    m2 = np.full((32, 32), 255, np.uint8)  # bg 255, 마스크 영역도 255
    check(h2["crop_name"] == "B_크롭" and h2["crop_match"] == "iou" and not h2["visible_effective"]
          and h2["mask"]["owner"] == "layer" and h2["mask"]["bg"] == 255 and np.array_equal(h2["mask"]["arr"], m2),
          "harvest h2 IoU 매칭/숨김/레이어 마스크 bg 반영")
    check(h3["crop_name"] is None and any("crop 매칭 실패" in s for s in h3["warnings"]) and h3["mask"] is None
          and all(abs(a - b) < 1e-9 for a, b in zip(h3["quad"], q3)), "harvest h3 회전 quad, 매칭 실패 경고")
    check([h["crop_index"] for h in hv] == [0, 1, None], f"harvest crop_index = crops 위치 {[h['crop_index'] for h in hv]}")
    hv_all = dpsd.harvest_results(rq_open, None, None)
    check(len(hv_all) == 3 and all(h["crop_name"] is None for h in hv_all), "harvest group=None, crops=None")
    expect_raises(lambda: dpsd.harvest_results(rq_open, "없는그룹", CROPS_SYN), "없는 결과 그룹 거부")
    # DP-1: 이름이 같은 크롭이 여럿 — 그룹 이름 매칭은 quad IoU 최대인 크롭, IoU 매칭은 index 로(예전: 이름 → 마지막 것)
    dup_crops = [{"name": "A_크롭", "rect": [200, 100, 232, 132]}, {"name": "A_크롭", "rect": [40, 40, 104, 104]},
                 {"name": "B_크롭", "rect": [120, 40, 152, 72]}, {"name": "B_크롭", "rect": [200, 200, 232, 232]}]
    hvd = dpsd.harvest_results(rq_open, "03.수정", dup_crops)
    check(hvd[0]["crop_index"] == 1 and hvd[0]["crop_match"] == "group_name" and hvd[0]["mask"]["rect"] == [40, 40, 104, 104]
          and any("크롭이 2개" in s for s in hvd[0]["warnings"]), f"DP-1 그룹 이름 중복 → IoU 최대 크롭(1) {hvd[0]['crop_index']}")
    check(hvd[1]["crop_index"] == 2 and hvd[1]["crop_match"] == "iou" and hvd[1]["mask"]["rect"][:2] == [120, 40],
          f"DP-1 IoU 매칭은 첫 중복 크롭(2) index {hvd[1]['crop_index']}")

    # ─────────────────────────────────────────────────────────────────
    # U5. 수정 회귀(작은 문서): 프리뷰 composite·RLE, 2GB 검사, JPEG SO 파일명, work_rect 자르기
    # ─────────────────────────────────────────────────────────────────
    section("U5 PSDWriter composite / 2GB guard / JPEG SO filename / work_rect clamp")
    comp = rng.integers(0, 256, (H, W, 3), dtype=np.uint8)
    wc = dpsd.PSDWriter((W, H))
    wc.add_pixel(None, "px", base, 32, 32)
    jpg_buf = io.BytesIO()
    Image.fromarray(so_src2).save(jpg_buf, "JPEG", quality=90)
    wc.add_smart_object(None, "jpg so", jpg_buf.getvalue(), filetype="jpg", quad=qb, filename="h_abc.jpg")
    expect_raises(lambda: wc.save(str(OUT / "unit_composite.psd"), composite=comp[:10]), "R2 composite 크기 오류 거부")
    resc = wc.save(str(OUT / "unit_composite.psd"), verify=True, composite=comp)
    rqc = PSDImage.open(str(OUT / "unit_composite.psd"))
    check(resc["problems"] == [] and np.array_equal(np.asarray(rqc.topil().convert("RGB")), comp)
          and dpsd.raw_walk(str(OUT / "unit_composite.psd"))["image_data_compression"] == 1,
          "R2 save(composite=…) → 병합 프리뷰 = 넘긴 그림, RLE(1)")
    soj = [x for x in rqc.descendants() if x.kind == "smartobject"][0].smart_object
    check(soj.filename == "h_abc.png" and soj.filetype == "png" and soj.data[:4] == b"\x89PNG",
          f"F3 JPEG SO 재인코딩 → lnk2 파일명 확장자도 .png ({soj.filename}, {soj.filetype})")
    check(dpsd.raw_walk(str(syn_path))["image_data_compression"] == 1, "R2 composite 없는 save 도 프리뷰 RLE")
    lim = dpsd._PSD_MAX_BYTES
    dpsd._PSD_MAX_BYTES = 1000  # 이 테스트 안에서만(모듈 상수) — 2GB 파일을 만들지 않고 한계 경로를 시험
    try:
        big_path = OUT / "unit_too_big.psd"
        if big_path.exists():
            big_path.unlink()
        expect_raises(lambda: wc.save(str(big_path), composite=comp), "F1 저장 크기 > 한계 → ValueError", contains="2GB")
        check(not big_path.exists() and not os.path.exists(str(big_path) + ".tmp"), "F1 한계 초과 파일·tmp 남기지 않음")
        check(any("2^31-1" in p for p in dpsd.verify_saved(str(OUT / "unit_composite.psd"))), "F1 verify_saved 파일 크기 검사")
    finally:
        dpsd._PSD_MAX_BYTES = lim
    wo = dpsd.PSDWriter((320, 256))
    wo.add_pixel(None, "I2I_base", np.full((336, 400, 3), 90, np.uint8), -40, -40)
    wo.save(str(OUT / "unit_base_overflow.psd"))
    b_ov, i_ov = dpsd.read_base(dpsd.open_psd(str(OUT / "unit_base_overflow.psd")), clean_plate_layer="none")
    check(i_ov["work_rect"] == [0, 0, 320, 256] and any("캔버스를 넘음" in w for w in i_ov["warnings"]) and (b_ov == 90).all(),
          f"R4 베이스 bbox 가 캔버스를 넘으면 work_rect 를 자름 {i_ov['work_rect']}")
    b_cmp, i_cmp = dpsd.read_base_rgba(rq_open, base_layer="none")
    check(i_cmp["method"] == "composite" and i_cmp["work_rect"] == [0, 0, W, H] and b_cmp[5, 5, 3] == 0
          and np.array_equal(b_cmp[150:182, 60:92], exp[150:182, 60:92])
          and np.array_equal(b_cmp[104:140, 104:160], exp[104:140, 104:160]),
          f"R3 base_layer=none → 보이는 레이어 수동 합성(베이스+클린플레이트 영역 일치) {i_cmp['method']}")


# ═════════════════════════════════════════════════════════════════
# R1–R4, RW, RX. psd 회귀 (03 / 05 PSD)
# ═════════════════════════════════════════════════════════════════
def psd_regression():
    OUT = OUT_ROOT / "psd"
    summary: dict = {}
    # ── R1 ──
    section("R1 read_crop_rects(03) vs crops_03.json")
    t0 = time.time()
    psd03 = psd_cached(PSD03)
    crops03 = dpsd.read_crop_rects(psd03)
    ref = json.load(open(CROPS03_JSON, encoding="utf-8"))["crops"]
    same = sum(1 for a, b in zip(crops03, ref) if a["name"] == b["name"] and a["rect"] == b["rect"])
    print("  %d/%d rect 일치, sources %s, %.2fs" % (same, len(ref), sorted({c["rect_source"] for c in crops03}), time.time() - t0))
    check(len(crops03) == 22 and same == 22, f"R1 22/22 rect 일치 ({same}/{len(crops03)})")
    check(all(c["rect_source"] == "vector" for c in crops03), "R1 rect_source 전부 vector")
    summary["R1"] = f"{same}/{len(ref)}"
    psd05 = psd_cached(PSD05)
    crops05 = dpsd.read_crop_rects(psd05)
    m05 = {c["name"]: c["rect"] for c in crops05}
    same05 = sum(1 for c in crops03 if m05.get(c["name"]) == c["rect"])
    print("  05 의 02.크롭영역: %d개, 03 과 rect 일치 %d" % (len(crops05), same05))
    check(len(crops05) == 22 and same05 == 22, f"R1b 05 rect == 03 rect ({same05})")
    # R6: 크롭 영역 그룹을 하위 그룹으로 정리해도 shape 를 읽음 (새로 연 03 에서 메모리로만 옮김, 저장하지 않음)
    from psd_tools.api.layers import Group  # noqa: E402
    psd03n = dpsd.open_psd(str(PSD03))
    grp = dpsd._find_group(psd03n, dpsd.CROP_GROUP_NAMES)
    moved = [x for x in grp if x.kind == "shape"][:2]
    sub = Group.new(grp, "머리")
    for x in moved:
        grp.remove(x)
        sub.append(x)
    nested = dpsd.read_crop_rects(psd03n)
    check(len(nested) == 22 and {c["name"] for c in nested} == {c["name"] for c in crops03}
          and all(c["rect"] == next(r["rect"] for r in crops03 if r["name"] == c["name"]) for c in nested),
          f"R6 하위 그룹 안 shape 도 읽음 ({len(nested)}/22, 옮긴 것 {[x.name for x in moved]})")

    # ── R2 ──
    section("R2 crop_sources(03) vs 03.크롭이미지 PNG")
    t0 = time.time()
    base03, binfo03 = dpsd.read_base(psd03)
    print("  read_base:", {k: v for k, v in binfo03.items() if k != "warnings"}, "%.2fs" % (time.time() - t0))
    check(binfo03["method"] == "base+clean" and binfo03["work_rect"] == [256, 256, 3328, 4352], "R2 read_base(03) 방법/작업영역")
    t1 = time.time()
    srcs = {c["name"]: s for c, s in zip(crops03, dpsd.crop_sources(psd03, crops03, base03))}  # 03 크롭 이름은 고유
    print("  crop_sources %.2fs" % (time.time() - t1))
    n_cmp = n_exact = 0
    rows = []
    for c in crops03:
        fn = CROP_PNG_DIR / (c["name"] + ".png")
        if not fn.exists():
            continue
        n_cmp += 1
        ps = np.asarray(Image.open(fn).convert("RGBA")).astype(int)
        mine = srcs[c["name"]]["rgba"].astype(int)
        if ps.shape != mine.shape:
            rows.append((c["name"], "shape", ps.shape, mine.shape))
            continue
        both = (ps[..., 3] == 255) & (mine[..., 3] == 255)
        diff = np.abs(ps[..., :3] - mine[..., :3]).max(2)
        mx = int(diff[both].max()) if both.any() else -1
        mean = float(diff[both].mean()) if both.any() else -1.0
        amis = float(((ps[..., 3] > 0) != (mine[..., 3] > 0)).mean())
        exact = mx == 0
        n_exact += exact
        rows.append((c["name"], srcs[c["name"]]["kind"], round(float(both.mean()) * 100, 1), mx, round(mean, 3),
                     round(amis * 100, 2), round(srcs[c["name"]]["alpha_frac"], 3), "EXACT" if exact else "",
                     "; ".join(srcs[c["name"]]["warnings"])[:90]))
    print("  %-28s %-16s %6s %4s %7s %6s %6s %s" % ("crop", "kind", "both%", "max", "mean", "amis%", "afrac", ""))
    for r in rows:
        print("  %-28s %-16s %6s %4s %7s %6s %6s %-5s %s" % r)
    print("  비트 일치(both-opaque maxdiff 0): %d/%d" % (n_exact, n_cmp))
    check(n_cmp == 19, f"R2 비교 대상 19개 ({n_cmp})")
    check(n_exact >= 15, f"R2 비트 일치 ≥15/19 ({n_exact}/{n_cmp})")
    a7 = srcs["007_머리장식-오른쪽"]["alpha_frac"]
    check(abs(a7 - 0.1836) < 0.01, f"R2 007-오른쪽 여백 투명 비율 ≈18.4% ({a7:.4f})")
    summary["R2"] = f"{n_exact}/{n_cmp}"
    # R3(수정): base_layer=none → 병합 이미지(psd.topil, 크롭 박스 획이 구워짐) 대신 보이는 레이어 수동 합성(shape 제외)
    t1 = time.time()
    bn, bninfo = dpsd.read_base(psd03, base_layer="none")
    wx0, wy0, wx1, wy1 = binfo03["work_rect"]
    same_in = np.array_equal(bn[wy0:wy1, wx0:wx1], base03[wy0:wy1, wx0:wx1])
    ring = []
    for c in crops03:
        x0, y0, x1, y1 = c["rect"]
        if x0 >= wx0 and y0 >= wy0 and x1 <= wx1 and y1 <= wy1:
            ring.append(float(np.abs(bn[y0:y0 + 4, x0:x1].astype(np.float32) - base03[y0:y0 + 4, x0:x1]).mean()))
    note(f"R3 base_layer=none: method {bninfo['method']} work_rect {bninfo['work_rect']}, 작업영역 == 정상 베이스 {same_in}, "
         f"크롭 테두리 4px 평균 차 {max(ring):.2f} (병합 이미지였다면 ~196), {time.time() - t1:.1f}s")
    check(bninfo["method"] == "composite" and same_in and max(ring) == 0.0
          and any("shape" in w for w in bninfo["warnings"]), "R3 base_layer=none: 작업영역 = 정상 베이스, 크롭 박스 획 없음")

    # ── R3 ──
    section("R3 harvest_results(05, '03.수정')")
    t0 = time.time()
    hw: list = []
    hv05 = dpsd.harvest_results(psd05, "03.수정", crops03, warnings=hw)
    print("  harvest %.2fs, 문서 경고 %s" % (time.time() - t0, hw))
    cats: dict = {}
    for h in hv05:
        cats[h["category"]] = cats.get(h["category"], 0) + 1
    rect_of = {c["name"]: c["rect"] for c in crops03}
    n_quad_eq = 0
    n_png = cats.get("png", 0)
    print("  %-3s %-34s %-16s %-24s %-11s %-10s %-6s %s" % ("z", "layer", "category", "crop", "match", "src", "quad=", "mask"))
    for h in hv05:
        qb_ = dpsd.quad_bbox(h["quad"])
        eq = h["crop_name"] is not None and dpsd.quad_kind(h["quad"]) == "rect_int" and qb_ == rect_of[h["crop_name"]]
        if eq and h["category"] == "png":
            n_quad_eq += 1
        mk = h["mask"]
        print("  %-3d %-34s %-16s %-24s %-11s %-10s %-6s %s" % (
            h["z_index"], h["layer"][:34], h["category"], str(h["crop_name"])[:24], h["crop_match"],
            "%dx%d" % tuple(h["src_size"]), "Y" if eq else "-",
            "-" if mk is None else "%s %s bg%d" % (mk["owner"], mk["mask_bbox"], mk["bg"])))
    print("  카테고리 %s, png quad==crop rect %d/%d" % (cats, n_quad_eq, n_png))
    names = {h["layer"]: h for h in hv05}
    check(n_png >= 20, f"R3 png SO ≥20 ({n_png})")
    check(names.get("030_팔매듭", {}).get("rasterized") and names.get("레이어 24", {}).get("rasterized"),
          "R3 중첩 PSB 030 / 레이어 24 래스터화")
    check(names["030_팔매듭"]["src_size"] == [2048, 2049] and names["레이어 24"]["src_size"] == [1280, 1536],
          f"R3 중첩 PSB 크기 {names['030_팔매듭']['src_size']} {names['레이어 24']['src_size']}")
    gpt_like = cats.get("png", 0) + sum(1 for h in hv05 if h["category"] == "nested_psd"
                                        and h["layer"] in ("030_팔매듭", "레이어 24"))
    check(22 <= gpt_like <= 24, f"R3 GPT 결과(png + 중첩 PSB 030/레이어24) 22~24 ({gpt_like})")
    check(len(hv05) == 26, f"R3 전체 SO 26 (png 22 + PSB 4: 030, 레이어24, 레이어12, 생성형 채우기2) ({len(hv05)})")
    check(n_quad_eq >= 19, f"R3 png quad == crop rect ≥19 ({n_quad_eq}/{n_png})")
    check(all(h["crop_name"] is not None for h in hv05), "R3 모든 SO crop 매칭")
    check(names["레이어 24"]["crop_name"] == "017_팔리본" and names["레이어 24"]["mask"]["owner"] == "group",
          "R3 레이어 24 → 017, 그룹 마스크 폴백(owner=group)")
    check(names["레이어 12"]["mask"] is None, "R3 레이어 12 마스크 없음")
    check(names["2026-09-11_14-24-16_img2img_3"]["crop_name"] == "010_손가락반창고"
          and names["2026-09-11_14-24-16_img2img_3"]["crop_match"] == "iou", "R3 IoU 매칭(10.손가락 그룹)")
    check(names["001_바보털E"]["crop_name"] == "001_바보털", "R3 001 손 이동 레이어 IoU 매칭")
    check(names["생성형 채우기 2"]["category"] == "generative_fill", "R3 생성형 채우기 분류")
    n_layer_mask = sum(1 for h in hv05 if h["mask"] is not None and h["mask"]["owner"] == "layer")
    n_group_mask = sum(1 for h in hv05 if h["mask"] is not None and h["mask"]["owner"] == "group")
    print("  마스크: layer %d, group %d, 없음 %d" % (n_layer_mask, n_group_mask, sum(1 for h in hv05 if h["mask"] is None)))
    check(n_layer_mask == 24 and n_group_mask == 1, f"R3 마스크 layer 24 / group 1 ({n_layer_mask}/{n_group_mask})")
    # 마스크 배열 독립 계산과 비교
    so_layers = [x for x in [g for g in psd05.descendants() if g.is_group() and g.name == "03.수정"][0].descendants()
                 if x.kind == "smartobject"]
    check(len(so_layers) == len(hv05), "R3 레이어 순서 대응")
    bad_mask = 0
    for h, lyr in zip(hv05, so_layers):
        if h["mask"] is None or h["mask"]["owner"] != "layer":
            continue
        x0, y0, x1, y1 = h["mask"]["rect"]
        full = Image.new("L", (x1 - x0, y1 - y0), lyr.mask.background_color)
        mi = lyr.mask.topil()
        if mi is not None:
            full.paste(mi, (lyr.mask.left - x0, lyr.mask.top - y0))
        if not np.array_equal(np.asarray(full), h["mask"]["arr"]):
            bad_mask += 1
    check(bad_mask == 0, f"R3 레이어 마스크 배열 = 독립 계산 ({bad_mask} 불일치)")
    png_ok = 0
    for h in hv05:
        try:
            im = Image.open(io.BytesIO(h["data"]))
            png_ok += int(list(im.size) == h["src_size"])
        except Exception:
            pass
    check(png_ok == len(hv05), f"R3 data 디코드·src_size 일치 {png_ok}/{len(hv05)}")
    check(not any("워프" in w for h in hv05 for w in h["warnings"]), "F4 05 의 warpCustom 메시는 전부 항등 → 워프 경고 없음")
    check([h["crop_index"] for h in hv05] == [next(i for i, c in enumerate(crops03) if c["name"] == h["crop_name"]) for h in hv05],
          "R3 crop_index ↔ crop_name 일치")
    # F4: SO 워프(메시)를 실제로 휘면 경고 (새로 연 05 에서 메모리로만 수정)
    psd05w = dpsd.open_psd(str(PSD05))
    lw = next(x for x in psd05w.descendants() if x.kind == "smartobject" and x.name == "001_바보털E")
    wp = lw.smart_object.warp
    mp = wp[b"customEnvelopeWarp"][b"meshPoints"]
    side = int(round(len(mp[b"Hrzn"].values) ** 0.5))
    span = float(wp[b"bounds"][b"Rght"]) - float(wp[b"bounds"][b"Left"])
    for i in range(1, side - 1):
        for j in range(1, side - 1):
            mp[b"Hrzn"].values[i * side + j] = float(mp[b"Hrzn"].values[i * side + j]) + 0.25 * span
    hvw = {h["layer"]: h for h in dpsd.harvest_results(psd05w, "03.수정", crops03)}
    ww = [w for w in hvw["001_바보털E"]["warnings"] if "워프" in w]
    check(len(ww) == 1 and sum(1 for h in hvw.values() for w in h["warnings"] if "워프" in w) == 1,
          f"F4 휜 SO 워프 → 그 레이어만 경고 1개 {ww}")
    summary["R3"] = f"SO {len(hv05)} {cats}, quad==rect {n_quad_eq}/{n_png}, mask layer {n_layer_mask}/group {n_group_mask}"

    # ── R4 ──
    section("R4 SO 배치 캐시(render_so_cache) vs 05 SO 렌더 픽셀")
    t0 = time.time()
    maes = []
    for h, lyr in zip(hv05, so_layers):
        if h["category"] != "png" or dpsd.quad_kind(h["quad"]) != "rect_int":
            continue
        rgb_c, _, lc, tc = dpsd.render_so_cache(Image.open(io.BytesIO(h["data"])), h["quad"])
        hh, ww = rgb_c.shape[:2]
        win = Image.new("RGBA", (ww, hh), (0, 0, 0, 0))
        win.paste(lyr.topil().convert("RGBA"), (lyr.left - lc, lyr.top - tc))
        ps = np.asarray(win).astype(np.float32)
        inner = np.zeros((hh, ww), bool)
        inner[2:-2, 2:-2] = True
        inner &= ps[..., 3] == 255
        mae = float(np.abs(ps[..., :3] - rgb_c.astype(np.float32))[inner].mean())
        maes.append((h["layer"], mae))
    n_ok = sum(1 for _, m in maes if m <= 0.25)
    for nm, m in maes:
        print("  %-40s MAE %.3f %s" % (nm[:40], m, "" if m <= 0.25 else "  (>0.25)"))
    print("  MAE ≤0.25: %d/%d, %.1fs" % (n_ok, len(maes), time.time() - t0))
    check(n_ok >= 19, f"R4 배치 MAE ≤0.25 ≥19 ({n_ok}/{len(maes)})")
    summary["R4"] = f"{n_ok}/{len(maes)} (max ok MAE {max(m for _, m in maes if m <= 0.25):.3f})"

    # ── RW ──
    section("RW 풀캔버스 SO PSD 쓰기 (05 데이터) → verify / 재열기")
    t0 = time.time()
    Wc, Hc = psd05.size
    wf = dpsd.PSDWriter((Wc, Hc))
    wr_ = binfo03["work_rect"]
    wf.add_pixel(None, "00.Base", base03[wr_[1]:wr_[3], wr_[0]:wr_[2]], wr_[0], wr_[1])
    g_out = wf.add_group(None, "02.크롭영역", visible=False, open_folder=False)
    for c in crops03:
        x0, y0, x1, y1 = c["rect"]
        o = np.zeros((y1 - y0, x1 - x0, 4), np.uint8)
        o[..., 1] = 255
        o[:4, :, 3] = 255
        o[-4:, :, 3] = 255
        o[:, :4, 3] = 255
        o[:, -4:, 3] = 255
        wf.add_pixel(g_out, c["name"], o, x0, y0)
    g_fix = wf.add_group(None, "03.수정")
    plan = []
    want = {"2026-09-11_16-59-23_edit_2": "008_목부분", "006_머리태슬-오른쪽": "006_머리태슬-오른쪽",
            "002_토스트-머리장식_right_빵": "002_토스트-머리장식_right", "레이어 24": "017_팔리본"}
    for h in hv05:
        if h["layer"] in want:
            plan.append(h)
    written = []
    for h in plan:
        cg = wf.add_group(g_fix, h["crop_name"])
        tg = wf.add_group(cg, "타깃 A", mask=None if h["mask"] is None else
                          {"arr": h["mask"]["arr"], "left": h["mask"]["rect"][0], "top": h["mask"]["rect"][1],
                           "bg": h["mask"]["bg"]})
        nm = f"{h['crop_name']} · 타깃 A · c01 ★"
        wf.add_smart_object(tg, nm, h["data"], src_size=h["src_size"], quad=h["quad"], label="green")
        written.append((nm, h))
    # 숨김 대안(Bcut) + 정합 보정 affine 예시(회전 1.1°, 007-왼쪽)
    bcut = GPT_DIR / "Bcut" / "006_머리태슬-오른쪽.png"
    g006 = [x for x in g_fix if x.name == "006_머리태슬-오른쪽"][0]
    if bcut.exists():
        bdata = bcut.read_bytes()
        wf.add_smart_object(g006, "006_머리태슬-오른쪽 · 타깃 A · c02", bdata,
                            quad=dpsd.quad_from_rect(rect_of["006_머리태슬-오른쪽"]), visible=False, label="red")
    h7 = names["007_머리장식-왼쪽"]
    r7 = rect_of["007_머리장식-왼쪽"]
    w7, h7h = r7[2] - r7[0], r7[3] - r7[1]
    th_ = math.radians(1.1)
    ccx, ccy = (w7 - 1) / 2.0, (h7h - 1) / 2.0
    cos_, sin_ = math.cos(th_), math.sin(th_)
    M7 = [[cos_, -sin_, ccx - cos_ * ccx + sin_ * ccy + 1.5], [sin_, cos_, ccy - sin_ * ccx - cos_ * ccy]]
    q7 = dpsd.quad_from_rect(r7, M7)
    g7 = wf.add_group(g_fix, "007_머리장식-왼쪽")
    wf.add_smart_object(g7, "007_머리장식-왼쪽 · 정합 affine(rot 1.1°) ★", h7["data"], quad=q7, label="green",
                        mask={"arr": h7["mask"]["arr"], "left": h7["mask"]["rect"][0], "top": h7["mask"]["rect"][1],
                              "bg": h7["mask"]["bg"]})
    full_path = OUT / "rw_full_so.psd"
    resf = wf.save(str(full_path), verify=True)
    print("  save:", {k: v for k, v in resf.items() if k != "warnings"}, "총 %.1fs" % (time.time() - t0))
    check(resf["problems"] == [], f"RW verify_saved problems {resf['problems']}")
    rf = PSDImage.open(str(full_path))
    rsos = {x.name: x for x in rf.descendants() if x.kind == "smartobject"}
    check(len(rsos) == len(written) + 2, f"RW SO 개수 {len(rsos)} == {len(written) + 2}")
    ok_emb = ok_q = ok_m = 0
    for nm, h in written:
        x = rsos.get(nm)
        if x is None:
            continue
        ok_emb += int(x.smart_object.data == h["data"] and list(Image.open(io.BytesIO(x.smart_object.data)).size) == h["src_size"])
        ok_q += int([float(v) for v in x.smart_object.transform_box] == h["quad"])
        if h["mask"] is not None:
            tgp = x.parent
            ok_m += int(tgp.has_mask() and list(tgp.mask.bbox) == h["mask"]["rect"]
                        and np.array_equal(np.asarray(tgp.mask.topil()), h["mask"]["arr"]))
        else:
            ok_m += 1
    check(ok_emb == len(written), f"RW 임베드 원본/크기 동일 {ok_emb}/{len(written)}")
    check(ok_q == len(written), f"RW quad 동일 {ok_q}/{len(written)}")
    check(ok_m == len(written), f"RW 타깃 그룹 마스크(bbox·픽셀) {ok_m}/{len(written)}")
    x7 = rsos["007_머리장식-왼쪽 · 정합 affine(rot 1.1°) ★"]
    check(all(abs(a - b) < 1e-9 for a, b in zip(x7.smart_object.transform_box, q7)), "RW affine quad 왕복")
    rwf = dpsd.raw_walk(str(full_path), want_raw=(b"lnk2",))
    itf, _ = dpsd.lnk2_items({b["key"]: b for b in rwf["global"]}[b"lnk2"]["raw"])
    check(all(len(dpsd.lnk2_item_tail(it["body"])) == 117 for it in itf) and len(itf) == len(rsos), "RW lnk2 꼬리 117B 전부")
    summary["RW"] = f"{full_path.name} {resf['bytes'] / 1e6:.1f}MB {resf['seconds']:.1f}s SO {len(rsos)} problems {len(resf['problems'])}"

    # ── RX ──
    section("RX SO 템플릿 상수 재현 (extract_design_patch_so_template)")
    import extract_design_patch_so_template as ext  # noqa: E402
    rx = ext.extract(str(PSD05))
    for line in rx["report"]:
        print("  -", line)
    check(rx["ok"], "RX 추출 검사(재구성 lnk2 항목 == 05 원본 bytes 등)")
    check(ext.compare_with_module(rx["consts"]) == [], "RX 모듈 상수 == 새로 추출한 상수")
    check(ext.check_clean(rx["consts"]) == [], f"RX 상수에 경로/개인정보 없음 {ext.check_clean(rx['consts'])}")

    section("요약")
    for k, v in summary.items():
        print("  %s: %s" % (k, v))
    return {"psd03": psd03, "psd05": psd05, "crops03": crops03, "hv05": hv05}


# ═════════════════════════════════════════════════════════════════
# R5–R7, G. analysis 회귀 — 05 PSD 를 psd 모듈로 읽어 입력을 만든다
# (base = I2I_base + 00.Base_마스크용, 후보 = 03.수정 의 GPT png 22개 + 중첩 PSB 030, 손 마스크 = 수확 마스크)
# ═════════════════════════════════════════════════════════════════
def analysis_regression(psd05):
    OUT_DIR = str(OUT_ROOT / "analysis")
    section("R5–R7 analysis 회귀 (05 PSD → psd 모듈)")
    t0 = time.time()
    base_full, _binfo = dpsd.read_base_rgba(psd05)
    rects = {c["name"]: c["rect"] for c in dpsd.read_crop_rects(psd05)}
    LAYERS = []
    for h in dpsd.harvest_results(psd05, "03.수정", [{"name": n, "rect": r} for n, r in rects.items()]):
        if h["mask"] is None or not (h["category"] == "png" or h["layer"] == "030_팔매듭"):
            continue
        crop = rects[h["crop_name"]]
        mk = h["mask"]
        LAYERS.append({"tag": h["layer"], "crop": crop, "quad": h["quad"],
                       "emb": np.asarray(Image.open(io.BytesIO(h["data"])).convert("RGB")),
                       "hand": crop_window(mk["arr"], mk["rect"][0], mk["rect"][1], mk["bg"], crop)})
    print(f"  입력 {len(LAYERS)}개 레이어 구성 {time.time() - t0:.1f}s")
    summary = {"layers": {}}
    gpt22 = [L for L in LAYERS if L["tag"] != "030_팔매듭"]
    check(len(gpt22) == 22, f"GPT 임베드 레이어 22개 (got {len(gpt22)})")
    check(any(L["tag"] == "030_팔매듭" for L in LAYERS), "030 중첩 PSB 포함")

    # R5: 정합
    print("\nR5 정합 (정방향 cand→base; ctr = 크롭 중심 이동 px, user = 05 PSD SO transform)")
    t1 = time.time()
    for L in LAYERS:
        x0, y0, x1, y1 = L["crop"]
        w, h = x1 - x0, y1 - y0
        L["B"] = base_full[y0:y1, x0:x1]
        L["G"] = an.place(L["emb"], (w, h))
        L["user"] = an.matrix_from_quad(L["quad"], L["crop"])
        L["reg"] = an.register(L["B"], L["G"])
        L["reg_m"] = an.register(L["B"], L["G"], exclude_mask=L["hand"])
    t_reg = time.time() - t1
    r5 = {}
    for variant in ("reg", "reg_m"):
        rows = {}
        n_ident = 0
        for L in LAYERS:
            x0, y0, x1, y1 = L["crop"]
            w, h = x1 - x0, y1 - y0
            r = L[variant]
            ctr = center_disp(r["matrix"], w, h)
            uctr = center_disp(L["user"], w, h)
            us = an.decompose(L["user"])
            row = {"kind": r["kind"], "sx": round(r["sx"], 4), "sy": round(r["sy"], 4), "rot": round(r["rot_deg"], 3),
                   "ctr": [round(float(v), 2) for v in ctr], "disp": r["disp"], "improve": r["improve"],
                   "user_ctr": [round(float(v), 2) for v in uctr], "user_sy": round(us["sy"], 4),
                   "ctr_diff": round(float(np.hypot(*(ctr - uctr))), 2),
                   "corner_diff": round(corner_err(r["matrix"], L["user"], w, h), 2)}
            rows[L["tag"]] = row
            print(f"  {variant:5s} {L['tag'][:28]:28s} {row['kind']:11s} s=({row['sx']:.4f},{row['sy']:.4f}) "
                  f"rot {row['rot']:+.2f} ctr ({ctr[0]:+6.2f},{ctr[1]:+6.2f}) disp {row['disp']:5.2f} "
                  f"| user ctr ({uctr[0]:+.1f},{uctr[1]:+.1f}) sy {us['sy']:.4f} | ctr_diff {row['ctr_diff']:.2f}")
            check(r["disp"] <= 40 and 0.9 <= r["sx"] <= 1.1 and 0.9 <= r["sy"] <= 1.1,
                  f"R5 {variant} {L['tag']} 발산 (disp {r['disp']}, s {r['sx']:.3f},{r['sy']:.3f})")
            tag = L["tag"]
            if tag == "001_바보털E":  # 001: sy 1.0286, ty −33 (사용자) / ECC 역 sy 0.966
                check(r["applied"] and 1.02 <= r["sy"] <= 1.05 and row["ctr_diff"] <= 2.0 and row["corner_diff"] <= 6.0,
                      f"R5 {variant} 001 재현: {row}")
            elif tag == "007_머리장식-왼쪽":  # 007-왼쪽: 회전 ≈1.1°, 사용자는 x −2 만
                check(r["applied"] and 0.6 <= abs(r["rot_deg"]) <= 1.5 and -2.5 <= ctr[0] <= -0.3,
                      f"R5 {variant} 007-왼쪽 재현: {row}")
            elif tag == "030_팔매듭":  # 030: 사용자 sy 1.035, ty −2
                if variant == "reg_m":
                    check(r["applied"] and 1.02 <= r["sy"] <= 1.05 and row["ctr_diff"] <= 2.0,
                          f"R5 {variant} 030 재현(손 마스크 제외): {row}")
                else:
                    check(r["applied"] and row["ctr_diff"] <= 2.0, f"R5 {variant} 030 적용 + 중심 일치: {row}")
            else:
                n_ident += not r["applied"]
                check((not r["applied"]) or (float(np.hypot(*ctr)) < 1.0 and r["disp"] <= 3.0),
                      f"R5 {variant} {tag} 정상 범위(identity 또는 중심 <1px, 코너 ≤3px): {row}")
        r5[variant] = {"identity_among_normal": n_ident, "rows": rows}
        print(f"  {variant}: 정상 레이어 중 identity {n_ident}개")
    summary["r5"] = r5
    summary["r5_seconds"] = round(t_reg, 1)

    # R6: 자동 마스크 soft IoU (22 GPT 레이어, 손 마스크 대비, base 유효 픽셀)
    t1 = time.time()
    ious, ious_roi = [], []
    for L in gpt22:
        valid = L["B"][..., 3] > 250
        M = L["hand"]
        ys, xs = np.nonzero(M > 0.5)
        bx0, bx1, by0, by1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
        px, py = 0.1 * (bx1 - bx0), 0.1 * (by1 - by0)
        roi = [bx0 - px, by0 - py, bx1 + px, by1 + py]
        L["am"] = an.auto_mask(L["B"], L["G"])
        Ma = L["am"]["params"]["align_matrix"]  # 같은 정합을 다시 돌리지 않도록 정렬본 + align=False
        am_roi = an.auto_mask(L["B"], L["G"] if Ma is None else an.warp_affine(L["G"], Ma), roi=roi, align=False)
        ious.append(an.soft_iou(L["am"]["mask"], M, valid))
        ious_roi.append(an.soft_iou(am_roi["mask"], M, valid))
        summary["layers"].setdefault(L["tag"], {}).update(
            {"iou": round(ious[-1], 3), "iou_roi": round(ious_roi[-1], 3),
             "recall": round(an.recall(L["am"]["mask"], M, valid), 3),
             "precision": round(an.precision(L["am"]["mask"], M, valid), 3)})
    print("\nR6 자동 마스크 soft IoU: " + " ".join(f"{L['tag'][:6]}:{v:.2f}/{vr:.2f}" for L, v, vr in zip(gpt22, ious, ious_roi)))
    print(f"  평균 roi 없음 {np.mean(ious):.4f} (≥0.63), roi=손 bbox+10% {np.mean(ious_roi):.4f} (≥0.66), "
          f"중앙값 {np.median(ious):.3f}/{np.median(ious_roi):.3f}  {time.time() - t1:.1f}s")
    check(np.mean(ious) >= 0.63, f"R6 mean IoU {np.mean(ious):.4f} ≥ 0.63")
    check(np.mean(ious_roi) >= 0.66, f"R6 mean IoU(roi) {np.mean(ious_roi):.4f} ≥ 0.66")
    summary["r6"] = {"mean": round(float(np.mean(ious)), 4), "mean_roi": round(float(np.mean(ious_roi)), 4),
                     "median": round(float(np.median(ious)), 4), "median_roi": round(float(np.median(ious_roi)), 4)}

    # R7: 톤 보정장 홀드아웃 (손 마스크 밖 6px 침식, 32px 체커보드 절반 학습 / 절반 평가)
    t1 = time.time()
    before, after = [], []
    for L in gpt22:
        h, w = L["G"].shape[:2]
        Gw = an.warp_affine(L["G"], L["reg"]["matrix"]) if L["reg"]["applied"] else L["G"]
        out = ndi.binary_erosion((L["hand"] < 0.01) & (L["B"][..., 3] > 250), iterations=6)
        yy, xx = np.mgrid[0:h, 0:w]
        train = out & (((yy // 32) + (xx // 32)) % 2 == 0)
        test = out & ~train
        tf = an.tone_field(L["B"], Gw, (~train).astype(np.float32), sigma=16, clamp=12.0)
        before.append(mae(Gw, L["B"][..., :3], test))
        after.append(mae(an.apply_delta(Gw, tf["delta"]), L["B"][..., :3], test))
        summary["layers"].setdefault(L["tag"], {}).update({"tone_before": round(before[-1], 3), "tone_after": round(after[-1], 3)})
    ratio = float(np.mean(after) / np.mean(before))
    print(f"\nR7 톤 홀드아웃: 마스크 밖 MAE {np.mean(before):.3f} → {np.mean(after):.3f}, 비 {ratio:.3f} (≤0.75)  "
          f"{time.time() - t1:.1f}s")
    check(ratio <= 0.75, f"R7 홀드아웃 비 {ratio:.3f} ≤ 0.75")
    summary["r7"] = {"before": round(float(np.mean(before)), 3), "after": round(float(np.mean(after)), 3), "ratio": round(ratio, 3)}

    # 게이트: 실제로 쓰인 결과들은 하드 실패 없음. 투명 입력으로 만든 007-오른쪽만 검은 띠(오른쪽 93px).
    gate_rows = {}
    for L in gpt22:
        Gw = an.warp_affine(L["G"], L["reg"]["matrix"]) if L["reg"]["applied"] else L["G"]
        g = an.gates(L["B"], Gw, L["am"]["mask"], L["reg"])
        gate_rows[L["tag"]] = g
        if L["tag"] == "007_머리장식-오른쪽_마름모":
            check(g["fails"] == ["black_band"] and g["black_edges"]["right"] >= 80, f"게이트 007-오른쪽 검은 띠 {g}")
        else:
            check(g["fails"] == [], f"게이트 {L['tag']} 통과 {g}")
    summary["gates"] = {t: {"outside_mad": g["outside_mad"], "outside_frac_gt24": g["outside_frac_gt24"],
                            "fails": g["fails"]} for t, g in gate_rows.items()}

    os.makedirs(OUT_DIR, exist_ok=True)
    summary["seconds"] = round(time.time() - t0, 1)
    with open(os.path.join(OUT_DIR, "analysis_regression.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    print(f"\n회귀 {time.time() - t0:.1f}s (정합 {t_reg:.1f}s) → {os.path.join(OUT_DIR, 'analysis_regression.json')}")


# ═════════════════════════════════════════════════════════════════
# N, Q, R8, R9. 노드 통합 (03 PSD 가져오기 → 05 PSD 수확 → 분석 → 합성 → SO PSD)
# ═════════════════════════════════════════════════════════════════
def _run_export(dp, h, *args, skip_empty=False):
    # M1 테스트(경로 충돌·work_rect 자르기 등)는 후보 없는 프로젝트도 PSD 를 써야 하므로 기본 False(노드 기본값은 True).
    r = dp.BMKDesignPatchExportPSD().run(h, *args, skip_empty=skip_empty)
    return r["result"][0], r["ui"]["text"]


def node_integration(psd05, crops03):
    NODES = OUT_ROOT / "nodes"
    section("N0 노드 모듈 import (패키지 상대 import / 단독 실행 폴백) + 규약")
    sys.path.insert(0, str(COMFY))
    import folder_paths  # noqa: E402  (ComfyUI 루트)

    pkg = types.ModuleType("_bmk_dp_pkg")
    pkg.__path__ = [str(PKG)]
    sys.modules["_bmk_dp_pkg"] = pkg
    t0 = time.time()
    dp = importlib.import_module("_bmk_dp_pkg.bmk_design_patch")
    note(f"N0 노드 모듈 패키지 import {time.time() - t0:.1f}s")
    check(dp.store.__name__ == "_bmk_dp_pkg.bmk_design_patch_store" and dp.dpsd.__name__ == "_bmk_dp_pkg.bmk_design_patch_psd"
          and dp.an.__name__ == "_bmk_dp_pkg.bmk_design_patch_analysis"
          and dp.dprompt.__name__ == "_bmk_dp_pkg.bmk_design_patch_prompt"
          and dp.dprompt.store is dp.store, "패키지 안에서는 상대 import 로 보조 모듈을 읽음")
    alone_spec = importlib.util.spec_from_file_location("bmk_design_patch_alone", PKG / "bmk_design_patch.py")
    alone = importlib.util.module_from_spec(alone_spec)
    alone_spec.loader.exec_module(alone)
    check(alone.store is store and alone.dpsd is dpsd, "단독 실행: 절대 import 폴백(sys.path 의 보조 모듈)")
    ids = ["BMKDesignPatchProject", "BMKDesignPatchImportPSD", "BMKDesignPatchCandidateIn", "BMKDesignPatchPrepare",
           "BMKDesignPatchRun", "BMKDesignPatchAnalyze", "BMKDesignPatchReview", "BMKDesignPatchCompose",
           "BMKDesignPatchExportPSD"]
    check(list(dp.NODE_CLASS_MAPPINGS) == ids and list(dp.NODE_DISPLAY_NAME_MAPPINGS) == ids, "노드 9개 매핑(M2 Run·Review)")
    spec_aliases = ["multi layer crop edit", "design patch", "디자인 패치", "디자인 수정", "파츠 보정", "크롭 수정", "psd"]
    for nid, cls in dp.NODE_CLASS_MAPPINGS.items():
        disp = dp.NODE_DISPLAY_NAME_MAPPINGS[nid]
        check(cls.CATEGORY == "BMK/Image" and disp.startswith("BMK Design Patch ") and nid.startswith("BMKDesignPatch"),
              f"{nid} 카테고리/표시 이름 {cls.CATEGORY} {disp}")
        check(isinstance(cls.DESCRIPTION, str) and len(cls.DESCRIPTION) > 20, f"{nid} DESCRIPTION")
        check(all(a in cls.SEARCH_ALIASES for a in spec_aliases) and not any("bmk" == a.lower() for a in cls.SEARCH_ALIASES),
              f"{nid} SEARCH_ALIASES")
        has_ic = "IS_CHANGED" in cls.__dict__
        check(has_ic == (nid in ids[:3]), f"{nid} IS_CHANGED 는 Project / ImportPSD / CandidateIn 에만 ({has_ic})")
        req = cls.INPUT_TYPES()["required"]
        if nid != "BMKDesignPatchProject":
            check(req["project"][0] == "BMK_DP_PROJECT" and list(req)[0] == "project", f"{nid} 첫 입력 project")
        hidden = list(cls.INPUT_TYPES().get("hidden") or {})
        optional = list(cls.INPUT_TYPES().get("optional") or {})
        sig = inspect.signature(getattr(cls, cls.FUNCTION)).parameters
        check(list(req) + optional + hidden == list(sig)[1:]
              and all(sig[o].default is not inspect.Parameter.empty for o in optional),
              f"{nid} 입력 이름(+optional 기본값 있음 +hidden) = 함수 인자")
    outs = [k for k, v in dp.NODE_CLASS_MAPPINGS.items() if getattr(v, "OUTPUT_NODE", False) is True]
    check(outs == ["BMKDesignPatchRun", "BMKDesignPatchReview", "BMKDesignPatchExportPSD"], f"OUTPUT_NODE {outs}")
    rt = {k: v.RETURN_TYPES for k, v in dp.NODE_CLASS_MAPPINGS.items()}
    check(rt == {"BMKDesignPatchProject": ("BMK_DP_PROJECT", "STRING"),
                 "BMKDesignPatchImportPSD": ("BMK_DP_PROJECT", "IMAGE", "STRING"),
                 "BMKDesignPatchCandidateIn": ("BMK_DP_PROJECT", "STRING"),
                 "BMKDesignPatchPrepare": ("BMK_DP_PROJECT", "IMAGE", "STRING"),
                 "BMKDesignPatchRun": ("BMK_DP_PROJECT", "STRING", "IMAGE"),
                 "BMKDesignPatchAnalyze": ("BMK_DP_PROJECT", "IMAGE", "STRING"),
                 "BMKDesignPatchReview": ("BMK_DP_PROJECT", "STRING"),
                 "BMKDesignPatchCompose": ("BMK_DP_PROJECT", "IMAGE", "MASK", "STRING"),
                 "BMKDesignPatchExportPSD": ("STRING",)}, f"RETURN_TYPES {rt}")
    tree = ast.parse((PKG / "__init__.py").read_text(encoding="utf-8"))
    mods = next(ast.literal_eval(n.value) for n in tree.body
                if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "_NODE_MODULES")
    bmk = [x for x in mods if x.startswith("bmk_")]
    check("bmk_design_patch" in mods and bmk == sorted(bmk), "__init__ _NODE_MODULES 에 bmk_design_patch(알파벳 순)")
    check(not any(x.startswith("bmk_design_patch_") for x in mods), "보조 모듈은 _NODE_MODULES 에 없음")
    check("bmk_<기능>_<역할>.py" in ast.get_docstring(tree), "규약 §1 에 노드 없는 보조 모듈 규칙 한 줄")
    src = (PKG / "bmk_design_patch.py").read_text(encoding="utf-8")
    check(not any(ord(ch) >= 0x1F000 or ch == "\ufe0f" for ch in src), "노드 모듈 소스에 이모지 없음")
    check(src.startswith('"""BMK Design Patch') and "from __future__ import annotations" in src, "노드 모듈 머리")

    P_ = dp.BMKDesignPatchProject()
    IMP = dp.BMKDesignPatchImportPSD()
    CIN = dp.BMKDesignPatchCandidateIn()
    PRE = dp.BMKDesignPatchPrepare()
    ANA = dp.BMKDesignPatchAnalyze()
    COM = dp.BMKDesignPatchCompose()
    proj = "m1_regression"
    shutil.rmtree(NODES / "bmk_design_patch" / proj, ignore_errors=True)
    shutil.rmtree(NODES / "output" / "design_patch" / proj, ignore_errors=True)
    folder_paths.set_output_directory(str(NODES / "output"))
    root = store.project_root(str(NODES), proj)

    def rev():
        return store.load_manifest(root)["rev"]

    def is_img(t):
        return t.ndim == 4 and t.shape[0] == 1 and t.shape[3] == 3 and str(t.dtype) == "torch.float32" \
            and float(t.min()) >= 0 and float(t.max()) <= 1

    section("N1 Project")
    expect_raises(lambda: P_.open("../x", str(NODES)), "Project 이름 검증")
    h, summ = P_.open(proj, str(NODES))
    check(h == {"schema": store.SCHEMA, "root": root, "project": proj, "rev": 1} and os.path.isfile(os.path.join(root, "design_patch.json")),
          f"Project 핸들 {h}")
    ic1 = dp.BMKDesignPatchProject.IS_CHANGED(project=proj, base_dir=str(NODES))
    check(ic1 == dp.BMKDesignPatchProject.IS_CHANGED(project=proj, base_dir=str(NODES)), "Project IS_CHANGED 안정")
    check(P_.open(proj, str(NODES))[0]["rev"] == 1, "Project 재실행은 rev 를 올리지 않음")

    section("N2 Import PSD (03)")
    t0 = time.time()
    h, ov, rep = IMP.run(h, str(PSD03), "auto", "auto", "auto", "auto", str(REFS_DIR), "")
    t_imp = time.time() - t0
    m = store.load_manifest(root)
    ref_rects = json.load(open(CROPS03_JSON, encoding="utf-8"))["crops"]
    check(len(m["crops"]) == 22 and [c["rect"] for c in m["crops"]] == [c["rect"] for c in ref_rects]
          and [c["name"] for c in m["crops"]] == [c["name"] for c in ref_rects], "Import: 22 rect == crops_03.json")
    check(all(re.fullmatch(r"\d{3}_[0-9a-f]{6}", c["id"]) for c in m["crops"]) and len({c["id"] for c in m["crops"]}) == 22,
          "Import: crop_id 형식·고유")
    check(m["canvas"] == [3584, 4608] and m["work_rect"] == [256, 256, 3328, 4352] and m["source"]["fingerprint"] == store.file_fingerprint(str(PSD03)),
          f"Import: canvas/work_rect/fingerprint {m['canvas']} {m['work_rect']}")
    base03, binfo03 = dpsd.read_base(psd_cached(PSD03))
    srcs03 = dpsd.crop_sources(psd_cached(PSD03), crops03, base03)
    same_src = sum(store.pixel_sha(s["rgba"]) == store.pixel_sha(np.asarray(Image.open(store.resolve_path(root, c["source"]))))
                   for s, c in zip(srcs03, m["crops"]))
    check(same_src == 22, f"Import: 크롭 소스 PNG == psd.crop_sources (위치로 짝지음 {same_src}/22)")
    check(store.pixel_sha(np.asarray(Image.open(store.resolve_path(root, m["base"])))) == store.pixel_sha(base03), "Import: 베이스 PNG == read_base")
    with_refs = sum(bool(c["refs_auto"]) for c in m["crops"])
    check(with_refs == 19, f"Import: 레퍼런스 자동 매칭 크롭 19/22 ({with_refs})")
    c002 = next(c for c in m["crops"] if c["name"] == "002_토스트-머리장식_right")
    check(c002["refs_auto"][0] == "002_토스트-머리장식_right_t.png" and c002["targets"] == [
        {"tid": "A", "label": "토스트-머리장식_right", "name_en": "", "mode": "ref_correct", "refs": []}], f"Import: 002 타깃/refs {c002['targets']}")
    c007 = next(c for c in m["crops"] if c["name"] == "007_머리장식-오른쪽")
    check(abs(c007["alpha_frac"] - 0.1836) < 0.01 and any("투명 여백" in w for w in c007["warnings"]), "Import: 007-오른쪽 투명 여백 경고")
    check(sum("rect 가 같음" in " ".join(c["warnings"]) for c in m["crops"]) == 2, "Import: 010 약지/새끼 같은 rect 경고")
    check(is_img(ov) and max(ov.shape[1:3]) == 1600, f"Import: overview {tuple(ov.shape)}")
    check(h["rev"] == 2 and "크롭 22" in rep, f"Import: rev {h['rev']}")
    t0 = time.time()
    h2, _ov, rep2 = IMP.run(h, str(PSD03), "auto", "auto", "auto", "auto", str(REFS_DIR), "")
    check(h2["rev"] == 2 and "캐시 적중" in rep2 and time.time() - t0 < t_imp, "Import 재실행: 캐시 적중, rev 불변")
    note(f"N2 Import PSD {t_imp:.1f}s, 재실행 {time.time() - t0:.1f}s (캐시)")
    ic_kw = dict(psd_path=str(PSD03), refs_dir=str(REFS_DIR), glossary_path="", crop_group="auto",
                 clean_plate_layer="auto", base_layer="auto", crop_pixels_group="auto")
    check(dp.BMKDesignPatchImportPSD.IS_CHANGED(**ic_kw) == dp.BMKDesignPatchImportPSD.IS_CHANGED(**ic_kw)
          and dp.BMKDesignPatchImportPSD.IS_CHANGED(**dict(ic_kw, crop_group="02.크롭영역")) != dp.BMKDesignPatchImportPSD.IS_CHANGED(**ic_kw),
          "ImportPSD IS_CHANGED: 같은 값 안정, 값이 바뀌면 다름")
    expect_raises(lambda: IMP.run(h, str(REF / "없음.psd"), "auto", "auto", "auto", "auto", "", ""), "Import: 없는 PSD 거부")

    section("N3 Candidate In (05 수확)")
    t0 = time.time()
    h, rep = CIN.run(h, "psd_harvest", str(PSD05), "03.수정", "", True, True)
    t_cin = time.time() - t0
    m = store.load_manifest(root)
    cands = m["candidates"]
    by_layer = {c["origin_info"]["layer"]: c for c in cands}
    check(len(cands) == 26 and len({c["key"] for c in cands}) == 26, f"CandidateIn: 후보 26 ({len(cands)})")
    check(sum(1 for c in cands if c["picked"]) == 26 and sum(len(v) for v in m["picks"].values()) == 26, "CandidateIn: 보이는 SO 26개 pick")
    check(sum(1 for c in cands if c["hand_mask"]) == 25 and by_layer["레이어 24"]["hand_mask"]["owner"] == "group"
          and by_layer["레이어 12"]["hand_mask"] is None, "CandidateIn: 손 마스크 25 (레이어 24 = 그룹 마스크, 레이어 12 없음)")
    hv_data = {h_["layer"]: h_ for h_ in dpsd.harvest_results(psd05, "03.수정", crops03)}
    ok_bytes = sum(open(store.resolve_path(root, c["file"]), "rb").read() == hv_data[c["origin_info"]["layer"]]["data"] for c in cands)
    check(ok_bytes == 26, f"CandidateIn: cands/ 원본 bytes == 수확 data ({ok_bytes}/26)")
    check(all(c["quad"] == hv_data[c["origin_info"]["layer"]]["quad"] and c["z"] == hv_data[c["origin_info"]["layer"]]["z_index"] for c in cands),
          "CandidateIn: quad / z == 수확")
    check(all(c["key"].startswith("h_") and c["file"] == f"cands/{c['key']}.png" for c in cands), "CandidateIn: key h_<sha12>, file cands/<key>.png")
    hm = by_layer["001_바보털E"]["hand_mask"]
    marr = np.asarray(Image.open(store.resolve_path(root, hm["file"])))
    hv1 = hv_data["001_바보털E"]["mask"]
    check(np.array_equal(marr, hv1["arr"]) and hm["rect"] == hv1["rect"] and hm["bg"] == hv1["bg"], "CandidateIn: 손 마스크 PNG == 수확 마스크")
    check(by_layer["2026-09-11_14-24-16_img2img_3"]["crop_id"] == next(c["id"] for c in m["crops"] if c["name"] == "010_손가락반창고"),
          "CandidateIn: IoU 매칭 크롭")
    check(h["rev"] == 3, f"CandidateIn rev {h['rev']}")
    h2, _ = CIN.run(h, "psd_harvest", str(PSD05), "03.수정", "", True, True)
    check(h2["rev"] == 3, "CandidateIn 재실행: rev 불변")
    snap_c = json.dumps(store.load_manifest(root)["candidates"], sort_keys=True, ensure_ascii=False)
    mj = json.dumps({"레이어 12": {"skip": True}, "002_토스트-머리장식_right_헤어핀": "B"}, ensure_ascii=False)
    h2, rep_m = CIN.run(h, "psd_harvest", str(PSD05), "03.수정", mj, True, True)
    mm = store.load_manifest(root)
    check(len(mm["candidates"]) == 25 and "레이어 12" not in {c["origin_info"]["layer"] for c in mm["candidates"]}
          and any("타깃 B 가" in ln for ln in rep_m.splitlines()), "CandidateIn mapping_json: skip 제거 + 없는 타깃 경고")
    h, _ = CIN.run(h2, "psd_harvest", str(PSD05), "03.수정", "", True, True)
    check(json.dumps(store.load_manifest(root)["candidates"], sort_keys=True, ensure_ascii=False) == snap_c,
          "CandidateIn: 매핑 없이 다시 수확하면 26개 그대로 복원")
    ck = dict(source="psd_harvest", path=str(PSD05), group="03.수정", mapping_json="", visible_is_pick=True, keep_hand_masks=True)
    check(dp.BMKDesignPatchCandidateIn.IS_CHANGED(**ck) == dp.BMKDesignPatchCandidateIn.IS_CHANGED(**ck), "CandidateIn IS_CHANGED 안정")
    note(f"N3 Candidate In {t_cin:.1f}s (SO 26, 손 마스크 25)")

    section("N4 Prepare")
    r_before = rev()
    t0 = time.time()
    h, sheet, rep = PRE.run(h, "gpt-image-2.5-sunburst", "max", "user_k", "V1,V4", 4, "at")
    t_pre = time.time() - t0
    m = store.load_manifest(root)
    jobs = m["jobs"]
    check(len(jobs) == 22 and all(j["variant"] == "V1_standard" for j in jobs) and "V4_checklist = V1_standard" in rep,
          f"Prepare: detail 에 ';' 가 없어 V4 = V1 → 크롭당 1작업 22 ({len(jobs)})")
    check(len({j["cell_key"] for j in jobs}) == len(jobs) and all(re.fullmatch(r"[0-9a-f]{24}", j["cell_key"]) for j in jobs),
          "Prepare: cell_key 24hex 고유")
    sizes_ok = all(j["size"] == list(store.gpt_output_size(c["rect"][2] - c["rect"][0], c["rect"][3] - c["rect"][1], "user_k")[:2])
                   for j, c in zip(jobs, m["crops"]))
    check(sizes_ok, "Prepare: 작업 크기 = gpt_output_size(user_k)")
    j002 = next(j for j in jobs if j["crop_id"] == c002["id"])
    check([i["role"] for i in j002["inputs"]] == ["canvas", "ref"] and "@image2" in j002["prompt"]
          and j002["prompt"].startswith("Input images, in upload order:"), "Prepare: 002 입력 canvas + ref, @image2")
    flat_ok = True
    for j in jobs:
        for inp in j["inputs"]:
            im = Image.open(store.resolve_path(root, inp["file"]))
            flat_ok &= im.mode == "RGB" and store.pixel_sha(im) == inp["sha"]
    check(flat_ok, "Prepare: 입력은 평탄화된 RGB, sha = pixel_sha")
    j007 = next(j for j in jobs if j["crop_id"] == c007["id"])
    can7 = np.asarray(Image.open(store.resolve_path(root, j007["inputs"][0]["file"])))
    check(float((can7.max(axis=2) < 8).mean(axis=0)[-20:].max()) < 0.5, "Prepare: 007-오른쪽 canvas 오른쪽 검은 띠 없음(edge-replicate)")
    recomputed = store.cell_key(j002["model"], j002["quality"], j002["size"], j002["background"], j002["prompt"],
                                [i["sha"] for i in j002["inputs"]], j002["template_version"], j002["variant"])
    check(recomputed == j002["cell_key"], "Prepare: cell_key 재계산 일치")
    check(is_img(sheet) and h["rev"] == r_before + 1, f"Prepare: job_sheet {tuple(sheet.shape)}, rev {r_before} → {h['rev']}")
    h2, _s, rep2 = PRE.run(h, "gpt-image-2.5-sunburst", "max", "user_k", "V1,V4", 4, "at")
    check(h2["rev"] == h["rev"] and "신규 0" in rep2, "Prepare 재실행: rev 불변, 신규 0")
    h2, _s, rep3 = PRE.run(h, "gpt-image-2.5-sunburst", "max", "user_k", "V1,V4", 8, "at")
    check(store.load_manifest(root)["jobs"][0]["cell_key"] == jobs[0]["cell_key"] and "신규 0" in rep3, "Prepare: n 4→8 은 cell_key 불변")
    h, _s, _r = PRE.run(h2, "gpt-image-2.5-sunburst", "max", "user_k", "V1,V4", 4, "at")
    expect_raises(lambda: PRE.run(h, "gpt-image-2.5-sunburst", "max", "user_k", "X9", 4, "at"), "Prepare: 잘못된 variants 거부")
    note(f"N4 Prepare {t_pre:.1f}s: 작업 {len(jobs)} (V4 중복 22개 건너뜀)")

    section("N5 Analyze")
    r_before = rev()
    t0 = time.time()
    h, csheet, rep = ANA.run(h, "guarded_affine", "field", 16, 8.0, 9, 3.5, "none", False)
    t_ana = time.time() - t0
    m = store.load_manifest(root)
    check(all(len(v) == 1 for v in m["analysis"].values()) and h.get("analyze", {}).get("dE") == 8.0,
          "Analyze: analysis[key] = {params_hash: 항목} 1개씩, 핸들에 분석 설정")
    ana = {k: next(iter(v.values())) for k, v in m["analysis"].items()}
    skipped = {c["origin_info"]["layer"] for c in m["candidates"] if ana[c["key"]].get("skipped")}
    check(len(ana) == 26 and skipped == {"레이어 12", "생성형 채우기 2"}, f"Analyze: 26 항목, 부분 패치 2개 생략 {skipped}")
    for nm in ("001_바보털E", "007_머리장식-왼쪽", "030_팔매듭"):
        e = ana[by_layer[nm]["key"]]
        check(e["reg"]["applied"] and e["reg"]["masked"], f"Analyze: {nm} 정합 적용(손 마스크 제외) {e['reg']['kind']}")
    files_ok = all(os.path.isfile(store.resolve_path(root, e[k])) for e in ana.values() if not e.get("skipped")
                   for k in ("automask", "thumb")) and all(os.path.isfile(store.resolve_path(root, e["tone"]["delta"]))
                                                          for e in ana.values() if not e.get("skipped"))
    check(files_ok, "Analyze: derived 산출(자동 마스크·썸네일·톤 보정장) 존재")
    n_fail = [by for by, c in by_layer.items() if not ana[c["key"]].get("skipped") and ana[c["key"]]["gates"]["fails"]]
    check(n_fail == ["007_머리장식-오른쪽_마름모"], f"Analyze 게이트: 007-오른쪽(투명 입력)만 검은 띠 {n_fail}")
    check(is_img(csheet) and h["rev"] == r_before + 1, f"Analyze: contact_sheet {tuple(csheet.shape)} rev {r_before} → {h['rev']}")
    t0 = time.time()
    h2, _cs, rep2 = ANA.run(h, "guarded_affine", "field", 16, 8.0, 9, 3.5, "none", False)
    check(h2["rev"] == h["rev"] and "계산 0" in rep2, "Analyze 재실행: 전부 캐시, rev 불변")
    note(f"N5 Analyze {t_ana:.1f}s (24 분석 + 2 생략), 재실행 {time.time() - t0:.1f}s")

    section("Q 정합 quad vs 05 사용자 quad (코너 오차 px)")
    crops_by_id = {c["id"]: c for c in m["crops"]}
    for nm, lim_corner in (("001_바보털E", 6.0), ("030_팔매듭", 8.0), ("007_머리장식-왼쪽", 8.0)):
        c = by_layer[nm]
        e = ana[c["key"]]
        qr = np.array(e["quad_reg"]).reshape(4, 2)
        qu = np.array(c["quad"]).reshape(4, 2)
        x0, y0, x1, y1 = crops_by_id[c["crop_id"]]["rect"]
        qrect = np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], np.float64)
        err = np.hypot(*(qr - qu).T)
        cdiff = float(np.hypot(*(qr.mean(0) - qu.mean(0))))
        um = an.decompose(an.matrix_from_quad(c["quad"], [x0, y0, x1, y1]))
        r = e["reg"]
        note(f"Q {nm}: 코너 오차 최대 {err.max():.2f} 평균 {err.mean():.2f}px, 중심 차 {cdiff:.2f}px | "
             f"자동 s=({r['sx']:.4f},{r['sy']:.4f}) rot {r['rot_deg']:+.2f}° | 사용자 s=({um['sx']:.4f},{um['sy']:.4f}) "
             f"rot {um['rot_deg']:+.2f}° | rect 대비 이동: 자동 {np.hypot(*(qr - qrect).T).max():.1f} / 사용자 {np.hypot(*(qu - qrect).T).max():.1f}px")
        check(err.max() <= lim_corner, f"Q {nm} 코너 오차 {err.max():.2f} ≤ {lim_corner}")
        if nm != "007_머리장식-왼쪽":
            check(cdiff <= 2.0 and abs(r["sy"] - um["sy"]) < 0.01, f"Q {nm} 중심 차 {cdiff:.2f} ≤ 2, sy 차 < 0.01")
        else:  # 사용자는 x −2 만 고치고 실측 회전 ≈1.1° 는 남김
            check(0.6 <= abs(r["rot_deg"]) <= 1.5 and -2.5 <= qr.mean(0)[0] - qrect.mean(0)[0] <= -0.3,
                  f"Q 007-왼쪽 회전 {r['rot_deg']:.2f}°, 중심 x 이동 {qr.mean(0)[0] - qrect.mean(0)[0]:.2f}")

    section("R9 Compose(mask=hand, 05 quad, 톤·정합 없음) vs 05 psd.topil() 작업영역 MAE")
    t0 = time.time()
    h, img, mask, rep = COM.run(h, "hand", False, False, "harvest")
    t_com = time.time() - t0
    ref05 = np.asarray(psd05.topil().convert("RGB")).astype(np.float32)
    ours = np.rint(img[0].numpy() * 255.0).astype(np.float32)
    base = np.asarray(Image.open(store.resolve_path(root, m["base"]))).astype(np.float32)
    d = np.abs(ours - ref05).mean(2)
    db = np.abs(base - ref05).mean(2)
    wx0, wy0, wx1, wy1 = m["work_rect"]
    mae_w = float(d[wy0:wy1, wx0:wx1].mean())
    print("  %-28s %8s %8s %7s" % ("crop (∩ 작업영역)", "MAE", "base", ">8 %"))
    exc = {"016_영수증": "05 의 016 그룹에 이전 결과를 가리는 픽셀 복사본(후보 아님)",
           "007_머리장식-왼쪽": "05 에 저장된 SO 렌더가 임베드 원본과 다름(R4 9.6)"}
    bad = []
    for c in m["crops"]:
        x0, y0, x1, y1 = max(c["rect"][0], wx0), max(c["rect"][1], wy0), min(c["rect"][2], wx1), min(c["rect"][3], wy1)
        v, vb, v8 = float(d[y0:y1, x0:x1].mean()), float(db[y0:y1, x0:x1].mean()), float((d[y0:y1, x0:x1] > 8).mean() * 100)
        print("  %-28s %8.3f %8.3f %7.2f %s" % (c["name"][:28], v, vb, v8, ("  ← " + exc[c["name"]]) if c["name"] in exc else ""))
        if v > 1.0 and c["name"] not in exc:
            bad.append((c["name"], round(v, 3)))
    note(f"R9 Compose(hand, 05 quad) vs 05 렌더: 작업영역 MAE {mae_w:.3f} (목표 ≤1.5), 베이스만 {float(db[wy0:wy1, wx0:wx1].mean()):.3f}, "
         f"{t_com:.1f}s")
    check(mae_w <= 1.5, f"R9 작업영역 MAE {mae_w:.3f} ≤ 1.5")
    check(not bad, f"R9 크롭별 MAE ≤1.0 (예외 2개 제외) {bad}")
    mk = mask[0].numpy()
    check(tuple(mask.shape) == (1, 4608, 3584) and float(mk.min()) >= 0 and float(mk.max()) <= 1
          and 0.05 < float((mk < 0.5).mean()) < 0.25, f"Compose mask(1 = 패치 없음) 패치 비율 {float((mk < 0.5).mean()):.3f}")
    check(h.get("compose") == {"mask_source": "hand", "use_registration": False, "use_tone": False, "zorder": "harvest"}
          and h.get("analyze", {}).get("dE") == 8.0 and "compose" not in store.load_manifest(root),
          "DP-INT-3 Compose 설정은 핸들로(analyze 이어받음), 매니페스트에는 쓰지 않음")
    h, img2, _mask2, rep2 = COM.run(h, "hand_else_auto", True, True, "auto_small_on_top")
    ours2 = np.rint(img2[0].numpy() * 255.0).astype(np.float32)
    mae2 = float(np.abs(ours2 - ref05).mean(2)[wy0:wy1, wx0:wx1].mean())
    note(f"R9b Compose(hand_else_auto, 정합, 톤, auto z) vs 05 렌더: 작업영역 MAE {mae2:.3f} (참고: 정합·톤은 05 와 다르게 적용)")
    check(mae2 < 3.0 and "톤 적용 24" in rep2, f"R9b MAE {mae2:.3f} < 3, 톤 24")

    section("R8 Export PSD (smart_object, 대안 숨김, toned) → 재열기")
    t0 = time.time()
    path, ui = _run_export(dp, h, "design_patch", "smart_object", "hidden_all", "toned", "hand_else_auto", True, True)
    t_exp = time.time() - t0
    m = store.load_manifest(root)
    rec = m["exports"][-1]
    out_dir = NODES / "output" / "design_patch" / proj
    check(Path(path) == out_dir / f"design_patch_r{rec['rev']}.psd" and os.path.isfile(path), f"Export 경로 {path}")
    check(rec["problems"] == 0 and rec["smart_objects"] == 26, f"Export 기록 problems {rec['problems']} SO {rec['smart_objects']}")
    check(rec["compose"] == h["compose"], f"DP-INT-3 Export 는 상류 Compose 설정(핸들)을 씀 {rec['compose']}")
    snap = json.load(open(out_dir / f"design_patch_r{rec['rev']}.json", encoding="utf-8"))
    written = snap["export_layers"]
    mpng = Image.open(out_dir / f"design_patch_r{rec['rev']}_mask.png")
    check(mpng.mode == "L" and mpng.size == (3584, 4608), "Export: 최종 마스크 PNG(L, 캔버스 크기)")
    check(dpsd.verify_saved(path) == [], "R8 verify_saved problems 0")
    rq = PSDImage.open(path)
    pv = np.asarray(rq.topil().convert("RGB")).astype(np.float32)
    d_pv = float(np.abs(pv - ours2)[wy0:wy1, wx0:wx1].max())
    raw_pv = dpsd.raw_walk(path)
    note(f"R2 Export 병합 프리뷰 vs Compose 미리보기(같은 설정) 작업영역 최대 차 {d_pv:.0f}, 프리뷰 압축 "
         f"{raw_pv['image_data_compression']} (1 = RLE), 작업영역 밖 흰 바탕 {pv[2, 2].tolist()}")
    check(d_pv <= 1 and raw_pv["image_data_compression"] == 1 and pv[2, 2].tolist() == [255, 255, 255],
          "R2 병합 프리뷰 = Compose 미리보기(작업영역), RLE, 밖은 흰 바탕")
    top = [x.name for x in rq]
    check(top == ["00.Base", "02.크롭영역", "03.수정"] and not rq[1].visible and len(list(rq[1])) == 22, f"R8 최상위 구조 {top}")
    sos = {x.name: x for x in rq.descendants() if x.kind == "smartobject"}
    check(len(sos) == len(written) == 26, f"R8 SO 개수 {len(sos)} / 기록 {len(written)}")
    ok_size = ok_quad = ok_mask = ok_vis = ok_name = 0
    for w in written:
        x = sos.get(w["name"])
        if x is None:
            continue
        so = x.smart_object
        ok_size += list(Image.open(io.BytesIO(so.data)).size) == w["src_size"]
        ok_quad += all(abs(a - b) < 1e-6 for a, b in zip(so.transform_box, w["quad"]))
        par = x.parent
        ok_mask += (w["mask_rect"] is None and not par.has_mask()) or (par.has_mask() and list(par.mask.bbox) == w["mask_rect"])
        ok_vis += x.visible == w["visible"] and x.sheet_color == (SheetColorType.GREEN if w["visible"] else SheetColorType.RED)
        ok_name += bool(re.fullmatch(r"\d{3}_\S+ · [A-Z] · c\d{2}( ★)?", w["name"]))
    check(ok_size == 26, f"R8 임베드 크기 == src_size {ok_size}/26")
    check(ok_quad == 26, f"R8 quad == 계획 {ok_quad}/26")
    check(ok_mask == 26, f"R8 타깃/슬라이스 그룹 마스크 bbox {ok_mask}/26")
    check(ok_vis == 26 and ok_name == 26, f"R8 보임·라벨 {ok_vis}/26, 이름 형식 {ok_name}/26")
    raw = dpsd.raw_walk(path, want_raw=(b"lnk2",))
    items, _consumed = dpsd.lnk2_items({b["key"]: b for b in raw["global"]}[b"lnk2"]["raw"])
    check(len(items) == 26 and all(len(dpsd.lnk2_item_tail(it["body"])) == 117 for it in items), "R8 lnk2 꼬리 117B × 26")
    q7 = sos[next(w["name"] for w in written if w["key"] == by_layer["007_머리장식-왼쪽"]["key"])].smart_object.transform_box
    check(dpsd.quad_kind(list(q7)) == "parallelogram", "R8 007-왼쪽 정합 = affine quad(비파괴)")
    n_toned = sum(w["toned"] for w in written)
    check(n_toned == 24, f"R8 톤 보정 풀해상도 임베드 24 ({n_toned})")
    c008 = by_layer["2026-09-11_16-59-23_edit_2"]  # 008_목부분 의 GPT 결과
    tw = next(w for w in written if w["key"] == c008["key"])
    raw008 = open(store.resolve_path(root, c008["file"]), "rb").read()
    check(sos[tw["name"]].smart_object.data != raw008 and list(Image.open(io.BytesIO(sos[tw["name"]].smart_object.data)).size) == tw["src_size"],
          "R8 toned 임베드 = 원본과 다른 풀해상도 PNG")
    note(f"R8 Export {t_exp:.1f}s {rec['bytes'] / 1e6:.1f}MB: SO 26 (pick 26, 톤 {n_toned}), verify 0, {path}")
    t0 = time.time()
    path2, ui2 = _run_export(dp, h, "design_patch", "smart_object", "hidden_all", "toned", "hand_else_auto", True, True)
    m2 = store.load_manifest(root)
    check(path2 == path and len(m2["exports"]) == len(m["exports"]) and m2["rev"] == m["rev"] and "변경 없음" in ui2[0]
          and time.time() - t0 < 5, "Export 재실행: 바뀐 것 없음 → 같은 파일, rev 불변")
    pathp, _uip = _run_export(dp, h, "design_patch_px", "pixel", "none", "raw", "hand", False, False)
    rp = PSDImage.open(pathp)
    kinds = [x.kind for x in rp.descendants() if not x.is_group()]
    check(dpsd.verify_saved(pathp) == [] and kinds.count("pixel") == 26 and "smartobject" not in kinds
          and [x.name for x in rp] == ["03.수정"], f"Export pixel 모드: 픽셀 26, SO 0 ({len(kinds)})")

    section("N6 멱등 — 전체 단계 재실행")
    r0 = rev()
    h, *_ = IMP.run(h, str(PSD03), "auto", "auto", "auto", "auto", str(REFS_DIR), "")
    h, _ = CIN.run(h, "psd_harvest", str(PSD05), "03.수정", "", True, True)
    h, *_ = PRE.run(h, "gpt-image-2.5-sunburst", "max", "user_k", "V1,V4", 4, "at")
    h, *_ = ANA.run(h, "guarded_affine", "field", 16, 8.0, 9, 3.5, "none", False)
    h, *_ = COM.run(h, "hand_else_auto", True, True, "auto_small_on_top")
    check(rev() == r0, f"전체 재실행 rev 불변 ({r0} → {rev()})")
    check(dp.BMKDesignPatchProject.IS_CHANGED(project=proj, base_dir=str(NODES)) != ic1, "Project IS_CHANGED: 매니페스트가 바뀌면 다름")

    section("Z 숨김 대안만 있는 타깃이 크롭 순서를 바꾸지 않음 (DP-5, 메모리에서만)")
    mz = copy.deepcopy(store.load_manifest(root))
    c17 = next(c for c in mz["crops"] if c["name"] == "017_팔리본")
    c17["targets"].append({"tid": "B", "label": "B 테스트", "name_en": "", "mode": "ref_correct", "refs": []})
    for c in mz["candidates"]:
        c["z"] = None if c.get("z") is None else c["z"] + 1
    src17 = next(c for c in mz["candidates"] if c["crop_id"] == c17["id"])
    mz["candidates"].append(dict(copy.deepcopy(src17), key=src17["key"] + "-zb", tid="B", picked=False, z=0))  # 맨 아래 숨김 SO
    wz: list = []
    oc = [p["crop"]["id"] for p in dp._plan(root, mz, {}, "hand", False, "harvest", False, wz)]
    oe = [p["crop"]["id"] for p in dp._plan(root, mz, {}, "hand", False, "harvest", True, wz)]
    check(oc == oe and len(oc) == len({c["crop_id"] for c in mz["candidates"] if c.get("picked")}),
          f"DP-5 zorder=harvest: Compose 크롭 순서 == Export(대안 포함) 순서 "
                                      f"(017 위치 {oc.index(c17['id'])} / {oe.index(c17['id'])})")

    section("N7 Candidate In (folder) — 별도 프로젝트")
    projf = "m1_folder"
    shutil.rmtree(NODES / "bmk_design_patch" / projf, ignore_errors=True)
    hf, _ = P_.open(projf, str(NODES))
    hf, _ov, _r = IMP.run(hf, str(PSD03), "auto", "auto", "auto", "auto", "", "")
    hf, repf = CIN.run(hf, "folder", str(GPT_DIR), "", "", True, True)
    mf = store.load_manifest(store.project_root(str(NODES), projf))
    fc = mf["candidates"]
    rects = {c["id"]: c["rect"] for c in mf["crops"]}
    check(len(fc) >= 10 and all(c["origin"] == "folder" and c["key"].startswith("f_") for c in fc), f"folder: 후보 {len(fc)}")
    check(all(c["quad"] == dpsd.quad_from_rect(rects[c["crop_id"]]) for c in fc), "folder: quad = crop rect")
    check(sum("건너뜀" in ln for ln in repf.splitlines()) >= 20, "folder: 크롭 이름으로 시작하지 않는 파일(타임스탬프) 경고")
    tgt = {(c["crop_id"], c["tid"]) for c in fc}
    check(sum(c["picked"] for c in fc) == len(tgt), "folder: 타깃마다 첫 후보 자동 pick")
    n0 = len(fc)
    mj = json.dumps({"2026-09-11_16-59-23_edit_2.png": {"crop": "008_목부분"}}, ensure_ascii=False)
    hf, _ = CIN.run(hf, "folder", str(GPT_DIR), "", mj, True, True)
    check(len(store.load_manifest(store.project_root(str(NODES), projf))["candidates"]) == n0 + 1, "folder mapping_json: 크롭 지정")
    hf, rej = CIN.run(hf, "folder", str(CROP_PNG_DIR), "", "", True, True)
    n_rej = sum("입력 크롭과 픽셀이 같아 거부" in ln for ln in rej.splitlines())
    check(n_rej >= 12, f"folder: 입력 크롭과 같은 이미지 거부 {n_rej}")
    note(f"N7 folder 후보 {n0}개(04.디자인수정), 입력과 같은 크롭 PNG 거부 {n_rej}개")
    node_fixes(dp, NODES)
    return path


# ═════════════════════════════════════════════════════════════════
# N8. 리뷰 확인 결함의 노드 수준 회귀 — 작은 합성 프로젝트(Import 없이 매니페스트를 직접 씀)
# ═════════════════════════════════════════════════════════════════
def _fx_project(dp, base_dir, name, rects, base):
    """합성 프로젝트: base(HxWx3) + rects=[(크롭 레이어명, rect)](이름 중복 허용). 반환 (핸들, root)."""
    root = store.project_root(str(base_dir), name)
    shutil.rmtree(root, ignore_errors=True)
    dp.BMKDesignPatchProject().open(name, str(base_dir))
    m = store.load_manifest(root)
    H, W = base.shape[:2]
    m["canvas"], m["work_rect"] = [W, H], [0, 0, W, H]
    m["base"] = store.save_png_ca(root, "inputs", base)
    for cid, (lname, r) in zip(store.assign_crop_ids([n for n, _ in rects]), rects):
        x0, y0, x1, y1 = r
        p = store.parse_crop_layer_name(lname)
        src = np.dstack([base[y0:y1, x0:x1], np.full((y1 - y0, x1 - x0), 255, np.uint8)])
        m["crops"].append({"id": cid, "name": p["name"], "nnn": p["nnn"], "part": p["part"], "layer_name": lname,
                           "rect": list(r), "rect_source": "vector", "source": store.save_png_ca(root, "inputs", src),
                           "source_kind": "base_crop", "alpha_frac": 0.0, "targets": dp._new_targets(p, None),
                           "refs_auto": [], "warnings": []})
    store.save_manifest(root, m)
    return dp._handle(root, m), root


def _fx_cand(base, rect, scale=2):
    """크롭 창을 scale 배로 키우고 원 하나를 빨갛게 바꾼 'GPT 결과' 흉내(uint8 RGB)."""
    x0, y0, x1, y1 = rect
    w, h = (x1 - x0) * scale, (y1 - y0) * scale
    im = np.asarray(Image.fromarray(base[y0:y1, x0:x1]).resize((w, h), Image.LANCZOS)).copy()
    yy, xx = np.mgrid[0:h, 0:w]
    im[(yy - 0.6 * h) ** 2 + (xx - 0.4 * w) ** 2 < (0.2 * min(w, h)) ** 2] = (230, 40, 40)
    return im


def _fx_harvest_psd(path, size, items, persp=None):
    """03.수정 > <그룹> > SO 인 합성 결과 PSD. items = [(그룹, 레이어, rgb, quad, visible, mask|None)].
    persp = {레이어: 8코너} — PS 원근/왜곡 변형 흉내(SoLd Trnf·nonAffineTransform, PlLd transform 교체)."""
    wr = dpsd.PSDWriter(size)
    g = wr.add_group(None, "03.수정")
    groups, layers = {}, {}
    for gname, lname, rgb, quad, vis, mask in items:
        if gname not in groups:
            groups[gname] = wr.add_group(g, gname)
        layers[lname] = wr.add_smart_object(groups[gname], lname, png_bytes(rgb), quad=quad, visible=vis, mask=mask)
    for lname, q in (persp or {}).items():
        tb = layers[lname]._record.tagged_blocks
        sold = tb.get_data(Tag.SMART_OBJECT_LAYER_DATA1).data
        for i, v in enumerate(q):
            sold[b"Trnf"][i].value = v
            sold[b"nonAffineTransform"][i].value = v
        tb.get_data(Tag.PLACED_LAYER2).transform = tuple(q)
    wr.save(str(path))


def _sha_file(p) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def node_fixes(dp, NODES):
    FX = NODES / "fixes"
    OUTD = NODES / "output" / "design_patch"
    shutil.rmtree(FX, ignore_errors=True)
    for nm in ("fx_syn", "fx_branch", "fx_same", "fx_copy", "fx_dup"):
        shutil.rmtree(OUTD / nm, ignore_errors=True)
    FX.mkdir(parents=True)
    IMP, CIN, ANA, COM = (dp.BMKDesignPatchImportPSD(), dp.BMKDesignPatchCandidateIn(), dp.BMKDesignPatchAnalyze(),
                          dp.BMKDesignPatchCompose())

    def load(root):
        return store.load_manifest(root)

    section("N8a IS_CHANGED 에 링크된 입력(None) → NaN (DP-INT-4)")
    vals = [dp.BMKDesignPatchProject.IS_CHANGED(project=None, base_dir=""),
            dp.BMKDesignPatchProject.IS_CHANGED(project="x", base_dir=None),
            dp.BMKDesignPatchImportPSD.IS_CHANGED(psd_path=None, refs_dir="", glossary_path=""),
            dp.BMKDesignPatchCandidateIn.IS_CHANGED(source="psd_harvest", path=None)]
    check(all(isinstance(v, float) and math.isnan(v) for v in vals), f"DP-INT-4 링크 입력 IS_CHANGED = NaN {vals}")

    section("N8b 같은 이름 크롭 (DP-1): 03 + '030_팔매듭 | B' 큰 rect (read_crop_rects 를 이 테스트 안에서만 감쌈)")
    orig = dp.dpsd.read_crop_rects

    def with_dup(psd, group="auto"):
        rs = orig(psd, group)
        r30 = next(r for r in rs if r["name"] == "030_팔매듭")
        x0, y0, x1, y1 = r30["rect"]
        return [dict(r30, name="030_팔매듭 | B: second target", rect=[x0 - 128, y0 - 128, x1 + 128, y1 + 128], warnings=[])] + rs

    hd, _s = dp.BMKDesignPatchProject().open("fx_dup", str(FX))
    dp.dpsd.read_crop_rects = with_dup
    try:
        hd, _ov, _rep = IMP.run(hd, str(PSD03), "auto", "auto", "auto", "auto", "", "")
    finally:
        dp.dpsd.read_crop_rects = orig
    rootd = hd["root"]
    c30 = [c for c in load(rootd)["crops"] if c["name"] == "030_팔매듭"]
    s30 = [np.asarray(Image.open(store.resolve_path(rootd, c["source"]))) for c in c30]
    check(len(c30) == 2 and c30[1]["id"] == c30[0]["id"] + "-2" and s30[0].shape == (512, 512, 4)
          and s30[1].shape == (256, 256, 4) and np.array_equal(s30[0][128:384, 128:384], s30[1]),
          f"DP-1 Import: 같은 이름 크롭 2개가 각자 rect 의 소스 {[s.shape for s in s30]}")
    fd = FX / "dup_folder"
    fd.mkdir()
    Image.fromarray(_fx_cand(s30[0][..., :3], [0, 0, 512, 512], 1)).save(fd / "030_팔매듭_gpt.png")
    hd, rep_f = CIN.run(hd, "folder", str(fd), "", "", True, True)
    check("모호" in rep_f and not load(rootd)["candidates"], "DP-1 폴더: 파일명이 같은 이름 크롭 둘에 걸리면 모호 → 건너뜀")
    hd, rep_f = CIN.run(hd, "folder", str(fd), "", json.dumps({"030_팔매듭_gpt.png": {"crop": c30[0]["id"]}}), True, True)
    cd = load(rootd)["candidates"]
    check(len(cd) == 1 and cd[0]["crop_id"] == c30[0]["id"] and cd[0]["picked"], "DP-1 mapping_json 크롭 ID 로 첫 중복(512)에 등록")
    hd, _cs, rep_a = ANA.run(hd, "guarded_affine", "field", 16, 8.0, 9, 3.5, "none", False)
    ed = next(iter(load(rootd)["analysis"][cd[0]["key"]].values()))
    check("계산 1" in rep_a and not ed.get("skipped") and ed.get("automask"),
          "DP-1 첫 중복 크롭 후보 Analyze 성공(예전: base 256 vs cand 512 ValueError)")

    section("N8c 합성 프로젝트: 원근 SO·숨김 수확 후보·폴더 자동 pick·JPEG SO·분석 해시 (F2/R5, DP-2, F3, DP-4)")
    rng = np.random.default_rng(5)
    base = texture(384, 384, 11)
    R1_, R2_, R3_ = [32, 32, 160, 160], [192, 160, 320, 288], [64, 224, 160, 320]
    hs, roots = _fx_project(dp, FX, "fx_syn", [("001_왼쪽", R1_), ("002_오른쪽", R2_), ("003_아래", R3_)], base)
    hand = np.zeros((128, 128), np.uint8)
    cv2.circle(hand, (51, 77), 30, 255, -1)
    persp = [192.0, 160.0, 320.0, 166.0, 316.0, 288.0, 192.0, 288.0]
    hp = FX / "fx_syn_result.psd"
    _fx_harvest_psd(hp, (384, 384), [
        ("001_왼쪽", "001 결과", _fx_cand(base, R1_), dpsd.quad_from_rect(R1_), True, {"arr": hand, "left": 32, "top": 32, "bg": 0}),
        ("002_오른쪽", "002 결과(원근)", _fx_cand(base, R2_), dpsd.quad_from_rect(R2_), True, None),
        ("003_아래", "003 결과(숨김)", _fx_cand(base, R3_), dpsd.quad_from_rect(R3_), False, None)],
        persp={"002 결과(원근)": persp})
    hs, rep_h = CIN.run(hs, "psd_harvest", str(hp), "03.수정", "", True, True)
    ms = load(roots)
    by = {c["origin_info"]["layer"]: c for c in ms["candidates"]}
    check(len(by) == 3 and dpsd.quad_kind(by["002 결과(원근)"]["quad"]) == "perspective" and "원근 변형" in rep_h
          and not by["003 결과(숨김)"]["picked"], "수확: 원근 quad 그대로 + 경고, 숨김 SO 는 pick 아님")
    fe = FX / "empty_folder"
    fe.mkdir()
    hs, rep_e = CIN.run(hs, "folder", str(fe), "", "", True, True)
    check(not load(roots)["candidates"][2]["picked"] and "자동 선택" not in rep_e,
          "DP-2 빈 폴더 실행이 숨김(거절) 수확 후보를 다시 pick 하지 않음")
    ff = FX / "jpg_folder"
    ff.mkdir()
    Image.fromarray(_fx_cand(base, R3_)).save(ff / "003_아래_new.jpg", quality=92)
    hs, rep_j = CIN.run(hs, "folder", str(ff), "", "", True, True)
    ms = load(roots)
    newc = next(c for c in ms["candidates"] if c["origin"] == "folder")
    check(newc["picked"] and not by["003 결과(숨김)"]["picked"] and not next(
        c for c in ms["candidates"] if c["key"] == by["003 결과(숨김)"]["key"])["picked"],
          "DP-2 새 폴더 후보만 자동 pick(수확 후보는 그대로 숨김)")
    t0 = time.time()
    hc, img_c, _mk, rep_c = COM.run(hs, "hand_else_auto", False, True, "auto_small_on_top")
    check("평행사변형으로 근사" in rep_c and "002 결과(원근)" in rep_c, "F2/R5 Compose(정합 끔): 원근 SO 를 평행사변형으로 근사(예전: ValueError)")
    pe, ui_e = _run_export(dp, hc, "fx", "smart_object", "hidden_all", "raw", "hand_else_auto", True, True)
    rq = PSDImage.open(pe)
    sos = {x.name: x.smart_object for x in rq.descendants() if x.kind == "smartobject"}
    q2 = next(so.transform_box for nm, so in sos.items() if nm.startswith("002_오른쪽"))
    fj = [so.filename for nm, so in sos.items() if nm.startswith("003_아래") and so.filename.startswith("f_")]
    check(len(sos) == 4 and dpsd.quad_kind(list(q2)) == "parallelogram" and dpsd.verify_saved(pe) == [],
          f"F2/R5 Export(기본): 성공, 원근 SO quad → 평행사변형 {[round(v, 1) for v in q2]}")
    check(fj and fj[0].endswith(".png"), f"F3 JPEG 폴더 후보 raw 임베드 → lnk2 파일명 .png ({fj})")
    note(f"N8c 합성 Compose+Export {time.time() - t0:.1f}s")
    # DP-4: 분석 해시에 '분석 대상 여부(quad)'·손 마스크 위치가 들어감
    ms = load(roots)
    c1 = next(c for c in ms["candidates"] if c["origin_info"].get("layer") == "001 결과")
    _e, d0, h0, _w = dp._ensure_analysis(roots, ms, [c1])
    c1p = dict(c1, quad=dpsd.quad_from_rect([32, 32, 64, 64]))  # 부분 패치로 옮김 → 분석 생략이어야 함
    e1, d1, _h1, _w = dp._ensure_analysis(roots, ms, [c1p])
    c1m = dict(c1, hand_mask=dict(c1["hand_mask"], rect=[52, 52] + c1["hand_mask"]["rect"][2:]))
    e2, d2, _h2, _w = dp._ensure_analysis(roots, ms, [c1m])
    _e3, d3, h3, _w = dp._ensure_analysis(roots, ms, [c1])
    check(d0 + h0 == 1 and d1 == 1 and e1[c1["key"]].get("skipped") and d2 == 1 and d3 == 0 and h3 == 1,
          f"DP-4 quad 가 분석 대상 여부를 바꾸면 / 손 마스크 위치가 바뀌면 다시 분석, 원래 값은 캐시 ({d1},{d2},{d3})")
    # DP-INT-2: specs 가 가리키는 guide·하위 폴더 ref 가 바뀌면 Project IS_CHANGED 가 바뀜
    refs = FX / "refs"
    (refs / "bcut").mkdir(parents=True)
    Image.fromarray(base[:64, :64]).save(refs / "bcut" / "001_ref.png")
    (Path(roots) / "guides").mkdir()
    Image.fromarray(base[:32, :32]).save(Path(roots) / "guides" / "g.png")
    ms = load(roots)
    ms["source"]["refs_dir"] = str(refs).replace("\\", "/")
    store.save_manifest(roots, ms)
    (Path(roots) / "specs").mkdir()
    store.write_json_atomic(str(Path(roots) / "specs" / f"{ms['crops'][0]['id']}.json"),
                            {"guide": "guides/g.png", "targets": [{"id": "A", "refs": ["bcut/001_ref.png"]}]})
    ic = [dp.BMKDesignPatchProject.IS_CHANGED(project="fx_syn", base_dir=str(FX))]
    Image.fromarray(base[:40, :40]).save(Path(roots) / "guides" / "g.png")
    ic.append(dp.BMKDesignPatchProject.IS_CHANGED(project="fx_syn", base_dir=str(FX)))
    Image.fromarray(base[:72, :72]).save(refs / "bcut" / "001_ref.png")
    ic.append(dp.BMKDesignPatchProject.IS_CHANGED(project="fx_syn", base_dir=str(FX)))
    ic.append(dp.BMKDesignPatchProject.IS_CHANGED(project="fx_syn", base_dir=str(FX)))
    check(ic[0] != ic[1] != ic[2] and ic[2] == ic[3], "DP-INT-2 guide·하위 폴더 ref 가 바뀌면 Project 지문이 바뀜(그대로면 같음)")

    section("N8d Analyze/Compose 가지 둘 (DP-INT-3) + 분석 항목 보관 수·derived 정리 (R7)")
    yy, xx = np.mgrid[0:256, 0:256].astype(np.float32)
    gbase = np.stack([90 + 80 * xx / 255, 100 + 60 * yy / 255, 120 + 30 * (xx + yy) / 510], -1)
    gbase = np.clip(gbase + rng.normal(0, 2, gbase.shape), 0, 255).astype(np.uint8)
    hb, rootb = _fx_project(dp, FX, "fx_branch", [("001_test", [64, 64, 192, 192])], gbase)
    cand = np.asarray(Image.fromarray(gbase[64:192, 64:192]).resize((256, 256), Image.LANCZOS)).astype(np.float32)
    cy, cx = np.mgrid[0:256, 0:256]
    cand[(cy - 70) ** 2 + (cx - 70) ** 2 < 30 ** 2] += 26.0  # 중간 변화(ΔE ≈ 10): dE 8 은 잡고 dE 12 는 놓침
    cand[(cy - 180) ** 2 + (cx - 180) ** 2 < 30 ** 2] = (230, 30, 30)
    fb = FX / "branch_folder"
    fb.mkdir()
    Image.fromarray(np.clip(cand, 0, 255).astype(np.uint8)).save(fb / "001_test_gpt.png")
    hb, _ = CIN.run(hb, "folder", str(fb), "", "", True, True)
    h8, _cs, _r = ANA.run(hb, "off", "field", 16, 8.0, 9, 3.5, "none", False)
    h12, _cs, _r = ANA.run(hb, "off", "field", 16, 12.0, 9, 3.5, "none", False)
    r_b = load(rootb)["rev"]
    _h, _cs, r8b = ANA.run(hb, "off", "field", 16, 8.0, 9, 3.5, "none", False)
    _h, _cs, r12b = ANA.run(hb, "off", "field", 16, 12.0, 9, 3.5, "none", False)
    check("계산 0" in r8b and "계산 0" in r12b and load(rootb)["rev"] == r_b,
          "DP-INT-3 Analyze 가지 둘(dE 8/12)을 번갈아 실행 → 둘 다 캐시, rev 불변(예전: 매번 재계산·rev 증가)")
    hc8, _i, m8, _r = COM.run(h8, "auto", False, True, "auto_small_on_top")
    hc12, _i, m12, _r = COM.run(h12, "auto", False, True, "auto_small_on_top")
    a8, a12 = int((m8[0].numpy() < 0.5).sum()), int((m12[0].numpy() < 0.5).sum())
    check(a8 > a12 > 0 and load(rootb)["rev"] == r_b and "compose" not in load(rootb),
          f"DP-INT-3 Compose 는 자기 상류 Analyze 의 마스크(dE 8 {a8}px > dE 12 {a12}px), 매니페스트 불변")
    p8, _u = _run_export(dp, hc8, "fx8", "smart_object", "none", "toned", "auto", True, False)
    p12, _u = _run_export(dp, hc12, "fx12", "pixel", "none", "toned", "auto", True, False)
    k8 = (np.asarray(Image.open(os.path.splitext(p8)[0] + "_mask.png")) > 127).sum()
    k12 = (np.asarray(Image.open(os.path.splitext(p12)[0] + "_mask.png")) > 127).sum()
    p8b, u8b = _run_export(dp, hc8, "fx8", "smart_object", "none", "toned", "auto", True, False)
    check(k8 > k12 and p8b == p8 and "변경 없음" in u8b[0],
          f"DP-INT-3 Export 도 자기 가지 설정(마스크 {k8} > {k12}px), 다른 가지 export 뒤에도 '변경 없음'")
    key_b = load(rootb)["candidates"][0]["key"]
    full8 = next(e["tone"].get("full") for e in load(rootb)["analysis"][key_b].values() if e["params"]["dE"] == 8.0)
    for dE in (6.0, 7.0, 9.0, 10.0):
        ANA.run(hb, "off", "field", 16, dE, 9, 3.5, "none", False)
    mb = load(rootb)
    per = mb["analysis"][key_b]
    used = set().union(*(dp._derived_files(e) for v in mb["analysis"].values() for e in v.values()))
    on_disk = {"derived/" + f for f in os.listdir(Path(rootb) / "derived")}
    check(len(per) == 4 and sorted(e["params"]["dE"] for e in per.values()) == [6.0, 7.0, 9.0, 10.0]
          and on_disk == used and full8 and not os.path.exists(store.resolve_path(rootb, full8)),
          f"R7 설정 6개 → 최근 4개만 보관, 밀려난 항목 파일(톤 보정 풀해상도 포함) 삭제, 고아 파일 "
          f"{len(on_disk - used)}")

    section("N8e Export 경로 충돌·덮어쓰기 (DP-3 / DP-INT-1 / R1) + work_rect 자르기 (R4)")
    gb2 = texture(256, 320, 21)
    rr = [("001_a", [16, 16, 144, 144])]
    hA, rootA = _fx_project(dp, FX / "baseA", "fx_same", rr, gb2)
    hB, rootB = _fx_project(dp, FX / "baseB", "fx_same", rr, gb2)
    pA, _u = _run_export(dp, hA, "dp", "pixel", "none", "raw", "hand", True, True)
    shaA = _sha_file(pA)
    pB, _u = _run_export(dp, hB, "dp", "pixel", "none", "raw", "hand", True, False)
    pA2, uA2 = _run_export(dp, hA, "dp", "pixel", "none", "raw", "hand", True, True)
    check(hA["rev"] == hB["rev"] and pA != pB and Path(pB).name == f"dp_r{hB['rev']}_2.psd" and _sha_file(pA) == shaA
          and pA2 == pA and "변경 없음" in uA2[0] and [x.name for x in PSDImage.open(pA2)] == ["00.Base", "02.크롭영역", "03.수정"],
          f"DP-3/R1 같은 이름·같은 rev 두 프로젝트: B 는 {Path(pB).name}, A 파일 그대로, A 재실행은 A 자기 파일")
    sj = os.path.splitext(pA)[0] + ".json"
    snap = json.load(open(sj, encoding="utf-8"))
    snap["exports"][-1]["hash"] = "다른 export"  # 수정 전 충돌로 옆 스냅샷이 덮인 상태 흉내
    store.write_json_atomic(sj, snap)
    pA3, uA3 = _run_export(dp, hA, "dp", "pixel", "none", "raw", "hand", True, True)
    check(pA3 not in (pA, pB) and "변경 없음" not in uA3[0] and _sha_file(pA) == shaA,
          f"DP-3 옆 스냅샷 해시가 다르면 재사용하지 않고 새 이름 {Path(pA3).name}")
    cp_root = store.project_root(str(FX / "baseC"), "fx_copy")
    shutil.copytree(rootA, cp_root)
    hcp, _s = dp.BMKDesignPatchProject().open("fx_copy", str(FX / "baseC"))
    # (내용이 원본과 같으면 원본의 같은 export 를 '변경 없음'으로 돌려주는 게 맞음 → 옵션을 바꿔 새로 쓰게 함)
    pcp, _u = _run_export(dp, hcp, "dpc", "pixel", "none", "raw", "hand", True, True)
    check(hcp["project"] == "fx_copy" and load(cp_root)["project"] == "fx_copy" and Path(pcp).parent.name == "fx_copy",
          f"DP-INT-1 복사한 폴더: 핸들·매니페스트·출력 폴더 = 폴더 이름 ({Path(pcp).parent.name})")
    hA, rootA = _fx_project(dp, FX / "baseA", "fx_same", rr, gb2)  # 지우고 다시 만든 프로젝트(rev 다시 2)
    ms_ = load(rootA)
    ms_["work_rect"] = [-40, -40, 360, 296]  # 캔버스 320x256 을 넘는 옛 매니페스트 값
    store.save_manifest(rootA, ms_)
    pR, _u = _run_export(dp, dp._handle(rootA, ms_), "dp", "pixel", "none", "raw", "hand", True, True)
    b00 = PSDImage.open(pR)[0]
    check(pR not in (pA, pB, pA3) and _sha_file(pA) == shaA and b00.name == "00.Base" and tuple(b00.bbox) == (0, 0, 320, 256),
          f"R1 다시 만든 프로젝트도 덮어쓰지 않음({Path(pR).name}), R4 00.Base bbox {tuple(b00.bbox)}")


# ═════════════════════════════════════════════════════════════════
# M2-S. store 트랜잭션·3-way 병합·잠금·루트 레지스트리·가격표 (M2_SPEC §1) — 옛 tools/_dp_test_store2.py
#   T0 의존성·M1 API·M2 키 기본값  T1 merge_manifest 진리표  T2 update_manifest·commit_merge  T3 manifest_lock
#   T4 스레드 경합 4×100(잃은 갱신 0) + 폴링 중 저장  T5 루트 레지스트리  T6 가격표(badge 식 파싱)  T7 실측
# ═════════════════════════════════════════════════════════════════
def m2_store_unit():
    OUT = OUT_ROOT / "store"
    REAL_MANIFEST = REF / "_proto" / "m1_test_out" / "nodes" / "bmk_design_patch" / "m1_smoke" / "design_patch.json"
    OPENAI_NODES = COMFY / "comfy_api_nodes" / "nodes_openai.py"

    def make_project(base: Path, name: str, **fields) -> str:
        root = store.project_root(str(base), name)
        m = store.new_manifest(name)
        m.update(fields)
        store.save_manifest(root, m)
        return root


    def mtime(root: str) -> int:
        return os.stat(os.path.join(root, store.MANIFEST_NAME)).st_mtime_ns


    M1_KEYS = {"schema", "project", "rev", "canvas", "work_rect", "source", "base", "crops",
               "candidates", "analysis", "picks", "jobs", "exports"}
    M2_KEYS = {"calls", "rerolls", "rejects", "approvals"}


    # ═════════════════════════════════════════════════════════════════
    # T0. 의존성 · M1 API · M2 키 기본값
    # ═════════════════════════════════════════════════════════════════
    def t0_basics():
        section("T0. 의존성 · M1 API · M2 키 기본값")
        for banned in ("torch", "comfy", "folder_paths", "aiohttp"):
            check(banned not in sys.modules, f"store import 가 {banned} 를 불러옴")
        m1_api = ["project_root", "new_manifest", "load_manifest", "save_manifest", "save_bytes_ca", "save_png_ca", "pixel_sha",
                  "crop_id_for", "assign_crop_ids", "parse_crop_layer_name", "load_glossary", "translate", "match_refs",
                  "is_ref_cutout", "gpt_output_size", "gpt_image_custom_size_ok", "cell_key", "resolve_path",
                  "write_json_atomic", "file_fingerprint", "load_spec_override"]
        check(all(callable(getattr(store, n, None)) for n in m1_api), "M1 공개 함수 유지")
        m2_api = ["manifest_lock", "update_manifest", "merge_manifest", "commit_merge", "root_key", "register_root",
                  "known_roots", "resolve_board_root", "load_root_registry", "estimate_usd"]
        check(all(callable(getattr(store, n, None)) for n in m2_api), "M2 공개 함수")

        base = OUT / "t0"
        check(set(store.new_manifest("p")) == M1_KEYS, "new_manifest 는 M1 고정 키 그대로(M1 테스트 호환)")
        root = store.project_root(str(base), "p0")
        check(store.load_manifest(root) == store.new_manifest("p0"), "없는 매니페스트 → new_manifest(M1 동작 유지)")
        # M1 형식 디스크 파일: M2 키 없음, jobs 에 reps 없음 / null / 3
        m = store.new_manifest("p0")
        m["jobs"] = [{"cell_key": "a"}, {"cell_key": "b", "reps": None}, {"cell_key": "c", "reps": 3}, "junk"]
        m["rejects"] = None
        store.save_manifest(root, m)
        raw = json.loads(Path(root, store.MANIFEST_NAME).read_text(encoding="utf-8"))
        check("calls" not in raw and raw["rejects"] is None, "디스크 파일은 M1 형식(준비)")
        lm = store.load_manifest(root)
        check(set(lm) == M1_KEYS | M2_KEYS, f"load 후 키 = M1 + M2 ({sorted(set(lm) ^ (M1_KEYS | M2_KEYS))})")
        check(lm["calls"] == [] and lm["rerolls"] == {} and lm["rejects"] == {} and lm["approvals"] == {}, "M2 키 기본값(null 포함)")
        check([j.get("reps") if isinstance(j, dict) else j for j in lm["jobs"]] == [1, 1, 3, "junk"], "jobs[].reps 기본 1, 기존 값 유지")
        check(lm["rev"] == 1, "rev 유지")
        # 저장 후 다시 읽으면 그대로(기본값이 값을 바꾸지 않음)
        store.save_manifest(root, lm)
        lm2 = store.load_manifest(root)
        check({k: v for k, v in lm2.items() if k != "rev"} == {k: v for k, v in lm.items() if k != "rev"}, "기본값 채운 매니페스트 왕복")
        m3 = dict(lm2, calls=[{"call_id": "x_r1", "status": "done"}], rerolls={"x": 2})
        store.save_manifest(root, m3)
        lm3 = store.load_manifest(root)
        check(lm3["calls"] == [{"call_id": "x_r1", "status": "done"}] and lm3["rerolls"] == {"x": 2}, "M2 키 값은 덮지 않음")


    # ═════════════════════════════════════════════════════════════════
    # T1. merge_manifest 진리표
    # ═════════════════════════════════════════════════════════════════
    def sample() -> dict:
        m = store.new_manifest("mg")
        m.update(calls=[], rerolls={}, rejects={}, approvals={})
        m["rev"] = 5
        m["canvas"] = [100, 100]
        m["source"]["psd"] = "a.psd"
        m["extra"] = {"keep": 1}
        m["crops"] = [{"id": "001_aaaaaa", "name": "001_a", "rect": [0, 0, 10, 10]},
                      {"id": "002_bbbbbb", "name": "002_b", "rect": [10, 10, 20, 20]}]
        m["candidates"] = [{"key": "h_1", "crop_id": "001_aaaaaa", "tid": "A", "picked": False, "z": 1},
                           {"key": "h_2", "crop_id": "002_bbbbbb", "tid": "A", "picked": True, "z": 2}]
        m["jobs"] = [{"cell_key": "c1", "reps": 1, "n": 4}, {"cell_key": "c2", "reps": 1, "n": 4}]
        m["exports"] = [{"rev": 3, "psd": "a.psd", "time": "t1"}]
        m["calls"] = [{"call_id": "c1_r1", "status": "done"}]
        m["picks"] = {"002_bbbbbb/A": ["h_2"]}
        m["rerolls"] = {"c1": 1}
        m["approvals"] = {"abc123": {"time": "t", "count": 2}}
        m["analysis"] = {"h_1": {"p1": {"g": 0}, "p2": {"g": 1}}, "h_2": {"p1": {"g": 2}}}
        return m


    def three():
        b = sample()
        return b, copy.deepcopy(b), copy.deepcopy(b)


    def by_id(seq, f):
        return {it[f]: it for it in seq}


    def t1_merge():
        section("T1. merge_manifest 진리표")
        mg = store.merge_manifest

        # ── 최상위 키
        b, o, t = three()
        t["canvas"] = [200, 200]
        r, c = mg(b, o, t)
        check(r["canvas"] == [200, 200] and c == [], "ours==base → theirs")
        b, o, t = three()
        o["canvas"] = [300, 300]
        r, c = mg(b, o, t)
        check(r["canvas"] == [300, 300] and c == [], "theirs==base → ours")
        b, o, t = three()
        o["canvas"] = t["canvas"] = [400, 400]
        r, c = mg(b, o, t)
        check(r["canvas"] == [400, 400] and c == [], "둘이 같게 바뀜 → 그 값, 충돌 없음")
        b, o, t = three()
        o["canvas"], t["canvas"] = [1, 1], [2, 2]
        o["source"]["psd"], t["source"]["psd"] = "o.psd", "t.psd"
        r, c = mg(b, o, t)
        check(r["canvas"] == [1, 1] and r["source"]["psd"] == "o.psd" and sorted(c) == ["canvas", "source"],
              f"일반 키 둘 다 다르게 → ours + 충돌 {c}")
        b, o, t = three()
        o["x_ours"], t["x_theirs"] = 1, 2
        r, c = mg(b, o, t)
        check(r["x_ours"] == 1 and r["x_theirs"] == 2 and c == [], "한쪽에만 생긴 키 보존")
        b, o, t = three()
        del o["extra"]
        r, c = mg(b, o, t)
        check("extra" not in r and c == [], "ours 가 지운 키(theirs 그대로) → 삭제")
        b, o, t = three()
        del t["extra"]
        r, c = mg(b, o, t)
        check("extra" not in r and c == [], "theirs 가 지운 키(ours 그대로) → 삭제")
        b, o, t = three()
        del o["extra"]
        t["extra"] = {"keep": 2}
        r, c = mg(b, o, t)
        check("extra" not in r and c == ["extra"], "ours 삭제 vs theirs 수정 → 삭제(ours) + 충돌")
        b, o, t = three()
        o["rev"], t["rev"] = 99, 7
        r, c = mg(b, o, t)
        check(r["rev"] == 7 and c == [], "rev 는 병합 안 함 → theirs rev")
        b, o, t = three()
        t["canvas"] = [5, 5]
        o["canvas"] = (100, 100)  # 튜플·numpy 는 JSON 기본형으로 비교
        o["crops"][0]["rect"] = np.array([0, 0, 10, 10])
        r, c = mg(b, o, t)
        check(r["canvas"] == [5, 5] and r["crops"] == b["crops"] and c == [], "튜플/numpy 값은 같은 값으로 비교")
        # 입력 불변 + 결과가 입력과 공유하지 않음
        b, o, t = three()
        o["crops"][0]["name"] = "001_o"
        t["crops"][1]["name"] = "002_t"
        snaps = [copy.deepcopy(x) for x in (b, o, t)]
        r, c = mg(b, o, t)
        check([b, o, t] == snaps, "입력 dict 를 바꾸지 않음")
        r["crops"][0]["name"] = "changed"
        r["picks"]["002_bbbbbb/A"].append("zz")
        check(o["crops"][0]["name"] == "001_o" and b["picks"]["002_bbbbbb/A"] == ["h_2"] and t["picks"]["002_bbbbbb/A"] == ["h_2"],
              "결과가 입력 객체를 공유하지 않음")
        expect_raises(lambda: mg(None, {}, {}), "base 가 dict 아님 → ValueError")
        expect_raises(lambda: mg({}, [], {}), "ours 가 dict 아님 → ValueError")

        # ── id 목록 (crops.id)
        b, o, t = three()
        o["crops"][0]["name"] = "001_o"
        t["crops"][1]["name"] = "002_t"
        r, c = mg(b, o, t)
        check([x["name"] for x in r["crops"]] == ["001_o", "002_t"] and c == [], "다른 항목 수정 → 둘 다")
        b, o, t = three()
        o["crops"].append({"id": "003_cccccc", "name": "003"})
        t["crops"].append({"id": "004_dddddd", "name": "004"})
        r, c = mg(b, o, t)
        check([x["id"] for x in r["crops"]] == ["001_aaaaaa", "002_bbbbbb", "003_cccccc", "004_dddddd"] and c == [],
              "양쪽 추가 → ours 순서 뒤 theirs 새 항목")
        b, o, t = three()
        o["crops"] = list(reversed(o["crops"])) + [{"id": "003_cccccc"}]
        t["crops"].insert(0, {"id": "000_eeeeee"})
        r, c = mg(b, o, t)
        check([x["id"] for x in r["crops"]] == ["002_bbbbbb", "001_aaaaaa", "003_cccccc", "000_eeeeee"], "순서 = ours 순서(재정렬 유지)")
        b, o, t = three()
        del o["crops"][0]
        t["crops"][1]["name"] = "002_t"
        r, c = mg(b, o, t)
        check([x["name"] for x in r["crops"]] == ["002_t"] and c == [], "ours 삭제 + theirs 다른 항목 수정")
        b, o, t = three()
        del t["crops"][0]
        o["crops"][1]["name"] = "002_o"
        r, c = mg(b, o, t)
        check([x["name"] for x in r["crops"]] == ["002_o"] and c == [], "theirs 삭제 + ours 다른 항목 수정")
        b, o, t = three()
        o["crops"][0]["name"], t["crops"][0]["name"] = "001_o", "001_t"
        t["crops"][1]["name"] = "002_t"
        r, c = mg(b, o, t)
        check([x["name"] for x in r["crops"]] == ["001_o", "002_t"] and c == ["crops/001_aaaaaa"], f"같은 항목 다르게 → ours + 충돌 {c}")
        b, o, t = three()
        del o["crops"][0]
        t["crops"][0]["name"] = "001_t"
        r, c = mg(b, o, t)
        check([x["id"] for x in r["crops"]] == ["002_bbbbbb"] and c == ["crops/001_aaaaaa"], "ours 삭제 vs theirs 수정 → 삭제 + 충돌")
        b, o, t = three()
        del t["crops"][0]
        o["crops"][0]["name"] = "001_o"
        r, c = mg(b, o, t)
        check([x["name"] for x in r["crops"]] == ["001_o", "002_b"] and c == ["crops/001_aaaaaa"], "theirs 삭제 vs ours 수정 → ours 유지 + 충돌")
        b, o, t = three()
        del o["crops"][0]
        del t["crops"][0]
        t["crops"].append({"id": "009"})
        r, c = mg(b, o, t)
        check([x["id"] for x in r["crops"]] == ["002_bbbbbb", "009"] and c == [], "양쪽 삭제 → 삭제")
        b, o, t = three()
        o["crops"].append({"id": "005", "v": 1})
        t["crops"].append({"id": "005", "v": 1})
        t["crops"][0]["name"] = "001_t"
        r, c = mg(b, o, t)
        check([x["id"] for x in r["crops"]].count("005") == 1 and c == [], "같은 id 같은 내용 추가 → 하나, 충돌 없음")
        b, o, t = three()
        o["crops"].append({"id": "005", "v": 1})
        t["crops"].append({"id": "005", "v": 2})
        r, c = mg(b, o, t)
        check(by_id(r["crops"], "id")["005"]["v"] == 1 and c == ["crops/005"], "같은 id 다른 내용 추가 → ours + 충돌")
        # 항목은 원자 단위: 같은 후보를 양쪽이 다른 필드로 바꿔도 충돌(ours 항목 통째)
        b, o, t = three()
        t["candidates"][0]["picked"] = True
        o["candidates"][0]["z"] = 9
        r, c = mg(b, o, t)
        h1 = by_id(r["candidates"], "key")["h_1"]
        check(h1["z"] == 9 and h1["picked"] is False and c == ["candidates/h_1"], "같은 항목 다른 필드 → 항목 단위 ours + 충돌")

        # ── 다른 id 목록: candidates.key, jobs.cell_key, calls.call_id, exports.(rev, psd)
        b, o, t = three()
        o["candidates"].append({"key": "r_1", "crop_id": "001_aaaaaa"})
        t["candidates"].append({"key": "r_2", "crop_id": "001_aaaaaa"})
        t["candidates"][1]["picked"] = False
        o["jobs"][0]["reps"] = 2
        t["jobs"][1]["n"] = 8
        o["calls"].append({"call_id": "c2_r1", "status": "done"})
        t["calls"].append({"call_id": "c1_r2", "status": "inflight"})
        t["calls"][0]["credits"] = 1.5
        o["exports"].append({"rev": 5, "psd": "b.psd", "time": "t2"})
        t["exports"].append({"rev": 5, "psd": "c.psd", "time": "t3"})
        r, c = mg(b, o, t)
        check([x["key"] for x in r["candidates"]] == ["h_1", "h_2", "r_1", "r_2"] and r["candidates"][1]["picked"] is False,
              "candidates(key) 병합")
        check([(x["cell_key"], x["reps"], x["n"]) for x in r["jobs"]] == [("c1", 2, 4), ("c2", 1, 8)], "jobs(cell_key) 병합")
        check([(x["call_id"], x.get("credits")) for x in r["calls"]] == [("c1_r1", 1.5), ("c2_r1", None), ("c1_r2", None)],
              "calls(call_id) 병합")
        check([(x["rev"], x["psd"]) for x in r["exports"]] == [(3, "a.psd"), (5, "b.psd"), (5, "c.psd")] and c == [],
              f"exports(rev+psd) 병합 {c}")
        b, o, t = three()
        o["exports"].append({"rev": 5, "psd": "b.psd", "time": "o"})
        t["exports"].append({"rev": 5, "psd": "b.psd", "time": "t"})
        r, c = mg(b, o, t)
        check(len(r["exports"]) == 2 and r["exports"][1]["time"] == "o" and c == ["exports/5|b.psd"], f"exports 같은 (rev, psd) 충돌 {c}")

        # ── 항목 단위로 합칠 수 없는 목록 → 통째로 ours + 충돌
        b, o, t = three()
        o["crops"].append({"id": "001_aaaaaa", "dup": True})
        t["crops"][1]["name"] = "002_t"
        r, c = mg(b, o, t)
        check(r["crops"] == o["crops"] and c == ["crops"], "ours 에 id 중복 → 통째 ours + 충돌")
        b, o, t = three()
        o["jobs"].append({"no_cell_key": 1})
        t["jobs"][0]["n"] = 2
        r, c = mg(b, o, t)
        check(r["jobs"] == o["jobs"] and c == ["jobs"], "id 필드 없는 항목 → 통째 ours + 충돌")
        b, o, t = three()
        o["calls"].append({"call_id": "x"})
        t["calls"] = {"oops": 1}
        r, c = mg(b, o, t)
        check(r["calls"] == o["calls"] and c == ["calls"], "theirs 가 목록이 아님 → 통째 ours + 충돌")
        b, o, t = three()
        o["candidates"].append({"key": ["unhashable"]})
        t["candidates"].append({"key": "r_9"})
        r, c = mg(b, o, t)
        check(c == ["candidates"], "스칼라가 아닌 id → 통째 ours + 충돌")

        # ── 키 dict: picks / rejects / rerolls / approvals
        b, o, t = three()
        o["picks"]["001_aaaaaa/A"] = ["h_1"]
        t["picks"]["002_bbbbbb/A"] = ["h_2", "h_9"]
        o["rejects"]["h_1"] = True
        t["rejects"]["h_2"] = True
        o["rerolls"]["c2"] = 1
        t["rerolls"]["c1"] = 2
        o["approvals"]["h_o"] = {"time": "o", "count": 1}
        t["approvals"]["h_t"] = {"time": "t", "count": 3}
        r, c = mg(b, o, t)
        check(r["picks"] == {"002_bbbbbb/A": ["h_2", "h_9"], "001_aaaaaa/A": ["h_1"]}, f"picks 키 단위 {r['picks']}")
        check(r["rejects"] == {"h_1": True, "h_2": True}, "rejects 키 단위")
        check(r["rerolls"] == {"c1": 2, "c2": 1}, "rerolls 키 단위")
        check(set(r["approvals"]) == {"abc123", "h_o", "h_t"} and c == [], "approvals 키 단위, 충돌 없음")
        b, o, t = three()
        o["picks"]["002_bbbbbb/A"] = ["h_2", "h_o"]
        t["picks"]["002_bbbbbb/A"] = ["h_t"]
        o["rerolls"]["c1"], t["rerolls"]["c1"] = 2, 3
        r, c = mg(b, o, t)
        check(r["picks"]["002_bbbbbb/A"] == ["h_2", "h_o"] and r["rerolls"]["c1"] == 2
              and sorted(c) == ["picks/002_bbbbbb/A", "rerolls/c1"], f"같은 키 다르게 → ours + 충돌 {c}")
        b, o, t = three()
        del o["picks"]["002_bbbbbb/A"]
        t["picks"]["001_aaaaaa/A"] = ["h_1"]
        r, c = mg(b, o, t)
        check(r["picks"] == {"001_aaaaaa/A": ["h_1"]} and c == [], "ours 가 지운 키 + theirs 새 키")
        b, o, t = three()
        del b["rejects"]  # base 스냅샷에 키가 없음(옛 스냅샷) → 빈 dict 로 봄
        o["rejects"] = {"h_o": True}
        t["rejects"] = {"h_t": True}
        r, c = mg(b, o, t)
        check(r["rejects"] == {"h_o": True, "h_t": True} and c == [], "base 에 없는 컬렉션 = 빈 것")
        b, o, t = three()
        o["picks"]["x"] = []
        t["picks"] = ["not", "dict"]
        r, c = mg(b, o, t)
        check(r["picks"] == o["picks"] and c == ["picks"], "theirs picks 가 dict 아님 → 통째 ours + 충돌")

        # ── analysis: (후보, params_hash) 단위
        b, o, t = three()
        o["analysis"]["h_1"]["p3"] = {"g": 3}
        t["analysis"]["h_1"]["p4"] = {"g": 4}
        r, c = mg(b, o, t)
        check(list(r["analysis"]["h_1"]) == ["p1", "p2", "p3", "p4"] and c == [], "같은 후보에 다른 params_hash 추가 → 둘 다")
        b, o, t = three()
        del o["analysis"]["h_1"]["p1"]  # 오래된 항목 정리(최대 4개 규칙)
        t["analysis"]["h_2"]["p9"] = {"g": 9}
        r, c = mg(b, o, t)
        check(r["analysis"] == {"h_1": {"p2": {"g": 1}}, "h_2": {"p1": {"g": 2}, "p9": {"g": 9}}} and c == [], "정리 + 추가")
        b, o, t = three()
        o["analysis"]["h_1"]["p2"], t["analysis"]["h_1"]["p2"] = {"g": "o"}, {"g": "t"}
        t["analysis"]["h_2"]["p1"] = {"g": "t2"}
        r, c = mg(b, o, t)
        check(r["analysis"]["h_1"]["p2"] == {"g": "o"} and r["analysis"]["h_2"]["p1"] == {"g": "t2"} and c == ["analysis/h_1/p2"],
              f"같은 (후보, hash) 다르게 → ours + 충돌 {c}")
        b, o, t = three()
        del o["analysis"]["h_2"]
        t["analysis"]["h_1"]["p5"] = {}
        r, c = mg(b, o, t)
        check("h_2" not in r["analysis"] and "p5" in r["analysis"]["h_1"] and c == [], "ours 가 후보 통째 삭제(theirs 그대로) → 삭제")
        b, o, t = three()
        b["analysis"]["h_3"] = [1]
        o["analysis"]["h_3"], t["analysis"]["h_3"] = [2], [3]
        r, c = mg(b, o, t)
        check(r["analysis"]["h_3"] == [2] and c == ["analysis/h_3"], "dict 가 아닌 후보 값 → 후보 단위 ours + 충돌")
        b, o, t = three()
        o["analysis"]["h_1"] = {}
        t["analysis"]["h_1"] = {}
        o["analysis"]["h_2"]["p8"] = {}
        r, c = mg(b, o, t)
        check(r["analysis"]["h_1"] == {} and c == [], "양쪽 모두 가진 빈 후보는 유지")


    # ═════════════════════════════════════════════════════════════════
    # T2. update_manifest · commit_merge
    # ═════════════════════════════════════════════════════════════════
    def t2_transactions():
        section("T2. update_manifest · commit_merge")
        base = OUT / "t2"
        root = make_project(base, "tx", picks={"a/A": ["k1"]})
        rev0 = store.load_manifest(root)["rev"]

        res, rev = store.update_manifest(root, lambda m: m["picks"].__setitem__("b/A", ["k2"]) or "ok")
        check(res == "ok" and rev == rev0 + 1 and store.load_manifest(root)["picks"]["b/A"] == ["k2"], "update_manifest 저장 + 반환값")
        seen = {}
        store.update_manifest(root, lambda m: seen.setdefault("picks", dict(m["picks"])))
        check(seen["picks"] == {"a/A": ["k1"], "b/A": ["k2"]}, "mutate 는 디스크 최신본을 받음")
        mt = mtime(root)
        res, rev = store.update_manifest(root, lambda m: len(m["picks"]))
        check(res == 2 and rev == rev0 + 1 and mtime(root) == mt, "바뀐 것 없음 → 저장 안 함(rev·mtime 그대로)")

        def boom(m):
            m["picks"]["c/A"] = ["x"]
            raise KeyError("boom")
        expect_raises(lambda: store.update_manifest(root, boom), "mutate 예외 그대로 전파", exc=KeyError)
        check("c/A" not in store.load_manifest(root)["picks"] and mtime(root) == mt, "mutate 예외 → 저장 안 함")

        def bump_rev(m):
            m["rev"] = 999
            m["rejects"]["k1"] = True
        _r, rev = store.update_manifest(root, bump_rev)
        check(rev == rev0 + 2 and store.load_manifest(root)["rev"] == rev0 + 2, "mutate 가 바꾼 rev 는 무시")
        expect_raises(lambda: store.update_manifest(root, lambda m: m["picks"].__setitem__("d", object())), "직렬화 불가 값 → ValueError")
        check("d" not in store.load_manifest(root)["picks"], "직렬화 실패 → 디스크 불변")
        missing = store.project_root(str(base), "nope")
        expect_raises(lambda: store.update_manifest(missing, lambda m: None), "매니페스트 없음 → ValueError", contains="매니페스트")
        check(not os.path.exists(missing), "update_manifest 는 새 프로젝트를 만들지 않음")

        # commit_merge
        snap = store.load_manifest(root)
        rev_s = snap["rev"]
        mt = mtime(root)
        merged, rev, conf = store.commit_merge(root, snap, copy.deepcopy(snap))
        check(rev == rev_s and mtime(root) == mt and conf == [] and merged == store.load_manifest(root), "ours == base → 저장 안 함")
        ours = copy.deepcopy(snap)
        ours["canvas"] = [10, 20]
        keep_o, keep_b = copy.deepcopy(ours), copy.deepcopy(snap)
        merged, rev, conf = store.commit_merge(root, snap, ours)
        check(rev == rev_s + 1 and merged["rev"] == rev and store.load_manifest(root) == merged and conf == [], "동시 쓰기 없음 → ours 저장")
        check(ours == keep_o and snap == keep_b, "commit_merge 는 ours·before 를 바꾸지 않음")
        # 낡은 스냅샷 + 그 사이 보드 쓰기
        snap = store.load_manifest(root)
        ours = copy.deepcopy(snap)
        ours["analysis"]["k1"] = {"ph": {"g": 1}}
        ours["candidates"].append({"key": "k9", "picked": False})
        store.update_manifest(root, lambda m: (m["picks"].__setitem__("a/A", ["k3"]), m["rerolls"].__setitem__("cell", 1)))
        merged, rev, conf = store.commit_merge(root, snap, ours)
        disk = store.load_manifest(root)
        check(disk["picks"]["a/A"] == ["k3"] and disk["rerolls"] == {"cell": 1}, "보드가 쓴 picks/rerolls 유지")
        check(disk["analysis"]["k1"] == {"ph": {"g": 1}} and disk["candidates"][-1]["key"] == "k9", "노드가 쓴 analysis/candidates 반영")
        check(conf == [] and rev == snap["rev"] + 2 and disk == merged, "rev = 디스크 rev + 1, 반환 merged = 디스크")
        # ours 변경이 이미 디스크에 있음 → 저장 안 함
        snap = store.load_manifest(root)
        store.update_manifest(root, lambda m: m["rejects"].__setitem__("k9", True))
        ours = copy.deepcopy(snap)
        ours["rejects"]["k9"] = True
        mt, rev_d = mtime(root), store.load_manifest(root)["rev"]
        merged, rev, conf = store.commit_merge(root, snap, ours)
        check(rev == rev_d and mtime(root) == mt and conf == [], "이미 반영된 변경 → 저장 안 함")
        # M1 _snapshot 형식(JSON 문자열) before
        snap = store.load_manifest(root)
        before_txt = json.dumps(snap, sort_keys=True, ensure_ascii=False)
        ours = copy.deepcopy(snap)
        ours["work_rect"] = [1, 2, 3, 4]
        store.update_manifest(root, lambda m: m["approvals"].__setitem__("ph1", {"time": "t", "count": 2}))
        merged, rev, conf = store.commit_merge(root, before_txt, ours)
        disk = store.load_manifest(root)
        check(disk["work_rect"] == [1, 2, 3, 4] and "ph1" in disk["approvals"] and conf == [], "before = JSON 문자열(M1 _snapshot) 허용")
        # 충돌: 둘 다 같은 pick 키를 다르게
        snap = store.load_manifest(root)
        ours = copy.deepcopy(snap)
        ours["picks"]["a/A"] = ["mine"]
        store.update_manifest(root, lambda m: m["picks"].__setitem__("a/A", ["board"]))
        merged, rev, conf = store.commit_merge(root, snap, ours)
        check(conf == ["picks/a/A"] and store.load_manifest(root)["picks"]["a/A"] == ["mine"], f"충돌 → ours + conflicts {conf}")
        expect_raises(lambda: store.commit_merge(missing, snap, ours), "commit_merge 매니페스트 없음 → ValueError")
        check(not [f for f in os.listdir(root) if f.endswith(".tmp")], "임시 파일 잔존 없음")


    # ═════════════════════════════════════════════════════════════════
    # T3. manifest_lock
    # ═════════════════════════════════════════════════════════════════
    def t3_lock():
        section("T3. manifest_lock")
        base = OUT / "t3"
        root = make_project(base, "lk")
        root2 = make_project(base, "lk2")
        rev0 = store.load_manifest(root)["rev"]
        with store.manifest_lock(root):
            with store.manifest_lock(root):
                _r, rev = store.update_manifest(root, lambda m: m["rejects"].__setitem__("re", True))
        check(rev == rev0 + 1, "재진입(같은 스레드 중첩 + update_manifest)")

        variant = root.upper().replace("\\", "/") + "/"
        marks: dict = {}
        held = threading.Event()

        def holder(path, hold):
            with store.manifest_lock(path):
                held.set()
                time.sleep(hold)
                marks["released"] = time.perf_counter()

        th = threading.Thread(target=holder, args=(variant, 0.4))
        th.start()
        held.wait()
        t0 = time.perf_counter()
        store.update_manifest(root, lambda m: marks.__setitem__("mutate", time.perf_counter()))
        th.join()
        check(marks["mutate"] >= marks["released"], "다른 표기(대소문자·/·끝 구분자)의 같은 root 잠금이 다른 스레드를 막음")
        note(f"잠금 대기 {(marks['mutate'] - t0) * 1000:.0f}ms (보유 400ms)")

        held.clear()
        th = threading.Thread(target=holder, args=(root2, 0.6))
        th.start()
        held.wait()
        t0 = time.perf_counter()
        store.update_manifest(root, lambda m: m["rejects"].__setitem__("other", True))
        dt = time.perf_counter() - t0
        th.join()
        check(dt < 0.3, f"다른 root 는 서로 막지 않음({dt * 1000:.0f}ms)")
        expect_raises(lambda: store.manifest_lock("").__enter__(), "빈 root → ValueError")


    # ═════════════════════════════════════════════════════════════════
    # T4. 스레드 경합 — 잃은 갱신 0
    # ═════════════════════════════════════════════════════════════════
    def t4_stress():
        section("T4. 스레드 경합 4 × 100")
        base = OUT / "t4"
        n_threads, n_ops = 4, 100
        root = make_project(base, "stress", candidates=[{"key": f"c_T{i}", "crop_id": "001", "tid": "A", "pick_count": -1}
                                                        for i in range(n_threads)])
        rev_start = store.load_manifest(root)["rev"]
        errors: list[str] = []
        stats = {"stale": 0, "conflicts": [], "commits": 0, "lag": []}
        slock = threading.Lock()
        barrier = threading.Barrier(n_threads)

        def worker(tid: int):
            rnd = random.Random(1000 + tid)
            try:
                barrier.wait()
                snap = store.load_manifest(root)  # 노드처럼 잠금 밖에서 연다
                for i in range(n_ops):
                    if i % 2 == 0:
                        def pick(m, tid=tid, i=i):
                            m["picks"][f"T{tid}/{i}"] = [f"k{tid}_{i}"]
                            for c in m["candidates"]:
                                if c["key"] == f"c_T{tid}":
                                    c["pick_count"] = i
                        store.update_manifest(root, pick)
                    else:
                        ours = copy.deepcopy(snap)
                        ours["rejects"][f"T{tid}_{i}"] = True
                        ours["calls"].append({"call_id": f"T{tid}_r{i}", "status": "done"})
                        ours["analysis"].setdefault(f"c_T{tid}", {})[f"h{i}"] = {"i": i}
                        ours["approvals"][f"a{tid}_{i}"] = {"time": "t", "count": 1}
                        time.sleep(rnd.random() * 0.002)
                        _merged, rev, conf = store.commit_merge(root, snap, ours)
                        with slock:
                            stats["commits"] += 1
                            stats["conflicts"] += conf
                            stats["lag"].append(rev - 1 - snap["rev"])
                            stats["stale"] += rev - 1 != snap["rev"]
                        if rnd.random() < 0.25:  # 대부분은 계속 낡은 스냅샷을 쓴다
                            snap = store.load_manifest(root)
            except Exception as e:  # 스레드 오류는 모아서 보고
                errors.append(f"T{tid}: {type(e).__name__}: {e}")

        t0 = time.perf_counter()
        ths = [threading.Thread(target=worker, args=(k,)) for k in range(n_threads)]
        for th in ths:
            th.start()
        for th in ths:
            th.join()
        dt = time.perf_counter() - t0
        check(not errors, f"스레드 오류 없음 {errors[:3]}")
        fin = store.load_manifest(root)
        evens = [i for i in range(n_ops) if i % 2 == 0]
        odds = [i for i in range(n_ops) if i % 2]
        exp_picks = {f"T{t}/{i}": [f"k{t}_{i}"] for t in range(n_threads) for i in evens}
        check(fin["picks"] == exp_picks, f"picks 잃은 갱신 0 ({len(fin['picks'])}/{len(exp_picks)})")
        pc = {c["key"]: c["pick_count"] for c in fin["candidates"]}
        check(pc == {f"c_T{t}": evens[-1] for t in range(n_threads)}, f"candidates[].pick_count 최종값 {pc}")
        check(set(fin["rejects"]) == {f"T{t}_{i}" for t in range(n_threads) for i in odds}, f"rejects {len(fin['rejects'])}/200")
        ids = [c["call_id"] for c in fin["calls"]]
        check(len(ids) == len(set(ids)) == n_threads * len(odds), f"calls {len(ids)}/200, 중복 없음")
        check(len(fin["approvals"]) == n_threads * len(odds), f"approvals {len(fin['approvals'])}/200")
        check(all(set(fin["analysis"].get(f"c_T{t}", {})) == {f"h{i}" for i in odds} for t in range(n_threads)), "analysis 후보별 50개")
        check(stats["conflicts"] == [], f"충돌 0 ({stats['conflicts'][:3]})")
        check(fin["rev"] == rev_start + n_threads * n_ops, f"rev = 시작 + 400 (모든 조작이 정확히 한 번 저장) → {fin['rev'] - rev_start}")
        check(stats["stale"] > 0, "낡은 스냅샷 병합이 실제로 일어남")
        note(f"경합 {n_threads}x{n_ops}: {dt:.2f}s, 낡은 스냅샷 commit {stats['stale']}/{stats['commits']}, "
             f"최대 지연 {max(stats['lag'])} rev, 잃은 갱신 0, 충돌 0")

        # 대조군: 잠금·병합 없이 낡은 스냅샷을 그대로 저장하면 갱신을 잃는다(테스트가 민감함을 보임)
        r_naive = make_project(base, "naive")
        r_merge = make_project(base, "merge")
        for r in (r_naive, r_merge):
            snap = store.load_manifest(r)
            store.update_manifest(r, lambda m: m["picks"].__setitem__("X/A", ["k"]))
            ours = copy.deepcopy(snap)
            ours["rejects"]["h"] = True
            if r == r_naive:
                ours["rev"] = store.load_manifest(r)["rev"]
                store.save_manifest(r, ours)
            else:
                store.commit_merge(r, snap, ours)
        nv, mm = store.load_manifest(r_naive), store.load_manifest(r_merge)
        check("X/A" not in nv["picks"] and nv["rejects"] == {"h": True}, "대조군: 단순 저장은 보드 pick 을 잃음")
        check(mm["picks"] == {"X/A": ["k"]} and mm["rejects"] == {"h": True}, "commit_merge 는 둘 다 유지")

        # 잦은 읽기(보드 폴링·노드 _open) 중 저장: Windows 는 열린 파일로 os.replace 를 못 한다(WinError 5).
        # load_manifest 가 같은 잠금을 잡지 않으면 쓰기가 재시도 끝에 실패한다(잠금 전 실측 13회 중 10회 실패).
        r_poll = make_project(base, "poll", candidates=[{"key": f"k{i}", "pad": "x" * 4000} for i in range(40)])
        stop = time.perf_counter() + 2.0
        errs = {"read": [], "write": []}
        cnt = {"read": 0, "write": 0}

        def poll_writer(k):
            i = 0
            while time.perf_counter() < stop:
                try:
                    store.update_manifest(r_poll, lambda m: m["rejects"].__setitem__(f"w{k}_{i}", True))
                    cnt["write"] += 1
                except Exception as exc:  # 실패를 모아서 보고
                    errs["write"].append(f"{type(exc).__name__}: {exc}")
                i += 1

        def poll_reader():
            while time.perf_counter() < stop:
                try:
                    store.load_manifest(r_poll)
                    cnt["read"] += 1
                except Exception as exc:  # 실패를 모아서 보고
                    errs["read"].append(f"{type(exc).__name__}: {exc}")

        ths = [threading.Thread(target=poll_writer, args=(k,)) for k in range(2)] + [threading.Thread(target=poll_reader) for _ in range(3)]
        for th in ths:
            th.start()
        for th in ths:
            th.join()
        n_rej = len(store.load_manifest(r_poll)["rejects"])
        check(not errs["write"] and not errs["read"], f"폴링 중 저장 실패 0 / 읽기 실패 0 ({errs['write'][:1]} {errs['read'][:1]})")
        check(n_rej == cnt["write"] and cnt["write"] > 20, f"폴링 중 쓰기 모두 반영 {n_rej}/{cnt['write']}")
        note(f"읽기 3 + 쓰기 2 스레드 2초(매니페스트 {os.path.getsize(os.path.join(r_poll, store.MANIFEST_NAME)) // 1024}KB): "
             f"쓰기 {cnt['write']} · 읽기 {cnt['read']} · 실패 0")


    # ═════════════════════════════════════════════════════════════════
    # T5. 루트 레지스트리
    # ═════════════════════════════════════════════════════════════════
    def fresh_store(name: str):
        """프로세스 재시작 흉내: 같은 파일을 다른 모듈 이름으로 새로 읽는다(메모리 레지스트리 비어 있음)."""
        spec = importlib.util.spec_from_file_location(name, store.__file__)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod


    def t5_registry():
        section("T5. 루트 레지스트리")
        base = OUT / "t5"
        ra = os.path.abspath(make_project(base, "projA"))
        rb = os.path.abspath(make_project(base, "프로젝트B"))
        reg_file = Path(base, "bmk_design_patch", "_roots.json")
        check(not reg_file.exists(), "등록 전 레지스트리 파일 없음")
        ka = store.register_root(ra)
        check(re.fullmatch(r"[0-9a-f]{12}", ka) is not None, f"키 형식 {ka}")
        check(ka == hashlib.sha1(os.path.normcase(ra).encode("utf-8")).hexdigest()[:12], "키 = sha1(정규화 경로)[:12]")
        check(store.root_key(ra.upper()) == ka and store.root_key(ra.replace("\\", "/") + "/") == ka, "다른 표기의 같은 폴더 → 같은 키")
        check(store.register_root(ra) == ka, "재등록 → 같은 키")
        kb = store.register_root(rb + os.sep)
        check(kb != ka and store.resolve_board_root(kb) == rb, "한글 프로젝트 등록·해석")
        check(store.resolve_board_root(ka) == ra, "resolve_board_root(키) → root")
        kn = {r["key"]: r for r in store.known_roots()}
        check(set(kn) >= {ka, kb} and kn[ka]["root"] == ra and kn[ka]["project"] == "projA" and kn[kb]["project"] == "프로젝트B",
              "known_roots 내용")
        check(all(set(r) == {"key", "root", "project", "time"} for r in kn.values()), "known_roots 항목 키")
        data = json.loads(reg_file.read_text(encoding="utf-8"))
        check(data.get("schema") == "bmk.design_patch.roots/1" and set(data["roots"]) == {ka, kb}, "_roots.json 내용")
        check(all(set(e) == {"project", "time"} for e in data["roots"].values()), "_roots.json 에 절대 경로를 적지 않음(이름만)")
        check(not [f for f in os.listdir(reg_file.parent) if f.endswith(".tmp")], "레지스트리 원자 저장(임시 파일 없음)")

        for bad in (None, "", 123, b"0" * 12, ka.upper(), ka + "\n", " " + ka, ka[:11], ka + "0", "0" * 12, "../" + ka,
                    ra, "C:/Windows", "..", ka.replace(ka[0], "g", 1)):
            check(store.resolve_board_root(bad) is None, f"resolve_board_root 거부 {bad!r}")

        no_manifest = Path(base, "bmk_design_patch", "empty")
        no_manifest.mkdir(parents=True, exist_ok=True)
        expect_raises(lambda: store.register_root(str(no_manifest)), "매니페스트 없는 폴더 등록 거부", contains="매니페스트")
        outside = Path(OUT, "t5_outside", "proj")
        outside.mkdir(parents=True, exist_ok=True)
        store.write_json_atomic(str(outside / store.MANIFEST_NAME), store.new_manifest("proj"))
        expect_raises(lambda: store.register_root(str(outside)), "…/bmk_design_patch/<project> 모양이 아닌 경로 거부")
        bad_name = Path(base, "bmk_design_patch", "bad name")
        bad_name.mkdir(parents=True, exist_ok=True)
        store.write_json_atomic(str(bad_name / store.MANIFEST_NAME), store.new_manifest("x"))
        expect_raises(lambda: store.register_root(str(bad_name)), "규칙에 안 맞는 프로젝트 폴더 이름 거부")
        expect_raises(lambda: store.register_root(""), "빈 경로 거부")
        expect_raises(lambda: store.register_root(None), "None 거부")
        check(set(json.loads(reg_file.read_text(encoding="utf-8"))["roots"]) == {ka, kb}, "거부된 등록은 파일에 남지 않음")

        # 재시작 복구
        s2 = fresh_store("bmk_dp_store_restart1")
        check(s2.resolve_board_root(ka) is None and s2.known_roots() == [], "재시작 직후 메모리 레지스트리는 비어 있음")
        check(s2.load_root_registry(str(base)) == 2, "load_root_registry → 2개")
        check(s2.resolve_board_root(ka) == ra and s2.resolve_board_root(kb) == rb, "재시작 뒤 키로 해석")
        check(s2.load_root_registry(str(OUT / "t5_nowhere")) == 0, "레지스트리 없는 base_dir → 0")
        expect_raises(lambda: s2.load_root_registry(""), "빈 base_dir → ValueError")

        # 변조된 레지스트리: 키 불일치 / 폴더 밖 이름 / 매니페스트 없는 프로젝트 / 형식 오류 항목은 무시
        evil_root = os.path.join(str(Path(base, "bmk_design_patch")), "..", "..", "t5_outside", "proj")
        ghost = os.path.join(str(Path(base, "bmk_design_patch")), "ghost")
        data = json.loads(reg_file.read_text(encoding="utf-8"))
        data["roots"].update({
            "deadbeef0000": {"project": "projA"},
            store.root_key(evil_root): {"project": "../../t5_outside/proj"},
            store.root_key(ghost): {"project": "ghost"},
            store.root_key(str(no_manifest)): {"project": "empty"},
            "aaaaaaaaaaaa": "not a dict",
        })
        reg_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        s3 = fresh_store("bmk_dp_store_restart2")
        check(s3.load_root_registry(str(base)) == 2, "변조 항목 무시 → 유효 2개만")
        check(s3.resolve_board_root("deadbeef0000") is None and s3.resolve_board_root(store.root_key(evil_root)) is None
              and s3.resolve_board_root(store.root_key(ghost)) is None, "변조 키는 해석 안 됨")
        reg_file.write_text("{broken json", encoding="utf-8")
        s4 = fresh_store("bmk_dp_store_restart3")
        check(s4.load_root_registry(str(base)) == 0, "깨진 레지스트리 → 0 (예외 없음)")
        check(s4.register_root(ra) == ka and set(json.loads(reg_file.read_text(encoding="utf-8"))["roots"]) == {ka},
              "깨진 파일은 다음 등록 때 다시 씀")

        # 프로젝트 삭제 → 해석 안 됨
        os.remove(os.path.join(rb, store.MANIFEST_NAME))
        check(store.resolve_board_root(kb) is None and kb not in {r["key"] for r in store.known_roots()},
              "매니페스트가 사라진 프로젝트는 해석·목록에서 빠짐")


    # ═════════════════════════════════════════════════════════════════
    # T6. 가격표
    # ═════════════════════════════════════════════════════════════════
    def badge_tables() -> dict:
        src = OPENAI_NODES.read_text(encoding="utf-8")
        blk = src[src.index("class OpenAIGPTImageNodeV2"):]
        blk = blk[:blk.index("async def execute")]
        out = {}
        for name in ("ranges", "presets", "perImage"):
            mm = re.search(r"\$" + name + r"\s*:=\s*(\{.*?\})\s*;", blk, re.S)
            out[name] = json.loads(mm.group(1))
        fam = re.search(r'\$family\s*:=\s*\(\$model\s*=\s*"([^"]+)"\s*or\s*\$model\s*=\s*"([^"]+)"\)\s*\?\s*"([^"]+)"', blk)
        out["family"] = {fam.group(1): fam.group(3), fam.group(2): fam.group(3)}
        out["formula"] = ("$min := ($out[0] + $refs * $image[0]) * $n;" in blk and "$max := ($out[1] + $refs * $image[1]) * $n;" in blk
                          and "$out := ($preset != null) ? [$preset, $preset] : $range;" in blk)
        return out


    def badge_price(tb, model, quality, size, n, refs):
        fam = tb["family"].get(model, model)
        preset = tb["presets"].get(fam, {}).get(quality, {}).get(size)
        out = [preset, preset] if preset is not None else tb["ranges"][model][quality]
        img = tb["perImage"][model]
        return (out[0] + refs * img[0]) * n, (out[1] + refs * img[1]) * n


    def plain(x):
        return json.loads(json.dumps(x))


    def t6_price():
        section("T6. 가격표")
        tb = badge_tables()
        check(tb["formula"], "badge 식 모양(프리셋 우선·입력 이미지·n 곱) 그대로")
        check(plain(store._PRICE_RANGES) == tb["ranges"], "ranges 표 = badge")
        check(plain(store._PRICE_PRESETS) == tb["presets"], "presets 표 = badge")
        check(plain(store._PRICE_PER_INPUT_IMAGE) == tb["perImage"], "입력 이미지당 표 = badge")
        check(store._PRICE_FAMILY == tb["family"], "모델 가족(2.5-flare/sunburst → gpt-image-2.5) = badge")
        n_ok = n_all = 0
        legacy_sizes = ["1024x1024", "1024x1536", "1536x1024"]
        for model, quals in tb["ranges"].items():
            fam = tb["family"].get(model, model)
            for q in quals:
                sizes = list(tb["presets"].get(fam, {}).get(q, {})) or legacy_sizes
                for size in sizes:
                    for n in (1, 4, 8):
                        for refs in (0, 1, 3):
                            exp = badge_price(tb, model, q, size, n, refs)
                            got = store.estimate_usd(model, q, size, n, refs)
                            n_all += 1
                            n_ok += abs(got[0] - exp[0]) < 1e-6 and abs(got[1] - exp[1]) < 1e-6
        check(n_ok == n_all and n_all > 1000, f"badge 와 같은 값 {n_ok}/{n_all} (모델·품질·프리셋·n·입력 수 전수)")
        note(f"가격 전수 비교 {n_ok}/{n_all} 일치")
        samples = [
            (("gpt-image-2.5-sunburst", "max", [2048, 2048], 4, 2), (2.5428, 2.59)),
            (("gpt-image-2.5-flare", "high", "1024x1536", 1, 0), (0.0589, 0.0589)),
            (("gpt-image-2.5-sunburst", "xhigh", (2160, 3840), 2, 1), ((0.2544 + 0.0117) * 2, (0.2544 + 0.0176) * 2)),
            (("gpt-image-2", "medium", [2048, 1152], 1, 2), (0.0509 + 2 * 0.0098, 0.0509 + 2 * 0.0147)),
            (("gpt-image-1", "high", (1024, 1024), 2, 1), ((0.167 + 0.0019) * 2, (0.25 + 0.0019) * 2)),
        ]
        for args, exp in samples:
            got = store.estimate_usd(*args)
            check(abs(got[0] - exp[0]) < 1e-9 and abs(got[1] - exp[1]) < 1e-9, f"표본 {args} → {got} != {exp}")

        # Custom 근사
        sq = store.estimate_usd("gpt-image-2.5-sunburst", "max", [1536, 1536], 1, 0)
        exp_sq = 0.3013 + (1536 * 1536 - 1024 * 1024) / (2048 * 2048 - 1024 * 1024) * (0.6123 - 0.3013)
        check(sq[0] == sq[1] and abs(sq[0] - round(exp_sq, 6)) < 1e-9, f"정사각 Custom 1536² → 정사각 보간 {sq}")
        ns = store.estimate_usd("gpt-image-2.5-sunburst", "max", [1024, 2048], 1, 0)
        exp_lo = 0.2354 + (1024 * 2048 - 1024 * 1536) / (2048 * 1152 - 1024 * 1536) * (0.2424 - 0.2354)
        exp_hi = 0.3013 + (1024 * 2048 - 1024 * 1024) / (2048 * 2048 - 1024 * 1024) * (0.6123 - 0.3013)
        check(abs(ns[0] - round(exp_lo, 6)) < 1e-9 and abs(ns[1] - round(exp_hi, 6)) < 1e-9, f"비정사각 1024x2048 → (비정사각, 정사각) {ns}")
        low = store.estimate_usd("gpt-image-2.5-sunburst", "max", [640, 1024], 1, 0)
        check(abs(low[0] - round(0.2354 * 655360 / 1572864, 6)) < 1e-9 and abs(low[1] - round(0.3013 * 655360 / 1048576, 6)) < 1e-9,
              f"최소 화소 쪽은 끝점 화소 비례 {low}")
        big = store.estimate_usd("gpt-image-2.5-sunburst", "max", [2880, 2880], 1, 0)
        check(big == (1.0175, 1.0175), f"badge 범위 상한으로 자름 {big}")
        tiny = store.estimate_usd("gpt-image-2", "low", [480, 1440], 1, 0)
        check(tiny[0] >= 0.0019 and tiny[1] <= 0.0237, f"badge 범위 하한 안 {tiny}")
        def presets_monotone(pres: dict) -> bool:
            """badge 프리셋 점이 정사각·비정사각 각각 화소에 대해 줄지 않는지(2.5 low 비정사각은 0.0068 → 0.0067 로 준다)."""
            for square in (True, False):
                pts = sorted((int(w) * int(h), p) for w, h, p in ((*k.split("x"), v) for k, v in pres.items()) if (w == h) == square)
                if any(b[1] < a[1] for a, b in zip(pts, pts[1:])):
                    return False
            return True

        in_range = mono = True
        skipped = []
        for model, quals in store._PRICE_RANGES.items():
            for q, (rlo, rhi) in quals.items():
                pres = store._PRICE_PRESETS.get(store._PRICE_FAMILY.get(model, model), {}).get(q, {})
                check_mono = presets_monotone(pres)
                if not check_mono:
                    skipped.append(f"{model}/{q}")
                for aspect in ((1, 1), (2, 3), (1, 2), (5, 8), (1, 3)):
                    prev = (0.0, 0.0)
                    for s in range(16, 4000, 16):
                        w, h = aspect[0] * s, aspect[1] * s
                        if not store.gpt_image_custom_size_ok(w, h) or f"{w}x{h}" in pres:
                            continue
                        lo, hi = store.estimate_usd(model, q, [w, h], 1, 0)
                        in_range &= rlo - 1e-9 <= lo <= hi <= rhi + 1e-9
                        if check_mono:
                            mono &= lo >= prev[0] - 1e-9 and hi >= prev[1] - 1e-9
                        prev = (lo, hi)
        check(in_range, "모든 Custom 근사가 lo ≤ hi 이고 badge 범위 안")
        check(mono, "같은 종횡비에서 화소가 늘면 근사 가격이 줄지 않음(badge 프리셋이 단조인 품질)")
        check(skipped == ["gpt-image-2.5-flare/low", "gpt-image-2.5-sunburst/low"], f"비단조 badge 프리셋 {skipped}")
        note(f"badge 프리셋 자체가 비단조라 단조 검사 제외: {', '.join(skipped)} (1024x1536 0.0068 > 2048x1152 0.0067)")
        rows = []
        for w, h in ((2048, 2048), (1024, 1536), (1024, 2048), (1024, 2560), (1536, 2560), (2560, 1536), (1280, 1536), (1152, 1536)):
            lo, hi = store.estimate_usd("gpt-image-2.5-sunburst", "max", [w, h], 4, 2)
            rows.append(f"{w}x{h} ${lo:.3f}-{hi:.3f}")
        note("2.5 max n4 입력2 호출당: " + " | ".join(rows))

        expect_raises(lambda: store.estimate_usd("gpt-image-9", "max", [1024, 1024], 1, 0), "모르는 모델", contains="모델")
        expect_raises(lambda: store.estimate_usd("gpt-image-2", "max", [1024, 1024], 1, 0), "모델에 없는 품질", contains="품질")
        for bad_n in (0, -1, True, 1.5, "2"):
            expect_raises(lambda b=bad_n: store.estimate_usd("gpt-image-2", "low", [1024, 1024], b, 0), f"잘못된 n {bad_n!r}")
        for bad_r in (-1, True, 1.0, None):
            expect_raises(lambda b=bad_r: store.estimate_usd("gpt-image-2", "low", [1024, 1024], 1, b), f"잘못된 입력 수 {bad_r!r}")
        for bad_s in ("abc", [0, 10], [1024], None):
            expect_raises(lambda b=bad_s: store.estimate_usd("gpt-image-2", "low", b, 1, 0), f"잘못된 크기 {bad_s!r}")


    # ═════════════════════════════════════════════════════════════════
    # T7. 실측 — 실제 크기 매니페스트
    # ═════════════════════════════════════════════════════════════════
    def t7_bench():
        section("T7. 실측(M1 스모크 매니페스트)")
        if not REAL_MANIFEST.exists():
            print("SKIP: M1 스모크 매니페스트 없음", REAL_MANIFEST)
            return
        root = store.project_root(str(OUT / "t7"), "bench")
        os.makedirs(root, exist_ok=True)
        shutil.copyfile(REAL_MANIFEST, os.path.join(root, store.MANIFEST_NAME))
        m = store.load_manifest(root)
        size_kb = os.path.getsize(os.path.join(root, store.MANIFEST_NAME)) / 1024

        def timed(fn, k=10):
            t0 = time.perf_counter()
            for _ in range(k):
                fn()
            return (time.perf_counter() - t0) / k * 1000

        t_load = timed(lambda: store.load_manifest(root))
        t_noop = timed(lambda: store.update_manifest(root, lambda mm: None))
        cnt = iter(range(10**6))
        t_upd = timed(lambda: store.update_manifest(root, lambda mm: mm["rejects"].__setitem__(f"b{next(cnt)}", True)))
        snap = store.load_manifest(root)
        t_cm0 = timed(lambda: store.commit_merge(root, snap, copy.deepcopy(snap)))
        # 낡은 스냅샷 + 양쪽 컬렉션 변경(항목 단위 병합 경로)
        snap = store.load_manifest(root)
        ours = copy.deepcopy(snap)
        cands = ours["candidates"]
        cands[0]["z"] = 777
        first_an = next(iter(ours["analysis"]), None)
        if first_an:
            ours["analysis"][first_an]["bench_ph"] = {"g": 1}
        store.update_manifest(root, lambda mm: (mm["candidates"][-1].__setitem__("picked", True),
                                                mm["picks"].__setitem__("bench/A", [mm["candidates"][-1]["key"]])))
        t0 = time.perf_counter()
        merged, rev, conf = store.commit_merge(root, snap, ours)
        t_cm = (time.perf_counter() - t0) * 1000
        disk = store.load_manifest(root)
        check(conf == [] and disk["candidates"][0]["z"] == 777 and disk["candidates"][-1]["picked"] is True
              and disk["picks"]["bench/A"] == [disk["candidates"][-1]["key"]], "실데이터 3-way: 노드 변경 + 보드 pick 모두 유지")
        if first_an:
            check("bench_ph" in disk["analysis"][first_an], "실데이터 analysis 항목 단위 병합")
        note(f"매니페스트 {size_kb:.0f}KB (후보 {len(m['candidates'])}, 분석 {len(m['analysis'])}): load {t_load:.1f}ms · "
             f"update 무변경 {t_noop:.1f}ms · update 저장 {t_upd:.1f}ms · commit_merge 무변경 {t_cm0:.1f}ms · "
             f"commit_merge 병합 저장 {t_cm:.1f}ms")


    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    for fn in (t0_basics, t1_merge, t2_transactions, t3_lock, t4_stress, t5_registry, t6_price, t7_bench):
        try:
            fn()
        except Exception as e:  # 한 묶음이 죽어도 나머지는 돌림
            traceback.print_exc()
            check(False, f"{fn.__name__} 예외: {type(e).__name__}: {e}")


# ═════════════════════════════════════════════════════════════════
# M2-R. runner: 실행 계획·승인 해시·원장·mock/comfy.org 어댑터·wave·동시성·인터럽트·drain (M2_SPEC §2)
#   — 옛 tools/_dp_test_runner.py. with_comfy=True 면 C* (comfy import, CPU): HiddenHolder, 내장
#   OpenAIGPTImageNodeV2 와 sync_op 인자 동등성, 크레딧 분리, 오류 매핑, 다운로드, 인터럽트 형식.
#   유료 호출 없음: MockBackend 이거나 ComfyOrgBackend 의 sync_op/다운로드를 이 테스트 안에서만 가짜로 바꾼 것.
# ═════════════════════════════════════════════════════════════════
def m2_runner_unit(with_comfy: bool):
    import bmk_design_patch_runner as runner

    OUT = OUT_ROOT / "runner"
    MODEL = "gpt-image-2.5-sunburst"
    FAKE_TOKEN = "fake-token-for-tests-not-real"

    # ═════════════════════════════════════════════════════════════════
    # 픽스처
    # ═════════════════════════════════════════════════════════════════
    def synth(seed: int, w: int = 128, h: int = 128) -> np.ndarray:
        rng = np.random.default_rng(seed)
        small = rng.integers(0, 256, size=(max(2, h // 8), max(2, w // 8), 3), dtype=np.uint8)
        return np.asarray(Image.fromarray(small).resize((w, h), Image.Resampling.BILINEAR))


    def make_project(case: str, n_cells: int = 3, *, n: int = 2, reps: int = 1, size=(256, 256), multi: bool = False,
                     input_sizes=None) -> str:
        base = OUT / case
        shutil.rmtree(base, ignore_errors=True)
        root = store.project_root(str(base), "proj")
        os.makedirs(root)
        m = store.new_manifest("proj")
        for i in range(n_cells):
            nnn = f"{i + 1:03d}"
            cid = f"{nnn}_{i:06x}"
            rect = [100 + i * 300, 100, 228 + i * 300, 228]
            sizes = input_sizes or [(128, 128), (96, 96)]
            arrs = [synth(i * 10 + k, w, h) for k, (w, h) in enumerate(sizes)]
            inputs = []
            for k, a in enumerate(arrs):
                rel = store.save_png_ca(root, "inputs", a)
                inputs.append({"role": "canvas" if k == 0 else "ref", "file": rel, "sha": store.pixel_sha(a)})
            tids = ["A", "B"] if multi and i == 0 else ["A"]
            m["crops"].append({
                "id": cid, "name": f"{nnn}_part{i}", "nnn": nnn, "part": f"part{i}", "rect": rect, "rect_source": "vector",
                "source": inputs[0]["file"], "source_kind": "base_crop", "alpha_frac": 0.0,
                "targets": [{"tid": t, "label": f"label{t}", "name_en": "", "mode": "ref_correct", "refs": []} for t in tids],
                "refs_auto": [], "warnings": []})
            prompt = f"Edit crop {i} targets {','.join(tids)}"
            ck = store.cell_key(MODEL, "max", list(size), "opaque", prompt, [x["sha"] for x in inputs], "dp-prompt/1",
                                "V1_standard")
            m["jobs"].append({"cell_key": ck, "crop_id": cid, "tid": ",".join(tids), "variant": "V1_standard", "model": MODEL,
                              "quality": "max", "size": list(size), "n": n, "background": "opaque", "prompt": prompt,
                              "inputs": inputs, "template_version": "dp-prompt/1", "status": "pending", "reps": reps})
        store.save_manifest(root, m)
        return root


    def plan(root, **kw):
        return runner.plan_calls(store.load_manifest(root), root, **kw)


    def sa(p, approve="", backend=runner.BACKEND_MOCK):
        return runner.split_approval(p, approve, backend=backend)


    def approved(root, **kw):
        p = plan(root, **kw)
        h = sa(p)["pending_hash"]
        return sa(p, h), p


    def run(root, calls, backend, **kw):
        return asyncio.run(runner.run_calls(root, calls, backend, **kw))


    def ledger_files(root, ck):
        d = os.path.join(root, "cands", ck)
        return sorted(os.listdir(d)) if os.path.isdir(d) else []


    def sha_file(path) -> str:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()


    def jobs_of(root):
        return [j["cell_key"] for j in store.load_manifest(root)["jobs"]]


    def set_manifest(root, fn):
        store.update_manifest(root, fn)


    # ═════════════════════════════════════════════════════════════════
    # R0 import / 규약
    # ═════════════════════════════════════════════════════════════════
    def t_import():
        section("R0 import 의존성 / 모듈 머리")
        banned = [k for k in sys.modules if k == "torch" or k.startswith("comfy") or k == "folder_paths"]
        check(not banned, f"runner import 가 torch/comfy/folder_paths 를 끌어오지 않음: {banned[:5]}")
        check(runner.store is store, "runner.store 는 같은 store 모듈(단독 실행 폴백)")
        src = (PKG / "bmk_design_patch_runner.py").read_text(encoding="utf-8")
        check(src.startswith('"""BMK Design Patch Runner') and "from __future__ import annotations" in src, "모듈 머리")
        check(not any(ord(ch) >= 0x1F000 or ch == "\ufe0f" for ch in src), "소스에 이모지 없음")
        check('_TAG = "[ComfyUI_BMK_Nodes::DesignPatch]"' in src and "logger = logging.getLogger(__name__)" in src, "로깅 규약")
        be = runner.ComfyOrgBackend({"auth_token": FAKE_TOKEN, "unique_id": "7"})
        check(be.api is None and "comfy_api_nodes" not in sys.modules, "ComfyOrgBackend 생성은 comfy 를 import 하지 않음(지연)")
        check(FAKE_TOKEN not in repr(be) and FAKE_TOKEN not in repr(be.holder), "repr 에 토큰이 없음")


    # ═════════════════════════════════════════════════════════════════
    # R1 계획 / 승인
    # ═════════════════════════════════════════════════════════════════
    def t_plan():
        section("R1 plan_calls / pending_hash / split_approval")
        root = make_project("r1_plan", 3, reps=2)
        p = plan(root)
        cks = jobs_of(root)
        check(set(p) >= {"pending", "done", "inflight", "orphaned", "failed"}, "plan 버킷 키")
        ids = [e["call_id"] for e in p["pending"]]
        check(ids == [f"{ck}_r0" for ck in cks] + [f"{ck}_r1" for ck in cks], f"breadth-first 순서 {ids}")
        check(all(e["kind"] == "new" and e["billable"] and not e["preapproved"] for e in p["pending"]), "새 호출 = 과금·승인 필요")
        check(all(e["reason"].startswith("새 셀") for e in p["pending"]), "사유: 새 셀")
        aids = [runner.approval_id(e) for e in p["pending"]]
        check(aids == [f"{i}:n2" for i in ids], f"approval_id = call_id + n {aids[:1]}")
        h0 = runner.pending_hash(aids)
        check(h0 == runner.pending_hash(list(reversed(aids))) and len(h0) == 12 and int(h0, 16) >= 0, "pending_hash 순서 무관·12 hex")
        check(h0 != runner.pending_hash(aids[:-1]), "집합이 바뀌면 해시가 바뀜")
        h = runner.pending_hash(["backend:mock"] + aids)
        check(h == hashlib.sha256(",".join(sorted(["backend:mock"] + aids)).encode("ascii")).hexdigest()[:12],
              "해시 정의(sha256 정렬 목록 [:12], 백엔드 포함)")
        ap = sa(p, "")
        check(ap["pending_hash"] == h and not ap["approved_now"] and ap["to_run"] == [] and len(ap["blocked"]) == 6,
              "approve 없음 → 과금 0, 전부 blocked")
        ap2 = sa(p, "  " + h + " ")
        check(ap2["approved_now"] and len(ap2["to_run"]) == 6 and all(e["approved_by"] == f"hash:{h}" for e in ap2["to_run"]),
              "approve == pending_hash → 전부 실행, approved_by=hash:<h>")
        check(not sa(p, "000000000000")["approved_now"], "틀린 해시 → 승인 아님")
        hc = sa(p, "", runner.BACKEND_COMFY_ORG)["pending_hash"]
        apc = sa(p, h, runner.BACKEND_COMFY_ORG)
        check(hc not in ("", h) and not apc["approved_now"] and apc["to_run"] == []
              and sa(p, hc, runner.BACKEND_COMFY_ORG)["approved_now"],
              "S4: 승인 해시는 백엔드에 묶임 — MOCK 드라이런 해시로는 comfy_org 계획이 승인되지 않음")
        expect_raises(lambda: runner.split_approval(p, h), "split_approval 의 backend 는 필수 키워드", exc=TypeError)
        lo = sum(store.estimate_usd(MODEL, "max", [256, 256], 2, 2)[0] for _ in range(6))
        check(abs(ap["est_usd"][0] - round(lo, 4)) < 1e-6 and ap["est_usd"][1] >= ap["est_usd"][0], f"예상 USD 합 {ap['est_usd']}")
        p1 = plan(root, only="002")
        check([e["crop_id"] for e in p1["pending"]] == ["002_000001", "002_000001"] and p1["filtered"] == 4,
              f"only=NNN 필터 {p1['filtered']}")
        p2 = plan(root, only=f"001_000000, {cks[2][:8]}")
        check(sorted({e["crop_id"] for e in p2["pending"]}) == ["001_000000", "003_000002"], "only = crop_id + cell_key 접두")
        txt = runner.format_plan(p, ap)
        check(h in txt and "승인 필요 6건" in txt and "$" in txt, "format_plan: 해시·개수·USD")
        check(runner.estimate_seconds(6, 4) == 2 * runner.SECONDS_PER_CALL and runner.estimate_seconds(0) == 0, "예상 시간")
        # n 은 cell_key 에 없다 → n 만 바꿔도 call_id 는 같지만 승인 해시는 바뀌어 옛 approve 로 과금되지 않음
        set_manifest(root, lambda m: [j.update(n=4) for j in m["jobs"]])
        p4 = plan(root)
        ap4 = sa(p4, h)
        check([e["call_id"] for e in p4["pending"]] == ids and ap4["pending_hash"] != h and not ap4["approved_now"]
              and ap4["to_run"] == [], "n 2→4: 같은 call_id, 새 해시 → 옛 해시로는 과금 0")
        check(ap4["est_usd"][0] > ap["est_usd"][0], f"n 4 예상 USD 증가 {ap['est_usd']} → {ap4['est_usd']}")
        set_manifest(root, lambda m: [j.update(n=2) for j in m["jobs"]])
        check(sa(plan(root), h)["approved_now"], "n 을 되돌리면 같은 해시")
        # 잘못된 job 은 건너뜀(경고), 같은 cell_key 중복은 첫 것만
        def bad(m):
            m["jobs"].append({"cell_key": "../../evil"})
            m["jobs"].append(dict(m["jobs"][0]))
        set_manifest(root, bad)
        p3 = plan(root)
        check(len(p3["pending"]) == 6 and len(p3["warnings"]) == 2, f"잘못된/중복 job 경고 {p3['warnings']}")
        expect_raises(lambda: runner.call_id_for("../x", 0), "call_id_for: 잘못된 cell_key 거부")
        # reps=0 은 퇴역(재굴림만)
        set_manifest(root, lambda m: m["jobs"][0].update(reps=0))
        check(sum(e["cell_key"] == cks[0] for e in plan(root)["pending"]) == 0, "reps=0 이면 그 셀은 호출 없음")


    def t_only():
        section("R1b only: NNN 은 cell_key 접두가 아님(S7) · 보드 재굴림·다운로드 복구는 걸러지지 않음(WSU-4)")
        root = make_project("r1b_only", 3, n=1)
        cks = jobs_of(root)
        set_manifest(root, lambda m: m["jobs"][2].update(cell_key="001" + "c" * 21))  # 크롭 003 의 셀이 "001" 로 시작
        cks[2] = "001" + "c" * 21
        p = plan(root, only="001")
        check([e["crop_id"] for e in p["pending"]] == ["001_000000"] and p["filtered"] == 2,
              f"only=001 → 크롭 001 만(cell_key 가 001 로 시작하는 크롭 003 은 아님) {[e['crop_id'] for e in p['pending']]}")
        check([e["crop_id"] for e in plan(root, only="001ccc")["pending"]] == ["003_000002"], "cell_key 앞 6자 이상은 접두로 매칭")
        h_only = sa(p)["pending_hash"]
        set_manifest(root, lambda m: m["rerolls"].update({cks[1]: 1}))
        p = plan(root, only="001")
        ap = sa(p)
        check([(e["cell_key"], e["rep"], e["preapproved"]) for e in p["pending"]] == [(cks[0], 0, False), (cks[1], 1, True)]
              and [(e["cell_key"], e["approved_by"]) for e in ap["to_run"]] == [(cks[1], "board")]
              and ap["pending_hash"] == h_only and p["filtered"] == 2,
              "only 밖 셀의 보드 재굴림(사전 승인)은 그대로 실행, 승인 해시는 그대로")
        be = runner.MockBackend(fail_plan={"*": {"download_fail": 1}})
        run(root, ap["to_run"], be)
        p = plan(root, only="001")
        check([(e["cell_key"], e["kind"]) for e in p["pending"]][:1] == [(cks[1], "recover")],
              "only 밖 셀의 다운로드 복구(무료)도 걸러지지 않음")


    # ═════════════════════════════════════════════════════════════════
    # R2 기본 실행
    # ═════════════════════════════════════════════════════════════════
    def t_basic():
        section("R2 mock 실행 · 원장 · 원본 바이트 · 매니페스트 · 재큐잉 0 호출")
        root = make_project("r2_basic", 3, n=2)
        ap, p = approved(root)
        be = runner.MockBackend(delay_s=0.02)
        seen_progress = []
        rep = run(root, ap["to_run"], be, progress_cb=lambda d, t, s: seen_progress.append((d, t, s)))
        check(rep.launched == 3 and rep.done == 3 and rep.failed == 0 and rep.stopped_reason == "none", f"보고 {rep.text()}")
        check(len(be.requests) == 3, "mock 요청 3")
        check([x[:2] for x in seen_progress] == [(1, 3), (2, 3), (3, 3)] and "Design Patch Run" in seen_progress[-1][2],
              f"progress_cb {seen_progress[-1:]}")
        m = store.load_manifest(root)
        credits = 0.0
        for ck in jobs_of(root):
            fl = ledger_files(root, ck)
            check(fl == ["r0.done.json", "r0.req.json", "r0.resp.json", "r0_0.png", "r0_1.png"], f"원장 파일 {fl}")
            cid = f"{ck}_r0"
            served = be.served[cid]
            check([sha_file(os.path.join(root, "cands", ck, f"r0_{i}.png")) for i in range(2)] == served,
                  "slot 파일 = 서버가 준 바이트 그대로(sha)")
            req = json.load(open(os.path.join(root, "cands", ck, "r0.req.json"), encoding="utf-8"))
            check(req["approved_by"] == f"hash:{ap['pending_hash']}" and req["backend"] == "mock" and req["n"] == 2
                  and req["prompt"].startswith("Edit crop") and req["inputs"][0]["role"] == "canvas" and req["attempt"] == 1,
                  "req.json 내용")
            resp = json.load(open(os.path.join(root, "cands", ck, "r0.resp.json"), encoding="utf-8"))
            check([d["kind"] for d in resp["data"]] == ["url", "url"] and resp["credits"] is not None, "resp.json: url 여부·크레딧")
            credits += resp["credits"]
            call = next(c for c in m["calls"] if c["call_id"] == cid)
            check(call["status"] == "done" and call["slots"] == [f"cands/{ck}/r0_0.png", f"cands/{ck}/r0_1.png"]
                  and call["credits"] == resp["credits"] and call["backend"] == "mock" and len(call["urls"]) == 2
                  and call["approved_by"].startswith("hash:") and call["started"] and call["finished"], f"calls 요약 {call}")
        check(abs(rep.credits_total - round(credits, 4)) < 1e-6, f"크레딧 합 {rep.credits_total} vs {credits}")
        est_lo = store.estimate_usd(MODEL, "max", [256, 256], 2, 2)[0]
        check(abs(resp["credits"] - round(est_lo * 211, 2)) < 1e-6, "mock 크레딧 = 예상 USD lo × 211")
        cands = [c for c in m["candidates"] if c["origin"] == "run"]
        check(len(cands) == 6, f"후보 6 ({len(cands)})")
        ck0 = jobs_of(root)[0]
        c0 = next(c for c in cands if c["key"] == f"r_{ck0[:12]}_0_0")
        crop0 = m["crops"][0]
        x0, y0, x1, y1 = crop0["rect"]
        check(c0["key"] == f"r_{ck0[:12]}_0_0" and c0["crop_id"] == crop0["id"] and c0["tid"] == "A"
              and c0["file"] == f"cands/{ck0}/r0_0.png" and c0["src_size"] == [256, 256] and c0["picked"] is False
              and c0["z"] is None and c0["hand_mask"] is None and c0["quad"] == [x0, y0, x1, y0, x1, y1, x0, y1],
              f"후보 항목 {c0}")
        oi = c0["origin_info"]
        check(oi["call_id"] == f"{ck0}_r0" and oi["variant"] == "V1_standard" and oi["model"] == MODEL and oi["quality"] == "max"
              and oi["backend"] == "mock", f"origin_info {oi}")
        check(m["approvals"].get(ap["pending_hash"], {}).get("count") == 3, f"approvals 기록 {m['approvals']}")
        check(sorted(rep.new_candidates) == sorted(c["key"] for c in cands), "report.new_candidates")
        check("[MOCK]" in rep.text(), "보고서 MOCK 표시")
        d = rep.to_dict()
        check(rep.stopped_reason in runner.STOP_REASONS and json.loads(json.dumps(d)) == d
              and set(d) >= {"launched", "done", "failed", "orphaned", "remaining", "credits_total", "elapsed", "calls",
                             "stopped_reason"}, "RunReport.to_dict: 명세 필드 + JSON 직렬화 가능")
        img = np.asarray(Image.open(os.path.join(root, c0["file"])).convert("RGB"))
        canvas = np.asarray(Image.open(os.path.join(root, m["jobs"][0]["inputs"][0]["file"])).convert("RGB").resize((256, 256)))
        check(float(np.abs(img.astype(int) - canvas.astype(int)).mean()) > 10, "mock 결과는 눈에 띄게 다름(색조·도형·글자)")
        # 재큐잉 0 호출
        rev0 = store.load_manifest(root)["rev"]
        ap2, p2 = approved(root)
        check(p2["pending"] == [] and len(p2["done"]) == 3 and ap2["pending_hash"] == "", "다시 계획 → pending 0, done 3")
        rep2 = run(root, ap2["to_run"], be)
        check(len(be.requests) == 3 and rep2.launched == 0 and rep2.done == 0, "재큐잉 → 0 호출")
        check(store.load_manifest(root)["rev"] == rev0, "재큐잉은 매니페스트 rev 를 올리지 않음(재조정 멱등)")
        # b64 응답
        root_b = make_project("r2_b64", 2, n=3)
        apb, _ = approved(root_b)
        beb = runner.MockBackend(response="b64")
        repb = run(root_b, apb["to_run"], beb)
        ok = repb.done == 2 and not beb.downloads
        for ck in jobs_of(root_b):
            ok &= [sha_file(os.path.join(root_b, "cands", ck, f"r0_{i}.png")) for i in range(3)] == beb.served[f"{ck}_r0"]
            resp = json.load(open(os.path.join(root_b, "cands", ck, "r0.resp.json"), encoding="utf-8"))
            ok &= [d["kind"] for d in resp["data"]] == ["b64"] * 3 and all(d["url"] is None for d in resp["data"])
        check(ok, "b64 응답: 디코드한 바이트 그대로, resp.json 에는 b64 여부만(본문 없음)")


    # ═════════════════════════════════════════════════════════════════
    # R3 재굴림 사전 승인 / R4 n 변경
    # ═════════════════════════════════════════════════════════════════
    def t_rerolls_and_n():
        section("R3 rerolls 사전 승인 · R4 n 변경 시 기존 slot 보존")
        root = make_project("r3_reroll", 2, n=2)
        ap, _ = approved(root)
        be = runner.MockBackend()
        run(root, ap["to_run"], be)
        ck0, ck1 = jobs_of(root)
        shas_r0 = [sha_file(os.path.join(root, "cands", ck0, f"r0_{i}.png")) for i in range(2)]
        set_manifest(root, lambda m: m["rerolls"].update({ck0: 1}))
        p = plan(root)
        check([(e["call_id"], e["preapproved"], e["kind"]) for e in p["pending"]] == [(f"{ck0}_r1", True, "new")],
              f"재굴림 → r1 사전 승인 {[(e['call_id'], e['preapproved']) for e in p['pending']]}")
        check(p["pending"][0]["reason"].startswith("재굴림"), "사유: 재굴림")
        ap = sa(p, "")
        check(ap["pending_hash"] == "" and len(ap["to_run"]) == 1 and ap["to_run"][0]["approved_by"] == "board"
              and not ap["blocked"], "approve 없이도 사전 승인 호출은 실행, approved_by=board")
        # R4: 실행 전에 n 2→4 (cell_key 는 n 을 제외 → 같은 셀)
        set_manifest(root, lambda m: m["jobs"][0].update(n=4))
        p = plan(root)
        check(len(p["pending"]) == 1 and p["pending"][0]["n"] == 4 and len(p["done"]) == 2, "n 변경: 기존 호출 done 유지")
        ap = sa(p, "")
        rep = run(root, ap["to_run"], be)
        check(rep.done == 1 and len(be.requests) == 3 and be.requests[-1]["n"] == 4, "재굴림 1 호출(n=4)")
        req1 = json.load(open(os.path.join(root, "cands", ck0, "r1.req.json"), encoding="utf-8"))
        check(req1["approved_by"] == "board", "원장 req.approved_by = board")
        check([sha_file(os.path.join(root, "cands", ck0, f"r0_{i}.png")) for i in range(2)] == shas_r0,
              "기존 r0 slot 바이트 그대로")
        m = store.load_manifest(root)
        keys = sorted(c["key"] for c in m["candidates"] if c["crop_id"] == m["crops"][0]["id"])
        check(len(keys) == 6 and f"r_{ck0[:12]}_1_3" in keys, f"후보 2 + 4 = 6 ({len(keys)})")
        check(plan(root)["pending"] == [], "재굴림 소진 → pending 0")
        # calls_per_cell(reps) 1→2: 사용한 board 승인 1 → r2 는 승인 필요
        set_manifest(root, lambda m: m["jobs"][0].update(reps=2))
        p = plan(root)
        e = p["pending"][0] if p["pending"] else {}
        check(len(p["pending"]) == 1 and e.get("call_id") == f"{ck0}_r2" and not e.get("preapproved")
              and "호출 수 증가" in e.get("reason", ""), f"reps 증가분은 승인 필요 {e.get('call_id')} {e.get('reason')}")
        check(sa(p, "")["to_run"] == [], "승인 없으면 과금 0")
        # rerolls 2 로 올리면 1개만 사전 승인(남은 board 몫)
        set_manifest(root, lambda m: m["rerolls"].update({ck0: 2}))
        p = plan(root)
        check([(x["rep"], x["preapproved"]) for x in p["pending"]] == [(2, False), (3, True)],
              f"남은 board 몫만 사전 승인(뒤쪽 rep) {[(x['rep'], x['preapproved']) for x in p['pending']]}")


    # ═════════════════════════════════════════════════════════════════
    # R5 동시성 / R6 500 / R7 401
    # ═════════════════════════════════════════════════════════════════
    def t_concurrency_errors():
        section("R5 동시성 상한 · R6 500 격리 + retry_failed · R7 401 중지")
        root = make_project("r5_conc", 8, n=1)
        ap, _ = approved(root)
        be = runner.MockBackend(delay_s=0.15)
        t0 = time.monotonic()
        rep = run(root, ap["to_run"], be, concurrency=3)
        dt = time.monotonic() - t0
        check(rep.done == 8 and be.max_active == 3, f"동시 실행 상한 3 준수(최대 {be.max_active})")
        note(f"R5 8 호출 · 동시 3 · 지연 0.15s → {dt:.2f}s (순차 이론 {8 * 0.15:.2f}s)")
        # R6
        root = make_project("r6_500", 3, n=1)
        cks = jobs_of(root)
        ap, _ = approved(root)
        be = runner.MockBackend(fail_plan={f"{cks[1]}_r0": {"error": 500}})
        rep = run(root, ap["to_run"], be)
        check(rep.done == 2 and rep.failed == 1 and rep.stopped_reason == "none", f"500 은 그 호출만 실패 {rep.text()}")
        fl = ledger_files(root, cks[1])
        check(fl == ["r0.failed.json", "r0.req.json"], f"실패 원장 {fl}")
        f = json.load(open(os.path.join(root, "cands", cks[1], "r0.failed.json"), encoding="utf-8"))
        check(f["status"] == 500 and f["retryable"] is True and f["maybe_billed"] is False and f["phase"] == "request",
              f"failed.json {f}")
        m = store.load_manifest(root)
        check(next(c for c in m["calls"] if c["call_id"] == f"{cks[1]}_r0")["status"] == "failed", "calls 상태 failed")
        p = plan(root)
        check(len(p["failed"]) == 1 and p["pending"] == [], "retry_failed 없으면 실패는 다시 호출하지 않음")
        p = plan(root, retry_failed=True)
        check([(e["kind"], e["billable"], e["preapproved"]) for e in p["pending"]] == [("retry_failed", True, False)],
              "retry_failed → pending(과금, 승인 필요)")
        first = runner.pending_hash(["backend:mock", f"{cks[1]}_r0:n1"])  # 그 호출 하나만 승인했던 해시(approve 위젯에 남아 있을 수 있음)
        check(p["pending"][0]["attempt"] == 2 and runner.approval_id(p["pending"][0]) == f"{cks[1]}_r0:n1:a2"
              and not sa(p, first)["approved_now"],
              "재시도는 시도 번호가 해시에 들어가 첫 승인 해시로는 과금되지 않음")
        ap = sa(p, sa(p)["pending_hash"])
        be2 = runner.MockBackend()
        rep = run(root, ap["to_run"], be2)
        req = json.load(open(os.path.join(root, "cands", cks[1], "r0.req.json"), encoding="utf-8"))
        check(rep.done == 1 and req["attempt"] == 2 and req["kind"] == "retry_failed"
              and req["history"][0]["failed"]["status"] == 500
              and ledger_files(root, cks[1]) == ["r0.done.json", "r0.req.json", "r0.resp.json", "r0_0.png"],
              f"재시도: attempt 2, 이전 실패는 history 로 {ledger_files(root, cks[1])}")
        # R7
        root = make_project("r7_401", 4, n=1)
        cks = jobs_of(root)
        ap, _ = approved(root)
        be = runner.MockBackend(fail_plan={f"{cks[0]}_r0": {"error": 401}})
        rep = run(root, ap["to_run"], be, concurrency=1)
        check(rep.stopped_reason == "auth" and len(be.requests) == 1 and rep.launched == 0 and rep.failed == 0
              and rep.remaining == [f"{ck}_r0" for ck in cks], f"401 → 새 발사 중지, 전부 pending {rep.text()}")
        check(ledger_files(root, cks[0]) == [], "401 호출의 원장은 지워져 pending 으로")
        check("인증" in rep.text() and "다시 큐" in rep.text(), "보고: 인증 만료/거부 — 다시 큐")
        check(len(plan(root)["pending"]) == 4, "다시 계획하면 4건 그대로 pending")
        c = next(c for c in store.load_manifest(root)["calls"] if c["call_id"] == f"{cks[0]}_r0")
        check(c["status"] == "pending" and "401" in (c["error"] or ""), f"calls: pending + 오류 {c}")
        check(rep.continuation_hash == plan_hash(root), "continuation_hash = 다음 계획의 pending_hash")
        for status in (403, 402):
            root = make_project(f"r7_{status}", 2, n=1)
            ap, _ = approved(root)
            be = runner.MockBackend(fail_plan={"*": {"error": status}})
            rep = run(root, ap["to_run"], be, concurrency=1)
            check(rep.stopped_reason == "auth" and len(be.requests) == 1, f"{status} 도 auth 중지")


    def plan_hash(root) -> str:
        return sa(plan(root))["pending_hash"]


    # ═════════════════════════════════════════════════════════════════
    # R8 인터럽트 / R9 크래시
    # ═════════════════════════════════════════════════════════════════
    def interrupted_run(root, calls, backend_kw, *, trigger_at, concurrency=2):
        async def go():
            flag = {"v": False}
            be = runner.MockBackend(interrupt_check=lambda: flag["v"], **backend_kw)

            async def trig():
                await asyncio.sleep(trigger_at)
                flag["v"] = True

            t = asyncio.create_task(trig())
            try:
                rep = await runner.run_calls(root, calls, be, concurrency=concurrency, interrupt_check=lambda: flag["v"])
                return be, rep, None
            except BaseException as e:  # 인터럽트 예외 형식 확인용
                return be, getattr(e, "bmk_dp_report", None), e
            finally:
                t.cancel()

        return asyncio.run(go())


    def t_interrupt_crash():
        section("R8 인터럽트 · R9 크래시 시뮬레이션")
        root = make_project("r8_interrupt", 6, n=1)
        cks = jobs_of(root)
        ap, _ = approved(root)
        fast = {f"{ck}_r0": {"delay": 0.05} for ck in cks[:2]}
        be, rep, exc = interrupted_run(root, ap["to_run"], {"delay_s": 1.5, "fail_plan": fast}, trigger_at=0.5)
        check(isinstance(exc, runner.RunInterrupted), f"인터럽트는 다시 raise ({type(exc).__name__})")
        check(rep is not None and rep.stopped_reason == "interrupt", "예외에 bmk_dp_report 첨부")
        if rep is not None:
            check(rep.done == 2 and sorted(rep.orphaned) == sorted(f"{ck}_r0" for ck in cks[2:4])
                  and sorted(rep.remaining) == sorted(f"{ck}_r0" for ck in cks[4:]),
                  f"도착분 2 저장 · 진행 중 2 orphaned · 2 미발사 {rep.done} {rep.orphaned} {rep.remaining}")
            check("orphaned" in rep.text() and "retry_orphans" in rep.text(), "보고: orphaned 와 처리 방법")
        check(len(be.requests) == 4, f"발사 4 ({len(be.requests)})")
        for ck in cks[:2]:
            check("r0.done.json" in ledger_files(root, ck), "도착분은 done 저장")
        for ck in cks[2:4]:
            check(ledger_files(root, ck) == ["r0.inflight.json", "r0.req.json"], f"진행 중 → inflight 남음 {ledger_files(root, ck)}")
        p = plan(root)
        check(len(p["done"]) == 2 and len(p["orphaned"]) == 2 and len(p["pending"]) == 2, "다시 계획: done 2 · orphaned 2 · pending 2")
        m = store.load_manifest(root)
        st = {c["call_id"]: c["status"] for c in m["calls"]}
        check(all(st.get(f"{ck}_r0") == "orphaned" for ck in cks[2:4]), f"calls: orphaned {st}")
        # R8b wave 로 발사를 멈춘 뒤에 온 취소도 반드시 다시 raise
        root_w = make_project("r8_wave_then_cancel", 4, n=1)
        cw = jobs_of(root_w)
        apw, _ = approved(root_w)

        async def wave_then_cancel():
            flag = {"v": False}
            be = runner.MockBackend(delay_s=1.0, fail_plan={f"{cw[0]}_r0": {"delay": 0.05}}, interrupt_check=lambda: flag["v"])

            async def trig():
                await asyncio.sleep(0.5)
                flag["v"] = True

            t = asyncio.create_task(trig())
            try:
                r = await runner.run_calls(root_w, apw["to_run"], be, concurrency=3, wave_minutes=0.03 / 60,
                                           interrupt_check=lambda: flag["v"])
                return r, None
            except BaseException as e:
                return getattr(e, "bmk_dp_report", None), e
            finally:
                t.cancel()

        rw, ew = asyncio.run(wave_then_cancel())
        check(isinstance(ew, runner.RunInterrupted) and rw is not None and rw.stopped_reason == "interrupt"
              and rw.done == 1 and len(rw.orphaned) == 2 and rw.remaining == [f"{cw[3]}_r0"],
              f"wave 중지 후 취소 → 인터럽트로 raise ({type(ew).__name__}, {rw and rw.stopped_reason})")
        # R8c 다운로드 중 취소 → 응답은 resp.json 에 있어 다음 실행이 다운로드만(재과금 0)
        root_d = make_project("r8_download_cancel", 2, n=2)
        cd = jobs_of(root_d)
        apd, _ = approved(root_d)
        bed = runner.MockBackend(fail_plan={f"{cd[0]}_r0": {"interrupt": "download"}})
        try:
            run(root_d, apd["to_run"], bed, concurrency=1)
            check(False, "다운로드 중 취소: 예외가 나지 않음")
        except runner.RunInterrupted as e:
            check(e.bmk_dp_report.done == 0 and not e.bmk_dp_report.orphaned and e.bmk_dp_report.remaining == [f"{cd[1]}_r0"],
                  "다운로드 중 취소 → orphaned 아님(응답 있음), 나머지 미발사")
        check(sorted(ledger_files(root_d, cd[0])) == ["r0.inflight.json", "r0.req.json", "r0.resp.json"], "resp.json 보존")
        st = {c["call_id"]: c["status"] for c in store.load_manifest(root_d)["calls"]}
        check(st.get(f"{cd[0]}_r0") == "orphaned", f"calls: orphaned(다음 실행이 다운로드만) {st}")
        pd = plan(root_d)
        check([(e["call_id"], e["kind"]) for e in pd["pending"]][0] == (f"{cd[0]}_r0", "recover"), "다음 계획: recover 먼저")
        bed.fail_plan.clear()
        n_req = len(bed.requests)
        rd = run(root_d, sa(pd, sa(pd)["pending_hash"])["to_run"], bed)
        check(rd.recovered == 1 and rd.done == 2 and len(bed.requests) == n_req + 1, "복구(0 과금) + 남은 1 호출")
        # R9a orphaned(inflight 만): retry_orphans 일 때만 재호출
        ap, p = approved(root)
        be2 = runner.MockBackend()
        run(root, ap["to_run"], be2)
        check(sorted(r["call_id"] for r in be2.requests) == sorted(f"{ck}_r0" for ck in cks[4:]),
              "retry_orphans 끔 → orphaned 는 호출 안 함")
        p = plan(root, retry_orphans=True)
        check(sorted((e["call_id"], e["kind"]) for e in p["pending"]) == sorted((f"{ck}_r0", "retry_orphan") for ck in cks[2:4])
              and all(e["billable"] and not e["preapproved"] for e in p["pending"]), "retry_orphans → 재호출(과금·승인 필요)")
        ap = sa(p, sa(p)["pending_hash"])
        be3 = runner.MockBackend()
        rep = run(root, ap["to_run"], be3)
        req = json.load(open(os.path.join(root, "cands", cks[2], "r0.req.json"), encoding="utf-8"))
        check(rep.done == 2 and len(be3.requests) == 2 and req["attempt"] == 2 and "inflight" in req["history"][0],
              "orphan 재호출: attempt 2, 이전 inflight 는 history")
        # R9b resp.json 있음(다운로드 실패) → 다운로드만 다시, 재과금 0
        root = make_project("r9_download", 2, n=2)
        cks = jobs_of(root)
        ap, _ = approved(root)
        be = runner.MockBackend(fail_plan={f"{cks[0]}_r0": {"download_fail": 1}})
        rep = run(root, ap["to_run"], be)
        check(rep.done == 1 and rep.failed == 1, f"다운로드 실패 1 {rep.text()}")
        f = json.load(open(os.path.join(root, "cands", cks[0], "r0.failed.json"), encoding="utf-8"))
        check(f["phase"] == "download" and f["retryable"] is True and f["maybe_billed"] is True
              and "r0.resp.json" in ledger_files(root, cks[0]), "failed(download) + resp.json 보존")
        p = plan(root)
        e = p["pending"][0] if p["pending"] else {}
        check(len(p["pending"]) == 1 and e.get("kind") == "recover" and e.get("billable") is False, "다음 계획: recover(과금 없음)")
        ap = sa(p, "")
        check(ap["pending_hash"] == "" and ap["to_run"] and ap["to_run"][0]["approved_by"] == "retry", "복구는 승인 없이 실행")
        n_req = len(be.requests)
        rep = run(root, ap["to_run"], be)
        check(rep.done == 1 and rep.recovered == 1 and rep.launched == 0 and len(be.requests) == n_req,
              f"다운로드만 다시 — 재과금 0 {rep.text()}")
        check([sha_file(os.path.join(root, "cands", cks[0], f"r0_{i}.png")) for i in range(2)] == be.served[f"{cks[0]}_r0"]
              and "r0.failed.json" not in ledger_files(root, cks[0]), "복구한 바이트 그대로, failed 정리")
        # R9c 일부 slot 저장 후 크래시(done 없음, inflight 있음): 남은 slot 만 다운로드
        ck = cks[1]
        d = os.path.join(root, "cands", ck)
        keep = sha_file(os.path.join(d, "r0_0.png"))
        mtime = os.stat(os.path.join(d, "r0_0.png")).st_mtime_ns
        os.remove(os.path.join(d, "r0.done.json"))
        os.remove(os.path.join(d, "r0_1.png"))
        store.write_json_atomic(os.path.join(d, "r0.inflight.json"), {"call_id": f"{ck}_r0", "started": "x", "pid": 1})
        p = plan(root)
        check([(e["call_id"], e["kind"]) for e in p["pending"]] == [(f"{ck}_r0", "recover")], "resp+inflight → recover")
        dl0 = len(be.downloads)
        rep = run(root, sa(p)["to_run"], be)
        check(rep.recovered == 1 and len(be.requests) == n_req and len(be.downloads) == dl0 + 1, "남은 slot 1개만 다운로드")
        check(sha_file(os.path.join(d, "r0_0.png")) == keep and os.stat(os.path.join(d, "r0_0.png")).st_mtime_ns == mtime
              and sha_file(os.path.join(d, "r0_1.png")) == be.served[f"{ck}_r0"][1] and not os.path.exists(os.path.join(d, "r0.inflight.json")),
              "이미 저장한 slot 은 그대로(다시 쓰지 않음)")
        # R9d 다운로드 영구 실패(404) → partial done, 재과금 없음
        root = make_project("r9_gone", 1, n=2)
        ck = jobs_of(root)[0]
        ap, _ = approved(root)
        be = runner.MockBackend(fail_plan={f"{ck}_r0": {"download_status": 404}})
        rep = run(root, ap["to_run"], be)
        done = json.load(open(os.path.join(root, "cands", ck, "r0.done.json"), encoding="utf-8"))
        check(rep.done == 1 and done["partial"] is True and done["missing"] == [0, 1] and done["slots"] == []
              and plan(root)["pending"] == [] and any("영구 실패" in w for w in rep.warnings),
              "404 → partial done(받은 것만), 다시 계획해도 0 호출")
        # R9e 빈 응답 → failed(response), resp.json 없이 failed 에 요약
        root = make_project("r9_empty", 1, n=2)
        ck = jobs_of(root)[0]
        ap, _ = approved(root)
        rep = run(root, ap["to_run"], runner.MockBackend(fail_plan={"*": {"empty": True}}))
        f = json.load(open(os.path.join(root, "cands", ck, "r0.failed.json"), encoding="utf-8"))
        check(rep.failed == 1 and f["phase"] == "response" and f["maybe_billed"] is True and f["resp"]["data"] == []
              and len(plan(root)["failed"]) == 1, "빈 응답 → failed(과금됐을 수 있음)")
        # R9f 일부만 돌려줌(n_return) → 받은 만큼 done
        root = make_project("r9_partial_n", 1, n=4)
        ap, _ = approved(root)
        rep = run(root, ap["to_run"], runner.MockBackend(fail_plan={"*": {"n_return": 3}}))
        m = store.load_manifest(root)
        check(rep.done == 1 and len([c for c in m["candidates"] if c["origin"] == "run"]) == 3, "n=4 요청에 3장 → 3 후보")


    # ═════════════════════════════════════════════════════════════════
    # R10 wave / R11 max_new_calls / R12 drain
    # ═════════════════════════════════════════════════════════════════
    def t_limits():
        section("R10 wave · R11 max_new_calls · R12 drain")
        root = make_project("r10_wave", 5, n=1)
        ap, _ = approved(root)
        be = runner.MockBackend(delay_s=0.3)
        rep = run(root, ap["to_run"], be, concurrency=1, wave_minutes=0.5 / 60)
        check(rep.stopped_reason == "wave" and rep.launched == 2 and len(rep.remaining) == 3 and rep.done == 2,
              f"wave 0.5s · 지연 0.3s · 동시 1 → 2 발사 {rep.launched} {rep.remaining}")
        check(rep.continuation_hash == plan_hash(root) and rep.continuation_hash, "남은 것의 해시 = 다음 pending_hash")
        check("wave" in rep.text(), "보고: wave")
        root = make_project("r10_wave_inflight", 3, n=1)
        ap, _ = approved(root)
        be = runner.MockBackend(delay_s=0.4)
        rep = run(root, ap["to_run"], be, concurrency=2, wave_minutes=0.1 / 60)
        check(rep.launched == 2 and rep.done == 2 and len(rep.remaining) == 1, "wave: 진행 중 호출은 끝까지 받음")
        # R11
        root = make_project("r11_max", 5, n=1)
        ap, _ = approved(root)
        be = runner.MockBackend()
        rep = run(root, ap["to_run"], be, max_new_calls=2)
        check(rep.stopped_reason == "max_calls" and rep.launched == 2 and len(be.requests) == 2 and len(rep.remaining) == 3,
              f"max_new_calls 2 {rep.text()}")
        rep = run(root, approved(root)[0]["to_run"], be, max_new_calls=0)
        check(rep.launched == 0 and len(be.requests) == 2, "max_new_calls 0 → 발사 없음")
        # R12 drain — 보드 POST /drain 경로(request_drain)
        root = make_project("r12_drain", 5, n=1)
        ap, _ = approved(root)
        be = runner.MockBackend(delay_s=0.05)
        seen = {}
        check(runner.request_drain(root) is False and not runner.drain_requested(root), "실행 중이 아니면 drain 은 아무것도 안 함")

        def cb(done, total, text):
            if done == 1:
                seen["running"] = runner.is_running(root)
                seen["drain"] = runner.request_drain(root)

        rep = run(root, ap["to_run"], be, concurrency=1, progress_cb=cb)
        check(seen == {"running": True, "drain": True} and rep.stopped_reason == "drain" and rep.done == 1
              and len(rep.remaining) == 4, f"drain → 새 발사 중지, 정상 반환 {seen} {rep.text()}")
        check(not runner.is_running(root) and not runner.drain_requested(root), "실행 끝 → 등록·drain 플래그 해제")
        rep = run(root, approved(root)[0]["to_run"], be, concurrency=1)
        check(rep.done == 4, "지난 drain 이 다음 실행을 막지 않음")
        root = make_project("r12_drain_flag", 3, n=1)
        ap, _ = approved(root)
        rep = run(root, ap["to_run"], runner.MockBackend(delay_s=0.05), concurrency=2, drain_flag=lambda: True)
        check(rep.stopped_reason == "drain" and rep.launched == 0, "drain_flag 인자")
        # R12b wave 상한으로 발사를 멈춘 뒤 진행 중 호출을 기다리는 사이의 Drain 도 drain (WSU-2: wave 로 남으면 자동 이어가기)
        root = make_project("r12_drain_after_wave", 4, n=1)
        cks = jobs_of(root)
        ap, _ = approved(root)

        async def drain_after_wave():
            b = runner.MockBackend(delay_s=1.2, fail_plan={f"{cks[0]}_r0": {"delay": 0.2}})

            async def press():
                await asyncio.sleep(0.6)
                return runner.is_running(root), runner.request_drain(root)

            t = asyncio.create_task(press())
            r = await runner.run_calls(root, ap["to_run"], b, concurrency=2, wave_minutes=0.1 / 60)
            return r, await t

        rep, pressed = asyncio.run(drain_after_wave())
        check(pressed == (True, True) and rep.stopped_reason == "drain" and rep.launched == 2 and rep.done == 2
              and len(rep.remaining) == 2 and "drain" in rep.text(),
              f"wave 뒤 진행 중 Drain → stopped_reason drain(이벤트·자동 이어가기 없음) {pressed} {rep.stopped_reason}")


    # ═════════════════════════════════════════════════════════════════
    # R13 가드 / claim / 경합
    # ═════════════════════════════════════════════════════════════════
    def t_guards():
        section("R13 승인 가드 · 같은 호출 동시 실행 · 실행 중 보드 쓰기 경합")
        root = make_project("r13_guard", 2, n=1)
        p = plan(root)
        be = runner.MockBackend()
        rep = run(root, p["pending"], be)
        check(len(be.requests) == 0 and len(rep.skipped) == 2 and "approved_by" in rep.skipped[0]["why"],
              "approved_by 없는 과금 호출은 실행하지 않음")
        ap, _ = approved(root)

        async def twice():
            b = runner.MockBackend(delay_s=0.1)
            r1, r2 = await asyncio.gather(runner.run_calls(root, ap["to_run"], b), runner.run_calls(root, ap["to_run"], b))
            return b, r1, r2

        b, r1, r2 = asyncio.run(twice())
        check(len(b.requests) == 2 and r1.launched + r2.launched == 2 and len(r1.skipped) + len(r2.skipped) == 2,
              f"같은 호출 두 실행 → 한 번만 과금 ({len(b.requests)})")
        rep = run(root, ap["to_run"], be)
        check(len(be.requests) == 0 and len(rep.skipped) == 2 and "원장 상태" in rep.skipped[0]["why"],
              "옛 계획으로 다시 실행 → 원장 상태가 달라 건너뜀")
        # 실행 중 보드 쓰기(다른 스레드 update_manifest) — 잃은 갱신 0
        root = make_project("r13_race", 6, n=2)
        ap, _ = approved(root)
        stop = threading.Event()
        wrote = []

        def board():
            i = 0
            while not stop.is_set() and i < 200:
                store.update_manifest(root, lambda m, i=i: m["rejects"].update({f"x{i}": True}))
                wrote.append(i)
                i += 1

        th = threading.Thread(target=board)
        th.start()
        try:
            rep = run(root, ap["to_run"], runner.MockBackend(delay_s=0.05), concurrency=3)
        finally:
            stop.set()
            th.join()
        m = store.load_manifest(root)
        check(rep.done == 6 and all(m["rejects"].get(f"x{i}") for i in wrote)
              and len([c for c in m["calls"] if c["status"] == "done"]) == 6
              and len([c for c in m["candidates"] if c["origin"] == "run"]) == 12,
              f"보드 쓰기 {len(wrote)}회와 경합해도 calls 6·후보 12·rejects 전부 보존")
        note(f"R13 실행 중 보드 쓰기 {len(wrote)}회 경합, 잃은 갱신 0")


    # ═════════════════════════════════════════════════════════════════
    # R14 reconcile / R15 다중 타깃·옛 셀 / R16 mock 활성
    # ═════════════════════════════════════════════════════════════════
    def t_reconcile_misc():
        section("R14 reconcile_ledger · R15 다중 타깃 / 옛 셀 · R16 mock 활성")
        root = make_project("r14_reconcile", 2, n=2)
        ap, _ = approved(root)
        run(root, ap["to_run"], runner.MockBackend())
        m_full = store.load_manifest(root)

        def wipe(m):  # 원장 저장 뒤 매니페스트 반영 전에 죽었다고 가정
            m["calls"] = []
            m["candidates"] = []

        set_manifest(root, wipe)
        res = runner.reconcile_ledger(root)
        m = store.load_manifest(root)
        check(res["calls"] == 2 and len(res["candidates"]) == 4, f"재조정: calls 2 · 후보 4 {res}")
        check(sorted(c["key"] for c in m["candidates"]) == sorted(c["key"] for c in m_full["candidates"])
              and [{k: v for k, v in c.items()} for c in sorted(m["calls"], key=lambda c: c["call_id"])]
              == sorted(m_full["calls"], key=lambda c: c["call_id"]), "재조정 결과 = 원래 실행 결과")
        rev = m["rev"]
        res2 = runner.reconcile_ledger(root)
        check(res2["calls"] == 0 and res2["candidates"] == [] and store.load_manifest(root)["rev"] == rev, "재조정 멱등(rev 불변)")
        set_manifest(root, lambda m: m["candidates"][0].update(picked=True))
        runner.reconcile_ledger(root)
        check(store.load_manifest(root)["candidates"][0]["picked"] is True, "재조정이 기존 후보(picked)를 건드리지 않음")
        # 실행 중 재조정(보드·다른 노드): 진행 중 호출을 orphaned 로 바꾸지 않음
        root_a = make_project("r14_reconcile_active", 3, n=1)
        ca = jobs_of(root_a)
        apa, _ = approved(root_a)
        mid = {}

        def cb(done, total, text):
            if done == 1:
                runner.reconcile_ledger(root_a)
                mid.update({c["call_id"]: c["status"] for c in store.load_manifest(root_a)["calls"]})

        ra = run(root_a, apa["to_run"], runner.MockBackend(fail_plan={f"{ca[0]}_r0": {"delay": 0.02}}, delay_s=0.3),
                 concurrency=3, progress_cb=cb)
        st = {c["call_id"]: c["status"] for c in store.load_manifest(root_a)["calls"]}
        check(mid.get(f"{ca[1]}_r0") == "inflight" and mid.get(f"{ca[2]}_r0") == "inflight" and ra.done == 3
              and set(st.values()) == {"done"}, f"실행 중 재조정: 진행 중은 inflight 유지 {mid} → 끝 {st}")
        # 실행 시작 시 자동 재조정
        set_manifest(root, wipe)
        rep = run(root, [], runner.MockBackend())
        check(len(rep.new_candidates) == 4 and len(store.load_manifest(root)["calls"]) == 2, "run_calls 시작 시 재조정")
        # R15 다중 타깃
        root = make_project("r15_multi", 2, n=2, multi=True)
        ap, _ = approved(root)
        run(root, ap["to_run"], runner.MockBackend())
        m = store.load_manifest(root)
        ck0 = m["jobs"][0]["cell_key"]
        keys0 = sorted(c["key"] for c in m["candidates"] if c["crop_id"] == m["crops"][0]["id"])
        check(keys0 == sorted(f"r_{ck0[:12]}_0_{s}_{t}" for s in range(2) for t in "AB"),
              f"다중 타깃(A,B): slot × 타깃 후보, key 에 _<tid> {keys0}")
        c = next(c for c in m["candidates"] if c["key"] == f"r_{ck0[:12]}_0_0_B")
        check(c["tid"] == "B" and c["file"] == f"cands/{ck0}/r0_0.png" and c["origin_info"]["tids"] == ["A", "B"],
              "같은 파일을 타깃마다 후보로")
        # 옛 셀(jobs 에서 사라짐)의 recoverable 은 계속 복구 대상
        root = make_project("r15_stale", 1, n=1)
        ck = jobs_of(root)[0]
        ap, _ = approved(root)
        be = runner.MockBackend(fail_plan={"*": {"download_fail": 1}})
        run(root, ap["to_run"], be)
        set_manifest(root, lambda m: m.update(jobs=[]))
        p = plan(root)
        check([(e["call_id"], e["kind"], e["stale"]) for e in p["pending"]] == [(f"{ck}_r0", "recover", True)],
              "jobs 에 없는 옛 셀도 다운로드 복구 대상")
        rep = run(root, sa(p)["to_run"], be)
        check(rep.recovered == 1 and len(be.requests) == 1, "옛 셀 복구(재과금 0)")
        # 새 셀 사유(이전 셀 대비 변경)
        root = make_project("r15_reason", 1, n=1)
        ap, _ = approved(root)
        run(root, ap["to_run"], runner.MockBackend())

        def change(m):
            j = m["jobs"][0]
            j["prompt"] += " (edited)"
            j["cell_key"] = store.cell_key(MODEL, "max", j["size"], "opaque", j["prompt"], [i["sha"] for i in j["inputs"]],
                                           "dp-prompt/1", "V1_standard")

        set_manifest(root, change)
        e = plan(root)["pending"][0]
        check("프롬프트" in e["reason"] and "크기" not in e["reason"], f"새 셀 사유 = 바뀐 항목 {e['reason']}")
        # R16 mock 활성 조건
        base = OUT / "r16_mock"
        shutil.rmtree(base, ignore_errors=True)
        root = store.project_root(str(base), "proj")
        os.makedirs(root)
        old = os.environ.pop(runner.MOCK_ENV, None)
        try:
            check(not runner.mock_enabled(root) and isinstance(runner.make_backend(root, {}), runner.ComfyOrgBackend),
                  "기본 = ComfyOrgBackend")
            os.environ[runner.MOCK_ENV] = "1"
            check(runner.mock_enabled(root) and isinstance(runner.make_backend(root, {}), runner.MockBackend), "환경변수 → mock")
            os.environ.pop(runner.MOCK_ENV)
            flag = os.path.join(os.path.dirname(root), runner.MOCK_FILE)
            open(flag, "w").close()
            be = runner.make_backend(root, {})
            check(runner.mock_enabled(root) and isinstance(be, runner.MockBackend) and be.delay_s == 1.5, "_MOCK_API 빈 파일 → mock")
            with open(flag, "w", encoding="utf-8") as f:
                json.dump({"delay_s": 0.2, "response": "b64", "fail_plan": {"*": {"error": 500, "bogus": 1}}}, f)
            be = runner.make_backend(root, {})
            check(be.delay_s == 0.2 and be.response == "b64" and be.fail_plan["*"].error == 500, "_MOCK_API JSON 옵션")
        finally:
            os.environ.pop(runner.MOCK_ENV, None)
            if old is not None:
                os.environ[runner.MOCK_ENV] = old
        check("comfy_api_nodes" not in sys.modules, "여기까지 comfy 를 import 하지 않음")


    # ═════════════════════════════════════════════════════════════════
    # C. comfy (CPU) — HiddenHolder, 내장 노드 동등성, 크레딧, 오류 매핑, 인터럽트 형식
    # ═════════════════════════════════════════════════════════════════
    def tiny_png_b64() -> str:
        buf = io.BytesIO()
        Image.fromarray(synth(999, 64, 48)).save(buf, "PNG")
        return base64.b64encode(buf.getvalue()).decode("ascii")


    def capture(store_list, response):
        async def fake_sync_op(cls, endpoint, **kw):
            files = []
            for name, (fname, buf, ctype) in kw.get("files") or []:
                data = buf.getvalue()
                with Image.open(io.BytesIO(data)) as im:
                    im.load()
                    files.append({"field": name, "filename": fname, "ctype": ctype, "size": im.size, "mode": im.mode,
                                  "pixels": hashlib.sha256(np.asarray(im).tobytes()).hexdigest()})
            store_list.append({"cls": cls, "path": endpoint.path, "method": endpoint.method,
                               "data": kw["data"].model_dump(exclude_none=True), "files": files,
                               "content_type": kw.get("content_type"), "asset_urls": kw.get("asset_urls"),
                               "response_model": kw.get("response_model"), "extra": sorted(kw)})
            return response(cls, kw)
        return fake_sync_op


    def t_comfy():
        section("C0 comfy import (CPU)")
        sys.path.insert(0, str(COMFY))
        t0 = time.time()
        import comfy.options
        comfy.options.enable_args_parsing(False)
        import comfy.cli_args
        comfy.cli_args.args.cpu = True
        import torch
        import comfy.model_management as mm
        from comfy_api.latest._io import HiddenHolder
        from comfy_api_nodes import nodes_openai
        from comfy_api_nodes.apis.openai import Datum2, OpenAIImageGenerationResponse
        from comfy_api_nodes.util import client
        from comfy_api_nodes.util.common_exceptions import LocalNetworkError, ProcessingInterrupted
        note(f"C0 comfy_api_nodes import {time.time() - t0:.1f}s (device {mm.get_torch_device()})")

        section("C1 HiddenHolder 필드 / 호출별 클래스")
        be = runner.ComfyOrgBackend({"auth_token": FAKE_TOKEN, "api_key": None, "usage_source": "comfyui-frontend",
                                     "unique_id": "42"})
        core = set(vars(HiddenHolder.from_dict({})))
        ours = set(vars(be.holder))
        check(core == ours, f"holder 필드 = HiddenHolder 필드 {sorted(core ^ ours)}")
        check(be.holder.something_else is None and be.holder.auth_token_comfy_org == FAKE_TOKEN
              and be.holder.comfy_usage_source == "comfyui-frontend" and be.holder.unique_id == "42", "holder 값·없는 속성 None")
        from comfy_api_nodes.util import _helpers
        k1, k2 = be.call_class(), be.call_class()
        check(k1 is not k2 and k1.hidden is be.holder and k1.__name__.startswith("BMKDPCall_"), "호출마다 새 클래스")
        check(_helpers.get_auth_header(k1) == {"Authorization": f"Bearer {FAKE_TOKEN}"} and _helpers.get_node_id(k1) == "42"
              and _helpers.get_usage_source(k1) == "comfyui-frontend", "comfy_api_nodes 헬퍼가 holder 를 그대로 읽음")
        be.check()
        check(be.api.sync_op is client.sync_op and be.api.credits_used is client._get_remembered_credits_used
              and be.api.friendly_http_message is client._friendly_http_message, "실제 sync_op / 크레딧 / 오류 문구 함수에 묶임")
        orig_credits = client._get_remembered_credits_used
        del client._get_remembered_credits_used  # 이 블록 안에서만: 업데이트로 비공개 함수 이름이 바뀐 경우
        try:
            b5 = runner.ComfyOrgBackend({"auth_token": FAKE_TOKEN, "unique_id": "1"})
            b5.check()
            check(b5.api.credits_used(b5.call_class()) is None and b5.api.sync_op is client.sync_op,
                  "API-5: 크레딧 조회 함수가 없어도 check 통과(크레딧 None) — 유료 Run 을 막지 않음")
        finally:
            client._get_remembered_credits_used = orig_credits
        check(client._get_remembered_credits_used is orig_credits, "크레딧 함수 복원")

        section("C2 내장 OpenAIGPTImageNodeV2 와 sync_op 인자 동등성 (1장 · 3장 · >2048²)")
        cases = [("one", [(512, 768)], (1024, 1536)), ("three", [(512, 768), (300, 300), (1000, 700)], (2048, 2048)),
                 ("big", [(3000, 2600)], (1536, 1024)), ("big3", [(2600, 3000), (128, 128), (2100, 2100)], (1024, 1024))]
        for name, sizes, (W, H) in cases:
            root = make_project(f"c2_{name}", 1, n=2, size=(W, H), input_sizes=sizes)
            job = store.load_manifest(root)["jobs"][0]
            paths = [store.resolve_path(root, i["file"]) for i in job["inputs"]]
            resp = lambda cls, kw: OpenAIImageGenerationResponse(data=[Datum2(b64_json=tiny_png_b64())] * kw["data"].n)
            got_core, got_ours = [], []
            tensors = {}
            for i, p in enumerate(paths):
                with Image.open(p) as im:
                    tensors[f"image_{i + 1}"] = torch.from_numpy(np.array(im.convert("RGB")).astype(np.float32) / 255.0)[None]
            model = {"model": MODEL, "size": "Custom", "custom_width": W, "custom_height": H, "background": "opaque",
                     "quality": "max", "images": tensors, "mask": None}
            orig = nodes_openai.sync_op
            nodes_openai.sync_op = capture(got_core, resp)
            try:
                out = asyncio.run(nodes_openai.OpenAIGPTImageNodeV2.execute(prompt=job["prompt"], model=model, n=2, seed=0))
            finally:
                nodes_openai.sync_op = orig
            check(nodes_openai.sync_op is client.sync_op, "nodes_openai.sync_op 복원")
            b = runner.ComfyOrgBackend({"auth_token": FAKE_TOKEN, "unique_id": "1"})
            b.check()
            b.api.sync_op = capture(got_ours, resp)  # 인스턴스 속성만 — 모듈 전역은 그대로
            r = asyncio.run(b.request(job, paths, "x"))
            check(client.sync_op is not b.api.sync_op and out is not None and len(r["data"]) == 2, f"{name}: 양쪽 모두 가짜 응답까지 진행")
            c, o = got_core[0], got_ours[0]
            check(c["path"] == o["path"] == "/proxy/openai/images/edits" and c["method"] == o["method"] == "POST",
                  f"{name}: endpoint")
            check(c["data"] == o["data"], f"{name}: data 필드 {c['data']} vs {o['data']}")
            check(o["data"] == {"model": MODEL, "prompt": job["prompt"], "quality": "max", "background": "opaque", "n": 2,
                                "size": f"{W}x{H}", "moderation": "low"}, f"{name}: data 값")
            strip = lambda fs: [{k: v for k, v in f.items()} for f in fs]
            check([(f["field"], f["filename"], f["ctype"]) for f in c["files"]]
                  == [(f["field"], f["filename"], f["ctype"]) for f in o["files"]], f"{name}: files 이름·순서·형식")
            check([f["size"] for f in c["files"]] == [f["size"] for f in o["files"]], f"{name}: 이미지 크기 {[f['size'] for f in o['files']]}")
            check(strip(c["files"]) == strip(o["files"]), f"{name}: 업로드 픽셀까지 동일")
            check(c["content_type"] == o["content_type"] == "multipart/form-data" and c["asset_urls"] is o["asset_urls"] is True
                  and c["response_model"] is o["response_model"] is OpenAIImageGenerationResponse, f"{name}: content_type·asset_urls·응답 모델")
            check(o["extra"] == sorted(set(c["extra"]) | {"is_rate_limited"}), f"{name}: 추가 인자는 is_rate_limited(관찰용)뿐 {o['extra']}")
            field = "image" if len(sizes) == 1 else "image[]"
            check(all(f["field"] == field for f in o["files"]), f"{name}: 필드 이름 {field}")
            for (w0, h0), f in zip(sizes, o["files"]):
                if w0 * h0 > runner.MAX_INPUT_PIXELS:
                    check(f["size"][0] * f["size"][1] <= runner.MAX_INPUT_PIXELS and f["size"][0] < w0, f"{name}: >2048² 축소 {f['size']}")
                else:
                    check(tuple(f["size"]) == (w0, h0), f"{name}: 작은 입력은 그대로 {f['size']}")
            note(f"C2 {name}: 입력 {sizes} → 업로드 {[tuple(f['size']) for f in o['files']]} (내장과 동일)")

        section("C3 크레딧: 호출별 클래스로 분리 기록")
        root = make_project("c3_credits", 2, n=1, size=(1024, 1024))
        m = store.load_manifest(root)
        jobs = m["jobs"]
        paths = [[store.resolve_path(root, i["file"]) for i in j["inputs"]] for j in jobs]
        b = runner.ComfyOrgBackend({"auth_token": FAKE_TOKEN, "unique_id": "5"})
        b.check()
        seen_cls = []

        async def credit_sync_op(cls, endpoint, **kw):
            seen_cls.append(cls)
            # 값은 요청(프롬프트)으로 정한다 — 입력 인코딩(to_thread)이 끝나는 순서는 부하에 따라 바뀐다
            val = "12.5" if kw["data"].prompt == jobs[0]["prompt"] else "7"
            await asyncio.sleep(0.05 if val == "12.5" else 0.01)
            client._maybe_remember_credits_used(cls, val)
            await asyncio.sleep(0.05)
            return OpenAIImageGenerationResponse(data=[Datum2(url="https://example.invalid/a.png")])

        b.api.sync_op = credit_sync_op

        async def two():
            return await asyncio.gather(b.request(jobs[0], paths[0], "a"), b.request(jobs[1], paths[1], "b"))

        r1, r2 = asyncio.run(two())
        check(r1["credits"] == 12.5 and r2["credits"] == 7.0 and seen_cls[0] is not seen_cls[1], f"동시 호출 크레딧 분리 {r1['credits']} {r2['credits']}")
        check(b.api.credits_used(b.call_class()) is None, "새 클래스는 크레딧 기억 없음")

        section("C4 오류 매핑 (is_rate_limited 관찰 / 문구 / 네트워크)")

        def failing(status, exc):
            async def f(cls, endpoint, **kw):
                if status is not None:
                    kw["is_rate_limited"](status, {})
                raise exc
            return f

        job, pth = jobs[0], paths[0]
        cases = [(401, Exception("Unauthorized: Please login first to use this node."), 401, False),
                 (403, Exception("API Error: forbidden"), 403, False),
                 (500, Exception("API Error: upstream exploded (Type: server_error)"), 500, False),
                 (None, Exception("Rate Limit Exceeded: The server returned 429 after all retry attempts."), 429, False),
                 (None, LocalNetworkError("Unable to connect"), None, True),
                 (200, Exception("Response validation failed for X: boom"), None, True)]
        for status, exc, want, billed in cases:
            b.api.sync_op = failing(status, exc)
            try:
                asyncio.run(b.request(job, pth, "e"))
                check(False, f"오류 매핑 {want}: 예외가 나지 않음")
            except runner.CallError as e:
                check(e.status == want and e.maybe_billed is billed, f"오류 매핑 {exc!s:.40} → status {e.status} billed {e.maybe_billed}")
        nob = runner.ComfyOrgBackend({"unique_id": "1"})
        nob.check()
        called = []
        nob.api.sync_op = capture(called, lambda cls, kw: None)
        try:
            asyncio.run(nob.request(job, pth, "z"))
            check(False, "로그인 없음: 예외가 나지 않음")
        except runner.CallError as e:
            check(e.status == 401 and not called, "로그인 없음 → 호출 전에 401(CallError)")
        bad = dict(job, size=[100, 100])
        try:
            asyncio.run(b.request(bad, pth, "z"))
            check(False, "받지 않는 크기: 예외가 나지 않음")
        except runner.CallError as e:
            check(e.status == 400 and e.retryable is False, "받지 않는 크기 → 호출 전 400")
        # API-1: comfy-api 의 평평한 오류 봉투 {"error": 코드, "message": 문구} — 문구에 접두가 없다
        def failing_body(status, body):
            async def f(cls, endpoint, **kw):
                kw["is_rate_limited"](status, body)
                raise Exception(client._friendly_http_message(status, body))
            return f

        for status, body, want_retry in ((403, {"error": "forbidden", "message": "Account is not allowed to use this model"}, False),
                                         (400, {"error": "bad_request", "message": "Invalid size"}, False),
                                         (403, {"error": {"message": "nope", "type": "forbidden"}}, False),
                                         (503, {"error": "comfy_cloud_provider_disabled", "message": "disabled"}, True)):
            b.api.sync_op = failing_body(status, body)
            try:
                asyncio.run(b.request(job, pth, "e"))
                check(False, f"평평한 봉투 {status}: 예외가 나지 않음")
            except runner.CallError as e:
                check(e.status == status and e.maybe_billed is False and e.retryable is want_retry,
                      f"API-1: {str(e)[:30]!r} → status {e.status} retryable {e.retryable} billed {e.maybe_billed}")
        root = make_project("c4_flat403", 3, n=1, size=(1024, 1024))
        ap, _ = approved(root)
        b4 = runner.ComfyOrgBackend({"auth_token": FAKE_TOKEN, "unique_id": "1"})
        b4.check()
        sent = []

        async def flat403(cls, endpoint, **kw):
            sent.append(cls)
            body = {"error": "forbidden", "message": "Account is not allowed to use this model"}
            kw["is_rate_limited"](403, body)
            raise Exception(client._friendly_http_message(403, body))

        b4.api.sync_op = flat403
        rep = run(root, ap["to_run"], b4, concurrency=1)
        check(rep.stopped_reason == "auth" and len(sent) == 1 and rep.failed == 0 and len(plan(root)["pending"]) == 3,
              f"API-1: 평평한 403 → auth 중지(1 호출만, 나머지 대기) {rep.text().splitlines()[0]}")
        # S6: 입력 인코딩 실패(요청 안 보냄) → phase encode, 과금 없음, 재시도 무의미
        root = make_project("c4_encode", 1, n=1, size=(1024, 1024))
        ap, _ = approved(root)
        junk = Path(root) / store.load_manifest(root)["jobs"][0]["inputs"][0]["file"]
        junk.write_bytes(b"\x89PNG\r\n\x1a\n" + b"junk" * 40)
        called = []
        b.api.sync_op = capture(called, lambda cls, kw: None)
        rep = run(root, ap["to_run"], b)
        ck = jobs_of(root)[0]
        f = json.loads(Path(root, "cands", ck, "r0.failed.json").read_text(encoding="utf-8"))
        check(not called and rep.failed == 1 and f["phase"] == "encode" and f["retryable"] is False and f["maybe_billed"] is False
              and "요청 안 보냄" in f["error"] and "과금됐을 수 있음" not in rep.text(),
              f"S6: 인코딩 실패 → sync_op 안 부름, failed(encode, 과금 없음) {f['phase']} {f['maybe_billed']}")
        # run_calls 경로: 401 → auth 중지
        root = make_project("c4_auth", 2, n=1, size=(1024, 1024))
        ap, _ = approved(root)
        b2 = runner.ComfyOrgBackend({"auth_token": FAKE_TOKEN, "unique_id": "1"})
        b2.check()
        b2.api.sync_op = failing(401, Exception("Unauthorized: Please login first to use this node."))
        rep = run(root, ap["to_run"], b2, concurrency=1)
        check(rep.stopped_reason == "auth" and len(plan(root)["pending"]) == 2, "ComfyOrg 401 → auth 중지, pending 유지")

        section("C5 다운로드 / run_calls 종단 (가짜 sync_op + 가짜 다운로드)")
        root = make_project("c5_e2e", 2, n=2, size=(1024, 1024))
        ap, _ = approved(root)
        b3 = runner.ComfyOrgBackend({"auth_token": FAKE_TOKEN, "unique_id": "9"})
        b3.check()
        blobs = {}

        async def url_sync_op(cls, endpoint, **kw):
            client._maybe_remember_credits_used(cls, "3.25")
            data = []
            for i in range(kw["data"].n):
                u = f"https://example.invalid/{cls.__name__}/{i}.png"
                buf = io.BytesIO()
                Image.fromarray(synth(len(blobs) + 7, 64, 64)).save(buf, "PNG", compress_level=9)
                blobs[u] = buf.getvalue()
                data.append(Datum2(url=u))
            return OpenAIImageGenerationResponse(data=data)

        dl_cls = []

        async def fake_download(url, dest, *, cls=None, **kw):
            dl_cls.append(cls)
            if "missing" in url:
                raise Exception("Failed to download (HTTP 404).")
            dest.write(blobs[url])

        b3.api.sync_op = url_sync_op
        b3.api.download_url_to_bytesio = fake_download
        rep = run(root, ap["to_run"], b3)
        ok = rep.done == 2 and rep.credits_total == 6.5
        for ck in jobs_of(root):
            resp = json.load(open(os.path.join(root, "cands", ck, "r0.resp.json"), encoding="utf-8"))
            ok &= resp["credits"] == 3.25 and all(
                sha_file(os.path.join(root, "cands", ck, f"r0_{d['slot']}.png")) == hashlib.sha256(blobs[d["url"]]).hexdigest()
                for d in resp["data"])
        check(ok, f"ComfyOrg 경로: 크레딧 기록 · 다운로드 바이트 그대로 {rep.text()}")
        check(all(c is not None and c.__name__.startswith("BMKDPCall_") for c in dl_cls), "다운로드는 그 호출의 클래스로(cls=call_cls)")
        for url in ("https://example.invalid/missing.png", "/proxy/missing.png"):
            try:
                asyncio.run(b3.download(url))
                check(False, f"다운로드 404 {url}: 예외가 나지 않음")
            except runner.CallError as e:
                check(e.phase == "download" and e.status == 404 and e.retryable is False, f"다운로드 404 = 영구 {url}: retryable {e.retryable}")

        async def raise403(url, dest, *, cls=None, **kw):
            raise Exception("Failed to download (HTTP 403).")

        b3.api.download_url_to_bytesio = raise403
        for url, want_retry in (("https://signed.example.invalid/x.png?sig=1", False), ("/proxy/openai/x.png", True)):
            try:
                asyncio.run(b3.download(url))
                check(False, f"403 다운로드 {url}: 예외가 나지 않음")
            except runner.CallError as e:
                check(e.retryable is want_retry, f"403 다운로드: 서명 URL 만료는 영구, 상대(/proxy) 는 다시(토큰) {url}")

        section("C6 comfy 인터럽트 예외 형식")
        root = make_project("c6_interrupt", 3, n=1)
        ap, _ = approved(root)
        be, rep, exc = interrupted_run(root, ap["to_run"], {"delay_s": 1.0}, trigger_at=0.2, concurrency=1)
        check(isinstance(exc, ProcessingInterrupted) and rep is not None and rep.orphaned, f"진행 중 취소 → ProcessingInterrupted 그대로 raise ({type(exc).__name__})")
        root = make_project("c6_flag", 3, n=1)
        ap, _ = approved(root)

        async def flag_run():
            try:
                await runner.run_calls(root, ap["to_run"], runner.MockBackend(interrupt_check=lambda: False),
                                       interrupt_check=lambda: True)
            except BaseException as e:
                return e

        e = asyncio.run(flag_run())
        check(isinstance(e, mm.InterruptProcessingException) and getattr(e, "bmk_dp_report", None) is not None
              and e.bmk_dp_report.launched == 0 and len(e.bmk_dp_report.remaining) == 3,
              f"발사 전 플래그 → InterruptProcessingException ({type(e).__name__})")
        root = make_project("c6_progress", 3, n=1)
        ap, _ = approved(root)

        def cb(done, total, text):
            mm.throw_exception_if_processing_interrupted()

        async def progress_run():
            mm.interrupt_current_processing(False)
            be = runner.MockBackend(delay_s=0.05, interrupt_check=lambda: False)

            def cb2(done, total, text):
                mm.interrupt_current_processing(True)  # 사용자가 Cancel
                cb(done, total, text)  # ProgressBar 훅처럼 플래그를 지우며 예외

            try:
                await runner.run_calls(root, ap["to_run"], be, concurrency=1, progress_cb=cb2, interrupt_check=lambda: False)
            except BaseException as e:
                return e, be

        e, be = asyncio.run(progress_run())
        check(isinstance(e, mm.InterruptProcessingException) and len(be.requests) == 1 and mm.processing_interrupted(),
              "progress_cb 의 InterruptProcessingException → 중지 + comfy 플래그 다시 세움(진행 중 sync_op 도 취소되게)")
        mm.interrupt_current_processing(False)
        check(len(plan(root)["done"]) == 1 and len(plan(root)["pending"]) == 2, "인터럽트 전 도착분은 저장")


    # ═════════════════════════════════════════════════════════════════
    # 리뷰 수정 회귀 (S1·S2·CR-1·CR-3·API-2·API-3)
    # ═════════════════════════════════════════════════════════════════
    def ledger_bytes(root, ck):
        return {fn: Path(root, "cands", ck, fn).read_bytes() for fn in ledger_files(root, ck)}


    def t_board_budget():
        section("R3b 재시도한 보드 재굴림의 몫은 돌아오지 않음 (S2 / CR-1)")
        for mode in ("retry_failed", "retry_orphans"):
            root = make_project(f"r3b_{mode}", 1, n=1)
            ck = jobs_of(root)[0]
            ap, _ = approved(root)
            run(root, ap["to_run"], runner.MockBackend())
            set_manifest(root, lambda m: m["rerolls"].update({ck: 1}))
            to_run = sa(plan(root))["to_run"]
            check([(e["rep"], e["approved_by"]) for e in to_run] == [(1, "board")], f"{mode}: 재굴림 r1 = 보드 사전 승인")
            if mode == "retry_failed":
                run(root, to_run, runner.MockBackend(fail_plan={"*": {"error": 500}}))
            else:
                runner._begin_call(root, to_run[0], runner.BACKEND_MOCK)  # 호출 중 크래시: req + inflight 만
            ap2, p2 = approved(root, **{mode: True})
            kind = "retry_failed" if mode == "retry_failed" else "retry_orphan"
            check([(e["rep"], e["kind"], e["preapproved"]) for e in p2["pending"]] == [(1, kind, False)],
                  f"{mode}: 보드 호출의 재시도는 승인 필요")
            run(root, ap2["to_run"], runner.MockBackend())
            req1 = json.loads(Path(root, "cands", ck, "r1.req.json").read_text(encoding="utf-8"))
            check(req1["approved_by"].startswith("hash:") and req1["history"][-1]["req"]["approved_by"] == "board",
                  f"{mode}: 재시도 req 는 hash 승인, 보드 승인은 history 에 {req1['approved_by']}")
            set_manifest(root, lambda m: m["jobs"][0].update(reps=2))  # calls_per_cell 1 → 2
            p3 = plan(root)
            be3 = runner.MockBackend()
            rep3 = run(root, sa(p3)["to_run"], be3)
            check([(e["rep"], e["preapproved"]) for e in p3["pending"]] == [(2, False)] and not be3.requests
                  and rep3.launched == 0, f"{mode}: reps 1→2 의 r2 는 승인 필요(보드 몫 1 은 이미 씀) — approve 없이 0 호출")


    def t_auth_retry():
        section("R7b 재시도(retry_failed / retry_orphans)의 401 → 원장을 시도 전으로 되돌림 (API-2)")
        root = make_project("r7b_auth_retry", 3, n=1)
        cks = jobs_of(root)
        ap, _ = approved(root)
        by_ck = {e["cell_key"]: e for e in ap["to_run"]}
        runner._begin_call(root, by_ck[cks[0]], runner.BACKEND_MOCK)  # c0: orphaned(과금됐을 수 있음)
        run(root, [by_ck[cks[1]]], runner.MockBackend(fail_plan={"*": {"error": "network"}}))  # c1: failed, maybe_billed
        run(root, [by_ck[cks[2]]], runner.MockBackend())
        set_manifest(root, lambda m: m["rerolls"].update({cks[2]: 1}))
        runner._begin_call(root, sa(plan(root))["to_run"][0], runner.BACKEND_MOCK)  # c2 r1: 보드 재굴림 orphan
        f1 = json.loads(Path(root, "cands", cks[1], "r0.failed.json").read_text(encoding="utf-8"))
        check(f1["maybe_billed"] is True, "준비: c1 실패는 과금됐을 수 있음")
        before = {ck: ledger_bytes(root, ck) for ck in cks}
        ap2, p2 = approved(root, retry_failed=True, retry_orphans=True)
        check(sorted(e["kind"] for e in p2["pending"]) == ["retry_failed", "retry_orphan", "retry_orphan"], "재시도 3건 계획")
        be = runner.MockBackend(delay_s=0.05, fail_plan={"*": {"error": 401}})
        rep = run(root, ap2["to_run"], be, concurrency=3)
        check(rep.stopped_reason == "auth" and len(be.requests) == 3 and rep.launched == 0 and len(rep.remaining) == 3,
              f"401 → auth 중지 {rep.text()}")
        check({ck: ledger_bytes(root, ck) for ck in cks} == before, "원장 파일 바이트가 시도 전과 같음(history·시작 시각 보존)")
        p3 = plan(root)
        check(len(p3["orphaned"]) == 2 and len(p3["failed"]) == 1 and p3["pending"] == [], "다시 계획: orphaned 2 · failed 1, 대기 0")
        st = {c["call_id"]: c["status"] for c in store.load_manifest(root)["calls"]}
        check(st.get(f"{cks[0]}_r0") == "orphaned" and st.get(f"{cks[1]}_r0") == "failed" and st.get(f"{cks[2]}_r1") == "orphaned",
              f"calls 요약도 시도 전 상태 {st}")
        be2 = runner.MockBackend()
        rep2 = run(root, sa(p3)["to_run"], be2)
        check(not be2.requests and rep2.launched == 0, "보통 Run(재시도 끔·approve 없음) → 0 호출(보드 orphan 도 사전 승인으로 다시 과금 안 함)")


    def t_b64_copy():
        section("R9g b64 응답: resp.json 전 사본 → 저장 전 크래시·디스크 오류에도 복구 (CR-3)")

        class _Crash(BaseException):
            pass

        orig = runner._save_slot
        root = make_project("r9g_b64_crash", 1, n=2)
        ck = jobs_of(root)[0]
        ap, _ = approved(root)
        be = runner.MockBackend(response="b64")

        def crash(*a):
            raise _Crash()

        runner._save_slot = crash  # 이 블록 안에서만(slot 저장 직전 프로세스가 죽은 것과 같은 원장)
        try:
            run(root, ap["to_run"], be)
        finally:
            runner._save_slot = orig
        fl = ledger_files(root, ck)
        resp = json.loads(Path(root, "cands", ck, "r0.resp.json").read_text(encoding="utf-8"))
        check(fl == ["r0.inflight.json", "r0.req.json", "r0.resp.json", "r0_0.b64.bin", "r0_1.b64.bin"]
              and [d.get("raw") for d in resp["data"]] == [True, True] and all("b64" not in d for d in resp["data"]),
              f"크래시 뒤 원장: resp.json(raw 표시, 본문 없음) + b64 사본 {fl}")
        p = plan(root)
        check([e["kind"] for e in p["pending"]] == ["recover"], "다음 계획: recover(과금 없음)")
        rep = run(root, sa(p)["to_run"], be)
        done = json.loads(Path(root, "cands", ck, "r0.done.json").read_text(encoding="utf-8"))
        check(rep.recovered == 1 and done["partial"] is False and len(done["slots"]) == 2 and len(be.requests) == 1
              and [sha_file(Path(root, "cands", ck, f"r0_{i}.png")) for i in range(2)] == be.served[f"{ck}_r0"]
              and not any(f.endswith(".b64.bin") for f in ledger_files(root, ck)),
              f"사본에서 복구: 2장 그대로, 재과금 0, 사본 삭제 {ledger_files(root, ck)}")
        root = make_project("r9g_b64_oserror", 1, n=2)
        ck = jobs_of(root)[0]
        ap, _ = approved(root)
        be = runner.MockBackend(response="b64")
        seen = {"n": 0}

        def flaky(*a):
            seen["n"] += 1
            if seen["n"] == 2:
                raise OSError(28, "No space left on device")
            return orig(*a)

        runner._save_slot = flaky
        try:
            rep = run(root, ap["to_run"], be)
        finally:
            runner._save_slot = orig
        f = json.loads(Path(root, "cands", ck, "r0.failed.json").read_text(encoding="utf-8"))
        check(rep.failed == 1 and f["phase"] == "download" and f["retryable"] is True and len(plan(root)["pending"]) == 1,
              "b64 slot 디스크 오류 → failed(retryable) — partial done 으로 확정하지 않음")
        rep = run(root, sa(plan(root))["to_run"], be)
        done = json.loads(Path(root, "cands", ck, "r0.done.json").read_text(encoding="utf-8"))
        check(rep.recovered == 1 and done["partial"] is False and len(done["slots"]) == 2 and len(be.requests) == 1
              and [sha_file(Path(root, "cands", ck, f"r0_{i}.png")) for i in range(2)] == be.served[f"{ck}_r0"],
              "다음 실행이 사본에서 남은 slot 저장(재과금 0)")
        root = make_project("r9g_b64_ok", 1, n=2)
        ck = jobs_of(root)[0]
        run(root, approved(root)[0]["to_run"], runner.MockBackend(response="b64"))
        check(ledger_files(root, ck) == ["r0.done.json", "r0.req.json", "r0.resp.json", "r0_0.png", "r0_1.png"],
              "정상 b64 실행은 사본을 남기지 않음")


    def t_recover_backend():
        section("R9h 다운로드 복구는 응답을 받은 백엔드로만 (S1)")
        root = make_project("r9h_recover_backend", 1, n=2)
        ck = jobs_of(root)[0]
        ap, _ = approved(root)
        paid = runner.MockBackend(fail_plan={"*": {"download_fail": 1}})
        paid.name = runner.BACKEND_COMFY_ORG  # 유료 백엔드 대역(같은 프로세스의 mock URL 이라 나중에 받을 수 있음)
        rep = run(root, ap["to_run"], paid)
        resp = json.loads(Path(root, "cands", ck, "r0.resp.json").read_text(encoding="utf-8"))
        check(rep.failed == 1 and resp["backend"] == runner.BACKEND_COMFY_ORG, "준비: comfy_org 응답 + 다운로드 실패(recoverable)")
        before = ledger_bytes(root, ck)
        mock = runner.MockBackend()
        rep = run(root, sa(plan(root))["to_run"], mock)
        check(len(rep.skipped) == 1 and "comfy_org" in rep.skipped[0]["why"] and rep.done == 0 and not mock.downloads
              and ledger_bytes(root, ck) == before and [e["kind"] for e in plan(root)["pending"]] == ["recover"],
              f"MOCK 으로는 건너뜀: 원장 그대로, 계속 recover {rep.skipped}")
        n_req = len(paid.requests)
        rep = run(root, sa(plan(root))["to_run"], paid)
        done = json.loads(Path(root, "cands", ck, "r0.done.json").read_text(encoding="utf-8"))
        check(rep.recovered == 1 and done["partial"] is False and len(done["slots"]) == 2 and len(paid.requests) == n_req,
              "같은 백엔드로 실행하면 다운로드만 다시(2장, 재과금 0)")


    def t_download_error():
        section("R9i 다운로드 오류 분류 (API-3)")
        for url, status, retry in (("https://signed.example.invalid/x.png?sig=1", 400, False),
                                   ("https://signed.example.invalid/x.png?sig=1", 403, False),
                                   ("https://a.example.invalid/x.png", 404, False), ("https://a.example.invalid/x.png", 408, True),
                                   ("https://a.example.invalid/x.png", 429, True), ("https://a.example.invalid/x.png", 503, True),
                                   ("/proxy/assets/x.png", 401, True), ("/proxy/assets/x.png", 403, True),
                                   ("/proxy/assets/x.png", 404, False), ("proxy/assets/x.png", 401, True),
                                   ("//cdn.example.invalid/x.png", 403, False)):
            e = runner._download_error(url, Exception(f"Failed to download (HTTP {status})."), ())
            check(e.status == status and e.retryable is retry and e.phase == "download" and not e.maybe_billed,
                  f"{url} HTTP {status} → retryable {retry}")


    OUT.mkdir(parents=True, exist_ok=True)
    t_import()
    t_plan()
    t_only()
    t_basic()
    t_rerolls_and_n()
    t_board_budget()
    t_concurrency_errors()
    t_auth_retry()
    t_interrupt_crash()
    t_b64_copy()
    t_recover_backend()
    t_download_error()
    t_limits()
    t_guards()
    t_reconcile_misc()
    if with_comfy:
        t_comfy()


# ═════════════════════════════════════════════════════════════════
# M2-B. Review Board 라우트·썸네일 (M2_SPEC §4) — 옛 tools/_dp_test_board.py
#   B0 import 의존성  B1 register_routes  B2 정적 파일  B3 root 검증  B4 state  B5 file  B6 pick  B7 reject
#   B8 reroll  B9 drain·run 레지스트리  B10 썸네일  B11 동시 요청  B12 board.js 정적 검사  B13 prebuild_thumbs
#   B14 핸들러 직접 호출  B15 실데이터(M1 회귀 프로젝트에서 크롭 5개 복사, 없으면 건너뜀)
#   aiohttp 테스트 서버는 127.0.0.1 임의 포트(8188 은 건드리지 않음).
# ═════════════════════════════════════════════════════════════════
def m2_board_unit():
    _PRE = set(sys.modules)
    import bmk_design_patch_board as board
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer, make_mocked_request

    OUT = OUT_ROOT / "board"
    BASE = OUT / "base"
    REG_SRC = REF / "_proto" / "m1_test_out" / "nodes" / "bmk_design_patch" / "m1_regression"
    REG_CROPS = ("002_388984", "016_cde70a", "003_aa1e8a", "030_d8449c", "010_22d16b")

    def png_bytes(arr) -> bytes:
        buf = io.BytesIO()
        Image.fromarray(arr).save(buf, "PNG")
        return buf.getvalue()


    def texture(h, w, seed):
        rng = np.random.default_rng(seed)
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        img = np.stack([128 + 90 * np.sin(xx / 23 + seed), 128 + 90 * np.cos(yy / 31), 110 + 60 * np.sin((xx + yy) / 47)], -1)
        img += rng.normal(0, 6, img.shape)
        return np.clip(img, 0, 255).astype(np.uint8)


    # ══════════════════════════════════════════════════════════════════════
    # 프로젝트 만들기
    # ══════════════════════════════════════════════════════════════════════
    XSS_NAME = '001_<img src=x onerror=alert(1)>"\'&'
    XSS_LAYER = XSS_NAME + " | A: red circle; B: blue square"


    def build_syn() -> str:
        root = store.project_root(str(BASE), "syn_xss")
        shutil.rmtree(root, ignore_errors=True)
        os.makedirs(root)
        m = store.new_manifest("syn_xss")
        base = texture(480, 640, 1)
        m["canvas"], m["work_rect"] = [640, 480], [0, 0, 640, 480]
        m["base"] = store.save_png_ca(root, "inputs", base)
        p = store.parse_crop_layer_name(XSS_LAYER)
        cid = store.crop_id_for(XSS_LAYER)
        rect = [100, 100, 300, 260]
        x0, y0, x1, y1 = rect
        src = np.dstack([base[y0:y1, x0:x1], np.full((y1 - y0, x1 - x0), 255, np.uint8)])
        m["crops"].append({
            "id": cid, "name": p["name"], "nnn": p["nnn"], "part": p["part"], "layer_name": XSS_LAYER, "rect": rect,
            "rect_source": "vector", "source": store.save_png_ca(root, "inputs", src), "source_kind": "base_crop",
            "alpha_frac": 0.0, "refs_auto": [], "warnings": [],
            "targets": [{"tid": t["tid"], "label": t["text"], "name_en": t["text"], "mode": "ref_correct", "refs": []}
                        for t in p["targets"]]})
        cands = []
        for i, (tid, color) in enumerate((("A", (230, 40, 40)), ("A", (40, 200, 40)), ("B", (40, 40, 230)))):
            big = np.asarray(Image.fromarray(base[y0:y1, x0:x1]).resize((400, 320), Image.LANCZOS)).copy()
            yy, xx = np.mgrid[0:320, 0:400]
            big[(yy - 150 - 10 * i) ** 2 + (xx - 180) ** 2 < 50 ** 2] = color
            rel = store.save_bytes_ca(root, "cands", png_bytes(big), "png", prefix="f_")
            cands.append({"key": Path(rel).stem, "crop_id": cid, "tid": tid, "file": rel, "src_size": [400, 320],
                          "origin": "folder", "origin_info": {"folder": "syn", "filename": f"c{i}.png"},
                          "quad": [float(v) for v in (x0, y0, x1, y0, x1, y1, x0, y1)], "picked": False, "z": None,
                          "hand_mask": None})
        patch = np.zeros((40, 40, 4), np.uint8)
        patch[..., 0], patch[..., 3] = 250, 255
        patch[:8, :, 3] = 0  # 부분 투명
        rel = store.save_bytes_ca(root, "cands", png_bytes(patch), "png", prefix="h_")
        cands.append({"key": Path(rel).stem, "crop_id": cid, "tid": "A", "file": rel, "src_size": [40, 40],
                      "origin": "psd_harvest", "origin_info": {"layer": "레이어 12"}, "quad": [140.0, 140.0, 180.0, 140.0,
                                                                                              180.0, 180.0, 140.0, 180.0],
                      "picked": False, "z": 3, "hand_mask": None})
        m["candidates"] = cands
        canvas_rel = store.save_png_ca(root, "inputs", src[..., :3])
        ref_rel = store.save_png_ca(root, "inputs", texture(300, 200, 7))
        for variant, tids, inputs in (("V1", "A,B", [("canvas", canvas_rel), ("ref", ref_rel)]),
                                      ("V2", "A", [("canvas", canvas_rel), ("ref", ref_rel)])):
            ck = store.cell_key("gpt-image-2.5-sunburst", "max", [2048, 1648], "opaque", f"prompt {variant}",
                                ["a", "b"], "dp-prompt/1", variant)
            m["jobs"].append({"cell_key": ck, "crop_id": cid, "tid": tids, "variant": variant,
                              "model": "gpt-image-2.5-sunburst", "quality": "max", "size": [2048, 1648], "n": 4,
                              "background": "opaque", "prompt": f"prompt {variant}",
                              "inputs": [{"role": r, "file": f, "sha": "x"} for r, f in inputs],
                              "template_version": "dp-prompt/1"})
        store.save_manifest(root, m)
        return root


    def _rels(obj, out: set):
        if isinstance(obj, dict):
            for v in obj.values():
                _rels(v, out)
        elif isinstance(obj, list):
            for v in obj:
                _rels(v, out)
        elif isinstance(obj, str) and re.match(r"^(inputs|cands|masks|derived)/[^/]", obj):
            out.add(obj)


    def build_reg() -> str | None:
        if not (REG_SRC / store.MANIFEST_NAME).is_file():
            return None
        root = store.project_root(str(BASE), "reg_copy")
        shutil.rmtree(root, ignore_errors=True)
        m = json.loads((REG_SRC / store.MANIFEST_NAME).read_text(encoding="utf-8"))
        m["project"] = "reg_copy"
        m["crops"] = [c for c in m["crops"] if c["id"] in REG_CROPS]
        m["candidates"] = [c for c in m["candidates"] if c["crop_id"] in REG_CROPS]
        keys = {c["key"] for c in m["candidates"]}
        m["analysis"] = {k: v for k, v in m["analysis"].items() if k in keys}
        m["picks"] = {k: v for k, v in m["picks"].items() if k.split("/")[0] in REG_CROPS}
        m["jobs"] = [j for j in m["jobs"] if j["crop_id"] in REG_CROPS]
        m["exports"] = []
        rels: set = set()
        _rels(m, rels)
        n = 0
        for rel in sorted(rels):
            s = REG_SRC / rel
            if s.is_file():
                d = Path(root) / rel
                d.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(s, d)
                n += 1
        store.write_json_atomic(os.path.join(root, store.MANIFEST_NAME), m)
        # mock run 후보 1장(030) + calls 요약 + 원장 파일
        job = next(j for j in m["jobs"] if j["crop_id"] == "030_d8449c")
        ck = job["cell_key"]
        c030 = next(c for c in m["candidates"] if c["crop_id"] == "030_d8449c")
        with Image.open(REG_SRC / c030["file"]) as im:
            arr = np.asarray(im.convert("RGB"))[..., ::-1].copy()  # 채널 뒤집기 = 눈에 띄는 mock 변형
        ledger = Path(root) / "cands" / ck
        ledger.mkdir(parents=True, exist_ok=True)
        Image.fromarray(arr).save(ledger / "r0_0.png")
        (ledger / "r0.done.json").write_text(json.dumps({"call_id": f"{ck}_r0"}), encoding="utf-8")
        rect = next(c for c in m["crops"] if c["id"] == "030_d8449c")["rect"]
        x0, y0, x1, y1 = rect

        def add_run(mm):
            mm["candidates"].append({
                "key": f"r_{ck[:12]}_0_0", "crop_id": "030_d8449c", "tid": "A", "file": f"cands/{ck}/r0_0.png",
                "src_size": [arr.shape[1], arr.shape[0]], "origin": "run",
                "origin_info": {"call_id": f"{ck}_r0", "variant": job["variant"], "model": job["model"],
                                "quality": job["quality"], "backend": "mock"},
                "quad": [float(v) for v in (x0, y0, x1, y0, x1, y1, x0, y1)], "picked": False, "z": None, "hand_mask": None})
            mm["calls"].append({"call_id": f"{ck}_r0", "cell_key": ck, "rep": 0, "status": "done", "backend": "mock", "n": 1,
                                "slots": [f"cands/{ck}/r0_0.png"], "approved_by": "board"})

        store.update_manifest(root, add_run)
        note(f"reg_copy: 크롭 {len(m['crops'])} · 후보 {len(m['candidates']) + 1} · 분석 {len(m['analysis'])} · "
             f"jobs {len(m['jobs'])} · 복사 파일 {n}")
        return root


    # ══════════════════════════════════════════════════════════════════════
    # HTTP 도우미
    # ══════════════════════════════════════════════════════════════════════
    class Http:
        def __init__(self, client: TestClient):
            self.c = client

        async def get(self, path, **params):
            r = await self.c.get(path, params=params, allow_redirects=False)
            body = await r.read()
            return r.status, r.headers, body

        async def jget(self, path, **params):
            st, hd, body = await self.get(path, **params)
            return st, json.loads(body.decode("utf-8")) if hd.get("Content-Type", "").startswith("application/json") else body

        async def post(self, path, payload, ctype="application/json"):
            data = payload if isinstance(payload, (bytes, str)) else json.dumps(payload)
            r = await self.c.post(path, data=data, headers={"Content-Type": ctype})
            body = await r.read()
            try:
                return r.status, json.loads(body.decode("utf-8"))
            except ValueError:
                return r.status, body


    def thumb_dir(root) -> Path:
        return Path(root) / "derived" / "thumbs"


    def jpeg_size(b: bytes):
        with Image.open(io.BytesIO(b)) as im:
            return im.format, im.size


    # ══════════════════════════════════════════════════════════════════════
    # 테스트
    # ══════════════════════════════════════════════════════════════════════
    def b0_import():
        section("B0 import 의존성·규약")
        new = set(sys.modules) - _PRE
        for banned in ("torch", "comfy", "folder_paths", "server", "nodes"):
            check(banned not in new, f"board import 가 {banned} 를 불러옴")
        src = (PKG / "bmk_design_patch_board.py").read_text(encoding="utf-8")
        check(src.startswith('"""BMK Design Patch Board') and "from __future__ import annotations" in src, "모듈 머리(docstring·future)")
        check("logger = logging.getLogger(__name__)" in src and '_TAG = "[ComfyUI_BMK_Nodes::DesignPatch]"' in src, "logger/_TAG 규약")
        check(not any(ord(ch) >= 0x1F000 or ch == "\ufe0f" for ch in src), "소스에 이모지 없음")
        check(board.store is store, "단독 실행: 절대 import 폴백")
        for name in ("register_routes", "build_state", "prebuild_thumbs", "board_url", "set_drain", "drain_requested",
                     "clear_drain", "run_active", "is_running", "render_thumb", "focus_box"):
            check(callable(getattr(board, name, None)), f"공개 함수 {name}")


    async def b1_routes(syn_key):
        section("B1 register_routes (멱등·경로·레지스트리 복구)")
        routes = web.RouteTableDef()
        check(board.register_routes(routes) is True, "첫 등록 True")
        n1 = len(routes)
        check(board.register_routes(routes) is False and len(routes) == n1, f"두 번째 등록은 아무것도 더하지 않음 ({n1})")
        paths = sorted({(r.method, r.path) for r in routes})
        want = sorted({("GET", "/bmk/design_patch/board"), ("GET", "/bmk/design_patch/board/"),
                       ("GET", "/bmk/design_patch/board/{name}"), ("GET", "/bmk/design_patch/state"),
                       ("GET", "/bmk/design_patch/thumb"), ("GET", "/bmk/design_patch/file"),
                       ("POST", "/bmk/design_patch/pick"), ("POST", "/bmk/design_patch/reject"),
                       ("POST", "/bmk/design_patch/reroll"), ("POST", "/bmk/design_patch/drain")})
        check(paths == want, f"라우트 목록 {paths}")
        # 재시작 흉내: 메모리 레지스트리에서 지움 → base_dir 로 다시 등록하면 _roots.json 에서 복구
        with store._ROOTS_LOCK:
            saved = store._ROOTS.pop(syn_key)
        check(store.resolve_board_root(syn_key) is None, "레지스트리에서 지운 키는 거부")
        check(board.register_routes(web.RouteTableDef(), base_dir=str(BASE)) is True, "새 RouteTableDef 에는 등록")
        check(store.resolve_board_root(syn_key) == saved["root"], "base_dir → _roots.json 에서 키 복구")
        check(board.register_routes(routes, base_dir=str(BASE)) is False, "base_dir 를 줘도 이미 등록된 테이블에는 안 더함")
        check(board.board_url(syn_key) == f"/bmk/design_patch/board/?root={syn_key}", "board_url")


    async def b2_static(h: Http, key):
        section("B2 정적 파일 (web_design_patch/ 화이트리스트)")
        st, hd, _ = await h.get("/bmk/design_patch/board", root=key)
        check(st == 302 and hd.get("Location") == f"/bmk/design_patch/board/?root={key}", f"board → board/ 리다이렉트 {st} {hd.get('Location')}")
        st, hd, body = await h.get("/bmk/design_patch/board/", root=key)
        check(st == 200 and hd["Content-Type"].startswith("text/html") and b"board.js" in body, f"board/ → board.html {st}")
        check(hd.get("X-Content-Type-Options") == "nosniff" and hd.get("Cache-Control") == "no-store", "정적 헤더")
        for name, ct in (("board.html", "text/html"), ("board.js", "text/javascript"), ("board.css", "text/css")):
            st, hd, body = await h.get(f"/bmk/design_patch/board/{name}")
            check(st == 200 and hd["Content-Type"].startswith(ct) and body == (PKG / "web_design_patch" / name).read_bytes(),
                  f"{name} {st} {hd.get('Content-Type')}")
        for bad in ("BOARD.JS", "board.js.bak", "secret.txt", "..%2F..%2Fbmk_design_patch_board.py", "%2e%2e", "board.js%00",
                    "board.js%2F..%2F..%2Fbmk_design_patch_board.py"):
            st, _, _ = await h.get(f"/bmk/design_patch/board/{bad}")
            check(st in (400, 404), f"화이트리스트 밖 거부 {bad} → {st}")
        st, _, _ = await h.get("/bmk/design_patch/board/../bmk_design_patch_board.py")
        check(st in (400, 404), f"경로 순회 정적 요청 거부 {st}")


    async def b3_roots(h: Http, key, root):
        section("B3 root 검증 (레지스트리 키만)")
        bads = ["", "000000000000", key.upper(), key + "\n", key[:-1], root, os.path.dirname(root), "../" + key,
                key + "0", "zzzzzzzzzzzz"]
        for b in bads:
            st, d = await h.jget("/bmk/design_patch/state", root=b)
            check(st == 404 and "등록되지 않은" in (d.get("error") or ""), f"state root={b!r} → {st}")
        st, d = await h.jget("/bmk/design_patch/state")
        check(st == 404, f"root 없음 → {st}")
        for path, params in (("/bmk/design_patch/thumb", {"kind": "before", "key": "x/A"}),
                             ("/bmk/design_patch/file", {"rel": "inputs/x.png"})):
            for b in (root, "000000000000"):
                st, _ = await h.jget(path, root=b, **params)
                check(st == 404, f"{path} root={b[:20]!r} → {st}")
        for path in ("pick", "reject", "reroll", "drain"):
            for b in (root, "000000000000", None, 123):
                st, d = await h.post(f"/bmk/design_patch/{path}", {"root": b, "key": "x", "crop_id": "x", "tid": "A",
                                                                    "mode": "set", "value": True, "cell_key": "0" * 24})
                check(st == 404, f"POST {path} root={str(b)[:20]!r} → {st} {d}")


    async def b4_state(h: Http, key, root):
        section("B4 state")
        st, s = await h.jget("/bmk/design_patch/state", root=key)
        check(st == 200 and isinstance(s, dict) and "targets" in s, f"state 200 {st}")
        m = store.load_manifest(root)
        check(s["rev"] == m["rev"] and s["project"] == "syn_xss" and s["root_key"] == key, "rev·project·root_key")
        check([t["tid"] for t in s["targets"]] == ["A", "B"], f"다중 타깃 크롭 → 타깃 2개 {[t['tid'] for t in s['targets']]}")
        tA = s["targets"][0]
        check(tA["crop_name"] == XSS_NAME, "XSS 문자열은 서버가 바꾸지 않고 JSON 텍스트로 그대로(보드가 textContent 로 씀)")
        check(len(tA["candidates"]) == 3 and len(s["targets"][1]["candidates"]) == 1, "타깃별 후보 수")
        req = {"key", "origin", "backend", "src_size", "gates", "reg", "picked", "slice_index", "rejected", "cell_key",
               "call_id", "thumb_url", "big_url", "delta_url", "context_url", "file_url", "mock", "analysis"}
        check(all(req <= set(c) for t in s["targets"] for c in t["candidates"]), "후보 필드(명세 §4.1 + 보조)")
        check({"crop_id", "tid", "label", "name_en", "rect", "candidates", "pick_keys", "refs", "focus_box", "cells",
               "pending_calls", "chips", "before_url"} <= set(tA), "타깃 필드")
        check({"rev", "project", "backend_mock", "targets", "pending_calls", "running", "drain"} <= set(s), "최상위 필드")
        check(tA["focus_box"] == [0, 0, 200, 160], f"분석 없으면 초점 = 크롭 전체 {tA['focus_box']}")
        check(all(c["analysis"] == "none" and c["gates"] is None for c in tA["candidates"]), "분석 없음 표시")
        check([c["variant"] for c in tA["cells"]] == ["V2", "V1"] and [c["variant"] for c in s["targets"][1]["cells"]] == ["V1"],
              f"타깃 셀(단독 작업 먼저) {[c['variant'] for c in tA['cells']]}")
        check(len(tA["refs"]) == 1 and tA["refs"][0]["file"].startswith("inputs/"), "레퍼런스 = jobs 의 role=ref(inputs/)")
        check(all(c["est_usd"] and c["est_usd"][0] <= c["est_usd"][1] for c in tA["cells"]), "셀 예상 USD")
        check(s["backend_mock"] is False, "mock 비활성")
        (BASE / "bmk_design_patch" / "_MOCK_API").write_text("", encoding="utf-8")
        st, s2 = await h.jget("/bmk/design_patch/state", root=key)
        check(s2["backend_mock"] is True, "_MOCK_API 파일 → backend_mock")
        (BASE / "bmk_design_patch" / "_MOCK_API").unlink()
        st, u = await h.jget("/bmk/design_patch/state", root=key, since_rev=str(s["rev"]))
        check(st == 200 and u == {"unchanged": True, "rev": s["rev"], "running": False, "drain": False}, f"since_rev 같음 → unchanged {u}")
        st, u = await h.jget("/bmk/design_patch/state", root=key, since_rev=str(s["rev"] - 1))
        check(st == 200 and "targets" in u, "since_rev 다름 → 전체")
        for bad in ("abc", "-1", "1.5", "9" * 20):
            st, _ = await h.jget("/bmk/design_patch/state", root=key, since_rev=bad)
            check(st == 400, f"since_rev={bad} → 400 ({st})")
        st, hd, body = await h.get("/bmk/design_patch/state", root=key)
        check(hd["Content-Type"].startswith("application/json") and hd.get("Cache-Control") == "no-store", "state JSON·no-store")
        check(b"<img" in body and json.dumps(XSS_NAME, ensure_ascii=False).encode("utf-8") in body,
              "응답 본문은 JSON(ensure_ascii=False) 텍스트 — 따옴표만 JSON 이스케이프")
        return s


    async def b5_file(h: Http, key, root, s):
        section("B5 file (프로젝트 안 이미지만)")
        c = s["targets"][0]["candidates"][0]
        m = store.load_manifest(root)
        cand = next(x for x in m["candidates"] if x["key"] == c["key"])
        st, hd, body = await h.get("/bmk/design_patch/file", root=key, rel=cand["file"])
        check(st == 200 and body == (Path(root) / cand["file"]).read_bytes() and hd["Content-Type"] == "image/png",
              f"cands 파일 그대로 {st}")
        st, hd, _ = await h.get("/bmk/design_patch/file", root=key, rel=m["base"])
        check(st == 200, "inputs 파일")
        other = store.project_root(str(BASE), "reg_copy")
        bads = {
            "../syn_xss/design_patch.json": 400, "design_patch.json": 400, "../_roots.json": 400,
            "inputs/../design_patch.json": 400, "inputs/../../_roots.json": 400, "/etc/passwd": 400,
            "H:/BmkNodeDesign/x.png": 400, "inputs\\..\\design_patch.json": 400, "cands/x.png:stream": 400,
            str(Path(root) / cand["file"]): 400, "specs/a.png": 400, "inputs/nope.png": 404, "cands/": 400,
            "../reg_copy/" + "inputs/x.png": 400, os.path.relpath(other, root).replace("\\", "/") + "/inputs/x.png": 400,
            "cands/x.txt": 400, "": 400, "a" * 400 + ".png": 400,
        }
        for rel, want in bads.items():
            st, _, _ = await h.get("/bmk/design_patch/file", root=key, rel=rel)
            check(st == want, f"file rel={rel[:60]!r} → {st} (기대 {want})")
        st, _, _ = await h.get("/bmk/design_patch/file", root=key)
        check(st == 400, "rel 없음 → 400")


    async def b6_pick(h: Http, key, root, s):
        section("B6 pick (set/add/remove, picked 동기화)")
        t = s["targets"][0]
        k0, k1, k2 = (c["key"] for c in t["candidates"])
        base_req = {"root": key, "crop_id": t["crop_id"], "tid": "A"}

        def picks():
            m = store.load_manifest(root)
            return m["picks"].get(t["key"]), {c["key"]: c["picked"] for c in m["candidates"] if c["tid"] == "A"}, m["rev"]

        rev0 = store.load_manifest(root)["rev"]
        st, d = await h.post("/bmk/design_patch/pick", dict(base_req, key=k0, mode="set"))
        p, flags, rev = picks()
        check(st == 200 and d["pick_keys"] == [k0] and p == [k0] and flags == {k0: True, k1: False, k2: False},
              f"set → [k0] {st} {d} {p}")
        check(rev == rev0 + 1 and d["rev"] == rev, f"쓰기 1회 = rev+1 ({rev0}→{rev})")
        st, d = await h.post("/bmk/design_patch/pick", dict(base_req, key=k1, mode="add"))
        p, flags, rev_a = picks()
        check(st == 200 and p == [k0, k1] and flags[k1] and flags[k0], f"add → [k0,k1] {p}")
        st, d = await h.post("/bmk/design_patch/pick", dict(base_req, key=k1, mode="add"))
        p, _, rev2 = picks()
        check(st == 200 and p == [k0, k1] and rev2 == rev_a == d["rev"], f"같은 add 두 번 → 저장 안 함(rev {rev_a} → {rev2})")
        st, s2 = await h.jget("/bmk/design_patch/state", root=key)
        c2 = {c["key"]: c for c in s2["targets"][0]["candidates"]}
        check(c2[k0]["slice_index"] == 1 and c2[k1]["slice_index"] == 2 and c2[k2]["slice_index"] is None
              and s2["targets"][0]["pick_keys"] == [k0, k1], "state 의 slice_index·pick_keys")
        st, d = await h.post("/bmk/design_patch/pick", dict(base_req, key=k2, mode="set"))
        p, flags, _ = picks()
        check(p == [k2] and flags == {k0: False, k1: False, k2: True}, f"set 은 교체 {p}")
        st, d = await h.post("/bmk/design_patch/pick", dict(base_req, key=k2, mode="remove"))
        p, flags, _ = picks()
        check(st == 200 and p is None and not any(flags.values()), "마지막 remove → picks 키 삭제·플래그 모두 False")
        st, d = await h.post("/bmk/design_patch/pick", dict(base_req, key=k2, mode="remove"))
        check(st == 200 and d["pick_keys"] == [], "없는 pick remove → 200, 변화 없음")
        # 다른 타깃(B) 은 건드리지 않음
        tB = s["targets"][1]
        kb = tB["candidates"][0]["key"]
        await h.post("/bmk/design_patch/pick", {"root": key, "crop_id": tB["crop_id"], "tid": "B", "key": kb, "mode": "set"})
        await h.post("/bmk/design_patch/pick", dict(base_req, key=k0, mode="set"))
        m = store.load_manifest(root)
        check(m["picks"].get(tB["key"]) == [kb] and m["picks"].get(t["key"]) == [k0], "타깃별 독립")
        # 오류
        cases = [
            (dict(base_req, key=kb, mode="set"), 409, "다른 타깃의 후보"),
            (dict(base_req, key="f_nonexistent", mode="set"), 404, "없는 후보"),
            (dict(base_req, key=k0, mode="toggle"), 400, "mode"),
            (dict(base_req, key="../x", mode="set"), 400, "key 형식"),
            (dict(base_req, key=k0, mode="set", tid="a"), 400, "tid 형식"),
            (dict(base_req, key=k0, mode="set", crop_id="../x"), 400, "crop_id 형식"),
            (dict(base_req, key=None, mode="set"), 400, "key 없음"),
            (dict(base_req, key=["x"], mode="set"), 400, "key 목록"),
        ]
        rev_before = store.load_manifest(root)["rev"]
        for body, want, what in cases:
            st, d = await h.post("/bmk/design_patch/pick", body)
            check(st == want and isinstance(d, dict) and d.get("error"), f"pick 오류 {what} → {st} (기대 {want}) {d}")
        st, d = await h.post("/bmk/design_patch/pick", json.dumps(dict(base_req, key=k0, mode="set")), ctype="text/plain")
        check(st == 415, f"Content-Type text/plain → 415 ({st})")
        st, d = await h.post("/bmk/design_patch/pick", "{not json")
        check(st == 400, f"깨진 JSON → 400 ({st})")
        st, d = await h.post("/bmk/design_patch/pick", "[1,2]")
        check(st == 400, f"JSON 배열 → 400 ({st})")
        st, d = await h.post("/bmk/design_patch/pick", json.dumps(dict(base_req, key=k0, mode="set", pad="x" * 20000)))
        check(st == 413, f"16KB 넘는 본문 → 413 ({st})")
        check(store.load_manifest(root)["rev"] == rev_before, "거부된 요청은 저장하지 않음(rev 그대로)")


    async def b7_reject(h: Http, key, root, s):
        section("B7 reject")
        t = s["targets"][0]
        k0, k1 = t["candidates"][0]["key"], t["candidates"][1]["key"]
        base_req = {"root": key, "crop_id": t["crop_id"], "tid": "A"}
        await h.post("/bmk/design_patch/pick", dict(base_req, key=k0, mode="set"))
        await h.post("/bmk/design_patch/pick", dict(base_req, key=k1, mode="add"))
        st, d = await h.post("/bmk/design_patch/reject", {"root": key, "key": k0, "value": True})
        m = store.load_manifest(root)
        c0 = next(c for c in m["candidates"] if c["key"] == k0)
        check(st == 200 and d["rejected"] is True and d["unpicked"] is True, f"reject true {st} {d}")
        check(m["rejects"].get(k0) is True and not c0["picked"] and m["picks"][t["key"]] == [k1],
              "rejects[k]=true + pick 에서 뺌(나머지 slice 유지)")
        st, s2 = await h.jget("/bmk/design_patch/state", root=key)
        c2 = {c["key"]: c for c in s2["targets"][0]["candidates"]}
        check(c2[k0]["rejected"] and s2["targets"][0]["chips"]["rejected"] == 1 and s2["counts"]["rejected"] == 1,
              "state rejected·chips")
        st, d = await h.post("/bmk/design_patch/reject", {"root": key, "key": k0, "value": False})
        m = store.load_manifest(root)
        check(st == 200 and k0 not in m["rejects"] and d["unpicked"] is False, "reject false → 키 삭제")
        await h.post("/bmk/design_patch/reject", {"root": key, "key": k0, "value": True})
        st, d = await h.post("/bmk/design_patch/pick", dict(base_req, key=k0, mode="set"))
        m = store.load_manifest(root)
        check(d["rejected_cleared"] is True and k0 not in m["rejects"], "탈락 후보를 ★ 하면 탈락 해제")
        for body, want in (({"root": key, "key": k0, "value": "true"}, 400), ({"root": key, "key": k0, "value": 1}, 400),
                           ({"root": key, "key": k0}, 400), ({"root": key, "key": "f_nope", "value": True}, 404)):
            st, d = await h.post("/bmk/design_patch/reject", body)
            check(st == want, f"reject 오류 {body} → {st} (기대 {want})")


    async def b8_reroll(h: Http, key, root, s):
        section("B8 reroll (+pending)")
        t = s["targets"][0]
        cells = {c["variant"]: c for c in t["cells"]}
        ck2 = cells["V2"]["cell_key"]
        ck1 = cells["V1"]["cell_key"]
        st0, s0 = await h.jget("/bmk/design_patch/state", root=key)
        p0 = s0["pending_calls"]
        cp0 = {c["cell_key"]: c["pending"] for c in s0["targets"][0]["cells"]}
        check(p0 == 2 and cp0 == {ck2: 1, ck1: 1},
              f"원장 없음 → 셀마다 reps 1 대기 {p0} {cp0} (runner {'있음' if board._runner() else '없음'})")
        st, d = await h.post("/bmk/design_patch/reroll", {"root": key, "cell_key": ck2, "count": 1})
        m = store.load_manifest(root)
        check(st == 200 and d["rerolls"] == 1 and m["rerolls"].get(ck2) == 1 and d["cell_key"] == ck2, f"cell_key +1 {st} {d}")
        check(d["pending_calls"] == p0 + 1 and d["cell_pending"] == cp0[ck2] + 1, f"응답 pending +1 ({p0}→{d['pending_calls']})")
        st, d = await h.post("/bmk/design_patch/reroll", {"root": key, "crop_id": t["crop_id"], "tid": "A", "variant": "V2",
                                                          "count": 2})
        check(st == 200 and d["rerolls"] == 3 and d["cell_key"] == ck2, f"(crop_id, tid, variant) +2 → 3 {d}")
        st, d = await h.post("/bmk/design_patch/reroll", {"root": key, "crop_id": t["crop_id"], "tid": "B", "count": 1})
        check(st == 200 and d["cell_key"] == ck1, f"B 는 작업이 하나(V1, 다중 타깃) → variant 없이 OK {st} {d}")
        st, d = await h.post("/bmk/design_patch/reroll", {"root": key, "crop_id": t["crop_id"], "tid": "A", "count": 1})
        check(st == 400 and "variant" in d.get("error", ""), f"A 는 작업 2개 → variant 필요 400 ({st})")
        st, s1 = await h.jget("/bmk/design_patch/state", root=key)
        cp1 = {c["cell_key"]: (c["rerolls"], c["pending"]) for c in s1["targets"][0]["cells"]}
        check(cp1[ck2] == (3, cp0[ck2] + 3) and cp1[ck1] == (1, cp0[ck1] + 1) and s1["rerolls_total"] == 4,
              f"state 셀 rerolls·pending {cp1}")
        check(s1["targets"][1]["pending_calls"] == cp0[ck1] + 1, "다중 타깃 셀은 B 타깃 대기에도 보임")
        errs = [({"root": key, "cell_key": "0" * 24, "count": 1}, 404), ({"root": key, "cell_key": ck2, "count": 0}, 400),
                ({"root": key, "cell_key": ck2, "count": 5}, 400), ({"root": key, "cell_key": ck2, "count": True}, 400),
                ({"root": key, "cell_key": ck2, "count": "1"}, 400), ({"root": key, "cell_key": "ZZ", "count": 1}, 400),
                ({"root": key, "crop_id": "nope_000000", "tid": "A", "count": 1}, 404),
                ({"root": key, "crop_id": t["crop_id"], "tid": "A", "variant": "V9", "count": 1}, 404)]
        for body, want in errs:
            st, d = await h.post("/bmk/design_patch/reroll", body)
            check(st == want, f"reroll 오류 {body} → {st} (기대 {want}) {d}")
        for _ in range(3):
            await h.post("/bmk/design_patch/reroll", {"root": key, "cell_key": ck2, "count": 4})
        st, d = await h.post("/bmk/design_patch/reroll", {"root": key, "cell_key": ck2, "count": 4})
        m = store.load_manifest(root)
        check(st == 409 and m["rerolls"][ck2] == 15, f"셀 대기 16 + 4 > 상한 16 → 409, 값 유지 ({st}, {m['rerolls'][ck2]})")
        pend, pre = board._pending(root, m)
        check(pend == {ck2: 16, ck1: 2} and pre == 16, f"대기 16+2, 그중 재굴림 사전 승인 15+1 ({pend}, {pre})")
        # 원장 파일·calls 요약이 발사로 셈해짐 (runner 쪽 계산 + 원장 대체 계산)
        ledger = Path(root) / "cands" / ck1
        ledger.mkdir(parents=True, exist_ok=True)
        (ledger / "r0.done.json").write_text("{}", encoding="utf-8")
        pend, pre = board._pending(root, store.load_manifest(root))
        check(pend[ck1] == 1, f"원장 r0.done → 셀 대기 2 → 1 ({pend[ck1]})")
        (ledger / "r1.inflight.json").write_text("{}", encoding="utf-8")
        (ledger / "r2.req.json").write_text("{}", encoding="utf-8")
        lp = board._ledger_pending(root, store.load_manifest(root))
        check(lp[ck1] == 0, f"원장 대체 계산: done+inflight = 발사 2, req 만 있는 것은 미발사 → {lp[ck1]}")
        store.update_manifest(root, lambda mm: mm["calls"].append({"call_id": f"{ck2}_r7", "cell_key": ck2,
                                                                     "status": "orphaned"}))
        lp = board._ledger_pending(root, store.load_manifest(root))
        check(lp[ck2] == 1 + 15 - 1, f"원장 대체 계산: calls 요약(call_id 에서 rep)도 발사로 셈 → {lp[ck2]}")
        shutil.rmtree(ledger)
        # 상한은 누적 rerolls 가 아니라 아직 소화하지 않은 대기 호출: Run 이 소화하면(원장 done) 다시 재굴림 가능
        ledger2 = Path(root) / "cands" / ck2
        ledger2.mkdir(parents=True, exist_ok=True)
        for r in range(12):
            (ledger2 / f"r{r}.done.json").write_text("{}", encoding="utf-8")
        m = store.load_manifest(root)
        m["calls"] = [c for c in m["calls"] if c.get("call_id") != f"{ck2}_r7"]
        store.update_manifest(root, lambda mm: mm.__setitem__("calls", m["calls"]))
        st, d = await h.post("/bmk/design_patch/reroll", {"root": key, "cell_key": ck2, "count": 4})
        check(st == 200 and d["rerolls"] == 19 and d["cell_pending"] == 1 + 19 - 12,
              f"원장 done 12개 소화 → 대기 4 → +4 허용, 누적 19 ({st}, {d.get('rerolls')}, 대기 {d.get('cell_pending')})")
        shutil.rmtree(ledger2)


    async def b9_drain(h: Http, key, root):
        section("B9 drain·run 레지스트리")
        board.clear_drain(root)
        check(not board.drain_requested(root) and not board.is_running(root), "초기 상태")
        st, d = await h.post("/bmk/design_patch/drain", {"root": key})
        check(st == 200 and d == {"ok": True, "drain": False, "running": False}, f"drain (실행 없음) → 아무것도 안 함 {d}")
        check(not board.drain_requested(root), "실행 밖 drain 은 플래그를 남기지 않음(다음 Run 을 막지 않음)")
        check(board.set_drain(root) is False and board.drain_requested(root) and board.drain_requested(root.upper()),
              "set_drain 직접 호출 → 이 모듈 플래그(경로 표기 무관), 반환 = 실행 중 아님")
        with board.run_active(root):
            check(board.is_running(root) and not board.drain_requested(root), "run_active 진입 → 지난 drain 플래그 지움")
            st, s = await h.jget("/bmk/design_patch/state", root=key)
            check(s["running"] is True and s["drain"] is False, "state running")
            st, d = await h.post("/bmk/design_patch/drain", {"root": key})
            check(d == {"ok": True, "drain": True, "running": True} and board.drain_requested(root), f"실행 중 drain {d}")
            with board.run_active(root):
                check(board.drain_requested(root), "중첩 Run 진입은 플래그 유지")
            check(board.is_running(root) and board.drain_requested(root), "안쪽 Run 끝나도 바깥 Run 이 있으면 유지")
            rev = store.load_manifest(root)["rev"]
            st, u = await h.jget("/bmk/design_patch/state", root=key, since_rev=str(rev))
            check(u.get("unchanged") and u["running"] is True and u["drain"] is True, f"unchanged 응답에도 running·drain {u}")
        check(not board.is_running(root) and not board.drain_requested(root), "마지막 Run 끝 → running 끔·drain 지움")
        board.set_drain(root)
        board.clear_drain(root)
        check(not board.drain_requested(root), "clear_drain")
        try:
            board.set_drain("")
            check(False, "빈 root → ValueError")
        except ValueError:
            check(True, "빈 root → ValueError")
        st, d = await h.post("/bmk/design_patch/drain", "{}", ctype="text/plain")
        check(st == 415, "drain 도 JSON 만")
        runner = board._runner()
        if runner is not None and all(hasattr(runner, a) for a in ("_REG_LOCK", "_RUNS", "_rid", "request_drain")):
            rid = runner._rid(root)
            with runner._REG_LOCK:  # runner.run_calls 가 도는 상태 흉내(이 테스트 안에서만, 끝나면 되돌림)
                runner._RUNS[rid] = runner._RUNS.get(rid, 0) + 1
            try:
                check(board.is_running(root) and not board.drain_requested(root), "runner 실행 중 → is_running")
                st, d = await h.post("/bmk/design_patch/drain", {"root": key})
                check(d == {"ok": True, "drain": True, "running": True} and runner.drain_requested(root)
                      and board.drain_requested(root) and store.root_key(root) not in board._DRAIN,
                      "runner 실행 중 drain → runner 레지스트리에 걸림(run_calls 기본 drain_flag 가 봄)")
                st, s = await h.jget("/bmk/design_patch/state", root=key)
                check(s["running"] is True and s["drain"] is True, "state 가 runner 의 running·drain 을 보여 줌")
            finally:
                with runner._REG_LOCK:
                    n = runner._RUNS.get(rid, 1) - 1
                    if n > 0:
                        runner._RUNS[rid] = n
                    else:
                        runner._RUNS.pop(rid, None)
                    runner._DRAIN.discard(rid)
            check(not board.is_running(root) and not board.drain_requested(root), "runner 실행 끝 → 원상태")
        else:
            skip("runner 모듈이 없어 runner 레지스트리 연동 검사 건너뜀")


    async def b10_thumbs(h: Http, key, root, s, label):
        section(f"B10 썸네일 ({label})")
        td = thumb_dir(root)
        shutil.rmtree(td, ignore_errors=True)
        t = next(t for t in s["targets"] if t["candidates"])
        c = t["candidates"][0]
        t0 = time.perf_counter()
        st, hd, b1 = await h.get("/bmk/design_patch/thumb", root=key, kind="cand", key=c["key"], size="256")
        cold = time.perf_counter() - t0
        check(st == 200 and hd["Content-Type"] == "image/jpeg" and jpeg_size(b1)[0] == "JPEG", f"cand 256 JPEG {st}")
        files1 = sorted(os.listdir(td))
        check(any(f.startswith("cand_") for f in files1) and any(f.startswith("placed_") for f in files1),
              f"derived/thumbs 에 cand_·placed_ 캐시 {files1}")
        mt = {f: os.stat(td / f).st_mtime_ns for f in files1}
        t0 = time.perf_counter()
        st, hd2, b2 = await h.get("/bmk/design_patch/thumb", root=key, kind="cand", key=c["key"], size="256")
        warm = time.perf_counter() - t0
        check(b2 == b1 and sorted(os.listdir(td)) == files1 and all(os.stat(td / f).st_mtime_ns == mt[f] for f in files1),
              "두 번째 요청은 캐시 파일 그대로(새 파일·덮어쓰기 없음)")
        note(f"{label} thumb cand@256 cold {cold * 1000:.0f}ms / warm {warm * 1000:.0f}ms")
        check(hd2.get("Cache-Control") == "no-cache" and hd2.get("ETag"), "v 없으면 no-cache")
        url = dict(p.split("=", 1) for p in c["thumb_url"].split("?", 1)[1].split("&"))
        st, hd3, b3 = await h.get("/bmk/design_patch/thumb", root=key, kind="cand", key=c["key"], size="256", v=url["v"])
        check(st == 200 and "immutable" in hd3.get("Cache-Control", "") and hd3["ETag"] == f'"{url["v"]}"',
              "state 의 thumb_url v == 캐시 키 → immutable")
        check(any(f == f"cand_{url['v']}.jpg" for f in os.listdir(td)), "캐시 파일 이름 = cand_<v>.jpg")
        # before / cand 같은 크기(Q 깜빡임 정렬), 크기 = 초점 박스 비율
        st, _, bb = await h.get("/bmk/design_patch/thumb", root=key, kind="before", key=t["key"], size="512")
        st2, _, bc = await h.get("/bmk/design_patch/thumb", root=key, kind="cand", key=c["key"], size="512")
        fx0, fy0, fx1, fy1 = t["focus_box"]
        fw, fh = fx1 - fx0, fy1 - fy0
        want = (max(1, round(fw * 512 / max(fw, fh))), max(1, round(fh * 512 / max(fw, fh))))
        check(st == st2 == 200 and jpeg_size(bb)[1] == jpeg_size(bc)[1] == want,
              f"before·cand 512 같은 크기 = 초점 비율 {jpeg_size(bb)[1]} {jpeg_size(bc)[1]} {want}")
        for kind, k in (("delta", c["key"]), ("context", c["key"])):
            st, hd, b = await h.get("/bmk/design_patch/thumb", root=key, kind=kind, key=k, size="512")
            check(st == 200 and jpeg_size(b)[0] == "JPEG" and max(jpeg_size(b)[1]) == 512, f"{kind} 512 {st}")
        if t["refs"]:
            st, hd, b = await h.get("/bmk/design_patch/thumb", root=key, kind="ref", key=f"{t['key']}/0", size="512")
            check(st == 200 and max(jpeg_size(b)[1]) == 512, f"ref 0 {st}")
            st, _, _ = await h.get("/bmk/design_patch/thumb", root=key, kind="ref", key=f"{t['key']}/{len(t['refs'])}", size="512")
            check(st == 404, "없는 레퍼런스 번호 → 404")
        # 모든 후보·kind 가 렌더되는지(부분 패치·mock run·손 마스크 포함)
        n_ok = 0
        for tt in s["targets"]:
            for cc in tt["candidates"]:
                for u in (cc["thumb_url"], cc["delta_url"], cc["context_url"]):
                    st, _, _ = await h.get("/bmk/design_patch/" + u.split("?")[0],
                                           **dict(p.split("=", 1) for p in u.split("?", 1)[1].split("&")))
                    n_ok += st == 200
                    if st != 200:
                        check(False, f"{label} {u} → {st}")
        total = sum(len(tt["candidates"]) for tt in s["targets"]) * 3
        check(n_ok == total, f"{label} 모든 후보 cand/delta/context 200 ({n_ok}/{total})")
        # 오류
        errs = [({"kind": "nope", "key": c["key"], "size": "256"}, 400), ({"kind": "cand", "key": c["key"], "size": "300"}, 400),
                ({"kind": "cand", "key": c["key"], "size": "abc"}, 400), ({"kind": "cand", "key": "../x", "size": "256"}, 400),
                ({"kind": "cand", "key": "f_nope", "size": "256"}, 404), ({"kind": "before", "key": "x", "size": "256"}, 400),
                ({"kind": "before", "key": t["crop_id"] + "/Z", "size": "256"}, 404),
                ({"kind": "ref", "key": t["key"] + "/x", "size": "256"}, 400), ({"kind": "before", "key": "../a/A", "size": "256"}, 400)]
        for params, want in errs:
            st, _, body = await h.get("/bmk/design_patch/thumb", root=key, **params)
            check(st == want, f"thumb 오류 {params} → {st} (기대 {want})")


    def focus_rule_check(root, s):
        section("B10b 초점 박스 규칙 (실데이터: 자동 마스크 bbox 합집합 +20%)")
        m = store.load_manifest(root)
        for t in s["targets"]:
            crop = next(c for c in m["crops"] if c["id"] == t["crop_id"])
            x0, y0, x1, y1 = crop["rect"]
            w, h = x1 - x0, y1 - y0
            files = []
            for c in t["candidates"]:
                e = board._entry_for(m, c["key"], None)
                if board._usable(e) and e.get("automask"):
                    files.append(e["automask"])
            if not files:
                check(t["focus_box"] == [0, 0, w, h], f"{t['key']} 자동 마스크 없음 → 크롭 전체 {t['focus_box']}")
                continue
            union = None
            for f in files:
                a = np.asarray(Image.open(Path(root) / f).convert("L"))
                ys, xs = np.nonzero(a >= 128)
                if len(xs):
                    b = [xs.min(), ys.min(), xs.max() + 1, ys.max() + 1]
                    union = b if union is None else [min(union[0], b[0]), min(union[1], b[1]), max(union[2], b[2]), max(union[3], b[3])]
            fb = t["focus_box"]
            inside = 0 <= fb[0] <= union[0] and 0 <= fb[1] <= union[1] and union[2] <= fb[2] <= w and union[3] <= fb[3] <= h
            uw, uh = union[2] - union[0], union[3] - union[1]
            px, py = -(-uw // 10), -(-uh // 10)
            padded = [max(0, union[0] - px), max(0, union[1] - py), min(w, union[2] + px), min(h, union[3] + py)]
            minimum = max(16, min(w, h) // 4)
            grown = fb != padded
            rule = fb == padded or (grown and min(minimum, w) <= fb[2] - fb[0] and min(minimum, h) <= fb[3] - fb[1]
                                    and all(abs(a) <= max(minimum, 1) for a in (fb[0] - padded[0], fb[2] - padded[2])))
            check(inside and rule, f"{t['key']} 초점 {fb} = 마스크 합집합 {union} 변마다 +10% {padded} (최소 {minimum}) (크롭 {w}x{h})")
            note(f"{t['key']} 크롭 {w}x{h} 마스크 합집합 {union} → 초점 {fb}" + (" (최소 크기로 넓힘)" if grown else ""))


    async def b11_concurrency(h: Http, key, root, s):
        section("B11 동시 요청 (쓰기 잃음 0, 같은 썸네일 한 번)")
        t = s["targets"][0]
        keys = [c["key"] for c in t["candidates"]]
        await h.post("/bmk/design_patch/pick", {"root": key, "crop_id": t["crop_id"], "tid": "A", "key": keys[0], "mode": "remove"})
        for k in keys:
            await h.post("/bmk/design_patch/pick", {"root": key, "crop_id": t["crop_id"], "tid": "A", "key": k, "mode": "remove"})
        store.update_manifest(root, lambda m: m["rejects"].clear())
        ck = s["targets"][1]["cells"][0]["cell_key"]
        store.update_manifest(root, lambda m: m["rerolls"].pop(ck, None))
        rev0 = store.load_manifest(root)["rev"]
        reqs = [h.post("/bmk/design_patch/pick", {"root": key, "crop_id": t["crop_id"], "tid": "A", "key": k, "mode": "add"})
                for k in keys]
        reqs += [h.post("/bmk/design_patch/reroll", {"root": key, "cell_key": ck, "count": 1}) for _ in range(8)]
        reqs += [h.post("/bmk/design_patch/reject", {"root": key, "key": keys[-1], "value": False}) for _ in range(4)]
        res = await asyncio.gather(*reqs)
        m = store.load_manifest(root)
        check(all(st == 200 for st, _ in res), f"동시 요청 모두 200 {[st for st, _ in res]}")
        check(sorted(m["picks"].get(t["key"]) or []) == sorted(keys) and m["rerolls"].get(ck) == 8,
              f"동시 add {len(keys)} + reroll 8 → 잃은 갱신 0 ({m['picks'].get(t['key'])}, rerolls {m['rerolls'].get(ck)})")
        check(m["rev"] == rev0 + len(keys) + 8, f"바뀐 쓰기만 rev 증가 ({rev0} → {m['rev']}, 기대 +{len(keys) + 8})")
        td = thumb_dir(root)
        shutil.rmtree(td, ignore_errors=True)
        c = t["candidates"][1]
        res = await asyncio.gather(*[h.get("/bmk/design_patch/thumb", root=key, kind="context", key=c["key"], size="384")
                                     for _ in range(6)])
        bodies = {r[2] for r in res}
        files = os.listdir(td)
        check(all(r[0] == 200 for r in res) and len(bodies) == 1, "같은 썸네일 동시 6회 → 같은 bytes")
        check(len([f for f in files if f.startswith("context_")]) == 1 and len([f for f in files if f.startswith("placed_")]) == 1
              and not any(f.endswith(".tmp") for f in files), f"캐시 파일 1개씩, 임시 파일 없음 {files}")


    def b12_static_js():
        section("B12 board.js / html / css 정적 검사")
        js = (PKG / "web_design_patch" / "board.js").read_text(encoding="utf-8")
        html = (PKG / "web_design_patch" / "board.html").read_text(encoding="utf-8")
        css = (PKG / "web_design_patch" / "board.css").read_text(encoding="utf-8")
        code = re.sub(r"//[^\n]*", "", js)
        for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function", "setAttribute(\"on",
                    "srcdoc", "javascript:"):
            check(bad not in code, f"board.js 에 {bad} 없음")
        check("textContent" in code and "replaceChildren" in code, "textContent / replaceChildren 사용")
        for name, text in (("board.js", js), ("board.html", html), ("board.css", css)):
            check(not re.search(r"https?://", text) and "@import" not in text and "cdn" not in text.lower(),
                  f"{name} 외부 URL·CDN 없음")
            check(not any(ord(ch) >= 0x1F000 or ch == "\ufe0f" for ch in text), f"{name} 이모지 없음")
        check(not re.search(r"<script(?![^>]*\bsrc=\"board\.js\")", html) and "on" + "click=" not in html.lower(),
              "html 에 인라인 스크립트·이벤트 속성 없음")
        check('postMessage({ type: "bmk-dp-queue", rootKey: ROOT }, location.origin)' in js, "opener postMessage 형식(명세 §4.2)")
        check("POLL_MS = 2000" in js and "since_rev" in js, "2초 폴링 + since_rev")
        for code_name in ("ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "\"Space\"", "KeyX", "KeyQ", "KeyD", "KeyC", "KeyR",
                          "Slash", "Escape"):
            check(code_name in js, f"키 처리 {code_name}")
        check("e.repeat" in js, "키 반복 무시(Space/X/R)")
        check('lang="ko"' in html and "color-scheme: dark" in css, "한국어·다크 테마")


    async def b13_prebuild(root, label):
        section(f"B13 prebuild_thumbs ({label})")
        shutil.rmtree(thumb_dir(root), ignore_errors=True)
        r1 = await asyncio.to_thread(board.prebuild_thumbs, root, None)
        check(r1["built"] > 0 and not r1["failed"], f"{label} 첫 미리 생성 {r1}")
        r2 = await asyncio.to_thread(board.prebuild_thumbs, root, None)
        check(r2["built"] == 0 and r2["thumbs"] == r1["thumbs"], f"{label} 두 번째 = 새로 만든 것 0 {r2}")
        note(f"{label} prebuild 첫 {r1['seconds']}s ({r1['thumbs']}장, 파일 {r1['built']}) / 두 번째 {r2['seconds']}s")
        try:
            board.prebuild_thumbs(root, None, size=300)
            check(False, "prebuild size 300 → ValueError")
        except ValueError:
            check(True, "prebuild size 300 → ValueError")


    async def b13b_pref(h: Http, key, root):
        section("B13b 분석 설정 선택 (prebuild_thumbs analyze_params)")
        m = store.load_manifest(root)
        k = next(k for k, per in m["analysis"].items() if per and not next(iter(per.values())).get("skipped"))
        per = m["analysis"][k]
        ph, e = next(iter(per.items()))
        alt = json.loads(json.dumps(e))
        alt_params = dict(e["params"], grow=3)
        alt.update(params_hash="f" * 16, params=alt_params, reg=dict(e["reg"], applied=False))

        def add_alt(mm):
            mm["analysis"][k]["f" * 16] = alt

        store.update_manifest(root, add_alt)
        m = store.load_manifest(root)
        check(board._entry_for(m, k, None)["params_hash"] == "f" * 16, "설정 없으면 가장 최근 항목")
        check(board._entry_for(m, k, e["params"])["params_hash"] == ph, "설정이 같은 항목 우선")
        check(board._entry_for(m, k, {"x": 1})["params_hash"] == "f" * 16, "맞는 설정 없으면 최근 항목")
        await asyncio.to_thread(board.prebuild_thumbs, root, e["params"])
        st, s = await h.jget("/bmk/design_patch/state", root=key)
        c = next(c for t in s["targets"] for c in t["candidates"] if c["key"] == k)
        check(c["reg"]["applied"] == e["reg"]["applied"], "prebuild 의 analyze_params 를 state 도 따름")
        await asyncio.to_thread(board.prebuild_thumbs, root, alt_params)
        st, s = await h.jget("/bmk/design_patch/state", root=key)
        c2 = next(c for t in s["targets"] for c in t["candidates"] if c["key"] == k)
        check(c2["reg"]["applied"] is False and c2["thumb_url"] != c["thumb_url"], "설정을 바꾸면 썸네일 서명도 바뀜")

        def drop_alt(mm):
            mm["analysis"][k].pop("f" * 16, None)

        store.update_manifest(root, drop_alt)
        board.prebuild_thumbs(root, None)


    async def b14_direct(syn_key, syn_root):
        section("B14 핸들러 직접 호출 (make_mocked_request)")
        req = make_mocked_request("GET", f"/bmk/design_patch/state?root={syn_key}")
        r = await board.state_handler(req)
        d = json.loads(r.body.decode("utf-8"))
        check(r.status == 200 and d["root_key"] == syn_key, f"state_handler 직접 {r.status}")
        req = make_mocked_request("GET", f"/bmk/design_patch/state?root={syn_root}")
        r = await board.state_handler(req)
        check(r.status == 404, "경로를 root 로 → 404")
        req = make_mocked_request("GET", "/bmk/design_patch/file?root=%s&rel=..%%2Fdesign_patch.json" % syn_key)
        r = await board.file_handler(req)
        check(r.status == 400, f"file_handler 순회 → 400 ({r.status})")
        req = make_mocked_request("GET", "/bmk/design_patch/board/x.py", match_info={"name": "x.py"})
        r = await board.board_static(req)
        check(r.status == 404, "board_static 화이트리스트 밖 → 404")
        req = make_mocked_request("GET", "/bmk/design_patch/board/", match_info={})
        r = await board.board_static(req)
        check(r.status == 200 and r.content_type == "text/html", "board_static 기본 = board.html")


    def b16_focus_race(root):
        section("B16 초점 박스: 확인 뒤 사라진 자동 마스크(Analyze 의 derived 정리와 경합)는 없는 것으로 (CR-4)")
        w, h = 64, 48
        a = np.zeros((h, w), np.uint8)
        a[10:20, 30:40] = 255
        rels = []
        for i in range(2):
            a[0, i] = 1  # 파일마다 다른 내용(메모 키 = 파일 목록)
            rels.append(store.save_png_ca(root, "derived", a.copy(), prefix="race_"))
        crop = {"rect": [0, 0, w, h]}
        check(board.focus_box(root, crop, [{"automask": rels[0], "reg": {}}]) != [0, 0, w, h], "준비: 마스크가 있으면 크롭보다 작은 초점")
        orig = board._read_l

        def vanish(path):  # 존재 확인과 읽기 사이에 다른 스레드가 지운 것과 같다
            os.remove(path)
            return orig(path)

        board._read_l = vanish  # 이 블록 안에서만
        try:
            box = board.focus_box(root, crop, [{"automask": rels[1], "reg": {}}])
            check(box == [0, 0, w, h], f"읽기 직전 삭제 → 예외 없이 크롭 전체 {box}")
        except OSError as e:
            check(False, f"focus_box 가 {type(e).__name__} 를 냄(Review 실패·state 500)")
        finally:
            board._read_l = orig


    async def b15_real(h: Http, key, reg):
        root = reg
        section("B15 실데이터 상태·썸네일 (reg_copy)")
        t0 = time.perf_counter()
        st, s = await h.jget("/bmk/design_patch/state", root=key)
        dt = time.perf_counter() - t0
        check(st == 200 and len(s["targets"]) == len(REG_CROPS), f"reg state {st} 타깃 {len(s['targets'])}")
        note(f"reg_copy state(첫 호출, 초점 계산 포함) {dt * 1000:.0f}ms, 본문 {len(json.dumps(s, ensure_ascii=False)) // 1024}KB")
        t0 = time.perf_counter()
        await h.jget("/bmk/design_patch/state", root=key)
        note(f"reg_copy state(두 번째) {(time.perf_counter() - t0) * 1000:.0f}ms")
        by = {t["crop_id"]: t for t in s["targets"]}
        t002 = by["002_388984"]
        check(t002["pick_keys"] == ["h_7e2538444e6c", "h_c74f68a0a04f"] and t002["chips"]["picked"] == 2,
              f"M1 picks 2개 → slice 1·2 {t002['pick_keys']}")
        check(any(c["reframed"] for c in t002["candidates"]) and t002["chips"]["reframed"] >= 1, "정합 적용 → reframed 칩")
        check(all(c["gates"] is not None for c in t002["candidates"]), "분석 있음 → gates")
        t003 = by["003_aa1e8a"]
        skipped = [c for c in t003["candidates"] if c["analysis"] == "skipped"]
        check(len(skipped) == 1, "부분 패치 = 분석 생략")
        t030 = by["030_d8449c"]
        run = [c for c in t030["candidates"] if c["origin"] == "run"]
        check(len(run) == 1 and run[0]["mock"] and run[0]["backend"] == "mock" and run[0]["cell_key"] == t030["cells"][0]["cell_key"]
              and run[0]["rep"] == 0 and t030["chips"]["mock"] == 1, f"mock run 후보 → MOCK·cell_key·rep {run[:1]}")
        check(t030["refs"] == [], "입력 1장 작업 → 레퍼런스 없음")
        t010 = by["010_22d16b"]
        check(t010["candidates"] == [] and t010["focus_box"] == [0, 0] + t010["size"] and t010["cells"], "후보 없는 타깃도 표시")
        check(t030["cells"][0]["pending"] == 0 and t010["pending_calls"] == 1 and s["pending_calls"] == len(REG_CROPS) - 1,
              f"원장 r0.done → 030 대기 0, 나머지 1 (전체 {s['pending_calls']}, runner {'있음' if board._runner() else '없음'})")
        pend, _pre = board._ledger_pending(reg, store.load_manifest(reg)), None
        check(pend[t030["cells"][0]["cell_key"]] == 0 and sum(pend.values()) == len(REG_CROPS) - 1, f"원장 대체 계산도 같음 {pend}")
        check(any(t["focus_box"] != [0, 0] + t["size"] for t in s["targets"]), "실데이터 초점 박스는 크롭보다 작음(확대)")
        focus_rule_check(root, s)
        return s


    async def run_all():
        shutil.rmtree(BASE, ignore_errors=True)
        BASE.mkdir(parents=True)
        os.environ.pop("BMK_DP_MOCK_API", None)
        b0_import()
        syn = build_syn()
        syn_key = store.register_root(syn)
        reg = build_reg()
        reg_key = store.register_root(reg) if reg else None
        if reg is None:
            skip(f"M1 회귀 프로젝트 없음({REG_SRC}) → reg_copy 테스트 건너뜀")
        await b1_routes(syn_key)
        routes = web.RouteTableDef()
        board.register_routes(routes)
        app = web.Application()
        app.add_routes(routes)
        async with TestClient(TestServer(app)) as client:
            h = Http(client)
            await b2_static(h, syn_key)
            await b3_roots(h, syn_key, syn)
            s = await b4_state(h, syn_key, syn)
            await b5_file(h, syn_key, syn, s)
            await b6_pick(h, syn_key, syn, s)
            await b7_reject(h, syn_key, syn, s)
            await b8_reroll(h, syn_key, syn, s)
            await b9_drain(h, syn_key, syn)
            st, s = await h.jget("/bmk/design_patch/state", root=syn_key)
            await b10_thumbs(h, syn_key, syn, s, "syn")
            await b11_concurrency(h, syn_key, syn, s)
            if reg:
                rs = await b15_real(h, reg_key, reg)
                await b10_thumbs(h, reg_key, reg, rs, "reg")
                await b13_prebuild(reg, "reg")
                await b13b_pref(h, reg_key, reg)
            await b13_prebuild(syn, "syn")
        b12_static_js()
        await b14_direct(syn_key, syn)
        b16_focus_race(syn)


    asyncio.run(run_all())


# ═════════════════════════════════════════════════════════════════
# M2-J. js/bmk_design_patch.js 시나리오 — node 로 실제 파일을 가짜 app/api 와 함께 돌린다(브라우저·서버 없음).
#   import 두 줄만 globalThis.__bmk 로 바꾼다. queuePrompt 가짜는 프론트처럼 프롬프트(approve 값)를 만든 뒤 afterQueued 를 부른다.
# ═════════════════════════════════════════════════════════════════
_JS_HARNESS = r"""
import { pathToFileURL } from "node:url";

const MOD = pathToFileURL(process.argv[2]).href;
const ROOT_KEY = "abcdef012345";
const flush = async (n = 6) => { for (let i = 0; i < n; i++) await new Promise((r) => setTimeout(r, 0)); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const windowListeners = {};
globalThis.window = {
    location: { origin: "http://127.0.0.1:8189" },
    addEventListener(name, fn) { (windowListeners[name] ??= []).push(fn); },
    open() { return {}; },
    confirm() { return true; },
};

async function setup(name) {
    const env = { toasts: [], queued: [], confirms: 0, listeners: {}, nodes: [], processingQueue: false, failSubmit: false,
                  drainRunning: true };
    windowListeners.message = [];
    const app = {
        get processingQueue() { return env.processingQueue; },
        extensionManager: {
            toast: { add: (t) => env.toasts.push(`${t.severity}: ${t.detail}`) },
            dialog: { confirm: async () => { env.confirms++; return true; } },
        },
        rootGraph: { nodes: env.nodes },
        registerExtension(ext) { env.ext = ext; },
        async queuePrompt(_num, _batch, opts) {
            if (env.processingQueue) return false;
            const approve = {};
            for (const n of env.nodes) {
                const w = n.widgets.find((x) => x.name === "approve");
                if (w) approve[String(n.id)] = w.value;
            }
            if (env.failSubmit) throw new Error("submit failed");
            env.queued.push({ ids: opts.queueNodeIds, approve });
            for (const n of env.nodes) for (const w of n.widgets) w.afterQueued?.({ isPartialExecution: true });
            return true;
        },
    };
    const api = {
        addEventListener(evt, fn) { (env.listeners[evt] ??= []).push(fn); },
        async fetchApi() { return { ok: true, status: 200, json: async () => ({ drain: env.drainRunning, running: env.drainRunning }) }; },
        fileURL: (p) => p,
    };
    globalThis.__bmk = { app, api };
    await import(`${MOD}?s=${name}`);
    env.ext.setup();
    env.emit = (evt, detail) => { for (const fn of env.listeners[evt] ?? []) fn({ detail }); };
    const types = {};
    for (const cls of ["BMKDesignPatchRun", "BMKDesignPatchReview"]) {
        const T = function () {};
        env.ext.beforeRegisterNodeDef(T, { name: cls });
        types[cls] = T;
    }
    env.add = (cls, id) => {
        const n = new types[cls]();
        Object.assign(n, {
            id, comfyClass: cls, mode: 0, properties: {}, size: [240, 120], widgets: [],
            addWidget(type, wname, value, cb, opts) {
                const w = { type, name: wname, value, callback: cb, options: opts };
                this.widgets.push(w);
                return w;
            },
            addProperty(p, v) { this.properties[p] = v; },
            findInputSlot() { return -1; },
            isInputConnected() { return false; },
            setDirtyCanvas() {},
            computeSize() { return [240, 120]; },
            setSize(s) { this.size = s; },
        });
        if (cls === "BMKDesignPatchRun") n.widgets.push({ name: "approve", value: "" });
        n.onNodeCreated();
        env.nodes.push(n);
        return n;
    };
    env.click = (n, wname) => n.widgets.find((w) => w.name === wname).callback();
    env.approve = (n) => n.widgets.find((w) => w.name === "approve");
    env.label = (n) => n.widgets.find((w) => w.name === "bmk_dp_approve").label;
    env.exec = (n, e) => n.onExecuted({ bmk_dp_run: [e] });
    env.wave = (pid, remaining, e) => {  // 이 탭이 넣은 프롬프트 pid 가 wave 로 끝남
        env.emit("bmk.dp.wave", { node: "4", root_key: ROOT_KEY, remaining });
        env.exec(env.nodes[0], e);
        env.emit("execution_success", { prompt_id: pid });
    };
    return env;
}

const entry = (o) => ({ est_usd: [0.1, 0.2], approved_now: false, root_key: ROOT_KEY, backend: "mock", ...o });
const out = {};

for (const [name, cont] of [["s3", ""], ["s3_old_server", undefined], ["s3_ok", "7f75252c3f38"]]) {
    const env = await setup(name);
    const run = env.add("BMKDesignPatchRun", 4);
    env.click(run, "bmk_dp_auto");
    env.emit("execution_start", { prompt_id: "P1" });
    const e = entry({ pending_hash: name === "s3_ok" ? "7f75252c3f38" : "fea99728c870", pending: 2 });
    if (cont !== undefined) e.continuation_hash = cont;
    env.wave("P1", 2, e);
    await flush();
    out[name] = { queued: env.queued.map((q) => q.approve["4"]), approve: env.approve(run).value, toasts: env.toasts };
}

{
    const env = await setup("s5");
    const run = env.add("BMKDesignPatchRun", 4);
    const rev = env.add("BMKDesignPatchReview", 6);
    rev.onExecuted({ bmk_dp_board: [{ root_key: ROOT_KEY, url: "/x" }] });
    env.approve(run).value = "cdeb62c7f246";
    for (const fn of windowListeners.message) fn({ origin: window.location.origin, data: { type: "bmk-dp-queue", rootKey: ROOT_KEY } });
    await sleep(950);
    await flush();
    out.s5 = { queued: env.queued.map((q) => ({ ids: q.ids, approve: q.approve["4"] })), approve: env.approve(run).value };
}

{
    const env = await setup("wsu1");
    const run = env.add("BMKDesignPatchRun", 4);
    env.click(run, "bmk_dp_auto");
    env.emit("execution_start", { prompt_id: "P1" });
    env.wave("P1", 3, entry({ pending_hash: "", pending: 0, continuation_hash: "" }));
    await flush();
    const probe = env.queued.map((q) => q.approve["4"]);
    env.emit("execution_start", { prompt_id: "P2" });
    env.emit("execution_interrupted", { prompt_id: "P2" });
    env.emit("execution_start", { prompt_id: "P3" });
    env.exec(run, entry({ pending_hash: "NEWHASH00000", pending: 2, continuation_hash: "" }));
    env.emit("execution_success", { prompt_id: "P3" });
    await flush();
    out.wsu1 = { probe, queued: env.queued.length, approve: env.approve(run).value };
    const env2 = await setup("wsu1c");
    const run2 = env2.add("BMKDesignPatchRun", 4);
    env2.click(run2, "bmk_dp_auto");
    env2.emit("execution_start", { prompt_id: "P1" });
    env2.wave("P1", 2, entry({ pending_hash: "", pending: 0, continuation_hash: "" }));
    await flush();
    env2.emit("execution_start", { prompt_id: "P2" });
    env2.exec(run2, entry({ pending_hash: "", pending: 0, continuation_hash: "" }));
    env2.emit("execution_success", { prompt_id: "P2" });
    await flush();
    out.wsu1c = { queued: env2.queued.length, warns: env2.toasts.filter((t) => t.startsWith("warn")) };
}

{
    const env = await setup("wsu3");
    const run = env.add("BMKDesignPatchRun", 4);
    env.exec(run, entry({ pending_hash: "H1aaaaaaaaaa", pending: 2 }));
    await env.click(run, "bmk_dp_approve");
    const a = { queued: env.queued.map((q) => q.approve["4"]), after: env.approve(run).value,
                hookLeft: "afterQueued" in env.approve(run), confirms: env.confirms };
    env.failSubmit = true;
    await env.click(run, "bmk_dp_approve");
    const b = { queued: env.queued.length, after: env.approve(run).value };
    env.failSubmit = false;
    env.processingQueue = true;
    await env.click(run, "bmk_dp_approve");
    const c = { queued: env.queued.length, after: env.approve(run).value, warned: env.toasts.some((t) => t.includes("다른 큐 제출")) };
    env.processingQueue = false;
    env.exec(run, entry({ pending_hash: "H2bbbbbbbbbb", pending: 1, backend: "comfy_org" }));
    await env.click(run, "bmk_dp_approve");
    const d = { confirms: env.confirms, last: env.queued.at(-1).approve["4"], after: env.approve(run).value };
    out.wsu3 = { a, b, c, d };
}

{
    const env = await setup("wsu5");
    const run = env.add("BMKDesignPatchRun", 4);
    const fresh = env.label(run);
    env.exec(run, entry({ pending_hash: "", pending: 0 }));
    const done = env.label(run);
    await env.click(run, "bmk_dp_approve");
    out.wsu5 = { fresh, done, toast: env.toasts.at(-1), queued: env.queued.length };
}

{
    const env = await setup("wsu2");
    const run = env.add("BMKDesignPatchRun", 4);
    env.click(run, "bmk_dp_auto");
    run.properties.bmk_dp_root_key = ROOT_KEY;
    env.emit("execution_start", { prompt_id: "P1" });
    env.drainRunning = false;  // 서버가 run_calls 를 막 끝내 Drain 이 닿지 않음
    await env.click(run, "bmk_dp_drain");
    env.wave("P1", 2, entry({ pending_hash: "Hxxxxxxxxxxx", pending: 2, continuation_hash: "Hxxxxxxxxxxx" }));
    await flush();
    const w1 = { queued: env.queued.length, told: env.toasts.some((t) => t.includes("Drain 요청으로 멈춘")) };
    env.emit("execution_start", { prompt_id: "P2" });
    env.wave("P2", 2, entry({ pending_hash: "Hyyyyyyyyyyy", pending: 2, continuation_hash: "Hyyyyyyyyyyy" }));
    await flush();
    const queuedCont = env.queued.length;
    await env.click(run, "bmk_dp_drain");
    out.wsu2 = { w1, queuedCont, last: env.toasts.at(-1) };
}

console.log(JSON.stringify(out));
"""


def m2_js_unit():
    section("M2-J js/bmk_design_patch.js 시나리오 (node + 가짜 app/api): S3·S5·WSU-1·WSU-2·WSU-3·WSU-5")
    node = shutil.which("node")
    if not node:
        skip("node 없음 → JS 시나리오 생략")
        return
    out = OUT_ROOT / "js"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    src = (PKG / "js" / "bmk_design_patch.js").read_text(encoding="utf-8")
    src, n_imp = re.subn(r'^import \{ (app|api) \} from "\.\./\.\./scripts/(app|api)\.js";\r?\n', "", src, flags=re.M)
    check(n_imp == 2, f"JS import 두 줄(app·api)만 바꿈 ({n_imp})")
    (out / "dp.mjs").write_text("const { app, api } = globalThis.__bmk;\n" + src, encoding="utf-8")
    (out / "harness.mjs").write_text(_JS_HARNESS, encoding="utf-8")
    r = subprocess.run([node, str(out / "harness.mjs"), str(out / "dp.mjs")], capture_output=True, text=True,
                       encoding="utf-8", timeout=60)
    if r.returncode != 0 or not r.stdout.strip():
        check(False, f"node 하네스 실패({r.returncode}): {r.stderr.strip()[-800:]}")
        return
    res = json.loads(r.stdout.strip().splitlines()[-1])
    s3, s3o, ok = res["s3"], res["s3_old_server"], res["s3_ok"]
    check(s3["queued"] == [] and s3["approve"] == "" and any("승인하지 않은" in t for t in s3["toasts"]),
          f"S3: 대기 2 = wave 잔여 2 여도 continuation_hash 가 다르면 자동 승인하지 않고 멈춤 {s3}")
    check(s3o["queued"] == [] and s3o["approve"] == "", "S3: continuation_hash 가 없는 옛 서버 응답도 멈춤(안전한 쪽)")
    check(ok["queued"] == ["7f75252c3f38"] and ok["approve"] == "", f"S3: 승인받은 wave(continuation = pending)는 이어서 승인 {ok}")
    check(res["s5"]["queued"] == [{"ids": ["6"], "approve": ""}] and res["s5"]["approve"] == "",
          f"S5: 보드 큐 요청은 Run 의 남은 approve 를 비우고 Review 만 큐 {res['s5']}")
    w1 = res["wsu1"]
    check(w1["probe"] == [""] and w1["queued"] == 1 and w1["approve"] == "",
          f"WSU-1: 무승인 이어가기 큐가 취소돼도 나중 Run 실행이 새 해시를 자동 승인하지 않음 {w1}")
    check(res["wsu1c"]["queued"] == 1 and res["wsu1c"]["warns"] == [], f"WSU-1: 이어가기가 정상으로 끝나면 거짓 경고 없음 {res['wsu1c']}")
    a, b, c, d = (res["wsu3"][k] for k in "abcd")
    check(a["queued"] == ["H1aaaaaaaaaa"] and a["after"] == "" and not a["hookLeft"] and a["confirms"] == 0,
          f"WSU-3: 버튼 승인은 그 프롬프트에만 — 제출 뒤 approve 비움, 훅 제거, MOCK 확인 창 없음 {a}")
    check(b["queued"] == 1 and b["after"] == "", f"WSU-3: 제출 실패해도 approve 비움 {b}")
    check(c["queued"] == 1 and c["after"] == "" and c["warned"], f"WSU-3: 다른 제출 처리 중이면 승인 거절 {c}")
    check(d["confirms"] == 1 and d["last"] == "H2bbbbbbbbbb" and d["after"] == "", f"WSU-3: 유료는 확인 창 후 같은 규칙 {d}")
    w5 = res["wsu5"]
    check("먼저 큐 실행" in w5["fresh"] and w5["done"] == "승인할 호출 없음" and "승인할 호출이 없습니다" in w5["toast"]
          and w5["queued"] == 0, f"WSU-5: 실행 결과가 있고 승인할 것이 없으면 '승인할 호출 없음' {w5}")
    w2 = res["wsu2"]
    check(w2["w1"] == {"queued": 0, "told": True}, f"WSU-2: Drain 을 누른 실행은 wave 로 끝나도 이어가지 않음 {w2['w1']}")
    check(w2["queuedCont"] == 1 and "시작 전" in w2["last"], f"WSU-2: 이어가기 큐가 시작 전이면 Drain 이 큐에서 지우라고 안내 {w2['last']}")


# ═════════════════════════════════════════════════════════════════
# M2-N. 노드 수준 (합성 프로젝트, mock — 참조 폴더 없이도 실행)
# ═════════════════════════════════════════════════════════════════
MODEL25 = "gpt-image-2.5-sunburst"


def _comfy_cpu():
    """comfy 를 CPU 로 import 하게 한다(사용자 GPU 를 건드리지 않게). comfy.model_management 첫 import 전에 불러야 효과."""
    if str(COMFY) not in sys.path:
        sys.path.insert(0, str(COMFY))
    import comfy.options
    comfy.options.enable_args_parsing(False)
    import comfy.cli_args
    comfy.cli_args.args.cpu = True


def _dp_package():
    """노드 모듈을 패키지(_bmk_dp_pkg)로 import — 보조 모듈은 상대 import(그 패키지의 store·runner·board 를 공유)."""
    _comfy_cpu()
    if "_bmk_dp_pkg" not in sys.modules:
        pkg = types.ModuleType("_bmk_dp_pkg")
        pkg.__path__ = [str(PKG)]
        sys.modules["_bmk_dp_pkg"] = pkg
    return importlib.import_module("_bmk_dp_pkg.bmk_design_patch")


class _FakeServer:
    """PromptServer.instance 대역: send_sync / send_progress_text 기록, routes 테이블."""

    def __init__(self, routes):
        self.client_id = "test-client"
        self.routes = routes
        self.events: list = []
        self.texts: list = []

    def send_sync(self, event, data, sid=None):
        self.events.append((event, data, sid))

    def send_progress_text(self, text, node_id, sid=None):
        self.texts.append((node_id, text))


@contextlib.contextmanager
def _prompt_server(fake):
    """이 블록 안에서만 server.PromptServer.instance = fake (server 모듈이 없으면 빈 모듈을 잠깐 둔다). 끝나면 되돌린다."""
    mod = sys.modules.get("server")
    added = mod is None
    if added:
        mod = types.ModuleType("server")
        mod.PromptServer = type("PromptServer", (), {})
        sys.modules["server"] = mod
    had = "instance" in vars(mod.PromptServer)
    old = vars(mod.PromptServer).get("instance")
    mod.PromptServer.instance = fake
    try:
        yield
    finally:
        if had:
            mod.PromptServer.instance = old
        else:
            del mod.PromptServer.instance
        if added:
            del sys.modules["server"]


def _ledger_reqs(root) -> int:
    d = Path(root) / "cands"
    return sum(1 for p in d.glob("*/r*.req.json")) if d.is_dir() else 0


def m2_node_unit():
    dp = _dp_package()
    import folder_paths  # noqa: E402  (ComfyUI 루트)
    import comfy.model_management as mm
    from aiohttp import web

    st, rn, bd = dp.store, dp.runner, dp.board  # 노드와 같은 모듈(잠금·레지스트리·실행 레지스트리 공유)
    NODES2 = OUT_ROOT / "m2_nodes"
    shutil.rmtree(NODES2, ignore_errors=True)
    NODES2.mkdir(parents=True)
    folder_paths.set_output_directory(str(NODES2 / "output"))
    mock_file = NODES2 / "bmk_design_patch" / "_MOCK_API"
    env_mock = os.environ.pop(rn.MOCK_ENV, None)

    def set_mock(**opts):
        mock_file.parent.mkdir(parents=True, exist_ok=True)
        mock_file.write_text(json.dumps(opts), encoding="utf-8")

    RUN_, REV_, PRE = dp.BMKDesignPatchRun(), dp.BMKDesignPatchReview(), dp.BMKDesignPatchPrepare()
    ANA, COM = dp.BMKDesignPatchAnalyze(), dp.BMKDesignPatchCompose()

    def run_node(h, approve="", **kw):
        args = dict(max_new_calls=64, concurrency=4, wave_minutes=25, retry_failed=False, retry_orphans=False, only="")
        args.update(kw)
        out = asyncio.run(RUN_.run(h, approve, unique_id="77", **args))
        check(out["ui"]["text"] == [out["result"][1]], "Run ui text = report 출력")
        return out["result"], out["ui"]["bmk_dp_run"][0]

    def rev(root):
        return st.load_manifest(root)["rev"]

    def run_cands(root):
        return [c for c in st.load_manifest(root)["candidates"] if c["origin"] == "run"]

    try:
        section("M2-N0 Run / Review / Prepare 정의 · 보드 라우트 등록(가짜 서버)")
        it = dp.BMKDesignPatchRun.INPUT_TYPES()
        req = it["required"]
        check(list(req) == ["project", "approve", "max_new_calls", "concurrency", "wave_minutes", "retry_failed",
                            "retry_orphans", "only"], f"Run 입력 {list(req)}")
        lim = {k: (req[k][1]["default"], req[k][1]["min"], req[k][1]["max"]) for k in ("max_new_calls", "concurrency", "wave_minutes")}
        check(lim == {"max_new_calls": (64, 1, 1000), "concurrency": (4, 1, 8), "wave_minutes": (25, 1, 60)}, f"Run 숫자 위젯 {lim}")
        check(req["approve"][0] == "STRING" and req["approve"][1]["default"] == "" and not req["approve"][1].get("multiline")
              and req["only"][0] == "STRING" and req["only"][1]["default"] == ""
              and all(req[k][0] == "BOOLEAN" and req[k][1]["default"] is False for k in ("retry_failed", "retry_orphans")),
              "approve·only 한 줄 STRING, retry_* BOOLEAN False")
        check(it["hidden"] == {"auth_token": "AUTH_TOKEN_COMFY_ORG", "api_key": "API_KEY_COMFY_ORG",
                               "usage_source": "COMFY_USAGE_SOURCE", "unique_id": "UNIQUE_ID"}, "Run hidden 입력")
        check(not any("seed" in k for k in req), "seed 이름 위젯 없음(randomize 재과금 방지)")
        check(inspect.iscoroutinefunction(dp.BMKDesignPatchRun.run) and inspect.iscoroutinefunction(dp.BMKDesignPatchReview.run)
              and dp.BMKDesignPatchRun.OUTPUT_NODE and dp.BMKDesignPatchReview.OUTPUT_NODE
              and "IS_CHANGED" not in vars(dp.BMKDesignPatchRun) and "IS_CHANGED" not in vars(dp.BMKDesignPatchReview),
              "Run·Review: async, OUTPUT_NODE, IS_CHANGED 없음")
        check(list(dp.BMKDesignPatchReview.INPUT_TYPES()["required"]) == ["project"], "Review 입력 = project 만(위젯 없음)")
        pre_in = dp.BMKDesignPatchPrepare.INPUT_TYPES()["required"]
        cpc = pre_in["calls_per_cell"]
        check(list(pre_in)[-1] == "calls_per_cell" and cpc[0] == "INT" and (cpc[1]["default"], cpc[1]["min"], cpc[1]["max"]) == (1, 1, 8),
              "Prepare calls_per_cell(INT 1, 1–8) 은 위젯 맨 뒤(widgets_values 위치 호환)")
        check(dp._server() is None, "서버 없음(테스트) → _server() None → import 때 라우트 등록 건너뜀")
        fake = _FakeServer(web.RouteTableDef())
        with _prompt_server(fake):
            check(dp._server() is fake, "PromptServer.instance 를 읽음")
            dp._register_board_routes()
            n1 = len(fake.routes)
            dp._register_board_routes()
        paths = {r.path for r in fake.routes}
        check(n1 == 10 and len(fake.routes) == 10 and {"/bmk/design_patch/state", "/bmk/design_patch/drain",
                                                       "/bmk/design_patch/board/"} <= paths,
              f"보드 라우트 10개, 두 번 불러도 한 번 ({n1}, {len(fake.routes)})")
        check(dp._server() is None, "가짜 서버는 블록 밖에서 되돌림")

        section("M2-N1 Prepare calls_per_cell · Run 과 같은 승인 해시")
        base = texture(384, 512, 31)
        rects = [("001_왼쪽", [32, 32, 160, 160]), ("002_가운데", [192, 64, 320, 192]), ("003_오른쪽", [352, 160, 480, 288])]
        h, root = _fx_project(dp, NODES2, "m2n", rects, base)
        set_mock(delay_s=0.02)
        h, _s, prep = PRE.run(h, MODEL25, "high", "user_k", "V1", 1, "at", 1)
        m = st.load_manifest(root)
        ph = rn.split_approval(rn.plan_calls(m, root), "", backend=rn.BACKEND_MOCK)["pending_hash"]
        check(len(m["jobs"]) == 3 and all(j["reps"] == 1 and j["size"] == [2048, 2048] for j in m["jobs"])
              and not any("status" in j for j in m["jobs"]), "jobs 3, reps 1, (M1 의 status 필드 없음)")
        check(f"승인 해시 {ph}" in prep and "승인 필요 3건" in prep and "$" in prep and "셀당 호출 1" in prep,
              "Prepare 보고: Run 과 같은 함수의 해시·개수·USD")
        h2, _s, prep2 = PRE.run(h, MODEL25, "high", "user_k", "V1", 1, "at", 2)
        check(all(j["reps"] == 2 for j in st.load_manifest(root)["jobs"]) and "승인 필요 6건" in prep2,
              "calls_per_cell 2 → jobs[].reps 2, 승인 필요 6")
        h, _s, _r = PRE.run(h2, MODEL25, "high", "user_k", "V1", 1, "at", 1)
        check(PRE.run(h, MODEL25, "high", "user_k", "V1", 1, "at")[0]["rev"] == h["rev"],
              "calls_per_cell 생략(Python 기본 1) = 같은 결과, rev 불변")

        section("M2-N2 Run: 드라이런 0 호출 → 승인 N 호출 → 다시 0 호출")
        rev0 = rev(root)
        (hd, rep_d, grid_d), e = run_node(h)
        check(_ledger_reqs(root) == 0 and e["pending"] == 3 and e["pending_hash"] == ph and e["approved_now"] is False
              and e["backend"] == "mock" and e["est_usd"][0] > 0 and e["est_usd"][1] >= e["est_usd"][0],
              f"드라이런: 과금 0, ui 해시·개수·USD {e}")
        check(e["root_key"] == st.root_key(root) and st.resolve_board_root(e["root_key"]) == root,
              "Run 이 보드 레지스트리에 등록(Review 전에도 Drain 키가 유효)")
        check("드라이런" in rep_d and "approve 가 비어 있음" in rep_d and ph in rep_d and "MOCK" in rep_d, "드라이런 보고")
        check(tuple(grid_d.shape) == (1, 1, 1, 3) and hd["rev"] == rev0 == rev(root), "드라이런: 1x1 격자, 매니페스트 불변")
        ex_in = dp.BMKDesignPatchExportPSD.INPUT_TYPES()
        check("skip_empty" not in ex_in["required"] and ex_in["optional"]["skip_empty"][1]["default"] is True,
              "Export skip_empty 는 optional(기본 True) — 예전 워크플로·API 프롬프트 호환")
        p_e, ui_e = _run_export(dp, hd, "m2empty", "pixel", "hidden_all", "raw", "auto", True, True, skip_empty=True)
        out_e = Path(folder_paths.get_output_directory()) / "design_patch"
        check(p_e == "" and "내보낼 후보 없음" in ui_e[0] and not (out_e.is_dir() and any(out_e.rglob("m2empty*"))),
              "후보 0(드라이런 직후) → Export 가 빈 PSD 를 쓰지 않음(스모크 테스트에서 72.9MB 빈 PSD 가 생기던 문제)")
        (_h, rep_w, _g), e = run_node(h, "0" * 12)
        check(_ledger_reqs(root) == 0 and "≠" in rep_w and e["pending_hash"] == ph, "틀린 approve → 과금 0, 이유 보고")
        (ha, rep_a, grid_a), e = run_node(h, f" {ph} ")
        m = st.load_manifest(root)
        rc = run_cands(root)
        check(_ledger_reqs(root) == 3 and len(rc) == 3 and all(c["origin_info"]["backend"] == "mock" for c in rc)
              and sum(c["status"] == "done" for c in m["calls"]) == 3 and m["approvals"].get(ph, {}).get("count") == 3,
              "승인 → 3 호출(mock), 후보 3, calls done 3, approvals 기록")
        check(e["pending"] == 0 and e["pending_hash"] == "" and e["approved_now"] is True and "발사 3" in rep_a,
              f"실행 뒤 ui: 남은 승인 0 {e}")
        check(grid_a.ndim == 4 and grid_a.shape[1] > 1 and grid_a.shape[3] == 3 and ha["rev"] == m["rev"] > rev0,
              f"새 후보 격자 {tuple(grid_a.shape)}, 핸들 rev 갱신")
        rev1 = m["rev"]
        (_h, rep_b, grid_b), e = run_node(ha, ph)
        check(_ledger_reqs(root) == 3 and "할 일 없음" in rep_b and rev(root) == rev1 and tuple(grid_b.shape) == (1, 1, 1, 3),
              "같은 승인으로 다시 → 0 호출, rev 불변")

        section("M2-N2b MOCK 드라이런 해시는 _MOCK_API 를 지운 뒤의 유료 실행을 승인하지 않음 (S4 / CR-2)")
        hs, roots = _fx_project(dp, NODES2, "m2s4", rects[:2], base)
        hs, _s, prep_s = PRE.run(hs, MODEL25, "high", "user_k", "V1", 1, "at", 1)
        _r, e = run_node(hs)
        hm = e["pending_hash"]
        check(e["backend"] == "mock" and hm and f"승인 해시 {hm} (MOCK)" in prep_s, f"MOCK: Prepare·Run 해시 같음 + (MOCK) 표시 {hm}")
        mock_file.unlink()
        try:
            check(not rn.mock_enabled(roots), "_MOCK_API 삭제 → 유료 백엔드")
            # 로그인 정보 없이 부른다: 회귀로 해시가 맞더라도 ComfyOrgBackend 가 sync_op 전에 401 을 내 네트워크에 닿지 않음
            (_h, rep_s, _g), e2 = run_node(hs, hm)
            check(_ledger_reqs(roots) == 0 and e2["backend"] == rn.BACKEND_COMFY_ORG and e2["approved_now"] is False
                  and e2["pending_hash"] not in ("", hm) and e2["pending"] == 2 and "comfy.org(유료)" in rep_s and "≠" in rep_s,
                  f"옛 MOCK 해시 → 과금 0, 유료 드라이런의 새 해시 {e2}")
            _h, _s, prep_s2 = PRE.run(hs, MODEL25, "high", "user_k", "V1", 1, "at", 1)
            check(f"승인 해시 {e2['pending_hash']} |" in prep_s2, "유료 모드: Prepare 해시 = Run 드라이런 해시, (MOCK) 없음")
        finally:
            set_mock(delay_s=0.02)

        section("M2-N3 n 변경은 새 승인 · 보드 재굴림은 사전 승인")
        h, _s, _r = PRE.run(ha, MODEL25, "high", "user_k", "V1", 1, "at", 2)
        _r3, e = run_node(h)
        ph2 = e["pending_hash"]
        check(e["pending"] == 3 and ph2 not in ("", ph), "calls_per_cell 2 → r1 3건 새 해시")
        h, _s, _r = PRE.run(h, MODEL25, "high", "user_k", "V1", 2, "at", 2)
        (_h, rep_n, _g), e = run_node(h, ph2)
        ph3 = e["pending_hash"]
        check(_ledger_reqs(root) == 3 and e["pending"] == 3 and ph3 not in ("", ph2) and "≠" in rep_n,
              "n 1→2(같은 call_id): 옛 해시로는 과금 0, 새 해시")
        (h, rep_n2, _g), e = run_node(h, ph3)
        check(_ledger_reqs(root) == 6 and len(run_cands(root)) == 9 and "발사 3" in rep_n2, "새 해시 승인 → 3 호출 × n2 = 후보 6")
        h, _s, _r = PRE.run(h, MODEL25, "high", "user_k", "V1", 2, "at", 3)
        ck0 = st.load_manifest(root)["jobs"][0]["cell_key"]
        bd.apply_reroll(root, 1, cell_key=ck0)
        (h, rep_r, _g), e = run_node(h, ph3)
        check(_ledger_reqs(root) == 7 and "발사 1" in rep_r and "승인 안 된 3건" in rep_r and e["pending"] == 3,
              "재굴림 1(사전 승인)만 과금, calls_per_cell 3 의 새 호출 3건은 승인 대기")
        check(json.loads((Path(root) / "cands" / ck0 / "r3.req.json").read_text(encoding="utf-8"))["approved_by"] == "board",
              "재굴림 호출 원장 approved_by = board(빠진 rep 중 뒤쪽)")

        section("M2-N4 wave: 이벤트·남은 것의 새 해시·이어가기 (가짜 PromptServer, wave_minutes 를 초 단위로)")
        set_mock(delay_s=0.3)
        phw = e["pending_hash"]
        fake = _FakeServer(web.RouteTableDef())
        with _prompt_server(fake):
            (h, rep_wv, _g), e = run_node(h, phw, concurrency=1, wave_minutes=0.6 / 60)
        ev = [(d, sid) for name, d, sid in fake.events if name == "bmk.dp.wave"]
        check(len(ev) == 1 and ev[0][0]["node"] == "77" and ev[0][0]["root_key"] == st.root_key(root)
              and ev[0][1] == "test-client" and 1 <= ev[0][0]["remaining"] <= 2 and ev[0][0]["remaining"] == e["pending"],
              f"bmk.dp.wave {ev}")
        check("wave" in rep_wv and e["pending_hash"] not in ("", phw), "wave 뒤 ui = 남은 승인분의 새 해시(JS 자동 이어가기용)")
        check(e["continuation_hash"] == e["pending_hash"] != "",
              "S3: 승인받은 wave 의 continuation_hash = 다시 계획한 pending_hash(JS 가 같을 때만 이어서 승인)")
        check(any(nid == "77" and "Design Patch Run" in t for nid, t in fake.texts), "진행 텍스트(send_progress_text)")
        n_left = e["pending"]
        set_mock(delay_s=0.02)
        (h, rep_c, _g), e = run_node(h, e["pending_hash"])
        check(f"발사 {n_left}" in rep_c and e["pending"] == 0 and _ledger_reqs(root) == 10, "새 해시로 이어서 나머지")
        # S3: 승인하지 않은 대기(calls_per_cell 1→2)가 있는데 보드 재굴림만 돈 wave → continuation_hash "" (JS 는 멈춤)
        h3, root3 = _fx_project(dp, NODES2, "m2s3", rects[:2], base)
        h3, _s, _r = PRE.run(h3, MODEL25, "high", "user_k", "V1", 1, "at", 1)
        (h3, _rp, _g), e = run_node(h3, run_node(h3)[1]["pending_hash"])
        h3, _s, _r = PRE.run(h3, MODEL25, "high", "user_k", "V1", 1, "at", 2)
        _r3, e = run_node(h3)
        h_unapproved = e["pending_hash"]
        bd.apply_reroll(root3, 3, cell_key=st.load_manifest(root3)["jobs"][0]["cell_key"])
        set_mock(delay_s=0.8)
        fake = _FakeServer(web.RouteTableDef())
        with _prompt_server(fake):
            (h3, rep_s3, _g), e = run_node(h3, "", concurrency=1, wave_minutes=0.5 / 60)
        ev = [d for name, d, _sid in fake.events if name == "bmk.dp.wave"]
        check(len(ev) == 1 and ev[0]["remaining"] == 2 == e["pending"] and e["pending_hash"] == h_unapproved
              and e["continuation_hash"] == "" and e["approved_now"] is False and "발사 1" in rep_s3,
              f"S3: 사전 승인만 돈 wave → 남은 2(사전 승인), ui pending 2 = 승인 안 한 해시, continuation_hash '' {ev} {e}")
        set_mock(delay_s=0.02)

        section("M2-N5 인터럽트 → InterruptProcessingException(도착분 저장, 진행 중 orphaned) · orphan 재호출은 새 승인")
        hi, rooti = _fx_project(dp, NODES2, "m2i", rects[:2], base)
        hi, _s, _r = PRE.run(hi, MODEL25, "high", "user_k", "V1", 1, "at", 1)
        c0, c1 = [j["cell_key"] for j in st.load_manifest(rooti)["jobs"]]
        set_mock(delay_s=0.02, fail_plan={f"{c0}_r0": {"interrupt": True, "delay": 0.4}})
        _r0, e = run_node(hi)
        phi = e["pending_hash"]
        mm.interrupt_current_processing(False)
        try:
            run_node(hi, phi, concurrency=2)
            check(False, "인터럽트: 예외가 나지 않음")
        except mm.InterruptProcessingException as ex:
            rep = getattr(ex, "bmk_dp_report", None) or getattr(ex.__cause__, "bmk_dp_report", None)
            check(rep is not None and rep.stopped_reason == "interrupt" and rep.done == 1 and rep.orphaned == [f"{c0}_r0"],
                  f"InterruptProcessingException + 보고(도착 1, orphaned c0) {rep and rep.text()}")
        names0 = sorted(p.name for p in (Path(rooti) / "cands" / c0).iterdir())
        check(names0 == ["r0.inflight.json", "r0.req.json"] and (Path(rooti) / "cands" / c1 / "r0.done.json").is_file(),
              f"원장: c0 inflight 남음(orphaned), c1 done {names0}")
        set_mock(delay_s=0.02)
        (_h, rep_o, _g), e = run_node(hi, phi)
        check("orphaned 1건" in rep_o and "retry_orphans" in rep_o and e["pending"] == 0 and _ledger_reqs(rooti) == 2,
              "orphaned 안내, retry_orphans 없이는 재호출 0")
        (_h, _r, _g), e = run_node(hi, phi, retry_orphans=True)
        pho = e["pending_hash"]
        check(e["pending"] == 1 and pho not in ("", phi) and not (Path(rooti) / "cands" / c0 / "r0.done.json").exists(),
              "retry_orphans: 옛 해시로는 과금 0, 새 해시")
        (_h, rep_ro, _g), e = run_node(hi, pho, retry_orphans=True)
        reqi = json.loads((Path(rooti) / "cands" / c0 / "r0.req.json").read_text(encoding="utf-8"))
        check("발사 1" in rep_ro and reqi["attempt"] == 2 and reqi["kind"] == "retry_orphan", "새 해시 승인 → orphan 재호출 attempt 2")
        # API-4: 발사 전 comfy 인터럽트 플래그 → run_calls 가 InterruptProcessingException(BaseException) 을 낸다 → 보고 로그 후 그대로
        hi2, rooti2 = _fx_project(dp, NODES2, "m2i2", rects[:2], base)
        hi2, _s, _r = PRE.run(hi2, MODEL25, "high", "user_k", "V1", 1, "at", 1)
        phi2 = run_node(hi2)[1]["pending_hash"]
        logs: list = []

        class _Grab(logging.Handler):
            def emit(self, record):
                logs.append(record.getMessage())

        grab = _Grab()
        logging.getLogger(dp.__name__).addHandler(grab)
        mm.interrupt_current_processing(True)
        try:
            run_node(hi2, phi2)
            check(False, "API-4: 인터럽트 예외가 나지 않음")
        except mm.InterruptProcessingException as ex:
            rep = getattr(ex, "bmk_dp_report", None)
            check(rep is not None and rep.launched == 0 and len(rep.remaining) == 2 and ex.__cause__ is None
                  and any("Run 취소" in s and "남은 호출 2건" in s for s in logs),
                  f"API-4: InterruptProcessingException 도 취소 보고를 로그로 남기고 그대로 raise {logs[-1:]}")
        finally:
            mm.interrupt_current_processing(False)
            logging.getLogger(dp.__name__).removeHandler(grab)
        check(_ledger_reqs(rooti2) == 0, "API-4: 발사 전 취소 → 과금 0")

        section("M2-N6 같은 호출의 retry_failed 는 첫 승인 해시로 과금되지 않음")
        hf, rootf = _fx_project(dp, NODES2, "m2f", rects[:1], base)
        hf, _s, _r = PRE.run(hf, MODEL25, "high", "user_k", "V1", 1, "at", 1)
        set_mock(delay_s=0.0, fail_plan={"*": {"error": 500}})
        _r0, e = run_node(hf)
        phf = e["pending_hash"]
        (_h, rep_f, _g), e = run_node(hf, phf)
        check("실패 1" in rep_f and len(list((Path(rootf) / "cands").glob("*/r0.failed.json"))) == 1, "500 → failed")
        set_mock(delay_s=0.0)
        (_h, rep_f2, _g), e = run_node(hf, phf, retry_failed=True)
        check(e["pending"] == 1 and e["pending_hash"] not in ("", phf) and "≠" in rep_f2
              and len(list((Path(rootf) / "cands").glob("*/r0.failed.json"))) == 1, "같은 집합이어도 재시도는 새 해시 필요")
        (_h, rep_f3, _g), e = run_node(hf, e["pending_hash"], retry_failed=True)
        check("발사 1" in rep_f3 and len(list((Path(rootf) / "cands").glob("*/r0.done.json"))) == 1, "새 해시 승인 → 재시도 done")

        section("M2-N7 Review: 레지스트리·썸네일·ui")
        out = asyncio.run(REV_.run(h))
        hb, rep_rv = out["result"]
        bu = out["ui"]["bmk_dp_board"][0]
        th_dir = Path(root) / "derived" / "thumbs"
        check(bu["root_key"] == st.root_key(root) and bu["url"] == f"/bmk/design_patch/board/?root={bu['root_key']}"
              and st.resolve_board_root(bu["root_key"]) == root, f"ui bmk_dp_board {bu}")
        check(th_dir.is_dir() and len(list(th_dir.glob("cand_*.jpg"))) >= len(run_cands(root)) and "Review: 타깃 3" in rep_rv
              and "보드:" in rep_rv and out["ui"]["text"] == [rep_rv] and hb["rev"] == rev(root),
              f"썸네일 미리 생성 {len(list(th_dir.glob('*.jpg')))}장, 보고")

        section("M2-N8 보드 탈락(rejects)은 Analyze / Compose / Export 에서 빠짐")
        m = st.load_manifest(root)
        crop0 = m["crops"][0]["id"]
        k0 = [c["key"] for c in m["candidates"] if c["crop_id"] == crop0]
        bd.apply_pick(root, crop0, "A", k0[0], "set")
        bd.apply_pick(root, crop0, "A", k0[1], "add")
        bd.apply_reject(root, k0[1], True)  # pick 에서도 빠짐
        bd.apply_reject(root, k0[2], True)
        n_all = len(m["candidates"])
        ha, _cs, rep_an = ANA.run(h, "guarded_affine", "field", 16, 8.0, 9, 3.5, "none", False)
        an_ = st.load_manifest(root)["analysis"]
        check(f"후보 {n_all - 2} " in rep_an and "보드 탈락 2 제외" in rep_an and k0[1] not in an_ and k0[2] not in an_
              and k0[0] in an_, "Analyze: 탈락 2 제외")
        hc, _img, _mk, rep_co = COM.run(ha, "auto", True, True, "auto_small_on_top")
        check(rep_co.startswith("Compose: pick 1 ") and k0[0] in rep_co and k0[1] not in rep_co, "Compose: 탈락은 pick 에서 빠짐")
        p_rej, ui_rej = _run_export(dp, hc, "m2rej", "pixel", "hidden_all", "raw", "auto", True, False)
        lay = json.loads(Path(os.path.splitext(p_rej)[0] + ".json").read_text(encoding="utf-8"))["export_layers"]
        keys = {w["key"]: w["visible"] for w in lay}
        check(keys.get(k0[0]) is True and k0[1] not in keys and k0[2] not in keys and len(keys) == n_all - 2,
              f"Export(hidden_all): 탈락 2 는 대안으로도 없음 ({len(keys)}/{n_all})")
        bd.apply_reject(root, k0[2], False)
        p_un, ui_un = _run_export(dp, hc, "m2rej", "pixel", "hidden_all", "raw", "auto", True, False)
        lay2 = json.loads(Path(os.path.splitext(p_un)[0] + ".json").read_text(encoding="utf-8"))["export_layers"]
        check(p_un != p_rej and "변경 없음" not in ui_un[0] and any(w["key"] == k0[2] and not w["visible"] for w in lay2),
              "탈락 해제 → export 해시가 바뀌어 새 PSD(숨김 대안으로 다시 들어감)")

        section("M2-N9 _commit = 3-way 병합(노드가 연 뒤의 보드 쓰기 보존, 충돌은 노드 값 + 경고)")
        r_, mo = dp._open(h)
        before = dp._snapshot(mo)
        orig = (mo["canvas"], mo["work_rect"])
        mo["canvas"], mo["work_rect"] = [111, 111], [0, 0, 5, 5]
        st.update_manifest(root, lambda d: (d.__setitem__("canvas", [222, 222]), d["rejects"].__setitem__(k0[3], True)))
        hn, conf = dp._commit(root, mo, before)
        disk = st.load_manifest(root)
        check(conf == ["매니페스트 병합 충돌(이 노드의 값으로 저장): canvas"] and disk["canvas"] == [111, 111]
              and disk["work_rect"] == [0, 0, 5, 5] and disk["rejects"].get(k0[3]) is True, f"충돌 보고 + 보드 쓰기 보존 {conf}")
        check(mo == disk and hn["rev"] == disk["rev"], "m 은 저장된 내용(rev 포함)으로 바뀜")
        r_, mo2 = dp._open(h)
        hn2, conf2 = dp._commit(root, mo2, dp._snapshot(mo2))
        check(conf2 == [] and hn2["rev"] == disk["rev"] == rev(root), "바뀐 것 없음 → 저장 안 함(rev 그대로)")
        st.update_manifest(root, lambda d: (d.update(canvas=orig[0], work_rect=orig[1]), d["rejects"].pop(k0[3], None)))
    finally:
        mock_file.unlink(missing_ok=True)
        if env_mock is not None:
            os.environ[rn.MOCK_ENV] = env_mock


# ═════════════════════════════════════════════════════════════════
# M2-C. 노드 체인 — 실제 로더 + execution.PromptExecutor (서버 포트 없음, mock), 실행 중 보드 쓰기 경합
# ═════════════════════════════════════════════════════════════════
def m2_chain():
    _comfy_cpu()
    os.environ.setdefault("BMK_WILDCARD_AUTORELOAD", "0")
    import logging
    import folder_paths  # noqa: E402
    import nodes  # noqa: E402
    import execution  # noqa: E402
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    CH = OUT_ROOT / "m2_chain"
    shutil.rmtree(CH, ignore_errors=True)
    for d in ("output", "temp", "input", "bmk_design_patch"):
        (CH / d).mkdir(parents=True)
    folder_paths.set_output_directory(str(CH / "output"))
    folder_paths.set_temp_directory(str(CH / "temp"))
    folder_paths.set_input_directory(str(CH / "input"))
    mock_file = CH / "bmk_design_patch" / "_MOCK_API"
    mock_file.write_text(json.dumps({"delay_s": 0.2}), encoding="utf-8")

    section("M2-C0 실제 로더로 패키지 import (nodes.load_custom_node)")
    t0 = time.time()
    ok = asyncio.run(nodes.load_custom_node(str(PKG)))
    pkgmod = sys.modules[str(PKG).replace(".", "_x_")]
    dp = sys.modules[pkgmod.__name__ + ".bmk_design_patch"]
    st, bd = dp.store, dp.board
    check(ok and all(nodes.NODE_CLASS_MAPPINGS.get(k) is dp.NODE_CLASS_MAPPINGS[k] for k in dp.NODE_CLASS_MAPPINGS),
          "load_custom_node: Design Patch 9 노드 등록")
    note(f"M2-C0 load_custom_node {time.time() - t0:.1f}s")

    ONLY = "004,005,030"
    root = st.project_root(str(CH), "m2_chain")

    def prompt(approve, concurrency=4):
        ana = {"register": "guarded_affine", "tone": "field", "tone_sigma": 16, "dE": 8.0, "grow": 9, "feather_sigma": 3.5,
               "roi_prior": "none", "only_picked": False}
        return {
            "1": {"class_type": "BMKDesignPatchProject", "inputs": {"project": "m2_chain", "base_dir": str(CH)}},
            "2": {"class_type": "BMKDesignPatchImportPSD",
                  "inputs": {"project": ["1", 0], "psd_path": str(PSD03), "crop_group": "auto", "clean_plate_layer": "auto",
                             "base_layer": "auto", "crop_pixels_group": "auto", "refs_dir": str(REFS_DIR),
                             "glossary_path": ""}},
            "3": {"class_type": "BMKDesignPatchPrepare",
                  "inputs": {"project": ["2", 0], "model": MODEL25, "quality": "high", "size_rule": "user_k",
                             "variants": "V1", "n": 1, "ref_style": "at", "calls_per_cell": 1}},
            "4": {"class_type": "BMKDesignPatchRun",
                  "inputs": {"project": ["3", 0], "approve": approve, "max_new_calls": 64, "concurrency": concurrency,
                             "wave_minutes": 25, "retry_failed": False, "retry_orphans": False, "only": ONLY}},
            "5": {"class_type": "BMKDesignPatchAnalyze", "inputs": dict(ana, project=["4", 0])},
            "6": {"class_type": "BMKDesignPatchReview", "inputs": {"project": ["5", 0]}},
            "7": {"class_type": "BMKDesignPatchCompose",
                  "inputs": {"project": ["6", 0], "mask_source": "hand_else_auto", "use_registration": True,
                             "use_tone": True, "zorder": "auto_small_on_top"}},
            "8": {"class_type": "BMKDesignPatchExportPSD",
                  "inputs": {"project": ["7", 0], "filename_prefix": "chain", "layer_mode": "smart_object",
                             "alternates": "hidden_all", "color": "toned", "mask_source": "hand_else_auto",
                             "include_base": True, "include_crop_outlines": True}},
            "9": {"class_type": "PreviewImage", "inputs": {"images": ["4", 2]}},
        }

    class Srv:
        def __init__(self):
            self.client_id = None
            self.last_node_id = None
            self.last_prompt_id = None
            self.log: list = []

        def send_sync(self, event, data, sid=None):
            self.log.append((time.perf_counter(), event, data))

        def queue_updated(self):
            pass

    srv = Srv()
    ex = execution.PromptExecutor(srv, cache_type=execution.CacheType.CLASSIC,
                                  cache_args={"lru": 0, "ram": 0, "ram_inactive": 0})

    def q(p, tag):
        srv.log = []
        tq = time.time()
        ex.execute(copy.deepcopy(p), tag, {"client_id": "m2chain"}, ["4", "6", "8", "9"])  # client_id 가 있어야 executing 이벤트
        if not ex.success:
            err = [m_[1] for m_ in ex.status_messages if m_[0] == "execution_error"]
            check(False, f"{tag} 실행 실패 {err[-1].get('node_type') if err else ''}: "
                         f"{err[-1].get('exception_message') if err else ex.status_messages[-1:]}")
        executed = [d["node"] for _t, ev, d in srv.log if ev == "executing" and d.get("node") is not None]
        note(f"M2-C {tag}: {time.time() - tq:.1f}s 실행 {','.join(executed) or '-'}")
        return executed, ex.history_result.get("outputs") or {}

    def layers(rec):
        return json.loads(Path(os.path.splitext(rec["psd"])[0] + ".json").read_text(encoding="utf-8"))["export_layers"]

    section("M2-C1 큐 0: 드라이런(approve 비움) → 과금 0, ui 해시")
    ex0, ui0 = q(prompt(""), "c0")
    e0 = (ui0.get("4") or {}).get("bmk_dp_run", [{}])[0]
    check(set("12345678") <= set(ex0) and e0.get("pending") == 3 and e0.get("backend") == "mock" and e0.get("pending_hash")
          and _ledger_reqs(root) == 0, f"c0: 전체 실행, 드라이런 3건(only {ONLY}), 원장 0 {e0}")
    H = e0["pending_hash"]
    b0 = (ui0.get("6") or {}).get("bmk_dp_board", [{}])[0]
    key = b0.get("root_key")
    check(key == e0.get("root_key") == st.root_key(root) and st.resolve_board_root(key) == root, "Review·Run 의 root_key 일치")
    check(st.load_manifest(root)["exports"] == [] and "내보낼 후보 없음" in (ui0.get("8") or {}).get("text", [""])[0],
          "c0: 후보 0 → Export 가 빈 PSD 를 쓰지 않음")

    section("M2-C2 큐 1: approve = 해시 → 3 호출(mock) → Analyze · Review · Compose · Export")
    ex1, ui1 = q(prompt(H), "c1")
    m = st.load_manifest(root)
    rc = [c for c in m["candidates"] if c["origin"] == "run"]
    e1 = ui1["4"]["bmk_dp_run"][0]
    check(_ledger_reqs(root) == 3 and len(rc) == 3 and e1["pending"] == 0 and e1["approved_now"] is True
          and set("45678") <= set(ex1), f"c1: 3 호출, 후보 3 {e1}")
    check(all(c["key"] in m["analysis"] for c in rc), "c1: Analyze 가 새 후보를 분석")
    check(len(m["exports"]) == 1 and len(layers(m["exports"][-1])) == 3, "c1: Export 에 run 후보 3 (숨김 대안)")
    rev1 = m["rev"]

    section("M2-C3 큐 2·3: 같은 승인으로 다시 → 0 호출, 그다음 큐는 아무것도 실행하지 않음")
    ex2, ui2 = q(prompt(H), "c2")
    check(_ledger_reqs(root) == 3 and "할 일 없음" in ui2["4"]["text"][0] and st.load_manifest(root)["rev"] == rev1
          and "변경 없음" in ui2["8"]["text"][0], "c2: 0 호출, rev 불변, Export 변경 없음")
    ex3, _ui3 = q(prompt(H), "c3")
    check(ex3 == [], f"c3: 안정(실행 없음) {ex3}")

    section("M2-C4 보드(라우트): ★ · 탈락 · 재굴림 → 큐 4 = 재굴림 1 호출, Compose/Export 가 보드 결과를 씀")
    by_crop = {c["crop_id"]: c for c in rc}
    tid = {c["crop_id"]: c["tid"] for c in rc}
    crop_ids = sorted(by_crop)
    c_pick, c_rej, c_rr = (by_crop[k] for k in crop_ids)
    ck_rr = c_rr["origin_info"]["cell_key"]

    async def board_post(ops):
        routes = web.RouteTableDef()
        bd.register_routes(routes)
        app = web.Application()
        app.add_routes(routes)
        out = []
        async with TestClient(TestServer(app)) as client:
            for path, body in ops:
                r = await client.post(f"/bmk/design_patch/{path}", json=dict(body, root=key))
                out.append((r.status, await r.json()))
        return out

    res = asyncio.run(board_post([
        ("pick", {"crop_id": c_pick["crop_id"], "tid": tid[c_pick["crop_id"]], "key": c_pick["key"], "mode": "set"}),
        ("reject", {"key": c_rej["key"], "value": True}),
        ("reroll", {"cell_key": ck_rr, "count": 1})]))
    check([s for s, _ in res] == [200, 200, 200] and res[2][1]["cell_pending"] == 1, f"보드 POST {res}")
    ex4, ui4 = q(prompt(H), "c4")
    m = st.load_manifest(root)
    lay = {w["key"]: w["visible"] for w in layers(m["exports"][-1])}
    rr_new = [c for c in m["candidates"] if c["origin"] == "run" and c["crop_id"] == c_rr["crop_id"]]
    check(_ledger_reqs(root) == 4 and "발사 1" in ui4["4"]["text"][0] and len(rr_new) == 2, "c4: 재굴림 1 호출(승인 없이), 후보 +1")
    check(lay.get(c_pick["key"]) is True and c_rej["key"] not in lay and sum(1 for k in lay if k in {c["key"] for c in rr_new}) == 2
          and "(pick 1)" in ui4["8"]["text"][0], f"c4: Export = 보드 ★ 보임, 탈락 없음, 재굴림 후보 숨김 대안 {lay}")

    section("M2-C5 경합: Run(사전 승인 6 호출, 동시 1) · Analyze · Export 실행 중 보드 쓰기 → 잃은 갱신 0")
    mock_file.write_text(json.dumps({"delay_s": 0.35}), encoding="utf-8")
    run_cells = sorted({c["origin_info"]["cell_key"] for c in rc})
    asyncio.run(board_post([("reroll", {"cell_key": ck, "count": 2}) for ck in run_cells]))
    m = st.load_manifest(root)
    # only 밖 셀의 재굴림: 보드 재굴림은 only 와 무관하게 사전 승인으로 과금된다(WSU-4). 그래서 Run 이 계획을 세우고
    # 발사를 시작한 뒤에만 보낸다 — 이 큐의 Run 은 이미 정한 6 호출만 하고, 쌓인 재굴림은 다음 Run 몫
    other_cells = [j["cell_key"] for j in m["jobs"] if j["cell_key"] not in run_cells]
    rr0 = dict(m["rerolls"])
    toggle_keys = [c_rej["key"]] + [c["key"] for c in rr_new]
    n_reqs0 = _ledger_reqs(root)
    keys0 = {c["key"] for c in m["candidates"] if c["origin"] == "run"}
    conflicts: list[str] = []

    class Grab(logging.Handler):
        def emit(self, record):
            if "병합 충돌" in record.getMessage():
                conflicts.append(record.getMessage())

    grab = Grab()
    logging.getLogger(st.__name__).addHandler(grab)
    result: dict = {}
    th = threading.Thread(target=lambda: result.update(zip(("executed", "ui"), q(prompt(H, concurrency=1), "c5"))))
    writes: list = []

    async def hammer():
        routes = web.RouteTableDef()
        bd.register_routes(routes)
        app = web.Application()
        app.add_routes(routes)
        i = 0
        run_seen = False
        async with TestClient(TestServer(app)) as client:
            th.start()
            while th.is_alive():
                run_seen = run_seen or bd.is_running(root)
                kind = i % 3 if run_seen else 1 + i % 2
                if kind == 0:
                    ck = other_cells[(i // 3) % len(other_cells)]
                    body = {"cell_key": ck, "count": 1}
                    path = "reroll"
                elif kind == 1:
                    k = toggle_keys[(i // 3) % len(toggle_keys)]
                    body = {"key": k, "value": (i // 3) % 2 == 0}
                    path = "reject"
                else:
                    body = {"crop_id": c_pick["crop_id"], "tid": tid[c_pick["crop_id"]], "key": c_pick["key"],
                            "mode": "set" if (i // 3) % 2 else "remove"}
                    path = "pick"
                r = await client.post(f"/bmk/design_patch/{path}", json=dict(body, root=key))
                writes.append((time.perf_counter(), path, body, r.status, await r.json()))
                i += 1
                await asyncio.sleep(0.01)

    try:
        asyncio.run(hammer())
        th.join()
    finally:
        logging.getLogger(st.__name__).removeHandler(grab)
    m = st.load_manifest(root)
    ok_w = [w for w in writes if w[3] == 200]
    bad_w = [(w[1], w[3]) for w in writes if w[3] != 200 and not (w[1] == "reroll" and w[3] == 409)]  # 409 = 셀 대기 상한
    check(not bad_w and len(ok_w) >= 30, f"보드 쓰기 {len(writes)}회(성공 {len(ok_w)}), 상한 409 외 오류 없음 {bad_w[:3]}")
    exp_rr = dict(rr0)
    exp_rej: dict = {}
    exp_pick = None
    for _t, path, body, _s, _d in ok_w:
        if path == "reroll":
            exp_rr[body["cell_key"]] = exp_rr.get(body["cell_key"], 0) + 1
        elif path == "reject":
            exp_rej[body["key"]] = body["value"]
        else:
            exp_pick = body["mode"]
    check(all(m["rerolls"].get(ck, 0) == v for ck, v in exp_rr.items()), "rerolls: 성공한 재굴림 수 그대로(잃은 갱신 0)")
    check(all(bool(m["rejects"].get(k)) == v for k, v in exp_rej.items()), f"rejects: 마지막 요청 값 그대로 {exp_rej}")
    tkey = f"{c_pick['crop_id']}/{tid[c_pick['crop_id']]}"
    picked_flag = next(c for c in m["candidates"] if c["key"] == c_pick["key"])["picked"]
    check((m["picks"].get(tkey) == [c_pick["key"]] and picked_flag) if exp_pick == "set"
          else (tkey not in m["picks"] and not picked_flag), f"pick: 마지막 요청({exp_pick}) 그대로 + picked 동기화")
    fresh = [c for c in m["candidates"] if c["origin"] == "run" and c["key"] not in keys0]
    check(_ledger_reqs(root) == n_reqs0 + 6 and len(fresh) == 6
          and sum(1 for c in m["calls"] if c["status"] == "done") == n_reqs0 + 6, "Run: 사전 승인 6 호출 결과 전부 반영")
    check(all(c["key"] in m["analysis"] for c in fresh), "Analyze: 새 후보 6 분석 결과 보존(보드 쓰기와 3-way 병합)")
    check(not conflicts, f"병합 충돌 0 {conflicts[:2]}")
    starts = [(t, d["node"]) for t, ev, d in srv.log if ev == "executing" and d.get("node") is not None]
    ends = [t for t, ev, d in srv.log if ev == "executing" and d.get("node") is None]
    span = {}
    for i_, (t, n_) in enumerate(starts):
        span[n_] = (t, starts[i_ + 1][0] if i_ + 1 < len(starts) else (ends[-1] if ends else time.perf_counter()))
    during = {n_: sum(1 for w in ok_w if s <= w[0] <= e_) for n_, (s, e_) in span.items()}
    check(during.get("4", 0) >= 3 and during.get("5", 0) >= 1, f"Run·Analyze 실행 중에 보드 쓰기가 실제로 겹침 {during}")
    note(f"M2-C5 경합: 보드 쓰기 {len(writes)}회(노드별 {during}), Run 6 호출, 병합 충돌 0")
    mock_file.unlink(missing_ok=True)


# ═════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    T0 = time.time()
    store_unit()
    m2_store_unit()  # T0 의 import 의존성 검사(torch·comfy·aiohttp 없음)는 다른 묶음이 그것들을 불러오기 전에
    prompt_unit()
    analysis_unit()
    psd_unit()
    m2_runner_unit(with_comfy=not QUICK)  # R0(torch·comfy 없음) 먼저, C*(comfy 를 CPU 로 import)는 끝에
    m2_board_unit()
    m2_js_unit()
    print(f"\n단위 테스트 {time.time() - T0:.1f}s (PASS {PASSES} FAIL {len(FAILS)})")
    if QUICK:
        print("\n--quick: 회귀·노드 생략")
    else:
        if not HAVE_REF:
            skip(f"참조 폴더 없음({REF}) — 회귀·M1 노드 통합·M2 체인 생략")
        else:
            t_r = time.time()
            reg = psd_regression()
            print(f"\npsd 회귀 {time.time() - t_r:.1f}s")
            t_r = time.time()
            analysis_regression(reg["psd05"])
            print(f"\nanalysis 회귀 {time.time() - t_r:.1f}s")
            t_r = time.time()
            export_path = node_integration(reg["psd05"], reg["crops03"])
            print(f"\n노드 통합 {time.time() - t_r:.1f}s → {export_path}")
        t_r = time.time()
        m2_node_unit()
        print(f"\nM2 노드 {time.time() - t_r:.1f}s")
        if HAVE_REF:
            t_r = time.time()
            m2_chain()
            print(f"\nM2 체인 {time.time() - t_r:.1f}s")
    print("\n── 측정값")
    for line in MEASURED:
        print("  -", line)
    print(f"\nPASS {PASSES}  FAIL {len(FAILS)}  SKIP {len(SKIPS)}  ({time.time() - T0:.1f}s)")
    if FAILS:
        for f in FAILS:
            print(" -", f)
        sys.exit(1)
