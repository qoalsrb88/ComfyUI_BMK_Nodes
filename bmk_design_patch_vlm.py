"""BMK Design Patch VLM — Spec Draft 노드(M3)의 VLM 초안기(문맥·키·프롬프트·원장·승인·백엔드·정규화·병합·보고서). 노드 없음.

배경
----
Prepare 는 크롭마다 specs/<crop_id>.json(사람이 고치는 스펙 오버라이드)을 읽어 GPT 편집 프롬프트를 렌더한다.
Spec Draft 노드는 그 파일의 초안을 VLM 으로 쓴다: 크롭 캔버스 + NNN_ 레퍼런스 + 한국어 레이어 라벨 + 용어집 → JSON
→ 정규화 → 필드 단위 3-way 병합 → 렌더 점검 → 원자 저장. 지키는 것 세 가지:
(1) 사람이 고친 값은 조용히 덮지 않는다 (2) 돈은 승인 해시가 맞을 때만 쓴다 (3) 입력이 같으면 다시 큐에 넣어도 호출 0건.
명세: H:\\BmkNodeDesign\\MultiLayerCropEdit\\_proto\\M3_SPEC.md §2·§4·§5, 근거: _proto\\design_m3\\report_*.md.
이 모듈은 표준 라이브러리·numpy·PIL + 같은 패키지 store / prompt / runner 만 import 한다.
torch·transformers·comfy·comfy_api_nodes 는 백엔드가 호출 시점에 읽는다(folder_paths 는 쓰지 않음 — 모델 경로는 main 이 넘김).

키와 승인
---------
- 캐시 키 = sha256(보낼 요청 그 자체의 정규 JSON)[:24]: 버전·백엔드·모델·effort·detail·프롬프트 지문·사용자 텍스트·
  이미지(역할, 이름, 원본 pixel_sha, 전송 크기)·draft_round. 스펙 내용(_hint 제외)·write_mode·only·동시성·상한은 빠진다.
- 승인 해시 = runner.pending_hash(["backend:<vlm_openai|vlm_local_qwen|vlm_mock>"] + ["<crop_id>:<key>:a<n>", …]).
  모든 백엔드가 승인 대상이다(local_qwen 은 ComfyUI 모델을 내리므로). 승인 없이 하는 일 = 무료 재폴링·재파싱·원장 캐시 병합.
- 시도 번호 a<n>: 과금됐을 수 있는 실패(failed·orphan)는 n+1, 과금 전 거부(rejected·req 만)는 n 유지.

원장 (<root>/vlm/<crop_id>/<key>/a<n>.<kind>.json, 화풍은 crop_id = "_style")
------------------------------------------------------------------------------
    req       호출 전: 설정·본문 요약(base64 없음)·추정값·전송 단계
    inflight  POST 직전(pid·세션) / created  POST 응답 즉시(response_id) / resp  폴링 완료(원문·usage·크레딧)
    done      추출 성공(text, parsed, repairs, credits, elapsed_s, transport)
    failed    과금됐을 수 있는 실패(error, status, phase, maybe_billed, retryable) / rejected  과금 전 거부(시도 유지)
- 레코드마다 _seq(같은 시도 안 쓰기 순서)와 at(시각)을 붙인다. 상태 = done 이 있으면 done, 없으면 가장 나중에 쓴 레코드.
  (rejected 뒤 같은 시도를 다시 보내면 req·inflight 가 더 나중이 된다 — 노드는 원장 파일을 지우지 않는다.)
- 백엔드 call() 은 원장을 직접 쓴다: 성공이면 done 까지 쓰고 CallResult, 실패면 failed / rejected 를 쓰고
  runner.CallError(status, phase = create|poll|result|parse, maybe_billed)를 낸다. 폴링 중 실패는 created 를 그대로 두고
  phase="poll" CallError(다음 실행 무료 재폴링) — 단 GET 4xx(401/402/403/408/429 제외)는 failed(poll, maybe_billed).
  폴링 마감 때 created 에 poll_status(마지막 상태)·poll_stalls 를 덧쓴다(response_id·at 그대로). 재폴링 마감은 created 부터
  effort 마감의 남은 시간, 지났으면 REPOLL_SHORT_S. 같은 비종료 상태로 마감까지 간 재폴링이 REPOLL_MAX_STALLS 번 이어지면
  failed(poll, maybe_billed, retryable) — 다음 계획이 승인 필요한 재시도를 낸다(GET 오류·401 로 끝난 재폴링은 세지 않음).
  인터럽트는 같은 예외를 다시 낸다(create 중이면 inflight 만 남음 = orphan). local_qwen 은 과금이 없어 생성 중 인터럽트는
  rejected, 로컬 오류는 failed(maybe_billed False) — orphan 으로 남기지 않는다.
- runner 원장(cands/<24hex>/)과 겹치지 않는다: runner._ledger_cells / plan_calls 결과는 바뀌지 않는다.

mock (MockVLM)
--------------
- 활성: openai 선택 + runner.mock_enabled(root)(환경변수 BMK_DP_MOCK_API 또는 <base_dir>/bmk_design_patch/_MOCK_API),
  또는 vlm 콤보 "mock". 옵션 = _MOCK_API JSON 의 "vlm" 키(vlm_mock_options): {"delay_s", "credits", "fail_plan"}.
- fail_plan = {crop_id | "_style": [동작, …]}. 동작은 그 MockVLM 인스턴스가 해당 크롭으로 보내는 순서대로 하나씩 소비한다
  (create 단계 동작은 전송 때, poll 단계 동작은 폴링 때. repoll 은 소비하지 않는다). 노드는 실행마다 새 인스턴스를 만든다.
  create: 400format(사다리 1단 하강) 400background(background 빼고 재전송) 400 401 500create interrupt_create crash_after_create
  poll:   poll401 poll404 poll_deadline interrupt_poll incomplete refusal badjson

병합과 쓰기 (§5)
---------------
- 경로 p(crop.preserve, A.detail …)마다 B = _draft.written[p], C = 파일 값, N = 정규화한 새 값으로 3-way 병합한다.
  fill 은 새 크롭·새 타깃만 채우고(C==B 유지), refresh 는 손대지 않은 필드를 갱신하고, force 는 노드 소유 필드를 전부
  새 값으로 바꾼다(백업). 사람이 고친 필드는 유지하고 새 값은 _draft.pending, 사람이 지운 필드는 _draft.deleted.
- style_note 는 specs/_project.json 이 정본이다. C==B 인 크롭은 모든 모드에서 프로젝트 값을 따라간다
  (project_style: 문자열 = 그 값, None = 키 없음 → 지움, "" = 전파 끔).
- 쓰기 전에 실제 spec_from_manifest → validate_spec → variants 로 렌더를 점검하고(render_check), 정규 dict 가 같으면
  파일을 다시 쓰지 않는다(write_spec — manifest_lock 안에서 다시 읽고 병합).

노드가 부르는 순서 (§3.3)
------------------------
    check_widgets → parse_vlm_choice → (family 결정: openai + runner.mock_enabled → "mock") → make_vlm
    크롭마다 build_crop_ctx → build_request, build_style_ctx → build_style_request → plan → approval(approve, backend.name)
    → to_run 이 있으면 run_units(reqs + {STYLE_ID: style_req})
    → 화풍 done → normalize_style(repairs) → write_spec(_project, merge_project), 크롭마다 mode = merge_mode(파일, mode,
      done 의 키) → done → normalize(constraints_for(ctx, None if mode == "force" else 파일), repairs = done 의 수리 목록)
      → merge_spec → render_check → write_spec(preview 는 쓰지 않음)
    → plan / approval 다시(남은 대기·해시) → format_report · review_markdown → finally: keep_loaded 가 아니면
    unload_cached()(vlm 이 무엇이든). merge_spec 의 provenance 에는 원장 done 의 transport 를 넣는다(_draft.transport).

버전 이력
---------
v1 (2026-10, M3) — 문맥·키·프롬프트(RP §7 + S1–S9, U1–U6)·스키마(L2/L1/L0)·비용 추정·원장·계획/승인·
  백엔드 3종(openai 사다리·background·재폴링 / local_qwen bf16 sdpa / mock)·JSON 추출·정규화(RP §9 + N0–N12, 뒤에 N13–N15)·
  3-way 병합·렌더 점검·스펙 쓰기·보고서·review.md.
v1.1 (2026-10-07, 리뷰 반영) — 정규화 dp-draft-norm/2(프롬프트·스키마·키는 그대로, 원장 초안은 다시 정규화된다):
  N3 사람 refs 가 있으면 mode 를 쓰지 않는다(VLM self_restore 는 suggestions.mode + self_restore_vs_human_refs).
  S() 가 Qwen 골격 "<...>"·부정구("nothing to ignore")·관사/전치사만 남은 값·지운 뒤 매달린 전치사를 비운다.
  N6 허용 밖 전치사(in, under, next to …)로 시작하는 location 은 그대로. must_not·N1 글자 판정은 단어 단위
  (must_not_in_detail 플래그). N9 는 ref_ignore 가 렌더되는 첫 소유자에 쓴다. N8 머리 명사 = 첫 전치사 앞 명사구.
  잘린 출력(repairs truncated*): extract_json 이 "truncated_string" 을 더 남기고, normalize(repairs=)가 끊긴 마지막
  문자열을 버리고 confidence low + truncated_output. render_check 린트 '<...>'·'the the'/'the nothing'.
v1.2 (2026-10-07, 리뷰 반영 — 병합·계획·원장·백엔드·보고서. 키·프롬프트·스키마는 그대로):
  force 는 키마다 1회(_draft.forced_key, merge_mode): 같은 키로 다시 실행되면 fill 규칙·_accepted 존중. force 정규화는 스펙의
  사람 mode·refs 를 제약으로 쓰지 않는다(호출자가 constraints_for(ctx, None)). fill 사유는 전부 본다(_fill_reasons —
  타깃 추가와 _hint 변경·mock 교체가 겹쳐도 refresh 규칙). _draft.labels(타깃별 라벨·name_en)와 지금 라벨이 다르면
  "타깃 변경(A)" — 그 타깃만 refresh 규칙. 사람 refs 가 있으면 노드가 쓴 mode 양보. fill 에서 값을 받지 않은 타깃은 _review
  유지. 화풍도 mock 초안 교체·갱신 가능 보고. 화풍 전파만 받은 _draft 는 한 번에 고정점. paste_layer_name 은 기존 타깃 문구를
  그대로 두고 새 제안만 붙인다. openai: created 를 caps 보다 먼저 쓰고 caps 쓰기는 실패해도 경고만, 폴링 GET 4xx(401/402/
  403/408/429 제외) = failed(maybe_billed, 재시도 승인). Qwen: CUDA 장치만(device_map = 그 장치), 이벤트 루프 스레드에서 동기
  실행, max_new_tokens 에서 EOS 없이 잘리거나 truncated 수리면 1회 재질문, 그래도 max_new_tokens 에서 잘리면 failed(result), 로컬 실패는
  failed(maybe_billed False)·인터럽트는 rejected(과금 없음), unload_cached()(모듈 수준, torch.cuda.empty_cache). mock 원장
  settings 의 model = "mock". run_units: 크레딧 미보고 과금 단위는 단위 최대로 cap 에 셈, 발사 판정에 이번 단위 최대 포함.
  보고서: 초안이 아직 없는 크롭은 한 줄로 묶음.
v1.3 (2026-10-08, 2차 리뷰 반영 — 키·프롬프트·스키마·렌더러 그대로, 정규화 표시는 dp-draft-norm/2 유지):
  S() 부정구: defects·canvas_now 도 그대로, must_not "no X"(X 명사구) → "the X"(must_not_neg_rewritten), 그 밖 must_not
  부정구·다른 필드의 부정구는 플래그(must_not_neg_dropped / negation_dropped — ref_ignore·occluded_by 는 조용히 비움).
  지운 자리표시가 머리 명사였으면("<...> of the sleeve", "the <...> of …") 값 전체를 비움, 앞 접속사 제거.
  N6 _location: 분사구·where·close/opposite/up/down 은 그대로(location_kept), 한정사 앞은 "on ", 기능어·조동사로 시작하면
  버림(location_dropped). render_check 린트 'on the tucked/where/of …'. N9 는 렌더되는 소유자 모두에 같은 값.
  merge_spec: _draft.target_keys[tid](값·_review 가 나온 키가 _draft.key 와 다른 타깃만) — 타깃 추가·타깃 변경 뒤 같은 병합이
  고정점. OpenAIVLM 재폴링 마감·진척 없는 재폴링 상한(REPOLL_SHORT_S, REPOLL_MAX_STALLS). 보고서: credit_cap 이 1건 최대
  추정보다 작아 아무것도 발사하지 않았으면 그 값을 알림(RunReport.cap_need).
v1.4 (2026-10-13, GPT Image A/B·커밋 전 리뷰 반영 — 키·프롬프트·스키마·렌더러 그대로, 정규화 dp-draft-norm/3):
  기본 vlm openai/gpt-6-sol · effort xhigh(사용자 결정). N14: VLM 의 shape locked → moderate(_review.shape_suggestion),
  경고 preserve_in_target(in·inside·within·into 뒤가 타깃 — 겹침·주변 표현 제외)·ref_object_singular·add_but_visible. N15: 렌더되는
  사람 refs 와 다른 이미지를 보고 쓴 ref 필드·detail·must_not·exact_ref 는 렌더하지 않음(refs_differ_from_human,
  human_ref_not_shown, 신뢰 낮음) — 같은 키의 값이면 병합은 fill 이어도 그 타깃만 refresh 규칙. N8 은 렌더하지 않은 ref_object 도 머리 명사로.
  merge_spec: 드라이런(draft None) 병합은 written 슬롯을 만들지 않음(그 뒤에도 "타깃 추가"). check_vlm_effort(VALIDATE_INPUTS 용).
  write_spec: 한 번만 읽고 읽기까지 막은 잠금도 OSError, 쓰지 못한 force 의 백업은 지움. 보고서: [신뢰 낮음] 줄도 모든 플래그, 승인 안내가
  버튼(부분 실행)과 직접 입력(전체 실행)을 구분, review.md 상태 '쓰지 않음'.
"""

from __future__ import annotations

import asyncio
import base64
import copy
import functools
import gc
import hashlib
import importlib.metadata
import io
import itertools
import json
import logging
import math
import os
import re
import shutil
import statistics
import sys
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Callable

import numpy as np
from PIL import Image

try:
    from . import bmk_design_patch_prompt as dprompt
    from . import bmk_design_patch_runner as runner
    from . import bmk_design_patch_store as store
except ImportError:  # 단독 실행/테스트
    import bmk_design_patch_prompt as dprompt
    import bmk_design_patch_runner as runner
    import bmk_design_patch_store as store

logger = logging.getLogger(__name__)

_TAG = "[ComfyUI_BMK_Nodes::DesignPatch]"

DRAFT_VERSION = "dp-draft/1"          # 프롬프트·스키마 계약(키에 포함). 문구를 바꾸면 올린다
NORM_VERSION = "dp-draft-norm/3"      # 정규화 규칙(키 밖, _draft.normalizer 에 기록)
PASS_PLAN = "single"
LEDGER_DIR = "vlm"; STYLE_ID = "_style"; IMG_SUBDIR = "vlm/img"
PROJECT_SPEC_ID = "_project"; BACKUP_SUBDIR = "_backup"; CAPS_FILE = "_vlm_caps.json"   # root 의 부모 폴더
RESPONSES_PATH = "/proxy/openai/v1/responses"
VLM_CHOICES = ("openai/gpt-6-astra", "openai/gpt-6-sol", "openai/gpt-5.6-sol", "openai/gpt-5.5-pro",
               "local_qwen/Qwen3-VL-8B-Instruct", "mock")
# 노드 기본값(2026-10-13 사용자 결정): sol/xhigh 는 실측 S 85.1·크롭당 ≈ 27 크레딧, 품질을 더 올릴 때 astra/high(S 84.2, 치명 0, ≈ 80)
DEFAULT_VLM = "openai/gpt-6-sol"
DEFAULT_EFFORT = "xhigh"
BACKEND_NAMES = {"openai": "vlm_openai", "local_qwen": "vlm_local_qwen", "mock": "vlm_mock"}
EFFORTS = ("low", "medium", "high", "xhigh", "max")
MODEL_EFFORTS = {"gpt-6-astra": ("low", "medium", "high", "xhigh", "max"),
                 "gpt-6-sol": ("low", "medium", "high", "xhigh", "max"),
                 "gpt-5.6-sol": ("low", "medium", "high", "xhigh", "max"),
                 "gpt-5.5-pro": ("medium", "high", "xhigh")}          # nodes_openai.py:65-88 ('none' 제외)
PRICE_PER_1K = {"gpt-6-astra": (0.0143, 0.0715), "gpt-6-sol": (0.00286, 0.0143),
                "gpt-5.6-sol": (0.005, 0.03), "gpt-5.5-pro": (0.03, 0.18)}  # nodes_openai.py:832-935
IMAGE_DETAILS = ("high", "original"); WRITE_MODES = ("fill", "refresh", "preview", "force")
OUT_TYPICAL = {"low": 3000, "medium": 6000, "high": 10000, "xhigh": 20000, "max": 30000}
MAX_OUTPUT_TOKENS = {"low": 16000, "medium": 24000, "high": 32000, "xhigh": 64000, "max": 100000}
POLL_DEADLINE_S = {"low": 900, "medium": 900, "high": 1200, "xhigh": 2400, "max": 5400}
CREATE_MAX_RETRIES = 0; POLL_INTERVAL_S = 5; POLL_TIMEOUT_S = 120; REPOLL_BILLABLE = False   # ii-4 결과로만 바꾼다
REPOLL_SHORT_S = 60; REPOLL_MAX_STALLS = 3   # created 부터 effort 마감이 지난 뒤의 재폴링 마감 / 진척 없는 재폴링 연속 상한
TEXT_OVERHEAD_TOKENS = 3000; STYLE_OUT_FACTOR = 0.5; CALIB_MIN = 3; CALIB_KEEP = 20
MAX_REFS_SHOWN = 6; CANVAS_LONG = (1024, 1536); UPSCALE_MAX = 4.0; REF_LONG_MAX = 768; REF_SHORT_MIN = 384
STYLE_LONG = 1536; STYLE_CROPS = 3; MAX_SUGGESTED = 3
QWEN_NEED_BYTES = int(19.5 * 2**30); QWEN_MAX_NEW_TOKENS = 4096; QWEN_FACTOR = 32; QWEN_ATTN = "sdpa"
OWNED_CROP = ("style_note", "preserve")
OWNED_TARGET = ("name", "mode", "action", "refs", "ref_object", "ref_ignore", "ref_view", "detail", "must_not",
                "defects", "location", "occluded_by", "shape", "text", "bbox")
NEVER_WRITE = ("canvas", "guide", "before_paste", "ref_style", "notes", "guide_color", "guide_type")

# main·js·test 가 참조하는 공유 이름(§10 경계 규칙)
UI_KEY = "bmk_dp_draft"   # 노드 ui 키
BRANCH_KEY = "draft"      # 핸들 가지 키(main _BRANCH_KEYS)
SCHEMA_ECHO = "dp-draft-1"

_KEY_RE = re.compile(r"[0-9a-f]{24}")
_LEDGER_RE = re.compile(r"a(\d+)\.(req|inflight|created|resp|done|failed|rejected)\.json")
_LEDGER_KINDS = ("req", "inflight", "created", "resp", "done", "failed", "rejected")
_KIND_RANK = {k: i for i, k in enumerate(_LEDGER_KINDS)}  # _seq 가 없는(손상) 레코드끼리의 순서
_SIDE_WORDS = {"right": "right", "오른쪽": "right", "left": "left", "왼쪽": "left"}
_SIDE_SPLIT_RE = re.compile(r"[^0-9a-z\uac00-\ud7a3]+")
_EFFORT_SECONDS = {"high": 150, "xhigh": 300, "max": 600}   # 그 밖 90
_QWEN_LOAD_S = 40; _QWEN_UNIT_S = 25   # RL §6


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _family(backend: str) -> str:
    """"openai" | "local_qwen" | "mock" — 백엔드 이름(vlm_openai …)도 받는다."""
    if backend in BACKEND_NAMES:
        return backend
    for fam, name in BACKEND_NAMES.items():
        if backend == name:
            return fam
    raise ValueError(f"{_TAG} 알 수 없는 VLM 백엔드: {backend!r}")


# ══════════════════════════════════════════════════════════════════════
# 예외
# ══════════════════════════════════════════════════════════════════════
class ParseError(ValueError):
    """VLM 출력에서 JSON 객체를 꺼내지 못함(관용 수리까지 실패)."""


class VramError(RuntimeError):
    """local_qwen 을 올릴 VRAM 이 모자람(아무것도 내리지 않고 거절했거나, 내린 뒤에도 모자람)."""


# ══════════════════════════════════════════════════════════════════════
# 문맥·요청 (§2.3)
# ══════════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class SentImage:
    role: str               # "canvas" | "ref" | "base" | "crop"
    name: str               # canvas/crop: crop_id, ref: refs_dir 기준 basename, base: "base"
    src_path: str           # 원본(평탄화 전) 절대 경로
    src_sha: str            # 평탄화 결과 store.pixel_sha — canvas 는 Prepare 의 sha_of 와 같은 값
    sent_wh: tuple[int, int]
    side: str | None        # "left" | "right" | None (보는 사람 기준, 파일명 토큰)
    cutout: bool            # store.is_ref_cutout


@dataclass(frozen=True)
class TargetIn:
    tid: str
    label: str
    name_en: str
    gloss: str | None              # store.translate(label, glossary)
    human_mode: str | None         # 매니페스트 mode ≠ "ref_correct" 이면 그 값
    human_refs: tuple[str, ...]    # 매니페스트 refs(비어 있지 않으면 사람 값)
    legacy: bool                   # crop.layer_name 에 '|' 없음


@dataclass
class CropCtx:
    crop_id: str
    name: str
    part: str
    nnn: str
    side: str | None
    layer_name: str
    canvas: SentImage
    canvas_orig_wh: tuple[int, int]
    pad_sides: list[str]
    refs: list[SentImage]
    refs_hidden: list[str]
    targets: list[TargetIn]
    gloss_hints: list[tuple[str, str]]   # ≤10, store._gloss_norm 키가 part·라벨에 포함되는 항목
    hint: str                            # 현재 스펙의 _hint
    spec: dict | None
    spec_error: str | None
    skip_reason: str | None
    warnings: list[str] = field(default_factory=list)   # 레퍼런스 없음·읽기 실패 등(보고서용)


@dataclass
class Request:
    kind: str                     # "crop" | "style"
    crop_id: str                  # style 은 "_style"
    key: str                      # 24hex
    system: str
    parts: list                   # [("text", str) | ("image", SentImage)]
    schema: dict                  # L2 스키마
    bbox_frame: tuple             # ("px", W, H) | ("q1000",)
    shown_refs: list[str]         # Image k(≥2) → shown_refs[k-2]
    user_text: str                # 키 계산용(이미지 자리는 "<image k>")
    # 아래는 build_request 가 채우는 설정(키에 이미 들어간 값 — plan 의 비용 추정·mock 교체 판정용)
    backend: str = "mock"         # family: "openai" | "local_qwen" | "mock"
    model: str = "mock"
    effort: str | None = None
    image_detail: str | None = None
    draft_round: int = 0


def parse_vlm_choice(choice: str) -> tuple[str, str]:
    """콤보 값 → (family, model). "openai/gpt-6-astra" → ("openai", "gpt-6-astra"), "mock" → ("mock", "mock")."""
    if choice not in VLM_CHOICES:
        raise ValueError(f"{_TAG} vlm 값이 목록에 없습니다: {choice!r} (선택지: {', '.join(VLM_CHOICES)})")
    if choice == "mock":
        return "mock", "mock"
    fam, model = choice.split("/", 1)
    return fam, model


def check_vlm_effort(choice: str | None, effort: str | None) -> None:
    """vlm·effort 조합 검사(ValueError, 한국어). None = 링크된 값(아직 모름) — 아는 쪽만 본다. 모델별 effort 는 openai 만."""
    fam, model = parse_vlm_choice(choice) if choice is not None else (None, None)
    if effort is None:
        return
    if effort not in EFFORTS:
        raise ValueError(f"{_TAG} effort 값이 잘못되었습니다: {effort!r} (선택지: {', '.join(EFFORTS)})")
    if fam == "openai" and effort not in MODEL_EFFORTS[model]:
        raise ValueError(f"{_TAG} {model} 은 effort {effort!r} 를 받지 않습니다(허용: {', '.join(MODEL_EFFORTS[model])})")


def check_widgets(choice: str, effort: str, image_detail: str, write_mode: str) -> None:
    """호출 전에 위젯 조합을 검사한다(ValueError, 한국어)."""
    check_vlm_effort(choice, effort)
    if image_detail not in IMAGE_DETAILS:
        raise ValueError(f"{_TAG} image_detail 값이 잘못되었습니다: {image_detail!r} (선택지: {', '.join(IMAGE_DETAILS)})")
    if write_mode not in WRITE_MODES:
        raise ValueError(f"{_TAG} write_mode 값이 잘못되었습니다: {write_mode!r} (선택지: {', '.join(WRITE_MODES)})")


def side_of(name: str) -> str | None:
    """이름의 좌우 토큰(보는 사람 기준): right|오른쪽 → "right", left|왼쪽 → "left". 토큰 경계만, 둘 다 있으면 None."""
    if not name:
        return None
    stem = unicodedata.normalize("NFC", os.path.basename(str(name)))
    root_, ext = os.path.splitext(stem)
    if ext.lower() in store.REF_EXTS:
        stem = root_
    found = {_SIDE_WORDS[t] for t in _SIDE_SPLIT_RE.split(stem.lower()) if t in _SIDE_WORDS}
    return found.pop() if len(found) == 1 else None


def parse_only(only: str) -> list[str]:
    """only 위젯 → 토큰 목록(쉼표·공백 분리, 순서 유지, 중복 제거). "style"(대소문자 무관, "_style" 포함)은 소문자로."""
    out: list[str] = []
    for t in re.split(r"[,\s]+", str(only or "").strip()):
        if not t:
            continue
        if t.lower() in ("style", STYLE_ID):
            t = "style"
        if t not in out:
            out.append(t)
    return out


def only_match(tokens: list[str], crop_id: str) -> bool:
    """빈 목록 = 전부. crop_id 일치, 또는 숫자 토큰이 crop_id 의 NNN 과 같음("2" == "002"). 화풍("_style")은 "style" 토큰."""
    if not tokens:
        return True
    if crop_id == STYLE_ID:
        return "style" in tokens
    nnn = crop_id.split("_", 1)[0]
    for t in tokens:
        if t == crop_id:
            return True
        if t.isascii() and t.isdigit() and nnn.isascii() and nnn.isdigit() and int(t) == int(nnn):
            return True
    return False


def pad_sides(finfo: dict, wh: tuple[int, int]) -> list[str]:
    """flatten_canvas 가 채운 영역(filled_bbox, 끝 배타)이 그 가장자리에 닿고 변 방향 길이 ≥90% 인 변들."""
    fb = (finfo or {}).get("filled_bbox")
    if not fb:
        return []
    W, H = (int(v) for v in wh)
    x0, y0, x1, y1 = (int(v) for v in fb)
    out = []
    if x0 <= 0 and y1 - y0 >= 0.9 * H:
        out.append("left")
    if x1 >= W and y1 - y0 >= 0.9 * H:
        out.append("right")
    if y0 <= 0 and x1 - x0 >= 0.9 * W:
        out.append("top")
    if y1 >= H and x1 - x0 >= 0.9 * W:
        out.append("bottom")
    return out


def _memo_get(memo: dict | None, tag: tuple, path: str, compute: Callable[[], tuple]) -> tuple:
    """memo[(tag…, 절대 경로, 크기, mtime_ns)] — 파일이 바뀌면 키가 바뀐다. memo 가 None 이면 매번 계산."""
    st = os.stat(path)
    k = tag + (os.path.normcase(os.path.abspath(path)), st.st_size, st.st_mtime_ns)
    hit = memo.get(k) if memo is not None else None
    if hit is None:
        hit = compute()
        if memo is not None:
            memo[k] = hit
    return hit


def _flat_canvas(path: str) -> tuple:
    """Prepare 와 같은 평탄화(RGBA → flatten_canvas) → (pixel_sha, (w, h), finfo)."""
    with Image.open(path) as im:
        rgb, finfo = dprompt.flatten_canvas(im.convert("RGBA"))
    return store.pixel_sha(rgb), (int(rgb.shape[1]), int(rgb.shape[0])), finfo


def _flat_ref(path: str) -> tuple:
    """Prepare 와 같은 평탄화(flatten_ref #808080) → (pixel_sha, (w, h), None)."""
    with Image.open(path) as im:
        rgb = dprompt.flatten_ref(im)
    return store.pixel_sha(rgb), (int(rgb.shape[1]), int(rgb.shape[0])), None


def _ref_name(r: str, refs_dir: str) -> str:
    """레퍼런스 표시 이름 = refs_dir 기준 상대 경로('/' 구분). 스펙 refs 에 그대로 쓴다(spec_from_manifest 가 refs_dir 과 합침)."""
    r = str(r)
    if os.path.isabs(r) and refs_dir:
        try:
            rel = os.path.relpath(r, refs_dir)
        except ValueError:  # 다른 드라이브
            rel = ""
        if rel and not rel.startswith(".."):
            r = rel
    return r.replace("\\", "/")


def _ref_key(r: str) -> str:
    """레퍼런스 비교 키(구분자·대소문자 무시) — 사람 refs(스펙·매니페스트 원문, 절대 경로 가능)와 보낸 이름을 맞출 때."""
    return os.path.normcase(os.path.normpath(str(r)))


