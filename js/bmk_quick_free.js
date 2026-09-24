// bmk_quick_free.js — 모델 언로드 / 실행 캐시 비우기 퀵 버튼 + 단축키.
//
// 배경
// ────
// 프론트엔드 개편으로 화면 오른쪽 위에 있던 "Unload Models" /
// "Unload Models and Execution Cache" 아이콘이 사라지고 메인 메뉴(C 로고)
// → Edit 하위로 들어갔다. 자주 쓰는 동작인데 메뉴를 두 단계 열어야 한다.
// 이 패치는 두 동작을 (a) 커맨드 + 단축키, (b) 상단 툴바(또는 플로팅)
// 버튼 두 경로로 되살린다.
//
// 동작
// ────
// - 프론트엔드 메뉴 항목을 클릭하는 대신 백엔드 /free 엔드포인트를 직접 호출.
//     { unload_models: true }                    → 모델 언로드
//     { unload_models: true, free_memory: true } → 모델 언로드 + 실행 캐시 초기화
//   메뉴 구조가 또 바뀌어도 백엔드 API가 유지되는 한 계속 동작한다.
// - 커맨드 2개를 등록하므로 Settings → Keybinding 에서 검색·재지정 가능하고
//   커맨드 팔레트에도 뜬다. 기본 단축키는 Alt+U / Alt+Shift+U.
// - 버튼은 상단 툴바 우측 슬롯(.comfyui-menu-right → .comfyui-menu)에 끼워
//   넣고, 슬롯을 못 찾으면 화면 모서리 플로팅 버튼으로 대체한다. Vue 리렌더로
//   DOM이 날아가면 MutationObserver 가 다시 붙인다.
// - 프론트엔드가 actionBarButtons 확장 API를 지원하면 그쪽이 우선이고,
//   DOM 마운트는 스스로 물러난다(중복 방지).
//
// 설정 (Settings → BMK → Quick Free)
//   - 버튼 위치        : auto / topbar / floating / off   (기본 auto)
//   - 플로팅 모서리    : top-right / top-left / bottom-right / bottom-left
//   - 완료 토스트 표시 : on/off (실패 토스트는 설정과 무관하게 항상 표시)
//
// 주의
// ────
// - 워크플로 실행 중에 누르면 현재 작업이 끝난 뒤 처리된다(기존 메뉴와 동일).
// - 별도 설치한 ComfyUI-QuickFree 패키지가 남아 있으면 버튼이 두 벌 생긴다.
//   해당 custom_nodes 폴더를 지우고 이 파일만 남길 것.
//
// 렌더링/네트워크 호출 외 그래프·직렬화에는 영향 없음.
//
// 버전 이력:
//   v1 (2026-09): 독립 패키지(ComfyUI-QuickFree)를 BMK 로 통합. 커맨드·키바인딩
//                 등록, 툴바 DOM 마운트 + 자동 재부착, 중복 요청 가드,
//                 설정 3종(위치/모서리/토스트) 추가.

import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const _TAG = "[ComfyUI_BMK_Nodes::QuickFree]";

const SETTING_PLACEMENT = "BMK.QuickFree.Placement";
const SETTING_CORNER = "BMK.QuickFree.FloatingCorner";
const SETTING_TOAST = "BMK.QuickFree.Toast";

const CMD_UNLOAD = "BMK.QuickFree.UnloadModels";
const CMD_UNLOAD_ALL = "BMK.QuickFree.UnloadModelsAndCache";

const CONTAINER_ID = "bmk-qf-container";
const STYLE_ID = "bmk-qf-style";
const NATIVE_MARKER = "bmk-qf-native"; // actionBarButtons 경로가 살아있는지 판별

// 툴바 우측 슬롯 후보 (우선순위 순). 첫 번째가 Run 버튼 그룹이 텔레포트되는 자리.
const TOPBAR_SELECTORS = [".comfyui-menu-right", ".comfyui-menu"];

// ─── 설정 접근 ──────────────────────────────────────────────────

function getSetting(id, fallback) {
    const v =
        app.extensionManager?.setting?.get?.(id) ??
        app.ui?.settings?.getSettingValue?.(id, fallback);
    return v === undefined || v === null ? fallback : v;
}

function toast(severity, summary, detail, life) {
    // 성공 토스트만 설정으로 끌 수 있다 — 실패는 항상 알린다.
    if (severity === "success" && !getSetting(SETTING_TOAST, true)) return;
    try {
        app.extensionManager?.toast?.add?.({ severity, summary, detail, life });
    } catch (err) {
        console.debug(`${_TAG} toast unavailable`, err);
    }
}

