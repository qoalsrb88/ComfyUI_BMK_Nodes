#!/usr/bin/env python
"""워크플로우 JSON에서 BMK Tabbed Notes의 개인 상태를 제거한다.

왜 필요한가
───────────
BMKTabbedNotes 노드의 `notes_data` 위젯에는 노트 원문이 아니라 "선택 상태"만
직렬화된다. 그런데 그 안에 경로 문자열이 들어 있다:

    {"mode": 2, "activeCategory": "회사/프로젝트A", "activeTab": "메인 프롬프트",
     "sidebarWidth": 110, "collapsed": {"회사": true, "회사/프로젝트A": true, ...},
     "targets": {...}, "sectionHeights": {...}}

- activeCategory / activeTab — 마지막으로 보던 카테고리·탭 이름
- collapsed — 키가 **카테고리 전체 경로**다. 접어둔 폴더가 여럿이면
  카테고리 트리 상당 부분이 워크플로우 파일에 통째로 박힌다. 이쪽이 더 크다.

즉 노트 폴더를 저장소 밖으로 뺐어도, examples/ 에 워크플로우를 넣는 순간
카테고리 구조가 공개된다. 이 스크립트는 `notes_data`를 "{}"로 통째 치환한다.
부분 삭제(activeCategory만 지우기 등)는 collapsed가 남아 의미가 없다.

정보 손실은 없다 — 남의 머신엔 그 카테고리가 없어서 프론트의
ensureActiveValid()가 어차피 첫 탭으로 폴백한다.

지원 포맷
─────────
- 그래프 포맷:  {"nodes": [...], "definitions": {"subgraphs": [{"nodes": [...]}]}}
                (중첩 subgraph도 재귀 탐색)
- API 포맷:     {"7": {"class_type": "BMKTabbedNotes", "inputs": {...}}}

사용법
──────
    python tools/scrub_workflow.py --check examples/*.json     # 검사만 (쓰기 없음)
    python tools/scrub_workflow.py examples/foo.json           # foo.scrubbed.json 생성
    python tools/scrub_workflow.py --in-place examples/*.json  # 덮어쓰기

--check는 스크럽할 게 남아 있으면 종료 코드 1을 반환하므로 pre-commit 훅으로
쓸 수 있다.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys

NODE_TYPE = "BMKTabbedNotes"
WIDGET_NAME = "notes_data"
CLEAN_VALUE = "{}"

# notes_data 안에서 개인 정보가 실리는 키 (검사 리포트용)
_LEAKY_KEYS = ("activeCategory", "activeTab", "collapsed")


def _describe(raw):
    """notes_data 값에서 무엇이 새는지 사람이 읽을 요약을 만든다."""
    if not isinstance(raw, str) or raw.strip() in ("", CLEAN_VALUE):
        return None
    try:
        d = json.loads(raw)
    except Exception:
        # 파싱이 안 돼도 비어있지 않으면 스크럽 대상으로 본다.
        return "파싱 불가 (내용 있음)"
    if not isinstance(d, dict):
        return "예상 밖 형식 (내용 있음)"

    bits = []
    for key in ("activeCategory", "activeTab"):
        val = d.get(key)
        if isinstance(val, str) and val.strip():
            bits.append(f"{key}={val!r}")
    collapsed = d.get("collapsed")
    if isinstance(collapsed, dict) and collapsed:
        paths = sorted(collapsed)
        shown = ", ".join(repr(p) for p in paths[:3])
        more = f" 외 {len(paths) - 3}개" if len(paths) > 3 else ""
        bits.append(f"collapsed 경로 {len(paths)}개 [{shown}{more}]")
    return "; ".join(bits) if bits else None


def _scrub_widgets_values(wv):
    """widgets_values를 스크럽. (변경여부, 원래값) 반환.

    BMKTabbedNotes의 위젯은 notes_data 하나뿐이라 리스트면 인덱스 0이다.
    프론트엔드 버전에 따라 dict로 직렬화되는 경우도 있어 둘 다 받는다.
    """
    if isinstance(wv, list) and wv:
        old = wv[0]
        if old != CLEAN_VALUE:
            wv[0] = CLEAN_VALUE
            return True, old
    elif isinstance(wv, dict) and WIDGET_NAME in wv:
        old = wv[WIDGET_NAME]
        if old != CLEAN_VALUE:
            wv[WIDGET_NAME] = CLEAN_VALUE
            return True, old
    return False, None


def _walk_graph(obj, hits):
    """그래프 포맷을 재귀 탐색하며 BMKTabbedNotes 노드를 스크럽한다.

    subgraph 정의가 definitions.subgraphs[] 아래에 중첩될 수 있으므로
    구조를 가정하지 않고 dict/list를 전부 훑는다.
    """
    if isinstance(obj, dict):
        if obj.get("type") == NODE_TYPE and "widgets_values" in obj:
            changed, old = _scrub_widgets_values(obj["widgets_values"])
            if changed:
                hits.append((obj.get("id", "?"), _describe(old)))
        # API 포맷: {"7": {"class_type": "...", "inputs": {"notes_data": "..."}}}
        if obj.get("class_type") == NODE_TYPE:
            inputs = obj.get("inputs")
            if isinstance(inputs, dict) and inputs.get(WIDGET_NAME) not in (None, CLEAN_VALUE):
                old = inputs[WIDGET_NAME]
                inputs[WIDGET_NAME] = CLEAN_VALUE
                hits.append(("api", _describe(old)))
        for v in obj.values():
            _walk_graph(v, hits)
    elif isinstance(obj, list):
        for v in obj:
            _walk_graph(v, hits)


def process(path, check_only, in_place):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"[건너뜀] {path}: 읽기 실패 ({e})")
        return None

    hits = []
    _walk_graph(data, hits)

    if not hits:
        print(f"[깨끗함] {path}")
        return False

    print(f"[발견 {len(hits)}건] {path}")
    for node_id, desc in hits:
        print(f"    node {node_id}: {desc or '(내용 있음)'}")

    if check_only:
        return True

    out = path if in_place else f"{os.path.splitext(path)[0]}.scrubbed.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"    → 저장: {out}")
    return True


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="워크플로우 JSON에서 BMK Tabbed Notes의 notes_data를 제거한다.")
    ap.add_argument("paths", nargs="+", help="워크플로우 JSON 경로 (glob 가능)")
    ap.add_argument("--check", action="store_true",
                    help="쓰지 않고 검사만. 남은 게 있으면 종료 코드 1")
    ap.add_argument("--in-place", action="store_true",
                    help="원본 덮어쓰기 (기본은 *.scrubbed.json 생성)")
    args = ap.parse_args(argv)

    files = []
    for pattern in args.paths:
        matched = glob.glob(pattern)
        if not matched:
            print(f"[경고] 일치하는 파일 없음: {pattern}")
        files.extend(matched)

    if not files:
        print("처리할 파일이 없습니다.")
        return 2

    dirty = 0
    for path in sorted(set(files)):
        if process(path, args.check, args.in_place):
            dirty += 1

    print(f"\n총 {len(set(files))}개 중 {dirty}개에서 발견"
          + (" (검사 모드 — 파일은 변경되지 않았습니다)" if args.check else ""))
    return 1 if (args.check and dirty) else 0


if __name__ == "__main__":
    sys.exit(main())
