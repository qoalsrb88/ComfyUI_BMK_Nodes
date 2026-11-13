"""Design Patch Studio — 개발용 가짜 Forge(서버 모드 F2 의 개발·시험 전용). 운영에서는 쓰지 않는다:
bmk_design_patch_server 가 --dev-handoff + --dev-parent-port 로 띄운 가짜 부모(8790)에만 붙는다.

FakeForge.handle 은 소켓을 쓰지 않는다 — bmk_design_patch_forge.ForgeClient(transport=fake.handle) 로 끼우면 시험이
네트워크 없이 돌고, mount() 가 같은 구현을 8790 의 HTTP 라우트로 내놓는다. 응답 모양 정본 = tools/fixtures/forge_api.json.

토큰: `dev:<계정>`(만료 없음) · `dev:<계정>:<만료 epoch 초>`. 사용자 id = `u_<계정>`, 조직 = org1(이름 「개발 조직」).
시나리오는 프롬프트 속 표지로 건다(여러 개 가능): [[fake:delay=초]] [[fake:queue=순번/전체]] [[fake:partial=장수]]
[[fake:fail=user|ailabs]] [[fake:changed]] [[fake:empty]] — 표지가 없으면 곧바로 completed.
"""

from __future__ import annotations

import time


def issue_token(account: str, ttl: float | None = None, *, clock=time.time) -> str:
    """개발 토큰. ttl 이 None 이면 만료 없는 `dev:<계정>`."""
    raise NotImplementedError


class FakeForge:
    def __init__(self, *, media_origin: str = "http://localhost:8790", clock=time.time):
        raise NotImplementedError

    async def handle(self, method: str, url: str, headers: dict, body: bytes | None = None,
                     timeout: float | None = None) -> tuple[int, dict, bytes]:
        """ForgeClient 의 전송 함수 모양. 모르는 경로는 404 {"detail": "Not Found"}."""
        raise NotImplementedError


def mount(app, config, fake: FakeForge | None = None):
    """가짜 부모 앱(aiohttp)에 가짜 Forge API 라우트와 토큰 갱신 페이지(/refresh)를 붙인다 → FakeForge. 갈래 D 가 채운다."""
    return None
