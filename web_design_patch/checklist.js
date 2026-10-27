// 실행 패널의 읽기 전용 작업 점검표. 이동 외에는 큐·저장·승인을 하지 않는다.
"use strict";

window.DPChecklist = (() => {
  const folded = new WeakMap(); // 패널별 접힘은 이 창에서만 기억한다.

  function rows(input = {}) {
    const S = input.S || {};
    const crops = S.crops || [];
    const targets = S.targets || [];
    const pending = input.pending || [];
    const prepareDrift = input.prepareDrift || [];
    const plan = input.plan;
    const draft = plan && plan.draft;
    const run = plan && plan.run;
    const planError = input.planErr ? `계획 오류 — 다시 계산 · ${String(input.planErr).slice(0, 120)}` : "";
    const planOk = !!plan && !planError && input.planOk !== false;
    const ready = planOk && !input.stale;
    const n = crops.length;
    const selected = input.selectedCount ?? n;
    const allSelected = selected === n;
    const selectionNote = allSelected ? "" : ` (선택 ${selected}/${n} 크롭 기준)`;
    const hasRefs = (c) => c.refs && (c.refs.n > 0 || (c.refs.fixed && c.refs.none && !c.refs.auto));
    const linked = crops.filter(hasRefs).length;
    const missingRef = crops.find((c) => !hasRefs(c));
    const noRefsDir = crops.some((c) => c.refs === null);
    const draftNeed = draft && Array.isArray(draft.units) ? draft.units.length : null;
    const draftFree = draft && Number.isFinite(draft.free) ? draft.free : 0;
    const draftUnwritten = draft && draft.warnings && Array.isArray(draft.warnings.unwritten) ? draft.warnings.unwritten.length : 0;
    const draftSettings = input.P0 && !input.P0.error && input.P0.settings && input.P0.settings.draft;
    const draftStyle = draftSettings && draftSettings.apply === "full" && draft && Number.isFinite(draft.pending_style) ? draft.pending_style : 0;
    const runNeed = run && Number.isFinite(run.need) ? run.need : null;
    const runPre = run && Number.isFinite(run.pre) ? run.pre : 0;
    const runFree = run && Number.isFinite(run.free) ? run.free : 0;
    const runCrops = (run && run.by_crop) || {};
    const progress = input.progress && input.progress.run;
    const inflight = progress && Number.isFinite(progress.inflight) ? progress.inflight
      : crops.reduce((v, c) => v + ((runCrops[c.id] || {}).inflight || 0), 0);
    const running = !!S.running || !!(run && run.running) || inflight > 0;
    const drafting = !!S.drafting || !!(draft && draft.running);
    const complete = n > 0 && crops.every((c) => (runCrops[c.id] || {}).done > 0);
    const draftError = planError || (planOk && draft && draft.error);
    const runError = planError || (planOk && run && run.error);
    const draftValue = draftError ? draftError : !planOk || !draft ? "계산 중"
      : draftUnwritten > 0 ? `못 쓴 스펙 ${draftUnwritten}` : drafting ? "초안 진행 중"
        : draftFree > 0 ? `초안 복구 대기 ${draftFree}` : draftStyle > 0 ? `화풍 초안 대기 ${draftStyle}`
          : draftNeed === null ? "계산 중" : `초안 승인 필요 ${draftNeed}${selectionNote}`;
    const runValue = runError ? runError : !planOk || !run ? "계산 중"
      : running ? `진행 ${inflight}` : runPre > 0 ? `다시 생성 대기 ${runPre}`
        : runFree > 0 ? `복구 대기 ${runFree}` : runNeed === null ? "계산 중" : `생성 승인 필요 ${runNeed}${selectionNote}`;
    // starChecks 의 공동 결과 판정과 같은 조건: 뒤 타깃은 첫 타깃의 공동 ★ 를 공유한다.
    const hasStar = (t) => {
      if ((t.pick_keys || []).length) return true;
      const live = (t.candidates || []).filter((c) => !c.rejected);
      if (!live.length || !live.every((c) => c.joint && c.joint.first && c.joint.first !== t.tid)) return false;
      const first = targets.find((x) => x.crop_id === t.crop_id && x.tid === live[0].joint.first);
      return !!first && live.some((c) => (first.pick_keys || []).includes(c.joint.first_key));
    };
    const missingStar = targets.find((t) => !hasStar(t));
    const stars = targets.filter(hasStar).length;
    const edits = crops.reduce((v, c) => v + (c.mask_edits || 0), 0);
    const maskTarget = targets.find((t) => (t.pick_keys || []).length);
    const maskKey = input.maskLeft ? input.maskLeft.key : maskTarget && maskTarget.pick_keys[0];
    const last = "export_last" in S ? S.export_last : (input.P0 && input.P0.exports || [])[0];
    const exported = !!last || S.export_state === "same" || S.export_state === "changed" || S.export_state === "unknown";
    const exValue = last ? `r${last.rev} · ${last.time || "시각 기록 없음"}` : "Export 기록 있음";
    const row = (n, name, state, value, hint, go) => ({ n, name, state, value, hint: `처음이면: ${hint}`, go });
    return [
      row(1, "PSD 가져오기", S.canvas ? "done" : "todo", S.canvas ? "가져옴" : "Import 필요",
        "ComfyUI 그래프에서 Import PSD 를 연결하고 큐를 한 번 실행하세요.", { section: "실행 설정" }),
      row(2, "크롭", n ? "done" : "todo", `크롭 ${n}`, "크롭 편집(E)에서 고칠 영역을 확인하세요.", { mode: "edit" }),
      row(3, "레퍼런스", n && linked === n ? "done" : "todo", `크롭 ${linked}/${n}${noRefsDir ? " · refs 폴더 없음" : ""}`,
        "레퍼런스(T)에서 각 크롭에 참고 이미지를 연결하세요.", { mode: "refs", cropId: missingRef ? missingRef.id : null }),
      row(4, "반영", !pending.length && !prepareDrift.length && !input.unsure ? "done" : "todo",
        pending.concat(prepareDrift.length ? [`Prepare 설정 갈라짐 ${prepareDrift.length}`] : [],
          input.unsure ? [`확인 필요: ${input.unsure}`] : []).join(" · ") || "미반영 없음",
        "크롭·레퍼런스를 바꾼 뒤 반영(무료)을 눌러 생성 입력을 갱신하세요.", { section: "반영" }),
      row(5, "초안", draftError || (planOk && draftUnwritten > 0) ? "warn"
        : n > 0 && allSelected && ready && draftNeed === 0 && draftFree === 0 && draftStyle === 0 && !drafting ? "done" : "todo",
        draftValue,
        "비용·승인에서 초안 계획과 비용을 확인한 뒤 승인하세요.", { section: "비용·승인" }),
      row(6, "생성", runError ? "warn"
        : allSelected && ready && runNeed === 0 && runPre === 0 && runFree === 0 && complete && !running && !drafting && !prepareDrift.length ? "done" : "todo",
        runValue,
        "비용·승인에서 생성 계획을 확인하세요. 모든 크롭에 완료 결과가 있어야 끝납니다.", { section: "비용·승인" }),
      row(7, "★", targets.length && !missingStar ? "done" : "todo", `타깃 ${stars}/${targets.length}`,
        "검토에서 쓸 후보를 ★ 하세요. 공동 결과의 뒤 타깃은 첫 타깃의 ★ 를 공유합니다.",
        missingStar ? { target: missingStar.key } : { section: "★ 점검" }),
      row(8, "마스크 다듬기", input.maskLeft ? "warn" : "optional",
        `편집 ${edits}${input.maskLeft ? ` · 적용 안 한 편집 ${input.maskLeft.n}획` : " · 선택"}`,
        "필요할 때만 마스크(K)에서 경계를 다듬고 적용하세요. 끝난 수에는 넣지 않습니다.",
        maskKey ? { mask: { key: maskKey }, target: maskTarget ? maskTarget.key : null } : { mode: "mask" }),
      row(9, "Export", !exported ? "todo" : S.export_state === "same" ? "done" : "warn",
        !exported ? "Export 없음" : S.export_state === "same" ? exValue
          : S.export_state === "changed" ? "다시 Export 필요" : "변경 여부 확인 불가 · 다시 Export 필요",
        "★ 와 마스크를 확인한 뒤 Export 하세요. 옛 기록은 변경 여부를 확인할 수 없습니다.", { section: "Export" }),
    ];
  }

  function render(container, items, opts = {}) {
    const key = opts.memory || container;
    const make = (tag, cls, text) => {
      const e = document.createElement(tag);
      e.className = cls;
      if (text !== undefined) e.textContent = text;
      return e;
    };
    const details = make("details", "pp-sec dp-checklist");
    details.open = !folded.get(key);
    details.addEventListener("toggle", () => {
      if (container.contains(details)) folded.set(key, !details.open);
    });
    const required = items.filter((r) => r.n !== 8);
    const done = required.filter((r) => r.state === "done").length;
    const title = make("summary", "lp-k", `작업 점검표 (${done}/${required.length})`);
    title.title = "마스크 다듬기는 선택 — 끝난 수에는 포함하지 않습니다.";
    const symbols = { done: "✓", todo: "○", warn: "!", optional: "선택" };
    const labels = { done: "끝", todo: "할 일", warn: "경고", optional: "선택" };
    const buttons = items.map((r) => {
      const b = make("button", `dp-check-row ${r.state}`);
      b.type = "button";
      b.title = r.hint;
      b.setAttribute("aria-label", `${labels[r.state]} · ${r.name} · ${r.value}`);
      b.replaceChildren(make("span", "dp-check-state", symbols[r.state]), make("span", "dp-check-name", r.name),
        make("span", "dp-check-value", r.value));
      b.addEventListener("click", () => {
        const go = r.go || {};
        if (go.mode && opts.goMode) opts.goMode(go.mode, go.cropId || null);
        else if (go.mask && opts.goMask) opts.goMask(go.mask, go.target || null);
        else if (go.target && opts.goTarget) opts.goTarget(go.target);
        else if (go.section && opts.goSection) opts.goSection(go.section);
      });
      return b;
    });
    details.replaceChildren(title, ...buttons);
    container.replaceChildren(details);
  }

  return { rows, render };
})();
