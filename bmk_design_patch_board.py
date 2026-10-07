"""BMK Design Patch Board — Review Board(별도 웹 페이지)의 aiohttp 라우트와 보드 썸네일 생성(노드 없는 보조 모듈).

배경
----
Run 이 만든 후보가 타깃마다 여러 장이 되면 노드 미리보기로는 고르기 어렵다. Review Board 는 ComfyUI 탭이 연
별도 창(두 번째 모니터 등)에서 타깃별 모든 후보를 같은 초점 영역으로 확대해 보여 주고, 키보드로 ★·slice·탈락을
고르고 재굴림(+1 호출)을 요청한다. 보드는 매니페스트만 고친다(모든 쓰기는 store.update_manifest).
과금 큐잉은 opener 인 ComfyUI 탭이 postMessage 를 받아 Review 노드를 부분 실행해서 한다 — 인증 토큰은
그 경로(app.queuePrompt)로만 붙으므로 라우트는 유료 호출을 할 수 없다.
명세: H:\\BmkNodeDesign\\MultiLayerCropEdit\\_proto\\M2_SPEC.md §4.

라우트 (register_routes — 모든 root 파라미터 = store 레지스트리 키(소문자 hex 12자), 경로 금지)
-------------------------------------------------------------------------------------------
- GET  /bmk/design_patch/board/            board.html (web_design_patch/ 의 board.html·board.js·board.css 만)
- GET  /bmk/design_patch/state?root=&since_rev=
       보드 요약(JSON). since_rev 가 지금 rev 와 같으면 {"unchanged": true, rev, running, drain}.
- GET  /bmk/design_patch/thumb?root=&kind=&key=&size=[&v=]
       JPEG. kind = cand(후보 키) | before(crop_id/tid) | ref(crop_id/tid/번호) | delta(후보 키) | context(후보 키).
       size ∈ 128·192·256·384·512·768·1024. v 가 서버의 캐시 키와 같으면 오래 캐시해도 된다고 응답한다.
- GET  /bmk/design_patch/file?root=&rel=   프로젝트 안 cands/·inputs/·derived/·masks/ 의 이미지 파일만.
- POST /bmk/design_patch/pick    {root, crop_id, tid, key, mode: set|add|remove}
- POST /bmk/design_patch/reject  {root, key, value: bool}
- POST /bmk/design_patch/reroll  {root, cell_key | (crop_id, tid[, variant]), count}
- POST /bmk/design_patch/drain   {root}   진행 중인 Run 이 있을 때만 플래그를 건다(없으면 drain false 로 응답)
POST 는 Content-Type application/json 만 받는다(다른 사이트의 단순 폼 POST 차단). 오류는 4xx + {"error": 한국어}.
CPU·파일 작업은 asyncio.to_thread 로 돌려 aiohttp 루프를 막지 않는다.

썸네일 (derived/thumbs/ 캐시, 파일명 = 내용 서명: 후보 파일·quad·크롭 rect·분석 params_hash·초점 박스·kind·size)
--------------------------------------------------------------------------------------------------------
- 배치: Analyze 와 같은 방법 — 후보 원본 → 크롭 rect 크기로 LANCZOS(an.place) → 정합 행렬 적용(an.warp_affine)
  → 톤 보정장(있으면 an.apply_delta). 크롭과 IoU < 0.5 인 부분 패치는 quad 그대로 렌더해 크롭 소스 위에 얹는다.
  크롭 해상도(= 최종 PSD 에서 보이는 해상도) 결과를 placed_<서명>.png 로 캐시해 다른 kind 가 재사용한다.
- 초점 박스(크롭 로컬): 그 타깃 후보들의 자동 마스크(>= 0.5) bbox 합집합을 변마다 10%(합 +20%) 넓힌 것,
  최소 크롭 짧은 변의 1/4. 분석이 없으면 크롭 전체. before 와 cand 는 같은 초점·같은 크기라 Q 깜빡임이 픽셀 정렬된다.
- delta = 크롭 소스 대비 ΔE76(an.delta_e, blur 1.5) 히트맵(0 → 3·dE 를 inferno 로) + 자동 마스크 외곽(청록).
- context = 베이스 위에 현재 마스크(손 마스크가 있으면 손, 없으면 자동 마스크 — Compose 기본 hand_else_auto)로
  합성한 크롭 rect 1.5배 영역 + 크롭 외곽선(주황).
- 분석 항목 선택: prebuild_thumbs(analyze_params) 로 받은 설정과 params 가 같은 항목, 없으면 가장 최근 항목.

공개 함수 (Review 노드·Run 노드용)
---------------------------------
- register_routes(routes, base_dir=None) -> bool   라우트 등록(이미 있으면 False). base_dir 를 주면 그 아래
                                                    _roots.json 을 store.load_root_registry 로 다시 읽는다(재시작 복구).
- build_state(root, since_rev=None) -> dict        state 라우트와 같은 요약(Review 보고서용).
- prebuild_thumbs(root, analyze_params=None, size=256) -> dict
                                                    분석 설정 기억 + 후보 그리드·Before 썸네일 미리 생성(캐시).
- board_url(key) -> str                            "/bmk/design_patch/board/?root=<key>"
- set_drain / drain_requested / clear_drain(root)  drain 플래그(프로세스 메모리, 키 = store.root_key). runner.run_calls 가
                                                    돌고 있으면 runner 레지스트리(request_drain)에 걸고, drain_requested 는
                                                    두 레지스트리를 합쳐 답한다(어느 쪽을 drain_flag 로 넘겨도 동작).
- run_active(root) / is_running(root)              Run 진행 표시(컨텍스트 관리자, runner.run_calls 밖에서 쓸 때). 처음 들어갈 때·
                                                    마지막으로 나갈 때 이 모듈 drain 플래그를 지운다. is_running 은 runner 것도 본다.

버전 이력
---------
v1 (2026-10, M2)
- 최초 구현: 라우트 8종(정적·state·thumb·file·pick·reject·reroll·drain), 썸네일 5종 + placed 중간 캐시,
  drain·run 레지스트리, pending 계산(runner.plan_calls 가 있으면 그것, 없으면 원장 파일 + calls 요약).
"""

from __future__ import annotations

import asyncio
import concurrent.futures
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
from typing import Callable
from urllib.parse import urlencode

import cv2
import numpy as np
from aiohttp import web
from PIL import Image

try:
    from . import bmk_design_patch_analysis as an
    from . import bmk_design_patch_psd as dpsd
    from . import bmk_design_patch_store as store
except ImportError:  # 단독 실행/테스트
    import bmk_design_patch_analysis as an
    import bmk_design_patch_psd as dpsd
    import bmk_design_patch_store as store

logger = logging.getLogger(__name__)

_TAG = "[ComfyUI_BMK_Nodes::DesignPatch]"

API = "/bmk/design_patch"
WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web_design_patch")
_STATIC = {"board.html": "text/html", "board.js": "text/javascript", "board.css": "text/css"}

THUMB_KINDS = ("cand", "before", "ref", "delta", "context")
THUMB_SIZES = (128, 192, 256, 384, 512, 768, 1024)
GRID_SIZE = 256
BIG_SIZE = 512
_THUMB_VERSION = "dp-thumb/1"
_THUMB_DIR = "derived/thumbs"
_FOCUS_PAD = 0.10          # 자동 마스크 bbox 합집합을 변마다 이만큼(합 +20%)
_CONTEXT_SCALE = 1.5
_MIN_CROP_IOU = 0.5        # bmk_design_patch._MIN_CROP_IOU 와 같음(부분 패치 판정)
_HEAT_DE_SPAN = 3.0        # ΔE 히트맵 상한 = 3·dE
_GRAY = 96                 # 크롭 소스의 투명(캔버스 밖) 부분 바탕

_FILE_DIRS = ("cands", "inputs", "derived", "masks")
_IMG_EXTS = (".png", ".jpg", ".jpeg", ".webp")
_MAX_BODY = 16 * 1024
_MAX_REROLL_COUNT = 4      # 요청 하나에 더할 수 있는 호출 수
_MAX_CELL_PENDING = 16     # 재굴림 후 셀의 대기 호출 상한(키 반복·버그로 인한 과금 폭주 방지, Run 이 소화하면 다시 가능)

