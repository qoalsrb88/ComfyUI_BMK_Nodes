"""BMK NAI Resolution — NovelAI 워크플로우의 해상도를 계산하는 단일 진실 공급원.

배경
────
ComfyUI_JPS-Nodes 의 SDXL Resolutions 는 "해상도 하나 = 드롭다운 항목 하나" 구조다.
NovelAI 쪽으로 옮기면 항목이 29개가 되어(1MP 계열 9 + ×1.25 계열 9 + ×1.50 계열 9 +
wallpaper 2) 드롭다운이 화면을 덮고, 노드를 가로로 줄이면 라벨이 잘려 어떤 값인지
알 수 없게 된다.

이 노드는 그 29개 조합을 **세 개의 축**으로 분해한다.

    ratio (5)  ×  orientation (2)  ×  scale (5)   →  11개 항목으로 29개 조합 커버

가장 잦은 조작인 "비율 유지한 채 세로↔가로 뒤집기"와 "구도 유지한 채 배율만 올리기"가
각각 ◀▶ 1클릭으로 끝난다. 드롭다운을 아예 열지 않게 되는 것이 항목 수 감소보다 큰 이득.

해상도 테이블은 하드코딩하지 않는다
──────────────────────────────────
9개 base 해상도와 배율만 상수로 두고 나머지는 런타임 계산한다. 실제로 NovelAI 가
쓰는 27개 값은 예외 없이 아래 한 줄로 재현된다.

    snapped = floor(base_dim * factor / 64 + 0.5) * 64

★ 파이썬 내장 round() 를 쓰면 안 된다. banker's rounding 이라 12.5→12, 22.5→22,
  28.5→28 로 떨어져서 832→768, 1472→1408, 1856→1792 로 세 항목이 어긋난다.
  반드시 floor(x + 0.5) 를 쓸 것. (타일 계산 때의 "round 대신 ceil" 원칙과는 다른
  맥락이다. 여기는 임계값을 넘겨야 하는 제약이 없고 AR 왜곡 최소화가 목적이라
  half-up round 가 맞다.)

  참고: snap_mode 가 nearest64 든 preserve_ratio 든 ×1.00 / ×1.25 / ×1.50 계열
  27개는 동일한 값이 나온다. ×1.69 계열은 4:3 에서만 갈린다 (1536×1920 vs
  1472×1920 — 후자가 base AR 에 더 가깝다). 그 외에 두 모드가 갈라지는 건
  custom 과 i2i 경로뿐이다.

Anlas 뱃지
──────────
NAID 의 generate() 안에서 limit_opus_free() 가 돈다. 계산해 보면 ×1.00 계열 9개가
전부 1,048,576px 이하이고(최대 1024×1024 = 정확히 경계), ×1.25 이상은 전부 초과다.
즉 scale 위젯이 그대로 "무료 / Anlas 소모" 축이 된다. readout 에 뱃지로 띄워
실수로 Anlas 를 태우는 일을 막는다. 정책이 바뀌면 _OPUS_FREE_PIXELS 한 줄만 고친다.

요청값 ≠ 확정값
───────────────
이 노드가 내보낸 width/height 가 NAI 로 실제 전송된 값이라는 보장이 없다. NAID 는
calculate_resolution / limit_opus_free 를 자기 안에서 다시 돌리므로, opus-free 옵션이
켜진 채 ×1.25 를 보내면 조용히 1MP 로 깎여 돌아온다.

그래서 GenerateNAID._post_image 에 **읽기 전용** 후킹을 걸어 확정 파라미터의
width/height 를 최근 요청 목록과 대조하고, 어긋나면 경고 로그만 남긴다. 값을
고치지 않으므로 bmk_nai_autosave 의 패치와 설치 순서에 관계없이 공존한다.

  주의: bmk_nai_autosave._validate() 가 inspect.signature(_post_image) 로 앞 5개
  인자 이름을 검사한다. 래퍼에 functools.wraps 를 걸지 않으면 시그니처가 (*args,)
  로 보여서 autosave 패치가 설치를 거부한다. wraps 는 __wrapped__ 를 남기고
  inspect.signature 가 이를 따라가므로 반드시 유지할 것.

i2i / 업스케일
──────────────
i2i 에서 진실은 소스 이미지의 실측 해상도이고 프리셋은 참고값이다. NAID 의
Img2ImgOption 은 소스를 목표 w/h 로 리사이즈해 보내므로, AR 이 안 맞으면 크롭이
아니라 스트레치가 난다. 사고가 조용히 난다.

  t2i               프리셋만. 소스 입력 무시
  i2i: scale source 소스 실측 × upscale → 64 스냅. AR 최대 보존 (업스케일 기본값)
  i2i: fit preset   소스 AR 에 가장 가까운 프리셋 자동 선택 후 scale 적용
  auto              is_i2i 입력(또는 소스 연결 여부)으로 위를 자동 선택

소스는 IMAGE 텐서보다 source_width / source_height INT 입력을 우선한다. NAI Extract
(novelai_metadata.py) 가 이미 width/height 를 내보내므로 그대로 체인되고, 텐서를
물리지 않아 캐시 시그니처도 가볍다. IMAGE 는 폴백.

scale_by 출력이 실질적 이득의 핵심이다. 스냅 **후** 실제 적용된 배율을 내보내면
소스 리사이즈 노드와 GenerateNAID 가 같은 숫자를 공유해서, 재계산으로 인한 어긋남이
원천 차단된다. BMKScaleToTarget 과 같은 원칙 — 계산은 한 곳에서만.

위젯 순서 규약 (중요)
─────────────────────
위젯 순서는 저장된 워크플로의 widgets_values 위치 배열과 1:1 대응한다. 신규 위젯은
반드시 required 맨 끝에만 추가(append-only). 중간 삽입 시 기존 워크플로가 조용히
깨진다. 출력도 동일 — RETURN_TYPES 는 끝에만 추가한다.

짝 JS: js/bmk_nai_resolution.js
  - 계산 결과 readout 위젯 (커스텀 draw, Anlas 뱃지 색상 구분)
  - 모드별 무관 위젯 비활성화(회색 처리)
  - 테이블은 JS 에 복제하지 않고 /bmk/nai_resolution/table 로 받아간다.
    _RATIOS / _SCALES / _OPUS_FREE_PIXELS 의 단일 진실 공급원은 이 파일이다.

버전 이력
─────────
v1 (2026-08): 최초. ratio/orientation/scale 3축 분리, FHD·custom 스케일,
              t2i/i2i 3모드 + auto, half-up 64 스냅, snap_mode 3종,
              clamp_max_mp, Anlas 무료 뱃지, _post_image 읽기전용 대조 후킹,
              테이블 HTTP 라우트 노출.
v2 (2026-08): ×1.69 계열 추가. 상한(1728² = 2.99MP) 바로 아래를 노리는 계열로,
              계수는 1.6875 다. ×1.75 로 하면 다섯 비율이 전부 상한을 넘는다.
              _NAI_MAX_PIXELS 상수 도입 — 초과 시 info 에 주의만 붙인다.
"""

