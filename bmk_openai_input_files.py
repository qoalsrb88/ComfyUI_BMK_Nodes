"""BMK OpenAI Input Files
OpenAI ChatGPT 노드의 files 입력용 파일 로더 — 하위 폴더·대문자 확장자 지원판.

배경
----
내장 "OpenAI ChatGPT Input Files"(comfy_api_nodes/nodes_openai.py) 는 input
폴더 최상위만 훑고(os.scandir), 확장자를 대소문자 구분(endswith(".pdf"))으로
걸러서 하위 폴더의 파일이나 .PDF / .TXT 파일이 목록에 나오지 않는다.
이 노드는 같은 OPENAI_INPUT_FILES 타입을 내보내므로 내장 노드 자리에
그대로 연결하거나, 내장 노드와 섞어서 체인할 수 있다.

옵션
----
- file: input 폴더 기준 상대 경로(예: "Doc_SPRT/spec.PDF"). 하위 폴더 전체를
  재귀 탐색하며, 확장자는 대소문자 무시. 크기 제한(32MB 미만)은 내장 노드와 동일.
  "."으로 시작하는 폴더는 건너뛴다.
- OPENAI_INPUT_FILES: 체인 입력(선택). 앞 노드의 파일 목록 뒤에 이어 붙인다.

전송 형식(base64 data URI, filename=파일명만)은 내장 노드와 동일하다.

v1 (2026-09)
------------
- 최초 구현.
"""

from __future__ import annotations

import hashlib
import logging
import os

import folder_paths

try:
    from comfy_api_nodes.apis.openai import InputFileContent
    from comfy_api_nodes.util import text_filepath_to_data_uri
except ImportError as e:
    raise ImportError(
        "[ComfyUI_BMK_Nodes::OpenAIInputFiles] comfy_api_nodes 의 OpenAI 모듈을 "
        f"불러올 수 없습니다(ComfyUI 버전 확인 필요): {e}"
    ) from e

logger = logging.getLogger(__name__)

_TAG = "[ComfyUI_BMK_Nodes::OpenAIInputFiles]"

_EXTENSIONS = (".txt", ".pdf")
_MAX_BYTES = 32 * 1024 * 1024


def _is_supported(name: str) -> bool:
    return name.lower().endswith(_EXTENSIONS)


def _list_input_files() -> list[str]:
    input_dir = folder_paths.get_input_directory()
    found = []
    for root, dirs, files in os.walk(input_dir):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in files:
            if not _is_supported(name):
                continue
            path = os.path.join(root, name)
            try:
                if os.path.getsize(path) >= _MAX_BYTES:
                    continue
            except OSError:
                continue
            found.append(os.path.relpath(path, input_dir).replace(os.sep, "/"))
    return sorted(found, key=str.lower)


def _resolve(file: str) -> str | None:
    """combo 값을 input 폴더 안의 실제 경로로 변환. 폴더 밖이거나 미지원 확장자면 None."""
    input_dir = os.path.abspath(folder_paths.get_input_directory())
    path = os.path.abspath(os.path.join(input_dir, file))
    if os.path.commonpath((input_dir, path)) != input_dir:
        return None
    if not _is_supported(path) or not os.path.isfile(path):
        return None
    return path


class BMKOpenAIInputFiles:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "file": (
                    _list_input_files(),
                    {
                        "tooltip": "input 폴더(하위 폴더 포함)의 .txt / .pdf 파일. "
                        "확장자 대소문자 무시, 32MB 미만.",
                    },
                ),
            },
            "optional": {
                "OPENAI_INPUT_FILES": (
                    "OPENAI_INPUT_FILES",
                    {"tooltip": "앞 노드의 파일 목록(체인). 이 노드의 파일이 앞에 붙습니다."},
                ),
            },
        }

    RETURN_TYPES = ("OPENAI_INPUT_FILES",)
    RETURN_NAMES = ("OPENAI_INPUT_FILES",)
    FUNCTION = "load"
    CATEGORY = "BMK/Text"
    DESCRIPTION = (
        "OpenAI ChatGPT 노드의 files 입력용 파일 로더. 내장 Input Files 노드와 달리 "
        "input 폴더의 하위 폴더까지 탐색하고 .PDF / .TXT 같은 대문자 확장자도 인식합니다. "
        "여러 개를 체인으로 이어 한 번에 보낼 수 있습니다."
    )
    SEARCH_ALIASES = [
        "openai input files", "chatgpt files", "gpt file", "pdf", "txt",
        "파일 첨부", "문서 첨부", "챗지피티 파일",
    ]

    def load(self, file, OPENAI_INPUT_FILES=None):
        path = _resolve(file)
        if path is None:
            raise ValueError(f"{_TAG} 사용할 수 없는 파일: {file}")
        content = InputFileContent(
            file_data=text_filepath_to_data_uri(path),
            filename=os.path.basename(path),
            type="input_file",
        )
        return ([content] + list(OPENAI_INPUT_FILES or []),)

    @classmethod
    def IS_CHANGED(cls, file, **kwargs):
        path = _resolve(file)
        if path is None:
            return ""
        m = hashlib.sha256()
        with open(path, "rb") as f:
            m.update(f.read())
        return m.digest().hex()

    @classmethod
    def VALIDATE_INPUTS(cls, file, **kwargs):
        if _resolve(file) is None:
            return f"Invalid input file: {file}"
        return True


NODE_CLASS_MAPPINGS = {
    "BMKOpenAIInputFiles": BMKOpenAIInputFiles,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "BMKOpenAIInputFiles": "BMK OpenAI Input Files",
}