def build_crop_ctx(root: str, m: dict, crop: dict, *, refs_dir: str, glossary: dict, backend: str,
                   memo: dict) -> CropCtx:
    """매니페스트 크롭 하나 → CropCtx(캔버스 평탄화·sha, 레퍼런스 정렬·숨김, 타깃, 용어집 적중, 스펙·_hint).
    memo = {(역할, abs path, size, mtime_ns): (src_sha, wh, finfo)} — 보드 큐마다 다시 실행되므로 캐시 적중이면 평탄화를 건너뛴다.
    평탄화 실패는 skip_reason(Prepare 의 "작업 없음"과 같음), 깨진 스펙 JSON 은 spec_error. 둘 다 build_request 는 되지만
    plan 이 건너뛴다."""
    cid = str(crop["id"])
    name = str(crop.get("name") or cid)
    layer = str(crop.get("layer_name") or name)
    side = side_of(name)
    warnings: list[str] = []
    spec, spec_error = _load_spec(root, cid)
    h = spec.get("_hint") if isinstance(spec, dict) else None
    hint = " ".join(h.split()) if isinstance(h, str) else ""

    skip, src, sha, wh, finfo = None, "", "", (0, 0), {}
    try:
        src = store.resolve_path(root, crop["source"])
        sha, wh, finfo = _memo_get(memo, ("canvas",), src, lambda: _flat_canvas(src))
    except (KeyError, OSError, ValueError) as e:  # 소스 없음 / 크롭 전체가 캔버스 밖(완전 불투명 픽셀 없음)
        skip = f"캔버스 평탄화 실패 — {_short(e)}"
    canvas = SentImage("canvas", cid, src, sha, sent_size(wh, "canvas", backend) if sha else (1, 1), side, False)

    # 레퍼런스 후보 = 매니페스트 사람 refs + refs_auto, 중복 제거 → (크롭과 반대 side, 원래 순서) 안정 정렬
    cands: list[str] = []
    for t in crop.get("targets") or []:
        cands += [str(r) for r in t.get("refs") or [] if r]
    cands += [str(r) for r in crop.get("refs_auto") or [] if r]
    cands = list(dict.fromkeys(cands))
    opp = {"left": "right", "right": "left"}.get(side or "")
    order = sorted(range(len(cands)), key=lambda i: (opp is not None and side_of(cands[i]) == opp, i))
    if cands and not refs_dir:
        warnings.append(f"refs_dir 가 비어 레퍼런스 {len(cands)}개를 보내지 않음(레퍼런스 0개)")
    refs: list[SentImage] = []
    hidden: list[str] = []
    for i in order:
        r = cands[i]
        rname = _ref_name(r, refs_dir)
        if len(refs) >= MAX_REFS_SHOWN:
            hidden.append(rname)
            continue
        path = r if os.path.isabs(r) else (os.path.join(refs_dir, r) if refs_dir else "")
        if not path:
            continue
        if not os.path.isfile(path):
            warnings.append(f"레퍼런스 파일 없음: {rname}")
            continue
        try:
            rsha, rwh, _ = _memo_get(memo, ("ref",), path, lambda: _flat_ref(path))
        except (OSError, ValueError) as e:
            warnings.append(f"레퍼런스 읽기 실패: {rname} ({_short(e)})")
            continue
        refs.append(SentImage("ref", rname, path, rsha, sent_size(rwh, "ref", backend), side_of(rname),
                              store.is_ref_cutout(os.path.basename(rname))))
    for w in warnings:
        logger.warning(f"{_TAG} VLM 초안 {cid}: {w}")

    legacy = store._PIPE_RE.search(layer) is None
    targets = []
    for t in crop.get("targets") or []:
        label = str(t.get("label") or "").strip()
        mode = str(t.get("mode") or "ref_correct")
        targets.append(TargetIn(tid=str(t.get("tid") or "A"), label=label, name_en=str(t.get("name_en") or "").strip(),
                                gloss=store.translate(label, glossary), human_mode=mode if mode != "ref_correct" else None,
                                human_refs=tuple(str(r) for r in t.get("refs") or [] if r), legacy=legacy))
    keys = [k for k in (store._gloss_norm(x) for x in [str(crop.get("part") or "")] + [t.label for t in targets]) if k]
    hints: list[tuple[str, str]] = []
    for k, v in (glossary or {}).items():
        nk = store._gloss_norm(k)
        if nk and any(nk in x for x in keys) and (k, v) not in hints:
            hints.append((k, v))
            if len(hints) >= 10:
                break
    return CropCtx(crop_id=cid, name=name, part=str(crop.get("part") or ""), nnn=str(crop.get("nnn") or ""), side=side,
                   layer_name=layer, canvas=canvas, canvas_orig_wh=tuple(int(v) for v in wh),
                   pad_sides=pad_sides(finfo, wh) if sha else [], refs=refs, refs_hidden=hidden, targets=targets,
                   gloss_hints=hints, hint=hint, spec=spec, spec_error=spec_error, skip_reason=skip, warnings=warnings)


def build_style_ctx(root: str, m: dict, *, backend: str, memo: dict) -> tuple[SentImage, list[SentImage]] | None:
    """화풍 호출 입력: (베이스 work 영역 SentImage, 매니페스트 순서 첫 STYLE_CROPS 크롭 캔버스). 베이스가 없으면 None.
    work 영역은 vlm/img/<sha12>.png 로 저장해 SentImage.src_path 로 쓴다(render_sent 는 RGB 그대로 읽는다)."""
    rel = m.get("base")
    if not rel:
        return None
    try:
        path = store.resolve_path(root, rel)
    except ValueError:
        return None
    if not os.path.isfile(path):
        return None
    rect = tuple(int(v) for v in m.get("work_rect") or ()) if len(m.get("work_rect") or ()) == 4 else ()

    def compute() -> tuple:
        with Image.open(path) as im:
            rgb = np.asarray(im.convert("RGB"))
        if rect:
            H, W = rgb.shape[:2]
            x0, y0 = max(0, rect[0]), max(0, rect[1])
            x1, y1 = min(W, rect[2]), min(H, rect[3])
            if x1 > x0 and y1 > y0:
                rgb = rgb[y0:y1, x0:x1]
        out = store.resolve_path(root, store.save_png_ca(root, IMG_SUBDIR, rgb))
        return store.pixel_sha(rgb), (int(rgb.shape[1]), int(rgb.shape[0])), out

    sha, wh, wpath = _memo_get(memo, ("base", rect), path, compute)
    if not os.path.isfile(wpath):  # memo 는 맞지만 저장본이 지워짐
        sha, wh, wpath = compute()
    base = SentImage("base", "base", wpath, sha, sent_size(wh, "base", backend), None, False)
    shots: list[SentImage] = []
    for crop in m.get("crops") or []:
        if len(shots) >= STYLE_CROPS:
            break
        try:
            src = store.resolve_path(root, crop["source"])
            csha, cwh, _ = _memo_get(memo, ("canvas",), src, lambda: _flat_canvas(src))
        except (KeyError, OSError, ValueError):
            continue
        shots.append(SentImage("crop", str(crop["id"]), src, csha, sent_size(cwh, "crop", backend),
                               side_of(str(crop.get("name") or "")), False))
    return base, shots


@functools.lru_cache(maxsize=8)
def _qwen_model_id(model_dir: str, mtime_ns: int) -> str:
    """local_qwen 키의 모델 표시: 이름|config.json sha8|transformers 버전|attn|max_new_tokens (transformers 는 import 하지 않음)."""
    try:
        with open(os.path.join(model_dir, "config.json"), "rb") as f:
            cfg = hashlib.sha256(f.read()).hexdigest()[:8]
    except OSError:
        cfg = "noconfig"
    try:
        tf = importlib.metadata.version("transformers")
    except importlib.metadata.PackageNotFoundError:
        tf = "none"
    return f"{os.path.basename(os.path.normpath(model_dir)) or 'Qwen3-VL-8B-Instruct'}|{cfg}|{tf}|{QWEN_ATTN}|{QWEN_MAX_NEW_TOKENS}"


def _key_model(fam: str, model: str, model_dir: str) -> str:
    if fam == "openai":
        return model
    if fam == "local_qwen":
        if not model_dir:
            return f"{model}|nodir|{QWEN_ATTN}|{QWEN_MAX_NEW_TOKENS}"
        try:
            mt = os.stat(os.path.join(model_dir, "config.json")).st_mtime_ns
        except OSError:
            mt = 0
        return _qwen_model_id(model_dir, mt)
    return "mock"


def _user_text(parts: list) -> str:
    """키 계산용 사용자 텍스트: 텍스트 그대로, 이미지 자리는 "<image k>"."""
    out, k = [], 0
    for kind, val in parts:
        if kind == "image":
            k += 1
            out.append(f"<image {k}>")
        else:
            out.append(val)
    return "\n".join(out)


def _finish_request(req: Request, model_dir: str) -> Request:
    fam = req.backend
    images = [[im.role, im.name, im.src_sha, int(im.sent_wh[0]), int(im.sent_wh[1])] for im in _images_of(req)]
    payload = {"v": DRAFT_VERSION, "pass": PASS_PLAN, "kind": req.kind, "backend": fam,
               "model": _key_model(fam, req.model, model_dir),
               "effort": req.effort if fam == "openai" else None,
               "detail": req.image_detail if fam == "openai" else None,
               "prompt": prompt_fingerprint(), "user": req.user_text, "images": images, "round": int(req.draft_round)}
    req.key = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:24]
    return req


def _join_words(items: list[str]) -> str:
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + f" and {items[-1]}"


def _target_line(t: TargetIn) -> str:
    nm = t.name_en or t.gloss
    english = _U_NAME_FIXED.format(name=nm) if nm else _U_NAME_FREE
    s = _U_TARGET.format(tid=t.tid, label=t.label, english=english)
    if t.legacy:
        s += _U_LEGACY
    if t.human_mode:
        s += _U_MODE.format(mode=t.human_mode)
    return s


def _ref_label(k: int, r: SentImage, crop_side: str | None) -> str:
    if r.side is None:
        side = _U_REF_SIDE["none"]
    elif crop_side is None:
        side = _U_REF_SIDE["crop_none"].format(side=r.side)
    elif r.side == crop_side:
        side = _U_REF_SIDE["same"]
    else:
        side = _U_REF_SIDE["opposite"].format(side=r.side)
    kind = _U_REF_KIND[0] if r.cutout else _U_REF_KIND[1]
    return _U_IMG_REF.format(k=k, name=os.path.basename(r.name), kind=kind, side=side)


def build_request(ctx: CropCtx, *, backend: str, model: str, effort: str, image_detail: str, draft_round: int,
                  model_dir: str = "") -> Request:
    """CropCtx → Request(시스템·사용자 텍스트·이미지 교차 parts·L2 스키마·bbox 틀·draft_key).
    model_dir 은 local_qwen 키(config.json sha8 + transformers 버전)에만 쓴다."""
    fam = _family(backend)
    W, H = (int(v) for v in ctx.canvas.sent_wh)
    frame = ("q1000",) if fam == "local_qwen" else ("px", W, H)
    w0, h0 = ctx.canvas_orig_wh
    pad = ""
    if ctx.pad_sides:
        pad = _U_PAD.format(sides=_join_words(ctx.pad_sides), region="regions are" if len(ctx.pad_sides) > 1 else "region is")
    gloss = "; ".join(f"{k} -> {v}" for k, v in ctx.gloss_hints) or "none"
    head = [_U_HEAD.format(crop=ctx.name, part=ctx.part, side=ctx.side or "none"), _U_TARGETS,
            *(_target_line(t) for t in ctx.targets),
            _U_GLOSS.format(gloss=gloss), _U_HINT.format(hint=ctx.hint or "none"),
            _U_CANVAS.format(w0=w0, h0=h0, w=W, h=H, pad=pad),
            _U_HIDDEN.format(names=", ".join(os.path.basename(n) for n in ctx.refs_hidden) or "none")]
    parts: list = [("text", "\n".join(head) + "\n\n" + _U_IMG_CANVAS.format(w=W, h=H)), ("image", ctx.canvas)]
    for k, r in enumerate(ctx.refs, 2):
        parts += [("text", _ref_label(k, r, ctx.side)), ("image", r)]
    ids = [t.tid for t in ctx.targets]
    final = _U_FINAL.format(ids=", ".join(ids), refs=", ".join(str(k) for k in range(2, len(ctx.refs) + 2)) or "none")
    if fam == "local_qwen":
        final += "\n" + _U_SKELETON.format(skeleton=json.dumps(_crop_skeleton(ids), ensure_ascii=False))
    parts.append(("text", final))
    req = Request(kind="crop", crop_id=ctx.crop_id, key="", system=system_prompt("crop", fam, frame), parts=parts,
                  schema=_crop_schema(ids, len(ctx.refs)), bbox_frame=frame, shown_refs=[r.name for r in ctx.refs],
                  user_text=_user_text(parts), backend=fam, model=model, effort=effort if fam == "openai" else None,
                  image_detail=image_detail if fam == "openai" else None, draft_round=int(draft_round))
    return _finish_request(req, model_dir)


def build_style_request(style_ctx, *, backend: str, model: str, effort: str, image_detail: str, draft_round: int,
                        model_dir: str = "") -> Request:
    """build_style_ctx 결과 → 화풍 Request(crop_id = STYLE_ID)."""
    fam = _family(backend)
    base, shots = style_ctx
    W, H = (int(v) for v in base.sent_wh)
    parts: list = [("text", _S_USER_BASE.format(w=W, h=H)), ("image", base)]
    for k, c in enumerate(shots, 2):
        parts += [("text", _S_USER_CROP.format(k=k, crop_id=c.name)), ("image", c)]
    final = _S_USER_FINAL
    if fam == "local_qwen":
        final += "\n" + _U_SKELETON.format(skeleton=json.dumps(_STYLE_SKELETON, ensure_ascii=False))
    parts.append(("text", final))
    frame = ("q1000",) if fam == "local_qwen" else ("px", W, H)
    req = Request(kind="style", crop_id=STYLE_ID, key="", system=system_prompt("style", fam, frame), parts=parts,
                  schema=_style_schema(), bbox_frame=frame, shown_refs=[], user_text=_user_text(parts), backend=fam,
                  model=model, effort=effort if fam == "openai" else None,
                  image_detail=image_detail if fam == "openai" else None, draft_round=int(draft_round))
    return _finish_request(req, model_dir)


@functools.lru_cache(maxsize=1)
def prompt_fingerprint() -> str:
    """sha256(SYSTEM + USER 템플릿 + 스키마 템플릿 + STYLE 템플릿 + DRAFT_VERSION)[:12]. 문구를 바꾸면 값이 바뀐다."""
    parts = [_SYSTEM_CROP, _BBOX_PX, _BBOX_Q1000, _U_HEAD, _U_TARGETS, _U_TARGET, _U_NAME_FIXED, _U_NAME_FREE, _U_LEGACY,
             _U_MODE, _U_GLOSS, _U_HINT, _U_CANVAS, _U_PAD, _U_HIDDEN, _U_IMG_CANVAS, _U_IMG_REF, *_U_REF_KIND,
             *(f"{k}={v}" for k, v in sorted(_U_REF_SIDE.items())), _U_FINAL, _U_SKELETON,
             json.dumps(_crop_skeleton(["A"]), sort_keys=True),
             json.dumps(_crop_schema(["A"], 1), sort_keys=True), json.dumps(_crop_schema(["A"], 0), sort_keys=True),
             _STYLE_SYSTEM, _S_USER_BASE, _S_USER_CROP, _S_USER_FINAL, json.dumps(_STYLE_SKELETON, sort_keys=True),
             json.dumps(_style_schema(), sort_keys=True), DRAFT_VERSION]
    return hashlib.sha256("\n\x1e\n".join(parts).encode("utf-8")).hexdigest()[:12]


def sent_size(wh: tuple[int, int], role: str, backend: str) -> tuple[int, int]:
    """전송 크기(§1 Q3). canvas·crop: 긴 변 [1024, 1536](작으면 Lanczos 확대 최대 4배, 크면 축소).
    ref: 긴 변 ≤768 축소, 짧은 변 <384 면 확대(최대 4배, 긴 변 상한이 우선). base: 긴 변 ≤1536 축소만.
    local_qwen 은 그 결과의 양 변을 32 배수로 맞춘다(processor 재리사이즈 방지)."""
    w, h = (int(v) for v in wh)
    if w <= 0 or h <= 0:
        raise ValueError(f"{_TAG} 이미지 크기가 잘못되었습니다: {wh!r}")
    long_, short = max(w, h), min(w, h)
    if role in ("canvas", "crop"):
        lo, hi = CANVAS_LONG
        s = min(lo / long_, UPSCALE_MAX) if long_ < lo else (hi / long_ if long_ > hi else 1.0)
    elif role == "ref":
        s = min(REF_LONG_MAX / long_, max(1.0, min(UPSCALE_MAX, REF_SHORT_MIN / short)))
    elif role == "base":
        s = min(1.0, STYLE_LONG / long_)
    else:
        raise ValueError(f"{_TAG} 알 수 없는 이미지 역할: {role!r}")
    sw, sh = max(1, round(w * s)), max(1, round(h * s))
    if _family(backend) == "local_qwen":
        sw, sh = (max(QWEN_FACTOR, round(v / QWEN_FACTOR) * QWEN_FACTOR) for v in (sw, sh))
    return sw, sh


def render_sent(img: SentImage) -> "Image.Image":
    """전송 직전에만 평탄화·리사이즈(키는 src_sha + sent_wh 로 이미 정해짐). canvas·crop = flatten_canvas,
    ref = flatten_ref(#808080), base = RGB 그대로. 크기가 다르면 LANCZOS."""
    with Image.open(img.src_path) as im:
        if img.role == "ref":
            rgb = dprompt.flatten_ref(im)
        elif img.role in ("canvas", "crop"):
            rgb, _ = dprompt.flatten_canvas(im.convert("RGBA"))
        else:
            rgb = np.asarray(im.convert("RGB"))
    pil = Image.fromarray(rgb)
    if pil.size != tuple(img.sent_wh):
        pil = pil.resize(tuple(int(v) for v in img.sent_wh), Image.LANCZOS)
    return pil


def _images_of(req: Request) -> list[SentImage]:
    return [p[1] for p in req.parts if isinstance(p, (tuple, list)) and len(p) == 2 and p[0] == "image"]


# ══════════════════════════════════════════════════════════════════════
# 계획·승인 (§2.4)
# ══════════════════════════════════════════════════════════════════════
@dataclass
class Unit:
    crop_id: str
    key: str
    attempt: int
    kind: str            # "crop" | "style"
    state: str           # "new" | "retry" | "orphan" | "rejected" | "repoll" | "reparse"
    reasons: list[str]   # "신규" "타깃 추가" "타깃 변경(A)" "_hint 변경" "입력 바뀜" "draft_round" "mock 초안 교체" "화풍 신규"
                         # "재시도(이전: …)" … (fill 사유가 여럿이면 모두, 첫 사유가 분류)
    billable: bool       # repoll·reparse = False (REPOLL_BILLABLE 가 True 면 repoll 도 True)
    est: tuple[int, int] # (보통, 최대) 크레딧 — openai. qwen 은 (초, 초)


def _spec_path(root: str, crop_id: str) -> str:
    if not isinstance(crop_id, str) or not store._CROP_ID_RE.match(crop_id):
        raise ValueError(f"{_TAG} crop_id 형식이 잘못되었습니다: {crop_id!r}")
    return os.path.join(root, "specs", f"{crop_id}.json")


def _load_spec(root: str, crop_id: str) -> tuple[dict | None, str | None]:
    """specs/<crop_id>.json → (dict | None(파일 없음), 오류 문구 | None). 깨진 JSON 은 줄 번호를 담은 오류."""
    path = _spec_path(root, crop_id)
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except FileNotFoundError:
        return None, None
    except OSError as e:
        return None, f"읽기 실패: {e}"
    return _parse_spec(raw)


def _parse_spec(raw: bytes) -> tuple[dict | None, str | None]:
    try:
        d = json.loads(raw.decode("utf-8-sig"))
    except UnicodeDecodeError as e:
        return None, f"UTF-8 이 아님: {e}"
    except json.JSONDecodeError as e:
        return None, f"JSON 오류 {e.lineno}행 {e.colno}열: {e.msg}"
    if not isinstance(d, dict):
        return None, "최상위가 객체가 아님"
    return d, None


def _short(msg: Any, limit: int = 80) -> str:
    return runner._short(msg, limit)


def _relabeled(d: dict | None, ctx: CropCtx) -> list[str]:
    """초안 뒤 라벨이 바뀐 타깃 id(같은 tid 가 다른 물체가 됨 — 레이어명 수정·타깃 순서 변경·지운 tid 재사용).
    _draft.labels 에 그 tid 의 기록이 있을 때만 비교한다(기록 전 초안은 판정하지 않음)."""
    labels = d.get("labels") if isinstance(d, dict) and isinstance(d.get("labels"), dict) else {}
    return [t.tid for t in ctx.targets if isinstance(labels.get(t.tid), dict) and labels[t.tid].get("label") != t.label]


def _fill_reasons(d: dict | None, ctx: CropCtx, backend: str) -> list[str]:
    """fill 에서 다시 초안할 사유 전부(없으면 []). d = 스펙의 _draft, backend = 지금 백엔드(family 또는 이름).
    key 가 없는 _draft(화풍 전파만 받은 크롭)는 초안이 없는 것으로 본다. 여럿이 겹칠 수 있다(타깃 추가 + _hint 변경 등 —
    merge_spec 이 그중 _hint 변경·mock 초안 교체가 있으면 refresh 규칙, 타깃 변경은 그 타깃만 refresh 규칙)."""
    if not isinstance(d, dict) or not d.get("key"):
        return ["신규"]
    written = d.get("written") if isinstance(d.get("written"), dict) else {}
    out = []
    if any(t.tid not in written for t in ctx.targets):
        out.append("타깃 추가")
    moved = _relabeled(d, ctx)
    if moved:
        out.append(f"타깃 변경({', '.join(moved)})")
    if str(d.get("hint") or "") != str(ctx.hint or ""):
        out.append("_hint 변경")
    if d.get("backend") == BACKEND_NAMES["mock"] and _family(backend) != "mock":
        out.append("mock 초안 교체")
    return out


def merge_mode(current: dict | None, mode: str, key: str | None) -> str:
    """force 는 키마다 1회(§5.2): 이 키의 초안을 이미 force 로 적용했으면(_draft.forced_key == key) fill. write_mode 위젯이
    force 로 남아 있어도 그 뒤 실행(Project 지문이 바뀔 때마다)이 사람의 다음 편집·_accepted 를 되돌리지 않는다.
    key 없음(적용할 초안 없음)도 fill — force 는 새 초안을 덮어쓸 때만. 그 밖의 mode 는 그대로."""
    if mode != "force":
        return mode
    d = current.get("_draft") if isinstance(current, dict) and isinstance(current.get("_draft"), dict) else {}
    return "fill" if not key or d.get("forced_key") == key else "force"


def _orphan_specs(root: str, crop_ids: set[str]) -> list[str]:
    """specs/*.json 가운데 크롭이 매니페스트에서 사라진 파일(_project.json 제외). Prepare 가 읽지 않는다."""
    d = os.path.join(root, "specs")
    try:
        names = os.listdir(d)
    except OSError:
        return []
    return sorted(n for n in names if n.endswith(".json") and n[:-5] not in crop_ids and n[:-5] != PROJECT_SPEC_ID
                  and os.path.isfile(os.path.join(d, n)))


def plan(root: str, ctxs: list[CropCtx], reqs: dict[str, Request], style_req: Request | None, *,
         mode: str, only: list[str]) -> dict:
    """대기 판정(§2.4). 반환:
      units    승인이 필요한 단위(과금 대상, 원래 순서 — 화풍은 맨 뒤)
      free     무료 단위(repoll = created 만 있음, reparse = resp 만 있음). only·_accepted 와 무관
      cached   지금 키의 done 이 원장에 있는 크롭(호출 없음, 정규화·병합만)
      refresh_available [(crop_id, (typ, hi))]  fill 에서 키만 바뀐 크롭·화풍(STYLE_ID)(refresh 로 갱신 가능, done 이 있으면 (0, 0))
      skipped  [(crop_id, 사유)]  스펙 JSON 오류·평탄화 실패·요청 없음
      accepted [crop_id]  _accepted 로 건너뜀(force + only 제외 — 단 그 키로 이미 force 한 크롭은 다시 _accepted 존중)
      filtered [crop_id]  only 로 걸러진 대기 / family, model, effort  요청 설정(approval·보고서용)
      orphan_specs [파일명]  매니페스트에 크롭이 없는 specs/*.json(지우지 않고 보고만, §5.2)
    est: openai = estimate_unit(caps 보정), local_qwen = (초, 초), mock = (0, 0)."""
    if mode not in WRITE_MODES:
        raise ValueError(f"{_TAG} write_mode 값이 잘못되었습니다: {mode!r}")
    ledger = Ledger(root)
    first = style_req or next(iter(reqs.values()), None)
    family = first.backend if first is not None else "mock"
    out: dict[str, Any] = {"units": [], "free": [], "cached": [], "refresh_available": [], "skipped": [],
                           "accepted": [], "filtered": [], "family": family,
                           "model": first.model if first is not None else "mock",
                           "effort": first.effort if first is not None else None,
                           "orphan_specs": _orphan_specs(root, {c.crop_id for c in ctxs})}
    calib = caps_load(root) if family == "openai" else {}
    style_units: list[Unit] = []

    def est(req: Request) -> tuple[int, int]:
        if req.backend == "openai":
            return estimate_unit(req, model=req.model, effort=req.effort, calib=calib)
        if req.backend == "local_qwen":
            return _QWEN_UNIT_S, _QWEN_UNIT_S
        return 0, 0

    def visit(crop_id: str, req: Request, reason: str | None, *, accepted: bool = False,
              dest: list | None = None, also: list | tuple = ()) -> None:
        dest = out["units"] if dest is None else dest
        st = ledger.state(crop_id, req.key)
        s = st["status"]
        if s in ("created", "resp"):
            free_state = "repoll" if s == "created" else "reparse"
            billable = free_state == "repoll" and REPOLL_BILLABLE
            u = Unit(crop_id, req.key, st["attempt"], req.kind, free_state,
                     ["복구(재폴링)" if s == "created" else "복구(재파싱)"], billable, est(req) if billable else (0, 0))
            (dest if billable else out["free"]).append(u)
            return
        if accepted:
            return
        if s == "done":
            out["cached"].append(crop_id)
            return
        if reason is None:
            return
        if only and not only_match(only, crop_id):
            out["filtered"].append(crop_id)
            return
        if s == "failed":
            state, reasons = "retry", [f"재시도(이전: {_short(st['error'])})", reason]
        elif s == "rejected":
            state, reasons = "rejected", [f"재시도(이전: {_short(st['error'])})", reason]
        elif s == "inflight":
            state, reasons = "orphan", ["orphan 재시도(과금됐을 수 있음)", reason]
        else:  # none / req 만(전송 전 크래시)
            state, reasons = "new", [reason]
        dest.append(Unit(crop_id, req.key, st["next_attempt"], req.kind, state, reasons + list(also), True, est(req)))

    for ctx in ctxs:
        cid = ctx.crop_id
        if ctx.spec_error:
            out["skipped"].append((cid, f"스펙 JSON 오류 — {ctx.spec_error}"))
            continue
        if ctx.skip_reason:
            out["skipped"].append((cid, ctx.skip_reason))
            continue
        req = reqs.get(cid)
        if req is None:
            out["skipped"].append((cid, "요청을 만들지 못함"))
            continue
        spec = ctx.spec or {}
        d = spec.get("_draft") if isinstance(spec.get("_draft"), dict) else None
        # force 는 키마다 1회 — 이 키로 이미 적용했으면(forced_key) 지정 크롭도 fill 처럼(_accepted 존중)
        forced = mode == "force" and bool(only) and only_match(only, cid) and merge_mode(spec, mode, req.key) == "force"
        if spec.get("_accepted") is True and not forced:
            out["accepted"].append(cid)
            visit(cid, req, None, accepted=True)
            continue
        reason, also = None, []
        if mode == "force":
            reason = "force 재초안" if forced else None
        else:
            reason, *also = _fill_reasons(d, ctx, req.backend) or [None]
            if reason is None and d.get("key") != req.key:
                if mode in ("refresh", "preview"):
                    rd = d.get("round")
                    reason = "draft_round" if rd is not None and rd != req.draft_round else "입력 바뀜"
                elif ledger.done(cid, req.key) is not None:
                    out["refresh_available"].append((cid, (0, 0)))
                else:
                    out["refresh_available"].append((cid, est(req)))
        visit(cid, req, reason, also=also)

    if style_req is not None:
        proj, perr = _load_spec(root, PROJECT_SPEC_ID)
        if perr:
            out["skipped"].append((STYLE_ID, f"{PROJECT_SPEC_ID}.json 오류 — {perr}"))
        else:
            proj = proj or {}
            pd = proj.get("_draft") if isinstance(proj.get("_draft"), dict) else {}
            pw = pd.get("written") if isinstance(pd.get("written"), dict) else {}
            moved = bool(pd.get("key")) and pd.get("key") != style_req.key
            reason = None
            if proj.get("style_note") == "":  # 사람이 화풍 초안·전파를 끔
                reason = None
            elif mode == "force":
                reason = "force 재초안" if "style" in only and merge_mode(proj, mode, style_req.key) == "force" else None
            elif "style_note" not in proj:
                reason = None if pw.get("style_note") not in (None, "") else "화풍 신규"  # 사람이 지운 기록이면 대기 아님
            elif moved and pd.get("backend") == BACKEND_NAMES["mock"] and style_req.backend != "mock":
                reason = "mock 초안 교체"  # mock 화풍이 실제 백엔드로 바뀐 뒤에도 모든 크롭에 전파되지 않게
            elif moved and mode in ("refresh", "preview"):
                reason = "입력 바뀜"
            elif moved and mode == "fill":
                out["refresh_available"].append(
                    (STYLE_ID, (0, 0) if ledger.done(STYLE_ID, style_req.key) is not None else est(style_req)))
            visit(STYLE_ID, style_req, reason, dest=style_units)
    out["units"] += style_units
    return out


def approval_id(u: Unit) -> str:
    """승인 해시에 넣는 단위 표시 "<crop_id>:<key>:a<n>"(화풍은 crop_id = "_style"). 모두 ASCII."""
    return f"{u.crop_id}:{u.key}:a{u.attempt}"


def approval(plan: dict, approve: str, *, backend_name: str, concurrency: int = 4) -> dict:
    """승인 판정. 반환:
      pending_hash   units 의 해시(없으면 "") / approved_now  approve == pending_hash(units 가 있을 때만)
      to_run         free + (승인 시 units) / blocked  승인이 없어 돌지 않는 units
      est_credits    units 의 (보통, 최대) 크레딧(openai 만, 그 밖 (0, 0)) / est_usd  같은 값 ÷ runner.CREDITS_PER_USD
      est_seconds    estimate_seconds(units) / reasons  {첫 사유의 분류("재시도(이전: …)" → "재시도"): 개수}
      orphan_retries orphan 재시도 수"""
    units: list[Unit] = list(plan.get("units") or [])
    free: list[Unit] = list(plan.get("free") or [])
    h = runner.pending_hash([f"backend:{backend_name}"] + [approval_id(u) for u in units]) if units else ""
    ok = bool(units) and str(approve or "").strip() == h
    paid = backend_name == BACKEND_NAMES["openai"]
    typ = sum(int(u.est[0]) for u in units) if paid else 0
    hi = sum(int(u.est[1]) for u in units) if paid else 0
    reasons: dict[str, int] = {}
    for u in units:
        r = (u.reasons[0] if u.reasons else "?").split("(", 1)[0].strip()
        reasons[r] = reasons.get(r, 0) + 1
    return {"pending_hash": h, "approved_now": ok, "to_run": free + units if ok else free,
            "blocked": [] if ok else units, "est_credits": (typ, hi),
            "est_usd": (round(typ / runner.CREDITS_PER_USD, 2), round(hi / runner.CREDITS_PER_USD, 2)),
            "est_seconds": estimate_seconds(units, backend=backend_name, concurrency=concurrency,
                                            effort=plan.get("effort")),
            "reasons": reasons, "orphan_retries": sum(1 for u in units if u.state == "orphan")}


