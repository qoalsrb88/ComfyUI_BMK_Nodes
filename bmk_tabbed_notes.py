# BMK Tabbed Notes (v9 — 노트 데이터를 패키지 밖으로 분리)
#
# 각 노트(탭)는 "하나의 이미지 생성 스펙"을 담는 구조화 문서(JSON)다.
# 카테고리 폴더 안에 "탭.json" 파일 하나가 문서 한 개에 대응한다.
#
# v9: 노트 데이터 루트가 패키지 안(custom_nodes/ComfyUI_BMK_Nodes/notes/)에서
#     ComfyUI/user/bmk_notes/ 로 옮겨졌다.
#   - 노드 소스와 사용자 데이터를 분리해, 저장소를 공개해도 개인 노트가
#     함께 딸려가지 않고 노드를 업데이트/재설치해도 노트가 남는다.
#   - 위치 해석은 3단계다: BMK_NOTES_DIR 환경변수 → ComfyUI user 디렉터리
#     (--user-directory를 쓰면 그쪽을 따라간다) → 구 위치 폴백(ComfyUI 밖).
#     노트를 별도 private 저장소에 두고 여러 머신에서 공유하려면
#     BMK_NOTES_DIR로 가리키면 된다.
#   - 기존 설치본의 notes/는 첫 임포트 때 _migrate_legacy_notes()가 통째로
#     옮긴다(새 위치에 이미 데이터가 있으면 건드리지 않고 안내만 출력).
#   - 저장 포맷·API·프론트엔드는 그대로 — js는 경로를 모르고 HTTP로만 통신한다.
#
# v8: 문서 ui에 "md"(마크다운 프리뷰로 볼 섹션, {key: true})가 추가됐다.
#   - 프론트(v7 js)의 Notes 섹션 프리뷰 토글 상태를 노트별로 보존하기 위한
#     표시 메타다. 렌더링은 전적으로 프론트엔드가 수행하며(자체 미니 렌더러),
#     서버는 order/collapsed와 동일하게 플래그를 정규화·보존만 한다.
#   - 실행/포트/txt 미러에는 영향 없음 — notes 원문은 마크다운 소스 그대로
#     저장되고, txt 미러도 원문을 충실히 미러링한다.
#   ⚠ 배포 주의: v8 py는 v7 js와 짝 배포할 것. 구버전 프론트의 normalizeUi는
#     ui.md를 깎아내므로, 구버전 UI로 노트를 편집·저장하면 프리뷰 상태만
#     소실된다(문서 내용은 안전).
#
# v7: 출력 포트(positive/negative/lora/active_tab_name)를 제거했다.
#   - 값 전달은 전송 패널·Params의 "무선 전송"(대상 노드 위젯 직접 주입)으로
#     완전히 대체돼, 실사용이 없던 유선 포트를 노드 공간 절약을 위해 삭제.
#   - 출력이 없고 OUTPUT_NODE도 아니므로 큐 실행 그래프에서 자연히 제외된다.
#     이에 따라 실행 경로(get_active/IS_CHANGED)도 함께 제거 — 노드는 이제
#     notes_data(선택 상태)만 워크플로우에 저장하는 순수 UI 노드다.
#   - 새 탭 생성 시 기본 섹션 표시 순서를 문서 ui.order로 부여한다:
#     prompt → negative → loras → params → notes (params를 LoRA 바로 아래에).
#     정본 순서(_SECTION_KEYS)와 달라 명시 저장되며, 기존 문서는 건드리지 않는다.
#   - 구버전 워크플로우가 직렬화해 둔 출력 슬롯은 프론트(v6 js)가 로드 시 걷어낸다.
#   ⚠ 배포 주의: v7 py는 v6 js와 짝 배포할 것.
#
# v6: 문서에 "params"(워크플로우 파라미터 목록)가 추가됐다.
#   - 각 항목은 워크플로우 내 특정 노드의 특정 위젯 값 하나를 기록한다:
#       {"label": "모델1 - 기본생성", "node": "10941", "widget": "unet_name",
#        "type": "combo", "value": "...", "enabled": true,
#        "hint": "Load Diffusion Model"}
#     · label  = 사람용 제목 / node = 대상 노드 ID(문자열 보관)
#     · widget = 대상 위젯 name / type = combo|int|float|string|bool 등(표시용)
#     · value  = JSON 스칼라(문자열/숫자/불리언) / enabled = 일괄 전송·복사 포함 여부
#     · hint   = 대상 노드 타이틀(워크플로우가 달라졌을 때의 불일치 감지용 표시 정보)
#   - params는 포트로 출력되지 않으며(전송은 프론트가 위젯에 직접 주입),
#     IS_CHANGED 해시에도 포함되지 않는다 → params만 고쳐도 노드 재실행 없음.
#   - ui와 마찬가지로 "있을 때만" 문서에 기록된다(구버전 파일은 그대로 유지).
#   - 파라미터 프리셋은 노트 루트의 .bmk_param_presets.json(dot파일 — 트리
#     스캔에서 무시됨)에 저장되고, 모든 트리 응답에 "param_presets"로 동봉돼
#     페이지 내 모든 노드가 자동 동기화된다(구버전 프론트는 무시).
#     op: save_param_preset(name, params — 동명 upsert) / delete_param_preset(name)
#   ⚠ 배포 주의: v6 py와 v6 js는 반드시 함께 배포할 것. 구버전 js의
#     normalizeDoc은 params를 깎아내므로, 구버전 UI로 노트를 편집·저장하면
#     params가 소실된다.
#
# v5: 카테고리는 폴더 계층 그대로 "무제한 중첩"할 수 있다.
#   - 카테고리 식별자는 이름이 아니라 루트 기준 경로(구분자 '/')다.
#     예) "artstyle/anime/cel shading"
#     op의 category/old/name/from_category/to_category 파라미터 모두 경로를 받는다.
#     (깊이 1 경로 == 기존 이름이므로 구버전 플랫 요청과 그대로 호환)
#   - 한 카테고리에 하위 카테고리와 탭이 함께 있을 수 있다.
#   - 부모가 다르면 같은 이름의 카테고리도 허용된다(경로로 식별).
#   - 깊이 제한은 없으나 Windows MAX_PATH 보호를 위해 절대경로가
#     MAX_ABS_PATH(240자)를 넘는 생성/이동은 오류로 차단한다.
#   - 메타(.bmk_meta.json)는 categories[].children으로 중첩을 표현하며,
#     구버전 플랫 메타는 그대로 깊이 1 트리로 읽힌다(마이그레이션 불필요).
#   - 트리 응답의 각 카테고리에 path("a/b/c")와 children이 추가된다.
#     (구버전 프론트는 두 필드를 무시 → 루트 카테고리만 표시하며 정상 동작)
#
#   ComfyUI/user/bmk_notes/                   ← v9: 패키지 밖 (NOTES_DIR)
#   ├── .bmk_meta.json   ← 카테고리/탭 순서, 접힘 상태 (children으로 중첩)
#   ├── .trash/          ← 삭제된 노트 보관 (안전망)
#   ├── <카테고리>/.../<탭>.json   ← 실제 문서 (이것이 정본)
#   └── <카테고리>/.../<탭>.txt    ← 사람용 읽기 사본 (json→txt 단방향 미러)
#
# .txt는 ComfyUI를 켜지 않은 상태에서 prompt/negative/lora/notes를 에디터로
# 바로 복사하기 위한 것이다. json 저장 시마다 자동 생성/갱신되며, txt를 직접
# 수정해도 json에는 반영되지 않는다(단방향). 트리 스캔 시 누락분도 백필된다.
#
# 문서 스키마 (모든 키 선택적 — 없으면 빈 값):
#   {
#     "prompt":   "...",                 # 긍정 프롬프트 (기본)
#     "negative": "...",                 # 네거티브 프롬프트
#     "loras":    [ {"name": "NikkeB1", "weight": 1.0, "enabled": true}, ... ],
#     "notes":    "...",                 # 메모/번역 등 (포트 출력 없음)
#     "params":   [ {"label","node","widget","type","value","enabled","hint"}, ... ]
#   }
#
# 출력 포트: 없음 (v7) — 값 전달은 프론트엔드가 대상 노드 위젯에 직접 주입한다.
#   prompt/negative는 전송 시점에 //주석 줄이 제거되고(JS stripComments),
#   lora는 enabled 항목만 "<lora:이름:가중치>, ..."로 컴파일된다(txt 미러와 동일 규칙).
#
# 프론트엔드는 아래 HTTP API로 통신한다:
#   GET  /bmk/notes/tree  → 전체 트리(문서 포함)
#   POST /bmk/notes/op    → {op, ...} 구조 변경/저장 작업
from __future__ import annotations

