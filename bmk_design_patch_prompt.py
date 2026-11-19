"""BMK Design Patch Prompt — 크롭 1개의 GPT 이미지 편집 프롬프트를 스펙에서 결정적으로 렌더(+변형)하고, 전송 전 이미지를 평탄화하는 순수 모듈.

배경
----
Design Patch(가제 Multi Layer Crop Edit)는 일러스트의 크롭마다 GPT 편집 후보를 만들어 PSD 로 되돌린다.
이 모듈은 그중 "무엇을 보낼지"를 담당한다. ComfyUI 에 의존하지 않는다(numpy / PIL / scipy + 같은 패키지 store).

- 렌더러: 시제품 `_proto\\work_prompts\\gpt_edit_prompt.py` 의 문장과 코드를 **그대로** 이식했다.
  같은 스펙 → 바이트 단위로 같은 프롬프트(난수 없음, 순서 고정, 공유 레퍼런스 중복 제거).
  템플릿 문장을 바꾸면 TEMPLATE_VERSION 을 올릴 것(cell_key 에 들어가 "템플릿 변경" 신규 사유가 된다).
- 평탄화: 크롭의 투명 여백 아래 RGB 는 (0,0,0) 이고, 알파를 버리고 보내면 GPT 가 그 자리를 검정 띠로 그린다
  (실측: 007 오른쪽 18.4%, 001 8.3%). 레퍼런스 컷아웃의 투명 아래 RGB 도 제각각(회색 잔상, 흰색 등)이다.
  → 크롭은 edge-replicate(가장 가까운 불투명 픽셀 색), 레퍼런스는 단색 매트(#808080) 위 합성으로 평탄화해 보낸다.

공개 API
--------
- TEMPLATE_VERSION = "dp-prompt/1"
- render(spec) -> {"prompt": str, "images": [path, ...], "legend": [str, ...]}
    images = 업로드 순서(Image 1 = canvas → guide → before_paste → 타깃 순서 refs, 중복 제거). prompt 의 @imageN 과 1:1.
- variants(spec) -> {name: variant}
    이름: V1_standard / V2_isolate_<id>(타깃 2개 이상) / V3_freer_shape(ref_correct·fix 타깃이 있을 때) / V4_checklist.
    variant = 변형 스펙 dict + render 결과 키(prompt / images / legend). 스펙으로 써도(render(v) 는 같은 결과 —
    렌더러는 이 세 키를 읽지 않음) 렌더 결과로 써도(v["prompt"]) 시제품 variants() 와 같은 문자열이 나온다.
- variant_specs(spec) -> {name: spec}            렌더 결과 키가 없는 순수 변형 스펙.
- select_variants(all_variants, wanted) -> dict  "V1,V4" 로 고름(V2 → V2_isolate_* 전부, 없는 변형은 건너뜀).
- validate_spec(spec) -> list[str]               렌더 전 점검(빈 목록 = 문제 없음). render 는 시제품처럼 검사하지 않는다.
- flatten_canvas(rgba) -> (rgb HxWx3 uint8, info)
- flatten_ref(rgba, matte=(128,128,128)) -> rgb HxWx3 uint8
- spec_from_manifest(crop, target, refs_dir, glossary, overrides) -> spec   매니페스트 crop/target 항목 → 최소 스펙.
- auto_pick_ref(crop_name, refs_auto) -> str      refs_auto 중 Prepare 가 고르는 1장(아래 refs 규칙 — Studio S2 refs.seed_link 도 이것).
- UX v2 본문 경로(파일 끝 절): v2_of / v2_update / v2_roles / roles_problem / v2_input_hash / v2_state / render_body / body_problem / text_problem,
  BODY_TEMPLATE = "dp-body/1" · BODY_VARIANT = "B1_body". 스펙의 "_v2" 가 없으면 아무것도 바뀌지 않는다.

스펙 형식 (JSON 직렬화 가능 dict — 시제품 docstring 과 같음)
------------------------------------------------------------
{
  "crop_id": "002_toast_right",
  "canvas": "path.png",                 # Image 1 (paste_harmonize 이면 붙여넣은 합성본)
  "before_paste": "path.png" | null,    # paste_harmonize 타깃이 있을 때만 사용
  "guide": "path.png" | null,           # 가이드 1장을 가이드 타깃 전부가 공유
  "style_note": "..." | "",             # @image1 렌더링을 구체적으로 묘사(선택)
  "preserve": ["...", ...],             # 타깃 근처에서 바뀌면 안 되는 것(이름으로)
  "notes": "",                          # 맨 끝에 붙는 자유 문장
  "ref_style": "at" | "plain" | "both", # 토큰 "@image1" / "Image 1" / 범례에 둘 다
  "targets": [
    {"id": "A", "name": "toast hair clip",
     "mode": "ref_correct" | "self_restore" | "sketch_guide" | "paste_harmonize",
     "action": "fix" | "add" | "relocate" | "replace",          # 기본 fix (sketch_guide 는 add)
     "refs": ["path.png", ...], "ref_object": "the toast-shaped clip", "ref_ignore": "the white hair ...",
     "detail": "...", "must_not": ["..."], "location": "on the ... (가이드 없이 add 일 때)",
     "shape": "locked" | "moderate" | "free",
     "guide_color": "green", "guide_type": "outline" | "blob" | "path" | "area",
     "occluded_by": "..." | "", "defects": "..." (self_restore),
     "text": "none" | "keep" | "plausible" | "exact_ref",
     "ref_view": "same" | "different"}                          # 레퍼런스가 다른 각도/포즈
  ]
}
렌더러는 위에 없는 키(warnings, bbox, prompt 등)를 무시한다.

평탄화 규칙
-----------
- 입력: HxWx4 / HxWx3 의 uint8 또는 float(0..1) 배열, 또는 PIL.Image. 출력은 항상 새 HxWx3 uint8.
- flatten_canvas: 알파<255 픽셀을 "가장 가까운 완전 불투명(255) 픽셀의 색"(scipy distance_transform_edt 의
  최근접 인덱스) 위에 자기 알파로 합성한다. 알파 0 → 최근접 불투명 색 그대로(edge-replicate),
  부분 알파 → 둘의 알파 혼합, 불투명 → 비트 그대로. 투명 영역이 한 변에 붙은 띠면 np.pad(mode="edge") 와 비트 동일.
  완전 불투명 픽셀이 하나도 없으면 ValueError. 복원 때는 info["filled_bbox"] 영역을 버리면 된다(여백은 정보가 아님).
- flatten_ref: out = (rgb·a + matte·(255−a) + 127) // 255 (정수 연산). 알파 0 → 정확히 매트색.
  PIL alpha_composite 와 ±1 이내(00.크롭레퍼런스 _t 컷아웃 16개는 차 0).

spec_from_manifest 규칙 (최소 스펙)
-----------------------------------
- target: 매니페스트 타깃 dict 하나, 또는 None(크롭의 모든 타깃 → 다중 타깃 스펙).
- 이름: overrides 타깃 name → target.name_en → store.translate(label, glossary) → label 그대로(+경고).
- refs: overrides 타깃 refs → target.refs → crop.refs_auto 에서 1개 선택(+경고, self_restore 면 안 함):
  crop.refs_main(Studio S2 가 고정한 대표)이 refs_auto 안이면 그것, 아니면 자동 선택(auto_pick_ref) —
  크롭 이름으로 시작 → `_t` 컷아웃 → 크롭 이름과 공통 접두 길이 → refs_auto 순서.
  refs_dir 가 있으면 상대 경로를 refs_dir 기준으로 합치고, 없는 파일은 경고.
- mode: overrides → target.mode → "ref_correct". 렌더가 깨지는 조합은 고치고 경고:
  sketch_guide 인데 spec guide 없음 → ref_correct, ref_correct·sketch_guide 인데 refs 없음 → self_restore.
- ref_correct·sketch_guide 의 detail 이 비면 DEFAULT_DETAIL 을 넣고 경고(빈 detail 은 "in @image2: ." 로 렌더됨).
- 크롭 공통 키(canvas, guide, before_paste, ref_style, style_note, preserve, notes)는 overrides 가 이긴다.
  canvas 기본값 = crop.source(프로젝트 루트 기준 상대 경로, 평탄화 전 원본) — 평탄화본으로 바꾸는 것은 호출자 몫.
- overrides 는 스펙과 같은 모양(specs/<crop_id>.json). overrides["targets"] 의 같은 id 항목을 키 단위로 덮어쓴다.
  모르는 키는 무시하고 경고("_" 로 시작하는 주석 키는 조용히 무시).
- 경고는 spec["warnings"](list[str], 한국어)에만 담는다(노드 report 가 보여줌).

버전 이력
---------
v1 (2026-10, M1) — 시제품 gpt_edit_prompt.py(2026-10-06) 문장 그대로 이식(TEMPLATE_VERSION "dp-prompt/1").
  rendered/*.spec.json 5건(toast, pen, fingers, toast_paste, self_receipt)의 .txt 와 바이트 일치 확인.
  flatten_canvas / flatten_ref / spec_from_manifest / validate_spec / select_variants 추가.
Studio S2 (2026-10, TEMPLATE_VERSION 그대로) — _auto_pick_ref → 공개 이름 auto_pick_ref(refs.seed_link 가 같은 규칙),
  refs 선택에 crop.refs_main. 렌더러·변형·평탄화는 바꾸지 않음(고정 때 refs_main = 지금 자동 선택이라 cell_key 그대로).
UX v2-A (2026-11, TEMPLATE_VERSION 그대로) — 스펙 "_v2"(목적·한국어 지시·AI 자동·쓸 글)와 본문 경로(render_body — 틀은 render 의 것,
  Target…Integration 만 "Request:" + 본문, template 칸 "dp-body/1"). 렌더러·변형·평탄화는 바꾸지 않음(_v2 없는 스펙은 바이트 그대로).
"""

