"""Studio external-engine bridge. Source folders are read-only; all writes stay in root."""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import re
import threading
import time
import unicodedata

import numpy as np
from PIL import Image

try:
    from . import bmk_design_patch_store as store
    from . import bmk_design_patch_pipeline as pl
    from . import bmk_design_patch_prompt as prompt
    from . import bmk_design_patch_psd as dpsd
except ImportError:
    import bmk_design_patch_store as store
    import bmk_design_patch_pipeline as pl
    import bmk_design_patch_prompt as prompt
    import bmk_design_patch_psd as dpsd


class BridgeError(ValueError):
    def __init__(self, status, message, **extra):
        super().__init__(message)
        self.status, self.extra = status, extra


_LOCKS, _LOCKS_GUARD = {}, threading.Lock()
_STAMP = re.compile(r"^export_src/\d{6}-\d{4}(-\d+)?$")
_NEW_COUNTS = {}  # (project, path, folder fp, decision signature) -> count; no image bytes
_CACHE_LOCK = threading.Lock()
logger = logging.getLogger(__name__)


def _root_lock(root):
    key = store.root_key(root)
    with _LOCKS_GUARD:
        if key not in _LOCKS:
            _LOCKS[key] = threading.RLock()
        return _LOCKS[key]


def _path(root, rel):
    path = store.resolve_path(root, rel)
    base, real = os.path.realpath(root), os.path.realpath(path)
    try:
        inside = os.path.normcase(os.path.commonpath([base, real])) == os.path.normcase(base)
    except ValueError:
        inside = False
    if not inside:
        raise BridgeError(400, "프로젝트 밖을 가리키는 경로(링크)는 쓸 수 없습니다")
    return path


def _board():
    try:
        from . import bmk_design_patch_board as board
    except ImportError:
        import bmk_design_patch_board as board
    return board


def _idle(root):
    if _board().is_running(root):
        raise BridgeError(409, "생성 실행 중에는 내보내기·등록을 할 수 없습니다 — 끝난 뒤 다시")


def _history(root):
    with _root_lock(root):
        return _read_history(root)


def _read_history(root, writable=False):
    """호출자는 프로젝트 잠금을 잡는다. 일시적 읽기 실패로 원본을 덮어쓰지 않는다."""
    path = _path(root, "ext_bridge.json")
    empty = {"exports": [], "imports": []}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or any(not isinstance(data.get(k), list) for k in ("exports", "imports")):
            raise ValueError()
    except FileNotFoundError:
        return empty
    except OSError as e:
        logger.warning("외부 엔진 기록을 읽지 못했습니다(원본 보존): %s", e)
        if writable:
            raise BridgeError(409, "외부 엔진 기록을 읽을 수 없습니다 — 잠금·권한을 확인한 뒤 다시") from e
        return empty
    except (ValueError, UnicodeError) as e:
        logger.warning("외부 엔진 기록을 읽지 못해 빈 기록으로 복구합니다: %s", e)
        with _root_lock(root):
            stamp = time.strftime("%Y%m%d-%H%M%S")
            backup, n = _path(root, f"ext_bridge.json.bad-{stamp}"), 1
            while os.path.exists(backup):
                n += 1
                backup = _path(root, f"ext_bridge.json.bad-{stamp}-{n}")
            try:
                os.rename(path, backup)
                store.write_json_atomic(path, empty)
                logger.warning("손상된 외부 엔진 기록 보존: %s", backup)
            except OSError as recovery:
                logger.warning("외부 엔진 기록을 보존·복구하지 못했습니다: %s", recovery)
                if writable:
                    raise BridgeError(409, "외부 엔진 기록을 복구할 수 없습니다 — 잠금·권한을 확인한 뒤 다시") from recovery
        return empty
    fields = {"exports": ("dir", "at", "n", "size"), "imports": ("path", "fp", "at", "keys")}
    for kind, keys in fields.items():
        for item in data[kind]:
            valid = isinstance(item, dict) and all(k in item for k in keys)
            if valid and kind == "exports":
                valid = (isinstance(item["dir"], str) and bool(_STAMP.fullmatch(item["dir"]))
                         and isinstance(item["at"], str) and type(item["n"]) is int and item["n"] >= 0
                         and item["size"] in ("gpt", "m64"))
            elif valid:
                valid = (all(isinstance(item[k], str) for k in ("path", "fp", "at"))
                         and os.path.isabs(item["path"]) and isinstance(item["keys"], list)
                         and all(isinstance(k, str) for k in item["keys"]))
            if valid:
                empty[kind].append({k: item[k] for k in keys})
            else:
                logger.warning("외부 엔진 기록의 잘못된 %s 항목을 건너뜁니다", kind)
    return empty