import json
import math
import os
import re
import shutil
import threading
import time

_PKG_DIR = os.path.dirname(os.path.abspath(__file__))
# v9 이전의 데이터 위치 (패키지 안). 이제는 이관 원본으로만 쓰인다.
_LEGACY_NOTES_DIR = os.path.join(_PKG_DIR, "notes")


def _resolve_notes_dir():
    """노트 데이터 루트를 3단계로 해석한다.

    1) BMK_NOTES_DIR 환경변수 — 명시 오버라이드. 노트를 별도 private 저장소나
       동기화 폴더에 두고 여러 머신에서 공유할 때 쓴다(심볼릭 링크보다 안전).
    2) ComfyUI/user/bmk_notes — 기본값. user/는 ComfyUI가 업데이트·
       재설치에서 보존을 보장하는 영역이라 커스텀팩 데이터의 정석 위치다.
       main.py는 --user-directory를 반영한 뒤 커스텀 노드를 로드하므로
       임포트 시점에 확정해도 안전하다.
    3) 패키지 안 notes/ — ComfyUI 밖(단위 테스트 등)에서 임포트된 경우의 폴백.
    """
    env = os.environ.get("BMK_NOTES_DIR", "").strip()
    if env:
        return os.path.abspath(os.path.expanduser(env))
    try:
        import folder_paths
    except Exception:
        return _LEGACY_NOTES_DIR
    return os.path.join(folder_paths.get_user_directory(), "bmk_notes")


NOTES_DIR = _resolve_notes_dir()
META_PATH = os.path.join(NOTES_DIR, ".bmk_meta.json")
TRASH_DIR = os.path.join(NOTES_DIR, ".trash")
DOC_EXT = ".json"
TXT_EXT = ".txt"  # json에 미러링되는 사람용 읽기 전용 사본 (단방향: json→txt)
# v6: 워크플로우 파라미터 프리셋 저장 파일 (루트 dot파일 — 트리 스캔에서 무시됨)
PARAM_PRESETS_PATH = os.path.join(NOTES_DIR, ".bmk_param_presets.json")


def _migrate_legacy_notes():
    """구 위치(패키지 안 notes/)의 데이터를 새 위치로 1회 이관한다.

    새 위치에 이미 내용이 있으면 병합 판단이 필요하므로 건드리지 않고 안내만 한다.
    이관에 실패해도 노드 로드는 계속돼야 하므로 예외는 로그로만 남긴다.
    """
    if NOTES_DIR == _LEGACY_NOTES_DIR or not os.path.isdir(_LEGACY_NOTES_DIR):
        return
    try:
        legacy_items = os.listdir(_LEGACY_NOTES_DIR)
    except OSError:
        return

    if not legacy_items:  # 빈 껍데기만 남은 경우 — 조용히 정리
        try:
            os.rmdir(_LEGACY_NOTES_DIR)
        except OSError:
            pass
        return

    if os.path.isdir(NOTES_DIR) and os.listdir(NOTES_DIR):
        print("[BMK Tabbed Notes] 새 노트 폴더에 이미 데이터가 있어 자동 이관을 건너뜁니다. "
              f"직접 병합해주세요:\n  구 위치: {_LEGACY_NOTES_DIR}\n  새 위치: {NOTES_DIR}")
        return

    try:
        os.makedirs(NOTES_DIR, exist_ok=True)
        for name in legacy_items:
            shutil.move(os.path.join(_LEGACY_NOTES_DIR, name),
                        os.path.join(NOTES_DIR, name))
        os.rmdir(_LEGACY_NOTES_DIR)
        print(f"[BMK Tabbed Notes] 노트 데이터를 이관했습니다: {_LEGACY_NOTES_DIR} → {NOTES_DIR}")
    except Exception as e:
        print(f"[BMK Tabbed Notes] 노트 데이터 이관 실패 ({e}). 수동으로 옮겨주세요:\n"
              f"  {_LEGACY_NOTES_DIR} → {NOTES_DIR}")


_migrate_legacy_notes()

# Windows 기본 MAX_PATH(260) 대비 안전 여유. 중첩 깊이 자체는 제한하지 않되,
# 이 길이를 넘는 절대경로의 생성/이동은 명확한 오류로 사전 차단한다.
MAX_ABS_PATH = 240

_LOCK = threading.RLock()
_INVALID_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


class BMKNotesError(Exception):
    """클라이언트에 400으로 전달되는 사용자 오류."""