_ROOT_HINT = "등록되지 않은 프로젝트 키입니다 — Review 노드를 실행한 뒤 노드의 Open Board 로 여세요"
_KEY_RE = re.compile(r"[A-Za-z0-9_\-]{1,96}")
_CROP_ID_RE = re.compile(r"[A-Za-z0-9_\-]{1,64}")
_TID_RE = re.compile(r"[A-Z]{1,3}")
_CELL_RE = re.compile(r"[0-9a-f]{24}")
_VARIANT_RE = re.compile(r"[A-Za-z0-9_\-]{1,48}")
_LEDGER_RE = re.compile(r"r(\d+)\.(inflight|done|failed)\.json")

_RENDER_SLOTS = threading.BoundedSemaphore(3)                 # 동시에 도는 썸네일 계산 수(라우트 + 미리 생성)
_PATH_LOCKS = [threading.Lock() for _ in range(32)]           # 같은 캐시 파일을 두 번 만들지 않게(중첩 금지)
_FOCUS_MEMO: dict[tuple, list[int]] = {}
_FOCUS_LOCK = threading.Lock()
_FLAGS_LOCK = threading.Lock()
_DRAIN: dict[str, float] = {}      # root_key → 요청 시각
_RUNNING: dict[str, int] = {}      # root_key → 진행 중인 Run 수
_PREF: dict[str, dict] = {}        # root_key → 보드가 쓸 분석 설정(prebuild_thumbs)
_PLAN_WARNED: set[str] = set()


