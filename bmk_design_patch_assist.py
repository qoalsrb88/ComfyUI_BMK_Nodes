"""서버 모드에서 크롭에 저장된 지시문을 Forge로 작성하고, 같은 스펙에 영문 본문을 저장한다."""

import asyncio
import base64
import io
import json
import logging
import math
import re
import threading
import time
import unicodedata
from collections import deque
from datetime import datetime, timezone

from PIL import Image

try:
    from . import bmk_design_patch_board as board
    from . import bmk_design_patch_forge as forge
    from . import bmk_design_patch_models as models
    from . import bmk_design_patch_prompt as dprompt
    from . import bmk_design_patch_runner as backend
    from . import bmk_design_patch_store as store
    from . import bmk_design_patch_v2 as specs
except ImportError:
    import bmk_design_patch_board as board
    import bmk_design_patch_forge as forge
    import bmk_design_patch_models as models
    import bmk_design_patch_prompt as dprompt
    import bmk_design_patch_runner as backend
    import bmk_design_patch_store as store
    import bmk_design_patch_v2 as specs


SKILLS = {
    "correct": ("scoped-component-edit", "element-add-replace"),
    "add": ("element-add-replace",),
    "remove": ("content-removal",),
    "vary": ("scoped-component-edit",),
    "detail": ("detail-repair",),
}
PURPOSE = {
    "correct": "Correct the specified target.",
    "add": "Add the requested element.",
    "remove": "Remove the specified target and restore the surrounding area.",
    "vary": "Create a variation of the specified target.",
    "detail": "Repair details of the specified target.",
}
_ROLES = {
    "canvas": "the original crop to edit",
    "guide": "a guide marking where the requested edit belongs",
    "before_paste": "the crop before the pasted element was added",
    "ref": "a visual design reference",
}
OUTPUT_CONTRACT = """Write only the request paragraph. The application supplies the input image list, the canvas rules for framing, composition and aspect ratio, and the rule to preserve everything outside the target. Omit these from your paragraph.
Describe what to change in @image1 and how. Use other images in the roles given in <user_prompt>. Refer to images only as @image1, @image2, etc., using only listed numbers.
Translate the user's instruction into English. This is an explicit translation request. Preserve names, numbers and literal text to appear in the image exactly as written; put any non-English text that must appear in the image inside double quotes.
Leave unspecified visual attributes open. Refer to the appropriate reference for details it supplies without inventing them.
Return only a JSON object with two string fields: body_en (the English request paragraph, at most 1500 characters, with no line beginning with Input images, Canvas:, Preserve:, Notes: or Request:) and summary_ko (one or two Korean sentences describing the requested edit)."""

_HEADS = ("Input images", "Canvas:", "Preserve:", "Notes:", "Request:")
_QUOTED = re.compile(r'"[^"\n]*"|\u201c[^\u201d\n]*\u201d|\u300c[^\u300d\n]*\u300d')
_REQUIRED = ("root", "crop_id", "seen", "crop_sig")
_STALE = "스펙이나 크롭이 다른 곳에서 바뀌었습니다 — 다시 읽기"
_TIMEOUT = "프롬프트 작성 시간이 초과되었습니다."
_BUSY = "앞선 프롬프트 작성이 끝난 뒤 다시 눌러 주세요."
_LOGGER = logging.getLogger(__name__)


def _access_error(exc):
    if exc.reason == "unauthorized":
        return board._Reject(401, "Forge 로그인 정보를 다시 받아야 합니다 — 잠시 뒤 자동으로 다시 시도합니다.", reason="forge_auth")
    if exc.reason == "forbidden":
        return board._Reject(403, "디자인 패치 스튜디오 사용 권한이 없습니다. 담당자에게 문의해 주세요.", reason="no_access")
    return board._Reject(503, forge.with_note("Forge 에 연결하지 못했습니다 — 잠시 뒤 다시 시도해 주세요.", exc),
                         reason="forge_unreachable")


