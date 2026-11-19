"""Design Patch Studio — 생성 모델 표(서버 모드의 모델 고르기). 순수 데이터와 계산만 둔다(파일·네트워크·ComfyUI 를 쓰지 않는다).

모델마다 생성 요청의 꼴이 다르다 — 크기를 주는 법, 받는 입력 장수, 한 번에 주는 장수, 품질 선택지. 그 차이를 이 표 한 곳에
적고, 나머지 코드는 모델 이름을 직접 비교하지 않고 표를 읽는다.

  kind        요청을 조립하는 방식. "gpt" = 지금까지의 GPT Image(로컬판도 생성) · "seedream" · "ideogram" · "gemini"(서버 모드 전용)
  checkpoint  생성 서버의 모델 목록에 있는 id — 목록에 그 id 가 없으면 제출하지 않는다(다른 모델로 바뀌어 불리는 것을 막는다)
  max_inputs  한 요청의 입력 그림 상한(타깃 크롭 포함). 실제 상한은 서버 목록의 값과 이 값 가운데 작은 쪽이다
  max_n       한 요청의 장 수 상한(장마다 따로 부르는 모델은 PROVIDER_CALL_LIMIT 를 넘지 않는다 — 잡 하나가 그만큼의 호출이다)
  qualities   품질 선택지. 비어 있으면 품질을 고르지 않는 모델이고 작업의 quality 자리에는 FIXED_QUALITY 가 들어간다
  fan_out     서버가 장마다 공급자를 따로 부른다(잡 하나 = n 번 호출) — 동시 제출 수를 줄이는 근거
  prompt_used 공급자가 프롬프트를 돌려주는 모델이다. 보낸 글과 다른지는 모든 모델에서 판정하고, 이 값은 표지의 이름만 가른다
              (이 모델 = 「공급자가 프롬프트를 고쳐 씀」, 그 밖 = 「프롬프트가 서버에서 바뀜」)
  opts        품질 말고 모델에만 있는 옵션의 선택지 {이름: (값…)}. 기본값은 opt_defaults(없으면 첫 값). 작업의 opts 와 생성 조건 키에 들어간다
  ratios      정해진 가로세로비로만 그리는 모델의 비율 목록("가로:세로"). 크롭의 비율이 이 가운데 하나의 예상 출력 비율
              (RATIO_SIZES 의 가로/세로)과 RATIO_TOLERANCE 안에서 맞을 때만 생성한다 — 맞은 비율은 작업의
              opts["aspect_ratio"] 에 적힌다(없으면 발사하지 않는다)
  family      같은 계열의 묶음 이름. 표의 순서가 계열 안의 우선순위다(모델이 서버 목록에서 빠지면 다음 것을 권한다)
  complexities 요청의 복잡도 선택지(GPT Image 2.6 계열만 — store.COMPLEXITY_MODELS 와 같은 모델). 비어 있으면 복잡도가 없는 모델이다

패치별 생성 설정(UX v2-B — 프로젝트 폴더의 crop_gen.json 한 항목): 패치마다 모델 묶음과 장 수를 따로 정한다.
  {"model", "quality"?, "opts"?, "complexity"?, "n"?}
  모델 묶음 = model + 그 모델에 필요한 칸 전부(품질을 고르는 모델 = quality · 옵션이 있는 모델 = opts 의 모든 이름 ·
  복잡도가 있는 모델 = complexity). 그 모델에 없는 칸은 적지 않는다. n 은 모델 묶음 없이 혼자 있을 수 있다.
  gen_bundle 이 저장할 모델 묶음을 만들고, gen_entry_problem 이 읽은 항목을 검사하고, effective_gen 이 프로젝트 값과 합친다.

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
COMPLEXITIES = ("low", "medium", "high")  # GPT Image 2.6 계열의 복잡도 선택지
DEFAULT_COMPLEXITY = "high"
GEN_KEYS = ("model", "quality", "opts", "complexity", "n")  # 패치별 생성 설정 한 항목의 키(저장 순서)
GEN_BROKEN = "패치의 생성 설정을 읽지 못했습니다"


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
    opt_defaults: dict = field(default_factory=dict)
    ratios: tuple = ()               # 정해진 가로세로비로만 그리는 모델의 비율 목록
    complexities: tuple = ()         # 복잡도 선택지(GPT Image 2.6 계열만)


def _gpt(name: str, checkpoint: str, complexities: tuple = ()) -> Model:
    return Model(name=name, label=name, checkpoint=checkpoint, kind="gpt", max_inputs=16, max_n=10,
                 qualities=GPT_QUALITIES, default_quality="max", complexities=complexities)


# 정해진 비율로만 그리는 모델(Gemini 계열)의 비율 목록과 해상도 단계. RATIO_SIZES 는 1K 단계의 예상 출력 크기(서버 모델 목록의
# 해상도 표) — 2K·4K 는 그 2배·4배다. 받을 그림의 비율이 이름의 비율과 조금 다르므로(21:9 는 1584×672 = 2.357) 크롭의 비율은
# 이름이 아니라 이 표의 가로/세로와 맞춘다(받은 그림을 크롭에 놓을 때 늘어나지 않게).
# 이 표는 생성 조건 키의 재료다(작업의 size 에 들어간다) — 값을 고치면 이 계열의 모든 키가 바뀌어 같은 요청이 다시 과금 대상이
# 된다. 실제 출력이 표와 다르다는 것을 알게 되면 이 표를 고치지 말고 판정용 표를 따로 둔다.
RATIOS = ("1:1", "4:5", "3:4", "2:3", "9:16", "5:4", "4:3", "3:2", "16:9", "21:9")
RATIO_SIZES = {"1:1": (1024, 1024), "4:5": (928, 1152), "3:4": (896, 1200), "2:3": (848, 1264), "9:16": (768, 1376),
               "5:4": (1152, 928), "4:3": (1200, 896), "3:2": (1264, 848), "16:9": (1376, 768), "21:9": (1584, 672)}
IMAGE_SIZES = {"1K": 1, "2K": 2, "4K": 4}
RATIO_OPT = "aspect_ratio"  # 비율이 맞은 크롭의 작업 opts 에 들어가는 이름(고르는 값이 아니라 크롭에서 정해진다)


def _gemini(name: str, label: str, checkpoint: str, thinking: tuple = (), thinking_default: str | None = None) -> Model:
    opts, defaults = {"image_size": tuple(IMAGE_SIZES)}, {"image_size": "2K"}
    if thinking:
        opts["thinking_level"], defaults["thinking_level"] = thinking, thinking_default
    return Model(name=name, label=label, checkpoint=checkpoint, kind="gemini", max_inputs=14, max_n=PROVIDER_CALL_LIMIT,
                 fan_out=True, family="nano-banana", opts=opts, opt_defaults=defaults, ratios=RATIOS)


# 순서 = 화면 선택지의 순서. 앞의 넷은 로컬판의 모델 목록(pipeline._MODELS)과 같은 순서다.
MODELS: dict[str, Model] = {m.name: m for m in (
    _gpt("gpt-image-2.6-sunburst", "gpt_image_26_sunburst", COMPLEXITIES),
    _gpt("gpt-image-2.6-flare", "gpt_image_26_flare", COMPLEXITIES),
    _gpt("gpt-image-2.5-sunburst", "gpt_image_25_sunburst"),
    _gpt("gpt-image-2.5-flare", "gpt_image_25_flare"),
    Model(name="seedream-5.5", label="Seedream 5.5", checkpoint="seedream_5_5", kind="seedream", max_inputs=10,
          max_n=PROVIDER_CALL_LIMIT, fan_out=True, area=(921_600, 4_624_220), max_ratio=16, min_side=512),
    Model(name="ideogram-4.5", label="Ideogram 4.5", checkpoint="ideogram_4_5", kind="ideogram", max_inputs=5, max_n=8,
          qualities=("low", "medium", "high"), default_quality="high", prompt_used=True, area=(1, 4_194_304)),
    # 나노바나나 계열 — 순서가 계열 안의 우선순위다(2.1 → 2 → 프로)
    _gemini("nano-banana-2.1", "나노바나나 2.1", "gemini-nano-banana-2.1", ("minimal", "medium", "high"), "medium"),
    _gemini("nano-banana-2", "나노바나나 2", "gemini_flash2", ("minimal", "high"), "minimal"),
    _gemini("nano-banana-pro", "나노바나나 프로", "gemini3"),
)}
LOCAL_MODELS = tuple(name for name, m in MODELS.items() if m.kind == "gpt")   # 로컬판(ComfyUI 탭)이 생성하는 모델
FORGE_ONLY = tuple(name for name, m in MODELS.items() if m.kind != "gpt")     # 서버 모드에서만 생성하는 모델


# 프롬프트 작성 지침(UX v2-A D526): 생성 서버에 그 모델의 지침 프로필이 없으면 같은 계열의 이 checkpoint 로 지침을 받는다.
# 표에 없는 모델은 자기 checkpoint 그대로. 생성 요청의 checkpoint 와는 무관하다(지침 글을 고르는 데만 쓴다).
GUIDANCE_CHECKPOINTS = {"gpt_image_26_sunburst": "gpt_image_25_sunburst", "gpt_image_26_flare": "gpt_image_25_flare"}


def guidance_checkpoint(name) -> str | None:
    """프롬프트 작성 지침을 받을 서버 id(모르는 모델 None)."""
    m = MODELS.get(name) if isinstance(name, str) else None
    return GUIDANCE_CHECKPOINTS.get(m.checkpoint, m.checkpoint) if m else None


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


def job_opts(name, opts=None, *, src=None) -> dict | None:
    """작업(jobs[].opts)·생성 조건 키에 들어갈 모델 전용 옵션 — 표에 opts 가 없는 모델은 None(키에 아무것도 더하지 않는다).
    있는 모델은 받은 값에서 표의 이름만 남기고, 빠졌거나 그 모델의 선택지에 없는 값은 그 모델의 기본값으로 채운다.
    정해진 비율로만 그리는 모델은 src = 크롭 (w, h) 의 비율이 목록과 맞을 때 RATIO_OPT 에 그 비율을 더한다 — src 가 없거나
    맞는 비율이 없으면 그 이름을 넣지 않는다(그 작업은 opts_problem 이 「발사 못 함」으로 막는다)."""
    m = get(name)
    if m is None or not m.opts:
        return None
    given = opts if isinstance(opts, dict) else {}
    out = {key: (given[key] if isinstance(given.get(key), str) and given[key] in choices else m.opt_defaults.get(key, choices[0]))
           for key, choices in sorted(m.opts.items())}
    if m.ratios and src is not None:
        ratio = match_ratio(name, *src)
        if ratio is not None:
            out[RATIO_OPT] = ratio
    return out


def opts_problem(name, opts) -> str | None:
    """작업의 opts 가 그 모델이 받는 꼴인가 → 안내 문구(받으면 None). 옵션이 없는 모델은 opts 가 없어야 하고, 있는 모델은 표의
    이름이 모두 있고 값이 선택지 안이어야 한다. 정해진 비율로만 그리는 모델은 RATIO_OPT 가 목록의 비율이어야 한다 — 없으면
    크롭의 비율이 맞지 않는 것이다(ratio_problem 의 문구)."""
    m = get(name)
    if m is None:
        return f"모르는 생성 모델입니다: {name!r}"
    bad = "생성 옵션 기록이 잘못되었습니다 — 반영을 다시 실행해 주세요."
    if not m.opts:
        return bad if opts else None
    if not isinstance(opts, dict) or any(not isinstance(opts.get(key), str) or opts[key] not in choices
                                         for key, choices in m.opts.items()):
        return bad
    allowed = set(m.opts) | ({RATIO_OPT} if m.ratios else set())
    if set(opts) - allowed:
        return bad
    if m.ratios:
        if RATIO_OPT not in opts:
            return ratio_problem(name)
        if not isinstance(opts[RATIO_OPT], str) or opts[RATIO_OPT] not in m.ratios:
            return bad
    return None


def _ratio(label: str) -> float:
    """그 비율로 받을 그림의 가로/세로(예상 출력 크기의 비율 — 이름의 비율과 1% 남짓까지 다르다)."""
    w, h = RATIO_SIZES[label]
    return w / h


def match_ratio(name, w, h) -> str | None:
    """크롭 w×h 의 가로세로비가 그 모델의 비율 목록 가운데 하나와 RATIO_TOLERANCE 안에서 맞으면 그 비율("4:5"), 아니면 None.
    판정식은 abs((w / h) / (예상 가로 / 예상 세로) - 1) <= RATIO_TOLERANCE, 예상 가로·세로 = RATIO_SIZES[비율] —
    화면(layout_geom.js)도 같은 식과 같은 표(env 의 ratio_sizes)를 쓴다."""
    m = get(name)
    if m is None or not m.ratios or type(w) is not int or type(h) is not int or w < 1 or h < 1:
        return None
    errors = [(abs((w / h) / _ratio(label) - 1.0), i, label) for i, label in enumerate(m.ratios)]
    err, _, label = min(errors)
    return label if err <= RATIO_TOLERANCE else None


def nearest_ratio(name, w, h) -> str | None:
    """그 모델의 비율 목록에서 크롭 w×h 와 가장 가까운 비율(로그 거리, 같으면 목록의 앞). 목록이 없는 모델은 None."""
    m = get(name)
    if m is None or not m.ratios or type(w) is not int or type(h) is not int or w < 1 or h < 1:
        return None
    return min((abs(math.log((w / h) / _ratio(label))), i, label) for i, label in enumerate(m.ratios))[2]


def ratio_problem(name) -> str:
    """크롭의 비율이 그 모델의 비율 목록에 없을 때의 안내 문구(비율 표기를 싣지 않는다 — 주소 꼴로 걸러지는 글자다)."""
    m = get(name)
    label = m.label if m is not None else str(name)
    return f"크롭의 가로세로비가 {label} 의 비율 목록에 없습니다 — ② 크롭에서 [모델 비율로 넓히기]로 맞춰 주세요."


def family_fallback(name, catalog) -> str | None:
    """그 모델이 서버 목록에 없을 때 같은 계열에서 대신 권할 모델 — 표의 순서로 첫 번째 「쓸 수 있는」 것. 계열이 없거나
    그 모델이 살아 있거나 대신할 것이 없으면 None."""
    m = get(name)
    if m is None or m.family is None or availability(name, catalog)[0]:
        return None
    for other in MODELS.values():
        if other.family == m.family and other.name != name and availability(other.name, catalog)[0]:
            return other.name
    return None


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


def _ratio_output_size(m: Model, w: int, h: int, rule: str, opts) -> tuple[int, int, dict]:
    """정해진 비율로만 그리는 모델의 예상 출력 크기 — 맞는 비율(없으면 가장 가까운 비율)의 1K 크기 × 해상도 단계.
    info: rule · src · method("ratio") · ratio(맞은 비율 | None) · nearest · image_size · scale · aspect_err · pixels · model · problem."""
    tier = job_opts(m.name, opts)["image_size"]
    ratio = match_ratio(m.name, w, h)
    near = ratio or nearest_ratio(m.name, w, h)
    W, H = (side * IMAGE_SIZES[tier] for side in RATIO_SIZES[near])
    aspect_err = (W / H) / (w / h) - 1.0
    info = {"rule": rule, "src": [w, h], "method": "ratio", "ratio": ratio, "nearest": near, "image_size": tier,
            "scale": [round(W / w, 6), round(H / h, 6)],
            "aspect_err": round(aspect_err, 6) if abs(aspect_err) > 1e-12 else 0.0, "pixels": W * H, "model": m.name}
    if ratio is None:
        info["problem"] = ratio_problem(m.name)
    return W, H, info


def output_size(name: str, w: int, h: int, rule: str = "user_k", opts=None) -> tuple[int, int, dict]:
    """크롭 w×h → 그 모델에 요청할 출력 크기 (W, H, info). GPT 계열은 다루지 않는다(store.gpt_output_size 를 쓴다).

    기준 배율(base_scale)을 곱한 뒤 모델의 화소 수 창 안으로 같은 비율로 줄이거나 키우고, 짧은 변 하한을 맞추고, 짝수로 맞춘다.
    짝수로 맞출 때는 창 안에 드는 후보(변마다 내림·올림, 한 변을 정하고 다른 변을 비율로 다시 구한 것) 가운데 비율 오차가 가장 작은 것을 고른다.
    크롭의 비율이 모델의 한도를 넘으면 창을 맞출 수 없다 — 그때도 가장 가까운 크기를 돌려주고 info["problem"] 에 이유를 적는다
    (작업은 만들어지고, 생성 직전 판정(size_problem)이 「발사 못 함」으로 막는다). 아주 가는 크롭이라 비율이 RATIO_TOLERANCE 넘게
    달라질 때도 info["problem"] 에 적는다(이때는 막히지 않는다 — 반영 보고의 경고로만 보인다).
    info: rule · src · method("k" | "fit") · k · k_str · scale[x, y] · aspect_err · pixels · model · problem(있을 때만).
    정해진 비율로만 그리는 모델(ratios)은 크기를 고르지 않는다 — opts 의 해상도 단계와 크롭의 비율로 예상 크기를 돌려준다
    (_ratio_output_size, 크기 규칙은 이름만 검사한다). 비율이 맞지 않으면 가장 가까운 비율의 크기와 info["problem"]."""
    m = get(name)
    if m is None or m.kind == "gpt" or (m.area is None and not m.ratios):
        raise ValueError(f"이 모델의 출력 크기는 여기서 정하지 않습니다: {name!r}")
    if type(w) is not int or type(h) is not int or w < 1 or h < 1:
        raise ValueError(f"크롭 크기가 잘못되었습니다: {w!r}x{h!r}")
    if m.ratios:
        if rule not in SIZE_RULES:
            raise ValueError(f"알 수 없는 출력 크기 규칙: {rule!r} (가능: {', '.join(SIZE_RULES)})")
        return _ratio_output_size(m, w, h, rule, opts)
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


def size_problem(name, size, opts=None) -> str | None:
    """요청 크기가 그 모델의 창 안인가 → 안내 문구(안이면 None). GPT 계열은 여기서 보지 않는다(None).
    정해진 비율로만 그리는 모델은 예상 크기 표(RATIO_SIZES × 해상도 단계)의 값이어야 한다 — 작업의 opts 를 주면 그 opts 의
    비율·해상도 단계가 정하는 예상 크기와 같은지까지 본다(크기 기록과 실제로 보낼 옵션이 어긋난 작업을 막는다)."""
    m = get(name)
    if m is None or m.kind == "gpt" or (m.area is None and not m.ratios):
        return None
    try:
        W, H = size
    except (TypeError, ValueError):
        return "요청 크기 기록이 잘못되었습니다 — 반영을 다시 실행해 주세요."
    if type(W) is not int or type(H) is not int or W < 1 or H < 1:
        return "요청 크기 기록이 잘못되었습니다 — 반영을 다시 실행해 주세요."
    if m.ratios:
        known = {(a * k, b * k) for a, b in (RATIO_SIZES[label] for label in m.ratios) for k in IMAGE_SIZES.values()}
        if isinstance(opts, dict):
            ratio, tier = opts.get(RATIO_OPT), opts.get("image_size")
            if isinstance(ratio, str) and ratio in m.ratios and isinstance(tier, str) and tier in IMAGE_SIZES:
                known = {tuple(side * IMAGE_SIZES[tier] for side in RATIO_SIZES[ratio])}
        return None if (W, H) in known else "요청 크기 기록이 잘못되었습니다 — 반영을 다시 실행해 주세요."
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
    max_inputs 는 표의 값이다. opt_defaults = 옵션의 기본값, ratios = 비율 목록(없으면 []), ratio_sizes = 비율마다 예상 출력 크기
    [가로, 세로](비율 맞춤의 기준 — 없으면 {}), ratio_tolerance = 비율 맞춤의 허용 오차(비율 목록이 없으면 None), family = 계열 이름,
    fallback = 목록에서 빠진 모델 대신 권할 같은 계열 모델의 이름(없으면 None).
    max_refs = 레퍼런스로 붙일 수 있는 그림 수(max_inputs 에서 타깃 크롭 한 장을 뺀 수), supports_quality = 품질을 고르는 모델인가,
    complexities = 복잡도 선택지(없는 모델은 []) — 화면이 모델 이름을 비교하지 않고 이 값으로 칸을 연다(UX v2-B D554)."""
    m = MODELS[name]
    ok, why, limit = availability(name, catalog) if checked else (True, None, m.max_inputs)
    limit = limit if limit is not None else m.max_inputs
    return {"name": m.name, "label": m.label, "kind": m.kind, "available": ok, "why": why,
            "qualities": list(m.qualities), "default_quality": m.default_quality if m.qualities else None,
            "opts": {key: list(values) for key, values in sorted(m.opts.items())},
            "opt_defaults": {key: m.opt_defaults.get(key, values[0]) for key, values in sorted(m.opts.items())},
            "ratios": list(m.ratios), "ratio_sizes": {label: list(RATIO_SIZES[label]) for label in m.ratios},
            "ratio_tolerance": RATIO_TOLERANCE if m.ratios else None, "family": m.family,
            "fallback": family_fallback(name, catalog) if checked else None,
            "max_inputs": limit, "max_n": m.max_n, "fan_out": m.fan_out,
            "max_refs": max(0, limit - 1), "supports_quality": bool(m.qualities), "complexities": list(m.complexities)}


