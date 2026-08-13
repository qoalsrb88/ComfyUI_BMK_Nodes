/**
 * ImpactWildcard ResizePatch
 * ---------------------------------------------------------------
 * Impact Pack의 ImpactWildcardProcessor / ImpactWildcardEncode 노드에서
 * 상단(wildcard_text)과 하단(populated_text) 텍스트 영역의 높이 비율을
 * 자유롭게 조절할 수 있도록 합니다.
 *
 * 동작 원리:
 *  - ComfyUI의 multiline 위젯은 노드 내 가용 세로 공간을 균등 분배함
 *  - 두 위젯의 computeSize() 반환값을 비율에 맞게 오버라이드해서
 *    minimum height를 다르게 만들면, 분배 비율이 그 비율을 따름
 *  - 비율은 node.properties.preview_ratio (0.1 ~ 0.9) 에 저장되어
 *    워크플로우와 함께 영구 저장됨
 * ---------------------------------------------------------------
 */

import { app } from "../../scripts/app.js";

// 적용 대상 노드 클래스
const TARGET_CLASSES = new Set([
    "ImpactWildcardProcessor",
    "ImpactWildcardEncode",
]);

// 슬라이더 설정
const RATIO_MIN = 0.15;   // 상단 영역이 차지하는 최소 비율
const RATIO_MAX = 0.85;   // 상단 영역이 차지하는 최대 비율
const RATIO_DEFAULT = 0.5;
const RATIO_STEP = 0.01;

// 멀티라인 위젯의 최소 높이 (노드를 매우 축소했을 때 보장값)
const MIN_TEXT_HEIGHT = 40;

// 노드 헤더 + 출력 영역 + 아래 패딩의 대략적 합산 픽셀
const NODE_HEADER_FOOTER = 50;

// 멀티라인 외 위젯 1개당 차지하는 대략적인 높이 (간격 포함)
const PER_WIDGET_HEIGHT = 26;

app.registerExtension({
    name: "Comfy.ImpactWildcard.ResizePatch",

    async nodeCreated(node) {
        if (!TARGET_CLASSES.has(node.comfyClass)) return;

        // ───────────────────────────────────────────────────────
        // 1) 비율 속성 초기화 (워크플로우 저장 시 함께 보존됨)
        // ───────────────────────────────────────────────────────
        if (!node.properties) node.properties = {};
        if (typeof node.properties.preview_ratio !== "number" ||
            isNaN(node.properties.preview_ratio)) {
            node.properties.preview_ratio = RATIO_DEFAULT;
        }
        // 범위 보정
        node.properties.preview_ratio = clamp(
            node.properties.preview_ratio, RATIO_MIN, RATIO_MAX
        );

        // ───────────────────────────────────────────────────────
        // 2) 대상 multiline 위젯 두 개를 이름으로 찾기
        //    (Impact Pack의 INPUT_TYPES에서 이름이 명확히 정의됨)
        // ───────────────────────────────────────────────────────
        const wildcardWidget  = node.widgets.find(w => w.name === "wildcard_text");
        const populatedWidget = node.widgets.find(w => w.name === "populated_text");

        if (!wildcardWidget || !populatedWidget) {
            console.warn(
                "[ImpactWildcard-ResizePatch] target widgets not found on node",
                node.comfyClass
            );
            return;
        }

        // ───────────────────────────────────────────────────────
        // 3) 두 위젯의 computeSize 오버라이드 (★ 핵심 ★)
        //    - 기존: 고정값 반환 → 노드를 늘려도 위젯이 안 늘어남
        //    - 신규: 노드 현재 높이에서 다른 위젯/헤더를 뺀 "가용 공간"을
        //            계산하고, 그 가용 공간을 비율대로 분배
        //    - 따라서 노드를 늘이면 두 위젯도 비율 유지하며 함께 늘어남
        // ───────────────────────────────────────────────────────
        const originalCompute1 = wildcardWidget.computeSize?.bind(wildcardWidget);
        const originalCompute2 = populatedWidget.computeSize?.bind(populatedWidget);

        wildcardWidget.computeSize = function (width) {
            const ratio = getRatio(node);
            const total = calcMultilineSpace(node, wildcardWidget, populatedWidget);
            return [width, Math.max(MIN_TEXT_HEIGHT, total * ratio)];
        };

        populatedWidget.computeSize = function (width) {
            const ratio = getRatio(node);
            const total = calcMultilineSpace(node, wildcardWidget, populatedWidget);
            return [width, Math.max(MIN_TEXT_HEIGHT, total * (1 - ratio))];
        };

        // ───────────────────────────────────────────────────────
        // 4) 비율 조절 슬라이더 위젯 추가
        //    - 다음 프레임으로 지연시켜 Impact Pack의 인덱스 기반
        //      위젯 접근이 모두 끝난 뒤에 추가되도록 함 (충돌 방지)
        // ───────────────────────────────────────────────────────
        let ratioWidget = null;

        const installSlider = () => {
            ratioWidget = node.addWidget(
                "slider",
                "↕ preview ratio",
                node.properties.preview_ratio,
                function (value) {
                    const v = clamp(value, RATIO_MIN, RATIO_MAX);
                    node.properties.preview_ratio = v;
                    this.value = v;

                    // 노드 크기 재계산 트리거
                    requestAnimationFrame(() => {
                        const cur = node.size;
                        node.setSize([cur[0], cur[1]]);
                        node.setDirtyCanvas(true, true);
                    });
                },
                {
                    min: RATIO_MIN,
                    max: RATIO_MAX,
                    step: RATIO_STEP * 10,
                    precision: 2,
                }
            );

            // 워크플로우에는 properties로만 저장되도록
            ratioWidget.serializeValue = () => node.properties.preview_ratio;

            // 슬라이더 추가 후 비율 즉시 반영
            const cur = node.size;
            node.setSize([cur[0], cur[1]]);
            node.setDirtyCanvas(true, true);
        };

        // Impact Pack이 인덱스 기반 위젯 처리를 다 끝낸 뒤에 슬라이더 추가
        requestAnimationFrame(installSlider);

        // ───────────────────────────────────────────────────────
        // 4-1) 노드 리사이즈 시 캔버스 재드로우 강제
        //      computeSize가 매 프레임 재호출되도록 dirty 마크
        // ───────────────────────────────────────────────────────
        const originalOnResize = node.onResize;
        node.onResize = function (size) {
            if (originalOnResize) originalOnResize.call(this, size);
            // 다음 프레임에 재드로우 → computeSize 다시 호출 → 새 크기 반영
            this.setDirtyCanvas(true, true);
        };

        // ───────────────────────────────────────────────────────
        // 5) 워크플로우 로드 후에도 비율이 잘 적용되도록 보정
        // ───────────────────────────────────────────────────────
        const originalConfigure = node.onConfigure;
        node.onConfigure = function (info) {
            if (originalConfigure) originalConfigure.call(this, info);
            if (typeof this.properties.preview_ratio === "number") {
                if (ratioWidget) {
                    ratioWidget.value = clamp(
                        this.properties.preview_ratio, RATIO_MIN, RATIO_MAX
                    );
                }
            }
            requestAnimationFrame(() => {
                const cur = this.size;
                this.setSize([cur[0], cur[1]]);
                this.setDirtyCanvas(true, true);
            });
        };
    },
});

