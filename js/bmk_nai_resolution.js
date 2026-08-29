// js/bmk_nai_resolution.js  (v4)
// BMKNaiResolution 프론트엔드 확장
//
// 기능:
//   1) 노드 하단에 계산 결과 readout 위젯을 붙인다. 위젯을 세 축으로 쪼갠 대가로
//      "최종 해상도가 한눈에 안 보이는" 문제가 생기므로 이걸로 메운다.
//      Opus 무료 한도 초과 여부를 색 있는 뱃지로 함께 띄운다.
//   2) 현재 mode 에서 의미 없는 위젯을 회색 처리한다. 숨기지 않고 회색으로 두는
//      이유는 노드 높이가 바뀌면 배치가 튀기 때문이다.
//   3) (v2) 이 노드를 품은 서브그래프 호스트 노드에도 같은 readout 을 그린다.
//   4) (v3) 겉모습 튜닝 값을 STYLE 블록 하나로 모았다. 호스트 오버레이는 배경을
//      거의 불투명하게 채우고 색은 호스트 노드의 body 색을 따라간다.
//   5) (v4) 위젯을 서브그래프 밖으로 승격해도 readout 이 실시간으로 따라간다.
//
// 겉모습을 바꾸고 싶으면 아래 "표시 튜닝" 의 STYLE 만 고치면 된다. draw 코드는
// 손댈 필요가 없다.
//
// 승격(promotion) 대응에 대해 (v4)
// ────────────────────────────────
// 위젯을 서브그래프 밖으로 승격하면 내부 노드의 그 위젯은 **입력 소켓으로 전환**
// 되고, 값의 정본이 호스트 노드의 위젯으로 옮겨간다. 내부 위젯의 .value 는 승격
// 시점 값에 그대로 얼어붙는다. v3 까지는 내부 .value 만 읽어서 readout 이 멈췄다.
//
// v4 는 값을 읽을 때 승격 경계를 넘어간다:
//
//   내부 입력이 연결돼 있나?
//     └ 아니오 → 내부 위젯 값 (평범한 상태)
//     └ 예 → 링크 출처가 서브그래프 입력 노드인가?
//              └ 아니오 → 내부 실노드에서 오는 진짜 데이터 링크. 값 미지.
//              └ 예 → 호스트의 같은 이름 입력이 또 연결돼 있나?
//                       └ 예 → 바깥에서 주입. 값 미지.
//                       └ 아니오 → 호스트 위젯 값 ★ 이게 정본
//
// 그리고 readout 값을 캐시하지 않고 매 draw 마다 새로 계산한다. 호스트 위젯이
// 바뀔 때 내부 노드의 위젯 callback 은 호출되지 않으므로, 콜백에만 의존하면
// 어차피 갱신 시점을 놓친다.
//
// 구현 주의:
//   - readout 위젯은 widgets 배열 "맨 뒤"에만 붙이고 serialize 를 끈다. 중간에
//     끼우면 저장된 워크플로의 widgets_values 인덱스가 한 칸씩 밀린다.
//     (bmk_cyclic_seed.js 의 버튼 위젯과 같은 규칙)
//   - 64 스냅은 파이썬과 동일하게 floor(x/64 + 0.5) 다. JS Math.round 는
//     .5 를 항상 올리므로 우연히 같은 결과가 나오지만, 음수/부동소수 경계에서
//     갈릴 수 있어 파이썬 쪽 식을 그대로 옮겼다.
//   - 승격 해석은 한 단계만 본다. 서브그래프를 두 겹 이상 중첩하고 그 위로 다시
//     승격하면 "값 미지"로 떨어진다 (프론트엔드 자체도 이 구간이 불안정하다).

import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const NODE_CLASS = "BMKNaiResolution";
const READOUT_NAME = "bmk_readout";
const TABLE_URL = "/bmk/nai_resolution/table";

const FONT = "Arial, sans-serif";
const ROW_HEIGHT = 24;

// 서브그래프 탐색 깊이 상한 / 실패 시 재탐색 간격(ms).
const MAX_DEPTH = 4;
const RESCAN_MS = 500;

