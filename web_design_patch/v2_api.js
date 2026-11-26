// BMK Design Patch Studio v2 — 백엔드 API 클라이언트(상대 경로 전용 · D4/D5 준수 · 유료 제출은 공용 모듈 위임).
"use strict";

window.DPV2Api = (() => {
  const API_BASE = new URL("../", location.href);
  const ASSIST_TIMEOUT_MS = 90000; // 계약 §7.3 C2: 90초 제한, 자동 재시도 없음

  function apiUrl(rel) {
    if (!rel) return null;
    const u = new URL(rel, API_BASE);
    return u.origin === location.origin ? u.href : null;
  }

  function qs(params) {
    const p = new URLSearchParams();
    for (const [k, v] of Object.entries(params || {})) {
      if (v !== undefined && v !== null && v !== "") p.set(k, String(v));
    }
    return p.toString();
  }

  async function readJson(res) {
    try { return await res.json(); }
    catch (_) { return { error: `응답을 읽을 수 없습니다 (HTTP ${res.status})`, unreadable: true }; }
  }

  async function getJson(rel, signal) {
    const url = apiUrl(rel);
    if (!url) throw new Error("허용되지 않은 주소입니다");
    const res = await fetch(url, { cache: "no-store", signal });
    const data = await readJson(res);
    if (!res.ok || data.unreadable) {
      throw Object.assign(new Error(data.error || `HTTP ${res.status}`), { status: res.status, data });
    }
    return data;
  }

  async function postJson(rel, body, signal) {
    const url = apiUrl(rel);
    if (!url) throw new Error("허용되지 않은 주소입니다");
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
      signal,
    });
    const data = await readJson(res);
    if (!res.ok || data.unreadable) {
      throw Object.assign(new Error(data.error || `HTTP ${res.status}`), { status: res.status, data });
    }
    return data;
  }

  function can(cap) {
    return !!(window.DPEnv && window.DPEnv.can && window.DPEnv.can(cap));
  }

  // ── 1. 프로젝트 목록 · 생성 · 열기 · 메타 관리(#C31 · caps.v2_project_home 게이트) ──
  const listProjects = () => getJson("projects");
  const openProject = (key) => postJson("projects/open", { key });

  async function listArchivedProjects() {
    if (!can("v2_project_home")) {
      return { projects: [], archived_count: 0 };
    }
    // #C31-8 · #C31-15: 보관함 열기 시 1회 조회(경로 글자 그대로)
    return getJson("projects?archived=1");
  }

  async function updateProjectMeta({ key, seen, op, title } = {}) {
    if (!can("v2_project_home")) {
      return { ok: false, unsupported: true, error: "프로젝트 홈 관리 기능이 아직 켜져 있지 않습니다." };
    }
    const seenInt = Number(seen);
    const body = {
      key: String(key || ""),
      seen: Number.isInteger(seenInt) && seenInt >= 0 ? seenInt : 0,
      op,
    };
    // #C31-9: rename 에서만 title 문자열을 전송(archive·restore 는 title 미포함)
    if (op === "rename") {
      body.title = String(title ?? "");
    }
    return postJson("projects/meta", body);
  }

  async function createProjectFile(name, file) {
    const url = apiUrl("projects/new?" + qs({ name, filename: file.name }));
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/octet-stream" },
      body: file,
    });
    const data = await readJson(res);
    if (!res.ok || !/^[0-9a-f]{12}$/.test(data.key || "")) {
      throw Object.assign(new Error(data.error || `프로젝트 생성 실패 (HTTP ${res.status})`), { status: res.status, data });
    }
    return data;
  }

  // ── 2. 상태 · 레이아웃 · Z-Order ──
  const getState = (root, sinceRev) => getJson("state?" + qs({ root, since_rev: sinceRev }));
  const getLayout = (root) => getJson("layout?" + qs({ root }));
  const startLayout = (root) => postJson("layout/start", { root });
  const mutateCrop = (root, rev, body) => postJson("layout/crop", Object.assign({ root, rev }, body));
  const reorderZ = (root, body) => postJson("zorder", Object.assign({ root }, body));

  // ── 3. 레퍼런스 · 시트 오려내기 ──
  const getRefs = (root, since) => getJson("refs?" + qs({ root, since }));
  const listSheets = (root) => getJson("refs/sheets?" + qs({ root }));

  function sheetUrl(root, sh, kind = "preview", rect = null, max = null) {
    const q = { root, sheet: sh.path, kind };
    if (rect) { q.rect = rect.join(","); q.max = max || 2048; }
    if (sh.v) q.v = sh.v;
    return apiUrl("refs/sheet?" + qs(q));
  }

  async function uploadSheet(root, file, rev) {
    if (file.size > 2 * 1024 ** 3) throw new Error("파일은 2GB 까지 올릴 수 있습니다");
    const url = apiUrl("refs/pool?" + qs({ root, op: "sheet_add", filename: file.name, rev: rev ?? "null" }));
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/octet-stream" },
      body: file,
    });
    const data = await readJson(res);
    if (!res.ok || data.unreadable) {
      throw Object.assign(new Error(data.error || `시트 올리기 실패 (HTTP ${res.status})`), { status: res.status, data });
    }
    return data;
  }

  const cutRef = (root, body) => postJson("refs/cut", Object.assign({ root }, body));
  const saveRef = (root, rev, body) => postJson("refs/save", Object.assign({ root, rev }, body));
  const linkRef = (root, rev, body) => postJson("refs/link", Object.assign({ root, rev }, body));

  // ── 4. 파이프라인 · 계획 · 진행 · 후보 채택 · 중단 · 내보내기 ──
  const getPipeline = (root) => getJson("pipeline?" + qs({ root }));
  const savePipelineSettings = (root, rev, section, values) => postJson("pipeline/settings", { root, rev, section, values });
  const getPlan = (root, only, style) => getJson("pipeline/plan?" + qs(only ? { root, only, style: style ? "1" : "0" } : { root }));
  const getProgress = (root) => getJson("pipeline/progress?" + qs({ root }));
  const pickCandidate = (root, body) => postJson("pick", Object.assign({ root }, body));
  const rejectCandidate = (root, key, value) => postJson("reject", { root, key, value });
  const drainRun = (root) => postJson("drain", { root });
  const getSeams = (root, sig) => getJson("seams?" + qs({ root, sig }));
  const saveToForge = (root) => postJson("forge/save", { root });
  const exportDownloadUrl = (root) => apiUrl("export/file?" + qs({ root }));

  // ── 5. 마스크 · 후보정 변형 (L 8-bit PNG 인코더 내장) ──
  const getMask = (root, key) => getJson("mask?" + qs({ root, key }));
  const adjustMask = (root, body) => postJson("mask/adjust", Object.assign({ root }, body));

  async function fetchImageRgba(rel) {
    const url = apiUrl(rel);
    if (!url) throw new Error("이미지 주소가 잘못되었습니다");
    const res = await fetch(url);
    if (!res.ok) throw new Error(`이미지 불러오기 실패 (HTTP ${res.status})`);
    const blob = await res.blob();
    const bmp = await createImageBitmap(blob, { colorSpaceConversion: "none", premultiplyAlpha: "none" });
    const cv = document.createElement("canvas");
    cv.width = bmp.width;
    cv.height = bmp.height;
    const ctx = cv.getContext("2d", { willReadFrequently: true });
    ctx.drawImage(bmp, 0, 0);
    bmp.close();
    return { w: cv.width, h: cv.height, data: ctx.getImageData(0, 0, cv.width, cv.height).data };
  }

  const CRC_TABLE = new Uint32Array(256).map((_, n) => {
    let c = n;
    for (let k = 0; k < 8; k++) c = (c & 1) ? (0xedb88320 ^ (c >>> 1)) : (c >>> 1);
    return c >>> 0;
  });

  function crc32(bytes) {
    let c = 0xffffffff;
    for (let i = 0; i < bytes.length; i++) c = CRC_TABLE[(c ^ bytes[i]) & 255] ^ (c >>> 8);
    return (c ^ 0xffffffff) >>> 0;
  }

  function pngChunk(type, data) {
    const out = new Uint8Array(12 + data.length);
    const dv = new DataView(out.buffer);
    dv.setUint32(0, data.length);
    for (let i = 0; i < 4; i++) out[4 + i] = type.charCodeAt(i);
    out.set(data, 8);
    dv.setUint32(8 + data.length, crc32(out.subarray(4, 8 + data.length)));
    return out;
  }

  async function encodePngL(px, w, h) {
    const raw = new Uint8Array((w + 1) * h);
    for (let y = 0; y < h; y++) {
      const o = y * (w + 1);
      raw[o] = 2; // Up 필터
      for (let x = 0; x < w; x++) raw[o + 1 + x] = (px[y * w + x] - (y ? px[(y - 1) * w + x] : 0)) & 255;
    }
    const z = new Uint8Array(await new Response(new Blob([raw]).stream().pipeThrough(new CompressionStream("deflate"))).arrayBuffer());
    const head = new Uint8Array(13);
    const dv = new DataView(head.buffer);
    dv.setUint32(0, w);
    dv.setUint32(4, h);
    head[8] = 8; // 8-bit grayscale(L)
    return new Blob([
      new Uint8Array([137, 80, 78, 71, 13, 10, 26, 10]),
      pngChunk("IHDR", head),
      pngChunk("IDAT", z),
      pngChunk("IEND", new Uint8Array(0)),
    ], { type: "image/png" });
  }

  async function saveMaskPng(root, params, px, w, h) {
    const sp = new URLSearchParams();
    sp.set("root", String(root || ""));
    for (const [k, v] of Object.entries(params || {})) {
      if (v !== undefined && v !== null) sp.set(k, String(v));
    }
    const url = apiUrl("mask/save?" + sp.toString());
    const blob = await encodePngL(px, w, h);
    const res = await fetch(url, { method: "POST", headers: { "Content-Type": "image/png" }, body: blob });
    const data = await readJson(res);
    if (res.status === 409 && data) return Object.assign({ conflict: true }, data);
    if (!res.ok || data.unreadable) {
      throw Object.assign(new Error(data.error || `마스크 저장 실패 (HTTP ${res.status})`), { status: res.status, data });
    }
    return data;
  }

  // ── 6. V2 계약 확장 라우트 (STUDIO_UX_V2A_SPEC §3.2–§3.4 · caps 게이트 · §7.3 C1/C2) ──
  async function getSpec(root, cropId) {
    if (!can("v2_spec_edit")) return null;
    return getJson("spec?" + qs({ root, crop_id: cropId }));
  }

  async function saveSpec(root, body) {
    if (!can("v2_spec_edit")) {
      // 계약 §7.2 B2 · Sync #C01: v1 /draft/adopt 폴백 금지(v1 대상 문구·생성 조건 키 보호)
      return {
        ok: false,
        unsupported: true,
        reason: "지시문 저장은 아직 연결되지 않았습니다.",
      };
    }
    // §3.3: 허용 키 외의 키는 400 이므로 계약 키만 정확히 전송
    const payload = {
      root,
      crop_id: body.crop_id,
      seen: body.seen ?? "null",
      crop_sig: body.crop_sig ?? "",
    };
    if ("purpose" in body && body.purpose !== undefined) payload.purpose = body.purpose;
    if ("user_instruction_ko" in body && body.user_instruction_ko !== undefined) payload.user_instruction_ko = body.user_instruction_ko;
    if ("ai_auto" in body && body.ai_auto !== undefined) payload.ai_auto = !!body.ai_auto;
    if ("raw_prompt_override" in body && body.raw_prompt_override !== undefined) payload.raw_prompt_override = body.raw_prompt_override;
    return postJson("spec", payload);
  }

  async function assistPrompt(root, body) {
    if (!can("v2_forge_prompt")) {
      return {
        ok: false,
        status: "unsupported",
        reason: "Phase V2-A 백엔드 브릿지(prompt/assist) 연결 대기 중입니다.",
        skill_ids: [],
        summary_ko: "",
        body_en: "",
        prompt_en: "",
      };
    }
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), ASSIST_TIMEOUT_MS);
    try {
      // §3.4: 서버가 저장된 스펙의 목적·지시문·모델을 직접 읽음 — UI는 식별자·seen·crop_sig·force 만 전송
      const payload = {
        root,
        crop_id: body.crop_id,
        seen: body.seen ?? "null",
        crop_sig: body.crop_sig ?? "",
      };
      if (body.force) payload.force = true;
      return await postJson("prompt/assist", payload, ctrl.signal);
    } catch (err) {
      if (err && err.name === "AbortError") {
        throw Object.assign(new Error("AI 프롬프트 작성이 90초 제한을 초과했습니다 — 잠시 후 다시 시도해 주세요."), { isTimeout: true });
      }
      throw err;
    } finally {
      clearTimeout(timer);
    }
  }

  // ── 6.5 V2-B 패치별 생성 설정 (STUDIO_UX_V2B_SPEC §10.3 · #C19 · caps.v2_crop_model 게이트) ──
  async function getCropGen(root) {
    if (!can("v2_crop_model")) return null;
    return getJson("crop_gen?" + qs({ root }));
  }

  async function postCropGen(root, seen, changes) {
    if (!can("v2_crop_model")) {
      return { ok: false, unsupported: true, error: "패치별 모델 선택 기능이 아직 켜져 있지 않습니다." };
    }
    return postJson("crop_gen", { root, seen, changes });
  }

  // ── 7. 유료·무료 실행 제출 어댑터 (STUDIO_UX_V2A_SPEC §3.6 · 계약 §7.4 D4·D5: 공용 모듈 DPSubmit 만 호출) ──
  function submitAdapter() {
    return window.DPSubmit || null;
  }

  async function submitStage(stage, options = {}) {
    const mod = submitAdapter();
    if (!mod) {
      return {
        ok: false,
        pendingIntegration: true,
        message: "실행 제출 공용 모듈(Phase V2-A submit.js · DPSubmit)이 연결되면 v2에서 바로 실행됩니다. 지금은 우측 상단 [기존 화면(v1)]에서 동일 프로젝트로 실행할 수 있습니다.",
      };
    }
    if (stage === "apply" || stage === "export") {
      if (typeof mod.submitFree === "function") return mod.submitFree(stage, { root: options.root });
    }
    if (stage === "run") {
      // §3.6: submitPaid("run", { root, only }) — 화면의 plan 은 넘기지 않음(모듈이 최신 계획을 직접 조회)
      const paidOpts = { root: options.root };
      if (options.only) paidOpts.only = options.only;
      if (typeof mod.submitPaid === "function") return mod.submitPaid(stage, paidOpts);
    }
    if (stage === "draft" || stage === "reroll" || stage === "postprocess") {
      if (typeof mod.submitPaid === "function") return mod.submitPaid(stage, options);
    }
    return { ok: false, reason: "unsupported", message: "이 단계에서 지원되지 않는 제출 유형입니다." };
  }

  async function applyChecked(root) {
    const mod = submitAdapter();
    if (!mod) {
      return {
        ok: false,
        pendingIntegration: true,
        message: "실행 제출 공용 모듈(Phase V2-A submit.js · DPSubmit)이 연결되면 v2에서 바로 실행됩니다. 지금은 우측 상단 [기존 화면(v1)]에서 동일 프로젝트로 실행할 수 있습니다.",
      };
    }
    if (typeof mod.applyChecked === "function") {
      return mod.applyChecked({ root });
    }
    return { ok: false, reason: "unsupported", error: "확인하는 무료 반영(DPSubmit.applyChecked)을 사용할 수 없습니다." };
  }

  async function waitJob(root, promptId, opts) {
    const mod = submitAdapter();
    if (mod && typeof mod.wait === "function" && promptId) {
      return mod.wait(root, promptId, opts);
    }
    return null;
  }

  function getJobQueue(root) {
    return getJson("jobs/queue?" + qs({ root }));
  }

  return {
    apiUrl, can,
    listProjects, listArchivedProjects, openProject, createProjectFile, updateProjectMeta,
    getState, getLayout, startLayout, mutateCrop, reorderZ,
    getRefs, listSheets, sheetUrl, uploadSheet, cutRef, saveRef, linkRef,
    getPipeline, savePipelineSettings, getPlan, getProgress,
    pickCandidate, rejectCandidate, drainRun,
    getSeams, saveToForge, exportDownloadUrl,
    getMask, adjustMask, fetchImageRgba, encodePngL, saveMaskPng,
    getSpec, saveSpec, assistPrompt, getCropGen, postCropGen, submitAdapter, submitStage, applyChecked, waitJob, getJobQueue,
  };
})();
