"""BMK Design Patch — PSD 크롭 영역에 GPT 편집 후보를 준비·정합·톤 보정·마스크 합성해 풀해상도 Smart Object PSD 로 되돌리는 노드 묶음(M1).

배경
----
캐릭터 일러스트의 틀린 디자인(파츠)을 고칠 때 Photoshop 에서 하던 일은 이렇다.
  크롭 Rectangle 그리기 → 크롭 PNG 내보내기 → 크롭마다 GPT 프롬프트 작성·반복 실행 → 결과를 rect 에
  배치·축소 → 어긋난 결과 재변형 → 톤 보정(_2tone) → 마스크 → 그룹 정리.
Design Patch 는 그 사이의 반복 계산을 노드로 옮긴다. PSD 를 psd_tools 로 직접 읽고(PS 스크립트 불필요),
프로젝트 폴더의 매니페스트 JSON 하나를 유일한 원본(원장)으로 삼는다. 모든 노드는
"매니페스트 읽기 → 일 → 바뀐 것이 있을 때만 원자 저장 → 같은 핸들(rev 갱신) 출력" 의 멱등 노드다.
M1 은 유료 GPT 호출 없이 기존 결과(PSD 수확·폴더)로 전체 흐름을 재현한다(Run 노드는 M2).
명세: H:\\BmkNodeDesign\\MultiLayerCropEdit\\_proto\\M1_SPEC.md, 근거: _proto\\reports\\final.md.

모듈 구성 (규약 §1 의 노드 없는 보조 모듈 bmk_design_patch_<역할>.py)
- bmk_design_patch_store     매니페스트·원자 저장·ID·레이어명 파싱·용어집·레퍼런스 매칭·출력 크기·cell_key
- bmk_design_patch_psd       PSD 읽기(rect·베이스·크롭 소스·수확) / PSDWriter(픽셀·그룹·마스크·SO)
- bmk_design_patch_analysis  배치·가드 정합·톤 보정장·ΔE 자동 마스크·게이트·z순서
- bmk_design_patch_prompt    결정적 프롬프트 렌더러·변형·평탄화·매니페스트 → 스펙
이 파일은 노드와 노드 경계 변환(텐서 ↔ numpy, MASK 의미 반전)만 담는다.

노드 (CATEGORY BMK/Image, 공용 핸들 BMK_DP_PROJECT = {"schema","root","project","rev"} + 가지 설정)
---------------------------------------------------------------------------------------------------
- 핸들의 가지 설정: Analyze 는 "analyze"(분석 설정), Compose 는 "compose"(정합 사용·z순서 등)를 핸들에 실어 보낸다.
  하류 노드는 매니페스트가 아니라 **자기 상류** 의 설정을 쓰므로, 한 프로젝트에 Analyze/Compose 가지가 여럿이어도
  서로의 결과를 덮어쓰지 않는다(명세 §5 의 핸들 4키에 더한 확장).
- Project      : <base_dir>/bmk_design_patch/<project>/ 를 열거나 만든다. base_dir "" = ComfyUI input 폴더.
                 IS_CHANGED = 매니페스트·specs/·glossary.json 과 specs 가 가리키는 파일(guide·before_paste·refs) 지문
                 → 다른 노드가 원장을 바꾸면 다음 큐에서 하류 전체가 다시 확인된다(각 노드는 디스크 캐시로 즉시 끝남).
                 매니페스트의 project 이름은 폴더 이름에 맞춘다(복사한 폴더).
- Import PSD   : 크롭 영역 그룹의 shape rect, 베이스(I2I_base + 클린플레이트), 크롭 이미지 그룹 합성을 읽어
                 crops[] 를 만든다. 레이어명 "NNN_부위 | A: …; B: …" 에서 타깃 골격, refs_dir 에서 NNN_ 레퍼런스.
                 PSD 지문·그룹 설정이 같으면 다시 읽지 않는다. overview = 번호 박스 오버뷰.
- Candidate In : 후보 등록. psd_harvest = 최종 PSD 의 결과 그룹 SO(원본 bytes·quad·손 마스크·보임 여부),
                 folder = 폴더 이미지(파일명 앞부분 = 크롭 이름, quad = crop rect). 원본 bytes 는 cands/ 에
                 디코드 없이 저장(내용 주소). mapping_json 으로 크롭/타깃 지정·건너뛰기.
- Prepare      : 무료. 크롭마다 스펙(specs/<crop_id>.json 오버라이드 + 용어집) → 평탄화한 입력 이미지 →
                 변형별 결정적 프롬프트 → GPT 출력 크기 → cell_key 로 jobs[] 를 만든다(호출은 M2 Run).
- Analyze      : 후보마다 배치(LANCZOS) → 가드 정합(손 마스크가 있으면 정합 제외 영역) → ΔE 자동 마스크 →
                 저주파 톤 보정장 → 게이트. 결과는 analysis[key][params_hash] (같은 설정이면 건너뜀, 후보마다 최근
                 설정 4개까지 보관하고 밀려난 항목의 derived 파일은 지움).
- Compose      : pick 을 z순으로 베이스 위에 합성한 평탄화 미리보기 + 마스크 합집합. 설정(정합 사용, z순서)은
                 핸들 compose 로 하류 Export 에 넘어가 같은 배치로 쓰인다(미리보기 = PSD). 상류 Analyze 설정의 분석을 쓴다.
- Export PSD   : 00.Base / 02.크롭영역(외곽선, 숨김) / 03.수정 > 크롭 그룹 > 타깃 그룹(그룹 마스크 = 최종 마스크)
                 > 후보 SO(pick 보임·녹색, 대안 숨김·빨강) 구조의 PSD 를 output/design_patch/<project 폴더 이름>/ 에 쓴다.
                 정합은 SO quad 에 affine 으로(비파괴), color=toned 면 톤 보정한 풀해상도 PNG 를 임베드.
                 이미 있는 파일은 덮어쓰지 않는다(<prefix>_r<rev>_2 …). 병합 프리뷰는 레이어와 같은 순서로 직접 합성.
                 정합·z순서는 상류 Compose 설정(핸들), 없으면 기본값.

옵션 메모
---------
- 정합 행렬은 cand → base 정방향(크롭 로컬, 픽셀 중심). SO quad = analysis.quad_from_matrix(M, rect).
- 손 마스크(hand) = 수확한 PSD 의 레이어/부모 그룹 마스크. auto = Analyze 의 ΔE 자동 마스크(grow·feather 굽기).
  hand_else_auto = 손 마스크가 있으면 손, 없으면 auto. hand 인데 손 마스크가 없으면 마스크 없음(SO 전체 보임).
- 크롭과 맞지 않는 부분 패치 SO(quad 와 crop rect IoU < 0.5, 예: 생성형 채우기, 손으로 옮긴 조각)는 분석하지
  않고 수확한 quad·마스크 그대로 쓴다.
- PS 에서 원근/왜곡(Distort) 변형한 SO 의 quad 는 Compose/Export 에서 가장 가까운 평행사변형으로 근사한다(경고).
- Compose 의 mask 출력은 ComfyUI MASK 규약(LoadImage 알파 마스크와 같음): 1 = 패치 없음(투명), 0 = 패치 적용.
  JoinImageWithAlpha 에 넣으면 패치만 보이는 RGBA 가 된다. 패치 영역 = 1 이 필요하면 InvertMask.

매니페스트 추가 키(명세 §1 스키마 밖, "추가 키 허용")
- source.refs_dir / refs_fingerprint / glossary / glossary_fingerprint, crops[].layer_name / rect_source
- candidates[].hand_mask.bg, 분석 항목의 params / skipped / quad_reg / mask_area / thumb / tone.full
- exports[].hash / options / compose / bytes / smart_objects / problems
명세와 다른 모양: analysis 는 {key: {params_hash: 항목}}(명세는 {key: 항목}) — Analyze 가지가 여럿이어도 서로
밀어내지 않게. 핸들은 4키 + analyze / compose(위).

버전 이력
---------
v1 (2026-10, M1)
- 노드 7개(Project, Import PSD, Candidate In, Prepare, Analyze, Compose, Export PSD). 유료 API 호출 없음.
- 회귀(tools/test_bmk_design_patch.py): 03 PSD 가져오기 → 05 PSD 수확 → 분석 → 합성(05 렌더 대비 MAE)
  → SO PSD 출력 재열기 검사.
- 리뷰 수정: 같은 이름 크롭은 위치/ID 로 짝지음, 가지 설정(analyze/compose)을 핸들로, 분석 해시에 분석 대상 여부·
  손 마스크 위치, export 덮어쓰기 금지·폴더 이름 출력, 병합 프리뷰 직접 합성(RLE), 원근 quad 근사, 링크 입력 IS_CHANGED.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import math
import os
import re
import time
import unicodedata
from functools import lru_cache

import cv2
import numpy as np
import torch
from PIL import Image, ImageDraw, ImageFont

import comfy.utils
import folder_paths

try:
    from . import bmk_design_patch_analysis as an
    from . import bmk_design_patch_prompt as dprompt
    from . import bmk_design_patch_psd as dpsd
    from . import bmk_design_patch_store as store
except ImportError:  # 단독 실행/테스트
    import bmk_design_patch_analysis as an
    import bmk_design_patch_prompt as dprompt
    import bmk_design_patch_psd as dpsd
    import bmk_design_patch_store as store

logger = logging.getLogger(__name__)

_TAG = "[ComfyUI_BMK_Nodes::DesignPatch]"

PROJECT_TYPE = "BMK_DP_PROJECT"
_CATEGORY = "BMK/Image"
_ALIASES = ["multi layer crop edit", "design patch", "디자인 패치", "디자인 수정", "디자인 보정", "파츠 보정",
            "크롭 수정", "레퍼런스 보정", "psd"]

_MODELS = ["gpt-image-2.5-sunburst", "gpt-image-2.5-flare"]
_QUALITIES = ["max", "xhigh", "high"]
_REF_STYLES = ["at", "plain", "both"]
_REGISTER_MODES = ["guarded_affine", "translation", "off"]
_TONE_MODES = ["field", "global", "off"]
_ROI_PRIORS = ["none", "hand_bbox"]
_MASK_SOURCES = ["auto", "hand", "hand_else_auto"]
_EXPORT_MASK_SOURCES = ["hand_else_auto", "auto", "hand"]
_ZORDERS = ["auto_small_on_top", "harvest"]
_LAYER_MODES = ["smart_object", "pixel"]
_ALTERNATES = ["hidden_all", "none"]
_COLORS = ["toned", "raw"]
_SOURCES = ["psd_harvest", "folder"]

_ANALYSIS_VERSION = "dp-an/2"
_ANALYSIS_KEEP = 4  # 후보마다 남기는 분석 설정 수(Analyze 가지 여럿·파라미터 조정). 넘으면 가장 오래된 것을 지운다
_ANALYZE_DEFAULTS = {"register": "guarded_affine", "tone": "field", "tone_sigma": 16, "dE": 8.0, "grow": 9,
                     "feather_sigma": 3.5, "roi_prior": "none"}
_COMPOSE_DEFAULTS = {"mask_source": "hand_else_auto", "use_registration": True, "use_tone": True,
                     "zorder": "auto_small_on_top"}
_TONE_CLAMP = 12.0
_BACKGROUND = "opaque"  # cell_key 의 background (GPT 편집 결과는 불투명)
_EXPORT_VERSION = "dp-export/2"
_MIN_CROP_IOU = 0.5  # 이보다 크롭과 덜 겹치는 SO 는 부분 패치로 보고 분석하지 않음
_THUMB = 160
_SAFE_PREFIX_RE = re.compile(r"^[\w\-.가-힣 ]+$")


# ══════════════════════════════════════════════════════════════════════
# 공통 — 핸들 / 매니페스트 / 경로
# ══════════════════════════════════════════════════════════════════════
def _strip_path(p) -> str:
    return str(p or "").strip().strip('"').strip("'").strip()


def _project_root(project: str, base_dir: str) -> str:
    base = _strip_path(base_dir) or folder_paths.get_input_directory()
    return store.project_root(base, str(project or "").strip())


_BRANCH_KEYS = ("analyze", "compose")


def _handle(root: str, m: dict, src: dict | None = None, **branch) -> dict:
    """핸들 = {schema, root, project, rev} + 가지 설정(analyze / compose). src(입력 핸들)의 가지 설정을 이어받고
    branch 로 덮는다. 가지 설정은 ComfyUI 그래프를 따라 흐르므로 하류 노드는 자기 상류의 설정을 쓴다."""
    h = {"schema": store.SCHEMA, "root": root, "project": m["project"], "rev": int(m["rev"])}
    for k in _BRANCH_KEYS:
        v = branch[k] if k in branch else (src or {}).get(k)
        if v is not None:
            h[k] = v
    return h


def _open(project) -> tuple[str, dict]:
    if not isinstance(project, dict) or project.get("schema") != store.SCHEMA or not project.get("root"):
        raise ValueError(f"{_TAG} project 입력이 BMK Design Patch Project 핸들이 아닙니다")
    root = project["root"]
    if not os.path.isfile(os.path.join(root, store.MANIFEST_NAME)):
        raise ValueError(f"{_TAG} 매니페스트가 없습니다: {root} (BMK Design Patch Project 를 먼저 실행하세요)")
    return root, store.load_manifest(root)


def _json_default(o):
    if isinstance(o, np.generic):
        return o.item()
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(f"JSON 으로 바꿀 수 없는 값: {type(o).__name__}")


def _plain(obj):
    """매니페스트에 넣을 값을 JSON 기본형으로(numpy·튜플 제거) — 저장 전후 비교가 흔들리지 않게."""
    return json.loads(json.dumps(obj, ensure_ascii=False, default=_json_default))


def _snapshot(m: dict) -> str:
    return json.dumps(m, sort_keys=True, ensure_ascii=False, default=_json_default)


def _commit(root: str, m: dict, before: str, src: dict | None = None, **branch) -> dict:
    """바뀐 것이 있을 때만 저장(rev += 1). 같은 입력의 재실행은 rev 를 올리지 않는다."""
    if _snapshot(m) != before:
        store.save_manifest(root, m)
    return _handle(root, m, src, **branch)


def _abs(root: str, rel: str) -> str:
    return store.resolve_path(root, rel)


def _file_fp(path: str):
    try:
        return store.file_fingerprint(path)
    except ValueError:
        return None


def _dir_fp(path: str):
    """폴더 바로 아래 파일 (이름, 크기, mtime) 목록의 해시. 없으면 None."""
    if not path or not os.path.isdir(path):
        return None
    rows = []
    for fn in sorted(os.listdir(path)):
        full = os.path.join(path, fn)
        if os.path.isfile(full):
            st = os.stat(full)
            rows.append(f"{fn}|{st.st_size}|{st.st_mtime_ns}")
    return hashlib.sha1("\n".join(rows).encode("utf-8")).hexdigest()[:16]


def _spec_files_fp(root: str) -> list:
    """specs/*.json 이 가리키는 파일(guide, before_paste, targets[].refs)과 매니페스트 타깃 refs 의 지문.
    경로 해석은 Prepare 와 같다(guide·before_paste = 프로젝트 폴더 기준, refs = refs_dir 기준). 가이드 그림이나
    하위 폴더 레퍼런스만 바뀌어도 Project 지문이 바뀌어 Prepare 가 다시 실행된다."""
    try:
        m = store.load_manifest(root)
    except ValueError:  # 깨진 매니페스트: 지문은 _file_fp 가 이미 반영
        return []
    refs_dir = (m.get("source") or {}).get("refs_dir") or ""

    def ref_path(r) -> str:
        r = str(r)
        return r if os.path.isabs(r) or not refs_dir else os.path.join(refs_dir, r)

    paths = [ref_path(r) for c in m["crops"] for t in c.get("targets") or [] for r in t.get("refs") or []]
    sd = os.path.join(root, "specs")
    for fn in sorted(os.listdir(sd)) if os.path.isdir(sd) else []:
        if not fn.endswith(".json"):
            continue
        try:
            ov = store.load_spec_override(root, fn[:-5])
        except ValueError:  # JSON 오류: 스펙 파일 자체의 지문은 _dir_fp(specs) 에 있다
            continue
        paths += [p if os.path.isabs(p) else os.path.join(root, p)
                  for p in (ov.get("guide"), ov.get("before_paste")) if isinstance(p, str) and p]
        paths += [ref_path(r) for t in ov.get("targets") or [] if isinstance(t, dict) for r in t.get("refs") or []]
    return [(p, _file_fp(p)) for p in paths]


def _project_fp(root: str) -> str:
    return "|".join(str(v) for v in (_file_fp(os.path.join(root, store.MANIFEST_NAME)),
                                     _dir_fp(os.path.join(root, "specs")),
                                     _file_fp(os.path.join(root, "glossary.json")),
                                     _spec_files_fp(root)))


def _rect_iou(a, b) -> float:
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _sync_picks(m: dict) -> None:
    """picks 맵을 candidates[].picked 에서 다시 만든다(원본은 picked 플래그)."""
    picks: dict[str, list[str]] = {}
    for c in m["candidates"]:
        if c.get("picked"):
            picks.setdefault(f"{c['crop_id']}/{c['tid']}", []).append(c["key"])
    m["picks"] = picks


# ══════════════════════════════════════════════════════════════════════
# 공통 — 이미지
# ══════════════════════════════════════════════════════════════════════
def _to_image(rgb: np.ndarray) -> torch.Tensor:
    return torch.from_numpy(np.ascontiguousarray(rgb[..., :3]).astype(np.float32) / 255.0)[None]


def _read(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def _load_rgba(root: str, rel: str) -> np.ndarray:
    with Image.open(_abs(root, rel)) as im:
        return np.asarray(im.convert("RGBA"))


def _load_rgb(root: str, rel: str) -> np.ndarray:
    with Image.open(_abs(root, rel)) as im:
        return np.asarray(im.convert("RGB"))


def _load_l(root: str, rel: str) -> np.ndarray:
    with Image.open(_abs(root, rel)) as im:
        return np.asarray(im.convert("L"))


def _decode(data: bytes) -> np.ndarray:
    """후보 원본 bytes → RGB, 부분 투명이 있으면 RGBA (uint8)."""
    with Image.open(io.BytesIO(data)) as im:
        im.load()
        if "A" in im.getbands() or (im.mode == "P" and "transparency" in im.info):
            rgba = np.asarray(im.convert("RGBA"))
            return rgba if int(rgba[..., 3].min()) < 255 else np.ascontiguousarray(rgba[..., :3])
        return np.asarray(im.convert("RGB"))


@lru_cache(maxsize=4)
def _font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def _fit(img: np.ndarray, box: int, bg=(40, 40, 40)) -> Image.Image:
    """box×box 칸 가운데에 비율 유지 축소. RGBA 는 bg 위에 합성."""
    pil = Image.fromarray(img)
    if pil.mode == "RGBA":
        under = Image.new("RGBA", pil.size, bg + (255,))
        pil = Image.alpha_composite(under, pil).convert("RGB")
    elif pil.mode != "RGB":
        pil = pil.convert("RGB")
    s = min(box / pil.width, box / pil.height)
    pil = pil.resize((max(1, round(pil.width * s)), max(1, round(pil.height * s))), Image.LANCZOS)
    cell = Image.new("RGB", (box, box), bg)
    cell.paste(pil, ((box - pil.width) // 2, (box - pil.height) // 2))
    return cell


def _grid(tiles: list[Image.Image], cols: int, bg=(24, 24, 24)) -> np.ndarray:
    if not tiles:
        im = Image.new("RGB", (320, 48), bg)
        ImageDraw.Draw(im).text((8, 14), "(empty)", fill=(200, 200, 200), font=_font(16))
        return np.asarray(im)
    tw = max(t.width for t in tiles)
    th = max(t.height for t in tiles)
    cols = max(1, min(cols, len(tiles)))
    rows = math.ceil(len(tiles) / cols)
    sheet = Image.new("RGB", (cols * tw, rows * th), bg)
    for i, t in enumerate(tiles):
        sheet.paste(t, ((i % cols) * tw, (i // cols) * th))
    return np.asarray(sheet)


def _label_tile(cells: list[Image.Image], lines: list[str], color=(220, 220, 220)) -> Image.Image:
    pad, lh = 4, 15
    w = sum(c.width for c in cells) + pad * (len(cells) + 1)
    h = max(c.height for c in cells) + pad * 2 + lh * len(lines)
    tile = Image.new("RGB", (w, h), (24, 24, 24))
    x = pad
    for c in cells:
        tile.paste(c, (x, pad))
        x += c.width + pad
    d = ImageDraw.Draw(tile)
    y = pad + max(c.height for c in cells) + 1
    for ln in lines:
        d.text((pad, y), ln, fill=color, font=_font(12))
        y += lh
    return tile


def _ascii(s: str) -> str:
    """기본 폰트에 한글 글리프가 없어 시트 라벨은 ASCII 만 쓴다."""
    return "".join(ch if 32 <= ord(ch) < 127 else "?" for ch in str(s))


# ══════════════════════════════════════════════════════════════════════
# 마스크 / 크롭 도우미
# ══════════════════════════════════════════════════════════════════════
def _mask_region(mask: dict | None, x0: int, y0: int, x1: int, y1: int) -> np.ndarray | None:
    """{"arr" uint8, "left", "top", "bg"} 마스크를 캔버스 창 [x0,y0,x1,y1] 로 잘라 float32 0..1. None 이면 None."""
    if mask is None:
        return None
    out = np.full((y1 - y0, x1 - x0), int(mask["bg"]), np.uint8)
    arr = mask["arr"]
    ax0, ay0 = int(mask["left"]), int(mask["top"])
    ix0, iy0 = max(x0, ax0), max(y0, ay0)
    ix1, iy1 = min(x1, ax0 + arr.shape[1]), min(y1, ay0 + arr.shape[0])
    if ix1 > ix0 and iy1 > iy0:
        out[iy0 - y0:iy1 - y0, ix0 - x0:ix1 - x0] = arr[iy0 - ay0:iy1 - ay0, ix0 - ax0:ix1 - ax0]
    return out.astype(np.float32) / 255.0


def _hand_mask(root: str, cand: dict) -> dict | None:
    hm = cand.get("hand_mask")
    if not hm:
        return None
    return {"arr": _load_l(root, hm["file"]), "left": int(hm["rect"][0]), "top": int(hm["rect"][1]),
            "bg": int(hm.get("bg", 0))}


def _hand_local(root: str, cand: dict, rect) -> np.ndarray | None:
    """손 마스크를 crop 로컬 HxW float32 로(창 밖 = 마스크 배경값)."""
    return _mask_region(_hand_mask(root, cand), *rect)


def _hand_roi(hand: np.ndarray | None):
    if hand is None:
        return None
    ys, xs = np.nonzero(hand >= 0.5)
    if not len(xs):
        return None
    x0, x1, y0, y1 = int(xs.min()), int(xs.max()) + 1, int(ys.min()), int(ys.max()) + 1
    px, py = 0.1 * (x1 - x0), 0.1 * (y1 - y0)
    return [x0 - px, y0 - py, x1 + px, y1 + py]


def _crop_map(m: dict) -> dict:
    return {c["id"]: c for c in m["crops"]}


def _analyzable(crop: dict, cand: dict) -> bool:
    """크롭 전체를 덮는 후보(GPT 결과)만 분석한다. 부분 패치 SO 는 수확한 quad·마스크 그대로."""
    return _rect_iou(dpsd.quad_bbox(cand["quad"]), crop["rect"]) >= _MIN_CROP_IOU


# ══════════════════════════════════════════════════════════════════════
# 분석 (Analyze 노드와 Compose/Export 의 누락분 보충이 공유)
# ══════════════════════════════════════════════════════════════════════
def _params_hash(crop: dict, cand: dict, prm: dict) -> str:
    """분석이 실제로 읽는 것: 후보·크롭 소스·rect, 분석 대상 여부(quad 가 크롭을 덮는지), 손 마스크(픽셀·위치·배경), 설정."""
    hm = cand.get("hand_mask") or {}
    payload = {"v": _ANALYSIS_VERSION, "cand": cand["file"], "src": crop["source"], "rect": crop["rect"],
               "analyzable": _analyzable(crop, cand),
               "hand": [hm.get("file"), hm.get("rect"), int(hm.get("bg", 0))] if hm else None, "prm": prm}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def _entry_valid(root: str, entry: dict | None, ph: str) -> bool:
    if not entry or entry.get("params_hash") != ph:
        return False
    if entry.get("skipped"):
        return True
    files = [entry.get("automask"), entry.get("thumb"), (entry.get("tone") or {}).get("delta")]
    return all(f is None or os.path.isfile(_abs(root, f)) for f in files)


def _save_delta(root: str, delta: np.ndarray, sigma: int, prefix: str) -> tuple[str, list[int]]:
    """톤 보정장은 저주파라 σ/4 격자로 줄여 float16 .npy 로 저장한다(읽을 때 선형 보간으로 복원)."""
    h, w = delta.shape[:2]
    f = max(1, int(sigma) // 4)
    small = cv2.resize(delta, (max(1, round(w / f)), max(1, round(h / f))), interpolation=cv2.INTER_AREA) if f > 1 else delta
    buf = io.BytesIO()
    np.save(buf, np.ascontiguousarray(small, dtype=np.float16), allow_pickle=False)
    return store.save_bytes_ca(root, "derived", buf.getvalue(), "npy", prefix=prefix), [w, h]


def _load_delta(root: str, tone: dict) -> np.ndarray:
    small = np.load(io.BytesIO(_read(_abs(root, tone["delta"]))), allow_pickle=False).astype(np.float32)
    w, h = tone["size"]
    if small.shape[:2] != (h, w):
        small = cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)
    return small


def _analyze_one(root: str, crop: dict, cand: dict, prm: dict, ph: str) -> dict:
    """후보 1개 분석 → analysis 항목(JSON 기본형). 산출 파일은 derived/<key>_<phash6>_* (내용 주소)."""
    if not _analyzable(crop, cand):
        return {"params_hash": ph, "params": dict(prm), "crop_id": crop["id"], "tid": cand["tid"],
                "skipped": "quad 가 크롭과 맞지 않는 부분 패치(IoU < 0.5) - 수확한 quad·마스크 그대로 사용"}
    x0, y0, x1, y1 = crop["rect"]
    w, h = x1 - x0, y1 - y0
    base = _load_rgba(root, crop["source"])
    full = _decode(_read(_abs(root, cand["file"])))
    G = an.place(np.ascontiguousarray(full[..., :3]), (w, h))
    hand = _hand_local(root, cand, crop["rect"])
    reg = an.register(base, G, mode=prm["register"], exclude_mask=hand)
    Ga = an.warp_affine(G, reg["matrix"]) if reg["applied"] else G
    roi = _hand_roi(hand) if prm["roi_prior"] == "hand_bbox" else None
    am = an.auto_mask(base, Ga, dE=float(prm["dE"]), grow=int(prm["grow"]), feather_sigma=float(prm["feather_sigma"]),
                      roi=roi, align=False)
    pre = f"{cand['key']}_{ph[:6]}_"
    tone = {"mode": prm["tone"], "delta": None}
    delta = None
    if prm["tone"] != "off":
        excl = am["mask"] > 0.01
        if hand is not None:
            excl |= hand >= 0.01
        tf = an.tone_field(base, Ga, excl.astype(np.float32), sigma=int(prm["tone_sigma"]), clamp=_TONE_CLAMP)
        tone["stats"] = tf["stats"]
        if "note" not in tf["stats"]:
            if prm["tone"] == "field":
                delta = tf["delta"]
            else:
                g = np.clip(np.asarray(tf["stats"]["mean_rgb"], np.float32), -_TONE_CLAMP, _TONE_CLAMP)
                delta = np.broadcast_to(g, (h, w, 3)).astype(np.float32)
            tone["delta"], tone["size"] = _save_delta(root, delta, int(prm["tone_sigma"]), pre + "tone_")
    Gt = an.apply_delta(Ga, delta) if delta is not None else Ga
    gates = an.gates(base, Gt, am["mask"], reg)
    am_rel = store.save_png_ca(root, "derived", am["mask"], prefix=pre + "am_")

    # 접촉시트 칸: 크롭 소스 | 정렬·톤 보정 후보 | 자동 마스크 오버레이(빨강) [+ 손 마스크 외곽 초록]
    mk = am["mask"][..., None]
    ov = (Gt.astype(np.float32) * (1 - 0.45 * mk) + np.float32([255, 40, 40]) * 0.45 * mk)
    if hand is not None:
        edge = cv2.morphologyEx((hand >= 0.5).astype(np.uint8), cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8)) > 0
        ov[edge] = (40, 255, 80)
    cells = [_fit(base, _THUMB), _fit(Gt, _THUMB), _fit(np.clip(ov, 0, 255).astype(np.uint8), _THUMB)]
    shift = an.quad_from_matrix(reg["matrix"], crop["rect"])
    disp = max(abs(a - b) for a, b in zip(shift, dpsd.quad_from_rect(crop["rect"])))
    lines = [_ascii(f"{crop['id']} {cand['tid']} {cand['key']}"),
             _ascii(f"reg {reg['kind']} {disp:.1f}px  s=({reg['sx']:.3f},{reg['sy']:.3f}) rot {reg['rot_deg']:+.2f}"),
             _ascii(f"mad {gates['outside_mad']:.1f} >24 {gates['outside_frac_gt24']:.2f} "
                    + ("FAIL " + ",".join(gates["fails"]) if gates["fails"] else "ok"))]
    tile = _label_tile(cells, lines, (255, 140, 120) if gates["fails"] else (220, 220, 220))
    thumb_rel = store.save_png_ca(root, "derived", np.asarray(tile), prefix=pre + "thumb_")

    reg_out = {k: reg[k] for k in ("matrix", "applied", "kind", "dx", "dy", "sx", "sy", "rot_deg", "shear",
                                   "med_before", "med_after", "improve", "disp", "masked")}
    return _plain({
        "params_hash": ph, "params": dict(prm), "crop_id": crop["id"], "tid": cand["tid"],
        "reg": reg_out, "quad_reg": an.quad_from_matrix(reg["matrix"], crop["rect"]),
        "tone": tone, "automask": am_rel, "components": am["components"][:16],
        "mask_area": round(float(am["mask"].sum()), 1), "gates": gates, "thumb": thumb_rel,
    })


def _derived_files(entry: dict) -> set:
    tone = entry.get("tone") or {}
    return {entry.get("automask"), entry.get("thumb"), tone.get("delta"), tone.get("full")} - {None}


def _drop_derived(root: str, old: dict, keep) -> None:
    """밀려난 분석 항목의 derived/ 파일을 지운다(남은 항목이 쓰는 파일은 둠). derived 는 다시 만들 수 있다."""
    used = set().union(*(_derived_files(e) for e in keep))
    for rel in _derived_files(old) - used:
        if rel.startswith("derived/"):
            try:
                os.remove(_abs(root, rel))
            except OSError:  # 이미 없음 / Windows 에서 뷰어가 잡고 있음
                pass


def _ensure_analysis(root: str, m: dict, cands: list[dict], prm: dict | None = None,
                     pbar=None) -> tuple[dict, int, int, list[str]]:
    """cands 의 분석 항목을 prm 설정(None = 기본값)으로 보장한다. prm = Analyze 노드 설정, Compose/Export 는 핸들의
    상류 Analyze 설정(없으면 기본값). analysis[key] = {params_hash: 항목}(삽입 순 = 오래된 것 먼저) — 같은 해시의
    항목이 있고 산출 파일이 있으면 건너뛰고, 후보마다 _ANALYSIS_KEEP 개를 넘으면 가장 오래된 항목과 그 파일을 지운다.
    반환 ({key: 이번 설정의 항목}, 계산 수, 캐시 적중 수, 경고)."""
    use = dict(prm or _ANALYZE_DEFAULTS)
    crops = _crop_map(m)
    entries: dict[str, dict] = {}
    done = hit = 0
    warns: list[str] = []
    for c in cands:
        crop = crops.get(c["crop_id"])
        if crop is None:
            warns.append(f"{c['key']}: 크롭 {c['crop_id']} 없음 → 분석 건너뜀")
        else:
            ph = _params_hash(crop, c, use)
            per = m["analysis"].get(c["key"])
            if not per or "params_hash" in per:  # 없음 / dp-an/1 의 평평한 항목(설정이 하나뿐이던 모양)
                per = m["analysis"][c["key"]] = {}
            if _entry_valid(root, per.get(ph), ph):
                hit += 1
            else:
                per[ph] = _analyze_one(root, crop, c, use, ph)
                done += 1
                while len(per) > _ANALYSIS_KEEP:
                    _drop_derived(root, per.pop(next(iter(per))), per.values())
            entries[c["key"]] = per[ph]
        if pbar is not None:
            pbar.update(1)
    return entries, done, hit, warns


# ══════════════════════════════════════════════════════════════════════
# 합성 계획 (Compose 와 Export 가 같은 순서·quad·마스크를 쓰도록 공유)
# ══════════════════════════════════════════════════════════════════════
def _auto_mask_dict(root: str, entry: dict | None, crop: dict) -> dict | None:
    if not entry or entry.get("skipped") or not entry.get("automask"):
        return None
    return {"arr": _load_l(root, entry["automask"]), "left": crop["rect"][0], "top": crop["rect"][1], "bg": 0}


def _final_mask(root: str, cand: dict, entry: dict | None, crop: dict, mask_source: str) -> tuple[dict | None, str]:
    hand = _hand_mask(root, cand)
    if mask_source == "hand":
        return hand, ("hand" if hand is not None else "none")
    if mask_source == "hand_else_auto" and hand is not None:
        return hand, "hand"
    auto = _auto_mask_dict(root, entry, crop)
    if auto is not None:
        return auto, "auto"
    return hand, ("hand" if hand is not None else "none")


def _eff_quad(cand: dict, entry: dict | None, use_registration: bool) -> list[float]:
    if use_registration and entry and not entry.get("skipped") and entry["reg"]["applied"]:
        return [float(v) for v in entry["quad_reg"]]
    return [float(v) for v in cand["quad"]]


def _target_label(crop: dict, tid: str) -> str:
    """레이어명의 타깃 조각: 라벨이 크롭 부위명과 같으면(레거시) tid, 아니면 라벨."""
    for t in crop.get("targets") or []:
        if t["tid"] == tid:
            lab = (t.get("label") or "").strip()
            if lab and lab != crop.get("part"):
                return lab
    return tid


def _plan(root: str, m: dict, entries: dict, mask_source: str, use_registration: bool, zorder: str, alternates: bool,
          warns: list[str]) -> list[dict]:
    """[{crop, targets: [{tid, label, slices: [{cand, entry, quad, mask, msrc, area}], alts: [...]}]}] (아래 → 위).
    entries = _ensure_analysis 가 돌려준 {key: 분석 항목}(분석이 필요 없는 설정이면 {})."""
    crops = _crop_map(m)
    groups: dict[tuple[str, str], dict] = {}
    order_idx = {c["key"]: i for i, c in enumerate(m["candidates"])}
    for c in m["candidates"]:
        crop = crops.get(c["crop_id"])
        if crop is None:
            warns.append(f"{c['key']}: 매니페스트에 크롭 {c['crop_id']} 가 없음(다시 가져온 PSD 에서 사라짐) → 제외")
            continue
        if not c.get("picked") and not alternates:
            continue
        entry = entries.get(c["key"])
        mask, msrc = _final_mask(root, c, entry, crop, mask_source)
        if mask_source != "hand" and msrc == "none":
            warns.append(f"{c['key']} ({crop['name']}): 손 마스크도 자동 마스크도 없음 → 마스크 없이(SO 전체 보임)")
        quad = _eff_quad(c, entry, use_registration)
        if dpsd.quad_kind(quad) == "perspective":  # PS Distort/Perspective — 쓰기·캐시 렌더는 affine 만
            sw, sh = c["src_size"]
            quad = an.quad_from_matrix(an.matrix_from_quad(quad, [0, 0, sw, sh]), [0, 0, sw, sh])
            warns.append(f"{c['key']} ({(c.get('origin_info') or {}).get('layer') or crop['name']}): 원근(Distort) quad 는 "
                         f"지원하지 않아 가장 가까운 평행사변형으로 근사")
        bb = dpsd.quad_bbox(quad)
        reg_m = _mask_region(mask, *bb)
        area = float(reg_m.sum()) if reg_m is not None else float((bb[2] - bb[0]) * (bb[3] - bb[1]))
        z = c.get("z")
        item = {"cand": c, "entry": entry, "quad": quad, "mask": mask, "msrc": msrc, "area": area,
                "z": (0, int(z)) if z is not None else (1, order_idx[c["key"]])}
        g = groups.setdefault((c["crop_id"], c["tid"]), {"slices": [], "alts": []})
        g["slices" if c.get("picked") else "alts"].append(item)

    def order(items, area_of, z_of):
        if zorder == "harvest":
            return sorted(items, key=z_of)
        keyed = {str(i): it for i, it in enumerate(items)}
        return [keyed[k] for k in an.zorder([{"key": k, "area": area_of(it)} for k, it in keyed.items()])]

    by_crop: dict[str, list[dict]] = {}
    for (cid, tid), g in groups.items():
        g["slices"] = order(g["slices"], lambda it: it["area"], lambda it: it["z"])
        g["alts"] = sorted(g["alts"], key=lambda it: it["z"])
        members = g["slices"] or g["alts"]
        by_crop.setdefault(cid, []).append({
            "tid": tid, "label": _target_label(crops[cid], tid), "slices": g["slices"], "alts": g["alts"],
            "area": sum(it["area"] for it in g["slices"]), "z": min(it["z"] for it in members)})
    plan = []
    for cid, targets in by_crop.items():
        targets = order(targets, lambda t: t["area"], lambda t: t["z"])
        shown = [t for t in targets if t["slices"]] or targets  # 숨김 대안만 있는 타깃이 크롭 순서를 바꾸지 않게(Compose = Export)
        plan.append({"crop": crops[cid], "targets": targets, "area": sum(t["area"] for t in targets),
                     "z": min(t["z"] for t in shown)})
    return order(plan, lambda p: p["area"], lambda p: p["z"])


def _cand_pixels(root: str, item: dict, toned: bool) -> tuple[np.ndarray, bool]:
    """후보 풀해상도 픽셀(RGB 또는 RGBA uint8). toned 이고 톤 보정장이 있으면 적용(원본 좌표로 되돌려서)."""
    c, entry = item["cand"], item["entry"]
    tone = (entry or {}).get("tone") or {}
    if toned and entry is not None and not entry.get("skipped") and tone.get("full") \
            and os.path.isfile(_abs(root, tone["full"])):
        return _decode(_read(_abs(root, tone["full"]))), True
    full = _decode(_read(_abs(root, c["file"])))
    if not toned or entry is None or entry.get("skipped") or not tone.get("delta"):
        return full, False
    delta = _load_delta(root, tone)
    if entry["reg"]["applied"]:
        delta = an.warp_affine(delta, entry["reg"]["matrix"], inverse=True)
    return an.apply_delta(full, delta, out_size=(full.shape[1], full.shape[0])), True


def _render(pix: np.ndarray, quad, mask: dict | None):
    """SO 캐시(PS 가 다시 그리기 전 보이는 픽셀) + 실효 알파(캐시 알파 × 마스크) → (rgb, alpha8|None, eff, left, top)."""
    rgb, alpha, left, top = dpsd.render_so_cache(pix, quad)
    h, w = rgb.shape[:2]
    eff = np.ones((h, w), np.float32) if alpha is None else alpha.astype(np.float32) / 255.0
    mk = _mask_region(mask, left, top, left + w, top + h)
    if mk is not None:
        eff *= mk
    return rgb, alpha, eff, left, top


def _clip_box(shape, eff: np.ndarray, left: int, top: int):
    H, W = shape[:2]
    h, w = eff.shape
    return max(0, left), max(0, top), min(W, left + w), min(H, top + h)


def _union_add(union: np.ndarray, eff: np.ndarray, left: int, top: int) -> None:
    x0, y0, x1, y1 = _clip_box(union.shape, eff, left, top)
    if x1 > x0 and y1 > y0:
        union[y0:y1, x0:x1] = np.maximum(union[y0:y1, x0:x1], eff[y0 - top:y1 - top, x0 - left:x1 - left])


def _paste(canvas: np.ndarray, union: np.ndarray, rgb: np.ndarray, eff: np.ndarray, left: int, top: int) -> None:
    """normal blend: canvas += (src − canvas)·eff, union = max(union, eff). 캔버스 밖은 잘라낸다."""
    x0, y0, x1, y1 = _clip_box(canvas.shape, eff, left, top)
    if x1 <= x0 or y1 <= y0:
        return
    a = eff[y0 - top:y1 - top, x0 - left:x1 - left, None]
    src = rgb[y0 - top:y1 - top, x0 - left:x1 - left].astype(np.float32)
    dst = canvas[y0:y1, x0:x1]
    canvas[y0:y1, x0:x1] = dst + (src - dst) * a
    _union_add(union, eff, left, top)


def _slices_in_order(plan: list[dict]):
    for p in plan:
        for t in p["targets"]:
            for it in t["slices"]:
                yield p, t, it


def _needs_analysis(mask_source: str, use_registration: bool, toned: bool) -> bool:
    return mask_source != "hand" or use_registration or toned


# ══════════════════════════════════════════════════════════════════════
# 노드 1 — Project
# ══════════════════════════════════════════════════════════════════════
def _summary(root: str, m: dict) -> str:
    n_pick = sum(1 for c in m["candidates"] if c.get("picked"))
    src = (m.get("source") or {}).get("psd") or "-"
    last = m["exports"][-1]["psd"] if m["exports"] else "-"
    return (f"{m['project']} rev {m['rev']} | 크롭 {len(m['crops'])} · 후보 {len(m['candidates'])} (pick {n_pick}) · "
            f"작업 {len(m['jobs'])} · 분석 {len(m['analysis'])} · export {len(m['exports'])}\n"
            f"폴더: {root}\nPSD: {src}\n마지막 export: {last}")


class BMKDesignPatchProject:
    """Design Patch 프로젝트 폴더(매니페스트)를 열거나 만든다."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "project": ("STRING", {
                    "default": "design_patch",
                    "tooltip": "프로젝트 이름(영문·숫자·한글·_ - .). <base_dir>/bmk_design_patch/<이름>/ 폴더가 원장입니다."}),
                "base_dir": ("STRING", {
                    "default": "",
                    "tooltip": "프로젝트들을 둘 폴더. 비우면 ComfyUI input 디렉터리."}),
            },
        }

    RETURN_TYPES = (PROJECT_TYPE, "STRING")
    RETURN_NAMES = ("project", "summary")
    OUTPUT_TOOLTIPS = ("다른 Design Patch 노드에 연결하는 핸들(텐서 없음).", "매니페스트 요약.")
    FUNCTION = "open"
    CATEGORY = _CATEGORY
    DESCRIPTION = (
        "Design Patch 프로젝트(크롭·후보·분석·작업을 기록하는 매니페스트 폴더)를 열거나 만듭니다. "
        "다른 노드가 매니페스트를 바꾸면 다음 실행에서 하류가 다시 확인되며, 각 노드는 디스크 캐시로 바로 끝납니다."
    )
    SEARCH_ALIASES = _ALIASES + ["project", "manifest", "프로젝트", "원장"]

    def open(self, project, base_dir):
        root = _project_root(project, base_dir)
        os.makedirs(root, exist_ok=True)
        m = store.load_manifest(root)
        name = os.path.basename(root)
        if m["project"] != name or not os.path.isfile(os.path.join(root, store.MANIFEST_NAME)):
            m["project"] = name  # 복사·이름을 바꾼 폴더: 핸들·요약은 폴더 이름을 따른다
            store.save_manifest(root, m)
        logger.info("%s Project %s rev %d (%s)", _TAG, m["project"], m["rev"], root)
        return (_handle(root, m), _summary(root, m))

    @classmethod
    def IS_CHANGED(cls, project="", base_dir="", **kwargs):
        if project is None or base_dir is None:  # 링크된 입력: 값이 보이지 않으니 매번 다시 확인
            return float("nan")
        root = _project_root(project, base_dir)
        return f"{root}|{_project_fp(root)}"


# ══════════════════════════════════════════════════════════════════════
# 노드 2 — Import PSD
# ══════════════════════════════════════════════════════════════════════
def _new_targets(parsed: dict, old_crop: dict | None) -> list[dict]:
    """레이어명 타깃 → 매니페스트 타깃. 영문(ASCII) 문구는 name_en, 한글 문구·레거시는 label(번역은 Prepare)."""
    old = {t["tid"]: t for t in (old_crop or {}).get("targets") or []}
    out = []
    for t in parsed["targets"]:
        text = t["text"].strip()
        label = text or parsed["part"] or parsed["name"]
        name_en = text if (text and text.isascii()) else ""
        tt = {"tid": t["tid"], "label": label, "name_en": name_en, "mode": "ref_correct", "refs": []}
        o = old.get(t["tid"])
        if o and o.get("label") == label:  # 사람이 매니페스트에서 고친 값은 유지
            for k in ("name_en", "mode", "refs"):
                if o.get(k) and not (k == "name_en" and name_en):
                    tt[k] = o[k]
        out.append(tt)
    return out


def _overview(base: np.ndarray, crops: list[dict], max_side: int = 1600) -> np.ndarray:
    H, W = base.shape[:2]
    s = min(1.0, max_side / max(W, H))
    im = Image.fromarray(base[..., :3]).resize((max(1, round(W * s)), max(1, round(H * s))), Image.LANCZOS)
    d = ImageDraw.Draw(im)
    font = _font(16)
    for i, c in enumerate(crops):
        x0, y0, x1, y1 = (round(v * s) for v in c["rect"])
        col = (255, 150, 0) if c.get("alpha_frac", 0) > 0 else ((255, 60, 60) if c.get("warnings") else (40, 230, 90))
        d.rectangle([x0, y0, x1 - 1, y1 - 1], outline=col, width=3)
        tag = f"{i + 1}:{c.get('nnn') or '?'}"
        tw = d.textlength(tag, font=font)
        d.rectangle([x0, y0, x0 + tw + 6, y0 + 19], fill=col)
        d.text((x0 + 3, y0 + 1), tag, fill=(0, 0, 0), font=font)
    return np.asarray(im)


class BMKDesignPatchImportPSD:
    """PSD 의 크롭 rect·베이스·크롭 소스를 읽어 매니페스트 crops[] 를 만든다."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "project": (PROJECT_TYPE, {"tooltip": "BMK Design Patch Project 출력."}),
                "psd_path": ("STRING", {"default": "", "tooltip": "크롭 영역(Rectangle)을 그린 PSD 경로. 따옴표는 무시."}),
                "crop_group": ("STRING", {"default": "auto",
                                          "tooltip": "크롭 shape 레이어 그룹. auto = '02.크롭영역' 또는 '크롭영역'."}),
                "clean_plate_layer": ("STRING", {"default": "auto",
                                                 "tooltip": "클린플레이트 픽셀 레이어. auto = 'non-gpt 1차수정' / '00.Base_마스크용', none = 안 씀."}),
                "base_layer": ("STRING", {"default": "auto",
                                          "tooltip": "베이스 레이어. auto = 'I2I_base'(없으면 아래와 같음), "
                                                     "none = 보이는 레이어 합성(shape·크롭 그룹 제외, 여백도 불투명)."}),
                "crop_pixels_group": ("STRING", {"default": "auto",
                                                 "tooltip": "크롭별 사전 편집 그룹(같은 이름 하위 그룹/레이어를 크롭 소스에 합성). "
                                                            "auto = '크롭이미지' / '01.크롭', none = 베이스만."}),
                "refs_dir": ("STRING", {"default": "", "tooltip": "레퍼런스 폴더(NNN_ 로 시작하는 이미지를 크롭에 자동 매칭). 비우면 안 씀."}),
                "glossary_path": ("STRING", {"default": "",
                                             "tooltip": "한→영 용어집 JSON(선택). 프로젝트 폴더의 glossary.json 위에 덮어씁니다."}),
            },
        }

    RETURN_TYPES = (PROJECT_TYPE, "IMAGE", "STRING")
    RETURN_NAMES = ("project", "overview", "report")
    OUTPUT_TOOLTIPS = ("갱신된 프로젝트 핸들.", "캔버스 + 번호 박스(초록 정상, 주황 투명 여백, 빨강 경고).", "크롭 목록과 경고.")
    FUNCTION = "run"
    CATEGORY = _CATEGORY
    DESCRIPTION = (
        "PSD 를 psd_tools 로 직접 읽어 크롭 rect(shape 벡터), 베이스(I2I_base + 클린플레이트), 크롭 그룹 사전 편집을 합성한 "
        "크롭 소스를 매니페스트에 기록합니다. 레이어명 'NNN_부위 | A: …; B: …' 로 타깃을, refs_dir 에서 레퍼런스를 매칭합니다."
    )
    SEARCH_ALIASES = _ALIASES + ["import psd", "crop rect", "psd 가져오기", "크롭 영역", "클린플레이트"]

    def run(self, project, psd_path, crop_group, clean_plate_layer, base_layer, crop_pixels_group, refs_dir,
            glossary_path):
        t0 = time.time()
        root, m = _open(project)
        before = _snapshot(m)
        psd_path = _strip_path(psd_path)
        refs_dir = _strip_path(refs_dir)
        glossary_path = _strip_path(glossary_path)
        if not os.path.isfile(psd_path):
            raise ValueError(f"{_TAG} PSD 파일을 찾을 수 없습니다: {psd_path!r}")
        if refs_dir and not os.path.isdir(refs_dir):
            raise ValueError(f"{_TAG} 레퍼런스 폴더가 없습니다: {refs_dir!r}")
        if glossary_path and not os.path.isfile(glossary_path):
            raise ValueError(f"{_TAG} 용어집 파일이 없습니다: {glossary_path!r}")
        heavy = {"psd": psd_path.replace("\\", "/"), "fingerprint": store.file_fingerprint(psd_path),
                 "crop_group": crop_group, "clean_plate_layer": clean_plate_layer, "base_layer": base_layer,
                 "crop_pixels_group": crop_pixels_group}
        src = m["source"]
        cached = (all(src.get(k) == v for k, v in heavy.items()) and m["base"] and m["crops"]
                  and os.path.isfile(_abs(root, m["base"]))
                  and all(os.path.isfile(_abs(root, c["source"])) for c in m["crops"]))
        info_line = "캐시 적중(PSD·그룹 설정 같음, 다시 읽지 않음)"
        if not cached:
            info_line = self._import(root, m, psd_path, heavy)
        light = {"refs_dir": refs_dir.replace("\\", "/"), "refs_fingerprint": _dir_fp(refs_dir),
                 "glossary": glossary_path.replace("\\", "/"), "glossary_fingerprint": _file_fp(glossary_path)}
        if not cached or any(src.get(k) != v for k, v in light.items()):
            for c in m["crops"]:
                c["refs_auto"] = store.match_refs(refs_dir, c["nnn"], part=c["part"]) if refs_dir and c["nnn"] else []
            m["source"].update(light)
        handle = _commit(root, m, before, project)
        base = _load_rgb(root, m["base"])
        report = self._report(m, psd_path, info_line, time.time() - t0)
        logger.info("%s %s", _TAG, report.splitlines()[0])
        return (handle, _to_image(_overview(base, m["crops"])), report)

    @staticmethod
    def _import(root: str, m: dict, psd_path: str, heavy: dict) -> str:
        t0 = time.time()
        psd = dpsd.open_psd(psd_path)
        rects = dpsd.read_crop_rects(psd, heavy["crop_group"])
        if not rects:
            raise ValueError(f"{_TAG} 크롭 영역 그룹에 shape 레이어가 없습니다: {psd_path}")
        pbar = comfy.utils.ProgressBar(len(rects) + 2)
        base_rgb, binfo = dpsd.read_base(psd, heavy["clean_plate_layer"], heavy["base_layer"])
        pbar.update(1)
        parsed = [store.parse_crop_layer_name(r["name"]) for r in rects]
        named = [{"name": p["name"], "rect": r["rect"]} for p, r in zip(parsed, rects)]
        srcs = dpsd.crop_sources(psd, named, base_rgb, heavy["crop_pixels_group"], work_rect=binfo["work_rect"])
        pbar.update(1)
        ids = store.assign_crop_ids([p["name"] for p in parsed])
        old = {c["id"]: c for c in m["crops"]}
        rect_count: dict[tuple, int] = {}
        for r in rects:
            rect_count[tuple(r["rect"])] = rect_count.get(tuple(r["rect"]), 0) + 1
        name_count: dict[str, int] = {}
        for p in parsed:
            name_count[p["name"]] = name_count.get(p["name"], 0) + 1
        crops = []
        for cid, p, r, s in zip(ids, parsed, rects, srcs):
            warns = list(r["warnings"]) + list(p["warnings"]) + list(s["warnings"])
            if s["alpha_frac"] > 0:
                warns.append(f"투명 여백 {s['alpha_frac'] * 100:.1f}% → Prepare 가 가장자리 복제로 평탄화")
            if rect_count[tuple(r["rect"])] > 1:
                warns.append("다른 크롭과 rect 가 같음(타깃을 한 크롭의 '| A: …; B: …' 로 합치는 것을 권장)")
            if name_count[p["name"]] > 1:
                warns.append("같은 이름의 크롭이 여러 개(ID 에 -2.. 접미, 폴더 후보·mapping_json 은 ID 로 지정)")
            crops.append({
                "id": cid, "name": p["name"], "nnn": p["nnn"], "part": p["part"], "layer_name": r["name"],
                "rect": [int(v) for v in r["rect"]], "rect_source": r["rect_source"],
                "source": store.save_png_ca(root, "inputs", s["rgba"]), "source_kind": s["kind"],
                "alpha_frac": round(float(s["alpha_frac"]), 6),
                "targets": _new_targets(p, old.get(cid)), "refs_auto": [], "warnings": warns,
            })
            pbar.update(1)
        m["crops"] = _plain(crops)
        m["canvas"] = list(psd.size)
        m["work_rect"] = [int(v) for v in binfo["work_rect"]]
        m["source"].update(heavy)
        m["base"] = store.save_png_ca(root, "inputs", base_rgb)
        line = (f"PSD 읽기 {time.time() - t0:.1f}s · 베이스 {binfo['method']} "
                f"({binfo.get('base_layer')} + {binfo.get('clean_plate_layer')}) · work_rect {m['work_rect']}")
        return "\n".join([line] + [f"  - 베이스: {w}" for w in binfo["warnings"]])

    @staticmethod
    def _report(m: dict, psd_path: str, info_line: str, sec: float) -> str:
        n_warn = sum(1 for c in m["crops"] if c["warnings"])
        lines = [f"Import PSD: {os.path.basename(psd_path)} {m['canvas'][0]}x{m['canvas'][1]} | 크롭 {len(m['crops'])}"
                 f" (경고 {n_warn}) | rev {m['rev']} | {sec:.1f}s", info_line]
        known = {c["id"] for c in m["crops"]}
        orphans = sorted({c["crop_id"] for c in m["candidates"] if c["crop_id"] not in known})
        if orphans:
            lines.append(f"주의: 매니페스트 후보가 가리키는 크롭이 PSD 에서 사라짐: {', '.join(orphans)}")
        for i, c in enumerate(m["crops"]):
            x0, y0, x1, y1 = c["rect"]
            tg = "; ".join(f"{t['tid']}: {t['name_en'] or t['label']}" for t in c["targets"])
            lines.append(f"{i + 1:2d}. {c['id']} {c['name']} {x1 - x0}x{y1 - y0} [{c['source_kind']}] "
                         f"refs {len(c['refs_auto'])} | {tg}")
            for wmsg in c["warnings"]:
                lines.append(f"      - {wmsg}")
        return "\n".join(lines)

    @classmethod
    def IS_CHANGED(cls, psd_path="", refs_dir="", glossary_path="", **kwargs):
        if psd_path is None or refs_dir is None or glossary_path is None:  # 링크된 경로: 파일 지문을 볼 수 없음
            return float("nan")
        psd_path, refs_dir, glossary_path = (_strip_path(v) for v in (psd_path, refs_dir, glossary_path))
        vals = {k: kwargs.get(k) for k in ("crop_group", "clean_plate_layer", "base_layer", "crop_pixels_group")}
        return json.dumps([psd_path, _file_fp(psd_path), refs_dir, _dir_fp(refs_dir), glossary_path,
                           _file_fp(glossary_path), vals], ensure_ascii=False)


# ══════════════════════════════════════════════════════════════════════
# 노드 3 — Candidate In
# ══════════════════════════════════════════════════════════════════════
def _parse_mapping(text: str) -> dict:
    """mapping_json: {"<레이어명/파일명>": {"crop": "<크롭 이름|ID>", "tid": "B", "skip": true}} 또는 {"이름": "B"}."""
    text = (text or "").strip()
    if not text:
        return {}
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"{_TAG} mapping_json 형식 오류: {e}") from e
    if not isinstance(raw, dict):
        raise ValueError(f"{_TAG} mapping_json 은 객체여야 합니다")
    out = {}
    for k, v in raw.items():
        if isinstance(v, str):
            v = {"tid": v}
        if not isinstance(v, dict):
            raise ValueError(f"{_TAG} mapping_json 값은 객체 또는 타깃 문자열이어야 합니다: {k!r}")
        out[unicodedata.normalize("NFC", str(k))] = v
    return out


def _resolve_crop(m: dict, ref: str) -> dict | None:
    for c in m["crops"]:
        if ref in (c["id"], c["name"]):
            return c
    return None


def _crop_for_filename(stem: str, crops: list[dict]) -> tuple[dict | None, str]:
    """파일명 앞부분 = 크롭 이름(가장 긴 일치) → 아니면 NNN 이 유일한 크롭. 같은 이름의 크롭이 여럿이면 모호함."""
    s = unicodedata.normalize("NFC", stem).casefold()
    best = None
    for c in crops:
        nm = unicodedata.normalize("NFC", c["name"]).casefold()
        if s.startswith(nm) and (best is None or len(nm) > len(best[1])):
            best = (c, nm)
    if best:
        n_same = sum(1 for c in crops if unicodedata.normalize("NFC", c["name"]).casefold() == best[1])
        if n_same > 1:
            return None, f"크롭 이름 {best[0]['name']} 이 {n_same}개라 모호함 - mapping_json 에 크롭 ID 로 지정"
        return best[0], "name"
    mt = re.match(r"^(\d+)_", stem)
    if mt:
        same = [c for c in crops if c["nnn"] == mt.group(1)]
        if len(same) == 1:
            return same[0], "nnn"
        if len(same) > 1:
            return None, f"NNN {mt.group(1)} 크롭이 {len(same)}개라 모호함"
    return None, "파일명이 크롭 이름/NNN 으로 시작하지 않음"


class BMKDesignPatchCandidateIn:
    """후보 등록: PSD 수확(SO 원본 bytes·quad·손 마스크) 또는 폴더 이미지."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "project": (PROJECT_TYPE, {"tooltip": "BMK Design Patch Project 출력(Import PSD 이후)."}),
                "source": (_SOURCES, {"default": "psd_harvest",
                                      "tooltip": "psd_harvest = 마감한 PSD 의 결과 그룹 SO 를 수확. folder = 폴더의 이미지."}),
                "path": ("STRING", {"default": "", "tooltip": "psd_harvest 면 PSD 경로, folder 면 폴더 경로."}),
                "group": ("STRING", {"default": "03.수정", "tooltip": "psd_harvest: 결과 그룹 이름(비우면 문서 전체)."}),
                "mapping_json": ("STRING", {"default": "", "multiline": True,
                                            "tooltip": '선택. {"레이어명/파일명": {"crop": "크롭 이름|ID", "tid": "B", "skip": true}} '
                                                       '또는 {"이름": "B"}(타깃만). 자동 매칭을 덮어씁니다.'}),
                "visible_is_pick": ("BOOLEAN", {"default": True,
                                                "tooltip": "psd_harvest: 보이는 SO 를 pick, 숨김을 대안으로. 끄면 기존 pick 을 유지."}),
                "keep_hand_masks": ("BOOLEAN", {"default": True,
                                                "tooltip": "psd_harvest: 레이어(또는 부모 그룹) 마스크를 손 마스크로 저장."}),
            },
        }

    RETURN_TYPES = (PROJECT_TYPE, "STRING")
    RETURN_NAMES = ("project", "report")
    FUNCTION = "run"
    CATEGORY = _CATEGORY
    DESCRIPTION = (
        "후보를 매니페스트에 등록합니다. psd_harvest 는 마감한 PSD 의 결과 그룹 SO 에서 원본 bytes·배치(quad)·마스크·보임 여부를, "
        "folder 는 폴더 이미지(파일명 = 크롭 이름으로 시작)를 읽어 원본 그대로 cands/ 에 저장합니다."
    )
    SEARCH_ALIASES = _ALIASES + ["candidate in", "harvest", "후보 등록", "수확", "결과 가져오기"]

    def run(self, project, source, path, group, mapping_json, visible_is_pick, keep_hand_masks):
        t0 = time.time()
        root, m = _open(project)
        before = _snapshot(m)
        if not m["crops"]:
            raise ValueError(f"{_TAG} 크롭이 없습니다 - BMK Design Patch Import PSD 를 먼저 실행하세요")
        if source not in _SOURCES:
            raise ValueError(f"{_TAG} source 는 {_SOURCES} 중 하나여야 합니다: {source!r}")
        path = _strip_path(path)
        mapping = _parse_mapping(mapping_json)
        warns: list[str] = []
        if source == "psd_harvest":
            new = self._harvest(root, m, path, group, mapping, bool(keep_hand_masks), warns)
        else:
            new = self._folder(root, m, path, mapping, warns)
        old = {c["key"]: c for c in m["candidates"]}
        seen = {c["key"] for c in new}
        origin_key = "psd" if source == "psd_harvest" else "folder"
        removed = [k for k, c in old.items() if k not in seen and c.get("origin") == source
                   and (c.get("origin_info") or {}).get(origin_key) == path]  # 같은 출처에서 사라진 후보
        added = updated = 0
        merged = [c for c in m["candidates"] if c["key"] not in seen and c["key"] not in removed]
        for c in new:
            prev = old.get(c["key"])
            if source == "folder" or not visible_is_pick:
                c["picked"] = bool(prev.get("picked")) if prev else False
            if prev is None:
                added += 1
            elif _snapshot(prev) != _snapshot(c):
                updated += 1
            merged.append(c)
        m["candidates"] = _plain(merged)
        for k in removed:
            m["analysis"].pop(k, None)
        if source == "folder":
            self._autopick(m, {c["key"] for c in new}, warns)
        _sync_picks(m)
        handle = _commit(root, m, before, project)
        per: dict[str, list[int]] = {}
        for c in m["candidates"]:
            row = per.setdefault(c["crop_id"], [0, 0])
            row[0] += 1
            row[1] += bool(c.get("picked"))
        names = {c["id"]: c["name"] for c in m["crops"]}
        lines = [f"Candidate In ({source}): {os.path.basename(path)} 항목 {len(new)} → 신규 {added} · 갱신 {updated} · "
                 f"제거 {len(removed)} | 전체 후보 {len(m['candidates'])}, pick {sum(len(v) for v in m['picks'].values())} "
                 f"| rev {m['rev']} | {time.time() - t0:.1f}s"]
        for cid, (n, p) in per.items():
            lines.append(f"  {cid} {names.get(cid, '(크롭 없음)')}: 후보 {n}, pick {p}")
        lines += [f"  - {w}" for w in warns]
        report = "\n".join(lines)
        logger.info("%s %s", _TAG, lines[0])
        return (handle, report)

    @staticmethod
    def _harvest(root, m, path, group, mapping, keep_masks, warns) -> list[dict]:
        if not os.path.isfile(path):
            raise ValueError(f"{_TAG} PSD 파일을 찾을 수 없습니다: {path!r}")
        psd = dpsd.open_psd(path)
        crops = [{"name": c["name"], "rect": c["rect"]} for c in m["crops"]]  # crop_index = m["crops"] 위치
        hv = dpsd.harvest_results(psd, (group or "").strip() or None, crops, warnings=warns)
        pbar = comfy.utils.ProgressBar(max(1, len(hv)))
        out, keys = [], set()
        for h in hv:
            pbar.update(1)
            mp = mapping.get(unicodedata.normalize("NFC", h["layer"]), {})
            if mp.get("skip"):
                continue
            if mp.get("crop"):
                crop = _resolve_crop(m, mp["crop"])
            else:
                crop = None if h["crop_index"] is None else m["crops"][h["crop_index"]]
            if crop is None:
                warns.append(f"{h['layer']}: 크롭을 찾지 못해 건너뜀 (mapping_json 으로 지정)")
                continue
            tids = [t["tid"] for t in crop["targets"]]
            tid = str(mp.get("tid") or tids[0]).upper()
            if tid not in tids:
                warns.append(f"{h['layer']}: 타깃 {tid} 가 {crop['name']} 에 없음 → {tids[0]}")
                tid = tids[0]
            rel = store.save_bytes_ca(root, "cands", h["data"], h["filetype"], prefix="h_")
            key = os.path.splitext(os.path.basename(rel))[0]
            n = 2
            while key in keys:  # 같은 임베드 원본을 쓰는 SO 레이어가 둘 이상
                key = f"{os.path.splitext(os.path.basename(rel))[0]}-{n}"
                n += 1
            keys.add(key)
            hand = None
            if keep_masks and h["mask"] is not None:
                hand = {"file": store.save_png_ca(root, "masks", h["mask"]["arr"], prefix=f"{key}_hand_"),
                        "rect": h["mask"]["rect"], "owner": h["mask"]["owner"], "bg": h["mask"]["bg"]}
            for wmsg in h["warnings"]:
                if "crop 매칭" not in wmsg:
                    warns.append(f"{h['layer']}: {wmsg}")
            out.append({
                "key": key, "crop_id": crop["id"], "tid": tid, "file": rel, "src_size": h["src_size"],
                "origin": "psd_harvest",
                "origin_info": {"psd": path, "layer": h["layer"], "group_path": h["group_path"],
                                "filetype": h["filetype_orig"], "category": h["category"], "z_index": h["z_index"],
                                "visible": h["visible_effective"], "crop_match": h["crop_match"]},
                "quad": h["quad"], "picked": bool(h["visible_effective"]), "z": h["z_index"], "hand_mask": hand,
            })
        return out

    @staticmethod
    def _folder(root, m, path, mapping, warns) -> list[dict]:
        if not os.path.isdir(path):
            raise ValueError(f"{_TAG} 폴더를 찾을 수 없습니다: {path!r}")
        files = sorted(f for f in os.listdir(path)
                       if os.path.isfile(os.path.join(path, f)) and os.path.splitext(f)[1].lower() in store.REF_EXTS)
        pbar = comfy.utils.ProgressBar(max(1, len(files)))
        out, keys = [], set()
        for fn in files:
            pbar.update(1)
            nfn = unicodedata.normalize("NFC", fn)
            stem = os.path.splitext(nfn)[0]
            mp = mapping.get(nfn) or mapping.get(stem) or {}
            if mp.get("skip"):
                continue
            if mp.get("crop"):
                crop, why = _resolve_crop(m, mp["crop"]), "mapping"
            else:
                crop, why = _crop_for_filename(stem, m["crops"])
            if crop is None:
                warns.append(f"{fn}: 건너뜀 - {why if why != 'mapping' else '매핑한 크롭 없음'}")
                continue
            data = _read(os.path.join(path, fn))
            try:
                pix = _decode(data)
            except OSError as e:
                warns.append(f"{fn}: 이미지를 열 수 없음 ({e})")
                continue
            x0, y0, x1, y1 = crop["rect"]
            if pix.shape[:2] == (y1 - y0, x1 - x0):
                src = _load_rgba(root, crop["source"])
                if np.array_equal(pix[..., :3], src[..., :3]):
                    warns.append(f"{fn}: 입력 크롭과 픽셀이 같아 거부(bypass 통과 의심)")
                    continue
            tids = [t["tid"] for t in crop["targets"]]
            tid = str(mp.get("tid") or tids[0]).upper()
            if tid not in tids:
                warns.append(f"{fn}: 타깃 {tid} 가 {crop['name']} 에 없음 → {tids[0]}")
                tid = tids[0]
            ext = os.path.splitext(fn)[1].lower().lstrip(".")
            rel = store.save_bytes_ca(root, "cands", data, ext, prefix="f_")
            key = os.path.splitext(os.path.basename(rel))[0]
            if key in keys:
                warns.append(f"{fn}: 같은 내용의 파일이 이미 등록됨 → 건너뜀")
                continue
            keys.add(key)
            out.append({
                "key": key, "crop_id": crop["id"], "tid": tid, "file": rel,
                "src_size": [int(pix.shape[1]), int(pix.shape[0])], "origin": "folder",
                "origin_info": {"folder": path, "filename": fn, "match": why},
                "quad": dpsd.quad_from_rect(crop["rect"]), "picked": False, "z": None, "hand_mask": None,
            })
        return out

    @staticmethod
    def _autopick(m: dict, keys: set[str], warns: list[str]) -> None:
        """이번 폴더 실행으로 들어온 후보(keys) 중, pick 이 하나도 없는 타깃의 첫 후보를 pick.
        수확 후보(PS 에서 숨겨 거절한 것)나 이전 실행 후보는 건드리지 않는다."""
        picked = {(c["crop_id"], c["tid"]) for c in m["candidates"] if c.get("picked")}
        for c in m["candidates"]:
            k = (c["crop_id"], c["tid"])
            if c["key"] in keys and k not in picked:
                c["picked"] = True
                picked.add(k)
                warns.append(f"{c['crop_id']}/{c['tid']}: pick 없음 → 첫 후보 {c['key']} 자동 선택")

    @classmethod
    def IS_CHANGED(cls, source="", path="", **kwargs):
        if path is None:  # 링크된 경로: 파일 지문을 볼 수 없음
            return float("nan")
        path = _strip_path(path)
        fp = _file_fp(path) if os.path.isfile(path) else _dir_fp(path)
        vals = {k: kwargs.get(k) for k in ("group", "mapping_json", "visible_is_pick", "keep_hand_masks")}
        return json.dumps([source, path, fp, vals], ensure_ascii=False)


# ══════════════════════════════════════════════════════════════════════
# 노드 4 — Prepare (무료)
# ══════════════════════════════════════════════════════════════════════
def _glossary(root: str, m: dict) -> dict:
    g = store.load_glossary(os.path.join(root, "glossary.json"))
    g.update(store.load_glossary((m.get("source") or {}).get("glossary") or ""))
    return g


class BMKDesignPatchPrepare:
    """크롭마다 스펙 → 평탄화 입력 → 변형별 결정적 프롬프트 → cell_key 로 GPT 작업 목록(jobs)을 만든다(호출 없음)."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "project": (PROJECT_TYPE, {"tooltip": "BMK Design Patch Project 출력(Import PSD 이후)."}),
                "model": (_MODELS, {"default": _MODELS[0], "tooltip": "GPT Image 모델(cell_key 에 들어감)."}),
                "quality": (_QUALITIES, {"default": "max", "tooltip": "품질(cell_key 에 들어감)."}),
                "size_rule": (list(store.GPT_SIZE_RULES), {
                    "default": "user_k",
                    "tooltip": "출력 크기 규칙. user_k = 정사각 2048², 비정사각 k=max(2,1024/짧은변)(정수 아니면 정수 k). "
                               "int_k_2560 = 긴 변 ≤2560 최대 정수 k."}),
                "variants": ("STRING", {"default": "V1,V4",
                                        "tooltip": "프롬프트 변형. V1 표준, V2 타깃 단독(다중 타깃), V3 형태 자유도 +1, V4 체크리스트."}),
                "n": ("INT", {"default": 4, "min": 1, "max": 10, "tooltip": "호출당 장 수(cell_key 에 넣지 않음)."}),
                "ref_style": (_REF_STYLES, {"default": "at",
                                            "tooltip": "이미지 토큰. at = @image1, plain = Image 1, both = 범례에 둘 다. "
                                                       "specs/<crop_id>.json 의 ref_style 이 있으면 그것이 우선."}),
            },
        }

    RETURN_TYPES = (PROJECT_TYPE, "IMAGE", "STRING")
    RETURN_NAMES = ("project", "job_sheet", "report")
    OUTPUT_TOOLTIPS = ("갱신된 프로젝트 핸들.", "크롭별 입력 시트(평탄화 canvas + 레퍼런스).", "작업 요약·승인 해시·경고.")
    FUNCTION = "run"
    CATEGORY = _CATEGORY
    DESCRIPTION = (
        "무료 단계. 크롭마다 레이어명 골격·용어집·specs/<crop_id>.json 으로 스펙을 만들고, 투명 여백을 평탄화한 입력과 "
        "결정적 프롬프트(변형별), GPT 출력 크기, cell_key 를 jobs 에 기록합니다. 유료 호출은 하지 않습니다(M2 Run)."
    )
    SEARCH_ALIASES = _ALIASES + ["prepare", "prompt", "gpt prompt", "프롬프트", "작업 준비", "평탄화"]

    def run(self, project, model, quality, size_rule, variants, n, ref_style):
        t0 = time.time()
        root, m = _open(project)
        before = _snapshot(m)
        if not m["crops"]:
            raise ValueError(f"{_TAG} 크롭이 없습니다 - BMK Design Patch Import PSD 를 먼저 실행하세요")
        if ref_style not in _REF_STYLES:
            raise ValueError(f"{_TAG} ref_style 은 {_REF_STYLES} 중 하나여야 합니다: {ref_style!r}")
        dprompt.select_variants({}, variants)  # 형식 검사(잘못된 토큰이면 ValueError)
        refs_dir = (m.get("source") or {}).get("refs_dir") or ""
        glossary = _glossary(root, m)
        old_jobs = {j["cell_key"]: j for j in m["jobs"]}
        jobs, tiles, warn_lines, dup_lines = [], [], [], []
        n_new = n_dup = 0
        pbar = comfy.utils.ProgressBar(len(m["crops"]))
        for crop in m["crops"]:
            pbar.update(1)
            ov = store.load_spec_override(root, crop["id"])
            spec = dprompt.spec_from_manifest(crop, None, refs_dir, glossary, ov)
            if "ref_style" not in ov:
                spec["ref_style"] = ref_style
            sha_of: dict[str, str] = {}
            rel_of: dict[str, str] = {}
            # canvas: 크롭 소스 RGBA → edge-replicate 평탄화
            try:
                canvas_rgb, finfo = dprompt.flatten_canvas(_load_rgba(root, crop["source"]))
            except ValueError as e:  # 크롭 전체가 캔버스/작업영역 밖
                warn_lines.append(f"  {crop['id']} {crop['name']}: 작업 없음 - {e}")
                continue
            crel = store.save_png_ca(root, "inputs", canvas_rgb)
            cpath = _abs(root, crel)
            sha_of[cpath], rel_of[cpath] = store.pixel_sha(canvas_rgb), crel
            spec["canvas"] = cpath
            for key in ("guide", "before_paste"):
                if spec.get(key):
                    p = spec[key] if os.path.isabs(spec[key]) else os.path.join(root, spec[key])
                    if not os.path.isfile(p):
                        spec["warnings"].append(f"{key} 파일 없음 → 사용 안 함: {spec[key]}")
                        spec[key] = None
                        continue
                    with Image.open(p) as im:
                        rgb, _ = dprompt.flatten_canvas(im)
                    rel = store.save_png_ca(root, "inputs", rgb)
                    sha_of[_abs(root, rel)], rel_of[_abs(root, rel)] = store.pixel_sha(rgb), rel
                    spec[key] = _abs(root, rel)
            ref_thumbs = []
            for t in spec["targets"]:
                flat = []
                for r in t.get("refs") or []:
                    if not os.path.isfile(r):
                        continue  # spec_from_manifest 가 이미 경고함
                    with Image.open(r) as im:
                        rgb = dprompt.flatten_ref(im)
                    rel = store.save_png_ca(root, "inputs", rgb)
                    ap = _abs(root, rel)
                    sha_of[ap], rel_of[ap] = store.pixel_sha(rgb), rel
                    flat.append(ap)
                    if ap not in [x[0] for x in ref_thumbs]:
                        ref_thumbs.append((ap, rgb))
                t["refs"] = flat
                if t["mode"] in ("ref_correct", "sketch_guide") and not flat:
                    spec["warnings"].append(f"{t['id']}: 레퍼런스 파일을 읽지 못해 self_restore 로 바꿈")
                    t["mode"] = "self_restore"
            probs = dprompt.validate_spec(spec)
            x0, y0, x1, y1 = crop["rect"]
            W, H, sinfo = store.gpt_output_size(x1 - x0, y1 - y0, size_rule)
            chosen = dprompt.select_variants(dprompt.variants(spec), variants)
            crop_jobs, seen_prompts = [], {}
            for vname, v in chosen.items():
                same = seen_prompts.get((v["prompt"], tuple(v["images"])))
                if same:  # 예: detail 에 ';' 가 없으면 V4 체크리스트 = V1 → 같은 요청을 두 번 과금하지 않게
                    n_dup += 1
                    dup_lines.append(f"  {crop['id']}: {vname} = {same} (같은 프롬프트·이미지) → 건너뜀")
                    continue
                seen_prompts[(v["prompt"], tuple(v["images"]))] = vname
                shas = [sha_of[p] for p in v["images"]]
                ck = store.cell_key(model, quality, [W, H], _BACKGROUND, v["prompt"], shas, dprompt.TEMPLATE_VERSION, vname)
                roles = []
                for p in v["images"]:
                    role = ("canvas" if p == spec["canvas"] else "guide" if p == spec.get("guide")
                            else "before_paste" if p == spec.get("before_paste") else "ref")
                    roles.append({"role": role, "file": rel_of[p], "sha": sha_of[p]})
                job = dict(old_jobs.get(ck) or {})
                if not job:
                    n_new += 1
                job.update({"cell_key": ck, "crop_id": crop["id"], "tid": ",".join(t["id"] for t in v["targets"]),
                            "variant": vname, "model": model, "quality": quality, "size": [W, H], "n": int(n),
                            "background": _BACKGROUND, "prompt": v["prompt"], "inputs": roles,
                            "template_version": dprompt.TEMPLATE_VERSION, "size_info": sinfo,
                            "status": job.get("status", "pending")})
                crop_jobs.append(job)
            jobs += crop_jobs
            for wmsg in spec["warnings"] + [f"스펙 점검: {p}" for p in probs]:
                warn_lines.append(f"  {crop['id']} {crop['name']}: {wmsg}")
            cells = [_fit(canvas_rgb, _THUMB)] + [_fit(rgb, _THUMB) for _, rgb in ref_thumbs[:4]]
            lab = [_ascii(f"{crop['id']}  {x1 - x0}x{y1 - y0} -> {W}x{H} ({sinfo['method']})"),
                   _ascii(f"{len(crop_jobs)} jobs: {','.join(j['variant'] for j in crop_jobs)}  n{int(n)}  refs {len(ref_thumbs)}"
                          + (f"  fill {finfo['alpha_frac'] * 100:.0f}%" if finfo["method"] != "none" else ""))]
            tiles.append(_label_tile(cells, lab, (255, 140, 120) if (spec["warnings"] or probs) else (220, 220, 220)))
        kept = [j for k, j in old_jobs.items() if j.get("results") and k not in {x["cell_key"] for x in jobs}]
        m["jobs"] = _plain(jobs + kept)
        handle = _commit(root, m, before, project)
        pending = sorted(j["cell_key"] for j in jobs if j.get("status", "pending") == "pending")
        approval = hashlib.sha256(",".join(pending).encode("ascii")).hexdigest()[:12]
        lines = [f"Prepare: 작업 {len(jobs)} (신규 {n_new} · 기존 {len(jobs) - n_new}) · 예상 후보 {len(jobs) * int(n)}장 · "
                 f"{model} {quality} {size_rule} · 템플릿 {dprompt.TEMPLATE_VERSION} | 승인 해시 {approval} | "
                 f"rev {m['rev']} | {time.time() - t0:.1f}s",
                 "유료 호출 없음(M1). 작업 목록은 매니페스트 jobs 에 있습니다."]
        if kept:
            lines.append(f"결과가 있는 이전 작업 {len(kept)}개 보존")
        if n_dup:
            lines.append(f"같은 요청이 되는 변형 {n_dup}개 건너뜀:")
            lines += dup_lines
        if warn_lines:
            lines.append(f"경고 {len(warn_lines)}:")
            lines += warn_lines
        logger.info("%s %s", _TAG, lines[0])
        return (handle, _to_image(_grid(tiles, 2)), "\n".join(lines))


# ══════════════════════════════════════════════════════════════════════
# 노드 5 — Analyze (무료)
# ══════════════════════════════════════════════════════════════════════
def _area(crop: dict) -> int:
    if not crop:
        return 1
    x0, y0, x1, y1 = crop["rect"]
    return (x1 - x0) * (y1 - y0)


class BMKDesignPatchAnalyze:
    """후보마다 배치 → 가드 정합 → ΔE 자동 마스크 → 톤 보정장 → 게이트."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "project": (PROJECT_TYPE, {"tooltip": "BMK Design Patch Project 출력(Candidate In 이후)."}),
                "register": (_REGISTER_MODES, {"default": "guarded_affine",
                                               "tooltip": "가드 정합. 중앙 |차이| 가 2% 이상 줄 때만 적용. "
                                                          "손 마스크가 있으면 그 영역은 정합 추정에서 뺀다."}),
                "tone": (_TONE_MODES, {"default": "field",
                                       "tooltip": "field = 마스크 밖 저주파 보정장(σ), global = 전역 평균, off = 없음."}),
                "tone_sigma": ("INT", {"default": 16, "min": 2, "max": 128, "tooltip": "보정장 σ(px). 실측 최적 16."}),
                "dE": ("FLOAT", {"default": 8.0, "min": 1.0, "max": 50.0, "step": 0.5, "tooltip": "자동 마스크 ΔE76 문턱."}),
                "grow": ("INT", {"default": 9, "min": 0, "max": 64, "tooltip": "자동 마스크 확장(px)."}),
                "feather_sigma": ("FLOAT", {"default": 3.5, "min": 0.0, "max": 32.0, "step": 0.5,
                                            "tooltip": "자동 마스크 가우시안 페더 σ(px, 마스크에 굽기)."}),
                "roi_prior": (_ROI_PRIORS, {"default": "none",
                                            "tooltip": "hand_bbox = 손 마스크 bbox+10% 밖은 자동 마스크 0."}),
                "only_picked": ("BOOLEAN", {"default": False, "tooltip": "pick 된 후보만 분석."}),
            },
        }

    RETURN_TYPES = (PROJECT_TYPE, "IMAGE", "STRING")
    RETURN_NAMES = ("project", "contact_sheet", "report")
    OUTPUT_TOOLTIPS = ("갱신된 프로젝트 핸들(이 분석 설정을 실어 하류 Compose/Export 가 같은 분석을 씀).",
                       "후보별: 크롭 소스 | 정렬·톤 보정 후보 | 자동 마스크(빨강)·손 마스크 외곽(초록).",
                       "정합·게이트 요약.")
    FUNCTION = "run"
    CATEGORY = _CATEGORY
    DESCRIPTION = (
        "무료 단계. 후보를 크롭 크기로 배치하고 가드 정합(어긋난 결과만 보정), ΔE 자동 마스크 초안, 마스크 밖 저주파 톤 보정장, "
        "게이트(마스크 밖 드리프트, 검은 띠)를 계산합니다. 같은 입력·설정이면 다시 계산하지 않습니다."
    )
    SEARCH_ALIASES = _ALIASES + ["analyze", "registration", "auto mask", "tone", "정합", "자동 마스크", "톤 보정", "게이트"]

    def run(self, project, register, tone, tone_sigma, dE, grow, feather_sigma, roi_prior, only_picked):
        t0 = time.time()
        root, m = _open(project)
        before = _snapshot(m)
        for v, allowed, nm in ((register, _REGISTER_MODES, "register"), (tone, _TONE_MODES, "tone"),
                               (roi_prior, _ROI_PRIORS, "roi_prior")):
            if v not in allowed:
                raise ValueError(f"{_TAG} {nm} 는 {allowed} 중 하나여야 합니다: {v!r}")
        prm = {"register": register, "tone": tone, "tone_sigma": int(tone_sigma), "dE": float(dE), "grow": int(grow),
               "feather_sigma": float(feather_sigma), "roi_prior": roi_prior}
        cands = [c for c in m["candidates"] if c.get("picked") or not only_picked]
        pbar = comfy.utils.ProgressBar(max(1, len(cands)))
        entries, done, hit, warns = _ensure_analysis(root, m, cands, prm, pbar=pbar)
        handle = _commit(root, m, before, project, analyze=prm)
        crops = _crop_map(m)
        tiles, lines = [], []
        n_reg = n_fail = n_skip = 0
        for c in cands:
            e = entries.get(c["key"])
            if e is None:
                continue
            if e.get("skipped"):
                n_skip += 1
                lines.append(f"  {c['crop_id']} {c['tid']} {c['key']}: 분석 생략 - {e['skipped']}")
                continue
            r, g = e["reg"], e["gates"]
            n_reg += r["applied"]
            n_fail += bool(g["fails"])
            tiles.append(Image.open(_abs(root, e["thumb"])).convert("RGB"))
            crop = crops.get(c["crop_id"]) or {}
            lines.append(f"  {c['crop_id']} {c['tid']} {c['key']} {'★' if c.get('picked') else ' '} "
                         f"reg {r['kind']}" + (f" (s {r['sx']:.3f},{r['sy']:.3f} rot {r['rot_deg']:+.2f} disp {r['disp']:.1f}px)"
                                               if r["applied"] else "")
                         + f" | mad {g['outside_mad']:.1f} | mask {e['mask_area'] / max(1, _area(crop)):.0%}"
                         + (f" | FAIL {','.join(g['fails'])}" if g["fails"] else ""))
        head = (f"Analyze: 후보 {len(cands)} (계산 {done} · 캐시 {hit} · 생략 {n_skip}) | 정합 적용 {n_reg} | 게이트 실패 {n_fail} | "
                f"{register}/{tone} σ{int(tone_sigma)} dE {float(dE):g} grow {int(grow)} feather {float(feather_sigma):g} "
                f"roi {roi_prior} | rev {m['rev']} | {time.time() - t0:.1f}s")
        report = "\n".join([head] + lines + [f"  - {w}" for w in warns])
        logger.info("%s %s", _TAG, head)
        return (handle, _to_image(_grid(tiles, 2 if len(tiles) > 4 else 1)), report)


# ══════════════════════════════════════════════════════════════════════
# 노드 6 — Compose
# ══════════════════════════════════════════════════════════════════════
class BMKDesignPatchCompose:
    """pick 을 z순으로 베이스 위에 합성한 평탄화 미리보기 + 마스크 합집합."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "project": (PROJECT_TYPE, {"tooltip": "BMK Design Patch Project 출력."}),
                "mask_source": (_MASK_SOURCES, {"default": "hand_else_auto",
                                                "tooltip": "auto = 자동 마스크, hand = 수확한 손 마스크(없으면 마스크 없음), "
                                                           "hand_else_auto = 손 마스크가 있으면 손, 없으면 자동."}),
                "use_registration": ("BOOLEAN", {"default": True,
                                                  "tooltip": "켜면 Analyze 정합을 quad 에 반영. 끄면 후보 quad 그대로"
                                                             "(수확 후보 = PSD 의 사용자 배치, 폴더 후보 = crop rect)."}),
                "use_tone": ("BOOLEAN", {"default": True, "tooltip": "Analyze 톤 보정장을 적용."}),
                "zorder": (_ZORDERS, {"default": "auto_small_on_top",
                                      "tooltip": "auto_small_on_top = 실효 마스크가 작은 것을 위로(실측 97% 일치). "
                                                 "harvest = 수확한 PSD 의 레이어 순서."}),
            },
        }

    RETURN_TYPES = (PROJECT_TYPE, "IMAGE", "MASK", "STRING")
    RETURN_NAMES = ("project", "image", "mask", "report")
    OUTPUT_TOOLTIPS = ("갱신된 프로젝트 핸들(이 합성 설정을 실어 하류 Export PSD 가 같은 배치로 씀).",
                       "베이스 위에 pick 을 합성한 풀캔버스 미리보기.",
                       "마스크 합집합(ComfyUI MASK 규약: 1 = 패치 없음/투명, 0 = 패치 적용).", "합성 요약.")
    FUNCTION = "run"
    CATEGORY = _CATEGORY
    DESCRIPTION = (
        "pick 된 후보를 정합 quad·톤 보정·최종 마스크로 베이스 위에 z순 합성한 평탄화 미리보기와 마스크 합집합을 냅니다. "
        "분석은 상류 Analyze 설정(없으면 기본값)을 쓰고, 설정(정합 사용, z순서)은 핸들에 실려 하류 Export PSD 가 같은 배치로 씁니다."
    )
    SEARCH_ALIASES = _ALIASES + ["compose", "flatten", "preview", "합성", "미리보기"]

    def run(self, project, mask_source, use_registration, use_tone, zorder):
        t0 = time.time()
        root, m = _open(project)
        before = _snapshot(m)
        if mask_source not in _MASK_SOURCES or zorder not in _ZORDERS:
            raise ValueError(f"{_TAG} mask_source/zorder 값이 잘못되었습니다: {mask_source!r} {zorder!r}")
        if not m["base"]:
            raise ValueError(f"{_TAG} 베이스가 없습니다 - Import PSD 를 먼저 실행하세요")
        warns: list[str] = []
        picks = [c for c in m["candidates"] if c.get("picked")]
        entries: dict = {}
        if _needs_analysis(mask_source, bool(use_registration), bool(use_tone)):
            entries, done, _hit, w2 = _ensure_analysis(root, m, picks, project.get("analyze"))
            warns += w2
            if done:
                warns.append(f"상류 Analyze 설정(없으면 기본값)의 분석이 없던 pick {done}개를 분석함")
        comp = {"mask_source": mask_source, "use_registration": bool(use_registration), "use_tone": bool(use_tone),
                "zorder": zorder}
        plan = _plan(root, m, entries, mask_source, bool(use_registration), zorder, False, warns)
        base = _load_rgb(root, m["base"])
        canvas = base.astype(np.float32)
        union = np.zeros(base.shape[:2], np.float32)
        items = list(_slices_in_order(plan))
        pbar = comfy.utils.ProgressBar(max(1, len(items)))
        lines, n_toned, msrc_count = [], 0, {}
        for p, t, it in items:
            pix, toned = _cand_pixels(root, it, bool(use_tone))
            n_toned += toned
            rgb, _alpha, eff, left, top = _render(pix, it["quad"], it["mask"])
            _paste(canvas, union, rgb, eff, left, top)
            msrc_count[it["msrc"]] = msrc_count.get(it["msrc"], 0) + 1
            q = it["quad"]
            lines.append(f"  {p['crop']['id']} {t['tid']} {it['cand']['key']} mask {it['msrc']} "
                         f"quad ({q[0]:.1f},{q[1]:.1f})-({q[4]:.1f},{q[5]:.1f})" + (" toned" if toned else ""))
            pbar.update(1)
        handle = _commit(root, m, before, project, compose=comp)
        out = np.clip(np.rint(canvas), 0, 255).astype(np.uint8)
        head = (f"Compose: pick {len(items)} (크롭 {len(plan)}) · 마스크 {msrc_count} · 톤 적용 {n_toned} · "
                f"정합 {'사용' if use_registration else '안 씀'} · z {zorder} | 패치 면적 {float((union > 0.5).mean()) * 100:.2f}% "
                f"| rev {m['rev']} | {time.time() - t0:.1f}s")
        report = "\n".join([head] + lines + [f"  - {w}" for w in warns])
        logger.info("%s %s", _TAG, head)
        return (handle, _to_image(out), torch.from_numpy(1.0 - union)[None], report)


# ══════════════════════════════════════════════════════════════════════
# 노드 7 — Export PSD
# ══════════════════════════════════════════════════════════════════════
def _outline(w: int, h: int, px: int = 4, color=(255, 64, 64)) -> np.ndarray:
    o = np.zeros((h, w, 4), np.uint8)
    o[..., :3] = color
    o[:px, :, 3] = 255
    o[-px:, :, 3] = 255
    o[:, :px, 3] = 255
    o[:, -px:, 3] = 255
    return o


def _export_hash(m: dict, entries: dict, comp: dict, opts: dict) -> str:
    payload = {"v": _EXPORT_VERSION, "opts": opts, "base": m["base"], "canvas": m["canvas"], "work_rect": m["work_rect"],
               "crops": [[c["id"], c["name"], c["rect"], c["targets"]] for c in m["crops"]],
               "cands": m["candidates"], "compose": comp,
               "analysis": {k: e.get("params_hash") for k, e in entries.items()}}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def _output_dir(project: str) -> str:
    return os.path.join(folder_paths.get_output_directory(), "design_patch", project)


def _sidecar_hash(psd_path: str):
    """export 옆 스냅샷(<stem>.json)의 마지막 export 해시 — 그 경로를 마지막으로 쓴 export. 읽을 수 없으면 None."""
    try:
        with open(os.path.splitext(psd_path)[0] + ".json", encoding="utf-8") as f:
            return (json.load(f).get("exports") or [{}])[-1].get("hash")
    except (OSError, ValueError, AttributeError):
        return None


class BMKDesignPatchExportPSD:
    """pick·대안 후보를 풀해상도 Smart Object(또는 픽셀) 레이어로 넣은 PSD 를 쓴다."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "project": (PROJECT_TYPE, {"tooltip": "BMK Design Patch Project 출력."}),
                "filename_prefix": ("STRING", {"default": "design_patch",
                                               "tooltip": "파일 이름 앞부분. output/design_patch/<project>/<prefix>_r<rev>.psd "
                                                          "(이미 있으면 _r<rev>_2 … — 덮어쓰지 않음)"}),
                "layer_mode": (_LAYER_MODES, {"default": "smart_object",
                                              "tooltip": "smart_object = 풀해상도 원본 임베드(PS 에서 재변형 가능), pixel = 배치된 픽셀."}),
                "alternates": (_ALTERNATES, {"default": "hidden_all",
                                             "tooltip": "hidden_all = pick 아닌 후보도 숨김·빨강 라벨로 함께, none = pick 만."}),
                "color": (_COLORS, {"default": "toned", "tooltip": "toned = 톤 보정한 풀해상도 PNG 임베드, raw = 원본 그대로."}),
                "mask_source": (_EXPORT_MASK_SOURCES, {"default": "hand_else_auto",
                                                       "tooltip": "타깃(슬라이스) 그룹 마스크로 쓸 최종 마스크."}),
                "include_base": ("BOOLEAN", {"default": True, "tooltip": "00.Base(클린플레이트 합성) 픽셀 레이어를 넣음."}),
                "include_crop_outlines": ("BOOLEAN", {"default": True,
                                                      "tooltip": "02.크롭영역 그룹(숨김)에 크롭 외곽선 픽셀 레이어를 넣음."}),
            },
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("psd_path",)
    OUTPUT_TOOLTIPS = ("저장한 PSD 경로.",)
    FUNCTION = "run"
    OUTPUT_NODE = True
    CATEGORY = _CATEGORY
    DESCRIPTION = (
        "00.Base / 02.크롭영역(외곽선, 숨김) / 03.수정 > 크롭 > 타깃(그룹 마스크 = 최종 마스크) > 후보 구조의 PSD 를 씁니다. "
        "후보는 풀해상도 Smart Object 로 임베드하고 정합은 SO 변형(affine)에 넣습니다(비파괴). pick 은 보임·녹색, 대안은 숨김·빨강. "
        "정합 사용·z순서는 상류 Compose 설정(없으면 기본값)을 따르고, 이미 있는 파일은 덮어쓰지 않습니다."
    )
    SEARCH_ALIASES = _ALIASES + ["export psd", "smart object", "psd 출력", "스마트 오브젝트", "레이어 출력"]

    def run(self, project, filename_prefix, layer_mode, alternates, color, mask_source, include_base,
            include_crop_outlines):
        t0 = time.time()
        root, m = _open(project)
        before = _snapshot(m)
        prefix = str(filename_prefix or "").strip() or "design_patch"
        if not _SAFE_PREFIX_RE.match(prefix) or ".." in prefix:
            raise ValueError(f"{_TAG} filename_prefix 에는 경로 구분자 없이 영문·숫자·한글·_ - . 만 쓸 수 있습니다: {prefix!r}")
        for v, allowed, nm in ((layer_mode, _LAYER_MODES, "layer_mode"), (alternates, _ALTERNATES, "alternates"),
                               (color, _COLORS, "color"), (mask_source, _EXPORT_MASK_SOURCES, "mask_source")):
            if v not in allowed:
                raise ValueError(f"{_TAG} {nm} 는 {allowed} 중 하나여야 합니다: {v!r}")
        if not m["base"] or not m["canvas"]:
            raise ValueError(f"{_TAG} 베이스가 없습니다 - Import PSD 를 먼저 실행하세요")
        comp = dict(_COMPOSE_DEFAULTS, **(project.get("compose") or {}))  # 상류 Compose 설정(없으면 기본값)
        use_reg, zorder = bool(comp["use_registration"]), comp["zorder"]
        toned = color == "toned"
        opts = {"layer_mode": layer_mode, "alternates": alternates, "color": color, "mask_source": mask_source,
                "include_base": bool(include_base), "include_crop_outlines": bool(include_crop_outlines),
                "prefix": prefix}
        warns: list[str] = []
        inc = [c for c in m["candidates"] if c.get("picked") or alternates == "hidden_all"]
        entries: dict = {}
        if _needs_analysis(mask_source, use_reg, toned):
            entries, done, _hit, w2 = _ensure_analysis(root, m, inc, project.get("analyze"))
            warns += w2
            if done:
                warns.append(f"상류 Analyze 설정(없으면 기본값)의 분석이 없던 후보 {done}개를 분석함")
        eh = _export_hash(m, entries, comp, opts)
        last = next((e for e in reversed(m["exports"]) if e.get("hash") == eh), None)
        if last and os.path.isfile(last["psd"]) and _sidecar_hash(last["psd"]) == eh:  # 다른 export 가 덮어쓴 파일은 재사용 안 함
            handle = _commit(root, m, before)
            logger.info("%s Export: 바뀐 것 없음 → 기존 파일 %s", _TAG, last["psd"])
            return {"ui": {"text": [f"변경 없음: {last['psd']}"]}, "result": (last["psd"],)}

        rev = int(m["rev"])
        out_dir = _output_dir(os.path.basename(os.path.normpath(root)))  # 폴더 이름(복사한 매니페스트의 project 값 아님)
        os.makedirs(out_dir, exist_ok=True)
        stem, k = f"{prefix}_r{rev}", 1
        while any(os.path.exists(os.path.join(out_dir, stem + ext)) for ext in (".psd", "_mask.png", ".json")):
            k += 1  # 같은 이름(다른 base_dir·다시 만든·복사한) 프로젝트의 파일이나 PS 에서 고친 파일을 덮어쓰지 않는다
            stem = f"{prefix}_r{rev}_{k}"
        psd_path = os.path.join(out_dir, stem + ".psd")
        plan = _plan(root, m, entries, mask_source, use_reg, zorder, alternates == "hidden_all", warns)
        W, H = m["canvas"]
        wr = dpsd.PSDWriter((W, H))
        base = _load_rgb(root, m["base"])
        flat = np.full((H, W, 3), 255.0, np.float32)  # 병합 프리뷰 = 흰 문서 위 보이는 레이어(아래와 같은 순서로 합성)
        if include_base:
            x0, y0, x1, y1 = m["work_rect"] or [0, 0, W, H]
            x0, y0, x1, y1 = max(0, x0), max(0, y0), min(W, x1), min(H, y1)
            if x1 > x0 and y1 > y0:
                wr.add_pixel(None, "00.Base", np.ascontiguousarray(base[y0:y1, x0:x1]), x0, y0)
                flat[y0:y1, x0:x1] = base[y0:y1, x0:x1]
        if include_crop_outlines:
            g_out = wr.add_group(None, "02.크롭영역", visible=False, open_folder=False)
            for c in m["crops"]:
                cx0, cy0, cx1, cy1 = (max(0, c["rect"][0]), max(0, c["rect"][1]), min(W, c["rect"][2]), min(H, c["rect"][3]))
                if cx1 > cx0 and cy1 > cy0:
                    wr.add_pixel(g_out, c["name"], _outline(cx1 - cx0, cy1 - cy0), cx0, cy0)
        g_fix = wr.add_group(None, "03.수정")
        union = np.zeros((H, W), np.float32)
        total = sum(len(t["slices"]) + len(t["alts"]) for p in plan for t in p["targets"])
        pbar = comfy.utils.ProgressBar(max(1, total) + 1)
        reg_order = {c["key"]: i for i, c in enumerate(m["candidates"])}  # c<번호> = 타깃 안 등록 순서
        written: list[dict] = []
        n_toned = n_groups = 0

        def add_cand(parent, crop, it, num, visible):
            nonlocal n_toned
            c = it["cand"]
            pix, is_toned = _cand_pixels(root, it, toned)
            n_toned += is_toned
            rgb, alpha, eff, left, top = _render(pix, it["quad"], it["mask"])
            cache = rgb if alpha is None else np.dstack([rgb, alpha])
            name = f"{crop['name']} · {_target_label(crop, c['tid'])} · c{num:02d}" + (" ★" if c.get("picked") else "")
            label = "green" if c.get("picked") else "red"
            if layer_mode == "smart_object":
                data, ft = self._so_bytes(root, it, pix, is_toned)
                wr.add_smart_object(parent, name, data, filetype=ft, src_size=(pix.shape[1], pix.shape[0]),
                                    quad=it["quad"], visible=visible, label=label, cache_rgb=cache,
                                    filename=f"{c['key']}.{ft}")
            else:
                wr.add_pixel(parent, name, cache, left, top, visible=visible, label=label)
            if visible:
                _paste(flat, union, rgb, eff, left, top)
            mk = it["mask"]
            written.append({"name": name, "key": c["key"], "quad": [float(v) for v in it["quad"]],
                            "src_size": [int(pix.shape[1]), int(pix.shape[0])], "visible": visible,
                            "toned": bool(is_toned), "mask_src": it["msrc"], "mask_rect": None if mk is None else
                            [mk["left"], mk["top"], mk["left"] + mk["arr"].shape[1], mk["top"] + mk["arr"].shape[0]]})
            pbar.update(1)

        for p in plan:
            crop = p["crop"]
            cg = wr.add_group(g_fix, crop["name"])
            for t in p["targets"]:
                members = sorted(t["slices"] + t["alts"], key=lambda it: reg_order[it["cand"]["key"]])
                num = {it["cand"]["key"]: i + 1 for i, it in enumerate(members)}
                if len(t["slices"]) <= 1:
                    first = t["slices"][0] if t["slices"] else None
                    tg = wr.add_group(cg, t["label"], mask=None if first is None else first["mask"])
                    n_groups += first is not None and first["mask"] is not None
                    for it in t["slices"]:
                        add_cand(tg, crop, it, num[it["cand"]["key"]], True)
                    for it in t["alts"]:
                        add_cand(tg, crop, it, num[it["cand"]["key"]], False)
                else:
                    tg = wr.add_group(cg, t["label"])
                    for k, it in enumerate(t["slices"], 1):
                        sg = wr.add_group(tg, f"{t['label']} · slice {k}", mask=it["mask"])
                        n_groups += it["mask"] is not None
                        add_cand(sg, crop, it, num[it["cand"]["key"]], True)
                        if k == 1:
                            for alt in t["alts"]:
                                add_cand(sg, crop, alt, num[alt["cand"]["key"]], False)
        res = wr.save(psd_path, verify=True, composite=np.clip(np.rint(flat), 0, 255).astype(np.uint8))
        pbar.update(1)
        mask_png = os.path.join(out_dir, stem + "_mask.png")
        Image.fromarray(np.clip(np.rint(union * 255.0), 0, 255).astype(np.uint8), "L").save(mask_png)
        snap = os.path.join(out_dir, stem + ".json")
        rec = {"rev": rev, "psd": psd_path.replace("\\", "/"), "time": time.strftime("%Y-%m-%d %H:%M:%S"), "hash": eh,
               "options": opts, "compose": comp, "bytes": res["bytes"], "smart_objects": res["smart_objects"],
               "layers": len(written), "problems": len(res["problems"]), "mask": mask_png.replace("\\", "/")}
        m["exports"].append(_plain(rec))
        store.write_json_atomic(snap, dict(m, export_layers=written))
        handle = _commit(root, m, before)
        head = (f"Export PSD: {psd_path} | 레이어 {len(written)} (pick {sum(w['visible'] for w in written)}) · "
                f"그룹 마스크 {n_groups} · 톤 {n_toned} · {layer_mode} · {res['bytes'] / 1e6:.1f}MB · 검사 문제 {len(res['problems'])} "
                f"| 정합 {'사용' if use_reg else '안 씀'} · z {zorder} | rev {m['rev']} | {time.time() - t0:.1f}s")
        for p_ in res["problems"]:
            warns.append(f"검사: {p_}")
        warns += res.get("warnings") or []
        logger.info("%s %s", _TAG, head)
        for wmsg in warns:
            logger.warning("%s Export: %s", _TAG, wmsg)
        return {"ui": {"text": [head] + warns}, "result": (psd_path.replace("\\", "/"),)}

    @staticmethod
    def _so_bytes(root: str, it: dict, pix: np.ndarray, is_toned: bool) -> tuple[bytes, str]:
        """SO 임베드 bytes: 원본 파일 그대로, 톤 보정이면 풀해상도 PNG(derived 에 캐시)."""
        c, entry = it["cand"], it["entry"]
        if not is_toned:
            ext = os.path.splitext(c["file"])[1].lower().lstrip(".")
            return _read(_abs(root, c["file"])), ("jpg" if ext == "jpeg" else ext)
        tone = entry["tone"]
        if not (tone.get("full") and os.path.isfile(_abs(root, tone["full"]))):
            tone["full"] = store.save_png_ca(root, "derived", pix, prefix=f"{c['key']}_{entry['params_hash'][:6]}_toned_")
        return _read(_abs(root, tone["full"])), "png"


# ══════════════════════════════════════════════════════════════════════
NODE_CLASS_MAPPINGS = {
    "BMKDesignPatchProject": BMKDesignPatchProject,
    "BMKDesignPatchImportPSD": BMKDesignPatchImportPSD,
    "BMKDesignPatchCandidateIn": BMKDesignPatchCandidateIn,
    "BMKDesignPatchPrepare": BMKDesignPatchPrepare,
    "BMKDesignPatchAnalyze": BMKDesignPatchAnalyze,
    "BMKDesignPatchCompose": BMKDesignPatchCompose,
    "BMKDesignPatchExportPSD": BMKDesignPatchExportPSD,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "BMKDesignPatchProject": "BMK Design Patch Project",
    "BMKDesignPatchImportPSD": "BMK Design Patch Import PSD",
    "BMKDesignPatchCandidateIn": "BMK Design Patch Candidate In",
    "BMKDesignPatchPrepare": "BMK Design Patch Prepare",
    "BMKDesignPatchAnalyze": "BMK Design Patch Analyze",
    "BMKDesignPatchCompose": "BMK Design Patch Compose",
    "BMKDesignPatchExportPSD": "BMK Design Patch Export PSD",
}
