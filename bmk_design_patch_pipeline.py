"""BMK Design Patch Pipeline — Studio S3 실행 설정(pipeline.json)·처음 값 유도·갈라짐·템플릿 프롬프트(ComfyUI 비의존).

배경
----
S3 는 그래프를 몰라도 Studio 에서 반영 → 계획 → 승인 → Export 를 하게 한다. 무료 단계(반영·Export)는 서버가 만든 템플릿 프롬프트(API
형식, 노드 키 dp_*)를 Studio 가 /api/prompt 로 직접 큐에 넣는다(D2). 템플릿의 위젯 값은 <project>/pipeline.json(Studio 소유, D3)이
원천이고, 처음 값은 매니페스트·스펙에 남은 마지막 값 + 노드 기본값으로 유도한다. 추정한 값은 "확인 필요"(confirm)로 두고 확인 전에는
반영을 막는다 — 추정한 Prepare 설정으로 첫 반영이 셀(cell_key)을 뒤집어 다시 과금 대상을 만들지 않게.
pipeline.json 은 노드가 읽지 않는다(Project 지문 밖). 값은 템플릿 프롬프트의 위젯 값으로만 흘러간다.
명세: H:\\BmkNodeDesign\\MultiLayerCropEdit\\_proto\\STUDIO_S3_SPEC.md §2.1(pipeline.json)·§2.2(유도·갈라짐)·§2.3(템플릿)·§6(라우트).

import 경계(T6): 모듈 수준은 표준 라이브러리·store 만. prompt(변형 토큰 검사)와 vlm(초안 위젯 선택지·조합 검사)은 함수 안에서만
지연 import 한다(상대 → 절대 폴백). 보드(board → pipeline)가 import 할 때 vlm 이 따라 올라오면 S2 B0(보드 state 경로에 vlm 없음)가
깨지므로 state 가 부를 수 있는 함수(pipeline_rev·load·current(deep=False)·derive(deep=False)·drift·prepare_record·path_warnings·recent_exports·
prompt_gate·build_prompt 의 apply·export·run·reroll·only_string·strip_style)는 vlm 을 부르지 않는다. vlm 은 validate_section("draft")·
current(deep=True)·derive(deep=True)·view·update·build_prompt("draft")·widget_table()·settings_widgets() 만 불러온다(/pipeline 라우트
경로 — B0‴ 허용).
노드 모듈(torch)도 import 하지 않으므로 노드 위젯 표(이름·기본값·선택지·min/max·_SAFE_PREFIX_RE)는 여기 복사해 두고, 패키지 시험 P2 가
실제 INPUT_TYPES 와 대조한다(노드 위젯이 생기거나 빠지거나 값이 바뀌면 실패 → 이 표를 고칠 것).

공개 API (예외는 ValueError + 한국어 메시지. PipelineConflict(ValueError) 만 보드가 409)
---------------------------------------------------------------------------------------
상수
- SCHEMA("bmk.design_patch.pipeline/1") · PIPELINE_NAME("pipeline.json") · SECTIONS(절 8개, 파일·응답 순서)
- STAGES("apply","export","draft","run","reroll") · FREE_STAGES("apply","export") · CLASS{역할: class_type} · NODE_KEYS{역할: "dp_<역할>"}
- STAGE_NODES{stage: 역할 순서(cand 는 켰을 때만)} · OUTPUT_KEY{stage: 출력 노드 키} · NOT_STORED · CONTROL_KEYS{"cand": ("enabled",)}
- WIDGETS{class_type: {입력 이름: {"type", "default", "choices"?, "min"?, "max"?, "step"?}}} — 노드 INPUT_TYPES(required∪optional,
  hidden 제외) 복사. type = STRING·INT·FLOAT·BOOLEAN·COMBO·BMK_DP_PROJECT(핸들 링크). vlm 상수에서 오는 선택지는 "vlm:<이름>" 표지
- NODE_INPUTS{class_type: 입력 이름 튜플} · _SAFE_PREFIX_RE(노드 Export 와 같은 식)
- class PipelineConflict(ValueError)       rev 가 요청과 다름·파일 상태와 맞지 않는 rev — 보드 _guard 409
표·검사
- widget_table() -> dict                   WIDGETS 의 "vlm:" 표지를 vlm 상수로 채운 사본(vlm 지연 import — P2 대조용)
- settings_widgets() -> {절: {키: {"type", "default", "choices"?, "min"?, "max"?, "step"?, "readonly"?}}}
      설정 패널용(GET /pipeline 의 widgets — JSON 형). 노드 선택지 그대로이되 draft.write_mode 는 fill·refresh, draft_round 는 readonly
- section_keys(section) -> tuple           절 키 = 그 노드 위젯 − NOT_STORED (+ CONTROL_KEYS). 모르는 절 ValueError
- defaults(root=None) -> settings          노드 기본값 8절(export.filename_prefix 는 root 가 있으면 폴더 이름)
- validate_section(section, values, *, draft_rounds=None) -> 정규화한 값
      키 집합이 정확히 같아야 함(빠짐·모르는 키 400), 타입 엄격(INT = JSON 정수(bool 아님), FLOAT = 정수·실수 → float, BOOLEAN = bool,
      STRING·COMBO = str), 선택지·min/max, 절 규칙(import 경로 정규화, cand.enabled + 빈 path, mapping_json, prepare.variants,
      draft = vlm.check_widgets(모델별 effort)·write_mode fill|refresh, export.filename_prefix). draft_rounds 가 있으면 draft_round ∈ 그 집합.
pipeline.json
- load(root) -> doc | None                 없으면 None, 깨졌으면(읽을 수 없음 포함) ValueError(JSON·schema·구조·값 — rev 는 1 이상
      2^53−1 이하: 넘으면 브라우저가 반올림해 rev 대조가 늘 어긋남). manifest_lock 안에서 읽음. 파일을 쓰지 않음
- pipeline_rev(root) -> int | None         state 플래그(없거나 깨졌으면 None)
- current(root, m=None, *, deep=False, derived=None) -> {"rev", "saved", "error", "settings", "confirm"}
      있음 = 파일 값 / 없음 = rev 0·saved False·유도값(derive(deep) — derived 를 주면 그것) / 깨짐 = rev None·error·settings None·
      confirm None. 파일을 쓰지 않음. 계획·prompt 라우트는 current(root, m, deep=True)["settings"](GET /pipeline 과 같은 유도값),
      state 경로는 deep=False
- update(root, rev, section, values) -> {"ok", "rev", "settings", "confirm", "drift"}   (POST /pipeline/settings 응답 모양)
      rev 규칙: 파일 없음 = 0 만(유도값(deep) 전체 + 보낸 절로 만들고 rev 1), 깨짐 = None 만(덮어쓰기 — 읽히는 rev 의 다음, 상한을
      넘었으면 1), 있음 = 지금 rev 만 — 아니면 PipelineConflict. 상한(2^53−1)인 파일을 저장하면 rev 1.
      절 하나를 통째 교체하고 confirm 의 그 절 항목을 지운다. 바뀐 것이 없으면 쓰지 않음(rev 그대로). 잠금 = store.manifest_lock.
      draft.draft_round 는 지금 유도값 또는 저장값만 받는다(바꾸면 400 — 다시 초안은 그래프)
- view(root, m=None) -> GET /pipeline 응답 {rev, saved, error, settings, confirm, derived(deep), drift, warnings, exports, widgets,
      manifest_rev}
유도·갈라짐·경고
- derive(root, m, *, deep=False) -> {"settings", "from": {"절.키": "manifest"|"jobs"|"spec_draft"|"default"}, "confirm": [키 경로], "notes"}
      매니페스트와 specs/<crop_id>.json(store.load_spec_override)·board_pref.json(analyze — 마지막 Analyze·Review 설정, 없으면 후보별
      마지막 분석 항목의 최다값 / export — compose·export 절은 store.last_export = 마지막 Export 가 낸 PSD 의 기록)만 읽는다
      (vlm 원장 아님). deep 이면 마지막 절 검사가 vlm 선택지·모델별 effort 까지 본다(vlm 지연 import — view·update·current(deep=True)
      가 씀). deep=False 는 그것을 보지 않아 draft.vlm 이 선택지 밖일 수 있다. 검사를 통과하지 못한 절은 노드 기본값 + 그 절 확인 — draft 는 vlm·effort·image_detail 만(draft_round·credit_cap 그대로)
- drift(settings, m) -> [{"section", "field", "manifest", "settings", "kind"}]   kind = "import"(마지막 Import 와 다름 — 대소문자 구분) |
      "cell"(마지막 Prepare 와 모델·품질·크기 규칙·변형 토큰 집합·ref_style 이 다름 — 셀이 바뀌어 다시 과금 대상) |
      "calls"(calls_per_cell 을 늘림 — 완료된 셀에도 빠진 rep 이 새 유료 호출) | "cost"(n 이 다름·calls_per_cell 을 줄임 — 셀 그대로)
- prepare_record(m) -> (출처 "prepare_settings"|"jobs"|None, {키: 값})   drift 가 비교할 마지막 Prepare 기록. 옛 프로젝트(prepare_settings
      없음)는 derive 와 같은 jobs 최다값의 model·quality·size_rule·n·calls_per_cell(variants·ref_style 은 기록이 없어 없음)
- path_warnings(settings) -> [{"field", "reason": "no_psd"|"missing_path", "msg"}]   field = import.* | cand.path(켠 후보 등록 —
      source 에 맞게 psd_harvest = 파일, folder = 폴더)
- recent_exports(m, n=10) -> [{"rev", "psd", "time", "layers", "problems", "bytes", "mask_groups", "mask_edits"}]   최근 것 먼저
템플릿
- prompt_gate(root, stage, m=None, *, cur=None) -> None | {"error", "reason", "fields"?, "field"?}   cur = 템플릿을 만들 current() 값
      reason = pipeline_broken | confirm_settings(fields — 파일 없음이거나 confirm 에 prepare.*·import.* 남음) | no_psd | missing_path(field, fields).
      missing_path 는 import 경로 + dp_cand 를 담는 stage(export 빼고 전부)면 cand.path 도. 보드는 그대로 409 로(_Reject(409, error,
      reason=…)). 그 밖에는 stage 와 무관하게 같은 판정(유료 stage 의 추가 판정 — 실행 중·반영 필요·해시 — 은 보드 pipeline_prompt)
- build_prompt(stage, settings, root, *, approve="", only="") -> {노드 키: {"class_type", "inputs"}}
      T1–T5: 무료 stage 에 Run·SpecDraft 없음, run·draft 는 approve(12자 소문자 hex) 필수·only 필수, reroll 은 approve "",
      무료 stage 는 approve·only 없음. adopt "", keep_loaded false. 연결 = [앞 노드 키, 0]. Project 위젯 = {project: 폴더 이름,
      base_dir: 루트의 두 단계 위}(store.project_root 로 다시 만든 경로가 root 와 같아야 함). inputs = 그 노드 입력 이름 정확히. 쓰는 절만
      validate_section 으로 다시 검사(draft stage 만 vlm 지연 import)
- only_string(selected_ids, style=False, *, known=None) -> str   정규형 = crop_id 명시 목록(정렬·중복 제거, 쉼표) + style 이면 ",style".
      선택 0개·NNN 만·밑줄 없는 토큰·style 이름·known 밖 = ValueError(빈 only 는 "전부"라 만들지 않음). style 은 Draft + apply=full 일 때만 부를 것
- strip_style(only) -> str                 Run 용(style 토큰을 뺀 쉼표 목록) — 계획(runner.plan_calls)과 Run 위젯이 같은 문자열을 쓴다

버전 이력
---------
v1 (2026-10)
- S3a U1 최초 구현(STUDIO_S3_SPEC §2.1–§2.3·§6).
- S3b(10/18): view 에 manifest_rev(무료 반영 expect — _proto/s3b/S3B_CONTRACT.md §1).
"""

