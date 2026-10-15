// bmk_design_patch.js — BMKDesignPatchRun / BMKDesignPatchSpecDraft / BMKDesignPatchReview 짝 JS 확장 (M2·M3)
//
// 얇은 확장이다. 과금 판단(승인 해시 대조·원장·사전 승인)은 전부 서버의 Run·Spec Draft 노드가 하고, 여기서는
// 버튼과 이벤트 중계만 한다. 과금이 걸린 큐잉은 이 탭의 app.queuePrompt 로만 한다(인증 토큰이 이 경로로만 붙는다).
//
// 유료 노드(Run·Spec Draft)끼리: 한 큐에는 승인 해시 하나만 나간다. 승인 큐·자동 이어가기·보드 큐는 큐에 넣기 전에
// 다른 유료 노드의 approve 를 비운다(clearOtherApproves) — Run 의 부분 실행은 조상인 Draft 도 다시 실행하므로, Draft 에
// 남은 승인이 함께 나가 그 초안 집합을 과금하지 않게.
//
// Run 노드
//  - 실행 결과 ui.bmk_dp_run[0] = {pending_hash, pending, est_usd, approved_now, root_key, backend, continuation_hash,
//    draft_pending, draft_style, draft_unwritten, draft_held, draft_held_style, draft_mock} 로 "승인 후 실행 (N건 ≈ $X)"
//    버튼 라벨을 갱신한다.
//  - 승인 버튼: approve 위젯 = pending_hash → 이 노드만 부분 실행 queuePrompt(0, 1, {queueNodeIds}).
//    승인은 그 프롬프트 하나에만 쓴다: 제출 직후(widget.afterQueued) 또는 제출 실패 때 approve 를 비운다 — 큐에서 지우거나
//    취소해 쓰이지 않은 승인이 위젯에 남아 나중의 큐(보드 재굴림 등)가 그 집합 전체를 과금하지 않게.
//    드라이런 이후 대기 목록이 바뀌었으면 해시가 맞지 않아 서버가 과금 없이 드라이런만 다시 한다.
//  - Drain: POST /bmk/design_patch/drain {root} — 새 발사를 멈추고 진행 중인 호출은 끝까지 받는다
//    (Cancel 은 진행 중 호출의 비용을 내고 결과를 버린다). Drain 을 누른 실행은 wave 로 끝나도 자동으로 이어 가지 않는다.
//  - 자동 이어가기(property bmk_dp_auto_continue): 이 탭이 큐에 넣은 실행에서 bmk.dp.wave(remaining>0)를 받으면
//    그 프롬프트가 성공으로 끝난 뒤 이어서 큐에 넣는다(큐마다 새 토큰). 새 대기 해시가 이번 wave 의 남은 승인분 해시
//    (continuation_hash)와 같을 때만 그 해시를 승인하고, 다르면(승인하지 않은 호출이 섞임) 멈추고 알린다.
//    승인할 것이 없으면 approve 없이 다시 넣어 사전 승인·다운로드 복구만 이어 간다(그 실행이 또 wave 면 다시 무장).
//  - 확인 창: 핸들의 Spec Draft 초안 대기(entry.draft_pending > 0)가 있으면 경고 한 줄을 덧붙인다(크롭과 화풍을 따로 —
//    draft_pending 은 화풍 단위 draft_style 0/1 을 포함). Draft 의 only 로 보류된 대기(draft_held, 같은 규칙),
//    받은 초안을 파일에 못 쓴 수(draft_unwritten), mock 초안이 남은 스펙 수(draft_mock — 유료 Run 일 때만)도 경고.
//
// Spec Draft 노드 (M3)
//  - ui.bmk_dp_draft[0] = {pending_hash, pending, est_credits, est_seconds, orphan_retries, backend, root_key, …} 로
//    "VLM 초안 승인 (N건 ≈ X 크레딧)" / "(로컬 N건 ≈ M분)" / "[MOCK]" 버튼 라벨을 갱신한다. 승인 버튼 하나뿐(Auto·Drain 없음).
//  - 승인: Run 과 같은 1회용 규칙(approve = pending_hash → Draft 만 부분 실행, 제출 직후 비움). 확인 창: openai = 크레딧
//    (orphan 재시도 포함 수), local_qwen = ComfyUI 모델을 VRAM 에서 내림, mock = 확인 창 없음.
//  - adopt(채택할 크롭)도 1회용 명령: 이 노드가 실린 큐가 제출되면(afterQueued — 전체 큐·부분 실행·승인 버튼) 비운다.
//    남겨 두면 그 뒤 큐마다 새로 받은 제안까지 검토 없이 채택된다. 꺼진(mute/bypass) 노드·제출 실패·이 노드가 빠진 부분 실행
//    (api.queuePrompt 를 감싸 대상 + 조상을 셈)이면 쓰이지 않았으니 남긴다.
//    서버가 쓴 _layer_targets 는 스펙에 그대로 남는다.
//
// Review 노드
//  - ui.bmk_dp_board[0].root_key 를 properties 에 기억한다(재로드 뒤에도 Open Board 가 동작).
//  - Open Board: window.open("/bmk/design_patch/board/?root=<key>", "bmk_dp_board_<key>").
//  - 보드 창의 postMessage {type:"bmk-dp-queue", rootKey}(같은 origin 만) → 그 프로젝트의 Review 노드만 부분 실행.
//    보드 재굴림은 서버에서 사전 승인된 호출이라 Run 이 그 호출만 과금한다 — 큐에 넣기 전에 모든 유료 노드(Run·Draft)의
//    approve 를 비운다.
//    연타는 0.8초 단위로 한 번에 묶는다.
//
// 1.53 클래식 모드 주의: 위젯은 addWidget("button") 만, 위젯 배열 맨 뒤에 serialize=false 로 붙인다
// (widgets_values 위치 호환). 노드별 상태는 WeakMap 에 둔다. 위젯 객체에 얹는 것은 프론트가 부르는 afterQueued 훅뿐이다
// — approve 는 승인 한 번 동안만, Draft 의 adopt 는 늘. onExecuted 의 ui 값은 한 단계 평탄화된 리스트로 온다
// (message.bmk_dp_run = [{…}]).