// ═══ 표시 튜닝 ═══════════════════════════════════════════════════
//
// opacity 는 배경 채움의 알파다. 0 = 완전 투명, 1 = 완전 불투명.
//
// 호스트 오버레이는 노드 바깥에 떠 있어서 밝은 그룹 배경이나 그 위를 지나는
// 링크가 그대로 비쳐 글자가 묻힌다. 그래서 기본을 거의 불투명하게 잡았다.
// 링크를 완전히 가리려면 1.0, 반투명하게 살짝 비치게 하려면 0.7 근처.
//
// 노드 본문 안 위젯은 이미 노드 배경 위에 얹히므로 흰색을 옅게 깐 인셋 느낌이
// 자연스럽다. 여기 opacity 를 올리면 도리어 위젯이 떠 보인다.
const STYLE = {
    // 서브그래프 호스트 노드 하단 오버레이
    host: {
        opacity: 0.94,
        borderOpacity: 0.30,
        followNodeColor: true,      // 호스트 노드 body 색(bgcolor)을 배경으로 사용
        fallbackColor: "#353535",   // 노드 색을 못 읽었을 때
        under: "canvas",            // 뒤에 깔린 것 — 글자색 대비 판단에 쓴다
        gap: 4,                     // 노드 하단과의 간격(px)
    },
    // 노드 본문 안에 붙는 readout 위젯
    widget: {
        opacity: 0.06,
        borderOpacity: 0.09,
        followNodeColor: false,
        fallbackColor: "#ffffff",
        under: "node",
        gap: 0,
    },
};

// under: "canvas" 일 때 뒤에 깔렸다고 가정할 색. 캔버스 배경값이다.
const CANVAS_BG = "#222222";

// 합성된 배경이 이보다 밝으면 글자·뱃지를 어두운 쪽으로 뒤집는다 (상대 휘도 0~1).
const LIGHT_BG_THRESHOLD = 0.5;

const TEXT_COLOR = { onDark: "#cfcfcf", onLight: "#2a2a2a" };

const BADGE_STYLE = {
    free: {
        bg: "rgba(106,168,79,0.22)", fg: "#8fce6b",
        bgOnLight: "rgba(60,120,40,0.18)", fgOnLight: "#2f6b1c",
    },
    anlas: {
        bg: "rgba(230,179,75,0.20)", fg: "#e6b34b",
        bgOnLight: "rgba(150,110,20,0.18)", fgOnLight: "#7a5710",
    },
    "?": {
        bg: "rgba(255,255,255,0.07)", fg: "#999",
        bgOnLight: "rgba(0,0,0,0.07)", fgOnLight: "#555",
    },
};

// ═══ 색 유틸 ═════════════════════════════════════════════════════

const _HEX3 = /^#([0-9a-f])([0-9a-f])([0-9a-f])$/i;
const _HEX6 = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i;
const _RGB_FN = /^rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)/i;

/** "#abc" / "#aabbcc" / "rgb(..)" / "rgba(..)" → {r,g,b}. 실패하면 null. */
function parseColor(css) {
    if (typeof css !== "string") return null;
    const s = css.trim();

    let m = _HEX6.exec(s);
    if (m) {
        return {
            r: parseInt(m[1], 16),
            g: parseInt(m[2], 16),
            b: parseInt(m[3], 16),
        };
    }
    m = _HEX3.exec(s);
    if (m) {
        return {
            r: parseInt(m[1] + m[1], 16),
            g: parseInt(m[2] + m[2], 16),
            b: parseInt(m[3] + m[3], 16),
        };
    }
    m = _RGB_FN.exec(s);
    if (m) return { r: +m[1], g: +m[2], b: +m[3] };

    return null;
}

function rgba(c, alpha) {
    return `rgba(${Math.round(c.r)}, ${Math.round(c.g)}, ${Math.round(c.b)}, ${alpha})`;
}

/** 상대 휘도 (0=검정, 1=흰색). 대비 판단용이라 sRGB 가중 평균으로 충분하다. */
function luminance(c) {
    return (0.2126 * c.r + 0.7152 * c.g + 0.0722 * c.b) / 255;
}

function nodeBodyColor(node) {
    return (
        parseColor(node?.bgcolor) ||
        parseColor(node?.constructor?.bgcolor) ||
        parseColor(globalThis.LiteGraph?.NODE_DEFAULT_BGCOLOR)
    );
}

/** 배경 채움에 쓸 색. */
function baseColorOf(style, node) {
    if (style.followNodeColor) {
        const c = nodeBodyColor(node);
        if (c) return c;
    }
    return parseColor(style.fallbackColor) || { r: 53, g: 53, b: 53 };
}