from __future__ import annotations

import copy
import json
import math
import os
import re
from collections import Counter
from typing import Any

try:
    from . import bmk_design_patch_store as store
except ImportError:  # 단독 실행/테스트
    import bmk_design_patch_store as store

SCHEMA = "bmk.design_patch.pipeline/1"
PIPELINE_NAME = "pipeline.json"

CLASS = {"project": "BMKDesignPatchProject", "import": "BMKDesignPatchImportPSD", "cand": "BMKDesignPatchCandidateIn",
         "prepare": "BMKDesignPatchPrepare", "draft": "BMKDesignPatchSpecDraft", "run": "BMKDesignPatchRun",
         "analyze": "BMKDesignPatchAnalyze", "review": "BMKDesignPatchReview", "compose": "BMKDesignPatchCompose",
         "export": "BMKDesignPatchExportPSD"}
NODE_KEYS = {role: f"dp_{role}" for role in CLASS}  # 숫자 아님 — 탭이 실행 중 노드를 자기 그래프의 같은 번호로 착각하지 않게
SECTIONS = ("import", "cand", "prepare", "draft", "run", "analyze", "compose", "export")
STAGES = ("apply", "export", "draft", "run", "reroll")
FREE_STAGES = ("apply", "export")
_RUN_NODES = ("project", "import", "cand", "prepare", "run", "analyze", "review")
STAGE_NODES = {"apply": ("project", "import", "cand", "prepare", "analyze", "review"),
               "export": ("project", "analyze", "compose", "export"),  # Import 없음 — 마지막 반영 상태를 내보냄(§2.3)
               "draft": ("project", "import", "cand", "draft"),
               "run": _RUN_NODES, "reroll": _RUN_NODES}
OUTPUT_KEY = {"apply": "dp_review", "export": "dp_export", "draft": "dp_draft", "run": "dp_review", "reroll": "dp_review"}
NOT_STORED = ("project", "approve", "only", "adopt", "keep_loaded")  # 저장하지 않음 — only 는 요청마다, approve 는 그 한 번만
CONTROL_KEYS = {"cand": ("enabled",)}  # 템플릿 제어 키(위젯 아님): build_prompt 가 dp_cand 를 넣을지만 정함

# ── 노드 위젯 표(bmk_design_patch.py INPUT_TYPES 복사 — P2 가 대조) ─────────────────────────────────────────────
_PROJECT_TYPE = "BMK_DP_PROJECT"
_MODELS = ("gpt-image-2.5-sunburst", "gpt-image-2.5-flare")
_QUALITIES = ("max", "xhigh", "high", "medium", "low")
_REF_STYLES = ("at", "plain", "both")
_REGISTER_MODES = ("guarded_affine", "translation", "off")
_TONE_MODES = ("field", "global", "off")
_ROI_PRIORS = ("none", "hand_bbox")
_MASK_SOURCES = ("auto", "hand", "hand_else_auto")
_EXPORT_MASK_SOURCES = ("hand_else_auto", "auto", "hand")
_ZORDERS = ("auto_small_on_top", "harvest")
_LAYER_MODES = ("smart_object", "pixel")
_ALTERNATES = ("hidden_all", "none")
_COLORS = ("toned", "raw")
_SOURCES = ("psd_harvest", "folder")
_SAFE_PREFIX_RE = re.compile(r"^[\w\-.가-힣 ]+$")  # 노드 Export filename_prefix 와 같은 식(+ '..' 금지)
_DRAFT_WRITE_MODES = ("fill", "refresh")  # Studio 가 받는 write_mode(preview = 유료 호출하고 안 씀, force = only 필수 — 범위 밖)


def _s(default):
    return {"type": "STRING", "default": default}


def _i(default, lo, hi):
    return {"type": "INT", "default": default, "min": lo, "max": hi}


def _f(default, lo, hi, step):
    return {"type": "FLOAT", "default": default, "min": lo, "max": hi, "step": step}


def _b(default):
    return {"type": "BOOLEAN", "default": default}


def _c(choices, default):
    return {"type": "COMBO", "default": default, "choices": choices}


