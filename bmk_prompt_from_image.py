"""BMK Prompt From Image — 이미지에 박힌 생성 메타데이터에서 positive/negative만 추출.

배경
────
NovelAI 공식 사이트가 아닌 서드파티 웹 서비스(NAI API 래퍼)로 뽑은 PNG는, 자체
포맷 tEXt 청크 하나에 생성 설정 전체를 JSON으로 밀어넣는 경우가 많다. 실제 샘플
(청크 키 ``forge``, nai-diffusion-5-curated):

    {"width":832, "height":1216, "steps":28, ...,
     "prompt":            "1girl, rabbit girl,\\nsolo, full body, ...",
     "negative_prompt":   "1.4::worst quality, ... ::,\\nlowres, ...",
     "image_workspace_state":{"settings":{
         "prompt":"(동일)", "negative_prompt":"(동일)",
         "positive_prompt_prefix":"", "positive_prompt_suffix":"", ...}},
     "forge_version":1, "backend":"novelai"}

기존 ``novelai_metadata.py`` (NAI Extract / NAI Extract Simple) 는 청크의
**top-level 키만** 후보 목록으로 훑기 때문에, 위처럼 프롬프트가 청크 값 JSON
*안쪽*에 들어있으면 positive/negative가 빈 문자열로 나온다. 이 노드는 그 지점을
메운다 — 서비스별 키 이름을 하드코딩하지 않고, 중첩 JSON을 전부 펼친 뒤
**경로(path) 기반**으로 positive/negative를 판정한다.

동작
────
1. 상류 Load Image 계열 노드를 그래프에서 역추적해 원본 파일 경로를 찾는다
   (BMKLoadImageCrop 처럼 표준 LoadImage가 아니어도 image 위젯 문자열로 인식).
2. PNG tEXt/iTXt 청크 + (JPEG/WebP인 경우) EXIF UserComment 를 모은다.
3. 값이 JSON이면 재귀적으로 펼친다. 깊이·노드 수 상한이 있고, ComfyUI 자신의
   ``prompt``/``workflow`` 그래프 청크는 오탐 방지를 위해 통째로 건너뛴다.
4. leaf 키가 프롬프트 후보(prompt / positive / base_caption / uc / negative_prompt
   …)면, **경로 전체에** negative 힌트가 있는지로 positive/negative를 가른다.
   예) ``Comment.v4_negative_prompt.caption.base_caption`` → negative.
5. 같은 후보가 여러 깊이에서 나오면 **얕은 쪽 우선**, 동률이면 긴 쪽.
   → forge 포맷은 top-level ``prompt`` (깊이 2) 가
     ``image_workspace_state.settings.prompt`` (깊이 4) 를 이긴다.
6. 위 경로로 못 찾으면 A1111 ``parameters`` 평문 포맷을 마지막으로 시도한다.

지원 확인된 포맷: forge(서드파티 NAI 래퍼), NovelAI 공식(Comment/Description,
V3·V4 base_caption), A1111 parameters, 그리고 같은 모양의 임의 서비스 JSON.

주의 — 문법 호환성
──────────────────
추출되는 문자열은 **NovelAI V4/V5 문법 그대로**다. ``1.4::worst quality ::``,
``-1::simple illustration ::`` 같은 ``가중치::내용::`` 표기는 ComfyUI CLIP 인코더가
리터럴 텍스트로 토큰화하므로 그대로 쓰면 안 된다. Anima i2i에 물릴 때는

    BMK Prompt From Image ─┬─ positive ─→ Prompt Converter (NovelAI → ComfyUI)
                           └─ negative ─→ Prompt Converter (NovelAI → ComfyUI)
                                              └─→ Prompt Converter (ComfyUI → Anima)

순으로 체이닝할 것. 이 노드는 의도적으로 변환을 하지 않는다(단일 책임 —
문법 변환은 prompt_converter.py 가 정본).

옵션
────
- metadata_key : 특정 청크만 보고 싶을 때 키 이름 지정(빈 값이면 전체 자동 탐색).
- apply_prefix_suffix : 선택된 프롬프트와 같은 depth에 positive_prompt_prefix /
  positive_prompt_suffix / trigger_words 가 비어있지 않게 들어있으면 앞뒤로 합침.
  (샘플에서는 전부 빈 문자열이라 무동작. 서비스가 프리셋을 쓰는 경우 대비.)
- join_lines : 줄바꿈을 ", " 로 접고 중복 콤마/공백을 정리. 기본 False —
  Prompt Converter 가 줄 단위 구조를 보존하므로 보통 그대로 두는 게 낫다.

버전 이력
─────────
v1 (2026-08) : 최초. 경로 기반 재귀 탐색 + forge/NAI/A1111 커버.
"""

