"""BMK Design Patch Runner — GPT Image 유료 호출 실행기(실행 계획·원장·API 어댑터·wave/동시성/인터럽트/drain). 노드 없음.

배경
----
Design Patch Run 노드(M2)는 Prepare 가 만든 jobs 를 comfy.org 프록시 `/proxy/openai/images/edits` 로 실행한다.
유료 호출이라 두 가지를 어기지 않는다: (1) 돈을 낸 결과를 잃지 않는다 (2) 같은 호출을 두 번 과금하지 않는다.
그래서 **호출 원장(ledger) 파일을 진실로 삼고** 매니페스트 calls[] 는 그 요약으로만 쓴다.
명세: H:\\BmkNodeDesign\\MultiLayerCropEdit\\_proto\\M2_SPEC.md §2, 근거: _proto\\reports\\final.md §3.6.
이 모듈은 numpy·PIL·표준 라이브러리 + store 만 import 한다. comfy_api_nodes·torch 는 ComfyOrgBackend 가 호출 시점에 읽는다.

호출 단위와 원장
----------------
- 호출 = (cell_key, rep), call_id = "<cell_key>_r<rep>" (rep 은 0부터). 셀 목표 호출 수 = jobs[].reps + rerolls[cell_key].
- 원장 폴더 <root>/cands/<cell_key>/ :
    r<rep>.req.json       요청 원문(모델·품질·크기·n·background·프롬프트·입력 파일/sha·템플릿·변형·백엔드·승인 출처·시도 이력)
    r<rep>.inflight.json  API 호출 **직전** 원자 기록(시작 시각, call_id, pid, 세션 id)
    r<rep>.resp.json      응답 수신 즉시(다운로드 전): data[i] 의 url/b64 여부와 URL, 크레딧, usage
    r<rep>_<slot>.<ext>   원본 바이트 그대로(b64 는 디코드한 바이트 그대로). 이미 있는 다른 바이트는 덮지 않는다(_<sha8> 이름)
    r<rep>.done.json      모든 slot 저장 후 기록 → inflight 삭제.  r<rep>.failed.json  실패(error, status, retryable, maybe_billed, phase)
- 상태(plan_calls): done > (이 프로세스에서 실행 중) inflight > resp 있음 → recoverable(다운로드만, 과금 없음, 자동)
  > failed > inflight 만 남음 → orphaned(과금됐을 수 있음, retry_orphans 일 때만 다시 호출) > 원장 없음 → pending.
- b64 응답은 resp.json 보다 먼저 r<rep>_<slot>.b64.bin(디코드한 바이트)으로 적어 두고 done 뒤에 지운다 — 저장 전에 끊겨도
  다운로드 복구가 그 사본에서 slot 을 저장한다(URL 이 없어 다시 받을 수 없으므로).
- 다운로드 복구는 응답을 받은 백엔드로만 한다(mock URL 은 comfy.org 로, 프록시 URL 은 mock 으로 받을 수 없음) — 다르면 건너뜀.
- 다운로드가 영구 실패(4xx — 만료 서명 URL 400/401/403, 404/410)하면 받은 slot 만으로 done(partial) — 재과금하지 않는다.
- 401/402/403 은 과금 전 거부이므로 그 호출의 원장을 시도 전으로 되돌리고(새 호출 = pending, 재시도 = 원래 orphaned/failed)
  새 발사를 멈춘다.
- 다시 과금하는 재시도(retry_failed / retry_orphans)는 이전 req·failed·inflight 를 새 req.json 의 history 로 옮긴다.

승인
----
- 과금 대상 = 보드 재굴림으로 사전 승인된 호출(preapproved) + (approve == pending_hash 이면 나머지 전부).
  pending_hash 는 **승인이 필요한** 호출(과금·비사전승인)만 해시한다 — 보드 재굴림이 생겨도 사용자 해시가 깨지지 않게.
  해시에 넣는 값은 "backend:<이름>" + approval_id(= call_id + n, 재시도면 + 시도 번호) 목록. n 은 cell_key 에 없으므로,
  approve 위젯에 남은 옛 해시가 n 을 바꾼 호출이나 다시 과금하는 재시도를 승인하지 않게 한다(새 드라이런 → 새 해시).
  백엔드를 넣는 이유: MOCK 드라이런의 해시가 _MOCK_API 를 지운 뒤의 유료 실행을 승인하지 않게.
- 사전 승인 수 = rerolls[cell] − 원장에서 보드 승인으로 이미 쓴 rep 수(지금 req 또는 history 의 approved_by == "board" —
  재시도로 req 가 바뀌어도 쓴 몫은 돌아오지 않는다). 빠진 rep 중 뒤쪽부터 배정(calls_per_cell 을 늘려 생긴 호출은 승인 필요).
- 재시도(retry_failed / retry_orphans)는 항상 승인 필요. 다운로드 복구(recover)는 과금이 없어 승인 없이 실행.
- approved_by: "hash:<h>" | "board" | "retry"(다운로드 복구).

API 어댑터
----------
- ComfyOrgBackend(hidden): 내장 OpenAIGPTImageNodeV2 와 같은 sync_op 인자(엔드포인트·OpenAIImageEditRequest·multipart
  "image"/"image[]"·≤2048² 축소·PNG·asset_urls). 호출마다 새 클래스 type("BMKDPCall_<n>", (), {"hidden": holder})
  → sync_op 의 크레딧 기억(클래스 키)이 동시 호출끼리 섞이지 않는다. holder 필드는 comfy_api.latest HiddenHolder 와 같다
  (comfy_api_nodes 가 읽는 것: unique_id, auth_token_comfy_org, api_key_comfy_org, comfy_usage_source, dynprompt).
  HTTP 상태·본문은 sync_op 의 is_rate_limited 콜백으로 관찰만 한다(항상 False 반환 → 동작 동일). 예외 문구가
  client._friendly_http_message(마지막 관찰 상태, 본문)와 같으면 그 상태(comfy-api 의 평평한 오류 봉투 포함).
  크레딧 조회(_get_remembered_credits_used)는 선택 — 없으면 크레딧 None 으로 실행한다.
- MockBackend(delay_s, fail_plan): 과금 없음. canvas 입력 → 요청 크기 + 색조 이동 + 도형 + "MOCK" 글자(slot/rep 별 시드).
  활성: 환경변수 BMK_DP_MOCK_API=1 또는 <base_dir>/bmk_design_patch/_MOCK_API 파일(내용이 JSON 객체면 MockBackend 인자:
  {"delay_s": 2, "response": "url|b64", "fail_plan": {...}}). fail_plan 키 = call_id 또는 "*":
  {"error": 500|401|"network", "delay": s, "interrupt": true|"request"|"download", "download_fail": k|true,
   "download_status": 404, "n_return": k, "empty": true}.

실행 루프 (run_calls)
---------------------
Semaphore(concurrency) · 발사 직전마다 중지 조건(인터럽트·401·drain·wave·max_new_calls) 확인 · gather(return_exceptions=True)
· 결과마다 짧은 store.update_manifest(calls/candidates) · 인터럽트는 도착분 저장 후 같은 예외를 다시 raise
(진행 중이던 요청은 sync_op 이 취소 → orphaned). 그 밖의 예외는 돈을 쓴 뒤에도 raise 하지 않는다(보고서에 기록).

버전 이력
---------
v1 (2026-10, M2)
- 최초 구현: plan_calls / pending_hash / split_approval, 원장, ComfyOrgBackend / MockBackend, run_calls, reconcile_ledger.
"""

from __future__ import annotations

import asyncio
import base64
import dataclasses
import hashlib
import io
import itertools
import json
import logging
import math
import os
import random
import re
import sys
import threading
import time
import types
import uuid
from typing import Any, Callable, Iterable
from urllib.parse import urlparse

import numpy as np
from PIL import Image, ImageDraw, ImageFont

try:
    from . import bmk_design_patch_store as store
except ImportError:  # 단독 실행/테스트
    import bmk_design_patch_store as store

logger = logging.getLogger(__name__)

_TAG = "[ComfyUI_BMK_Nodes::DesignPatch]"

LEDGER_SCHEMA = "bmk.design_patch.call/1"
BACKEND_COMFY_ORG = "comfy_org"
BACKEND_MOCK = "mock"
MOCK_ENV = "BMK_DP_MOCK_API"
MOCK_FILE = "_MOCK_API"
EDITS_PATH = "/proxy/openai/images/edits"
MAX_INPUT_PIXELS = 2048 * 2048  # 내장 노드와 같은 입력 축소 상한
SECONDS_PER_CALL = 91.0  # 실측(max 품질 1콜) — 예상 시간 표시용
CREDITS_PER_USD = 211  # comfy_api_nodes client._display_text 의 환산
STOP_REASONS = ("none", "wave", "max_calls", "auth", "drain", "interrupt")
AUTH_STATUSES = frozenset({401, 402, 403})  # 과금 전 거부: pending 으로 되돌리고 새 발사 중지

_CELL_RE = re.compile(r"[0-9a-f]{24}")
_LEDGER_RE = re.compile(r"r(\d+)\.(req|inflight|resp|done|failed)\.json")
_LEDGER_KINDS = ("req", "inflight", "resp", "done", "failed")
_HTTP_PREFIXES = {  # client._friendly_http_message 문구 → 상태(관찰값이 없을 때)
    "Unauthorized:": 401, "Payment Required:": 402, "There is a problem with your account": 409,
    "Rate Limit Exceeded": 429,
}
_HTTP_ERROR_STARTS = tuple(_HTTP_PREFIXES) + ("API Error", "The API server could not verify", "HTTP ")
_SESSION = uuid.uuid4().hex[:12]
_CALL_SEQ = itertools.count(1)


class RunInterrupted(Exception):
    """comfy 모듈이 없을 때(테스트·단독) 쓰는 인터럽트 예외."""


class CallError(Exception):
    """백엔드 호출 실패. status = HTTP 상태(모르면 None), phase = encode|request|download|response,
    retryable = 다시 시도할 만함, maybe_billed = 과금됐을 수 있음(네트워크 끊김·응답 검증 실패 등)."""

    def __init__(self, message: str, *, status: int | None = None, phase: str = "request",
                 retryable: bool | None = None, maybe_billed: bool | None = None):
        super().__init__(message)
        self.status = status
        self.phase = phase
        self.retryable = (status is None or status in (408, 429) or status >= 500) if retryable is None else retryable
        self.maybe_billed = (status is None and phase == "request") if maybe_billed is None else maybe_billed


# ══════════════════════════════════════════════════════════════════════
# 공통 보조
# ══════════════════════════════════════════════════════════════════════
def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _rid(root: str) -> str:
    if not isinstance(root, str) or not root.strip():
        raise ValueError(f"{_TAG} 프로젝트 폴더 경로가 비어 있습니다: {root!r}")
    return os.path.normcase(os.path.abspath(root.strip()))


def _check_cell(cell_key: Any) -> str:
    if not isinstance(cell_key, str) or not _CELL_RE.fullmatch(cell_key):
        raise ValueError(f"{_TAG} cell_key 형식이 아닙니다(소문자 hex 24자): {cell_key!r}")
    return cell_key


def call_id_for(cell_key: str, rep: int) -> str:
    return f"{_check_cell(cell_key)}_r{int(rep)}"


def _ldir(root: str, cell_key: str) -> str:
    return store.resolve_path(root, f"cands/{_check_cell(cell_key)}")


def _lpath(root: str, cell_key: str, rep: int, what: str) -> str:
    return os.path.join(_ldir(root, cell_key), f"r{int(rep)}.{what}.json")


def _rawpath(root: str, cell_key: str, rep: int, slot: int) -> str:
    """b64 응답의 디코드한 바이트 사본(다운로드 복구용, done 뒤 삭제). _LEDGER_RE·slot 파일 이름과 겹치지 않는다."""
    return os.path.join(_ldir(root, cell_key), f"r{int(rep)}_{int(slot)}.b64.bin")


