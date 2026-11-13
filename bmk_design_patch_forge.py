"""Design Patch Studio — Forge 연결(서버 모드 F2): 설정 · HTTP 클라이언트 · 토큰 검증기 · 계정 창구.

Forge 와의 HTTP 결합은 이 모듈에만 둔다(요청·응답 모양 정본 = tools/fixtures/forge_api.json). 로컬판(ComfyUI 안)은 이 모듈을
import 하지 않는다. 실제 Forge 주소는 코드·문서·fixture 어디에도 적지 않는다 — 실행 옵션(--forge-api …)으로만 받는다.

지킬 것
- 토큰은 인자로만 받고 어디에도 보관하지 않는다(보관은 서버의 세션 표 — bmk_design_patch_server). 토큰·Authorization·요청/응답
  본문·Forge 주소를 로그·예외 문구·repr 에 싣지 않는다. aiohttp 예외는 밖으로 내보내지 않고 고정 문구의 ForgeError 로 바꾼다
  (`raise … from None`).
- HTTP 세션은 호출마다 열고 닫는다 — 검증기는 서버 이벤트 루프에서, 생성 백엔드는 작업 스레드의 asyncio.run(노드마다 새 루프)
  에서 부른다. 세션을 나눠 쓰지 않는다. 쿠키 저장 없음(DummyCookieJar), 환경 변수 프록시 없음(trust_env=False), 리다이렉트를
  따라가지 않는다.
"""

from __future__ import annotations

import asyncio
import base64
import dataclasses
import json
import math
import re
import time
import unicodedata
from typing import Any, Awaitable, Callable

import aiohttp
from yarl import URL

JSON_LIMIT = 8 * 1024 ** 2      # 압축을 푼 뒤의 JSON 응답 상한
MEDIA_LIMIT = 64 * 1024 ** 2    # 결과 그림 한 장 상한
ACCESS_TTL = 1800.0             # 메뉴 권한을 다시 보는 간격(초)


@dataclasses.dataclass(frozen=True, repr=False)
class ForgeConfig:
    api: str                                   # Forge API origin(https — allow_http 일 때만 루프백 http)
    media_origins: tuple = ()                  # 결과·자료 그림을 받아도 되는 origin 들
    access_keys: tuple = ()                    # access/summary 의 serviceWhitelist 에서 전부 참이어야 하는 키(비면 조직 소속만)
    timeout: float = 30.0                      # 호출 하나의 시간 제한(초)
    verify_timeout: float = 8.0                # 검증(/auth/me + access/summary) 합계 상한(초)
    concurrency: int = 2                       # 한 Run 이 동시에 제출해 두는 잡 수
    prompt_autopilot: Any = False              # 생성 요청의 prompt_autopilot 키 값(자동 재작성 대상에서 빠지는 표지)
    session_message: str = "design-patch-session"            # 부모 → 앱 토큰 갱신 메시지 type
    request_message: str = "design-patch-session-request"    # 앱 → 부모 토큰 요청 메시지 type
    allow_http: bool = False                   # 개발 모드(--dev-handoff)에서만 참

    def __post_init__(self):
        api = normalize_origin(self.api)
        if not isinstance(self.media_origins, (tuple, list)) or not isinstance(self.access_keys, (tuple, list)):
            raise ValueError("Forge origin·접근 키는 목록이어야 합니다")
        media = tuple(dict.fromkeys(normalize_origin(value) for value in self.media_origins))
        for value in (api, *media):
            if not _allowed_scheme(URL(value), self.allow_http):
                raise ValueError("Forge 주소는 HTTPS 또는 개발용 루프백 HTTP 여야 합니다")
        if (type(self.allow_http) is not bool or type(self.concurrency) is not int or not 1 <= self.concurrency <= 8
                or any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0
                       for v in (self.timeout, self.verify_timeout))):
            raise ValueError("Forge 시간 제한·동시 제출 설정이 잘못되었습니다")
        if any(not isinstance(key, str) or not key for key in self.access_keys):
            raise ValueError("Forge 접근 키는 비지 않은 문자열이어야 합니다")
        if any(not isinstance(v, str) or not v or len(v) > 128
               or any(unicodedata.category(c).startswith("C") for c in v)
               for v in (self.session_message, self.request_message)):
            raise ValueError("Forge 메시지 이름이 잘못되었습니다")
        object.__setattr__(self, "api", api)
        object.__setattr__(self, "media_origins", media)
        object.__setattr__(self, "access_keys", tuple(self.access_keys))


