# -*- coding: utf-8 -*-
r"""Design Patch Studio S2 — U0 사전 실측: 로컬 SAM3 컷이 사람 레퍼런스 컷아웃을 얼마나 재현하는가 (STUDIO_S2_SPEC §6.5).

정답 = 프로젝트 refs\ 바로 아래 사람 컷 중 투명이 있는 것(사각형 자르기는 정답 마스크가 못 됨). 시트에서 그 컷의 자리를 찾고(locate, CPU),
그 사각형을 선택 영역으로 SAM3 를 돌려(run) 사람 알파와 IoU·경계 F(2px)·시간·VRAM 을 잰다. 유료 호출·네트워크 없음, 프로젝트 파일은 읽기만 한다.
SAM3 는 BMK SAM3 Mask Layers 의 체크포인트 로더(_load_sam3)와 코어 경로(forward_segment 의 box/point, 감지기 텍스트 + _refine_mask)를 쓴다
— S2 Ref Cut 노드가 쓸 경로와 같다.

실행 (ComfyUI-Easy-Install 루트에서):
    python_embeded\python.exe -X utf8 -W ignore ComfyUI\custom_nodes\ComfyUI_BMK_Nodes\tools\eval_sam3_refcut.py <명령> …

  locate --project P [--sheet 00_원본/refer_total.png] --out D
        P\refs\ 의 투명 있는 PNG 를 시트에서 찾는다(1/8 축소 맞춤 → 원해상도 다듬기, 안 맞으면 좌우 반전·회전 ±180° 탐색).
        D\cases.json(자리·반전·회전·오차·텍스트) + 표. 원해상도 오차(불투명 픽셀 평균 |차|)가 크면 excluded.
        텍스트 = 매니페스트에서 그 컷 이름의 크롭 타깃 영문 이름(name_en)을 ", " 로 이은 것
  run --cases D\cases.json --out D [--ckpt 이름] [--grid full|quick] [--limit N] [--names a,b] [--cpu] [--min-free-gb 6]
        사례 × 격자(프롬프트 · 창 여백 pad · refine · 상자 여유 margin · 고침 점 0/1) → D\results.json·results.md·overlay\.
        GPU 여유가 min-free-gb 미만이면 시작하지 않는다(사용자 생성과 겹치지 않게). --cpu 는 코드 확인용(호출당 약 20초, GPU 안 씀)
  report --results D\results.json   results.md 를 다시 쓴다

프롬프트: rect = SAM 없이 선택 안 시트 알파 그대로(사각형 방법, 기준선) / box = 상자만 / box_pt = 상자 + 자동 양점(상자 가운데에 가장 가까운
시트 불투명 픽셀) / box_pt2 = box_pt 결과의 경계 상자로 한 번 더 / text = 감지기 텍스트(+ _refine_mask).
refine 은 코어 SAM3 Detect 와 같은 뜻: 상자·점은 mask_inputs 다듬기 max(0, refine − 1) 번, 텍스트는 _refine_mask 반복 refine 번.
고침 점 = 첫 결과의 가장 큰 틀린 덩어리 안쪽에 음(넘침)·양(모자람) 점 하나를 더해 다시(사람이 한 번 고치는 흉내, 텍스트·rect·box_pt2 는 없음).
멀티마스크 auto = 코어 forward_segment(점이 정확히 1개일 때만 후보 3개 중 최고), always = 늘 후보 3개 중 최고(S2 Ref Cut 노드가 쓸 경로).
격자: full · multimask · tighten · final(GRIDS). 판정(§6.5): DEFAULT_CFG(= SAM '시도' 기본값)의 중앙 IoU ≥ 0.9 이면 SAM3 를 기본 방법으로.
10/16 결과: 0.650 → 시작 방법 = 사각형, SAM3 는 '시도' 단추(명세 §6.5).
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

Image.MAX_IMAGE_PIXELS = None

_HERE = Path(__file__).resolve()
PKG = _HERE.parents[1]
COMFY = _HERE.parents[3]
sys.path.insert(0, str(PKG))
import bmk_design_patch_store as store  # noqa: E402  (torch 없음)

COARSE = 8                 # 1/8 축소로 먼저 찾는다
FINE_SLACK = 2 * COARSE    # 원해상도 다듬기 범위(축소 1px = 원본 8px, 양쪽 2배)
EXCLUDE_ERR = 6.0          # 원해상도 불투명 픽셀 평균 |차| 가 이보다 크면 시트에서 자른 컷이 아님(다시 그림·다른 시트)
RECT_TRANSP = 0.01         # 투명이 이보다 적으면 사각형 자르기(정답 마스크 아님)
DROP_SMALL = 0.005
BF_TOL = 2
TEXT_THRESHOLD = 0.3       # BMK SAM3 Mask Layers 기본값과 같음
SAM_SIZE = 1008
KEYS = ("prompt", "pad", "refine", "margin", "fix", "mm")
DEFAULT_CFG = {"prompt": "box_pt", "pad": 0.12, "refine": 0, "margin": 0.0, "fix": 0, "mm": "always"}  # SAM '시도' 기본값(U0 결과, 명세 §3.2)
GRIDS = {
    "full": {"prompt": ["box", "box_pt", "text"], "pad": [0.0, 0.12, 0.25], "refine": [0, 2], "margin": [0.0, 0.05], "fix": [0, 1], "mm": ["auto"]},
    "quick": {"prompt": ["box", "box_pt", "text"], "pad": [0.12], "refine": [2], "margin": [0.05], "fix": [0, 1], "mm": ["auto"]},
    # 멀티마스크: auto = 코어 forward_segment(점이 정확히 1개일 때만 후보 3개 중 예측 IoU 최고), always = 늘 후보 3개 중 최고
    "multimask": {"prompt": ["box", "box_pt"], "pad": [0.0, 0.05, 0.12], "refine": [0], "margin": [0.0, 0.05, 0.1], "fix": [0, 1],
                  "mm": ["auto", "always"]},
    # 좁히기: box_pt2 = box_pt 결과의 경계 상자로 한 번 더(사람이 넉넉히 그린 상자 흉내 = margin)
    "tighten": {"prompt": ["box_pt", "box_pt2"], "pad": [0.0], "refine": [0], "margin": [0.0, 0.05, 0.1, 0.2], "fix": [0],
                "mm": ["always"]},
    # 판정: rect = SAM 없이 선택 안 시트 알파 그대로(사각형 방법) 대 SAM '시도' 후보
    "final": {"prompt": ["rect", "box_pt"], "pad": [0.0, 0.12], "refine": [0], "margin": [0.0, 0.05], "fix": [0, 1], "mm": ["always"]},
}


def _cfg_of(r: dict) -> tuple:
    return tuple(r.get(k, DEFAULT_CFG[k]) for k in KEYS)


# ══════════════════════════════════════════════════════════════════════
# 공통
# ══════════════════════════════════════════════════════════════════════
def _rgba(path: Path) -> np.ndarray:
    with Image.open(path) as im:
        return np.asarray(im.convert("RGBA"))


def _on_white(rgba: np.ndarray) -> np.ndarray:
    a = rgba[..., 3:4].astype(np.float32) / 255.0
    return rgba[..., :3].astype(np.float32) * a + 255.0 * (1.0 - a)


def _transform(rgba: np.ndarray, flip: bool, rotate: float) -> np.ndarray:
    """컷을 시트 방향으로 — 찾을 때 쓴 변환 그대로(반전 → 회전, 회전은 캔버스를 넓힘)."""
    out = rgba[:, ::-1] if flip else rgba
    if rotate:
        out = np.asarray(Image.fromarray(np.ascontiguousarray(out)).rotate(rotate, resample=Image.BILINEAR, expand=True))
    return np.ascontiguousarray(out)


def _match(img: np.ndarray, tmpl: np.ndarray, mask: np.ndarray) -> tuple[float, tuple[int, int]]:
    """마스크 안 평균 제곱 차(채널당)가 가장 작은 자리."""
    res = cv2.matchTemplate(img, tmpl, cv2.TM_SQDIFF, mask=mask)
    res[~np.isfinite(res)] = np.inf
    v, _, loc, _ = cv2.minMaxLoc(res)
    return float(v) / max(1.0, float(mask[..., 0].sum()) * 3.0), (int(loc[0]), int(loc[1]))


def _small(arr: np.ndarray) -> np.ndarray:
    h, w = arr.shape[:2]
    return cv2.resize(arr, (max(1, w // COARSE), max(1, h // COARSE)), interpolation=cv2.INTER_AREA)


# ══════════════════════════════════════════════════════════════════════
# locate (CPU)
# ══════════════════════════════════════════════════════════════════════
def _coarse(sheet_small: np.ndarray, gold: np.ndarray, flip: bool, rot: float):
    ts = _small(_transform(gold, flip, rot))
    m = np.repeat((ts[..., 3:4] > 127).astype(np.float32), 3, axis=2)
    if ts.shape[0] >= sheet_small.shape[0] or ts.shape[1] >= sheet_small.shape[1] or m.sum() < 30:
        return None
    score, loc = _match(sheet_small, _on_white(ts), m)
    return score, loc, flip, rot


def _fine(sheet: np.ndarray, sheet_white: np.ndarray, gold: np.ndarray, flip: bool, rot: float, cx: int, cy: int) -> dict:
    """1/8 자리 (cx, cy) 근처를 원해상도로 맞추고 오차(양쪽 불투명 픽셀 평균 |차|)를 잰다."""
    t = _transform(gold, flip, rot)
    th, tw = t.shape[:2]
    H, W = sheet.shape[:2]
    x0, y0 = max(0, cx * COARSE - FINE_SLACK), max(0, cy * COARSE - FINE_SLACK)
    x1, y1 = min(W, cx * COARSE + tw + FINE_SLACK), min(H, cy * COARSE + th + FINE_SLACK)
    if x1 - x0 < tw or y1 - y0 < th:
        return {"err": float("inf")}
    m = np.repeat((t[..., 3:4] > 127).astype(np.float32), 3, axis=2)
    _f, (fx, fy) = _match(sheet_white[y0:y1, x0:x1], _on_white(t), m)
    bx, by = x0 + fx, y0 + fy
    win = sheet[by:by + th, bx:bx + tw]
    both = (t[..., 3] == 255) & (win[..., 3] == 255)
    err = float(np.abs(t[..., :3].astype(np.int16) - win[..., :3].astype(np.int16))[both].mean()) if both.any() else float("inf")
    outside = float(((t[..., 3] > 127) & (win[..., 3] == 0)).sum() / max(1, (t[..., 3] > 127).sum()))
    return {"bbox": [int(bx), int(by), int(bx + tw), int(by + th)], "flip": bool(flip), "rotate": round(rot, 2),
            "err": round(err, 2), "outside_sheet_alpha": round(outside, 4)}


def _locate_one(sheet: np.ndarray, sheet_white: np.ndarray, sheet_small: np.ndarray, gold: np.ndarray) -> dict:
    """반전·회전 없이 → 안 맞으면 1/8 에서 회전 −180…177°(3° 간격)·반전 → ±3°(0.5°) → 원해상도 ±0.5°(0.1°)."""
    best = None
    for flip in (False, True):
        c = _coarse(sheet_small, gold, flip, 0.0)
        if c is not None:
            r = _fine(sheet, sheet_white, gold, flip, 0.0, *c[1])
            if best is None or r["err"] < best["err"]:
                best = r
            if r["err"] <= EXCLUDE_ERR:
                return r
    found = [c for c in (_coarse(sheet_small, gold, f, float(a)) for a in range(-180, 180, 3) for f in (False, True)) if c is not None]
    if not found:
        if best is None:
            raise ValueError("시트보다 큰 컷")
        return best
    _s, _l, flip, rot = min(found)
    near = [c for c in (_coarse(sheet_small, gold, flip, rot + d / 2) for d in range(-6, 7)) if c is not None]
    _s, loc, flip, rot = min(near)
    for d in range(-5, 6):
        r = _fine(sheet, sheet_white, gold, flip, rot + d / 10, *loc)
        if best is None or r["err"] < best["err"]:
            best = r
    return best


def _case_text(manifest: dict, ref_name: str) -> str:
    """컷 파일 이름의 '_t' 앞 = 크롭 이름 → 그 크롭 타깃 영문 이름들(", " 로 이음 — SAM3 문법의 범주 합집합)."""
    stem = os.path.splitext(ref_name)[0]
    m = store._REF_T_RE.search(stem)
    crop_name = stem[: m.start()] if m else stem
    for c in manifest.get("crops") or []:
        if c.get("name") == crop_name:
            return ", ".join(t["name_en"] for t in c.get("targets") or [] if t.get("name_en"))
    return ""


def cmd_locate(a) -> int:
    proj = Path(a.project)
    sheet_path = proj / a.sheet
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    manifest = json.loads((proj / store.MANIFEST_NAME).read_text(encoding="utf-8")) if (proj / store.MANIFEST_NAME).is_file() else {}
    sheet = _rgba(sheet_path)
    sheet_white = _on_white(sheet)
    sheet_small = _small(sheet_white)
    cases = []
    for fn in sorted(os.listdir(proj / "refs")):
        p = proj / "refs" / fn
        if not p.is_file() or p.suffix.lower() != ".png":
            continue
        gold = _rgba(p)
        transp = float((gold[..., 3] < 255).mean())
        row = {"name": fn, "size": [int(gold.shape[1]), int(gold.shape[0])], "transparent": round(transp, 3), "text": _case_text(manifest, fn)}
        if transp < RECT_TRANSP:
            row.update(excluded=f"사각형 자르기(투명 {transp:.1%}) — 정답 마스크 아님")
        else:
            t1 = time.time()
            try:
                row.update(_locate_one(sheet, sheet_white, sheet_small, gold))
            except ValueError as e:
                row.update(excluded=str(e))
            row["sec"] = round(time.time() - t1, 2)
            if "err" in row and row["err"] > EXCLUDE_ERR:
                row["excluded"] = f"시트와 픽셀이 다름(평균 |차| {row['err']}) — 다시 그렸거나 다른 시트"
        cases.append(row)
        print(f"{fn}: " + json.dumps({k: v for k, v in row.items() if k != 'name'}, ensure_ascii=False))
    doc = {"project": str(proj).replace("\\", "/"), "sheet": a.sheet.replace("\\", "/"),
           "sheet_size": [int(sheet.shape[1]), int(sheet.shape[0])], "fingerprint": store.file_fingerprint(str(sheet_path)),
           "cases": cases, "sec": round(time.time() - t0, 1)}
    (out / "cases.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    used = [c for c in cases if "excluded" not in c]
    print(f"정답 사례 {len(used)} / 컷 {len(cases)} · {doc['sec']}s → {out / 'cases.json'}")
    return 0


# ══════════════════════════════════════════════════════════════════════
# run (SAM3)
# ══════════════════════════════════════════════════════════════════════
def _comfy(cpu: bool):
    """comfy 를 이 프로세스에 불러온다(인자 파싱 끔). cpu 면 GPU 를 건드리지 않는다."""
    sys.path.insert(0, str(COMFY))
    import comfy.options
    comfy.options.enable_args_parsing(False)
    import comfy.cli_args
    comfy.cli_args.args.cpu = cpu
    import torch
    import comfy.model_management as mm
    import comfy.utils
    from comfy.ops import cast_to_input
    import folder_paths
    from comfy_extras import nodes_sam3
    import bmk_sam3_mask_layers as sl
    return torch, mm, comfy.utils, cast_to_input, folder_paths, nodes_sam3, sl


def _expand(box, frac, W, H):
    x0, y0, x1, y1 = box
    dx, dy = (x1 - x0) * frac, (y1 - y0) * frac
    return [max(0, int(round(x0 - dx))), max(0, int(round(y0 - dy))), min(W, int(round(x1 + dx))), min(H, int(round(y1 + dy)))]


def _boundary(m: np.ndarray) -> np.ndarray:
    k = np.ones((3, 3), np.uint8)
    u = m.astype(np.uint8)
    return (cv2.dilate(u, k) - cv2.erode(u, k)) > 0


def _scores(pred: np.ndarray, gold: np.ndarray) -> dict:
    inter = int((pred & gold).sum())
    union = int((pred | gold).sum())
    bp, bg = _boundary(pred), _boundary(gold)
    dg = cv2.distanceTransform((~bg).astype(np.uint8), cv2.DIST_L2, 3)
    dp = cv2.distanceTransform((~bp).astype(np.uint8), cv2.DIST_L2, 3)
    prec = float((dg[bp] <= BF_TOL).mean()) if bp.any() else 0.0
    rec = float((dp[bg] <= BF_TOL).mean()) if bg.any() else 0.0
    bf = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    return {"iou": round(inter / union, 4) if union else 0.0, "bf": round(bf, 4),
            "fp": int((pred & ~gold).sum()), "fn": int((gold & ~pred).sum())}


def _drop_small(m: np.ndarray, frac: float) -> np.ndarray:
    n, lab, st, _ = cv2.connectedComponentsWithStats(m.astype(np.uint8), connectivity=8)
    if n <= 2:
        return m
    keep = np.zeros(n, bool)
    keep[1:] = st[1:, cv2.CC_STAT_AREA] >= frac * st[1:, cv2.CC_STAT_AREA].sum()
    return keep[lab]


def _auto_point(alpha: np.ndarray, rect: tuple[int, int, int, int]):
    """상자 가운데에 가장 가까운 불투명 픽셀(창 좌표) — 사람 클릭 없이 만드는 양점. 상자 안이 전부 투명이면 None."""
    x0, y0, x1, y1 = rect
    ys, xs = np.nonzero(alpha[y0:y1, x0:x1])
    if not len(xs):
        return None
    i = int(np.argmin((xs + x0 - (x0 + x1) / 2) ** 2 + (ys + y0 - (y0 + y1) / 2) ** 2))
    return int(xs[i] + x0), int(ys[i] + y0), 1


def _fix_point(pred: np.ndarray, gold: np.ndarray):
    """가장 큰 틀린 덩어리 안쪽 한 점 — 넘친 것이면 음(0), 모자란 것이면 양(1). 사람이 한 번 고치는 흉내."""
    best = None
    for err, label in ((pred & ~gold, 0), (gold & ~pred, 1)):
        n, lab, st, _ = cv2.connectedComponentsWithStats(err.astype(np.uint8), connectivity=8)
        if n > 1:
            i = 1 + int(np.argmax(st[1:, cv2.CC_STAT_AREA]))
            area = int(st[i, cv2.CC_STAT_AREA])
            if best is None or area > best[0]:
                best = (area, lab == i, label)
    if best is None or best[0] < 16:
        return None
    d = cv2.distanceTransform(best[1].astype(np.uint8), cv2.DIST_L2, 3)
    y, x = np.unravel_index(int(np.argmax(d)), d.shape)
    return int(x), int(y), best[2]


def _overlay(rgb_white: np.ndarray, pred: np.ndarray, gold: np.ndarray, path: Path) -> None:
    img = rgb_white.astype(np.float32).copy()
    img[pred & ~gold] = img[pred & ~gold] * 0.5 + np.array([255, 0, 0]) * 0.5
    img[gold & ~pred] = img[gold & ~pred] * 0.5 + np.array([0, 80, 255]) * 0.5
    img[_boundary(gold)] = (0, 200, 0)
    img[_boundary(pred)] = (255, 0, 0)
    im = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8))
    im.thumbnail((1024, 1024), Image.LANCZOS)
    im.save(path)


def cmd_run(a) -> int:
    doc = json.loads(Path(a.cases).read_text(encoding="utf-8"))
    out = Path(a.out)
    (out / "overlay").mkdir(parents=True, exist_ok=True)
    names = {n.strip() for n in a.names.split(",") if n.strip()}
    cases = [c for c in doc["cases"] if "excluded" not in c and (not names or c["name"] in names)][: a.limit or None]
    if not cases:
        print("정답 사례가 없습니다(locate 결과 확인)")
        return 1
    torch, mm, cu, cast_to_input, folder_paths, nodes_sam3, sl = _comfy(a.cpu)
    if not a.cpu:
        free = torch.cuda.mem_get_info()[0] / 2**30
        if free < a.min_free_gb:
            print(f"GPU 여유 {free:.1f}GB < {a.min_free_gb}GB — 사용자 작업과 겹치지 않게 시작하지 않습니다")
            return 2
        torch.cuda.reset_peak_memory_stats()
    ckpts = [n for n in folder_paths.get_filename_list("checkpoints") if "sam3" in n.lower()]
    ckpt = a.ckpt or (ckpts[0] if ckpts else None)
    if not ckpt:
        print("checkpoints 에 sam3 체크포인트가 없습니다")
        return 1
    path = folder_paths.get_full_path_or_raise("checkpoints", ckpt)
    t0 = time.time()
    model, clip = sl._load_sam3((path, os.path.getmtime(path)))
    mm.load_model_gpu(model)
    dev, dtype = mm.get_torch_device(), model.model.get_dtype()
    sam = model.model.diffusion_model
    load_sec = time.time() - t0
    vram_loaded = torch.cuda.memory_allocated() / 2**30 if not a.cpu else None
    print(f"SAM3 {ckpt} 로드 {load_sec:.1f}s · {dev} {dtype}" + (f" · VRAM {vram_loaded:.2f}GB" if vram_loaded is not None else ""))

    sheet = _rgba(Path(doc["project"]) / doc["sheet"])
    H, W = sheet.shape[:2]
    grid = GRIDS[a.grid]
    rows: list[dict] = []
    calls: dict[str, list[float]] = {}

    def timed(kind, fn):
        if not a.cpu:
            torch.cuda.synchronize()
        t = time.time()
        r = fn()
        if not a.cpu:
            torch.cuda.synchronize()
        calls.setdefault(kind, []).append(time.time() - t)
        return r

    def forward_multimask(frame, pts, box):
        """코어 SAM3Model.forward_segment(comfy/ldm/sam3/detector.py:524-565)와 같되 multimask_output 을 늘 켠다."""
        bb = sam.detector.backbone["vision_backbone"]
        if bb.multiplex:
            _, _, feats, _pos = bb(frame, tracker_mode="interactive")
        else:
            _, _, feats, _pos = bb(frame, need_tracker=True)
            if sam.detector.scalp > 0:
                feats = feats[:-sam.detector.scalp]
        high_res, feat = list(feats[:-1]), feats[-1]
        B, C, h, w = feat.shape
        no_mem = getattr(sam.tracker, "interactivity_no_mem_embed", None)
        if no_mem is None:
            no_mem = getattr(sam.tracker, "no_mem_embed", None)
        if no_mem is not None:
            flat = feat.flatten(2).permute(0, 2, 1)
            feat = (flat + cast_to_input(no_mem, flat)).view(B, h, w, C).permute(0, 3, 1, 2)
        _, hr, _, _ = sam.tracker._forward_sam_heads(backbone_features=feat, point_inputs=pts, mask_inputs=None, box_inputs=box,
                                                     high_res_features=high_res, multimask_output=True)
        return hr

    def segment(frame, box, points, refine, size, mm="auto"):
        pts = None
        if points:
            pts = {"point_coords": torch.tensor([[[p[0], p[1]] for p in points]], dtype=dtype, device=dev),
                   "point_labels": torch.tensor([[p[2] for p in points]], dtype=torch.int32, device=dev)}

        def run():
            logit = forward_multimask(frame, pts, box) if mm == "always" else sam.forward_segment(frame, point_inputs=pts, box_inputs=box)
            for _ in range(max(0, refine - 1)):
                logit = sam.forward_segment(frame, mask_inputs=logit)
            return logit
        logit = timed("box" if not points else "box_pt", run)
        return torch.nn.functional.interpolate(logit.float(), size=size, mode="bilinear", align_corners=False)[0, 0].cpu().numpy() > 0

    def detect_text(frame, img_hwc, text, refine, size):
        def run():
            cond = clip.encode_from_tokens_scheduled(clip.tokenize(text))
            acc = np.zeros(size, bool)
            for emb, tmask, max_det in nodes_sam3._extract_text_prompts(cond, dev, dtype):
                r = sam(frame, text_embeddings=emb, text_mask=tmask, threshold=TEXT_THRESHOLD, orig_size=size)
                probs = r["scores"][0].sigmoid()
                idx = torch.nonzero(probs > TEXT_THRESHOLD).squeeze(1)
                idx = idx[probs[idx].argsort(descending=True)[:max_det]]
                for m, b in zip(r["masks"][0][idx], r["boxes"][0][idx]):
                    acc |= (nodes_sam3._refine_mask(sam, img_hwc, m, b, size[0], size[1], dev, dtype, refine)[0] > 0).cpu().numpy()
            return acc
        return timed("text", run)

    def tighten(m, win, pad, refine, mm):
        """box_pt2: 첫 결과의 경계 상자를 새 선택으로 한 번 더 컷(넉넉히 그린 상자를 바짝 좁힘). 결과는 첫 창 좌표로 돌려준다."""
        ys, xs = np.nonzero(m)
        sel = [win[0] + int(xs.min()), win[1] + int(ys.min()), win[0] + int(xs.max()) + 1, win[1] + int(ys.max()) + 1]
        w2 = _expand(sel, pad, W, H)
        crop2 = sheet[w2[1]:w2[3], w2[0]:w2[2]]
        a2 = crop2[..., 3] > 127
        h2, ww2 = a2.shape
        img2 = torch.from_numpy(_on_white(crop2) / 255.0).float()
        f2 = cu.common_upscale(img2[None].movedim(-1, 1), SAM_SIZE, SAM_SIZE, "bilinear", crop="disabled").to(device=dev, dtype=dtype)
        r2 = (sel[0] - w2[0], sel[1] - w2[1], sel[2] - w2[0], sel[3] - w2[1])
        s2x, s2y = SAM_SIZE / ww2, SAM_SIZE / h2
        b2 = torch.tensor([[[r2[0] * s2x, r2[1] * s2y], [r2[2] * s2x, r2[3] * s2y]]], dtype=dtype, device=dev)
        p2 = _auto_point(a2, r2)
        m2 = segment(f2, b2, [(p2[0] * s2x, p2[1] * s2y, 1)] if p2 else [], refine, (h2, ww2), mm)
        rect2 = np.zeros((h2, ww2), bool)
        rect2[r2[1]:r2[3], r2[0]:r2[2]] = True
        m2 = _drop_small(m2 & rect2, DROP_SMALL) & a2
        out_m = np.zeros_like(m)
        x0, y0, x1, y1 = max(w2[0], win[0]), max(w2[1], win[1]), min(w2[2], win[2]), min(w2[3], win[3])
        out_m[y0 - win[1]:y1 - win[1], x0 - win[0]:x1 - win[0]] = m2[y0 - w2[1]:y1 - w2[1], x0 - w2[0]:x1 - w2[0]]
        return out_m

    with torch.inference_mode():
        for c in cases:
            gold_t = _transform(_rgba(Path(doc["project"]) / "refs" / c["name"]), c["flip"], c["rotate"])
            gx0, gy0, gx1, gy1 = c["bbox"]
            text = c.get("text") or ""
            for pad in grid["pad"]:
                for margin in grid["margin"]:
                    box_s = _expand(c["bbox"], margin, W, H)
                    wx0, wy0, wx1, wy1 = _expand(box_s, pad, W, H)
                    ww, wh = wx1 - wx0, wy1 - wy0
                    crop = sheet[wy0:wy1, wx0:wx1]
                    alpha = crop[..., 3] > 127
                    white = _on_white(crop)
                    gold = np.zeros((wh, ww), bool)
                    gold[gy0 - wy0:gy1 - wy0, gx0 - wx0:gx1 - wx0] = gold_t[..., 3] > 127
                    rx = (box_s[0] - wx0, box_s[1] - wy0, box_s[2] - wx0, box_s[3] - wy0)
                    rect = np.zeros((wh, ww), bool)
                    rect[rx[1]:rx[3], rx[0]:rx[2]] = True
                    img_hwc = torch.from_numpy(white / 255.0).float()
                    frame = cu.common_upscale(img_hwc[None].movedim(-1, 1), SAM_SIZE, SAM_SIZE, "bilinear", crop="disabled").to(device=dev, dtype=dtype)
                    sx, sy = SAM_SIZE / ww, SAM_SIZE / wh
                    box = torch.tensor([[[rx[0] * sx, rx[1] * sy], [rx[2] * sx, rx[3] * sy]]], dtype=dtype, device=dev)
                    auto = _auto_point(alpha, rx)
                    for prompt, refine, mm in ((p, r, x) for p in grid["prompt"] for r in grid["refine"] for x in grid["mm"]):
                        if prompt == "text" and (not text or mm != "auto"):
                            continue
                        pred0 = None
                        for fix in sorted(grid["fix"]):
                            cfg = {"prompt": prompt, "pad": pad, "refine": refine, "margin": margin, "fix": fix, "mm": mm}
                            points = [auto] if prompt in ("box_pt", "box_pt2") and auto else []
                            if fix:
                                if prompt in ("text", "box_pt2", "rect"):
                                    continue
                                fp = _fix_point(pred0, gold)
                                if fp is None:  # 고칠 곳이 없음 — 첫 결과 그대로
                                    rows.append({**rows[-1], **cfg, "points": rows[-1]["points"]})
                                    continue
                                points = points + [fp]
                            if prompt == "rect":
                                m = np.ones((wh, ww), bool)
                            elif prompt == "text":
                                m = detect_text(frame, img_hwc, text, refine, (wh, ww))
                            else:
                                m = segment(frame, box, [(p[0] * sx, p[1] * sy, p[2]) for p in points], refine, (wh, ww), mm)
                            m = _drop_small(m & rect, DROP_SMALL) & alpha
                            if prompt == "box_pt2" and m.any():
                                m = tighten(m, (wx0, wy0, wx1, wy1), pad, refine, mm)
                            if not fix:
                                pred0 = m
                            rows.append({"name": c["name"], **cfg, "window": [ww, wh], "points": points, **_scores(m, gold)})
                            if _cfg_of(cfg) == tuple(DEFAULT_CFG.values()):
                                _overlay(white, m, gold, out / "overlay" / (Path(c["name"]).stem + ".png"))
            done = [r for r in rows if r["name"] == c["name"]]
            best = {p: max((r["iou"] for r in done if r["prompt"] == p), default=None) for p in grid["prompt"]}
            dflt = [r["iou"] for r in done if _cfg_of(r) == tuple(DEFAULT_CFG.values())]
            print(f"{c['name']}: 최고 IoU " + " · ".join(f"{p} {v:.3f}" for p, v in best.items() if v is not None)
                  + (f" | 기본 {dflt[0]:.3f}" if dflt else ""))
    res = {"ckpt": ckpt, "device": str(dev), "dtype": str(dtype), "load_sec": round(load_sec, 2),
           "call_sec": {k: {"first": round(v[0], 3), "median": round(statistics.median(v[1:] or v), 3), "n": len(v)} for k, v in calls.items()},
           "vram_gb": None if a.cpu else {"loaded": round(vram_loaded, 2), "peak": round(torch.cuda.max_memory_allocated() / 2**30, 2)},
           "grid": a.grid, "cases": [c["name"] for c in cases], "rows": rows}
    (out / "results.json").write_text(json.dumps(res, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    _write_report(res, out / "results.md")
    print(f"→ {out / 'results.md'}")
    return 0


# ══════════════════════════════════════════════════════════════════════
# report
# ══════════════════════════════════════════════════════════════════════
def _write_report(res: dict, path: Path) -> None:
    rows = res["rows"]
    dflt = tuple(DEFAULT_CFG.values())
    cfgs = sorted({_cfg_of(r) for r in rows})
    calls = " · ".join(f"{k} 첫 {v['first']}s 중앙 {v['median']}s ({v['n']}회)" for k, v in res["call_sec"].items())
    lines = [f"# S2 U0 — SAM3 컷 대 사람 컷 ({res['ckpt']}, {res['device']} {res['dtype']}, 격자 {res.get('grid')})", "",
             f"사례 {len(res['cases'])} · 로드 {res['load_sec']}s · 호출(다듬기 포함) {calls}"
             + (f" · VRAM 로드 {res['vram_gb']['loaded']}GB · 최대 {res['vram_gb']['peak']}GB" if res.get("vram_gb") else ""), "",
             "| 프롬프트 | pad | refine | margin | 고침 점 | 멀티마스크 | 중앙 IoU | 최소 IoU | 중앙 경계F | IoU≥0.9 |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for cfg in cfgs:
        rs = [r for r in rows if _cfg_of(r) == cfg]
        ious = [r["iou"] for r in rs]
        mark = " ★기본" if cfg == dflt else ""
        lines.append(f"| {cfg[0]}{mark} | {cfg[1]} | {cfg[2]} | {cfg[3]} | {cfg[4]} | {cfg[5]} | {statistics.median(ious):.3f} | {min(ious):.3f} | "
                     f"{statistics.median(r['bf'] for r in rs):.3f} | {sum(i >= 0.9 for i in ious)}/{len(ious)} |")
    base = [r for r in rows if _cfg_of(r) == dflt]
    if base:
        med = statistics.median(r["iou"] for r in base)
        verdict = "SAM3 를 기본 방법으로" if med >= 0.9 else "다각형을 기본으로, SAM3 는 '시도' 단추"
        lines += ["", f"판정(§6.5): 기본 설정 중앙 IoU {med:.3f} → **{verdict}**", "",
                  "| 컷 | 창 | IoU | 경계F | 넘침 px | 모자람 px |", "|---|---|---|---|---|---|"]
        for r in base:
            lines.append(f"| {r['name']} | {r['window'][0]}×{r['window'][1]} | {r['iou']:.3f} | {r['bf']:.3f} | {r['fp']} | {r['fn']} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def cmd_report(a) -> int:
    p = Path(a.results)
    _write_report(json.loads(p.read_text(encoding="utf-8")), p.with_name("results.md"))
    print(f"→ {p.with_name('results.md')}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="eval_sam3_refcut", description="S2 U0 — SAM3 컷 실측(유료 호출 없음)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("locate")
    p.add_argument("--project", required=True)
    p.add_argument("--sheet", default="00_원본/refer_total.png")
    p.add_argument("--out", required=True)
    p.set_defaults(fn=cmd_locate)
    p = sub.add_parser("run")
    p.add_argument("--cases", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--ckpt", default="")
    p.add_argument("--grid", choices=list(GRIDS), default="full")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--names", default="", help="쉼표로 고른 컷 파일 이름만")
    p.add_argument("--cpu", action="store_true")
    p.add_argument("--min-free-gb", type=float, default=6.0)
    p.set_defaults(fn=cmd_run)
    p = sub.add_parser("report")
    p.add_argument("--results", required=True)
    p.set_defaults(fn=cmd_report)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
