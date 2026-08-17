"""BMK Conditional LoRA — 프롬프트 태그 조건에 따라 LoRA를 켜고 끈다.

배경:
LoRA Manager 의 Lora Loader / Lora Stacker (LoraManager) 는 lora 목록을
AUTOCOMPLETE_TEXT_LORAS 라는 전용 위젯 타입으로 들고 있어서 STRING 출력을
꽂을 수 없고, 카드의 on/off 토글도 워크플로 JSON 에 저장된 상태값일 뿐
실행 시점에 평가되지 않는다. 즉 "프롬프트 내용에 따라 LoRA 자동 on/off" 를
그 노드들만으로는 만들 수 없다.

이 노드는 판정 → 결과물 생성 → 적용의 3단계 중 앞의 두 단계만 담당하고,
적용은 하지 않는다. 그래서 소비자를 골라 쓸 수 있다:

  A) lora_tags (STRING)  → LoRA Text Loader (LoraManager), rgthree Power Prompt
                            등 <lora:...> 문법을 런타임 파싱하는 노드.
  B) lora_stack (LORA_STACK) → Lora Loader (LoraManager) 의 lora_stack 입력,
                            Lora Stack Combiner (LoraManager), CR Apply LoRA
                            Stack, efficiency 로더 등.

B 경로가 있는 이유: LoRA Text Loader (LoraManager) 는 MODEL 입력이 필수라
모델 라인이 확정되기 전 단계에 둘 수 없고, LORA_STACK 출력도 없어서 스택을
합치거나 나중에 적용하는 배선이 불가능하다. LORA_STACK 은 모델을 물리지 않고
"어떤 LoRA 를 얼마로" 라는 정보만 들고 다니므로 조건 판정 시점과 적용 시점을
분리할 수 있다. (스택 자체는 아무것도 로드하지 않는다 — 소비자가 필요하다.)

규칙 문법 (rules 위젯, 한 줄에 하나):

    임계값 | 태그1, 태그2, ..., !제외태그 | <lora:이름:강도>

    2 | dark, darkness, dark room, dark background | <lora:Dark_Slider_Anima:1>
    1 | rain, wet, puddle, !indoors                | <lora:Rain_Anima:0.8>
    # '#' 으로 시작하는 줄은 주석

- 임계값 n : 매칭된 태그가 n개 이상이면 해당 lora 문법을 활성화.
- '!' 접두 태그 : 하나라도 프롬프트에 있으면 그 규칙은 무조건 비활성(거부권).
- 한 줄에 lora 문법을 여러 개 써도 되고(공백 구분), 규칙을 여러 줄 써도 된다.
- 강도는 <lora:이름:모델강도> 또는 <lora:이름:모델강도:클립강도> 둘 다 가능.
  클립강도를 생략하면 모델강도와 같은 값을 쓴다.

태그 정규화 (프롬프트 쪽과 규칙 쪽을 같은 함수로 통과시킨다):
- 대소문자 무시, 앞뒤 공백 제거
- 언더스코어/연속 공백 → 단일 공백  (dark_room == dark room)
- 괄호·대괄호·중괄호·역슬래시 제거 후 끝의 :가중치 제거
  ((dark:1.2) == [dark] == dark)
- 콤마와 개행이 태그 경계. 부분 문자열 매칭은 하지 않으므로
  darkness 가 dark 로 오인되지 않는다.

LoRA 파일명 해석 (LORA_STACK 출력 전용):
LoraManager 의 <lora:...> 문법은 서브폴더를 구분하지 않아 파일명만 들어있지만,
LORA_STACK 소비자는 folder_paths 기준 상대경로를 요구한다. 그래서 loras 폴더
목록에서 전체경로 → 확장자 제거 → 파일명 → 확장자 제거한 파일명 순으로 조회해
실제 경로로 복원한다. 못 찾은 항목은 스택에서 빠지지만 lora_tags 문자열에는
그대로 남는다 (텍스트 소비자는 자체 해석 규칙을 가질 수 있으므로).
동명이 여러 폴더에 있으면 첫 후보를 쓰고 report 에 경고를 남긴다.

옵션:
- count_mode : unique = 서로 다른 태그 종류 수(기본), occurrence = 등장 횟수.
    "dark, dark" 는 unique 에서 1, occurrence 에서 2로 센다.
- append_to_prompt : prompt 출력 끝에 활성 lora 문법을 붙일지 여부.
    LoRA Text Loader 계열에 프롬프트를 통째로 넘길 때 True,
    lora_tags / lora_stack 출력만 따로 쓸 때는 False.
- stack_scope : LORA_STACK 에 무엇을 담을지.
    merged        = 입력 lora_tags + 활성 항목 전부 (기본, lora_tags 출력과 동일 집합)
    activated_only= 이 노드가 켠 항목만.
    ※ LoraManager 로더가 이미 적용한 LoRA 를 lora_tags 로 받아왔는데 스택도
      같은 모델에 적용하면 이중 적용이 된다. 그럴 땐 activated_only 를 쓸 것.

입출력 배선:
- prompt 입력은 판정 대상. 이미 들어있는 <lora:...> 는 판정에서 제외된다.
- lora_tags(optional) 에 LoraManager 의 loaded_loras 를 물리면 병합해서 내보낸다.
  같은 lora 이름이 이미 있으면 추가하지 않는다 (경로/확장자 무시, 파일명 기준).
- lora_stack(optional) 에 상위 스택을 물리면 앞에 붙여 이어받는다. 상위 스택에
  이미 있는 lora 도 중복 판정 대상이다.
- report 출력에 규칙별 판정 결과와 파일 해석 결과가 남는다.

버전 이력:
- v1: 최초 구현. 임계값/제외태그 규칙 테이블, unique·occurrence 카운트,
      lora_tags 병합 및 이름 기준 중복 제거, 판정 리포트 출력.
- v2: LORA_STACK 입출력 추가 (모델 없이도 조건부 LoRA 를 실어 나를 수 있게).
      loras 폴더 조회 기반 파일명 → 상대경로 해석기, 클립강도 표기
      (<lora:이름:모델:클립>) 지원, stack_scope 옵션 추가.
      ※ 출력 포트는 append-only — lora_stack 을 report 뒤에 붙였다.
        중간 삽입은 저장된 워크플로의 링크를 어긋나게 한다.
"""