from __future__ import annotations

import json
import logging
import re
from collections import deque
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from PIL import Image, UnidentifiedImageError

try:
    import folder_paths
except Exception:  # ComfyUI 밖에서 import 되는 경우
    folder_paths = None


logger = logging.getLogger(__name__)
_TAG = "[ComfyUI_BMK_Nodes::PromptFromImage]"


# ─── 탐색 상수 ──────────────────────────────────────────────────

# leaf 키가 이 집합에 있으면 프롬프트 후보로 본다(정규화된 이름 기준).
_POS_LEAF_KEYS = frozenset(
    {
        "prompt",
        "positive",
        "positive_prompt",
        "base_caption",
        "caption",
        "description",
        "input",
    }
)

_NEG_LEAF_KEYS = frozenset(
    {
        "negative",
        "negative_prompt",
        "negativeprompt",
        "uc",
        "undesired_content",
        "unwanted_content",
        "negative_caption",
    }
)

# 경로 어딘가에 이 힌트가 있으면 negative 로 분류(예: v4_negative_prompt.caption.base_caption)
_NEG_PATH_SUBSTRINGS = ("negative", "undesired", "unwanted")
_NEG_PATH_EXACT = frozenset({"uc", "nc"})

# 바이너리/무의미 청크는 아예 건너뜀
_SKIP_CHUNK_KEYS = frozenset({"exif", "icc_profile", "xmp", "photoshop", "dpi", "gamma", "srgb"})

_MAX_DEPTH = 12
_MAX_NODES = 20000
_MAX_JSON_CHARS = 8 * 1024 * 1024

# 파일 경로 역추적에서 image 파일명으로 인정할 확장자
_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".avif", ".jxl")


# ─── 유틸 ───────────────────────────────────────────────────────

def _norm(key: Any) -> str:
    return str(key).strip().replace("-", "_").replace(" ", "_").lower()


def _try_json(value: str) -> Optional[Any]:
    """문자열이 JSON 객체/배열이면 파싱해서 반환, 아니면 None."""
    if not isinstance(value, str):
        return None
    s = value.strip()
    if len(s) > _MAX_JSON_CHARS:
        return None
    if not s or s[0] not in "{[":
        return None
    try:
        return json.loads(s)
    except Exception:
        return None


def _looks_like_comfy_graph(obj: Any) -> bool:
    """ComfyUI 가 자기 PNG에 박는 prompt/workflow 그래프인지 판별.

    이 안에도 'prompt' 라는 이름의 위젯이 흔해서, 걸러내지 않으면 엉뚱한
    노드 입력값이 positive 로 뽑힌다.
    """
    if isinstance(obj, dict):
        if "nodes" in obj and "links" in obj:  # workflow 포맷
            return True
        for v in obj.values():
            if isinstance(v, dict) and "class_type" in v and "inputs" in v:
                return True
    return False


