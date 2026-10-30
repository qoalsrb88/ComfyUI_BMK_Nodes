"""dp-prompt/1 기록 원문의 고정 문장만 풀이한다. 재렌더·번역 호출·파일 쓰기는 없다.

현재 스펙으로 과거 프롬프트를 재구성하지 않는다. 알려진 문장 틀에 맞지 않는 줄과
사용자가 채운 자유 문장은 「」 안에 원문을 남긴다. 줄 순서는 영문과 같다.
"""

from __future__ import annotations

import re


def _q(value):
    return f"「{value}」"


_SHAPE = {
    " Keep its current silhouette and volume; change only its surface design and details.": "현재 실루엣과 부피를 유지하고 표면 디자인과 세부만 바꿉니다.",
    " Change its outline only where the reference design requires it.": "레퍼런스 디자인에 필요한 부분만 윤곽을 바꿉니다.",
}
_TEXT = {
    " Keep any writing at its current legibility; do not invent words or numbers.": "글씨는 현재의 판독성을 유지하고 단어나 숫자를 새로 만들지 않습니다.",
    " Where its writing is illegible, render short, plausible text that suits the object.": "읽을 수 없는 글씨는 대상에 맞는 짧고 자연스러운 글로 표현합니다.",
}
_GUIDE = {
    "outline": "윤곽선", "solid shape": "채운 도형", "line": "중심선", "hatched area": "빗금 영역",
}
_IMAGE = r"(?:@image\d+|Image \d+)"
_IMAGES = _IMAGE + r"(?:(?:, | and |, and )" + _IMAGE + r")*"
_RULE = {
    "Follow the outline closely for its silhouette and extent.": "윤곽선의 형태와 범위를 정확히 따릅니다.",
    "The solid shape marks only its position and approximate size; take the exact shape from the reference.": "채운 도형은 위치와 대략적인 크기만 나타내며 정확한 형태는 레퍼런스를 따릅니다.",
    "The line is the centerline it follows; draw it along this path at a natural width.": "표시선은 중심 경로이며 자연스러운 폭으로 그 경로를 따릅니다.",
    "The hatched area marks the region it should cover.": "빗금은 대상이 덮을 영역을 나타냅니다.",
}


def _mark(value):
    m = re.fullmatch(r"(.+?) (outline|solid shape|line|hatched area)", value)
    return f"{_q(m[1])} {_GUIDE[m[2]]}" if m else _q(value)


