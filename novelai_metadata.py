from __future__ import annotations

import hashlib
import html
import json
from collections import deque
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple

from PIL import Image, UnidentifiedImageError

try:
    import folder_paths
except Exception:
    folder_paths = None


# ComfyUI_NAIDGenerator ModelOption ✒️🅝🅐🅘 combo choices.
# Use the same concrete combo list as the target input type.
NAID_MODELS = [
    "nai-diffusion-2",
    "nai-diffusion-furry-3",
    "nai-diffusion-3",
    "nai-diffusion-4-curated-preview",
    "nai-diffusion-4-full",
    "nai-diffusion-4-5-curated",
    "nai-diffusion-4-5-full",
]

# ComfyUI_NAIDGenerator Generate ✒️🅝🅐🅘 combo choices.
# These lists are intentionally used directly in RETURN_TYPES so the output
# socket type matches NAIDGenerator's concrete combo input type list.
NAID_SAMPLERS = [
    "k_euler",
    "k_euler_ancestral",
    "k_dpmpp_2s_ancestral",
    "k_dpmpp_2m_sde",
    "k_dpmpp_2m",
    "k_dpmpp_sde",
    "ddim",
]

NAID_SCHEDULERS = [
    "native",
    "karras",
    "exponential",
    "polyexponential",
]

MODEL_ALIASES = {
    "nai-diffusion-2": "nai-diffusion-2",
    "nai diffusion 2": "nai-diffusion-2",
    "nai_diffusion_2": "nai-diffusion-2",
    "nai-diffusion-furry-3": "nai-diffusion-furry-3",
    "nai diffusion furry 3": "nai-diffusion-furry-3",
    "nai_diffusion_furry_3": "nai-diffusion-furry-3",
    "nai-diffusion-3": "nai-diffusion-3",
    "nai diffusion 3": "nai-diffusion-3",
    "nai_diffusion_3": "nai-diffusion-3",
    "nai-diffusion-4-curated-preview": "nai-diffusion-4-curated-preview",
    "nai diffusion 4 curated preview": "nai-diffusion-4-curated-preview",
    "nai_diffusion_4_curated_preview": "nai-diffusion-4-curated-preview",
    "nai-diffusion-4-full": "nai-diffusion-4-full",
    "nai diffusion 4 full": "nai-diffusion-4-full",
    "nai_diffusion_4_full": "nai-diffusion-4-full",
    "nai-diffusion-4-5-curated": "nai-diffusion-4-5-curated",
    "nai diffusion 4 5 curated": "nai-diffusion-4-5-curated",
    "nai_diffusion_4_5_curated": "nai-diffusion-4-5-curated",
    "nai-diffusion-4-5-full": "nai-diffusion-4-5-full",
    "nai diffusion 4 5 full": "nai-diffusion-4-5-full",
    "nai_diffusion_4_5_full": "nai-diffusion-4-5-full",
}

SAMPLER_ALIASES = {
    "euler": "k_euler",
    "k_euler": "k_euler",
    "euler_ancestral": "k_euler_ancestral",
    "euler ancestral": "k_euler_ancestral",
    "k_euler_ancestral": "k_euler_ancestral",
    "k_euler_a": "k_euler_ancestral",
    "dpmpp_2s_ancestral": "k_dpmpp_2s_ancestral",
    "k_dpmpp_2s_ancestral": "k_dpmpp_2s_ancestral",
    "dpmpp_2m_sde": "k_dpmpp_2m_sde",
    "k_dpmpp_2m_sde": "k_dpmpp_2m_sde",
    "dpmpp_2m": "k_dpmpp_2m",
    "k_dpmpp_2m": "k_dpmpp_2m",
    "dpmpp_sde": "k_dpmpp_sde",
    "k_dpmpp_sde": "k_dpmpp_sde",
    "ddim": "ddim",
    "ddim_v3": "ddim",
}

SCHEDULER_ALIASES = {
    "native": "native",
    "karras": "karras",
    "exponential": "exponential",
    "polyexponential": "polyexponential",
    "poly_exponential": "polyexponential",
    "poly exponential": "polyexponential",
}