# ══════════════════════════════════════════════════════════════════════
# 원장 (§2.5, §5.3)
# ══════════════════════════════════════════════════════════════════════
class Ledger:
    """<root>/vlm/<crop_id>/<key>/a<n>.<kind>.json. 쓰기는 store.write_json_atomic, 지우지 않는다."""

    def __init__(self, root: str):
        self.root = os.path.abspath(root)

    def dir(self, crop_id: str, key: str) -> str:
        if not isinstance(crop_id, str) or not store._CROP_ID_RE.match(crop_id) or crop_id == "img":
            raise ValueError(f"{_TAG} 원장 crop_id 형식이 잘못되었습니다: {crop_id!r}")
        if not isinstance(key, str) or not _KEY_RE.fullmatch(key):
            raise ValueError(f"{_TAG} 초안 키 형식이 아닙니다(소문자 hex 24자): {key!r}")
        return store.resolve_path(self.root, f"{LEDGER_DIR}/{crop_id}/{key}")

    def _path(self, crop_id: str, key: str, attempt: int, kind: str) -> str:
        if kind not in _LEDGER_KINDS:
            raise ValueError(f"{_TAG} 원장 종류가 잘못되었습니다: {kind!r}")
        if isinstance(attempt, bool) or int(attempt) < 1:
            raise ValueError(f"{_TAG} 시도 번호는 1 이상: {attempt!r}")
        return os.path.join(self.dir(crop_id, key), f"a{int(attempt)}.{kind}.json")

    def _attempts(self, crop_id: str, key: str) -> dict[int, dict]:
        d = self.dir(crop_id, key)
        out: dict[int, dict] = {}
        try:
            names = os.listdir(d)
        except FileNotFoundError:
            return out
        for fn in names:
            mt = _LEDGER_RE.fullmatch(fn)
            if mt:
                v = runner._read_json(os.path.join(d, fn))
                if v is not None:
                    out.setdefault(int(mt.group(1)), {})[mt.group(2)] = v
        return out

    def write(self, crop_id, key, attempt: int, kind: str, obj: dict) -> None:
        """레코드에 _seq(같은 시도 안 쓰기 순서)·at(시각)을 붙여 원자 저장."""
        path = self._path(crop_id, key, attempt, kind)
        files = self._attempts(crop_id, key).get(int(attempt), {})
        seq = max([int(v.get("_seq", 0)) for v in files.values() if isinstance(v, dict)
                   and isinstance(v.get("_seq"), int)] + [0]) + 1
        rec = dict(obj)
        rec.setdefault("at", _now())
        rec["_seq"] = seq
        store.write_json_atomic(path, rec)

    def read(self, crop_id, key, attempt: int, kind: str) -> dict | None:
        """없으면 None, 깨졌으면 {"_corrupt": 메시지}."""
        return runner._read_json(self._path(crop_id, key, attempt, kind))

    def state(self, crop_id, key) -> dict:
        """가장 큰 시도 n 기준: done 이 있으면 done, 없으면 가장 나중에 쓴 레코드 종류.
        {"status": none|req|inflight|created|resp|done|failed|rejected, "attempt", "next_attempt", "credits", "error"}"""
        files = self._attempts(crop_id, key)
        if not files:
            return {"status": "none", "attempt": 0, "next_attempt": 1, "credits": None, "error": None}
        n = max(files)
        f = files[n]
        done = f.get("done")
        if isinstance(done, dict) and "_corrupt" not in done:
            status = "done"
        elif done is not None:  # 손상된 done — 결과를 쓸 수 없으니 과금된 실패로 본다
            status = "failed"
            f = dict(f, failed={"error": f"원장 done 파일 손상: {done.get('_corrupt')}", "maybe_billed": True})
        else:
            rest = [k for k in f if k != "done"]
            status = max(rest, key=lambda k: (int(f[k].get("_seq", 0)) if isinstance(f[k].get("_seq"), int) else 0,
                                              _KIND_RANK[k]))
        nxt = n + 1 if status in ("failed", "inflight") else n
        credits = None
        for rec, keys in ((f.get("done"), ("credits",)), (f.get("failed"), ("credits",)),
                          (f.get("resp"), ("credits_poll",)), (f.get("created"), ("credits_create",))):
            vals = [rec.get(k) for k in keys] if isinstance(rec, dict) else []
            vals = [float(v) for v in vals if isinstance(v, (int, float)) and not isinstance(v, bool)]
            if vals:
                credits = max(vals) if credits is None else max(credits, *vals)
        err = None
        if status in ("failed", "rejected"):
            rec = f.get(status) or {}
            err = rec.get("error") or rec.get("_corrupt")
        return {"status": status, "attempt": n, "next_attempt": nxt, "credits": credits, "error": err}

    def done(self, crop_id, key) -> dict | None:
        """키의 done 레코드(+ crop_id, key, attempt). 없거나 손상이면 None."""
        files = self._attempts(crop_id, key)
        for n in sorted(files, reverse=True):
            d = files[n].get("done")
            if isinstance(d, dict) and "_corrupt" not in d:
                return dict(d, crop_id=crop_id, key=key, attempt=n)
        return None

    def all_done(self, crop_id: str | None = None) -> list[dict]:
        """평가 도구용: done 레코드 전부(+ crop_id, key, attempt, settings = 그 시도 req 의 settings)."""
        base = os.path.join(self.root, LEDGER_DIR)
        if crop_id is not None:
            cids = [crop_id]
        else:
            try:
                cids = sorted(n for n in os.listdir(base) if n != "img" and store._CROP_ID_RE.match(n)
                              and os.path.isdir(os.path.join(base, n)))
            except FileNotFoundError:
                return []
        out = []
        for cid in cids:
            try:
                keys = sorted(k for k in os.listdir(os.path.join(base, cid)) if _KEY_RE.fullmatch(k))
            except FileNotFoundError:
                continue
            for k in keys:
                d = self.done(cid, k)
                if d is None:
                    continue
                req = self.read(cid, k, d["attempt"], "req") or {}
                out.append(dict(d, settings=req.get("settings")))
        return out


def _caps_path(root: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(root)), CAPS_FILE)


def caps_load(root: str) -> dict:
    """<base_dir>/bmk_design_patch/_vlm_caps.json. 없거나 깨졌으면 {}."""
    path = _caps_path(root)
    with store.manifest_lock(os.path.dirname(path)):
        d = runner._read_json(path)
    return d if isinstance(d, dict) and "_corrupt" not in d else {}


def caps_update(root: str, fn: Callable[[dict], None]) -> None:
    """부모 폴더 잠금 안에서 읽기 → fn(d) → 바뀌었으면 원자 저장."""
    path = _caps_path(root)
    with store.manifest_lock(os.path.dirname(path)):
        d = runner._read_json(path)
        d = d if isinstance(d, dict) and "_corrupt" not in d else {}
        before = json.dumps(d, sort_keys=True, ensure_ascii=False)
        fn(d)
        if json.dumps(d, sort_keys=True, ensure_ascii=False) != before:
            store.write_json_atomic(path, d)


def _caps_transport(root: str, family: str, model: str) -> dict:
    return dict(((caps_load(root).get(family) or {}).get(model)) or {})


def _caps_set(root: str, family: str, model: str, **vals) -> None:
    def fn(d):
        d.setdefault(family, {}).setdefault(model, {}).update(vals)
    caps_update(root, fn)


def _caps_hint(fn: Callable, *args, **kw) -> None:
    """과금이 시작된 뒤의 caps 기록(_caps_set·_calib_record)은 전송 힌트일 뿐 — 쓰기 실패(백신·동기화가 파일을 잡음 등)가
    원장 기록·폴링을 끊어 응답 id 를 잃게 하지 않도록 경고만 남긴다."""
    try:
        fn(*args, **kw)
    except OSError as e:
        logger.warning(f"{_TAG} {CAPS_FILE} 기록 실패(무시 — 전송 힌트만 잃음): {e}")


def _calib_record(root: str, model: str, effort: str, kind: str, output_tokens: int) -> None:
    """caps["usage"]["<model>|<effort>|<kind>"] 에 output_tokens 를 더한다(최근 CALIB_KEEP 건)."""
    def fn(d):
        lst = d.setdefault("usage", {}).setdefault(f"{model}|{effort}|{kind}", [])
        lst.append(int(output_tokens))
        del lst[:-CALIB_KEEP]
    caps_update(root, fn)


