"""BMK Prompt From Image — 이미지에 박힌 생성 메타데이터에서 positive/negative만 추출.

배경
────
생성 이미지에 프롬프트를 심는 방식은 서비스마다 다르다. 이 노드가 실제로 마주친
것만 크게 세 갈래다.

1) **NovelAI 계열** — PNG tEXt 청크에 ``Description`` (positive 평문) 과
   ``Comment`` (생성 설정 전체 JSON) 를 넣는다. V3는 ``Comment.uc`` 가 negative,
   V4/V5는 ``Comment.v4_prompt.caption.base_caption`` / ``v4_negative_prompt`` 가
   정본이고 다중 캐릭터 프롬프트는 ``char_captions[].char_caption`` 에 따로 들어간다::

       Description: "1girl, rabbit girl, solo, ..."
       Comment:     {"prompt":"...", "uc":"1.4::worst quality ::,\\nlowres, ...",
                     "steps":28, "sampler":"k_euler_ancestral",
                     "v4_prompt":{"caption":{"base_caption":"...",
                                             "char_captions":[{"char_caption":"..."}]}},
                     "v4_negative_prompt":{"caption":{"base_caption":"...",
                                                      "char_captions":[]}},
                     "request_type":"PromptGenerateRequest",
                     "model_name":"NovelAI Diffusion V5", ...}
       Software:    "NovelAI"
       Source:      "NovelAI Diffusion V5 DB276663"

2) **자체 래퍼(사내 API 등)** — NAI를 감싼 웹 서비스가 자기 이름의 tEXt 청크 하나에
   생성 설정 전체를 JSON으로 밀어넣는다. 프롬프트가 청크 값 JSON *안쪽*에,
   때로는 ``image_workspace_state.settings`` 처럼 한참 아래 중첩되어 있다::

       <서비스명>: {"width":832, "height":1216, "steps":28,
                    "prompt":"1girl, rabbit girl,\\nsolo, ...",
                    "negative_prompt":"1.4::worst quality ::,\\nlowres, ...",
                    "image_workspace_state":{"settings":{
                        "prompt":"(동일)", "negative_prompt":"(동일)",
                        "positive_prompt_prefix":"", "positive_prompt_suffix":""}},
                    "backend":"novelai"}

3) **A1111 계열** — ``parameters`` 청크 하나에 평문으로.

기존 ``novelai_metadata.py`` (NAI Extract / NAI Extract Simple) 는 청크의
**top-level 키만** 후보 목록으로 훑기 때문에 (2) 처럼 중첩된 경우 빈 문자열이
나온다. 이 노드가 그 지점을 메운다.

동작
────
1. 상류 Load Image 계열 노드를 그래프에서 역추적해 원본 파일 경로를 찾는다
   (BMKLoadImageCrop 처럼 표준 LoadImage가 아니어도 image 위젯 문자열로 인식).
2. PNG tEXt/zTXt/iTXt 청크 + (JPEG/WebP인 경우) EXIF UserComment 를 모은다.
   전부 비었고 알파 채널이 있으면 stealth pnginfo(알파 LSB 스테가노그래피)까지
   마지막으로 훑는다.
3. **포맷 인식기**를 순서대로 돌린다. 각 인식기는 자기 포맷의 키 경로를 알고 있어서
   깊이 휴리스틱에 기대지 않는다.

       NovelAI  →  자체 래퍼 JSON  →  A1111 parameters  →  범용 경로 탐색

4. 위에서 아무도 못 잡으면 마지막으로 **범용 경로 탐색**을 돈다. 중첩 JSON을 전부
   펼친 뒤 leaf 키가 프롬프트 후보(prompt / positive / base_caption / uc …)면
   **경로 전체에** negative 힌트가 있는지로 positive/negative를 가른다.
   예) ``Comment.v4_negative_prompt.caption.base_caption`` → negative.
   같은 후보가 여러 깊이에서 나오면 얕은 쪽 우선, 동률이면 긴 쪽.
   → 처음 보는 서비스 포맷도 이 단계에서 대개 걸린다.

3단계가 4단계보다 앞서는 게 핵심이다. 깊이만 보는 4단계는 NovelAI 포맷에서
``Description`` (깊이 1) 이 정본인 ``v4_prompt.caption.base_caption`` (깊이 4) 을
이겨버리고, 캐릭터 프롬프트(``char_captions``)를 통째로 흘린다.

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

배치 처리
─────────
IMAGE 텐서에는 파일명이 실려오지 않는다. 그래서 이 노드는 1단계에서 **그래프를
역추적해** 상류 로더의 파일명 위젯을 읽는다. 표준 Load Image 는 ``image`` 위젯에
파일명이 박혀 있어 그냥 되지만, **폴더를 훑는 배치 로더**(Load Image Batch 류)는
파일명 위젯 자체가 없고 폴더 경로만 들고 있어서 역추적이 빈손으로 끝난다.

이럴 때는 로더가 별도로 뱉는 파일명 출력을 ``image_path`` 입력으로 넘긴다::

    Load Image Batch ─┬─ image ────────→ BMK Prompt From Image ─→ positive
                      └─ filename_text ─→   image_path            └→ negative

폴더는 로더의 ``path`` 위젯을 자동으로 후보에 넣으므로 보통 따로 안 줘도 된다.
안 잡히면 ``search_directory`` 에 직접 적는다. 확장자가 빠진 파일명
(``filename_text_extension=false``)도 알아서 붙여가며 찾는다.

큐를 여러 번 돌리는 방식(Run × N)이라 이미지마다 결과가 따로 나오고, Save Text 로
장당 한 파일씩 떨어진다. 노드 자체가 폴더를 통째로 순회하지는 않는다 — 순회는
로더 책임이다(단일 책임).

옵션
────
- metadata_key : 특정 청크만 보고 싶을 때 키 이름 지정(빈 값이면 전체 자동 탐색).
- apply_prefix_suffix : 선택된 프롬프트와 같은 depth에 positive_prompt_prefix /
  positive_prompt_suffix / trigger_words 가 비어있지 않게 들어있으면 앞뒤로 합침.
- include_char_captions : NAI V4/V5 다중 캐릭터 프롬프트(char_captions)를 base
  뒤에 줄바꿈으로 이어붙임. 끄면 base_caption만.
- join_lines : 줄바꿈을 ", " 로 접고 중복 콤마/공백을 정리. 기본 False —
  Prompt Converter 가 줄 단위 구조를 보존하므로 보통 그대로 두는 게 낫다.

버전 이력
─────────
v1 (2026-08) : 최초. 경로 기반 재귀 탐색 + 래퍼/NAI/A1111 커버.
v2 (2026-08) : 포맷 인식 계층 도입(범용 탐색은 폴백으로 강등).
               NAI V4/V5 char_captions 지원, stealth pnginfo 폴백,
               이중 인코딩 JSON 해제, source 출력에 감지된 포맷 표기.
v3 (2026-08) : 배치 로더 대응. image_path / search_directory 입력 추가,
               상류 폴더 위젯 자동 수집, 확장자 없는 파일명 보정.
"""

