"""Design Patch Studio — 생성 모델 표(서버 모드의 모델 고르기). 순수 데이터와 계산만 둔다(파일·네트워크·ComfyUI 를 쓰지 않는다).

모델마다 생성 요청의 꼴이 다르다 — 크기를 주는 법, 받는 입력 장수, 한 번에 주는 장수, 품질 선택지. 그 차이를 이 표 한 곳에
적고, 나머지 코드는 모델 이름을 직접 비교하지 않고 표를 읽는다.

  kind        요청을 조립하는 방식. "gpt" = 지금까지의 GPT Image(로컬판도 생성) · "seedream" · "ideogram"(서버 모드 전용)
  checkpoint  생성 서버의 모델 목록에 있는 id — 목록에 그 id 가 없으면 제출하지 않는다(다른 모델로 바뀌어 불리는 것을 막는다)
  max_inputs  한 요청의 입력 그림 상한(타깃 크롭 포함). 실제 상한은 서버 목록의 값과 이 값 가운데 작은 쪽이다
  max_n       한 요청의 장 수 상한(장마다 따로 부르는 모델은 PROVIDER_CALL_LIMIT 를 넘지 않는다 — 잡 하나가 그만큼의 호출이다)
  qualities   품질 선택지. 비어 있으면 품질을 고르지 않는 모델이고 작업의 quality 자리에는 FIXED_QUALITY 가 들어간다
  fan_out     서버가 장마다 공급자를 따로 부른다(잡 하나 = n 번 호출) — 동시 제출 수를 줄이는 근거
  prompt_used 서버가 실제 쓰인 프롬프트를 돌려준다(「프롬프트가 서버에서 바뀜」 판정을 이 모델에서만 한다)
  opts        품질 말고 모델에만 있는 옵션의 선택지 {이름: (값…)} — 첫 값이 기본값. 작업의 opts 와 생성 조건 키에 들어간다

이름(표의 키)은 프로젝트 기록에 남는 값이다 — 서버의 id 가 바뀌어도 이름은 그대로 두고 checkpoint 만 고친다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from fractions import Fraction

FIXED_QUALITY = "standard"  # 품질을 고르지 않는 모델의 작업·생성 조건 키에 들어가는 값
GPT_QUALITIES = ("max", "xhigh", "high", "medium", "low")
SIZE_RULES = ("user_k", "int_k_2560")  # store.GPT_SIZE_RULES 와 같은 이름 — 기준 배율만 같이 쓴다
RATIO_TOLERANCE = 0.01  # 받은 그림의 가로세로비가 요청과 이보다 크게 다르면 「비율 다름」
PROVIDER_CALL_LIMIT = 8  # 장마다 따로 부르는 모델: 동시에 낸 잡들의 장 수 합 상한


@dataclass(frozen=True)
class Model:
    name: str
    label: str
    checkpoint: str
    kind: str
    max_inputs: int
    max_n: int = 10
    qualities: tuple = ()
    default_quality: str = FIXED_QUALITY
    fan_out: bool = False
    prompt_used: bool = False
    area: tuple | None = None        # (최소, 최대) 출력 화소 수
    max_ratio: int | None = None     # 긴 변 / 짧은 변 상한
    min_side: int = 1                # 짧은 변 하한
    family: str | None = None        # 같은 계열에서 기본값을 차례로 고를 때 쓰는 묶음 이름
    opts: dict = field(default_factory=dict)


def _gpt(name: str, checkpoint: str) -> Model:
    return Model(name=name, label=name, checkpoint=checkpoint, kind="gpt", max_inputs=16, max_n=10,
                 qualities=GPT_QUALITIES, default_quality="max")


# 순서 = 화면 선택지의 순서. 앞의 넷은 로컬판의 모델 목록(pipeline._MODELS)과 같은 순서다.
MODELS: dict[str, Model] = {m.name: m for m in (
    _gpt("gpt-image-2.6-sunburst", "gpt_image_26_sunburst"),
    _gpt("gpt-image-2.6-flare", "gpt_image_26_flare"),
    _gpt("gpt-image-2.5-sunburst", "gpt_image_25_sunburst"),
    _gpt("gpt-image-2.5-flare", "gpt_image_25_flare"),
    Model(name="seedream-5.5", label="Seedream 5.5", checkpoint="seedream_5_5", kind="seedream", max_inputs=10,
          max_n=PROVIDER_CALL_LIMIT, fan_out=True, area=(921_600, 4_624_220), max_ratio=16, min_side=512),
    Model(name="ideogram-4.5", label="Ideogram 4.5", checkpoint="ideogram_4_5", kind="ideogram", max_inputs=5, max_n=8,
          qualities=("low", "medium", "high"), default_quality="high", prompt_used=True, area=(1, 4_194_304)),
)}
LOCAL_MODELS = tuple(name for name, m in MODELS.items() if m.kind == "gpt")   # 로컬판(ComfyUI 탭)이 생성하는 모델
FORGE_ONLY = tuple(name for name, m in MODELS.items() if m.kind != "gpt")     # 서버 모드에서만 생성하는 모델


def get(name) -> Model | None:
    return MODELS.get(name) if isinstance(name, str) else None


def is_forge_only(name) -> bool:
    return name in FORGE_ONLY


def names(server: bool) -> tuple:
    """실행 설정 「모델」의 선택지 — 로컬판은 지금의 넷, 서버 모드는 표 전부."""
    return tuple(MODELS) if server else LOCAL_MODELS


def quality_problem(name, quality) -> str | None:
    """그 모델이 받는 품질인가 → 안내 문구(받으면 None). 품질을 고르지 않는 모델은 어떤 값이든 받는다(쓰지 않는다)."""
    m = get(name)
    if m is None:
        return f"모르는 생성 모델입니다: {name!r}"
    if m.qualities and quality not in m.qualities:
        return f"{m.label} 의 품질은 {' · '.join(m.qualities)} 가운데 하나여야 합니다: {quality!r}"
    return None


def job_quality(name, quality) -> str:
    """작업(jobs[].quality)·생성 조건 키에 들어갈 품질 값. GPT 는 받은 값 그대로다(키가 바뀌지 않는다)."""
    m = get(name)
    if m is None or m.kind == "gpt":
        return quality
    return quality if m.qualities else FIXED_QUALITY


def job_opts(name, opts=None) -> dict | None:
    """작업(jobs[].opts)·생성 조건 키에 들어갈 모델 전용 옵션 — 표에 opts 가 없는 모델은 None(키에 아무것도 더하지 않는다).
    있는 모델은 받은 값에서 표의 이름만 남기고, 빠졌거나 선택지에 없는 값은 기본값(첫 값)으로 채운다."""
    m = get(name)
    if m is None or not m.opts:
        return None
    given = opts if isinstance(opts, dict) else {}
    return {key: (given[key] if given.get(key) in choices else choices[0]) for key, choices in sorted(m.opts.items())}


def _even(value: float, *, up: bool) -> int:
    n = math.ceil(value) if up else math.floor(value)
    if n % 2:
        n += 1 if up else -1
    return max(2, n)


def base_scale(w: int, h: int, rule: str = "user_k") -> Fraction:
    """크롭 w×h 에 곱할 기준 배율 — GPT 의 크기 규칙(store.gpt_output_size)과 같은 출발값.
    user_k: 정사각은 2048 이 되게, 그 밖은 max(2, 1024/짧은 변). int_k_2560: 긴 변 ≤ 2560 인 최대 정수(최소 2)."""
    if rule not in SIZE_RULES:
        raise ValueError(f"알 수 없는 출력 크기 규칙: {rule!r} (가능: {', '.join(SIZE_RULES)})")
    if rule == "int_k_2560":
        return Fraction(max(2, 2560 // max(w, h)))
    return Fraction(2048, w) if w == h else max(Fraction(2), Fraction(1024, min(w, h)))


def output_size(name: str, w: int, h: int, rule: str = "user_k") -> tuple[int, int, dict]:
    """크롭 w×h → 그 모델에 요청할 출력 크기 (W, H, info). GPT 계열은 다루지 않는다(store.gpt_output_size 를 쓴다).

    기준 배율(base_scale)을 곱한 뒤 모델의 화소 수 창 안으로 같은 비율로 줄이거나 키우고, 짧은 변 하한을 맞추고, 짝수로 맞춘다.
    짝수로 맞출 때는 창 안에 드는 후보(변마다 내림·올림, 한 변을 정하고 다른 변을 비율로 다시 구한 것) 가운데 비율 오차가 가장 작은 것을 고른다.
    크롭의 비율이 모델의 한도를 넘으면 창을 맞출 수 없다 — 그때도 가장 가까운 크기를 돌려주고 info["problem"] 에 이유를 적는다
    (작업은 만들어지고, 생성 직전 판정(size_problem)이 「발사 못 함」으로 막는다). 아주 가는 크롭이라 비율이 RATIO_TOLERANCE 넘게
    달라질 때도 info["problem"] 에 적는다(이때는 막히지 않는다 — 반영 보고의 경고로만 보인다).
    info: rule · src · method("k" | "fit") · k · k_str · scale[x, y] · aspect_err · pixels · model · problem(있을 때만)."""
    m = get(name)
    if m is None or m.kind == "gpt" or m.area is None:
        raise ValueError(f"이 모델의 출력 크기는 여기서 정하지 않습니다: {name!r}")
    if type(w) is not int or type(h) is not int or w < 1 or h < 1:
        raise ValueError(f"크롭 크기가 잘못되었습니다: {w!r}x{h!r}")
    k = base_scale(w, h, rule)
    lo, hi = m.area
    scale = float(k)
    if w * h * scale * scale > hi:
        scale = math.sqrt(hi / (w * h))
    if w * h * scale * scale < lo:
        scale = math.sqrt(lo / (w * h))
    if min(w, h) * scale < m.min_side:
        scale = m.min_side / min(w, h)
    exact = float(k) == scale and (w * k).denominator == 1 and (h * k).denominator == 1 and not (w * k) % 2 and not (h * k) % 2
    if exact:
        W, H = int(w * k), int(h * k)
    else:
        ws = {_even(w * scale, up=False), _even(w * scale, up=True)}
        hs = {_even(h * scale, up=False), _even(h * scale, up=True)}
        pairs = {(a, b) for a in ws for b in hs}
        for a in ws:  # 가로를 정하고 세로를 비율로(화소 상한을 넘으면 상한 안의 가장 큰 짝수로)
            pairs |= {(a, min(_even(a * h / w, up=up), _even(hi / a, up=False))) for up in (False, True)}
        for b in hs:
            pairs |= {(min(_even(b * w / h, up=up), _even(hi / b, up=False)), b) for up in (False, True)}
        fits = [(abs((a / b) / (w / h) - 1.0), -a * b, a, b) for a, b in pairs if size_problem(name, [a, b]) is None]
        if fits:  # 비율 오차가 가장 작은 것, 같으면 화소가 많은 것
            W, H = min(fits)[2:]
        else:  # 창을 맞출 수 없는 비율 — 가장 가까운 크기(아래에서 problem 이 붙는다)
            W, H = _even(w * scale, up=False), _even(h * scale, up=False)
            if W * H < lo or min(W, H) < m.min_side:
                W, H = _even(w * scale, up=True), _even(h * scale, up=True)
            while W * H > hi and min(W, H) > 2:
                if W >= H:
                    W -= 2
                else:
                    H -= 2
    aspect_err = (W / H) / (w / h) - 1.0
    info = {"rule": rule, "src": [w, h], "method": "k" if exact else "fit", "k": round(float(k), 6),
            "k_str": f"{k.numerator}/{k.denominator}" if k.denominator != 1 else str(k.numerator),
            "scale": [round(W / w, 6), round(H / h, 6)],
            "aspect_err": round(aspect_err, 6) if abs(aspect_err) > 1e-12 else 0.0, "pixels": W * H, "model": name}
    problem = size_problem(name, [W, H])
    if problem is None and abs(aspect_err) > RATIO_TOLERANCE:
        problem = f"크롭이 너무 가늘어 {m.label} 의 크기로는 가로세로비가 {abs(aspect_err) * 100:.1f}% 달라집니다."
    if problem:
        info["problem"] = problem
    return W, H, info


def size_problem(name, size) -> str | None:
    """요청 크기가 그 모델의 창 안인가 → 안내 문구(안이면 None). GPT 계열은 여기서 보지 않는다(None)."""
    m = get(name)
    if m is None or m.kind == "gpt" or m.area is None:
        return None
    try:
        W, H = size
    except (TypeError, ValueError):
        return "요청 크기 기록이 잘못되었습니다 — 반영을 다시 실행해 주세요."
    if type(W) is not int or type(H) is not int or W < 1 or H < 1:
        return "요청 크기 기록이 잘못되었습니다 — 반영을 다시 실행해 주세요."
    if m.max_ratio is not None and max(W, H) > m.max_ratio * min(W, H):
        return f"크롭의 가로세로비가 {m.label} 의 한도(1:{m.max_ratio}–{m.max_ratio}:1)를 벗어납니다."
    lo, hi = m.area
    if not lo <= W * H <= hi or min(W, H) < m.min_side:
        return f"요청 크기 {W}x{H} 가 {m.label} 이 받는 범위를 벗어납니다."
    return None


def ratio_off(requested, received) -> bool:
    """받은 그림의 가로세로비가 요청한 크기와 RATIO_TOLERANCE 넘게 다른가. 어느 쪽이든 크기를 모르면 False."""
    try:
        (rw, rh), (gw, gh) = requested, received
        if min(rw, rh, gw, gh) <= 0:
            return False
        return abs((gw / gh) / (rw / rh) - 1.0) > RATIO_TOLERANCE
    except (TypeError, ValueError, ZeroDivisionError):
        return False


def job_slots(name, concurrency: int, n: int) -> int:
    """한 번에 내 둘 잡 수. 장마다 따로 부르는 모델은 「잡 수 × 장 수 ≤ PROVIDER_CALL_LIMIT」이 되게 줄인다(최소 1)."""
    slots = max(1, int(concurrency))
    m = get(name)
    if m is not None and m.fan_out:
        slots = max(1, min(slots, PROVIDER_CALL_LIMIT // max(1, int(n))))
    return slots


def availability(name, catalog) -> tuple[bool, str | None, int | None]:
    """서버 모델 목록(catalog = {checkpoint: 입력 상한 | None}, 읽지 못했으면 None)에 비춘 그 모델의 상태 →
    (쓸 수 있나, 못 쓰는 이유, 실제 입력 상한). 표에 없는 모델·목록을 읽지 못함·목록에 없음은 모두 「쓸 수 없음」이다."""
    m = get(name)
    if m is None:
        return False, "지원하지 않는 생성 모델입니다.", None
    if not isinstance(catalog, dict):
        return False, "Forge 의 모델 목록을 읽지 못했습니다 — 잠시 뒤 다시 시도해 주세요.", None
    if m.checkpoint not in catalog:
        return False, f"이 모델({m.label})은 지금 Forge 에 없습니다 — 실행 설정에서 다른 모델로 바꿔 주세요.", None
    limit = catalog[m.checkpoint]
    limit = min(m.max_inputs, limit) if type(limit) is int and limit > 0 else m.max_inputs
    return True, None, limit


def describe(name, catalog=None, *, checked: bool = False) -> dict:
    """화면에 주는 모델 한 줄(GET env 의 caps.models 항목). checked = 서버 목록을 대조했는가 — 대조하지 않았으면 available 은 참이고
    max_inputs 는 표의 값이다."""
    m = MODELS[name]
    ok, why, limit = availability(name, catalog) if checked else (True, None, m.max_inputs)
    return {"name": m.name, "label": m.label, "kind": m.kind, "available": ok, "why": why,
            "qualities": list(m.qualities), "default_quality": m.default_quality if m.qualities else None,
            "opts": {key: list(values) for key, values in sorted(m.opts.items())},
            "max_inputs": limit if limit is not None else m.max_inputs, "max_n": m.max_n, "fan_out": m.fan_out}