_LINK = {"type": _PROJECT_TYPE}
WIDGETS = {
    "BMKDesignPatchProject": {"project": _s("design_patch"), "base_dir": _s("")},
    "BMKDesignPatchImportPSD": {
        "project": _LINK, "psd_path": _s(""), "crop_group": _s("auto"), "clean_plate_layer": _s("auto"),
        "base_layer": _s("auto"), "crop_pixels_group": _s("auto"), "refs_dir": _s(""), "glossary_path": _s("")},
    "BMKDesignPatchCandidateIn": {
        "project": _LINK, "source": _c(_SOURCES, "psd_harvest"), "path": _s(""), "group": _s("03.수정"),
        "mapping_json": _s(""), "visible_is_pick": _b(True), "keep_hand_masks": _b(True)},
    "BMKDesignPatchPrepare": {
        "project": _LINK, "model": _c(_MODELS, _MODELS[0]), "quality": _c(_QUALITIES, "max"),
        "size_rule": _c(tuple(store.GPT_SIZE_RULES), "user_k"), "variants": _s("V1,V4"), "n": _i(4, 1, 10),
        "ref_style": _c(_REF_STYLES, "at"), "calls_per_cell": _i(1, 1, 8)},
    "BMKDesignPatchSpecDraft": {
        "project": _LINK, "approve": _s(""), "vlm": _c("vlm:VLM_CHOICES", "openai/gpt-6-sol"),
        "effort": _c("vlm:EFFORTS", "xhigh"), "image_detail": _c("vlm:IMAGE_DETAILS", "high"),
        "write_mode": _c("vlm:WRITE_MODES", "fill"), "only": _s(""), "draft_round": _i(0, 0, 99),
        "concurrency": _i(4, 1, 8), "credit_cap": _i(0, 0, 100000), "keep_loaded": _b(False),
        "apply": _c("vlm:APPLY_MODES", "targets"), "adopt": _s("")},
    "BMKDesignPatchRun": {
        "project": _LINK, "approve": _s(""), "max_new_calls": _i(64, 1, 1000), "concurrency": _i(4, 1, 8),
        "wave_minutes": _i(25, 1, 60), "retry_failed": _b(False), "retry_orphans": _b(False), "only": _s("")},
    "BMKDesignPatchAnalyze": {
        "project": _LINK, "register": _c(_REGISTER_MODES, "guarded_affine"), "tone": _c(_TONE_MODES, "field"),
        "tone_sigma": _i(16, 2, 128), "dE": _f(8.0, 1.0, 50.0, 0.5), "grow": _i(9, 0, 64),
        "feather_sigma": _f(3.5, 0.0, 32.0, 0.5), "roi_prior": _c(_ROI_PRIORS, "none"), "only_picked": _b(False)},
    "BMKDesignPatchReview": {"project": _LINK},
    "BMKDesignPatchCompose": {
        "project": _LINK, "mask_source": _c(_MASK_SOURCES, "hand_else_auto"), "use_registration": _b(True),
        "use_tone": _b(True), "zorder": _c(_ZORDERS, "auto_small_on_top")},
    "BMKDesignPatchExportPSD": {
        "project": _LINK, "filename_prefix": _s("design_patch"), "layer_mode": _c(_LAYER_MODES, "smart_object"),
        "alternates": _c(_ALTERNATES, "hidden_all"), "color": _c(_COLORS, "toned"),
        "mask_source": _c(_EXPORT_MASK_SOURCES, "hand_else_auto"), "include_base": _b(True),
        "include_crop_outlines": _b(True), "skip_empty": _b(True)},  # skip_empty 는 optional
}
NODE_INPUTS = {cls: tuple(spec) for cls, spec in WIDGETS.items()}
_CONTROL_SPECS = {("cand", "enabled"): _b(False)}

_IMPORT_PATHS = ("psd_path", "refs_dir", "glossary_path")
# import 절 키 → 매니페스트 source 키(Import 노드가 쓰는 이름). 앞 다섯은 Import 캐시 판정(heavy), 뒤 둘은 light(없으면 "")
_IMPORT_SOURCE = (("psd_path", "psd"), ("crop_group", "crop_group"), ("clean_plate_layer", "clean_plate_layer"),
                  ("base_layer", "base_layer"), ("crop_pixels_group", "crop_pixels_group"), ("refs_dir", "refs_dir"),
                  ("glossary_path", "glossary"))
_PREPARE_CELL = ("model", "quality", "size_rule", "variants", "ref_style")  # cell_key 에 들어가는 설정(다르면 새 셀)
# n·calls_per_cell 은 셀이 그대로 — n 은 장 수·비용·승인 해시만. calls_per_cell 을 늘리면 jobs[].reps 가 늘어 완료된 셀에도 빠진 rep 이
# 새 유료 호출(runner.plan_calls '생성 조건마다 호출 수 증가')이 되므로 갈라짐 kind "calls"(줄이면 "cost")
_REV_MAX = 2 ** 53 - 1  # 브라우저 JSON 수(Number.MAX_SAFE_INTEGER) — 넘는 rev 는 반올림돼 rev·sig 대조가 늘 어긋나므로 깨진 파일로 본다
_ANALYZE_PARAMS = ("register", "tone", "tone_sigma", "dE", "grow", "feather_sigma", "roi_prior")
_QWEN_CHOICE = "local_qwen/Qwen3-VL-8B-Instruct"
_DRAFT_SOURCE = ("vlm", "effort", "image_detail")  # 스펙 _draft 출처에서 오는 조합(선택지·모델별 effort 검사 대상)
_RECOMMENDED_CREDIT_CAP = 1200
_HASH_RE = re.compile(r"[0-9a-f]{12}")
_ONLY_ID_RE = re.compile(r"[A-Za-z0-9\-]+_[A-Za-z0-9_\-]+")  # 밑줄이 있어야 함 — NNN·cell_key 접두(hex)로 읽히지 않게
_ONLY_SPLIT_RE = re.compile(r"[,\s]+")
_DOC_KEYS = ("schema", "rev", "confirm") + SECTIONS


class PipelineConflict(ValueError):
    """pipeline.json rev 가 요청과 다름(다른 창에서 바뀜)·파일 상태와 맞지 않는 rev — 보드가 409 로 돌려준다."""


# ══════════════════════════════════════════════════════════════════════
# 지연 import(T6) — prompt·vlm 은 함수 안에서만
# ══════════════════════════════════════════════════════════════════════
def _prompt():
    try:
        from . import bmk_design_patch_prompt as dprompt
    except ImportError:
        import bmk_design_patch_prompt as dprompt
    return dprompt


def _vlm():
    try:
        from . import bmk_design_patch_vlm as dvlm
    except ImportError:
        import bmk_design_patch_vlm as dvlm
    return dvlm


# ══════════════════════════════════════════════════════════════════════
# 표·검사
# ══════════════════════════════════════════════════════════════════════
def widget_table() -> dict:
    """WIDGETS 사본 — "vlm:<이름>" 선택지 표지를 vlm 모듈 상수(목록)로 채운다(vlm 지연 import)."""
    dvlm = None
    out = {}
    for cls, specs in WIDGETS.items():
        out[cls] = {}
        for name, spec in specs.items():
            sp = dict(spec)
            ch = sp.get("choices")
            if isinstance(ch, str) and ch.startswith("vlm:"):
                dvlm = dvlm or _vlm()
                sp["choices"] = tuple(getattr(dvlm, ch[4:]))
            out[cls][name] = sp
    return out


def settings_widgets() -> dict:
    """설정 패널용 절별 위젯 {절: {키: {"type", "default", "choices"?, "min"?, "max"?, "step"?, "readonly"?}}}(GET /pipeline 의 widgets,
    JSON 형 — 선택지는 목록). 노드 선택지 그대로(widget_table)이되 Studio 가 받는 값만: draft.write_mode 는 fill·refresh,
    draft.draft_round 는 읽기 전용(유도값 그대로 — 다시 초안은 그래프). 선택지를 JS 에 따로 복사하지 않게 서버가 준다."""
    wt = widget_table()
    out = {}
    for sec in SECTIONS:
        out[sec] = {}
        for k in section_keys(sec):
            sp = dict(_CONTROL_SPECS.get((sec, k)) or wt[CLASS[sec]][k])
            if "choices" in sp:
                sp["choices"] = list(sp["choices"])
            out[sec][k] = sp
    out["draft"]["write_mode"]["choices"] = list(_DRAFT_WRITE_MODES)
    out["draft"]["draft_round"]["readonly"] = True
    return out


def section_keys(section: str) -> tuple:
    """절 키 = 그 노드 입력 − NOT_STORED(+ 제어 키). 모르는 절이면 ValueError."""
    if section not in SECTIONS:
        raise ValueError(f"모르는 설정 절입니다: {section!r} (절: {', '.join(SECTIONS)})")
    return CONTROL_KEYS.get(section, ()) + tuple(k for k in NODE_INPUTS[CLASS[section]] if k not in NOT_STORED)


def _spec(section: str, key: str) -> dict:
    return _CONTROL_SPECS.get((section, key)) or WIDGETS[CLASS[section]][key]


def defaults(root: str | None = None) -> dict:
    """노드 기본값 8절. export.filename_prefix 는 root 가 있으면 프로젝트 폴더 이름(§2.2 — exports 가 빌 때의 유도값)."""
    out = {sec: {k: _spec(sec, k)["default"] for k in section_keys(sec)} for sec in SECTIONS}
    if root:
        out["export"]["filename_prefix"] = os.path.basename(os.path.normpath(root))
    return out


def _strip_path(p) -> str:
    """노드 _strip_path 와 같음(따옴표·공백 제거)."""
    return str(p or "").strip().strip('"').strip("'").strip()


