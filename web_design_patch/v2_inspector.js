// BMK Design Patch Studio v2 — 좌측 동시 도크(레이어+보관함) · 스플리터/드래그 도킹 · 우측 3탭 인스펙터 · 2열 모델 가이드 · 실행 도크.
"use strict";

window.DPV2Inspector = (() => {
  const Data = window.DPV2Data;
  const Api = window.DPV2Api;
  const Store = window.DPV2Store;
  const Canvas = window.DPV2Canvas;

  const $ = (id) => document.getElementById(id);
  let popoverAnchor = null;
  let draggedPatchId = null;

  function pulseCard(sel) {
    const el = document.querySelector(sel);
    if (!el) return;
    el.classList.remove("pulse-flash");
    void el.offsetWidth;
    el.classList.add("pulse-flash");
  }

  function setActiveTab(tab) {
    Store.state.rightTab = tab;
    document.querySelectorAll(".itab").forEach((b) => b.classList.toggle("on", b.dataset.tab === tab));
    const p1 = $("pane-instruct"), p2 = $("pane-compare"), p3 = $("pane-retouch");
    if (p1) p1.classList.toggle("hidden", tab !== "instruct");
    if (p2) p2.classList.toggle("hidden", tab !== "compare");
    if (p3) p3.classList.toggle("hidden", tab !== "retouch");
    renderInspector();
  }

  // ── 레퍼런스 연결/해제 공용 헬퍼 (POST /refs/link: op "add"|"remove", name, explicit 409 확인 재전송) ──
  async function sendLinkOp(rev, body) {
    try {
      return await Api.linkRef(Store.state.root, rev, body);
    } catch (err) {
      const ex = err && err.status === 409 && err.data && Array.isArray(err.data.explicit) ? err.data.explicit : null;
      if (!ex || !ex.length) throw err;
      const ok = await Data.askDialogConfirm(
        `${err.message || "다른 곳에서 참조 중인 파일입니다."}\n계속 진행할까요?`,
        { ok: "계속", cancel: "취소" }
      );
      if (!ok) return { cancelled: true };
      return Api.linkRef(Store.state.root, rev, Object.assign({}, body, { confirm: ex.map((x) => x.file) }));
    }
  }

  async function linkRefToPatches(fileName, patches) {
    const list = (patches || []).filter(Boolean);
    if (!list.length) {
      Store.toast("먼저 왼쪽 목록이나 캔버스에서 수정 영역을 선택해 주세요.");
      return;
    }
    let rev = Store.state.R?.rev ?? null;
    let linked = 0;
    try {
      for (const p of list) {
        const r = await sendLinkOp(rev, { op: "add", crop_id: p.id, name: fileName });
        if (r && r.cancelled) continue;
        if (r && typeof r.rev === "number") rev = r.rev;
        linked += 1;
      }
      if (linked > 0) {
        Store.toast(
          linked > 1
            ? `"${fileName}"를 선택된 ${linked}개 영역에 연결했습니다.`
            : `"${fileName}"를 "${list[0].name}"에 연결했습니다.`,
          "ok"
        );
        pulseCard("#ref-drop-slot");
        await Store.refreshAll(true);
      }
    } catch (err) {
      Store.toast(`레퍼런스 연결 실패: ${err.message}`, "err");
    }
  }

  async function unlinkRefFromPatch(patch, fileName) {
    if (!patch || !fileName) return;
    try {
      const r = await sendLinkOp(Store.state.R?.rev ?? null, { op: "remove", crop_id: patch.id, name: fileName });
      if (r && r.cancelled) return;
      Store.toast(`"${fileName}" 연결을 해제했습니다.`);
      await Store.refreshAll(true);
    } catch (err) {
      Store.toast(`연결 해제 실패: ${err.message}`, "err");
    }
  }

  // ── 1. 좌측 동시 도크: 수정 영역 레이어 목록 ──
  function renderPatchList() {
    const listEl = $("patch-list");
    if (!listEl) return;
    const patches = Store.getPatches();
    const selIds = Store.state.selectedIds;
    const primary = Store.primaryPatch();
    const multiMode = selIds.size > 1;
    const baseImg = Canvas.getBaseImage();
    const imgW = Canvas.view.imgW || (baseImg ? baseImg.naturalWidth : 1);
    const imgH = Canvas.view.imgH || (baseImg ? baseImg.naturalHeight : 1);

    const cntEl = $("layer-count");
    if (cntEl) cntEl.textContent = String(patches.length);

    const chkAll = $("chk-select-all");
    if (chkAll) {
      chkAll.checked = patches.length > 0 && patches.every((p) => p.selectedForRun);
    }

    const deselBtn = $("btn-deselect-patch");
    if (deselBtn) deselBtn.disabled = selIds.size === 0;

    listEl.innerHTML = "";
    if (!patches.length) {
      listEl.append(Data.el("li", { className: "vault-empty", text: "아직 수정 영역이 없습니다. 상단 [+ 추가] 또는 M 키로 그려보세요." }));
      return;
    }

    patches.forEach((p, idx) => {
      const isSel = selIds.has(p.id);
      const isPrimary = !!(primary && primary.id === p.id);
      const purp = Data.purposeInfo(p.meta.purpose);

      const li = Data.el("li", {
        className: "patch-item" + (isPrimary ? " selected" : "") + (multiMode && isSel ? " multi-selected" : ""),
        draggable: "true",
        dataset: { patchId: p.id },
      });

      // 좌측 6-dot Drag Handle (드래그 재정렬 가능 UI 명시)
      const dragHandle = Data.el("span", {
        className: "patch-drag-handle",
        title: "드래그해서 레이어 겹침 순서(Z-Order) 변경",
      }, Data.icon("grip", 13));

      // 생성 대상 체크박스
      const chk = Data.el("input", {
        type: "checkbox",
        title: "생성 대상에 포함",
        onClick: (e) => e.stopPropagation(),
        onChange: (e) => Store.setPatchCheckedForRun(p.id, e.target.checked),
      });
      chk.checked = p.selectedForRun;

      // 42x42 실시간 크롭 썸네일
      const thumb = Data.el("div", { className: "patch-thumb" });
      const tc = document.createElement("canvas");
      tc.width = 84;
      tc.height = 84;
      if (baseImg && baseImg.naturalWidth && baseImg.naturalHeight) {
        const [x1, y1, x2, y2] = p.rect;
        const sx = (x1 / imgW) * baseImg.naturalWidth;
        const sy = (y1 / imgH) * baseImg.naturalHeight;
        const sw = Math.max(8, ((x2 - x1) / imgW) * baseImg.naturalWidth);
        const sh = Math.max(8, ((y2 - y1) / imgH) * baseImg.naturalHeight);
        try {
          tc.getContext("2d").drawImage(baseImg, sx, sy, sw, sh, 0, 0, 84, 84);
        } catch (_) {}
      }
      thumb.append(tc, Data.el("span", { className: "patch-idx-badge", text: String(idx + 1) }));
      if (p.anyPicked) thumb.append(Data.el("span", { className: "patch-star-mini", text: "★" }));

      // 정보 & 행 컨트롤 (눈 · 잠금 · 삭제)
      const themeMode = Store.activeTheme().mode;
      const pCol = Data.purposeColor(p.meta.purpose, themeMode);
      const info = Data.el("div", { className: "patch-info" });
      const purpIcoWrap = Data.el("span", {
        className: "patch-purpose-ico",
        title: purp.label,
        style: `--p-col:${pCol};--p-soft:${Data.hexToRgba(pCol, themeMode === "light" ? 0.13 : 0.18)};`,
      }, Data.icon(purp.icon, 11));
      const nameLine = Data.el("div", { className: "patch-name-line" },
        purpIcoWrap,
        Data.el("span", { className: "patch-name", text: p.name, title: p.name }),
      );

      const hasCands = p.candidates.length > 0;
      const statusCls = (Store.state.S && Store.state.S.running)
        ? "running"
        : p.anyPicked ? "done" : hasCands ? "warn" : "idle";
      const statusTxt = p.anyPicked
        ? `★ ${p.candidates.length}장`
        : hasCands ? `${p.candidates.length}장` : "대기";
      const rawModelShort = p.modelInfo ? p.modelInfo.shortName : p.effectiveModel;
      const compactModel = rawModelShort
        .replace(/^GPT\s+2\.6.*$/i, "GPT 2.6")
        .replace(/^GPT\s+2\.5.*$/i, "GPT 2.5")
        .replace(/^Nano Banana\s*/i, "NB ")
        .replace(/^Seedream\s*/i, "SR ")
        .replace(/^Ideogram\s*/i, "IG ");

      const metaEl = Data.el("div", { className: "patch-meta", title: `${statusTxt} · ${rawModelShort}` },
        Data.el("span", { className: `status-dot ${statusCls}` }),
        Data.el("span", { className: "patch-meta-text", text: `${statusTxt} · ${compactModel}` }),
      );

      const ctrls = Data.el("div", { className: "patch-row-ctrls", onClick: (e) => e.stopPropagation() });

      const btnEye = Data.el("button", {
        type: "button",
        className: "row-icon-btn" + (p.meta.hidden ? " off" : ""),
        title: p.meta.hidden ? "가이드 다시 표시" : "가이드 숨기기",
        onClick: () => { p.meta.hidden = !p.meta.hidden; Store.emit("selection"); },
      }, Data.icon(p.meta.hidden ? "eyeOff" : "eye", 12));
      const btnLock = Data.el("button", {
        type: "button",
        className: "row-icon-btn" + (p.locked ? " locked" : ""),
        title: p.locked ? "잠금 해제" : "위치 잠금",
        onClick: () => Store.updateCrop(p.id, { locked: !p.locked }, p.locked ? `잠금 해제 (${p.name})` : `잠금 (${p.name})`),
      }, Data.icon(p.locked ? "lock" : "unlock", 12));
      const btnDel = Data.el("button", {
        type: "button", className: "row-icon-btn danger", title: "영역 삭제",
        onClick: async () => {
          const ok = await Data.askDialogConfirm(`"${p.name}" 수정 영역을 삭제할까요?`, { ok: "삭제", cancel: "취소", danger: true });
          if (ok) await Store.deleteCrop(p.id);
        },
      }, Data.icon("x", 12));

      ctrls.append(btnEye, btnLock, btnDel);
      info.append(nameLine, Data.el("div", { className: "patch-sub-row" }, metaEl, ctrls));

      // 우측 세로 정렬 상하 이동 화살표 스택 (Z-Order 위/아래)
      const zStack = Data.el("div", { className: "patch-z-stack", onClick: (e) => e.stopPropagation() },
        Data.el("button", {
          type: "button", className: "z-step-btn", title: "한 단계 위로 이동 (Z-순서)", disabled: idx === 0,
          onClick: () => Store.moveCropZ(p.id, "step", "up"),
        }, Data.icon("up", 11)),
        Data.el("button", {
          type: "button", className: "z-step-btn", title: "한 단계 아래로 이동 (Z-순서)", disabled: idx === patches.length - 1,
          onClick: () => Store.moveCropZ(p.id, "step", "down"),
        }, Data.icon("down", 11)),
      );

      li.append(dragHandle, chk, thumb, info, zStack);

      li.addEventListener("click", (e) => {
        Store.selectPatch(p.id, { shift: e.shiftKey, ctrl: e.ctrlKey || e.metaKey });
      });

      // 드래그 Z-Order 재정렬(상/하반부 정밀 드롭) + 레퍼런스 카드 드롭 수용
      li.addEventListener("dragstart", (e) => {
        draggedPatchId = p.id;
        li.classList.add("dragging");
        e.dataTransfer.effectAllowed = "move";
        e.dataTransfer.setData("text/dps-patch-id", p.id);
      });
      li.addEventListener("dragend", () => {
        draggedPatchId = null;
        document.querySelectorAll(".patch-item.dragging, .patch-item.drag-over-top, .patch-item.drag-over-bottom").forEach((el) => {
          el.classList.remove("dragging", "drag-over-top", "drag-over-bottom");
        });
      });
      li.addEventListener("dragover", (e) => {
        if (e.dataTransfer?.types?.includes("text/dps-ref-file")) {
          e.preventDefault();
          li.classList.add("drop-hover");
        } else if (draggedPatchId && draggedPatchId !== p.id) {
          e.preventDefault();
          const r = li.getBoundingClientRect();
          const isLower = (e.clientY - r.top) > r.height / 2;
          li.classList.toggle("drag-over-top", !isLower);
          li.classList.toggle("drag-over-bottom", isLower);
        }
      });
      li.addEventListener("dragleave", () => {
        li.classList.remove("drop-hover", "drag-over-top", "drag-over-bottom");
      });
      li.addEventListener("drop", async (e) => {
        const dropBelow = li.classList.contains("drag-over-bottom");
        li.classList.remove("drop-hover", "drag-over-top", "drag-over-bottom");
        const refFile = e.dataTransfer?.getData("text/dps-ref-file");
        if (refFile) {
          e.preventDefault();
          Store.selectPatch(p.id);
          await linkRefToPatches(refFile, [p]);
          return;
        }
        if (draggedPatchId && draggedPatchId !== p.id) {
          e.preventDefault();
          const moving = draggedPatchId;
          draggedPatchId = null;
          await Store.moveCropZ(moving, dropBelow ? "before" : "after", p.id);
        }
      });

      listEl.append(li);
    });
  }

  // ── 2. 좌측 동시 도크: 레퍼런스 보관함 ──
  function renderRefVault() {
    const grid = $("ref-vault-grid");
    if (!grid) return;
    const pool = (Store.state.R && Array.isArray(Store.state.R.pool)) ? Store.state.R.pool : [];
    const sheets = Array.isArray(Store.state.sheets) ? Store.state.sheets : [];

    const cntEl = $("ref-count");
    if (cntEl) cntEl.textContent = String(pool.length + sheets.length);

    grid.innerHTML = "";
    if (!pool.length && !sheets.length) {
      grid.append(Data.el("div", { className: "vault-empty", text: "레퍼런스를 추가하거나 시트를 올려 오려내세요." }));
      return;
    }

    for (const item of pool) {
      const thumbUrl = Api.apiUrl(item.thumb_url || "");
      const card = Data.el("div", {
        className: "ref-card",
        draggable: "true",
        title: "수정 영역으로 끌어다 놓거나 + 버튼으로 바로 연결",
      },
        Data.el("span", { className: "ref-card-type", text: "레퍼런스" }),
        Data.el("div", { className: "ref-card-img" }, thumbUrl ? Data.el("img", { src: thumbUrl, alt: item.name }) : null),
        Data.el("div", { className: "ref-card-label", text: item.name }),
        Data.el("button", {
          type: "button", className: "ref-card-attach-btn", text: "+", title: "현재 선택 영역에 연결",
          onClick: (e) => {
            e.stopPropagation();
            linkRefToPatches(item.name, Store.selectedPatches());
          },
        }),
      );
      card.addEventListener("dragstart", (e) => {
        e.dataTransfer.setData("text/dps-ref-file", item.name);
        e.dataTransfer.effectAllowed = "copy";
      });
      grid.append(card);
    }

    for (const sh of sheets) {
      const previewUrl = Api.sheetUrl(Store.state.root, sh, "preview");
      const card = Data.el("div", {
        className: "ref-card",
        title: "클릭해 시트 오리기 · 4점 원근 열기",
        onClick: () => window.DPV2Overlays && window.DPV2Overlays.openSheetOverlay(sh.path),
      },
        Data.el("span", { className: "ref-card-type", text: "원화 시트" }),
        Data.el("div", { className: "ref-card-img" }, previewUrl ? Data.el("img", { src: previewUrl, alt: sh.name }) : null),
        Data.el("div", { className: "ref-card-label", text: sh.name }),
      );
      grid.append(card);
    }
  }

  // ── 3. 우측 3탭 컨텍스트 인스펙터 ──
  function renderInspector() {
    const patch = Store.primaryPatch();
    const selList = Store.selectedPatches();
    const isMulti = selList.length > 1;

    // 탭 배지
    const candCount = patch ? patch.candidates.length : 0;
    const badge = $("tab-cand-badge");
    if (badge) {
      badge.textContent = String(candCount);
      badge.classList.toggle("hidden", candCount === 0);
    }

    // ── TAB 1: 지시 · 모델 ──
    const empty1 = $("instruct-empty");
    const cont1 = $("instruct-content");
    if (empty1) empty1.classList.toggle("hidden", !!patch);
    if (cont1) cont1.classList.toggle("hidden", !patch);

    if (patch) {
      const msb = $("multi-select-banner");
      const headCard = $("single-patch-head-card");
      if (msb) msb.classList.toggle("hidden", !isMulti);
      if (headCard) headCard.classList.toggle("hidden", isMulti);
      if (isMulti) {
        const msbTitle = $("msb-title");
        if (msbTitle) msbTitle.textContent = `${selList.length}개 영역 동시 편집 중 (Shift/Ctrl)`;
      } else {
        const nameInp = $("inp-patch-name");
        if (nameInp && document.activeElement !== nameInp) nameInp.value = patch.name;
        const [x1, y1, x2, y2] = patch.rect;
        const rw = x2 - x1, rh = y2 - y1;
        const af = Data.computeAutoCropFrame(patch.rect, Canvas.view.imgW || 1024, Canvas.view.imgH || 1024);
        const infoEl = $("crop-auto-info");
        if (infoEl) {
          infoEl.innerHTML = "";
          infoEl.append(
            Data.el("span", { text: `선택 ${rw}×${rh}px · AI 자동 크롭 ${af.w}×${af.h}px` }),
            Data.el("span", { className: "help-tip", text: "?", title: "원본 훼손 없이 주변 맥락을 함께 보도록 128px 배수로 자동 계산됩니다." }),
          );
        }
      }

      // 5종 작업 목적 그리드 (고유 시맨틱 컬러 연동)
      const pg = $("purpose-grid");
      const commonPurp = Store.getCommonOrMixed(selList, (p) => p.meta.purpose);
      const themeMode = Store.activeTheme().mode;
      if (pg) {
        pg.innerHTML = "";
        for (const pr of Data.PURPOSES) {
          const pc = Data.purposeColor(pr.id, themeMode);
          const pSoft = Data.hexToRgba(pc, themeMode === "light" ? 0.12 : 0.17);
          const pBorder = Data.hexToRgba(pc, themeMode === "light" ? 0.55 : 0.62);
          const btn = Data.el("button", {
            type: "button",
            className: "purpose-btn" + (!commonPurp.mixed && commonPurp.value === pr.id ? " on" : ""),
            title: pr.hint,
            style: `--p-col:${pc};--p-soft:${pSoft};--p-border:${pBorder};`,
            onClick: () => {
              for (const sp of selList) {
                sp.meta.purpose = pr.id;
                sp.meta.dirtySpec = true;
              }
              Store.emit("selection");
              if (!isMulti && Api.can("v2_spec_edit")) {
                Store.savePatchSpec(patch).catch((err) => Store.toast(`지시문 저장 실패: ${err.message}`, "err"));
              }
            },
          }, Data.el("span", { className: "purpose-btn-ico" }, Data.icon(pr.icon, 13)), Data.el("span", { text: pr.label }));
          pg.append(btn);
        }
        if (isMulti && commonPurp.mixed) {
          const mb = Data.el("span", { className: "mixed-badge", text: "목적 혼합됨 · 클릭 시 일괄 적용" });
          mb.style.gridColumn = "1 / -1";
          pg.append(mb);
        }
      }

      // 목적별 모델 추천 배너
      const curPurp = Data.purposeInfo(commonPurp.mixed ? patch.meta.purpose : (commonPurp.value || patch.meta.purpose));
      const commonModel = Store.getCommonOrMixed(selList, (p) => p.effectiveModel);
      const recBanner = $("purpose-model-rec");
      if (recBanner) {
        const recModelObj = Data.modelCatalog().find((m) => m.name === curPurp.recModel);
        if (recModelObj && (commonModel.mixed || commonModel.value !== recModelObj.name)) {
          recBanner.classList.remove("hidden");
          recBanner.innerHTML = "";
          recBanner.append(
            Data.el("span", {}, Data.el("b", { text: curPurp.short }), " 추천: ", Data.el("b", { text: recModelObj.shortName })),
            Data.el("button", {
              type: "button", className: "mini-btn accent-outline",
              text: isMulti ? `${selList.length}개 적용` : "이 영역에 적용",
              onClick: () => applyModelSelection(recModelObj.name),
            }),
          );
        } else {
          recBanner.classList.add("hidden");
        }
      }

      // 연결된 레퍼런스 목록
      const refListEl = $("attached-refs-list");
      const files = (patch.refCrop && Array.isArray(patch.refCrop.files)) ? patch.refCrop.files : [];
      if (refListEl) {
        refListEl.innerHTML = "";
        if (!files.length) {
          refListEl.append(Data.el("div", {
            className: "attached-ref-empty",
            text: "연결된 레퍼런스가 없습니다. 좌측 보관함 카드를 여기로 끌어다 놓거나 [시트 오리기]를 눌러주세요.",
          }));
        } else {
          files.forEach((rf, i) => {
            const thumb = Api.apiUrl(rf.thumb_url || "");
            const row = Data.el("div", { className: "attached-ref-item" },
              thumb ? Data.el("img", { src: thumb, alt: "" }) : null,
              Data.el("div", { className: "attached-ref-meta" },
                Data.el("strong", { text: rf.name }),
                Data.el("small", { text: rf.role === "main" ? "대표 레퍼런스" : "보조 레퍼런스" }),
              ),
              Data.el("span", { className: "ref-role-badge", text: i === 0 ? "대표 ★" : `보조 ${i}` }),
              Data.el("button", {
                type: "button", className: "row-icon-btn", title: "연결 해제",
                onClick: () => unlinkRefFromPatch(patch, rf.name),
              }, Data.icon("x", 12)),
            );
            refListEl.append(row);
          });
        }
      }

      // @원본 / @참조1..N 멘션 바
      const mbar = $("mention-bar");
      if (mbar) {
        mbar.innerHTML = "";
        const baseSrc = Store.state.S && Store.state.S.canvas ? Api.apiUrl(Store.state.S.canvas.base_url) : "";
        mbar.append(Data.el("button", {
          type: "button", className: "mention-chip",
          onClick: () => insertMention(patch, "@원본"),
        }, baseSrc ? Data.el("img", { src: baseSrc, alt: "" }) : null, Data.el("span", { text: "@원본" })));

        files.forEach((rf, i) => {
          const thumb = Api.apiUrl(rf.thumb_url || "");
          mbar.append(Data.el("button", {
            type: "button", className: "mention-chip",
            onClick: () => insertMention(patch, `@참조${i + 1}`),
          }, thumb ? Data.el("img", { src: thumb, alt: "" }) : null, Data.el("span", { text: `@참조${i + 1}` })));
        });
      }

      // 지시문 · AI 자동 · AI 요약 · 원문 오버라이드
      const chkAuto = $("chk-ai-auto");
      if (chkAuto) chkAuto.checked = !!patch.meta.aiAuto;
      const instEl = $("inp-instruction");
      if (instEl) {
        if (document.activeElement !== instEl) instEl.value = patch.meta.user_instruction_ko || "";
        instEl.placeholder = curPurp.placeholder;
      }

      const aiDraftBtn = $("btn-ai-draft");
      if (aiDraftBtn) {
        aiDraftBtn.innerHTML = "";
        aiDraftBtn.append(
          Data.icon("sparkles", 14),
          Store.state.assistBusy
            ? "AI 프롬프트 작성 중…"
            : (patch.meta.body_en ? "AI 프롬프트 다시 작성" : "AI 프롬프트 미리 작성"),
        );
        aiDraftBtn.disabled = !!Store.state.assistBusy;
      }

      // D503 차단 상태 또는 미반영 새 크롭 배너
      const blockedEl = $("spec-blocked-note");
      if (blockedEl) {
        const showBlocked = patch.meta.specState === "blocked" || patch.meta.needsApplyForSpec;
        blockedEl.classList.toggle("hidden", !showBlocked);
        if (showBlocked) {
          blockedEl.textContent = patch.meta.needsApplyForSpec
            ? "새로 추가한 영역입니다 — 무료 [합성 적용] 후 지시문 저장 및 AI 작성을 사용할 수 있습니다."
            : (patch.meta.specWhy || "지시문에 맞는 최신 프롬프트가 필요합니다. [AI 스마트 프롬프트 미리 작성] 또는 생성 시 자동 작성됩니다.");
        }
      }

      // AI 요약 박스
      const aiBox = $("ai-summary-box");
      const hasSummaryOrBody = !!(patch.meta.summary_ko || patch.meta.body_en || patch.meta.prompt_en);
      if (aiBox) {
        aiBox.classList.toggle("hidden", !hasSummaryOrBody);
        if (hasSummaryOrBody) {
          const tagEl = $("ai-skill-tag");
          if (tagEl) {
            tagEl.textContent = patch.meta.skill_ids.length
              ? `Forge AI · ${patch.meta.skill_ids.join(", ")}`
              : (patch.meta.promptSource === "manual" ? "직접 작성 본문" : "AI 프롬프트");
          }
          const tmEl = $("ai-target-model");
          if (tmEl) tmEl.textContent = `${patch.modelInfo ? patch.modelInfo.shortName : patch.effectiveModel} 맞춤`;
          const koEl = $("ai-summary-ko");
          if (koEl) koEl.textContent = patch.meta.summary_ko || (patch.meta.promptSource === "manual" ? "직접 수정한 영문 요청 본문이 적용됩니다." : "작성된 프롬프트가 준비되었습니다.");
          const rawEl = $("inp-raw-prompt");
          if (rawEl && document.activeElement !== rawEl) rawEl.value = patch.meta.body_en || "";
          const fullEl = $("box-full-prompt");
          if (fullEl) {
            fullEl.classList.toggle("hidden", !patch.meta.prompt_en);
            fullEl.textContent = patch.meta.prompt_en || "";
          }
        }
      }

      // 모델 & 생성 옵션
      const chkInherit = $("chk-inherit-model");
      if (chkInherit) chkInherit.checked = selList.every((p) => !p.meta.modelOverride);

      const mName = $("mdt-name"), mSub = $("mdt-sub");
      if (mName && mSub) {
        if (commonModel.mixed) {
          mName.textContent = `혼합됨 (${new Set(selList.map((p) => p.effectiveModel)).size}종 모델)`;
          mSub.textContent = "클릭해 선택된 모든 영역의 모델을 한 번에 통일할 수 있습니다";
        } else {
          const mi = patch.modelInfo || Data.findModel(patch.effectiveModel);
          mName.textContent = mi ? mi.label : patch.effectiveModel;
          mSub.textContent = mi ? `${mi.badge} · 최대 참조 ${mi.max_inputs}장` : "";
        }
      }

      // 후보 장수 세그먼트
      const commonCount = Store.getCommonOrMixed(selList, (p) => p.effectiveCount);
      document.querySelectorAll("#seg-count button").forEach((b) => {
        b.classList.toggle("on", !commonCount.mixed && Number(b.dataset.val) === commonCount.value);
      });

      // 생성 품질 세그먼트 (라이브 모델 카탈로그 기준 동적)
      const qualHost = $("seg-quality");
      if (qualHost) {
        qualHost.innerHTML = "";
        const mi = commonModel.mixed ? null : patch.modelInfo;
        const quals = (mi && mi.qualities && mi.qualities.length) ? mi.qualities : [];
        if (!quals.length) {
          qualHost.append(Data.el("button", { type: "button", disabled: true, text: "자동" }));
        } else {
          const commonQ = Store.getCommonOrMixed(selList, (p) => p.effectiveQuality);
          const pickList = quals.slice(0, 4);
          for (const q of pickList) {
            qualHost.append(Data.el("button", {
              type: "button",
              className: !commonQ.mixed && commonQ.value === q ? "on" : "",
              text: q,
              onClick: () => applyQualitySelection(q),
            }));
          }
        }
      }

      // 모델 고유 동적 옵션 (Nano Banana image_size / thinking_level 등)
      const extraHost = $("model-extra-opts");
      if (extraHost) {
        extraHost.innerHTML = "";
        const mi = commonModel.mixed ? null : patch.modelInfo;
        const optEntries = mi && mi.opts ? Object.entries(mi.opts) : [];
        extraHost.classList.toggle("hidden", optEntries.length === 0);
        for (const [optKey, choices] of optEntries) {
          if (!Array.isArray(choices) || !choices.length) continue;
          const label = optKey === "image_size" ? "출력 해상도" : optKey === "thinking_level" ? "추론 깊이" : optKey;
          const prep = Store.projectPrepare();
          const curVal = (patch.meta.opts && patch.meta.opts[optKey]) || prep.opts[optKey] || (mi.opt_defaults && mi.opt_defaults[optKey]) || choices[0];
          const seg = Data.el("div", { className: "seg-group" });
          for (const ch of choices) {
            seg.append(Data.el("button", {
              type: "button",
              className: curVal === ch ? "on" : "",
              text: optKey === "thinking_level" ? (Data.THINKING_LABELS[ch] || ch) : ch,
              onClick: () => applyExtraOpt(optKey, ch),
            }));
          }
          extraHost.append(Data.el("div", { className: "param-col" }, Data.el("label", { text: label }), seg));
        }
      }

      // 전문가 설정 상태
      const chkAutoCrop = $("adv-show-autocrop");
      if (chkAutoCrop) chkAutoCrop.checked = !!Store.state.prefs.showAutoCrop;
      const advComp = $("adv-complexity");
      if (advComp) advComp.value = Store.projectPrepare().complexity || "high";
      const advChanged = !!Store.state.prefs.showAutoCrop || (Store.projectPrepare().complexity !== "high");
      const advDot = $("adv-changed-dot");
      if (advDot) advDot.classList.toggle("hidden", !advChanged);
      const advTxt = $("adv-summary-text");
      if (advTxt) advTxt.textContent = advChanged ? "사용자 조정됨" : "기본값 적용 중";
    }

    // ── TAB 2: 후보 비교 ──
    const hasCands = !!(patch && patch.candidates.length > 0);
    const cmpEmpty = $("compare-empty"), cmpCont = $("compare-content");
    if (cmpEmpty) cmpEmpty.classList.toggle("hidden", hasCands);
    if (cmpCont) cmpCont.classList.toggle("hidden", !hasCands);

    if (hasCands) {
      const titleEl = $("compare-patch-title");
      if (titleEl) titleEl.textContent = `${patch.name} 후보 (${patch.candidates.length}장)`;

      // 멀티타깃 A/B/C 필 바
      const tRow = $("target-pill-row");
      if (tRow) {
        tRow.innerHTML = "";
        tRow.classList.toggle("hidden", patch.targets.length <= 1);
        for (const tg of patch.targets) {
          const isCur = patch.target && patch.target.key === tg.key;
          tRow.append(Data.el("button", {
            type: "button",
            className: "target-pill" + (isCur ? " on" : ""),
            text: `타깃 ${tg.tid}${tg.label ? ` · ${tg.label.slice(0, 12)}` : ""}`,
            onClick: () => Store.selectTargetForCrop(patch.id, tg.key),
          }));
        }
      }

      const activeCand = Store.currentCandidate(patch);
      const tl = Data.trafficLight(activeCand, patch.seam);
      const tlEl = $("quality-traffic-light");
      if (tlEl) tlEl.className = `traffic-banner ${tl.level === "none" ? "" : tl.level}`.trim();
      const tlTitle = $("tl-title"), tlDesc = $("tl-desc"), tlFix = $("btn-tl-fix");
      if (tlTitle) tlTitle.textContent = `${tl.icon} ${tl.label}`;
      if (tlDesc) tlDesc.textContent = tl.detail;
      if (tlFix) tlFix.classList.toggle("hidden", tl.level === "ok" || tl.level === "none");

      // 후보 카드 그리드
      const cgrid = $("cand-grid");
      if (cgrid) {
        cgrid.innerHTML = "";
        patch.candidates.forEach((cand, idx) => {
          const isPicked = !!(patch.pickedCand && patch.pickedCand.key === cand.key);
          const isActive = !!(activeCand && activeCand.key === cand.key);
          const thumbUrl = Api.apiUrl(cand.thumb_url || cand.big_url || cand.pick_url || "");
          const cTl = Data.trafficLight(cand, patch.seam);

          const card = Data.el("div", {
            className: "cand-card" + (isPicked ? " picked" : "") + (isActive ? " active" : "") + (cand.rejected ? " rejected" : ""),
            onClick: () => {
              if (patch.target) Store.selectCandidate(patch.target.key, cand.key);
            },
            onDblclick: () => window.DPV2Overlays && window.DPV2Overlays.openCompareOverlay(cand.key),
          },
            Data.el("div", { className: "cand-thumb" },
              thumbUrl ? Data.el("img", { src: thumbUrl, alt: `후보 ${idx + 1}` }) : null,
              isPicked ? Data.el("span", { className: "cand-star-badge", text: "★ 적용 중" }) : null,
              Data.el("span", { className: "cand-num-badge", text: `#${cand.n || idx + 1}` }),
            ),
            Data.el("div", { className: "cand-foot" },
              Data.el("div", { className: "cand-foot-top" },
                Data.el("span", { text: `${cTl.icon} ${cand.variant || `V${idx + 1}`}` }),
                Data.el("span", { text: cand.took ? Data.formatElapsed(cand.took) : "" }),
              ),
              Data.el("div", { className: "cand-foot-btns" },
                Data.el("button", {
                  type: "button",
                  className: "mini-btn" + (isPicked ? " accent-outline" : ""),
                  text: isPicked ? "적용됨 ★" : "★ 채택",
                  onClick: async (e) => {
                    e.stopPropagation();
                    if (!patch.target) return;
                    try {
                      await Api.pickCandidate(Store.state.root, {
                        crop_id: patch.id, tid: patch.target.tid, key: cand.key,
                        mode: isPicked ? "remove" : "set",
                      });
                      Store.selectCandidate(patch.target.key, cand.key);
                      Store.toast(isPicked ? "채택을 해제했습니다." : `"${patch.name}"에 후보 #${cand.n || idx + 1}을 확정 적용했습니다 ★`, "ok");
                      await Store.refreshAll(true);
                    } catch (err) {
                      Store.toast(`채택 실패: ${err.message}`, "err");
                    }
                  },
                }),
                Data.el("button", {
                  type: "button", className: "mini-btn",
                  text: cand.rejected ? "복원" : "제외",
                  onClick: async (e) => {
                    e.stopPropagation();
                    try {
                      await Api.rejectCandidate(Store.state.root, cand.key, !cand.rejected);
                      await Store.refreshAll(true);
                    } catch (err) {
                      Store.toast(`상태 변경 실패: ${err.message}`, "err");
                    }
                  },
                }),
              ),
            ),
          );
          cgrid.append(card);
        });
      }

      // 클릭 점프 설정 요약 카드
      const sj = $("used-settings-summary");
      if (sj) {
        sj.innerHTML = "";
        const purp = Data.purposeInfo(patch.meta.purpose);
        const files = (patch.refCrop && Array.isArray(patch.refCrop.files)) ? patch.refCrop.files : [];
        const items = [
          { label: "작업 목적", val: purp.label, target: "#purpose-card" },
          { label: "사용 모델", val: patch.modelInfo ? patch.modelInfo.shortName : patch.effectiveModel, target: "#model-card" },
          { label: "참고 레퍼런스", val: `${files.length}장 연결`, target: "#ref-drop-slot" },
          { label: "수정 지시", val: patch.meta.user_instruction_ko || "(기본 템플릿)", target: "#prompt-card" },
        ];
        for (const it of items) {
          sj.append(Data.el("button", {
            type: "button", className: "jump-card",
            onClick: () => {
              setActiveTab("instruct");
              setTimeout(() => {
                const el = document.querySelector(it.target);
                if (el) {
                  el.scrollIntoView({ behavior: "smooth", block: "center" });
                  pulseCard(it.target);
                }
              }, 30);
            },
          }, Data.el("small", { text: it.label }), Data.el("strong", { text: it.val })));
        }
      }
    }

    // ── TAB 3: 후보정 (비파괴 스마트 필터) ──
    const activeCand = Store.currentCandidate(patch);
    const canRetouch = !!(patch && activeCand);
    const rtEmpty = $("retouch-empty"), rtCont = $("retouch-content");
    if (rtEmpty) rtEmpty.classList.toggle("hidden", canRetouch);
    if (rtCont) rtCont.classList.toggle("hidden", !canRetouch);

    if (canRetouch) {
      const f = patch.meta.filters;
      setFilterCardUI("brush", f.brushEnabled, f.brushEnabled ? "활성" : "꺼짐");
      setFilterCardUI("edge", f.edgeEnabled, `${f.feather}px`);
      setFilterCardUI("transform", f.transformEnabled, "");
      setFilterCardUI("color", f.colorEnabled, `${f.opacity}%`);

      const setVal = (id, v) => { const el = $(id); if (el) el.value = String(v); };
      const setTxt = (id, t) => { const el = $(id); if (el) el.textContent = t; };
      setVal("rt-feather", f.feather); setTxt("rt-feather-val", `${f.feather}px`);
      setVal("rt-fade-top", f.fadeTop); setVal("rt-fade-bottom", f.fadeBottom);
      setVal("rt-fade-left", f.fadeLeft); setVal("rt-fade-right", f.fadeRight);
      setVal("rt-dx", f.dx); setVal("rt-dy", f.dy);
      setVal("rt-scale", f.scale); setVal("rt-rot", f.rot);
      setVal("rt-opacity", f.opacity); setTxt("rt-opacity-val", `${f.opacity}%`);
      setVal("rt-bright", f.bright); setTxt("rt-bright-val", f.bright > 0 ? `+${f.bright}` : String(f.bright));
      setVal("rt-sat", f.sat); setTxt("rt-sat-val", f.sat > 0 ? `+${f.sat}` : String(f.sat));

      // Forge 후처리 버튼 게이트
      const canPost = Api.can("v2_postprocess");
      const upBtn = $("btn-tool-upscale"), bgBtn = $("btn-tool-bgremove");
      if (upBtn) upBtn.disabled = !canPost;
      if (bgBtn) bgBtn.disabled = !canPost;
      setTxt("upscale-desc", canPost ? "Magnific Precision V2 업스케일 실행" : "Phase V2-B 연결 대기 중");
      setTxt("bgremove-desc", canPost ? "배경 제거(RMBG) 누끼 실행" : "Phase V2-B 연결 대기 중");
    }
  }

  function setFilterCardUI(key, enabled, metaText) {
    const card = document.querySelector(`.filter-card[data-filter="${key}"]`);
    if (!card) return;
    card.classList.toggle("muted", !enabled);
    const eye = card.querySelector(".eye-btn");
    if (eye) {
      eye.classList.toggle("on", enabled);
      eye.innerHTML = "";
      eye.append(Data.icon(enabled ? "eye" : "eyeOff", 13));
    }
    const meta = $(`fm-${key}`);
    if (meta && metaText) meta.textContent = metaText;
  }

  // ── 4. 하단 고정 실행 도크 ──
  function renderRunDock() {
    const patches = Store.getPatches();
    const targets = patches.filter((p) => p.selectedForRun);
    const totalImgs = targets.reduce((s, p) => s + (p.effectiveCount || 4), 0);
    const models = [...new Set(targets.map((p) => p.modelInfo ? p.modelInfo.shortName : p.effectiveModel))];
    const running = !!(Store.state.S && Store.state.S.running);

    const leftEl = $("dock-summary-left"), rightEl = $("dock-summary-right");
    if (leftEl) {
      leftEl.textContent = targets.length
        ? `생성 대상 ${targets.length}개 · 예상 ${totalImgs}장`
        : "생성할 영역을 왼쪽에서 체크하세요";
    }
    if (rightEl) rightEl.textContent = models.length ? models.join(", ") : "-";

    const btnRun = $("btn-run-selected");
    if (btnRun) {
      btnRun.disabled = targets.length === 0 || running;
      btnRun.textContent = running
        ? "서버에서 생성 중…"
        : targets.length ? `선택 영역 ${targets.length}개 생성 시작` : "생성할 영역을 체크하세요";
    }

    const progWrap = $("run-progress-wrap");
    if (progWrap) {
      progWrap.classList.toggle("hidden", !running);
      if (running && Store.state.progress) {
        const pr = Store.state.progress;
        const pct = pr.total ? Math.round(((pr.done || 0) / pr.total) * 100) : 25;
        const fill = $("run-progress-fill");
        const txt = $("run-progress-text");
        if (fill) fill.style.width = `${Math.max(8, pct)}%`;
        if (txt) txt.textContent = pr.message || `생성 진행 중 (${pr.done || 0}/${pr.total || "?"})`;
      }
    }
  }

  // ── 5. 2열 검색형 모델 팝오버 (독립 스크롤 · 3방향 리사이즈 · 크기 영속 저장) ──
  const MP_DEF_W = 640, MP_DEF_H = 490;
  const MP_MIN_W = 480, MP_MIN_H = 320;

  function clampModelPopoverSize(w, h) {
    const maxW = Math.max(MP_MIN_W, Math.min(960, Math.floor(window.innerWidth * 0.9)));
    const maxH = Math.max(MP_MIN_H, Math.min(760, Math.floor(window.innerHeight * 0.82)));
    return {
      w: Math.round(Math.max(MP_MIN_W, Math.min(maxW, Number(w) || MP_DEF_W))),
      h: Math.round(Math.max(MP_MIN_H, Math.min(maxH, Number(h) || MP_DEF_H))),
    };
  }

  function applyModelPopoverBox(pop, anchorEl) {
    const saved = (Store.state.prefs && Store.state.prefs.modelPopoverSize) || {};
    const sz = clampModelPopoverSize(saved.w ?? MP_DEF_W, saved.h ?? MP_DEF_H);
    pop.style.width = `${sz.w}px`;
    pop.style.height = `${sz.h}px`;

    const rect = anchorEl ? anchorEl.getBoundingClientRect() : null;
    let left = rect ? rect.right - sz.w : window.innerWidth - sz.w - 16;
    if (left < 12) left = 12;
    if (left + sz.w > window.innerWidth - 12) left = Math.max(12, window.innerWidth - sz.w - 12);

    let top = rect ? rect.bottom + 8 : 64;
    if (top + sz.h > window.innerHeight - 14) {
      const above = rect ? rect.top - sz.h - 8 : 54;
      top = above >= 54 ? above : Math.max(54, window.innerHeight - sz.h - 14);
    }
    if (top < 54) top = 54;
    pop.style.left = `${Math.round(left)}px`;
    pop.style.top = `${Math.round(top)}px`;
  }

  function openModelPopover(anchorEl) {
    const pop = $("model-popover");
    if (!pop) return;
    if (!pop.classList.contains("hidden") && popoverAnchor === anchorEl) {
      pop.classList.add("hidden");
      return;
    }
    popoverAnchor = anchorEl;
    pop.classList.remove("hidden");

    const search = $("mp-search");
    if (search) search.value = "";
    renderModelPopoverList("");
    applyModelPopoverBox(pop, anchorEl);
    requestAnimationFrame(() => applyModelPopoverBox(pop, anchorEl));
    if (search) search.focus();
  }

  function setupModelPopoverResize() {
    const pop = $("model-popover");
    if (!pop) return;
    const handles = [
      { id: "mp-resize-sw", dx: -1, dy: 1 },
      { id: "mp-resize-s", dx: 0, dy: 1 },
      { id: "mp-resize-se", dx: 1, dy: 1 },
    ];
    let activeDrag = null;

    const onMove = (e) => {
      if (!activeDrag) return;
      const { h, sx, sy, left, right, top, w, height } = activeDrag;
      const deltaX = e.clientX - sx;
      const deltaY = e.clientY - sy;
      const rawW = h.dx === 0 ? w : w + h.dx * deltaX;
      const rawH = height + h.dy * deltaY;
      const sz = clampModelPopoverSize(rawW, rawH);

      let nextLeft = left;
      if (h.dx < 0) {
        nextLeft = Math.max(12, right - sz.w);
        sz.w = Math.round(Math.max(MP_MIN_W, right - nextLeft));
      } else if (nextLeft + sz.w > window.innerWidth - 12) {
        nextLeft = Math.max(12, window.innerWidth - 12 - sz.w);
        sz.w = Math.round(Math.min(sz.w, window.innerWidth - 24));
      }

      let nextTop = top;
      if (nextTop + sz.h > window.innerHeight - 14) {
        nextTop = Math.max(54, window.innerHeight - 14 - sz.h);
        sz.h = Math.round(Math.min(sz.h, window.innerHeight - 68));
      }

      activeDrag.cur = sz;
      pop.style.left = `${Math.round(nextLeft)}px`;
      pop.style.top = `${Math.round(nextTop)}px`;
      pop.style.width = `${sz.w}px`;
      pop.style.height = `${sz.h}px`;
    };

    const onUp = () => {
      if (!activeDrag) return;
      const fin = activeDrag.cur;
      activeDrag = null;
      pop.classList.remove("resizing");
      if (fin) Store.savePrefs({ modelPopoverSize: { w: fin.w, h: fin.h } });
    };

    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onUp);

    for (const h of handles) {
      const el = $(h.id);
      if (!el) continue;
      el.addEventListener("pointerdown", (e) => {
        if (e.button !== 0) return;
        e.preventDefault();
        e.stopPropagation();
        const r = pop.getBoundingClientRect();
        activeDrag = { h, sx: e.clientX, sy: e.clientY, left: r.left, right: r.right, top: r.top, w: r.width, height: r.height };
        pop.classList.add("resizing");
        try { el.setPointerCapture(e.pointerId); } catch (_) {}
      });
      el.addEventListener("dblclick", (e) => {
        e.stopPropagation();
        Store.savePrefs({ modelPopoverSize: { w: MP_DEF_W, h: MP_DEF_H } });
        applyModelPopoverBox(pop, popoverAnchor);
        Store.toast("모델 선택 창 크기를 기본값으로 복원했습니다.");
      });
    }
    window.addEventListener("resize", () => {
      if (!pop.classList.contains("hidden")) applyModelPopoverBox(pop, popoverAnchor);
    });
  }

  function renderModelPopoverList(query) {
    const q = (query || "").trim().toLowerCase();
    const listEl = $("mp-list");
    if (!listEl) return;
    const catalog = Data.modelCatalog();
    const patch = Store.primaryPatch();
    const curName = patch ? patch.effectiveModel : Store.projectPrepare().model;

    const filtered = catalog.filter((m) =>
      !q
      || m.label.toLowerCase().includes(q)
      || m.name.toLowerCase().includes(q)
      || (m.badge || "").toLowerCase().includes(q)
      || (m.guide && m.guide.when.toLowerCase().includes(q))
    );

    const cntEl = $("mp-count");
    if (cntEl) {
      cntEl.textContent = q ? `${filtered.length}/${catalog.length}` : `${catalog.length}종`;
    }

    listEl.innerHTML = "";
    for (const m of filtered) {
      const btn = Data.el("button", {
        type: "button",
        className: "mp-item" + (m.name === curName ? " selected" : ""),
        onMouseenter: () => renderModelGuide(m),
        onClick: async () => {
          $("model-popover").classList.add("hidden");
          await applyModelSelection(m.name);
        },
      },
        Data.el("div", {},
          Data.el("span", { className: "mp-item-title", text: m.label }),
          Data.el("span", { className: "mp-item-sub", text: `${m.family} · 최대 참조 ${m.max_inputs}장` }),
        ),
        Data.el("span", { className: "mp-badge", text: m.badge }),
      );
      listEl.append(btn);
    }

    renderModelGuide(filtered.find((m) => m.name === curName) || filtered[0] || catalog[0]);
  }

  function renderModelGuide(m) {
    const box = $("mp-guide");
    if (!box || !m) return;
    const g = m.guide || {};
    box.innerHTML = "";
    box.append(
      Data.el("h3", { text: m.label }),
      Data.el("div", { className: "mp-guide-tags" },
        Data.el("span", { className: "mp-guide-tag", text: m.family || m.kind }),
        Data.el("span", { className: "mp-guide-tag", text: m.badge }),
        Data.el("span", { className: "mp-guide-tag", text: `최대 참조 ${m.max_inputs}장` }),
        Data.el("span", { className: "mp-guide-tag", text: `최대 ${m.max_n}장/회` }),
      ),
      Data.el("div", { className: "mp-guide-sec" },
        Data.el("h4", { text: "어떤 작업에 고를까요?" }),
        Data.el("p", { text: g.when || m.summary || "" }),
      ),
      Data.el("div", { className: "mp-guide-sec" },
        Data.el("h4", { text: "강점" }),
        Data.el("p", { text: g.strengths || (m.strengths || []).join(" · ") }),
      ),
      Data.el("div", { className: "mp-guide-sec" },
        Data.el("h4", { text: "참고 사항" }),
        Data.el("p", { text: g.caution || "목적에 맞는 레퍼런스를 연결하면 일관성이 높아집니다." }),
      ),
      Data.el("div", { className: "mp-guide-sec" },
        Data.el("h4", { text: "실무 팁" }),
        Data.el("p", { text: g.tip || "후보 비교 탭에서 4장 시안을 나란히 확인해 보세요." }),
      ),
    );
  }

  async function applyModelSelection(modelName) {
    const selList = Store.selectedPatches();
    const m = Data.findModel(modelName);
    const canPerCrop = Api.can("v2_crop_model");
    const useProjectDefault = !canPerCrop || (selList.length <= 1 && (!selList[0] || !selList[0].meta.modelOverride));

    if (useProjectDefault) {
      try {
        const nextVals = Store.buildPreparePayload({
          model: modelName,
          quality: (m && m.default_quality) || Store.projectPrepare().quality,
        });
        await Api.savePipelineSettings(Store.state.root, Store.state.P?.rev ?? 0, "prepare", nextVals);
        for (const p of selList) {
          p.meta.modelOverride = "";
          p.meta.quality = "";
          p.meta.opts = {};
        }
        Store.toast(`프로젝트 기본 모델을 ${m ? m.shortName : modelName}(으)로 변경했습니다.`, "ok");
        await Store.refreshAll(true);
      } catch (err) {
        Store.toast(`모델 변경 실패: ${err.message}`, "err");
      }
    } else {
      for (const p of selList) p.meta.modelOverride = modelName;
      try {
        await Api.batchUpdateCrops(Store.state.root, Store.state.L?.rev ?? null, {
          ids: selList.map((p) => p.id), model: modelName,
        });
      } catch (_) {}
      Store.emit("selection");
      Store.toast(`${selList.length}개 영역의 모델을 ${m ? m.shortName : modelName}(으)로 설정했습니다.`, "ok");
    }
    pulseCard("#model-card");
  }

  async function applyQualitySelection(q) {
    const selList = Store.selectedPatches();
    for (const p of selList) p.meta.quality = q;
    try {
      const nextVals = Store.buildPreparePayload({ quality: q });
      await Api.savePipelineSettings(Store.state.root, Store.state.P?.rev ?? 0, "prepare", nextVals);
      await Store.refreshAll(true);
    } catch (err) {
      Store.toast(`품질 설정 저장 실패: ${err.message}`, "err");
      Store.emit("selection");
    }
  }

  async function applyExtraOpt(key, val) {
    const selList = Store.selectedPatches();
    for (const p of selList) p.meta.opts[key] = val;
    try {
      const nextVals = Store.buildPreparePayload({ [key]: val });
      await Api.savePipelineSettings(Store.state.root, Store.state.P?.rev ?? 0, "prepare", nextVals);
      await Store.refreshAll(true);
    } catch (err) {
      Store.toast(`옵션 저장 실패: ${err.message}`, "err");
      Store.emit("selection");
    }
  }

  function insertMention(patch, tag) {
    const cur = patch.meta.user_instruction_ko || "";
    const sep = cur && !cur.endsWith(" ") ? " " : "";
    patch.meta.user_instruction_ko = `${cur}${sep}${tag} `;
    patch.meta.dirtySpec = true;
    Store.emit("selection");
    const el = $("inp-instruction");
    if (el) el.focus();
  }

  // ── 6. AI 스마트 프롬프트 작성 (계약 §3.4 · 최대 8패치 직렬 · 확인 창 없이 즉시 호출 D521 · 429/5xx/timeout 즉시 중단 D520) ──
  async function runAssistForPatches(patches, force = true) {
    const list = (patches || []).slice(0, 8);
    if (!list.length) {
      Store.toast("프롬프트를 작성할 영역을 먼저 선택해 주세요.");
      return { ok: false, count: 0, aborted: false };
    }
    if ((patches || []).length > 8) {
      Store.toast("한 번에 최대 8개 영역까지만 AI 프롬프트를 작성합니다. 앞 8개부터 진행합니다.");
    }
    if (!Api.can("v2_forge_prompt")) {
      Store.toast("Phase V2-A 백엔드 브릿지(prompt/assist) 연결 대기 중입니다.", "err");
      return { ok: false, count: 0, aborted: true };
    }
    Store.state.assistBusy = true;
    Store.emit("selection");
    let written = 0;
    let aborted = false;
    try {
      for (const p of list) {
        // §3.4: 먼저 POST /spec 으로 최신 한국어 지시·목적 저장
        let saved;
        try {
          saved = await Store.savePatchSpec(p);
        } catch (err) {
          Store.toast(`${p.name}: ${err.message}`, "err");
          if (err && (err.status === 429 || err.status >= 500 || err.data?.reason === "apply_needed")) {
            aborted = true;
            break;
          }
          continue;
        }
        if (saved && saved.unsupported) {
          Store.toast(saved.reason || "지시문 저장이 아직 연결되지 않았습니다.", "err");
          return { ok: false, count: written, aborted: true };
        }
        let res;
        try {
          res = await Api.assistPrompt(Store.state.root, {
            crop_id: p.id,
            seen: p.meta.seen,
            crop_sig: p.meta.crop_sig,
            force: !!force,
          });
        } catch (err) {
          const reason = err && err.data && err.data.reason;
          if (err && err.status === 409 && reason === "stale") {
            await Store.loadSpec(p.id, true);
            Store.toast(`${p.name}: 다른 곳에서 변경되어 최신 상태로 다시 읽었습니다 — 다시 눌러 주세요.`, "err");
          } else {
            Store.toast(`${p.name}: ${err.message}`, "err");
          }
          // D520 / §7.3 C1: 429·5xx·timeout·busy·no_backend 이면 남은 패치 즉시 중단
          if (err && (err.isTimeout || err.status === 429 || err.status >= 500
              || ["busy", "rate_limited", "no_backend", "forge_auth", "no_access", "forge_unreachable"].includes(reason))) {
            aborted = true;
            break;
          }
          continue;
        }
        if (res && res.ok && res.status === "written") {
          p.meta.seen = res.seen || p.meta.seen;
          p.meta.body_en = res.body_en || "";
          p.meta.prompt_en = res.prompt_en || "";
          p.meta.summary_ko = res.summary_ko || "";
          p.meta.skill_ids = Array.isArray(res.skill_ids) ? res.skill_ids : [];
          p.meta.specState = res.state || "body";
          p.meta.specWhy = null;
          p.meta.promptSource = "assist";
          p.meta.promptInputHash = res.input_hash || p.meta.specInputHash;
          p.meta.specInputHash = res.input_hash || p.meta.specInputHash;
          p.meta.cached = !!res.cached;
          p.meta.forgeCalls = Number(res.forge_calls || 0);
          written += 1;
        } else if (res && res.status === "unsupported") {
          Store.toast(`${p.name}: 이 모델은 AI 프롬프트 작성을 지원하지 않습니다 — 원문 프롬프트에 직접 입력해 주세요.`, "err");
        } else {
          const detail = (res && (res.error || res.reason)) || "프롬프트 작성 실패";
          Store.toast(`${p.name}: ${detail}`, "err");
          if (res && res.reason === "timeout") {
            aborted = true;
            break;
          }
        }
      }
      if (written > 0) {
        pulseCard("#prompt-card");
        Store.toast(`${written}개 영역의 AI 스마트 프롬프트를 작성했습니다.`, "ok");
      }
      return { ok: written > 0 && !aborted, count: written, aborted };
    } finally {
      Store.state.assistBusy = false;
      Store.emit("selection");
    }
  }

  function runAssistForSelection() {
    return runAssistForPatches(Store.selectedPatches(), true);
  }

  // ── 7. 선택 영역 생성 실행 (D519 미작성 패치 자동 assist 선행 → DPSubmit.submitPaid §3.6) ──
  async function runSelectedGeneration(useSelectedOnly = false) {
    const patches = Store.getPatches();
    const targets = useSelectedOnly
      ? Store.selectedPatches()
      : patches.filter((p) => p.selectedForRun);
    if (!targets.length) {
      Store.toast("생성할 영역을 먼저 선택하거나 체크해 주세요.");
      return;
    }

    // 미저장 변경이 있는 패치는 먼저 POST /spec 저장 (직접 입력 포함) 및 스펙 미로드 패치 동기화
    if (Api.can("v2_spec_edit")) {
      for (const p of targets) {
        if (!p.meta.specLoaded && !p.meta.needsApplyForSpec) {
          await Store.loadSpec(p.id);
        }
        if ((p.meta.dirtySpec || p.meta.rawDirty) && !p.meta.needsApplyForSpec) {
          try {
            await Store.savePatchSpec(p, { includeRaw: p.meta.rawDirty });
          } catch (err) {
            Store.toast(`${p.name} 지시문 저장 실패: ${err.message}`, "err");
            return;
          }
        }
      }
    }

    // D519: 지시가 바뀐(입력 해시 불일치 또는 blocked·미작성) ai_auto 패치만 최대 8건 자동 assist 선행
    const needAssist = targets.filter((p) => {
      if (!p.meta.aiAuto || p.meta.needsApplyForSpec) return false;
      const hasInst = !!(p.meta.user_instruction_ko || "").trim();
      const bodyOnlyPurpose = ["remove", "vary", "detail"].includes(p.meta.purpose);
      if (!hasInst && !bodyOnlyPurpose) return false;
      const hashMismatch = !!(p.meta.specInputHash && p.meta.promptInputHash !== p.meta.specInputHash);
      return p.meta.dirtySpec || p.meta.specState === "blocked" || !p.meta.body_en || hashMismatch;
    });
    if (needAssist.length && Api.can("v2_forge_prompt")) {
      const ar = await runAssistForPatches(needAssist, false);
      if (ar.aborted) return;
      if (needAssist.length > 8) {
        Store.toast(`상한(8개)을 넘은 ${needAssist.length - 8}개 영역은 작성 대기 상태로 남습니다.`);
      }
    }

    // §3.6: DPSubmit.submitPaid("run", { root, only }) 는 선택된 crop id 목록을 항상 쉼표로 전달
    const only = targets.map((p) => p.id).join(",");
    try {
      const res = await Api.submitStage("run", { root: Store.state.root, only });
      if (res && res.pendingIntegration) {
        Store.toast(res.message, "accent");
        return;
      }
      if (res && res.ok) {
        Store.toast("생성을 시작했습니다.", "ok");
        await Store.refreshAll(true);
      } else if (res && res.reason === "cancelled") {
        Store.toast("생성을 취소했습니다.");
      } else if (res) {
        Store.toast(res.error || res.message || res.reason || "생성을 시작하지 못했습니다.", "err");
      }
    } catch (err) {
      Store.toast(`생성 요청 실패: ${err.message}`, "err");
    }
  }

  // ── 8. 좌/우 스플리터 & 패널 ⋮⋮ 드래그 도킹 ──
  function setupSplitter(id, cssVar, minW, maxW, invert, defaultW) {
    const sp = $(id);
    if (!sp) return;
    let active = false, startX = 0, startW = defaultW;
    sp.addEventListener("pointerdown", (e) => {
      active = true;
      startX = e.clientX;
      startW = parseInt(getComputedStyle(document.documentElement).getPropertyValue(cssVar), 10) || defaultW;
      sp.classList.add("dragging");
      sp.setPointerCapture(e.pointerId);
    });
    sp.addEventListener("pointermove", (e) => {
      if (!active) return;
      const dx = invert ? startX - e.clientX : e.clientX - startX;
      const next = Math.max(minW, Math.min(maxW, startW + dx));
      document.documentElement.style.setProperty(cssVar, `${next}px`);
    });
    sp.addEventListener("pointerup", () => {
      active = false;
      sp.classList.remove("dragging");
    });
    sp.addEventListener("dblclick", () => {
      document.documentElement.style.setProperty(cssVar, `${defaultW}px`);
    });
  }

  function setupDockDrag() {
    let draggedDockId = null;
    document.querySelectorAll("[data-drag-panel]").forEach((bar) => {
      bar.addEventListener("dragstart", (e) => {
        draggedDockId = bar.dataset.dragPanel;
        e.dataTransfer.effectAllowed = "move";
      });
      bar.addEventListener("dragend", () => {
        draggedDockId = null;
        document.querySelectorAll(".dock-drop-highlight").forEach((el) => el.classList.remove("dock-drop-highlight"));
      });
    });
    document.querySelectorAll("[data-panel-id]").forEach((dock) => {
      dock.addEventListener("dragover", (e) => {
        if (draggedDockId && dock.dataset.panelId !== draggedDockId) {
          e.preventDefault();
          dock.classList.add("dock-drop-highlight");
        }
      });
      dock.addEventListener("dragleave", () => dock.classList.remove("dock-drop-highlight"));
      dock.addEventListener("drop", (e) => {
        dock.classList.remove("dock-drop-highlight");
        if (!draggedDockId || dock.dataset.panelId === draggedDockId) return;
        e.preventDefault();
        const nextPreset = draggedDockId === "layers"
          ? (Store.state.prefs.layoutPreset === "right-stack" ? "standard" : "right-stack")
          : (Store.state.prefs.layoutPreset === "left-stack" ? "standard" : "left-stack");
        Store.savePrefs({ layoutPreset: nextPreset });
        const lp = Data.LAYOUT_PRESETS.find((x) => x.id === nextPreset);
        Store.toast(`패널 배치: ${lp ? lp.name : nextPreset}`);
      });
    });
    document.querySelectorAll("[data-dock-action]").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        const nextPreset = btn.dataset.dockAction === "move-right"
          ? (Store.state.prefs.layoutPreset === "right-stack" ? "standard" : "right-stack")
          : (Store.state.prefs.layoutPreset === "left-stack" ? "standard" : "left-stack");
        Store.savePrefs({ layoutPreset: nextPreset });
        const lp = Data.LAYOUT_PRESETS.find((x) => x.id === nextPreset);
        Store.toast(`패널 배치: ${lp ? lp.name : nextPreset}`);
      });
    });
  }

  function bindEvents() {
    setupSplitter("splitter-left", "--left-w", 248, 420, false, 294);
    setupSplitter("splitter-right", "--right-w", 300, 500, true, 368);
    setupDockDrag();
    setupModelPopoverResize();

    // 좌측 도크 버튼
    const btnDesel = $("btn-deselect-patch");
    if (btnDesel) btnDesel.addEventListener("click", () => Store.clearSelection());
    const btnAddRect = $("btn-add-rect");
    if (btnAddRect) btnAddRect.addEventListener("click", () => {
      Canvas.setTool("rect");
      Store.toast("캔버스에서 수정할 부위를 드래그해 영역을 추가하세요.");
    });
    const chkAll = $("chk-select-all");
    if (chkAll) chkAll.addEventListener("change", (e) => Store.setAllCheckedForRun(e.target.checked));

    const btnRefUp = $("btn-ref-upload");
    if (btnRefUp) btnRefUp.addEventListener("click", () => $("hidden-file-ref").click());
    const btnRefForge = $("btn-ref-forge");
    if (btnRefForge) btnRefForge.addEventListener("click", () => window.DPV2Overlays && window.DPV2Overlays.openForgeImportForRef());
    const btnSheet = $("btn-open-sheet-cut");
    if (btnSheet) btnSheet.addEventListener("click", () => window.DPV2Overlays && window.DPV2Overlays.openSheetOverlay());

    // 우측 탭 전환
    document.querySelectorAll(".itab").forEach((b) => b.addEventListener("click", () => setActiveTab(b.dataset.tab)));
    const btnEmptyRect = $("btn-empty-rect");
    if (btnEmptyRect) btnEmptyRect.addEventListener("click", () => Canvas.setTool("rect"));
    const btnCmpGoto = $("btn-compare-goto-instruct");
    if (btnCmpGoto) btnCmpGoto.addEventListener("click", () => setActiveTab("instruct"));
    const btnRtGoto = $("btn-retouch-goto-compare");
    if (btnRtGoto) btnRtGoto.addEventListener("click", () => setActiveTab("compare"));

    // 다중 선택 일괄 액션
    const btnClearMulti = $("btn-clear-multi");
    if (btnClearMulti) btnClearMulti.addEventListener("click", () => {
      const p = Store.primaryPatch();
      if (p) Store.selectPatch(p.id);
    });
    const btnMultiVis = $("btn-multi-vis");
    if (btnMultiVis) btnMultiVis.addEventListener("click", () => {
      const list = Store.selectedPatches();
      const nextHidden = list.every((p) => !p.meta.hidden);
      list.forEach((p) => { p.meta.hidden = nextHidden; });
      Store.emit("selection");
    });
    const btnMultiLock = $("btn-multi-lock");
    if (btnMultiLock) btnMultiLock.addEventListener("click", async () => {
      const list = Store.selectedPatches();
      const nextLock = !list.every((p) => p.locked);
      for (const p of list) await Store.updateCrop(p.id, { locked: nextLock });
      Store.toast(`${list.length}개 영역을 일괄 ${nextLock ? "잠금" : "잠금 해제"}했습니다.`);
    });
    const btnMultiDel = $("btn-multi-del");
    if (btnMultiDel) btnMultiDel.addEventListener("click", async () => {
      const list = Store.selectedPatches();
      if (!list.length) return;
      const ok = await Data.askDialogConfirm(`선택된 ${list.length}개 수정 영역을 모두 삭제할까요?`, { ok: "삭제", cancel: "취소", danger: true });
      if (!ok) return;
      for (const p of list) await Store.deleteCrop(p.id);
    });

    // TAB 1 바인딩
    const nameInp = $("inp-patch-name");
    if (nameInp) nameInp.addEventListener("change", (e) => {
      const p = Store.primaryPatch();
      const val = e.target.value.trim();
      if (p && val && val !== p.name) Store.updateCrop(p.id, { name: val }, `이름 변경 (${val})`);
    });

    const slotCut = $("btn-slot-cut");
    if (slotCut) slotCut.addEventListener("click", () => window.DPV2Overlays && window.DPV2Overlays.openSheetOverlay());
    const slotForge = $("btn-slot-forge");
    if (slotForge) slotForge.addEventListener("click", () => window.DPV2Overlays && window.DPV2Overlays.openForgeImportForRef());

    const refDropSlot = $("ref-drop-slot");
    if (refDropSlot) {
      refDropSlot.addEventListener("dragover", (e) => {
        if (e.dataTransfer?.types?.includes("text/dps-ref-file")) {
          e.preventDefault();
          refDropSlot.classList.add("pulse-flash");
        }
      });
      refDropSlot.addEventListener("dragleave", () => refDropSlot.classList.remove("pulse-flash"));
      refDropSlot.addEventListener("drop", async (e) => {
        refDropSlot.classList.remove("pulse-flash");
        const refFile = e.dataTransfer?.getData("text/dps-ref-file");
        if (refFile) {
          e.preventDefault();
          await linkRefToPatches(refFile, Store.selectedPatches());
        }
      });
    }

    const chkAuto = $("chk-ai-auto");
    if (chkAuto) chkAuto.addEventListener("change", (e) => {
      for (const p of Store.selectedPatches()) { p.meta.aiAuto = e.target.checked; p.meta.dirtySpec = true; }
      const p = Store.primaryPatch();
      if (p && Api.can("v2_spec_edit")) {
        Store.savePatchSpec(p).catch((err) => Store.toast(`지시문 저장 실패: ${err.message}`, "err"));
      }
    });

    const instEl = $("inp-instruction");
    if (instEl) instEl.addEventListener("input", (e) => {
      const p = Store.primaryPatch();
      if (!p) return;
      p.meta.user_instruction_ko = e.target.value;
      p.meta.dirtySpec = true;
      const fb = $("fb-instruction");
      if (fb && document.activeElement !== fb) fb.value = e.target.value;
    });

    const rawEl = $("inp-raw-prompt");
    if (rawEl) rawEl.addEventListener("input", (e) => {
      const p = Store.primaryPatch();
      if (!p) return;
      p.meta.body_en = e.target.value;
      p.meta.rawDirty = true;
    });

    const btnSaveSpec = $("btn-save-spec");
    if (btnSaveSpec) btnSaveSpec.addEventListener("click", async () => {
      const p = Store.primaryPatch();
      if (!p) return;
      try {
        const res = await Store.savePatchSpec(p, { includeRaw: p.meta.rawDirty });
        if (res && res.unsupported) Store.toast(res.reason, "err");
        else if (res && res.ok) Store.toast("지시문을 저장했습니다.", "ok");
      } catch (err) {
        Store.toast(`지시문 저장 실패: ${err.message}`, "err");
      }
    });

    const btnAiDraft = $("btn-ai-draft");
    if (btnAiDraft) btnAiDraft.addEventListener("click", () => runAssistForSelection());

    const btnPresets = $("btn-prompt-presets");
    if (btnPresets) btnPresets.addEventListener("click", () => window.DPV2Overlays && window.DPV2Overlays.openPresetsModal());

    const chkInherit = $("chk-inherit-model");
    if (chkInherit) chkInherit.addEventListener("change", (e) => {
      for (const p of Store.selectedPatches()) {
        p.meta.modelOverride = e.target.checked ? "" : p.effectiveModel;
      }
      Store.emit("selection");
    });

    const btnModel = $("btn-model-dropdown");
    if (btnModel) btnModel.addEventListener("click", (e) => openModelPopover(e.currentTarget));
    const mpSearch = $("mp-search");
    if (mpSearch) mpSearch.addEventListener("input", (e) => renderModelPopoverList(e.target.value));

    document.querySelectorAll("#seg-count button").forEach((b) => {
      b.addEventListener("click", async () => {
        const val = Number(b.dataset.val);
        for (const p of Store.selectedPatches()) p.meta.count = val;
        try {
          const nextVals = Store.buildPreparePayload({ n: val });
          await Api.savePipelineSettings(Store.state.root, Store.state.P?.rev ?? 0, "prepare", nextVals);
          await Store.refreshAll(true);
        } catch (err) {
          Store.toast(`후보 장수 저장 실패: ${err.message}`, "err");
          Store.emit("selection");
        }
      });
    });

    const chkAutoCrop = $("adv-show-autocrop");
    if (chkAutoCrop) chkAutoCrop.addEventListener("change", (e) => Store.savePrefs({ showAutoCrop: e.target.checked }));
    const advComp = $("adv-complexity");
    if (advComp) advComp.addEventListener("change", async (e) => {
      try {
        const nextVals = Store.buildPreparePayload({ complexity: e.target.value });
        await Api.savePipelineSettings(Store.state.root, Store.state.P?.rev ?? 0, "prepare", nextVals);
        await Store.refreshAll(true);
      } catch (err) {
        Store.toast(`복잡도 저장 실패: ${err.message}`, "err");
      }
    });

    // TAB 2 비교 버튼
    const btnFullCmp = $("btn-open-fullscreen-compare");
    if (btnFullCmp) btnFullCmp.addEventListener("click", () => window.DPV2Overlays && window.DPV2Overlays.openCompareOverlay());
    const btnTlFix = $("btn-tl-fix");
    if (btnTlFix) btnTlFix.addEventListener("click", () => {
      setActiveTab("retouch");
      const p = Store.primaryPatch();
      if (p) {
        p.meta.filters.edgeEnabled = true;
        p.meta.filters.feather = Math.max(18, p.meta.filters.feather);
        Store.emit("filters");
      }
    });
    const btnReroll = $("btn-reroll-more");
    if (btnReroll) btnReroll.addEventListener("click", async () => {
      const p = Store.primaryPatch();
      if (!p || !p.target) return;
      try {
        const res = await Api.submitStage("reroll", { root: Store.state.root, crop_id: p.id, tid: p.target.tid });
        if (res && res.pendingIntegration) Store.toast(res.message, "accent");
        else if (res && res.ok) { Store.toast("추가 후보 생성을 시작했습니다.", "ok"); await Store.refreshAll(true); }
      } catch (err) {
        Store.toast(`재생성 실패: ${err.message}`, "err");
      }
    });
    const btnGotoRt = $("btn-goto-retouch");
    if (btnGotoRt) btnGotoRt.addEventListener("click", () => setActiveTab("retouch"));

    // TAB 3 후보정 스마트 필터 바인딩
    document.querySelectorAll("[data-toggle-filter]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const p = Store.primaryPatch();
        if (!p) return;
        const map = { brush: "brushEnabled", edge: "edgeEnabled", transform: "transformEnabled", color: "colorEnabled" };
        const k = map[btn.dataset.toggleFilter];
        p.meta.filters[k] = !p.meta.filters[k];
        Store.emit("filters");
        renderInspector();
      });
    });

    document.querySelectorAll("[data-rt-tool]").forEach((b) => {
      b.addEventListener("click", () => Canvas.setTool(b.dataset.rtTool));
    });
    const btnResetMask = $("btn-rt-reset-mask");
    if (btnResetMask) btnResetMask.addEventListener("click", () => {
      const ms = Store.state.mask;
      if (!ms) return;
      ms.m.set(ms.base);
      ms.dirty = true;
      Store.emit("mask");
      Store.toast("브러시 마스크를 초기 상태로 되돌렸습니다.");
    });
    const btnSaveMask = $("btn-rt-save-mask");
    if (btnSaveMask) btnSaveMask.addEventListener("click", () => Store.saveMaskSession());

    const rtBrushSz = $("rt-brush-size");
    if (rtBrushSz) rtBrushSz.addEventListener("input", (e) => {
      const v = Number(e.target.value);
      $("rt-brush-size-val").textContent = `${v}px`;
      if (Store.state.mask) Store.state.mask.brushSize = v;
      const hud = $("hud-brush-size");
      if (hud) { hud.value = String(v); $("hud-brush-size-val").textContent = `${v}px`; }
    });

    const bindFilter = (id, key, suffix = "") => {
      const el = $(id);
      if (!el) return;
      el.addEventListener("input", (e) => {
        const p = Store.primaryPatch();
        if (!p) return;
        p.meta.filters[key] = Number(e.target.value);
        const valEl = $(`${id}-val`);
        if (valEl) {
          const n = p.meta.filters[key];
          valEl.textContent = (key === "bright" || key === "sat") && n > 0 ? `+${n}${suffix}` : `${n}${suffix}`;
        }
        if (key === "feather") { const m = $("fm-edge"); if (m) m.textContent = `${p.meta.filters[key]}px`; }
        if (key === "opacity") { const m = $("fm-color"); if (m) m.textContent = `${p.meta.filters[key]}%`; }
        Store.emit("filters");
      });
    };
    bindFilter("rt-feather", "feather", "px");
    bindFilter("rt-fade-top", "fadeTop");
    bindFilter("rt-fade-bottom", "fadeBottom");
    bindFilter("rt-fade-left", "fadeLeft");
    bindFilter("rt-fade-right", "fadeRight");
    bindFilter("rt-dx", "dx");
    bindFilter("rt-dy", "dy");
    bindFilter("rt-scale", "scale");
    bindFilter("rt-rot", "rot");
    bindFilter("rt-opacity", "opacity", "%");
    bindFilter("rt-bright", "bright");
    bindFilter("rt-sat", "sat");

    const btnResetTf = $("btn-rt-reset-tf");
    if (btnResetTf) btnResetTf.addEventListener("click", () => {
      const p = Store.primaryPatch();
      if (!p) return;
      p.meta.filters.dx = 0; p.meta.filters.dy = 0; p.meta.filters.scale = 100; p.meta.filters.rot = 0;
      Store.emit("filters");
      renderInspector();
    });
    const btnApplyTf = $("btn-rt-apply-tf");
    if (btnApplyTf) btnApplyTf.addEventListener("click", async () => {
      const p = Store.primaryPatch();
      const cand = Store.currentCandidate(p);
      if (!p || !cand) return;
      const f = p.meta.filters;
      try {
        let seen = "";
        if (Store.state.mask && Store.state.mask.key === cand.key && Store.state.mask.adjust) {
          seen = Store.state.mask.adjust.at || "";
        } else {
          const info = await Api.getMask(Store.state.root, cand.key);
          seen = (info && info.adjust && info.adjust.at) || "";
        }
        const scaleVal = f.transformEnabled ? Math.max(0.5, Math.min(2.0, (f.scale || 100) / 100)) : 1.0;
        const res = await Api.adjustMask(Store.state.root, {
          key: cand.key,
          op: "set",
          seen,
          blur: f.edgeEnabled ? Math.max(0, Math.min(24, Math.round(f.feather || 0))) : 0,
          invert: false,
          dx: f.transformEnabled ? Number(f.dx || 0) : 0,
          dy: f.transformEnabled ? Number(f.dy || 0) : 0,
          rot: f.transformEnabled ? Number(f.rot || 0) : 0,
          sx: scaleVal,
          sy: scaleVal,
          flip_h: false,
          flip_v: false,
        });
        if (Store.state.mask && Store.state.mask.key === cand.key && res && res.adjust) {
          Store.state.mask.adjust = res.adjust;
        }
        Store.toast("후보정 변형을 서버에 적용했습니다.", "ok");
        await Store.refreshAll(true);
      } catch (err) {
        Store.toast(`변형 적용 실패: ${err.message}`, "err");
      }
    });

    // 실행 도크 버튼
    const btnRun = $("btn-run-selected");
    if (btnRun) btnRun.addEventListener("click", () => runSelectedGeneration(false));
    const btnApplyFree = $("btn-apply-free");
    if (btnApplyFree) btnApplyFree.addEventListener("click", async () => {
      try {
        const res = await Api.submitStage("apply", { root: Store.state.root });
        if (res && res.pendingIntegration) {
          Store.toast(res.message, "accent");
          return;
        }
        if (res && res.ok) {
          Store.toast("무료 반영 작업을 실행 중입니다…");
          const waited = await Api.waitJob(Store.state.root, res.prompt_id);
          if (waited && waited.done && !waited.ok) {
            Store.toast("무료 반영 작업이 실패했습니다.", "err");
          } else {
            Store.toast("합성 반영을 완료했습니다.", "ok");
          }
          await Store.refreshAll(true);
        } else if (res) {
          Store.toast(res.error || res.message || res.reason || "합성 적용을 시작하지 못했습니다.", "err");
        }
      } catch (err) {
        Store.toast(`합성 적용 실패: ${err.message}`, "err");
      }
    });
    const btnCancel = $("btn-cancel-run");
    if (btnCancel) btnCancel.addEventListener("click", async () => {
      try {
        await Api.drainRun(Store.state.root);
        Store.toast("진행 중인 생성 중단을 요청했습니다.");
        await Store.refreshAll(true);
      } catch (err) {
        Store.toast(`중단 요청 실패: ${err.message}`, "err");
      }
    });

    // 팝오버 바깥 클릭 닫기
    document.addEventListener("click", (e) => {
      const pop = $("model-popover");
      if (pop && !pop.classList.contains("hidden") && !pop.contains(e.target)
          && !e.target.closest("#btn-model-dropdown") && !e.target.closest("#fb-model-btn")) {
        pop.classList.add("hidden");
      }
    });

    Store.subscribe((reason) => {
      if (reason === "state" || reason === "selection" || reason === "base-image-loaded") {
        renderPatchList();
        renderRefVault();
        renderInspector();
        renderRunDock();
      } else if (reason === "prefs") {
        renderPatchList();
        renderInspector();
      }
    });
  }

  return {
    setActiveTab,
    renderPatchList,
    renderRefVault,
    renderInspector,
    renderRunDock,
    openModelPopover,
    linkRefToPatches,
    unlinkRefFromPatch,
    runAssistForSelection,
    runSelectedGeneration,
    bindEvents,
  };
})();