import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const RUN_NODE = "BMKDesignPatchRun";
const DRAFT_NODE = "BMKDesignPatchSpecDraft";
const REVIEW_NODE = "BMKDesignPatchReview";
const PAID_NODES = [RUN_NODE, DRAFT_NODE];
const DRAFT_UI = "bmk_dp_draft"; // vlm.UI_KEY
const DRAFT_LOCAL = "vlm_local_qwen"; // vlm.BACKEND_NAMES
const DRAFT_MOCK = "vlm_mock";
const PROP_AUTO = "bmk_dp_auto_continue";
const PROP_ROOT = "bmk_dp_root_key";
const W_APPROVE = "bmk_dp_approve";
const W_AUTO = "bmk_dp_auto";
const W_DRAIN = "bmk_dp_drain";
const W_BOARD = "bmk_dp_open_board";
const TITLE = "BMK Design Patch";
const BOARD_PATH = "/bmk/design_patch/board/";
const DRAIN_PATH = "/bmk/design_patch/drain";
const ROOT_KEY_RE = /^[0-9a-f]{12}$/; // store.root_key = sha1(root)[:12]
const BOARD_QUEUE_DELAY_MS = 800;
const MODE_NEVER = 2;
const MODE_BYPASS = 4;

const states = new WeakMap(); // Run·Draft 노드 → { entry, cont, prevRemaining, stalls, busy, drainedPrompt }
const armed = new Set(); // 자동 이어가기 대기 중인 Run 노드
const contQueued = new Set(); // 자동 이어가기로 큐에 넣었고 그 Run 이 아직 실행되지 않은 노드(Drain 안내용)
const boardTimers = new Map(); // rootKey → 보드 큐 요청 묶음 타이머
// 이 탭이 큐에 넣어 지금 실행 중인 프롬프트. execution_start/success 는 큐에 넣은 탭에만 온다
// (bmk.dp.wave 는 모든 탭에 방송되므로 이 값으로 남의 실행을 걸러 낸다).
let runningPromptId = null;

function stateOf(node) {
    let s = states.get(node);
    if (!s) states.set(node, (s = { entry: null, cont: null, prevRemaining: null, stalls: 0, busy: false, drainedPrompt: null }));
    return s;
}

function getWidget(node, name) {
    return node.widgets?.find((w) => w.name === name) ?? null;
}

function classOf(node) {
    return node.comfyClass ?? node.type;
}

function isOff(node) {
    return node.mode === MODE_NEVER || node.mode === MODE_BYPASS;
}

function notify(severity, detail, life = 6000) {
    const toast = app.extensionManager?.toast;
    if (toast?.add) toast.add({ severity, summary: TITLE, detail, life });
    else console.warn(`[BMK DesignPatch] ${detail}`);
}

async function confirmDialog(message) {
    const dialog = app.extensionManager?.dialog;
    if (dialog?.confirm) return (await dialog.confirm({ title: TITLE, message })) === true;
    return window.confirm(message);
}

function checkState() {
    app.extensionManager?.workflow?.activeWorkflow?.changeTracker?.checkState?.();
}

// ui 값은 보통 [{…}]. 평탄화 전 모양(message.ui.key)이나 리스트 없이 온 객체도 받는다.
function uiEntry(message, key) {
    let v = message?.[key] ?? message?.ui?.[key];
    for (let i = 0; i < 3 && Array.isArray(v); i++) v = v[0];
    return v && typeof v === "object" ? v : null;
}

