import { app } from "../../scripts/app.js";

// BMK Group Z-Order (v3)
// 겹쳐진 그룹의 그리기 순서(z-order)를 단축키와 컨텍스트 메뉴로 조정한다.
//
// 원리
// ────
// graph._groups 배열의 순서가 곧 z-order다.
//   - drawGroups() 가 `for (const g of graph._groups) g.draw(...)` 로 순회하므로
//     배열 뒤쪽 = 나중에 그려짐 = 위에 표시.
//   - getGroupOnPos() 는 배열을 뒤에서부터 훑어 첫 히트를 반환하므로,
//     순서를 바꾸면 겹친 영역의 우클릭/선택 대상도 함께 바뀐다.
//     (보기 문제만이 아니라 조작 대상이 바뀐다 — 실사용상 이쪽이 더 크다)
// 그룹은 항상 노드보다 아래에 그려지므로, 그룹끼리 겹칠 때만 의미가 있다.
//
// 조작 방법
// ─────────
// 1) 단축키 — 그룹을 선택(제목 표시줄 클릭)한 뒤:
//      Ctrl+]        한 단계 앞으로
//      Ctrl+[        한 단계 뒤로
//      Ctrl+Shift+]  맨 앞으로
//      Ctrl+Shift+[  맨 뒤로
//    Photoshop/Figma 관례와 같다. ComfyUI 기본 바인딩에서 [ ] 는 쓰이지 않는다.
//    캔버스에 포커스가 있을 때만 동작한다(텍스트 입력 중에는 발동하지 않음).
//
// 2) 컨텍스트 메뉴 — 그룹 영역의 "빈 캔버스"(노드 위가 아닌 곳) 우클릭 →
//    Edit Group ▸ 아래쪽. 확장을 많이 설치했다면 캔버스 메뉴가 길어져
//    Edit Group 이 맨 끝에 있으므로, 평소에는 단축키가 훨씬 빠르다.
//
// 버전 이력
// ─────────
// v3: 단축키(commands + keybindings) 추가. 메뉴와 로직을 공유하도록 정리하고,
//     여러 그룹을 선택한 경우 서로의 상대 순서를 유지하도록 처리 순서를 잡았다.
//
// v2: 메뉴가 아예 뜨지 않던 문제 수정.
//     v1 은 LGraphCanvas.prototype.getGroupMenuOptions 를 패치했는데, 현재
//     프론트엔드에서 이 함수는 호출되지 않는 deprecated 껍데기다:
//
//         getGroupMenuOptions(group) {
//           console.warn("LGraphCanvas.getGroupMenuOptions is deprecated, " +
//                        "use LGraphGroup.getMenuOptions instead");
//           return group.getMenuOptions();
//         }
//
//     실제 메뉴는 processContextMenu 가 LGraphGroup.prototype.getMenuOptions()
//     를 직접 호출해 "Edit Group" 서브메뉴로 붙인다. v1 이 덧붙인 항목은 아무도
//     읽지 않는 함수에 들어 있었으므로 어디에도 나타나지 않았다.
//     v2 는 LGraphGroup.prototype.getMenuOptions 를 패치한다. 위 deprecated
//     껍데기도 결국 이 함수를 호출하므로 구경로 호환은 자동으로 유지된다.

// mode: "front" | "forward" | "backward" | "back"

/** 현재 그래프의 그룹 배열 (없으면 null) */
function getGroups(graph) {
  const groups = graph?._groups;
  return Array.isArray(groups) ? groups : null;
}

/** 그룹 하나를 배열 안에서 이동. 배열만 건드리고 다시 그리기는 하지 않는다. */
function moveGroup(groups, group, mode) {
  const i = groups.indexOf(group);
  if (i === -1) return false;

  groups.splice(i, 1); // 제거 후 인덱스는 줄어든 배열 기준

  if (mode === "front") {
    groups.push(group); // 배열 끝 = 맨 위
  } else if (mode === "back") {
    groups.unshift(group); // 배열 앞 = 맨 아래
  } else if (mode === "forward") {
    groups.splice(Math.min(i + 1, groups.length), 0, group);
  } else {
    groups.splice(Math.max(i - 1, 0), 0, group);
  }
  return true;
}

/** 다시 그리기 + 실행 취소(Ctrl+Z) 스택에 상태 기록. 이동 후 한 번만 호출한다. */
function commit(graph) {
  graph?.setDirtyCanvas?.(true, true);

  // ※ graph.beforeChange()/afterChange() 는 쓰지 않는다 — 현재 프론트엔드에서
  //   canvas.onBeforeChange/onAfterChange 는 아무도 할당하지 않아 그 호출이
  //   조용한 no-op 이 된다(실제로 확인함). 아래가 프론트엔드 자신이 쓰는
  //   경로다. 워크플로우 탭이 열려 있지 않으면 activeWorkflow 가 null 이라
  //   자연히 건너뛴다(그 상태에서는 애초에 되돌릴 것도 없다).
  app.extensionManager?.workflow?.activeWorkflow
    ?.changeTracker?.captureCanvasState?.();
}

/** 선택된 그룹들을 현재 z 순서(배열 순서)대로 정렬해 반환 */
function selectedGroups(canvas, groups) {
  const items = canvas?.selectedItems;
  if (!items?.size) return [];
  return [...items]
    .filter((it) => groups.includes(it))
    .sort((a, b) => groups.indexOf(a) - groups.indexOf(b));
}