from __future__ import annotations

import functools
import logging
import math
import threading
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_TAG = "[ComfyUI_BMK_Nodes::NAIResolution]"

# JS 와 공유하는 테이블 스키마 버전. _RATIOS / _SCALES 의 의미가 바뀌면 올린다.
TABLE_VERSION = 1


# ─── 해상도 테이블 (단일 진실 공급원) ─────────────────────────────

# (라벨, 짧은 변, 긴 변). 라벨은 긴 변 기준으로 통일한다 — 방향은 orientation
# 위젯이 결정하므로 3:4 / 4:3 을 따로 두면 축이 중복된다.
_RATIOS: Tuple[Tuple[str, int, int], ...] = (
    ("1:1", 1024, 1024),
    ("4:3", 896, 1152),
    ("3:2", 832, 1216),
    ("16:9", 768, 1344),
    ("21:9", 640, 1536),
)

_RATIO_KEYS = [r[0] for r in _RATIOS]
_RATIO_BASE: Dict[str, Tuple[int, int]] = {r[0]: (r[1], r[2]) for r in _RATIOS}

# 배율 프리셋. FHD / custom 은 배율이 아니라 목표 크기 지정이므로 따로 뺀다.
_SCALE_FACTORS: Dict[str, float] = {
    "\u00d71.00": 1.00,
    "\u00d71.25": 1.25,
    "\u00d71.50": 1.50,
    # 라벨은 ×1.69 지만 실제 계수는 1.6875 (= 27/16) 다. ×1.75 로 잡으면 다섯
    # 비율 전부가 NAI 상한을 넘는다 (1:1 이 1792² = 3.21MP). 1.6875 는
    #   1:1 → 1728×1728 (= 상한 그 자체)
    #   3:2 → 1408×2048
    # 로 정확히 떨어지면서 모든 비율이 상한 아래에 남는 최대 계열이다.
    # 라벨을 ×1.75 로 보고 싶으면 키만 바꾸면 된다 — JS 는 값(계수)을 쓴다.
    "\u00d71.69": 1.6875,
}
_SCALE_FHD = "FHD"
_SCALE_CUSTOM = "custom"
_SCALE_KEYS = list(_SCALE_FACTORS.keys()) + [_SCALE_FHD, _SCALE_CUSTOM]

# wallpaper 예외. 본질적으로 "특정 목표 크기"이므로 ratio 축이 아니라 scale 축에
# 속한다. orientation 이 방향을 결정하고 ratio 는 무시된다.
_FHD_SHORT, _FHD_LONG = 1088, 1920

ORIENT_PORTRAIT = "portrait"
ORIENT_LANDSCAPE = "landscape"
_ORIENT_KEYS = [ORIENT_PORTRAIT, ORIENT_LANDSCAPE]

