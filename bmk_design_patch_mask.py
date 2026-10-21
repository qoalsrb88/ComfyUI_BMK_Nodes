"""BMK Design Patch Mask — Studio S4 마무리 편집: ★ 후보의 최종 마스크(= Export 타깃 그룹 마스크) 편집 기록·적용 규칙(ComfyUI 비의존).

배경
----
지금 이음매 손질은 PS 에서 그룹 마스크를 칠하는 일이다. S4 는 Studio 가 ★ 후보의 마스크를 크롭 해상도로 칠해 [적용] 때 래스터 PNG 를
올리고(붓·경계 페이드·전체 페더는 모두 브라우저 — 서버는 검사·저장만, D2), 노드 _plan 이 그 래스터로 _final_mask 의 결과를 바꾼다
(msrc "edit"). 편집을 손 마스크(hand_mask)로 쓰지 않는다 — 손 마스크는 분석 해시·정합 제외·톤 제외에 들어가 편집 한 번에 재분석이 돌고
패치가 움직인다(명세 §1.2). 그래서 매니페스트 최상위 mask_edits {후보 key: 기록} 에 두고(★·탈락처럼 보드가 update_manifest 로 씀,
store 3-way 병합은 키 단위), 분석·z 순서(기준 마스크 넓이, D5)는 편집을 모른다. 편집은 mask_source 와 상관없이 이긴다(D4) — 기준이
바뀌면 stale 로 알릴 뿐. ★ 가 아닌 후보(탈락·★ 해제·크롭에서 빠진 타깃)의 기록은 지우지 않고 쓰이지 않을 뿐이다(다시 ★ 하면 살아남).
torch·comfy·vlm·노드 모듈을 import 하지 않는다(store·numpy·PIL 만) — 노드와 보드(라우트·state)가 같은 함수를 쓴다.
명세: H:\\BmkNodeDesign\\MultiLayerCropEdit\\_proto\\STUDIO_S4_SPEC.md §3(데이터 모델)·§4(라우트), 응답 모양 정본 tools/fixtures/mask_api.json.

기록 mask_edits[key] (키 = 아래 여섯, 모르는 키·빠진 키는 ValueError)
  file  "masks/<key 앞 10자>_e_<pixel_sha 12>.png" — L 8비트(255 = 패치 보임), 크기 = rect, 창 밖 = 0(bg 0). 내용 주소(store.save_png_ca)
  rect  편집 창 [x0, y0, x1, y1] = 그 ★ 항목의 실효 quad bbox ∩ 캔버스(edit_rect)
  base  편집을 시작한 기준 지문 {src: auto|hand|none, file: 그 마스크 파일 | null(none), ph: 분석 params_hash | null, ms: mask_source}
  ops   사람이 읽는 요약(≤ 200자 한 줄)   at  "YYYY-MM-DD HH:MM:SS"
  hist  이전 판(가장 최근이 앞, ≤ 20) [{file, rect, base, at, ops}] — [이전 판으로]가 그 판의 창·기준까지 되돌린다

공개 API (예외는 ValueError + 한국어 메시지. MaskConflict(→ 409)만 다름)
- class MaskConflict(ValueError)   .extra = {"reason": "not_picked" | "changed" | "rect_changed"(+ rect)} — 보드가 409 {error, **extra}
- star_cand(m, key) -> cand | None   지금 ★: picked·탈락 아님·크롭과 타깃이 매니페스트에 있음(보드 _composite_picks·노드 _in_crop 과 같은 규칙)
- edit_record(m, key) -> 기록 | None
- base_source(cand, entry, mask_source) -> (src, file)   노드 _final_mask 의 고르기(노드가 이것을 부른다 — 지문과 실제 기준이 늘 같게)
- base_fp(cand, entry, mask_source) -> {src, file, ph, ms} · is_stale(rec, fp) -> 넷 중 하나라도 다름
- edit_rect(bbox, canvas) -> [x0, y0, x1, y1] | None   bbox = dpsd.quad_bbox(_plan 항목의 실효 quad)
- edit_mask(root, rec) -> (마스크 dict {"arr", "left", "top", "bg": 0} | None, 경고 | None)   기록 형식 오류·파일 없음·깨진 PNG·크기 다름 =
  None + 경고, 잠김·권한(errno 가 있는 OSError)은 올림
- check_record(rec) -> rec · validate_png(data, rect=None) -> uint8 HxW(PNG·모드 L·투명 없음·≤ 4096²·rect 가 있으면 그 크기)
- save_edit(root, key, data, *, seen, rect, base, ops, placed=None) -> (rec, rev)   이전 판은 hist 앞에(20 넘으면 버림). placed(m) = 잠근
  매니페스트로 다시 계산한 지금 편집 창 — rect 와 다르면 MaskConflict("rect_changed", rect=지금 창)(창을 고른 뒤 Analyze 가 정합을 바꾼 경합)
- undo_edit(root, key, *, seen) -> (rec | None, rev)   hist 첫 판으로, hist 가 비면 기록 지움(= 기준). 기록이 없으면 그대로
- reset_edit(root, key, *, seen) -> (None, rev)        기록 지움(hist 도)
  셋 다 store.update_manifest 의 mutate 안에서 seen(지금 기록의 file, 없으면 "")을 대조 — 다르면 MaskConflict("changed"). 형식이 깨진 기록
  (check_record 실패 — 손으로 고친 매니페스트)은 없음("")으로 본다: save 는 hist 없이 덮고 undo·reset 은 지운다(_plan·보드처럼 편집 없음).
  save 는 지금 ★ 가 아니면 MaskConflict("not_picked"). save 의 PNG(내용 주소)는 잠금 밖에서 먼저 쓴다(mutate 안 인코딩 금지 —
  update_manifest) — 그래서 409 로 거절된 저장도 masks/ 에 파일이 남는다(같은 내용은 한 파일, 정리는 명세 §12 범위 밖). rect·base 는 보드가 지금 계획(_plan)에서 계산해 넘긴다 — 올린 PNG 크기나
  Studio 가 세션을 연 창(쿼리 rect)이 지금 창과 다른지(창이 바뀜 — 409 rect_changed {rect})는 보드가 validate_png(data) 로 크기를 먼저 보고 가린다
  (mask_api.json 'save 판정 순서'). stale 판정도 보드가 is_stale 에 '기록 rect ≠ 지금 창'을 더한다(board._edit_stale). 노드 _plan 은
  그 Compose·Export 설정의 창·분석 항목 ph 가 기록과 다르면 경고만 한다(편집은 그대로 씀).
"""
from __future__ import annotations

