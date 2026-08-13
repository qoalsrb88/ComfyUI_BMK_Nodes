from __future__ import annotations

import datetime
import json
import os
import random
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import torch
from PIL import Image, ImageDraw, ImageFont
from PIL.PngImagePlugin import PngInfo

import comfy.model_management
import comfy.samplers
import comfy.sd
import comfy.utils
import folder_paths
import nodes  # ComfyUI 표준 노드 (common_ksampler, UNETLoader, LoraLoader, CLIPTextEncode 활용)

# ─── V3 NodeOutput 호환 ────────────────────────────────────────
# ComfyUI 0.21.x 이후 일부 코어 노드(ModelSamplingAuraFlow, CFGNorm 등)가 V3 API로
# 마이그레이션되면서 튜플 대신 io.NodeOutput(...) 객체를 반환함. 우리가 _call_node로
# 직접 호출할 때 execution.py의 unwrap 경로를 거치지 않으므로, NodeOutput을 직접
# 풀어줘야 함. _NodeOutputInternal은 모든 V3 NodeOutput의 베이스 클래스.
try:
    from comfy_api.internal import _NodeOutputInternal  # type: ignore
    print(f"[BMK XY Plot] V3 NodeOutput base loaded: {_NodeOutputInternal}")
except ImportError as _e:
    _NodeOutputInternal = None  # V3 API가 없는 구버전 ComfyUI
    print(f"[BMK XY Plot] V3 NodeOutput base NOT found ({_e}); duck-typing fallback will be used")


# ─── 설정 ──────────────────────────────────────────────────────
# 축 종류 그룹
_SAMPLER_AXES = {"cfg", "steps", "sampler_name", "scheduler", "seed", "denoise"}
_LOAD_AXES = {"checkpoint", "lora_name"}     # 디스크에서 로드 필요
_NUMERIC_OVERRIDE_AXES = {"lora_strength"}    # 숫자값 오버라이드
_SR_AXES = {"positive_sr", "negative_sr"}     # 프롬프트 Search/Replace

# 체인 위젯 축: 업스트림 model 체인 내부 노드의 위젯 값을 셀별로 오버라이드.
# 매핑: axis_type → (class_type, widget_name)
# 매핑 추가 시 체인 리플레이를 통해 자동 처리됨 (해당 class_type 노드가 체인에 존재해야 함).
_CHAIN_WIDGET_AXES_MAP: Dict[str, Tuple[str, str]] = {
    "lllite_strength":      ("AnimaLLLiteApply",      "strength"),
    "lllite_start_percent": ("AnimaLLLiteApply",      "start_percent"),
    "lllite_end_percent":   ("AnimaLLLiteApply",      "end_percent"),
    "auraflow_shift":       ("ModelSamplingAuraFlow", "shift"),
    "cfgnorm_strength":     ("CFGNorm",               "strength"),
}
_CHAIN_WIDGET_AXES = set(_CHAIN_WIDGET_AXES_MAP.keys())

_AXIS_CHOICES = [
    "none",
    "cfg", "steps", "sampler_name", "scheduler", "seed", "denoise",
    "checkpoint", "lora_name", "lora_strength",
    "positive_sr", "negative_sr",
    "lllite_strength", "lllite_start_percent", "lllite_end_percent",
    "auraflow_shift", "cfgnorm_strength",
]