MODE_AUTO = "auto"
MODE_T2I = "t2i"
MODE_I2I_SCALE = "i2i: scale source"
MODE_I2I_FIT = "i2i: fit preset"
_MODE_KEYS = [MODE_AUTO, MODE_T2I, MODE_I2I_SCALE, MODE_I2I_FIT]

SNAP_NEAREST = "nearest64"
SNAP_PRESERVE = "preserve_ratio"
SNAP_FLOOR = "floor64"
_SNAP_KEYS = [SNAP_NEAREST, SNAP_PRESERVE, SNAP_FLOOR]

_SNAP_UNIT = 64

# Opus 무료 생성 한도. NAID 의 limit_opus_free() 기준값과 같다. 정책이 바뀌면
# 여기 한 줄만 고치면 노드와 JS readout 이 동시에 따라간다.
_OPUS_FREE_PIXELS = 1024 * 1024

# NAI 가 받아주는 최대 픽셀 수. 1728² = 2,985,984 로 관측됐다. 공식 문서 수치가
# 아니라 실제로 통과하는 최대 해상도(1728×1728 / 1408×2048)에서 역산한 값이므로,
# 정책이 바뀌면 여기만 고친다.
#
# 이 값으로 클램프하지는 않는다 — 클램프는 clamp_max_mp 위젯의 몫이고, 여기서는
# info 에 주의만 붙인다. 관측값이 틀렸을 때 조용히 해상도가 깎이는 것보다
# 경고만 뜨고 실제 요청은 그대로 나가는 편이 진단하기 쉽다.
_NAI_MAX_PIXELS = 1728 * 1728

BADGE_FREE = "free"
BADGE_ANLAS = "anlas"


# ─── 스냅 ────────────────────────────────────────────────────────

def _snap(value: float, rounding: str = "nearest") -> int:
    """64 배수로 스냅한다.

    rounding="nearest" 는 half-up 이다. 파이썬 round() 는 banker's rounding 이라
    NovelAI 표를 재현하지 못한다 (모듈 docstring 참고).
    """
    if value <= 0:
        return _SNAP_UNIT
    if rounding == "floor":
        n = math.floor(value / _SNAP_UNIT)
    else:
        n = math.floor(value / _SNAP_UNIT + 0.5)
    return max(_SNAP_UNIT, int(n) * _SNAP_UNIT)


def _rounding_of(snap_mode: str) -> str:
    return "floor" if snap_mode == SNAP_FLOOR else "nearest"


def _scale_pair(width: int, height: int, factor: float, snap_mode: str) -> Tuple[int, int]:
    """(w, h) 를 factor 배 하고 64 스냅한다.

    preserve_ratio 는 긴 변만 스냅하고 짧은 변을 원본 AR 로 되계산한다. 프리셋
    27개에 대해서는 nearest64 와 결과가 같고, custom / i2i 경로에서만 갈라진다.
    """
    rounding = _rounding_of(snap_mode)

    if snap_mode == SNAP_PRESERVE:
        if width >= height:
            out_w = _snap(width * factor, rounding)
            out_h = _snap(out_w * height / width, rounding)
        else:
            out_h = _snap(height * factor, rounding)
            out_w = _snap(out_h * width / height, rounding)
        return out_w, out_h

    return _snap(width * factor, rounding), _snap(height * factor, rounding)


def _orient(short_side: int, long_side: int, orientation: str) -> Tuple[int, int]:
    """(짧은 변, 긴 변) 을 방향에 맞춰 (w, h) 로 배치한다."""
    if orientation == ORIENT_LANDSCAPE:
        return long_side, short_side
    return short_side, long_side


def _preset_dims(
    ratio_key: str,
    orientation: str,
    scale_key: str,
    custom_long_side: int,
    snap_mode: str,
) -> Tuple[int, int]:
    """프리셋 축(ratio × orientation × scale)에서 목표 해상도를 만든다."""
    rounding = _rounding_of(snap_mode)
    short_base, long_base = _RATIO_BASE.get(ratio_key, _RATIO_BASE["1:1"])

    if scale_key == _SCALE_FHD:
        # ratio 는 무시된다 — wallpaper 는 고정 크기다.
        return _orient(_FHD_SHORT, _FHD_LONG, orientation)

    if scale_key == _SCALE_CUSTOM:
        out_long = _snap(custom_long_side, rounding)
        out_short = _snap(out_long * short_base / long_base, rounding)
        return _orient(out_short, out_long, orientation)

    factor = _SCALE_FACTORS.get(scale_key, 1.0)
    out_short = _snap(short_base * factor, rounding)
    out_long = _snap(long_base * factor, rounding)
    if snap_mode == SNAP_PRESERVE:
        out_long = _snap(long_base * factor, rounding)
        out_short = _snap(out_long * short_base / long_base, rounding)
    return _orient(out_short, out_long, orientation)


