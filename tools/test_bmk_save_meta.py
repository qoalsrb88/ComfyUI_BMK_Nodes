# -*- coding: utf-8 -*-
r"""bmk_save_meta 자체 테스트.

실행 (ComfyUI-Easy-Install 루트에서):
    python_embeded\python.exe ComfyUI\custom_nodes\ComfyUI_BMK_Nodes\tools\test_bmk_save_meta.py

worktree 처럼 ComfyUI 밖에 있는 사본은 COMFYUI_DIR 로 ComfyUI 폴더를 알려 준다.

refs 파싱, on_prompt 링크 컴파일(ksampler 묶음, model·loras·te·vae 체인, SamplerCustomAdvanced,
서브그래프 범위, 재컴파일), Meta 값 조립(meta_in 체인, 유선 우선), 파일명 렌더링,
A1111 parameters, 해석 미리보기·실행 후 UI, 실제 PNG 저장(청크·중복 이름·하위 폴더)을 검사한다.
노드 클래스 목록(nodes 모듈)은 가짜로 바꿔 끼운다.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import os
import sys
import tempfile
import types

from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

_HERE = Path(__file__).resolve()
COMFY = os.environ.get("COMFYUI_DIR") or str(_HERE.parents[3])  # .../ComfyUI
MODULE = str(_HERE.parents[1] / "bmk_save_meta.py")
sys.path.insert(0, COMFY)

import torch  # noqa: E402
from PIL import Image  # noqa: E402


class _Loader:
    RETURN_TYPES = ("MODEL",)


class _LoraManagerLoader:
    RETURN_TYPES = ("MODEL", "CLIP", "STRING", "STRING")
    RETURN_NAMES = ("MODEL", "CLIP", "trigger_words", "loaded_loras")


class _Wildcard:
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("text",)


class _Seed:
    RETURN_TYPES = ("INT",)


class _PrimitiveString:
    RETURN_TYPES = ("STRING",)


class _PrimitiveFloat:
    RETURN_TYPES = ("FLOAT",)


class _CyclicSeed:
    RETURN_TYPES = ("INT", "STRING")
    RETURN_NAMES = ("seed", "seed_text")


class _LoraStacker:
    RETURN_TYPES = ("LORA_STACK", "STRING", "STRING")
    RETURN_NAMES = ("LORA_STACK", "trigger_words", "active_loras")


class _Switch:
    RETURN_TYPES = ("COMFY_MATCHTYPE_V3",)
    RETURN_NAMES = ("output",)


class _ClipLoader:
    RETURN_TYPES = ("CLIP",)


class _VaeLoader:
    RETURN_TYPES = ("VAE",)


class _CheckpointLoader:
    RETURN_TYPES = ("MODEL", "CLIP", "VAE")


class _Sampler:
    RETURN_TYPES = ("LATENT",)


fake_nodes = types.ModuleType("nodes")
fake_nodes.NODE_CLASS_MAPPINGS = {
    "UNETLoader": _Loader,
    "LoraLoaderModelOnly": _Loader,
    "LoraManagerLoader": _LoraManagerLoader,
    "Lora Loader (LoraManager)": _LoraManagerLoader,
    "Lora Stacker (LoraManager)": _LoraStacker,
    "SkipLayerGuidanceDiTSimple": _Loader,
    "ComfySwitchNode": _Switch,
    "PrimitiveStringMultiline": _PrimitiveString,
    "PrimitiveFloat": _PrimitiveFloat,
    "BMKCyclicSeed": _CyclicSeed,
    "Wildcard": _Wildcard,
    "SeedNode": _Seed,
    "CLIPLoader": _ClipLoader,
    "DualCLIPLoader": _ClipLoader,
    "TripleCLIPLoader": _ClipLoader,
    "QuadrupleCLIPLoader": _ClipLoader,
    "VAELoader": _VaeLoader,
    "CheckpointLoaderSimple": _CheckpointLoader,
    "KSampler": _Sampler,
    "SamplerCustomAdvanced": _Sampler,
}
sys.modules["nodes"] = fake_nodes

import folder_paths  # noqa: E402
from comfy_api.latest import io  # noqa: E402

spec = importlib.util.spec_from_file_location("bmk_save_meta", MODULE)
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)

FAILS: list[str] = []
PASSES = 0


class _Warnings(logging.Handler):
    def __init__(self):
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record):
        self.messages.append(record.getMessage())


WARNINGS = _Warnings()
mod.logger.addHandler(WARNINGS)


def check(cond, msg):
    global PASSES
    if cond:
        PASSES += 1
    else:
        FAILS.append(msg)
        print("FAIL:", msg)


def eq(actual, expected, msg):
    check(actual == expected, f"{msg}\n    기대: {expected!r}\n    실제: {actual!r}")


# ─── 그래프 ──────────────────────────────────────────────────────

def make_prompt(refs: str, meta_id: str = "9") -> dict:
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "anima/fnMixAnimaTurbo_v31.safetensors"}},
        "2": {"class_type": "LoraLoaderModelOnly",
              "inputs": {"model": ["1", 0], "lora_name": "styles/NikkeB1.safetensors", "strength_model": 0.8}},
        "12": {"class_type": "LoraManagerLoader", "inputs": {"model": ["2", 0], "text": ""}},
        "10": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["12", 0], "shift": 1.5}},
        "15": {"class_type": "CFGNorm", "inputs": {"model": ["10", 0], "strength": 1.0}},
        "40": {"class_type": "Wildcard", "inputs": {"wildcard_text": "__x__"}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": ["40", 0]}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "lowres, bad hands"}},
        "25": {"class_type": "SeedNode", "inputs": {}},
        "3": {"class_type": "KSampler", "inputs": {
            "model": ["15", 0], "positive": ["6", 0], "negative": ["7", 0], "seed": ["25", 0],
            "steps": 16, "cfg": 1.5, "sampler_name": "er_sde", "scheduler": "simple", "denoise": 1.0,
            "latent_image": ["5", 0]}},
        "31": {"class_type": "RandomNoise", "inputs": {"noise_seed": 77}},
        "32": {"class_type": "CFGGuider", "inputs": {"model": ["15", 0], "positive": ["6", 0],
                                                      "negative": ["7", 0], "cfg": 3.5}},
        "33": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
        "34": {"class_type": "BasicScheduler", "inputs": {"model": ["15", 0], "scheduler": "normal",
                                                           "steps": 20, "denoise": 0.6}},
        "30": {"class_type": "SamplerCustomAdvanced", "inputs": {
            "noise": ["31", 0], "guider": ["32", 0], "sampler": ["33", 0], "sigmas": ["34", 0]}},
        "20:3": {"class_type": "KSampler", "inputs": {"seed": 5, "steps": 8, "cfg": 2.0,
                                                       "sampler_name": "euler_a", "scheduler": "karras"}},
        meta_id: {"class_type": "BMKSaveMeta", "inputs": {"refs": refs}},
    }


def compile_slots(refs: str, meta_id: str = "9") -> dict:
    prompt = make_prompt(refs, meta_id)
    mod.compile_refs_in_prompt({"prompt": prompt})
    return {k: v for k, v in prompt[meta_id]["inputs"].items() if k.startswith(mod._REF_PREFIX)}


RUNTIME = {
    ("40", 0): "1girl, solo, <lora:NikkeB1:0.8>",
    ("25", 0): 1016,
    ("12", 3): "<lora:Extra:0.6> <lora:Two.v1.5:0.5:0.4>",
}


def run_meta(refs: str, meta_in=None, **wired) -> dict:
    """컴파일 → 실행기처럼 링크를 런타임 값으로 바꿔 Meta.execute 를 부른다."""
    kwargs = {}
    for name, value in compile_slots(refs).items():
        kwargs[name] = RUNTIME[(value[0], value[1])] if isinstance(value, list) else value
    kwargs.update(wired)
    out = mod.BMKSaveMeta.execute(refs, mod._DEFAULT_TEMPLATE, "", mod._DEFAULT_TIME_FORMAT,
                                  "one line", True, True, meta_in=meta_in, **kwargs)
    return out.args[0]


# ─── 1. 파싱 ─────────────────────────────────────────────────────

lines, errors = mod._parse_refs(
    "# 주석\n"
    "Seed: 3\n"
    "prompt: 6\n"
    "Sft: 10.shift   # 꼬리 주석\n"
    'Note: "a, b: c"  # 주석\n'
    "loras: 2, 12\n"
    "ksampler: 20:3\n"
    "bad line\n"
    "time: 3\n"
    "steps: abc\n"
)
eq([(k, s) for k, s, _ in lines], [
    ("seed", [("ref", "3", None)]),
    ("positive", [("ref", "6", None)]),
    ("Sft", [("ref", "10", "shift")]),
    ("Note", [("lit", "a, b: c")]),
    ("loras", [("ref", "2", None), ("ref", "12", None)]),
    ("ksampler", [("ref", "20:3", None)]),
], "파싱: 표준 키 소문자·별칭, 커스텀 대소문자, 리터럴, 다중 loras, 서브그래프 ID")
eq(errors, ["bad line", "time: 3", "steps: abc"], "파싱: 읽을 수 없는 줄과 예약 키")


# ─── 2. 컴파일 ───────────────────────────────────────────────────

P = mod._REF_PREFIX
slots = compile_slots("ksampler: 3\nSft: 10.shift\nNorm: 15.strength\nloras_out: 12.loaded_loras")
eq(slots.get(P + "seed|0|value"), ["25", 0], "묶음: 링크인 seed 는 링크로")
eq(slots.get(P + "steps|0|value"), 16, "묶음: steps 리터럴")
eq(slots.get(P + "sampler|0|value"), "er_sde", "묶음: sampler_name")
eq(slots.get(P + "positive|0|value"), ["40", 0], "묶음: positive 링크 → text 링크")
eq(slots.get(P + "negative|0|value"), "lowres, bad hands", "묶음: negative → text")
eq(slots.get(P + "model|0|value"), "anima/fnMixAnimaTurbo_v31.safetensors", "묶음: model 체인 → unet_name")
eq(slots.get(P + "loras|0|name"), "styles/NikkeB1.safetensors", "묶음: loras 체인 — LoraLoader 가 먼저")
eq(slots.get(P + "loras|0|weight"), 0.8, "묶음: LoraLoader strength_model")
eq(slots.get(P + "loras|1|value"), ["12", 3], "묶음: LoraManager loaded_loras 출력")
eq(slots.get(P + "Sft|0|value"), 1.5, "커스텀: 노드ID.위젯")
eq(slots.get(P + "loras_out|0|value"), ["12", 3], "커스텀: 노드ID.출력 이름")

slots = compile_slots("ksampler: 30")
eq((slots.get(P + "seed|0|value"), slots.get(P + "steps|0|value"), slots.get(P + "cfg|0|value"),
    slots.get(P + "sampler|0|value"), slots.get(P + "scheduler|0|value"), slots.get(P + "denoise|0|value"),
    slots.get(P + "positive|0|value"), slots.get(P + "model|0|value")),
   (77, 20, 3.5, "euler", "normal", 0.6, ["40", 0], "anima/fnMixAnimaTurbo_v31.safetensors"),
   "SamplerCustomAdvanced: noise·guider·sampler·sigmas 를 한 단계씩 따라간다")

slots = compile_slots("ksampler: 3\nsteps: 34\nseed: \"42\"")
eq((slots.get(P + "steps|0|value"), slots.get(P + "seed|0|value")), (20, "42"), "따로 적은 키가 묶음을 이긴다")

slots = compile_slots("seed: 99\nSft: 10\nshift: 10\nmodel: 2.nothing")
eq(sorted(slots), [P + "shift|0|value"], "없는 노드·이름은 넣지 않고, 커스텀 노드ID 는 키와 같은 입력만 찾는다")

prompt = make_prompt("a: 6.clip\nb: 7.list\nc: 9.meta\nd: 8.meta")
prompt["6"]["inputs"]["clip"] = ["99", 0]
prompt["7"]["inputs"]["list"] = ["x", 1]
prompt["8"] = {"class_type": "Other", "inputs": {"meta": ["9", 0]}}
mod.compile_refs_in_prompt({"prompt": prompt})
check(not any(k.startswith(P) for k in prompt["9"]["inputs"]),
      "없는 노드로 가는 링크, 리스트 리터럴, Meta 를 가리키는 링크는 넣지 않는다")

prompt = make_prompt("positive: 6\nksampler: 3")
prompt["9"]["inputs"]["positive"] = ["21", 0]
prompt["9"]["inputs"]["steps"] = ["22", 0]
mod.compile_refs_in_prompt({"prompt": prompt})
injected = {k.split("|")[1] for k in prompt["9"]["inputs"] if k.startswith(P)}
check("positive" not in injected and "steps" not in injected and "seed" in injected,
      f"유선이 이어진 키는 참조를 넣지 않는다(쓰지 않을 노드를 실행하지 않게)\n    {sorted(injected)}")

slots = compile_slots("ksampler: 3", meta_id="20:9")
eq((slots.get(P + "steps|0|value"), slots.get(P + "sampler|0|value")), (8, "euler_a"),
   "서브그래프 안 Meta 는 같은 서브그래프의 번호를 먼저 찾는다")
slots = compile_slots("ksampler: 20:3")
eq(slots.get(P + "scheduler|0|value"), "karras", "루트에서 서브그래프 노드를 직접 가리킨다")

prompt = make_prompt("steps: 3")
prompt["9"]["inputs"][P + "stale|0|value"] = "old"
mod.compile_refs_in_prompt({"prompt": prompt})
mod.compile_refs_in_prompt({"prompt": prompt})
eq(sorted(k for k in prompt["9"]["inputs"] if k.startswith(P)), [P + "steps|0|value"],
   "재컴파일: 남은 숨은 입력을 지우고 다시 만든다")

prompt = make_prompt("steps: 3")
prompt["9"]["inputs"]["refs"] = ["40", 0]
mod.compile_refs_in_prompt({"prompt": prompt})
check(not any(k.startswith(P) for k in prompt["9"]["inputs"]), "refs 가 링크면 컴파일하지 않는다")

plain = {"prompt": {"1": {"class_type": "KSampler", "inputs": {"steps": 3}}}}
mod.compile_refs_in_prompt(plain)
eq(plain, {"prompt": {"1": {"class_type": "KSampler", "inputs": {"steps": 3}}}}, "Meta 가 없는 프롬프트는 그대로")
eq(mod.compile_refs_in_prompt({"client_id": "x"}), {"client_id": "x"}, "prompt 가 없는 요청도 그대로")


# ─── 2-1. 사용자 워크플로 구조 (MOSS 서브그래프, 8188 실행 기록의 API 프롬프트) ──

def user_prompt(refs: str) -> dict:
    return {
        "46": {"class_type": "PrimitiveStringMultiline", "inputs": {"value": "A photorealistic photograph"}},
        "56": {"class_type": "PrimitiveStringMultiline", "inputs": {"value": "anime, 2d illustration"}},
        "51": {"class_type": "BMKCyclicSeed", "inputs": {"mode": "control_after_generate", "base_seed": 1}},
        "55": {"class_type": "Lora Stacker (LoraManager)", "inputs": {"text": "<lora:a:1>", "loras": {"__value__": []}}},
        "77": {"class_type": "PrimitiveFloat", "inputs": {"value": 0.5}},
        "61:31": {"class_type": "UNETLoader", "inputs": {"unet_name": "Moss\\mossImage_sft_bf16.safetensors"}},
        "61:50": {"class_type": "Lora Loader (LoraManager)",
                  "inputs": {"text": "", "loras": {"__value__": []}, "model": ["61:31", 0], "lora_stack": ["55", 0]}},
        "61:41": {"class_type": "SkipLayerGuidanceDiTSimple", "inputs": {"model": ["61:50", 0], "single_layers": "22"}},
        "61:90": {"class_type": "LoraLoaderModelOnly",
                  "inputs": {"model": ["61:50", 0], "lora_name": "unused.safetensors", "strength_model": 1.0}},
        "61:42": {"class_type": "ComfySwitchNode", "inputs": {"switch": True, "on_false": ["61:90", 0], "on_true": ["61:41", 0]}},
        "61:37": {"class_type": "ComfySwitchNode", "inputs": {"switch": False, "on_false": ["46", 0], "on_true": ["61:53", 0]}},
        "61:32": {"class_type": "CLIPTextEncode", "inputs": {"text": ["61:37", 0], "clip": ["61:33", 0]}},
        "61:39": {"class_type": "CLIPTextEncode", "inputs": {"text": ["56", 0], "clip": ["61:33", 0]}},
        "61:33": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen3.5_9b_int8_convrot.safetensors"}},
        "61:35": {"class_type": "VAELoader", "inputs": {"vae_name": "flux2-vae_small.safetensors"}},
        "61:40": {"class_type": "VAEDecode", "inputs": {"samples": ["61:34", 0], "vae": ["61:35", 0]}},
        "61:34": {"class_type": "KSampler", "inputs": {
            "seed": ["51", 0], "steps": 25, "cfg": 3.0, "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0,
            "model": ["61:42", 0], "positive": ["61:32", 0], "negative": ["61:39", 0], "latent_image": ["61:38", 0]}},
        "61:66": {"class_type": "BMKSaveMeta", "inputs": {"refs": refs}},
    }


def user_slots(refs: str) -> dict:
    prompt = user_prompt(refs)
    mod.compile_refs_in_prompt({"prompt": prompt})
    return {k[len(P):]: v for k, v in prompt["61:66"]["inputs"].items() if k.startswith(P)}


slots = user_slots("KSampler: 34\npositive: 46\nnegative: 56\nloras: 55")
eq((slots.get("positive|0|value"), slots.get("negative|0|value"), slots.get("loras|0|value")),
   (["46", 0], ["56", 0], ["55", 0]),
   "사용자 refs: 서브그래프 밖 Primitive 프롬프트는 STRING 출력, 스태커는 LORA_STACK 출력")
eq(slots.get("model|0|value"), "Moss\\mossImage_sft_bf16.safetensors", "사용자 refs: model 은 스위치 갈래를 지나 UNETLoader 까지")
eq(slots.get("seed|0|value"), ["51", 0], "사용자 refs: 시드는 루트 시드 노드 링크")

slots = user_slots("KSampler: 34")
eq((slots.get("positive|0|value"), slots.get("negative|0|value"), slots.get("loras|0|value"),
    any(k.startswith("loras|1|") for k in slots)),
   (["61:37", 0], ["56", 0], ["61:50", 3], False),
   "묶음만: 실제 인코딩한 텍스트(스위치 출력), 고른 갈래의 loaded_loras 만(안 고른 갈래 LoRA 제외)")

slots = user_slots("seed: 51\nSLG: 41.single_layers\nScale: 77")
eq((slots.get("seed|0|value"), slots.get("SLG|0|value"), slots.get("Scale|0|value")),
   (["51", 0], "22", ["77", 0]), "시드 노드는 INT 출력, 커스텀 노드ID 는 하나뿐인 출력")

prompt = user_prompt("KSampler: 34")
prompt["61:42"]["inputs"]["switch"] = ["51", 0]
mod.compile_refs_in_prompt({"prompt": prompt})
check(not any(k.startswith((P + "loras|", P + "model|")) for k in prompt["61:66"]["inputs"]),
      "switch 가 링크면 갈래를 정할 수 없어 멈춘다(안 쓸 로더를 실행시키지 않게)")

prompt = user_prompt("KSampler: 34")
prompt["61:43"] = {"class_type": "ComfySwitchNode", "inputs": {"switch": False, "on_false": ["61:41", 0], "on_true": ["61:90", 0]}}
prompt["61:42"]["inputs"]["on_true"] = ["61:43", 0]
mod.compile_refs_in_prompt({"prompt": prompt})
eq((prompt["61:66"]["inputs"].get(P + "loras|0|value"), prompt["61:66"]["inputs"].get(P + "model|0|value")),
   (["61:50", 3], "Moss\\mossImage_sft_bf16.safetensors"), "리터럴 스위치 두 개가 이어져도 고른 갈래로 건너간다")

prompt = user_prompt("KSampler: 34\nsteps: 80\ndenoise: 80")
prompt["80"] = {"class_type": "Context", "inputs": {"seed": 123, "steps": 20, "cfg": 3.0, "denoise": 0.6,
                                                    "model_raw": ["61:90", 0], "model": ["61:41", 0]}}
prompt["61:34"]["inputs"]["model"] = ["80", 0]
fake_nodes.NODE_CLASS_MAPPINGS["Context"] = type("_Context", (), {
    "RETURN_TYPES": ("MODEL", "INT", "INT", "FLOAT", "FLOAT"),
    "RETURN_NAMES": ("model", "seed", "steps", "cfg", "denoise")})
mod.compile_refs_in_prompt({"prompt": prompt})
inputs = prompt["61:66"]["inputs"]
eq((inputs.get(P + "steps|0|value"), inputs.get(P + "denoise|0|value"), inputs.get(P + "loras|0|value"),
    any(k.startswith(P + "loras|1|") for k in inputs)),
   (["80", 2], ["80", 4], ["61:50", 3], False),
   "같은 타입 출력이 여럿이면 이름이 같은 출력, MODEL 입력이 여럿이면 model 입력만 따라간다")

out = mod.BMKSaveMeta.execute("loras: 55, 50", mod._DEFAULT_TEMPLATE, "", "T", "one line", True, True, **{
    P + "loras|0|value": [("D:\\loras\\cine-moss.safetensors", 0.3, 0.3)],
    P + "loras|1|value": "<lora:cine-moss:0.3> <lora:extra:1> <lora:extra:0.5>",
})
eq([(e["name"], e["weight"]) for e in out.args[0]["values"]["loras"]],
   [("cine-moss", 0.3), ("extra", 1.0), ("extra", 0.5)],
   "LORA_STACK 파싱, 스태커와 로더가 같은 항목을 함께 보고하면 한 번, 강도가 다르면 둘 다")


# ─── 2-2. 텍스트 인코더·VAE ──────────────────────────────────────

def prompt_slots(prompt: dict, node_id: str = "61:66") -> dict:
    mod.compile_refs_in_prompt({"prompt": prompt})
    return {k[len(P):]: v for k, v in prompt[node_id]["inputs"].items() if k.startswith(P)}


def execute_prompt(prompt: dict, runtime=None, meta_in=None):
    """실제 실행기 대신 지정한 출력 값만 대입한다. 모델·서비스는 실행하지 않는다."""
    refs = prompt["61:66"]["inputs"]["refs"]
    slots = prompt_slots(prompt)
    kwargs = {P + key: (runtime[(value[0], value[1])] if isinstance(value, list) else value)
              for key, value in slots.items()}
    return mod.BMKSaveMeta.execute(refs, mod._DEFAULT_TEMPLATE, "", "T", "one line", True, True,
                                  meta_in=meta_in, **kwargs)


slots = user_slots("KSampler: 34")
eq((slots.get("te|0|value"), slots.get("vae|0|value")),
   ("qwen3.5_9b_int8_convrot.safetensors", "flux2-vae_small.safetensors"),
   "사용자 묶음: positive 인코더의 CLIP 과 samples 를 받는 디코더의 VAE")
eq(user_slots("TEXT_ENCODER: 33\nVAE: 35"),
   {"te|0|value": "qwen3.5_9b_int8_convrot.safetensors", "vae|0|value": "flux2-vae_small.safetensors"},
   "te 별칭·대소문자, 로더 직접 참조")
eq(user_slots("te: 32\nvae: 40"), user_slots("te: 33\nvae: 35"), "인코더·디코더에서 이름을 역추적")

prompt = user_prompt('KSampler: 34\ntext_encoder: "enc/explicit.safetensors"\nvae: "vae/explicit.pt"')
prompt["61:66"]["inputs"]["steps"] = ["51", 0]
slots = prompt_slots(prompt)
eq((slots.get("te|0|value"), slots.get("vae|0|value"), "steps|0|value" in slots),
   ("enc/explicit.safetensors", "vae/explicit.pt", False), "별도 te·vae 는 묶음보다 우선, 기존 유선도 우선")

for count, loader in ((2, "DualCLIPLoader"), (3, "TripleCLIPLoader"), (4, "QuadrupleCLIPLoader")):
    prompt = user_prompt("te: 34\nvae: 40")
    prompt["61:33"] = {"class_type": loader, "inputs": {
        f"clip_name{i}": ["46", 0] if i == 2 else f"enc/e{i}.v1.safetensors" for i in range(1, count + 1)}}
    prompt["61:35"]["inputs"]["vae_name"] = ["56", 0]
    slots = prompt_slots(prompt)
    eq([slots.get(f"te|{i}|value") for i in range(count)],
       [["46", 0] if i == 1 else f"enc/e{i + 1}.v1.safetensors" for i in range(count)],
       f"{loader}: 리터럴·링크 이름을 각 숨은 그룹으로 주입")
    out = execute_prompt(prompt, {("46", 0): "enc\\e2.v1.gguf", ("56", 0): "vae\\v1.pt"})
    eq((out.args[0]["values"]["te"], out.args[0]["values"]["vae"]),
       (" + ".join(f"e{i}.v1" for i in range(1, count + 1)), "v1"),
       f"{loader}: 실행된 이름 각각의 경로·확장자를 제거하고 순서대로 합침")

prompt = user_prompt("te: 34\nvae: 34")
prompt["61:80"] = {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "built-in.safetensors"}}
prompt["61:32"]["inputs"]["clip"] = ["61:80", 1]
prompt["61:40"]["inputs"]["vae"] = ["61:80", 2]
eq(prompt_slots(prompt), {}, "체크포인트 내장 CLIP·VAE 이름은 생략")

prompt = user_prompt("te: 30\nvae: 30")
prompt["61:30"] = {"class_type": "SamplerCustomAdvanced", "inputs": {"guider": ["61:81", 0]}}
prompt["61:81"] = {"class_type": "CFGGuider", "inputs": {"positive": ["61:32", 0]}}
prompt["61:40"]["inputs"]["samples"] = ["61:30", 0]
eq(prompt_slots(prompt), user_slots("te: 33\nvae: 35"), "Custom 샘플러의 guider → positive 와 출력 디코더")

for key, component, start, loader_id in (("te", "clip", "61:32", "61:33"),
                                       ("vae", "vae", "61:40", "61:35")):
    prompt = user_prompt(f"{key}: {start}")
    original = prompt_slots(deepcopy(prompt))
    prompt[start]["inputs"][component] = ["61:80", 0]
    prompt["61:80"] = {"class_type": "ComfySwitchNode", "inputs": {
        "switch": True, "on_true": ["61:81", 0], "on_false": ["999", 0]}}
    prompt["61:81"] = {"class_type": "ComfySwitchNode", "inputs": {
        "switch": False, "on_false": [loader_id, 0], "on_true": ["999", 0]}}
    eq(prompt_slots(prompt), original, f"{key}: 중첩 리터럴 스위치의 고른 갈래만 추적")
    prompt["61:80"]["inputs"].update(switch=["51", 0], on_true=[loader_id, 0])
    eq(prompt_slots(prompt), {}, f"{key}: 타입이 맞는 갈래가 하나라도 조건이 링크면 추측하지 않음")
    prompt["61:80"] = {"class_type": "Wrapper", "inputs": {"renamed": [loader_id, 0]}}
    eq(prompt_slots(prompt), original, f"{key}: 이름이 달라도 타입 입력이 하나면 추적")
    prompt["61:80"]["inputs"]["other"] = [loader_id, 0]
    eq(prompt_slots(prompt), {}, f"{key}: 같은 타입 입력이 둘이면 멈춤")
    prompt["61:80"]["inputs"][component] = [loader_id, 0]
    eq(prompt_slots(prompt), original, f"{key}: 같은 이름 입력은 여러 타입 후보보다 우선")
    prompt["61:80"]["inputs"] = {component: ["61:80", 0]}
    eq(prompt_slots(prompt), {}, f"{key}: 순환 링크에서 중단")
    prompt["61:80"]["inputs"] = {component: ["999", 0]}
    eq(prompt_slots(prompt), {}, f"{key}: 없는 노드에서 중단")
    for i in range(mod._MAX_CHAIN):
        prompt[str(1000 + i)] = {"class_type": "Wrapper", "inputs": {component: [str(1001 + i), 0]}}
    prompt[str(1000 + mod._MAX_CHAIN)] = deepcopy(prompt[loader_id])
    prompt[start]["inputs"][component] = ["1000", 0]
    eq(prompt_slots(prompt), {}, f"{key}: 최대 체인 길이를 넘기면 중단")

prompt = user_prompt("vae: 34")
prompt["61:41"] = {"class_type": "VAEDecodeTiled", "inputs": {"samples": ["61:34", 0], "vae": ["61:36", 0]}}
prompt["61:36"] = deepcopy(prompt["61:35"])
eq(prompt_slots(prompt), {"vae|0|value": "flux2-vae_small.safetensors"}, "여러 디코더의 VAE 리터럴 이름이 같으면 기록")
prompt["61:36"]["inputs"]["vae_name"] = "different.safetensors"
eq(prompt_slots(prompt), {}, "여러 디코더의 VAE 이름이 다르면 생략")
prompt["61:36"]["inputs"]["vae_name"] = ["46", 0]
eq(prompt_slots(prompt), {}, "여러 디코더 중 VAE 이름 링크가 있으면 생략")
prompt["61:35"]["inputs"]["vae_name"] = ["46", 0]
eq(prompt_slots(prompt), {}, "여러 디코더가 같은 이름 링크여도 실행 전 비교하지 않음")
prompt["61:41"]["inputs"]["vae"] = ["999", 0]
eq(prompt_slots(prompt), {}, "여러 디코더 중 VAE 를 해석 못 한 경우도 생략")
del prompt["61:41"]
eq(prompt_slots(prompt), {"vae|0|value": ["46", 0]}, "디코더가 하나면 VAE 이름 링크를 그대로 주입")
prompt["61:40"]["inputs"]["samples"] = ["61:34", 1]
eq(prompt_slots(prompt), {}, "샘플러의 LATENT 출력 슬롯만 디코더 탐색에 사용")

prompt = user_prompt("te: 33\nvae: 34")
prompt["33"] = {"class_type": "CLIPLoader", "inputs": {"clip_name": "root.safetensors"}}
prompt["34"] = {"class_type": "KSampler", "inputs": {}}
prompt["35"] = {"class_type": "VAELoader", "inputs": {"vae_name": "root.safetensors"}}
prompt["40"] = {"class_type": "VAEDecode", "inputs": {"samples": ["34", 0], "vae": ["35", 0]}}
eq(prompt_slots(prompt), user_slots("te: 33\nvae: 35"), "서브그래프의 같은 번호가 우선, 다른 샘플러의 디코더는 제외")
prompt["61:66"]["inputs"]["refs"] = "te: 33.clip_name\nvae: 35.vae_name"
eq(prompt_slots(prompt), user_slots("te: 33\nvae: 35"), "새 키도 노드ID.입력 이름 문법 유지")

schema = mod.BMKSaveMeta.define_schema()
eq([item.id for item in schema.inputs],
   ["refs", "filename", "path", "time_format", "loras_in_prompt", "a1111_metadata", "embed_workflow", "meta_in",
    "positive", "negative", "model", "loras", "seed", "steps", "cfg", "sampler", "scheduler", "denoise"],
   "기존 입력 목록·순서 유지, te·vae 소켓 없음")


# ─── 3. Meta 값 조립 ─────────────────────────────────────────────

meta = run_meta("ksampler: 3\nSft: 10.shift\nNorm: 15.strength\nMissing: 99.x")
values = meta["values"]
eq(values["positive"], "1girl, solo, <lora:NikkeB1:0.8>", "런타임 positive")
eq(values["seed"], 1016, "런타임 seed")
eq(values["model"], "fnMixAnimaTurbo_v31", "모델 이름은 폴더·확장자를 뗀다")
eq(values["loras"], [
    {"name": "NikkeB1", "weight": 0.8, "clip": None},
    {"name": "Extra", "weight": 0.6, "clip": None},
    {"name": "Two.v1.5", "weight": 0.5, "clip": 0.4},
], "LoRA: 체인 순서, loaded_loras 파싱, 이름 속 점 보존")
eq(values["Missing"], None, "해석 못 한 커스텀 키는 None (파일명 토큰은 빈 값)")
eq(list(values)[-3:], ["Sft", "Norm", "Missing"], "커스텀 키는 refs 순서")
eq(meta["settings"]["loras_in_prompt"], "one line", "설정 전달")

chained = run_meta("steps: 34\ndenoise: 34\nSft: \"2.0\"", meta_in=meta)["values"]
eq((chained["steps"], chained["denoise"], chained["Sft"], chained["seed"]), (20, 0.6, "2.0", 1016),
   "meta_in: 물려받고 얻은 값만 덮어쓴다")
kept = run_meta("Sft: 99.x", meta_in=meta)["values"]
eq(kept["Sft"], 1.5, "meta_in: 해석 못 한 값은 물려받은 값을 지우지 않는다")

WARNINGS.messages.clear()
run_meta("ksampler: 999\nSft: 10.shift")
check(any("ksampler: 999" in m for m in WARNINGS.messages), f"묶음이 아무것도 못 찾으면 경고\n    {WARNINGS.messages}")
WARNINGS.messages.clear()
mod.BMKSaveMeta.execute("positive: 6", mod._DEFAULT_TEMPLATE, "", "T", "one line", True, True, positive="wired")
eq(WARNINGS.messages, [], "유선이 이긴 키는 참조가 없어도 경고하지 않는다")

wired = run_meta("ksampler: 3", steps=30, model="other/Model.v2.safetensors", loras="<lora:W:1>",
                 cfg=torch.zeros(1, 2))["values"]
eq((wired["steps"], wired["model"], wired["loras"], "cfg" in wired),
   (30, "Model.v2", [{"name": "W", "weight": 1.0, "clip": None}], False),
   "유선: 참조보다 우선, 텐서 같은 객체는 경고하고 버린다(그 키의 참조는 컴파일되지 않으므로)")


# ─── 4. 파일명 ───────────────────────────────────────────────────

values = dict(meta["values"])
tokens = mod._filename_tokens(values, "260820-181420", 1728, 1344, "01.basic")
name = mod._sanitize(mod._render_filename(
    mod._DEFAULT_TEMPLATE.replace("_[%postfix]", "_[Sft%Sft]_[Norm%Norm]_[%postfix]"), tokens))
eq(name, "[260820-181420]_[fnMixAnimaTurbo_v31]_[step16]_[cfg1.5]_[er_sde]_[simple]_[seed1016]_"
         "[1728x1344]_[Sft1.5]_[Norm1]_[01.basic]", "기본 템플릿 + 커스텀 토큰")

tokens = mod._filename_tokens({"seed": 7}, "T", 512, 768, "")
eq(mod._render_filename(mod._DEFAULT_TEMPLATE, tokens), "[T]_[seed7]_[512x768]",
   "값이 모두 빈 묶음은 구분자와 함께 지운다")
eq(mod._render_filename("[%postfix]-[%seed] [lit]_[%model]", tokens), "[7] [lit]",
   "맨 앞 빈 묶음, 토큰 없는 묶음은 그대로")

tokens = mod._filename_tokens({"S": "x", "Sft": 2.0, "Path": "a/b\\c"}, "T", 1, 1, "")
eq(mod._render_filename("%S_%Sft_%Path", tokens), "x_2_a_b_c", "긴 토큰 먼저, 값 속 / 는 _")
eq(mod._sanitize(mod._render_filename("out/%time/[%seed]x", tokens)), "out/T/x", "템플릿 / 는 하위 폴더")
eq(mod._sanitize('../a:b/./c?. /..'), "a_b/c_", "금지문자, . .. 세그먼트 제거")
eq(mod._sanitize("CON/aux.txt/com1x/LPT9 "), "_CON/_aux.txt/com1x/_LPT9", "Windows 예약 이름 피하기")
capped = mod._sanitize("😀" * 128)
check(len(capped.encode("utf-16-le")) // 2 <= mod._MAX_SEGMENT and capped.startswith("😀"),
      f"UTF-16 단위로 길이 제한 ({len(capped.encode('utf-16-le')) // 2})")


# ─── 5. A1111 ────────────────────────────────────────────────────

values = {
    "positive": "1girl, <lora:nikkeb1:0.8>",
    "negative": "lowres",
    "loras": [{"name": "NikkeB1", "weight": 0.8, "clip": None},
              {"name": "Extra", "weight": 0.6, "clip": 0.4}],
    "steps": 16, "sampler": "er_sde", "scheduler": "simple", "cfg": 1.5, "seed": 1016,
    "model": "fnMixAnimaTurbo_v31", "denoise": 1.0,
    "Sft": 1.5, "SLG": "[7, 8, 9]", "Empty": None,
}
eq(mod._a1111_text(values, "one line", 1728, 1344),
   "1girl, <lora:nikkeb1:0.8>\n<lora:Extra:0.6:0.4>\nNegative prompt: lowres\n"
   'Steps: 16, Sampler: er_sde, Schedule type: simple, CFG scale: 1.5, Seed: 1016, Size: 1728x1344, '
   'Model: fnMixAnimaTurbo_v31, Sft: 1.5, SLG: "[7, 8, 9]", Version: ComfyUI',
   "A1111: 프롬프트에 있는 LoRA 는 빼고, denoise 1 생략, 쉼표 값은 따옴표")
values["loras"].append({"name": "Third", "weight": 1.0, "clip": 1.0})
values["denoise"] = 0.55
text = mod._a1111_text(values, "one per line", 64, 64)
check(text.startswith("1girl, <lora:nikkeb1:0.8>\n<lora:Extra:0.6:0.4>\n<lora:Third:1>\nNegative prompt:"),
      f"A1111: 로라마다 줄바꿈, 같은 클립 강도는 생략\n    {text!r}")
check("Denoising strength: 0.55" in text, "A1111: denoise 1 이 아니면 기록")
check("Denoising strength: 0.55" in mod._a1111_text({"denoise": "0.55"}, "none", 8, 8),
      "A1111: 따옴표로 쓴 denoise 도 숫자로 보고 기록")
eq(mod._a1111_text({"positive": "a", "loras": values["loras"]}, "none", 8, 8).splitlines(),
   ["a", "Size: 8x8, Version: ComfyUI"], "A1111: none 이면 LoRA 를 붙이지 않고 빈 값은 생략")


# ─── 6. 실제 저장 ────────────────────────────────────────────────

with tempfile.TemporaryDirectory() as tmp:
    folder_paths.set_output_directory(tmp)
    hidden = {io.Hidden.prompt: {"9": {"class_type": "BMKSaveMeta"}},
              io.Hidden.extra_pnginfo: {"workflow": {"nodes": ["한글"]}}}
    saver = mod.BMKSaveImage.PREPARE_CLASS_CLONE({"hidden_inputs": hidden})
    images = torch.rand(2, 24, 40, 3)
    meta["settings"] = dict(meta["settings"], path="bmk/../test", filename="%seed/%time_[%postfix]",
                            time_format="T")
    out = saver.execute(images, postfix="a/b", meta=meta)
    eq(out.ui["images"], [
        {"filename": "T_[a_b].png", "subfolder": "bmk/test/1016", "type": "output"},
        {"filename": "T_[a_b]_02.png", "subfolder": "bmk/test/1016", "type": "output"},
    ], "저장: 하위 폴더(.. 제거), 값 속 / 치환, 배치 중복 이름 _02")
    png = Image.open(os.path.join(tmp, "bmk", "test", "1016", "T_[a_b].png"))
    eq(png.size, (40, 24), "저장: 이미지 크기")
    check(png.text.get("parameters", "").endswith("Size: 40x24, Model: fnMixAnimaTurbo_v31, Sft: 1.5, "
                                                  "Norm: 1, Version: ComfyUI"),
          f"저장: parameters 청크\n    {png.text.get('parameters')!r}")
    eq(json.loads(png.text.get("workflow", "null")), {"nodes": ["한글"]}, "저장: workflow 청크")
    eq(json.loads(png.text.get("prompt", "null")), {"9": {"class_type": "BMKSaveMeta"}}, "저장: prompt 청크")
    png.close()

    meta["settings"] = dict(meta["settings"], a1111_metadata=False, embed_workflow=False)
    saver.execute(images[:1], postfix="", meta=meta)
    png = Image.open(os.path.join(tmp, "bmk", "test", "1016", "T.png"))
    eq(dict(png.text), {}, "저장: 두 설정을 끄면 청크 없음")
    png.close()

    out = saver.execute(images[:1])
    files = out.ui["images"]
    check(len(files) == 1 and files[0]["subfolder"] == "" and files[0]["filename"].count("[") == 2,
          f"저장: meta 없이 기본 템플릿(시간·크기만)\n    {files}")
    png = Image.open(os.path.join(tmp, files[0]["filename"]))
    check("parameters" not in png.text and "workflow" in png.text, "저장: meta 없으면 워크플로만")
    png.close()


# ─── 7. 새 키의 파일명·A1111 표기 ────────────────────────────────

tokens = mod._filename_tokens({"te": "clip1 + clip2", "vae": "vae.v1"}, "T", 8, 8, "")
eq(mod._render_filename("[%te]_[%vae]", tokens), "[clip1 + clip2]_[vae.v1]", "te·vae 파일명 토큰")
tokens = mod._filename_tokens({}, "T", 8, 8, "")
eq(mod._render_filename("[%time]_[te%te]-[vae%vae]", tokens), "[T]", "없는 te·vae 의 빈 묶음 제거")
eq(mod._a1111_text({"model": "m", "vae": "v,1", "te": "clip:1 + clip2", "denoise": 0.5}, "none", 8, 8),
   '\nSize: 8x8, Model: m, VAE: "v,1", Text encoder: "clip:1 + clip2", Denoising strength: 0.5, Version: ComfyUI',
   "A1111: Model → VAE → Text encoder 순서와 기존 따옴표 규칙")
eq(mod._a1111_text({"model": "m", "vae": "", "te": None}, "none", 8, 8),
   "\nSize: 8x8, Model: m, Version: ComfyUI", "A1111: 빈 te·vae 생략")


# ─── 8. 미리보기 응답·라우트 (실제 HTTP 없음) ─────────────────────

refs = '# 주석\n\nKSampler: 34\npositive: 99\nbad line\nsteps: 34\nwidth: 34\nNote: "메모"'
prompt = user_prompt(refs)
prompt["51"]["_meta"] = {"title": "Seed"}
prompt["61:66"]["inputs"].update(steps=["51", 0], **{P + "stale|0|value": "old"})
payload = {"prompt": prompt, "node_id": "61:66"}
before = deepcopy(payload)
preview = mod.resolve_refs(payload)
eq(payload, before, "미리보기는 refs·숨은 입력을 포함해 요청 프롬프트를 바꾸지 않음")
eq(preview["node_found"], True, "미리보기: 서브그래프 Meta 찾음")
eq([(line["line"], line["key"], line["status"]) for line in preview["lines"]], [
    ("KSampler: 34", "ksampler", "ok"), ("positive: 99", "positive", "missing"),
    ("bad line", None, "invalid"), ("steps: 34", "steps", "wired"),
    ("width: 34", "width", "invalid"), ('Note: "메모"', "Note", "ok"),
], "미리보기: 빈 줄·주석만 제외하고 문법 오류·예약 키·유선·미해석 줄 순서 유지")
bundle_fields = preview["lines"][0]["fields"]
check({"key": "seed", "part": "value", "kind": "link", "node": "51", "class_type": "BMKCyclicSeed",
       "title": "Seed", "output": "seed"} in bundle_fields, "미리보기 링크: 노드 ID·클래스·제목·출력 이름")
check({"key": "loras", "part": "value", "kind": "link", "node": "61:50",
       "class_type": "Lora Loader (LoraManager)", "title": None, "output": "loaded_loras"} in bundle_fields,
      "미리보기 링크: 제목이 없으면 null")
check({"key": "te", "part": "value", "kind": "literal", "value": "qwen3.5_9b_int8_convrot.safetensors"}
      in bundle_fields, "미리보기 리터럴: 실행 전에 원본 이름을 보여 줌")
check(all(not line["fields"] for line in preview["lines"][1:5]), "미해석·문법 오류·유선 줄은 fields 가 빈 배열")
eq(preview["lines"][-1]["fields"], [{"key": "Note", "part": "value", "kind": "literal", "value": "메모"}],
   "미리보기: 커스텀 키 대소문자와 리터럴 유지")
compiled = prompt_slots(deepcopy(prompt))
preview_slots = []
for line in preview["lines"]:
    for field in line["fields"]:
        value = field["value"] if field["kind"] == "literal" else [
            field["node"], mod._output_names(field["class_type"]).index(field["output"])]
        preview_slots.append((field["key"], field["part"], value))
eq(preview_slots, [(name.rsplit("|", 2)[0], name.rsplit("|", 2)[2], value) for name, value in compiled.items()],
   "미리보기의 모든 필드가 실제 on_prompt 주입 결과와 일치")

override = dict(payload, refs="TEXT_ENCODER: 33\nvae: 35")
eq([line["key"] for line in mod.resolve_refs(override)["lines"]], ["te", "vae"], "요청 refs 가 노드의 refs 보다 우선")
eq(mod.resolve_refs(dict(payload, refs="")), {"node_found": True, "lines": []}, "빈 refs 덮어쓰기")
eq(payload, before, "여러 번 미리보기 후에도 원본 프롬프트 보존")
for node_id in ("999", "51", None):
    eq(mod.resolve_refs(dict(payload, node_id=node_id)), {"node_found": False, "lines": []},
       f"없는 ID·다른 클래스·누락 ID 응답: {node_id}")
prompt = user_prompt("Extra: 88.value")
prompt["88"] = {"class_type": "Other", "inputs": {"value": ["89", 3]}}
prompt["89"] = {"class_type": "Unknown", "inputs": {}}
eq(mod.resolve_refs({"prompt": prompt, "node_id": "61:66"})["lines"][0]["fields"],
   [{"key": "Extra", "part": "value", "kind": "link", "node": "89", "class_type": "Unknown",
     "title": None, "output": "3"}], "출력 이름을 모르면 슬롯 번호 문자열")
prompt["61:66"]["inputs"]["refs"] = ["46", 0]
eq(mod.resolve_refs({"prompt": prompt, "node_id": "61:66"}), {"node_found": True, "lines": []},
   "refs 가 링크면 큐 컴파일처럼 해석하지 않음")

prompt = make_prompt("loras: 2")
eq(mod.resolve_refs({"prompt": prompt, "node_id": "9"})["lines"][0]["fields"], [
    {"key": "loras", "part": "name", "kind": "literal", "value": "styles/NikkeB1.safetensors"},
    {"key": "loras", "part": "weight", "kind": "literal", "value": 0.8},
], "LoRA 이름·강도 숨은 입력마다 미리보기 필드를 하나씩 반환")
prompt = user_prompt("te: 33")
prompt["61:33"] = {"class_type": "DualCLIPLoader", "inputs": {
    "clip_name1": "one.safetensors", "clip_name2": ["46", 0]}}
eq([field["kind"] for field in mod.resolve_refs({"prompt": prompt, "node_id": "61:66"})["lines"][0]["fields"]],
   ["literal", "link"], "DualCLIP 미리보기: 이름마다 리터럴·링크 종류를 따로 표시")


class _Request:
    def __init__(self, body):
        self.body = body

    async def json(self):
        return json.loads(self.body)


response = asyncio.run(mod._resolve_request(_Request(json.dumps(payload))))
eq((response.status, json.loads(response.text)), (200, preview), "라우트 래퍼: 순수 응답 함수를 200 JSON 으로 반환")
response = asyncio.run(mod._resolve_request(_Request(json.dumps(dict(payload, node_id="999")))))
eq((response.status, json.loads(response.text)), (200, {"node_found": False, "lines": []}), "라우트: 없는 ID 도 200")
for body in ('{"prompt":', 'null', '{"prompt": []}'):
    response = asyncio.run(mod._resolve_request(_Request(body)))
    check(response.status == 400 and isinstance(json.loads(response.text).get("error"), str),
          f"깨진 JSON·잘못된 요청 객체는 400 error: {body}")

check(not getattr(mod._register_routes, "_done", False), "서버 없는 단독 import 는 라우트 등록을 건너뜀")
handlers = []
routes = mod.web.RouteTableDef()
server = types.SimpleNamespace(PromptServer=types.SimpleNamespace(instance=types.SimpleNamespace(
    routes=routes, add_on_prompt_handler=handlers.append)))
with patch.dict(sys.modules, {"server": server}):
    route_spec = importlib.util.spec_from_file_location("_bmk_save_meta_routes", MODULE)
    route_mod = importlib.util.module_from_spec(route_spec)
    route_spec.loader.exec_module(route_mod)
    route_mod._register_routes()
eq([(route.method, route.path) for route in routes], [("POST", "/bmk/save_meta/resolve")],
   "서버가 있으면 import 시 POST 라우트를 한 번만 등록")
eq(handlers, [route_mod.compile_refs_in_prompt], "import 시 기존 on_prompt 핸들러도 유지")


# ─── 9. 실행 후 UI ───────────────────────────────────────────────

inherited = {"values": {"seed": 42, "Keep": "상속", "Missing": "보존", "Empty": None,
                        "Flag": False, "Zero": 0, "cfg": 2.5,
                        "loras": [{"name": "a", "weight": 1.0, "clip": None},
                                  {"name": "b", "weight": 0.5, "clip": 0.25}]}}
before = deepcopy(inherited)
WARNINGS.messages.clear()
out = mod.BMKSaveMeta.execute('bad line\nMissing: 99\nwidth: 3\nsteps: 99\nksampler: 999',
                              mod._DEFAULT_TEMPLATE, "", "T", "one line", True, True,
                              meta_in=inherited, steps=17)
eq(out.ui, {"bmk_save_meta": [{"values": {
    "seed": "42", "Keep": "상속", "Missing": "보존", "Empty": "", "Flag": "off", "Zero": "0", "cfg": "2.5",
    "loras": "<lora:a:1> <lora:b:0.5:0.25>", "steps": "17",
}, "missing": ["bad line", "Missing: 99", "width: 3", "ksampler: 999"]}]},
   "UI: 리스트 한 겹, 상속 포함 최종 표시 문자열·LoRA 공백 결합, 유선 제외 미해석·문법 오류 줄")
check(all(any(line in warning for warning in WARNINGS.messages) for line in out.ui["bmk_save_meta"][0]["missing"]),
      "UI missing 은 이번 실행의 경고에 나온 refs 줄")
eq(inherited, before, "실행 후 UI 생성도 meta_in 을 바꾸지 않음")
eq(out.args[0]["values"]["seed"], 42, "UI 문자열 변환이 실제 meta 값의 타입을 바꾸지 않음")
out = execute_prompt(user_prompt("te: 33\nvae: 35\nAbsent: 999"))
eq(out.ui["bmk_save_meta"], [{"values": {"te": "qwen3.5_9b_int8_convrot", "vae": "flux2-vae_small", "Absent": ""},
                            "missing": ["Absent: 999"]}], "UI: 새 키는 실행 후 정리된 이름, 없는 값은 빈 문자열")
out = mod.BMKSaveMeta.execute("", mod._DEFAULT_TEMPLATE, "", "T", "one line", True, True)
eq(out.ui, {"bmk_save_meta": [{"values": {}, "missing": []}]}, "값이 없어도 UI 계약 유지")


print(f"\n{PASSES} passed, {len(FAILS)} failed")
sys.exit(1 if FAILS else 0)
