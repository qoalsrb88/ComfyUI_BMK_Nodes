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


def blocked_reason(job: dict) -> str | None:
    """forge 백엔드의 과금 전 판정 몫 — 매핑 표에 없는 모델 · 프롬프트의 `@@` · 입력 MAX_INPUTS 초과. 없으면 None."""
    raise NotImplementedError


def encode_inputs(input_paths: list[str]) -> list[bytes]:
    """입력 파일 → PNG 바이트(순서 유지). RGB 변환 → 총 픽셀이 2048² 를 넘을 때만 축소. torch 를 쓰지 않는다."""
    raise NotImplementedError


def build_request(job: dict, images: list[bytes], *, organization_id: str, request_id: str, prompt_autopilot) -> dict:
    """Forge POST /api/image/generate 본문(fixture forge_api.json 의 generate.request 모양 — 그 밖의 키를 넣지 않는다)."""
    raise NotImplementedError


def read_sidecar(root: str, cell_key: str, rep: int) -> dict:
    """사이드카를 읽는다(없거나 깨졌으면 빈 기록)."""
    raise NotImplementedError


def prompt_changed(root: str, call_id: str) -> list[bool]:
    """그 호출의 슬롯별 '서버가 프롬프트를 바꿈' 표지(보드 후보 응답이 읽는다). 기록이 없으면 []."""
    raise NotImplementedError


def progress(root: str) -> dict | None:
    """보드 GET pipeline/progress 의 forge 값 → {"queue": {"position", "total"} | None, "auth_wait": bool}."""
    raise NotImplementedError


class ForgeBackend:
    name = "forge"

    def __init__(self, ctx: dict):
        """ctx = runner.forge_context() — {"client", "token_of", "reject_token", "organization_id", "root", "stop", "options"}."""
        raise NotImplementedError

    def __repr__(self) -> str:
        return "ForgeBackend()"

    def check(self) -> None:
        raise NotImplementedError

    async def request(self, job: dict, input_paths: list[str], call_id: str) -> dict:
        """→ {"data": [{"url": "forge-job:<job_id>/<slot>", "b64": None}…], "credits": None, "usage": None, "ctx": {…}}."""
        raise NotImplementedError

    async def download(self, url: str, ctx=None) -> bytes:
        raise NotImplementedError