from __future__ import annotations

import copy
import logging
import os
import re

import numpy as np
from PIL import Image
from scipy import ndimage

try:
    from . import bmk_design_patch_store as store
except ImportError:  # 단독 실행/테스트
    import bmk_design_patch_store as store

logger = logging.getLogger(__name__)

_TAG = "[ComfyUI_BMK_Nodes::DesignPatch]"

TEMPLATE_VERSION = "dp-prompt/1"
PROMPT_HEAD = "Input images, in upload order:"  # render() 가 내는 프롬프트의 첫 줄(시험이 같은지 본다) — 가져오기 창이 스튜디오가 만든 조각을 가릴 때 쓴다

MODES = ("ref_correct", "self_restore", "sketch_guide", "paste_harmonize")
ACTIONS = ("fix", "add", "relocate", "replace")
REF_STYLES = ("at", "plain", "both")
REF_VIEWS = ("same", "different")
DEFAULT_MATTE = (128, 128, 128)
# spec_from_manifest 가 detail 이 빈 ref_correct / sketch_guide 타깃에 넣는 기본 설명(스펙 데이터, 템플릿 문장 아님).
DEFAULT_DETAIL = "its shape, colors, materials, and surface details"

_CROP_KEYS = ("canvas", "guide", "before_paste", "ref_style", "style_note", "preserve", "notes")
_TARGET_KEYS = ("action", "ref_object", "ref_ignore", "detail", "must_not", "location", "shape", "guide_color",
                "guide_type", "occluded_by", "defects", "text", "ref_view", "bbox")


# ════════════════════════════════════════════════════════════════
# 렌더러 — 시제품 gpt_edit_prompt.py 에서 그대로 이식 (GUIDE_NOUN ~ _target_line 수정 금지, 테스트가 AST 대조)
# ════════════════════════════════════════════════════════════════
GUIDE_NOUN = {"outline": "outline", "blob": "solid shape", "path": "line", "area": "hatched area"}
GUIDE_RULE = {
    "outline": "Follow the outline closely for its silhouette and extent.",
    "blob": "The solid shape marks only its position and approximate size; take the exact shape from the reference.",
    "path": "The line is the centerline it follows; draw it along this path at a natural width.",
    "area": "The hatched area marks the region it should cover.",
}
SHAPE = {
    "locked": " Keep its current silhouette and volume; change only its surface design and details.",
    "moderate": " Change its outline only where the reference design requires it.",
    "free": " Its shape and proportions may change as much as the reference design requires, adapted to the pose,"
            " perspective, and attachment point in {c}.",
}
TEXT = {
    "none": "",
    "keep": " Keep any writing at its current legibility; do not invent words or numbers.",
    "plausible": " Where its writing is illegible, render short, plausible text that suits the object.",
    "exact_ref": " Copy its lettering glyph by glyph from {r} exactly as drawn; do not substitute real or different characters.",
}


def _join(items):
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f", and {items[-1]}"


def _plan_images(spec):
    """Upload order: canvas, guide, before-paste, then refs in target order (deduplicated)."""
    order, roles = [], {}

    def add(path, role):
        if path not in roles:
            order.append(path)
            roles[path] = []
        roles[path].append(role)

    add(spec["canvas"], ("canvas", None))
    if spec.get("guide") and any(t["mode"] == "sketch_guide" or t.get("guide_color") for t in spec["targets"]):
        add(spec["guide"], ("guide", None))
    if spec.get("before_paste") and any(t["mode"] == "paste_harmonize" for t in spec["targets"]):
        add(spec["before_paste"], ("before", None))
    for t in spec["targets"]:
        for r in t.get("refs", []):
            add(r, ("ref", t["id"]))
    return order, roles


