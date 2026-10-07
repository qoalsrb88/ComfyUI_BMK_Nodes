"""BMK Design Patch Store — 디자인 패치 프로젝트 폴더·매니페스트·ID·이름 규칙·출력 크기 규칙(ComfyUI 비의존).

배경
----
Design Patch(가제 Multi Layer Crop Edit) 노드 패밀리는 프로젝트 폴더의 매니페스트 JSON 하나를
유일한 원본(원장)으로 삼는다. 각 노드는 매니페스트를 읽고 → 일하고 → 원자 저장한다.
이 모듈은 그 공통 바닥(디스크 형식, ID, 이름 파싱, 크기 규칙, 캐시 키)만 담는 순수 함수 모음이며
torch / comfy / folder_paths 를 import 하지 않는다(numpy·PIL + 표준 라이브러리만).
명세: H:\\BmkNodeDesign\\MultiLayerCropEdit\\_proto\\M1_SPEC.md §1.

폴더 구조
---------
    <base_dir>/bmk_design_patch/<project>/
      design_patch.json      매니페스트 (원자 저장: tmp → os.replace)
      inputs/<sha12>.png      크롭 소스·평탄화 입력 (내용 주소)
      cands/<key>.<ext>       후보 원본 바이트(디코드 없이 그대로, 수정·자동삭제 금지)
      masks/<key>_hand.png    harvest 된 손 마스크
      derived/...             분석 산출(재생성 가능)
      specs/<crop_id>.json    사람이 편집하는 스펙 오버라이드(선택)
      glossary.json           한→영 용어집(선택)

공개 함수 (예외는 모두 ValueError + 한국어 메시지)
---------------------------------------------------
- project_root(base_dir, project)            프로젝트 폴더 경로(이름 검증만, 폴더는 만들지 않음)
- new_manifest / load_manifest / save_manifest  매니페스트 골격·읽기·원자 저장(rev += 1)
- save_bytes_ca / save_png_ca                내용 주소 저장(이미 있으면 쓰지 않음)
- pixel_sha                                  디코드된 픽셀 다이제스트(save_png_ca 파일명·cell_key 입력용)
- crop_id_for / assign_crop_ids              ASCII 크롭 ID (NNN_<blake2b 3바이트>)
- parse_crop_layer_name                      "NNN_부위 | A: …; B: …" 레이어명 파싱(레거시 관용)
- load_glossary / translate                  한→영 용어집
- match_refs                                 레퍼런스 폴더에서 NNN_ 접두 이미지 매칭
- gpt_output_size / gpt_image_custom_size_ok GPT 출력 크기 규칙(user_k / int_k_2560)
- cell_key                                   유료 호출 셀 캐시 키(n 제외)
- 보조: resolve_path, write_json_atomic, file_fingerprint, load_spec_override

규칙 메모
---------
- 내용 주소 파일명 = sha256 앞 12자.
  save_bytes_ca 는 바이트 그대로의 sha256, save_png_ca 는 **디코드된 픽셀**의 sha256
  (모드·크기·픽셀 바이트, pixel_sha). PNG 인코더/zlib 버전이 바뀌어도 같은 픽셀이면 같은 파일명이 되고,
  파일이 이미 있으면 인코딩조차 하지 않는다(멱등 노드의 캐시 적중 비용 최소화).
- crop_id 는 레이어명의 "|" 앞(기본 이름)만 해시한다 → 타깃 문구를 고쳐도 ID·specs 파일이 유지된다.
  같은 이름이 두 번 나오면 같은 ID 가 되므로 assign_crop_ids 로 -2, -3 접미를 붙인다(017 사례).
- 매니페스트 저장 시 numpy 스칼라/배열·튜플·Path 는 JSON 기본형으로 바꾸고, NaN/inf 는 null 로 쓴다
  (나중에 브라우저 JSON.parse 로도 읽히도록).

버전 이력
---------
v1 (2026-10)
- M1 최초 구현. 명세 §1 검증표(user_k 13행 + 정사각 4행) 전부 일치, 결과는 항상 gpt_image_custom_size_ok.
"""

from __future__ import annotations

import functools
import hashlib
import io
import json
import logging
import math
import os
import re
import time
import unicodedata
import uuid
from fractions import Fraction
from typing import Any, Iterable

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

_TAG = "[ComfyUI_BMK_Nodes::DesignPatch]"

SCHEMA = "bmk.design_patch/1"
MANIFEST_NAME = "design_patch.json"
PROJECTS_DIRNAME = "bmk_design_patch"
CELL_KEY_VERSION = "dp-cell/1"
PIXEL_SHA_VERSION = "dp-px/1"
GPT_SIZE_RULES = ("user_k", "int_k_2560")
REF_EXTS = (".png", ".jpg", ".jpeg", ".webp")

# GPT Image 2.5 size=Custom 백엔드 제약 (bmk_canvas_snap.gpt_image_custom_size_ok 와 동일)
_GPT_SNAP = 16
_GPT_MIN_EDGE = 480
_GPT_MAX_EDGE = 3840
_GPT_MAX_RATIO = 3
_GPT_MIN_PIXELS = 655_360
_GPT_MAX_PIXELS = 8_294_400

