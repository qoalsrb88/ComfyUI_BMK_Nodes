"""서버 모드 프로젝트 홈의 표시 이름·보관 설정."""

import logging
import os
import threading
import time
import unicodedata

try:
    from . import bmk_design_patch_board as board
    from . import bmk_design_patch_store as store
except ImportError:
    import bmk_design_patch_board as board
    import bmk_design_patch_store as store


logger = logging.getLogger(__name__)
_HOME_LOCKS = {}
_HOME_LOCKS_GUARD = threading.Lock()


def _validate(data):
    extra = data.keys() - {"key", "seen", "op", "title"}
    if extra:
        raise board._Reject(400, "모르는 키: " + ", ".join(sorted(extra)), reason="bad_request")
    if type(data.get("seen")) is not int or data["seen"] < 0:
        raise board._Reject(400, "seen은 0 이상 정수여야 합니다", reason="bad_request")
    op = data.get("op")
    if op not in ("rename", "archive", "restore"):
        raise board._Reject(400, "op는 rename·archive·restore 중 하나여야 합니다", reason="bad_request")
    if op == "rename" and not isinstance(data.get("title"), str):
        raise board._Reject(400, "이름을 바꾸려면 title 문자열이 필요합니다", reason="bad_request")
    if op != "rename" and "title" in data:
        raise board._Reject(400, "이름을 바꿀 때만 title을 쓸 수 있습니다", reason="bad_request")


def _check_target(root):
    if (not board.in_scope(root) or board._is_link(root)
            or not os.path.isfile(os.path.join(root, store.MANIFEST_NAME))):
        raise board._Reject(404, board._PROJECT_NOT_FOUND, reason="not_found")


def _name_key(name):
    return unicodedata.normalize("NFC", name).casefold()


def _name_taken(key, title):
    wanted = _name_key(title)
    for other in board._project_records():
        if other["key"] == key:
            continue
        if wanted == _name_key(os.path.basename(other["root"])):
            return True
        other_title = store.read_project_meta(other["root"])[0]["title"]
        if other_title and wanted == _name_key(other_title):
            return True
    return False


def _busy(runner, root):
    queue = runner.queue(board.current_scope(), root)
    return (any(row[1] for group in ("queue_running", "queue_pending") for row in queue[group])
            or board.is_running(root) or board.draft_since(root) is not None)


def _change(doc, data, runner, root):
    op = data["op"]
    if op == "rename":
        if doc["archived"] is not None:
            raise board._Reject(409, "보관된 프로젝트입니다 — 먼저 되돌려 주세요", reason="archived")
        try:
            title = store.check_project_title(data["title"])
        except ValueError as exc:
            raise board._Reject(400, str(exc), reason="bad_title") from None
        folder = os.path.basename(root)
        if _name_taken(data["key"], title or folder):
            raise board._Reject(409, "같은 이름의 프로젝트가 있습니다 — 다른 이름을 써 주세요", reason="name_taken")
        doc["title"] = None if title == unicodedata.normalize("NFC", folder) else title
    elif op == "restore":
        doc["archived"] = None
    elif doc["archived"] is None:
        if _busy(runner, root):
            raise board._Reject(409, "진행 중인 작업이 있습니다 — 끝난 뒤 다시 보관해 주세요", reason="busy")
        doc["archived"] = {"at": time.strftime("%Y-%m-%d %H:%M:%S")}
    _check_target(root)


def _update(data, runner):
    record = board._project_find(data.get("key"))
    root = record["root"]
    parent = os.path.normcase(os.path.abspath(os.path.dirname(root)))
    with _HOME_LOCKS_GUARD:
        lock = _HOME_LOCKS.setdefault(parent, threading.Lock())
    with lock:
        stale = None
        try:
            with store.manifest_lock(root):
                _check_target(root)
                _, changed = store.update_project_meta(root, lambda doc: _change(doc, data, runner, root), expect_rev=data["seen"])
        except store.ProjectMetaConflict as exc:
            stale = str(exc)
        except ValueError:
            raise board._Reject(422, store._PROJECT_META_ERROR, reason="meta_broken") from None
        _check_target(root)
        project = board._project_row(record, True)
        archived_count = sum(store.read_project_meta(r["root"])[0]["archived"] is not None for r in board._project_records())
        if stale is not None:
            raise board._Reject(409, stale, reason="stale", project=project, archived_count=archived_count)
        if changed:
            logger.info("%s %s", data["op"], data["key"])
        return {"ok": True, "changed": changed, "project": project, "archived_count": archived_count}


def routes(runner) -> tuple:
    @board._guard
    async def post(request):
        try:
            data = await board._body(request)
            _validate(data)
            return board._json(await board._project_call(_update, data, runner))
        except board._Reject as exc:
            if "reason" in exc.extra:
                raise
            status = 400 if exc.status == 415 else exc.status
            raise board._Reject(status, exc.message, reason="not_found" if status == 404 else "bad_request") from None

    return (("POST", board.API + "/projects/meta", post),)