/** 오버레이 뒤에 깔려 있다고 볼 색. 합성 결과의 밝기를 재는 데 쓴다. */
function underColorOf(style, node) {
    if (style.under === "node") {
        const c = nodeBodyColor(node);
        if (c) return c;
    }
    return parseColor(CANVAS_BG) || { r: 34, g: 34, b: 34 };
}

/**
 * base 를 under 위에 alpha 로 얹었을 때의 실제 색.
 *
 * 글자색을 base 만 보고 정하면 안 된다 — 위젯은 base 가 흰색(#ffffff)이지만
 * alpha 0.06 이라 실제로는 어두운 노드 배경에 가깝다. 합성 후를 봐야 맞다.
 */
function compositeColor(base, under, alpha) {
    const a = Math.max(0, Math.min(1, alpha));
    return {
        r: base.r * a + under.r * (1 - a),
        g: base.g * a + under.g * (1 - a),
        b: base.b * a + under.b * (1 - a),
    };
}

// ═══ 테이블 로딩 ═════════════════════════════════════════════════

let TABLE = null;
let TABLE_PROMISE = null;

function ensureTable() {
    if (TABLE) return Promise.resolve(TABLE);
    if (TABLE_PROMISE) return TABLE_PROMISE;

    TABLE_PROMISE = (async () => {
        try {
            const res = await api.fetchApi(TABLE_URL);
            if (!res.ok) throw new Error("HTTP " + res.status);
            TABLE = await res.json();
        } catch (err) {
            console.warn(
                "[BMK NAI Resolution] 해상도 테이블을 받지 못했습니다. " +
                "readout 은 값 요약만 표시합니다.",
                err
            );
            TABLE = null;
        }
        return TABLE;
    })();

    return TABLE_PROMISE;
}

// ═══ 계산 (파이썬 _preset_dims / _scale_pair 미러) ════════════════

function snap(value, rounding, unit) {
    if (!(value > 0)) return unit;
    const n =
        rounding === "floor"
            ? Math.floor(value / unit)
            : Math.floor(value / unit + 0.5);
    return Math.max(unit, n * unit);
}

function roundingOf(snapMode) {
    return snapMode === "floor64" ? "floor" : "nearest";
}

function orient(shortSide, longSide, orientation) {
    return orientation === "landscape"
        ? [longSide, shortSide]
        : [shortSide, longSide];
}

function presetDims(ratioKey, orientation, scaleKey, customLong, snapMode) {
    if (!TABLE) return null;

    const unit = TABLE.snap_unit;
    const rounding = roundingOf(snapMode);
    const entry =
        TABLE.ratios.find((r) => r.key === ratioKey) || TABLE.ratios[0];

    if (scaleKey === TABLE.scale_fhd) {
        return orient(TABLE.fhd.short, TABLE.fhd.long, orientation);
    }

    if (scaleKey === TABLE.scale_custom) {
        const outLong = snap(customLong, rounding, unit);
        const outShort = snap(
            (outLong * entry.short) / entry.long,
            rounding,
            unit
        );
        return orient(outShort, outLong, orientation);
    }

    const factor = TABLE.scale_factors[scaleKey];
    if (factor === undefined) return null;

    const outLong = snap(entry.long * factor, rounding, unit);
    let outShort;
    if (snapMode === "preserve_ratio") {
        outShort = snap((outLong * entry.short) / entry.long, rounding, unit);
    } else {
        outShort = snap(entry.short * factor, rounding, unit);
    }
    return orient(outShort, outLong, orientation);
}

function badgeOf(width, height) {
    if (!TABLE) return "?";
    return width * height <= TABLE.opus_free_pixels
        ? TABLE.badges.free
        : TABLE.badges.anlas;
}

// ═══ 그래프 / 승격 경계 ══════════════════════════════════════════

function widgetOf(node, name) {
    return node?.widgets?.find((w) => w.name === name);
}

/** 루트 + 열려 있는 뷰 + 등록된 서브그래프 정의를 전부 모은다. */
function collectGraphs() {
    const seen = new Set();
    const out = [];
    const push = (g) => {
        if (g && !seen.has(g)) {
            seen.add(g);
            out.push(g);
        }
    };

    push(app.graph);
    push(app.canvas?.graph);

    const defs = app.graph?.subgraphs;
    if (defs && typeof defs.values === "function") {
        try {
            for (const sg of defs.values()) push(sg);
        } catch (e) {
            /* noop */
        }
    }
    // 큐 방식: 새로 push 된 그래프도 이어서 순회한다.
    for (let i = 0; i < out.length; i++) {
        for (const n of out[i]?._nodes || []) if (n?.subgraph) push(n.subgraph);
    }
    return out;
}