class BMKNovelAIMetadataExtractor:
    CATEGORY = "BMK Nodes/NovelAI"
    FUNCTION = "extract"
    DESCRIPTION = "Extracts NovelAI PNG metadata from a Load Image connection."
    SEARCH_ALIASES = [
        "bmk",
        "nai",
        "novelai",
        "novel ai",
        "metadata",
        "extract",
        "nai extract",
    ]

    RETURN_TYPES = (
        NAID_MODELS,      # model
        "INT",           # width
        "INT",           # height
        "STRING",        # positive
        "STRING",        # negative
        "INT",           # steps
        "FLOAT",         # cfg
        NAID_SAMPLERS,    # sampler
        NAID_SCHEDULERS,  # scheduler
        "INT",           # seed
        "FLOAT",         # cfg_rescale
        "STRING",        # json
    )

    RETURN_NAMES = (
        "model",
        "width",
        "height",
        "positive",
        "negative",
        "steps",
        "cfg",
        "sampler",
        "scheduler",
        "seed",
        "cfg_rescale",
        "json",
    )

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
            },
            "hidden": {
                "prompt": "PROMPT",
                "unique_id": "UNIQUE_ID",
            },
        }

    @classmethod
    def IS_CHANGED(cls, image=None, prompt=None, unique_id=None, **kwargs):
        try:
            path = _resolve_or_discover_image_path(prompt, unique_id)

            digest = hashlib.sha256()
            with open(path, "rb") as f:
                for block in iter(lambda: f.read(1024 * 1024), b""):
                    digest.update(block)
            return digest.hexdigest()

        except Exception:
            # Do not return float("nan") here.
            # In ComfyUI, NaN is never equal to the previous NaN,
            # so the node is treated as changed every time.
            # Return a stable value instead so downstream API nodes can be cached.
            try:
                if isinstance(prompt, dict) and unique_id is not None:
                    node = _prompt_get_node(prompt, unique_id)
                    if isinstance(node, dict):
                        return json.dumps(node, ensure_ascii=False, sort_keys=True, default=str)
            except Exception:
                pass

            return "BMK_NAI_EXTRACT_STABLE_FALLBACK"

    def extract(
        self,
        image: Any,
        prompt: Optional[Dict[str, Any]] = None,
        unique_id: Optional[Any] = None,
    ) -> Tuple[str, int, int, str, str, int, float, str, str, int, float, str]:
        try:
            path = _resolve_or_discover_image_path(prompt, unique_id)
            png_text, image_size = _read_image_metadata(path)
            parsed = _build_novelai_metadata(png_text, image_size)
            parsed["source_path"] = str(path)
        except Exception as exc:
            tensor_size = _image_tensor_size(image)
            width, height = tensor_size if tensor_size else (-1, -1)
            error_json = json.dumps(
                {
                    "error": str(exc),
                    "hint": "Connect this node directly or indirectly from a Load Image node.",
                    "connected_image_size": {
                        "width": width,
                        "height": height,
                    },
                },
                ensure_ascii=False,
                indent=2,
            )
            return (
                "nai-diffusion-4-5-full",
                width,
                height,
                "",
                "",
                -1,
                0.0,
                "k_euler",
                "native",
                -1,
                0.0,
                error_json,
            )

        model = _normalize_model(
            _first_value(
                parsed,
                ["model", "source_model", "generation_model", "model_id", "base_model"],
            ),
            default="nai-diffusion-4-5-full",
        )
        width = _as_int(_first_value(parsed, ["width"]), default=image_size[0])
        height = _as_int(_first_value(parsed, ["height"]), default=image_size[1])
        positive = _as_str(_first_value(parsed, ["positive", "prompt", "prompts", "description"]))
        negative = _as_str(
            _first_value(
                parsed,
                [
                    "negative",
                    "negative_prompt",
                    "uc",
                    "undesired_content",
                    "v4_negative_prompt",
                    "negativePrompt",
                ],
            )
        )
        steps = _as_int(_first_value(parsed, ["steps"]), default=-1)
        cfg = _as_float(
            _first_value(parsed, ["cfg", "scale", "guidance", "cfg_scale", "prompt_guidance"]),
            default=0.0,
        )
        sampler = _normalize_sampler(
            _first_value(parsed, ["sampler", "sampler_name"]),
            default="k_euler",
        )
        scheduler = _normalize_scheduler(
            _first_value(parsed, ["scheduler", "noise_schedule", "schedule"]),
            default="native",
        )
        seed = _as_int(_first_value(parsed, ["seed"]), default=-1)
        cfg_rescale = _as_float(
            _first_value(
                parsed,
                [
                    "cfg_rescale",
                    "rescale",
                    "dynamic_thresholding",
                    "dynamic_thresholding_percentile",
                ],
            ),
            default=0.0,
        )
        json_text = json.dumps(parsed, ensure_ascii=False, indent=2, sort_keys=True)

        return (
            model,
            width,
            height,
            positive,
            negative,
            steps,
            cfg,
            sampler,
            scheduler,
            seed,
            cfg_rescale,
            json_text,
        )


