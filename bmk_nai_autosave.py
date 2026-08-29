"""BMK NAI Autosave Name — NAIDGenerator 자동저장 PNG 의 파일명과 메타데이터를 제어한다.

배경:
ComfyUI_NAIDGenerator (bedovyy) 의 GenerateNAID 는 NAI 응답 zip 에서 꺼낸
원본 PNG 바이트를 그대로 output/NAI_autosave/NAI_autosave_#####_.png 에
기록한다. 재인코딩을 하지 않으므로 NovelAI 메타데이터가 온전히 보존되지만,
두 가지가 아쉽다.

  (a) 파일명 프리픽스가 소스에 문자열 리터럴로 박혀 있어 제어할 수 없다.
      여러 장을 뽑아 놓으면 탐색기에서 어떤 게 어떤 설정인지 구분이 안 된다.
  (b) ComfyUI 워크플로 메타데이터가 없다. 그래서 SaveImage 브랜치를 따로
      두게 되는데, 그쪽은 반대로 NAI 메타데이터가 없다. 같은 그림이 서로
      다른 정보를 가진 두 파일로 갈라진다.

둘 다 하류 노드로는 해결할 수 없다. 원본 바이트는 generate() 지역변수로만
존재하고, bytes_to_image() 로 IMAGE 텐서가 되는 순간 PNG 텍스트 청크가
사라진다. 알파 채널에 심긴 NAI stealth pnginfo 도 마찬가지로 소실된다 —
텐서 왕복이 uint8 → /255.0 → float32 → *255.0 → astype(uint8) 인데 마지막이
반올림이 아니라 절삭이라, 부동소수점 오차가 아래로 떨어지는 순간 LSB 가
뒤집히기 때문이다. 따라서 개입 지점은 generate() 실행 시점뿐이며, 이 파일은
런타임 몽키패치로 그 지점에 끼어든다.

(b) 가 가능한 이유는 두 메타데이터가 애초에 충돌하지 않기 때문이다. NAI 가
쓰는 tEXt 키는 Software / Source / Comment / Title / Description 이고 ComfyUI
는 prompt / workflow 다. 겹치는 키가 없고 PNG 스펙상 tEXt 청크 개수 제한도
없다. 한 파일에 둘 다 넣는 것은 규격 위반이 아니라 정상이다.

설계:
1) 파라미터 전달은 위젯이 아니라 option 체인으로 한다.
   generate() 는 option dict 에서 자기가 아는 키만 읽고 나머지는 무시하므로,
   ModelOption 과 같은 형태의 노드가 option["bmk_autosave"] 를 실어 보내면
   된다. GenerateNAID 의 위젯 목록을 건드리지 않으니 widgets_values 위치
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

4) 워크플로 메타데이터는 PIL 재저장이 아니라 바이트 레벨 청크 삽입으로 넣는다.
   PIL 로 열어 pnginfo 를 붙여 다시 쓰면 IDAT 을 재인코딩하고, NAI 의 원본
   청크를 손으로 복사해 주지 않으면 날아가며, PIL 이 노출하지 않는 부가 청크도
   잃는다. 무엇보다 알파 채널 stealth 데이터가 인코딩 경로를 한 번 더 타게 된다.
   첫 IDAT 앞에 tEXt 청크를 끼워 넣기만 하면 픽셀 데이터는 1바이트도 건드리지
   않는다. 이미 rename 을 하고 있던 _finalize() 에 합쳤으므로 파일 I/O 도
   늘지 않는다.

5) PROMPT / EXTRA_PNGINFO 는 이 노드가 아니라 GenerateNAID 에 hidden 으로 붙인다.
   이 노드에 붙이면 캐시 때문에 어긋난다. 위젯 값이 그대로면 ComfyUI 가 결과를
   캐시해 set_option 을 재실행하지 않으므로, seed 만 바꿔 재생성했을 때 직전
   실행의 워크플로 JSON 이 박힌다. IS_CHANGED 로 강제 재실행하면 하류
   GenerateNAID 까지 매번 더러워져 Anlas 가 나간다. GenerateNAID 에 붙이면
   자기 실행 시점에 주입되고, 캐시되면 애초에 파일도 안 만들어지므로 불일치가
   성립하지 않는다.

   hidden 입력은 위젯이 아니라서 widgets_values 배열에 자리를 차지하지 않고,
   노드에 렌더링되지 않으며, 캐시 시그니처에도 들어가지 않는다. 즉 1) 에서
   위젯 추가를 피한 이유가 여기엔 해당하지 않는다. 프론트엔드가 이미 받아 둔
   /object_info 에 없어도 무방하다 — hidden 은 실행 시점에 서버가
   INPUT_TYPES() 를 다시 읽어 해석하기 때문이다.

6) 설치는 지연 실행한다.
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
  (rename / 청크 삽입 대상 경로를 이 포맷으로 재구성한다. 재구성한 경로가
   없으면 후처리를 건너뛰고 경고만 남긴다.)
- GenerateNAID.INPUT_TYPES 가 hidden 에 prompt / extra_pnginfo 를 선언하지
  않는다. 선언한다면 우리가 주입하지 않고, generate 호출 시에도 값을 읽기만
  하고 인자에서 빼지 않는다 — upstream 이 직접 쓰려는 것이므로.
설치 시 위 항목을 검사하고, 구조가 다르면 패치하지 않고 경고 로그만 남긴다.
조용히 어긋나는 것보다 명시적으로 비활성화되는 편이 낫다.

사용법:
  [BMK NAI Autosave Name] --option--> [ModelOption] --option--> [GenerateNAID]

  option 입력이 있는 NAID 노드(ModelOption / NetworkOption /
  VibeTransferOption / CharacterReferenceOption)는 deepcopy 후 update 하므로
  체인 어디에 끼워도 값이 보존된다. 다만 Img2ImgOption 과 InpaintingOption 은
  option 입력이 없어 매번 새 dict 를 만드는 체인의 시작점이다. 이 노드는
  반드시 그 뒤에 두어야 한다.

  embed_workflow 를 켜면 저장 파일 하나에 워크플로 + NAI tEXt 메타데이터 +
  알파 stealth 데이터가 모두 들어간다. 지금의 SaveImage 출력보다 정보량이
  많으므로 SaveImage 브랜치를 PreviewImage 로 바꿔도 된다.

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
- A1111 형식 parameters 청크는 일부러 넣지 않는다. 한 파일에 NAI Comment 와
  A1111 parameters 가 동시에 있으면 bmk_prompt_from_image 의 포맷 인식 계층에서
  감지 우선순위가 흔들린다. 필요해지면 별도 토글로 빼고, 감지기 쪽에서 NAI 가
  이기도록 순서를 못박은 뒤에 추가할 것.
- tEXt 청크는 latin-1 만 담을 수 있다. json.dumps 의 기본값(ensure_ascii=True)
  을 유지해야 한글이 이스케이프되어 안전하게 들어간다. iTXt 로 넘어가면
  프론트엔드가 읽어 줄지 보장할 수 없다.

버전 이력:
- v1 (2026-08): 최초. option 체인 기반 파라미터 전달, _post_image 후킹으로
      확정 파라미터 수집, 정의 모듈 한정 folder_paths 프록시, 지연 설치,
      구조 검증 후 실패 시 무패치 폴백, keep_counter=False 시 카운터 제거
      rename.
- v2 (2026-08): ComfyUI 워크플로 메타데이터 동시 임베드. GenerateNAID 에
      hidden PROMPT / EXTRA_PNGINFO 주입(위젯이 아니므로 워크플로 무해),
      첫 IDAT 앞 tEXt 청크 바이트 삽입, 기존 키 중복 검사, _finalize 에
      rename 과 통합(임시 파일 + os.replace). embed_workflow 토글 추가 —
      기존 위젯 순서를 흔들지 않도록 required 끝에 append 했다.
"""

