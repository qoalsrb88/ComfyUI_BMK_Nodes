"""Design Patch Studio — 개발용 가짜 Forge(서버 모드 F2 의 개발·시험 전용). 운영에서는 쓰지 않는다:
bmk_design_patch_server 가 --dev-handoff + --dev-parent-port 로 띄운 가짜 부모(8790)에만 붙는다.

FakeForge.handle 은 소켓을 쓰지 않는다 — bmk_design_patch_forge.ForgeClient(transport=fake.handle) 로 끼우면 시험이
네트워크 없이 돌고, mount() 가 같은 구현을 8790 의 HTTP 라우트로 내놓는다. 응답 모양 정본 = tools/fixtures/forge_api.json.

토큰: `dev:<계정>`(만료 없음) · `dev:<계정>:<만료 epoch 초>`. 사용자 id = `u_<계정>`, 조직 = org1(이름 「개발 조직」).
시나리오는 프롬프트 속 표지로 건다(여러 개 가능): [[fake:delay=초]] [[fake:queue=순번/전체]] [[fake:partial=장수]]
[[fake:fail=user|ailabs]] [[fake:changed]] [[fake:empty]] — 표지가 없으면 곧바로 completed.
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


def _input_source(groups):
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
        prompt = data.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise _Validation("prompt")
        if data.get("checkpoint") not in fixture["checkpoints"].values():
            raise _Validation("checkpoint")
        organization = self._organization(data.get("organization_id"))
        if not self._org(account, organization):
            return self._error("org_denied")
        count = data.get("num_outputs", 1)
        if type(count) is not int or not rules["num_outputs"][0] <= count <= rules["num_outputs"][1]:
            raise _Validation("num_outputs")
        request_id = data.get("request_id")
        if "request_id" in data:
            self._validate_id(request_id, "request_id")
        groups = data.get("input_images", [[]])
        if (not isinstance(groups, list) or any(not isinstance(group, list)
                or len(group) > rules["max_inputs_per_group"] for group in groups)):
            raise _Validation("input_images")
        first = _input_source(groups)
        size = data.get("size", "1024x1024")
        if not isinstance(size, str) or re.fullmatch(r"[0-9]{1,4}x[0-9]{1,4}", size) is None:
            raise _Validation("size")
        width, height = map(int, size.split("x"))
        if not (1 <= width <= 3840 and 1 <= height <= 3840 and width * height <= 8294400):
            raise _Validation("size")
        scenario = self._scenario(prompt)
        user_id = "u_" + account
        job_id = str(uuid.uuid5(_NAMESPACE, json.dumps({"user_id": user_id, "request_id": request_id, "index": 0},
                                                     sort_keys=True, separators=(",", ":")))) if request_id else str(uuid.uuid4())
        if job_id in self.jobs:
            job = self._update_job(job_id)
            return _json(200, {"job_id": job_id, "job_ids": [job_id], "status": job["status"]})
        now = self._now()
        job = copy.deepcopy(self._fixture["job"]["pending"])
        summary = {key: value for key, value in data.items() if key in fixture["request"] and key != "input_images"}
        summary.update(prompt=prompt, num_outputs=count, size=size)
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
        if state["fail"]:
            job.update(copy.deepcopy(self._fixture["job"]["failed_" + state["fail"]]))
            return job
        count = job["request_data"]["num_outputs"]
        actual = 0 if state["empty"] else min(count, state["partial"] if state["partial"] is not None else count)
        metadata = [{} for _ in range(actual)]
        if state["changed"] and actual:
            metadata = [{"arm": "control", "call_index": i} for i in range(actual)]
            metadata[-1].update(arm="autopilot", prompt_used=job["request_data"]["prompt"] + " (rewritten)")
        result = {"images": [f"{self.media_origin}/fake-media/{job_id}/{i}.png" for i in range(actual)],
                  "result_ids": [f"{job_id}:{i}" for i in range(actual)], "result_metadata": metadata,
                  "thumbnails": [{} for _ in range(actual)], "saved_id": job_id}
        if actual < count:
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
        search = query.get("search", [""])[0].casefold()
        view = query.get("view", ["summary"])[0]
        jobs = [self._update_job(key) for key, job in reversed(self.jobs.items()) if job["user_id"] == "u_" + account]
        jobs = [job for job in jobs if job["status"] == "completed" and job["result_data"]["images"]
                and search in job["request_data"]["prompt"].casefold()]
        jobs.sort(key=lambda job: (self._states[job["id"]]["at"] + self._states[job["id"]]["delay"],
                                   self._states[job["id"]]["at"]), reverse=True)
        templates = self._fixture["history"]["response"]["items"]
        items = []
        for job in jobs[offset:offset + limit]:
            urls = job["result_data"]["images"]
            common = {"prompt": job["request_data"]["prompt"], "created_at": job["created_at"]}
            if len(urls) == 1:
                item = copy.deepcopy(templates[0])
                item.update(common, id=job["id"] + ":0", result_id=job["id"] + ":0", statement_id=job["id"],
                            result_index=0, result_url=urls[0], thumbnail_url=urls[0],
                            model_used=job["request_data"]["checkpoint"], completed_at=job["completed_at"],
                            parameters=dict(zip(("width", "height"), self._states[job["id"]]["size"])), history_view=view)
            else:
                item = copy.deepcopy(templates[1])
                item.update(common, id=job["id"], num_outputs=len(urls), completed_at=job["completed_at"], items=[])
                for index, url in enumerate(urls):
                    child = copy.deepcopy(templates[1]["items"][min(index, 1)])
                    child.update(common, id=f"{job['id']}:{index}", result_url=url,
                                 thumbnail_url=url if child["thumbnail_url"] is not None else None,
                                 model_used=job["request_data"]["checkpoint"])
                    item["items"].append(child)
            items.append(item)
        more = offset + limit < len(jobs)
        next_cursor = base64.urlsafe_b64encode(json.dumps({"offset": offset + limit}).encode()).decode() if more else None
        return _json(200, {"items": items, "limit": limit, "next_cursor": next_cursor, "has_more": more, "trace_id": uuid.uuid4().hex})

    def _account_assets(self, account):
        if account not in self._assets:
            assets = []
            for index, (name, folder) in enumerate((("레퍼런스 시트 A", "레퍼런스/의상"),
                                                    ("색상 시트 B", "레퍼런스/색상"), ("구도 시트 C", "구도"))):
                asset = copy.deepcopy(self._fixture["assets"]["response"][0])
                asset_id = "a_" + uuid.uuid5(_NAMESPACE, f"{account}:asset:{index}").hex
                path = f"/fake-media/{asset_id}.png"
                png = _png((256, 256), f"FAKE FORGE ASSET {index + 1}", asset_id)
                self._media[path] = ("image/png", png)
                asset.update(id=asset_id, owner_id="u_" + account, name=name, folder_path=folder,
                             storage_key=f"org1/u_{account}/asset/{asset_id}.png", url=self.media_origin + path,
                             thumbnail_url=self.media_origin + path, width=256, height=256, size_bytes=len(png),
                             client_request_id=f"dev-asset-{index}", tags=[folder.split("/")[-1]])
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
                self.calls.append((method, path))  # 쿼리(업로드 서명)·본문·Authorization 은 보관하지 않는다.
                headers = {key.lower(): value for key, value in headers.items()}
                query = parse_qs(parsed.query, keep_blank_values=True)
                routes = {"/auth/me": ("GET",), "/api/access/summary": ("GET",),
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
                    return _json(200, self._account_assets(account)[offset:offset + limit])
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
    for path in ("/auth/me", "/api/access/summary", "/api/image/generate", "/api/image/jobs/{job_id}",
                 "/api/image/history", "/api/library/assets", "/api/library/assets/upload-url",
                 "/api/library/assets/uploads/complete", "/fake-media/{path:.+}", "/fake-upload/{key:.+}"):
        app.router.add_route("*", path, forward)
    app.router.add_route("*", "/{tail:.*}", forward)
    return fake