// 개수 필드는 숫자 또는 id 목록
function countOf(v) {
    if (Array.isArray(v)) return v.length;
    const n = Number(v);
    return Number.isFinite(n) ? n : 0;
}

// est_usd = 숫자 | [lo, hi] | {lo, hi}
function formatUsd(v) {
    const [lo, hi] = (Array.isArray(v) ? v : [v?.lo ?? v, v?.hi ?? v]).map(parseFloat);
    if (!Number.isFinite(lo)) return "";
    if (!Number.isFinite(hi) || hi - lo < 0.005) return `$${lo.toFixed(2)}`;
    return `$${lo.toFixed(2)}–${hi.toFixed(2)}`;
}

// 루트 + 서브그래프 노드를 실행 ID("호스트:…:노드")와 함께 순회. fn 이 true 를 돌려주면 멈춘다.
function walkNodes(fn, graph = app.rootGraph ?? app.graph, prefix = "", depth = 0) {
    if (!graph?.nodes || depth > 8) return false;
    for (const n of graph.nodes) {
        const execId = prefix ? `${prefix}:${n.id}` : String(n.id);
        if (fn(n, execId)) return true;
        if (n.isSubgraphNode?.() && n.subgraph && walkNodes(fn, n.subgraph, execId, depth + 1)) return true;
    }
    return false;
}

function execIdOf(node) {
    let id = null;
    walkNodes((n, execId) => n === node && ((id = execId), true));
    return id;
}

// 같은 서브그래프 정의를 쓰는 인스턴스가 여럿이면 노드 하나가 실행 ID 여럿("10:3", "20:3")을 갖는다
function execIdsOf(node) {
    const ids = [];
    walkNodes((n, execId) => {
        if (n === node) ids.push(execId);
    });
    return ids;
}

function nodesOf(cls) {
    const out = [];
    walkNodes((n) => {
        if (classOf(n) === cls) out.push(n);
    });
    return out;
}

function rootKeyOf(node) {
    const k = node.properties?.[PROP_ROOT];
    return typeof k === "string" && ROOT_KEY_RE.test(k) ? k : null;
}

function setRootKey(node, key) {
    if (typeof key !== "string" || !ROOT_KEY_RE.test(key) || rootKeyOf(node) === key) return;
    node.properties ??= {};
    node.properties[PROP_ROOT] = key;
}

// 이 노드가 아직 프로젝트를 모를 때: 그래프의 Design Patch 노드들이 아는 프로젝트가 하나뿐이면 그것
function soleRootKey() {
    const keys = new Set();
    walkNodes((n) => {
        const k = [RUN_NODE, DRAFT_NODE, REVIEW_NODE].includes(classOf(n)) && rootKeyOf(n);
        if (k) keys.add(k);
    });
    return keys.size === 1 ? [...keys][0] : null;
}

async function queueOnly(nodes, what) {
    const ids = nodes.map(execIdOf).filter(Boolean);
    if (!ids.length) {
        notify("error", `${what}: 지금 열린 워크플로에서 노드를 찾지 못해 큐에 넣지 못했습니다.`);
        return false;
    }
    try {
        await app.queuePrompt(0, 1, { queueNodeIds: ids });
        return true;
    } catch (e) {
        notify("error", `${what} 큐 실패: ${e?.message ?? e}`);
        return false;
    }
}

// approve 를 비운다(바뀌었으면 true). 자동 이어가기의 무승인 큐·보드 큐는 사전 승인·무료 호출만 과금해야 한다.
function clearApprove(node) {
    const w = getWidget(node, "approve");
    if (!w?.value) return false;
    w.value = "";
    node.setDirtyCanvas?.(true, true);
    return true;
}

// except 를 뺀 모든 유료 노드(Run·Draft)의 approve 를 비운다. 한 큐에 승인 해시 하나 — Run 의 부분 실행은 조상인 Draft 도
// 다시 실행하고, 보드 큐도 Review 의 조상(Run·Draft)을 실행하므로 남은 승인이 함께 나가 과금하지 않게
function clearOtherApproves(except = null) {
    let cleared = false;
    for (const cls of PAID_NODES) for (const n of nodesOf(cls)) if (n !== except) cleared = clearApprove(n) || cleared;
    if (cleared) checkState();
    return cleared;
}