// ─── 유틸 ──────────────────────────────────────────────────────
function clamp(v, lo, hi) {
    return Math.max(lo, Math.min(hi, v));
}
function getRatio(node) {
    const r = node?.properties?.preview_ratio;
    return (typeof r === "number" && !isNaN(r))
        ? clamp(r, RATIO_MIN, RATIO_MAX)
        : RATIO_DEFAULT;
}

/**
 * 노드 내에서 두 멀티라인 위젯이 사용할 수 있는 총 세로 공간을 계산.
 *
 * 가용공간 = 노드 현재 높이 − (헤더/푸터 + 기타 위젯들의 높이 합)
 *
 * 이 값이 node.size[1] 변화에 따라 달라지기 때문에, 노드를 늘리면
 * 두 위젯의 computeSize 결과도 함께 늘어나서 자연스럽게 채워집니다.
 */
function calcMultilineSpace(node, wcW, popW) {
    // 노드 크기가 아직 정해지지 않은 초기 단계에는 안전한 디폴트
    if (!node.size || !node.size[1] || node.size[1] < 100) {
        return MIN_TEXT_HEIGHT * 4;  // 디폴트 가용 공간
    }

    // 멀티라인 외 위젯들의 실제 높이를 합산
    // (가능하면 computedHeight를, 없으면 추정치 사용)
    let otherHeight = 0;
    for (const w of node.widgets || []) {
        if (w === wcW || w === popW) continue;
        const h = (typeof w.computedHeight === "number" && w.computedHeight > 0)
            ? w.computedHeight
            : PER_WIDGET_HEIGHT;
        otherHeight += h + 4;  // +4 = 위젯간 간격
    }

    // 가용 공간 = 노드 전체 높이 − (헤더+푸터) − 기타 위젯 합산
    const available = node.size[1] - NODE_HEADER_FOOTER - otherHeight;
    return Math.max(MIN_TEXT_HEIGHT * 2, available);
}