from __future__ import annotations

import gzip
import json
import logging
import re
from collections import deque
from pathlib import Path
from typing import Any, Dict, Iterator, List, NamedTuple, Optional, Sequence, Tuple

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
        "char_caption",
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

# 상류 노드에서 "이 위젯은 이미지 파일명이다" 로 인정할 입력 키
_FILE_INPUT_KEYS = frozenset(
    {"image", "image_path", "path", "filepath", "file_path", "filename", "file", "upload"}
)

# 프롬프트 앞뒤로 합칠 프리셋 키(서비스가 프리셋을 쓰는 경우 대비)
_PREFIX_KEYS = ("positive_prompt_prefix",)
_SUFFIX_KEYS = ("positive_prompt_suffix",)
_TRIGGER_KEYS = ("trigger_words",)


# ─── 유틸 ───────────────────────────────────────────────────────

def _norm(key: Any) -> str:
    return str(key).strip().replace("-", "_").replace(" ", "_").lower()


def _try_json(value: Any) -> Optional[Any]:
    """문자열이 JSON 객체/배열이면 파싱해서 반환, 아니면 None.

    값이 JSON으로 한 번 더 감싸인 경우(문자열을 통째로 json.dumps 한 서비스)가
    있어서, 파싱 결과가 또 문자열이면 몇 번 더 벗겨본다.
    """
    if not isinstance(value, str):
        return None

    current = value
    for _ in range(3):
        s = current.strip()
        if len(s) > _MAX_JSON_CHARS:
            return None
        if not s:
            return None
        if s[0] in "{[":
            try:
                return json.loads(s)
            except Exception:
                return None
        if s[0] == '"':  # 이중 인코딩 의심 — 한 겹 벗기고 재시도
            try:
                unwrapped = json.loads(s)
            except Exception:
                return None
            if not isinstance(unwrapped, str):
                return None
            current = unwrapped
            continue
        return None
    return None