def _norm_path(p) -> str:
    """Import 가 매니페스트 source 에 적는 꼴(_strip_path 뒤 '\\' → '/') — 저장·유도·비교가 같은 문자열을 쓰게."""
    return _strip_path(p).replace("\\", "/")


def _value(section: str, key: str, v: Any, *, deep: bool) -> Any:
    """위젯 값 하나의 타입·선택지·범위 검사(JSON 타입 엄격). FLOAT 는 float 로 바꿔 돌려준다."""
    sp = _spec(section, key)
    t, where = sp["type"], f"{section}.{key}"
    if t == "BOOLEAN":
        if not isinstance(v, bool):
            raise ValueError(f"{where} 는 true/false 여야 합니다: {v!r}")
        return v
    if t in ("INT", "FLOAT"):
        # JSON 정수는 math.isfinite·float() 에 넣지 않는다 — 310자리가 넘으면 OverflowError(ValueError 아님)라 깨진 파일 경로를 빠져나감.
        # 범위 비교(int 대 float 는 정확 비교)가 먼저 거르고 float 변환은 그 뒤
        ok = isinstance(v, int) or (t == "FLOAT" and isinstance(v, float) and math.isfinite(v))
        if isinstance(v, bool) or not ok:
            raise ValueError(f"{where} 는 {'정수' if t == 'INT' else '숫자'}여야 합니다: {v!r}")
        if not sp["min"] <= v <= sp["max"]:
            raise ValueError(f"{where} 는 {sp['min']}–{sp['max']} 범위여야 합니다: {v!r}")
        return float(v) if t == "FLOAT" else v
    if not isinstance(v, str):
        raise ValueError(f"{where} 는 문자열이어야 합니다: {v!r}")
    ch = sp.get("choices")
    if t == "COMBO" and isinstance(ch, str):
        ch = tuple(getattr(_vlm(), ch[4:])) if deep else None  # vlm 선택지는 deep 검사에서만(T6)
    if ch is not None and v not in ch:
        raise ValueError(f"{where} 값이 선택지에 없습니다: {v!r} (선택지: {', '.join(ch)})")
    return v


def _check_mapping(text: str) -> None:
    """cand.mapping_json = 빈 문자열 또는 JSON 객체(값은 객체 또는 타깃 문자열) — 노드 _parse_mapping 과 같은 거절."""
    text = text.strip()
    if not text:
        return
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"cand.mapping_json 형식 오류: {e}") from e
    if not isinstance(raw, dict):
        raise ValueError("cand.mapping_json 은 JSON 객체여야 합니다")
    for k, v in raw.items():
        if not isinstance(v, (dict, str)):
            raise ValueError(f"cand.mapping_json 값은 객체 또는 타깃 문자열이어야 합니다: {k!r}")


def _check_round(value: int, allowed) -> None:
    """draft.draft_round 는 유도값(·저장값) 그대로 — 다시 초안(round 올리기)은 그래프에서(§9)."""
    if value not in allowed:
        raise ValueError(f"draft.draft_round 는 바꿀 수 없습니다(유도값 {sorted(allowed)} — 다시 초안은 그래프에서): {value!r}")


def _check_section(section: str, values: Any, *, deep: bool, draft_rounds=None) -> dict:
    """절 검사 → 정규화한 값(키 순서 = section_keys). deep = vlm 선택지·조합까지(지연 import)."""
    keys = section_keys(section)
    if not isinstance(values, dict):
        raise ValueError(f"{section} 절 값은 객체여야 합니다: {type(values).__name__}")
    missing = [k for k in keys if k not in values]
    extra = sorted(str(k) for k in values if k not in keys)
    if extra:
        raise ValueError(f"{section} 절에 모르는 키: {', '.join(extra)} (키: {', '.join(keys)})")
    if missing:
        raise ValueError(f"{section} 절의 키를 모두 보내야 합니다 — 빠짐: {', '.join(missing)}")
    out = {k: _value(section, k, values[k], deep=deep) for k in keys}
    if section == "import":
        for k in _IMPORT_PATHS:
            out[k] = _norm_path(out[k])
    elif section == "cand":
        if out["enabled"] and not _strip_path(out["path"]):
            raise ValueError("cand.enabled 를 켜려면 cand.path(PSD 또는 폴더 경로)가 있어야 합니다")
        _check_mapping(out["mapping_json"])
    elif section == "prepare":
        try:
            _prompt().select_variants({}, out["variants"])  # 노드 Prepare 의 형식 검사와 같음
        except ValueError as e:
            raise ValueError(f"prepare.variants: {e}") from e
    elif section == "draft":
        if out["write_mode"] not in _DRAFT_WRITE_MODES:
            raise ValueError(f"draft.write_mode 는 {' · '.join(_DRAFT_WRITE_MODES)} 만 씁니다(preview·force 는 그래프): "
                             f"{out['write_mode']!r}")
        if deep:  # 선택지(apply 포함)는 _value 가 봤다 — 모델별 effort 조합은 노드 VALIDATE_INPUTS·run 과 같은 함수로
            dvlm = _vlm()
            try:
                dvlm.check_widgets(out["vlm"], out["effort"], out["image_detail"], out["write_mode"])
            except ValueError as e:  # vlm 메시지의 노드 태그는 떼고 절 이름을 붙인다
                raise ValueError(f"draft: {str(e).replace(dvlm._TAG, '').strip()}") from e
        if draft_rounds is not None:
            _check_round(out["draft_round"], draft_rounds)
    elif section == "export":
        prefix = out["filename_prefix"].strip() or "design_patch"  # 노드 Export 와 같은 해석
        if not _SAFE_PREFIX_RE.match(prefix) or ".." in prefix:
            raise ValueError(f"export.filename_prefix 에는 경로 구분자 없이 영문·숫자·한글·_ - . 만 쓸 수 있습니다: {prefix!r}")
    return out


def validate_section(section: str, values: Any, *, draft_rounds=None) -> dict:
    """POST·템플릿용 전체 검사(draft 는 vlm 지연 import). 정규화한 값을 돌려준다."""
    return _check_section(section, values, deep=True, draft_rounds=draft_rounds)


# ══════════════════════════════════════════════════════════════════════
# pipeline.json 읽기·쓰기
# ══════════════════════════════════════════════════════════════════════
def _path(root: str) -> str:
    return os.path.join(root, PIPELINE_NAME)


def _read_raw(root: str) -> bytes | None:
    """파일 바이트(없으면 None, 읽을 수 없으면(폴더·권한) ValueError — 깨진 파일처럼). Windows 는 열린 파일로 os.replace 를 못 하므로
    쓰기와 같은 잠금 안에서 읽는다."""
    with store.manifest_lock(root):
        try:
            with open(_path(root), "rb") as f:
                return f.read()
        except FileNotFoundError:
            return None
        except OSError as e:
            raise ValueError(f"{PIPELINE_NAME} 파일을 읽을 수 없습니다({e})") from e


def _parse(raw: bytes) -> dict:
    """바이트 → 검사한 문서. 깨졌으면 ValueError(JSON·schema·구조·값)."""
    try:
        doc = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ValueError(f"{PIPELINE_NAME} JSON 형식이 잘못되었습니다({e})") from e
    if not isinstance(doc, dict):
        raise ValueError(f"{PIPELINE_NAME} 최상위가 객체가 아닙니다")
    if doc.get("schema") != SCHEMA:
        raise ValueError(f"{PIPELINE_NAME} schema 가 {SCHEMA!r} 가 아닙니다({doc.get('schema')!r})")
    extra = sorted(str(k) for k in doc if k not in _DOC_KEYS)
    if extra:
        raise ValueError(f"{PIPELINE_NAME} 에 모르는 키: {', '.join(extra)}")
    rev = doc.get("rev")
    if isinstance(rev, bool) or not isinstance(rev, int) or rev < 1:
        raise ValueError(f"{PIPELINE_NAME} rev 가 1 이상 정수가 아닙니다: {rev!r}")
    if rev > _REV_MAX:
        raise ValueError(f"{PIPELINE_NAME} rev 가 너무 큽니다({rev} > {_REV_MAX} — 브라우저가 정확히 읽지 못함)")
    conf = doc.get("confirm")
    valid = {f"{s}.{k}" for s in SECTIONS for k in section_keys(s)}
    if not isinstance(conf, list) or any(not isinstance(c, str) or c not in valid for c in conf):
        raise ValueError(f"{PIPELINE_NAME} confirm 은 '절.키' 문자열 목록이어야 합니다: {conf!r}")
    out = {"schema": SCHEMA, "rev": rev, "confirm": list(conf)}
    for sec in SECTIONS:
        if sec not in doc:
            raise ValueError(f"{PIPELINE_NAME} 에 {sec} 절이 없습니다")
        try:
            out[sec] = _check_section(sec, doc[sec], deep=False)
        except ValueError as e:
            raise ValueError(f"{PIPELINE_NAME} {e}") from e
    return out


