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
      board_pref.json         보드가 고를 분석 설정(마지막 Analyze·Review — save_board_pref)·마지막 Export 해시(save_board_export)

공개 함수 (예외는 모두 ValueError + 한국어 메시지)
---------------------------------------------------
- project_root(base_dir, project)            프로젝트 폴더 경로(이름 검증만, 폴더는 만들지 않음)
- new_manifest / load_manifest / save_manifest  매니페스트 골격·읽기·원자 저장(rev += 1)
- save_bytes_ca / save_png_ca                내용 주소 저장(이미 있으면 쓰지 않음)
- pixel_sha                                  디코드된 픽셀 다이제스트(save_png_ca 파일명·cell_key 입력용)
- crop_id_for / assign_crop_ids              ASCII 크롭 ID (NNN_<blake2b 3바이트>)
- parse_crop_layer_name                      "NNN_부위 | A: …; B: …" 레이어명 파싱(레거시 관용)
- merge_layer_targets                        레이어명 파싱 + 스펙 _layer_targets(채택 타깃, origin "adopt") 병합
- load_glossary / translate                  한→영 용어집
- match_refs                                 레퍼런스 폴더에서 NNN_ 접두 이미지 매칭
- gpt_output_size / gpt_image_custom_size_ok GPT 출력 크기 규칙(user_k / int_k_2560)
- cell_key                                   유료 호출 셀 캐시 키(n 제외)
- 보조: resolve_path, write_json_atomic, file_fingerprint, load_spec_override
M2 (명세 M2_SPEC.md §1)
- manifest_lock / update_manifest            root 별 RLock, 잠금 안에서 최신본 로드 → 수정 → 바뀌었으면 저장
- merge_manifest / commit_merge              3-way 병합(컬렉션은 id 단위) / 노드 _commit 대체(잠금 + 병합 저장)
- root_key / register_root / known_roots / resolve_board_root / load_root_registry
                                             보드 URL 용 루트 레지스트리(키 = sha1(정규화 경로)[:12], 임의 경로 금지)
- save_board_pref / load_board_pref          <root>/board_pref.json — 보드가 고를 분석 설정(마지막 Analyze·Review)을 재시작 너머로(S4)
- save_board_export / last_export            같은 파일 — 마지막 Export 가 낸 PSD 의 해시 / 그 exports[] 기록('변경 없음' 포함, 없으면 마지막 기록)
- estimate_usd                               내장 GPT Image 노드 price_badge 표 복제 + Custom 크기 근사
S1 레이아웃 (명세 STUDIO_S1_SPEC.md §1.3·§3.6·§3.7)
- load_layout / update_layout / validate_layout  <root>/layout.json 읽기·낙관적 동시성 쓰기(rev 단조 증가, 이력 50)·구조 검사
- load_layout_version                        이력 판 layout_history/r<rev>.json 읽기(없거나 깨졌으면 None)
- layout_from_manifest                       이관: 매니페스트 크롭 → layout + PSD 기준점(레이아웃 모드 결과면 PSD 스냅숏으로)
- check_crop_name / check_target_text / check_layout_rect / layout_rect_problem   이름·대상 문구·사각형 규칙(서버 거절용)
- layout_parsed / layout_layer_name / new_layout_id / layout_sig   Import 가 쓰는 크롭 정의(합성 레이어명·고정 ID·지문)
- layout_geom_checks / psd_diff              편집기 검사 목록(L-·N-·P- 코드) / PSD 기준점 대 스냅숏 차이
S3 Studio 파이프라인 (명세 STUDIO_S3_SPEC.md §2.2·§2.4 — 노드에서 옮겨 노드·보드·vlm 이 같이 씀)
- merge_adopted(crop, parsed, ov) / new_targets(parsed, old_crop) / adopt_stale(crop, ov)
                                             Import 의 채택 병합·매니페스트 타깃 만들기, Prepare 채택 가드(Import 가 만들 타깃 ≠ 매니페스트)
- project_glossary(root, m)                  프로젝트 glossary.json + Import 의 glossary_path(source.glossary) 용어집
- prepare_inputs_fp(root, m)                 Prepare 입력 지문(스펙·참조 파일·specs/·refs.json·layout.json·용어집·refs_dir·source.layout.rev)
- 보조: _file_fp / _dir_fp / _spec_files_fp  노드 Project 지문·Import IS_CHANGED 와 같은 파일·폴더 지문(없으면 None)
- 보조: _spec_paths(root, refs_dir)          스펙이 가리키는 파일 경로(_spec_files_fp 의 스펙 쪽 — 보드 계획 서명이 기억해 stat 만)
다듬기 배치 3b (_proto/STUDIO_POLISH_PLAN_2610.md §11)
- check_export_subfolder(name)               Export 출력 하위 폴더 이름(output/design_patch/<이름> — "" = 프로젝트 폴더 이름) — 노드·pipeline·보드

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
- 동시 쓰기(M2): 보드 라우트·Run·노드가 같은 매니페스트를 쓴다. 쓰기는 모두 manifest_lock 안에서 디스크 최신본을
  다시 읽고 고친다(update_manifest / commit_merge). load_manifest / save_manifest 도 파일을 여는 동안 같은 잠금을
  잡는다 — Windows 는 누가 열고 있는 파일로 os.replace 를 못 하므로(WinError 5), 잠금 없이 읽기가 잦으면 저장이
  재시도 끝에 실패한다(실측: 읽기 스레드 4개 폴링 중 쓰기 13회 중 10회 실패 → 잠금 후 0회).
  잠금은 프로세스 안에서만 유효하다 — ComfyUI 프로세스 하나가 프로젝트 폴더를 쓴다고 가정하며 파일 잠금은 하지 않는다
  (두 ComfyUI 가 같은 폴더를 쓰면 보호되지 않음).
- M2 최상위 키(calls, rerolls, rejects, approvals)와 jobs[].reps(기본 1)는 디스크에서 읽을 때 채운다.
  new_manifest 는 M1 고정 키 집합 그대로다(처음 저장한 뒤 다시 읽으면 항상 있음).
- layout.json(S1)은 3-way 병합을 하지 않는다: 쓰기는 update_layout 하나이고 요청 rev 가 지금 rev 일 때만 쓴다(LayoutConflict).
  잠금은 매니페스트와 같은 manifest_lock 이다(레이아웃 쓰기는 짧고 드묾). 편집기 기하(스냅·핸들)는 서버에 없고
  JS layout_geom.js 와 테스트 오라클이 tools/fixtures/layout_rules.json 공용 표로 맞춘다 — 여기 규칙(이름·문구·사각형·GPT 크기·검사)도 같은 표.

버전 이력
---------
v4 (2026-10, Studio S3a)
- 노드에서 옮김(동작 그대로): _merge_adopted·_new_targets·_adopt_stale·_glossary → merge_adopted·new_targets·adopt_stale·
  project_glossary, 지문 _file_fp·_dir_fp·_spec_files_fp. 단 _dir_fp 는 목록 뒤 사라진 파일(원자 쓰기 임시 파일)을
  건너뜀 — 옛 노드는 FileNotFoundError(경합이 없으면 값은 같음). 새 prepare_inputs_fp(Prepare 가 매니페스트 prepare_inputs 로
  남기고 보드가 '반영 필요'를 판정).