class ForgeError(Exception):
    """Forge 호출 실패. reason ∈ REASONS, status = HTTP 상태(없으면 None), detail = 화면에 보여도 되는 짧은 글(없으면 None).
    str(e) 는 reason 별 고정 문구다 — 토큰·주소·본문이 들어가지 않는다."""

    REASONS = ("unauthorized", "forbidden", "invalid", "not_found", "unreachable", "server")

    def __init__(self, reason: str, status: int | None = None, detail: str | None = None, account_id: str | None = None):
        self.reason = reason if reason in self.REASONS else "unreachable"
        self.status = status if type(status) is int else None
        self.detail = _safe_detail(detail)
        self.account_id = account_id
        super().__init__({
            "unauthorized": "Forge 로그인 갱신이 필요합니다.",
            "forbidden": "Design Patch Studio 사용 권한이 없습니다.",
            "invalid": "Forge 가 요청을 받아들이지 못했습니다.",
            "not_found": "Forge 에서 결과를 찾지 못했습니다.",
            "unreachable": "Forge 에 연결하지 못했습니다.",
            "server": "Forge 응답을 받지 못했습니다.",
        }[self.reason])


# 전송 함수: async (method, url, headers, body, timeout) -> (status, headers, body). 시험·개발용 가짜 Forge 가 끼운다.
Transport = Callable[[str, str, dict, "bytes | None", float], Awaitable["tuple[int, dict, bytes]"]]


def token_exp(token: str) -> float | None:
    """토큰의 만료 시각(epoch 초). JWT 면 payload 의 exp 를 서명 검증 없이 읽고(검증은 /auth/me 가 한다), 개발 토큰
    `dev:<계정>:<epoch>` 면 그 값, 그 밖은 None. 예외를 내지 않는다."""
    try:
        if token.startswith("dev:"):
            parts = token.split(":")
            value = float(parts[2]) if len(parts) == 3 and parts[1] else None
        else:
            parts = token.split(".")
            if len(parts) != 3 or not all(parts):
                return None
            payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
            value = payload.get("exp")
            if type(value) not in (int, float):
                return None
            value = float(value)
        return value if value is not None and math.isfinite(value) else None
    except Exception:
        return None


def _url(value):
    valid = False
    try:
        if isinstance(value, str) and value and not any(c.isspace() or ord(c) < 32 for c in value):
            parsed = URL(value)
            host = (parsed.raw_host or "").lower().rstrip(".")
            valid = (parsed.scheme in ("http", "https") and bool(host)
                     and parsed.user is None and parsed.password is None and not parsed.fragment
                     and not re.match(r"(?i)https?://[^/?#]*:(?:[/?#]|$)", value)
                     and not any(c in host for c in "\\/%@") and 1 <= parsed.port <= 65535)
            if valid:
                parsed = parsed.with_host(host)
    except Exception:
        valid = False
    if not valid:
        raise ValueError("Forge 주소 형식이 잘못되었습니다") from None
    return parsed


def _allowed_scheme(url, allow_http):
    return url.scheme == "https" or (allow_http and url.host in ("localhost", "127.0.0.1", "::1"))


def normalize_origin(value: str) -> str:
    """origin 비교용 정규화 — scheme·IDNA 소문자 호스트(끝 점 제거)·실제 포트. userinfo·경로·쿼리가 있으면 ValueError."""
    parsed = _url(value)
    if parsed.path not in ("", "/") or parsed.query_string:
        raise ValueError("Forge origin 에는 경로·쿼리를 넣을 수 없습니다")
    return str(parsed.origin())


def _safe_detail(value):
    if not isinstance(value, str):
        return None
    if ("://" in value or "\\\\" in value or re.search(r"[A-Za-z]:[\\/]", value)
            or re.search(r"(?<![\w/])/{1,2}[^/\s]+/[^/\s]+", value)
            or re.search(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?!\w)", value)
            or re.search(r"(?i)(?:[0-9a-f]{0,4}:){2,}[0-9a-f:.]*", value)):
        return None
    for match in re.finditer(r"(?<![\w.-])([\w.-]+):\d+\b", value):
        if re.fullmatch(r"\d+(?:\.\d+)?", match[1]) is None:  # 숫자 비율은 주소가 아니다.
            return None
    for match in re.finditer(r"(?<![\w.-])[\w-]+(?:\.[\w-]+)+\.?(?![\w.-])", value):
        if re.fullmatch(r"\d+(?:\.\d+)+(?:[A-Za-z%]+)?", match[0].rstrip(".")) is None:
            return None
    return value[:300]