def load(root: str) -> dict | None:
    """pipeline.json → 검사한 문서 {schema, rev, confirm, 절 8개}. 없으면 None, 깨졌으면 ValueError. 파일을 쓰지 않는다."""
    raw = _read_raw(root)
    return None if raw is None else _parse(raw)


def pipeline_rev(root: str) -> int | None:
    """state 플래그용 rev(없거나 깨졌으면 None)."""
    try:
        doc = load(root)
    except ValueError:
        return None
    return None if doc is None else doc["rev"]


def _settings_of(doc: dict) -> dict:
    return {sec: copy.deepcopy(doc[sec]) for sec in SECTIONS}


def _ordered_paths(paths) -> list[str]:
    """키 경로를 절·키 순서로(중복 제거)."""
    order = [f"{s}.{k}" for s in SECTIONS for k in section_keys(s)]
    have = set(paths)
    return [p for p in order if p in have]


def current(root: str, m: dict | None = None, *, deep: bool = False, derived: dict | None = None) -> dict:
    """지금 설정 {"rev", "saved", "error", "settings", "confirm"} — 파일 값 / 없으면 rev 0·saved False·유도값(derive(deep)) /
    깨졌으면 rev None·error·settings None·confirm None. 파일을 쓰지 않는다. 라우트(view·plan·prompt)는 deep=True — 파일이 없을 때
    계획이 설정 패널(GET /pipeline)과 같은 유도값으로 계산되게. state 경로는 기본값(vlm 을 불러오지 않음)."""
    try:
        doc = load(root)
    except ValueError as e:
        return {"rev": None, "saved": True, "error": str(e), "settings": None, "confirm": None}
    if doc is None:
        der = derived if derived is not None else derive(root, m if m is not None else store.load_manifest(root), deep=deep)
        return {"rev": 0, "saved": False, "error": None, "settings": copy.deepcopy(der["settings"]),
                "confirm": list(der["confirm"])}
    return {"rev": doc["rev"], "saved": True, "error": None, "settings": _settings_of(doc), "confirm": list(doc["confirm"])}


def _salvage_rev(raw: bytes | None) -> int:
    """깨진 파일의 rev(읽히면) — 덮어쓴 뒤 rev 가 낡은 창의 rev 와 다시 같아지지 않게 그 뒤 번호를 쓴다(상한 이상이면 update 가 1 로)."""
    try:
        rev = json.loads((raw or b"").decode("utf-8-sig")).get("rev")
    except (UnicodeDecodeError, ValueError, AttributeError):
        return 0
    return rev if isinstance(rev, int) and not isinstance(rev, bool) and rev > 0 else 0


def update(root: str, rev: int | None, section: str, values: Any) -> dict:
    """POST /pipeline/settings: 절 하나를 통째 교체 → {"ok", "rev", "settings", "confirm", "drift"}.
    rev: 파일 없음 = 0 만(유도값 전체 + 이 절로 만들고 rev 1), 깨짐 = None 만(덮어쓰기), 있음 = 지금 rev 만 — 아니면 PipelineConflict.
    값 검사는 validate_section(draft_round = 유도값 또는 저장값만). 저장하면 confirm 의 그 절 항목을 지운다. 같으면 쓰지 않는다.
    파일을 만들 때의 유도값은 deep(선택지 밖 초안 모델은 vlm·effort·image_detail = 노드 기본값 + 확인) — 다시 저장할 수 없는 값을
    파일에 쓰지 않게."""
    if rev is not None and (isinstance(rev, bool) or not isinstance(rev, int) or rev < 0):
        raise ValueError(f"rev 는 0 이상 정수 또는 null 이어야 합니다: {rev!r}")
    vals = validate_section(section, values)  # 잠금 밖(draft 는 vlm 을 처음 읽을 수 있음) — draft_round 는 잠금 안에서
    m = store.load_manifest(root)
    der = derive(root, m, deep=True)
    with store.manifest_lock(root):
        raw = _read_raw(root)
        try:
            doc, broken = (None if raw is None else _parse(raw)), None
        except ValueError as e:
            doc, broken = None, e
        if broken is not None:
            if rev is not None:
                raise PipelineConflict(f"{PIPELINE_NAME} 이 깨져 있습니다 — 덮어쓰려면 rev 없이(null) 저장: {broken}")
            base_rev = _salvage_rev(raw)
        elif doc is None:
            if rev != 0:
                raise PipelineConflict(f"{PIPELINE_NAME} 이 없습니다(rev 0) — 새로고침")
            base_rev = 0
        else:
            if rev != doc["rev"]:
                raise PipelineConflict(f"실행 설정이 다른 창에서 바뀌었습니다(rev {rev} → {doc['rev']}) — 새로고침")
            base_rev = doc["rev"]
        if section == "draft":
            _check_round(vals["draft_round"], {der["settings"]["draft"]["draft_round"]} | ({doc["draft"]["draft_round"]} if doc else set()))
        if doc is None:
            new = {"schema": SCHEMA, "rev": base_rev, "confirm": list(der["confirm"]), **copy.deepcopy(der["settings"])}
        else:
            new = copy.deepcopy(doc)
        new[section] = vals
        new["confirm"] = [c for c in new["confirm"] if not c.startswith(section + ".")]
        if doc is None or new != doc:
            new["rev"] = base_rev + 1 if base_rev < _REV_MAX else 1  # 상한 이상(그런 rev 의 깨진 파일 포함)이면 1 — 저장한 파일이 깨진 파일로 읽히지 않게
            store.write_json_atomic(_path(root), {k: new[k] for k in _DOC_KEYS})
    settings = _settings_of(new)
    return {"ok": True, "rev": new["rev"], "settings": settings, "confirm": list(new["confirm"]), "drift": drift(settings, m)}


# ══════════════════════════════════════════════════════════════════════
# 처음 값 유도(§2.2)
# ══════════════════════════════════════════════════════════════════════
def _majority(values: list) -> tuple[Any, bool]:
    """최다값(같으면 먼저 나온 것)과 섞였는지."""
    c = Counter(json.dumps(v, sort_keys=True, ensure_ascii=False) for v in values)
    top = c.most_common(1)[0][0]
    return json.loads(top), len(c) > 1


def _variant_prefix(name: Any) -> str | None:
    head = str(name or "").split("_", 1)[0].upper()
    return head if re.fullmatch(r"V\d+", head) else None


def _derive_import(m: dict, out: dict, frm: dict, conf: set, notes: list) -> None:
    src = m.get("source") if isinstance(m.get("source"), dict) else {}
    if not src.get("psd"):
        conf.update(f"import.{k}" for k in section_keys("import"))
        notes.append("import: 매니페스트에 Import 기록이 없음 — PSD 경로를 확인(처음 Import 는 그래프에서)")
        return
    for key, sk in _IMPORT_SOURCE:
        v = src.get(sk)
        if isinstance(v, str):
            out[key] = _norm_path(v) if key in _IMPORT_PATHS else v
            frm[f"import.{key}"] = "manifest"


def _jobs_of(m: dict) -> list[dict]:
    return [j for j in m.get("jobs") or [] if isinstance(j, dict)]


def _jobs_prepare(jobs: list[dict]) -> dict:
    """jobs 에 남은 Prepare 설정 {키: 작업마다 값} — model·quality·n·calls_per_cell(= reps, 없으면 1)·size_rule(= size_info.rule).
    variants·ref_style 은 기록이 없다(옛 프로젝트 유도·갈라짐이 같이 쓴다)."""
    return {"model": [j.get("model") for j in jobs], "quality": [j.get("quality") for j in jobs],
            "n": [j.get("n") for j in jobs], "calls_per_cell": [j.get("reps") if j.get("reps") is not None else 1 for j in jobs],
            "size_rule": [(j.get("size_info") or {}).get("rule") if isinstance(j.get("size_info"), dict) else None
                          for j in jobs]}


