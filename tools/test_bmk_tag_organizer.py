# -*- coding: utf-8 -*-
r"""bmk_tag_organizer(v2) 자체 테스트 — 노드 팩 패키지 전체가 아니라 단일 모듈로 불러 쓴다.

실행 (ComfyUI-Easy-Install 루트에서):
    python_embeded\python.exe ComfyUI\custom_nodes\ComfyUI_BMK_Nodes\tools\test_bmk_tag_organizer.py

v2 입력(framing·view·guess·arrangement)이 optional 이라 v1 입력만 주면 결과가 레이아웃 그대로인지,
배열 세팅·덮어쓰기·추정이 코어와 같은 결과를 내는지, 편집기 경로(_preview_payload·_palette_payload)가
노드 실행과 같은 결과를 내는지 본다. 코어·팩은 TagOrganizer `tagorg.py sync` 가 복사한 사본을 쓴다.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
PACK_DIR = _HERE.parents[1]
sys.path.insert(0, str(PACK_DIR))  # 코어(bmk_tag_organizer_core)를 같은 폴더에서

spec = importlib.util.spec_from_file_location("bmk_tag_organizer", PACK_DIR / "bmk_tag_organizer.py")
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)
core = mod._core()

FAILS: list[str] = []
PASSES = 0


def check(cond, msg):
    global PASSES
    if cond:
        PASSES += 1
    else:
        FAILS.append(msg)
        print("FAIL:", msg)


V1 = dict(preset="sort_only", exclude="", max_rating="e", rules="remove", layout="default", input_dialect="comfyui",
          output_dialect="same", line_per_section=True, trailing_comma=True, dedupe=True)
TEXTS = [
    "1girl, upper body, shoes, soles, navel, smile, masterpiece, mystery word, <lora:x:0.8>, (red eyes:1.2)",
    "2girls, hatsune miku, vocaloid, from behind, looking back, smile, aqua eyes, BREAK 1boy, standing, "
    "holding chainsword, sharp crimson eyes, another hand up",
]


def run(text, **kw):
    out = mod.BMKTagOrganizer().organize(text, **{**V1, **kw})
    return out["result"], out["ui"]


pack = mod._load_pack()

# ── 노드 정의 ──
it = mod.BMKTagOrganizer.INPUT_TYPES()
check(list(it["required"])[-1] == "dedupe" and len(it["required"]) == 11, "required 는 v1 그대로(11개)")
check(list(it["optional"]) == ["framing", "view", "guess", "arrangement"], "v2 입력은 optional 맨 뒤")
check(it["optional"]["arrangement"][1].get("hidden") is True, "arrangement 숨김 위젯")
quick = core.palette(pack)["quick"]
check(it["optional"]["framing"][0] == ["auto"] + quick["framing"], "framing 콤보 = auto + 팩 퀵 목록")
check(it["optional"]["view"][0] == ["auto"] + quick["view"], "view 콤보 = auto + 팩 퀵 목록")

# ── v1 입력만 → 레이아웃 결과, ui 에 입력 ──
for text in TEXTS:
    for over in ({}, {"preset": "character_swap"}, {"layout": "compact", "rules": "report"}):
        (t, removed, report), ui = run(text, **over)
        legacy = core.organize(text, pack, {**{k: v for k, v in {**V1, **over}.items()}, "output_dialect": ""})
        check((t, removed) == (legacy.text, legacy.removed_text), f"v1 입력 == 코어 레이아웃 {over}")
        check(ui == {"bmk_tag_input": [text]}, "ui 에 입력 글")
        check(json.loads(report)["arrangement"] == "", "배열을 쓰지 않으면 report.arrangement 비움")
        check(run(text, arrangement="", **over)[0] == (t, removed, report), "빈 arrangement == layout")

# ── 배열 세팅·덮어쓰기·추정 == 코어 ──
arr = core.arrangement_from_layout(pack, "default")
arr["rows"].insert(0, ["mine"])
arr["chunks"]["mine"] = {"label": "내 묘사", "custom": True, "tags": ["mystery word"], "patterns": ["* eyes"]}
arr["chunks"]["count"]["hidden"] = True
arr["label"] = "시험 배열"
arr_text = json.dumps(arr, ensure_ascii=False)
for text in TEXTS:
    for framing, view, guess in (("auto", "auto", False), ("cowboy shot", "from behind", True)):
        (t, removed, report), _ = run(text, framing=framing, view=view, guess=guess, arrangement=arr_text)
        want = core.organize(text, pack, {"arrangement": arr, "framing": "" if framing == "auto" else framing,
                                          "view": "" if view == "auto" else view, "guess": guess})
        check((t, removed) == (want.text, want.removed_text), f"배열 == 코어 {framing}/{view}/{guess}")
        rep = json.loads(report)
        check(rep["arrangement"] == "시험 배열", "report.arrangement = 배열 이름")
        # 편집기 미리보기 경로 == 실행. 모르는 키는 버리고 형식이 다른 값은 기본값
        pv = mod._preview_payload({**V1, "text": text, "framing": framing, "view": view, "guess": guess,
                                   "arrangement": arr_text, "unknown": 1, "dedupe": "yes"})
        check((pv["text"], pv["removed"]) == (t, removed), "미리보기 == 실행")
        check(pv["arranged"] == rep["arranged"] and pv["tokens"] == rep["tokens"], "미리보기 구조 == report")
check("1girl" not in run(TEXTS[0], arrangement=arr_text)[0][0], "숨긴 청크의 태그는 뺀다")
check(run(TEXTS[0], arrangement=arr_text)[0][0].startswith("mystery word, (red eyes:1.2)"), "커스텀 청크가 맨 앞 줄")

# ── 잘못된 값 ──
for bad, what in (('{"rows": [["nope"]], "chunks": {}}', "없는 청크"), ("{", "깨진 JSON")):
    try:
        run(TEXTS[0], arrangement=bad)
        check(False, f"잘못된 배열({what})이 통과")
    except ValueError as e:
        check(str(e).startswith(mod._TAG), f"잘못된 배열({what}) ValueError")
try:
    mod._preview_payload({"text": "1girl", "framing": "smile"})
    check(False, "크롭이 아닌 framing 이 통과")
except ValueError:
    check(True, "크롭이 아닌 framing 은 ValueError")
check(mod._request_values({"guess": "true", "preset": 3})["guess"] is False, "형식이 다른 값은 기본값")

# ── palette ──
pal = mod._palette_payload()
check(pal["palette"]["format"] == core.PALETTE_FORMAT, "palette 형식")
check(set(pal["layouts"]) == set(pack.layouts), "레이아웃마다 시작 배열")
for lid, start in pal["layouts"].items():
    for text in TEXTS:
        a = core.organize(text, pack, {"layout": lid})
        b = core.organize(text, pack, {"arrangement": start})
        check(a.text == b.text, f"레이아웃 {lid} 시작 배열 == 레이아웃 결과")

print(f"\n{PASSES} passed, {len(FAILS)} failed")
sys.exit(1 if FAILS else 0)