def render(spec: dict) -> dict:
    style = spec.get("ref_style", "at")
    order, roles = _plan_images(spec)
    idx = {p: i + 1 for i, p in enumerate(order)}

    def tok(path):
        i = idx[path]
        return f"Image {i}" if style == "plain" else f"@image{i}"

    c = tok(spec["canvas"])
    g = tok(spec["guide"]) if spec.get("guide") in idx else None
    b = tok(spec["before_paste"]) if spec.get("before_paste") in idx else None
    targets = spec["targets"]
    multi = len(targets) > 1
    pasted = any(t["mode"] == "paste_harmonize" for t in targets)

    # Legend
    legend = []
    for p in order:
        i = idx[p]
        head = f"Image {i} ({tok(p)})" if style == "both" else tok(p)
        kinds = [r[0] for r in roles[p]]
        if "canvas" in kinds:
            what = ("the canvas: a crop of a finished illustration into which reference cutouts were roughly pasted."
                    if pasted else "the canvas: a crop of a finished illustration.") + " This is the image to edit."
        elif "guide" in kinds:
            what = f"placement guide: a copy of {c} with colored annotation marks. Instructions only."
        elif "before" in kinds:
            what = f"the same crop before the cutouts were pasted. Use it only to restore surroundings."
        else:
            owners = [r[1] for r in roles[p] if r[0] == "ref"]
            objs = []
            for t in targets:
                if t["id"] in owners and t.get("ref_object"):
                    objs.append(t["ref_object"])
            ign = [t["ref_ignore"] for t in targets if t["id"] in owners and t.get("ref_ignore")]
            label = ("Targets " if len(owners) > 1 else "Target ") + _join(owners) if multi else "the target"
            what = f"design reference for {label}" + (f" ({_join(dict.fromkeys(objs))})" if objs else "") + "."
            if ign:
                what += f" Ignore {ign[0]}."
        legend.append(f"- {head}: {what}")
    lines = ["Input images, in upload order:", *legend, ""]

    # Canvas
    canvas = (f"Canvas: Edit {c} and output only its finished edit. Keep {c}'s framing, composition, and aspect ratio"
              f" exactly, so every unchanged area stays aligned with it.")
    if len(order) > 1:
        canvas += " Use the other images only as described here; never take their framing, backgrounds, or unrelated content."
    lines.append(canvas)

    # Guide
    guided = [t for t in targets if t["mode"] == "sketch_guide" or (t["mode"] == "paste_harmonize" and t.get("guide_color") and g)]
    if guided and g:
        marks = _join(f"{t['guide_color']} {GUIDE_NOUN[t.get('guide_type', 'outline')]}" + (f" = Target {t['id']}" if multi else "")
                      for t in guided)
        lines.append(f"Guide: {g} is {c} with annotation marks ({marks}). The marks only show where things go and how"
                     f" much area they cover; they are not artwork. The output must contain no trace of them.")

    # Targets
    lines.append("Targets (change only these):" if multi else "Target:")
    for t in targets:
        lines.append(_target_line(t, c, g, b, tok, multi))

    # Integration
    style_note = spec.get("style_note") or "its line weight and line color, shading style, texture, and level of detail"
    if any(t["mode"] != "self_restore" for t in targets):
        lines.append(f"Integration: Paint {'each target' if multi else 'the target'} in {c}'s own rendering—{style_note}—so it looks"
                     f" painted together with the surrounding illustration. Take base colors and materials from the"
                     f" references, but show them under {c}'s lighting and color grading. Do not copy the references'"
                     f" line art, flat lighting, or backgrounds.")
    else:
        lines.append(f"Integration: Match {c}'s colors, materials, lighting, line quality, texture, and level of detail."
                     f" Keep intentional brushwork, softness, and effects; avoid blanket smoothing, sharpening, or added detail.")

    # Preserve
    keep = spec.get("preserve") or []
    keep_txt = f", especially {_join(keep)}" if keep else ""
    lines.append(f"Preserve: Leave everything outside the {'targets' if multi else 'target'} as it is in {c}{keep_txt}. Do not"
                 f" re-line, sharpen, smooth, recolor, or restyle untouched areas; keep the overall brightness, saturation,"
                 f" and color temperature; add no other objects. Around {'each target' if multi else 'the target'}, adjust only"
                 f" the edges, overlaps, and contact shadows needed to blend it in.")
    if spec.get("notes"):
        lines.append(f"Notes: {spec['notes']}")
    return {"prompt": "\n".join(lines), "images": order, "legend": legend}


def _target_line(t, c, g, b, tok, multi):
    name = t["name"]
    refs = [tok(r) for r in t.get("refs", [])]
    r = _join(refs) if refs else ""
    obj = t.get("ref_object") or f"the {name}"
    head = f"- Target {t['id']} — {name}" if multi else f"- {name[0].upper() + name[1:]}"
    detail = t.get("detail", "").rstrip(".")
    must_not = t.get("must_not") or []
    mode, action = t["mode"], t.get("action")
    text = TEXT[t.get("text", "none")].format(r=r or c)
    if t.get("ref_view") == "different" and mode in ("ref_correct", "sketch_guide"):
        text += f" {r} shows it from another angle; draw it at the angle and perspective it needs in {c}."

    if mode == "ref_correct":
        action = action or "fix"
        if action == "add":
            body = (f"Add the {name} {t.get('location', 'where it belongs')}, with the design of {obj} in {r}: {detail}."
                    f" Adapt it to {c}'s pose, perspective, surface curvature, and occlusion, at a scale consistent with"
                    f" the surrounding forms.")
        else:
            body = f"Correct the {name} in {c} to match {obj} in {r}: {detail}."
            if must_not:
                body += f" Remove {_join(must_not)}."
            body += SHAPE[t.get("shape", "moderate")].format(c=c) + " Keep its position, size, and orientation."
        return f"{head} (reference: {r}): {body}{text}"

    if mode == "sketch_guide":
        action = action or "add"
        col, gt = t.get("guide_color", "green"), t.get("guide_type", "outline")
        noun = f"{col} {GUIDE_NOUN[gt]}"
        verb = {"add": f"Add the {name} where {g} marks it with the {noun}.",
                "relocate": f"Move the {name}: remove it from its current place, restoring what lies behind it, and draw it"
                            f" where {g} marks it with the {noun}.",
                "replace": f"Replace what is inside the {noun} in {g} with the {name}.",
                "fix": f"Redraw the {name} inside the {noun} in {g}."}[action]
        body = (f"{verb} {GUIDE_RULE[gt]} Take its design from {obj} in {r}, adapted to the perspective, curvature,"
                f" and lighting at that spot: {detail}.")
        if must_not:
            body += f" Remove {_join(must_not)}."
        if t.get("occluded_by"):
            body += f" Keep it behind {t['occluded_by']}."
        return f"{head} (reference: {r}; guide: {noun} in {g}): {body}{text}"

    if mode == "paste_harmonize":
        where = f" (outlined in {t['guide_color']} in {g})" if g and t.get("guide_color") else ""
        body = (f"A cutout of the {name} has been roughly pasted into {c}{where}. Its design, position, scale, and"
                f" orientation are already correct—keep them. Re-render it so it belongs in the painting: clean up cutout"
                f" edges, halos, and leftover outlines; match {c}'s line weight, shading, texture, and sharpness;")
        if r:
            body += f" restore crisp detail from {r} where the paste is blurry;"
        occ = f" and let {t['occluded_by']} overlap it as before" if t.get("occluded_by") else ""
        body += f" add contact shadows{occ}; and remove any remains of the previous {name} around it."
        if b:
            body += f" Use {b} only to restore the surroundings where the paste left gaps or seams."
        if detail:
            body += f" Its design: {detail}."
        return f"{head} (reference: {r or 'none'}): {body}{text}"

    if mode == "self_restore":
        defects = f": {t['defects'].rstrip('.')}" if t.get("defects") else ""
        body = (f"Repair the {name} in {c}{defects}. Infer the intended form from intact parts of {c}—repeated shapes,"
                f" symmetry, materials, and what each part holds, connects to, or overlaps—and rebuild the simplest"
                f" coherent structure. Keep its silhouette, size, position, orientation, and design; change only what is"
                f" broken; where the form stays uncertain, keep it simple instead of inventing new detail.")
        return f"{head} (no reference): {body}{text}"
    raise ValueError(mode)


