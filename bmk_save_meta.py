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
                        sampler·scheduler·denoise·positive·negative·model·loras 를
                        한꺼번에 채운다. 따로 적은 키가 이긴다.
  서브그래프 안 Meta 는 같은 서브그래프의 노드 번호를 먼저 찾는다("12:5" 처럼 직접 써도 된다).

  표준 키      노드 ID 만 주었을 때 찾는 곳
  positive     text  /  positive 링크 → text  /  guider → positive → text
  negative     text  /  negative 링크 → text  /  guider → negative → text
  model        model 링크를 따라 올라가 unet_name · ckpt_name
  loras        model 링크를 따라 올라가며 만나는 LoraLoader(lora_name·strength)와
               loaded_loras 출력(LoraManager). 쉼표로 여러 노드를 줄 수 있다.
  seed         seed / noise_seed / noise → noise_seed
  steps        steps / sigmas → steps
  cfg          cfg / guider → cfg
  sampler      sampler_name / sampler → sampler_name
  scheduler    scheduler / sigmas → scheduler
  denoise      denoise / sigmas → denoise
  그 밖의 키는 커스텀 항목이다. 노드 ID 만 주면 키 이름과 같은 입력을 찾는다.
  A1111 줄 끝에 "키: 값" 으로, 파일명에는 %키 토큰으로 들어간다.

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
  - 표준 키의 유선 소켓에 선을 이으면 참조보다 우선한다.

파일명 토큰 (BMK NAI Autosave Name 과 같은 문법 — 끝에 % 가 없다):
  %time %model %steps %cfg %sampler %scheduler %seed %denoise
  %width %height (저장하는 이미지의 실제 크기) %postfix (Save 노드 입력) %커스텀키
  - 긴 토큰부터 치환한다(%S 가 %Sft 를 먹지 않게).
  - 토큰이 모두 빈 값인 [...] 묶음은 앞의 구분자(_ - 공백)와 함께 지운다.
  - 템플릿의 / 는 하위 폴더. 값 안의 / 는 _ 로 바꾼다. 금지문자도 _ 로 바꾼다.
  - 같은 이름이 있으면 _02 부터 번호를 붙인다.
  - %time 과 파일 확인은 Save 실행 시점에 한다. Meta 는 캐시될 수 있기 때문이다
    (NAI Autosave 설계 메모 5번과 같은 이유). 워크플로도 Save 의 hidden 입력으로 받는다.

A1111 parameters:
  긍정 프롬프트, LoRA 태그(none / one line / one per line), Negative prompt:,
  Steps, Sampler, Schedule type, CFG scale, Seed, Size, Model, Denoising strength
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

_STANDARD_KEYS = (
    "positive", "negative", "model", "loras",
    "seed", "steps", "cfg", "sampler", "scheduler", "denoise",
)
_KEY_ALIASES = {"prompt": "positive", "lora": "loras"}
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
    "positive": (("text",), ("positive", "text"), ("guider", "positive", "text")),
    "negative": (("text",), ("negative", "text"), ("guider", "negative", "text")),
}
_MODEL_NAME_INPUTS = ("unet_name", "ckpt_name")
_MODEL_EXTS = (".safetensors", ".sft", ".ckpt", ".pt", ".pth", ".bin", ".gguf")
_MAX_CHAIN = 64

_FORBIDDEN_CHARS = re.compile(r'[<>:"|?*\x00-\x1f]')
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


def _parse_refs(text: str):
    """refs 를 [(키, 스펙 목록, 원래 줄)] 과 읽지 못한 줄 목록으로 나눈다.

    표준 키는 소문자로 맞추고 커스텀 키는 대소문자를 살린다.
    """
    lines = []
    errors = []
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
        if specs is None or key.lower() in _RESERVED_KEYS:
            errors.append(line)
            continue
        lines.append((key, specs, line))
    return lines, errors


# ─── 큐 직전 링크 컴파일 (on_prompt) ──────────────────────────────

def _output_names(class_type: Any) -> tuple:
    """화면에 보이는 출력 이름. V1 노드는 RETURN_NAMES 가 없으면 RETURN_TYPES 가 이름이다."""
    cls = nodes.NODE_CLASS_MAPPINGS.get(class_type)
    if cls is None:
        return ()
    return tuple(getattr(cls, "RETURN_NAMES", None) or cls.RETURN_TYPES or ())


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


def _model_chain(prompt: dict, node_id: str):
    """node_id 에서 model 입력 링크를 따라 올라가며 노드 ID 를 낸다.

    SamplerCustomAdvanced 처럼 model 입력이 없고 guider 가 있으면 guider 에서 시작한다.
    """
    guider = _inputs(prompt, node_id).get("guider")
    if "model" not in _inputs(prompt, node_id) and is_link(guider):
        node_id = guider[0]
    seen = set()
    while node_id in prompt and node_id not in seen and len(seen) < _MAX_CHAIN:
        seen.add(node_id)
        yield node_id
        link = _inputs(prompt, node_id).get("model")
        if not is_link(link):
            return
        node_id = link[0]


