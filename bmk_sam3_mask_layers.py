"""BMK SAM3 Mask Layers — SAM3 텍스트 감지 결과를 레이어(행) 단위로 더하고 빼서 대상 마스크를 만들고, 색상 덧칠·투명화 결과와 원본 복원용 stitch 를 한 번에 내는 노드 + 짝 BMK Mask Stitch.

배경
----
피부처럼 여러 부위로 나뉘는 대상을 SAM3 로 가리려면 "프롬프트 → SAM3 Detect → 마스크 보정" 체인을
부위마다 만들고 MaskComposite(invert + multiply / add) 로 합친 뒤, 색 합성과 알파 합성을 따로 붙여야
했다. 이 노드는 체크포인트 로드까지 포함해 그 전부를 레이어 목록 하나로 접는다.

    acc = 0
    레이어를 위에서 아래로:  + 레이어 → acc = max(acc, m)
                              − 레이어 → acc = clamp(acc − m, 0)

포토샵 선택 영역 더하기/빼기와 같아서 순서가 결과를 바꾼다. 예) + body → − face → + lips 는
얼굴을 빼되 입술은 다시 넣는다.

레이어 한 행의 값 (layers 위젯 = JSON 배열, 프론트 행 편집기가 관리)
------------------------------------------------------------------
- op        : "+" 더하기 / "-" 빼기
- prompt    : SAM3 텍스트 프롬프트. 코어 문법 그대로 — "thigh skin:6" (최대 6개 감지),
              "arm, leg:2" (한 행에 여러 범주 = 합집합). 괄호는 무시된다.
- threshold : 감지 점수 하한(0~1).
- grow      : 확장(+)/축소(−) px. 정사각 커널(MaskEnhancer 의 mask_offset 과 같은 모양).
- blur      : 가우시안 페더 σ(px). grow 다음에 적용 — 경계를 정한 뒤 부드럽게 만든다.
- on        : 끄면 행을 지우지 않고 건너뛴다(A/B 비교).
- id        : 프론트가 붙이는 행 식별자(감지 개수 표시·프리뷰 행 강조용). 계산에는 쓰지 않는다.
prompt 가 비었거나 on 이 꺼진 행은 건너뛴다.

전역 옵션
---------
- ckpt_name         : SAM3 / SAM3.1 체크포인트(checkpoints 폴더). 목록은 파일명에 "sam3" 가 든 것만 보이고,
                      하나도 없으면 전체를 보인다. 감지할 레이어가 캐시에 없을 때만 불러오며(blur 등 후처리만
                      바꾼 실행은 모델을 건드리지 않음), 불러온 모델 하나를 다음 실행에 재사용한다.
                      다른 파일을 고르거나 파일이 바뀌면(mtime) 교체한다.
- refine_iterations : SAM 디코더 보정 횟수(코어 SAM3 Detect 와 같음). 0 = 감지기 마스크 그대로.
- fill_color / fill_opacity : image 출력에서 대상 영역을 칠할 색과 불투명도.

출력
----
- image      : 대상 영역을 fill_color 로 덧칠한 RGB.
- image_rgba : 대상 영역 알파 0 인 RGBA (알파 = 1 − mask).
- mask       : 최종 대상 마스크(1 = 대상). JoinImageWithAlpha·GPT 노드 mask 와 같은 의미.
- stitch     : BMK_MASK_STITCH (원본 이미지 + mask). BMK Mask Stitch 에 연결해 처리 후 대상 영역만
               원본 픽셀로 되돌린다.
- 프리뷰     : 레이어별 오버레이(+ 초록 / − 빨강, 첫 프레임) 뒤에 프레임별 최종 덧칠 결과.
               기본 다중 이미지 프리뷰(x = 격자 보기, n/N = 다음 장)로 넘겨 본다.

성능
----
- SAM3 비전 트렁크(ViT)는 프레임당 한 번만 돌리고 모든 레이어가 공유한다(코어 영상 추적과 같은
  detector.forward_from_trunk 경로). 코어 SAM3 Detect 를 행마다 두면 트렁크가 행 수만큼 돈다.
- 감지 결과 캐시: (체크포인트, 이미지, prompt, threshold, refine_iterations) 가 같은 레이어는 SAM3 를
  다시 돌리지 않는다. ComfyUI 캐시는 노드 단위라 blur 하나만 바꿔도 노드 전체가 다시 실행되기 때문이다.
  grow / blur / op / 순서 / on / 색은 후처리만 다시 한다. 이진 마스크를 CPU 에 최대 32개 보관
  (832×1216 한 장 ≈ 1MB).

BMK Mask Stitch
---------------
처리된 image 에 stitch 의 원본을 mask 영역만 되돌린다: out = image·(1−m) + 원본·m.
- 해상도가 다르면 원본과 mask 를 image 크기로 리샘플한다(출력 = image 해상도). Canvas Snap Restore 는
  원본이 아니라 원본×s 크기를 돌려주므로 이 경로가 기본이다. 종횡비가 1% 넘게 다르면 경고하고 늘려 붙인다.
- grow / feather : 출력 해상도 기준 px. 덧칠 경계의 반투명 띠가 처리 후 어두운 테두리로 남으면 grow 1~2.
- 채널 자동 정합(BMK Crop Stitch v3 와 같은 규칙): image 가 RGBA(GPT 출력 등)면 원본에 불투명 알파를
  붙이고, image 가 RGB 면 원본 알파를 뗀다.
- 배치: image 프레임마다 원본·mask 를 순환 사용.

v1 (2026-10)
------------
- 최초 구현. 테스트 워크플로우(Load Checkpoint + CLIPTextEncode → SAM3 Detect → AILab MaskEnhancer ×3 +
  MaskComposite + 색 합성 2단 + JoinImageWithAlpha) 대체. 차이: grow → blur 순서(MaskEnhancer 는
  blur → offset), 합집합은 max(테스트는 1−(1−a)(1−b)). 이진 영역은 같고 경계 1~2px 만 다르다.
- 체크포인트 로더 내장(model/clip 입력 대신 ckpt_name). 사용 모델이 사실상 고정이라 노드 하나를 줄이고,
  감지가 필요 없는 실행에서는 모델을 불러오지 않게 했다.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
from collections import OrderedDict

import torch
import torch.nn.functional as F

import comfy.model_base
import comfy.model_management
import comfy.sd
import comfy.utils
import folder_paths
import nodes as core_nodes
from comfy_extras import nodes_sam3

logger = logging.getLogger(__name__)

_TAG = "[ComfyUI_BMK_Nodes::SAM3MaskLayers]"
_TAG_STITCH = "[ComfyUI_BMK_Nodes::MaskStitch]"

LAYERS_TYPE = "BMK_SAM3_LAYERS"
STITCH_TYPE = "BMK_MASK_STITCH"

_RESIZE_ALGOS = ["lanczos", "bicubic", "bilinear", "area", "nearest-exact"]
_PREVIEW_TINT = {"+": (0.25, 0.85, 0.35), "-": (0.95, 0.30, 0.30)}
_PREVIEW_ALPHA = 0.6
_CACHE_CAPACITY = 32

# 감지 캐시: key → (bool 마스크 [B,H,W] CPU, 감지 개수). key 첫 항목이 체크포인트 (경로, mtime) 라
# 모델이 바뀌면 자연히 빗나간다.
_cache: OrderedDict = OrderedDict()
# 불러온 SAM3 체크포인트 하나: ((경로, mtime), model, clip). 다른 파일을 고르면 교체.
_loaded = None


def _sam3_checkpoints():
    names = folder_paths.get_filename_list("checkpoints")
    return [n for n in names if "sam3" in n.lower()] or names


def _load_sam3(ckpt):
    """ckpt = (경로, mtime). 같은 파일이면 이전에 불러온 model/clip 을 그대로 쓴다."""
    global _loaded
    if _loaded is None or _loaded[0] != ckpt:
        _loaded = None  # 교체할 때 이전 모델을 먼저 놓아 두 벌이 동시에 메모리에 있지 않게
        model, clip = comfy.sd.load_checkpoint_guess_config(
            ckpt[0], output_vae=False, output_clip=True, embedding_directory=folder_paths.get_folder_paths("embeddings")
        )[:2]
        if not isinstance(model.model, comfy.model_base.SAM3):
            raise ValueError(f"{_TAG} {os.path.basename(ckpt[0])} 은(는) SAM3 / SAM3.1 체크포인트가 아닙니다.")
        _loaded = (ckpt, model, clip)
    return _loaded[1], _loaded[2]


def _parse_layers(layers_json):
    try:
        rows = json.loads(layers_json or "[]")
    except json.JSONDecodeError as e:
        raise ValueError(f"{_TAG} layers 값이 올바른 JSON 이 아닙니다: {e}") from e
    if not isinstance(rows, list):
        raise ValueError(f"{_TAG} layers 는 레이어 목록(JSON 배열)이어야 합니다.")

    layers = []
    for i, row in enumerate(rows):
        prompt = str(row.get("prompt", "")).strip()
        if not prompt or not row.get("on", True):
            continue
        layers.append({
            "id": str(row.get("id", i)),
            "op": "-" if row.get("op") == "-" else "+",
            "prompt": prompt,
            "threshold": float(row.get("threshold", 0.3)),
            "grow": int(row.get("grow", 0)),
            "blur": float(row.get("blur", 0)),
        })
    return layers


def _hex_rgb(color):
    s = str(color).strip().lstrip("#")
    return tuple(int(s[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def _grow(m, px):
    """[B,H,W] 마스크를 정사각 커널로 확장(px>0)/축소(px<0). 가로·세로 분리 max-pool."""
    if px == 0:
        return m
    r = abs(px)
    x = m.unsqueeze(1)
    if px < 0:
        x = -x
    x = F.max_pool2d(x, (2 * r + 1, 1), stride=1, padding=(r, 0))
    x = F.max_pool2d(x, (1, 2 * r + 1), stride=1, padding=(0, r))
    if px < 0:
        x = -x
    return x.squeeze(1)


def _blur(m, sigma):
    """[B,H,W] 마스크 가우시안 블러(σ=sigma px, 가장자리 복제 패딩)."""
    if sigma <= 0:
        return m
    r = max(1, math.ceil(sigma * 3))
    x = torch.arange(-r, r + 1, dtype=m.dtype, device=m.device)
    k = torch.exp(-(x * x) / (2.0 * sigma * sigma))
    k = k / k.sum()
    t = F.pad(m.unsqueeze(1), (r, r, r, r), mode="replicate")
    t = F.conv2d(t, k.view(1, 1, 1, -1))
    t = F.conv2d(t, k.view(1, 1, -1, 1))
    return t.squeeze(1)


def _image_digest(rgb):
    h = hashlib.blake2b(digest_size=16)
    h.update(repr(tuple(rgb.shape)).encode())
    h.update(rgb.contiguous().cpu().numpy().tobytes())
    return h.hexdigest()


def _run_sam3(model, clip, image, prompts, refine_iterations):
    """prompts = [(prompt, threshold), ...] 각각의 이진 마스크 [B,H,W](CPU)와 감지 개수.

    트렁크는 프레임당 한 번만 돌리고 모든 프롬프트가 공유한다. 감지 후처리(점수 → 상위 max_det →
    원본 크기 보간 → _refine_mask)는 코어 SAM3_Detect 와 같다.
    """
    conds = [clip.encode_from_tokens_scheduled(clip.tokenize(p)) for p, _ in prompts]

    comfy.model_management.load_model_gpu(model)
    device = comfy.model_management.get_torch_device()
    dtype = model.model.get_dtype()
    sam3_model = model.model.diffusion_model
    detector = sam3_model.detector
    resizer = detector.backbone["language_backbone"]["resizer"]
    trunk_fn = detector.backbone["vision_backbone"].trunk

    text_prompts = [
        [(resizer(emb), mask, max_det) for emb, mask, max_det in nodes_sam3._extract_text_prompts(c, device, dtype)]
        for c in conds
    ]

    B, H, W, _ = image.shape
    image_in = comfy.utils.common_upscale(image[..., :3].movedim(-1, 1), 1008, 1008, "bilinear", crop="disabled")
    masks = [torch.zeros(B, H, W, dtype=torch.bool) for _ in prompts]
    counts = [0] * len(prompts)
    pbar = comfy.utils.ProgressBar(B * len(prompts))

    for b in range(B):
        trunk = trunk_fn(image_in[b:b + 1].to(device=device, dtype=dtype))
        for i, (_, threshold) in enumerate(prompts):
            acc = torch.zeros(H, W, dtype=torch.bool, device=device)
            for emb, text_mask, max_det in text_prompts[i]:
                det = detector.forward_from_trunk(trunk, emb, text_mask)
                probs = det["scores"][0].sigmoid()
                idx = torch.nonzero(probs > threshold).squeeze(1)
                idx = idx[probs[idx].argsort(descending=True)[:max_det]]
                if idx.numel() == 0:
                    continue
                boxes = det["boxes"][0][idx]
                boxes = boxes * torch.tensor([W, H, W, H], device=boxes.device, dtype=boxes.dtype)
                coarse = F.interpolate(det["masks"][:, idx], size=(H, W), mode="bilinear", align_corners=False)[0]
                for m, box in zip(coarse, boxes):
                    refined = nodes_sam3._refine_mask(sam3_model, image[b], m, box, H, W, device, dtype, refine_iterations)
                    acc |= refined[0] > 0
                counts[i] += idx.numel()
            masks[i][b] = acc.cpu()
            pbar.update(1)
    return masks, counts


def _detect(ckpt_name, image, layers, refine_iterations):
    """레이어별 (원시 이진 마스크, 감지 개수). 캐시에 없는 (prompt, threshold) 가 있을 때만 체크포인트를
    불러와 SAM3 를 돌린다."""
    path = folder_paths.get_full_path_or_raise("checkpoints", ckpt_name)
    ckpt = (path, os.path.getmtime(path))
    digest = _image_digest(image[..., :3])
    keys = [(ckpt, digest, l["prompt"], l["threshold"], refine_iterations) for l in layers]

    found = {k: _cache[k] for k in keys if k in _cache}
    missing = [k for k in dict.fromkeys(keys) if k not in found]
    if missing:
        model, clip = _load_sam3(ckpt)
        masks, counts = _run_sam3(model, clip, image, [(k[2], k[3]) for k in missing], refine_iterations)
        for k, m, c in zip(missing, masks, counts):
            found[k] = (m, c)
            _cache[k] = (m, c)
    for k in keys:
        _cache.move_to_end(k)
    while len(_cache) > _CACHE_CAPACITY:
        _cache.popitem(last=False)
    logger.debug("%s 감지 %d개 중 캐시 적중 %d개", _TAG, len(set(keys)), len(set(keys)) - len(missing))
    return [found[k] for k in keys]


class BMKSAM3MaskLayers:
    """SAM3 감지 레이어를 순서대로 더하고 빼서 대상 마스크·덧칠·투명화·stitch 를 한 번에 낸다."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "ckpt_name": (
                    _sam3_checkpoints(),
                    {
                        "tooltip": (
                            "SAM3 / SAM3.1 체크포인트(파일명에 'sam3' 가 든 것만 표시). "
                            "감지가 필요할 때만 불러오고 다음 실행에 재사용한다."
                        ),
                    },
                ),
                "refine_iterations": (
                    "INT",
                    {
                        "default": 0,
                        "min": 0,
                        "max": 5,
                        "tooltip": "SAM 디코더 보정 횟수(코어 SAM3 Detect 와 같음). 0 = 감지기 마스크 그대로.",
                    },
                ),
                "fill_color": ("COLOR", {"default": "#000000", "tooltip": "image 출력에서 대상 영역을 칠할 색."}),
                "fill_opacity": (
                    "FLOAT",
                    {
                        "default": 0.9,
                        "min": 0.0,
                        "max": 1.0,
                        "step": 0.01,
                        "tooltip": "덧칠 불투명도. 1 = 완전히 덮음.",
                    },
                ),
                "layers": (
                    LAYERS_TYPE,
                    {
                        "default": "[]",
                        "tooltip": (
                            "레이어는 위에서 아래로 적용된다(+ 더하기 / − 빼기). "
                            "프롬프트 'thigh skin:6' = 최대 6개 감지, 'arm, leg' = 한 행에 여러 범주."
                        ),
                    },
                ),
            },
        }

    RETURN_TYPES = ("IMAGE", "IMAGE", "MASK", STITCH_TYPE)
    RETURN_NAMES = ("image", "image_rgba", "mask", "stitch")
    OUTPUT_TOOLTIPS = (
        "대상 영역을 fill_color 로 덧칠한 RGB.",
        "대상 영역 알파 0 인 RGBA.",
        "최종 대상 마스크(1 = 대상).",
        "BMK Mask Stitch 용 원본 + 마스크.",
    )
    FUNCTION = "run"
    CATEGORY = "BMK/Image"
    DESCRIPTION = (
        "SAM3 텍스트 감지 레이어를 위에서 아래로 더하고(+) 빼서(−) 대상 마스크를 만들고, "
        "대상 영역을 색으로 덧칠한 이미지·투명화한 RGBA·마스크·원본 복원용 stitch 를 한 번에 출력합니다. "
        "SAM3 체크포인트 로더를 내장했고, 감지 결과를 캐시해 blur·grow·순서만 바꾸면 모델을 다시 돌리지 않습니다."
    )
    SEARCH_ALIASES = [
        "sam3",
        "segment anything",
        "mask layers",
        "multi detect",
        "skin mask",
        "censor",
        "마스크 레이어",
        "다중 감지",
        "피부 마스킹",
        "가리기",
    ]

    def run(self, image, ckpt_name, refine_iterations, fill_color, fill_opacity, layers):
        parsed = _parse_layers(layers)
        rgb = image[..., :3]
        B, H, W, _ = rgb.shape

        detected = _detect(ckpt_name, image, parsed, refine_iterations) if parsed else []

        mask = torch.zeros(B, H, W, dtype=rgb.dtype, device=rgb.device)
        processed = []
        for (raw, _), layer in zip(detected, parsed):
            m = raw.to(device=rgb.device, dtype=rgb.dtype)
            m = _blur(_grow(m, layer["grow"]), layer["blur"]).clamp_(0.0, 1.0)
            processed.append(m)
            if layer["op"] == "+":
                mask = torch.maximum(mask, m)
            else:
                mask = (mask - m).clamp_(min=0.0)

        color = torch.tensor(_hex_rgb(fill_color), dtype=rgb.dtype, device=rgb.device)
        a = (mask * fill_opacity).unsqueeze(-1)
        filled = rgb * (1.0 - a) + color * a
        rgba = torch.cat([rgb, (1.0 - mask).unsqueeze(-1)], dim=-1)
        stitch = {"version": 1, "image": image, "mask": mask}

        pages = []
        for m, layer in zip(processed, parsed):
            tint = torch.tensor(_PREVIEW_TINT[layer["op"]], dtype=rgb.dtype, device=rgb.device)
            t = (m[0] * _PREVIEW_ALPHA).unsqueeze(-1)
            pages.append(rgb[0] * (1.0 - t) + tint * t)
        pages.extend(filled)
        preview = core_nodes.PreviewImage().save_images(torch.stack(pages), filename_prefix="BMK_SAM3Layers")

        ui = {
            "images": preview["ui"]["images"],
            "bmk_sam3_layers": [{"id": l["id"], "count": c} for l, (_, c) in zip(parsed, detected)],
        }
        return {"ui": ui, "result": (filled, rgba, mask, stitch)}