def _pick_ratio(src_w: int, src_h: int) -> Tuple[str, str]:
    """소스 AR 에 가장 가까운 (ratio_key, orientation) 을 고른다.

    거리는 log 공간에서 잰다. 선형 차이를 쓰면 21:9 쪽이 과대평가되어 세로로
    긴 소스가 항상 극단 비율로 끌려간다.
    """
    orientation = ORIENT_PORTRAIT if src_h >= src_w else ORIENT_LANDSCAPE
    long_s, short_s = (max(src_w, src_h), min(src_w, src_h))
    target = math.log(long_s / short_s) if short_s > 0 else 0.0

    best_key = _RATIO_KEYS[0]
    best_dist = None
    for key, short_b, long_b in _RATIOS:
        dist = abs(math.log(long_b / short_b) - target)
        if best_dist is None or dist < best_dist:
            best_dist, best_key = dist, key
    return best_key, orientation


def _badge(width: int, height: int) -> str:
    return BADGE_FREE if width * height <= _OPUS_FREE_PIXELS else BADGE_ANLAS


def _readout(width: int, height: int) -> str:
    mp = width * height / 1_000_000.0
    return f"{width} \u00d7 {height} \u00b7 {mp:.2f}MP \u00b7 {_badge(width, height)}"


# ─── 요청/확정 대조 후킹 ──────────────────────────────────────────
#
# 이 노드는 그래프상 GenerateNAID 보다 항상 먼저 실행되므로, 실행 시점에 요청값을
# 기록해 두고 _post_image 후킹이 확정값과 대조한다. 노드가 여러 개여도 되도록
# "최근 요청 집합에 확정값이 들어있는가" 로만 판정한다 — 오탐을 내느니 놓치는
# 쪽이 낫다.

_pending_lock = threading.Lock()
_pending: Deque[Dict[str, Any]] = deque(maxlen=16)

_hook_installed = False
_hook_refused = False


def _record_request(width: int, height: int, label: str) -> None:
    with _pending_lock:
        _pending.append({"w": int(width), "h": int(height), "label": label, "t": time.time()})


def _check_confirmed(parameters: Any) -> None:
    """NAI 로 전송된 확정 파라미터를 최근 요청과 대조한다. 로그만 남긴다."""
    try:
        if not isinstance(parameters, dict):
            return
        conf_w = int(parameters.get("width") or 0)
        conf_h = int(parameters.get("height") or 0)
        if conf_w <= 0 or conf_h <= 0:
            return

        with _pending_lock:
            entries = list(_pending)
        if not entries:
            return
        if any(e["w"] == conf_w and e["h"] == conf_h for e in entries):
            return

        recent = entries[-3:]
        asked = ", ".join(f"{e['w']}\u00d7{e['h']}({e['label']})" for e in recent)
        logger.warning(
            "%s \uc694\uccad %s \u2192 NAI \ud655\uc815 %d\u00d7%d. "
            "opus free \uc81c\ud55c(limit_opus_free) \ub610\ub294 "
            "calculate_resolution \ubcf4\uc815\uc774 \uac1c\uc785\ud588\uc744 \uc218 "
            "\uc788\uc2b5\ub2c8\ub2e4. \uc800\uc7a5\ub41c PNG \uba54\ud0c0\ub370\uc774\ud130\uc640 "
            "\ud30c\uc77c\uba85\uc740 \ud655\uc815\uac12 \uae30\uc900\uc785\ub2c8\ub2e4.",
            _TAG,
            asked,
            conf_w,
            conf_h,
        )
    except Exception:  # 진단 기능이 생성을 막아서는 안 된다
        logger.debug("%s \ud655\uc815\uac12 \ub300\uc870 \uc911 \uc608\uc678", _TAG, exc_info=True)


def _install_hook() -> bool:
    """GenerateNAID._post_image 에 읽기 전용 래퍼를 건다. 멱등."""
    global _hook_installed, _hook_refused

    if _hook_installed:
        return True
    if _hook_refused:
        return False

    try:
        import nodes as comfy_nodes  # ComfyUI 코어 레지스트리
    except Exception as exc:
        logger.debug("%s ComfyUI \ucf54\uc5b4 nodes \ubaa8\ub4c8\uc744 \uc5f4 \uc218 \uc5c6\uc2b5\ub2c8\ub2e4: %s", _TAG, exc)
        return False

    cls = getattr(comfy_nodes, "NODE_CLASS_MAPPINGS", {}).get("GenerateNAID")
    if cls is None:
        # 아직 로드 전일 수 있으므로 영구 실패로 못박지 않는다.
        return False

    original = getattr(cls, "_post_image", None)
    if not callable(original):
        _hook_refused = True
        logger.info(
            "%s GenerateNAID._post_image \ub97c \ucc3e\uc9c0 \ubabb\ud574 "
            "\ud655\uc815\uac12 \ub300\uc870\ub97c \uac74\ub108\ub701\ub2c8\ub2e4.",
            _TAG,
        )
        return False

    if getattr(original, "_bmk_res_hooked", False):
        _hook_installed = True
        return True

    # functools.wraps 필수 — bmk_nai_autosave._validate() 가
    # inspect.signature(_post_image) 로 앞 5개 인자 이름을 검사한다.
    @functools.wraps(original)
    def hooked(access_token, prompt, model, action, parameters, *args, **kwargs):
        _check_confirmed(parameters)
        return original(access_token, prompt, model, action, parameters, *args, **kwargs)

    hooked._bmk_res_hooked = True  # type: ignore[attr-defined]
    cls._post_image = staticmethod(hooked)

    _hook_installed = True
    logger.info("%s \ud655\uc815 \ud574\uc0c1\ub3c4 \ub300\uc870 \ud6c4\ud0b9\uc744 \uc124\uce58\ud588\uc2b5\ub2c8\ub2e4.", _TAG)
    return True


