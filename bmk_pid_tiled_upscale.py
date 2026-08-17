"""BMK PiD Tiled Upscale — NVIDIA PiD(PixelDiT) 4배 업스케일을 타일 루프로 수행.

PiD 체크포인트(`pid_*_1024_to_4096_*`)는 네이티브 4배 고정이며, 이름의 "1024" 는
학습 시 입력 규모(≈1MP → 출력 ≈16.8MP)를 뜻한다. 이 규모를 벗어난 캔버스를 한 번에
그리게 하면 픽셀 공간 텐서가 폭발하고, 학습 분포 밖이라 형태 경계의 응집력이 떨어진다
(꽃잎 실루엣이 톱니처럼 서걱거리는 현상).

실측으로 확인된 관계 (1280×960 소스, 동일 조건 3시드):

    패스당 캔버스        학습(16.8MP) 대비   결과
    2048×5120 = 10.5MP        63%          부드럽고 응집력 있음
    4096×4096 = 16.8MP       100%          무난, 약간 무름
    5120×5120 = 26.2MP       156%          경계 거칠어짐

즉 품질을 지배하는 것은 "PiD 가 한 번에 그리는 캔버스 면적"이다. 이 모듈은 두 가지
장치로 그 면적을 통제한다.

1. 자동 타일링 — 소스 종횡비를 유지한 채, 타일 하나의 PiD 입력 면적이
   `pid_input_size²` 근처가 되도록 격자를 자동 결정한다. 정사각 강제가 없으므로
   1280×960 같은 입력은 패딩 없이 1타일로 처리된다.
2. 타일 내부 컨텍스트 윈도우 — 각 타일의 출력을 높이 축으로 잘라 여러 번의
   forward pass 로 나누고 가중 융합한다. 패스당 캔버스가 더 줄어들고, 겹침 구간은
   매 스텝 예측의 앙상블이 되어 비간섭성 그레인이 상쇄된다.
   1타일 + 컨텍스트 윈도우 = 코어 ContextWindows 방식과 동일한 동작.

타일 기하
---------
    core_w × core_h  : 실제로 결과에 반영되는 영역 (자동 결정, 소스 종횡비 유지)
    PiD 입력          = (core_w + 2×context_pixel) × (core_h + 2×context_pixel)
    stride           = core − core_overlap

    ┌──────────────── PiD 입력 ────────────────┐
    │ ctx │            core            │ ctx │
    └─────┴────────────────────────────┴─────┘
             ↑ 생성에만 쓰고 페이스트에서 제외 ↑

경계는 가상 캔버스(BMK Flexible Tile SEGS 의 virtual_canvas 와 같은 사고방식)로
처리한다. 좌상단 기준 고정 스트라이드로 코어를 깔고, 모자란 부분은 이미지 밖으로
확장해 `padding_fill` 로 채운다. 마지막 타일을 가장자리에 스냅하지 않으므로 타일
크기가 끝까지 균일하고, 출력에서 패딩분은 ×4 비례로 잘라낸다.

과채도 억제
-----------
1. `degrade_sigma` 기본 0.06 — PiD 를 "재생성"이 아니라 "충실한 디코더"로 쓴다.
2. 코어 페이스트 — 각 픽셀은 단 한 타일에서만 온다(겹침 밴드 이중 처리 제거).
3. per-tile color match — 타일 결과를 자기 소스 타일의 색 통계로 되돌린다.
   출력을 소스 해상도로 area 다운샘플한 뒤 비교하므로, PiD 가 새로 만든 고주파가
   통계에 섞여 눌리지 않는다(주파수 공정 비교).
4. 전역 톤은 BMK Wavelet Tone Restore 에 맡긴다(이 노드에 내장하지 않음).

입력 경로
---------
- `latent` 연결 : 이미 인코딩된 것을 그대로 타일링. VAE 왕복이 없어 가장 정확·빠름.
- `image` 연결  : 이미지 단계에서 패딩한 뒤 타일별로 인코딩.
둘 다 연결되면 `latent` 가 우선한다. 인코딩 VAE 는 `vae_encode` 포트 > 로더의
`encode_vae` 위젯 순으로 해석한다.

버전 이력
---------
v2.2 (2026-08)
- BMK PiD Loader 에 `encode_vae` 위젯 추가. PiD 모델과 인코딩 VAE 는 latent 계열로
  이미 묶여 있으므로(qwenimage PiD ↔ qwen VAE) 같이 두는 편이 배선이 짧다.
  업스케일 노드의 `vae_encode` 포트는 오버라이드로 남는다(포트 우선).
  latent 직결 + color_match=off 로만 쓴다면 none 으로 두어 불필요한 로드를 피한다.
- `_pid_conditioning` 이 코어 PiDConditioning 노드를 먼저 호출하고, 인자가 맞지
  않을 때만 자체 구현으로 폴백한다. 노드 ID 로 찾으므로 업스트림이 파일을 옮겨도
  살아남고, 조건 키가 추가·변경되면 자동으로 따라간다.
  (컨텍스트 윈도우는 dim=2 처럼 기본값과 다른 값을 반드시 명시해야 해서 노드 경유
   대신 핸들러 직접 구성 + 시그니처 필터를 유지한다.)

v2.1 (2026-08)
- `tile_shape` 추가: auto(기본) / square. square 는 v1 방식 재현용으로,
  pid_input_size 를 PiD 입력 한 변으로 보고 정사각 타일을 강제한다
  (core = pid_input_size − 2×context_pixel). 면적 예산·종횡비 제약·패스 상한이
  모두 무시되며, 단일 타일 시 context_pixel 자동 하향도 적용하지 않는다.
- 패스당 캔버스가 기준의 150% 를 넘으면 경고 로그를 남긴다.

v2 (2026-08)
- 자동 타일링: 정사각 강제를 제거. `pid_input_size` 는 이제 "변 길이"가 아니라
  "면적 기준값"이며, 소스 종횡비를 유지하는 격자를 자동 계산한다. `tile_tolerance`
  배수 안에 들면 쪼개지 않는다(기본 1.3 → 1280×960 은 패딩 0 으로 1타일).
- 타일 내부 컨텍스트 윈도우 옵션 추가. 1타일 + on = AIO 동일 동작.
- VAE 디코드 직후의 clamp(0,1) 제거. 하이라이트 헤드룸을 후단
  (BMK Wavelet Tone Restore)까지 넘긴다. 코어 VAEDecode 와 동일한 거동.
- color match 의 std 게인을 [0.5, 2.0] 으로 제한(저대비 타일에서의 폭주 방지).
- ※ 위젯 구성이 바뀌었으므로 v1 노드가 놓인 워크플로우는 노드를 다시 추가해야 한다.

v1 (2026-08)
- 최초 구현. BMKPiDLoader(BMK_PID_CTX) + BMKPiDTiledUpscale 2노드 구성.
- 로더를 분리해 ComfyUI 표준 노드 캐시가 모델·프롬프트 재사용을 처리하도록 했다.
- latent_format 별 공간 배율(flux2 = 16×, 그 외 8×)을 분리하고 채널 수를 교차 검증.
"""

