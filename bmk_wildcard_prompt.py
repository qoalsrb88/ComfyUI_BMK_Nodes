# bmk_wildcard_prompt.py
# 백엔드(실행 시점)에서 와일드카드를 해석하는 노드.
#
# ImpactWildcardProcessor 는 브라우저(큐 시점)에서 populate 하므로
# BMKCyclicSeed 처럼 "실행 시점에 계산되는" 시드를 받을 수 없다.
# 이 노드는 Impact Pack 의 와일드카드 엔진(impact.wildcards.process)을
# 그대로 재사용하되, 실행 시점에 seed 입력값으로 해석한다.
#   - __wildcard__ / {a|b|c} / 가중치 / 파일 / 캐시 등 기존 문법 그대로
#   - seed 가 입력으로 들어오므로 BMKCyclicSeed → seed 연결이 정상 동작
#   - 같은 seed 를 샘플러에 연결하면 와일드카드 선택과 샘플링이 완전히 싱크됨
#
# 트레이드오프: 큐 시점 미리보기(populated_text)는 없음.
#              실행 전 결과를 보고 싶으면 출력 뒤에 Show Text 류를 붙인다.

import os
import sys
import traceback

MAX_SEED = 0xFFFFFFFFFFFFFFFF


def _load_impact_wildcards():
    """Impact Pack 의 백엔드 와일드카드 모듈을 lazy 로 가져온다."""
    # Impact Pack 이 로드되면 modules 경로가 sys.path 에 추가되어
    # 'impact.wildcards' 가 바로 import 된다.
    try:
        from impact import wildcards as wc
        return wc
    except Exception:
        pass

    # 폴백: custom_nodes 에서 Impact Pack 의 modules 경로를 직접 찾아 추가
    try:
        import folder_paths
        cn_root = os.path.join(os.path.dirname(folder_paths.__file__), "custom_nodes")
        for name in os.listdir(cn_root):
            if name.lower().replace("_", "-") == "comfyui-impact-pack":
                mod_dir = os.path.join(cn_root, name, "modules")
                if os.path.isdir(mod_dir) and mod_dir not in sys.path:
                    sys.path.append(mod_dir)
                from impact import wildcards as wc
                return wc
    except Exception:
        pass

    return None


class BMKWildcardPrompt:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "wildcard_text": ("STRING", {"multiline": True, "default": ""}),
                "seed": ("INT", {"default": 0, "min": 0, "max": MAX_SEED}),
            },
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("text",)
    FUNCTION = "resolve"
    CATEGORY = "BMK/utils"
    DESCRIPTION = (
        "Impact Pack의 와일드카드 엔진을 실행 시점에 해석합니다. seed를 입력으로 받으므로 "
        "BMK Cyclic Seed처럼 실행 시점에 계산되는 시드와 연결할 수 있습니다"
        "(ImpactWildcardProcessor는 큐 시점 populate라 불가). 같은 seed를 샘플러에도 "
        "연결하면 와일드카드 선택과 샘플링이 완전히 동기화됩니다. 큐 시점 미리보기는 없습니다."
    )
    SEARCH_ALIASES = [
        "wildcard", "wildcard prompt", "dynamic prompt", "impact wildcard",
        "와일드카드", "동적 프롬프트", "프롬프트",
    ]

    def resolve(self, wildcard_text, seed):
        wc = _load_impact_wildcards()
        if wc is None:
            raise RuntimeError(
                "[BMKWildcardPrompt] impact.wildcards 모듈을 찾지 못했습니다. "
                "ComfyUI-Impact-Pack 이 설치/로드되어 있는지 확인하세요."
            )

        # Impact 의 백엔드 와일드카드 치환 함수 호출
        # (Inspire Pack 도 동일하게 process(text=..., seed=...) 형태로 호출함)
        try:
            populated = wc.process(text=wildcard_text, seed=int(seed))
        except Exception as e:
            # Impact 엔진 내부에서 터지면(예: 빈 옵션의 {n$$...} 멀티셀렉트)
            # 어떤 seed / 어떤 원본 텍스트가 범인인지 명확히 찍어준다.
            # → 와일드카드 파일의 잘못된 {} 그룹을 바로 추적 가능.
            print("[BMKWildcardPrompt] 와일드카드 해석 실패")
            print(f"  - seed : {seed}")
            print(f"  - error: {type(e).__name__}: {e}")
            print(f"  - text :\n{wildcard_text}")
            traceback.print_exc()
            raise RuntimeError(
                f"[BMKWildcardPrompt] seed={seed} 에서 와일드카드 해석 실패: {e}\n"
                "와일드카드 내용에 빈 '{}' 또는 옵션이 비는 '{n$$...}' 멀티셀렉트, "
                "'$$' 본문에 끼어든 '::' 가중치, 떠다니는 '|' 가 있는지 확인하세요."
            ) from e

        print(f"[BMKWildcardPrompt] seed={seed} -> {populated}")
        return (populated,)


NODE_CLASS_MAPPINGS = {
    "BMKWildcardPrompt": BMKWildcardPrompt,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "BMKWildcardPrompt": "BMK Wildcard Prompt",
}