class BMKNovelAIMetadataExtractorSimple:
    CATEGORY = "BMK Nodes/NovelAI"
    FUNCTION = "extract"
    DESCRIPTION = "Extracts a small set of NovelAI PNG metadata from a Load Image connection."
    SEARCH_ALIASES = [
        "bmk",
        "nai",
        "novelai",
        "novel ai",
        "metadata",
        "extract",
        "nai extract simple",
    ]

    RETURN_TYPES = (
        "INT",     # width
        "INT",     # height
        "STRING",  # positive
        "STRING",  # negative
        "INT",     # seed
    )

    RETURN_NAMES = (
        "width",
        "height",
        "positive",
        "negative",
        "seed",
    )

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
            },
            "hidden": {
                "prompt": "PROMPT",
                "unique_id": "UNIQUE_ID",
            },
        }

    @classmethod
    def IS_CHANGED(cls, image=None, prompt=None, unique_id=None, **kwargs):
        try:
            path = _resolve_or_discover_image_path(prompt, unique_id)

            digest = hashlib.sha256()
            with open(path, "rb") as f:
                for block in iter(lambda: f.read(1024 * 1024), b""):
                    digest.update(block)
            return digest.hexdigest()

        except Exception:
            # Same cache-safe fallback as the full node.
            # Returning NaN here makes ComfyUI rerun downstream nodes every time.
            try:
                if isinstance(prompt, dict) and unique_id is not None:
                    node = _prompt_get_node(prompt, unique_id)
                    if isinstance(node, dict):
                        return json.dumps(node, ensure_ascii=False, sort_keys=True, default=str)
            except Exception:
                pass

            return "BMK_NAI_EXTRACT_SIMPLE_STABLE_FALLBACK"

    def extract(
        self,
        image: Any,
        prompt: Optional[Dict[str, Any]] = None,
        unique_id: Optional[Any] = None,
    ) -> Tuple[int, int, str, str, int]:
        try:
            path = _resolve_or_discover_image_path(prompt, unique_id)
            png_text, image_size = _read_image_metadata(path)
            parsed = _build_novelai_metadata(png_text, image_size)
        except Exception:
            tensor_size = _image_tensor_size(image)
            width, height = tensor_size if tensor_size else (-1, -1)
            return (width, height, "", "", -1)

        width = _as_int(_first_value(parsed, ["width"]), default=image_size[0])
        height = _as_int(_first_value(parsed, ["height"]), default=image_size[1])
        positive = _as_str(_first_value(parsed, ["positive", "prompt", "prompts", "description"]))
        negative = _as_str(
            _first_value(
                parsed,
                [
                    "negative",
                    "negative_prompt",
                    "uc",
                    "undesired_content",
                    "v4_negative_prompt",
                    "negativePrompt",
                ],
            )
        )
        seed = _as_int(_first_value(parsed, ["seed"]), default=-1)

        return (
            width,
            height,
            positive,
            negative,
            seed,
        )


def _resolve_or_discover_image_path(
    prompt: Optional[Dict[str, Any]],
    unique_id: Optional[Any],
) -> Path:
    discovered = _find_connected_image_path(prompt, unique_id)
    if discovered:
        return _resolve_image_path(discovered)

    raise FileNotFoundError(
        "Could not find the original image file. Connect from Load Image."
    )


def _find_connected_image_path(
    prompt: Optional[Dict[str, Any]],
    unique_id: Optional[Any],
) -> Optional[str]:
    if not isinstance(prompt, dict) or unique_id is None:
        return None

    current = _prompt_get_node(prompt, unique_id)
    if not current:
        return None

    current_inputs = current.get("inputs", {}) if isinstance(current, dict) else {}
    image_link = current_inputs.get("image")
    source_id = _link_source_id(image_link)
    if source_id is None:
        return None

    queue = deque([source_id])
    visited = set()
    max_nodes = 32

    while queue and len(visited) < max_nodes:
        node_id = queue.popleft()
        if node_id in visited:
            continue
        visited.add(node_id)

        node = _prompt_get_node(prompt, node_id)
        if not isinstance(node, dict):
            continue

        inputs = node.get("inputs", {}) or {}
        class_type = str(node.get("class_type", ""))

        if class_type.lower() in {"loadimage", "load image"}:
            value = inputs.get("image")
            if isinstance(value, str) and value.strip():
                return value

        for key in (
            "image",
            "image_path",
            "path",
            "filepath",
            "file_path",
            "filename",
            "file",
            "upload",
        ):
            value = inputs.get(key)
            if isinstance(value, str) and _looks_like_image_filename(value):
                return value

        for value in inputs.values():
            next_id = _link_source_id(value)
            if next_id is not None and next_id not in visited:
                queue.append(next_id)

    return None


