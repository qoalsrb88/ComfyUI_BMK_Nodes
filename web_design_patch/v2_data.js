// BMK Design Patch Studio v2 — 순수 메타데이터 · 테마/포인트 팔레트 · 모델 가이드 · SVG 아이콘 팩(외부/사내 주소 없음).
"use strict";

window.DPV2Data = (() => {
  const SVG_NS = "http://www.w3.org/2000/svg";

  // Forge 6종 테마 (다크 3 + 라이트 3)
  const THEMES = [
    {
      id: "graphite", name: "그래파이트", mode: "dark", desc: "기본 중립 다크",
      vars: {
        "--bg": "#09090b", "--panel": "#141417", "--panel2": "#1c1c21", "--panel3": "#27272a",
        "--canvas-bg": "#0e0e12", "--text": "#e4e4e7", "--text-strong": "#fafafa",
        "--dim": "#a1a1aa", "--muted": "#71717a", "--line": "#2e2e35", "--line2": "#46464f",
      },
    },
    {
      id: "midnight", name: "미드나잇", mode: "dark", desc: "딥 블루 네이비",
      vars: {
        "--bg": "#080d17", "--panel": "#0f172a", "--panel2": "#172033", "--panel3": "#22304a",
        "--canvas-bg": "#0b1120", "--text": "#e2e8f0", "--text-strong": "#f8fafc",
        "--dim": "#94a3b8", "--muted": "#64748b", "--line": "#25344d", "--line2": "#3b4f70",
      },
    },
    {
      id: "charcoal", name: "차콜 웜", mode: "dark", desc: "따뜻한 회갈색 다크",
      vars: {
        "--bg": "#11100f", "--panel": "#1b1917", "--panel2": "#252220", "--panel3": "#322e2b",
        "--canvas-bg": "#141311", "--text": "#e7e5e4", "--text-strong": "#fafaf9",
        "--dim": "#a8a29e", "--muted": "#78716c", "--line": "#332f2c", "--line2": "#4a4540",
      },
    },
    {
      id: "cloud", name: "클라우드", mode: "light", desc: "깔끔한 중립 라이트",
      vars: {
        "--bg": "#f4f6f9", "--panel": "#ffffff", "--panel2": "#eef1f6", "--panel3": "#e2e7ef",
        "--canvas-bg": "#dde3ec", "--text": "#1f2937", "--text-strong": "#0f172a",
        "--dim": "#4b5563", "--muted": "#6b7280", "--line": "#d3dae6", "--line2": "#b7c2d4",
      },
    },
    {
      id: "mist", name: "블루 미스트", mode: "light", desc: "시원한 스카이 톤",
      vars: {
        "--bg": "#eef4fa", "--panel": "#f9fcff", "--panel2": "#e3edf7", "--panel3": "#d5e3f2",
        "--canvas-bg": "#d6e2ef", "--text": "#1e293b", "--text-strong": "#0f172a",
        "--dim": "#475569", "--muted": "#64748b", "--line": "#c6d6e8", "--line2": "#a8bdd6",
      },
    },
    {
      id: "ivory", name: "아이보리", mode: "light", desc: "눈이 편한 종이 톤",
      vars: {
        "--bg": "#f6f3ec", "--panel": "#fdfcf8", "--panel2": "#eee9de", "--panel3": "#e2dbcc",
        "--canvas-bg": "#e4ded1", "--text": "#292524", "--text-strong": "#1c1917",
        "--dim": "#57534e", "--muted": "#78716c", "--line": "#d9d1c2", "--line2": "#bfb4a0",
      },
    },
  ];

  // Forge 7종 포인트 컬러 (다크/라이트 모드별 최적 대비 자동 적응)
  const POINT_COLORS = [
    { id: "lime", name: "라임 옐로우", dark: "#dfe104", light: "#4f46e5", hex: "#dfe104", hover: "#c9cb03", on: "#09090b" },
    { id: "indigo", name: "인디고", dark: "#818cf8", light: "#4f46e5", hex: "#6366f1", hover: "#4f46e5", on: "#ffffff" },
    { id: "cyan", name: "시안", dark: "#22d3ee", light: "#0284c7", hex: "#06b6d4", hover: "#0891b2", on: "#08141a" },
    { id: "emerald", name: "에메랄드", dark: "#34d399", light: "#059669", hex: "#10b981", hover: "#059669", on: "#051912" },
    { id: "orange", name: "오렌지", dark: "#fb923c", light: "#ea580c", hex: "#f97316", hover: "#ea580c", on: "#1c0b02" },
    { id: "rose", name: "로즈", dark: "#fb7185", light: "#e11d48", hex: "#f43f5e", hover: "#e11d48", on: "#ffffff" },
    { id: "violet", name: "바이올렛", dark: "#a78bfa", light: "#7c3aed", hex: "#8b5cf6", hover: "#7c3aed", on: "#ffffff" },
  ];

  const LAYOUT_PRESETS = [
    { id: "standard", name: "기본 3단", desc: "좌측 목록 · 중앙 캔버스 · 우측 속성" },
    { id: "right-stack", name: "우측 집중 (포토샵식)", desc: "좌측 비우고 우측에 목록+속성 통합" },
    { id: "left-stack", name: "좌측 집중", desc: "좌측에 목록+속성 통합 · 우측 캔버스 확장" },
    { id: "focus", name: "캔버스 집중", desc: "도구 레일과 캔버스만 넓게 보기" },
  ];

  // 작업자 친화 목적 5종 (계약 §3.1 + 전용 SVG 아이콘 키 + 다크/라이트 시맨틱 컬러 + 추천 모델 + 프리셋)
  const PURPOSES = [
    {
      id: "correct",
      icon: "correct",
      color: { dark: "#60a5fa", light: "#2563eb" },
      label: "수정 · 보정",
      short: "수정",
      recModel: "gpt-image-2.6-sunburst",
      desc: "형태·색감·의상 디테일을 자연스럽게 보정",
      hint: "틀어진 장식·비율·의상 디테일을 레퍼런스에 맞춰 정밀하게 바로잡습니다.",
      placeholder: "예: @참조1 의상 시트처럼 금장 장식 곡선을 또렷하게 보정하고 주변 머리카락은 그대로 유지해줘",
      presets: [
        "@참조1 원화 시트 형태에 맞춰 장식 구조를 정확히 보정하고 주변 영역은 그대로 유지해줘",
        "비뚤어진 대칭과 실루엣을 자연스럽게 바로잡고 기존 화풍과 조명을 그대로 맞춰줘",
        "어색한 손·소매 주름을 원화 퀄리티에 맞게 깔끔하게 다듬어줘",
      ],
    },
    {
      id: "add",
      icon: "add",
      color: { dark: "#34d399", light: "#059669" },
      label: "요소 추가",
      short: "추가",
      recModel: "seedream-5.5",
      desc: "레퍼런스 소품·장식·요소를 자연스럽게 합성",
      hint: "없는 장식·소품·무늬를 선택한 위치에 원근과 그림자에 맞춰 자연스럽게 덧붙입니다.",
      placeholder: "예: @참조1 브로치를 칼라 중앙에 원근 각도에 맞춰 자연스럽게 추가해줘",
      presets: [
        "@참조1 디자인 소품을 선택 위치의 원근·광원에 맞춰 자연스럽게 추가해줘",
        "원단 질감과 어우러지는 은은한 금빛 자수 패턴을 추가해줘",
        "주변 그림자와 반사광이 이어지도록 장식 디테일을 자연스럽게 얹어줘",
      ],
    },
    {
      id: "remove",
      icon: "remove",
      color: { dark: "#fb7185", light: "#e11d48" },
      label: "제거 · 정리",
      short: "제거",
      recModel: "ideogram-4.5",
      desc: "불필요한 장식·오염·요소를 주변 배경으로 정리",
      hint: "방해되는 소품·잡티·불필요한 요소를 지우고 뒤쪽 의상·배경을 자연스럽게 복원합니다.",
      placeholder: "예: 불필요한 끈과 장식을 깔끔하게 지우고 뒤쪽 원단과 배경을 자연스럽게 복원해줘",
      presets: [
        "선택 영역 안의 불필요한 소품·잡티를 깔끔히 제거하고 뒤쪽 배경을 자연스럽게 채워줘",
        "어색하게 겹친 끈과 장식을 없애고 아래 의상 주름을 자연스럽게 이어줘",
        "외곽에 튀어나온 오염 픽셀을 정리하고 원래 피부·머리카락 톤으로 복원해줘",
      ],
    },
    {
      id: "vary",
      icon: "vary",
      color: { dark: "#c084fc", light: "#7c3aed" },
      label: "디자인 변형",
      short: "변형",
      recModel: "nano-banana-2.1",
      desc: "구도나 실루엣을 유지하며 다른 디자인 바리에이션",
      hint: "기존 구도와 캐릭터 정체성을 유지하면서 새로운 장식·소재·분위기 시안을 탐색합니다.",
      placeholder: "예: 전체 색감은 유지하면서 좀 더 화려한 바로크풍 금장 장식으로 변형해줘",
      presets: [
        "전체 화풍과 컬러 팔레트는 유지하면서 더 고급스러운 실루엣의 바리에이션으로 바꿔줘",
        "무광 가죽 대신 은은한 광택이 도는 실크·금속 소재 조합으로 디자인을 변형해줘",
        "원래 인상을 해치지 않는 선에서 장식 패턴과 배색을 새롭게 제안해줘",
      ],
    },
    {
      id: "detail",
      icon: "detail",
      color: { dark: "#fbbf24", light: "#d97706" },
      label: "디테일 강화",
      short: "디테일",
      recModel: "gpt-image-2.6-sunburst",
      desc: "질감·재질·광택·선명도 밀도를 높이기",
      hint: "형태와 구도는 그대로 고정한 채 재질 묘사·선명도·광택 밀도만 끌어올립니다.",
      placeholder: "예: 형태는 그대로 두고 금속 광택과 보석 반사 디테일만 더 선명하게 다듬어줘",
      presets: [
        "원래 형태와 구도는 절대 바꾸지 말고 금속·보석·원단 재질 묘사만 선명하게 올려줘",
        "흐릿한 외곽선과 자수 디테일을 하이엔드 원화 밀도로 정교하게 다듬어줘",
        "기존 조명 방향을 지키면서 하이라이트와 미세 음영 대비만 살짝 강화해줘",
      ],
    },
  ];

  // 격자 스냅 단계 선택지 (GPT 128px 권장 · 다중 모델 16/32/64px · 1px 자유)
  const SNAP_STEPS = [
    { val: 128, label: "128px", tag: "GPT 권장", desc: "v1 표준 · 정수 배율 2× 최적" },
    { val: 64, label: "64px", tag: "중형 파츠", desc: "장식·소품 균형 격자" },
    { val: 32, label: "32px", tag: "소형 파츠", desc: "브로치·눈·손 등 세부 영역" },
    { val: 16, label: "16px", tag: "공통 정밀", desc: "GPT·나노바나나 출력 공통 약수" },
    { val: 1, label: "1px", tag: "자유", desc: "격자 제한 없이 픽셀 단위 조절" },
  ];

  // 공용 인라인 SVG 아이콘 팩 (외부 리소스 0개 · 다크/라이트 6종 테마 currentColor 자동 추종)
  const ICON_DEFS = {
    projects: [["rect", { x: 3, y: 3, width: 7, height: 7, rx: 1 }], ["rect", { x: 14, y: 3, width: 7, height: 7, rx: 1 }], ["rect", { x: 3, y: 14, width: 7, height: 7, rx: 1 }], ["rect", { x: 14, y: 14, width: 7, height: 7, rx: 1 }]],
    undo: [["path", { d: "M3 7v6h6" }], ["path", { d: "M21 17a9 9 0 0 0-9-9 9 9 0 0 0-6 2.3L3 13" }]],
    redo: [["path", { d: "M21 7v6h-6" }], ["path", { d: "M3 17a9 9 0 0 1 9-9 9 9 0 0 1 6 2.3l3 2.7" }]],
    history: [["circle", { cx: 12, cy: 12, r: 9 }], ["polyline", { points: "12 7 12 12 15 15" }]],
    before: [["rect", { x: 3, y: 5, width: 18, height: 14, rx: 1.5 }], ["path", { d: "M12 5v14" }]],
    split: [["rect", { x: 3, y: 4, width: 18, height: 16, rx: 1.5 }], ["line", { x1: 12, y1: 2, x2: 12, y2: 22 }], ["polyline", { points: "8 9 5 12 8 15" }], ["polyline", { points: "16 9 19 12 16 15" }]],
    seam: [["path", { d: "M12 3L2 20h20L12 3z" }], ["line", { x1: 12, y1: 9, x2: 12, y2: 14 }], ["circle", { cx: 12, cy: 17, r: 0.8, fill: "currentColor" }]],
    guides: [["rect", { x: 4, y: 4, width: 16, height: 16, "stroke-dasharray": "3 2" }]],
    settings: [["circle", { cx: 12, cy: 12, r: 3 }], ["path", { d: "M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33h.09a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82v.09a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" }]],
    layout: [["rect", { x: 3, y: 4, width: 18, height: 16, rx: 1.5 }], ["line", { x1: 9, y1: 4, x2: 9, y2: 20 }], ["line", { x1: 15, y1: 4, x2: 15, y2: 20 }]],
    download: [["path", { d: "M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" }], ["polyline", { points: "7 10 12 15 17 10" }], ["line", { x1: 12, y1: 15, x2: 12, y2: 3 }]],
    upload: [["path", { d: "M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" }], ["polyline", { points: "17 8 12 3 7 8" }], ["line", { x1: 12, y1: 3, x2: 12, y2: 15 }]],
    scissors: [["circle", { cx: 6, cy: 6, r: 3 }], ["circle", { cx: 6, cy: 18, r: 3 }], ["line", { x1: 20, y1: 4, x2: 8.12, y2: 15.88 }], ["line", { x1: 14.47, y1: 14.48, x2: 20, y2: 20 }], ["line", { x1: 8.12, y1: 8.12, x2: 12, y2: 12 }]],
    plus: [["line", { x1: 12, y1: 5, x2: 12, y2: 19 }], ["line", { x1: 5, y1: 12, x2: 19, y2: 12 }]],
    trash: [["polyline", { points: "3 6 5 6 21 6" }], ["path", { d: "M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" }], ["line", { x1: 10, y1: 11, x2: 10, y2: 17 }], ["line", { x1: 14, y1: 11, x2: 14, y2: 17 }]],
    lock: [["rect", { x: 4, y: 11, width: 16, height: 10, rx: 2 }], ["path", { d: "M8 11V7a4 4 0 0 1 8 0v4" }]],
    unlock: [["rect", { x: 4, y: 11, width: 16, height: 10, rx: 2 }], ["path", { d: "M8 11V7a4 4 0 0 1 7.6-1.7" }]],
    eye: [["path", { d: "M2 12s3.8-7 10-7 10 7 10 7-3.8 7-10 7S2 12 2 12z" }], ["circle", { cx: 12, cy: 12, r: 3 }]],
    eyeOff: [["path", { d: "M17.94 17.94A10.94 10.94 0 0 1 12 19c-6.2 0-10-7-10-7a18.5 18.5 0 0 1 5.06-5.94" }], ["path", { d: "M9.9 4.24A10.9 10.9 0 0 1 12 4c6.2 0 10 8 10 8a18.5 18.5 0 0 1-2.16 3.19" }], ["line", { x1: 2, y1: 2, x2: 22, y2: 22 }]],
    up: [["polyline", { points: "18 15 12 9 6 15" }]],
    down: [["polyline", { points: "6 9 12 15 18 9" }]],
    star: [["polygon", { points: "12 2 15.1 8.3 22 9.3 17 14.1 18.2 21 12 17.8 5.8 21 7 14.1 2 9.3 8.9 8.3 12 2" }]],
    refresh: [["polyline", { points: "23 4 23 10 17 10" }], ["path", { d: "M20.49 15a9 9 0 1 1-2.12-9.36L23 10" }]],
    expand: [["polyline", { points: "15 3 21 3 21 9" }], ["polyline", { points: "9 21 3 21 3 15" }], ["line", { x1: 21, y1: 3, x2: 14, y2: 10 }], ["line", { x1: 3, y1: 21, x2: 10, y2: 14 }]],
    sparkles: [["path", { d: "M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9L12 3z" }], ["path", { d: "M19 15l.9 2.1L22 18l-2.1.9L19 21l-.9-2.1L16 18l2.1-.9L19 15z" }]],
    correct: [["path", { d: "M12 20h9" }], ["path", { d: "M16.5 3.5a2.12 2.12 0 1 1 3 3L7 19l-4 1 1-4 12.5-12.5z" }]],
    add: [["circle", { cx: 12, cy: 12, r: 9 }], ["line", { x1: 12, y1: 8, x2: 12, y2: 16 }], ["line", { x1: 8, y1: 12, x2: 16, y2: 12 }]],
    remove: [["path", { d: "M20 20H7L3 16a2 2 0 0 1 0-2.8L13.4 2.8a2 2 0 0 1 2.8 0L21 7.6a2 2 0 0 1 0 2.8L11.4 20" }], ["line", { x1: 6, y1: 11, x2: 13, y2: 18 }]],
    vary: [["path", { d: "M12 2a10 10 0 1 0 0 20c1.4 0 2.2-.8 2.2-2 0-.5-.2-1-.5-1.4-.3-.4-.4-.9-.4-1.4 0-1.1.9-2 2-2h2.3A4.4 4.4 0 0 0 22 10.8C22 5.9 17.5 2 12 2z" }], ["circle", { cx: 7.5, cy: 10.5, r: 1, fill: "currentColor" }], ["circle", { cx: 11, cy: 7.5, r: 1, fill: "currentColor" }], ["circle", { cx: 15.5, cy: 8.5, r: 1, fill: "currentColor" }]],
    detail: [["circle", { cx: 11, cy: 11, r: 7 }], ["line", { x1: 21, y1: 21, x2: 16.65, y2: 16.65 }], ["line", { x1: 11, y1: 8, x2: 11, y2: 14 }], ["line", { x1: 8, y1: 11, x2: 14, y2: 11 }]],
    brush: [["path", { d: "M18.4 2.6a2.12 2.12 0 0 1 3 3L11 16l-4 1 1-4L18.4 2.6z" }], ["path", { d: "M7 17c-1.5 0-3 1.2-3 3 2.2 0 4-.8 4-2.4" }]],
    eraser: [["path", { d: "M20 20H7L3 16a2 2 0 0 1 0-2.8L13.4 2.8a2 2 0 0 1 2.8 0L21 7.6a2 2 0 0 1 0 2.8L11.4 20" }], ["line", { x1: 6, y1: 11, x2: 13, y2: 18 }]],
    ratio: [["rect", { x: 3, y: 5, width: 18, height: 14, rx: 1.5 }], ["polyline", { points: "7 13 7 9 11 9" }], ["polyline", { points: "17 11 17 15 13 15" }]],
    layers: [["polygon", { points: "12 2 2 7 12 12 22 7 12 2" }], ["polyline", { points: "2 17 12 22 22 17" }], ["polyline", { points: "2 12 12 17 22 12" }]],
    sliders: [["line", { x1: 4, y1: 21, x2: 4, y2: 14 }], ["line", { x1: 4, y1: 10, x2: 4, y2: 3 }], ["line", { x1: 12, y1: 21, x2: 12, y2: 12 }], ["line", { x1: 12, y1: 8, x2: 12, y2: 3 }], ["line", { x1: 20, y1: 21, x2: 20, y2: 16 }], ["line", { x1: 20, y1: 12, x2: 20, y2: 3 }], ["line", { x1: 1, y1: 14, x2: 7, y2: 14 }], ["line", { x1: 9, y1: 8, x2: 15, y2: 8 }], ["line", { x1: 17, y1: 16, x2: 23, y2: 16 }]],
    wand: [["path", { d: "M15 4V2m0 14v-2M8 9h2m10 0h2m-4.2 3.2l1.4 1.4M10.8 4.8L9.4 3.4m9.8 0l-1.4 1.4M3 21l9-9" }]],
    xCircle: [["circle", { cx: 12, cy: 12, r: 9 }], ["line", { x1: 15, y1: 9, x2: 9, y2: 15 }], ["line", { x1: 9, y1: 9, x2: 15, y2: 15 }]],
    check: [["polyline", { points: "20 6 9 17 4 12" }]],
    swap: [["polyline", { points: "17 1 21 5 17 9" }], ["path", { d: "M3 11V9a4 4 0 0 1 4-4h14" }], ["polyline", { points: "7 23 3 19 7 15" }], ["path", { d: "M21 13v2a4 4 0 0 1-4 4H3" }]],
    cursor: [["path", { d: "M5 3l14 9-6.5 1.5L9 20 5 3z" }]],
    rectTool: [["rect", { x: 4, y: 4, width: 16, height: 16, "stroke-dasharray": "4 2" }]],
    hand: [["path", { d: "M18 11V6a2 2 0 0 0-4 0v4" }], ["path", { d: "M14 10V4a2 2 0 0 0-4 0v6" }], ["path", { d: "M10 10.5V6a2 2 0 0 0-4 0v8" }], ["path", { d: "M18 11a2 2 0 1 1 4 0v3a8 8 0 0 1-8 8h-2c-2.8 0-4.5-.9-5.9-2.3L3.3 17a2 2 0 0 1 2.8-2.8L8 16" }]],
    search: [["circle", { cx: 11, cy: 11, r: 7 }], ["line", { x1: 21, y1: 21, x2: 16.65, y2: 16.65 }]],
    flip: [["polyline", { points: "8 3 3 8 8 13" }], ["polyline", { points: "16 3 21 8 16 13" }], ["line", { x1: 12, y1: 3, x2: 12, y2: 21 }]],
    help: [["circle", { cx: 12, cy: 12, r: 9 }], ["path", { d: "M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3" }], ["circle", { cx: 12, cy: 17, r: 0.8, fill: "currentColor" }]],
    chevronDown: [["polyline", { points: "6 9 12 15 18 9" }]],
    x: [["line", { x1: 18, y1: 6, x2: 6, y2: 18 }], ["line", { x1: 6, y1: 6, x2: 18, y2: 18 }]],
    grip: [
      ["circle", { cx: 9, cy: 6, r: 1.35, fill: "currentColor", stroke: "none" }],
      ["circle", { cx: 15, cy: 6, r: 1.35, fill: "currentColor", stroke: "none" }],
      ["circle", { cx: 9, cy: 12, r: 1.35, fill: "currentColor", stroke: "none" }],
      ["circle", { cx: 15, cy: 12, r: 1.35, fill: "currentColor", stroke: "none" }],
      ["circle", { cx: 9, cy: 18, r: 1.35, fill: "currentColor", stroke: "none" }],
      ["circle", { cx: 15, cy: 18, r: 1.35, fill: "currentColor", stroke: "none" }],
    ],
    arrowLeft: [["line", { x1: 19, y1: 12, x2: 5, y2: 12 }], ["polyline", { points: "12 19 5 12 12 5" }]],
    dockLeft: [["rect", { x: 3, y: 4, width: 18, height: 16, rx: 2 }], ["line", { x1: 9, y1: 4, x2: 9, y2: 20 }], ["rect", { x: 4.5, y: 6, width: 3, height: 12, rx: 0.5, fill: "currentColor", stroke: "none", opacity: "0.45" }]],
    dockRight: [["rect", { x: 3, y: 4, width: 18, height: 16, rx: 2 }], ["line", { x1: 15, y1: 4, x2: 15, y2: 20 }], ["rect", { x: 16.5, y: 6, width: 3, height: 12, rx: 0.5, fill: "currentColor", stroke: "none", opacity: "0.45" }]],
    floatWin: [["rect", { x: 3, y: 4, width: 14, height: 12, rx: 2 }], ["path", { d: "M8 20h11a2 2 0 0 0 2-2V9" }]],
    gridSnap: [["path", { d: "M3 9h18M3 15h18M9 3v18M15 3v18" }]],
    maximize: [["polyline", { points: "15 3 21 3 21 9" }], ["polyline", { points: "9 21 3 21 3 15" }], ["line", { x1: 21, y1: 3, x2: 14, y2: 10 }], ["line", { x1: 3, y1: 21, x2: 10, y2: 14 }]],
    minimize: [["polyline", { points: "4 14 10 14 10 20" }], ["polyline", { points: "20 10 14 10 14 4" }], ["line", { x1: 14, y1: 10, x2: 21, y2: 3 }], ["line", { x1: 3, y1: 21, x2: 10, y2: 14 }]],
    pin: [["path", { d: "M12 17v5" }], ["path", { d: "M9 10.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24V16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V7a1 1 0 0 1 1-1 2 2 0 0 0 0-4H8a2 2 0 0 0 0 4 1 1 0 0 1 1 1z" }]],
    gridView: [["rect", { x: 3, y: 3, width: 7, height: 7, rx: 1 }], ["rect", { x: 14, y: 3, width: 7, height: 7, rx: 1 }], ["rect", { x: 3, y: 14, width: 7, height: 7, rx: 1 }], ["rect", { x: 14, y: 14, width: 7, height: 7, rx: 1 }]],
    listView: [["line", { x1: 8, y1: 6, x2: 21, y2: 6 }], ["line", { x1: 8, y1: 12, x2: 21, y2: 12 }], ["line", { x1: 8, y1: 18, x2: 21, y2: 18 }], ["circle", { cx: 4, cy: 6, r: 1, fill: "currentColor", stroke: "none" }], ["circle", { cx: 4, cy: 12, r: 1, fill: "currentColor", stroke: "none" }], ["circle", { cx: 4, cy: 18, r: 1, fill: "currentColor", stroke: "none" }]],
    arrowRight: [["line", { x1: 5, y1: 12, x2: 19, y2: 12 }], ["polyline", { points: "12 5 19 12 12 19" }]],
    image: [["rect", { x: 3, y: 3, width: 18, height: 18, rx: 2 }], ["circle", { cx: 8.5, cy: 8.5, r: 1.5 }], ["polyline", { points: "21 15 16 10 5 21" }]],
  };

  function icon(name, size = 14) {
    const svg = svgEl("svg", {
      viewBox: "0 0 24 24", width: size, height: size,
      fill: "none", stroke: "currentColor", "stroke-width": 1.9,
      "stroke-linecap": "round", "stroke-linejoin": "round",
      class: "v2-ico", "aria-hidden": "true",
    });
    for (const [tag, attrs] of (ICON_DEFS[name] || [])) {
      svg.append(svgEl(tag, attrs));
    }
    return svg;
  }

  // 모델 계열별 상세 작업자 가이드 메타 (2열 검색형 모델 팝오버 우측 가이드 패널용)
  const MODEL_GUIDE = {
    "gpt-image-2.6-sunburst": {
      shortName: "GPT 2.6 Sunburst",
      family: "OpenAI · 정밀 편집",
      badge: "추천 · 정밀 수정",
      syntax: "natural",
      summary: "지시문 이해도와 원화 화풍 유지력이 가장 균형 잡힌 기본 편집 모델",
      strengths: ["복잡한 한글·영문 지시 반영", "다중 레퍼런스 일관성", "의상·장식 정밀 교체"],
      speed: "보통",
      guide: {
        when: "의상 장식 보정, 부분 형태 수정, 다중 레퍼런스 참조 패칭 등 대부분의 정밀 수정 작업에 가장 먼저 선택하세요.",
        strengths: "복잡한 한국어 의도 파악, 최대 15장 레퍼런스 일관성, 마스크 바깥 영역 보존력이 뛰어납니다.",
        caution: "해상도와 품질을 최대로 올리면 생성 시간이 다소 길어질 수 있습니다.",
        tip: "시트에서 오린 레퍼런스를 @참조1 로 연결하고 '수정 · 보정' 목적과 함께 쓰면 성공률이 가장 높습니다.",
      },
    },
    "gpt-image-2.6-flare": {
      shortName: "GPT 2.6 Flare",
      family: "OpenAI · 고속 편집",
      badge: "고속 · 시안 탐색",
      syntax: "natural",
      summary: "빠르게 여러 바리에이션을 뽑아볼 때 유리한 경량 고속 모델",
      strengths: ["빠른 후보 탐색", "간단한 색상·재질 변경"],
      speed: "빠름",
      guide: {
        when: "색상 변경, 빠른 1차 시안 탐색, 여러 영역을 동시에 빠르게 테스트할 때 유리합니다.",
        strengths: "Sunburst와 동일한 지시 문법을 쓰면서 응답 속도가 빠릅니다.",
        caution: "극도로 세밀한 금속 문양·장신구 정합은 Sunburst가 더 안정적입니다.",
        tip: "Flare로 빠르게 구도·방향을 잡은 뒤 마음에 드는 영역만 Sunburst로 마감해 보세요.",
      },
    },
    "gpt-image-2.5-sunburst": {
      shortName: "GPT 2.5 Sunburst",
      family: "OpenAI · 2.5 안정형",
      badge: "안정형",
      syntax: "natural",
      summary: "기존 2.5 세대 표준 편집 모델(이전 프로젝트 호환용)",
      strengths: ["안정적인 마스크 경계 보존", "기존 결과물 톤 유지"],
      speed: "보통",
      guide: {
        when: "이전 2.5 세대로 작업하던 프로젝트의 톤과 화풍을 그대로 이어가고 싶을 때 사용하세요.",
        strengths: "마스크 경계 안정성과 일관된 톤 유지력이 검증되어 있습니다.",
        caution: "최신 2.6 대비 복합 지시문 반영력은 다소 보수적입니다.",
        tip: "기존 프로젝트 리터칭 시 톤 차이를 줄이고 싶을 때 선택하면 좋습니다.",
      },
    },
    "gpt-image-2.5-flare": {
      shortName: "GPT 2.5 Flare",
      family: "OpenAI · 2.5 고속",
      badge: "고속 2.5",
      syntax: "natural",
      summary: "2.5 세대 고속 시안 모델",
      strengths: ["빠른 테스트"],
      speed: "빠름",
      guide: {
        when: "2.5 세대 파이프라인에서 가볍게 시안을 빠르게 확인할 때 사용합니다.",
        strengths: "빠른 응답과 안정적인 기본 편집 품질을 제공합니다.",
        caution: "복잡한 구조 교체에는 2.6 Sunburst를 권장합니다.",
        tip: "단순한 소품 제거·배색 테스트에 적합합니다.",
      },
    },
    "seedream-5.5": {
      shortName: "Seedream 5.5",
      family: "ByteDance · 질감/합성",
      badge: "질감 · 리터칭",
      syntax: "natural",
      summary: "부드러운 회화 질감과 자연스러운 광원 융합에 강한 모델",
      strengths: ["피부·헤어·천 질감 보정", "자연스러운 광원 합성", "넓은 비율 지원"],
      speed: "보통",
      guide: {
        when: "소품 추가, 원단·피부·머리카락 리터칭, 기존 조명에 자연스럽게 녹아드는 합성이 필요할 때 추천합니다.",
        strengths: "회화적 질감 표현력과 주변 광원·그림자 융합 능력이 매우 자연스럽습니다.",
        caution: "동시 대량 요청 시 서버 운영 부하 수칙(동시 잡 × 장수)이 자동 관리됩니다.",
        tip: "'요소 추가' 목적과 시트 컷 레퍼런스를 함께 사용하면 이질감 없는 합성이 가능합니다.",
      },
    },
    "ideogram-4.5": {
      shortName: "Ideogram 4.5",
      family: "Ideogram · 정리/문양",
      badge: "정리 · 문양",
      syntax: "natural",
      summary: "문양·로고·텍스트·규칙적 패턴 및 깔끔한 영역 정리에 특화된 모델",
      strengths: ["텍스트·엠블럼·문양 정확도", "선명한 그래픽 윤곽", "깔끔한 배경 복원"],
      speed: "보통",
      guide: {
        when: "불필요한 요소 제거·정리, 문양·엠블럼·로고·텍스트가 포함된 의상·소품 수정에 가장 뛰어납니다.",
        strengths: "기하학적 패턴과 문양 윤곽이 뭉개지지 않고 깔끔하게 떨어집니다.",
        caution: "최대 참조 장수가 4장으로 제한되므로 핵심 레퍼런스 위주로 연결하세요.",
        tip: "깔끔한 제거·복원이나 갑옷 문양·텍스트 수정 시 선택하면 실패율이 크게 줄어듭니다.",
      },
    },
    "nano-banana-2.1": {
      shortName: "Nano Banana 2.1",
      family: "Google · 고해상도 추론",
      badge: "고해상도 · 추론",
      syntax: "natural",
      summary: "지정 비율·고해상도(최대 4K)와 단계적 추론을 지원하는 최신 계열",
      strengths: ["정교한 구도 추론", "1K/2K/4K 해상도 선택", "다중 참조 이해"],
      speed: "보통",
      guide: {
        when: "디자인 변형, 고해상도(2K/4K) 디테일 업, 복잡한 공간·원근 추론이 필요한 수정에 강력합니다.",
        strengths: "추론 깊이(간단히/보통/깊게)와 1K·2K·4K 해상도, 지정 비율 정합을 직접 고를 수 있습니다.",
        caution: "지정 비율 외 크롭은 '비율 맞춤' 버튼으로 틀을 맞추면 구도 왜곡을 예방할 수 있습니다.",
        tip: "복잡한 변형 지시에는 추론 수준을 '깊게'로 올리고 2K 이상 해상도를 사용해 보세요.",
      },
    },
    "nano-banana-2": {
      shortName: "Nano Banana 2",
      family: "Google · 고속 비율형",
      badge: "고속 비율형",
      syntax: "natural",
      summary: "빠른 응답 속도로 지정 비율 패치를 생성하는 모델",
      strengths: ["빠른 생성", "지정 비율 정합"],
      speed: "빠름",
      guide: {
        when: "지정 비율 기반의 빠른 변형 시안을 여러 장 뽑아볼 때 유용합니다.",
        strengths: "가볍고 빠른 응답으로 반복 탐색에 유리합니다.",
        caution: "최고 난도 추론·4K 마감은 Nano Banana 2.1을 권장합니다.",
        tip: "초안 탐색 후 2.1 또는 Pro로 최종 마감하는 워크플로우에 잘 맞습니다.",
      },
    },
    "nano-banana-pro": {
      shortName: "Nano Banana Pro",
      family: "Google · 디테일 프로",
      badge: "디테일 프로",
      syntax: "natural",
      summary: "세밀한 표현과 정교한 텍스처 묘사에 강한 프로 모델",
      strengths: ["고밀도 디테일", "복잡한 소재 표현"],
      speed: "느림",
      guide: {
        when: "최종 폴리싱 단계에서 복잡한 금속·보석·자수 질감을 고밀도로 마감할 때 선택하세요.",
        strengths: "미세 텍스처와 명암 대비 완성도가 매우 높습니다.",
        caution: "속도가 다소 느리므로 확정된 구도에서 최종 패스용으로 권장합니다.",
        tip: "2차 라운드 디테일 강화 단계에서 쓰면 효과가 가장 두드러집니다.",
      },
    },
  };

  const DEFAULT_MODELS = [
    { name: "gpt-image-2.6-sunburst", label: "GPT Image 2.6 Sunburst", kind: "gpt", available: true, why: null, max_inputs: 16, max_refs: 15, max_n: 10, supports_quality: true, qualities: ["max", "xhigh", "high", "medium", "low"], default_quality: "max", complexities: ["low", "medium", "high"], opts: {}, opt_defaults: {}, ratios: [], ratio_sizes: {}, ratio_tolerance: null },
    { name: "gpt-image-2.6-flare", label: "GPT Image 2.6 Flare", kind: "gpt", available: true, why: null, max_inputs: 16, max_refs: 15, max_n: 10, supports_quality: true, qualities: ["max", "xhigh", "high", "medium", "low"], default_quality: "max", complexities: ["low", "medium", "high"], opts: {}, opt_defaults: {}, ratios: [], ratio_sizes: {}, ratio_tolerance: null },
    { name: "gpt-image-2.5-sunburst", label: "GPT Image 2.5 Sunburst", kind: "gpt", available: true, why: null, max_inputs: 16, max_refs: 15, max_n: 10, supports_quality: true, qualities: ["max", "xhigh", "high", "medium", "low"], default_quality: "max", complexities: [], opts: {}, opt_defaults: {}, ratios: [], ratio_sizes: {}, ratio_tolerance: null },
    { name: "gpt-image-2.5-flare", label: "GPT Image 2.5 Flare", kind: "gpt", available: true, why: null, max_inputs: 16, max_refs: 15, max_n: 10, supports_quality: true, qualities: ["max", "xhigh", "high", "medium", "low"], default_quality: "max", complexities: [], opts: {}, opt_defaults: {}, ratios: [], ratio_sizes: {}, ratio_tolerance: null },
  ];

  // Gemini 비율 기준 1K 픽셀 표 (DPLayoutGeom.ratioFitRect 용)
  const RATIO_SIZES = {
    "1:1": [1024, 1024], "4:5": [928, 1152], "3:4": [896, 1200], "2:3": [848, 1264], "9:16": [768, 1376],
    "5:4": [1152, 928], "4:3": [1200, 896], "3:2": [1264, 848], "16:9": [1376, 768], "21:9": [1584, 672],
  };

  const THINKING_LABELS = { minimal: "간단히", medium: "보통", high: "깊게" };

  function el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    if (attrs && typeof attrs === "object") {
      for (const [k, v] of Object.entries(attrs)) {
        if (v === undefined || v === null || v === false) continue;
        if (k === "className") node.className = String(v);
        else if (k === "text") node.textContent = String(v);
        else if (k === "dataset" && typeof v === "object") {
          for (const [dk, dv] of Object.entries(v)) node.dataset[dk] = String(dv);
        } else if (k.startsWith("on") && typeof v === "function") {
          node.addEventListener(k.slice(2).toLowerCase(), v);
        } else if (v === true) {
          node.setAttribute(k, "");
        } else {
          node.setAttribute(k, String(v));
        }
      }
    }
    for (const child of children) {
      if (child === null || child === undefined || child === false) continue;
      node.append(typeof child === "string" || typeof child === "number" ? document.createTextNode(String(child)) : child);
    }
    return node;
  }

  function svgEl(tag, attrs = {}) {
    const node = document.createElementNS(SVG_NS, tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v !== undefined && v !== null) node.setAttribute(k, String(v));
    }
    return node;
  }

  // DPDialog 공용 호출 래퍼 (sandbox iframe 에서 안전 — 네이티브 다이얼로그 미사용 · #C19-12: DPDialog.confirm 평범하게 호출)
  function askDialogConfirm(message, options = {}) {
    const dlg = window.DPDialog;
    if (!dlg || typeof dlg.confirm !== "function") return Promise.resolve(false);
    const normalized = {
      ok: options.ok || options.okText || "확인",
      cancel: options.cancel || options.cancelText || "취소",
      danger: !!options.danger,
    };
    return dlg.confirm(message, normalized);
  }

  function showDialogNotice(message) {
    const dlg = window.DPDialog;
    return dlg && typeof dlg.alert === "function" ? dlg.alert(message) : Promise.resolve();
  }

  // #C15-5: 닫기(Esc·배경 클릭)와 「버리기」를 명확히 구분하는 다중 선택 대화 상자 (sandbox iframe 안전 · textContent 전용)
  function askDialogChoice(message, choices = [], options = {}) {
    const cancelId = options.cancelId || "cancel";
    const dlg = window.DPDialog;
    if (!dlg || typeof dlg.lock !== "function" || typeof dlg.unlock !== "function") {
      return Promise.resolve(cancelId);
    }
    const list = Array.isArray(choices) && choices.length
      ? choices
      : [{ id: "ok", label: "확인" }, { id: cancelId, label: "취소" }];
    let finish = null;
    const buttons = [];
    const handleKeys = (e) => {
      if (["Tab", "Enter", " ", "Spacebar", "Escape"].includes(e.key)) e.preventDefault();
      if (e.type !== "keydown" || !finish) return;
      if (e.key === "Escape") {
        finish(cancelId);
        return;
      }
      const idx = buttons.indexOf(document.activeElement);
      if (e.key === "Tab" && buttons.length) {
        buttons[(idx + (e.shiftKey ? buttons.length - 1 : 1)) % buttons.length].focus();
      } else if (e.key === "Enter" || e.key === " " || e.key === "Spacebar") {
        if (idx >= 0 && list[idx]) finish(list[idx].id);
      }
    };
    if (!dlg.lock({ keys: handleKeys })) return Promise.resolve(cancelId);
    return new Promise((resolve) => {
      let settled = false;
      const overlay = document.createElement("div");
      const sheet = document.createElement("div");
      const row = document.createElement("div");
      overlay.className = "overlay dp-dialog";
      sheet.className = "sheet";
      row.className = "lp-btns";
      sheet.setAttribute("role", "dialog");
      sheet.setAttribute("aria-modal", "true");
      sheet.setAttribute("aria-label", options.title || "확인");
      const lines = Array.isArray(message) ? message : String(message || "").split("\n");
      for (const line of lines) {
        const p = document.createElement("p");
        p.textContent = line || "\u00a0";
        sheet.appendChild(p);
      }
      finish = (choiceId) => {
        if (settled) return;
        settled = true;
        overlay.remove();
        dlg.unlock();
        resolve(choiceId);
      };
      overlay.addEventListener("click", (e) => {
        if (e.target === overlay) finish(cancelId);
      });
      for (const item of list) {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.textContent = item.label || item.id;
        if (item.primary) btn.className = "primary";
        else if (item.danger) btn.className = "danger";
        btn.addEventListener("click", () => finish(item.id));
        row.appendChild(btn);
        buttons.push(btn);
      }
      sheet.appendChild(row);
      overlay.appendChild(sheet);
      document.body.appendChild(overlay);
      const focusIdx = list.findIndex((x) => x.id === options.focusId);
      const targetBtn = buttons[focusIdx >= 0 ? focusIdx : 0];
      if (targetBtn) targetBtn.focus();
    });
  }

  function hexMix(a, b, w) {
    const clean = (h) => {
      const s = String(h || "#000000").replace("#", "");
      return s.length === 3 ? s.split("").map((c) => c + c).join("") : s.padEnd(6, "0");
    };
    const ca = clean(a), cb = clean(b);
    return "#" + [0, 2, 4].map((i) => {
      const va = parseInt(ca.slice(i, i + 2), 16) || 0;
      const vb = parseInt(cb.slice(i, i + 2), 16) || 0;
      return Math.round(va * (1 - w) + vb * w).toString(16).padStart(2, "0");
    }).join("");
  }

  function hexToRgba(hex, alpha) {
    const h = String(hex || "#dfe104").replace("#", "");
    const r = parseInt(h.slice(0, 2), 16) || 223;
    const g = parseInt(h.slice(2, 4), 16) || 225;
    const b = parseInt(h.slice(4, 6), 16) || 4;
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
  }

  function computeAutoCropFrame(rect, imgW, imgH) {
    const [x1, y1, x2, y2] = Array.isArray(rect) ? rect : [rect.x, rect.y, rect.x + rect.w, rect.y + rect.h];
    const rw = Math.max(1, x2 - x1), rh = Math.max(1, y2 - y1);
    const padX = Math.max(48, Math.round(rw * 0.22));
    const padY = Math.max(48, Math.round(rh * 0.22));
    let w = Math.min(imgW, Math.max(256, Math.ceil((rw + padX * 2) / 128) * 128));
    let h = Math.min(imgH, Math.max(256, Math.ceil((rh + padY * 2) / 128) * 128));
    const cx = (x1 + x2) / 2, cy = (y1 + y2) / 2;
    let x = Math.round((cx - w / 2) / 16) * 16;
    let y = Math.round((cy - h / 2) / 16) * 16;
    x = Math.max(0, Math.min(imgW - w, x));
    y = Math.max(0, Math.min(imgH - h, y));
    return { x, y, w, h };
  }

  // #C36-5: projects/new 서버 허용 원화 확장자(v1 board.js 와 동일)
  const TARGET_ACCEPT = ".psd,.psb,.png,.jpg,.jpeg,.webp";
  const TARGET_EXT_RE = /\.(psd|psb|png|jpe?g|webp)$/i;
  const MAX_UPLOAD_BYTES = 2 * 1024 ** 3;

  function validateTargetFile(file) {
    if (!file) return "타깃 원화 이미지를 먼저 선택하거나 끌어 놓아 주세요.";
    if (typeof file.size === "number" && file.size > MAX_UPLOAD_BYTES) {
      return "파일은 2GB 까지 올릴 수 있습니다";
    }
    if (!TARGET_EXT_RE.test(String(file.name || ""))) {
      return "PSD·PSB·PNG·JPG·WEBP 파일만 받습니다";
    }
    return "";
  }

  function validateProjectName(raw) {
    const name = String(raw || "").trim();
    if (!name || !/^[\p{L}\p{N}_.-]+$/u.test(name)) return "이름에는 문자·숫자·_ - . 만 쓸 수 있습니다";
    if (name.includes("..") || name.endsWith(".")) return "'..' 또는 '.' 로 끝나는 이름은 쓸 수 없습니다";
    if (/^(con|prn|aux|nul|com[0-9]|lpt[0-9])(\..*)?$/i.test(name)) return "Windows 예약 이름은 쓸 수 없습니다";
    return "";
  }

  function suggestProjectName(filename) {
    let base = String(filename || "")
      .replace(/\.[^.]+$/, "")
      .replace(/[^\p{L}\p{N}_.-]+/gu, "_")
      .replace(/\.{2,}/g, "_")
      .replace(/^[_.-]+|[_.-]+$/g, "");
    // #C36-5: 64자 절단 뒤 끝이 '.'·'_'·'-' 로 남지 않도록 정리한 뒤 다시 검사한다
    base = base.slice(0, 64).replace(/[_.-]+$/g, "").replace(/^[_.-]+/g, "");
    if (!base || validateProjectName(base)) {
      base = base ? `proj_${base}`.slice(0, 64).replace(/\.+$/g, "") : "new_project";
    }
    if (!base || validateProjectName(base)) return "new_project";
    return base;
  }

  function projectDisplayName(proj) {
    if (!proj || typeof proj !== "object") return "";
    return String(proj.title || proj.name || proj.project || proj.id || "");
  }

  function formatRelativeTime(raw) {
    const s = String(raw || "").trim();
    if (!s) return "";
    const normalized = s.includes("T") ? s : s.replace(" ", "T");
    const ts = Date.parse(normalized);
    if (!Number.isFinite(ts)) return s;
    const diffSec = Math.floor((Date.now() - ts) / 1000);
    if (diffSec < 0) return s.slice(0, 16);
    if (diffSec < 60) return "방금 전";
    const diffMin = Math.floor(diffSec / 60);
    if (diffMin < 60) return `${diffMin}분 전`;
    const diffHour = Math.floor(diffMin / 60);
    if (diffHour < 24) return `${diffHour}시간 전`;
    const diffDay = Math.floor(diffHour / 24);
    if (diffDay === 1) return "어제";
    if (diffDay < 7) return `${diffDay}일 전`;
    return s.slice(0, 10);
  }

  function formatElapsed(sec) {
    if (typeof sec !== "number" || !Number.isFinite(sec) || sec < 0) return "";
    if (sec < 9.95) return `${Math.round(sec * 10) / 10}초`;
    const s = Math.round(sec);
    if (s < 60) return `${s}초`;
    const m = Math.floor(s / 60);
    if (m < 60) return `${m}분` + (s % 60 ? ` ${s % 60}초` : "");
    return `${Math.floor(m / 60)}시간` + (m % 60 ? ` ${m % 60}분` : "");
  }

  // 신호등 품질 번역기: 전문 지표(MAD / gates / seams) → 작업자 직관 3단계
  function trafficLight(cand, seamInfo) {
    if (!cand) return { level: "none", icon: "⚪", label: "후보 없음", detail: "아직 생성된 후보가 없습니다." };
    const fails = (cand.gates && Array.isArray(cand.gates.fails)) ? cand.gates.fails : [];
    const badSeam = !!(seamInfo && seamInfo.bad > 0);
    const weakSeam = !!(seamInfo && seamInfo.n > 0 && !badSeam);
    if (fails.length > 0 || badSeam || cand.stale_rect || cand.external_missing) {
      const reasons = [];
      if (fails.length) reasons.push(`품질 점검 주의(${fails.join(", ")})`);
      if (badSeam) reasons.push(`경계 이음매 눈에 띔(${seamInfo.bad}곳)`);
      if (cand.stale_rect) reasons.push("영역 변경 전 생성된 후보");
      if (cand.external_missing) reasons.push("원본 파일 없음");
      return { level: "bad", icon: "🔴", label: "재확인 권장", detail: reasons.join(" · ") };
    }
    if (weakSeam || cand.reframed || cand.old_cell || cand.prompt_changed) {
      const notes = [];
      if (weakSeam) notes.push(`경계 이음매 미세 주의(${seamInfo.n}곳)`);
      if (cand.reframed) notes.push("구도 자동 보정됨");
      if (cand.old_cell) notes.push("이전 설정 조건 후보");
      if (cand.prompt_changed) notes.push("서버가 프롬프트를 조정함");
      return { level: "warn", icon: "🟡", label: "경계·조건 확인", detail: notes.join(" · ") };
    }
    return { level: "ok", icon: "🟢", label: "경계 자연스러움", detail: "원본 영역 보존 및 톤 매칭이 안정적입니다." };
  }

  // v1 ↔ v2 전환 주소 생성 (#C27-1 · STUDIO_UX_V2_HOME_SPEC §10.3-1: v1 전환 시 항상 ui=v1 명시, root·parent_origin 등 쿼리 유지)
  function buildSwitchUrl(toV2) {
    const url = new URL(location.href);
    const slash = url.pathname.lastIndexOf("/");
    const dir = url.pathname.slice(0, slash + 1);
    const file = url.pathname.slice(slash + 1);
    if (file === "v2.html" || file === "board.html") {
      url.pathname = dir + (toV2 ? "v2.html" : "board.html");
    } else {
      url.pathname = dir;
    }
    if (toV2) url.searchParams.set("ui", "v2");
    else url.searchParams.set("ui", "v1");
    return url.pathname + url.search;
  }

  function purposeInfo(id) {
    return PURPOSES.find((p) => p.id === id) || PURPOSES[0];
  }

  function purposeColor(id, mode = "dark") {
    const p = purposeInfo(id);
    const pal = p && p.color ? p.color : { dark: "#60a5fa", light: "#2563eb" };
    return mode === "light" ? pal.light : pal.dark;
  }

  function appendSvgIcon(parentSvg, name, x, y, size, strokeColor, strokeW = 2.1) {
    const nested = svgEl("svg", {
      x, y, width: size, height: size,
      viewBox: "0 0 24 24",
      fill: "none",
      stroke: strokeColor,
      "stroke-width": strokeW,
      "stroke-linecap": "round",
      "stroke-linejoin": "round",
    });
    for (const [tag, attrs] of (ICON_DEFS[name] || [])) {
      const a = Object.assign({}, attrs);
      if (a.fill === "currentColor") a.fill = strokeColor;
      nested.append(svgEl(tag, a));
    }
    parentSvg.append(nested);
    return nested;
  }

  const COMPLEXITY_LABELS = { low: "낮음", medium: "보통", high: "높음 (기본)" };

  function modelCatalog() {
    const live = window.DPEnv && window.DPEnv.caps && Array.isArray(window.DPEnv.caps.models)
      ? window.DPEnv.caps.models : null;
    const base = live && live.length ? live : DEFAULT_MODELS;
    return base.map((m) => {
      const guide = MODEL_GUIDE[m.name] || {
        shortName: m.label || m.name,
        family: m.kind ? String(m.kind).toUpperCase() : "생성 모델",
        badge: m.kind ? String(m.kind).toUpperCase() : "생성 모델",
        syntax: "natural",
        summary: `${m.label || m.name} 생성 모델`,
        strengths: [],
        speed: "보통",
        guide: {
          when: `${m.label || m.name} 모델로 편집을 수행합니다.`,
          strengths: "기본 품질과 자연어 지시 반영을 지원합니다.",
          caution: "목적에 맞는 레퍼런스를 연결하면 정확도가 높아집니다.",
          tip: "후보를 비교한 뒤 후보정 탭에서 경계를 미세 조정할 수 있습니다.",
        },
      };
      const qualities = Array.isArray(m.qualities) ? m.qualities : [];
      const maxInputs = Number(m.max_inputs) || 10;
      const maxRefs = Number.isInteger(m.max_refs) ? m.max_refs : Math.max(0, maxInputs - 1);
      const supportsQuality = typeof m.supports_quality === "boolean" ? m.supports_quality : qualities.length > 0;
      const complexities = Array.isArray(m.complexities)
        ? m.complexities
        : (/^gpt-image-2\.6-/.test(String(m.name || "")) ? ["low", "medium", "high"] : []);
      const ratios = Array.isArray(m.ratios) ? m.ratios : [];
      const ratioSizes = (m.ratio_sizes && typeof m.ratio_sizes === "object" && Object.keys(m.ratio_sizes).length)
        ? m.ratio_sizes
        : (ratios.length ? RATIO_SIZES : {});
      const ratioTolerance = typeof m.ratio_tolerance === "number" ? m.ratio_tolerance : (ratios.length ? 0.01 : null);
      const family = (typeof m.family === "string" && m.family.trim())
        ? m.family.trim()
        : (guide.family || (m.kind ? String(m.kind).toUpperCase() : ""));
      return Object.assign({}, guide, m, {
        label: m.label || m.name,
        shortName: guide.shortName || m.label || m.name,
        family,
        available: typeof m.available === "boolean" ? m.available : true,
        why: m.why || null,
        qualities,
        supports_quality: supportsQuality,
        complexities,
        ratios,
        ratio_sizes: ratioSizes,
        ratio_tolerance: ratioTolerance,
        opts: (m.opts && typeof m.opts === "object") ? m.opts : {},
        opt_defaults: (m.opt_defaults && typeof m.opt_defaults === "object") ? m.opt_defaults : {},
        max_n: Number(m.max_n) || 4,
        max_inputs: maxInputs,
        max_refs: maxRefs,
      });
    });
  }

  function findModelExact(name) {
    if (!name || typeof name !== "string") return null;
    const list = modelCatalog();
    return list.find((m) => m.name === name) || null;
  }

  function findModel(name) {
    const list = modelCatalog();
    return list.find((m) => m.name === name) || list[0] || null;
  }

  // #C19-10: GET /state 후보(candidates[].model·quality·opts)의 모델 꼬리표 문자열 생성
  function formatCandidateModelTag(cand) {
    if (!cand || typeof cand !== "object" || !cand.model) return "";
    const mi = findModelExact(cand.model);
    const parts = [mi ? (mi.shortName || mi.label) : cand.model];
    const opts = (cand.opts && typeof cand.opts === "object") ? cand.opts : null;
    if (mi && mi.supports_quality && cand.quality) {
      parts.push(cand.quality);
    } else if (!mi && cand.quality && cand.quality !== "standard") {
      parts.push(cand.quality);
    }
    if (opts && opts.image_size) parts.push(opts.image_size);
    if (opts && opts.thinking_level) parts.push(THINKING_LABELS[opts.thinking_level] || opts.thinking_level);
    return parts.join(" · ");
  }

  // 효과 값(effective) 또는 프로젝트 기본 설정 한 줄 요약 (확인 상자·요약 줄용)
  function formatModelSummary(eff) {
    if (!eff || !eff.model) return "";
    const mi = findModelExact(eff.model);
    const parts = [mi ? mi.label : eff.model];
    if (mi ? mi.supports_quality : (eff.quality && eff.quality !== "standard")) {
      if (eff.quality) parts.push(`품질 ${eff.quality}`);
    }
    if (mi && mi.complexities && mi.complexities.length && eff.complexity) {
      parts.push(`복잡도 ${eff.complexity}`);
    }
    const opts = (eff.opts && typeof eff.opts === "object") ? eff.opts : null;
    if (opts && opts.image_size && (!mi || (mi.opts && mi.opts.image_size))) {
      parts.push(`해상도 ${opts.image_size}`);
    }
    if (opts && opts.thinking_level && (!mi || (mi.opts && mi.opts.thinking_level))) {
      parts.push(`추론 ${THINKING_LABELS[opts.thinking_level] || opts.thinking_level}`);
    }
    if (Number.isInteger(eff.n) && eff.n > 0) parts.push(`${eff.n}장`);
    return parts.join(" · ");
  }

  return {
    THEMES, POINT_COLORS, LAYOUT_PRESETS, PURPOSES, SNAP_STEPS, MODEL_GUIDE, RATIO_SIZES, THINKING_LABELS, COMPLEXITY_LABELS, TARGET_ACCEPT,
    el, svgEl, icon, appendSvgIcon, askDialogConfirm, askDialogChoice, showDialogNotice, hexMix, hexToRgba, computeAutoCropFrame,
    validateTargetFile, validateProjectName, suggestProjectName, projectDisplayName, formatRelativeTime, formatElapsed, trafficLight, buildSwitchUrl, purposeInfo, purposeColor, modelCatalog, findModelExact, findModel, formatCandidateModelTag, formatModelSummary,
  };
})();
