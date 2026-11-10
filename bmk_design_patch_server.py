"""BMK Design Patch Studio — 서버 모드 진입점(Forge 이식 F1, _proto/STUDIO_FORGE_F1_SPEC.md).

ComfyUI 없이 도는 독립 aiohttp 서버. 사내 툴 Forge 가 iframe 으로 끼우는 '별도 확장 메뉴'용이다.
- 보드 라우트(bmk_design_patch_board.register_routes)를 자기 앱에 붙이고, 요청마다 세션 계정의 범위(board.set_scope)를 건다.
- 세션: Forge 가 준 해시 토큰 → POST /api/session → 세션 쿠키(D332). 토큰 검증기는 갈아 끼운다 — F1 은 개발용(DevVerifier)만.
- 요청 검사(D333): 세션 · GET 아닌 요청의 전용 헤더 + Origin 대조 · Host 허용 목록 · frame-ancestors.
- 계정별 저장(D329): <data>/<org>/<acct>/bmk_design_patch/<프로젝트>, 출력 <홈>/_output, 화면 설정 <홈>/_prefs.json.
- 실행기(bmk_design_patch_jobs)의 라우트·화면 설정·Export 내려받기·개발용 가짜 부모 페이지.

띄우기(개발):
    python_embeded\\python.exe -X utf8 bmk_design_patch_server.py --data <폴더> --port 8791 --dev-handoff --dev-parent-port 8790

공용 자리(감독관) — 머리만. 구현은 F1 갈래 S.
"""

from __future__ import annotations

import dataclasses

from aiohttp import web


@dataclasses.dataclass
class ServerConfig:
    data_dir: str                              # 계정 홈들이 놓이는 폴더
    host: str = "127.0.0.1"
    port: int = 8791
    public_origin: str | None = None           # 서버 자기 origin(예 https://designpatch.example). 개발 모드(dev_handoff)가 아니면 필수(D344)
    parent_origins: tuple = ()                 # iframe 부모로 허용할 origin 들(frame-ancestors)
    allowed_hosts: tuple = ()                  # 허용 Host(비면 localhost·127.0.0.1 + public_origin 의 host)
    dev_handoff: bool = False                  # 개발용 가짜 넘겨받기("dev:<account_id>" 토큰) 허용
    cookie_samesite: str = "Lax"
    idle_ttl: int = 43200                      # 세션 유휴 만료(초)
    max_ttl: int = 604800                      # 세션 절대 만료(초)
    workers: int = 1                           # 실행기 동시 실행 수(F1 기본 1 — D338)
    dev_parent_port: int | None = None         # 개발용 가짜 부모 페이지 포트(없으면 띄우지 않음)


class DevVerifier:
    """개발용 토큰 검증기 — "dev:<account_id>" 꼴만 받는다(ServerConfig.dev_handoff 일 때만 쓴다)."""

    async def verify(self, token: str, organization_id: str) -> dict | None:
        """→ {"account_id", "organization_id", "name"} | None."""
        raise NotImplementedError


def make_app(config: ServerConfig, *, verifier=None) -> web.Application:
    """서버 모드 앱. verifier 가 None 이고 dev_handoff 면 DevVerifier, 둘 다 없으면 POST /api/session 은 503."""
    raise NotImplementedError


def main(argv=None) -> int:
    raise NotImplementedError


if __name__ == "__main__":
    raise SystemExit(main())