def _as_text(value: Any) -> str:
    """프롬프트로 쓸 수 있는 문자열이면 strip해서, 아니면 빈 문자열."""
    return value.strip() if isinstance(value, str) and value.strip() else ""


def _dig(node: Any, *keys: str) -> Any:
    """중첩 dict를 안전하게 파고든다. 중간에 JSON 문자열이 있으면 펼친다."""
    for key in keys:
        if isinstance(node, str):
            node = _try_json(node)
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    if isinstance(node, str):
        parsed = _try_json(node)
        if isinstance(parsed, (dict, list)):
            return parsed
    return node


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


def _iter_scopes(root: Dict[str, Any]) -> Iterator[Tuple[str, Dict[str, Any]]]:
    """top-level 청크 중 JSON 객체인 것만 (키, 파싱된 dict) 로 흘린다."""
    for key, value in root.items():
        if _norm(key) in _SKIP_CHUNK_KEYS:
            continue
        parsed = _try_json(value) if isinstance(value, str) else value
        if isinstance(parsed, dict) and not _looks_like_comfy_graph(parsed):
            yield str(key), parsed


class _Found(NamedTuple):
    """인식기 한 판의 결과."""

    positive: str
    negative: str
    source: str
    # positive_prompt_prefix / suffix / trigger_words 를 찾아볼 컨테이너
    affix_scope: Optional[Dict[str, Any]] = None


# ─── 인식기 1: NovelAI (공식 / API 패스스루) ────────────────────

def _is_novelai(root: Dict[str, Any]) -> bool:
    for key, value in root.items():
        nk = _norm(key)
        if nk in ("software", "source") and isinstance(value, str):
            if "novelai" in value.lower():
                return True

    for _, scope in _iter_scopes(root):
        if "v4_prompt" in scope or "v4_negative_prompt" in scope:
            return True
        if _as_text(scope.get("request_type")) == "PromptGenerateRequest":
            return True
        if "uc" in scope and any(k in scope for k in ("steps", "sampler", "seed", "noise_schedule")):
            return True
    return False


def _caption_text(caption: Any, include_chars: bool) -> str:
    """v4_prompt.caption / v4_negative_prompt.caption 을 한 덩어리 문자열로.

    base_caption 뒤에 char_captions[].char_caption 을 줄바꿈으로 잇는다.
    캐릭터-좌표(centers) 대응은 ComfyUI 쪽에 표현할 자리가 없어 버린다.
    """
    if not isinstance(caption, dict):
        return ""

    parts = [_as_text(caption.get("base_caption"))]

    if include_chars:
        chars = caption.get("char_captions")
        if isinstance(chars, (list, tuple)):
            for entry in chars[:64]:
                if isinstance(entry, dict):
                    parts.append(_as_text(entry.get("char_caption")))
                elif isinstance(entry, str):
                    parts.append(_as_text(entry))

    return "\n".join(p for p in parts if p)


def _extract_novelai(root: Dict[str, Any], include_chars: bool) -> Optional[_Found]:
    if not _is_novelai(root):
        return None

    positive = negative = ""
    pos_src = neg_src = ""

    for chunk, scope in _iter_scopes(root):
        if not positive:
            text = _caption_text(_dig(scope, "v4_prompt", "caption"), include_chars)
            if text:
                positive, pos_src = text, f"{chunk}/v4_prompt/caption"
            elif _as_text(scope.get("prompt")):
                positive, pos_src = _as_text(scope["prompt"]), f"{chunk}/prompt"

        if not negative:
            text = _caption_text(_dig(scope, "v4_negative_prompt", "caption"), include_chars)
            if text:
                negative, neg_src = text, f"{chunk}/v4_negative_prompt/caption"
            else:
                for name in ("uc", "negative_prompt"):
                    if _as_text(scope.get(name)):
                        negative, neg_src = _as_text(scope[name]), f"{chunk}/{name}"
                        break

    # Comment 청크가 통째로 없는 구형 NAI PNG — Description 평문이 곧 positive.
    if not positive:
        for key, value in root.items():
            if _norm(key) == "description" and _as_text(value):
                positive, pos_src = _as_text(value), str(key)
                break

    if not positive and not negative:
        return None

    bits = [b for b in (f"+{pos_src}" if pos_src else "", f"-{neg_src}" if neg_src else "") if b]
    return _Found(positive, negative, "novelai " + " ".join(bits))


