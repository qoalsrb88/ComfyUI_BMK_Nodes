"""Design Patch Studio — Forge 연결(서버 모드 F2): 설정 · HTTP 클라이언트 · 토큰 검증기 · 계정 창구.

Forge 와의 HTTP 결합은 이 모듈에만 둔다(요청·응답 모양 정본 = tools/fixtures/forge_api.json). 로컬판(ComfyUI 안)은 이 모듈을
import 하지 않는다. 실제 Forge 주소는 코드·문서·fixture 어디에도 적지 않는다 — 실행 옵션(--forge-api …)으로만 받는다.

지킬 것
- 토큰은 인자로만 받고 어디에도 보관하지 않는다(보관은 서버의 세션 표 — bmk_design_patch_server). 토큰·Authorization·요청/응답
  본문·Forge 주소를 로그·예외 문구·repr 에 싣지 않는다. aiohttp 예외는 밖으로 내보내지 않고 고정 문구의 ForgeError 로 바꾼다
  (`raise … from None`).
- 실패는 종류(ForgeError.kind — 연결 실패·시간 초과·상태 코드·응답 형식)를 가려 로그에 한 줄 남긴다(_log_failure). 그 줄에
  싣는 것은 호출 이름(코드에 적힌 고정 낱말)·종류·상태 코드·걸린 시간·예외 클래스 이름·errno 뿐이다 — 예외 문구(str)는
  주소가 들어 있을 수 있어 싣지 않는다.
- HTTP 세션은 호출마다 열고 닫는다 — 검증기는 서버 이벤트 루프에서, 생성 백엔드는 작업 스레드의 asyncio.run(노드마다 새 루프)
  에서 부른다. 세션을 나눠 쓰지 않는다. 쿠키 저장 없음(DummyCookieJar), 환경 변수 프록시 없음(trust_env=False), 리다이렉트를
  따라가지 않는다.
"""

from __future__ import annotations