// ─── /free 호출 ────────────────────────────────────────────────

let _busy = false;

async function requestFree(payload, label) {
    if (_busy) return; // 연타/단축키 중복 방지
    _busy = true;
    setButtonsBusy(true);
    try {
        const res = await api.fetchApi("/free", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });
        if (!res.ok) throw new Error(`HTTP ${res.status} ${res.statusText}`);
        console.log(`${_TAG} ${label} 완료`);
        toast("success", label, undefined, 1500);
    } catch (err) {
        console.error(`${_TAG} ${label} 실패`, err);
        toast("error", "QuickFree 실패", String(err), 4000);
    } finally {
        _busy = false;
        setButtonsBusy(false);
    }
}

const ACTIONS = [
    {
        id: CMD_UNLOAD,
        label: "Unload Models",
        short: "Unload",
        icon: "pi pi-eraser",
        run: () => requestFree({ unload_models: true }, "Unload Models"),
    },
    {
        id: CMD_UNLOAD_ALL,
        label: "Unload Models and Execution Cache",
        short: "Unload+Cache",
        icon: "pi pi-trash",
        run: () =>
            requestFree(
                { unload_models: true, free_memory: true },
                "Unload Models + Execution Cache"
            ),
    },
];

// ─── 스타일 ────────────────────────────────────────────────────

const CSS = `
#${CONTAINER_ID} { display:flex; align-items:center; gap:4px; }
#${CONTAINER_ID}.bmk-qf-floating {
    position:fixed; z-index:1000; padding:2px;
}
#${CONTAINER_ID} .bmk-qf-btn {
    display:inline-flex; align-items:center; gap:6px;
    padding:5px 8px; border-radius:6px; cursor:pointer;
    font-size:12px; line-height:1;
    color: var(--fg-color, #ddd);
    background: transparent;
    border:1px solid transparent;
}
#${CONTAINER_ID} .bmk-qf-btn:hover:not(:disabled) {
    background: var(--comfy-input-bg, #2a2a2a);
    border-color: var(--border-color, #444);
}
#${CONTAINER_ID} .bmk-qf-btn:disabled { opacity:.45; cursor:default; }
#${CONTAINER_ID}.bmk-qf-floating .bmk-qf-btn {
    background: var(--comfy-menu-bg, #2a2a2a);
    border-color: var(--border-color, #444);
    box-shadow: 0 1px 4px rgba(0,0,0,.35);
}
#${CONTAINER_ID}.bmk-qf-topbar .bmk-qf-label { display:none; }
#${CONTAINER_ID} .bmk-qf-label { white-space:nowrap; }
`;

function ensureStyle() {
    if (document.getElementById(STYLE_ID)) return;
    const style = document.createElement("style");
    style.id = STYLE_ID;
    style.textContent = CSS;
    document.head.appendChild(style);
}

// ─── 버튼 DOM ──────────────────────────────────────────────────

let _container = null;
let _mode = null; // "topbar" | "floating"
let _topbarWarned = false;

function setButtonsBusy(busy) {
    if (!_container) return;
    for (const btn of _container.querySelectorAll(".bmk-qf-btn")) {
        btn.disabled = busy;
    }
}

function floatingStyle() {
    const corner = String(getSetting(SETTING_CORNER, "top-right"));
    const [v, h] = corner.split("-");
    // 상단은 툴바 높이를 피해 56px 아래에서 시작.
    return (
        (v === "bottom" ? "bottom:12px;" : "top:56px;") +
        (h === "left" ? "left:12px;" : "right:12px;")
    );
}

function buildContainer(mode) {
    const wrap = document.createElement("div");
    wrap.id = CONTAINER_ID;
    wrap.className = mode === "floating" ? "bmk-qf-floating" : "bmk-qf-topbar";
    if (mode === "floating") wrap.style.cssText = floatingStyle();

    for (const action of ACTIONS) {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "bmk-qf-btn";
        btn.title = action.label;
        btn.setAttribute("aria-label", action.label);

        const icon = document.createElement("i");
        icon.className = action.icon;
        btn.appendChild(icon);

        const label = document.createElement("span");
        label.className = "bmk-qf-label";
        label.textContent = action.short;
        btn.appendChild(label);

        btn.addEventListener("click", (e) => {
            e.preventDefault();
            e.stopPropagation();
            action.run();
        });
        wrap.appendChild(btn);
    }
    return wrap;
}

function unmount() {
    _container?.remove();
    _container = null;
    _mode = null;
}

function findTopbarSlot() {
    for (const sel of TOPBAR_SELECTORS) {
        const el = document.querySelector(sel);
        if (el?.isConnected) return el;
    }
    return null;
}