# ══════════════════════════════════════════════════════════════════════
# 패치별 생성 설정(UX v2-B) — 항목 만들기·검사·효과 값
# ══════════════════════════════════════════════════════════════════════
class GenError(ValueError):
    """패치별 생성 설정의 값 오류. key = 틀린 칸의 이름(요청의 이름 그대로 — model_id·quality·opts·complexity·count, 읽은 항목이면 entry)."""

    def __init__(self, key: str, message: str):
        super().__init__(message)
        self.key = key


def gen_bundle(model_id, quality=None, opts=None, complexity=None) -> dict:
    """저장할 모델 묶음 {"model", "quality"?, "opts"?, "complexity"?} — 그 모델에 필요한 칸을 전부 채운다(안 준 칸은 그 모델의
    기본값: 품질 = default_quality, 옵션 = opt_defaults, 복잡도 = DEFAULT_COMPLEXITY). 그 모델에 없는 칸을 주었거나 값이 선택지
    밖이면 GenError(틀린 칸). 비율(RATIO_OPT)은 고르는 값이 아니라 크롭에서 정해지므로 받지 않는다."""
    m = get(model_id)
    if m is None:
        raise GenError("model_id", f"모르는 생성 모델입니다: {model_id!r}")
    out: dict = {"model": m.name}
    if quality is not None and not m.qualities:
        raise GenError("quality", f"{m.label} 은 품질을 고르지 않는 모델입니다")
    if m.qualities:
        if quality is None:
            quality = m.default_quality
        if not isinstance(quality, str) or quality not in m.qualities:
            raise GenError("quality", f"{m.label} 의 품질은 {' · '.join(m.qualities)} 가운데 하나여야 합니다: {quality!r}")
        out["quality"] = quality
    if opts is not None and not isinstance(opts, dict):
        raise GenError("opts", "opts 는 객체여야 합니다")
    given = opts or {}
    if RATIO_OPT in given:
        raise GenError("opts", "가로세로비는 고르는 값이 아닙니다 — 크롭의 비율에서 정해집니다")
    extra = sorted(str(k) for k in set(given) - set(m.opts))
    if extra:
        raise GenError("opts", f"{m.label} 에 없는 옵션입니다: {', '.join(extra)}")
    if m.opts:
        filled = {}
        for key, choices in sorted(m.opts.items()):
            value = given.get(key, m.opt_defaults.get(key, choices[0]))
            if not isinstance(value, str) or value not in choices:
                raise GenError("opts", f"{m.label} 의 {key} 는 {' · '.join(choices)} 가운데 하나여야 합니다: {value!r}")
            filled[key] = value
        out["opts"] = filled
    if complexity is not None and not m.complexities:
        raise GenError("complexity", f"{m.label} 은 복잡도가 없는 모델입니다")
    if m.complexities:
        if complexity is None:
            complexity = DEFAULT_COMPLEXITY
        if not isinstance(complexity, str) or complexity not in m.complexities:
            raise GenError("complexity", f"복잡도는 {' · '.join(m.complexities)} 가운데 하나여야 합니다: {complexity!r}")
        out["complexity"] = complexity
    return out