def _one_line(text) -> str:
    """요청 글의 한 줄에 넣을 사용자 글: 줄바꿈·연속 공백은 공백 하나로, 꺾쇠는 괄호로 — 지시문으로 <user_prompt> 틀이나 줄 구조를
    흉내 낼 수 없게 한다."""
    return " ".join(str(text).replace("<", "(").replace(">", ")").split())


def _writer_input(root, view, purpose, instruction):
    with store.manifest_lock(root):
        crop = board._crop(store.load_manifest(root), view["crop_id"])
        if crop is None or board._vlm().crop_signature(crop) != view["crop_sig"]:
            raise board._Reject(409, _STALE, reason="stale")
    try:
        path = board._file_path(root, crop.get("source"))
        with Image.open(path) as source:
            rgb, _ = dprompt.flatten_canvas(source)
    except (board._Reject, OSError, ValueError, Image.DecompressionBombError):
        raise board._Reject(409, "먼저 반영이 필요합니다", reason="apply_needed") from None
    picture = Image.fromarray(rgb)
    picture.thumbnail((1536, 1536), Image.Resampling.LANCZOS)
    encoded = io.BytesIO()
    picture.save(encoded, format="JPEG", quality=85)
    image = "data:image/jpeg;base64," + base64.b64encode(encoded.getvalue()).decode("ascii")

    lines = ["<user_prompt>", "Purpose: " + PURPOSE[purpose]]
    if instruction:
        refs = {str(i): row["token"] for i, row in enumerate((r for r in view["images"] if r["role"] == "ref"), 1)}
        translated = re.sub(r"@참조([0-9]+)", lambda m: refs.get(m[1], m[0]), instruction.replace("@원본", "@image1"))
        lines.append("Instruction (Korean, from the user): " + _one_line(translated))
    lines.append("Images:")
    for row in view["images"]:
        role = row["role"]
        attached = "attached" if role == "canvas" else "not attached"
        lines.append(f"- {row['token']}: {_ROLES[role]} ({attached})")
    names = [t.get("name_en") or t.get("label") for t in crop.get("targets", [])]
    if any(names):
        lines.append("Targets in this crop: " + ", ".join(_one_line(name) for name in names if name))
    return "\n".join([*lines, "</user_prompt>"]), image


def _output(text, image_count, token, instruction=""):
    try:
        data = json.loads(text)
    except (ValueError, RecursionError):
        raise ValueError("작성 결과가 JSON 객체가 아닙니다.") from None
    if not isinstance(data, dict) or not all(isinstance(data.get(k), str) for k in ("body_en", "summary_ko")):
        raise ValueError("작성 결과에 body_en·summary_ko 문자열이 필요합니다.")
    body = data["body_en"].replace("\r\n", "\n").replace("\r", "\n").strip()
    problem = dprompt.body_problem(body, image_count)
    if problem:
        raise ValueError(problem)
    if len(body) > 1500:
        raise ValueError("작성한 본문은 1500자까지입니다.")
    # 그림 안에 들어갈 글자(간판·라벨 등)는 따옴표 안에 원문 그대로 둘 수 있다 — 따옴표 밖의 한글은 영어로 옮기지 않은 것이다.
    hangul = lambda s: any("HANGUL" in unicodedata.name(c, "") for c in s)  # noqa: E731
    if hangul(_QUOTED.sub("", body)):
        raise ValueError("영문 본문에 한글이 있습니다(그림에 넣을 글자는 큰따옴표 안에).")
    # 따옴표 안의 한글은 사용자가 지시문에 적은 글자 그대로여야 한다 — 번역하지 않은 지시를 따옴표로 감싼 본문을 받지 않는다.
    for quoted in _QUOTED.findall(body):
        inner = quoted[1:-1].strip()
        # 작성기는 한 줄로 고친 지시문(_one_line — 공백 정리·꺾쇠는 괄호)을 받았다 — 원문과 그 꼴 어느 쪽에 있어도 받는다.
        flat = " ".join(inner.split())
        if hangul(inner) and (len(inner) > 60 or not (flat in " ".join(instruction.split()) or flat in _one_line(instruction))):
            raise ValueError("영문 본문의 따옴표 안 한글이 지시문에 적힌 글자와 다릅니다.")
    if any(unicodedata.category(c) == "Cs" for c in body):
        raise ValueError("영문 본문에 잘못된 유니코드 문자가 있습니다.")
    if token in body or "data:image/" in body.lower() or re.search(r"(?:^|\s)/[^\s/]+/", body):
        raise ValueError("영문 본문에 비공개 데이터가 있습니다.")
    if any(line.lstrip().startswith(_HEADS) for line in body.splitlines()):
        raise ValueError("작성한 본문에 응용 프로그램이 붙이는 틀의 줄이 있습니다.")
    summary = data["summary_ko"]
    return body, "" if "data:image/" in summary.lower() else forge.display(summary, token, size=400)