def _derive_prepare(m: dict, out: dict, frm: dict, conf: set, notes: list) -> None:
    ps = m.get("prepare_settings")
    if isinstance(ps, dict) and ps:
        vals = {k: ps[k] for k in section_keys("prepare") if k in ps}
        vals.setdefault("calls_per_cell", 1)  # 생략 = 1(prepare_settings 계약)
        try:
            vals = _check_section("prepare", vals, deep=False)
        except ValueError as e:
            notes.append(f"prepare: 매니페스트 prepare_settings 를 쓸 수 없음({e}) — jobs 에서 읽음")
        else:
            out.update(vals)
            frm.update({f"prepare.{k}": "manifest" for k in vals})
            return
    jobs = _jobs_of(m)
    if not jobs:
        conf.update(f"prepare.{k}" for k in section_keys("prepare"))
        notes.append("prepare: Prepare 기록(prepare_settings·jobs)이 없음 — 노드 기본값(모델·품질·n 등 확인 필요)")
        return
    mixed = []
    for key, vs in _jobs_prepare(jobs).items():
        v, mix = _majority(vs)
        if v is None:
            conf.add(f"prepare.{key}")
            continue
        out[key] = v
        frm[f"prepare.{key}"] = "jobs"
        if mix:
            mixed.append(key)
            conf.add(f"prepare.{key}")
    prefixes = {p for p in (_variant_prefix(j.get("variant")) for j in jobs) if p}
    if prefixes and "V1" in prefixes and prefixes <= {"V1", "V4"}:
        out["variants"] = "V1,V4"  # V4 가 V1 과 같은 프롬프트면 Prepare 가 건너뛰어 jobs 에 남지 않음(노드 기본값)
    elif prefixes:
        out["variants"] = ",".join(sorted(prefixes, key=lambda p: int(p[1:])))
    frm["prepare.variants"] = "jobs" if prefixes else "default"
    conf.update(("prepare.variants", "prepare.ref_style"))
    notes.append("prepare: prepare_settings 없음(옛 프로젝트) — jobs 에서 읽음(variants·ref_style 은 기록이 없어 확인 필요"
                 + (f", 섞인 값: {', '.join(mixed)}" if mixed else "") + ")")


def _derive_draft(root: str, m: dict, out: dict, frm: dict, conf: set, notes: list) -> None:
    rows, n_mock = [], 0
    for crop in m.get("crops") or []:
        cid = crop.get("id") if isinstance(crop, dict) else None
        try:
            spec = store.load_spec_override(root, cid)
        except ValueError as e:
            notes.append(f"draft: 스펙을 읽지 못해 건너뜀({cid}): {e}")
            continue
        d = spec.get("_draft")
        if not isinstance(d, dict):
            continue
        be = d.get("backend")
        if be == "vlm_mock":  # _MOCK_API 로 강제된 openai 포함 — 원래 모델을 알 수 없음
            n_mock += 1
            continue
        if be == "vlm_openai" and isinstance(d.get("model"), str) and d["model"]:
            choice = f"openai/{d['model']}"
        elif be == "vlm_local_qwen":
            choice = _QWEN_CHOICE
        else:
            continue
        rd = d.get("round")
        rows.append({"vlm": choice, "effort": d.get("effort") or out["effort"],
                     "image_detail": d.get("image_detail") or "high",
                     "draft_round": rd if isinstance(rd, int) and not isinstance(rd, bool) and 0 <= rd <= 99 else 0})
    if rows:
        top, mix = _majority(rows)
        out.update(top)
        frm.update({f"draft.{k}": "spec_draft" for k in top})
        if mix:
            notes.append(f"draft: 크롭마다 초안 설정이 다름({len({json.dumps(r, sort_keys=True) for r in rows})}가지) — 가장 많은 것")
    else:
        conf.add("draft.vlm")
        notes.append("draft: 스펙 초안 기록이 없음" + (f"(mock 초안 {n_mock}개는 세지 않음)" if n_mock else "") + " — 노드 기본 vlm")
    out["credit_cap"] = _RECOMMENDED_CREDIT_CAP
    notes.append(f"draft: credit_cap 은 기록이 없어 권장 {_RECOMMENDED_CREDIT_CAP}(노드 기본값 0 = 상한 없음)")
    conf.add("draft.apply")  # apply 는 스펙에 기록되지 않음


def _derive_analyze(root: str, m: dict, out: dict, frm: dict, conf: set, notes: list) -> None:
    pref = store.load_board_pref(root)
    if pref is not None and all(k in pref for k in _ANALYZE_PARAMS):
        # 마지막 Analyze·Review 설정(board_pref.json — 보드가 ★ 합성·마스크 편집 기준으로 고르는 항목의 설정). 후보의 마지막 삽입 항목은
        # 설정을 A→B→A 로 되돌리면 B 다
        for k in _ANALYZE_PARAMS:
            out[k] = pref[k]
            frm[f"analyze.{k}"] = "manifest"
        return
    latest = []
    for per in (m.get("analysis") or {}).values() if isinstance(m.get("analysis"), dict) else ():
        if not isinstance(per, dict):
            continue
        # 후보의 마지막 항목(삽입 순 = 오래된 것 먼저 — 보드 _entry_for 가 pref 를 모를 때와 같음), dp-an/1 은 평평한 항목 하나
        ents = [per] if "params_hash" in per else [e for e in per.values() if isinstance(e, dict)]
        e = ents[-1] if ents else {}
        if isinstance(e.get("params"), dict):
            latest.append({k: e["params"].get(k) for k in _ANALYZE_PARAMS})
    if not latest:
        conf.update(f"analyze.{k}" for k in section_keys("analyze"))
        notes.append("analyze: 분석 기록이 없음 — 노드 기본값")
        return
    top, mix = _majority(latest)
    for k, v in top.items():
        if v is not None:
            out[k] = v
            frm[f"analyze.{k}"] = "manifest"
    if mix:
        notes.append("analyze: 후보마다 마지막 분석 설정이 다름 — 가장 많은 것")


def _derive_export(root: str, m: dict, s: dict, frm: dict, conf: set, notes: list) -> None:
    # 마지막 Export 가 낸 PSD 의 기록('변경 없음'으로 끝난 Export 포함 — 보드 ★ 합성·마스크 편집 창과 같은 Compose 설정)
    last = store.last_export(root, m)
    if last is None:
        conf.update(f"compose.{k}" for k in section_keys("compose"))
        conf.update(f"export.{k}" for k in section_keys("export"))
        notes.append("export: Export 기록이 없음 — 노드 기본값(filename_prefix = 프로젝트 폴더 이름)")
        return
    comp = last.get("compose") if isinstance(last.get("compose"), dict) else {}
    for k in section_keys("compose"):
        if k in comp:
            s["compose"][k] = comp[k]
            frm[f"compose.{k}"] = "manifest"
    opts = last.get("options") if isinstance(last.get("options"), dict) else {}
    for k in section_keys("export"):
        ok = "prefix" if k == "filename_prefix" else k  # 매니페스트 options 의 이름은 prefix
        if ok in opts:
            s["export"][k] = opts[ok]
            frm[f"export.{k}"] = "manifest"


def derive(root: str, m: dict, *, deep: bool = False) -> dict:
    """처음 값 유도(§2.2) → {"settings", "from", "confirm", "notes"}. 매니페스트·스펙·board_pref.json 만 읽는다(vlm 원장 아님, 쓰기 없음).
    절마다 유도한 값이 검사를 통과하지 못하면 그 절은 노드 기본값 + 전부 확인 필요 — draft 는 스펙 출처 조합(vlm·effort·image_detail)만
    (draft_round 는 스펙 출처 그대로(0–99 로 정리됨 — 읽기 전용이라 Studio 에서 되돌릴 수 없음), credit_cap 은 권장값 그대로).
    deep = 그 검사가 vlm 선택지·모델별 effort 까지(vlm 지연 import — 라우트 경로). deep=False 면 선택지에서 빠진 옛 초안 모델도
    draft.vlm 으로 남는다."""
    s = defaults(root)
    frm = {f"{sec}.{k}": "default" for sec in SECTIONS for k in section_keys(sec)}
    conf: set[str] = set()
    notes: list[str] = []
    _derive_import(m, s["import"], frm, conf, notes)
    _derive_prepare(m, s["prepare"], frm, conf, notes)
    _derive_draft(root, m, s["draft"], frm, conf, notes)
    _derive_analyze(root, m, s["analyze"], frm, conf, notes)
    _derive_export(root, m, s, frm, conf, notes)
    base = defaults(root)
    for sec in SECTIONS:
        try:
            s[sec] = _check_section(sec, s[sec], deep=deep)
        except ValueError as e:
            keys = _DRAFT_SOURCE if sec == "draft" else section_keys(sec)
            s[sec].update({k: base[sec][k] for k in keys})
            frm.update({f"{sec}.{k}": "default" for k in keys})
            conf.update(f"{sec}.{k}" for k in keys)
            notes.append(f"{sec}: 기록 값을 쓸 수 없음({e}) — " + ("·".join(keys) + " 은 " if sec == "draft" else "") + "노드 기본값")
    return {"settings": s, "from": frm, "confirm": _ordered_paths(conf), "notes": notes}