_PROJECT_RE = re.compile(r"^[\w\-.가-힣]+$")
_WIN_RESERVED_RE = re.compile(r"^(con|prn|aux|nul|com[0-9]|lpt[0-9])(\..*)?$", re.IGNORECASE)
_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_\-.]+$")
_EXT_RE = re.compile(r"^[a-z0-9]{1,8}$")
_PREFIX_RE = re.compile(r"^[A-Za-z0-9_\-.]*$")
_CROP_ID_RE = re.compile(r"^[A-Za-z0-9_\-]+$")

# 레이어명 구분자 관용: | ｜ │ ¦ / ; ； / : ：
_PIPE_CHARS = "|\uFF5C\u2502\u00A6"
_PIPE_RE = re.compile("[" + re.escape(_PIPE_CHARS) + "]")
# 타깃 구분: ; ； 와 | 계열, 그리고 "…, B: …" 처럼 쉼표 뒤에 대문자 타깃 라벨이 오는 경우
_TARGET_SPLIT_RE = re.compile(
    "[;\uFF1B" + re.escape(_PIPE_CHARS) + "]" + r"|[,\uFF0C\u3001](?=\s*[A-Z]\s*[:\uFF1A])"
)
_TARGET_HEAD_RE = re.compile(r"^\s*([A-Za-z\uFF21-\uFF3A\uFF41-\uFF5A])\s*[:\uFF1A]\s*(.*)$", re.DOTALL)
_NNN_RE = re.compile(r"^\s*([0-9\uFF10-\uFF19]+)[\s_\-.]*(.*)$", re.DOTALL)
# 레퍼런스 컷아웃 표지 "_t": 뒤에 영문자가 오지 않는 경우만(_t, _t2, _tㄱ 은 해당 / _top, _tassel 은 아님)
_REF_T_RE = re.compile(r"_t(?![A-Za-z])", re.IGNORECASE)
# 용어집 비교용 정규화에서 지우는 문자
_GLOSS_STRIP_RE = re.compile(r"[\s_\-\u00B7\u30FB.]+")


# ══════════════════════════════════════════════════════════════════════
# 공통 보조
# ══════════════════════════════════════════════════════════════════════
def _nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def _ascii_digits(s: str) -> str:
    """전각 숫자를 ASCII 로."""
    return "".join(chr(ord(c) - 0xFF10 + 0x30) if "\uFF10" <= c <= "\uFF19" else c for c in s)


def _jsonable(obj: Any) -> Any:
    """매니페스트 저장용: numpy·튜플·Path → JSON 기본형, NaN/inf → None."""
    if obj is None or isinstance(obj, (bool, str)):
        return obj
    if isinstance(obj, int):
        return int(obj)
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return _jsonable(obj.tolist())
    if isinstance(obj, np.generic):
        return _jsonable(obj.item())
    if isinstance(obj, os.PathLike):
        return os.fspath(obj).replace("\\", "/")  # 매니페스트 경로는 '/' 구분(예: "H:/.../03.psd")
    if isinstance(obj, (set, frozenset)):
        return [_jsonable(v) for v in sorted(obj, key=repr)]
    raise ValueError(f"JSON 으로 저장할 수 없는 값 형식: {type(obj).__name__}")