from __future__ import annotations

import logging
import math
import re
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

# ComfyUI 코어 모듈. 단위 테스트 등 ComfyUI 밖에서 import 되는 경우를 위해
# 격리한다 — 없으면 LORA_STACK 만 비고 나머지 출력은 정상 동작한다.
try:
    import folder_paths
except Exception:  # pragma: no cover - ComfyUI 런타임에서는 항상 성공
    folder_paths = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)
_TAG = "[ComfyUI_BMK_Nodes::ConditionalLora]"


# ─── 상수 ────────────────────────────────────────────────────────

_ANGLE_RE = re.compile(r"<[^>]*>")
_LORA_TAG_RE = re.compile(r"<lora:([^:>]+?)(?::([^>]*))?>", re.IGNORECASE)
_BRACKET_RE = re.compile(r"[()\[\]{}\\]")
_WEIGHT_RE = re.compile(r":\s*-?\d*\.?\d+\s*$")
_SPACE_RE = re.compile(r"[\s_]+")

_LORA_EXTS = (".safetensors", ".ckpt", ".pt", ".pth", ".sft", ".bin")

_DEFAULT_RULES = (
    "# 임계값 | 태그1, 태그2, ..., !제외태그 | <lora:이름:강도>\n"
    "2 | dark, darkness, dark room, dark background | <lora:Dark_Slider_Anima:1>\n"
)


# ─── 태그 정규화 ──────────────────────────────────────────────────

def _normalize_tag(raw: str) -> str:
    """'(dark_room:1.2)' → 'dark room' 형태로 정규화한다.

    프롬프트 쪽 태그와 규칙 쪽 태그를 같은 함수로 통과시켜야 비교가 성립한다.
    """
    text = _BRACKET_RE.sub("", raw)
    text = _WEIGHT_RE.sub("", text.strip())
    text = _SPACE_RE.sub(" ", text)
    return text.strip().lower()