// 승인 한 번 = 프롬프트 하나. approve = hash 로 큐에 넣고, 그 프롬프트가 제출되면(afterQueued — 다른 큐 항목이 프롬프트를
// 만들기 전) 또는 제출이 실패하면 비운다. 쓰이지 않은 승인이 위젯에 남아 나중의 큐가 그 집합을 과금하지 않게.
async function queueApproved(node, hash, what) {
    if (app.processingQueue) {
        // 다른 제출을 처리하는 중이면 queuePrompt 가 바로 돌아오고 이 항목은 나중에 나간다 → 비울 때를 알 수 없어 거절
        notify("warn", `${what}: 다른 큐 제출을 처리하는 중입니다. 잠시 뒤 승인 버튼을 다시 누르세요.`);
        return false;
    }
    clearOtherApproves(node);
    const w = getWidget(node, "approve");
    const clear = () => {
        if (w.afterQueued === clear) delete w.afterQueued;
        if (w.value === hash) w.value = "";
        checkState();
        node.setDirtyCanvas?.(true, true);
    };
    w.value = hash;
    w.afterQueued = clear;
    try {
        return await queueOnly([node], what);
    } finally {
        clear();
    }
}

// 탭 전환·새로고침으로 노드가 다시 만들어지면 드라이런 보고(승인 해시)를 잃는다. 그때 승인 버튼은 이 노드만 드라이런으로
// 다시 큐에 넣어 보고를 받는다. 모든 유료 노드의 approve 를 먼저 비우므로 이 큐는 과금하지 않는다.
async function requeueDryRun(node, what) {
    if (app.processingQueue) {
        notify("warn", `${what}: 다른 큐 제출을 처리하는 중입니다. 잠시 뒤 다시 누르세요.`);
        return;
    }
    clearOtherApproves();
    if (await queueOnly([node], what)) {
        notify("info", `${what}: 드라이런 보고를 받는 중입니다(호출·과금 없음). 버튼 라벨이 바뀌면 다시 누르세요.`);
    }
}

function addButton(node, name, label, onClick) {
    const w = node.addWidget("button", name, null, onClick, { serialize: false });
    w.label = label;
    w.serialize = false;
    return w;
}

function fitHeight(node) {
    const sz = node.computeSize?.() ?? node.size;
    node.setSize?.([Math.max(sz[0], node.size[0]), Math.max(sz[1], node.size[1])]);
}

// ─── Run 노드 ──────────────────────────────────────────────────

function refreshRunLabels(node) {
    const e = stateOf(node).entry;
    const approve = getWidget(node, W_APPROVE);
    if (approve) {
        const n = countOf(e?.pending);
        const usd = formatUsd(e?.est_usd);
        if (!e) approve.label = "승인 후 실행 (누르면 드라이런부터)";
        else if (n <= 0 || !e.pending_hash) approve.label = "승인할 호출 없음";
        else approve.label = `승인 후 실행 (${n}건${usd ? ` ≈ ${usd}` : ""})${e.backend === "mock" ? " [MOCK]" : ""}`;
    }
    const auto = getWidget(node, W_AUTO);
    if (auto) auto.label = `자동 이어가기 (wave): ${node.properties?.[PROP_AUTO] ? "켜짐" : "꺼짐"}`;
    node.setDirtyCanvas?.(true, true);
}

// 자동 이어가기 연쇄를 끝낸다. 다음 연쇄의 진행 감시(남은 수가 줄어드는지)도 처음부터 센다.
function disarm(node) {
    const s = stateOf(node);
    s.cont = null;
    s.prevRemaining = null;
    s.stalls = 0;
    armed.delete(node);
}

function stopChain(node, why) {
    disarm(node);
    notify("warn", `자동 이어가기 중단: ${why}`);
}

function approveBlocker(node) {
    const w = getWidget(node, "approve");
    if (!w) return "approve 위젯을 찾지 못했습니다.";
    const slot = node.findInputSlot?.("approve") ?? -1;
    if (slot >= 0 && node.isInputConnected?.(slot)) return "approve 가 링크로 연결되어 있어 버튼으로 승인할 수 없습니다.";
    if (isOff(node)) return "노드가 꺼져(mute/bypass) 있습니다.";
    return null;
}

