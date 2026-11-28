# -*- coding: utf-8 -*-
"""Prompt Converter 골든·회귀 테스트 (F-006 가중치 상한, F-007 백슬래시/센티넬/이스케이프 괄호)."""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import prompt_converter as pc


def _conv(text: str, mode: str, mult: float = 1.5, artist_mult: bool = False) -> str:
    return pc.BMKPromptSyntaxConverter().convert(text, mode, mult, artist_mult)[0]


def test_basic_roundtrip():
    assert _conv("(1girl:1.2), (solo)", "ComfyUI → NovelAI") == "1.2::1girl::, 1.1::solo::"
    assert _conv("1.2::1girl::, 1.1::solo::", "NovelAI → ComfyUI") == "(1girl:1.2), (solo:1.1)"
    assert _conv("artist:foo, (artist:bar:0.8)", "ComfyUI → NovelAI") == "artist:foo, 0.8::artist:bar::"
    assert _conv("artist:foo, 0.8::artist:bar::", "NovelAI → ComfyUI") == "artist:foo, (artist:bar:0.8)"
    assert _conv("artist:foo, (artist:bar:0.8), (ribbon:1.2)", "ComfyUI → Anima", 1.5, False) == (
        "@foo, (@bar:0.8), (ribbon:1.8)"
    )
    assert _conv("@foo, (@bar:0.8), (ribbon:1.8)", "Anima → ComfyUI", 1.5, False) == (
        "artist:foo, (artist:bar:0.8), (ribbon:1.2)"
    )
    assert _conv("  (1girl:1.2),\n\n(solo:0.9)", "ComfyUI → NovelAI") == "  1.2::1girl::,\n\n0.9::solo::"


def test_f006_no_arbitrary_weight_caps():
    # NAI 범위 음수·고배율 가중치가 1.1로 변질되거나 부호가 반전되지 않는다.
    assert _conv("(flat color:-6)", "ComfyUI → NovelAI") == "-6::flat color::"
    assert _conv("(ribbon:6)", "ComfyUI → NovelAI") == "6::ribbon::"
    assert _conv("(flat color:-6.5), (ribbon:12.25)", "ComfyUI → NovelAI") == (
        "-6.5::flat color::, 12.25::ribbon::"
    )
    assert _conv("(tag:-0)", "ComfyUI → NovelAI") == "0::tag::"

    # ComfyUI ↔ Anima 고배율 가중치 왕복(|w| > 10)
    anima = _conv("(ribbon:8)", "ComfyUI → Anima", 1.5, False)
    assert anima == "(ribbon:12.0)", anima
    back = _conv(anima, "Anima → ComfyUI", 1.5, False)
    assert back == "(ribbon:8.0)", back

    anima_neg = _conv("(flat color:-8)", "ComfyUI → Anima", 1.5, False)
    assert anima_neg == "(flat color:-12.0)", anima_neg
    back_neg = _conv(anima_neg, "Anima → ComfyUI", 1.5, False)
    assert back_neg == "(flat color:-8.0)", back_neg

    # 숫자처럼 보이지만 유효한 가중치가 아닌 콜론 접미사(1.2.3, --)는 예외 없이 태그 그대로 유지
    assert _conv("(tag:1.2.3)", "ComfyUI → NovelAI") == "1.1::tag:1.2.3::"
    assert _conv("(tag:--)", "ComfyUI → NovelAI") == "1.1::tag:--::"
    assert _conv("(tag:1.2.3)", "ComfyUI → Anima", 1.5, False) == "(tag:1.2.3)"


def test_f007_backslash_sentinel_and_escaped_parens():
    # 1) 일반 백슬래시 태그(\||/) 보존
    assert _conv(r"\||/, 1girl", "ComfyUI → NovelAI") == r"\||/, 1girl"
    assert _conv(r"(\||/:1.2)", "ComfyUI → NovelAI") == r"1.2::\||/::"
    assert _conv(r"1.2::\||/::", "NovelAI → ComfyUI") == r"(\||/:1.2)"
    assert _conv(r"\||/, (\||/:1.2)", "ComfyUI → Anima", 1.5, False) == r"\||/, (\||/:1.8)"

    # 2) __artist__ 와일드카드 및 내부 센티넬 문자열 충돌 방지
    src = "__artist__, artist:naga u, (__artist__:1.2), (artist:naga u:0.8)"
    assert _conv(src, "ComfyUI → NovelAI") == (
        "__artist__, artist:naga u, 1.2::__artist__::, 0.8::artist:naga u::"
    )
    assert _conv(
        "__artist__, artist:naga u, 1.2::__artist__::, 0.8::artist:naga u::",
        "NovelAI → ComfyUI",
    ) == src
    assert _conv(src, "ComfyUI → Anima", 1.5, False) == (
        "__artist__, @naga u, (__artist__:1.8), (@naga u:0.8)"
    )
    sentinel_clash = "\x00BMK_ARTIST\x00, (artist:foo:1.2)"
    assert _conv(sentinel_clash, "ComfyUI → NovelAI") == "\x00BMK_ARTIST\x00, 1.2::artist:foo::"

    # 3) 리터럴 이스케이프 괄호(\( \)) — 중첩·단독·짝 없음 모두 균형 파괴 없이 처리
    assert _conv(r"hatsune miku \(vocaloid\), 1girl", "ComfyUI → NovelAI") == (
        "hatsune miku (vocaloid), 1girl"
    )
    assert _conv(r"hatsune miku \(vocaloid\), 1girl", "ComfyUI → Anima", 1.5, False) == (
        r"hatsune miku \(vocaloid\), 1girl"
    )
    assert _conv(r"(foo \(bar\):1.2)", "ComfyUI → NovelAI") == "1.2::foo (bar)::"
    assert _conv(r"(foo \(bar\):1.2)", "ComfyUI → Anima", 1.5, False) == r"(foo \(bar\):1.8)"
    assert _conv(r"(foo \):1.2)", "ComfyUI → NovelAI") == "1.2::foo )::"
    assert _conv(r"(foo \):1.2)", "ComfyUI → Anima", 1.5, False) == r"(foo \):1.8)"
    assert _conv(r"(foo \(:1.2)", "ComfyUI → NovelAI") == "1.2::foo (::"
    assert _conv(r"(foo \(:1.2)", "ComfyUI → Anima", 1.5, False) == r"(foo \(:1.8)"
    assert _conv(r":\), :\(", "ComfyUI → NovelAI") == ":), :("
    assert _conv(r":\), :\(", "ComfyUI → Anima", 1.5, False) == r":\), :\("


def main():
    tests = [
        test_basic_roundtrip,
        test_f006_no_arbitrary_weight_caps,
        test_f007_backslash_sentinel_and_escaped_parens,
    ]
    for fn in tests:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"\nPASS {len(tests)} FAIL 0")


if __name__ == "__main__":
    main()