# ══════════════════════════════════════════════════════════════════════
# 비용 추정 (§2.6)
# ══════════════════════════════════════════════════════════════════════
def estimate_unit(req: Request, *, model: str, effort: str, calib: dict) -> tuple[int, int]:
    """(보통, 최대) 크레딧. in = TEXT_OVERHEAD_TOKENS + len(user_text 바이트)/4 + Σ ceil(w/32)·ceil(h/32),
    out 보통 = caps 보정 중앙값(CALIB_MIN 건 이상) 또는 OUT_TYPICAL(화풍은 × STYLE_OUT_FACTOR), out 최대 = MAX_OUTPUT_TOKENS."""
    if model not in PRICE_PER_1K:
        raise ValueError(f"{_TAG} 가격표에 없는 모델: {model!r}")
    if effort not in EFFORTS:
        raise ValueError(f"{_TAG} effort 값이 잘못되었습니다: {effort!r}")
    p_in, p_out = PRICE_PER_1K[model]
    in_tok = TEXT_OVERHEAD_TOKENS + len((req.user_text or "").encode("utf-8")) / 4
    for im in _images_of(req):
        w, h = im.sent_wh
        in_tok += math.ceil(w / 32) * math.ceil(h / 32)
    hist = ((calib or {}).get("usage") or {}).get(f"{model}|{effort}|{req.kind}") or []
    hist = [int(v) for v in hist if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if len(hist) >= CALIB_MIN:
        out_typ = statistics.median(hist)
    else:
        out_typ = OUT_TYPICAL[effort] * (STYLE_OUT_FACTOR if req.kind == "style" else 1.0)
    out_hi = MAX_OUTPUT_TOKENS[effort]

    def credits(out_tok: float) -> int:
        return round((in_tok / 1000 * p_in + out_tok / 1000 * p_out) * runner.CREDITS_PER_USD)

    return credits(out_typ), credits(out_hi)


def estimate_seconds(units: list[Unit], *, backend: str, concurrency: int, effort: str | None = None) -> int:
    """openai: ceil(n/concurrency) × {high 150, xhigh 300, max 600, 그 밖 90}초. local_qwen: 40 + 25·n. mock: 0."""
    n = len(units)
    if n == 0:
        return 0
    fam = _family(backend)
    if fam == "openai":
        return math.ceil(n / max(1, int(concurrency))) * _EFFORT_SECONDS.get(effort or "", 90)
    if fam == "local_qwen":
        return _QWEN_LOAD_S + _QWEN_UNIT_S * n
    return 0


# ══════════════════════════════════════════════════════════════════════
# 프롬프트·스키마·파싱·정규화 (§2.7, §4)
# ══════════════════════════════════════════════════════════════════════
# 크롭 시스템 프롬프트 = RP §7 원문 + S1–S9(§4.1). 문구를 바꾸면 prompt_fingerprint 가 바뀌어 모든 키가 바뀐다.
_SYSTEM_CROP = """You write draft edit specs for an image-editing model that fixes character-design details in crops of a finished anime-style game illustration. A human art lead reviews every draft, so be literal, concrete, and honest about uncertainty.

INPUTS
- Image 1 is the CANVAS: the crop that will be edited. Some parts are drawn wrong (wrong design, wrong part count, missing ornament). Stretched, smeared bands at its borders are padding, not artwork. Thin pure-red or pure-green scribbles, if any, are annotation marks, not artwork.
- Images 2..N are REFERENCES cut from the official character sheet, placed on a flat mid-gray background. They show the correct design but may use another angle or pose, flat neutral lighting, different hair color, extra objects, or the other side of the character. Each reference is labeled with its file name; file names hint at the part and side (left/right, 단일 = single piece, 헤드 = head piece, _t = clean cutout). Each reference label says whether the side word in its file name matches the crop's side.
- The TARGET LIST is fixed. Write exactly one targets entry per listed id, in the given order. Never add, merge, split, or rename ids. If you notice another wrong element, put it only in suggested_targets. A target marked "legacy" stands for the whole part named by the crop: draft its single most important wrong element as that target, and put every other wrong element in suggested_targets.

HOW TO WORK
1. In observations, state in one line per image what it shows.
2. For each target, find it in the canvas and in the references. Fill canvas_now and counts BEFORE detail. Count repeated parts by enumerating them one by one; use -1 when a count cannot be seen.
3. Describe the REFERENCE design in detail. Describe only wrong things VISIBLE IN THE CANVAS in must_not.

FIELD CONTRACTS (your text is inserted verbatim into fixed English sentences, so follow the grammar exactly)
- name: 2-6 word English noun phrase, lowercase, no article, singular head noun ("toast hair clip", "pair of ribbon loops"). It follows "Correct the ...". If the target list gives an English name, copy it exactly.
- found: true if the target is visible in the canvas now; false if it is missing and must be added.
- mode: "ref_correct" if at least one reference shows this object; otherwise "self_restore".
- suggested_mode: "paste_harmonize" if the canvas object's silhouette, part count, or ornament differs so much that pasting the reference cutout would be more faithful; "sketch_guide" if it must be drawn at a new place that words cannot pin down; else "none". Advice only.
- action: "fix" if found is true, "add" if found is false.
- ref_images: image numbers (2..N) that show this object most clearly. Prefer one; at most two. Prefer a clean cutout (_t) unless only a context image shows how it attaches. Prefer the file whose side matches the crop name. Empty only for self_restore.
- ref_object: how to point at the object inside those references: "the " + 2-8 words, lowercase, no period, no image numbers ("the toast-shaped clip"). It follows "to match ... in @image2".
- ref_ignore: what else is visible in those references that must not be copied, starting with "the" or "everything except", no period ("the white hair and the gray background"). "" if nothing.
- ref_view: "different" if the references draw the object from another angle or pose than the canvas, else "same".
- detail: the reference design as 2-5 clauses separated by "; ". Each clause is a lowercase noun phrase that stands alone (no "it", no "the same"): overall shape; parts with exact counts; base colors; material and finish; motifs. 12-60 words in total. Plain color names, base colors only; never words about light, shadow, highlight, glow, or background. No final period. It follows "... in @image2: ". Write a number in detail only if counts lists that part with the same reference count.
- must_not: 0-3 wrong features visible in the canvas now that must disappear; each "the ...", lowercase, no period, no comma or "and" inside an item ("the round green filling"). Empty when action is "add".
- defects: only for self_restore: what is broken in the canvas, 1-3 lowercase clauses separated by "; ", no final period. Otherwise "".
- location: only for action "add": where it goes, a lowercase phrase starting with on, around, at, above, below, along, between, or inside; no period. Otherwise "". Name a visible anchor in the canvas (for example "below the black cuff band").
- occluded_by: what overlaps the target in the canvas, as "the ...", lowercase; or "".
- shape: "locked" if the canvas silhouette and volume are right and only the surface design is wrong; "free" if the outline or part count must change a lot; otherwise "moderate".
- text: "exact_ref" only if the chosen reference shows short lettering or symbols that belong to this object's design (for example glyphs on a name plate); "keep" if the canvas object carries dense or illegible writing, such as printed lines, that must stay as it is; otherwise "none". If the canvas shows lettering on this object that the reference does not have, use "none" and name that lettering in must_not.
- bbox: the target's box in Image 1 as {BBOX_FORMAT}. For action "add", the area it should occupy. Tight around the visible object including its outline, excluding cast shadows. [0,0,0,0] if you cannot locate it.
- confidence: "high", "medium", or "low" for this target's draft.
- review_note: one short Korean sentence telling the reviewer what to double-check; "" if nothing.
- preserve (crop level): 2-4 things in the canvas that touch or overlap the targets and must not change; each "the ...", lowercase, no period, no comma or "and" inside an item; never a target.

RULES
- Left and right always mean the viewer's left and right in the image, never the character's.
- Never write letters, characters, or words that appear in the images; call them "stylized glyphs" or "small lettering".
- English and ASCII only, except review_note.
- Do not invent parts you cannot see in a reference. When unsure of a count, leave the number out of detail and set confidence to "low".
- A side word in a crop or file name (right, left, 오른쪽, 왼쪽) means the viewer's side.
- If a target line says its mode is fixed by the art lead, keep that mode and still fill every field.
- An artist hint, if given, overrides your own reading; it may be written in Korean.
- Output only the JSON object."""
_BBOX_PX = "[x1, y1, x2, y2] as integer pixel coordinates in Image 1, which is exactly {W} x {H} pixels (0,0 = top-left)"
_BBOX_Q1000 = "[x1, y1, x2, y2] as integers from 0 to 1000 relative to Image 1's width and height"

# 사용자 템플릿(RP §7 + U1–U6). 이미지마다 앞에 라벨 텍스트가 온다.
_U_HEAD = 'Crop: {crop}   Part label (Korean): "{part}"   Side (viewer\'s, from the name): {side}'
_U_TARGETS = "Targets (fixed list, in this order):"
_U_TARGET = '- {tid}: Korean label "{label}"; {english}'
_U_NAME_FIXED = 'English name: "{name}" (use exactly)'
_U_NAME_FREE = "English name: none, choose one"
_U_LEGACY = "; legacy: the whole part"
_U_MODE = "; mode fixed by the art lead: {mode}"
_U_GLOSS = "Glossary (Korean -> English, use these terms where they apply): {gloss}"
_U_HINT = "Artist hint (may be Korean): {hint}"
_U_CANVAS = "Canvas: original crop {w0}x{h0} px, sent as {w}x{h} px.{pad}"
_U_PAD = " The {sides} border {region} padding."
_U_HIDDEN = "Reference files with this number that are not shown: {names}"
_U_IMG_CANVAS = "Image 1 - CANVAS ({w}x{h} px). The crop to be edited."
_U_IMG_REF = 'Image {k} - REFERENCE "{name}" ({kind}, on flat gray; {side}).'
_U_REF_KIND = ("clean cutout", "sheet crop with surroundings")
_U_REF_SIDE = {"same": "same side as the crop", "opposite": "OPPOSITE SIDE ({side}), may show the other piece",
               "none": "no side in its name", "crop_none": "{side} side in its name; the crop name has no side"}
_U_FINAL = "Write the JSON now: exactly one targets entry for each of {ids}, in that order. Allowed ref_images: {refs}."
_U_SKELETON = "Fill this JSON skeleton:\n{skeleton}"

# 화풍 프롬프트(§4.2)
_STYLE_SYSTEM = ("You describe the rendering style of an anime-style game illustration so an image editor can paint new parts "
                 "that blend in. Image 1 is the whole illustration; the other images are crops of it. Output style_note: "
                 "6-25 words, a comma-separated lowercase list covering line color and weight, shading style, light color, "
                 "and texture. Base colors of objects are not style. No period, no dashes. Also output observations, one "
                 "line per image. English and ASCII only. Output only the JSON object.")
_S_USER_BASE = "Image 1 - WHOLE ILLUSTRATION ({w}x{h} px)."
_S_USER_CROP = 'Image {k} - CROP "{crop_id}"'
_S_USER_FINAL = "Write the JSON now."
_STYLE_SKELETON = {"observations": [{"image": "<...>", "shows": "<...>"}], "style_note": "<...>"}
_SCHEMA_NAME = "design_patch_spec_draft"

_TARGET_REQUIRED = ("id", "name", "found", "canvas_now", "counts", "mode", "suggested_mode", "action", "ref_images",
                    "ref_object", "ref_ignore", "ref_view", "detail", "must_not", "defects", "location", "occluded_by",
                    "shape", "text", "bbox", "confidence", "review_note")


def _str_array(max_items: int) -> dict:
    return {"type": "array", "maxItems": max_items, "items": {"type": "string"}}


def _obs_schema(max_items: int) -> dict:
    return {"type": "array", "maxItems": max_items,
            "items": {"type": "object", "additionalProperties": False, "required": ["image", "shows"],
                      "properties": {"image": {"type": "string"}, "shows": {"type": "string"}}}}


def _bbox_schema() -> dict:
    return {"type": "array", "minItems": 4, "maxItems": 4, "items": {"type": "integer"}}


def _crop_schema(ids: list[str], n_refs: int) -> dict:
    """크롭 L2 스키마(RP §8 + §4.3): schema_echo 첫 속성, style_note 없음, id enum = 매니페스트 tid, targets 정확히 K개.
    레퍼런스가 없으면 ref_images = maxItems 0."""
    if n_refs:
        ref_images = {"type": "array", "maxItems": 2, "items": {"type": "integer", "enum": list(range(2, n_refs + 2))}}
    else:
        ref_images = {"type": "array", "maxItems": 0, "items": {"type": "integer"}}
    s = {"type": "string"}
    target = {"type": "object", "additionalProperties": False, "required": list(_TARGET_REQUIRED), "properties": {
        "id": {"type": "string", "enum": list(ids)}, "name": s, "found": {"type": "boolean"}, "canvas_now": s,
        "counts": {"type": "array", "maxItems": 4, "items": {
            "type": "object", "additionalProperties": False, "required": ["part", "reference", "canvas"],
            "properties": {"part": s, "reference": {"type": "integer"}, "canvas": {"type": "integer"}}}},
        "mode": {"type": "string", "enum": ["ref_correct", "self_restore"]},
        "suggested_mode": {"type": "string", "enum": ["none", "paste_harmonize", "sketch_guide"]},
        "action": {"type": "string", "enum": ["fix", "add"]},
        "ref_images": ref_images, "ref_object": s, "ref_ignore": s,
        "ref_view": {"type": "string", "enum": ["same", "different"]},
        "detail": s, "must_not": _str_array(3), "defects": s, "location": s, "occluded_by": s,
        "shape": {"type": "string", "enum": ["locked", "moderate", "free"]},
        "text": {"type": "string", "enum": ["none", "keep", "exact_ref"]},
        "bbox": _bbox_schema(), "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "review_note": s}}
    return {"type": "object", "additionalProperties": False,
            "required": ["schema_echo", "observations", "preserve", "targets", "suggested_targets"],
            "properties": {
                "schema_echo": {"type": "string", "enum": [SCHEMA_ECHO]},
                "observations": _obs_schema(10), "preserve": _str_array(4),
                "targets": {"type": "array", "minItems": len(ids), "maxItems": len(ids), "items": target},
                "suggested_targets": {"type": "array", "maxItems": MAX_SUGGESTED, "items": {
                    "type": "object", "additionalProperties": False, "required": ["name", "reason", "bbox"],
                    "properties": {"name": s, "reason": s, "bbox": _bbox_schema()}}}}}


def _style_schema() -> dict:
    return {"type": "object", "additionalProperties": False, "required": ["schema_echo", "observations", "style_note"],
            "properties": {"schema_echo": {"type": "string", "enum": [SCHEMA_ECHO]}, "observations": _obs_schema(5),
                           "style_note": {"type": "string"}}}


def _crop_skeleton(ids: list[str]) -> dict:
    """Qwen 용 골격(U6): 값 자리는 "<...>" — 채운 예시는 베끼므로 쓰지 않는다. schema_echo 는 넣지 않는다."""
    box = ["<x1>", "<y1>", "<x2>", "<y2>"]
    return {"observations": [{"image": "<...>", "shows": "<...>"}], "preserve": ["<...>"],
            "targets": [{"id": tid, "name": "<...>", "found": "<true|false>", "canvas_now": "<...>",
                         "counts": [{"part": "<...>", "reference": "<integer>", "canvas": "<integer>"}],
                         "mode": "<ref_correct|self_restore>", "suggested_mode": "<none|paste_harmonize|sketch_guide>",
                         "action": "<fix|add>", "ref_images": ["<integer>"], "ref_object": "<...>", "ref_ignore": "<...>",
                         "ref_view": "<same|different>", "detail": "<...>", "must_not": ["<...>"], "defects": "<...>",
                         "location": "<...>", "occluded_by": "<...>", "shape": "<locked|moderate|free>",
                         "text": "<none|keep|exact_ref>", "bbox": box, "confidence": "<high|medium|low>",
                         "review_note": "<...>"} for tid in ids],
            "suggested_targets": [{"name": "<...>", "reason": "<...>", "bbox": box}]}


def system_prompt(kind: str, backend: str, bbox_frame: tuple) -> str:
    """크롭(RP §7 + S1–S9) 또는 화풍(§4.2) 시스템 프롬프트. {BBOX_FORMAT} 은 백엔드·bbox_frame 으로 채운다
    (local_qwen 또는 ("q1000",) = 0–1000 상대 좌표, openai·mock ("px", W, H) = 전송 캔버스 픽셀)."""
    if kind == "style":
        return _STYLE_SYSTEM
    if kind != "crop":
        raise ValueError(f"{_TAG} 알 수 없는 요청 종류: {kind!r}")
    frame = tuple(bbox_frame or ("q1000",))
    if _family(backend) == "local_qwen" or frame[0] != "px":
        fmt = _BBOX_Q1000
    else:
        fmt = _BBOX_PX.format(W=int(frame[1]), H=int(frame[2]))
    return _SYSTEM_CROP.replace("{BBOX_FORMAT}", fmt)


def _png_data_uri(pil: "Image.Image") -> str:
    buf = io.BytesIO()
    pil.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def _with_transport(body: dict, req: Request, level: int, background: bool) -> dict:
    """같은 본문(이미지 인코딩 1번)에 사다리 단계·background 만 바꿔 끼운다."""
    out = {k: v for k, v in body.items() if k not in ("text", "background")}
    sch = schema_for_level(req.schema, level)
    if sch is not None:
        out["text"] = {"format": {"type": "json_schema", "name": _SCHEMA_NAME, "strict": True, "schema": sch}}
    if background:
        out["background"] = True
    return out


def openai_body(req: Request, *, model: str, effort: str, image_detail: str, level: int, background: bool) -> dict:
    """Responses API 본문(dict, sync_op_raw 로 보냄). level ≥1 이면 text.format json_schema(strict), background 면 background:True.
    이미지는 render_sent → PNG data URI, 라벨 텍스트와 교차(input_text / input_image)."""
    if effort not in MAX_OUTPUT_TOKENS:
        raise ValueError(f"{_TAG} effort 값이 잘못되었습니다: {effort!r}")
    content = []
    for kind, val in req.parts:
        if kind == "image":
            content.append({"type": "input_image", "image_url": _png_data_uri(render_sent(val)), "detail": image_detail})
        else:
            content.append({"type": "input_text", "text": val})
    body = {"model": model, "instructions": req.system, "input": [{"role": "user", "content": content}],
            "reasoning": {"effort": effort}, "max_output_tokens": MAX_OUTPUT_TOKENS[effort],
            "store": True, "stream": False, "truncation": "disabled"}
    return _with_transport(body, req, level, background)


def _decode_uri(uri: str) -> "Image.Image":
    head, _, b64 = str(uri).partition(",")
    if not head.startswith("data:image/") or not b64:
        raise ValueError(f"{_TAG} data URI 가 아닙니다: {_short(uri, 40)!r}")
    im = Image.open(io.BytesIO(base64.b64decode(b64)))
    im.load()
    return im


def ledger_body(body: dict, req: Request) -> dict:
    """원장용 본문: input_image 의 base64 data URI → {"img": "vlm/img/<sha12>.png", "detail", 역할·이름·src_sha·전송 크기}.
    img 경로는 전송 픽셀의 pixel_sha 앞 12자(store.save_png_ca 와 같은 이름 — _save_sent 가 그 파일을 쓴다)."""
    imgs = _images_of(req)
    out = {k: copy.deepcopy(v) for k, v in body.items() if k != "input"}
    msgs, j = [], 0
    for msg in body.get("input") or []:
        content = []
        for c in msg.get("content") or []:
            if c.get("type") != "input_image":
                content.append(dict(c))
                continue
            item = {"type": "input_image", "img": f"{IMG_SUBDIR}/{store.pixel_sha(_decode_uri(c.get('image_url')))[:12]}.png",
                    "detail": c.get("detail")}
            if j < len(imgs):
                im = imgs[j]
                item.update(role=im.role, name=im.name, src_sha=im.src_sha, sent_wh=[int(v) for v in im.sent_wh])
            j += 1
            content.append(item)
        msgs.append({**{k: v for k, v in msg.items() if k != "content"}, "content": content})
    out["input"] = msgs
    return out


def _save_sent(root: str, body: dict) -> None:
    """전송한 이미지를 vlm/img/<sha12>.png 로 저장(내용 주소 — 이미 있으면 쓰지 않음)."""
    for msg in body.get("input") or []:
        for c in msg.get("content") or []:
            if c.get("type") == "input_image":
                store.save_png_ca(root, IMG_SUBDIR, _decode_uri(c.get("image_url")))


def _strip_items_limits(node: Any) -> Any:
    if isinstance(node, dict):
        return {k: _strip_items_limits(v) for k, v in node.items() if k not in ("minItems", "maxItems")}
    if isinstance(node, list):
        return [_strip_items_limits(v) for v in node]
    return node


def schema_for_level(schema: dict, level: int) -> dict | None:
    """2 = 그대로(사본), 1 = 모든 위치의 minItems / maxItems 제거(enum 유지), 0 = None(text.format 없이 보냄)."""
    if level >= 2:
        return copy.deepcopy(schema)
    if level == 1:
        return _strip_items_limits(schema)
    return None


_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_FENCE_RE = re.compile(r"```[A-Za-z0-9_\-]*")
_SMART_DQ = "\u201c\u201d\u201e\u201f\u2033"
_PY_WORDS = {"True": "true", "False": "false", "None": "null", "NaN": "null", "Infinity": "null", "undefined": "null"}
_MAX_CUTS = 64


def _scan(s: str) -> tuple[int, list[str], bool, list[tuple[int, tuple]]]:
    """s[0] == '{' 부터 문자열 인식 괄호 매칭. (끝 위치(배타), 열린 괄호 스택, 문자열 안에서 끝남, 쉼표 위치·스택 목록)."""
    stack: list[str] = []
    in_str = esc = False
    cuts: list[tuple[int, tuple]] = []
    for i, ch in enumerate(s):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append(ch)
        elif ch in "}]":
            if stack:
                stack.pop()
            if not stack:
                return i + 1, [], False, cuts
        elif ch == ",":
            cuts.append((i, tuple(stack)))
    return len(s), stack, in_str, cuts


def _closers(stack) -> str:
    return "".join("}" if c == "{" else "]" for c in reversed(stack))


def _repair_outside(s: str, repairs: list[str]) -> str:
    """문자열 밖만 고친다: 스마트 따옴표 → ", True/False/None/NaN → JSON, // · /* */ 주석 제거, 꼬리 쉼표 제거."""
    out: list[str] = []
    i, n = 0, len(s)
    in_str: str | None = None  # None | '"' | "smart"

    def note(r: str) -> None:
        if r not in repairs:
            repairs.append(r)

    while i < n:
        ch = s[i]
        if in_str:
            if ch == "\\" and i + 1 < n:
                out.append(s[i:i + 2])
                i += 2
                continue
            if in_str == "smart" and (ch == '"' or ch in _SMART_DQ):
                # 스마트 따옴표로 연 문자열: 뒤에 구조 문자(: , } ])나 끝이 올 때만 닫는다 — 안쪽 인용은 그대로 둔다
                j = i + 1
                while j < n and s[j] in " \t\r\n":
                    j += 1
                if j >= n or s[j] in ":,}]":
                    out.append('"')
                    in_str = None
                else:
                    out.append('\\"' if ch == '"' else ch)
                i += 1
                continue
            if ch == '"':
                in_str = None
            out.append(ch)
            i += 1
            continue
        if ch == '"':
            in_str = '"'
        elif ch in _SMART_DQ:
            note("smart_quotes")
            out.append('"')
            in_str = "smart"
            i += 1
            continue
        elif s.startswith("//", i):
            note("comment")
            j = s.find("\n", i)
            i = n if j < 0 else j
            continue
        elif s.startswith("/*", i):
            note("comment")
            j = s.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        elif ch == ",":
            j = i + 1
            while j < n and s[j] in " \t\r\n":
                j += 1
            if j < n and s[j] in "}]":
                note("trailing_comma")
                i += 1
                continue
        elif ch.isalpha() or ch == "_":
            j = i
            while j < n and (s[j].isalnum() or s[j] == "_"):
                j += 1
            w = s[i:j]
            if w in _PY_WORDS:
                note("py_literal")
                w = _PY_WORDS[w]
            out.append(w)
            i = j
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _loads(s: str) -> Any:
    return json.loads(s, strict=False)  # 문자열 안 제어 문자(줄바꿈 등)는 허용


def extract_json(text: str) -> tuple[dict, list[str]]:
    """VLM 출력 → (객체, 수리 목록). <think>·펜스 제거 → 문자열 인식 괄호 매칭 → 실패하면 문자열 밖 관용 수리(RP §4)
    → 잘렸으면 열린 문자열·괄호 닫기("truncated", 열린 문자열을 닫았으면 + "truncated_string")
    (그래도 안 되면 마지막 쉼표들에서 잘라 닫기, + "truncated_cut"). 객체가 아니거나 실패면 ParseError."""
    if not isinstance(text, str):
        raise ParseError(f"{_TAG} VLM 출력이 문자열이 아닙니다: {type(text).__name__}")
    repairs: list[str] = []
    s = text
    low = s.lower()
    if "<think>" in low or "</think>" in low:
        s = _THINK_RE.sub("", s)
        low = s.lower()
        if "</think>" in low:  # 여는 태그 없이 닫는 태그만(채팅 템플릿이 여는 태그를 넣은 경우)
            s = s[low.rfind("</think>") + len("</think>"):]
        elif "<think>" in low:  # 닫히지 않은 생각 — 그 뒤는 JSON 이 아니다
            s = s[:low.find("<think>")]
        repairs.append("think")
    if "```" in s:
        s = _FENCE_RE.sub("", s)
        repairs.append("fence")
    start = s.find("{")
    if start < 0:
        raise ParseError(f"{_TAG} 출력에 JSON 객체('{{')가 없습니다: {_short(text, 120)!r}")
    if s[:start].strip():
        repairs.append("prefix_text")
    s = s[start:]

    def finish(obj: Any) -> tuple[dict, list[str]]:
        if not isinstance(obj, dict):
            raise ParseError(f"{_TAG} JSON 최상위가 객체가 아닙니다: {type(obj).__name__}")
        return obj, repairs

    end, stack, in_str, cuts = _scan(s)
    if not stack and not in_str:
        if s[end:].strip():
            repairs.append("suffix_text")
        try:
            return finish(_loads(s[:end]))
        except json.JSONDecodeError:
            pass
    fixed = _repair_outside(s, repairs)
    end, stack, in_str, cuts = _scan(fixed)
    if not stack and not in_str:
        try:
            return finish(_loads(fixed[:end]))
        except json.JSONDecodeError as e:
            raise ParseError(f"{_TAG} JSON 수리 실패: {e}") from e
    repairs.append("truncated")
    t = fixed[:end]
    if in_str:
        t = (t[:-1] if t.endswith("\\") else t) + '"'
    t = re.sub(r"[,:]\s*$", "", t.rstrip())
    try:
        obj = _loads(t + _closers(stack))
    except json.JSONDecodeError:
        pass
    else:
        if in_str:  # 마지막 문자열 값이 중간에서 끊겼다 — normalize 가 그 값을 버린다
            repairs.append("truncated_string")
        return finish(obj)
    for pos, st in list(reversed(cuts))[:_MAX_CUTS]:
        try:
            obj = _loads(fixed[:pos] + _closers(st))
        except json.JSONDecodeError:
            continue
        repairs.append("truncated_cut")
        return finish(obj)
    raise ParseError(f"{_TAG} 잘린 JSON 을 닫지 못했습니다: {_short(text, 120)!r}")


@dataclass
class Draft:
    crop: dict                       # {"preserve": [...]} (+ 정규화 메모)
    targets: dict[str, dict]         # tid → 스펙에 쓸 수 있는 필드(소유권·렌더 규칙 적용 후)
    review: dict[str, dict]          # tid → _review(confidence, note, found, counts, canvas_now, suggested_mode,
                                     #        name_suggestion, unrendered{…}, flags[…], side)
    suggestions: dict                # {"name":{tid:…}, "mode":{tid:…}, "refs":{tid:[…]}, "targets":[{name,reason,bbox}],
                                     #  "layer_name": str, "glossary": {label: name}}
    observations: list[dict]
    flags: list[str]
    transport: dict                  # {"schema_enforced"} — schema_level·background 는 원장 done 의 transport 에서


def constraints_for(ctx: CropCtx, current: dict | None) -> dict[str, dict]:
    """tid → {"mode": 사람 mode|None, "action": 사람 action|None, "refs": 사람 refs|None}.
    출처 우선순위: 스펙의 사람 값(C 있고 B 없거나 C≠B) > 매니페스트 human_mode/human_refs.
    스펙 refs 는 키가 있으면(빈 목록 포함) 값이다. 같은 id 가 여럿이면 마지막(spec_from_manifest 와 같음)."""
    cur = current if isinstance(current, dict) else {}
    d = cur.get("_draft") if isinstance(cur.get("_draft"), dict) else {}
    written = d.get("written") if isinstance(d.get("written"), dict) else {}
    spec_t = {str(t.get("id")): t for t in cur.get("targets") or [] if isinstance(t, dict) and t.get("id") is not None}
    out: dict[str, dict] = {}
    for t in ctx.targets:
        st = spec_t.get(t.tid) or {}
        b = written.get(t.tid) if isinstance(written.get(t.tid), dict) else {}

        def human(f: str) -> bool:
            return f in st and st[f] is not None and (f not in b or not _eq(st[f], b[f]))

        mode = st.get("mode") if human("mode") and st.get("mode") in dprompt.MODES else None
        action = st.get("action") if human("action") and st.get("action") in dprompt.ACTIONS else None
        refs = [str(r) for r in st["refs"]] if human("refs") and isinstance(st.get("refs"), list) else None
        out[t.tid] = {"mode": mode or t.human_mode, "action": action,
                      "refs": refs if refs is not None else (list(t.human_refs) or None)}
    return out


_CJK_RE = re.compile("[\u1100-\u11ff\u2e80-\u2fff\u3000-\u303f\u3040-\u30ff\u3100-\u31ff\u3200-\u32ff"
                     "\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\uf900-\ufaff\uff00-\uffef]+")
_IMG_REF_RE = re.compile(r"@image\s*\d+|\b[Ii]mage\s+\d+\b")
_QUOTED_RE = re.compile('"[^"]{1,60}"|\u201c[^\u201d]{1,60}\u201d|\u300c[^\u300d]{1,60}\u300d|\u300e[^\u300f]{1,60}\u300f')
_WRAP_QUOTES = {'"': '"', "'": "'", "\u201c": "\u201d", "\u2018": "\u2019", "`": "`"}
_BULLET_RE = re.compile(r"^(?:[-*\u2022\u00b7]+|\d+[.)])\s+")
_ARTICLE_RE = re.compile(r"^(?:the|a|an)\s+", re.I)
_NONE_WORDS = {"none", "n/a", "na", "null", "nothing", "nil", "-", "no"}
_PLACEHOLDER_RE = re.compile(r"<[^<>]{0,60}>")   # Qwen 골격(U6)의 "<...>" 를 베낀 값
_NEG_RE = re.compile(r"^(?:nothing|none|no|not|n/a)(?![\w-])", re.I)   # "nothing to ignore", "no other objects"("no-entry" 는 아님)
_NEG_KEEP = ("detail", "defects", "style_note", "canvas_now")   # 부정구가 내용인 필드("no visible crust")
_NEG_SILENT = ("ref_ignore", "occluded_by")       # 부정구 = 빈 값("nothing to ignore") — 플래그 없이 비움
_NOT_NP = frozenset("more less longer other others change changes changed need needed such matter one further".split())
_AUX_WORDS = frozenset("is are was were be been being should must can could will would has have had do does".split())
_PREPS = ("on", "around", "at", "above", "below", "along", "between", "inside")   # 프롬프트가 location 에 허용한 시작어
_PREP_WORDS = frozenset(("of in on at to from with by for as into onto inside outside around above below under underneath "
                         "beneath over near next beside behind between among along across through within against toward "
                         "towards upon atop alongside beyond").split())
_FUNC_WORDS = _PREP_WORDS | {"the", "a", "an", "and", "or", "but", "nor", "than", "it", "its", "their", "this", "that", "these",
                             "those", "same", "shown", "seen"}
_LOC_ADV = frozenset("just right directly slightly partly immediately".split())
_LOC_RE = re.compile(r"^(?:(?:" + "|".join(sorted(_LOC_ADV)) + r")\s+)?(?:"
                     + "|".join(sorted(_PREP_WORDS - {"of", "as", "for", "with", "from"})) + r")\b")
_LOC_KEEP = frozenset("where wherever close closer opposite up down adjacent halfway partway".split())
_DETERMINERS = frozenset("the a an its their his her this that these those each both either one".split())
_ED_ING_NOUNS = frozenset(("ring rings earring earrings string strings ceiling lining opening openings piping stitching edging "
                           "binding padding railing bedding clothing building speed").split())   # -ed/-ing 로 끝나는 명사
# render_check 린트: "on the tucked behind …", "on the where …", "on the of …" — location 앞에 붙인 "on the" 가 문장을 깬 자리
_LOC_LINT_RE = re.compile(r"\bon the (?!(?:" + "|".join(sorted(_ED_ING_NOUNS)) + r")\b)(?:[a-z]{2,}(?:ed|ing)\s+(?:"
                          + "|".join(sorted((_PREP_WORDS - {"of"}) | {"up", "down", "close"})) + r")|where|wherever|"
                          r"of|for|with|from|its|their|an?|(?:close|opposite|up|down|near) (?:the|an?|to|of|from))\b", re.I)
_NP_CUT = (_PREP_WORDS - {"of"}) | {"that", "which", "where", "who"}
_NP_SKIP = frozenset("the a an of its their this these those same other each both all".split())
_LETTER_RE = re.compile(r"\b(?:letter\w*|writing|texts?|glyph\w*|characters|inscriptions?)\b", re.I)
_LIGHT_RE = re.compile(r"\b(?:lighting|lit|shadows?|highlights?|glow\w*|backgrounds?|light(?!\s+(?:blue|green|pink|"
                       r"purple|brown|gray|grey|yellow|red|orange|beige|tan|violet|cyan|teal)\b))\b", re.I)
_NUM_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
              "ten": 10, "eleven": 11, "twelve": 12}
_MANY_WORDS = frozenset(["pair", "pairs", "both", "set", "sets", "several"] + [w for w, n in _NUM_WORDS.items() if n > 1])
_POS_WORDS = frozenset("left right upper lower top bottom middle center centre front back inner outer side".split())
_INSIDE_WORDS = frozenset("in inside within into".split())   # N14 preserve: 이 전치사 뒤가 타깃이면 타깃 안("on" 은 겹침이라 뺌)
# 겹침·주변이지 타깃 안이 아닌 것: "in front of"·"in the shadow of"(관계 명사 + of — middle·center 는 "of 여럿" 일 때만, 사이라서),
# "held in"·"tucked into"(쥐고 있음 — in·into 앞의 분사, -ly 부사 하나는 건너뜀)
_IN_RELATION = frozenset("front back place shadow foreground background reach middle center centre".split())
_IN_MIDDLE = frozenset("middle center centre".split())
_HOLD_WORDS = frozenset("held caught tucked gathered clipped pinned trapped wrapped stuck secured fastened gripped".split())
_CONF_RANK = {"low": 0, "medium": 1, "high": 2}


def _flag(flags: list, f: str) -> None:
    if f not in flags:
        flags.append(f)


def _clean(v: Any, flags: list, field_: str, *, english: bool = True) -> str:
    """RP §9 기본 규칙 S(): NFC → 공백 정리 → 감싼 따옴표·글머리 제거 → 골격 자리표시 "<...>" 제거 → (영문 필드)
    @imageN·Image N·따옴표 인용·CJK 제거(플래그). 지운 뒤에는 끝에 매달린 전치사·관사("the clip in Image 2" → "the clip"),
    앞 접속사를 빼고, 머리 명사가 사라진 값("<...> of the sleeve" → "of …", "the of the sleeve")은 "" → 끝 .;:, 제거 →
    첫 글자 소문자(약어 제외). "none"·"n/a" 같은 빈 값 표시, 관사·전치사만 남은 값("the", "on the")은 "".
    부정구("nothing to ignore", "no other objects")는 detail·defects·style_note·canvas_now 에서는 그대로, must_not 의 "no X"(X 명사구)는
    "X"(호출자가 "the X"), 그 밖의 must_not 부정구는 플래그 달고 "", 나머지 필드는 "" — ref_ignore·occluded_by 밖이면 플래그."""
    if isinstance(v, bool) or v is None:
        return ""
    if isinstance(v, (int, float)):
        v = str(v)
    if not isinstance(v, str):
        return ""
    t = unicodedata.normalize("NFC", v).replace("’", "'").replace("‘", "'")
    t = " ".join(t.split())
    for _ in range(2):
        if len(t) >= 2 and t[0] in _WRAP_QUOTES and t.endswith(_WRAP_QUOTES[t[0]]):
            t = t[1:-1].strip()
        t = _BULLET_RE.sub("", t)
    subs = [(_PLACEHOLDER_RE, "placeholder_removed")]
    if english:
        subs += [(_IMG_REF_RE, "image_ref_removed"), (_QUOTED_RE, "quoted_text_removed"), (_CJK_RE, "cjk_removed")]
    removed = False
    for rx, code in subs:
        t2 = rx.sub(" ", t)
        if t2 != t:
            _flag(flags, f"{code}: {field_}")
            t, removed = t2, True
    if english:
        t = re.sub(r"\(\s*\)", " ", t)
    t = " ".join(t.split()).replace(" ,", ",")
    t = t.strip().rstrip(".;:,").strip()
    if english and removed:
        ws = t.split()
        while ws and ws[-1].lower().strip(",;:") in _FUNC_WORDS:
            ws.pop()
        while ws and ws[0].lower().strip(",;:") in ("and", "or", "but", "nor", ","):
            ws.pop(0)
        t = " ".join(ws).rstrip(".;:,").strip()
        if re.match(r"(?:of|with|for)\b", t, re.I) or re.search(
                r"\b(?:the|an?)\s+(?:" + "|".join(sorted(_PREP_WORDS | {"and", "or"})) + r")\b", t, re.I):
            t = ""  # 지운 것이 머리 명사였다 — 남은 수식어만으로는 무엇인지 모른다
    if english and t:
        w = re.match(r"[A-Za-z]+", t)
        if not (w and len(w.group(0)) > 1 and w.group(0).isupper()):
            t = t[0].lower() + t[1:]
        neg = _NEG_RE.match(t) if field_ not in _NEG_KEEP else None
        if t.lower() in _NONE_WORDS or not set(re.findall(r"[a-z0-9]+", t.lower())) - _FUNC_WORDS:
            t = ""
        elif neg and field_ == "must_not":
            rest = t[neg.end():].strip(" ,")
            ws = re.findall(r"[a-z0-9]+", rest.lower())
            if (neg.group(0).lower() == "no" and ws and len(ws) <= 8 and ws[0] not in _FUNC_WORDS | _NOT_NP
                    and not set(ws) & _AUX_WORDS):
                _flag(flags, f"must_not_neg_rewritten: {t}")
                t = rest
            else:
                _flag(flags, f"must_not_neg_dropped: {t}")
                t = ""
        elif neg:
            if field_ not in _NEG_SILENT:
                _flag(flags, f"negation_dropped: {field_}: {t}")
            t = ""
    return t


def _name_phrase(v: Any, flags: list, field_: str) -> str:
    """이름 = S() + 앞 관사 제거 + 레이어명 구분자(; : |) 제거 + Title Case 단어 소문자(약어는 그대로)."""
    t = _ARTICLE_RE.sub("", _LAYER_SEP_RE.sub(" ", _clean(v, flags, field_)))
    return " ".join(w.lower() if w[:1].isupper() and w[1:].islower() else w for w in t.split())


def _the(t: str) -> str:
    """"the …" 로 시작하게: a/an → the, 없으면 앞에 붙인다(everything / all 은 그대로 두지 않음 — ref_ignore 는 따로)."""
    if not t:
        return t
    if re.match(r"^(?:a|an)\s+", t, re.I):
        return "the " + _ARTICLE_RE.sub("", t)
    return t if re.match(r"^the\s+", t, re.I) else "the " + t


def _as_int(v: Any) -> int | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float) and v.is_integer():
        return int(v)
    if isinstance(v, str) and re.fullmatch(r"\s*-?\d+\s*", v):
        return int(v)
    return None


def _as_bool(v: Any) -> bool | None:
    if isinstance(v, bool):
        return v
    if isinstance(v, str) and v.strip().lower() in ("true", "false"):
        return v.strip().lower() == "true"
    return None


def _enum(v: Any, allowed: tuple) -> str:
    s = re.sub(r"[\s\-]+", "_", str(v).strip().lower()) if isinstance(v, str) else ""
    return s if s in allowed else ""


def _str_list(v: Any, flags: list, field_: str) -> list[str]:
    items = v if isinstance(v, list) else ([v] if isinstance(v, str) else [])
    out = []
    for x in items:
        s = _clean(x, flags, field_)
        if s and s not in out:
            out.append(s)
    return out


def _norm_bbox(v: Any, frame: tuple, flags: list, field_: str = "bbox") -> tuple[list[float] | None, str | None]:
    """RP §3 가드 → [x0, y0, x1, y1] 분율(소수 4자리)과 출처(px | q1000 | frac | px→q1000). 못 쓰면 (None, None)."""
    if not isinstance(v, (list, tuple)) or len(v) != 4:
        return None, None
    vals = []
    for x in v:
        if isinstance(x, bool):
            return None, None
        try:
            f = float(x)
        except (TypeError, ValueError):
            return None, None
        if not math.isfinite(f):
            return None, None
        vals.append(f)
    mx = max(abs(x) for x in vals)
    if mx == 0:
        return None, None  # [0,0,0,0] = 찾지 못함
    frame = tuple(frame or ("q1000",))
    if mx <= 1.0:
        src, sx, sy = "frac", 1.0, 1.0
    elif frame[0] == "px":
        W, H = float(frame[1]), float(frame[2])
        if mx > 1.05 * max(W, H):
            if mx > 1000:
                _flag(flags, f"bbox_out_of_range: {field_}")
                return None, None
            src, sx, sy = "px→q1000", 1000.0, 1000.0
            _flag(flags, f"bbox_q1000_in_px_frame: {field_}")
        else:
            src, sx, sy = "px", W, H
    else:
        src, sx, sy = "q1000", 1000.0, 1000.0
    x0, x1 = sorted((vals[0] / sx, vals[2] / sx))
    y0, y1 = sorted((vals[1] / sy, vals[3] / sy))
    x0, y0, x1, y1 = (min(1.0, max(0.0, c)) for c in (x0, y0, x1, y1))
    w, h = x1 - x0, y1 - y0
    if w < 0.01 or h < 0.01 or w * h < 0.0004 or w * h > 0.95:
        _flag(flags, f"bbox_dropped: {field_}")
        return None, None
    return [round(c, 4) for c in (x0, y0, x1, y1)], src


def _stems(w: str) -> set[str]:
    w = w.lower()
    out = {w}
    if w.endswith("es") and len(w) > 3:
        out.add(w[:-2])
    if w.endswith("s") and len(w) > 2:
        out.add(w[:-1])
    return out


def _count_flags(detail: str, counts: list[dict]) -> tuple[list[str], bool]:
    """N5: detail 의 수사(one–twelve, 숫자) + 3단어 안의 부위 명사 ≠ counts.reference → (플래그, 불일치 있음)."""
    words = re.findall(r"[a-z0-9]+", detail.lower())
    flags, bad = [], False
    for c in counts:
        ref, part = c.get("reference"), str(c.get("part") or "")
        pw = re.findall(r"[a-z0-9]+", part.lower())
        if not isinstance(ref, int) or ref < 0 or not pw:
            continue
        head = _stems(pw[-1])
        found = None
        for i, w in enumerate(words):
            n = int(w) if w.isdigit() else _NUM_WORDS.get(w)
            if n is None:
                continue
            if any(_stems(w2) & head for w2 in words[i + 1:i + 5]):
                found = (w, n)
                break
        if found and found[1] != ref:
            flags.append(f"count_mismatch: detail '{found[0]}' != counts {ref} ({pw[-1]})")
            bad = True
        elif not found and isinstance(c.get("canvas"), int) and c["canvas"] >= 0 and c["canvas"] != ref:
            flags.append(f"count_not_in_detail: {pw[-1]} ref {ref} canvas {c['canvas']}")
    return flags, bad


def _detail(v: Any, flags: list, field_: str, max_clauses: int = 5, max_words: int = 60) -> str:
    """절 단위 S(): 문장 마침표 → "; ", 빈 절 제거, ≤max_clauses 절·≤max_words 단어(뒤 절부터 버림)."""
    raw = v if isinstance(v, str) else ""
    raw = re.sub(r"\.\s+(?=\S)", "; ", raw)
    clauses = [c for c in (_clean(x, flags, field_) for x in raw.split(";")) if c]
    trimmed = False
    while len(clauses) > 1 and (len(clauses) > max_clauses or sum(len(c.split()) for c in clauses) > max_words):
        clauses.pop()
        trimmed = True
    if trimmed:
        _flag(flags, f"trimmed: {field_}")
    return "; ".join(clauses)


def _location(v: Any, flags: list) -> str:
    """N6: location 은 렌더에서 "Add the <name> <location>, with …" 로 이어 읽힌다. 허용 전치사(_LOC_RE, in·under·next to …
    포함)면 그대로. 분사구("tucked behind the ear", "centered")·where 절·close/opposite/up/down 으로 시작하면 그대로 두고
    location_kept, 한정사(the, its, this …)로 시작하면 "on ", 명사구면 "on the "(location_prefixed). 남은 기능어
    (of, with, for, from, as, and …)·조동사로 시작하면 이어 붙일 수 없어 버린다(location_dropped)."""
    loc = _clean(v, flags, "location")
    if not loc or _LOC_RE.match(loc):
        return loc
    ws = re.findall(r"[a-z]+", loc.lower())
    if ws and ws[0] in _LOC_ADV:
        ws = ws[1:]
    w, nxt = (ws + ["", ""])[:2]
    if w in _LOC_KEEP or (re.fullmatch(r"[a-z]{2,}(?:ed|ing)", w) and w not in _ED_ING_NOUNS
                          and (not nxt or nxt.endswith("ly") or nxt in (_PREP_WORDS - {"of"}) | _LOC_ADV | _LOC_KEEP)):
        _flag(flags, "location_kept")
        return loc
    if w in _DETERMINERS:
        _flag(flags, "location_prefixed")
        return "on " + loc
    if not w or w in (_FUNC_WORDS - {"same"}) | _AUX_WORDS:
        _flag(flags, f"location_dropped: {loc}")
        return ""
    _flag(flags, "location_prefixed")
    return "on the " + loc


def _norm_target(item: dict, t: TargetIn, ctx: CropCtx, req: Request, con: dict, guide: bool, sugg: dict
                 ) -> tuple[dict, dict]:
    """타깃 하나 → (스펙에 쓸 필드, _review). 규칙: RP §9 표 + N0(렌더 행렬)·N1(text)·N2(name)·N3(mode·refs)·N4(side)·
    N5(count)·N6(location)·N7(detail)·N10·N11(bbox)·N14(shape·ref_object·add)·N15(사람 refs 와 VLM 이 본 이미지)."""
    fl: list[str] = []
    out: dict = {}
    unr: dict = {}
    tid = t.tid
    # N2 name — name_en·용어집 적중이 없을 때만 쓴다
    name = _name_phrase(item.get("name"), fl, "name")
    if len(name.split()) > 8:
        _flag(fl, "name_too_long")
        name = ""
    fixed = t.name_en or t.gloss or ""
    name_sugg = ""
    if name and not fixed:
        out["name"] = name
    elif name and name.lower() != fixed.lower():
        name_sugg = name
        sugg["name"][tid] = name
    eff_name = out.get("name") or fixed or name or t.label
    # found / action
    act_raw = _enum(item.get("action"), ("fix", "add"))
    found = _as_bool(item.get("found"))
    if found is None:
        found = act_raw != "add"
    action = "fix" if found else "add"
    if act_raw and act_raw != action:
        _flag(fl, f"action_found_mismatch: {act_raw}")
    # mode — sketch_guide / paste_harmonize 는 ref_correct + suggested_mode
    sug_mode = _enum(item.get("suggested_mode"), ("none", "paste_harmonize", "sketch_guide")) or "none"
    mraw = _enum(item.get("mode"), tuple(dprompt.MODES))
    if mraw in ("sketch_guide", "paste_harmonize"):
        _flag(fl, f"mode_coerced: {mraw}")
        sug_mode = mraw if sug_mode == "none" else sug_mode
        mraw = "ref_correct"
    # ref_images → refs (shown_refs[k-2] 만, 1·범위 밖·중복·경로 이탈 거부, 최대 2 / different 면 1)
    ref_view = _enum(item.get("ref_view"), dprompt.REF_VIEWS)
    refs, bad, outside = [], [], []
    for k in item.get("ref_images") if isinstance(item.get("ref_images"), list) else []:
        kk = _as_int(k)
        if kk is None or kk < 2 or kk - 2 >= len(req.shown_refs):
            bad.append(str(k))
            continue
        r = req.shown_refs[kk - 2]
        if ".." in r.replace("\\", "/").split("/") or "\\" in r:
            bad.append(str(k))
            outside.append(r)  # 노드가 쓰지는 않지만 VLM 이 본 이미지(N15 비교용 — 사람 ref 가 refs_dir 밖일 수 있음)
        elif r not in refs:
            refs.append(r)
    if bad:
        _flag(fl, f"ref_images_rejected: {', '.join(bad)}")
    cap = 1 if ref_view == "different" else 2
    if len(refs) > cap:
        _flag(fl, f"refs_capped: {cap}")
        refs = refs[:cap]
    dmode = mraw or ("ref_correct" if refs else "self_restore")
    if dmode == "ref_correct" and not refs:
        _flag(fl, "no_valid_ref: self_restore")
        dmode = "self_restore"
    if dmode == "self_restore":
        refs = []
    # N3 — 사람 mode·refs 가 있으면 쓰지 않고 suggestions 로. 사람 refs 만 있어도 mode 는 쓰지 않는다(Q6): 기본 ref_correct
    # 로 렌더되어 사람 refs 가 쓰인다(self_restore 를 쓰면 그 refs 와 사람 detail 이 렌더에서 사라진다)
    hmode, hrefs, hact = con.get("mode"), con.get("refs"), con.get("action")
    if hmode is None and not hrefs:
        out["mode"] = dmode
    elif (hmode or "ref_correct") != dmode:
        sugg["mode"][tid] = dmode
        if hmode is None:
            _flag(fl, "self_restore_vs_human_refs")
    eff_mode = hmode or ("ref_correct" if hrefs else dmode)
    if hrefs is not None:
        if refs and refs != list(hrefs):
            sugg["refs"][tid] = refs
        final_refs: list | None = list(hrefs)
    elif eff_mode == "self_restore":
        out["refs"] = []  # 자동 선택을 막는다(prompt.py:549-557)
        final_refs = []
    elif refs:
        out["refs"] = refs
        final_refs = refs
    else:
        final_refs = None  # 사람 mode 인데 고른 레퍼런스 없음 → Prepare 가 refs_auto 에서 자동 선택
        _flag(fl, "human_mode_without_ref")
    has_refs = bool(final_refs) if final_refs is not None else bool(ctx.refs or ctx.refs_hidden)
    # 실제 렌더 mode(spec_from_manifest 의 보정과 같게)
    rmode = eff_mode
    if rmode == "sketch_guide" and not guide:
        rmode = "ref_correct"
    if rmode in ("ref_correct", "sketch_guide") and not has_refs:
        rmode = "self_restore"
    eff_action = hact or action
    if rmode == "ref_correct" and eff_action in ("relocate", "replace"):
        _flag(fl, "action_renders_as_fix")
    is_add = rmode == "ref_correct" and eff_action == "add"
    is_fix = rmode == "ref_correct" and not is_add
    # N15 사람 refs 가 렌더되는데 VLM 이 고른 이미지 중 사람 refs 밖의 것이 있거나 아무것도 안 골랐으면, 그 이미지를 보고 쓴
    # ref_object·ref_ignore·ref_view·detail·must_not·exact_ref 를 사람 refs 옆에 렌더하지 않는다(_review.unrendered, 신뢰 낮음).
    # self_restore 는 refs 가 렌더되지 않아 해당 없음
    ref_foreign = False
    if hrefs and rmode != "self_restore":
        sent = {}
        for im in ctx.refs:
            if im.name in req.shown_refs:
                sent[_ref_key(im.name)] = sent[_ref_key(os.path.abspath(im.src_path))] = im.name
        hnames = [sent.get(_ref_key(r)) for r in hrefs]
        unseen = [os.path.basename(str(r)) for r, n in zip(hrefs, hnames) if n is None]
        if unseen:
            _flag(fl, f"human_ref_not_shown: {', '.join(unseen)}")
        picks = refs + [r for r in outside if r not in refs]
        ref_foreign = not picks or any(r not in hnames for r in picks)
        if ref_foreign:
            _flag(fl, f"refs_differ_from_human: {', '.join(os.path.basename(r) for r in picks) or 'none'}")
    # 필드 정규화
    vals: dict[str, Any] = {"action": action}
    ro = _the(_clean(item.get("ref_object"), fl, "ref_object"))
    if len(ro.split()) > 10:
        _flag(fl, "ref_object_too_long")
        ro = ""
    vals["ref_object"] = ro
    ri = _clean(item.get("ref_ignore"), fl, "ref_ignore")
    vals["ref_ignore"] = ri if not ri or re.match(r"^(?:the|everything|all)\b", ri, re.I) else "the " + ri
    vals["ref_view"] = ref_view
    detail = _detail(item.get("detail"), fl, "detail")
    vals["detail"] = detail
    mn = []
    for x in (_the(x) for x in _str_list(item.get("must_not"), fl, "must_not")):
        if re.search(rf"(?<![a-z0-9]){re.escape(_ARTICLE_RE.sub('', x).lower())}(?![a-z0-9])", detail.lower()):
            _flag(fl, f"must_not_in_detail: {x}")  # 지우라는 것이 레퍼런스 디자인에 있다(단어 단위 비교)
        else:
            mn.append(x)
    vals["must_not"] = mn[:3]
    vals["defects"] = _detail(item.get("defects"), fl, "defects", max_clauses=3)
    vals["location"] = _location(item.get("location"), fl)
    vals["occluded_by"] = _the(_clean(item.get("occluded_by"), fl, "occluded_by"))
    vals["shape"] = _enum(item.get("shape"), tuple(dprompt.SHAPE))
    shape_sugg = vals["shape"] == "locked" and is_fix   # N14: 잠금은 모양 결함까지 지킨다(A/B 10/13) → locked 는 사람만
    if vals["shape"] == "locked":
        vals["shape"] = "moderate"
    # N0 — 렌더되는 필드만 스펙에, 나머지는 _review.unrendered
    rendered = {"action": rmode in ("ref_correct", "sketch_guide"),
                "ref_object": rmode != "self_restore" and has_refs, "ref_ignore": rmode != "self_restore" and has_refs,
                "ref_view": rmode in ("ref_correct", "sketch_guide"), "detail": rmode != "self_restore",
                "must_not": is_fix or rmode == "sketch_guide", "defects": rmode == "self_restore", "location": is_add,
                "occluded_by": rmode in ("sketch_guide", "paste_harmonize"), "shape": is_fix}
    if ref_foreign:
        rendered.update(dict.fromkeys(("ref_object", "ref_ignore", "ref_view", "detail", "must_not"), False))
    for f in ("action", "ref_object", "ref_ignore", "ref_view", "detail", "must_not", "defects", "location",
              "occluded_by", "shape"):
        v = vals[f]
        if v in ("", None, []):
            continue
        (out if rendered[f] else unr)[f] = v
    # N14 여럿인 타깃(pair of pins)을 레퍼런스에서 하나만 가리킴("the upper red pen") → 나머지는 안 바뀐다
    if out.get("ref_object") and _plural(eff_name) and not _plural(out["ref_object"]):
        _flag(fl, f"ref_object_singular: {out['ref_object']} (target {eff_name})")
    # N1 text
    text = _enum(item.get("text"), ("none", "keep", "exact_ref", "plausible"))
    if text == "plausible":
        _flag(fl, "text_plausible: keep")
        text = "keep"
    if text == "exact_ref" and (rmode == "self_restore" or not has_refs):
        _flag(fl, "exact_ref_without_ref")
        text = "keep"
    if text == "exact_ref" and ref_foreign:  # N15 다른 이미지의 글자를 사람 refs 에서 베끼라고 하지 않는다
        unr["text"] = text
        text = "keep"
    if text == "keep" and any(_LETTER_RE.search(x) for x in out.get("must_not") or []):
        _flag(fl, "text_contradiction")
        text = "none"
    if text == "none" and _LETTER_RE.search(out.get("detail") or ""):
        _flag(fl, "text_none_but_lettering")
    if text:
        out["text"] = text
    # N7 detail
    if out.get("detail"):
        if len(out["detail"].split(";")) == 1:
            _flag(fl, "v4_equals_v1")
        if _LIGHT_RE.search(out["detail"]):
            _flag(fl, "lighting_words")
    # N11 bbox
    bb, bsrc = _norm_bbox(item.get("bbox"), req.bbox_frame, fl)
    if bb is not None:
        out["bbox"] = bb
    # 검토 정보(_review)
    conf = "low" if ref_foreign else _enum(item.get("confidence"), ("high", "medium", "low")) or "medium"
    counts = []
    for c in item.get("counts") if isinstance(item.get("counts"), list) else []:
        if isinstance(c, dict) and _clean(c.get("part"), [], "counts", english=False):
            ref_n, can_n = _as_int(c.get("reference")), _as_int(c.get("canvas"))
            counts.append({"part": _clean(c.get("part"), fl, "counts"),
                           "reference": -1 if ref_n is None else ref_n, "canvas": -1 if can_n is None else can_n})
    counts = counts[:4]
    if is_add:  # N14 add 인데 VLM 자신의 counts 가 그 부품이 캔버스에 있다고 함
        th = _head_stems(eff_name) | _head_stems(ro)
        seen = next((c for c in counts if c["canvas"] >= 1 and _head_stems(c["part"]) & th), None)
        if seen:
            _flag(fl, f"add_but_visible: {seen['part']} canvas {seen['canvas']}")
    cflags, mismatch = _count_flags(out.get("detail") or "", counts)
    for f in cflags:
        _flag(fl, f)
    if mismatch:
        conf = "low"
    # N4 side — 고른 레퍼런스가 크롭과 반대 side 이고, 보여 준 것 중 같은 side 가 있으면
    opp = {"left": "right", "right": "left"}.get(ctx.side or "")
    if opp and any(side_of(r) == opp for r in refs) and any(r.side == ctx.side for r in ctx.refs):
        _flag(fl, f"side_mismatch: {', '.join(os.path.basename(r) for r in refs if side_of(r) == opp)} (crop {ctx.side})")
        if _CONF_RANK[conf] > _CONF_RANK["medium"]:
            conf = "medium"
    rv = {"confidence": conf, "note": " ".join(str(item.get("review_note") or "").split()), "found": found,
          "counts": counts, "canvas_now": _clean(item.get("canvas_now"), [], "canvas_now"), "suggested_mode": sug_mode,
          "unrendered": unr, "flags": fl}
    if bsrc:
        rv["bbox_src"] = bsrc
    if name_sugg:
        rv["name_suggestion"] = name_sugg
    if shape_sugg:
        rv["shape_suggestion"] = "locked"
    if refs:
        rv["side"] = side_of(refs[0]) or "none"
    rv["_name"] = eff_name  # 내부용(preserve·ref_ignore 계산) — normalize 에서 뺀다
    rv["_rmode"] = rmode    # 내부용(N9: ref_ignore 가 렌더되는 소유자)
    return out, rv


def _drop_cut_string(obj: Any, sep: str = ";") -> str | None:
    """잘린 출력(repairs "truncated_string")에서 끊긴 문자열 = 문서 순서 마지막 잎을 제자리에서 고친다: 목록 원소면 빼고,
    값이면 마지막 sep 절만 버린다(절이 하나면 ""). 반환 = 고친 경로(플래그용), 마지막 잎이 문자열이 아니면 None."""
    path, parent, key, node = [], None, None, obj
    while isinstance(node, (dict, list)) and node:
        parent, key = node, (list(node)[-1] if isinstance(node, dict) else len(node) - 1)
        path.append(str(key))
        node = parent[key]
    if parent is None or not isinstance(node, str):
        return None
    if isinstance(parent, list):
        parent.pop()
    else:
        parent[key] = node.rpartition(sep)[0].strip()
    return ".".join(path)


def _np_words(phrase: str) -> list[str]:
    """명사구 몸통의 내용어 — 첫 전치사·관계사(또는 관사가 뒤따르는 -ing 분사) 앞까지, "of" 는 이어 본다(N8).
    "the pink hair beneath the ornaments" → [pink, hair], "pair of ribbon loops" → [pair, ribbon, loops]."""
    ws = re.findall(r"[a-z0-9]+", phrase.lower())
    out = []
    for i, w in enumerate(ws):
        if w in _NP_CUT or (out and w.endswith("ing") and ws[i + 1:i + 2] in (["the"], ["a"], ["an"], ["its"], ["their"])):
            break
        if w not in _NP_SKIP:
            out.append(w)
    return out


def _head_stems(phrase: str) -> set[str]:
    """머리 명사 어간 — 콜론·쉼표·괄호 앞 몸통(_np_words)의 마지막 내용어(위치어 제외, 3자 이상). 없으면 빈 집합.
    "chest clasps: upper, middle, lower" → clasps, "the clip on the left" → clip."""
    ws = [w for w in _np_words(re.split(r"[:,;(]", phrase, maxsplit=1)[0]) if w not in _POS_WORDS]
    return _stems(ws[-1]) if ws and len(ws[-1]) >= 3 else set()


def _plural(phrase: str) -> bool:
    """명사구 몸통(첫 전치사·관계사 앞)이 여럿을 가리키나: pair·both·two… 가 있거나 머리 명사가 -s(-ss·-us·-is 제외)."""
    body = list(itertools.takewhile(lambda w: w not in _NP_CUT, re.findall(r"[a-z0-9]+", phrase.lower())))
    if any(w in _MANY_WORDS or (w.isdigit() and int(w) > 1) for w in body):
        return True
    ws = [w for w in body if w not in _NP_SKIP and w not in _POS_WORDS]
    return bool(ws) and len(ws[-1]) > 3 and ws[-1].endswith("s") and not ws[-1].endswith(("ss", "us", "is"))


def normalize(parsed: dict, ctx: CropCtx, req: Request, *, constraints: dict, glossary: dict,
              repairs: list | None = None) -> Draft:
    """파싱된 크롭 출력 → Draft(RP §9 + §4.4 N0–N15). 매니페스트에 없는 id·중복 id 는 버리고 플래그, 출력에 없는 타깃은
    Draft.targets 에 넣지 않는다(병합이 그 타깃을 건드리지 않음). 타깃 1개·출력 1개면 id 가 틀려도 그 타깃으로 본다.
    constraints = constraints_for(ctx, 현재 스펙), glossary 는 지금 단계에서 쓰지 않는다(용어집 적중은 ctx 에 있음).
    repairs = 원장 done 의 수리 목록: 잘린 출력("truncated…")이면 끊긴 마지막 문자열을 버리고("truncated_string"),
    크롭 플래그 truncated_output + 모든 타깃 confidence low."""
    flags: list[str] = []
    parsed = parsed if isinstance(parsed, dict) else {}
    truncated = [str(r) for r in repairs or [] if str(r).startswith("truncated")]
    if truncated:
        cut = None
        if "truncated_string" in truncated:
            parsed = copy.deepcopy(parsed)  # 원장 done 은 그대로 둔다
            cut = _drop_cut_string(parsed)
        _flag(flags, "truncated_output" + (f": 끊긴 값 버림 {cut}" if cut else ""))
    tin = {t.tid: t for t in ctx.targets}
    items = [x for x in (parsed.get("targets") if isinstance(parsed.get("targets"), list) else []) if isinstance(x, dict)]
    by_tid: dict[str, dict] = {}
    unknown = []
    for item in items:
        tid = str(item.get("id") or "").strip().upper()
        if tid not in tin:
            unknown.append(tid or "?")
        elif tid in by_tid:
            _flag(flags, f"dup_target: {tid}")
        else:
            by_tid[tid] = item
    if not by_tid and len(tin) == 1 and len(items) == 1:
        by_tid[ctx.targets[0].tid] = items[0]
        _flag(flags, f"id_coerced: {unknown[0] if unknown else '?'} -> {ctx.targets[0].tid}")
        unknown = []
    if unknown:
        _flag(flags, f"unknown_target: {', '.join(unknown)}")
    missing = [t.tid for t in ctx.targets if t.tid not in by_tid]
    if missing:
        _flag(flags, f"missing_target: {', '.join(missing)}")
    guide = bool((ctx.spec or {}).get("guide")) if isinstance(ctx.spec, dict) else False
    sugg: dict = {"name": {}, "mode": {}, "refs": {}, "targets": [], "layer_name": "", "glossary": {}}
    targets: dict[str, dict] = {}
    review: dict[str, dict] = {}
    for t in ctx.targets:
        if t.tid in by_tid:
            targets[t.tid], review[t.tid] = _norm_target(by_tid[t.tid], t, ctx, req, constraints.get(t.tid) or {},
                                                         guide, sugg)
    names = {tid: rv.pop("_name") for tid, rv in review.items()}
    rmodes = {tid: rv.pop("_rmode") for tid, rv in review.items()}
    if truncated:
        for rv in review.values():
            rv["confidence"] = "low"
            _flag(rv["flags"], "truncated_output")
    # N9 — 공유 레퍼런스의 범례는 ref_ignore 가 있는 첫 소유자(스펙 타깃 순서)의 값만 렌더된다(prompt.py:214-218).
    # ref_ignore 가 렌더되는 소유자(self_restore 아님) 모두에 같은 "everything except …" 를 쓴다 — 그 타깃이 ref_ignore 를
    # 안 썼어도, 사람이 스펙에서 타깃 순서를 바꿔도 범례가 같다
    con_refs = {tid: (constraints.get(tid) or {}).get("refs") for tid in targets}
    owners: dict[str, list[str]] = {}
    for tid in targets:
        for r in targets[tid].get("refs") or con_refs.get(tid) or []:
            owners.setdefault(r, []).append(tid)
    for r, tids in owners.items():
        shown = [t for t in tids if rmodes[t] != "self_restore"]
        if len(tids) < 2 or not shown or not any(targets[t].get("ref_ignore") for t in tids):
            continue
        objs = list(dict.fromkeys(targets[t].get("ref_object") or f"the {names[t]}" for t in tids))
        for t in shown:
            targets[t]["ref_ignore"] = "everything except " + _join_words(objs)
            _flag(review[t]["flags"], f"shared_ref_ignore: {os.path.basename(r)}")
    # N8 preserve — background 가 든 항목, 항목 몸통(첫 전치사 앞, _np_words)에 타깃 이름·ref_object 의 머리 명사가 든
    # 항목은 버린다("the pink hair beneath the ornaments" 는 남는다)
    heads = set()
    ref_objects = {tid: targets[tid].get("ref_object") or review[tid]["unrendered"].get("ref_object") or "" for tid in targets}
    for tid in targets:  # 렌더하지 않은 ref_object(N15·self_restore)도 타깃을 가리키는 말이다
        heads |= _head_stems(names[tid]) | _head_stems(ref_objects[tid])
    preserve = []
    for p in _str_list(parsed.get("preserve"), flags, "preserve"):
        p = _the(p)
        if "background" in p.lower() or any(_stems(w) & heads for w in _np_words(p)):
            _flag(flags, f"preserve_dropped: {p}")
            continue
        if p not in preserve:
            preserve.append(p)
    # N14 남은 항목의 첫 전치사가 in·inside·within·into 이고 그 뒤 명사구에 타깃의 머리 명사(name·ref_object·counts 부위)가
    # 있으면 타깃 안을 지키라는 말 — 경고만("the dark inset inside the lower-left loop", 고리 = 매듭 counts 의 부위).
    # 겹침·주변(_HOLD_WORDS·_IN_RELATION + of)은 뺀다
    vocab = {tid: set().union(_head_stems(names[tid]), _head_stems(ref_objects[tid]),
                              *(_head_stems(c["part"]) for c in review[tid]["counts"]))
             for tid in targets}
    for p in preserve[:4]:
        ws = re.findall(r"[a-z0-9]+", p.lower())
        i = next((k for k, w in enumerate(ws) if w in _NP_CUT), None)
        if i is None or ws[i] not in _INSIDE_WORDS:
            continue
        before = [w for w in ws[max(0, i - 2):i] if not w.endswith("ly")][-1:]
        if ws[i] in ("in", "into") and before and before[0] in _HOLD_WORDS:
            continue
        j = i + 1 + (ws[i + 1:i + 2] in (["the"], ["a"], ["an"]))
        if ws[j + 1:j + 2] == ["of"] and _stems(ws[j]) & _IN_RELATION and (
                ws[j] not in _IN_MIDDLE or _plural(" ".join(ws[j + 2:]))):
            continue
        rest = _np_words(" ".join(ws[i + 1:]))
        hit = [tid for tid, hs in vocab.items() if any(_stems(w) & hs for w in rest)]
        if hit:
            _flag(flags, f"preserve_in_target: {p} ({', '.join(hit)})")
    crop = {"preserve": preserve[:4]} if preserve else {}
    # 제안 타깃(목록 밖에서 찾은 틀린 요소)
    for st in (parsed.get("suggested_targets") if isinstance(parsed.get("suggested_targets"), list) else []):
        if not isinstance(st, dict) or len(sugg["targets"]) >= MAX_SUGGESTED:
            continue
        nm = _name_phrase(st.get("name"), flags, "suggested_targets.name")
        if not nm:
            continue
        bb, _ = _norm_bbox(st.get("bbox"), req.bbox_frame, flags, "suggested_targets.bbox")
        sugg["targets"].append({"name": nm, "reason": " ".join(str(st.get("reason") or "").split()), "bbox": bb})
    # 용어집 제안: 이름을 VLM 이 지은 한글 라벨(레거시 = 부위 전체라 제외)
    for t in ctx.targets:
        nm = targets.get(t.tid, {}).get("name")
        if nm and not t.legacy and t.label and not t.label.isascii():
            sugg["glossary"][t.label] = nm
    obs = []
    for o in (parsed.get("observations") if isinstance(parsed.get("observations"), list) else [])[:10]:
        if isinstance(o, dict):
            obs.append({"image": str(o.get("image") or ""), "shows": " ".join(str(o.get("shows") or "").split())})
    draft = Draft(crop=crop, targets=targets, review=review, suggestions=sugg, observations=obs, flags=flags,
                  transport={"schema_enforced": parsed.get("schema_echo") == SCHEMA_ECHO})
    sugg["layer_name"] = paste_layer_name(ctx, draft)
    return draft


def normalize_style(parsed: dict, repairs: list | None = None) -> tuple[str | None, list[str]]:
    """화풍 출력 → (style_note | None, 플래그). S() + 대시 → ", ", 문장 마침표 → ", ", 소문자, ≤30 단어(뒤 항목부터 버림).
    repairs(원장 done)가 잘린 출력이면 플래그 style_truncated, 끊긴 문자열이 style_note 면 마지막 항목을 버린다."""
    flags: list[str] = []
    if any(str(r).startswith("truncated") for r in repairs or []) and isinstance(parsed, dict):
        flags.append("style_truncated")
        if "truncated_string" in repairs:
            parsed = copy.deepcopy(parsed)
            _drop_cut_string(parsed, sep=",")
    raw = parsed.get("style_note") if isinstance(parsed, dict) else None
    if not isinstance(raw, str):
        return None, ["style_missing"]
    t = re.sub(r"\s*[\u2014\u2013]\s*|\s+-\s+", ", ", raw)
    t = re.sub(r"\.\s+(?=\S)", ", ", t)
    t = _clean(t, flags, "style_note").lower()
    items = [x.strip() for x in t.split(",") if x.strip()]
    trimmed = False
    while len(items) > 1 and sum(len(x.split()) for x in items) > 30:
        items.pop()
        trimmed = True
    if trimmed:
        flags.append("style_trimmed")
    t = ", ".join(items)
    if not t:
        return None, flags + ["style_empty"]
    return t, flags


_LAYER_SEP_RE = re.compile("[;:|\uff1b\uff1a\uff5c\u2502\u00a6]")


def paste_layer_name(ctx: CropCtx, draft: Draft) -> str:
    """바로 붙여 넣을 레이어명 "<crop.name> | A: <지금 A 문구>; B: <제안 1>; …"(새로 붙이는 이름은 ASCII, ; : | 제거).
    store.parse_crop_layer_name 으로 다시 읽어 타깃 수가 맞아야 한다(아니면 ValueError — 버그).
    이미 있는 타깃은 지금 레이어명의 문구를 그대로 둔다 — 라벨이 바뀌면 재가져오기 때 매니페스트의 사람 mode·refs·name_en 이
    초기화되고 그 타깃이 "타깃 변경" 이 되기 때문. 문구가 없는 타깃(레거시 = 부위 전체)과 제안 타깃만 이름을 짓는다:
    name_en → 용어집 적중 → 초안 이름, ASCII 이름이 없으면 라벨."""
    def clean(s: str, ascii_only: bool = True) -> str:
        s = _LAYER_SEP_RE.sub(" ", unicodedata.normalize("NFKC", str(s or "")))
        if ascii_only:
            s = "".join(ch for ch in s if 32 <= ord(ch) < 127)
        return " ".join(s.split())

    try:
        now = {t["tid"]: t["text"] for t in store.parse_crop_layer_name(ctx.layer_name)["targets"]}
    except ValueError:  # 매니페스트에 들어온 레이어명은 이미 파싱된 것 — 손상 시 이름 짓기로
        now = {}
    pairs = []
    for t in ctx.targets:
        if now.get(t.tid):
            pairs.append((t.tid, now[t.tid]))
            continue
        rv = draft.review.get(t.tid) or {}
        nm = clean(t.name_en or t.gloss or (draft.targets.get(t.tid) or {}).get("name") or rv.get("name_suggestion") or "")
        pairs.append((t.tid, nm or clean(t.label, ascii_only=False) or t.tid))
    used = {tid for tid, _ in pairs}
    free = [chr(c) for c in range(ord("A"), ord("Z") + 1) if chr(c) not in used]
    for st in draft.suggestions.get("targets") or []:
        nm = clean(st.get("name") or "")
        if nm and free:
            pairs.append((free.pop(0), nm))
    s = f"{ctx.name} | " + "; ".join(f"{tid}: {nm}" for tid, nm in pairs)
    p = store.parse_crop_layer_name(s)
    if len(p["targets"]) != len(pairs) or store.crop_id_for(s) != store.crop_id_for(ctx.layer_name or ctx.name):
        raise ValueError(f"{_TAG} 제안 레이어명이 다시 읽히지 않습니다: {s!r}")
    return s


# ══════════════════════════════════════════════════════════════════════
# 병합·점검·쓰기 (§2.8, §5)
# ══════════════════════════════════════════════════════════════════════
@dataclass
class DiffRow:
    path: str
    kind: str   # add | update | delete | keep_human | keep_deleted | yield | unrendered | unknown_target | dup_target | force
    old: Any
    new: Any


@dataclass
class MergeResult:
    spec: dict | None        # 쓸 내용. None = 쓰지 않음(변화 없음·preview·건너뜀)
    changed: bool
    rows: list[DiffRow]
    backup: bool
    render_changed: bool
    notes: list[str]
    merged: dict | None = None   # 병합 결과(preview·변화 없음이어도 채움 — 렌더 점검·보고용)


_MISSING = object()
_DIFF_MARK = {"add": "+", "update": "~", "delete": "~", "force": "~", "keep_human": "=", "keep_deleted": "-",
              "yield": "\u2193"}


def _canon(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str)


def _eq(a: Any, b: Any) -> bool:
    return _canon(a) == _canon(b)


def _merge_path(rule: str, C: Any, B: Any, N: Any, *, style: bool = False, yielded: bool = False) -> tuple:
    """§5.2 표의 한 칸. _MISSING = 없음. → (새 C, 새 B, 행 종류 | None, pending 에 N, deleted 에 경로).
    rule = fill | refresh | force (preview 는 refresh 로 계산). style=True 는 style_note(C==B 면 fill 에서도 N)."""
    e = lambda v: v is _MISSING  # noqa: E731
    if yielded:  # 사람 출처가 생겨 소유권이 없어짐: 노드가 쓴 그대로면 지운다(모든 모드)
        if not e(C) and not e(B) and _eq(C, B):
            return _MISSING, _MISSING, "yield", False, False
        return C, B, None, False, False
    if not e(C) and not e(N) and _eq(C, N):
        return C, N, None, False, False
    if e(C) and e(B):
        return (N, N, "add", False, False) if not e(N) else (C, B, None, False, False)
    if not e(C) and not e(B) and _eq(C, B):
        if rule == "fill" and not style:
            return C, B, None, False, False
        kind = "force" if rule == "force" else ("delete" if e(N) else "update")
        return N, N, kind, False, False
    if rule == "force":
        return N, N, "force", False, False
    if e(C):  # B 있음 = 사람이 지움
        return C, B, ("keep_deleted" if not e(N) else None), False, not e(N)
    return C, B, ("keep_human" if not e(N) else None), not e(N), False  # 사람이 고쳤거나 직접 쓴 값


def _rendered_view(spec: dict | None) -> str:
    """렌더에 쓰이는 부분만(_ 키·bbox 제외) — render_changed 판정용."""
    s = {k: v for k, v in (spec or {}).items() if not str(k).startswith("_") and k != "targets"}
    s["targets"] = [{k: v for k, v in t.items() if not str(k).startswith("_") and k != "bbox"}
                    for t in (spec or {}).get("targets") or [] if isinstance(t, dict)]
    return _canon(s)


def _set_key(d: dict, k: str, v: Any) -> None:
    if v is _MISSING:
        d.pop(k, None)
    else:
        d[k] = copy.deepcopy(v)  # 있는 키는 제자리, 새 키는 뒤에


def _to_end(d: dict, k: str) -> None:
    if k in d:
        d[k] = d.pop(k)


def merge_spec(current: dict | None, draft: Draft | None, ctx: CropCtx, *, mode: str, project_style: str | None,
               provenance: dict) -> MergeResult:
    """필드 단위 3-way 병합(§5.2). draft None = 화풍 전파·양보만.
    - mode: fill | refresh | preview | force. force 는 only 에 든 크롭에만 넘길 것(그 밖의 크롭은 fill).
      fill 에서 _hint 변경·mock 교체로 다시 초안한 크롭은 refresh 규칙(타깃 추가가 겹쳐도), 라벨이 바뀐 타깃(타깃 변경)은
      그 타깃만 refresh 규칙, 키만 바뀐 초안(갱신 가능)은 적용하지 않는다. fill 에서 값을 받지 않은 기존 타깃은 _review 유지 —
      그 값·_review 가 나온 키를 _draft.target_keys[tid] 에 남겨(_draft.key 와 다를 때만) 같은 병합을 다시 해도 고정점.
      force 는 merge_mode 로 키마다 1회(_draft.forced_key) — 초안 없이(draft None) 또는 같은 키로 다시 오면 fill 규칙.
    - project_style: _project.json 의 style_note(문자열) / None = 키 없음(노드가 쓴 그대로인 크롭에서 지움) / "" = 전파 끔.
    - provenance: 초안 출처 = 그 원장 done 을 만든 요청(key, attempt, backend 이름, model, effort, image_detail, prompt,
      drafted = done 의 at, credits, round, refs_shown, transport = done 의 transport). 그대로 _draft 머리가 된다.
    - 사람 refs(스펙 — force 제외 — 또는 매니페스트)가 있으면 노드가 쓴 mode 는 양보(지움): 기본 ref_correct 로 그 refs 가 렌더된다.
    - _accepted: true 인 크롭은 force 가 아니면 건드리지 않는다. 결과를 다시 병합하면 changed=False(멱등)."""
    if mode not in WRITE_MODES:
        raise ValueError(f"{_TAG} write_mode 값이 잘못되었습니다: {mode!r}")
    cur = copy.deepcopy(current) if isinstance(current, dict) else None
    spec = copy.deepcopy(cur) if cur is not None else {}
    notes: list[str] = []
    rows: list[DiffRow] = []
    prov = dict(provenance or {})
    if mode == "force" and merge_mode(spec, mode, prov.get("key") if draft is not None else None) != "force":
        mode = "fill"
        notes.append("force 는 이 키로 이미 적용함(또는 초안 없음) → fill 규칙(사람 편집·_accepted 유지)")
    if spec.get("_accepted") is True and mode != "force":
        return MergeResult(None, False, [], False, False, ["_accepted — 건드리지 않음(force + only 만)"], merged=cur)
    d_old = spec.get("_draft") if isinstance(spec.get("_draft"), dict) else None
    rule = "refresh" if mode == "preview" else mode
    moved: set[str] = set()
    if draft is not None and mode == "fill":
        whys = _fill_reasons(d_old, ctx, prov.get("backend") or BACKEND_NAMES["mock"])
        moved = set(_relabeled(d_old, ctx))
        why = next((w for w in whys if w in ("_hint 변경", "mock 초안 교체")), None)
        if why:
            rule = "refresh"
            notes.append(f"{why} → refresh 규칙으로 병합")
        elif moved:
            notes.append(f"타깃 변경({', '.join(sorted(moved))}) → 그 타깃만 refresh 규칙으로 병합")
        elif not whys and d_old.get("key") != prov.get("key"):
            draft = None
            notes.append("갱신 가능 — 키가 바뀐 초안은 fill 에서 적용하지 않음(write_mode=refresh 로 갱신)")
    w_old = d_old.get("written") if d_old and isinstance(d_old.get("written"), dict) else {}
    # 타깃별 "값·_review 가 나온 초안 키"(_draft.target_keys — _draft.key 와 다른 타깃만 기록, 없으면 _draft.key)
    tk_old = d_old.get("target_keys") if d_old and isinstance(d_old.get("target_keys"), dict) else {}
    target_keys = {k: v for k, v in tk_old.items() if v != prov.get("key")}  # 병합하지 않는 타깃(중복 id 등)은 기록 유지
    written = {k: copy.deepcopy(v) for k, v in w_old.items() if isinstance(v, dict)}
    written.setdefault("crop", {})
    pending = dict(d_old.get("pending") or {}) if d_old and isinstance(d_old.get("pending"), dict) else {}
    deleted = [str(p) for p in d_old.get("deleted") or []] if d_old and isinstance(d_old.get("deleted"), list) else []

    def apply(path: str, holder: dict, wslot: dict, key: str, N: Any, rule_: str | None = None, **kw) -> None:
        C = holder.get(key, _MISSING)
        C = _MISSING if C is None else C
        B = wslot.get(key, _MISSING)
        newC, newB, kind, pend, dele = _merge_path(rule_ or rule, C, B, N, **kw)
        _set_key(holder, key, newC)
        _set_key(wslot, key, newB)
        pending.pop(path, None)
        if path in deleted:
            deleted.remove(path)
        if pend:
            pending[path] = copy.deepcopy(N)
        if dele:
            deleted.append(path)
        if kind:
            rows.append(DiffRow(path, kind, None if C is _MISSING else copy.deepcopy(C),
                                None if N is _MISSING else copy.deepcopy(N)))

    # 크롭 단위: style_note(프로젝트 전파) → preserve
    if project_style != "":
        apply("crop.style_note", spec, written["crop"], "style_note",
              project_style if isinstance(project_style, str) else _MISSING, style=True)
    if draft is not None:
        apply("crop.preserve", spec, written["crop"], "preserve", draft.crop.get("preserve", _MISSING))

    # 타깃: id 로 맞춘다. 모르는 id 는 보존(Prepare 가 무시), 중복 id 는 병합하지 않음
    tids = [t.tid for t in ctx.targets]
    st_list = spec.get("targets")
    if st_list is not None and not isinstance(st_list, list):
        notes.append("targets 가 목록이 아님 — 타깃 병합 건너뜀")
    else:
        st_list = st_list if st_list is not None else []
        ids = [str(t.get("id")) for t in st_list if isinstance(t, dict)]
        dups = sorted({i for i in ids if ids.count(i) > 1})
        for i in dict.fromkeys(ids):
            if i not in tids:
                rows.append(DiffRow(f"{i}", "unknown_target", None, None))
        for i in dups:
            rows.append(DiffRow(f"{i}", "dup_target", None, None))
        # 사람 refs(force 는 매니페스트만 — 스펙의 사람 refs 는 force 가 새 값으로 바꾼다): 노드가 쓴 mode 는 양보한다
        con = constraints_for(ctx, None if rule == "force" else cur)
        for t in ctx.targets:
            if t.tid in dups:
                continue
            has = draft is not None and t.tid in draft.targets
            ylds = {f for f, own_lost in (("name", bool(t.name_en or t.gloss)),
                                          ("mode", bool(t.human_mode or (con.get(t.tid) or {}).get("refs"))),
                                          ("refs", bool(t.human_refs))) if own_lost}
            st = next((x for x in st_list if isinstance(x, dict) and str(x.get("id")) == t.tid), None)
            if st is None:
                if not has:
                    continue
                st = {"id": t.tid}
                st_list.append(st)
            # 드라이런(draft None)이 빈 슬롯을 만들면 "타깃 추가"(written 에 tid 없음) 판정이 사라진다. 초안이 있으면 출력에
            # 없던 타깃도 아래에서 {} (의도 — 그 키로는 다시 초안하지 않음)
            wslot = written.setdefault(t.tid, {}) if draft is not None else written.get(t.tid, {})
            t_rule = "refresh" if t.tid in moved else None
            # N15 로 사람 refs 옆에서 뺀 필드는 같은 키의 초안이 전에 쓴 값도 남기지 않는다(초안 뒤 아트 리드가 refs 를 고친 경우) —
            # fill 이어도 그 타깃만 refresh 규칙(사람 값 유지). 다른 키의 값(타깃 추가 등)은 그 키에서 받은 것이라 건드리지 않는다
            if (rule == "fill" and has and t_rule is None and (tk_old.get(t.tid) or (d_old or {}).get("key")) == prov.get("key")
                    and any(str(f).startswith("refs_differ_from_human") for f in draft.review.get(t.tid, {}).get("flags") or [])):
                t_rule = "refresh"
                notes.append(f"N15({t.tid}) 사람 refs 와 다른 이미지로 쓴 값 → 그 타깃만 refresh 규칙으로 병합")
            # 이 초안의 값을 받는 타깃(새 타깃·refresh·force·타깃 변경)은 _review 도 이 초안 것. fill 에서 값을 받지 않는 기존
            # 타깃(C==B 유지)은 그 값이 나온 키가 이 초안 키일 때만 — 아니면 _review 를 그대로 두고 그 키를 기록한다(고정점)
            t_key = tk_old.get(t.tid) or (d_old or {}).get("key")
            if has and (rule != "fill" or t_rule is not None or not w_old.get(t.tid)):
                t_key = prov.get("key")
            has_review = has and t_key == prov.get("key")
            if t_key and t_key != prov.get("key"):
                target_keys[t.tid] = t_key
            else:
                target_keys.pop(t.tid, None)
            for f in OWNED_TARGET:
                if f in ylds:
                    apply(f"{t.tid}.{f}", st, wslot, f, _MISSING, yielded=True)
                elif has:
                    apply(f"{t.tid}.{f}", st, wslot, f, draft.targets[t.tid].get(f, _MISSING), rule_=t_rule)
            if has_review:
                st["_review"] = copy.deepcopy(draft.review.get(t.tid) or {})
                for f, v in (draft.review.get(t.tid) or {}).get("unrendered", {}).items():
                    rows.append(DiffRow(f"{t.tid}.{f}", "unrendered", None, copy.deepcopy(v)))
            _to_end(st, "_review")
        if st_list and "targets" not in spec:
            spec["targets"] = st_list

    # _draft
    if draft is not None:
        for tid in tids:  # 출력에 없던 타깃도 {} 로 — "타깃 추가" 판정이 이 키로 한다
            written.setdefault(tid, {})
        spec.setdefault("_hint", "")
        spec.setdefault("_accepted", False)
        tr = dict(prov.get("transport") or {})
        tr.update(draft.transport)
        # labels: 타깃별 초안 때 라벨(지운 tid 기록도 남김 — 나중에 그 tid 를 다른 물체로 다시 쓰면 "타깃 변경")
        old_labels = d_old.get("labels") if d_old and isinstance(d_old.get("labels"), dict) else {}
        labels = {**old_labels, **{t.tid: {"label": t.label, "name_en": t.name_en} for t in ctx.targets}}
        forced = prov.get("key") if rule == "force" else (d_old or {}).get("forced_key")
        spec["_draft"] = {
            "version": DRAFT_VERSION, "normalizer": NORM_VERSION, "pass_plan": PASS_PLAN,
            **{k: prov.get(k) for k in ("key", "attempt", "backend", "model", "effort", "image_detail", "prompt",
                                        "drafted", "credits")},
            "hint": ctx.hint, "round": prov.get("round"), "refs_shown": list(prov.get("refs_shown") or []),
            "transport": tr, "labels": labels, **({"forced_key": forced} if forced else {}),
            **({"target_keys": target_keys} if target_keys else {}),
            "written": written, "pending": pending, "deleted": deleted,
            "observations": copy.deepcopy(draft.observations), "suggestions": copy.deepcopy(draft.suggestions),
            "flags": list(draft.flags)}
    elif d_old is not None:
        d_new = copy.deepcopy(d_old)
        d_new["written"] = written
        for k, v in (("pending", pending), ("deleted", deleted)):  # 없던 빈 키는 만들지 않는다(화풍 전파만 = 고정점)
            if v or k in d_old:
                d_new[k] = v
        spec["_draft"] = d_new
    elif any(written.values()):
        spec["_draft"] = {"version": DRAFT_VERSION, "written": written}  # 화풍 전파만 — key 없음 = 초안 없음
    _to_end(spec, "_draft")

    changed = not _eq(spec, cur if cur is not None else {})
    return MergeResult(spec=spec if changed and mode != "preview" else None, changed=changed, rows=rows,
                       backup=rule == "force" and changed, render_changed=_rendered_view(spec) != _rendered_view(cur),
                       notes=notes, merged=spec if (spec or cur is not None) else None)


def merge_project(current: dict | None, style: str | None, *, mode: str, provenance: dict) -> MergeResult:
    """specs/_project.json 병합({"style_note", "_draft"}). style None(화풍 초안 없음·실패)이면 바꾸지 않는다.
    mode 는 merge_spec 과 같다(force 는 only 에 "style" 이 있을 때만 넘길 것 — 키마다 1회, merge_mode). fill 에서 키만
    바뀐 초안은 적용하지 않는다(mock 화풍을 실제 백엔드 초안으로 바꿀 때는 refresh 규칙).
    style_note == "" 는 사람 값(화풍 초안·전파 끔)이다."""
    if mode not in WRITE_MODES:
        raise ValueError(f"{_TAG} write_mode 값이 잘못되었습니다: {mode!r}")
    cur = copy.deepcopy(current) if isinstance(current, dict) else None
    spec = copy.deepcopy(cur) if cur is not None else {}
    if style is None:
        return MergeResult(None, False, [], False, False, ["화풍 초안 없음"], merged=cur)
    prov = dict(provenance or {})
    mode = merge_mode(spec, mode, prov.get("key"))
    d_old = spec.get("_draft") if isinstance(spec.get("_draft"), dict) else {}
    rule = "refresh" if mode == "preview" else mode
    notes = []
    if mode == "fill" and d_old.get("key") and d_old.get("key") != prov.get("key"):
        if d_old.get("backend") != BACKEND_NAMES["mock"] or prov.get("backend") == BACKEND_NAMES["mock"]:
            return MergeResult(None, False, [], False, False, ["화풍 갱신 가능 — fill 에서는 적용하지 않음(refresh)"],
                               merged=cur)
        rule = "refresh"
        notes.append("mock 초안 교체 → refresh 규칙으로 병합")
    w_old = d_old.get("written") if isinstance(d_old.get("written"), dict) else {}
    C = spec.get("style_note", _MISSING)
    C = _MISSING if C is None else C
    newC, newB, kind, pend, dele = _merge_path(rule, C, w_old.get("style_note", _MISSING), style)
    _set_key(spec, "style_note", newC)
    rows = [DiffRow("style_note", kind, None if C is _MISSING else C, style)] if kind else []
    d = {"version": DRAFT_VERSION, **{k: prov.get(k) for k in ("key", "attempt", "backend", "model", "effort",
                                                                  "drafted", "credits")},
         "written": {} if newB is _MISSING else {"style_note": newB},
         "observations": copy.deepcopy(prov.get("observations") or []), "flags": list(prov.get("flags") or [])}
    forced = prov.get("key") if rule == "force" else d_old.get("forced_key")
    if forced:
        d["forced_key"] = forced
    if pend:
        d["pending"] = {"style_note": style}
    if dele:
        d["deleted"] = ["style_note"]
    spec["_draft"] = d
    _to_end(spec, "_draft")
    changed = not _eq(spec, cur if cur is not None else {})
    return MergeResult(spec=spec if changed and mode != "preview" else None, changed=changed, rows=rows,
                       backup=rule == "force" and changed, render_changed=False, notes=notes, merged=spec)


@dataclass
class RenderCheck:
    ok: bool
    problems: list[str]
    flags: list[str]
    dropped: list[str]
    v1: str | None


_DROP_ORDER = ("occluded_by", "location", "ref_ignore", "ref_object", "shape", "text", "ref_view")
_AND_SHOWS_RE = re.compile(r"@image\d+ and @image\d+ shows")
_DANGLING_RE = re.compile(r"\b(the|an?|in|on|of|at|to)\s+\1\b|\bthe\s+(?:nothing|none|no)\b|\bthe[,.;:]", re.I)


def _try_render(crop: dict, spec: dict, refs_dir: str, glossary: dict):
    try:
        s = dprompt.spec_from_manifest(crop, None, refs_dir, glossary, spec)
        probs = dprompt.validate_spec(s)
        return dprompt.variants(s), list(s.get("warnings") or []), probs
    except Exception as e:  # 렌더러는 형식마다 KeyError·TypeError·AttributeError·ValueError 를 낸다
        return e


def render_check(crop: dict, spec: dict, *, refs_dir: str, glossary: dict, owned: set[str]) -> RenderCheck:
    """spec_from_manifest → validate_spec → variants() → V1–V4 문장 린트(§4.4: None, ' ,', '@imageN and @imageM shows',
    CJK, 골격 자리표시 '<...>', 'the the'·'on on'·'the nothing'·'the,' 같은 빈 자리, 'on the tucked behind'·'on the where'·
    'on the of' 같은 location 접두 오류). 예외가 나면 노드 소유 필드를
    occluded_by, location, ref_ignore, ref_object, shape, text, ref_view 순서로 빼며 다시 검사.
    owned = 노드가 쓴 그대로인 경로("A.location" …). 다 빼도 예외면 사람 값 때문 — ok=False(쓰지 말 것).
    ok=True 이고 dropped 가 있으면 호출자가 그 경로를 스펙에서 빼고 써야 한다. problems = validate_spec 결과(막지 않음)."""
    work = copy.deepcopy(spec) if isinstance(spec, dict) else {}
    dropped: list[str] = []
    res = _try_render(crop, work, refs_dir, glossary)
    first_err = res if isinstance(res, Exception) else None
    for f in _DROP_ORDER if first_err is not None else ():
        for t in work.get("targets") or []:
            if isinstance(t, dict) and f"{t.get('id')}.{f}" in owned and f in t:
                t.pop(f)
                dropped.append(f"{t.get('id')}.{f}")
        res = _try_render(crop, work, refs_dir, glossary)
        if not isinstance(res, Exception):
            break
    if isinstance(res, Exception):
        e = first_err or res
        return RenderCheck(False, [f"사람 값 때문에 Prepare 가 이 크롭에서 멈춥니다 — {type(e).__name__}: {_short(e, 200)}"],
                           [], [], None)
    vs, warns, probs = res
    flags = [f"render_fallback: {', '.join(dropped)} 뺌({type(first_err).__name__})"] if dropped else []
    prompts = [v["prompt"] for v in vs.values()]
    for test, code in ((lambda p: re.search(r"\bNone\b", p), "lint: None"), (lambda p: " ," in p, "lint: ' ,'"),
                       (lambda p: _AND_SHOWS_RE.search(p), "lint: '@imageN and @imageM shows'"),
                       (lambda p: _CJK_RE.search(p), "lint: CJK"), (lambda p: _PLACEHOLDER_RE.search(p), "lint: '<...>'"),
                       (lambda p: _DANGLING_RE.search(p), "lint: 'the the' / 'the nothing'"),
                       (lambda p: _LOC_LINT_RE.search(p), "lint: 'on the tucked/where/of …'")):
        if any(test(p) for p in prompts):
            flags.append(code)
    if any("모르는 키" in w for w in warns):
        flags.append("warn: 모르는 키")
    if any("기본 설명" in w for w in warns):
        flags.append("warn: 기본 설명")
    v1 = vs["V1_standard"]["prompt"]
    detailed = any(t.get("detail") and t.get("mode") != "self_restore" for t in vs["V1_standard"]["targets"])
    if detailed and "V4_checklist" in vs and vs["V4_checklist"]["prompt"] == v1:
        flags.append("v4_equals_v1")  # Prepare 가 V4 를 중복으로 건너뛴다(best-of-N 다양성 줄어듦)
    return RenderCheck(True, probs, flags, dropped, v1)


def write_spec(root: str, crop_id: str, merge: Callable[[dict | None], MergeResult]) -> tuple[MergeResult, str | None]:
    """store.manifest_lock(root) 안에서: 파일 다시 읽기 → merge(current) → 정규 dict 비교 → (force·내용 다름) 백업
    → store.write_json_atomic. 반환 (결과, 백업 상대 경로). 파일이 깨져 있으면 ValueError(쓰지 않음), 잠겨 읽거나 쓸 수 없으면
    OSError(호출자가 '못 씀' 으로 남겨 다음 큐에 다시 쓴다 — 실패한 쓰기의 백업은 지운다)."""
    path = _spec_path(root, crop_id)
    with store.manifest_lock(root):
        try:  # 한 번만 읽는다 — 읽기까지 막은 잠금은 깨진 파일(ValueError)이 아니라 OSError 로 올린다
            with open(path, "rb") as f:
                current, err = _parse_spec(f.read())
        except FileNotFoundError:
            current, err = None, None
        if err:
            raise ValueError(f"{_TAG} specs/{crop_id}.json — {err}")
        res = merge(current)
        if res.spec is None:
            return res, None
        if _eq(res.spec, current if current is not None else {}):
            res.spec, res.changed = None, False
            return res, None
        backup = None
        if res.backup and current is not None:
            stamp = time.strftime("%Y%m%d-%H%M%S")
            rel = f"specs/{BACKUP_SUBDIR}/{crop_id}.{stamp}.json"
            n = 2
            while os.path.exists(os.path.join(root, *rel.split("/"))):
                rel = f"specs/{BACKUP_SUBDIR}/{crop_id}.{stamp}-{n}.json"
                n += 1
            dst = os.path.join(root, *rel.split("/"))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(path, dst)
            backup = rel
        try:
            store.write_json_atomic(path, res.spec)
        except OSError:
            if backup:  # 잠긴 동안 큐마다 다시 시도하므로 쓰지 못한 백업이 쌓이지 않게
                os.remove(os.path.join(root, *backup.split("/")))
            raise
    return res, backup


# ══════════════════════════════════════════════════════════════════════
# 백엔드 (§2.9)
# ══════════════════════════════════════════════════════════════════════
@dataclass
class CallResult:
    text: str
    usage: dict | None
    credits: float | None
    response_id: str | None
    transport: dict
    seconds: float


_ACTIVE: set[tuple[str, str]] = set()   # (runner._rid(root), key) — 프로세스 안 중복 실행 방지
_ACTIVE_LOCK = threading.Lock()


def _claim(root: str, key: str) -> bool:
    k = (runner._rid(root), key)
    with _ACTIVE_LOCK:
        if k in _ACTIVE:
            return False
        _ACTIVE.add(k)
        return True


def _release(root: str, key: str) -> None:
    with _ACTIVE_LOCK:
        _ACTIVE.discard((runner._rid(root), key))


def _settings(backend_name: str, unit: Unit, req: Request, model, effort, image_detail) -> dict:
    return {"version": DRAFT_VERSION, "pass_plan": PASS_PLAN, "kind": unit.kind, "backend": backend_name,
            "model": model, "effort": effort, "image_detail": image_detail, "draft_round": req.draft_round,
            "prompt": prompt_fingerprint()}


def _record_req(ledger: Ledger, unit: Unit, *, settings: dict, body: dict, transport: dict) -> None:
    ledger.write(unit.crop_id, unit.key, unit.attempt, "req",
                 {"kind": unit.kind, "state": unit.state, "reasons": list(unit.reasons), "est": list(unit.est),
                  "settings": settings, "body": body, "transport": dict(transport)})


def _record_inflight(ledger: Ledger, unit: Unit, transport: dict) -> None:
    ledger.write(unit.crop_id, unit.key, unit.attempt, "inflight",
                 {"pid": os.getpid(), "session": runner._SESSION, "transport": dict(transport)})


def _record_failed(ledger: Ledger, unit: Unit, err: str, *, status: int | None, phase: str, maybe_billed: bool,
                   retryable: bool, credits: float | None = None) -> runner.CallError:
    ledger.write(unit.crop_id, unit.key, unit.attempt, "failed",
                 {"error": err, "status": status, "phase": phase, "maybe_billed": maybe_billed,
                  "retryable": retryable, "credits": credits})
    return runner.CallError(err, status=status, phase=phase, retryable=retryable, maybe_billed=maybe_billed)


def _record_rejected(ledger: Ledger, unit: Unit, err: str, *, status: int | None, phase: str = "create") -> runner.CallError:
    ledger.write(unit.crop_id, unit.key, unit.attempt, "rejected", {"error": err, "status": status, "phase": phase})
    return runner.CallError(err, status=status, phase=phase, retryable=False, maybe_billed=False)


def _complete(ledger: Ledger, unit: Unit, text: str, *, credits: float | None, elapsed_s: float,
              transport: dict, billed: bool = True) -> dict:
    """원문 → extract_json → done 기록(반환). 추출 실패는 failed(parse, maybe_billed=billed — local_qwen 은 False) 를 쓰고
    CallError. transport.schema_enforced = 출력에 schema_echo 가 있음(N12 — strict 가 실제로 강제됨)."""
    try:
        parsed, repairs = extract_json(text)
    except ParseError as e:
        raise _record_failed(ledger, unit, f"JSON 추출 실패{'(과금됐을 수 있음)' if billed else ''}: {_short(e, 200)}",
                             status=None, phase="parse", maybe_billed=billed, retryable=True, credits=credits) from e
    tr = dict(transport)
    tr["schema_enforced"] = parsed.get("schema_echo") == SCHEMA_ECHO
    rec = {"text": text, "parsed": parsed, "repairs": repairs, "credits": credits,
           "elapsed_s": round(float(elapsed_s), 2), "transport": tr}
    ledger.write(unit.crop_id, unit.key, unit.attempt, "done", rec)
    return rec


class OpenAIVLM:
    """comfy.org 프록시 Responses API(유료). 비공개 API 결합은 _load_api 한 곳에 둔다(§9 위험표).
    hidden = Run 노드와 같은 hidden 인자(auth_token / api_key / usage_source / unique_id)."""

    name = BACKEND_NAMES["openai"]

    def __init__(self, hidden: dict, *, root: str):
        h = dict(hidden or {})
        self.holder = runner._CallHidden(
            unique_id=runner._pick(h, "unique_id"),
            auth_token_comfy_org=runner._pick(h, "auth_token_comfy_org", "auth_token"),
            api_key_comfy_org=runner._pick(h, "api_key_comfy_org", "api_key"),
            comfy_usage_source=runner._pick(h, "comfy_usage_source", "usage_source"),
            dynprompt=h.get("dynprompt"),
        )
        self.root = root
        self.api: SimpleNamespace | None = None
        self.interrupt_check: Callable[[], bool] = runner._comfy_interrupt_flag

    def __repr__(self) -> str:
        return f"OpenAIVLM({self.holder!r})"

    def has_auth(self) -> bool:
        return bool(self.holder.auth_token_comfy_org or self.holder.api_key_comfy_org)

    def _load_api(self) -> SimpleNamespace:
        """sync_op_raw, ApiEndpoint, credits_used(선택), friendly_http_message, network_errors. torch 불필요."""
        if self.api is None:
            try:
                from comfy_api_nodes.util import ApiEndpoint, sync_op_raw
                from comfy_api_nodes.util import client
                from comfy_api_nodes.util.common_exceptions import ApiServerError, LocalNetworkError
            except Exception as e:  # 업데이트로 경로·이름이 바뀐 경우 등
                raise RuntimeError(
                    f"{_TAG} comfy.org API 모듈(comfy_api_nodes)을 불러오지 못했습니다. ComfyUI 업데이트로 내부 API 가 "
                    f"바뀌었을 수 있습니다 — bmk_design_patch_vlm.OpenAIVLM 을 확인하세요: {e}") from e
            credits_used = getattr(client, "_get_remembered_credits_used", None)
            if credits_used is None:  # 선택: 없으면 크레딧 None(실행은 막지 않는다)
                logger.warning(f"{_TAG} comfy_api_nodes 에 크레딧 조회 함수가 없어 VLM 크레딧을 기록하지 않습니다")
                credits_used = lambda _cls: None  # noqa: E731
            self.api = SimpleNamespace(sync_op_raw=sync_op_raw, ApiEndpoint=ApiEndpoint, credits_used=credits_used,
                                       friendly_http_message=getattr(client, "_friendly_http_message", None),
                                       network_errors=(LocalNetworkError, ApiServerError))
        return self.api

    def call_class(self) -> type:
        """호출마다 새 클래스 — 크레딧 기억(WeakKeyDictionary, 클래스 키)이 섞이지 않게(create·poll 도 따로)."""
        return type(f"BMKDPVLM_{next(_CALL_SEQ)}", (), {"hidden": self.holder})

    async def _send(self, cls: type, path: str, method: str, **kw) -> tuple[Any, list]:
        """sync_op_raw 1회. is_rate_limited 로 상태·본문만 관찰(동작은 그대로). 예외는 (예외, seen) 으로 넘긴다."""
        api = self._load_api()
        seen: list[tuple[int, Any]] = []

        def observe(status: int, body: Any) -> bool:
            seen.append((int(status), body))
            return False

        try:
            return await api.sync_op_raw(cls, api.ApiEndpoint(path=path, method=method), monitor_progress=True,
                                         is_rate_limited=observe, **kw), seen
        except runner._interrupt_types():
            raise
        except Exception as e:  # 오류 분류는 호출자(create / poll 단계가 다름)
            return e, seen

    async def _wait(self, seconds: float, progress, t0: float) -> None:
        """폴링 간격: 0.5초마다 comfy 인터럽트 플래그를 본다(서 있으면 인터럽트 예외 — created 는 남는다)."""
        if progress is not None:
            el = int(time.monotonic() - t0)
            progress(f"폴링 {el // 60}:{el % 60:02d}")
        end = time.monotonic() + seconds
        while True:
            if self.interrupt_check():
                raise runner._make_interrupt()
            left = end - time.monotonic()
            if left <= 0:
                return
            await asyncio.sleep(min(0.5, left))

    async def call(self, unit: Unit, req: Request, ledger: Ledger, *, model, effort, image_detail,
                   progress=None) -> CallResult:
        """inflight → POST(create) → created → poll → resp → done. 400 사다리 L2→L1→L0·background 제거는 같은 시도 안에서.
        create: timeout ≥3600, max_retries=0(같은 Idempotency-Key 재전송 금지), monitor_progress(인터럽트 감시).
        400(사다리 밖)·4xx = rejected(시도 유지), 401/402/403 = rejected(run_units 가 발사 중지), 5xx·네트워크·타임아웃 =
        failed(maybe_billed). create 가 받아들여지면 created 를 먼저 쓰고 caps 는 그 뒤(실패해도 경고만).
        폴링 중 오류·마감은 created 를 그대로 두고 phase="poll" CallError(GET 4xx 는 _poll 이 failed 로 끝냄)."""
        api = self._load_api()
        root = ledger.root
        t0 = time.monotonic()
        if not self.has_auth():
            raise _record_rejected(ledger, unit, "comfy.org 로그인 필요(인증 정보 없음 — 요청 안 보냄)", status=401)
        caps = _caps_transport(root, "openai", model)
        if image_detail == "original" and caps.get("detail_original") is False:
            raise _record_rejected(ledger, unit, f"{model} 이 image_detail=original 을 거부한 기록(_vlm_caps.json) — "
                                                 f"image_detail=high 로 바꾸세요(요청 안 보냄)", status=400)
        level = int(caps.get("schema_level", 2))
        background = bool(caps.get("background", True))
        try:
            body = await asyncio.to_thread(openai_body, req, model=model, effort=effort, image_detail=image_detail,
                                           level=2, background=True)
            lbody = await asyncio.to_thread(ledger_body, body, req)
            await asyncio.to_thread(_save_sent, root, body)
        except (OSError, ValueError) as e:  # 전송 전 로컬 실패 — 과금 없음
            raise _record_rejected(ledger, unit, f"이미지 준비 실패(요청 안 보냄): {_short(e, 200)}", status=None,
                                   phase="encode") from e
        lbody.pop("text", None)
        lbody.pop("background", None)
        _record_req(ledger, unit, settings=_settings(self.name, unit, req, model, effort, image_detail), body=lbody,
                    transport={"schema_level": level, "background": background})
        while True:
            transport = {"schema_level": level, "background": background}
            _record_inflight(ledger, unit, transport)
            cls = self.call_class()
            resp, seen = await self._send(cls, RESPONSES_PATH, "POST", data=_with_transport(body, req, level, background),
                                          timeout=max(3600, POLL_DEADLINE_S[effort]), max_retries=CREATE_MAX_RETRIES,
                                          wait_label="VLM 초안")
            if not isinstance(resp, Exception):
                break
            err = runner._request_error(resp, seen, api.network_errors, api.friendly_http_message)
            text = " ".join([str(resp)] + [_canon(b) for _s, b in seen[-1:]])
            st = err.status
            if st == 400:  # 사다리·background 하강은 create 가 받아들여진 뒤에 caps 에 기록(사다리 밖 400 이 caps 를 깎지 않게)
                if background and re.search(r"background", text, re.I):
                    background = False
                    continue
                if image_detail == "original" and re.search(r"\bdetail\b", text, re.I):
                    _caps_set(root, "openai", model, detail_original=False)
                    raise _record_rejected(ledger, unit, f"image_detail=original 거부(HTTP 400, 과금 전) — image_detail=high "
                                                         f"로 바꾸세요: {_short(err, 160)}", status=400)
                if level > 0 and _LADDER_RE.search(text):
                    level -= 1
                    continue
                raise _record_rejected(ledger, unit, f"HTTP 400(요청 거부, 과금 전): {_short(err, 200)}", status=400)
            if st is not None and 400 <= st < 500 and st != 408:
                why = "comfy.org 로그인 만료/권한·크레딧 문제" if st in runner.AUTH_STATUSES else "요청 거부"
                raise _record_rejected(ledger, unit, f"HTTP {st}({why}, 과금 전): {_short(err, 200)}", status=st)
            raise _record_failed(ledger, unit, f"create 실패(과금됐을 수 있음): {_short(err, 200)}", status=st,
                                 phase="create", maybe_billed=True, retryable=True)
        credits_create = api.credits_used(cls)
        if not isinstance(resp, dict):
            raise _record_failed(ledger, unit, f"create 응답이 JSON 객체가 아님: {_short(resp, 120)}", status=None,
                                 phase="create", maybe_billed=True, retryable=True, credits=credits_create)
        rid = resp.get("id")
        if not rid:
            raise _record_failed(ledger, unit, f"create 응답에 id 없음(status {resp.get('status')!r})", status=None,
                                 phase="create", maybe_billed=True, retryable=True, credits=credits_create)
        created = {"response_id": rid, "status": resp.get("status"), "credits_create": credits_create,
                   "transport": transport}
        ledger.write(unit.crop_id, unit.key, unit.attempt, "created", created)  # 과금이 시작됨 — 응답 id 부터 남긴다(Q7)
        caps_new = {}  # 받아들여진 전송 단계(사다리 밖 400 이 caps 를 깎지 않게 여기서만)
        if level != int(caps.get("schema_level", 2)):
            caps_new["schema_level"] = level
        if background != bool(caps.get("background", True)) or (
                background and str(resp.get("status")) in ("queued", "in_progress")):
            caps_new["background"] = background
        if image_detail == "original":
            caps_new["detail_original"] = True
        if caps_new:
            _caps_hint(_caps_set, root, "openai", model, **caps_new)
        return await self._poll(unit, created, ledger, effort=effort, model=model, progress=progress, t0=t0, first=resp)

    async def repoll(self, unit: Unit, created: dict, ledger: Ledger, *, effort, progress=None) -> CallResult:
        """기록된 response_id 로 GET 폴링만(무료 복구, 승인 불필요). 마감 = created 부터 센 effort 마감의 남은 시간,
        그 마감이 지났으면 REPOLL_SHORT_S(큐마다 오래 막지 않게). 진척 없는 재폴링 연속 횟수는 _poll 이 센다."""
        rq = ledger.read(unit.crop_id, unit.key, unit.attempt, "req") or {}
        model = (rq.get("settings") or {}).get("model") or ""
        effort = effort or (rq.get("settings") or {}).get("effort") or "high"
        full = POLL_DEADLINE_S.get(effort, POLL_DEADLINE_S["high"])
        try:
            age = time.time() - time.mktime(time.strptime(str(created.get("at")), "%Y-%m-%d %H:%M:%S"))
        except (ValueError, OverflowError):  # at 없음·손상 — 지금부터 전체 마감
            age = 0.0
        return await self._poll(unit, created, ledger, effort=effort, model=model, progress=progress,
                                t0=time.monotonic(), first=None, limit_s=max(REPOLL_SHORT_S, full - age))

    async def _poll(self, unit: Unit, created: dict, ledger: Ledger, *, effort, model, progress, t0: float,
                    first: dict | None, limit_s: float | None = None) -> CallResult:
        """first = create 응답(call) | None(repoll). 마감 초과면 created 에 마지막 상태(poll_status)와 진척 없는 재폴링
        연속 횟수(poll_stalls — repoll 세션이 같은 비종료 상태로 마감까지 감)를 남기고 created 유지. 그 횟수가
        REPOLL_MAX_STALLS 가 되면 failed(poll, maybe_billed) — 다음 계획이 승인 필요한 재시도를 낸다. GET 오류(401 포함)로
        끝난 세션은 세지 않는다."""
        api = self._load_api()
        root = ledger.root
        rid = str((created or {}).get("response_id") or "")
        if not rid:
            raise runner.CallError("created 기록에 response_id 없음", status=None, phase="poll", retryable=False,
                                   maybe_billed=False)
        transport = dict(created.get("transport") or {})
        limit_s = POLL_DEADLINE_S.get(effort, POLL_DEADLINE_S["high"]) if limit_s is None else limit_s
        deadline = time.monotonic() + limit_s
        cls = self.call_class()
        r = first
        while not isinstance(r, dict) or str(r.get("status") or "") not in _TERMINAL:
            if r is not None:
                if time.monotonic() >= deadline:
                    now = str(r.get("status") or "")
                    rec = ledger.read(unit.crop_id, unit.key, unit.attempt, "created") or {}
                    rec = rec if rec.get("response_id") == rid else dict(created)  # 손상 레코드로 response_id 를 잃지 않게
                    stalls = int(rec.get("poll_stalls") or 0) + 1 if first is None and now == rec.get(
                        "poll_status", rec.get("status")) else 0
                    if stalls >= REPOLL_MAX_STALLS:
                        raise _record_failed(ledger, unit, f"재폴링 {stalls}회 연속 진척 없음(상태 {now or '?'}) — 응답을 "
                                                           f"포기(과금됐을 수 있음, 다음 승인 목록에 '재시도')", status=None,
                                             phase="poll", maybe_billed=True, retryable=True,
                                             credits=created.get("credits_create"))
                    ledger.write(unit.crop_id, unit.key, unit.attempt, "created",
                                 {**{k: v for k, v in rec.items() if k != "_seq"}, "poll_status": now, "poll_stalls": stalls})
                    raise runner.CallError(f"폴링 마감 초과({int(limit_s)}초, 상태 {now or '?'}) — created 유지, 다시 큐에 "
                                           f"넣으면 무료 재폴링" + (f"(진척 없음 {stalls}/{REPOLL_MAX_STALLS})" if stalls else ""),
                                           status=None, phase="poll", retryable=True, maybe_billed=False)
                await self._wait(POLL_INTERVAL_S, progress, t0)
            res, seen = await self._send(cls, f"{RESPONSES_PATH}/{rid}", "GET", timeout=POLL_TIMEOUT_S, max_retries=3,
                                         wait_label="VLM 초안 폴링")
            if isinstance(res, Exception):
                err = runner._request_error(res, seen, api.network_errors, api.friendly_http_message)
                st = err.status
                if st is not None and 400 <= st < 500 and st not in runner.AUTH_STATUSES and st not in (408, 429):
                    # 404·410·400 = 이 응답 id 를 더는 읽을 수 없음(만료·다른 키·GET 미지원) — created 로 두면 매 큐마다
                    # 다시 GET 해서 영원히 막힌다. 과금됐을 수 있는 실패로 끝내고 다음 계획이 승인 필요한 재시도를 낸다
                    raise _record_failed(ledger, unit, f"폴링 GET HTTP {st} — 응답을 더 읽을 수 없음(과금됐을 수 있음, "
                                                       f"다음 승인 목록에 '재시도'): {_short(err, 200)}", status=st,
                                         phase="poll", maybe_billed=True, retryable=True,
                                         credits=created.get("credits_create")) from res
                raise runner.CallError(f"폴링 실패 — created 유지(다시 큐에 넣으면 무료 재폴링): {_short(err, 200)}",
                                       status=st, phase="poll", retryable=True, maybe_billed=False) from res
            r = res if isinstance(res, dict) else {"status": ""}
        credits_poll = api.credits_used(cls)
        vals = [v for v in (created.get("credits_create"), credits_poll) if isinstance(v, (int, float))]
        credits = max(vals) if vals else None
        status = str(r.get("status"))
        text, refusal = _response_text(r)
        usage = r.get("usage") if isinstance(r.get("usage"), dict) else None
        ledger.write(unit.crop_id, unit.key, unit.attempt, "resp",
                     {"status": status, "output_text": text, "usage": usage, "credits_poll": credits_poll,
                      "incomplete_details": r.get("incomplete_details"), "error": r.get("error"), "refusal": refusal})
        if usage and isinstance(usage.get("output_tokens"), int) and model:
            _caps_hint(_calib_record, root, model, effort, unit.kind, usage["output_tokens"])
        if status == "incomplete":
            why = (r.get("incomplete_details") or {}).get("reason") if isinstance(r.get("incomplete_details"), dict) else None
            raise _record_failed(ledger, unit, f"OpenAI incomplete({why or '출력 한도 소진'}) — effort 를 낮추거나 다시 승인",
                                 status=None, phase="result", maybe_billed=True, retryable=True, credits=credits)
        if status in ("failed", "cancelled"):
            raise _record_failed(ledger, unit, f"OpenAI {status}: {_short(r.get('error'), 200)}", status=None,
                                 phase="result", maybe_billed=True, retryable=True, credits=credits)
        if refusal:
            raise _record_failed(ledger, unit, f"모델이 거절함(refusal): {_short(refusal, 200)}", status=None,
                                 phase="result", maybe_billed=True, retryable=True, credits=credits)
        rec = _complete(ledger, unit, text, credits=credits, elapsed_s=time.monotonic() - t0, transport=transport)
        if int(transport.get("schema_level", 0)) >= 1 and not rec["transport"]["schema_enforced"] and model:
            _caps_hint(_caps_set, root, "openai", model, schema_level=0)  # 프록시가 text.format 을 조용히 버림(N12)
        return CallResult(text=text, usage=usage, credits=credits, response_id=rid, transport=rec["transport"],
                          seconds=rec["elapsed_s"])


_TERMINAL = ("completed", "incomplete", "failed", "cancelled")
_LADDER_RE = re.compile(r"text|format|schema|json_schema|minItems|maxItems", re.I)
_CALL_SEQ = itertools.count(1)


def _response_text(r: dict) -> tuple[str, str | None]:
    """Responses API 응답 → (output_text 이어 붙임, refusal 문구 | None)."""
    texts, refusal = [], None
    for item in r.get("output") or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for c in item.get("content") or []:
            if not isinstance(c, dict):
                continue
            if c.get("type") == "output_text":
                texts.append(str(c.get("text") or ""))
            elif c.get("type") == "refusal":
                refusal = str(c.get("refusal") or c.get("text") or "refusal")
    return "".join(texts), refusal


_QWEN: dict[str, Any] = {"key": None, "model": None, "processor": None}   # 모듈 캐시(ComfyUI 의 Unload 버튼은 못 봄)
_QWEN_LOCK = threading.RLock()
QWEN_RESIDENT_WARNING = ("Qwen 16GB 상주 — ComfyUI 의 Unload 버튼으로 내려가지 않음(keep_loaded 를 끄고 한 번 실행하면 내림)")


def unload_cached(torch_mod=None) -> None:
    """모듈 캐시(_QWEN)의 Qwen 을 내린다 — 백엔드 인스턴스·지금 vlm 선택과 무관(keep_loaded 로 남긴 모델을 openai·mock 실행의
    finally 에서도 내린다). 올라 있지 않으면 아무것도 하지 않는다. 해제한 VRAM 은 torch.cuda.empty_cache 로 바로 돌려준다
    (mm.soft_empty_cache 는 --cpu 에서 아무것도 하지 않는다). torch_mod = 시험용 가짜(없으면 이미 import 된 torch)."""
    with _QWEN_LOCK:
        model = _QWEN["model"]
        _QWEN.update(key=None, model=None, processor=None)
    if model is None:
        return
    del model
    gc.collect()
    torch_mod = torch_mod or sys.modules.get("torch")
    if torch_mod is not None:
        torch_mod.cuda.empty_cache()  # CUDA 를 쓴 적이 없으면 아무것도 하지 않는다
    logger.info(f"{_TAG} Qwen 모델을 내렸습니다")


class QwenVLM:
    """로컬 Qwen3-VL-8B-Instruct(bf16, sdpa, CUDA 만). 처음 쓸 때 한 번 로드, 순차 실행, finally 에서 언로드(keep_loaded 제외).
    로드·생성은 이벤트 루프 스레드에서 동기로 한다(ComfyUI 의 다른 노드가 그동안 GPU·model_management 를 쓰지 않게).
    torch·comfy·transformers 는 _load_libs 에서만 읽는다(시험은 libs 를 가짜 namespace 로 바꿔 끼운다)."""

    name = BACKEND_NAMES["local_qwen"]

    def __init__(self, model_dir: str, *, keep_loaded: bool):
        self.model_dir = model_dir
        self.keep_loaded = bool(keep_loaded)
        self.libs: SimpleNamespace | None = None

    def __repr__(self) -> str:
        return f"QwenVLM({self.model_dir!r}, keep_loaded={self.keep_loaded})"

    def has_auth(self) -> bool:
        return True

    def _load_libs(self) -> SimpleNamespace:
        if self.libs is None:
            try:
                import torch
                import comfy.model_management as mm
                from transformers import AutoProcessor, Qwen3VLForConditionalGeneration, StoppingCriteria, StoppingCriteriaList
            except Exception as e:
                raise RuntimeError(f"{_TAG} local_qwen 에 필요한 torch / transformers 를 불러오지 못했습니다: {e}") from e
            self.libs = SimpleNamespace(torch=torch, mm=mm, Model=Qwen3VLForConditionalGeneration,
                                        Processor=AutoProcessor, StoppingCriteria=StoppingCriteria,
                                        StoppingCriteriaList=StoppingCriteriaList)
        return self.libs

    @staticmethod
    def vram_check(need: int = QWEN_NEED_BYTES, mm=None) -> tuple[bool, float, float]:
        """(ok, 여유 GiB, 내릴 수 있는 ComfyUI 모델 GiB). ok = 여유 + 같은 장치의 current_loaded_models 합 ≥ need."""
        if mm is None:
            import comfy.model_management as mm
        dev = mm.get_torch_device()
        free = float(mm.get_free_memory(dev))
        loaded = 0.0
        for lm in list(getattr(mm, "current_loaded_models", None) or []):
            if getattr(lm, "device", None) != dev:
                continue
            try:
                loaded += float(lm.model_loaded_memory())
            except (AttributeError, TypeError, ReferenceError):  # 죽은 weakref 등
                continue
        return free + loaded >= need, free / 2**30, loaded / 2**30

    def _ensure(self) -> tuple:
        """모델·processor 를 올린다(이미 같은 키면 그대로). CUDA 장치 확인 → 사전 검사 → free_memory → 재검사 → bf16 sdpa
        로드(device_map = ComfyUI 장치 그대로). 모자라거나 CUDA 가 아니면 아무것도 올리지 않고 VramError
        (CPU 오프로드·nf4 자동 전환 없음 — --cpu 에서 여유 '메모리' 는 시스템 RAM 이라 검사가 무의미하다)."""
        with _QWEN_LOCK:
            L = self._load_libs()
            key = (os.path.normcase(os.path.abspath(self.model_dir)), QWEN_ATTN)
            if _QWEN["key"] == key and _QWEN["model"] is not None:
                return _QWEN["model"], _QWEN["processor"]
            if not os.path.isfile(os.path.join(self.model_dir, "config.json")):
                raise RuntimeError(f"{_TAG} Qwen 모델 폴더가 없습니다(config.json): {self.model_dir}")
            self.unload()
            mm = L.mm
            dev = mm.get_torch_device()
            if dev.type != "cuda":
                raise VramError(f"local_qwen 은 CUDA GPU 가 필요합니다 — 지금 ComfyUI 장치는 {dev}(--cpu 등). "
                                f"openai 나 mock 을 쓰세요")
            need = QWEN_NEED_BYTES / 2**30
            ok, free, loaded = self.vram_check(QWEN_NEED_BYTES, mm=mm)
            if not ok:
                raise VramError(f"VRAM 부족: 필요 {need:.1f}GiB, 여유 {free:.1f} GiB + 내릴 수 있는 ComfyUI 모델 {loaded:.1f} GiB")
            mm.free_memory(QWEN_NEED_BYTES, dev)
            mm.soft_empty_cache()
            free2 = float(mm.get_free_memory(dev)) / 2**30
            if free2 < need:
                raise VramError(f"VRAM 부족(ComfyUI 모델을 내린 뒤): 필요 {need:.1f}GiB, 여유 {free2:.1f} GiB — "
                                f"다른 프로세스(다른 ComfyUI 등)가 VRAM 을 쓰고 있을 수 있습니다")
            model = L.Model.from_pretrained(self.model_dir, dtype=L.torch.bfloat16, device_map=dev,
                                            attn_implementation=QWEN_ATTN, local_files_only=True).eval()
            g = model.generation_config  # 모델 기본값(do_sample=True, 0.7/20/0.8)이 GenerationConfig 를 덮는 함정(RL §3)
            g.do_sample = False
            g.temperature = g.top_p = g.top_k = None
            proc = L.Processor.from_pretrained(self.model_dir, local_files_only=True)
            _QWEN.update(key=key, model=model, processor=proc)
            return model, proc

    def _stopper(self, progress):
        L = self.libs

        class _Stop(L.StoppingCriteria):  # 인터럽트면 생성을 끝내고, 64 토큰마다 진행 표시
            def __init__(self):
                self.n = 0

            def __call__(self, input_ids, scores, **kw):
                self.n += 1
                if progress is not None and self.n % 64 == 0:
                    progress(f"생성 {self.n} 토큰")
                return L.torch.full((input_ids.shape[0],), bool(L.mm.processing_interrupted()), dtype=L.torch.bool,
                                    device=input_ids.device)

        return _Stop()

    def _generate(self, req: Request, images: list, progress, reask: tuple[str, str] | None = None) -> tuple[str, dict]:
        """system + user(텍스트 라벨·PIL 교차) → apply_chat_template + "{" 프리필 → greedy generate.
        usage.finish = "length"(max_new_tokens 를 다 쓰고 마지막 토큰이 EOS 아님 = 잘림) | "stop"."""
        with _QWEN_LOCK:
            model, proc = self._ensure()
            L = self.libs
            content, imgs, it = [], [], iter(images)
            for kind, val in req.parts:
                if kind == "image":
                    pil = next(it)
                    content.append({"type": "image", "image": pil})
                    imgs.append(pil)
                else:
                    content.append({"type": "text", "text": val})
            msgs = [{"role": "system", "content": [{"type": "text", "text": req.system}]},
                    {"role": "user", "content": content}]
            if reask:
                msgs += [{"role": "assistant", "content": [{"type": "text", "text": reask[0]}]},
                         {"role": "user", "content": [{"type": "text", "text": reask[1]}]}]
            prompt = proc.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True) + "{"
            inp = proc(text=[prompt], images=imgs, return_tensors="pt").to(model.device)
            out = model.generate(**inp, max_new_tokens=QWEN_MAX_NEW_TOKENS, do_sample=False, repetition_penalty=1.0,
                                 stopping_criteria=L.StoppingCriteriaList([self._stopper(progress)]))
            if L.mm.processing_interrupted():
                raise L.mm.InterruptProcessingException()
            n_in = int(inp["input_ids"].shape[1])
            new = out[:, n_in:]
            n_out = int(new.shape[1])
            eos = model.generation_config.eos_token_id if n_out >= QWEN_MAX_NEW_TOKENS else None
            cut = n_out >= QWEN_MAX_NEW_TOKENS and int(new[0, -1]) not in (eos if isinstance(eos, (list, tuple)) else [eos])
            text = "{" + proc.batch_decode(new, skip_special_tokens=True)[0]
            return text, {"input_tokens": n_in, "output_tokens": n_out, "finish": "length" if cut else "stop"}

    @staticmethod
    def _reask_text(text: str, usage: dict) -> str | None:
        """같은 시도 안에서 1회 다시 물을 사유(없으면 None): 파싱 실패 / max_new_tokens 에서 잘림 / 닫히지 않은 JSON(truncated)."""
        if usage.get("finish") == "length":
            return (f"Your output was cut off at the {QWEN_MAX_NEW_TOKENS}-token limit. Output the complete JSON object "
                    f"again, much shorter: short phrases, no repetition.")
        try:
            _obj, repairs = extract_json(text)
        except ParseError as e:
            return f"Your output was not valid JSON: {_short(e, 200)}. Output only the JSON object."
        if any(str(r).startswith("truncated") for r in repairs):
            return "Your JSON output stopped before it was complete. Output the complete JSON object again, shorter."
        return None

    async def call(self, unit, req, ledger, *, progress=None, **_) -> CallResult:
        """로드·생성을 이벤트 루프 스레드에서 동기로 한다(asyncio.to_thread 를 쓰지 않음 — ComfyUI 실행 루프가 그동안 다른
        노드를 돌리지 않으므로 free_memory·로드·생성이 다른 GPU 노드와 겹치지 않는다. 인터럽트·진행 표시는 서버 루프가 따로
        처리한다). 로드(VramError)는 원장을 쓰기 전에 한다 — 실패하면 단위는 대기 그대로.
        파싱 실패·잘림(max_new_tokens, truncated 수리)이면 첫 출력을 resp(status "reask")에 남기고 같은 시도 안에서 1회 다시
        묻는다(무료). 다시 물어도 max_new_tokens 에서 잘리면 failed(result). 과금이 없으므로 실패는 failed(maybe_billed False),
        생성 중 인터럽트는 rejected(같은 시도 재실행) — inflight(orphan '과금됐을 수 있음')로 남기지 않는다."""
        t0 = time.monotonic()
        self._ensure()
        images = [render_sent(im) for im in _images_of(req)]
        transport = {"schema_level": 0, "background": False}
        body = {"model": _key_model("local_qwen", req.model, self.model_dir),
                "system_sha": hashlib.sha256((req.system or "").encode("utf-8")).hexdigest()[:12],
                "user_text": req.user_text, "max_new_tokens": QWEN_MAX_NEW_TOKENS, "prefill": "{", "attn": QWEN_ATTN,
                "images": [[im.role, im.name, im.src_sha, int(im.sent_wh[0]), int(im.sent_wh[1])] for im in _images_of(req)]}
        _record_req(ledger, unit, settings=_settings(self.name, unit, req, req.model, None, None), body=body,
                    transport=transport)
        _record_inflight(ledger, unit, transport)
        try:
            text, usage = self._generate(req, images, progress)
            resp = {"status": "completed", "output_text": text, "usage": usage, "credits_poll": None}
            ask = self._reask_text(text, usage)
            if ask:
                ledger.write(unit.crop_id, unit.key, unit.attempt, "resp", dict(resp, status="reask", reask=ask))
                text2, usage2 = self._generate(req, images, progress, (text, ask))
                resp.update(output_text=text2, usage=usage2, first_output=text, reask=ask)
                text, usage = text2, usage2
            if usage.get("finish") == "length":
                resp["status"] = "incomplete"  # 재파싱(_reparse)도 이 출력을 done 으로 만들지 않게
            ledger.write(unit.crop_id, unit.key, unit.attempt, "resp", resp)
            if usage.get("finish") == "length":
                raise _record_failed(ledger, unit, f"Qwen 출력이 max_new_tokens({QWEN_MAX_NEW_TOKENS})에서 잘림(재질문 뒤에도) "
                                                   f"— 같은 입력이면 다시 해도 같음(greedy): _hint 로 줄이거나 openai 로",
                                     status=None, phase="result", maybe_billed=False, retryable=False)
            rec = _complete(ledger, unit, text, credits=None, elapsed_s=time.monotonic() - t0, transport=transport,
                            billed=False)
        except runner.CallError:
            raise
        except BaseException as e:
            if isinstance(e, Exception) and not isinstance(e, runner._interrupt_types() + (self.libs.mm.InterruptProcessingException,)):
                raise _record_failed(ledger, unit, f"로컬 생성 실패(과금 없음): {_short(repr(e), 200)}", status=None,
                                     phase="result", maybe_billed=False, retryable=True) from e
            _record_rejected(ledger, unit, "취소됨 — 로컬 생성 중단(과금 없음)", status=None, phase="result")
            raise
        return CallResult(text=text, usage=usage, credits=None, response_id=None, transport=rec["transport"],
                          seconds=rec["elapsed_s"])

    async def repoll(self, unit, created, ledger, **_) -> CallResult:
        raise runner.CallError("local_qwen 에는 재폴링이 없습니다(다른 백엔드의 created)", status=None, phase="poll",
                               retryable=False, maybe_billed=False)

    def unload(self) -> None:
        """모듈 캐시의 모델을 내린다(로드된 것이 없으면 아무것도 하지 않음 — 0 호출 실행의 finally 에서도 불러도 된다)."""
        unload_cached(self.libs.torch if self.libs is not None else None)


