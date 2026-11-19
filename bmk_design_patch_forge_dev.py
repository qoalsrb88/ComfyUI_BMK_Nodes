"""Design Patch Studio — 개발용 가짜 Forge(서버 모드 F2 의 개발·시험 전용). 운영에서는 쓰지 않는다:
bmk_design_patch_server 가 --dev-handoff + --dev-parent-port 로 띄운 가짜 부모(8790)에만 붙는다.

FakeForge.handle 은 소켓을 쓰지 않는다 — bmk_design_patch_forge.ForgeClient(transport=fake.handle) 로 끼우면 시험이
네트워크 없이 돌고, mount() 가 같은 구현을 8790 의 HTTP 라우트로 내놓는다. 응답 모양 정본 = tools/fixtures/forge_api.json.

토큰: `dev:<계정>`(만료 없음) · `dev:<계정>:<만료 epoch 초>`. 사용자 id = `u_<계정>`, 조직 = org1(이름 「개발 조직」).
시나리오는 프롬프트 속 표지로 건다(여러 개 가능): [[fake:delay=초]] [[fake:queue=순번/전체]] [[fake:partial=장수]]
[[fake:fail=user|ailabs]] [[fake:changed]] [[fake:empty]] — 표지가 없으면 곧바로 completed.
퇴역 모델은 BMK_DP_FAKE_RETIRED 환경 변수에 checkpoint 를 쉼표로 나열하거나 fake.retire(checkpoint, True/False) 로 바꾼다.
fake.requests 는 최근 요청의 키·checkpoint·입력 인덱스·공급자 호출 인덱스만 기록한다(토큰·입력 본문 없음).
"""

from __future__ import annotations

import asyncio
import base64
from collections import OrderedDict, deque
import copy
from datetime import datetime, timezone
import hashlib
import html
import io
import json
import math
import os
from pathlib import Path
import re
import secrets
import threading
import time
from urllib.parse import parse_qs, urlencode, urlsplit
import uuid

from PIL import Image, ImageDraw


_ACCOUNT = re.compile(r"[a-z0-9_-]{1,32}")
_TOKEN = re.compile(r"dev:([a-z0-9_-]{1,32})(?::([0-9]+))?")
_NAMESPACE = uuid.UUID("03e4c283-d946-5aa4-bd83-22dcd16574ce")
_BODY_LIMIT = 64 * 1024 * 1024
_IMAGE_TYPES = {"image/png": "PNG", "image/jpeg": "JPEG", "image/webp": "WEBP"}
_MAX_JOBS = 200
_MAX_UPLOADS = 16
_MAX_CALLS = 1000
_MAX_RENDERED = 32
_MAX_RENDERED_BYTES = 64 * 1024 * 1024


class _Validation(ValueError):
    pass


