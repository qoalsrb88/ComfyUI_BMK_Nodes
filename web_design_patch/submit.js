// 서버 모드 생성 승인과 작업 제출. 승인한 계획 해시만 서버에 보낸다.
"use strict";

window.DPSubmit = (() => {
  let active = null, paying = false;
  const server = () => window.DPEnv?.serverMode === true;
  const failed = (reason, error) => ({ ok: false, reason, error });
  const serverOnly = () => failed("server_only", "서버 모드에서만 사용할 수 있습니다");
  const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  function request(path, query, options) {
    const url = new URL(path, new URL("../", location.href));
    if (!server() || url.origin !== location.origin) throw new Error("서버 주소를 확인할 수 없습니다");
    for (const [key, value] of Object.entries(query)) url.searchParams.set(key, value);
    return fetch(url, { cache: "no-store", mode: "same-origin", redirect: "error", ...options });
  }

  function post(body) {
    return postJob("jobs/prompt", body);
  }

  async function postJob(path, body) {
    if (!server()) return serverOnly();
    try {
      const response = await request(path, {}, { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body) });
      const result = await response.json().catch(() => null);
      const postprocess = path === "postprocess/run";
      if (response.ok && (!postprocess || result?.ok === true) && typeof result?.prompt_id === "string" && result.prompt_id) {
        const job = { ok: true, prompt_id: result.prompt_id };
        if (postprocess) {
          const { number, summary } = result;
          job.number = number;
          if (["new", "reuse", "recover", "blocked"].every(key => Number.isSafeInteger(summary?.[key]) && summary[key] >= 0)) {
            job.summary = summary;
          }
        }
        return job;
      }
      const clientError = response.status >= 400 && response.status < 500;
      const error = result?.error || `HTTP ${response.status}`;
      if (postprocess && (clientError || (response.status === 503 && result?.reason === "forge_unreachable"))) {
        return failed(result?.reason || `http_${response.status}`, error);
      }
      if (clientError && typeof result?.reason === "string" && result.reason) {
        return { ...failed(result.reason, error), hash: result.hash, pending: result.pending };
      }
      return failed("unknown", result?.error || `작업 응답을 확인하지 못했습니다(HTTP ${response.status})`);
    } catch (e) { return failed("unknown", e?.message || String(e)); }
  }

  function askKeys(e) {
    if (!active) return;
    if (e.type !== "keyup") e.stopPropagation();
    if (["Tab", "Enter", " ", "Spacebar"].includes(e.key)) e.preventDefault();
    if ((!e.type || e.type === "keydown") && e.key === "Escape") {
      e.preventDefault();
      active(false);
    }
  }

  function ask(lines, warnings = [], label = "생성 승인") {
    const dialog = window.DPDialog;
    if (active || !dialog?.lock({ keys: askKeys })) return Promise.resolve(false);
    return new Promise((resolve) => {
      const box = document.createElement("div"), sheet = document.createElement("div"), row = document.createElement("div");
      box.className = "overlay"; sheet.className = "sheet"; row.className = "lp-btns";
      sheet.setAttribute("role", "dialog");
      sheet.setAttribute("aria-modal", "true");
      sheet.setAttribute("aria-label", label);
      function close(ok) {
        if (active !== close) return;
        active = null;
        box.remove();
        dialog.unlock();
        resolve(ok);
      }
      active = close;
      for (const [items, cls] of [[lines, ""], [warnings, "warn"]]) {
        for (const line of items) {
          const p = document.createElement("p");
          p.className = cls; p.textContent = line; sheet.append(p);
        }
      }
      for (const ok of [true, false]) {
        const b = document.createElement("button");
        b.type = "button"; b.tabIndex = -1;
        b.title = ok ? label : "Esc"; b.textContent = ok ? "승인" : "취소";
        if (ok) b.className = "primary";
        b.addEventListener("click", (e) => {
          e.currentTarget.blur();
          if (e.isTrusted) close(ok);
        });
        row.append(b);
      }
      sheet.append(row); box.append(sheet); document.body.append(box);
      sheet.setAttribute("tabindex", "-1"); sheet.focus();
    });
  }

  function modelInfo(name) {
    const models = server() && window.DPEnv.caps?.models;
    return Array.isArray(models) && models.find((m) => m.name === name);
  }

  function modelText(p) {
    const m = modelInfo(p.model);
    function option(key) {
      const choices = m?.opts?.[key];
      if (!choices?.length) return null;
      if (p.opts) return choices.includes(p.opts[key]) ? p.opts[key] : null;
      if (!(key in p)) return null;
      return choices.includes(p[key]) ? p[key] : m.opt_defaults?.[key];
    }
    const size = option("image_size"), level = option("thinking_level");
    const thinking = { minimal: "간단히", medium: "보통", high: "깊게" };
    const hasComplexity = Array.isArray(m?.complexities) ? m.complexities.length : /^gpt-image-2\.6-/.test(p.model);
    return (m?.label || p.model) + (m && !m.qualities.length ? "" : ` · 품질 ${p.quality}`)
      + (hasComplexity ? ` · 복잡도 ${p.complexity || "high"}` : "")
      + (size ? ` · 해상도 ${size}` : "") + (level ? ` · 추론 ${thinking[level]}` : "");
  }

  async function read(path, query, options) {
    const response = await request(path, query, options);
    const data = await response.json();
    if (!response.ok) throw new Error(data?.error || `HTTP ${response.status}`);
    return { data, status: response.status };
  }

  async function plan(root, only) {
    if (!server()) return null;
    const path = "pipeline/plan", query = only ? { root, only } : { root };
    // 계획 잠금은 서버 전체에 하나라 다른 요청과 겹치면 202 busy 다 — 0.5초 간격으로 여섯 번까지 읽고, 끝내 바쁘면 그 본문({busy})을 돌려준다.
    let result = await read(path, query);
    for (let i = 0; i < 5 && result.status === 202 && result.data?.busy; i++) {
      await delay(500);
      result = await read(path, query);
    }
    return result.data;
  }

  async function wait(root, promptId, { timeoutMs = 600000, intervalMs = 1000 } = {}) {
    if (!server()) return { done: false, ...serverOnly() };
    const deadline = Date.now() + timeoutMs, interval = Math.max(1000, intervalMs || 1000);
    const controller = new AbortController();
    const timedOut = { done: false, reason: "timeout" };
    let timer;
    const timeout = new Promise((resolve) => {
      timer = setTimeout(() => { controller.abort(); resolve(timedOut); }, timeoutMs);
    });
    async function poll() {
      let failures = 0;
      while (Date.now() < deadline && !controller.signal.aborted) {
        try {
          const { data } = await read("jobs/history/" + encodeURIComponent(promptId), { root }, { signal: controller.signal });
          if (Date.now() >= deadline || controller.signal.aborted) break;
          if (!data || typeof data !== "object" || Array.isArray(data)) throw new Error("작업 기록을 읽지 못했습니다");
          if (Object.hasOwn(data, promptId)) {
            const record = data[promptId], status = record?.status?.status_str;
            if (status !== "success" && status !== "error") throw new Error("작업 상태를 읽지 못했습니다");
            return { done: true, ok: status === "success", record };
          }
          failures = 0;
        } catch (e) {
          if (controller.signal.aborted || Date.now() >= deadline) break;
          if (++failures === 3) return { done: false, reason: "read_failed" };
        }
        await delay(Math.min(interval, Math.max(0, deadline - Date.now())));
      }
      return timedOut;
    }
    try { return await Promise.race([poll(), timeout]); }
    finally { clearTimeout(timer); }
  }

  async function submitFree(stage, { root } = {}) {
    if (!server()) return serverOnly();
    if (!["apply", "export"].includes(stage)) return failed("unsupported", "지원하지 않는 무료 작업입니다");
    return post({ root, stage });
  }

  function unnamed(state, ids) {
    const names = new Map(state.crops.map((c) => [c.id, c.layout_diff?.name || c.name || ""]));
    for (const c of state.layout_added || []) names.set(c.id, c.name || "");
    const bad = [...names].filter(([id, name]) => ids.has(id) && name.includes("_미정")).map(([, name]) => name);
    return bad.length ? `이름 미정 크롭 ${bad.length}개(${bad.slice(0, 3).join(", ")}${bad.length > 3 ? " …" : ""}) — ② 크롭에서 이름을 정한 뒤 승인하세요` : "";
  }

  const sigOf = (p) => (Number.isInteger(p?.rev) && Number.isInteger(p?.manifest_rev) ? `${p.rev}:${p.manifest_rev}` : undefined);
  const unread = () => failed("plan_error", "실행 설정을 읽지 못했습니다 — ① 준비에서 실행 설정을 확인한 뒤 다시 눌러 주세요");
  const notes = (p) => (Array.isArray(p.notes) ? p.notes : []);
  const trouble = (p) => (!p ? failed("plan_error", "계획을 읽지 못했습니다")
    : p.busy ? failed("busy", "서버가 계획을 계산하는 중입니다 — 잠시 뒤 다시 눌러 주세요")
    : !p.run || p.run.error ? failed("plan_error", p.run?.error || "계획을 읽지 못했습니다")
    : p.run.running ? failed("running", "생성이 실행 중입니다 — 끝난 뒤 다시 눌러 주세요") : null);

  async function applyPlan(root, pipe, current) {
    if (notes(current).length && !await ask(["무료 반영을 먼저 실행합니다(과금 없음). 실행 설정이 마지막 반영과 다릅니다.",
      ...notes(current), "계속할까요?"], [], "반영 확인")) {
      return failed("cancelled", "반영을 취소했습니다");
    }
    const job = await post({ root, stage: "apply", expect: sigOf(pipe) });
    if (!job.ok) return failed("apply_failed", `${job.reason} · ${job.error}`);
    const result = await wait(root, job.prompt_id);
    if (result.done && result.ok) return { ...job, summary: result.record.apply_summary };
    // 끝을 못 본 반영이 나중에 실행되지 않도록 대기에서 뺀다. 이미 도는 중이면 서버가 그대로 둔다.
    if (!result.done) {
      await request("jobs/queue", {}, { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ root, delete: [job.prompt_id] }) }).catch(() => null);
    }
    const detail = result.record?.status?.messages?.find(([kind]) => kind === "execution_error")?.[1]?.exception_message;
    return { ...failed("apply_failed", detail || `무료 반영을 완료하지 못했습니다 (${result.reason || "작업 실패"})`), prompt_id: job.prompt_id };
  }

  async function applyChecked({ root } = {}) {
    if (!server()) return serverOnly();
    if (paying) return failed("busy", "앞선 제출을 처리하는 중입니다");
    paying = true;
    try {
      const pipe = (await read("pipeline", { root })).data;
      if (!sigOf(pipe)) return unread();
      const current = await plan(root);
      return trouble(current) || await applyPlan(root, pipe, current);
    } catch (e) { return failed("plan_error", e?.message || String(e)); }
    finally { paying = false; }
  }

  function approvalLines(run, prepare) {
    if (!Number.isSafeInteger(run?.need) || run.need < 0) return null;
    let groups = run.groups;
    if (Array.isArray(groups)) {
      if (groups.some(g => !Number.isSafeInteger(g?.need) || g.need < 1 || !Number.isSafeInteger(g.images) || g.images < g.need
          || typeof g.model !== "string" || !g.model.trim() || typeof g.quality !== "string" || !g.quality.trim()
          || typeof g.fan_out !== "boolean") || groups.reduce((n, g) => n + g.need, 0) !== run.need) return null;
    } else {
      if (typeof prepare?.model !== "string" || !prepare.model.trim() || !Number.isSafeInteger(prepare.n) || prepare.n < 1) return null;
      groups = [{ ...prepare, images: run.need * prepare.n, fan_out: modelInfo(prepare.model)?.fan_out }];
    }
    const lines = [`Forge 로 생성 ${run.need}건을 요청합니다. 사내 사용량이 잡힙니다.`];
    if (groups.length === 1) {
      const g = groups[0];
      lines.push(`모델 ${modelText(g)}`, `잡 ${run.need}건 · 그림 ${g.images}장`);
      if (g.fan_out) lines.push("이 모델은 장마다 따로 생성합니다");
    } else {
      const shown = groups.length > 6 ? 5 : groups.length;
      for (const g of groups.slice(0, shown)) {
        lines.push(`${modelText(g)} — 잡 ${g.need}건 · 그림 ${g.images}장` + (g.fan_out ? " · 장마다 따로 생성" : ""));
      }
      const rest = groups.slice(shown);
      const sum = (rows, key) => rows.reduce((total, g) => total + g[key], 0);
      if (rest.length) lines.push(`그 밖 ${rest.length}종 — 잡 ${sum(rest, "need")}건 · 그림 ${sum(rest, "images")}장`);
      lines.push(`합계 잡 ${run.need}건 · 그림 ${sum(groups, "images")}장`);
    }
    return lines;
  }

  // 이번 계획에서 서버가 막은 작업(run.reasons 의 blocked 줄 — 비율이 맞지 않는 크롭 등. 선택과 무관하게 실행되는 사전 승인분도
  // 여기에 든다). 승인 건수에는 들지 않으므로 승인 상자가 「몇 건이 왜 빠지는지」를 따로 알린다. 줄이 없거나 읽지 못하면 null.
  function blockedInfo(run) {
    const rows = (Array.isArray(run?.reasons) ? run.reasons : [])
      .filter((row) => row?.reason === "blocked" && Number.isSafeInteger(row.n) && row.n > 0);
    if (!rows.length) return null;
    const why = [...new Set(rows.map((row) => (typeof row.msg === "string" ? row.msg.replace(/^발사 못 함\s*—\s*/, "").trim() : ""))
      .filter(Boolean))].join("; ");
    return { n: rows.reduce((total, row) => total + row.n, 0), why };
  }

  function blockedNote(run) {
    const info = blockedInfo(run);
    return info && `막힌 작업 ${info.n}건은 이번 요청에서 빠집니다` + (info.why ? ` — ${info.why}` : "");
  }

  function postLines(plan) {
    const { tool, model, opts, counts, items } = plan || {};
    if (plan?.ok !== true || typeof plan.expect !== "string" || !plan.expect.trim() || typeof plan.hash !== "string"
        || !["upscale", "bg_removal"].includes(tool) || typeof model?.id !== "string" || !model.id.trim()
        || typeof model.name !== "string" || !model.name.trim()
        || typeof model.external !== "boolean" || !Array.isArray(items) || !counts) return null;
    if (tool === "upscale" ? !(Number.isFinite(opts?.scale) && opts.scale > 0)
        : !(Number.isFinite(opts?.mask_offset) && Number.isFinite(opts?.mask_blur))) return null;
    const totals = { new: 0, reuse: 0, recover: 0, blocked: 0 };
    const keys = new Set();
    for (const item of items) {
      if (!item || typeof item.state !== "string" || !Object.hasOwn(totals, item.state) || typeof item.key !== "string" || !item.key.trim()
          || keys.has(item.key) || typeof item.crop !== "string" || !item.crop.trim()
          || (item.state === "blocked" && (typeof item.why !== "string" || !item.why.trim()))) return null;
      totals[item.state]++;
      keys.add(item.key);
    }
    if (Object.keys(totals).some(state => counts[state] !== totals[state]) || (totals.new && !plan.hash.trim())) return null;
    const label = tool === "upscale" ? `업스케일 — ${model.name} · ×${opts.scale}` : `배경 제거 — ${model.name}`;
    const lines = [`Forge 로 후보정 ${counts.new}건을 요청합니다. 사내 사용량이 잡힙니다.`,
      `${label} · 그림 ${counts.new}장` + (model.external ? " · 외부 유료 공급자" : "")];
    if (counts.reuse) lines.push(`이미 받은 결과 ${counts.reuse}건은 다시 요청하지 않습니다.`);
    if (counts.recover) lines.push(`결과만 다시 받기 ${counts.recover}건(추가 요청 없음)`);
    if (counts.blocked) lines.push(`할 수 없는 그림 ${counts.blocked}장은 건너뜁니다.`);
    return lines;
  }

  function blockedItems(plan) {
    return plan.items.filter(item => item.state === "blocked").map(({ key, crop, why }) => ({ key, crop, why }));
  }

  async function submitPostprocess({ root, tool, keys, model, scale, opts }) {
    if (window.DPEnv.caps?.v2_postprocess !== true) return failed("disabled", "후보정 기능이 아직 켜져 있지 않습니다");
    if (window.DPEnv.backend !== "forge") return failed("no_backend", "후보정은 Forge 백엔드에서만 됩니다");
    if (!["upscale", "bg_removal"].includes(tool)) return failed("plan_error", "지원하지 않는 후보정 도구입니다");
    if (!Array.isArray(keys) || !keys.length) return failed("no_selection", "후보정할 패치를 고르세요");
    keys = [...new Set(keys)];
    if (keys.length > 8) return failed("too_many", "한 번에 8장까지 후보정할 수 있습니다 — 나눠서 눌러 주세요");
    if (keys.some(key => typeof key !== "string" || !key.trim() || key.includes(","))) return failed("plan_error", "후보 키를 읽지 못했습니다");
    if (paying) return failed("busy", "앞선 제출을 처리하는 중입니다");
    paying = true;
    try {
      const query = { root, tool, keys: keys.join(",") };
      if (tool === "upscale") {
        if ((model !== undefined && (typeof model !== "string" || !model.trim())) || (scale !== undefined && !Number.isFinite(scale))) {
          return failed("plan_error", "후보정 옵션을 읽지 못했습니다");
        }
        if (model !== undefined) query.model = model;
        if (scale !== undefined) query.scale = scale;
      } else if (opts !== undefined) {
        if (!opts || typeof opts !== "object" || Array.isArray(opts)
            || [opts.mask_offset, opts.mask_blur].some(value => value !== undefined && !Number.isFinite(value))) {
          return failed("plan_error", "후보정 옵션을 읽지 못했습니다");
        }
        if (opts.mask_offset !== undefined) query.offset = opts.mask_offset;
        if (opts.mask_blur !== undefined) query.blur = opts.mask_blur;
      }
      async function readPlan() {
        const response = await request("postprocess/plan", query);
        const plan = await response.json();
        if (!response.ok || plan?.ok !== true) return failed(plan?.reason || "plan_error", plan?.error || `계획을 읽지 못했습니다(HTTP ${response.status})`);
        const lines = postLines(plan);
        if (!lines || plan.tool !== tool || plan.items.some(item => !keys.includes(item.key))
            || (plan.items.length && plan.items.length !== keys.length)) {
          return failed("plan_error", "후보정 계획을 읽지 못했습니다");
        }
        return { ok: true, plan, lines };
      }
      const current = await readPlan();
      if (!current.ok) return current;
      const approved = current.plan;
      const { counts, hash, expect } = approved;
      if (!counts.new && !counts.reuse && !counts.recover) {
        const blocked = blockedItems(approved);
        return blocked.length ? { ...failed("blocked", blocked[0].why), blocked } : failed("nothing_to_do", "후보정할 것이 없습니다");
      }
      if (counts.new && !await ask(current.lines, [], "후보정 승인")) return failed("cancelled", "승인을 취소했습니다");
      const latest = await readPlan();
      if (!latest.ok) return latest;
      if (latest.plan.hash !== hash || latest.plan.expect !== expect || latest.plan.counts.new !== counts.new
          || window.DPEnv.backend !== "forge" || window.DPEnv.caps?.v2_postprocess !== true) {
        return failed("stale", "계획이 바뀌었습니다 — 다시 눌러 주세요");
      }
      const options = tool === "upscale" ? { model: approved.model.id, scale: approved.opts.scale }
        : { opts: { mask_offset: approved.opts.mask_offset, mask_blur: approved.opts.mask_blur } };
      const job = await postJob("postprocess/run", { root, tool, keys, ...options, expect, approve: counts.new ? hash : "" });
      if (!job.ok) return job;
      const totals = job.summary || latest.plan.counts;
      return { ...job, summary: { new: totals.new, reuse: totals.reuse, recover: totals.recover, blocked: blockedItems(latest.plan) } };
    } catch (e) { return failed("plan_error", e?.message || String(e)); }
    finally { paying = false; }
  }

  async function submitPaid(stage, options = {}) {
    if (!server()) return serverOnly();
    if (stage === "postprocess") return submitPostprocess(options || {});
    if (stage !== "run") return failed("unsupported", "지원하지 않는 유료 작업입니다");
    const backend = window.DPEnv.backend;
    if (!["forge", "mock"].includes(backend)) return failed("no_backend", "서버 모드 생성 백엔드가 아직 연결되지 않았습니다");
    const { root, only } = options || {};
    const ids = new Set(typeof only === "string" ? only.split(",").map((id) => id.trim()).filter(Boolean) : []);
    if (!ids.size) return failed("no_selection", "승인할 크롭을 선택하세요");
    if (paying) return failed("busy", "앞선 제출을 처리하는 중입니다");
    paying = true;
    let summary;
    try {
      const state = (await read("state", { root })).data;
      const nameError = unnamed(state, ids);
      if (nameError) return failed("unnamed", nameError);
      // 아직 반영 전인 새 크롭이 선택에 있으면 계획 라우트는 그 id 를 모른다 — 먼저 반영한 뒤에 그 크롭까지 넣어 계획을 받는다.
      const known = new Set((state.crops || []).map((c) => c.id));
      const added = new Set((state.layout_added || []).map((c) => c.id));
      // 어디에도 없는 크롭(다른 창에서 지움 등)이 선택에 있으면 아무것도 내지 않는다 — 화면이 낡았다.
      if ([...ids].some((id) => !known.has(id) && !added.has(id))) return failed("stale", "선택한 크롭이 지금 프로젝트에 없습니다 — 화면을 새로 고쳐 주세요");
      const fresh = [...ids].some((id) => !known.has(id));
      let pipe = (await read("pipeline", { root })).data;
      // 요청에 실어 보내는 표지: 서버는 실행 설정·매니페스트가 이 판과 다르면 받지 않는다(다른 창이 그 사이 설정을 바꾼 경우).
      if (!sigOf(pipe)) return unread();  // 판 없이(깨진 설정 파일 등) 반영·생성을 내면 서버가 그 사이의 설정 변경을 가려낼 수 없다
      // 실행 설정이 마지막 반영과 다름(모델·장 수 같은 Prepare 설정, 가져오기 설정) — 계획의 apply_pending 에는 없지만 서버는 생성을 막는다.
      const drifted = (pipe?.drift || []).some((d) => d.section === "prepare" || d.kind === "import");
      let current = await plan(root, fresh ? [...ids].filter((id) => known.has(id)).join(",") : only);
      let bad = trouble(current);
      if (bad) return bad;
      if (current.apply_pending?.length || fresh || drifted) {
        // 그림·지시 편집뿐이면 묻지 않고 반영한다. 실행 설정 쪽 사유(계획의 notes)가 있으면 v1 의 반영 확인처럼 먼저 묻는다 —
        // 설정이 바뀐 반영은 크롭·작업을 바꿔 다시 과금 대상을 만들 수 있다.
        const applied = await applyPlan(root, pipe, current);
        if (!applied.ok) return applied;
        summary = applied.summary;
        pipe = (await read("pipeline", { root })).data;  // 반영으로 매니페스트가 바뀌었다 — 승인 상자의 모델·장 수와 제출 표지는 이 판으로
        if (!sigOf(pipe)) return { ...unread(), summary };
        current = await plan(root, only);
        bad = trouble(current);
        if (bad) return { ...bad, summary };
        if (current.apply_pending?.length) return { ...failed("apply_needed", "반영이 필요합니다"), pending: current.apply_pending, summary };
      }
      // 반영으로 풀리지 않는 설정 쪽 사유(확인하지 않은 실행 설정 등)가 남아 있으면 승인받지 않는다(v1 의 승인 보류와 같은 조건).
      if (notes(current).length) return { ...failed("settings_pending", notes(current).join(" · ")), summary };
      const run = current.run;
      const grouped = Array.isArray(run.groups);
      let lines = grouped ? approvalLines(run) : null;
      if (grouped && !lines) {
        return { ...failed("plan_error", "계획의 모델별 건수를 읽지 못했습니다"), summary };
      }
      const hash = run.hash, skipped = blockedNote(run);
      // 승인할 것이 없는데 막힌 작업이 있으면 그 까닭을 돌려준다(화면이 오류 글을 그대로 알린다).
      if (typeof hash !== "string" || !hash) {
        const info = blockedInfo(run);
        return { ...(info ? failed("blocked", `승인할 것이 없습니다 — 막힌 작업 ${info.n}건` + (info.why ? `: ${info.why}` : ""))
          : failed("nothing_to_approve", "승인할 것이 없습니다")), summary };
      }
      if (run.backend && run.backend !== backend) return failed("no_backend", "생성 백엔드 설정이 바뀌었습니다 — 계획을 다시 확인해 주세요");
      if (backend === "mock") {
        lines = [`[MOCK] 과금 없음 — 생성 ${run.need}건`, "mock 백엔드 — 승인 코드가 mock 에 묶여 있어 유료 실행을 승인하지 못합니다."];
      } else if (!grouped) {
        lines = approvalLines(run, run.prepare || current.prepare || pipe?.settings?.prepare);
        if (!lines) return { ...failed("plan_error", "모델 설정을 읽지 못했습니다"), summary };
      }
      if (summary) lines.push(`자동 반영: 새 조건 ${summary.new_jobs}개 · 옛 조건이 되는 후보 ${summary.old_cands}장 · 작업 없음 ${summary.no_job.length}개`);
      const warnings = backend === "forge" && run.pre ? [`다시 생성(사전 승인) ${run.pre}건도 함께 실행(선택과 무관) — 사내 사용량이 잡힙니다.`] : [];
      if (skipped) warnings.push(skipped);
      if (!await ask(lines, warnings)) return { ...failed("cancelled", "승인을 취소했습니다"), summary };
      const latest = await plan(root, only);
      bad = trouble(latest);
      if (bad) return { ...bad, summary };  // 계획을 다시 받지 못함(바쁨·실행 중·오류) — '계획이 바뀜'과 구분해 알린다
      // 상자가 알린 「함께 실행되는 사전 승인 건수」와 「막혀서 빠지는 작업」도 그대로여야 한다 — 그 사이 막힘이 풀린 사전 승인분은
      // 승인 해시에 들지 않은 채 실행되므로, 달라졌으면 다시 보이고 승인받는다.
      if (latest.run.hash !== hash || latest.apply_pending?.length || notes(latest).length || window.DPEnv.backend !== backend
          || (latest.run.backend && latest.run.backend !== backend)
          || (latest.run.pre || 0) !== (run.pre || 0) || blockedNote(latest.run) !== skipped) {
        return { ...failed("stale", "계획이 바뀌었습니다 — 다시 눌러 주세요"), summary };
      }
      // 승인 상자가 보인 설정(pipe)과 같은 판일 때만 서버가 받는다 — 그 사이 모델·장 수가 바뀌었으면 409(stale_settings).
      return { ...await post({ root, stage, approve: hash, only, expect: sigOf(pipe) }), summary };
    } catch (e) { return { ...failed("plan_error", e?.message || String(e)), summary }; }
    finally { paying = false; }
  }

  return { post, ask, asking: () => !!active, modelText, approvalLines, blockedNote, postLines, submitFree, wait, plan, submitPaid, applyChecked };
})();