def _prompt_get_node(prompt: Dict[str, Any], node_id: Any) -> Optional[Dict[str, Any]]:
    for key in (node_id, str(node_id)):
        node = prompt.get(key)
        if isinstance(node, dict):
            return node

    try:
        node = prompt.get(int(node_id))
        if isinstance(node, dict):
            return node
    except Exception:
        pass

    return None


def _link_source_id(value: Any) -> Optional[str]:
    if isinstance(value, (list, tuple)) and len(value) >= 1:
        first = value[0]
        if isinstance(first, (str, int)):
            return str(first)
    return None


def _looks_like_image_filename(value: str) -> bool:
    lower = value.lower().strip()
    image_exts = (
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".bmp",
        ".tif",
        ".tiff",
        ".avif",
    )
    return any(ext in lower for ext in image_exts)


def _resolve_image_path(image_path: str) -> Path:
    raw = (image_path or "").strip().strip('"').strip("'")
    if not raw:
        raise ValueError("image path is empty")

    raw_path = Path(raw)
    if raw_path.is_absolute() and raw_path.exists():
        return raw_path

    candidates = [Path.cwd() / raw_path]

    if folder_paths is not None:
        try:
            annotated = folder_paths.get_annotated_filepath(raw)
            if annotated:
                candidates.append(Path(annotated))
        except Exception:
            pass

        for attr in ("input_directory", "output_directory", "temp_directory"):
            base = getattr(folder_paths, attr, None)
            if base:
                candidates.append(Path(base) / raw_path)

        basename = Path(raw).name
        if basename and basename != raw:
            for attr in ("input_directory", "output_directory", "temp_directory"):
                base = getattr(folder_paths, attr, None)
                if base:
                    candidates.append(Path(base) / basename)

    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()

    searched = "\n".join(f"- {c}" for c in candidates)
    raise FileNotFoundError(f"Image not found: {image_path}\nSearched:\n{searched}")


def _read_image_metadata(path: Path) -> Tuple[Dict[str, Any], Tuple[int, int]]:
    try:
        with Image.open(path) as img:
            image_size = tuple(img.size)
            info: Dict[str, Any] = {}

            for key, value in img.info.items():
                if isinstance(value, bytes):
                    try:
                        value = value.decode("utf-8", errors="replace")
                    except Exception:
                        value = repr(value)

                if isinstance(value, (str, int, float, bool)) or value is None:
                    info[str(key)] = value
                else:
                    info[str(key)] = repr(value)

            try:
                exif = img.getexif()
                if exif:
                    info["EXIF"] = {str(k): _json_safe(v) for k, v in dict(exif).items()}
            except Exception:
                pass

            return info, (int(image_size[0]), int(image_size[1]))

    except UnidentifiedImageError as exc:
        raise ValueError(f"Unsupported or unreadable image file: {path}") from exc


def _build_novelai_metadata(png_text: Dict[str, Any], image_size: Tuple[int, int]) -> Dict[str, Any]:
    normalized_text = {_normalize_key(k): v for k, v in png_text.items()}

    description = _as_str(
        _first_value(
            normalized_text,
            ["description", "prompt", "prompts", "parameters"],
        )
    )

    comment_raw = _as_str(
        _first_value(
            normalized_text,
            ["comment", "generation_data", "generation_metadata", "metadata"],
        )
    )

    comment_json = _parse_json_object(comment_raw)

    merged: Dict[str, Any] = dict(png_text)

    for k, v in normalized_text.items():
        merged.setdefault(k, v)

    if description:
        merged.setdefault("positive", description)
        merged.setdefault("prompt", description)
        merged.setdefault("prompts", description)
        merged.setdefault("description", description)

    if isinstance(comment_json, dict):
        merged.update(comment_json)
        merged["comment_json"] = comment_json
    elif comment_raw:
        merged["comment_raw"] = comment_raw

    if "uc" in merged:
        merged.setdefault("negative", merged["uc"])
        merged.setdefault("negative_prompt", merged["uc"])

    if "scale" in merged:
        merged.setdefault("cfg", merged["scale"])

    if "noise_schedule" in merged:
        merged.setdefault("scheduler", merged["noise_schedule"])

    merged.setdefault("width", image_size[0])
    merged.setdefault("height", image_size[1])

    return _json_safe(merged)


