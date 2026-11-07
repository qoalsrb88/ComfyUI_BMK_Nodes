"""BMK Save Meta / BMK Save Image — 노드 ID 참조로 생성 정보를 모아 파일명·A1111 메타데이터·워크플로와 함께 저장한다.

배경:
Image Saver 같은 저장 노드는 seed·steps·sampler 같은 값을 선으로 하나씩
받아야 메타데이터에 넣을 수 있다. 저장 지점이 늘수록 선이 엉킨다.
BMK NAI Autosave Name 이 NAI 쪽에서 해 주던 일(파일명 + 워크플로 + 생성 정보를
한 파일에)을 일반 ComfyUI 저장에도 하되, 값은 선 대신 노드 ID 로 가리킨다.

구성:
  [BMK Save Meta] --meta--> [BMK Save Image]   (Meta 하나를 여러 Save 가 함께 쓴다)
  - Meta: refs(무엇을 기록할지) + 파일명 템플릿·경로·LoRA 표기 같은 설정.
  - Save: images, meta, postfix. 저장 지점마다 다른 건 postfix 와 이미지 크기뿐이다.
  - Meta 의 meta_in 에 다른 Meta 를 이으면 앞 Meta 의 값을 물려받고 이 노드에서
    얻은 값만 덮어쓴다(예: 하이레스 패스에서 denoise·steps 만 교체). 설정은 이 노드 것.

refs 문법 (한 줄에 하나, # 은 주석, 줄 순서 = 커스텀 항목 기록 순서):
  키: 노드ID            키마다 정해진 위젯을 찾는다(아래 표).
  키: 노드ID.이름       그 노드의 입력(위젯) 이름, 없으면 출력 이름.
  키: "값"              리터럴.
  ksampler: 노드ID      KSampler(또는 SamplerCustom 계열) 하나로 seed·steps·cfg·
                        sampler·scheduler·denoise·positive·negative·model·loras·te·vae 를
                        한꺼번에 채운다. 따로 적은 키가 이긴다.
  서브그래프 안 Meta 는 같은 서브그래프의 노드 번호를 먼저 찾는다("12:5" 처럼 직접 써도 된다).

  표준 키      노드 ID 만 주었을 때 찾는 곳 (왼쪽부터)
  ("STRING 출력" 등은 키와 같은 이름의 출력, 없으면 그 타입 출력이 하나뿐일 때)
  positive     STRING 출력(프롬프트 노드) / positive 컨디셔닝을 거슬러 올라가 처음 만나는 text 입력
               (FluxGuidance·ControlNet 같은 중간 노드, guider, 리터럴 스위치를 지나간다)
  negative     STRING 출력 / 같은 방식의 negative 체인(BasicGuider 의 conditioning 은 따라가지 않고,
               ConditioningZeroOut 에서 멈춘다)
  model        모델 링크를 한 줄로 거슬러 올라가 unet_name · ckpt_name. switch 가 리터럴인 스위치는
               고른 갈래, 그 밖엔 model 입력 또는 하나뿐인 MODEL 입력. 정할 수 없으면 멈춘다.
  loras        같은 길에서 만나는 LoraLoader(lora_name·strength), loaded_loras 출력(LoraManager 로더),
               LORA_STACK 출력(스태커). 쉼표로 여러 노드를 줄 수 있다.
  te           (별칭 text_encoder) clip_name 또는 clip_name1~4. 샘플러는 positive 체인에서 처음 만나는
               clip 입력(인코더)에서 CLIP 링크를 거슬러 올라간다. 여러 이름은 폴더·확장자를 떼고 " + " 로 잇는다.
  vae          vae_name 또는 VAE 링크를 거슬러 올라간 로더. 샘플러는 출력을 samples 로 받는
               디코더의 vae 를 찾는다. 여러 디코더의 이름이 다르거나 링크면 추측하지 않는다.
               te·vae 모두 리터럴 스위치의 고른 갈래만 따라가며 체크포인트 안의 이름은 생략한다.
  seed         INT 출력(시드 노드) / seed / noise_seed / noise → noise_seed
  steps        INT 출력 / steps / sigmas → steps
  cfg          FLOAT 출력 / cfg / guider → cfg
  sampler      sampler_name / sampler → sampler_name
  scheduler    scheduler / sigmas → scheduler
  denoise      FLOAT 출력 / denoise / sigmas → denoise
  위에서 못 찾으면 출력이 하나뿐인 노드(Primitive 등)의 그 출력을 쓴다.
  그 밖의 키는 커스텀 항목이다. 노드 ID 만 주면 키 이름과 같은 입력, 없으면 출력이 하나뿐인
  노드의 그 출력을 쓴다. A1111 줄 끝에 "키: 값" 으로, 파일명에는 %키 토큰으로 들어간다.

참조 해석 — 큐 직전 링크 컴파일:
  PromptServer 의 on_prompt 핸들러가 /prompt 요청을 검증 전에 고친다. Meta 노드의
  refs 를 읽어, 가리킨 입력이 리터럴이면 그 값을, 링크(랜덤 시드 노드·와일드카드
  노드 출력 등)면 그 링크를 Meta 노드의 숨은 입력(bmkref|키|번호|부분)으로 넣는다.
  - 링크는 실행기가 실제 실행 결과로 풀어 준다. 위젯 값을 읽는 방식(Widget To
    String, 프론트엔드 %Node.widget%)은 연결된 값을 못 읽는다.
  - 숨은 입력은 Meta 노드의 캐시 서명에 들어가므로 참조한 값이 바뀌면 Meta 가
    다시 실행되고, 그대로면 캐시를 쓴다. IS_CHANGED 가 필요 없다.
  - 선언하지 않은 입력을 받으려고 V3 스키마의 accept_all_inputs 를 쓴다.
  - 서버에서 도는 일이라 UI 없이 API 로 큐에 넣어도 동작한다.
  - 뮤트·바이패스된 노드는 API 프롬프트에서 빠지므로 그 항목은 비고 경고만 남는다.
  - 표준 키의 유선 소켓에 선을 이으면 참조보다 우선하고, 그 키의 참조는 아예 넣지 않는다
    (쓰지 않을 노드를 실행하지 않게). te·vae 는 refs 전용이며 기존 10개 소켓은 그대로다.
  - POST /bmk/save_meta/resolve 는 같은 컴파일 함수로 줄별 해석 결과를 미리 보여 준다.
    실행 후 ui.bmk_save_meta[0] 에 최종 표시 문자열(values)과 해석 못 한 줄(missing)을 보낸다.

파일명 토큰 (BMK NAI Autosave Name 과 같은 문법 — 끝에 % 가 없다):
  %time %model %vae %te %steps %cfg %sampler %scheduler %seed %denoise
  %width %height (저장하는 이미지의 실제 크기) %postfix (Save 노드 입력) %커스텀키
  - 긴 토큰부터 치환한다(%S 가 %Sft 를 먹지 않게).
  - 토큰이 모두 빈 값인 [...] 묶음은 앞의 구분자(_ - 공백)와 함께 지운다.
  - 템플릿의 / 는 하위 폴더. 값 안의 / 는 _ 로 바꾼다. 금지문자도 _ 로 바꾼다.
  - 같은 이름이 있으면 _02 부터 번호를 붙인다.
  - %time 과 파일 확인은 Save 실행 시점에 한다. Meta 는 캐시될 수 있기 때문이다
    (NAI Autosave 설계 메모 5번과 같은 이유). 워크플로도 Save 의 hidden 입력으로 받는다.

A1111 parameters:
  긍정 프롬프트, LoRA 태그(none / one line / one per line), Negative prompt:,
  Steps, Sampler, Schedule type, CFG scale, Seed, Size, Model, VAE, Text encoder, Denoising strength
  (1 이 아닐 때), 커스텀 항목, Version: ComfyUI. 값에 , : 줄바꿈이 있으면 A1111 처럼
  큰따옴표로 감싼다. 프롬프트에 이미 있는 <lora:이름:...> 은 다시 붙이지 않는다.
  sampler·scheduler 는 ComfyUI 이름 그대로 쓴다(er_sde 처럼 A1111 대응이 없는 것이 많다).

제약:
- PNG 만 저장한다. WebP/JPEG(EXIF), 모델·LoRA 해시, Civitai resources 는 다음 단계.
- refs 를 다른 노드의 출력으로 연결하면 큐 시점에 내용을 알 수 없어 해석하지 않는다.
- --disable-metadata 면 코어 SaveImage 처럼 텍스트 청크를 하나도 넣지 않는다.

버전 이력:
- v1 (2026-11): 최초. on_prompt 링크 컴파일, ksampler 묶음, model·loras 체인 추적,
      meta_in 체인, 빈 묶음 제거, A1111 parameters + 워크플로 임베드.
- v1.1 (2026-11): 노드 ID 만 준 키가 PrimitiveStringMultiline(입력 이름 value)·Lora Stacker 를
      못 찾고, model 체인이 ComfySwitchNode(on_true/on_false)에서 끊기던 문제. 키 타입의 출력을
      먼저 보고, 리터럴 스위치는 고른 갈래로 건너가며, LORA_STACK 출력도 LoRA 로 읽는다.
- v1.2 (2026-11): te·vae 참조와 파일명·A1111 표기, 줄별 해석 미리보기 라우트,
      실행 후 최종 값·미해석 줄 UI 메시지. 기존 소켓과 위젯 순서는 유지한다.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from typing import Any

import numpy as np
from aiohttp import web
from PIL import Image
from PIL.PngImagePlugin import PngInfo

import folder_paths
import nodes
from comfy.cli_args import args
from comfy_api.latest import io
from comfy_execution.graph_utils import is_link

logger = logging.getLogger(__name__)
_TAG = "[ComfyUI_BMK_Nodes::SaveMeta]"


# ─── 상수 ────────────────────────────────────────────────────────

_META_NODE_ID = "BMKSaveMeta"
_MetaType = io.Custom("BMK_SAVE_META")

# 컴파일러가 Meta 노드에 넣는 숨은 입력의 접두사: bmkref|키|번호|부분
_REF_PREFIX = "bmkref|"

_DEFAULT_TEMPLATE = (
    "[%time]_[%model]_[step%steps]_[cfg%cfg]_[%sampler]_[%scheduler]_"
    "[seed%seed]_[%widthx%height]_[%postfix]"
)
_DEFAULT_TIME_FORMAT = "%y%m%d-%H%M%S"
_LORA_MODES = ("none", "one line", "one per line")

_WIRED_KEYS = (
    "positive", "negative", "model", "loras",
    "seed", "steps", "cfg", "sampler", "scheduler", "denoise",
)
_STANDARD_KEYS = (*_WIRED_KEYS, "te", "vae")
_KEY_ALIASES = {"prompt": "positive", "lora": "loras", "text_encoder": "te"}
_BUNDLE_KEY = "ksampler"
# Save 노드가 채우는 토큰. 같은 이름의 커스텀 키는 받지 않는다.
_RESERVED_KEYS = ("time", "width", "height", "postfix")

# 노드 ID 만 주었을 때의 입력 경로. 마지막 이름이 값이고 그 앞은 따라갈 링크 입력이다.
_FIELD_PATHS = {
    "seed": (("seed",), ("noise_seed",), ("noise", "noise_seed")),
    "steps": (("steps",), ("sigmas", "steps")),
    "cfg": (("cfg",), ("guider", "cfg")),
    "sampler": (("sampler_name",), ("sampler", "sampler_name")),
    "scheduler": (("scheduler",), ("sigmas", "scheduler")),
    "denoise": (("denoise",), ("sigmas", "denoise")),
}
# positive·negative 는 경로 대신 컨디셔닝 체인을 거슬러 올라가 text 입력을 찾는다(_text_source).
_PROMPT_KEYS = ("positive", "negative")
# 체크포인트 겸용 로더(Efficient Loader 등)가 vae_name 에 넣는 "내장 VAE" 표시.
_EMBEDDED_NAMES = ("baked vae", "none")
# 가리킨 노드가 이 타입의 값을 내보내면(텍스트·시드 노드) 입력 경로보다 그 출력을 먼저 쓴다.
_KEY_TYPES = {"positive": "STRING", "negative": "STRING", "seed": "INT", "steps": "INT",
              "cfg": "FLOAT", "denoise": "FLOAT"}
_SCALAR_TYPES = ("STRING", "INT", "FLOAT", "BOOLEAN", "COMBO")
_MODEL_NAME_INPUTS = ("unet_name", "ckpt_name")
_CLIP_NAME_INPUTS = ("clip_name", "clip_name1", "clip_name2", "clip_name3", "clip_name4")
_MODEL_EXTS = (".safetensors", ".sft", ".ckpt", ".pt", ".pth", ".bin", ".gguf")
_MAX_CHAIN = 64

_FORBIDDEN_CHARS = re.compile(r'[<>:"|?*\x00-\x1f]')
_RESERVED_NAMES = frozenset(
    ["CON", "PRN", "AUX", "NUL"] + [f"{name}{i}" for name in ("COM", "LPT") for i in range(1, 10)]
)
_MAX_SEGMENT = 200
_ID_SPEC = re.compile(r"^(\d+(?::\d+)*)(?:\.(.+))?$")
_LITERAL_SPEC = re.compile(r'^"(.*)"\s*(?:#.*)?$')
_LORA_TAG = re.compile(r"<lora:([^:>]+):([^:>]+)(?::([^:>]+))?>", re.IGNORECASE)

_DEFAULT_SETTINGS = {
    "filename": _DEFAULT_TEMPLATE,
    "path": "",
    "time_format": _DEFAULT_TIME_FORMAT,
    "loras_in_prompt": "one line",
    "a1111_metadata": True,
    "embed_workflow": True,
}


# ─── refs 파싱 ────────────────────────────────────────────────────

def _parse_specs(body: str, multi: bool):
    """스펙 목록을 돌려준다. 읽을 수 없으면 None.

    스펙은 ("lit", 값) 또는 ("ref", 노드ID, 이름|None).
    """
    literal = _LITERAL_SPEC.match(body)
    if literal:
        return [("lit", literal.group(1))]
    body = body.split("#", 1)[0].strip()
    parts = body.split(",") if multi else [body]
    specs = []
    for part in parts:
        match = _ID_SPEC.match(part.strip())
        if not match:
            return None
        specs.append(("ref", match.group(1), match.group(2)))
    return specs


def _parse_ref_lines(text: str):
    """빈 줄·주석을 빼고 원래 순서로 낸다. 문법 오류의 스펙은 None, 커스텀 키는 대소문자를 살린다."""
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        label, sep, body = line.partition(":")
        label = label.strip()
        body = body.strip()
        key = _KEY_ALIASES.get(label.lower(), label.lower())
        if key not in _STANDARD_KEYS and key != _BUNDLE_KEY:
            key = label
        specs = _parse_specs(body, multi=key == "loras") if sep and label and body else None
        if key.lower() in _RESERVED_KEYS:
            specs = None
        yield key if sep and label else None, specs, line


def _parse_refs(text: str):
    """refs 를 [(키, 스펙 목록, 원래 줄)] 과 읽지 못한 줄 목록으로 나눈다."""
    lines = []
    errors = []
    for key, specs, line in _parse_ref_lines(text):
        if specs is None:
            errors.append(line)
        else:
            lines.append((key, specs, line))
    return lines, errors


# ─── 큐 직전 링크 컴파일 (on_prompt) ──────────────────────────────

def _output_names(class_type: Any) -> tuple:
    """화면에 보이는 출력 이름. V1 노드는 RETURN_NAMES 가 없으면 RETURN_TYPES 가 이름이다."""
    cls = nodes.NODE_CLASS_MAPPINGS.get(class_type)
    if cls is None:
        return ()
    return tuple(getattr(cls, "RETURN_NAMES", None) or cls.RETURN_TYPES or ())


def _output_types(class_type: Any) -> tuple:
    cls = nodes.NODE_CLASS_MAPPINGS.get(class_type)
    return tuple(cls.RETURN_TYPES or ()) if cls is not None else ()


def _link_type(prompt: dict, link: list):
    types = _output_types(prompt[link[0]].get("class_type")) if link[0] in prompt else ()
    index = int(link[1])
    return types[index] if index < len(types) else None


def _typed_output(prompt: dict, node_id: str, key: str):
    """키 이름과 같은 이름의 출력, 없으면 키 타입의 출력이 하나뿐일 때 그 출력.

    seed·steps·cfg·denoise 를 모두 내보내는 컨텍스트 노드에서 첫 INT·FLOAT 를 잡지 않게 한다.
    """
    wanted = _KEY_TYPES.get(key)
    if wanted is None:
        return None
    class_type = prompt[node_id].get("class_type")
    types = _output_types(class_type)
    for index, (name, kind) in enumerate(zip(_output_names(class_type), types)):
        if kind == wanted and str(name).lower() == key:
            return [node_id, index]
    matches = [index for index, kind in enumerate(types) if kind == wanted]
    return [node_id, matches[0]] if len(matches) == 1 else None


def _sole_scalar_output(prompt: dict, node_id: str):
    """출력이 하나뿐이고 문자열·숫자 같은 값이면 그 출력(Primitive 노드 등)."""
    types = _output_types(prompt[node_id].get("class_type"))
    if len(types) == 1 and (isinstance(types[0], list) or types[0] in _SCALAR_TYPES):
        return [node_id, 0]
    return None


def _find_node(prompt: dict, scope: str, ref_id: str):
    if scope and scope + ref_id in prompt:
        return scope + ref_id
    return ref_id if ref_id in prompt else None


def _inputs(prompt: dict, node_id: str) -> dict:
    return prompt[node_id].get("inputs") or {}


def _named_source(prompt: dict, node_id: str, name: str):
    """입력(위젯) 이름 → 그 값, 없으면 출력 이름 → 링크. 대소문자 정확 일치를 먼저 본다."""
    inputs = _inputs(prompt, node_id)
    outputs = _output_names(prompt[node_id].get("class_type"))
    for exact in (True, False):
        for key in inputs:
            if key == name if exact else key.lower() == name.lower():
                return inputs[key]
        for index, out in enumerate(outputs):
            if out == name if exact else str(out).lower() == name.lower():
                return [node_id, index]
    return None


def _path_source(prompt: dict, node_id: str, paths: tuple):
    for path in paths:
        current = node_id
        for link_name in path[:-1]:
            link = _inputs(prompt, current).get(link_name)
            if not is_link(link) or link[0] not in prompt:
                break
            current = link[0]
        else:
            inputs = _inputs(prompt, current)
            if path[-1] in inputs:
                return inputs[path[-1]]
    return None


def _next_conditioning_link(prompt: dict, node_id: str, polarity: str, strict: bool):
    """컨디셔닝을 거슬러 올라갈 (링크, 입력 이름). 정할 수 없으면 (None, None).

    리터럴 스위치는 고른 갈래, 그 밖엔 polarity(positive/negative) 입력 → conditioning → guider →
    CONDITIONING 타입 입력이 딱 하나일 때 그것. strict(negative 로 가이더에 막 들어옴)면 negative 입력만 —
    BasicGuider 의 conditioning 은 positive 라서 negative 로 따라가면 안 된다.
    """
    inputs = _inputs(prompt, node_id)
    if "switch" in inputs and ("on_true" in inputs or "on_false" in inputs):
        switch = inputs["switch"]
        chosen = inputs.get("on_true" if switch else "on_false") if isinstance(switch, bool) else None
        return (chosen, "switch") if is_link(chosen) else (None, None)
    if is_link(inputs.get(polarity)):
        return inputs[polarity], polarity
    if strict:
        return None, None
    for name in ("conditioning", "guider"):
        if is_link(inputs.get(name)):
            return inputs[name], name
    candidates = [link for link in inputs.values() if is_link(link) and _link_type(prompt, link) == "CONDITIONING"]
    return (candidates[0], "conditioning") if len(candidates) == 1 else (None, None)


def _conditioning_chain(prompt: dict, node_id: str, polarity: str):
    """샘플러·가이더에서 polarity 컨디셔닝을 거슬러 올라가며 노드 ID 를 낸다(시작 노드 포함).

    FluxGuidance·ControlNetApply·ReferenceLatent 처럼 인코더와 샘플러 사이에 낀 노드를 지나간다.
    ConditioningZeroOut 에서는 멈춘다(0으로 만든 컨디셔닝에는 기록할 텍스트가 없다).
    """
    seen = set()
    strict = False
    while node_id in prompt and node_id not in seen and len(seen) < _MAX_CHAIN:
        seen.add(node_id)
        yield node_id
        if prompt[node_id].get("class_type") == "ConditioningZeroOut":
            return
        link, name = _next_conditioning_link(prompt, node_id, polarity, strict)
        if link is None:
            return
        strict = name == "guider" and polarity == "negative"
        node_id = link[0]


def _text_source(prompt: dict, node_id: str, polarity: str):
    for current in _conditioning_chain(prompt, node_id, polarity):
        inputs = _inputs(prompt, current)
        if "text" in inputs:
            return inputs["text"]
    return None


def _field_source(prompt: dict, node_id: str, key: str):
    """노드 ID 만 준 표준 키: 키 타입의 출력 → 입력 경로(프롬프트는 컨디셔닝 체인) → 하나뿐인 스칼라 출력."""
    source = _typed_output(prompt, node_id, key)
    if source is None:
        source = (_text_source(prompt, node_id, key) if key in _PROMPT_KEYS
                  else _path_source(prompt, node_id, _FIELD_PATHS[key]))
    if source is None:
        source = _sole_scalar_output(prompt, node_id)
    return source


def _next_model_link(prompt: dict, node_id: str):
    """이 노드가 받은 모델을 거슬러 올라갈 링크 하나. 하나로 정할 수 없으면 None(멈춤).

    - switch 가 리터럴인 스위치(on_true·on_false)는 고른 갈래. 안쪽 스위치 출력은 MODEL 로
      선언되지 않으므로 타입을 보지 않고 따라간다.
    - 그 밖에는 model 입력, 없으면 MODEL 타입 입력이 딱 하나일 때 그것.
    조건이 링크인 스위치나 MODEL 입력이 여럿인 노드에서 갈래를 다 따라가면 실행되지 않을
    로더까지 링크로 끌어와 실행시키고 쓰지 않은 LoRA 를 기록하게 된다.
    """
    inputs = _inputs(prompt, node_id)
    switch = inputs.get("switch")
    if isinstance(switch, bool) and ("on_true" in inputs or "on_false" in inputs):
        chosen = inputs.get("on_true" if switch else "on_false")
        return chosen if is_link(chosen) else None
    if is_link(inputs.get("model")):
        return inputs["model"]
    candidates = [link for link in inputs.values() if is_link(link) and _link_type(prompt, link) == "MODEL"]
    return candidates[0] if len(candidates) == 1 else None


def _model_chain(prompt: dict, node_id: str):
    """node_id 에서 모델을 거슬러 올라가며 노드 ID 를 낸다(_next_model_link 를 따라 한 줄로).

    SamplerCustomAdvanced 처럼 model 입력이 없고 guider 가 있으면 guider 에서 시작한다.
    """
    guider = _inputs(prompt, node_id).get("guider")
    if "model" not in _inputs(prompt, node_id) and is_link(guider):
        node_id = guider[0]
    seen = set()
    while node_id in prompt and node_id not in seen and len(seen) < _MAX_CHAIN:
        seen.add(node_id)
        yield node_id
        link = _next_model_link(prompt, node_id)
        if link is None:
            return
        node_id = link[0]


def _model_source(prompt: dict, node_id: str):
    for current in _model_chain(prompt, node_id):
        inputs = _inputs(prompt, current)
        for name in _MODEL_NAME_INPUTS:
            if name in inputs:
                return inputs[name]
    return None


def _next_component_link(prompt: dict, node_id: str, component: str):
    """CLIP·VAE 체인에서 고른 갈래, 같은 이름 입력, 유일한 타입 입력 순으로 찾는다."""
    inputs = _inputs(prompt, node_id)
    if "switch" in inputs and ("on_true" in inputs or "on_false" in inputs):
        switch = inputs["switch"]
        if not isinstance(switch, bool):
            return None
        chosen = inputs.get("on_true" if switch else "on_false")
        return chosen if is_link(chosen) else None
    if is_link(inputs.get(component)):
        return inputs[component]
    candidates = [link for link in inputs.values()
                  if is_link(link) and _link_type(prompt, link) == component.upper()]
    return candidates[0] if len(candidates) == 1 else None


def _component_sources(prompt: dict, node_id: str, component: str) -> list:
    """체인을 따라 로더의 이름 입력들을 찾는다. 체크포인트 내장 이름은 기록하지 않는다."""
    names = _CLIP_NAME_INPUTS if component == "clip" else ("vae_name",)
    seen = set()
    while node_id in prompt and node_id not in seen and len(seen) < _MAX_CHAIN:
        seen.add(node_id)
        inputs = _inputs(prompt, node_id)
        found = [inputs[name] for name in names if name in inputs]
        if "ckpt_name" in inputs:
            return [value for value in found if not (isinstance(value, str) and value.lower() in _EMBEDDED_NAMES)]
        if found:
            return found
        link = _next_component_link(prompt, node_id, component)
        if link is None:
            break
        node_id = link[0]
    return []


def _te_sources(prompt: dict, node_id: str) -> list:
    """positive 컨디셔닝을 거슬러 올라가 처음 만나는 CLIP 입력(또는 clip_name) 노드에서 CLIP 체인을 탄다."""
    for current in _conditioning_chain(prompt, node_id, "positive"):
        inputs = _inputs(prompt, current)
        if is_link(inputs.get("clip")) or any(name in inputs for name in _CLIP_NAME_INPUTS):
            return _component_sources(prompt, current, "clip")
    return []


def _vae_source(prompt: dict, node_id: str):
    inputs = _inputs(prompt, node_id)
    if ("vae_name" not in inputs and "vae" not in inputs
            and "LATENT" in _output_types(prompt[node_id].get("class_type"))):
        sources = []
        for decoder in prompt:
            decoder_inputs = _inputs(prompt, decoder)
            samples = decoder_inputs.get("samples")
            if (is_link(samples) and samples[0] == node_id and _link_type(prompt, samples) == "LATENT"
                    and "vae" in decoder_inputs):
                found = _component_sources(prompt, decoder, "vae")
                sources.append(found[0] if found else None)
        if not sources:
            return None
        # 링크는 실행 전에 비교할 수 없고, 미해석 디코더도 일치한다고 보지 않는다.
        if len(sources) > 1 and (not all(isinstance(source, str) for source in sources)
                                 or len(set(sources)) != 1):
            return None
        return sources[0]
    sources = _component_sources(prompt, node_id, "vae")
    return sources[0] if sources else None


def _lora_sources(prompt: dict, node_id: str) -> list:
    """model 체인의 LoRA 를 적용 순서(로더 쪽 먼저)로 [(노드ID, {부분: 소스})] 로 모은다.

    LoraManager 로더의 loaded_loras 는 받은 lora_stack 까지 담고, 스태커의 LORA_STACK 은
    위쪽 스태커까지 담는다(active_loras 는 그 노드 몫뿐이라 쓰지 않는다).
    """
    found = []
    for current in _model_chain(prompt, node_id):
        inputs = _inputs(prompt, current)
        if "lora_name" in inputs:
            parts = {"name": inputs["lora_name"]}
            if "strength_model" in inputs:
                parts["weight"] = inputs["strength_model"]
            if "strength_clip" in inputs:
                parts["clip"] = inputs["strength_clip"]
            found.append((current, parts))
            continue
        outputs = _output_names(prompt[current].get("class_type"))
        types = _output_types(prompt[current].get("class_type"))
        if "loaded_loras" in outputs:
            found.append((current, {"value": [current, outputs.index("loaded_loras")]}))
        elif "LORA_STACK" in types:
            found.append((current, {"value": [current, types.index("LORA_STACK")]}))
    found.reverse()
    return found


def _usable_source(prompt: dict, value: Any) -> bool:
    """스칼라 리터럴과, 프롬프트에 있는 노드로 가는 링크만 주입한다.

    리스트 리터럴은 링크로 오인될 수 있고, 없는 노드로 가는 링크는 Meta 실행을 깨뜨린다.
    다른 Meta 를 가리키는 링크는 meta_in 체인과 사이클을 만들 수 있어 넣지 않는다(값도 dict 라 쓸 데가 없다).
    """
    if is_link(value):
        return value[0] in prompt and prompt[value[0]].get("class_type") != _META_NODE_ID
    return isinstance(value, (str, int, float, bool))


def _add_slots(slots: dict, key: str, specs: list, prompt: dict, scope: str) -> None:
    lora_nodes = set()
    index = 0
    for spec in specs:
        groups = []
        if spec[0] == "lit":
            groups.append({"value": spec[1]})
        else:
            node_id = _find_node(prompt, scope, spec[1])
            if node_id is None:
                continue
            if spec[2] is not None:
                groups.append({"value": _named_source(prompt, node_id, spec[2])})
            elif key == "loras":
                for lora_node, parts in _lora_sources(prompt, node_id):
                    if lora_node not in lora_nodes:
                        lora_nodes.add(lora_node)
                        groups.append(parts)
            elif key == "model":
                groups.append({"value": _model_source(prompt, node_id)})
            elif key == "te":
                groups.extend({"value": source} for source in _te_sources(prompt, node_id))
            elif key == "vae":
                groups.append({"value": _vae_source(prompt, node_id)})
            elif key in _FIELD_PATHS or key in _PROMPT_KEYS:
                groups.append({"value": _field_source(prompt, node_id, key)})
            else:
                source = _named_source(prompt, node_id, key)
                groups.append({"value": _sole_scalar_output(prompt, node_id) if source is None else source})
        for parts in groups:
            usable = {part: value for part, value in parts.items() if _usable_source(prompt, value)}
            if "value" not in usable and "name" not in usable:
                continue
            for part, value in usable.items():
                slots[f"{_REF_PREFIX}{key}|{index}|{part}"] = list(value) if is_link(value) else value
            index += 1


def _resolve_ref_lines(text: str, prompt: dict, scope: str, wired: set) -> list:
    """컴파일과 미리보기가 공유하는 줄별 결과. 유선·명시 키 우선순위도 여기서 정한다."""
    lines = list(_parse_ref_lines(text))
    explicit = {key for key, specs, _ in lines if specs is not None}
    results = []
    for key, specs, line in lines:
        slots = {}
        if specs is None:
            status = "invalid"
        elif key in wired:
            status = "wired"
        elif key != _BUNDLE_KEY:
            _add_slots(slots, key, specs, prompt, scope)
            status = "ok" if slots else "missing"
        else:
            bare = [spec for spec in specs if spec[0] == "ref" and spec[2] is None]
            for field in _STANDARD_KEYS:
                if field not in explicit and field not in wired:
                    _add_slots(slots, field, bare, prompt, scope)
            status = "ok" if slots else "missing"
        results.append({"line": line, "key": key, "status": status, "slots": slots})
    return results


def _compile_refs(text: str, prompt: dict, scope: str, wired: set) -> dict:
    """wired = 선이 이어진 표준 키. 유선이 이기므로 그 키의 참조는 의존성으로 넣지 않는다."""
    slots = {}
    for result in _resolve_ref_lines(text, prompt, scope, wired):
        slots.update(result["slots"])
    return slots


def compile_refs_in_prompt(json_data: dict) -> dict:
    """on_prompt 핸들러. Meta 노드마다 숨은 입력을 지우고 refs 로 다시 만든다(재큐잉해도 같다)."""
    prompt = json_data.get("prompt")
    if not isinstance(prompt, dict):
        return json_data
    for node_id, node in prompt.items():
        if not isinstance(node, dict) or node.get("class_type") != _META_NODE_ID:
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        for name in [name for name in inputs if name.startswith(_REF_PREFIX)]:
            del inputs[name]
        refs = inputs.get("refs")
        if isinstance(refs, str):
            head, sep, _ = str(node_id).rpartition(":")
            wired = {key for key in _WIRED_KEYS if key in inputs}
            inputs.update(_compile_refs(refs, prompt, head + sep, wired))
    return json_data


def _register_prompt_handler() -> None:
    """서버가 있을 때만(노드 모듈 import 시점) 핸들러를 건다. 단독 실행·테스트면 건너뛴다."""
    server = getattr(getattr(sys.modules.get("server"), "PromptServer", None), "instance", None)
    if server is not None:
        server.add_on_prompt_handler(compile_refs_in_prompt)


def resolve_refs(payload: dict) -> dict:
    """미리보기 응답을 순수 계산한다. 요청 프롬프트는 고치지 않는다(해석 결과는 별도 dict 에 모은다).

    해석 함수는 프론트가 만든 정상 프롬프트를 전제로 하므로, 깨진 노드·링크는 여기서 400 으로 거른다.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("prompt"), dict):
        raise ValueError("prompt 객체가 필요합니다.")
    prompt = payload["prompt"]
    for node in prompt.values():
        if (not isinstance(node, dict) or not isinstance(node.get("class_type"), str)
                or not isinstance(node.get("inputs", {}), dict)):
            raise ValueError("노드는 class_type(문자열)과 inputs(객체)를 가진 객체여야 합니다.")
        if any(is_link(value) and not isinstance(value[1], int) for value in node.get("inputs", {}).values()):
            raise ValueError("링크의 출력 번호는 정수여야 합니다.")
    node_id = payload.get("node_id")
    node = prompt.get(node_id) if isinstance(node_id, str) else None
    if not isinstance(node, dict) or node.get("class_type") != _META_NODE_ID:
        return {"node_found": False, "lines": []}
    inputs = _inputs(prompt, node_id)
    refs = payload.get("refs", inputs.get("refs", ""))
    if not isinstance(refs, str):
        return {"node_found": True, "lines": []}
    head, sep, _ = node_id.rpartition(":")
    wired = {key for key in _WIRED_KEYS if key in inputs}
    lines = []
    for result in _resolve_ref_lines(refs, prompt, head + sep, wired):
        fields = []
        for name, value in result["slots"].items():
            key, _, part = name[len(_REF_PREFIX):].rsplit("|", 2)
            field = {"key": key, "part": part}
            if is_link(value):
                source_id, index = value
                source = prompt[source_id]
                names = _output_names(source.get("class_type"))
                field.update(kind="link", node=source_id, class_type=source.get("class_type"),
                             title=(source.get("_meta") or {}).get("title"),
                             output=str(names[index]) if 0 <= index < len(names) else str(index))
            else:
                field.update(kind="literal", value=value)
            fields.append(field)
        lines.append({"line": result["line"], "key": result["key"], "status": result["status"],
                      "fields": fields})
    return {"node_found": True, "lines": lines}


