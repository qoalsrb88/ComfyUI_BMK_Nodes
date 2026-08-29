// js/bmk_cyclic_seed.js  (v2)
// BMK run-batch 계열 노드 프론트엔드 확장
//
// v1 기능:
//   1) Run 컨트롤러의 batch count → BMKCyclicSeed / BMKRunBatchGrid의
//      batch_count 위젯에 자동 싱크 (큐 직전에 동기화)
//   2) BMKCyclicSeed가 cycle 모드일 때 control_after_generate를 "fixed"로 강제
//
// v2 변경점 (base_seed 난수 버튼):
//   3) BMKCyclicSeed에 "🎲 New Fixed Random" 버튼 위젯 추가. rgthree Seed 노드의
//      동명 버튼처럼, 누를 때마다 base_seed에 새 난수를 한 번 박아 넣고 그 뒤로는
//      고정된다 — cycle 모드가 요구하는 "base_seed는 fixed" 전제와 충돌하지 않는다.
//   4) 난수 범위는 노드 property(bmk_seed_range)로 선택. 기본값은 NovelAI 호환
//      32비트(1 ~ 4,294,967,295). NAI는 시드를 uint32로 다루고 UI상 0은 "랜덤"의
//      의미라 하한에서 0을 뺐다.
//   5) cycle 모드에서는 base_seed + (batch_count - 1)이 범위를 넘지 않도록 상한에
//      여유를 두고 뽑는다. 한 run의 모든 시드가 같은 자릿수 대역에 머무른다.
//   6) property(bmk_randomize_each_run)를 켜면 Run을 누를 때마다 자동으로 새
//      base_seed를 뽑는다. 한 run 안에서는 고정이므로 사이클 규칙은 그대로 유지.
//
// 구현 주의:
//   - 버튼 위젯은 widgets 배열 "맨 뒤"에만 붙이고 serialize = false로 둔다.
//     중간에 끼우면 기존 워크플로우의 widgets_values 인덱스가 한 칸씩 밀려
//     link_to_run / batch_count 값이 어긋난다.
//   - 난수는 crypto.getRandomValues + 거부 표집(rejection sampling)으로 뽑는다.
//     Math.random() % span 은 상위 구간에 편향이 생긴다.

import { app } from "../../scripts/app.js";

const SEED_CLASS = "BMKCyclicSeed";
const GRID_CLASS = "BMKRunBatchGrid";
const MODE_CYCLE = "cycle";

const PROP_RANGE = "bmk_seed_range";
const PROP_AUTO_RANDOM = "bmk_randomize_each_run";
const BUTTON_NAME = "bmk_new_fixed_random";

// base_seed 난수 범위 프리셋.
const SEED_RANGES = {
    // NovelAI는 시드를 uint32로 다룬다. 0은 UI상 "랜덤"이라 제외.
    novelai: { short: "NAI", label: "NovelAI 32bit (1 ~ 4294967295)", min: 1, max: 4294967295 },
    // ComfyUI 프론트엔드 기본 randomize와 같은 대역(2^50).
    comfy: { short: "Comfy", label: "ComfyUI 기본 (0 ~ 2^50)", min: 0, max: 1125899906842624 },
    // 9자리 짧은 시드.
    short: { short: "9자리", label: "짧은 시드 (0 ~ 999999999)", min: 0, max: 999999999 },
};
const DEFAULT_RANGE = "novelai";

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

function currentRangeKey(node) {
    const key = node.properties?.[PROP_RANGE];
    return SEED_RANGES[key] ? key : DEFAULT_RANGE;
}

/** [lo, hi] 균등 정수 난수. 편향 없이 뽑기 위해 거부 표집을 쓴다. */
function randomInt(lo, hi) {
    const span = hi - lo + 1;
    if (span <= 1) return lo;

    const rng = globalThis.crypto;
    if (span <= 4294967296 && rng?.getRandomValues) {
        const buf = new Uint32Array(1);
        // 2^32를 span의 배수로 잘라, 나머지 구간에 걸린 값은 버린다.
        const limit = Math.floor(4294967296 / span) * span;
        let v;
        do {
            rng.getRandomValues(buf);
            v = buf[0];
        } while (v >= limit);
        return lo + (v % span);
    }
    // 2^32를 넘는 범위(comfy 프리셋 등)는 부동소수 난수로 폴백.
    return lo + Math.floor(Math.random() * span);
}