# ════════════════════════════════════════════════════════════════
# best-of-N 변형 (규칙은 시제품 variants() 와 동일)
# ════════════════════════════════════════════════════════════════
def variant_specs(spec: dict) -> dict:
    """V1 그대로, V2 타깃마다 단독, V3 형태 자유도 한 단계 위, V4 detail 체크리스트. {이름: 스펙(깊은 복사)}."""
    out = {"V1_standard": copy.deepcopy(spec)}
    if len(spec["targets"]) > 1:
        for t in spec["targets"]:
            s = copy.deepcopy(spec)
            s["targets"] = [copy.deepcopy(t)]
            if t["mode"] not in ("sketch_guide", "paste_harmonize") and not t.get("guide_color"):
                s["guide"] = None
            out[f"V2_isolate_{t['id']}"] = s
    s = copy.deepcopy(spec)
    bump = {"locked": "moderate", "moderate": "free", "free": "free"}
    changed = False
    for t in s["targets"]:
        if t["mode"] == "ref_correct" and t.get("action", "fix") == "fix":
            t["shape"] = bump[t.get("shape", "moderate")]
            changed = True
    if changed:
        out["V3_freer_shape"] = s
    s = copy.deepcopy(spec)
    for t in s["targets"]:
        parts = [p.strip() for p in t.get("detail", "").split(";") if p.strip()]
        if len(parts) > 1:
            t["detail"] = "all of these features — " + "; ".join(f"({i}) {q}" for i, q in enumerate(parts, 1))
    out["V4_checklist"] = s
    return out


def variants(spec: dict) -> dict:
    """{이름: 변형 스펙 + render 결과 키(prompt / images / legend)} — 모듈 docstring 참조."""
    out = {}
    for name, s in variant_specs(spec).items():
        out[name] = {**s, **render(s)}
    return out


def select_variants(all_variants: dict, wanted) -> dict:
    """wanted("V1,V4" 또는 목록)에 맞는 변형만 원래 순서로. 토큰 = 정확한 이름 또는 V<n>(그 접두 전부)."""
    tokens = re.split(r"[\s,;]+", wanted.strip()) if isinstance(wanted, str) else [str(w).strip() for w in wanted]
    tokens = [tk if "_" in tk else tk.upper() for tk in tokens if tk]
    for tk in tokens:
        if not re.fullmatch(r"V\d+(_\w+)?", tk):
            raise ValueError(f"변형 이름 형식이 아닙니다: {tk!r} (예: V1, V4, V2_isolate_A)")
    return {name: v for name, v in all_variants.items()
            if any(name == tk or name.startswith(tk + "_") for tk in tokens)}


def validate_spec(spec: dict) -> list:
    """렌더 전에 스펙을 점검해 문제 목록(한국어)을 돌려준다. 빈 목록 = 문제 없음."""
    if not isinstance(spec, dict):
        return ["스펙이 dict 가 아닙니다"]
    probs = []
    if not spec.get("canvas"):
        probs.append("canvas 가 없습니다")
    if spec.get("ref_style", "at") not in REF_STYLES:
        probs.append(f"ref_style {spec.get('ref_style')!r} 은 {REF_STYLES} 중 하나여야 합니다")
    targets = spec.get("targets")
    if not isinstance(targets, list) or not targets:
        return probs + ["targets 가 비어 있습니다"]
    ids = [t.get("id") for t in targets]
    if len(set(ids)) != len(ids):
        probs.append(f"타깃 id 가 중복됩니다: {ids}")
    for t in targets:
        tid = t.get("id") or "?"
        if not t.get("id"):
            probs.append("id 없는 타깃이 있습니다")
        if not str(t.get("name") or "").strip():
            probs.append(f"{tid}: name(영문 이름)이 비어 있습니다")
        mode = t.get("mode")
        if mode not in MODES:
            probs.append(f"{tid}: mode {mode!r} 은 {MODES} 중 하나여야 합니다")
            continue
        for key, allowed, default in (("action", ACTIONS, "fix"), ("shape", tuple(SHAPE), "moderate"),
                                      ("text", tuple(TEXT), "none"), ("guide_type", tuple(GUIDE_NOUN), "outline"),
                                      ("ref_view", REF_VIEWS, "same")):
            if t.get(key, default) not in allowed:
                probs.append(f"{tid}: {key} {t.get(key)!r} 은 {allowed} 중 하나여야 합니다")
        if mode in ("ref_correct", "sketch_guide"):
            if not t.get("refs"):
                probs.append(f"{tid}: {mode} 에는 refs 가 필요합니다(없으면 self_restore)")
            if not str(t.get("detail") or "").strip():
                probs.append(f"{tid}: detail 이 비어 있어 '...: .' 로 렌더됩니다")
        if mode == "sketch_guide" and not spec.get("guide"):
            probs.append(f"{tid}: sketch_guide 에는 spec guide 이미지가 필요합니다")
    return probs


# ════════════════════════════════════════════════════════════════
# 평탄화 (전송 전 알파 제거)
# ════════════════════════════════════════════════════════════════
def _to_uint8_hwc(img) -> np.ndarray:
    """PIL / ndarray → HxWx(3|4) uint8 (입력은 바꾸지 않음)."""
    if isinstance(img, Image.Image):
        has_alpha = "A" in img.getbands() or (img.mode == "P" and "transparency" in img.info)
        return np.asarray(img.convert("RGBA" if has_alpha else "RGB"))
    arr = np.asarray(img)
    if arr.ndim != 3 or arr.shape[2] not in (3, 4) or arr.shape[0] == 0 or arr.shape[1] == 0:
        raise ValueError(f"이미지 배열 모양이 HxWx3 / HxWx4 가 아닙니다: {arr.shape}")
    if np.issubdtype(arr.dtype, np.floating):
        return np.clip(np.rint(arr.astype(np.float64) * 255.0), 0, 255).astype(np.uint8)
    if arr.dtype != np.uint8:
        raise ValueError(f"지원하지 않는 dtype 입니다: {arr.dtype} (uint8 또는 float 0..1)")
    return arr


