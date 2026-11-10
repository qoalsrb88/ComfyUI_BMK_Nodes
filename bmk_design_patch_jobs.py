"""BMK Design Patch — 프로세스 안 작업 실행기(Forge 이식 F1, _proto/STUDIO_FORGE_F1_SPEC.md §3.3 D327·D338).

서버 모드에는 ComfyUI 큐가 없다. 반영·Export·초안·Run 이 하던 일을, 서버가 만든 **같은 템플릿**(board.pipeline_prompt →
pipeline.build_prompt — 승인·차단 판정까지 끝난 것)을 이 프로세스 안에서 차례로 실행해 대신한다. 각 노드는 노드 클래스의 FUNCTION
메서드를 그대로 부른다(로컬판과 같은 코드가 돈다 = 합성 결과·Export 해시·생성 조건 키가 같다).

- execute(prompt): 템플릿 하나를 끝까지(큐 없음). 노드 키 = pipeline.NODE_KEYS 값만, class_type = pipeline.CLASS 값만.
- JobRunner: 프로젝트당 직렬(FIFO) + 서버 전체 동시 실행 workers(F1 기본 1 — 노드·보드의 전역 자원이 '한 번에 하나'를 전제로 한다,
  D338). Export 는 계정당 한 번에 하나, 계정당 대기 작업 max_pending. 대기열·기록 모양은 ComfyUI /api/queue·/api/history/<id> 중
  Studio 가 읽는 부분과 같다(tools/fixtures/server_api.json — pipeline.js 의 place·record·endOf·paidReport 를 그대로 쓴다).
- routes(runner): POST jobs/prompt · GET·POST jobs/queue · GET jobs/history/{prompt_id}. 화면은 템플릿을 보내지 않는다 —
  {root, stage, expect?, approve?, only?} 만 보내고 서버가 board.pipeline_prompt 로 판정·템플릿을 만든다.

공용 자리(감독관) — 머리만. 구현은 F1 갈래 J.
"""

from __future__ import annotations

try:
    from . import bmk_design_patch_board as board
    from . import bmk_design_patch_pipeline as pl
except ImportError:  # 단독 실행/테스트
    import bmk_design_patch_board as board
    import bmk_design_patch_pipeline as pl

API = board.API


def execute(prompt: dict, *, hidden: dict | None = None) -> dict:
    """템플릿 하나를 차례로 실행 → {"outputs": {노드 키: ui dict}}. 큐·스레드를 만들지 않는다(부르는 스레드에서 끝까지).
    실행 전 검사에 걸리면 ValueError, 노드가 낸 예외는 그대로 올리되 어느 노드였는지(node_id·node_type)를 예외에 붙인다.
    캐시 없음 — IS_CHANGED 를 부르지 않고 모든 노드를 실행한다(D343)."""
    raise NotImplementedError


class JobRunner:
    """프로젝트당 직렬 작업 큐. scope = board.current_scope() 모양(작업 스레드가 그 범위를 건다)."""

    def __init__(self, *, workers: int = 1, keep: int = 200, max_pending: int = 8):
        raise NotImplementedError

    def submit(self, scope: dict, root: str, stage: str, prompt: dict) -> dict:
        """작업을 줄에 넣는다 → {"prompt_id": str, "number": int}."""
        raise NotImplementedError

    def queue(self, scope: dict, root: str) -> dict:
        """{"queue_running": [[number, prompt_id, stage]], "queue_pending": […]} — 이 프로젝트 것 + 앞에 선 남의 작업은 [number, "", ""]."""
        raise NotImplementedError

    def delete(self, scope: dict, root: str, ids: list[str]) -> list[str]:
        """대기 중인 작업만 지운다 → 지운 prompt_id 목록(실행 중·끝난 것·남의 것은 건드리지 않음)."""
        raise NotImplementedError

    def history(self, scope: dict, root: str, prompt_id: str) -> dict:
        """{} (없음·아직) 또는 {prompt_id: {"status": {...}, "outputs": {...}}}."""
        raise NotImplementedError

    def close(self) -> None:
        """작업 스레드를 멈춘다(대기 중인 것은 버림)."""
        raise NotImplementedError


def routes(runner: JobRunner) -> tuple:
    """서버 진입점이 자기 앱에 붙일 (method, path, handler) 넷 — 보드 _ROUTES 에는 넣지 않는다(D334)."""
    raise NotImplementedError
