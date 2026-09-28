// legacy_widget_width_patch.js — 속성 패널을 연 뒤 커스텀/DOM 위젯 폭이 고정되는 프론트 버그 우회.
//
// 원인 (프론트 1.53.6, 상위 main 도 동일):
//   속성 패널과 앱 모드는 전용 Vue 컴포넌트가 없는 위젯 타입(커스텀 위젯, DOM 위젯)을
//   WidgetLegacy.vue 로 그린다. 이 컴포넌트는 draw 할 때마다 캔버스 노드의 실제 위젯에
//   `widget.width = 패널 캔버스 폭` 을 써 넣고 되돌리지 않는다. LiteGraph 는 그리기,
//   클릭 판정, DOM 오버레이 크기에 모두 `widget.width || node.width` 를 쓰기 때문에
//   이때부터 위젯이 패널 폭에 고정된다(노드를 줄이면 튀어나오고, 늘리면 빈 공간이 남는다).
//   패널을 닫아도 값이 남아서, 워크플로를 다시 열어 위젯을 새로 만들 때까지 유지된다.
//   Comfy-Org/ComfyUI_frontend#12443 / 수정 PR #15331 (2026-09 기준 미병합).
//
// 우회: 위젯마다 width 를 접근자로 바꾼다. LiteGraph 모드에서 생성 이후에 들어온 쓰기는
//   그 동기 작업(WidgetLegacy 의 draw 호출) 동안에만 보이고 다음 마이크로태스크에서 사라진다.
//   PR #15331 이 WidgetLegacy 안에서 하는 "그리는 동안만 적용하고 복원"과 같은 효과다.
//   생성 시점의 width 와 Vue 노드 모드에서의 쓰기는 지금처럼 유지된다.
//   width 는 BaseWidget 의 클래스 필드(인스턴스 자체 속성)라 프로토타입 접근자로는 못 잡는다.
//
// 한계: 서브그래프 호스트의 승격 위젯은 SubgraphNode.addCustomWidget 경로라 덮지 않는다.
//
// 상위에서 고쳐지면 이 파일을 삭제할 것.
//
// 버전 이력:
//   v1 (2026-09): addCustomWidget 래핑 + width 접근자.

import { app } from "../../scripts/app.js";

const _TAG = "[ComfyUI_BMK_Nodes::LegacyWidgetWidth]";

const guarded = new WeakSet();

function guardWidth(widget) {
    if (!widget || guarded.has(widget)) return;
    const desc = Object.getOwnPropertyDescriptor(widget, "width");
    // 다른 확장이 이미 접근자로 바꿔 둔 위젯은 건드리지 않는다.
    if (desc?.get || desc?.set) return;
    guarded.add(widget);

    let width = desc?.value;
    let vueWidth;
    let hostWidth;
    Object.defineProperty(widget, "width", {
        configurable: true,
        enumerable: true,
        get() {
            if (hostWidth !== undefined) return hostWidth;
            return LiteGraph.vueNodesMode ? (vueWidth ?? width) : width;
        },
        set(v) {
            if (LiteGraph.vueNodesMode) {
                vueWidth = v;
                return;
            }
            hostWidth = v;
            queueMicrotask(() => {
                hostWidth = undefined;
            });
        },
    });
}

const addCustomWidget = LiteGraph.LGraphNode.prototype.addCustomWidget;
LiteGraph.LGraphNode.prototype.addCustomWidget = function (widget) {
    const concrete = addCustomWidget.call(this, widget);
    guardWidth(concrete);
    return concrete;
};
console.log(`${_TAG} addCustomWidget patched.`);

app.registerExtension({
    name: "BMK.LegacyWidgetWidth",
    // addCustomWidget 을 거치지 않고 node.widgets 에 직접 넣은 위젯용.
    nodeCreated(node) {
        for (const w of node.widgets ?? []) guardWidth(w);
    },
});
