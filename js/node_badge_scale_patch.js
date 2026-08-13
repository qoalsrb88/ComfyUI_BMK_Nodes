// node_badge_scale_patch.js — 노드 상단 오버레이(뱃지 / 실행시간) 크기 일괄 축소 패치.
//
// ComfyUI 기본 설정에는 뱃지 표시 모드만 있고 크기 조절 옵션이 없다.
// 노드를 밀집 배치하면 뱃지·실행시간 표시가 위쪽 노드를 가린다.
//
// 두 갈래를 하나의 스케일 값(%)으로 통일해서 축소한다:
//   (A) node.badges 경로 — ID/출처 뱃지 등. LGraphNode.drawBadges 래핑.
//   (B) 노드 상단 오버레이 draw 훅 경로 — 실행시간 표시(0.102s) 등.
//       LGraphCanvas.drawNode 를 래핑하고, 그리기 직전에 해당 노드의
//       onDrawForeground / onDrawTitle 을 "타이틀 바 상단 고정점 축소"
//       래퍼로 임시 교체한 뒤 원복.
//
// (B)를 따로 다루는 이유: 실행시간 표시는 node.badges 에 등록되지 않고
// 자체 draw 훅에서 직접 그려지므로 (A)의 래핑으로는 잡히지 않는다.
// (그래서 뱃지는 우상단, 실행시간은 좌상단에 나오는 위치 차이가 생긴다)
//
// 설정 (Settings → BMK):
//   - "노드 뱃지 크기 (%)"           : 10~100, 기본 50. 두 갈래 공통 스케일.
//   - "상단 오버레이 스케일 적용 대상" : auto / all / off (기본 auto)
//       auto = 훅 소스에 실행시간 관련 식별자가 보이는 경우만 축소(안전).
//       all  = onDrawForeground/onDrawTitle 로 그려지는 모든 것을 축소.
//              auto 로 안 잡히면 all 로 올릴 것. 단, 다른 확장이 노드 상단에
//              그리는 오버레이(프로그레스/커스텀 UI)도 같이 줄어들 수 있음.
//
// 렌더링에만 영향. 직렬화/실행/링크에는 무관.
//
// 버전 이력:
//   v1 (2026-07): drawBadges 래핑으로 node.badges 축소.
//   v2 (2026-07): drawNode 래핑 + draw 훅 스케일링 추가 — 실행시간 표시를
//                 뱃지와 동일 스케일로 통일. 적용 범위 설정(auto/all/off) 추가.

import { app } from "../../scripts/app.js";

const SETTING_SCALE = "BMK.NodeBadgeScale";
const SETTING_OVERLAY = "BMK.NodeBadgeScale.OverlayMode";
const _TAG = "[ComfyUI_BMK_Nodes::NodeBadgeScale]";

// 상단 오버레이가 그려질 수 있는 노드 draw 훅.
const OVERLAY_HOOKS = ["onDrawForeground", "onDrawTitle"];

// auto 모드 판정용 — 훅 함수 소스에 나타나는 실행시간 관련 식별자.
const EXEC_HINT = /execut|elapsed|duration|_time|Time\b/i;

let _badgesPatched = false;
let _drawNodePatched = false;
let _hookNoticed = false;

function getScale() {
    const raw = app.ui?.settings?.getSettingValue?.(SETTING_SCALE, 50);
    const n = Number(raw);
    if (!Number.isFinite(n)) return 1;
    return Math.min(Math.max(n, 10), 100) / 100;
}

function getOverlayMode() {
    return app.ui?.settings?.getSettingValue?.(SETTING_OVERLAY, "auto") ?? "auto";
}

function redraw() {
    app.graph?.setDirtyCanvas?.(true, true);
}

// 프로토타입 체인에서 메서드를 실제로 소유한 객체 찾기
// (전역 노출 여부/프론트엔드 버전 차이에 대한 안전망)
function findOwner(obj, method) {
    let proto = obj;
    while (proto) {
        if (Object.prototype.hasOwnProperty.call(proto, method)) return proto;
        proto = Object.getPrototypeOf(proto);
    }
    return null;
}

// ─── (A) node.badges 축소 ──────────────────────────────────────

// 원본 뱃지를 변형하지 않도록 프로토타입을 공유하는 얕은 클론을 만들어
// 크기 관련 수치만 축소한다 (프레임마다 누적 축소되는 것을 방지).
function scaledClone(badge, scale) {
    const clone = Object.create(Object.getPrototypeOf(badge));
    Object.assign(clone, badge);
    for (const key of ["fontSize", "padding", "cornerRadius"]) {
        if (typeof clone[key] === "number") clone[key] *= scale;
    }
    if (typeof clone.height === "number") {
        clone.height = Math.max(2, clone.height * scale);
    }
    return clone;
}

function patchDrawBadges(proto) {
    if (_badgesPatched) return true;
    if (!proto || typeof proto.drawBadges !== "function") return false;

    const original = proto.drawBadges;
    proto.drawBadges = function (ctx, ...args) {
        const scale = getScale();
        const badges = this.badges;
        if (scale >= 0.999 || !badges?.length) {
            return original.call(this, ctx, ...args);
        }
        // badges 항목은 인스턴스이거나 이를 반환하는 팩토리. 그리는 동안만
        // 축소 클론을 반환하는 팩토리로 교체하고 복원한다.
        try {
            this.badges = badges.map((entry) => () => {
                const inst = typeof entry === "function" ? entry() : entry;
                return inst ? scaledClone(inst, scale) : inst;
            });
            return original.call(this, ctx, ...args);
        } finally {
            this.badges = badges;
        }
    };
    _badgesPatched = true;
    return true;
}

