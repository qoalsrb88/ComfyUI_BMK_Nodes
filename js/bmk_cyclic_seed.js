// js/bmk_cyclic_seed.js
// BMK run-batch 계열 노드 프론트엔드 확장
//  1) Run 컨트롤러의 batch count → BMKCyclicSeed / BMKRunBatchGrid의
//     batch_count 위젯에 자동 싱크 (큐 직전에 동기화)
//  2) BMKCyclicSeed가 cycle 모드일 때 control_after_generate를 "fixed"로 강제

import { app } from "../../scripts/app.js";

const SEED_CLASS = "BMKCyclicSeed";
const GRID_CLASS = "BMKRunBatchGrid";

function activeNodes(cls) {
    return (app.graph?._nodes ?? []).filter(
        // mode 0 = ALWAYS (mute/bypass 된 노드는 제외)
        (n) => n.comfyClass === cls && n.mode === 0
    );
}

function getWidget(node, name) {
    return node.widgets?.find((w) => w.name === name);
}

function setBatchWidget(node, batchCount) {
    const w = getWidget(node, "batch_count");
    if (w && w.value !== batchCount) w.value = batchCount;
}

app.registerExtension({
    name: "BMK.RunBatch",

    setup() {
        // 구/신 프론트엔드 모두 Run 버튼(및 Ctrl+Enter)이
        // app.queuePrompt(number, batchCount)를 거치므로 여기를 후킹
        const origQueuePrompt = app.queuePrompt.bind(app);
        app.queuePrompt = async function (number, batchCount = 1) {
            try {
                if (typeof batchCount === "number" && batchCount >= 1) {
                    // Cyclic Seed: cycle 모드일 때만 싱크 + control 고정
                    for (const node of activeNodes(SEED_CLASS)) {
                        if (getWidget(node, "mode")?.value !== "cycle") continue;
                        setBatchWidget(node, batchCount);
                        const ctrlW = getWidget(node, "control_after_generate");
                        if (ctrlW) ctrlW.value = "fixed";
                    }
                    // Run Batch Grid: 항상 싱크
                    for (const node of activeNodes(GRID_CLASS)) {
                        setBatchWidget(node, batchCount);
                    }
                    app.graph?.setDirtyCanvas(true, false);
                }
            } catch (e) {
                console.warn("[BMK.RunBatch] batch count sync failed:", e);
            }
            return origQueuePrompt(number, batchCount);
        };
    },

    beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== SEED_CLASS) return;

        const onNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const r = onNodeCreated?.apply(this, arguments);

            const modeW = getWidget(this, "mode");
            const syncCtrl = () => {
                const ctrlW = getWidget(this, "control_after_generate");
                if (ctrlW && modeW?.value === "cycle") {
                    ctrlW.value = "fixed";
                }
            };

            if (modeW) {
                const origCb = modeW.callback;
                modeW.callback = (...args) => {
                    const r2 = origCb?.apply(this, args);
                    syncCtrl();
                    return r2;
                };
            }
            setTimeout(syncCtrl, 0);
            return r;
        };
    },
});