def _save(root, view, prompt, disconnected):
    vlm = board._vlm()
    crop_id = view["crop_id"]

    def decide(cur):
        crop = board._crop(store.load_manifest(root), crop_id)
        if crop is None or vlm.crop_signature(crop) != view["crop_sig"]:
            raise vlm.SpecConflict(_STALE)
        # 표지(seen)는 스펙 파일 + 레퍼런스 구성이다. 모델은 스펙 파일을 쓰지 않고도 달라질 수 있어 입력 지문도 본다.
        now = specs.spec_view(root, crop_id)
        if now["seen"] != view["seen"] or now["input_hash"] != view["input_hash"]:
            raise vlm.SpecConflict(_STALE)
        v2 = view["v2"]
        updated = dprompt.v2_update(cur, purpose=v2["purpose"], user_instruction_ko=v2["user_instruction_ko"],
                                    ai_auto=True, prompt=prompt)
        if disconnected():
            raise asyncio.CancelledError
        return vlm.MergeResult(updated, updated != cur, [], False, False, [])

    with store.manifest_lock(root):
        try:
            vlm.write_spec(root, crop_id, decide)
        except vlm.SpecConflict as exc:
            raise board._Reject(409, str(exc), reason="stale") from None
        except ValueError as exc:
            raise board._Reject(422, str(exc)) from None
        return specs.spec_view(root, crop_id)


def _written(view, cached, calls):
    prompt = view["v2"]["prompt"]
    return {"ok": True, "status": "written", "reason": None, "cached": cached, "forge_calls": calls,
            **{k: view[k] for k in ("crop_id", "seen", "state", "input_hash", "model", "prompt_en")},
            "body_en": prompt["body_en"], "summary_ko": prompt.get("summary_ko", ""),
            "skill_ids": prompt.get("skill_ids", []), "guidance_revision": prompt.get("guidance_revision", "")}