from __future__ import annotations

import inspect
import logging
import math
import os

import numpy as np
import torch
import torch.nn.functional as F

import folder_paths
import node_helpers
import comfy.latent_formats
import comfy.model_management as mm

import nodes as core_nodes
from comfy_extras import nodes_custom_sampler

try:
    from comfy.utils import ProgressBar
except Exception:  # pragma: no cover
    ProgressBar = None

logger = logging.getLogger(__name__)

_TAG_LOADER = "[ComfyUI_BMK_Nodes::PiDLoader]"
_TAG_UPSCALE = "[ComfyUI_BMK_Nodes::PiDTiledUpscale]"

_CTX_TYPE = "BMK_PID_CTX"

# PiD 는 네이티브 4배 고정 (릴리즈된 모든 체크포인트 공통)
_PID_SCALE = 4

# 자동 격자에서 허용하는 타일 종횡비 편차(소스 대비 로그 비율). 0.5 ≈ 1.65배까지.
_ASPECT_DEV_MAX = 0.5

_DEFAULT_PID_MODEL = "pid_1.5_qwenimage_1024_to_4096_4step_bf16.safetensors"
_DEFAULT_CLIP = "gemma_2_2b_it_elm_bf16.safetensors"
_DEFAULT_PID_VAE = "pixel_space"


# ─────────────────────────────────────────────────────────────────────────────
# 공용 헬퍼
# ─────────────────────────────────────────────────────────────────────────────
def _resolve_default(file_list, name):
    """file_list 에서 basename 이 name 인 항목을 찾아 반환(하위 폴더 대응)."""
    for f in file_list:
        if f.replace("\\", "/").split("/")[-1] == name:
            return f
    return file_list[0] if file_list else name


def _file_list(category, node_cls=None, input_key=None):
    """노드 클래스의 INPUT_TYPES 목록을 우선 사용하고, 실패 시 folder_paths 폴백."""
    if node_cls is not None and input_key is not None:
        try:
            return list(node_cls.INPUT_TYPES()["required"][input_key][0])
        except Exception:
            pass
    try:
        return list(folder_paths.get_filename_list(category))
    except Exception:
        return []