def _split_tags(prompt: str) -> List[str]:
    """프롬프트를 정규화된 태그 리스트로 평탄화한다 (등장 순서 유지).

    이미 들어있는 <lora:...> 등 꺾쇠 토큰은 판정 대상에서 제외한다 —
    lora 파일명에 우연히 dark 가 들어있어도 조건에 영향을 주지 않게.
    """
    body = _ANGLE_RE.sub("", prompt).replace("\r\n", "\n").replace("\n", ",")
    return [t for t in (_normalize_tag(p) for p in body.split(",")) if t]


# ─── LoRA 이름 / 강도 ─────────────────────────────────────────────

def _strip_lora_ext(name: str) -> str:
    lowered = name.lower()
    for ext in _LORA_EXTS:
        if lowered.endswith(ext):
            return name[: -len(ext)]
    return name


def _lora_key(name: str) -> str:
    """중복 판정용 lora 식별 키 — 경로와 확장자를 뗀 파일명(소문자)."""
    base = name.replace("\\", "/").rsplit("/", 1)[-1].strip()
    return _strip_lora_ext(base).strip().lower()


def _parse_strengths(raw: Optional[str]) -> Tuple[float, float]:
    """'1.0' 또는 '1.0:0.8' 을 (모델강도, 클립강도) 로 해석한다.

    클립강도가 없으면 모델강도를 그대로 쓴다 (A1111 / LoraManager 관례).
    숫자가 아니거나 NaN/inf 면 1.0 으로 폴백 — 규칙 오타가 샘플링을
    망가뜨리지 않게.
    """
    def _to_float(token: str, fallback: float) -> float:
        try:
            value = float(token)
        except (TypeError, ValueError):
            return fallback
        return value if math.isfinite(value) else fallback

    if not raw:
        return 1.0, 1.0
    parts = [p.strip() for p in raw.split(":")]
    model_str = _to_float(parts[0], 1.0) if parts else 1.0
    clip_str = model_str
    if len(parts) > 1 and parts[1]:
        clip_str = _to_float(parts[1], model_str)
    return model_str, clip_str


def _iter_lora_tags(text: str):
    """문자열에서 (전체태그, 이름, 모델강도, 클립강도) 를 순서대로 뽑는다."""
    for match in _LORA_TAG_RE.finditer(text or ""):
        model_str, clip_str = _parse_strengths(match.group(2))
        yield match.group(0), match.group(1).strip(), model_str, clip_str


# ─── loras 폴더 조회 / 경로 복원 ──────────────────────────────────

def _build_lora_index() -> Dict[str, List[str]]:
    """loras 폴더 목록으로 조회 인덱스를 만든다.

    캐시하지 않는다 — folder_paths.get_filename_list 가 이미 캐시를 갖고 있고,
    여기서 또 들고 있으면 LoRA 를 새로 넣었을 때 재시작 전까지 못 찾는다.
    """
    index: Dict[str, List[str]] = {}
    if folder_paths is None:
        return index

    try:
        entries = folder_paths.get_filename_list("loras")
    except Exception:
        logger.warning("%s loras 폴더 목록 조회 실패", _TAG, exc_info=True)
        return index

    def _add(key: str, value: str) -> None:
        if not key:
            return
        bucket = index.setdefault(key, [])
        if value not in bucket:
            bucket.append(value)

    for entry in entries:
        normalized = str(entry).replace("\\", "/")
        basename = normalized.rsplit("/", 1)[-1]
        _add(normalized.lower(), entry)
        _add(_strip_lora_ext(normalized).lower(), entry)
        _add(basename.lower(), entry)
        _add(_strip_lora_ext(basename).lower(), entry)
    return index


def _resolve_lora_path(
    name: str, index: Dict[str, List[str]],
) -> Tuple[Optional[str], int]:
    """<lora:이름> 의 이름을 folder_paths 기준 상대경로로 복원한다.

    Returns:
        (해석된 경로 또는 None, 동명 후보 개수)
    """
    query = str(name).replace("\\", "/").strip()
    if not query:
        return None, 0
    basename = query.rsplit("/", 1)[-1]

    for key in (
        query.lower(),
        _strip_lora_ext(query).lower(),
        basename.lower(),
        _strip_lora_ext(basename).lower(),
    ):
        hits = index.get(key)
        if hits:
            return hits[0], len(hits)
    return None, 0


# ─── 규칙 파싱 / 평가 ─────────────────────────────────────────────

