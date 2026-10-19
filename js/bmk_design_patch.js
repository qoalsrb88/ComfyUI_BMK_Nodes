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
//  - 보드 창의 postMessage {type:"bmk-dp-queue", rootKey, layout?}(같은 origin 만) → 그 프로젝트의 Review 노드만 부분 실행.
//    보드 재굴림은 서버에서 사전 승인된 호출이라 Run 이 그 호출만 과금한다 — 큐에 넣기 전에 모든 유료 노드(Run·Draft)의
//    approve 를 비운다.
//    layout = 편집기 "반영(큐)": Review 의 상류에 켜진 Import PSD 가 있을 때만 넣는다(부분 실행이 Import 를 다시 돌려야 반영됨).
//    연타는 0.8초 단위로 한 번에 묶는다(그 안에 layout 요청이 있으면 묶음 전체가 layout). S3 Studio 는 무료 반영을 직접 큐하고
//    이 길은 옛 서버(재시작 전) 대체로만 쓴다.
//
// Studio 승인(S3b — Studio 의 유료 Run·Spec Draft·재굴림을 이 탭이 큐에 넣는다. 토큰은 프론트가 app.queuePrompt 안에서만 붙인다)
//  - 길: 같은 출처 postMessage(opener — 답은 ev.source 로) 또는 BroadcastChannel("bmk-dp")(답은 채널로). tab = 탭 로드마다 새 임의
//    ID, tab 을 지정한 메시지는 그 탭만 받는다(채널은 모든 탭에 간다 — 다른 탭의 응답도 tab 이 있어 여기서 걸러짐).
//  - hello {type:"bmk-dp-hello", id} → {type, id, tab, ext: 3, submit, busy}. submit = 제출 감싸개가 설치됐고 app.queuePrompt 가
//    함수면 "ok"(아니면 "unsupported" — Studio 유료 단추 끔), busy = 이 탭이 큐를 처리하는 중이거나 Studio 승인을 처리하는 중.
//  - 승인 {type:"bmk-dp-approve", id, tab, rootKey, stage: run|draft|reroll, hash, only, what}: tab 이 이 탭일 때만(없으면 무시).
//    모양이 틀리면 bad_request, 바쁘면 busy(프론트 자동 큐가 켜져 있어도 — 아래), 아니면 곧바로 ack 를 보내고 서버(GET /pipeline/prompt)에서 그 단계의 템플릿을 받는다 — 서버가 승인 해시를 지금 계획·반영
//    상태와 대조하고(409 는 reason 그대로 Studio 로), Studio 가 보낸 프롬프트는 쓰지 않는다. 템플릿 검사(응답 stage·hash·only =
//    요청, 대상 = 출력 노드 하나, 노드 키 dp_*·class BMKDesignPatch*, 승인 해시는 그 단계 유료 노드 하나에만, 초안 adopt ""·
//    keep_loaded false)를 통과하면 무장하고 app.queuePrompt(0, 1, {queueNodeIds: 대상}) — 제출 감싸개(watchSubmits)가 그 제출
//    한 번의 output 만 템플릿으로 바꾼다(workflow 는 그대로 — 안쪽 감싸개가 만짐).
//  - 닫힌 실패: 무장 중(서버 템플릿을 받은 뒤 app.queuePrompt 가 돌아오고 프론트 큐 처리 app.processingQueue 가 끝날 때까지 — 기다리지 않는
//    감싸개·먼저 시작된 처리 루프에서 늦게 꺼낸 항목도 막음)에 대상이 다르거나 두 번째인 제출은 오류를 던져 아무것도 보내지 않는다 —
//    처리 중 끼어든 사용자 큐(프론트는 뒤에 쌓인 것부터 꺼냄 — 다시 넣을 것은 이것뿐)·셋째 인자를 버린 다른 확장 감싸개(사용자 그래프 전체
//    실행이 토큰과 나감)·큐 반복 노드의 두 번째 제출(까닭마다 토스트 문구가 다름 — 원래 함수를 미뤄 부르는 감싸개 탓에 우리 항목을 아직 모르면
//    '직접 누른 큐였다면 다시'). 무장을 풀 때 무장 동안 쌓여 꺼내지지 않은 항목(먼저 꺼낸 항목이 던져 처리 루프가 끝남)은 프론트 큐에서 빼고,
//    막힌·거절된 제출 뒤 프론트가 남긴 토큰(api.authToken·apiKey)도 지운다.
//    성공 = 바꿔치기 했고 prompt_id 를 받음(app.queuePrompt 는 막혀도 true) — 아니면 not_submitted(까닭별 문구)·rejected.
//    결과마다 탭 토스트(프론트 대화상자는 다른 확장 스크립트를 탓할 수 있음) + Studio 에 bmk-dp-approved.
//  - 프론트 자동 큐(Run (On Change)·Run (Instant) 실행 중·옛 메뉴 Auto Queue)가 켜져 있으면 받을 때·무장 직전에 busy — 승인 제출의
//    부수 효과(시드 afterQueued·처리 시작의 clearRunErrors)로 템플릿 실행이 끝난 뒤 프론트가 사용자 그래프 전체를 무장 없이 토큰과 함께
//    다시 큐한다. hello busy 에는 넣지 않는다(Studio 에 '큐 처리 중'으로 보이고 R 이 옛 Review 부분 실행 길로 감 — 승인 때 이유와 거절).
//  - 템플릿 실행의 wave(노드 키 dp_*)는 그래프에서 찾지 않고 bmk-dp-wave 로 채널과 그 프로젝트 승인을 보낸 창들에 전한다
//    (Run 은 Studio 에서 이어서 승인, 재굴림은 사전 승인분이라 Studio 머리줄 'ComfyUI 큐' — 자동 없음).
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
const IMPORT_NODE = "BMKDesignPatchImportPSD";
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
const HELLO = "bmk-dp-hello";
const HELLO_CHANNEL = "bmk-dp";
const EXT_VERSION = 3; // S3a 2(hello 응답만) → S3b 3(Studio 승인 처리)
const APPROVE = "bmk-dp-approve";
const PROMPT_PATH = "/bmk/design_patch/pipeline/prompt";
const HASH_RE = /^[0-9a-f]{12}$/; // 승인 해시(run·draft)
const ONLY_RE = /^[A-Za-z0-9_, -]+$/; // only = crop_id·style 토큰(쉼표·공백 구분 — 서버가 정규화)
// Studio 승인 stage → 템플릿 출력 노드(부분 실행 대상)·그 단계 유료 노드(승인 해시를 싣는 곳 — reroll 은 "")·토스트 이름
const STUDIO_STAGES = {
    run: { target: "dp_review", paid: "dp_run", cls: RUN_NODE, label: "Run" },
    draft: { target: "dp_draft", paid: "dp_draft", cls: DRAFT_NODE, label: "Spec Draft" },
    reroll: { target: "dp_review", paid: "dp_run", cls: RUN_NODE, label: "재굴림" },
};
const TAB_ID = `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`;
const MODE_NEVER = 2;
const MODE_BYPASS = 4;