def _json(status, value):
    return status, {"Content-Type": "application/json", "Cache-Control": "no-store"}, json.dumps(
        value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")


def _stamp(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def _picture(data):
    with Image.open(io.BytesIO(data)) as picture:
        if picture.width * picture.height > 16 * 1024 * 1024:
            raise ValueError
        picture.load()
        return picture.convert("RGB"), picture.format


def _input_source(groups, *, size_out=None):
    """모든 입력의 실제 형식을 검사하고, 첫 그림만 작은 PNG로 보관한다. 작업 스레드에서 호출한다."""
    first = None
    for group in groups:
        for value in group:
            if not isinstance(value, str):
                raise _Validation("input_images")
            header, separator, encoded = value.partition(",")
            content_type = header.removeprefix("data:").removesuffix(";base64")
            if not separator or content_type not in _IMAGE_TYPES or header != f"data:{content_type};base64":
                raise _Validation("input_images")
            try:
                picture, image_format = _picture(base64.b64decode(encoded, validate=True))
                with picture:
                    if image_format != _IMAGE_TYPES[content_type]:
                        raise ValueError
                    if first is None:
                        if size_out is not None:
                            size_out[:] = picture.size
                        picture.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
                        picture.info.clear()
                        output = io.BytesIO()
                        picture.save(output, format="PNG")
                        first = output.getvalue()
            except Exception:
                raise _Validation("input_images") from None
    return first


def _png(size, label, seed, source=None):
    tint = tuple(hashlib.sha256(seed.encode("utf-8")).digest()[:3])
    picture = source.resize(size, Image.Resampling.LANCZOS) if source is not None else Image.new("RGB", size, tint)
    if source is not None:
        picture = Image.blend(picture, Image.new("RGB", size, tint), 0.18)
    draw = ImageDraw.Draw(picture)
    draw.rectangle((0, 0, min(size[0] - 1, 210), min(size[1] - 1, 23)), fill=(24, 24, 24))
    draw.text((6, 5), label, fill=(255, 255, 255))
    output = io.BytesIO()
    picture.save(output, format="PNG")
    return output.getvalue()


def issue_token(account: str, ttl: float | None = None, *, clock=time.time) -> str:
    """개발 토큰. ttl 이 None 이면 만료 없는 `dev:<계정>`."""
    if not isinstance(account, str) or _ACCOUNT.fullmatch(account) is None:
        raise ValueError("개발 계정 이름 형식이 잘못되었습니다")
    if ttl is None:
        return "dev:" + account
    if type(ttl) not in (int, float) or not math.isfinite(ttl):
        raise ValueError("개발 토큰 수명이 잘못되었습니다")
    expiry = clock() + ttl
    if not math.isfinite(expiry) or expiry < 0:
        raise ValueError("개발 토큰 만료 시각이 잘못되었습니다")
    return f"dev:{account}:{int(expiry)}"


class FakeForge:
    def __init__(self, *, media_origin: str = "http://localhost:8790", clock=time.time):
        self.media_origin = media_origin.rstrip("/")
        self._clock = clock
        self._offset = 0.0
        self._lock = threading.RLock()
        self._fixture = json.loads((Path(__file__).parent / "tools" / "fixtures" / "forge_api.json").read_text(encoding="utf-8"))
        self.calls = deque(maxlen=_MAX_CALLS)
        self.requests = deque(maxlen=_MAX_CALLS)
        self._retired = set()
        for checkpoint in os.environ.get("BMK_DP_FAKE_RETIRED", "").split(","):
            if checkpoint.strip():
                self.retire(checkpoint.strip())
        self.query_calls = deque(maxlen=_MAX_CALLS)
        self.fault = None  # 전송 대역 전용: {path, times, status?, delay?, query?}.
        self._history_seed = {}
        self.jobs = {}
        self._states = {}
        self._assets = {}
        self._media = {}
        self._uploads = {}
        self._created = {}
        self._rendered = OrderedDict()
        self._rendered_bytes = 0

    def _now(self):
        return self._clock() + self._offset

    def advance(self, seconds):
        """대기 없이 개발 시계를 앞으로 옮긴다. 상태는 다음 조회에 반영된다."""
        if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds < 0:
            raise ValueError("개발 시계 간격이 잘못되었습니다")
        with self._lock:
            self._offset += seconds

    def _error(self, name):
        error = self._fixture["errors"][name]
        return _json(error["status"], error["response"])

    def retire(self, checkpoint, retired=True):
        """목록에서만 빼거나 되살린다. 퇴역 모델 제출은 대체 모델로 접수되며 requests 에 남는다."""
        catalog = self._fixture["models"]["catalog"]["response"]
        if type(retired) is not bool or checkpoint not in [row["checkpoint"] for row in catalog["models"]]:
            raise ValueError("개발 퇴역 모델 설정이 잘못되었습니다")
        with self._lock:
            if retired:
                self._retired.add(checkpoint)
            else:
                self._retired.discard(checkpoint)

    def _catalog(self):
        result = copy.deepcopy(self._fixture["models"]["catalog"]["response"])
        result["models"] = [row for row in result["models"] if row["checkpoint"] not in self._retired]
        result["count"] = len(result["models"])
        return result

    def _model_input(self, name, spec, data, groups):
        """스튜디오의 요청 계약을 검사하되 입력 오류도 접수된 잡의 실패로 돌려준다."""
        limits = self._fixture["models"]["limits"][name]
        errors = spec["errors"]["input"]
        kind = "gemini" if limits.get("family") == "nano-banana" else "ideogram" if "ideogram" in spec["request"] else "seedream"
        inputs = [value for group in groups for value in group]
        used = inputs[:limits["max_inputs"]] if kind == "seedream" else inputs
        size = [1024, 1024]
        error = None
        forbidden = any(key in data for key in spec["never"])
        if kind == "gemini":
            gemini = self._fixture["models"]["limits"]["gemini"]
            ratio = data.get("aspect_ratio")
            # Forge는 잘못된 비율·해상도를 말없이 바꾸지만, 가짜는 스튜디오의 잘못된 요청을 잡으려고 실패시킨다.
            if (forbidden or not isinstance(ratio, str) or ratio not in gemini["ratios"]
                    or any(not isinstance(data.get(key), str) or data[key] not in choices
                           for key, choices in limits["opts"].items())
                    or len(inputs) > limits["max_inputs"]):
                error = "생성 요청이 올바르지 않습니다."
            else:
                scale = gemini["image_sizes"][data["image_size"]]
                size = [side * scale for side in gemini["ratio_sizes"][ratio]]
        elif kind == "seedream":
            if "background" in data:
                error = errors[1] + "."
            else:
                value = data.get("image_size")
                valid = isinstance(value, str) and re.fullmatch(r"[0-9]{1,7}x[0-9]{1,7}", value) is not None
                if valid:
                    size = list(map(int, value.split("x")))
                lo, hi = limits["area"]
                if (forbidden or not valid or min(size) <= 0 or not lo <= size[0] * size[1] <= hi
                        or max(size) > limits["max_ratio"] * min(size)):
                    error = errors[0] + f" WxH 형식, {lo}~{hi}화소, 긴 변은 짧은 변의 {limits['max_ratio']}배 이하여야 합니다."
            if not data["prompt"].strip():
                error = errors[2] + "."
        else:
            options = data.get("ideogram", {})
            if (forbidden or not isinstance(options, dict) or set(options) - set(spec["ideogram_keys"])
                    or options.get("quality", limits["default_quality"]) not in ("very_low", *limits["qualities"])):
                error = errors[3] + " — 옵션이 올바르지 않습니다."
            elif len(inputs) > limits["max_inputs"]:
                error = errors[1] + "까지 보낼 수 있습니다."
            elif not 1 <= data.get("num_outputs", 1) <= limits["max_n"]:
                error = errors[2] + "."
            elif not data["prompt"].strip() or len(data["prompt"]) > 10000:
                error = errors[0] + "."
        first = None
        if error is None:
            original_size = []
            try:
                first = _input_source([used], size_out=original_size)
            except _Validation:
                if kind != "ideogram":
                    raise
                error = errors[5] + "."
            if kind == "ideogram" and original_size:
                size = original_size
        failure = None
        if error is not None:
            failure = dict(self._fixture["job"]["failed_ailabs"], error_message=kind + ": " + error)
        return kind, first, tuple(size), failure, list(range(len(used))) if failure is None else []

    def _validate_id(self, value, field):
        rules = self._fixture["generate"]["request_rules"]
        if (not isinstance(value, str) or not 1 <= len(value) <= rules["request_id_max"]
                or re.fullmatch(rules["request_id_pattern"], value) is None):
            raise _Validation(field)

    @staticmethod
    def _org(account, organization):
        return organization == "org1" and not account.startswith("outsider")

    @staticmethod
    def _organization(organization):
        if (not isinstance(organization, str) or not 1 <= len(organization) <= 128
                or any(character.isspace() or ord(character) < 32 for character in organization)):
            raise _Validation("organization_id")
        return organization

    @staticmethod
    def _integer(query, name, default, minimum, maximum):
        value = query.get(name, [str(default)])
        if len(value) != 1 or re.fullmatch(r"[0-9]{1,12}", value[0]) is None:
            raise _Validation(name)
        number = int(value[0])
        if not minimum <= number <= maximum:
            raise _Validation(name)
        return number

    def _scenario(self, prompt):
        result = {"delay": 0.0, "queue": None, "partial": None, "fail": None, "changed": False, "empty": False}
        markers = re.findall(r"\[\[fake:([^\]]+)\]\]", prompt)
        for marker in markers:
            name, _, value = marker.partition("=")
            try:
                if name == "delay":
                    delay = float(value)
                    if not math.isfinite(delay) or not 0 <= delay <= 86400:
                        raise ValueError
                    result[name] = delay
                elif name == "queue":
                    position, total = map(int, value.split("/"))
                    if not 1 <= position <= total <= 1000000:
                        raise ValueError
                    result[name] = (position, total)
                elif name == "partial":
                    if not value.isascii() or not value.isdigit() or len(value) > 3:
                        raise ValueError
                    result[name] = int(value)
                elif name == "fail":
                    if value not in ("user", "ailabs"):
                        raise ValueError
                    result[name] = value
                elif name in ("changed", "empty"):
                    result[name] = True
            except (ValueError, OverflowError):
                raise _Validation("prompt") from None
        if result["queue"] and not any(marker.startswith("delay=") for marker in markers):
            result["delay"] = 6.0
        return result

    def _submit(self, account, data):
        fixture = self._fixture["generate"]
        rules = fixture["request_rules"]
        models = self._fixture["models"]
        checkpoint = data.get("checkpoint")
        model = next(((name, spec) for name, spec in models["requests"].items()
                      if spec["request"]["checkpoint"] == checkpoint), None)
        known = checkpoint in models["names"].values()
        record = {"keys": sorted(key if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", key) else "<invalid>"
                                 for key in data), "checkpoint": checkpoint if known else None, "provider_calls": []}
        self.requests.append(record)
        retired = known and checkpoint in self._retired
        prompt = data.get("prompt")
        if not isinstance(prompt, str) or (not prompt.strip() and model is None):
            raise _Validation("prompt")
        if not known:
            raise _Validation("checkpoint")
        organization = self._organization(data.get("organization_id"))
        if not self._org(account, organization):
            return self._error("org_denied")
        count = data.get("num_outputs", 1)
        ideogram = model is not None and "ideogram" in model[1]["request"] and not retired
        if type(count) is not int or (not ideogram and not rules["num_outputs"][0] <= count <= rules["num_outputs"][1]):
            raise _Validation("num_outputs")
        request_id = data.get("request_id")
        if "request_id" in data:
            self._validate_id(request_id, "request_id")
        groups = data.get("input_images", [[]])
        if (not isinstance(groups, list) or any(not isinstance(group, list)
                or (model is None and not retired and len(group) > rules["max_inputs_per_group"]) for group in groups)):
            raise _Validation("input_images")
        kind, failure = "gpt", None
        effective = checkpoint
        used = list(range(sum(map(len, groups))))
        if retired:
            # 목록에만 있는 대체 모델을 사용한다. 실제 공급자를 부르지는 않는다.
            effective = next(row["checkpoint"] for row in models["catalog"]["response"]["models"]
                             if row["checkpoint"] not in models["names"].values())
            kind = "fallback"
            original_size = []
            first = _input_source(groups, size_out=original_size)
            width, height = original_size or (1024, 1024)
            size = f"{width}x{height}"
        elif model is not None:
            kind, first, (width, height), failure, used = self._model_input(*model, data, groups)
            size = f"{width}x{height}"
        else:
            first = _input_source(groups)
            size = data.get("size", "1024x1024")
            if not isinstance(size, str) or re.fullmatch(r"[0-9]{1,4}x[0-9]{1,4}", size) is None:
                raise _Validation("size")
            width, height = map(int, size.split("x"))
            if not (1 <= width <= 3840 and 1 <= height <= 3840 and width * height <= 8294400):
                raise _Validation("size")
        record.update(effective_checkpoint=effective, retired=retired, input_count=sum(map(len, groups)), used_input_indices=used)
        scenario = self._scenario(prompt)
        user_id = "u_" + account
        job_id = str(uuid.uuid5(_NAMESPACE, json.dumps({"user_id": user_id, "request_id": request_id, "index": 0},
                                                     sort_keys=True, separators=(",", ":")))) if request_id else str(uuid.uuid4())
        if job_id in self.jobs:
            job = self._update_job(job_id)
            record.update(job_id=job_id, reused=True, effective_checkpoint=job["request_data"]["checkpoint"])
            return _json(200, {"job_id": job_id, "job_ids": [job_id], "status": job["status"]})
        now = self._now()
        job = copy.deepcopy(self._fixture["job"]["pending"])
        summary = {key: value for key, value in data.items() if key in fixture["request"] and key != "input_images"}
        summary.update(prompt=prompt, num_outputs=count, size=size)
        if kind != "gpt":
            # 새 경로는 검증하지 않은 옵션 값을 되비추지 않는다. 요청 키는 requests 에 따로 남긴다.
            summary = dict(checkpoint=effective, prompt=prompt, num_outputs=count, organization_id=organization)
            if request_id is not None:
                summary["request_id"] = request_id
            if kind == "seedream" and failure is None:
                summary["image_size"] = size
        job.update(id=job_id, user_id=user_id, created_at=_stamp(now), request_data=summary,
                   queue_position=scenario["queue"][0] if scenario["queue"] else None,
                   queue_total=scenario["queue"][1] if scenario["queue"] else None)
        while len(self.jobs) >= _MAX_JOBS:
            oldest = next(iter(self.jobs))
            del self.jobs[oldest]
            del self._states[oldest]
            for key in list(self._rendered):
                if key[0] == oldest:
                    self._rendered_bytes -= len(self._rendered.pop(key))
        self.jobs[job_id] = job
        autopilot = data.get("prompt_autopilot")
        self._states[job_id] = dict(scenario, at=now, polls=0, size=(width, height), source=first,
                                   changed=scenario["changed"] or "prompt_autopilot" not in data
                                   or isinstance(autopilot, dict) and "candidate_prompt" in autopilot)
        record.update(job_id=job_id, reused=False)
        if kind != "gpt":
            record["provider_calls"] = list(range(count if kind in ("seedream", "gemini") else 1)) if failure is None else []
            self._states[job_id].update(kind=kind, input_error=failure, changed=scenario["changed"], record=record)
        return _json(200, {"job_id": job_id, "job_ids": [job_id], "status": "pending"})

    def _update_job(self, job_id, poll=False):
        job = self.jobs[job_id]
        state = self._states[job_id]
        if job["status"] in ("completed", "failed"):
            return job
        now = max(state["at"], self._now())
        delay = state["delay"]
        queued = state["queue"] is not None
        start = state["at"] + (delay / 2 if queued else 0)
        end = state["at"] + delay
        duration = end - start
        if duration > 0 and queued and now < start:
            job["queue_position"] = max(1, state["queue"][0] - state["polls"])
            if poll:
                state["polls"] += 1
            return job
        job.update(status="processing", queue_position=None, queue_total=None,
                   started_at=_stamp(start), execution_deadline_at=_stamp(start + 1200))
        if duration > 0 and now < end:
            job["progress"] = max(1, min(99, int(100 * (now - start) / duration)))
            return job
        job.update(progress=100, completed_at=_stamp(end))
        if state.get("input_error") is not None:
            job.update(state["input_error"])
            return job
        if state["fail"]:
            job.update(copy.deepcopy(self._fixture["job"]["failed_" + state["fail"]]))
            if state.get("kind") == "seedream":
                job["error_message"] = "seedream: " + job["error_message"]
            return job
        count = job["request_data"]["num_outputs"]
        actual = 0 if state["empty"] else min(count, state["partial"] if state["partial"] is not None else count)
        metadata = [{} for _ in range(actual)]
        kind = state.get("kind")
        if kind in ("seedream", "ideogram", "gemini"):
            indices = list(range(actual))
            if kind in ("seedream", "gemini") and 1 < actual < count:
                indices[-1] = count - 1
            metadata = [{"call_index": index} for index in indices]
            if kind == "ideogram":
                for item in metadata:
                    item["prompt_used"] = job["request_data"]["prompt"]
            if kind != "gemini" and state["changed"] and actual:
                metadata[-1]["prompt_used"] = job["request_data"]["prompt"] + " (rewritten)"
            state["record"]["successful_calls"] = indices if kind in ("seedream", "gemini") else ([0] if actual else [])
        elif state["changed"] and actual:
            metadata = [{"arm": "control", "call_index": i} for i in range(actual)]
            metadata[-1].update(arm="autopilot", prompt_used=job["request_data"]["prompt"] + " (rewritten)")
        result = {"images": [f"{self.media_origin}/fake-media/{job_id}/{i}.png" for i in range(actual)],
                  "result_ids": [f"{job_id}:{i}" for i in range(actual)], "result_metadata": metadata,
                  "thumbnails": [{} for _ in range(actual)], "saved_id": job_id}
        if actual < count and kind not in ("seedream", "gemini"):
            result["warning"] = self._fixture["job"]["completed_partial"]["result_data"]["warning"]
        job.update(status="completed", result_data=result)
        return job

    def _history(self, account, query):
        organization = query.get("organization_id", [None])[0]
        if organization is None:
            raise _Validation("organization_id")
        if not self._org(account, organization):
            return self._error("org_denied")
        limit = self._integer(query, "limit", 40, 1, 100)
        cursor = query.get("cursor", [""])[0]
        offset = 0
        if cursor:
            try:
                offset = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))["offset"]
                if type(offset) is not int or offset < 0:
                    raise ValueError
            except Exception:
                raise _Validation("cursor") from None
        search = query.get("search", [""])[0]
        try:
            pattern = re.compile(search, re.I)
            since = datetime.fromisoformat(query["date_start"][0].replace("Z", "+00:00")) if query.get("date_start") else None
            if since is not None and since.tzinfo is None:
                raise ValueError
        except (ValueError, re.error):
            raise _Validation("search/date_start") from None
        view = query.get("view", ["summary"])[0]
        jobs = [self._update_job(key) for key, job in reversed(self.jobs.items())
                if self._org(job["user_id"].removeprefix("u_"), organization)]
        jobs = [job for job in jobs if job["status"] == "completed" and job["result_data"]["images"]]
        jobs.sort(key=lambda job: (self._states[job["id"]]["at"] + self._states[job["id"]]["delay"],
                                   self._states[job["id"]]["at"]), reverse=True)
        templates = self._fixture["history"]["response"]["items"]
        items = []
        for job in jobs:
            urls = job["result_data"]["images"]
            common = {"prompt": job["request_data"]["prompt"], "created_at": job["created_at"],
                      "created_by": job["user_id"], "is_favorite": False, "generation_type": "generate",
                      "batch_id": job["id"]}
            if len(urls) == 1:
                item = copy.deepcopy(templates[0])
                item.update(common, id=job["id"] + ":0", result_id=job["id"] + ":0", statement_id=job["id"],
                            result_index=0, result_url=urls[0], thumbnail_url=urls[0],
                            batch_position=0,
                            model_used=job["request_data"]["checkpoint"], completed_at=job["completed_at"],
                            parameters=dict(zip(("width", "height"), self._states[job["id"]]["size"])), history_view=view)
            else:
                item = copy.deepcopy(templates[1])
                item.update(common, id=job["id"], num_outputs=len(urls), completed_at=job["completed_at"], items=[])
                for index, url in enumerate(urls):
                    child = copy.deepcopy(templates[1]["items"][min(index, 1)])
                    child.update(common, id=f"{job['id']}:{index}", result_url=url, batch_position=index,
                                 thumbnail_url=url if child["thumbnail_url"] is not None else None,
                                 model_used=job["request_data"]["checkpoint"])
                    item["items"].append(child)
            items.append(item)
        items.extend(copy.deepcopy(self._seed_history(account)))
        def selected(item):
            model = item.get("model_used") or (item.get("items") or [{}])[0].get("model_used")
            return (pattern.search(item["prompt"]) is not None
                    and (not query.get("user_id") or item.get("created_by") == query["user_id"][0])
                    and (not query.get("model_used") or model == query["model_used"][0])
                    and (not query.get("is_favorite") or item.get("is_favorite") is (query["is_favorite"][0] == "true"))
                    and (since is None or datetime.fromisoformat(item["created_at"]) >= since))
        items = [item for item in items if selected(item)]
        items.sort(key=lambda item: item.get("completed_at") or item["created_at"], reverse=True)
        more = offset + limit < len(items)
        next_cursor = base64.urlsafe_b64encode(json.dumps({"offset": offset + limit}).encode()).decode() if more else None
        return _json(200, {"items": items[offset:offset + limit], "limit": limit, "next_cursor": next_cursor, "has_more": more, "trace_id": uuid.uuid4().hex})

    def _seed_history(self, account):
        if account not in self._history_seed:
            try:
                from .bmk_design_patch_prompt import PROMPT_HEAD
            except ImportError:
                from bmk_design_patch_prompt import PROMPT_HEAD
            items = []
            for i, kind in enumerate(("generate", "generate", "generate", "upscale", "bg_removal", "generate")):
                ident = str(uuid.uuid5(_NAMESPACE, account + ":history:" + str(i)))
                stamp = _stamp(self._now() - (i + 1) * 86400)
                common = dict(created_by="u_teammate" if i == 1 else "u_" + account,
                              is_favorite=i % 2 == 0, generation_type=kind,
                              prompt=PROMPT_HEAD + " 개발 조각" if i == 2 else "개발 그림 [의상] " + str(i),
                              created_at=stamp, completed_at=stamp, batch_id=ident)
                children = []
                for pos in range(3 if i == 0 else 1):
                    path = f"/fake-media/seed-{ident}-{pos}.png"
                    self._media[path] = ("image/png", _png((320, 256), "FAKE HISTORY", f"{ident}:{pos}"))
                    child = copy.deepcopy(self._fixture["history"]["response"]["items"][0])
                    child.update(common, id=f"{ident}:{pos}", result_id=f"{ident}:{pos}", statement_id=ident,
                                 result_index=pos, batch_position=pos, result_url=self.media_origin + path,
                                 thumbnail_url=self.media_origin + path,
                                 model_used="gpt_image_26_sunburst" if i % 2 == 0 else "dev_model")
                    children.append(child)
                items.append(dict(common, id=ident, num_outputs=3, items=children) if len(children) > 1 else children[0])
            self._history_seed[account] = items
        return self._history_seed[account]

    def _account_assets(self, account):
        if account not in self._assets:
            assets = []
            for index, (name, folder) in enumerate((("레퍼런스 시트 A", "레퍼런스/의상"),
                                                    ("색상 시트 B", "레퍼런스/색상"), ("구도 시트 C", "구도"),
                                                    ("의상 시트 D", "레퍼런스/의상"), ("빈 폴더 시트 E", ""), ("외부 자료 F", "외부"))):
                asset = copy.deepcopy(self._fixture["assets"]["response"][0])
                asset_id = "a_" + uuid.uuid5(_NAMESPACE, f"{account}:asset:{index}").hex
                path = f"/fake-media/{asset_id}.png"
                png = _png((256, 256), f"FAKE FORGE ASSET {index + 1}", asset_id)
                self._media[path] = ("image/png", png)
                asset.update(id=asset_id, owner_id="u_" + account, name=name, folder_path=folder,
                             storage_key=f"org1/u_{account}/asset/{asset_id}.png", url=self.media_origin + path,
                             thumbnail_url=self.media_origin + path, width=256, height=256, size_bytes=len(png),
                             client_request_id=f"dev-asset-{index}", tags=[folder.split("/")[-1], "Costume", "개발", "시트"],
                             created_at=_stamp(self._now() - index * 3600))
                if index == 5:
                    asset.update(url="https://outside.example/image.png", thumbnail_url="https://outside.example/thumb.png")
                assets.append(asset)
            self._assets[account] = assets
        return self._assets[account]

    def _upload_url(self, account, data):
        content_type, size = data.get("content_type"), data.get("size_bytes")
        if not isinstance(content_type, str) or content_type not in _IMAGE_TYPES:
            raise _Validation("content_type")
        if type(size) is not int or not 1 <= size <= self._fixture["asset_upload"]["max_bytes"]:
            raise _Validation("size_bytes")
        if not isinstance(data.get("filename"), str) or not data["filename"] or data.get("kind", "asset") != "asset":
            raise _Validation("filename")
        key = f"org1/u_{account}/asset/{uuid.uuid4().hex}"
        signature = secrets.token_urlsafe(24)
        # 자료 등록 전까지의 임시 올리기만 보관한다. 오래된 서명·본문도 함께 버린다.
        while len(self._uploads) >= _MAX_UPLOADS:
            del self._uploads[next(iter(self._uploads))]
        self._uploads[key] = {"account": account, "content_type": content_type, "size_bytes": size,
                              "sig": signature, "data": None, "complete": False}
        return _json(200, {"upload_url": f"{self.media_origin}/fake-upload/{key}?" + urlencode({"sig": signature}),
                           "file_url": "", "s3_key": key})

    def _put(self, key, query, headers, body):
        upload = self._uploads.get(key)
        signature = query.get("sig", [""])
        if (upload is None or len(signature) != 1 or not signature[0].isascii()
                or not secrets.compare_digest(signature[0], upload["sig"])):
            return _json(403, {"detail": "Invalid upload signature"})
        if upload["complete"]:
            return _json(409, {"detail": "Upload already completed"})
        if (body is None or len(body) != upload["size_bytes"]
                or headers.get("content-type", "").split(";", 1)[0] != upload["content_type"]):
            raise _Validation("upload")
        try:
            picture, image_format = _picture(body)
            if image_format != _IMAGE_TYPES[upload["content_type"]]:
                raise ValueError
        except Exception:
            raise _Validation("upload") from None
        upload.update(data=body, size=picture.size)
        return 200, {"Content-Type": "application/octet-stream"}, b""

    def _asset_write(self, account, path, data):
        if not self._org(account, data.get("organization_id")):
            return self._error("org_denied")
        if path.endswith("/upload-url"):
            return self._upload_url(account, data)
        key = data.get("s3_key")
        if not isinstance(key, str):
            raise _Validation("s3_key")
        if path == "/api/library/assets":
            request_id = data.get("client_request_id")
            self._validate_id(request_id, "client_request_id")
            previous = self._created.get((account, request_id))
            if previous:
                if previous["storage_key"] != key:
                    conflict = self._fixture["asset_upload"]["create"]["conflict"]
                    return _json(conflict["status"], conflict["response"])
                return self._asset_response(previous)
        upload = self._uploads.get(key)
        if upload is None or upload["account"] != account or upload["data"] is None:
            raise _Validation("s3_key")
        if path.endswith("/uploads/complete"):
            upload["complete"] = True
            return _json(200, {"size_bytes": len(upload["data"]), "content_type": upload["content_type"]})
        if not upload["complete"]:
            raise _Validation("s3_key")
        if not isinstance(data.get("name"), str) or not data["name"].strip():
            raise _Validation("name")
        if data.get("content_type") != upload["content_type"]:
            raise _Validation("content_type")
        if any(type(data.get(key)) is not int or data[key] != value for key, value in zip(("width", "height"), upload["size"])):
            raise _Validation("dimensions")
        assets = self._account_assets(account)
        asset = copy.deepcopy(self._fixture["assets"]["response"][0])
        asset_id = "a_" + uuid.uuid4().hex
        suffix = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}[upload["content_type"]]
        media_path = f"/fake-media/{asset_id}{suffix}"
        self._media[media_path] = (upload["content_type"], upload["data"])
        asset.update(id=asset_id, owner_id="u_" + account, name=data["name"], folder_path="", storage_key=key,
                     url=self.media_origin + media_path, thumbnail_url=self.media_origin + media_path,
                     content_type=upload["content_type"], size_bytes=len(upload["data"]), tags=[],
                     width=upload["size"][0], height=upload["size"][1], client_request_id=request_id,
                     content_hash=hashlib.sha256(upload["data"]).hexdigest(), created_at=_stamp(self._now()), updated_at=_stamp(self._now()))
        assets.insert(0, asset)
        self._created[(account, request_id)] = asset
        del self._uploads[key]
        return self._asset_response(asset)

    def _asset_response(self, asset):
        return _json(200, {key: asset[key] for key in self._fixture["asset_upload"]["create"]["response"]})

    def _get_media(self, path):
        if path in self._media:
            content_type, data = self._media[path]
            return 200, {"Content-Type": content_type}, data
        match = re.fullmatch(r"/fake-media/([a-f0-9-]+)/([0-9]{1,2})\.png", path)
        if match and match[1] in self.jobs:
            job = self._update_job(match[1])
            slot = int(match[2])
            if job["status"] == "completed" and slot < len(job["result_data"]["images"]):
                key = (match[1], slot)
                if key in self._rendered:
                    self._rendered.move_to_end(key)
                    return 200, {"Content-Type": "image/png"}, self._rendered[key]
                state = self._states[match[1]]
                source = _picture(state["source"])[0] if state["source"] is not None else None
                png = _png(state["size"], "FAKE FORGE", f"{match[1]}:{slot}", source)
                if len(png) <= _MAX_RENDERED_BYTES:
                    while self._rendered and (len(self._rendered) >= _MAX_RENDERED
                                             or self._rendered_bytes + len(png) > _MAX_RENDERED_BYTES):
                        self._rendered_bytes -= len(self._rendered.popitem(last=False)[1])
                    self._rendered[key] = png
                    self._rendered_bytes += len(png)
                return 200, {"Content-Type": "image/png"}, png
        return _json(404, {"detail": "Not Found"})

    async def handle(self, method: str, url: str, headers: dict, body: bytes | None = None,
                     timeout: float | None = None) -> tuple[int, dict, bytes]:
        """ForgeClient 의 전송 함수 모양. 모르는 경로는 404 {"detail": "Not Found"}."""
        try:
            parsed = urlsplit(url)
            query = parse_qs(parsed.query, keep_blank_values=True)
            with self._lock:
                self.calls.append((method.upper(), parsed.path))
                allowed = {"organization_id", "limit", "offset", "view", "cursor", "search", "user_id", "model_used", "is_favorite", "date_start"}
                bearer = next((v.removeprefix("Bearer ") for k, v in headers.items() if k.lower() == "authorization"), "")
                self.query_calls.append((method.upper(), parsed.path, {k: [
                    _TOKEN.sub("", v.replace(bearer, "") if bearer else v) for v in values] if k in allowed else []
                    for k, values in query.items()}))
                fault = self.fault
                if (fault and fault.get("times", 0) > 0 and fault.get("path") == parsed.path
                        and all(query.get(k) == [str(v)] for k, v in fault.get("query", {}).items())):
                    fault["times"] -= 1
                    fault = dict(fault)
                else:
                    fault = None
            if fault:
                await asyncio.sleep(fault.get("delay", 0))
                if fault.get("status", 200) != 200:
                    return _json(fault["status"], {"detail": "Development fault"})
            # 이미지 작업과 상태 잠금 대기를 모두 이벤트 루프 밖에서 수행한다.
            return await asyncio.to_thread(self._handle, method, url, headers, body)
        except Exception:
            return _json(500, {"detail": "Internal Server Error"})

    def _handle(self, method, url, headers, body):
        try:
            with self._lock:
                parsed = urlsplit(url)
                path = parsed.path
                method = method.upper()
                headers = {key.lower(): value for key, value in headers.items()}
                query = parse_qs(parsed.query, keep_blank_values=True)
                routes = {"/auth/me": ("GET",), "/api/access/summary": ("GET",),
                          "/api/image/templates": ("GET",),
                          "/api/image/generate": ("POST",), "/api/image/history": ("GET",),
                          "/api/library/assets": ("GET", "POST"), "/api/library/assets/upload-url": ("POST",),
                          "/api/library/assets/uploads/complete": ("POST",)}
                allowed = routes.get(path)
                if path.startswith("/api/image/jobs/") or path.startswith("/fake-media/"):
                    allowed = ("GET",)
                elif path.startswith("/fake-upload/"):
                    allowed = ("PUT",)
                if allowed is None:
                    return _json(404, {"detail": "Not Found"})
                if method not in allowed:
                    return _json(405, {"detail": "Method Not Allowed"})
                if body is not None and (not isinstance(body, bytes) or len(body) > _BODY_LIMIT):
                    return _json(413, {"detail": "Request body too large"})
                if path.startswith("/fake-media/"):
                    return self._get_media(path)
                if path.startswith("/fake-upload/"):
                    return self._put(path[len("/fake-upload/"):], query, headers, body)
                if "authorization" not in headers and path == "/auth/me":
                    return _json(200, {"authenticated": False})
                authorization = headers.get("authorization", "")
                bearer = re.fullmatch(r"Bearer (.+)", authorization, re.I) if isinstance(authorization, str) else None
                token = _TOKEN.fullmatch(bearer[1]) if bearer else None
                if token is None:
                    return self._error("invalid_token")
                try:
                    if token[2] is not None and int(token[2]) <= self._now():
                        return self._error("invalid_token")
                except ValueError:
                    return self._error("invalid_token")
                account = token[1]
                if path == "/auth/me":
                    return _json(200, {"authenticated": True, "user": {"id": "u_" + account,
                                      "email": account + "@example.com", "name": account, "picture": None}})
                if path == "/api/access/summary":
                    result = copy.deepcopy(self._fixture["access_summary"]["response"])
                    result["serviceWhitelist"]["designPatch"] = not account.startswith("noaccess")
                    if account.startswith("outsider"):
                        result["organizations"] = []
                    return _json(200, result)
                if path == "/api/image/templates":
                    return _json(200, self._catalog())
                if path.startswith("/api/image/jobs/"):
                    job_id = path[len("/api/image/jobs/"):]
                    if job_id not in self.jobs or self.jobs[job_id]["user_id"] != "u_" + account:
                        return self._error("job_not_found")
                    return _json(200, self._update_job(job_id, poll=True))
                if path == "/api/image/history":
                    return self._history(account, query)
                if path == "/api/library/assets" and method == "GET":
                    organization = self._organization(query.get("organization_id", [None])[0])
                    if not self._org(account, organization):
                        return self._error("org_denied")
                    limit = self._integer(query, "limit", 200, 1, 200)
                    offset = self._integer(query, "offset", 0, 0, 10**12 - 1)
                    assets = copy.deepcopy(self._account_assets(account)[offset:offset + limit])
                    for asset in assets:
                        for key in ("url", "thumbnail_url"):
                            if isinstance(asset.get(key), str) and asset[key].startswith(self.media_origin + "/"):
                                asset[key] = asset[key].split("?", 1)[0] + "?issued=" + str(self._now())
                    return _json(200, assets)
                try:
                    data = json.loads(body)
                    if not isinstance(data, dict):
                        raise ValueError
                except (ValueError, TypeError, UnicodeError):
                    raise _Validation("body") from None
                if path == "/api/image/generate":
                    return self._submit(account, data)
                return self._asset_write(account, path, data)
        except _Validation as error:
            result = copy.deepcopy(self._fixture["errors"]["validation"]["response"])
            result["detail"][0].update(loc=["body", str(error)], msg="Invalid value", type="value_error")
            return _json(422, result)
        except Exception:
            return _json(500, {"detail": "Internal Server Error"})