def _read_json(path: str) -> Any:
    """없으면 None, 깨졌으면 {"_corrupt": 메시지}(파일이 있다는 사실은 상태 판정에 쓴다)."""
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except FileNotFoundError:
        return None
    except OSError as e:
        return {"_corrupt": str(e)}
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        logger.warning(f"{_TAG} 원장 파일이 깨졌습니다: {path} ({e})")
        return {"_corrupt": str(e)}


def _write_json(path: str, obj: dict) -> None:
    store.write_json_atomic(path, obj)


def _remove(path: str) -> None:
    for attempt in range(6):
        try:
            os.remove(path)
            return
        except FileNotFoundError:
            return
        except PermissionError:  # Windows: 다른 스레드가 잠깐 열고 있음
            if attempt == 5:
                raise
            time.sleep(0.05 * (attempt + 1))


def _sniff_ext(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def _image_size(data: bytes) -> list[int] | None:
    try:
        with Image.open(io.BytesIO(data)) as im:
            return [int(im.size[0]), int(im.size[1])]
    except Exception:  # PIL 은 형식마다 다른 예외를 낸다
        return None


def _nonneg_int(v: Any, default: int) -> int:
    if isinstance(v, bool):
        return default
    try:
        iv = int(v)
    except (TypeError, ValueError):
        return default
    return iv if iv >= 0 and iv == v else default


def _short(msg: Any, limit: int = 300) -> str:
    s = " ".join(str(msg).split())
    return s if len(s) <= limit else s[: limit - 3] + "..."


# ══════════════════════════════════════════════════════════════════════
# 원장 읽기
# ══════════════════════════════════════════════════════════════════════
def read_ledger(root: str, cell_key: str) -> dict[int, dict]:
    """셀의 원장 → {rep: {"req"|"inflight"|"resp"|"done"|"failed": dict}} (있는 파일만)."""
    d = _ldir(root, cell_key)
    out: dict[int, dict] = {}
    try:
        names = os.listdir(d)
    except FileNotFoundError:
        return out
    for fn in names:
        mt = _LEDGER_RE.fullmatch(fn)
        if mt:
            v = _read_json(os.path.join(d, fn))
            if v is not None:
                out.setdefault(int(mt.group(1)), {})[mt.group(2)] = v
    return out


def _ledger_cells(root: str) -> list[str]:
    try:
        names = sorted(os.listdir(os.path.join(root, "cands")))
    except FileNotFoundError:
        return []
    return [n for n in names if _CELL_RE.fullmatch(n) and os.path.isdir(os.path.join(root, "cands", n))]


def _rep_state(files: dict, active: bool) -> str:
    if "done" in files:
        return "done"
    if active:
        return "inflight"
    failed = files.get("failed")
    if "resp" in files:
        return "failed" if isinstance(failed, dict) and failed.get("retryable") is False else "recoverable"
    if "failed" in files:
        return "failed"
    if "inflight" in files:
        return "orphaned"
    return "none"  # 원장 없음(또는 req 만 — 호출 전에 멈춤)


# ══════════════════════════════════════════════════════════════════════
# 실행 중 레지스트리 / drain (프로세스 전역 — 보드 라우트 스레드와 공유)
# ══════════════════════════════════════════════════════════════════════
_REG_LOCK = threading.Lock()
_ACTIVE_CALLS: set[tuple[str, str]] = set()
_RUNS: dict[str, int] = {}
_DRAIN: set[str] = set()


def is_running(root: str) -> bool:
    """이 프로세스에서 root 의 run_calls 가 돌고 있는지(보드의 drain 버튼 활성 여부)."""
    with _REG_LOCK:
        return _RUNS.get(_rid(root), 0) > 0


def request_drain(root: str) -> bool:
    """진행 중 Run 에 drain 요청(새 발사 중지, 진행 중 호출은 끝까지 받음). 돌고 있는 Run 이 없으면 False(아무것도 안 함)."""
    rid = _rid(root)
    with _REG_LOCK:
        if _RUNS.get(rid, 0) <= 0:
            return False
        _DRAIN.add(rid)
        return True


def drain_requested(root: str) -> bool:
    with _REG_LOCK:
        return _rid(root) in _DRAIN


def _is_active(rid: str, call_id: str) -> bool:
    with _REG_LOCK:
        return (rid, call_id) in _ACTIVE_CALLS


def _claim(rid: str, call_id: str) -> bool:
    with _REG_LOCK:
        if (rid, call_id) in _ACTIVE_CALLS:
            return False
        _ACTIVE_CALLS.add((rid, call_id))
        return True


def _release(rid: str, call_id: str) -> None:
    with _REG_LOCK:
        _ACTIVE_CALLS.discard((rid, call_id))


# ══════════════════════════════════════════════════════════════════════
# 실행 계획
# ══════════════════════════════════════════════════════════════════════
_JOB_FIELDS = ("cell_key", "crop_id", "tid", "variant", "model", "quality", "size", "n", "background", "prompt",
               "inputs", "template_version")
_CHANGE_FIELDS = (("model", "모델"), ("quality", "품질"), ("size", "크기"), ("background", "배경"),
                  ("prompt", "프롬프트"), ("inputs", "입력 이미지"), ("template_version", "템플릿"))


def pending_hash(pending_ids: Iterable[str]) -> str:
    """승인 해시 = sha256(정렬한 id 를 ',' 로 이은 ASCII)[:12]. 순서와 무관, 집합이 바뀌면 바뀐다.
    split_approval / run_calls 는 id 로 "backend:<이름>" + approval_id(항목)들을 넘긴다(백엔드가 바뀌면 해시도 바뀜)."""
    ids = sorted(str(x) for x in pending_ids)
    return hashlib.sha256(",".join(ids).encode("ascii")).hexdigest()[:12]


def approval_id(e: dict) -> str:
    """승인 해시에 넣는 호출 표시: "<call_id>:n<n>", 재시도면 ":a<시도 번호>" 를 더한다."""
    aid = f"{e['call_id']}:n{e.get('n')}"
    if e.get("kind") in ("retry_failed", "retry_orphan"):
        aid += f":a{e.get('attempt')}"
    return aid


def _job_view(job: dict) -> dict:
    v = {k: job.get(k) for k in _JOB_FIELDS}
    v["inputs"] = [{"role": i.get("role"), "file": i.get("file"), "sha": i.get("sha")}
                   for i in (job.get("inputs") or []) if isinstance(i, dict)]
    return v


def _est(job: dict) -> list[float]:
    try:
        lo, hi = store.estimate_usd(job.get("model"), job.get("quality"), job.get("size"), job.get("n"),
                                    len(job.get("inputs") or []))
        return [lo, hi]
    except ValueError:
        return [0.0, 0.0]


def _only_match(tokens: list[str], entry: dict) -> bool:
    cid, ck = str(entry.get("crop_id") or ""), entry["cell_key"]
    for t in tokens:  # cell_key 접두는 6자 이상만 — "030" 같은 NNN 이 우연히 같은 hex 로 시작하는 셀을 끌어오지 않게
        if t == cid or cid.startswith(t + "_") or (len(t) >= 6 and ck.startswith(t)) or t == entry["call_id"]:
            return True
    return False


def _board_spent(files: dict) -> bool:
    """이 rep 이 보드 사전 승인으로 한 번이라도 호출됐는지(재시도로 req 가 바뀌어도 history 에 남는다)."""
    req = files.get("req")
    if not isinstance(req, dict):
        return False
    reqs = [req] + [h.get("req") for h in req.get("history") or [] if isinstance(h, dict)]
    return any(isinstance(r, dict) and r.get("approved_by") == "board" for r in reqs)


def _change_reason(job: dict, prior: dict | None) -> str:
    if not prior:
        return "새 셀(처음 실행)"
    diffs = [label for key, label in _CHANGE_FIELDS
             if (prior.get(key) if key != "inputs" else [i.get("sha") for i in prior.get("inputs") or []])
             != (job.get(key) if key != "inputs" else [i.get("sha") for i in job.get("inputs") or []])]
    return "새 셀 — 이전 셀 대비 변경: " + (", ".join(diffs) if diffs else "변형/기타")


def plan_calls(m: dict, ledger_root: str, *, retry_failed: bool = False, retry_orphans: bool = False,
               only: str = "") -> dict:
    """매니페스트 jobs/rerolls + 원장 → {"pending","done","inflight","orphaned","failed", "filtered", "warnings"}.

    원장 파일이 진실이다(매니페스트 calls 는 보지 않는다). 각 항목:
      {call_id, cell_key, rep, crop_id, tid, variant, state, kind, billable, preapproved, reason, n, est_usd:[lo,hi],
       job:{요청 필드}, info:{원장 요약}, stale}
    pending 의 kind: new | recover(다운로드만, 과금 없음) | retry_failed | retry_orphan.
    pending 순서: recover → breadth-first(셀마다 첫 호출부터, jobs 순서). retry_failed / retry_orphans 가 켜지면
    해당 failed / orphaned 항목이 pending 으로 옮겨진다(과금, 승인 필요). only = 쉼표 구분 crop_id / NNN / cell_key 접두
    (6자 이상) / call_id — 승인이 필요한 pending(new·retry)에만 적용, 걸러낸 수는 filtered. 보드 재굴림(사전 승인)과
    다운로드 복구(무료)는 걸러지지 않는다(보드가 요청한 호출이 only 때문에 조용히 밀리지 않게).
    jobs 에 없는 옛 셀(stale)의 원장도 훑어 recoverable 은 pending(recover), orphaned 는 목록에 넣는다(돈 낸 결과 보호)."""
    if not isinstance(m, dict):
        raise ValueError(f"{_TAG} plan_calls: 매니페스트(dict)가 아닙니다: {type(m).__name__}")
    rid = _rid(ledger_root)
    out: dict[str, Any] = {k: [] for k in ("pending", "done", "inflight", "orphaned", "failed")}
    warnings: list[str] = []
    rerolls = m.get("rerolls") if isinstance(m.get("rerolls"), dict) else {}
    tokens = [t.strip() for t in str(only or "").replace("，", ",").split(",") if t.strip()]
    jobs, seen = [], set()
    for job in m.get("jobs") or []:
        ck = job.get("cell_key") if isinstance(job, dict) else None
        if not isinstance(ck, str) or not _CELL_RE.fullmatch(ck):
            warnings.append(f"cell_key 가 잘못된 job 건너뜀: {ck!r}")
            continue
        if ck in seen:
            warnings.append(f"같은 cell_key 의 job 이 둘 이상 → 첫 것만: {ck}")
            continue
        seen.add(ck)
        jobs.append(job)
    prior_index: dict | None = None
    pending_by_cell: list[list[dict]] = []
    recover: list[dict] = []

    def entry(ck, rep, state, view, files, *, kind=None, billable=False, pre=False, reason="", stale=False):
        prev = files.get("req") if isinstance(files.get("req"), dict) else {}
        attempt = _nonneg_int(prev.get("attempt"), 1) + 1 if kind in ("retry_failed", "retry_orphan") else 1
        e = {"call_id": call_id_for(ck, rep), "cell_key": ck, "rep": rep, "crop_id": view.get("crop_id"),
             "tid": view.get("tid"), "variant": view.get("variant"), "state": state, "kind": kind,
             "billable": billable, "preapproved": pre, "reason": reason, "n": view.get("n"), "attempt": attempt,
             "est_usd": _est(view) if billable else [0.0, 0.0], "job": view, "info": _ledger_info(files),
             "stale": stale}
        return e

    for job in jobs:
        ck = job["cell_key"]
        view = _job_view(job)
        reps = _nonneg_int(job.get("reps", 1), 1)
        extra = _nonneg_int(rerolls.get(ck, 0), 0)
        ledger = read_ledger(ledger_root, ck)
        states = {rep: _rep_state(files, _is_active(rid, call_id_for(ck, rep))) for rep, files in ledger.items()}
        board_used = sum(1 for rep, f in ledger.items() if states[rep] != "none" and _board_spent(f))  # req 만 = 호출 전
        board_left = max(0, extra - board_used)
        missing = [r for r in range(reps + extra) if states.get(r, "none") == "none"]
        pre_set = set(missing[len(missing) - board_left:]) if board_left else set()
        fresh_cell = all(s == "none" for s in states.values())
        new_cell_reason = None
        cell_pending = []
        for rep in sorted(set(range(reps + extra)) | set(states)):
            files = ledger.get(rep, {})
            st = states.get(rep, "none")
            if st == "none":
                if rep >= reps + extra:
                    continue
                pre = rep in pre_set
                if pre:
                    reason = "재굴림(보드 사전 승인)"
                elif fresh_cell:
                    if new_cell_reason is None:
                        if prior_index is None:
                            prior_index = _prior_requests(ledger_root, {j["cell_key"] for j in jobs})
                        new_cell_reason = _change_reason(view, prior_index.get(
                            (view.get("crop_id"), view.get("tid"), view.get("variant"))))
                    reason = new_cell_reason
                else:  # 재굴림 몫은 모두 사전 승인으로 배정되므로 나머지는 기본 호출 수(reps) 몫
                    reason = "셀당 호출 수 증가(또는 비어 있는 rep)"
                cell_pending.append(entry(ck, rep, "pending", view, files, kind="new", billable=True, pre=pre,
                                          reason=reason))
            elif st == "recoverable":
                rview = _job_view(files["req"]) if isinstance(files.get("req"), dict) else view
                recover.append(entry(ck, rep, st, rview, files, kind="recover", reason="응답 있음 — 다운로드만 다시"))
            elif st == "failed" and retry_failed:
                cell_pending.append(entry(ck, rep, st, view, files, kind="retry_failed", billable=True,
                                          reason="실패 재시도(retry_failed)"))
            elif st == "orphaned" and retry_orphans:
                cell_pending.append(entry(ck, rep, st, view, files, kind="retry_orphan", billable=True,
                                          reason="orphaned 재호출(retry_orphans, 과금됐을 수 있음)"))
            else:  # done / inflight / failed / orphaned (재시도 꺼짐)
                out[st].append(entry(ck, rep, st, view, files))
        pending_by_cell.append(cell_pending)

    for ck in _ledger_cells(ledger_root):  # jobs 에 없는 옛 셀: 돈 낸 결과(recoverable·orphaned)만
        if ck in seen:
            continue
        for rep, files in sorted(read_ledger(ledger_root, ck).items()):
            st = _rep_state(files, _is_active(rid, call_id_for(ck, rep)))
            rview = _job_view(files["req"]) if isinstance(files.get("req"), dict) else {"cell_key": ck}
            if st == "recoverable":
                recover.append(entry(ck, rep, st, rview, files, kind="recover", reason="옛 셀 — 다운로드만 다시",
                                     stale=True))
            elif st in ("orphaned", "inflight"):
                out[st].append(entry(ck, rep, st, rview, files, stale=True))

    ordered = list(recover)
    depth = max((len(p) for p in pending_by_cell), default=0)
    for i in range(depth):  # breadth-first: 모든 셀의 첫 호출 → 두 번째 …
        ordered += [p[i] for p in pending_by_cell if i < len(p)]
    if tokens:
        kept = [e for e in ordered if e["preapproved"] or not e["billable"] or _only_match(tokens, e)]
        out["filtered"] = len(ordered) - len(kept)
        ordered = kept
    else:
        out["filtered"] = 0
    out["pending"] = ordered
    out["warnings"] = warnings
    return out


def _ledger_info(files: dict) -> dict:
    info: dict[str, Any] = {}
    inf, resp, done, failed = (files.get(k) for k in ("inflight", "resp", "done", "failed"))
    if isinstance(inf, dict):
        info.update(started=inf.get("started"), pid=inf.get("pid"), session=inf.get("session"))
    if isinstance(resp, dict):
        info.update(credits=resp.get("credits"), n_data=len(resp.get("data") or []))
    if isinstance(done, dict):
        info.update(slots=[s.get("file") for s in done.get("slots") or []], credits=done.get("credits"),
                    partial=bool(done.get("partial")))
    if isinstance(failed, dict):
        info.update(error=failed.get("error"), status=failed.get("status"), retryable=failed.get("retryable"),
                    maybe_billed=failed.get("maybe_billed"), phase=failed.get("phase"))
    return info


def _prior_requests(root: str, exclude: set[str]) -> dict:
    """다른(옛) 셀의 가장 최근 요청 → {(crop_id, tid, variant): req} (새 셀 사유: 무엇이 바뀌었나)."""
    out: dict = {}
    for ck in _ledger_cells(root):
        if ck in exclude:
            continue
        for files in read_ledger(root, ck).values():
            req = files.get("req")
            if isinstance(req, dict) and "_corrupt" not in req:
                key = (req.get("crop_id"), req.get("tid"), req.get("variant"))
                if key not in out or str(req.get("time") or "") > str(out[key].get("time") or ""):
                    out[key] = req
    return out


def estimate_calls(entries: Iterable[dict]) -> tuple[float, float]:
    """항목들의 예상 USD 합 (lo, hi) — 과금 항목만(store.estimate_usd, 내장 badge 와 같은 식)."""
    lo = hi = 0.0
    for e in entries:
        if e.get("billable"):
            lo += float(e["est_usd"][0])
            hi += float(e["est_usd"][1])
    return round(lo, 4), round(hi, 4)


def estimate_seconds(count: int, concurrency: int = 4, per_call_s: float = SECONDS_PER_CALL) -> float:
    if count <= 0:
        return 0.0
    return math.ceil(count / max(1, int(concurrency))) * float(per_call_s)


def split_approval(plan: dict, approve: str = "", *, backend: str) -> dict:
    """승인 판정. backend = 이 계획을 실행할 백엔드 이름(BACKEND_MOCK / BACKEND_COMFY_ORG — 해시에 들어간다). 반환:
      pending_hash   승인이 필요한 호출(과금·비사전승인)의 해시. 없으면 ""
      needs_approval 그 호출들 / preapproved 보드 재굴림 / free 다운로드 복구
      approved_now   approve == pending_hash (승인 필요 호출이 있을 때만 True)
      to_run         실행할 호출(plan 순서, approved_by 채움): free + preapproved + (approved_now 면 needs_approval)
      blocked        승인이 없어 과금하지 않는 호출
      est_usd        needs_approval 의 예상 USD (lo, hi) / est_usd_run  to_run 의 과금 예상"""
    pending = plan.get("pending") or []
    need = [e for e in pending if e["billable"] and not e["preapproved"]]
    h = pending_hash([f"backend:{backend}"] + [approval_id(e) for e in need]) if need else ""
    ok = bool(need) and str(approve or "").strip() == h
    to_run = []
    for e in pending:
        if not e["billable"]:
            to_run.append(dict(e, approved_by="retry"))
        elif e["preapproved"]:
            to_run.append(dict(e, approved_by="board"))
        elif ok:
            to_run.append(dict(e, approved_by=f"hash:{h}"))
    return {"pending_hash": h, "approved_now": ok, "to_run": to_run, "blocked": [] if ok else need,
            "needs_approval": need, "preapproved": [e for e in pending if e["billable"] and e["preapproved"]],
            "free": [e for e in pending if not e["billable"]], "est_usd": estimate_calls(need),
            "est_usd_run": estimate_calls(to_run)}


def format_plan(plan: dict, approval: dict, *, concurrency: int = 4) -> str:
    """드라이런/Prepare 보고용 한국어 요약(개수·예상 USD·예상 시간·pending_hash·신규 사유·orphaned/failed 처리법).
    approval = split_approval(plan, …, backend=…) 결과(해시가 백엔드에 묶이므로 호출자가 백엔드를 정한다)."""
    ap = approval
    need = ap["needs_approval"]
    lo, hi = ap["est_usd"]
    lines = [f"호출 계획: 대기 {len(plan['pending'])} (승인 필요 {len(need)} · 보드 재굴림 {len(ap['preapproved'])} · "
             f"다운로드 복구 {len(ap['free'])}) | 완료 {len(plan['done'])} · 실행 중 {len(plan['inflight'])} · "
             f"orphaned {len(plan['orphaned'])} · 실패 {len(plan['failed'])}"
             + (f" | only 로 제외 {plan['filtered']}" if plan.get("filtered") else "")]
    if need:
        secs = estimate_seconds(len(need), concurrency)
        lines.append(f"승인 필요 {len(need)}건 ≈ ${lo:.2f}–${hi:.2f} · 예상 {secs / 60:.0f}분(동시 {concurrency}) | "
                     f"pending_hash {ap['pending_hash']} → approve 에 넣으면 과금")
        reasons: dict[str, int] = {}
        for e in need:
            reasons[e["reason"]] = reasons.get(e["reason"], 0) + 1
        lines += [f"  사유: {r} × {k}" for r, k in reasons.items()]
    if plan["orphaned"]:
        lines.append(f"orphaned {len(plan['orphaned'])}건(과금됐을 수 있음, 결과 없음) — retry_orphans 를 켜야만 다시 호출:")
        lines += [f"  {e['call_id']} {e.get('crop_id') or ''} 시작 {e['info'].get('started')}"
                  + (" (옛 셀)" if e.get("stale") else "") for e in plan["orphaned"]]
    if plan["failed"]:
        lines.append(f"실패 {len(plan['failed'])}건 — retry_failed 를 켜면 다시 호출(과금):")
        lines += [f"  {e['call_id']} {e.get('crop_id') or ''}: {_short(e['info'].get('error'), 120)}" for e in plan["failed"]]
    lines += [f"  경고: {w}" for w in plan.get("warnings") or []]
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════
# 백엔드 — comfy.org (유료)
# ══════════════════════════════════════════════════════════════════════
class _CallHidden:
    """comfy_api.latest._io.HiddenHolder 와 같은 필드(없는 속성은 None). comfy_api_nodes 가 읽는 것:
    unique_id(진행 표시), auth_token_comfy_org / api_key_comfy_org(인증 헤더), comfy_usage_source(헤더), dynprompt."""

    def __init__(self, *, unique_id=None, auth_token_comfy_org=None, api_key_comfy_org=None,
                 comfy_usage_source=None, dynprompt=None):
        self.unique_id = unique_id
        self.prompt = None
        self.extra_pnginfo = None
        self.dynprompt = dynprompt
        self.auth_token_comfy_org = auth_token_comfy_org
        self.api_key_comfy_org = api_key_comfy_org
        self.comfy_usage_source = comfy_usage_source
        self.execution_list = None

    def __getattr__(self, key: str):
        return None

    def __repr__(self) -> str:  # 토큰을 로그에 남기지 않는다
        auth = "token" if self.auth_token_comfy_org else "api_key" if self.api_key_comfy_org else "none"
        return f"_CallHidden(unique_id={self.unique_id!r}, auth={auth})"


def _pick(d: dict, *keys):
    for k in keys:
        if d.get(k) is not None:
            return d[k]
    return None


class ComfyOrgBackend:
    """comfy.org 프록시 GPT Image edits (유료). 비공개 API 결합은 이 클래스에만 둔다.
    hidden = {"auth_token_comfy_org"|"auth_token", "api_key_comfy_org"|"api_key", "comfy_usage_source"|"usage_source",
              "unique_id", "dynprompt"(선택)} — Run 노드의 hidden 인자를 그대로 넘기면 된다."""

    name = BACKEND_COMFY_ORG

    def __init__(self, hidden: dict | None):
        h = dict(hidden or {})
        self.holder = _CallHidden(
            unique_id=_pick(h, "unique_id"),
            auth_token_comfy_org=_pick(h, "auth_token_comfy_org", "auth_token"),
            api_key_comfy_org=_pick(h, "api_key_comfy_org", "api_key"),
            comfy_usage_source=_pick(h, "comfy_usage_source", "usage_source"),
            dynprompt=h.get("dynprompt"),
        )
        self.api: types.SimpleNamespace | None = None

    def __repr__(self) -> str:
        return f"ComfyOrgBackend({self.holder!r})"

    def check(self) -> None:
        self._load_api()

    def _load_api(self) -> types.SimpleNamespace:
        if self.api is None:
            try:
                import torch
                from comfy_api_nodes.apis.openai import OpenAIImageEditRequest, OpenAIImageGenerationResponse
                from comfy_api_nodes.util import ApiEndpoint, download_url_to_bytesio, downscale_image_tensor, sync_op
                from comfy_api_nodes.util import client
                from comfy_api_nodes.util.common_exceptions import ApiServerError, LocalNetworkError
            except Exception as e:  # 업데이트로 경로·이름이 바뀐 경우 등
                raise RuntimeError(
                    f"{_TAG} comfy.org API 모듈(comfy_api_nodes)을 불러오지 못했습니다. ComfyUI 업데이트로 내부 API 가 "
                    f"바뀌었을 수 있습니다 — bmk_design_patch_runner.ComfyOrgBackend 를 확인하세요: {e}") from e
            # 비공개 보조 함수 둘은 선택: 없으면 크레딧 None, HTTP 상태는 문구 접두로만 판단(실행은 막지 않는다)
            credits_used = getattr(client, "_get_remembered_credits_used", None)
            if credits_used is None:
                logger.warning(f"{_TAG} comfy_api_nodes 에 크레딧 조회 함수가 없어 크레딧을 기록하지 않습니다(ComfyUI 업데이트 확인)")
                credits_used = lambda _cls: None  # noqa: E731
            self.api = types.SimpleNamespace(
                torch=torch, sync_op=sync_op, ApiEndpoint=ApiEndpoint, download_url_to_bytesio=download_url_to_bytesio,
                downscale_image_tensor=downscale_image_tensor, OpenAIImageEditRequest=OpenAIImageEditRequest,
                OpenAIImageGenerationResponse=OpenAIImageGenerationResponse, credits_used=credits_used,
                friendly_http_message=getattr(client, "_friendly_http_message", None),
                network_errors=(LocalNetworkError, ApiServerError))
        return self.api

    def call_class(self) -> type:
        """호출마다 새 클래스 — sync_op 의 크레딧·추정 기억(WeakKeyDictionary, 클래스 키)이 섞이지 않게."""
        return type(f"BMKDPCall_{next(_CALL_SEQ)}", (), {"hidden": self.holder})

    def encode_inputs(self, input_paths: list[str]) -> list[tuple]:
        """내장 노드와 같은 multipart files: 1장 "image", 여럿 "image[]"(순서 유지), downscale_image_tensor(≤2048²) → PNG."""
        api = self._load_api()
        files = []
        for i, p in enumerate(input_paths):
            with Image.open(p) as im:
                arr = np.asarray(im.convert("RGB"))
            t = api.torch.from_numpy(arr.astype(np.float32) / 255.0)[None]
            scaled = api.downscale_image_tensor(t, total_pixels=MAX_INPUT_PIXELS).squeeze()
            buf = io.BytesIO()
            Image.fromarray((scaled.numpy() * 255).astype(np.uint8)).save(buf, format="PNG")
            buf.seek(0)
            files.append(("image" if len(input_paths) == 1 else "image[]", (f"image_{i}.png", buf, "image/png")))
        return files

    async def request(self, job: dict, input_paths: list[str], call_id: str) -> dict:
        api = self._load_api()
        if not self.holder.auth_token_comfy_org and not self.holder.api_key_comfy_org:
            raise CallError("ComfyUI 에 comfy.org 로그인(또는 API 키)이 없습니다 — 로그인한 뒤 다시 큐에 넣으세요.",
                            status=401, maybe_billed=False)
        w, h = (int(v) for v in job["size"])
        if not store.gpt_image_custom_size_ok(w, h):
            raise CallError(f"GPT Image 가 받지 않는 출력 크기: {w}x{h}", status=400, retryable=False, maybe_billed=False)
        try:
            files = await asyncio.to_thread(self.encode_inputs, input_paths)
        except Exception as e:  # 요청을 보내기 전 로컬 실패 — 과금 없음, 같은 입력이면 다시 해도 같은 실패
            raise CallError(f"입력 이미지 인코딩 실패(요청 안 보냄): {e}", status=None, phase="encode", retryable=False,
                            maybe_billed=False) from e
        call_cls = self.call_class()
        seen: list[tuple[int, Any]] = []

        def observe_status(status: int, body: Any) -> bool:  # sync_op 의 is_rate_limited: 상태·본문만 기록, 동작은 그대로
            seen.append((int(status), body))
            return False

        try:
            resp = await api.sync_op(
                call_cls,
                api.ApiEndpoint(path=EDITS_PATH, method="POST"),
                response_model=api.OpenAIImageGenerationResponse,
                asset_urls=True,
                data=api.OpenAIImageEditRequest(
                    model=job["model"], prompt=job["prompt"], quality=job["quality"], background=job["background"],
                    n=int(job["n"]), size=f"{w}x{h}", moderation="low"),
                content_type="multipart/form-data",
                files=files,
                is_rate_limited=observe_status,
            )
        except _interrupt_types():
            raise
        except Exception as e:
            raise _request_error(e, seen, api.network_errors, api.friendly_http_message) from e
        credits = api.credits_used(call_cls)
        usage = resp.usage.model_dump(exclude_none=True) if resp.usage is not None else None
        data = [{"url": d.url, "b64": d.b64_json} for d in resp.data or []]
        return {"data": data, "credits": credits, "usage": usage, "ctx": call_cls}

    async def download(self, url: str, ctx: Any = None) -> bytes:
        api = self._load_api()
        buf = io.BytesIO()
        try:
            await api.download_url_to_bytesio(url, buf, cls=ctx or self.call_class())
        except _interrupt_types():
            raise
        except Exception as e:
            raise _download_error(url, e, api.network_errors) from e
        return buf.getvalue()


def _request_error(e: Exception, seen: list[tuple[int, Any]], network_errors: tuple, friendly) -> CallError:
    msg = str(e)
    if isinstance(e, network_errors):
        return CallError(msg, status=None)
    # sync_op 은 마지막 >=400 응답(is_rate_limited 로 관찰한 그 상태·본문)으로 Exception(_friendly_http_message(...))
    # 을 낸다. comfy-api 의 평평한 봉투 {"error": 코드, "message": 문구} 는 접두 없는 문구라 이 비교로만 상태를 안다.
    if seen and friendly is not None and msg == friendly(*seen[-1]):
        return CallError(msg, status=seen[-1][0])
    status = None
    if msg.startswith(_HTTP_ERROR_STARTS):  # HTTP 오류 응답(과금 전 거부). 그 밖의 문구는 과금됐을 수 있음(상태 None)
        # 429 는 is_rate_limited 를 거치지 않으므로 문구 우선, 그다음 마지막으로 관찰한 상태
        status = next((s for p, s in _HTTP_PREFIXES.items() if msg.startswith(p)), None)
        if status is None and seen:
            status = seen[-1][0]
        if status is None:
            mt = re.search(r"HTTP (\d{3})", msg)
            status = int(mt.group(1)) if mt else None
    return CallError(msg, status=status)


def _download_error(url: str, e: Exception, network_errors: tuple) -> CallError:
    msg = str(e)
    if isinstance(e, network_errors):
        return CallError(msg, status=None, phase="download", retryable=True, maybe_billed=False)
    mt = re.search(r"HTTP (\d{3})", msg)
    status = int(mt.group(1)) if mt else None
    p = urlparse(url)
    relative = not p.scheme and not p.netloc  # download_url_to_bytesio 와 같은 판정: 상대 = 인증 헤더를 붙이는 프록시 경로
    # 408/429/5xx 는 core 가 이미 재시도했다. 그 밖의 4xx 는 다시 해도 같다 — 프록시 경로의 인증 거부만 예외(토큰 갱신)
    gone = status is not None and 400 <= status < 500 and status not in (408, 429) and not (relative and status in AUTH_STATUSES)
    return CallError(msg, status=status, phase="download", retryable=not gone, maybe_billed=False)


# ══════════════════════════════════════════════════════════════════════
# 백엔드 — mock (과금 없음)
# ══════════════════════════════════════════════════════════════════════
_MOCK_ASSETS: dict[str, bytes] = {}  # mock://asset/<id> → 바이트 (같은 프로세스 안에서만 — 다운로드 복구 시험용)
_MOCK_ASSET_CALL: dict[str, str] = {}


def _interrupt_types() -> tuple:
    """인터럽트 예외 형식들. comfy 모듈은 이미 import 된 것만 본다(없으면 그 예외도 생길 수 없음)."""
    out: list[type] = [RunInterrupted, asyncio.CancelledError]
    mod = sys.modules.get("comfy_api_nodes.util.common_exceptions")
    if mod is not None:
        out.append(mod.ProcessingInterrupted)
    mod = sys.modules.get("comfy.model_management")
    if mod is not None:
        out.append(mod.InterruptProcessingException)
    return tuple(out)


def _make_interrupt(msg: str = "Task cancelled") -> BaseException:
    mod = sys.modules.get("comfy_api_nodes.util.common_exceptions")
    if mod is not None:
        return mod.ProcessingInterrupted(msg)
    return RunInterrupted(msg)


def _comfy_interrupt_flag() -> bool:
    mod = sys.modules.get("comfy.model_management")
    return bool(mod.processing_interrupted()) if mod is not None else False


def mock_enabled(root: str) -> bool:
    """환경변수 BMK_DP_MOCK_API(1/true/yes/on) 또는 <base_dir>/bmk_design_patch/_MOCK_API 파일(= root 의 부모 폴더)."""
    if os.environ.get(MOCK_ENV, "").strip().lower() in ("1", "true", "yes", "on"):
        return True
    return os.path.isfile(os.path.join(os.path.dirname(os.path.abspath(root)), MOCK_FILE))


def _mock_options(root: str) -> dict:
    path = os.path.join(os.path.dirname(os.path.abspath(root)), MOCK_FILE)
    try:
        with open(path, "rb") as f:
            raw = f.read().decode("utf-8-sig").strip()
    except OSError:
        return {}
    if not raw:
        return {}
    try:
        opts = json.loads(raw)
    except ValueError:
        logger.warning(f"{_TAG} {MOCK_FILE} 내용이 JSON 이 아님 → 기본 mock 설정")
        return {}
    return {k: opts[k] for k in ("delay_s", "fail_plan", "response") if isinstance(opts, dict) and k in opts}


def make_backend(root: str, hidden: dict | None):
    """mock 활성이면 MockBackend(_MOCK_API 의 JSON 옵션 반영), 아니면 ComfyOrgBackend(hidden)."""
    if mock_enabled(root):
        opts = _mock_options(root)
        return MockBackend(delay_s=float(opts.get("delay_s", 1.5)), fail_plan=opts.get("fail_plan"),
                           response=str(opts.get("response", "url")))
    return ComfyOrgBackend(hidden)


@dataclasses.dataclass
class _MockFail:
    error: Any = None
    delay: float | None = None
    interrupt: Any = False  # True / "request" = 요청 중 취소, "download" = 다운로드 중 취소
    download_fail: Any = 0
    download_status: int | None = None
    n_return: int | None = None
    empty: bool = False


class MockBackend:
    """과금 없는 시험 백엔드. 결과 = canvas 입력을 요청 크기로 + 색조 이동 + 도형 + "MOCK" 글자(slot·rep 별 시드) PNG.
    response="url" 이면 mock:// URL(다운로드 경로 시험), "b64" 면 b64. interrupt_check 가 True 를 내면 대기 중인 요청을
    sync_op 처럼 취소한다(인터럽트 예외). 시험용 기록: requests, downloads, served{call_id: [sha256]}, max_active."""

    name = BACKEND_MOCK

    def __init__(self, delay_s: float = 0.0, fail_plan: dict | None = None, *, response: str = "url",
                 interrupt_check: Callable[[], bool] | None = None):
        if response not in ("url", "b64"):
            raise ValueError(f"{_TAG} MockBackend response 는 url / b64: {response!r}")
        self.delay_s = float(delay_s)
        fields = {f.name for f in dataclasses.fields(_MockFail)}
        self.fail_plan = {}
        for k, v in (fail_plan or {}).items():
            unknown = set(v) - fields if isinstance(v, dict) else {"(dict 아님)"}
            if unknown:
                logger.warning(f"{_TAG} mock fail_plan[{k}] 의 모르는 키 무시: {sorted(unknown)}")
            self.fail_plan[str(k)] = _MockFail(**{f: v[f] for f in fields if isinstance(v, dict) and f in v})
        self.response = response
        self.interrupt_check = interrupt_check or _comfy_interrupt_flag
        self.requests: list[dict] = []
        self.downloads: list[str] = []
        self.served: dict[str, list[str]] = {}
        self.active = 0
        self.max_active = 0
        self._dl_fail_left: dict[str, int] = {}

    def __repr__(self) -> str:
        return f"MockBackend(delay_s={self.delay_s}, response={self.response})"

    def check(self) -> None:
        return None

    def _plan(self, call_id: str) -> _MockFail:
        return self.fail_plan.get(call_id) or self.fail_plan.get("*") or _MockFail()

    async def _sleep(self, seconds: float) -> None:
        end = time.monotonic() + max(0.0, seconds)
        while True:
            if self.interrupt_check():
                raise _make_interrupt()
            left = end - time.monotonic()
            if left <= 0:
                return
            await asyncio.sleep(min(0.02, left))

    async def request(self, job: dict, input_paths: list[str], call_id: str) -> dict:
        fp = self._plan(call_id)
        self.requests.append({"call_id": call_id, "n": int(job["n"]), "size": list(job["size"]),
                              "inputs": [os.path.basename(p) for p in input_paths], "t": time.monotonic()})
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await self._sleep(self.delay_s if fp.delay is None else fp.delay)
            if fp.interrupt is True or fp.interrupt == "request":
                raise _make_interrupt()
            if fp.error is not None:
                if fp.error == "network":
                    raise CallError("mock: 연결 끊김", status=None)
                raise CallError(f"mock: HTTP {int(fp.error)}", status=int(fp.error))
            n = 0 if fp.empty else int(job["n"]) if fp.n_return is None else int(fp.n_return)
            images = await asyncio.to_thread(_mock_images, job, input_paths, call_id, n)
        finally:
            self.active -= 1
        self.served[call_id] = [hashlib.sha256(b).hexdigest() for b in images]
        data = []
        for b in images:
            if self.response == "b64":
                data.append({"url": None, "b64": base64.b64encode(b).decode("ascii")})
            else:
                url = f"mock://asset/{uuid.uuid4().hex}"
                _MOCK_ASSETS[url] = b
                _MOCK_ASSET_CALL[url] = call_id
                self._dl_fail_left[url] = (10 ** 9 if fp.download_fail is True else int(fp.download_fail or 0))
                data.append({"url": url, "b64": None})
        try:
            lo, _ = store.estimate_usd(job["model"], job["quality"], job["size"], int(job["n"]), len(input_paths))
            credits = round(lo * CREDITS_PER_USD, 2)
        except ValueError:
            credits = float(n)
        return {"data": data, "credits": credits, "usage": {"mock": True}, "ctx": None}

    async def download(self, url: str, ctx: Any = None) -> bytes:
        self.downloads.append(url)
        fp = self._plan(_MOCK_ASSET_CALL.get(url, ""))
        if self.interrupt_check() or fp.interrupt == "download":
            raise _make_interrupt()
        if fp.download_status is not None:
            raise CallError(f"mock: Failed to download (HTTP {fp.download_status}).", status=fp.download_status,
                            phase="download", retryable=False, maybe_billed=False)
        if self._dl_fail_left.get(url, 0) > 0:
            self._dl_fail_left[url] -= 1
            raise CallError("mock: Failed to download (HTTP 503).", status=503, phase="download", retryable=True,
                            maybe_billed=False)
        if url not in _MOCK_ASSETS:
            raise CallError("mock: 알 수 없는 URL(다른 프로세스에서 만든 mock 결과?)", status=404, phase="download",
                            retryable=False, maybe_billed=False)
        return _MOCK_ASSETS[url]


def _mock_font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def _mock_images(job: dict, input_paths: list[str], call_id: str, n: int) -> list[bytes]:
    w, h = (int(v) for v in job["size"])
    roles = [i.get("role") for i in job.get("inputs") or []]
    canvas = input_paths[roles.index("canvas")] if "canvas" in roles else (input_paths[0] if input_paths else None)
    if canvas:
        with Image.open(canvas) as im:
            base = im.convert("RGB").resize((w, h), Image.Resampling.BILINEAR)
    else:
        base = Image.new("RGB", (w, h), (128, 128, 128))
    hue, sat, val = base.convert("HSV").split()
    out = []
    for slot in range(n):
        rng = random.Random(int(hashlib.sha256(f"{call_id}/{slot}".encode("ascii")).hexdigest()[:12], 16))
        shift = rng.randint(24, 96)
        img = Image.merge("HSV", (hue.point(lambda x, s=shift: (x + s) % 256), sat, val)).convert("RGB")
        d = ImageDraw.Draw(img)
        cx, cy = w * rng.uniform(0.35, 0.65), h * rng.uniform(0.35, 0.65)
        r = min(w, h) * rng.uniform(0.08, 0.16)
        color = tuple(rng.randint(0, 255) for _ in range(3))
        d.ellipse([cx - r, cy - r * 0.7, cx + r, cy + r * 0.7], fill=color, outline=(255, 255, 255),
                  width=max(2, int(r * 0.08)))
        fs = max(12, min(w, h) // 7)
        d.text((w * 0.05, h * 0.04), "MOCK", fill=(255, 0, 255), font=_mock_font(fs), stroke_width=max(1, fs // 12),
               stroke_fill=(0, 0, 0))
        d.text((w * 0.05, h * 0.04 + fs * 1.2), f"{call_id[-10:]} s{slot}", fill=(255, 255, 255),
               font=_mock_font(max(10, fs // 3)), stroke_width=1, stroke_fill=(0, 0, 0))
        buf = io.BytesIO()
        img.save(buf, "PNG", compress_level=1)
        out.append(buf.getvalue())
    return out


# ══════════════════════════════════════════════════════════════════════
# 원장 쓰기 (동기 — run_calls 가 asyncio.to_thread 로 부른다)
# ══════════════════════════════════════════════════════════════════════
def _begin_call(root: str, e: dict, backend_name: str) -> dict:
    """req.json(재시도면 이전 시도를 history 로) → inflight.json → (재시도면) 이전 failed/resp 삭제.
    반환 = {"req", "inflight", "prev"}. prev = 재시도 전 원장 파일의 원본 바이트 {종류: bytes} — 과금 전 거부(401 등)면
    _request_failed 가 이것으로 되돌린다(깨진 파일도 바이트 그대로 보존)."""
    ck, rep = e["cell_key"], e["rep"]
    os.makedirs(_ldir(root, ck), exist_ok=True)
    old = read_ledger(root, ck).get(rep, {})
    prev_raw: dict[str, bytes] = {}
    if e["kind"] in ("retry_failed", "retry_orphan"):
        for what in old:
            with open(_lpath(root, ck, rep, what), "rb") as f:
                prev_raw[what] = f.read()
    history = list((old.get("req") or {}).get("history") or []) if isinstance(old.get("req"), dict) else []
    if e["kind"] in ("retry_failed", "retry_orphan") and old:
        prev = {k: v for k, v in old.items() if k != "req"}
        if isinstance(old.get("req"), dict):
            prev["req"] = {k: old["req"].get(k) for k in ("time", "approved_by", "backend", "attempt", "n")}
        history.append(prev)
    job = e["job"]
    req = {"schema": LEDGER_SCHEMA, "call_id": e["call_id"], "cell_key": ck, "rep": rep,
           **{k: job.get(k) for k in _JOB_FIELDS if k != "cell_key"},
           "backend": backend_name, "approved_by": e.get("approved_by"), "kind": e["kind"],
           "attempt": len(history) + 1, "history": history, "time": _now()}
    _write_json(_lpath(root, ck, rep, "req"), req)
    inflight = {"call_id": e["call_id"], "started": _now(), "started_ts": time.time(), "pid": os.getpid(),
                "session": _SESSION, "backend": backend_name}
    _write_json(_lpath(root, ck, rep, "inflight"), inflight)
    for what in ("failed", "resp"):  # 이전 시도의 흔적(history 로 옮김). inflight 뒤에 지워 중간에 끊겨도 "req 만"(= 새 호출)이 되지 않게
        if what in prev_raw:
            _remove(_lpath(root, ck, rep, what))
    return {"req": req, "inflight": inflight, "prev": prev_raw}


def _save_slot(root: str, ck: str, rep: int, slot: int, data: bytes) -> dict:
    """원본 바이트 그대로 저장(디코드·재인코드 없음). 같은 이름에 다른 바이트가 있으면 덮지 않고 _<sha8> 이름."""
    ext = _sniff_ext(data)
    size = _image_size(data) if ext else None
    if ext is None or size is None:
        raise CallError(f"다운로드한 내용이 이미지가 아닙니다({len(data)} bytes)", status=None, phase="download",
                        retryable=True, maybe_billed=False)
    sha = hashlib.sha256(data).hexdigest()
    rel = f"cands/{ck}/r{rep}_{slot}.{ext}"
    path = store.resolve_path(root, rel)
    if os.path.isfile(path):
        with open(path, "rb") as f:
            same = hashlib.sha256(f.read()).hexdigest() == sha
        if not same:
            rel = f"cands/{ck}/r{rep}_{slot}_{sha[:8]}.{ext}"
            path = store.resolve_path(root, rel)
    if not os.path.isfile(path):
        store._atomic_write_bytes(path, data)
    return {"slot": slot, "file": rel, "sha256": sha, "bytes": len(data), "size": size, "ext": ext}


def _read_raw(root: str, ck: str, rep: int, slot: int) -> bytes:
    path = _rawpath(root, ck, rep, slot)
    if not os.path.isfile(path):
        raise CallError("b64 사본이 없음(복구 불가)", status=None, phase="download", retryable=False)
    with open(path, "rb") as f:
        return f.read()


def _existing_slot(root: str, ck: str, rep: int, slot: int) -> dict | None:
    """복구: 이미 저장된 slot(원자 저장이라 있으면 완전함)."""
    d = _ldir(root, ck)
    for fn in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        mt = re.fullmatch(rf"r{rep}_{slot}(?:_[0-9a-f]{{8}})?\.(png|jpg|webp)", fn)
        if mt:
            with open(os.path.join(d, fn), "rb") as f:
                data = f.read()
            return {"slot": slot, "file": f"cands/{ck}/{fn}", "sha256": hashlib.sha256(data).hexdigest(),
                    "bytes": len(data), "size": _image_size(data), "ext": mt.group(1)}
    return None


# ══════════════════════════════════════════════════════════════════════
# 매니페스트 반영 (요약) — 원장에서 만든 레코드를 update_manifest 로
# ══════════════════════════════════════════════════════════════════════
def _call_record(ck: str, rep: int, files: dict, state: str) -> dict:
    """원장 파일 → 매니페스트 calls[] 항목(같은 파일이면 항상 같은 값 → 재조정이 rev 를 흔들지 않음)."""
    req, inf, resp, done, failed = (files.get(k) if isinstance(files.get(k), dict) else {} for k in _LEDGER_KINDS)
    if state == "recoverable":  # 응답은 있음: 다운로드 실패 → failed, 저장 중 끊김 → orphaned (둘 다 다음 실행이 다운로드만)
        status = "failed" if failed else "orphaned"
    else:
        status = {"done": "done", "inflight": "inflight", "failed": "failed", "orphaned": "orphaned"}.get(state, "pending")
    urls = [d.get("url") for d in resp.get("data") or [] if isinstance(d, dict) and d.get("url")]
    return {
        "call_id": call_id_for(ck, rep), "cell_key": ck, "rep": rep, "status": status,
        "crop_id": req.get("crop_id"), "tid": req.get("tid"), "variant": req.get("variant"), "kind": req.get("kind"),
        "attempt": req.get("attempt"), "backend": req.get("backend") or inf.get("backend"), "n": req.get("n"),
        "started": inf.get("started") or done.get("started") or failed.get("started"),
        "finished": done.get("finished") or failed.get("time"), "elapsed_s": done.get("elapsed_s") or failed.get("elapsed_s"),
        "credits": done.get("credits", resp.get("credits")), "urls": urls,
        "slots": [s.get("file") for s in done.get("slots") or []],
        "error": failed.get("error") or (done.get("missing_error") if done.get("partial") else None),
        "approved_by": req.get("approved_by"),
    }


def _quad(rect) -> list[float]:
    x0, y0, x1, y1 = (float(v) for v in rect)
    return [x0, y0, x1, y0, x1, y1, x0, y1]


def _upsert_call(m: dict, rec: dict) -> None:
    calls = m.setdefault("calls", [])
    for i, c in enumerate(calls):
        if isinstance(c, dict) and c.get("call_id") == rec["call_id"]:
            calls[i] = rec
            return
    calls.append(rec)


def _add_candidates(m: dict, req: dict, done: dict, warns: list[str]) -> list[str]:
    """done slot → candidates(타깃마다 1개, 이미 있으면 그대로 — picked 를 건드리지 않음). 새 key 목록."""
    crop = next((c for c in m.get("crops") or [] if c.get("id") == req.get("crop_id")), None)
    if crop is None:
        warns.append(f"{req.get('call_id')}: 크롭 {req.get('crop_id')} 이 매니페스트에 없어 후보로 등록하지 않음(원장에는 있음)")
        return []
    tids_crop = [t.get("tid") for t in crop.get("targets") or []]
    tids = [t.strip() for t in str(req.get("tid") or "").split(",") if t.strip() in tids_crop] or tids_crop[:1]
    have = {c.get("key") for c in m.get("candidates") or [] if isinstance(c, dict)}
    ck, rep = req["cell_key"], int(req["rep"])
    new = []
    for s in done.get("slots") or []:
        for tid in tids:
            key = f"r_{ck[:12]}_{rep}_{s['slot']}" + (f"_{tid}" if len(tids) > 1 else "")
            if key in have:
                continue
            m.setdefault("candidates", []).append({
                "key": key, "crop_id": crop["id"], "tid": tid, "file": s["file"], "src_size": s.get("size"),
                "origin": "run",
                "origin_info": {"call_id": req.get("call_id"), "cell_key": ck, "rep": rep, "slot": s["slot"],
                                "variant": req.get("variant"), "model": req.get("model"), "quality": req.get("quality"),
                                "backend": req.get("backend"), "tids": tids},
                "quad": _quad(crop["rect"]), "picked": False, "z": None, "hand_mask": None,
            })
            have.add(key)
            new.append(key)
    return new


def _apply_result(root: str, ck: str, rep: int, files: dict, state: str) -> tuple[list[str], list[str]]:
    """결과 하나를 짧은 트랜잭션으로 반영 → (새 후보 key, 경고)."""
    warns: list[str] = []

    def mutate(m):
        _upsert_call(m, _call_record(ck, rep, files, state))
        if state == "done" and isinstance(files.get("req"), dict) and isinstance(files.get("done"), dict):
            return _add_candidates(m, files["req"], files["done"], warns)
        return []

    keys, _ = store.update_manifest(root, mutate)
    return keys, warns


def reconcile_ledger(root: str) -> dict:
    """원장 → 매니페스트 재조정(한 트랜잭션). 원장에는 끝났는데 매니페스트에 반영되지 않은 호출(저장 직후 크래시)의
    calls·candidates 를 채우고, calls 상태를 원장에 맞춘다(실행 중이 아닌 inflight → orphaned/pending).
    반환 {"calls": 바뀐 calls 수, "candidates": 새 후보 key 목록, "warnings": [...]}."""
    rid = _rid(root)
    warns: list[str] = []

    def mutate(m):
        # 원장은 잠금 안에서 읽는다 — 밖에서 읽은 낡은 상태가 다른 실행이 방금 쓴 done 을 덮지 않게
        snap = {ck: read_ledger(root, ck) for ck in _ledger_cells(root)}
        before = {c.get("call_id"): c for c in m.get("calls") or [] if isinstance(c, dict)}
        new, changed, seen = [], 0, set()
        for ck, ledger in snap.items():
            for rep, files in sorted(ledger.items()):
                cid = call_id_for(ck, rep)
                seen.add(cid)
                state = _rep_state(files, _is_active(rid, cid))
                rec = _call_record(ck, rep, files, state)
                if before.get(cid) != rec:
                    _upsert_call(m, rec)
                    changed += 1
                if state == "done" and isinstance(files.get("req"), dict) and isinstance(files.get("done"), dict):
                    new += _add_candidates(m, files["req"], files["done"], warns)
        for c in m.get("calls") or []:
            if (isinstance(c, dict) and c.get("status") == "inflight" and c.get("call_id") not in seen
                    and not _is_active(rid, str(c.get("call_id")))):
                c["status"] = "pending"
                changed += 1
        return {"calls": changed, "candidates": new}

    res, _ = store.update_manifest(root, mutate)
    res["warnings"] = warns
    return res


# ══════════════════════════════════════════════════════════════════════
# 실행 루프
# ══════════════════════════════════════════════════════════════════════
@dataclasses.dataclass
class RunReport:
    """run_calls 결과. orphaned / remaining 은 call_id 목록, calls 는 호출별 요약 dict 목록."""

    backend: str
    launched: int = 0  # 과금 호출 발사 수(다운로드 복구 제외)
    done: int = 0  # 이번에 끝난 호출(복구 포함)
    recovered: int = 0  # 그중 다운로드만 다시 받은 것(과금 없음)
    failed: int = 0
    orphaned: list = dataclasses.field(default_factory=list)  # 인터럽트로 결과 없이 끝난 진행 중 호출(과금됐을 수 있음)
    remaining: list = dataclasses.field(default_factory=list)  # 발사하지 않은 호출(pending 유지)
    skipped: list = dataclasses.field(default_factory=list)  # [{"call_id","why"}] 승인 없음·이미 실행 중·상태 바뀜
    credits_total: float | None = None
    elapsed: float = 0.0
    calls: list = dataclasses.field(default_factory=list)
    stopped_reason: str = "none"  # wave|max_calls|auth|drain|interrupt|none
    new_candidates: list = dataclasses.field(default_factory=list)
    continuation_hash: str = ""  # remaining 중 승인이 필요한 것의 pending_hash (wave 자동 이어가기용)
    errors: list = dataclasses.field(default_factory=list)  # 저장·반영 오류(원장 기준 다음 실행에서 복구)
    warnings: list = dataclasses.field(default_factory=list)
    limits: dict = dataclasses.field(default_factory=dict)

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    def text(self) -> str:
        be = "MOCK" if self.backend == BACKEND_MOCK else self.backend
        cr = "-" if self.credits_total is None else f"{self.credits_total:,.2f}"
        head = (f"Run [{be}] 발사 {self.launched} · 완료 {self.done}"
                + (f"(다운로드 복구 {self.recovered})" if self.recovered else "")
                + f" · 실패 {self.failed} · orphaned {len(self.orphaned)} · 남음 {len(self.remaining)}"
                + f" · 크레딧 {cr} · {self.elapsed:.1f}s")
        why = {
            "wave": f"wave 시간 상한({self.limits.get('wave_minutes')}분) — 다시 큐에 넣으면 남은 것부터 이어서",
            "max_calls": f"max_new_calls 상한({self.limits.get('max_new_calls')}) — 다시 큐에 넣으면 이어서",
            "auth": "인증 만료/거부(401/402/403) — 다시 로그인(또는 크레딧 충전)한 뒤 다시 큐에 넣으면 이어서",
            "drain": "drain 요청 — 진행 중 호출만 받고 멈춤. 다시 큐에 넣으면 이어서",
            "interrupt": "취소됨 — 진행 중이던 호출은 결과 없이 orphaned(과금됐을 수 있음). retry_orphans 로만 다시 호출",
        }.get(self.stopped_reason)
        lines = [head] + ([f"중지: {why}"] if why else [])
        for c in self.calls:
            tail = (f"{c.get('slots') or 0}장" if c["status"] == "done" else _short(c.get("error") or "", 160))
            cc = "" if c.get("credits") is None else f" · 크레딧 {float(c['credits']):,.2f}"
            lines.append(f"  {c['call_id']} {c.get('crop_id') or ''}/{c.get('tid') or ''} {c.get('variant') or ''} "
                         f"[{c['kind']}] {c['status']}: {tail}{cc} · {float(c.get('elapsed_s') or 0):.1f}s")
        if self.orphaned:
            lines.append(f"orphaned {len(self.orphaned)}건: " + ", ".join(self.orphaned)
                         + " — 결과를 받지 못함. 과금 여부는 comfy.org 사용 내역 확인, 다시 하려면 retry_orphans")
        if self.remaining:
            lines.append(f"남은 호출 {len(self.remaining)}건(과금 안 됨, pending 유지)")
        for s in self.skipped:
            lines.append(f"  건너뜀 {s['call_id']}: {s['why']}")
        lines += [f"  경고: {w}" for w in self.warnings]
        lines += [f"  오류: {w}" for w in self.errors]
        return "\n".join(lines)


class _RunState:
    def __init__(self, report: RunReport, wave_s: float, max_new: int, drain_flag, interrupt_check):
        self.report = report
        self.t0 = time.monotonic()
        self.wave_s = wave_s
        self.max_new = max_new
        self.drain_flag = drain_flag
        self.interrupt_check = interrupt_check
        self.stop: str | None = None
        self.interrupt_exc: BaseException | None = None
        self.finished = 0
        self.credits: list[float] = []

    def set_stop(self, reason: str) -> None:
        if self.stop is None:
            self.stop = reason

    def interrupt(self, exc: BaseException | None) -> None:
        self.stop = "interrupt"  # 다른 중지 사유(wave 등) 뒤에 온 취소도 반드시 다시 raise 되게 덮어쓴다
        if self.interrupt_exc is None and exc is not None:
            self.interrupt_exc = exc

    def launch_block(self, e: dict) -> str | None:
        """발사 직전 확인 → 막을 이유(없으면 None, 과금 호출이면 발사 수를 이 자리에서 예약 — await 없이).
        다운로드 복구는 과금이 없어 wave·max_calls·drain·auth 에 막히지 않는다(인터럽트만)."""
        if self.stop == "interrupt":
            return "interrupt"
        if self.interrupt_check():
            self.interrupt(None)
            return "interrupt"
        if not e["billable"]:
            return None
        if self.stop is not None:
            return self.stop
        if self.drain_flag():
            self.set_stop("drain")
            return "drain"
        if time.monotonic() - self.t0 >= self.wave_s:
            self.set_stop("wave")
            return "wave"
        if self.report.launched >= self.max_new:
            self.set_stop("max_calls")
            return "max_calls"
        self.report.launched += 1
        return None


async def _to_thread(fn, *args):
    return await asyncio.to_thread(fn, *args)


async def run_calls(root: str, calls: list[dict], backend, *, concurrency: int = 4, wave_minutes: float = 25,
                    max_new_calls: int = 64, drain_flag: Callable[[], bool] | None = None,
                    progress_cb: Callable[[int, int, str], Any] | None = None,
                    interrupt_check: Callable[[], bool] | None = None) -> RunReport:
    """calls(split_approval()["to_run"] 항목) 실행 → RunReport.

    - asyncio.Semaphore(concurrency). 발사 직전마다: 인터럽트 → (과금 호출만) 401 중지 · drain · wave(시작 후
      wave_minutes 경과) · max_new_calls(과금 발사 수) 순으로 확인, 걸리면 그 뒤 호출은 remaining(pending 유지).
    - 과금 호출에 approved_by 가 없으면 실행하지 않는다(skipped). 같은 call_id 가 이미 실행 중이거나 원장 상태가 계획과
      달라졌으면 건너뛴다(이중 과금 방지).
    - 결과는 도착하는 대로 원장 저장 + store.update_manifest(calls/candidates) 짧은 트랜잭션.
    - gather(return_exceptions=True): 한 호출 실패가 나머지를 버리지 않고, 인터럽트가 아닌 예외는 raise 하지 않는다.
    - 인터럽트(ProcessingInterrupted / InterruptProcessingException / CancelledError, 또는 interrupt_check()):
      새 발사 중지 → 도착분 저장 → 같은 예외를 다시 raise(예외에 bmk_dp_report 속성으로 RunReport 를 붙임).
      진행 중이던 요청은 sync_op 이 취소한다 — 원장 inflight 가 남아 orphaned 로 보고된다.
    - drain_flag 기본 = drain_requested(root) (보드 POST /drain), interrupt_check 기본 = comfy 인터럽트 플래그.
    - progress_cb(done, total, text): 호출이 끝날 때마다. 거기서 나는 InterruptProcessingException 은 인터럽트로 처리하고
      comfy 플래그를 다시 세운다(내장 poll_op 와 같은 방식 — 다른 진행 중 요청도 취소되게)."""
    rid = _rid(root)
    if not isinstance(concurrency, int) or isinstance(concurrency, bool) or concurrency < 1:
        raise ValueError(f"{_TAG} concurrency 는 1 이상의 정수여야 합니다: {concurrency!r}")
    if not (isinstance(wave_minutes, (int, float)) and wave_minutes > 0):
        raise ValueError(f"{_TAG} wave_minutes 는 0 보다 커야 합니다: {wave_minutes!r}")
    max_new_calls = int(max_new_calls)
    if max_new_calls < 0:
        raise ValueError(f"{_TAG} max_new_calls 는 0 이상이어야 합니다: {max_new_calls!r}")
    backend.check()
    report = RunReport(backend=backend.name, limits={"concurrency": concurrency, "wave_minutes": wave_minutes,
                                                     "max_new_calls": max_new_calls})
    drain = drain_flag or (lambda: drain_requested(root))
    state = _RunState(report, float(wave_minutes) * 60.0, max_new_calls, drain, interrupt_check or _comfy_interrupt_flag)
    calls = list(calls)
    total = len(calls)
    with _REG_LOCK:
        if _RUNS.get(rid, 0) == 0:
            _DRAIN.discard(rid)  # 지난 drain 요청이 새 실행을 막지 않게
        _RUNS[rid] = _RUNS.get(rid, 0) + 1
    try:
        await _record_start(root, calls, report)
        sem = asyncio.Semaphore(concurrency)

        async def one(e: dict) -> None:
            async with sem:
                await _one_call(root, rid, e, backend, state)
            state.finished += 1
            _progress(progress_cb, state, total)

        results = await asyncio.gather(*(one(e) for e in calls), return_exceptions=True)
        for e, r in zip(calls, results):
            if isinstance(r, _interrupt_types()):
                state.interrupt(r)
            elif isinstance(r, BaseException):
                logger.error(f"{_TAG} 호출 처리 중 예외({e.get('call_id')}): {r!r}")
                report.errors.append(f"{e.get('call_id')}: {_short(repr(r))}")
        # wave·max_calls 로 멈춘 뒤 진행 중 호출을 기다리는 사이에 누른 Drain 도 drain 으로 보고한다
        # (wave 로 남으면 노드가 bmk.dp.wave 를 보내 자동 이어가기가 다음 wave 를 과금한다). 플래그는 finally 가 지운다
        if state.stop in ("wave", "max_calls") and state.drain_flag():
            state.stop = "drain"
    finally:
        with _REG_LOCK:
            _RUNS[rid] = _RUNS.get(rid, 1) - 1
            if _RUNS[rid] <= 0:
                _RUNS.pop(rid, None)
                _DRAIN.discard(rid)
    report.elapsed = round(time.monotonic() - state.t0, 2)
    report.credits_total = round(sum(state.credits), 4) if state.credits else None
    report.stopped_reason = state.stop or "none"
    left = set(report.remaining)
    need = [approval_id(e) for e in calls if e["call_id"] in left and e["billable"] and not e.get("preapproved")]
    report.continuation_hash = pending_hash([f"backend:{backend.name}"] + need) if need else ""
    logger.info(f"{_TAG} {report.text().splitlines()[0]}")
    if state.stop == "interrupt":
        exc = state.interrupt_exc or _final_interrupt()
        try:
            exc.bmk_dp_report = report
        except AttributeError:
            pass
        raise exc
    return report


def _final_interrupt() -> BaseException:
    mod = sys.modules.get("comfy.model_management")
    if mod is not None:
        return mod.InterruptProcessingException()
    return _make_interrupt()


def _progress(cb, state: _RunState, total: int) -> None:
    if cb is None:
        return
    r = state.report
    cr = f" · 크레딧 {sum(state.credits):,.1f}" if state.credits else ""
    text = (f"Design Patch Run {state.finished}/{total} · 완료 {r.done} · 실패 {r.failed} · 남음 {len(r.remaining)}{cr}"
            + (f" · 중지({state.stop})" if state.stop else ""))
    try:
        cb(state.finished, total, text)
    except _interrupt_types() as e:
        state.interrupt(e)
        mod = sys.modules.get("comfy.model_management")
        if mod is not None:  # ProgressBar 훅이 플래그를 지웠다 → 다시 세워 진행 중 sync_op 도 취소되게
            mod.interrupt_current_processing(True)
    except Exception as e:  # 진행 표시 실패가 유료 실행을 멈추면 안 된다
        logger.warning(f"{_TAG} progress_cb 오류(무시): {e!r}")


async def _record_start(root: str, calls: list[dict], report: RunReport) -> None:
    """approvals[hash] 기록 + 원장 재조정(지난 실행의 미반영 결과)."""
    counts: dict[str, int] = {}
    for e in calls:
        ab = str(e.get("approved_by") or "")
        if ab.startswith("hash:") and e.get("billable"):
            counts[ab[5:]] = counts.get(ab[5:], 0) + 1

    def mutate(m):
        ap = m.setdefault("approvals", {})
        for h, k in counts.items():
            if h not in ap:
                ap[h] = {"time": _now(), "count": k}

    try:
        res = await _to_thread(reconcile_ledger, root)
        report.new_candidates += res["candidates"]
        report.warnings += res["warnings"]
        if counts:
            await _to_thread(store.update_manifest, root, mutate)
    except Exception as e:  # 매니페스트 문제로 실행 자체를 막지는 않는다(원장이 진실)
        report.errors.append(f"시작 반영 실패: {_short(e)}")
        logger.warning(f"{_TAG} 실행 시작 매니페스트 반영 실패: {e!r}")


def _expected_state(kind: str) -> tuple[str, ...]:
    return {"new": ("none",), "recover": ("recoverable",), "retry_failed": ("failed",),
            "retry_orphan": ("orphaned",)}[kind]


async def _one_call(root: str, rid: str, e: dict, backend, state: _RunState) -> None:
    report = state.report
    cid, ck, rep, kind = e["call_id"], e["cell_key"], int(e["rep"]), e["kind"]

    def skip(why: str) -> None:
        report.skipped.append({"call_id": cid, "why": why})
        if e["billable"]:
            report.launched -= 1  # launch_block 이 예약한 발사 취소

    if e["billable"] and not str(e.get("approved_by") or ""):
        report.skipped.append({"call_id": cid, "why": "승인 정보(approved_by) 없음 — 과금하지 않음"})
        return
    if state.launch_block(e) is not None:
        report.remaining.append(cid)
        return
    if not _claim(rid, cid):
        skip("같은 호출이 이미 실행 중")
        return
    try:
        files = await _to_thread(lambda: read_ledger(root, ck).get(rep, {}))
        st = _rep_state(files, False)
        if st not in _expected_state(kind):
            skip(f"원장 상태가 계획과 다름({st}) — 다시 계획하세요")
            return
        if kind == "recover":
            made_by = next((f["backend"] for f in (files.get("resp"), files.get("req"))
                            if isinstance(f, dict) and f.get("backend")), None)
            if made_by is not None and made_by != backend.name:
                # 다른 백엔드의 URL 은 받을 수 없다(mock 이 프록시 URL 을 404 → 돈 낸 결과를 partial 로 확정, 반대는 31초 재시도)
                skip(f"응답을 받은 백엔드({made_by})와 지금 백엔드({backend.name})가 다름 — {made_by} 로 실행할 때 다운로드만 다시")
                return
            await _recover(root, e, files, backend, state)
            return
        try:
            input_paths = [store.resolve_path(root, i["file"]) for i in e["job"].get("inputs") or []]
        except (KeyError, TypeError, ValueError) as ex:
            skip(f"입력 경로 오류: {_short(ex)}")
            return
        lost = [os.path.basename(p) for p in input_paths if not os.path.isfile(p)]
        if lost:
            skip(f"입력 파일 없음({', '.join(lost)}) — Prepare 를 다시 실행하세요")
            return
        await _billable(root, e, input_paths, backend, state)
    finally:
        _release(rid, cid)


def _summary(e: dict, status: str, **kw) -> dict:
    s = {"call_id": e["call_id"], "cell_key": e["cell_key"], "rep": e["rep"], "crop_id": e.get("crop_id"),
         "tid": e.get("tid"), "variant": e.get("variant"), "kind": e["kind"], "status": status}
    s.update(kw)
    return s


async def _billable(root: str, e: dict, input_paths: list[str], backend, state: _RunState) -> None:
    report = state.report
    ck, rep = e["cell_key"], int(e["rep"])
    job = e["job"]
    try:
        begun = await _to_thread(_begin_call, root, e, backend.name)
    except Exception as ex:  # 원장을 쓸 수 없으면 호출하지 않는다(inflight 없이 과금하면 추적 불가)
        report.launched -= 1
        report.errors.append(f"{e['call_id']}: 원장 쓰기 실패 → 호출 안 함 {_short(ex)}")
        return
    prev = begun.pop("prev")
    files = dict(begun)
    t0 = time.monotonic()
    await _persist(root, ck, rep, files, "inflight", state)
    try:
        resp = await backend.request(job, input_paths, e["call_id"])
    except _interrupt_types() as ex:  # sync_op 이 요청을 취소 → 결과 없음, inflight 남김(orphaned)
        report.orphaned.append(e["call_id"])
        report.calls.append(_summary(e, "orphaned", error="인터럽트로 취소(과금됐을 수 있음)",
                                     elapsed_s=round(time.monotonic() - t0, 2)))
        state.interrupt(ex)
        await _persist(root, ck, rep, files, "orphaned", state)
        raise
    except CallError as ex:
        await _request_failed(root, e, files, ex, t0, state, prev=prev)
        return
    except Exception as ex:  # 모르는 실패: 과금됐을 수 있음으로 기록
        await _request_failed(root, e, files, CallError(_short(repr(ex)), status=None), t0, state)
        return
    elapsed_req = round(time.monotonic() - t0, 2)
    if resp.get("credits") is not None:
        state.credits.append(float(resp["credits"]))
    resp_rec = {"call_id": e["call_id"], "received": _now(), "backend": backend.name, "elapsed_s": elapsed_req,
                "credits": resp.get("credits"), "usage": resp.get("usage"),
                "data": [{"slot": i, "kind": "url" if d.get("url") else "b64" if d.get("b64") else "none",
                          "url": d.get("url")} for i, d in enumerate(resp.get("data") or [])]}
    b64 = {i: d["b64"] for i, d in enumerate(resp.get("data") or []) if d.get("b64") and not d.get("url")}
    if not resp_rec["data"]:  # 받을 것이 없음 → failed(응답 요약은 failed.json 에), 재시도는 retry_failed
        ex = CallError("응답에 이미지가 없습니다", status=None, phase="response", retryable=True, maybe_billed=True)
        await _request_failed(root, e, files, ex, t0, state, resp_rec=resp_rec)
        return
    for i, s in b64.items():  # 복구가 다시 받을 URL 이 없다 → resp.json 보다 먼저 디코드한 바이트 사본
        try:
            await _to_thread(store._atomic_write_bytes, _rawpath(root, ck, rep, i), base64.b64decode(s))
            resp_rec["data"][i]["raw"] = True
        except Exception as ex:
            report.errors.append(f"{e['call_id']}: slot {i} b64 사본 저장 실패 {_short(ex)}")
    try:
        await _to_thread(_write_json, _lpath(root, ck, rep, "resp"), resp_rec)
    except Exception as ex:  # 디스크 문제 — 그래도 바이트 저장을 시도한다
        report.errors.append(f"{e['call_id']}: resp.json 저장 실패 {_short(ex)}")
    files["resp"] = resp_rec
    await _collect(root, e, files, resp_rec, b64, resp.get("ctx"), backend, state, t0)


async def _recover(root: str, e: dict, files: dict, backend, state: _RunState) -> None:
    t0 = time.monotonic()
    resp_rec = files.get("resp")
    if not isinstance(resp_rec, dict) or "_corrupt" in resp_rec:
        ex = CallError("resp.json 이 깨져 다운로드 복구 불가", status=None, phase="download", retryable=False,
                       maybe_billed=True)
        await _download_failed(root, e, files, ex, t0, state)
        return
    await _collect(root, e, dict(files), resp_rec, {}, None, backend, state, t0, recovered=True)


async def _collect(root, e, files, resp_rec, b64, ctx, backend, state: _RunState, t0, recovered=False) -> None:
    """slot 마다 원본 바이트 확보(b64 디코드 / 복구면 b64 사본 / 다운로드) → 저장 → done(b64 사본 삭제).
    다운로드 영구 실패는 partial done."""
    report = state.report
    ck, rep = e["cell_key"], int(e["rep"])
    slots, missing, perm_err, retry_err = [], [], None, None
    for d in resp_rec.get("data") or []:
        i = int(d["slot"])
        if recovered:  # 지난 시도에서 이미 저장한 slot(원자 저장이라 있으면 완전함)
            have = await _to_thread(_existing_slot, root, ck, rep, i)
            if have is not None:
                slots.append(have)
                continue
        try:
            if i in b64:
                data = base64.b64decode(b64[i])
            elif d.get("raw"):
                data = await _to_thread(_read_raw, root, ck, rep, i)
            elif d.get("url"):
                data = await backend.download(d["url"], ctx)
            else:
                raise CallError("응답에 URL 도 b64 도 없음(복구 불가)", status=None, phase="download", retryable=False)
            slots.append(await _to_thread(_save_slot, root, ck, rep, i, data))
        except _interrupt_types() as ex:  # 응답은 resp.json 에 있음 → 다음 실행이 다운로드만 다시
            report.calls.append(_summary(e, "interrupted", error="다운로드 중 취소 — 다음 실행에서 다운로드만 다시",
                                         slots=len(slots), elapsed_s=round(time.monotonic() - t0, 2)))
            state.interrupt(ex)
            await _persist(root, ck, rep, files, "recoverable", state)
            raise
        except Exception as ex:
            if not isinstance(ex, CallError):  # 디스크 오류 등: URL·b64 사본이 있으면 다음 실행이 다시(사본도 없으면 영구)
                ex = CallError(f"slot {i} 저장 실패: {_short(repr(ex))}", status=None, phase="download",
                               retryable=bool(d.get("url") or d.get("raw")), maybe_billed=True)
            elif not d.get("url"):  # b64 바이트는 다시 읽어도 같다(이미지가 아님 = 영구 실패)
                ex.retryable = False
            if ex.retryable:  # 나머지 slot 은 계속 받아 둔다(다음 복구는 빠진 slot 만)
                retry_err = retry_err or ex
                continue
            missing.append(i)
            perm_err = perm_err or str(ex)
    if retry_err is not None:
        await _download_failed(root, e, files, retry_err, t0, state)
        return
    inflight, failed = files.get("inflight") or {}, files.get("failed") or {}
    done = {"call_id": e["call_id"], "finished": _now(), "started": inflight.get("started") or failed.get("started"),
            "elapsed_s": round(time.monotonic() - t0, 2), "credits": resp_rec.get("credits"), "slots": slots,
            "partial": bool(missing), "missing": missing, "missing_error": perm_err, "recovered": recovered}
    if recovered:  # 호출 시간 = 원래 요청 시간(복구 다운로드 시간이 아님)
        done["elapsed_s"] = resp_rec.get("elapsed_s")
    try:
        await _to_thread(_write_json, _lpath(root, ck, rep, "done"), done)
        await _to_thread(_remove, _lpath(root, ck, rep, "inflight"))
        await _to_thread(_remove, _lpath(root, ck, rep, "failed"))
        for d in resp_rec.get("data") or []:
            if d.get("raw"):
                await _to_thread(_remove, _rawpath(root, ck, rep, int(d["slot"])))
    except Exception as ex:
        report.errors.append(f"{e['call_id']}: done.json 저장 실패 {_short(ex)} — 슬롯 파일은 저장됨")
    files["done"] = done
    files.pop("inflight", None)
    files.pop("failed", None)
    report.done += 1
    report.recovered += int(recovered)
    report.calls.append(_summary(e, "done", slots=len(slots), missing=missing, credits=resp_rec.get("credits")
                                 if not recovered else None, elapsed_s=round(time.monotonic() - t0, 2),
                                 files=[s["file"] for s in slots]))
    if missing:
        report.warnings.append(f"{e['call_id']}: slot {missing} 다운로드 영구 실패({_short(perm_err, 120)}) — 받은 "
                               f"{len(slots)}장만 등록, 재과금 안 함")
    await _persist(root, ck, rep, files, "done", state)


async def _request_failed(root, e, files, ex: CallError, t0, state: _RunState, resp_rec: dict | None = None,
                          prev: dict | None = None) -> None:
    report = state.report
    ck, rep = e["cell_key"], int(e["rep"])
    elapsed = round(time.monotonic() - t0, 2)
    if ex.status in AUTH_STATUSES and ex.phase == "request":
        # 과금 전 거부 → 원장을 이 시도 전으로 되돌리고(새 호출 = 원장 없음 = pending, 재시도 = 원래 orphaned/failed 그대로 —
        # 지우면 과금됐을 수 있는 이전 시도의 기록과 보드 사전 승인 사용 기록이 사라진다) 새 발사를 멈춘다
        state.set_stop("auth")
        prev = prev or {}
        try:
            for what in ("req", "failed", "resp", "inflight"):  # 되살린 뒤 지운다 → 중간에 끊겨도 "원장 없음"이 되지 않게
                if what in prev:
                    await _to_thread(store._atomic_write_bytes, _lpath(root, ck, rep, what), prev[what])
            for what in ("inflight", "req"):
                if what not in prev:
                    await _to_thread(_remove, _lpath(root, ck, rep, what))
        except Exception as err:
            report.errors.append(f"{e['call_id']}: 원장 정리 실패 {_short(err)}")
        restored = await _to_thread(lambda: read_ledger(root, ck).get(rep, {}))
        st = _rep_state(restored, False)
        report.remaining.append(e["call_id"])
        report.launched -= 1  # 서버가 받기 전 거부 — 발사로 세지 않음
        report.calls.append(_summary(e, "pending" if st == "none" else st, error=f"HTTP {ex.status}: {_short(ex)}",
                                     elapsed_s=elapsed))

        def mutate(m):
            rec = _call_record(ck, rep, restored, st)
            if st == "none":
                rec.update(crop_id=e.get("crop_id"), tid=e.get("tid"), variant=e.get("variant"),
                           error=f"HTTP {ex.status}: {_short(ex)}")
            _upsert_call(m, rec)

        await _manifest(root, mutate, state)
        return
    failed = {"call_id": e["call_id"], "time": _now(), "started": (files.get("inflight") or {}).get("started"),
              "elapsed_s": elapsed, "error": _short(ex, 500), "status": ex.status, "retryable": bool(ex.retryable),
              "maybe_billed": bool(ex.maybe_billed), "phase": ex.phase, "resp": resp_rec}
    try:
        await _to_thread(_write_json, _lpath(root, ck, rep, "failed"), failed)
        await _to_thread(_remove, _lpath(root, ck, rep, "inflight"))
    except Exception as err:
        report.errors.append(f"{e['call_id']}: failed.json 저장 실패 {_short(err)}")
    files["failed"] = failed
    files.pop("inflight", None)
    report.failed += 1
    report.calls.append(_summary(e, "failed", error=f"{'HTTP ' + str(ex.status) + ': ' if ex.status else ''}{_short(ex)}"
                                 + (" (과금됐을 수 있음)" if ex.maybe_billed else ""), elapsed_s=elapsed))
    await _persist(root, ck, rep, files, _rep_state(files, False), state)


async def _download_failed(root, e, files, ex: CallError, t0, state: _RunState) -> None:
    """응답은 받았는데 다운로드가 (아직) 안 됨 → failed(phase=download). retryable 이면 다음 실행이 다운로드만 다시."""
    report = state.report
    ck, rep = e["cell_key"], int(e["rep"])
    failed = {"call_id": e["call_id"], "time": _now(), "started": (files.get("inflight") or {}).get("started"),
              "elapsed_s": round(time.monotonic() - t0, 2), "error": _short(ex, 500), "status": ex.status,
              "retryable": bool(ex.retryable), "maybe_billed": True, "phase": "download"}
    try:
        await _to_thread(_write_json, _lpath(root, ck, rep, "failed"), failed)
        await _to_thread(_remove, _lpath(root, ck, rep, "inflight"))
    except Exception as err:
        report.errors.append(f"{e['call_id']}: failed.json 저장 실패 {_short(err)}")
    files["failed"] = failed
    files.pop("inflight", None)
    report.failed += 1
    report.calls.append(_summary(e, "failed", error=f"다운로드 실패: {_short(ex)}"
                                 + (" — 다음 실행에서 다운로드만 다시(과금 없음)" if ex.retryable else ""),
                                 elapsed_s=round(time.monotonic() - t0, 2)))
    await _persist(root, ck, rep, files, _rep_state(files, False), state)


async def _persist(root: str, ck: str, rep: int, files: dict, st: str, state: _RunState) -> None:
    def work():
        return _apply_result(root, ck, rep, files, st)

    try:
        keys, warns = await _to_thread(work)
        state.report.new_candidates += keys
        state.report.warnings += warns
    except Exception as e:  # 원장은 이미 저장됨 → 다음 실행의 reconcile_ledger 가 반영
        state.report.errors.append(f"{call_id_for(ck, rep)}: 매니페스트 반영 실패 {_short(e)} — 다음 실행에서 원장으로 복구")
        logger.warning(f"{_TAG} 매니페스트 반영 실패({call_id_for(ck, rep)}): {e!r}")


async def _manifest(root: str, mutate, state: _RunState) -> None:
    try:
        await _to_thread(store.update_manifest, root, mutate)
    except Exception as e:
        state.report.errors.append(f"매니페스트 반영 실패 {_short(e)}")