def count_problem(model, count) -> str | None:
    """장 수 덮어쓰기 값이 그 모델(이름)의 범위 1…max_n 안인가 → 안내 문구(받으면 None). 모르는 모델이면 표의 가장 큰 상한으로 본다."""
    m = get(model)
    limit = m.max_n if m is not None else max(x.max_n for x in MODELS.values())
    if type(count) is not int or not 1 <= count <= limit:
        return f"장 수는 1–{limit} 사이의 정수여야 합니다" + (f"({m.label})" if m is not None else "") + f": {count!r}"
    return None


def gen_entry_problem(entry) -> str | None:
    """읽은 항목(crop_gen.json 의 한 크롭)이 저장 꼴인가 → 문제 문구(맞으면 None). 저장 꼴 = 키는 GEN_KEYS 안, 모델 묶음과 장 수
    가운데 하나는 있고, 모델 묶음은 gen_bundle 이 만든 것과 같으며(필요한 칸 전부·없는 칸 없음), n 은 1 이상 표의 가장 큰 상한
    이하의 정수. n 이 그 모델의 상한을 넘는 것은 문제가 아니다(저장 뒤 모델이 바뀐 경우 — effective_gen 이 낮춘다)."""
    if not isinstance(entry, dict) or not entry:
        return "항목이 객체가 아니거나 비어 있습니다"
    extra = sorted(str(k) for k in set(entry) - set(GEN_KEYS))
    if extra:
        return f"모르는 키: {', '.join(extra)}"
    if "model" not in entry:
        if set(entry) != {"n"}:
            return "모델 없이 품질·옵션·복잡도만 적을 수 없습니다"
    else:
        try:
            bundle = gen_bundle(entry["model"], entry.get("quality"), entry.get("opts"), entry.get("complexity"))
        except GenError as e:
            return str(e)
        if bundle != {k: v for k, v in entry.items() if k != "n"}:
            return "모델 묶음에 빠진 칸이 있습니다"
    if "n" in entry and count_problem(None, entry["n"]):
        return f"장 수가 잘못되었습니다: {entry['n']!r}"
    return None