# ─── 테이블 HTTP 라우트 (JS readout 용) ───────────────────────────

def _table_payload() -> Dict[str, Any]:
    return {
        "version": TABLE_VERSION,
        "snap_unit": _SNAP_UNIT,
        "opus_free_pixels": _OPUS_FREE_PIXELS,
        "ratios": [{"key": k, "short": s, "long": l} for k, s, l in _RATIOS],
        "scale_factors": _SCALE_FACTORS,
        "scale_fhd": _SCALE_FHD,
        "scale_custom": _SCALE_CUSTOM,
        "fhd": {"short": _FHD_SHORT, "long": _FHD_LONG},
        "orientations": _ORIENT_KEYS,
        "modes": _MODE_KEYS,
        "snap_modes": _SNAP_KEYS,
        "badges": {"free": BADGE_FREE, "anlas": BADGE_ANLAS},
    }


def _register_routes() -> None:
    """해상도 테이블을 JS 로 노출한다. JS 에 숫자를 복제하지 않기 위한 것."""
    if getattr(_register_routes, "_done", False):
        return
    try:
        from aiohttp import web
        from server import PromptServer
    except Exception:
        return

    server = getattr(PromptServer, "instance", None)
    routes = getattr(server, "routes", None)
    if routes is None:
        return

    try:
        @routes.get("/bmk/nai_resolution/table")
        async def _bmk_nai_resolution_table(_request):  # noqa: ANN001
            return web.json_response(_table_payload())
    except Exception as exc:
        logger.debug("%s \ud14c\uc774\ube14 \ub77c\uc6b0\ud2b8 \ub4f1\ub85d \uc2e4\ud328: %s", _TAG, exc)
        return

    _register_routes._done = True  # type: ignore[attr-defined]


_register_routes()


# ─── 노드 ────────────────────────────────────────────────────────