async function approveAndRun(node) {
    const s = stateOf(node);
    if (s.busy) return;
    const e = s.entry;
    if (!e) {
        await requeueDryRun(node, "Run");
        return;
    }
    const n = countOf(e.pending);
    if (n <= 0 || !e.pending_hash) {
        notify("info", "승인할 호출이 없습니다.");
        return;
    }
    const blocker = approveBlocker(node);
    if (blocker) {
        notify("error", blocker);
        return;
    }
    s.busy = true;
    try {
        if (e.backend !== "mock") {
            const usd = formatUsd(e.est_usd);
            // 서버 _draft_pending_text 와 같은 문구("22크롭 + 화풍")
            const what = (total, styleUnits) => {
                const style = Math.min(total, countOf(styleUnits));
                return [total - style > 0 ? `${total - style}크롭` : "", style > 0 ? "화풍" : ""].filter(Boolean).join(" + ");
            };
            const drafts = countOf(e.draft_pending);
            const held = countOf(e.draft_held);
            const unwritten = countOf(e.draft_unwritten);
            const mockSpecs = countOf(e.draft_mock);
            const msg =
                `GPT Image 호출 ${n}건${usd ? `(예상 ${usd})` : ""}을 승인하고 과금합니다.\n` +
                "드라이런 이후 대기 목록이 바뀌었으면 서버가 과금 없이 드라이런만 다시 합니다." +
                (drafts > 0
                    ? `\n주의: 스펙 초안 대기 ${what(drafts, e.draft_style)}(Spec Draft 미승인) — 지금 승인하면 초안 없는 프롬프트로 과금합니다.`
                    : "") +
                (held > 0
                    ? `\n주의: Spec Draft 의 only 로 보류된 초안 대기 ${what(held, e.draft_held_style)} — 지금 승인하면 초안 없는 프롬프트로 과금합니다.`
                    : "") +
                (unwritten > 0
                    ? `\n주의: 스펙 파일 ${unwritten}개에 초안·화풍 변경을 아직 못 씀(잠김) — 지금 승인하면 그 변경 없이 과금합니다.`
                    : "") +
                (mockSpecs > 0
                    ? `\n주의: 스펙 ${mockSpecs}개가 mock 초안(자리표시 문구) — 지금 승인하면 그 문구로 과금합니다.`
                    : "");
            if (!(await confirmDialog(msg))) return;
        }
        disarm(node);
        await queueApproved(node, String(e.pending_hash), "Run");
    } finally {
        s.busy = false;
    }
}

function toggleAuto(node) {
    node.properties ??= {};
    node.properties[PROP_AUTO] = !node.properties[PROP_AUTO];
    if (!node.properties[PROP_AUTO]) disarm(node);
    refreshRunLabels(node);
    checkState();
}

async function drain(node) {
    const key = rootKeyOf(node) ?? soleRootKey();
    if (!key) {
        notify("warn", "프로젝트를 아직 모릅니다. Run 또는 Review 를 한 번 실행한 뒤 다시 누르세요.");
        return;
    }
    disarm(node);
    // 지금 이 탭이 돌리는 실행은 wave 로 끝나도 이어 가지 않는다(서버가 Run 을 끝낸 직후라 drain 을 못 받은 경우까지)
    stateOf(node).drainedPrompt = runningPromptId;
    let data = null;
    try {
        const res = await api.fetchApi(DRAIN_PATH, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ root: key }),
        });
        data = await res.json().catch(() => null);
        if (!res.ok) throw new Error(data?.error ?? `HTTP ${res.status}`);
    } catch (err) {
        notify("error", `Drain 실패: ${err?.message ?? err}`);
        return;
    }
    // 실행 중인 Run 이 없을 때의 drain 은 다음 Run 이 시작하며 지운다(서버 규칙)
    if (data?.running === false) {
        if (contQueued.has(node)) {
            notify(
                "warn",
                "자동 이어가기로 넣은 Run 이 아직 시작 전이라 Drain 이 닿지 않습니다. 과금을 막으려면 큐에서 그 항목을 지우세요.",
                10000
            );
        } else notify("info", "진행 중인 Run 이 없어 멈출 호출이 없습니다.");
        return;
    }
    notify(
        "success",
        "Drain 요청됨: 새 호출 발사를 멈추고 진행 중인 호출은 끝까지 받아 저장합니다. (Cancel 은 진행 중 호출의 비용을 내고 결과를 버립니다)"
    );
}

function onRunExecuted(node, message) {
    const e = uiEntry(message, "bmk_dp_run");
    if (!e) return;
    const s = stateOf(node);
    s.entry = e;
    contQueued.delete(node);
    setRootKey(node, e.root_key);
    if (s.cont) s.cont.entry = e;
    refreshRunLabels(node);
}

function onWave(detail) {
    if (!runningPromptId) return; // 이 탭이 큐에 넣은 실행이 아님
    const nodeId = String(detail?.node ?? "");
    let node = null;
    walkNodes((n, execId) => execId === nodeId && classOf(n) === RUN_NODE && ((node = n), true));
    const remaining = countOf(detail?.remaining);
    if (!node) {
        if (remaining > 0) notify("warn", `wave 종료(남은 ${remaining}건). 실행한 워크플로가 지금 열려 있지 않아 이어가지 못합니다.`);
        return;
    }
    setRootKey(node, detail.root_key);
    contQueued.delete(node);
    if (remaining <= 0) return;
    const s = stateOf(node);
    if (s.drainedPrompt === runningPromptId) {
        notify("info", `Drain 요청으로 멈춘 실행이라 자동으로 이어 가지 않습니다(남은 ${remaining}건).`);
        return;
    }
    if (!node.properties?.[PROP_AUTO]) {
        notify("info", `wave 시간 상한으로 멈췄습니다. 남은 호출 ${remaining}건 — 이 실행이 끝나면 승인 버튼으로 이어서 실행하세요.`);
        return;
    }
    s.stalls = s.prevRemaining != null && remaining >= s.prevRemaining ? s.stalls + 1 : 0;
    s.prevRemaining = remaining;
    if (s.stalls >= 2) return stopChain(node, `남은 호출이 줄지 않습니다(${remaining}건). Run 보고서를 확인하세요.`);
    s.cont = { promptId: runningPromptId, entry: null };
    armed.add(node);
    notify("info", `wave 종료: 남은 ${remaining}건. 이 실행이 끝나면 자동으로 이어서 큐에 넣습니다.`);
}