def _atomic_write_bytes(path: str, data: bytes) -> None:
    """같은 폴더의 임시 파일에 쓰고 fsync → os.replace. Windows 일시 잠금은 짧게 재시도."""
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    tmp = os.path.join(folder, f".{os.path.basename(path)}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        for attempt in range(6):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt == 5:
                    raise
                time.sleep(0.05 * (attempt + 1))
    except BaseException:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        raise


def write_json_atomic(path: str, obj: Any) -> None:
    """obj 를 UTF-8 JSON(들여쓰기 1, 한글 그대로)으로 원자 저장."""
    text = json.dumps(_jsonable(obj), ensure_ascii=False, indent=1, allow_nan=False)
    _atomic_write_bytes(path, (text + "\n").encode("utf-8"))


def _read_json(path: str, what: str) -> Any:
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError as e:
        raise ValueError(f"{what} 파일을 읽을 수 없습니다: {path} ({e})") from e
    try:
        return json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ValueError(f"{what} JSON 형식이 잘못되었습니다: {path} ({e})") from e


def file_fingerprint(path: str) -> list[int]:
    """[mtime_ns, size] — 매니페스트 source.fingerprint / IS_CHANGED 용(내용 해시보다 싸다)."""
    try:
        st = os.stat(path)
    except OSError as e:
        raise ValueError(f"파일을 찾을 수 없습니다: {path} ({e})") from e
    return [int(st.st_mtime_ns), int(st.st_size)]


def _norm_relparts(rel: str, what: str) -> list[str]:
    if not isinstance(rel, str) or not rel.strip():
        raise ValueError(f"{what} 가 비어 있습니다.")
    s = rel.strip().replace("\\", "/")
    if s.startswith("/") or re.match(r"^[A-Za-z]:", s):
        raise ValueError(f"{what} 는 프로젝트 폴더 기준 상대 경로여야 합니다: {rel!r}")
    parts = [p for p in s.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        raise ValueError(f"{what} 에 '..' 또는 빈 경로를 쓸 수 없습니다: {rel!r}")
    return parts


def resolve_path(root: str, relpath: str) -> str:
    """프로젝트 상대 경로(매니페스트에 적힌 'inputs/abc.png' 등) → 절대 경로. 폴더 밖으로 나가면 ValueError."""
    parts = _norm_relparts(relpath, "상대 경로")
    base = os.path.abspath(root)
    path = os.path.normpath(os.path.join(base, *parts))
    if os.path.commonpath([base, path]) != base:
        raise ValueError(f"프로젝트 폴더 밖 경로입니다: {relpath!r}")
    return path


# ══════════════════════════════════════════════════════════════════════
# 프로젝트 폴더와 매니페스트
# ══════════════════════════════════════════════════════════════════════
def _check_project_name(project: Any) -> str:
    if not isinstance(project, str):
        raise ValueError(f"프로젝트 이름은 문자열이어야 합니다: {project!r}")
    name = project.strip()
    if not name:
        raise ValueError("프로젝트 이름이 비어 있습니다.")
    if not _PROJECT_RE.match(name):
        raise ValueError(
            f"프로젝트 이름에 쓸 수 없는 문자가 있습니다: {project!r} (영문·숫자·한글·_ - . 만 허용)"
        )
    if ".." in name or set(name) == {"."} or name.endswith("."):
        raise ValueError(f"프로젝트 이름에 '..' 이나 '.' 로 끝나는 이름은 쓸 수 없습니다: {project!r}")
    if _WIN_RESERVED_RE.match(name):
        raise ValueError(f"Windows 예약 이름은 프로젝트 이름으로 쓸 수 없습니다: {project!r}")
    return name


def project_root(base_dir: str, project: str) -> str:
    """<base_dir>/bmk_design_patch/<project> 의 절대 경로. 이름만 검증하고 폴더는 만들지 않는다."""
    name = _check_project_name(project)
    if not isinstance(base_dir, str) or not base_dir.strip():
        raise ValueError("base_dir 가 비어 있습니다(노드에서 ComfyUI input 디렉터리로 결정해 넘길 것).")
    return os.path.normpath(os.path.join(os.path.abspath(base_dir.strip()), PROJECTS_DIRNAME, name))


def _skeleton(project: str) -> dict:
    return {
        "schema": SCHEMA,
        "project": project,
        "rev": 0,
        "canvas": None,
        "work_rect": None,
        "source": {
            "psd": None,
            "fingerprint": None,
            "crop_group": None,
            "clean_plate_layer": None,
            "base_layer": None,
            "crop_pixels_group": None,
        },
        "base": None,
        "crops": [],
        "candidates": [],
        "analysis": {},
        "picks": {},
        "jobs": [],
        "exports": [],
    }


def new_manifest(project: str) -> dict:
    """빈 매니페스트(모든 고정 키 포함, rev 0)."""
    return _skeleton(_check_project_name(project))


def load_manifest(root: str) -> dict:
    """root/design_patch.json 을 읽는다. 없으면 new_manifest(폴더 이름). 빠진 고정 키는 기본값으로 채우고
    알 수 없는 추가 키는 그대로 둔다(저장 시 보존)."""
    path = os.path.join(root, MANIFEST_NAME)
    if not os.path.isfile(path):
        return new_manifest(os.path.basename(os.path.normpath(root)))
    m = _read_json(path, "매니페스트")
    if not isinstance(m, dict):
        raise ValueError(f"매니페스트 최상위가 객체가 아닙니다: {path}")
    schema = m.get("schema")
    if schema != SCHEMA:
        raise ValueError(f"매니페스트 schema 가 {SCHEMA!r} 가 아닙니다({schema!r}): {path}")
    project = m.get("project") or os.path.basename(os.path.normpath(root))
    skel = _skeleton(str(project))
    for key, default in skel.items():
        if key not in m or (m[key] is None and isinstance(default, (list, dict))):
            m[key] = default
    if isinstance(m.get("source"), dict):
        for key, default in skel["source"].items():
            m["source"].setdefault(key, default)
    try:
        m["rev"] = int(m["rev"])
    except (TypeError, ValueError) as e:
        raise ValueError(f"매니페스트 rev 가 정수가 아닙니다({m.get('rev')!r}): {path}") from e
    if m["rev"] < 0:
        raise ValueError(f"매니페스트 rev 가 음수입니다: {path}")
    return m


def save_manifest(root: str, m: dict) -> int:
    """rev += 1 후 원자 저장(tmp → os.replace). 새 rev 를 반환하고 m["rev"] 도 갱신한다(실패 시 되돌림)."""
    if not isinstance(m, dict) or m.get("schema") != SCHEMA:
        raise ValueError(f"매니페스트가 아니거나 schema 가 {SCHEMA!r} 가 아닙니다.")
    old = m.get("rev", 0)
    try:
        new_rev = int(old) + 1
    except (TypeError, ValueError) as e:
        raise ValueError(f"매니페스트 rev 가 정수가 아닙니다: {old!r}") from e
    m["rev"] = new_rev
    try:
        write_json_atomic(os.path.join(root, MANIFEST_NAME), m)
    except BaseException:
        m["rev"] = old
        raise
    return new_rev


def load_spec_override(root: str, crop_id: str) -> dict:
    """specs/<crop_id>.json (사람이 편집하는 스펙 오버라이드). 없으면 {}."""
    if not isinstance(crop_id, str) or not _CROP_ID_RE.match(crop_id):
        raise ValueError(f"crop_id 형식이 잘못되었습니다: {crop_id!r}")
    path = os.path.join(root, "specs", f"{crop_id}.json")
    if not os.path.isfile(path):
        return {}
    d = _read_json(path, "스펙 오버라이드")
    if not isinstance(d, dict):
        raise ValueError(f"스펙 오버라이드 최상위가 객체가 아닙니다: {path}")
    return d


# ══════════════════════════════════════════════════════════════════════
# 내용 주소 저장
# ══════════════════════════════════════════════════════════════════════
def _norm_subdir(subdir: str) -> str:
    parts = _norm_relparts(subdir, "subdir")
    for p in parts:
        if not _SEGMENT_RE.match(p):
            raise ValueError(f"subdir 에는 ASCII 영문·숫자·_ - . 만 쓸 수 있습니다: {subdir!r}")
    return "/".join(parts)


def _norm_ext(ext: str) -> str:
    e = str(ext or "").strip().lower().lstrip(".")
    if e == "jpeg":
        e = "jpg"
    if not _EXT_RE.match(e):
        raise ValueError(f"확장자 형식이 잘못되었습니다: {ext!r}")
    return e


def _check_prefix(prefix: str) -> str:
    p = "" if prefix is None else str(prefix)
    if not _PREFIX_RE.match(p) or len(p) > 32:
        raise ValueError(f"prefix 에는 ASCII 영문·숫자·_ - . 만 쓸 수 있습니다(32자 이하): {prefix!r}")
    return p


def save_bytes_ca(root: str, subdir: str, data: bytes, ext: str, prefix: str = "") -> str:
    """bytes 를 <subdir>/<prefix><sha256 앞 12자>.<ext> 로 저장하고 프로젝트 상대 경로('/' 구분)를 반환.
    같은 크기의 파일이 이미 있으면 쓰지 않는다(크기가 다르면 깨진 파일로 보고 원자적으로 다시 쓴다)."""
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise ValueError(f"data 는 bytes 여야 합니다: {type(data).__name__}")
    data = bytes(data)
    rel = f"{_norm_subdir(subdir)}/{_check_prefix(prefix)}{hashlib.sha256(data).hexdigest()[:12]}.{_norm_ext(ext)}"
    path = resolve_path(root, rel)
    if os.path.isfile(path):
        if os.path.getsize(path) == len(data):
            return rel
        logger.warning(f"{_TAG} 내용 주소 파일 크기 불일치 → 다시 씀: {rel}")
    _atomic_write_bytes(path, data)
    return rel


def _to_pil(img: Any) -> Image.Image:
    """HxW / HxWx{1,2,3,4} 배열(uint8, bool, float 0..1) 또는 PIL → L/LA/RGB/RGBA PIL."""
    if isinstance(img, Image.Image):
        if img.mode in ("L", "LA", "RGB", "RGBA"):
            return img
        if img.mode == "1":
            return img.convert("L")
        if img.mode in ("P", "PA"):
            return img.convert("RGBA" if (img.mode == "PA" or "transparency" in img.info) else "RGB")
        if img.mode in ("I;16", "I;16B", "I;16L", "I", "F"):
            raise ValueError(f"16bit/정수/실수 모드 PIL 이미지는 지원하지 않습니다: {img.mode}")
        return img.convert("RGBA" if "A" in img.getbands() else "RGB")
    if isinstance(img, np.ndarray):
        a = img
        if a.ndim == 3 and a.shape[2] == 1:
            a = a[:, :, 0]
        if a.ndim not in (2, 3) or (a.ndim == 3 and a.shape[2] not in (2, 3, 4)):
            raise ValueError(f"이미지 배열 모양이 잘못되었습니다: {a.shape} (HxW 또는 HxWx1/2/3/4)")
        if a.shape[0] < 1 or a.shape[1] < 1:
            raise ValueError(f"빈 이미지 배열입니다: {a.shape}")
        if a.dtype == np.uint8:
            pass
        elif a.dtype == np.bool_:
            a = a.astype(np.uint8) * 255
        elif np.issubdtype(a.dtype, np.floating):
            f = np.nan_to_num(a.astype(np.float32), nan=0.0, posinf=1.0, neginf=0.0)
            a = np.floor(np.clip(f, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)
        else:
            raise ValueError(f"지원하지 않는 배열 dtype 입니다: {a.dtype} (uint8 / bool / float 0..1)")
        # HxW → L, HxWx2 → LA, x3 → RGB, x4 → RGBA (uint8 에서 PIL 이 모드를 추론; mode 인자는 Pillow 11.3+ 폐기 예정)
        return Image.fromarray(np.ascontiguousarray(a))
    raise ValueError(f"이미지는 numpy 배열 또는 PIL 이어야 합니다: {type(img).__name__}")


def pixel_sha(img: Any) -> str:
    """디코드된 픽셀의 sha256 hex(64자). 모드·크기·픽셀 바이트를 해시하므로 PNG 인코더와 무관하다.
    save_png_ca 파일명의 앞 12자와 같고, cell_key 의 input_shas 로 쓴다."""
    pil = _to_pil(img)
    h = hashlib.sha256()
    h.update(f"{PIXEL_SHA_VERSION}|{pil.mode}|{pil.size[0]}x{pil.size[1]}|".encode("ascii"))
    h.update(pil.tobytes())
    return h.hexdigest()


def save_png_ca(root: str, subdir: str, img: Any, prefix: str = "") -> str:
    """이미지를 <subdir>/<prefix><pixel_sha 앞 12자>.png 로 저장하고 상대 경로를 반환.
    파일이 이미 있으면 인코딩도 하지 않는다. 메타데이터 없는 PNG(compress_level 4)."""
    pil = _to_pil(img)
    rel = f"{_norm_subdir(subdir)}/{_check_prefix(prefix)}{pixel_sha(pil)[:12]}.png"
    path = resolve_path(root, rel)
    if os.path.isfile(path) and os.path.getsize(path) > 0:
        return rel
    buf = io.BytesIO()
    pil.save(buf, format="PNG", compress_level=4)
    _atomic_write_bytes(path, buf.getvalue())
    return rel


# ══════════════════════════════════════════════════════════════════════
# 크롭 레이어명 / ID
# ══════════════════════════════════════════════════════════════════════
def _base_name(name: Any) -> str:
    if not isinstance(name, str):
        raise ValueError(f"크롭 이름은 문자열이어야 합니다: {name!r}")
    base = _PIPE_RE.split(_nfc(name), maxsplit=1)[0].strip()
    if not base:
        raise ValueError(f"크롭 이름이 비어 있습니다: {name!r}")
    return base


def _split_nnn(base: str) -> tuple[str, str]:
    m = _NNN_RE.match(base)
    if not m:
        return "", base
    return _ascii_digits(m.group(1)), m.group(2).strip()


def crop_id_for(name: str) -> str:
    """ASCII 크롭 ID: '<앞자리 숫자>_<blake2b(기본 이름, 3바이트) hex>', 숫자가 없으면 'x_<hex>'.
    레이어명의 '|' 뒤(타깃 문구)는 해시에서 뺀다(문구를 고쳐도 ID 유지). 기본 이름은 NFC 정규화."""
    base = _base_name(name)
    nnn, _ = _split_nnn(base)
    digest = hashlib.blake2b(base.encode("utf-8"), digest_size=3).hexdigest()
    return f"{nnn or 'x'}_{digest}"


def assign_crop_ids(names: Iterable[str]) -> list[str]:
    """이름 목록 → ID 목록. 같은 ID 가 다시 나오면 '-2', '-3' … 을 붙여 고유하게(017 이름 중복 사례)."""
    out: list[str] = []
    seen: dict[str, int] = {}
    for n in names:
        cid = crop_id_for(n)
        k = seen.get(cid, 0) + 1
        seen[cid] = k
        out.append(cid if k == 1 else f"{cid}-{k}")
    return out


def _upper_tid(c: str) -> str:
    if "\uFF21" <= c <= "\uFF3A" or "\uFF41" <= c <= "\uFF5A":
        c = unicodedata.normalize("NFKC", c)
    return c.upper()


def parse_crop_layer_name(name: str) -> dict:
    """크롭 shape 레이어 이름을 파싱한다.

    "002_토스트-머리장식_right | A: toast hair clip; B: red hairpin bar"
      → {"name": "002_토스트-머리장식_right", "nnn": "002", "part": "토스트-머리장식_right",
         "targets": [{"tid": "A", "text": "toast hair clip"}, {"tid": "B", "text": "red hairpin bar"}],
         "warnings": []}
    '|' 가 없는 레거시 이름은 타깃 1개 {"tid": "A", "text": ""}.
    관용: 전각 ｜ ； ： , 앞뒤 공백, 소문자/전각 타깃 라벨, 타깃 구분자로 '|' 반복, "…, B: …" 쉼표 구분.
    라벨 없는 조각은 남은 글자(A, B, …)를 순서대로 배정하고 warnings 에 적는다. 같은 라벨 두 번은 ValueError.
    """
    if not isinstance(name, str):
        raise ValueError(f"레이어 이름은 문자열이어야 합니다: {name!r}")
    full = _nfc(name)
    pieces = _PIPE_RE.split(full, maxsplit=1)
    base = pieces[0].strip()
    if not base:
        raise ValueError(f"레이어 이름의 크롭 부분('|' 앞)이 비어 있습니다: {name!r}")
    nnn, part = _split_nnn(base)
    warnings: list[str] = []
    if not nnn:
        warnings.append(f"이름이 숫자(NNN)로 시작하지 않습니다: {base!r}")

    if len(pieces) == 1:
        return {"name": base, "nnn": nnn, "part": part, "targets": [{"tid": "A", "text": ""}], "warnings": warnings}

    segs = [s.strip() for s in _TARGET_SPLIT_RE.split(pieces[1])]
    segs = [s for s in segs if s]
    parsed: list[list] = []  # [tid|None, text]
    for s in segs:
        m = _TARGET_HEAD_RE.match(s)
        if m:
            parsed.append([_upper_tid(m.group(1)), m.group(2).strip()])
        else:
            parsed.append([None, s])
    used = [p[0] for p in parsed if p[0]]
    dup = sorted({t for t in used if used.count(t) > 1})
    if dup:
        raise ValueError(f"레이어 이름에 같은 타깃 라벨이 두 번 있습니다({', '.join(dup)}): {name!r}")
    free = [chr(c) for c in range(ord("A"), ord("Z") + 1) if chr(c) not in used]
    for p in parsed:
        if p[0] is None:
            if not free:
                raise ValueError(f"타깃이 너무 많습니다(최대 26개): {name!r}")
            p[0] = free.pop(0)
            warnings.append(f"타깃 라벨이 없는 조각 → {p[0]} 로 지정: {p[1]!r}")
    if not parsed:
        warnings.append("'|' 뒤에 타깃이 없습니다 → 타깃 A(빈 문구)")
        parsed = [["A", ""]]
    return {
        "name": base,
        "nnn": nnn,
        "part": part,
        "targets": [{"tid": t, "text": x} for t, x in parsed],
        "warnings": warnings,
    }


# ══════════════════════════════════════════════════════════════════════
# 용어집
# ══════════════════════════════════════════════════════════════════════
def _gloss_norm(s: str) -> str:
    return _GLOSS_STRIP_RE.sub("", _nfc(str(s)).casefold())


def load_glossary(path: str) -> dict:
    """한→영 용어집 JSON 을 {한글: 영문} dict 로 읽는다.
    허용 형식: {"토스트": "toast hair clip", ...} / {"terms": {...}} / [["토스트", "toast"], ...] /
    [{"ko": "토스트", "en": "toast"}, ...]. '_' 로 시작하는 키(주석)는 무시.
    path 가 비었거나 파일이 없으면 {} (프로젝트의 glossary.json 은 선택 파일). 형식 오류는 ValueError."""
    if path is None or not str(path).strip():
        return {}
    path = str(path).strip()
    if not os.path.isfile(path):
        return {}
    raw = _read_json(path, "용어집")
    if isinstance(raw, dict) and isinstance(raw.get("terms"), (dict, list)):
        raw = raw["terms"]
    pairs: list[tuple[Any, Any]] = []
    if isinstance(raw, dict):
        pairs = list(raw.items())
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, (list, tuple)) and len(item) == 2:
                pairs.append((item[0], item[1]))
            elif isinstance(item, dict) and ("ko" in item or "src" in item):
                pairs.append((item.get("ko", item.get("src")), item.get("en", item.get("dst"))))
            else:
                raise ValueError(f"용어집 항목 형식이 잘못되었습니다: {item!r}")
    else:
        raise ValueError(f"용어집 최상위는 객체나 배열이어야 합니다: {path}")
    out: dict[str, str] = {}
    for k, v in pairs:
        if not isinstance(k, str) or k.startswith("_"):
            continue
        if not isinstance(v, str):
            raise ValueError(f"용어집 값은 문자열이어야 합니다: {k!r} → {v!r}")
        k2, v2 = _nfc(k).strip(), v.strip()
        if k2 and v2:
            out[k2] = v2
    return out


def translate(label: str, glossary: dict) -> str | None:
    """완전 일치 → 부분 일치(라벨 안에 들어 있는 가장 긴 키) 순으로 영문을 찾는다. 없으면 None.
    비교는 NFC + casefold + 공백·_·-·가운뎃점 제거 후."""
    if not label or not glossary:
        return None
    nl = _gloss_norm(label)
    if not nl:
        return None
    norm = [(_gloss_norm(k), k, v) for k, v in glossary.items() if isinstance(v, str) and v.strip()]
    for nk, _k, v in norm:
        if nk == nl:
            return v
    best = None
    for nk, k, v in norm:
        if nk and nk in nl:
            cand = (-len(nk), nl.find(nk), k, v)
            if best is None or cand < best:
                best = cand
    return best[3] if best else None


# ══════════════════════════════════════════════════════════════════════
# 레퍼런스 매칭
# ══════════════════════════════════════════════════════════════════════
def _norm_nnn(nnn: Any) -> str:
    if nnn is None:
        return ""
    if isinstance(nnn, bool):
        raise ValueError(f"nnn 형식이 잘못되었습니다: {nnn!r}")
    if isinstance(nnn, int):
        if nnn < 0:
            raise ValueError(f"nnn 이 음수입니다: {nnn!r}")
        return f"{nnn:03d}"
    return _ascii_digits(_nfc(str(nnn)).strip())


def is_ref_cutout(filename: str) -> bool:
    """파일 이름에 컷아웃 표지 '_t'(뒤에 영문자 없음: _t, _t2, _tㄱ)가 있는지."""
    stem = os.path.splitext(_nfc(filename))[0]
    return bool(_REF_T_RE.search(stem))


def match_refs(refs_dir: str, nnn: Any, *, part: str | None = None) -> list[str]:
    """refs_dir 바로 아래에서 'NNN_' 로 시작하는 이미지(png/jpg/jpeg/webp)를 찾아 refs_dir 기준 상대 경로로 반환.
    정렬: '_t' 컷아웃 우선 → 이름순. (하위 폴더는 보지 않는다: bcut 등 탈락본 보관용)
    part 를 주면(확장, 순서만 바뀜) 'NNN_' 뒤 '_t…' 표지 앞까지가 part 와 같은 파일 → part 로 시작하는 파일
    → 나머지 순으로 먼저 두고, 각 묶음 안에서는 위 기본 정렬(비교 시 대소문자·공백·_·- 무시).
    refs_dir 가 비었거나 nnn 이 비었으면 [], 폴더가 없으면 ValueError."""
    if refs_dir is None or not str(refs_dir).strip():
        return []
    refs_dir = str(refs_dir).strip()
    key = _norm_nnn(nnn)
    if not key:
        return []
    if not os.path.isdir(refs_dir):
        raise ValueError(f"레퍼런스 폴더가 없습니다: {refs_dir}")
    prefix = key + "_"
    npart = _gloss_norm(part) if part else ""
    rows = []
    for fn in os.listdir(refs_dir):
        full = os.path.join(refs_dir, fn)
        nfn = _nfc(fn)
        if not nfn.startswith(prefix) or not os.path.isfile(full):
            continue
        if os.path.splitext(nfn)[1].lower() not in REF_EXTS:
            continue
        stem = os.path.splitext(nfn)[0][len(prefix):]
        mt = _REF_T_RE.search(stem)
        cut = mt is not None
        if npart:
            # '_t…' 표지 앞까지를 부위 이름으로 보고 비교: 같음 0 / part 로 시작 1 / 그 외 2
            rest = _gloss_norm(stem[: mt.start()] if mt else stem)
            prank = 0 if rest == npart else (1 if rest.startswith(npart) else 2)
            grp = (prank, 0 if cut else 1)
        else:
            grp = (0, 0 if cut else 1)
        rows.append((grp, nfn, fn))
    rows.sort()
    return [fn for _g, _n, fn in rows]


# ══════════════════════════════════════════════════════════════════════
# GPT 출력 크기 규칙
# ══════════════════════════════════════════════════════════════════════
def gpt_image_custom_size_ok(width: int, height: int) -> bool:
    """내장 OpenAI GPT Image 노드의 size=Custom 백엔드 검증과 같은 조건(bmk_canvas_snap 과 동일 복제)."""
    if width % 16 or height % 16:
        return False
    if not (480 <= width <= 3840 and 480 <= height <= 3840):
        return False
    if max(width, height) > 3 * min(width, height):
        return False
    return 655_360 <= width * height <= 8_294_400


def _within_limits(W: int, H: int) -> bool:
    return max(W, H) <= _GPT_MAX_EDGE and W * H <= _GPT_MAX_PIXELS


def _pos_int(v: Any, what: str) -> int:
    if isinstance(v, bool):
        raise ValueError(f"{what} 는 양의 정수여야 합니다: {v!r}")
    try:
        iv = int(v)
    except (TypeError, ValueError) as e:
        raise ValueError(f"{what} 는 양의 정수여야 합니다: {v!r}") from e
    if iv != v or iv <= 0:
        raise ValueError(f"{what} 는 양의 정수여야 합니다: {v!r}")
    return iv


def _floor16(x: float) -> int:
    return int(math.floor(x / _GPT_SNAP + 1e-9)) * _GPT_SNAP


@functools.lru_cache(maxsize=1)
def _valid_size_grid() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """gpt_image_custom_size_ok 를 만족하는 모든 (W, H) — 폴백 검색용(한 번만 계산)."""
    vals = np.arange(_GPT_MIN_EDGE, _GPT_MAX_EDGE + 1, _GPT_SNAP, dtype=np.int64)
    Wg, Hg = np.meshgrid(vals, vals, indexing="ij")
    Wg, Hg = Wg.ravel(), Hg.ravel()
    px = Wg * Hg
    ok = (
        (np.maximum(Wg, Hg) <= _GPT_MAX_RATIO * np.minimum(Wg, Hg))
        & (px >= _GPT_MIN_PIXELS)
        & (px <= _GPT_MAX_PIXELS)
    )
    return Wg[ok], Hg[ok], px[ok]


def _nearest_valid(w: int, h: int, target_area: float) -> tuple[int, int]:
    """제약을 만족하는 모든 16배수 크기 중 종횡 오차 최소 → 목표 면적에 가장 가까움 → 작은 W·H."""
    Wg, Hg, px = _valid_size_grid()
    ae = np.round(np.abs(np.log((Wg / Hg) / (w / h))), 12)
    ar = np.round(np.abs(np.log(px / max(target_area, 1.0))), 12)
    order = np.lexsort((Hg, Wg, ar, ae))
    i = int(order[0])
    return int(Wg[i]), int(Hg[i])


def _fallback_size(w: int, h: int, k: Fraction) -> tuple[int, int, str]:
    """정수 배율로 안 될 때: 목표 배율을 제약 안으로 줄여 16배수 내림 → 그래도 안 되면 최근접 유효 크기."""
    s = min(
        float(k),
        _GPT_MAX_EDGE / max(w, h),
        math.sqrt(_GPT_MAX_PIXELS / (w * h)),
    )
    W, H = _floor16(w * s), _floor16(h * s)
    if W >= _GPT_SNAP and H >= _GPT_SNAP and gpt_image_custom_size_ok(W, H):
        return W, H, "floor16"
    W, H = _nearest_valid(w, h, (w * s) * (h * s))
    return W, H, "nearest"


def gpt_output_size(w: int, h: int, rule: str = "user_k") -> tuple[int, int, dict]:
    """크롭 크기 w×h → GPT Image Custom 출력 크기 (W, H, info).

    user_k (기본, 사용자 실측 습관 24/24):
      정사각 → 2048×2048.
      비정사각 → k = max(2, 1024/짧은변). w·k, h·k 가 모두 16배수 정수면 그대로(method "k"),
      아니면 정수 k' = ceil(k) 로 (w·k', h·k') (method "int_k"). 긴 변 > 3840 또는 화소 > 8,294,400 이면 k' 를 줄인다.
    int_k_2560:
      긴 변 ≤ 2560 을 만족하는 최대 정수 k(최소 2; 정사각 포함). 3840/화소 상한을 넘으면 k 를 줄인다.
    두 규칙 공통 폴백: 그래도 안 되면 목표 배율을 상한 안으로 줄여 16배수 내림(method "floor16"),
      그 결과도 제약 밖(비 > 3 등)이면 제약을 만족하는 최근접 크기(method "nearest").
    info: rule, src, method, k(float), k_str(분수), scale[x,y], aspect_err((W/H)/(w/h)-1), pixels.
    결과는 항상 gpt_image_custom_size_ok 를 만족한다.
    """
    w = _pos_int(w, "w")
    h = _pos_int(h, "h")
    if rule not in GPT_SIZE_RULES:
        raise ValueError(f"알 수 없는 출력 크기 규칙: {rule!r} (가능: {', '.join(GPT_SIZE_RULES)})")

    W = H = 0
    method = ""
    if rule == "user_k":
        if w == h:
            W = H = 2048
            k = Fraction(2048, w)
            method = "square_preset"
        else:
            k = max(Fraction(2), Fraction(1024, min(w, h)))
            Wk, Hk = w * k, h * k
            if (
                Wk.denominator == 1
                and Hk.denominator == 1
                and gpt_image_custom_size_ok(int(Wk), int(Hk))
            ):
                W, H, method = int(Wk), int(Hk), "k"
            else:
                kp = math.ceil(k)
                while kp >= 1 and not _within_limits(w * kp, h * kp):
                    kp -= 1
                if kp >= 1 and gpt_image_custom_size_ok(w * kp, h * kp):
                    W, H, method, k = w * kp, h * kp, "int_k", Fraction(kp)
    else:  # int_k_2560
        k = Fraction(max(2, 2560 // max(w, h)))
        kp = int(k)
        while kp >= 1 and not _within_limits(w * kp, h * kp):
            kp -= 1
        if kp >= 1 and gpt_image_custom_size_ok(w * kp, h * kp):
            W, H, method, k = w * kp, h * kp, "int_k", Fraction(kp)

    if not method:
        W, H, method = _fallback_size(w, h, k)
    if not gpt_image_custom_size_ok(W, H):  # 방어: 위 경로는 항상 유효해야 한다
        raise ValueError(f"출력 크기 계산 실패: {w}x{h} → {W}x{H} ({rule}, {method})")

    aspect_err = (W / H) / (w / h) - 1.0
    info = {
        "rule": rule,
        "src": [w, h],
        "method": method,
        "k": round(float(k), 6),
        "k_str": f"{k.numerator}/{k.denominator}" if k.denominator != 1 else str(k.numerator),
        "scale": [round(W / w, 6), round(H / h, 6)],
        "aspect_err": round(aspect_err, 6) if abs(aspect_err) > 1e-12 else 0.0,
        "pixels": W * H,
    }
    if method in ("floor16", "nearest"):
        logger.debug(f"{_TAG} gpt_output_size {w}x{h} {rule} → {W}x{H} ({method}, aspect_err {info['aspect_err']})")
    return W, H, info


# ══════════════════════════════════════════════════════════════════════
# 셀 캐시 키
# ══════════════════════════════════════════════════════════════════════
def _norm_size(size: Any) -> list[int]:
    if isinstance(size, str):
        m = re.match(r"^\s*(\d+)\s*[xX×*]\s*(\d+)\s*$", size)
        if not m:
            raise ValueError(f"size 형식이 잘못되었습니다: {size!r} (예: [2048, 2048] 또는 '2048x2048')")
        return [int(m.group(1)), int(m.group(2))]
    try:
        a, b = size
        return [_pos_int(a, "size[0]"), _pos_int(b, "size[1]")]
    except (TypeError, ValueError) as e:
        raise ValueError(f"size 형식이 잘못되었습니다: {size!r} (예: [2048, 2048])") from e


def cell_key(
    model: str,
    quality: str,
    size: Any,
    background: Any,
    prompt: str,
    input_shas: Iterable[str],
    template_version: str,
    variant: str,
) -> str:
    """유료 호출 셀의 캐시 키 = sha256(정규 JSON) hex 앞 24자. **n 은 넣지 않는다**(n 4→8 이 재과금되지 않게).
    input_shas 는 업로드 순서대로(순서가 바뀌면 다른 키). size 는 [W, H] / (W, H) / 'WxH' 모두 같은 키.
    prompt 는 바이트 그대로(정규화 없음). 키 형식 버전 CELL_KEY_VERSION 을 함께 해시한다."""
    if isinstance(input_shas, (str, bytes)):
        raise ValueError("input_shas 는 문자열 목록이어야 합니다(문자열 하나가 아님).")
    if not isinstance(prompt, str):
        raise ValueError(f"prompt 는 문자열이어야 합니다: {type(prompt).__name__}")
    payload = {
        "v": CELL_KEY_VERSION,
        "model": str(model),
        "quality": str(quality),
        "size": _norm_size(size),
        "background": None if background is None else str(background),
        "prompt": prompt,
        "inputs": [str(s).strip().lower() for s in list(input_shas)],
        "template": str(template_version),
        "variant": str(variant),
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:24]
