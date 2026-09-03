"""BMK Text Viewer (Tabs) — 여러 STRING 입력을 높이가 고정된 뷰어 하나에서 탭으로 돌려 본다.

배경
  pysssss Show Text 는 입력마다 읽기 전용 위젯을 세로로 쌓기 때문에 볼 텍스트가
  늘어날수록 노드가 길어진다. 이 노드는 받은 텍스트를 전부 프론트로 보내고,
  프론트엔드 짝 JS(js/bmk_text_viewer_tabs.js)가 탭 바 + 본문 + 푸터로 된
  DOM 위젯 하나에 담아 보여 준다. 노드 높이는 입력 개수와 무관하게 고정된다.

구성
  - 입력: io.Autogrow(text_1 ~ text_10). 하나를 연결하면 다음 포트가 자동으로 생긴다.
    (템플릿이 String 이므로 위젯 없이 소켓 전용으로 강제된다.)
  - 출력: 없음. OUTPUT_NODE 이므로 상류 노드 실행을 유발한다.
  - 프론트 전달: ui.text(문자열 리스트) + ui.names(포트 이름 리스트, 같은 순서).
    ComfyUI 는 ui 값을 실행 단위로 한 단계 평탄화하므로 반드시 리스트여야 한다.
  - 표시 / 탭 전환 / 복사 / 줄바꿈 / 탭 배치 / 마지막 결과 기억은 전부 JS 가 담당.

주의
  - V3 스키마(comfy_api.latest) 노드지만 패키지 로더 규약(NODE_CLASS_MAPPINGS)으로
    등록한다. nodes.py 는 V1/V3 경로 모두 같은 전역 매핑에 넣으므로 동작은 같다.
    CATEGORY / DESCRIPTION 은 V3 classproperty 가 스키마에서 읽어 주고,
    SEARCH_ALIASES 만 규약 점검·server.node_info 용으로 클래스 속성으로도 둔다.
  - 프론트엔드(1.49.6)의 슬롯 이름은 Autogrow id 가 접두어로 붙은 "texts.text_1" 이고,
    execute 가 받는 dict 키는 접두어 없는 "text_1" 이다. JS 는 두 형태를 모두 매칭한다.
  - 마지막 결과는 위젯 값이 아니라 node.properties 에 저장된다(widgets_values 는 항상 []).

버전 이력
  - v1 (2026-09-03): 최초. ComfyUI-TextViewerTabs 단독 패키지에서 BMK 규약으로 이관.
    입력 슬롯을 뷰어 왼쪽 열에 두는 배치, 코어 textarea 와 같은 글꼴/색, 탭 배치 모드
    (fit / wrap / scroll) 추가.
"""

from __future__ import annotations

import logging

from comfy_api.latest import io

logger = logging.getLogger(__name__)
_TAG = "[ComfyUI_BMK_Nodes::TextViewerTabs]"

MAX_TABS = 10
INPUT_NAMES = [f"text_{i}" for i in range(1, MAX_TABS + 1)]

_DESCRIPTION = (
    "여러 STRING 입력을 높이가 고정된 뷰어 하나에서 탭으로 돌려 봅니다. "
    "텍스트를 연결하면 다음 입력 포트가 자동으로 생기고, 탭 이름은 연결된 노드의 제목을 따릅니다. "
    "복사 버튼은 클릭 = 현재 탭, Shift+클릭 또는 우클릭 = 전체 탭입니다."
)
_SEARCH_ALIASES = [
    "show text",
    "text viewer",
    "preview text",
    "tabs",
    "텍스트 뷰어",
    "텍스트 보기",
    "탭 뷰어",
    "프롬프트 보기",
]


def _port_index(name: str) -> int:
    try:
        return int(name.rsplit("_", 1)[-1])
    except ValueError:
        return 0


class BMKTextViewerTabs(io.ComfyNode):
    # V3 노드에는 SEARCH_ALIASES classproperty 가 없어 규약 점검과 server.node_info 가
    # 읽을 수 있도록 클래스 속성으로도 노출한다(스키마의 search_aliases 와 같은 리스트).
    SEARCH_ALIASES = _SEARCH_ALIASES

    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="BMKTextViewerTabs",
            display_name="BMK Text Viewer (Tabs)",
            category="BMK/Text",
            description=_DESCRIPTION,
            search_aliases=_SEARCH_ALIASES,
            inputs=[
                io.Autogrow.Input(
                    "texts",
                    template=io.Autogrow.TemplateNames(
                        io.String.Input("text"),
                        names=INPUT_NAMES,
                        min=0,
                    ),
                    tooltip=f"텍스트 입력(최대 {MAX_TABS}개). 연결된 입력마다 탭 하나가 됩니다.",
                ),
            ],
            outputs=[],
            is_output_node=True,
        )

    @classmethod
    def execute(cls, texts: io.Autogrow.Type = None) -> io.NodeOutput:
        texts = texts or {}
        names = sorted(texts, key=_port_index)
        values: list[str] = []
        for name in names:
            value = texts[name]
            if value is None:
                value = ""
            elif not isinstance(value, str):
                value = str(value)
            values.append(value)
        logger.debug("%s %d text(s): %s", _TAG, len(values), ", ".join(names))
        # ui 값은 리스트여야 한다 — ComfyUI 가 실행 단위로 한 단계 평탄화한다.
        return io.NodeOutput(ui={"text": values, "names": names})


NODE_CLASS_MAPPINGS = {
    "BMKTextViewerTabs": BMKTextViewerTabs,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "BMKTextViewerTabs": "BMK Text Viewer (Tabs)",
}
