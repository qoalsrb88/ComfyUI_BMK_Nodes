// topbar_overflow_patch.js — 창 폭이 좁을 때 상단 바 확장 버튼이 서브그래프 탐색 영역을 밀어내는 문제 우회.
//
// 원인 (프론트 1.53.6):
//   TopMenuSection 은 한 줄 flex 다. 왼쪽 탐색 영역(보기 모드 토글 + 뒤로가기 + 브레드크럼)을 감싼
//   div 는 `flex-1 min-w-0 overflow-hidden` 이라 0px 까지 줄고, 오른쪽 액션바는 내용물의 최소 폭을
//   지킨다. 그래서 창을 줄이면 탐색 영역이 먼저 사라지고, 그다음 레거시 버튼 그룹(Crystools 모니터,
//   이미지 피드 등)이 줄바꿈되며 찌그러진다. 브레드크럼 자체의 min-width:120px 는 바깥 div 의
//   min-w-0 에 막혀 효과가 없다.
//
// 우회:
//   1. 탐색 영역에 최소 폭을 예약한다. 보기 모드 토글 + 뒤로가기 + 부모 항목 + 현재 항목(최대 ACTIVE_MAX).
//      나머지 압축은 브레드크럼 자체의 접기 로직이 맡는다.
//   2. 액션바 카드를 min-width:max-content 로 묶어 버튼이 찌그러지지 않게 한다.
//   3. 그래도 넘치면 app.menu.element 의 직계 버튼 그룹을 오른쪽부터 ⋯ 트레이로 옮긴다.
//      DOM 노드를 그대로 옮기므로 확장의 이벤트와 상태가 유지되고, 확장 코드를 몰라도 된다.
//      공간이 생기면 원래 자리(주석 노드로 표시)로 돌린다.
//   4. 그룹을 전부 옮겨도 넘치면 오른쪽 액션바를 두 번째 줄로 내린다.
//   Vue 가 그리는 버튼(확장 매니저, actionBarButtons, Run)은 옮기지 않는다.
//
// 상위에서 고쳐지면 이 파일을 삭제할 것.
//
// 버전 이력:
//   v1 (2026-09): 탐색 영역 예약 + 그룹 단위 오버플로 트레이 + 두 줄 폴백.

import { app } from "../../scripts/app.js";

const _TAG = "[ComfyUI_BMK_Nodes::TopbarOverflow]";

const ACTIVE_MAX = 200; // 현재 서브그래프 항목에 예약하는 최대 폭
const PARENT_MIN = 44; // 접힌 브레드크럼의 부모 항목 폭. 상위 접기 로직의 항목당 추정치와 같다.
const SLACK = 24; // 트레이에서 되돌리거나 한 줄로 돌아갈 때 남아야 하는 여유 폭(경계에서 들락거림 방지)
const MORE_W = 32;

const CSS = `
[data-testid="action-bar-card"] { min-width: max-content; }
.bmk-tb-more {
    display: inline-flex; align-items: center; justify-content: center;
    width: ${MORE_W}px; height: 32px; padding: 0; border: none; border-radius: 6px; cursor: pointer;
    color: var(--base-foreground, #fff); background: var(--secondary-background, #262729);
}
.bmk-tb-more:hover, .bmk-tb-more[aria-expanded="true"] {
    background: var(--secondary-background-hover, #313235);
}
.bmk-tb-more[hidden], .bmk-tb-tray[hidden] { display: none; }
.bmk-tb-tray {
    position: fixed; z-index: 1000;
    display: flex; flex-direction: column; align-items: flex-end; gap: 8px; padding: 8px;
    border: 1px solid var(--interface-stroke, #4e4e4e); border-radius: 8px;
    background: var(--comfy-menu-bg, #171718); box-shadow: 1px 1px 8px rgba(0, 0, 0, 0.4);
}
`;

const placeholders = new WeakMap(); // 트레이에 있는 그룹 → 원래 자리의 주석 노드
const widths = new WeakMap(); // 트레이로 옮기기 직전 바에서 잰 폭

