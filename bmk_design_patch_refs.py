"""BMK Design Patch Refs — Studio S2 레퍼런스 편집기의 연결 문서(refs.json)·이름 규칙·시트 캐시·컷·변환·파일 동작(ComfyUI 비의존).

배경
----
파이프라인은 레퍼런스를 refs_dir 바로 아래 파일 이름으로 찾는다(store.match_refs — 번호·부위 기준). 이름 매칭은 같은 번호 크롭이
서로의 파일을 받고, S1 에서 크롭 이름을 바꾸면 고르는 파일이 바뀐다. S2 는 크롭 ID 로 잇는 연결 문서 <project>/refs.json 을 두고,
크롭의 레퍼런스를 처음 고칠 때 **그 순간의 이름 매칭 결과(files)와 자동 선택(main)을 고정**한다(지연 고정 — 고정만으로는 refs_auto·
Prepare 선택·cell_key·초안 키가 바뀌지 않음, 명세 §2.5 I3). 시트 이미지(PNG·JPG·WEBP·PSD/PSB)에서 부품을 잘라(사각형·SAM3·다각형)
회전·반전·축소한 뒤 refs_dir 에 새 파일로 저장한다 — 덮어쓰지 않고 지우지 않는다(옮기는 것은 _보류 뿐, D5).
store 가 prompt 를 import 하면 순환이라 새 모듈에 둔다. torch·comfy·folder_paths·aiohttp 를 import 하지 않는다
(store·prompt·psd + numpy·PIL·cv2). SAM3 호출은 노드(BMKDesignPatchRefCut → bmk_sam3_mask_layers.segment)가 하고, 이 모듈은 그 앞뒤
(창·자동 양점·후처리·결과 파일)만 맡는다. 라우트(보드)와 노드가 같은 함수를 쓴다.
명세: H:\\BmkNodeDesign\\MultiLayerCropEdit\\_proto\\STUDIO_S2_SPEC.md §1(데이터 모델·API)·§3(컷)·§5.5–5.6(요청 본문).

공개 API (예외는 ValueError + 한국어 메시지. 따로 적은 FileNotFoundError(→ 404)·FileExistsError·RefsConflict(→ 409)만 다름)
-----------------------------------------------------------------------------------------------------------------
연결 문서 refs.json (§1.2 스키마 bmk.design_patch.refs/1)
- REFS_NAME · REFS_SCHEMA · REFS_HISTORY_DIR · HELD_DIR("_보류") · MAX_LINK_FILES(64) · MAX_SHEETS(8) · FIX_WHY
- class RefsConflict(ValueError)                rev 불일치·이미 있음/없음(create) — 보드가 409
- class SheetChanged(ValueError)                컷 요청의 sheet_v 가 지금 시트 서명과 다름(바깥에서 저장됨) — 보드가 409 {sheet_changed}
- load_refs(root) -> dict | None                manifest_lock 안에서 읽고 validate. 깨졌으면 ValueError(refs_history 복구 안내)
- update_refs(root, mutate, *, expect_rev, create=False) -> (result, rev)
                                                S1 update_layout 과 같은 틀: 잠금·expect_rev·검증·이력 50(refs_history/r<rev>.json)·
                                                단조 rev(refs_history/max_rev.json)·원자 저장. mutate 예외면 저장 안 함.
                                                create=True 면 mutate 가 빈 dict 를 채운다(보통 d.update(new_refs(refs_dir))).
- new_refs(refs_dir) -> dict                    빈 문서(rev 0, links·files·sheets 비움)
- validate_refs(doc) -> list[str]               구조 오류 ValueError, 반환 = 경고(한 파일이 여러 크롭에 연결됨)
- refs_sig(doc, refs_dir) -> str | None         links 의 (id, main, files) + refs_dir 해시(16자). 문서 없음·refs_dir 불일치면 None
- resolve_refs(refs_dir, crop, doc) -> {"refs_auto", "refs_main", "linked", "notes", "missing", "unlinked"}   §1.3
- seed_link(refs_dir, crop) -> {"files", "main"}   고정 값 = match_refs 순서 그대로 + prompt.auto_pick_ref (D1). 64개 넘으면 앞 64개 + 경고
- fix_link(doc, refs_dir, crop, why) -> link    고정 안 된 크롭이면 seed(why "none" 은 빈 목록)로 links 에 넣고 fixed{at,why,name}.
                                                이미 고정이면 그대로 돌려준다(update_refs 의 mutate 안에서 쓰는 순수 함수)
- explicit_refs(root, m) -> {ref_key(이름): [(crop_id, tid, "spec"|"manifest")]}
                                                스펙 targets[].refs·매니페스트 타깃 refs 가 이름으로 직접 가리키는 파일(D5). 해석은
                                                bmk_design_patch_store._spec_files_fp 와 같다(절대 경로는 그대로, 아니면 refs_dir 기준).
                                                키 = refs_dir 기준 상대 경로('/' 구분, 보류면 "_보류/<이름>")의 ref_key — refs_dir 밖은 뺀다
- ref_key(name) -> str                          이름 비교 키(NFC·'/'·casefold — Windows 는 대소문자를 구분하지 않는 경로)
- norm_refs_dir(path) -> str · same_refs_dir(a, b) -> bool   refs.json 에 적는 꼴(절대·'/') / 같은 폴더인지(normcase)
이름 규칙 (§1.5, 공용 표 tools/fixtures/refs_rules.json — Python·JS 같은 값)
- check_ref_name(name, *, any_ext=False) -> str 파일 이름만(구분자·'..'·Windows 금지 문자·제어 문자 금지, REF_EXTS). NFC 로 바꾸지 않음
- check_desc(desc) -> str                       NFC·strip, 1–24자, 유니코드 L*·N*·'-'(글자·숫자 하나 이상), "_"+설명에 _t 표지 금지
- ref_file_name(crop_name, role, desc=None) -> str   대표 "<크롭 이름>_t.png", 보조 "<크롭 이름>_<설명>_t.png" — match_refs 재매칭 확인
- check_transform(t) -> {"rotate", "flip_h", "scale", "pad"}   rotate −180…180(0.5° 단위), scale 0.05–1, pad 0–256(기본 8)
시트·이미지 (CPU, 라우트는 _REF_SLOTS 세마포어로 한 번에 하나)
- SHEET_EXTS · SHEET_EXCLUDE · SHEETS_DIR("derived/sheets") · SHEET_CACHE_KEEP(4) · MAX_RECT_AREA(8192²)
- list_sheets(root, m) -> [{path, size, kind("image"|"psd"), bytes}]   §5.2(m = 매니페스트 — source.refs_dir·source.psd)
- sheet_check(root, m, rel) -> {path, file, size, kind, bytes}   목록 규칙 검사(절대·'..' = ValueError, 목록 밖 = FileNotFoundError)
- sheet_sig(root, rel, layer=None) -> str        캐시 서명(상대 경로·지문·레이어·캐시 판)
- sheet_array(root, rel, layer=None) -> np.memmap(H, W, 4) uint8   캐시 .npy(PSD = 보이는 레이어 수동 합성 — 수동 합성이 다르게
                                                그리는 요소(_psd_lossy: blend·클리핑·fx·shape·조정, Fill < 255·벡터 마스크·Blend If·마스크
                                                농도/페더, 그룹 포함)가 있거나 레이어가 없으면 파일의 병합 이미지(보이는 가이드 그룹이 들어
                                                있으면 경고 로그), layer = 그 노드만)
- sheet_preview(root, rel, layer=None) -> (jpg 경로, sig)   긴 변 2048, 흰 바탕(캐시 <sig>_prev.jpg) — sig = 그 그림을 만든 캐시의 서명
- sheet_region(root, rel, rect, max_side, layer=None) -> (bytes, sig)   원해상도 창 → 긴 변 ≤ max_side, 흰 바탕 JPEG q90 — sig 같음
- cut_window(arr, rect, pad_frac) -> (rgb_on_white HxWx3, alpha HxW uint8, window [x0,y0,x1,y1])
                                                창 = rect + 변마다 (폭·높이 × pad_frac) 반올림, 시트 안으로 자름(U0 _expand 와 같음)
- raster_polys(shape, origin, polys, base=None) -> float32   다각형 더하기(max)/빼기(min), 차례대로 — 덮임 = 4배 초표본 면적 평균
                                                (명세 §1.4 의 LINE_AA 는 경계 밖 0.7px 번짐·아래 1px 치우침을 실측해 바꿈)
- merge_alpha(sheet_alpha, mask, grow, feather) -> uint8      시트 알파 × (mask → grow(정사각 커널) → 가우시안 σ) — SAM3 Mask Layers 순서
- transform_rgba(rgba, rotate, flip_h, scale, pad) -> rgba    회전 → 반전 → 배율 → 다듬기(알파 bbox + pad). 90° 배수·배율 1 은
                                                np.rot90 그대로(바이트 같음), 그 밖은 프리멀티 알파 보간(INTER_LINEAR·INTER_AREA)
- same_name(folder, name) -> 실제 이름 | None    folder 바로 아래의 같은 이름(ref_key — NTFS 는 NFC·NFD 를 다른 이름으로 봄). 아래 셋의 '같은 이름'
- save_ref(refs_dir, name, rgba) -> path        배타 생성(이미 있으면 FileExistsError, 덮어쓰기 0) + 원자(임시 파일 → 이름 바꾸기)
- hold_file(refs_dir, name) -> 보류 이름          refs_dir/_보류/ 로 옮김. 같은 이름이 있으면 "<stem>__YYMMDD-HHMMSS<ext>"(-2, -3 …)
- restore_file(refs_dir, held, name=None) -> 이름   _보류 에서 name(보류 때 원래 이름 — refs.json held 기록, 없으면 held 그대로)으로
                                                되살림. 같은 이름이 있으면 FileExistsError(409)
컷 계약 (§3·§5.5 — U2 노드·U3 라우트가 같이 씀)
- CUT_DIR = "derived/refcut" · CUT_SCHEMA · CUT_METHODS = ("rect", "sam3", "poly")
- SAM_DEFAULTS = {"pad": 0.12, "refine": 0, "drop_small": 0.005, "multimask": "always"}   U0 실측(§6.5 끝·§3.2)
- new_cut_request(root, m, body) -> req dict     §5.5 본문 검증 → req = uuid4 hex 32자 → <req>.req.json 원자 저장 → 정규화한 요청
      req dict = {schema, req, at, sheet(정규화 상대 경로), layer, sheet_fp(요청 때 시트 지문), sheet_size, rect, method, points[[x,y,0|1]],
                  text, polys[{op: add|sub, pts: [[x,y],…]}], base, pad, refine, drop_small, grow, feather, ckpt, multimask}
      시트 = sheet_check 규칙을 통과한 프로젝트 상대 경로만. rect = 정수 4·시트 안·짧은 변 ≥ 16·넓이 ≤ 8192². points ≤ 32(rect 안),
      text ≤ 80자 ASCII(points·text·ckpt 는 sam3 만), polys ≤ 16 × 꼭짓점 3–256(시트 안 좌표, px 연속 좌표 — 픽셀 i 는 [i, i+1)),
      base = 끝난(ok) 앞 컷 req(method poly 만, 같은 시트·layer·rect·지문 — 창(pad)은 base 것을 쓴다). pad 0–0.5(기본 sam3 0.12,
      그 밖 0 — 창 = 선택), refine 0–5, drop_small 0–0.5, grow −64…64(px), feather 0–64(σ). 모르는 키는 거절("root" 는 무시).
      sheet_v(선택, 저장 안 함) = 화면이 연 시트의 서명(목록 행 v) — 지금 sheet_sig(시트, layer)와 다르면 SheetChanged(base 다듬기도).
- load_cut_request(root, req) -> req dict        파일을 다시 검증(사람이 고칠 수 있음). req 형식 [0-9a-f]{32}, 없으면 FileNotFoundError,
                                                시트 지문이 요청 때와 다르면 ValueError
- auto_point(alpha_bool, rect_in_window) -> (x, y, 1) | None   상자 가운데에 가장 가까운 불투명 픽셀(창 좌표 — alpha_bool = 창 알파 > 127,
                                                U0 와 같음). 노드는 사람 점이 있어도 늘 맨 앞에 넣는다(§3.2). 상자 안이 모두 투명이면 None.
                                                rect_in_window = [rect[0]−창x0, rect[1]−창y0, rect[2]−창x0, rect[3]−창y0]
- finish_mask(raw_bool_window, req, window, sheet_alpha_window, base_mask=None) -> (sel float32 0..1, stats)
      **선택 마스크**(다듬기 전): base 가 없으면 raw(rect = 전부 1, poly = 0, sam3 = SAM 결과) 의 rect 밖 0 → 작은 조각(전체의 drop_small
      미만, 8-연결) 버림, base 가 있으면 base 선택 그대로 → polys 더하기/빼기. stats = {"area_frac": Σ(sel·시트α)/rect 넓이,
      "n_parts": (sel ≥ 0.5 ∧ 시트α > 0) 8-연결 조각 수}. grow·feather·× 시트 알파는 여기서 하지 않는다(apply_cut·merge_alpha) —
      마스크 파일을 다음 다듬기(base)에 그대로 다시 쓰므로 grow·feather 가 겹쳐 쌓이지 않게(명세 §1.4 는 한 함수로 적었음).
- save_cut_result(root, req, sel, info) -> res   <req>_mask.png(L, 창 크기 = 선택 마스크) + <req>.res.json 원자.
      res = {ok: true, req, at, window, rect, area_frac, n_parts, sec, ckpt, warnings, …info 의 나머지, mask(프로젝트 상대 경로), mask_sha}
      info 에 window(필수)·rect·area_frac·n_parts(= stats)·sec·ckpt·warnings 를 넣는다. 마스크를 먼저 쓰고 res 를 쓴다(res 있음 = 끝남)
- save_cut_error(root, req, message) -> None     res.json {ok: false, req, at, error}
- load_cut_result(root, req) -> dict | None      없으면 None(아직 대기), 깨졌으면 ValueError
- load_cut_mask(root, req) -> float32 HxW        <req>_mask.png / 255 (finish_mask 의 base_mask 로)
- prune_cuts(root, keep=()) -> 지운 파일 수       7일 넘었거나 최근 200개 밖인 요청의 파일을 지움. keep = 32자 req 가 든 문자열들
                                                (refs.json files[].cut.mask 경로 그대로 넘겨도 됨) — 그 요청은 남김
- apply_cut(root, req, res) -> rgba HxWx4 uint8  시트 창 RGB + 알파 merge_alpha(시트α, 선택 마스크, req grow, req feather) = 저장 전
                                                RGBA(변환 전, 창 크기). 시트 지문이 요청 때와 다르면 ValueError(다시 컷)

규칙 메모
---------
- refs.json 은 S1 layout.json 과 같은 규칙: 3-way 병합 없음, 낙관적 동시성(expect_rev), 잠금 = 매니페스트와 같은 store.manifest_lock.
  update_refs 안에서 update_layout·update_manifest 를 부르지 않는다(S1 이름 바꾸기 고정은 update_refs 를 끝낸 뒤 update_layout, §2.6).
- 파일 이름은 디스크 이름 그대로 둔다(명세 §1.2 는 "NFC" — 그러나 NTFS 는 NFC·NFD 를 다른 이름으로 보므로, match_refs 가 돌려준
  이름을 바꾸면 Prepare 가 같은 파일을 못 연다). S2 가 새로 만드는 이름은 크롭 이름(S1 규칙 = NFC)이라 NFC 다.
  중복은 casefold 로 본다(Windows 는 대소문자를 구분하지 않음).
- 시트 목록은 refs_dir 바로 아래, 어느 깊이든 이름이 '_'·'.' 로 시작하는 폴더(_보류·_prep_runs …), 작업 PSD(source.psd)를 뺀다
  (10/16 결정 — 261014_Test 는 _prep_runs/** 와 작업 PSD 때문에 117개였다 → 4개).
- 시트 캐시는 프로세스 잠금(_SHEET_LOCK) 안에서 만들고(라우트·노드 스레드가 같은 시트를 동시에 만들지 않게), 4개 넘으면 오래 안 쓴
  것(mtime — 적중 때 갱신)부터 지운다. 다른 스레드가 memmap 으로 연 파일은 Windows 에서 지워지지 않아 다음 정리로 미룬다.
  만들기 전·후 서명이 다르면(읽는 동안 저장) 그 캐시를 버리고 다시 만들고, 미리보기 이름은 읽은 캐시의 서명으로 짓는다(codex4 P2).
- 회전 각은 양수 = 반시계(cv2.getRotationMatrix2D·np.rot90 과 같은 뜻), 반전은 회전 뒤 좌우.
- 보류 기록 held(refs.json 최상위, 보드의 hold·대표 교체가 씀) = 보류 이름 → {from: 원래 이름, crop: 보류한 크롭 ID}. 되살리기는 from 으로
  (이름에서 접미를 짐작하지 않음 — 원래 이름이 "__YYMMDD-HHMMSS" 꼴일 수 있음), 보드 보류 목록은 지금 번호 접두 밖에 crop·files[].for 로도
  크롭을 찾는다(S1 번호 바꾸기 뒤에도 보임, codex6). 기록이 없는 보류 파일(사람이 옮김)은 이름 그대로 되살린다.

버전 이력
---------
v1 (2026-10, Studio S2 U1)
- 최초 구현: refs.json 스키마·쓰기·해석·고정, 이름 규칙(공용 표 refs_rules.json), 시트 목록·캐시·미리보기·창, 컷 요청·후처리·결과 파일,
  변환(프리멀티), 파일 동작(배타 생성·보류·되살리기), explicit_refs.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import math
import os
import re
import struct
import threading
import time
import unicodedata
import uuid
from typing import Any, Callable, Iterable

import cv2
import numpy as np
from PIL import Image

try:
    from . import bmk_design_patch_prompt as prompt
    from . import bmk_design_patch_psd as dpsd
    from . import bmk_design_patch_store as store
except ImportError:  # 단독 실행/테스트
    import bmk_design_patch_prompt as prompt
    import bmk_design_patch_psd as dpsd
    import bmk_design_patch_store as store

logger = logging.getLogger(__name__)

_TAG = "[ComfyUI_BMK_Nodes::DesignPatch]"

REFS_NAME = "refs.json"
REFS_SCHEMA = "bmk.design_patch.refs/1"
REFS_HISTORY_DIR = "refs_history"
HELD_DIR = "_보류"
MAX_LINK_FILES = 64     # 크롭당 연결 파일 — seed(이름 매칭)를 자르지 않을 만큼(10/16 결정, 넘으면 seed_link)
MAX_SHEETS = 8
FIX_WHY = ("edit", "rename", "fix", "none")
DESC_MAX = 24
TRIM_PAD = 8

SHEET_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".psd", ".psb")
SHEET_EXCLUDE = ("inputs", "cands", "derived", "masks", "vlm", "specs", "layout_history", "refs_history")
SHEET_MIN_SIDE = 256
SHEET_MAX_DEPTH = 3       # 폴더 깊이(루트 바로 아래 파일 = 0, 00_원본/psd/x.psd = 2)
SHEET_LIST_MAX = 200
SHEETS_DIR = "derived/sheets"
SHEET_CACHE_KEEP = 4
SHEET_PREVIEW = 2048
MAX_RECT_AREA = 8192 * 8192
_SHEET_RENDER = 3         # 시트 캐시 판(sheet_sig 에 들어감) — 합성 방식을 바꾸면 올려 옛 .npy 를 버린다(2 = PSD 병합 이미지, 3 = 경고 없이
                          # 틀리는 Fill·벡터 마스크·Blend If 등도 병합 이미지, 10/16)

CUT_DIR = "derived/refcut"
CUT_SCHEMA = "bmk.design_patch.refcut/1"
CUT_METHODS = ("rect", "sam3", "poly")
SAM_DEFAULTS = {"pad": 0.12, "refine": 0, "drop_small": 0.005, "multimask": "always"}
CUT_KEEP_DAYS = 7
CUT_KEEP_N = 200
_CUT_BODY_KEYS = ("sheet", "layer", "rect", "method", "points", "text", "polys", "base", "pad", "refine", "drop_small",
                  "grow", "feather", "ckpt")
_CUT_FILE_RE = re.compile(r"([0-9a-f]{32})(\.req\.json|\.res\.json|_mask\.png)")
_REQ_RE = re.compile(r"[0-9a-f]{32}")

_ID_RE = re.compile(r"[A-Za-z0-9_\-]{1,64}")
_BAD_NAME_CHARS = frozenset('<>:"/\\|?*')
_DESC_PROBLEMS = {
    "len": f"설명은 1–{DESC_MAX}자여야 합니다",
    "char": "설명에는 글자·숫자·- 만 쓸 수 있고 글자·숫자가 하나는 있어야 합니다(_ 공백 . 등 금지)",
    "tmark": "설명이 t·t2·tㄱ 꼴로 시작하면 파일 이름에 '_t' 표지가 생겨 이름 매칭이 설명 앞을 부위로 읽지 못합니다(top·tassel 은 됨)",
}
_SHEET_LOCK = threading.Lock()


class RefsConflict(ValueError):
    """refs.json rev 가 요청과 다름(다른 창에서 바뀜)·create 인데 이미 있음·파일이 없음 — 보드가 409 로 돌려준다."""


class SheetChanged(ValueError):
    """컷 요청의 sheet_v(화면이 연 시트의 서명)가 지금 시트 서명과 다름 — 바깥에서 저장된 시트의 옛 미리보기로 고른 영역을 지금 파일에서
    오리지 않게. 보드가 409 {sheet_changed: true} 로 돌려준다(화면은 시트를 다시 연다)."""


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _num(v, what: str, lo: float, hi: float) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not lo <= v <= hi:
        raise ValueError(f"{what} 는 {lo}–{hi} 사이의 수여야 합니다: {v!r}")
    return v


def _int(v, what: str, lo: int, hi: int) -> int:
    if not _is_int(v) or not lo <= v <= hi:
        raise ValueError(f"{what} 는 {lo}–{hi} 사이의 정수여야 합니다: {v!r}")
    return v


def _rect4(v, what: str) -> list[int]:
    if not isinstance(v, list) or len(v) != 4 or not all(_is_int(x) for x in v) or v[2] <= v[0] or v[3] <= v[1]:
        raise ValueError(f"{what} 는 정수 [x0, y0, x1, y1](x1>x0, y1>y0) 여야 합니다: {v!r}")
    return v


# ══════════════════════════════════════════════════════════════════════
# 이름
# ══════════════════════════════════════════════════════════════════════
def check_ref_name(name: Any, *, any_ext: bool = False) -> str:
    """레퍼런스 파일 이름(경로 아님) 검사: 문자열 1–255자, '.'·'..' 아님, 끝이 '.'·공백 아님, Windows 금지 문자(< > : " / \\ | ? *)·제어 문자 없음,
    확장자 REF_EXTS(any_ext 면 보지 않음 — _보류 의 PSD 표시용). 이름을 바꾸지 않고 그대로 돌려준다(모듈 docstring '규칙 메모')."""
    if not isinstance(name, str) or not 1 <= len(name) <= 255:
        raise ValueError(f"레퍼런스 파일 이름이 비었거나 255자를 넘습니다: {name!r}")
    if (name in (".", "..") or name.endswith((".", " "))
            or any(c in _BAD_NAME_CHARS or unicodedata.category(c) == "Cc" for c in name)):
        raise ValueError(f"레퍼런스 파일 이름에 경로·금지 문자를 쓸 수 없습니다: {name!r}")
    if not any_ext and os.path.splitext(name)[1].lower() not in store.REF_EXTS:
        raise ValueError(f"레퍼런스 파일은 {'·'.join(store.REF_EXTS)} 만 됩니다: {name!r}")
    return name


def _check_record_key(key: Any) -> str:
    """refs.json files 키: refs_dir 바로 아래 이름 또는 "_보류/<이름>"."""
    if not isinstance(key, str):
        raise ValueError(f"files 키가 문자열이 아닙니다: {key!r}")
    head, sep, rest = key.partition("/")
    if sep and head != HELD_DIR:
        raise ValueError(f"files 키는 이름 또는 '{HELD_DIR}/<이름>' 이어야 합니다: {key!r}")
    check_ref_name(rest if sep else key)
    return key


def ref_key(name: str) -> str:
    """이름 비교 키: NFC · 역슬래시 → '/' · casefold. explicit_refs 의 키이고, 같은 파일을 가리키는지 볼 때 양쪽에 쓴다."""
    return unicodedata.normalize("NFC", str(name)).replace("\\", "/").casefold()


def norm_refs_dir(path: Any) -> str:
    """refs.json 에 적는 refs_dir 꼴: 절대 경로, '/' 구분. 빈 값이면 ""."""
    p = str(path or "").strip()
    return os.path.abspath(p).replace("\\", "/") if p else ""


def same_refs_dir(a: Any, b: Any) -> bool:
    """두 refs_dir 가 같은 폴더인지(절대 경로·구분자·대소문자 정규화 — 매니페스트 refs_dir 는 위젯 문자열 그대로). 빈 값은 늘 다름."""
    na, nb = norm_refs_dir(a), norm_refs_dir(b)
    return bool(na) and os.path.normcase(na) == os.path.normcase(nb)


def _desc_problem(d: str) -> str | None:
    """정규화한 설명의 위반 코드(이 순서로 첫 번째): "len"(1–24 코드 포인트) · "char"(L*·N*·'-' 밖 글자, 또는 '-' 뿐) ·
    "tmark"("_" + 설명에 store._REF_T_RE 표지 — t·T·t2·tㄱ·t-앞 거절, top·tassel 허용). 맞으면 None."""
    if not 1 <= len(d) <= DESC_MAX:
        return "len"
    if set(d) == {"-"} or any(c != "-" and unicodedata.category(c)[0] not in "LN" for c in d):
        return "char"
    if store._REF_T_RE.search("_" + d):
        return "tmark"
    return None


def check_desc(desc: Any) -> str:
    """보조 레퍼런스 설명(§1.5): NFC → 앞뒤 공백 제거(str.strip) → 위 규칙. 정규화한 설명을 돌려주고 위반은 ValueError."""
    if not isinstance(desc, str):
        raise ValueError(f"설명은 문자열이어야 합니다: {desc!r}")
    d = unicodedata.normalize("NFC", desc).strip()
    problem = _desc_problem(d)
    if problem:
        raise ValueError(f"{_DESC_PROBLEMS[problem]}: {d!r}")
    return d


def _part_rank(fn: str, part: str) -> int:
    """match_refs 의 부위 순위(같음 0 / part 로 시작 1 / 그 밖 2) — 'NNN_' 뒤 '_t…' 표지 앞까지를 부위로 본다."""
    npart = store._gloss_norm(part) if part else ""
    if not npart:
        return 0
    stem = os.path.splitext(unicodedata.normalize("NFC", fn))[0].split("_", 1)[1]
    mt = store._REF_T_RE.search(stem)
    rest = store._gloss_norm(stem[: mt.start()] if mt else stem)
    return 0 if rest == npart else (1 if rest.startswith(npart) else 2)


def ref_file_name(crop_name: Any, role: str, desc: Any = None) -> str:
    """저장 이름(§1.5): 대표 "<크롭 이름>_t.png"(desc 무시), 보조 "<크롭 이름>_<check_desc(설명)>_t.png".
    크롭 이름은 S1 이름 규칙(NFC·strip·5–64자·'NNN_'·L*·N*·_·-)을 통과해야 하고, 결과 이름이 match_refs 로 그 크롭에 다시 잡혀야 한다
    (대표 = 부위 순위 0, 보조 = 1 — 크롭 이름에 '_t' 표지 꼴(예: 009_모자_t)이 있으면 설명 앞을 부위로 읽지 못해 거절)."""
    if role not in ("main", "aux"):
        raise ValueError(f"역할은 main(대표) 또는 aux(보조)여야 합니다: {role!r}")
    if (not isinstance(crop_name, str) or unicodedata.normalize("NFC", crop_name).strip() != crop_name
            or store._name_problem(crop_name)):
        raise ValueError(f"크롭 이름이 이름 규칙 밖이라 레퍼런스 이름을 만들 수 없습니다 — S1 에서 이름을 바꾸세요: {crop_name!r}")
    name = f"{crop_name}_t.png" if role == "main" else f"{crop_name}_{check_desc(desc)}_t.png"
    if _part_rank(name, store.parse_crop_layer_name(crop_name)["part"]) != (0 if role == "main" else 1):
        raise ValueError(f"크롭 이름에 '_t' 표지 꼴이 있어 {name!r} 이 이름 매칭에서 이 크롭의 부위로 읽히지 않습니다 — 크롭 이름을 바꾸세요")
    return name


def check_transform(t: Any) -> dict:
    """변환 값 검사·기본값: rotate −180…180 의 0.5 배수(기본 0), flip_h bool(False), scale 0.05–1(1.0, 확대 없음 — D4), pad 0–256 정수(8).
    모르는 키는 무시한다. 반환 {"rotate": float, "flip_h": bool, "scale": float, "pad": int}."""
    if not isinstance(t, dict):
        raise ValueError(f"변환은 객체여야 합니다: {t!r}")
    r = _num(t.get("rotate", 0), "rotate", -180, 180)
    if (r * 2) % 1:
        raise ValueError(f"rotate 는 0.5° 단위여야 합니다: {r!r}")
    flip = t.get("flip_h", False)
    if not isinstance(flip, bool):
        raise ValueError(f"flip_h 는 true/false 여야 합니다: {flip!r}")
    return {"rotate": float(r), "flip_h": flip, "scale": float(_num(t.get("scale", 1.0), "scale", 0.05, 1.0)),
            "pad": _int(t.get("pad", TRIM_PAD), "pad", 0, 256)}


# ══════════════════════════════════════════════════════════════════════
# refs.json
# ══════════════════════════════════════════════════════════════════════
def new_refs(refs_dir: Any) -> dict:
    """빈 연결 문서(rev 0). update_refs(create=True) 의 mutate 에서 d.update(new_refs(refs_dir))."""
    return {"schema": REFS_SCHEMA, "rev": 0, "refs_dir": norm_refs_dir(refs_dir), "links": {}, "files": {}, "sheets": []}


def validate_refs(doc: Any) -> list[str]:
    """refs.json 구조 검사. 구조 오류는 ValueError: schema·rev(0 이상 정수)·refs_dir(빈 문자열 아님)·links(키 = 크롭 ID [A-Za-z0-9_-]{1,64},
    files = check_ref_name 이름 ≤ MAX_LINK_FILES·중복 없음(casefold), main = files 안 또는 files 가 비었을 때만 null, fixed = 객체(why ∈ FIX_WHY,
    auto 있으면 bool — 보드가 부수 효과로 정한 연결의 표지)·
    files(키 = 이름 또는 "_보류/<이름>", 값 = 객체 — for·size·rect·transform 이 있으면 형식)·sheets(≤ 8, {path, layer, view})·
    held(있으면 — S2 가 보류한 파일 {보류 이름: {from: 원래 이름, crop: 보류한 크롭 ID | null}}, 되살리기 이름·보류 목록의 크롭).
    반환 = 경고(같은 파일이 여러 크롭에 연결됨 — 허용). 알 수 없는 키는 그대로 둔다(검사만 함)."""
    if not isinstance(doc, dict):
        raise ValueError(f"레퍼런스 연결 최상위가 객체가 아닙니다: {type(doc).__name__}")
    if doc.get("schema") != REFS_SCHEMA:
        raise ValueError(f"레퍼런스 연결 schema 가 {REFS_SCHEMA!r} 가 아닙니다: {doc.get('schema')!r}")
    rev = doc.get("rev")
    if not _is_int(rev) or rev < 0:
        raise ValueError(f"레퍼런스 연결 rev 가 0 이상의 정수가 아닙니다: {rev!r}")
    if not isinstance(doc.get("refs_dir"), str) or not doc["refs_dir"].strip():
        raise ValueError("레퍼런스 연결 refs_dir 가 비었습니다.")
    links, files, sheets = doc.get("links"), doc.get("files"), doc.get("sheets")
    if not isinstance(links, dict) or not isinstance(files, dict) or not isinstance(sheets, list):
        raise ValueError("레퍼런스 연결에 links(객체)·files(객체)·sheets(목록)가 있어야 합니다.")

    owners: dict[str, tuple[str, list[str]]] = {}
    for cid, ln in links.items():
        if not isinstance(cid, str) or not _ID_RE.fullmatch(cid):
            raise ValueError(f"links 키(크롭 ID) 형식이 잘못되었습니다: {cid!r}")
        if not isinstance(ln, dict) or not isinstance(ln.get("files"), list):
            raise ValueError(f"크롭 {cid} 연결에 files 목록이 없습니다.")
        fl = ln["files"]
        if len(fl) > MAX_LINK_FILES:
            raise ValueError(f"크롭 {cid} 연결 파일이 {MAX_LINK_FILES}개를 넘습니다({len(fl)}개).")
        seen: set[str] = set()
        for f in fl:
            check_ref_name(f)
            if f.casefold() in seen:
                raise ValueError(f"크롭 {cid} 연결 파일이 겹칩니다: {f!r}")
            seen.add(f.casefold())
            owners.setdefault(ref_key(f), (f, []))[1].append(cid)
        main = ln.get("main")
        if (fl and main not in fl) or (not fl and main is not None):
            raise ValueError(f"크롭 {cid} 대표(main)는 files 안의 이름이어야 하고, files 가 비었을 때만 null 입니다: {main!r}")
        fixed = ln.get("fixed")
        if fixed is not None and (not isinstance(fixed, dict) or fixed.get("why", "edit") not in FIX_WHY
                                  or not isinstance(fixed.get("auto", False), bool)):
            raise ValueError(f"크롭 {cid} fixed 형식이 잘못되었습니다(why ∈ {FIX_WHY}, auto 는 참/거짓): {fixed!r}")
    for key, rec in files.items():
        _check_record_key(key)
        if not isinstance(rec, dict):
            raise ValueError(f"files[{key!r}] 가 객체가 아닙니다.")
        if rec.get("for") is not None and (not isinstance(rec["for"], str) or not _ID_RE.fullmatch(rec["for"])):
            raise ValueError(f"files[{key!r}].for 는 크롭 ID 여야 합니다: {rec['for']!r}")
        size = rec.get("size")
        if size is not None and (not isinstance(size, list) or len(size) != 2 or not all(_is_int(v) and v > 0 for v in size)):
            raise ValueError(f"files[{key!r}].size 는 양의 정수 [w, h] 여야 합니다: {size!r}")
        if rec.get("rect") is not None:
            _rect4(rec["rect"], f"files[{key!r}].rect")
        if rec.get("transform") is not None:
            check_transform(rec["transform"])
    held = doc.get("held", {})
    if not isinstance(held, dict):
        raise ValueError("held 는 객체여야 합니다({보류 이름: {from, crop}}).")
    for key, rec in held.items():
        check_ref_name(key)
        if (not isinstance(rec, dict) or not isinstance(rec.get("from"), str)
                or (rec.get("crop") is not None and (not isinstance(rec["crop"], str) or not _ID_RE.fullmatch(rec["crop"])))):
            raise ValueError(f"held[{key!r}] 는 {{from: 원래 이름, crop: 크롭 ID | null}} 이어야 합니다: {rec!r}")
        check_ref_name(rec["from"])
    if len(sheets) > MAX_SHEETS:
        raise ValueError(f"sheets 는 {MAX_SHEETS}개 이하여야 합니다({len(sheets)}개).")
    for s in sheets:
        view = s.get("view") if isinstance(s, dict) else None
        if (not isinstance(s, dict) or not isinstance(s.get("path"), str) or not s["path"]
                or (s.get("layer") is not None and not isinstance(s["layer"], str))
                or (view is not None and (not isinstance(view, list) or len(view) != 4
                                          or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in view)))):
            raise ValueError(f"sheets 항목 형식이 잘못되었습니다({{path, layer, view}}): {s!r}")
    return [f"{name}: 크롭 {', '.join(ids)} 에 함께 연결됨" for name, ids in owners.values() if len(ids) > 1]


def load_refs(root: str) -> dict | None:
    """<root>/refs.json 을 읽어 validate_refs 를 통과한 dict 로. 없으면 None, 깨졌거나 구조 오류면 ValueError(refs_history 복구 안내 —
    조용히 이름 매칭으로 돌아가면 원천이 뒤집힘). 매니페스트와 같은 잠금 안에서 읽는다(Windows os.replace 경합, store 규칙 메모)."""
    path = os.path.join(root, REFS_NAME)
    if not os.path.isfile(path):
        return None
    try:
        with store.manifest_lock(root):
            doc = store._read_json(path, "레퍼런스 연결")
        validate_refs(doc)
    except ValueError as e:
        raise ValueError(f"{e} — {REFS_HISTORY_DIR}/r<rev>.json 을 {REFS_NAME} 로 복사해 이전 판으로 되돌릴 수 있습니다") from e
    return doc


def _keep_history(root: str, prev: dict) -> int:
    """이전 판을 refs_history/r<rev>.json 으로 남기고(이미 있으면 덮지 않음) rev 가 큰 50개만 둔다. 반환 = 이력의 가장 큰 rev."""
    folder = os.path.join(root, REFS_HISTORY_DIR)
    path = os.path.join(folder, f"r{prev['rev']}.json")
    if not os.path.isfile(path):
        store.write_json_atomic(path, prev)
    revs = store._layout_history_revs(folder)
    for r in revs[:-store._LAYOUT_HISTORY_KEEP]:
        try:
            os.remove(os.path.join(folder, f"r{r}.json"))
        except OSError as e:
            logger.warning(f"{_TAG} 레퍼런스 연결 이력 r{r}.json 을 지우지 못함(저장은 계속): {e}")
    return revs[-1]


def update_refs(root: str, mutate: Callable[[dict], Any], *, expect_rev: int | None, create: bool = False) -> tuple[Any, int]:
    """refs.json 쓰기(낙관적 동시성) — store.update_layout 과 같은 틀: manifest_lock → 디스크 최신본 → expect_rev 가 지금 rev 와 다르면
    RefsConflict(None = 보지 않음) → mutate(doc)(제자리 수정) → validate_refs(구조 오류면 ValueError, 저장 안 함) → 바뀌었으면 이전 판을
    이력으로 남기고 새 rev = 지금까지 쓴 가장 큰 rev(refs_history/max_rev.json·이력 r<n>) + 1 로 원자 저장. 반환 (mutate 반환값, rev).
    mutate 가 예외를 내면 저장하지 않는다. 파일이 없으면 RefsConflict.
    create=True: 파일이 이미 있으면 RefsConflict, mutate 가 빈 dict 를 채우고 rev = 지금까지 쓴 가장 큰 rev + 1(처음이면 0)."""
    path = os.path.join(root, REFS_NAME)
    folder = os.path.join(root, REFS_HISTORY_DIR)
    with store.manifest_lock(root):
        if create:
            if os.path.isfile(path):
                raise RefsConflict("레퍼런스 연결 파일이 이미 있습니다 — 새로고침")
            rev = store._layout_rev_max(folder) + 1
            doc: dict = {}
            result = mutate(doc)
            doc["rev"] = rev
            validate_refs(doc)
            store.write_json_atomic(os.path.join(folder, store._LAYOUT_REV_MAX), {"rev": rev})
            store.write_json_atomic(path, doc)
            return result, rev
        doc = load_refs(root)
        if doc is None:
            raise RefsConflict("레퍼런스 연결 파일이 없습니다 — 새로고침")
        rev = doc["rev"]
        if expect_rev is not None and expect_rev != rev:
            raise RefsConflict(f"레퍼런스가 다른 창에서 바뀌었습니다(rev {expect_rev} → {rev}) — 새로고침")
        before = store._jsonable(doc)
        result = mutate(doc)
        doc["rev"] = rev
        validate_refs(doc)
        if store._jsonable(doc) != before:
            rev = max(_keep_history(root, before), store._layout_rev_max(folder)) + 1
            doc["rev"] = rev
            store.write_json_atomic(os.path.join(folder, store._LAYOUT_REV_MAX), {"rev": rev})  # 먼저 — 문서 쓰기가 실패해도 번호만 건너뜀
            store.write_json_atomic(path, doc)
    return result, rev


def refs_sig(doc: dict | None, refs_dir: Any) -> str | None:
    """매니페스트 source.refs_links 값(§1.6): links 의 (id, main, files) 를 ID 순으로 + refs_dir(정규화) 의 sha256 앞 16자.
    문서가 없거나 doc.refs_dir ≠ refs_dir 이면 None(그때 Import 는 이름 매칭이고 연결은 무시된다)."""
    if doc is None or not same_refs_dir(doc["refs_dir"], refs_dir):
        return None
    rows = [[cid, ln.get("main"), list(ln["files"])] for cid, ln in sorted(doc["links"].items())]
    blob = json.dumps([os.path.normcase(norm_refs_dir(refs_dir)), rows], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _name_matches(refs_dir: Any, crop: dict) -> list[str]:
    """Import 와 같은 이름 매칭: match_refs(refs_dir, nnn, part) — nnn·part 는 크롭 이름을 parse_crop_layer_name 으로(매니페스트 nnn·part 와 같은 값)."""
    p = store.parse_crop_layer_name(crop["name"])
    return store.match_refs(refs_dir, p["nnn"], part=p["part"]) if refs_dir and p["nnn"] else []


def seed_link(refs_dir: Any, crop: dict) -> dict:
    """고정 값(D1): files = 지금 이름 매칭 결과(match_refs 순서 그대로), main = prompt.auto_pick_ref(크롭 이름, files)(files 가 비면 None)
    — Prepare 가 지금 고르는 파일과 같아 고정해도 cell_key·초안 키가 바뀌지 않는다. 그래서 목록을 자르지 않는다: 이름 매칭이
    MAX_LINK_FILES 를 넘을 때만(드묾) 앞 MAX_LINK_FILES 개 + 경고 로그 — 대표가 그 밖이면 마지막 자리를 대표로 바꿔 Prepare 선택은 지키고,
    refs_auto·초안 키는 바뀐다(I3 의 유일한 예외, 빠진 파일은 resolve 의 '연결 안 된 파일'로 보임)."""
    files = _name_matches(refs_dir, crop)
    main = prompt.auto_pick_ref(str(crop["name"]), files) if files else None
    if len(files) > MAX_LINK_FILES:
        logger.warning("%s 크롭 %s 이름 매칭 레퍼런스 %d개 — 앞 %d개만 연결(대표 %s 유지)", _TAG, crop["name"], len(files), MAX_LINK_FILES, main)
        files = files[:MAX_LINK_FILES]
        if main not in files:
            files[-1] = main
    return {"files": files, "main": main}


def fix_link(doc: dict, refs_dir: Any, crop: dict, why: str) -> dict:
    """크롭 연결을 고정한다(update_refs mutate 안에서 쓰는 순수 함수): 이미 links 에 있으면 그대로 돌려주고, 없으면
    seed_link(why "none" 이면 {files: [], main: null} — 레퍼런스 없음으로 고정) + fixed {at, why, name: 지금 크롭 이름} 을 넣는다."""
    if why not in FIX_WHY:
        raise ValueError(f"고정 이유는 {FIX_WHY} 중 하나여야 합니다: {why!r}")
    ln = doc["links"].get(crop["id"])
    if ln is None:
        ln = {"main": None, "files": []} if why == "none" else seed_link(refs_dir, crop)
        ln["fixed"] = {"at": _now(), "why": why, "name": crop["name"]}
        doc["links"][crop["id"]] = ln
    return ln


def resolve_refs(refs_dir: Any, crop: dict, doc: dict | None) -> dict:
    """Import 가 크롭마다 부르는 연결 해석(§1.3). crop = {"id", "name", …}.
    - 문서 없음 · doc.refs_dir ≠ refs_dir · crop.id 가 links 에 없음 → refs_auto = 이름 매칭(지금과 같음), refs_main None, linked False.
      refs_dir 불일치인데 이 크롭이 연결돼 있으면 notes 한 줄.
    - 연결됨 → refs_auto = files 중 지금 refs_dir 바로 아래에 있는 것(순서 유지), refs_main = main 이 그 안에 있으면 그것 아니면 None
      (→ Prepare 가 이름 규칙으로 고름) + notes. 같은 번호인데 files 에 없는 refs_dir 파일은 unlinked·notes 로만(넣지 않음 — 고정의 뜻).
    반환 {"refs_auto", "refs_main", "linked", "notes", "missing"(연결됐는데 없는 파일), "unlinked"}."""
    link = doc["links"].get(crop["id"]) if doc is not None and same_refs_dir(doc["refs_dir"], refs_dir) else None
    if link is None:
        notes = []
        if doc is not None and crop["id"] in doc["links"]:
            notes.append(f"refs.json 의 refs_dir({doc['refs_dir']})가 지금 refs_dir 와 달라 연결을 무시하고 이름으로 찾음")
        return {"refs_auto": _name_matches(refs_dir, crop), "refs_main": None, "linked": False, "notes": notes,
                "missing": [], "unlinked": []}
    here = [f for f in link["files"] if os.path.isfile(os.path.join(refs_dir, f))]
    missing = [f for f in link["files"] if f not in here]
    main = link.get("main") if link.get("main") in here else None
    known = {f.casefold() for f in link["files"]}
    unlinked = [f for f in _name_matches(refs_dir, crop) if f.casefold() not in known]
    notes = [f"연결된 레퍼런스 파일이 없음: {f}" for f in missing]
    if link.get("main") and main is None:
        notes.append(f"대표 파일 없음: {link['main']} — Prepare 가 이름 규칙으로 고름")
    if unlinked:
        notes.append(f"연결 안 된 파일 {len(unlinked)}개(Studio 에서 연결): {', '.join(unlinked)}")
    return {"refs_auto": here, "refs_main": main, "linked": True, "notes": notes, "missing": missing, "unlinked": unlinked}


def explicit_refs(root: str, m: dict) -> dict[str, list[tuple[str, str, str]]]:
    """스펙(specs/*.json targets[].refs)·매니페스트(crops[].targets[].refs)가 이름으로 직접 가리키는 파일(D5 — 옮기거나 그 이름에 새 그림을
    쓰면 확인). 경로 해석은 bmk_design_patch_store._spec_files_fp·Prepare 와 같다: 절대 경로는 그대로, 아니면 매니페스트 source.refs_dir 기준.
    반환 {ref_key(refs_dir 기준 상대 경로): [(crop_id, tid, "manifest"|"spec"), …]} — refs_dir 밖(다른 드라이브 포함)은 뺀다.
    refs_dir 가 비면 {}. 깨진 스펙 파일은 건너뛴다(그 파일 자체는 Project 지문이 본다)."""
    refs_dir = (m.get("source") or {}).get("refs_dir") or ""
    if not refs_dir:
        return {}
    base = os.path.normcase(os.path.abspath(refs_dir))
    out: dict[str, list[tuple[str, str, str]]] = {}

    def add(r, cid, tid, where):
        if not isinstance(r, str) or not r:
            return
        p = os.path.normcase(os.path.abspath(r if os.path.isabs(r) else os.path.join(refs_dir, r)))
        try:
            rel = os.path.relpath(p, base)
        except ValueError:  # 다른 드라이브
            return
        if rel == ".." or rel.startswith(".." + os.sep) or os.path.isabs(rel):
            return
        row = (str(cid), str(tid), where)
        hits = out.setdefault(ref_key(rel), [])
        if row not in hits:
            hits.append(row)

    for c in m.get("crops") or []:
        for t in c.get("targets") or []:
            for r in t.get("refs") or []:
                add(r, c.get("id"), t.get("tid"), "manifest")
    sd = os.path.join(root, "specs")
    for fn in sorted(os.listdir(sd)) if os.path.isdir(sd) else []:
        if not fn.endswith(".json"):
            continue
        try:
            ov = store.load_spec_override(root, fn[:-5])
        except ValueError:
            continue
        ts = ov.get("targets")  # 사람이 고친 스펙: 목록이 아닌 targets·refs 는 가리키는 이름 없음(store._spec_files_fp 와 같게)
        for t in ts if isinstance(ts, list) else []:
            if isinstance(t, dict) and isinstance(t.get("refs"), list):
                for r in t["refs"]:
                    add(r, fn[:-5], t.get("id"), "spec")
    return out


# ══════════════════════════════════════════════════════════════════════
# 시트
# ══════════════════════════════════════════════════════════════════════
def _sheet_header(path: str) -> tuple[int, int]:
    """(W, H) — 헤더만 읽는다(PSD/PSB 는 앞 26바이트, 그 밖은 PIL 지연 열기). 읽을 수 없으면 ValueError."""
    if path.lower().endswith((".psd", ".psb")):
        with open(path, "rb") as f:
            head = f.read(26)
        if len(head) < 26 or head[:4] != b"8BPS" or head[4:6] not in (b"\x00\x01", b"\x00\x02"):
            raise ValueError(f"PSD/PSB 헤더가 아닙니다: {path}")
        h, w = struct.unpack(">II", head[14:22])
        return w, h
    try:
        with Image.open(path) as im:
            return im.size
    except (OSError, Image.DecompressionBombError) as e:
        raise ValueError(f"이미지를 열 수 없습니다: {path} ({e})") from e


def _sheet_kind(name: str) -> str:
    return "psd" if name.lower().endswith((".psd", ".psb")) else "image"


def _sheet_skips(m: dict) -> tuple[str, str]:
    """시트 목록에서 뺄 것(normcase 절대 경로, 없으면 ""): 매니페스트 source.refs_dir(그 바로 아래 = 레퍼런스 자체)·source.psd(작업 PSD)."""
    src = m.get("source") or {}
    return tuple(os.path.normcase(os.path.abspath(str(v))) if v else "" for v in (src.get("refs_dir"), src.get("psd")))


def _hidden_dir(name: str) -> bool:
    """어느 깊이든 들어가지 않는 폴더: 이름이 '_'·'.' 로 시작(_prep_runs·_보류·.git 같은 작업·보관 폴더)."""
    return name.startswith(("_", "."))


def list_sheets(root: str, m: dict) -> list[dict]:
    """프로젝트 폴더 안의 시트 후보(§5.2): 폴더 깊이 ≤ 3(루트 바로 아래 파일 = 0), 루트 바로 아래 SHEET_EXCLUDE 폴더(대소문자 무시)·
    어느 깊이든 이름이 '_'·'.' 로 시작하는 폴더·refs_dir 바로 아래·작업 PSD(매니페스트 source.psd) 제외, 확장자 SHEET_EXTS,
    짧은 변 ≥ 256(헤더만 읽음), 이름순 깊이 우선으로 최대 200개. m = 매니페스트(source.refs_dir·source.psd 만 읽음).
    반환 [{"path": 루트 기준 '/' 상대 경로, "size": [W, H], "kind": "image"|"psd", "bytes"}]."""
    out: list[dict] = []
    refs_dir, work_psd = _sheet_skips(m)

    def walk(folder: str, parts: list[str]) -> None:
        try:
            names = sorted(os.listdir(folder))
        except OSError:
            return
        in_refs = os.path.normcase(folder) == refs_dir
        for fn in names:
            if len(out) >= SHEET_LIST_MAX:
                return
            full = os.path.join(folder, fn)
            if os.path.isdir(full):
                if len(parts) < SHEET_MAX_DEPTH and not _hidden_dir(fn) and not (not parts and fn.casefold() in SHEET_EXCLUDE):
                    walk(full, parts + [fn])
            elif not in_refs and os.path.splitext(fn)[1].lower() in SHEET_EXTS and os.path.normcase(full) != work_psd:
                try:
                    w, h = _sheet_header(full)
                except (OSError, ValueError):
                    continue
                if min(w, h) >= SHEET_MIN_SIDE:
                    out.append({"path": "/".join(parts + [fn]), "size": [w, h], "kind": _sheet_kind(fn),
                                "bytes": os.path.getsize(full)})

    walk(os.path.abspath(root), [])
    return out


def sheet_check(root: str, m: dict, rel: Any) -> dict:
    """시트 경로를 list_sheets 와 같은 규칙으로 검사(라우트·컷 요청 — m = 매니페스트). 절대 경로·'..'·빈 값 = ValueError(400), 목록에 들 수
    없는 경로(없음·깊이·제외 폴더·'_'·'.' 폴더·refs_dir·작업 PSD·확장자·짧은 변) = FileNotFoundError(404). 반환 {"path": 정규화 상대 경로,
    "file": 절대 경로, "size": [W, H], "kind", "bytes"}."""
    if not isinstance(rel, str):
        raise ValueError(f"시트 경로는 문자열이어야 합니다: {rel!r}")
    parts = store._norm_relparts(rel, "시트 경로")
    path = store.resolve_path(root, rel)
    refs_dir, work_psd = _sheet_skips(m)
    miss = FileNotFoundError(f"시트 목록에 없는 경로입니다: {rel!r}")
    if (len(parts) > SHEET_MAX_DEPTH + 1 or (len(parts) > 1 and parts[0].casefold() in SHEET_EXCLUDE)
            or any(_hidden_dir(p) for p in parts[:-1]) or os.path.splitext(parts[-1])[1].lower() not in SHEET_EXTS
            or os.path.normcase(os.path.dirname(path)) == refs_dir or os.path.normcase(path) == work_psd or not os.path.isfile(path)):
        raise miss
    try:
        w, h = _sheet_header(path)
    except (OSError, ValueError):
        raise miss from None
    if min(w, h) < SHEET_MIN_SIDE:
        raise miss
    return {"path": "/".join(parts), "file": path, "size": [w, h], "kind": _sheet_kind(parts[-1]), "bytes": os.path.getsize(path)}


def sheet_sig(root: str, rel: str, layer: str | None = None) -> str:
    """시트 캐시 서명(sha1 앞 16자): 정규화 상대 경로 · 파일 지문 [mtime_ns, size] · 레이어 · 캐시 판(_SHEET_RENDER).
    파일이 바뀌면 새 서명(새 캐시)."""
    rel_n = "/".join(store._norm_relparts(rel, "시트 경로"))
    fp = store.file_fingerprint(store.resolve_path(root, rel_n))
    return hashlib.sha1(json.dumps([rel_n, fp, layer, _SHEET_RENDER], ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


_DEFAULT_RANGE = (0, 65535)   # 혼합 조건(Blend If) 범위의 기본값 — 실파일 1385 레이어 전부 이 값(10/16 읽기 전용 표본)


def _node_extras(node, label: str, out: list[str]) -> None:
    """수동 합성(opacity·user mask 만 곱함)이 경고 없이 틀리게 그리는 노드 속성: 칠 불투명도(Fill < 255 — opacity 만 곱함), 켜진 벡터 마스크
    (_mask_window 는 user mask 만 읽음), 혼합 조건(Blend If), 마스크 농도·페더(Masks 패널). psd_tools 레코드만 본다(픽셀 디코드 없음)."""
    if node.fill_opacity < 255:
        out.append(f"칠 불투명도(Fill) {node.fill_opacity}/255 무시: {label}")
    if node.has_vector_mask() and node.vector_mask is not None and not node.vector_mask.disabled:
        out.append(f"벡터 마스크 무시: {label}")
    br = node._record.blending_ranges
    ranges = list(br.composite_ranges or []) + [r for ch in (br.channel_ranges or []) for r in ch]
    if any(tuple(r) != _DEFAULT_RANGE for r in ranges):
        out.append(f"혼합 조건(Blend If) 무시: {label}")
    md = node._record.mask_data
    par = getattr(md, "parameters", None) if md is not None else None
    if (par is not None and node.has_mask() and node.mask is not None and not node.mask.disabled
            and (par.user_mask_density not in (None, 255) or par.user_mask_feather not in (None, 0))):
        out.append(f"마스크 농도·페더 무시: {label}")


def _psd_lossy(group, path: str, lossy: list[str], guides: list) -> None:
    """수동 합성(dpsd._composite_children — normal blend·opacity·user mask)이 Photoshop 과 다르게 그리는 보이는 노드를 픽셀 디코드 없이 센다.
    _composite_children 이 경고하는 것 전부(비-normal blend·클리핑·fx·shape·조정/채우기·그룹 blend)와 경고 없이 틀리는 것(그룹 fx·클리핑,
    _node_extras)을 lossy 에(문구), 수동 합성이 통째로 빼는 가이드 이름 그룹은 guides 에 (label, 노드) 로 — 그 안은 보지 않는다.
    _composite_children 의 경고 문구(크롭 소스와 같이 씀)에 기대지 않는다. 그 함수보다 넓게 잡아도 되는 쪽이다(병합 이미지 = Photoshop 렌더)."""
    for child in group:
        if not child.visible:
            continue
        label = (path + "/" + child.name) if path else child.name
        if child.is_group():
            if dpsd._GUIDE_RE.search(child.name or ""):
                guides.append((label, child))
                continue
            if child.blend_mode not in (dpsd.BlendMode.PASS_THROUGH, dpsd.BlendMode.NORMAL):
                lossy.append(f"그룹 blend {child.blend_mode.name}: {label}")
            if child.clipping:
                lossy.append(f"클리핑 그룹: {label}")
            if child.has_effects():
                lossy.append(f"그룹 레이어 효과(fx): {label}")
            _node_extras(child, label, lossy)
            _psd_lossy(child, label, lossy, guides)
            continue
        kind = child.kind
        if kind == "shape" or kind not in dpsd._RENDER_KINDS:
            lossy.append(f"{kind} 레이어: {label}")
            continue
        if child.blend_mode != dpsd.BlendMode.NORMAL:
            lossy.append(f"blend {child.blend_mode.name}: {label}")
        if child.clipping:
            lossy.append(f"클리핑 레이어: {label}")
        if child.has_effects():
            lossy.append(f"레이어 효과(fx): {label}")
        _node_extras(child, label, lossy)


def _draws(group) -> bool:
    """보이는 그룹 안에 그림을 바꾸는 보이는 노드가 있는가(빈 픽셀 레이어만 있으면 False — 병합 이미지에 아무것도 남기지 않음)."""
    for c in group:
        if not c.visible:
            continue
        if c.is_group():
            if _draws(c):
                return True
        elif c.kind not in dpsd._RENDER_KINDS or (c.width > 0 and c.height > 0):
            return True
    return False


def _psd_rgba(path: str, layer: str | None) -> np.ndarray:
    """PSD/PSB 풀캔버스 RGBA. layer None = 보이는 레이어 수동 합성(psd._composite_children — normal blend, 가이드 이름 그룹 제외).
    수동 합성이 Photoshop 과 다르게 그리는 문서(_psd_lossy — blend·클리핑·fx·shape·조정/채우기·칠 불투명도·벡터 마스크·혼합 조건·마스크
    농도/페더, 그룹 포함)와 레이어가 없는(평탄화) 문서는 파일의 병합 이미지(psd.topil — Photoshop 렌더 그대로)를 쓴다. 판정은 레코드만 보고
    먼저 하며, 병합 이미지를 쓰면 수동 합성은 돌리지 않는다(3053×4067 실파일 37s → 1.3s, 그동안 _REF_SLOTS·_SHEET_LOCK 을 잡음).
    병합 이미지에는 보이는 가이드 그룹이 구워져 있다 — 그래도 병합 이미지를 쓰고(수동 합성은 클리핑·MULTIPLY 음영을 덮개로 그려 그림 전체가
    틀림) 경고 로그로 알린다(빼려면 Photoshop 에서 가이드 그룹을 숨기고 저장). 병합 이미지가 없으면(호환성 최대화 끔) 수동 합성 그대로 +
    경고 로그, 레이어도 없으면 ValueError. psd_tools 자체 합성(composite)은 6000×7000 실측 3분이라 쓰지 않는다.
    layer = 그 이름의 노드 하나만(숨김이어도)."""
    psd = dpsd.open_psd(path)
    W, H = psd.size
    full = (0, 0, W, H)
    warns: list[str] = []
    if layer is None:
        lossy: list[str] = []
        guides: list = []
        _psd_lossy(psd, "", lossy, guides)
        if lossy or not len(psd):
            merged = psd.topil()
            if merged is not None:
                name = os.path.basename(path)
                logger.info("%s 시트 %s: 병합 이미지 사용(%s)", _TAG, name,
                            f"수동 합성이 다르게 그리는 요소 {len(lossy)}건 — {lossy[0]}" if lossy else "레이어 없음")
                shown = [label for label, g in guides if _draws(g)]
                if shown:
                    logger.warning("%s 시트 %s: 병합 이미지(Photoshop 렌더)에 보이는 가이드 그룹이 들어 있음(%s) — 시트·컷에 가이드가 "
                                   "보입니다. 빼려면 Photoshop 에서 그 그룹을 숨기고 저장하세요", _TAG, name, ", ".join(shown))
                if merged.mode != "RGBA":
                    merged = merged.convert("RGBA")
                return np.asarray(merged)
            if not len(psd):
                raise ValueError(f"PSD 에 레이어도 병합 이미지도 없습니다: {os.path.basename(path)}")
            logger.warning("%s 시트 %s: 병합 이미지가 없어 blend·클리핑 등을 normal 로 근사 합성(%d건 — %s) — Photoshop 에서 "
                           "'호환성 최대화'로 다시 저장하세요", _TAG, os.path.basename(path), len(lossy), lossy[0])
        rgba = dpsd._composite_children(psd, full, warns)
    else:
        node = next((n for n in psd.descendants() if n.name == layer), None)
        if node is None:
            raise ValueError(f"PSD 에 그 이름의 레이어가 없습니다: {layer!r}")
        if node.is_group():
            rgba = dpsd._apply_node_alpha(node, dpsd._composite_children(node, full, warns), full)
        else:
            rgba = dpsd._layer_window(node, full)
            rgba = np.zeros((H, W, 4), np.uint8) if rgba is None else rgba
    for w in warns:
        logger.info("%s 시트 합성 %s: %s", _TAG, os.path.basename(path), w)
    return rgba


def _build_sheet(path: str, layer: str | None, npy: str) -> None:
    """시트 → RGBA .npy(임시 파일 → os.replace). 이미지는 디코드한 뒤 512행씩 RGBA 로 바꿔 써서 RGBA 사본을 통째로 만들지 않는다."""
    tmp = f"{npy}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp"
    try:
        with open(tmp, "wb") as f:
            if path.lower().endswith((".psd", ".psb")):
                np.save(f, np.ascontiguousarray(_psd_rgba(path, layer)))
            else:
                if layer is not None:
                    raise ValueError("layer 는 PSD 시트에만 쓸 수 있습니다.")
                with Image.open(path) as im:
                    im.load()
                    W, H = im.size
                    np.lib.format.write_array_header_1_0(f, {"descr": "|u1", "fortran_order": False, "shape": (H, W, 4)})
                    for y in range(0, H, 512):
                        s = im.crop((0, y, W, min(H, y + 512)))
                        f.write(np.asarray(s if s.mode == "RGBA" else s.convert("RGBA")).tobytes())
        os.replace(tmp, npy)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _prune_sheets(folder: str) -> None:
    """캐시 .npy 가 SHEET_CACHE_KEEP 개를 넘으면 mtime 이 오래된 것부터 .npy·_prev.jpg 를 지운다(열린 파일은 다음 정리로)."""
    rows = sorted((os.stat(os.path.join(folder, fn)).st_mtime_ns, fn) for fn in os.listdir(folder) if fn.endswith(".npy"))
    for _t, fn in rows[:-SHEET_CACHE_KEEP]:
        for victim in (fn, fn[:-4] + "_prev.jpg"):
            try:
                os.remove(os.path.join(folder, victim))
            except FileNotFoundError:
                pass
            except OSError as e:
                logger.info("%s 시트 캐시 %s 를 지우지 못함(다음 정리로): %s", _TAG, victim, e)


def _sheets_folder(root: str) -> str:
    return os.path.join(root, *SHEETS_DIR.split("/"))


def _sheet_npy(root: str, rel: str, layer: str | None) -> tuple[str, str]:
    """(sheet_sig, 그 판의 캐시 .npy 경로) — 서명은 잠금 안에서 잡고 그 서명의 캐시만 돌려준다(미리보기 이름도 이 서명으로).
    캐시가 없으면 만들고, 만든 뒤 서명이 달라졌으면(읽는 동안 Photoshop 이 저장) 옛 서명 이름에 새 픽셀이 들었을 수 있어 지우고
    새 판으로 다시 만든다 — 3번 다 바뀌면 ValueError."""
    folder = _sheets_folder(root)
    with _SHEET_LOCK:
        for _ in range(3):
            sig = sheet_sig(root, rel, layer)
            npy = os.path.join(folder, f"{sig}.npy")
            if os.path.isfile(npy):
                try:
                    os.utime(npy)
                except OSError:
                    pass
                return sig, npy
            os.makedirs(folder, exist_ok=True)
            _build_sheet(store.resolve_path(root, rel), layer, npy)
            if sheet_sig(root, rel, layer) == sig:
                _prune_sheets(folder)
                return sig, npy
            os.remove(npy)
    raise ValueError(f"시트를 읽는 동안 파일이 계속 바뀝니다 — 저장이 끝난 뒤 다시 시도하세요: {rel}")


def sheet_array(root: str, rel: str, layer: str | None = None) -> np.memmap:
    """시트 RGBA(H, W, 4) uint8 읽기 전용 memmap. 캐시 derived/sheets/<sheet_sig>.npy 가 없으면 만들고(PSD = 보이는 레이어 합성 —
    읽는 동안 파일이 바뀌면 다시, _sheet_npy), 있으면 mtime 을 갱신한다(최근 사용). 만든 뒤 캐시가 4개를 넘으면 오래된 것부터 지운다.
    경로 검사는 store.resolve_path(프로젝트 밖 금지)뿐 — 목록 규칙은 부르는 쪽이 sheet_check 로."""
    return np.load(_sheet_npy(root, rel, layer)[1], mmap_mode="r")


def _jpg_on_white(rgba: np.ndarray, max_side: int, quality: int) -> bytes:
    """RGBA 배열 → 긴 변 ≤ max_side(LANCZOS, PIL 이 프리멀티로 줄임) → 흰 바탕 합성 → JPEG bytes."""
    im = Image.fromarray(np.ascontiguousarray(rgba))
    W, H = im.size
    if max(W, H) > max_side:
        s = max_side / max(W, H)
        im = im.resize((max(1, int(W * s + 0.5)), max(1, int(H * s + 0.5))), Image.LANCZOS)
    bg = Image.new("RGB", im.size, (255, 255, 255))
    bg.paste(im, mask=im.getchannel("A"))
    buf = io.BytesIO()
    bg.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def sheet_preview(root: str, rel: str, layer: str | None = None) -> tuple[str, str]:
    """(시트 미리보기 JPEG 경로, sig) — 긴 변 2048, 흰 바탕, q85, 캐시 derived/sheets/<sig>_prev.jpg, sig = 읽은 .npy 의 서명(보드 ETag·v —
    부르기 전에 잰 서명이면 읽는 사이 저장된 새 그림이 옛 v 로 캐시됨, codex9). 보드는 시트 원크기로 늘여 놓는다."""
    sig, npy = _sheet_npy(root, rel, layer)
    jpg = os.path.join(_sheets_folder(root), f"{sig}_prev.jpg")
    if not os.path.isfile(jpg):
        store._atomic_write_bytes(jpg, _jpg_on_white(np.load(npy, mmap_mode="r"), SHEET_PREVIEW, 85))
    return jpg, sig


def _check_sheet_rect(rect: Any, size: Iterable[int], what: str = "rect") -> list[int]:
    W, H = size
    x0, y0, x1, y1 = _rect4(rect, what)
    if x0 < 0 or y0 < 0 or x1 > W or y1 > H:
        raise ValueError(f"{what} 가 시트({W}×{H}) 밖입니다: {rect}")
    if (x1 - x0) * (y1 - y0) > MAX_RECT_AREA:
        raise ValueError(f"{what} 넓이가 너무 큽니다(≤ 8192² px): {rect}")
    return rect


def sheet_region(root: str, rel: str, rect: Any, max_side: Any, layer: str | None = None) -> tuple[bytes, str]:
    """(시트의 rect(정수, 시트 안, 넓이 ≤ 8192²)를 원해상도로 잘라 긴 변 ≤ max_side(1–2048)로 줄인 흰 바탕 JPEG q90 bytes, 그 픽셀의 sig)."""
    sig, npy = _sheet_npy(root, rel, layer)
    arr = np.load(npy, mmap_mode="r")
    x0, y0, x1, y1 = _check_sheet_rect(rect, (arr.shape[1], arr.shape[0]))
    return _jpg_on_white(arr[y0:y1, x0:x1], _int(max_side, "max", 1, 2048), 90), sig


# ══════════════════════════════════════════════════════════════════════
# 컷 — 창·후처리
# ══════════════════════════════════════════════════════════════════════
def cut_window(arr: np.ndarray, rect: list[int], pad_frac: float) -> tuple[np.ndarray, np.ndarray, list[int]]:
    """창 = rect 를 변마다 (폭·높이 × pad_frac) 만큼 넓혀 반올림(round)하고 시트 안으로 자른 것(U0 도구 _expand 와 같음).
    반환 (흰 바탕 RGB HxWx3 uint8 — (rgb·a + 255·(255−a) + 127) // 255, 시트 알파 HxW uint8, 창 [x0, y0, x1, y1])."""
    H, W = arr.shape[:2]
    x0, y0, x1, y1 = rect
    dx, dy = (x1 - x0) * pad_frac, (y1 - y0) * pad_frac
    win = [max(0, int(round(x0 - dx))), max(0, int(round(y0 - dy))), min(W, int(round(x1 + dx))), min(H, int(round(y1 + dy)))]
    sub = np.ascontiguousarray(arr[win[1]:win[3], win[0]:win[2]])
    return prompt.flatten_ref(sub, (255, 255, 255)), sub[..., 3].copy(), win


def auto_point(alpha_bool: np.ndarray, rect_in_window: Iterable[int]) -> tuple[int, int, int] | None:
    """SAM 자동 양점(§3.2): 상자(창 좌표) 가운데에 가장 가까운 불투명 픽셀 (x, y, 1). 상자 안이 모두 투명이면 None."""
    x0, y0, x1, y1 = rect_in_window
    ys, xs = np.nonzero(alpha_bool[y0:y1, x0:x1])
    if not len(xs):
        return None
    i = int(np.argmin((xs + x0 - (x0 + x1) / 2) ** 2 + (ys + y0 - (y0 + y1) / 2) ** 2))
    return int(xs[i] + x0), int(ys[i] + y0), 1


def _drop_small(m: np.ndarray, frac: float) -> np.ndarray:
    """8-연결 조각 중 넓이가 전체 조각 넓이 합의 frac 미만인 것을 버린다(조각이 하나 이하면 그대로)."""
    n, lab, st, _ = cv2.connectedComponentsWithStats(m.astype(np.uint8), connectivity=8)
    if n <= 2 or frac <= 0:
        return m
    keep = np.zeros(n, bool)
    keep[1:] = st[1:, cv2.CC_STAT_AREA] >= frac * st[1:, cv2.CC_STAT_AREA].sum()
    return keep[lab]


def _poly_cover(shape: tuple[int, int], pts: np.ndarray) -> np.ndarray:
    """다각형 덮임 float32 0..1(창 좌표, 픽셀 i 는 [i, i+1)): bbox 를 4배 초표본으로 채워(LINE_8, 픽셀 가운데 규칙) 면적 평균.
    bbox 가 2048² 를 넘으면 초표본 없이 LINE_8. cv2 채우기는 경계를 표본 가운데로 반올림한다(딱 반이면 오른쪽·아래를 넣음 — ≤ ¼px).
    cv2 LINE_AA 채우기는 경계 밖으로 약 0.7px 번지고 아래로 약 1px 치우쳐(실측) 쓰지 않는다."""
    h, w = shape
    out = np.zeros(shape, np.float32)
    x0, y0 = max(0, math.floor(pts[:, 0].min())), max(0, math.floor(pts[:, 1].min()))
    x1, y1 = min(w, math.ceil(pts[:, 0].max())), min(h, math.ceil(pts[:, 1].max()))
    if x1 <= x0 or y1 <= y0:
        return out
    s = 4 if (x1 - x0) * (y1 - y0) <= 2048 * 2048 else 1
    hi = np.zeros(((y1 - y0) * s, (x1 - x0) * s), np.uint8)
    q = np.round(((pts - (x0, y0)) * s - 0.5) * 16).astype(np.int32)  # cv2 정수 좌표 = 픽셀 가운데, 1/16 px
    cv2.fillPoly(hi, [q], 255, lineType=cv2.LINE_8, shift=4)
    if s > 1:
        hi = cv2.resize(hi.astype(np.float32), (x1 - x0, y1 - y0), interpolation=cv2.INTER_AREA)
    out[y0:y1, x0:x1] = hi / 255.0
    return out


def raster_polys(shape: tuple[int, int], origin: Iterable[float], polys: list[dict], base: np.ndarray | None = None) -> np.ndarray:
    """다각형 더하기/빼기 → float32 HxW 0..1. base(없으면 0)에서 시작해 차례대로 add = max(m, 덮임), sub = min(m, 1 − 덮임).
    덮임 = 4배 초표본 면적 평균(_poly_cover). 좌표는 시트 px 연속 좌표(픽셀 i 는 [i, i+1)), origin = 창 왼쪽 위 [x0, y0]."""
    m = np.zeros(shape, np.float32) if base is None else np.array(base, np.float32)
    for p in polys:
        c = _poly_cover(shape, np.asarray(p["pts"], np.float64) - (float(origin[0]), float(origin[1])))
        m = np.maximum(m, c) if p["op"] == "add" else np.minimum(m, 1.0 - c)
    return m


def finish_mask(raw_bool_window: np.ndarray | None, req: dict, window: list[int], sheet_alpha_window: np.ndarray,
                base_mask: np.ndarray | None = None) -> tuple[np.ndarray, dict]:
    """컷 후처리 → (선택 마스크 float32 HxW 0..1, stats). 다듬기(grow·feather)·× 시트 알파 전 — 모듈 docstring '컷 계약'.
    base_mask 가 없으면 raw(method rect = 전부, poly = 없음, sam3 = raw_bool_window)의 rect 밖 0 → 작은 조각 버림(req drop_small),
    있으면(앞 컷 다듬기) 그 선택 그대로 → req polys 더하기/빼기. stats = {"area_frac", "n_parts"}."""
    h, w = sheet_alpha_window.shape
    if [window[2] - window[0], window[3] - window[1]] != [w, h]:
        raise ValueError(f"창 {window} 와 시트 알파 크기 {w}×{h} 가 다릅니다.")
    x0, y0, x1, y1 = (req["rect"][0] - window[0], req["rect"][1] - window[1], req["rect"][2] - window[0], req["rect"][3] - window[1])
    if base_mask is not None:
        sel = np.array(base_mask, np.float32)
    else:
        if req["method"] == "sam3":
            raw = np.asarray(raw_bool_window, bool)
        else:
            raw = np.full((h, w), req["method"] == "rect", bool)
        m = np.zeros((h, w), bool)
        m[y0:y1, x0:x1] = raw[y0:y1, x0:x1]
        sel = _drop_small(m, req["drop_small"]).astype(np.float32)
    if sel.shape != (h, w):
        raise ValueError(f"마스크 크기 {sel.shape} 가 창 {w}×{h} 와 다릅니다.")
    sel = raster_polys((h, w), window[:2], req["polys"], base=sel)
    a = sheet_alpha_window.astype(np.float32) / 255.0
    n_parts = cv2.connectedComponents(((sel >= 0.5) & (a > 0)).astype(np.uint8), connectivity=8)[0] - 1
    return sel, {"area_frac": round(float((sel * a).sum()) / ((x1 - x0) * (y1 - y0)), 4), "n_parts": int(n_parts)}


def merge_alpha(sheet_alpha: np.ndarray, mask: np.ndarray, grow: int, feather: float) -> np.ndarray:
    """최종 알파 uint8 = 시트 알파 × (mask → grow(정사각 커널 (2|g|+1)², +확장/−축소) → 가우시안 σ=feather(반지름 ceil(3σ))).
    순서는 SAM3 Mask Layers(_grow → _blur)와 같고, 창 밖은 선택 아님(0) — 사각형 방법(pad 0)은 선택이 창 가득이라 창 가장자리가
    선택 경계다(가장자리 복제면 축소·페더가 듣지 않음, codex7)."""
    m = np.asarray(mask, np.float32)
    if grow:
        k = np.ones((2 * abs(grow) + 1, 2 * abs(grow) + 1), np.uint8)
        m = (cv2.dilate if grow > 0 else cv2.erode)(m, k, borderType=cv2.BORDER_CONSTANT, borderValue=0)
    if feather > 0:
        r = max(1, math.ceil(feather * 3))
        m = cv2.GaussianBlur(m, (2 * r + 1, 2 * r + 1), feather, borderType=cv2.BORDER_CONSTANT)
    return np.floor(np.clip(m, 0.0, 1.0) * sheet_alpha.astype(np.float32) + 0.5).astype(np.uint8)


# ══════════════════════════════════════════════════════════════════════
# 컷 — 요청·결과 파일
# ══════════════════════════════════════════════════════════════════════
def _cut_path(root: str, req: Any, suffix: str) -> str:
    if not isinstance(req, str) or not _REQ_RE.fullmatch(req):
        raise ValueError(f"컷 요청 ID 형식이 잘못되었습니다([0-9a-f] 32자): {req!r}")
    return os.path.join(root, *CUT_DIR.split("/"), req + suffix)


def _read_cut(root: str, req: Any, suffix: str, what: str) -> Any:
    path = _cut_path(root, req, suffix)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"{what} 파일이 없습니다: {req}")
    return store._read_json(path, what)


def _check_cut(root: str, m: dict, body: Any) -> dict:
    """컷 요청 본문 검증·정규화(§5.5) — 규칙은 모듈 docstring '컷 계약' new_cut_request."""
    if not isinstance(body, dict):
        raise ValueError("컷 요청 본문이 객체가 아닙니다.")
    unknown = sorted(k for k in body if k not in _CUT_BODY_KEYS and k not in ("root", "sheet_v"))
    if unknown:
        raise ValueError(f"컷 요청에 모르는 키가 있습니다: {unknown}")
    sh = sheet_check(root, m, body.get("sheet"))
    fp = store.file_fingerprint(sh["file"])
    W, H = sh["size"]
    layer = body.get("layer")
    if layer is not None and (sh["kind"] != "psd" or not isinstance(layer, str) or not 1 <= len(layer) <= 255):
        raise ValueError(f"layer 는 PSD 시트의 레이어 이름(1–255자)이어야 합니다: {layer!r}")
    # 서명은 fp 뒤에 잰다 — 그 사이 저장되면 409, 그 뒤 저장은 요청의 sheet_fp 가 apply_cut·load_cut_request 에서 막음.
    # 사각형·base 검사(새 크기·새 지문)보다 먼저: 원인이 시트 저장이면 400 이 아니라 409(화면이 시트를 다시 엶)
    if body.get("sheet_v") is not None and body["sheet_v"] != sheet_sig(root, sh["path"], layer):
        raise SheetChanged("시트가 바깥에서 바뀌었습니다 — 시트를 다시 엽니다")
    rect = _check_sheet_rect(body.get("rect"), (W, H))
    x0, y0, x1, y1 = rect
    if min(x1 - x0, y1 - y0) < 16:
        raise ValueError(f"선택 사각형의 짧은 변은 16px 이상이어야 합니다: {rect}")
    method = body.get("method")
    if method not in CUT_METHODS:
        raise ValueError(f"method 는 {CUT_METHODS} 중 하나여야 합니다: {method!r}")
    sam = method == "sam3"

    points = body.get("points") or []
    if not isinstance(points, list) or len(points) > 32 or (points and not sam):
        raise ValueError("points 는 sam3 에서만, 32개 이하 [x, y, 0|1] 목록이어야 합니다.")
    pts = []
    for p in points:
        if not isinstance(p, list) or len(p) != 3 or not _is_int(p[2]) or p[2] not in (0, 1):
            raise ValueError(f"점은 [x, y, 0|1] 이어야 합니다: {p!r}")
        _num(p[0], "점 x", x0, x1)
        _num(p[1], "점 y", y0, y1)
        pts.append([p[0], p[1], p[2]])
    text = body.get("text") or ""
    if not isinstance(text, str) or len(text) > 80 or any(not " " <= c <= "~" for c in text) or (text.strip() and not sam):
        raise ValueError("text 는 sam3 에서만, 80자 이하 ASCII 여야 합니다.")
    ckpt = body.get("ckpt")
    if ckpt is not None and (not sam or not isinstance(ckpt, str) or not 1 <= len(ckpt) <= 255 or ".." in ckpt
                             or os.path.isabs(ckpt) or any(unicodedata.category(c) == "Cc" for c in ckpt)):
        raise ValueError(f"ckpt 는 sam3 에서만, 체크포인트 이름(경로·'..' 금지)이어야 합니다: {ckpt!r}")
    polys = body.get("polys") or []
    if not isinstance(polys, list) or len(polys) > 16:
        raise ValueError("polys 는 16개 이하 목록이어야 합니다.")
    pl = []
    for p in polys:
        if (not isinstance(p, dict) or p.get("op") not in ("add", "sub") or not isinstance(p.get("pts"), list)
                or not 3 <= len(p["pts"]) <= 256):
            raise ValueError(f"다각형은 {{op: add|sub, pts: 꼭짓점 3–256개}} 여야 합니다: {str(p)[:80]}")
        for q in p["pts"]:
            if not isinstance(q, list) or len(q) != 2:
                raise ValueError(f"다각형 꼭짓점은 [x, y] 여야 합니다: {q!r}")
            _num(q[0], "다각형 x", 0, W)
            _num(q[1], "다각형 y", 0, H)
        pl.append({"op": p["op"], "pts": [[q[0], q[1]] for q in p["pts"]]})

    base = body.get("base")
    if base is not None:
        if method != "poly":
            raise ValueError("base(앞 컷 다듬기)는 method poly 에서만 씁니다.")
        breq = _read_cut(root, base, ".req.json", "base 컷 요청")
        bres = load_cut_result(root, base)
        if not bres or not bres.get("ok"):
            raise ValueError(f"base 컷이 끝나지 않았거나 실패했습니다: {base}")
        if [breq.get(k) for k in ("sheet", "layer", "rect", "sheet_fp")] != [sh["path"], layer, rect, fp]:
            raise ValueError(f"base 컷의 시트·레이어·사각형이 이 요청과 다릅니다: {base}")
        pad = breq.get("pad")
        if "pad" in body and body["pad"] != pad:
            raise ValueError(f"base 가 있으면 창(pad)은 base 것({pad})을 씁니다: {body['pad']!r}")
    else:
        if method == "poly" and not any(p["op"] == "add" for p in pl):
            raise ValueError("다각형 방법에 더하기 다각형이 없습니다.")
        pad = _num(body.get("pad", SAM_DEFAULTS["pad"] if sam else 0.0), "pad", 0, 0.5)
    return {"sheet": sh["path"], "layer": layer, "sheet_fp": fp, "sheet_size": [W, H], "rect": rect, "method": method,
            "points": pts, "text": text.strip(), "polys": pl, "base": base, "pad": pad,
            "refine": _int(body.get("refine", SAM_DEFAULTS["refine"]), "refine", 0, 5),
            "drop_small": _num(body.get("drop_small", SAM_DEFAULTS["drop_small"]), "drop_small", 0, 0.5),
            "grow": _int(body.get("grow", 0), "grow", -64, 64), "feather": _num(body.get("feather", 0), "feather", 0, 64),
            "ckpt": ckpt, "multimask": SAM_DEFAULTS["multimask"]}


def new_cut_request(root: str, m: dict, body: Any) -> dict:
    """컷 요청 만들기(§5.5): 본문 검증(시트 = 매니페스트 source.refs_dir 기준 목록 규칙) → req = uuid4 hex → derived/refcut/<req>.req.json
    원자 저장 → 정규화한 요청 dict(모듈 docstring '컷 계약'). 오래된 요청 정리(prune_cuts)는 부르는 쪽이 한다. sheet_v 가 지금 시트
    서명과 다르면 SheetChanged(요청 파일을 쓰지 않음)."""
    r = _check_cut(root, m, body)
    req = uuid.uuid4().hex
    doc = {"schema": CUT_SCHEMA, "req": req, "at": _now(), **r}
    store.write_json_atomic(_cut_path(root, req, ".req.json"), doc)
    return doc


def load_cut_request(root: str, req: Any) -> dict:
    """<req>.req.json 을 읽어 new_cut_request 와 같은 규칙으로 다시 검증한다(사람이 고칠 수 있는 파일 — 노드가 실행 직전에).
    req 형식이 틀리면 ValueError, 파일이 없으면 FileNotFoundError, 시트 지문이 요청 때와 다르면 ValueError."""
    doc = _read_cut(root, req, ".req.json", "컷 요청")
    if not isinstance(doc, dict) or doc.get("schema") != CUT_SCHEMA or doc.get("req") != req:
        raise ValueError(f"컷 요청 파일 형식이 잘못되었습니다: {req}")
    r = _check_cut(root, store.load_manifest(root), {k: doc[k] for k in _CUT_BODY_KEYS if k in doc})
    if doc.get("sheet_fp") != r["sheet_fp"]:
        raise ValueError(f"시트 파일이 컷 요청 뒤에 바뀌었습니다 — 다시 컷: {r['sheet']}")
    return {"schema": CUT_SCHEMA, "req": req, "at": doc.get("at"), **r}


def save_cut_result(root: str, req: str, sel: np.ndarray, info: dict) -> dict:
    """컷 결과 저장: <req>_mask.png(L, 창 크기 — 선택 마스크 0..1 을 0..255 로) → <req>.res.json(원자, 있음 = 끝남).
    info = {window(필수), rect, area_frac, n_parts, sec, ckpt, warnings, …}. 반환 = res dict."""
    a = np.asarray(sel)
    if a.ndim != 2:
        raise ValueError(f"마스크는 HxW 여야 합니다: {a.shape}")
    win = info["window"]
    if [win[2] - win[0], win[3] - win[1]] != [a.shape[1], a.shape[0]]:
        raise ValueError(f"마스크 크기 {a.shape[1]}×{a.shape[0]} 가 창 {win} 와 다릅니다.")
    if a.dtype == np.bool_:
        a = a.astype(np.uint8) * 255
    elif a.dtype != np.uint8:
        a = np.floor(np.clip(a.astype(np.float32), 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)
    im = Image.fromarray(np.ascontiguousarray(a))
    buf = io.BytesIO()
    im.save(buf, format="PNG", compress_level=4)
    store._atomic_write_bytes(_cut_path(root, req, "_mask.png"), buf.getvalue())
    res = {"ok": True, "req": req, "at": _now(), "sec": None, "ckpt": None, "warnings": [], **info,
           "mask": f"{CUT_DIR}/{req}_mask.png", "mask_sha": store.pixel_sha(im)}
    store.write_json_atomic(_cut_path(root, req, ".res.json"), res)
    return res


def save_cut_error(root: str, req: str, message: Any) -> None:
    """실패 결과 <req>.res.json {ok: false, req, at, error} (노드는 쓰고 나서 예외를 다시 올린다 — ComfyUI 에도 보이게)."""
    store.write_json_atomic(_cut_path(root, req, ".res.json"),
                            {"ok": False, "req": req, "at": _now(), "error": str(message)[:2000]})


def load_cut_result(root: str, req: Any) -> dict | None:
    """<req>.res.json — 없으면 None(대기 중), 깨졌으면 ValueError."""
    try:
        res = _read_cut(root, req, ".res.json", "컷 결과")
    except FileNotFoundError:
        return None
    if not isinstance(res, dict) or res.get("req") != req:
        raise ValueError(f"컷 결과 파일 형식이 잘못되었습니다: {req}")
    return res


def load_cut_mask(root: str, req: Any) -> np.ndarray:
    """<req>_mask.png → float32 HxW 0..1(선택 마스크 — finish_mask 의 base_mask)."""
    path = _cut_path(root, req, "_mask.png")
    if not os.path.isfile(path):
        raise FileNotFoundError(f"컷 마스크 파일이 없습니다: {req}")
    with Image.open(path) as im:
        return np.asarray(im.convert("L"), np.float32) / 255.0


def prune_cuts(root: str, keep: Iterable[Any] = ()) -> int:
    """derived/refcut 정리: 요청(파일 묶음 = <req>.req.json·.res.json·_mask.png)의 가장 새 mtime 기준으로 7일이 넘었거나 최근 200개 밖이면
    지운다. keep = 32자 req 가 든 문자열들(refs.json files[].cut.mask 경로 그대로) — 그 요청은 남긴다. 반환 = 지운 파일 수."""
    folder = os.path.join(root, *CUT_DIR.split("/"))
    if not os.path.isdir(folder):
        return 0
    keep_ids = {mt.group(0) for k in keep for mt in [_REQ_RE.search(str(k))] if mt}
    groups: dict[str, list[str]] = {}
    newest: dict[str, float] = {}
    for fn in os.listdir(folder):
        mt = _CUT_FILE_RE.fullmatch(fn)
        if mt:
            groups.setdefault(mt.group(1), []).append(fn)
            newest[mt.group(1)] = max(newest.get(mt.group(1), 0.0), os.path.getmtime(os.path.join(folder, fn)))
    cutoff = time.time() - CUT_KEEP_DAYS * 86400
    removed = 0
    for i, r in enumerate(sorted(groups, key=lambda k: newest[k], reverse=True)):
        if r in keep_ids or (i < CUT_KEEP_N and newest[r] >= cutoff):
            continue
        for fn in groups[r]:
            try:
                os.remove(os.path.join(folder, fn))
                removed += 1
            except OSError as e:
                logger.info("%s 컷 파일 %s 를 지우지 못함: %s", _TAG, fn, e)
    return removed


def apply_cut(root: str, req: dict, res: dict | None) -> np.ndarray:
    """저장 전 RGBA(변환 전, 창 크기): 시트 창 RGB + 알파 merge_alpha(시트 알파, 선택 마스크, req grow, req feather).
    끝나지 않았거나 실패한 컷·시트 지문이 요청 때와 다르면 ValueError."""
    if not res or not res.get("ok"):
        raise ValueError(f"끝나지 않았거나 실패한 컷입니다: {req.get('req')}")
    arr = sheet_array(root, req["sheet"], req["layer"])
    if store.file_fingerprint(store.resolve_path(root, req["sheet"])) != req["sheet_fp"]:  # 읽은 뒤에 본다(읽기 전이면 그 사이 저장을 놓침)
        raise ValueError(f"시트 파일이 컷 뒤에 바뀌었습니다 — 다시 컷: {req['sheet']}")
    x0, y0, x1, y1 = res["window"]
    win = np.array(arr[y0:y1, x0:x1])
    sel = load_cut_mask(root, req["req"])
    if sel.shape != win.shape[:2]:
        raise ValueError(f"컷 마스크 크기 {sel.shape} 가 창 {res['window']} 와 다릅니다.")
    win[..., 3] = merge_alpha(win[..., 3], sel, req["grow"], req["feather"])
    return win


# ══════════════════════════════════════════════════════════════════════
# 변환
# ══════════════════════════════════════════════════════════════════════
def _rotate_premul(f: np.ndarray, deg: float) -> np.ndarray:
    """프리멀티 float RGBA 를 deg(양수 = 반시계) 회전 — 캔버스 = 회전한 사각형의 bbox(ceil), INTER_LINEAR, 바깥 0."""
    h, w = f.shape[:2]
    th = math.radians(deg)
    c, s = abs(math.cos(th)), abs(math.sin(th))
    W, H = math.ceil(w * c + h * s - 1e-6), math.ceil(w * s + h * c - 1e-6)
    M = cv2.getRotationMatrix2D(((w - 1) / 2, (h - 1) / 2), deg, 1.0)
    M[0, 2] += (W - w) / 2
    M[1, 2] += (H - h) / 2
    return cv2.warpAffine(f, M, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)


def transform_rgba(rgba: np.ndarray, rotate: float, flip_h: bool, scale: float, pad: int) -> np.ndarray:
    """저장 전 변환(§1.2 순서): 회전(양수 = 반시계) → 좌우 반전 → 배율(≤ 1) → 다듬기(알파 > 0 bbox + 변마다 pad 투명).
    90° 배수·배율 1 은 np.rot90·뒤집기만(바이트 그대로). 그 밖은 프리멀티 알파 float 로 회전(INTER_LINEAR)·축소(INTER_AREA)한 뒤
    되돌린다(반투명 가장자리 색 유지 — 검은 띠 없음). 값 검사는 check_transform. 모두 투명이면 ValueError."""
    t = check_transform({"rotate": rotate, "flip_h": flip_h, "scale": scale, "pad": pad})
    a = np.asarray(rgba)
    if a.dtype != np.uint8 or a.ndim != 3 or a.shape[2] != 4:
        raise ValueError(f"변환 입력은 HxWx4 uint8 이어야 합니다: {a.shape} {a.dtype}")
    r, k = t["rotate"] % 360, int(t["rotate"] % 360 // 90)
    if r % 90 == 0 and t["scale"] == 1:
        out = np.rot90(a, k)
        out = out[:, ::-1] if t["flip_h"] else out
    else:
        f = a.astype(np.float32)
        f[..., :3] *= f[..., 3:4] / 255.0
        f = np.ascontiguousarray(np.rot90(f, k)) if r % 90 == 0 else _rotate_premul(f, r)
        f = np.ascontiguousarray(f[:, ::-1]) if t["flip_h"] else f
        if t["scale"] < 1:
            h, w = f.shape[:2]
            f = cv2.resize(f, (max(1, int(w * t["scale"] + 0.5)), max(1, int(h * t["scale"] + 0.5))), interpolation=cv2.INTER_AREA)
        A = np.clip(f[..., 3], 0.0, 255.0)
        out = np.zeros(f.shape, np.uint8)
        out[..., 3] = np.floor(A + 0.5)
        rgb = f[..., :3] * (255.0 / np.maximum(A, 1e-6))[..., None]
        out[..., :3] = np.where(out[..., 3:4] > 0, np.floor(np.clip(rgb, 0.0, 255.0) + 0.5), 0)
    alpha = out[..., 3]
    ys, xs = np.flatnonzero(alpha.any(axis=1)), np.flatnonzero(alpha.any(axis=0))
    if not len(ys):
        raise ValueError("변환 결과가 모두 투명합니다(빈 컷).")
    out = out[ys[0]:ys[-1] + 1, xs[0]:xs[-1] + 1]
    p = t["pad"]
    return np.ascontiguousarray(np.pad(out, ((p, p), (p, p), (0, 0))) if p else out)


# ══════════════════════════════════════════════════════════════════════
# 파일 동작 (덮어쓰지 않고 지우지 않는다 — D5)
# ══════════════════════════════════════════════════════════════════════
def _move_excl(src: str, dst: str) -> None:
    """dst 가 없을 때만 옮긴다(있으면 FileExistsError). Windows os.rename 은 덮어쓰지 않고, 그 밖은 하드 링크 + 원본 지우기."""
    if os.name == "nt":
        os.rename(src, dst)
    else:
        os.link(src, dst)
        os.unlink(src)


def same_name(folder: str, name: str) -> str | None:
    """folder 바로 아래에서 name 과 같은 이름(ref_key — NFC·대소문자 무관)인 실제 이름 | None. NTFS 는 NFC·NFD 를 다른 이름으로 보므로
    os.path.lexists 로는 macOS 등에서 온 NFD 이름을 놓친다(match_refs·보드는 그 파일을 같은 이름으로 봄 — 눈으로 같은 이름 둘이 생김)."""
    names = os.listdir(folder)
    if name in names:
        return name
    k = ref_key(name)
    return next((n for n in names if ref_key(n) == k), None)


def save_ref(refs_dir: str, name: str, rgba: np.ndarray) -> str:
    """refs_dir/<name>(.png) 에 RGBA PNG 를 새로 만든다 — 같은 이름(same_name — NFD 이름 포함)이 있으면 FileExistsError(덮어쓰기 0).
    같은 폴더 임시 파일('.' 시작 — match_refs 가 보지 않음)에 쓰고 fsync 뒤 배타 이름 바꾸기. 반환 = 만든 파일 경로."""
    check_ref_name(name)
    if not name.lower().endswith(".png"):
        raise ValueError(f"S2 가 저장하는 레퍼런스는 .png 여야 합니다: {name!r}")
    if not refs_dir or not os.path.isdir(refs_dir):
        raise ValueError(f"레퍼런스 폴더가 없습니다: {refs_dir!r}")
    a = np.asarray(rgba)
    if a.dtype != np.uint8 or a.ndim != 3 or a.shape[2] != 4:
        raise ValueError(f"레퍼런스 그림은 HxWx4 uint8 이어야 합니다: {a.shape} {a.dtype}")
    path = os.path.join(refs_dir, name)
    old = same_name(refs_dir, name)
    if old is not None:
        raise FileExistsError(f"같은 이름의 레퍼런스가 이미 있습니다(덮어쓰지 않음): {old}")
    buf = io.BytesIO()
    Image.fromarray(np.ascontiguousarray(a)).save(buf, format="PNG")
    tmp = os.path.join(refs_dir, f".{uuid.uuid4().hex[:12]}.tmp")
    try:
        with open(tmp, "wb") as f:
            f.write(buf.getvalue())
            f.flush()
            os.fsync(f.fileno())
        _move_excl(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    return path


def hold_file(refs_dir: str, name: str) -> str:
    """refs_dir/<name> 을 refs_dir/_보류/ 로 옮긴다(되살리기 가능). 보류에 같은 이름(same_name)이 있으면 "<stem>__YYMMDD-HHMMSS<ext>"
    (그것도 있으면 -2, -3 …). 반환 = 보류 안의 파일 이름(refs.json files 키는 "_보류/<그 이름>"). 파일이 없으면 FileNotFoundError."""
    check_ref_name(name)
    src = os.path.join(refs_dir, name)
    if not os.path.isfile(src):
        raise FileNotFoundError(f"레퍼런스 파일이 없습니다: {name}")
    held_dir = os.path.join(refs_dir, HELD_DIR)
    os.makedirs(held_dir, exist_ok=True)
    stem, ext = os.path.splitext(name)
    cand = name
    if same_name(held_dir, cand) is not None:
        ts = time.strftime("%y%m%d-%H%M%S")
        cand, k = f"{stem}__{ts}{ext}", 2
        while same_name(held_dir, cand) is not None:
            cand, k = f"{stem}__{ts}-{k}{ext}", k + 1
    _move_excl(src, os.path.join(held_dir, cand))
    return cand


def restore_file(refs_dir: str, held: str, name: str | None = None) -> str:
    """refs_dir/_보류/<held> 를 refs_dir/<name> 으로 되살린다. name = 보류할 때의 원래 이름(refs.json held 기록 — 보드가 넘김), 없으면 held
    그대로(사람이 옮긴 파일 — 이름에서 접미를 짐작하지 않음: 원래 이름이 "__YYMMDD-HHMMSS" 꼴일 수 있음). 그 이름(same_name — NFD 이름 포함)이
    refs_dir 에 있으면 FileExistsError(409), 보류 파일이 없으면 FileNotFoundError. 이미지(REF_EXTS)만. 반환 = 되살린 이름."""
    check_ref_name(held)
    name = check_ref_name(held if name is None else name)
    src = os.path.join(refs_dir, HELD_DIR, held)
    if not os.path.isfile(src):
        raise FileNotFoundError(f"보류 파일이 없습니다: {held}")
    dst = os.path.join(refs_dir, name)
    old = same_name(refs_dir, name)
    if old is not None:
        raise FileExistsError(f"refs 에 같은 이름의 파일이 있어 되살릴 수 없습니다: {old}")
    _move_excl(src, dst)
    return name
