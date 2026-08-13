// bmk_run_batch_sync.js  (v2)
//
// v2 변경점 (중요):
//   - 이전 버전은 app.queuePrompt(number, batchCount) 인자를 읽어
//     batch_count 위젯에 써넣었으나, 신형 프론트엔드는 Run 컨트롤러 값을
//     이 인자로 넘기지 않고 단일 프롬프트를 N번 큐에 넣는다(인자는 항상 1).
//     → 그 동기화는 잘못된 값(1)을 써넣어 그리드를 망가뜨린다. 제거함.
//   - 실제 batch 연동은 이제 파이썬 쪽에서 "큐 잔량"으로 판정하므로
//     JS가 컨트롤러 값을 읽거나 위젯을 덮어쓸 필요가 전혀 없다.
//   - 이 파일은 순수 UI용: link_to_run=ON 일 때 batch_count 위젯을
//     회색(비활성)으로 표시해 "지금은 Run에 종속되어 무시됨"을 알린다.
//
// 설치: ComfyUI_BMK_Nodes/js/ 에 두면 자동 로드.
// 주의: 기존 bmk_cyclic_seed.js 안에 queuePrompt 패치/배치 동기화 코드가
//       남아 있다면 반드시 제거하세요(잘못된 batch_count=1을 써넣습니다).

import { app } from "../../scripts/app.js";

const TARGET_NODES = ["BMKCyclicSeed", "BMKRunBatchGrid"];
const LINK_WIDGET = "link_to_run";
const COUNT_WIDGET = "batch_count";

function nodeClass(node) {
    return node.comfyClass || node.type;
}

function getWidget(node, name) {
    return node.widgets ? node.widgets.find((w) => w.name === name) : null;
}

app.registerExtension({
    name: "BMK.RunBatchSync",

    nodeCreated(node) {
        if (!TARGET_NODES.includes(nodeClass(node))) return;

        const link = getWidget(node, LINK_WIDGET);
        const cnt = getWidget(node, COUNT_WIDGET);
        if (!link || !cnt) return;

        // link_to_run 상태에 따라 batch_count 위젯 활성/비활성 표시
        const refresh = () => {
            cnt.disabled = link.value !== false; // 연동 시 회색 처리(무시됨 표시)
            if (app.graph) app.graph.setDirtyCanvas(true, false);
        };

        const origCb = link.callback;
        link.callback = function (v) {
            if (origCb) origCb.call(this, v);
            refresh();
        };

        setTimeout(refresh, 0);
    },
});