def _save_history(root, data):
    with _root_lock(root):
        _read_history(root, writable=True)
        store.write_json_atomic(_path(root, "ext_bridge.json"), data)


def _fold(s):
    return unicodedata.normalize("NFC", s).casefold()


def _clean_name(name):
    return re.sub(r'[\\/:*?"<>|\x00-\x1f\x7f]', "_", name)


def save_prefixes(root):
    prefix = f"design_patch/{os.path.basename(os.path.normpath(root))}/ext_"
    output = _board().output_dir()
    return {"save_prefix": prefix,
            "save_prefix_abs": os.path.join(output, *prefix.split("/")) if output else None}


def export_folder(root, sub):
    if not isinstance(sub, str) or not _STAMP.fullmatch(sub):
        raise BridgeError(400, "내보낸 소스 폴더 경로가 잘못되었습니다")
    path = _path(root, sub)
    if not os.path.isdir(path):
        raise BridgeError(404, "내보낸 소스 폴더가 없습니다")
    return path


def export_sources(root, crops=None, size="gpt"):
    if size not in ("gpt", "m64"):
        raise BridgeError(400, "크기는 gpt 또는 m64 여야 합니다")
    if crops is not None and (not isinstance(crops, list) or any(not isinstance(c, str) for c in crops)):
        raise BridgeError(400, "crops 는 크롭 ID 목록 또는 null 이어야 합니다")
    with _root_lock(root):
        _idle(root)
        history = _read_history(root, writable=True)
        m = store.load_manifest(root)
        settings = pl.current(root, m)["settings"]
        if not settings:
            raise BridgeError(400, "실행 설정을 읽을 수 없습니다 — 크기 규칙을 확인하세요")
        rule = settings["prepare"]["size_rule"]
        by_id = {c["id"]: c for c in m["crops"]}
        names, files = {}, {}
        for c in m["crops"]:
            names.setdefault(_fold(c["name"]), []).append(c["id"])
            files[c["id"]] = _clean_name(c["name"]) + "__src.png"
        collisions = {}
        for cid, fn in files.items():
            collisions.setdefault(_fold(fn), []).append(cid)
        items, skipped, encoded = [], [], []
        for cid in dict.fromkeys(by_id if crops is None else crops):
            c = by_id.get(cid)
            why = None
            if c is None:
                why = "크롭 없음"
            elif len(names[_fold(c["name"])]) > 1 or len(collisions[_fold(files[cid])]) > 1:
                why = "같은 이름 크롭이 여럿 — 이름을 바꾼 뒤 내보내기"
            if why:
                skipped.append({"crop_id": cid, "name": c["name"] if c else None, "why": why})
                continue
            try:
                x0, y0, x1, y1 = c["rect"]
                w, h = x1 - x0, y1 - y0
                W, H, _ = store.gpt_output_size(w, h, rule)
                if size == "m64":
                    W, H = (max(64, int(v / 64 + .5) * 64) for v in (W, H))
                with Image.open(_path(root, c["source"])) as im:
                    rgba = np.asarray(im.convert("RGBA"))
                rgb, _ = prompt.flatten_canvas(rgba)
                if rgba.shape[:2] != (h, w):
                    raise ValueError("크롭 소스 크기가 rect 와 다름 — 반영 뒤 다시")
                buf = io.BytesIO()
                Image.fromarray(rgb).resize((W, H), Image.Resampling.LANCZOS).convert("RGB").save(buf, format="PNG")
                data = buf.getvalue()
            except (OSError, ValueError) as e:
                skipped.append({"crop_id": cid, "name": c["name"], "why": f"크롭 소스를 읽을 수 없음: {e}"})
                continue
            items.append({"crop_id": cid, "name": c["name"], "file": files[cid], "rect": list(c["rect"]),
                          "src_wh": [w, h], "out_wh": [W, H], "ratio_err": abs((W / H) / (w / h) - 1),
                          "sha256": hashlib.sha256(data).hexdigest(), "crop_source": c["source"]})
            encoded.append(data)
        if not items:
            raise BridgeError(400, "내보낼 크롭이 없습니다", skipped=skipped)
        _idle(root)
        stamp = time.strftime("%y%m%d-%H%M")
        sub, n = f"export_src/{stamp}", 1
        while True:
            folder = _path(root, sub)
            try:
                os.makedirs(folder, exist_ok=False)
                break
            except FileExistsError:
                n += 1
                sub = f"export_src/{stamp}-{n}"
        saved = []
        for item, data in zip(items, encoded):
            created, filename = False, None
            try:
                filename = _path(root, f"{sub}/{item['file']}")
                with open(filename, "xb") as f:
                    created = True
                    f.write(data)
                saved.append(item)
            except OSError as e:
                skipped.append({"crop_id": item["crop_id"], "name": item["name"], "why": f"크롭 파일을 저장할 수 없음: {e}"})
                if created:
                    try:
                        os.remove(filename)  # 이번 쓰기의 불완전한 파일만 정리한다.
                    except OSError as cleanup:
                        skipped[-1]["why"] += f" — 불완전한 파일 정리 실패: {cleanup}"
        if not saved:
            try:
                os.rmdir(folder)  # 이번 호출이 만든 빈 stamp 폴더만 제거한다.
            except OSError as cleanup:
                logger.warning("실패한 내보내기 폴더를 정리하지 못했습니다: %s", cleanup)
            raise BridgeError(400, "내보낼 크롭이 없습니다", skipped=skipped)
        items = saved
        at = time.strftime("%Y-%m-%d %H:%M:%S")
        store.write_json_atomic(_path(root, f"{sub}/export.json"),
                                {"v": 1, "at": at, "size": size, "size_rule": rule, "items": items})
        history["exports"] = [{"dir": sub, "at": at, "n": len(items), "size": size}] + history["exports"][:9]
        try:
            _save_history(root, history)
        except (BridgeError, OSError) as e:
            logger.warning("내보내기 완료 뒤 외부 엔진 기록 저장 실패: %s", e)
        return {"ok": True, "dir": sub, "abs": folder,
                "items": [{k: i[k] for k in ("crop_id", "name", "file", "src_wh", "out_wh", "ratio_err")} for i in items],
                "skipped": skipped, **save_prefixes(root)}