// ─── (B) 상단 오버레이 draw 훅 축소 ────────────────────────────

function shouldScaleHook(fn, mode) {
    if (typeof fn !== "function") return false;
    if (mode === "all") return true;
    try {
        return EXEC_HINT.test(Function.prototype.toString.call(fn));
    } catch {
        return false;
    }
}

// 타이틀 바 상단(y = -titleHeight)을 고정점으로 삼아 축소.
// 캔버스 변환 합성 순서상 translate(0,ay) → scale(s) → translate(0,-ay) 가
// 정확히 "점 (0, ay) 고정 축소"가 된다 (p ↦ s·p + (0, ay(1-s))).
function scaledHook(fn, scale) {
    return function (ctx, ...args) {
        const ay = -(window.LiteGraph?.NODE_TITLE_HEIGHT ?? 30);
        ctx.save();
        ctx.translate(0, ay);
        ctx.scale(scale, scale);
        ctx.translate(0, -ay);
        try {
            return fn.apply(this, [ctx, ...args]);
        } finally {
            ctx.restore();
        }
    };
}

function patchDrawNode(proto) {
    if (_drawNodePatched) return true;
    if (!proto || typeof proto.drawNode !== "function") return false;

    const original = proto.drawNode;
    proto.drawNode = function (node, ctx, ...rest) {
        const mode = getOverlayMode();
        const scale = getScale();
        if (!node || mode === "off" || scale >= 0.999) {
            return original.call(this, node, ctx, ...rest);
        }

        // 훅을 인스턴스 레벨에서 임시 교체 — 확장 로드 순서와 무관하게
        // "그리는 순간"의 최종 훅을 감싸므로 체인 충돌이 없다.
        const saved = [];
        for (const name of OVERLAY_HOOKS) {
            const fn = node[name];
            if (!shouldScaleHook(fn, mode)) continue;
            saved.push([name, Object.prototype.hasOwnProperty.call(node, name), fn]);
            node[name] = scaledHook(fn, scale);
        }

        if (!saved.length && !_hookNoticed && mode === "auto") {
            _hookNoticed = true; // 1회만
            console.debug(
                `${_TAG} auto 모드에서 축소 대상 훅을 찾지 못했습니다. ` +
                    "실행시간 표시가 그대로라면 설정을 'all' 로 바꿔보세요."
            );
        }

        try {
            return original.call(this, node, ctx, ...rest);
        } finally {
            for (const [name, own, fn] of saved) {
                if (own) node[name] = fn;
                else delete node[name];
            }
        }
    };
    _drawNodePatched = true;
    return true;
}

app.registerExtension({
    name: "BMK.NodeBadgeScale",
    settings: [
        {
            id: SETTING_SCALE,
            name: "노드 뱃지 크기 (%)",
            category: ["BMK", "Node Badge", "Scale"],
            tooltip:
                "노드 상단 뱃지(ID/출처)와 실행시간 표시의 렌더링 크기. " +
                "100 = 기본 크기. 밀집 배치 시 위 노드 가림을 줄이려면 축소.",
            type: "slider",
            attrs: { min: 10, max: 100, step: 5 },
            defaultValue: 50,
            onChange: redraw,
        },
        {
            id: SETTING_OVERLAY,
            name: "상단 오버레이 스케일 적용 대상",
            category: ["BMK", "Node Badge", "Overlay"],
            tooltip:
                "실행시간(0.102s) 등 자체 draw 훅으로 그려지는 상단 표시의 축소 범위. " +
                "auto = 실행시간 관련 훅만 / all = 노드 상단에 그려지는 모든 것 / off = 미적용.",
            type: "combo",
            options: ["auto", "all", "off"],
            defaultValue: "auto",
            onChange: redraw,
        },
    ],
    setup() {
        const nodeCtor = window.LGraphNode ?? window.LiteGraph?.LGraphNode;
        if (nodeCtor && patchDrawBadges(findOwner(nodeCtor.prototype, "drawBadges"))) {
            console.log(`${_TAG} drawBadges patched.`);
        }

        const canvasProto = app.canvas
            ? findOwner(Object.getPrototypeOf(app.canvas), "drawNode")
            : null;
        const fallbackProto = window.LGraphCanvas?.prototype
            ? findOwner(window.LGraphCanvas.prototype, "drawNode")
            : null;
        if (patchDrawNode(canvasProto ?? fallbackProto)) {
            console.log(`${_TAG} drawNode patched (overlay scaling enabled).`);
        } else {
            console.warn(
                `${_TAG} drawNode not found — 상단 오버레이 축소 미적용 ` +
                    "(뱃지 축소는 정상 동작)."
            );
        }
    },
    nodeCreated(node) {
        // 안전망: 전역 노출이 없는 프론트엔드 버전에서 인스턴스로 재시도.
        if (_badgesPatched) return;
        patchDrawBadges(findOwner(node, "drawBadges"));
    },
});