function linkById(graph, id) {
    if (!graph || id === null || id === undefined) return null;
    if (typeof graph.getLink === "function") {
        const l = graph.getLink(id);
        if (l) return l;
    }
    const links = graph.links;
    if (!links) return null;
    if (typeof links.get === "function") return links.get(id) || null;
    return links[id] || null;
}

/**
 * 내부 노드를 품고 있는 서브그래프 호스트 인스턴스를 찾는다.
 *
 * 서브그래프 정의는 인스턴스 여러 개가 공유할 수 있다. 그 경우 첫 인스턴스를
 * 쓴다 — 정의 하나에 값이 여러 벌 붙는 상황 자체가 모호해서 더 나은 답이 없다.
 */
function hostOf(innerNode) {
    const graph = innerNode?.graph;
    if (!graph || graph === app.graph) return null;

    const cached = innerNode.__bmkResHost;
    if (cached && cached.graph && cached.subgraph === graph) return cached;

    const now = performance.now();
    if (now - (innerNode.__bmkResHostAt || 0) < RESCAN_MS) {
        return cached || null;
    }
    innerNode.__bmkResHostAt = now;

    let found = null;
    outer: for (const g of collectGraphs()) {
        for (const n of g?._nodes || []) {
            if (n?.subgraph === graph) {
                found = n;
                break outer;
            }
        }
    }
    innerNode.__bmkResHost = found;
    return found;
}

/**
 * 내부 입력이 "서브그래프 입력 노드"에서 오는지 판정하고, 그렇다면 바깥에서의
 * 이름을 돌려준다. 내부 실노드에서 오는 진짜 데이터 링크면 null.
 */
function promotedName(innerNode, inputName) {
    const input = innerNode.inputs?.find((i) => i.name === inputName);
    if (!input || input.link === null || input.link === undefined) return null;

    const graph = innerNode.graph;
    const link = linkById(graph, input.link);
    if (!link) return null;

    const originId = link.origin_id;
    const fromInputNode =
        graph?.inputNode?.id === originId ||
        typeof graph?.getNodeById !== "function" ||
        !graph.getNodeById(originId);
    if (!fromInputNode) return null;

    const slot = link.origin_slot;
    return (
        graph?.inputs?.[slot]?.name ||
        graph?.inputNode?.slots?.[slot]?.name ||
        inputName
    );
}

/** readout 계산에 쓰는 문맥. host 는 없을 수도 있다(루트 배치). */
function makeContext(node, hostHint) {
    return { node, host: hostHint || hostOf(node) };
}

/**
 * 승격 경계를 넘어 값을 읽는다.
 *
 * driven=true 는 "값이 실제로 어딘가에서 흘러 들어오지만 편집 시점에는 알 수
 * 없다" 는 뜻이다. 소켓이 연결돼 있다는 사실만으로 driven 을 켜면 안 된다 —
 * 승격만 해두고 바깥에서 아무것도 안 꽂은 상태가 그렇게 오판된다.
 */
function readInput(ctx, name) {
    const { node, host } = ctx;
    const input = node.inputs?.find((i) => i.name === name);
    const widget = widgetOf(node, name);

    if (!input || input.link === null || input.link === undefined) {
        return { value: widget ? widget.value : undefined, driven: false };
    }

    const outer = promotedName(node, name);
    if (!outer) {
        // 서브그래프 안의 실제 노드에서 값이 들어온다.
        return { value: undefined, driven: true };
    }
    if (!host) return { value: undefined, driven: true };

    const hostInput = host.inputs?.find((i) => i.name === outer);
    if (hostInput && hostInput.link !== null && hostInput.link !== undefined) {
        // 호스트 바깥에서 주입되고 있다.
        return { value: undefined, driven: true };
    }

    const hostWidget = widgetOf(host, outer);
    if (hostWidget) return { value: hostWidget.value, driven: false };

    // 승격은 됐는데 위젯도 링크도 없다 = 값이 없는 상태.
    return { value: undefined, driven: false };
}