// wave 로 멈춘 프롬프트가 성공으로 끝난 뒤. cont.entry = 그 실행 뒤에 받은 bmk_dp_run.
// 이어서 넣은 큐는 무장하지 않는다 — 그 실행이 또 wave 로 멈추면 onWave 가 그 프롬프트로 다시 무장한다
// (미리 무장하면 그 큐가 취소·삭제됐을 때 무장이 남아 나중의 아무 Run 실행이 새 대기 해시를 자동 승인하게 된다).
async function continueRun(node, cont) {
    const blocker = approveBlocker(node);
    if (blocker) return stopChain(node, blocker);
    const e = cont.entry;
    if (!e) return stopChain(node, "Run 결과를 받지 못했습니다. Run 보고서를 확인하세요.");
    const hash = e.pending_hash ? String(e.pending_hash) : "";
    let queued;
    if (hash) {
        // 집합 비교: 지금 승인 대기가 이번 wave 가 승인받고 남긴 호출과 같을 때만 승인한다(개수 비교는 사전 승인 몫이
        // 섞여 승인하지 않은 호출을 통과시킨다). 옛 서버라 continuation_hash 가 없으면 "" → 멈춤(안전한 쪽)
        if (hash !== String(e.continuation_hash ?? "")) {
            return stopChain(node, `대기 ${countOf(e.pending)}건에 승인하지 않은 호출이 섞여 있습니다. 확인 후 승인 버튼을 누르세요.`);
        }
        queued = await queueApproved(node, hash, "자동 이어가기");
    } else {
        // 승인할 것이 없음 = 남은 것은 사전 승인(보드 재굴림)·다운로드 복구뿐 → approve 없이 다시 넣는다
        // (이 Run 과 조상인 Draft 의 approve 를 모두 비운다)
        clearOtherApproves();
        queued = await queueOnly([node], "자동 이어가기");
    }
    if (queued) contQueued.add(node);
}

function onPromptEnd(detail, failure) {
    const id = detail?.prompt_id;
    if (!id) return;
    if (id === runningPromptId) runningPromptId = null;
    for (const node of [...armed]) {
        const s = stateOf(node);
        const cont = s.cont;
        if (cont?.promptId !== id) continue;
        if (failure) stopChain(node, `실행이 ${failure}.`);
        else if (!execIdOf(node)) stopChain(node, "실행한 워크플로가 지금 열려 있지 않습니다. 그 탭에서 승인 버튼을 누르세요.");
        else {
            s.cont = null; // 진행 감시(prevRemaining)는 이어지는 연쇄를 위해 남긴다
            armed.delete(node);
            continueRun(node, cont);
        }
    }
}

function registerRun(nodeType) {
    const onNodeCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
        const r = onNodeCreated?.apply(this, arguments);
        this.addProperty?.(PROP_AUTO, false, "boolean");
        addButton(this, W_APPROVE, "", () => approveAndRun(this));
        addButton(this, W_AUTO, "", () => toggleAuto(this));
        addButton(this, W_DRAIN, "Drain (새 발사 중지 · 진행 중 호출은 받기)", () => drain(this));
        refreshRunLabels(this);
        fitHeight(this);
        return r;
    };

    const onConfigure = nodeType.prototype.onConfigure;
    nodeType.prototype.onConfigure = function () {
        const r = onConfigure?.apply(this, arguments);
        refreshRunLabels(this);
        return r;
    };

    const onExecuted = nodeType.prototype.onExecuted;
    nodeType.prototype.onExecuted = function (message) {
        const r = onExecuted?.apply(this, arguments);
        onRunExecuted(this, message);
        return r;
    };
}

// ─── Spec Draft 노드 (M3) ───────────────────────────────────────

// est_credits = [보통, 최대] → 천 단위 쉼표 문자열(없으면 "")
function creditsOf(e) {
    const v = Array.isArray(e?.est_credits) ? e.est_credits : [e?.est_credits, e?.est_credits];
    return [v[0], v[1]].map((x) => (x == null || !Number.isFinite(Number(x)) ? "" : Math.round(Number(x)).toLocaleString("en-US")));
}

