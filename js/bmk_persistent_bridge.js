// bmk_persistent_bridge.js — BMKPersistentBridge / BMKPersistentBridgeString 짝 JS 확장
//
// slot 자동 발급·복사본 중복 방지(아래 1)는 두 노드 공통이며, 중복 검사는 같은 종류의
// 노드끼리만 한다(이미지 <slot>_b0.png 와 텍스트 <slot>.txt 는 충돌하지 않음).
// String 전용 동작은 파일 아래쪽 "String 버전" 절 참고.
//
// 역할 (Image)
//  1) slot(저장본 이름) 자동 발급: 노드 생성 시 `auto-xxxxxxxx` 를 채운다.
//     붙이기(Ctrl+V)·복제로 같은 워크플로우에 같은 이름이 생기면 새 노드 쪽을 바꾼다.
//       auto 이름 → 재발급,  직접 적은 이름 → `이름_2`, `이름_3` … 접미.
//     한 번의 붙이기에 포함된 노드들은 같은 원본 이름을 같은 새 이름으로 매핑해
//     붙여넣은 묶음 안의 의도적 공유를 유지한다.
//     워크플로우 로드(재시작·undo 포함)에서는 건드리지 않는다 → 직접 같은 이름을
//     적어 공유하는 구성은 보존된다.
//     구분 근거: 로드는 노드를 먼저 추가하고 id 를 보존한 채 configure 하지만,
//     붙이기는 id=-1 로 추가한 뒤 configure 하고, 복제는 id 없이 configure 한 뒤
//     추가한다. → onConfigure 의 info.id 와 node.id 비교로 "복사본" 을 판별.
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
const TEXT_NODE_NAME = "BMKPersistentBridgeString";
const PROP_SAVED = "bmk_bridge_saved";
const PROP_SAVED_TEXT = "bmk_bridge_saved_text"; // { base, text, mtime } — 워크플로우에 실리는 사본
// 복사본으로 slot 이 바뀌면 원본 노드의 저장본 정보는 넘겨받지 않는다
const SAVED_PROP_OF = { [NODE_NAME]: PROP_SAVED, [TEXT_NODE_NAME]: PROP_SAVED_TEXT };
const MODE_PASSTHROUGH = "passthrough";
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

function nodeTypeOf(node) {
    return node.comfyClass ?? node.type;
}

// 워크플로우 전체(루트 + 서브그래프)에서 같은 종류의 다른 브릿지 노드가 쓰는 slot 이름 집합
function collectBridgeNodes(graph, type, out = [], depth = 0) {
    if (!graph?.nodes || depth > 8) return out;
    for (const n of graph.nodes) {
        if (nodeTypeOf(n) === type) out.push(n);
        if (n.isSubgraphNode?.() && n.subgraph) collectBridgeNodes(n.subgraph, type, out, depth + 1);
    }
    return out;
}

function takenSlots(node) {
    const type = nodeTypeOf(node);
    const root = app.rootGraph ?? node.graph?.rootGraph ?? node.graph;
    const taken = new Set();
    for (const n of collectBridgeNodes(root, type)) {
        if (n === node) continue;
        const v = String(getWidget(n, "slot")?.value ?? "").trim();
        if (v) taken.add(v);
    }
    // 루트에서 닿지 않는 그래프(분리된 서브그래프 편집 중 등)면 현재 그래프도 포함
    if (node.graph && node.graph !== root) {
        for (const n of collectBridgeNodes(node.graph, type)) {
            if (n === node) continue;
            const v = String(getWidget(n, "slot")?.value ?? "").trim();
            if (v) taken.add(v);
        }
    }
    return taken;
}

// "이름" → "이름_2", "이름_3" … (이미 _N 접미가 있으면 그 다음 번호부터)
function nextSuffixName(name, taken) {
    const m = name.match(/^(.*?)_(\d+)$/);
    let base = name;
    let n = 2;
    if (m) {
        base = m[1];
        n = parseInt(m[2], 10) + 1;
    }
    let candidate = `${base}_${n}`;
    while (taken.has(candidate)) {
        n += 1;
        candidate = `${base}_${n}`;
    }
    return candidate;
}

