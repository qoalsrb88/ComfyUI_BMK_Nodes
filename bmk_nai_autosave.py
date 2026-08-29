"""BMK NAI Autosave Name — NAIDGenerator 자동저장 PNG 의 파일명을 제어한다.

배경:
ComfyUI_NAIDGenerator (bedovyy) 의 GenerateNAID 는 NAI 응답 zip 에서 꺼낸
원본 PNG 바이트를 그대로 output/NAI_autosave/NAI_autosave_#####_.png 에
기록한다. 재인코딩을 하지 않으므로 NovelAI 메타데이터가 온전히 보존되지만,
파일명 프리픽스가 소스에 문자열 리터럴로 박혀 있어 제어할 수 없다. 여러 장을
뽑아 놓으면 탐색기에서 어떤 게 어떤 설정인지 구분이 안 된다.

하류에 저장 노드를 붙이는 방식은 원리상 불가능하다. 원본 바이트는
generate() 지역변수로만 존재하고, bytes_to_image() 로 IMAGE 텐서가 되는
순간 PNG 텍스트 청크가 사라진다. 따라서 개입 지점은 generate() 실행
시점뿐이며, 이 파일은 런타임 몽키패치로 그 지점에 끼어든다.

설계:
1) 파라미터 전달은 위젯이 아니라 option 체인으로 한다.
   generate() 는 option dict 에서 자기가 아는 키만 읽고 나머지는 무시하므로,
   ModelOption 과 같은 형태의 노드가 option["bmk_autosave"] 를 실어 보내면
   된다. GenerateNAID 의 INPUT_TYPES 를 건드리지 않으니 widgets_values 위치
   배열이 그대로고, 기존 워크플로 마이그레이션이 없다. 노드를 꽂지 않으면
   패치가 설치조차 되지 않아 원본 동작 100% 다.

2) 파일명에 쓸 값은 GenerateNAID._post_image 후킹으로 얻는다.
   보정된 해상도(calculate_resolution / limit_opus_free), 28로 클램프된 steps,
   ddim → ddim_v3 치환, infill 시 model 에 붙는 -inpainting 접미사는 전부
   generate() 안에서 결정되는 지역변수다. 바깥에서 재계산하면 upstream 로직
   변경 시 파일명과 실제 메타데이터가 어긋난다. _post_image 는 NAI 로 전송되는
   최종 (model, action, parameters) 를 인자로 받고 자동저장 블록 직전에
   호출되므로, 여기서 낚아채면 파일명이 메타데이터와 정의상 일치한다.

3) folder_paths 는 전역이 아니라 정의 모듈 네임스페이스만 프록시로 바꾼다.
   generate.__globals__ 가 곧 NAIDGenerator nodes.py 의 __dict__ 이므로
   거기의 folder_paths 만 교체하면 패치 범위가 그 모듈 안에 갇힌다. 코어
   SaveImage 등은 영향권 밖이고, 전역 스왑/복원의 경합 문제도 없다.
   프록시는 프리픽스가 정확히 "NAI_autosave" 이고 슬롯이 채워져 있을 때만
   동작하며, 그 외에는 원본 함수로 그대로 위임한다.

   실제 파일 쓰기는 원본 코드가 그대로 수행하므로 raw bytes 보존은 자동이고,
   get_save_image_path 의 경로 탈출 검증·서브폴더 분리·카운터 스캔도 그대로
   물려받는다. 원본의 d.mkdir(exist_ok=True) 는 비재귀라 중첩 하위 폴더에서
   실패하므로, 프록시가 반환 직전에 parents=True 로 미리 만들어 둔다.

4) 설치는 지연 실행한다.
   custom_nodes 는 폴더명 알파벳 순으로 로드되어 ComfyUI_BMK_Nodes 가
   ComfyUI_NAIDGenerator 보다 먼저 import 된다. import 시점에는 대상이
   sys.modules 에 없으므로, 노드의 set_option() 이 처음 실행될 때 설치한다.
   set_option 은 자기 출력이 generate 로 흘러가므로 그래프 순서상 항상
   generate 보다 먼저 실행된다. 대상 탐색은 sys.modules 순회가 아니라
   코어의 NODE_CLASS_MAPPINGS["GenerateNAID"] 를 쓰므로 설치 폴더명이
   무엇이든 상관없다.

upstream 의존 가정 (기능이 조용히 꺼졌다면 여기부터 대조할 것):
- 코어 NODE_CLASS_MAPPINGS 에 "GenerateNAID" 키가 있다.
- GenerateNAID.generate 가 option 을 키워드 인자로 받는다.
- GenerateNAID._post_image 가 (access_token, prompt, model, action,
  parameters, ...) 순서로 호출된다.
- 자동저장 프리픽스 리터럴이 "NAI_autosave" 다.
- 저장 파일명 포맷이 f"{filename}_{counter:05}_.png" 다.
  (keep_counter=False 일 때 rename 대상 경로를 이 포맷으로 재구성한다.
   재구성한 경로가 없으면 rename 을 건너뛰고 경고만 남긴다.)
설치 시 위 항목을 검사하고, 구조가 다르면 패치하지 않고 경고 로그만 남긴다.
조용히 어긋나는 것보다 명시적으로 비활성화되는 편이 낫다.

사용법:
  [BMK NAI Autosave Name] --option--> [ModelOption] --option--> [GenerateNAID]

  option 입력이 있는 NAID 노드(ModelOption / NetworkOption /
  VibeTransferOption / CharacterReferenceOption)는 deepcopy 후 update 하므로
  체인 어디에 끼워도 값이 보존된다. 다만 Img2ImgOption 과 InpaintingOption 은
  option 입력이 없어 매번 새 dict 를 만드는 체인의 시작점이다. 이 노드는
  반드시 그 뒤에 두어야 한다.

토큰 (모두 NAI 로 전송된 확정값 기준):
  %time          time_format 위젯의 strftime 포맷
  %model         nai-diffusion-4-5-full 등 (infill 시 -inpainting 포함)
  %action        generate / img2img / infill
  %sampler       ddim 은 실제 전송값인 ddim_v3 로 나온다
  %scheduler     native / karras / exponential / polyexponential
  %steps %seed %width %height
  %cfg %cfg_rescale %uncond_scale
  %smea          none / SMEA / SMEA+DYN
  %variety %decrisper   on / off
  템플릿 안의 / 는 하위 폴더가 된다. Windows 금지문자는 _ 로 치환한다.
  ComfyUI 고유의 %date:...% 는 콜론이 금지문자 치환에 걸리므로 지원하지
  않는다. %time 과 time_format 을 쓸 것.

제약:
- director tools(base_augment) 는 별도 코드 경로이고 생성 파라미터가 없어
  이번 버전 범위 밖이다. 해당 경로의 자동저장은 원본 동작 그대로 남는다.
- NAIDGenerator 는 GPL-3.0 이다. 이 파일은 런타임에 그 내부 구현에 결합하는
  성격이므로 패키지 나머지(MIT)와 구분해 GPL-3.0 으로 둔다.

버전 이력:
- v1 (2026-08): 최초. option 체인 기반 파라미터 전달, _post_image 후킹으로
      확정 파라미터 수집, 정의 모듈 한정 folder_paths 프록시, 지연 설치,
      구조 검증 후 실패 시 무패치 폴백, keep_counter=False 시 카운터 제거
      rename.
"""