def _model_source(prompt: dict, node_id: str):
    for current in _model_chain(prompt, node_id):
        inputs = _inputs(prompt, current)
        for name in _MODEL_NAME_INPUTS:
            if name in inputs:
                return inputs[name]
    return None


def _lora_sources(prompt: dict, node_id: str) -> list:
    """model 체인의 LoRA 를 적용 순서(로더 쪽 먼저)로 [(노드ID, {부분: 소스})] 로 모은다."""
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
        if "loaded_loras" in outputs:
            found.append((current, {"value": [current, outputs.index("loaded_loras")]}))
    found.reverse()
    return found


def _usable_source(value: Any) -> bool:
    """링크이거나 스칼라 리터럴만 주입한다. 리스트 리터럴은 링크로 오인될 수 있다."""
    return is_link(value) or isinstance(value, (str, int, float, bool))


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
            elif key in _FIELD_PATHS:
                groups.append({"value": _path_source(prompt, node_id, _FIELD_PATHS[key])})
            else:
                groups.append({"value": _named_source(prompt, node_id, key)})
        for parts in groups:
            usable = {part: value for part, value in parts.items() if _usable_source(value)}
            if "value" not in usable and "name" not in usable:
                continue
            for part, value in usable.items():
                slots[f"{_REF_PREFIX}{key}|{index}|{part}"] = list(value) if is_link(value) else value
            index += 1


def _compile_refs(text: str, prompt: dict, scope: str) -> dict:
    lines, _ = _parse_refs(text)
    explicit = {key for key, _, _ in lines}
    slots: dict = {}
    for key, specs, _ in lines:
        if key != _BUNDLE_KEY:
            _add_slots(slots, key, specs, prompt, scope)
            continue
        bare = [spec for spec in specs if spec[0] == "ref" and spec[2] is None]
        for field in _STANDARD_KEYS:
            if field not in explicit:
                _add_slots(slots, field, bare, prompt, scope)
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
            inputs.update(_compile_refs(refs, prompt, head + sep))
    return json_data


def _register_prompt_handler() -> None:
    """서버가 있을 때만(노드 모듈 import 시점) 핸들러를 건다. 단독 실행·테스트면 건너뛴다."""
    server = getattr(getattr(sys.modules.get("server"), "PromptServer", None), "instance", None)
    if server is not None:
        server.add_on_prompt_handler(compile_refs_in_prompt)


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
    clip = None if clip in (None, "") else _number(clip)
    return {"name": _model_display(str(name)), "weight": _number(weight), "clip": clip}


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
        return entries
    value = _scalar(groups[min(groups)].get("value"))
    if key == "model" and isinstance(value, str):
        return _model_display(value)
    return value


def _collect_values(refs: Any, kwargs: dict) -> dict:
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
            for field in _STANDARD_KEYS:
                if field not in explicit and field in grouped:
                    values[field] = _assemble(field, grouped[field])
            continue
        if key in grouped:
            values[key] = _assemble(key, grouped[key])
        else:
            values[key] = None
            missing.append(line)

    if errors:
        logger.warning("%s 읽을 수 없는 refs 줄(키: 노드ID[.이름] 또는 키: \"값\"): %s", _TAG, " | ".join(errors))
    if missing:
        logger.warning("%s 해석하지 못한 참조 — 노드 ID·이름을 확인하세요(뮤트·바이패스 노드는 빠집니다): %s",
                       _TAG, " | ".join(missing))

    for key in _STANDARD_KEYS:
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
    return values


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


def _sanitize(text: str) -> str:
    """금지문자를 _ 로, / 는 하위 폴더 구분자로 둔다. 빈 세그먼트와 . .. 은 버린다."""
    parts = []
    for segment in text.replace("\\", "/").split("/"):
        segment = _FORBIDDEN_CHARS.sub("_", segment).strip().rstrip(". ")
        if segment:
            parts.append(segment[:_MAX_SEGMENT])
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
    ]
    denoise = values.get("denoise")
    if isinstance(denoise, (int, float)) and denoise != 1:
        params.append(("Denoising strength", denoise))
    params.extend((key, value) for key, value in values.items() if key not in _STANDARD_KEYS)
    params.append(("Version", "ComfyUI"))
    lines.append(", ".join(f"{label}: {_quote(_format_value(value))}"
                           for label, value in params if _format_value(value)))
    return "\n".join(lines)


# ─── 노드 ────────────────────────────────────────────────────────

_REFS_PLACEHOLDER = (
    "ksampler: 3        (KSampler ID 하나로 seed·steps·cfg·sampler·scheduler·denoise·프롬프트·모델·LoRA)\n"
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
            for key in _STANDARD_KEYS
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
                    tooltip="파일명 템플릿. 토큰: %time %model %steps %cfg %sampler %scheduler %seed "
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
        for key, value in _collect_values(refs, kwargs).items():
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
        return io.NodeOutput({"values": values, "settings": settings})


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


NODE_CLASS_MAPPINGS = {
    "BMKSaveMeta": BMKSaveMeta,
    "BMKSaveImage": BMKSaveImage,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "BMKSaveMeta": "BMK Save Meta 🏷️",
    "BMKSaveImage": "BMK Save Image 💾",
}