# ─── 인식기 2: 자체 래퍼 JSON ───────────────────────────────────

_WRAPPER_NEG_KEYS = ("negative_prompt", "negativeprompt", "uc", "undesired_content")
_WRAPPER_NESTED = (("image_workspace_state", "settings"), ("settings",), ("params",), ("parameters",))


def _extract_wrapper(root: Dict[str, Any]) -> Optional[_Found]:
    """청크 값 JSON 안에 prompt / negative_prompt 를 담는 래퍼 서비스 포맷.

    top-level 을 먼저 보고, 비어 있을 때만 알려진 중첩 위치를 뒤진다
    (중첩 쪽은 UI 상태 스냅샷이라 실제 생성값과 어긋나는 경우가 있다).
    """
    for chunk, scope in _iter_scopes(root):
        containers: List[Tuple[str, Dict[str, Any]]] = [(chunk, scope)]
        for path in _WRAPPER_NESTED:
            nested = _dig(scope, *path)
            if isinstance(nested, dict):
                containers.append((chunk + "/" + "/".join(path), nested))

        for label, container in containers:
            positive = _as_text(container.get("prompt")) or _as_text(container.get("positive_prompt"))
            negative = ""
            neg_key = ""
            for name in _WRAPPER_NEG_KEYS:
                if _as_text(container.get(name)):
                    negative, neg_key = _as_text(container[name]), name
                    break

            if positive and negative:
                pos_key = "prompt" if _as_text(container.get("prompt")) else "positive_prompt"
                return _Found(
                    positive,
                    negative,
                    f"wrapper +{label}/{pos_key} -{label}/{neg_key}",
                    container,
                )
    return None


# ─── 인식기 3: A1111 평문 ───────────────────────────────────────

_A1111_NEG = re.compile(r"\nNegative prompt:\s*", re.IGNORECASE)
_A1111_TAIL = re.compile(r"\n(?:Steps|Sampler|CFG scale|Seed|Size|Model):", re.IGNORECASE)


def _extract_a1111(root: Dict[str, Any]) -> Optional[_Found]:
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
            return _Found(positive, negative, f"a1111 +{key}")
    return None


# ─── 인식기 4: 범용 경로 탐색(폴백) ─────────────────────────────

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

        if not value.strip():
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


def _container_at(root: Dict[str, Any], path: Sequence[str]) -> Optional[Dict[str, Any]]:
    """선택된 프롬프트가 들어있던 부모 컨테이너를 되짚어 온다."""
    node: Any = root
    for seg in path[:-1]:
        if isinstance(node, str):
            node = _try_json(node)
        if isinstance(node, dict):
            node = node.get(seg)
        elif isinstance(node, (list, tuple)):
            try:
                node = node[int(seg)]
            except Exception:
                return None
        else:
            return None

    if isinstance(node, str):
        node = _try_json(node)
    return node if isinstance(node, dict) else None


def _extract_generic(root: Dict[str, Any]) -> Optional[_Found]:
    pos_cands, neg_cands = _collect_candidates(root)
    if not pos_cands and not neg_cands:
        return None

    positive = pos_cands[0][2] if pos_cands else ""
    negative = neg_cands[0][2] if neg_cands else ""

    bits = []
    affix: Optional[Dict[str, Any]] = None
    if pos_cands:
        bits.append("+" + "/".join(pos_cands[0][1]))
        affix = _container_at(root, pos_cands[0][1])
    if neg_cands:
        bits.append("-" + "/".join(neg_cands[0][1]))

    return _Found(positive, negative, "generic " + " ".join(bits), affix)


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


_STEALTH_MAGICS = {
    "stealth_pnginfo": False,
    "stealth_pngcomp": True,
    "stealth_rgbinfo": False,
    "stealth_rgbcomp": True,
}
_STEALTH_MAGIC_BITS = 15 * 8  # 매직 문자열은 전부 15자
_STEALTH_MAX_BYTES = 4 * 1024 * 1024