function valueOf(ctx, name, fallback) {
    const r = readInput(ctx, name);
    return r.value === undefined ? fallback : r.value;
}

function isDriven(ctx, name) {
    return readInput(ctx, name).driven;
}

// ═══ 모드 / 활성화 / readout ═════════════════════════════════════

/** 파이썬 _resolve_mode 와 같은 규칙. */
function effectiveMode(ctx) {
    const mode = valueOf(ctx, "mode", "auto");
    if (mode !== "auto") return { mode, certain: true };

    // is_i2i 가 실제로 흘러 들어오면 값을 알 수 없다.
    if (isDriven(ctx, "is_i2i")) return { mode: "auto", certain: false };

    const hasSource =
        isDriven(ctx, "source_width") ||
        isDriven(ctx, "source_height") ||
        isDriven(ctx, "image");

    return { mode: hasSource ? "i2i: scale source" : "t2i", certain: true };
}

/** 내부 위젯과, 승격됐다면 호스트 쪽 대응 위젯까지 함께 회색 처리한다. */
function applyEnable(ctx, eff) {
    const { node, host } = ctx;
    const scaleKey = valueOf(ctx, "scale", "");
    const isCustom = TABLE && scaleKey === TABLE.scale_custom;
    const isFhd = TABLE && scaleKey === TABLE.scale_fhd;

    // certain=false 면 무엇이 쓰일지 모르므로 전부 살려 둔다.
    let usesPreset = true;
    let usesRatio = true;
    let usesUpscale = true;

    if (eff.certain) {
        usesPreset = eff.mode !== "i2i: scale source";
        // fit preset 은 ratio/orientation 을 소스에서 자동으로 뽑는다.
        usesRatio = usesPreset && eff.mode !== "i2i: fit preset";
        usesUpscale = eff.mode === "i2i: scale source";
    }

    const active = {
        ratio: usesRatio && !isFhd,
        orientation: usesPreset && (eff.mode !== "i2i: fit preset" || !eff.certain),
        scale: usesPreset,
        custom_long_side: usesPreset && (isCustom || !eff.certain),
        upscale: usesUpscale,
    };

    for (const [name, on] of Object.entries(active)) {
        const w = widgetOf(node, name);
        if (w) w.disabled = !on;

        if (host) {
            const outer = promotedName(node, name);
            const hw = outer && widgetOf(host, outer);
            if (hw) hw.disabled = !on;
        }
    }
}

function computeReadout(ctx, eff) {
    if (!TABLE) {
        return { text: "테이블 미수신 — 서버 로그 확인", badge: "?" };
    }

    const snapMode = valueOf(ctx, "snap_mode", "nearest64");
    const scaleKey = valueOf(ctx, "scale", "");

    if (!eff.certain) {
        return { text: "is_i2i 입력에 따라 결정", badge: "?" };
    }

    if (eff.mode === "i2i: scale source") {
        const up = Number(valueOf(ctx, "upscale", 1)) || 1;
        return {
            text: `소스 × ${up.toFixed(2)} → ${TABLE.snap_unit}px 스냅`,
            badge: "?",
        };
    }

    if (eff.mode === "i2i: fit preset") {
        return { text: `소스 AR → 프리셋 · ${scaleKey}`, badge: "?" };
    }

    // 프리셋 축 중 하나라도 바깥에서 주입되면 계산이 성립하지 않는다.
    for (const name of ["ratio", "orientation", "scale", "custom_long_side"]) {
        if (isDriven(ctx, name)) {
            return { text: `${name} 이 외부 입력 — 실행 후 확인`, badge: "?" };
        }
    }

    const dims = presetDims(
        valueOf(ctx, "ratio", ""),
        valueOf(ctx, "orientation", "portrait"),
        scaleKey,
        Number(valueOf(ctx, "custom_long_side", 1024)) || 1024,
        snapMode
    );
    if (!dims) return { text: "—", badge: "?" };

    const [w, h] = dims;
    const mp = (w * h) / 1000000;
    return {
        text: `${w} × ${h} · ${mp.toFixed(2)}MP`,
        badge: badgeOf(w, h),
    };
}

/**
 * 한 번에 계산해서 돌려준다. 값을 캐시하지 않는 게 핵심이다 — 호스트 위젯이
 * 바뀔 때 내부 위젯의 callback 은 호출되지 않으므로, 콜백에만 기대면 갱신
 * 시점을 놓친다. 계산 자체는 산술 몇 줄이라 매 프레임 돌려도 부담이 없다.
 */