def flatten_canvas(rgba) -> tuple:
    """크롭 평탄화: 알파<255 픽셀을 가장 가까운 완전 불투명 픽셀 색 위에 합성(edge-replicate, 거리 변환).

    반환 (rgb HxWx3 uint8, info). info = {"method": "none"|"edge_replicate", "alpha_frac": 알파<255 비율,
    "transparent_frac": 알파 0 비율, "filled_bbox": [x0,y0,x1,y1](끝 배타) | None, "max_fill_dist": px}."""
    arr = _to_uint8_hwc(rgba)
    info = {"method": "none", "alpha_frac": 0.0, "transparent_frac": 0.0, "filled_bbox": None, "max_fill_dist": 0.0}
    if arr.shape[2] == 3:
        return arr.copy(), info
    rgb, a = arr[..., :3], arr[..., 3]
    trans = a < 255
    if not trans.any():
        return rgb.copy(), info
    if trans.all():
        raise ValueError("완전 불투명 픽셀이 없어 edge-replicate 평탄화를 할 수 없습니다(크롭이 캔버스 밖이거나 반투명 레이어)")
    ys, xs = np.nonzero(trans)
    iy, ix = ndimage.distance_transform_edt(trans, return_distances=False, return_indices=True)
    sy, sx = iy[ys, xs], ix[ys, xs]
    at = a[ys, xs].astype(np.uint32)[:, None]
    out = rgb.copy()
    out[ys, xs] = (rgb[ys, xs].astype(np.uint32) * at + rgb[sy, sx].astype(np.uint32) * (255 - at) + 127) // 255
    d2 = (sy - ys).astype(np.int64) ** 2 + (sx - xs).astype(np.int64) ** 2
    info.update(method="edge_replicate", alpha_frac=len(ys) / a.size, transparent_frac=float(np.mean(a == 0)),
                filled_bbox=[int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1],
                max_fill_dist=float(np.sqrt(d2.max())))
    return out, info


def flatten_ref(rgba, matte=DEFAULT_MATTE) -> np.ndarray:
    """레퍼런스 컷아웃 평탄화: 단색 매트 위 알파 합성. matte = (r, g, b) 또는 "#rrggbb". 반환 HxWx3 uint8."""
    if isinstance(matte, str) and re.fullmatch(r"#?[0-9a-fA-F]{6}", matte.strip()):
        hx = matte.strip().lstrip("#")
        matte = tuple(int(hx[i:i + 2], 16) for i in (0, 2, 4))
    if not (isinstance(matte, (tuple, list)) and len(matte) == 3
            and all(isinstance(v, (int, np.integer)) and 0 <= v <= 255 for v in matte)):
        raise ValueError(f"매트 색은 (r, g, b) 0~255 또는 '#rrggbb' 여야 합니다: {matte!r}")
    arr = _to_uint8_hwc(rgba)
    if arr.shape[2] == 3:
        return arr.copy()
    m = np.array([int(v) for v in matte], dtype=np.uint32)
    a32 = arr[..., 3].astype(np.uint32)[..., None]
    out = (arr[..., :3].astype(np.uint32) * a32 + m * (255 - a32) + 127) // 255
    return out.astype(np.uint8)


# ════════════════════════════════════════════════════════════════
# 매니페스트 → 최소 스펙
# ════════════════════════════════════════════════════════════════
def auto_pick_ref(crop_name: str, refs_auto: list) -> str:
    """refs_auto 중 하나: 크롭 이름으로 시작 → `_t` 컷아웃 → 크롭 이름과 공통 접두 길이 → refs_auto 순서."""
    def key(item):
        i, r = item
        stem = os.path.splitext(os.path.basename(r))[0]
        lcp = len(os.path.commonprefix([stem, crop_name]))
        return (stem.startswith(crop_name), store.is_ref_cutout(os.path.basename(r)), lcp, -i)

    return max(enumerate(refs_auto), key=key)[1]


def spec_from_manifest(crop: dict, target, refs_dir: str, glossary, overrides) -> dict:
    """매니페스트 crop 항목 + 타깃(dict, None=전부)에서 렌더 가능한 최소 스펙을 만든다(규칙은 모듈 docstring).

    refs_dir: 레퍼런스 폴더("" 이면 경로 그대로). glossary: store.load_glossary 결과(없으면 {} 또는 None).
    overrides: specs/<crop_id>.json 내용(dict) 또는 None. 경고는 반환 스펙의 "warnings"."""
    if not isinstance(crop, dict):
        raise ValueError("crop 은 매니페스트 crops[] 항목(dict)이어야 합니다")
    if overrides is not None and not isinstance(overrides, dict):
        raise ValueError("overrides 는 dict 여야 합니다")
    ov = overrides or {}
    crop_name = str(crop.get("name") or "")
    crop_id = str(crop.get("id") or crop_name)
    chosen = (crop.get("targets") or []) if target is None else [target]
    if not chosen or not all(isinstance(t, dict) for t in chosen):
        raise ValueError(f"크롭 {crop_name!r}: 타깃이 없습니다(target 은 매니페스트 타깃 dict 또는 None)")
    warnings = []

    spec = {"crop_id": crop_id, "canvas": crop.get("source"), "guide": None, "before_paste": None,
            "ref_style": "at", "style_note": "", "preserve": [], "notes": "", "targets": []}
    for k in _CROP_KEYS:
        if k in ov:
            spec[k] = copy.deepcopy(ov[k])
    unknown = sorted(k for k in ov if k not in _CROP_KEYS + ("targets", "crop_id") and not str(k).startswith("_"))
    if unknown:
        warnings.append(f"overrides 의 모르는 키를 무시했습니다: {unknown}")
    if not spec["canvas"]:
        raise ValueError(f"크롭 {crop_name!r}: canvas 경로가 없습니다(crop.source 또는 overrides.canvas)")
    if spec["ref_style"] not in REF_STYLES:
        raise ValueError(f"ref_style {spec['ref_style']!r} 은 {REF_STYLES} 중 하나여야 합니다")

    ov_t = {str(t.get("id")): t for t in ov.get("targets") or [] if isinstance(t, dict)}
    refs_auto = [str(r) for r in crop.get("refs_auto") or [] if r]
    for t in chosen:
        tid = str(t.get("tid") or t.get("id") or "A")
        o = ov_t.get(tid, {})

        name = str(o.get("name") or t.get("name_en") or "").strip()
        if not name:
            label = str(t.get("label") or crop.get("part") or crop_name).strip()
            name = (store.translate(label, glossary) or "").strip()
            if not name:
                name = label or tid
                warnings.append(f"{tid}: 영문 이름(name_en)이 없어 라벨 {label!r} 을 그대로 씁니다"
                                f" — 용어집이나 레이어명 '| {tid}: <영문>' 으로 채우세요")

        mode = str(o.get("mode") or t.get("mode") or "ref_correct")
        if mode not in MODES:
            raise ValueError(f"{tid}: mode {mode!r} 은 {MODES} 중 하나여야 합니다")

        if "refs" in o:
            refs = [str(r) for r in o["refs"] or []]
        elif t.get("refs"):
            refs = [str(r) for r in t["refs"]]
        elif refs_auto and mode != "self_restore" and crop.get("refs_main") in refs_auto:
            refs = [crop["refs_main"]]
            warnings.append(f"{tid}: 대표 레퍼런스: {os.path.basename(refs[0])} (Studio 연결, 후보 {len(refs_auto)}개)")
        elif refs_auto and mode != "self_restore":
            refs = [auto_pick_ref(crop_name, refs_auto)]
            warnings.append(f"{tid}: 레퍼런스를 자동 선택했습니다: {os.path.basename(refs[0])} (후보 {len(refs_auto)}개)")
        else:
            refs = []
        if refs_dir:
            refs = [r if os.path.isabs(r) else os.path.join(refs_dir, r) for r in refs]
            warnings += [f"{tid}: 레퍼런스 파일이 없습니다: {r}" for r in refs if not os.path.isfile(r)]

        tt = {"id": tid, "name": name, "mode": mode, "refs": refs}
        for k, v in o.items():
            if k in _TARGET_KEYS:
                tt[k] = copy.deepcopy(v)
            elif k not in ("id", "name", "mode", "refs") and not str(k).startswith("_"):
                warnings.append(f"{tid}: overrides 타깃의 모르는 키를 무시했습니다: {k}")
        if tt["mode"] == "sketch_guide" and not spec["guide"]:
            tt["mode"] = "ref_correct"
            warnings.append(f"{tid}: sketch_guide 인데 guide 이미지가 없어 ref_correct 로 바꿨습니다")
        if tt["mode"] in ("ref_correct", "sketch_guide") and not refs:
            warnings.append(f"{tid}: {tt['mode']} 인데 레퍼런스가 없어 self_restore 로 바꿨습니다")
            tt["mode"] = "self_restore"
        if tt["mode"] in ("ref_correct", "sketch_guide") and not str(tt.get("detail") or "").strip():
            tt["detail"] = DEFAULT_DETAIL
            warnings.append(f"{tid}: detail 이 없어 기본 설명을 넣었습니다 — specs/{crop_id}.json 에 레퍼런스 디자인을 적으세요")
        spec["targets"].append(tt)

    ids = [t["id"] for t in spec["targets"]]
    if len(set(ids)) != len(ids):
        raise ValueError(f"크롭 {crop_name!r}: 타깃 id 가 중복됩니다: {ids}")
    spec["warnings"] = warnings
    return spec