from __future__ import annotations

import contextvars
import copy
import functools
import inspect
import json
import logging
import os
import struct
import time
import zlib
from pathlib import Path
from typing import Any, Dict, Iterator, Optional, Set, Tuple

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

# GenerateNAID 에 주입할 hidden 입력.
_HIDDEN_INPUTS = {"prompt": "PROMPT", "extra_pnginfo": "EXTRA_PNGINFO"}

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_TEXT_CHUNK_TYPES = (b"tEXt", b"zTXt", b"iTXt")

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


# ─── PNG tEXt 청크 삽입 ───────────────────────────────────────────

def _iter_chunks(png: bytes) -> Iterator[Tuple[int, int, bytes]]:
    """(offset, length, chunk_type) 을 순회한다. 손상된 지점에서 멈춘다."""
    offset = len(_PNG_SIGNATURE)
    total = len(png)
    while offset + 8 <= total:
        length = struct.unpack(">I", png[offset:offset + 4])[0]
        chunk_type = png[offset + 4:offset + 8]
        end = offset + 12 + length  # length(4) + type(4) + data + crc(4)
        if end > total:
            return
        yield offset, length, chunk_type
        if chunk_type == b"IEND":
            return
        offset = end


def _make_text_chunk(keyword: str, text: str) -> bytes:
    """tEXt 청크 하나를 바이트로 만든다.

    PNG 스펙상 keyword 는 1~79자 latin-1 이고, 본문과는 널 바이트로 구분한다.
    """
    payload = keyword.encode("latin-1") + b"\x00" + text.encode("latin-1")
    crc = zlib.crc32(b"tEXt" + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + b"tEXt" + payload + struct.pack(">I", crc)


def _insert_text_chunks(png: bytes, entries: Dict[str, str]) -> bytes:
    """첫 IDAT 앞에 tEXt 청크들을 끼워 넣는다. 픽셀 데이터는 건드리지 않는다.

    이미 같은 키워드가 있으면 건너뛴다 — NAI 원본 청크를 덮어쓰지 않기 위해서,
    그리고 재실행 시 중복 삽입을 막기 위해서.
    """
    if not png.startswith(_PNG_SIGNATURE):
        raise ValueError("PNG 시그니처가 아닙니다")

    existing: Set[str] = set()
    idat_offset: Optional[int] = None

    for offset, length, chunk_type in _iter_chunks(png):
        if chunk_type in _TEXT_CHUNK_TYPES:
            keyword, _, _ = png[offset + 8:offset + 8 + length].partition(b"\x00")
            existing.add(keyword.decode("latin-1", "ignore"))
        elif chunk_type == b"IDAT" and idat_offset is None:
            idat_offset = offset

    if idat_offset is None:
        raise ValueError("IDAT 청크를 찾지 못했습니다")

    blocks = []
    for keyword, text in entries.items():
        if keyword in existing:
            logger.debug("%s '%s' 청크가 이미 있어 건너뜁니다.", _TAG, keyword)
            continue
        if not 1 <= len(keyword) <= 79:
            logger.warning("%s 키워드 길이가 규격을 벗어납니다: %r", _TAG, keyword)
            continue
        try:
            blocks.append(_make_text_chunk(keyword, text))
        except UnicodeEncodeError:
            # json.dumps 의 ensure_ascii=True 를 지켰다면 도달하지 않는다.
            logger.warning(
                "%s '%s' 를 latin-1 로 인코딩할 수 없어 건너뜁니다.", _TAG, keyword
            )

    if not blocks:
        return png
    return png[:idat_offset] + b"".join(blocks) + png[idat_offset:]


def _collect_embed_entries(prompt: Any, extra_pnginfo: Any) -> Dict[str, str]:
    """코어 SaveImage 와 같은 형태로 prompt / workflow 항목을 만든다."""
    entries: Dict[str, str] = {}
    if prompt is not None:
        try:
            entries["prompt"] = json.dumps(prompt)
        except (TypeError, ValueError) as exc:
            logger.warning("%s prompt 직렬화 실패: %s", _TAG, exc)
    if isinstance(extra_pnginfo, dict):
        for key, value in extra_pnginfo.items():
            try:
                entries[key] = json.dumps(value)
            except (TypeError, ValueError) as exc:
                logger.warning("%s '%s' 직렬화 실패: %s", _TAG, key, exc)
    return entries


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


# ─── 후처리 (카운터 제거 + 워크플로 임베드) ───────────────────────

def _unique_target(folder: Path, filename: str) -> Path:
    """카운터를 뗀 이름을 고른다. 이미 있으면 _02 부터 번호를 붙인다."""
    target = folder / f"{filename}.png"
    index = 2
    while target.exists() and index <= 9999:
        target = folder / f"{filename}_{index:02d}.png"
        index += 1
    return target


def _finalize(cfg: Dict[str, Any]) -> None:
    """저장된 파일에 워크플로 청크를 넣고 최종 이름으로 정리한다.

    원본의 f"{filename}_{counter:05}_.png" 는 리터럴이라 프리픽스만 바꿔서는
    카운터를 없앨 수 없다. 프록시가 반환한 값으로 경로를 재구성해 처리하고,
    재구성한 경로가 없으면 upstream 포맷이 바뀐 것이므로 건너뛴다.
    """
    saved = cfg.get("saved")
    if not saved:
        return

    folder_name, filename, counter = saved
    folder = Path(folder_name)
    source = folder / f"{filename}_{counter:05}_.png"
    if not source.exists():
        logger.warning(
            "%s 저장 파일을 찾지 못해 후처리를 건너뜁니다 (%s). "
            "upstream 파일명 포맷이 바뀌었을 수 있습니다.",
            _TAG,
            source.name,
        )
        return

    if cfg.get("keep_counter", False):
        target = source
    else:
        target = _unique_target(folder, filename)

    entries: Dict[str, str] = cfg.get("embed") or {}

    if entries:
        temp = folder / (target.name + ".bmk-tmp")
        try:
            patched = _insert_text_chunks(source.read_bytes(), entries)
            temp.write_bytes(patched)
            os.replace(temp, target)
            if source != target and source.exists():
                source.unlink()
            return
        except Exception as exc:
            logger.warning(
                "%s 워크플로 메타데이터 삽입에 실패했습니다 — "
                "NAI 메타데이터만 담긴 원본을 유지합니다: %s",
                _TAG,
                exc,
            )
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass

    # 청크 삽입을 안 했거나 실패한 경우 — 이름만 정리한다.
    if target != source:
        try:
            source.rename(target)
        except OSError as exc:
            logger.warning("%s 이름 변경 실패 — 원본 파일명을 유지합니다: %s", _TAG, exc)


# ─── 패치 설치 ────────────────────────────────────────────────────

def _validate(cls: Any) -> Optional[str]:
    """구조 가정을 검사한다. 문제가 있으면 사유 문자열을 반환."""
    generate = getattr(cls, "generate", None)
    post_image = getattr(cls, "_post_image", None)
    input_types = getattr(cls, "INPUT_TYPES", None)

    if not callable(generate):
        return "GenerateNAID.generate 를 찾을 수 없습니다"
    if not callable(post_image):
        return "GenerateNAID._post_image 를 찾을 수 없습니다"
    if not callable(input_types):
        return "GenerateNAID.INPUT_TYPES 를 찾을 수 없습니다"
    if "folder_paths" not in getattr(generate, "__globals__", {}):
        return "generate 의 정의 모듈에 folder_paths 가 없습니다"

    try:
        signature = inspect.signature(post_image)
        expected = ("access_token", "prompt", "model", "action", "parameters")
        actual = tuple(signature.parameters)[:len(expected)]
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
                "카운터 제거와 워크플로 임베드가 동작하지 않을 수 있습니다.",
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
    original_input_types = cls.INPUT_TYPES
    module_globals = original_generate.__globals__

    # upstream 이 이미 선언한 hidden 은 건드리지 않는다. 우리가 주입한 것만
    # generate 호출 전에 제거해야 원본 시그니처가 깨지지 않는다.
    try:
        base_hidden = set((original_input_types().get("hidden") or {}).keys())
    except Exception:
        base_hidden = set()
    injected_keys = frozenset(k for k in _HIDDEN_INPUTS if k not in base_hidden)

    def build_input_types() -> Dict[str, Any]:
        spec = dict(original_input_types())
        hidden = dict(spec.get("hidden") or {})
        for key, kind in _HIDDEN_INPUTS.items():
            hidden.setdefault(key, kind)
        spec["hidden"] = hidden
        return spec

    def patched_post_image(
        access_token, prompt, model, action, parameters, *args, **kwargs
    ):
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
        # 우리가 주입한 hidden 은 원본이 받지 못하므로 반드시 걷어낸다.
        hidden_values: Dict[str, Any] = {}
        for key in _HIDDEN_INPUTS:
            if key in injected_keys:
                hidden_values[key] = kwargs.pop(key, None)
            else:
                hidden_values[key] = kwargs.get(key)

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

        if cfg.get("embed_workflow", True):
            cfg["embed"] = _collect_embed_entries(
                hidden_values.get("prompt"), hidden_values.get("extra_pnginfo")
            )
            if not cfg["embed"]:
                logger.warning(
                    "%s 워크플로 정보를 받지 못해 임베드를 건너뜁니다. "
                    "NAI 메타데이터는 정상 저장됩니다.",
                    _TAG,
                )
        else:
            cfg["embed"] = {}

        token = _slot.set(cfg)
        try:
            result = original_generate(self, *args, **kwargs)
        finally:
            _slot.reset(token)

        _finalize(cfg)
        return result

    patched_generate._bmk_patched = True  # type: ignore[attr-defined]

    module_globals["folder_paths"] = _FolderPathsProxy(module_globals["folder_paths"])
    cls.INPUT_TYPES = classmethod(lambda _cls: build_input_types())
    cls._post_image = staticmethod(patched_post_image)
    cls.generate = patched_generate

    _installed = True
    logger.info(
        "%s GenerateNAID 패치를 설치했습니다 (파일명 제어 + 워크플로 임베드).",
        _TAG,
    )
    return True


# ─── 노드 ────────────────────────────────────────────────────────

class BMKNaiAutosaveName:
    """NAID_OPTION 체인에 자동저장 파일명 / 메타데이터 설정을 실어 보낸다."""

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
                # ※ 위젯은 append-only — 중간에 끼우면 저장된 워크플로의
                #   widgets_values 위치 배열이 어긋난다.
                "embed_workflow": (
                    "BOOLEAN",
                    {
                        "default": True,
                        "tooltip": (
                            "ComfyUI 워크플로 메타데이터를 같은 파일에 추가로 "
                            "심습니다. NAI 메타데이터와 알파 채널 데이터는 그대로 "
                            "보존되므로 한 파일이 양쪽 모두와 호환됩니다."
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
        "PNG 의 파일명을 Image Saver 방식 토큰 템플릿으로 제어하고, 같은 파일에 "
        "ComfyUI 워크플로 메타데이터를 함께 심습니다. 생성 파라미터는 NAI 로 "
        "실제 전송된 확정값을 쓰므로 파일명과 PNG 메타데이터가 항상 일치합니다. "
        "Img2ImgOption / InpaintingOption 은 option 입력이 없는 체인 시작점이므로 "
        "이 노드를 그 뒤에 두세요."
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
        "workflow",
        "metadata",
        "pnginfo",
        "자동저장",
        "파일명",
        "파일 이름",
        "저장 이름",
        "워크플로우",
        "메타데이터",
    ]

    def set_option(
        self,
        filename_template: str,
        path: str,
        time_format: str,
        keep_counter: bool,
        enabled: bool,
        embed_workflow: bool = True,
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
            "embed_workflow": bool(embed_workflow),
        }
        return (option,)


# ─── 등록 ────────────────────────────────────────────────────────

NODE_CLASS_MAPPINGS = {
    "BMKNaiAutosaveName": BMKNaiAutosaveName,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "BMKNaiAutosaveName": "BMK NAI Autosave Name",
}