# ══════════════════════════════════════════════════════════════════════
# 갈라짐·경고·조회
# ══════════════════════════════════════════════════════════════════════
def _variant_tokens(v: Any) -> set[str]:
    """변형 토큰 집합 — Prepare(prompt.select_variants)와 같은 정규화: V<n> 접두 토큰만 대문자로, 밑줄 있는 정확한 이름은 대소문자 그대로
    (V2_isolate_a 와 V2_isolate_A 는 다른 변형을 고른다)."""
    return {t if "_" in t else t.upper() for t in re.split(r"[\s,;]+", str(v or "").strip()) if t}


def prepare_record(m: dict) -> tuple[str | None, dict]:
    """갈라짐이 비교할 마지막 Prepare 기록 → (출처, {키: 값}). "prepare_settings" = 키 7개(calls_per_cell 생략 = 1) / "jobs" = 옛 프로젝트
    (prepare_settings 없음 — derive 와 같은 jobs 최다값의 model·quality·size_rule·n·calls_per_cell, 최다값이 없음(None)인 키는 뺌.
    variants·ref_style 은 기록이 없어 없음 — confirm 이 맡는다) / None = 기록 없음({})."""
    ps = m.get("prepare_settings")
    if isinstance(ps, dict) and ps:
        return "prepare_settings", {k: ps.get(k, 1 if k == "calls_per_cell" else None) for k in section_keys("prepare")}
    rec = {}
    for key, vs in _jobs_prepare(_jobs_of(m)).items():
        v = _majority(vs)[0] if vs else None
        if v is not None:
            rec[key] = v
    return ("jobs" if rec else None), rec


def drift(settings: dict, m: dict) -> list[dict]:
    """설정과 매니페스트의 갈라짐 [{"section", "field", "manifest", "settings", "kind"}].
    import = 매니페스트 source 와 그대로 비교(Import 캐시 판정과 같게 대소문자 구분, Import 기록이 없으면 비교 안 함),
    prepare = 마지막 Prepare 기록(prepare_record — prepare_settings, 없으면 jobs. 기록이 없으면 비교 안 함)과 비교: 셀 설정이 다르면
    kind "cell", calls_per_cell 을 늘렸으면 "calls"(완료된 셀에도 빠진 rep 이 새 유료 호출 — runner.plan_calls), n 이 다르거나
    calls_per_cell 을 줄였으면 "cost"."""
    out: list[dict] = []
    src = m.get("source") if isinstance(m.get("source"), dict) else {}
    imp = (settings or {}).get("import") or {}
    if src.get("psd"):
        for key, sk in _IMPORT_SOURCE:
            mv = src.get(sk)
            if key in ("refs_dir", "glossary_path"):
                mv = mv or ""  # light 키 — 없음과 빈 값은 같은 Import
            if imp.get(key) != mv:
                out.append({"section": "import", "field": key, "manifest": mv, "settings": imp.get(key), "kind": "import"})
    _src, rec = prepare_record(m)
    pre = (settings or {}).get("prepare") or {}
    for key in section_keys("prepare"):
        if key not in rec:
            continue
        mv, sv = rec[key], pre.get(key)
        if (_variant_tokens(mv) == _variant_tokens(sv)) if key == "variants" else mv == sv:
            continue
        more = key == "calls_per_cell" and not (type(mv) is int and type(sv) is int and mv >= sv)  # 모르면(정수 아님) 늘린 쪽으로
        out.append({"section": "prepare", "field": key, "manifest": mv, "settings": sv,
                    "kind": "cell" if key in _PREPARE_CELL else "calls" if more else "cost"})
    return out


def path_warnings(settings: dict) -> list[dict]:
    """저장은 받지만 템플릿을 막는 경로 문제 [{"field", "reason": "no_psd"|"missing_path", "msg"}] — import 절(반영·Export)
    + 켠 후보 등록의 cand.path(dp_cand 를 담는 stage 만 — prompt_gate). 판정은 노드와 같게(Import: PSD·용어집 isfile, refs_dir isdir /
    Candidate In: psd_harvest isfile, folder isdir) — 노드가 그 자리에서 실패할 경로를 통과시키지 않게."""
    out: list[dict] = []
    imp = (settings or {}).get("import") or {}
    psd = imp.get("psd_path") or ""
    if not psd:
        out.append({"field": "import.psd_path", "reason": "no_psd", "msg": "PSD 경로가 비어 있음 — 그래프에서 처음 Import"})
    elif not os.path.isfile(psd):
        out.append({"field": "import.psd_path", "reason": "missing_path", "msg": f"PSD 파일이 없음: {psd}"})
    for key, isdir, what in (("refs_dir", True, "레퍼런스 폴더가"), ("glossary_path", False, "용어집 파일이")):
        p = imp.get(key) or ""
        if p and not (os.path.isdir(p) if isdir else os.path.isfile(p)):
            out.append({"field": f"import.{key}", "reason": "missing_path", "msg": f"{what} 없음: {p}"})
    cand = (settings or {}).get("cand") or {}
    cp = _strip_path(cand.get("path"))
    if cand.get("enabled") and cp:
        folder = cand.get("source") == "folder"
        if not (os.path.isdir(cp) if folder else os.path.isfile(cp)):
            out.append({"field": "cand.path", "reason": "missing_path",
                        "msg": f"후보 등록 {'폴더가' if folder else 'PSD 파일이'} 없음: {cp}"})
    return out


def recent_exports(m: dict, n: int = 10) -> list[dict]:
    """최근 Export n개(최근 것 먼저) [{"rev", "psd", "time", "layers", "problems", "bytes", "mask_groups", "mask_edits"}] — layers = Export 가
    쓴 레이어 수(§3.6 new), mask_groups·mask_edits = 그룹 마스크 수·그 가운데 ★ 마스크 편집 수(S4 결과 줄 '그룹 마스크 n (편집 k)').
    기록에 없는 키는 null(옛 Export)."""
    rows = [e for e in m.get("exports") or [] if isinstance(e, dict)]
    return [{k: e.get(k) for k in ("rev", "psd", "time", "layers", "problems", "bytes", "mask_groups", "mask_edits")} for e in reversed(rows[-n:])]


def view(root: str, m: dict | None = None) -> dict:
    """GET /pipeline 응답 {rev, saved, error, settings, confirm, derived, drift, warnings, exports, widgets, manifest_rev}. 유도는
    deep(설정 패널의 [매니페스트 값으로 다시]가 그대로 저장되게), widgets = settings_widgets(), manifest_rev = 매니페스트 rev(Studio 가
    GET /pipeline/prompt 의 expect '<rev>:<manifest_rev>' 를 만듦 — S3b 계약 §1). 파일을 쓰지 않는다."""
    m = m if m is not None else store.load_manifest(root)
    der = derive(root, m, deep=True)
    cur = current(root, m, derived=der)
    st = cur["settings"]
    return {**cur, "derived": der, "drift": drift(st, m) if st else [], "warnings": path_warnings(st) if st else [],
            "exports": recent_exports(m), "widgets": settings_widgets(), "manifest_rev": m["rev"]}


