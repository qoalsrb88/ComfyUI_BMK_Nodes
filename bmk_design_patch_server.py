"""BMK Design Patch Studio — 서버 모드 진입점(Forge 이식 F1, _proto/STUDIO_FORGE_F1_SPEC.md).

ComfyUI 없이 도는 독립 aiohttp 서버. 사내 툴 Forge 가 iframe 으로 끼우는 '별도 확장 메뉴'용이다.
- 보드 라우트(bmk_design_patch_board.register_routes)를 자기 앱에 붙이고, 요청마다 세션 계정의 범위(board.set_scope)를 건다.
- 세션: Forge 가 준 해시 토큰 → POST /api/session → 세션 쿠키(D332). 토큰 검증기는 갈아 끼운다 — F1 은 개발용(DevVerifier)만.
- 요청 검사(D333): 세션 · GET 아닌 요청의 전용 헤더 + Origin 대조 · Host 허용 목록 · frame-ancestors.
- 계정별 저장(D329): <data>/<org>/<acct>/bmk_design_patch/<프로젝트>, 출력 <홈>/_output, 화면 설정 <홈>/_prefs.json.
- 실행기(bmk_design_patch_jobs)의 라우트·화면 설정·Export 내려받기·개발용 가짜 부모 페이지.

띄우기(개발):
    python_embeded\\python.exe -X utf8 bmk_design_patch_server.py --data <폴더> --port 8791 --dev-handoff --dev-parent-port 8790

F1 은 mock 전용이며 운영 토큰 검증기는 주입해야 한다.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import hashlib
import hmac
import html
import json
import logging
import os
import re
import secrets
import shutil
import threading
import time
import unicodedata
from urllib.parse import quote, urlencode, urlsplit

from aiohttp import web


logger = logging.getLogger(__name__)
COOKIE = "bmk_dp_session"
_PREF_KEY = re.compile(r"bmk-dp-[\w:.-]{1,80}")
_SESSION_KEY = web.AppKey("bmk_dp_sessions", object)
_RUNNER_KEY = web.AppKey("bmk_dp_runner", object)
_ACCOUNT_LOCK = threading.Lock()
_ERRORS = {
    "bad_origin": (403, "요청 헤더·출처 검사에 걸렸습니다."),
    "no_session": (401, "Forge에서 Design Patch Studio를 다시 열어 주세요."),
    "session_changed": (409, "다른 계정으로 바뀌었습니다 — Forge에서 Design Patch Studio를 다시 열어 주세요."),
    "server_mode": (404, "서버 모드에서는 쓸 수 없는 기능입니다."),
    "bad_token": (401, "토큰을 확인하지 못했습니다 — Forge에서 Design Patch Studio를 다시 열어 주세요."),
    "no_verifier": (503, "토큰 검증기가 설정되지 않았습니다."),
}


def _error(reason):
    status, message = _ERRORS[reason]
    return web.json_response({"error": message, "reason": reason}, status=status)


def _valid_id(value):
    return (isinstance(value, str) and 1 <= len(value) <= 128
            and not any(unicodedata.category(c).startswith("C") for c in value))


def _host(value):
    if not isinstance(value, str) or not value or any(c.isspace() or ord(c) < 32 for c in value):
        raise ValueError("Host 형식이 잘못되었습니다")
    parsed = urlsplit("//" + value)
    if (not parsed.hostname or parsed.username is not None or parsed.password is not None
            or parsed.path or parsed.query or parsed.fragment or parsed.netloc != value
            or value.endswith(":")):
        raise ValueError("Host 형식이 잘못되었습니다")
    parsed.port  # 잘못된 포트도 거절한다.
    return value.lower()


def _origin(value):
    parsed = urlsplit(value)
    if (parsed.scheme not in ("http", "https") or parsed.path not in ("", "/")
            or parsed.query or parsed.fragment):
        raise ValueError("origin 은 http(s) 서버 주소여야 합니다")
    return parsed.scheme + "://" + _host(parsed.netloc)


def _validate_config(config):
    if not config.dev_handoff and not config.public_origin:
        raise ValueError("개발 모드 밖에서는 public_origin 이 필요합니다")
    origin = _origin(config.public_origin) if config.public_origin else None
    if config.cookie_samesite not in ("Lax", "Strict", "None"):
        raise ValueError("cookie_samesite 는 Lax·Strict·None 중 하나여야 합니다")
    if config.cookie_samesite == "None" and (not origin or not origin.startswith("https://")):
        raise ValueError("SameSite=None 은 HTTPS public_origin 이 필요합니다")
    if config.dev_parent_port is not None and not config.dev_handoff:
        raise ValueError("dev_parent_port 는 개발 모드에서만 쓸 수 있습니다")
    if not config.data_dir or config.idle_ttl <= 0 or config.max_ttl <= 0 or config.workers < 1:
        raise ValueError("저장 폴더·세션 만료·실행기 수 설정을 확인하세요")
    for port in (config.port, config.dev_parent_port):
        if port is not None and (type(port) is not int or not 1 <= port <= 65535):
            raise ValueError("포트는 1–65535 여야 합니다")
    return dataclasses.replace(config, public_origin=origin,
                               parent_origins=tuple(_origin(p) for p in config.parent_origins),
                               allowed_hosts=tuple(_host(h) for h in config.allowed_hosts))


def _inside(path, base):
    try:
        base = os.path.normcase(os.path.realpath(base))
        path = os.path.normcase(os.path.realpath(path))
        return os.path.commonpath([base, path]) == base
    except (OSError, ValueError, TypeError):
        return False


def _plain_path(path):
    # 계정 메타데이터·폴더를 링크로 바꿔 다른 계정의 파일을 읽거나 쓰지 못하게 한다.
    if os.path.normcase(os.path.abspath(path)) != os.path.normcase(os.path.realpath(path)):
        raise ValueError("계정 저장 경로에 링크를 사용할 수 없습니다")
    return path


def _folder_id(value):
    prefix = re.sub(r"[^a-z0-9_]", "_", value.lower())[:24] or "id"
    return prefix + "-" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _server_secret(data_dir):
    os.makedirs(data_dir, exist_ok=True)
    path = _plain_path(os.path.join(data_dir, "_server_secret"))
    with _ACCOUNT_LOCK:
        try:
            with open(path, "xb") as f:
                f.write(secrets.token_bytes(32))
        except FileExistsError:
            pass
        with open(path, "rb") as f:
            secret = f.read(33)
        if len(secret) != 32:
            raise ValueError("서버 비밀 파일 형식이 잘못되었습니다")
        return secret


def _account_scope(data_dir, identity, store, now):
    org, acct = identity["organization_id"], identity["account_id"]
    home = os.path.join(data_dir, _folder_id(org), _folder_id(acct))
    with _ACCOUNT_LOCK:
        for directory in (os.path.dirname(home), home):
            _plain_path(directory)
            os.makedirs(directory, exist_ok=True)
        path = _plain_path(os.path.join(home, "_account.json"))
        try:
            with open(path, "x", encoding="utf-8", newline="\n") as f:
                json.dump({"schema": "bmk.design_patch.account/1", "organization_id": org,
                           "account_id": acct, "created": now}, f, ensure_ascii=False)
        except FileExistsError:
            with open(path, encoding="utf-8") as f:
                doc = json.load(f)
            if (not isinstance(doc, dict) or doc.get("schema") != "bmk.design_patch.account/1"
                    or doc.get("organization_id") != org or doc.get("account_id") != acct):
                logger.error("계정 폴더 충돌")
                raise ValueError("계정 폴더 충돌")
        for name in (store.PROJECTS_DIRNAME, "_output"):
            os.makedirs(_plain_path(os.path.join(home, name)), exist_ok=True)
        store.load_root_registry(home)
    return {"input": home, "output": os.path.join(home, "_output"), "home": home, "account": acct, "org": org}


class SessionStore:
    """원본 토큰 없이 계정과 범위만 보관한다. 시계를 주입해 두 만료 조건을 검증한다."""

    def __init__(self, idle_ttl, max_ttl, *, clock=time.time, limit=10000):
        self.idle_ttl, self.max_ttl, self.clock, self.limit = idle_ttl, max_ttl, clock, limit
        self._sessions = {}
        self._lock = threading.Lock()

    def get(self, sid):
        with self._lock:
            session = self._sessions.get(sid)
            if session is None:
                return None
            now = self.clock()
            if now - session["seen"] >= self.idle_ttl or now - session["created"] >= self.max_ttl:
                del self._sessions[sid]
                return None
            session["seen"] = now
            return dict(session)

    def create(self, identity, scope, tag, old_sid=None):
        with self._lock:
            self._sessions.pop(old_sid, None)
            now = self.clock()
            for sid, session in list(self._sessions.items()):
                if now - session["seen"] >= self.idle_ttl or now - session["created"] >= self.max_ttl:
                    del self._sessions[sid]
            while len(self._sessions) >= self.limit:
                del self._sessions[min(self._sessions, key=lambda sid: self._sessions[sid]["seen"])]
            sid = secrets.token_urlsafe(32)
            while sid in self._sessions or sid == old_sid:
                sid = secrets.token_urlsafe(32)
            self._sessions[sid] = {k: identity[k] for k in ("account_id", "organization_id", "name")}
            self._sessions[sid].update(tag=tag, scope=dict(scope), created=now, seen=now)
            return sid

    def delete(self, sid):
        with self._lock:
            self._sessions.pop(sid, None)

    def clear(self):
        with self._lock:
            self._sessions.clear()


async def _body(request, limit):
    # request.json/read 의 본문 캐시에 로그인 토큰을 남기지 않고, 청크 전송에도 상한을 건다.
    if request.content_type != "application/json":
        raise web.HTTPBadRequest(text="JSON 본문이 필요합니다")
    if request.content_length is not None and request.content_length > limit:
        raise web.HTTPBadRequest(text="요청 본문이 너무 큽니다")
    raw = bytearray()
    async for chunk in request.content.iter_chunked(min(limit + 1, 65536)):
        raw.extend(chunk)
        if len(raw) > limit:
            raise web.HTTPBadRequest(text="요청 본문이 너무 큽니다")
    try:
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError
        return data
    except (UnicodeError, ValueError):
        raise web.HTTPBadRequest(text="JSON 본문을 읽을 수 없습니다") from None
    finally:
        raw.clear()


def _prefs_doc(values):
    if not isinstance(values, dict):
        raise ValueError("설정은 객체여야 합니다")
    for key, value in values.items():
        if (not isinstance(key, str) or _PREF_KEY.fullmatch(key) is None or not isinstance(value, str)
                or len(value.encode("utf-8")) > 65536):
            raise ValueError("설정 키·값 형식 또는 길이가 잘못되었습니다")
    doc = {"schema": "bmk.design_patch.prefs/1", "values": values}
    if len((json.dumps(doc, ensure_ascii=False, indent=1) + "\n").encode("utf-8")) > 1024 ** 2:
        raise ValueError("설정 전체 크기가 1MiB 를 넘습니다")
    return doc


class _Preferences:
    def __init__(self, store):
        self.store, self.locks, self.guard = store, {}, threading.Lock()

    def read(self, path):
        try:
            with open(_plain_path(path), "rb") as f:
                raw = f.read(1024 ** 2 + 1)
            doc = json.loads(raw.decode("utf-8"))
            if doc["schema"] != "bmk.design_patch.prefs/1" or len(raw) > 1024 ** 2:
                raise ValueError
            return _prefs_doc(doc["values"])["values"], False
        except FileNotFoundError:
            return {}, False
        except (ValueError, KeyError, TypeError, UnicodeError):
            return {}, True

    def apply(self, home, data=None):
        path = _plain_path(os.path.join(home, "_prefs.json"))
        with self.guard:
            lock = self.locks.setdefault(os.path.normcase(home), threading.Lock())
        with lock:
            values, broken = self.read(path)
            if data is None:
                return {"values": values}
            updates, remove = data.get("set", {}), data.get("remove", [])
            _prefs_doc(updates)
            if not isinstance(remove, list) or any(not isinstance(k, str) or _PREF_KEY.fullmatch(k) is None for k in remove):
                raise ValueError("지울 설정 키 형식이 잘못되었습니다")
            values.update(updates)
            for key in remove:
                values.pop(key, None)
            doc = _prefs_doc(values)  # 모든 검사가 끝나기 전에는 파일을 바꾸지 않는다.
            if broken:
                backup = os.path.join(home, "_prefs.bad.json")
                try:
                    with open(backup, "xb") as dest, open(path, "rb") as source:
                        shutil.copyfileobj(source, dest)
                except FileExistsError:
                    pass
            self.store.write_json_atomic(path, doc)
            return {"ok": True}


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
        if not isinstance(token, str) or not token.startswith("dev:"):
            return None
        account_id = token[4:]
        if not _valid_id(account_id) or not _valid_id(organization_id):
            return None
        return {"account_id": account_id, "organization_id": organization_id, "name": account_id}


def make_app(config: ServerConfig, *, verifier=None, jobs_module=None, clock=time.time) -> web.Application:
    """서버 모드 앱. verifier 가 None 이고 dev_handoff 면 DevVerifier, 둘 다 없으면 POST /api/session 은 503."""
    config = _validate_config(config)
    old_mock = os.environ.get("BMK_DP_MOCK_API")
    runner = None
    # main 을 통하지 않는 시험·임베딩에서도 노드의 첫 import 전에 모드를 정한다.
    os.environ["BMK_DP_SERVER_MODE"] = "1"
    os.environ["BMK_DP_MOCK_API"] = "1"
    try:
        try:
            from . import bmk_design_patch_board as board
            from . import bmk_design_patch_store as store
        except ImportError:
            import bmk_design_patch_board as board
            import bmk_design_patch_store as store
        board.set_server_mode(True)
        if jobs_module is None:
            try:
                from . import bmk_design_patch_jobs as jobs_module
            except ImportError:
                import bmk_design_patch_jobs as jobs_module
        runner = jobs_module.JobRunner(workers=config.workers)
        data_dir = os.path.realpath(os.path.abspath(config.data_dir))
        secret = _server_secret(data_dir)
        sessions = SessionStore(config.idle_ttl, config.max_ttl, clock=clock)
        prefs = _Preferences(store)
        if verifier is None and config.dev_handoff:
            verifier = DevVerifier()
        if isinstance(verifier, DevVerifier) and not config.dev_handoff:
            verifier = None
        api = board.API
        blocked = {("POST", api + path) for path in
                   ("/open_folder", "/candidates/export", "/candidates/import", "/mask/ps_import")}
        blocked.update(("GET", api + path) for path in ("/candidates/ext", "/mask/ps_list", "/pipeline/prompt"))

        def public(request):
            if request.method in ("GET", "HEAD"):
                return (request.path in ("/", "/api/session", api + "/env", api + "/board", api + "/board/")
                        or re.fullmatch(re.escape(api) + r"/board/[^/]+", request.path) is not None)
            return request.method == "POST" and request.path in ("/api/session", "/api/session/logout")

        def host_allowed(request):
            try:
                host = _host(request.headers.get("Host"))
                if config.allowed_hosts:
                    return host in config.allowed_hosts
                if config.public_origin and host == urlsplit(config.public_origin).netloc:
                    return True
                return config.dev_handoff and urlsplit("//" + host).hostname in ("localhost", "127.0.0.1", "::1")
            except ValueError:
                return False

        @web.middleware
        async def protect(request, handler):
            response = None
            try:
                if not host_allowed(request):
                    return _error("bad_origin")
                mutation = request.method not in ("GET", "HEAD")
                own_origin = config.public_origin or request.scheme + "://" + request.headers["Host"]
                if mutation and (request.headers.get("X-DesignPatch-Request") != "1"
                                 or request.headers.get("Origin") != own_origin):
                    return _error("bad_origin")
                session = sessions.get(request.cookies.get(COOKIE))
                request["bmk_dp_session"] = session
                if not public(request) and session is None:
                    return _error("no_session")
                if session:
                    tag = request.headers.get("X-DesignPatch-Account")
                    if tag is not None and tag != session["tag"]:
                        return _error("session_changed")
                    if mutation and not public(request) and tag is None:
                        return _error("bad_origin")
                method = "GET" if request.method == "HEAD" else request.method
                if (method, request.path) in blocked:
                    return _error("server_mode")
                scope_token = board.set_scope(session["scope"] if session else None)
                try:
                    return await handler(request)
                finally:
                    board.reset_scope(scope_token)
            except board._Reject as e:
                response = board._json({"error": e.message, **e.extra}, status=e.status)
            except web.HTTPException as e:
                response = e
            except Exception:
                # 검증기·본문 등에서 받은 비밀을 traceback 이나 오류 문구에 싣지 않는다.
                logger.error("서버 요청 처리 실패")
                response = web.json_response({"error": "요청을 처리하지 못했습니다."}, status=500)
            return response

        async def headers(request, response):
            response.headers["Content-Security-Policy"] = "frame-ancestors " + (" ".join(config.parent_origins) or "'none'")
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "same-origin"
            if not public(request):
                vary = response.headers.get("Vary", "")
                if "cookie" not in {v.strip().lower() for v in vary.split(",")}:
                    response.headers["Vary"] = (vary + ", " if vary else "") + "Cookie"
            if "Cache-Control" not in response.headers and (response.content_type == "application/json"
                                                           or request.path == api + "/export/file"):
                response.headers["Cache-Control"] = "no-store"

        def account(session):
            return {"name": session["name"], "tag": session["tag"]} if session else None

        def env(request):
            session = request.get("bmk_dp_session")
            return {"server_mode": True, "session": session is not None, "account": account(session),
                    "session_url": "/api/session", "prefs_url": "/api/preferences",
                    "caps": {name: name in ("jobs", "export_download") for name in board.LOCAL_CAPS}, "backend": "mock"}

        async def index(request):
            raise web.HTTPFound(api + "/board/" + ("?" + request.query_string if request.query_string else ""))

        async def session_get(request):
            session = request.get("bmk_dp_session")
            return web.json_response({"ok": True, "account": account(session)}) if session else _error("no_session")

        async def session_post(request):
            data = await _body(request, 16 * 1024)
            token, org = data.get("token"), data.get("organization_id")
            try:
                if not isinstance(token, str) or not 1 <= len(token) <= 4096 or not _valid_id(org):
                    raise web.HTTPBadRequest(text="토큰·조직 형식이 잘못되었습니다")
                if not config.dev_handoff and token.startswith("dev:"):
                    return _error("bad_token")
                if verifier is None:
                    return _error("no_verifier")
                try:
                    identity = await verifier.verify(token, org)
                except Exception:
                    return _error("bad_token")
            finally:
                data.clear()
                token = None
            if (not isinstance(identity, dict) or not all(_valid_id(identity.get(k)) for k in ("account_id", "organization_id", "name"))
                    or identity["organization_id"] != org):
                return _error("bad_token")
            try:
                scope = await asyncio.to_thread(_account_scope, data_dir, identity, store, clock())
            except (OSError, ValueError, TypeError):
                return _error("bad_token")
            tag = hmac.new(secret, (org + "\0" + identity["account_id"]).encode("utf-8"), hashlib.sha256).hexdigest()[:16]
            sid = sessions.create(identity, scope, tag, request.cookies.get(COOKIE))
            response = web.json_response({"ok": True, "account": {"name": identity["name"], "tag": tag}})
            secure = (config.public_origin or request.scheme + "://").startswith("https://")
            response.set_cookie(COOKIE, sid, httponly=True, path="/", samesite=config.cookie_samesite, secure=secure)
            return response

        async def logout(request):
            sessions.delete(request.cookies.get(COOKIE))
            response = web.json_response({"ok": True})
            response.del_cookie(COOKIE, path="/", samesite=config.cookie_samesite, httponly=True,
                                secure=(config.public_origin or request.scheme + "://").startswith("https://"))
            return response

        async def preferences(request):
            data = await _body(request, 2 * 1024 ** 2) if request.method == "POST" else None
            try:
                result = await asyncio.to_thread(prefs.apply, board.current_scope()["home"], data)
            except (ValueError, UnicodeError):
                raise web.HTTPBadRequest(text="설정 키·값 형식 또는 크기가 잘못되었습니다") from None
            return web.json_response(result)

        async def export_file(request):
            root = board._root_param(request.query.get("root"))

            def last_path():
                record = store.last_export(root, store.load_manifest(root)) or {}
                path = record.get("psd")
                if not isinstance(path, str) or not path or not _inside(path, board.output_dir()) or not os.path.isfile(path):
                    raise web.HTTPNotFound(text="내려받을 PSD 가 없습니다")
                return path

            path = await asyncio.to_thread(last_path)
            return web.FileResponse(path, headers={"Content-Type": "application/octet-stream", "Cache-Control": "no-store",
                                    "Content-Disposition": "attachment; filename*=UTF-8''" + quote(os.path.basename(path), safe="")})

        app = web.Application(middlewares=[protect], client_max_size=board._MAX_UPLOAD,
                              handler_args={"access_log": None})
        app[_SESSION_KEY], app[_RUNNER_KEY] = sessions, runner
        app.on_response_prepare.append(headers)
        routes = web.RouteTableDef()
        board.register_routes(routes, base_dir=None)
        app.add_routes(routes)
        for method, path, handler in jobs_module.routes(runner):
            app.router.add_route(method, path, handler)
        app.add_routes([web.get("/", index), web.get("/api/session", session_get), web.post("/api/session", session_post),
                        web.post("/api/session/logout", logout), web.get("/api/preferences", preferences),
                        web.post("/api/preferences", preferences), web.get(api + "/export/file", export_file)])
        board.set_env_provider(env)

        async def cleanup(app):
            try:
                await asyncio.to_thread(runner.close)
            finally:
                sessions.clear()
                board.set_env_provider(None)
                board.set_server_mode(False)
                if old_mock is None:
                    os.environ.pop("BMK_DP_MOCK_API", None)
                else:
                    os.environ["BMK_DP_MOCK_API"] = old_mock

        app.on_cleanup.append(cleanup)
        return app
    except BaseException:
        try:
            if runner is not None:
                runner.close()
        finally:
            if "board" in locals():
                board.set_env_provider(None)
                board.set_server_mode(False)
            os.environ.pop("BMK_DP_SERVER_MODE", None)
            if old_mock is None:
                os.environ.pop("BMK_DP_MOCK_API", None)
            else:
                os.environ["BMK_DP_MOCK_API"] = old_mock
        raise


def make_dev_parent_app(config):
    config = _validate_config(config)
    if not config.dev_handoff or config.dev_parent_port is None:
        raise ValueError("가짜 부모 페이지는 개발 모드·부모 포트가 필요합니다")
    host = config.host
    if ":" in host and not host.startswith("["):
        host = "[" + host + "]"

    async def index(request):
        acct = request.query.get("acct", "alice")
        if re.fullmatch(r"[a-z0-9_-]{1,32}", acct) is None:
            raise web.HTTPBadRequest(text="개발 계정 이름 형식이 잘못되었습니다")
        parent = "http://" + request.headers["Host"]
        source = f"http://{host}:{config.port}/?" + urlencode({"parent_origin": parent})
        source += "#" + urlencode({"token": "dev:" + acct, "organization_id": "org1"})
        page = ('<!doctype html><html lang="ko"><meta charset="utf-8"><title>Forge 개발</title>'
                '<body style="margin:0"><header style="height:56px;display:flex;align-items:center;gap:24px">'
                '<strong>Forge 개발</strong><a href="?acct=alice">alice</a><a href="?acct=bob">bob</a></header>'
                '<iframe title="Design Patch Studio" style="border:0;width:100%;height:calc(100dvh - 56px)" '
                'sandbox="allow-scripts allow-same-origin allow-downloads allow-popups allow-popups-to-escape-sandbox" '
                'allow="clipboard-write" referrerpolicy="origin" src="' + html.escape(source, quote=True) + '"></iframe></body></html>')
        return web.Response(text=page, content_type="text/html", headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    app = web.Application(handler_args={"access_log": None})
    app.add_routes([web.get("/", index)])
    return app


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Design Patch Studio 서버 모드")
    parser.add_argument("--data", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8791)
    parser.add_argument("--public-origin")
    parser.add_argument("--parent-origin", action="append", default=[])
    parser.add_argument("--allowed-host", action="append", default=[])
    parser.add_argument("--dev-handoff", action="store_true")
    parser.add_argument("--dev-parent-port", type=int)
    parser.add_argument("--cookie-samesite", default="Lax")
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args(argv)
    parents = list(args.parent_origin)
    if args.dev_parent_port:
        parents.extend(f"http://{host}:{args.dev_parent_port}" for host in ("localhost", "127.0.0.1"))
    config = ServerConfig(args.data, host=args.host, port=args.port, public_origin=args.public_origin,
                          parent_origins=tuple(dict.fromkeys(parents)), allowed_hosts=tuple(args.allowed_host),
                          dev_handoff=args.dev_handoff, dev_parent_port=args.dev_parent_port,
                          cookie_samesite=args.cookie_samesite, workers=args.workers)
    try:
        config = _validate_config(config)
    except ValueError as e:
        parser.error(str(e))
    os.environ["BMK_DP_SERVER_MODE"] = "1"  # Design Patch 모듈을 더 import 하기 전에 건다.

    async def serve():
        runners = []
        try:
            app = make_app(config)
            runner = web.AppRunner(app, access_log=None)
            runners.append(runner)
            await runner.setup()
            await web.TCPSite(runner, config.host, config.port).start()
            print(f"Design Patch Studio 서버 모드: http://{config.host}:{config.port}/", flush=True)
            if config.dev_parent_port:
                parent = web.AppRunner(make_dev_parent_app(config), access_log=None)
                runners.append(parent)
                await parent.setup()
                await web.TCPSite(parent, config.host, config.dev_parent_port).start()
                print(f"Forge 개발 부모: http://{config.host}:{config.dev_parent_port}/", flush=True)
            await asyncio.Event().wait()
        finally:
            for runner in reversed(runners):
                await runner.cleanup()

    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        pass
    finally:
        os.environ.pop("BMK_DP_SERVER_MODE", None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