def _classify(path: Sequence[str]) -> Optional[str]:
    """경로를 보고 'pos' / 'neg' / None 판정."""
    segs = [_norm(p) for p in path]
    leaf = segs[-1]
    if leaf not in _POS_LEAF_KEYS and leaf not in _NEG_LEAF_KEYS:
        return None

    for seg in segs:
        if seg in _NEG_PATH_EXACT:
            return "neg"
        if any(sub in seg for sub in _NEG_PATH_SUBSTRINGS):
            return "neg"

    return "pos"


def _collect_candidates(root: Dict[str, Any]) -> Tuple[List[tuple], List[tuple]]:
    """BFS로 펼치며 (depth, path, value) 후보를 모은다.

    BFS라서 자연스럽게 얕은 항목이 먼저 나오지만, 정렬로 한 번 더 못을 박는다.
    """
    pos: List[tuple] = []
    neg: List[tuple] = []

    queue: deque = deque()
    for key, value in root.items():
        if _norm(key) in _SKIP_CHUNK_KEYS:
            continue
        queue.append(((str(key),), value))

    seen = 0
    while queue and seen < _MAX_NODES:
        path, value = queue.popleft()
        seen += 1

        if len(path) > _MAX_DEPTH:
            continue

        # 문자열이 JSON이면 컨테이너로 승격해서 계속 파고든다.
        if isinstance(value, str):
            parsed = _try_json(value)
            if parsed is not None:
                value = parsed

        if isinstance(value, dict):
            if _looks_like_comfy_graph(value):
                logger.debug("%s skip comfy graph at %s", _TAG, "/".join(path))
                continue
            for k, v in value.items():
                queue.append((path + (str(k),), v))
            continue

        if isinstance(value, (list, tuple)):
            for i, v in enumerate(value[:64]):
                queue.append((path + (str(i),), v))
            continue

        if not isinstance(value, str):
            continue

        text = value.strip()
        if not text:
            continue

        kind = _classify(path)
        if kind == "pos":
            pos.append((len(path), path, value))
        elif kind == "neg":
            neg.append((len(path), path, value))

    # 얕은 것 우선, 동률이면 긴 쪽
    pos.sort(key=lambda t: (t[0], -len(t[2])))
    neg.sort(key=lambda t: (t[0], -len(t[2])))
    return pos, neg


def _sibling(root: Dict[str, Any], path: Sequence[str], name: str) -> str:
    """선택된 프롬프트와 같은 부모 아래의 형제 키 값을 문자열로 가져온다."""
    node: Any = root
    for seg in path[:-1]:
        if isinstance(node, str):
            parsed = _try_json(node)
            if parsed is None:
                return ""
            node = parsed
        if isinstance(node, dict) and seg in node:
            node = node[seg]
        elif isinstance(node, (list, tuple)):
            try:
                node = node[int(seg)]
            except Exception:
                return ""
        else:
            return ""

    if isinstance(node, str):
        parsed = _try_json(node)
        node = parsed if parsed is not None else {}
    if isinstance(node, dict):
        val = node.get(name)
        return val.strip() if isinstance(val, str) else ""
    return ""


# ─── A1111 폴백 ─────────────────────────────────────────────────

_A1111_NEG = re.compile(r"\nNegative prompt:\s*", re.IGNORECASE)
_A1111_TAIL = re.compile(r"\n(?:Steps|Sampler|CFG scale|Seed|Size|Model):", re.IGNORECASE)


