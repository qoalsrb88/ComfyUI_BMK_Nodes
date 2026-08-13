"""
BMK Klein Reference Latent Per-SEGS Hook  (v2)
==============================================

DetailerForEach (Detailer (SEGS)) 가 각 타일을 샘플링하기 직전(pre_ksample)에,
그 타일에 맞는 FLUX.2 Klein `reference_latents` 를 per-tile 로 주입한다.

v2 추가: per-tile COLOR MATCH
-----------------------------
타일 업스케일에서 denoise 가 높으면(특히 1.0) 각 타일이 독립적으로 재생성되며
채도/색조가 제각각 드리프트한다. 인접 타일이 서로 다른 방향으로 색이 튀면,
오버랩/crop_pixel 밴드에서 feather 블렌딩으로 색이 누적되어
"보라색으로 진해지거나 채도가 과해지는" seam 이 생긴다.

color_match=True 면 각 타일의 결과(post_decode)를 그 타일의 원본 색 통계
(채널별 mean/std)에 맞춰 되돌려, 드리프트를 타일 단위에서 상쇄한다.
-> denoise 를 높게(디테일 많이) 둬도 오버랩 색 seam 이 사라진다.
   (vae 입력이 연결돼 있어야 동작. 원본 latent 1회 decode 비용 추가.)

모드
----
- self   (기본): 각 타일이 자기 자신의 깨끗한 encode latent 를 reference 로 사용.
- source       : 외부 reference_image 를 seg crop 으로 잘라 인코딩해 reference 로 사용.

설치: ComfyUI_BMK_Nodes 에 추가하거나 custom_nodes 에 단독 배치.
"""

import torch
import torch.nn.functional as F


# ---------------------------------------------------------------------------
# reference_latents 주입 헬퍼
# ---------------------------------------------------------------------------
def _conditioning_set_values(conditioning, values, append=False):
    try:
        import node_helpers
        return node_helpers.conditioning_set_values(conditioning, values, append=append)
    except Exception:
        c = []
        for t in conditioning:
            n = [t[0], t[1].copy()]
            for k, v in values.items():
                if append:
                    old = n[1].get(k, None)
                    if old is not None:
                        v = old + v
                n[1][k] = v
            c.append(n)
        return c


def _apply_reference(conditioning, samples, replace_existing=True):
    if conditioning is None or samples is None:
        return conditioning
    return _conditioning_set_values(
        conditioning,
        {"reference_latents": [samples]},
        append=not replace_existing,
    )


# ---------------------------------------------------------------------------
# per-tile color match (채널별 mean/std 매칭 = reinhard 계열, strength 블렌드)
# out, src : torch [B,H,W,C], 0..1 / RGB 3채널 기준
# ---------------------------------------------------------------------------
def _match_color(out, src, strength):
    if strength <= 0.0:
        return out
    eps = 1e-5
    # 공간 크기 다르면 src 를 out 에 맞춰 리사이즈(보통은 동일)
    if src.shape[1:3] != out.shape[1:3]:
        s = src.movedim(-1, 1)
        s = F.interpolate(s, size=(out.shape[1], out.shape[2]),
                          mode="bilinear", align_corners=False)
        src = s.movedim(1, -1)

    o = out[..., :3]
    s = src[..., :3]
    om = o.mean(dim=(1, 2), keepdim=True)
    osd = o.std(dim=(1, 2), keepdim=True)
    sm = s.mean(dim=(1, 2), keepdim=True)
    ssd = s.std(dim=(1, 2), keepdim=True)

    matched = (o - om) / (osd + eps) * ssd + sm
    matched = matched.clamp(0.0, 1.0)

    result = out.clone()
    result[..., :3] = (o * (1.0 - strength) + matched * strength).clamp(0.0, 1.0)
    return result


# ---------------------------------------------------------------------------
# Impact Pack DetailerHook lazy 상속
# ---------------------------------------------------------------------------
_HOOK_CLS = None


