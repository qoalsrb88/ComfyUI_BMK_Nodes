"""★ 후보 그림의 Forge 후보정 계획, 승인 원장과 서버 라우트."""

import asyncio
import base64
import functools
import hashlib
import io
import json
import math
import os
import re
import stat
import struct
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

from aiohttp import web
from PIL import Image

try:
    from . import bmk_design_patch_board as board
    from . import bmk_design_patch_forge_backend as fg
    from . import bmk_design_patch_jobs as jobs
    from . import bmk_design_patch_runner as runner
    from . import bmk_design_patch_store as store
    from .bmk_design_patch_forge import ForgeError, MEDIA_LIMIT
except ImportError:
    import bmk_design_patch_board as board
    import bmk_design_patch_forge_backend as fg
    import bmk_design_patch_jobs as jobs
    import bmk_design_patch_runner as runner
    import bmk_design_patch_store as store
    from bmk_design_patch_forge import ForgeError, MEDIA_LIMIT


LIMIT = 8
MODELS = ("pid_upscale", "magnific_v2")
FORMATS = {"PNG": ("png", "image/png"), "JPEG": ("jpg", "image/jpeg"), "WEBP": ("webp", "image/webp")}
MASK_OPTS = {"mask_offset": [-64, 64], "mask_blur": [0, 64]}
RATIO = "결과의 비율이 입력과 다릅니다 — 켜면 늘어나 보입니다"
SMALL = "업스케일 결과가 입력보다 크지 않습니다"
OPAQUE = "배경 제거 결과가 전부 불투명합니다"
CHANGED = "그 사이 다른 창에서 바뀌었습니다 — 받아 둔 결과는 다시 누르면 재사용됩니다"
INPUT_CHANGED = "입력 그림이 바뀌었습니다 — 계획을 다시 확인해 주세요"
SCHEMA = "bmk.design_patch.post/1"


def _bad_record():
    return board._Reject(422, "후보정 기록을 읽을 수 없습니다.")


def _path(root, rel):
    try:
        path = Path(store.resolve_path(root, rel))
    except ValueError:
        raise _bad_record() from None
    if not path.resolve().is_relative_to(Path(root).resolve()):
        raise _bad_record()
    return path


def _fields(data, allowed):
    extra = data.keys() - allowed
    if extra:
        raise board._Reject(400, "모르는 키: " + ", ".join(sorted(extra)))


def _integer(name, value, lo, hi, query):
    if query and isinstance(value, str) and re.fullmatch(r"-?[0-9]{1,3}", value):
        value = int(value)
    if type(value) is not int or not lo <= value <= hi:
        raise board._Reject(400, f"{name} 값은 {lo}–{hi} 정수여야 합니다.")
    return value


def _arguments(data, *, query):
    tool = data.get("tool")
    if tool not in store.POST_TOOLS:
        raise board._Reject(400, "tool 값은 upscale 또는 bg_removal 이어야 합니다.")
    allowed = {"root", "tool", "keys", "model"}
    allowed.update({"scale"} if tool == "upscale" else ({"offset", "blur"} if query else {"opts"}))
    if not query:
        allowed.update(("expect", "approve"))
        for field in ("expect", "approve"):
            if field in data and not isinstance(data[field], str):
                raise board._Reject(400, f"{field} 값은 문자열이어야 합니다.")
    _fields(data, allowed)
    keys = data.get("keys")
    if query and isinstance(keys, str):
        keys = keys.split(",")
    if not isinstance(keys, list) or not keys:
        raise board._Reject(400, "keys 에 후보를 한 장 이상 골라 주세요.")
    if len(keys) > LIMIT:
        raise board._Reject(400, "한 번에 8장까지 후보정할 수 있습니다.", reason="too_many")
    if any(not isinstance(k, str) or not board._KEY_RE.fullmatch(k) for k in keys) or len(set(keys)) != len(keys):
        raise board._Reject(400, "keys 의 후보 키가 잘못되었거나 중복되었습니다.")
    if tool == "upscale":
        model = data.get("model", MODELS[0])
        if model not in MODELS:
            raise board._Reject(400, "model 값이 업스케일 모델 목록에 없습니다.")
        scale = _integer("scale", data.get("scale", 2), 2, 4, query)
        if scale not in (2, 4):
            raise board._Reject(400, "scale 은 2 또는 4여야 합니다.")
        opts = {"scale": scale}
    else:
        model = data.get("model", "RMBG-2.0")
        if model != "RMBG-2.0":
            raise board._Reject(400, "배경 제거 model 은 RMBG-2.0 이어야 합니다.")
        options = data.get("opts", {})
        if not isinstance(options, dict):
            raise board._Reject(400, "opts 는 객체여야 합니다.")
        _fields(options, MASK_OPTS.keys())
        opts = {k: _integer(k, data.get(k[5:], 0) if query else options.get(k, 0), *bounds, query)
                for k, bounds in MASK_OPTS.items()}
    return keys, tool, model, opts


def _settings(tool, model, opts):
    if tool == "upscale":
        return {"checkpoint": model, **opts}
    return {"model": model, "model_node": "RMBG", "background": "Alpha", **opts}