import asyncio
import base64
import dataclasses
import hashlib
import hmac
import json
import logging
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

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True, repr=False)
class ForgeConfig:
    api: str                                   # Forge API origin(https — 예외는 allow_http·allow_private_api_http)
    media_origins: tuple = ()                  # 결과·자료 그림을 받아도 되는 origin 들(https — 예외는 allow_http 뿐)
    access_keys: tuple = ()                    # access/summary 의 serviceWhitelist 에서 전부 참이어야 하는 키(비면 조직 소속만)
    timeout: float = 30.0                      # 호출 하나의 시간 제한(초)
    verify_timeout: float = 8.0                # 검증(/auth/me + access/summary) 합계 상한(초)
    concurrency: int = 2                       # 한 Run 이 동시에 제출해 두는 잡 수
    prompt_autopilot: Any = False              # 생성 요청의 prompt_autopilot 키 값(자동 재작성 대상에서 빠지는 표지)
    session_message: str = "design-patch-session"            # 부모 → 앱 토큰 갱신 메시지 type
    request_message: str = "design-patch-session-request"    # 앱 → 부모 토큰 요청 메시지 type
    allow_private_api_http: bool = False       # 같은 망 안의 Forge 를 http 로 부를 때. API origin 에만 듣는다 — 그림 origin 은
    #                                            API 와 같은 주소여도 https 여야 한다(fetch_media 가 http 그림을 받지 않는다)
    allow_http: bool = False                   # 개발 모드(--dev-handoff)에서만 참

    def __post_init__(self):
        api = normalize_origin(self.api)
        if not isinstance(self.media_origins, (tuple, list)) or not isinstance(self.access_keys, (tuple, list)):
            raise ValueError("Forge origin·접근 키는 목록이어야 합니다")
        media = tuple(dict.fromkeys(normalize_origin(value) for value in self.media_origins))
        if type(self.allow_private_api_http) is not bool:
            raise ValueError("Forge 내부망 HTTP 설정은 참·거짓이어야 합니다")
        if not _allowed_scheme(URL(api), self.allow_http) and not (
                self.allow_private_api_http and URL(api).scheme == "http"):
            raise ValueError("Forge 주소는 HTTPS 또는 개발용 루프백 HTTP 여야 합니다")
        # 그림 origin 은 API 와 따로 본다 — API 에만 준 http 예외가 같은 주소의 그림 origin 까지 통과시키면,
        # 설정은 받아들여지는데 결과 그림은 fetch_media 에서 모두 거절된다.
        if any(not _allowed_scheme(URL(value), self.allow_http) for value in media):
            raise ValueError("Forge 그림 주소는 HTTPS 또는 개발용 루프백 HTTP 여야 합니다")
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
    str(e) 는 reason 별 고정 문구다 — 토큰·주소·본문이 들어가지 않는다.
    kind ∈ KINDS | None = 실패의 종류(진단용 — 분류·과금 판정은 reason·status 만 본다). note = 그 종류를 안내 문구 끝에 붙일
    짧은 글(고정 낱말 + 상태 코드 숫자뿐)."""

    REASONS = ("unauthorized", "forbidden", "invalid", "not_found", "unreachable", "server")
    KINDS = ("connect", "timeout", "status", "format")

    def __init__(self, reason: str, status: int | None = None, detail: str | None = None, account_id: str | None = None,
                 *, kind: str | None = None):
        self.reason = reason if reason in self.REASONS else "unreachable"
        self.status = status if type(status) is int else None
        self.detail = _safe_detail(detail)
        self.account_id = account_id
        self.kind = kind if isinstance(kind, str) and kind in self.KINDS and (kind != "status" or self.status is not None) else None
        super().__init__({
            "unauthorized": "Forge 로그인 갱신이 필요합니다.",
            "forbidden": "디자인 패치 스튜디오 사용 권한이 없습니다.",
            "invalid": "Forge 가 요청을 받아들이지 못했습니다.",
            "not_found": "Forge 에서 결과를 찾지 못했습니다.",
            "unreachable": "Forge 에 연결하지 못했습니다.",
            "server": "Forge 응답을 받지 못했습니다.",
        }[self.reason])

    @property
    def note(self) -> str | None:
        kind = getattr(self, "kind", None)  # __init__ 을 거치지 않은 하위 클래스(시험용 가짜)도 안내를 깨지 않게
        if kind == "status":
            return f"상태 코드 {self.status}" if type(getattr(self, "status", None)) is int else None
        return {"connect": "연결 실패", "timeout": "시간 초과", "format": "응답 형식"}.get(kind) if isinstance(kind, str) else None


def with_note(message: str, error) -> str:
    """안내 문구 끝에 실패 종류를 붙인다 — 「… 다시 시도해 주세요. (시간 초과)」. 종류를 모르면 문구 그대로."""
    note = error.note if isinstance(error, ForgeError) else None
    return f"{message} ({note})" if note else message


def _log_failure(call: str, kind: str, started: float | None = None, *, status: int | None = None,
                 exc_name: str | None = None, errno: int | None = None, level: int = logging.WARNING) -> None:
    """실패 한 줄(모듈 머리말의 「지킬 것」). call = 이 모듈에 적힌 고정 낱말, 나머지는 숫자와 클래스 이름뿐이다."""
    parts = ["call=" + call, "kind=" + kind]
    if type(status) is int:
        parts.append(f"status={status}")
    if type(started) in (int, float):
        parts.append(f"elapsed={max(0.0, time.monotonic() - started):.2f}s")
    if isinstance(exc_name, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", exc_name):
        parts.append("exc=" + exc_name)
    if type(errno) is int:
        parts.append(f"errno={errno}")
    logger.log(level, "Forge 호출 실패 — %s", " ".join(parts))


def _failure(call: str, kind: str, started: float | None = None, *, reason: str = "unreachable",
             status: int | None = None) -> ForgeError:
    """로그 한 줄 + 그 종류의 ForgeError(부르는 쪽이 `raise … from None`)."""
    _log_failure(call, kind, started, status=status)
    return ForgeError(reason, status, kind=kind)


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

    async def _exchange(self, method, url, headers, body, limit, call="?"):
        reason = None
        failure_status = None
        kind = exc_name = errno = None
        started = time.monotonic()
        try:
            if self._transport is None:
                result = await _http(method, url, headers, body, self.config.timeout, limit)
            else:
                result = await asyncio.wait_for(self._transport(method, url, headers, body, self.config.timeout),
                                                timeout=self.config.timeout)
            status, response_headers, raw = result
            if type(status) is not int or not isinstance(response_headers, dict) or not isinstance(raw, bytes):
                reason, kind = "unreachable", "format"
            elif len(raw) > limit:
                reason, failure_status, kind = "server", 413, "format"
        except _BodyTooLarge:
            reason, failure_status, kind = "server", 413, "format"
        except Exception as exc:
            reason = "unreachable"
            # 종류·클래스 이름·errno 만 꺼낸다 — 예외 객체와 그 문구(주소·헤더가 들어 있을 수 있다)는 여기서 버린다.
            if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
                kind = "timeout"
            elif isinstance(exc, (aiohttp.ClientPayloadError, aiohttp.ClientResponseError)):
                kind = "format"
            elif isinstance(exc, (aiohttp.ClientError, OSError)):
                kind = "connect"
            exc_name = type(exc).__name__
            errno = getattr(exc, "errno", None)
            if type(errno) is not int:
                errno = getattr(getattr(exc, "os_error", None), "errno", None)
        # except 밖에서 새 예외를 만들어 __context__ 에도 원래 헤더·주소가 남지 않게 한다.
        if reason:
            # 413 은 우리가 정한 크기 상한에 걸린 것이다(Forge 가 준 상태 코드가 아니다) — 로그에는 종류만 적는다.
            _log_failure(call, kind or "other", started, exc_name=exc_name, errno=errno)
            raise ForgeError(reason, failure_status, kind=kind) from None
        return status, raw

    @staticmethod
    def _status(status, raw, token=None, call="?", started=None):
        if status == 200:
            return
        reason = {401: "unauthorized", 403: "forbidden", 404: "not_found",
                  400: "invalid", 409: "invalid", 413: "invalid", 422: "invalid"}.get(status)
        # 표에 든 4xx 는 화면이 따로 안내하는 흐름(로그인 갱신·권한·입력 오류)이라 INFO 로만 남긴다.
        _log_failure(call, "status", started, status=status, level=logging.INFO if reason else logging.WARNING)
        kind = None if reason else "status"
        reason = reason or ("server" if 500 <= status < 600 else "unreachable")
        detail = None
        if reason == "invalid":
            try:
                data = json.loads(raw)
                value = data.get("detail") if isinstance(data, dict) else None
                detail = _safe_detail(value.replace(token, "") if isinstance(value, str) and token else value)
            except Exception:
                pass
        raise ForgeError(reason, status, detail, kind=kind) from None

    async def _json(self, method, path, token, body=None, call=None):
        call = call or method + " " + path
        started = time.monotonic()
        headers = {"Authorization": "Bearer " + token}
        encoded = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
        url = str(URL(self.config.api).with_path(path))
        status, raw = await self._exchange(method, url, headers, encoded, JSON_LIMIT, call)
        self._status(status, raw, token, call, started)
        data = None
        try:
            data = json.loads(raw)
        except Exception:
            pass
        if not isinstance(data, dict):
            _log_failure(call, "format", started)
            raise ForgeError("unreachable", status, kind="format") from None
        return data

    def __repr__(self) -> str:  # 설정의 주소를 싣지 않는다
        return "ForgeClient()"

    async def me(self, token: str) -> dict:
        """GET /auth/me → {"id", "email", "name"}. 401·authenticated 가 참이 아님 → ForgeError("unauthorized")."""
        started = time.monotonic()
        data = await self._json("GET", "/auth/me", token)
        if data.get("authenticated") is not True:
            raise ForgeError("unauthorized", 200)
        user = data.get("user")
        if not isinstance(user, dict) or not isinstance(user.get("id"), str) or not user["id"]:
            _log_failure("GET /auth/me", "format", started)
            raise ForgeError("unreachable", 200, kind="format")
        return {key: user.get(key) for key in ("id", "email", "name")}

    async def access(self, token: str) -> dict:
        """GET /api/access/summary → {"orgs": [id…], "whitelist": {키: bool}}."""
        started = time.monotonic()
        data = await self._json("GET", "/api/access/summary", token)
        orgs, whitelist = data.get("organizations"), data.get("serviceWhitelist")
        if not isinstance(orgs, list) or not isinstance(whitelist, dict):
            _log_failure("GET /api/access/summary", "format", started)
            raise ForgeError("unreachable", 200, kind="format")
        return {"orgs": [org["id"] for org in orgs if isinstance(org, dict) and isinstance(org.get("id"), str)],
                "whitelist": dict(whitelist)}

    async def submit(self, token: str, body: dict) -> dict:
        """POST /api/image/generate → {"job_id", "status"}. 4xx 는 ForgeError(reason, status, detail=가린 detail)."""
        started = time.monotonic()
        data = await self._json("POST", "/api/image/generate", token, body)
        job_id = data.get("job_id")
        if job_id is None and isinstance(data.get("job_ids"), list) and data["job_ids"]:
            job_id = data["job_ids"][0]
        if not isinstance(job_id, str) or not job_id or data.get("status") not in ("pending", "processing", "completed", "failed"):
            _log_failure("POST /api/image/generate", "format", started)
            raise ForgeError("unreachable", 200, kind="format")
        return {"job_id": job_id, "status": data["status"]}

    async def job(self, token: str, job_id: str) -> dict:
        """GET /api/image/jobs/{id} → {"status", "queue_position", "queue_total", "execution_deadline_at", "images": [url…],
        "result_ids": […], "metadata": [{…}…], "error_message", "error_category"} — request_data 는 버린다."""
        if not isinstance(job_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", job_id):
            raise ForgeError("invalid")
        call, started = "GET /api/image/jobs/{id}", time.monotonic()  # 잡 id 는 로그에 싣지 않는다
        data = await self._json("GET", "/api/image/jobs/" + job_id, token, call=call)
        result = data.get("result_data")
        if data.get("status") not in ("pending", "processing", "completed", "failed") or (result is not None and not isinstance(result, dict)):
            _log_failure(call, "format", started)
            raise ForgeError("unreachable", 200, kind="format")
        result = result or {}
        images, ids, metadata = (result.get(key, []) for key in ("images", "result_ids", "result_metadata"))
        if (any(not isinstance(value, list) for value in (images, ids, metadata))
                or any(not isinstance(value, str) for value in images)
                or any(value is not None and not isinstance(value, str) for value in ids)
                or any(value is not None and not isinstance(value, dict) for value in metadata)):
            _log_failure(call, "format", started)
            raise ForgeError("unreachable", 200, kind="format")
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
        call, started = "GET (그림 받기)", time.monotonic()
        if (parsed is None or not _allowed_scheme(parsed, self.config.allow_http)
                or str(parsed.origin()) not in self.config.media_origins):
            _log_failure(call, "origin")  # 허용 목록 밖 주소 — 보내지 않았다. 주소는 적지 않는다
            raise ForgeError("forbidden") from None
        if type(limit) is not int or limit <= 0:
            raise ForgeError("invalid")
        status, raw = await self._exchange("GET", str(parsed), {}, None, min(limit, MEDIA_LIMIT), call)
        self._status(status, raw, None, call, started)
        return raw

    async def history(self, token: str, organization_id: str, *, cursor: str | None = None, search: str | None = None,
                      limit: int = 40) -> dict:
        """GET /api/image/history(view=summary) → {"items": [{"id", "url", "thumb", "prompt", "model", "created"}…],
        "next": cursor | None} — 묶음 항목은 장 단위로 편다."""
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ForgeError("invalid")
        query = {"organization_id": organization_id, "limit": limit, "view": "summary"}
        for key, value in (("cursor", cursor), ("search", search)):
            if value:
                if not isinstance(value, str):
                    raise ForgeError("invalid")
                query[key] = value
        url = str(URL(self.config.api).with_path("/api/image/history").with_query(query))
        call, started = "GET /api/image/history", time.monotonic()
        status, raw = await self._exchange("GET", url, {"Authorization": "Bearer " + token}, None, JSON_LIMIT, call)
        self._status(status, raw, token, call, started)
        data = None
        try:
            data = json.loads(raw)
        except (ValueError, UnicodeError, RecursionError):
            pass
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            _log_failure(call, "format", started)
            raise ForgeError("unreachable", status, kind="format")
        items = []
        for group in data["items"]:
            if not isinstance(group, dict):
                continue
            rows = group.get("items", [group])
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, dict) or not isinstance(row.get("result_url"), str) or not row["result_url"]:
                    continue
                items.append({"id": row.get("id"), "url": row["result_url"], "thumb": row.get("thumbnail_url"),
                              "prompt": row.get("prompt", group.get("prompt")),
                              "model": row.get("model_used", group.get("model_used")),
                              "created": row.get("created_at", group.get("created_at"))})
        return {"items": items, "next": data.get("next_cursor")
                if data.get("has_more") is True and isinstance(data.get("next_cursor"), str) else None}

    async def assets(self, token: str, organization_id: str, *, offset: int = 0, limit: int = 200,
                     include_pending: bool = False) -> dict:
        """GET /api/library/assets → {"items": [{"id", "url", "thumb", "name", "folder"}…], "next": offset | None}.
        기본은 그림만, 저장 충돌 조회(include_pending)는 아직 URL·매체가 없는 등록도 포함한다."""
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 200:
            raise ForgeError("invalid")
        url = str(URL(self.config.api).with_path("/api/library/assets").with_query(
            organization_id=organization_id, limit=limit, offset=offset))
        call, started = "GET /api/library/assets", time.monotonic()
        status, raw = await self._exchange("GET", url, {"Authorization": "Bearer " + token}, None, JSON_LIMIT, call)
        self._status(status, raw, token, call, started)
        data = None
        try:
            data = json.loads(raw)
        except (ValueError, UnicodeError, RecursionError):
            pass
        if not isinstance(data, list):
            _log_failure(call, "format", started)
            raise ForgeError("unreachable", status, kind="format")
        return {"items": [{"id": row.get("id"), "url": row.get("url"), "thumb": row.get("thumbnail_url"),
                           "name": row.get("name"), "folder": row.get("folder_path"), "tags": row.get("tags", []),
                           "client_request_id": row.get("client_request_id")}
                          for row in data if isinstance(row, dict) and (include_pending or (row.get("media_type") == "image"
                          and isinstance(row.get("url"), str) and row["url"]))],
                "next": offset + limit if len(data) == limit else None}

    async def upload_asset(self, token: str, organization_id: str, *, name: str, data: bytes, content_type: str,
                           client_request_id: str, width: int | None = None, height: int | None = None) -> dict:
        """upload-url → PUT → uploads/complete → POST /api/library/assets → {"id", "name"}."""
        if not isinstance(data, bytes) or not data:
            raise ForgeError("invalid")
        if len(data) > 25 * 1024 ** 2:
            raise ForgeError("invalid", 413)
        suffix = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}.get(content_type)
        if (suffix is None or not isinstance(name, str) or not name or not isinstance(client_request_id, str)
                or not client_request_id or any(v is not None and (type(v) is not int or v <= 0) for v in (width, height))):
            raise ForgeError("invalid")
        base = "/api/library/assets"
        started = time.monotonic()
        try:
            upload = await self._json("POST", base + "/upload-url", token,
                                      {"organization_id": organization_id, "filename": name + suffix,
                                       "content_type": content_type, "size_bytes": len(data), "kind": "asset"})
        except ForgeError as error:
            if error.status == 409:  # 등록 단계의 409 만 client_request_id 충돌이다.
                raise _failure("POST " + base + "/upload-url", "status", started, status=409) from None
            raise
        parsed = None
        try:
            parsed = _url(upload.get("upload_url"))
        except ValueError:
            pass
        key = upload.get("s3_key")
        if (parsed is None or not _allowed_scheme(parsed, self.config.allow_http)
                or not isinstance(key, str) or not key):
            raise _failure("POST " + base + "/upload-url", "format", started) from None
        # 이 요청만 늘린다. 공유 클라이언트의 설정·Forge API 제한은 바꾸지 않는다.
        put_timeout = min(300.0, max(self.config.timeout, len(data) / (256 * 1024)))
        storage = ForgeClient(dataclasses.replace(self.config, timeout=put_timeout), self._transport)
        put_call, put_started = "PUT (자료 올리기)", time.monotonic()
        status, _ = await storage._exchange("PUT", str(parsed), {"Content-Type": content_type}, data, JSON_LIMIT, put_call)
        if not 200 <= status < 300:
            raise _failure(put_call, "status", put_started, status=status)  # 저장소 오류는 Forge 인증·권한 오류가 아니다.
        started = time.monotonic()
        try:
            await self._json("POST", base + "/uploads/complete", token,
                             {"organization_id": organization_id, "s3_key": key})
        except ForgeError as error:
            if error.status == 409:
                raise _failure("POST " + base + "/uploads/complete", "status", started, status=409) from None
            raise
        body = {"organization_id": organization_id, "client_request_id": client_request_id,
                "name": name, "s3_key": key, "content_type": content_type}
        body.update({k: v for k, v in (("width", width), ("height", height)) if v is not None})
        started = time.monotonic()
        result = await self._json("POST", base, token, body)
        if not isinstance(result.get("id"), str) or not isinstance(result.get("name"), str):
            raise _failure("POST " + base, "format", started)
        if any(token and token in result[k] or upload["upload_url"] in result[k] for k in ("id", "name")):
            raise _failure("POST " + base, "format", started)
        return {"id": result["id"], "name": result["name"]}


class ForgeVerifier:
    """bmk_design_patch_server.make_app(verifier=…) 자리. 실패는 ForgeError 로 올린다(unauthorized → 401 · forbidden → 403 ·
    그 밖 → 503)."""

    def __init__(self, client: ForgeClient, config: ForgeConfig):
        self.client, self.config = client, config

    async def verify(self, token: str, organization_id: str) -> dict:
        """→ {"account_id", "organization_id", "name", "exp": float | None}."""
        started = time.monotonic()
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
        if pending:
            # 두 호출 가운데 답이 없는 것을 적는다 — 호출별 시간 제한(config.timeout)보다 검증 상한이 짧을 때 여기로 온다.
            waiting = "+".join(name for name, task in zip(("me", "access"), tasks) if task in pending)
            raise _failure("verify(" + waiting + ")", "timeout", started)
        failed = next((err for err in errors
                       if not isinstance(err, ForgeError) or err.reason not in ("unauthorized", "forbidden")), None)
        if failed is not None:
            if isinstance(failed, ForgeError):  # 호출이 이미 한 줄 남겼다 — 종류만 이어 준다
                raise ForgeError("unreachable", failed.status if failed.kind == "status" else None, kind=failed.kind)
            _log_failure("verify", "other", started, exc_name=type(failed).__name__)
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
        if not _valid_id(user.get("id")):
            raise _failure("GET /auth/me", "format", started)
        if not _valid_id(organization_id):
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
    """「Forge에서 가져오기」·[Forge 자료에 저장]. 원격 식별자·주소는 계정별 메모리에만 둔다."""
    import io
    import os
    import secrets
    from collections import OrderedDict
    from contextlib import asynccontextmanager, contextmanager
    from aiohttp import web
    from PIL import Image
    try:
        from . import bmk_design_patch_board as board, bmk_design_patch_forge_backend as backend
    except ImportError:
        import bmk_design_patch_board as board
        import bmk_design_patch_forge_backend as backend

    # Run 의 동시 제출 설정과 별개. 활동 중인 계정은 LRU 에서 밀어내지 않는다.
    account_limit, root_limit, entry_limit = 128, 128, 2000
    states = OrderedDict()
    slots = asyncio.Semaphore(8)
    save_slots = asyncio.Semaphore(2)
    messages = {
        "forge_auth": (401, "Forge 로그인 정보를 다시 받아야 합니다 — 잠시 뒤 자동으로 다시 시도합니다."),
        "no_access": (403, "디자인 패치 스튜디오 사용 권한이 없습니다. 담당자에게 문의해 주세요."),
        "forge_unreachable": (503, "Forge 에 연결하지 못했습니다 — 잠시 뒤 다시 시도해 주세요."),
        "forge_stale": (404, "목록이 오래됐습니다 — 다시 불러온 뒤 골라 주세요."),
        "no_export": (409, "먼저 Export 를 실행해 주세요."),
        "too_large": (413, "그림이 25MB 를 넘어 Forge 자료에 올릴 수 없습니다 — 내려받아 직접 올려 주세요."),
        "busy": (409, "앞선 저장이 끝난 뒤 다시 눌러 주세요"),
        "already_saved": (409, "이미 저장한 그림입니다 — Forge 내 자료에서 확인해 주세요"),
    }

    def reject(reason, error=None):
        status, message = messages[reason]
        raise board._Reject(status, with_note(message, error), reason=reason)

    @contextmanager
    def account_state(scope):
        key = (scope["org"], scope["account"])
        if key not in states:
            if len(states) >= account_limit:
                idle = next((k for k, state in states.items() if not state["active"]), None)
                if idle is None:
                    reject("busy")
                del states[idle]
            states[key] = {"rows": OrderedDict(), "saved": OrderedDict(), "saving": set(),
                           "heavy": asyncio.Semaphore(1), "thumb": asyncio.Semaphore(4),
                           "list": asyncio.Semaphore(2), "active": 0}
        state = states[key]
        states.move_to_end(key)
        state["active"] += 1  # 슬롯 대기 중에도 같은 계정의 제한·잠금을 보존한다.
        try:
            yield state
        finally:
            state["active"] -= 1

    @asynccontextmanager
    async def limited(state, action):
        local = state["heavy"] if action in ("import", "save") else state["thumb"] if action == "thumb" else state["list"]
        async with local:  # 계정 대기자가 서버 전체 슬롯을 차지하지 않는다.
            if action == "save":
                if save_slots.locked():
                    reject("busy")
                async with save_slots:
                    async with slots:
                        yield
            else:
                async with slots:
                    yield

    def table(scope):
        rows = states[scope["org"], scope["account"]]["rows"]
        now = accounts.clock()
        if rows and now < rows[next(reversed(rows))]["at"]:
            rows.clear()  # 시계가 뒤로 갔을 때도 그 계정만 만료시킨다.
        while rows:
            ident = next(iter(rows))
            if now - rows[ident]["at"] < 1800:
                break
            rows.popitem(last=False)
        return rows

    ident_key = secrets.token_bytes(32)  # 프로세스마다 새로 만든다 — 항목 ID 를 밖에서 계산할 수 없다.

    def remember(scope, row):
        rows = table(scope)
        source = row.get("source")
        if isinstance(source, str) and not source.startswith("page:"):
            # 같은 계정의 같은 원본 항목은 늘 같은 ID 를 받는다 — 다음 쪽에 다시 내려와도 화면이 한 번만 보여 준다
            # (forge_import.js 의 seen). 원본 id 가 있으면 주소는 쿼리(요청마다 바뀌는 서명 등)를 빼고 견준다.
            # 표에는 가장 새 행을 맨 뒤에 남긴다(만료·개수 제한이 넣은 순서를 본다).
            where = row.get("url")
            if isinstance(where, str) and row.get("id") is not None:
                where = where.split("#", 1)[0].split("?", 1)[0]
            material = json.dumps([scope["org"], scope["account"], source, row.get("id"), where],
                                  ensure_ascii=False, default=str)
            ident = base64.urlsafe_b64encode(hmac.new(
                ident_key, material.encode("utf-8", "surrogatepass"), hashlib.sha256).digest()[:24]).decode("ascii")
            rows.pop(ident, None)
        else:
            ident = secrets.token_urlsafe(24)  # 쪽 이음표는 요청마다 새로
        rows[ident] = dict(row, at=accounts.clock())
        while len(rows) > entry_limit:
            rows.popitem(last=False)
        return ident

    def lookup(scope, ident, source):
        row = table(scope).get(ident) if isinstance(ident, str) else None
        if row is None or row.get("source") != source:
            reject("forge_stale")
        return row

    def display(value, token, size=128):
        if not isinstance(value, str):
            return ""
        value = value.replace(token, "") if token else value
        value = "".join(c for c in value if not unicodedata.category(c).startswith("C"))
        value = re.sub(r"(?i)https?://[^\s<>]+", "", value)
        for origin in (accounts.config.api, *accounts.config.media_origins):
            value = re.sub(re.escape(URL(origin).host), "", value, flags=re.I)
        if re.search(r"[A-Za-z]:[\\/]|\\\\|(?:^|\s)/[^\s]+", value):
            return ""
        return value[:size]

    async def root_of(data, optional=False):
        if optional and not data.get("root"):
            return None
        return await asyncio.to_thread(board._root_param, data.get("root"))

    def picture(raw, shrink=False):
        # Content-Type 은 상류 헤더 대신 그림 서명과 디코드로 정한다.
        try:
            with board._RENDER_SLOTS, Image.open(io.BytesIO(raw)) as im:
                mime = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}.get(im.format)
                if mime is None or max(im.size) > board._MAX_IMAGE_SIDE or im.width * im.height > board._MAX_IMAGE_AREA:
                    raise ValueError
                im.load()
                if shrink or max(im.size) > 512:
                    im.thumbnail((256, 256), Image.Resampling.LANCZOS)
                    result = io.BytesIO()
                    im.convert("RGBA" if "A" in im.getbands() else "RGB").save(result, "PNG")
                    return result.getvalue(), "image/png"
                return raw, mime
        except Exception:
            raise board._Reject(422, "그림 파일을 읽을 수 없습니다(깨졌거나 지원하지 않는 형식)") from None

    def filename(raw, row, token):
        # 잘못된 그림은 기존 올리기 처리기가 같은 오류로 거절한다.
        suffix = ".png" if raw.startswith(b"\x89PNG\r\n\x1a\n") else ".jpg" if raw.startswith(b"\xff\xd8") else (
            ".webp" if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP" else ".png")
        name = display(row.get("name") or row.get("prompt"), token)
        name = re.sub(r'[<>:"/\\|?*]', "_", name).strip(" .")[:120] or "Forge"
        if name.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
            name = os.path.splitext(name)[0]
        try:
            return board.check_upload_name(name + suffix)
        except board._Reject:
            return board.check_upload_name("Forge" + suffix)

    async def listing(request, scope, ctx, token, source):
        query = request.query
        await root_of(query, optional=True)  # 새 프로젝트 창은 아직 root 가 없다.
        search = query.get("search", "")[:300]
        field = "cursor" if source == "history" else "offset"
        page = query.get(field)
        value = None if source == "history" else 0
        if page not in (None, "", "0"):
            row = lookup(scope, page, "page:" + source)
            if row["search"] != search:
                reject("forge_stale")
            value = row["value"]
        client, org = ctx["client"], ctx["organization_id"]
        if source == "history":
            result = await client.history(token, org, cursor=value, search=search)
        else:
            for _ in range(5):
                result = await client.assets(token, org, offset=value)
                result["items"] = [row for row in result["items"] if search.casefold() in " ".join(
                    str(row.get(k) or "") for k in ("name", "folder", "tags")).casefold()]
                if result["items"] or result["next"] is None:
                    break
                value = result["next"]
        rows = []
        for row in result["items"]:
            ident = remember(scope, dict(row, source=source))
            thumb = str(URL(board.API + "/forge/thumb").with_query(
                root=query.get("root", ""), source=source, id=ident))
            keys = ("prompt", "model", "created") if source == "history" else ("name", "folder")
            rows.append(dict(id=ident, thumb=thumb, **{
                key: display(row.get(key), token, 300 if key == "prompt" else 128) for key in keys}))
        next_page = remember(scope, {"source": "page:" + source, "value": result["next"], "search": search}) \
            if result["next"] is not None else None
        return board._json({"items": rows, "next": next_page})

    def export_info(root):
        record = board.store.last_export(root, board.store.load_manifest(root))
        if not record:
            reject("no_export")
        path = record.get("psd")
        # Export 는 별도 합성 PNG 대신 PSD 병합 프리뷰에 원해상도 flat 을 보존한다.
        # 현재 ★ 를 다시 렌더하면 Export 뒤의 편집·include_base 설정에 따라 다른 그림이 된다.
        output = board.output_dir()
        if (not isinstance(path, str) or not output
                or os.path.commonpath([os.path.realpath(path), os.path.realpath(output)]) != os.path.realpath(output)
                or not os.path.isfile(path)):
            reject("no_export")
        return path, os.path.splitext(os.path.basename(path))[0], f"dp-save-{backend._nonce(root)}-r{record['rev']}"

    def exported(path):
        if os.path.getsize(path) > 512 * 1024 ** 2:
            raise board._Reject(413, "Export 파일이 커서 서버에서 합성 그림을 만들 수 없습니다 — PSD 를 내려받아 직접 올려 주세요.",
                                reason="too_large")
        im = None
        try:
            with board._RENDER_SLOTS:
                psd = board.dpsd.open_psd(path)
                im = psd.topil()  # 저장된 병합 프리뷰만; 레이어를 재합성하지 않는다.
                del psd  # PNG 인코딩 동안 레이어 데이터 전체를 붙들지 않는다.
                if im is None:
                    raise board._Reject(422, "Export 합성 그림을 읽을 수 없습니다 — Export 를 다시 실행해 주세요.")
                im.load()
            with io.BytesIO() as buf:
                im.save(buf, "PNG")
                with buf.getbuffer() as view:
                    if view.nbytes > 25 * 1024 ** 2:
                        reject("too_large")
                    return bytes(view), im.size
        finally:
            if im is not None:
                im.close()

    async def save(root, scope, ctx, state):
        path, name, request_id = await asyncio.to_thread(export_info, root)
        cached = state["saved"]
        asset = cached.get(request_id)
        already = asset is not None
        token = ctx["token_of"]()
        if not token:
            reject("forge_auth")
        if asset is None:
            # 요청 취소도 디코드·인코드 스레드 종료를 기다린 뒤 저장 슬롯을 놓는다.
            raw, size = await board._project_new_thread(exported, path)
            try:
                token = ctx["token_of"]()
                if not token:
                    reject("forge_auth")
                try:
                    asset = await ctx["client"].upload_asset(token, ctx["organization_id"], name=name, data=raw,
                        content_type="image/png", width=size[0], height=size[1], client_request_id=request_id)
                except ForgeError as error:
                    if error.reason != "invalid" or error.status != 409:
                        raise
                    # 재시작·응답 유실로 캐시가 없을 때만 최신 5쪽 안에서 등록 결과를 복구한다.
                    offset = 0
                    for _ in range(5):
                        token = ctx["token_of"]()
                        if not token:
                            reject("forge_auth")
                        result = await ctx["client"].assets(token, ctx["organization_id"], offset=offset, include_pending=True)
                        asset = next((row for row in result["items"] if row.get("client_request_id") == request_id), None)
                        if asset is not None or result["next"] is None:
                            break
                        offset = result["next"]
                    if asset is None:
                        reject("already_saved")
                    already = True
            except ForgeError as error:
                if error.reason == "unauthorized":
                    ctx["reject_token"](token)  # 슬롯 대기·페이지 조회 중 갱신됐으면 실제 거절된 토큰만 표시한다.
                    reject("forge_auth")
                raise
            cached[request_id] = {"id": asset["id"], "name": asset.get("name")}
            while len(cached) > entry_limit:
                cached.popitem(last=False)
        cached.move_to_end(request_id)
        ident = remember(scope, {"source": "saved", "id": asset["id"]})
        result = {"ok": True, "asset": {"id": ident, "name": display(asset.get("name"), token)}}
        if already:
            result["already"] = True
        return board._json(result)

    def handler(action):
        @board._guard
        async def handle(request):
            scope = board.current_scope()
            if not board.server_mode() or not scope:
                raise board._Reject(401, "Forge에서 디자인 패치 스튜디오를 다시 열어 주세요.", reason="no_session")
            ctx = accounts.context(scope)
            token = ctx["token_of"]()
            if not token:
                reject("forge_auth")
            try:
                with account_state(scope) as state:
                    is_list = action in ("history", "assets")
                    data = request.query if is_list or action == "thumb" else await board._body(request)
                    root = None if is_list else await root_of(data, optional=action == "thumb" or (
                        action == "import" and data.get("into") == "target"))
                    if action == "save":
                        if root in state["saving"] or len(state["saving"]) >= root_limit:
                            reject("busy")
                        state["saving"].add(root)  # 기다리지 않고, 계정·전체 슬롯을 잡기 전에 중복을 거절한다.
                    try:
                        async with limited(state, action):
                            token = ctx["token_of"]()
                            if not token:
                                reject("forge_auth")
                            if action in ("import", "save"):
                                try:
                                    await accounts.ensure_access(scope)
                                except ForgeError as error:
                                    # ensure_access 가 토큰 거절·계정 회수를 이미 처리한다.
                                    reject({"unauthorized": "forge_auth", "forbidden": "no_access"}.get(
                                        error.reason, "forge_unreachable"), error)
                            if is_list:
                                return await listing(request, scope, ctx, token, "history" if action == "history" else "asset")
                            if action == "save":
                                return await save(root, scope, ctx, state)
                            source = data.get("source")
                            if source not in ("history", "asset"):
                                reject("forge_stale")
                            row = lookup(scope, data.get("id"), source)
                            if action == "thumb":
                                large = False
                                try:
                                    raw = await ctx["client"].fetch_media(row.get("thumb") or row["url"], 4 * 1024 ** 2)
                                except ForgeError as error:
                                    large = error.status == 413
                                    try:
                                        raw = await ctx["client"].fetch_media(row["url"])
                                    except ForgeError:
                                        reject("forge_stale")
                                raw, mime = await asyncio.to_thread(picture, raw, large)
                                return web.Response(body=raw, content_type=mime, headers={
                                    "Cache-Control": "private, max-age=600", "X-Content-Type-Options": "nosniff"})
                            if data.get("into") not in ("target", "refs"):
                                raise board._Reject(400, "가져올 자리를 확인하세요.")
                            try:
                                raw = await ctx["client"].fetch_media(row["url"])
                            except ForgeError as error:
                                if error.reason in ("forbidden", "unauthorized", "not_found") or error.status in (401, 403, 404):
                                    reject("forge_stale")
                                reject("forge_unreachable", error)
                            return await board.import_received(data, raw, filename(raw, row, token))
                    finally:
                        if action == "save":
                            state["saving"].discard(root)
            except ForgeError as error:
                if error.reason == "unauthorized":
                    ctx["reject_token"](token)
                    reject("forge_auth")
                if error.reason == "forbidden":
                    reject("no_access")
                if action == "save" and error.reason == "invalid" and error.status == 413:
                    reject("too_large")
                reject("forge_unreachable", error)
        return handle

    return tuple(("GET" if name in ("history", "assets", "thumb") else "POST",
                  board.API + "/forge/" + name, handler(name)) for name in ("history", "assets", "thumb", "import", "save"))
