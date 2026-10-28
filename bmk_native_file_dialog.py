"""BMK Native File Dialog — 워크플로 저장·열기에 윈도우 기본 파일 창을 띄우는 서버 쪽.

배경
  ComfyUI 프론트엔드의 저장은 사용자 폴더의 workflows/ 라이브러리에 HTTP 로 쓰는 구조라
  파일명만 받는 입력창을 띄우고, 열기는 브라우저 <input type=file> 이라 시작 폴더를 정할 수 없다.
  브라우저는 서버 쪽 경로를 다루지 못하므로, 같은 PC에서 접속한 경우에 한해 서버가 윈도우
  Common Item Dialog(IFileSaveDialog / IFileOpenDialog)를 띄우고 고른 결과만 돌려준다.
  짝 JS: js/bmk_native_file_dialog.js

라우트 (윈도우가 아니거나 루프백이 아닌 곳에서 온 요청은 {"status": "unsupported"} → 프론트엔드 기본 동작)
  POST /bmk/native_dialog/save  {directory, filename}
      → {"status": "ok", "directory": "workflows/…", "filename": "….json"} | cancelled | busy
      저장 자체는 프론트엔드가 기존 userdata API 로 한다(덮어쓰기 확인·탭 상태가 그대로 동작).
      라이브러리 밖을 고르면 안내 창을 띄우고 라이브러리 루트에서 다시 연다.
  POST /bmk/native_dialog/open  {directory, extensions}
      → 라이브러리 안 .json: {"status": "ok", "path": "workflows/….json"} (프론트엔드가 라이브러리 탭으로 연다)
        그 밖(이미지·영상·바깥 .json): 파일 내용 + X-BMK-Filename 헤더 (프론트엔드가 기존 열기처럼 처리)
  directory 는 사용자 폴더 기준 상대 경로이며, 라이브러리 안의 있는 폴더일 때만 시작 폴더로 쓰고 아니면
  라이브러리 루트에서 연다. 마지막으로 쓴 폴더는 프론트엔드가 기억해 보낸다(윈도우의 최근 폴더 기억은
  SetFolder 와 함께 쓰면 갱신되지 않았다).
  창을 띄우다 실패하면 {"status": "error"} → 프론트엔드가 알리고 기본 동작.

설계 메모
  - 창은 전용 스레드 하나(STA)에서 띄워 이벤트 루프를 막지 않는다. 이미 창이 떠 있으면 busy.
  - 전경은 브라우저라 그냥 띄우면 창이 브라우저 뒤에 숨는다. 브라우저 창 가운데에 크기 0 인 최상위
    소유 창을 만들고, 전경 스레드에 입력을 잠시 붙여 그 창을 전경으로 올린 뒤 대화상자의 소유자로 쓴다.
  - 파일 내용을 돌려주는 것은 사람이 창에서 고른 파일 하나뿐이다. 경로를 받아 읽는 라우트는 없다.

버전 이력:
  v1 (2026-10): 저장 이름 입력창·열기를 윈도우 기본 파일 창으로.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import ctypes
import functools
import ipaddress
import logging
import os
import posixpath
import re
import sys
from ctypes import wintypes
from urllib.parse import quote

from aiohttp import web

logger = logging.getLogger(__name__)
_TAG = "[ComfyUI_BMK_Nodes::NativeFileDialog]"

API = "/bmk/native_dialog"
WORKFLOWS_DIR = "workflows"

# 프론트엔드가 MIME 으로만 구분하는 형식(이름으로도 구분하는 mp4·svg·glb·json 등은 추정에 맡긴다).
# 임베디드 파이썬의 mimetypes 는 .webp 를 모른다.
_MIME_BY_EXT = {
    ".png": "image/png",
    ".webp": "image/webp",
    ".avif": "image/avif",
    ".mp3": "audio/mpeg",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
    ".webm": "video/webm",
}
_EXT_RE = re.compile(r"^\.[A-Za-z0-9]{1,10}$")

_ERROR_CANCELLED = 0x800704C7  # HRESULT_FROM_WIN32(ERROR_CANCELLED): 사용자가 창을 닫음

if sys.platform == "win32":
    _ole32 = ctypes.OleDLL("ole32")
    _shell32 = ctypes.OleDLL("shell32")
    _user32 = ctypes.WinDLL("user32")
    _kernel32 = ctypes.WinDLL("kernel32")

    class _GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    class _FILTERSPEC(ctypes.Structure):
        _fields_ = [("pszName", wintypes.LPCWSTR), ("pszSpec", wintypes.LPCWSTR)]

    _ole32.IIDFromString.argtypes = (wintypes.LPCWSTR, ctypes.POINTER(_GUID))
    _ole32.CoInitializeEx.argtypes = (ctypes.c_void_p, wintypes.DWORD)
    _ole32.CoUninitialize.restype = None
    _ole32.CoCreateInstance.argtypes = (
        ctypes.POINTER(_GUID), ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p),
    )
    _ole32.CoTaskMemFree.argtypes = (ctypes.c_void_p,)
    _ole32.CoTaskMemFree.restype = None
    _shell32.SHCreateItemFromParsingName.argtypes = (
        wintypes.LPCWSTR, ctypes.c_void_p, ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p),
    )
    _user32.GetForegroundWindow.restype = wintypes.HWND
    _user32.GetWindowRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
    _user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.c_void_p)
    _user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    _user32.AttachThreadInput.argtypes = (wintypes.DWORD, wintypes.DWORD, wintypes.BOOL)
    _user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
    _user32.CreateWindowExW.argtypes = (
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p,
    )
    _user32.CreateWindowExW.restype = wintypes.HWND
    _user32.DestroyWindow.argtypes = (wintypes.HWND,)
    _user32.MessageBoxW.argtypes = (wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT)
    _user32.SetThreadDpiAwarenessContext.argtypes = (ctypes.c_void_p,)
    _user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    _kernel32.GetCurrentThreadId.restype = wintypes.DWORD
    _kernel32.GetLongPathNameW.argtypes = (wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD)
    _kernel32.GetLongPathNameW.restype = wintypes.DWORD

    def _guid(text: str) -> _GUID:
        g = _GUID()
        _ole32.IIDFromString(text, ctypes.byref(g))
        return g

    _CLSID_FILE_OPEN_DIALOG = _guid("{DC1C5A9C-E88A-4DDE-A5A1-60F82A20AEF7}")
    _CLSID_FILE_SAVE_DIALOG = _guid("{C0B4E2F3-BA21-4773-8DBA-335EC946EB8B}")
    _IID_IFILE_DIALOG = _guid("{42F85136-DB7E-439C-85F1-E4075D135FC8}")
    _IID_ISHELL_ITEM = _guid("{43826D1E-E718-42EE-BC55-A1E261C37BFE}")
    # 이 창들의 크기·보기 방식을 다른 파이썬 프로그램의 파일 창과 따로 기억하게 하는 키.
    _CLIENT_GUID = _guid("{6F1B5D2E-8C4A-4E7B-9B3D-2A7C1E5F9D40}")

_CLSCTX_INPROC_SERVER = 0x1
_COINIT_APARTMENTTHREADED = 0x2
_COINIT_DISABLE_OLE1DDE = 0x4
_FOS_OVERWRITEPROMPT = 0x2
_FOS_NOCHANGEDIR = 0x8
_FOS_FORCEFILESYSTEM = 0x40
_FOS_PATHMUSTEXIST = 0x800
_FOS_FILEMUSTEXIST = 0x1000
_SIGDN_FILESYSPATH = 0x80058000
_WS_POPUP = 0x80000000
_WS_EX_TOPMOST = 0x8
_WS_EX_TOOLWINDOW = 0x80
_MB_ICONWARNING = 0x30
_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4

# IFileDialog vtable 순서(ShObjIdl_core.h): IUnknown 3개 → IModalWindow::Show → IFileDialog 메서드.
_SHOW, _SET_FILE_TYPES, _SET_OPTIONS, _GET_OPTIONS, _SET_FOLDER, _SET_FILE_NAME = 3, 4, 9, 10, 12, 15
_SET_TITLE, _GET_RESULT, _SET_DEFAULT_EXTENSION, _SET_CLIENT_GUID = 17, 20, 22, 24
_ISHELLITEM_GET_DISPLAY_NAME = 5

# 창은 이 스레드 하나에서만 띄운다(COM STA). 동시에 두 창을 띄우지 않으려고 _busy 로 막는다.
_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="BMKNativeFileDialog")
_busy = False


# ─── COM 호출 ────────────────────────────────────────────────────

def _method(obj: ctypes.c_void_p, index: int, *argtypes):
    """COM 객체 obj 의 vtable index 번 메서드. 실패 HRESULT 는 OSError 로 올라온다."""
    vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    proto = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, *argtypes)
    return functools.partial(proto(vtbl[index]), obj)


def _release(obj: ctypes.c_void_p) -> None:
    vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtbl[2])(obj)


def _shell_item(path: str) -> ctypes.c_void_p:
    item = ctypes.c_void_p()
    _shell32.SHCreateItemFromParsingName(path, None, ctypes.byref(_IID_ISHELL_ITEM), ctypes.byref(item))
    return item


def _item_path(item: ctypes.c_void_p) -> str:
    raw = ctypes.c_void_p()
    _method(item, _ISHELLITEM_GET_DISPLAY_NAME, ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p))(
        _SIGDN_FILESYSPATH, ctypes.byref(raw)
    )
    try:
        return ctypes.wstring_at(raw.value)
    finally:
        _ole32.CoTaskMemFree(raw)


def _show(
    clsid,
    owner: int,
    title: str,
    filters: list[tuple[str, str]],
    *,
    options: int,
    folder: str,
    filename: str | None = None,
    default_ext: str | None = None,
) -> str | None:
    """folder 에서 파일 창 하나를 띄워 고른 파일의 절대 경로를 돌려준다. 취소면 None."""
    dlg = ctypes.c_void_p()
    _ole32.CoCreateInstance(
        ctypes.byref(clsid), None, _CLSCTX_INPROC_SERVER, ctypes.byref(_IID_IFILE_DIALOG), ctypes.byref(dlg)
    )
    try:
        _method(dlg, _SET_CLIENT_GUID, ctypes.POINTER(_GUID))(ctypes.byref(_CLIENT_GUID))
        current = wintypes.DWORD()
        _method(dlg, _GET_OPTIONS, ctypes.POINTER(wintypes.DWORD))(ctypes.byref(current))
        _method(dlg, _SET_OPTIONS, wintypes.DWORD)(current.value | options)
        specs = (_FILTERSPEC * len(filters))(*filters)
        _method(dlg, _SET_FILE_TYPES, ctypes.c_uint, ctypes.POINTER(_FILTERSPEC))(len(filters), specs)
        _method(dlg, _SET_TITLE, wintypes.LPCWSTR)(title)
        if default_ext:
            _method(dlg, _SET_DEFAULT_EXTENSION, wintypes.LPCWSTR)(default_ext)
        if filename:
            _method(dlg, _SET_FILE_NAME, wintypes.LPCWSTR)(filename)
        item = _shell_item(folder)
        try:
            _method(dlg, _SET_FOLDER, ctypes.c_void_p)(item)
        finally:
            _release(item)
        try:
            _method(dlg, _SHOW, wintypes.HWND)(owner)
        except OSError as e:
            if (e.winerror or 0) & 0xFFFFFFFF == _ERROR_CANCELLED:
                return None
            raise
        item = ctypes.c_void_p()
        _method(dlg, _GET_RESULT, ctypes.POINTER(ctypes.c_void_p))(ctypes.byref(item))
        try:
            return _item_path(item)
        finally:
            _release(item)
    finally:
        _release(dlg)


@contextlib.contextmanager
def _dialog_owner():
    """브라우저(전경 창) 가운데에 크기 0 인 최상위 창을 만들어 전경으로 올린다. 파일 창의 소유자로 쓴다."""
    fg = _user32.GetForegroundWindow()
    rect = wintypes.RECT()
    x = y = 0
    if fg and _user32.GetWindowRect(fg, ctypes.byref(rect)):
        x, y = (rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2
    owner = _user32.CreateWindowExW(
        _WS_EX_TOPMOST | _WS_EX_TOOLWINDOW, "STATIC", None, _WS_POPUP, x, y, 0, 0, None, None, None, None
    )
    if not owner:
        raise ctypes.WinError()
    try:
        # 전경 잠금 때문에 다른 프로세스 창을 그냥 올릴 수는 없다. 전경 스레드에 입력을 잠시 붙이면 허용된다.
        fg_thread = _user32.GetWindowThreadProcessId(fg, None) if fg else 0
        me = _kernel32.GetCurrentThreadId()
        attached = bool(fg_thread) and fg_thread != me and _user32.AttachThreadInput(me, fg_thread, True)
        _user32.SetForegroundWindow(owner)
        if attached:
            _user32.AttachThreadInput(me, fg_thread, False)
        yield owner
    finally:
        _user32.DestroyWindow(owner)


def _in_dialog_thread(fn, *args):
    def job():
        _user32.SetThreadDpiAwarenessContext(_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
        _ole32.CoInitializeEx(None, _COINIT_APARTMENTTHREADED | _COINIT_DISABLE_OLE1DDE)
        try:
            with _dialog_owner() as owner:
                return fn(owner, *args)
        finally:
            _ole32.CoUninitialize()

    return asyncio.get_running_loop().run_in_executor(_EXECUTOR, job)


# ─── 저장·열기 ───────────────────────────────────────────────────

def _inside(path: str, root: str) -> bool:
    path, root = os.path.normcase(os.path.abspath(path)), os.path.normcase(os.path.abspath(root))
    try:
        return os.path.commonpath((path, root)) == root
    except ValueError:  # 드라이브가 다름
        return False


def _pick_save(owner: int, root: str, folder: str, filename: str) -> str | None:
    while True:
        path = _show(
            _CLSID_FILE_SAVE_DIALOG, owner, "워크플로 저장", [("워크플로 (*.json)", "*.json")],
            # 덮어쓰기 확인은 프론트엔드가 한다(라이브러리 목록·열린 탭까지 맞춰 처리).
            options=_FOS_FORCEFILESYSTEM | _FOS_PATHMUSTEXIST | _FOS_NOCHANGEDIR,
            folder=folder, filename=filename, default_ext="json",
        )
        if path is None or _inside(path, root):
            return path
        _user32.MessageBoxW(owner, f"워크플로 폴더 안에만 저장할 수 있습니다.\n\n{root}", "워크플로 저장", _MB_ICONWARNING)
        folder, filename = root, os.path.basename(path)


def _pick_open(owner: int, folder: str, extensions: list[str]) -> str | None:
    filters = [("워크플로 (*.json)", "*.json"), ("모든 파일 (*.*)", "*.*")]
    if extensions:
        filters.insert(0, ("워크플로·이미지·영상", ";".join("*" + e for e in extensions)))
    return _show(
        _CLSID_FILE_OPEN_DIALOG, owner, "워크플로 열기", filters,
        options=_FOS_FORCEFILESYSTEM | _FOS_FILEMUSTEXIST | _FOS_NOCHANGEDIR,
        folder=folder,
    )


def _is_local(request: web.Request) -> bool:
    try:
        ip = ipaddress.ip_address(request.remote or "")
    except ValueError:
        return False
    mapped = getattr(ip, "ipv4_mapped", None)
    return ip.is_loopback or (mapped is not None and mapped.is_loopback)


def _user_root(request: web.Request) -> str | None:
    """사용자 폴더. 창이 돌려주는 경로와 비교할 수 있게 8.3 짧은 이름은 긴 이름으로 편다(정션은 그대로)."""
    from server import PromptServer

    try:
        path = PromptServer.instance.user_manager.get_request_user_filepath(request, None)
    except KeyError:  # 다중 사용자 모드의 모르는 사용자
        return None
    if path is None:
        return None
    buf = ctypes.create_unicode_buffer(32768)
    n = _kernel32.GetLongPathNameW(path, buf, len(buf))
    return buf.value if 0 < n < len(buf) else path


def _start_folder(user_root: str, root: str, directory) -> str:
    """프론트엔드가 보낸 사용자 폴더 기준 상대 경로. 라이브러리 안의 있는 폴더가 아니면 라이브러리 루트."""
    if isinstance(directory, str) and directory:
        path = os.path.abspath(os.path.join(user_root, directory))
        if _inside(path, root) and os.path.isdir(path):
            return path
    return root


def _relative(path: str, user_root: str) -> str:
    return os.path.relpath(path, user_root).replace(os.sep, "/")


async def _pick(fn, *args) -> str | None:
    """창을 띄우는 동안 _busy. 호출 전에 _busy 를 확인할 것(await 없이 바로 이어지므로 경합 없음).
    창을 띄우지 못하면 OSError."""
    global _busy
    _busy = True
    try:
        return await _in_dialog_thread(fn, *args)
    except OSError:
        logger.exception(f"{_TAG} 파일 창을 띄우지 못함")
        raise
    finally:
        _busy = False


async def _context(request: web.Request):
    """(사용자 폴더, 라이브러리 루트, 요청 본문). 이 PC의 창을 쓸 수 없는 요청이면 None."""
    if sys.platform != "win32" or not _is_local(request):
        return None
    user_root = _user_root(request)
    if user_root is None:
        return None
    try:
        body = await request.json()
    except ValueError:
        body = None
    root = os.path.join(user_root, WORKFLOWS_DIR)
    os.makedirs(root, exist_ok=True)
    return user_root, root, body if isinstance(body, dict) else {}


async def save_handler(request: web.Request) -> web.Response:
    ctx = await _context(request)
    if ctx is None:
        return web.json_response({"status": "unsupported"})
    if _busy:
        return web.json_response({"status": "busy"})
    user_root, root, body = ctx
    filename = body.get("filename") if isinstance(body.get("filename"), str) else ""
    try:
        path = await _pick(_pick_save, root, _start_folder(user_root, root, body.get("directory")), filename)
    except OSError as e:
        return web.json_response({"status": "error", "message": str(e)})
    if path is None:
        return web.json_response({"status": "cancelled"})
    rel = _relative(path, user_root)
    return web.json_response({"status": "ok", "directory": posixpath.dirname(rel), "filename": posixpath.basename(rel)})


async def open_handler(request: web.Request) -> web.StreamResponse:
    ctx = await _context(request)
    if ctx is None:
        return web.json_response({"status": "unsupported"})
    if _busy:
        return web.json_response({"status": "busy"})
    user_root, root, body = ctx
    extensions = body.get("extensions") if isinstance(body.get("extensions"), list) else []
    extensions = [e for e in extensions if isinstance(e, str) and _EXT_RE.match(e)]
    try:
        path = await _pick(_pick_open, _start_folder(user_root, root, body.get("directory")), extensions)
    except OSError as e:
        return web.json_response({"status": "error", "message": str(e)})
    if path is None:
        return web.json_response({"status": "cancelled"})
    if _inside(path, root) and path.lower().endswith(".json"):
        return web.json_response({"status": "ok", "path": _relative(path, user_root)})
    headers = {"X-BMK-Filename": quote(os.path.basename(path)), "Cache-Control": "no-store"}
    mime = _MIME_BY_EXT.get(os.path.splitext(path)[1].lower())
    if mime:
        headers["Content-Type"] = mime
    return web.FileResponse(path, headers=headers)


def register_routes() -> bool:
    """라우트를 PromptServer.instance.routes 에 더한다. 서버의 add_routes 전(노드 로딩 때) 불러야 /api 사본도 생긴다."""
    from server import PromptServer

    routes = PromptServer.instance.routes
    if any(isinstance(r, web.RouteDef) and r.path == f"{API}/save" for r in routes):
        return False
    routes.post(f"{API}/save")(save_handler)
    routes.post(f"{API}/open")(open_handler)
    return True