def post_key(tool, body, sha):
    settings = {k: v for k, v in body.items() if k not in ("input_images", "organization_id", "request_id")}
    wire = json.dumps(["dp-post/1", tool, settings, sha], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(wire.encode()).hexdigest()[:24]


def _alpha(im):
    if "A" in im.getbands():
        return im.getchannel("A").getextrema()[0] < 255
    return im.mode == "P" and "transparency" in im.info and im.convert("RGBA").getchannel("A").getextrema()[0] < 255


def _sha256(stream):
    digest = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()


@functools.lru_cache(maxsize=256)
def _result_sha(path, mtime_ns, size):
    with path.open("rb") as stream:
        return _sha256(stream)


def _record(m, key):
    rec = store.cand_post_record(m, key)
    table = m.get("cand_post", {})
    if not isinstance(table, dict) or (key in table and rec is None):
        raise _bad_record()
    return rec


def _source(root, m, cand, *, fingerprint=True):
    effective = store.cand_effective(m, cand, root)
    src = {"file": effective["file"], "size": effective["size"]}
    try:
        path = _path(root, src["file"])
        with path.open("rb") as stream:
            if fingerprint:
                src["sha"] = _sha256(stream)
                stream.seek(0)
            with Image.open(stream) as im:
                src.update(size=list(im.size), alpha=_alpha(im))
    except FileNotFoundError:
        src["error"] = "입력 파일이 없습니다."
    except (OSError, ValueError, Image.DecompressionBombError):
        src["error"] = "입력 그림을 읽을 수 없습니다."
    return src


def _available(model, catalog):
    if catalog is None:
        return fg._CATALOG_UNREADABLE
    if model not in catalog:
        return "계정의 Forge 모델 목록에 이 모델이 없습니다."
    return None


def _blocked(src, tool, opts):
    if src.get("error"):
        return src["error"]
    if tool == "upscale" and src["alpha"]:
        return "배경을 지운 그림은 업스케일할 수 없습니다 — 업스케일을 먼저 한 뒤 배경을 지워 주세요"
    side = max(src["size"])
    if side * opts.get("scale", 1) > store.POST_MAX_SIDE:
        if tool == "bg_removal":
            return "그림의 변이 4096 을 넘어 후보정 결과를 붙일 수 없습니다"
        if side * 2 > store.POST_MAX_SIDE:
            return "이미 충분히 큰 그림입니다"
        return "지금 그림의 변 × 배율 이 4096 을 넘습니다"
    return None


class Ledger:
    def __init__(self, root, key):
        self.root, self.key = root, key

    def path(self, name):
        return _path(self.root, f"post/{self.key}/{name}")

    def read(self, name):
        try:
            value = fg._read_record(self.path(name + ".json"))
        except FileNotFoundError:
            return None
        except (ValueError, OSError):
            raise _bad_record() from None
        if not isinstance(value, dict):
            raise _bad_record()
        return value

    def write(self, name, doc):
        store.write_json_atomic(self.path(name + ".json"), doc)

    def done(self):
        done = self.read("done")
        if done is None:
            return None
        if (done.get("file") not in [f"post/{self.key}/out.{ext}" for ext, _ in FORMATS.values()]
                or not isinstance(done.get("size"), list) or len(done["size"]) != 2
                or not all(type(v) is int and 0 < v <= store.POST_MAX_SIDE for v in done["size"])
                or not isinstance(done.get("warnings"), list)
                or any(w not in (RATIO, SMALL, OPAQUE) for w in done["warnings"])):
            raise _bad_record()
        path = _path(self.root, done["file"])
        try:
            info = path.stat()
            if not stat.S_ISREG(info.st_mode):
                return None
            return done if _result_sha(path, info.st_mtime_ns, info.st_size) == done.get("sha") else None
        except (FileNotFoundError, NotADirectoryError, IsADirectoryError):
            return None

    def unfinished_output(self):
        # 이전 완료 기록이 있는 손상 파일은 반드시 원격 결과와 대조해 복구한다.
        if self.path("done.json").exists():
            return None
        for ext, _ in FORMATS.values():
            path = self.path("out." + ext)
            if path.is_file():
                return path
        return None

    def sidecar(self):
        side = self.read("forge")
        if side is None:
            return {"schema": fg.SIDECAR_SCHEMA, "requests": []}
        if side.get("schema") != fg.SIDECAR_SCHEMA or not isinstance(side.get("requests"), list) or not side["requests"]:
            raise _bad_record()
        last = side["requests"][-1]
        if (not isinstance(last, dict) or last.get("state") not in ("open", "submitted", "failed", "rejected")
                or type(last.get("attempt")) is not int or last["attempt"] < 1
                or not re.fullmatch(rf"dp-[0-9a-f]{{12}}-p{self.key}-a{last['attempt']}", str(last.get("id")))
                or (last["state"] == "submitted" and (not isinstance(last.get("job_id"), str)
                    or not fg._REQUEST_ID.fullmatch(last["job_id"]) or fg._timestamp(last.get("time")) is None))):
            raise _bad_record()
        return side

    def receipt(self):
        resp = self.read("resp")
        if resp is not None:
            if (resp.get("post_key") != self.key or resp.get("backend") != "forge"
                    or not isinstance(resp.get("job_id"), str) or not fg._REQUEST_ID.fullmatch(resp["job_id"])
                    or fg._timestamp(resp.get("received")) is None):
                raise _bad_record()
            return resp
        # 제출 성공을 두 파일 중 하나에만 기록하고 종료된 경우에도 다시 내지 않는다.
        rows = self.sidecar()["requests"]
        if rows and rows[-1]["state"] == "submitted":
            return dict(post_key=self.key, backend="forge", received=rows[-1]["time"], job_id=rows[-1]["job_id"])
        return None

    def state(self):
        req = self.read("req")
        attempt = 1 if req is None else req.get("attempt")
        if req is not None and (req.get("schema") != SCHEMA or req.get("post_key") != self.key
                                or req.get("backend") != "forge" or not isinstance(req.get("history"), list)
                                or fg._timestamp(req.get("time")) is None
                                or not re.fullmatch(r"hash:[0-9a-f]{12}", str(req.get("approved_by", "")))):
            raise _bad_record()
        if type(attempt) is not int or attempt < 1:
            raise _bad_record()
        failed = self.read("failed")
        if failed and failed.get("unusable") is True:
            return "unusable", attempt
        if self.done() is not None:
            return "reuse", attempt
        if self.receipt() is not None or self.unfinished_output() is not None:
            return "recover", attempt
        if failed is not None:
            return "new", attempt + 1
        if req is not None:
            # 거절 표지를 쓴 뒤 실패 파일을 쓰기 전에 중단되어도 지난 승인을 다시 쓰지 않는다.
            rows = self.sidecar()["requests"]
            if rows and rows[-1]["state"] == "rejected" and rows[-1]["attempt"] == attempt:
                return "new", attempt + 1
        return "new", attempt

    def unusable_reason(self):
        failed = self.read("failed") or {}
        reason = fg._safe_message(failed.get("error"), "결과 그림을 읽을 수 없습니다.")
        return f"받은 결과를 쓸 수 없습니다({reason}) — 같은 조건으로는 다시 요청하지 않습니다. 다른 옵션으로 해 보세요"


def _model(model):
    return {"id": model, "name": store.POST_MODEL_NAMES[model], "external": model == "magnific_v2"}


def _plan(root, args, catalog):
    keys, tool, model, opts = args
    m = store.load_manifest(root)
    crops = {c["id"]: c for c in m["crops"]}
    items, inputs, stamps, pending, scheduled = [], {}, {}, [], set()
    counts = dict.fromkeys(("new", "reuse", "recover", "blocked"), 0)
    for key in keys:
        cand = board._mask_cand(m, key)
        rec = _record(m, key)
        stamps[key] = rec["at"] if rec else ""
        src = _source(root, m, cand)
        inputs[key] = {k: src[k] for k in ("file", "size", "sha") if k in src}
        why = _blocked(src, tool, opts)
        pk = post_key(tool, _settings(tool, model, opts), src["sha"]) if "sha" in src else None
        state, attempt = "blocked", 0
        if not why:
            ledger = Ledger(root, pk)
            state, attempt = ledger.state()
            if state == "unusable":
                state, why = "blocked", ledger.unusable_reason()
            elif state == "new" and tool == "upscale":
                why = _available(model, catalog)
                if why:
                    state, attempt = "blocked", 0
        if not why and pk in scheduled:
            state = "reuse"  # 앞 후보의 결과만 등록한다. 앞 후보가 실패해도 추가 제출하지 않는다.
        if not why:
            scheduled.add(pk)
        if state == "new":
            pending.append(f"p:{pk}:a{attempt}")
        crop = crops[cand["crop_id"]]
        items.append({"key": key, "crop": f"{crop['name']} · {cand['tid']}", "state": state, "post_key": pk,
                      "attempt": attempt, "in": src["size"], "out": [s * opts.get("scale", 1) for s in src["size"]],
                      "why": why})
        counts[state] += 1
    mark = hashlib.sha256("".join(stamps[k] for k in sorted(keys)).encode()).hexdigest()[:12]
    approval = runner.pending_hash(["backend:forge", "root:" + store.root_key(root), *pending]) if pending else ""
    plan = {"ok": True, "expect": f"{m['rev']}:{mark}", "hash": approval,
            "tool": tool, "model": _model(model), "opts": opts, "items": items, "counts": counts}
    return plan, inputs, stamps


def _data_url(data):
    with Image.open(io.BytesIO(data)) as im:
        im.load()
        mime = FORMATS.get(im.format)
        if mime is None:
            buf = io.BytesIO()
            im.convert("RGBA" if _alpha(im) else "RGB").save(buf, format="PNG")
            data, mime = buf.getvalue(), FORMATS["PNG"]
    return "data:" + mime[1] + ";base64," + base64.b64encode(data).decode("ascii")


class _Drained(Exception):
    """첫 전송 전 drain. 접수 여부가 불명확한 요청의 멱등 재전송은 끝까지 한다."""


def _draining(ctx):
    flag = ctx.get("draining")
    return (flag is not None and flag()) or board.drain_requested(ctx["root"])


async def _submit(ctx, poller, tool, body, *, uncertain):
    lost, overload = uncertain, False
    for n in range(3):
        poller._stop()
        token = await poller._fresh_token(body["request_id"])
        if _draining(ctx) and (n == 0 or not lost):
            raise _Drained
        try:
            res = await ctx["client"].submit_post(token, tool, body)
            job_id = res.get("job_id") if isinstance(res, dict) else None
            if isinstance(job_id, str) and fg._REQUEST_ID.fullmatch(job_id):
                return job_id
        except ForgeError as exc:
            overload = fg._is_overload(exc)
            if exc.reason == "unauthorized" or exc.status == 401:
                ctx["reject_token"](token)
                if n < 2:
                    await poller._fresh_token(body["request_id"], token)
                    continue
            # 클라이언트가 너무 큰 제출 응답을 버린 413과 요청 타임아웃은 서버의 접수 거절 증거가 아니다.
            ambiguous = exc.status == 408 or (exc.status == 413 and exc.kind == "format")
            if exc.status is not None and 400 <= exc.status < 500 and not ambiguous:
                raise runner.CallError("Forge 가 후보정 요청을 받지 않았습니다.", status=exc.status,
                                       maybe_billed=lost) from None
        except (OSError, TimeoutError):
            overload = True
        except Exception:
            pass  # 제출 여부를 모른다. 원격 예외의 본문은 보관하지 않는다.
        lost = True
        if n < 2:
            await asyncio.sleep(fg.SUBMIT_RETRY_DELAY)
    raise fg._pressure_error("Forge 후보정 요청의 응답을 받지 못했습니다.", overloaded=overload,
                             phase="request", retryable=True, maybe_billed=True)


class _NoResult(runner.CallError):
    """접수한 잡에 결과가 없어 새 승인이 필요하다."""


class _Unusable(runner.CallError):
    """같은 잡의 결과를 다시 받아도 등록할 수 없다."""

    def __init__(self, message, *, phase="response", status=None):
        super().__init__(message, status=status, phase=phase, retryable=False, maybe_billed=True)


def _check_result(data, tool, src_size):
    if not isinstance(data, bytes) or len(data) > MEDIA_LIMIT:
        raise _Unusable("결과 그림의 형식 또는 크기가 잘못되었습니다.", phase="download")
    try:
        with Image.open(io.BytesIO(data)) as im:
            if im.format not in FORMATS:
                raise ValueError
            if max(im.size) > store.POST_MAX_SIDE:
                raise _Unusable("결과 그림의 변이 4096 을 넘습니다.")
            im.verify()
        with Image.open(io.BytesIO(data)) as im:
            im.load()
            w, h = im.size
            iw, ih = src_size
            warnings = []
            if abs(w * ih - iw * h) * 100 > iw * h:
                warnings.append(RATIO)
            if tool == "upscale" and (w <= iw or h <= ih):
                warnings.append(SMALL)
            if tool == "bg_removal" and not _alpha(im):
                warnings.append(OPAQUE)
            return FORMATS[im.format][0], [w, h], warnings
    except (OSError, ValueError, SyntaxError, EOFError, struct.error, Image.DecompressionBombError):
        raise _Unusable("결과 그림을 읽을 수 없습니다.") from None


async def _request(ledger, ctx, poller, item, plan, src, crop_id, data):
    side = ledger.sidecar()
    rows = side["requests"]
    attempt = item["attempt"]
    # 응답 유실 뒤의 재승인은 별도로 기록하되, Forge 에는 아직 열린 첫 멱등 키로만 재전송한다.
    uncertain = bool(rows and rows[-1]["state"] == "open")
    if not uncertain:
        _path(ledger.root, "cands/_forge.json")
        rid = f"dp-{fg._nonce(ledger.root)}-p{ledger.key}-a{attempt}"
        rows.append({"id": rid, "state": "open", "attempt": attempt, "time": runner._now()})
        side.pop("job_id", None)
    last = rows[-1]
    tool, model, opts = plan["tool"], plan["model"]["id"], plan["opts"]
    body = dict(_settings(tool, model, opts), organization_id=ctx["organization_id"],
                request_id=last["id"], input_images=[[_data_url(data)]])
    old = ledger.read("req") or {}
    history = list(old.get("history", []))
    if old and old["attempt"] != attempt:
        history.append({k: old[k] for k in ("attempt", "approved_by", "time")})
    ledger.write("req", dict(schema=SCHEMA, post_key=ledger.key, tool=tool, model=model, opts=opts, input=src,
                             key=item["key"], crop_id=crop_id, backend="forge", approved_by="hash:" + plan["hash"],
                             attempt=attempt, history=history, time=runner._now()))
    ledger.write("forge", side)
    ledger.path("failed.json").unlink(missing_ok=True)
    try:
        job_id = await _submit(ctx, poller, tool, body, uncertain=uncertain)
    except runner.CallError as exc:
        if exc.maybe_billed is False:
            last["state"] = "rejected"
            ledger.write("forge", side)
        raise
    finally:
        poller._clear_progress(last["id"])
    resp = {"post_key": ledger.key, "received": runner._now(), "backend": "forge", "job_id": job_id}
    last.update(state="submitted", job_id=job_id, time=resp["received"])
    side["job_id"] = job_id
    try:
        ledger.write("resp", resp)
    finally:
        ledger.write("forge", side)
    return resp


async def _download(ledger, ctx, poller, resp):
    job_id, submitted = resp["job_id"], fg._timestamp(resp["received"])
    # receipt 를 복구했으면 조회보다 먼저 영속화한다.
    if not ledger.path("resp.json").exists():
        ledger.write("resp", resp)
    try:
        result = await poller._poll(job_id, ledger.key, submitted, recovery=True)
    except runner.CallError as exc:
        if exc.status in (404, 410) and exc.retryable is False:
            raise _NoResult("Forge 후보정 작업이 만료되어 결과를 조회할 수 없습니다.", status=exc.status,
                            phase=exc.phase, retryable=False, maybe_billed=True) from None
        raise
    finally:
        poller._clear_progress(ledger.key)
    if result["status"] == "failed":
        raise _NoResult("Forge 후보정 작업이 실패로 끝났습니다.", phase="response", maybe_billed=True)
    images = result.get("images")
    if not isinstance(images, list) or not images or not isinstance(images[0], str) or not images[0]:
        raise _NoResult("Forge 결과 그림이 없습니다.", phase="download", maybe_billed=True)
    try:
        return await ctx["client"].fetch_media(images[0], limit=MEDIA_LIMIT)
    except ForgeError as exc:
        if exc.status == 413 and exc.kind == "format":
            raise _Unusable("결과 그림의 형식 또는 크기가 잘못되었습니다.", phase="download", status=413) from None
        raise fg._pressure_error("Forge 결과 그림을 받지 못했습니다.", overloaded=fg._is_overload(exc), status=exc.status,
                                 phase="download", retryable=exc.status != 413, maybe_billed=True) from None
    except Exception as exc:
        raise fg._pressure_error("Forge 결과 그림을 받지 못했습니다.", overloaded=isinstance(exc, OSError),
                                 phase="download", maybe_billed=True) from None


async def _receive(ledger, ctx, poller, resp, tool, size, *, local_only):
    local = ledger.unfinished_output()
    if local is not None:
        with local.open("rb") as stream:
            data = stream.read(MEDIA_LIMIT + 1)
    else:
        if local_only or resp is None:
            raise board._Reject(409, "복구할 결과가 없습니다 — 계획을 다시 확인해 주세요.")
        data = await _download(ledger, ctx, poller, resp)
    ext = runner._sniff_ext(data) if isinstance(data, bytes) and len(data) <= MEDIA_LIMIT else None
    if ext is None:
        raise _Unusable("결과 그림의 형식 또는 크기가 잘못되었습니다.", phase="download")
    name = "out." + ext
    # 소실된 완료 그림을 복구할 때는 이미 매니페스트가 이 이름을 가리킬 수 있다.
    # 옛 done 과 다른 바이트가 합성에 노출되지 않게, 원본 지문과 일치할 때만 되살린다.
    digest = hashlib.sha256(data).hexdigest()
    previous = ledger.read("done")
    if previous is not None and previous.get("sha") != digest:
        raise _Unusable("기존 완료 그림과 복구 결과가 다릅니다.")
    if local is None:
        store._atomic_write_bytes(ledger.path(name), data)
    elif name != local.name:
        raise _Unusable("결과 그림의 형식 또는 크기가 잘못되었습니다.", phase="download")
    _, result_size, warnings = _check_result(data, tool, size)
    started = resp["received"] if resp else (ledger.read("req") or {}).get("time")
    submitted = fg._timestamp(started)
    done = {"file": f"post/{ledger.key}/{name}", "size": result_size, "sha": digest,
            "elapsed_s": round(max(0, time.time() - submitted), 2) if submitted is not None else 0,
            "warnings": warnings, "finished": runner._now()}
    ledger.write("done", done)
    ledger.path("failed.json").unlink(missing_ok=True)
    return done


def _failed(ledger, exc):
    doc = {"error": board.public_error(str(exc)), "phase": exc.phase, "retryable": exc.retryable,
           "maybe_billed": exc.maybe_billed, "time": runner._now()}
    if exc.status is not None:
        doc["status"] = exc.status
    if isinstance(exc, _Unusable):
        doc["unusable"] = True
    if isinstance(exc, _NoResult):
        side = ledger.sidecar()
        if side["requests"]:
            side["requests"][-1]["state"] = "failed"
            ledger.write("forge", side)
    ledger.write("failed", doc)
    if isinstance(exc, _NoResult):
        # 시도 번호를 올릴 실패 기록이 영속화된 뒤에만 접수표를 지운다.
        ledger.path("resp.json").unlink()


def _at(previous):
    ns = time.time_ns()
    stamp = datetime.fromtimestamp(ns // 10**9).strftime("%Y-%m-%d %H:%M:%S") + f".{ns % 10**9:09d}"
    if previous and stamp <= previous:
        base, _, fraction = previous.partition(".")
        tick = int(fraction.ljust(9, "0")) + 1
        if tick == 10**9:
            base = (datetime.fromisoformat(base) + timedelta(seconds=1)).strftime("%Y-%m-%d %H:%M:%S")
        stamp = f"{base}.{tick % 10**9:09d}"
    return stamp


def _register(root, key, seen, plan, src, done, pk):
    # 매니페스트 잠금 밖에서 바이트를 읽고, 잠금 안에서는 경로와 파일 교체 여부를 확인한다.
    path = _path(root, src["file"])
    try:
        with path.open("rb") as stream:
            info = os.fstat(stream.fileno())
            if _sha256(stream) != src["sha"]:
                raise board._Reject(409, INPUT_CHANGED)
    except FileNotFoundError:
        raise board._Reject(409, INPUT_CHANGED) from None
    planned_input = {"file": src["file"], "sha": src["sha"]}

    def mutate(m):
        old = _record(m, key)
        cand = board.dmask.star_cand(m, key)
        if cand is None or (old["at"] if old else "") != seen:
            raise board._Reject(409, CHANGED, reason="changed")
        try:
            current = _path(root, src["file"]).stat()
        except FileNotFoundError:
            raise board._Reject(409, INPUT_CHANGED) from None
        if not os.path.samestat(info, current) or (info.st_mtime_ns, info.st_size) != (current.st_mtime_ns, current.st_size):
            raise board._Reject(409, INPUT_CHANGED)
        effective = store.cand_effective(m, cand, root)["file"]
        if effective != src["file"]:
            # 잃었던 현재 머리의 동일 파일을 되살리면 이미 등록돼 있다. 이력도 그대로 둔다.
            if (old and old["on"] and old["call"] == pk and old["file"] == effective == done["file"]
                    and old["input"] == planned_input and cand["file"] == src["file"]):
                return
            raise board._Reject(409, INPUT_CHANGED)
        hist = []
        if old:
            hist = [{k: v for k, v in old.items() if k != "hist"}, *old["hist"]][:store.RECORD_HIST_KEEP]
        rec = {"on": RATIO not in done["warnings"], "file": done["file"], "size": done["size"], "tool": plan["tool"],
               "model": plan["model"]["id"], "opts": plan["opts"], "input": planned_input,
               "call": pk, "at": _at(seen), "hist": hist}
        m.setdefault("cand_post", {})[key] = rec
        if store.cand_post_record(m, key) is None:
            raise _bad_record()
    store.update_manifest(root, mutate)


async def _execute(root, bundle, *, local_only=False):
    plan, inputs, stamps = bundle
    ctx = runner.forge_context()
    poller = fg.ForgeBackend(ctx)
    result = {"done": [], "reused": [], "recovered": [], "failed": [], "warnings": [], "stopped_reason": None}
    overloads = 0
    items = [i for i in plan["items"] if i["state"] != "blocked"]
    progress = jobs.compat.ProgressBar(len(items))
    with board.run_active(root):
        for item in items:
            if ctx["stop"]():
                result["stopped_reason"] = "drain"
                break
            key, pk = item["key"], item["post_key"]
            ledger = Ledger(root, pk)
            src = inputs[key]
            writing = False
            try:
                state, attempt = ledger.state()
                if state == "unusable":
                    raise board._Reject(409, ledger.unusable_reason())
                done = ledger.done()
                if done is None:
                    if item["state"] == "reuse":
                        raise board._Reject(409, "재사용할 결과가 없습니다 — 계획을 다시 확인해 주세요.")
                    if state == "new" and (item["state"] != "new" or attempt != item["attempt"] or not plan["hash"]):
                        raise board._Reject(409, "승인한 호출 목록이 바뀌었습니다 — 계획을 다시 확인해 주세요.")
                    if state == "new":
                        if _draining(ctx):
                            result["stopped_reason"] = "drain"
                            continue
                        m = store.load_manifest(root)
                        cand = board._mask_cand(m, key)
                        rec = _record(m, key)
                        if (rec["at"] if rec else "") != stamps[key]:
                            raise board._Reject(409, "후보 그림이 바뀌었습니다 — 계획을 다시 확인해 주세요.")
                        if store.cand_effective(m, cand, root)["file"] != src["file"]:
                            raise board._Reject(409, INPUT_CHANGED)
                        try:
                            data = _path(root, src["file"]).read_bytes()
                        except FileNotFoundError:
                            raise board._Reject(409, INPUT_CHANGED) from None
                        if hashlib.sha256(data).hexdigest() != src["sha"]:
                            raise board._Reject(409, INPUT_CHANGED)
                        writing = True
                        resp = await _request(ledger, ctx, poller, item, plan, src, cand["crop_id"], data)
                        del data
                    else:
                        writing = True
                        resp = ledger.receipt()
                    done = await _receive(ledger, ctx, poller, resp, plan["tool"], src["size"], local_only=local_only)
                writing = False
                _register(root, key, stamps[key], plan, src, done, pk)
                result[{"reuse": "reused", "recover": "recovered", "new": "done"}[state]].append(key)
                result["warnings"].extend({"key": key, "text": w} for w in done["warnings"])
            except _Drained:
                result["stopped_reason"] = "drain"
            except runner._interrupt_types():
                raise
            except Exception as error:
                exc = error if isinstance(error, runner.CallError) else runner.CallError(
                    str(error) if isinstance(error, board._Reject) else "후보정 파일을 처리하지 못했습니다.",
                    phase="record", maybe_billed=writing)
                if writing:
                    try:
                        _failed(ledger, exc)
                    except OSError:
                        exc = runner.CallError("후보정 실패 기록을 저장하지 못했습니다.", phase="record",
                                               maybe_billed=exc.maybe_billed)
                result["failed"].append({"key": key, "error": board.public_error(str(exc))})
                if fg._is_overload(exc):
                    overloads += 1
                    if overloads >= 2:
                        result["stopped_reason"] = "overload"
                        break
            finally:
                progress.update(1)
    return {"outputs": {"dp_post": result}}


def _version(rec):
    return hashlib.sha256((rec["at"] + "|" + rec["file"]).encode()).hexdigest()[:16]


def _public_post(root, key, rec):
    if rec is None:
        return None
    out = {k: rec[k] for k in ("on", "tool", "model", "opts", "size", "at")}
    query = urlencode({"root": store.root_key(root), "key": key, "v": _version(rec)})
    done = Ledger(root, rec["call"]).done()
    return dict(out, label=store.post_label(rec), hist=len(rec["hist"]), url="postprocess/img?" + query,
                warnings=done["warnings"] if done else [])


def _catalog(scope):
    return fg.cached_catalog((scope["org"], scope["account"]), max_age=None, failure_max_age=fg.CATALOG_PREVIEW_TTL)


def _view(root, scope, queue):
    catalog = _catalog(scope)
    models = [dict(_model(model), available=_available(model, catalog) is None, why=_available(model, catalog))
              for model in MODELS]
    tools = {"upscale": {"models": models, "default_model": MODELS[0], "scales": [2, 4], "max_side": store.POST_MAX_SIDE},
             "bg_removal": {"model": "RMBG-2.0", "opts": MASK_OPTS}}
    m = store.load_manifest(root)
    registration = board._compose_settings(root, m)["use_registration"]
    pref = board._PREF.get(store.root_key(root))
    candidates = []
    for cand in m["candidates"]:
        key = cand["key"]
        if not board.dmask.star_cand(m, key):
            continue
        rec = _record(m, key)
        src = _source(root, m, cand, fingerprint=False)
        quad = board._adjust_base_quad(cand, board._entry_for(m, key, pref), registration)
        adj = store.cand_adjust_effect(store.cand_adjust(m, key))
        if adj and "dx" in adj and "transform" not in (store.mask_stack_applied(m, key) or {}).get("off", []):
            quad = board.an.xform_quad(quad, **{k: adj[k] for k in ("dx", "dy", "rot", "sx", "sy")})
        edges = [math.dist(quad[:2], quad[2:4]), math.dist(quad[:2], quad[6:8])]
        density = round(min(s / length for s, length in zip(src["size"], edges)), 2) if min(edges) > 0 else None
        model = next((r["id"] for r in models if r["available"]), MODELS[0])
        reasons = {s: _blocked(src, "upscale", {"scale": s}) or _available(model, catalog) for s in (2, 4)}
        scales = [s for s, why in reasons.items() if not why]
        bg = _blocked(src, "bg_removal", {})
        can = {"upscale": {"ok": bool(scales), "why": reasons[2], "scales": scales},
               "bg_removal": {"ok": bg is None, "why": bg}}
        candidates.append({"key": key, "crop_id": cand["crop_id"], "tid": cand["tid"], "size": cand["src_size"],
                           "placed": [round(s) for s in edges], "density": density, "post": _public_post(root, key, rec),
                           "can": can})
    return {"ok": True, "tools": tools, "limits": {"per_request": LIMIT}, "cands": candidates,
            "running": queue.post_running(scope, root)}


def _set(root, data):
    key, seen, op = (data.get(k) for k in ("key", "seen", "op"))
    if not isinstance(seen, str) or (seen and not store._REC_AT_RE.fullmatch(seen)):
        raise board._Reject(400, "seen 은 후보정 시각 또는 빈 문자열이어야 합니다.")
    if op not in ("on", "off", "undo", "reset"):
        raise board._Reject(400, "op 값은 on, off, undo, reset 중 하나여야 합니다.")

    def mutate(m):
        board._mask_cand(m, key)
        old = _record(m, key)
        if seen != (old["at"] if old else ""):
            raise board._Reject(409, "후보정 기록이 다른 창에서 바뀌었습니다.", reason="changed")
        if old is None:
            if op == "on":
                raise board._Reject(409, "켤 후보정 그림이 없습니다.")
            return None
        if op == "on" and not _path(root, old["file"]).is_file():
            raise board._Reject(409, "후보정 그림 파일이 없습니다.")
        if op == "reset" or (op == "undo" and not old["hist"]):
            del m["cand_post"][key]
            return None
        if op == "undo":
            rec = dict(old["hist"][0], hist=old["hist"][1:])
        else:
            if old["on"] == (op == "on"):
                return old
            rec = dict(old, on=op == "on")
        rec["at"] = _at(seen)
        m["cand_post"][key] = rec
        return rec

    with store.manifest_lock(root):
        rec, rev = store.update_manifest(root, mutate)
        sig = board._composite_sig(root, store.load_manifest(root), board._PREF.get(store.root_key(root)))
    return {"ok": True, "post": _public_post(root, key, rec), "rev": rev, "composite_sig": sig}


def _image(root, key, version):
    m = store.load_manifest(root)
    board._mask_cand(m, key)
    rec = _record(m, key)
    if rec is None or _version(rec) != version:
        raise board._Reject(409, "후보정 그림이 바뀌었습니다.", reason="stale")
    data = _path(root, rec["file"]).read_bytes()
    try:
        with Image.open(io.BytesIO(data)) as im:
            mime = FORMATS[im.format][1]
    except (OSError, KeyError, Image.DecompressionBombError):
        raise _bad_record() from None
    return web.Response(body=data, content_type=mime, headers={"Cache-Control": "no-store"})


def _guard(handler):
    @board._guard
    @functools.wraps(handler)
    async def wrapped(request):
        try:
            return await handler(request)
        except FileNotFoundError:
            raise board._Reject(404, "후보정 파일이 없습니다.") from None
        except OSError:
            # OSError의 파일 이름에는 계정의 프로젝트 경로가 들어갈 수 있다.
            raise board._Reject(500, "후보정 파일을 읽거나 쓰지 못했습니다.") from None
    return wrapped


def _needs_forge(root, plan):
    return plan["counts"]["new"] > 0 or any(
        item["state"] == "recover" and Ledger(root, item["post_key"]).unfinished_output() is None
        for item in plan["items"])


def routes(queue, accounts):
    def root_of(data):
        root = board._root_param(data.get("root"))
        if runner.backend_name(root) != runner.BACKEND_FORGE:
            raise board._Reject(404, "이 프로젝트에서는 후보정을 사용할 수 없습니다.")
        return root

    @_guard
    async def view(request):
        _fields(request.query, {"root"})
        root = root_of(request.query)
        return board._json(await asyncio.to_thread(_view, root, board.current_scope(), queue))

    @_guard
    async def plan(request):
        root = root_of(request.query)
        args = _arguments(request.query, query=True)
        bundle = await asyncio.to_thread(_plan, root, args, _catalog(board.current_scope()))
        return board._json(bundle[0])

    @_guard
    async def run(request):
        data = await board._body(request)
        root = root_of(data)
        scope = board.current_scope()
        args = _arguments(data, query=False)
        if queue.post_busy(scope):
            raise board._Reject(409, "대기 중이거나 실행 중인 후보정 작업이 있습니다.", reason="busy")
        bundle = await asyncio.to_thread(_plan, root, args, _catalog(scope))
        needs_forge = await asyncio.to_thread(_needs_forge, root, bundle[0])
        if needs_forge:
            await jobs.ensure_forge_access(accounts, scope)
            bundle = await asyncio.to_thread(_plan, root, args, _catalog(scope))
        current = bundle[0]
        counts = current["counts"]
        if data.get("expect") != current["expect"] or data.get("approve", "") != current["hash"]:
            raise board._Reject(409, "후보정 계획을 다시 확인해 주세요.", reason="stale", plan=current)
        meta = {"tool": args[1], "total": len(args[0]) - counts["blocked"]}
        try:
            result = queue.submit(scope, root, "post", meta, "forge",
                                  run=lambda: asyncio.run(_execute(root, bundle, local_only=not needs_forge)))
        except jobs.PostBusy as exc:
            raise board._Reject(409, str(exc), reason="busy") from None
        except jobs.QueueFull as exc:
            raise board._Reject(409, str(exc), reason="queue_full") from None
        return board._json(dict(ok=True, **result, summary=counts))

    @_guard
    async def set_post(request):
        data = await board._body(request)
        root = root_of(data)
        _fields(data, {"root", "key", "seen", "op"})
        return board._json(await asyncio.to_thread(_set, root, data))

    @_guard
    async def image(request):
        root = root_of(request.query)
        _fields(request.query, {"root", "key", "v"})
        return await asyncio.to_thread(_image, root, request.query.get("key"), request.query.get("v"))

    base = board.API + "/postprocess"
    return (("GET", base, view), ("GET", base + "/plan", plan), ("POST", base + "/run", run),
            ("POST", base + "/set", set_post), ("GET", base + "/img", image))