# ─── 이름/경로 안전화 ────────────────────────────────────────────

def _safe_name(name, fallback=""):
    name = _INVALID_CHARS.sub("_", str(name or ""))
    name = name.strip().rstrip(". ").lstrip(".").strip()
    return name or fallback


def _require_name(name, what):
    safe = _safe_name(name)
    if not safe:
        raise BMKNotesError(f"잘못된 {what} 이름입니다: {name!r}")
    return safe


def _inside_root(path):
    root = os.path.realpath(NOTES_DIR)
    return os.path.realpath(path).startswith(root + os.sep)


def _split_path(path, what="카테고리"):
    """'a/b/c' 경로(또는 세그먼트 리스트) → 안전화된 세그먼트 리스트.
    카테고리 이름에 슬래시가 올 수 없으므로(_INVALID_CHARS) 구분은 명확하다.
    빈 세그먼트(연속/말단 슬래시)는 무시하되, 안전화 후 비어버리는
    세그먼트('..' 등)는 오류 처리해 의도치 않은 경로 병합을 막는다."""
    if isinstance(path, (list, tuple)):
        raw = [str(x) for x in path]
    else:
        raw = re.split(r"[\\/]", str(path or ""))
    parts = []
    for seg in raw:
        if not str(seg).strip():
            continue
        safe = _safe_name(seg)
        if not safe:
            raise BMKNotesError(f"잘못된 {what} 이름입니다: {seg!r}")
        parts.append(safe)
    return parts


def _join_parts(parts):
    return "/".join(parts)


def _abs_dir(parts):
    """세그먼트 리스트 → notes/ 아래 절대 경로 (빈 리스트 = 루트)."""
    p = os.path.join(NOTES_DIR, *parts) if parts else NOTES_DIR
    if parts and not _inside_root(p):
        raise BMKNotesError("경로가 허용 범위를 벗어났습니다")
    return p


def _cat_dir(path):
    """카테고리 경로 → (절대 디렉터리, 정규화 경로 'a/b', 세그먼트 리스트).
    v5: 카테고리 식별자는 이름이 아니라 루트 기준 경로다."""
    parts = _split_path(path)
    if not parts:
        raise BMKNotesError(f"잘못된 카테고리 경로입니다: {path!r}")
    return _abs_dir(parts), _join_parts(parts), parts


def _parent_dir(path):
    """상위 카테고리 경로(빈 값/생략 = 루트) → (절대 디렉터리, 경로, 세그먼트)."""
    parts = _split_path(path) if str(path or "").strip() else []
    return _abs_dir(parts), _join_parts(parts), parts


def _tab_path(cat, tab):
    cdir, cpath, parts = _cat_dir(cat)
    tab = _require_name(tab, "탭")
    p = os.path.join(cdir, tab + DOC_EXT)
    if not _inside_root(p):
        raise BMKNotesError("경로가 허용 범위를 벗어났습니다")
    return p, cpath, parts, tab


def _check_path_len(abs_path, what):
    """Windows 기본 MAX_PATH(260) 초과를 사전 차단 — 무제한 중첩의 안전망.
    (초과 경로는 생성돼도 탐색기/외부 도구 호환 문제를 일으키므로 명확한 오류가 낫다)"""
    if len(abs_path) > MAX_ABS_PATH:
        raise BMKNotesError(
            f"{what} 경로가 너무 깁니다 ({len(abs_path)}자 > 최대 {MAX_ABS_PATH}자). "
            "이름을 줄이거나 계층 깊이를 줄여주세요"
        )


# ─── 문서 모델 ───────────────────────────────────────────────────

def _empty_doc():
    return {"prompt": "", "negative": "", "loras": [], "notes": ""}


# 섹션 표시 메타(ui)에서 다루는 섹션 key의 정본 순서.
# 출력 포트에는 영향을 주지 않으며 ComfyUI UI 표시(순서/접힘)에만 쓰인다.
# v6: "params" 섹션 추가 — 구버전 ui.order(4키)는 누락분 보충 규칙에 따라
# params가 맨 뒤에 자동으로 붙는다(마이그레이션 불필요).
_SECTION_KEYS = ("prompt", "negative", "loras", "notes", "params")

# v7: 새 탭에 부여하는 기본 섹션 표시 순서 — params를 LoRA 바로 아래에 둔다.
# 정본 순서(_SECTION_KEYS)와 다르므로 _normalize_ui가 ui.order로 문서에 남긴다.
# 기존 문서에는 소급 적용하지 않는다(새로 만드는 탭에만 부여).
_NEW_TAB_ORDER = ("prompt", "negative", "loras", "params", "notes")


def _new_tab_doc():
    """새 탭의 초기 문서: 빈 값 + 기본 섹션 순서(ui.order)."""
    d = _empty_doc()
    d["ui"] = {"order": list(_NEW_TAB_ORDER)}
    return d


def _normalize_ui(ui):
    """섹션 표시 메타(order/collapsed/md)를 표준화. 유효 정보가 없으면 None.
      - order: 섹션 key의 순열(중복/미지 key 제거 후 누락분을 기본순서로 보충).
               기본순서와 같으면 저장하지 않음(불필요한 dirty 방지).
      - collapsed: 알려진 key 중 접힌 것만 {key: true}로 보관.
      - md: 마크다운 프리뷰로 표시할 섹션만 {key: true}로 보관 (v8).
            렌더링은 프론트 전용 — 서버는 collapsed와 동일하게 보존만 한다.
    """
    if not isinstance(ui, dict):
        return None
    out = {}
    raw_order = ui.get("order")
    if isinstance(raw_order, list):
        seen = []
        for k in raw_order:
            if k in _SECTION_KEYS and k not in seen:
                seen.append(k)
        order = seen + [k for k in _SECTION_KEYS if k not in seen]
        if order != list(_SECTION_KEYS):
            out["order"] = order
    raw_col = ui.get("collapsed")
    if isinstance(raw_col, dict):
        collapsed = {k: True for k in _SECTION_KEYS if raw_col.get(k)}
        if collapsed:
            out["collapsed"] = collapsed
    raw_md = ui.get("md")
    if isinstance(raw_md, dict):
        md = {k: True for k in _SECTION_KEYS if raw_md.get(k)}
        if md:
            out["md"] = md
    return out or None


