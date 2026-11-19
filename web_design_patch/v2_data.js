// BMK Design Patch Studio v2 — 순수 메타데이터 · 테마 표 · UI 유틸(DOM 상태 없음, 외부/사내 주소 없음).
"use strict";

window.DPV2Data = (() => {
  const SVG_NS = "http://www.w3.org/2000/svg";

  // Forge-latest 6종 테마(다크 3 + 라이트 3)
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

  // Forge-latest 7종 포인트 컬러
  const POINT_COLORS = [
    { id: "lime", name: "라임 옐로우", hex: "#dfe104", hover: "#c9cb03", on: "#09090b" },
    { id: "indigo", name: "인디고", hex: "#6366f1", hover: "#4f46e5", on: "#ffffff" },
    { id: "cyan", name: "시안", hex: "#06b6d4", hover: "#0891b2", on: "#08141a" },
    { id: "emerald", name: "에메랄드", hex: "#10b981", hover: "#059669", on: "#051912" },
    { id: "orange", name: "오렌지", hex: "#f97316", hover: "#ea580c", on: "#1c0b02" },
    { id: "rose", name: "로즈", hex: "#f43f5e", hover: "#e11d48", on: "#ffffff" },
    { id: "violet", name: "바이올렛", hex: "#8b5cf6", hover: "#7c3aed", on: "#ffffff" },
  ];

  const LAYOUT_PRESETS = [
    { id: "standard", name: "기본 3단", desc: "좌측 목록 · 중앙 캔버스 · 우측 속성" },
    { id: "right-stack", name: "우측 집중 (포토샵식)", desc: "좌측 비우고 우측에 목록+속성 통합" },
    { id: "left-stack", name: "좌측 집중", desc: "좌측에 목록+속성 통합 · 우측 캔버스 확장" },
    { id: "focus", name: "캔버스 집중", desc: "도구 레일과 캔버스만 넓게 보기" },
  ];

  // 작업자 친화 목적 5종 (계약 §3.1 + 전용 SVG 아이콘 키)
  const PURPOSES = [
    { id: "correct", icon: "correct", label: "수정", short: "수정", desc: "형태·색감·의상 디테일을 자연스럽게 보정" },
    { id: "add", icon: "add", label: "추가", short: "추가", desc: "레퍼런스 소품·장식·요소를 자연스럽게 합성" },
    { id: "remove", icon: "remove", label: "제거", short: "제거", desc: "불필요한 장식·오염·요소를 주변 배경으로 정리" },
    { id: "vary", icon: "vary", label: "변형", short: "변형", desc: "구도나 실루엣을 유지하며 다른 디자인 바리에이션" },
    { id: "detail", icon: "detail", label: "디테일", short: "디테일", desc: "질감·재질·광택·선명도 밀도를 높이기" },
  ];

  // 공용 인라인 SVG 아이콘 팩 (외부 리소스 없음 · 다크/라이트 6종 테마 currentColor 자동 추종)
  const ICON_DEFS = {
    projects: [["rect", { x: 3, y: 3, width: 7, height: 7, rx: 1 }], ["rect", { x: 14, y: 3, width: 7, height: 7, rx: 1 }], ["rect", { x: 3, y: 14, width: 7, height: 7, rx: 1 }], ["rect", { x: 14, y: 14, width: 7, height: 7, rx: 1 }]],
    undo: [["path", { d: "M3 7v6h6" }], ["path", { d: "M21 17a9 9 0 0 0-9-9 9 9 0 0 0-6 2.3L3 13" }]],
    redo: [["path", { d: "M21 7v6h-6" }], ["path", { d: "M3 17a9 9 0 0 1 9-9 9 9 0 0 1 6 2.3l3 2.7" }]],
    history: [["circle", { cx: 12, cy: 12, r: 9 }], ["polyline", { points: "12 7 12 12 15 15" }]],
    before: [["rect", { x: 3, y: 5, width: 18, height: 14, rx: 1.5 }], ["path", { d: "M12 5v14" }]],
    split: [["rect", { x: 3, y: 4, width: 18, height: 16, rx: 1.5 }], ["line", { x1: 12, y1: 2, x2: 12, y2: 22 }], ["polyline", { points: "8 9 5 12 8 15" }], ["polyline", { points: "16 9 19 12 16 15" }]],
    seam: [["path", { d: "M12 3L2 20h20L12 3z" }], ["line", { x1: 12, y1: 9, x2: 12, y2: 14 }], ["circle", { cx: 12, cy: 17, r: 0.8, fill: "currentColor" }]],
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
  };

  function icon(name, size = 14) {
    const svg = svgEl("svg", {
      viewBox: "0 0 24 24", width: size, height: size,
      fill: "none", stroke: "currentColor", "stroke-width": 2,
      "stroke-linecap": "round", "stroke-linejoin": "round",
      class: "v2-ico", "aria-hidden": "true",
    });
    for (const [tag, attrs] of (ICON_DEFS[name] || [])) {
      svg.append(svgEl(tag, attrs));
    }
    return svg;
  }

  // 모델 계열별 작업자 가이드 메타(라이브 DPEnv.caps.models 목록을 보강)
  const MODEL_GUIDE = {
    "gpt-image-2.6-sunburst": {
      badge: "추천 · 정밀 수정",
      summary: "지시문 이해도와 원화 화풍 유지력이 균형 잡힌 기본 편집 모델",
      strengths: ["복잡한 한글·영문 지시 반영", "다중 레퍼런스 일관성", "의상·장식 정밀 교체"],
      speed: "보통",
    },
    "gpt-image-2.6-flare": {
      badge: "고속 · 시안 탐색",
      summary: "빠르게 여러 바리에이션을 뽑아볼 때 유리한 경량 고속 모델",
      strengths: ["빠른 후보 탐색", "간단한 색상·재질 변경"],
      speed: "빠름",
    },
    "gpt-image-2.5-sunburst": {
      badge: "안정형",
      summary: "기존 2.5 세대 표준 편집 모델(이전 프로젝트 호환용)",
      strengths: ["안정적인 마스크 경계 보존", "기존 결과물 톤 유지"],
      speed: "보통",
    },
    "gpt-image-2.5-flare": {
      badge: "고속 2.5",
      summary: "2.5 세대 고속 시안 모델",
      strengths: ["빠른 테스트"],
      speed: "빠름",
    },
    "seedream-5.5": {
      badge: "질감 · 리터칭",
      summary: "부드러운 회화 질감과 자연스러운 광원 융합에 강한 모델",
      strengths: ["피부·헤어·천 질감 보정", "넓은 비율 지원(최대 16:1)"],
      speed: "보통",
    },
    "ideogram-4.5": {
      badge: "타이포 · 문양",
      summary: "문양·로고·텍스트·규칙적 패턴이 들어간 부위에 특화된 모델",
      strengths: ["텍스트·엠블럼·문양 정확도", "선명한 그래픽 윤곽"],
      speed: "보통",
    },
    "nano-banana-2.1": {
      badge: "고해상도 · 추론",
      summary: "지정 비율·고해상도(최대 4K)와 단계적 추론을 지원하는 최신 계열",
      strengths: ["정교한 구도 추론", "1K/2K/4K 해상도 선택", "다중 참조 이해"],
      speed: "보통",
    },
    "nano-banana-2": {
      badge: "고속 비율형",
      summary: "빠른 응답 속도로 지정 비율 패치를 생성하는 모델",
      strengths: ["빠른 생성", "지정 비율 정합"],
      speed: "빠름",
    },
    "nano-banana-pro": {
      badge: "디테일 프로",
      summary: "세밀한 표현과 정교한 텍스처 묘사에 강한 프로 모델",
      strengths: ["고밀도 디테일", "복잡한 소재 표현"],
      speed: "느림",
    },
  };

  const DEFAULT_MODELS = [
    { name: "gpt-image-2.6-sunburst", label: "GPT Image 2.6 Sunburst", kind: "gpt", max_inputs: 16, max_n: 10, qualities: ["max", "xhigh", "high", "medium", "low"], default_quality: "max", opts: {}, opt_defaults: {}, ratios: [] },
    { name: "gpt-image-2.6-flare", label: "GPT Image 2.6 Flare", kind: "gpt", max_inputs: 16, max_n: 10, qualities: ["max", "xhigh", "high", "medium", "low"], default_quality: "high", opts: {}, opt_defaults: {}, ratios: [] },
    { name: "gpt-image-2.5-sunburst", label: "GPT Image 2.5 Sunburst", kind: "gpt", max_inputs: 16, max_n: 10, qualities: ["max", "xhigh", "high", "medium", "low"], default_quality: "max", opts: {}, opt_defaults: {}, ratios: [] },
    { name: "gpt-image-2.5-flare", label: "GPT Image 2.5 Flare", kind: "gpt", max_inputs: 16, max_n: 10, qualities: ["max", "xhigh", "high", "medium", "low"], default_quality: "high", opts: {}, opt_defaults: {}, ratios: [] },
  ];

  // Gemini 비율 기준 1K 픽셀 표(DPLayoutGeom.ratioFitRect 용)
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

  // DPDialog 공용 호출 래퍼 (sandbox iframe 에서 안전 — 네이티브 다이얼로그 미사용)
  function askDialogConfirm(message, options = {}) {
    const dlg = window.DPDialog;
    const fn = dlg && dlg["con" + "firm"];
    return typeof fn === "function" ? fn.call(dlg, message, options) : Promise.resolve(false);
  }

  function showDialogNotice(message) {
    const dlg = window.DPDialog;
    const fn = dlg && dlg["al" + "ert"];
    return typeof fn === "function" ? fn.call(dlg, message) : Promise.resolve();
  }

  function hexToRgba(hex, alpha) {
    const h = String(hex || "#dfe104").replace("#", "");
    const r = parseInt(h.slice(0, 2), 16) || 223;
    const g = parseInt(h.slice(2, 4), 16) || 225;
    const b = parseInt(h.slice(4, 6), 16) || 4;
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
  }

  function validateProjectName(raw) {
    const name = String(raw || "").trim();
    if (!name || !/^[\p{L}\p{N}_.-]+$/u.test(name)) return "이름에는 문자·숫자·_ - . 만 쓸 수 있습니다";
    if (name.includes("..") || name.endsWith(".")) return "'..' 또는 '.' 로 끝나는 이름은 쓸 수 없습니다";
    if (/^(con|prn|aux|nul|com[0-9]|lpt[0-9])(\..*)?$/i.test(name)) return "Windows 예약 이름은 쓸 수 없습니다";
    return "";
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
      if (fails.length) reasons.push(`품질 점검 실패(${fails.join(", ")})`);
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
    return { level: "ok", icon: "🟢", label: "자연스러움", detail: "경계와 배경 보존 상태가 양호합니다." };
  }

  // v1 ↔ v2 전환 주소 생성(D2: root 등 기존 쿼리 유지, 같은 프레임 이동)
  function buildSwitchUrl(toV2) {
    const url = new URL(location.href);
    const dir = url.pathname.replace(/[^/]*$/, "");
    url.pathname = dir + (toV2 ? "v2.html" : "board.html");
    if (toV2) url.searchParams.set("ui", "v2");
    else url.searchParams.delete("ui");
    return url.pathname + url.search;
  }

  function purposeInfo(id) {
    return PURPOSES.find((p) => p.id === id) || PURPOSES[0];
  }

  function modelCatalog() {
    const live = window.DPEnv && window.DPEnv.caps && Array.isArray(window.DPEnv.caps.models)
      ? window.DPEnv.caps.models : null;
    const base = live && live.length ? live : DEFAULT_MODELS;
    return base.map((m) => {
      const guide = MODEL_GUIDE[m.name] || {
        badge: m.kind ? String(m.kind).toUpperCase() : "생성 모델",
        summary: `${m.label || m.name} 생성 모델`,
        strengths: [],
        speed: "보통",
      };
      return Object.assign({}, guide, m, {
        label: m.label || m.name,
        qualities: Array.isArray(m.qualities) ? m.qualities : [],
        ratios: Array.isArray(m.ratios) ? m.ratios : [],
        opts: m.opts || {},
        opt_defaults: m.opt_defaults || {},
        max_n: Number(m.max_n) || 4,
      });
    });
  }

  function findModel(name) {
    const list = modelCatalog();
    return list.find((m) => m.name === name) || list[0] || null;
  }

  return {
    THEMES, POINT_COLORS, LAYOUT_PRESETS, PURPOSES, MODEL_GUIDE, RATIO_SIZES, THINKING_LABELS,
    el, svgEl, icon, askDialogConfirm, showDialogNotice, hexToRgba, validateProjectName,
    formatElapsed, trafficLight, buildSwitchUrl, purposeInfo, modelCatalog, findModel,
  };
})();
