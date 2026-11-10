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

import asyncio
import contextvars
import copy
import inspect
import itertools
import json
import logging
import os
import re
import secrets
import threading
import time
from collections import OrderedDict

try:
    from . import bmk_design_patch_board as board
    from . import bmk_design_patch_compat as compat
    from . import bmk_design_patch_pipeline as pl
    from . import bmk_design_patch_runner as backend
except ImportError:  # 단독 실행/테스트
    import bmk_design_patch_board as board
    import bmk_design_patch_compat as compat
    import bmk_design_patch_pipeline as pl
    import bmk_design_patch_runner as backend

API = board.API
logger = logging.getLogger(__name__)
_NUMBERS = itertools.count(1)
_NUMBER_LOCK = threading.Lock()
_STATE = contextvars.ContextVar("bmk_dp_job", default=None)
_PROMPT_ID = re.compile(r"j_[0-9a-f]{16}")


class QueueFull(Exception):
    """이 계정의 대기 작업 상한에 닿았다."""


def _node_classes():
    """실행할 때만 노드를 읽는다. 시험은 이 함수만 가짜 클래스 표로 바꿔 끼운다."""
    try:
        from . import bmk_design_patch as nodes
    except ImportError:  # 단독 실행/테스트
        import bmk_design_patch as nodes
    return nodes.NODE_CLASS_MAPPINGS


def _validate_prompt(prompt):
    if not isinstance(prompt, dict):
        raise ValueError("프롬프트는 노드 객체여야 합니다")
    kinds = {key: pl.CLASS[role] for role, key in pl.NODE_KEYS.items()}
    seen = set()
    for key, node in prompt.items():
        if key not in kinds or not isinstance(node, dict) or node.get("class_type") != kinds[key]:
            raise ValueError(f"허용하지 않는 노드 키·종류입니다: {key!r}")
        inputs = node.get("inputs")
        if not isinstance(inputs, dict) or any(not isinstance(k, str) for k in inputs):
            raise ValueError(f"노드 입력은 이름·값 객체여야 합니다: {key}")
        for value in inputs.values():
            if isinstance(value, (list, tuple)):
                if (not isinstance(value, list) or len(value) != 2 or not isinstance(value[0], str)
                        or value[0] not in seen or type(value[1]) is not int or value[1] < 0):
                    raise ValueError(f"연결은 앞선 노드와 0 이상 출력 번호여야 합니다: {key}")
        seen.add(key)


def _validation_args(fn, inputs):
    params = inspect.signature(fn).parameters
    if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return inputs
    return {k: v for k, v in inputs.items() if k in params and params[k].kind in
            (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)}


def _validate_scope(prompt):
    """서버 템플릿의 경로를 노드 실행 전에 검사한다(옛 설정·우회 입력도 같은 범위)."""
    if not compat.server_mode():
        return
    scope = board.current_scope()
    if scope is None:
        raise RuntimeError("서버 모드의 작업에는 계정 범위가 필요합니다")

    def path_value(value):
        if not isinstance(value, str):
            raise ValueError
        # 노드의 _strip_path 와 같은 앞뒤 공백·따옴표 처리. 검사는 실제로 읽을 경로로 한다.
        return value.strip().strip('"').strip("'").strip()

    try:
        project = prompt[pl.NODE_KEYS["project"]]["inputs"]
        base = path_value(project.get("base_dir"))
        if not base or _path(base) != _path(scope["input"]):
            raise ValueError
        root = _path(pl.store.project_root(base, project["project"]))
        for role, names in (("import", ("psd_path", "refs_dir", "glossary_path")), ("cand", ("path",))):
            inputs = prompt.get(pl.NODE_KEYS[role], {}).get("inputs", {})
            for name in names:
                value = path_value(inputs.get(name, ""))
                if value and os.path.commonpath((root, _path(value))) != root:
                    raise ValueError
    except (KeyError, TypeError, ValueError, OSError):
        raise ValueError("작업의 프로젝트·입력 경로가 계정 범위를 벗어났거나 잘못되었습니다") from None