_FONT_CANDIDATES = [
    "arial.ttf",
    "Arial.ttf",
    "DejaVuSans.ttf",
    "malgun.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/malgun.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


# ─── 유틸: 폰트 / 텐서 변환 ────────────────────────────────────

def _get_font(size: int):
    for candidate in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(candidate, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def _tensor_to_pil(image_tensor: torch.Tensor) -> Image.Image:
    if image_tensor.ndim == 4:
        image_tensor = image_tensor[0]
    arr = (image_tensor.detach().cpu().numpy() * 255.0).clip(0, 255).astype(np.uint8)
    return Image.fromarray(arr, mode="RGB")


def _pil_to_tensor(pil: Image.Image) -> torch.Tensor:
    arr = np.array(pil.convert("RGB"), dtype=np.float32) / 255.0
    return torch.from_numpy(arr)[None, ...]


def _format_value(value: Any) -> str:
    if isinstance(value, float):
        s = f"{value:.6f}".rstrip("0").rstrip(".")
        return s if s else "0"
    if isinstance(value, str):
        # 경로 → basename ("Anima\\NikkeB1.safetensors" → "NikkeB1.safetensors")
        if "/" in value or "\\" in value:
            value = value.replace("\\", "/").split("/")[-1]
        # 모델 확장자 제거
        for ext in (".safetensors", ".ckpt", ".pt"):
            if value.endswith(ext):
                return value[: -len(ext)]
        return value
    return str(value)


def _axis_display_name(axis_type: str, lora_name_default: str, axes_set: set) -> str:
    """사용자에게 보이는 축 이름. lora_strength 축일 때 어떤 LoRA의 strength인지
    혼동이 없도록 LoRA 파일명(basename)을 축 라벨에 포함시킴.

    lora_name이 다른 축으로도 변하고 있으면 (cell마다 LoRA가 바뀌므로) 축 라벨에
    단일 이름을 넣을 수 없어 'lora_strength' 그대로 둠.
    """
    if axis_type == "lora_strength" and "lora_name" not in axes_set:
        if lora_name_default and lora_name_default != "None":
            lora_disp = _format_value(lora_name_default)
            return f"{lora_disp} strength"
    return axis_type


# ─── 유틸: CSV / 값 파싱 ──────────────────────────────────────

def _split_csv_a1111(csv_text: str) -> List[str]:
    """A1111 WebUI 호환 CSV 분할. 큰따옴표로 묶인 값은 내부 쉼표를 보존한다.

    예: '"a, b", c, "d"'  →  ['a, b', 'c', 'd']

    csv.reader는 따옴표 밖의 공백을 보존하므로 (strip은 RFC 4180을 따르려고 일부러 안 함),
    각 토큰을 직접 strip한다. 빈 토큰은 버린다.
    공백을 살리고 싶으면 "  value  " 처럼 따옴표로 감싸면 됨.
    """
    import csv
    import io
    if not csv_text.strip():
        return []
    # 줄바꿈은 공백으로 (멀티라인 입력 지원)
    flat = csv_text.replace("\n", " ").replace("\r", " ")
    reader = csv.reader(io.StringIO(flat), skipinitialspace=True)
    out: List[str] = []
    for row in reader:
        for cell in row:
            cell = cell.strip()
            if cell:
                out.append(cell)
    return out


def _parse_values(axis_type: str, csv_text: str) -> List[Any]:
    if axis_type == "none":
        return [None]

    raw = _split_csv_a1111(csv_text)
    if not raw:
        return [None]

    parsed: List[Any] = []
    for v in raw:
        if axis_type in {"cfg", "denoise", "lora_strength"} or axis_type in _CHAIN_WIDGET_AXES:
            parsed.append(float(v))
        elif axis_type == "steps":
            parsed.append(int(v))
        elif axis_type == "seed":
            if v.lower() in ("random", "-1", "rand"):
                parsed.append(-1)
            else:
                parsed.append(int(v))
        else:
            # sampler_name, scheduler, checkpoint, lora_name, positive_sr, negative_sr
            parsed.append(v)
    return parsed


def _validate_combo_values(axis_type: str, values: List[Any]) -> None:
    if axis_type == "sampler_name":
        valid = set(comfy.samplers.KSampler.SAMPLERS)
        bad = [v for v in values if v not in valid]
        if bad:
            raise ValueError(
                f"[BMK XY Plot] Unknown sampler(s): {bad}. "
                f"Valid: {sorted(valid)}"
            )
    elif axis_type == "scheduler":
        valid = set(comfy.samplers.KSampler.SCHEDULERS)
        bad = [v for v in values if v not in valid]
        if bad:
            raise ValueError(
                f"[BMK XY Plot] Unknown scheduler(s): {bad}. "
                f"Valid: {sorted(valid)}"
            )
    elif axis_type == "checkpoint":
        valid = set(folder_paths.get_filename_list("diffusion_models"))
        bad = [v for v in values if v not in valid]
        if bad:
            raise ValueError(
                f"[BMK XY Plot] Unknown checkpoint file(s): {bad}. "
                f"Place files in ComfyUI/models/diffusion_models/"
            )
    elif axis_type == "lora_name":
        valid = set(folder_paths.get_filename_list("loras"))
        valid.add("None")
        bad = [v for v in values if v not in valid]
        if bad:
            raise ValueError(
                f"[BMK XY Plot] Unknown LoRA file(s): {bad}. "
                f"Place files in ComfyUI/models/loras/, or use 'None' to skip."
            )


def _resolve_seed(seed_val: int) -> int:
    if seed_val < 0:
        return random.randint(0, 0xFFFFFFFFFFFFFFFF)
    return seed_val


# ─── 유틸: 모델/LoRA/프롬프트 로딩 ────────────────────────────
# ComfyUI 표준 노드 인스턴스를 호출 → 버전 호환성 자동 확보

def _load_unet(unet_name: str, weight_dtype: str):
    """UNETLoader 노드와 완전 동일한 로직."""
    loader = nodes.UNETLoader()
    return loader.load_unet(unet_name, weight_dtype)[0]


def _apply_lora(model, clip, lora_name: str, strength: float):
    """LoraLoader 노드와 완전 동일한 로직. strength_model=strength_clip=strength로 통일."""
    if not lora_name or lora_name == "None" or strength == 0.0:
        return model, clip
    loader = nodes.LoraLoader()
    new_model, new_clip = loader.load_lora(model, clip, lora_name, strength, strength)
    return new_model, new_clip


def _encode_text(clip, text: str):
    """CLIPTextEncode 노드와 완전 동일한 로직."""
    encoder = nodes.CLIPTextEncode()
    return encoder.encode(clip, text)[0]


def _apply_sr(text: str, search: str, replacement: str) -> str:
    """A1111 스타일 Prompt S/R: 검색어를 치환값으로 단순 교체."""
    if not search:
        return text
    return text.replace(search, replacement)


# ─── 유틸: 체크포인트 체인 리플레이 ─────────────────────────────
# 사용자 워크플로우가 [Loader → LoRA → 패처 → ... → XY Plot] 구조일 때
# 체크포인트 축이 활성화되면 LoRA/패처를 우회하지 않고 같은 설정으로 재실행.

def _build_model_chain(prompt: dict, my_node_id: str) -> List[Tuple[str, dict]]:
    """내 model 입력에서 시작해서 root loader까지 거슬러 올라간 체인을 반환.
    리스트 순서: [root_loader, ..., 마지막_중간노드] (XY Plot 자신은 미포함).

    Context (rgthree) 류 패스스루 노드는 chain에 포함시키지 않고 통과 — model 입력
    우선, 없으면 base_ctx로 fallback해서 거꾸로 이어간다. 이 노드들은 model에 patch를
    가하지 않고 단순 통과시키는 역할이므로 체인 리플레이에서 우회해도 결과 동일.
    """
    if not prompt or not my_node_id or my_node_id not in prompt:
        return []

    chain: List[Tuple[str, dict]] = []
    inputs = prompt[my_node_id].get("inputs", {})
    model_link = inputs.get("model")
    if not (isinstance(model_link, list) and len(model_link) >= 1):
        return []

    current_id = str(model_link[0])
    visited = set()
    for _ in range(50):
        if current_id in visited or current_id not in prompt:
            break
        visited.add(current_id)
        node = prompt[current_id]
        class_type = node.get("class_type", "")

        # Context 패스스루: chain에 포함시키지 않고 진짜 source 쪽으로 계속 거슬러 올라감.
        # model 입력이 connected이면 그게 진짜 source, 아니면 base_ctx에서 받은 컨텍스트의
        # model이 흐르므로 base_ctx를 따라간다.
        if class_type in _CONTEXT_PASSTHROUGH_TYPES:
            node_inputs = node.get("inputs", {})
            model_in = node_inputs.get("model")
            if isinstance(model_in, list) and len(model_in) >= 1:
                current_id = str(model_in[0])
                continue
            base_in = node_inputs.get("base_ctx")
            if isinstance(base_in, list) and len(base_in) >= 1:
                current_id = str(base_in[0])
                continue
            # model도 base_ctx도 없으면 추적 불가
            break

        # ComfySwitch 패스스루: switch widget 값(bool)을 보고 활성 분기(on_true/on_false)
        # 쪽으로 chain 추적을 이어감. 다른 분기는 우리 관심사 아님 (실제 실행에서도
        # 평가되지 않으므로). chain에는 포함시키지 않음.
        if class_type in _SWITCH_PASSTHROUGH_TYPES:
            node_inputs = node.get("inputs", {})
            switch_val = node_inputs.get("switch", False)
            is_true = _coerce_bool(switch_val)
            active_name = "on_true" if is_true else "on_false"
            active_link = node_inputs.get(active_name)
            if isinstance(active_link, list) and len(active_link) >= 1:
                current_id = str(active_link[0])
                continue
            # 활성 분기가 unconnected이면 추적 불가 — 끊김
            break

        chain.insert(0, (current_id, node))

        # Root loader 도달 시 종료
        if class_type in _LOADER_WIDGET_NAMES:
            break

        # model 링크 따라 계속 올라가기
        next_link = node.get("inputs", {}).get("model")
        if isinstance(next_link, list) and len(next_link) >= 1:
            current_id = str(next_link[0])
        else:
            break

    return chain


def _call_node(cls, kwargs: dict, prompt: dict, node_id: str) -> tuple:
    """노드 클래스의 FUNCTION을 호출. HIDDEN 입력 자동 주입.

    V3 노드(io.NodeOutput 반환)는 자동으로 .result 튜플로 unwrap. 따라서 호출자는
    항상 일관된 튜플 형태로 결과를 받음."""
    try:
        input_types = cls.INPUT_TYPES()
        hidden = input_types.get("hidden", {})
        for h_name, h_type in hidden.items():
            if h_type == "PROMPT":
                kwargs[h_name] = prompt
            elif h_type == "UNIQUE_ID":
                kwargs[h_name] = node_id
            # EXTRA_PNGINFO는 의도적으로 패스 (재실행 중에 메타데이터 오염 방지)
    except Exception:
        pass

    instance = cls()
    result = getattr(instance, cls.FUNCTION)(**kwargs)

    # V3 NodeOutput → 튜플 unwrap. V3 노드는 io.NodeOutput(out1, out2, ...)을 반환하고
    # 내부적으로 .result 속성에 튜플로 보관함. execution.py에서 자동으로 풀어주지만
    # 우리는 _call_node로 직접 호출하므로 여기서 처리.
    #
    # 안전망 2중 구조:
    #   (1) _NodeOutputInternal 베이스 클래스 isinstance 체크 (정상 케이스)
    #   (2) duck typing — 클래스 이름이 'NodeOutput'이고 .result 속성이 있으면 V3로 간주.
    #       comfy_api.internal._NodeOutputInternal import 실패하거나 NodeOutput의 베이스
    #       클래스가 ComfyUI 버전에 따라 바뀌어도 unwrap이 동작하도록 폴백.
    is_v3 = False
    if _NodeOutputInternal is not None and isinstance(result, _NodeOutputInternal):
        is_v3 = True
    elif type(result).__name__ == "NodeOutput" and hasattr(result, "result"):
        is_v3 = True

    if is_v3:
        extracted = getattr(result, "result", None)
        # .result가 None이면 빈 튜플, 단일 객체면 1-tuple로, 이미 튜플이면 그대로.
        if extracted is None:
            result = ()
        elif not isinstance(extracted, tuple):
            result = (extracted,)
        else:
            result = extracted

    if not isinstance(result, tuple):
        result = (result,)
    return result


def _execute_external_node(
    src_id: str, prompt: dict, external_cache: Dict[str, tuple],
) -> tuple:
    """체인 외부 노드 (예: 별도 CLIPLoader) 를 재실행해서 출력 반환. 캐시됨.

    외부 노드의 입력은 widget이어야 함 — 연결된 입력은 다단계 재귀가 되어 위험.
    단, Context (rgthree) 류 패스스루 노드는 호출 자체를 우회하므로 (출력 슬롯이
    곧 입력 슬롯으로 위임됨) 이 함수가 아니라 _resolve_external_value 쪽에서
    처리되며 여기 도달하지 않는다.
    """
    if src_id in external_cache:
        return external_cache[src_id]

    node = prompt.get(src_id)
    if not node:
        raise RuntimeError(f"External node {src_id} not found in prompt")

    class_type = node.get("class_type", "")
    cls = nodes.NODE_CLASS_MAPPINGS.get(class_type)
    if not cls:
        raise RuntimeError(f"Unknown external node class: {class_type}")

    inputs = node.get("inputs", {})
    kwargs = {}
    for k, v in inputs.items():
        if isinstance(v, list):
            raise RuntimeError(
                f"External node {src_id} ({class_type}) has connected input '{k}'; "
                f"multi-level external resolution not supported."
            )
        kwargs[k] = v

    result = _call_node(cls, kwargs, prompt, src_id)
    external_cache[src_id] = result
    return result


def _resolve_external_value(
    src_id: str,
    src_slot: int,
    prompt: dict,
    chain_outputs: Dict[str, Dict[str, Any]],
    external_cache: Dict[str, tuple],
    _visited: Optional[set] = None,
) -> Any:
    """체인 외부 노드의 특정 출력 슬롯 값을 해석.

    일반 외부 노드는 _execute_external_node로 전체 실행 후 슬롯 인덱싱.
    Context (rgthree) 류 패스스루 노드는 호출하지 않고 출력 슬롯에 해당하는 입력
    소스로 위임 (자체 model 입력이 connected이면 그것, 없으면 base_ctx의 같은 슬롯
    으로 거슬러 올라감). 이렇게 하면 Context 노드가 connected 입력을 가져도
    "multi-level external resolution not supported" 에러가 나지 않는다.

    위임 과정에서 chain_outputs에 있는 source가 나오면 (예: Context의 model이
    체인 안에서 방금 만든 새 model을 가리키는 경우) 그 값을 그대로 사용.
    """
    if _visited is None:
        _visited = set()
    if src_id in _visited:
        raise RuntimeError(
            f"External node {src_id} forms a cycle in context passthrough chain"
        )
    _visited = _visited | {src_id}

    node = prompt.get(src_id)
    if not node:
        raise RuntimeError(f"External node {src_id} not found in prompt")

    class_type = node.get("class_type", "")

    # Context 패스스루: 호출 없이 슬롯을 위임
    if class_type in _CONTEXT_PASSTHROUGH_TYPES:
        cls = nodes.NODE_CLASS_MAPPINGS.get(class_type)
        if not cls:
            raise RuntimeError(f"Unknown context node class: {class_type}")
        return_names = getattr(cls, "RETURN_NAMES", None)
        return_types = getattr(cls, "RETURN_TYPES", ())
        if not return_names or src_slot >= len(return_names):
            raise RuntimeError(
                f"Context node {src_id} ({class_type}) slot {src_slot} "
                f"out of RETURN_NAMES range"
            )
        slot_name = return_names[src_slot]
        slot_type = return_types[src_slot] if src_slot < len(return_types) else None

        # rgthree Context 노드의 컨벤션: RETURN_NAMES은 대문자 ('MODEL', 'CLIP', ...)
        # 인데 입력 이름은 소문자 ('model', 'clip', ...). 그래서 slot_name을 그대로
        # node_inputs lookup에 쓰면 매번 miss 나서 base_ctx로 잘못 떠밀린다.
        # 소문자 변환한 input 이름으로 lookup해야 _build_model_chain의 추적 경로와
        # 일치한다.
        input_name = slot_name.lower()

        node_inputs = node.get("inputs", {})

        # 1) 같은 이름의 입력이 connected이면 그쪽으로 위임
        direct_link = node_inputs.get(input_name)
        if isinstance(direct_link, list) and len(direct_link) >= 2:
            return _follow_link(
                direct_link, prompt, chain_outputs, external_cache, _visited,
            )

        # 2) base_ctx가 있으면 거기서 같은 슬롯 요청
        #    Context 출력 슬롯 인덱스는 Context Big 끼리 호환됨 (앞부분 공통).
        #    base_ctx로 거슬러 올라간 노드도 Context이면 같은 src_slot 사용 가능.
        base_link = node_inputs.get("base_ctx")
        if isinstance(base_link, list) and len(base_link) >= 2:
            base_src_id = str(base_link[0])
            return _resolve_external_value(
                base_src_id, src_slot, prompt, chain_outputs,
                external_cache, _visited,
            )

        # 3) 어디서도 받을 수 없는 슬롯이면 None을 반환 (정상 ComfyUI 실행에서도
        #    Context 노드의 unconnected slot은 None을 흘림 — new_context의 동작).
        #    단, RGTHREE_CONTEXT 자체를 요청한 경우는 None으로 처리하면 곤란하지만,
        #    체인 중간 노드가 RGTHREE_CONTEXT를 입력으로 받지는 않으므로 패스.
        return None

    # Switch 패스스루: 호출 없이 활성 분기로 위임 (_build_model_chain과 동일한 규칙).
    # switch widget 값을 기준으로 분기 결정 — switch 입력이 connected여서 widget 값이
    # stale할 수도 있지만, _build_model_chain도 같은 규칙으로 분기를 선택해 root까지
    # 추적했으므로 일관성을 위해 같은 규칙을 사용. 패치 체인의 두 분기는 보통 같은
    # 체크포인트로 수렴하는 일반적 패턴이라 model 결과에는 영향이 작음.
    # Switch는 단일 MODEL/이미지/conditioning 등을 통과시키므로 src_slot은 0이 일반적.
    if class_type in _SWITCH_PASSTHROUGH_TYPES:
        node_inputs = node.get("inputs", {})
        switch_val = node_inputs.get("switch", False)
        # switch 입력이 connected (list)이면 widget 기본값으로 fallback.
        # 실제 노드 정의의 widget 기본값은 prompt에 없을 수 있으므로 False로 가정 —
        # 이는 _build_model_chain의 _coerce_bool 동작과 일관됨.
        if isinstance(switch_val, list):
            switch_val = False
        is_true = _coerce_bool(switch_val)
        active_name = "on_true" if is_true else "on_false"
        active_link = node_inputs.get(active_name)
        if isinstance(active_link, list) and len(active_link) >= 2:
            return _follow_link(
                active_link, prompt, chain_outputs, external_cache, _visited,
            )
        # 활성 분기가 unconnected이면 None — 호출자가 처리.
        return None

    # 일반 외부 노드: 기존 경로
    ext_result = _execute_external_node(src_id, prompt, external_cache)
    if src_slot >= len(ext_result):
        raise RuntimeError(
            f"External node {src_id} slot {src_slot} out of range"
        )
    return ext_result[src_slot]


def _follow_link(
    link: list,
    prompt: dict,
    chain_outputs: Dict[str, Dict[str, Any]],
    external_cache: Dict[str, tuple],
    visited: set,
) -> Any:
    """[src_id, src_slot] 링크를 따라가 값을 가져옴.

    src_id가 체인 내부면 chain_outputs에서, 아니면 _resolve_external_value로 위임.
    Context 패스스루 재귀를 위한 visited 세트를 그대로 전달.
    """
    src_id = str(link[0])
    src_slot = int(link[1])

    if src_id in chain_outputs:
        src_class = prompt[src_id].get("class_type", "")
        src_cls = nodes.NODE_CLASS_MAPPINGS.get(src_class)
        src_return_types = getattr(src_cls, "RETURN_TYPES", ()) if src_cls else ()
        if src_slot >= len(src_return_types):
            raise RuntimeError(
                f"Chain node {src_id} slot {src_slot} out of range"
            )
        rt = src_return_types[src_slot]
        val = chain_outputs[src_id].get(rt)
        if val is None:
            raise RuntimeError(
                f"Chain node {src_id} didn't produce required type {rt}"
            )
        return val

    return _resolve_external_value(
        src_id, src_slot, prompt, chain_outputs, external_cache, visited,
    )


def _resolve_chain_node_inputs(
    node: dict, node_id: str, prompt: dict,
    chain_outputs: Dict[str, Dict[str, Any]],
    external_cache: Dict[str, tuple],
    chain_widget_overrides: Optional[Dict[str, Dict[str, Any]]] = None,
    ambient_values: Optional[Dict[str, Any]] = None,
) -> dict:
    """체인 중간 노드의 kwargs를 만든다. 연결된 입력은 chain_outputs / external에서 해결.

    chain_widget_overrides: {class_type: {widget_name: value}} — 매칭되는 노드의 위젯 값을
    덮어쓴다. 같은 class_type 노드가 체인에 여러 개 있으면 모두 동일하게 덮어씀.

    ambient_values: {ComfyType: value} — prompt에 키가 빠진 required input을 보충할 때
    사용하는 "주변 값". 예: 사용자 워크플로우에서 Power Lora Loader의 clip 슬롯이
    unconnected이면 prompt의 inputs dict에 clip 키가 없을 수 있는데, 정상 실행 시
    ComfyUI는 None을 자동 채워 호출함. 우리는 그 메커니즘을 직접 거치지 않으므로 여기서
    수동으로 보충해야 함. CLIP은 fallback_clip, VAE는 fallback_vae를 넣고 그 외 타입은
    None을 넣는다.
    """
    kwargs = {}
    inputs = node.get("inputs", {})
    for input_name, input_value in inputs.items():
        if isinstance(input_value, list) and len(input_value) >= 2:
            # _follow_link 가 chain_outputs / external (Context 패스스루 포함) 을 모두 처리.
            try:
                kwargs[input_name] = _follow_link(
                    input_value, prompt, chain_outputs, external_cache, set(),
                )
            except RuntimeError as e:
                # 컨텍스트 보강: 어느 체인 노드의 어느 입력에서 터졌는지 명시.
                raise RuntimeError(
                    f"Chain node {node_id} ({node.get('class_type', '')}) "
                    f"input '{input_name}': {e}"
                )
        else:
            # Widget 값
            kwargs[input_name] = input_value

    # 위젯 오버라이드 적용 (연결된 입력은 위에서 결정됐으니, 위젯에 해당하는 키만 덮어씀)
    if chain_widget_overrides:
        class_type = node.get("class_type", "")
        if class_type in chain_widget_overrides:
            for widget_name, value in chain_widget_overrides[class_type].items():
                kwargs[widget_name] = value

    # Missing required input 보충 — Power Lora Loader 같은 노드의 clip 슬롯이
    # unconnected일 때 prompt에 키가 없으면 함수 호출이 TypeError로 터지므로,
    # required 섹션을 훑어 빠진 키를 안전한 기본값으로 채워준다.
    class_type = node.get("class_type", "")
    cls = nodes.NODE_CLASS_MAPPINGS.get(class_type)
    if cls is not None:
        try:
            input_types = cls.INPUT_TYPES()
            required = input_types.get("required", {}) or {}
            for req_name in required.keys():
                if req_name in kwargs:
                    continue
                req_spec = required[req_name]
                # spec 형태: (TYPE_NAME, {...config}) or (TYPE_NAME,) or list of options
                req_type = None
                if isinstance(req_spec, tuple) and len(req_spec) >= 1:
                    first = req_spec[0]
                    if isinstance(first, str):
                        req_type = first
                # ambient 우선, 없으면 None
                fill = None
                if ambient_values and req_type and req_type in ambient_values:
                    fill = ambient_values[req_type]
                kwargs[req_name] = fill
                print(
                    f"[BMK XY Plot]   chain node {node_id} ({class_type}): "
                    f"padded missing required input '{req_name}' (type={req_type}) "
                    f"with {'ambient' if fill is not None else 'None'}"
                )
        except Exception as e:
            # INPUT_TYPES 호출 실패 등은 fatal이 아니므로 경고만
            print(f"[BMK XY Plot]   chain node {node_id} ({class_type}): "
                  f"warning, missing-input padding skipped ({type(e).__name__}: {e})")

    return kwargs


def _execute_root_with_checkpoint(
    root_id: str, root_node: dict, new_checkpoint: Optional[str], dtype: str, prompt: dict,
) -> tuple:
    """Root loader를 새 체크포인트로 재실행. weight_dtype은 사용자 위젯값 우선,
    위젯이 없거나 'default'면 dtype 인자 사용.

    new_checkpoint=None 이면 prompt에 기록된 원래 위젯 값 그대로 사용 (체인 위젯 축만
    활성화된 케이스).
    """
    class_type = root_node.get("class_type", "")
    cls = nodes.NODE_CLASS_MAPPINGS.get(class_type)
    if not cls:
        raise RuntimeError(f"Unknown root loader class: {class_type}")

    widget_name = _LOADER_WIDGET_NAMES.get(class_type)
    if not widget_name:
        raise RuntimeError(f"No checkpoint widget known for loader class: {class_type}")

    inputs = root_node.get("inputs", {})
    kwargs = {}
    for k, v in inputs.items():
        if isinstance(v, list):
            raise RuntimeError(
                f"Root loader {class_type} has connected input '{k}' — not supported"
            )
        kwargs[k] = v

    # 체크포인트 위젯 치환 (None이면 원래 값 유지)
    if new_checkpoint is not None:
        kwargs[widget_name] = new_checkpoint
    # weight_dtype은 사용자 dtype 입력으로 override (위젯이 있을 때만, 그리고 체크포인트
    # 축이 활성화된 경우에만 — 체인 위젯 축만 쓸 때는 원래 dtype을 건드리지 않음)
    if new_checkpoint is not None and "weight_dtype" in kwargs and dtype:
        kwargs["weight_dtype"] = dtype

    return _call_node(cls, kwargs, prompt, root_id)


def _execute_checkpoint_chain(
    prompt: Optional[dict],
    my_node_id: Optional[str],
    new_checkpoint: Optional[str],
    dtype: str,
    fallback_clip: Any,
    chain_widget_overrides: Optional[Dict[str, Dict[str, Any]]] = None,
    fallback_vae: Any = None,
) -> Tuple[Any, Any, str]:
    """업스트림 model 체인을 새 체크포인트 / 위젯 오버라이드로 리플레이.

    Args:
        new_checkpoint: 새 체크포인트 파일명. None이면 원래 위젯 값 유지 (체인 위젯 축만
            활성화된 경우).
        chain_widget_overrides: {class_type: {widget_name: value}} — 체인 중간 노드의
            위젯 값을 셀별로 덮어쓰기 위한 매핑.
        fallback_clip: 체인 위쪽에서 못 가져오는 unconnected required CLIP 슬롯을 채울 값.
            보통 XY Plot의 clip 입력.
        fallback_vae: 마찬가지로 unconnected required VAE 슬롯을 채울 값.

    Returns: (final_model, final_clip, status_message). 실패 시 RuntimeError raise.
    """
    if not prompt or not my_node_id:
        raise RuntimeError("prompt/unique_id unavailable for chain replay")

    chain = _build_model_chain(prompt, str(my_node_id))
    if not chain:
        raise RuntimeError("No model chain found")

    root_id, root_node = chain[0]
    root_class = root_node.get("class_type", "")
    if root_class not in _LOADER_WIDGET_NAMES:
        raise RuntimeError(f"Chain root '{root_class}' is not a known loader")

    # Root 실행 (새 체크포인트로 — None이면 원래 값 유지)
    root_result = _execute_root_with_checkpoint(
        root_id, root_node, new_checkpoint, dtype, prompt,
    )

    # 체인 출력 트래킹
    root_cls = nodes.NODE_CLASS_MAPPINGS.get(root_class)
    root_return_types = getattr(root_cls, "RETURN_TYPES", ())
    chain_outputs: Dict[str, Dict[str, Any]] = {root_id: {}}
    for i, rt in enumerate(root_return_types):
        if i < len(root_result):
            chain_outputs[root_id][rt] = root_result[i]

    running_model = chain_outputs[root_id].get("MODEL")
    running_clip = chain_outputs[root_id].get("CLIP", fallback_clip)
    if running_model is None:
        raise RuntimeError(f"Root loader {root_class} didn't produce MODEL output")

    # 외부 노드 결과 캐시 (CLIPLoader 등)
    external_cache: Dict[str, tuple] = {}

    # 체인 중간 노드들 순차 실행
    intermediate_count = 0
    pad_log_count = 0
    for node_id, node in chain[1:]:
        class_type = node.get("class_type", "")
        cls = nodes.NODE_CLASS_MAPPINGS.get(class_type)
        if not cls:
            raise RuntimeError(f"Unknown chain node class: {class_type} (id={node_id})")

        # 현재 시점의 ambient — running_clip은 체인을 거치며 갱신될 수 있음
        ambient = {
            "CLIP": running_clip,
            "VAE": fallback_vae,
        }

        kwargs = _resolve_chain_node_inputs(
            node, node_id, prompt, chain_outputs, external_cache,
            chain_widget_overrides=chain_widget_overrides,
            ambient_values=ambient,
        )

        try:
            result = _call_node(cls, kwargs, prompt, node_id)
        except Exception as e:
            # 어떤 노드가 어떤 kwargs로 실패했는지 명확히 — fallback 진단에 결정적.
            kwargs_summary = {k: type(v).__name__ for k, v in kwargs.items()}
            raise RuntimeError(
                f"Chain node {node_id} ({class_type}) execution failed: "
                f"{type(e).__name__}: {e}. kwargs types={kwargs_summary}"
            )

        chain_outputs[node_id] = {}
        return_types = getattr(cls, "RETURN_TYPES", ())
        for i, rt in enumerate(return_types):
            if i < len(result):
                chain_outputs[node_id][rt] = result[i]

        if "MODEL" in chain_outputs[node_id]:
            running_model = chain_outputs[node_id]["MODEL"]
        if "CLIP" in chain_outputs[node_id] and chain_outputs[node_id]["CLIP"] is not None:
            # 체인 노드가 None을 CLIP으로 반환하면 (예: PowerLoraLoader clip=None일 때)
            # 이전 running_clip을 유지해서 다음 노드들이 여전히 ambient로 활용 가능하게.
            running_clip = chain_outputs[node_id]["CLIP"]
        intermediate_count += 1

    ckpt_note = "ckpt=keep" if new_checkpoint is None else f"ckpt={new_checkpoint}"
    ovr_note = ""
    if chain_widget_overrides:
        flat = []
        for cls_name, widgets in chain_widget_overrides.items():
            for w, v in widgets.items():
                flat.append(f"{cls_name}.{w}={v}")
        ovr_note = f", overrides=[{', '.join(flat)}]"
    status = (
        f"chain replay OK (root={root_class}, {ckpt_note}, "
        f"intermediate={intermediate_count}, external={len(external_cache)}{ovr_note})"
    )
    # 진단: 최종 model 객체가 ModelPatcher인지 (get_model_object를 갖는지) 확인.
    # 만약 has_gmo=False면 어떤 체인 노드가 V3 NodeOutput을 풀지 못했다는 뜻 → 위쪽
    # _call_node의 unwrap 로직 점검 필요. 정상이면 True여야 common_ksampler가 받아준다.
    print(
        f"[BMK XY Plot]   running_model type={type(running_model).__name__}, "
        f"has get_model_object={hasattr(running_model, 'get_model_object')}"
    )
    return running_model, running_clip, status


def _scan_chain_widget_state(
    prompt: Optional[dict], my_node_id: Optional[str],
) -> Dict[str, Dict[str, Any]]:
    """체인을 한 번 훑어 각 class_type에 대한 현재 위젯 값을 수집.
    Returns: {class_type: {widget_name: current_value}}.

    같은 class_type 노드가 여러 개 있으면 첫 번째 것이 우선 (사용자 워크플로우 기준
    체인엔 각 종류 1개만 존재).
    """
    state: Dict[str, Dict[str, Any]] = {}
    if not prompt or not my_node_id:
        return state

    chain = _build_model_chain(prompt, str(my_node_id))
    target_classes = {ct for ct, _ in _CHAIN_WIDGET_AXES_MAP.values()}
    for _node_id, node in chain:
        class_type = node.get("class_type", "")
        if class_type in target_classes and class_type not in state:
            inputs = node.get("inputs", {})
            widget_vals = {}
            for axis_type, (ct, wname) in _CHAIN_WIDGET_AXES_MAP.items():
                if ct == class_type and wname in inputs:
                    val = inputs[wname]
                    # 연결된 입력 (list)이면 위젯이 아니라 link이므로 건너뜀
                    if not isinstance(val, list):
                        widget_vals[wname] = val
            if widget_vals:
                state[class_type] = widget_vals
    return state


def _format_chain_extras(state: Dict[str, Dict[str, Any]]) -> str:
    """체인 위젯 상태를 A1111 메타데이터용 단일 라인으로 포맷.
    예: 'LLLite strength: 0.8, LLLite start_percent: 0, AuraFlow shift: 1.73, CFGNorm strength: 1'
    """
    pretty_class = {
        "AnimaLLLiteApply": "LLLite",
        "ModelSamplingAuraFlow": "AuraFlow",
        "CFGNorm": "CFGNorm",
    }
    parts = []
    for class_type, widgets in state.items():
        label = pretty_class.get(class_type, class_type)
        for wname, val in widgets.items():
            parts.append(f"{label} {wname}: {_format_value(val)}")
    return ", ".join(parts)


# ─── 유틸: 텍스트 오버레이 ────────────────────────────────────

def _apply_overlay(
    pil_image: Image.Image,
    text: str,
    position: str,
    font_size: int,
) -> Image.Image:
    """A1111-style overlay: solid black box fitted to the text block, white text on top."""
    if not text:
        return pil_image.copy()

    img = pil_image.copy()
    draw = ImageDraw.Draw(img)
    font = _get_font(font_size)

    lines = text.split("\n")
    line_metrics: List[Tuple[int, int]] = []
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        line_metrics.append((bbox[2] - bbox[0], bbox[3] - bbox[1]))

    block_w = max((w for w, _ in line_metrics), default=0)
    line_gap = 4
    block_h = sum(h for _, h in line_metrics) + line_gap * max(0, len(lines) - 1)

    box_pad_x = max(4, font_size // 6)
    box_pad_y = max(3, font_size // 8)

    box_w = block_w + box_pad_x * 2
    box_h = block_h + box_pad_y * 2

    edge_pad = max(8, font_size // 3)
    W, H = img.size

    if position == "top-left":
        box_x, box_y = edge_pad, edge_pad
    elif position == "top-right":
        box_x, box_y = W - box_w - edge_pad, edge_pad
    elif position == "bottom-left":
        box_x, box_y = edge_pad, H - box_h - edge_pad
    else:  # bottom-right
        box_x, box_y = W - box_w - edge_pad, H - box_h - edge_pad

    draw.rectangle(
        [box_x, box_y, box_x + box_w, box_y + box_h],
        fill=(0, 0, 0),
    )

    text_x = box_x + box_pad_x
    cur_y = box_y + box_pad_y
    for (line, (_, lh)) in zip(lines, line_metrics):
        draw.text(
            (text_x, cur_y), line, font=font,
            fill=(255, 255, 255),
        )
        cur_y += lh + line_gap

    return img


# ─── 유틸: 그리드 합성 ────────────────────────────────────────

def _compose_grid(
    images: List[Image.Image],
    x_vals: List[Any], y_vals: List[Any],
    x_axis: str, y_axis: str,
    x_axis_display: str, y_axis_display: str,
    draw_labels: bool, cell_gap: int, font_size: int,
) -> Image.Image:
    if not images:
        raise RuntimeError("[BMK XY Plot] No images to compose.")

    cell_w, cell_h = images[0].size
    cols = max(1, len(x_vals))
    rows = max(1, len(y_vals))

    show_top = draw_labels and x_axis != "none"
    show_left = draw_labels and y_axis != "none"

    label_top_h = font_size * 2 + 16 if show_top else 0
    label_left_w = 0
    if show_left:
        font = _get_font(font_size)
        probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
        max_w = 0
        for yv in y_vals:
            for line in (y_axis_display, _format_value(yv)):
                bbox = probe.textbbox((0, 0), line, font=font)
                max_w = max(max_w, bbox[2] - bbox[0])
        label_left_w = max_w + 24

    total_w = label_left_w + cols * cell_w + (cols - 1) * cell_gap
    total_h = label_top_h + rows * cell_h + (rows - 1) * cell_gap

    grid = Image.new("RGB", (total_w, total_h), (255, 255, 255))

    for r in range(rows):
        for c in range(cols):
            idx = r * cols + c
            if idx >= len(images):
                break
            x = label_left_w + c * (cell_w + cell_gap)
            y = label_top_h + r * (cell_h + cell_gap)
            grid.paste(images[idx], (x, y))

    if draw_labels:
        draw = ImageDraw.Draw(grid)
        font = _get_font(font_size)

        if show_top:
            for c, xv in enumerate(x_vals):
                label = f"{x_axis_display}: {_format_value(xv)}"
                bbox = draw.textbbox((0, 0), label, font=font)
                tw = bbox[2] - bbox[0]
                cx = label_left_w + c * (cell_w + cell_gap) + cell_w // 2
                draw.text(
                    (cx - tw // 2, 8),
                    label,
                    fill=(0, 0, 0),
                    font=font,
                )

        if show_left:
            line_gap = 4
            for r, yv in enumerate(y_vals):
                lines = [y_axis_display, _format_value(yv)]
                heights = []
                for ln in lines:
                    bbox = draw.textbbox((0, 0), ln, font=font)
                    heights.append(bbox[3] - bbox[1])
                total_text_h = sum(heights) + line_gap * (len(lines) - 1)
                cy = label_top_h + r * (cell_h + cell_gap) + cell_h // 2
                y_cur = cy - total_text_h // 2
                for ln, h in zip(lines, heights):
                    draw.text((12, y_cur), ln, fill=(0, 0, 0), font=font)
                    y_cur += h + line_gap

    return grid


# ─── 유틸: 메타데이터 / 저장 ──────────────────────────────────

def _build_a1111_parameters(
    params: Dict[str, Any],
    positive_text: str,
    negative_text: str,
    width: int,
    height: int,
    model_name: Optional[str] = None,
    lora_name: Optional[str] = None,
    lora_strength: Optional[float] = None,
    chain_extras: Optional[str] = None,
) -> str:
    # LoRA가 적용됐다면 A1111 표준 <lora:name:strength> 토큰을 positive 끝에 추가
    pos = positive_text or ""
    if lora_name and lora_name != "None" and lora_strength and lora_strength != 0.0:
        lora_basename = os.path.splitext(os.path.basename(lora_name))[0]
        pos = f"{pos} <lora:{lora_basename}:{_format_value(lora_strength)}>".strip()

    lines = [pos]
    if negative_text:
        lines.append(f"Negative prompt: {negative_text}")

    settings = [
        f"Steps: {params['steps']}",
        f"Sampler: {params['sampler_name']}",
        f"Schedule type: {params['scheduler']}",
        f"CFG scale: {params['cfg']}",
        f"Seed: {params['seed']}",
        f"Size: {width}x{height}",
    ]
    if model_name:
        settings.append(f"Model: {os.path.splitext(os.path.basename(model_name))[0]}")
    # 체인 위젯 값들 (LLLite/AuraFlow/CFGNorm 등) 을 셀별 실제 사용값으로 기록
    if chain_extras:
        settings.append(chain_extras)

    lines.append(", ".join(settings))
    return "\n".join(lines)


def _make_metadata(
    params: Dict[str, Any],
    positive_text: str,
    negative_text: str,
    width: int,
    height: int,
    prompt: Optional[dict],
    extra_pnginfo: Optional[dict],
    embed_a1111: bool,
    embed_workflow: bool,
    model_name: Optional[str] = None,
    lora_name: Optional[str] = None,
    lora_strength: Optional[float] = None,
    chain_extras: Optional[str] = None,
) -> PngInfo:
    metadata = PngInfo()
    if embed_a1111:
        a1111 = _build_a1111_parameters(
            params, positive_text, negative_text, width, height,
            model_name=model_name, lora_name=lora_name, lora_strength=lora_strength,
            chain_extras=chain_extras,
        )
        metadata.add_text("parameters", a1111)
    if embed_workflow:
        if prompt is not None:
            metadata.add_text("prompt", json.dumps(prompt))
        if extra_pnginfo is not None:
            for k, v in extra_pnginfo.items():
                metadata.add_text(k, json.dumps(v))
    return metadata


def _save_pil(
    pil: Image.Image,
    full_folder: str,
    filename_base: str,
    counter: int,
    suffix: str,
    metadata: Optional[PngInfo],
) -> str:
    """[Legacy] Save with fixed prefix_counter_suffix format. Kept for compatibility."""
    os.makedirs(full_folder, exist_ok=True)
    file_name = f"{filename_base}_{counter:05}_{suffix}.png"
    full_path = os.path.join(full_folder, file_name)
    pil.save(full_path, pnginfo=metadata, compress_level=4)
    return full_path


# ─── 유틸: 파일명 템플릿 (Image-Saver 스타일) ──────────────────

# 업스트림 그래프에서 basemodel 자동 추적 시 알려진 로더 클래스 목록
_LOADER_WIDGET_NAMES = {
    "UNETLoader": "unet_name",
    "UnetLoaderGGUF": "unet_name",
    "UnetLoaderGGUFAdvanced": "unet_name",
    "CheckpointLoaderSimple": "ckpt_name",
    "CheckpointLoader": "ckpt_name",
    "ImageOnlyCheckpointLoader": "ckpt_name",
    "DualCheckpointLoader": "ckpt_name",
    "DiffusersLoader": "model_path",
    "NunchakuFluxDiTLoader": "model_path",
}

# rgthree Context 노드들 — model이 RGTHREE_CONTEXT 안에 묶여 흐른다.
# 동작: model 입력이 connected이면 그 값으로 출력 model 결정, unconnected이면 base_ctx
# 에서 받은 context의 model을 그대로 패스스루. 우리 model 체인 추적은 같은 규칙을 적용
# (model 입력 우선, 없으면 base_ctx로 fallback). 체인 실행 시 이 노드들은 patch 효과가
# 없으므로 chain에 포함시키지 않고 통과 — 정상 ComfyUI 실행에서도 이 노드들은 model을
# 그대로 흘려보내므로 우회해도 결과 동일.
_CONTEXT_PASSTHROUGH_TYPES = {
    "Context (rgthree)",
    "Context Big (rgthree)",
    "Context Switch (rgthree)",
    "Context Switch Big (rgthree)",
    "Context Merge (rgthree)",
    "Context Merge Big (rgthree)",
    "BMKContextAnima",
}

# ComfyUI 내장 conditional passthrough 노드들 — switch (BOOLEAN) widget 값에 따라
# on_true / on_false 입력 중 하나를 단순 통과. ComfyUI Frontend의 Subgraph 기능이
# 워크플로우 일부를 backend로 unwrap할 때 노출되는 경우가 있음. 우리는 활성 분기 쪽으로
# chain 추적을 이어간다. chain에는 포함시키지 않음 (model에 patch를 가하지 않으므로).
_SWITCH_PASSTHROUGH_TYPES = {
    "ComfySwitchNode",
    "ComfySoftSwitchNode",
}


def _coerce_bool(value: Any) -> bool:
    """Switch 노드의 switch widget 값을 bool로 안전 변환. widget이 직렬화 형태에
    따라 bool / str("true"/"True"/"1") / int 등으로 올 수 있음."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "on")
    if isinstance(value, (int, float)):
        return bool(value)
    return False

# 토큰 치환은 긴 것부터 (짧은 토큰이 긴 토큰의 일부를 먹어버리는 것 방지)
# 예: %scheduler_name 보다 %sampler가 먼저면 %sampler_name 매칭 깨짐
_FILENAME_TOKENS_BY_LENGTH = [
    "%lllite_start_percent",  # 21
    "%lllite_end_percent",    # 19
    "%cfgnorm_strength",      # 17
    "%lllite_strength",       # 16
    "%scheduler_name",        # 15
    "%auraflow_shift",        # 15
    "%basemodelname",         # 14
    "%lora_strength",         # 14
    "%sampler_name",          # 13
    "%counter",
    "%height",
    "%steps",
    "%width",
    "%type",
    "%time",
    "%seed",
    "%lora",
    "%cfg",
]


def _trace_basemodel(prompt: Optional[dict], start_node_id: Optional[str]) -> Optional[str]:
    """내 노드의 'model' 입력에서 시작해서 업스트림 그래프를 거슬러 올라가며
    최초의 알려진 로더 노드를 찾아 모델 파일명을 반환.

    체인 추적 규칙은 _build_model_chain과 동일:
      - LoRA loader, ModelSamplingAuraFlow, CFGNorm 등 중간 노드는 'model' 링크를 따라 통과
      - Context (rgthree) 패스스루: 'model' 입력 우선, 없으면 'base_ctx'로 fallback
      - ComfySwitch 패스스루: 'switch' 값에 따라 'on_true' / 'on_false' 활성 분기로 진행
    이 함수는 추적만 하므로 chain에는 아무것도 쌓지 않고 ID만 따라간다.
    """
    if not prompt or not start_node_id:
        return None
    start_node_id = str(start_node_id)
    if start_node_id not in prompt:
        return None

    inputs = prompt[start_node_id].get("inputs", {})
    model_link = inputs.get("model")
    if not (isinstance(model_link, list) and len(model_link) >= 1):
        print(f"[BMK XY Plot]   trace: 'model' input on node {start_node_id} is unconnected.")
        return None

    current_id = str(model_link[0])
    visited = set()

    for _ in range(50):
        if current_id in visited:
            print(f"[BMK XY Plot]   trace: cycle detected at node {current_id}, stopping.")
            break
        visited.add(current_id)

        node = prompt.get(current_id)
        if not node:
            print(f"[BMK XY Plot]   trace: node id {current_id} not in prompt, stopping.")
            break

        class_type = node.get("class_type", "")
        node_inputs = node.get("inputs", {})

        # 1) 알려진 로더 노드 도달 → 모델 파일명 반환
        if class_type in _LOADER_WIDGET_NAMES:
            widget = _LOADER_WIDGET_NAMES[class_type]
            value = node_inputs.get(widget)
            if isinstance(value, str) and value:
                return value
            print(f"[BMK XY Plot]   trace: loader '{class_type}' (id={current_id}) "
                  f"has no value for widget '{widget}'.")
            break

        # 2) Context (rgthree) 패스스루
        if class_type in _CONTEXT_PASSTHROUGH_TYPES:
            model_in = node_inputs.get("model")
            if isinstance(model_in, list) and len(model_in) >= 1:
                current_id = str(model_in[0])
                continue
            base_in = node_inputs.get("base_ctx")
            if isinstance(base_in, list) and len(base_in) >= 1:
                current_id = str(base_in[0])
                continue
            print(f"[BMK XY Plot]   trace: context node '{class_type}' (id={current_id}) "
                  f"has neither 'model' nor 'base_ctx' connected.")
            break

        # 3) ComfySwitch 패스스루: 활성 분기 쪽으로 진행
        if class_type in _SWITCH_PASSTHROUGH_TYPES:
            switch_val = node_inputs.get("switch", False)
            is_true = _coerce_bool(switch_val)
            active_name = "on_true" if is_true else "on_false"
            active_link = node_inputs.get(active_name)
            if isinstance(active_link, list) and len(active_link) >= 1:
                current_id = str(active_link[0])
                continue
            print(f"[BMK XY Plot]   trace: switch node '{class_type}' (id={current_id}) "
                  f"active branch '{active_name}' is unconnected.")
            break

        # 4) 일반 중간 노드 → 'model' 링크 따라 계속
        next_link = node_inputs.get("model")
        if isinstance(next_link, list) and len(next_link) >= 1:
            current_id = str(next_link[0])
        else:
            print(f"[BMK XY Plot]   trace: node '{class_type}' (id={current_id}) "
                  f"has no upstream 'model' link, stopping.")
            break

    return None


def _resolve_filename_tokens(
    template: str,
    *,
    time_str: str,
    basemodel: str,
    steps: int,
    cfg: float,
    sampler_name: str,
    scheduler_name: str,
    seed: int,
    width: int,
    height: int,
    counter: int,
    file_type: str,
    lora: str = "",
    lora_strength: float = 0.0,
    chain_state: Optional[Dict[str, Dict[str, Any]]] = None,
) -> str:
    """템플릿의 %token을 실제 값으로 치환. 토큰 길이 내림차순으로 처리.

    chain_state: {class_type: {widget_name: value}}. 셀별 chain widget 값.
    """
    # 체인 위젯 토큰 값 추출 (해당 노드/위젯이 없으면 빈 문자열)
    def _chain_val(class_type: str, widget_name: str) -> str:
        if not chain_state:
            return ""
        widgets = chain_state.get(class_type, {})
        if widget_name not in widgets:
            return ""
        return _format_value(widgets[widget_name])

    token_values = {
        "%lllite_start_percent": _chain_val("AnimaLLLiteApply", "start_percent"),
        "%lllite_end_percent":   _chain_val("AnimaLLLiteApply", "end_percent"),
        "%cfgnorm_strength":     _chain_val("CFGNorm", "strength"),
        "%lllite_strength":      _chain_val("AnimaLLLiteApply", "strength"),
        "%scheduler_name":       scheduler_name,
        "%auraflow_shift":       _chain_val("ModelSamplingAuraFlow", "shift"),
        "%basemodelname":        basemodel or "unknown",
        "%lora_strength":        _format_value(lora_strength) if lora else "",
        "%sampler_name":         sampler_name,
        "%counter":              str(counter),
        "%height":               str(height),
        "%steps":                str(steps),
        "%width":                str(width),
        "%type":                 file_type,
        "%time":                 time_str,
        "%seed":                 str(seed),
        "%lora":                 lora or "",
        "%cfg":                  _format_value(cfg),
    }
    out = template
    for token in _FILENAME_TOKENS_BY_LENGTH:
        out = out.replace(token, str(token_values[token]))
    return out


def _resolve_save_dir(subfolder: str) -> str:
    """저장 디렉토리 결정. 빈 값=output 루트, 절대=그대로, 상대=output 하위."""
    output_root = folder_paths.get_output_directory()
    if not subfolder or not subfolder.strip():
        return output_root
    subfolder = subfolder.strip()
    if os.path.isabs(subfolder):
        return subfolder
    return os.path.join(output_root, subfolder)


_FILENAME_FORBIDDEN = ['<', '>', ':', '"', '|', '?', '*', '\x00']


def _sanitize_filename_part(s: str) -> str:
    """파일명에 못 쓰는 문자만 _로 치환. 슬래시는 보존 (하위 폴더 표현용)."""
    for c in _FILENAME_FORBIDDEN:
        s = s.replace(c, "_")
    s = s.strip().rstrip(".")
    return s


def _save_pil_templated(
    pil: Image.Image,
    save_dir: str,
    resolved_filename: str,
    metadata: Optional[PngInfo],
) -> str:
    """이미 토큰 치환이 끝난 filename을 받아서 저장. 템플릿 안의 /는 하위 폴더로 처리."""
    # 템플릿에 슬래시가 있으면 하위 폴더로 분할 (예: "subdir/file" → save_dir/subdir/file.png)
    parts = resolved_filename.replace("\\", "/").split("/")
    parts = [_sanitize_filename_part(p) for p in parts if p]
    if not parts:
        parts = ["unnamed"]
    rel_path = os.path.join(*parts) + ".png"
    full_path = os.path.join(save_dir, rel_path)
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    pil.save(full_path, pnginfo=metadata, compress_level=4)
    return full_path


# ─── 노드 본체 ────────────────────────────────────────────────

class BMKXYPlot:
    TITLE = "XY Plot"
    CATEGORY = "BMK Nodes/XY Plot"
    FUNCTION = "execute"
    RETURN_TYPES = ("IMAGE", "IMAGE", "IMAGE")
    RETURN_NAMES = ("grid", "clean_individuals", "labeled_individuals")
    DESCRIPTION = (
        "Universal XY plot. Supports sampler params (cfg/steps/sampler/scheduler/"
        "seed/denoise), checkpoint, LoRA name/strength, and A1111-style prompt "
        "Search/Replace. Compatible with Anima/Cosmos/Flux/SDXL/SD3 via standard "
        "common_ksampler. Outputs labeled grid + clean/labeled individuals with "
        "A1111/ComfyUI PNG metadata."
    )
    SEARCH_ALIASES = [
        "bmk", "xy plot", "xyz plot", "xy", "xyz",
        "plot", "grid", "comparison", "matrix",
        "a1111 xy", "anima xy", "prompt sr", "search replace",
        "checkpoint compare", "lora compare",
    ]

    @classmethod
    def INPUT_TYPES(cls):
        # 옵션 입력은 캔버스가 열리는 시점에 실행되므로 try/except로 안전망
        try:
            unet_list = ["None"] + folder_paths.get_filename_list("diffusion_models")
        except Exception:
            unet_list = ["None"]
        try:
            lora_list = ["None"] + folder_paths.get_filename_list("loras")
        except Exception:
            lora_list = ["None"]

        return {
            "required": {
                "model": ("MODEL",),
                "positive": ("CONDITIONING",),
                "negative": ("CONDITIONING",),
                "latent": ("LATENT",),
                "vae": ("VAE",),

                "seed": ("INT", {"default": 0, "min": 0, "max": 0xFFFFFFFFFFFFFFFF}),
                "steps": ("INT", {"default": 20, "min": 1, "max": 10000}),
                "cfg": ("FLOAT", {"default": 7.0, "min": 0.0, "max": 100.0, "step": 0.1, "round": 0.01}),
                "sampler_name": (comfy.samplers.KSampler.SAMPLERS,),
                "scheduler": (comfy.samplers.KSampler.SCHEDULERS,),
                "denoise": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 1.0, "step": 0.01}),

                "x_axis": (_AXIS_CHOICES, {"default": "cfg"}),
                "x_values": ("STRING", {
                    "default": "3.5, 5.0, 7.0, 9.0",
                    "multiline": False,
                    "tooltip": "Comma-separated. For *_sr axes: first value is the search term.",
                }),
                "y_axis": (_AXIS_CHOICES, {"default": "sampler_name"}),
                "y_values": ("STRING", {
                    "default": "euler, dpmpp_2m, er_sde",
                    "multiline": False,
                    "tooltip": "Comma-separated. For *_sr axes: first value is the search term.",
                }),

                "draw_grid_labels": ("BOOLEAN", {"default": True}),
                "cell_gap": ("INT", {"default": 8, "min": 0, "max": 128}),
                "label_font_size": ("INT", {"default": 28, "min": 8, "max": 128}),

                "save_clean_individuals": ("BOOLEAN", {"default": True}),
                "save_labeled_individuals": ("BOOLEAN", {"default": True}),
                "save_grid": ("BOOLEAN", {"default": True, "tooltip": "Save the composed grid image too."}),
                "overlay_position": (
                    ["top-left", "top-right", "bottom-left", "bottom-right"],
                    {"default": "top-left"},
                ),
                "overlay_font_size": ("INT", {"default": 28, "min": 8, "max": 128}),

                "filename_template": ("STRING", {
                    "default": "[%time]_[%basemodelname]_[step%steps]_[cfg%cfg]_[%sampler_name]_[%scheduler_name]_[seed%seed]_[%widthx%height]_%counter_%type",
                    "multiline": False,
                    "tooltip": (
                        "Tokens: %time %basemodelname %steps %cfg %sampler_name "
                        "%scheduler_name %seed %width %height %counter %type %lora "
                        "%lora_strength %lllite_strength %lllite_start_percent "
                        "%lllite_end_percent %auraflow_shift %cfgnorm_strength. "
                        "Slashes (/) create subfolders."
                    ),
                }),
                "path": ("STRING", {
                    "default": "",
                    "multiline": False,
                    "tooltip": "Save folder. Empty = ComfyUI output root. Relative path = subfolder under output. Absolute path = use as-is.",
                }),
                "time_format": ("STRING", {
                    "default": "%Y%m%d-%H%M%S",
                    "tooltip": "strftime format for %time token",
                }),

                "embed_a1111_metadata": ("BOOLEAN", {"default": True}),
                "embed_workflow_metadata": ("BOOLEAN", {"default": True}),
            },
            "optional": {
                # ─── 체크포인트 축용 ────────────────────────────
                "unet_name": (unet_list, {
                    "default": "None",
                    "tooltip": "Default UNet for the 'checkpoint' axis. 'None' = use the input 'model'.",
                }),
                "unet_weight_dtype": (
                    ["default", "fp8_e4m3fn", "fp8_e4m3fn_fast", "fp8_e5m2"],
                    {"default": "default"},
                ),

                # ─── LoRA 축용 ──────────────────────────────────
                "clip": ("CLIP", {
                    "tooltip": "Required for LoRA and prompt SR axes. CLIP encoder for re-encoding prompts.",
                }),
                "lora_name": (lora_list, {
                    "default": "None",
                    "tooltip": "Default LoRA. Used when lora_strength axis is active without lora_name axis.",
                }),
                "lora_strength": ("FLOAT", {"default": 1.0, "min": -10.0, "max": 10.0, "step": 0.01}),

                # ─── Prompt SR 축용 / A1111 메타데이터용 ─────────
                "positive_text": ("STRING", {
                    "default": "", "multiline": True,
                    "tooltip": "Base positive prompt. Required for positive_sr axis. Also embedded in A1111 metadata.",
                }),
                "negative_text": ("STRING", {
                    "default": "", "multiline": True,
                    "tooltip": "Base negative prompt. Required for negative_sr axis. Also embedded in A1111 metadata.",
                }),

                # ─── 파일명 토큰 수동 override ──────────────────
                "basemodel_name": ("STRING", {
                    "default": "",
                    "multiline": False,
                    "tooltip": "Override for %basemodelname token. Leave empty to auto-detect by tracing the 'model' input upstream.",
                }),
            },
            "hidden": {
                "prompt": "PROMPT",
                "extra_pnginfo": "EXTRA_PNGINFO",
                "unique_id": "UNIQUE_ID",
            },
        }

    def execute(
        self,
        model, positive, negative, latent, vae,
        seed, steps, cfg, sampler_name, scheduler, denoise,
        x_axis, x_values, y_axis, y_values,
        draw_grid_labels, cell_gap, label_font_size,
        save_clean_individuals, save_labeled_individuals, save_grid,
        overlay_position, overlay_font_size,
        filename_template, path, time_format,
        embed_a1111_metadata, embed_workflow_metadata,
        unet_name="None", unet_weight_dtype="default",
        clip=None, lora_name="None", lora_strength=1.0,
        positive_text="", negative_text="",
        basemodel_name="",
        prompt=None, extra_pnginfo=None, unique_id=None,
    ):
        # ─── 입력 검증 ─────────────────────────────────────────
        if x_axis == "none" and y_axis == "none":
            raise ValueError("[BMK XY Plot] At least one axis must be selected.")
        if x_axis == y_axis and x_axis != "none":
            raise ValueError("[BMK XY Plot] X and Y axes must be different.")

        x_vals = _parse_values(x_axis, x_values)
        y_vals = _parse_values(y_axis, y_values)
        _validate_combo_values(x_axis, x_vals)
        _validate_combo_values(y_axis, y_vals)

        # 어떤 종류의 동적 처리가 필요한지 판단
        axes_set = {x_axis, y_axis}
        use_checkpoint = "checkpoint" in axes_set
        use_lora = bool(axes_set & {"lora_name", "lora_strength"})
        use_pos_sr = "positive_sr" in axes_set
        use_neg_sr = "negative_sr" in axes_set
        active_chain_widget_axes = axes_set & _CHAIN_WIDGET_AXES
        use_chain_widget = bool(active_chain_widget_axes)
        needs_reencode = use_lora or use_pos_sr or use_neg_sr
        needs_chain_replay = use_checkpoint or use_chain_widget

        # 축 표시명 (lora_strength 축에 LoRA 이름을 박아 식별성 확보)
        x_axis_display = _axis_display_name(x_axis, lora_name, axes_set)
        y_axis_display = _axis_display_name(y_axis, lora_name, axes_set)

        # 필수 의존성 검증
        if needs_reencode and clip is None:
            raise ValueError(
                "[BMK XY Plot] 'clip' input is required when using LoRA or prompt SR axes. "
                "Connect your CLIP loader output to the 'clip' optional input."
            )
        if use_pos_sr and not positive_text:
            raise ValueError(
                "[BMK XY Plot] 'positive_text' is required when using positive_sr axis."
            )
        if use_neg_sr and not negative_text:
            raise ValueError(
                "[BMK XY Plot] 'negative_text' is required when using negative_sr axis."
            )
        # lora_strength만 축으로 쓰는데 기본 lora_name이 없는 경우
        if "lora_strength" in axes_set and "lora_name" not in axes_set:
            if lora_name == "None":
                raise ValueError(
                    "[BMK XY Plot] When using lora_strength axis alone, set a default "
                    "'lora_name' in the optional inputs (the LoRA to vary the strength of)."
                )

        # 체인 위젯 상태 스캔: 체인 위젯 노드가 있으면 축으로 안 쓰더라도 메타데이터에 박힘.
        # _scan_chain_widget_state는 prompt dict만 읽고 노드 실행은 안 하므로 매우 가벼움.
        chain_base_state = _scan_chain_widget_state(prompt, unique_id)

        # 체인 위젯 축 검증: 대상 노드가 업스트림 체인에 실제로 존재하는지 확인
        if use_chain_widget:
            for axis_type in active_chain_widget_axes:
                req_class, req_widget = _CHAIN_WIDGET_AXES_MAP[axis_type]
                if req_class not in chain_base_state or req_widget not in chain_base_state[req_class]:
                    raise ValueError(
                        f"[BMK XY Plot] Axis '{axis_type}' requires a '{req_class}' node "
                        f"with widget '{req_widget}' in the model chain upstream of this node, "
                        f"but none was found. Connect your chain through that node, or pick a "
                        f"different axis."
                    )

        # S/R용 검색어 (각 축의 첫 값)
        sr_search_x = x_vals[0] if x_axis in _SR_AXES else None
        sr_search_y = y_vals[0] if y_axis in _SR_AXES else None

        total_iters = len(x_vals) * len(y_vals)
        pbar = comfy.utils.ProgressBar(total_iters)
        print(
            f"[BMK XY Plot] Running {len(x_vals)} x {len(y_vals)} = {total_iters} "
            f"iterations  (X: {x_axis}, Y: {y_axis})"
        )
        if needs_reencode:
            print(f"[BMK XY Plot]   re-encoding prompts each iteration "
                  f"(lora={use_lora}, pos_sr={use_pos_sr}, neg_sr={use_neg_sr})")
        if use_chain_widget:
            print(f"[BMK XY Plot]   chain-widget axes active: {sorted(active_chain_widget_axes)}")
            print(f"[BMK XY Plot]   chain base widget state: {chain_base_state}")

        base_params: Dict[str, Any] = {
            "seed": seed, "steps": steps, "cfg": cfg,
            "sampler_name": sampler_name, "scheduler": scheduler,
            "denoise": denoise,
        }

        # 시드가 축이 아닐 때는 시작 시점에 한 번만 해석 (모든 셀이 동일 시드)
        if "seed" not in axes_set:
            base_params["seed"] = _resolve_seed(base_params["seed"])

        # 개별 저장 경로 + 타임스탬프 + 베이스모델 해석 (run 시작 시 한 번만)
        save_any_ind = save_clean_individuals or save_labeled_individuals
        save_dir = _resolve_save_dir(path)

        # 모든 파일이 같은 timestamp를 공유 (run 식별성)
        try:
            time_str = datetime.datetime.now().strftime(time_format)
        except (ValueError, TypeError):
            time_str = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")

        # 베이스모델 이름 해석 (우선순위: 수동 override > 그래프 자동 추적 > 노드의 unet_name 위젯)
        resolved_basemodel = (basemodel_name or "").strip()
        if resolved_basemodel:
            print(f"[BMK XY Plot] using manual basemodel_name override: {resolved_basemodel}")
        else:
            traced = _trace_basemodel(prompt, unique_id)
            if traced:
                resolved_basemodel = traced
                print(f"[BMK XY Plot] auto-detected basemodel from upstream: {traced}")
            elif unet_name and unet_name != "None":
                # 폴백: 'model' 입력이 unconnected이거나 추적이 실패한 경우, 노드의
                # unet_name 위젯값을 사용 (체크포인트 축이 활성화돼 있지 않은 한 모든 셀이
                # 같은 모델을 쓰므로 이 값이 정확함).
                resolved_basemodel = unet_name
                print(f"[BMK XY Plot] using node's unet_name widget as basemodel: {unet_name}")
            else:
                print(f"[BMK XY Plot] basemodel auto-detection failed; %basemodelname will be 'unknown' "
                      f"(set 'basemodel_name' input to override, or connect a known loader upstream)")

        # 셀 카운터 (run 내부에서만 사용; run 간 충돌은 %time 토큰으로 회피)
        counter = 0

        # 체크포인트 / 체인-위젯 체인 리플레이 결과 캐시.
        # key: (checkpoint_or_None, dtype, frozenset(((class, widget), value), ...)) → (model, clip)
        # 같은 (checkpoint + 위젯 오버라이드 조합)이 여러 셀에 등장해도 디스크 로딩 + 체인
        # 재실행을 1회로 줄임. 체크포인트 축이 활성화돼 있지 않으면 첫 요소는 None.
        ChainCacheKey = Tuple[Optional[str], str, frozenset]
        chain_cache: Dict[ChainCacheKey, Tuple[Any, Any]] = {}
        chain_replay_disabled = False  # 한 번 실패하면 이번 run은 fallback 모드로

        def _overrides_key(ovr: Dict[str, Dict[str, Any]]) -> frozenset:
            flat = []
            for cls_name, widgets in ovr.items():
                for w, v in widgets.items():
                    flat.append(((cls_name, w), v))
            return frozenset(flat)

        def get_model_for_state(
            checkpoint: str,
            chain_overrides: Dict[str, Dict[str, Any]],
        ) -> Tuple[Any, Any]:
            """(체크포인트, 체인 위젯 오버라이드) 조합에 대해 (model, clip) 반환.

            - checkpoint='None' AND chain_overrides 비어있음 → 입력 그대로 반환 (리플레이 X)
            - 그 외 → 체인 리플레이 (체크포인트가 'None'이면 None 전달해서 원래 위젯 유지)
            - 리플레이 실패 시: 체크포인트 축만 활성화면 단순 UNet 로드로 fallback
              체인 위젯 축이 활성화돼 있으면 fallback 불가 → 에러
            """
            nonlocal chain_replay_disabled
            no_ckpt_change = (checkpoint == "None" or not checkpoint)
            no_overrides = not chain_overrides

            if no_ckpt_change and no_overrides:
                return model, clip

            ckpt_key: Optional[str] = None if no_ckpt_change else checkpoint
            key: ChainCacheKey = (ckpt_key, unet_weight_dtype, _overrides_key(chain_overrides))
            if key in chain_cache:
                return chain_cache[key]

            # 체인 리플레이 우선 시도
            if not chain_replay_disabled:
                try:
                    ck_msg = f"checkpoint={checkpoint}" if not no_ckpt_change else "checkpoint=keep"
                    print(f"[BMK XY Plot]   chain replay ({ck_msg}, overrides={chain_overrides or 'none'})")
                    m, c, status = _execute_checkpoint_chain(
                        prompt, unique_id, ckpt_key, unet_weight_dtype, clip,
                        chain_widget_overrides=chain_overrides or None,
                        fallback_vae=vae,
                    )
                    print(f"[BMK XY Plot]   {status}")
                    chain_cache[key] = (m, c)
                    return m, c
                except RuntimeError as e:
                    # 체인 리플레이가 실패하면 이 run의 모든 후속 셀이 fallback 모드로
                    # 들어가서 LoRA / 모델 패치가 전부 우회됨. 사용자가 이 경고를 보고
                    # 원인 노드를 진단할 수 있도록 한 번 크게 찍어준다.
                    print(f"[BMK XY Plot] !!! CHAIN REPLAY FAILED !!! {e}")
                    # 체인 위젯 축이 활성화된 경우엔 fallback 불가
                    if use_chain_widget:
                        raise RuntimeError(
                            f"[BMK XY Plot] Chain replay failed and a chain-widget axis is active. "
                            f"Chain-widget axes ({sorted(active_chain_widget_axes)}) require successful "
                            f"chain replay; cannot fall back to direct UNet load. Underlying error: {e}"
                        )
                    print(f"[BMK XY Plot] !!! Falling back to direct UNet load. "
                          f"Upstream LoRA / model patches (ModelSamplingAuraFlow, CFGNorm, etc.) "
                          f"will be BYPASSED for ALL cells in this run. !!!")
                    chain_replay_disabled = True

            # Fallback: 단순 UNet 로드 (체인 우회) — 체크포인트 축 단독일 때만 도달
            print(f"[BMK XY Plot]   direct UNet load (no upstream patches): "
                  f"{checkpoint} (dtype={unet_weight_dtype})")
            m = _load_unet(checkpoint, unet_weight_dtype)
            chain_cache[key] = (m, clip)
            return m, clip

        # ─── 반복 실행 ─────────────────────────────────────────
        pil_results: List[Image.Image] = []
        labeled_results: List[Image.Image] = []

        for y_idx, yv in enumerate(y_vals):
            for x_idx, xv in enumerate(x_vals):
                params = dict(base_params)

                # 1) 샘플러 파라미터 오버라이드
                if x_axis in _SAMPLER_AXES:
                    params[x_axis] = xv
                if y_axis in _SAMPLER_AXES:
                    params[y_axis] = yv

                # 시드가 축일 때만 셀별로 해석 (-1 = 랜덤)
                if "seed" in axes_set:
                    params["seed"] = _resolve_seed(params["seed"])

                # 2) 체크포인트 + 체인-위젯 오버라이드 결정 (체인 리플레이로 LoRA/패처 유지)
                current_unet = unet_name
                if x_axis == "checkpoint": current_unet = xv
                if y_axis == "checkpoint": current_unet = yv

                # 체인 위젯 오버라이드: {class_type: {widget_name: value}}
                # 베이스는 체인의 원래 위젯 상태 → 축으로 활성화된 위젯만 셀별로 덮어씀
                cell_chain_overrides: Dict[str, Dict[str, Any]] = {}
                if use_chain_widget:
                    for axis_type, axis_val in ((x_axis, xv), (y_axis, yv)):
                        if axis_type in _CHAIN_WIDGET_AXES:
                            cls_name, wname = _CHAIN_WIDGET_AXES_MAP[axis_type]
                            cell_chain_overrides.setdefault(cls_name, {})[wname] = axis_val

                if needs_chain_replay:
                    iter_model, iter_clip = get_model_for_state(current_unet, cell_chain_overrides)
                else:
                    iter_model = model
                    iter_clip = clip

                # 이 셀에서 실제로 사용된 체인 위젯 값들 (메타데이터/파일명 토큰용).
                # 베이스 상태 위에 셀 오버라이드를 머지한다.
                cell_chain_state: Dict[str, Dict[str, Any]] = {}
                for cls_name, widgets in chain_base_state.items():
                    cell_chain_state[cls_name] = dict(widgets)
                for cls_name, widgets in cell_chain_overrides.items():
                    cell_chain_state.setdefault(cls_name, {}).update(widgets)

                # 3) LoRA 결정 & 적용 (체인 리플레이 결과 위에 추가로 stack)
                current_lora_name = lora_name
                current_lora_strength = lora_strength
                if x_axis == "lora_name": current_lora_name = xv
                if y_axis == "lora_name": current_lora_name = yv
                if x_axis == "lora_strength": current_lora_strength = xv
                if y_axis == "lora_strength": current_lora_strength = yv

                if use_lora:
                    iter_model, iter_clip = _apply_lora(
                        iter_model, iter_clip, current_lora_name, current_lora_strength
                    )

                # 4) Prompt SR 결정 & 인코딩
                if needs_reencode:
                    # 재인코딩이 필요한 경우 텍스트로부터 다시 생성
                    cur_pos_text = positive_text
                    cur_neg_text = negative_text

                    if use_pos_sr:
                        sr_repl = xv if x_axis == "positive_sr" else yv
                        cur_pos_text = _apply_sr(cur_pos_text, sr_search_x or sr_search_y, sr_repl)
                    if use_neg_sr:
                        sr_repl = xv if x_axis == "negative_sr" else yv
                        cur_neg_text = _apply_sr(cur_neg_text, sr_search_x or sr_search_y, sr_repl)

                    iter_positive = _encode_text(iter_clip, cur_pos_text)
                    iter_negative = _encode_text(iter_clip, cur_neg_text)
                else:
                    # 변경 없음 → 입력 conditioning 그대로
                    iter_positive = positive
                    iter_negative = negative
                    cur_pos_text = positive_text
                    cur_neg_text = negative_text

                # 5) ★ ComfyUI 표준 common_ksampler ★ (Anima/Cosmos 호환 핵심)
                out_latent_tuple = nodes.common_ksampler(
                    iter_model,
                    params["seed"],
                    params["steps"],
                    params["cfg"],
                    params["sampler_name"],
                    params["scheduler"],
                    iter_positive, iter_negative,
                    latent,
                    denoise=params["denoise"],
                )
                out_latent = out_latent_tuple[0]

                # 6) VAE decode
                decoded = vae.decode(out_latent["samples"])
                pil = _tensor_to_pil(decoded[0])
                pil_results.append(pil)

                # 7) 라벨 텍스트
                label_parts = []
                if x_axis != "none":
                    label_parts.append(f"{x_axis_display}: {_format_value(xv)}")
                if y_axis != "none":
                    label_parts.append(f"{y_axis_display}: {_format_value(yv)}")
                label_text = " | ".join(label_parts)
                overlay_lines = "\n".join(label_parts)

                print(f"[BMK XY Plot]  cell ({x_idx},{y_idx})  {label_text}")

                # 8) labeled 버전 항상 생성 (출력 포트 + 옵션 저장 모두 활용)
                labeled_pil = _apply_overlay(
                    pil, overlay_lines, overlay_position, overlay_font_size,
                )
                labeled_results.append(labeled_pil)

                # 9) 개별 저장 (옵션, 템플릿 기반 파일명)
                if save_any_ind:
                    # 이 셀의 basemodel (체크포인트 축이 있으면 셀별로 다름)
                    cell_basemodel = resolved_basemodel
                    if use_checkpoint and current_unet and current_unet != "None":
                        cell_basemodel = current_unet

                    # 체인 extras: 셀별 실제 사용값을 A1111 메타데이터에 한 줄로 박음
                    cell_chain_extras = _format_chain_extras(cell_chain_state) if cell_chain_state else None

                    metadata = _make_metadata(
                        params, cur_pos_text, cur_neg_text,
                        pil.width, pil.height,
                        prompt, extra_pnginfo,
                        embed_a1111_metadata, embed_workflow_metadata,
                        model_name=current_unet if current_unet != "None" else None,
                        lora_name=current_lora_name if use_lora else None,
                        lora_strength=current_lora_strength if use_lora else None,
                        chain_extras=cell_chain_extras,
                    )

                    common_tokens = dict(
                        time_str=time_str,
                        basemodel=_format_value(cell_basemodel) if cell_basemodel else "",
                        steps=params["steps"],
                        cfg=params["cfg"],
                        sampler_name=params["sampler_name"],
                        scheduler_name=params["scheduler"],
                        seed=params["seed"],
                        width=pil.width,
                        height=pil.height,
                        counter=counter,
                        lora=_format_value(current_lora_name) if (use_lora and current_lora_name != "None") else "",
                        lora_strength=current_lora_strength if use_lora else 0.0,
                        chain_state=cell_chain_state,
                    )

                    if save_clean_individuals:
                        fname = _resolve_filename_tokens(
                            filename_template, file_type="clean", **common_tokens,
                        )
                        _save_pil_templated(pil, save_dir, fname, metadata)
                    if save_labeled_individuals:
                        fname = _resolve_filename_tokens(
                            filename_template, file_type="labeled", **common_tokens,
                        )
                        _save_pil_templated(labeled_pil, save_dir, fname, metadata)
                    counter += 1

                pbar.update(1)

                # 메모리 정리 힌트 (체크포인트/체인 위젯 전환이 잦을 때 유용)
                if needs_chain_replay:
                    comfy.model_management.soft_empty_cache()

        # ─── 그리드 합성 ──────────────────────────────────────
        grid_pil = _compose_grid(
            pil_results, x_vals, y_vals, x_axis, y_axis,
            x_axis_display, y_axis_display,
            draw_grid_labels, cell_gap, label_font_size,
        )
        grid_tensor = _pil_to_tensor(grid_pil)

        # ─── 그리드 저장 (옵션) ───────────────────────────────
        grid_saved_path = None
        if save_grid:
            # 그리드 메타데이터: 베이스 파라미터로 (셀이 여러 개라 단일 값 표기 불가능한 항목은 기본값)
            # 체인 위젯 축이 활성화된 경우, 그리드 메타엔 베이스 위젯 값을 넣음 (셀별 값은 개별 이미지에)
            grid_chain_extras = _format_chain_extras(chain_base_state) if chain_base_state else None
            grid_metadata = _make_metadata(
                base_params, positive_text, negative_text,
                grid_pil.width, grid_pil.height,
                prompt, extra_pnginfo,
                embed_a1111_metadata, embed_workflow_metadata,
                model_name=resolved_basemodel if resolved_basemodel else None,
                lora_name=lora_name if (lora_name and lora_name != "None") else None,
                lora_strength=lora_strength if (lora_name and lora_name != "None") else None,
                chain_extras=grid_chain_extras,
            )
            grid_fname = _resolve_filename_tokens(
                filename_template,
                time_str=time_str,
                basemodel=_format_value(resolved_basemodel) if resolved_basemodel else "",
                steps=base_params["steps"],
                cfg=base_params["cfg"],
                sampler_name=base_params["sampler_name"],
                scheduler_name=base_params["scheduler"],
                seed=base_params["seed"],
                width=grid_pil.width,
                height=grid_pil.height,
                counter=0,
                file_type="grid",
                lora=_format_value(lora_name) if (lora_name and lora_name != "None") else "",
                lora_strength=lora_strength if (lora_name and lora_name != "None") else 0.0,
                chain_state=chain_base_state,
            )
            grid_saved_path = _save_pil_templated(grid_pil, save_dir, grid_fname, grid_metadata)

        # ─── 개별 이미지 배치 텐서 (출력 포트용) ────────────────
        # _pil_to_tensor가 [1,H,W,3]을 반환하므로 dim=0으로 concat → [N,H,W,3]
        clean_batch = torch.cat([_pil_to_tensor(p) for p in pil_results], dim=0)
        labeled_batch = torch.cat([_pil_to_tensor(p) for p in labeled_results], dim=0)

        summary_parts = [f"Grid: {grid_pil.width}x{grid_pil.height}px",
                         f"batch: {clean_batch.shape[0]} cells"]
        if save_any_ind:
            summary_parts.append(f"saved {counter} individual pair(s) to {save_dir}")
        if grid_saved_path:
            summary_parts.append(f"grid saved: {os.path.basename(grid_saved_path)}")
        print(f"[BMK XY Plot] Done. " + ", ".join(summary_parts))
        return (grid_tensor, clean_batch, labeled_batch)


NODE_CLASS_MAPPINGS = {
    "BMKXYPlot": BMKXYPlot,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "BMKXYPlot": "XY Plot",
}
