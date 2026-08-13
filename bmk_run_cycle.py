# bmk_run_cycle.py
# Run 연동(run-linked) 노드들이 공유하는 사이클 인덱스.
#
# 설계:
#   - 시드 노드(BMKCyclicSeed)는 그래프상 맨 앞에서 실행되므로, 그 시점엔
#     큐가 아직 채워지는 중이라 tasks_remaining 으로 "마지막"을 판정할 수
#     없다(첫 프롬프트에서 1로 잡혀 오판 → 시드 중복).
#   - 반대로 그리드 노드(BMKRunBatchGrid)는 맨 뒤에서 실행되므로 큐 잔량이
#     신뢰 가능하다. 따라서 "사이클 경계(리셋)"는 그리드가 소유한다.
#   - 시드는 매 실행 인덱스를 1씩 올리기만 하고(타이밍 무관), 그리드가 한
#     사이클(run)을 완성할 때 인덱스를 0으로 리셋한다.
#   - 시드는 generation→grid 의존성상 항상 그리드보다 먼저 실행되므로,
#     한 run에서 base+0, base+1, ... base+(N-1) 이 정확히 보장된다.
#
# 주의: 인덱스는 프로세스 전역(단일 카운터)이다. 한 워크플로에 Run 연동
#       시드+그리드를 "한 쌍"만 두는 것을 전제로 한다. 여러 쌍을 동시에
#       Run 연동으로 쓰면 인덱스가 충돌하므로, 그 경우 수동 모드를 쓸 것.


class BMKRunCycle:
    _index = 0

    @classmethod
    def peek(cls):
        return cls._index

    @classmethod
    def advance(cls):
        cls._index += 1
        return cls._index

    @classmethod
    def reset(cls):
        cls._index = 0