def _sidecars(root):
    base = _path(root, "export_src")
    if not os.path.isdir(base):
        return []
    stamps = [s for s in os.listdir(base) if _STAMP.fullmatch(f"export_src/{s}")]
    stamps.sort(key=lambda s: (s[:11], int(s[12:]) if len(s) > 11 else 1), reverse=True)
    out = []
    for stamp in stamps:
        try:
            with open(_path(root, f"export_src/{stamp}/export.json"), encoding="utf-8") as f:
                doc = json.load(f)
            for i in doc["items"]:
                r = i["rect"]
                if len(r) != 4 or not all(isinstance(v, int) for v in r) or r[2] <= r[0] or r[3] <= r[1]:
                    continue
                if all(isinstance(i.get(k), str) for k in ("crop_id", "file", "sha256", "crop_source")):
                    out.append((stamp, i))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return out


def _import_path(root, path):
    path = str(path or "").strip().strip('"').strip("'").strip()
    if not os.path.isabs(path):
        raise BridgeError(400, "결과 폴더는 절대 경로여야 합니다")
    path = os.path.realpath(path)
    base = os.path.realpath(_path(root, "export_src"))
    try:
        inside = os.path.normcase(os.path.commonpath([base, path])) == os.path.normcase(base)
    except ValueError:
        inside = False
    if inside:
        raise BridgeError(400, "export_src 안의 소스 폴더는 가져올 수 없습니다 — 결과 폴더를 고르세요")
    if not os.path.isdir(path):
        raise BridgeError(404, "결과 폴더가 없습니다")
    return path


