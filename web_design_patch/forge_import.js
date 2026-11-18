// Forge F2-C: 목록·그림·저장은 앱 서버만 호출한다.
"use strict";

window.DPForgeImport = (() => {
  const API = "/bmk/design_patch/forge/";
  const can = (name) => !!(window.DPEnv && window.DPEnv.serverMode && window.DPEnv.can(name));
  const el = (tag, cls, text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text) node.textContent = text;
    return node;
  };
  const button = (label, cls = "") => {
    const node = el("button", cls, label);
    node.type = "button";
    return node;
  };
  const dateText = (value) => {
    const date = value ? new Date(value) : null;
    return date && !Number.isNaN(date.getTime()) ? date.toLocaleDateString("ko-KR") : "";
  };
  function sourceLine(data) {
    if (!window.DPEnv || !window.DPEnv.serverMode || !data || !["history", "asset"].includes(data.source)) return null;
    const line = el("div", "dp-forge-source");
    const label = [data.source === "history" ? "Forge 결과" : "Forge 자료",
      data.source === "history" ? data.model : data.folder || data.name,
      dateText(data.created || data.imported_at)].filter(Boolean).join(" · ");
    if (data.prompt) {
      const toggle = button(label, "dp-forge-source-toggle"), text = el("p", "dp-forge-source-text", data.prompt);
      toggle.setAttribute("aria-label", label + " · 프롬프트 펼치기");
      toggle.setAttribute("aria-expanded", "false"); text.hidden = true;
      toggle.addEventListener("click", () => {
        text.hidden = !text.hidden;
        toggle.setAttribute("aria-expanded", String(!text.hidden));
        toggle.setAttribute("aria-label", label + (text.hidden ? " · 프롬프트 펼치기" : " · 프롬프트 접기"));
      });
      line.append(toggle, text);
    } else line.append(el("span", "", label));
    line.addEventListener("click", (event) => event.stopPropagation());
    return line;
  }
  async function call(path, body, signal) {
    const response = await fetch(API + path, body ? {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    } : { signal });
    let data;
    try { data = await response.json(); } catch (_) { throw new Error("응답을 읽지 못했습니다 — 다시 시도해 주세요."); }
    if (!response.ok) {
      const error = new Error(data.error || "요청을 처리하지 못했습니다.");
      error.reason = data.reason;
      throw error;
    }
    return data;
  }
  let active = null;
  function open({ root = "", into, name, onDone }) {
    if (!can("forge_import") || active || !["target", "refs"].includes(into) || !window.DPDialog) return false;
    const overlay = el("div", "overlay dp-forge"), sheet = el("div", "sheet dp-forge-sheet");
    const heading = el("div", "dp-forge-heading"), tabs = el("div", "lp-btns");
    const filters = el("div", "dp-forge-filters"), search = el("input", "dp-forge-search");
    const status = el("p", "dp-forge-status"), grid = el("div", "dp-forge-grid");
    const statusRow = el("div", "dp-forge-status-row"), footer = el("div", "dp-forge-footer");
    const more = button("계속 불러오기"), closeButton = button("닫기"), retry = button("다시 시도");
    const importButton = button("0장 가져오기", "dp-forge-import"), refresh = button("새로고침");
    const columns = el("input", "dp-forge-columns"), sizeLabel = el("label", "dp-forge-size", "썸네일 크기 조절");
    const seen = new Set(), models = new Set(), cards = new Map(), selected = new Map(), completed = new Map();
    let source = "history", next = null, sequence = 0, loading = false, sending = false, timer, listRequest;
    let searchTerm = "", since = "", retryAction = null, viewer = null, previewFocus = null, completion = null;
    let previewScroll = 0;
    const alive = () => active === overlay;
    function select(label, choices) {
      const node = el("select");
      node.setAttribute("aria-label", label);
      for (const [value, text] of choices) {
        const option = el("option", "", text); option.value = value; node.append(option);
      }
      node.value = choices[0][0];
      return node;
    }
    const model = select("모델", [["", "전체 모델"]]);
    const period = select("기간", [["", "전체 기간"], ["today", "오늘"], ["week", "이번 주"], ["month", "이번 달"]]);
    const scope = select("범위", [["mine", "내 결과"], ["team", "조직 전체"]]);
    const folder = select("폴더", [["", "전체 폴더"]]);
    const favorite = button("♥ 좋아요 한 항목"), studio = button("스튜디오 결과 보기");
    for (const node of [favorite, studio]) {
      node.setAttribute("aria-label", node.textContent); node.setAttribute("aria-pressed", "false");
      node.addEventListener("click", () => {
        if (sending) return;
        node.setAttribute("aria-pressed", String(node.getAttribute("aria-pressed") !== "true")); reload(300);
      });
    }
    function updateChoices(node, values, label) {
      const value = node.value;
      node.replaceChildren();
      for (const name of ["", ...new Set([...values, value].filter(Boolean))].sort((a, b) => a.localeCompare(b, "ko-KR"))) {
        const option = el("option", "", name || label); option.value = name; node.append(option);
      }
      node.value = value;
    }
    function controls() { return [...sheet.querySelectorAll("button, input, select")]; }
    function syncControls() {
      for (const node of controls()) node.disabled = sending;
      for (const [id, card] of cards) {
        const picked = selected.has(id);
        card.node.className = "dp-forge-item" + (picked ? " dp-forge-selected" : "");
        card.pick.disabled = sending || !!card.item.blocked;
        card.zoom.disabled = sending || !!card.item.blocked || !card.thumb;
        if (into === "refs") card.pick.setAttribute("aria-pressed", String(picked));
        card.mark.hidden = !picked;
      }
      more.disabled = sending || loading || !next;
      refresh.disabled = sending || loading;
      importButton.disabled = sending || !selected.size;
      importButton.textContent = selected.size + "장 가져오기";
      retry.disabled = sending || loading;
    }
    function clearRetry() { retryAction = null; retry.hidden = true; }
    function offerRetry(action) { retryAction = action; retry.hidden = false; }
    function cancelList() {
      if (listRequest) listRequest.abort();
      listRequest = null;
    }
    function close() {
      if (!alive() || sending) return;
      clearTimeout(timer); sequence += 1; cancelList();
      overlay.remove(); active = null;
      window.removeEventListener("keydown", inputKeys, true);
      window.DPDialog.unlock();
      if (onDone && completion) return onDone(completion);
    }
    // DPDialog 보다 먼저 컨트롤의 기본 편집·선택 동작을 살리고 보드 단축키는 막는다.
    function inputKeys(e) {
      if (alive() && controls().includes(e.target) && !["Escape", "Tab"].includes(e.key)) {
        if (sending || (e.repeat && e.target.tagName === "BUTTON" && ["Enter", " "].includes(e.key))) e.preventDefault();
        e.stopImmediatePropagation();
      }
    }
    function keys(e) {
      if (e.type !== "keydown") return;
      if (e.key === "Escape") { e.preventDefault(); if (viewer) closePreview(); else close(); }
      if (e.key === "Tab") {
        e.preventDefault();
        const nodes = [...(viewer || sheet).querySelectorAll("button, input, select")].filter((n) => !n.disabled && !n.hidden);
        const index = nodes.indexOf(document.activeElement);
        if (nodes.length) nodes[(index + (e.shiftKey ? nodes.length - 1 : 1)) % nodes.length].focus();
      }
    }
    window.addEventListener("keydown", inputKeys, true);
    if (!window.DPDialog.lock({ keys })) {
      window.removeEventListener("keydown", inputKeys, true);
      return false;
    }
    active = overlay;
    sheet.setAttribute("role", "dialog"); sheet.setAttribute("aria-modal", "true");
    sheet.setAttribute("aria-label", "Forge에서 가져오기");
    status.setAttribute("role", "status");
    search.type = "search"; search.maxLength = 300; search.setAttribute("role", "searchbox");
    columns.type = "range"; columns.min = "3"; columns.max = "8"; columns.step = "1"; columns.value = "4";
    columns.setAttribute("aria-label", "썸네일 크기 조절");
    const sizeKey = "bmk-dp-forge-columns";
    try {
      const value = Number(localStorage.getItem(sizeKey));
      if (Number.isInteger(value) && value >= 3 && value <= 8) columns.value = String(value);
    } catch (_) { /* 저장소를 막은 iframe 에서도 기본 크기로 연다. */ }
    function resize() { sheet.style.setProperty("--dp-forge-columns", columns.value); }
    columns.addEventListener("input", () => {
      if (sending) return;
      resize();
      try { localStorage.setItem(sizeKey, columns.value); } catch (_) { /* 이번 창에는 적용한다. */ }
    });
    resize();
    function closePreview() {
      if (!viewer || sending) return;
      viewer.remove(); viewer = null;
      sheet.style.overflowY = "";
      for (const node of sheet.children) node.inert = false;
      if (previewFocus && previewFocus.isConnected) previewFocus.focus({ preventScroll: true });
      sheet.scrollTop = previewScroll;
    }
    function preview(card, label) {
      if (sending || viewer || !card.thumb || card.item.blocked) return;
      previewFocus = card.zoom;
      previewScroll = sheet.scrollTop; sheet.scrollTop = 0; sheet.style.overflowY = "hidden";
      viewer = el("div", "dp-forge-viewer");
      viewer.setAttribute("role", "dialog"); viewer.setAttribute("aria-modal", "true");
      viewer.setAttribute("aria-label", label + " 크게 보기");
      const image = el("img"), note = el("p", "dp-forge-status"), again = button("다시 시도");
      const back = button("크게 보기 닫기");
      image.alt = label; note.setAttribute("role", "status"); again.hidden = true;
      const read = () => { again.hidden = true; note.textContent = ""; image.src = card.thumb + "&size=large"; };
      image.addEventListener("error", () => { note.textContent = "그림을 읽지 못했습니다."; again.hidden = false; });
      again.addEventListener("click", read); back.addEventListener("click", closePreview);
      for (const node of sheet.children) node.inert = true;
      viewer.append(el("p", "dp-forge-viewer-title", label), image, note, again, back); sheet.append(viewer);
      read(); back.focus();
    }
    async function importItems(items) {
      if (!alive() || sending || !items.length || viewer) return;
      sending = true; clearTimeout(timer); sequence += 1; cancelList(); loading = false;
      clearRetry(); syncControls(); status.textContent = "그림을 가져오는 중…";
      let data;
      try {
        const body = into === "target" ? { into: "target", source, id: items[0].id, name }
          : { into: "refs", source, ids: items.map((item) => item.id), root };
        data = await call("import", body);
      } catch (error) {
        if (alive()) {
          sending = false;
          status.textContent = error.message;
          offerRetry(() => into === "refs" ? importItems([...selected.values()]) : importItems(items));
          syncControls();
        }
        return;
      }
      sending = false;
      if (into === "target") { completion = data; return close(); }
      const done = data.done || [], failed = data.failed || [];
      for (const result of done) completed.set(result.id, result);
      if (completed.size) completion = { ok: true, done: [...completed.values()], failed };
      if (!failed.length) return close();
      selected.clear();
      for (const card of cards.values()) card.error.textContent = "";
      for (const result of failed) {
        const item = items.find((it) => it.id === result.id);
        if (item) selected.set(item.id, item);
        const card = cards.get(result.id);
        if (card) card.error.textContent = result.error;
      }
      status.textContent = done.length + "장 가져옴 · " + failed.length + "장 실패";
      offerRetry(() => importItems([...selected.values()])); syncControls();
    }
    function choose(item) {
      if (sending || item.blocked || viewer) return;
      if (into === "target") return importItems([item]);
      if (selected.has(item.id)) selected.delete(item.id);
      else if (selected.size === 8) { status.textContent = "한 번에 8장까지 고를 수 있습니다"; return; }
      else selected.set(item.id, item);
      syncControls();
    }
    function addCard(item) {
      const card = el("div", "dp-forge-item"), pick = button("", "dp-forge-pick"), image = el("img");
      const picture = el("div", "dp-forge-preview"), badges = el("div", "dp-forge-badges");
      const label = (source === "history" ? item.prompt : item.name) || "이름 없는 이미지";
      const zoom = button("⌕", "dp-forge-zoom"), mark = el("span", "dp-forge-check", "✓");
      const error = el("small", "dp-forge-error");
      const thumb = typeof item.thumb === "string" && item.thumb.startsWith(API + "thumb?") ? item.thumb : "";
      card.title = label; pick.title = label;
      pick.setAttribute("aria-label", label + (into === "refs" ? " 선택" : " 가져오기"));
      zoom.setAttribute("aria-label", label + " 크게 보기");
      image.loading = "lazy"; image.alt = "";
      const unavailable = () => {
        image.remove(); picture.append(el("span", "dp-forge-unavailable", "미리보기 없음"));
      };
      image.addEventListener("error", unavailable, { once: true });
      picture.append(image);
      if (thumb && !item.blocked) image.src = thumb; else unavailable();
      if (item.fav) badges.append(el("span", "", "♥ 좋아요"));
      if (scope.value === "team" && item.mine) badges.append(el("span", "", "내 것"));
      if (item.batch && item.batch.n >= 2) badges.append(el("span", "", "묶음 " + item.batch.n + "장 중 " + item.batch.pos));
      mark.hidden = true; picture.append(badges, mark);
      const details = source === "history" ? [item.model, dateText(item.created)]
        : [item.folder || "기본 폴더", ...(Array.isArray(item.tags) ? item.tags.slice(0, 3) : [])];
      pick.append(picture, el("span", "dp-forge-label", label), el("small", "dp-forge-meta", details.filter(Boolean).join(" · ")));
      if (item.blocked) pick.append(el("small", "dp-forge-blocked", "가져올 수 없는 주소"));
      const record = { node: card, item, pick, zoom, mark, error, thumb };
      card.addEventListener("click", () => choose(item));
      pick.addEventListener("click", (event) => { event.stopPropagation(); return choose(item); });
      zoom.addEventListener("click", (event) => { event.stopPropagation(); preview(record, label); });
      card.append(pick, zoom, error); cards.set(item.id, record); grid.append(card);
    }
    async function load(append = false, refreshAssets = false) {
      if (!alive() || sending || (append && (loading || !next))) return;
      const seq = ++sequence;
      cancelList();
      const request = new AbortController();
      listRequest = request;
      loading = true; clearRetry(); syncControls(); status.textContent = "목록을 불러오는 중…";
      const query = new URLSearchParams({ root });
      if (searchTerm) query.set("search", searchTerm);
      if (source === "history") {
        query.set("scope", scope.value);
        if (model.value) query.set("model", model.value);
        if (since) query.set("since", since);
        if (favorite.getAttribute("aria-pressed") === "true") query.set("fav", "1");
        if (studio.getAttribute("aria-pressed") === "true") query.set("studio", "1");
      } else {
        if (folder.value) query.set("folder", folder.value);
        if (refreshAssets) query.set("refresh", "1");
      }
      if (append) query.set(source === "history" ? "cursor" : "offset", next);
      try {
        const data = await call((source === "history" ? "history" : "assets") + "?" + query, null, request.signal);
        if (!alive() || seq !== sequence) return;
        for (const item of data.items || []) {
          if (source === "history" && item.model) models.add(item.model);
          if (seen.has(item.id)) continue;
          seen.add(item.id);
          addCard(item);
        }
        updateChoices(model, models, "전체 모델");
        if (source === "asset" && Array.isArray(data.folders)) updateChoices(folder, data.folders, "전체 폴더");
        next = data.next; more.hidden = !next;
        status.textContent = grid.children.length ? (into === "refs" ? "최대 8장을 골라 가져오세요." : "그림을 고르면 가져옵니다.") :
          next ? "이 범위에는 없습니다 — [계속 불러오기]" : "표시할 그림이 없습니다.";
        if (data.more) status.textContent += " 더 있음 — 검색어를 좁히세요.";
      } catch (error) {
        if (alive() && seq === sequence && !request.signal.aborted && error.name !== "AbortError") {
          status.textContent = error.message;
          // 쪽 이음표가 오래됐으면(서버가 목록을 다시 읽었거나 30분이 지남) 같은 쪽을 다시 보내도 같은 실패다 — 첫 쪽부터 다시 읽는다.
          offerRetry(append && error.reason === "forge_stale" ? () => reload() : () => load(append, refreshAssets));
        }
      } finally {
        if (listRequest === request) listRequest = null;
        if (alive() && seq === sequence) { loading = false; syncControls(); }
      }
    }
    function reload(delay = 0, refreshAssets = false) {
      if (!alive() || sending) return;
      searchTerm = search.value.trim();
      clearTimeout(timer); sequence += 1; cancelList(); loading = false; next = null;
      seen.clear(); cards.clear(); selected.clear(); grid.replaceChildren(); more.hidden = true;
      clearRetry(); syncControls();
      if ([...searchTerm].length === 1) { status.textContent = "두 글자 이상 입력하세요"; return; }
      if (delay) {
        status.textContent = delay === 300 ? "필터를 적용하는 중…" : "검색 입력을 기다리는 중…";
        timer = setTimeout(() => load(), delay);
      } else load(false, refreshAssets);
    }
    function showFilters() {
      const history = source === "history";
      for (const node of [model, period, favorite, scope, studio]) node.hidden = !history;
      for (const node of [folder, refresh]) node.hidden = history;
      search.placeholder = history ? "프롬프트 검색..." : "자료 이름·폴더·태그 검색";
      search.setAttribute("aria-label", search.placeholder);
    }
    for (const [key, label] of [["history", "최근 결과"], ["asset", "내 자료"]]) {
      const tab = button(label);
      tab.setAttribute("aria-pressed", String(source === key));
      tab.addEventListener("click", () => {
        if (sending || source === key) return;
        source = key;
        for (const node of tabs.children) node.setAttribute("aria-pressed", String(node === tab));
        showFilters(); reload();
      });
      tabs.append(tab);
    }
    for (const node of [model, scope, folder]) node.addEventListener("change", () => reload(300));
    period.addEventListener("change", () => {
      if (sending) return;
      since = "";
      if (period.value) {
        const date = new Date(); date.setHours(0, 0, 0, 0);
        if (period.value === "week") date.setDate(date.getDate() - date.getDay());
        if (period.value === "month") date.setDate(1);
        since = date.toISOString();
      }
      reload(300);
    });
    search.addEventListener("input", () => { if (search.value.trim() !== searchTerm) reload(500); });
    refresh.addEventListener("click", () => { if (!loading) reload(0, true); });
    retry.addEventListener("click", () => { if (!sending && !loading && retryAction) return retryAction(); });
    importButton.addEventListener("click", () => importItems([...selected.values()]));
    more.addEventListener("click", () => load(true)); closeButton.addEventListener("click", close);
    sizeLabel.append(columns); heading.append(el("h2", "", "Forge에서 가져오기"), sizeLabel);
    filters.append(search, model, period, favorite, scope, studio, folder, refresh);
    statusRow.append(status, retry); footer.append(more);
    if (into === "refs") footer.append(importButton);
    footer.append(closeButton);
    sheet.append(heading, tabs, filters, statusRow, grid, footer);
    overlay.append(sheet); document.body.append(overlay); showFilters(); search.focus(); reload();
    return true;
  }

  function saveButton({ root, ready, askFree, toast }) {
    if (!can("forge_save")) return null;
    const button = el("button", "dp-forge-save", "Forge 자료에 저장");
    button.type = "button"; button.disabled = !ready();
    let pending = false;
    button.addEventListener("click", async (event) => {
      if (!event.isTrusted || pending || !ready() || !can("forge_save")) return;
      pending = true;
      try {
        if (!await askFree("합성 그림을 Forge 내 자료에 저장합니다. 계속할까요?", ready) || !ready()) return;
        button.disabled = true;
        const data = await call("save", { root });
        toast((data.already ? "이미 저장한 그림입니다: " : "Forge 내 자료에 저장했습니다: ") + data.asset.name);
      } catch (error) { toast(error.message, "err"); }
      finally { pending = false; button.disabled = !ready(); }
    });
    return button;
  }
  return { open, saveButton, sourceLine };
})();