_MOCK_CREATE_ACTIONS = ("400format", "400background", "400", "401", "500create", "interrupt_create", "crash_after_create")
_MOCK_POLL_ACTIONS = ("poll401", "poll404", "poll_deadline", "interrupt_poll", "incomplete", "refusal", "badjson")
_MOCK_TARGET_RE = re.compile(r'^- ([A-Z]): Korean label "(.*)"; English name: (?:"(.*?)" \(use exactly\)|none)', re.M)


def _schema_ids(schema: dict) -> list[str]:
    try:
        return list(schema["properties"]["targets"]["items"]["properties"]["id"]["enum"])
    except (KeyError, TypeError):
        return []


def _mock_text(req: Request) -> str:
    """req 로 결정적인 JSON 텍스트(실제와 같은 extract → normalize 경로를 지난다)."""
    if req.kind == "style":
        obj = {"schema_echo": SCHEMA_ECHO,
               "observations": [{"image": str(k + 1), "shows": "mock illustration view"} for k in range(len(_images_of(req)) or 1)],
               "style_note": "thin dark line art, flat cel shading, neutral warm light, smooth paint texture"}
        return json.dumps(obj, ensure_ascii=False)
    names = {mt.group(1): (mt.group(3) or "") for mt in _MOCK_TARGET_RE.finditer(req.user_text or "")}
    tids = _schema_ids(req.schema) or list(names) or ["A"]
    has_ref = bool(req.shown_refs)
    frame = tuple(req.bbox_frame or ("q1000",))
    if frame[0] == "px" and len(frame) == 3:
        W, H = int(frame[1]), int(frame[2])
        bbox = [W // 4, H // 4, W * 3 // 4, H * 3 // 4]
    else:
        bbox = [250, 250, 750, 750]
    targets = []
    for tid in tids:
        name = names.get(tid) or f"mock part {tid}"
        targets.append({
            "id": tid, "name": name, "found": True, "canvas_now": f"a rough drawing of the {name}", "counts": [],
            "mode": "ref_correct" if has_ref else "self_restore", "suggested_mode": "none", "action": "fix",
            "ref_images": [2] if has_ref else [], "ref_object": f"the {name}" if has_ref else "", "ref_ignore": "",
            "ref_view": "same", "detail": f"a simple {name} outline; two flat base colors",
            "must_not": [], "defects": "" if has_ref else "a smudged outline", "location": "", "occluded_by": "",
            "shape": "moderate", "text": "none", "bbox": bbox, "confidence": "high", "review_note": ""})
    obj = {"schema_echo": SCHEMA_ECHO,
           "observations": [{"image": "1", "shows": "mock canvas"}]
           + [{"image": str(k + 2), "shows": f"mock reference {r}"} for k, r in enumerate(req.shown_refs)],
           "preserve": ["the neighboring artwork"], "targets": targets, "suggested_targets": []}
    return json.dumps(obj, ensure_ascii=False)


class MockVLM:
    """과금 없는 시험 백엔드. 결과 = _mock_text(req). created → poll 을 흉내 내 원장 경로를 그대로 탄다.
    fail_plan 동작은 인스턴스마다 crop_id 별로 앞에서부터 소비(모듈 docstring 'mock'). 시험용 기록: calls, polls."""

    name = BACKEND_NAMES["mock"]

    def __init__(self, options: dict):
        opts = dict(options or {})
        self.delay_s = float(opts.get("delay_s", 0.0))
        cr = opts.get("credits")
        self.credits = float(cr) if isinstance(cr, (int, float)) and not isinstance(cr, bool) else None
        self.fail_plan: dict[str, list[str]] = {}
        for k, v in (opts.get("fail_plan") or {}).items():
            acts = [str(a) for a in (v if isinstance(v, list) else [v])]
            unknown = [a for a in acts if a not in _MOCK_CREATE_ACTIONS + _MOCK_POLL_ACTIONS]
            if unknown:
                logger.warning(f"{_TAG} VLM mock fail_plan[{k}] 의 모르는 동작 무시: {unknown}")
            self.fail_plan[str(k)] = [a for a in acts if a not in unknown]
        self._cursor: dict[str, int] = {}
        self.interrupt_check: Callable[[], bool] = runner._comfy_interrupt_flag
        self.calls: list[dict] = []   # 전송(create) 기록
        self.polls: list[dict] = []   # 폴링 기록(repoll 포함)

    def __repr__(self) -> str:
        return f"MockVLM(delay_s={self.delay_s}, fail_plan={sorted(self.fail_plan)})"

    def has_auth(self) -> bool:
        return True

    def _take(self, crop_id: str, allowed: tuple) -> str | None:
        acts = self.fail_plan.get(crop_id) or []
        i = self._cursor.get(crop_id, 0)
        if i < len(acts) and acts[i] in allowed:
            self._cursor[crop_id] = i + 1
            return acts[i]
        return None

    async def _sleep(self, seconds: float, progress=None, t0: float | None = None) -> None:
        end = time.monotonic() + max(0.0, seconds)
        while True:
            if self.interrupt_check():
                raise runner._make_interrupt()
            left = end - time.monotonic()
            if left <= 0:
                return
            if progress is not None and t0 is not None:
                el = int(time.monotonic() - t0)
                progress(f"폴링 {el // 60}:{el % 60:02d}")
            await asyncio.sleep(min(0.02, left))

    async def call(self, unit: Unit, req: Request, ledger: Ledger, *, model="mock", effort=None, image_detail=None,
                   progress=None, **_) -> CallResult:
        t0 = time.monotonic()
        caps = _caps_transport(ledger.root, "mock", "mock")
        level = int(caps.get("schema_level", 2))
        background = bool(caps.get("background", True))
        transport = {"schema_level": level, "background": background}
        body = {"model": "mock", "system_sha": hashlib.sha256((req.system or "").encode("utf-8")).hexdigest()[:12],
                "user_text": req.user_text,
                "images": [[im.role, im.name, im.src_sha, int(im.sent_wh[0]), int(im.sent_wh[1])] for im in _images_of(req)]}
        # 기록은 사실대로: _MOCK_API 가 openai 를 mock 으로 바꿔도 model 은 "mock"(effort·detail 은 mock 요청에 없음)
        _record_req(ledger, unit, settings=_settings(self.name, unit, req, "mock", req.effort, req.image_detail), body=body,
                    transport=transport)
        while True:
            transport = {"schema_level": level, "background": background}
            _record_inflight(ledger, unit, transport)
            self.calls.append({"crop_id": unit.crop_id, "key": unit.key, "attempt": unit.attempt, "kind": unit.kind,
                               "level": level, "background": background})
            await self._sleep(self.delay_s / 2)
            act = self._take(unit.crop_id, _MOCK_CREATE_ACTIONS)
            if act == "400format" and level > 0:
                level -= 1
                continue
            if act == "400background" and background:
                background = False
                continue
            if act == "interrupt_create":
                raise runner._make_interrupt()
            if act in ("400", "400format", "400background"):
                raise _record_rejected(ledger, unit, "mock: HTTP 400 (요청 거부, 과금 전)", status=400)
            if act == "401":
                raise _record_rejected(ledger, unit, "mock: Unauthorized: comfy.org 로그인 필요", status=401)
            if act == "500create":
                raise _record_failed(ledger, unit, "mock: create 단계 HTTP 500", status=500, phase="create",
                                     maybe_billed=True, retryable=True)
            break
        if level != int(caps.get("schema_level", 2)):  # OpenAIVLM 과 같게: 받아들여진 단계만 caps 에
            _caps_set(ledger.root, "mock", "mock", schema_level=level)
        if background != bool(caps.get("background", True)):
            _caps_set(ledger.root, "mock", "mock", background=background)
        poll_act = self._take(unit.crop_id, _MOCK_POLL_ACTIONS)
        created = {"response_id": f"mock_{unit.key[:12]}_a{unit.attempt}", "status": "queued", "credits_create": None,
                   "transport": transport, "mock": {"text": _mock_text(req)}}
        ledger.write(unit.crop_id, unit.key, unit.attempt, "created", created)
        if act == "crash_after_create":
            raise RuntimeError("mock: create 직후 크래시(시험) — created 만 남김")
        return await self._poll(unit, created, ledger, t0, progress, poll_act)

    async def repoll(self, unit: Unit, created: dict, ledger: Ledger, *, effort=None, progress=None) -> CallResult:
        return await self._poll(unit, created, ledger, time.monotonic(), progress, None)

    async def _poll(self, unit: Unit, created: dict, ledger: Ledger, t0: float, progress, act: str | None) -> CallResult:
        mock = created.get("mock") if isinstance(created, dict) else None
        if not isinstance(mock, dict) or not isinstance(mock.get("text"), str):
            raise runner.CallError("mock: created 기록에 mock 결과가 없습니다(다른 백엔드의 원장)", status=None,
                                   phase="poll", retryable=False, maybe_billed=False)
        transport = dict(created.get("transport") or {})
        self.polls.append({"crop_id": unit.crop_id, "key": unit.key, "attempt": unit.attempt,
                           "response_id": created.get("response_id")})
        await self._sleep(self.delay_s / 2, progress, t0)
        if act == "interrupt_poll":
            raise runner._make_interrupt()
        if act == "poll401":
            raise runner.CallError("mock: Unauthorized (폴링 중) — created 유지, 다시 큐에 넣으면 무료 재폴링",
                                   status=401, phase="poll", retryable=True, maybe_billed=False)
        if act == "poll404":  # OpenAIVLM._poll 과 같게: 읽을 수 없는 응답 id 는 과금됐을 수 있는 실패
            raise _record_failed(ledger, unit, "mock: 폴링 GET HTTP 404 — 응답을 더 읽을 수 없음(과금됐을 수 있음)",
                                 status=404, phase="poll", maybe_billed=True, retryable=True)
        if act == "poll_deadline":
            raise runner.CallError("mock: 폴링 마감 초과 — created 유지(복구 가능)", status=None, phase="poll",
                                   retryable=True, maybe_billed=False)
        text = "mock: 이 응답에는 JSON 이 없습니다" if act == "badjson" else mock["text"]
        status = "incomplete" if act == "incomplete" else "completed"
        usage = {"input_tokens": len((created.get("response_id") or "")) + 1000, "output_tokens": len(text) // 4,
                 "mock": True}
        ledger.write(unit.crop_id, unit.key, unit.attempt, "resp",
                     {"status": status, "output_text": "" if act == "incomplete" else text, "usage": usage,
                      "credits_poll": self.credits, "refusal": act == "refusal",
                      "incomplete_details": {"reason": "max_output_tokens"} if act == "incomplete" else None})
        if act == "incomplete":
            raise _record_failed(ledger, unit, "mock: OpenAI incomplete(출력 한도 소진)", status=None, phase="result",
                                 maybe_billed=True, retryable=True, credits=self.credits)
        if act == "refusal":
            raise _record_failed(ledger, unit, "mock: 모델이 거절함(refusal)", status=None, phase="result",
                                 maybe_billed=True, retryable=True, credits=self.credits)
        rec = _complete(ledger, unit, text, credits=self.credits, elapsed_s=time.monotonic() - t0, transport=transport)
        return CallResult(text=text, usage=usage, credits=self.credits, response_id=created.get("response_id"),
                          transport=transport, seconds=rec["elapsed_s"])


def vlm_mock_options(root: str) -> dict:
    """_MOCK_API JSON 의 "vlm" 키(runner._mock_options 는 delay_s/fail_plan/response 만 통과시키므로 따로 읽는다).
    파일이 없거나(환경변수만) JSON 이 아니거나 "vlm" 이 객체가 아니면 {}."""
    path = os.path.join(os.path.dirname(os.path.abspath(root)), runner.MOCK_FILE)
    try:
        with open(path, "rb") as f:
            raw = f.read().decode("utf-8-sig").strip()
    except OSError:
        return {}
    if not raw:
        return {}
    try:
        opts = json.loads(raw)
    except ValueError:
        logger.warning(f"{_TAG} {runner.MOCK_FILE} 내용이 JSON 이 아님 → 기본 VLM mock 설정")
        return {}
    v = opts.get("vlm") if isinstance(opts, dict) else None
    return dict(v) if isinstance(v, dict) else {}


def make_vlm(family: str, root: str, hidden: dict, *, model_dir: str, keep_loaded: bool):
    """백엔드 생성. 호출자가 family 를 정한다(openai 이고 runner.mock_enabled(root) 면 "mock" — local_qwen 은 바꾸지 않음)."""
    if family == "mock":
        return MockVLM(vlm_mock_options(root))
    if family == "openai":
        return OpenAIVLM(hidden, root=root)
    if family == "local_qwen":
        return QwenVLM(model_dir, keep_loaded=keep_loaded)
    raise ValueError(f"{_TAG} 알 수 없는 VLM family: {family!r}")


@dataclass
class RunReport:
    launched: int = 0
    done: int = 0
    failed: int = 0
    rejected: int = 0
    orphaned: int = 0
    repolled: int = 0
    left_created: int = 0
    credits: float = 0.0             # 보고된 크레딧(실측)만
    unreported: int = 0              # 과금됐을 수 있는데 크레딧이 보고되지 않은 단위 — credit_cap 은 단위 최대로 셈
    stopped_reason: str = "none"     # none | auth | interrupt | credit_cap | vram
    cap_need: float = 0.0            # credit_cap 이 지출·진행 중 0 에서 막은 단위의 최대 추정(> 0 = 상한이 1건보다 작음)
    lines: list[str] = field(default_factory=list)
    usage: list[dict] = field(default_factory=list)


_EXPECT_STATE = {"new": ("none", "req"), "retry": ("failed",), "orphan": ("inflight",), "rejected": ("rejected",),
                 "repoll": ("created",), "reparse": ("resp",)}
_STOP_TEXT = {"auth": "인증 거부/없음(401/402/403) — 다시 로그인한 뒤 큐", "interrupt": "취소됨",
              "credit_cap": "credit_cap 도달(실측(미보고 = 단위 최대) + 다음 단위 최대 > 상한) — 남은 단위는 대기 유지",
              "vram": "VRAM 부족·CUDA 없음 — 로컬 단위는 대기 유지"}


def _state_matches(u: Unit, st: dict) -> bool:
    """계획 뒤에 원장이 바뀌었는지(다른 실행이 먼저 처리) — 이중 과금 방지."""
    if st["status"] not in _EXPECT_STATE.get(u.state, ()):
        return False
    return (st["attempt"] if u.state in ("repoll", "reparse") else st["next_attempt"]) == u.attempt


def _reparse(ledger: Ledger, u: Unit) -> CallResult:
    """원장 resp 의 원문을 다시 추출(무료). resp 가 완료가 아니면 failed(local_qwen 원장은 maybe_billed False —
    재질문 중 끊긴 Qwen 의 resp 는 status "reask")."""
    resp = ledger.read(u.crop_id, u.key, u.attempt, "resp") or {}
    created = ledger.read(u.crop_id, u.key, u.attempt, "created") or {}
    rq = ledger.read(u.crop_id, u.key, u.attempt, "req") or {}
    transport = dict(created.get("transport") or rq.get("transport") or {})
    vals = [v for v in (created.get("credits_create"), resp.get("credits_poll")) if isinstance(v, (int, float))]
    credits = max(vals) if vals else None
    billed = (rq.get("settings") or {}).get("backend") != BACKEND_NAMES["local_qwen"]
    if resp.get("status") != "completed":
        raise _record_failed(ledger, u, f"원장 resp 가 완료가 아님({resp.get('status')!r})", status=None, phase="result",
                             maybe_billed=billed, retryable=True, credits=credits)
    text = str(resp.get("output_text") or "")
    rec = _complete(ledger, u, text, credits=credits, elapsed_s=0.0, transport=transport, billed=billed)
    return CallResult(text=text, usage=resp.get("usage"), credits=credits, response_id=created.get("response_id"),
                      transport=rec["transport"], seconds=0.0)


async def run_units(root: str, units: list[Unit], reqs: dict[str, Request], backend, *, model: str, effort: str,
                    image_detail: str, concurrency: int, credit_cap: int, progress_cb) -> RunReport:
    """to_run 실행: openai = Semaphore(concurrency), local_qwen = 순차. repoll → backend.repoll, reparse → 원장 resp 재추출.
    401/402/403 이면 새 발사 중지. credit_cap: 실측 + 이번 단위 최대 > cap 이면 새 발사 중지, 진행 중 단위 최대(예약)까지
    더해야 넘치면 진행 중인 것이 끝나 실측으로 바뀔 때까지 기다렸다가 다시 판정한다(동시 실행 때문에 멈추지 않게).
    과금됐을 수 있는데 크레딧이 보고되지 않은 단위(헤더 없음·5xx·orphan 등)는 실측 대신 단위 최대로 센다(report.unreported).
    인터럽트는 도착분 저장 후 다시 낸다. reqs 는 crop_id(화풍 STYLE_ID) → Request.
    - 인증 정보가 없는 백엔드(has_auth False)면 네트워크 단위는 0건(stopped_reason "auth"), 재파싱만 한다.
    - VramError(local_qwen) 면 나머지 단위를 발사하지 않는다(stopped_reason "vram", 원장은 그대로 → 대기 유지).
    - 인터럽트: 새 발사를 멈추고 같은 예외를 다시 낸다(예외의 bmk_dp_report = RunReport). create 중이면 inflight(orphan),
      폴링 중이면 created 가 남는다(다음 실행 무료 재폴링).
    - progress_cb(done, total, text): 단위가 끝날 때마다와 폴링·생성 중. 거기서 나는 인터럽트는 인터럽트로 처리한다."""
    report = RunReport()
    units = list(units)
    total = len(units)
    if not total:
        return report
    ledger = Ledger(root)
    paid = backend.name == BACKEND_NAMES["openai"]
    local = backend.name == BACKEND_NAMES["local_qwen"]
    cap = max(0, int(credit_cap or 0))
    st = SimpleNamespace(stop=None, exc=None, spent=0.0, actual=0.0, inflight={}, finished=0,  # spent = cap 용(미보고 = 단위 최대)
                         released=asyncio.Event())  # 진행 중 예약이 풀릴 때마다 — credit_cap 'wait' 단위가 다시 판정
    attempted: list[Unit] = []

    def interrupt(exc: BaseException | None) -> None:
        st.stop = "interrupt"
        if st.exc is None and exc is not None:
            st.exc = exc

    def progress(i: int, u: Unit, text: str) -> None:
        if progress_cb is None:
            return
        try:
            progress_cb(st.finished, total, f"초안 {i}/{total} · {u.crop_id} · {text}")
        except runner._interrupt_types() as e:
            interrupt(e)
            mod = sys.modules.get("comfy.model_management")
            if mod is not None:  # ProgressBar 훅이 플래그를 지웠다 → 다시 세워 폴링·생성도 멈추게
                mod.interrupt_current_processing(True)
        except Exception as e:  # 진행 표시 실패가 실행을 멈추면 안 된다
            logger.warning(f"{_TAG} progress_cb 오류(무시): {e!r}")

    if not backend.has_auth() and any(u.state != "reparse" for u in units):
        st.stop = "auth"
        report.lines.append("comfy.org 로그인 필요 — 인증 정보가 없어 호출하지 않음(0건). ComfyUI 에 로그인한 뒤 다시 큐")
        units = [u for u in units if u.state == "reparse"]

    def block(u: Unit) -> str | None:
        """발사 직전 확인(await 없이) → 막을 이유. 통과한 과금 단위는 이 자리에서 진행 중으로 예약한다(동시 발사 경쟁 방지)."""
        if st.stop == "interrupt":
            return "interrupt"
        if runner._comfy_interrupt_flag():
            interrupt(None)
            return "interrupt"
        if u.state == "reparse":
            return None
        if st.stop is not None:
            return st.stop
        need = float(u.est[1]) if paid and u.billable else 0.0
        if u.billable and cap > 0:
            if st.spent >= cap or st.spent + need > cap:
                st.stop = "credit_cap"
                if not st.spent and not st.inflight:
                    report.cap_need = need
                return "credit_cap"
            if st.spent + sum(st.inflight.values()) + need > cap:
                return "wait"  # 진행 중 예약 때문에 넘친다 — 그것들이 끝나 실측으로 바뀐 뒤 다시 판정
        if u.billable:
            st.inflight[id(u)] = need
        return None

    async def one(i: int, u: Unit) -> None:
        try:
            await one_inner(i, u)
        finally:
            st.finished += 1
            progress(i, u, "끝")

    async def one_inner(i: int, u: Unit) -> None:
        async with sem:
            why = block(u)
            while why == "wait":
                st.released.clear()
                await st.released.wait()
                why = block(u)
            if why is not None:
                report.lines.append(f"  {u.crop_id} a{u.attempt}: 발사 안 함 — {_STOP_TEXT.get(why, why)}")
                return
            try:
                await launch(i, u)
            finally:
                st.inflight.pop(id(u), None)
                st.released.set()

    async def launch(i: int, u: Unit) -> None:
        req = reqs.get(u.crop_id)
        if req is None or req.key != u.key:
            report.lines.append(f"  {u.crop_id} a{u.attempt}: 요청이 계획과 다름 — 건너뜀(다시 큐)")
            return
        if not _claim(root, u.key):
            report.lines.append(f"  {u.crop_id} a{u.attempt}: 같은 초안이 이미 실행 중 — 건너뜀")
            return
        try:
            cur = await asyncio.to_thread(ledger.state, u.crop_id, u.key)
            if not _state_matches(u, cur):
                report.lines.append(f"  {u.crop_id} a{u.attempt}: 원장 상태가 계획과 다름({cur['status']}) — 건너뜀(다시 큐)")
                return
            attempted.append(u)
            if u.billable:
                report.launched += 1
            try:
                res = await dispatch(i, u, req)
            except runner.CallError as e:
                if e.status in runner.AUTH_STATUSES and st.stop is None:
                    st.stop = "auth"
                report.lines.append(f"  {u.crop_id} a{u.attempt} [{u.state}] {e.phase}: {_short(e, 200)}")
            except VramError as e:
                st.stop = st.stop or "vram"
                report.lines.append(f"  {u.crop_id} a{u.attempt}: {e}")
            except runner._interrupt_types() as e:  # 기다리던 단위가 발사하지 않게 바로 표시하고 다시 낸다
                interrupt(e)
                raise
            except Exception as e:  # 백엔드 버그·로컬 실패 — 원장 상태로 다음 계획이 정한다
                logger.error(f"{_TAG} VLM 초안 {u.crop_id} 처리 중 예외: {e!r}")
                report.lines.append(f"  {u.crop_id} a{u.attempt} [{u.state}] 오류: {_short(repr(e), 200)}")
            else:
                report.done += 1
                if u.state in ("repoll", "reparse"):
                    report.repolled += 1
                report.usage.append({"crop_id": u.crop_id, "key": u.key, "attempt": u.attempt, "kind": u.kind,
                                     "usage": res.usage, "credits": res.credits, "seconds": res.seconds})
                cr = "" if res.credits is None else f" · 크레딧 {float(res.credits):,.2f}"
                report.lines.append(f"  {u.crop_id} a{u.attempt} [{u.state}] 완료 · {res.seconds:.1f}s{cr}")
            finally:
                # 원장을 먼저 읽고, 예약 해제와 지출 반영은 await 없이 한 번에(그 사이 발사 판정이 둘 다 못 보는 틈 없음)
                after = await asyncio.to_thread(ledger.state, u.crop_id, u.key)
                st.inflight.pop(id(u), None)
                if after["attempt"] == u.attempt and after["credits"] is not None:
                    st.spent += float(after["credits"])
                    st.actual += float(after["credits"])
                elif (after["attempt"] == u.attempt and paid and u.billable
                      and after["status"] in ("inflight", "created", "resp", "done", "failed")):
                    st.spent += float(u.est[1])  # 과금됐을 수 있는데 크레딧 미보고 — 0 이 아니라 단위 최대로
                    report.unreported += 1
        finally:
            _release(root, u.key)

    async def dispatch(i: int, u: Unit, req: Request) -> CallResult:
        p = functools.partial(progress, i, u)
        if u.state == "reparse":
            return await asyncio.to_thread(_reparse, ledger, u)
        if u.state == "repoll":
            created = await asyncio.to_thread(ledger.read, u.crop_id, u.key, u.attempt, "created")
            return await backend.repoll(u, created or {}, ledger, effort=effort, progress=p)
        return await backend.call(u, req, ledger, model=model, effort=effort, image_detail=image_detail, progress=p)

    sem = asyncio.Semaphore(1 if local else max(1, int(concurrency)))
    results = await asyncio.gather(*(one(i, u) for i, u in enumerate(units, 1)), return_exceptions=True)
    for u, r in zip(units, results):
        if isinstance(r, runner._interrupt_types()):
            interrupt(r)
        elif isinstance(r, BaseException):
            logger.error(f"{_TAG} VLM 초안 {u.crop_id} 처리 중 예외: {r!r}")
            report.lines.append(f"  {u.crop_id} a{u.attempt}: 오류 {_short(repr(r), 200)}")
    for u in attempted:  # 끝난 뒤 원장 상태로 집계(인터럽트로 끊긴 단위 포함)
        s = ledger.state(u.crop_id, u.key)
        if s["status"] == "failed":
            report.failed += 1
        elif s["status"] == "rejected":
            report.rejected += 1
        elif s["status"] == "inflight":
            report.orphaned += 1
            report.lines.append(f"  {u.crop_id} a{u.attempt}: orphan — 결과 없이 끊김(과금됐을 수 있음), 다음 승인 목록에 "
                                f"'orphan 재시도'")
        elif s["status"] in ("created", "resp"):
            report.left_created += 1
            report.lines.append(f"  {u.crop_id} a{u.attempt}: created 유지 — 다시 큐에 넣으면 무료 재폴링")
    report.credits = round(st.actual, 4)
    report.stopped_reason = st.stop or "none"
    logger.info(f"{_TAG} VLM 초안 실행: 발사 {report.launched} · 완료 {report.done} · 실패 {report.failed} · "
                f"거부 {report.rejected} · orphan {report.orphaned} · created {report.left_created} · "
                f"크레딧 {report.credits:,.2f}(미보고 {report.unreported}) · 중지 {report.stopped_reason}")
    if st.stop == "interrupt":
        exc = st.exc or runner._final_interrupt()
        try:
            exc.bmk_dp_report = report
        except AttributeError:
            pass
        raise exc
    return report


# ══════════════════════════════════════════════════════════════════════
# 보고서 (§2.10)
# ══════════════════════════════════════════════════════════════════════
_CONF_KO = {"high": "높음", "medium": "중간", "low": "낮음"}


def _val(v: Any, limit: int = 60) -> str:
    s = v if isinstance(v, str) else _canon(v)
    return f'"{_short(s, limit)}"' if isinstance(v, str) else _short(s, limit)


def _row_line(r: DiffRow) -> str:
    mark = _DIFF_MARK.get(r.kind, "?")
    if r.kind == "keep_human":
        return f"{mark} {r.path} 사람 값 유지(새 값 {_val(r.new)} → _draft.pending)"
    if r.kind == "keep_deleted":
        return f"{mark} {r.path} 사람이 지움 — 다시 쓰지 않음"
    if r.kind == "yield":
        return f"{mark} {r.path} 양보(사람 출처가 생김) — 지움"
    if r.kind == "delete":
        return f"{mark} {r.path} 지움"
    return f"{mark} {r.path} {_val(r.new)}"


_NO_DRAFT_FLAGS = ("lint: CJK", "warn: 기본 설명", "v4_equals_v1")   # 초안 전 크롭은 한글 라벨·기본 설명으로 렌더된다


def _crop_status(ctx: CropCtx, plan: dict, merges: dict, checks: dict, drafts: dict) -> tuple[int, str, list[str]]:
    """(심각도 0 실패 · 1 신뢰 낮음 · 2 경고 · 3 정상 · 4 초안 없음, 머리 줄, 붙는 줄). 4 = 초안이 아직 한 번도 없고 경고가
    초안 전 렌더(_NO_DRAFT_FLAGS)뿐인 크롭 — 보고서가 크롭마다 경고하지 않고 한 줄로 묶는다."""
    cid = ctx.crop_id
    head = f"{cid} {ctx.name}"
    extra: list[str] = []
    res, chk, d = merges.get(cid), checks.get(cid), drafts.get(cid)
    unit = next((u for u in plan.get("units") or [] if u.crop_id == cid and u.state in ("retry", "rejected", "orphan")), None)
    if ctx.spec_error:
        return 0, f"{head}: specs/{cid}.json JSON 오류 — {ctx.spec_error} (Prepare 도 이 파일에서 멈춤)", extra
    if unit is not None:
        why = unit.reasons[0]
        tail = " — 다음 승인 목록에 'orphan 재시도' 로 포함" if unit.state == "orphan" else " — 다음 승인 목록에 '재시도' 로 포함"
        if "incomplete" in why:
            tail += "(effort 낮추기 권장)"
        return 0, f"{head} a{unit.attempt}: {why}{tail}", extra
    if chk is not None and not chk.ok:
        return 0, f"{head}: {'; '.join(chk.problems)}", extra
    notes = [n for n in (res.notes if res else []) if n.startswith("쓰지 않음")]
    if notes:
        return 0, f"{head}: {'; '.join(notes)}", extra
    flags = list(ctx.warnings) + ([ctx.skip_reason] if ctx.skip_reason else [])
    confs = []
    if d is not None:
        flags += d.flags
        for tid, rv in d.review.items():
            tf = [f for f in rv.get("flags") or []]
            c = _CONF_KO.get(rv.get("confidence"), "?")
            confs.append(f"{tid} {c}" + (f"({'; '.join(tf[:2])})" if tf else ""))
            flags += [f"{tid} {f}" for f in tf]
            if rv.get("note"):
                extra.append(f"  검토 {tid}: {rv['note']}")
        sug = d.suggestions
        legacy = any(t.legacy for t in ctx.targets)
        if sug.get("targets") or (legacy and sug.get("layer_name")):
            extra.append(f"  제안 타깃 → 레이어명: {sug.get('layer_name')}")
    if chk is not None:
        flags += chk.flags + [f"validate: {p}" for p in chk.problems]  # validate_spec 문제는 쓰기를 막지 않고 경고로
    sd = ctx.spec.get("_draft") if isinstance(ctx.spec, dict) else None
    if d is None and not (isinstance(sd, dict) and sd.get("key")) and any(f in _NO_DRAFT_FLAGS for f in flags):
        flags = [f for f in flags if f not in _NO_DRAFT_FLAGS]
        if not flags:
            return 4, cid, []
    if res is not None:
        extra = [f"  {_row_line(r)}" for r in res.rows
                 if r.kind in ("add", "update", "delete", "force", "keep_human", "keep_deleted", "yield")][:8] + extra
        flags += [f"{r.path} {r.kind}" for r in res.rows if r.kind in ("unknown_target", "dup_target")]
    line = head + (" · " + " · ".join(confs) if confs else "")
    tail = " · " + "; ".join(dict.fromkeys(flags)) if flags else ""
    if d is not None and any(rv.get("confidence") == "low" for rv in d.review.values()):
        return 1, line + tail, extra
    if flags:
        return 2, line + tail, extra
    return 3, cid + (" (확정)" if cid in (plan.get("accepted") or []) else ""), []


def format_report(*, header: dict, plan: dict, approval: dict, run: RunReport | None, merges: dict[str, MergeResult],
                  checks: dict[str, RenderCheck], drafts: dict[str, Draft], ctxs: list[CropCtx]) -> str:
    """한국어 보고서(나쁜 것부터: 실패 > 신뢰 낮음 > 경고 > 정상, §3.6 예). plan / approval 은 실행 뒤 다시 계산한
    지금 상태(남은 승인 대기·해시)를 넘긴다. header 키(모두 선택): backend(이름), model, effort, write_mode, seconds,
    has_auth(False 면 로그인 안내), keep_loaded(local_qwen 상주 경고), style_note, credit_cap(상한이 1건보다 작을 때 안내),
    errors[], backups[].
    merges[PROJECT_SPEC_ID] 가 있으면 _project.json 병합으로 보고 화풍 줄에 쓴다."""
    be = header.get("backend") or BACKEND_NAMES["mock"]
    fam = _family(be)
    model = header.get("model") or plan.get("model") or ""
    effort = header.get("effort") or plan.get("effort") or ""
    label = {"openai": f"OpenAI {model} {effort} · 유료", "local_qwen": f"로컬 {model} · 무료(VRAM)",
             "mock": "MOCK · 과금 없음"}[fam]
    units = plan.get("units") or []
    n = len(units)
    typ, hi = approval.get("est_credits") or (0, 0)
    if fam == "openai":
        cost = f" ≈ {typ:,} 크레딧(최대 {hi:,})"
    elif fam == "local_qwen":
        cost = f" ≈ {max(1, math.ceil((approval.get('est_seconds') or 0) / 60))}분"
    else:
        cost = ""
    reasons = " · ".join(f"{k} {v}" for k, v in (approval.get("reasons") or {}).items())
    if run is not None:
        state = (f"실행: 발사 {run.launched} · 완료 {run.done} · 실패 {run.failed} · 거부 {run.rejected} · "
                 f"orphan {run.orphaned} · created {run.left_created}"
                 + (f" · 크레딧 {run.credits:,.1f}" if fam == "openai" or run.credits else "")
                 + (f" · 크레딧 미보고 {run.unreported}건(credit_cap 은 단위 최대로 셈)" if run.unreported else "")
                 + (f" | 남은 승인 대기 {n}건{cost}" if n else ""))
    elif n:
        state = f"드라이런: 승인 필요 {n}건({reasons}){cost}"
    else:
        state = "할 일 없음(대기 0)"
    ra = plan.get("refresh_available") or []
    ra_cost = sum(int(e[0]) for _c, e in ra) if fam == "openai" else 0
    lines = [f"Spec Draft [{label}] {state} | 캐시 {len(plan.get('cached') or [])} · 복구 {len(plan.get('free') or [])} · "
             f"갱신 가능 {len(ra)}" + (f"(refresh ≈ {ra_cost:,})" if ra and fam == "openai" else "")
             + f" | {float(header.get('seconds') or 0):.1f}s"]
    if fam == "local_qwen" and header.get("keep_loaded"):
        lines.append(QWEN_RESIDENT_WARNING)
    if n:
        lines.append(f"→ 승인: 노드의 'VLM 초안 승인' 버튼(이 노드만 부분 실행·approve 자동 비움), 또는 approve 에 "
                     f"{approval.get('pending_hash')} 를 넣고 큐(워크플로 전체가 함께 실행되고 approve 는 직접 비워야 함)")
        if approval.get("orphan_retries"):
            lines.append(f"  orphan 재시도 {approval['orphan_retries']}건 포함(과금됐을 수 있음)")
    if fam == "openai" and not header.get("has_auth", True):
        lines.append("주의: comfy.org 로그인 필요 — ComfyUI 에 로그인(또는 API 키)한 뒤 큐에 넣으세요(지금은 호출 0건)")
    if run is not None:
        if run.stopped_reason == "credit_cap" and run.cap_need:
            need = math.ceil(run.cap_need)
            lines.append(f"중지: 크레딧 상한(credit_cap) {header.get('credit_cap', '?')} < 1건 최대 추정 {need:,} → 발사 안 함 — "
                         f"credit_cap 을 {need:,} 이상(0 = 상한 없음)으로 올린 뒤 같은 approve 로 다시 큐")
        elif run.stopped_reason != "none":
            lines.append(f"중지: {_STOP_TEXT.get(run.stopped_reason, run.stopped_reason)}")
        lines += run.lines
    # 스펙 요약
    mode = header.get("write_mode") or "fill"
    marks = {m: 0 for m in _DIFF_MARK.values()}
    for res in merges.values():
        for r in res.rows:
            if r.kind in _DIFF_MARK:
                marks[_DIFF_MARK[r.kind]] += 1
    down = _DIFF_MARK["yield"]
    files = sum(1 for r in merges.values() if r.changed and (r.spec is not None or mode == "preview"))
    rc = sum(1 for r in merges.values() if r.changed and r.render_changed and (r.spec is not None or mode == "preview"))
    lines.append(f"스펙({mode}{' — 쓰지 않고 diff 만' if mode == 'preview' else ''}): {files}파일 · + {marks['+']} · "
                 f"~ {marks['~']} · = 사람 값 유지 {marks['=']} · - 사람이 지운 필드 {marks['-']} · "
                 f"{down} 양보 {marks[down]} | 렌더가 바뀐 크롭 {rc}"
                 + (" → Prepare 새 cell_key, Run 재승인 필요" if rc and mode != "preview" else ""))
    pres = merges.get(PROJECT_SPEC_ID)
    if isinstance(pres, MergeResult) and pres.rows:
        lines.append(f"화풍(_project.json): {'; '.join(_row_line(r) for r in pres.rows)}")
    elif header.get("style_note"):
        lines.append(f"화풍: {_short(header['style_note'], 120)}")
    groups: dict[int, list] = {0: [], 1: [], 2: [], 3: [], 4: []}
    for ctx in ctxs:
        sev, head, extra = _crop_status(ctx, plan, merges, checks, drafts)
        groups[sev].append((head, extra))
    for sev, tag in ((0, "실패"), (1, "신뢰 낮음"), (2, "경고")):
        for head, extra in groups[sev]:
            lines.append(f"[{tag}] {head}")
            lines += extra
    if groups[4]:
        ids = [h for h, _ in groups[4]]
        lines.append(f"[경고] 초안 없음 {len(ids)}크롭: 한글 라벨·기본 설명으로 렌더됨(승인하면 채워짐) — "
                     + ", ".join(ids[:8]) + (" …" if len(ids) > 8 else ""))
    for cid, why in plan.get("skipped") or []:
        if cid == STYLE_ID:
            lines.append(f"[경고] 화풍: {why}")
    orphans = plan.get("orphan_specs") or []
    if orphans:
        lines.append(f"[경고] 크롭이 사라진 스펙 파일 {len(orphans)}개(지우지 않음, Prepare 가 읽지 않음): "
                     + ", ".join(orphans[:6]) + (" …" if len(orphans) > 6 else ""))
    if groups[3]:
        lines.append("[정상] " + " · ".join(h for h, _ in groups[3]))
    if ra:
        lines.append(f"갱신 가능(write_mode=refresh 로 다시 초안·갱신): "
                     f"{', '.join('화풍' if c == STYLE_ID else c for c, _ in ra)}")
    gloss: dict[str, str] = {}
    for d in drafts.values():
        gloss.update(d.suggestions.get("glossary") or {})
    if gloss:
        lines.append(f"용어집 제안(glossary.json): {json.dumps(gloss, ensure_ascii=False)}")
    lines += [f"백업: {b}" for b in header.get("backups") or []]
    lines += [f"오류: {e}" for e in header.get("errors") or []]
    return "\n".join(lines)


def _md(v: Any) -> str:
    s = v if isinstance(v, str) else _canon(v)
    return " ".join(str(s).split()).replace("|", "\\|")


def review_markdown(ctxs, merges, checks, drafts) -> str:
    """vlm/review.md 내용(크롭별 V1 전문·diff·플래그). specs 밖이라 Project 지문에 영향이 없다."""
    out = ["# Spec Draft 검토", "",
           "기호: `+` 새로 씀 · `~` 갱신 · `=` 사람 값 유지 · `-` 사람이 지운 필드 존중 · `↓` 양보. "
           "고칠 곳은 `specs/<crop_id>.json` — `_hint` 를 쓰면 재초안, `_accepted: true` 로 확정.", ""]
    for ctx in ctxs:
        cid = ctx.crop_id
        res, chk, d = merges.get(cid), checks.get(cid), drafts.get(cid)
        out += [f"## {cid} {ctx.name}", ""]
        if ctx.spec_error or ctx.skip_reason:
            out += [f"- 건너뜀: {ctx.spec_error or ctx.skip_reason}", ""]
            continue
        state = "변경 없음"
        if res is not None and res.changed:
            state = "변경 있음" + (" (렌더 바뀜 → Prepare 새 cell_key)" if res.render_changed else "") + \
                    ("" if res.spec is not None else " — 쓰지 않음")
        elif res is not None and any(n.startswith("쓰지 않음") for n in res.notes):
            state = "쓰지 않음(아래 메모)"
        out.append(f"- 상태: {state}")
        if d is not None:
            out.append("- 타깃: " + " · ".join(f"{tid} {_CONF_KO.get(rv.get('confidence'), '?')}"
                                              for tid, rv in d.review.items()))
            if d.suggestions.get("layer_name"):
                out.append(f"- 제안 레이어명: `{d.suggestions['layer_name']}`")
        flags = (list(d.flags) if d else []) + (list(chk.flags) if chk else []) + list(ctx.warnings)
        if d is not None:
            flags += [f"{tid} {f}" for tid, rv in d.review.items() for f in rv.get("flags") or []]
        if flags:
            out.append(f"- 플래그: {'; '.join(dict.fromkeys(flags))}")
        if res is not None and res.notes:
            out.append(f"- 메모: {'; '.join(res.notes)}")
        if chk is not None and chk.problems:
            out.append(f"- 렌더 점검: {'; '.join(chk.problems)}")
        out.append("")
        rows = [r for r in (res.rows if res else []) if r.kind != "unrendered"]
        if rows:
            out += ["| 경로 | 종류 | 이전 | 새 값 |", "|---|---|---|---|"]
            out += [f"| {r.path} | {_DIFF_MARK.get(r.kind, '')} {r.kind} | {_md(r.old) if r.old is not None else ''} | "
                    f"{_md(r.new) if r.new is not None else ''} |" for r in rows]
            out.append("")
        if d is not None:
            notes = [f"- {tid}: {rv['note']}" for tid, rv in d.review.items() if rv.get("note")]
            unr = [f"- {tid}.{f}: {_md(v)}" for tid, rv in d.review.items() for f, v in (rv.get("unrendered") or {}).items()]
            if notes:
                out += ["검토 메모:", *notes, ""]
            if unr:
                out += ["렌더되지 않는 값(_review.unrendered):", *unr, ""]
        if chk is not None and chk.v1:
            out += ["V1 프롬프트:", "", "```text", chk.v1, "```", ""]
    return "\n".join(out) + "\n"
