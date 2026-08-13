from __future__ import annotations

import os
import threading
import time
from typing import Dict, Iterable, List, Optional


# Impact Pack의 와일드카드 txt/yaml 파일을 감시하여, 변경이 감지되면
# 자동으로 `impact.wildcards.wildcard_load()`를 호출한다.
# 이는 "Impact: Refresh Wildcard" 버튼을 누르는 것과 동일한 효과로,
# 전역 wildcard_dict를 다시 채워 ImpactWildcardProcessor / Inspire Pack /
# BMKWildcardPrompt 등 모든 소비자에 즉시 반영된다.

_THREAD_NAME = "BMKWildcardAutoreload"


def _build_snapshot(watch_dirs: Iterable[str]) -> Dict[str, float]:
    """감시 대상 디렉토리의 와일드카드 파일 → mtime 매핑을 생성한다."""
    signature: Dict[str, float] = {}
    for directory in watch_dirs:
        for root, _, files in os.walk(directory):
            for name in files:
                if name.lower().endswith((".txt", ".yaml", ".yml")):
                    path = os.path.join(root, name)
                    try:
                        signature[path] = os.path.getmtime(path)
                    except OSError:
                        pass
    return signature


def start(interval: float = 1.0, extra_dirs: Optional[Iterable[str]] = None) -> bool:
    """와일드카드 자동 리로드 백그라운드 스레드를 기동한다.

    Args:
        interval: 변경 감지 폴링 주기(초).
        extra_dirs: 기본 경로 외에 추가로 감시할 디렉토리 목록.

    Returns:
        새로 기동했으면 True, 이미 실행 중이라 건너뛰었으면 False.
    """
    # 중복 기동 방지 (ComfyUI 재import / 핫리로드 대비)
    if any(t.name == _THREAD_NAME for t in threading.enumerate()):
        return False

    def _loop() -> None:
        # Impact Pack 로드 순서에 의존하지 않도록 import를 스레드 내에서 재시도.
        wildcards_module = None
        for _ in range(60):
            try:
                import impact.wildcards as _iw

                wildcards_module = _iw
                break
            except Exception:
                time.sleep(1.0)

        if wildcards_module is None:
            print(
                "[ComfyUI_BMK_Nodes] Impact Pack not found "
                "-> wildcard autoreload disabled."
            )
            return

        base = wildcards_module.wildcards_path
        watch_dirs: List[str] = [
            base,
            os.path.join(os.path.dirname(base), "custom_wildcards"),
        ]
        if extra_dirs:
            watch_dirs.extend(extra_dirs)
        watch_dirs = [d for d in watch_dirs if d and os.path.isdir(d)]

        last = _build_snapshot(watch_dirs)
        print(
            "[ComfyUI_BMK_Nodes] Wildcard autoreload started "
            f"(watching: {watch_dirs})"
        )

        while True:
            time.sleep(interval)
            try:
                current = _build_snapshot(watch_dirs)
                if current != last:
                    wildcards_module.wildcard_load()  # = "Impact: Refresh Wildcard"
                    print(
                        "[ComfyUI_BMK_Nodes] Wildcard change detected "
                        "-> reloaded."
                    )
                    last = current
            except Exception as exc:
                print(f"[ComfyUI_BMK_Nodes] Wildcard autoreload error: {exc}")

    threading.Thread(target=_loop, name=_THREAD_NAME, daemon=True).start()
    return True