def _read_stealth_pnginfo(img: "Image.Image") -> Optional[str]:
    """알파 채널 LSB에 숨겨진 NovelAI stealth pnginfo 를 꺼낸다.

    tEXt/EXIF 가 전부 털린 사본(리사이즈·재인코딩 등)에서도 프롬프트가 살아있는
    경우가 있어 최후 폴백으로만 쓴다. 비트는 열 우선(column-major)으로 읽는다.
    """
    try:
        import numpy as np
    except Exception:
        return None

    if img.mode not in ("RGBA", "LA", "PA"):
        return None

    try:
        arr = np.array(img.convert("RGBA"))
        # (h, w, 4) → 알파만, 열 우선 순서로 펼치기
        alpha = arr[:, :, 3]
        bits = np.unpackbits((alpha & 1).astype(np.uint8).T.reshape(-1, 1), axis=1)[:, 7]
    except Exception:
        return None

    if bits.size < _STEALTH_MAGIC_BITS + 32:
        return None

    def _bits_to_bytes(chunk) -> bytes:
        usable = (chunk.size // 8) * 8
        return np.packbits(chunk[:usable]).tobytes()

    try:
        magic = _bits_to_bytes(bits[:_STEALTH_MAGIC_BITS]).decode("ascii", errors="ignore")
    except Exception:
        return None
    if magic not in _STEALTH_MAGICS:
        return None
    compressed = _STEALTH_MAGICS[magic]

    length_bits = bits[_STEALTH_MAGIC_BITS:_STEALTH_MAGIC_BITS + 32]
    payload_bits = int.from_bytes(_bits_to_bytes(length_bits), "big")
    if payload_bits <= 0 or payload_bits > _STEALTH_MAX_BYTES * 8:
        return None

    start = _STEALTH_MAGIC_BITS + 32
    end = start + payload_bits
    if end > bits.size:
        return None

    payload = _bits_to_bytes(bits[start:end])
    try:
        if compressed:
            payload = gzip.decompress(payload)
        text = payload.decode("utf-8")
    except Exception:
        return None

    return text if text.strip() else None


def _read_metadata(path: Path) -> Dict[str, Any]:
    """PNG tEXt/zTXt/iTXt + EXIF UserComment/ImageDescription 을 평평한 dict로."""
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

            if not out:
                hidden = _read_stealth_pnginfo(img)
                if hidden:
                    out["stealth_pnginfo"] = hidden
                    logger.info("%s stealth pnginfo 에서 메타데이터 복구", _TAG)
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


def _looks_like_directory(value: str) -> bool:
    """위젯 문자열이 실재하는 폴더 경로인지. 프롬프트 본문 오탐을 길이로 막는다."""
    if not value or len(value) > 260 or "\n" in value:
        return False
    if _looks_like_image_name(value):
        return False
    try:
        return Path(value.strip().strip('"').strip("'")).is_dir()
    except Exception:
        return False


def _clean(raw: Any) -> str:
    return str(raw or "").strip().strip('"').strip("'") if raw is not None else ""


def _scan_upstream(prompt: Optional[Dict[str, Any]], unique_id: Any) -> Tuple[List[str], List[str]]:
    """내 image 입력에서 거슬러 올라가며 (파일명, 폴더) 후보를 모은다.

    배치 로더(Load Image Batch 류)는 파일명 위젯이 아예 없고 폴더만 들고 있다.
    파일명은 못 건져도 폴더는 건져서, image_path 로 받은 파일명을 그 안에서
    찾을 수 있게 한다.
    """
    names: List[str] = []
    dirs: List[str] = []

    if not isinstance(prompt, dict) or unique_id is None:
        return names, dirs

    me = _prompt_node(prompt, unique_id)
    if not me:
        return names, dirs

    start = _link_source((me.get("inputs") or {}).get("image"))
    if start is None:
        return names, dirs

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

        for key, value in inputs.items():
            if not isinstance(value, str):
                continue
            if _norm(key) in _FILE_INPUT_KEYS and _looks_like_image_name(value):
                if value not in names:
                    names.append(value)
            elif _looks_like_directory(value):
                cleaned = _clean(value)
                if cleaned not in dirs:
                    dirs.append(cleaned)

        for value in inputs.values():
            nxt = _link_source(value)
            if nxt is not None and nxt not in visited:
                queue.append(nxt)

    return names, dirs


def _find_upstream_image(prompt: Optional[Dict[str, Any]], unique_id: Any) -> Optional[str]:
    """상류 노드의 파일명 위젯 하나(있으면)."""
    names, _ = _scan_upstream(prompt, unique_id)
    return names[0] if names else None


def _path_candidates(raw: str, extra_dirs: Sequence[str]) -> List[Path]:
    direct = Path(raw)
    out: List[Path] = []
    if direct.is_absolute():
        out.append(direct)

    bases: List[Path] = []
    for d in extra_dirs:
        d = _clean(d)
        if d:
            bases.append(Path(d))
    bases.append(Path.cwd())

    if folder_paths is not None:
        try:
            annotated = folder_paths.get_annotated_filepath(raw)
            if annotated:
                out.append(Path(annotated))
        except Exception:
            pass
        for attr in ("input_directory", "output_directory", "temp_directory"):
            base = getattr(folder_paths, attr, None)
            if base:
                bases.append(Path(base))

    for base in bases:
        out.append(base / direct)
        if direct.name != str(direct):
            out.append(base / direct.name)
    return out


def _resolve_path(raw: str, extra_dirs: Sequence[str] = ()) -> Path:
    """파일명/부분 경로 + 후보 폴더들 → 실재하는 파일 경로.

    확장자가 없으면(배치 로더의 filename_text_extension=false) 알려진 이미지
    확장자를 하나씩 붙여가며 재시도한다.
    """
    raw = _clean(raw)
    if not raw:
        raise ValueError("이미지 경로가 비어 있습니다.")

    variants = [raw]
    if Path(raw).suffix.lower() not in _IMAGE_EXTS:
        variants += [raw + ext for ext in _IMAGE_EXTS]

    tried: List[Path] = []
    for variant in variants:
        for cand in _path_candidates(variant, extra_dirs):
            tried.append(cand)
            try:
                if cand.is_file():
                    return cand.resolve()
            except Exception:
                continue

    shown = tried[:12]
    more = f"\n  ... 외 {len(tried) - len(shown)}곳" if len(tried) > len(shown) else ""
    raise FileNotFoundError(
        "원본 이미지 파일을 찾지 못했습니다: " + raw + "\n탐색 경로:\n"
        + "\n".join(f"  - {c}" for c in shown) + more
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


def _first_text(container: Optional[Dict[str, Any]], names: Sequence[str]) -> str:
    if not isinstance(container, dict):
        return ""
    for name in names:
        text = _as_text(container.get(name))
        if text:
            return text
    return ""


def extract_prompts(
    raw: Dict[str, Any],
    include_char_captions: bool = True,
    apply_prefix_suffix: bool = True,
) -> Tuple[str, str, str]:
    """메타데이터 dict → (positive, negative, source).

    노드 밖에서도 부르기 좋게 순수 함수로 떼어놨다(테스트/디버깅용).
    """
    found: Optional[_Found] = None
    for recognizer in (
        lambda: _extract_novelai(raw, include_char_captions),
        lambda: _extract_wrapper(raw),
        lambda: _extract_a1111(raw),
        lambda: _extract_generic(raw),
    ):
        found = recognizer()
        if found and (found.positive or found.negative):
            break
        found = None

    if found is None:
        return "", "", ""

    positive, negative, source = found.positive, found.negative, found.source

    if apply_prefix_suffix:
        prefix = _first_text(found.affix_scope, _PREFIX_KEYS)
        suffix = _first_text(found.affix_scope, _SUFFIX_KEYS)
        trigger = _first_text(found.affix_scope, _TRIGGER_KEYS)
        parts = [p for p in (prefix, trigger, positive, suffix) if p]
        if len(parts) > 1:
            positive = ", ".join(parts)
            source += " (prefix/suffix applied)"

    return positive, negative, source


# ─── 노드 ───────────────────────────────────────────────────────

class BMKPromptFromImage:
    CATEGORY = "BMK/Text"
    FUNCTION = "extract"

    DESCRIPTION = (
        "이미지에 박힌 생성 메타데이터에서 positive / negative 프롬프트만 뽑아냅니다. "
        "NovelAI 공식/API 포맷(V3의 uc, V4·V5의 v4_prompt·char_captions)을 먼저 정확히 인식하고, "
        "자체 래퍼 서비스의 중첩 JSON과 A1111 parameters도 처리합니다. "
        "처음 보는 포맷은 경로 기반 범용 탐색으로 폴백합니다. "
        "배치 로더를 쓸 때는 로더의 filename_text 를 image_path 입력에 연결하세요. "
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
        "a1111",
        "i2i",
    ]

    RETURN_TYPES = ("STRING", "STRING", "STRING", "STRING")
    RETURN_NAMES = ("positive", "negative", "source", "metadata_json")

    OUTPUT_TOOLTIPS = (
        "추출된 positive 프롬프트 원문. NAI 문법이 그대로 남아 있으니 Prompt Converter를 거치세요.",
        "추출된 negative 프롬프트 원문.",
        "감지된 포맷과 키 경로 (예: novelai +Comment/v4_prompt/caption). 비어 있으면 못 찾은 것.",
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
                        "tooltip": "특정 청크만 탐색하려면 키 이름 지정 (예: Comment, parameters). 비우면 전체 자동 탐색.",
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
                "include_char_captions": (
                    "BOOLEAN",
                    {
                        "default": True,
                        "tooltip": "NAI V4/V5 다중 캐릭터 프롬프트(char_captions)를 base 뒤에 줄바꿈으로 이어붙입니다.",
                    },
                ),
                "image_path": (
                    "STRING",
                    {
                        "default": "",
                        "tooltip": (
                            "배치용. 파일명 또는 전체 경로를 직접 지정합니다. "
                            "Load Image Batch 처럼 파일명 위젯이 없는 로더는 그 노드의 filename_text 출력을 "
                            "여기로 연결하세요. 비우면 그래프를 역추적합니다."
                        ),
                    },
                ),
                "search_directory": (
                    "STRING",
                    {
                        "default": "",
                        "tooltip": (
                            "image_path 가 파일명뿐일 때 찾아볼 폴더. "
                            "비워도 상류 로더의 path 위젯을 자동으로 후보에 넣습니다."
                        ),
                    },
                ),
            },
            "hidden": {
                "prompt": "PROMPT",
                "unique_id": "UNIQUE_ID",
            },
        }

    @classmethod
    def _locate(cls, prompt, unique_id, image_path="", search_directory="") -> Path:
        """image_path(명시) 우선, 없으면 그래프 역추적. 폴더 후보는 둘 다 합친다."""
        names, up_dirs = _scan_upstream(prompt, unique_id)
        target = _clean(image_path) or (names[0] if names else "")
        if not target:
            raise FileNotFoundError(
                "이미지 파일명을 찾지 못했습니다.\n"
                "Load Image Batch 처럼 파일명 위젯이 없는 배치 로더는 텐서에 파일명이 실려오지 않습니다. "
                "로더의 filename_text 출력을 이 노드의 image_path 입력으로 연결하세요."
            )
        extra_dirs = [d for d in ([_clean(search_directory)] + up_dirs) if d]
        return _resolve_path(target, extra_dirs)

    @classmethod
    def IS_CHANGED(cls, image=None, metadata_key="", apply_prefix_suffix=True,
                   join_lines=False, include_char_captions=True,
                   image_path="", search_directory="",
                   prompt=None, unique_id=None, **kwargs):
        """파일 mtime/size 만 해시 — 안정값이라 캐시가 정상 동작한다.

        예외를 던지면 ComfyUI가 NaN 취급(always-dirty)하므로 절대 raise 하지 않는다.
        """
        opts = (f"{metadata_key}|{apply_prefix_suffix}|{join_lines}"
                f"|{include_char_captions}|{image_path}|{search_directory}")
        try:
            path = cls._locate(prompt, unique_id, image_path, search_directory)
            st = path.stat()
            return f"{path}|{st.st_mtime_ns}|{st.st_size}|{opts}"
        except Exception:
            return f"BMK_PFI_UNRESOLVED|{opts}"

    def extract(
        self,
        image: Any = None,
        metadata_key: str = "",
        apply_prefix_suffix: bool = True,
        join_lines: bool = False,
        include_char_captions: bool = True,
        image_path: str = "",
        search_directory: str = "",
        prompt: Optional[Dict[str, Any]] = None,
        unique_id: Optional[Any] = None,
    ) -> Tuple[str, str, str, str]:
        try:
            path = self._locate(prompt, unique_id, image_path, search_directory)
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

        positive, negative, source = extract_prompts(
            scope,
            include_char_captions=include_char_captions,
            apply_prefix_suffix=apply_prefix_suffix,
        )

        if join_lines:
            positive = _join_lines(positive)
            negative = _join_lines(negative)

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