def _build_hook_class():
    global _HOOK_CLS
    if _HOOK_CLS is not None:
        return _HOOK_CLS

    from impact.hooks import DetailerHook

    class _BMKKleinRefHook(DetailerHook):
        def __init__(self, mode="self", apply_to_negative=False, replace_existing=True,
                     color_match=False, color_match_strength=1.0,
                     reference_image=None, vae=None):
            super().__init__()
            self.mode = mode
            self.apply_to_negative = apply_to_negative
            self.replace_existing = replace_existing
            self.color_match = color_match
            self.color_match_strength = float(color_match_strength)
            self.reference_image = reference_image
            self.vae = vae

            self._crop_region = None       # source 모드: seg crop (x1,y1,x2,y2)
            self._scaled_size = None       # source 모드: 타일 작업 px (w,h)
            self._src_samples = None       # color match: 이 타일 원본 latent 캐시

        # seg crop 영역 캐시 (source 모드)
        def post_crop_region(self, w, h, item_bbox, crop_region):
            self._crop_region = crop_region
            return crop_region

        # 타일 작업 해상도 캐시 (source 모드)
        def touch_scaled_size(self, w, h):
            self._scaled_size = (w, h)
            return w, h

        # 타일 KSampler 직전: reference 주입 + 원본 latent 캐시
        def pre_ksample(self, model, seed, steps, cfg, sampler_name, scheduler,
                        positive, negative, upscaled_latent, denoise):
            # color match 용으로 "이 타일의 원본 latent" 를 항상 캐시
            if self.color_match:
                try:
                    self._src_samples = upscaled_latent["samples"].detach().clone()
                except Exception:
                    self._src_samples = None

            try:
                ref_samples = self._resolve_reference(upscaled_latent)
            except Exception as e:
                print(f"[BMKKleinRefHook] reference 생성 실패, reference 없이 진행: {e}")
                ref_samples = None

            if ref_samples is not None:
                positive = _apply_reference(positive, ref_samples, self.replace_existing)
                if self.apply_to_negative:
                    negative = _apply_reference(negative, ref_samples, self.replace_existing)

            return (model, seed, steps, cfg, sampler_name, scheduler,
                    positive, negative, upscaled_latent, denoise)

        # 타일 decode 직후: 원본 색 통계로 매칭하여 드리프트 상쇄
        def post_decode(self, pixels):
            if not self.color_match:
                return pixels
            if self.vae is None or self._src_samples is None:
                if self.vae is None:
                    print("[BMKKleinRefHook] color_match=True 이지만 vae 미연결 → 색 매칭 건너뜀")
                return pixels
            try:
                src_pixels = self.vae.decode(self._src_samples)
                # comfy vae.decode -> [B,H,W,C]; 혹시 5D(영상)면 첫 프레임만
                if src_pixels.dim() == 5:
                    src_pixels = src_pixels[:, 0]
                out = _match_color(pixels, src_pixels.to(pixels.device),
                                   self.color_match_strength)
                return out
            except Exception as e:
                print(f"[BMKKleinRefHook] color match 실패, 원본 결과 유지: {e}")
                return pixels
            finally:
                self._src_samples = None  # 타일별 캐시 정리

        # ------------------------------------------------------------------
        def _resolve_reference(self, upscaled_latent):
            tile_samples = upscaled_latent["samples"]

            if self.mode != "source" or self.reference_image is None or self.vae is None:
                return tile_samples.detach().clone()

            if self._crop_region is None:
                return tile_samples.detach().clone()

            x1, y1, x2, y2 = self._crop_region
            crop = self.reference_image[:, y1:y2, x1:x2, :]

            tw, th = self._scaled_size if self._scaled_size is not None else (None, None)
            if tw and th:
                crop = crop.movedim(-1, 1)
                crop = F.interpolate(crop, size=(th, tw), mode="bilinear", align_corners=False)
                crop = crop.movedim(1, -1)

            ref_latent = self.vae.encode(crop[:, :, :, :3])
            if ref_latent.shape[-2:] != tile_samples.shape[-2:]:
                ref_latent = F.interpolate(ref_latent, size=tile_samples.shape[-2:],
                                           mode="bilinear", align_corners=False)
            return ref_latent.detach().clone()

    _HOOK_CLS = _BMKKleinRefHook
    return _HOOK_CLS


# ---------------------------------------------------------------------------
# Provider 노드
# ---------------------------------------------------------------------------
class BMKKleinReferenceLatentSEGSHook:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "mode": (["self", "source"], {"default": "self"}),
                "apply_to_negative": ("BOOLEAN", {"default": False}),
                "replace_existing": ("BOOLEAN", {"default": True}),
                "color_match": ("BOOLEAN", {"default": False}),
                "color_match_strength": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 1.0, "step": 0.05}),
            },
            "optional": {
                # source 모드 reference 소스 / color_match 시 원본 decode 에 필요
                "reference_image": ("IMAGE",),
                "vae": ("VAE",),
            },
        }

    RETURN_TYPES = ("DETAILER_HOOK",)
    RETURN_NAMES = ("detailer_hook",)
    FUNCTION = "build"
    CATEGORY = "BMK/detailer"
    DESCRIPTION = (
        "DetailerForEach가 각 타일을 샘플링하기 직전에, 그 타일에 맞는 FLUX.2 Klein "
        "reference_latents를 per-tile로 주입하는 DETAILER_HOOK입니다. "
        "color_match를 켜면 타일 결과를 원본 색 통계(채널별 mean/std)에 맞춰 되돌려, "
        "denoise를 높게 둬도 오버랩 색 seam이 생기지 않습니다(vae 입력 필요)."
    )
    SEARCH_ALIASES = [
        "klein reference", "reference latent", "detailer hook", "flux klein",
        "color match", "레퍼런스 latent", "디테일러 훅", "색 보정",
    ]

    def build(self, mode, apply_to_negative, replace_existing,
              color_match, color_match_strength,
              reference_image=None, vae=None):
        HookCls = _build_hook_class()
        hook = HookCls(
            mode=mode,
            apply_to_negative=apply_to_negative,
            replace_existing=replace_existing,
            color_match=color_match,
            color_match_strength=color_match_strength,
            reference_image=reference_image,
            vae=vae,
        )
        return (hook,)


NODE_CLASS_MAPPINGS = {
    "BMKKleinReferenceLatentSEGSHook": BMKKleinReferenceLatentSEGSHook,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "BMKKleinReferenceLatentSEGSHook": "BMK Klein Reference Latent Per-SEGS Hook",
}