# ════════════════════════════════════════════════════════════════
# UX v2 — 패치별 지시(스펙의 _v2)와 본문 경로 (UX v2 Phase V2-A D495–D509)
# 스펙 오버라이드의 "_v2" 키 하나에 목적·한국어 지시·AI 자동 여부·쓸 글(AI 작성본 또는 직접 입력)을 둔다. "_" 키라
# spec_from_manifest 가 조용히 무시한다 — _v2 가 없거나 쓸 글이 없으면 프롬프트·생성 조건 키는 지금과 바이트가 같다.
# 쓸 글이 있으면 그 크롭은 「본문 경로」로 간다: render() 가 만든 틀(범례·Canvas·Guide·Preserve·Notes)은 그대로 두고
# Target… 줄부터 Integration 줄까지만 "Request:" + 본문으로 바꾼다. 렌더러 함수는 고치지 않는다(결과를 줄 단위로 조립).
# ════════════════════════════════════════════════════════════════
V2_KEY = "_v2"
PURPOSES = ("correct", "add", "remove", "vary", "detail")
BODY_ONLY_PURPOSES = ("remove", "vary", "detail")   # 템플릿에 대응 문장이 없는 목적 — 본문이 있어야 생성한다
PROMPT_SOURCES = ("assist", "manual")
BODY_TEMPLATE = "dp-body/1"      # 본문 경로의 cell_key template 칸(조립 규칙의 판) — 템플릿 경로는 TEMPLATE_VERSION 그대로
BODY_VARIANT = "B1_body"         # 본문 경로의 변형 이름(하나뿐 — V1–V4 는 템플릿의 detail·shape 변형이라 본문에는 없다)
BODY_HEAD = "Request:"
ASSIST_VERSION = "dp-assist/1"   # 입력 해시의 판(재료를 바꾸면 올린다 — 저장된 작성본이 모두 '낡음'이 된다)
INSTRUCTION_MAX = 2000
BODY_MAX = 4000
_TARGET_HEADS = ("Target:", "Targets (change only these):")
_BODY_TOKEN = re.compile(r"@image(\d+)")
_BODY_MENTION = re.compile(r"(?i)@?\s*\bimages?\s*#?\s*\d+")   # 그림 번호로 읽힐 수 있는 모든 표기 — @image1 꼴만 받는다
_BODY_PLAIN_FRAME = re.compile(r"\bImage \d+ \(@image\d+\)|(?<!@)\bImage \d+: ")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_ADDRESS = re.compile(r"(?im)\b[a-z][a-z0-9+.-]*://|\b(?:10\.\d{1,3}|192\.168|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b|[A-Za-z]:[\\/]|\\\\"
                      r"|(?:^|[\s\"'(=\[])(?:\.{1,2}/|~/|/)[A-Za-z0-9_.-]")
# 본문이 틀의 줄(범례·Canvas·Guide·Target·Integration·Preserve·Notes·Request)을 흉내 내면 뒤의 규칙과 섞인다 — 그런 줄 머리는 받지 않는다.
_RESERVED_LINE = re.compile(r"(?im)^[ \t]*(?:input images\b|canvas:|guide:|targets?(?: \([^)\n]*\))?:|integration:|preserve:|notes:|request:)")
_INVISIBLE = re.compile("[\u00ad\u061c\u180e\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u206f\ufeff]")  # 보이지 않는 서식 문자
_FILE_SHA: dict = {}


def text_problem(text, limit: int, what: str, *, empty_ok: bool = True) -> str | None:
    """저장할 글(지시문·본문) 점검 → 문제 문구(없으면 None): 문자열·길이·제어 문자·주소나 경로 꼴."""
    if not isinstance(text, str):
        return f"{what}은 문자열이어야 합니다"
    if not text.strip():
        return None if empty_ok else f"{what}이 비었습니다"
    if len(text) > limit:
        return f"{what}이 너무 깁니다({len(text)}자 — {limit}자까지)"
    if _CONTROL.search(text):
        return f"{what}에 쓸 수 없는 제어 문자가 있습니다"
    if _ADDRESS.search(text):
        return f"{what}에 주소·경로 꼴이 있습니다 — 빼고 적어 주세요"
    return None


def body_problem(body, image_count: int | None = None) -> str | None:
    """본문(틀 안에 들어갈 영문 글) 점검 → 문제 문구(없으면 None). image_count 를 주면 @imageN 이 1…image_count 인지도 본다."""
    problem = text_problem(body, BODY_MAX, "프롬프트 본문", empty_ok=False)
    if problem:
        return problem
    if not re.search(r"[A-Za-z0-9]", body):
        return "프롬프트 본문에 읽을 글자가 없습니다"
    if _INVISIBLE.search(body):
        return "프롬프트 본문에 보이지 않는 서식 문자가 있습니다 — 빼고 적어 주세요"
    reserved = _RESERVED_LINE.search(body)
    if reserved:
        return f"프롬프트 본문의 줄은 「{reserved.group(0).strip()}」로 시작할 수 없습니다(프롬프트의 틀이 쓰는 줄 머리)"
    odd = next((m.group(0).strip() for m in _BODY_MENTION.finditer(body) if not _BODY_TOKEN.fullmatch(m.group(0).strip())), None)
    if odd:
        return f"프롬프트 본문은 그림을 @image1 꼴로만 가리켜야 합니다(「{odd}」 꼴은 쓰지 않음)"
    numbers = [int(n) for n in _BODY_TOKEN.findall(body)]
    if any(n < 1 for n in numbers):
        return "프롬프트 본문의 @image 번호는 1부터입니다"
    if image_count is not None and any(n > image_count for n in numbers):
        return f"프롬프트 본문이 없는 그림(@image{max(numbers)})을 가리킵니다 — 지금 그림은 {image_count}장"
    return None