def _normalize_params(params):
    """워크플로우 파라미터 목록을 표준화. 유효 항목이 없으면 빈 리스트.

    항목 스키마: {label, node, widget, type, value, enabled, hint}
      - node는 문자열로 보관(숫자 입력도 str화). value는 JSON 스칼라만 허용
        (str/int/float/bool — 비유한 float와 그 외 타입은 ""로 대체).
      - type은 표시/렌더 힌트일 뿐이라 미지 값도 그대로 보존(소문자화만).
      - 편집 중인 미완성 행(위젯 미지정 등)도 보존한다 — 디바운스 저장이
        입력 도중 발화해도 행이 사라지면 안 된다. 단, 모든 식별 정보가
        비어 있는 완전 빈 행은 버린다.
    """
    out = []
    for it in (params or []):
        if not isinstance(it, dict):
            continue
        label = it["label"] if isinstance(it.get("label"), str) else ""
        node = it.get("node")
        if isinstance(node, bool):
            node = ""
        node = str(node).strip() if isinstance(node, (str, int)) else ""
        widget = it["widget"].strip() if isinstance(it.get("widget"), str) else ""
        ptype = it["type"].strip().lower() if isinstance(it.get("type"), str) else ""
        value = it.get("value")
        if isinstance(value, bool):
            pass
        elif isinstance(value, float):
            if not math.isfinite(value):
                value = ""
        elif isinstance(value, (int, str)):
            pass
        else:
            value = ""
        if not (label.strip() or node or widget or value != ""):
            continue  # 완전 빈 행(garbage)은 버림
        out.append({
            "label": label,
            "node": node,
            "widget": widget,
            "type": ptype or "string",
            "value": value,
            "enabled": bool(it.get("enabled", True)),
            "hint": it["hint"] if isinstance(it.get("hint"), str) else "",
        })
    return out


def _normalize_doc(d):
    """임의의 dict를 표준 문서 스키마로 정규화. 손상/누락 필드는 빈 값으로."""
    if not isinstance(d, dict):
        return _empty_doc()
    out = _empty_doc()
    out["prompt"] = d["prompt"] if isinstance(d.get("prompt"), str) else ""
    out["negative"] = d["negative"] if isinstance(d.get("negative"), str) else ""
    out["notes"] = d["notes"] if isinstance(d.get("notes"), str) else ""
    loras = []
    for it in (d.get("loras") or []):
        if not isinstance(it, dict):
            continue
        name = it.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        try:
            weight = float(it.get("weight", 1.0))
        except (TypeError, ValueError):
            weight = 1.0
        if not math.isfinite(weight):
            weight = 1.0
        loras.append({
            "name": name.strip(),
            "weight": weight,
            "enabled": bool(it.get("enabled", True)),
        })
    out["loras"] = loras
    ui = _normalize_ui(d.get("ui"))
    if ui:
        out["ui"] = ui  # UI 표시 메타(순서/접힘) — 있을 때만 보존
    params = _normalize_params(d.get("params"))
    if params:
        out["params"] = params  # v6: 워크플로우 파라미터 — 있을 때만 보존
    return out


def _lora_basename(name):
    """LoraManager는 loras 폴더의 서브폴더를 구분하지 않으므로,
    'Anima\\NikkeB1' 같은 경로에서 파일명만 남긴다 → 'NikkeB1'."""
    return re.split(r"[\\/]", str(name))[-1] or str(name)


def _compile_loras(loras):
    """enabled 로라만 LoraManager 붙여넣기용 문자열로 컴파일.
    (폴더 경로는 제거 — LoraManager는 서브폴더를 구분하지 않음)"""
    parts = []
    for it in loras or []:
        if not it.get("enabled", True):
            continue
        name = str(it.get("name", "")).strip()
        if not name:
            continue
        try:
            weight = float(it.get("weight", 1.0))
        except (TypeError, ValueError):
            weight = 1.0
        if not math.isfinite(weight):
            weight = 1.0
        parts.append(f"<lora:{_lora_basename(name)}:{weight:.2f}>")
    return ", ".join(parts)


# ─── 사람용 txt 미러 ─────────────────────────────────────────────
# json 옆에 같은 이름의 .txt를 두어, ComfyUI를 켜지 않은 상태에서도
# 텍스트 에디터로 prompt/negative/lora/notes를 바로 복사할 수 있게 한다.
# 단방향(json→txt)이므로 txt를 직접 고쳐도 json에는 반영되지 않는다.
# 주석(//)은 "저장 내용 그대로" 미러링한다(노드 텍스트칸에 보이는 것과 동일).
# 포트 출력은 _strip_comments로 //줄을 제거하지만, txt는 정본의 충실한 사본이다.

def _normalize_newlines(s):
    return str(s or "").replace("\r\n", "\n").replace("\r", "\n")


def _params_to_txt(params):
    """params를 사람용 한 줄 텍스트로 렌더링 (txt 미러 [Params] 섹션용).
    형식: '제목: 값 → #노드ID.위젯' — 비활성 행은 '(off) ' 접두.
    txt는 정본의 충실한 사본이므로 비활성 행도 표기해 남긴다."""
    lines = []
    for it in params or []:
        val = it.get("value", "")
        if isinstance(val, bool):
            val = "true" if val else "false"
        target = ""
        if it.get("node") or it.get("widget"):
            target = f" \u2192 #{it.get('node') or '?'}.{it.get('widget') or '?'}"
        prefix = "" if it.get("enabled", True) else "(off) "
        label = it.get("label") or it.get("widget") or "param"
        lines.append(f"{prefix}{label}: {val}{target}")
    return "\n".join(lines)


def _doc_to_txt(doc):
    """문서를 [Prompt]/[Negative]/[Lora]/[Notes]/[Params] 블록의 평문으로 렌더링.
    Lora는 포트 출력과 동일하게 enabled 항목만 <lora:이름:가중치>로 컴파일한다."""
    doc = _normalize_doc(doc)
    sections = [
        ("[Prompt]", _normalize_newlines(doc["prompt"])),
        ("[Negative]", _normalize_newlines(doc["negative"])),
        ("[Lora]", _compile_loras(doc["loras"])),
        ("[Notes]", _normalize_newlines(doc["notes"])),
        ("[Params]", _params_to_txt(doc.get("params"))),
    ]
    return "\n\n".join(f"{head}\n{body}" for head, body in sections) + "\n"


# ─── 주석(//) 처리 ───────────────────────────────────────────────

_COMMENT_RE = re.compile(r"^\s*//")


