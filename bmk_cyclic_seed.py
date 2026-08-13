# bmk_cyclic_seed.py  (v5)
# WebUI 스타일 순환 시드 노드 (run/queue 배치 기준)
#
# v5 변경점 (시드 중복 버그 수정):
#   - cycle + link_to_run 모드에서 더 이상 큐 잔량(tasks_remaining)을 보지
#     않는다. 시드 노드는 그래프 맨 앞에서 실행돼 큐가 채워지는 중이라
#     첫 프롬프트에서 잔량이 1로 잡혀 "마지막"으로 오판 → 시드 중복이
#     발생했다(300,300,301,302).
#   - 대신 공유 인덱스(BMKRunCycle)를 사용한다. 시드는 매 실행 인덱스를
#     1씩 올리기만 하고, 사이클 리셋은 그리드 노드가 한 run 완성 시 수행한다.
#     시드는 generation→grid 의존성상 항상 그리드보다 먼저 실행되므로
#     한 run에서 base+0 .. base+(N-1) 이 정확히 나온다.
#   - cycle + link_to_run=OFF(수동) 은 기존처럼 batch_count로 wrap.
#   - mode="control_after_generate"(일반 시드) 은 기존 그대로(영향 없음).
#
# 사용 전제:
#   - cycle 모드는 base_seed의 control_after_generate 위젯을 'fixed'로 둘 것.
#   - run 연동 cycle 시드는 run 연동 그리드와 "한 쌍"으로 쓸 것
#     (그리드가 사이클 경계를 리셋해 준다). 그리드 없이 단독으로 run 연동
#     cycle을 쓰면 인덱스가 리셋되지 않고 계속 증가한다 → 이 경우 수동 모드 사용.

MAX_SEED = 0xFFFFFFFFFFFFFFFF

MODE_CYCLE = "cycle"
MODE_NORMAL = "control_after_generate"

try:
    from .bmk_run_cycle import BMKRunCycle
except Exception:  # 비패키지 로드 등 폴백
    try:
        from bmk_run_cycle import BMKRunCycle
    except Exception:
        class BMKRunCycle:  # 최소 폴백(리셋 없음)
            _index = 0
            @classmethod
            def peek(cls): return cls._index
            @classmethod
            def advance(cls): cls._index += 1; return cls._index
            @classmethod
            def reset(cls): cls._index = 0


class BMKCyclicSeed:
    # 수동 cycle 모드용 인스턴스 상태. 서버가 살아있는 동안 유지됨.
    _state = {}

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "mode": ([MODE_CYCLE, MODE_NORMAL], {"default": MODE_CYCLE}),
                "base_seed": ("INT", {
                    "default": 0, "min": 0, "max": MAX_SEED,
                    "control_after_generate": True,
                }),
                "link_to_run": ("BOOLEAN", {
                    "default": True,
                    "label_on": "Run 연동(그리드와 동기화)",
                    "label_off": "수동(batch_count 사용)",
                }),
                "batch_count": ("INT", {"default": 4, "min": 1, "max": 4096}),
            },
            "hidden": {
                "unique_id": "UNIQUE_ID",
            },
        }

    RETURN_TYPES = ("INT", "STRING")
    RETURN_NAMES = ("seed", "seed_text")
    FUNCTION = "next_seed"
    CATEGORY = "BMK/utils"
    DESCRIPTION = (
        "WebUI 스타일 순환 시드. cycle 모드는 base_seed부터 1씩 올린 시드를 배치 안에서 "
        "순환시켜, 시드만 다른 결과를 한 run으로 뽑습니다. base_seed의 "
        "control_after_generate는 'fixed'로 두세요. run 연동 시에는 BMK Run Batch Grid와 "
        "한 쌍으로 써야 합니다(사이클 경계 리셋을 그리드가 담당)."
    )
    SEARCH_ALIASES = [
        "cyclic seed", "seed", "batch seed", "seed increment",
        "순환 시드", "시드", "배치 시드",
    ]

    @classmethod
    def IS_CHANGED(cls, mode=MODE_CYCLE, base_seed=0, batch_count=1, **kwargs):
        if mode != MODE_CYCLE:
            # 일반 시드 모드: 값이 같으면 캐시 유지 → 평범한 노드처럼 동작
            return f"{mode}:{base_seed}"
        # cycle 모드: NaN != NaN 이므로 매 실행마다 강제 재실행
        return float("NaN")

    def next_seed(self, mode, base_seed, link_to_run, batch_count, unique_id):
        # ── 일반 시드 모드 ─────────────────────────────────────
        if mode != MODE_CYCLE:
            return (base_seed, str(base_seed))

        # ── cycle + Run 연동 ──────────────────────────────────
        # 큐 타이밍을 보지 않는다. 공유 인덱스를 읽고 1 증가시킨다.
        # 리셋은 그리드가 run 완성 시 BMKRunCycle.reset() 으로 수행.
        if link_to_run:
            idx = BMKRunCycle.peek()
            seed = (base_seed + idx) % (MAX_SEED + 1)
            BMKRunCycle.advance()
            print(f"[BMKCyclicSeed] seed {seed} (run-linked idx {idx})")
            return (seed, str(seed))

        # ── cycle + 수동: batch_count 기준 wrap (기존 동작) ────
        key = str(unique_id)
        st = BMKCyclicSeed._state.get(key)
        if (st is None or st.get("mode") != "manual"
                or st.get("params") != (base_seed, batch_count)):
            st = {"mode": "manual", "params": (base_seed, batch_count), "counter": 0}
            BMKCyclicSeed._state[key] = st
        seed = (base_seed + st["counter"]) % (MAX_SEED + 1)
        st["counter"] = (st["counter"] + 1) % batch_count
        print(f"[BMKCyclicSeed] seed {seed} "
              f"(cycle pos {st['counter']}/{batch_count}, manual)")
        return (seed, str(seed))


NODE_CLASS_MAPPINGS = {
    "BMKCyclicSeed": BMKCyclicSeed,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "BMKCyclicSeed": "Cyclic Seed (Run Batch)",
}