function draftMinutes(e) {
    return Math.max(1, Math.ceil(Number(e?.est_seconds ?? 0) / 60));
}

function refreshDraftLabel(node) {
    const b = getWidget(node, W_APPROVE);
    if (b) {
        const e = stateOf(node).entry;
        const n = countOf(e?.pending);
        if (!e) b.label = "VLM 초안 승인 (누르면 드라이런부터)";
        else if (n <= 0 || !e.pending_hash) b.label = "승인할 초안 없음";
        else if (e.backend === DRAFT_MOCK) b.label = `VLM 초안 승인 (${n}건) [MOCK]`;
        else if (e.backend === DRAFT_LOCAL) b.label = `VLM 초안 승인 (로컬 ${n}건 ≈ ${draftMinutes(e)}분)`;
        else {
            const [typ] = creditsOf(e);
            b.label = `VLM 초안 승인 (${n}건${typ ? ` ≈ ${typ} 크레딧` : ""})`;
        }
    }
    node.setDirtyCanvas?.(true, true);
}

// Draft 만 부분 실행(Prepare·Run 은 돌지 않음). openai 는 크레딧, local_qwen 은 ComfyUI 모델 언로드를 확인 창으로 알린다
async function approveDraft(node) {
    const s = stateOf(node);
    if (s.busy) return;
    const e = s.entry;
    if (!e) {
        await requeueDryRun(node, "Spec Draft");
        return;
    }
    const n = countOf(e.pending);
    if (n <= 0 || !e.pending_hash) {
        notify("info", "승인할 초안이 없습니다.");
        return;
    }
    const blocker = approveBlocker(node);
    if (blocker) {
        notify("error", blocker);
        return;
    }
    s.busy = true;
    try {
        let msg = null;
        if (e.backend === DRAFT_LOCAL) {
            msg = `로컬 Qwen 으로 ${n}건(≈ ${draftMinutes(e)}분). ComfyUI 모델을 VRAM 에서 내립니다(과금 없음).`;
        } else if (e.backend !== DRAFT_MOCK) {
            const [typ, hi] = creditsOf(e);
            const k = countOf(e.orphan_retries);
            msg =
                `VLM 초안 ${n}건(보통 ≈ ${typ || "?"}, 최대 ${hi || "?"} 크레딧)을 승인하고 과금합니다.` +
                (k > 0 ? ` orphan 재시도 ${k}건 포함(과금됐을 수 있음).` : "") +
                "\n드라이런 이후 대기 목록이 바뀌었으면 서버가 호출 없이 드라이런만 다시 합니다.";
        }
        if (msg && !(await confirmDialog(msg))) return;
        await queueApproved(node, String(e.pending_hash), "Spec Draft");
    } finally {
        s.busy = false;
    }
}

// 마지막 부분 실행이 실제로 돌리는 노드(실행 ID) — 서버처럼 partial_execution_targets 와 그 조상(입력 링크 [id, slot]).
// 전체 실행이면 null. 프론트(1.53.10)는 제출이 성공하면 그래프의 모든 노드 위젯에 {isPartialExecution} 만 넘겨 afterQueued 를
// 부르므로, 이 노드가 그 실행에 들었는지는 제출된 프롬프트에서 직접 센다.
let partialRun = null;

function partialRunIds(prompt, targets) {
    const ids = new Set();
    const stack = targets.map(String);
    while (stack.length) {
        const id = stack.pop();
        if (ids.has(id) || !prompt[id]) continue;
        ids.add(id);
        for (const v of Object.values(prompt[id].inputs ?? {})) {
            if (Array.isArray(v) && typeof v[0] === "string" && Number.isInteger(v[1])) stack.push(v[0]);
        }
    }
    return ids;
}

function watchSubmits() {
    const submit = api.queuePrompt;
    api.queuePrompt = function (number, data, options) {
        const targets = options?.partialExecutionTargets;
        partialRun = targets?.length ? partialRunIds(data?.output ?? {}, targets) : null;
        return submit.apply(this, arguments);
    };
}

// adopt 는 큐 하나에만 쓴다: 프론트가 프롬프트를 제출한 뒤 모든 노드의 위젯에 afterQueued 를 부른다(제출 실패면 부르지 않음).
// 이 노드가 빠진 부분 실행(다른 가지의 선택 출력 실행·다른 Draft 의 승인 버튼)은 쓰지 않았으니 남긴다.
function armAdopt(node) {
    const w = getWidget(node, "adopt");
    if (!w) return;
    w.afterQueued = (opts) => {
        if (isOff(node) || !w.value) return;
        if (opts?.isPartialExecution && partialRun && !execIdsOf(node).some((id) => partialRun.has(id))) return;
        w.value = "";
        checkState();
        node.setDirtyCanvas?.(true, true);
    };
}