def _strip_comments(text):
    """줄의 첫 비공백 문자가 '//'인 줄(주석/라벨)을 통째로 제거.
    JS의 stripComments와 동일 규칙. v7부터 서버 실행 경로가 없어 여기서는
    호출되지 않지만, 전송 규칙의 정본 문서화·백엔드 테스트용으로 유지한다.
      - 줄 자체를 지우므로 주석이 남긴 빈 줄도 사라진다.
      - 인라인 '//'(줄 중간)는 보존 → URL/이스케이프/<lora:..>/(text:1.2) 안전.
      - 사용자가 직접 넣은 빈 줄은 보존.
    """
    if not text:
        return text
    out = [ln for ln in re.split(r"\r\n|\r|\n", str(text))
           if not _COMMENT_RE.match(ln)]
    return "\n".join(out)


# ─── 파일 IO ────────────────────────────────────────────────────

def _read_raw(path):
    """해시/감지용 원본 파일 문자열 (없으면 빈 문자열)."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace", newline="") as f:
            return f.read()
    except OSError:
        return ""


def _read_doc(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return _normalize_doc(json.load(f))
    except Exception:
        return _empty_doc()


def _txt_path_for(json_path):
    """'.../탭.json' → '.../탭.txt'."""
    if json_path.lower().endswith(DOC_EXT):
        return json_path[: -len(DOC_EXT)] + TXT_EXT
    return json_path + TXT_EXT


def _write_txt_mirror(json_path, doc):
    """json 파일에 대응하는 사람용 .txt 미러를 쓴다. (단방향: json→txt)
    텍스트 에디터 호환을 위해 OS 기본 줄바꿈을 사용한다(Windows=CRLF)."""
    txt_path = _txt_path_for(json_path)
    try:
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(_doc_to_txt(doc))
    except OSError as e:
        print(f"[BMK Tabbed Notes] txt 미러 쓰기 실패: {txt_path} ({e})")


def _ensure_txt_mirror(json_path, doc):
    """txt 미러가 없거나 json보다 오래됐으면 다시 생성. (트리 스캔 시 백필)
    txt가 json보다 최신이면 사용자가 직접 손댄 것일 수 있으므로 건드리지 않는다."""
    txt_path = _txt_path_for(json_path)
    try:
        j_m = os.path.getmtime(json_path)
    except OSError:
        return
    try:
        if os.path.getmtime(txt_path) >= j_m:
            return  # txt가 최신 → 유지
    except OSError:
        pass        # txt 없음 → 생성
    _write_txt_mirror(json_path, doc)


def _write_doc(path, doc):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    norm = _normalize_doc(doc)
    with open(path, "w", encoding="utf-8", newline="") as f:
        json.dump(norm, f, ensure_ascii=False, indent=1)
    _write_txt_mirror(path, norm)  # json 저장 때마다 txt 미러 갱신


def _to_trash(path):
    if not os.path.exists(path):
        return
    os.makedirs(TRASH_DIR, exist_ok=True)
    base = time.strftime("%Y%m%d_%H%M%S") + "_" + os.path.basename(path)
    dst = os.path.join(TRASH_DIR, base)
    i = 2
    while os.path.exists(dst):
        dst = os.path.join(TRASH_DIR, f"{base} ({i})")
        i += 1
    shutil.move(path, dst)


# ─── 파라미터 프리셋 (v6) ────────────────────────────────────────
# notes/.bmk_param_presets.json 에 순서 있는 리스트로 저장한다.
# 이름은 파일시스템에 닿지 않으므로 안전화 불필요(공백 제거만).
# 동명(대소문자 무시) 프리셋은 로드 시 앞선 항목만 유지한다.

def _load_param_presets():
    try:
        with open(PARAM_PRESETS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return []
    raw = data.get("presets") if isinstance(data, dict) else None
    out, seen = [], set()
    for pr in raw or []:
        if not isinstance(pr, dict):
            continue
        name = pr.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        name = name.strip()
        if name.lower() in seen:
            continue
        seen.add(name.lower())
        out.append({"name": name, "params": _normalize_params(pr.get("params"))})
    return out


def _save_param_presets(presets):
    os.makedirs(NOTES_DIR, exist_ok=True)
    with open(PARAM_PRESETS_PATH, "w", encoding="utf-8") as f:
        json.dump({"presets": presets}, f, ensure_ascii=False, indent=1)


# ─── 메타 (순서/접힘 상태) ───────────────────────────────────────

def _load_meta():
    try:
        with open(META_PATH, "r", encoding="utf-8") as f:
            m = json.load(f)
        if isinstance(m, dict) and isinstance(m.get("categories"), list):
            return m
    except Exception:
        pass
    return {"categories": []}


def _save_meta(meta):
    os.makedirs(NOTES_DIR, exist_ok=True)
    with open(META_PATH, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)


def _norm_cat_entry(c):
    c.setdefault("tabs", [])
    c.setdefault("collapsed", False)
    c.setdefault("children", [])  # v5: 하위 카테고리 (구버전 플랫 메타는 자동으로 빈 리스트)
    return c


def _meta_find(meta, parts, create=True):
    """경로 세그먼트를 따라 내려가 해당 카테고리 메타 항목을 반환.
    create=True면 없는 구간을 만들어가며 내려간다. parts가 비면 None."""
    node = None
    lst = meta["categories"]
    for name in parts:
        node = next((c for c in lst
                     if isinstance(c, dict) and c.get("name") == name), None)
        if node is None:
            if not create:
                return None
            node = {"name": name, "collapsed": False, "tabs": [], "children": []}
            lst.append(node)
        _norm_cat_entry(node)
        lst = node["children"]
    return node


def _meta_locate(meta, parts):
    """(부모 children 리스트, 인덱스, 항목)을 반환 — 없으면 (리스트, -1, None).
    move/delete처럼 항목을 '원위치에서 떼어내야' 할 때 사용한다."""
    lst = meta["categories"]
    for i, name in enumerate(parts):
        idx = next((j for j, c in enumerate(lst)
                    if isinstance(c, dict) and c.get("name") == name), -1)
        if idx < 0:
            return lst, -1, None
        node = _norm_cat_entry(lst[idx])
        if i == len(parts) - 1:
            return lst, idx, node
        lst = node["children"]
    return lst, -1, None


# ─── 트리 (디스크 ↔ 메타 동기화) ─────────────────────────────────

def _ensure_root():
    if not os.path.isdir(NOTES_DIR):
        first = os.path.join(NOTES_DIR, "기본")
        os.makedirs(first, exist_ok=True)
        _write_doc(os.path.join(first, "노트 1" + DOC_EXT), _new_tab_doc())


def _strip_ext(filename):
    return filename[: -len(DOC_EXT)]


# ─── 복제 이름 생성 ──────────────────────────────────────────────
# 'preset' → 'preset(1)', 'preset(1)' 이미 있으면 'preset(2)' …
# 끝의 '(n)' 접미사는 기준 이름에서 떼어내 누적되지 않게 한다.
_DUP_SUFFIX_RE = re.compile(r"\s*\((\d+)\)$")


def _dup_base(name):
    return _DUP_SUFFIX_RE.sub("", str(name)).strip() or str(name)


def _dup_name(base, existing_lower):
    """base(1), base(2)… 중 비어있는 첫 이름을 반환 (대소문자 무시 충돌 회피)."""
    i = 1
    name = f"{base}({i})"
    while name.lower() in existing_lower:
        i += 1
        name = f"{base}({i})"
    return name


def _scan_level(abs_dir, rel_parts, meta_list):
    """abs_dir 바로 아래의 하위 카테고리들을 스캔해 트리 노드 리스트를 만든다.
    - 순서: 메타 순서 우선, 디스크에만 있는 항목은 뒤에(대소문자 무시 정렬)
    - meta_list는 디스크 기준으로 제자리 정리된다(없어진 항목 제거, 새 항목 추가)
      → 호출자(_tree)가 마지막에 한 번 _save_meta 한다.
    - children으로 재귀해 깊이 제한 없이 내려간다."""
    try:
        entries = os.listdir(abs_dir)
    except OSError:
        entries = []
    disk_cats = sorted(
        (d for d in entries
         if os.path.isdir(os.path.join(abs_dir, d)) and not d.startswith(".")),
        key=str.lower,
    )
    meta_order = [c.get("name") for c in meta_list if isinstance(c, dict)]
    cat_order = [n for n in meta_order if n in disk_cats] + \
                [d for d in disk_cats if d not in meta_order]

    by_name = {}
    for c in meta_list:
        if isinstance(c, dict) and c.get("name") in disk_cats \
                and c.get("name") not in by_name:
            by_name[c["name"]] = _norm_cat_entry(c)
    meta_list[:] = [
        by_name.get(n) or {"name": n, "collapsed": False, "tabs": [], "children": []}
        for n in cat_order
    ]

    out = []
    for mc in meta_list:
        cname = mc["name"]
        cdir = os.path.join(abs_dir, cname)
        rel = rel_parts + [cname]
        try:
            files = os.listdir(cdir)
        except OSError:
            files = []
        disk_tabs = sorted(
            (_strip_ext(f) for f in files
             if f.lower().endswith(DOC_EXT) and not f.startswith(".")),
            key=str.lower,
        )
        tab_order = [t for t in mc["tabs"] if t in disk_tabs] + \
                    [t for t in disk_tabs if t not in mc["tabs"]]
        mc["tabs"] = tab_order
        tabs = []
        for t in tab_order:
            jp = os.path.join(cdir, t + DOC_EXT)
            doc = _read_doc(jp)
            _ensure_txt_mirror(jp, doc)  # 기존/외부수정 json에 대한 txt 백필
            tabs.append({"name": t, "doc": doc})
        out.append({
            "name": cname,
            "path": _join_parts(rel),  # v5: 프론트엔드 식별자 (루트 기준 경로)
            "collapsed": bool(mc.get("collapsed")),
            "tabs": tabs,
            "children": _scan_level(cdir, rel, mc["children"]),
        })
    return out


def _tree():
    """디스크를 재귀 스캔하고 메타의 순서/접힘 상태를 적용한 전체 트리를 반환.
    탐색기에서 직접 추가/삭제한 (중첩) 폴더·파일도 이 시점에 자동 반영된다.
    v6: 파라미터 프리셋 목록(param_presets)을 함께 동봉한다 — 페이지 내
    모든 노드가 트리 갱신만으로 프리셋도 동기화된다(구버전 프론트는 무시)."""
    _ensure_root()
    meta = _load_meta()
    cats = _scan_level(NOTES_DIR, [], meta["categories"])
    _save_meta(meta)
    return {"categories": cats, "param_presets": _load_param_presets()}


# ─── 작업 핸들러 ────────────────────────────────────────────────

def _handle_op(p):
    op = p.get("op")

    if op == "save_doc":
        path, _, _, _ = _tab_path(p.get("category"), p.get("tab"))
        _write_doc(path, p.get("doc") or {})
        return {"ok": True}

    meta = _load_meta()
    extra = {}  # 트리에 실어 보낼 부가 정보(경로 재매핑 등) — 구버전 프론트는 무시

    if op == "create_category":
        # name = 새 이름(한 단계), parent = 상위 카테고리 경로("" 또는 생략 = 루트)
        pdir, ppath, pparts = _parent_dir(p.get("parent"))
        name = _require_name(p.get("name"), "카테고리")
        if pparts and not os.path.isdir(pdir):
            raise BMKNotesError(f'상위 카테고리 "{ppath}"을(를) 찾을 수 없습니다')
        cdir = os.path.join(pdir, name)
        if os.path.isdir(cdir):
            raise BMKNotesError(
                f'"{ppath or "루트"}"에 카테고리 "{name}"이(가) 이미 있습니다')
        _check_path_len(cdir, "카테고리")
        os.makedirs(cdir, exist_ok=True)
        _meta_find(meta, pparts + [name])

    elif op == "create_tab":
        path, _, parts, name = _tab_path(p.get("category"), p.get("name"))
        if os.path.exists(path):
            raise BMKNotesError(f'탭 "{name}"이(가) 이미 있습니다')
        _check_path_len(path, "탭")
        # v7: 새 탭은 기본 섹션 순서(ui.order — params를 LoRA 바로 아래)를 갖고 시작
        _write_doc(path, _new_tab_doc())
        mc = _meta_find(meta, parts)
        if name not in mc["tabs"]:
            mc["tabs"].append(name)

    elif op == "rename_category":
        # old = 대상 카테고리 "경로", new = 새 "이름"(마지막 세그먼트만).
        # 다른 부모로의 이동은 move_category(to_parent)를 사용한다.
        old_dir, old_path, old_parts = _cat_dir(p.get("old"))
        new_name = _require_name(p.get("new"), "카테고리")
        new_parts = old_parts[:-1] + [new_name]
        if new_parts != old_parts:
            new_dir = _abs_dir(new_parts)
            if not os.path.isdir(old_dir):
                raise BMKNotesError(f'카테고리 "{old_path}"을(를) 찾을 수 없습니다')
            if os.path.exists(new_dir):
                raise BMKNotesError(f'카테고리 "{new_name}"이(가) 이미 있습니다')
            _check_path_len(new_dir, "카테고리")
            os.rename(old_dir, new_dir)
            _, _, entry = _meta_locate(meta, old_parts)
            if entry is not None:
                entry["name"] = new_name
            # 모든 자손 경로가 함께 바뀌므로 프론트의 prefix 치환용 정보를 동봉
            extra["renamed_category"] = {
                "old": old_path, "new": _join_parts(new_parts)}

    elif op == "rename_tab":
        old_path, _, parts, old = _tab_path(p.get("category"), p.get("old"))
        new_path, _, _, new = _tab_path(p.get("category"), p.get("new"))
        if old != new:
            if not os.path.isfile(old_path):
                raise BMKNotesError(f'탭 "{old}"을(를) 찾을 수 없습니다')
            if os.path.exists(new_path):
                raise BMKNotesError(f'탭 "{new}"이(가) 이미 있습니다')
            _check_path_len(new_path, "탭")
            os.rename(old_path, new_path)
            old_txt = _txt_path_for(old_path)
            if os.path.isfile(old_txt):
                try:
                    os.replace(old_txt, _txt_path_for(new_path))
                except OSError:
                    pass
            mc = _meta_find(meta, parts, create=False)
            if mc and old in mc["tabs"]:
                mc["tabs"][mc["tabs"].index(old)] = new

    elif op == "delete_category":
        # 하위 카테고리·탭을 통째로 .trash로 이동 (폴더 단위 안전망)
        cdir, _, parts = _cat_dir(p.get("name"))
        _to_trash(cdir)
        lst, idx, entry = _meta_locate(meta, parts)
        if entry is not None:
            lst.pop(idx)

    elif op == "delete_tab":
        path, _, parts, name = _tab_path(p.get("category"), p.get("tab"))
        _to_trash(path)
        _to_trash(_txt_path_for(path))  # txt 미러도 함께 (없으면 무시됨)
        mc = _meta_find(meta, parts, create=False)
        if mc and name in mc["tabs"]:
            mc["tabs"].remove(name)

    elif op == "duplicate_tab":
        # 원본 문서를 그대로 복사해 'base(n)' 이름의 사본을 원본 바로 아래에 생성.
        # 최종 이름은 서버가 디스크 기준으로 중복 회피해 정하고 응답에 담아 돌려준다.
        src_path, _, parts, src = _tab_path(p.get("category"), p.get("tab"))
        if not os.path.isfile(src_path):
            raise BMKNotesError(f'탭 "{src}"을(를) 찾을 수 없습니다')
        cdir = os.path.dirname(src_path)
        existing = {_strip_ext(f).lower() for f in os.listdir(cdir)
                    if f.lower().endswith(DOC_EXT) and not f.startswith(".")}
        name = _dup_name(_dup_base(src), existing)
        new_path = os.path.join(cdir, name + DOC_EXT)
        _check_path_len(new_path, "탭")
        _write_doc(new_path, _read_doc(src_path))  # 정규화해 그대로 복제
        mc = _meta_find(meta, parts)
        idx = mc["tabs"].index(src) + 1 if src in mc["tabs"] else len(mc["tabs"])
        mc["tabs"].insert(idx, name)
        _save_meta(meta)
        result = _tree()
        result["new_tab"] = name  # 클라이언트가 새 탭을 선택하도록 이름 반환
        return result

    elif op == "move_tab":
        # index = 원본 제거 후 대상 목록에서의 최종 삽입 위치
        src, fpath, fparts, name = _tab_path(p.get("from_category"), p.get("tab"))
        dst, tpath, tparts, _ = _tab_path(p.get("to_category"), p.get("tab"))
        if fpath != tpath:
            if not os.path.isfile(src):
                raise BMKNotesError(f'탭 "{name}"을(를) 찾을 수 없습니다')
            if os.path.exists(dst):
                raise BMKNotesError(f'"{tpath}"에 같은 이름의 탭이 이미 있습니다')
            _check_path_len(dst, "탭")
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.move(src, dst)
            src_txt = _txt_path_for(src)
            if os.path.isfile(src_txt):
                try:
                    shutil.move(src_txt, _txt_path_for(dst))
                except OSError:
                    pass
        fmc = _meta_find(meta, fparts, create=False)
        if fmc and name in fmc["tabs"]:
            fmc["tabs"].remove(name)
        tmc = _meta_find(meta, tparts)
        idx = p.get("index")
        idx = len(tmc["tabs"]) if not isinstance(idx, int) else max(0, min(idx, len(tmc["tabs"])))
        if name not in tmc["tabs"]:
            tmc["tabs"].insert(idx, name)

    elif op == "move_category":
        # 재정렬 + 부모 변경(중첩)을 겸한다:
        #   name      = 대상 카테고리 경로
        #   to_parent = 새 상위 경로("" = 루트). 생략하면 현재 부모 유지(재정렬만
        #               — 구버전 프론트의 {name, index} 요청이 그대로 이 경우에 해당).
        #   index     = 대상 목록에서의 최종 삽입 위치
        parts = _split_path(p.get("name"))
        if not parts:
            raise BMKNotesError("카테고리 경로가 필요합니다")
        old_dir = _abs_dir(parts)
        old_path = _join_parts(parts)
        if "to_parent" in p:
            pdir, ppath, pparts = _parent_dir(p.get("to_parent"))
        else:
            pparts = parts[:-1]
            pdir, ppath = _abs_dir(pparts), _join_parts(pparts)
        # 순환 방지 (서버측 필수 검증): 자기 자신/자손 아래로는 이동 금지
        if pparts[:len(parts)] == parts:
            raise BMKNotesError("카테고리를 자기 자신이나 그 하위로 옮길 수 없습니다")
        new_parts = pparts + [parts[-1]]
        if new_parts != parts:  # 부모가 실제로 바뀜 → 디스크 이동
            new_dir = _abs_dir(new_parts)
            if not os.path.isdir(old_dir):
                raise BMKNotesError(f'카테고리 "{old_path}"을(를) 찾을 수 없습니다')
            if pparts and not os.path.isdir(pdir):
                raise BMKNotesError(f'대상 카테고리 "{ppath}"을(를) 찾을 수 없습니다')
            if os.path.exists(new_dir):
                raise BMKNotesError(
                    f'"{ppath or "루트"}"에 같은 이름의 카테고리가 이미 있습니다')
            _check_path_len(new_dir, "카테고리")
            shutil.move(old_dir, new_dir)
            extra["moved_category"] = {
                "old": old_path, "new": _join_parts(new_parts)}
        # 메타: 원위치에서 떼어 새 부모의 children[index]로 (하위 구조 통째 유지)
        src_list, src_idx, entry = _meta_locate(meta, parts)
        if entry is not None:
            src_list.pop(src_idx)
        else:
            entry = {"name": parts[-1], "collapsed": False,
                     "tabs": [], "children": []}
        dst_list = _meta_find(meta, pparts)["children"] if pparts else meta["categories"]
        idx = p.get("index")
        idx = len(dst_list) if not isinstance(idx, int) else max(0, min(idx, len(dst_list)))
        dst_list.insert(idx, entry)

    elif op == "set_collapsed":
        parts = _split_path(p.get("name"))
        mc = _meta_find(meta, parts, create=False) if parts else None
        if mc:
            mc["collapsed"] = bool(p.get("collapsed"))
        _save_meta(meta)
        # 트리를 반환하지 않음 → 클라이언트가 재렌더하지 않음
        # (접기 토글이 이름 변경 입력창을 파괴하는 회귀 방지)
        return {"ok": True}

    elif op == "save_param_preset":
        # 프리셋 저장/갱신 — 동명(대소문자 무시)은 제자리 교체(upsert).
        # params는 노트 문서와 동일한 규칙으로 정규화해 저장한다.
        name = str(p.get("name") or "").strip()
        if not name:
            raise BMKNotesError("프리셋 이름이 필요합니다")
        params = _normalize_params(p.get("params"))
        presets = _load_param_presets()
        for pr in presets:
            if pr["name"].lower() == name.lower():
                pr["name"] = name  # 대소문자 표기 갱신
                pr["params"] = params
                break
        else:
            presets.append({"name": name, "params": params})
        _save_param_presets(presets)

    elif op == "delete_param_preset":
        name = str(p.get("name") or "").strip().lower()
        if not name:
            raise BMKNotesError("프리셋 이름이 필요합니다")
        presets = [pr for pr in _load_param_presets()
                   if pr["name"].lower() != name]
        _save_param_presets(presets)

    elif op == "import_embedded":
        # 구버전(내장형) 노드 데이터를 공유 폴더(루트 레벨)로 병합. 동명 탭은 (2) 부여.
        # 구버전 탭의 평문 text는 새 문서의 prompt 필드로 옮긴다.
        data = p.get("data") or {}
        for cat in data.get("categories", []) or []:
            cname = _safe_name((cat or {}).get("name"), "가져온 노트")
            cdir = os.path.join(NOTES_DIR, cname)
            os.makedirs(cdir, exist_ok=True)
            existing = {_strip_ext(f).lower() for f in os.listdir(cdir)
                        if f.lower().endswith(DOC_EXT)}
            for tab in cat.get("tabs", []) or []:
                base = _safe_name((tab or {}).get("name"), "노트")
                tname, i = base, 2
                while tname.lower() in existing:
                    tname = f"{base} ({i})"
                    i += 1
                existing.add(tname.lower())
                _write_doc(os.path.join(cdir, tname + DOC_EXT),
                           {"prompt": (tab or {}).get("text") or ""})

    else:
        raise BMKNotesError(f"알 수 없는 작업: {op!r}")

    _save_meta(meta)
    result = _tree()
    result.update(extra)
    return result


# ─── HTTP API ───────────────────────────────────────────────────

def _register_routes():
    try:
        from server import PromptServer
        from aiohttp import web
    except Exception:
        return  # 서버 환경이 아님 (단위 테스트 등)

    server = getattr(PromptServer, "instance", None)
    if server is None or getattr(server, "_bmk_notes_routes_registered", False):
        return
    server._bmk_notes_routes_registered = True

    @server.routes.get("/bmk/notes/tree")
    async def bmk_notes_tree(request):
        with _LOCK:
            tree = _tree()
        return web.json_response(tree)

    @server.routes.post("/bmk/notes/op")
    async def bmk_notes_op(request):
        try:
            payload = await request.json()
        except Exception:
            return web.json_response({"error": "잘못된 요청 본문"}, status=400)
        try:
            with _LOCK:
                result = _handle_op(payload if isinstance(payload, dict) else {})
            return web.json_response(result)
        except BMKNotesError as e:
            return web.json_response({"error": str(e)}, status=400)
        except Exception as e:
            print(f"[BMK Tabbed Notes] op 처리 오류: {e}")
            return web.json_response({"error": f"서버 오류: {e}"}, status=500)


_register_routes()


# ─── 노드 ───────────────────────────────────────────────────────

class BMKTabbedNotes:
    """공유 노트 폴더의 구조화 노트를 편집·전송하는 UI 전용 노드 (v7: 출력 포트 없음).

    notes_data(JSON)에는 선택 상태만 저장된다:
      {"mode": 2, "activeCategory": "...", "activeTab": "...", "sidebarWidth": 110}
    activeCategory는 루트 기준 경로("artstyle/anime")다. 값 전달(프롬프트/LoRA/
    파라미터)은 프론트엔드가 대상 노드 위젯에 직접 주입하므로 서버 실행 경로가 없고,
    구버전(내장형) 데이터({"categories": [...]})는 프론트의 "가져오기" 안내로 병합한다.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "notes_data": ("STRING", {"default": "{}", "multiline": False}),
            }
        }

    # v7: 출력 포트 없음. RETURN_TYPES가 빈 튜플이고 OUTPUT_NODE도 아니므로
    # 이 노드는 큐 실행 그래프에 포함되지 않는다(전송 패널의 프론트 주입이 유일한 경로).
    RETURN_TYPES = ()
    FUNCTION = "noop"
    CATEGORY = "BMK/Text"
    SEARCH_ALIASES = ["bmk", "tabbed notes", "notes", "memo", "notepad", "탭 노트", "메모"]
    DESCRIPTION = (
        "공유 노트 폴더(ComfyUI/user/bmk_notes — 무제한 중첩 가능한 카테고리 폴더/탭.json)를 "
        "모든 노드·워크플로우에서 "
        "함께 쓰는 탭형 노트 노드. 노트별 prompt/negative/LoRA/워크플로우 파라미터를 기록하고 "
        "전송 패널로 대상 노드 위젯에 직접 주입합니다. "
        "출력 포트가 없는 UI 전용 노드로, 큐 실행에는 포함되지 않습니다."
    )

    def noop(self, notes_data):
        # v7: 출력이 없어 정상 경로에서는 호출되지 않는다.
        # 혹시 실행 그래프에 포함되더라도 아무 일도 하지 않도록 방어적으로 존재한다.
        return ()


NODE_CLASS_MAPPINGS = {
    "BMKTabbedNotes": BMKTabbedNotes,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "BMKTabbedNotes": "BMK Tabbed Notes \U0001F4D1",
}
