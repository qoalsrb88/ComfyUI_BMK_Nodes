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
  async function call(path, body) {
    const response = await fetch(API + path, body ? {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    } : undefined);
    let data;
    try { data = await response.json(); } catch (_) { throw new Error("응답을 읽지 못했습니다 — 다시 시도해 주세요."); }
    if (!response.ok) throw new Error(data.error || "요청을 처리하지 못했습니다.");
    return data;
  }
  let active = null;
  function open({ root = "", into, name, onDone }) {
    if (!can("forge_import") || active || !["target", "refs"].includes(into) || !window.DPDialog) return false;
    const overlay = el("div", "overlay dp-forge"), sheet = el("div", "sheet dp-forge-sheet");
    const tabs = el("div", "lp-btns"), search = el("input", "dp-forge-search");
    const status = el("p", "dp-forge-status"), grid = el("div", "dp-forge-grid");
    const more = el("button", "", "더 보기"), closeButton = el("button", "", "닫기");
    let source = "history", next = null, sequence = 0, loading = false, sending = false, timer;
    const alive = () => active === overlay;
    function close() {
      if (!alive() || sending) return;
      clearTimeout(timer); sequence += 1;
      overlay.remove(); active = null;
      window.removeEventListener("keydown", inputKeys, true);
      window.DPDialog.unlock();
    }
    // DPDialog 의 보드 방향키 차단보다 먼저 입력칸의 기본 편집 동작을 살린다.
    function inputKeys(e) {
      if (alive() && e.target === search && !["Escape", "Tab"].includes(e.key)) e.stopImmediatePropagation();
    }
    function keys(e) {
      if (e.type !== "keydown") return;
      if (e.key === "Escape") { e.preventDefault(); close(); }
      if (e.key === "Tab") {
        e.preventDefault();
        const controls = [...sheet.querySelectorAll("button, input")].filter((n) => !n.disabled && !n.hidden);
        const index = controls.indexOf(document.activeElement);
        if (controls.length) controls[(index + (e.shiftKey ? controls.length - 1 : 1)) % controls.length].focus();
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
    search.type = "search"; search.placeholder = "검색"; search.setAttribute("aria-label", "Forge 자료 검색");
    async function choose(item) {
      if (sending) return;
      sending = true; clearTimeout(timer); sequence += 1;
      for (const node of sheet.querySelectorAll("button, input")) node.disabled = true;
      status.textContent = "그림을 가져오는 중…";
      try {
        const body = { into, source, id: item.id };
        if (root) body.root = root;
        if (into === "target") body.name = name;
        const data = await call("import", body);
        sending = false; close();
        if (onDone) await onDone(data);
      } catch (error) {
        if (alive()) {
          sending = false; loading = false;
          for (const node of sheet.querySelectorAll("button, input")) node.disabled = false;
          more.hidden = !next;
          status.textContent = error.message;
        }
      }
    }
    async function load(append = false) {
      if (!alive() || sending || (append && (loading || !next))) return;
      const seq = ++sequence;
      loading = true; more.disabled = true; status.textContent = "목록을 불러오는 중…";
      if (!append) { next = null; grid.replaceChildren(); more.hidden = true; }
      const query = new URLSearchParams({ root, search: search.value.trim() });
      if (append) query.set(source === "history" ? "cursor" : "offset", next);
      try {
        const data = await call((source === "history" ? "history" : "assets") + "?" + query);
        if (!alive() || seq !== sequence) return;
        for (const item of data.items || []) {
          const button = el("button", "dp-forge-item"), image = el("img");
          image.loading = "lazy"; image.alt = "";
          if (typeof item.thumb === "string" && item.thumb.startsWith(API + "thumb?")) image.src = item.thumb;
          button.append(image, el("span", "", source === "history" ? item.prompt : [item.name, item.folder].filter(Boolean).join(" · ")));
          button.addEventListener("click", () => choose(item));
          grid.append(button);
        }
        next = data.next; more.hidden = next === null || next === undefined;
        status.textContent = grid.children.length ? "그림을 고르면 가져옵니다." :
          next !== null && next !== undefined ? "이 범위에는 없습니다 — [더 보기]" : "표시할 그림이 없습니다.";
      } catch (error) {
        if (alive() && seq === sequence) status.textContent = error.message;
      } finally {
        if (alive() && seq === sequence) { loading = false; more.disabled = false; }
      }
    }
    for (const [key, label] of [["history", "최근 결과"], ["asset", "내 자료"]]) {
      const tab = el("button", "", label);
      tab.setAttribute("aria-pressed", String(source === key));
      tab.addEventListener("click", () => {
        if (sending || source === key) return;
        clearTimeout(timer); source = key;
        for (const node of tabs.children) node.setAttribute("aria-pressed", String(node === tab));
        load();
      });
      tabs.append(tab);
    }
    search.addEventListener("input", () => {
      clearTimeout(timer); sequence += 1; next = null; more.hidden = true;
      grid.replaceChildren(); timer = setTimeout(() => load(), 300);
    });
    more.addEventListener("click", () => load(true)); closeButton.addEventListener("click", close);
    sheet.append(el("h2", "", "Forge에서 가져오기"), tabs, search, status, grid, more, closeButton);
    overlay.append(sheet); document.body.append(overlay); search.focus(); load();
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
        toast("Forge 내 자료에 저장했습니다: " + data.asset.name);
      } catch (error) { toast(error.message, "err"); }
      finally { pending = false; button.disabled = !ready(); }
    });
    return button;
  }
  return { open, saveButton };
})();
