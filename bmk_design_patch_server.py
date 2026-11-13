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
import ipaddress
import json
import logging
import math
import os
import re
import secrets
import shutil
import sys
import threading
import time
import unicodedata
from collections import OrderedDict
from urllib.parse import quote, urlencode, urlsplit

from aiohttp import web
from yarl import URL

if __package__ in (None, ""):
    # `python bmk_design_patch_server.py` 로 직접 실행할 때: python_embeded(._pth 가 import 경로를 정함)는 스크립트 폴더를
    # import 경로에 넣지 않는다 → 형제 모듈(bmk_design_patch_board 등)을 최상위 모듈로 읽게 이 폴더를 넣는다.
    _HERE = os.path.dirname(os.path.abspath(__file__))
    if _HERE not in sys.path:
        sys.path.insert(0, _HERE)

logger = logging.getLogger(__name__)
COOKIE = "bmk_dp_session"
_PREF_KEY = re.compile(r"bmk-dp-[\w:.-]{1,80}")
_SESSION_KEY = web.AppKey("bmk_dp_sessions", object)
_RUNNER_KEY = web.AppKey("bmk_dp_runner", object)
_LOGIN_FAILURES_KEY = web.AppKey("bmk_dp_login_failures", object)
_FORGE_KEY = web.AppKey("bmk_dp_forge", object)
_ACCOUNT_LOCK = threading.Lock()
_ERRORS = {
    "bad_origin": (403, "요청 헤더·출처 검사에 걸렸습니다."),
    "no_session": (401, "Forge에서 Design Patch Studio를 다시 열어 주세요."),
    "session_changed": (409, "다른 계정으로 바뀌었습니다 — Forge에서 Design Patch Studio를 다시 열어 주세요."),
    "server_mode": (404, "서버 모드에서는 쓸 수 없는 기능입니다."),
    "bad_token": (401, "토큰을 확인하지 못했습니다 — Forge에서 Design Patch Studio를 다시 열어 주세요."),
    "no_verifier": (503, "토큰 검증기가 설정되지 않았습니다."),
    "rate_limited": (429, "로그인 시도가 너무 많습니다 — 잠시 뒤 Forge에서 Design Patch Studio를 다시 열어 주세요."),
    "no_access": (403, "Design Patch Studio 사용 권한이 없습니다. 담당자에게 문의해 주세요."),
    "forge_unreachable": (503, "Forge 에 연결하지 못했습니다 — 잠시 뒤 다시 시도해 주세요."),
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
    if config.dev_handoff and config.host not in ("127.0.0.1", "::1", "localhost"):
        raise ValueError("개발 모드는 루프백 주소(127.0.0.1·::1·localhost)에서만 띄울 수 있습니다")
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
    if (type(config.login_fail_limit) is not int or config.login_fail_limit < 1
            or type(config.login_fail_global) is not int or config.login_fail_global < 1
            or type(config.login_fail_window) not in (int, float)
            or not math.isfinite(config.login_fail_window) or config.login_fail_window <= 0):
        raise ValueError("로그인 실패 한도는 양의 정수, 시간 창은 양의 유한한 초여야 합니다")
    if not isinstance(config.trusted_proxies, (tuple, list)):
        raise ValueError("trusted_proxies 는 IP 주소·CIDR 목록이어야 합니다")
    try:
        if any(not isinstance(address, str) for address in config.trusted_proxies):
            raise ValueError
        proxies = []
        for address in config.trusted_proxies:
            if "/" in address:
                interface = ipaddress.ip_interface(address)
                mapped = interface.ip.ipv4_mapped if interface.version == 6 else None
                if mapped is not None:
                    if interface.network.prefixlen < 96:
                        raise ValueError
                    proxy = ipaddress.ip_network(f"{mapped}/{interface.network.prefixlen - 96}", strict=False)
                else:
                    proxy = interface.network
            else:
                proxy = ipaddress.ip_address(address)
                if proxy.version == 6:
                    proxy = proxy.ipv4_mapped or proxy
            proxies.append(str(proxy))
    except ValueError:
        raise ValueError("trusted_proxies 는 IP 주소·CIDR 목록이어야 합니다") from None
    forge_config = config.forge
    if forge_config is not None:
        forge_config = dataclasses.replace(forge_config, allow_http=config.dev_handoff)
        if config.dev_handoff and any(URL(value).host not in ("localhost", "127.0.0.1", "::1")
                                      for value in (forge_config.api, *forge_config.media_origins)):
            raise ValueError("개발 모드의 Forge 주소는 루프백이어야 합니다")
    return dataclasses.replace(config, public_origin=origin, forge=forge_config,
                               parent_origins=tuple(_origin(p) for p in config.parent_origins),
                               allowed_hosts=tuple(_host(h) for h in config.allowed_hosts), trusted_proxies=tuple(proxies))


def _client_ip(value):
    """프록시가 붙인 포트는 떼고, mapped IPv6 는 신뢰 검사 전 IPv4 로 맞춘다."""
    value = value.strip()
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        match = re.fullmatch(r"(?:\[([^\]]+)\]|([^:]+)):([0-9]{1,5})", value)
        if match is None or int(match[3]) > 65535:
            raise ValueError("클라이언트 주소 형식이 잘못되었습니다") from None
        address = ipaddress.ip_address(match[1] or match[2])
    return (address.ipv4_mapped or address) if isinstance(address, ipaddress.IPv6Address) else address


def _client_address(request, trusted_proxies):
    remote = request.remote or ""
    try:
        address = _client_ip(remote)
    except ValueError:
        return remote
    networks = tuple(ipaddress.ip_network(proxy, strict=False) for proxy in trusted_proxies)

    def trusted(value):
        return any(value in network for network in networks)

    if trusted(address):
        forwarded = ",".join(request.headers.getall("X-Forwarded-For", ()))
        for value in reversed(forwarded.split(",")):
            try:
                candidate = _client_ip(value)
            except ValueError:
                break  # 불완전한 체인은 접속 상대의 버킷으로 돌린다.
            if not trusted(candidate):
                address = candidate
                break
    return str(ipaddress.ip_network(f"{address}/64", strict=False)) if address.version == 6 else str(address)


class LoginFailures:
    """거절 시각만 기억한다. 진행 중 수는 별도로 두어 LRU 에서 밀려나도 동시 요청을 놓치지 않는다."""

    def __init__(self, limit, window, global_limit, *, clock=time.time, max_clients=10000):
        self.limit, self.window, self.global_limit = limit, window, global_limit
        self.clock, self.max_clients = clock, max_clients
        self._clients = OrderedDict()
        self._global = []
        self._inflight = {}
        self._pending = 0
        self._last_clock = float("-inf")

    def retry_after(self, client):
        now = self.clock()
        failures = self._clients.get(client, [])
        if now >= self._last_clock:
            self._global[:] = [stamp for stamp in self._global if now - stamp < self.window]
            failures[:] = [stamp for stamp in failures if now - stamp < self.window]
            self._last_clock = now
        if client in self._clients:
            self._clients.move_to_end(client)
        waits = []
        if len(failures) + self._inflight.get(client, 0) >= self.limit:
            waits.append(max(1, math.ceil(min(failures) + self.window - now)) if failures else 1)
        # 전역 한도는 토큰 거절 이력이 있는 주소만 늦춘다. 새 주소·정상 사용자까지 함께 막지 않는다.
        if failures and len(self._global) > self.global_limit:
            waits.append(max(1, math.ceil(min(self._global) + self.window - now)))
        return max(waits, default=0)

    def begin(self, client):
        retry = self.retry_after(client)
        if retry:
            return retry
        self._inflight[client] = self._inflight.get(client, 0) + 1
        self._pending += 1
        return 0

    def reject(self, client):
        now = self.clock()
        self._last_clock = max(self._last_clock, now)
        self._clients.setdefault(client, []).append(now)
        self._clients.move_to_end(client)
        while len(self._clients) > self.max_clients:
            self._clients.popitem(last=False)
        self._global.append(now)

    def finish(self, client):
        active = self._inflight.get(client, 0)
        if active <= 0:
            return  # 종료 정리 뒤 도착한 finally 는 이미 비운 수를 다시 내리지 않는다.
        remaining = active - 1
        if remaining:
            self._inflight[client] = remaining
        else:
            del self._inflight[client]
        self._pending = max(0, self._pending - 1)

    def clear(self):
        self._clients.clear()
        self._global.clear()
        self._inflight.clear()
        self._pending = 0


def _public_json(data, board, scope=None):
    """안내 글만 가린다. 설정을 저장할 때 다시 보내는 경로 값은 바꾸지 않는다."""
    if not board.server_mode():
        return data

    def message(value):
        cleaned = board.public_error(value, scope)
        return value if cleaned == value else cleaned

    def items(values, *, messages=False, exports=False):
        result = values
        for index, value in enumerate(values):
            cleaned = message(value) if messages and isinstance(value, str) else visit(value)
            if exports and isinstance(cleaned, dict) and isinstance(cleaned.get("psd"), str):
                basename = os.path.basename(cleaned["psd"].replace("\\", "/"))
                if basename != cleaned["psd"]:
                    cleaned = {**cleaned, "psd": basename}
            if cleaned is not value:
                if result is values:
                    result = list(values)
                result[index] = cleaned
        return result

    def visit(value):
        if isinstance(value, list):
            return items(value)
        if not isinstance(value, dict):
            return value
        result = value
        for key, entry in value.items():
            if isinstance(entry, str) and (key in ("error", "warning", "start_blocked", "msg") or key.endswith(("_error", "_warning"))):
                cleaned = message(entry)
            elif isinstance(entry, list) and (key in ("notes", "failed", "pending", "reasons", "warnings")
                                              or key.endswith(("_pending", "_notes", "_errors", "_warnings"))):
                cleaned = items(entry, messages=True)
            elif key == "exports" and isinstance(entry, list):
                cleaned = items(entry, exports=True)
            else:
                cleaned = visit(entry)
            if cleaned is not entry:
                if result is value:
                    result = dict(value)
                result[key] = cleaned
        return result

    # 바뀐 부분만 복사한다. 같은 객체면 깊은 비교·재직렬화를 생략할 수 있다.
    return visit(data)


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
    try:
        from . import bmk_design_patch_board as board
    except ImportError:
        import bmk_design_patch_board as board
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
    try:
        board._sweep_new_tmp(home)
    except Exception as e:
        logger.warning("계정 임시 폴더 정리 실패: %s", type(e).__name__)
    return {"input": home, "output": os.path.join(home, "_output"), "home": home, "account": acct, "org": org}


@dataclasses.dataclass(repr=False)
class _SessionToken:
    token: str
    exp: float | None
    at: float


class SessionStore:
    """공개 세션과 토큰 전용 표를 분리한다. 작업의 토큰 조회는 유휴 시각을 늘리지 않는다."""

    def __init__(self, idle_ttl, max_ttl, *, clock=time.time, limit=10000):
        self.idle_ttl, self.max_ttl, self.clock, self.limit = idle_ttl, max_ttl, clock, limit
        self._sessions = {}
        self._tokens = {}
        self._rejected = {}
        self._access = {}
        self._refresh = {}
        self._lock = threading.Lock()

    def _live(self, sid, now):
        session = self._sessions.get(sid)
        if session and (now - session["seen"] >= self.idle_ttl or now - session["created"] >= self.max_ttl):
            self._delete(sid)
            return None
        return session

    def _delete(self, sid):
        session = self._sessions.pop(sid, None)
        self._tokens.pop(sid, None)
        self._refresh.pop(sid, None)
        if session:
            key = (session["organization_id"], session["account_id"])
            if not any((s["organization_id"], s["account_id"]) == key for s in self._sessions.values()):
                self._access.pop(key, None)
                self._rejected.pop(key, None)

    def get(self, sid):
        with self._lock:
            now = self.clock()
            session = self._live(sid, now)
            if session is None:
                return None
            session["seen"] = now
            return dict(session)

    def create(self, identity, scope, tag, old_sid=None):
        with self._lock:
            self._delete(old_sid)
            now = self.clock()
            for sid, session in list(self._sessions.items()):
                if now - session["seen"] >= self.idle_ttl or now - session["created"] >= self.max_ttl:
                    self._delete(sid)
            own = sorted((sid for sid, session in self._sessions.items()
                          if session["organization_id"] == identity["organization_id"]
                          and session["account_id"] == identity["account_id"]),
                         key=lambda sid: self._sessions[sid]["seen"])
            for sid in own[:max(0, len(own) - 19)]:
                self._delete(sid)  # 새 세션까지 계정당 20개. 전역 LRU 는 마지막 방어선이다.
            while len(self._sessions) >= self.limit:
                self._delete(min(self._sessions, key=lambda sid: self._sessions[sid]["seen"]))
            sid = secrets.token_urlsafe(32)
            while sid in self._sessions or sid == old_sid:
                sid = secrets.token_urlsafe(32)
            self._sessions[sid] = {k: identity[k] for k in ("account_id", "organization_id", "name")}
            self._sessions[sid].update(tag=tag, scope=dict(scope), created=now, seen=now)
            return sid

    def delete(self, sid):
        with self._lock:
            self._delete(sid)

    def set_token(self, sid, token, exp):
        with self._lock:
            if self._live(sid, self.clock()):
                self._tokens[sid] = _SessionToken(token, exp, self.clock())

    def token_for(self, org, acct):
        with self._lock:
            candidates = []
            for sid in list(self._sessions):
                session = self._live(sid, self.clock())
                entry = self._tokens.get(sid)
                if (session and entry and (session["organization_id"], session["account_id"]) == (org, acct)
                        and hashlib.sha256(entry.token.encode("utf-8")).digest() not in self._rejected.get((org, acct), ())):
                    candidates.append(entry)
            return max(candidates, key=lambda entry: (entry.exp if entry.exp is not None else entry.at, entry.at)).token if candidates else None

    def reject_token(self, org, acct, token):
        with self._lock:
            if any((s["organization_id"], s["account_id"]) == (org, acct) for s in self._sessions.values()):
                self._rejected.setdefault((org, acct), set()).add(hashlib.sha256(token.encode("utf-8")).digest())

    def drop_token(self, sid):
        with self._lock:
            self._tokens.pop(sid, None)

    def drop_account(self, org, acct):
        with self._lock:
            for sid, session in list(self._sessions.items()):
                if (session["organization_id"], session["account_id"]) == (org, acct):
                    self._delete(sid)

    def access_at(self, org, acct):
        with self._lock:
            return self._access.get((org, acct))

    def mark_access(self, org, acct, at):
        with self._lock:
            if any((s["organization_id"], s["account_id"]) == (org, acct) for s in self._sessions.values()):
                self._access[(org, acct)] = at

    def begin_refresh(self, sid, token, org):
        with self._lock:
            now = self.clock()
            session = self._live(sid, now)
            if session is None:
                return "missing"
            entry = self._tokens.get(sid)
            if entry and entry.token == token and session["organization_id"] == org:
                return "same"
            last, pending = self._refresh.get(sid, (float("-inf"), False))
            if pending or now - last < 5:
                return "limited"
            self._refresh[sid] = (now, True)
            return "verify"

    def finish_refresh(self, sid):
        with self._lock:
            if sid in self._refresh:
                self._refresh[sid] = (self._refresh[sid][0], False)

    def replace_token(self, sid, token, exp):
        with self._lock:
            session = self._live(sid, self.clock())
            if session is None:
                return False
            old = self._tokens.get(sid)
            rejected = old and hashlib.sha256(old.token.encode("utf-8")).digest() in self._rejected.get(
                (session["organization_id"], session["account_id"]), ())
            if old and not rejected and old.exp is not None and (exp is None or exp < old.exp):
                return True
            self._tokens[sid] = _SessionToken(token, exp, self.clock())
            session["token_refresh"] = True
            return True

    def clear(self):
        with self._lock:
            self._sessions.clear()
            self._tokens.clear()
            self._rejected.clear()
            self._access.clear()
            self._refresh.clear()


async def _body(request, limit):
    # request.json/read 의 본문 캐시에 로그인 토큰을 남기지 않고, 청크 전송에도 상한을 건다.
    if request.content_type != "application/json":
        return web.json_response({"error": "JSON 본문이 필요합니다"}, status=400)
    if request.content_length is not None and request.content_length > limit:
        return web.json_response({"error": "요청 본문이 너무 큽니다"}, status=400)
    raw = bytearray()
    try:
        async for chunk in request.content.iter_chunked(min(limit + 1, 65536)):
            raw.extend(chunk)
            if len(raw) > limit:
                return web.json_response({"error": "요청 본문이 너무 큽니다"}, status=400)
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError
        return data
    except (UnicodeError, ValueError, RecursionError):
        return web.json_response({"error": "JSON 본문을 읽을 수 없습니다"}, status=400)
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
    close_timeout: float = 30.0                # 서버를 내릴 때 실행 중인 작업을 기다리는 시간(초 — 새 호출만 멈추고 진행 중인 것은 받는다)
    dev_parent_port: int | None = None         # 개발용 가짜 부모 페이지 포트(없으면 띄우지 않음)
    login_fail_limit: int = 10                 # 클라이언트별 거절 + 진행 중 상한
    login_fail_window: float = 300             # 거절 기록의 이동 창(초)
    login_fail_global: int = 200               # 서버 전체 거절이 넘으면 거절 이력이 있는 주소만 제한
    trusted_proxies: tuple = ()                # 주소·CIDR. 오른쪽부터 신뢰한 프록시를 건너뛴다
    forge: object | None = None                # bmk_design_patch_forge.ForgeConfig — 있으면 실제 검증기·Forge 생성 백엔드(F2), 없으면 F1 그대로(mock)


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


def make_app(config: ServerConfig, *, verifier=None, jobs_module=None, clock=time.time, forge_transport=None) -> web.Application:
    """서버 모드 앱. verifier 가 None 이고 dev_handoff 면 DevVerifier, 둘 다 없으면 POST /api/session 은 503."""
    config = _validate_config(config)
    old_mock = os.environ.get("BMK_DP_MOCK_API")
    old_forge = os.environ.get("BMK_DP_FORGE")
    runner = None
    # main 을 통하지 않는 시험·임베딩에서도 노드의 첫 import 전에 모드를 정한다.
    os.environ["BMK_DP_SERVER_MODE"] = "1"
    if config.forge is None:
        os.environ["BMK_DP_MOCK_API"] = "1"
    else:
        os.environ["BMK_DP_FORGE"] = "1"
    try:
        try:
            from . import bmk_design_patch_board as board
            from . import bmk_design_patch_store as store
        except ImportError:
            import bmk_design_patch_board as board
            import bmk_design_patch_store as store
        try:
            from . import bmk_design_patch_forge as forge
        except ImportError:
            import bmk_design_patch_forge as forge
        board.set_server_mode(True)
        if jobs_module is None:
            try:
                from . import bmk_design_patch_jobs as jobs_module
            except ImportError:
                import bmk_design_patch_jobs as jobs_module
        sessions = SessionStore(config.idle_ttl, config.max_ttl, clock=clock)
        accounts = None
        if config.forge is not None:
            client = forge.ForgeClient(config.forge, transport=forge_transport)
            accounts = forge.ForgeAccounts(client, config.forge, tokens=sessions, clock=clock)
            if verifier is None:
                verifier = forge.ForgeVerifier(client, config.forge)
            runner = jobs_module.JobRunner(workers=config.workers, forge=accounts)
        else:
            runner = jobs_module.JobRunner(workers=config.workers)
        data_dir = os.path.realpath(os.path.abspath(config.data_dir))
        secret = _server_secret(data_dir)
        login_failures = LoginFailures(config.login_fail_limit, config.login_fail_window, config.login_fail_global, clock=clock)
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
        async def public_json(request, handler):
            response = await handler(request)
            # 파일·그림·스트리밍은 그대로 둔다. protect 가 범위를 되돌린 뒤라 요청의 세션 범위를 넘긴다.
            if (board.server_mode() and isinstance(response, web.Response)
                    and response.content_type == "application/json" and isinstance(response.body, bytes)
                    and any(word in response.body for word in (b"error", b"warning", b"pending", b"notes", b"failed",
                                                              b"reasons", b"msg", b"exports", b"start_blocked"))):
                scope = (request.get("bmk_dp_session") or {}).get("scope")

                def clean_body():
                    data = json.loads(response.body.decode(response.charset or "utf-8"))
                    cleaned = _public_json(data, board, scope)
                    return json.dumps(cleaned, ensure_ascii=False) if cleaned is not data else None

                cleaned = await asyncio.to_thread(clean_body) if len(response.body) > 64 * 1024 else clean_body()
                if cleaned is not None:
                    response.text = cleaned
            return response

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
                if config.forge is None and request.path == "/api/session/token":
                    raise web.HTTPNotFound()
                session = sessions.get(request.cookies.get(COOKIE))
                request["bmk_dp_session"] = session
                if not public(request) and session is None:
                    return _error("no_session")
                if session and not public(request):
                    tag = request.headers.get("X-DesignPatch-Account")
                    if tag is not None and tag != session["tag"]:
                        return _error("session_changed")
                    if mutation and tag is None:
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
                message = e.safe if isinstance(e, board._PathReject) and e.safe else e.message
                scope = (request.get("bmk_dp_session") or {}).get("scope")
                response = board._json({"error": board.public_error(message, scope), **e.extra}, status=e.status)
            except web.HTTPException as e:
                response = e
            except Exception as e:
                # 검증기·본문 등에서 받은 비밀을 traceback 이나 오류 문구에 싣지 않는다.
                logger.error("서버 요청 처리 실패: %s", type(e).__name__)
                response = web.json_response({"error": "요청을 처리하지 못했습니다."}, status=500)
            return response

        async def headers(request, response):
            # 브라우저는 frame-ancestors 에 IPv6 리터럴 origin(http://[::1]:p)을 받지 않고 응답마다 콘솔 오류를 낸다(Chrome 확인) — 싣지 않는다.
            ancestors = [p for p in config.parent_origins if "[" not in p]
            response.headers["Content-Security-Policy"] = "frame-ancestors " + (" ".join(ancestors) or "'none'")
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
            result = {"server_mode": True, "session": session is not None, "account": account(session),
                    "session_url": "/api/session", "prefs_url": "/api/preferences",
                    "caps": {name: name in ("jobs", "export_download") for name in board.LOCAL_CAPS}, "backend": "mock"}
            if accounts is not None:
                result["backend"] = "mock" if os.environ.get("BMK_DP_MOCK_API", "").strip().lower() in ("1", "true", "yes", "on") else "forge"
                result["caps"].update(forge_import=bool(forge_routes), forge_save=bool(forge_routes))
                result.update(token_url="/api/session/token", token_refresh=bool(session and session.get("token_refresh")),
                              parent_origins=list(config.parent_origins),
                              messages={"session": config.forge.session_message, "request": config.forge.request_message})
            return result

        async def index(request):
            raise web.HTTPFound(api + "/board/" + ("?" + request.query_string if request.query_string else ""))

        async def session_get(request):
            session = request.get("bmk_dp_session")
            return web.json_response({"ok": True, "account": account(session)}) if session else _error("no_session")

        async def session_post(request):
            client = _client_address(request, config.trusted_proxies)
            retry = login_failures.retry_after(client)  # 막혔으면 본문을 읽지 않는다. 수신 중에는 자리를 잡지 않는다.
            if retry:
                response = _error("rate_limited")
                response.headers["Retry-After"] = str(retry)
                return response
            try:
                data = await asyncio.wait_for(_body(request, 16 * 1024), timeout=10)
            except asyncio.TimeoutError:
                return web.json_response({"error": "JSON 본문을 읽을 수 없습니다"}, status=400)
            if isinstance(data, web.Response):
                return data
            reserved = False
            try:
                token, org = data.get("token"), data.get("organization_id")
                try:
                    if not isinstance(token, str) or not 1 <= len(token) <= 4096 or not _valid_id(org):
                        return web.json_response({"error": "토큰·조직 형식이 잘못되었습니다"}, status=400)
                    retry = login_failures.begin(client)  # 본문을 받는 사이 바뀐 한도를 다시 보고, await 없이 예약한다.
                    if retry:
                        response = _error("rate_limited")
                        response.headers["Retry-After"] = str(retry)
                        return response
                    reserved = True
                    if not config.dev_handoff and token.startswith("dev:"):
                        login_failures.reject(client)
                        return _error("bad_token")
                    if verifier is None:
                        return _error("no_verifier")
                    try:
                        identity = await verifier.verify(token, org)
                    except forge.ForgeError as error:
                        if error.reason == "unauthorized":
                            login_failures.reject(client)
                            return _error("bad_token")
                        if error.reason == "forbidden":
                            response = _error("no_access")
                            session = request.get("bmk_dp_session")
                            if session and error.account_id == session["account_id"] and org == session["organization_id"]:
                                sessions.drop_account(org, session["account_id"])
                                response.del_cookie(COOKIE, path="/", samesite=config.cookie_samesite, httponly=True,
                                                    secure=(config.public_origin or request.scheme + "://").startswith("https://"))
                            return response
                        return _error("forge_unreachable")
                    except Exception:
                        return _error("bad_token")
                finally:
                    data.clear()
                if (not isinstance(identity, dict) or not all(_valid_id(identity.get(k)) for k in ("account_id", "organization_id", "name"))
                        or identity["organization_id"] != org):
                    login_failures.reject(client)
                    return _error("bad_token")
                try:
                    scope = await asyncio.to_thread(_account_scope, data_dir, identity, store, clock())
                except (OSError, ValueError, TypeError):
                    return _error("bad_token")
                tag = hmac.new(secret, (org + "\0" + identity["account_id"]).encode("utf-8"), hashlib.sha256).hexdigest()[:16]
                sid = sessions.create(identity, scope, tag, request.cookies.get(COOKIE))
                if accounts is not None:
                    if "exp" in identity:
                        sessions.set_token(sid, token, identity["exp"])
                    sessions.mark_access(org, identity["account_id"], clock())
                response = web.json_response({"ok": True, "account": {"name": identity["name"], "tag": tag}})
                secure = (config.public_origin or request.scheme + "://").startswith("https://")
                response.set_cookie(COOKIE, sid, httponly=True, path="/", samesite=config.cookie_samesite, secure=secure)
                return response
            finally:
                token = None
                if reserved:
                    login_failures.finish(client)

        async def session_token(request):
            try:
                data = await asyncio.wait_for(_body(request, 16 * 1024), timeout=10)
            except asyncio.TimeoutError:
                return web.json_response({"error": "JSON 본문을 읽을 수 없습니다"}, status=400)
            if isinstance(data, web.Response):
                return data
            sid = request.cookies.get(COOKIE)
            session = request.get("bmk_dp_session")
            reserved = False
            try:
                token, org = data.get("token"), data.get("organization_id")
                if not isinstance(token, str) or not 1 <= len(token) <= 4096 or not _valid_id(org):
                    return web.json_response({"error": "토큰·조직 형식이 잘못되었습니다"}, status=400)
                if org != session["organization_id"]:
                    sessions.drop_token(sid)
                    return _error("session_changed")
                state = sessions.begin_refresh(sid, token, org)
                if state == "missing":
                    return _error("no_session")
                if state == "same":
                    return web.json_response({"ok": True})
                if state == "limited":
                    return web.json_response({"error": "토큰 갱신 요청이 너무 잦습니다 — 잠시 뒤 다시 시도합니다.",
                                              "reason": "rate_limited"}, status=429, headers={"Retry-After": "5"})
                reserved = True
                if not config.dev_handoff and token.startswith("dev:"):
                    return _error("bad_token")
                try:
                    identity = await verifier.verify(token, org)
                except forge.ForgeError as error:
                    if error.reason == "unauthorized":
                        return _error("bad_token")
                    if error.reason == "forbidden":
                        if error.account_id != session["account_id"]:
                            sessions.drop_token(sid)
                            return _error("session_changed")
                        sessions.drop_account(session["organization_id"], session["account_id"])
                        return _error("no_access")
                    return _error("forge_unreachable")
                except Exception:
                    return _error("forge_unreachable")
                if not isinstance(identity, dict) or not all(_valid_id(identity.get(k)) for k in ("account_id", "organization_id")):
                    return _error("forge_unreachable")
                if identity["account_id"] != session["account_id"] or identity["organization_id"] != session["organization_id"] or org != session["organization_id"]:
                    sessions.drop_token(sid)
                    return _error("session_changed")
                if not sessions.replace_token(sid, token, identity.get("exp")):
                    return _error("no_session")
                sessions.mark_access(org, identity["account_id"], clock())
                return web.json_response({"ok": True})
            finally:
                data.clear()
                token = None
                if reserved:
                    sessions.finish_refresh(sid)

        async def logout(request):
            sessions.delete(request.cookies.get(COOKIE))
            response = web.json_response({"ok": True})
            response.del_cookie(COOKIE, path="/", samesite=config.cookie_samesite, httponly=True,
                                secure=(config.public_origin or request.scheme + "://").startswith("https://"))
            return response

        async def preferences(request):
            data = await _body(request, 2 * 1024 ** 2) if request.method == "POST" else None
            if isinstance(data, web.Response):
                return data
            try:
                result = await asyncio.to_thread(prefs.apply, board.current_scope()["home"], data)
            except (ValueError, UnicodeError):
                return web.json_response({"error": "설정 키·값 형식 또는 크기가 잘못되었습니다"}, status=400)
            return web.json_response(result)

        async def export_file(request):
            root = board._root_param(request.query.get("root"))

            def last_path():
                record = store.last_export(root, store.load_manifest(root)) or {}
                path = record.get("psd")
                if not isinstance(path, str) or not path or not _inside(path, board.output_dir()) or not os.path.isfile(path):
                    return None
                return path

            path = await asyncio.to_thread(last_path)
            if path is None:
                return web.json_response({"error": "내려받을 PSD 가 없습니다"}, status=404)
            return web.FileResponse(path, headers={"Content-Type": "application/octet-stream", "Cache-Control": "no-store",
                                    "Content-Disposition": "attachment; filename*=UTF-8''" + quote(os.path.basename(path), safe="")})

        app = web.Application(middlewares=[public_json, protect], client_max_size=8 * 1024 ** 2,
                              handler_args={"access_log": None})
        app[_SESSION_KEY], app[_RUNNER_KEY] = sessions, runner
        app[_LOGIN_FAILURES_KEY] = login_failures
        forge_routes = forge.routes(accounts) if accounts is not None else ()
        if accounts is not None:
            app[_FORGE_KEY] = accounts
            app.router.add_post("/api/session/token", session_token)
        for method, path, handler in forge_routes:
            app.router.add_route(method, path, handler)
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
                await asyncio.to_thread(runner.close, config.close_timeout)
            finally:
                sessions.clear()
                login_failures.clear()
                board.set_env_provider(None)
                board.set_server_mode(False)
                if old_mock is None:
                    os.environ.pop("BMK_DP_MOCK_API", None)
                else:
                    os.environ["BMK_DP_MOCK_API"] = old_mock
                if config.forge is not None:
                    if old_forge is None:
                        os.environ.pop("BMK_DP_FORGE", None)
                    else:
                        os.environ["BMK_DP_FORGE"] = old_forge

        app.on_cleanup.append(cleanup)
        return app
    except BaseException:
        try:
            if runner is not None:
                runner.close()
        finally:
            if "sessions" in locals():
                sessions.clear()
            if "board" in locals():
                board.set_env_provider(None)
                board.set_server_mode(False)
            os.environ.pop("BMK_DP_SERVER_MODE", None)
            if old_mock is None:
                os.environ.pop("BMK_DP_MOCK_API", None)
            else:
                os.environ["BMK_DP_MOCK_API"] = old_mock
            if config.forge is not None:
                if old_forge is None:
                    os.environ.pop("BMK_DP_FORGE", None)
                else:
                    os.environ["BMK_DP_FORGE"] = old_forge
        raise


def make_dev_parent_app(config):
    config = _validate_config(config)
    if not config.dev_handoff or config.dev_parent_port is None:
        raise ValueError("가짜 부모 페이지는 개발 모드·부모 포트가 필요합니다")
    async def index(request):
        try:
            authority = _host(request.headers.get("Host"))
            host = urlsplit("//" + authority).hostname
            if host not in ("127.0.0.1", "::1", "localhost"):
                raise ValueError
        except ValueError:
            raise web.HTTPBadRequest(text="개발 부모 페이지는 루프백 호스트로 열어 주세요") from None
        if host == "::1":
            host = "[::1]"
        acct = request.query.get("acct", "alice")
        if re.fullmatch(r"[a-z0-9_-]{1,32}", acct) is None:
            raise web.HTTPBadRequest(text="개발 계정 이름 형식이 잘못되었습니다")
        parent = "http://" + authority
        source = f"http://{host}:{config.port}/?" + urlencode({"parent_origin": parent})
        source += "#" + urlencode({"token": "dev:" + acct, "organization_id": "org1"})
        page = ('<!doctype html><html lang="ko"><meta charset="utf-8"><title>Forge 개발</title>'
                '<body style="margin:0;background:#09090b;color:#e4e4e7"><header style="height:64px;display:flex;align-items:center;gap:24px">'
                '<strong>Forge 개발</strong><a style="color:#a1a1aa" href="?acct=alice">alice</a><a style="color:#a1a1aa" href="?acct=bob">bob</a></header>'
                '<iframe title="Design Patch Studio" style="display:block;border:0;width:100%;height:calc(100dvh - 64px)" '
                'sandbox="allow-scripts allow-same-origin allow-downloads allow-popups allow-popups-to-escape-sandbox" '
                'allow="clipboard-write" referrerpolicy="origin" src="' + html.escape(source, quote=True) + '"></iframe></body></html>')
        return web.Response(text=page, content_type="text/html", headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    app = web.Application(handler_args={"access_log": None})
    app.add_routes([web.get("/", index)])
    try:
        from . import bmk_design_patch_forge_dev as forge_dev
    except ImportError:
        import bmk_design_patch_forge_dev as forge_dev
    forge_dev.mount(app, config)  # 개발용 가짜 Forge API·토큰 갱신 페이지(F2 D406·D407)
    return app


def _dev_parent_hint(config):
    if config.dev_parent_port and config.host == "::1":
        return (f"Forge 개발 부모는 http://localhost:{config.dev_parent_port}/ 로 여세요"
                "(브라우저가 [::1] 부모를 frame-ancestors 에서 받지 않을 수 있습니다)")
    return None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Design Patch Studio 서버 모드")
    parser.add_argument("--data", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8791)
    parser.add_argument("--public-origin")
    parser.add_argument("--parent-origin", action="append", default=[])
    parser.add_argument("--allowed-host", action="append", default=[])
    parser.add_argument("--trusted-proxy", action="append", default=[])
    parser.add_argument("--dev-handoff", action="store_true")
    parser.add_argument("--dev-parent-port", type=int)
    parser.add_argument("--cookie-samesite", default="Lax")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--forge-api")
    parser.add_argument("--forge-media-origin", action="append")
    parser.add_argument("--forge-access-key", action="append")
    parser.add_argument("--forge-concurrency", type=int)
    parser.add_argument("--forge-autopilot-opt-out")
    parser.add_argument("--forge-session-message")
    parser.add_argument("--forge-request-message")
    args = parser.parse_args(argv)
    forge_config = None
    if args.forge_api is None and any(getattr(args, name) is not None for name in (
            "forge_media_origin", "forge_access_key", "forge_concurrency", "forge_autopilot_opt_out",
            "forge_session_message", "forge_request_message")):
        parser.error("Forge 옵션은 --forge-api 와 함께 써야 합니다")
    if args.forge_api is not None:
        try:
            try:
                from .bmk_design_patch_forge import ForgeConfig
            except ImportError:
                from bmk_design_patch_forge import ForgeConfig
            forge_config = ForgeConfig(
                args.forge_api, media_origins=tuple(args.forge_media_origin or ()),
                access_keys=tuple(args.forge_access_key or ()), allow_http=args.dev_handoff,
                concurrency=args.forge_concurrency if args.forge_concurrency is not None else 2,
                prompt_autopilot=json.loads(args.forge_autopilot_opt_out) if args.forge_autopilot_opt_out is not None else False,
                session_message=args.forge_session_message if args.forge_session_message is not None else "design-patch-session",
                request_message=args.forge_request_message if args.forge_request_message is not None else "design-patch-session-request")
        except ValueError:
            parser.error("Forge 주소·동시 제출 수(1–8)·메시지·JSON 설정을 확인하세요")
    parents = list(args.parent_origin)
    if args.dev_parent_port:
        parents.extend(f"http://{host}:{args.dev_parent_port}" for host in ("localhost", "127.0.0.1", "[::1]"))
    config = ServerConfig(args.data, host=args.host, port=args.port, public_origin=args.public_origin,
                          parent_origins=tuple(dict.fromkeys(parents)), allowed_hosts=tuple(args.allowed_host),
                          dev_handoff=args.dev_handoff, dev_parent_port=args.dev_parent_port,
                          cookie_samesite=args.cookie_samesite, workers=args.workers, trusted_proxies=tuple(args.trusted_proxy),
                          forge=forge_config)
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
            if config.public_origin and not config.trusted_proxies:
                print("trusted_proxies 가 비어 있습니다 — 프록시 뒤에서는 모든 사용자가 같은 로그인 실패 한도를 공유합니다.", flush=True)
            if config.dev_parent_port:
                parent = web.AppRunner(make_dev_parent_app(config), access_log=None)
                runners.append(parent)
                await parent.setup()
                await web.TCPSite(parent, config.host, config.dev_parent_port).start()
                print(f"Forge 개발 부모: http://{config.host}:{config.dev_parent_port}/", flush=True)
                hint = _dev_parent_hint(config)
                if hint:
                    print(hint, flush=True)
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