v3 (2026-10)
- S1 레이아웃: layout.json 스키마·이관·쓰기·이름/문구/사각형 규칙·검사 목록·psd_diff.
v2 (2026-10)
- M2: 매니페스트 트랜잭션(잠금·3-way 병합), M2 키 기본값, 루트 레지스트리, 가격표(estimate_usd).
- 타깃만 채택(10/13, TARGETS_ONLY_SPEC §9.5): merge_layer_targets — Import PSD·Prepare 가드가 같은 규칙을 쓴다.
v1 (2026-10)
- M1 최초 구현. 명세 §1 검증표(user_k 13행 + 정사각 4행) 전부 일치, 결과는 항상 gpt_image_custom_size_ok.
"""

from __future__ import annotations

import contextlib
import functools
import hashlib
import io
import json
import logging
import math
import os
import re
import threading
import time
import unicodedata
import uuid
from fractions import Fraction
from typing import Any, Callable, Iterable

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
_SUBFOLDER_RE = re.compile(r"[\w\-.가-힣 ]+")  # Export 하위 폴더(배치 3-5b) — 노드 Export filename_prefix(_SAFE_PREFIX_RE)와 같은 글자
_SUBFOLDER_MAX = 64

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


def _file_fp(path: str):
    """file_fingerprint, 없거나 읽을 수 없으면 None(노드 IS_CHANGED·Project 지문·prepare_inputs_fp)."""
    try:
        return file_fingerprint(path)
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
            try:
                st = os.stat(full)
            except FileNotFoundError:  # 목록 뒤 사라짐 — 잠금 없이 부르는 보드 계획과 겹친 원자 쓰기의 임시 파일
                continue
            rows.append(f"{fn}|{st.st_size}|{st.st_mtime_ns}")
    return hashlib.sha1("\n".join(rows).encode("utf-8")).hexdigest()[:16]


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


def check_export_subfolder(name: Any) -> str:
    """Export 출력 하위 폴더 이름(배치 3-5b — ComfyUI output/design_patch/<이름>) → 앞뒤 공백을 뗀 이름, "" = 기본(프로젝트 폴더 이름).
    폴더 이름 하나만: filename_prefix 와 같은 글자(영문·숫자·한글·_ - . 공백 — 경로 구분자·드라이브 콜론·<>"|?*·제어 문자 없음)이고
    '..'·'.' 로 끝나는 이름('.' 포함)·Windows 예약 이름(CON·NUL·COM1 … — 확장자·대소문자 무관)·64자 초과는 ValueError.
    노드 Export·pipeline export 절·보드가 같이 쓴다."""
    if not isinstance(name, str):
        raise ValueError(f"subfolder 는 문자열이어야 합니다: {name!r}")
    s = name.strip()
    if not s:
        return ""
    if not _SUBFOLDER_RE.fullmatch(s):
        raise ValueError(f"subfolder 에는 폴더 이름 하나만 — 경로 구분자 없이 영문·숫자·한글·_ - . 공백만 쓸 수 있습니다: {name!r}")
    if ".." in s or s.endswith("."):
        raise ValueError(f"subfolder 에 '..' 이나 '.' 로 끝나는 이름은 쓸 수 없습니다: {name!r}")
    if _WIN_RESERVED_RE.match(s):
        raise ValueError(f"subfolder 에 Windows 예약 이름(CON·NUL·COM1 …)은 쓸 수 없습니다: {name!r}")
    if len(s) > _SUBFOLDER_MAX:
        raise ValueError(f"subfolder 는 {_SUBFOLDER_MAX}자 이하여야 합니다: {len(s)}자")
    return s


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
    """빈 매니페스트(M1 고정 키, rev 0). M2 키는 load_manifest 가 채운다."""
    return _skeleton(_check_project_name(project))


# M2 최상위 키 → 기본값 생성자 (load 시 없거나 null 이면 채움)
#   calls     [{call_id, cell_key, rep, status, backend, n, started, finished, elapsed_s, credits, urls, slots, error, approved_by}]
#   rerolls   {cell_key: 추가 호출 수}   rejects {cand_key: true}   approvals {pending_hash: {"time", "count"}}
_M2_KEYS = {"calls": list, "rerolls": dict, "rejects": dict, "approvals": dict}


def load_manifest(root: str) -> dict:
    """root/design_patch.json 을 읽는다. 없으면 new_manifest(폴더 이름). 빠진 고정 키·M2 키·jobs[].reps(1)는
    기본값으로 채우고 알 수 없는 추가 키는 그대로 둔다(저장 시 보존)."""
    path = os.path.join(root, MANIFEST_NAME)
    if not os.path.isfile(path):
        return new_manifest(os.path.basename(os.path.normpath(root)))
    with manifest_lock(root):  # Windows 는 열려 있는 파일로 os.replace 를 못 한다 → 같은 프로세스의 저장과 겹치지 않게
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
    for key, factory in _M2_KEYS.items():
        if m.get(key) is None:
            m[key] = factory()
    for job in m["jobs"]:
        if isinstance(job, dict) and job.get("reps") is None:
            job["reps"] = 1
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
    """rev += 1 후 원자 저장(tmp → os.replace). 새 rev 를 반환하고 m["rev"] 도 갱신한다(실패 시 되돌림).
    m 을 그대로 덮어쓴다 — 다른 쓰기와 겹칠 수 있는 곳은 update_manifest / commit_merge 를 쓸 것."""
    if not isinstance(m, dict) or m.get("schema") != SCHEMA:
        raise ValueError(f"매니페스트가 아니거나 schema 가 {SCHEMA!r} 가 아닙니다.")
    old = m.get("rev", 0)
    try:
        new_rev = int(old) + 1
    except (TypeError, ValueError) as e:
        raise ValueError(f"매니페스트 rev 가 정수가 아닙니다: {old!r}") from e
    m["rev"] = new_rev
    try:
        with manifest_lock(root):
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


def merge_layer_targets(parsed: dict, text: Any, *, crop_id: str | None = None, aliases: Iterable[str] = ()) -> tuple[dict, list[str]]:
    """parse_crop_layer_name 결과 + 스펙 _layer_targets(채택, 레이어명 '|' 뒤와 같은 문법) → (같은 모양, 경고).

    "003_펜이름표" + "A: name badge; B: red pen"
      → targets [{"tid": "A", "text": "name badge", "origin": "adopt"}, {"tid": "B", "text": "red pen", "origin": "adopt"}]
    PSD 문구가 빈 타깃(레거시 A 포함)과 PSD 에 없는 tid 는 _layer_targets 문구 + "origin": "adopt". 둘 다 문구가 있으면 PSD 가
    이긴다(다를 때만 경고). 순서 = PSD 타깃 → 새 tid(문자열 순서). 앞의 '<크롭 이름> |' 는 떼어 낸다(제안 레이어명을 통째로
    붙여 넣은 경우) — 이름이 parsed name 이거나 aliases(옛 이름, 예: psd_name) 중 하나이거나, crop_id 의 '-k' 앞부분이
    crop_id_for(이름) 일 때(D3: ID = 처음 이름 해시 — Studio 에서 이름을 바꾼 크롭의 스펙). 빈 문구 항목은 무시 + 경고.
    text 가 None·빈 문자열이면 그대로.
    라벨 없는 조각(다른 크롭 이름 앞머리 포함)·같은 라벨 두 번(라벨이 A–Z 라 26개 초과도 여기에 걸림)·문자열 아님은
    ValueError(PS 레이어명과 달리 글자를 대신 정하지 않는다).
    """
    out = {**parsed, "targets": [dict(t) for t in parsed["targets"]]}
    if text is None:
        return out, []
    if not isinstance(text, str):
        raise ValueError(f"_layer_targets 는 문자열이어야 합니다: {text!r}")
    body = _nfc(text)
    head = _PIPE_RE.split(body, maxsplit=1)
    name = head[0].strip()
    if len(head) == 2 and name and (name == parsed["name"] or name in aliases
                                    or (crop_id is not None and crop_id_for(name) == crop_id.split("-")[0])):
        body = head[1]
    entries: list[tuple[str, str]] = []
    for s in _TARGET_SPLIT_RE.split(body):
        s = s.strip()
        if not s:
            continue
        m = _TARGET_HEAD_RE.match(s)
        if not m:
            raise ValueError(f"_layer_targets 에 라벨 없는 조각이 있습니다('B: …' 처럼 적을 것): {s!r}")
        entries.append((_upper_tid(m.group(1)), m.group(2).strip()))
    used = [t for t, _ in entries]
    dup = sorted({t for t in used if used.count(t) > 1})
    if dup:
        raise ValueError(f"_layer_targets 에 같은 타깃 라벨이 두 번 있습니다({', '.join(dup)}): {text!r}")
    warnings: list[str] = []
    by_tid = {t["tid"]: t for t in out["targets"]}
    for tid, x in entries:
        t = by_tid.get(tid)
        if not x:
            warnings.append(f"_layer_targets {tid}: 문구가 비어 무시")
        elif t is None:
            out["targets"].append({"tid": tid, "text": x, "origin": "adopt"})
        elif not t["text"].strip():
            t.update(text=x, origin="adopt")
        elif t["text"].strip() != x:
            warnings.append(f"_layer_targets {tid}: 레이어명 문구 {t['text']!r} 가 이김({x!r} 무시)")
    return out, warnings


def merge_adopted(crop: dict, parsed: dict, ov: dict) -> tuple[dict, list[str]]:
    """merge_layer_targets(레이어명 파싱 + 스펙 _layer_targets). 크롭 ID·psd_name 을 넘겨 Studio 에서 이름을 바꾼 크롭의 스펙
    앞머리 '<처음 이름> |' 도 뗀다. Import(_targets)·Prepare 가드(adopt_stale)·Studio 채택(vlm.adopt_write)이 같은 규칙."""
    return merge_layer_targets(parsed, ov.get("_layer_targets"), crop_id=crop["id"],
                               aliases=[crop["psd_name"]] if crop.get("psd_name") else [])


def new_targets(parsed: dict, old_crop: dict | None) -> list[dict]:
    """레이어명 타깃 → 매니페스트 타깃. 영문(ASCII) 문구는 name_en, 한글 문구·레거시는 label(번역은 Prepare).
    parsed 가 merge_layer_targets 를 거쳤으면 채택 타깃의 origin 을 그대로 옮긴다(채택 타깃에만 키가 있음)."""
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
        if t.get("origin"):
            tt["origin"] = t["origin"]
        out.append(tt)
    return out


def adopt_stale(crop: dict, ov: dict) -> str:
    """Prepare 가드: 레이어명 + 스펙 _layer_targets 로 Import 가 만들 타깃(tid, 라벨)이 매니페스트와 다르면 그 이유, 같으면 "".
    Draft 는 Import 뒤에 채택을 쓰거나 지우므로(mock 채택 양보) 그 큐의 매니페스트는 옛 타깃이다 — 그 크롭의 jobs 를 만들지
    않아 Run 해시가 바뀐다(드라이런, 옛 타깃 과금 없음). 문구는 채택·채택 해제 어느 쪽에도 맞게."""
    p = parse_crop_layer_name(crop["layer_name"])
    try:
        merged, _w = merge_adopted(crop, p, ov)
    except ValueError as e:
        return f"채택 확인 못 함 — {e}"
    want = [(t["tid"], t["label"]) for t in new_targets(merged, None)]
    if want != [(t["tid"], t["label"]) for t in crop["targets"]]:
        return "매니페스트 타깃이 레이어명 + _layer_targets 와 다름 — 다음 큐에서 Import PSD 가 반영"
    return ""


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


def project_glossary(root: str, m: dict) -> dict:
    """프로젝트 용어집 = <root>/glossary.json + Import 의 glossary_path(source.glossary, 같은 키는 뒤가 이김). Prepare·Spec Draft·
    Studio 계획·채택이 같은 값을 쓴다. 형식 오류는 ValueError."""
    g = load_glossary(os.path.join(root, "glossary.json"))
    g.update(load_glossary((m.get("source") or {}).get("glossary") or ""))
    return g


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


# ══════════════════════════════════════════════════════════════════════
# 매니페스트 트랜잭션 (M2)
# ══════════════════════════════════════════════════════════════════════
_MANIFEST_LOCKS: dict[str, threading.RLock] = {}
_MANIFEST_LOCKS_GUARD = threading.Lock()

# 둘 다 바뀐 최상위 키 중 항목 단위로 합치는 컬렉션: 목록(항목 id 필드) / dict(키) / analysis(후보 → params_hash)
_MERGE_ID_LISTS = {
    "crops": ("id",),
    "candidates": ("key",),
    "jobs": ("cell_key",),
    "exports": ("rev", "psd"),
    "calls": ("call_id",),
}
_MERGE_KEYED_DICTS = ("picks", "rejects", "rerolls", "approvals", "mask_edits")  # mask_edits = S4 마스크 편집(후보 key 단위)
_MISSING = object()


def _root_id(root: str) -> str:
    """같은 폴더면 같은 문자열(절대 경로 + Windows 대소문자·구분자 정규화) — 잠금·레지스트리 키의 입력."""
    if not isinstance(root, str) or not root.strip():
        raise ValueError(f"프로젝트 폴더 경로가 비어 있습니다: {root!r}")
    return os.path.normcase(os.path.abspath(root.strip()))


@contextlib.contextmanager
def manifest_lock(root: str):
    """root 별 프로세스 전역 RLock(같은 스레드는 다시 잡을 수 있음). 파일 잠금은 하지 않는다 —
    ComfyUI 프로세스 하나만 프로젝트 폴더를 쓴다고 가정한다(모듈 docstring '동시 쓰기')."""
    rid = _root_id(root)
    with _MANIFEST_LOCKS_GUARD:
        lock = _MANIFEST_LOCKS.get(rid)
        if lock is None:
            lock = _MANIFEST_LOCKS[rid] = threading.RLock()
    with lock:
        yield


def _load_existing(root: str) -> dict:
    if not os.path.isfile(os.path.join(root, MANIFEST_NAME)):
        raise ValueError(f"매니페스트가 없습니다: {root} (BMK Design Patch Project 를 먼저 실행하세요)")
    return load_manifest(root)


def update_manifest(root: str, mutate: Callable[[dict], Any]) -> tuple[Any, int]:
    """잠금 → 디스크 최신본 로드 → mutate(m) → 바뀐 것이 있으면 저장(rev + 1). (mutate 반환값, rev) 를 돌려준다.
    mutate 는 잠금을 쥔 채 도는 짧은 동기 함수여야 한다(await·네트워크·무거운 계산 금지 — 미리 해 두고 결과만 넣을 것).
    mutate 안에서 update_manifest / commit_merge 를 다시 부르지 말 것(잠금은 재진입되지만 바깥 저장이 안쪽 변경을 덮는다).
    mutate 가 예외를 내면 저장하지 않고 그대로 올린다. mutate 가 rev 를 바꿔도 무시한다.
    매니페스트 파일이 없으면 ValueError(새 프로젝트를 만들지 않는다)."""
    with manifest_lock(root):
        m = _load_existing(root)
        rev = m["rev"]
        before = _jsonable(m)
        result = mutate(m)
        m["rev"] = rev
        if _jsonable(m) != before:
            rev = save_manifest(root, m)
    return result, rev


def _merge3(b: Any, o: Any, t: Any) -> tuple[Any, bool]:
    """값 하나의 3-way → (결과, 충돌). 없는 값 = _MISSING(결과가 _MISSING 이면 삭제). 충돌이면 ours."""
    if o == b:
        return t, False
    if t == b or o == t:
        return o, False
    return o, True


def _ordered_keys(*dicts: Any) -> list:
    keys: dict = {}
    for d in dicts:
        if isinstance(d, dict):
            keys.update(dict.fromkeys(d))
    return list(keys)


def _index_items(seq: Any, fields: tuple[str, ...]) -> dict | None:
    """목록 → {id 튜플: 항목}. 없음/null = 빈 목록. 목록이 아니거나, 항목이 dict 가 아니거나, id 필드가 없거나
    스칼라가 아니거나, id 가 겹치면 None(그 컬렉션은 항목 단위로 합칠 수 없음)."""
    if seq is _MISSING or seq is None:
        return {}
    if not isinstance(seq, list):
        return None
    out: dict = {}
    for it in seq:
        if not isinstance(it, dict) or any(f not in it for f in fields):
            return None
        key = tuple(it[f] for f in fields)
        if not all(v is None or isinstance(v, (str, int, float)) for v in key) or key in out:
            return None
        out[key] = it
    return out


def _merge_items(name: str, b: Any, o: Any, t: Any, conflicts: list[str]) -> Any:
    """id 목록의 항목 단위 3-way. 순서 = ours 순서, 그 뒤에 theirs 에만 있는 항목(theirs 순서)."""
    fields = _MERGE_ID_LISTS[name]
    bi, oi, ti = (_index_items(x, fields) for x in (b, o, t))
    if bi is None or oi is None or ti is None:
        conflicts.append(name)
        return o
    out = []
    for key in _ordered_keys(oi, ti):
        v, c = _merge3(bi.get(key, _MISSING), oi.get(key, _MISSING), ti.get(key, _MISSING))
        if c:
            conflicts.append(f"{name}/{'|'.join(str(x) for x in key)}")
        if v is not _MISSING:
            out.append(v)
    return out


def _merge_keyed(name: str, b: Any, o: Any, t: Any, conflicts: list[str]) -> Any:
    """dict 의 키 단위 3-way(없음/null = 빈 dict). 키 순서 = ours, 그 뒤에 theirs 에만 있는 키."""
    bd, od, td = ({} if x is _MISSING or x is None else x for x in (b, o, t))
    if not (isinstance(bd, dict) and isinstance(od, dict) and isinstance(td, dict)):
        conflicts.append(name)
        return o
    out = {}
    for key in _ordered_keys(od, td):
        v, c = _merge3(bd.get(key, _MISSING), od.get(key, _MISSING), td.get(key, _MISSING))
        if c:
            conflicts.append(f"{name}/{key}")
        if v is not _MISSING:
            out[key] = v
    return out


def _merge_analysis(b: Any, o: Any, t: Any, conflicts: list[str]) -> Any:
    """analysis = {후보 key: {params_hash: entry}} — (후보, params_hash) 단위 3-way.
    후보 값이 dict 가 아니면(옛 형식) 후보 단위로. 합친 뒤 빈 후보는 ours·theirs 둘 다 가진 경우만 남긴다."""
    bd, od, td = ({} if x is _MISSING or x is None else x for x in (b, o, t))
    if not (isinstance(bd, dict) and isinstance(od, dict) and isinstance(td, dict)):
        conflicts.append("analysis")
        return o
    out = {}
    for cand in _ordered_keys(od, td):
        cb, co, ct = (d.get(cand, _MISSING) for d in (bd, od, td))
        if all(x is _MISSING or isinstance(x, dict) for x in (cb, co, ct)):
            sub = _merge_keyed(f"analysis/{cand}", cb, co, ct, conflicts)
            if sub or (co is not _MISSING and ct is not _MISSING):
                out[cand] = sub
            continue
        v, c = _merge3(cb, co, ct)
        if c:
            conflicts.append(f"analysis/{cand}")
        if v is not _MISSING:
            out[cand] = v
    return out


def merge_manifest(base: dict, ours: dict, theirs: dict) -> tuple[dict, list[str]]:
    """3-way 병합 → (merged, conflicts). base = ours 를 만들기 시작한 시점의 매니페스트, theirs = 디스크 최신본.

    최상위 키마다: ours == base → theirs, theirs == base → ours, 둘이 같게 바뀜 → 그 값.
    둘 다 다르게 바뀌었으면 컬렉션은 항목 단위로 같은 규칙을 적용하고(crops.id, candidates.key, jobs.cell_key,
    exports.(rev, psd), calls.call_id / picks·rejects·rerolls·approvals·mask_edits 의 키 / analysis 의 (후보, params_hash)),
    그 밖의 키나 같은 항목이 양쪽에서 다르게 바뀐 경우는 ours 를 쓰고 conflicts 에 경로를 적는다
    (예: "source", "candidates/h_abc", "picks/002_1a2b3c/A", "analysis/h_abc/9f8e"). 삭제도 변경으로 본다.
    항목 단위로 합칠 수 없는 컬렉션(id 없음·중복 등)은 통째로 ours + 충돌.
    목록 순서 = ours 순서 뒤에 theirs 에만 새로 생긴 항목. rev 는 병합하지 않는다: merged['rev'] = theirs['rev']
    (commit_merge 가 저장하며 +1). 입력은 바꾸지 않고, 결과는 입력과 공유하지 않는 JSON 기본형이다."""
    for what, d in (("base", base), ("ours", ours), ("theirs", theirs)):
        if not isinstance(d, dict):
            raise ValueError(f"merge_manifest: {what} 가 매니페스트(dict)가 아닙니다: {type(d).__name__}")
    b, o, t = _jsonable(base), _jsonable(ours), _jsonable(theirs)
    conflicts: list[str] = []
    merged: dict = {}
    for key in _ordered_keys(o, t, b):
        if key == "rev":
            merged["rev"] = t.get("rev", 0)
            continue
        bv, ov, tv = (d.get(key, _MISSING) for d in (b, o, t))
        v, c = _merge3(bv, ov, tv)
        if c:
            if key in _MERGE_ID_LISTS:
                v = _merge_items(key, bv, ov, tv, conflicts)
            elif key in _MERGE_KEYED_DICTS:
                v = _merge_keyed(key, bv, ov, tv, conflicts)
            elif key == "analysis":
                v = _merge_analysis(bv, ov, tv, conflicts)
            else:
                conflicts.append(key)
        if v is not _MISSING:
            merged[key] = v
    return merged, conflicts


def commit_merge(root: str, before_snapshot: dict | str, ours: dict) -> tuple[dict, int, list[str]]:
    """노드 저장(M1 _commit 대체): 잠금 → 디스크 최신본(theirs) → merge_manifest(before_snapshot, ours, theirs) → 저장.
    before_snapshot = 노드가 매니페스트를 연 직후의 상태(dict, 또는 M1 _snapshot 이 만든 JSON 문자열).
    반환 (merged, rev, conflicts): merged = 디스크에 있는 내용(rev 포함). 합친 결과가 디스크와 같으면(ours 가 바꾼 것이
    없거나 이미 반영됨) 저장하지 않고 디스크 rev 를 그대로 돌려준다 — 같은 입력의 재실행은 rev 를 올리지 않는다.
    ours·before_snapshot 은 바꾸지 않는다(ours['rev'] 가 필요하면 반환 rev 를 쓸 것). 매니페스트가 없으면 ValueError."""
    base = json.loads(before_snapshot) if isinstance(before_snapshot, str) else before_snapshot
    with manifest_lock(root):
        theirs = _load_existing(root)
        merged, conflicts = merge_manifest(base, ours, theirs)
        rev = theirs["rev"]
        if merged != theirs:
            rev = save_manifest(root, merged)
    if conflicts:
        logger.warning(f"{_TAG} 매니페스트 병합 충돌 {len(conflicts)}건(이 노드의 값 사용): {', '.join(conflicts[:8])}"
                       + (" …" if len(conflicts) > 8 else ""))
    return merged, rev, conflicts


# ══════════════════════════════════════════════════════════════════════
# 루트 레지스트리 (M2) — 보드 URL 은 경로 대신 키만 싣는다
# ══════════════════════════════════════════════════════════════════════
ROOTS_FILE = "_roots.json"
_ROOTS_SCHEMA = "bmk.design_patch.roots/1"
_ROOT_KEY_RE = re.compile(r"[0-9a-f]{12}")
_ROOTS: dict[str, dict] = {}
_ROOTS_LOCK = threading.Lock()


def root_key(root: str) -> str:
    """레지스트리 키 = sha1(정규화한 절대 경로)[:12] (소문자 hex). 같은 폴더를 다르게 적어도(대소문자·구분자) 같은 키."""
    return hashlib.sha1(_root_id(root).encode("utf-8")).hexdigest()[:12]


def _registry_entries(projects_dir: str) -> dict[str, dict]:
    """<projects_dir>/_roots.json 의 유효 항목 {key: {"root","project","time"}}. root 는 파일 값이 아니라
    projects_dir/<project> 로 다시 만든다(파일을 고쳐도 이 폴더 밖을 가리킬 수 없음). 이름 검증·키 일치·매니페스트 존재를
    모두 통과한 항목만. 파일이 없거나 깨졌으면 {}(경고)."""
    path = os.path.join(projects_dir, ROOTS_FILE)
    if not os.path.isfile(path):
        return {}
    try:
        data = _read_json(path, "루트 레지스트리")
    except ValueError as e:
        logger.warning(f"{_TAG} {e} → 무시")
        return {}
    rows = data.get("roots") if isinstance(data, dict) else None
    out: dict[str, dict] = {}
    for key, e in rows.items() if isinstance(rows, dict) else ():
        if not isinstance(e, dict):
            continue
        try:
            name = _check_project_name(e.get("project"))
        except ValueError:
            continue
        root = os.path.join(projects_dir, name)
        if key == root_key(root) and os.path.isfile(os.path.join(root, MANIFEST_NAME)):
            out[key] = {"root": root, "project": name, "time": str(e.get("time") or "")}
    return out


def register_root(root: str) -> str:
    """프로젝트 폴더를 보드에 노출하고 키를 돌려준다(Review 노드). <base_dir>/bmk_design_patch/_roots.json 에도 원자 저장해
    재시작 뒤 load_root_registry 로 되살린다. root 는 project_root() 모양(…/bmk_design_patch/<project>)이고
    매니페스트가 있어야 한다(아니면 ValueError)."""
    if not isinstance(root, str) or not root.strip():
        raise ValueError(f"프로젝트 폴더 경로가 비어 있습니다: {root!r}")
    root = os.path.abspath(root.strip())
    projects_dir, name = os.path.split(root)
    if os.path.normcase(os.path.basename(projects_dir)) != os.path.normcase(PROJECTS_DIRNAME):
        raise ValueError(f"Design Patch 프로젝트 폴더(…/{PROJECTS_DIRNAME}/<project>)가 아닙니다: {root}")
    if _check_project_name(name) != name:
        raise ValueError(f"프로젝트 폴더 이름이 잘못되었습니다: {name!r}")
    if not os.path.isfile(os.path.join(root, MANIFEST_NAME)):
        raise ValueError(f"매니페스트가 없는 폴더는 등록할 수 없습니다: {root}")
    key = root_key(root)
    with _ROOTS_LOCK:
        entries = _registry_entries(projects_dir)
        entries[key] = {"root": root, "project": name, "time": time.strftime("%Y-%m-%d %H:%M:%S")}
        write_json_atomic(os.path.join(projects_dir, ROOTS_FILE), {
            "schema": _ROOTS_SCHEMA,
            "roots": {k: {"project": e["project"], "time": e["time"]} for k, e in entries.items()},
        })
        _ROOTS.update(entries)
    return key


def load_root_registry(base_dir: str) -> int:
    """<base_dir>/bmk_design_patch/_roots.json 의 유효 항목을 메모리 레지스트리에 넣고 개수를 돌려준다(없으면 0).
    재시작 뒤 보드 링크 복구용 — 보드 라우트 등록 때 ComfyUI input 디렉터리로 한 번 부른다.
    다른 base_dir 의 프로젝트는 그 Review 노드가 다시 실행될 때 등록된다."""
    if not isinstance(base_dir, str) or not base_dir.strip():
        raise ValueError("base_dir 가 비어 있습니다.")
    entries = _registry_entries(os.path.join(os.path.abspath(base_dir.strip()), PROJECTS_DIRNAME))
    with _ROOTS_LOCK:
        _ROOTS.update(entries)
    return len(entries)


def known_roots() -> list[dict]:
    """등록된 프로젝트 [{"key","root","project","time"}], 최근 등록 순. 매니페스트가 사라진 프로젝트는 뺀다."""
    with _ROOTS_LOCK:
        rows = [dict(e, key=k) for k, e in _ROOTS.items()]
    rows = [r for r in rows if os.path.isfile(os.path.join(r["root"], MANIFEST_NAME))]
    rows.sort(key=lambda r: r["time"], reverse=True)
    return rows


def resolve_board_root(param: Any) -> str | None:
    """보드 요청의 root 파라미터 → 프로젝트 폴더. 레지스트리 키(소문자 hex 12자)만 받는다:
    경로·형식이 다른 값·모르는 키·매니페스트가 사라진 프로젝트는 None(임의 경로를 열 수 없게)."""
    if not isinstance(param, str) or not _ROOT_KEY_RE.fullmatch(param):
        return None
    with _ROOTS_LOCK:
        e = _ROOTS.get(param)
    if e is None or not os.path.isfile(os.path.join(e["root"], MANIFEST_NAME)):
        return None
    return e["root"]


BOARD_PREF_NAME = "board_pref.json"


def _board_pref_file(root: str) -> dict:
    """board_pref.json 의 객체 {"analyze": 분석 설정, "export": export 해시}. 없거나 읽지 못하거나 객체가 아니면 {}."""
    path = os.path.join(root, BOARD_PREF_NAME)
    if not os.path.isfile(path):
        return {}
    try:
        d = _read_json(path, "보드 설정")
    except ValueError:
        return {}
    return d if isinstance(d, dict) else {}


def _save_board_pref_key(root: str, key: str, value: Any, lost: str) -> None:
    """board_pref.json 의 한 키만 바꿔 원자 저장(같으면 쓰지 않음, 다른 키는 그대로). 쓰지 못하면 경고만(lost = 그 때문에 생기는 일)."""
    cur = _board_pref_file(root)
    if cur.get(key) == value:
        return
    try:
        write_json_atomic(os.path.join(root, BOARD_PREF_NAME), dict(cur, **{key: value}))
    except OSError as e:
        logger.warning(f"{_TAG} 보드 설정을 저장하지 못함({e}) — {lost}")


def save_board_pref(root: str, analyze: dict) -> None:
    """보드가 고를 분석 설정(board._PREF 에 넣는 마지막 Analyze·Review 설정)을 <root>/board_pref.json 에 남긴다.
    재시작 뒤 노드 모듈이 등록된 프로젝트마다 되살린다 — 설정을 A→B→A 로 되돌린 프로젝트에서 보드가 마지막 삽입 항목(B)으로 ★ 합성·
    마스크 편집 기준을 잡지 않게. 매니페스트 밖이라 rev·Project 지문이 그대로다(Analyze 가지 둘을 번갈아 돌려도 하류가 다시 돌지 않음,
    DP-INT-3)."""
    _save_board_pref_key(root, "analyze", analyze, "재시작 뒤 보드는 가장 최근 분석 항목을 봄")


def load_board_pref(root: str) -> dict | None:
    """save_board_pref 의 분석 설정. 없거나 읽지 못하거나 모양이 틀리면 None(보드는 가장 최근 분석 항목)."""
    a = _board_pref_file(root).get("analyze")
    ok = isinstance(a, dict) and a and all(isinstance(k, str) and isinstance(v, (str, int, float)) for k, v in a.items())
    return a if ok else None


def save_board_export(root: str, export_hash: str) -> None:
    """Export 가 낸 PSD(새 파일·'변경 없음'으로 다시 낸 기존 파일)의 export 해시를 board_pref.json 에 남긴다(last_export).
    '변경 없음'은 exports[] 에 아무것도 더하지 않으므로 설정을 켬→끔→켬으로 돌리면 exports[-1] 은 끔 기록인데 최신 PSD 는 켬이다
    (리뷰 analysis-r3-01). 매니페스트에 쓰면 rev·Project 지문이 바뀌어 가지를 번갈아 돌릴 때마다 하류가 다시 돈다(DP-INT-3)."""
    _save_board_pref_key(root, "export", export_hash, "보드 편집 창·★ 합성과 Studio 처음 값이 이전 Export 기록의 설정을 따름")


def last_export(root: str, m: dict) -> dict | None:
    """마지막 Export 가 낸 PSD 의 exports[] 기록 = save_board_export 해시와 같은 마지막 항목, 없으면(해시를 남기기 전 프로젝트·매니페스트를
    다시 만듦) exports[] 의 마지막 항목, 기록이 없으면 None. 보드 ★ 합성·마스크 편집 창의 Compose 설정과 Studio 처음 값이 이것을 따른다."""
    rows = [e for e in m.get("exports") or [] if isinstance(e, dict)]
    h = _board_pref_file(root).get("export")
    hit = [e for e in rows if isinstance(h, str) and e.get("hash") == h]
    return (hit or rows or [None])[-1]


def board_pref_v(root: str) -> str | None:
    """board_pref.json 내용 지문(sha1 12자), 없거나 읽지 못하면 None. 보드 state 플래그 pref_v(unchanged 응답에도) — 분석·Export 설정이
    바뀌어도(save_board_pref·save_board_export) 매니페스트 rev 는 그대로라, Studio 는 이것으로 편집 창·★ 합성·stale 을 다시 받는다."""
    try:
        with open(os.path.join(root, BOARD_PREF_NAME), "rb") as f:
            return hashlib.sha1(f.read()).hexdigest()[:12]
    except OSError:
        return None


# ══════════════════════════════════════════════════════════════════════
# 가격표 (M2) — comfy_api_nodes/nodes_openai.py OpenAIGPTImageNodeV2.price_badge 복제 (ComfyUI v0.39)
# 표가 바뀌면 tools 의 가격 테스트가 원본 식을 파싱해 비교하므로 실패한다 → 여기도 고칠 것.
# ══════════════════════════════════════════════════════════════════════
_PRICE_RANGES_25 = {
    "low": (0.0023, 0.0283),
    "medium": (0.0056, 0.0636),
    "high": (0.0222, 0.2544),
    "xhigh": (0.0388, 0.4523),
    "max": (0.0887, 1.0175),
}
_PRICE_RANGES = {  # 모델 → 품질 → (lo, hi): 프리셋이 아닌 크기(auto·Custom)일 때 badge 가 보여 주는 범위
    "gpt-image-1": {"low": (0.011, 0.02), "medium": (0.042, 0.07), "high": (0.167, 0.25)},
    "gpt-image-1.5": {"low": (0.009, 0.02), "medium": (0.034, 0.062), "high": (0.133, 0.22)},
    "gpt-image-2": {"low": (0.0019, 0.0237), "medium": (0.0186, 0.2135), "high": (0.0744, 0.8539)},
    "gpt-image-2.5-flare": _PRICE_RANGES_25,
    "gpt-image-2.5-sunburst": _PRICE_RANGES_25,
}
_PRICE_PRESETS = {  # 가족 → 품질 → "WxH" → 1장 가격
    "gpt-image-2": {
        "low": {"1024x1024": 0.0071, "1024x1536": 0.0057, "1536x1024": 0.0057, "2048x2048": 0.0143, "2048x1152": 0.0057, "1152x2048": 0.0057, "3840x2160": 0.0134, "2160x3840": 0.0134},
        "medium": {"1024x1024": 0.0632, "1024x1536": 0.0494, "1536x1024": 0.0494, "2048x2048": 0.1284, "2048x1152": 0.0509, "1152x2048": 0.0509, "3840x2160": 0.1201, "2160x3840": 0.1201},
        "high": {"1024x1024": 0.2529, "1024x1536": 0.1976, "1536x1024": 0.1976, "2048x2048": 0.5138, "2048x1152": 0.2034, "1152x2048": 0.2034, "3840x2160": 0.4803, "2160x3840": 0.4803},
    },
    "gpt-image-2.5": {
        "low": {"1024x1024": 0.0084, "1024x1536": 0.0068, "1536x1024": 0.0068, "2048x2048": 0.0170, "2048x1152": 0.0067, "1152x2048": 0.0067, "3840x2160": 0.0159, "2160x3840": 0.0159},
        "medium": {"1024x1024": 0.0188, "1024x1536": 0.0147, "1536x1024": 0.0147, "2048x2048": 0.0383, "2048x1152": 0.0157, "1152x2048": 0.0157, "3840x2160": 0.0371, "2160x3840": 0.0371},
        "high": {"1024x1024": 0.0753, "1024x1536": 0.0589, "1536x1024": 0.0589, "2048x2048": 0.1531, "2048x1152": 0.0606, "1152x2048": 0.0606, "3840x2160": 0.1431, "2160x3840": 0.1431},
        "xhigh": {"1024x1024": 0.1339, "1024x1536": 0.1055, "1536x1024": 0.1055, "2048x2048": 0.2721, "2048x1152": 0.1077, "1152x2048": 0.1077, "3840x2160": 0.2544, "2160x3840": 0.2544},
        "max": {"1024x1024": 0.3013, "1024x1536": 0.2354, "1536x1024": 0.2354, "2048x2048": 0.6123, "2048x1152": 0.2424, "1152x2048": 0.2424, "3840x2160": 0.5724, "2160x3840": 0.5724},
    },
}
_PRICE_FAMILY = {"gpt-image-2.5-flare": "gpt-image-2.5", "gpt-image-2.5-sunburst": "gpt-image-2.5"}
_PRICE_PER_INPUT_IMAGE = {  # 입력 이미지 1장당 (lo, hi)
    "gpt-image-1": (0.0019, 0.0019),
    "gpt-image-1.5": (0.0016, 0.0016),
    "gpt-image-2": (0.0098, 0.0147),
    "gpt-image-2.5-flare": (0.0117, 0.0176),
    "gpt-image-2.5-sunburst": (0.0117, 0.0176),
}


def _price_curve(points: list[tuple[int, float]], px: int) -> float:
    """(화소, 가격) 점들의 화소 선형 보간. 범위 밖은 끝점의 화소당 가격으로 비례."""
    if px <= points[0][0]:
        return points[0][1] * px / points[0][0]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if px <= x1:
            return y0 + (y1 - y0) * (px - x0) / (x1 - x0)
    return points[-1][1] * px / points[-1][0]


def _custom_size_price(presets: dict, w: int, h: int, rng: tuple[float, float]) -> tuple[float, float]:
    """프리셋이 아닌 크기의 1장 가격 근사 (lo, hi). 프리셋을 정사각 / 비정사각 두 갈래의 (화소, 가격) 곡선으로 나눠
    보간한다(같은 화소면 정사각이 더 비쌈: 2.5 max 1024² 0.3013 > 1024x1536 0.2354). 정사각 크기는 (정사각, 정사각),
    비정사각은 (두 곡선 중 싼 값, 비싼 값) — hi 는 보수적 상한. 둘 다 badge 의 Custom 범위 안으로 자른다."""
    curves: dict[bool, dict[int, float]] = {True: {}, False: {}}
    for size, price in presets.items():
        pw, ph = (int(v) for v in size.split("x"))
        pts = curves[pw == ph]
        pts[pw * ph] = max(price, pts.get(pw * ph, 0.0))
    sq = _price_curve(sorted(curves[True].items()), w * h)
    if w == h:
        lo = hi = sq
    else:
        ns = _price_curve(sorted(curves[False].items()), w * h)
        lo, hi = min(sq, ns), max(sq, ns)
    return min(max(lo, rng[0]), rng[1]), min(max(hi, rng[0]), rng[1])


def estimate_usd(model: str, quality: str, size_wh: Any, n: int, n_input_images: int) -> tuple[float, float]:
    """GPT Image edits 호출 1건의 예상 USD (lo, hi) — 내장 노드 price_badge 와 같은 식:
        (출력 1장 가격 + 입력 이미지 수 × 입력 1장 가격) × n
    출력 1장 가격: 크기가 그 모델 가족의 프리셋("2048x2048" 등)이면 표 값(lo = hi). 프리셋 표가 없는 모델
    (gpt-image-1 / 1.5)은 badge 와 같이 품질 범위.
    Custom 크기는 badge 가 넓은 범위(2.5 max: 0.0887–1.0175)만 보여 주므로 **근사**를 쓴다: 같은 품질의 프리셋을
    정사각·비정사각 곡선으로 나눠 화소 수로 선형 보간(끝 밖은 화소 비례), 정사각은 lo = hi, 비정사각은 lo = 비정사각 곡선,
    hi = 정사각 곡선(보수적). 결과는 badge 범위 안으로 자른다. 실제 청구액은 calls[].credits 를 볼 것.
    size_wh = [W, H] / (W, H) / "WxH". 모르는 모델·품질이나 잘못된 n·입력 수는 ValueError. 값은 소수 6자리 반올림."""
    ranges = _PRICE_RANGES.get(model)
    if ranges is None:
        raise ValueError(f"가격표에 없는 모델입니다: {model!r} (가능: {', '.join(_PRICE_RANGES)})")
    rng = ranges.get(quality)
    if rng is None:
        raise ValueError(f"{model} 에 없는 품질입니다: {quality!r} (가능: {', '.join(ranges)})")
    w, h = _norm_size(size_wh)
    n = _pos_int(n, "n")
    if isinstance(n_input_images, bool) or not isinstance(n_input_images, int) or n_input_images < 0:
        raise ValueError(f"n_input_images 는 0 이상의 정수여야 합니다: {n_input_images!r}")
    presets = _PRICE_PRESETS.get(_PRICE_FAMILY.get(model, model), {}).get(quality)
    if presets is None:
        out = rng
    elif f"{w}x{h}" in presets:
        out = (presets[f"{w}x{h}"],) * 2
    else:
        out = _custom_size_price(presets, w, h, rng)
    per = _PRICE_PER_INPUT_IMAGE[model]
    return (round((out[0] + n_input_images * per[0]) * n, 6), round((out[1] + n_input_images * per[1]) * n, 6))


# ══════════════════════════════════════════════════════════════════════
# 레이아웃 (S1) — <root>/layout.json: Studio 가 편집하는 크롭 사각형·이름·대상·ID 의 원천 (STUDIO_S1_SPEC §1)
#   {"schema", "rev", "canvas": [W, H], "psd": {"path", "fingerprint", "crop_group", "at", "crops": [{name, layer_name, rect}]},
#    "crops": [{"id", "name", "rect", "targets": [{"tid", "text", "box"}], "psd_name", "note"}]}
# ══════════════════════════════════════════════════════════════════════
LAYOUT_NAME = "layout.json"
LAYOUT_SCHEMA = "bmk.design_patch.layout/1"
LAYOUT_HISTORY_DIR = "layout_history"
PSD_SNAPSHOT = os.path.join("derived", "psd_crops.json")  # Import PSD 가 레이아웃 모드에서 PSD 를 열 때 쓰는 shape 스냅숏(§2.3)
_LAYOUT_HISTORY_KEEP = 50
_LAYOUT_REV_MAX = "max_rev.json"  # layout_history 안 {"rev": 지금까지 쓴 가장 큰 rev} — 지금 판은 이력에 없어, 이력 파일로 되돌린 뒤에도 rev 를 다시 쓰지 않게
_LAYOUT_SIZE_STEP = 128   # 편집기 크기 스냅(L-128)
_LAYOUT_MAX_RATIO = 3
_LAYOUT_SMALL = 384       # 짧은 변 정보 기준(L-small, "작은 장식도 384 정사각")
_LAYOUT_COORD_MAX = 65536
_LAYOUT_TEXT_MAX = 120
_LAYOUT_NOTE_MAX = 500
_LAYOUT_ID_RE = re.compile(r"[A-Za-z0-9_\-]{1,64}")
_LAYOUT_TID_RE = re.compile(r"[A-Z]")
_LAYOUT_HISTORY_RE = re.compile(r"r[0-9]+\.json")
_NAME_HEAD_RE = re.compile(r"[0-9]{3}_")
_NAME_SIDES = {"왼쪽": "left", "left": "left", "오른쪽": "right", "right": "right"}
_NAME_PROBLEMS = {
    "len": "이름은 5–64자여야 합니다",
    "prefix": "이름은 세 자리 숫자와 '_' 로 시작해야 합니다(예: 009_모자)",
    "char": "이름에는 글자·숫자·_·- 만 쓸 수 있습니다(공백·| ; : , / \\ < > \" ' ? * 금지)",
}
_TEXT_BANNED = _PIPE_CHARS + ";；  "
_TEXT_PROBLEMS = {
    "char": "대상 문구에 줄바꿈·제어 문자나 | ｜ │ ¦ ; ； 를 쓸 수 없습니다",
    "len": f"대상 문구는 {_LAYOUT_TEXT_MAX}자 이하여야 합니다",
    "roundtrip": "대상 문구가 레이어명 문법과 부딪힙니다(', B: …' 처럼 쉼표 뒤 대문자 라벨 꼴은 다른 대상으로 읽힘)",
}


class LayoutConflict(ValueError):
    """layout.json rev 가 요청과 다름(다른 창에서 바뀜) — 보드가 409 로 돌려준다."""


def _name_bad_char(name: str) -> str | None:
    """'NNN_' 뒤에서 유니코드 L*·N* 범주나 _ - 가 아닌 첫 글자(없으면 None). 범주는 서버 unicodedata 판 기준이라
    브라우저(더 새 유니코드)가 받는 글자를 거절할 수 있다 — 거절 메시지에 이 글자를 밝힌다."""
    return next((c for c in name[4:] if c not in "_-" and unicodedata.category(c)[0] not in "LN"), None)


def _name_problem(name: str) -> str | None:
    """정규화한 이름의 규칙 위반 코드(이 순서로 첫 번째): "len"(5–64자, 코드 포인트) · "prefix"(^[0-9]{3}_) ·
    "char"(그 뒤 글자가 유니코드 L*·N* 범주나 _ - 가 아님 — 공백·구분자·제어 문자는 모두 여기서 걸림). 맞으면 None."""
    if not 5 <= len(name) <= 64:
        return "len"
    if not _NAME_HEAD_RE.match(name):
        return "prefix"
    if _name_bad_char(name) is not None:
        return "char"
    return None


def _name_side(name: str) -> str | None:
    """이름 끝 토큰('_' 뒤)이 왼쪽/left → "left", 오른쪽/right → "right"(보는 사람 기준, vlm.side_of 와 같은 뜻)."""
    return _NAME_SIDES.get(name.rsplit("_", 1)[-1].casefold())


def check_crop_name(name: Any, taken: Iterable[str] = ()) -> dict:
    """새 이름·바꾼 이름 검사(§3.6, 이관된 이름은 검사 N-name 으로만 알림). NFC → 앞뒤 공백 제거(str.strip) →
    길이 5–64 · '^[0-9]{3}_' · 나머지는 글자·숫자·_·- 만(위반 메시지 끝에 문제 글자 '(문제 글자: U+XXXX 「x」)').
    taken = 레이아웃의 다른 크롭 이름들(NFC·casefold 로 같으면 오류).
    반환 {"name": 정규화한 이름, "nnn", "part"(parse_crop_layer_name 과 같음 — 좌우 토큰 포함), "side": "left"|"right"|None}."""
    if not isinstance(name, str):
        raise ValueError(f"이름은 문자열이어야 합니다: {name!r}")
    n = _nfc(name).strip()
    problem = _name_problem(n)
    if problem == "char":
        c = _name_bad_char(n)
        raise ValueError(f"{_NAME_PROBLEMS[problem]}: {n!r} (문제 글자: U+{ord(c):04X} 「{c}」)")
    if problem:
        raise ValueError(f"{_NAME_PROBLEMS[problem]}: {n!r}")
    if any(_nfc(t).casefold() == n.casefold() for t in taken):
        raise ValueError(f"같은 이름의 크롭이 이미 있습니다: {n!r}")
    nnn, part = _split_nnn(n)
    return {"name": n, "nnn": nnn, "part": part, "side": _name_side(n)}


def _text_problem(text: str) -> str | None:
    """정규화한 대상 문구의 위반 코드: "char"(제어 문자 Cc·U+2028/2029·| ｜ │ ¦ ; ；) · "len"(> 120) ·
    "roundtrip"('X | A: <문구>' 를 다시 읽으면 다른 타깃이 됨). 맞으면 None."""
    if any(c in _TEXT_BANNED or unicodedata.category(c) == "Cc" for c in text):
        return "char"
    if len(text) > _LAYOUT_TEXT_MAX:
        return "len"
    if parse_crop_layer_name(f"000_x | A: {text}")["targets"] != [{"tid": "A", "text": text}]:
        return "roundtrip"
    return None


def check_target_text(text: Any) -> str:
    """대상 문구 검사(§3.6): NFC → 앞뒤 공백 제거 → 위 규칙. 정규화한 문구를 돌려주고 위반은 ValueError. 빈 문구는 허용."""
    if not isinstance(text, str):
        raise ValueError(f"대상 문구는 문자열이어야 합니다: {text!r}")
    t = _nfc(text).strip()
    problem = _text_problem(t)
    if problem:
        raise ValueError(f"{_TEXT_PROBLEMS[problem]}: {t!r}")
    return t


def _rect_ints(rect: Any, what: str) -> list[int]:
    """[x0, y0, x1, y1] = 정수 4개(bool 제외), |값| ≤ 65536, x1 > x0, y1 > y0 — 아니면 ValueError."""
    if (not isinstance(rect, (list, tuple)) or len(rect) != 4
            or any(isinstance(v, bool) or not isinstance(v, int) or abs(v) > _LAYOUT_COORD_MAX for v in rect)):
        raise ValueError(f"{what} 는 정수 4개 [x0, y0, x1, y1] 이어야 합니다(|값| ≤ {_LAYOUT_COORD_MAX}): {rect!r}")
    if rect[2] <= rect[0] or rect[3] <= rect[1]:
        raise ValueError(f"{what} 의 폭·높이가 0 이하입니다: {list(rect)}")
    return list(rect)


def layout_rect_problem(rect: Any, canvas: Any) -> str | None:
    """사각형 규칙(G5): 형식 오류는 ValueError, 캔버스 밖(0 미만·W/H 초과) = "outside", 긴 변 > 3 × 짧은 변 = "ratio"
    (이 순서로 첫 번째), 맞으면 None. 이관된 값은 이 코드를 검사·경고로만 쓴다."""
    x0, y0, x1, y1 = _rect_ints(rect, "rect")
    if x0 < 0 or y0 < 0 or x1 > canvas[0] or y1 > canvas[1]:
        return "outside"
    if max(x1 - x0, y1 - y0) > _LAYOUT_MAX_RATIO * min(x1 - x0, y1 - y0):
        return "ratio"
    return None


def check_layout_rect(rect: Any, canvas: Any) -> list[int]:
    """서버 add/update 의 rect 검사: layout_rect_problem 이 무엇이든 내면 ValueError(한국어). 맞으면 [x0, y0, x1, y1]."""
    problem = layout_rect_problem(rect, canvas)
    if problem == "outside":
        raise ValueError(f"사각형이 캔버스 {canvas[0]}x{canvas[1]} 밖으로 나갑니다: {list(rect)}")
    if problem == "ratio":
        raise ValueError(f"사각형의 긴 변이 짧은 변의 3배를 넘습니다: {rect[2] - rect[0]}x{rect[3] - rect[1]}")
    return list(rect)


def _legacy_targets(targets: list) -> bool:
    """빈 목록 또는 빈 문구 A 하나 = 레거시(레이어명에 '|' 없음과 같음)."""
    return not targets or (len(targets) == 1 and targets[0]["tid"] == "A" and targets[0]["text"] == "")


def layout_layer_name(crop: dict) -> str:
    """합성 레이어명: 레거시면 이름만, 아니면 "이름 | A: …; B: …"(빈 문구는 "B:"). Import 가 매니페스트 layer_name 으로 쓴다."""
    if _legacy_targets(crop["targets"]):
        return crop["name"]
    return crop["name"] + " | " + "; ".join(f"{t['tid']}: {t['text']}" if t["text"] else f"{t['tid']}:"
                                            for t in crop["targets"])


def layout_parsed(crop: dict) -> dict:
    """레이아웃 크롭 → parse_crop_layer_name 과 같은 모양(name·nnn·part·targets[tid·text]·warnings) = 합성 레이어명의 파싱 결과.
    validate_layout 이 왕복을 보장하므로 targets 는 레이아웃의 tid·text 그대로다(레거시 = [{"tid": "A", "text": ""}])."""
    return parse_crop_layer_name(layout_layer_name(crop))


def new_layout_id(name: str, taken: Iterable[str]) -> str:
    """새 크롭 ID(D3, 이관·add·PSD 새 shape 때만): crop_id_for(name), 이미 쓰는 ID 면 -2, -3 … 중 처음 비는 것.
    이름을 바꿔도 ID 는 바꾸지 않는다(specs·vlm·후보·★ 가 이어짐)."""
    base = crop_id_for(name)
    used = set(taken)
    cid, k = base, 1
    while cid in used:
        k += 1
        cid = f"{base}-{k}"
    return cid


def layout_sig(lay: dict) -> str:
    """Import 가 읽는 크롭 정의의 지문(sha256 앞 16자): 순서대로 id·name·rect·합성 레이어명(대상 문구)·psd_name(없으면 name).
    box·note·locked·psd 기준점·rev 는 넣지 않는다 — Import 캐시와 '반영됨' 표시용(매니페스트 source.layout.sig)."""
    rows = [[c["id"], c["name"], list(c["rect"]), layout_layer_name(c), c.get("psd_name") or c["name"]] for c in lay["crops"]]
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()[:16]


def validate_layout(lay: Any) -> list[str]:
    """layout.json 구조 검사. 구조 오류는 ValueError: schema·rev(0 이상 정수)·canvas([W, H] 양의 정수)·psd(crops: name·layer_name·rect)·
    crops[](id 형식·유일, name = NFC·앞뒤 공백 없음·'|' 계열 없음, rect, targets ≤26(tid A–Z 유일·text 문자열·box null 또는 rect),
    psd_name 문자열/null, note ≤500, locked bool — 없으면 false, Studio 잠금(배치 3-3)이라 Import·layout_sig 는 보지 않음)
    그리고 합성 레이어명을 다시 읽으면 같은 대상이 나와야 한다.
    반환 = 경고(이관된 값이 편집기 규칙 밖인 것): 캔버스 밖·비 3:1 초과·이름 규칙·이름 중복(NFC·casefold)·대상 문구 규칙, 그리고
    PSD 레이어명이 '|' 뒤가 비어 레거시(이름만)로 바뀐 크롭. 알 수 없는 키는 그대로 둔다(검사만 함)."""
    if not isinstance(lay, dict):
        raise ValueError(f"레이아웃 최상위가 객체가 아닙니다: {type(lay).__name__}")
    if lay.get("schema") != LAYOUT_SCHEMA:
        raise ValueError(f"레이아웃 schema 가 {LAYOUT_SCHEMA!r} 가 아닙니다: {lay.get('schema')!r}")
    rev = lay.get("rev")
    if isinstance(rev, bool) or not isinstance(rev, int) or rev < 0:
        raise ValueError(f"레이아웃 rev 가 0 이상의 정수가 아닙니다: {rev!r}")
    canvas = lay.get("canvas")
    if (not isinstance(canvas, list) or len(canvas) != 2
            or any(isinstance(v, bool) or not isinstance(v, int) or v <= 0 for v in canvas)):
        raise ValueError(f"레이아웃 canvas 는 양의 정수 [W, H] 여야 합니다: {canvas!r}")
    psd = lay.get("psd")
    if not isinstance(psd, dict) or not isinstance(psd.get("crops"), list):
        raise ValueError("레이아웃 psd 기준점(psd.crops 목록)이 없습니다.")
    for p in psd["crops"]:
        if not isinstance(p, dict) or not isinstance(p.get("name"), str) or not isinstance(p.get("layer_name"), str):
            raise ValueError(f"psd 기준점 크롭 형식이 잘못되었습니다: {p!r}")
        _rect_ints(p.get("rect"), f"psd 기준점 {p['name']} rect")
    crops = lay.get("crops")
    if not isinstance(crops, list):
        raise ValueError("레이아웃 crops 가 목록이 아닙니다.")

    warns: list[str] = []
    ids: set[str] = set()
    names: dict[str, str] = {}
    for c in crops:
        if not isinstance(c, dict):
            raise ValueError(f"레이아웃 크롭이 객체가 아닙니다: {c!r}")
        cid = c.get("id")
        if not isinstance(cid, str) or not _LAYOUT_ID_RE.fullmatch(cid):
            raise ValueError(f"크롭 id 형식이 잘못되었습니다([A-Za-z0-9_-] 1–64자): {cid!r}")
        if cid in ids:
            raise ValueError(f"크롭 id 가 겹칩니다: {cid}")
        ids.add(cid)
        name = c.get("name")
        if not isinstance(name, str) or not name.strip() or _base_name(name) != name:
            raise ValueError(f"크롭 {cid} 이름이 비었거나 NFC·앞뒤 공백·'|' 규칙에 어긋납니다: {name!r}")
        rect = _rect_ints(c.get("rect"), f"크롭 {cid} rect")
        targets = c.get("targets")
        if not isinstance(targets, list) or len(targets) > 26:
            raise ValueError(f"크롭 {cid} targets 는 26개 이하의 목록이어야 합니다.")
        tids = []
        for t in targets:
            if (not isinstance(t, dict) or not isinstance(t.get("tid"), str) or not _LAYOUT_TID_RE.fullmatch(t["tid"])
                    or not isinstance(t.get("text"), str)):
                raise ValueError(f"크롭 {cid} 대상 형식이 잘못되었습니다(tid A–Z, text 문자열): {t!r}")
            if t.get("box") is not None:
                _rect_ints(t["box"], f"크롭 {cid} 대상 {t['tid']} box")
            tids.append(t["tid"])
        if len(set(tids)) != len(tids):
            raise ValueError(f"크롭 {cid} 대상 tid 가 겹칩니다: {tids}")
        if c.get("psd_name") is not None and not isinstance(c["psd_name"], str):
            raise ValueError(f"크롭 {cid} psd_name 은 문자열이어야 합니다: {c['psd_name']!r}")
        note = c.get("note", "")
        if not isinstance(note, str) or len(note) > _LAYOUT_NOTE_MAX:
            raise ValueError(f"크롭 {cid} note 는 {_LAYOUT_NOTE_MAX}자 이하 문자열이어야 합니다.")
        if not isinstance(c.get("locked", False), bool):
            raise ValueError(f"크롭 {cid} locked 는 true·false 여야 합니다: {c['locked']!r}")
        want = [{"tid": t["tid"], "text": t["text"]} for t in targets] or [{"tid": "A", "text": ""}]
        if layout_parsed(c)["targets"] != want:
            raise ValueError(f"크롭 {cid} 대상 문구를 레이어명으로 합치면 다르게 읽힙니다: {layout_layer_name(c)!r}")

        problem = layout_rect_problem(rect, canvas)
        if problem:
            warns.append(f"{name}: 사각형이 {'캔버스 밖' if problem == 'outside' else '비 3:1 초과'} {rect}")
        problem = _name_problem(name)
        if problem:
            warns.append(f"{name}: {_NAME_PROBLEMS[problem]}")
        key = name.casefold()
        if key in names:
            warns.append(f"{name}: 이름이 {names[key]} 와 겹침(NFC·대소문자 무시)")
        names.setdefault(key, cid)
        for t in targets:
            problem = _text_problem(t["text"])
            if problem:
                warns.append(f"{name} {t['tid']}: {_TEXT_PROBLEMS[problem]}")
    flipped = {p["name"]: p["layer_name"] for p in psd["crops"]
               if _PIPE_RE.search(p["layer_name"]) and _legacy_targets(parse_crop_layer_name(p["layer_name"])["targets"])}
    for c in crops:
        layer = flipped.get(c.get("psd_name") or c["name"])
        if layer is not None and _legacy_targets(c["targets"]):
            warns.append(f"{c['name']}: PSD 레이어명 {layer!r} 은 '|' 뒤가 비어 레거시(이름만)로 다룸")
    return warns


def load_layout(root: str) -> dict | None:
    """<root>/layout.json 을 읽어 validate_layout 을 통과한 dict 로 돌려준다. 없으면 None, 깨졌거나 구조 오류면 ValueError
    (메시지에 layout_history 복구 안내). 매니페스트와 같은 잠금 안에서 읽는다(Windows os.replace 경합 — 규칙 메모 '동시 쓰기')."""
    path = os.path.join(root, LAYOUT_NAME)
    if not os.path.isfile(path):
        return None
    try:
        with manifest_lock(root):
            lay = _read_json(path, "레이아웃")
        validate_layout(lay)
    except ValueError as e:
        raise ValueError(f"{e} — {LAYOUT_HISTORY_DIR}/r<rev>.json 을 {LAYOUT_NAME} 로 복사해 이전 판으로 되돌릴 수 있습니다") from e
    return lay


def load_layout_version(root: str, rev: Any) -> dict | None:
    """이력 판 layout_history/r<rev>.json(실행 취소 되살리기 판정용 — rev 는 단조 증가라 r<n> 은 그 판 하나). validate_layout 을
    통과하면 dict, 없거나 깨졌거나 구조 오류·rev 가 0 이상 정수가 아니면 None(예외 없음). 이력 정리와 같은 잠금 안에서 읽는다."""
    if isinstance(rev, bool) or not isinstance(rev, int) or rev < 0:
        return None
    try:
        with manifest_lock(root):
            lay = _read_json(os.path.join(root, LAYOUT_HISTORY_DIR, f"r{rev}.json"), "레이아웃 이력")
        validate_layout(lay)
    except ValueError:
        return None
    return lay


def _layout_history_revs(folder: str) -> list[int]:
    """layout_history 의 r<n>.json 번호(오름차순, 폴더가 없으면 [])."""
    if not os.path.isdir(folder):
        return []
    return sorted(int(fn[1:-5]) for fn in os.listdir(folder) if _LAYOUT_HISTORY_RE.fullmatch(fn))


def _layout_rev_max(folder: str) -> int:
    """지금까지 쓴 가장 큰 rev = max_rev.json 과 이력 r<n>.json 중 큰 값(둘 다 없으면 -1). max_rev.json 이 없거나 깨졌으면 이력만."""
    revs = _layout_history_revs(folder)
    try:
        hw = _read_json(os.path.join(folder, _LAYOUT_REV_MAX), "레이아웃 rev 최댓값")["rev"]
    except (ValueError, KeyError, TypeError):
        hw = -1
    return max(revs[-1] if revs else -1, hw if isinstance(hw, int) and not isinstance(hw, bool) else -1)


def _keep_layout_history(root: str, prev: dict) -> int:
    """이전 판을 layout_history/r<rev>.json 으로 남기고(이미 있으면 덮지 않음 — 그 파일에서 복구한 판) rev 가 큰 50개만 둔다.
    오래된 이력을 지우지 못하면(열린 파일 등) 경고만 하고 저장은 계속한다. 반환 = 이력의 가장 큰 rev."""
    folder = os.path.join(root, LAYOUT_HISTORY_DIR)
    path = os.path.join(folder, f"r{prev['rev']}.json")
    if not os.path.isfile(path):
        write_json_atomic(path, prev)
    revs = _layout_history_revs(folder)
    for r in revs[:-_LAYOUT_HISTORY_KEEP]:
        try:
            os.remove(os.path.join(folder, f"r{r}.json"))
        except OSError as e:
            logger.warning(f"{_TAG} 레이아웃 이력 r{r}.json 을 지우지 못함(저장은 계속): {e}")
    return revs[-1]


def update_layout(root: str, mutate: Callable[[dict], Any], *, expect_rev: int | None, create: bool = False) -> tuple[Any, int]:
    """레이아웃 쓰기(낙관적 동시성): manifest_lock → 디스크 최신본 → expect_rev 가 지금 rev 와 다르면 LayoutConflict(None = 보지 않음)
    → mutate(lay)(제자리 수정) → validate_layout(구조 오류면 ValueError, 저장 안 함) → 바뀌었으면 이전 판을 이력으로 남기고
    새 rev = 지금까지 쓴 가장 큰 rev(layout_history/max_rev.json·이력 r<n>) + 1 로 원자 저장. 반환 (mutate 반환값, rev).
    mutate 가 예외를 내면 저장하지 않고, rev 를 바꿔도 무시한다. 파일이 없으면 LayoutConflict.
    create=True: 파일이 이미 있으면 LayoutConflict, mutate 가 빈 dict 를 채우고 rev = 지금까지 쓴 가장 큰 rev + 1(처음이면 0)로
    저장한다(expect_rev 는 보지 않음). 그래서 layout.json 을 치우거나 이력 파일 복사로 되돌려도 rev 는 프로젝트 폴더 안에서 다시
    쓰이지 않는다(단조 증가 — 낡은 창의 expect_rev 가 우연히 맞아 되돌린 판을 덮지 않게, 편집기 rev 비교도 기댐). mutate 안에서 update_manifest·commit_merge 를 부르지 말 것(반대도 같음)."""
    path = os.path.join(root, LAYOUT_NAME)
    with manifest_lock(root):
        if create:
            if os.path.isfile(path):
                raise LayoutConflict("레이아웃이 이미 있습니다 — 새로고침")
            folder = os.path.join(root, LAYOUT_HISTORY_DIR)
            rev = _layout_rev_max(folder) + 1
            lay: dict = {}
            result = mutate(lay)
            lay["rev"] = rev
            validate_layout(lay)
            write_json_atomic(os.path.join(folder, _LAYOUT_REV_MAX), {"rev": rev})
            write_json_atomic(path, lay)
            return result, rev
        lay = load_layout(root)
        if lay is None:
            raise LayoutConflict("레이아웃 파일이 없습니다 — 새로고침")
        rev = lay["rev"]
        if expect_rev is not None and expect_rev != rev:
            raise LayoutConflict(f"레이아웃이 다른 창에서 바뀌었습니다(rev {expect_rev} → {rev}) — 새로고침")
        before = _jsonable(lay)
        result = mutate(lay)
        lay["rev"] = rev
        validate_layout(lay)
        if _jsonable(lay) != before:
            folder = os.path.join(root, LAYOUT_HISTORY_DIR)
            rev = max(_keep_layout_history(root, before), _layout_rev_max(folder)) + 1
            lay["rev"] = rev
            write_json_atomic(os.path.join(folder, _LAYOUT_REV_MAX), {"rev": rev})  # 먼저 — 레이아웃 쓰기가 실패해도 번호만 건너뜀
            write_json_atomic(path, lay)
    return result, rev


def layout_from_manifest(m: dict, snapshot: dict | None = None) -> dict:
    """이관(D1 = Studio '레이아웃 편집 시작'): 매니페스트 크롭 → layout(rev 0). 크롭마다 id·name·rect 그대로, targets =
    parse_crop_layer_name(layer_name) 의 tid·text(box null), psd_name = 매니페스트 psd_name(없으면 name — 이름을 바꾼 크롭도 사전
    편집 자식을 계속 찾음), note "". 캔버스가 없으면(Import 전) ValueError.
    PSD 기준점: PSD 모드 매니페스트면 source.psd·fingerprint·crop_group + 크롭들의 name·layer_name·rect(at = 지금).
    레이아웃 모드 결과(source.layout 있음 — layout.json 을 치운 뒤 다시 시작)는 name·layer_name·rect 가 Studio 값이라
    snapshot(derived/psd_crops.json — 호출자가 source 와 psd·fingerprint·crop_group·crop_pixels_group 이 같은지 확인한 것)의
    psd·fingerprint·crop_group·at·크롭 name·layer_name·rect 로 만든다. 그때 snapshot 이 None 이면 ValueError.
    레이어명 '|' 뒤가 빈 크롭은 레거시(이름만)로 바뀐다 → validate_layout 경고로 보고된다."""
    if not m.get("canvas"):
        raise ValueError("매니페스트에 캔버스가 없습니다 — Import PSD 를 먼저 실행하세요.")
    src = m.get("source") or {}
    crops = []
    for c in m["crops"]:
        targets = parse_crop_layer_name(c["layer_name"])["targets"]
        crops.append({"id": c["id"], "name": c["name"], "rect": [int(v) for v in c["rect"]],
                      "targets": [{"tid": t["tid"], "text": t["text"], "box": None} for t in targets],
                      "psd_name": c.get("psd_name") or c["name"], "note": ""})
    origin, at, shapes = src, time.strftime("%Y-%m-%d %H:%M:%S"), m["crops"]
    if src.get("layout"):
        if snapshot is None:
            raise ValueError("매니페스트가 레이아웃 모드 결과(source.layout)라 PSD 기준점을 만들 수 없습니다 — layout.json 없이 "
                             "Import PSD 를 한 번 실행해 PSD 크롭으로 다시 가져온 뒤 시작하세요")
        origin, at, shapes = snapshot, snapshot.get("at"), snapshot["crops"]
    return {
        "schema": LAYOUT_SCHEMA, "rev": 0, "canvas": [int(v) for v in m["canvas"]],
        "psd": {"path": origin.get("psd"), "fingerprint": origin.get("fingerprint"), "crop_group": origin.get("crop_group"), "at": at,
                "crops": [{"name": c["name"], "layer_name": c["layer_name"], "rect": [int(v) for v in c["rect"]]} for c in shapes]},
        "crops": crops,
    }


def _overlap(a: list[int], b: list[int]) -> tuple[int, int]:
    """두 사각형(끝 배타)이 겹친 폭·높이(겹치지 않으면 0 이하)."""
    return min(a[2], b[2]) - max(a[0], b[0]), min(a[3], b[3]) - max(a[1], b[1])


def layout_geom_checks(lay: dict) -> dict[str, list[dict]]:
    """검사 목록(§3.7 의 L-·N-·P- 코드) — 구조가 맞는 레이아웃(canvas·crops 만 읽음)에 대해 {crop_id: [check, …]}(모든 크롭, 없으면 []).
    check = {"code", "level": "error"|"warn"|"info", "msg"} + 짝 검사(L-same·L-overlap·N-dup)는 "other"(상대 crop_id),
    부품 상자 검사(P-*)는 "tid". 규칙(정수 식 — JS layout_geom.js 가 같은 (crop_id, code) 집합을 내야 함):
      L-ratio error 긴 변 > 3 × 짧은 변 · L-outside warn 캔버스 밖 · L-128 info 폭이나 높이가 128 배수 아님 · L-small info 짧은 변 < 384
      L-gptk info gpt_output_size(user_k) 의 W < 2w 또는 H < 2h · L-same warn rect 가 같음(상대마다) · L-overlap info 겹친 넓이 > 0(같은 rect 제외)
      N-name error 이름 규칙 위반 · N-dup error NFC·casefold 이름이 같음(상대마다) · N-nnn info 앞자리 숫자(parse 의 nnn)가 같은 크롭이 있음
      N-side info 이름 끝 _왼쪽/_left 인데 3(x0+x1) > 4W, _오른쪽/_right 인데 3(x0+x1) < 2W(가운데가 반대쪽 1/3)
      P-out / P-edge / P-70 warn (상자가 있는 대상마다 하나, 이 순서로 첫 번째): 상자가 크롭 밖으로 나감 / 크롭 변까지 여백 m 이
        m < 8 또는 50m < 변 방향 크기(왼·오른쪽 여백은 폭, 위·아래는 높이) / 20m < 3 × 변 방향 크기(70% 가이드 밖).
        크롭 변이 캔버스 끝인 쪽(x0 ≤ 0·x1 ≥ W·y0 ≤ 0·y1 ≥ H)의 여백은 셋 다 보지 않고, 셋이 없을 때 그쪽 여백이 20m < 3 × 크기면
        P-canvas info(부품이 그림 끝에 닿음 — 크롭으로 피할 수 없음)."""
    W, H = lay["canvas"]
    crops = lay["crops"]
    keys = [c["name"].casefold() for c in crops]
    nnns = [_split_nnn(c["name"])[0] for c in crops]
    out: dict[str, list[dict]] = {}
    for i, c in enumerate(crops):
        rows: list[dict] = []
        x0, y0, x1, y1 = rect = c["rect"]
        w, h = x1 - x0, y1 - y0
        long_, short = max(w, h), min(w, h)
        if long_ > _LAYOUT_MAX_RATIO * short:
            rows.append({"code": "L-ratio", "level": "error", "msg": f"비 {long_ / short:.2f}:1 — 3:1 이하로 줄이세요"})
        if x0 < 0 or y0 < 0 or x1 > W or y1 > H:
            rows.append({"code": "L-outside", "level": "warn", "msg": f"캔버스 {W}×{H} 밖으로 나감"})
        if w % _LAYOUT_SIZE_STEP or h % _LAYOUT_SIZE_STEP:
            rows.append({"code": "L-128", "level": "info", "msg": f"크기 {w}×{h} 가 128 배수가 아님"})
        if short < _LAYOUT_SMALL:
            rows.append({"code": "L-small", "level": "info", "msg": f"짧은 변 {short} < {_LAYOUT_SMALL}"})
        gw, gh, _info = gpt_output_size(w, h)
        if gw < 2 * w or gh < 2 * h:
            rows.append({"code": "L-gptk", "level": "info", "msg": f"GPT 배율 {min(gw / w, gh / h):.2f} < 2 ({gw}×{gh})"})
        for j, o in enumerate(crops):
            if j == i:
                continue
            if list(o["rect"]) == list(rect):
                rows.append({"code": "L-same", "level": "warn", "msg": f"{o['name']} 와 사각형이 같음", "other": o["id"]})
                continue
            ow, oh = _overlap(rect, o["rect"])
            if ow > 0 and oh > 0:
                rows.append({"code": "L-overlap", "level": "info", "msg": f"겹침: {o['name']} ({ow}×{oh})", "other": o["id"]})
        problem = _name_problem(c["name"])
        if problem:
            rows.append({"code": "N-name", "level": "error", "msg": _NAME_PROBLEMS[problem]})
        for j, o in enumerate(crops):
            if j != i and keys[j] == keys[i]:
                rows.append({"code": "N-dup", "level": "error", "msg": f"이름 중복: {o['id']}", "other": o["id"]})
        same_nnn = sum(1 for j, n in enumerate(nnns) if j != i and n and n == nnns[i])
        if same_nnn:
            rows.append({"code": "N-nnn", "level": "info", "msg": f"같은 번호 {same_nnn + 1}개 — 레퍼런스는 부위 이름으로 고름"})
        side = _name_side(c["name"])
        if side == "left" and 3 * (x0 + x1) > 4 * W:
            rows.append({"code": "N-side", "level": "info", "msg": "이름은 왼쪽인데 크롭이 화면 오른쪽 1/3 에 있음(보는 사람 기준)"})
        elif side == "right" and 3 * (x0 + x1) < 2 * W:
            rows.append({"code": "N-side", "level": "info", "msg": "이름은 오른쪽인데 크롭이 화면 왼쪽 1/3 에 있음(보는 사람 기준)"})
        for t in c["targets"]:
            box = t.get("box")
            if box is None:
                continue
            bx0, by0, bx1, by1 = box
            if bx0 < x0 or by0 < y0 or bx1 > x1 or by1 > y1:
                rows.append({"code": "P-out", "level": "warn", "msg": f"{t['tid']} 상자가 크롭 밖으로 나감", "tid": t["tid"]})
                continue
            # 크롭 변이 캔버스 끝인 쪽은 크롭으로 여백을 만들 수 없다(부품이 그림 끝에서 잘림) — 경고에서 빼고, 거기만 좁으면 정보 P-canvas
            sides = ((bx0 - x0, w, x0 <= 0), (x1 - bx1, w, x1 >= W), (by0 - y0, h, y0 <= 0), (y1 - by1, h, y1 >= H))
            margins = [(m, size) for m, size, edge in sides if not edge]
            if any(m < 8 or 50 * m < size for m, size in margins):
                rows.append({"code": "P-edge", "level": "warn", "msg": f"{t['tid']} 부품이 경계에 닿음", "tid": t["tid"]})
            elif any(20 * m < 3 * size for m, size in margins):
                rows.append({"code": "P-70", "level": "warn", "msg": f"{t['tid']} 상자가 70% 가이드 밖 — 크롭을 키우세요(여백 15%)",
                             "tid": t["tid"]})
            elif any(edge and 20 * m < 3 * size for m, size, edge in sides):
                rows.append({"code": "P-canvas", "level": "info", "msg": f"{t['tid']} 부품이 그림 끝에 닿음 — 크롭으로 피할 수 없음",
                             "tid": t["tid"]})
        out[c["id"]] = rows
    return out


def psd_diff(baseline: dict, snapshot: dict) -> dict:
    """PSD 기준점(layout.psd) 대 지금 PSD 스냅숏(derived/psd_crops.json)의 shape 차이(§2.3). 둘 다 {"crops": [{name, layer_name, rect}]}.
    짝짓기 = 기본 이름(name)이 같은 것끼리, 같은 이름이 여럿이면 IoU 큰 쌍부터(같으면 기준점·스냅숏 순서) 탐욕.
    반환 {"changed": [{"name", "fields": ["rect"|"layer_name", …], "from": {"layer_name", "rect"}, "to": {…}}],
          "added": [{"name", "layer_name", "rect"}](스냅숏에만), "removed": [{…}](기준점에만)}.
    순서: changed·removed = 기준점 순서, added = 스냅숏 순서. 빈 차이 = 세 목록 모두 []."""
    old, new = baseline["crops"], snapshot["crops"]

    def iou(a, b):
        ow, oh = _overlap(a, b)
        inter = max(ow, 0) * max(oh, 0)
        return inter / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter)

    pairs: dict[int, int] = {}
    for name in dict.fromkeys(c["name"] for c in old):
        cands = sorted((-iou(old[i]["rect"], new[j]["rect"]), i, j)
                       for i, c in enumerate(old) if c["name"] == name
                       for j, s in enumerate(new) if s["name"] == name)
        for _neg, i, j in cands:
            if i not in pairs and j not in pairs.values():
                pairs[i] = j

    def shape(c):
        return {"name": c["name"], "layer_name": c["layer_name"], "rect": list(c["rect"])}

    changed, removed = [], []
    for i, c in enumerate(old):
        if i not in pairs:
            removed.append(shape(c))
            continue
        s = new[pairs[i]]
        fields = [f for f, a, b in (("rect", list(c["rect"]), list(s["rect"])), ("layer_name", c["layer_name"], s["layer_name"]))
                  if a != b]
        if fields:
            changed.append({"name": c["name"], "fields": fields, "from": {"layer_name": c["layer_name"], "rect": list(c["rect"])},
                            "to": {"layer_name": s["layer_name"], "rect": list(s["rect"])}})
    paired = set(pairs.values())
    return {"changed": changed, "added": [shape(s) for j, s in enumerate(new) if j not in paired], "removed": removed}


# ══════════════════════════════════════════════════════════════════════
# S3 Studio 파이프라인 — Prepare 입력 지문 (STUDIO_S3_SPEC §2.2 prepare_inputs)
# ══════════════════════════════════════════════════════════════════════
_REFS_FILE = "refs.json"  # bmk_design_patch_refs.REFS_NAME(refs 가 store 를 import 하므로 이름만 복사 — V-S1 이 대조)


def _ref_path(r, refs_dir: str) -> str:
    r = str(r)
    return r if os.path.isabs(r) or not refs_dir else os.path.join(refs_dir, r)


def _spec_paths(root: str, refs_dir: str) -> list[str]:
    """specs/*.json 이 가리키는 파일 경로(guide, before_paste = 프로젝트 폴더 기준, targets[].refs = refs_dir 기준 — Prepare 와 같은
    해석), 스펙 파일 이름 순. 스펙 JSON 을 모두 파싱하므로 보드 계획 서명(state 폴링)은 specs/ 목록이 같으면 기억한 결과를 쓴다."""
    paths = []
    sd = os.path.join(root, "specs")
    for fn in sorted(os.listdir(sd)) if os.path.isdir(sd) else []:
        if not fn.endswith(".json"):
            continue
        try:
            ov = load_spec_override(root, fn[:-5])
        except ValueError:  # JSON 오류: 스펙 파일 자체의 지문은 _dir_fp(specs) 에 있다
            continue
        paths += [p if os.path.isabs(p) else os.path.join(root, p)
                  for p in (ov.get("guide"), ov.get("before_paste")) if isinstance(p, str) and p]
        ts = ov.get("targets")  # 사람이 고친 스펙: 목록이 아닌 targets·refs 는 가리키는 파일 없음(Studio 계획 라우트가 500 이 되지 않게)
        paths += [_ref_path(r, refs_dir) for t in (ts if isinstance(ts, list) else []) if isinstance(t, dict)
                  for r in (t["refs"] if isinstance(t.get("refs"), list) else [])]
    return paths


def _spec_files_fp(root: str, m: dict | None = None) -> list:
    """매니페스트 타깃 refs 와 specs/*.json 이 가리키는 파일(_spec_paths)의 지문. 가이드 그림이나 하위 폴더 레퍼런스만 바뀌어도
    Project 지문이 바뀌어 Prepare 가 다시 실행된다. m 이 없으면 디스크에서 읽는다."""
    if m is None:
        try:
            m = load_manifest(root)
        except ValueError:  # 깨진 매니페스트: 지문은 _file_fp 가 이미 반영
            return []
    refs_dir = (m.get("source") or {}).get("refs_dir") or ""
    paths = [_ref_path(r, refs_dir) for c in m["crops"] for t in c.get("targets") or [] for r in t.get("refs") or []]
    return [(p, _file_fp(p)) for p in paths + _spec_paths(root, refs_dir)]


def prepare_inputs_fp(root: str, m: dict) -> str:
    """Prepare 입력 지문(sha256 앞 16자) — Prepare 가 jobs 와 같은 커밋에 매니페스트 prepare_inputs 로 남기고, 지금 값이 그것과
    다르면 '반영 필요'(레이아웃·레퍼런스·_layer_targets 미반영, 마지막 Prepare 뒤 초안 쓰기·채택·스펙 수정). 재료 = 스펙이
    가리키는 파일(_spec_files_fp — 노드 Project 지문과 같은 해석)·specs/·refs.json·layout.json·glossary.json·source.glossary·
    refs_dir 바로 아래 파일 목록의 [mtime_ns, size](폴더는 _dir_fp)와 두 경로, source.layout.rev. 매니페스트·원장(cands/·vlm/)·
    mock 스위치는 넣지 않는다 — Prepare 자신의 쓰기(inputs/·jobs)로는 바뀌지 않는다."""
    src = m.get("source") or {}
    refs_dir = src.get("refs_dir") or ""
    gloss = src.get("glossary") or ""
    lay = src.get("layout") if isinstance(src.get("layout"), dict) else {}
    rows = [_spec_files_fp(root, m), _dir_fp(os.path.join(root, "specs")), _file_fp(os.path.join(root, _REFS_FILE)),
            _file_fp(os.path.join(root, LAYOUT_NAME)), _file_fp(os.path.join(root, "glossary.json")),
            [gloss, _file_fp(gloss)], [refs_dir, _dir_fp(refs_dir)], lay.get("rev")]
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()[:16]