function readoutFor(node, hostHint) {
    const ctx = makeContext(node, hostHint);
    const eff = effectiveMode(ctx);
    applyEnable(ctx, eff);
    return computeReadout(ctx, eff);
}

function refresh(node) {
    // 값은 draw 에서 새로 계산하므로 여기서는 회색 처리 즉시 반영 + 재드로우만.
    try {
        readoutFor(node);
    } catch (e) {
        /* noop */
    }
    node.setDirtyCanvas(true, false);
}

// ═══ 공용 draw ═══════════════════════════════════════════════════

function ellipsize(ctx, text, maxWidth) {
    if (maxWidth <= 0) return "";
    if (ctx.measureText(text).width <= maxWidth) return text;
    let out = text;
    while (out.length > 1 && ctx.measureText(out + "…").width > maxWidth) {
        out = out.slice(0, -1);
    }
    return out + "…";
}

function drawReadoutBox(ctx, x, y, boxW, boxH, value, style, node) {
    if (boxW <= 8 || boxH <= 4) return;
    const cy = y + boxH / 2;

    const base = baseColorOf(style, node);
    const under = underColorOf(style, node);
    const light =
        luminance(compositeColor(base, under, style.opacity)) > LIGHT_BG_THRESHOLD;

    ctx.save();

    ctx.beginPath();
    if (ctx.roundRect) ctx.roundRect(x, y, boxW, boxH, 4);
    else ctx.rect(x, y, boxW, boxH);
    ctx.fillStyle = rgba(base, style.opacity);
    ctx.fill();
    ctx.strokeStyle = light
        ? `rgba(0, 0, 0, ${style.borderOpacity})`
        : `rgba(255, 255, 255, ${style.borderOpacity})`;
    ctx.lineWidth = 1;
    ctx.stroke();

    const badge = value?.badge || "";
    let reserved = 0;

    if (badge) {
        const bs = BADGE_STYLE[badge] || BADGE_STYLE["?"];
        ctx.font = "10px " + FONT;
        const bw = ctx.measureText(badge).width + 12;
        const bx = x + boxW - bw - 6;
        ctx.beginPath();
        if (ctx.roundRect) ctx.roundRect(bx, cy - 8, bw, 16, 8);
        else ctx.rect(bx, cy - 8, bw, 16);
        ctx.fillStyle = light ? bs.bgOnLight : bs.bg;
        ctx.fill();
        ctx.fillStyle = light ? bs.fgOnLight : bs.fg;
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillText(badge, bx + bw / 2, cy);
        reserved = bw + 12;
    }

    ctx.font = "12px " + FONT;
    ctx.fillStyle = light ? TEXT_COLOR.onLight : TEXT_COLOR.onDark;
    ctx.textAlign = "left";
    ctx.textBaseline = "middle";
    ctx.fillText(
        ellipsize(ctx, value?.text || "—", boxW - 20 - reserved),
        x + 10,
        cy
    );

    ctx.restore();
}

// ═══ readout 위젯 (내부 노드) ════════════════════════════════════

function addReadout(node) {
    const widget = {
        type: "bmk_readout",
        name: READOUT_NAME,
        value: null,
        // 워크플로에 저장되지 않도록 두 경로 모두 막는다.
        options: { serialize: false },
        serialize: false,
        serializeValue() {
            return undefined;
        },
        computeSize(width) {
            return [width, ROW_HEIGHT];
        },
        // 클릭을 먹지 않게 해서 아래 위젯 조작을 방해하지 않는다.
        mouse() {
            return false;
        },
        draw(ctx, drawNode, widgetWidth, widgetY, height) {
            const target = drawNode || node;
            const M = 15;
            let value;
            try {
                value = readoutFor(target);
            } catch (err) {
                value = { text: "계산 오류 — 콘솔 확인", badge: "?" };
                console.warn("[BMK NAI Resolution] readout 계산 오류", err);
            }
            drawReadoutBox(
                ctx,
                M,
                widgetY + 2,
                widgetWidth - M * 2,
                height - 4,
                value,
                STYLE.widget,
                target
            );
        },
    };

    // addCustomWidget 은 배열 끝에 붙인다 — 인덱스 규약상 반드시 끝이어야 한다.
    node.addCustomWidget(widget);
    return widget;
}