def execute(prompt: dict, *, hidden: dict | None = None) -> dict:
    """템플릿 하나를 차례로 실행 → {"outputs": {노드 키: ui dict}}. 큐·스레드를 만들지 않는다(부르는 스레드에서 끝까지).
    실행 전 검사에 걸리면 ValueError, 노드가 낸 예외는 그대로 올리되 어느 노드였는지(node_id·node_type)를 예외에 붙인다.
    캐시 없음 — IS_CHANGED 를 부르지 않고 모든 노드를 실행한다(D343)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise RuntimeError("execute 는 실행 중인 이벤트 루프 밖의 작업 스레드에서 불러야 합니다")
    _validate_prompt(prompt)  # 뒤쪽 노드가 잘못돼도 앞쪽 노드의 쓰기가 시작되지 않게
    _validate_scope(prompt)
    classes = _node_classes()
    state = _STATE.get()
    outputs = state["outputs"] if state is not None else {}
    results = {}
    try:
        for key, node in prompt.items():
            kind = node["class_type"]
            if state is not None:
                state["node"] = {"node_id": "", "node_type": ""}
            inputs = {}
            for name, value in node["inputs"].items():
                if isinstance(value, list):
                    previous, index = value
                    if index >= len(results[previous]):
                        raise ValueError(f"노드 출력 번호가 범위를 벗어났습니다: {previous}[{index}]")
                    value = results[previous][index]
                inputs[name] = value
            try:
                if state is not None:
                    state["node"] = {"node_id": key, "node_type": kind}
                    if state["closed"]:
                        raise compat.InterruptProcessingException()
                cls = classes[kind]
                for name in cls.INPUT_TYPES().get("hidden", {}):
                    inputs[name] = key if name == "unique_id" else (hidden or {}).get(name)
                validate = getattr(cls, "VALIDATE_INPUTS", None)
                if validate is not None:
                    valid = validate(**_validation_args(validate, inputs))
                    if valid is not True:
                        raise ValueError(str(valid))
                fn = getattr(cls(), cls.FUNCTION)
                result = asyncio.run(fn(**inputs)) if inspect.iscoroutinefunction(fn) else fn(**inputs)
                if isinstance(result, dict):
                    if "ui" in result:
                        ui = result["ui"]
                        try:
                            if not isinstance(ui, dict):
                                raise TypeError("ui 는 객체여야 합니다")
                            json.dumps(ui, allow_nan=False)
                        except (TypeError, ValueError, OverflowError):
                            logger.warning("Design Patch 작업 %s: JSON 으로 바꿀 수 없는 ui 를 생략합니다", key)
                        else:
                            outputs[key] = ui
                    result = result.get("result", ())
                if not isinstance(result, tuple):
                    raise ValueError("노드 결과는 튜플 또는 ui·result 객체여야 합니다")
                results[key] = result
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException as exc:
                try:
                    exc.bmk_dp_node = {"node_id": key, "node_type": kind}
                except Exception:
                    pass  # 속성 대입을 막은 예외도 원래 종류·내용을 보존한다
                raise
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as exc:
        try:
            exc.bmk_dp_outputs = outputs
        except Exception:
            pass
        raise
    return {"outputs": outputs}


def _path(path):
    return os.path.normcase(os.path.realpath(path))


def _error_message(exc, scope):
    try:
        message = str(exc)
    except BaseException:
        message = type(exc).__name__
    home = (scope or {}).get("home")
    if compat.server_mode() and isinstance(home, str) and home:
        pattern = r"[\\/]".join(re.escape(part) for part in re.split(r"[\\/]", home.rstrip("\\/")))
        if pattern:
            message = re.sub(pattern + r"(?![\w.-])", "<계정 폴더>", message, flags=re.IGNORECASE)
    return message


def _record(outputs, exc=None, *, interrupted=False, node=None, scope=None):
    info = dict(node or getattr(exc, "bmk_dp_node", None) or {"node_id": "", "node_type": ""})
    if exc is None and not interrupted:
        record = {"status": {"status_str": "success", "completed": True, "messages": []}, "outputs": outputs}
    else:
        if not interrupted:
            info["exception_message"] = _error_message(exc, scope)
        name = "execution_interrupted" if interrupted else "execution_error"
        record = {"status": {"status_str": "error", "completed": False, "messages": [[name, info]]}, "outputs": outputs}
    try:
        # JSON 왕복으로 기록을 분리한다. 이후 deepcopy 가 노드 객체의 사용자 코드를 부르지 않는다.
        return json.loads(json.dumps(record, allow_nan=False))
    except BaseException:
        info = {k: v if type(v) is str else "" for k, v in ((k, info.get(k, "")) for k in ("node_id", "node_type"))}
        info["exception_message"] = _error_message(exc, scope) if exc is not None else "작업 출력을 JSON 으로 바꿀 수 없습니다"
        return {"status": {"status_str": "error", "completed": False, "messages": [["execution_error", info]]}, "outputs": {}}


class JobRunner:
    """프로젝트당 직렬 작업 큐. scope = board.current_scope() 모양(작업 스레드가 그 범위를 건다)."""

    def __init__(self, *, workers: int = 1, keep: int = 200, max_pending: int = 8):
        for name, value, minimum in (("workers", workers, 1), ("keep", keep, 0), ("max_pending", max_pending, 1)):
            if type(value) is not int or value < minimum:
                raise ValueError(f"{name} 는 {minimum} 이상 정수여야 합니다")
        self._keep = keep
        self._max_pending = max_pending
        self._condition = threading.Condition()
        self._pending = []
        self._running = {}
        self._history = {}
        self._closed = False
        self._threads = [threading.Thread(target=self._work, name=f"design-patch-{i}", daemon=True)
                         for i in range(workers)]
        for thread in self._threads:
            thread.start()

    def submit(self, scope: dict, root: str, stage: str, prompt: dict) -> dict:
        """작업을 줄에 넣는다 → {"prompt_id": str, "number": int}."""
        home, project = _path(scope["home"]), _path(root)
        with self._condition:
            if self._closed:
                raise RuntimeError("작업 실행기가 닫혔습니다")
            if sum(j["home"] == home for j in self._pending) >= self._max_pending:
                raise QueueFull("대기 중인 작업이 너무 많습니다 — 앞 작업이 끝난 뒤 다시 하세요.")
            with _NUMBER_LOCK:
                number = next(_NUMBERS)
            prompt_id = "j_" + secrets.token_hex(8)
            job = {"number": number, "prompt_id": prompt_id, "stage": stage, "scope": copy.deepcopy(scope),
                   "home": home, "root": project, "prompt": copy.deepcopy(prompt), "outputs": {},
                   "node": {"node_id": "", "node_type": ""}, "closed": False}
            self._pending.append(job)
            self._condition.notify_all()
            return {"prompt_id": prompt_id, "number": number}

    def queue(self, scope: dict, root: str) -> dict:
        """{"queue_running": [[number, prompt_id, stage]], "queue_pending": […]} — 이 프로젝트 것 + 앞에 선 남의 작업은 [number, "", ""]."""
        home, project = _path(scope["home"]), _path(root)
        with self._condition:
            own = [j["number"] for j in [*self._running.values(), *self._pending]
                   if j["home"] == home and j["root"] == project]
            last = max(own, default=-1)

            def rows(jobs):
                out = []
                for job in sorted(jobs, key=lambda j: j["number"]):
                    if job["home"] == home and job["root"] == project:
                        out.append([job["number"], job["prompt_id"], job["stage"]])
                    elif job["number"] < last:
                        out.append([job["number"], "", ""])
                return out

            return {"queue_running": rows(self._running.values()), "queue_pending": rows(self._pending)}

    def delete(self, scope: dict, root: str, ids: list[str]) -> list[str]:
        """대기 중인 작업만 지운다 → 지운 prompt_id 목록(실행 중·끝난 것·남의 것은 건드리지 않음)."""
        home, project, wanted = _path(scope["home"]), _path(root), set(ids)
        with self._condition:
            deleted = [j["prompt_id"] for j in self._pending
                       if j["home"] == home and j["root"] == project and j["prompt_id"] in wanted]
            self._pending = [j for j in self._pending if j["prompt_id"] not in deleted]
            self._condition.notify_all()
            return deleted

    def history(self, scope: dict, root: str, prompt_id: str) -> dict:
        """{} (없음·아직) 또는 {prompt_id: {"status": {...}, "outputs": {...}}}."""
        home, project = _path(scope["home"]), _path(root)
        with self._condition:
            entry = self._history.get(home, {}).get(prompt_id)
            return {prompt_id: copy.deepcopy(entry[1])} if entry is not None and entry[0] == project else {}

    def close(self, timeout: float = 0.0) -> None:
        """새 호출을 멈추고 전체 timeout 동안 저장·끝 처리를 기다린다(대기는 기록 없이 버림)."""
        deadline = time.monotonic() + max(0.0, float(timeout))
        with self._condition:
            self._closed = True
            self._pending.clear()
            active = list(self._running.values())
            self._condition.notify_all()
        for job in active:
            try:
                board.set_drain(job["root"])
            except Exception:
                pass
        for thread in self._threads:
            if thread is not threading.current_thread():
                thread.join(max(0.0, deadline - time.monotonic()))
        with self._condition:
            for job in list(self._running.values()):
                job["closed"] = True
                self._finish(job, _record(job["outputs"], interrupted=True, node=job["node"], scope=job["scope"]))
            self._condition.notify_all()

    def _take(self):
        busy = {j["root"] for j in self._running.values()}
        exporting = {j["home"] for j in self._running.values() if j["stage"] == "export"}
        for index, job in enumerate(self._pending):
            blocked = job["root"] in busy or (job["stage"] == "export" and job["home"] in exporting)
            busy.add(job["root"])  # 막힌 앞 작업을 같은 프로젝트의 뒤 작업이 추월하지 않는다
            if not blocked:
                self._pending.pop(index)
                self._running[job["prompt_id"]] = job
                return job
        return None

    def _finish(self, job, record):
        """조건 잠금 안에서 한 번만 끝낸다(close 와 실행 끝의 경합 포함)."""
        pid = job["prompt_id"]
        if pid not in self._running:
            return
        snapshot = copy.deepcopy(record)
        history = self._history.setdefault(job["home"], OrderedDict())
        history[pid] = (job["root"], snapshot)
        while len(history) > self._keep:
            history.popitem(last=False)
        self._running.pop(pid, None)
        self._condition.notify_all()

    def _progress(self, job, value, total):
        with self._condition:
            if not job["closed"]:
                job["progress"] = (value, total)

    def _work(self):
        while True:
            with self._condition:
                while not self._closed:
                    job = self._take()
                    if job is not None:
                        break
                    self._condition.wait()
                else:
                    return
            try:
                self._run(job)
            except BaseException as exc:
                logger.warning("Design Patch 작업 스레드 복구: %s", type(exc).__name__)
                with self._condition:
                    try:
                        self._finish(job, _record(job["outputs"], exc, node=job["node"], scope=job["scope"]))
                    except BaseException as finish_exc:
                        logger.warning("Design Patch 작업 끝 처리 실패: %s", type(finish_exc).__name__)
                    finally:
                        self._running.pop(job["prompt_id"], None)
                        self._condition.notify_all()

    def _run(self, job):
        scope_token = progress_token = state_token = None
        record = _record({}, RuntimeError("작업이 끝나기 전에 중단됐습니다"))
        try:
            scope_token = board.set_scope(job["scope"])
            progress_token = compat.set_progress_sink(lambda value, total: self._progress(job, value, total))
            state_token = _STATE.set(job)
            if job["closed"]:
                raise compat.InterruptProcessingException()
            result = execute(job["prompt"])
            record = _record(result["outputs"], node=job["node"], scope=job["scope"])
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                logger.warning("Design Patch 작업의 프로세스 종료 예외를 오류로 기록합니다: %s", type(exc).__name__)
            interrupted = isinstance(exc, (compat.InterruptProcessingException, *backend._interrupt_types()))
            record = _record(getattr(exc, "bmk_dp_outputs", job["outputs"]), exc, interrupted=interrupted,
                             node=getattr(exc, "bmk_dp_node", None) or job["node"], scope=job["scope"])
        finally:
            try:
                if state_token is not None:
                    state_token.var.reset(state_token)
                if progress_token is not None:
                    progress_token.var.reset(progress_token)
                if scope_token is not None:
                    board.reset_scope(scope_token)
            finally:
                with self._condition:
                    self._finish(job, record)


def routes(runner: JobRunner) -> tuple:
    """서버 진입점이 자기 앱에 붙일 (method, path, handler) 넷 — 보드 _ROUTES 에는 넣지 않는다(D334)."""
    def scope():
        value = board.current_scope()
        if value is None:
            raise board._Reject(401, board.NO_SESSION, reason="no_session")
        return value

    def prompt_id(value):
        if not isinstance(value, str) or _PROMPT_ID.fullmatch(value) is None:
            raise board._Reject(400, "작업 ID 형식이 잘못되었습니다")
        return value

    @board._guard
    async def submit(request):
        data = await board._body(request)
        if set(data) - {"root", "stage", "expect", "approve", "only"}:
            raise board._Reject(400, "작업 요청에 허용하지 않는 키가 있습니다")
        if any(not isinstance(value, str) for value in data.values()):
            raise board._Reject(400, "작업 요청의 root·stage·expect·approve·only 는 문자열이어야 합니다")
        root = board._root_param(data.get("root"))
        account = scope()
        stage = data.get("stage")
        if stage in ("run", "draft", "reroll") and not backend.mock_enabled(root):
            raise board._Reject(409, "서버 모드 생성 백엔드가 아직 연결되지 않았습니다(mock 만).", reason="no_backend")
        res = await asyncio.to_thread(board.pipeline_prompt, root, stage, data.get("approve"),
                                      data.get("only"), data.get("expect"))
        try:
            result = runner.submit(account, root, stage, res["prompt"])
        except QueueFull as exc:
            raise board._Reject(409, str(exc), reason="queue_full") from None
        return board._json(result)

    @board._guard
    async def queue(request):
        root = board._root_param(request.query.get("root"))
        return board._json(runner.queue(scope(), root))

    @board._guard
    async def delete(request):
        data = await board._body(request)
        if set(data) - {"root", "delete"} or not isinstance(data.get("delete"), list):
            raise board._Reject(400, "대기 삭제 요청은 root·delete 목록이어야 합니다")
        root = board._root_param(data.get("root"))
        account = scope()
        ids = [prompt_id(value) for value in data["delete"]]
        return board._json({"deleted": runner.delete(account, root, ids)})

    @board._guard
    async def history(request):
        root = board._root_param(request.query.get("root"))
        account = scope()
        pid = prompt_id(request.match_info.get("prompt_id"))
        return board._json(runner.history(account, root, pid))

    return (("POST", f"{API}/jobs/prompt", submit), ("GET", f"{API}/jobs/queue", queue),
            ("POST", f"{API}/jobs/queue", delete), ("GET", f"{API}/jobs/history/{{prompt_id}}", history))
