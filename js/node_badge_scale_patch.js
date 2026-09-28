// node_badge_scale_patch.js — 노드 상단 오버레이(뱃지 / 실행시간) 크기 일괄 축소 패치.
//
// ComfyUI 기본 설정에는 뱃지 표시 모드만 있고 크기 조절 옵션이 없다.
// 노드를 밀집 배치하면 뱃지·실행시간 표시가 위쪽 노드를 가린다.
//
// 두 갈래를 하나의 스케일 값(%)으로 통일해서 축소한다:
//   (A) 뱃지 줄 — ID/출처/크레딧 뱃지 + node.badges. LGraphNode.drawBadges 래핑.
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
// 캔버스(LiteGraph) 노드 전용 — Vue 노드 모드의 뱃지는 DOM 이라 적용되지 않는다.
//
// 버전 이력:
//   v1 (2026-07): drawBadges 래핑으로 node.badges 축소.
//   v2 (2026-07): drawNode 래핑 + draw 훅 스케일링 추가 — 실행시간 표시를
//                 뱃지와 동일 스케일로 통일. 적용 범위 설정(auto/all/off) 추가.
//   v3 (2026-09): 프론트 1.53 대응. ID/출처 뱃지가 node.badges 에서 빠져
//                 내부 badge rows provider 로 옮겨가면서 v1 의 뱃지 클론 방식이
//                 아무것도 잡지 못하게 됨 → drawBadges 전체를 캔버스 변환으로 축소.
//                 설정 읽기에서 deprecated 된 기본값 인자 제거(프레임마다 경고 폭주).

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

// 프론트 1.53 의 getSettingValue(id, default) 는 기본값 인자를 받으면 호출마다
// console.warn 을 찍는다. draw 경로에서 노드마다 불리므로 인자 없이 읽는다.
function readSetting(id, fallback) {
    return (
        app.extensionManager?.setting?.get?.(id) ??
        app.ui?.settings?.getSettingValue?.(id) ??
        fallback
    );
}

function getScale() {
    const n = Number(readSetting(SETTING_SCALE, 50));
    if (!Number.isFinite(n)) return 1;
    return Math.min(Math.max(n, 10), 100) / 100;
}

function getOverlayMode() {
    return readSetting(SETTING_OVERLAY, "auto");
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

// ─── (A) 뱃지 줄 축소 ──────────────────────────────────────────

// 프론트 1.53 부터 ID/출처/크레딧 뱃지는 node.badges 가 아니라 모듈 비공개
// badge rows provider 가 만들어 drawBadges 안에서 node.badges 와 합쳐진다.
// 개별 뱃지 객체에 손댈 수 없으므로 drawBadges 호출 전체를 변환으로 축소한다.
//
// drawBadges 는 줄의 우하단 모서리를 (width - gap, -(TH + gap)) 에 맞춰 그린다.
// gap 을 g/s 로 넘겨 그린 뒤 그 모서리 A 를 원래 g 기준 모서리 B 로 옮기면
// (p ↦ B + s·(p − A)) 축소 후에도 뱃지 사이·타이틀 바와의 간격이 g 로 유지된다.
function badgeTransform(node, opts, scale) {
    const g = typeof opts?.gap === "number" ? opts.gap : 2;
    const th = window.LiteGraph?.NODE_TITLE_HEIGHT ?? 30;
    const w = node.width;
    return {
        gap: g / scale,
        ax: w - g / scale,
        ay: -(th + g / scale),
        bx: w - g,
        by: -(th + g),
    };
}

// 클릭 판정은 node.badges 인스턴스 항목의 boundingRect(노드 로컬 좌표)를 쓴다.
// 변환 전 좌표로 기록되므로 이번 draw 에서 갱신된 것만 같은 변환으로 옮긴다.
// (팩토리 항목은 매번 새 객체라 판정에 쓰이지 않음)
function snapshotHitRects(badges) {
    const snaps = [];
    for (const b of badges ?? []) {
        const r = typeof b === "function" ? null : b?.boundingRect;
        if (r?.length >= 4) snaps.push([r, r[0], r[1], r[2], r[3]]);
    }
    return snaps;
}

function remapHitRects(snaps, s, t) {
    for (const [r, x, y, w, h] of snaps) {
        if (r[0] === x && r[1] === y && r[2] === w && r[3] === h) continue;
        r[0] = t.bx + s * (r[0] - t.ax);
        r[1] = t.by + s * (r[1] - t.ay);
        r[2] *= s;
        r[3] *= s;
    }
}

function patchDrawBadges(proto) {
    if (_badgesPatched) return true;
    if (!proto || typeof proto.drawBadges !== "function") return false;

    const original = proto.drawBadges;
    proto.drawBadges = function (ctx, opts, ...rest) {
        const scale = getScale();
        if (scale >= 0.999) return original.call(this, ctx, opts, ...rest);

        const t = badgeTransform(this, opts, scale);
        const snaps = snapshotHitRects(this.badges);
        ctx.save();
        ctx.translate(t.bx, t.by);
        ctx.scale(scale, scale);
        ctx.translate(-t.ax, -t.ay);
        try {
            const ret = original.call(this, ctx, { ...opts, gap: t.gap }, ...rest);
            remapHitRects(snaps, scale, t);
            return ret;
        } finally {
            ctx.restore();
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
