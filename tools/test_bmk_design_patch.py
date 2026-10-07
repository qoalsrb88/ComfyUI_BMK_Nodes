# -*- coding: utf-8 -*-
r"""Design Patch 통합 테스트 (M1_SPEC §6): 보조 모듈 단위 테스트 + 참조 데이터 회귀 + 노드 통합.

실행 (ComfyUI-Easy-Install 루트에서):
    python_embeded\python.exe -X utf8 -W ignore ComfyUI\custom_nodes\ComfyUI_BMK_Nodes\tools\test_bmk_design_patch.py [--quick]

단위(합성 데이터, 수 초)
  S  store: 프로젝트 이름, 매니페스트 원자 저장, 내용 주소 저장, crop_id, 레이어명 파싱, 용어집, 레퍼런스 매칭,
     GPT 출력 크기(명세 검증표 + 전수/무작위), cell_key
  P  prompt: 렌더러 바이트 일치(rendered/*.txt), 시제품 대조, 변형, 평탄화, spec_from_manifest
  A  analysis: 배치, Lab, 정합 부호·가드·affine·exclude_mask, 색 맞춤, ΔE, 자동 마스크(합성 원), 톤 보정장, 게이트
  U  psd: quad 도우미, SO 캐시, PSDWriter 왕복(픽셀/그룹/마스크/SO/한글명/숨김/라벨, lnk2 꼬리 117B, 원시 검사 0)
회귀(참조 폴더 H:\BmkNodeDesign\MultiLayerCropEdit 가 있을 때, --quick 이면 생략)
  R1 03 rect 22/22 · R2 크롭 소스 ≥15/19 비트 일치 · R3 05 harvest · R4 SO 배치 MAE ≤0.25 · RW 풀캔버스 SO PSD · RX 템플릿 상수
  R5 정합 기대값 · R6 자동 마스크 IoU · R7 톤 홀드아웃 · G 게이트   (05 PSD 를 psd 모듈로 읽어 입력 구성)
  N  노드: import(상대/단독)·규약·__init__ 등록, Project → Import PSD(03) → Candidate In(05) → Prepare → Analyze,
     Q  정합 quad vs 05 사용자 quad(001 / 007-왼쪽 / 030 코너 오차 px)
     R9 Compose(mask=hand, 05 quad) vs 05 psd.topil() 작업영역 MAE(목표 ≤1.5, 크롭별 표)
     R8 Export PSD 재열기(SO 개수, 임베드 크기, quad, 그룹 마스크 bbox, 꼬리) + 멱등(재실행 rev 불변) + 폴더 후보
출력물은 H:\BmkNodeDesign\MultiLayerCropEdit\_proto\m1_test_out\ 아래에만 쓴다(노드 프로젝트·PSD 는 nodes\).
"""
from __future__ import annotations

import ast
import copy
import hashlib
import importlib
import importlib.util
import inspect
import io
import json
import math
import os
import random
import re
import shutil
import sys
import time
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
OUT_ROOT = REF / "_proto" / "m1_test_out"
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
def _run_export(dp, h, *args):
    r = dp.BMKDesignPatchExportPSD().run(h, *args)
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
           "BMKDesignPatchAnalyze", "BMKDesignPatchCompose", "BMKDesignPatchExportPSD"]
    check(list(dp.NODE_CLASS_MAPPINGS) == ids and list(dp.NODE_DISPLAY_NAME_MAPPINGS) == ids, "노드 7개 매핑")
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
        check(list(req) == list(inspect.signature(getattr(cls, cls.FUNCTION)).parameters)[1:], f"{nid} 입력 이름 = 함수 인자")
    check(getattr(dp.BMKDesignPatchExportPSD, "OUTPUT_NODE", False) is True, "Export PSD 는 OUTPUT_NODE")
    rt = {k: v.RETURN_TYPES for k, v in dp.NODE_CLASS_MAPPINGS.items()}
    check(rt == {"BMKDesignPatchProject": ("BMK_DP_PROJECT", "STRING"),
                 "BMKDesignPatchImportPSD": ("BMK_DP_PROJECT", "IMAGE", "STRING"),
                 "BMKDesignPatchCandidateIn": ("BMK_DP_PROJECT", "STRING"),
                 "BMKDesignPatchPrepare": ("BMK_DP_PROJECT", "IMAGE", "STRING"),
                 "BMKDesignPatchAnalyze": ("BMK_DP_PROJECT", "IMAGE", "STRING"),
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
if __name__ == "__main__":
    T0 = time.time()
    store_unit()
    prompt_unit()
    analysis_unit()
    psd_unit()
    print(f"\n단위 테스트 {time.time() - T0:.1f}s (PASS {PASSES} FAIL {len(FAILS)})")
    if QUICK:
        print("\n--quick: 회귀 생략")
    elif not HAVE_REF:
        skip(f"참조 폴더 없음({REF}) — 회귀 생략")
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
    print("\n── 측정값")
    for line in MEASURED:
        print("  -", line)
    print(f"\nPASS {PASSES}  FAIL {len(FAILS)}  SKIP {len(SKIPS)}  ({time.time() - T0:.1f}s)")
    if FAILS:
        for f in FAILS:
            print(" -", f)
        sys.exit(1)