def _align_down(v, unit):
    return max(unit, (int(v) // unit) * unit)


def _align_up(v, unit):
    return int(math.ceil(float(v) / unit)) * unit


def _latent_spec(latent_format: str):
    """(latent_format 클래스, 공간 배율, 허용 채널 수) 를 반환.

    공간 배율 = latent 1 픽셀이 대응하는 이미지 픽셀 수. flux2(Klein) 계열만 16 이고
    나머지는 8 이다. 이 값을 8 로 고정하면 flux2 에서 목표 크기가 절반이 된다.
    """
    lf = str(latent_format)
    if lf == "qwenimage":
        return getattr(comfy.latent_formats, "Wan21", None), 8, (16,)
    if lf == "flux":
        return getattr(comfy.latent_formats, "Flux", None), 8, (16,)
    if lf == "flux2":
        return getattr(comfy.latent_formats, "Flux2", None), 16, (128,)
    if lf == "sd3":
        return getattr(comfy.latent_formats, "SD3", None), 8, (16,)
    if lf == "sdxl":
        return getattr(comfy.latent_formats, "SDXL", None), 8, (4,)
    raise ValueError(f"{_TAG_UPSCALE} 알 수 없는 latent_format: {latent_format}")


# 내 latent_format 표기 → 코어 PiDConditioning 이 받는 표기.
# 코어는 flux 안에서 채널 수(128)로 Flux2 를 스니핑하므로 flux2 도 flux 로 넘긴다.
_CORE_FORMAT = {"qwenimage": "qwenimage", "flux": "flux", "flux2": "flux",
                "sd3": "sd3", "sdxl": "sdxl"}

_CORE_NODE_CACHE = {}


def _find_core_node(*substrings):
    """NODE_CLASS_MAPPINGS 에서 키에 모든 substring 이 들어간 노드 클래스를 찾는다.

    모듈 경로가 아니라 노드 ID 로 찾으므로 ComfyUI 가 파일을 옮겨도 살아남는다.
    """
    key = substrings
    if key in _CORE_NODE_CACHE:
        return _CORE_NODE_CACHE[key]
    found = None
    try:
        for node_id, cls in getattr(core_nodes, "NODE_CLASS_MAPPINGS", {}).items():
            low = node_id.lower()
            if all(sub.lower() in low for sub in substrings):
                found = cls
                break
    except Exception:
        found = None
    _CORE_NODE_CACHE[key] = found
    return found


def _call_core_node(cls, values):
    """INPUT_TYPES 로 인자 이름을 맞춰 코어 노드를 호출. 못 맞추면 None.

    V3 노드(io.NodeOutput)는 .result 튜플로 언랩한다.
    """
    try:
        spec = cls.INPUT_TYPES()
        required = dict(spec.get("required", {}))
        optional = dict(spec.get("optional", {}))
        names = list(required) + list(optional)
        kwargs = {n: values[n] for n in names if n in values}
        if any(n not in kwargs for n in required):
            return None
        result = getattr(cls(), cls.FUNCTION)(**kwargs)
        if not isinstance(result, tuple):
            result = getattr(result, "result", result)
            if not isinstance(result, tuple):
                result = (result,)
        return result
    except Exception:
        logger.debug("%s 코어 노드 호출 실패 → 자체 구현으로 폴백",
                     _TAG_UPSCALE, exc_info=True)
        return None


def _pid_conditioning(positive, samples, fmt_cls, degrade_sigma, latent_format):
    """lq_latent / degrade_sigma 를 조건에 실어준다.

    코어 PiDConditioning 노드가 있으면 그것을 그대로 호출한다(업스트림이 조건 키를
    추가·변경해도 자동으로 따라감). 없거나 인자가 맞지 않으면 동등한 자체 구현으로
    폴백한다.
    """
    cls = _find_core_node("pidconditioning")
    if cls is not None:
        out = _call_core_node(cls, {
            "positive": positive,
            "latent": {"samples": samples},
            "latent_format": _CORE_FORMAT.get(str(latent_format), str(latent_format)),
            "degrade_sigma": float(degrade_sigma),
        })
        if out:
            return out[0]

    lq = fmt_cls().process_in(samples)
    if lq.ndim == 5:
        lq = lq[:, :, 0]
    sigma_t = torch.tensor([float(degrade_sigma)], dtype=torch.float32)
    return node_helpers.conditioning_set_values(
        positive, {"lq_latent": lq, "degrade_sigma": sigma_t}
    )


def _zero_out(conditioning):
    """ConditioningZeroOut 과 동등."""
    out = []
    for t in conditioning:
        d = t[1].copy()
        pooled = d.get("pooled_output")
        if pooled is not None:
            d["pooled_output"] = torch.zeros_like(pooled)
        out.append([torch.zeros_like(t[0]), d])
    return out


def _apply_model_shift(model, shift):
    """ModelSamplingSD3(shift) 적용. shift<=0 이면 통과."""
    if not shift or float(shift) <= 0.0:
        return model
    try:
        from comfy_extras.nodes_model_advanced import ModelSamplingSD3
    except Exception:
        logger.warning("%s ModelSamplingSD3 를 찾지 못해 model_shift 를 건너뜁니다.",
                       _TAG_UPSCALE)
        return model
    return ModelSamplingSD3().patch(model, float(shift))[0]


def _apply_context_windows(model, length, overlap, fuse):
    """타일 출력을 높이 축(dim=2)으로 잘라 여러 패스로 나눈다.

    코어 ContextWindowsManual 과 동일한 설정(standard_static / stride 1 /
    closed_loop off / freenoise off)이며, 실패하면 경고만 남기고 원본을 돌려준다.
    ComfyUI 버전에 따라 생성자 인자가 다를 수 있어 시그니처로 필터링한다.
    """
    try:
        import comfy.context_windows as cwmod
    except Exception:
        logger.warning(
            "%s 이 ComfyUI 버전에는 comfy.context_windows 가 없어 컨텍스트 윈도우를 "
            "건너뜁니다. 타일을 통째로 그리게 되니 pid_input_size 를 낮추세요.",
            _TAG_UPSCALE)
        return model, False

    kwargs = dict(
        context_schedule=cwmod.get_matching_context_schedule("standard_static"),
        fuse_method=cwmod.get_matching_fuse_method(fuse),
        context_length=int(length),
        context_overlap=int(overlap),
        context_stride=1,
        closed_loop=False,
        dim=2,
        freenoise=False,
        cond_retain_index_list="",
        split_conds_to_windows=False,
        latent_retain_index_list="",
        causal_window_fix=False,
    )
    try:
        try:
            handler = cwmod.IndexListContextHandler(**kwargs)
        except TypeError:
            params = inspect.signature(
                cwmod.IndexListContextHandler.__init__).parameters
            handler = cwmod.IndexListContextHandler(
                **{k: v for k, v in kwargs.items() if k in params})
        m = model.clone()
        m.model_options["context_handler"] = handler
        cwmod.create_prepare_sampling_wrapper(m)
        return m, True
    except Exception:
        logger.warning("%s 컨텍스트 윈도우 적용에 실패해 건너뜁니다.",
                       _TAG_UPSCALE, exc_info=True)
        return model, False


def _pad_nchw(x, pl, pr, pt, pb, mode):
    """[B,C,H,W] 패딩. reflect 는 크기 제약이 있어 초과 시 replicate 로 폴백."""
    if pl == pr == pt == pb == 0:
        return x
    if mode == "reflect":
        if pl < x.shape[3] and pr < x.shape[3] and pt < x.shape[2] and pb < x.shape[2]:
            return F.pad(x, (pl, pr, pt, pb), mode="reflect")
        logger.info("%s reflect 패딩 크기 제약 초과 → replicate 로 폴백", _TAG_UPSCALE)
        return F.pad(x, (pl, pr, pt, pb), mode="replicate")
    if mode == "edge":
        return F.pad(x, (pl, pr, pt, pb), mode="replicate")
    value = {"gray": 0.5, "black": 0.0, "white": 1.0}.get(mode, 0.0)
    return F.pad(x, (pl, pr, pt, pb), mode="constant", value=value)


def _pad_nhwc(img, pl, pr, pt, pb, mode):
    if pl == pr == pt == pb == 0:
        return img
    return _pad_nchw(img.movedim(-1, 1), pl, pr, pt, pb, mode).movedim(1, -1)


def _axis_starts(length, core, stride):
    """좌상단 기준 고정 스트라이드 코어 시작 좌표. 마지막 타일을 스냅하지 않는다."""
    if length <= core:
        return [0]
    n = math.ceil((length - core) / stride) + 1
    return [i * stride for i in range(n)]


def _axis_weight(length, lo, hi):
    """1D feather 가중치. lo/hi 는 각 끝에서 선형 전이시킬 폭(px)."""
    w = np.ones(length, dtype=np.float32)
    if lo > 0:
        lo = min(int(lo), length)
        w[:lo] *= (np.arange(lo, dtype=np.float32) + 1.0) / (lo + 1.0)
    if hi > 0:
        hi = min(int(hi), length)
        w[length - hi:] *= (np.arange(hi, 0, -1, dtype=np.float32)) / (hi + 1.0)
    return w


def _auto_grid(src_w, src_h, size_ref, context, overlap, unit, tolerance,
               budget_core, pass_height=None, max_n=64):
    """소스 종횡비를 유지하며 면적 예산에 맞는 격자를 찾는다.

    반환: (nx, ny, core_w, core_h)

    격자 (nx, ny) 로 축을 덮으려면 core 는 최소 ceil((L + (n−1)·overlap) / n) 이면
    된다. 그 core 에 컨텍스트를 더한 값이 실제 PiD 입력 크기다. 조건을 만족하는
    조합 중 **타일 수가 가장 적고 → 종횡비 편차가 가장 작은** 것을 고른다.

    면적 예산의 기준은 컨텍스트 윈도우 사용 여부에 따라 달라진다.

    - budget_core=False (창 off): 타일을 통째로 한 번에 그리므로 **PiD 입력 면적**
      자체가 곧 패스당 캔버스다. (core + 2·context)² 를 size_ref²×tolerance 로 제한.
    - budget_core=True (창 on): 패스당 캔버스는 창 높이가 잘라주므로 타일 면적을
      그렇게까지 조일 필요가 없다. **코어 면적**만 제한하고 컨텍스트 링은 오버헤드로
      허용한다. 이 덕분에 1024×1024 + context 128 같은 조합이 1타일로 유지된다.

    두 경우 모두 타일 출력 텐서가 과대해지지 않도록 별도의 메모리 상한
    (기준 출력 면적의 4배)을 함께 건다. 그리고 pass_height(=context_length)가
    주어지면 **패스당 캔버스**(타일 출력 폭 × 창 높이)도 기준 출력 면적×tolerance
    이하로 제한한다. 창은 높이만 자르므로 이것이 없으면 4096×512 같은 극단 와이드
    입력에서 패스 캔버스가 35MP 까지 부풀어 학습 규모를 넘긴다.
    """
    ref = float(size_ref) * float(size_ref)
    area_max = ref * float(tolerance)
    out_max = ref * (_PID_SCALE ** 2) * 4.0
    pass_max = ref * (_PID_SCALE ** 2) * float(tolerance)
    src_ar = float(src_w) / float(src_h)

    def _search(dev_max):
        """dev_max = 소스 대비 허용 종횡비 편차(로그 비율). None 이면 제한 없음.

        점수는 n_tiles × (1 + dev) — 타일 수만으로 고르면 창이 높이를 처리해주는
        만큼 "얇고 긴 띠"로 붕괴하므로(예: 4096² → 320×4096 스트립 13장) 종횡비
        편차를 곱셈 페널티로 함께 건다.
        """
        found = None
        for ny in range(1, max_n + 1):
            core_h = _align_up((src_h + (ny - 1) * overlap) / ny, unit)
            if core_h - overlap < unit:   # stride 가 unit 미만이 되는 조합 배제
                continue
            in_h = core_h + 2 * context
            for nx in range(1, max_n + 1):
                core_w = _align_up((src_w + (nx - 1) * overlap) / nx, unit)
                if core_w - overlap < unit:
                    continue
                in_w = core_w + 2 * context
                area = (core_w * core_h) if budget_core else (in_w * in_h)
                if area > area_max:
                    continue
                out_w = in_w * _PID_SCALE
                out_h = in_h * _PID_SCALE
                if out_w * out_h > out_max:
                    continue
                if pass_height is not None and \
                        out_w * min(out_h, int(pass_height)) > pass_max:
                    continue
                dev = abs(math.log((in_w / in_h) / src_ar))
                if dev_max is not None and dev > dev_max:
                    continue
                key = (round(nx * ny * (1.0 + dev), 6), in_w * in_h)
                if found is None or key < found[0]:
                    found = (key, nx, ny, core_w, core_h)
        return found

    best = _search(_ASPECT_DEV_MAX) or _search(None)

    if best is None:
        raise ValueError(
            f"{_TAG_UPSCALE} 타일 격자를 찾지 못했습니다. context_pixel({context}) 이 "
            f"pid_input_size({size_ref}) 대비 과대하거나 tile_tolerance({tolerance}) 가 "
            "너무 낮습니다. context_pixel 을 줄이거나, context_windows 를 켜거나, "
            "pid_input_size 를 키우세요.")
    return best[1], best[2], best[3], best[4]


def _color_match(out_core, src_core, mode, strength):
    """타일 출력을 소스 타일의 색 통계로 되돌린다.

    출력을 소스 해상도로 area 다운샘플한 뒤 통계를 비교하므로, PiD 가 새로 만든
    고주파 디테일이 std 에 섞여 눌리는 일이 없다(주파수 공정 비교).

    - "mean"     : 채널별 평균만 맞춘다. 색 편이를 잡되 대비는 건드리지 않음.
    - "mean_std" : Reinhard 계열 전체 매칭. 게인은 [0.5, 2.0] 으로 제한한다.
    """
    if mode == "off" or float(strength) <= 0.0:
        return out_core

    o = out_core[..., :3]
    s = src_core[..., :3].to(o.device, dtype=o.dtype)

    o_ds = F.interpolate(
        o.movedim(-1, 1), size=(int(s.shape[1]), int(s.shape[2])), mode="area"
    ).movedim(1, -1)

    om = o_ds.mean(dim=(1, 2), keepdim=True)
    sm = s.mean(dim=(1, 2), keepdim=True)

    if mode == "mean_std":
        eps = 1e-5
        osd = o_ds.std(dim=(1, 2), keepdim=True)
        ssd = s.std(dim=(1, 2), keepdim=True)
        gain = ((ssd + eps) / (osd + eps)).clamp(0.5, 2.0)
        corrected = (o - om) * gain + sm
    else:
        corrected = o + (sm - om)

    st = float(strength)
    blended = o * (1.0 - st) + corrected * st

    if out_core.shape[-1] > 3:
        return torch.cat([blended, out_core[..., 3:]], dim=-1)
    return blended


class _PiDContext(dict):
    """BMK_PID_CTX 컨테이너. 텍스트 프리뷰에서 텐서를 덤프하지 않는다."""

    def __repr__(self):  # pragma: no cover - 표시 전용
        return (f"<BMK_PID_CTX model={self.get('model_name')!r} "
                f"clip={self.get('clip_name')!r} prompt={self.get('prompt')!r}>")

    __str__ = __repr__


# ─────────────────────────────────────────────────────────────────────────────
# 로더
# ─────────────────────────────────────────────────────────────────────────────
class BMKPiDLoader:
    """PiD UNET + 텍스트 인코더 + pixel_space VAE 를 한 번에 준비해 CTX 로 내보낸다."""

    @classmethod
    def INPUT_TYPES(cls):
        unet_list = _file_list("diffusion_models", core_nodes.UNETLoader, "unet_name")
        clip_list = _file_list("text_encoders", core_nodes.CLIPLoader, "clip_name")
        vae_list = _file_list("vae", core_nodes.VAELoader, "vae_name") or [_DEFAULT_PID_VAE]

        return {
            "required": {
                "pid_model": (unet_list, {
                    "default": _resolve_default(unet_list, _DEFAULT_PID_MODEL),
                    "tooltip": "PiD 체크포인트. 인코딩에 쓴 VAE 의 latent 계열과 반드시 "
                               "일치해야 합니다(qwenimage / flux1 / flux2 / sd3 / sdxl)."}),
                "clip_name": (clip_list, {
                    "default": _resolve_default(clip_list, _DEFAULT_CLIP),
                    "tooltip": "PiD 전용 텍스트 인코더 (gemma_2_2b_it_elm)."}),
                "pid_vae": (vae_list, {
                    "default": _resolve_default(vae_list, _DEFAULT_PID_VAE),
                    "tooltip": "PiD 디코드용 VAE. pixel_space 만 동작합니다. "
                               "(입력 인코딩용 VAE 는 업스케일 노드 쪽에 연결하세요.)"}),
                "prompt": ("STRING", {
                    "default": "best quality", "multiline": True,
                    "tooltip": "업스케일 보조 프롬프트. 기본값 그대로 두어도 됩니다."}),
                "weight_dtype": (["default", "fp8_e4m3fn", "fp8_e4m3fn_fast", "fp8_e5m2"], {
                    "default": "default"}),
                "encode_vae": (["none"] + vae_list, {
                    "default": "none",
                    "tooltip": "입력 인코딩용 VAE(생성 백본과 같은 계열. 예: qwen_image_vae). "
                               "여기서 지정하면 업스케일 노드의 vae_encode 포트를 연결하지 "
                               "않아도 됩니다. PiD 모델과 latent 계열이 묶여 있으므로 대개 "
                               "한 번 정하면 바뀌지 않습니다. 포트를 연결하면 그쪽이 "
                               "우선합니다. latent 직결 + color_match=off 로만 쓴다면 "
                               "none 으로 두어 불필요한 로드를 피하세요."}),
            }
        }

    RETURN_TYPES = (_CTX_TYPE,)
    RETURN_NAMES = ("pid_ctx",)
    FUNCTION = "doit"
    CATEGORY = "BMK/Image"
    DESCRIPTION = (
        "PiD 업스케일에 필요한 UNET·텍스트 인코더·pixel_space VAE 와 프롬프트 조건을 "
        "한 번에 준비해 BMK PiD Tiled Upscale 로 넘깁니다. 로더를 분리해 두었으므로 "
        "타일 파라미터만 바꿔 재실행할 때 모델이 다시 로드되지 않습니다."
    )
    SEARCH_ALIASES = [
        "pid loader", "pixeldit loader", "pid model", "pid context",
        "PiD 로더", "픽셀디트", "업스케일 로더",
    ]

    @classmethod
    def IS_CHANGED(cls, pid_model, clip_name, pid_vae, prompt, weight_dtype,
                   encode_vae="none"):
        """디스크의 파일이 조용히 교체된 경우에만 캐시를 무효화한다."""
        parts = []
        for category, name in (("diffusion_models", pid_model),
                               ("text_encoders", clip_name),
                               ("vae", pid_vae),
                               ("vae", encode_vae)):
            try:
                path = folder_paths.get_full_path(category, name)
                parts.append(str(os.path.getmtime(path)) if path else "-")
            except Exception:
                parts.append("-")
        return "|".join(parts) + f"|{prompt}|{weight_dtype}|{encode_vae}"

    def doit(self, pid_model, clip_name, pid_vae, prompt, weight_dtype,
             encode_vae="none"):
        if "pixel_space" not in str(pid_vae):
            raise ValueError(
                f"{_TAG_LOADER} pid_vae 는 pixel_space 여야 합니다 (선택됨: {pid_vae}). "
                "PiD 는 픽셀 공간에서 디코드하는 모델이라 일반 VAE 를 고르면 채널/구조 "
                "불일치로 실패하거나 결과가 망가집니다. 입력 인코딩용 VAE(qwen_image_vae "
                "등)는 BMK PiD Tiled Upscale 의 vae_encode 입력에 연결하세요.")

        model = core_nodes.UNETLoader().load_unet(pid_model, weight_dtype)[0]
        clip = core_nodes.CLIPLoader().load_clip(clip_name, "pixeldit", "default")[0]
        vae = core_nodes.VAELoader().load_vae(pid_vae)[0]

        tokens = clip.tokenize(prompt)
        if hasattr(clip, "encode_from_tokens_scheduled"):
            positive = clip.encode_from_tokens_scheduled(tokens)
        else:  # 구버전 CLIP API 폴백
            cond, pooled = clip.encode_from_tokens(tokens, return_pooled=True)
            positive = [[cond, {"pooled_output": pooled}]]
        negative = _zero_out(positive)

        enc_vae = None
        if encode_vae and str(encode_vae) != "none":
            if "pixel_space" in str(encode_vae):
                raise ValueError(
                    f"{_TAG_LOADER} encode_vae 에 pixel_space 를 고를 수 없습니다. "
                    "이건 입력 이미지를 latent 로 인코딩하는 VAE 자리이며, 생성 백본과 "
                    "같은 계열(qwen_image_vae 등)이어야 합니다.")
            enc_vae = core_nodes.VAELoader().load_vae(encode_vae)[0]

        logger.info("%s ready: model=%s clip=%s pid_vae=%s encode_vae=%s",
                    _TAG_LOADER, pid_model, clip_name, pid_vae, encode_vae)

        return (_PiDContext(
            model=model, vae=vae, positive=positive, negative=negative,
            encode_vae=enc_vae,
            model_name=pid_model, clip_name=clip_name, vae_name=pid_vae,
            encode_vae_name=str(encode_vae), prompt=prompt,
        ),)


# ─────────────────────────────────────────────────────────────────────────────
# 타일 업스케일
# ─────────────────────────────────────────────────────────────────────────────
class BMKPiDTiledUpscale:
    """PiD 4배 업스케일. 소스 종횡비를 유지하는 자동 타일링 + 타일 내부 컨텍스트 윈도우."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "pid_ctx": (_CTX_TYPE, {"tooltip": "BMK PiD Loader 출력"}),
                "latent_format": (["qwenimage", "flux", "flux2", "sd3", "sdxl"], {
                    "default": "qwenimage",
                    "tooltip": "입력 latent 의 계열. 생성 모델이 아니라 '인코딩에 쓴 VAE' 를 "
                               "따릅니다. 채널 수가 맞지 않으면 실행 시점에 막습니다."}),
                "pid_input_size": ("INT", {
                    "default": 1024, "min": 256, "max": 4096, "step": 16,
                    "tooltip": "타일 크기의 '면적 기준값'(변 길이가 아님). 타일 하나의 PiD "
                               "입력 면적이 이 값의 제곱 근처가 되도록 격자를 자동 계산하며, "
                               "타일 종횡비는 소스를 따라갑니다. 체크포인트 학습 크기와 "
                               "맞추세요(1024_to_4096 → 1024)."}),
                "tile_tolerance": ("FLOAT", {
                    "default": 1.3, "min": 1.0, "max": 3.0, "step": 0.05,
                    "tooltip": "기준 면적의 몇 배까지는 쪼개지 않을지. 1.3 이면 1280×960 "
                               "(=1.17×1024²) 이 1타일로 처리됩니다. 예산 기준은 "
                               "context_windows 가 켜져 있으면 코어 면적, 꺼져 있으면 "
                               "PiD 입력 면적(코어+컨텍스트)입니다. "
                               "tile_shape=square 에서는 무시됩니다."}),
                "context_pixel": ("INT", {
                    "default": 128, "min": 0, "max": 1024, "step": 16,
                    "tooltip": "코어 바깥 컨텍스트(px). 생성에는 쓰이고 결과에서는 버려집니다. "
                               "PiD 입력 = core + 2 × context_pixel. 1타일이면 무의미하므로 "
                               "0 으로 두어도 됩니다."}),
                "core_overlap": ("INT", {
                    "default": 64, "min": 0, "max": 512, "step": 16,
                    "tooltip": "코어끼리 겹치는 폭(px). 이 구간에서만 feather 블렌드. "
                               "0 = 완전 분할(이중 처리 전무, 대신 seam 이 보일 수 있음)."}),
                "padding_fill": (["reflect", "edge", "gray", "black", "white"], {
                    "default": "reflect",
                    "tooltip": "가상 캔버스(이미지 밖) 채움 방식. reflect 권장."}),
                "context_windows": ("BOOLEAN", {
                    "default": True, "label_on": "on", "label_off": "off",
                    "tooltip": "각 타일의 출력을 높이 축으로 잘라 여러 패스로 나눕니다. "
                               "패스당 캔버스가 줄어 형태 응집력이 좋아지고, 겹침 구간은 "
                               "매 스텝 예측의 앙상블이 되어 그레인이 상쇄됩니다. "
                               "1타일 + on = 코어 ContextWindows 방식과 동일 동작."}),
                "context_length": ("INT", {
                    "default": 2048, "min": 512, "max": 8192, "step": 128,
                    "tooltip": "창 하나의 높이(출력 px). 타일 출력 높이가 이보다 작거나 "
                               "같으면 창이 1개라 적용을 건너뜁니다."}),
                "context_overlap": ("INT", {
                    "default": 512, "min": 0, "max": 4096, "step": 64,
                    "tooltip": "창 간 겹침(출력 px). 클수록 앙상블 구간이 넓어지고 느려집니다."}),
                "fuse_method": (["pyramid", "overlap-linear", "flat"], {
                    "default": "pyramid",
                    "tooltip": "창 융합 가중. pyramid = 창 중앙 가중(기본), "
                               "overlap-linear = 겹침 구간만 선형(저사양·빠름)."}),
                "degrade_sigma": ("FLOAT", {
                    "default": 0.06, "min": 0.0, "max": 1.0, "step": 0.01,
                    "tooltip": "낮을수록 원본 충실(=색 편이 억제). 0.05~0.10 권장."}),
                "steps": ("INT", {"default": 4, "min": 1, "max": 20}),
                "seed": ("INT", {
                    "default": 42, "min": 0, "max": 0xffffffffffffffff,
                    "control_after_generate": True}),
                "seed_mode": (["same", "per_tile"], {
                    "default": "same",
                    "tooltip": "same: 모든 타일이 같은 시드(타일 간 일관성 우수, 권장)."}),
                "color_match": (["mean", "mean_std", "off"], {
                    "default": "mean",
                    "tooltip": "타일 출력을 자기 소스 타일의 색 통계로 되돌립니다. "
                               "mean: 색 편이만 보정(디테일 대비 보존). "
                               "mean_std: 채도 폭주까지 억제. 1타일이면 효과가 거의 없습니다."}),
                "color_match_strength": ("FLOAT", {
                    "default": 1.0, "min": 0.0, "max": 1.0, "step": 0.05}),
                "model_shift": ("FLOAT", {
                    "default": 0.0, "min": 0.0, "max": 10.0, "step": 0.1,
                    "tooltip": "ModelSamplingSD3 shift. 0 = 미적용(AIO 와 동일). "
                               "올리면 과한 샤픈/헤일로가 완화되는 대신 디테일이 약간 "
                               "줄어듭니다. 4096² 실측: 0→3 에서 고주파 평균 -10%, "
                               "백/흑 클리핑 -46%/-66%. 3→8 에서는 평균은 -1.4% 뿐인데 "
                               "극단 오버슈트(p99.9)만 -14% 추가로 깎입니다. "
                               "권장 탐색 구간 2~8, 클리핑 최소는 3 부근."}),
                "accum_dtype": (["fp32", "fp16"], {
                    "default": "fp32",
                    "tooltip": "CPU 누적 캔버스 dtype. 초대형 입력에서 RAM 이 빠듯하면 fp16."}),
                "tile_shape": (["auto", "square"], {
                    "default": "auto",
                    "tooltip": "auto: 소스 종횡비를 유지하며 면적 예산에 맞는 격자를 자동 "
                               "계산합니다(권장). square: v1 방식 재현 — pid_input_size 를 "
                               "'PiD 입력 한 변'으로 보고 정사각 타일을 강제합니다 "
                               "(core = pid_input_size − 2×context_pixel). 이때 "
                               "tile_tolerance·종횡비 제약·면적/패스 상한이 전부 무시되므로 "
                               "비교 테스트용으로만 쓰세요."}),
            },
            "optional": {
                "latent": ("LATENT", {
                    "tooltip": "KSampler 직결. 연결되면 image 보다 우선하며 VAE 왕복이 없습니다."}),
                "image": ("IMAGE", {
                    "tooltip": "임의 이미지 업스케일용. 인코딩 VAE 가 필요합니다 — 로더의 "
                               "encode_vae 위젯 또는 아래 vae_encode 포트."}),
                "vae_encode": ("VAE", {
                    "tooltip": "입력 인코딩용 VAE 오버라이드. 미연결이면 BMK PiD Loader 의 "
                               "encode_vae 위젯 값을 씁니다. PiD 디코드용 pixel_space VAE 와 "
                               "다릅니다."}),
            },
        }

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("image",)
    FUNCTION = "doit"
    CATEGORY = "BMK/Image"
    DESCRIPTION = (
        "PiD 업스케일을 타일 루프로 수행해 입력 해상도 제한 없이 4배 확대합니다. "
        "소스 종횡비를 유지하는 자동 타일링으로 PiD 가 한 번에 그리는 캔버스 면적을 "
        "학습 규모 근처로 묶고, 타일 내부 컨텍스트 윈도우로 패스당 캔버스를 더 줄여 "
        "형태 응집력을 확보합니다. VRAM 피크는 타일(또는 창) 하나로 고정됩니다."
    )
    SEARCH_ALIASES = [
        "pid upscale", "pid tiled", "pixeldit upscale", "tiled 4x", "super resolution",
        "PiD 업스케일", "타일 업스케일", "4배 업스케일", "고해상도",
    ]

    # ── 타일 1장 처리 ────────────────────────────────────────────────────────
    def _sample_tile(self, ctx, model, sampler, sigmas, tile_lat, fmt_cls,
                     degrade_sigma, seed, out_h, out_w, latent_format):
        positive = _pid_conditioning(ctx["positive"], tile_lat, fmt_cls,
                                     degrade_sigma, latent_format)
        empty = torch.zeros((tile_lat.shape[0], 3, out_h, out_w),
                            device=mm.intermediate_device())
        out = nodes_custom_sampler.SamplerCustom().sample(
            model, True, int(seed), 1.0, positive, ctx["negative"],
            sampler, sigmas, {"samples": empty})[0]
        img = ctx["vae"].decode(out["samples"])
        if img.ndim == 5:
            img = img.reshape(-1, img.shape[-3], img.shape[-2], img.shape[-1])
        # clamp 하지 않는다 — 하이라이트 헤드룸을 후단(Wavelet Tone Restore)까지 넘김
        return img

    # ── 본체 ─────────────────────────────────────────────────────────────────
    def doit(self, pid_ctx, latent_format, pid_input_size, tile_tolerance,
             context_pixel, core_overlap, padding_fill,
             context_windows, context_length, context_overlap, fuse_method,
             degrade_sigma, steps, seed, seed_mode,
             color_match, color_match_strength, model_shift, accum_dtype,
             tile_shape="auto", latent=None, image=None, vae_encode=None):

        if not isinstance(pid_ctx, dict) or "model" not in pid_ctx:
            raise ValueError(f"{_TAG_UPSCALE} pid_ctx 가 유효하지 않습니다. "
                             "BMK PiD Loader 의 출력을 연결하세요.")

        # 인코딩 VAE: 포트 연결이 우선, 없으면 로더 위젯 값
        enc_vae = vae_encode if vae_encode is not None else pid_ctx.get("encode_vae")

        fmt_cls, lf_factor, allowed_ch = _latent_spec(latent_format)
        if fmt_cls is None:
            raise ValueError(
                f"{_TAG_UPSCALE} 이 ComfyUI 버전에는 '{latent_format}' latent format 이 "
                "없습니다. ComfyUI 를 업데이트하거나 다른 형식을 고르세요.")

        unit = max(16, lf_factor)
        size_ref = _align_down(pid_input_size, unit)
        ctx_px = max(0, (int(context_pixel) // unit) * unit)
        ov = max(0, (int(core_overlap) // unit) * unit)

        # ── 입력 정규화: latent 우선, 없으면 image → 타일별 인코딩 ──
        use_latent = latent is not None
        if use_latent:
            src_lat = latent["samples"]
            if src_lat.ndim == 5:
                src_lat = src_lat[:, :, 0]
            channels = int(src_lat.shape[1])
            if channels not in allowed_ch:
                raise ValueError(
                    f"{_TAG_UPSCALE} latent_format='{latent_format}' 은 채널 "
                    f"{allowed_ch} 를 기대하는데 입력은 {channels}채널입니다. "
                    "인코딩에 쓴 VAE 와 latent_format 을 맞추세요.")
            batch = int(src_lat.shape[0])
            src_h = int(src_lat.shape[-2]) * lf_factor
            src_w = int(src_lat.shape[-1]) * lf_factor
            src_img = None
        else:
            if image is None:
                raise ValueError(
                    f"{_TAG_UPSCALE} latent 또는 image 중 하나는 연결해야 합니다.")
            if enc_vae is None:
                raise ValueError(
                    f"{_TAG_UPSCALE} image 경로에는 입력 인코딩용 VAE 가 필요합니다. "
                    "BMK PiD Loader 의 encode_vae 를 지정하거나, vae_encode 포트에 "
                    "생성 백본과 같은 계열의 VAE 를 연결하세요.")
            src_lat = None
            batch = int(image.shape[0])
            src_h = int(image.shape[1])
            src_w = int(image.shape[2])
            src_img = image

        # ── 격자 결정 ──
        square = (str(tile_shape) == "square")
        if square:
            # [v1 재현] pid_input_size 를 PiD 입력 한 변으로 보고 정사각 강제.
            # 면적 예산·종횡비 제약·패스 상한을 모두 무시하므로 비교 테스트 전용.
            core_sq = size_ref - 2 * ctx_px
            if core_sq < unit:
                raise ValueError(
                    f"{_TAG_UPSCALE} tile_shape=square 에서 context_pixel({ctx_px}) 이 "
                    f"과대합니다. core = pid_input_size({size_ref}) − 2×context_pixel "
                    f"이 최소 {unit}px 이어야 합니다.")
            if ov >= core_sq:
                ov = max(0, core_sq - unit)
                logger.warning("%s core_overlap 이 코어(%d) 이상이라 %dpx 로 축소했습니다.",
                               _TAG_UPSCALE, core_sq, ov)
            core_w = core_h = core_sq
        else:
            core_w = core_h = 0
            nx, ny, core_w, core_h = _auto_grid(
                src_w, src_h, size_ref, ctx_px, ov, unit, tile_tolerance,
                budget_core=bool(context_windows),
                pass_height=int(context_length) if context_windows else None)

            # 타일이 하나뿐이면 컨텍스트 링은 전부 가상 패딩(미러)이라 실제 컨텍스트가
            # 아니다. 연산만 늘리므로(1280×960 기준 +52%) 자동으로 떨군다.
            # square 모드에서는 v1 거동을 그대로 두기 위해 적용하지 않는다.
            if nx * ny == 1 and ctx_px > 0:
                logger.info("%s 단일 타일이라 context_pixel %d → 0 (컨텍스트 링이 전부 "
                            "가상 패딩이므로 순수 오버헤드)", _TAG_UPSCALE, ctx_px)
                ctx_px = 0
                nx, ny, core_w, core_h = _auto_grid(
                    src_w, src_h, size_ref, ctx_px, ov, unit, tile_tolerance,
                    budget_core=bool(context_windows),
                    pass_height=int(context_length) if context_windows else None)
        stride_x = core_w - ov
        stride_y = core_h - ov
        xs = _axis_starts(src_w, core_w, stride_x)
        ys = _axis_starts(src_h, core_h, stride_y)

        tile_w = core_w + 2 * ctx_px
        tile_h = core_h + 2 * ctx_px
        out_w = tile_w * _PID_SCALE
        out_h = tile_h * _PID_SCALE

        pad_l = pad_t = ctx_px
        pad_r = max(0, xs[-1] + core_w + ctx_px - src_w)
        pad_b = max(0, ys[-1] + core_h + ctx_px - src_h)
        pad_w = src_w + pad_l + pad_r
        pad_h = src_h + pad_t + pad_b
        canvas_w = pad_w * _PID_SCALE
        canvas_h = pad_h * _PID_SCALE
        n_tiles = len(xs) * len(ys)

        logger.info(
            "%s src %dx%d (%s, %dch/%dx) shape=%s → grid %dx%d = %d tile(s) "
            "[core %dx%d, PiD input %dx%d = %.2fMP (기준 %.2fMP), context %d, "
            "overlap %d] → out %dx%d",
            _TAG_UPSCALE, src_w, src_h, latent_format, allowed_ch[0], lf_factor,
            tile_shape, len(xs), len(ys), n_tiles, core_w, core_h, tile_w, tile_h,
            tile_w * tile_h / 1e6, size_ref * size_ref / 1e6, ctx_px, ov,
            src_w * _PID_SCALE, src_h * _PID_SCALE)

        # ── 모델/샘플러 준비 (루프 밖에서 1회) ──
        model = _apply_model_shift(pid_ctx["model"], model_shift)
        sigmas = nodes_custom_sampler.BasicScheduler().get_sigmas(
            model, "sgm_uniform", int(steps), 1.0)[0]
        sampler = nodes_custom_sampler.KSamplerSelect().get_sampler("lcm")[0]

        if context_windows and out_h > int(context_length):
            model, applied = _apply_context_windows(
                model, context_length, context_overlap, fuse_method)
            if applied:
                cl, co = int(context_length), int(context_overlap)
                stride = max(1, cl - co)
                n_win = math.ceil((out_h - cl) / stride) + 1
                logger.info(
                    "%s context windows: %d 창 (창 %dx%d = %.2fMP, length %d, "
                    "overlap %d, %s) — 타일당 forward pass 가 %d 배",
                    _TAG_UPSCALE, n_win, out_w, cl, out_w * cl / 1e6, cl, co,
                    fuse_method, n_win)
        elif context_windows:
            logger.info("%s 타일 출력 높이 %d ≤ context_length %d → 창이 1개라 건너뜁니다.",
                        _TAG_UPSCALE, out_h, int(context_length))

        # 패스당 캔버스가 학습 규모를 크게 넘으면 형태 경계 응집력이 떨어진다.
        pass_h = min(out_h, int(context_length)) if context_windows else out_h
        pass_px = out_w * pass_h
        ref_out = size_ref * size_ref * (_PID_SCALE ** 2)
        if pass_px > ref_out * 1.5:
            logger.warning(
                "%s 패스당 캔버스 %.1fMP 가 기준(%.1fMP)의 %.0f%% 입니다. 형태 경계가 "
                "거칠어질 수 있습니다 — context_windows 를 켜거나 context_length 를 "
                "낮추거나 pid_input_size 를 줄이세요.",
                _TAG_UPSCALE, pass_px / 1e6, ref_out / 1e6, pass_px / ref_out * 100)
        else:
            logger.info("%s 패스당 캔버스 %.1fMP (기준 %.1fMP 의 %.0f%%)",
                        _TAG_UPSCALE, pass_px / 1e6, ref_out / 1e6,
                        pass_px / ref_out * 100)

        accum_torch_dtype = torch.float16 if accum_dtype == "fp16" else torch.float32
        use_weight = ov > 0 and n_tiles > 1
        pbar = ProgressBar(n_tiles * batch) if ProgressBar is not None else None

        results = []
        for b in range(batch):
            if use_latent:
                lat_b = src_lat[b:b + 1]
                padded_lat = _pad_nchw(
                    lat_b, pad_l // lf_factor, pad_r // lf_factor,
                    pad_t // lf_factor, pad_b // lf_factor, padding_fill)
                padded_img = None
            else:
                padded_img = _pad_nhwc(
                    src_img[b:b + 1], pad_l, pad_r, pad_t, pad_b, padding_fill)
                padded_lat = None

            canvas = torch.zeros((canvas_h, canvas_w, 3), dtype=accum_torch_dtype)
            weight = (torch.zeros((canvas_h, canvas_w, 1), dtype=torch.float32)
                      if use_weight else None)

            for ri, y in enumerate(ys):
                for ci, x in enumerate(xs):
                    mm.throw_exception_if_processing_interrupted()

                    # 패딩 좌표계에서 타일 crop 시작 = 코어 시작(pad_l == ctx_px 이므로)
                    tile_img = None
                    if use_latent:
                        ls = lf_factor
                        tile_lat = padded_lat[
                            :, :, y // ls: (y + tile_h) // ls,
                            x // ls: (x + tile_w) // ls]
                    else:
                        tile_img = padded_img[:, y: y + tile_h, x: x + tile_w, :]
                        tile_lat = enc_vae.encode(tile_img[..., :3])

                    tile_seed = int(seed) + (ri * len(xs) + ci
                                             if seed_mode == "per_tile" else 0)

                    try:
                        out_tile = self._sample_tile(
                            pid_ctx, model, sampler, sigmas, tile_lat, fmt_cls,
                            degrade_sigma, tile_seed, out_h, out_w, latent_format)
                    except mm.OOM_EXCEPTION:
                        logger.warning("%s 타일 r%dc%d OOM → 캐시 정리 후 1회 재시도",
                                       _TAG_UPSCALE, ri, ci)
                        mm.soft_empty_cache()
                        try:
                            out_tile = self._sample_tile(
                                pid_ctx, model, sampler, sigmas, tile_lat, fmt_cls,
                                degrade_sigma, tile_seed, out_h, out_w)
                        except mm.OOM_EXCEPTION as exc:
                            raise RuntimeError(
                                f"{_TAG_UPSCALE} 타일 하나({tile_w}x{tile_h} → "
                                f"{out_w}x{out_h})가 VRAM 에 들어가지 않습니다. "
                                f"context_windows 를 켜서 패스당 캔버스를 줄이거나, "
                                f"pid_input_size 를 낮추세요.") from exc

                    # 코어 영역만 추출 (컨텍스트는 버림)
                    cs = ctx_px * _PID_SCALE
                    core_out = out_tile[:, cs:cs + core_h * _PID_SCALE,
                                        cs:cs + core_w * _PID_SCALE, :]

                    # per-tile color match
                    if color_match != "off":
                        src_core = None
                        if use_latent:
                            if enc_vae is not None:
                                src_core = enc_vae.decode(tile_lat)
                                if src_core.ndim == 5:
                                    src_core = src_core.reshape(
                                        -1, src_core.shape[-3], src_core.shape[-2],
                                        src_core.shape[-1])
                                src_core = src_core[:, ctx_px:ctx_px + core_h,
                                                    ctx_px:ctx_px + core_w, :]
                            elif ri == 0 and ci == 0 and b == 0:
                                logger.warning(
                                    "%s color_match 를 쓰려면 latent 경로에서도 인코딩 "
                                    "VAE 가 필요합니다(소스 타일 디코드용). 로더의 "
                                    "encode_vae 를 지정하거나 vae_encode 를 연결하세요. "
                                    "건너뜁니다.", _TAG_UPSCALE)
                        else:
                            src_core = tile_img[:, ctx_px:ctx_px + core_h,
                                                ctx_px:ctx_px + core_w, :]
                        if src_core is not None:
                            core_out = _color_match(
                                core_out, src_core, color_match, color_match_strength)

                    # 코어 페이스트 (겹침 밴드에만 feather)
                    ch = core_h * _PID_SCALE
                    cw = core_w * _PID_SCALE
                    oy = (y + ctx_px) * _PID_SCALE
                    ox = (x + ctx_px) * _PID_SCALE
                    patch = core_out[0, :, :, :3].to("cpu", dtype=accum_torch_dtype)

                    if use_weight:
                        fw = ov * _PID_SCALE
                        wy = _axis_weight(ch, fw if ri > 0 else 0,
                                          fw if ri < len(ys) - 1 else 0)
                        wx = _axis_weight(cw, fw if ci > 0 else 0,
                                          fw if ci < len(xs) - 1 else 0)
                        w2d = torch.from_numpy(np.outer(wy, wx)).unsqueeze(-1)
                        canvas[oy:oy + ch, ox:ox + cw, :] += (
                            patch * w2d.to(accum_torch_dtype))
                        weight[oy:oy + ch, ox:ox + cw, :] += w2d
                    else:
                        canvas[oy:oy + ch, ox:ox + cw, :] = patch

                    del out_tile, core_out, patch
                    if pbar is not None:
                        pbar.update(1)

            if use_weight:
                weight.clamp_(min=1e-6)
                canvas.div_(weight.to(canvas.dtype))
                del weight

            # 가상 캔버스 패딩 제거 (×4 비례)
            y0 = pad_t * _PID_SCALE
            x0 = pad_l * _PID_SCALE
            final = canvas[y0: y0 + src_h * _PID_SCALE,
                           x0: x0 + src_w * _PID_SCALE, :]
            results.append(final.float().unsqueeze(0))
            del canvas

        return (torch.cat(results, dim=0),)


NODE_CLASS_MAPPINGS = {
    "BMKPiDLoader": BMKPiDLoader,
    "BMKPiDTiledUpscale": BMKPiDTiledUpscale,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "BMKPiDLoader": "BMK PiD Loader",
    "BMKPiDTiledUpscale": "BMK PiD Tiled Upscale",
}
