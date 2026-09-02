// bmk_persistent_bridge.js — BMKPersistentBridge 짝 JS 확장
//
// 역할
//  1) slot(저장본 이름) 자동 발급: 노드 생성 시 `auto-xxxxxxxx` 를 채운다.
//     같은 그래프 안에서 auto 이름이 중복되면(복사/붙이기) 나중에 추가된 노드 쪽을
//     재발급한다. 사용자가 직접 적은 이름은 공유가 의도일 수 있어 건드리지 않는다.
//  2) 실행 결과(ui.bmk_bridge[0].saved)의 저장본 파일 목록을 node.properties 에 기록.
//     → 워크플로우 재로드/서버 재시작 후에도 노드 프리뷰에 저장본을 다시 표시한다.
//  3) mode 에 맞는 프리뷰 표시.
//       passthrough / saved → 저장본,  custom → image 위젯이 가리키는 파일.
//     프리뷰 반영은 실제 실행 결과와 같은 경로(api 'executed' 이벤트)로 흘려보내
//     출력 스토어와 리액티브 미러가 모두 갱신되게 한다.
//  4) image 위젯이 사용자(파일 선택/업로드) 또는 마스크 에디터·클립스페이스에 의해
//     바뀌면 mode 를 custom 으로 자동 전환한다(그 파일을 쓰겠다는 의도로 해석).
//  5) custom 이 아닐 때 image / upload 위젯을 회색(비활성) 표시.
//
// 구현 메모
//  * 프론트엔드의 image_upload 콤보는 노드 생성 직후 rAF 로 콤보 파일을 프리뷰에
//    올린다. mode 와 무관하게 덮어쓰므로, 우리 프리뷰 적용은 2단 rAF 로 그 뒤에 건다.
//  * 마스크 에디터/클립스페이스는 콜백 없이 widget.value 를 직접 대입한다.
//    BaseWidget.prototype 의 value 접근자를 인스턴스 레벨에서 감싸 대입을 감지하고,
//    'clipspace/' 로 시작하는 값에 한해 custom 전환한다(콤보 새로고침 등과 구분).
//    configure(저장본 복원) 중의 대입은 사용자 행동이 아니므로 무시한다.
//    후킹이 불가능한 환경을 위해 onDrawBackground 폴링을 보조로 남긴다.

import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const NODE_NAME = "BMKPersistentBridge";
const PROP_SAVED = "bmk_bridge_saved";
const MODE_CUSTOM = "custom";
const PREVIEW_PROMPT_ID = "bmk-bridge-preview";
const AUTO_SLOT_RE = /^auto-[0-9a-f]{8}$/;
const CLIPSPACE_RE = /^clipspace[\\/]/i;

function getWidget(node, name) {
    return node.widgets?.find((w) => w.name === name) ?? null;
}

function findUploadWidget(node) {
    return (
        node.widgets?.find(
            (w) =>
                w.type === "button" &&
                w.name !== "image" &&
                (w.name === "upload" ||
                    w.value === "image" ||
                    /choose file/i.test(String(w.label ?? "")) ||
                    /choose file/i.test(String(w.name ?? "")))
        ) ?? null
    );
}

// ─── slot 자동 발급 ──────────────────────────────────────────────

function newAutoSlot() {
    let hex = "";
    try {
        const buf = new Uint8Array(4);
        crypto.getRandomValues(buf);
        hex = Array.from(buf, (b) => b.toString(16).padStart(2, "0")).join("");
    } catch (e) {
        hex = Math.floor(Math.random() * 0xffffffff).toString(16).padStart(8, "0");
    }
    return `auto-${hex}`;
}

function ensureAutoSlot(node) {
    const w = getWidget(node, "slot");
    if (!w) return;
    if (!String(w.value ?? "").trim()) w.value = newAutoSlot();
}

// 같은 그래프의 다른 BMKPersistentBridge 가 같은 auto 이름을 쓰면 이 노드를 재발급
function dedupeAutoSlot(node, graph) {
    const w = getWidget(node, "slot");
    if (!w || !graph?.nodes) return;
    const mine = String(w.value ?? "");
    if (!AUTO_SLOT_RE.test(mine)) return;
    const clash = graph.nodes.some(
        (n) =>
            n !== node &&
            (n.comfyClass ?? n.type) === NODE_NAME &&
            String(getWidget(n, "slot")?.value ?? "") === mine
    );
    if (clash) {
        w.value = newAutoSlot();
        console.log(
            `[BMK PersistentBridge] #${node.id} slot 중복(${mine}) → ${w.value} 재발급`
        );
        // 저장본 목록은 원본 노드의 것이므로 넘겨받지 않는다
        if (node.properties) delete node.properties[PROP_SAVED];
    }
}

// ─── 프리뷰 ─────────────────────────────────────────────────────