class _Rule:
    __slots__ = ("lineno", "threshold", "targets", "excludes", "syntax")

    def __init__(
        self,
        lineno: int,
        threshold: int,
        targets: Set[str],
        excludes: Set[str],
        syntax: str,
    ) -> None:
        self.lineno = lineno
        self.threshold = threshold
        self.targets = targets
        self.excludes = excludes
        self.syntax = syntax


def _parse_rules(rules: str) -> Tuple[List[_Rule], List[str]]:
    """규칙 텍스트를 _Rule 목록으로 파싱한다.

    잘못된 줄은 예외를 던지지 않고 경고 목록에 모은다 — 규칙 한 줄의 오타가
    생성 전체를 막지 않도록. (경고는 report 출력과 로그 양쪽에 남는다)
    """
    parsed: List[_Rule] = []
    warnings: List[str] = []

    for lineno, raw_line in enumerate(rules.replace("\r\n", "\n").split("\n"), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        parts = [p.strip() for p in line.split("|")]
        if len(parts) != 3:
            warnings.append(
                f"[{lineno}] 형식 오류 — '임계값 | 태그들 | lora문법' 3칸이 아님: {line}"
            )
            continue

        threshold_raw, tags_raw, syntax = parts

        try:
            threshold = int(threshold_raw)
        except ValueError:
            warnings.append(f"[{lineno}] 임계값이 정수가 아님: {threshold_raw!r}")
            continue
        if threshold < 1:
            warnings.append(f"[{lineno}] 임계값은 1 이상이어야 함: {threshold}")
            continue

        targets: Set[str] = set()
        excludes: Set[str] = set()
        for token in tags_raw.split(","):
            token = token.strip()
            if not token:
                continue
            if token.startswith("!"):
                normalized = _normalize_tag(token[1:])
                if normalized:
                    excludes.add(normalized)
            else:
                normalized = _normalize_tag(token)
                if normalized:
                    targets.add(normalized)

        if not targets:
            warnings.append(f"[{lineno}] 매칭 태그가 비어 있음 (제외 태그만 있음)")
            continue
        if not _LORA_TAG_RE.search(syntax):
            warnings.append(
                f"[{lineno}] lora 문법을 찾지 못함 (<lora:이름:강도> 형식): {syntax!r}"
            )
            continue

        parsed.append(_Rule(lineno, threshold, targets, excludes, syntax))

    return parsed, warnings


def _evaluate_rule(
    rule: _Rule,
    tags: Sequence[str],
    unique_tags: Set[str],
    count_mode: str,
) -> Tuple[bool, int, List[str], List[str]]:
    """규칙 하나를 평가한다.

    Returns:
        (활성 여부, 매칭 점수, 매칭된 태그 목록, 발동한 제외 태그 목록)
    """
    blocked = sorted(rule.excludes & unique_tags)
    if count_mode == "occurrence":
        hits = [t for t in tags if t in rule.targets]
    else:
        hits = sorted(rule.targets & unique_tags)

    score = len(hits)
    active = (not blocked) and (score >= rule.threshold)
    return active, score, hits, blocked


# ─── 노드 클래스 ──────────────────────────────────────────────────

class BMKConditionalLora:
    TITLE = "BMK Conditional LoRA"
    CATEGORY = "BMK/Text"
    FUNCTION = "resolve"
    RETURN_TYPES = ("STRING", "STRING", "STRING", "LORA_STACK")
    RETURN_NAMES = ("prompt", "lora_tags", "report", "lora_stack")
    OUTPUT_TOOLTIPS = (
        "판정에 사용한 프롬프트. append_to_prompt 가 켜져 있으면 활성 lora 문법이 뒤에 붙는다.",
        "활성 lora 문법 (입력 lora_tags 가 있으면 병합·중복 제거된 결과).",
        "규칙별 판정 결과와 파일 해석 로그. 왜 켜졌는지/안 켜졌는지 추적용.",
        "(경로, 모델강도, 클립강도) 튜플 리스트. LoraManager Lora Loader 의 "
        "lora_stack 입력이나 Lora Stack Combiner 등에 물린다. 모델은 필요 없다.",
    )
    DESCRIPTION = (
        "프롬프트에 지정한 태그가 n개 이상 등장할 때만 해당 LoRA를 켭니다. "
        "결과를 <lora:이름:강도> 문자열과 LORA_STACK 두 형태로 내보내므로, "
        "모델 입력이 필요한 LoRA Text Loader를 거치지 않고도 조건부 LoRA를 배선할 수 있습니다."
    )
    SEARCH_ALIASES = [
        "conditional lora",
        "lora switch",
        "auto lora",
        "tag lora",
        "lora trigger",
        "prompt lora",
        "lora stack from prompt",
        "조건부 로라",
        "로라 자동",
        "태그 로라",
        "로라 스위치",
        "로라 스택",
    ]

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "prompt": (
                    "STRING",
                    {
                        "multiline": True,
                        "dynamicPrompts": False,
                        "tooltip": (
                            "판정 대상 프롬프트. 콤마·개행이 태그 경계이며, "
                            "이미 들어있는 <lora:...> 는 판정에서 제외됩니다."
                        ),
                    },
                ),
                "rules": (
                    "STRING",
                    {
                        "multiline": True,
                        "dynamicPrompts": False,
                        "default": _DEFAULT_RULES,
                        "tooltip": (
                            "한 줄에 규칙 하나: '임계값 | 태그1, 태그2, !제외태그 | "
                            "<lora:이름:강도>'. '#' 으로 시작하는 줄은 주석입니다."
                        ),
                    },
                ),
                "count_mode": (
                    ["unique", "occurrence"],
                    {
                        "default": "unique",
                        "tooltip": (
                            "unique = 서로 다른 태그 종류 수로 카운트(기본). "
                            "occurrence = 등장 횟수로 카운트."
                        ),
                    },
                ),
                "append_to_prompt": (
                    "BOOLEAN",
                    {
                        "default": True,
                        "tooltip": (
                            "prompt 출력 끝에 활성 lora 문법을 붙입니다. "
                            "LoRA Text Loader 에 프롬프트째로 넘길 때 켜세요."
                        ),
                    },
                ),
                "stack_scope": (
                    ["merged", "activated_only"],
                    {
                        "default": "merged",
                        "tooltip": (
                            "LORA_STACK 에 담을 범위. merged = 입력 lora_tags 포함 "
                            "전체(기본), activated_only = 이 노드가 켠 것만. "
                            "입력 lora_tags 를 이미 다른 로더가 적용했다면 "
                            "activated_only 로 두어 이중 적용을 피하세요."
                        ),
                    },
                ),
            },
            "optional": {
                "lora_tags": (
                    "STRING",
                    {
                        "forceInput": True,
                        "tooltip": (
                            "기존 LoRA 태그 문자열(LoraManager loaded_loras 등). "
                            "같은 이름이 이미 있으면 중복 추가하지 않습니다."
                        ),
                    },
                ),
                "lora_stack": (
                    "LORA_STACK",
                    {
                        "tooltip": (
                            "상위 LORA_STACK. 앞에 그대로 이어붙이며, 여기 이미 있는 "
                            "lora 는 중복 추가하지 않습니다."
                        ),
                    },
                ),
            },
        }

    # ── 스택 생성 ────────────────────────────────────────────────

    @staticmethod
    def _build_stack(
        source_tags: str,
        upstream: Optional[Sequence[Any]],
        report_lines: List[str],
    ) -> List[Tuple[str, float, float]]:
        """lora 문법 문자열을 LORA_STACK 튜플 리스트로 변환한다.

        해석 실패 항목은 스택에서 제외하고 report 에 남긴다 — 존재하지 않는
        경로를 스택에 넣으면 소비자 로더에서 터진다.
        """
        stack: List[Tuple[str, float, float]] = []
        seen: Set[str] = set()

        for item in upstream or []:
            if not isinstance(item, (list, tuple)) or len(item) < 3:
                report_lines.append(f"  ⚠ 상위 스택 항목 형식이 예상 밖이라 무시: {item!r}")
                continue
            name = str(item[0])
            model_str, clip_str = float(item[1]), float(item[2])
            stack.append((name, model_str, clip_str))
            seen.add(_lora_key(name))
            report_lines.append(f"  · (상위) {name} [{model_str}/{clip_str}]")

        if not source_tags.strip():
            return stack

        index = _build_lora_index()
        if not index:
            report_lines.append(
                "  ⚠ loras 폴더 목록이 비어 있어 LORA_STACK 을 만들 수 없습니다."
            )
            return stack

        for tag, name, model_str, clip_str in _iter_lora_tags(source_tags):
            key = _lora_key(name)
            if key in seen:
                report_lines.append(f"  · {tag} → 스택에 이미 있어 생략")
                continue

            path, candidates = _resolve_lora_path(name, index)
            if path is None:
                report_lines.append(f"  ⚠ {tag} → loras 폴더에서 파일을 찾지 못함 (스택 제외)")
                logger.warning("%s LoRA 파일 해석 실패: %s", _TAG, name)
                continue

            seen.add(key)
            stack.append((path, model_str, clip_str))
            suffix = f" (동명 {candidates}개 중 첫 후보)" if candidates > 1 else ""
            report_lines.append(f"  · {tag} → {path} [{model_str}/{clip_str}]{suffix}")
            if candidates > 1:
                logger.warning(
                    "%s '%s' 동명 후보 %d개 — '%s' 선택", _TAG, name, candidates, path
                )

        return stack

    # ── 실행 ─────────────────────────────────────────────────────

    def resolve(
        self,
        prompt: str,
        rules: str,
        count_mode: str,
        append_to_prompt: bool,
        stack_scope: str = "merged",
        lora_tags: Optional[str] = None,
        lora_stack: Optional[Sequence[Any]] = None,
    ):
        base_tags = (lora_tags or "").strip()

        tags = _split_tags(prompt)
        unique_tags = set(tags)

        parsed, report_lines = _parse_rules(rules)
        for warning in report_lines:
            logger.warning("%s 규칙 파싱: %s", _TAG, warning)

        # 중복 판정 기준: 입력 lora_tags + 상위 스택에 이미 있는 것들
        existing_keys: Set[str] = {
            _lora_key(name) for _tag, name, _m, _c in _iter_lora_tags(base_tags)
        }
        for item in lora_stack or []:
            if isinstance(item, (list, tuple)) and item:
                existing_keys.add(_lora_key(str(item[0])))

        activated: List[str] = []

        for rule in parsed:
            active, score, hits, blocked = _evaluate_rule(
                rule, tags, unique_tags, count_mode
            )

            if blocked:
                report_lines.append(
                    f"[{rule.lineno}] BLOCK {score}/{rule.threshold}  "
                    f"{rule.syntax}  ← 제외 태그 {blocked}"
                )
                continue
            if not active:
                report_lines.append(
                    f"[{rule.lineno}] off   {score}/{rule.threshold}  {rule.syntax}"
                )
                continue

            # 이미 적용된 lora 는 건너뛰되, 같은 규칙의 나머지 태그는 살린다.
            keep: List[str] = []
            skipped: List[str] = []
            for tag, name, _model_str, _clip_str in _iter_lora_tags(rule.syntax):
                key = _lora_key(name)
                if key in existing_keys:
                    skipped.append(tag)
                    continue
                existing_keys.add(key)
                keep.append(tag)

            activated.extend(keep)
            note = f"  (중복 생략: {' '.join(skipped)})" if skipped else ""
            report_lines.append(
                f"[{rule.lineno}] ON    {score}/{rule.threshold}  "
                f"{' '.join(keep) or '-'}  ← {hits}{note}"
            )

        new_syntax = " ".join(activated)
        merged_tags = " ".join(part for part in (base_tags, new_syntax) if part)

        out_prompt = prompt
        if append_to_prompt and new_syntax:
            stem = prompt.rstrip().rstrip(",").rstrip()
            out_prompt = f"{stem}, {new_syntax}" if stem else new_syntax

        source_tags = new_syntax if stack_scope == "activated_only" else merged_tags
        report_lines.append(f"── LORA_STACK ({stack_scope}) ──")
        out_stack = self._build_stack(source_tags, lora_stack, report_lines)
        if not out_stack:
            report_lines.append("  (비어 있음)")

        if activated:
            logger.info("%s 활성 LoRA: %s", _TAG, new_syntax)

        report = "\n".join(report_lines)
        return (out_prompt, merged_tags, report, out_stack)


NODE_CLASS_MAPPINGS = {
    "BMKConditionalLora": BMKConditionalLora,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "BMKConditionalLora": "BMK Conditional LoRA",
}