class _Reject(Exception):
    """4xx 응답으로 바꿀 오류(한국어 메시지)."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


# ══════════════════════════════════════════════════════════════════════
# drain / run 레지스트리 (Run 노드·runner 가 조회)
# runner.run_calls 는 자기 레지스트리(is_running / request_drain / drain_requested)를 가진다. 그 Run 이 돌고 있으면
# 거기에 걸고(run_calls 의 기본 drain_flag 가 본다), 아니면 이 모듈 플래그(run_active 로 감싼 Run 용)에 건다.
# drain_requested / is_running 은 둘을 합쳐 답한다 → 노드가 어느 쪽을 drain_flag 로 넘겨도 보드 drain 이 먹는다.
# ══════════════════════════════════════════════════════════════════════
def set_drain(root: str) -> bool:
    """진행 중인 Run 에 '새 발사 중지'를 요청한다(진행 중인 호출은 끝까지 받는다). 반환: 진행 중인 Run 이 있는지.
    실행 중이 아닐 때 건 이 모듈 플래그는 다음 run_active 진입 때 지워진다(보드 라우트는 실행 중일 때만 부른다)."""
    runner = _runner()
    if runner is not None and runner.is_running(root):
        runner.request_drain(root)
        return True
    k = store.root_key(root)
    with _FLAGS_LOCK:
        _DRAIN[k] = time.time()
        return _RUNNING.get(k, 0) > 0


def drain_requested(root: str) -> bool:
    with _FLAGS_LOCK:
        if store.root_key(root) in _DRAIN:
            return True
    runner = _runner()
    return bool(runner is not None and runner.drain_requested(root))


def clear_drain(root: str) -> None:
    """이 모듈의 drain 플래그를 지운다(runner 플래그는 run_calls 가 시작할 때 스스로 지운다)."""
    with _FLAGS_LOCK:
        _DRAIN.pop(store.root_key(root), None)


def is_running(root: str) -> bool:
    with _FLAGS_LOCK:
        if _RUNNING.get(store.root_key(root), 0) > 0:
            return True
    runner = _runner()
    return bool(runner is not None and runner.is_running(root))


@contextlib.contextmanager
def run_active(root: str):
    """Run 이 호출을 발사하는 동안 감싼다: 보드의 running 표시 + drain 플래그 수명 관리.
    처음 들어가는 Run 은 지난 drain 플래그를 지우고, 마지막으로 나가는 Run 도 지운다."""
    k = store.root_key(root)
    with _FLAGS_LOCK:
        if _RUNNING.get(k, 0) == 0:
            _DRAIN.pop(k, None)
        _RUNNING[k] = _RUNNING.get(k, 0) + 1
    try:
        yield
    finally:
        with _FLAGS_LOCK:
            n = _RUNNING.get(k, 1) - 1
            if n > 0:
                _RUNNING[k] = n
            else:
                _RUNNING.pop(k, None)
                _DRAIN.pop(k, None)


def board_url(key: str) -> str:
    return f"{API}/board/?{urlencode({'root': key})}"


# ══════════════════════════════════════════════════════════════════════
# 매니페스트 조회 도우미
# ══════════════════════════════════════════════════════════════════════
def _rect_iou(a, b) -> float:
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _analyzable(crop: dict, cand: dict) -> bool:
    """크롭 전체를 덮는 후보(GPT 결과)만 크롭 rect 로 배치한다. 부분 패치 SO 는 quad 그대로(노드와 같은 규칙)."""
    return _rect_iou(dpsd.quad_bbox(cand["quad"]), crop["rect"]) >= _MIN_CROP_IOU


def _entry_for(m: dict, key: str, pref: dict | None) -> dict | None:
    """후보의 분석 항목: pref(분석 설정)와 params 가 같은 마지막 항목, 없으면 가장 최근 항목."""
    per = (m.get("analysis") or {}).get(key)
    if not isinstance(per, dict) or not per:
        return None
    if "params_hash" in per:  # dp-an/1 평평한 항목
        return per
    entries = [e for e in per.values() if isinstance(e, dict)]
    if pref is not None:
        same = [e for e in entries if e.get("params") == pref]
        if same:
            return same[-1]
    return entries[-1] if entries else None


def _usable(entry: dict | None) -> bool:
    return entry is not None and not entry.get("skipped") and isinstance(entry.get("reg"), dict)


def _target_members(m: dict, crop_id: str, tid: str) -> list[dict]:
    return [c for c in m["candidates"] if c.get("crop_id") == crop_id and c.get("tid") == tid]


def _crop(m: dict, crop_id: str) -> dict | None:
    return next((c for c in m["crops"] if c.get("id") == crop_id), None)


def _cand(m: dict, key: str) -> dict | None:
    return next((c for c in m["candidates"] if c.get("key") == key), None)


def _job_tids(job: dict) -> list[str]:
    return [t for t in str(job.get("tid") or "").split(",") if t]


def _target_jobs(m: dict, crop_id: str, tid: str) -> list[dict]:
    """그 타깃이 들어간 jobs(타깃 단독 작업 먼저)."""
    jobs = [j for j in m["jobs"] if j.get("crop_id") == crop_id and tid in _job_tids(j)]
    return sorted(jobs, key=lambda j: len(_job_tids(j)) > 1)


def _target_refs(m: dict, crop_id: str, tid: str) -> list[str]:
    """Prepare 가 평탄화해 inputs/ 에 둔 레퍼런스(그 타깃이 들어간 jobs 의 role=ref, 순서대로·중복 없이)."""
    out: list[str] = []
    for j in _target_jobs(m, crop_id, tid):
        for inp in j.get("inputs") or []:
            f = inp.get("file") if isinstance(inp, dict) else None
            if inp.get("role") == "ref" and isinstance(f, str) and f not in out:
                out.append(f)
    return out


def _cell_of(cand: dict) -> tuple[str | None, str | None, int | None]:
    """run 후보의 (cell_key, call_id, rep). call_id = f"{cell_key}_r{rep}"."""
    info = cand.get("origin_info") or {}
    call_id = info.get("call_id")
    if not isinstance(call_id, str) or "_r" not in call_id:
        return info.get("cell_key"), None, None
    ck, rep = call_id.rsplit("_r", 1)
    return ck, call_id, int(rep) if rep.isdigit() else None


def _mock_active(root: str) -> bool:
    """Run 의 MockBackend 활성 조건(M2_SPEC §2.3): BMK_DP_MOCK_API=1 또는 <base>/bmk_design_patch/_MOCK_API.
    runner 가 있으면 Run 이 실제로 쓰는 판정(runner.mock_enabled — 1/true/yes/on)을 그대로 쓴다."""
    runner = _runner()
    if runner is not None:
        return runner.mock_enabled(root)
    return os.environ.get("BMK_DP_MOCK_API") == "1" or os.path.isfile(os.path.join(os.path.dirname(root), "_MOCK_API"))


# ══════════════════════════════════════════════════════════════════════
# 대기 호출 수
# ══════════════════════════════════════════════════════════════════════
@functools.lru_cache(maxsize=1)
def _runner():
    """bmk_design_patch_runner(plan_calls·실행 레지스트리) — 노드 모듈이 두 모듈을 함께 import 하고 runner 가 이 모듈의
    drain 함수를 쓸 수도 있으므로 순환 import 를 피해 처음 쓸 때 읽는다. 없으면 None(원장 파일로 계산)."""
    try:
        from . import bmk_design_patch_runner as runner
    except ImportError:
        try:
            import bmk_design_patch_runner as runner
        except ImportError:
            return None
    return runner if hasattr(runner, "plan_calls") else None


def _ledger_pending(root: str, m: dict) -> dict[str, int]:
    """runner 가 없을 때의 계산: 셀 목표(reps + rerolls) − 이미 발사된 rep 수.
    발사됨 = 원장 r<rep>.inflight/done/failed.json 이 있거나 calls 요약의 status 가 inflight/done/failed/orphaned."""
    fired: dict[str, set] = {}
    for c in m.get("calls") or []:
        if not isinstance(c, dict) or c.get("status") not in ("inflight", "done", "failed", "orphaned"):
            continue
        rep = c.get("rep")
        if rep is None and isinstance(c.get("call_id"), str) and "_r" in c["call_id"]:
            rep = c["call_id"].rsplit("_r", 1)[1]
        if str(rep).isdigit():
            fired.setdefault(str(c.get("cell_key")), set()).add(int(rep))
    out: dict[str, int] = {}
    rerolls = m.get("rerolls") or {}
    for j in m["jobs"]:
        ck = j.get("cell_key")
        if not isinstance(ck, str) or not _CELL_RE.fullmatch(ck):
            continue
        reps = set(fired.get(ck, ()))
        folder = os.path.join(root, "cands", ck)
        if os.path.isdir(folder):
            for fn in os.listdir(folder):
                mt = _LEDGER_RE.fullmatch(fn)
                if mt:
                    reps.add(int(mt.group(1)))
        target = int(j.get("reps") or 1) + int(rerolls.get(ck) or 0)
        out[ck] = max(0, target - len(reps))
    return out


def _pending(root: str, m: dict) -> tuple[dict[str, int], int]:
    """({cell_key: 대기 호출 수}, 그중 보드 사전 승인 수). runner.plan_calls 가 있으면 그것(Run 과 같은 판단:
    다운로드 복구 포함), 없으면 _ledger_pending(사전 승인 = 셀마다 min(rerolls, 대기))."""
    runner = _runner()
    if runner is not None:
        try:
            plan = runner.plan_calls(m, root)
        except Exception as e:  # 보드 폴링이 runner 오류로 죽지 않게 — 원장 계산으로 대신하고 한 번만 알림
            rk = store.root_key(root)
            if rk not in _PLAN_WARNED:
                _PLAN_WARNED.add(rk)
                logger.warning(f"{_TAG} Board: runner.plan_calls 실패 → 원장 파일로 대기 호출 계산 ({e})")
        else:
            out: dict[str, int] = {}
            pre = 0
            for it in plan.get("pending") or []:
                ck = it.get("cell_key") if isinstance(it, dict) else str(it).rsplit("_r", 1)[0]
                out[str(ck)] = out.get(str(ck), 0) + 1
                pre += bool(isinstance(it, dict) and it.get("preapproved"))
            return out, pre
    out = _ledger_pending(root, m)
    rerolls = m.get("rerolls") or {}
    return out, sum(min(int(rerolls.get(ck) or 0), n) for ck, n in out.items())


def _est_usd(job: dict):
    try:
        lo, hi = store.estimate_usd(job.get("model"), job.get("quality"), job.get("size"), int(job.get("n") or 1),
                                    len(job.get("inputs") or []))
    except (ValueError, TypeError):
        return None
    return [lo, hi]


# ══════════════════════════════════════════════════════════════════════
# 썸네일 — 파일·이미지 도우미
# ══════════════════════════════════════════════════════════════════════
def _sig(*parts) -> str:
    blob = json.dumps([_THUMB_VERSION, *parts], sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _thumb_path(root: str, name: str) -> str:
    return store.resolve_path(root, f"{_THUMB_DIR}/{name}")


def _lock_for(path: str) -> threading.Lock:
    return _PATH_LOCKS[int(hashlib.md5(path.encode("utf-8")).hexdigest()[:8], 16) % len(_PATH_LOCKS)]


def _read_bytes(path: str) -> bytes | None:
    try:
        with open(path, "rb") as f:
            return f.read()
    except FileNotFoundError:
        return None


def _open_rel(root: str, rel: str) -> str:
    path = store.resolve_path(root, rel)
    if not os.path.isfile(path):
        raise _Reject(404, f"파일이 없습니다: {rel}")
    return path


def _read_rgb(path: str) -> np.ndarray:
    with Image.open(path) as im:
        return np.asarray(im.convert("RGB"))


def _read_rgba(path: str) -> np.ndarray:
    with Image.open(path) as im:
        return np.asarray(im.convert("RGBA"))


def _read_l(path: str) -> np.ndarray:
    with Image.open(path) as im:
        return np.asarray(im.convert("L"))


def _decode(path: str) -> np.ndarray:
    """후보 원본 → RGB, 부분 투명이 있으면 RGBA (bmk_design_patch._decode 와 같은 규칙)."""
    with Image.open(path) as im:
        im.load()
        if "A" in im.getbands() or (im.mode == "P" and "transparency" in im.info):
            rgba = np.asarray(im.convert("RGBA"))
            return rgba if int(rgba[..., 3].min()) < 255 else np.ascontiguousarray(rgba[..., :3])
        return np.asarray(im.convert("RGB"))


def _over_gray(rgba: np.ndarray) -> np.ndarray:
    if rgba.shape[2] == 3:
        return rgba
    a = rgba[..., 3:4].astype(np.float32) / 255.0
    return np.clip(rgba[..., :3].astype(np.float32) * a + _GRAY * (1.0 - a) + 0.5, 0, 255).astype(np.uint8)


def _load_delta(root: str, tone: dict) -> np.ndarray:
    """톤 보정장 .npy(σ/4 격자 float16) → 크롭 크기 float32 (bmk_design_patch._load_delta 와 같음)."""
    with open(store.resolve_path(root, tone["delta"]), "rb") as f:
        small = np.load(io.BytesIO(f.read()), allow_pickle=False).astype(np.float32)
    w, h = tone["size"]
    if small.shape[:2] != (h, w):
        small = cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)
    return small


def _mask_region(mask: dict | None, x0: int, y0: int, x1: int, y1: int) -> np.ndarray | None:
    """{"arr","left","top","bg"} 캔버스 마스크 → 창 [x0,y0,x1,y1] float32 0..1 (bmk_design_patch._mask_region)."""
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


def _fit(img: np.ndarray, size: int) -> Image.Image:
    pil = Image.fromarray(np.ascontiguousarray(img))
    s = size / max(pil.width, pil.height)
    return pil.resize((max(1, round(pil.width * s)), max(1, round(pil.height * s))), Image.LANCZOS)


def _jpeg(pil: Image.Image) -> bytes:
    buf = io.BytesIO()
    pil.convert("RGB").save(buf, "JPEG", quality=90, subsampling=0)
    return buf.getvalue()


def _cached(root: str, name: str, make: Callable, prepare: Callable | None = None) -> bytes:
    """derived/thumbs/<name> 이 있으면 그 bytes, 없으면 make() → 원자 저장. 같은 파일은 한 스레드만 만든다.
    prepare 가 있으면 잠금 밖에서 먼저 불러 결과를 make(결과) 로 넘긴다 — prepare 는 자기 _cached(placed)를 쓰므로
    한 스레드가 파일 잠금·계산 슬롯을 겹쳐 잡지 않게(겹치면 미리 생성 스레드끼리 교착)."""
    path = _thumb_path(root, name)
    data = _read_bytes(path)
    if data is not None:
        return data
    pre = prepare() if prepare is not None else None
    with _lock_for(path):
        data = _read_bytes(path)
        if data is None:
            with _RENDER_SLOTS:
                data = make() if prepare is None else make(pre)
            store._atomic_write_bytes(path, data)
    return data


# ══════════════════════════════════════════════════════════════════════
# 썸네일 — 배치·초점·kind 별 렌더
# ══════════════════════════════════════════════════════════════════════
def _placed_parts(crop: dict, cand: dict, entry: dict | None) -> list:
    return [cand["file"], [float(v) for v in cand["quad"]], cand.get("src_size"), crop["rect"],
            entry.get("params_hash") if _usable(entry) else None]


def _mask_parts(cand: dict, entry: dict | None) -> list:
    hm = cand.get("hand_mask") or {}
    auto = entry.get("automask") if _usable(entry) else None
    return [hm.get("file"), hm.get("rect"), int(hm.get("bg", 0)) if hm else None, auto]


def _make_placed(root: str, crop: dict, cand: dict, entry: dict | None) -> tuple[np.ndarray, np.ndarray | None]:
    """크롭 로컬 (rgb HxWx3 uint8, cover HxW float32 | None). Analyze 의 배치·정합 + 톤 보정장, 부분 패치는 quad 렌더."""
    x0, y0, x1, y1 = crop["rect"]
    w, h = x1 - x0, y1 - y0
    path = _open_rel(root, cand["file"])
    if _analyzable(crop, cand):
        G = an.place(_read_rgb(path), (w, h))
        if _usable(entry):
            if entry["reg"].get("applied"):
                G = an.warp_affine(G, entry["reg"]["matrix"])
            tone = entry.get("tone") or {}
            if tone.get("delta") and tone.get("size"):
                G = an.apply_delta(G, _load_delta(root, tone))
        return np.ascontiguousarray(G[..., :3]), None
    quad = [float(v) for v in cand["quad"]]
    if dpsd.quad_kind(quad) == "perspective":  # Compose 와 같은 평행사변형 근사
        sw, sh = cand["src_size"]
        quad = an.quad_from_matrix(an.matrix_from_quad(quad, [0, 0, sw, sh]), [0, 0, sw, sh])
    rgb, alpha, left, top = dpsd.render_so_cache(_decode(path), quad)
    out = np.zeros((h, w, 3), np.uint8)
    cov = np.zeros((h, w), np.float32)
    a = np.ones(rgb.shape[:2], np.float32) if alpha is None else alpha.astype(np.float32) / 255.0
    ix0, iy0 = max(x0, left), max(y0, top)
    ix1, iy1 = min(x1, left + rgb.shape[1]), min(y1, top + rgb.shape[0])
    if ix1 > ix0 and iy1 > iy0:
        out[iy0 - y0:iy1 - y0, ix0 - x0:ix1 - x0] = rgb[iy0 - top:iy1 - top, ix0 - left:ix1 - left]
        cov[iy0 - y0:iy1 - y0, ix0 - x0:ix1 - x0] = a[iy0 - top:iy1 - top, ix0 - left:ix1 - left]
    return out, cov


def _placed(root: str, crop: dict, cand: dict, entry: dict | None) -> tuple[np.ndarray, np.ndarray | None]:
    """_make_placed 의 크롭 해상도 결과를 placed_<서명>.png(무손실)로 캐시해 다른 kind·크기가 재사용."""
    name = f"placed_{_sig('placed', _placed_parts(crop, cand, entry))}.png"

    def make() -> bytes:
        rgb, cov = _make_placed(root, crop, cand, entry)
        arr = rgb if cov is None else np.dstack([rgb, np.clip(cov * 255.0 + 0.5, 0, 255).astype(np.uint8)])
        buf = io.BytesIO()
        Image.fromarray(arr).save(buf, "PNG", compress_level=1)
        return buf.getvalue()

    with Image.open(io.BytesIO(_cached(root, name, make))) as im:
        arr = np.asarray(im.convert("RGBA" if "A" in im.getbands() else "RGB"))
    if arr.shape[2] == 4:
        return np.ascontiguousarray(arr[..., :3]), arr[..., 3].astype(np.float32) / 255.0
    return arr, None


def _grow_to(box: list[int], minimum: int, w: int, h: int) -> list[int]:
    """축마다 길이가 minimum(크롭보다 크면 크롭 길이)보다 짧으면 가운데를 유지한 채 넓힌다(크롭 안으로)."""
    out = list(box)
    for i0, i1, lim in ((0, 2, w), (1, 3, h)):
        need = min(minimum, lim)
        if out[i1] - out[i0] < need:
            lo = max(0, min(round((out[i0] + out[i1] - need) / 2), lim - need))
            out[i0], out[i1] = lo, lo + need
    return out


def focus_box(root: str, crop: dict, entries) -> list[int]:
    """타깃 초점 박스(크롭 로컬 [x0,y0,x1,y1]): 자동 마스크(>= 0.5) bbox 합집합을 변마다 10% 넓힘
    (최소 크롭 짧은 변의 1/4). 쓸 수 있는 자동 마스크가 없으면 크롭 전체."""
    x0, y0, x1, y1 = crop["rect"]
    w, h = x1 - x0, y1 - y0
    files = sorted({e["automask"] for e in entries if _usable(e) and e.get("automask")})
    if not files:
        return [0, 0, w, h]
    memo_key = (store.root_key(root), w, h, tuple(files))
    with _FOCUS_LOCK:
        hit = _FOCUS_MEMO.get(memo_key)
    if hit is not None:
        return list(hit)
    bx = None
    for f in files:
        try:
            a = _read_l(store.resolve_path(root, f))
        except OSError:  # 없음, 또는 Analyze 가 밀려난 분석 항목의 derived 파일을 지우는 중(_drop_derived)
            continue
        if a.shape != (h, w):
            continue
        ys, xs = np.nonzero(a >= 128)
        if not len(xs):
            continue
        b = [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]
        bx = b if bx is None else [min(bx[0], b[0]), min(bx[1], b[1]), max(bx[2], b[2]), max(bx[3], b[3])]
    if bx is None:
        box = [0, 0, w, h]
    else:
        px, py = math.ceil((bx[2] - bx[0]) * _FOCUS_PAD), math.ceil((bx[3] - bx[1]) * _FOCUS_PAD)
        box = [max(0, bx[0] - px), max(0, bx[1] - py), min(w, bx[2] + px), min(h, bx[3] + py)]
        box = _grow_to(box, max(16, min(w, h) // 4), w, h)
    with _FOCUS_LOCK:
        if len(_FOCUS_MEMO) > 2048:
            _FOCUS_MEMO.clear()
        _FOCUS_MEMO[memo_key] = list(box)
    return box


def _source_local(root: str, crop: dict) -> np.ndarray:
    """크롭 소스 RGBA(크롭 로컬, 캔버스 밖 = 알파 0)."""
    return _read_rgba(_open_rel(root, crop["source"]))


def _composited(root: str, crop: dict, placed) -> tuple[np.ndarray, np.ndarray]:
    """(크롭 소스 RGBA, 크롭 소스 위에 놓은 후보 RGB) — 부분 패치는 덮은 곳만 후보. placed = _placed() 결과."""
    src = _source_local(root, crop)
    rgb, cov = placed
    if cov is None:
        return src, rgb
    bg = _over_gray(src).astype(np.float32)
    return src, np.clip(bg + (rgb.astype(np.float32) - bg) * cov[..., None] + 0.5, 0, 255).astype(np.uint8)


def _focus_crop(img: np.ndarray, box: list[int]) -> np.ndarray:
    x0, y0, x1, y1 = box
    return img[y0:y1, x0:x1]


def _automask_local(root: str, entry: dict | None, shape) -> np.ndarray | None:
    if not _usable(entry) or not entry.get("automask"):
        return None
    path = store.resolve_path(root, entry["automask"])
    if not os.path.isfile(path):
        return None
    a = _read_l(path)
    return a.astype(np.float32) / 255.0 if a.shape == tuple(shape) else None


def _final_mask_local(root: str, crop: dict, cand: dict, entry: dict | None) -> np.ndarray | None:
    """Compose 기본(hand_else_auto): 손 마스크가 있으면 손, 없으면 자동 마스크, 둘 다 없으면 None(전체 보임)."""
    hm = cand.get("hand_mask")
    if hm:
        arr = _read_l(_open_rel(root, hm["file"]))
        return _mask_region({"arr": arr, "left": int(hm["rect"][0]), "top": int(hm["rect"][1]),
                             "bg": int(hm.get("bg", 0))}, *crop["rect"])
    x0, y0, x1, y1 = crop["rect"]
    return _automask_local(root, entry, (y1 - y0, x1 - x0))


def _render_cand(root, crop, placed, focus, size) -> bytes:
    _src, img = _composited(root, crop, placed)
    return _jpeg(_fit(_focus_crop(img, focus), size))


def _render_before(root, crop, focus, size) -> bytes:
    return _jpeg(_fit(_focus_crop(_over_gray(_source_local(root, crop)), focus), size))


def _render_ref(root, rel, size) -> bytes:
    path = _open_rel(root, rel)
    with Image.open(path) as im:
        rgba = np.asarray(im.convert("RGBA"))
    return _jpeg(_fit(_over_gray(rgba), size))


def _render_delta(root, crop, entry, placed, focus, size) -> bytes:
    src, img = _composited(root, crop, placed)
    d = an.delta_e(src, img, blur=1.5)
    dE = float(((entry or {}).get("params") or {}).get("dE") or 8.0)
    v = np.clip(d / (dE * _HEAT_DE_SPAN), 0.0, 1.0)
    heat = cv2.cvtColor(cv2.applyColorMap((v * 255.0 + 0.5).astype(np.uint8), cv2.COLORMAP_INFERNO), cv2.COLOR_BGR2RGB)
    am = _automask_local(root, entry, d.shape)
    if am is not None:
        fx0, fy0, fx1, fy1 = focus
        t = max(1, round(1.5 * max(fx1 - fx0, fy1 - fy0) / size))
        k = np.ones((2 * t + 1, 2 * t + 1), np.uint8)
        edge = cv2.morphologyEx((am >= 0.5).astype(np.uint8), cv2.MORPH_GRADIENT, k) > 0
        heat[edge] = (40, 230, 230)
    return _jpeg(_fit(_focus_crop(heat, focus), size))


def _render_context(root, m, crop, cand, entry, placed, size) -> bytes:
    if not m.get("base") or not m.get("canvas"):
        raise _Reject(409, "베이스가 없습니다 — Import PSD 를 먼저 실행하세요")
    W, H = (int(v) for v in m["canvas"])
    x0, y0, x1, y1 = crop["rect"]
    w, h = x1 - x0, y1 - y0
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    rx0, ry0 = max(0, math.floor(cx - w * _CONTEXT_SCALE / 2)), max(0, math.floor(cy - h * _CONTEXT_SCALE / 2))
    rx1, ry1 = min(W, math.ceil(cx + w * _CONTEXT_SCALE / 2)), min(H, math.ceil(cy + h * _CONTEXT_SCALE / 2))
    if rx1 <= rx0 or ry1 <= ry0:
        raise _Reject(409, "크롭이 캔버스 밖에 있습니다")
    base = _read_rgb(_open_rel(root, m["base"]))
    region = base[ry0:ry1, rx0:rx1].astype(np.float32)
    rgb, cov = placed
    mask =_final_mask_local(root, crop, cand, entry)
    eff = np.ones((h, w), np.float32) if cov is None else cov
    if mask is not None:
        eff = eff * mask
    ix0, iy0, ix1, iy1 = max(x0, rx0), max(y0, ry0), min(x1, rx1), min(y1, ry1)
    if ix1 > ix0 and iy1 > iy0:
        dst = region[iy0 - ry0:iy1 - ry0, ix0 - rx0:ix1 - rx0]
        src = rgb[iy0 - y0:iy1 - y0, ix0 - x0:ix1 - x0].astype(np.float32)
        a = eff[iy0 - y0:iy1 - y0, ix0 - x0:ix1 - x0, None]
        region[iy0 - ry0:iy1 - ry0, ix0 - rx0:ix1 - rx0] = dst + (src - dst) * a
    out = np.clip(region + 0.5, 0, 255).astype(np.uint8)
    t = max(1, round(max(rx1 - rx0, ry1 - ry0) / size))
    cv2.rectangle(out, (x0 - rx0, y0 - ry0), (x1 - rx0 - 1, y1 - ry0 - 1), (255, 170, 40), t)
    return _jpeg(_fit(out, size))


def _target_ctx(root: str, m: dict, crop: dict, tid: str, pref: dict | None) -> dict:
    cands = _target_members(m, crop["id"], tid)
    entries = {c["key"]: _entry_for(m, c["key"], pref) for c in cands}
    return {"cands": cands, "entries": entries, "focus": focus_box(root, crop, entries.values())}


def _cand_sig(kind: str, size: int, m: dict, crop: dict, cand: dict, entry: dict | None, focus: list[int]) -> str:
    parts = _placed_parts(crop, cand, entry)
    if kind == "cand":
        return _sig("cand", size, parts, crop["source"], focus)
    if kind == "delta":
        dE = ((entry or {}).get("params") or {}).get("dE") if _usable(entry) else None
        return _sig("delta", size, parts, crop["source"], focus, _mask_parts(cand, entry)[3], dE)
    return _sig("context", size, parts, _mask_parts(cand, entry), m.get("base"), m.get("canvas"))


def _thumb_job(root: str, m: dict, kind: str, key: str, size: int,
               pref: dict | None) -> tuple[str, Callable, Callable | None]:
    """(서명, make, prepare) — 서명은 캐시 파일 이름과 URL 의 v. make([placed]) 는 JPEG bytes,
    prepare 는 후보 kind 의 배치(placed 캐시) — _cached 가 잠금 밖에서 먼저 부른다."""
    if kind in ("cand", "delta", "context"):
        if not _KEY_RE.fullmatch(key):
            raise _Reject(400, "후보 키 형식이 잘못되었습니다")
        cand = _cand(m, key)
        if cand is None:
            raise _Reject(404, f"후보가 없습니다: {key}")
        crop = _crop(m, cand["crop_id"])
        if crop is None:
            raise _Reject(404, f"후보의 크롭이 매니페스트에 없습니다: {cand['crop_id']}")
        ctx = _target_ctx(root, m, crop, cand["tid"], pref)
        entry, focus = ctx["entries"][key], ctx["focus"]
        sig = _cand_sig(kind, size, m, crop, cand, entry, focus)

        def prepare():
            return _placed(root, crop, cand, entry)

        if kind == "cand":
            return sig, lambda placed: _render_cand(root, crop, placed, focus, size), prepare
        if kind == "delta":
            return sig, lambda placed: _render_delta(root, crop, entry, placed, focus, size), prepare
        return sig, lambda placed: _render_context(root, m, crop, cand, entry, placed, size), prepare
    parts = key.split("/")
    if len(parts) != (2 if kind == "before" else 3) or not _CROP_ID_RE.fullmatch(parts[0]) \
            or not _TID_RE.fullmatch(parts[1]) or (kind == "ref" and not parts[2].isdigit()):
        raise _Reject(400, "key 형식이 잘못되었습니다(before: crop_id/tid, ref: crop_id/tid/번호)")
    crop = _crop(m, parts[0])
    if crop is None or parts[1] not in [t.get("tid") for t in crop.get("targets") or []]:
        raise _Reject(404, f"타깃이 없습니다: {parts[0]}/{parts[1]}")
    if kind == "before":
        focus = _target_ctx(root, m, crop, parts[1], pref)["focus"]
        return _sig("before", size, crop["source"], crop["rect"], focus), \
            lambda: _render_before(root, crop, focus, size), None
    refs = _target_refs(m, crop["id"], parts[1])
    i = int(parts[2])
    if i >= len(refs):
        raise _Reject(404, f"레퍼런스 {i} 가 없습니다(이 타깃의 레퍼런스 {len(refs)}개)")
    return _sig("ref", size, refs[i]), lambda: _render_ref(root, refs[i], size), None


def render_thumb(root: str, kind: str, key: str, size: int = GRID_SIZE) -> tuple[bytes, str]:
    """썸네일 JPEG bytes 와 서명(캐시 키). derived/thumbs/<kind>_<서명>.jpg 가 있으면 그대로 읽는다."""
    if kind not in THUMB_KINDS:
        raise _Reject(400, f"kind 는 {', '.join(THUMB_KINDS)} 중 하나여야 합니다")
    if size not in THUMB_SIZES:
        raise _Reject(400, f"size 는 {', '.join(map(str, THUMB_SIZES))} 중 하나여야 합니다")
    m = store.load_manifest(root)
    sig, make, prepare = _thumb_job(root, m, kind, key, size, _PREF.get(store.root_key(root)))
    try:
        return _cached(root, f"{kind}_{sig}.jpg", make, prepare), sig
    except (OSError, ValueError) as e:  # 깨진 이미지·크기 불일치 등 — 보드에는 4xx 로
        raise _Reject(422, f"썸네일을 만들 수 없습니다({kind} {key}): {e}") from e


# ══════════════════════════════════════════════════════════════════════
# 상태 요약
# ══════════════════════════════════════════════════════════════════════
def _turl(rk: str, kind: str, key: str, size: int, sig: str) -> str:
    return "thumb?" + urlencode({"root": rk, "kind": kind, "key": key, "size": size, "v": sig})


def _furl(rk: str, rel: str) -> str:
    return "file?" + urlencode({"root": rk, "rel": rel})


def _gates_out(entry: dict | None):
    if not _usable(entry):
        return None
    g = entry.get("gates") or {}
    return {"fails": list(g.get("fails") or []), "outside_mad": g.get("outside_mad"),
            "outside_frac_gt24": g.get("outside_frac_gt24"), "black_band": bool(g.get("black_band"))}


def _reg_out(entry: dict | None):
    if not _usable(entry):
        return None
    r = entry["reg"]
    return {k: r.get(k) for k in ("applied", "kind", "disp", "dx", "dy", "sx", "sy", "rot_deg")}


def build_state(root: str, since_rev: int | None = None) -> dict:
    """state 라우트의 응답(JSON 기본형). URL 은 /bmk/design_patch/ 기준 상대 경로(thumb?…, file?…).
    문자열(이름·라벨)은 그대로 싣는다 — 보드는 textContent 로만 쓴다."""
    m = store.load_manifest(root)
    rk = store.root_key(root)
    running = is_running(root)
    flags = {"running": running, "drain": running and drain_requested(root)}
    if since_rev is not None and since_rev == m["rev"]:
        return {"unchanged": True, "rev": m["rev"], **flags}
    pref = _PREF.get(rk)
    rejects = m.get("rejects") or {}
    picks = m.get("picks") or {}
    rerolls = m.get("rerolls") or {}
    pending, preapproved = _pending(root, m)
    crop_ids = {c.get("id") for c in m["crops"]}
    targets = []
    for crop in m["crops"]:
        x0, y0, x1, y1 = crop["rect"]
        for t in crop.get("targets") or []:
            tid = t["tid"]
            tkey = f"{crop['id']}/{tid}"
            ctx = _target_ctx(root, m, crop, tid, pref)
            focus = ctx["focus"]
            members = {c["key"] for c in ctx["cands"]}
            pick_keys = [k for k in picks.get(tkey) or [] if k in members]
            pick_keys += [c["key"] for c in ctx["cands"] if c.get("picked") and c["key"] not in pick_keys]
            cands = []
            for n, c in enumerate(ctx["cands"], 1):
                e = ctx["entries"][c["key"]]
                info = c.get("origin_info") or {}
                ck, call_id, rep = _cell_of(c)
                gates = _gates_out(e)
                reg = _reg_out(e)
                cands.append({
                    "key": c["key"], "n": n, "origin": c.get("origin"), "backend": info.get("backend"),
                    "mock": info.get("backend") == "mock", "src_size": c.get("src_size"),
                    "label": info.get("layer") or info.get("filename") or c["key"],
                    "picked": c["key"] in pick_keys,
                    "slice_index": pick_keys.index(c["key"]) + 1 if c["key"] in pick_keys else None,
                    "rejected": bool(rejects.get(c["key"])),
                    "cell_key": ck, "call_id": call_id, "rep": rep, "variant": info.get("variant"),
                    "model": info.get("model"), "quality": info.get("quality"),
                    "analysis": "ok" if _usable(e) else ("skipped" if e and e.get("skipped") else "none"),
                    "gates": gates, "reg": reg, "reframed": bool(reg and reg.get("applied")),
                    "hand_mask": bool(c.get("hand_mask")),
                    "thumb_url": _turl(rk, "cand", c["key"], GRID_SIZE,
                                       _cand_sig("cand", GRID_SIZE, m, crop, c, e, focus)),
                    "big_url": _turl(rk, "cand", c["key"], BIG_SIZE, _cand_sig("cand", BIG_SIZE, m, crop, c, e, focus)),
                    "delta_url": _turl(rk, "delta", c["key"], BIG_SIZE,
                                       _cand_sig("delta", BIG_SIZE, m, crop, c, e, focus)),
                    "context_url": _turl(rk, "context", c["key"], BIG_SIZE,
                                         _cand_sig("context", BIG_SIZE, m, crop, c, e, focus)),
                    "file_url": _furl(rk, c["file"]),
                })
            cells = []
            for j in _target_jobs(m, crop["id"], tid):
                ck = j["cell_key"]
                cells.append({"cell_key": ck, "variant": j.get("variant"), "tids": _job_tids(j),
                              "reps": int(j.get("reps") or 1), "rerolls": int(rerolls.get(ck) or 0),
                              "pending": pending.get(ck, 0), "model": j.get("model"), "quality": j.get("quality"),
                              "size": j.get("size"), "n": j.get("n"), "est_usd": _est_usd(j)})
            refs = [{"index": i, "file": r, "thumb_url": _turl(rk, "ref", f"{tkey}/{i}", BIG_SIZE, _sig("ref", BIG_SIZE, r)),
                     "file_url": _furl(rk, r)} for i, r in enumerate(_target_refs(m, crop["id"], tid))]
            live = [c for c in cands if not c["rejected"]]
            targets.append({
                "key": tkey, "crop_id": crop["id"], "tid": tid, "nnn": crop.get("nnn"), "crop_name": crop.get("name"),
                "part": crop.get("part"), "label": t.get("label"), "name_en": t.get("name_en"), "mode": t.get("mode"),
                "rect": crop["rect"], "size": [x1 - x0, y1 - y0], "focus_box": focus,
                "before_url": _turl(rk, "before", tkey, BIG_SIZE,
                                    _sig("before", BIG_SIZE, crop["source"], crop["rect"], focus)),
                "refs": refs, "candidates": cands, "pick_keys": pick_keys, "cells": cells,
                "pending_calls": sum(c["pending"] for c in cells),
                "chips": {"n": len(cands), "live": len(live), "picked": len(pick_keys),
                          "gate_fail": sum(1 for c in live if c["gates"] and c["gates"]["fails"]),
                          "reframed": sum(1 for c in live if c["reframed"]),
                          "mock": sum(1 for c in cands if c["mock"]), "rejected": len(cands) - len(live)},
                "warnings": list(crop.get("warnings") or []),
            })
    return {
        "rev": m["rev"], "project": m.get("project"), "root_key": rk, "backend_mock": _mock_active(root),
        **flags, "time": time.strftime("%H:%M:%S"),
        "pending_calls": sum(pending.values()), "pending_preapproved": preapproved, "rerolls_total": sum(int(v or 0) for v in rerolls.values()),
        "counts": {"targets": len(targets), "candidates": len(m["candidates"]), "rejected": len(rejects),
                   "picks": sum(len(t["pick_keys"]) for t in targets)},
        "orphan_candidates": sum(1 for c in m["candidates"] if c.get("crop_id") not in crop_ids),
        "targets": targets,
    }


def prebuild_thumbs(root: str, analyze_params: dict | None = None, size: int = GRID_SIZE) -> dict:
    """Review 노드용: 보드가 쓸 분석 설정을 기억하고(None = 가장 최근 분석) 후보 그리드(size)·Before(BIG)·
    pick 후보 큰 썸네일을 미리 만든다(이미 있으면 건너뜀). 반환 {"targets","thumbs","built","failed":[...] ,"seconds"}."""
    if size not in THUMB_SIZES:
        raise ValueError(f"size 는 {', '.join(map(str, THUMB_SIZES))} 중 하나여야 합니다: {size!r}")
    t0 = time.time()
    rk = store.root_key(root)
    if analyze_params is None:
        _PREF.pop(rk, None)
    else:
        _PREF[rk] = json.loads(json.dumps(analyze_params))
    st = build_state(root)
    jobs = []
    for t in st["targets"]:
        jobs.append(("before", t["key"], BIG_SIZE))
        for c in t["candidates"]:
            jobs.append(("cand", c["key"], size))
            if c["picked"]:
                jobs.append(("cand", c["key"], BIG_SIZE))
    thumbs = os.path.join(root, *_THUMB_DIR.split("/"))
    before = set(os.listdir(thumbs)) if os.path.isdir(thumbs) else set()
    failed: list[str] = []

    def one(job):
        try:
            render_thumb(root, *job)
        except _Reject as e:
            failed.append(f"{job[0]} {job[1]}: {e.message}")
        except ValueError as e:  # 매니페스트 경로 형식 오류 등 — 한 장 실패가 Review 를 멈추지 않게
            failed.append(f"{job[0]} {job[1]}: {e}")

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
        list(ex.map(one, jobs))
    after = set(os.listdir(thumbs)) if os.path.isdir(thumbs) else set()
    return {"targets": len(st["targets"]), "thumbs": len(jobs), "built": len(after - before), "failed": failed,
            "seconds": round(time.time() - t0, 2)}


# ══════════════════════════════════════════════════════════════════════
# 쓰기 (모두 store.update_manifest)
# ══════════════════════════════════════════════════════════════════════
def apply_pick(root: str, crop_id: str, tid: str, key: str, mode: str) -> tuple[dict, int]:
    """picks[crop_id/tid] 갱신 + 그 타깃 후보의 picked 플래그 동기화. set = ★ 교체(그 후보만), add = slice 추가(끝에),
    remove = 빼기(비면 키 삭제). set/add 는 그 후보의 탈락 표시를 지운다. → ({"pick_keys", "rejected_cleared"}, rev)"""
    if mode not in ("set", "add", "remove"):
        raise _Reject(400, "mode 는 set, add, remove 중 하나여야 합니다")

    def mutate(m):
        cand = _cand(m, key)
        if cand is None:
            raise _Reject(404, f"후보가 없습니다: {key}")
        if cand.get("crop_id") != crop_id or cand.get("tid") != tid:
            raise _Reject(409, f"후보 {key} 는 {cand.get('crop_id')}/{cand.get('tid')} 타깃입니다(보드 상태가 낡음 — 새로고침)")
        members = _target_members(m, crop_id, tid)
        keys = {c["key"] for c in members}
        tkey = f"{crop_id}/{tid}"
        cur = [k for k in m["picks"].get(tkey) or [] if k in keys]
        cur += [c["key"] for c in members if c.get("picked") and c["key"] not in cur]
        if mode == "set":
            new = [key]
        elif mode == "add":
            new = cur if key in cur else cur + [key]
        else:
            new = [k for k in cur if k != key]
        if new:
            m["picks"][tkey] = new
        else:
            m["picks"].pop(tkey, None)
        for c in members:
            c["picked"] = c["key"] in new
        cleared = mode != "remove" and m["rejects"].pop(key, None) is not None
        return {"pick_keys": new, "rejected_cleared": cleared}

    return store.update_manifest(root, mutate)


def apply_reject(root: str, key: str, value: bool) -> tuple[dict, int]:
    """rejects[key] = true / 삭제. 탈락시키면 pick 에서도 뺀다(내보내기에 탈락 후보가 남지 않게)."""

    def mutate(m):
        cand = _cand(m, key)
        if cand is None:
            raise _Reject(404, f"후보가 없습니다: {key}")
        unpicked = False
        if value:
            m["rejects"][key] = True
            tkey = f"{cand['crop_id']}/{cand['tid']}"
            lst = m["picks"].get(tkey) or []
            unpicked = bool(cand.get("picked")) or key in lst
            if key in lst:
                lst = [k for k in lst if k != key]
                if lst:
                    m["picks"][tkey] = lst
                else:
                    m["picks"].pop(tkey, None)
            cand["picked"] = False
        else:
            m["rejects"].pop(key, None)
        return {"rejected": bool(value), "unpicked": unpicked}

    return store.update_manifest(root, mutate)


def apply_reroll(root: str, count: int, cell_key: str | None = None, crop_id: str | None = None,
                 tid: str | None = None, variant: str | None = None) -> tuple[dict, int]:
    """rerolls[cell] += count (보드 사전 승인 호출). 셀은 cell_key, 아니면 (crop_id, tid[, variant]) 로 찾는다
    (variant 가 없으면 그 타깃의 작업이 하나일 때만). 더한 뒤 그 셀의 대기 호출이 _MAX_CELL_PENDING 을 넘으면 409
    (rerolls 는 셀 목표의 누적값이라 줄지 않으므로 상한은 아직 소화하지 않은 대기 호출로 본다)."""
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= _MAX_REROLL_COUNT:
        raise _Reject(400, f"count 는 1–{_MAX_REROLL_COUNT} 정수여야 합니다")

    def mutate(m):
        if cell_key is not None:
            job = next((j for j in m["jobs"] if j.get("cell_key") == cell_key), None)
            if job is None:
                raise _Reject(404, f"작업(셀)이 없습니다: {cell_key} — Prepare 를 다시 실행했다면 보드를 새로고침")
        else:
            jobs = _target_jobs(m, crop_id, tid)
            if variant is not None:
                jobs = [j for j in jobs if j.get("variant") == variant]
            if not jobs:
                raise _Reject(404, f"{crop_id}/{tid} 타깃의 작업이 없습니다" + (f"(변형 {variant})" if variant else "")
                              + " — Prepare 를 먼저 실행하세요")
            if len(jobs) > 1 and variant is None:
                raise _Reject(400, f"{crop_id}/{tid} 타깃에 작업이 {len(jobs)}개입니다 — variant 또는 cell_key 를 지정하세요 "
                                   f"({', '.join(str(j.get('variant')) for j in jobs)})")
            job = jobs[0]
        ck = job["cell_key"]
        cur = int(m["rerolls"].get(ck) or 0)
        waiting = _pending(root, m)[0].get(ck, 0)  # 원장 폴더 몇 개를 훑는 정도(잠금 안에서도 짧다)
        if waiting + count > _MAX_CELL_PENDING:
            raise _Reject(409, f"이 셀에 대기 호출이 이미 {waiting}개입니다(상한 {_MAX_CELL_PENDING}) — Run 으로 소화한 뒤 다시")
        m["rerolls"][ck] = cur + count
        return {"cell_key": ck, "variant": job.get("variant"), "rerolls": cur + count, "est_usd": _est_usd(job)}

    return store.update_manifest(root, mutate)


# ══════════════════════════════════════════════════════════════════════
# HTTP 핸들러
# ══════════════════════════════════════════════════════════════════════
def _dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


def _json(data: dict, status: int = 200) -> web.Response:
    return web.json_response(data, status=status, dumps=_dumps,
                             headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


def _guard(fn):
    @functools.wraps(fn)
    async def wrapped(request: web.Request) -> web.StreamResponse:
        try:
            return await fn(request)
        except _Reject as e:
            return _json({"error": e.message}, status=e.status)
        except ValueError as e:  # store 검증(매니페스트가 사라짐·형식 오류 등)
            return _json({"error": str(e)}, status=400)
    return wrapped


def _root_param(value) -> str:
    root = store.resolve_board_root(value)
    if root is None:
        raise _Reject(404, _ROOT_HINT)
    return root


async def _body(request: web.Request) -> dict:
    if request.content_type != "application/json":
        raise _Reject(415, "Content-Type 은 application/json 이어야 합니다")
    if request.content_length is not None and request.content_length > _MAX_BODY:
        raise _Reject(413, "요청 본문이 너무 큽니다")
    raw = await request.read()
    if len(raw) > _MAX_BODY:
        raise _Reject(413, "요청 본문이 너무 큽니다")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise _Reject(400, "JSON 본문을 읽을 수 없습니다") from None
    if not isinstance(data, dict):
        raise _Reject(400, "JSON 본문은 객체여야 합니다")
    return data


def _field(data: dict, name: str, rx: re.Pattern, required: bool = True) -> str | None:
    v = data.get(name)
    if v is None and not required:
        return None
    if not isinstance(v, str) or not rx.fullmatch(v):
        raise _Reject(400, f"{name} 형식이 잘못되었습니다")
    return v


@_guard
async def board_redirect(request: web.Request) -> web.StreamResponse:
    qs = request.query_string
    raise web.HTTPFound(request.path + "/" + (f"?{qs}" if qs else ""))


@_guard
async def board_static(request: web.Request) -> web.StreamResponse:
    name = request.match_info.get("name") or "board.html"
    ctype = _STATIC.get(name)
    if ctype is None:
        raise _Reject(404, "보드 파일이 아닙니다")
    data = await asyncio.to_thread(_read_bytes, os.path.join(WEB_DIR, name))
    if data is None:
        raise _Reject(404, f"보드 파일이 없습니다: web_design_patch/{name}")
    return web.Response(body=data, content_type=ctype, charset="utf-8",
                        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


@_guard
async def state_handler(request: web.Request) -> web.StreamResponse:
    root = _root_param(request.query.get("root"))
    raw = request.query.get("since_rev")
    since = None
    if raw not in (None, ""):
        if not re.fullmatch(r"\d{1,12}", raw):
            raise _Reject(400, "since_rev 는 0 이상의 정수여야 합니다")
        since = int(raw)
    return _json(await asyncio.to_thread(build_state, root, since))


@_guard
async def thumb_handler(request: web.Request) -> web.StreamResponse:
    q = request.query
    root = _root_param(q.get("root"))
    raw = q.get("size") or str(GRID_SIZE)
    if not raw.isdigit():
        raise _Reject(400, "size 는 정수여야 합니다")
    data, sig = await asyncio.to_thread(render_thumb, root, q.get("kind") or "", q.get("key") or "", int(raw))
    cache = "private, max-age=604800, immutable" if q.get("v") == sig else "no-cache"
    return web.Response(body=data, content_type="image/jpeg",
                        headers={"Cache-Control": cache, "ETag": f'"{sig}"', "X-Content-Type-Options": "nosniff"})


def _file_path(root: str, rel) -> str:
    if not isinstance(rel, str) or not 0 < len(rel) <= 300 or any(ch in rel for ch in "\\:\x00"):
        raise _Reject(400, "rel 형식이 잘못되었습니다")
    try:
        path = store.resolve_path(root, rel)
    except ValueError:
        raise _Reject(400, "프로젝트 폴더 밖이거나 잘못된 경로입니다") from None
    parts = rel.replace("\\", "/").strip("/").split("/")
    if parts[0] not in _FILE_DIRS or os.path.splitext(path)[1].lower() not in _IMG_EXTS:
        raise _Reject(400, f"이미지 파일({'/'.join(_FILE_DIRS)} 아래 {', '.join(_IMG_EXTS)})만 열 수 있습니다")
    real_root = os.path.realpath(root)
    real = os.path.realpath(path)
    if os.path.commonpath([real_root, real]) != real_root:
        raise _Reject(400, "프로젝트 폴더 밖 경로입니다")
    if not os.path.isfile(real):
        raise _Reject(404, f"파일이 없습니다: {rel}")
    return real


@_guard
async def file_handler(request: web.Request) -> web.StreamResponse:
    root = _root_param(request.query.get("root"))
    path = await asyncio.to_thread(_file_path, root, request.query.get("rel"))
    ext = os.path.splitext(path)[1].lower()
    ctype = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}[ext]
    data = await asyncio.to_thread(_read_bytes, path)
    if data is None:
        raise _Reject(404, "파일이 없습니다")
    return web.Response(body=data, content_type=ctype,
                        headers={"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"})


@_guard
async def pick_handler(request: web.Request) -> web.StreamResponse:
    data = await _body(request)
    root = _root_param(data.get("root"))
    crop_id = _field(data, "crop_id", _CROP_ID_RE)
    tid = _field(data, "tid", _TID_RE)
    key = _field(data, "key", _KEY_RE)
    mode = data.get("mode")
    if mode not in ("set", "add", "remove"):
        raise _Reject(400, "mode 는 set, add, remove 중 하나여야 합니다")
    res, rev = await asyncio.to_thread(apply_pick, root, crop_id, tid, key, mode)
    return _json({"ok": True, "rev": rev, **res})


@_guard
async def reject_handler(request: web.Request) -> web.StreamResponse:
    data = await _body(request)
    root = _root_param(data.get("root"))
    key = _field(data, "key", _KEY_RE)
    value = data.get("value")
    if not isinstance(value, bool):
        raise _Reject(400, "value 는 true/false 여야 합니다")
    res, rev = await asyncio.to_thread(apply_reject, root, key, value)
    return _json({"ok": True, "rev": rev, "key": key, **res})


def _reroll_and_count(root: str, count: int, sel: dict) -> dict:
    res, rev = apply_reroll(root, count, **sel)
    pending, preapproved = _pending(root, store.load_manifest(root))
    return {"ok": True, "rev": rev, **res, "cell_pending": pending.get(res["cell_key"], 0),
            "pending_calls": sum(pending.values()), "pending_preapproved": preapproved}


@_guard
async def reroll_handler(request: web.Request) -> web.StreamResponse:
    data = await _body(request)
    root = _root_param(data.get("root"))
    count = data.get("count", 1)
    if data.get("cell_key") is not None:
        sel = {"cell_key": _field(data, "cell_key", _CELL_RE)}
    else:
        sel = {"crop_id": _field(data, "crop_id", _CROP_ID_RE), "tid": _field(data, "tid", _TID_RE),
               "variant": _field(data, "variant", _VARIANT_RE, required=False)}
    return _json(await asyncio.to_thread(_reroll_and_count, root, count, sel))


@_guard
async def drain_handler(request: web.Request) -> web.StreamResponse:
    data = await _body(request)
    root = _root_param(data.get("root"))
    if not is_running(root):  # 실행 밖에서 건 플래그가 다음 Run 을 막지 않게 아무것도 하지 않는다
        return _json({"ok": True, "drain": False, "running": False})
    set_drain(root)
    logger.info(f"{_TAG} Board: drain 요청 {os.path.basename(root)}")
    return _json({"ok": True, "drain": True, "running": True})


_ROUTES = (
    ("GET", f"{API}/board", board_redirect),
    ("GET", f"{API}/board/", board_static),
    ("GET", f"{API}/board/{{name}}", board_static),
    ("GET", f"{API}/state", state_handler),
    ("GET", f"{API}/thumb", thumb_handler),
    ("GET", f"{API}/file", file_handler),
    ("POST", f"{API}/pick", pick_handler),
    ("POST", f"{API}/reject", reject_handler),
    ("POST", f"{API}/reroll", reroll_handler),
    ("POST", f"{API}/drain", drain_handler),
)


def register_routes(routes: web.RouteTableDef, base_dir: str | None = None) -> bool:
    """보드 라우트를 routes(PromptServer.instance.routes)에 더한다. 이미 있으면 아무것도 더하지 않고 False.
    PromptServer.add_routes() 전에(노드 모듈 import 때) 불러야 /api 접두 사본도 생긴다.
    base_dir(ComfyUI input 디렉터리)를 주면 <base_dir>/bmk_design_patch/_roots.json 을 다시 읽는다(재시작 뒤 보드 링크 복구)."""
    if base_dir:
        n = store.load_root_registry(base_dir)
        logger.debug(f"{_TAG} Board: 루트 레지스트리 {n}개 복구 ({base_dir})")
    if any(isinstance(r, web.RouteDef) and r.path == f"{API}/state" for r in routes):
        return False
    for method, path, handler in _ROUTES:
        routes.route(method, path)(handler)
    return True
