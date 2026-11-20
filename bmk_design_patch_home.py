"""서버 모드 프로젝트 홈의 표시 이름·보관 설정."""

import logging
import os
import time
import unicodedata

try:
    from . import bmk_design_patch_board as board
    from . import bmk_design_patch_store as store
except ImportError:
    import bmk_design_patch_board as board
    import bmk_design_patch_store as store


logger = logging.getLogger(__name__)


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
    manifest = os.path.join(root, store.MANIFEST_NAME)
    # 프로젝트 폴더뿐 아니라 매니페스트 파일도 링크가 아니어야 한다(응답의 줄이 그 파일에서 만들어진다)
    if not board.in_scope(root) or board._is_link(root) or board._is_link(manifest) or not os.path.isfile(manifest):
        raise board._Reject(404, board._PROJECT_NOT_FOUND, reason="not_found")


def _name_taken(key, title):
    wanted = board._project_name_key(title)
    return (any(board._project_name_key(os.path.basename(other["root"])) == wanted
                for other in board._project_records() if other["key"] != key)
            or board._project_title_taken(title, key))


def _busy(runner, root):
    queue = runner.queue(board.current_scope(), root)
    return (any(row[1] for group in ("queue_running", "queue_pending") for row in queue[group])
            or board.is_running(root) or board.draft_since(root) is not None)


def _precheck(data, runner, root):
    """프로젝트 잠금 밖에서 해 두는 판정 — 다른 프로젝트의 파일을 읽거나 작업 큐를 묻는 일을 이 프로젝트의 잠금 안에서 하지 않는다
    (두 프로젝트의 잠금을 겹쳐 잡는 순서가 생기지 않게). 결과는 _change 가 판(seen) 대조 뒤에 쓴다."""
    op = data["op"]
    if op == "rename":
        try:
            title = store.check_project_title(data["title"])
        except ValueError as exc:
            return {"error": board._Reject(400, str(exc), reason="bad_title")}
        if title == unicodedata.normalize("NFC", os.path.basename(root)):
            title = None  # 폴더 이름과 같은 값 = 표시 이름을 지운다
        # 지우는 것(폴더 이름으로 돌아감)은 늘 받는다 — 중복은 새 표시 이름을 붙일 때만 본다
        return {"title": title, "taken": title is not None and _name_taken(data["key"], title)}
    if op == "archive":
        return {"busy": _busy(runner, root)}
    return {}


def _change(doc, data, pre, root):
    op = data["op"]
    if op == "rename":
        if doc["archived"] is not None:
            raise board._Reject(409, "보관된 프로젝트입니다 — 먼저 되돌려 주세요", reason="archived")
        if "error" in pre:
            raise pre["error"]
        if pre["title"] != doc["title"] and pre["taken"]:
            raise board._Reject(409, "같은 이름의 프로젝트가 있습니다 — 다른 이름을 써 주세요", reason="name_taken")
        doc["title"] = pre["title"]
    elif op == "restore":
        doc["archived"] = None
    elif doc["archived"] is None:
        if pre["busy"]:
            raise board._Reject(409, "진행 중인 작업이 있습니다 — 끝난 뒤 다시 보관해 주세요", reason="busy")
        doc["archived"] = {"at": time.strftime("%Y-%m-%d %H:%M:%S")}
    _check_target(root)


def _update(data, runner):
    record = board._project_find(data.get("key"))
    root = record["root"]
    with board._project_account_lock(root):
        stale = None
        _check_target(root)
        pre = _precheck(data, runner, root)
        try:
            # update_project_meta 가 이 프로젝트의 잠금 안에서 판 대조 → _change(대상 재확인 포함) → 쓰기를 한다
            _, changed = store.update_project_meta(root, lambda doc: _change(doc, data, pre, root), expect_rev=data["seen"])
        except store.ProjectMetaConflict as exc:
            stale = str(exc)
        except ValueError:
            raise board._Reject(422, store._PROJECT_META_ERROR, reason="meta_broken") from None
        except FileNotFoundError:  # 쓰기 직전에 프로젝트 폴더가 사라짐(만들기가 취소돼 정리됨) — 폴더를 다시 만들지 않는다
            raise board._Reject(404, board._PROJECT_NOT_FOUND, reason="not_found") from None
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
