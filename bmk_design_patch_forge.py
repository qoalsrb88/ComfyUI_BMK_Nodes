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

import dataclasses
import time
from typing import Any, Awaitable, Callable

JSON_LIMIT = 8 * 1024 ** 2      # 압축을 푼 뒤의 JSON 응답 상한
MEDIA_LIMIT = 64 * 1024 ** 2    # 결과 그림 한 장 상한
ACCESS_TTL = 1800.0             # 메뉴 권한을 다시 보는 간격(초)


@dataclasses.dataclass(frozen=True)
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


class ForgeError(Exception):
    """Forge 호출 실패. reason ∈ REASONS, status = HTTP 상태(없으면 None), detail = 화면에 보여도 되는 짧은 글(없으면 None).
    str(e) 는 reason 별 고정 문구다 — 토큰·주소·본문이 들어가지 않는다."""

    REASONS = ("unauthorized", "forbidden", "invalid", "not_found", "unreachable", "server")

    def __init__(self, reason: str, status: int | None = None, detail: str | None = None):
        raise NotImplementedError


# 전송 함수: async (method, url, headers, body, timeout) -> (status, headers, body). 시험·개발용 가짜 Forge 가 끼운다.
Transport = Callable[[str, str, dict, "bytes | None", float], Awaitable["tuple[int, dict, bytes]"]]


def token_exp(token: str) -> float | None:
    """토큰의 만료 시각(epoch 초). JWT 면 payload 의 exp 를 서명 검증 없이 읽고(검증은 /auth/me 가 한다), 개발 토큰
    `dev:<계정>:<epoch>` 면 그 값, 그 밖은 None. 예외를 내지 않는다."""
    raise NotImplementedError


def normalize_origin(value: str) -> str:
    """origin 비교용 정규화 — scheme·IDNA 소문자 호스트(끝 점 제거)·실제 포트. userinfo·경로·쿼리가 있으면 ValueError."""
    raise NotImplementedError


class ForgeClient:
    """설정 + 전송 함수. 인스턴스는 이벤트 루프에 묶이지 않는다(스레드·루프를 넘겨 써도 된다)."""

    def __init__(self, config: ForgeConfig, transport: Transport | None = None):
        raise NotImplementedError

    def __repr__(self) -> str:  # 설정의 주소를 싣지 않는다
        return "ForgeClient()"

    async def me(self, token: str) -> dict:
        """GET /auth/me → {"id", "email", "name"}. 401·authenticated 가 참이 아님 → ForgeError("unauthorized")."""
        raise NotImplementedError

    async def access(self, token: str) -> dict:
        """GET /api/access/summary → {"orgs": [id…], "whitelist": {키: bool}}."""
        raise NotImplementedError

    async def submit(self, token: str, body: dict) -> dict:
        """POST /api/image/generate → {"job_id", "status"}. 4xx 는 ForgeError(reason, status, detail=가린 detail)."""
        raise NotImplementedError

    async def job(self, token: str, job_id: str) -> dict:
        """GET /api/image/jobs/{id} → {"status", "queue_position", "queue_total", "execution_deadline_at", "images": [url…],
        "result_ids": […], "metadata": [{…}…], "error_message", "error_category"} — request_data 는 버린다."""
        raise NotImplementedError

    async def fetch_media(self, url: str, limit: int = MEDIA_LIMIT) -> bytes:
        """media_origins 에 든 origin 의 그림만 받는다(Authorization 없이). 밖이면 ForgeError("forbidden")."""
        raise NotImplementedError

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
        raise NotImplementedError

    async def verify(self, token: str, organization_id: str) -> dict:
        """→ {"account_id", "organization_id", "name", "exp": float | None}."""
        raise NotImplementedError


class ForgeAccounts:
    """서버가 하나 만들어 작업 실행기(JobRunner(forge=…))와 라우트에 넘기는 창구. tokens = 서버의 세션 표(토큰 조회·거절 표시)."""

    def __init__(self, client: ForgeClient, config: ForgeConfig, tokens, *, clock=time.time):
        raise NotImplementedError

    def context(self, scope: dict) -> dict:
        """그 계정의 Forge 문맥 → {"client", "token_of": () -> str | None, "reject_token": (token) -> None,
        "organization_id", "options": {"prompt_autopilot", "concurrency"}}. 실행기가 "root"·"stop" 을 더해 runner.set_forge 에 건다."""
        raise NotImplementedError

    async def ensure_access(self, scope: dict) -> None:
        """유료 제출 전 확인: 쓸 토큰이 있고, 마지막 권한 확인이 ACCESS_TTL 을 넘겼으면 access/summary 를 다시 본다.
        실패는 ForgeError(unauthorized·forbidden·unreachable…)."""
        raise NotImplementedError


def routes(accounts: ForgeAccounts) -> tuple:
    """「Forge에서 가져오기」·[Forge 자료에 저장] 라우트((method, path, handler)…) — 갈래 C 가 채운다."""
    return ()