function registerDraft(nodeType) {
    const onNodeCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
        const r = onNodeCreated?.apply(this, arguments);
        armAdopt(this);
        addButton(this, W_APPROVE, "", () => approveDraft(this));
        refreshDraftLabel(this);
        fitHeight(this);
        return r;
    };

    const onConfigure = nodeType.prototype.onConfigure;
    nodeType.prototype.onConfigure = function () {
        const r = onConfigure?.apply(this, arguments);
        refreshDraftLabel(this);
        return r;
    };

    const onExecuted = nodeType.prototype.onExecuted;
    nodeType.prototype.onExecuted = function (message) {
        const r = onExecuted?.apply(this, arguments);
        const e = uiEntry(message, DRAFT_UI);
        if (e) {
            stateOf(this).entry = e;
            setRootKey(this, e.root_key);
            refreshDraftLabel(this);
        }
        return r;
    };
}

// ─── Review 노드 / 보드 ─────────────────────────────────────────

function refreshReviewLabel(node) {
    const b = getWidget(node, W_BOARD);
    if (b) b.label = rootKeyOf(node) ? "Open Board" : "Open Board (먼저 실행)";
    node.setDirtyCanvas?.(true, true);
}

function openBoard(node) {
    const key = rootKeyOf(node);
    if (!key) {
        notify("info", "먼저 Review 노드를 실행하세요. 보드 주소는 실행 결과로 받습니다.");
        return;
    }
    const win = window.open(api.fileURL(`${BOARD_PATH}?root=${key}`), `bmk_dp_board_${key}`);
    if (!win) notify("warn", "팝업이 차단되었습니다. 이 주소의 팝업을 허용하세요.");
}

async function queueReview(key) {
    const targets = nodesOf(REVIEW_NODE).filter((n) => rootKeyOf(n) === key && !isOff(n));
    if (!targets.length) {
        notify("warn", "보드 큐 요청: 지금 열린 워크플로에 이 프로젝트의 켜진 Review 노드가 없습니다(한 번 실행해야 연결됩니다).");
        return;
    }
    // Review 의 상류인 Run·Draft 도 함께 실행된다. 보드 요청은 사전 승인된 재굴림만 과금해야 하므로 쓰이지 않은 승인 해시
    // (직접 붙여 넣은 것 등)가 함께 나가지 않게 모든 유료 노드의 approve 를 비운다(이미 큐에 들어간 프롬프트는 그대로)
    clearOtherApproves();
    if (await queueOnly(targets, "보드 요청")) notify("info", "보드 요청으로 Review 를 큐에 넣었습니다.", 3000);
}

function onBoardMessage(ev) {
    if (ev.origin !== window.location.origin) return;
    const d = ev.data;
    if (d?.type !== "bmk-dp-queue" || typeof d.rootKey !== "string" || !ROOT_KEY_RE.test(d.rootKey)) return;
    const key = d.rootKey;
    clearTimeout(boardTimers.get(key));
    boardTimers.set(
        key,
        setTimeout(() => {
            boardTimers.delete(key);
            queueReview(key);
        }, BOARD_QUEUE_DELAY_MS)
    );
}

function registerReview(nodeType) {
    const onNodeCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
        const r = onNodeCreated?.apply(this, arguments);
        addButton(this, W_BOARD, "", () => openBoard(this));
        refreshReviewLabel(this);
        fitHeight(this);
        return r;
    };

    const onConfigure = nodeType.prototype.onConfigure;
    nodeType.prototype.onConfigure = function () {
        const r = onConfigure?.apply(this, arguments);
        refreshReviewLabel(this);
        return r;
    };

    const onExecuted = nodeType.prototype.onExecuted;
    nodeType.prototype.onExecuted = function (message) {
        const r = onExecuted?.apply(this, arguments);
        setRootKey(this, uiEntry(message, "bmk_dp_board")?.root_key);
        refreshReviewLabel(this);
        return r;
    };
}

app.registerExtension({
    name: "BMK.DesignPatch",

    setup() {
        api.addEventListener("execution_start", (e) => {
            runningPromptId = e.detail?.prompt_id ?? null;
        });
        api.addEventListener("execution_success", (e) => onPromptEnd(e.detail, null));
        api.addEventListener("execution_error", (e) => onPromptEnd(e.detail, "오류로 끝났습니다"));
        api.addEventListener("execution_interrupted", (e) => onPromptEnd(e.detail, "취소되었습니다"));
        api.addEventListener("bmk.dp.wave", (e) => onWave(e.detail));
        window.addEventListener("message", onBoardMessage);
        watchSubmits();
    },

    beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name === RUN_NODE) registerRun(nodeType);
        else if (nodeData.name === DRAFT_NODE) registerDraft(nodeType);
        else if (nodeData.name === REVIEW_NODE) registerReview(nodeType);
    },
});