# ══════════════════════════════════════════════════════════════════════
# 템플릿 프롬프트(§2.3)
# ══════════════════════════════════════════════════════════════════════
def prompt_gate(root: str, stage: str, m: dict | None = None, *, cur: dict | None = None) -> dict | None:
    """템플릿을 내주기 전 설정 판정. None = 통과, 아니면 409 본문 {"error", "reason", "fields"?, "field"?}:
    pipeline_broken → confirm_settings(파일 없음 또는 confirm 에 prepare.*·import.* — 추정한 설정으로 첫 반영이 셀을 뒤집지 않게)
    → no_psd → missing_path(import 경로 + 템플릿에 dp_cand 가 들면 cand.path — export 빼고 전부. Candidate In 은 Import 가 커밋한
    뒤에 실패하므로 막지 않으면 Import 뒤·Prepare 전의 반쪽 상태가 남는다). cur = 템플릿을 만들 current() 값(라우트 — 판정한 값
    그대로 템플릿에 싣게), 없으면 읽는다. 파일을 쓰지 않는다."""
    if stage not in STAGES:
        raise ValueError(f"모르는 stage 입니다: {stage!r} (stage: {', '.join(STAGES)})")
    cur = current(root, m) if cur is None else cur
    if cur["error"] is not None:
        return {"error": f"실행 설정 파일이 깨졌습니다 — 설정 패널에서 다시 저장(덮어쓰기): {cur['error']}",
                "reason": "pipeline_broken"}
    need = [f for f in cur["confirm"] if f.split(".", 1)[0] in ("prepare", "import")]
    if not cur["saved"] or need:
        return {"error": "실행 설정 확인 필요 — 추정한 Import·Prepare 설정으로 반영하면 생성 조건이 바뀌어 다시 과금 대상이 될 수 있음"
                         + ("" if cur["saved"] else "(설정을 한 번 저장하세요)"),
                "reason": "confirm_settings", "fields": need}
    blocks = ("import.", "cand.") if "cand" in STAGE_NODES[stage] else ("import.",)  # cand.path 는 cand.enabled 일 때만 나온다
    rows = [w for w in path_warnings(cur["settings"]) if w["field"].startswith(blocks)]
    if any(w["reason"] == "no_psd" for w in rows):
        return {"error": "PSD 경로가 비어 있습니다 — 그래프에서 처음 Import", "reason": "no_psd"}
    if rows:
        return {"error": "; ".join(w["msg"] for w in rows), "reason": "missing_path", "field": rows[0]["field"],
                "fields": [w["field"] for w in rows]}
    return None


def _project_widgets(root: str) -> tuple[str, str]:
    """Project 위젯 (project = 폴더 이름, base_dir = 루트의 두 단계 위). 루트가 <base>/bmk_design_patch/<name> 모양이 아니면 ValueError."""
    if not isinstance(root, str) or not root.strip():
        raise ValueError(f"프로젝트 폴더 경로가 비어 있습니다: {root!r}")
    r = os.path.abspath(root.strip())
    parent, name = os.path.split(r)
    if os.path.normcase(os.path.basename(parent)) != os.path.normcase(store.PROJECTS_DIRNAME):
        raise ValueError(f"Design Patch 프로젝트 폴더(…/{store.PROJECTS_DIRNAME}/<project>)가 아닙니다: {root}")
    base = os.path.dirname(parent)
    if os.path.normcase(store.project_root(base, name)) != os.path.normcase(r):
        raise ValueError(f"프로젝트 폴더를 Project 위젯으로 다시 만들 수 없습니다: {root}")
    return name, base


def _only_tokens(only: str) -> list[str]:
    return [t for t in _ONLY_SPLIT_RE.split(str(only or "").strip()) if t]


def _is_style(tok: str) -> bool:
    return tok.lower() in ("style", "_style")


def strip_style(only: str) -> str:
    """Run 용 only(style 토큰을 뺀 쉼표 목록). Run 은 화풍 단위가 없다 — 계획(plan_calls)과 Run 위젯이 같은 문자열을 쓴다."""
    if not isinstance(only, str):
        raise ValueError(f"only 는 문자열이어야 합니다: {only!r}")
    return ",".join(t for t in _only_tokens(only) if not _is_style(t))


def only_string(selected_ids, style: bool = False, *, known=None) -> str:
    """only 정규형(§2.3): crop_id 명시 목록(정렬·중복 제거, 쉼표) + style 이면 ",style"(Draft 에서 화풍을 고르고 apply=full 일 때만).
    선택 0개는 ValueError — 빈 only 는 노드에서 "전부"라 승인 집합이 계획과 달라진다(계획·승인 단추를 끌 것).
    토큰은 밑줄이 있는 crop_id 꼴만(NNN 만·cell_key 접두로 읽히는 토큰·style 이름 거절), known 이 있으면 그 안의 것만."""
    if isinstance(selected_ids, str) or not isinstance(selected_ids, (list, tuple, set, frozenset)):
        raise ValueError(f"selected_ids 는 crop_id 목록이어야 합니다: {selected_ids!r}")
    if not isinstance(style, bool):
        raise ValueError(f"style 은 true/false 여야 합니다: {style!r}")
    known = None if known is None else set(known)
    ids = []
    for x in selected_ids:
        if not isinstance(x, str) or not _ONLY_ID_RE.fullmatch(x) or _is_style(x):
            raise ValueError(f"only 에는 crop_id(NNN_해시)만 씁니다: {x!r}")
        if known is not None and x not in known:
            raise ValueError(f"매니페스트에 없는 크롭입니다: {x}")
        if x not in ids:
            ids.append(x)
    if not ids:
        raise ValueError("고른 크롭이 없습니다 — 빈 only 는 '전부'라 계획·승인을 끕니다")
    return ",".join(sorted(ids) + (["style"] if style else []))


def _check_only(only: str) -> str:
    """유료 stage 의 only 토큰 검사(crop_id 꼴·style) → 쉼표로 다시 이은 문자열(순서 그대로 — 정규형은 only_string 이 만든다)."""
    toks = _only_tokens(only)
    for t in toks:
        if not (_ONLY_ID_RE.fullmatch(t) or _is_style(t)):
            raise ValueError(f"only 토큰은 crop_id(NNN_해시)·style 만 씁니다: {t!r}")
    return ",".join(toks)


def build_prompt(stage: str, settings: dict, root: str, *, approve: str = "", only: str = "") -> dict:
    """템플릿 프롬프트(API 형식) {노드 키: {"class_type", "inputs"}}. 불변 조건 T1–T5(§2.3):
    무료 stage(apply·export) = Run·SpecDraft 없음·approve/only 없음, run·draft = approve(12자 소문자 hex)와 only(명시 목록) 필수 —
    approve 는 그 단계 유료 노드 하나에만, reroll = approve "". adopt "", keep_loaded false. 연결 = [앞 노드 키, 0].
    위젯 값 = settings 절 값(쓰는 절만 validate_section — draft stage 만 vlm), inputs = 그 노드 입력 이름 정확히(아니면 ValueError)."""
    if stage not in STAGES:
        raise ValueError(f"모르는 stage 입니다: {stage!r} (stage: {', '.join(STAGES)})")
    if not isinstance(settings, dict):
        raise ValueError("settings 는 절 객체여야 합니다")
    if not isinstance(approve, str) or not isinstance(only, str):
        raise ValueError("approve·only 는 문자열이어야 합니다")
    approve, only = approve.strip(), only.strip()
    if stage in FREE_STAGES:
        if approve or only:
            raise ValueError(f"무료 stage({stage})에는 approve·only 를 싣지 않습니다")
    else:
        only = _check_only(only)  # 같은 정규 only 문자열 — Run 에는 style 을 뺀 것(strip_style)이 들어간다
        if stage == "reroll":
            if approve:
                raise ValueError("reroll 은 approve 없이(사전 승인 재굴림·다운로드 복구만)")
        else:
            if not _HASH_RE.fullmatch(approve):
                raise ValueError(f"{stage} 는 승인 해시(12자 소문자 hex)가 필요합니다: {approve!r}")
            if not (only if stage == "draft" else strip_style(only)):
                raise ValueError(f"{stage} 는 only(고른 크롭 명시 목록)가 필요합니다 — 빈 only 는 '전부'")
    name, base = _project_widgets(root)
    roles = list(STAGE_NODES[stage])
    used = [r for r in roles if r in SECTIONS]
    vals = {}
    for sec in used:
        if sec not in settings:
            raise ValueError(f"settings 에 {sec} 절이 없습니다")
        vals[sec] = validate_section(sec, settings[sec])
    if "cand" in roles and not vals["cand"]["enabled"]:
        roles.remove("cand")
    prompt: dict[str, dict] = {}
    prev = None
    for role in roles:
        cls = CLASS[role]
        if role == "project":
            inputs: dict[str, Any] = {"project": name, "base_dir": base}
        else:
            inputs = {"project": [NODE_KEYS[prev], 0]}
            if role in vals:
                inputs.update({k: v for k, v in vals[role].items() if k not in CONTROL_KEYS.get(role, ())})
            if role == "run":
                inputs.update(approve=approve if stage == "run" else "", only=strip_style(only))
            elif role == "draft":
                inputs.update(approve=approve, only=only, adopt="", keep_loaded=False)
        if set(inputs) != set(NODE_INPUTS[cls]):
            raise ValueError(f"{cls} 입력이 노드 위젯과 다릅니다: {sorted(set(inputs) ^ set(NODE_INPUTS[cls]))}")
        prompt[NODE_KEYS[role]] = {"class_type": cls, "inputs": {k: inputs[k] for k in NODE_INPUTS[cls]}}
        prev = role
    return prompt