from __future__ import annotations

import contextvars
import copy
import functools
import inspect
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)
_TAG = "[ComfyUI_BMK_Nodes::NAIAutosave]"


# ─── 상수 ────────────────────────────────────────────────────────

# generate() 안에 리터럴로 박혀 있는 자동저장 프리픽스. 프록시는 이 값이
# 들어올 때만 개입한다.
_AUTOSAVE_PREFIX = "NAI_autosave"

# option dict 에 실어 보내는 키. NAID 쪽이 모르는 키라 무시된다.
_OPTION_KEY = "bmk_autosave"

_DEFAULT_TEMPLATE = (
    "[%time]_[%model]_[step%steps]_[cfg%cfg]_"
    "[%sampler]_[%scheduler]_[seed%seed]_[%widthx%height]"
)
_DEFAULT_TIME_FORMAT = "%y%m%d-%H%M%S"

# 슬래시는 하위 폴더 표현용이므로 보존한다.
_FILENAME_FORBIDDEN = ("<", ">", ":", '"', "|", "?", "*", "\x00")

# 실행 중인 generate 호출에 대한 설정 슬롯. _post_image 후킹과 folder_paths
# 프록시가 같은 스레드/컨텍스트에서 이 값을 공유한다.
_slot: contextvars.ContextVar[Optional[Dict[str, Any]]] = contextvars.ContextVar(
    "bmk_nai_autosave_slot", default=None
)