async def _resolve_request(request):
    try:
        result = resolve_refs(await request.json())
    except ValueError as exc:
        return web.json_response({"error": f"요청 JSON을 해석할 수 없습니다: {exc}"}, status=400)
    return web.json_response(result)


def _register_routes() -> None:
    """서버가 있을 때만 해석 미리보기를 등록한다. 서버 모듈을 직접 불러오지 않는다."""
    if getattr(_register_routes, "_done", False):
        return
    server = getattr(getattr(sys.modules.get("server"), "PromptServer", None), "instance", None)
    routes = getattr(server, "routes", None)
    if routes is not None:
        routes.post("/bmk/save_meta/resolve")(_resolve_request)
        _register_routes._done = True


# ─── 값 정리 ─────────────────────────────────────────────────────

def _format_value(value: Any) -> str:
    """float 는 꼬리 0을 떼고, None 은 빈 문자열로 (NAI Autosave 와 같은 규칙)."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "on" if value else "off"
    if isinstance(value, float):
        text = f"{value:.4f}".rstrip("0").rstrip(".")
        return text or "0"
    return str(value)


def _model_display(name: str) -> str:
    """폴더와 모델 파일 확장자만 뗀다. style_v1.5 처럼 이름 속 점은 살린다."""
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    stem, ext = os.path.splitext(base)
    return stem if ext.lower() in _MODEL_EXTS else base


def _number(value: Any) -> Any:
    """<lora:..> 에서 읽은 강도 문자열을 숫자로. 숫자가 아니면 그대로 둔다."""
    if not isinstance(value, str):
        return value
    try:
        return float(value)
    except ValueError:
        return value


def _lora_entry(name: Any, weight: Any = 1.0, clip: Any = None) -> dict:
    """클립 강도가 모델 강도와 같으면 None 으로 둔다(LORA_STACK 과 <lora:이름:강도> 가 같은 항목이 되게)."""
    weight = _number(weight)
    clip = None if clip in (None, "") else _number(clip)
    return {"name": _model_display(str(name)), "weight": weight, "clip": None if clip == weight else clip}


def _loras_from_value(value: Any) -> list:
    """<lora:이름:강도[:클립]> 문자열(LoraManager loaded_loras 등)이나 LORA_STACK 리스트."""
    if isinstance(value, str):
        return [_lora_entry(name, weight, clip) for name, weight, clip in _LORA_TAG.findall(value)]
    if isinstance(value, (list, tuple)):
        return [_lora_entry(*item[:3]) for item in value if isinstance(item, (list, tuple)) and item]
    return []


def _scalar(value: Any):
    """메타데이터에 쓸 수 있는 값만 남긴다. 텐서·모델 객체 같은 것은 None."""
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)) and len(value) == 1 and isinstance(value[0], (str, int, float, bool)):
        return value[0]
    return None


def _assemble(key: str, groups: dict):
    if key == "loras":
        entries = []
        for _, parts in sorted(groups.items()):
            if "value" in parts:
                entries.extend(_loras_from_value(parts["value"]))
            elif "name" in parts:
                entries.append(_lora_entry(parts["name"], parts.get("weight", 1.0), parts.get("clip")))
        # 스태커와 그 스택을 받은 로더를 함께 적으면 같은 항목이 두 번 나온다. 강도가 다르면 별개 적용이라 둔다.
        unique = {}
        for entry in entries:
            unique.setdefault((entry["name"].lower(), entry["weight"], entry["clip"]), entry)
        return list(unique.values())
    if key == "te":
        names = [_scalar(parts.get("value")) for _, parts in sorted(groups.items())]
        names = [_model_display(name) for name in names if isinstance(name, str) and name]
        return " + ".join(names) if names else None
    value = _scalar(groups[min(groups)].get("value"))
    if key in ("model", "vae") and isinstance(value, str):
        return _model_display(value)
    return value


def _collect_values(refs: Any, kwargs: dict) -> tuple[dict, list]:
    """실행된 숨은 입력과 유선을 합치고, 경고에 나온 미해석 줄을 원래 순서로 돌려준다."""
    grouped: dict = {}
    for name, value in kwargs.items():
        if name.startswith(_REF_PREFIX):
            key, index, part = name[len(_REF_PREFIX):].rsplit("|", 2)
            grouped.setdefault(key, {}).setdefault(int(index), {})[part] = value

    lines, errors = _parse_refs(refs if isinstance(refs, str) else "")
    explicit = {key for key, _, _ in lines}
    values: dict = {}
    missing = []
    for key, _, line in lines:
        if key == _BUNDLE_KEY:
            wanted = [field for field in _STANDARD_KEYS if field not in explicit and kwargs.get(field) is None]
            for field in wanted:
                if field in grouped:
                    values[field] = _assemble(field, grouped[field])
            if wanted and not any(field in grouped for field in wanted):
                missing.append(line)
            continue
        if key in grouped:
            values[key] = _assemble(key, grouped[key])
        elif kwargs.get(key) is None:
            values[key] = None
            missing.append(line)

    if errors:
        logger.warning("%s 읽을 수 없는 refs 줄(키: 노드ID[.이름] 또는 키: \"값\"): %s", _TAG, " | ".join(errors))
    if missing:
        logger.warning("%s 해석하지 못한 참조 — 노드 ID·이름을 확인하세요(뮤트·바이패스 노드는 빠집니다): %s",
                       _TAG, " | ".join(missing))

    for key in _WIRED_KEYS:
        wired = kwargs.get(key)
        if wired is None:
            continue
        if key == "loras":
            values[key] = _loras_from_value(wired)
            continue
        value = _scalar(wired)
        if value is None:
            logger.warning("%s 연결된 %s 값은 문자열·숫자가 아니라 무시합니다.", _TAG, key)
            continue
        values[key] = _model_display(value) if key == "model" and isinstance(value, str) else value
    unresolved = set(errors + missing)
    return values, [line for _, _, line in _parse_ref_lines(refs if isinstance(refs, str) else "")
                    if line in unresolved]


# ─── 파일명 ───────────────────────────────────────────────────────

def _token_value(value: Any) -> str:
    return _format_value(value).replace("/", "_").replace("\\", "_")


def _filename_tokens(values: dict, stamp: str, width: int, height: int, postfix: str) -> dict:
    tokens = {"%" + key: "" for key in _STANDARD_KEYS if key not in ("positive", "negative", "loras")}
    for key, value in values.items():
        if key not in ("positive", "negative", "loras"):
            tokens["%" + key] = _token_value(value)
    tokens.update({
        "%time": stamp,
        "%width": str(width),
        "%height": str(height),
        "%postfix": _token_value(postfix),
    })
    return tokens


def _render_filename(template: str, tokens: dict) -> str:
    names = sorted(tokens, key=len, reverse=True)
    token_re = re.compile("|".join(map(re.escape, names)))

    def replace(match: re.Match) -> str:
        inner = match.group(1)
        if inner is None:
            return tokens[match.group(0)]
        hits = token_re.findall(inner)
        if hits and not any(tokens[hit] for hit in hits):
            return "\x00"
        return "[" + token_re.sub(lambda m: tokens[m.group(0)], inner) + "]"

    text = re.sub(r"\[([^\[\]]*)\]|" + token_re.pattern, replace, template)
    text = re.sub(r"(^|/)(?:\x00[_\- ]*)+", r"\1", text)
    return re.sub(r"(?:[_\- ]*\x00)+", "", text)


def _cap_segment(segment: str) -> str:
    """Windows 경로 요소 길이(UTF-16 단위)를 넘지 않게 자른다. 확장자·_02 자리를 남긴다."""
    segment = segment[:_MAX_SEGMENT]
    while len(segment.encode("utf-16-le")) // 2 > _MAX_SEGMENT:
        segment = segment[:-1]
    return segment.rstrip(". ")


def _sanitize(text: str) -> str:
    """금지문자를 _ 로, / 는 하위 폴더 구분자로 둔다. 빈 세그먼트와 . .. 은 버리고 CON 같은 예약 이름은 피한다."""
    parts = []
    for segment in text.replace("\\", "/").split("/"):
        segment = _cap_segment(_FORBIDDEN_CHARS.sub("_", segment).strip().rstrip(". "))
        if segment.split(".", 1)[0].upper() in _RESERVED_NAMES:
            segment = "_" + segment
        if segment:
            parts.append(segment)
    return "/".join(parts)


def _unique_path(folder: str, name: str) -> str:
    """같은 이름이 있으면 _02 부터 번호를 붙인다 (NAI Autosave 와 같은 규칙)."""
    target = os.path.join(folder, f"{name}.png")
    index = 2
    while os.path.exists(target):
        target = os.path.join(folder, f"{name}_{index:02d}.png")
        index += 1
    return target


# ─── A1111 parameters ─────────────────────────────────────────────

def _quote(text: str) -> str:
    if "," not in text and ":" not in text and "\n" not in text:
        return text
    return json.dumps(text, ensure_ascii=False)


def _lora_tag(entry: dict) -> str:
    weights = _format_value(entry["weight"])
    clip = entry.get("clip")
    if clip is not None and _format_value(clip) != weights:
        weights += ":" + _format_value(clip)
    return f"<lora:{entry['name']}:{weights}>"


def _a1111_text(values: dict, lora_mode: str, width: int, height: int) -> str:
    positive = _format_value(values.get("positive"))
    if lora_mode != "none":
        present = positive.lower()
        tags = [_lora_tag(entry) for entry in values.get("loras") or []
                if f"<lora:{entry['name'].lower()}:" not in present]
        if tags:
            joined = (", " if lora_mode == "one line" else "\n").join(tags)
            positive = f"{positive}\n{joined}" if positive else joined
    lines = [positive]
    negative = _format_value(values.get("negative"))
    if negative:
        lines.append(f"Negative prompt: {negative}")

    params = [
        ("Steps", values.get("steps")),
        ("Sampler", values.get("sampler")),
        ("Schedule type", values.get("scheduler")),
        ("CFG scale", values.get("cfg")),
        ("Seed", values.get("seed")),
        ("Size", f"{width}x{height}"),
        ("Model", values.get("model")),
        ("VAE", values.get("vae")),
        ("Text encoder", values.get("te")),
    ]
    denoise = _number(values.get("denoise"))
    if isinstance(denoise, (int, float)) and denoise != 1:
        params.append(("Denoising strength", denoise))
    params.extend((key, value) for key, value in values.items() if key not in _STANDARD_KEYS)
    params.append(("Version", "ComfyUI"))
    lines.append(", ".join(f"{label}: {_quote(_format_value(value))}"
                           for label, value in params if _format_value(value)))
    return "\n".join(lines)


# ─── 노드 ────────────────────────────────────────────────────────

_REFS_PLACEHOLDER = (
    "ksampler: 3        (KSampler ID 하나로 seed·steps·cfg·sampler·scheduler·denoise·프롬프트·모델·LoRA·te·vae)\n"
    "positive: 6        (키: 노드ID)\n"
    "Sft: 10.shift      (키: 노드ID.위젯 또는 출력 이름 — 표준 키가 아니면 커스텀 항목, 파일명 %Sft)\n"
    "Note: \"직접 쓴 값\""
)

_WIRED_TOOLTIPS = {
    "positive": "긍정 프롬프트 문자열. 연결하면 refs 보다 우선합니다.",
    "negative": "부정 프롬프트 문자열. 연결하면 refs 보다 우선합니다.",
    "model": "모델 이름 문자열(경로·확장자는 떼고 씁니다). 연결하면 refs 보다 우선합니다.",
    "loras": "<lora:이름:강도> 문자열 또는 LORA_STACK. 연결하면 refs 보다 우선합니다.",
}

_META_DESCRIPTION = (
    "노드 ID 참조(refs)로 프롬프트·샘플러 값·LoRA·커스텀 파라미터를 선 없이 모아 "
    "BMK Save Image 에 넘깁니다. 파일명 템플릿·저장 경로·A1111 메타데이터 설정도 여기서 정합니다."
)
_META_ALIASES = [
    "save meta", "metadata", "image saver metadata", "a1111", "filename template",
    "메타데이터", "파일명", "저장 설정",
]
_SAVE_DESCRIPTION = (
    "이미지를 템플릿 파일명으로 저장하고 A1111 parameters 와 워크플로를 PNG 에 함께 넣습니다. "
    "설정은 연결한 BMK Save Meta 를 따르고, 없으면 기본 템플릿으로 워크플로만 넣습니다."
)
_SAVE_ALIASES = ["save image", "image saver", "save png", "이미지 저장", "저장"]


class BMKSaveMeta(io.ComfyNode):
    # V3 노드에는 SEARCH_ALIASES classproperty 가 없어 규약 점검이 읽을 수 있도록 클래스 속성으로도 둔다.
    SEARCH_ALIASES = _META_ALIASES

    @classmethod
    def define_schema(cls) -> io.Schema:
        wired = [
            io.AnyType.Input(key, optional=True,
                             tooltip=_WIRED_TOOLTIPS.get(key, f"{key} 값. 연결하면 refs 보다 우선합니다."))
            for key in _WIRED_KEYS
        ]
        return io.Schema(
            node_id=_META_NODE_ID,
            display_name="BMK Save Meta 🏷️",
            category="BMK/Image",
            description=_META_DESCRIPTION,
            search_aliases=_META_ALIASES,
            accept_all_inputs=True,
            inputs=[
                io.String.Input(
                    "refs", multiline=True, default="", placeholder=_REFS_PLACEHOLDER,
                    tooltip="한 줄에 하나: '키: 노드ID', '키: 노드ID.이름', '키: \"값\"', 'ksampler: 노드ID'. "
                            "큐에 넣을 때 서버가 실제 값(연결된 값 포함)으로 바꿔 넣습니다.",
                ),
                io.String.Input(
                    "filename", default=_DEFAULT_TEMPLATE,
                    tooltip="파일명 템플릿. 토큰: %time %model %vae %te %steps %cfg %sampler %scheduler %seed "
                            "%denoise %width %height %postfix %커스텀키. / 는 하위 폴더, 값이 모두 빈 [...] 는 지워집니다.",
                ),
                io.String.Input("path", default="", tooltip="output 폴더 아래 하위 폴더. 비우면 output 바로 아래."),
                io.String.Input("time_format", default=_DEFAULT_TIME_FORMAT, tooltip="%time 에 쓰는 strftime 포맷."),
                io.Combo.Input(
                    "loras_in_prompt", options=list(_LORA_MODES), default="one line",
                    tooltip="A1111 메타데이터의 긍정 프롬프트 뒤에 LoRA 태그를 붙이는 방식.",
                ),
                io.Boolean.Input("a1111_metadata", default=True, tooltip="A1111 형식 parameters 청크를 넣습니다."),
                io.Boolean.Input("embed_workflow", default=True, tooltip="ComfyUI 워크플로(prompt/workflow)를 넣습니다."),
                _MetaType.Input("meta_in", optional=True,
                                tooltip="앞 BMK Save Meta 의 값을 물려받고, 이 노드에서 얻은 값만 덮어씁니다."),
                *wired,
            ],
            outputs=[_MetaType.Output(display_name="meta")],
        )

    @classmethod
    def execute(cls, refs, filename, path, time_format, loras_in_prompt, a1111_metadata, embed_workflow,
                meta_in=None, **kwargs) -> io.NodeOutput:
        values = dict(meta_in["values"]) if meta_in else {}
        collected, missing = _collect_values(refs, kwargs)
        for key, value in collected.items():
            if value is not None or key not in values:
                values[key] = value
        settings = {
            "filename": filename,
            "path": path,
            "time_format": time_format,
            "loras_in_prompt": loras_in_prompt,
            "a1111_metadata": a1111_metadata,
            "embed_workflow": embed_workflow,
        }
        displayed = {key: " ".join(_lora_tag(entry) for entry in value or []) if key == "loras"
                     else _format_value(value) for key, value in values.items()}
        return io.NodeOutput({"values": values, "settings": settings},
                             ui={"bmk_save_meta": [{"values": displayed, "missing": missing}]})


class BMKSaveImage(io.ComfyNode):
    SEARCH_ALIASES = _SAVE_ALIASES

    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="BMKSaveImage",
            display_name="BMK Save Image 💾",
            category="BMK/Image",
            description=_SAVE_DESCRIPTION,
            search_aliases=_SAVE_ALIASES,
            is_output_node=True,
            inputs=[
                io.Image.Input("images"),
                _MetaType.Input("meta", optional=True, tooltip="BMK Save Meta. 없으면 기본 템플릿을 씁니다."),
                io.String.Input("postfix", default="", tooltip="파일명 %postfix 토큰 값. 저장 지점을 구분할 때 씁니다."),
            ],
            outputs=[],
            hidden=[io.Hidden.prompt, io.Hidden.extra_pnginfo],
        )

    @classmethod
    def execute(cls, images, postfix="", meta=None) -> io.NodeOutput:
        values = meta["values"] if meta else {}
        settings = meta["settings"] if meta else _DEFAULT_SETTINGS
        height, width = images.shape[1], images.shape[2]

        stamp = time.strftime(settings["time_format"] or _DEFAULT_TIME_FORMAT)
        tokens = _filename_tokens(values, stamp, width, height, postfix)
        rendered = _sanitize(_render_filename(settings["filename"] or _DEFAULT_TEMPLATE, tokens))
        template_dir, _, name = rendered.rpartition("/")
        subfolder = "/".join(part for part in (_sanitize(settings["path"]), template_dir) if part)

        output_dir = os.path.abspath(folder_paths.get_output_directory())
        folder = os.path.abspath(os.path.join(output_dir, subfolder))
        if os.path.commonpath((output_dir, folder)) != output_dir:
            raise ValueError(f"{_TAG} output 폴더 밖에는 저장할 수 없습니다: {folder}")
        os.makedirs(folder, exist_ok=True)

        metadata = None
        if not args.disable_metadata:
            metadata = PngInfo()
            if settings["a1111_metadata"] and values:
                metadata.add_text("parameters", _a1111_text(values, settings["loras_in_prompt"], width, height))
            if settings["embed_workflow"]:
                if cls.hidden.prompt is not None:
                    metadata.add_text("prompt", json.dumps(cls.hidden.prompt))
                for key, value in (cls.hidden.extra_pnginfo or {}).items():
                    metadata.add_text(key, json.dumps(value))

        results = []
        for image in images:
            pixels = np.clip(255.0 * image.cpu().numpy(), 0, 255).astype(np.uint8)
            target = _unique_path(folder, name or "image")
            Image.fromarray(pixels).save(target, pnginfo=metadata, compress_level=4)
            results.append({"filename": os.path.basename(target), "subfolder": subfolder, "type": "output"})
        return io.NodeOutput(ui={"images": results})


_register_prompt_handler()
_register_routes()


NODE_CLASS_MAPPINGS = {
    "BMKSaveMeta": BMKSaveMeta,
    "BMKSaveImage": BMKSaveImage,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "BMKSaveMeta": "BMK Save Meta 🏷️",
    "BMKSaveImage": "BMK Save Image 💾",
}
