"""BMK Design Patch — ComfyUI 대체물(Forge 이식 F1, _proto/STUDIO_FORGE_F1_SPEC.md §3.3 D327·D339).

서버 모드(bmk_design_patch_server — ComfyUI 없는 독립 서버)에서 노드 모듈(bmk_design_patch)이 import 될 수 있게, 그 모듈이 쓰는
ComfyUI 이름만 흉내 낸다. 노드 모듈은 **서버 모드 플래그(환경 변수 BMK_DP_SERVER_MODE=1)가 켜져 있을 때만** 이것을 쓴다 — 플래그가
꺼져 있으면(로컬판) 지금 import 문 그대로 진짜 ComfyUI 모듈이 들어온다. 'import 실패'로 가르지 않는다: ComfyUI 가 import 되는
환경(python_embeded)에서 서버를 띄워도 대체물을 쓴다.

- `server_mode()` — 환경 변수 판정. runner·vlm·노드 모듈이 board 를 import 하지 않고 읽는다.
- `comfy.utils.ProgressBar` — 같은 메서드(update·update_absolute). 값은 진행 받개(set_progress_sink — contextvar)로 넘긴다.
- `comfy.model_management.InterruptProcessingException` · `processing_interrupted()`(늘 False — 서버 모드에는 ComfyUI Cancel 이 없다).
- `folder_paths.get_input_directory()`·`get_output_directory()` — 늘 RuntimeError(전역 기본 폴더 없음: 서버 모드의 경로는 계정 범위
  board.current_scope() 로만 정한다 — 노드 모듈의 _project_root·_output_dir 가 범위를 먼저 본다). `models_dir` = "" ·
  `get_filename_list(kind)` = [].

이 모듈은 표준 라이브러리만 쓰고(torch·comfy·aiohttp 없음) sys.modules 에 comfy·folder_paths 이름으로 등록하지 않는다
(runner 가 sys.modules["comfy.model_management"] 를 직접 본다).

공용 자리(감독관) — server_mode 만 구현, 나머지는 머리만. 구현은 F1 갈래 J.
"""

from __future__ import annotations

import contextvars
import os
import types

SERVER_ENV = "BMK_DP_SERVER_MODE"  # bmk_design_patch_board.SERVER_ENV 와 같은 이름
_PROGRESS = contextvars.ContextVar("bmk_dp_progress", default=None)


def server_mode() -> bool:
    """서버 모드 프로세스인가(D339) — 환경 변수 하나로 판정한다."""
    return os.environ.get(SERVER_ENV, "") == "1"


class InterruptProcessingException(BaseException):
    """comfy.model_management.InterruptProcessingException 자리(BaseException — 노드의 except 절이 같은 꼴로 잡는다)."""


def set_progress_sink(fn) -> contextvars.Token:
    """지금 문맥의 진행 받개 fn(value, total) | None. 실행기가 작업 스레드에서 건다(돌려준 토큰으로 되돌림)."""
    return _PROGRESS.set(fn)


class ProgressBar:
    """comfy.utils.ProgressBar 자리."""

    def __init__(self, total, node_id=None):
        self.total = total
        self.current = 0
        self.node_id = node_id

    def update(self, value=1):
        self.update_absolute(self.current + value)

    def update_absolute(self, value, total=None, preview=None):
        if total is not None:
            self.total = total
        self.current = min(value, self.total)
        sink = _PROGRESS.get()
        if sink is not None:
            try:
                sink(self.current, self.total)
            except BaseException:  # 표시 받개의 실패로 노드 실행을 끊지 않는다
                pass


def _no_directory():
    raise RuntimeError("서버 모드에는 전역 기본 폴더가 없습니다 — 계정 범위가 필요합니다")


# 노드 모듈이 `comfy`·`folder_paths` 이름으로 쓰는 대체 객체(갈래 J 가 채운다)
comfy = types.SimpleNamespace(utils=types.SimpleNamespace(ProgressBar=ProgressBar),
                              model_management=types.SimpleNamespace(InterruptProcessingException=InterruptProcessingException,
                                                                     processing_interrupted=lambda: False))
folder_paths = types.SimpleNamespace(models_dir="", get_input_directory=_no_directory,
                                     get_output_directory=_no_directory, get_filename_list=lambda kind: [])
