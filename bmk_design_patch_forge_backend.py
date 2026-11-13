"""Design Patch Studio — Forge 생성 백엔드(서버 모드 F2). bmk_design_patch_runner 의 백엔드 계약(name · check() ·
async request(job, input_paths, call_id) · async download(url, ctx))을 Forge 잡(제출 → 폴링 → 내려받기)으로 구현한다.

로컬판(ComfyUI 안)은 이 모듈을 import 하지 않는다 — runner.make_backend 가 서버 모드 + forge 일 때만 늦게 부른다.
유료 실행 함수(runner._billable 등)의 본문은 바꾸지 않는다. Forge 고유 기록은 원장 파일이 아니라 사이드카에 둔다:

  cands/<cell_key>/r<rep>.forge.json   {"schema": "dp-forge/1", "requests": [{"id", "attempt", "state", "job_id"?, "time"}…],
                                         "job_id", "result_ids", "prompt_changed": [슬롯별 bool]}
  cands/_forge.json                    {"schema": "dp-forge-project/1", "nonce": 12hex}   (프로젝트마다 한 번, 배타 생성)

원장 resp.json 의 data[].url 에는 실제 주소 대신 불투명 주소 `forge-job:<job_id>/<slot>` 만 적힌다. 제출 응답을 받으면 폴링
전에 resp.json(영수증)을 써서, 그 뒤 무엇으로 끊겨도 기존 「다운로드만 다시」(recover)가 download() 로 이어받게 한다.
토큰·조직 이름·Forge 주소는 디스크·로그·예외 문구에 싣지 않는다. request()·download() 는 runner.CallError 와 인터럽트
말고는 어떤 예외도 내보내지 않는다.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import math
import os
import re
import secrets
import tempfile
import threading
import time
from datetime import datetime

from PIL import Image

try:
    from . import bmk_design_patch_runner as runner
    from .bmk_design_patch_forge import ForgeError, MEDIA_LIMIT
except ImportError:
    import bmk_design_patch_runner as runner
    from bmk_design_patch_forge import ForgeError, MEDIA_LIMIT

CHECKPOINTS = {
    "gpt-image-2.5-sunburst": "gpt_image_25_sunburst",
    "gpt-image-2.5-flare": "gpt_image_25_flare",
    "gpt-image-2.6-sunburst": "gpt_image_26_sunburst",
    "gpt-image-2.6-flare": "gpt_image_26_flare",
}
MAX_INPUTS = 16
URL_SCHEME = "forge-job:"
SIDECAR_SCHEMA = "dp-forge/1"
POLL_FAST, POLL_SLOW, POLL_SLOW_AFTER = 3.0, 5.0, 30.0
DEADLINE_GRACE, DEADLINE_DEFAULT = 150.0, 1500.0
AUTH_WAIT = 90.0
SUBMIT_RETRY_DELAY = 0.25
_CALL_ID = re.compile(r"([0-9a-f]{24})_r(\d+)")
_JOB_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
_REQUEST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_PRIVATE_TEXT = re.compile(r"://|data:|\\|[a-z0-9_.-]+:\d+|(?:\d{1,3}\.){3}\d{1,3}|::|(?:[0-9a-f]{1,4}:){7}[0-9a-f]{1,4}", re.I)
_LOCK = threading.Lock()
_PROGRESS = {}


def blocked_reason(job: dict) -> str | None:
    """forge 백엔드의 과금 전 판정 몫 — 매핑 표에 없는 모델 · 프롬프트의 `@@` · 입력 MAX_INPUTS 초과. 없으면 None."""
    if job.get("model") not in CHECKPOINTS:
        return "Forge 에서 지원하지 않는 생성 모델입니다."
    if "@@" in str(job.get("prompt") or ""):
        return "프롬프트의 @@ 표기를 지운 뒤 다시 시도해 주세요."
    if len(job.get("inputs") or []) > MAX_INPUTS:
        return "입력 이미지는 16장까지 보낼 수 있습니다."
    return None


def encode_inputs(input_paths: list[str]) -> list[bytes]:
    """입력 파일 → PNG 바이트(순서 유지). RGB 변환 → 총 픽셀이 2048² 를 넘을 때만 축소. torch 를 쓰지 않는다."""
    images = []
    for path in input_paths:
        with Image.open(path) as source:
            im = source.convert("RGB")
        if im.width * im.height > runner.MAX_INPUT_PIXELS:
            scale = math.sqrt(runner.MAX_INPUT_PIXELS / (im.width * im.height))
            im = im.resize((max(1, int(im.width * scale)), max(1, int(im.height * scale))), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "PNG")
        images.append(buf.getvalue())
    return images


def build_request(job: dict, images: list[bytes], *, organization_id: str, request_id: str, prompt_autopilot) -> dict:
    """Forge POST /api/image/generate 본문(fixture forge_api.json 의 generate.request 모양 — 그 밖의 키를 넣지 않는다)."""
    body = {"checkpoint": CHECKPOINTS[job["model"]], "prompt": job["prompt"], "quality": job["quality"],
            "size": f"{job['size'][0]}x{job['size'][1]}", "background": job["background"],
            "num_outputs": job["n"], "moderation": "low",
            "input_images": [["data:image/png;base64," + base64.b64encode(data).decode("ascii") for data in images]],
            "organization_id": organization_id, "request_id": request_id, "prompt_autopilot": prompt_autopilot}
    if job.get("complexity") is not None:
        body["complexity"] = job["complexity"]
    return body


def read_sidecar(root: str, cell_key: str, rep: int) -> dict:
    """파일이 없을 때만 빈 기록. 읽기 실패·손상은 제출 전에 막을 수 있게 올린다."""
    try:
        with open(runner._lpath(root, cell_key, rep, "forge"), encoding="utf-8") as stream:
            value = json.load(stream)
    except FileNotFoundError:
        return {"schema": SIDECAR_SCHEMA, "requests": []}
    if not isinstance(value, dict) or value.get("schema") != SIDECAR_SCHEMA or not isinstance(value.get("requests"), list):
        raise ValueError("Forge 제출 기록의 형식이 잘못되었습니다.")
    for row in value["requests"]:
        if (not isinstance(row, dict) or not isinstance(row.get("id"), str) or not _REQUEST_ID.fullmatch(row["id"])
                or type(row.get("attempt")) is not int or row["attempt"] < 1
                or row.get("state") not in ("open", "submitted", "failed", "rejected")
                or _timestamp(row.get("time")) is None
                or (("job_id" in row or row.get("state") == "submitted") and
                    (not isinstance(row.get("job_id"), str) or not _JOB_ID.fullmatch(row["job_id"])))):
            raise ValueError("Forge 제출 기록의 형식이 잘못되었습니다.")
    if ("job_id" in value and (not isinstance(value["job_id"], str) or not _JOB_ID.fullmatch(value["job_id"]))
            or "result_ids" in value and (not isinstance(value["result_ids"], list) or
                                         not all(isinstance(item, str) for item in value["result_ids"]))
            or "prompt_changed" in value and (not isinstance(value["prompt_changed"], list) or
                                             not all(type(item) is bool for item in value["prompt_changed"]))):
        raise ValueError("Forge 결과 기록의 형식이 잘못되었습니다.")
    return value


def prompt_changed(root: str, call_id: str) -> list[bool]:
    """그 호출의 슬롯별 '서버가 프롬프트를 바꿈' 표지(보드 후보 응답이 읽는다). 기록이 없으면 []."""
    match = _CALL_ID.fullmatch(call_id or "")
    if match is None:
        return []
    try:
        flags = read_sidecar(root, match[1], int(match[2])).get("prompt_changed")
    except (OSError, ValueError, TypeError):
        return []
    return [flag is True for flag in flags] if isinstance(flags, list) else []


def progress(root: str) -> dict | None:
    """보드 GET pipeline/progress 의 forge 값 → {"queue": {"position", "total"} | None, "auth_wait": bool}."""
    rid = runner._rid(root)
    with _LOCK:
        rows = [v.copy() for (project, _), v in _PROGRESS.items() if project == rid]
    if not rows:
        return None
    queues = [row["queue"] for row in rows if row.get("queue") is not None]
    return {"queue": min(queues, key=lambda q: q["position"]) if queues else None,
            "auth_wait": any(row.get("auth_wait") for row in rows)}


def _nonce(root):
    path = runner.store.resolve_path(root, "cands/_forge.json")
    with _LOCK:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            with open(path, encoding="utf-8") as stream:
                record = json.load(stream)
        except FileNotFoundError:
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", delete=False,
                                                 dir=os.path.dirname(path), prefix="._forge-", suffix=".tmp") as stream:
                    temporary = stream.name
                    json.dump({"schema": "dp-forge-project/1", "nonce": secrets.token_hex(6)}, stream)
                    stream.flush()
                    os.fsync(stream.fileno())
                try:
                    os.link(temporary, path)  # 완성된 파일만 배타 공개. 다른 생성자가 이겼다면 그 값을 읽는다.
                except FileExistsError:
                    pass
            finally:
                if temporary is not None:
                    os.unlink(temporary)
            with open(path, encoding="utf-8") as stream:
                record = json.load(stream)
        if (not isinstance(record, dict) or record.get("schema") != "dp-forge-project/1"
                or not isinstance(record.get("nonce"), str) or not re.fullmatch(r"[0-9a-f]{12}", record["nonce"])):
            raise ValueError("프로젝트 식별 기록을 읽을 수 없습니다")
        return record["nonce"]


def _timestamp(value):
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None


def _safe_message(value, fallback):
    """원격 오류는 자르기 전에 주소·경로 꼴을 검사한다. 일반 안내는 300자까지 그대로."""
    if not isinstance(value, str) or not value.strip() or _PRIVATE_TEXT.search(value):
        return fallback
    return value[:300]


class ForgeBackend:
    name = "forge"

    def __init__(self, ctx: dict):
        """ctx = runner.forge_context() — {"client", "token_of", "reject_token", "organization_id", "root", "stop", "options"}."""
        self._ctx = ctx
        self._root = ctx["root"]
        self._semaphore = None
        self._recoveries = {}

    def __repr__(self) -> str:
        return "ForgeBackend()"

    def check(self) -> None:
        if not callable(self._ctx.get("token_of")) or not callable(self._ctx.get("reject_token")):
            raise runner.CallError("Forge 연결 설정을 확인해 주세요.", phase="blocked", retryable=False, maybe_billed=False)

    async def request(self, job: dict, input_paths: list[str], call_id: str) -> dict:
        """→ {"data": [{"url": "forge-job:<job_id>/<slot>", "b64": None}…], "credits": None, "usage": None, "ctx": {…}}."""
        phase, sent = "blocked", False
        try:
            why = blocked_reason(job)
            if why or len(input_paths) > MAX_INPUTS:
                raise runner.CallError(why or "입력 이미지는 16장까지 보낼 수 있습니다.", phase="blocked",
                                       retryable=False, maybe_billed=False)
            phase = "request"
            if self._ctx["token_of"]() is None:
                raise runner.CallError("Forge 로그인 정보를 다시 받아야 합니다.", status=401, maybe_billed=False)
            if self._semaphore is None:
                self._semaphore = asyncio.Semaphore(max(1, int(self._ctx["options"]["concurrency"])))
            async with self._semaphore:
                self._stop()
                match = _CALL_ID.fullmatch(call_id)
                if match is None:
                    raise runner.CallError("생성 호출 식별자가 잘못되었습니다.", phase="blocked",
                                           retryable=False, maybe_billed=False)
                ck, rep = match[1], int(match[2])
                req = runner._read_json(runner._lpath(self._root, ck, rep, "req")) or {}
                attempt = int(req.get("attempt", 1))
                phase = "blocked"
                side = read_sidecar(self._root, ck, rep)
                last = side["requests"][-1] if side["requests"] else None
                if not last or last.get("state") not in ("open", "submitted"):
                    last = {"id": f"dp-{_nonce(self._root)}-{call_id}-a{attempt}", "attempt": attempt,
                            "state": "open", "time": runner._now()}
                    side["requests"].append(last)
                    for key in ("job_id", "result_ids", "prompt_changed"):
                        side.pop(key, None)
                self._save(ck, rep, side)
                phase = "encode"
                images = await asyncio.to_thread(encode_inputs, input_paths)
                body = build_request(job, images, organization_id=self._ctx["organization_id"], request_id=last["id"],
                                     prompt_autopilot=self._ctx["options"]["prompt_autopilot"])
                phase, sent = "request", True
                t0 = time.monotonic()
                job_id = await self._submit(body, ck, rep, side)
                phase = "poll"
                last.update(state="submitted", job_id=job_id, time=runner._now())
                side["job_id"] = job_id
                side_error = None
                try:
                    self._save(ck, rep, side)
                except BaseException as exc:
                    side_error = exc
                runner._write_json(runner._lpath(self._root, ck, rep, "resp"), {
                    "call_id": call_id, "received": runner._now(), "backend": self.name,
                    "elapsed_s": round(time.monotonic() - t0, 2), "credits": None, "usage": None,
                    "data": [{"slot": i, "kind": "url", "url": f"{URL_SCHEME}{job_id}/{i}"} for i in range(job["n"])]})
                if side_error is not None:
                    raise side_error
                result = await self._poll(job_id, call_id, _timestamp(last["time"]))
                phase = "response"
                self._result(ck, rep, side, result, job["prompt"], recovery=False)
                return {"data": [{"url": f"{URL_SCHEME}{job_id}/{i}", "b64": None} for i in range(len(result["images"]))],
                        "credits": None, "usage": None, "ctx": {"job_id": job_id, "images": result["images"]}}
        except (runner.CallError, *runner._interrupt_types()):
            raise
        except BaseException:
            raise runner.CallError("Forge 생성 요청을 처리하지 못했습니다.", phase=phase,
                                   retryable=sent, maybe_billed=sent) from None
        finally:
            self._clear_progress(call_id)

    async def download(self, url: str, ctx=None) -> bytes:
        try:
            match = re.fullmatch(r"forge-job:([A-Za-z0-9_-]{1,128})/(\d+)", url or "")
            if match is None:
                raise runner.CallError("결과 식별자가 잘못되었습니다.", phase="download", retryable=False, maybe_billed=True)
            job_id, slot = match[1], int(match[2])
            self._stop()
            if isinstance(ctx, dict) and ctx.get("job_id") == job_id and isinstance(ctx.get("images"), list):
                images = ctx["images"]
            else:
                key = (asyncio.get_running_loop(), job_id)
                if key not in self._recoveries:
                    self._recoveries[key] = asyncio.create_task(self._recover_job(job_id))
                images = await self._recoveries[key]
            if slot >= len(images) or not isinstance(images[slot], str) or not images[slot]:
                raise runner.CallError("이 슬롯의 결과 이미지가 없습니다.", phase="download", retryable=False, maybe_billed=True)
            try:
                data = await self._ctx["client"].fetch_media(images[slot], limit=MEDIA_LIMIT)
            except ForgeError as exc:
                raise runner.CallError("Forge 결과 이미지를 받지 못했습니다.", status=exc.status, phase="download",
                                       retryable=exc.status != 413, maybe_billed=True) from None
            if not isinstance(data, bytes) or len(data) > MEDIA_LIMIT or runner._sniff_ext(data) is None:
                raise runner.CallError("결과 이미지의 형식 또는 크기가 잘못되었습니다.", phase="download",
                                       retryable=False, maybe_billed=True)
            return data
        except runner._interrupt_types():
            raise
        except runner.CallError as exc:
            if exc.phase == "download":
                raise
            raise runner.CallError(str(exc), status=exc.status, phase="download", retryable=exc.retryable,
                                   maybe_billed=True) from None
        except BaseException:
            raise runner.CallError("Forge 결과 이미지를 받지 못했습니다.", phase="download",
                                   retryable=True, maybe_billed=True) from None

    def _stop(self):
        if self._ctx["stop"]():
            raise runner._make_interrupt()

    def _save(self, ck, rep, side):
        runner._write_json(runner._lpath(self._root, ck, rep, "forge"), side)

    def _set_progress(self, call_id, *, queue=None, auth_wait=False):
        with _LOCK:
            _PROGRESS[(runner._rid(self._root), call_id)] = {"queue": queue, "auth_wait": auth_wait}

    def _clear_progress(self, call_id):
        with _LOCK:
            _PROGRESS.pop((runner._rid(self._root), call_id), None)

    async def _submit(self, body, ck, rep, side):
        response_lost = False
        for attempt in range(3):
            self._stop()
            token = self._ctx["token_of"]()
            if token is None:
                if response_lost:
                    break
                raise runner.CallError("Forge 로그인 정보를 다시 받아야 합니다.", status=401, maybe_billed=False)
            try:
                result = await self._ctx["client"].submit(token, body)
                job_id = result.get("job_id") if isinstance(result, dict) else None
                if isinstance(job_id, str) and _JOB_ID.fullmatch(job_id):
                    return job_id
            except runner._interrupt_types():
                raise
            except ForgeError as exc:
                if exc.reason == "unauthorized" or exc.status == 401:
                    self._ctx["reject_token"](token)
                    if response_lost:
                        break
                    raise runner.CallError("Forge 로그인 정보를 다시 받아야 합니다.", status=401, maybe_billed=False) from None
                if response_lost and exc.status != 422 and (exc.reason == "forbidden" or
                        exc.status is not None and 400 <= exc.status < 500):
                    break
                if exc.reason == "forbidden" or exc.status == 403:
                    raise runner.CallError("Design Patch Studio 사용 권한이 없습니다. 담당자에게 문의해 주세요.",
                                           status=403, retryable=False, maybe_billed=False) from None
                if exc.status == 422:
                    side["requests"][-1]["state"] = "rejected"
                    self._save(ck, rep, side)
                    raise runner.CallError(_safe_message(exc.detail, "생성 요청이 거절되었습니다."), status=422,
                                           retryable=False, maybe_billed=False) from None
                if exc.status is not None and 400 <= exc.status < 500:
                    raise runner.CallError(_safe_message(exc.detail, "Forge 생성 요청을 받지 못했습니다."), status=exc.status,
                                           maybe_billed=False) from None
            except runner.CallError:
                raise
            except Exception:
                pass  # 연결·해석 실패: 예외 본문은 버리고 같은 멱등 키로 다시 제출한다.
            response_lost = True
            if attempt < 2:
                await asyncio.sleep(SUBMIT_RETRY_DELAY)
        raise runner.CallError("Forge 생성 요청의 응답을 받지 못했습니다.", phase="request",
                               retryable=True, maybe_billed=True)

    async def _fresh_token(self, call_id, rejected=None):
        started = time.monotonic()
        while True:
            self._stop()
            token = self._ctx["token_of"]()
            if token is not None and token != rejected:
                self._set_progress(call_id)
                return token
            self._set_progress(call_id, auth_wait=True)
            left = AUTH_WAIT - (time.monotonic() - started)
            if left <= 0:
                raise runner.CallError("Forge 로그인 갱신을 기다리다 중단됐습니다.", status=401, phase="poll",
                                       retryable=True, maybe_billed=True)
            await asyncio.sleep(min(POLL_FAST, left))

    async def _poll(self, job_id, call_id, submitted=None, *, recovery=False):
        started = time.monotonic()
        deadline = (submitted if submitted is not None else time.time()) + DEADLINE_DEFAULT
        token = await self._fresh_token(call_id)
        while True:
            self._stop()
            try:
                token = self._ctx["token_of"]() or await self._fresh_token(call_id)
                result = await self._ctx["client"].job(token, job_id)
            except ForgeError as exc:
                if exc.reason == "unauthorized" or exc.status == 401:
                    self._ctx["reject_token"](token)
                    token = await self._fresh_token(call_id, token)
                    continue
                if exc.status != 413:
                    expired = recovery and exc.status == 404 and submitted is not None and time.time() - submitted >= 3600
                    raise runner.CallError("Forge 생성 결과를 조회하지 못했습니다.", status=exc.status,
                                           phase="download" if recovery else "poll", retryable=not expired,
                                           maybe_billed=True) from None
                result = {}  # 크기 상한 초과는 다음 주기에 재조회한다(D425).
            if not isinstance(result, dict):
                raise runner.CallError("Forge 생성 응답을 읽지 못했습니다.", phase="poll", retryable=True, maybe_billed=True)
            if result.get("status") in ("completed", "failed"):
                return result
            until = _timestamp(result.get("execution_deadline_at"))
            if until is not None:
                deadline = until + DEADLINE_GRACE
            position, total = result.get("queue_position"), result.get("queue_total")
            self._set_progress(call_id, queue={"position": position, "total": total} if type(position) is int else None)
            left = deadline - time.time()
            if left <= 0:
                raise runner.CallError("Forge 생성 결과를 기다리는 시간이 지났습니다.", phase="poll",
                                       retryable=True, maybe_billed=True)
            interval = POLL_SLOW if time.monotonic() - started >= POLL_SLOW_AFTER else POLL_FAST
            await asyncio.sleep(min(interval, left))

    def _result(self, ck, rep, side, result, prompt, *, recovery):
        images = result.get("images")
        if result.get("status") == "failed" or not isinstance(images, list) or not images:
            if side["requests"]:
                side["requests"][-1]["state"] = "failed"
            self._save(ck, rep, side)
            runner._remove(runner._lpath(self._root, ck, rep, "resp"))
            user = result.get("status") == "failed" and result.get("error_category") == "user"
            message = result.get("error_message")
            raise runner.CallError(_safe_message(message, "Forge 생성 결과에 이미지가 없습니다."),
                                   phase="download" if recovery else "response", retryable=recovery or not user,
                                   maybe_billed=recovery or not user)
        metadata = result.get("metadata") or []
        side["prompt_changed"] = [isinstance(item, dict) and isinstance(item.get("prompt_used"), str)
                                  and item["prompt_used"] != prompt for item in
                                  [metadata[i] if i < len(metadata) else {} for i in range(len(images))]]
        side["result_ids"] = [value for value in result.get("result_ids") or []
                              if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9._:-]{1,256}", value)]
        self._save(ck, rep, side)

    async def _recover_job(self, job_id):
        call_id = None
        try:
            for ck in runner._ledger_cells(self._root):
                reps = {int(match[1]) for name in os.listdir(runner._ldir(self._root, ck))
                        if (match := re.fullmatch(r"r(\d+)\.(?:forge|resp)\.json", name))}
                for rep in sorted(reps):
                    side = read_sidecar(self._root, ck, rep)
                    if side.get("job_id") == job_id and side["requests"]:
                        submitted = _timestamp(side["requests"][-1].get("time"))
                    else:
                        receipt = runner._read_json(runner._lpath(self._root, ck, rep, "resp")) or {}
                        prefix = f"{URL_SCHEME}{job_id}/"
                        if receipt.get("backend") != self.name or not any(
                                isinstance(row, dict) and isinstance(row.get("url"), str) and row["url"].startswith(prefix)
                                for row in receipt.get("data") or []):
                            continue
                        submitted = _timestamp(receipt.get("received"))
                        side["job_id"] = job_id
                        if side["requests"]:
                            side["requests"][-1].update(state="submitted", job_id=job_id, time=receipt.get("received"))
                    call_id = runner.call_id_for(ck, rep)
                    result = await self._poll(job_id, call_id, submitted, recovery=True)
                    req = runner._read_json(runner._lpath(self._root, ck, rep, "req")) or {}
                    self._result(ck, rep, side, result, req.get("prompt"), recovery=True)
                    return result["images"]
            raise runner.CallError("Forge 결과의 제출 기록을 찾지 못했습니다.", phase="download", retryable=True, maybe_billed=True)
        finally:
            if call_id is not None:
                self._clear_progress(call_id)