def _loopback_authority(value):
    if not isinstance(value, str) or not value or any(c.isspace() or ord(c) < 32 for c in value):
        raise ValueError
    parsed = urlsplit("//" + value)
    if (parsed.hostname not in ("localhost", "127.0.0.1", "::1") or parsed.username is not None
            or parsed.password is not None or parsed.path or parsed.query or parsed.fragment
            or parsed.netloc != value or value.endswith(":")):
        raise ValueError
    parsed.port
    return value.lower(), "[::1]" if parsed.hostname == "::1" else parsed.hostname


def _refresh_page(query, authority, host, config, fake):
    account = query.get("acct", "alice")
    if not isinstance(account, str) or _ACCOUNT.fullmatch(account) is None:
        raise ValueError
    ttl = FakeForge._integer({"ttl": [query.get("ttl", "120")]}, "ttl", 120, 5, 86400)
    every = FakeForge._integer({"every": [query.get("every", "60")]}, "every", 60, 2, 3600)
    silent = query.get("silent", "0")
    if silent not in ("0", "1"):
        raise ValueError
    token = issue_token(account, ttl, clock=fake._now)
    expiry = int(token.rsplit(":", 1)[1])
    origin = f"http://{host}:{config.port}"
    source = origin + "/?" + urlencode({"parent_origin": "http://" + authority})
    source += "#" + urlencode({"token": token, "organization_id": "org1"})
    script = (f"let expires = {expiry};\n"
              'const remaining = document.getElementById("remaining");\n'
              'function countdown() { remaining.textContent = "남은 시간: " + Math.max(0, expires - Math.floor(Date.now() / 1000)) + "초"; }\n'
              'countdown();\nsetInterval(countdown, 1000);\n')
    if silent != "1":
        # 검증된 값도 JSON 리터럴로 넣어 HTML/JavaScript 경계를 지킨다.
        encoded_origin = json.dumps(origin).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
        script += (f"const appOrigin = {encoded_origin};\nconst account = {json.dumps(account)};\n"
                   'const iframe = document.getElementById("studio");\n'
                   f"function refresh() {{\n  expires = Math.floor(Date.now() / 1000) + {ttl};\n"
                   '  const token = "dev:" + account + ":" + expires;\n'
                   '  iframe.contentWindow.postMessage({type: "design-patch-session", token, organization_id: "org1"}, appOrigin);\n'
                   '  countdown();\n}\n'
                   f"setInterval(refresh, {every * 1000});\n"
                   'window.addEventListener("message", function(event) {\n'
                   '  if (event.origin === appOrigin && event.source === iframe.contentWindow &&\n'
                   '      event.data && event.data.type === "design-patch-session-request") refresh();\n});\n')
    return ('<!doctype html><html lang="ko"><meta charset="utf-8"><title>Forge 개발</title>'
            '<body style="margin:0;background:#09090b;color:#e4e4e7"><header style="height:64px;display:flex;align-items:center;gap:24px">'
            '<strong>Forge 개발</strong><a style="color:#a1a1aa" href="?acct=alice">alice</a><a style="color:#a1a1aa" href="?acct=bob">bob</a>'
            '<small id="remaining">남은 시간: ' + str(ttl) + '초</small></header>'
            '<iframe id="studio" title="Design Patch Studio" style="display:block;border:0;width:100%;height:calc(100dvh - 64px)" '
            'sandbox="allow-scripts allow-same-origin allow-downloads allow-popups allow-popups-to-escape-sandbox" '
            'allow="clipboard-write" referrerpolicy="origin" src="' + html.escape(source, quote=True) + '"></iframe>'
            '<script>' + script + '</script></body></html>')