def v2_of(overrides) -> dict | None:
    """스펙 오버라이드의 _v2 를 읽어 정규화한 사본 → {"purpose", "user_instruction_ko", "ai_auto", "prompt": dict | None} 또는
    {"invalid": 사유}(값이 객체가 아니거나 필드 꼴이 틀림). _v2 키가 없으면 None."""
    if not isinstance(overrides, dict) or V2_KEY not in overrides:
        return None
    raw = overrides[V2_KEY]
    if not isinstance(raw, dict):
        return {"invalid": "스펙의 _v2 가 객체가 아닙니다"}
    purpose = raw.get("purpose", "correct")
    instruction = raw.get("user_instruction_ko", "")
    ai_auto = raw.get("ai_auto", True)
    prompt = raw.get("prompt")
    if purpose not in PURPOSES or not isinstance(instruction, str) or type(ai_auto) is not bool:
        return {"invalid": "스펙의 _v2 값이 잘못되었습니다(목적·지시문·AI 자동)"}
    if prompt is not None:
        if (not isinstance(prompt, dict) or prompt.get("source") not in PROMPT_SOURCES or not isinstance(prompt.get("body_en"), str)
                or not isinstance(prompt.get("input_hash"), str)):
            return {"invalid": "스펙의 _v2.prompt 값이 잘못되었습니다"}
        prompt = copy.deepcopy(prompt)
    return {"purpose": purpose, "user_instruction_ko": instruction, "ai_auto": ai_auto, "prompt": prompt}


def v2_update(overrides, *, purpose=None, user_instruction_ko=None, ai_auto=None, prompt=...) -> dict:
    """오버라이드 사본에 _v2 변경을 적용해 돌려준다(입력은 바꾸지 않음). None 인 인자는 그대로 둔다. prompt: ... = 그대로,
    None = 글 지움, dict = 그 글로. 결과가 기본 상태(목적 correct·지시문 없음·AI 자동 켬·글 없음)면 _v2 키를 지운다 —
    지시를 썼다가 모두 지우면 스펙이 쓰기 전과 같다. _v2 는 _draft 앞에 둔다(_draft 는 늘 맨 끝)."""
    out = copy.deepcopy(overrides) if isinstance(overrides, dict) else {}
    cur = v2_of(out) or {}
    if "invalid" in cur:
        # 일부 필드만 틀린 _v2: 멀쩡한 필드는 살린다(AI 자동만 바꾸는 저장이 목적·지시문을 지우지 않게). 틀린 필드만 기본값으로.
        raw = out.get(V2_KEY) if isinstance(out.get(V2_KEY), dict) else {}
        cur = {}
        if raw.get("purpose") in PURPOSES:
            cur["purpose"] = raw["purpose"]
        if isinstance(raw.get("user_instruction_ko"), str):
            cur["user_instruction_ko"] = raw["user_instruction_ko"]
        if type(raw.get("ai_auto")) is bool:
            cur["ai_auto"] = raw["ai_auto"]
        one = v2_of({V2_KEY: {"prompt": raw.get("prompt")}}) if raw.get("prompt") is not None else None
        if one and "invalid" not in one:
            cur["prompt"] = one["prompt"]
    new = {"purpose": cur.get("purpose", "correct") if purpose is None else purpose,
           "user_instruction_ko": cur.get("user_instruction_ko", "") if user_instruction_ko is None else user_instruction_ko,
           "ai_auto": cur.get("ai_auto", True) if ai_auto is None else ai_auto,
           "prompt": cur.get("prompt") if prompt is ... else copy.deepcopy(prompt)}
    if new["purpose"] not in PURPOSES or not isinstance(new["user_instruction_ko"], str) or type(new["ai_auto"]) is not bool:
        raise ValueError("_v2 값이 잘못되었습니다(목적·지시문·AI 자동)")
    new["user_instruction_ko"] = new["user_instruction_ko"].strip()
    if new["prompt"] is not None and (not isinstance(new["prompt"], dict) or new["prompt"].get("source") not in PROMPT_SOURCES
                                      or not isinstance(new["prompt"].get("body_en"), str)
                                      or not isinstance(new["prompt"].get("input_hash"), str)):
        raise ValueError("_v2.prompt 값이 잘못되었습니다")
    out.pop(V2_KEY, None)
    default = new["purpose"] == "correct" and not new["user_instruction_ko"] and new["ai_auto"] is True and new["prompt"] is None
    if default:
        return out
    value = {"purpose": new["purpose"]}
    if new["user_instruction_ko"]:
        value["user_instruction_ko"] = new["user_instruction_ko"]
    value["ai_auto"] = new["ai_auto"]
    if new["prompt"] is not None:
        value["prompt"] = new["prompt"]
    draft = out.pop("_draft", ...)
    out[V2_KEY] = value
    if draft is not ...:
        out["_draft"] = draft
    return out


def _file_sha(path: str) -> str:
    """파일 내용의 sha256(없거나 못 읽으면 "missing"). (경로·크기·수정 시각)이 같으면 기억한 값."""
    try:
        st = os.stat(path)
        key = (os.path.normcase(os.path.abspath(path)), st.st_size, st.st_mtime_ns)
        value = _FILE_SHA.get(key)
        if value is None:
            import hashlib
            digest = hashlib.sha256()
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(1024 * 1024), b""):
                    digest.update(chunk)
            value = digest.hexdigest()
            if len(_FILE_SHA) > 4096:  # 다른 스레드가 비워도 이 호출은 지역 값(value)을 돌려준다
                _FILE_SHA.clear()
            _FILE_SHA[key] = value
        return value
    except OSError:
        return "missing"


def v2_roles(spec: dict, root: str = "") -> list:
    """레퍼런스 구성 = 크롭(@image1) 뒤에 올라갈 그림들의 [역할, 파일 지문] 목록(업로드 순서 — _plan_images). 역할 = "guide" /
    "before" / "ref". spec = spec_from_manifest 결과 그대로(평탄화 전 — guide·before_paste 는 root 기준 상대 경로일 수 있다).
    크롭 자신은 넣지 않는다(크롭을 조금 옮겨도 쓴 글은 유효하다)."""
    order, roles = _plan_images(spec)
    out = []
    for path in order[1:]:
        full = path if os.path.isabs(path) else os.path.join(root, path)
        out.append([roles[path][0][0], _file_sha(full)])
    return out