/** 단축키용 — 선택된 그룹 전체를 이동 */
function moveSelected(mode) {
  const canvas = app.canvas;
  const graph = canvas?.graph;
  const groups = getGroups(graph);
  if (!groups || groups.length < 2) return;

  const targets = selectedGroups(canvas, groups);
  if (!targets.length) return;

  // 처리 순서가 중요하다. 예컨대 여러 그룹을 "맨 앞으로" 보낼 때 아래쪽부터
  // push 해야 서로의 상대 순서가 그대로 유지된다. 반대 방향은 반대로 돈다.
  const order =
    mode === "front" || mode === "backward" ? targets : [...targets].reverse();

  let changed = false;
  for (const g of order) changed = moveGroup(groups, g, mode) || changed;
  if (changed) commit(graph);
}

app.registerExtension({
  name: "BMK.GroupZOrder",

  commands: [
    {
      id: "BMK.GroupZOrder.BringToFront",
      label: "BMK: 그룹 맨 앞으로 가져오기",
      function: () => moveSelected("front"),
    },
    {
      id: "BMK.GroupZOrder.BringForward",
      label: "BMK: 그룹 한 단계 앞으로",
      function: () => moveSelected("forward"),
    },
    {
      id: "BMK.GroupZOrder.SendBackward",
      label: "BMK: 그룹 한 단계 뒤로",
      function: () => moveSelected("backward"),
    },
    {
      id: "BMK.GroupZOrder.SendToBack",
      label: "BMK: 그룹 맨 뒤로 보내기",
      function: () => moveSelected("back"),
    },
  ],

  // targetElementId 로 캔버스에 한정 — 텍스트 입력 중에는 발동하지 않는다.
  //
  // ⚠ combo.key 는 "누르는 키"가 아니라 "실제로 만들어지는 문자"다.
  //   ComfyUI 는 KeyComboImpl.fromEvent 에서 event.key 를 그대로 쓴다:
  //       fromEvent(e) { return new KeyComboImpl({ key: e.key, ctrl: ..., shift: e.shiftKey }) }
  //   그래서 사용자가 Ctrl+Shift+] 를 누르면 event.key 는 "]" 가 아니라 "}" 다.
  //   Shift 조합을 "]" 로 등록하면 영영 매칭되지 않는다(실제로 확인함).
  keybindings: [
    {
      combo: { key: "}", ctrl: true, shift: true }, // 사용자가 누르는 것: Ctrl+Shift+]
      commandId: "BMK.GroupZOrder.BringToFront",
      targetElementId: "graph-canvas-container",
    },
    {
      combo: { key: "]", ctrl: true },
      commandId: "BMK.GroupZOrder.BringForward",
      targetElementId: "graph-canvas-container",
    },
    {
      combo: { key: "[", ctrl: true },
      commandId: "BMK.GroupZOrder.SendBackward",
      targetElementId: "graph-canvas-container",
    },
    {
      combo: { key: "{", ctrl: true, shift: true }, // 사용자가 누르는 것: Ctrl+Shift+[
      commandId: "BMK.GroupZOrder.SendToBack",
      targetElementId: "graph-canvas-container",
    },
  ],

  setup() {
    const LGraphGroup =
      window.LGraphGroup ??
      window.LiteGraph?.LGraphGroup ??
      app?.canvas?.graph?._groups?.[0]?.constructor;

    const proto = LGraphGroup?.prototype;
    if (typeof proto?.getMenuOptions !== "function") {
      console.warn(
        "[BMK.GroupZOrder] LGraphGroup.prototype.getMenuOptions 를 찾지 못했습니다. " +
          "프론트엔드 구조가 바뀐 것 같습니다 — 컨텍스트 메뉴는 생략하고 단축키만 씁니다."
      );
      return;
    }

    // 중복 패치 방지 (리로드 시 메뉴 항목 누적 방지)
    if (proto.__bmkGroupZOrderPatched) return;
    proto.__bmkGroupZOrderPatched = true;

    const original = proto.getMenuOptions;

    proto.getMenuOptions = function () {
      const options = original.call(this) ?? [];

      const group = this;
      // 서브그래프 안이면 app.canvas.graph 가 현재 보고 있는 그래프다.
      const graph = group.graph ?? app.canvas?.graph;
      const groups = getGroups(graph);

      // 그룹이 하나뿐이면 바꿀 순서가 없다 — 항목을 아예 붙이지 않는다.
      if (!groups || groups.length < 2) return options;

      const i = groups.indexOf(group);
      if (i === -1) return options;

      const atBack = i === 0;
      const atFront = i === groups.length - 1;

      const run = (mode) => {
        // 메뉴를 연 뒤 그래프가 바뀌었을 수 있으므로 이동 시점에 다시 찾는다.
        if (moveGroup(groups, group, mode)) commit(graph);
      };

      options.push(
        null, // 구분선
        { content: "⬆ 맨 앞으로 가져오기 (Ctrl+Shift+])", disabled: atFront, callback: () => run("front") },
        { content: "↑ 한 단계 앞으로 (Ctrl+])", disabled: atFront, callback: () => run("forward") },
        { content: "↓ 한 단계 뒤로 (Ctrl+[)", disabled: atBack, callback: () => run("backward") },
        { content: "⬇ 맨 뒤로 보내기 (Ctrl+Shift+[)", disabled: atBack, callback: () => run("back") }
      );

      return options;
    };

    console.log(
      "[BMK.GroupZOrder] 로드됨 — 단축키 Ctrl+[ / Ctrl+] (+Shift 는 맨 뒤/맨 앞), " +
        "또는 그룹 빈 곳 우클릭 → Edit Group"
    );
  },
});