def effective_gen(prepare: dict, entry=None) -> dict:
    """그 패치의 「효과 값」 — 프로젝트 실행 설정(prepare: model·quality·n·complexity·image_size·thinking_level)에 패치별 생성 설정
    항목(entry — 없으면 None)을 얹은 값. Prepare·스펙 보기·AI 작성·전환 미리 보기·목록이 모두 이 함수 하나만 쓴다(D531).

      {"model", "quality", "opts", "complexity", "n", "inherit": {"model": bool, "n": bool}, "clamped": int | None}

      model       항목에 모델 묶음이 있으면 그 모델, 없으면 prepare["model"]
      quality     작업(jobs[].quality)·생성 조건 키에 들어갈 값 — 프로젝트 값을 따르면 job_quality(model, prepare["quality"]),
                  모델 묶음이면 그 묶음의 quality(품질을 고르지 않는 모델은 FIXED_QUALITY)
      opts        job_opts(model, …)(비율 없이 — 비율은 Prepare 가 크롭 크기로 더한다). 옵션이 없는 모델은 None
      complexity  프로젝트 값을 따르면 prepare["complexity"] 그대로(2.6 이 아니어도 — 쓰는 쪽이 모델로 가린다), 모델 묶음이면
                  그 묶음의 complexity(복잡도가 없는 모델은 None)
      n           항목에 n 이 있으면 그 값, 없으면 int(prepare["n"]). 항목이 있는 패치(모델 묶음이든 장 수든)는 효과 모델의
                  상한(max_n)으로 낮추고 낮추기 전 값을 clamped 에 적는다(D535). 항목이 없는 패치는 낮추지 않는다(지금 그대로)
      inherit     프로젝트 값을 따르는가(모델 묶음 · 장 수)

    entry 가 None 이면 Prepare 가 지금까지 쓰던 값과 글자 그대로 같다(D533 — 기존 프로젝트의 작업·생성 조건 키 불변).
    entry 가 저장 꼴이 아니면 GenError("entry", …) — 쓰는 쪽이 그 패치만 「작업 없음」으로 남긴다(D537)."""
    if entry is not None:
        problem = gen_entry_problem(entry)
        if problem:
            raise GenError("entry", f"{GEN_BROKEN}({problem})")
    entry = entry or {}
    own = "model" in entry
    if own:
        model = entry["model"]
        quality = entry["quality"] if MODELS[model].qualities else FIXED_QUALITY
        opts = job_opts(model, entry.get("opts"))
        complexity = entry.get("complexity")
    else:
        model = prepare["model"]
        quality = job_quality(model, prepare["quality"])
        opts = job_opts(model, {"image_size": prepare.get("image_size"), "thinking_level": prepare.get("thinking_level")})
        complexity = prepare.get("complexity")
    n, clamped = (entry["n"] if "n" in entry else int(prepare["n"])), None
    m = get(model)
    if entry and m is not None and n > m.max_n:
        n, clamped = m.max_n, n
    return {"model": model, "quality": quality, "opts": opts, "complexity": complexity, "n": n,
            "inherit": {"model": not own, "n": "n" not in entry}, "clamped": clamped}