let moreBtn;
let tray;
let ro;
let observed = [];
let frame = 0;

const px = (v) => parseFloat(v) || 0;

function outerWidth(el) {
    const s = getComputedStyle(el);
    return el.getBoundingClientRect().width + px(s.marginLeft) + px(s.marginRight);
}

function navReserve(crumb) {
    let w = px(getComputedStyle(crumb).paddingLeft);
    for (const el of crumb.children) {
        if (el.tagName !== "NAV") w += outerWidth(el);
    }
    // 루트 그래프에서는 브레드크럼이 hidden 이라 폭이 0 이다.
    const active = crumb.querySelector(".p-breadcrumb-item-link[data-active]");
    if (active?.getBoundingClientRect().width) {
        const label = active.querySelector(".p-breadcrumb-item-label");
        const natural =
            outerWidth(active.closest(".p-breadcrumb-item")) +
            (label ? label.scrollWidth - label.clientWidth : 0);
        const seps = crumb.querySelectorAll(".p-breadcrumb-separator");
        w += PARENT_MIN + (seps.length ? outerWidth(seps[seps.length - 1]) : 0);
        w += Math.min(natural, ACTIVE_MAX);
    }
    return Math.ceil(w);
}

// 폭이 0 인 그룹(비어 있는 코어 그룹, 확장이 숨긴 그룹)은 자리를 차지하지 않으니 옮기지 않는다.
function groupsInBar(menu) {
    return [...menu.children].filter(
        (el) => el !== moreBtn && el.getBoundingClientRect().width > 0
    );
}

function hide(group) {
    widths.set(group, group.getBoundingClientRect().width);
    const ph = document.createComment("bmk-topbar-overflow");
    placeholders.set(group, ph);
    group.replaceWith(ph);
    tray.prepend(group);
}

function restore(group, menu) {
    const ph = placeholders.get(group);
    placeholders.delete(group);
    if (ph?.parentNode === menu) {
        ph.replaceWith(group);
        return;
    }
    // 트레이에 있는 동안 다른 확장이 끼워 넣은 그룹(settingsGroup.element.before 등)은
    // 트레이에서 바로 뒤에 있던 그룹의 원래 자리 앞으로 보낸다.
    const next = group.nextElementSibling && placeholders.get(group.nextElementSibling);
    menu.insertBefore(group, next?.parentNode === menu ? next : moreBtn);
}

function setWrapped(row, wrapped) {
    row.style.flexWrap = wrapped ? "wrap" : "";
    row.style.justifyContent = wrapped ? "flex-end" : "";
}

function setOpen(open) {
    open = open && tray.childElementCount > 0 && moreBtn.isConnected;
    tray.hidden = !open;
    moreBtn.setAttribute("aria-expanded", String(open));
    if (open) {
        const r = moreBtn.getBoundingClientRect();
        tray.style.top = `${r.bottom + 6}px`;
        tray.style.right = `${Math.max(8, innerWidth - r.right)}px`;
    }
}

function watch(els) {
    if (els.length === observed.length && els.every((el, i) => el === observed[i])) return;
    ro.disconnect();
    observed = els;
    for (const el of els) if (el) ro.observe(el);
}