// LoadImage 계열 위젯 값("a.png", "sub/a.png", "a.png [input]") → 출력 항목
function parseImageValue(value) {
    let filename = String(value ?? "");
    let type = "input";
    const annotated = filename.match(/^(.*) \[(input|output|temp)\]$/);
    if (annotated) {
        filename = annotated[1];
        type = annotated[2];
    }
    filename = filename.replace(/\\/g, "/");
    let subfolder = "";
    const slash = filename.lastIndexOf("/");
    if (slash >= 0) {
        subfolder = filename.slice(0, slash);
        filename = filename.slice(slash + 1);
    }
    return { filename, subfolder, type };
}

// 루트 그래프 노드는 "id", 서브그래프 안이면 "부모SubgraphNode id:...:노드 id"
function findSubgraphPath(graph, target, depth) {
    if (!graph || depth > 8) return null;
    for (const n of graph.nodes ?? []) {
        if (!n.isSubgraphNode?.() || !n.subgraph) continue;
        if (n.subgraph === target) return [n.id];
        const sub = findSubgraphPath(n.subgraph, target, depth + 1);
        if (sub) return [n.id, ...sub];
    }
    return null;
}

function executionIdOf(node) {
    const root = app.rootGraph ?? app.graph;
    if (!node.graph || node.graph === root || node.graph.isRootGraph) {
        return String(node.id);
    }
    const path = findSubgraphPath(root, node.graph, 0);
    return path ? [...path, node.id].join(":") : String(node.id);
}

function locatorIdOf(node) {
    const root = app.rootGraph ?? app.graph;
    if (!node.graph || node.graph === root || node.graph.isRootGraph || !node.graph.id) {
        return String(node.id);
    }
    return `${node.graph.id}:${node.id}`;
}

function showOutputs(node, images) {
    if (!images?.length) {
        // 표시할 것이 없으면 프리뷰 제거 (image_upload 콤보가 올려둔 것 포함)
        try {
            delete app.nodeOutputs[locatorIdOf(node)];
        } catch (e) {
            /* noop */
        }
        node.imgs = undefined;
        node.images = undefined;
        node.graph?.setDirtyCanvas(true);
        return;
    }
    const execId = executionIdOf(node);
    api.dispatchCustomEvent("executed", {
        node: execId,
        display_node: execId,
        output: { images },
        prompt_id: PREVIEW_PROMPT_ID,
    });
    node.graph?.setDirtyCanvas(true);
}

function refreshWidgetState(node) {
    const custom = getWidget(node, "mode")?.value === MODE_CUSTOM;
    const imgW = getWidget(node, "image");
    const upW = findUploadWidget(node);
    if (imgW) imgW.disabled = !custom;
    if (upW) upW.disabled = !custom;
    node.graph?.setDirtyCanvas(true, false);
}

function applyPreview(node) {
    const mode = getWidget(node, "mode")?.value;
    if (mode === MODE_CUSTOM) {
        const v = getWidget(node, "image")?.value;
        const entry = v ? parseImageValue(v) : null;
        showOutputs(node, entry?.filename ? [entry] : []);
    } else {
        const saved = node.properties?.[PROP_SAVED];
        showOutputs(node, Array.isArray(saved) ? saved : []);
    }
    refreshWidgetState(node);
}

// image_upload 콤보의 생성 직후 rAF 뒤에 우리 프리뷰가 오도록 2단 rAF
function schedulePreview(node) {
    requestAnimationFrame(() => requestAnimationFrame(() => applyPreview(node)));
}

function switchToCustom(node, reason) {
    const modeW = getWidget(node, "mode");
    if (modeW && modeW.value !== MODE_CUSTOM) {
        modeW.value = MODE_CUSTOM;
        console.log(`[BMK PersistentBridge] #${node.id} mode → custom (${reason})`);
        node.graph?.setDirtyCanvas(true, true);
    }
    applyPreview(node);
}

// ─── image 위젯 값 변경 감지 ────────────────────────────────────

// 콤보 목록에 없는 값(마스크 에디터의 clipspace 경로 등)은 프론트엔드의
// "미디어 입력 누락" 검사에 걸린다(값이 options.values 에 있는지로 판정).
// 현재 값을 목록에 등록해 두면 검사를 통과한다 (업로드 시 core 가 하는 것과 동일).
function ensureComboValue(widget, value) {
    if (!widget || typeof value !== "string" || !value.trim()) return;
    widget.options ??= {};
    let values = widget.options.values;
    if (typeof values === "function") return; // 동적 목록은 건드리지 않음
    if (!Array.isArray(values)) values = widget.options.values = [];
    if (!values.includes(value)) values.push(value);
}

