# -*- coding: utf-8 -*-
r"""Design Patch Spec Draft 평가 도구 (M3_SPEC §8.1–8.2) — 평가 프로젝트 구성, 원장 재정규화 채점, A/B, 크레딧 합산.

순수 도구다: store / prompt / vlm 모듈만 import 하고 인증·네트워크·유료 호출이 없다. 채점은 원장 done 의 parsed 를
지금의 정규화로 다시 돌려 하므로(스펙 파일과 무관) 호출 0건이다. 노드처럼 done 의 repairs 도 넘긴다(잘린 출력의
끊긴 마지막 값 버림·truncated_output). 사례 정의: _proto\vlm_eval\cases.json.

실행 (ComfyUI-Easy-Install 루트에서):
    python_embeded\python.exe -X utf8 -W ignore ComfyUI\custom_nodes\ComfyUI_BMK_Nodes\tools\eval_bmk_design_patch_vlm.py <명령> …

  setup --named R1 --legacy R2            Import PSD 를 마친 두 프로젝트를 평가용으로 고친다(크롭 거르기, 03 PNG 캔버스,
                                          named 의 합성 레이어명 타깃). 결과를 표로 보고
  validate                                정답 스펙(gold_specs/)을 채점기에 넣는다. 모두 ≥90 이어야 한다(채점기 자기 검증)
                                          + 대조 예문(_SELFCHECK_COUNTS·_SELFCHECK_WORDS)이 모두 기대대로여야 한다
  score --root R [--select k=v,…] --tag T 원장 done 을 다시 정규화해 채점 → scores/T.json, scores/T.md
                                          (select 키: model, effort, round, backend, detail). 원장 뒤 입력이 바뀌어
                                          키가 다르면 ref_images 번호·px bbox 틀은 원장 req 본문의 보낸 이미지로 되살리고
                                          [입력 바뀜] 으로 표시, 본문이 없으면 stale(채점 안 함)
  compare A B                             scores/A 와 scores/B 의 사례 대응 표, 평균 차이, SE
  determinism --root R [--select …] [--rounds 0,1]
                                          C10 결정성: 두 draft_round 의 정규화 결과(스펙 필드·review counts/found)를
                                          크롭별로 비교. 다름이 있으면 종료 코드 1
  budget --roots R… [--stage N]           VLM 원장 credits + 매니페스트 calls[].credits 합계, 남은 예산, 단계 게이트.
                                          과금됐을 수 있는데 크레딧 보고가 없는 openai 시도는 req 의 est 보통값으로
                                          세어(추정, 따로 표시) 누적·게이트·credit_cap 에 넣는다
  ab-setup --from R1 --draft-tag T [--replace]
                                          R1(named)을 복사해 ab_minimal / ab_draft / ab_gold 를 만든다(§8.2)
  ab-sheet --roots R…                     크롭별 후보를 섞은 맹검 시트(문자 라벨) + ab/key.json + ab/scores.csv 틀.
                                          다시 돌려도 있던 라벨은 고정, 새 후보만 새 라벨·scores.csv 에 줄 추가
  ab-tally [--scores ab/scores.csv]       Borda·크롭별 승(군 Borda 합, 보조 최고 순위)·쓸 수 없음 수, 보조 지표
                                          (Analyze outside_mad, 정답 마스크 안 ΔE76)

점수 S = 100·(0.35·G + 0.25·K + 0.15·C + 0.10·B + 0.15·F) — 해당 없는 항목(개수 정답 없음, bbox 없음)은 빼고 가중치를
다시 나눈다. 치명 게이트(렌더 예외, CJK, 텍스트 정책 위반)에 걸리면 S ≤ 50.
  G 게이트 통과율   렌더 깨끗(validate_spec·모르는 키·기본 설명·V1–V4 린트), 타깃 수, CJK, 텍스트 정책, 레퍼런스 금지·필수,
                    mode·action·shape 허용 집합, (E6) 따옴표 인용 없음, (legacy) 제안 레이어명 파싱
  K 키워드          묶음(필드, any-of) 재현율 − 0.25 × 금지어 적중. 렌더되지 않아 _review.unrendered 로 간 값도 본다(내용 평가).
                    토큰 단위 대조: 굴절형(pins·lettering·pinned)과 합성어 끝(hairpin ∋ pin)은 맞음, 앞만 같은 낱말
                    (pinkish ∌ pin)은 아님. 금지어는 양쪽 경계 — 굴절형만 받고 합성어 끝은 아님(marigold ∌ gold,
                    rainbow ∌ bow)
  C 개수            대상 명사 앞 수사(one–twelve·숫자·pair of) 또는 _review.counts 값 == 정답. 명사 뒤 같은 절의 첫
                    each/per/every 가 다른 명사를 꾸미면 개체당 수("one clip on each stack", "two loops per side")라
                    세지 않는다. 대상 명사 바로 뒤에 떠 있는 수량사("two buttons each with a rim")는 센다
  B bbox            IoU ≥ 0.3 또는 중심이 상자 안(인스턴스 상자 중 최대)
  F 형식            name 2–6 단어, detail 2–5 절·≤60 단어·조명어 없음, preserve 2–4 개
legacy 사례는 초안 A 를 가장 잘 맞는 정답 타깃과 대조하고, suggested_targets 가 나머지 정답 타깃을 키워드로 덮는지와
제안 레이어명이 파싱되는지를 더 본다.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import os
import random
import re
import shutil
import statistics
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

_HERE = Path(__file__).resolve()
PKG = _HERE.parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import bmk_design_patch_prompt as dprompt  # noqa: E402
import bmk_design_patch_store as store  # noqa: E402
import bmk_design_patch_vlm as dvlm  # noqa: E402

logger = logging.getLogger(__name__)

_TAG = "[ComfyUI_BMK_Nodes::DesignPatch]"

EVAL_DIR = Path(r"H:\BmkNodeDesign\MultiLayerCropEdit\_proto\vlm_eval")
QWEN_DIR = _HERE.parents[3] / "models" / "LLM" / "Qwen-VL" / "Qwen3-VL-8B-Instruct"  # 노드와 같은 위치(local_qwen 키용)
BUDGET_TOTAL = 10000     # 사용자 승인 상한(M3_SPEC 전제)
GATE_LIMIT = 9700        # 누적 실측 + 1.5 × 단계 예상 ≤ 9,700
CAP_LIMIT = 9800         # 노드 credit_cap = min(1.6 × 단계 예상, 9,800 − 누적)
PASS_S = 90.0            # validate 기준
FATAL_CAP = 50.0
IOU_HIT = 0.3
AB_GROUPS = ("minimal", "draft", "gold")

_CJK_RE = re.compile(r"[\u1100-\u11ff\u3040-\u30ff\u3130-\u318f\u3400-\u9fff\uac00-\ud7a3\uf900-\ufaff]")
_LIGHT_RE = re.compile(r"\b(light|lights|lit|lighting|shadow|shadows|highlight|highlights|glow|glows|glowing|background)\b",
                       re.IGNORECASE)
_QUOTE_RE = re.compile(r"[\"\u201c\u201d\u2018\u2019]")
_IMG_PAIR_RE = re.compile(r"(@image\d+|Image \d+) and (@image\d+|Image \d+) shows")
_NONE_RE = re.compile(r"\bNone\b")
_NEG_WORDS = ("no", "without", "not", "never")
_NUM_WORDS = {"one": 1, "single": 1, "two": 2, "pair": 2, "both": 2, "three": 3, "four": 4, "five": 5, "six": 6,
              "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12}
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_SUFFIXES = ("s", "es", "ed", "ing", "er", "ers", "ish", "y")
_SUFFIXES_LONG = ("ed", "ing", "er", "ers", "ish", "y")   # 겹친 자음·e 탈락 뒤
_DISTRIB_WORDS = ("each", "per", "every")
_NP_LEAD = {"of", "the", "a", "an", "other"}   # each/per/every 뒤 명사구 앞에서 건너뜀("each of the two stacks")
_NP_STOP = {"of", "with", "without", "in", "on", "at", "to", "from", "by", "for", "near", "into", "and", "or", "but", "that",
            "which", "has", "have", "is", "are", "was", "were", "sits", "holds", "carries", "bears", "shows", "features"}
_EN_TARGET = ("name", "ref_object", "ref_ignore", "detail", "must_not", "location", "occluded_by", "defects")
_EN_CROP = ("style_note", "preserve")
_LEDGER_FILE_RE = re.compile(r"a(\d+)\.(req|inflight|created|resp|done|failed|rejected)\.json")


# ══════════════════════════════════════════════════════════════════════
# 사례·프로젝트
# ══════════════════════════════════════════════════════════════════════
def load_cases(path: str | os.PathLike | None = None) -> dict:
    p = Path(path) if path else EVAL_DIR / "cases.json"
    doc = json.loads(p.read_text(encoding="utf-8"))
    doc["_dir"] = str(p.parent)
    return doc


def _ref_root(doc: dict) -> Path:
    return Path(doc["ref_root"])


def _refs_dir(doc: dict) -> str:
    return str(_ref_root(doc) / doc["refs_dir"])


def new_targets(parsed: dict, old_crop: dict | None) -> list[dict]:
    """bmk_design_patch._new_targets 의 순수 복제(이 도구는 노드 모듈을 import 하지 않는다 — 테스트 N9 가 일치를 검사)."""
    old = {t["tid"]: t for t in (old_crop or {}).get("targets") or []}
    out = []
    for t in parsed["targets"]:
        text = t["text"].strip()
        label = text or parsed["part"] or parsed["name"]
        name_en = text if (text and text.isascii()) else ""
        tt = {"tid": t["tid"], "label": label, "name_en": name_en, "mode": "ref_correct", "refs": []}
        o = old.get(t["tid"])
        if o and o.get("label") == label:
            for k in ("name_en", "mode", "refs"):
                if o.get(k) and not (k == "name_en" and name_en):
                    tt[k] = o[k]
        if t.get("origin"):
            tt["origin"] = t["origin"]
        out.append(tt)
    return out


def case_crop(doc: dict, case: dict, *, legacy: bool = False, only_tids=None) -> dict:
    """사례 → 매니페스트 크롭 모양(validate 용 합성). source = 03 PNG 절대 경로, refs_auto = match_refs."""
    lname = case["crop_name"] if legacy else case["layer_name"]
    p = store.parse_crop_layer_name(lname)
    targets = new_targets(p, None)
    if only_tids is not None:
        targets = [t for t in targets if t["tid"] in only_tids]
    return {"id": case["crop_id"], "name": p["name"], "nnn": p["nnn"], "part": p["part"], "layer_name": lname,
            "rect": [0, 0] + list(case["canvas_size"]), "source": str(_ref_root(doc) / case["canvas"]),
            "refs_auto": store.match_refs(_refs_dir(doc), p["nnn"], part=p["part"]), "targets": targets,
            "warnings": []}


def _glossary(root: str, m: dict) -> dict:
    """bmk_design_patch._glossary 와 같은 규칙(프로젝트 glossary.json + Import 의 glossary_path)."""
    g = store.load_glossary(os.path.join(root, "glossary.json"))
    g.update(store.load_glossary((m.get("source") or {}).get("glossary") or ""))
    return g


def setup_project(root: str, doc: dict, kind: str) -> list[dict]:
    """§8.1: 평가 크롭만 남기고 source = 03 PNG RGBA(save_png_ca), named 의 '|' 레이어명 사례는 타깃을 다시 만든다.
    legacy = legacy 표시 사례만, 레이어명은 Import 그대로(레거시 1타깃). 반환: 크롭별 표 행."""
    if kind not in ("named", "legacy"):
        raise ValueError(f"kind 는 named / legacy: {kind!r}")
    want = {c["crop_id"]: c for c in doc["cases"] if kind == "named" or c.get("legacy")}
    srcs = {}
    for cid, case in want.items():
        with Image.open(_ref_root(doc) / case["canvas"]) as im:
            rgba = np.asarray(im.convert("RGBA"))
        srcs[cid] = (store.save_png_ca(root, "inputs", rgba), round(float(np.mean(rgba[..., 3] < 255)), 6))
    rows: list[dict] = []

    def mutate(m):
        rows.clear()
        have = {c["id"] for c in m["crops"]}
        for cid in want:
            if cid not in have:
                rows.append({"crop_id": cid, "case": want[cid]["id"], "status": "없음(Import 결과에 이 크롭이 없음)"})
        crops = []
        for cr in m["crops"]:
            case = want.get(cr["id"])
            if case is None:
                continue
            cr["source"], cr["alpha_frac"] = srcs[cr["id"]]
            if kind == "named" and store._PIPE_RE.search(case["layer_name"]):
                cr["layer_name"] = case["layer_name"]
                cr["targets"] = new_targets(store.parse_crop_layer_name(case["layer_name"]), cr)
            crops.append(cr)
            rows.append({"crop_id": cr["id"], "case": case["id"], "status": "ok",
                         "targets": [f"{t['tid']}:{t['label']}" for t in cr["targets"]], "source": cr["source"]})
        m["crops"] = crops
        keep = {c["id"] for c in crops}
        m["jobs"] = [j for j in m.get("jobs") or [] if j.get("crop_id") in keep]
        m["candidates"] = [c for c in m.get("candidates") or [] if c.get("crop_id") in keep]
        if isinstance(m.get("source"), dict):
            m["source"]["glossary"] = ""  # 이름 짓기도 평가 대상 → 용어집 없음

    store.update_manifest(root, mutate)
    if os.path.isfile(os.path.join(root, "glossary.json")):
        rows.append({"crop_id": "-", "case": "-", "status": "경고: glossary.json 이 있습니다 — 평가 프로젝트는 용어집 없이"})
    return rows


# ══════════════════════════════════════════════════════════════════════
# 채점
# ══════════════════════════════════════════════════════════════════════
def _as_list(v) -> list[str]:
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return [str(x) for x in v if x is not None]
    return [str(v)]


def _words(s: str) -> list[str]:
    return _TOKEN_RE.findall(str(s).lower())


def spec_from_draft(draft, order: list[str]) -> dict:
    """Draft → 스펙 dict(현재 파일 없음 = merge 결과와 같은 노드 소유 필드). order = 매니페스트 tid 순서."""
    spec = {k: draft.crop[k] for k in dvlm.OWNED_CROP if k in (draft.crop or {})}
    spec["targets"] = [dict({"id": tid}, **draft.targets[tid]) for tid in order if tid in (draft.targets or {})]
    return spec


def render_gate(crop: dict, spec: dict, refs_dir: str, glossary: dict) -> tuple[bool, list[str]]:
    """(예외 없음, 문제 목록). spec_from_manifest → validate_spec → variants → V1–V4 린트(§4.4)."""
    try:
        s = dprompt.spec_from_manifest(crop, None, refs_dir, glossary or {}, spec)
        probs = list(dprompt.validate_spec(s))
        vs = dprompt.variants(s)
    except Exception as e:  # 렌더 예외 = 치명
        return False, [f"렌더 예외: {type(e).__name__}: {e}"]
    probs += [w for w in s.get("warnings") or [] if "모르는 키" in w or "기본 설명" in w or "파일이 없습니다" in w]
    for name, v in vs.items():
        pr = v["prompt"]
        for label, hit in (("None", _NONE_RE.search(pr)), ("' ,'", " ," in pr), ("and @imageN shows", _IMG_PAIR_RE.search(pr)),
                           ("CJK", _CJK_RE.search(pr))):
            if hit:
                probs.append(f"{name}: {label}")
    return True, probs


def _field_text(t: dict, review: dict, field: str) -> str:
    if field == "name":
        return " ".join(x for x in (t.get("name"), review.get("name_suggestion")) if x)
    vals = _as_list(t.get(field))
    un = review.get("unrendered") if isinstance(review.get("unrendered"), dict) else {}
    vals += _as_list(un.get(field))
    return " ; ".join(vals)


def _all_text(spec: dict, reviews: dict) -> str:
    parts = []
    for k in _EN_CROP:
        parts += _as_list(spec.get(k))
    for t in spec.get("targets") or []:
        for f in _EN_TARGET:
            parts.append(_field_text(t, reviews.get(t.get("id")) or {}, f))
    return " ; ".join(p for p in parts if p)


def _bases(word: str) -> list[str]:
    """키워드 → 비교 기준형(그대로 + 단순 복수의 단수: fingers → finger, notches → notch, bodies → body)."""
    out = [word]
    if word.endswith("ies") and len(word) > 4:
        out.append(word[:-3] + "y")
    elif word.endswith("es") and word[:-2].endswith(("s", "x", "z", "ch", "sh")):
        out.append(word[:-2])
    elif word.endswith("s") and not word.endswith("ss") and len(word) > 3:
        out.append(word[:-1])
    return out


def _inflects(tok: str, w: str) -> bool:
    """tok 이 w 이거나 그 굴절형(-s/-es/-ed/-ing/-er/-ish/-y, 자음 겹침 pinned·e 탈락 taped·y→i bodies)."""
    if tok == w:
        return True
    stems = [(w, _SUFFIXES), (w + w[-1], _SUFFIXES_LONG)]
    if w.endswith("e"):
        stems.append((w[:-1], _SUFFIXES_LONG))
    elif w.endswith("y"):
        stems.append((w[:-1] + "i", ("es", "ed", "er")))
    return any(tok.startswith(s) and tok[len(s):] in sufs for s, sufs in stems)


def _tok_match(tok: str, word: str, compound: bool = True) -> bool:
    """토큰 하나 ↔ 키워드 토큰 하나. 굴절형과(compound 면) 합성어 끝(hairpins ∋ pin)을 받는다 — 합성어는 키워드 ≥3자·
    앞부분 ≥3자, -ed/-ing 로 끝나는 키워드는 제외(tapered ∌ red, earring ∌ ring). 앞쪽만 맞는 낱말(pinkish ∌ pin)은
    받지 않는다. compound=False(금지어) = 토큰 앞 경계도 지킴(marigold ∌ gold, rainbow ∌ bow)."""
    for w in _bases(word):
        if _inflects(tok, w):
            return True
        if compound and len(w) >= 3 and not w.endswith(("ed", "ing")) and any(
                _inflects(tok[i:], w) for i in range(3, len(tok) - len(w) + 1)):
            return True
    return False


def _find_word(toks: list[str], word: str, compound: bool = True) -> list[int]:
    """word(여러 낱말이면 연속 토큰)가 맞는 첫 토큰 위치들."""
    ws = _words(word)
    if not ws:
        return []
    n = len(ws)
    return [i for i in range(len(toks) - n + 1) if all(_tok_match(toks[i + j], ws[j], compound) for j in range(n))]


def _has_word(text: str, word: str) -> bool:
    return bool(_find_word(_words(text), word))


def _forbidden_hits(text: str, word: str) -> int:
    """금지어 적중 수(토큰 단위 — 양쪽 경계, 굴절형만: crossed ∋ cross, crosswise·marigold·rainbow 는 아님).
    바로 앞 단어가 no/without/not/never 면 부정 표현이라 세지 않는다("no gold bands")."""
    toks = _words(text)
    return sum(1 for i in _find_word(toks, word, compound=False) if not (i > 0 and toks[i - 1] in _NEG_WORDS))


def _np_after(toks: list[str], clause: list[int], k: int) -> list[str]:
    """each/per/every(toks[k]) 가 꾸미는 명사구 낱말 — 같은 절, 앞의 관사·of·수사는 건너뛰고 전치사·동사 앞까지(최대 5 토큰).
    첫 낱말이 분사·부사(-ed/-ing/-ly, 5자 이상)거나 바로 전치사·동사면 빈 목록 = 앞 명사에 떠 있는 수량사
    ("buttons each with a rim", "loops each edged in gold", "clips each gripping a stack") 또는 절 끝 대명사("… on each")."""
    out: list[str] = []
    for j in range(k + 1, min(len(toks), k + 6)):
        t = toks[j]
        if clause[j] != clause[k]:
            break
        if not out and (t in _NP_LEAD or t.isdigit() or t in _NUM_WORDS):
            continue
        if t in _NP_STOP or (not out and len(t) >= 5 and t.endswith(("ed", "ing", "ly"))):
            break
        out.append(t)
    return out


def _numbers_before(text: str, nouns: list[str]) -> set[int]:
    """대상 명사 앞 4 토큰 안의 수사. 개체당 수는 세지 않는다 — 같은 절(; , .) 안에서 명사 뒤 6 토큰 안의 첫 each/per/every
    가 대상이 아닌 명사를 꾸미는 경우("one clip on each stack", "two loops per side"), 수사와 명사 사이에 each/per/every
    가 있는 경우("2 holes per clip" 의 clip). 떠 있는 수량사("two buttons each with a rim")와 대상 명사를 꾸미는 경우
    ("two clips with 2 holes per clip" 의 clips)는 센다."""
    toks, clause, c = [], [], 0
    for t in re.findall(r"[a-z0-9]+|[;,.]", str(text).lower()):
        if t in ";,.":
            c += 1
        else:
            toks.append(t)
            clause.append(c)
    out: set[int] = set()
    for i, tk in enumerate(toks):
        if not any(_tok_match(tk, n) for n in nouns):
            continue
        k = next((k for k in range(i + 1, min(len(toks), i + 7)) if toks[k] in _DISTRIB_WORDS and clause[k] == clause[i]),
                 None)
        if k is not None:
            np_ = _np_after(toks, clause, k)
            if np_ and not any(_tok_match(t, n) for t in np_ for n in nouns):
                continue
        for j in range(i - 1, max(-1, i - 5), -1):
            w = toks[j]
            if w in _DISTRIB_WORDS:  # "2 holes per clip" — 앞 수사는 다른 명사의 것
                break
            if w.isdigit():
                out.add(int(w))
                break
            if w in _NUM_WORDS:
                out.add(_NUM_WORDS[w])
                break
    return out


def count_ok(check: dict, t: dict, review: dict) -> tuple[bool, str]:
    """개수 정답: 본문(name·detail·location·must_not)의 대상 명사 앞 수사 또는 _review.counts[review 필드]."""
    text = " ; ".join(_field_text(t, review, f) for f in ("name", "detail", "location", "must_not"))
    in_text = _numbers_before(text, check["nouns"])
    found = set(in_text)
    for c in review.get("counts") or []:
        if not isinstance(c, dict) or not any(_has_word(str(c.get("part") or ""), n) for n in check["nouns"]):
            continue
        for f in check.get("review") or []:
            v = c.get(f)
            if isinstance(v, int) and not isinstance(v, bool) and v >= 0:
                found.add(v)
    ok = check["n"] in found and not (in_text - {check["n"]})  # 본문에 다른 수가 있으면 틀림(렌더되는 쪽)
    return ok, f"{'/'.join(check['nouns'][:2])}={check['n']} 찾음 {sorted(found)}"


def _iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


def bbox_hit(box, gold_boxes) -> tuple[bool, float]:
    if not (isinstance(box, (list, tuple)) and len(box) == 4):
        return False, 0.0
    b = [float(v) for v in box]
    cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
    best = max((_iou(b, g) for g in gold_boxes), default=0.0)
    inside = any(g[0] <= cx <= g[2] and g[1] <= cy <= g[3] for g in gold_boxes)
    return best >= IOU_HIT or inside, round(best, 3)


def _format_checks(t: dict, review: dict) -> list[tuple[str, bool]]:
    tid = t.get("id")
    out = []
    nm = t.get("name") or review.get("name_suggestion") or ""
    if nm:
        out.append((f"{tid}.name 2–6 단어", 2 <= len(str(nm).split()) <= 6))
    det = str(t.get("detail") or "")
    if det and t.get("mode") != "self_restore":
        clauses = [c for c in det.split(";") if c.strip()]
        out.append((f"{tid}.detail 2–5 절·≤60 단어", 2 <= len(clauses) <= 5 and len(det.split()) <= 60))
        out.append((f"{tid}.detail 조명어 없음", not _LIGHT_RE.search(det)))
    return out


def score_spec(doc: dict, case: dict, spec: dict, *, crop: dict, refs_dir: str, glossary: dict | None = None,
               draft=None, legacy: bool = False, partial: bool = False) -> dict:
    """사례 하나 채점. spec = 쓰일 스펙 dict(정답 스펙 또는 spec_from_draft), draft = Draft(초안이면 — review·제안 사용).
    partial = 정답 스펙이 일부 타깃만 가짐(그 타깃만 채점). 반환 행 dict(S 와 항목·게이트·메모)."""
    w = doc.get("weights") or {"G": 0.35, "K": 0.25, "C": 0.15, "B": 0.10, "F": 0.15}
    reviews = dict(getattr(draft, "review", None) or {})
    sugg = dict(getattr(draft, "suggestions", None) or {})
    st = {str(t.get("id")): t for t in spec.get("targets") or [] if isinstance(t, dict)}
    gates: list[tuple[str, bool, bool]] = []
    kws: list[tuple[str, bool]] = []
    forbidden = 0
    counts: list[tuple[str, bool]] = []
    boxes: list[tuple[str, bool, float]] = []
    notes: list[str] = []

    if legacy:
        dt = st.get("A") or {}
        rv = reviews.get("A") or {}

        def tkw(ct):
            return sum(any(_has_word(_field_text(dt, rv, f), wd) for wd in words) for f, words in ct.get("kw") or [])

        best = max(case["targets"], key=lambda ct: (tkw(ct), -case["targets"].index(ct)))
        pairs = [(best, dt, rv, "A")]
        notes.append(f"legacy: A ↔ 정답 {best['tid']}({best['label']})")
    else:
        cts = [ct for ct in case["targets"] if not partial or ct["tid"] in st]
        pairs = [(ct, st.get(ct["tid"]) or {}, reviews.get(ct["tid"]) or {}, ct["tid"]) for ct in cts]

    ok, probs = render_gate(crop, spec, refs_dir, glossary or {})
    gates.append(("렌더 예외 없음", ok, True))
    gates.append(("렌더 깨끗", ok and not probs, False))
    notes += probs[:6]
    if not partial:
        want_n = 1 if legacy else len(case["targets"])
        gates.append((f"타깃 수 {want_n}", len(st) == want_n, False))
    en = _all_text(spec, reviews)
    gates.append(("CJK 없음", not _CJK_RE.search(en), True))
    if case.get("no_quotes"):
        gates.append(("따옴표 인용 없음", not _QUOTE_RE.search(en), False))
    all_refs = [os.path.basename(r) for t in st.values() for r in _as_list(t.get("refs"))]
    for sub in case.get("refs_forbidden") or []:
        gates.append((f"금지 레퍼런스 없음({sub})", not any(sub in r for r in all_refs), False))
    for req in case.get("refs_required") or []:
        gates.append((f"필수 레퍼런스({req})", req in all_refs, False))
    if case.get("refs_empty"):
        gates.append(("레퍼런스 없음", not all_refs, False))

    for ct, t, rv, tid in pairs:
        mode = t.get("mode") or "ref_correct"
        action = t.get("action") or ("add" if mode == "sketch_guide" else "fix")
        if ct.get("mode_ok"):
            gates.append((f"{tid}.mode {mode}", mode in ct["mode_ok"], False))
        if ct.get("action_ok") and mode != "self_restore":
            gates.append((f"{tid}.action {action}", action in ct["action_ok"], False))
        if ct.get("shape_ok") and mode == "ref_correct" and action == "fix":
            gates.append((f"{tid}.shape {t.get('shape', 'moderate')}", t.get("shape", "moderate") in ct["shape_ok"], False))
        if ct.get("text_policy"):
            gates.append((f"{tid}.text {t.get('text', 'none')}", t.get("text", "none") in ct["text_policy"], True))
        for f, words in ct.get("kw") or []:
            kws.append((f"{tid}.{f}∋{'|'.join(words[:3])}", any(_has_word(_field_text(t, rv, f), wd) for wd in words)))
        for f, words in ct.get("kw_none") or []:
            forbidden += sum(_forbidden_hits(_field_text(t, rv, f), wd) for wd in words)
        for chk in ct.get("counts") or []:
            c_ok, desc = count_ok(chk, t, rv)
            counts.append((f"{tid} {desc}", c_ok))
        if ct.get("boxes") and (draft is not None or "bbox" in t):
            hit, iou = bbox_hit(t.get("bbox"), ct["boxes"])
            boxes.append((tid, hit, iou))

    for f, words in case.get("kw") or []:
        text = " ; ".join(_as_list(spec.get(f))) if f in _EN_CROP else en
        kws.append((f"crop.{f}∋{'|'.join(words[:3])}", any(_has_word(text, wd) for wd in words)))
    for fields, words in case.get("kw_none") or []:
        fl = fields if isinstance(fields, list) else [fields]
        for t in st.values():
            rv = reviews.get(str(t.get("id"))) or {}
            text = en if "*" in fl else " ; ".join(_field_text(t, rv, f) for f in fl)
            forbidden += sum(_forbidden_hits(text, wd) for wd in words)
            if "*" in fl:
                break

    if legacy and draft is not None:
        rest = [ct for ct in case["targets"] if ct is not pairs[0][0]]
        snames = " ; ".join(str(s.get("name") or "") + " " + str(s.get("reason") or "")
                            for s in sugg.get("targets") or [] if isinstance(s, dict))
        for ct in rest:
            name_kw = next((words for f, words in ct.get("kw") or [] if f == "name"), [])
            kws.append((f"제안이 {ct['tid']}({ct['label']}) 를 덮음", any(_has_word(snames, wd) for wd in name_kw)))
        ln = str(sugg.get("layer_name") or "")
        try:
            p = store.parse_crop_layer_name(ln)
            n_s = len([s for s in sugg.get("targets") or [] if isinstance(s, dict)])
            gates.append(("제안 레이어명 파싱", len(p["targets"]) == 1 + n_s, False))
        except ValueError as e:
            gates.append(("제안 레이어명 파싱", False, False))
            notes.append(f"레이어명 제안 오류: {e}")

    fmt: list[tuple[str, bool]] = []
    for ct, t, rv, tid in pairs:
        fmt += _format_checks(dict(t, id=tid), rv)
    pres = _as_list(spec.get("preserve"))
    fmt.append(("preserve 2–4 개", 2 <= len(pres) <= 4))

    parts: dict[str, float | None] = {
        "G": sum(g[1] for g in gates) / len(gates) if gates else None,
        "K": max(0.0, min(1.0, (sum(k[1] for k in kws) / len(kws) if kws else 1.0) - 0.25 * forbidden))
        if (kws or forbidden) else None,
        "C": sum(c[1] for c in counts) / len(counts) if counts else None,
        "B": sum(b[1] for b in boxes) / len(boxes) if boxes else None,
        "F": sum(f[1] for f in fmt) / len(fmt) if fmt else None,
    }
    wsum = sum(w[k] for k, v in parts.items() if v is not None)
    S = 100.0 * sum(w[k] * v for k, v in parts.items() if v is not None) / wsum if wsum else 0.0
    fatal = [g[0] for g in gates if g[2] and not g[1]]
    if fatal:
        S = min(S, FATAL_CAP)
    return {"id": case["id"], "crop_id": case["crop_id"], "S": round(S, 1),
            "parts": {k: (None if v is None else round(v, 3)) for k, v in parts.items()},
            "fatal": fatal, "gates": [[n, bool(o), bool(f)] for n, o, f in gates], "kw": [[n, bool(o)] for n, o in kws],
            "forbidden_hits": forbidden, "counts": [[n, bool(o)] for n, o in counts],
            "bbox": [[t, bool(h), i] for t, h, i in boxes], "format": [[n, bool(o)] for n, o in fmt], "notes": notes}


# 채점기 대조 예문(validate 가 함께 돌림). 개수: (본문 detail, 대상 명사, 정답 n, review counts, 기대 count_ok)
_SELFCHECK_COUNTS = [
    ("two black binder clips; one clip on each stack of four receipts", ["clip", "clips"], 2, None, True),
    ("two black binder clips; silver handles; one clip per stack", ["clip", "clips"], 2, None, True),
    ("two black binder clips with 2 holes per clip", ["clip", "clips"], 2, None, True),
    ("two black binder clips each gripping a stack of receipts", ["clip", "clips"], 2, None, True),
    ("three black binder clips; silver handles", ["clip", "clips"], 2, None, False),
    ("four loops; two loops on each side", ["loop", "loops"], 4, None, True),
    ("four rounded loops each edged in gold", ["loop", "loops"], 4, None, True),
    ("two round gold buttons each with a dark rim", ["button", "buttons"], 2, None, True),
    ("two round gold buttons, each with a dark rim", ["button", "buttons"], 2, None, True),
    ("three slim red pins each with a round tip", ["pin", "pins"], 2, {"part": "pins", "canvas": 2}, False),
    ("three slim red pins, each with a round tip", ["pin", "pins"], 2, {"part": "pins", "canvas": 2}, False),
    ("two slim red hairpins; rounded tips", ["pin", "pins"], 2, {"part": "hairpins", "canvas": 2}, True),
]
# 낱말: (본문, 낱말, 필수 키워드 _has_word 기대, 금지어 _forbidden_hits 기대)
_SELFCHECK_WORDS = [
    ("marigold flower", "gold", True, 0), ("rainbow stripes", "bow", True, 0), ("gold trim", "gold", True, 1),
    ("two bows", "bow", True, 1), ("crossed pins", "cross", True, 1), ("crosswise", "cross", False, 0),
    ("red hairpin", "pin", True, 0), ("no gold bands; gold band at the tip", "gold band", True, 1),
]


def selfcheck() -> list[str]:
    """대조 예문 중 기대와 다른 것(없으면 []). 개수 규칙·낱말 경계를 바꾸면 여기 예문이 먼저 깨진다."""
    bad = []
    for text, nouns, n, cnt, want in _SELFCHECK_COUNTS:
        rv = {"counts": [cnt]} if cnt else {}
        got, desc = count_ok({"nouns": nouns, "n": n, "review": ["canvas"]}, {"detail": text}, rv)
        if got is not want:
            bad.append(f"개수 {text!r} n={n}: {got} (기대 {want}, {desc})")
    for text, word, want_kw, want_hits in _SELFCHECK_WORDS:
        kw, hits = _has_word(text, word), _forbidden_hits(text, word)
        if (kw, hits) != (want_kw, want_hits):
            bad.append(f"낱말 {text!r} ∋ {word!r}: 키워드 {kw}·금지어 {hits} (기대 {want_kw}·{want_hits})")
    return bad


def validate(doc: dict) -> list[dict]:
    """정답 스펙 채점(bbox 없음 → B 제외). partial(_partial: true)은 정답이 있는 타깃만."""
    refs_dir = _refs_dir(doc)
    rows = []
    for case in doc["cases"]:
        if not case.get("gold_spec"):
            continue
        spec = json.loads((Path(doc["_dir"]) / case["gold_spec"]).read_text(encoding="utf-8"))
        partial = bool(spec.get("_partial"))
        tids = {str(t.get("id")) for t in spec.get("targets") or []} if partial else None
        crop = case_crop(doc, case, only_tids=tids)
        row = score_spec(doc, case, spec, crop=crop, refs_dir=refs_dir, partial=partial)
        row["partial"] = partial
        row["pass"] = row["S"] >= PASS_S
        rows.append(row)
    return rows


# ══════════════════════════════════════════════════════════════════════
# 원장 재정규화 채점
# ══════════════════════════════════════════════════════════════════════
def parse_select(text: str) -> dict:
    out = {}
    for part in [p for p in re.split(r"[,\s]+", str(text or "").strip()) if p]:
        if "=" not in part:
            raise ValueError(f"select 는 키=값 목록입니다: {part!r}")
        k, v = part.split("=", 1)
        k = k.strip().lower()
        if k not in ("model", "effort", "round", "backend", "detail"):
            raise ValueError(f"select 키는 model / effort / round / backend / detail: {k!r}")
        out[k] = v.strip()
    return out


def _select_ok(settings: dict, sel: dict) -> bool:
    s = settings or {}
    for k, v in sel.items():
        if k == "round":
            if str(s.get("draft_round", 0)) != v:
                return False
        elif k == "backend":
            b = str(s.get("backend") or "")
            if v not in (b, b.replace("vlm_", "")):
                return False
        elif k == "detail":
            if str(s.get("image_detail")) != v:
                return False
        elif str(s.get(k)) != v:
            return False
    return True


def _project_kind(m: dict, doc: dict) -> str:
    """프로젝트 이름이 cases.json 의 것이면 그대로, 아니면 평가 크롭에 '|' 레이어명이 하나도 없을 때 legacy."""
    for kind, name in (doc.get("projects") or {}).items():
        if m.get("project") == name:
            return kind
    ids = {c["crop_id"] for c in doc["cases"]}
    crops = [c for c in m["crops"] if c["id"] in ids]
    return "legacy" if crops and not any(store._PIPE_RE.search(str(c.get("layer_name") or "")) for c in crops) else "named"


def _with_req(led: dvlm.Ledger, rec: dict) -> dict:
    """done 레코드 + 그 시도 req 의 settings·body(Ledger.done 에는 둘 다 없고 all_done 에는 settings 만 있다)."""
    req = led.read(rec["crop_id"], rec["key"], rec["attempt"], "req") or {}
    return dict(rec, settings=req.get("settings"), body=req.get("body"))


def latest_done(root: str, sel: dict) -> dict[str, dict]:
    """crop_id(화풍 = "_style") → 선택에 맞는 가장 나중 done 레코드(+ settings·body)."""
    led = dvlm.Ledger(root)
    best: dict[str, dict] = {}
    for rec in led.all_done():
        if not _select_ok(rec.get("settings") or {}, sel):
            continue
        cur = best.get(rec["crop_id"])
        if cur is None or (str(rec.get("at") or ""), rec["attempt"]) > (str(cur.get("at") or ""), cur["attempt"]):
            best[rec["crop_id"]] = rec
    return {cid: _with_req(led, rec) for cid, rec in best.items()}


def _sent_from_body(body) -> tuple[list[str], tuple[int, int]] | None:
    """원장 req 본문 → (보낸 레퍼런스 이름 순서 = Image 2.., 캔버스 전송 크기). local_qwen·mock 은 images 목록,
    openai 는 input_image 항목(ledger_body 가 role·name·sent_wh 를 붙임). 모양을 모르면 None."""
    if not isinstance(body, dict):
        return None
    if isinstance(body.get("images"), list):
        ims = [(im[0], im[1], im[3:5]) for im in body["images"] if isinstance(im, list) and len(im) >= 5]
    else:
        ims = [(c.get("role"), c.get("name"), c.get("sent_wh")) for msg in body.get("input") or [] if isinstance(msg, dict)
               for c in msg.get("content") or [] if isinstance(c, dict) and c.get("type") == "input_image"]
    canvas = [wh for role, _n, wh in ims if role == "canvas"]
    if len(canvas) != 1 or not (isinstance(canvas[0], list) and len(canvas[0]) == 2):
        return None
    refs = [n for role, n, _wh in ims if role == "ref"]
    if not all(isinstance(n, str) for n in refs):
        return None
    return refs, (int(canvas[0][0]), int(canvas[0][1]))


def renormalize(root: str, m: dict, crop: dict, rec: dict, *, glossary: dict, memo: dict, constraints_spec=None):
    """원장 done 하나(settings·body 포함, latest_done / _with_req) → (ctx, req, Draft, 입력). 요청은 기록된 설정으로 다시
    만든다. 입력 = "same"(키 같음) | "changed"(키 다름 — ref_images 번호·px bbox 틀은 원장 req 본문의 보낸 이미지로 되살림)
    | "stale"(키 다르고 본문도 못 읽음 — 지금 문맥 기준이라 레퍼런스 대응을 믿을 수 없음)."""
    s = rec.get("settings") or {}
    fam = dvlm._family(str(s.get("backend") or "mock"))
    refs_dir = (m.get("source") or {}).get("refs_dir") or ""
    ctx = dvlm.build_crop_ctx(root, m, crop, refs_dir=refs_dir, glossary=glossary, backend=fam, memo=memo)
    req = dvlm.build_request(ctx, backend=fam, model=str(s.get("model") or "mock"), effort=s.get("effort") or "high",
                             image_detail=s.get("image_detail") or "high", draft_round=int(s.get("draft_round") or 0),
                             model_dir=str(QWEN_DIR) if fam == "local_qwen" else "")
    state = "same"
    if req.key != rec["key"]:
        sent = _sent_from_body(rec.get("body"))
        if sent is None:
            state = "stale"
        else:
            state = "changed"
            req.shown_refs = sent[0]
            if req.bbox_frame[0] == "px":
                req.bbox_frame = ("px", *sent[1])
    draft = dvlm.normalize(rec["parsed"], ctx, req, constraints=dvlm.constraints_for(ctx, constraints_spec),
                           glossary=glossary, repairs=rec.get("repairs"))
    return ctx, req, draft, state


def rescore(root: str, doc: dict, sel: dict) -> dict:
    """원장 재정규화 채점(0 호출). 반환 {root, kind, select, rows, used, summary}."""
    m = store.load_manifest(root)
    kind = _project_kind(m, doc)
    refs_dir = (m.get("source") or {}).get("refs_dir") or ""
    glossary = _glossary(root, m)
    recs = latest_done(root, sel)
    crops = {c["id"]: c for c in m["crops"]}
    memo: dict = {}
    rows, used = [], []
    for case in doc["cases"]:
        crop = crops.get(case["crop_id"])
        if crop is None:
            continue
        rec = recs.get(case["crop_id"])
        if rec is None:
            rows.append({"id": case["id"], "crop_id": case["crop_id"], "S": None, "missing": True})
            continue
        ctx, req, draft, state = renormalize(root, m, crop, rec, glossary=glossary, memo=memo)
        if state == "stale":  # 레퍼런스 번호를 지금 목록에 대면 다른 파일이 될 수 있다 — 채점하지 않는다
            rows.append({"id": case["id"], "crop_id": case["crop_id"], "S": None, "stale": True, "key": rec["key"],
                         "attempt": rec["attempt"],
                         "notes": [f"입력이 원장 이후 바뀜(키 {rec['key'][:8]} → {req.key[:8]})·원장 req 본문 없음 — 채점 안 함"]})
            continue
        order = [t.tid for t in ctx.targets]
        spec = spec_from_draft(draft, order)
        row = score_spec(doc, case, spec, crop=crop, refs_dir=refs_dir, glossary=glossary, draft=draft,
                         legacy=kind == "legacy")
        if state == "changed":
            row["input_changed"] = True
            row["notes"].insert(0, f"입력이 원장 이후 바뀜(키 {rec['key'][:8]} → {req.key[:8]}) — 레퍼런스 번호·bbox 틀은 원장 "
                                   f"요청 기준, 타깃·사람 값은 지금 매니페스트 기준")
        row.update(key=rec["key"], attempt=rec["attempt"], credits=rec.get("credits"), elapsed_s=rec.get("elapsed_s"),
                   found=[tid for tid, rv in (draft.review or {}).items() if rv.get("found") is True])
        rows.append(row)
        used.append({"crop_id": case["crop_id"], "key": rec["key"], "attempt": rec["attempt"]})
    st = recs.get(dvlm.STYLE_ID)
    if st is not None:
        used.append({"crop_id": dvlm.STYLE_ID, "key": st["key"], "attempt": st["attempt"]})
    scored = [r for r in rows if r.get("S") is not None]
    bb = [b for r in scored for b in r["bbox"] if b[0] in (r.get("found") or [])]
    summary = {
        "n": len(scored), "mean_S": round(statistics.mean(r["S"] for r in scored), 2) if scored else None,
        "fatal_pass": sum(not r["fatal"] for r in scored),
        "count_wrong": [r["id"] for r in scored if r["parts"]["C"] is not None and r["parts"]["C"] < 1
                        and r["id"] in ("E3", "E4", "E5", "E6", "E7")],
        "bbox_hit_found": round(sum(b[1] for b in bb) / len(bb), 3) if bb else None,
        "credits": round(sum(float(r.get("credits") or 0) for r in scored), 2),
        "input_changed": [r["id"] for r in scored if r.get("input_changed")],
        "stale": [r["id"] for r in rows if r.get("stale")],
    }
    return {"root": os.path.abspath(root), "kind": kind, "select": sel, "rows": rows, "used": used, "summary": summary}


def _md_table(rows: list[dict]) -> list[str]:
    out = ["| 사례 | 크롭 | S | G | K | C | B | F | 치명 | 메모 |", "|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        if r.get("S") is None:
            why = "입력 바뀜·원장 req 본문 없음(stale) — 채점 안 함" if r.get("stale") else "원장 done 없음"
            out.append(f"| {r['id']} | {r['crop_id']} | - | | | | | | | {why} |")
            continue
        p = r["parts"]
        f = lambda v: "-" if v is None else f"{v:.2f}"  # noqa: E731
        memo = ("[입력 바뀜] " if r.get("input_changed") else "") + "; ".join(
            [n for n, ok, _fat in r["gates"] if not ok] + [n for n, ok in r["kw"] if not ok]
            + [n for n, ok in r["counts"] if not ok])[:160]
        out.append(f"| {r['id']} | {r['crop_id']} | {r['S']:.1f} | {f(p['G'])} | {f(p['K'])} | {f(p['C'])} | {f(p['B'])} "
                   f"| {f(p['F'])} | {', '.join(r['fatal']) or '-'} | {memo} |")
    return out


def write_scores(res: dict, tag: str, out_dir: str | os.PathLike) -> tuple[Path, Path]:
    if not re.fullmatch(r"[A-Za-z0-9_.\-]+", tag or ""):
        raise ValueError(f"tag 는 영문·숫자·_.- 만: {tag!r}")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    pj, pm = out / f"{tag}.json", out / f"{tag}.md"
    store.write_json_atomic(str(pj), dict(res, tag=tag))
    s = res["summary"]
    lines = [f"# {tag} — {res['kind']} · select {res['select'] or '(전부)'}", "",
             f"- root: `{res['root']}`",
             f"- 사례 {s['n']} · 평균 S {s['mean_S']} · 치명 게이트 통과 {s['fatal_pass']}/{s['n']} · "
             f"개수 틀림(E3–E7) {', '.join(s['count_wrong']) or '없음'} · found bbox 적중 {s['bbox_hit_found']} · "
             f"크레딧 {s['credits']}"]
    if s.get("input_changed") or s.get("stale"):
        lines.append(f"- 원장 이후 입력 바뀜: {', '.join(s.get('input_changed') or []) or '없음'} (레퍼런스 번호는 원장 요청 기준) · "
                     f"stale(채점 안 함): {', '.join(s.get('stale') or []) or '없음'}")
    lines += [""] + _md_table(res["rows"])
    pm.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return pj, pm


def compare(a: dict, b: dict) -> dict:
    ra = {r["id"]: r for r in a["rows"] if r.get("S") is not None}
    rb = {r["id"]: r for r in b["rows"] if r.get("S") is not None}
    ids = [i for i in ra if i in rb]
    diffs = [rb[i]["S"] - ra[i]["S"] for i in ids]
    mean = statistics.mean(diffs) if diffs else None
    se = statistics.stdev(diffs) / math.sqrt(len(diffs)) if len(diffs) >= 2 else None
    return {"ids": ids, "rows": [[i, ra[i]["S"], rb[i]["S"], round(rb[i]["S"] - ra[i]["S"], 1)] for i in ids],
            "mean_diff": None if mean is None else round(mean, 2), "se": None if se is None else round(se, 2),
            "max_abs_diff": round(max((abs(d) for d in diffs), default=0.0), 1),
            "fatal_a": sum(bool(ra[i]["fatal"]) for i in ids), "fatal_b": sum(bool(rb[i]["fatal"]) for i in ids)}


def _flat(v, path: str = "") -> dict:
    """중첩 dict → {"targets.A.detail": 값}. 리스트는 dict·리스트가 들었을 때만 펼친다([i])."""
    if isinstance(v, dict):
        out = {}
        for k, x in v.items():
            out.update(_flat(x, f"{path}.{k}" if path else str(k)))
        return out
    if isinstance(v, list) and any(isinstance(x, (dict, list)) for x in v):
        out = {}
        for i, x in enumerate(v):
            out.update(_flat(x, f"{path}[{i}]"))
        return out
    return {path: v}


def determinism(root: str, doc: dict, sel: dict, rounds: tuple[str, str] = ("0", "1")) -> dict:
    """§8.2 C10 결정성(Qwen): 두 draft_round 의 원장 done 을 지금 정규화로 다시 돌려 크롭별로 비교한다(0 호출).
    비교 = spec_from_draft(노드가 쓰는 필드) + _review 의 counts·found, 화풍은 normalize_style. 같은 S 라도 내용이 다르면
    다름이다. 반환 {rows: [{id, crop_id, keys, status(same|diff|missing), diffs: [[필드, a, b]], inputs}], summary}."""
    if "round" in sel:
        raise ValueError("determinism 의 select 에는 round 를 넣지 않습니다 — --rounds 로 두 값을 줍니다")
    m = store.load_manifest(root)
    glossary = _glossary(root, m)
    crops = {c["id"]: c for c in m["crops"]}
    case_of = {c["crop_id"]: c["id"] for c in doc["cases"]}
    recs = [latest_done(root, dict(sel, round=str(r))) for r in rounds]
    memo: dict = {}
    rows = []
    for cid in sorted(set(recs[0]) | set(recs[1])):
        pair = [recs[0].get(cid), recs[1].get(cid)]
        row = {"id": case_of.get(cid, "화풍" if cid == dvlm.STYLE_ID else "-"), "crop_id": cid,
               "keys": [x["key"] if x else None for x in pair], "diffs": [], "inputs": []}
        if None in pair or (cid != dvlm.STYLE_ID and cid not in crops):
            row["status"] = "missing"
            rows.append(row)
            continue
        outs = []
        for x in pair:
            if cid == dvlm.STYLE_ID:
                outs.append({"style_note": dvlm.normalize_style(x["parsed"], x.get("repairs"))[0]})
                continue
            ctx, _req, draft, state = renormalize(root, m, crops[cid], x, glossary=glossary, memo=memo)
            spec = spec_from_draft(draft, [t.tid for t in ctx.targets])
            row["inputs"].append(state)
            outs.append({"crop": {k: v for k, v in spec.items() if k != "targets"},
                         "targets": {t["id"]: {k: v for k, v in t.items() if k != "id"} for t in spec["targets"]},
                         "review": {tid: {k: rv.get(k) for k in ("counts", "found")} for tid, rv in (draft.review or {}).items()}})
        fa, fb = _flat(outs[0]), _flat(outs[1])
        row["diffs"] = [[p, fa.get(p), fb.get(p)] for p in sorted(set(fa) | set(fb)) if fa.get(p) != fb.get(p)]
        row["status"] = "diff" if row["diffs"] else "same"
        rows.append(row)
    summary = {k: sum(1 for r in rows if r["status"] == k) for k in ("same", "diff", "missing")}
    return {"root": os.path.abspath(root), "select": sel, "rounds": list(rounds), "rows": rows, "summary": summary}


# ══════════════════════════════════════════════════════════════════════
# 크레딧
# ══════════════════════════════════════════════════════════════════════
def _num(v) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _maybe_billed(recs: dict[str, dict]) -> bool:
    """한 시도의 기록들({종류: 레코드}, 깨진 파일은 {}) → 과금됐을 수 있음: created·resp·done 이 있거나, failed 가
    maybe_billed False 가 아니거나(깨짐 포함), 가장 나중 기록이 inflight(POST 뒤 소식 없음 = orphan). inflight 뒤의
    rejected(과금 전 거부)·req 만 남은 시도는 아니다."""
    if any(k in recs for k in ("created", "resp", "done")):
        return True
    if "failed" in recs and recs["failed"].get("maybe_billed") is not False:
        return True
    last = max(recs, key=lambda k: (recs[k].get("_seq") if isinstance(recs[k].get("_seq"), int) else 0, dvlm._KIND_RANK[k]))
    return last == "inflight"


def vlm_credits(root: str) -> dict:
    """VLM 원장 크레딧. 반환 {"credits": 보고된 합계(시도마다 done/failed/resp/created 기록 중 최댓값), "est": 추정 합계,
    "attempts": 시도 수, "est_attempts": 추정으로 센 시도 수}. 크레딧 보고가 하나도 없는데 과금됐을 수 있는 openai 시도
    (_maybe_billed — 헤더 없음·5xx·orphan·poll GET 4xx)는 그 시도 req 의 est 보통값으로 센다(단계 게이트가 덜 세지 않게).
    local_qwen(est 가 초)·mock 시도는 추정하지 않는다."""
    base = Path(root) / dvlm.LEDGER_DIR
    out = {"credits": 0.0, "est": 0.0, "attempts": 0, "est_attempts": 0}
    if not base.is_dir():
        return out
    for cdir in base.iterdir():
        if not cdir.is_dir() or cdir.name == "img":
            continue
        for kdir in cdir.iterdir():
            if not kdir.is_dir():
                continue
            per: dict[int, dict[str, dict]] = {}
            for f in kdir.iterdir():
                mt = _LEDGER_FILE_RE.fullmatch(f.name)
                if not mt:
                    continue
                try:
                    rec = json.loads(f.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    rec = {}
                per.setdefault(int(mt.group(1)), {})[mt.group(2)] = rec if isinstance(rec, dict) else {}
            for recs in per.values():
                out["attempts"] += 1
                vals = [_num(rec.get(k)) for rec in recs.values() for k in ("credits", "credits_poll", "credits_create")]
                vals = [v for v in vals if v is not None]
                if vals:
                    out["credits"] += max(vals)
                    continue
                req = recs.get("req") or {}
                est = req.get("est") if isinstance(req.get("est"), list) else []
                if ((req.get("settings") or {}).get("backend") == dvlm.BACKEND_NAMES["openai"] and est
                        and _num(est[0]) is not None and _maybe_billed(recs)):
                    out["est"] += _num(est[0])
                    out["est_attempts"] += 1
    out["credits"], out["est"] = round(out["credits"], 2), round(out["est"], 2)
    return out


def budget(roots: list[str], stage_est: float | None = None) -> dict:
    """누적 = VLM 보고 + VLM 추정 + GPT Image calls[].credits. total 에 추정이 들어가고 est 는 그중 추정분(따로 표시)."""
    rows = []
    for r in roots:
        v = vlm_credits(r)
        try:
            calls = store.load_manifest(r).get("calls") or []
        except ValueError:
            calls = []
        mc = round(sum(_num(c.get("credits")) or 0.0 for c in calls if isinstance(c, dict)), 2)
        rows.append({"root": os.path.abspath(r), "vlm": v["credits"], "vlm_est": v["est"], "vlm_attempts": v["attempts"],
                     "vlm_est_attempts": v["est_attempts"], "calls": mc,
                     "n_calls": sum(1 for c in calls if isinstance(c, dict))})
    total = round(sum(x["vlm"] + x["vlm_est"] + x["calls"] for x in rows), 2)
    out = {"rows": rows, "total": total, "est": round(sum(x["vlm_est"] for x in rows), 2),
           "remaining": round(BUDGET_TOTAL - total, 2)}
    if stage_est is not None:
        out["stage_est"] = stage_est
        out["gate_ok"] = total + 1.5 * stage_est <= GATE_LIMIT
        out["credit_cap"] = max(0, int(min(1.6 * stage_est, CAP_LIMIT - total)))
    return out


# ══════════════════════════════════════════════════════════════════════
# A/B
# ══════════════════════════════════════════════════════════════════════
def _copy_project(src_root: str, dst_root: str, name: str, keep: set[str], replace: bool) -> None:
    if os.path.exists(dst_root):
        if not replace:
            raise ValueError(f"{dst_root} 가 이미 있습니다 — 다시 만들려면 --replace")
        shutil.rmtree(dst_root)
    os.makedirs(dst_root)
    shutil.copy2(os.path.join(src_root, store.MANIFEST_NAME), os.path.join(dst_root, store.MANIFEST_NAME))
    if os.path.isdir(os.path.join(src_root, "inputs")):
        shutil.copytree(os.path.join(src_root, "inputs"), os.path.join(dst_root, "inputs"))

    def mutate(m):
        m["project"] = name
        m["crops"] = [c for c in m["crops"] if c["id"] in keep]
        for k in ("jobs", "candidates", "calls", "exports"):
            m[k] = []
        for k in ("analysis", "picks", "rerolls", "rejects", "approvals"):
            m[k] = {}
        if isinstance(m.get("source"), dict):
            m["source"]["glossary"] = ""

    store.update_manifest(dst_root, mutate)


def ab_setup(src_root: str, doc: dict, tag: str, *, scores_dir: str | os.PathLike | None = None,
             replace: bool = False) -> dict:
    """R1(named) → ab_minimal(스펙 없음) / ab_draft(T 의 초안을 merge_spec(fill)로) / ab_gold(정답 E1·E2·E5, name 은 빼서
    용어집 이름을 씀). 세 프로젝트 모두 minimal_names 로 만든 같은 glossary.json. 반환 {group: root, …, "rows": […]}."""
    res = json.loads((Path(scores_dir or EVAL_DIR / "scores") / f"{tag}.json").read_text(encoding="utf-8"))
    used = {u["crop_id"]: u for u in res.get("used") or []}
    ab_cases = [c for c in doc["cases"] if c.get("ab")]
    gold_cases = [c for c in ab_cases if c.get("gold_spec") and c["id"] in ("E1", "E2", "E5")]
    glossary = {}
    for c in ab_cases:
        glossary.update(c.get("minimal_names") or {})
    projects_dir = os.path.dirname(os.path.abspath(src_root))
    names = doc.get("ab_projects") or {g: f"ab_{g}" for g in AB_GROUPS}
    out: dict = {"rows": []}
    led = dvlm.Ledger(src_root)
    for group in AB_GROUPS:
        keep = {c["crop_id"] for c in (gold_cases if group == "gold" else ab_cases)}
        root = os.path.join(projects_dir, names[group])
        _copy_project(src_root, root, names[group], keep, replace)
        store.write_json_atomic(os.path.join(root, "glossary.json"), glossary)
        out[group] = root
    # draft: 원장 초안을 지금의 ab_draft 문맥(용어집 이름 → name 양보)으로 다시 정규화해 fill 로 쓴다
    root = out["draft"]
    m = store.load_manifest(root)
    gl = _glossary(root, m)
    style = None
    if dvlm.STYLE_ID in used:
        u = used[dvlm.STYLE_ID]
        rec = led.done(dvlm.STYLE_ID, u["key"])
        if rec is not None:
            rec = _with_req(led, rec)
            style, _flags = dvlm.normalize_style(rec["parsed"], rec.get("repairs"))
            obs = rec["parsed"].get("observations")
            prov = _provenance(rec, observations=obs if isinstance(obs, list) else [])
            dvlm.write_spec(root, dvlm.PROJECT_SPEC_ID,
                            lambda cur: dvlm.merge_project(cur, style, mode="fill", provenance=prov))
    memo: dict = {}
    for crop in m["crops"]:
        u = used.get(crop["id"])
        rec = led.done(crop["id"], u["key"]) if u else None
        if rec is None:
            out["rows"].append({"group": "draft", "crop_id": crop["id"], "status": f"{tag} 에 이 크롭 초안 없음"})
            continue
        rec = _with_req(led, rec)
        ctx, req, draft, state = renormalize(root, m, crop, rec, glossary=gl, memo=memo)
        prov = _provenance(rec, hint=ctx.hint, refs_shown=list(req.shown_refs))
        mr, _bk = dvlm.write_spec(root, crop["id"], lambda cur, d=draft, c=ctx, p=prov: dvlm.merge_spec(
            cur, d, c, mode="fill", project_style=style, provenance=p))
        out["rows"].append({"group": "draft", "crop_id": crop["id"], "status": ("씀" if mr.changed else "같음") + (
            " — 원장 req 본문 없음, 레퍼런스 번호를 지금 목록에 댐(확인 필요)" if state == "stale" else "")})
    # gold: 정답 스펙 사본(name 제거 — 세 군이 같은 용어집 이름), 가이드 파일 복사
    root = out["gold"]
    for c in gold_cases:
        spec = json.loads((Path(doc["_dir"]) / c["gold_spec"]).read_text(encoding="utf-8"))
        for t in spec.get("targets") or []:
            t.pop("name", None)
        g = spec.get("guide")
        if g:
            dst = os.path.join(root, *Path(g).parts)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(Path(doc["_dir"]) / g, dst)
        os.makedirs(os.path.join(root, "specs"), exist_ok=True)
        store.write_json_atomic(os.path.join(root, "specs", f"{c['crop_id']}.json"), spec)
        out["rows"].append({"group": "gold", "crop_id": c["crop_id"], "status": "정답 스펙"})
    return out


def _provenance(rec: dict, **extra) -> dict:
    """원장 done(+ settings, _with_req) → _draft 출처 기록(§5.1 모양 — 노드 _draft_provenance 와 같은 키). extra = 크롭은
    hint·refs_shown, 화풍은 observations. merge_spec / merge_project 에 그대로 넘긴다."""
    s = rec.get("settings") or {}
    return {"version": s.get("version") or dvlm.DRAFT_VERSION, "normalizer": dvlm.NORM_VERSION,
            "pass_plan": s.get("pass_plan") or dvlm.PASS_PLAN, "key": rec["key"], "attempt": rec["attempt"],
            "backend": s.get("backend"), "model": s.get("model"), "effort": s.get("effort"),
            "image_detail": s.get("image_detail"), "round": s.get("draft_round", 0), "prompt": s.get("prompt"),
            "drafted": rec.get("at"), "credits": rec.get("credits"), "transport": rec.get("transport") or {}, **extra}


def _group_of(m: dict, doc: dict) -> str:
    names = doc.get("ab_projects") or {}
    for g, n in names.items():
        if m.get("project") == n:
            return g
    return str(m.get("project"))


def _thumb(path: str, side: int) -> Image.Image:
    with Image.open(path) as im:
        im = im.convert("RGB")
        s = side / max(im.size)
        return im.resize((max(1, round(im.width * s)), max(1, round(im.height * s))), Image.LANCZOS)


def _ab_label(i: int) -> str:
    return chr(ord("A") + i) if i < 26 else f"Z{i}"


def _read_scores(p: str | os.PathLike) -> list[dict]:
    """scores.csv 행. 한국어 엑셀의 "CSV (쉼표로 분리)" 저장은 CP949 라 UTF-8 로 안 읽히면 CP949 로."""
    raw = Path(p).read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp949")
    return list(csv.DictReader(text.splitlines(keepends=True)))


def ab_sheet(roots: list[str], doc: dict, out_dir: str | os.PathLike | None = None, *, side: int = 384,
             seed: int = 20261007) -> dict:
    """크롭별 맹검 시트: 맨 왼쪽 = 원래 캔버스(라벨 0), 나머지 후보를 섞어 A, B, … 라벨. key.json 에 정답, scores.csv 틀.
    다시 실행해도 key.json 에 이미 있는 후보(normcase(abspath(root))·key)의 라벨은 그대로 둔다 — 새 후보만 뒤 라벨을 섞어 받고, 사라진
    후보(거절 등)는 "gone" 으로 남겨 라벨을 다시 쓰지 않는다. scores.csv 는 고치지 않고 새 라벨 줄만 덧붙인다.
    scores.csv 가 key.json 과 맞지 않으면(key.json 없음, key 에 없는 라벨) 아무것도 쓰지 않고 ValueError."""
    out = Path(out_dir or EVAL_DIR / "ab")
    out.mkdir(parents=True, exist_ok=True)
    kp, sc = out / "key.json", out / "scores.csv"
    old = json.loads(kp.read_text(encoding="utf-8")) if kp.is_file() else None
    old_crops = (old or {}).get("crops") or {}
    have_rows: set[tuple[str, str]] = set()
    if sc.is_file():
        have_rows = {((r.get("crop_id") or "").strip(), (r.get("label") or "").strip()) for r in _read_scores(sc)}
        bad = sorted(f"{c}:{lb}" for c, lb in have_rows if c and lb not in (old_crops.get(c) or {}))
        if old is None and have_rows:
            raise ValueError(f"{sc} 는 있는데 key.json 이 없습니다 — 라벨이 어느 후보인지 알 수 없어 멈춥니다"
                             f"(채점한 scores.csv 면 key.json 을 되살리고, 아니면 scores.csv 를 옮긴 뒤 다시)")
        if bad:
            raise ValueError(f"scores.csv 의 라벨이 key.json 에 없습니다: {', '.join(bad[:8])} — 다른 key 로 만든 scores.csv 로 "
                             f"보여 멈춥니다")
    if old is not None:
        seed = int(old.get("seed", seed))
    per_crop: dict[str, list[dict]] = {}
    canvas: dict[str, str] = {}
    for r in roots:
        m = store.load_manifest(r)
        g = _group_of(m, doc)
        crops = {c["id"]: c for c in m["crops"]}
        seen_files: set[tuple[str, str]] = set()  # 다중 타깃 셀의 결과는 타깃마다(r_…_A, r_…_B) 같은 파일로 등록된다 → 한 장만
        for c in m.get("candidates") or []:
            if not isinstance(c, dict) or c.get("crop_id") not in crops or (m.get("rejects") or {}).get(c.get("key")):
                continue
            fp = store.resolve_path(r, c["file"])
            if (c["crop_id"], os.path.normcase(fp)) in seen_files:
                continue
            seen_files.add((c["crop_id"], os.path.normcase(fp)))
            per_crop.setdefault(c["crop_id"], []).append({"group": g, "root": os.path.abspath(r), "key": c["key"], "file": fp})
            canvas.setdefault(c["crop_id"], store.resolve_path(r, crops[c["crop_id"]]["source"]))
    key: dict = {}
    font = ImageFont.load_default()
    rows_csv = []
    for cid in sorted(set(per_crop) | set(old_crops)):
        prev = dict(old_crops.get(cid) or {})
        by_id = {(os.path.normcase(c.get("root") or ""), c.get("key")): lb for lb, c in prev.items()}
        cur, new = {}, []
        for c in per_crop.get(cid) or []:
            lb = by_id.get((os.path.normcase(c["root"]), c["key"]))
            if lb is None:
                new.append(c)
            else:
                cur[lb] = c
        start = len(prev)
        random.Random(f"{seed}:{cid}" if start == 0 else f"{seed}:{cid}:{start}").shuffle(new)
        cur.update({_ab_label(start + i): c for i, c in enumerate(new)})
        key[cid] = {lb: cur.get(lb) or dict(c, gone=True) for lb, c in prev.items()}
        key[cid].update({lb: c for lb, c in cur.items() if lb not in prev})
        rows_csv += [[cid, lb, "", "", ""] for lb in key[cid] if (cid, lb) not in have_rows and lb in cur]
        if cid not in per_crop:
            continue
        labels = [lb for lb in key[cid] if lb in cur]
        tiles = [("0", _thumb(canvas[cid], side))] + [(lb, _thumb(cur[lb]["file"], side)) for lb in labels]
        cols = min(6, len(tiles))
        rows_n = math.ceil(len(tiles) / cols)
        sheet = Image.new("RGB", (cols * (side + 8) + 8, rows_n * (side + 28) + 8), (40, 40, 40))
        d = ImageDraw.Draw(sheet)
        for i, (lb, im) in enumerate(tiles):
            x, y = 8 + (i % cols) * (side + 8), 8 + (i // cols) * (side + 28)
            sheet.paste(im, (x, y + 20))
            d.text((x + 2, y + 2), f"{cid}  {lb}" if lb != "0" else f"{cid}  0 = before", fill=(255, 255, 255), font=font)
        sheet.save(out / f"sheet_{cid}.jpg", quality=92)
    all_roots: dict[str, str] = {}  # 같은 폴더를 다른 대소문자(h: / H:)로 줘도 한 번만
    for r in list((old or {}).get("roots") or []) + [os.path.abspath(r) for r in roots]:
        all_roots.setdefault(os.path.normcase(r), r)
    store.write_json_atomic(str(kp), {"seed": seed, "roots": list(all_roots.values()), "crops": key})
    if not sc.exists():
        with open(sc, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["crop_id", "label", "rank", "unusable", "note"])
            w.writerows(rows_csv)
    elif rows_csv:  # 채점 중인 파일 — 기존 줄은 그대로, 새 라벨 줄만 뒤에(ASCII 라 CP949 파일에 붙여도 됨)
        tail = sc.read_bytes()[-1:]
        with open(sc, "a", newline="", encoding="utf-8") as f:
            if tail not in (b"", b"\n"):
                f.write("\r\n")
            csv.writer(f).writerows(rows_csv)
    gone = sum(1 for v in key.values() for c in v.values() if c.get("gone"))
    return {"crops": len(key), "candidates": sum(len(v) for v in key.values()) - gone, "gone": gone,
            "new_rows": len(rows_csv), "out": str(out)}


def _srgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    c = rgb.astype(np.float64) / 255.0
    c = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    xyz = c @ np.array([[0.4124564, 0.3575761, 0.1804375], [0.2126729, 0.7151522, 0.0721750],
                        [0.0193339, 0.1191920, 0.9503041]]).T
    xyz /= np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > (6 / 29) ** 3, np.cbrt(xyz), xyz / (3 * (6 / 29) ** 2) + 4 / 29)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], -1)


def _gold_mask(doc: dict, case: dict, rect, canvas_wh) -> np.ndarray | None:
    """사례의 정답 마스크(합집합)를 크롭 rect 좌표로. meta.json 의 레이어 마스크 bbox 로 배치. 없으면 None."""
    names = [n for v in (case.get("gold_masks") or {}).values() for n in v]
    if not names:
        return None
    mdir = Path(doc["_dir"]) / "gold_masks"
    meta = json.loads((mdir / "meta.json").read_text(encoding="utf-8"))
    by_tag = {L["tag"]: L for L in meta["layers"]}
    x0, y0, x1, y1 = (int(v) for v in rect)
    out = np.zeros((y1 - y0, x1 - x0), bool)
    for n in names:
        L, p = by_tag.get(n), mdir / f"{n}_mask.png"
        if L is None or not (L.get("mask") or {}).get("bbox") or not p.is_file():
            continue
        mx0, my0, _mx1, _my1 = L["mask"]["bbox"]
        with Image.open(p) as im:
            a = np.asarray(im.convert("L")) >= 128
        ix0, iy0 = max(x0, mx0), max(y0, my0)
        ix1, iy1 = min(x1, mx0 + a.shape[1]), min(y1, my0 + a.shape[0])
        if ix1 > ix0 and iy1 > iy0:
            out[iy0 - y0:iy1 - y0, ix0 - x0:ix1 - x0] |= a[iy0 - my0:iy1 - my0, ix0 - mx0:ix1 - mx0]
    return out if out.any() else None


def _mask_de76(doc: dict, case: dict, crop: dict, cand_file: str, final: Image.Image | None) -> float | None:
    if final is None:
        return None
    rect = crop["rect"]
    mask = _gold_mask(doc, case, rect, final.size)
    if mask is None:
        return None
    x0, y0, x1, y1 = (int(v) for v in rect)
    ref = np.asarray(final.crop((x0, y0, x1, y1)).convert("RGB"))
    with Image.open(cand_file) as im:
        cand = np.asarray(im.convert("RGB").resize((x1 - x0, y1 - y0), Image.LANCZOS))
    d = np.sqrt(((_srgb_to_lab(ref) - _srgb_to_lab(cand)) ** 2).sum(-1))
    return round(float(d[mask].mean()), 2)


def ab_tally(scores_csv: str | os.PathLike, doc: dict, key_json: str | os.PathLike | None = None, *,
             with_de: bool = True) -> dict:
    """scores.csv(crop_id,label,rank(1=최고),unusable,note) → Borda·승수·쓸 수 없음, 보조 지표. D5 판정 포함.
    크롭별 승(D5) = 그 크롭에서 draft 군 Borda 합 vs minimal 군 Borda 합. 군의 최고 순위 비교는 wins_best(보조)."""
    kp = Path(key_json) if key_json else Path(scores_csv).parent / "key.json"
    key = json.loads(kp.read_text(encoding="utf-8"))["crops"]
    by_crop: dict[str, list[dict]] = {}
    for row in _read_scores(scores_csv):
        cid, lb = (row.get("crop_id") or "").strip(), (row.get("label") or "").strip()
        info = (key.get(cid) or {}).get(lb)
        if info is None:
            continue
        rank = (row.get("rank") or "").strip()
        # 빈칸이 아니면 표시로 본다 — 엑셀 CP949 저장은 ✗·❌ 같은 표시를 ? 로 바꾼다
        unusable = (row.get("unusable") or "").strip().lower() not in ("", "0", "n", "no", "false")
        if rank and not rank.isdigit():
            if not unusable:
                raise ValueError(f"{scores_csv}: {cid} {lb} 의 rank {rank!r} 가 정수가 아닙니다(쓸 수 없음이면 unusable 칸에 표시)")
            rank = ""
        by_crop.setdefault(cid, []).append({"label": lb, "group": info["group"], "rank": int(rank) if rank else None,
                                            "unusable": unusable, "key": info["key"], "root": info["root"],
                                            "file": info["file"]})
    groups: dict[str, dict] = {}
    wins = {"draft": 0, "minimal": 0, "tie": 0}
    wins_best = {"draft": 0, "minimal": 0, "tie": 0}   # 보조: 군의 최고 순위 한 장끼리
    per_crop = {}

    def judge(a, b):
        return "draft" if a > b else "minimal" if b > a else "tie"

    for cid, items in by_crop.items():
        ranked = [x for x in items if x["rank"] is not None]
        k = len(ranked)
        borda: dict[str, int] = {}
        n_ranked: dict[str, int] = {}
        best: dict[str, int] = {}
        for x in items:
            g = groups.setdefault(x["group"], {"borda": 0, "n": 0, "unusable": 0, "mad": [], "de76": []})
            g["n"] += 1
            g["unusable"] += x["unusable"]
            borda.setdefault(x["group"], 0)  # 전부 쓸 수 없는 군도 0 점으로 겨룬다(크롭 승에서 빠지지 않게)
            n_ranked.setdefault(x["group"], 0)
            if x["rank"] is not None:
                g["borda"] += k - x["rank"]
                borda[x["group"]] = borda.get(x["group"], 0) + k - x["rank"]
                n_ranked[x["group"]] = n_ranked.get(x["group"], 0) + 1
                best[x["group"]] = min(best.get(x["group"], 10 ** 6), x["rank"])
        pc = {"borda": borda, "n": n_ranked, "best": best}
        if "draft" in borda and "minimal" in borda:
            pc["win"] = judge(borda["draft"], borda["minimal"])
            wins[pc["win"]] += 1
            wins_best[judge(-best.get("draft", 10 ** 6), -best.get("minimal", 10 ** 6))] += 1
            if n_ranked["draft"] and n_ranked["minimal"] and n_ranked["draft"] != n_ranked["minimal"]:
                pc["uneven"] = True  # Borda 합은 후보가 많은 군에 유리 — 표에 표시
        per_crop[cid] = pc
    final = None
    fp = _ref_root(doc) / doc.get("final_composite", "")
    if with_de and fp.is_file():
        Image.MAX_IMAGE_PIXELS = None
        final = Image.open(fp)
    cases = {c["crop_id"]: c for c in doc["cases"]}
    manifests: dict[str, dict] = {}
    for cid, items in by_crop.items():
        for x in items:
            m = manifests.get(x["root"])
            if m is None:
                m = manifests[x["root"]] = store.load_manifest(x["root"])
            g = groups[x["group"]]
            for entry in ((m.get("analysis") or {}).get(x["key"]) or {}).values():
                if isinstance(entry, dict) and isinstance((entry.get("gates") or {}).get("outside_mad"), (int, float)):
                    g["mad"].append(float(entry["gates"]["outside_mad"]))
                    break
            crop = next((c for c in m["crops"] if c["id"] == cid), None)
            if crop is not None and cid in cases:
                de = _mask_de76(doc, cases[cid], crop, x["file"], final)
                if de is not None:
                    g["de76"].append(de)
    for g in groups.values():
        g["mad"] = round(statistics.mean(g["mad"]), 2) if g["mad"] else None
        g["de76"] = round(statistics.mean(g["de76"]), 2) if g["de76"] else None
    d = groups.get("draft") or {}
    mn = groups.get("minimal") or {}
    d5 = wins["draft"] >= 4 and d.get("unusable", 0) <= mn.get("unusable", 0)
    return {"groups": groups, "wins": wins, "wins_best": wins_best, "per_crop": per_crop, "d5_default_use": d5}


# ══════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════
def _print_rows(rows: list[dict]) -> None:
    for line in _md_table(rows):
        print(line)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="eval_bmk_design_patch_vlm", description="Design Patch Spec Draft 평가 도구(유료 호출 없음)")
    ap.add_argument("--cases", default=None, help="cases.json (기본 _proto/vlm_eval/cases.json)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("setup")
    p.add_argument("--named", required=True)
    p.add_argument("--legacy", required=True)
    sub.add_parser("validate")
    p = sub.add_parser("score")
    p.add_argument("--root", required=True)
    p.add_argument("--select", default="")
    p.add_argument("--tag", required=True)
    p.add_argument("--out", default=None)
    p = sub.add_parser("compare")
    p.add_argument("a")
    p.add_argument("b")
    p.add_argument("--dir", default=None)
    p = sub.add_parser("determinism")
    p.add_argument("--root", required=True)
    p.add_argument("--select", default="")
    p.add_argument("--rounds", default="0,1", help="비교할 두 draft_round(기본 0,1)")
    p = sub.add_parser("budget")
    p.add_argument("--roots", nargs="+", required=True)
    p.add_argument("--stage", type=float, default=None, help="이번 단계 예상 크레딧 → 게이트·credit_cap")
    p = sub.add_parser("ab-setup")
    p.add_argument("--from", dest="src", required=True)
    p.add_argument("--draft-tag", required=True)
    p.add_argument("--replace", action="store_true")
    p = sub.add_parser("ab-sheet")
    p.add_argument("--roots", nargs="+", required=True)
    p.add_argument("--out", default=None)
    p = sub.add_parser("ab-tally")
    p.add_argument("--scores", default=str(EVAL_DIR / "ab" / "scores.csv"))
    p.add_argument("--key", default=None)
    a = ap.parse_args(argv)
    doc = load_cases(a.cases)

    if a.cmd == "setup":
        for kind, root in (("named", a.named), ("legacy", a.legacy)):
            print(f"── {kind}: {root}")
            for r in setup_project(root, doc, kind):
                print(f"  {r['case']:>3} {r['crop_id']:<12} {r['status']}  {' '.join(r.get('targets') or [])}")
        return 0
    if a.cmd == "validate":
        rows = validate(doc)
        _print_rows(rows)
        bad = [r["id"] for r in rows if not r["pass"]]
        print(f"정답 스펙 {len(rows)} · 기준 S ≥ {PASS_S:g} · " + (f"미달 {', '.join(bad)} → 채점기 확인" if bad else "모두 통과"))
        sc = selfcheck()
        for line in sc:
            print(f"  대조 예문 틀림: {line}")
        print(f"대조 예문 {len(_SELFCHECK_COUNTS) + len(_SELFCHECK_WORDS)} · " + (f"틀림 {len(sc)}" if sc else "모두 기대대로"))
        return 1 if bad or sc else 0
    if a.cmd == "score":
        res = rescore(a.root, doc, parse_select(a.select))
        pj, pm = write_scores(res, a.tag, a.out or EVAL_DIR / "scores")
        _print_rows(res["rows"])
        print(f"{res['summary']} → {pj}, {pm}")
        return 0
    if a.cmd == "compare":
        d = Path(a.dir or EVAL_DIR / "scores")
        ra = json.loads((d / f"{a.a}.json").read_text(encoding="utf-8"))
        rb = json.loads((d / f"{a.b}.json").read_text(encoding="utf-8"))
        c = compare(ra, rb)
        print(f"| 사례 | {a.a} | {a.b} | 차 |\n|---|---|---|---|")
        for i, sa, sb, df in c["rows"]:
            print(f"| {i} | {sa:.1f} | {sb:.1f} | {df:+.1f} |")
        print(f"평균 차 {c['mean_diff']} · SE {c['se']} · 최대 |차| {c['max_abs_diff']} · 치명 {c['fatal_a']} → {c['fatal_b']}")
        return 0
    if a.cmd == "determinism":
        rounds = tuple(x.strip() for x in a.rounds.split(","))
        if len(rounds) != 2:
            raise ValueError(f"--rounds 는 두 값(예: 0,1): {a.rounds!r}")
        d = determinism(a.root, doc, parse_select(a.select), rounds)
        print(f"| 사례 | 크롭 | round {rounds[0]} 키 | round {rounds[1]} 키 | 결과 | 다른 필드 |\n|---|---|---|---|---|---|")
        for r in d["rows"]:
            ka, kb = ((k or "-")[:8] for k in r["keys"])
            res = {"same": "같음", "diff": f"다름 {len(r['diffs'])}", "missing": "짝 없음"}[r["status"]]
            if "stale" in r["inputs"]:
                res += " (원장 req 본문 없음 — 지금 문맥 기준)"
            fields = "; ".join(f"{p}: {str(va)[:40]!r} → {str(vb)[:40]!r}" for p, va, vb in r["diffs"][:4])
            print(f"| {r['id']} | {r['crop_id']} | {ka} | {kb} | {res} | {fields} |")
        s = d["summary"]
        print(f"결정성 round {rounds[0]} ↔ {rounds[1]}: 같음 {s['same']} · 다름 {s['diff']} · 짝 없음 {s['missing']}")
        return 1 if s["diff"] else 0
    if a.cmd == "budget":
        b = budget(a.roots, a.stage)
        for r in b["rows"]:
            est = f" + 추정 {r['vlm_est']} (보고 없는 {r['vlm_est_attempts']} 시도, req est 보통값)" if r["vlm_est_attempts"] else ""
            print(f"  {r['root']}: VLM {r['vlm']} ({r['vlm_attempts']} 시도){est} + GPT Image {r['calls']} ({r['n_calls']} 호출)")
        print(f"누적 {b['total']}" + (f" (그중 추정 {b['est']})" if b["est"] else "")
              + f" · 남음 {b['remaining']} / {BUDGET_TOTAL}")
        if "gate_ok" in b:
            print(f"단계 예상 {b['stage_est']:g}: 게이트(누적 + 1.5×예상 ≤ {GATE_LIMIT}) {'통과' if b['gate_ok'] else '초과 — 크롭 수를 줄이거나 중단'}"
                  f" · credit_cap {b['credit_cap']}")
        return 0
    if a.cmd == "ab-setup":
        r = ab_setup(a.src, doc, a.draft_tag, replace=a.replace)
        for g in AB_GROUPS:
            print(f"  {g}: {r[g]}")
        for row in r["rows"]:
            print(f"    {row['group']:<7} {row['crop_id']:<12} {row['status']}")
        return 0
    if a.cmd == "ab-sheet":
        r = ab_sheet(a.roots, doc, a.out)
        print(f"크롭 {r['crops']} · 후보 {r['candidates']} · scores.csv 새 줄 {r['new_rows']} · 사라진 후보(라벨 유지) {r['gone']}"
              f" → {r['out']} (sheet_*.jpg, key.json, scores.csv)")
        return 0
    if a.cmd == "ab-tally":
        r = ab_tally(a.scores, doc, a.key)
        for g, v in sorted(r["groups"].items()):
            print(f"  {g:<8} Borda {v['borda']} · 후보 {v['n']} · 쓸 수 없음 {v['unusable']} · outside_mad {v['mad']} · ΔE76(정답 마스크) {v['de76']}")
        for cid, pc in sorted(r["per_crop"].items()):
            print(f"    {cid:<12} Borda {pc['borda']} · 순위 매긴 수 {pc['n']} · 최고 순위 {pc['best']} → {pc.get('win', '-')}"
                  + (" (군별 후보 수가 달라 Borda 합이 기울 수 있음)" if pc.get("uneven") else ""))
        wb = r["wins_best"]
        print(f"크롭별 승(Borda): draft {r['wins']['draft']} · minimal {r['wins']['minimal']} · 동률 {r['wins']['tie']} "
              f"(보조 — 최고 순위: draft {wb['draft']} · minimal {wb['minimal']} · 동률 {wb['tie']}) → "
              f"D5 {'초안 기본 사용' if r['d5_default_use'] else '참고용(원인 조사)'}")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
