# -*- coding: utf-8 -*-
r"""bmk_save_meta 자체 테스트.

실행 (ComfyUI-Easy-Install 루트에서):
    python_embeded\python.exe ComfyUI\custom_nodes\ComfyUI_BMK_Nodes\tools\test_bmk_save_meta.py

worktree 처럼 ComfyUI 밖에 있는 사본은 COMFYUI_DIR 로 ComfyUI 폴더를 알려 준다.

refs 파싱, on_prompt 링크 컴파일(ksampler 묶음, model·loras 체인, SamplerCustomAdvanced,
서브그래프 범위, 재컴파일), Meta 값 조립(meta_in 체인, 유선 우선), 파일명 렌더링,
A1111 parameters, 실제 PNG 저장(청크·중복 이름·하위 폴더)을 검사한다.
노드 클래스 목록(nodes 모듈)은 가짜로 바꿔 끼운다.
"""
from __future__ import annotations

import importlib.util
import json
import logging
import os
import sys
import tempfile
import types

from pathlib import Path

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
lora_parts = sorted(k for k in prompt["61:66"]["inputs"] if k.startswith(P + "loras|"))
eq(len({k.split("|")[2] for k in lora_parts}), 2, "switch 가 링크면 두 갈래를 다 본다")

out = mod.BMKSaveMeta.execute("loras: 55, 50", mod._DEFAULT_TEMPLATE, "", "T", "one line", True, True, **{
    P + "loras|0|value": [("D:\\loras\\cine-moss.safetensors", 0.3, 0.3)],
    P + "loras|1|value": "<lora:cine-moss:0.3> <lora:extra:1>",
})
eq([e["name"] for e in out.args[0]["values"]["loras"]], ["cine-moss", "extra"],
   "LORA_STACK 파싱, 스태커와 로더를 함께 적어도 같은 LoRA 는 한 번")


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


print(f"\n{PASSES} passed, {len(FAILS)} failed")
sys.exit(1 if FAILS else 0)