// 한 번의 붙이기(동기 루프) 안에서 원본 이름 → 새 이름 매핑을 공유.
// 마이크로태스크에서 비워지므로 다음 붙이기에는 영향이 없다.
let pasteRenameMap = null;
function currentPasteRenameMap() {
    if (!pasteRenameMap) {
        pasteRenameMap = new Map();
        queueMicrotask(() => {
            pasteRenameMap = null;
        });
    }
    return pasteRenameMap;
}

// 복사본(붙이기/복제)의 slot 이 같은 종류의 기존 노드와 겹치면 새 이름으로 바꾼다.
function renameSlotForCopy(node) {
    const w = getWidget(node, "slot");
    if (!w) return;
    const cur = String(w.value ?? "").trim();
    if (!cur) {
        w.value = newAutoSlot();
        return;
    }
    const type = nodeTypeOf(node);
    const batchKey = `${type}\u0000${cur}`;
    const batch = currentPasteRenameMap();
    if (batch.has(batchKey)) {
        // 같은 붙이기 묶음에서 이미 바뀐 이름 → 같은 새 이름으로 (묶음 내 공유 유지)
        w.value = batch.get(batchKey);
        if (node.properties) delete node.properties[SAVED_PROP_OF[type]];
        return;
    }
    const taken = takenSlots(node);
    if (!taken.has(cur)) return; // 겹치지 않으면 그대로 (다른 워크플로우로 옮긴 경우 등)

    const next = AUTO_SLOT_RE.test(cur) ? newAutoSlot() : nextSuffixName(cur, taken);
    batch.set(batchKey, next);
    w.value = next;
    // 저장본 정보는 원본 노드의 것이므로 넘겨받지 않는다
    if (node.properties) delete node.properties[SAVED_PROP_OF[type]];
    console.log(`[BMK PersistentBridge] #${node.id} slot 중복(${cur}) → ${next}`);
}

