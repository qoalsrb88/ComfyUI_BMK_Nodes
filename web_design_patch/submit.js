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

  async function post(body) {
    if (!server()) return serverOnly();
    try {
      const response = await request("jobs/prompt", {}, { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body) });
      const result = await response.json().catch(() => null);
      if (response.ok && typeof result?.prompt_id === "string" && result.prompt_id) {
        return { ok: true, prompt_id: result.prompt_id };
      }
      if (response.status >= 400 && response.status < 500 && typeof result?.reason === "string" && result.reason) {
        return { ok: false, reason: result.reason, error: result.error || `HTTP ${response.status}`, hash: result.hash, pending: result.pending };
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
    return (m?.label || p.model) + (m && !m.qualities.length ? "" : ` · 품질 ${p.quality}`)
      + (/^gpt-image-2\.6-/.test(p.model) ? ` · 복잡도 ${p.complexity || "high"}` : "")
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

  async function submitPaid(stage, options = {}) {
    if (!server()) return serverOnly();
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
      const sigOf = (p) => (Number.isInteger(p?.rev) && Number.isInteger(p?.manifest_rev) ? `${p.rev}:${p.manifest_rev}` : undefined);
      const stamped = (body, p) => ({ ...body, expect: sigOf(p) });
      const unread = () => failed("plan_error", "실행 설정을 읽지 못했습니다 — ① 준비에서 실행 설정을 확인한 뒤 다시 눌러 주세요");
      if (!sigOf(pipe)) return unread();  // 판 없이(깨진 설정 파일 등) 반영·생성을 내면 서버가 그 사이의 설정 변경을 가려낼 수 없다
      // 실행 설정이 마지막 반영과 다름(모델·장 수 같은 Prepare 설정, 가져오기 설정) — 계획의 apply_pending 에는 없지만 서버는 생성을 막는다.
      const drifted = (pipe?.drift || []).some((d) => d.section === "prepare" || d.kind === "import");
      const notes = (p) => (Array.isArray(p.notes) ? p.notes : []);
      const trouble = (p) => (!p ? failed("plan_error", "계획을 읽지 못했습니다")
        : p.busy ? failed("busy", "서버가 계획을 계산하는 중입니다 — 잠시 뒤 다시 눌러 주세요")
        : !p.run || p.run.error ? failed("plan_error", p.run?.error || "계획을 읽지 못했습니다")
        : p.run.running ? failed("running", "생성이 실행 중입니다 — 끝난 뒤 다시 눌러 주세요") : null);
      let current = await plan(root, fresh ? [...ids].filter((id) => known.has(id)).join(",") : only);
      let bad = trouble(current);
      if (bad) return bad;
      if (current.apply_pending?.length || fresh || drifted) {
        // 그림·지시 편집뿐이면 묻지 않고 반영한다. 실행 설정 쪽 사유(계획의 notes)가 있으면 v1 의 반영 확인처럼 먼저 묻는다 —
        // 설정이 바뀐 반영은 크롭·작업을 바꿔 다시 과금 대상을 만들 수 있다.
        if (notes(current).length && !await ask(["무료 반영을 먼저 실행합니다(과금 없음). 실행 설정이 마지막 반영과 다릅니다.",
          ...notes(current), "계속할까요?"], [], "반영 확인")) {
          return failed("cancelled", "반영을 취소했습니다");
        }
        const job = await post(stamped({ root, stage: "apply" }, pipe));
        if (!job.ok) return failed("apply_failed", `${job.reason} · ${job.error}`);
        const result = await wait(root, job.prompt_id);
        if (!result.done || !result.ok) {
          // 끝을 보지 못한 반영(앞선 실행 뒤에 줄 서 있음 등)은 대기에서 뺀다 — 사용자가 모르는 사이 나중에 돌지 않게. 이미 도는 중이면 서버가 그대로 둔다.
          if (!result.done) {
            await request("jobs/queue", {}, { method: "POST", headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ root, delete: [job.prompt_id] }) }).catch(() => null);
          }
          const detail = result.record?.status?.messages?.find(([kind]) => kind === "execution_error")?.[1]?.exception_message;
          return { ...failed("apply_failed", detail || `무료 반영을 완료하지 못했습니다 (${result.reason || "작업 실패"})`), prompt_id: job.prompt_id };
        }
        summary = result.record.apply_summary;
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
      const hash = run.hash;
      if (typeof hash !== "string" || !hash) return { ...failed("nothing_to_approve", "승인할 것이 없습니다"), summary };
      if (run.backend && run.backend !== backend) return failed("no_backend", "생성 백엔드 설정이 바뀌었습니다 — 계획을 다시 확인해 주세요");
      let lines;
      if (backend === "mock") {
        lines = [`[MOCK] 과금 없음 — 생성 ${run.need}건`, "mock 백엔드 — 승인 코드가 mock 에 묶여 있어 유료 실행을 승인하지 못합니다."];
      } else {
        // 계획에 준비 설정이 없으면 처음에 읽은 실행 설정(GET pipeline)의 것을 쓴다(v1 이 진행 패널에 보관하는 값과 같다).
        const pr = run.prepare || current.prepare || pipe?.settings?.prepare;
        if (!pr) return { ...failed("plan_error", "모델 설정을 읽지 못했습니다"), summary };
        lines = [`Forge 로 생성 ${run.need}건을 요청합니다. 사내 사용량이 잡힙니다.`,
          `모델 ${modelText(pr)}`, `잡 ${run.need}건 · 그림 ${run.need * pr.n}장`];
        if (modelInfo(pr.model)?.fan_out) lines.push("이 모델은 장마다 따로 생성합니다");
      }
      if (summary) lines.push(`자동 반영: 새 조건 ${summary.new_jobs}개 · 옛 조건이 되는 후보 ${summary.old_cands}장 · 작업 없음 ${summary.no_job.length}개`);
      const warnings = backend === "forge" && run.pre ? [`다시 생성(사전 승인) ${run.pre}건도 함께 실행(선택과 무관) — 사내 사용량이 잡힙니다.`] : [];
      if (!await ask(lines, warnings)) return { ...failed("cancelled", "승인을 취소했습니다"), summary };
      const latest = await plan(root, only);
      bad = trouble(latest);
      if (bad) return { ...bad, summary };  // 계획을 다시 받지 못함(바쁨·실행 중·오류) — '계획이 바뀜'과 구분해 알린다
      if (latest.run.hash !== hash || latest.apply_pending?.length || notes(latest).length || window.DPEnv.backend !== backend
          || (latest.run.backend && latest.run.backend !== backend)) {
        return { ...failed("stale", "계획이 바뀌었습니다 — 다시 눌러 주세요"), summary };
      }
      // 승인 상자가 보인 설정(pipe)과 같은 판일 때만 서버가 받는다 — 그 사이 모델·장 수가 바뀌었으면 409(stale_settings).
      return { ...await post(stamped({ root, stage, approve: hash, only }, pipe)), summary };
    } catch (e) { return { ...failed("plan_error", e?.message || String(e)), summary }; }
    finally { paying = false; }
  }

  return { post, ask, asking: () => !!active, modelText, submitFree, wait, plan, submitPaid };
})();