def _target(line, tids, labels):
    head = re.fullmatch(r"- (?:Target (?P<tid>\S+) — )?(?P<name>.+?) \((?P<ref>no reference|reference: .+?)\): (?P<body>.*)", line)
    if not head:
        return None
    tid = head["tid"] or (tids[0] if len(tids) == 1 else "")
    name = labels.get(tid) or _q(head["name"])
    source_name = re.escape(head["name"])
    body = head["body"]
    suffix = []
    diff = re.search(r" (.+?) shows it from another angle; draw it at the angle and perspective it needs in (.+?)\.$", body)
    # 이미지 토큰으로 시작하는 고정 접미사만 분리한다(슬롯 안의 영어는 건드리지 않음).
    if diff:
        diff = re.search(r" ((?:@image\d+|Image \d+)(?:(?:, | and |, and )(?:@image\d+|Image \d+))*) shows it from another angle; draw it at the angle and perspective it needs in (.+?)\.$", body)
    if diff:
        suffix.insert(0, f"{diff[1]}는 다른 각도이며 {diff[2]}에 필요한 각도와 원근으로 그립니다.")
        body = body[:diff.start()]
    exact = re.search(r" Copy its lettering glyph by glyph from (.+?) exactly as drawn; do not substitute real or different characters\.$", body)
    if exact:
        suffix.insert(0, f"글자는 {exact[1]}의 획 모양을 그대로 옮기고 실제 글자나 다른 글자로 바꾸지 않습니다.")
        body = body[:exact.start()]
    else:
        for en, ko in _TEXT.items():
            if body.endswith(en):
                body = body[:-len(en)]
                suffix.insert(0, ko)
                break
    ko = None
    m = re.fullmatch(r"Repair the (.+?) in (" + _IMAGE + r")(?:: (.*?))?\. Infer the intended form from intact parts of (.+?)—repeated shapes, symmetry, materials, and what each part holds, connects to, or overlaps—and rebuild the simplest coherent structure\. Keep its silhouette, size, position, orientation, and design; change only what is broken; where the form stays uncertain, keep it simple instead of inventing new detail\.", body)
    if m:
        ko = f"{m[2]}의 {name}을 복구합니다" + (f": {_q(m[3])}" if m[3] else "") + f". {m[4]}의 온전한 부분(반복 형태·대칭·재질·연결·겹침)에서 의도를 추론해 가장 단순하고 일관된 구조로 복구합니다. 실루엣·크기·위치·방향·디자인을 유지하고 망가진 곳만 고칩니다. 불확실한 형태에 새 세부를 만들지 않습니다."
    m = re.fullmatch(r"Add the ((?i:" + source_name + r")) (.+?), with the design of (.+?) in (" + _IMAGES + r"): (.*?)\. Adapt it to (.+?)'s pose, perspective, surface curvature, and occlusion, at a scale consistent with the surrounding forms\.", body)
    if m:
        ko = f"{name}을 {_q(m[2])}에 추가합니다. {m[4]}의 {_q(m[3])} 디자인: {_q(m[5])}. {m[6]}의 자세·원근·표면 곡률·가림에 맞추고 주변 형태와 크기를 맞춥니다."
    if body.startswith("Correct the ") and body.endswith(" Keep its position, size, and orientation."):
        base = body[:-len(" Keep its position, size, and orientation.")]
        shape = None
        for en, text in _SHAPE.items():
            if base.endswith(en):
                base, shape = base[:-len(en)], text
                break
        free = re.search(r" Its shape and proportions may change as much as the reference design requires, adapted to the pose, perspective, and attachment point in (.+?)\.$", base)
        if free:
            shape = f"레퍼런스에 필요한 만큼 형태와 비율을 바꾸되 {free[1]}의 자세·원근·부착점에 맞춥니다."
            base = base[:free.start()]
        m = re.fullmatch(r"Correct the (.+?) in (" + _IMAGE + r") to match (.+?) in (" + _IMAGES + r"): (.*?)\.(?: Remove (.*?)\.)?", base)
        if m and shape:
            ko = f"{m[2]}의 {name}을 {m[4]}의 {_q(m[3])}에 맞춰 수정합니다: {_q(m[5])}."
            if m[6]:
                ko += f" {_q(m[6])}을 제거합니다."
            ko += f" {shape} 위치·크기·방향은 유지합니다."
    # 가이드 타깃: 명령, 가이드 규칙, 디자인, 제거/가림을 각각 보존한다.
    for rule, rule_ko in _RULE.items():
        if f" {rule} Take its design from " not in body:
            continue
        verb, rest = body.split(f" {rule} Take its design from ", 1)
        verbs = [
            (r"Add the (.+?) where (.+?) marks it with the (.+?)\.", lambda m: f"{m[2]}의 {_mark(m[3])} 표시 위치에 {name}을 추가합니다."),
            (r"Move the (.+?): remove it from its current place, restoring what lies behind it, and draw it where (.+?) marks it with the (.+?)\.", lambda m: f"{name}을 옮깁니다. 원래 자리에서 제거하고 뒤를 복구한 뒤 {m[2]}의 {_mark(m[3])} 위치에 그립니다."),
            (r"Replace what is inside the (.+?) in (.+?) with the (.+?)\.", lambda m: f"{m[2]}의 {_mark(m[1])} 안을 {name}으로 교체합니다."),
            (r"Redraw the (.+?) inside the (.+?) in (.+?)\.", lambda m: f"{m[3]}의 {_mark(m[2])} 안에 {name}을 다시 그립니다."),
        ]
        m = re.fullmatch(r"(.+?) in (" + _IMAGES + r"), adapted to the perspective, curvature, and lighting at that spot: (.*?)\.(?: Remove (.*?)\.)?(?: Keep it behind (.*?)\.)?", rest)
        for pat, render in verbs:
            v = re.fullmatch(pat, verb)
            if v and m:
                ko = render(v) + f" {rule_ko} {m[2]}의 {_q(m[1])} 디자인을 그 위치의 원근·곡률·조명에 맞춥니다: {_q(m[3])}."
                if m[4]:
                    ko += f" {_q(m[4])}을 제거합니다."
                if m[5]:
                    ko += f" {_q(m[5])} 뒤에 둡니다."
    m = re.fullmatch(r"A cutout of the (.+?) has been roughly pasted into (.+?)(?: \(outlined in (.+?) in (.+?)\))?\. Its design, position, scale, and orientation are already correct—keep them\. Re-render it so it belongs in the painting: clean up cutout edges, halos, and leftover outlines; match (.+?)'s line weight, shading, texture, and sharpness;(?: restore crisp detail from (.+?) where the paste is blurry;)? add contact shadows(?: and let (.+?) overlap it as before)?; and remove any remains of the previous (.+?) around it\.(?: Use (.+?) only to restore the surroundings where the paste left gaps or seams\.)?(?: Its design: (.*?)\.)?", body)
    if m:
        ko = f"{name}의 컷아웃이 {m[2]}에 거칠게 붙여져 있습니다" + (f"({m[4]}의 {_q(m[3])} 윤곽)" if m[3] else "")
        ko += f". 디자인·위치·크기·방향은 이미 맞으므로 유지합니다. 컷아웃 경계·후광·남은 윤곽을 정리하고 {m[5]}의 선 굵기·음영·질감·선명도에 맞춰 다시 그립니다."
        if m[6]:
            ko += f" 흐린 부분은 {m[6]}에서 선명한 세부를 복구합니다."
        ko += " 접촉 그림자를 추가합니다."
        if m[7]:
            ko += f" {_q(m[7])}이 이전처럼 겹치게 합니다."
        ko += f" 주변에 남은 이전 {name}의 흔적을 제거합니다."
        if m[9]:
            ko += f" {m[9]}는 붙여넣기 틈·이음매 주변 복구에만 씁니다."
        if m[10]:
            ko += f" 디자인: {_q(m[10])}."
    if ko is None:
        return None
    ref = "레퍼런스 없음" if head["ref"] == "no reference" else head["ref"].replace("reference:", "레퍼런스:", 1).replace("; guide:", "; 가이드:", 1)
    return f"- {('타깃 ' + tid + ' — ') if head['tid'] else ''}{name} ({ref}): {ko}" + (" " + " ".join(suffix) if suffix else "")