const states = new WeakMap(); // Run·Draft 노드 → { entry, cont, prevRemaining, stalls, busy, drainedPrompt }
const armed = new Set(); // 자동 이어가기 대기 중인 Run 노드
const contQueued = new Set(); // 자동 이어가기로 큐에 넣었고 그 Run 이 아직 실행되지 않은 노드(Drain 안내용)
const boardTimers = new Map(); // rootKey → 보드 큐 요청 묶음 타이머
const boardLayout = new Set(); // 묶음 안에 레이아웃 반영 요청이 있는 rootKey
// 이 탭이 큐에 넣어 지금 실행 중인 프롬프트. execution_start/success 는 큐에 넣은 탭에만 온다
// (bmk.dp.wave 는 모든 탭에 방송되므로 이 값으로 남의 실행을 걸러 낸다).
let runningPromptId = null;
// Studio 승인 무장 {targets, output: 템플릿, used, result: 그 제출의 결과 promise({prompt_id} | {error, unknown}), items, told, changed, unsure} — app.queuePrompt 가 돌아오고 프론트 큐 처리가 끝날 때까지만
let studioSubmit = null;
let studioBusy = false; // Studio 승인을 받아 결과를 돌려줄 때까지(그동안 온 승인은 busy)
const approveIds = new Set(); // 이 탭 로드 동안 받은 Studio 승인 id — 같은 id 가 다시 와도(중복 전달·window 와 채널 둘 다) 한 번만 처리
let submitHooked = false; // 제출 감싸개(watchSubmits) 설치됨 — hello submit
let studioChannel = null;
const waveTo = new Map(); // rootKey → 그 프로젝트 승인을 보낸 Studio 창들(템플릿 실행의 wave 를 전함)
const studioRuns = new Map(); // Studio 승인으로 낸 prompt_id → stage(그 실행이 끝날 때까지 — wave 문구)
// 무장 중 막은 제출의 까닭 → 문구(토스트·던지는 오류). 다시 넣을 것은 처리 중 끼어든 사용자 큐뿐. unsure = 우리 항목을 아직 모를 때 막은 것
// (감싸개가 원래 함수를 미뤄 부르며 셋째 인자도 버렸으면 우리 항목도 사용자 큐와 똑같이 보임) — 누른 사람만 다시 넣게
const BLOCKED = {
    user: "Studio 승인을 제출하는 중이라 이 큐 제출을 막았습니다(과금 없음) — 다시 큐에 넣으세요",
    repeat: "Studio 승인 제출을 다른 확장(큐 반복 노드 — Prompt Pack·Prompt Multi·XY Plot 등)이 되풀이해 두 번째부터 막았습니다(과금 없음) — 다시 큐에 넣지 마세요",
    changed: "Studio 승인 제출이 다른 확장 감싸개를 지나며 대상이 바뀌어(셋째 인자를 버림 등 — 사용자 그래프 전체 실행이 됨) 막았습니다(과금 없음) — 다시 승인해도 같음",
    unsure: "Studio 승인을 제출하는 중이라 이 큐 제출을 막았습니다(과금 없음) — 직접 누른 큐였다면 다시 큐에 넣으세요(누르지 않았다면 다른 확장 감싸개가 " +
        "Studio 승인 제출의 대상을 바꾼 것 — 다시 승인해도 같음)",
};
const AUTO_QUEUE = { change: "Run (On Change)", "instant-running": "Run (Instant)" }; // 1.53 queueSettings.mode → Run 단추 이름

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
    if (studioSubmit) {
        // 넣어도 처리 중이라 나중에 꺼내지고 무장 중인 제출 감싸개가 막는다 — 넣었다고 표시하지 않게 여기서 거절
        notify("warn", `${what}: Studio 승인을 제출하는 중입니다. 잠시 뒤 다시 큐에 넣으세요.`);
        return false;
    }
    const aq = autoQueueMode();
    if (aq) {
        // Studio 승인과 같은 판정(autoQueueWhy) — 이 큐가 끝난 뒤 프론트가 사용자 그래프 전체를 다시 큐해 사전 승인 재굴림이 예상 밖에 돈다
        notify("warn", `${what}: ComfyUI 탭의 자동 큐(${aq})가 켜져 있어 큐에 넣지 않았습니다 — 넣으면 실행이 끝난 뒤 그래프 전체가 자동으로 다시 큐됩니다(사전 승인한 재굴림 과금). Run 단추 모드를 Run 으로 바꾼 뒤 다시 하세요.`, 10000);
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
    if (nodeId.startsWith("dp_")) return studioWave(detail, nodeId);
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
    studioRuns.delete(id);
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

// 서버 응답 오류 문구를 토스트 한 줄로: HTML 주석·태그와 줄바꿈을 걷고 200자까지
function brief(text) {
    const s = text.replace(/<!--[\s\S]*?-->|<[!/]?[A-Za-z][^>]*>/g, " ").replace(/\s+/g, " ").trim();
    return s.length > 200 ? `${s.slice(0, 200)}…` : s;
}

function watchSubmits() {
    const submit = api.queuePrompt;
    api.queuePrompt = function (number, data, options) {
        const targets = options?.partialExecutionTargets;
        const s = studioSubmit;
        if (s) {
            // Studio 승인 무장 중: 대상이 정확히 같은 첫 제출만 output 을 템플릿으로(새 객체 — workflow 는 그대로), 그 밖(처리 중 끼어든
            // 사용자 항목·셋째 인자를 버린 감싸개의 전체 실행·반복 제출)은 아무것도 보내지 않는다
            const same = targets?.length === s.targets.length && s.targets.every((t, i) => targets[i] === t);
            if (s.used || !same) {
                // 대상이 같은데 이미 썼음 = 승인 제출의 반복(사용자 그래프에는 dp_* 가 없음). 안 썼는데 우리 호출이 바로 쌓은 항목 중 큐에서
                // 꺼내진 수가 지금까지 '대상이 바뀜'으로 막은 수보다 많음 = 꺼낸 것이 우리 항목(대상이 바뀜). 안 썼고 우리 항목을 아직 모름(바로 쌓은
                // 항목이 없고 대상이 같은 항목도 큐에 없음 — 감싸개가 미뤄 쌓음) = 어느 쪽인지 모름. 그 밖(우리 항목이 아직 큐에 있음·꺼내진 우리
                // 항목은 이미 막음) = 끼어든 다른 제출
                let kind = s.used && same ? "repeat" : "user";
                if (!s.used && s.items.filter((it) => !app.queueItems?.includes(it)).length > s.changed) {
                    kind = "changed";
                    s.changed += 1;
                } else if (!s.used && !s.items.length && !app.queueItems?.some((it) => it?.queueNodeIds === s.targets)) {
                    kind = "unsure";
                    s.unsure += 1;
                }
                // 프론트 대화상자는 다른 확장 스크립트를 탓할 수 있다(제출마다 뜸) — 탭 토스트는 까닭마다 무장 한 번에 한 번
                if (!s.told.has(kind)) notify("warn", BLOCKED[kind], 10000);
                s.told.add(kind);
                throw new Error(`BMK Design Patch: ${BLOCKED[kind]}`);
            }
            s.used = true;
            const args = [...arguments];
            args[1] = { ...data, output: s.output };
            partialRun = partialRunIds(s.output, targets);
            // PromptExecutionError(서버 응답 4xx·5xx)는 message 가 늘 같아 toString 이 이유를 담는다 — JSON 이 아닌 응답(프록시 오류 페이지)이면
            // 본문 전체가 실리므로 줄여 씀
            const why = (e) => ({ error: e?.response ? brief(String(e)) : (e?.message ?? String(e)) });
            let sent;
            try {
                sent = submit.apply(this, args);
            } catch (e) {
                s.result = Promise.resolve(why(e)); // 안쪽 감싸개가 보내기 전에 동기로 던짐
                throw e;
            }
            // 거절이 확정된 것은 서버의 4xx(ComfyUI 는 큐에 넣지 않을 때 400)뿐. 연결 끊김(TypeError)·200 인데 JSON 아님·5xx(프록시가 넘긴 뒤
            // 오류) 는 서버가 이미 큐에 넣었을 수 있다 — 결과 모름
            s.result = Promise.resolve(sent).then((r) => ({ prompt_id: r?.prompt_id ?? null }),
                (e) => ({ ...why(e), unknown: !(e?.response && e.status >= 400 && e.status < 500) }));
            return sent;
        }
        partialRun = targets?.length ? partialRunIds(data?.output ?? {}, targets) : null;
        return submit.apply(this, arguments);
    };
    submitHooked = true;
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

// 레이아웃 반영은 부분 실행이 Import PSD 를 다시 돌릴 때만 된다 — 대상(Review)의 조상(입력 링크)에 Import 가 있는가.
// 꺼진(mute) 노드는 프롬프트에 없고 bypass 된 노드는 graphToPrompt 가 건너 이으므로 조상에 남지 않는다.
function importUpstream(prompt, targets) {
    return [...partialRunIds(prompt, targets)].some((id) => prompt[id].class_type === IMPORT_NODE);
}

async function queueReview(key, layout) {
    const targets = nodesOf(REVIEW_NODE).filter((n) => rootKeyOf(n) === key && !isOff(n));
    if (!targets.length) {
        notify("warn", "보드 큐 요청: 지금 열린 워크플로에 이 프로젝트의 켜진 Review 노드가 없습니다(한 번 실행해야 연결됩니다).");
        return;
    }
    if (layout) {
        let prompt;
        try {
            prompt = (await app.graphToPrompt()).output;
        } catch (e) {
            notify("error", `보드 요청 큐 실패: ${e?.message ?? e}`);
            return;
        }
        if (!importUpstream(prompt, targets.map(execIdOf).filter(Boolean))) {
            notify("warn", "레이아웃 반영은 Import PSD 가 Review 상류일 때만 됩니다 — ComfyUI 에서 Import 를 포함해 큐에 넣으세요");
            return;
        }
    }
    // Review 의 상류인 Run·Draft 도 함께 실행된다. 보드 요청은 사전 승인된 재굴림만 과금해야 하므로 쓰이지 않은 승인 해시
    // (직접 붙여 넣은 것 등)가 함께 나가지 않게 모든 유료 노드의 approve 를 비운다(이미 큐에 들어간 프롬프트는 그대로)
    clearOtherApproves();
    if (await queueOnly(targets, "보드 요청")) notify("info", "보드 요청으로 Review 를 큐에 넣었습니다.", 3000);
}

function onBoardMessage(ev) {
    if (ev.origin !== window.location.origin) return;
    const d = ev.data;
    const hello = helloReply(d);
    if (hello) {
        ev.source?.postMessage(hello, window.location.origin);
        return;
    }
    if (d?.type === APPROVE) {
        studioApprove(d, (m) => ev.source?.postMessage(m, window.location.origin), ev.source);
        return;
    }
    if (d?.type !== "bmk-dp-queue" || typeof d.rootKey !== "string" || !ROOT_KEY_RE.test(d.rootKey)) return;
    const key = d.rootKey;
    if (d.layout === true) boardLayout.add(key);
    clearTimeout(boardTimers.get(key));
    boardTimers.set(
        key,
        setTimeout(() => {
            boardTimers.delete(key);
            queueReview(key, boardLayout.delete(key));
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

// ─── Studio 승인 (S3b) ──────────────────────────────────────────

// 프론트 큐 항목이 이 무장의 승인 제출인가: 프론트는 options.queueNodeIds 배열을 그대로 담는다(인자를 넘기는 감싸개도 같은 배열),
// 셋째 인자를 바꾸거나 버린 감싸개면 우리 호출이 바로 쌓은 항목(items — 원래 함수를 미뤄 부르는 감싸개면 알 수 없음)
function ourItem(s, it) {
    return it?.queueNodeIds === s.targets || s.items.includes(it);
}

// 프론트 큐 처리 루프가 끝날 때(processingQueue = false 대입 — 1.53.10 은 finally·작업공간 전환 실패·로그인 오류) 풀리는 promise. 그 대입에서 덫을
// 거두고(보통 속성으로 되돌림) 풀므로 무장은 바로 뒤 마이크로태스크에서 풀린다 — 타이머로 살피면 그 틈에 시작된 새 사용자 처리(클릭 = 새 태스크)까지
// 무장에 걸리고, 숨은 탭에서는 그 틈이 1초 이상으로 늘어난다
function queueLoopEnd() {
    return new Promise((resolve) => {
        let on = true;
        Object.defineProperty(app, "processingQueue", {
            configurable: true,
            enumerable: true,
            get: () => on,
            set(v) {
                on = v;
                if (v) return;
                Object.defineProperty(app, "processingQueue", { value: false, writable: true, configurable: true, enumerable: true });
                resolve();
            },
        });
    });
}

// 재굴림 실패의 탭 토스트 문구: 할 일 '(Studio 에서) 다시 승인'이 R 재누름(+1 사전 승인)으로 읽히지 않게 빼거나 Studio 머리줄 'ComfyUI 큐'로 바꾸고,
// 사전 승인분이 예약된 채 남는다고 덧붙인다(Studio refusal()·paidResult 와 같은 안내 — Studio 로 보내는 error 는 Studio 가 바꿈)
function rerollWhy(reason, error) {
    const why = error
        .replace(/ — (잠시 뒤 다시 승인|승인은 Studio 에서 다시)(?= — |$)/, "")
        .replace(/(Studio 에서 )?다시 승인(?= — |$)/, "Studio 머리줄 'ComfyUI 큐'");
    return (
        `${why} · ${reason === "unknown" ? "큐에 들어가지 않았으면 " : ""}사전 승인한 재굴림은 예약된 채 남음(다음 Run·ComfyUI 큐 때 과금): ` +
        "지금 보내려면 Studio 머리줄 'ComfyUI 큐' — R 을 다시 누르면 +1 호출이 더 승인됨"
    );
}

// 프론트 자동 큐가 켜져 있으면 승인을 받지 않는 이유 | "". 켜져 있으면 승인 제출의 부수 효과(시드 afterQueued 가 그래프를 바꿔 promptQueued
// 뒤 'change' 판정, 처리 시작의 clearRunErrors 가 오류로 멈춘 Instant 를 풂)로 템플릿 실행이 끝난 뒤 프론트가 사용자 그래프 전체를
// app.queuePrompt(셋째 인자 없음)로 다시 큐한다 — 무장이 풀린 뒤라 토큰과 함께 나가고 시드가 randomize 면 되풀이된다.
// S3a 길(보드 큐·승인 버튼·자동 이어가기 — queueOnly)도 같은 판정(autoQueueMode)으로 거절한다
function autoQueueWhy() {
    const on = autoQueueMode();
    if (!on) return "";
    return (
        `ComfyUI 탭의 자동 큐(${on})가 켜져 있음 — 승인 제출 뒤 사용자 그래프 전체가 토큰과 함께 자동으로 큐될 수 있어 받지 않음: ` +
        "ComfyUI 탭의 Run 단추 모드를 Run 으로 바꾼 뒤 다시 승인"
    );
}

// 켜진 프론트 자동 큐의 Run 단추 이름 | "". instant-idle(Instant 를 골랐지만 멈춤)은 사용자가 Run 을 눌러야 돈다
function autoQueueMode() {
    const mode = app.extensionManager?.queueSettings?.mode;
    const on = typeof mode === "string" && mode !== "disabled" && mode !== "instant-idle" ? (AUTO_QUEUE[mode] ?? mode) : "";
    return on || (app.ui?.autoQueueEnabled === true ? "Auto Queue" : ""); // 옛 메뉴
}

function helloReply(d) {
    // Studio hello → 이 탭의 응답(아니면 null). tab 을 지정했으면 그 탭만(다른 탭의 응답 메시지도 여기서 걸러짐)
    if (d?.type !== HELLO || typeof d.id !== "string" || (d.tab !== undefined && d.tab !== TAB_ID)) return null;
    const submit = submitHooked && typeof app.queuePrompt === "function" ? "ok" : "unsupported";
    return { type: HELLO, id: d.id, tab: TAB_ID, ext: EXT_VERSION, submit, busy: !!app.processingQueue || studioBusy };
}

// 승인 메시지 모양에서 틀린 필드 이름 하나 | "". reroll 은 사전 승인분 전체라 해시·only 가 없다
function approveFault(d, stage) {
    if (!stage) return "stage";
    if (typeof d.rootKey !== "string" || !ROOT_KEY_RE.test(d.rootKey)) return "rootKey";
    const paid = stage !== "reroll";
    if (paid ? typeof d.hash !== "string" || !HASH_RE.test(d.hash) : d.hash !== "") return "hash";
    if (paid ? typeof d.only !== "string" || !ONLY_RE.test(d.only) : d.only !== "") return "only";
    return "";
}

// 서버가 준 템플릿에서 어긋난 항목 하나 | "". 응답 stage·hash·only = 요청(서버가 지금 계획과 대조하고 정규화한 값),
// 대상 = 그 단계 출력 노드 하나, 노드 키 dp_*·class BMKDesignPatch*, 유료 노드는 그 단계 것 하나(그 키에만), 승인 해시는 그 노드에만
function templateFault(t, d) {
    const spec = STUDIO_STAGES[d.stage];
    const p = t?.prompt;
    if (!p || typeof p !== "object" || Array.isArray(p)) return "prompt 없음";
    if (t.stage !== d.stage) return `응답 stage ${t.stage} ≠ ${d.stage}`;
    if (t.hash !== d.hash) return "응답 hash ≠ 승인 해시";
    if (t.only !== d.only) return "응답 only ≠ 요청 only";
    if (!Array.isArray(t.targets) || t.targets.length !== 1 || t.targets[0] !== spec.target) return `targets ≠ [${spec.target}]`;
    if (!p[spec.target]) return `대상 노드 ${spec.target} 없음`;
    for (const [k, n] of Object.entries(p)) {
        const cls = n?.class_type;
        if (!/^dp_[a-z]+$/.test(k) || typeof cls !== "string" || !cls.startsWith("BMKDesignPatch")) return `규칙 밖 노드 ${k}`;
        if (k !== spec.paid && (n.inputs?.approve ?? "") !== "") return `${k}.inputs.approve 가 비지 않음`;
        if (PAID_NODES.includes(cls) && (cls !== spec.cls || k !== spec.paid)) return `${spec.label} 템플릿에 ${k}(${cls})`;
    }
    if (p[spec.paid]?.class_type !== spec.cls) return `${spec.paid}(${spec.cls}) 없음`;
    if (p[spec.paid].inputs?.approve !== d.hash) return `${spec.paid}.inputs.approve ≠ ${d.hash ? "승인 해시" : '""'}`;
    const dr = p.dp_draft?.inputs;
    if (dr && dr.adopt !== "") return 'dp_draft.inputs.adopt ≠ ""';
    if (dr && dr.keep_loaded !== false) return "dp_draft.inputs.keep_loaded ≠ false";
    return "";
}

// Studio 승인 메시지 하나 → 서버 템플릿 → 무장 제출 → bmk-dp-approved. reply = 받은 길로 답함, source = 보낸 창(채널이면 null)
async function studioApprove(d, reply, source) {
    if (d.tab !== TAB_ID || typeof d.id !== "string") return; // 채널은 모든 탭에 간다
    if (approveIds.has(d.id)) return; // 승인 한 번 = 프롬프트 하나 — 처리 중이든 끝났든 답도 하나
    approveIds.add(d.id);
    const stage = Object.keys(STUDIO_STAGES).includes(d.stage) ? d.stage : null;
    const label = stage ? STUDIO_STAGES[stage].label : "";
    const answer = (m) => reply({ type: "bmk-dp-approved", id: d.id, tab: TAB_ID, stage, ...m });
    const fail = (reason, error, more) => {
        const shown = stage === "reroll" ? rerollWhy(reason, error) : error;
        notify("warn", `Studio 승인${label ? `(${label})` : ""}${reason === "unknown" ? "" : " 안 됨"}: ${shown}`, 10000);
        answer({ ok: false, reason, error, ...more });
    };
    const bad = approveFault(d, stage);
    if (bad) return fail("bad_request", `승인 메시지 모양이 잘못됨: ${bad}`);
    if (app.processingQueue || studioBusy) {
        return fail("busy", `ComfyUI 탭이 ${studioBusy ? "다른 Studio 승인을" : "큐를"} 처리하는 중 — 잠시 뒤 다시 승인`);
    }
    const aq = autoQueueWhy();
    if (aq) return fail("busy", aq);
    studioBusy = true;
    reply({ type: "bmk-dp-ack", id: d.id, tab: TAB_ID });
    if (source) waveTo.set(d.rootKey, (waveTo.get(d.rootKey) ?? new Set()).add(source));
    try {
        const q = stage === "reroll" ? "" : `&approve=${encodeURIComponent(d.hash)}&only=${encodeURIComponent(d.only)}`;
        let res, t;
        try {
            res = await api.fetchApi(`${PROMPT_PATH}?root=${encodeURIComponent(d.rootKey)}&stage=${stage}${q}`);
            t = await res.json().catch(() => null);
        } catch (e) {
            return fail("http", `템플릿 요청 실패: ${e?.message ?? e}`);
        }
        if (res.status === 409 && typeof t?.reason === "string") {
            // 서버가 승인 해시를 지금 계획·반영 상태와 대조한 결과 — Studio 가 계획을 다시 받는다
            const more = t.reason === "stale" ? { hash: t.hash } : t.reason === "apply_needed" ? { pending: t.pending } : {};
            return fail(t.reason, t.error ?? `HTTP ${res.status}`, more);
        }
        if (!res.ok) return fail("http", `템플릿 요청 실패(HTTP ${res.status})${t?.error ? `: ${t.error}` : ""}`);
        const fault = templateFault(t, d);
        if (fault) return fail("template", `템플릿 검사 실패: ${fault}`);
        // 서버를 기다리는 사이 큐 처리가 시작됐으면 지금 넣은 항목은 무장이 풀린 뒤에 나간다. 그사이 켠 자동 큐도 거절
        if (app.processingQueue) return fail("busy", "ComfyUI 탭이 큐를 처리하는 중 — 잠시 뒤 다시 승인");
        const late = autoQueueWhy();
        if (late) return fail("busy", late);
        // items = 우리 호출이 쌓은 큐 항목, told = 토스트를 낸 막힘 까닭, changed = 대상이 바뀌어 막은 우리 항목 수, unsure = 우리 것인지 모르고 막은 수
        const sub = (studioSubmit = {
            targets: [STUDIO_STAGES[stage].target], output: t.prompt, used: false, result: null, items: [], told: new Set(), changed: 0, unsure: 0,
        });
        let err = null;
        let ret;
        let left = 0;
        let stray = 0;
        const before = new Set(app.queueItems ?? []);
        try {
            const call = app.queuePrompt(0, 1, { queueNodeIds: sub.targets });
            sub.items = (app.queueItems ?? []).filter((it) => !before.has(it)); // 감싸개가 원래 함수를 바로 부르면 그 안에서 쌓임
            ret = await call;
            // 처리를 기다리지 않고 돌아오는 감싸개(return 누락)·우리 호출 전에 시작된 처리에서는 우리 항목이 아직 큐에 있다 — 그 처리가 끝날 때까지 무장
            if (app.processingQueue) await queueLoopEnd();
        } catch (e) {
            err = e;
        } finally {
            // 무장 동안 쌓였는데 꺼내지지 않은 항목(먼저 꺼낸 항목이 던져 처리 루프가 끝남 등)은 무장이 풀리면 다음 큐 때 토큰과 함께 나간다 — 우리 항목은
            // 사용자 그래프 + 대상 dp_review, 우리 것인지 모르는 항목(원래 함수를 미뤄 부르며 셋째 인자를 버린 감싸개·그동안 누른 큐)은 전체 실행. 모두 뺀다
            const queue = app.queueItems;
            for (let i = Array.isArray(queue) ? queue.length - 1 : -1; i >= 0; i--) {
                if (before.has(queue[i])) continue;
                if (ourItem(sub, queue[i])) left++;
                else stray++;
                queue.splice(i, 1);
            }
            if (stray && !sub.told.has("unsure")) notify("warn", BLOCKED.unsure, 10000);
            // 프론트는 성공한 제출 뒤에만 토큰을 지운다 — 막힌(던짐)·거절된 제출 뒤 남은 토큰이 다음 app.queuePrompt 전에 api.queuePrompt 를
            // 직접 부르는 코드에 실리지 않게(진행 중인 제출이 없을 때만)
            if (!app.processingQueue) {
                delete api.authToken;
                delete api.apiKey;
            }
            studioSubmit = null;
        }
        if (!sub.used) {
            // 제출을 멈춘 확장 알림의 계속 단추(deno Director 의 Apply/Keep 등)는 이 제출을 잇지 않고 무장이 풀린 뒤 app.queuePrompt(0)·
            // (0, 1, [그 노드]) 로 열린 그래프를 토큰과 함께 큐한다 — '알림 확인'으로 이끌지 않음
            let why =
                "ComfyUI 탭이 제출하지 않고 돌아옴 — 다른 확장이 제출을 멈췄거나(Pixaroma 큐 반복 노드의 빈 목록·XY Plot 확인 취소·실행 중, " +
                "deno Ideogram 사전 점검 등) 로그인·작업공간 전환 오류: 다른 확장 알림의 계속·적용 단추는 Studio 승인을 잇지 않고 열린 그래프를 " +
                "실행함 — 승인은 Studio 에서 다시";
            if (err) {
                why =
                    `ComfyUI 탭의 큐 제출이 오류로 끝남(${err.message ?? err}) — 승인 제출도 그 탭에 열린 워크플로를 프롬프트로 바꾼 뒤 내용만 ` +
                    "바꿔치기하므로 그 워크플로가 변환에 실패하면(서브그래프·bypass 된 Get 노드 등) 다시 승인해도 같음: 그 탭에서 다른 워크플로로 바꾼 뒤 다시 승인";
            } else if (ret?.deno_ideogram_director) {
                why =
                    "deno Ideogram Director 의 실행 전 점검(새 JSON 프롬프트 확인 등)이 승인 제출을 멈춤 — 그 노드 알림의 Apply and Replace·" +
                    "Keep Current Board 는 Studio 승인을 잇지 않고 ComfyUI 탭에 열린 그래프를 실행함(그 그래프의 유료 노드는 과금): 그래프를 돌릴 게 " +
                    "아니면 누르지 말고 Director 가 없는 워크플로로 바꾼 뒤 Studio 에서 다시 승인";
            } else if (sub.changed) {
                why = "다른 확장 감싸개가 승인 제출의 대상을 바꿈(셋째 인자를 버림 등 — 사용자 그래프 전체 실행이 되어 막음)";
            } else if (sub.unsure) {
                why =
                    "무장 중 막은 큐 제출이 승인 제출인지 모름 — 다른 확장 감싸개가 원래 함수를 미뤄 부르며 셋째 인자를 버렸으면 승인 제출의 대상이 바뀐 것" +
                    "(사용자 그래프 전체 실행이 되어 막음 — 다시 승인해도 같음)";
            } else if (left || stray) {
                why = "ComfyUI 탭의 큐 처리가 승인 항목을 꺼내기 전에 끝남";
            }
            const gone = `${left ? " — 남은 승인 항목은 큐에서 뺌" : ""}${stray ? " — 무장 중 쌓여 남은 큐 항목(승인 항목인지 모름)은 큐에서 뺌" : ""}`;
            return fail("not_submitted", `제출되지 않음(과금 없음) — ${why}${gone}`);
        }
        const r = await sub.result;
        if (r?.unknown) return fail("unknown", `제출 결과 모름(응답 오류: ${r.error}) — 이미 큐에 들어갔을 수 있음: ComfyUI 큐를 확인`);
        if (!r?.prompt_id) return fail("rejected", `ComfyUI 가 거절함(prompt_id 없음): ${r?.error ?? "응답에 prompt_id 없음"}`);
        studioRuns.set(r.prompt_id, stage);
        const what = typeof d.what === "string" ? d.what.slice(0, 200) : "";
        notify("success", `Studio 승인으로 ${label} 큐${what ? ` — ${what}` : ""}`);
        answer({ ok: true, prompt_id: r.prompt_id });
    } finally {
        studioBusy = false;
    }
}

// 템플릿 실행(노드 키 dp_* — 그래프에 없음)의 wave: 이어 가지 않고 Studio 로 전한다(채널 + 그 프로젝트 승인을 보낸 창들)
function studioWave(detail, node) {
    const rootKey = String(detail.root_key ?? "");
    const remaining = countOf(detail.remaining);
    const msg = { type: "bmk-dp-wave", tab: TAB_ID, rootKey, prompt_id: runningPromptId, node, remaining };
    studioChannel?.postMessage(msg);
    for (const w of waveTo.get(rootKey) ?? []) w.postMessage(msg, window.location.origin);
    // 재굴림 템플릿도 Run 노드 키가 dp_run — 남은 것은 사전 승인분이라 새 승인 없이 이어 감(Studio 의 재굴림 wave 안내와 같게)
    const go = studioRuns.get(runningPromptId) === "reroll" ? "사전 승인 재굴림이라 Studio 머리줄 'ComfyUI 큐'(또는 다음 Run)로 이어서" : "Studio 에서 이어서 승인";
    notify("info", `wave 종료(남은 ${remaining}건) — ${go}`, 10000);
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
        if (typeof window.BroadcastChannel === "function") {
            // opener 가 없는 Studio(즐겨찾기·패널 브라우저)도 이 탭을 찾고 승인을 보내게 — 답은 채널로
            const ch = (studioChannel = new window.BroadcastChannel(HELLO_CHANNEL));
            ch.onmessage = (ev) => {
                const hello = helloReply(ev.data);
                if (hello) ch.postMessage(hello);
                else if (ev.data?.type === APPROVE) studioApprove(ev.data, (m) => ch.postMessage(m), null);
            };
        }
        watchSubmits();
    },

    beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name === RUN_NODE) registerRun(nodeType);
        else if (nodeData.name === DRAFT_NODE) registerDraft(nodeType);
        else if (nodeData.name === REVIEW_NODE) registerReview(nodeType);
    },
});
