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

  // 작업자 친화 목적 5종 (계약 §3.1)
  const PURPOSES = [
    { id: "correct", label: "수정", short: "수정", desc: "형태·색감·의상 디테일을 자연스럽게 보정" },
    { id: "add", label: "추가", short: "추가", desc: "레퍼런스 소품·장식·요소를 자연스럽게 합성" },
    { id: "remove", label: "제거", short: "제거", desc: "불필요한 장식·오염·요소를 주변 배경으로 정리" },
    { id: "vary", label: "변형", short: "변형", desc: "구도나 실루엣을 유지하며 다른 디자인 바리에이션" },
    { id: "detail", label: "디테일", short: "디테일", desc: "질감·재질·광택·선명도 밀도를 높이기" },
  ];

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
    el, svgEl, askDialogConfirm, showDialogNotice, hexToRgba, validateProjectName,
    formatElapsed, trafficLight, buildSwitchUrl, purposeInfo, modelCatalog, findModel,
  };
})();