def _scan(root, path):
    path = _import_path(root, path)
    fp = store._dir_fp(path)
    m = store.load_manifest(root)
    node = _board()._node()
    sidecars = _sidecars(root)
    source_hashes = {i["sha256"] for _, i in sidecars}
    latest = {}
    for stamp, item in sidecars:
        latest.setdefault(item["crop_id"], (stamp, item))
    old = {c["key"] for c in m["candidates"]}
    crops = {c["id"]: c for c in m["crops"]}
    seen, rows, prepared = set(), [], []
    for fn in sorted(os.listdir(path)):
        full = os.path.join(path, fn)
        if not os.path.isfile(full) or os.path.splitext(fn)[1].lower() not in store.REF_EXTS:
            continue
        row = {"file": fn, "crop_id": None, "name": None, "tid": None, "match": None,
               "status": "skip", "why": None, "wh": None, "ratio_err": None, "warn": []}
        rows.append(row)
        stem = unicodedata.normalize("NFC", os.path.splitext(fn)[0])
        if stem.lower().endswith("__src"):
            row["why"] = "내보낸 크롭 소스(__src)"
            continue
        try:
            with open(full, "rb") as f:
                data = f.read()
            sha = hashlib.sha256(data).hexdigest()
            if sha in source_hashes:
                row["why"] = "내보낸 크롭 소스와 같은 파일"
                continue
            key = "f_" + sha[:12]
            crop, match = node._crop_for_filename(stem, m["crops"])
            prefix = crop["name"] if crop else ""
            best_len = len(_fold(prefix)) if match == "name" else 0
            # 최신부터 순회하되 더 긴 접두만 교체: 동률은 현재 이름, 사이드카끼리는 최신.
            for _, item in sidecars:
                clean_name = os.path.splitext(item["file"])[0].removesuffix("__src")
                folded = _fold(clean_name)
                if len(folded) > best_len and _fold(stem).startswith(folded) and item["crop_id"] in crops:
                    crop, match, prefix = crops[item["crop_id"]], "export", clean_name
                    best_len = len(folded)
            if crop is None:
                row["why"] = match
                continue
            if match == "export" and _clean_name(crop["name"]) != prefix:
                row["warn"].append("내보낸 뒤 크롭 이름이 바뀜")
            row.update(crop_id=crop["id"], name=crop["name"], match=match)
            tids = [t["tid"] for t in crop["targets"]]
            if not tids:
                row["why"] = "크롭에 타깃이 없음"
                continue
            # NNN 매칭에서는 이름 대신 번호 접두 뒤의 토큰을 본다.
            tail = stem[len(unicodedata.normalize("NFC", prefix)):] if match != "nnn" else stem
            token = re.search(r"__([A-Z]{1,3})(?=__|_|$)", tail)
            tid = token.group(1) if token else tids[0]
            if tid not in tids:
                row["warn"].append(f"타깃 {tid} 없음 — 첫 타깃 {tids[0]} 사용")
                tid = tids[0]
            row["tid"] = tid
            stamp, item = latest.get(crop["id"], (None, None))
            try:
                pix = node._decode(data)
                h, w = pix.shape[:2]
            except Exception as e:
                row["why"] = f"이미지를 읽을 수 없음: {e}"
                continue
            row["wh"] = [w, h]
            if item and [w, h] == item.get("out_wh"):
                try:
                    with Image.open(_path(root, f"export_src/{stamp}/{item['file']}")) as image:
                        source_rgb = np.asarray(image.convert("RGB"))
                    if np.array_equal(pix[..., :3], source_rgb):
                        row["why"] = "내보낸 소스와 같음 — 엔진을 거치지 않음"
                        continue
                except Exception:
                    row["warn"].append("내보낸 소스를 읽지 못해 통과 여부 확인 못 함")
            x0, y0, x1, y1 = crop["rect"]
            if (w, h) == (x1 - x0, y1 - y0):
                try:
                    src = node._load_rgba(root, crop["source"])
                    if np.array_equal(pix[..., :3], src[..., :3]):
                        row["why"] = "입력 크롭과 픽셀이 같음(bypass 통과 의심)"
                        continue
                except Exception:
                    row["warn"].append("크롭 소스를 읽지 못해 통과 여부 확인 못 함")
            if key in seen:
                row["why"] = "같은 내용의 파일이 이미 있음"
                continue
            seen.add(key)
            rect = item["rect"] if item else crop["rect"]
            if list(rect) != list(crop["rect"]):
                row["warn"].append("내보낸 뒤 크롭 위치·크기가 바뀜 — 내보낸 때 자리에 놓음")
            err = abs((w / h) / ((rect[2] - rect[0]) / (rect[3] - rect[1])) - 1)
            row["ratio_err"] = err
            if err > .01:
                row["warn"].append(f"비율 다름 — 늘어남 {err * 100:.1f}%")
            row["status"] = "exists" if key in old else "new"
            if row["status"] == "new":
                cand = {"key": key, "crop_id": crop["id"], "tid": tid, "src_size": [w, h], "origin": "folder",
                        "origin_info": {"folder": path, "filename": fn, "match": match,
                                        "crop_source": item["crop_source"] if item else crop["source"],
                                        "via": "studio", "export": stamp, "tid_token": bool(token)},
                        "quad": dpsd.quad_from_rect(rect), "picked": False, "z": None, "hand_mask": None}
                prepared.append((cand, sha, os.path.splitext(fn)[1], row))
        except (OSError, ValueError) as e:
            row.update(status="skip", why=f"이미지를 읽을 수 없음: {e}")
    if fp != store._dir_fp(path):
        raise BridgeError(409, "폴더가 바뀌었습니다 — 미리보기를 다시 하세요")
    return {"ok": True, "path": path, "fp": str(fp), "rows": rows,
            "counts": {k: sum(r["status"] == k for r in rows) for k in ("new", "exists", "skip")}}, prepared