function onImageValueChanged(node, value) {
    ensureComboValue(getWidget(node, "image"), value);
    if (node._bmkConfiguring) {
        node._bmkLastImage = value; // 저장본 복원 — 사용자 행동 아님
        return;
    }
    if (value === node._bmkLastImage) return;
    node._bmkLastImage = value;
    if (typeof value === "string" && CLIPSPACE_RE.test(value)) {
        // 마스크 에디터는 대입 직후 자체 프리뷰 갱신을 이어가므로 한 틱 뒤에 전환
        setTimeout(() => switchToCustom(node, "mask editor / clipspace"), 0);
    } else {
        refreshWidgetState(node);
    }
}

function findDescriptor(obj, prop) {
    let o = obj;
    while (o) {
        const d = Object.getOwnPropertyDescriptor(o, prop);
        if (d) return d;
        o = Object.getPrototypeOf(o);
    }
    return null;
}

// BaseWidget.prototype 의 value 접근자를 인스턴스에서 감싸 직접 대입을 감지
function hookImageValue(node, imgW) {
    if (!imgW || imgW._bmkValueHooked) return;
    const desc = findDescriptor(imgW, "value");
    if (!desc || !desc.configurable) return; // 후킹 불가 → 폴링만 사용
    let store = desc.get ? undefined : desc.value;
    try {
        Object.defineProperty(imgW, "value", {
            configurable: true,
            enumerable: desc.enumerable ?? true,
            get() {
                return desc.get ? desc.get.call(this) : store;
            },
            set(v) {
                if (desc.set) desc.set.call(this, v);
                else store = v;
                onImageValueChanged(node, v);
            },
        });
        imgW._bmkValueHooked = true;
    } catch (e) {
        console.warn("[BMK PersistentBridge] image value hook failed:", e);
    }
}

// 보조: 후킹이 안 된 환경에서 그리기 시점에 값 변화를 감지
function pollImageWidget(node) {
    const imgW = getWidget(node, "image");
    if (!imgW || imgW._bmkValueHooked) return;
    const v = imgW.value;
    if (v === node._bmkLastImage) return;
    onImageValueChanged(node, v);
}

function setupNode(node) {
    node.properties ??= {};
    ensureAutoSlot(node);

    const modeW = getWidget(node, "mode");
    if (modeW) {
        const origCb = modeW.callback;
        modeW.callback = function (...args) {
            const r = origCb?.apply(this, args);
            applyPreview(node);
            return r;
        };
    }

    const imgW = getWidget(node, "image");
    if (imgW) {
        const origCb = imgW.callback;
        imgW.callback = function (...args) {
            // 기본 콜백: 프리뷰를 선택 파일로 교체 (프론트엔드 image_upload 동작)
            const r = origCb?.apply(this, args);
            node._bmkLastImage = imgW.value;
            switchToCustom(node, "image 위젯 변경");
            return r;
        };
        hookImageValue(node, imgW);
    }

    node._bmkLastImage = imgW?.value;
    schedulePreview(node);
}

app.registerExtension({
    name: "BMK.PersistentBridge",

    beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== NODE_NAME) return;

        const onNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const r = onNodeCreated?.apply(this, arguments);
            setupNode(this);
            return r;
        };

        // 저장본 복원 중의 위젯 대입은 사용자 행동으로 취급하지 않도록 플래그
        const configure = nodeType.prototype.configure;
        nodeType.prototype.configure = function () {
            this._bmkConfiguring = true;
            try {
                return configure?.apply(this, arguments);
            } finally {
                this._bmkConfiguring = false;
            }
        };

        const onConfigure = nodeType.prototype.onConfigure;
        nodeType.prototype.onConfigure = function () {
            const r = onConfigure?.apply(this, arguments);
            // 저장본 로드로 위젯값이 복원된 뒤 기준값을 다시 잡고 프리뷰 적용
            const imgW = getWidget(this, "image");
            this._bmkLastImage = imgW?.value;
            ensureComboValue(imgW, imgW?.value); // 누락 미디어 검사 대비
            schedulePreview(this);
            return r;
        };

        // 그래프에 추가된 시점(configure 이후)에 auto slot 중복 검사
        const onAdded = nodeType.prototype.onAdded;
        nodeType.prototype.onAdded = function (graph) {
            const r = onAdded?.apply(this, arguments);
            dedupeAutoSlot(this, graph ?? this.graph);
            return r;
        };

        const onExecuted = nodeType.prototype.onExecuted;
        nodeType.prototype.onExecuted = function (message) {
            const r = onExecuted?.apply(this, arguments);
            const info = message?.bmk_bridge?.[0];
            if (info) {
                this.properties ??= {};
                if (Array.isArray(info.saved) && info.saved.length) {
                    this.properties[PROP_SAVED] = info.saved;
                } else {
                    delete this.properties[PROP_SAVED];
                }
                refreshWidgetState(this);
            }
            return r;
        };

        const onDrawBackground = nodeType.prototype.onDrawBackground;
        nodeType.prototype.onDrawBackground = function () {
            const r = onDrawBackground?.apply(this, arguments);
            pollImageWidget(this);
            return r;
        };
    },
});