function layout() {
    const menu = app.menu.element;
    // 포커스 모드 등으로 TopMenuSection 이 내려가면 메뉴가 떨어진 트리에 남는다.
    const bars = menu.isConnected && menu.closest('[data-testid="top-menu-actionbars"]');
    const right = bars?.parentElement;
    const row = right?.parentElement;
    const crumb = row?.querySelector('[data-testid="subgraph-breadcrumb"]');
    const nav = crumb?.parentElement;
    if (!crumb || nav.parentElement !== row) {
        setOpen(false);
        return;
    }
    watch([menu, row, bars, crumb.querySelector("nav"), crumb.firstElementChild]);
    if (moreBtn.parentElement !== menu || moreBtn.nextSibling) menu.append(moreBtn);

    const reserve = Math.min(navReserve(crumb), row.clientWidth);
    nav.style.minWidth = `${reserve}px`;
    const gap = px(getComputedStyle(menu).columnGap);
    const rowGap = px(getComputedStyle(row).columnGap);

    // 한 번에 그룹 하나씩 옮기고 다시 잰다. 옮길 때마다 폭 캐시가 갱신되므로 들락거리지 않고 수렴한다.
    for (let i = menu.childElementCount + tray.childElementCount + 2; i > 0; i--) {
        moreBtn.hidden = tray.childElementCount === 0;
        const wrapped = row.style.flexWrap === "wrap";
        const rightW = outerWidth(right);
        const room = row.clientWidth - rightW - (wrapped ? 0 : reserve + rowGap);
        if (room < 0) {
            const groups = groupsInBar(menu);
            if (groups.length) hide(groups[groups.length - 1]);
            else if (!wrapped) setWrapped(row, true);
            else break;
            continue;
        }
        if (wrapped) {
            // 바에 남은 그룹을 전부 트레이로 보냈을 때 한 줄에 들어가면 한 줄로 돌아간다.
            const groups = groupsInBar(menu);
            let minRight = rightW;
            for (const g of groups) minRight -= g.getBoundingClientRect().width + gap;
            if (groups.length && moreBtn.hidden) minRight += MORE_W + gap;
            if (row.clientWidth - minRight - reserve - rowGap >= SLACK) {
                setWrapped(row, false);
                continue;
            }
        }
        const next = tray.firstElementChild;
        if (!next) break;
        let need = (widths.get(next) ?? 0) + gap;
        if (tray.childElementCount === 1) need -= MORE_W + gap;
        if (room - need < SLACK) break;
        restore(next, menu);
    }

    const count = tray.childElementCount;
    moreBtn.hidden = count === 0;
    moreBtn.title = `숨긴 툴바 버튼 ${count}개`;
    moreBtn.setAttribute("aria-label", moreBtn.title);
    setOpen(!tray.hidden);
}

// ResizeObserver 콜백 안에서 관찰 대상의 크기를 바꾸면 "ResizeObserver loop" 오류가 날 수 있어
// 다음 프레임에 처리한다. 연달아 들어오는 알림(Crystools 수치 갱신 등)도 한 번으로 합쳐진다.
function schedule() {
    frame ||= requestAnimationFrame(() => {
        frame = 0;
        layout();
    });
}

app.registerExtension({
    name: "BMK.TopbarOverflow",

    setup() {
        if (!app.menu?.element) {
            console.warn(`${_TAG} app.menu 가 없어 비활성화합니다.`);
            return;
        }
        const style = document.createElement("style");
        style.textContent = CSS;
        document.head.appendChild(style);

        moreBtn = document.createElement("button");
        moreBtn.type = "button";
        moreBtn.className = "bmk-tb-more";
        moreBtn.hidden = true;
        moreBtn.innerHTML = '<i class="pi pi-ellipsis-h"></i>';
        moreBtn.addEventListener("click", () => setOpen(tray.hidden));

        tray = document.createElement("div");
        tray.className = "bmk-tb-tray";
        tray.hidden = true;
        document.body.appendChild(tray);

        document.addEventListener(
            "pointerdown",
            (e) => {
                if (!tray.hidden && !tray.contains(e.target) && !moreBtn.contains(e.target)) {
                    setOpen(false);
                }
            },
            true
        );
        // Esc 는 코어 단축키(서브그래프 나가기)보다 먼저 받아서 트레이만 닫는다.
        window.addEventListener(
            "keydown",
            (e) => {
                if (e.key !== "Escape" || tray.hidden) return;
                e.preventDefault();
                e.stopImmediatePropagation();
                setOpen(false);
            },
            true
        );

        ro = new ResizeObserver(schedule);
        watch([app.menu.element]);
        schedule();
    },
});