// configure 에 넘어온 직렬화 정보로 "복사본" 인지 판별.
//   로드: info.id === node.id (id 보존)   붙이기: info.id === -1   복제: info.id 없음
function isCopyConfigure(node, info) {
    if (!info || typeof info !== "object") return false;
    if (info.id == null || info.id === -1) return true;
    return String(info.id) !== String(node.id);
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

// ─── String 버전 ────────────────────────────────────────────────
//
//  1) 저장본 미리보기(읽기 전용 DOM 위젯, 직렬화 안 함). 로드·slot 변경 시 서버 파일
//     (/view → input/bmk_bridge/<slot>.txt)을 직접 읽어 표시하고, 파일이 없으면
//     node.properties 의 사본(마지막 실행 결과, 워크플로우와 함께 저장됨)을 "사본"으로 표시.
//     로드 때 읽은 내용은 properties 에 쓰지 않는다(열기만 해도 수정됨 표시가 뜨지 않게).
//  2) "저장본 → 편집칸" 버튼: 서버 파일(없으면 사본)을 custom_text 로 복사하고 mode=custom.
//     custom_text 에 다른 내용이 있으면 덮어쓰기 전에 확인한다.
//  3) custom_text 가 바뀌면 mode 를 custom 으로 자동 전환. 프론트 1.53 의 DOM 위젯은 사용자
//     입력뿐 아니라 코드 대입(widget.value = …)에서도 callback 을 부른다. 복원(configure:
//     로드·undo·붙이기) 중 대입은 _bmkConfiguring 으로 거르고, 그 밖의 대입(버튼, 서브그래프
//     승격 위젯 편집, Vue 노드 모드 입력)은 그 텍스트를 쓰겠다는 의도로 보고 전환한다.
//  4) 쓰이지 않는 쪽을 흐리게 표시: custom 이면 미리보기, 아니면 custom_text(포커스 중엔 선명).
//  5) 탐색 모드 토글: 결과를 여러 번 뽑아 보는 동안 passthrough 를 유지한다. 켜져 있으면
//     custom_text 입력·"저장본 → 편집칸" 복사가 mode 를 바꾸지 않는다(편집칸 = 후보 메모장).
//     켜면 mode 를 passthrough 로 맞추고, 사용자가 mode 를 직접 바꾸면 꺼진다
//     (켜짐 ⇔ passthrough 고정). 상태는 properties 에만 둔다 → 프롬프트·캐시와 무관.
//  6) 미리보기 하단 손잡이를 끌어 미리보기 : custom_text 높이 비율 조절(더블클릭 = 50%).
//     프론트는 DOM 위젯에 최소 높이를 먼저 주고 남는 공간을 최대 높이까지 균등 분배하므로,
//     두 위젯의 최대 높이를 비율대로 주면 정확히 그 비율로 나뉜다. 비율은 properties 에 저장.
//     (Vue 노드 모드는 이 배치 경로를 쓰지 않아 비율이 적용되지 않는다)
//  위젯 순서는 mode, 탐색 모드, slot, 미리보기, 버튼, custom_text. 추가 위젯은 모두 직렬화되지
//  않으므로 widgets_values 의 순서(mode, slot, custom_text)는 그대로다.

const TEXT_PREVIEW_WIDGET = "bmk_saved_text";
const TEXT_COPY_WIDGET = "bmk_copy_saved";
const TEXT_EXPLORE_WIDGET = "bmk_explore";
const PROP_EXPLORE = "bmk_bridge_explore";
const PROP_SPLIT = "bmk_bridge_split"; // 미리보기 / (미리보기 + custom_text) 높이 비율
const SPLIT_DEFAULT = 0.5;
const PREVIEW_MIN_HEIGHT = 48;
const EDITOR_MIN_HEIGHT = 50; // 코어 멀티라인 위젯의 computeLayoutSize 기본 최소 높이
const TEXT_TITLE = "BMK Persistent Bridge (String)";
const TEXT_SLOT_REFRESH_MS = 300;

const TEXT_STYLE = `
.bmk-pbt-root{height:100%;display:flex;flex-direction:column;box-sizing:border-box;background:var(--comfy-input-bg,#222);color:var(--input-text,#ddd);border:1px solid var(--border-color,#4e4e4e);border-radius:6px;overflow:hidden}
.bmk-pbt-root.dim{opacity:.5}
.bmk-pbt-head{flex:0 0 auto;display:flex;gap:8px;justify-content:space-between;padding:2px 6px;border-bottom:1px solid var(--border-color,#4e4e4e);color:var(--descrip-text,#999);font-size:11px;white-space:nowrap;user-select:none}
.bmk-pbt-head span{overflow:hidden;text-overflow:ellipsis}
.bmk-pbt-head .copy{color:#e0a83a}
.bmk-pbt-body{flex:1 1 auto;min-height:0;overflow:auto;padding:3px 4px;font-size:var(--comfy-textarea-font-size,10px);white-space:pre-wrap;overflow-wrap:break-word;user-select:text;cursor:text}
.bmk-pbt-body.empty{color:var(--descrip-text,#999);font-style:italic}
.bmk-pbt-grip{flex:0 0 8px;display:flex;align-items:center;justify-content:center;border-top:1px solid var(--border-color,#4e4e4e);cursor:row-resize;touch-action:none}
.bmk-pbt-grip::after{content:"";width:32px;height:2px;border-radius:1px;background:var(--descrip-text,#999);opacity:.45}
.bmk-pbt-grip:hover::after,.bmk-pbt-grip.dragging::after{opacity:1}
.comfy-multiline-input.bmk-pbt-idle:not(:focus){opacity:.55}
`;

const textViews = new WeakMap(); // node → 미리보기 DOM + 마지막으로 읽은 서버 파일 상태

function injectTextStyle() {
    if (document.getElementById("bmk-pbt-style")) return;
    const el = document.createElement("style");
    el.id = "bmk-pbt-style";
    el.textContent = TEXT_STYLE;
    document.head.appendChild(el);
}

// 파이썬 _sanitize / _resolve_base 와 같은 규칙 (\w = 유니코드 문자·숫자·_)
function sanitizeSlot(value) {
    return String(value).replace(/[^\p{L}\p{N}_-]+/gu, "_").replace(/^_+|_+$/g, "") || "x";
}

function textBaseOf(node) {
    const slot = String(getWidget(node, "slot")?.value ?? "").trim();
    return slot ? sanitizeSlot(slot) : `node_${sanitizeSlot(executionIdOf(node))}`;
}

function formatMtime(httpDate) {
    const d = httpDate ? new Date(httpDate) : null;
    if (!d || isNaN(d)) return "";
    const p = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

// → { base, text, mtime } | { base, missing: true }
async function fetchSavedTextFile(base) {
    const q = new URLSearchParams({ filename: `${base}.txt`, subfolder: "bmk_bridge", type: "input", t: String(Date.now()) });
    const res = await api.fetchApi(`/view?${q}`);
    if (res.status === 404) return { base, missing: true };
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return { base, text: await res.text(), mtime: formatMtime(res.headers.get("Last-Modified")) };
}

function createTextView() {
    const root = document.createElement("div");
    root.className = "bmk-pbt-root";
    const head = document.createElement("div");
    head.className = "bmk-pbt-head";
    const name = document.createElement("span");
    const status = document.createElement("span");
    head.append(name, status);
    const body = document.createElement("div");
    body.className = "bmk-pbt-body";
    const grip = document.createElement("div");
    grip.className = "bmk-pbt-grip";
    grip.title = "드래그: 미리보기 / custom_text 높이 비율 · 더블클릭: 50%";
    root.append(head, body, grip);

    // 본문 위 휠 = 텍스트 스크롤. 스크롤할 게 없거나 Ctrl 이면 캔버스 줌으로 넘긴다.
    root.addEventListener(
        "wheel",
        (e) => {
            if (e.ctrlKey || body.scrollHeight <= body.clientHeight) {
                e.preventDefault();
                e.stopPropagation();
                app.canvas?.processMouseWheel?.(e);
                return;
            }
            e.stopPropagation();
        },
        { passive: false }
    );
    return { root, name, status, body, grip, file: undefined, req: 0, slotTimer: 0 };
}

// 표시·복사에 쓸 저장본: 서버 파일 → 없으면 properties 사본
function savedTextOf(node) {
    const base = textBaseOf(node);
    const view = textViews.get(node);
    const file = view?.file?.base === base ? view.file : null;
    if (file && !file.missing) return { text: file.text, mtime: file.mtime, source: "file" };
    const copy = node.properties?.[PROP_SAVED_TEXT];
    if (copy?.base === base && typeof copy.text === "string") {
        return { text: copy.text, mtime: copy.mtime, source: file?.missing ? "copy-missing" : "copy" };
    }
    return null;
}

function renderTextView(node) {
    const view = textViews.get(node);
    if (!view) return;
    const saved = savedTextOf(node);
    const custom = getWidget(node, "mode")?.value === MODE_CUSTOM;

    let status = "저장본 없음";
    if (saved) {
        const parts = [`${Array.from(saved.text).length}자`];
        if (saved.mtime) parts.push(saved.mtime);
        if (saved.source === "copy-missing") parts.unshift("서버에 파일 없음 · 워크플로우 사본");
        else if (saved.source === "copy") parts.unshift("워크플로우 사본");
        status = parts.join(" · ");
    }
    view.name.textContent = `${textBaseOf(node)}.txt`;
    view.status.textContent = status;
    view.status.className = saved && saved.source !== "file" ? "copy" : "";
    view.body.textContent = saved
        ? saved.text || "(빈 텍스트)"
        : "passthrough 로 상위를 한 번 실행하면 저장본이 여기에 표시됩니다.";
    view.body.classList.toggle("empty", !saved?.text);
    view.root.classList.toggle("dim", custom);

    getWidget(node, "custom_text")?.element?.classList.toggle("bmk-pbt-idle", !custom);
    const exploreW = getWidget(node, TEXT_EXPLORE_WIDGET);
    if (exploreW) exploreW.value = isExploring(node);
    node.graph?.setDirtyCanvas(true, false);
}

// ─── 탐색 모드 ───

function isExploring(node) {
    return !!node.properties?.[PROP_EXPLORE] && getWidget(node, "mode")?.value === MODE_PASSTHROUGH;
}

function setExplore(node, on) {
    if (on) {
        node.properties[PROP_EXPLORE] = true;
        const modeW = getWidget(node, "mode");
        if (modeW && modeW.value !== MODE_PASSTHROUGH) modeW.value = MODE_PASSTHROUGH;
    } else {
        delete node.properties[PROP_EXPLORE];
    }
    renderTextView(node);
}

// ─── 미리보기 / custom_text 높이 비율 ───

function splitOf(node) {
    const r = Number(node.properties?.[PROP_SPLIT]);
    return r > 0 && r < 1 ? r : SPLIT_DEFAULT;
}

// 미리보기 + custom_text 가 나눠 쓰는 높이. 고정 위젯·여백 몫은 직전 배치 결과(y, computedHeight)로
// 구하고 본문 높이는 현재 값을 써서, 노드 크기를 바꾸는 중에도 한 프레임 늦지 않게 한다.
// 프론트의 _arrangeWidgets 와 같은 기준: computeSize 가 없고 computeLayoutSize 가 있으면 가변 위젯.
function sharedTextHeight(node) {
    const layout = node.getLayoutWidgets?.() ?? node.widgets ?? [];
    if (!layout.length || layout[0].y == null) return null;
    let fixed = layout[0].y;
    for (const w of layout) {
        if (!w.computeSize && w.computeLayoutSize) continue;
        if (w.computedHeight == null) return null;
        fixed += w.computedHeight;
    }
    const h = node.bodyHeight - fixed;
    return h > 0 ? h : null;
}

function previewMaxHeight(node) {
    const h = sharedTextHeight(node);
    if (h == null) return undefined;
    return Math.max(PREVIEW_MIN_HEIGHT, Math.min(Math.round(h * splitOf(node)), h - EDITOR_MIN_HEIGHT));
}

function editorMaxHeight(node) {
    const h = sharedTextHeight(node);
    const top = previewMaxHeight(node);
    return h == null || top == null ? undefined : Math.max(EDITOR_MIN_HEIGHT, h - top);
}

function saveWorkflowState() {
    app.extensionManager?.workflow?.activeWorkflow?.changeTracker?.checkState?.();
}

function attachSplitter(node, view) {
    const grip = view.grip;
    grip.addEventListener("pointerdown", (e) => {
        if (e.button !== 0) return;
        const h = sharedTextHeight(node);
        if (!h) return;
        e.preventDefault();
        e.stopPropagation();
        grip.classList.add("dragging");
        const startY = e.clientY;
        const startTop = previewMaxHeight(node);
        const scale = app.canvas?.ds?.scale || 1; // DOM 위젯은 캔버스 배율로 그려진다

        const minRatio = PREVIEW_MIN_HEIGHT / h;
        const maxRatio = Math.max(minRatio, (h - EDITOR_MIN_HEIGHT) / h);
        const onMove = (ev) => {
            const ratio = (startTop + (ev.clientY - startY) / scale) / h;
            node.properties[PROP_SPLIT] = Math.round(Math.min(maxRatio, Math.max(minRatio, ratio)) * 1000) / 1000;
            const top = Math.round(splitOf(node) * 100);
            view.status.textContent = `미리보기 ${top}% · custom_text ${100 - top}%`;
            node.setDirtyCanvas(true, true);
        };
        // 손잡이 밖으로 끌고 나가도 이어지도록 window 에서 받는다
        const onEnd = () => {
            window.removeEventListener("pointermove", onMove);
            window.removeEventListener("pointerup", onEnd);
            window.removeEventListener("pointercancel", onEnd);
            grip.classList.remove("dragging");
            renderTextView(node);
            saveWorkflowState();
        };
        window.addEventListener("pointermove", onMove);
        window.addEventListener("pointerup", onEnd);
        window.addEventListener("pointercancel", onEnd);
    });
    grip.addEventListener("dblclick", (e) => {
        e.stopPropagation();
        delete node.properties[PROP_SPLIT];
        node.setDirtyCanvas(true, true);
        saveWorkflowState();
    });
}

async function refreshTextFromServer(node) {
    const view = textViews.get(node);
    if (!view) return;
    const req = ++view.req;
    let file;
    try {
        file = await fetchSavedTextFile(textBaseOf(node));
    } catch (e) {
        console.warn("[BMK PersistentBridge] 텍스트 저장본 읽기 실패:", e);
        return;
    }
    if (req !== view.req) return; // 그 사이 slot 이 또 바뀜
    view.file = file;
    renderTextView(node);
}

function notifyText(severity, detail) {
    const toast = app.extensionManager?.toast;
    if (toast?.add) toast.add({ severity, summary: TEXT_TITLE, detail, life: 4000 });
    else console.warn(`[BMK PersistentBridge] ${detail}`);
}

async function confirmOverwrite() {
    const message = "custom_text 에 있는 내용을 저장본으로 덮어씁니다.";
    const dialog = app.extensionManager?.dialog;
    if (dialog?.confirm) return (await dialog.confirm({ title: TEXT_TITLE, message })) === true;
    return window.confirm(message);
}

function switchTextToCustom(node, reason) {
    const modeW = getWidget(node, "mode");
    if (modeW && modeW.value !== MODE_CUSTOM) {
        modeW.value = MODE_CUSTOM;
        console.log(`[BMK PersistentBridge] #${node.id} mode → custom (${reason})`);
    }
    renderTextView(node);
}

async function copySavedToCustom(node) {
    await refreshTextFromServer(node);
    const saved = savedTextOf(node);
    if (!saved) {
        notifyText("warn", "복사할 저장본이 없습니다. passthrough 로 상위를 한 번 실행하세요.");
        return;
    }
    const textW = getWidget(node, "custom_text");
    if (!textW) return;
    const cur = String(textW.value ?? "");
    if (cur.trim() && cur !== saved.text && !(await confirmOverwrite())) return;
    textW.value = saved.text;
    if (isExploring(node)) renderTextView(node); // 후보만 옮겨 두고 passthrough 유지
    else switchTextToCustom(node, "저장본 → 편집칸");
    // 클릭 직후의 변경 감지는 이미 지나갔으므로(파일을 읽느라 비동기) undo 기록을 직접 남긴다
    saveWorkflowState();
}

function chainCallback(widget, fn) {
    if (!widget) return;
    const orig = widget.callback;
    widget.callback = function (...args) {
        const r = orig?.apply(this, args);
        fn(...args);
        return r;
    };
}

function setupTextNode(node) {
    injectTextStyle();
    node.properties ??= {};
    ensureAutoSlot(node);

    const view = createTextView();
    textViews.set(node, view);
    const preview = node.addDOMWidget(TEXT_PREVIEW_WIDGET, "BMK_SAVED_TEXT", view.root, {
        serialize: false,
        hideOnZoom: true,
        getMinHeight: () => PREVIEW_MIN_HEIGHT,
        getMaxHeight: () => previewMaxHeight(node),
        getValue: () => "",
        setValue: () => {},
    });
    preview.serialize = false;
    attachSplitter(node, view);

    const copyBtn = node.addWidget("button", TEXT_COPY_WIDGET, null, () => copySavedToCustom(node), {
        serialize: false,
    });
    copyBtn.label = "⇩ 저장본 → 편집칸";
    copyBtn.serialize = false;

    const exploreW = node.addWidget("toggle", TEXT_EXPLORE_WIDGET, false, (v) => setExplore(node, !!v), {
        serialize: false,
        on: "켜짐",
        off: "꺼짐",
    });
    exploreW.label = "탐색 모드 · passthrough 유지";
    exploreW.serialize = false;

    const textW = getWidget(node, "custom_text");
    if (textW?.options) textW.options.getMaxHeight = () => editorMaxHeight(node);

    const ordered = ["mode", TEXT_EXPLORE_WIDGET, "slot", TEXT_PREVIEW_WIDGET, TEXT_COPY_WIDGET, "custom_text"]
        .map((name) => getWidget(node, name))
        .filter(Boolean);
    node.widgets = [...ordered, ...node.widgets.filter((w) => !ordered.includes(w))];

    chainCallback(getWidget(node, "mode"), (value) => {
        // 사용자가 mode 를 직접 바꾸면 탐색 모드 해제 (켜짐 ⇔ passthrough 고정)
        if (value !== MODE_PASSTHROUGH && node.properties[PROP_EXPLORE]) setExplore(node, false);
        else renderTextView(node);
    });
    chainCallback(getWidget(node, "slot"), () => {
        renderTextView(node);
        clearTimeout(view.slotTimer);
        view.slotTimer = setTimeout(() => refreshTextFromServer(node), TEXT_SLOT_REFRESH_MS);
    });
    chainCallback(textW, () => {
        if (!node._bmkConfiguring && !isExploring(node)) switchTextToCustom(node, "custom_text 입력");
    });

    const sz = node.computeSize?.() ?? node.size;
    node.setSize([Math.max(sz[0], node.size[0]), Math.max(sz[1], node.size[1], 360)]);
    renderTextView(node);
}

// ─── 확장 등록 ─────────────────────────────────────────────────

// 두 노드 공통: 복원 중 플래그, 복제(clone) 경로의 slot 중복 검사
function installSlotHooks(nodeType) {
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

    // 복제(clone) 경로: configure 뒤에 그래프에 추가되므로 여기서 중복 검사
    const onAdded = nodeType.prototype.onAdded;
    nodeType.prototype.onAdded = function () {
        const r = onAdded?.apply(this, arguments);
        if (this._bmkPendingCopyCheck) {
            this._bmkPendingCopyCheck = false;
            renameSlotForCopy(this);
        }
        return r;
    };
}

// 붙이기/복제로 들어온 복사본이면 slot 중복을 막는다 (로드는 건드리지 않음)
function checkCopyOnConfigure(node, info) {
    if (!isCopyConfigure(node, info)) return;
    if (node.graph) renameSlotForCopy(node);
    else node._bmkPendingCopyCheck = true; // 복제: 아직 그래프에 없음 → onAdded 에서
}

function registerImageBridge(nodeType) {
    installSlotHooks(nodeType);

    const onNodeCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
        const r = onNodeCreated?.apply(this, arguments);
        setupNode(this);
        return r;
    };

    const onConfigure = nodeType.prototype.onConfigure;
    nodeType.prototype.onConfigure = function (info) {
        const r = onConfigure?.apply(this, arguments);
        checkCopyOnConfigure(this, info);
        // 저장본 로드로 위젯값이 복원된 뒤 기준값을 다시 잡고 프리뷰 적용
        const imgW = getWidget(this, "image");
        this._bmkLastImage = imgW?.value;
        ensureComboValue(imgW, imgW?.value); // 누락 미디어 검사 대비
        schedulePreview(this);
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
}

function registerTextBridge(nodeType) {
    installSlotHooks(nodeType);

    const onNodeCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
        const r = onNodeCreated?.apply(this, arguments);
        setupTextNode(this);
        return r;
    };

    const onConfigure = nodeType.prototype.onConfigure;
    nodeType.prototype.onConfigure = function (info) {
        const r = onConfigure?.apply(this, arguments);
        checkCopyOnConfigure(this, info);
        if (this.properties?.[PROP_EXPLORE] && !isExploring(this)) delete this.properties[PROP_EXPLORE];
        renderTextView(this);
        // 복제는 onAdded 에서 slot 이 바뀌므로 현재 작업이 끝난 뒤에 서버 파일을 읽는다
        setTimeout(() => refreshTextFromServer(this), 0);
        return r;
    };

    const onExecuted = nodeType.prototype.onExecuted;
    nodeType.prototype.onExecuted = function (message) {
        const r = onExecuted?.apply(this, arguments);
        const info = message?.bmk_bridge_text?.[0];
        const view = textViews.get(this);
        if (info && view) {
            this.properties ??= {};
            if (info.saved) {
                view.file = { base: info.base, ...info.saved };
                this.properties[PROP_SAVED_TEXT] = { base: info.base, ...info.saved };
            } else {
                view.file = { base: info.base, missing: true };
            }
            renderTextView(this);
        }
        return r;
    };
}

app.registerExtension({
    name: "BMK.PersistentBridge",

    beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name === NODE_NAME) registerImageBridge(nodeType);
        else if (nodeData.name === TEXT_NODE_NAME) registerTextBridge(nodeType);
    },
});