def roles_problem(roles: list, image_count: int | None = None) -> str | None:
    """쓴 글의 @imageN 이 가리키는 그림이 실제 업로드와 달라지는 구성 → 문제 문구(없으면 None). Prepare 는 없는 파일을 빼고 같은
    그림(평탄화한 픽셀이 같은 것)을 하나로 합쳐 올리므로, 그런 구성에서는 번호가 한 칸씩 밀린다 — 번호가 범위 안이어도 다른 그림이다.
    image_count = 실제로 올라갈 그림 수(Prepare 가 평탄화 뒤에 준다): 크롭 1 + 레퍼런스 구성의 수와 다르면 겹치거나 빠진 것이다."""
    if any(sha == "missing" for _, sha in roles):
        return "레퍼런스·가이드 파일이 없어 프롬프트의 그림 번호가 달라집니다 — 레퍼런스 연결을 확인해 주세요"
    refs = [sha for role, sha in roles if role == "ref"]
    if len(set(refs)) != len(refs):
        return "같은 레퍼런스 그림이 두 번 연결되어 프롬프트의 그림 번호가 달라집니다 — 겹친 연결을 정리해 주세요"
    if image_count is not None and image_count != 1 + len(roles):
        return "올라갈 그림 수가 프롬프트를 쓸 때와 다릅니다(같은 그림이 겹치거나 빠짐) — 레퍼런스 연결을 정리해 주세요"
    return None


def v2_input_hash(v2: dict, model: str, roles: list, *, manual: bool = False) -> str:
    """쓴 글이 어떤 입력으로 쓰였는지의 지문(sha256 앞 24자). AI 작성본 = 목적·지시문·생성 모델·레퍼런스 구성·판, 직접 입력 글 =
    레퍼런스 구성·판(지시문이 바뀌어도 사람이 쓴 글은 유효 — D505). 생성 서버의 지침 개정은 넣지 않는다."""
    import hashlib
    import json
    material = {"v": ASSIST_VERSION, "roles": [[str(r), str(s)] for r, s in roles]}
    if not manual:
        material.update(purpose=v2["purpose"], instruction=v2["user_instruction_ko"].strip(), model=str(model))
    return hashlib.sha256(json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:24]


def v2_state(v2, model: str, roles: list, image_count: int | None = None) -> dict:
    """이 크롭이 지금 어느 경로로 가는가 → {"state": "template" | "body" | "blocked", "why": str | None, "body": str | None,
    "source": str | None, "input_hash": 지금 입력의 AI 작성 지문 | None}.
    - template: _v2 가 없거나, 쓸 글이 없고 지시문도 없고 목적이 correct·add — 지금 템플릿 글 그대로.
    - body: 쓸 글이 있다 — 직접 입력 글(레퍼런스 구성이 쓸 때와 같음) 또는 AI 작성본(AI 자동이 켜져 있고 입력이 쓸 때와 같음).
    - blocked: 지시문이 있거나 목적이 remove·vary·detail 인데 쓸 글이 없다(없음·낡음·AI 자동 꺼짐·그림 번호 어긋남) — 작업을
      만들지 않는다(과금 없음, D503)."""
    if v2 is None:
        return {"state": "template", "why": None, "body": None, "source": None, "input_hash": None}
    if "invalid" in v2:
        return {"state": "blocked", "why": v2["invalid"], "body": None, "source": None, "input_hash": None}
    current = v2_input_hash(v2, model, roles)
    prompt = v2["prompt"]
    why = None
    if prompt is not None:
        manual = prompt["source"] == "manual"
        if not manual and not v2["ai_auto"]:
            why = "AI 자동이 꺼져 있습니다 — 영문 프롬프트를 직접 입력해 주세요"
        elif prompt["input_hash"] != (v2_input_hash(v2, model, roles, manual=True) if manual else current):
            why = ("레퍼런스 구성이 바뀌어 직접 입력한 프롬프트의 그림 번호를 다시 확인해야 합니다 — 프롬프트를 다시 저장해 주세요" if manual
                   else "지시·모델·레퍼런스가 바뀌어 작성본이 낡았습니다 — 다시 작성해 주세요")
        else:
            why = roles_problem(roles, image_count) or body_problem(prompt["body_en"], image_count)
        if why is None:
            return {"state": "body", "why": None, "body": prompt["body_en"].replace("\r\n", "\n").replace("\r", "\n").strip(),
                    "source": prompt["source"], "input_hash": current}
    needs = bool(v2["user_instruction_ko"].strip()) or v2["purpose"] in BODY_ONLY_PURPOSES
    if prompt is None and needs:
        why = ("지시문에 맞는 프롬프트가 없습니다 — [AI 작성] 을 누르거나 영문 프롬프트를 직접 입력해 주세요" if v2["ai_auto"]
               else "AI 자동이 꺼져 있습니다 — 영문 프롬프트를 직접 입력해 주세요")
    if needs or prompt is not None:
        return {"state": "blocked", "why": why, "body": None, "source": None, "input_hash": current}
    return {"state": "template", "why": None, "body": None, "source": None, "input_hash": current}


def render_body(spec: dict, body: str) -> dict:
    """본문 경로의 프롬프트 = render(spec) 의 틀 + 본문. Target… 줄부터 Preserve 줄 앞(Integration 줄 포함)까지를 "Request:" 줄과
    본문으로 바꾼다. 반환은 render 와 같은 키(prompt / images / legend) — images·legend 는 render 그대로."""
    # 틀은 줄 단위로 자른다 — 범례·타깃 줄에 들어가는 글에 줄바꿈이 있으면 경계(Target…·Preserve 줄)를 잘못 짚을 수 있어 받지 않는다.
    # (preserve·notes 는 Preserve 줄 뒤라 경계에 닿지 않는다.)
    between = [spec.get("style_note"), spec.get("ref_style")]
    for t in spec.get("targets") or []:
        between += [t.get(k) for k in ("name", "detail", "ref_object", "ref_ignore", "location", "occluded_by", "defects", "guide_color")]
        between += list(t.get("must_not") or [])
    if any(isinstance(x, str) and ("\n" in x or "\r" in x) for x in between):
        raise ValueError("타깃 설명에 줄바꿈이 있어 직접 쓴 프롬프트를 넣을 자리를 정할 수 없습니다 — 타깃 설명을 한 줄로 고쳐 주세요")
    # 본문은 @imageN 으로만 그림을 가리킨다 — 틀도 같은 표기로 맞춘다(실행 설정의 ref_style 이 plain·both 여도. 본문 경로의 글만 달라진다).
    rendered = render({**spec, "ref_style": "at"})
    lines = rendered["prompt"].split("\n")
    start = next((i for i, line in enumerate(lines) if line in _TARGET_HEADS), None)
    end = next((i for i, line in enumerate(lines) if start is not None and i > start and line.startswith("Preserve: ")), None)
    if start is None or end is None:
        raise ValueError("프롬프트 틀에서 Target·Preserve 줄을 찾지 못했습니다")
    if _BODY_PLAIN_FRAME.search("\n".join(lines[:start] + lines[end:])):
        raise ValueError("프롬프트 틀의 그림 표기가 @image 꼴이 아닙니다")
    text = body.replace("\r\n", "\n").replace("\r", "\n").strip()
    problem = body_problem(text, len(rendered["images"]))
    if problem:
        raise ValueError(problem)
    return {"prompt": "\n".join(lines[:start] + [BODY_HEAD, text] + lines[end:]), "images": rendered["images"], "legend": rendered["legend"]}