def _parse_json_object(value: str) -> Dict[str, Any]:
    if not value:
        return {}

    candidates = [value, html.unescape(value)]

    for candidate in candidates:
        candidate = candidate.strip()
        if not candidate:
            continue

        try:
            parsed = json.loads(candidate)
            return parsed if isinstance(parsed, dict) else {"value": parsed}
        except Exception:
            pass

        start = candidate.find("{")
        end = candidate.rfind("}")
        if 0 <= start < end:
            try:
                parsed = json.loads(candidate[start:end + 1])
                return parsed if isinstance(parsed, dict) else {"value": parsed}
            except Exception:
                pass

    return {}


def _first_value(mapping: Dict[str, Any], keys: Iterable[str], default: Any = None) -> Any:
    if not mapping:
        return default

    for key in keys:
        if key in mapping and mapping[key] not in (None, ""):
            return mapping[key]

    normalized = {_normalize_key(k): v for k, v in mapping.items()}
    for key in keys:
        nk = _normalize_key(key)
        if nk in normalized and normalized[nk] not in (None, ""):
            return normalized[nk]

    return default


def _normalize_key(key: Any) -> str:
    return str(key).strip().replace("-", "_").replace(" ", "_").lower()


def _normalize_model(value: Any, default: str = "nai-diffusion-4-5-full") -> str:
    raw = _as_str(value).strip()
    if not raw:
        return default

    if raw in NAID_MODELS:
        return raw

    key = raw.lower().replace("-", " ").replace("_", " ").strip()
    key = " ".join(key.split())
    if key in MODEL_ALIASES:
        return MODEL_ALIASES[key]

    key_underscore = key.replace(" ", "_")
    if key_underscore in MODEL_ALIASES:
        return MODEL_ALIASES[key_underscore]

    key_hyphen = key.replace(" ", "-")
    if key_hyphen in MODEL_ALIASES:
        return MODEL_ALIASES[key_hyphen]

    return default


def _normalize_sampler(value: Any, default: str = "k_euler") -> str:
    raw = _as_str(value).strip()
    if not raw:
        return default

    if raw in NAID_SAMPLERS:
        return raw

    key = raw.lower().replace("-", "_").strip()
    key = "_".join(key.split())
    return SAMPLER_ALIASES.get(key, default)


def _normalize_scheduler(value: Any, default: str = "native") -> str:
    raw = _as_str(value).strip()
    if not raw:
        return default

    if raw in NAID_SCHEDULERS:
        return raw

    key = raw.lower().replace("-", "_").strip()
    key = "_".join(key.split())
    return SCHEDULER_ALIASES.get(key, default)


def _as_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except Exception:
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _image_tensor_size(image: Any) -> Optional[Tuple[int, int]]:
    if image is None:
        return None

    shape = getattr(image, "shape", None)
    if shape is None:
        return None

    try:
        shape = tuple(int(x) for x in shape)

        if len(shape) == 4:
            return shape[2], shape[1]

        if len(shape) == 3:
            return shape[1], shape[0]

    except Exception:
        return None

    return None


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


def _validate_node_layout() -> None:
    for cls in (BMKNovelAIMetadataExtractor, BMKNovelAIMetadataExtractorSimple):
        if len(cls.RETURN_TYPES) != len(cls.RETURN_NAMES):
            raise RuntimeError(
                f"{cls.__name__}: RETURN_TYPES and RETURN_NAMES length mismatch "
                f"({len(cls.RETURN_TYPES)} != {len(cls.RETURN_NAMES)})"
            )


_validate_node_layout()


NODE_CLASS_MAPPINGS = {
    "BMKNovelAIMetadataExtractor": BMKNovelAIMetadataExtractor,
    "BMKNovelAIMetadataExtractorSimple": BMKNovelAIMetadataExtractorSimple,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "BMKNovelAIMetadataExtractor": "NAI Extract",
    "BMKNovelAIMetadataExtractorSimple": "NAI Extract Simple",
}