class BMKNaiResolution:
    """NovelAI 해상도를 ratio × orientation × scale 세 축으로 계산한다."""

    TITLE = "BMK NAI Resolution"
    CATEGORY = "BMK/NovelAI"
    FUNCTION = "run"

    DESCRIPTION = (
        "NovelAI 해상도 29개 조합을 ratio(5) / orientation(2) / scale(5) 세 축으로 "
        "분해해 고릅니다. 배율 계열은 64px half-up 스냅으로 런타임 계산하므로 표를 "
        "하드코딩하지 않습니다. Opus 무료 한도(1MP) 초과 여부를 노드 위에 뱃지로 "
        "표시하고, GenerateNAID 가 내부에서 해상도를 보정하면 요청값과 확정값의 "
        "차이를 경고 로그로 남깁니다. i2i 모드에서는 소스 실측 해상도를 기준으로 "
        "목표 크기와 scale_by 를 계산해 리사이즈 노드와 생성 노드가 같은 숫자를 "
        "쓰도록 합니다. 소스는 NAI Extract 의 width/height 를 연결하는 것이 가장 "
        "가볍고, IMAGE 입력은 폴백입니다."
    )

    SEARCH_ALIASES = [
        "bmk",
        "nai",
        "novelai",
        "novel ai",
        "resolution",
        "resolutions",
        "size",
        "aspect ratio",
        "wallpaper",
        "upscale",
        "i2i",
        "img2img",
        "opus",
        "anlas",
        "해상도",
        "화면비",
        "비율",
        "가로세로",
        "업스케일",
        "배율",
        "월페이퍼",
        "무료",
    ]

    RETURN_TYPES = ("INT", "INT", "STRING", "FLOAT", "FLOAT", "STRING")
    RETURN_NAMES = ("width", "height", "resolution_str", "megapixels", "scale_by", "info")

    OUTPUT_TOOLTIPS = (
        "목표 가로. GenerateNAID 의 width 로 연결하세요.",
        "목표 세로. GenerateNAID 의 height 로 연결하세요.",
        "1216x832 형태 문자열. autosave 파일명 템플릿이나 XY Plot 축 라벨용.",
        "목표 해상도의 메가픽셀 수. 1.05 이하면 Opus 무료 범위입니다.",
        "스냅 후 실제 적용된 소스 대비 배율. 소스 리사이즈 노드에 그대로 물리면 "
        "재계산 없이 목표 크기가 일치합니다. 소스가 없으면 1.0.",
        "모드/소스/목표/배율/스냅을 한눈에 보는 요약. 결과가 예상과 다를 때 여기부터 봅니다.",
    )

    @classmethod
    def INPUT_TYPES(cls):
        # ※ 위젯은 append-only — 중간에 끼우면 저장된 워크플로의 widgets_values
        #   위치 배열이 어긋난다.
        return {
            "required": {
                "mode": (
                    _MODE_KEYS,
                    {
                        "default": MODE_AUTO,
                        "tooltip": (
                            "auto: is_i2i 입력이 연결돼 있으면 그 값으로, 아니면 소스 "
                            "연결 여부로 t2i / i2i: scale source 를 고릅니다.\n"
                            "t2i: 프리셋만 씁니다.\n"
                            "i2i: scale source: 소스 실측 × upscale. AR 을 최대한 "
                            "보존하므로 업스케일의 기본값입니다.\n"
                            "i2i: fit preset: 소스 AR 에 가장 가까운 프리셋을 자동 "
                            "선택합니다. ratio / orientation 위젯은 무시됩니다."
                        ),
                    },
                ),
                "ratio": (
                    _RATIO_KEYS,
                    {
                        "default": "3:2",
                        "tooltip": (
                            "긴 변 기준 비율. 세로/가로는 orientation 이 정합니다. "
                            "scale 이 FHD 면 무시됩니다."
                        ),
                    },
                ),
                "orientation": (
                    _ORIENT_KEYS,
                    {
                        "default": ORIENT_PORTRAIT,
                        "tooltip": "1:1 에서는 결과가 같습니다.",
                    },
                ),
                "scale": (
                    _SCALE_KEYS,
                    {
                        "default": "\u00d71.00",
                        "tooltip": (
                            "×1.00 은 전부 Opus 무료 범위(≤1MP)이고 ×1.25 이상은 "
                            "Anlas 를 소모합니다.\n"
                            "×1.69: 실제 계수는 1.6875. NAI 상한(1728² ≈ 2.99MP) "
                            "바로 아래를 노리는 계열입니다 (1:1 → 1728×1728, "
                            "3:2 → 1408×2048). ×1.75 는 다섯 비율 전부가 상한을 "
                            "넘어 쓸 수 없습니다.\n"
                            "FHD: 1088×1920 wallpaper 고정 크기 (ratio 무시).\n"
                            "custom: custom_long_side 로 긴 변을 직접 지정하고 짧은 "
                            "변은 ratio 에서 계산합니다."
                        ),
                    },
                ),
                "custom_long_side": (
                    "INT",
                    {
                        "default": 1536,
                        "min": 64,
                        "max": 8192,
                        "step": 64,
                        "tooltip": "scale 이 custom 일 때만 쓰입니다. 긴 변 픽셀.",
                    },
                ),
                "upscale": (
                    "FLOAT",
                    {
                        "default": 1.5,
                        "min": 0.25,
                        "max": 4.0,
                        "step": 0.05,
                        "tooltip": (
                            "i2i: scale source 전용. 소스 실측 대비 배율입니다. "
                            "scale 위젯(1MP 기준 배율)과 의미 축이 다르므로 분리돼 "
                            "있습니다. ×1.5 한 번보다 ×1.25 를 두 번 체인하는 쪽이 "
                            "디테일 붕괴가 적은 경우가 많습니다."
                        ),
                    },
                ),
                "snap_mode": (
                    _SNAP_KEYS,
                    {
                        "default": SNAP_NEAREST,
                        "tooltip": (
                            "nearest64: 축별 반올림. AR 이 0.5~1% 변동합니다.\n"
                            "preserve_ratio: 긴 변만 스냅하고 짧은 변을 원본 AR 로 "
                            "되계산. 타일/컨트롤넷 정합이 필요할 때.\n"
                            "floor64: 항상 내림. 한도 근처에서 안전합니다.\n"
                            "×1.00/×1.25/×1.50 프리셋은 앞의 두 모드가 같은 값을 "
                            "냅니다. ×1.69 는 4:3 에서만 갈립니다."
                        ),
                    },
                ),
                "clamp_max_mp": (
                    "FLOAT",
                    {
                        "default": 0.0,
                        "min": 0.0,
                        "max": 16.0,
                        "step": 0.05,
                        "tooltip": (
                            "0 이면 끕니다. 0 보다 크면 목표가 이 메가픽셀을 넘을 때 "
                            "AR 을 유지한 채 낮추고 경고 로그를 남깁니다. i2i 는 t2i "
                            "보다 상한이 낮은 경우가 있어 조용히 실패하는 것보다 낫습니다."
                        ),
                    },
                ),
            },
            "optional": {
                "source_width": (
                    "INT",
                    {
                        "default": 0,
                        "min": 0,
                        "max": 16384,
                        "forceInput": True,
                        "tooltip": "i2i 소스 가로. NAI Extract 의 width 를 연결하세요.",
                    },
                ),
                "source_height": (
                    "INT",
                    {
                        "default": 0,
                        "min": 0,
                        "max": 16384,
                        "forceInput": True,
                        "tooltip": "i2i 소스 세로. NAI Extract 의 height 를 연결하세요.",
                    },
                ),
                "image": (
                    "IMAGE",
                    {
                        "tooltip": (
                            "소스 폴백. source_width/height 가 비어 있을 때만 텐서에서 "
                            "크기를 읽습니다. INT 입력 쪽이 캐시에 가볍습니다."
                        ),
                    },
                ),
                "is_i2i": (
                    "BOOLEAN",
                    {
                        "forceInput": True,
                        "tooltip": (
                            "mode 가 auto 일 때만 쓰입니다. BMKContextAnima 의 is_i2i "
                            "를 연결하면 t2i/i2i 를 두 곳에서 세팅할 일이 없어집니다."
                        ),
                    },
                ),
            },
            "hidden": {"unique_id": "UNIQUE_ID"},
        }

    # ── 실행 ─────────────────────────────────────────────────────

    def run(
        self,
        mode: str,
        ratio: str,
        orientation: str,
        scale: str,
        custom_long_side: int,
        upscale: float,
        snap_mode: str,
        clamp_max_mp: float,
        source_width: int = 0,
        source_height: int = 0,
        image: Any = None,
        is_i2i: Optional[bool] = None,
        unique_id: Any = None,
    ) -> Tuple[int, int, str, float, float, str]:
        notes: List[str] = []

        src_w, src_h = self._resolve_source(source_width, source_height, image)
        has_source = src_w > 0 and src_h > 0

        eff_mode, auto_note = self._resolve_mode(mode, is_i2i, has_source)
        if auto_note:
            notes.append(auto_note)

        if eff_mode in (MODE_I2I_SCALE, MODE_I2I_FIT) and not has_source:
            logger.warning(
                "%s mode=%s \uc778\ub370 \uc18c\uc2a4 \ud574\uc0c1\ub3c4\uac00 "
                "\uc5c6\uc5b4 t2i \ub85c \ud3f4\ubc31\ud569\ub2c8\ub2e4. NAI Extract "
                "\uc758 width/height \ub97c \uc5f0\uacb0\ud558\uc138\uc694.",
                _TAG,
                eff_mode,
            )
            notes.append("소스 없음 → t2i 폴백")
            eff_mode = MODE_T2I

        used_ratio, used_orient = ratio, orientation

        if eff_mode == MODE_I2I_SCALE:
            width, height = _scale_pair(src_w, src_h, float(upscale), snap_mode)
        elif eff_mode == MODE_I2I_FIT:
            used_ratio, used_orient = _pick_ratio(src_w, src_h)
            width, height = _preset_dims(
                used_ratio, used_orient, scale, int(custom_long_side), snap_mode
            )
            notes.append(f"소스 AR → {used_ratio} / {used_orient}")
        else:
            width, height = _preset_dims(
                ratio, orientation, scale, int(custom_long_side), snap_mode
            )

        width, height, clamp_note = self._apply_clamp(
            width, height, float(clamp_max_mp), snap_mode
        )
        if clamp_note:
            notes.append(clamp_note)

        if width * height > _NAI_MAX_PIXELS:
            notes.append(
                f"NAI 상한 추정치 {_NAI_MAX_PIXELS / 1_000_000:.2f}MP 초과 — "
                "API 가 거부하거나 보정할 수 있음"
            )

        scale_by, axis_x, axis_y = self._scale_by(src_w, src_h, width, height, has_source)
        if has_source and axis_x > 0 and axis_y > 0:
            drift = abs(axis_x - axis_y) / max(axis_x, axis_y)
            if drift > 0.01:
                # preserve_ratio 로도 못 줄이는 경우가 있다 — 64 스냅 격자 자체의
                # 한계라 이때는 소스를 먼저 크롭하는 수밖에 없다.
                hint = (
                    "소스를 먼저 크롭해야 줄어듭니다"
                    if snap_mode == SNAP_PRESERVE
                    else "preserve_ratio 검토"
                )
                notes.append(f"축별 배율 불일치 {drift * 100:.1f}% — {hint}")

        label = self._label(unique_id, eff_mode)
        _record_request(width, height, label)
        _install_hook()

        info = self._info(
            mode=mode,
            eff_mode=eff_mode,
            ratio=used_ratio,
            orientation=used_orient,
            scale=scale,
            snap_mode=snap_mode,
            src=(src_w, src_h) if has_source else None,
            target=(width, height),
            scale_by=scale_by,
            axis=(axis_x, axis_y),
            notes=notes,
        )

        megapixels = round(width * height / 1_000_000.0, 4)
        return (
            int(width),
            int(height),
            f"{width}x{height}",
            float(megapixels),
            float(round(scale_by, 6)),
            info,
        )

    # ── 보조 ─────────────────────────────────────────────────────

    @staticmethod
    def _resolve_source(source_width: Any, source_height: Any, image: Any) -> Tuple[int, int]:
        try:
            width = int(source_width or 0)
            height = int(source_height or 0)
        except (TypeError, ValueError):
            width = height = 0

        if (width <= 0 or height <= 0) and image is not None:
            try:
                shape = image.shape  # ComfyUI IMAGE 는 [B, H, W, C]
                height, width = int(shape[1]), int(shape[2])
            except Exception:
                logger.debug("%s IMAGE \ud150\uc11c\uc5d0\uc11c \ud06c\uae30\ub97c \uc77d\uc9c0 \ubabb\ud588\uc2b5\ub2c8\ub2e4.", _TAG)

        return max(0, width), max(0, height)

    @staticmethod
    def _resolve_mode(
        mode: str, is_i2i: Optional[bool], has_source: bool
    ) -> Tuple[str, str]:
        if mode != MODE_AUTO:
            return mode, ""
        if is_i2i is not None:
            picked = MODE_I2I_SCALE if bool(is_i2i) else MODE_T2I
            return picked, f"auto → {picked} (is_i2i={bool(is_i2i)})"
        picked = MODE_I2I_SCALE if has_source else MODE_T2I
        return picked, f"auto → {picked} (소스 {'연결' if has_source else '없음'})"

    @staticmethod
    def _apply_clamp(
        width: int, height: int, clamp_max_mp: float, snap_mode: str
    ) -> Tuple[int, int, str]:
        if clamp_max_mp <= 0:
            return width, height, ""

        limit = clamp_max_mp * 1_000_000.0
        pixels = width * height
        if pixels <= limit:
            return width, height, ""

        factor = math.sqrt(limit / pixels)
        # 한도를 넘지 않아야 하므로 내림으로 스냅한다.
        new_w = _snap(width * factor, "floor")
        new_h = _snap(height * factor, "floor")
        logger.warning(
            "%s \ubaa9\ud45c %d\u00d7%d (%.2fMP) \uac00 \uc0c1\ud55c %.2fMP \ub97c "
            "\ub118\uc5b4 %d\u00d7%d \ub85c \ub0ae\ucd94\uc5c8\uc2b5\ub2c8\ub2e4.",
            _TAG,
            width,
            height,
            pixels / 1_000_000.0,
            clamp_max_mp,
            new_w,
            new_h,
        )
        return new_w, new_h, f"clamp {clamp_max_mp:.2f}MP → {new_w}×{new_h}"

    @staticmethod
    def _scale_by(
        src_w: int, src_h: int, width: int, height: int, has_source: bool
    ) -> Tuple[float, float, float]:
        if not has_source:
            return 1.0, 0.0, 0.0
        axis_x = width / src_w
        axis_y = height / src_h
        # 축별 비율이 스냅으로 미세하게 갈리므로 기하평균을 쓴다.
        return math.sqrt(axis_x * axis_y), axis_x, axis_y

    @staticmethod
    def _label(unique_id: Any, eff_mode: str) -> str:
        node = str(unique_id) if unique_id is not None else "?"
        return f"#{node} {eff_mode}"

    @staticmethod
    def _info(
        mode: str,
        eff_mode: str,
        ratio: str,
        orientation: str,
        scale: str,
        snap_mode: str,
        src: Optional[Tuple[int, int]],
        target: Tuple[int, int],
        scale_by: float,
        axis: Tuple[float, float],
        notes: List[str],
    ) -> str:
        width, height = target
        lines = [
            f"mode     : {eff_mode}" + (f"  (mode={mode})" if mode != eff_mode else ""),
        ]
        # i2i: scale source 는 프리셋 축을 전혀 쓰지 않는다. 위젯의 잔존값을
        # 찍으면 그 값이 결과에 반영된 것처럼 읽혀서 오해를 부른다.
        if eff_mode != MODE_I2I_SCALE:
            lines.append(f"preset   : {ratio} / {orientation} / {scale}")
        if src:
            src_mp = src[0] * src[1] / 1_000_000.0
            lines.append(f"source   : {src[0]}\u00d7{src[1]}  ({src_mp:.2f}MP)")
        lines.append(f"target   : {_readout(width, height)}")
        if src:
            lines.append(
                f"scale_by : {scale_by:.4f}  (x {axis[0]:.4f} / y {axis[1]:.4f})"
            )
        lines.append(f"snap     : {snap_mode}")
        for note in notes:
            lines.append(f"[!] {note}")
        return "\n".join(lines)


# ─── 등록 ────────────────────────────────────────────────────────

NODE_CLASS_MAPPINGS = {
    "BMKNaiResolution": BMKNaiResolution,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "BMKNaiResolution": "BMK NAI Resolution",
}