class _BodyTooLarge(Exception):
    pass


async def _http(method, url, headers, body, timeout, limit):
    async with aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar(), trust_env=False,
                                     timeout=aiohttp.ClientTimeout(total=timeout)) as session:
        async with session.request(method, url, headers=headers, data=body, allow_redirects=False) as response:
            raw = bytearray()
            # aiohttp 기본 auto_decompress 를 거친 바이트에 상한을 적용한다.
            async for chunk in response.content.iter_chunked(min(limit + 1, 65536)):
                if len(raw) + len(chunk) > limit:
                    raise _BodyTooLarge
                raw.extend(chunk)
            return response.status, dict(response.headers), bytes(raw)


class ForgeClient:
    """설정 + 전송 함수. 인스턴스는 이벤트 루프에 묶이지 않는다(스레드·루프를 넘겨 써도 된다)."""

    def __init__(self, config: ForgeConfig, transport: Transport | None = None):
        self.config, self._transport = config, transport

    async def _exchange(self, method, url, headers, body, limit):
        reason = None
        failure_status = None
        try:
            if self._transport is None:
                result = await _http(method, url, headers, body, self.config.timeout, limit)
            else:
                result = await asyncio.wait_for(self._transport(method, url, headers, body, self.config.timeout),
                                                timeout=self.config.timeout)
            status, response_headers, raw = result
            if type(status) is not int or not isinstance(response_headers, dict) or not isinstance(raw, bytes):
                reason = "unreachable"
            elif len(raw) > limit:
                reason, failure_status = "server", 413
        except _BodyTooLarge:
            reason, failure_status = "server", 413
        except Exception:
            reason = "unreachable"
        # except 밖에서 새 예외를 만들어 __context__ 에도 원래 헤더·주소가 남지 않게 한다.
        if reason:
            raise ForgeError(reason, failure_status) from None
        return status, raw

    @staticmethod
    def _status(status, raw, token=None):
        if status == 200:
            return
        reason = {401: "unauthorized", 403: "forbidden", 404: "not_found",
                  400: "invalid", 409: "invalid", 413: "invalid", 422: "invalid"}.get(status)
        reason = reason or ("server" if 500 <= status < 600 else "unreachable")
        detail = None
        if reason == "invalid":
            try:
                data = json.loads(raw)
                value = data.get("detail") if isinstance(data, dict) else None
                detail = _safe_detail(value.replace(token, "") if isinstance(value, str) and token else value)
            except Exception:
                pass
        raise ForgeError(reason, status, detail) from None

    async def _json(self, method, path, token, body=None):
        headers = {"Authorization": "Bearer " + token}
        encoded = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
        url = str(URL(self.config.api).with_path(path))
        status, raw = await self._exchange(method, url, headers, encoded, JSON_LIMIT)
        self._status(status, raw, token)
        data = None
        try:
            data = json.loads(raw)
        except Exception:
            pass
        if not isinstance(data, dict):
            raise ForgeError("unreachable", status) from None
        return data

    def __repr__(self) -> str:  # 설정의 주소를 싣지 않는다
        return "ForgeClient()"

    async def me(self, token: str) -> dict:
        """GET /auth/me → {"id", "email", "name"}. 401·authenticated 가 참이 아님 → ForgeError("unauthorized")."""
        data = await self._json("GET", "/auth/me", token)
        if data.get("authenticated") is not True:
            raise ForgeError("unauthorized", 200)
        user = data.get("user")
        if not isinstance(user, dict) or not isinstance(user.get("id"), str) or not user["id"]:
            raise ForgeError("unreachable", 200)
        return {key: user.get(key) for key in ("id", "email", "name")}

    async def access(self, token: str) -> dict:
        """GET /api/access/summary → {"orgs": [id…], "whitelist": {키: bool}}."""
        data = await self._json("GET", "/api/access/summary", token)
        orgs, whitelist = data.get("organizations"), data.get("serviceWhitelist")
        if not isinstance(orgs, list) or not isinstance(whitelist, dict):
            raise ForgeError("unreachable", 200)
        return {"orgs": [org["id"] for org in orgs if isinstance(org, dict) and isinstance(org.get("id"), str)],
                "whitelist": dict(whitelist)}

    async def submit(self, token: str, body: dict) -> dict:
        """POST /api/image/generate → {"job_id", "status"}. 4xx 는 ForgeError(reason, status, detail=가린 detail)."""
        data = await self._json("POST", "/api/image/generate", token, body)
        job_id = data.get("job_id")
        if job_id is None and isinstance(data.get("job_ids"), list) and data["job_ids"]:
            job_id = data["job_ids"][0]
        if not isinstance(job_id, str) or not job_id or data.get("status") not in ("pending", "processing", "completed", "failed"):
            raise ForgeError("unreachable", 200)
        return {"job_id": job_id, "status": data["status"]}

    async def job(self, token: str, job_id: str) -> dict:
        """GET /api/image/jobs/{id} → {"status", "queue_position", "queue_total", "execution_deadline_at", "images": [url…],
        "result_ids": […], "metadata": [{…}…], "error_message", "error_category"} — request_data 는 버린다."""
        if not isinstance(job_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", job_id):
            raise ForgeError("invalid")
        data = await self._json("GET", "/api/image/jobs/" + job_id, token)
        result = data.get("result_data")
        if data.get("status") not in ("pending", "processing", "completed", "failed") or (result is not None and not isinstance(result, dict)):
            raise ForgeError("unreachable", 200)
        result = result or {}
        images, ids, metadata = (result.get(key, []) for key in ("images", "result_ids", "result_metadata"))
        if (any(not isinstance(value, list) for value in (images, ids, metadata))
                or any(not isinstance(value, str) for value in images)
                or any(value is not None and not isinstance(value, str) for value in ids)
                or any(value is not None and not isinstance(value, dict) for value in metadata)):
            raise ForgeError("unreachable", 200)
        size = max(len(images), len(ids), len(metadata))
        keys = ("arm", "call_index", "seed", "seed_supported", "prompt_used")
        normalized = {key: data.get(key) for key in ("status", "queue_position", "queue_total", "execution_deadline_at", "error_category")}
        normalized.update(images=images + [None] * (size - len(images)), result_ids=ids + [None] * (size - len(ids)),
                          metadata=[{key: row[key] for key in keys if key in row} for row in (row or {} for row in metadata)]
                          + [{} for _ in range(size - len(metadata))],
                          error_message=_safe_detail(data.get("error_message", "").replace(token, ""))
                          if isinstance(data.get("error_message"), str) else None)
        return normalized

    async def fetch_media(self, url: str, limit: int = MEDIA_LIMIT) -> bytes:
        """media_origins 에 든 origin 의 그림만 받는다(Authorization 없이). 밖이면 ForgeError("forbidden")."""
        parsed = None
        try:
            parsed = _url(url)
        except ValueError:
            pass
        if (parsed is None or not _allowed_scheme(parsed, self.config.allow_http)
                or str(parsed.origin()) not in self.config.media_origins):
            raise ForgeError("forbidden") from None
        if type(limit) is not int or limit <= 0:
            raise ForgeError("invalid")
        status, raw = await self._exchange("GET", str(parsed), {}, None, min(limit, MEDIA_LIMIT))
        self._status(status, raw)
        return raw

    async def history(self, token: str, organization_id: str, *, cursor: str | None = None, search: str | None = None,
                      limit: int = 40) -> dict:
        """GET /api/image/history(view=summary) → {"items": [{"id", "url", "thumb", "prompt", "model", "created"}…],
        "next": cursor | None} — 묶음 항목은 장 단위로 편다."""
        raise NotImplementedError

    async def assets(self, token: str, organization_id: str, *, offset: int = 0, limit: int = 200) -> dict:
        """GET /api/library/assets → {"items": [{"id", "url", "thumb", "name", "folder"}…], "next": offset | None} — 그림만."""
        raise NotImplementedError

    async def upload_asset(self, token: str, organization_id: str, *, name: str, data: bytes, content_type: str,
                           client_request_id: str, width: int | None = None, height: int | None = None) -> dict:
        """upload-url → PUT → uploads/complete → POST /api/library/assets → {"id", "name"}."""
        raise NotImplementedError


class ForgeVerifier:
    """bmk_design_patch_server.make_app(verifier=…) 자리. 실패는 ForgeError 로 올린다(unauthorized → 401 · forbidden → 403 ·
    그 밖 → 503)."""

    def __init__(self, client: ForgeClient, config: ForgeConfig):
        self.client, self.config = client, config

    async def verify(self, token: str, organization_id: str) -> dict:
        """→ {"account_id", "organization_id", "name", "exp": float | None}."""
        tasks = [asyncio.create_task(call(token)) for call in (self.client.me, self.client.access)]
        try:
            _, pending = await asyncio.wait(tasks, timeout=min(8.0, self.config.verify_timeout))
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            values = await asyncio.gather(*tasks, return_exceptions=True)
        errors = [value for value in values if isinstance(value, BaseException)]
        if any(isinstance(err, ForgeError) and err.reason == "unauthorized" and err.status != 200 for err in errors):
            raise ForgeError("unauthorized", 401)
        if pending or any(not isinstance(err, ForgeError) or err.reason not in ("unauthorized", "forbidden") for err in errors):
            raise ForgeError("unreachable")
        if any(isinstance(err, ForgeError) and err.reason == "unauthorized" for err in errors):
            raise ForgeError("unauthorized", 200)
        try:
            from .bmk_design_patch_server import _valid_id
        except ImportError:
            from bmk_design_patch_server import _valid_id
        user = values[0]
        account_id = user.get("id") if isinstance(user, dict) and _valid_id(user.get("id")) else None
        if any(isinstance(err, ForgeError) and err.reason == "forbidden" for err in errors):
            raise ForgeError("forbidden", 403, account_id=account_id)
        user, access = values
        if not _valid_id(user.get("id")) or not _valid_id(organization_id):
            raise ForgeError("unreachable")
        if not _has_access(access, organization_id, self.config.access_keys):
            raise ForgeError("forbidden", 403, account_id=account_id)
        def clean(value):
            return "".join(c for c in value if not unicodedata.category(c).startswith("C")).strip()[:128] if isinstance(value, str) else ""
        email = user.get("email")
        name = clean(user.get("name")) or clean(email.split("@", 1)[0] if isinstance(email, str) else "") or "user"
        return {"account_id": user["id"], "organization_id": organization_id, "name": name, "exp": token_exp(token)}


def _has_access(access, org, keys):
    return org in access["orgs"] and all(access["whitelist"].get(key) is True for key in keys)


class ForgeAccounts:
    """서버가 하나 만들어 작업 실행기(JobRunner(forge=…))와 라우트에 넘기는 창구. tokens = 서버의 세션 표(토큰 조회·거절 표시)."""

    def __init__(self, client: ForgeClient, config: ForgeConfig, tokens, *, clock=time.time):
        self.client, self.config, self.tokens, self.clock = client, config, tokens, clock

    def __repr__(self):
        return "ForgeAccounts()"

    def context(self, scope: dict) -> dict:
        """그 계정의 Forge 문맥 → {"client", "token_of": () -> str | None, "reject_token": (token) -> None,
        "organization_id", "options": {"prompt_autopilot", "concurrency"}}. 실행기가 "root"·"stop" 을 더해 runner.set_forge 에 건다."""
        org, acct = scope["org"], scope["account"]
        return {"client": self.client, "token_of": lambda: self.tokens.token_for(org, acct),
                "reject_token": lambda token: self.tokens.reject_token(org, acct, token), "organization_id": org,
                "options": {"prompt_autopilot": self.config.prompt_autopilot, "concurrency": self.config.concurrency}}

    async def ensure_access(self, scope: dict) -> None:
        """유료 제출 전 확인: 쓸 토큰이 있고, 마지막 권한 확인이 ACCESS_TTL 을 넘겼으면 access/summary 를 다시 본다.
        실패는 ForgeError(unauthorized·forbidden·unreachable…)."""
        org, acct = scope["org"], scope["account"]
        token = self.tokens.token_for(org, acct)
        if token is None:
            raise ForgeError("unauthorized", 401)
        checked = self.tokens.access_at(org, acct)
        if checked is not None and 0 <= self.clock() - checked < ACCESS_TTL:
            return
        try:
            access = await self.client.access(token)
        except ForgeError as error:
            if error.reason == "unauthorized":
                self.tokens.reject_token(org, acct, token)
            elif error.reason == "forbidden":
                self.tokens.drop_account(org, acct)
            raise
        if not _has_access(access, org, self.config.access_keys):
            self.tokens.drop_account(org, acct)
            raise ForgeError("forbidden", 403)
        self.tokens.mark_access(org, acct, self.clock())


def routes(accounts: ForgeAccounts) -> tuple:
    """「Forge에서 가져오기」·[Forge 자료에 저장] 라우트((method, path, handler)…) — 갈래 C 가 채운다."""
    return ()