def explain(prompt: str, template_version: str, tids=(), labels=None) -> str:
    """기록된 prompt 의 줄 구조를 유지한 한국어 풀이. 모르는 버전은 원문만 인용."""
    if template_version != "dp-prompt/1":
        return _q(prompt)
    labels = labels or {}
    out = []
    for line in prompt.split("\n"):
        ko = {"Input images, in upload order:": "입력 이미지 — 업로드 순서:", "": "",
              "Target:": "타깃:", "Targets (change only these):": "타깃 — 아래 대상만 변경:"}.get(line)
        if ko is None and line.startswith("Notes: "):
            ko = "메모: " + _q(line[len("Notes: "):])
        if ko is None and line.startswith("- "):
            ko = _target(line, tids, labels)
        if ko is None:
            ko = _line(line)
        out.append(ko if ko is not None else _q(line))
    return "\n".join(out)


def _line(line):
    m = re.fullmatch(r"- (.+?): the canvas: a crop of a finished illustration( into which reference cutouts were roughly pasted)?\. This is the image to edit\.", line)
    if m:
        return f"- {m[1]}: 완성 일러스트의 크롭" + ("에 레퍼런스 컷아웃을 거칠게 붙인 캔버스" if m[2] else " 캔버스") + ". 이 이미지를 편집합니다."
    m = re.fullmatch(r"- (.+?): placement guide: a copy of (.+?) with colored annotation marks\. Instructions only\.", line)
    if m:
        return f"- {m[1]}: {m[2]}에 색 표시를 더한 배치 가이드. 지시용입니다."
    m = re.fullmatch(r"- (.+?): the same crop before the cutouts were pasted\. Use it only to restore surroundings\.", line)
    if m:
        return f"- {m[1]}: 컷아웃을 붙이기 전 같은 크롭. 주변 복구에만 씁니다."
    m = re.fullmatch(r"- (.+?): design reference for (the target|Targets? .+?)(?: \((.*?)\))?\.(?: Ignore (.*?)\.)?", line)
    if m:
        return f"- {m[1]}: {m[2].replace('the target', '타깃').replace('Targets', '타깃').replace('Target', '타깃')}의 디자인 레퍼런스" + (f" ({_q(m[3])})" if m[3] else "") + "." + (f" {_q(m[4])}은 무시합니다." if m[4] else "")
    m = re.fullmatch(r"Canvas: Edit (.+?) and output only its finished edit\. Keep (.+?)'s framing, composition, and aspect ratio exactly, so every unchanged area stays aligned with it\.( Use the other images only as described here; never take their framing, backgrounds, or unrelated content\.)?", line)
    if m:
        return f"캔버스: {m[1]}을 편집하고 완성된 편집본만 출력합니다. {m[2]}의 프레이밍·구도·가로세로 비율을 정확히 유지해 바꾸지 않은 영역이 정렬되도록 합니다." + (" 다른 이미지는 여기 적힌 용도로만 쓰고 프레이밍·배경·무관한 내용을 가져오지 않습니다." if m[3] else "")
    m = re.fullmatch(r"Guide: (.+?) is (.+?) with annotation marks \((.*?)\)\. The marks only show where things go and how much area they cover; they are not artwork\. The output must contain no trace of them\.", line)
    if m:
        return f"가이드: {m[1]}는 {m[2]}에 주석 표시({_q(m[3])})를 더한 것입니다. 표시는 위치와 범위만 알리며 그림의 일부가 아닙니다. 결과에 표시 흔적을 남기지 않습니다."
    m = re.fullmatch(r"Integration: Paint (each target|the target) in (.+?)'s own rendering—(.*?)—so it looks painted together with the surrounding illustration\. Take base colors and materials from the references, but show them under (.+?)'s lighting and color grading\. Do not copy the references' line art, flat lighting, or backgrounds\.", line)
    if m:
        style = "선 굵기·선 색·음영 방식·질감·세부 수준" if m[3] == "its line weight and line color, shading style, texture, and level of detail" else _q(m[3])
        return f"통합: {'각 타깃' if m[1] == 'each target' else '타깃'}을 {m[2]}의 표현({style})으로 그려 주변 일러스트와 함께 그린 것처럼 만듭니다. 기본 색과 재질은 레퍼런스에서 가져오되 {m[4]}의 조명과 색 보정을 따릅니다. 레퍼런스의 선화·평평한 조명·배경은 복사하지 않습니다."
    m = re.fullmatch(r"Integration: Match (.+?)'s colors, materials, lighting, line quality, texture, and level of detail\. Keep intentional brushwork, softness, and effects; avoid blanket smoothing, sharpening, or added detail\.", line)
    if m:
        return f"통합: {m[1]}의 색·재질·조명·선·질감·세부 수준을 맞춥니다. 의도한 붓질·부드러움·효과를 유지하고 전체를 매끈하게 하거나 선명하게 하거나 세부를 더하지 않습니다."
    m = re.fullmatch(r"Preserve: Leave everything outside the (targets|target) as it is in (.+?)(?:, especially (.*?))?\. Do not re-line, sharpen, smooth, recolor, or restyle untouched areas; keep the overall brightness, saturation, and color temperature; add no other objects\. Around (each target|the target), adjust only the edges, overlaps, and contact shadows needed to blend it in\.", line)
    if m:
        return f"보존: 타깃 바깥은 {m[2]} 그대로 둡니다" + (f"(특히 {_q(m[3])})" if m[3] else "") + ". 건드리지 않은 영역의 선·선명도·부드러움·색·화풍을 바꾸지 않고 전체 밝기·채도·색온도를 유지하며 다른 물체를 더하지 않습니다. 타깃 주변에서는 어우러지는 데 필요한 경계·겹침·접촉 그림자만 조정합니다."
    return None