/** 설정과 현재 DOM 상태를 비교해 필요한 만큼만 붙이거나 뗀다(멱등). */
function ensureMounted() {
    const placement = String(getSetting(SETTING_PLACEMENT, "auto"));

    if (placement === "off") {
        unmount();
        return;
    }

    // 프론트엔드 네이티브 액션바 버튼이 살아있으면 DOM 버튼은 물러난다.
    if (document.querySelector(`.${NATIVE_MARKER}`)) {
        unmount();
        return;
    }

    const slot = placement === "floating" ? null : findTopbarSlot();

    if (placement === "topbar" && !slot) {
        if (!_topbarWarned) {
            _topbarWarned = true;
            console.warn(
                `${_TAG} 상단 툴바 슬롯을 찾지 못했습니다 ` +
                    `(${TOPBAR_SELECTORS.join(", ")}). ` +
                    "설정에서 버튼 위치를 auto 또는 floating 으로 바꾸세요."
            );
        }
        unmount();
        return;
    }

    const mode = slot ? "topbar" : "floating";
    const parent = slot ?? document.body;

    // 이미 올바른 부모에 올바른 모드로 붙어 있으면 아무것도 하지 않는다.
    if (_container?.isConnected && _mode === mode && _container.parentElement === parent) {
        return;
    }

    unmount();
    ensureStyle();
    _container = buildContainer(mode);
    _mode = mode;
    parent.appendChild(_container);
    setButtonsBusy(_busy);
    console.log(`${_TAG} 버튼 마운트: ${mode}`);
}

function remount() {
    unmount();
    ensureMounted();
}

// Vue 리렌더로 컨테이너가 떨어져 나가면 다시 붙인다(디바운스).
let _pending = null;
function scheduleEnsure() {
    if (_pending) return;
    _pending = setTimeout(() => {
        _pending = null;
        try {
            ensureMounted();
        } catch (err) {
            console.error(`${_TAG} mount 실패`, err);
        }
    }, 200);
}

// ─── 확장 등록 ─────────────────────────────────────────────────

app.registerExtension({
    name: "BMK.QuickFree",

    commands: ACTIONS.map((a) => ({
        id: a.id,
        label: a.label,
        icon: a.icon,
        function: a.run,
    })),

    // 코어 키와 겹치면 Settings → Keybinding 에서 변경 가능.
    keybindings: [
        { combo: { key: "u", alt: true }, commandId: CMD_UNLOAD },
        { combo: { key: "u", alt: true, shift: true }, commandId: CMD_UNLOAD_ALL },
    ],

    // 프론트엔드가 지원하면 네이티브 액션바에, 아니면 무시된다(폴백은 DOM 마운트).
    actionBarButtons: ACTIONS.map((a) => ({
        icon: `${a.icon} ${NATIVE_MARKER}`,
        tooltip: a.label,
        onClick: a.run,
    })),

    settings: [
        {
            id: SETTING_PLACEMENT,
            name: "퀵 언로드 버튼 위치",
            category: ["BMK", "Quick Free", "Placement"],
            tooltip:
                "auto = 상단 툴바에 끼워넣고 실패하면 플로팅 / topbar = 툴바만 / " +
                "floating = 항상 화면 모서리 / off = 버튼 없이 단축키만 사용.",
            type: "combo",
            options: ["auto", "topbar", "floating", "off"],
            defaultValue: "auto",
            onChange: remount,
        },
        {
            id: SETTING_CORNER,
            name: "플로팅 버튼 모서리",
            category: ["BMK", "Quick Free", "Corner"],
            tooltip: "버튼 위치가 플로팅으로 표시될 때 화면의 어느 모서리에 둘지.",
            type: "combo",
            options: ["top-right", "top-left", "bottom-right", "bottom-left"],
            defaultValue: "top-right",
            onChange: remount,
        },
        {
            id: SETTING_TOAST,
            name: "완료 토스트 표시",
            category: ["BMK", "Quick Free", "Toast"],
            tooltip: "언로드 성공 시 토스트 알림. 실패 알림은 이 설정과 무관하게 항상 표시됩니다.",
            type: "boolean",
            defaultValue: true,
        },
    ],

    setup() {
        // 네이티브 액션바 버튼이 렌더링될 시간을 주고 첫 마운트(중복 방지).
        setTimeout(scheduleEnsure, 800);

        const observer = new MutationObserver(scheduleEnsure);
        observer.observe(document.body, { childList: true, subtree: true });

        window.addEventListener("resize", scheduleEnsure);
    },
});