def _parse_a1111(root: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    for key, value in root.items():
        if not isinstance(value, str):
            continue
        if _norm(key) != "parameters" and "Negative prompt:" not in value:
            continue

        neg_split = _A1111_NEG.split(value, maxsplit=1)
        positive = neg_split[0]
        negative = neg_split[1] if len(neg_split) > 1 else ""

        tail = _A1111_TAIL.search(positive)
        if tail and not negative:
            positive = positive[: tail.start()]
        tail = _A1111_TAIL.search(negative)
        if tail:
            negative = negative[: tail.start()]

        positive, negative = positive.strip(), negative.strip()
        if positive or negative:
            return positive, negative
    return None


# ─── 메타데이터 읽기 ────────────────────────────────────────────

def _decode_user_comment(raw: bytes) -> str:
    for prefix, enc in ((b"UNICODE\x00", "utf-16-be"), (b"ASCII\x00\x00\x00", "ascii"), (b"\x00" * 8, "utf-8")):
        if raw.startswith(prefix):
            body = raw[len(prefix):]
            for candidate in (enc, "utf-16-le", "utf-8"):
                try:
                    return body.decode(candidate).rstrip("\x00")
                except Exception:
                    continue
    return raw.decode("utf-8", errors="replace").rstrip("\x00")


def _read_metadata(path: Path) -> Dict[str, Any]:
    """PNG tEXt/iTXt + EXIF UserComment/ImageDescription 을 평평한 dict로."""
    out: Dict[str, Any] = {}
    try:
        with Image.open(path) as img:
            text = getattr(img, "text", None)
            if isinstance(text, dict):
                for k, v in text.items():
                    if isinstance(v, str):
                        out[str(k)] = v

            for k, v in (img.info or {}).items():
                if str(k) in out or _norm(k) in _SKIP_CHUNK_KEYS:
                    continue
                if isinstance(v, bytes):
                    continue
                if isinstance(v, str):
                    out[str(k)] = v

            try:
                exif = img.getexif()
                if exif:
                    desc = exif.get(270)  # ImageDescription
                    if isinstance(desc, str) and desc.strip():
                        out.setdefault("ImageDescription", desc)
                    ifd = exif.get_ifd(0x8769) or {}
                    uc = ifd.get(37510)  # UserComment
                    if isinstance(uc, bytes):
                        uc = _decode_user_comment(uc)
                    if isinstance(uc, str) and uc.strip():
                        out.setdefault("UserComment", uc)
            except Exception:
                pass
    except UnidentifiedImageError as exc:
        raise ValueError(f"읽을 수 없는 이미지 형식: {path}") from exc

    return out


# ─── 원본 파일 경로 역추적 ──────────────────────────────────────

def _prompt_node(prompt: Dict[str, Any], node_id: Any) -> Optional[Dict[str, Any]]:
    for key in (node_id, str(node_id)):
        node = prompt.get(key)
        if isinstance(node, dict):
            return node
    return None


def _link_source(value: Any) -> Optional[str]:
    if isinstance(value, (list, tuple)) and value and isinstance(value[0], (str, int)):
        return str(value[0])
    return None


def _looks_like_image_name(value: str) -> bool:
    lower = value.lower().strip()
    return any(ext in lower for ext in _IMAGE_EXTS)


def _find_upstream_image(prompt: Optional[Dict[str, Any]], unique_id: Any) -> Optional[str]:
    """내 image 입력에서 거슬러 올라가며 파일명 위젯을 가진 노드를 찾는다."""
    if not isinstance(prompt, dict) or unique_id is None:
        return None

    me = _prompt_node(prompt, unique_id)
    if not me:
        return None

    start = _link_source((me.get("inputs") or {}).get("image"))
    if start is None:
        return None

    queue = deque([start])
    visited: set = set()
    while queue and len(visited) < 64:
        node_id = queue.popleft()
        if node_id in visited:
            continue
        visited.add(node_id)

        node = _prompt_node(prompt, node_id)
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs") or {}

        for key in ("image", "image_path", "path", "filepath", "file_path", "filename", "file", "upload"):
            value = inputs.get(key)
            if isinstance(value, str) and _looks_like_image_name(value):
                return value

        for value in inputs.values():
            nxt = _link_source(value)
            if nxt is not None and nxt not in visited:
                queue.append(nxt)

    return None


def _resolve_path(raw: str) -> Path:
    raw = (raw or "").strip().strip('"').strip("'")
    if not raw:
        raise ValueError("이미지 경로가 비어 있습니다.")

    candidates: List[Path] = []
    direct = Path(raw)
    if direct.is_absolute():
        candidates.append(direct)
    candidates.append(Path.cwd() / direct)

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
                candidates.append(Path(base) / direct)
                candidates.append(Path(base) / direct.name)

    for cand in candidates:
        try:
            if cand.exists():
                return cand.resolve()
        except Exception:
            continue

    raise FileNotFoundError(
        "원본 이미지 파일을 찾지 못했습니다: " + raw + "\n탐색 경로:\n"
        + "\n".join(f"  - {c}" for c in candidates)
    )


# ─── 후처리 ─────────────────────────────────────────────────────

_MULTI_COMMA = re.compile(r"\s*,(?:\s*,)+\s*")
_SPACES = re.compile(r"[ \t]{2,}")


def _join_lines(text: str) -> str:
    if not text:
        return text
    joined = re.sub(r"\s*\n\s*", ", ", text.strip())
    joined = _MULTI_COMMA.sub(", ", joined)
    joined = _SPACES.sub(" ", joined)
    return joined.strip().strip(",").strip()


# ─── 노드 ───────────────────────────────────────────────────────

class BMKPromptFromImage:
    CATEGORY = "BMK/Text"
    FUNCTION = "extract"

    DESCRIPTION = (
        "이미지에 박힌 생성 메타데이터에서 positive / negative 프롬프트만 뽑아냅니다. "
        "청크 값 안쪽에 중첩된 JSON까지 재귀 탐색하므로, NovelAI 공식 포맷뿐 아니라 "
        "서드파티 NAI 래퍼(forge 등)와 A1111 parameters 포맷도 처리합니다. "
        "문법 변환은 하지 않습니다 — NAI의 '가중치::내용::' 표기는 Prompt Converter로 넘기세요."
    )

    SEARCH_ALIASES = [
        "prompt from image",
        "metadata",
        "메타데이터",
        "프롬프트 추출",
        "png info",
        "exif",
        "novelai",
        "nai",
        "forge",
        "a1111",
        "i2i",
    ]

    RETURN_TYPES = ("STRING", "STRING", "STRING", "STRING")
    RETURN_NAMES = ("positive", "negative", "source", "metadata_json")

    OUTPUT_TOOLTIPS = (
        "추출된 positive 프롬프트 원문. NAI 문법이 그대로 남아 있으니 Prompt Converter를 거치세요.",
        "추출된 negative 프롬프트 원문.",
        "어느 키 경로에서 뽑았는지 (예: forge/prompt). 비어 있으면 못 찾은 것.",
        "발견한 메타데이터 전체를 보기 좋게 정리한 JSON. 새 서비스 포맷 디버깅용.",
    )

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": (
                    "IMAGE",
                    {"tooltip": "Load Image 계열 노드에서 연결. 텐서가 아니라 상류 노드의 원본 파일을 읽습니다."},
                ),
            },
            "optional": {
                "metadata_key": (
                    "STRING",
                    {
                        "default": "",
                        "tooltip": "특정 청크만 탐색하려면 키 이름 지정 (예: forge, Comment). 비우면 전체 자동 탐색.",
                    },
                ),
                "apply_prefix_suffix": (
                    "BOOLEAN",
                    {
                        "default": True,
                        "tooltip": "같은 계층에 positive_prompt_prefix / suffix / trigger_words 가 있으면 앞뒤로 붙입니다.",
                    },
                ),
                "join_lines": (
                    "BOOLEAN",
                    {
                        "default": False,
                        "tooltip": "줄바꿈을 ', ' 로 접고 중복 콤마를 정리. 기본 off — Prompt Converter가 줄 구조를 보존합니다.",
                    },
                ),
            },
            "hidden": {
                "prompt": "PROMPT",
                "unique_id": "UNIQUE_ID",
            },
        }

    @classmethod
    def IS_CHANGED(cls, image=None, metadata_key="", apply_prefix_suffix=True,
                   join_lines=False, prompt=None, unique_id=None, **kwargs):
        """파일 mtime/size 만 해시 — 안정값이라 캐시가 정상 동작한다.

        예외를 던지면 ComfyUI가 NaN 취급(always-dirty)하므로 절대 raise 하지 않는다.
        """
        try:
            found = _find_upstream_image(prompt, unique_id)
            if not found:
                return f"BMK_PFI_NOPATH|{metadata_key}|{apply_prefix_suffix}|{join_lines}"
            path = _resolve_path(found)
            st = path.stat()
            return f"{path}|{st.st_mtime_ns}|{st.st_size}|{metadata_key}|{apply_prefix_suffix}|{join_lines}"
        except Exception:
            return f"BMK_PFI_STABLE_FALLBACK|{metadata_key}|{apply_prefix_suffix}|{join_lines}"

    def extract(
        self,
        image: Any = None,
        metadata_key: str = "",
        apply_prefix_suffix: bool = True,
        join_lines: bool = False,
        prompt: Optional[Dict[str, Any]] = None,
        unique_id: Optional[Any] = None,
    ) -> Tuple[str, str, str, str]:
        try:
            found = _find_upstream_image(prompt, unique_id)
            if not found:
                raise FileNotFoundError(
                    "상류에서 이미지 파일명을 찾지 못했습니다. Load Image 계열 노드에서 직접 연결하세요."
                )
            path = _resolve_path(found)
            raw = _read_metadata(path)
        except Exception as exc:
            logger.warning("%s 메타데이터 읽기 실패: %s", _TAG, exc)
            return ("", "", "", json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))

        key = (metadata_key or "").strip()
        if key:
            matched = {k: v for k, v in raw.items() if _norm(k) == _norm(key)}
            if not matched:
                logger.warning(
                    "%s metadata_key '%s' 없음. 존재하는 키: %s",
                    _TAG, key, ", ".join(sorted(raw)) or "(없음)",
                )
            scope = matched or raw
        else:
            scope = raw

        pos_cands, neg_cands = _collect_candidates(scope)

        positive = pos_cands[0][2] if pos_cands else ""
        negative = neg_cands[0][2] if neg_cands else ""
        source_bits: List[str] = []
        if pos_cands:
            source_bits.append("+" + "/".join(pos_cands[0][1]))
        if neg_cands:
            source_bits.append("-" + "/".join(neg_cands[0][1]))

        if not positive and not negative:
            a1111 = _parse_a1111(scope)
            if a1111:
                positive, negative = a1111
                source_bits.append("a1111/parameters")

        if apply_prefix_suffix and pos_cands:
            ppath = pos_cands[0][1]
            prefix = _sibling(scope, ppath, "positive_prompt_prefix")
            suffix = _sibling(scope, ppath, "positive_prompt_suffix")
            trigger = _sibling(scope, ppath, "trigger_words")
            parts = [p for p in (prefix, trigger, positive, suffix) if p]
            if len(parts) > 1:
                positive = ", ".join(parts)
                source_bits.append("prefix/suffix applied")

        if join_lines:
            positive = _join_lines(positive)
            negative = _join_lines(negative)

        source = " ".join(source_bits)
        if not source:
            logger.warning(
                "%s 프롬프트를 찾지 못했습니다. 청크 키: %s",
                _TAG, ", ".join(sorted(raw)) or "(없음)",
            )

        pretty: Dict[str, Any] = {}
        for k, v in raw.items():
            parsed = _try_json(v) if isinstance(v, str) else None
            pretty[k] = parsed if parsed is not None else v

        return (
            positive,
            negative,
            source,
            json.dumps(pretty, ensure_ascii=False, indent=2, default=str),
        )


NODE_CLASS_MAPPINGS = {
    "BMKPromptFromImage": BMKPromptFromImage,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "BMKPromptFromImage": "BMK Prompt From Image",
}
