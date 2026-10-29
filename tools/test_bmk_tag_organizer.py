# -*- coding: utf-8 -*-
r"""bmk_tag_organizer(v3) 자체 테스트 — 노드 팩 패키지 전체가 아니라 단일 모듈로 불러 쓴다.

실행 (ComfyUI-Easy-Install 루트에서):
    python_embeded\python.exe ComfyUI\custom_nodes\ComfyUI_BMK_Nodes\tools\test_bmk_tag_organizer.py

v2 입력(framing·view·guess·arrangement)이 optional 이라 v1 입력만 주면 결과가 레이아웃 그대로인지,
배열 세팅·덮어쓰기·추정이 코어와 같은 결과를 내는지, 편집기 경로(_preview_payload·_palette_payload)가
노드 실행과 같은 결과를 내는지 본다. v3 입력(dialect_priority·comments·sentences)은 arrangement 뒤에 붙어
v2 워크플로의 widgets_values 자리가 그대로인지, 자동 인식·주석·문장·경고가 코어와 같은지 본다.
코어·팩은 TagOrganizer `tagorg.py sync` 가 복사한 사본을 쓴다.
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
check(list(it["optional"]) == ["framing", "view", "guess", "arrangement", "dialect_priority", "comments", "sentences"],
      "v2 입력은 optional 맨 뒤, v3 입력은 그 뒤")
check(it["optional"]["arrangement"][1].get("hidden") is True, "arrangement 숨김 위젯")
# v2 워크플로의 widgets_values(위젯 순서대로 15개)가 같은 이름 자리에 들어간다
V2_WIDGETS = ["text", "preset", "exclude", "max_rating", "rules", "layout", "input_dialect", "output_dialect",
              "line_per_section", "trailing_comma", "dedupe", "framing", "view", "guess", "arrangement"]
check((list(it["required"]) + list(it["optional"]))[:15] == V2_WIDGETS, "v2 위젯 자리 그대로")
dialects, dopt = it["required"]["input_dialect"]
check(dialects == ["comfyui", "nai", "anima", "auto"] and dopt["default"] == "comfyui", "input_dialect = 세 문법 + auto")
check(set(dialects) == set(core.INPUT_DIALECTS), "input_dialect 목록 == 코어 INPUT_DIALECTS")
prios, popt = it["optional"]["dialect_priority"]
check(len(prios) == 6 and len(set(prios)) == 6 and popt["default"] == prios[0], "우선순위 콤보 6가지")
check(core.Options(dialect_priority=prios[0]).priority() == core.DIALECT_PRIORITY, "우선순위 기본 == 코어 기본")
check(all(core.Options(dialect_priority=p).priority() == tuple(p.split(" > ")) for p in prios), "우선순위 글을 코어가 읽음")
check(it["optional"]["comments"][0] == list(core.COMMENT_MODES) and it["optional"]["comments"][1]["default"] == "remove",
      "comments 콤보 == 코어 COMMENT_MODES, 기본 뺌")
check(it["optional"]["sentences"][1]["default"] is True, "sentences 기본 켬")
check(core.Options(dialect_priority=mod._DEFAULTS["dialect_priority"]).priority() == core.Options().priority()
      and mod._DEFAULTS["comments"] == core.Options().comments and mod._DEFAULTS["sentences"] == core.Options().sentences,
      "새 위젯 기본값 == 코어 Options 기본값")
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
bad = mod._request_values({"sentences": "no", "comments": 0, "dialect_priority": None})
check((bad["sentences"], bad["comments"], bad["dialect_priority"]) == (True, "remove", mod._PRIORITIES[0]),
      "v3 값도 형식이 다르면 기본값")
for body, what in (({"input_dialect": "auto", "dialect_priority": "nai > nai"}, "겹친 우선순위"),
                   ({"comments": "drop"}, "모르는 comments")):
    try:
        mod._preview_payload({"text": "1girl", **body})
        check(False, f"{what} 가 통과")
    except ValueError as e:
        check(str(e).startswith(mod._TAG), f"{what} 는 ValueError")

# ── v3: 자동 인식·우선순위·주석·문장·경고 == 코어 ──
V3_TEXTS = [
    "1girl, 1.2::red eyes ::, {smile}, upper body",                 # NAI 표기
    "1girl, (red eyes:1.2), \\(cosplay\\), smile",                   # ComfyUI 표기
    "1girl, @wlop, (smile:1.1), upper body",                         # Anima @작가
    "1girl, (smile:1.2), 1.1::red eyes ::",                          # 섞임 → 우선순위
    "1girl, smile, upper body",                                      # 표기 없음 → 우선순위 첫 문법
    "1girl, smile // 메모: 나중에 지울 것\nupper body, she stands in the rain. looking up",
    "1girl, 1.1::artist:as109::, 1.1::year 2024::, smile",          # NAI 숫자 닫기 경고
]
for text in V3_TEXTS:
    for over in ({}, {"input_dialect": "auto"}, {"input_dialect": "auto", "dialect_priority": "comfyui > nai > anima"},
                 {"input_dialect": "auto", "dialect_priority": "anima > comfyui > nai", "output_dialect": "nai"},
                 {"comments": "keep", "sentences": False}, {"input_dialect": "nai", "comments": "keep"}):
        (t, removed, report), _ = run(text, **over)
        opts = {**V1, **over, "output_dialect": "" if over.get("output_dialect", "same") == "same" else over["output_dialect"]}
        want = core.organize(text, pack, opts)
        check((t, removed) == (want.text, want.removed_text), f"v3 == 코어 {over} {text[:24]}")
        rep = json.loads(report)
        check(rep["dialect"] == want.report["dialect"] and rep["warnings"] == want.report["warnings"], "report 문법·경고 == 코어")
        pv = mod._preview_payload({**V1, **over, "text": text})
        check((pv["text"], pv["removed"]) == (t, removed), "미리보기 == 실행 (v3)")
        check(pv["dialect"] == rep["dialect"], "미리보기 dialect == report")
        check(pv["warnings"] == [{**w, "text": core.warning_text(w["reason"])} for w in rep["warnings"]],
              "미리보기 경고 = report 경고 + warning_text")


def dialect_of(text, **kw):
    return json.loads(run(text, input_dialect="auto", **kw)[0][2])["dialect"]


d = dialect_of(V3_TEXTS[0])
check(d["auto"] is True and d["input"] == "nai" and "nai" in d["evidence"] and d["conflict"] is False, "auto: NAI 표기 → nai")
check(dialect_of(V3_TEXTS[1])["input"] == "comfyui", "auto: ComfyUI 표기 → comfyui")
check(dialect_of(V3_TEXTS[2])["input"] == "anima", "auto: @작가 → anima(ComfyUI 표기가 있어도)")
check(dialect_of(V3_TEXTS[3])["input"] == "nai" and dialect_of(V3_TEXTS[3])["conflict"] is True, "섞임: 기본 우선순위 nai")
check(dialect_of(V3_TEXTS[3], dialect_priority="comfyui > nai > anima")["input"] == "comfyui", "섞임: 우선순위 comfyui")
check(dialect_of(V3_TEXTS[4], dialect_priority="anima > nai > comfyui")["input"] == "anima", "표기 없음: 우선순위 첫 문법")
check(json.loads(run(V3_TEXTS[0])[0][2])["dialect"]["auto"] is False, "auto 가 아니면 dialect.auto 끔")
# 주석: 기본 뺌(removed 에 comment), keep 이면 구간 맨 위 한 줄
(t, removed, report), _ = run(V3_TEXTS[5])
check("메모" not in t and "# comment" in removed, "주석 기본 뺌")
(t, _, _), _ = run(V3_TEXTS[5], comments="keep")
check(t.split("\n")[0].startswith("//") and "메모" in t.split("\n")[0], "주석 keep → 맨 위 한 줄")
# 문장: 기본 켬이면 자연어 토큰, 끄면 없음
rep = json.loads(run(V3_TEXTS[5])[0][2])
check(rep["counts"]["sentences"] == 1 and any(r["kind"] == "natural" for r in rep["tokens"]), "문장 나누기 켬 → natural")
check(json.loads(run(V3_TEXTS[5], sentences=False)[0][2])["counts"]["sentences"] == 0, "문장 나누기 끔")
# NAI 숫자 닫기: 경고와 출력 빈칸
(t, _, report), _ = run(V3_TEXTS[6], input_dialect="nai")
rep = json.loads(report)
check({w["reason"] for w in rep["warnings"]} == {"nai_number:109", "nai_number:2024"}, "NAI 숫자 닫기 경고")
check("1.1::year 2024 ::" in t, "NAI 출력은 닫는 :: 앞을 띄움")
check("1.1::red eyes ::" in run("(red eyes:1.1), smile", output_dialect="nai")[0][0].split("\n"), "ComfyUI → NAI 출력 빈칸")
# v2 워크플로·API 프롬프트(새 입력 없음) == 새 기본값으로 돈 결과
for text in V3_TEXTS:
    v2 = mod.BMKTagOrganizer().organize(text, **V1, framing="auto", view="auto", guess=False, arrangement="")
    v3 = mod.BMKTagOrganizer().organize(text, **V1, dialect_priority=mod._PRIORITIES[0], comments="remove",
                                        sentences=True)
    check(v2["result"] == v3["result"], "새 입력이 빠지면 기본값")

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