// ═══ 서브그래프 호스트 오버레이 ══════════════════════════════════

/** 서브그래프 정의 안에서 BMKNaiResolution 인스턴스를 찾는다 (직계 우선). */
function findResolutionNode(hostNode, depth = 0) {
    const sg = hostNode?.subgraph;
    if (!sg || depth > MAX_DEPTH) return null;

    const nodes = sg._nodes || [];
    for (const n of nodes) {
        if (n?.comfyClass === NODE_CLASS) return n;
    }
    for (const n of nodes) {
        if (n?.subgraph) {
            const hit = findResolutionNode(n, depth + 1);
            if (hit) return hit;
        }
    }
    return null;
}

/**
 * 매 프레임 그래프를 훑지 않도록 찾은 노드를 캐시한다. 못 찾았을 때만 주기적으로
 * 재탐색한다 — 서브그래프 내용은 편집 중에 바뀔 수 있다.
 */
function resolveInner(hostNode) {
    const cached = hostNode.__bmkResInner;
    if (cached && cached.graph) return cached;

    const now = performance.now();
    if (now - (hostNode.__bmkResScanAt || 0) < RESCAN_MS) return null;
    hostNode.__bmkResScanAt = now;

    const found = findResolutionNode(hostNode);
    hostNode.__bmkResInner = found || null;
    return found;
}

function attachHostOverlay(hostNode) {
    if (hostNode.__bmkResOverlay) return;
    hostNode.__bmkResOverlay = true;

    const original = hostNode.onDrawForeground;
    hostNode.onDrawForeground = function (ctx, ...rest) {
        const result = original ? original.call(this, ctx, ...rest) : undefined;
        try {
            if (this.flags?.collapsed) return result;

            const inner = resolveInner(this);
            if (!inner) return result;

            // 호스트를 직접 넘긴다 — 승격된 위젯 값의 정본이 여기 있고,
            // hostOf() 의 그래프 순회도 건너뛴다.
            const value = readoutFor(inner, this);

            // 본문 바깥(하단). 위젯 재조정의 영향권 밖이라 쓸려나가지 않는다.
            // 색은 호스트 노드(this)의 body 색을 따라간다.
            drawReadoutBox(
                ctx,
                0,
                this.size[1] + STYLE.host.gap,
                this.size[0],
                ROW_HEIGHT - 2,
                value,
                STYLE.host,
                this
            );
        } catch (err) {
            console.warn("[BMK NAI Resolution] 호스트 오버레이 draw 오류", err);
        }
        return result;
    };
}

// ═══ 등록 ════════════════════════════════════════════════════════

app.registerExtension({
    name: "BMK.NaiResolution",

    async setup() {
        await ensureTable();
    },

    async nodeCreated(node) {
        // 서브그래프 호스트: 내부에 이 노드가 있으면 하단에 같은 readout 을 그린다.
        if (node?.subgraph) {
            attachHostOverlay(node);
            return;
        }

        if (node.comfyClass !== NODE_CLASS) return;
        if (widgetOf(node, READOUT_NAME)) return;

        addReadout(node);

        // 값이 바뀔 때마다 회색 처리를 즉시 반영한다. readout 값 자체는 draw 에서
        // 새로 계산하므로 이 콜백이 없어도 표시는 맞는다 — 승격 상태에서 콜백이
        // 아예 안 불리는 경우를 v4 가 그렇게 넘긴다.
        for (const w of node.widgets || []) {
            if (w.name === READOUT_NAME) continue;
            const original = w.callback;
            w.callback = function (...args) {
                const result = original ? original.apply(this, args) : undefined;
                refresh(node);
                return result;
            };
        }

        const onConnections = node.onConnectionsChange;
        node.onConnectionsChange = function (...args) {
            const result = onConnections
                ? onConnections.apply(this, args)
                : undefined;
            // 승격/해제는 링크 변경으로 나타난다. 호스트 캐시를 버려 다시 찾게 한다.
            this.__bmkResHost = null;
            this.__bmkResHostAt = 0;
            refresh(this);
            return result;
        };

        const onConfigure = node.onConfigure;
        node.onConfigure = function (...args) {
            const result = onConfigure ? onConfigure.apply(this, args) : undefined;
            requestAnimationFrame(() => refresh(this));
            return result;
        };

        ensureTable().then(() => refresh(node));
        requestAnimationFrame(() => refresh(node));
    },
});