def mount(app, config, fake: FakeForge | None = None):
    """가짜 부모 앱(aiohttp)에 가짜 Forge API 라우트와 토큰 갱신 페이지(/refresh)를 붙인다 → FakeForge."""
    from aiohttp import web

    if not config.dev_handoff or config.dev_parent_port is None:
        raise ValueError("가짜 부모 페이지는 개발 모드·부모 포트가 필요합니다")
    if fake is None:
        fake = FakeForge(media_origin=f"http://localhost:{config.dev_parent_port}")

    async def refresh(request):
        try:
            authority, host = _loopback_authority(request.headers.get("Host"))
            if getattr(config, "forge", None) is None:
                return web.Response(status=400, text="토큰 갱신 페이지는 서버를 --forge-api 로 띄웠을 때만 씁니다")
            if any(len(request.query.getall(key)) != 1 for key in request.query):
                raise ValueError
            page = _refresh_page(request.query, authority, host, config, fake)
        except (ValueError, TypeError):
            return web.Response(status=400, text="개발 부모 페이지의 호스트·쿼리를 확인해 주세요")
        return web.Response(text=page, content_type="text/html", headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    async def forward(request):
        try:
            _loopback_authority(request.headers.get("Host"))
        except ValueError:
            return web.Response(status=400, text="개발 부모 페이지는 루프백 호스트로 열어 주세요")
        try:
            body = await request.clone(client_max_size=_BODY_LIMIT + 1).read()
            response = await fake.handle(request.method, str(request.rel_url), dict(request.headers), body or None)
        except web.HTTPRequestEntityTooLarge:
            response = _json(413, {"detail": "Request body too large"})
        except Exception:
            response = _json(500, {"detail": "Internal Server Error"})
        status, headers, body = response
        return web.Response(status=status, headers=headers, body=body)

    app.router.add_get("/refresh", refresh)
    for path in ("/auth/me", "/api/access/summary", "/api/image/templates", "/api/image/generate", "/api/image/jobs/{job_id}",
                 "/api/image/history", "/api/library/assets", "/api/library/assets/upload-url",
                 "/api/library/assets/uploads/complete", "/fake-media/{path:.+}", "/fake-upload/{key:.+}"):
        app.router.add_route("*", path, forward)
    app.router.add_route("*", "/{tail:.*}", forward)
    return fake