_installed = False
_install_refused = False  # 구조 검증 실패 — 재시도해도 소용없음


# ─── 토큰 해석 ────────────────────────────────────────────────────

def _format_value(value: Any) -> str:
    """float 는 꼬리 0을 떼고, None 은 빈 문자열로."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "on" if value else "off"
    if isinstance(value, float):
        text = f"{value:.4f}".rstrip("0").rstrip(".")
        return text or "0"
    return str(value)


def _build_tokens(cfg: Dict[str, Any]) -> Dict[str, str]:
    """확정 파라미터 dict 로부터 토큰 → 값 매핑을 만든다."""
    params: Dict[str, Any] = cfg.get("params") or {}

    if params.get("sm_dyn"):
        smea = "SMEA+DYN"
    elif params.get("sm"):
        smea = "SMEA"
    else:
        smea = "none"

    return {
        "%uncond_scale": _format_value(params.get("uncond_scale")),
        "%cfg_rescale": _format_value(params.get("cfg_rescale")),
        "%decrisper": _format_value(bool(params.get("dynamic_thresholding"))),
        "%scheduler": _format_value(params.get("noise_schedule")),
        "%variety": _format_value("skip_cfg_above_sigma" in params),
        "%sampler": _format_value(params.get("sampler")),
        "%height": _format_value(params.get("height")),
        "%action": _format_value(cfg.get("action")),
        "%model": _format_value(cfg.get("model")),
        "%steps": _format_value(params.get("steps")),
        "%width": _format_value(params.get("width")),
        "%smea": smea,
        "%seed": _format_value(params.get("seed")),
        "%time": _format_value(cfg.get("time")),
        "%cfg": _format_value(params.get("scale")),
    }


def _sanitize(text: str) -> str:
    """파일명 금지문자를 _ 로 치환. / 는 하위 폴더 구분자로 보존한다."""
    parts = []
    for segment in text.replace("\\", "/").split("/"):
        for char in _FILENAME_FORBIDDEN:
            segment = segment.replace(char, "_")
        segment = segment.strip().rstrip(".")
        if segment:
            parts.append(segment)
    return "/".join(parts)


def _resolve_template(cfg: Dict[str, Any]) -> str:
    """템플릿의 %token 을 치환하고 파일명으로 정제한다.

    토큰 길이 내림차순으로 치환해야 %cfg 가 %cfg_rescale 을 먼저 잡아먹지
    않는다 (xy_plot.py 와 같은 규칙).
    """
    template = cfg.get("template") or _DEFAULT_TEMPLATE
    tokens = _build_tokens(cfg)
    for token in sorted(tokens, key=len, reverse=True):
        template = template.replace(token, tokens[token])
    return _sanitize(template)


def _resolve_save_dir(path: str, output_root: str) -> str:
    """빈 값 = output 루트, 절대경로 = 그대로, 상대경로 = output 하위."""
    path = (path or "").strip()
    if not path:
        return os.path.abspath(output_root)
    if os.path.isabs(path):
        return os.path.abspath(path)
    return os.path.abspath(os.path.join(output_root, path))


# ─── folder_paths 프록시 ──────────────────────────────────────────

class _FolderPathsProxy:
    """NAIDGenerator nodes.py 네임스페이스 전용 folder_paths 대역.

    get_save_image_path 만 가로채고 나머지 속성은 원본 모듈로 위임한다.
    개입 조건이 어긋나면 인자를 그대로 원본에 넘기므로, 최악의 경우에도
    원본 동작으로 수렴한다.
    """

    def __init__(self, real_module: Any) -> None:
        self._real = real_module

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)

    def get_save_image_path(
        self,
        filename_prefix: str,
        output_dir: str,
        image_width: int = 0,
        image_height: int = 0,
    ) -> Tuple[str, str, int, str, str]:
        cfg = _slot.get()
        if cfg is None or filename_prefix != _AUTOSAVE_PREFIX:
            return self._real.get_save_image_path(
                filename_prefix, output_dir, image_width, image_height
            )

        try:
            resolved = _resolve_template(cfg)
            if not os.path.basename(resolved):
                raise ValueError("해석 결과가 비어 있습니다")

            params: Dict[str, Any] = cfg.get("params") or {}
            width = int(params.get("width") or image_width or 0)
            height = int(params.get("height") or image_height or 0)

            save_dir = _resolve_save_dir(
                cfg.get("path", ""), self._real.get_output_directory()
            )
            result = self._real.get_save_image_path(resolved, save_dir, width, height)

            # 원본의 d.mkdir(exist_ok=True) 는 비재귀라 중첩 폴더에서 깨진다.
            Path(result[0]).mkdir(parents=True, exist_ok=True)

            cfg["saved"] = (result[0], result[1], result[2])
            return result

        except Exception as exc:
            logger.warning(
                "%s 파일명 해석에 실패해 원본 동작으로 폴백합니다: %s", _TAG, exc
            )
            cfg["saved"] = None
            return self._real.get_save_image_path(
                filename_prefix, output_dir, image_width, image_height
            )


# ─── 후처리 (카운터 제거) ─────────────────────────────────────────

def _finalize(cfg: Dict[str, Any]) -> None:
    """keep_counter=False 면 _00001_ 꼬리를 떼어낸다.

    원본의 f"{filename}_{counter:05}_.png" 는 리터럴이라 프리픽스만 바꿔서는
    없앨 수 없다. 프록시가 반환한 값으로 경로를 재구성해 rename 한다.
    재구성한 경로가 없으면 upstream 포맷이 바뀐 것이므로 건너뛴다.
    """
    if cfg.get("keep_counter", False):
        return
    saved = cfg.get("saved")
    if not saved:
        return

    folder, filename, counter = saved
    source = Path(folder) / f"{filename}_{counter:05}_.png"
    if not source.exists():
        logger.warning(
            "%s 저장 파일을 찾지 못해 카운터 제거를 건너뜁니다 (%s). "
            "upstream 파일명 포맷이 바뀌었을 수 있습니다.",
            _TAG,
            source.name,
        )
        return

    target = Path(folder) / f"{filename}.png"
    index = 2
    while target.exists():
        target = Path(folder) / f"{filename}_{index:02d}.png"
        index += 1
        if index > 9999:
            return

    try:
        source.rename(target)
    except OSError as exc:
        logger.warning("%s 이름 변경 실패 — 원본 파일명을 유지합니다: %s", _TAG, exc)


# ─── 패치 설치 ────────────────────────────────────────────────────

def _validate(cls: Any) -> Optional[str]:
    """구조 가정을 검사한다. 문제가 있으면 사유 문자열을 반환."""
    generate = getattr(cls, "generate", None)
    post_image = getattr(cls, "_post_image", None)

    if not callable(generate):
        return "GenerateNAID.generate 를 찾을 수 없습니다"
    if not callable(post_image):
        return "GenerateNAID._post_image 를 찾을 수 없습니다"
    if "folder_paths" not in getattr(generate, "__globals__", {}):
        return "generate 의 정의 모듈에 folder_paths 가 없습니다"

    try:
        signature = inspect.signature(post_image)
        expected = ("access_token", "prompt", "model", "action", "parameters")
        actual = tuple(signature.parameters)[: len(expected)]
        if actual != expected:
            return f"_post_image 인자 순서가 예상과 다릅니다: {actual}"
    except (TypeError, ValueError):
        pass  # 시그니처를 못 읽어도 호출 자체는 성립할 수 있다

    try:
        source = inspect.getsource(generate)
    except (OSError, TypeError):
        source = ""
    if source:
        if f'"{_AUTOSAVE_PREFIX}"' not in source:
            return f'generate 안에서 "{_AUTOSAVE_PREFIX}" 리터럴을 찾을 수 없습니다'
        if "_{counter:05}_" not in source:
            logger.warning(
                "%s 저장 파일명 포맷이 예상과 다릅니다. "
                "카운터 제거가 동작하지 않을 수 있습니다.",
                _TAG,
            )
    return None


def _install_patch() -> bool:
    """GenerateNAID 에 패치를 설치한다. 멱등이며 실패해도 예외를 내지 않는다."""
    global _installed, _install_refused

    if _installed:
        return True
    if _install_refused:
        return False

    try:
        import nodes as comfy_nodes  # ComfyUI 코어 레지스트리
    except Exception as exc:
        logger.warning("%s ComfyUI 코어 nodes 모듈을 열 수 없습니다: %s", _TAG, exc)
        return False

    cls = getattr(comfy_nodes, "NODE_CLASS_MAPPINGS", {}).get("GenerateNAID")
    if cls is None:
        # 아직 로드 전일 수 있으므로 영구 실패로 못박지 않는다.
        logger.debug("%s GenerateNAID 가 아직 등록되지 않았습니다.", _TAG)
        return False

    if getattr(getattr(cls, "generate", None), "_bmk_patched", False):
        _installed = True
        return True

    reason = _validate(cls)
    if reason:
        _install_refused = True
        logger.warning(
            "%s 패치를 설치하지 않습니다 — %s. "
            "NAIDGenerator 업데이트로 구조가 바뀌었을 수 있습니다. "
            "자동저장은 원본 동작(NAI_autosave_#####_.png)으로 남습니다.",
            _TAG,
            reason,
        )
        return False

    original_generate = cls.generate
    original_post_image = cls._post_image
    module_globals = original_generate.__globals__

    def patched_post_image(access_token, prompt, model, action, parameters, *args, **kwargs):
        # NAI 로 실제 전송되는 확정값. 자동저장 블록 직전에 호출되므로
        # 여기서 담아 두면 파일명이 PNG 메타데이터와 정의상 일치한다.
        cfg = _slot.get()
        if cfg is not None:
            cfg["model"] = model
            cfg["action"] = action
            cfg["params"] = parameters
        return original_post_image(
            access_token, prompt, model, action, parameters, *args, **kwargs
        )

    @functools.wraps(original_generate)
    def patched_generate(self, *args, **kwargs):
        option = kwargs.get("option")
        source_cfg = option.get(_OPTION_KEY) if isinstance(option, dict) else None

        if not isinstance(source_cfg, dict) or not source_cfg.get("enabled", True):
            return original_generate(self, *args, **kwargs)

        cfg: Dict[str, Any] = dict(source_cfg)
        try:
            cfg["time"] = time.strftime(cfg.get("time_format") or _DEFAULT_TIME_FORMAT)
        except (ValueError, TypeError):
            cfg["time"] = time.strftime(_DEFAULT_TIME_FORMAT)
            logger.warning("%s time_format 이 올바르지 않아 기본값을 씁니다.", _TAG)
        cfg["saved"] = None

        token = _slot.set(cfg)
        try:
            result = original_generate(self, *args, **kwargs)
        finally:
            _slot.reset(token)

        _finalize(cfg)
        return result

    patched_generate._bmk_patched = True  # type: ignore[attr-defined]

    module_globals["folder_paths"] = _FolderPathsProxy(module_globals["folder_paths"])
    cls._post_image = staticmethod(patched_post_image)
    cls.generate = patched_generate

    _installed = True
    logger.info("%s GenerateNAID 자동저장 파일명 패치를 설치했습니다.", _TAG)
    return True


# ─── 노드 ────────────────────────────────────────────────────────

class BMKNaiAutosaveName:
    """NAID_OPTION 체인에 자동저장 파일명 설정을 실어 보낸다."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "filename_template": (
                    "STRING",
                    {
                        "default": _DEFAULT_TEMPLATE,
                        "multiline": True,
                        "dynamicPrompts": False,
                        "tooltip": (
                            "토큰: %time %model %action %sampler %scheduler "
                            "%steps %seed %width %height %cfg %cfg_rescale "
                            "%uncond_scale %smea %variety %decrisper. "
                            "슬래시(/)는 하위 폴더가 됩니다."
                        ),
                    },
                ),
                "path": (
                    "STRING",
                    {
                        "default": _AUTOSAVE_PREFIX,
                        "multiline": False,
                        "tooltip": (
                            "저장 폴더. 빈 값은 output 루트, 상대경로는 output "
                            "하위, 절대경로는 그대로 사용합니다."
                        ),
                    },
                ),
                "time_format": (
                    "STRING",
                    {
                        "default": _DEFAULT_TIME_FORMAT,
                        "multiline": False,
                        "tooltip": "%time 토큰에 쓰는 strftime 포맷.",
                    },
                ),
                "keep_counter": (
                    "BOOLEAN",
                    {
                        "default": False,
                        "tooltip": (
                            "원본이 붙이는 _00001_ 꼬리를 남깁니다. 끄면 저장 후 "
                            "이름을 바꿔 제거하고, 같은 이름이 있으면 _02 부터 "
                            "번호를 붙입니다."
                        ),
                    },
                ),
                "enabled": (
                    "BOOLEAN",
                    {
                        "default": True,
                        "tooltip": (
                            "끄면 이 노드를 연결한 채로도 원본 자동저장 동작"
                            "(NAI_autosave_#####_.png)을 그대로 씁니다."
                        ),
                    },
                ),
            },
            "optional": {
                "option": (
                    "NAID_OPTION",
                    {"tooltip": "상위 NAID 옵션 체인. 비워 두면 새로 시작합니다."},
                ),
            },
        }

    RETURN_TYPES = ("NAID_OPTION",)
    RETURN_NAMES = ("option",)
    OUTPUT_TOOLTIPS = (
        "GenerateNAID 의 option 입력으로 연결하세요. ModelOption 등 다른 "
        "옵션 노드를 거쳐도 값이 보존됩니다.",
    )
    FUNCTION = "set_option"
    CATEGORY = "BMK/NovelAI"
    DESCRIPTION = (
        "ComfyUI_NAIDGenerator 가 NovelAI 메타데이터 보존용으로 자동 저장하는 "
        "PNG 의 파일명을 Image Saver 방식 토큰 템플릿으로 제어합니다. "
        "생성 파라미터는 NAI 로 실제 전송된 확정값을 쓰므로 파일명과 PNG "
        "메타데이터가 항상 일치합니다. Img2ImgOption / InpaintingOption 은 "
        "option 입력이 없는 체인 시작점이므로 이 노드를 그 뒤에 두세요."
    )
    SEARCH_ALIASES = [
        "bmk",
        "nai",
        "novelai",
        "novel ai",
        "autosave",
        "filename",
        "file name",
        "image saver",
        "자동저장",
        "파일명",
        "파일 이름",
        "저장 이름",
    ]

    def set_option(
        self,
        filename_template: str,
        path: str,
        time_format: str,
        keep_counter: bool,
        enabled: bool,
        option: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Dict[str, Any]]:
        # 지연 설치: BMK 패키지가 NAIDGenerator 보다 먼저 로드되므로 import
        # 시점에는 대상이 없다. 이 노드의 출력이 generate 로 흘러가는 이상
        # 그래프 순서상 여기가 generate 보다 항상 먼저 실행된다.
        if enabled and not _install_patch():
            logger.warning(
                "%s 패치가 설치되지 않아 파일명 설정이 적용되지 않습니다. "
                "ComfyUI_NAIDGenerator 가 설치되어 있는지 확인하세요.",
                _TAG,
            )

        option = copy.deepcopy(option) if option else {}
        option[_OPTION_KEY] = {
            "template": filename_template,
            "path": path,
            "time_format": time_format,
            "keep_counter": bool(keep_counter),
            "enabled": bool(enabled),
        }
        return (option,)


# ─── 등록 ────────────────────────────────────────────────────────

NODE_CLASS_MAPPINGS = {
    "BMKNaiAutosaveName": BMKNaiAutosaveName,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "BMKNaiAutosaveName": "BMK NAI Autosave Name",
}