def routes(accounts, *, model: str | None = None, timeout: float = 90.0, per_minute: int = 12, global_slots: int = 2,
           cache_ttl: float = 600.0, clock=time.monotonic) -> tuple:
    """이 앱의 Forge 계정과 부하 한도·지침 캐시를 공유하는 작성 라우트."""
    active, history, cache = set(), {}, {}

    def reserve(key):
        now = clock()
        for account, times in list(history.items()):
            while times and times[0] <= now - 60:
                times.popleft()
            if not times:
                del history[account]
        times = history.get(key)
        if times and len(times) >= per_minute:
            return math.ceil(times[0] + 60 - now)
        if key in active or len(active) >= global_slots or (times is None and len(history) >= 256):
            raise board._Reject(409, _BUSY, reason="busy")
        active.add(key)
        history.setdefault(key, deque()).append(now)
        return None

    async def write(root, scope, view, disconnected):
        deadline = clock() + timeout  # 접근 확인(마지막 확인이 오래됐을 때만 Forge 를 한 번 부른다)까지 같은 마감 안에서
        calls = 0
        try:
            await asyncio.wait_for(accounts.ensure_access(scope), timeout)
        except (forge.ForgeError, TimeoutError, asyncio.TimeoutError) as exc:
            # 접근 확인이 시간 안에 끝나지 않음(바깥 마감·Forge 창구의 자체 제한) = 작성 호출의 시간 초과와 같은 응답
            if isinstance(exc, forge.ForgeError) and exc.kind != "timeout":
                raise _access_error(exc) from None
            return {"ok": True, "status": "failed", "reason": "timeout", "error": _TIMEOUT, "crop_id": view["crop_id"],
                    "seen": view["seen"], "cached": False, "forge_calls": calls}
        ctx = accounts.context(scope)
        token = ctx["token_of"]()
        if token is None:
            raise _access_error(forge.ForgeError("unauthorized"))
        client = ctx["client"]
        v2 = view["v2"]
        purpose, instruction = v2["purpose"], v2["user_instruction_ko"].strip()

        async def invoke(method, *, key=None, keep=None, timed=True, **kwargs):
            """Forge 호출 하나. key 가 있으면 그 응답을 cache_ttl 초 기억한다 — keep(result) 가 참일 때만(실패·미지원 응답을 기억하면
            일시 장애가 기억 시간만큼 모든 요청에 되돌아간다). 기억은 조직 단위다."""
            nonlocal calls
            if disconnected():
                raise asyncio.CancelledError
            now = clock()
            if key is not None:
                key = (scope["org"], *key)
            if key in cache and now < cache[key][0]:
                return cache[key][1]
            remaining = deadline - now
            if remaining < 0.5:
                raise TimeoutError
            calls += 1
            if timed:  # 목록 API 에는 timeout 인자가 없다 — 응답 해석까지 포함해 같은 마감 안에 끝낸다
                kwargs["timeout"] = remaining
            result = await asyncio.wait_for(method(token, **kwargs), remaining)
            if clock() >= deadline:
                raise TimeoutError
            if key is not None and keep(result):
                cache[key] = (clock() + cache_ttl, result)
            return result

        def unsuccessful(status, reason, error=None):
            if error:
                error = "" if "data:image/" in error.lower() else forge.display(error, token, size=400)
            return {"ok": True, "status": status, "reason": reason, "error": board.public_error(error) if error else None,
                    "crop_id": view["crop_id"], "seen": view["seen"], "cached": False, "forge_calls": calls}

        now = clock()
        for key, (expires, _) in list(cache.items()):
            if expires <= now:
                del cache[key]
        try:
            caps = await invoke(client.prompt_capabilities, key=("capabilities",), keep=lambda r: r["available"] is True, timed=False)
            if not caps["available"]:
                return unsuccessful("unsupported", "guidance_unavailable")
            checkpoint = models.guidance_checkpoint(view["model"])
            profile = caps["profiles"].get(checkpoint)
            if profile is None:
                return unsuccessful("unsupported", "model_not_supported")
            skills = tuple(skill for skill in SKILLS[purpose] if skill in caps["skills"][profile])
            prepared = await invoke(client.prepare_prompt, key=("guidance", checkpoint, skills, caps["guidance_revision"]),
                                    keep=lambda r: r["status"] == "prepared",
                                    checkpoint=checkpoint, skill_ids=list(skills), request=(instruction or PURPOSE[purpose])[:6000],
                                    image_count=len(view["images"]))
            if prepared["status"] == "unsupported":
                reason = forge.display(prepared["reason"], token)
                return unsuccessful("unsupported", reason if reason and "data:image/" not in reason.lower() else "guidance")
            if prepared["status"] == "failed":
                return unsuccessful("failed", "guidance", prepared["reason"])
            guidance = prepared["guidance"]
            system = "\n\n".join([guidance["rewrite"], guidance["global"], guidance["model"],
                                  *(skill["body"] for skill in guidance["skills"]), OUTPUT_CONTRACT])
            prompt, image = await asyncio.wait_for(asyncio.to_thread(_writer_input, root, view, purpose, instruction),
                                                   max(0, deadline - clock()))
            response = await invoke(client.prompt_assist, system=system, prompt=prompt, image=image, model=model)
        except forge.ForgeError as exc:
            if exc.reason == "unauthorized":
                ctx["reject_token"](token)
            if exc.kind == "timeout":
                return unsuccessful("failed", "timeout", _TIMEOUT)
            raise _access_error(exc) from None
        except (TimeoutError, asyncio.TimeoutError):
            return unsuccessful("failed", "timeout", _TIMEOUT)
        if not response["ok"]:
            return unsuccessful("failed", "writer", response["error"])
        try:
            body, summary = _output(response["text"], len(view["images"]), token, instruction)
        except ValueError as exc:
            return unsuccessful("failed", "bad_output", str(exc))
        record = {"source": "assist", "body_en": body, "summary_ko": summary, "input_hash": view["input_hash"],
                  "written_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "skill_ids": prepared["skill_ids"],
                  "profile": prepared["profile"], "guidance_revision": prepared["guidance_revision"],
                  "writer": model or "forge-default"}
        if disconnected():
            raise asyncio.CancelledError
        return _written(await asyncio.to_thread(_save, root, view, record, disconnected), False, calls)

    @board._guard
    async def handler(request):
        scope = board.current_scope()
        if scope is None:
            raise board._Reject(401, board.NO_SESSION, reason="no_session")
        data = await board._body(request)
        unknown = data.keys() - {*_REQUIRED, "force"}
        if unknown:
            raise board._Reject(400, "모르는 키: " + ", ".join(sorted(unknown)))
        for field in _REQUIRED:
            if not isinstance(data.get(field), str):
                raise board._Reject(400, f"{field}는 필수 문자열입니다")
        if type(data.get("force", False)) is not bool:
            raise board._Reject(400, "force는 true/false여야 합니다")
        root = await asyncio.to_thread(board._root_param, data["root"])
        if await asyncio.to_thread(backend.backend_name, root) != backend.BACKEND_FORGE:
            raise board._Reject(409, "Forge 생성 백엔드가 연결되지 않았습니다.", reason="no_backend")
        view = await asyncio.to_thread(specs.spec_view, root, data["crop_id"])
        if any(data[k] != view[k] for k in ("seen", "crop_sig")):
            raise board._Reject(409, _STALE, reason="stale")
        v2 = view["v2"]
        if v2 is None and view["state"] == "blocked":
            raise board._Reject(409, view["why"], reason="bad_spec")
        if v2 and not v2["ai_auto"]:
            raise board._Reject(400, "AI 자동이 꺼져 있습니다 — 켜거나 영문 프롬프트를 직접 입력해 주세요", reason="ai_off")
        if v2 is None or (not v2["user_instruction_ko"].strip() and v2["purpose"] in ("correct", "add")):
            raise board._Reject(400, "지시문을 적어 주세요", reason="no_instruction")
        previous = v2.get("prompt") or {}
        if (not data.get("force") and previous.get("source") == "assist" and previous.get("input_hash") == view["input_hash"]
                and view["state"] == "body"):
            return board._json(_written(view, True, 0))
        key = (scope["org"], scope["account"])
        retry = reserve(key)
        if retry is not None:
            response = board._json({"error": board.public_error("프롬프트 작성 횟수가 많습니다 — 잠시 뒤 다시 시도해 주세요."),
                                    "reason": "rate_limited"}, status=429)
            response.headers["Retry-After"] = str(retry)
            return response
        started = clock()
        cancelled = threading.Event()

        def disconnected():
            transport = request.transport
            return cancelled.is_set() or transport is None or transport.is_closing() is True

        result = "failed"
        try:
            data = await write(root, scope, view, disconnected)
            result = data["status"]
            return board._json(data)
        except asyncio.CancelledError:
            result = "cancelled"
            raise
        except board._Reject as exc:
            result = exc.extra.get("reason", "rejected")
            raise
        except OSError:
            raise board._Reject(500, "작성에 필요한 파일을 읽거나 쓰지 못했습니다.") from None
        finally:
            cancelled.set()
            active.remove(key)
            _LOGGER.info("POST /prompt/assist %s %.3fs", result, clock() - started)

    return (("POST", board.API + "/prompt/assist", handler),)