def scan_import(root, path):
    return _scan(root, path)[0]


def apply_import(root, path, expect_fp=None):
    if expect_fp is not None and not isinstance(expect_fp, str):
        raise BridgeError(400, "expect_fp 는 미리보기 지문 문자열이어야 합니다")
    with _root_lock(root):
        _idle(root)
        history = _read_history(root, writable=True)
        result, prepared = _scan(root, path)
        if expect_fp is not None and result["fp"] != expect_fp:
            raise BridgeError(409, "폴더가 바뀌었습니다 — 미리보기를 다시 하세요")
        for c, sha, ext, _ in prepared:
            try:
                with open(os.path.join(result["path"], c["origin_info"]["filename"]), "rb") as f:
                    data = f.read()
            except OSError:
                raise BridgeError(409, "폴더가 바뀌었습니다 — 미리보기를 다시 하세요") from None
            if hashlib.sha256(data).hexdigest() != sha:
                raise BridgeError(409, "폴더가 바뀌었습니다 — 미리보기를 다시 하세요")
            _path(root, f"cands/{c['key']}{ext}")
            c["file"] = store.save_bytes_ca(root, "cands", data, ext, prefix="f_")
        node = _board()._node()
        added = []

        def add(m):
            _idle(root)
            if store._dir_fp(result["path"]) != result["fp"]:
                raise BridgeError(409, "폴더가 바뀌었습니다 — 미리보기를 다시 하세요")
            keys = {c["key"] for c in m["candidates"]}
            targets = {c["id"]: {t["tid"] for t in c["targets"]} for c in m["crops"]}
            for c, _, _, row in prepared:
                if c["key"] in keys:
                    row["status"] = "exists"
                    continue
                if c["tid"] not in targets.get(c["crop_id"], set()):
                    row.update(status="skip", why="크롭·타깃이 사라짐 — 미리보기를 다시 하세요")
                    continue
                m["candidates"].append(c)
                keys.add(c["key"])
                added.append(c["key"])

        if prepared:
            _, rev = store.update_manifest(root, add)
        else:
            rev = store.load_manifest(root)["rev"]
        # 분석은 manifest 잠금 밖에서 계산하고, 후보·크롭이 그대로일 때만 결과를 붙인다.
        m = store.load_manifest(root)
        fresh = [c for c in m["candidates"] if c["key"] in added]
        warns, analyzed = [], 0
        entries = {}
        if fresh:
            try:
                _path(root, "derived")
                entries, _, _, aw = node._ensure_analysis(root, m, fresh, prm=store.load_board_pref(root))
                warns.extend(aw)
            except Exception:
                warns.append("분석 실패 — [반영 (무료)] 뒤 다시")

            def analysis(current):
                nonlocal analyzed
                if current["crops"] != m["crops"]:
                    warns.append("분석 중 크롭이 바뀜 — [반영 (무료)] 뒤 다시")
                    return
                by_key = {c["key"]: c for c in current["candidates"]}
                for c in fresh:
                    if by_key.get(c["key"]) == c and c["key"] in entries:
                        current["analysis"].setdefault(c["key"], {}).update(m["analysis"][c["key"]])
                        analyzed += 1

            _, rev = store.update_manifest(root, analysis)
        path = result["path"]
        prev = next((i for i in history["imports"] if i["path"] == path), {})
        folder_keys = [c["key"] for c in m["candidates"] if (c.get("origin_info") or {}).get("via") == "studio"
                       and c["origin_info"].get("folder") == path]
        record = {"path": path, "fp": result["fp"], "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                  "keys": list(dict.fromkeys(prev.get("keys", []) + folder_keys))}
        history["imports"] = [record] + [i for i in history["imports"] if i["path"] != path][:19]
        try:
            _save_history(root, history)
        except (BridgeError, OSError) as e:
            warns.append("기록 저장 실패 — 다음 등록 때 다시 기록")
            logger.warning("등록 완료 뒤 외부 엔진 기록 저장 실패: %s", e)
        result["counts"] = {k: sum(r["status"] == k for r in result["rows"]) for k in ("new", "exists", "skip")}
        return dict(result, added=added, analyzed=analyzed, warns=warns, rev=rev)


def candidate_missing(c):
    return c["key"] in missing_candidates([c])


def missing_candidates(candidates):
    """폴더마다 한 번 목록을 읽고 state 의 지문·후보 칩에 같은 결과를 쓴다."""
    folders, missing = {}, set()
    for c in candidates:
        info = c.get("origin_info") or {}
        if info.get("via") == "studio":
            folder = os.path.normcase(os.path.normpath(info.get("folder") or ""))
            folders.setdefault(folder, []).append(c)
    for folder, members in folders.items():
        try:
            with os.scandir(folder) as entries:
                names = {os.path.normcase(e.name) for e in entries if e.is_file()}
        except OSError:
            names = set()
        missing.update(c["key"] for c in members if os.path.normcase(c["origin_info"].get("filename", "")) not in names)
    return missing


def candidate_label(c):
    info = c.get("origin_info") or {}
    if info.get("via") == "studio":
        folder = info.get("folder", "").replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
        return f"외부 · {folder}/{info.get('filename', '')}"
    return info.get("layer") or info.get("filename") or c["key"]


def _scan_signature(root, m):
    inputs = {"keys": sorted({c["key"] for c in m["candidates"]}),
              "crops": [{k: c.get(k) for k in ("id", "name", "nnn", "rect", "source", "targets")} for c in m["crops"]],
              "sidecars": _sidecars(root)}
    return hashlib.sha256(json.dumps(inputs, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _export_items(root, sub):
    """최근 내보내기의 실제 파일 이름으로 새로고침 뒤에도 접두를 복원한다."""
    try:
        with open(_path(root, f"{sub}/export.json"), encoding="utf-8") as f:
            doc = json.load(f)
        if not isinstance(doc, dict) or not isinstance(doc.get("items"), list):
            raise ValueError("내보내기 항목 형식 오류")
        return [{"crop_id": i["crop_id"], "name": i["name"], "file": i["file"],
                 "clean": os.path.splitext(i["file"])[0].removesuffix("__src")}
                for i in doc["items"] if isinstance(i, dict)
                and all(isinstance(i.get(k), str) for k in ("crop_id", "name", "file"))]
    except (OSError, ValueError) as e:
        logger.warning("최근 내보내기 항목을 읽지 못했습니다: %s", e)
        return []


def ext_summary(root):
    history = _history(root)
    m = store.load_manifest(root)
    missing_keys = missing_candidates(m["candidates"])
    exports, imports, signature = [], [], None
    for item in history["exports"]:
        row = dict(item)
        if not exports:
            row.update(abs=None, items=[])
        try:
            folder = export_folder(root, row["dir"])
            if not exports:
                row.update(abs=folder, items=_export_items(root, row["dir"]))
        except (ValueError, OSError):
            row["gone"] = True
        exports.append(row)
    for item in history["imports"]:
        path = item["path"]
        missing = [c["key"] for c in m["candidates"] if (c.get("origin_info") or {}).get("folder") == path and c["key"] in missing_keys]
        row = {"path": path, "at": item["at"], "exists": False, "new_n": None, "missing": missing}
        try:
            row["exists"] = os.path.isdir(path)
            fp = store._dir_fp(path)
            if fp is not None:
                if fp == item["fp"]:
                    row["new_n"] = 0
                    imports.append(row)
                    continue
                if signature is None:
                    signature = _scan_signature(root, m)
                cache_key = (store.root_key(root), path, fp, signature)
                with _CACHE_LOCK:
                    cached = _NEW_COUNTS.get(cache_key)
                if cached is not None:
                    row["new_n"] = cached
                else:
                    scanned = scan_import(root, path)
                    if scanned["fp"] != fp or _scan_signature(root, store.load_manifest(root)) != signature:
                        raise BridgeError(409, "폴더·판정 입력이 바뀌었습니다 — 다음에 다시 확인하세요")
                    row["new_n"] = scanned["counts"]["new"]
                    with _CACHE_LOCK:
                        _NEW_COUNTS[cache_key] = row["new_n"]
                        while len(_NEW_COUNTS) > 128:
                            _NEW_COUNTS.pop(next(iter(_NEW_COUNTS)))
        except Exception as e:
            row.update(new_n=None, error=f"결과 폴더를 확인하지 못했습니다: {e}")
        imports.append(row)
    return {"ok": True, "exports": exports, "imports": imports, **save_prefixes(root)}