/** 현재 모드/범위/배치 수를 반영한 새 base_seed 값을 계산한다. */
function pickBaseSeed(node) {
    const range = SEED_RANGES[currentRangeKey(node)];
    const opts = getWidget(node, "base_seed")?.options ?? {};

    // 위젯 자체의 min/max와 교집합을 취한다.
    const widgetMin = Number(opts.min);
    const widgetMax = Number(opts.max);
    let lo = Number.isFinite(widgetMin) ? Math.max(range.min, widgetMin) : range.min;
    let hi = Number.isFinite(widgetMax) ? Math.min(range.max, widgetMax) : range.max;

    // cycle 모드: base_seed + (batch_count - 1) 까지 같은 대역에 머물도록 상한 축소.
    if (getWidget(node, "mode")?.value === MODE_CYCLE) {
        const n = Math.max(1, Number(getWidget(node, "batch_count")?.value) || 1);
        hi = Math.max(lo, hi - (n - 1));
    }

    if (hi < lo) hi = lo;
    return randomInt(lo, hi);
}

/** base_seed에 새 난수를 박아 넣는다 (rgthree의 New Fixed Random과 동일한 의미). */
function applyNewFixedRandom(node) {
    const w = getWidget(node, "base_seed");
    if (!w) return;

    const value = pickBaseSeed(node);
    w.value = value;
    try {
        w.callback?.(value, app.canvas, node);
    } catch (e) {
        console.warn("[BMK.RunBatch] base_seed callback failed:", e);
    }

    // cycle 모드 전제(base_seed는 fixed)를 유지.
    if (getWidget(node, "mode")?.value === MODE_CYCLE) {
        const ctrlW = getWidget(node, "control_after_generate");
        if (ctrlW) ctrlW.value = "fixed";
    }

    node.setDirtyCanvas?.(true, true);
    return value;
}

/** 버튼 라벨에 현재 선택된 범위를 표기한다. */
function refreshButtonLabel(node) {
    const btn = getWidget(node, BUTTON_NAME);
    if (btn) btn.label = `🎲 New Fixed Random (${SEED_RANGES[currentRangeKey(node)].short})`;
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
                        if (getWidget(node, "mode")?.value === MODE_CYCLE) {
                            setBatchWidget(node, batchCount);
                            const ctrlW = getWidget(node, "control_after_generate");
                            if (ctrlW) ctrlW.value = "fixed";
                        }
                        // batch_count 싱크 이후에 뽑아야 상한 여유 계산이 맞다.
                        if (node.properties?.[PROP_AUTO_RANDOM]) {
                            applyNewFixedRandom(node);
                        }
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

            // ── 난수 관련 property (widgets_values와 무관하게 별도 직렬화됨) ──
            this.addProperty?.(PROP_RANGE, DEFAULT_RANGE, "combo", {
                values: Object.keys(SEED_RANGES),
            });
            this.addProperty?.(PROP_AUTO_RANDOM, false, "boolean");

            // ── New Fixed Random 버튼 ──
            // 반드시 widgets 배열 맨 뒤에 붙일 것 (인덱스 시프트 방지).
            const btn = this.addWidget(
                "button",
                BUTTON_NAME,
                null,
                () => applyNewFixedRandom(this)
            );
            btn.serialize = false;
            btn.options = { ...(btn.options ?? {}), serialize: false };

            const modeW = getWidget(this, "mode");
            const syncCtrl = () => {
                const ctrlW = getWidget(this, "control_after_generate");
                if (ctrlW && modeW?.value === MODE_CYCLE) {
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

            setTimeout(() => {
                syncCtrl();
                refreshButtonLabel(this);
            }, 0);
            return r;
        };

        // 저장된 워크플로우를 다시 열었을 때도 라벨을 property에 맞춘다.
        const onConfigure = nodeType.prototype.onConfigure;
        nodeType.prototype.onConfigure = function () {
            const r = onConfigure?.apply(this, arguments);
            setTimeout(() => refreshButtonLabel(this), 0);
            return r;
        };

        const origMenu = nodeType.prototype.getExtraMenuOptions;
        nodeType.prototype.getExtraMenuOptions = function (canvas, options) {
            const r = origMenu?.apply(this, arguments);
            const node = this;
            const activeKey = currentRangeKey(node);

            options.push(null, {
                content: "🎲 New Fixed Random",
                callback: () => applyNewFixedRandom(node),
            });

            for (const [key, range] of Object.entries(SEED_RANGES)) {
                options.push({
                    content: `${key === activeKey ? "●" : "○"} 범위: ${range.label}`,
                    callback: () => {
                        node.properties[PROP_RANGE] = key;
                        refreshButtonLabel(node);
                        node.setDirtyCanvas?.(true, true);
                    },
                });
            }

            options.push({
                content: `${node.properties?.[PROP_AUTO_RANDOM] ? "☑" : "☐"} Run마다 base_seed 새로 뽑기`,
                callback: () => {
                    node.properties[PROP_AUTO_RANDOM] = !node.properties?.[PROP_AUTO_RANDOM];
                },
            });

            return r;
        };
    },
});
