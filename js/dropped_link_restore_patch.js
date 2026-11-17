// dropped_link_restore_patch.js — Autogrow 입력이 있는 노드에서 워크플로를 열 때 링크가 빠지는 프론트 버그 우회.
//
// 원인 (프론트 1.59.1):
//   DynamicCombo 안에 Autogrow 묶음이 있는 노드(ByteDanceSeedreamNodeV3 의 model.images 등)는
//   새로 만든 노드의 입력 순서와 한 번 로드된 뒤 저장된 입력 순서가 다르다(model 소켓 위치, Autogrow 칸 수).
//   로드 때 링크는 슬롯 번호대로 먼저 붙고, LGraphNode.configure 의 realignGroupWidgetChildLinks 가
//   이름 기준으로 바로잡는다. 그런데 Autogrow 칸이 2개 이상 연결돼 있으면 묶음 뒤의 자식 위젯 입력
//   (model.seed 등)으로 가는 링크가 Autogrow 칸에 잘못 얹히고, 옮길 자리에는 재정렬 대상에서 빠진
//   Autogrow 링크가 앉아 있어 재정렬이 실패한다(콘솔: Failed to realign input link slots, occupied-target).
//   곧이어 DynamicCombo 값 복원이 자식 입력을 다시 만들면서 그 링크가 끊긴다.
//   그 상태로 저장하면 파일에서도 링크가 빠진다. 서브그래프 안의 노드에서도, 링크가 서브그래프
//   입력에서 올 때도 같다. Comfy-Org/ComfyUI_frontend#10136 과 같은 계열.
//
// 우회: 로드 직전(beforeConfigureGraph)에 저장본의 링크를 "대상 입력 이름"으로 적어 두고,
//   로드 직후(afterConfigureGraph)에 Autogrow 노드의 그 입력이 비어 있고 링크가 사라졌으면
//   원래 id 로 다시 잇는다. afterConfigureGraph 는 변경 추적의 기준 상태를 잡기 전이라
//   파일을 열었을 때 워크플로가 수정됨으로 표시되지 않는다.
//   직렬화 데이터는 configure 가 고쳐 쓰므로 반드시 로드 전에 적어 둔다.
//
// 한계:
//   - 로드 뒤 노드에 없는 입력(자라지 않은 Autogrow 칸 등)으로 가던 링크는 되살리지 못한다.
//   - 링크가 끊길 때 함께 지워진 리라우트 점은 되살리지 않아 그 링크는 직선으로 이어진다
//     (다른 링크가 함께 쓰는 리라우트는 남으므로 다시 걸친다). 링크의 숨김·라벨 표시도 복구하지 않는다.
//   - 로드 중 id 가 다시 발급된 노드(서브그래프와 노드 id 가 겹치던 옛 워크플로)는 건너뛴다.
//   - 로드가 아닌 경로(서브그래프로 변환할 때 링크가 어긋나는 문제 등)는 다루지 않는다.
//
// 상위에서 고쳐지면 이 파일을 삭제할 것(1.61 의 #23042, #23241 이 후보 — 이 경우를 고치는지는 미확인).
//
// 버전 이력:
//   v1 (2026-11): 로드 전 기록 + 로드 뒤 이름 기준 재연결.

import { app } from "../../scripts/app.js";

const _TAG = "[ComfyUI_BMK_Nodes::DroppedLinkRestore]";

const SUBGRAPH_INPUT_ID = -10; // 서브그래프 안에서 입력 노드를 가리키는 링크의 origin_id

let recorded = null;

function recordLinks(data) {
    const nodes = new Map((data.nodes ?? []).map((n) => [String(n.id), n]));
    // 배열 꼴 링크(스키마 0.4)는 리라우트 부모를 extra.linkExtensions 에 따로 둔다.
    const parents = new Map((data.extra?.linkExtensions ?? []).map((e) => [e.id, e.parentId]));
    const links = [];
    for (const l of data.links ?? []) {
        const [id, originId, originSlot, targetId, targetSlot, parentId] = Array.isArray(l)
            ? [...l.slice(0, 5), parents.get(l[0])]
            : [l.id, l.origin_id, l.origin_slot, l.target_id, l.target_slot, l.parentId];
        const target = nodes.get(String(targetId));
        const input = target?.inputs?.[targetSlot];
        // 입력이 가리키지 않는 링크는 파일에 남은 찌꺼기라 프론트가 버리는 것이 맞다.
        if (input?.link !== id) continue;
        links.push({ id, originId, originSlot, targetId, targetType: target.type, name: input.name, parentId });
    }
    return links;
}

function restoreLinks(graph, links) {
    const nodeOf = (id) => graph.getNodeById(id) ?? graph.getNodeById(String(id));
    for (const { id, originId, originSlot, targetId, targetType, name, parentId } of links) {
        const target = nodeOf(targetId);
        if (!target?.comfyDynamic?.autogrow || target.type !== targetType) continue;
        const slot = target.inputs.findIndex((i) => i.name === name);
        // 링크가 다른 칸에 살아 있으면 프론트가 옮긴 것이므로 건드리지 않는다.
        if (slot === -1 || target.inputs[slot].link != null || graph.getLink(id)) continue;
        const input = target.inputs[slot];
        const subgraphInput = Number(originId) === SUBGRAPH_INPUT_ID ? graph.inputNode?.slots[originSlot] : undefined;
        if (subgraphInput) {
            if (!subgraphInput.isValidTarget(input)) continue;
            // 서브그래프 입력에는 끊긴 링크의 id 가 남아 있다.
            const stale = subgraphInput.linkIds.indexOf(id);
            if (stale !== -1) subgraphInput.linkIds.splice(stale, 1);
        }
        // 새 링크가 원래 id 를 받게 해, 되살린 그래프가 저장본과 같아지게 한다.
        const { state } = graph;
        const lastLinkId = state.lastLinkId;
        state.lastLinkId = id - 1;
        // 다른 링크가 함께 써서 남아 있는 리라우트에는 다시 걸친다.
        const afterRerouteId = graph.reroutes.has(parentId) ? parentId : undefined;
        // 노드의 connect 는 출력 슬롯이 없거나 타입이 맞지 않으면 잇지 않는다.
        const link = subgraphInput
            ? subgraphInput.connect(input, target, afterRerouteId)
            : nodeOf(originId)?.connect(originSlot, target, slot, afterRerouteId);
        state.lastLinkId = Math.max(lastLinkId, state.lastLinkId);
        if (link) console.warn(`${_TAG} restored link dropped on load: ${originId}:${originSlot} -> ${targetId} "${name}"`);
    }
}

app.registerExtension({
    name: "BMK.DroppedLinkRestore",
    beforeLoadGraph() {
        recorded = null;
    },
    beforeConfigureGraph(graphData) {
        recorded = [
            [null, recordLinks(graphData)],
            ...(graphData.definitions?.subgraphs ?? []).map((s) => [s.id, recordLinks(s)]),
        ];
    },
    afterConfigureGraph() {
        const graphs = recorded;
        recorded = null;
        if (!graphs) return;
        const root = app.rootGraph;
        for (const [subgraphId, links] of graphs) {
            const graph = subgraphId === null ? root : root.subgraphs.get(subgraphId);
            if (graph) restoreLinks(graph, links);
        }
    },
});
