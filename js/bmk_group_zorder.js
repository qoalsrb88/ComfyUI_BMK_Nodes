import { app } from "../../scripts/app.js";

// BMK Group Z-Order
// 겹쳐진 그룹의 그리기 순서(z-order)를 우클릭 메뉴로 조정한다.
// graph._groups 배열에서 뒤쪽 = 나중에 그려짐 = 위에 표시.
// (그룹은 항상 노드보다 아래에 그려지므로, 그룹끼리 겹칠 때만 효과가 있다.)

app.registerExtension({
  name: "BMK.GroupZOrder",

  setup() {
    // LGraphCanvas 프로토타입 확보 (모듈 환경 대비 폴백 체인)
    const LGC = window.LGraphCanvas || app?.canvas?.constructor;
    const proto = LGC?.prototype;
    if (!proto) {
      console.warn("[BMK.GroupZOrder] LGraphCanvas prototype을 찾지 못함. 로드 중단.");
      return;
    }

    // 중복 패치 방지 (업데이트/리로드 시 메뉴 항목 누적 방지)
    if (proto.__bmkGroupZOrderPatched) return;
    proto.__bmkGroupZOrderPatched = true;

    const original = proto.getGroupMenuOptions;

    proto.getGroupMenuOptions = function (group) {
      // 원본 그룹 메뉴(Edit Group, Remove 등)를 먼저 가져온다
      const options = original ? original.call(this, group) : [];

      const canvas = this;
      const graph = canvas.graph;

      const getGroups = () => graph?._groups || graph?.groups || [];

      const redraw = () => {
        canvas.setDirty?.(true, true);
        graph?.setDirtyCanvas?.(true, true);
        // 워크플로우 변경 플래그 (저장 안 함 표시용, 버전 따라 없을 수 있음)
        graph?.change?.();
      };

      // mode: "front" | "forward" | "backward" | "back"
      const move = (mode) => {
        const groups = getGroups();
        const i = groups.indexOf(group);
        if (i === -1) return;

        groups.splice(i, 1); // 일단 제거 (이후 인덱스는 제거된 배열 기준)

        if (mode === "front") {
          groups.push(group); // 배열 끝 = 맨 위
        } else if (mode === "back") {
          groups.unshift(group); // 배열 앞 = 맨 아래
        } else if (mode === "forward") {
          // 다음 그룹과 swap → 한 단계 위로
          groups.splice(Math.min(i + 1, groups.length), 0, group);
        } else if (mode === "backward") {
          // 이전 그룹과 swap → 한 단계 아래로
          groups.splice(Math.max(i - 1, 0), 0, group);
        }

        redraw();
      };

      options.push(
        null, // 구분선
        { content: "⬆ 맨 앞으로 가져오기", callback: () => move("front") },
        { content: "↑ 한 단계 앞으로", callback: () => move("forward") },
        { content: "↓ 한 단계 뒤로", callback: () => move("backward") },
        { content: "⬇ 맨 뒤로 보내기", callback: () => move("back") }
      );

      return options;
    };

    console.log("[BMK.GroupZOrder] 그룹 z-order 메뉴 로드됨");
  },
});
