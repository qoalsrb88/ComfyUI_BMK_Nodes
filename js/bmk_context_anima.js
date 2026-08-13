// bmk_context_anima.js — BMKContextAnima 짝 JS 확장 (cosmetic 전용)
//
// 1) 출력 라벨을 공백(" ")으로 바꿔 노드 가로폭을 절반 수준으로 축소.
//    같은 행 = 같은 키 규칙이므로 왼쪽 입력 이름만으로 식별 가능.
//    링크/직렬화는 슬롯 인덱스와 name 기반이라 label 변경은 무해.
// 2) properties["bmk_ctx_schema"]에 스키마 버전 각인 — 훗날 포트 재배열이
//    필요해질 때 링크 마이그레이션(구버전 → 신버전 슬롯 리매핑)의 기준값.
//
// 변형 노드(BMKContextFlux2Klein / BMKContextKrea2 등)를 추가하면
// NODE_CLASSES에 클래스명만 등록하면 동일하게 적용된다.

import { app } from "../../scripts/app.js";

const NODE_CLASSES = new Set(["BMKContextAnima"]);
const SCHEMA_VERSION = 1; // Python 쪽 SCHEMA_VERSION과 일치시킬 것

function applyCosmetics(node) {
  if (node?.outputs) {
    for (const out of node.outputs) {
      // 빈 문자열("")은 일부 프론트엔드 버전에서 falsy 처리되어 name으로
      // 폴백 렌더링되므로, 공백 한 칸(" ")을 사용한다.
      out.label = " ";
    }
  }
  node.properties = node.properties || {};
  if (node.properties["bmk_ctx_schema"] == null) {
    node.properties["bmk_ctx_schema"] = SCHEMA_VERSION;
  }
}

app.registerExtension({
  name: "BMK.ContextAnima",
  beforeRegisterNodeDef(nodeType, nodeData) {
    if (!NODE_CLASSES.has(nodeData.name)) return;

    const onNodeCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const r = onNodeCreated?.apply(this, arguments);
      applyCosmetics(this);
      // 라벨 축소를 반영해 폭 재계산 (신규 생성 시에만; 세로는 계산값 유지)
      const sz = this.computeSize();
      this.setSize([sz[0], Math.max(sz[1], this.size?.[1] ?? 0)]);
      return r;
    };

    const onConfigure = nodeType.prototype.onConfigure;
    nodeType.prototype.onConfigure = function () {
      const r = onConfigure?.apply(this, arguments);
      // 저장본 로드 시에도 재적용. 사용자가 저장한 크기는 건드리지 않는다.
      applyCosmetics(this);
      return r;
    };
  },
});