import io
import os
import re
import time
from typing import Callable

import numpy as np
from PIL import Image

try:
    from . import bmk_design_patch_store as store
except ImportError:  # 단독 실행/테스트
    import bmk_design_patch_store as store

MASKS_DIR = "masks"
HIST_KEEP = 20
OPS_MAX = 200
MAX_PIXELS = 4096 * 4096
BASE_SRCS = ("auto", "hand", "none")
MASK_SOURCES = ("auto", "hand", "hand_else_auto")  # 노드 _MASK_SOURCES(노드를 import 하지 않아 복사 — mask_node_unit 이 대조)
_REC_KEYS = ("file", "rect", "base", "ops", "at", "hist")
_HIST_KEYS = ("file", "rect", "base", "at", "ops")
_BASE_KEYS = ("src", "file", "ph", "ms")
_FILE_RE = re.compile(r"masks/[A-Za-z0-9_\-]{1,64}\.png")
_AT_RE = re.compile(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d")
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")
_PNG_SIG = b"\x89PNG\r\n\x1a\n"


class MaskConflict(ValueError):
    """seen 이 지금 기록과 다름(다른 창에서 바뀜)·지금 ★ 가 아님 — 보드가 409 {error, reason} 로 돌려준다."""

    def __init__(self, msg: str, reason: str, **extra):
        super().__init__(msg)
        self.extra = {"reason": reason, **extra}


# ══════════════════════════════════════════════════════════════════════
# 기준·적용 (노드 _plan · 보드 state·라우트)
# ══════════════════════════════════════════════════════════════════════
def star_cand(m: dict, key: str) -> dict | None:
    c = next((c for c in m.get("candidates") or [] if c.get("key") == key), None)
    if c is None or not c.get("picked") or key in (m.get("rejects") or {}):
        return None
    crop = next((x for x in m.get("crops") or [] if x.get("id") == c.get("crop_id")), None)
    return c if crop is not None and any(t.get("tid") == c.get("tid") for t in crop.get("targets") or []) else None


def edit_record(m: dict, key: str) -> dict | None:
    edits = m.get("mask_edits")
    return edits.get(key) if isinstance(edits, dict) else None


def base_source(cand: dict, entry: dict | None, mask_source: str) -> tuple[str, str | None]:
    """hand = 손 마스크(없으면 none), hand_else_auto = 손 마스크가 있으면 손, 아니면 자동, auto = 자동, 자동이 없으면 손(없으면 none)."""
    hm = cand.get("hand_mask")
    hand = ("hand", hm["file"]) if hm else ("none", None)
    if mask_source == "hand" or (mask_source == "hand_else_auto" and hm):
        return hand
    if entry and not entry.get("skipped") and entry.get("automask"):
        return "auto", entry["automask"]
    return hand


def base_fp(cand: dict, entry: dict | None, mask_source: str) -> dict:
    """기준 지문. 정합·톤이 바뀌면 자동 마스크 파일(내용 주소)과 ph 가 함께 바뀐다. 분석 항목이 없으면 ph null."""
    src, file = base_source(cand, entry, mask_source)
    return {"src": src, "file": file, "ph": entry.get("params_hash") if entry else None, "ms": mask_source}


def is_stale(rec: dict, fp: dict) -> bool:
    return any(rec["base"].get(k) != fp.get(k) for k in _BASE_KEYS)


def edit_rect(bbox, canvas) -> list[int] | None:
    W, H = canvas
    x0, y0, x1, y1 = max(0, int(bbox[0])), max(0, int(bbox[1])), min(int(W), int(bbox[2])), min(int(H), int(bbox[3]))
    return [x0, y0, x1, y1] if x1 > x0 and y1 > y0 else None


def edit_mask(root: str, rec: dict) -> tuple[dict | None, str | None]:
    """기록 → 노드 마스크 dict. 쓸 수 없으면 (None, 경고) — 호출자는 기준 마스크를 쓰고 경고 앞에 후보를 붙인다.
    기준으로 떨어뜨리는 것은 파일 없음·크기 다름(명세 §3.2)과 다시 읽어도 같은 내용 오류(기록 형식·깨진 PNG — errno 없음)뿐이다.
    잠김·권한처럼 errno 가 있는 OSError 는 올린다(손·자동 마스크를 못 읽을 때와 같게 — 노드는 실패, 보드는 캐시하지 않는 422).
    떨어뜨리면 보드 서명(기록 file·rect)은 그대로라 편집 전 ★ 합성·이음매가 그 서명으로 캐시되어, 풀린 뒤에도 Studio 는 편집 전을
    보이고 Export 는 편집을 쓴다."""
    try:
        check_record(rec)
        with Image.open(store.resolve_path(root, rec["file"])) as im:
            arr = np.asarray(im.convert("L"))
    except FileNotFoundError:
        return None, "마스크 편집 파일 없음 → 기준 마스크"
    except OSError as e:
        if e.errno is not None:
            raise
        return None, f"마스크 편집을 읽지 못함({e}) → 기준 마스크"
    except (ValueError, SyntaxError, EOFError, Image.DecompressionBombError) as e:
        return None, f"마스크 편집을 읽지 못함({e}) → 기준 마스크"
    x0, y0, x1, y1 = rec["rect"]
    if arr.shape != (y1 - y0, x1 - x0):
        return None, f"마스크 편집 크기 {arr.shape[1]}x{arr.shape[0]} ≠ 기록의 창 {x1 - x0}x{y1 - y0} → 기준 마스크"
    return {"arr": arr, "left": x0, "top": y0, "bg": 0}, None


# ══════════════════════════════════════════════════════════════════════
# 검사
# ══════════════════════════════════════════════════════════════════════
def _keys(d, keys, what: str) -> None:
    if not isinstance(d, dict) or set(d) != set(keys):
        got = sorted(d) if isinstance(d, dict) else type(d).__name__
        raise ValueError(f"{what} 의 키는 {', '.join(keys)} 여야 합니다: {got}")


def _check_rect(v, what: str = "rect") -> None:
    if not (isinstance(v, list) and len(v) == 4 and all(isinstance(x, int) and not isinstance(x, bool) for x in v)):
        raise ValueError(f"{what} 는 정수 4개 [x0, y0, x1, y1] 여야 합니다: {v!r}")
    x0, y0, x1, y1 = v
    if x0 < 0 or y0 < 0 or x1 <= x0 or y1 <= y0 or (x1 - x0) * (y1 - y0) > MAX_PIXELS:
        raise ValueError(f"{what} 가 잘못되었습니다(음수·빈 창·4096² 초과): {v!r}")


def _check_base(b, what: str = "base") -> None:
    _keys(b, _BASE_KEYS, what)
    if b["src"] not in BASE_SRCS or b["ms"] not in MASK_SOURCES:
        raise ValueError(f"{what}.src 는 {BASE_SRCS}, ms 는 {MASK_SOURCES} 중 하나여야 합니다: {b['src']!r} {b['ms']!r}")
    if (b["src"] == "none") != (b["file"] is None) or not isinstance(b["file"], (str, type(None))):
        raise ValueError(f"{what}.file 은 src none 이면 null, 아니면 문자열이어야 합니다: {b['file']!r}")
    if not isinstance(b["ph"], (str, type(None))):
        raise ValueError(f"{what}.ph 는 문자열 또는 null 이어야 합니다: {b['ph']!r}")


def _check_ops(v) -> None:
    if not isinstance(v, str) or len(v) > OPS_MAX or _CTRL_RE.search(v):
        raise ValueError(f"ops 는 {OPS_MAX}자 이하 한 줄 문자열이어야 합니다: {str(v)[:40]!r}")


def _check_version(v: dict, what: str) -> None:
    if not isinstance(v["file"], str) or not _FILE_RE.fullmatch(v["file"]):
        raise ValueError(f"{what}.file 은 masks/ 바로 아래 PNG 이름이어야 합니다: {v['file']!r}")
    _check_rect(v["rect"], f"{what}.rect")
    _check_base(v["base"], f"{what}.base")
    _check_ops(v["ops"])
    if not isinstance(v["at"], str) or not _AT_RE.fullmatch(v["at"]):
        raise ValueError(f"{what}.at 은 'YYYY-MM-DD HH:MM:SS' 여야 합니다: {v['at']!r}")


def check_record(rec) -> dict:
    _keys(rec, _REC_KEYS, "마스크 편집 기록")
    _check_version(rec, "마스크 편집 기록")
    if not isinstance(rec["hist"], list) or len(rec["hist"]) > HIST_KEEP:
        raise ValueError(f"hist 는 {HIST_KEEP}개 이하 목록이어야 합니다")
    for i, h in enumerate(rec["hist"]):
        _keys(h, _HIST_KEYS, f"hist[{i}]")
        _check_version(h, f"hist[{i}]")
    return rec


def validate_png(data, rect=None) -> np.ndarray:
    """올린 편집 래스터 → uint8 HxW. rect 가 있으면 크기 = 그 창이어야 한다."""
    if not isinstance(data, (bytes, bytearray)) or bytes(data[:8]) != _PNG_SIG:
        raise ValueError("마스크 본문이 PNG 가 아닙니다")
    try:
        with Image.open(io.BytesIO(bytes(data))) as im:
            w, h = im.size
            if im.format != "PNG" or im.mode != "L" or "transparency" in im.info:
                raise ValueError(f"마스크 PNG 는 8비트 회색조(L, 투명 없음)여야 합니다: {im.mode}")
            if w * h > MAX_PIXELS:
                raise ValueError(f"마스크 PNG 가 너무 큽니다: {w}x{h} (최대 4096² 픽셀)")
            if rect is not None and (w, h) != (rect[2] - rect[0], rect[3] - rect[1]):
                raise ValueError(f"마스크 크기 {w}x{h} ≠ 편집 창 {rect[2] - rect[0]}x{rect[3] - rect[1]}")
            arr = np.asarray(im)
    except (OSError, EOFError, SyntaxError, Image.DecompressionBombError) as e:
        raise ValueError(f"깨진 PNG 입니다: {e}") from None
    return arr


# ══════════════════════════════════════════════════════════════════════
# 쓰기 (보드 라우트 — 잠금 안에서 seen 대조)
# ══════════════════════════════════════════════════════════════════════
def _expect(m: dict, key: str, seen) -> dict | None:
    """seen 대조 → 지금 기록(없으면 None). 형식이 깨진 기록(손으로 고친 매니페스트 — save_edit 은 쓰지 않음)은 없음으로 본다: _plan·보드
    (_edit_rec)가 편집 없음으로 보여 Studio 는 seen "" 를 보내므로, [적용]은 그 기록을 hist 없이 덮고 되돌리기·지우기는 지운다."""
    rec = edit_record(m, key)
    try:
        rec = rec if rec is None else check_record(rec)
    except ValueError:
        rec = None
    cur = rec["file"] if rec else ""
    if seen != cur:
        raise MaskConflict(f"마스크 편집이 다른 창에서 바뀌었습니다({seen or '없음'} → {cur or '없음'}) — 다시 받기", "changed")
    return rec


def save_edit(root: str, key: str, data, *, seen: str, rect: list[int], base: dict, ops: str,
              placed: Callable[[dict], list[int] | None] | None = None) -> tuple[dict, int]:
    """placed(m) = 잠근 매니페스트로 다시 계산한 지금 편집 창 — rect 와 다르면 409 rect_changed {rect}(창을 고른 뒤 잠그기 전에 Analyze 가 정합을
    바꾼 경우 옛 좌표로 저장하지 않게)."""
    _check_rect(rect)
    _check_base(base)
    _check_ops(ops)
    arr = validate_png(data, rect)

    def mutate(m):
        if star_cand(m, key) is None:
            raise MaskConflict("지금 ★ 후보가 아닙니다(★ 해제·탈락·크롭에서 빠짐) — 다시 받기", "not_picked")
        if placed is not None and (now := placed(m)) != list(rect):
            raise MaskConflict("편집 창이 바뀌었습니다(정합·배치 변경) — 다시 받기", "rect_changed", rect=now)
        old = _expect(m, key, seen)
        hist = [{k: old[k] for k in _HIST_KEYS}] + old["hist"] if old else []
        rec = check_record({"file": rel, "rect": list(rect), "base": dict(base), "ops": ops,
                            "at": time.strftime("%Y-%m-%d %H:%M:%S"), "hist": hist[:HIST_KEEP]})
        m["mask_edits"] = {**(m.get("mask_edits") or {}), key: rec}
        _touch_dropped(root, old, rec)  # HIST_KEEP 을 넘어 밀려난 판
        return rec

    # 파일 쓰기(같은 내용이면 옛 파일을 그대로 씀 — mtime 그대로)부터 기록까지 한 잠금: 보드 masks/ 정리(board.mask_cleanup, 같은 잠금)가 그 사이 지우지 않게
    with store.manifest_lock(root):
        rel = store.save_png_ca(root, MASKS_DIR, arr, prefix=re.sub(r"[^A-Za-z0-9_\-]", "", key)[:10] + "_e_")
        return store.update_manifest(root, mutate)


def _touch_dropped(root: str, before: dict | None, after: dict | None) -> None:
    """기록에서 빠지는 편집 파일(되돌리기·지우기·hist 밀려남)의 수정 시각을 지금으로 — 보드 masks/ 정리는 10분 안에 바뀐 파일을 남기므로,
    빠지기 전에 매니페스트를 읽어 간 실행(Export·Compose)이 쓰는 동안 지우지 않는다(Codex 3a 5차)."""
    files = lambda r: {x["file"] for x in [r, *r["hist"]]} if r else set()  # noqa: E731
    for f in files(before) - files(after):
        try:
            os.utime(store.resolve_path(root, f))
        except (OSError, ValueError):
            pass  # 이미 없는 파일·폴더 밖 이름 — 지킬 것 없음


def _drop(m: dict, key: str) -> None:
    """기록을 지운다(형식이 깨진 기록도 — _expect 가 없음으로 본 것). 기록이 없으면 그대로(update_manifest 가 저장하지 않음)."""
    if isinstance(m.get("mask_edits"), dict):
        m["mask_edits"].pop(key, None)


def undo_edit(root: str, key: str, *, seen: str) -> tuple[dict | None, int]:
    def mutate(m):
        old = _expect(m, key, seen)
        if old is None or not old["hist"]:
            _drop(m, key)
            _touch_dropped(root, old, None)
            return None
        rec = m["mask_edits"][key] = check_record(dict(old["hist"][0], hist=old["hist"][1:]))
        _touch_dropped(root, old, rec)
        return rec

    return store.update_manifest(root, mutate)


def reset_edit(root: str, key: str, *, seen: str) -> tuple[None, int]:
    def mutate(m):
        old = _expect(m, key, seen)
        _drop(m, key)
        _touch_dropped(root, old, None)

    return store.update_manifest(root, mutate)