class BMKMaskStitch:
    """stitch 의 원본을 처리된 이미지의 mask 영역에만 되돌린다."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE", {"tooltip": "처리된 결과(예: Canvas Snap Restore 출력). 출력은 이 해상도."}),
                "stitch": (STITCH_TYPE, {"tooltip": "BMK SAM3 Mask Layers 의 stitch 출력."}),
                "grow": (
                    "INT",
                    {
                        "default": 0,
                        "min": -64,
                        "max": 64,
                        "tooltip": "되돌릴 영역 확장(+)/축소(−) px(출력 해상도 기준). 덧칠 경계가 남으면 1~2.",
                    },
                ),
                "feather": (
                    "INT",
                    {
                        "default": 0,
                        "min": 0,
                        "max": 64,
                        "tooltip": "되돌릴 영역 경계 가우시안 페더 σ(px, 출력 해상도 기준).",
                    },
                ),
                "resize_algorithm": (
                    _RESIZE_ALGOS,
                    {"default": "lanczos", "tooltip": "image 와 해상도가 다를 때 원본 리샘플 방법."},
                ),
            },
        }

    RETURN_TYPES = ("IMAGE", "MASK")
    RETURN_NAMES = ("image", "mask")
    OUTPUT_TOOLTIPS = ("대상 영역만 원본으로 되돌린 이미지.", "실제로 되돌린 영역(출력 해상도).")
    FUNCTION = "stitch"
    CATEGORY = "BMK/Image"
    DESCRIPTION = (
        "BMK SAM3 Mask Layers 의 stitch 를 받아, 처리된 이미지에서 감지 영역만 원본 픽셀로 되돌립니다. "
        "해상도가 다르면 원본과 마스크를 처리된 이미지 크기로 맞추고, RGB/RGBA 채널 수도 자동으로 맞춥니다."
    )
    SEARCH_ALIASES = [
        "mask stitch",
        "restore original",
        "paste back",
        "unmask",
        "스티치",
        "원본 복원",
        "마스크 복원",
    ]

    def stitch(self, image, stitch, grow, feather, resize_algorithm):
        if not isinstance(stitch, dict) or stitch.get("version") != 1:
            raise ValueError(f"{_TAG_STITCH} 지원하지 않는 stitch 형식입니다. BMK SAM3 Mask Layers 의 stitch 출력을 연결하세요.")

        B, H, W, C = image.shape
        orig = stitch["image"].to(device=image.device, dtype=image.dtype)
        mask = stitch["mask"].to(device=image.device, dtype=image.dtype)

        oc = int(orig.shape[3])
        if oc == 3 and C == 4:
            orig = torch.cat([orig, torch.ones_like(orig[..., :1])], dim=-1)
        elif oc == 4 and C == 3:
            orig = orig[..., :3]
        elif oc != C:
            raise ValueError(f"{_TAG_STITCH} 지원하지 않는 채널 조합입니다: image={C}ch, 원본={oc}ch")

        oh, ow = int(orig.shape[1]), int(orig.shape[2])
        if (oh, ow) != (H, W):
            if abs((W / H) / (ow / oh) - 1.0) > 0.01:
                logger.warning(
                    "%s 원본(%dx%d)과 image(%dx%d)의 종횡비가 달라 원본을 늘려 붙입니다. "
                    "Canvas Snap 을 거쳤다면 Restore 출력을 연결하세요.",
                    _TAG_STITCH, ow, oh, W, H,
                )
            orig = comfy.utils.common_upscale(orig.movedim(-1, 1), W, H, resize_algorithm, "disabled").movedim(1, -1)
            mask = F.interpolate(mask.unsqueeze(1), size=(H, W), mode="bilinear", align_corners=False).squeeze(1)

        mask = _blur(_grow(mask, grow), feather).clamp_(0.0, 1.0)

        frames = []
        masks = []
        for i in range(B):
            m = mask[i % mask.shape[0]]
            frames.append(image[i] * (1.0 - m.unsqueeze(-1)) + orig[i % orig.shape[0]] * m.unsqueeze(-1))
            masks.append(m)
        return (torch.stack(frames), torch.stack(masks))


NODE_CLASS_MAPPINGS = {
    "BMKSAM3MaskLayers": BMKSAM3MaskLayers,
    "BMKMaskStitch": BMKMaskStitch,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "BMKSAM3MaskLayers": "BMK SAM3 Mask Layers",
    "BMKMaskStitch": "BMK Mask Stitch",
}
