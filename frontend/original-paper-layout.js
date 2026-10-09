/* Original-paper layout editing is a manual, transactional operation. */
(function (root, factory) {
  const value = factory();
  if (typeof module === "object" && module.exports) module.exports = value;
  else root.QBOriginalLayout = value;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";
  const COLORS = ["198,80,80", "63,151,102", "62,125,196", "139,99,186", "208,137,54", "48,155,167"];
  const copy = (value) => JSON.parse(JSON.stringify(value));
  const regions = (items) => (items || []).map((r) => ({ page_idx: r.page_idx, bbox: [...r.bbox] }));
  const groupId = (q) => q.group?.id ?? q.group_id ?? null;
  const targetOf = (q) => ({ number: q.number, group_id: groupId(q), question_type: q.question_type || "unknown", regions: regions(q.regions) });
  const colorOf = (q, fallback = 0) => COLORS[Number.isInteger(q.color_index) && q.color_index >= 0 && q.color_index < 6 ? q.color_index : fallback % 6];
  function hitTest(questions, page, point) {
    const hits = [];
    questions.forEach((q) => (q.regions || []).forEach((r, index) => {
      const b = r.bbox;
      if (r.page_idx === page && point[0] >= b[0] && point[0] <= b[2] && point[1] >= b[1] && point[1] <= b[3]) {
        hits.push({ question: q, index, area: (b[2] - b[0]) * (b[3] - b[1]) });
      }
    }));
    return hits.sort((a, b) => a.area - b.area || a.question.id - b.question.id || a.index - b.index);
  }
  function nextNumber(questions, group, reserved = [], excluded = []) {
    const used = new Set(questions.filter((q) => groupId(q) === group && !excluded.includes(q.id)).map((q) => Number(q.number)));
    reserved.forEach((n) => used.add(Number(n)));
    let number = 1;
    while (used.has(number) && number <= 999) number += 1;
    return number <= 999 ? number : null;
  }
  function createDraft(kind, sources, questions, defaultGroup = null) {
    sources = kind === "add" ? [] : [...sources];
    if (!["regions", "renumber", "add", "split", "merge"].includes(kind)) throw new Error("不支持的题目操作");
    if (kind !== "add" && !sources.length) throw new Error("请先选题");
    if (kind === "merge" && (sources.length < 2 || sources.some((q) => groupId(q) !== groupId(sources[0])))) throw new Error("只能合并同一题组中的至少两道题");
    let targets;
    if (kind === "add") targets = [{ number: nextNumber(questions, defaultGroup), group_id: defaultGroup, question_type: "unknown", regions: [] }];
    else if (kind === "split") {
      const first = targetOf(sources[0]);
      targets = [first, { ...targetOf(sources[0]), number: nextNumber(questions, first.group_id, [first.number], sources.map((q) => q.id)), regions: [] }];
    } else if (kind === "merge") targets = [{ ...targetOf(sources[0]), regions: sources.flatMap((q) => regions(q.regions)) }];
    else targets = [targetOf(sources[0])];
    if (kind !== "renumber" && targets.some((t) => t.regions.length > 12)) throw new Error("一道题最多保留 12 段，请先减少片段再合题");
    return { kind, sources: sources.map((q) => ({ id: q.id, revision: q.content_revision, fingerprint: q.layout_fingerprint })), targets, active: 0 };
  }
  function resizeSplit(draft, count, questions) {
    if (draft.kind !== "split" || !Number.isInteger(count) || count < 2 || count > 12) throw new Error("拆成题数应为 2–12");
    const result = copy(draft);
    if (count < result.targets.length && result.targets.slice(count).some((t) => t.regions.length)) throw new Error("待移除的题还有片段，请先转移或移除这些片段");
    while (result.targets.length < count) {
      const first = result.targets[0];
      result.targets.push({ ...copy(first), number: nextNumber(questions, first.group_id, result.targets.map((t) => t.number), result.sources.map((s) => s.id)), regions: [] });
    }
    result.targets.length = count;
    result.active = Math.min(result.active, count - 1);
    return result;
  }
  function validate(draft, questions, pages) {
    if (!draft || !draft.targets.length) return "请先选择要做的操作";
    const seen = new Set(), excluded = new Set(draft.sources.map((s) => s.id));
    for (const target of draft.targets) {
      if (!Number.isInteger(Number(target.number)) || Number(target.number) < 1 || Number(target.number) > 999) return "题号应为 1–999 的整数";
      const key = `${target.group_id}:${Number(target.number)}`;
      if (seen.has(key)) return "同一题组内的题号不能重复";
      seen.add(key);
      if (draft.kind !== "regions" && questions.some((q) => !excluded.has(q.id) && groupId(q) === target.group_id && Number(q.number) === Number(target.number))) return "这个题组中已存在该题号，请调整题号";
      if (draft.kind === "renumber") continue;
      if (!target.regions.length || target.regions.length > 12) return "每道题应有 1–12 段范围；拆题后的空题也需要画框";
      for (const r of target.regions) {
        if (!pages.some((p) => p.page_idx === r.page_idx) || !Array.isArray(r.bbox) || r.bbox.length !== 4 || r.bbox.some((v) => !Number.isFinite(v) || v < 0 || v > 1000)
          || r.bbox[0] >= r.bbox[2] || r.bbox[1] >= r.bbox[3]) return "范围无效，请重新调整";
      }
    }
    return "";
  }
  function payload(draft, revision, requestId) {
    return { kind: draft.kind, layout_revision: revision, client_request_id: requestId, sources: copy(draft.sources),
      targets: draft.targets.map((t) => draft.kind === "regions" ? { regions: regions(t.regions) } : draft.kind === "renumber" ? { number: Number(t.number) } : { number: Number(t.number), group_id: t.group_id, question_type: t.question_type, regions: regions(t.regions) }) };
  }
  const movedEnough = (start, event) => Math.max(Math.abs(event.clientX - start.clientX), Math.abs(event.clientY - start.clientY)) >= 4;
  function sourceEvidence(snapshot, questionId) {
    const source = snapshot?.questions?.find((q) => q.id === questionId) || null;
    const publications = (snapshot?.publications || []).filter((p) => p.question_id === questionId);
    const ids = new Set(publications.map((p) => p.id));
    return { question: source, region_reads: (snapshot?.region_reads || []).filter((r) => r.question_id === questionId),
      publications, library_jobs: (snapshot?.library_jobs || []).filter((job) => ids.has(job.publication_id)) };
  }

  function createController(host) {
    const { dialog, state, $, el, button, api, toast, cropView, renderStage, renderPageTabs, goToDialogPage, pointFrom, placeBox, setCropSaving, trackCropDraft, confirmDialog } = host;
    let draft = null, baseline = "", history = [], future = [], selectedId = null, operations = [], latestOperation = null, request = null;
    let pageOnly = false, groupFilter = "", selectedPart = null, confirmOpen = false, conflict = false, lookupFailed = false, previewOpen = true;
    let manualSession = false, continuousAdd = false, saveDisposition = null;
    let generation = 0, closing = false, draftRevision = null, pendingAfterSave = null, lastFrameTap = null, lastDeleted = null;
    const kindNames = { regions: "调整范围", renumber: "修改题号", add: "补题", split: "拆题", merge: "合题" };
    const panel = $("pageLayoutPanel"), overlap = $("pageLayoutOverlap");
    $("pageStage").addEventListener("dblclick", async (event) => {
      if (!dialog.layoutWorkspace || dialog.tool === "draw" || dialog.drag || dialog.sketch || dialog.saving || lookupFailed) return;
      const node = event.target.closest?.("[data-layout-coverage-id]");
      if (!node) return;
      let id = Number(node.dataset.layoutCoverageId);
      if (id < 0 && draft?.kind === "regions") id = selectedId;
      const q = state.questions.find((item) => item.id === id);
      if (!q) return;
      event.preventDefault(); event.stopPropagation();
      await reviewQuestion(q.id);
    });
    const current = () => draft?.targets[draft.active];
    const selectedQuestion = () => state.questions.find((q) => q.id === selectedId);
    const readOnly = () => Boolean(state.paper?.archived) || Boolean(state.paper?.status && state.paper.status !== "ready");
    const groups = () => state.paper?.question_groups || [];
    const label = (q) => `${q.group?.title || groups().find((g) => g.id === groupId(q))?.title || "未分组"} · 第 ${q.number} 题`;
    const status = (q) => host.isApproved(q) ? "已核对" : q.ocr_pending ? "识读中 · 待核对" : "待核对";
    const snap = () => draft ? JSON.stringify({ kind: draft.kind, sources: draft.sources, targets: draft.targets }) : "";
    // A merge changes the visible question set even when the user keeps the
    // default combined ranges and number. Treat choosing that operation as a
    // real change so Save always reaches its impact confirmation.
    const dirty = () => draft?.kind === "merge" || snap() !== baseline;
    function snapshot() { return { layout: snap() }; }
    function sync() {
      dialog.mode = draft ? "regions" : "view";
      dialog.question = selectedQuestion() || null;
      dialog.boxes = current()?.regions || [];
      dialog.selected = selectedPart;
      dialog.history = history;
      dialog.future = future;
    }
    function markBefore(before = copy(draft)) {
      if (!draft) return;
      history.push(before); if (history.length > 50) history.shift(); future = [];
      request = null; saveDisposition = null; confirmOpen = false; conflict = false; lookupFailed = false;
      sync();
    }
    function change(action) {
      if (!draft || dialog.saving || lookupFailed) return;
      markBefore(); action(); sync(); render(); renderPageTabs(); renderStage();
    }
    function redraw() { sync(); configure(); render(); renderPageTabs(); renderStage(); }
    function configure() {
      $("pageDialog").classList.toggle("layout-editing", Boolean(draft));
      $("pageDialogSave").hidden = !draft;
      $("pageDialogSave").textContent = draft?.kind === "split" || draft?.kind === "merge" ? "预览并确认" : "保存";
      const missingRange = draft && draft.kind !== "renumber" && draft.targets.some((t) => !t.regions.length);
      $("pageDialogSave").disabled = dialog.saving || lookupFailed || Boolean(missingRange);
      $("pageDialogSave").title = missingRange ? "请先画出题目范围；删除整题请使用“删除这题”" : "保存当前修改";
      $("pageDialogSaveNext").hidden = !(draft?.kind === "add" && continuousAdd);
      $("pageDialogComplete").hidden = !manualSession || readOnly();
      $("pageDialogComplete").disabled = dialog.saving || lookupFailed || readOnly();
      $("pageDialogComplete").replaceChildren(el("span", "", "返回审核"), el("span", "kbd-hint", "Ctrl+Enter"));
      $("pageDialogComplete").title = "保存当前改动后返回审核；不会自动识读";
      $("pageDialogClose").textContent = "关闭";
      ["numberField", "cropTypeField", "groupField", "readTargetField", "pageToolControls"].forEach((id) => { $(id).hidden = true; });
      $("pageManualCut").hidden = readOnly();
      $("pageManualCut").textContent = "从这页补题";
      $("pageHistoryControls").hidden = !draft && !lastDeleted;
      $("pageUndo").disabled = !history.length || dialog.saving || lookupFailed;
      $("pageRedo").disabled = !future.length || dialog.saving || lookupFailed;
      if (draft?.kind === "renumber") {
        const control = panel.querySelector("#pageLayoutNumber"); if (control) control.disabled = dialog.saving || lookupFailed;
      }
      $("pageDialogTitle").textContent = "切题与校正";
      $("pageDialogHint").textContent = "悬停查看题框，单击选中后直接拖边角调整；拖题号标签移动片段。重叠区域会先让你选题。补题时点两角固定范围；跨栏或跨页可再加一块。双击已保存题框可回到这道题的审核卡。右键、中键或空格拖动浏览。";
      $("pageCanvasHint").textContent = draft?.kind === "renumber" ? "在左侧输入新题号并保存 · 右键拖动浏览" : draft && dialog.tool === "draw" ? "十字光标：点两角固定范围 · Esc 取消框选" : "单击题框直接调整 · 双击返回审核 · 右键拖动浏览";
      $("pageStage").classList.remove("read-only", "pan-ready");
      $("pageStage").classList.toggle("layout-drawing", Boolean(draft && dialog.tool === "draw"));
    }
    function askDirtyAction() {
      return new Promise((resolve) => {
        const prompt = el("dialog", "layout-leave-dialog");
        const canSave = !validate(draft, state.questions, state.paper.pages) && !dialog.sketch;
        prompt.append(el("strong", "", "这道题的调整还没保存"), el("p", "", canSave ? "你可以先保存再继续，也可以放弃本次改动。" : "当前范围还不完整。继续修改可恢复或重新画框；放弃修改会保留原来的题目和范围。"));
        const actions = el("div", "layout-actions");
        const finish = (choice) => { prompt.close(); prompt.remove(); resolve(choice); };
        if (canSave) actions.append(button("保存后继续", "small primary", () => finish("save")));
        actions.append(button("继续修改", canSave ? "small quiet" : "small primary", () => finish("stay")),
          button("放弃修改", "small", () => finish("discard")));
        prompt.append(actions);
        prompt.addEventListener("cancel", (event) => { event.preventDefault(); finish("stay"); }, { once: true });
        document.body.append(prompt);
        prompt.showModal();
        prompt.querySelector("button")?.focus();
      });
    }
    async function guard(afterSave = null) {
      if (dialog.saving || closing) return false;
      if (lookupFailed) { toast("保存结果尚未核实，请先重试查询，避免重复提交", "error"); return false; }
      if (!dirty()) return true;
      closing = true;
      try {
        const choice = await askDirtyAction();
        if (choice === "stay") return false;
        if (choice === "discard") return true;
        if (["split", "merge"].includes(draft?.kind)) {
          pendingAfterSave = afterSave;
          confirmOpen = true; render(); $("pageLayoutConfirm")?.scrollIntoView({ block: "nearest" });
          toast("拆题或合题需要先查看替换题目的影响，再确认保存。", "success");
          return false;
        }
        return await preview({ direct: true });
      }
      finally { closing = false; }
    }
    function clearDraft() {
      draft = null; baseline = ""; history = []; future = []; selectedPart = null;
      request = null; saveDisposition = null; continuousAdd = false; confirmOpen = false; conflict = false; lookupFailed = false; pendingAfterSave = null;
      dialog.tool = "select"; sync(); trackCropDraft();
    }
    async function selectQuestion(id, index = null) {
      if (id !== selectedId && draft && !(await guard(() => { void selectQuestion(id, index); }))) return;
      if (draft && id !== selectedId) clearDraft();
      selectedId = id; selectedPart = index; overlap.hidden = true;
      const q = selectedQuestion();
      if (q && !readOnly() && !draft) beginSelectedRegionDraft(q, index);
      if (index === null) {
        const first = q?.regions?.[0];
        if (first && first.page_idx !== dialog.page) goToDialogPage(first.page_idx);
      }
      redraw();
    }
    function beginSelectedRegionDraft(q, index = null) {
      draft = createDraft("regions", [q], state.questions);
      baseline = snap(); history = []; future = []; selectedPart = index ?? (q.regions.length ? 0 : null);
      request = null; saveDisposition = "stay"; confirmOpen = false; conflict = false; lookupFailed = false;
      continuousAdd = false; draftRevision = state.paper.layout_revision; dialog.tool = "select"; sync(); trackCropDraft();
    }
    async function begin(kind, sources = null, options = {}) {
      if (readOnly()) { toast("请先恢复资料并完成原卷处理，再调整题目范围", "error"); return; }
      if (!(await guard(() => { void begin(kind, sources, options); }))) return;
      if (dialog.drag) dialog.drag(); if (dialog.sketch) dialog.sketch.cancel();
      const selected = selectedQuestion();
      const qs = kind === "add" ? [] : sources || (selected ? [selected] : []);
      if (host.ensureSourcesReady && !(await host.ensureSourcesReady(qs.map((q) => q.id)))) return;
      const defaultGroup = options.groupId !== undefined ? options.groupId : selected ? groupId(selected) : groups()[0]?.id ?? null;
      try { draft = createDraft(kind, qs, state.questions, defaultGroup); }
      catch (error) { toast(error.message, "error"); return; }
      continuousAdd = kind === "add" && options.continuous !== false;
      saveDisposition = null;
      if (kind === "add") {
        if (options.number !== undefined) draft.targets[0].number = options.number;
        if (options.questionType) draft.targets[0].question_type = options.questionType;
      }
      baseline = snap(); history = []; future = []; request = null; conflict = false; lookupFailed = false; confirmOpen = false;
      draftRevision = state.paper.layout_revision;
      if (qs[0]) selectedId = qs[0].id;
      selectedPart = kind === "regions" && draft.targets[0].regions.length ? 0 : null;
      if (Number.isInteger(options.page) && state.paper.pages.some((page) => page.page_idx === options.page)) dialog.page = options.page;
      dialog.tool = continuousAdd ? "draw" : "select"; sync(); trackCropDraft(); redraw();
      if (kind === "renumber") { const input = panel.querySelector("#pageLayoutNumber"); input?.focus(); input?.select?.(); }
    }
    function restore(redo) {
      if (!draft || dialog.saving || lookupFailed) return;
      if (dialog.sketch) { dialog.sketch.cancel(); return; } if (dialog.drag) dialog.drag();
      const from = redo ? future : history, to = redo ? history : future;
      if (!from.length) return;
      to.push(copy(draft)); draft = from.pop(); selectedPart = null; request = null; confirmOpen = false; conflict = false;
      redraw();
    }
    function remove(index) {
      if (dialog.saving || lookupFailed || draft?.kind === "renumber" || !current()?.regions[index]) return;
      if (dialog.sketch) dialog.sketch.cancel(); if (dialog.drag) dialog.drag();
      change(() => { current().regions.splice(index, 1); selectedPart = null; dialog.tool = "select"; });
    }
    function toggleDrawing() {
      if (dialog.saving || lookupFailed) return;
      const wasDrawing = dialog.tool === "draw";
      if (dialog.sketch) dialog.sketch.cancel();
      dialog.tool = wasDrawing ? "select" : "draw";
      lastFrameTap = null; configure(); render();
    }
    async function deleteSelectedQuestion() {
      const q = selectedQuestion();
      if (!q || !host.deleteQuestion || dialog.saving || lookupFailed || readOnly() || draft?.kind !== "regions") return;
      const reason = host.questionDeleteBlockReason?.(q);
      if (reason) { toast(reason, "error"); return; }
      if (dialog.sketch) dialog.sketch.cancel(); if (dialog.drag) dialog.drag();
      const token = generation, paperId = state.paperId;
      setCropSaving(true);
      try {
        const deleted = await host.deleteQuestion(q);
        if (token !== generation || paperId !== state.paperId || !deleted) return;
        lastDeleted = deleted.undo_batch?.id ? { batchId: deleted.undo_batch.id, questionId: q.id, number: q.number } : null;
        clearDraft(); selectedId = null; redraw(); void loadHistory();
      } catch (error) {
        if (token === generation) toast(`删除失败，当前修改仍保留：${error.message}`, "error");
      } finally {
        if (token === generation) { setCropSaving(false); configure(); }
      }
    }
    async function restoreLastDeletedQuestion() {
      if (!lastDeleted || !host.restoreDeletedBatch || dialog.saving || lookupFailed || draft) return;
      const token = generation, paperId = state.paperId, removed = lastDeleted;
      setCropSaving(true);
      try {
        if (!(await host.restoreDeletedBatch(paperId, removed.batchId)) || token !== generation || paperId !== state.paperId) return;
        lastDeleted = null;
        const q = state.questions.find((item) => item.id === removed.questionId);
        selectedId = q?.id ?? null;
        if (q && !draft && !readOnly()) beginSelectedRegionDraft(q, 0);
        redraw(); void loadHistory();
      } catch (error) {
        if (token === generation) toast(`恢复失败，可以重试：${error.message}`, "error");
      } finally {
        if (token === generation) { setCropSaving(false); configure(); }
      }
    }
    function refresh() {
      if (!dialog.layoutWorkspace) return;
      if (lastDeleted && state.questions.some((q) => q.id === lastDeleted.questionId)) lastDeleted = null;
      redraw(); void loadHistory();
    }
    function close() {
      generation += 1; clearDraft(); operations = []; latestOperation = null; overlap.hidden = true;
      lastDeleted = null;
      panel.hidden = true; panel.replaceChildren(); $("pageDialog").classList.remove("layout-workspace", "layout-editing");
      dialog.layoutWorkspace = false;
    }
    function open(mode, q, options = {}) {
      generation += 1; dialog.layoutWorkspace = true; selectedId = q?.id ?? null;
      lastDeleted = null;
      draft = null; baseline = ""; history = []; future = []; request = null; operations = []; latestOperation = null; selectedPart = null;
      confirmOpen = false; conflict = false; lookupFailed = false; pageOnly = false; groupFilter = ""; manualSession = true; continuousAdd = false; saveDisposition = null;
      if (mode === "regions" && q && !readOnly()) { draft = createDraft("regions", [q], state.questions); baseline = snap(); selectedPart = Number.isInteger(options.focusRegionIndex) ? options.focusRegionIndex : 0; }
      draftRevision = state.paper.layout_revision;
      dialog.tool = "select"; dialog.scrolled = true;
      dialog.layoutFocus = q?.id != null ? { questionId: q.id, index: Number.isInteger(options.focusRegionIndex) ? options.focusRegionIndex : 0 } : null;
      dialog.layoutCenterMargins = false;
      panel.hidden = false; $("pageDialog").classList.add("layout-workspace");
      sync(); configure(); render(); void loadHistory();
      if (!q && (options.autoAdd || !state.questions.length) && !readOnly()) void begin("add", null, { continuous: true, number: options.initialNumber, questionType: options.questionType, page: options.page });
    }
    const labeledField = (text, control) => { const node = el("label", "layout-field"); node.append(el("span", "", text), control); return node; };
    function render() {
      if (!dialog.layoutWorkspace) return;
      panel.replaceChildren();
      const selected = selectedQuestion();
      const titleText = draft?.kind === "add" ? `补一题 · 第 ${current().number ?? "?"} 题${dirty() ? " · 未保存" : ""}`
        : draft?.kind === "regions" ? `${label(selected || { number: "?" })}${dirty() ? " · 未保存" : ""}`
          : draft ? `${kindNames[draft.kind]} · 未保存` : selected ? label(selected) : "切题与校正";
      panel.append(el("strong", "", titleText));
      if (readOnly()) panel.append(el("p", "hint", state.paper?.archived ? "原卷已归档，可查看题框和保存记录；恢复资料后才能调整。" : "原卷处理期间只能查看题框和保存记录。"));
      if (draft) renderDraft(); else renderOverview();
      renderHistory();
      configure();
    }
    function renderOverview() {
      if (lastDeleted && host.restoreDeletedBatch) {
        const removed = el("section", "layout-empty-range");
        removed.append(el("strong", "", `第 ${lastDeleted.number} 题已移到回收站`));
        const restoreQuestion = button("恢复这题", "small primary", restoreLastDeletedQuestion);
        restoreQuestion.id = "pageLayoutRestoreDeleted"; restoreQuestion.disabled = dialog.saving || lookupFailed;
        removed.append(restoreQuestion); panel.append(removed);
      }
      const actions = el("div", "layout-actions");
      const add = button("补一题", "small primary", () => begin("add", null, { continuous: true })); add.id = "pageLayoutAdd"; add.disabled = readOnly(); actions.append(add);
      const q = selectedQuestion();
      const renumber = button("修改题号", "small", () => begin("renumber")); renumber.id = "pageLayoutRenumber"; renumber.disabled = !q || readOnly();
      const split = button("拆题", "small", () => begin("split")); split.id = "pageLayoutSplit"; split.disabled = !q || readOnly();
      const merge = button("合题…", "small", () => renderMergePicker()); merge.id = "pageLayoutMerge"; merge.disabled = !q || readOnly();
      if (q) actions.append(renumber);
      const more = el("details", "layout-more-actions"); more.append(el("summary", "", "更多操作"));
      const advanced = el("div", "layout-actions"); advanced.append(split, merge); more.append(advanced);
      if (q) actions.append(more); panel.append(actions);
      if (q) {
        const pages = [...new Set(q.regions.map((r) => r.page_idx + 1))];
        panel.append(el("p", "layout-current", `${q.regions.length} 段 · ${pages.length ? `第 ${pages.join("、")} 页` : "尚无范围"} · ${status(q)}`));
        panel.append(button("查看这题", "small quiet", () => { void reviewQuestion(q.id); }, "返回审核并定位到这道题"));
      } else panel.append(el("p", "hint", "单击原卷题框即可选中并调整；双击已保存题框返回审核。白底区域可以直接补题。"));
    }
    async function renderMergePicker() {
      const q = selectedQuestion(); if (!q || dialog.saving) return;
      if (draft && !(await guard(() => { void renderMergePicker(); }))) return;
      if (draft) clearDraft();
      const chooser = el("section", "layout-merge-picker"); chooser.id = "pageLayoutMergePicker"; chooser.append(el("strong", "", "选择同组题目，按下面顺序合成一道题"));
      const chosen = [q.id], list = el("div");
      state.questions.filter((item) => groupId(item) === groupId(q)).sort(host.questionCompare).forEach((item) => {
        const check = el("input"); check.type = "checkbox"; check.checked = item.id === q.id; check.dataset.layoutMergeId = item.id;
        check.addEventListener("change", () => { const i = chosen.indexOf(item.id); if (check.checked && i < 0) chosen.push(item.id); else if (!check.checked && i >= 0) chosen.splice(i, 1); summary.textContent = `合题顺序：${chosen.map((id) => label(state.questions.find((x) => x.id === id))).join(" → ")}`; });
        list.append(labeledField(label(item), check));
      });
      const summary = el("p", "hint", `合题顺序：${label(q)}`);
      const start = button("编辑合题结果", "small primary", () => begin("merge", chosen.map((id) => state.questions.find((x) => x.id === id)))); start.id = "pageLayoutMergeBegin";
      chooser.append(list, summary, start, button("返回", "small quiet", render)); panel.replaceChildren(chooser);
    }
    function renderDraft() {
      const cancelLabel = draft.kind === "renumber" ? "返回题框" : draft.kind === "add" && continuousAdd ? "取消本题" : draft.kind === "regions" ? "返回总览" : "取消这次操作";
      const cancel = button(cancelLabel, "small quiet", async () => { if (await guard(() => { void render(); })) { clearDraft(); redraw(); } }); cancel.id = "pageLayoutCancelDraft"; panel.append(cancel);
      if (draft.kind === "regions" && selectedQuestion() && !current().regions.length) {
        renderEmptyRange(); renderSaveIssue(); return;
      }
      if (draft.kind === "regions" && selectedQuestion()) {
        const q = selectedQuestion(), t = current(), pages = [...new Set(t.regions.map((r) => r.page_idx + 1))];
        panel.append(el("p", "layout-current", `${t.regions.length} 个框 · 第 ${pages.join("、")} 页 · ${status(q)}`));
        const actions = el("div", "layout-actions");
        actions.append(button("查看这题", "small quiet", () => { void reviewQuestion(q.id); }, "返回审核并定位到这道题"));
        const add = button("补一题", "small primary", () => { void begin("add", null, { continuous: true }); }); add.id = "pageLayoutAdd"; actions.append(add);
        const renumber = button("修改题号", "small", () => begin("renumber")); renumber.id = "pageLayoutRenumber"; actions.append(renumber);
        const more = el("details", "layout-more-actions"); more.append(el("summary", "", "更多操作"));
        const advanced = el("div", "layout-actions");
        const split = button("拆题", "small", () => begin("split")); split.id = "pageLayoutSplit";
        const merge = button("合题…", "small", () => { void renderMergePicker(); }); merge.id = "pageLayoutMerge";
        advanced.append(split, merge);
        more.append(advanced); actions.append(more); panel.append(actions);
      }
      if (draft.kind === "renumber") {
        const q = selectedQuestion(), t = current();
        panel.append(el("p", "layout-current", `当前：${label(q)}`));
        const num = el("input"); num.id = "pageLayoutNumber"; num.type = "number"; num.min = "1"; num.max = "999"; num.value = t.number ?? ""; num.disabled = dialog.saving || lookupFailed;
        num.addEventListener("input", () => {
          if (dialog.saving || lookupFailed) return;
          markBefore(); t.number = Number(num.value); sync();
          // A full stage repaint also rebuilds the sidebar in the app host.
          // Update labels directly so typing keeps the same focused input.
          $("pageStage").querySelectorAll('[data-layout-coverage-id="-1"]').forEach((box) => {
            const tag = box.querySelector(".layout-coverage-label");
            if (tag) tag.textContent = `${t.number || "?"}${t.regions.length > 1 ? ` · ${Number(box.dataset.layoutCoveragePiece) + 1}` : ""}`;
          });
          $("pageUndo").disabled = !history.length; $("pageRedo").disabled = !future.length;
        });
        num.addEventListener("keydown", (event) => {
          if (event.key === "Enter" && !event.isComposing && !event.repeat) { event.preventDefault(); event.stopPropagation(); preview(); }
        });
        panel.append(labeledField("新题号", num));
        panel.append(el("p", "hint", "输入后按 Enter 或点击上方“保存”。原框、颜色和题文保留；本次不会调用 AI。"));
        renderSaveIssue();
        return;
      }
      if (draft.kind === "regions") panel.append(el("p", "hint", "直接拖边角改大小、拖题号标签移动；保存后留在原卷。"));
      else if (draft.kind === "add" && continuousAdd) panel.append(el("p", "hint", "十字光标下点两角固定题框；保存只保留原图，不会识读。"));
      else panel.append(el("p", "hint", "结果作为原图题保存，不自动识读。请逐题核对题号、题组、题型和完整范围。"));
      if (draft.kind === "split") {
        const count = el("input"); count.id = "pageLayoutSplitCount"; count.type = "number"; count.min = "2"; count.max = "12"; count.value = draft.targets.length;
        count.addEventListener("change", () => { try { const resized = resizeSplit(draft, Number(count.value), state.questions); change(() => { draft = resized; }); } catch (error) { toast(error.message, "error"); count.value = draft.targets.length; } });
        panel.append(labeledField("拆成几题", count));
      }
      if (draft.kind === "split" || draft.kind === "merge") {
        const tabs = el("div", "layout-targets");
        draft.targets.forEach((t, index) => {
          const tab = button(`结果 ${index + 1} · 第 ${t.number ?? "?"} 题 · ${t.regions.length} 段`, "small", () => { if (dialog.drag) dialog.drag(); if (dialog.sketch) dialog.sketch.cancel(); draft.active = index; selectedPart = null; dialog.tool = "select"; redraw(); });
          tab.dataset.layoutTargetIndex = index; tab.setAttribute("aria-pressed", String(index === draft.active)); tabs.append(tab);
        }); panel.append(tabs);
      }
      const t = current();
      if (draft.kind !== "regions") {
        const num = el("input"); num.id = "pageLayoutNumber"; num.type = "number"; num.min = "1"; num.max = "999"; num.value = t.number ?? "";
        num.addEventListener("change", () => change(() => { t.number = Number(num.value); })); panel.append(labeledField("题号", num));
        if (draft.kind !== "add" || groups().length > 1) {
          const group = el("select"); group.id = "pageLayoutGroup";
          if (!groups().length) { const o = el("option", "", "未分组"); o.value = ""; group.append(o); }
          groups().forEach((g) => { const o = el("option", "", g.title || `第 ${g.sequence + 1} 组`); o.value = g.id; group.append(o); }); group.value = t.group_id ?? "";
          // Splitting and merging remain inside their original group.
          group.disabled = draft.kind !== "add"; group.addEventListener("change", () => change(() => { t.group_id = group.value ? Number(group.value) : null; })); panel.append(labeledField("题组", group));
        }
        const type = el("select"); type.id = "pageLayoutType";
        Object.entries(host.TYPE_NAMES).forEach(([value, name]) => { const o = el("option", "", name); o.value = value; type.append(o); }); type.value = t.question_type;
        type.addEventListener("change", () => change(() => { t.question_type = type.value; })); panel.append(labeledField("题型", type));
      }
      const draw = button(dialog.tool === "draw" ? "取消框选" : t.regions.length ? "再加一块" : "画题框", "small", toggleDrawing);
      draw.id = "pageLayoutDraw"; draw.setAttribute("aria-pressed", String(dialog.tool === "draw")); panel.append(draw);
      const parts = el("ol", "layout-parts"); parts.id = "pageLayoutParts";
      t.regions.forEach((r, index) => {
        const row = el("li", selectedPart === index ? "selected" : ""); row.dataset.layoutPartIndex = index;
        const locate = button(`${index + 1} · 第 ${r.page_idx + 1} 页`, "small quiet", () => {
          selectedPart = index; dialog.layoutFocus = { questionId: selectedId, index };
          if (r.page_idx !== dialog.page) goToDialogPage(r.page_idx); else redraw();
        }); row.append(locate);
        for (const offset of [-1, 1]) { const move = button(offset < 0 ? "前移" : "后移", "small quiet", () => change(() => { const n = index + offset; [t.regions[index], t.regions[n]] = [t.regions[n], t.regions[index]]; selectedPart = n; })); move.disabled = index + offset < 0 || index + offset >= t.regions.length; row.append(move); }
        row.append(button("移除", "small quiet", () => remove(index)));
        if (draft.targets.length > 1) {
          const transfer = el("select"); transfer.setAttribute("aria-label", `将第 ${index + 1} 段转给其他结果`); transfer.dataset.layoutTransfer = index;
          const o = el("option", "", "转给其他题…"); o.value = ""; transfer.append(o);
          draft.targets.forEach((other, targetIndex) => { if (targetIndex === draft.active) return; const opt = el("option", "", `结果 ${targetIndex + 1} · 第 ${other.number} 题`); opt.value = targetIndex; transfer.append(opt); });
          transfer.addEventListener("change", () => { if (!transfer.value) return; const dest = draft.targets[Number(transfer.value)]; if (dest.regions.length >= 12) { toast("目标题已有 12 段", "error"); return; } change(() => { dest.regions.push(t.regions.splice(index, 1)[0]); selectedPart = null; }); }); row.append(transfer);
        }
        parts.append(row);
      }); panel.append(parts);
      if (!t.regions.length) panel.append(el("p", "hint", dialog.tool === "draw" ? "请在原卷上点题目范围的两个角。" : "点击“画题框”，再在原卷上点题目范围的两个角。"));
      if (draft.kind === "add" && continuousAdd) {
        const cropPreview = el("section", "layout-draft-preview layout-draft-preview-fixed"); cropPreview.id = "pageLayoutTargetPreview";
        cropPreview.append(el("strong", "", "裁片预览"));
        if (t.regions.length) cropPreview.append(cropView(t.regions));
        else cropPreview.append(el("p", "hint", "固定第一个框后，这里会显示题目裁片。"));
        panel.append(cropPreview);
      } else {
        const cropPreview = el("details", "layout-draft-preview"); cropPreview.id = "pageLayoutTargetPreview"; cropPreview.open = previewOpen;
        cropPreview.append(el("summary", "", "当前题保存效果 · 按片段顺序拼接"));
        if (t.regions.length) cropPreview.append(cropView(t.regions));
        cropPreview.addEventListener("toggle", () => { previewOpen = cropPreview.open; }); panel.append(cropPreview);
      }
        if (confirmOpen) renderConfirmation();
      renderSaveIssue();
    }
    function renderEmptyRange() {
      const box = el("section", "layout-empty-range"); box.id = "pageLayoutEmptyRange";
      box.setAttribute("role", "status");
      box.append(el("strong", "", "这道题已经没有范围框"), el("p", "", "题目尚未删除。你可以恢复刚删的框、重新画框，或删除整道题。"));
      const previous = history.at(-1)?.targets[draft.active]?.regions;
      if (previous?.length) {
        const restoreFrame = button("恢复刚删的框", "small primary", () => restore(false));
        restoreFrame.id = "pageLayoutRestoreFrame"; restoreFrame.disabled = dialog.saving || lookupFailed; box.append(restoreFrame);
      }
      const draw = button(dialog.tool === "draw" ? "取消重画" : "重新画框", "small", toggleDrawing);
      draw.id = "pageLayoutDraw"; draw.disabled = dialog.saving || lookupFailed;
      draw.setAttribute("aria-pressed", String(dialog.tool === "draw")); box.append(draw);
      if (host.deleteQuestion) {
        const reason = host.questionDeleteBlockReason?.(selectedQuestion()) || "";
        const removeQuestion = button("删除这题", "small quiet danger", deleteSelectedQuestion);
        removeQuestion.id = "pageLayoutDeleteQuestion"; removeQuestion.disabled = dialog.saving || lookupFailed || Boolean(reason);
        removeQuestion.title = reason || "移到回收站，可以撤销"; box.append(removeQuestion);
        if (reason) box.append(el("p", "hint", reason));
      }
      if (dialog.tool === "draw") box.append(el("p", "hint", "请在原卷上点题目范围的两个角；Esc 取消当前新框。"));
      panel.append(box);
    }
    function renderSaveIssue() {
      if (conflict) {
        const box = el("section", "layout-conflict"); box.id = "pageLayoutConflict";
        box.append(el("p", "", "原卷或题目已被其他操作修改。本次草稿仍保留；请先载入最新题目并核对，再重新调整。"));
        const reload = button("丢弃草稿并载入最新", "small", async () => {
          if (!(await guard())) return;
          const token = generation; setCropSaving(true);
          try { await host.reloadCanonical(); if (token !== generation) return; clearDraft(); redraw(); void loadHistory(); }
          catch (error) { toast(`最新题目暂时无法载入，草稿仍保留：${error.message}`, "error"); }
          finally { if (token === generation) { setCropSaving(false); configure(); } }
        }); reload.id = "pageLayoutReload"; box.append(reload); panel.append(box);
      }
      if (lookupFailed) {
        const retry = button("重试查询保存结果", "small primary", () => recoverRequest()); retry.id = "pageLayoutRetryLookup"; panel.append(el("p", "hint", "连接中断，暂时不知道是否已保存。草稿和本次请求标识已保留，不会重复提交。"), retry);
      }
    }
    function renderConfirmation() {
      const box = el("section", "layout-confirm"); box.id = "pageLayoutConfirm"; box.setAttribute("aria-label", "核对保存结果");
      box.append(el("strong", "", "核对这次保存"));
      if (["split", "merge"].includes(draft.kind)) {
        box.append(el("p", "layout-warning", "拆题或合题后，旧题会移入回收站，受影响的已入库版本会撤回发布。历史记录保留，旧组卷不会静默换成新题；结果需要重新核对。"));
        const published = draft.sources.map((s) => state.questions.find((q) => q.id === s.id)).filter((q) => q?.publication);
        const list = el("ul", "layout-withdrawals"); list.id = "pageLayoutWithdrawals";
        published.forEach((q) => list.append(el("li", "", `${label(q)} · 已入库 v${q.publication.version}（编号 ${q.publication.id}）`)));
        if (!published.length) list.append(el("li", "", "当前所选旧题没有有效的入库版本需要撤回。")); box.append(list);
      } else box.append(el("p", "hint", "补题保存为原图题；核对通过后才会入库。"));
      box.append(el("p", "hint", draft.kind === "regions" ? "保留原有人工文字；本次不提交 AI。" : "保存为原图题；旧题的人工文字保留在操作历史中，本次不提交 AI。"));
      draft.targets.forEach((t, i) => { box.append(el("strong", "", `结果 ${i + 1} · 第 ${t.number} 题 · ${host.TYPE_NAMES[t.question_type] || t.question_type} · ${t.regions.length} 段`), cropView(t.regions)); });
      const save = button("确认保存", "small primary", commit); save.id = "pageLayoutConfirmSave";
      box.append(save, button("继续调整", "small quiet", () => { confirmOpen = false; pendingAfterSave = null; render(); })); panel.append(box);
    }
    async function loadHistory() {
      const token = generation, id = state.paperId;
      try {
        const data = await api(`/api/papers/${id}/question-layout`);
        if (token !== generation || !dialog.layoutWorkspace || id !== state.paperId) return;
        operations = data.operations || [];
        latestOperation = data.latest_operation || null;
        // A draft binds to the revision it opened against, never silently rebase.
        if (!draft && Number.isInteger(data.layout_revision)) state.paper.layout_revision = data.layout_revision;
        renderLoadedHistory();
      } catch (error) { if (token === generation && dialog.layoutWorkspace) { operations = []; latestOperation = null; renderLoadedHistory(); const e = el("p", "hint", `操作历史暂时无法读取：${error.message}`); $("pageLayoutHistory").append(e); } }
    }
    function renderLoadedHistory() {
      if (draft?.kind === "renumber") { panel.querySelector("#pageLayoutHistory")?.remove(); renderHistory(); }
      else render();
    }
    function renderHistory() {
      const details = el("details", "layout-history"); details.id = "pageLayoutHistory"; details.append(el("summary", "", "已保存的调整记录"));
      const latest = latestOperation;
      const undo = button("撤销上次保存", "small", () => undoSaved(latest)); undo.id = "pageLayoutUndoSaved"; undo.disabled = !latest?.can_undo || dirty() || dialog.saving || readOnly();
      details.append(undo, button("刷新记录", "small quiet", loadHistory));
      if (latest?.blocked_reason) details.append(el("p", "hint", latest.blocked_reason));
      if (!operations.length) details.append(el("p", "hint", "尚无范围操作记录。"));
      operations.forEach((op) => {
        const row = el("details", "layout-operation"); row.dataset.layoutOperationId = op.id;
        row.append(el("summary", "", `${kindNames[op.kind] || op.kind} · ${new Date(op.created_at).toLocaleString()}${op.undone_at ? " · 已撤销" : ""}`));
        row.append(el("p", "hint", op.blocked_reason || (op.can_undo ? "可撤销；会恢复操作前的题目和原有人工内容。" : "此记录目前不可撤销。")));
        (op.before_snapshot?.questions || []).forEach((q) => {
          const previous = el("details", "layout-before-question"); previous.append(el("summary", "", `${label(q)} · 保存前`));
          if (q.regions?.length) previous.append(cropView(q.regions));
          const evidence = sourceEvidence(op.before_snapshot, q.id);
          const content = el("details", "layout-source-evidence"); content.dataset.layoutSourceEvidence = q.id;
          content.append(el("summary", "", "旧题文与识读记录"), el("strong", "", q.edited ? "保存前题文 · 包含已保存的人工修改" : "保存前题文"));
          const text = q.edited_stem ?? q.stem ?? q.ai_stem ?? ""; if (text) content.append(el("pre", "layout-history-text", text));
          Object.entries(q.edited_options || q.options || q.ai_options || {}).forEach(([key, value]) => content.append(el("p", "layout-history-text", `${key}：${value}`)));
          for (const [key, name] of [["answer", "答案"], ["analysis", "解析"]]) {
            const value = q[`edited_${key}`] ?? q[key]; if (value) content.append(el("p", "layout-history-text", `${name}：${value}`));
          }
          let readingCount = 0;
          const jsonRecord = (name, value, key) => {
            if (!value || (typeof value === "object" && !Object.keys(value).length)) return;
            const record = el("details", "layout-reading-record"); record.dataset.layoutReadingKey = key;
            record.append(el("summary", "", name), el("pre", "layout-history-text", typeof value === "string" ? value : JSON.stringify(value, null, 2))); content.append(record); readingCount += 1;
          };
          for (const [key, name] of [["read_a", "识读甲"], ["read_b", "识读乙"], ["read_c", "裁决识读"], ["ocr_suggestion", "后来识读的建议稿"]]) jsonRecord(name, q[key], key);
          evidence.region_reads.forEach((reading) => jsonRecord(`单块识读 · 第 ${reading.page_idx + 1} 页`, reading, `region_read_${reading.id}`));
          if (!readingCount) content.append(el("p", "hint", "保存前暂无识读记录。"));
          const complete = el("details", "layout-complete-evidence"); complete.append(el("summary", "", "完整保存前记录"), el("pre", "layout-history-text", JSON.stringify(evidence, null, 2))); content.append(complete);
          previous.append(content);
          row.append(previous);
        }); details.append(row);
      }); panel.append(details);
    }
    async function undoSaved(op) {
      if (!op?.can_undo || dirty() || dialog.saving) return;
      if (!(await confirmDialog({ title: "撤销上次保存？", text: "恢复保存前的题目、范围和人工内容。发生后续题面修改时会拒绝撤销，避免覆盖新内容。", ok: "撤销上次保存", cancel: "保留" }))) return;
      const token = generation, id = state.paperId; setCropSaving(true);
      try {
        const data = await api(`/api/papers/${id}/question-layout/${op.id}/undo`, { method: "POST", body: { layout_revision: state.paper.layout_revision } });
        if (token !== generation || id !== state.paperId) return;
        clearDraft();
        host.applyCanonical(data); selectedId = data.questions.find((q) => op.source_ids?.includes(q.id))?.id ?? null;
        toast("上次保存已撤销", "success"); redraw(); await loadHistory();
      } catch (error) { toast(`撤销失败：${error.message}`, "error"); await loadHistory(); }
      finally { if (token === generation) { setCropSaving(false); configure(); } }
    }
    async function preview({ next = false, complete = false, direct = false } = {}) {
      if (dialog.saving || lookupFailed) return false;
      if (!draft) {
        if (complete && manualSession) await host.returnReview?.(selectedId);
        return Boolean(complete);
      }
      if (dialog.sketch) { toast("请先固定新框，或按 Esc 取消", "error"); return false; }
      if (dialog.drag) dialog.drag();
      if (complete) return returnToReview();
      if (draft.kind === "regions" && !current().regions.length) {
        toast("请先恢复刚删的框或重新画框；想删除整题请点“删除这题”。", "error"); return false;
      }
      const error = validate(draft, state.questions, state.paper.pages); if (error) { toast(error, "error"); return; }
      if (!dirty()) { toast("没有尚未保存的改动"); return true; }
      saveDisposition = next ? "next" : "stay";
      if (draft.kind === "add" && continuousAdd) {
        confirmOpen = false;
        return commit({ direct: true });
      }
      if (["split", "merge"].includes(draft.kind) && !direct) {
        confirmOpen = true; render(); $("pageLayoutConfirm")?.scrollIntoView({ block: "nearest" }); $("pageLayoutConfirmSave")?.focus();
        return false;
      }
      if (["split", "merge"].includes(draft.kind) && direct) {
        confirmOpen = true; render(); $("pageLayoutConfirm")?.scrollIntoView({ block: "nearest" });
        return false;
      }
      confirmOpen = false;
      return commit({ direct: true });
    }
    async function returnToReview() {
      if (dialog.saving || lookupFailed) { toast("请先完成当前保存或核实保存结果", "error"); return false; }
      if (dialog.sketch) { toast("请先固定新框，或按 Esc 取消", "error"); return false; }
      if (dialog.drag) dialog.drag();
      if (draft && dirty() && !(await guard(() => { void returnToReview(); }))) return false;
      if (draft) clearDraft();
      await host.returnReview?.(selectedId);
      return true;
    }
    async function reviewQuestion(id) {
      if (dialog.saving || lookupFailed || dialog.sketch || dialog.drag) return false;
      if (draft && dirty() && !(await guard(() => { void reviewQuestion(id); }))) return false;
      if (draft) clearDraft();
      await host.reviewQuestion?.(id);
      return true;
    }
    async function requestClose() {
      if (dialog.saving || lookupFailed) { toast("请先完成当前保存或核实保存结果", "error"); return false; }
      if (dialog.drag) dialog.drag();
      if (draft && dirty() && !(await guard(async () => {
        if (await requestClose()) host.closeWorkspace?.();
      }))) return false;
      if (draft) clearDraft();
      return true;
    }
    function nextNumberAfter(question) {
      if (!question) return null;
      const group = groupId(question), used = new Set(state.questions.filter((q) => groupId(q) === group).map((q) => Number(q.number)));
      let number = Number(question.number) + 1;
      while (number <= 999 && used.has(number)) number += 1;
      return number <= 999 ? number : null;
    }
    async function accepted(data, disposition = saveDisposition || "stay") {
      const kind = draft?.kind, renumbered = kind === "renumber", wasContinuousAdd = kind === "add" && continuousAdd;
      const afterSave = pendingAfterSave; pendingAfterSave = null;
      const first = data.operation?.target_ids?.[0];
      host.applyCanonical(data); selectedId = first ?? selectedId;
      if (wasContinuousAdd) {
        const saved = state.questions.find((q) => q.id === first);
        if (saved?.number != null) host.onCutSaved?.(saved.number);
        if (dialog.saving) setCropSaving(false);
        clearDraft();
        if (disposition === "next") {
          const page = dialog.page, zoom = dialog.zoom, zoomMode = dialog.zoomMode;
          const stage = $("pageStage"), top = stage.scrollTop, left = stage.scrollLeft;
          const number = nextNumberAfter(saved);
          await begin("add", null, { continuous: true, groupId: saved ? groupId(saved) : null, number, questionType: saved?.question_type || "unknown", page: page });
          dialog.page = page; dialog.zoom = zoom; dialog.zoomMode = zoomMode;
          requestAnimationFrame(() => { if (dialog.layoutWorkspace) stage.scrollTo({ top, left }); });
          toast(number === null ? "题目已保存。题号已到 999，请为下一题手动填写题号。" : "题目已保存；已准备下一题。", "success");
          void loadHistory();
          return true;
        }
        if (disposition === "return") {
          toast("题目已保存，正在返回审核。", "success");
          await host.returnReview?.(saved?.id ?? selectedId);
          void loadHistory();
          return true;
        }
        if (saved) beginSelectedRegionDraft(saved, 0);
        redraw(); toast("题目已保存为原图题；可以继续校正或主动识读。", "success"); void loadHistory();
        if (afterSave) await afterSave();
        return true;
      }
      const part = selectedPart;
      clearDraft();
      const selected = selectedQuestion();
      if (selected && !readOnly()) beginSelectedRegionDraft(selected, part);
      redraw(); toast(renumbered ? "题号已保存，请重新核对" : "原卷调整已保存；未提交识读", "success"); void loadHistory();
      if (afterSave) await afterSave();
      return true;
    }
    async function recoverRequest() {
      if (!request || dialog.saving) return;
      const token = generation, id = state.paperId; setCropSaving(true);
      try {
        const data = await api(`/api/papers/${id}/question-layout?client_request_id=${encodeURIComponent(request.client_request_id)}`);
        if (token !== generation || id !== state.paperId) return;
        if (data.operation || data.already_applied) {
          // Normal responses and receipt lookup share one canonical success path.
          if (!(data.questions && data.paper)) await host.reloadCanonical();
          if (data.operation?.target_ids?.[0] != null) selectedId = data.operation.target_ids[0];
          await accepted({ paper: data.paper || state.paper, questions: data.questions || state.questions, operation: data.operation }, saveDisposition);
        } else { lookupFailed = false; render(); toast("本次请求尚未保存，可再次确认保存"); }
      } catch (error) { lookupFailed = true; render(); toast(`保存结果仍无法核实：${error.message}`, "error"); }
      finally { if (token === generation) { setCropSaving(false); configure(); } }
    }
    async function commit({ direct = false } = {}) {
      if (!draft || (!confirmOpen && !direct) || (direct && ["split", "merge"].includes(draft.kind)) || dialog.saving || lookupFailed) return false;
      const error = validate(draft, state.questions, state.paper.pages); if (error) { toast(error, "error"); return; }
      const token = generation, id = state.paperId; setCropSaving(true);
      try {
        if (host.ensureSourcesReady && !(await host.ensureSourcesReady(draft.sources.map((s) => s.id)))) return;
        request ||= payload(draft, draftRevision, crypto.randomUUID());
        const data = await api(`/api/papers/${id}/question-layout`, { method: "POST", body: request });
        if (token === generation && id === state.paperId) return await accepted(data, saveDisposition);
      } catch (error) {
        if (token !== generation || id !== state.paperId) return;
        if (["renumber", "add"].includes(draft?.kind) && /题号.*(?:占用|重复|存在)|(?:占用|重复|存在).*题号/.test(error.message)) { request = null; saveDisposition = null; confirmOpen = false; toast(`保存被阻止：${error.message} 请改用其他题号。草稿仍保留。`, "error"); render(); }
        else if (error.status === 409 || /版本|冲突|已被|revision|fingerprint/i.test(error.message)) { conflict = true; request = null; toast(`保存被阻止：${error.message}`, "error"); render(); }
        else if (error.status) { toast(`保存失败，草稿仍保留：${error.message}`, "error"); render(); }
        else { lookupFailed = true; toast("连接中断，正在核实保存结果", "error"); }
      } finally { if (token === generation) { setCropSaving(false); configure(); } }
      if (lookupFailed) { await recoverRequest(); return false; }
      return false;
    }
    function renderParts() {
      if (!dialog.layoutWorkspace) return;
      $("regionPieces").hidden = true; $("regionPieces").replaceChildren();
      $("pageCropPreview").hidden = true; $("pageCropPreviewBody").replaceChildren();
      render();
    }
    function activeQuestions() {
      const result = state.questions.filter((q) => !draft?.sources.some((s) => s.id === q.id));
      if (draft) draft.targets.forEach((t, i) => result.push({ ...t, id: -(i + 1), group: groups().find((g) => g.id === t.group_id), color_index: i === 0 && selectedQuestion() ? (selectedQuestion().color_index + (continuousAdd ? 1 : 0)) % 6 : (i + (selectedQuestion()?.color_index ?? 0)) % 6 }));
      return result;
    }
    function selectPart(surface, index) {
      selectedPart = index; dialog.selected = index;
      surface.querySelectorAll(".layout-edit-box").forEach((n) => n.classList.toggle("selected", Number(n.dataset.layoutBoxIndex) === index));
      renderParts();
    }
    function chooseHit(hit) {
      overlap.hidden = true;
      if (draft && hit.question.id === -(draft.active + 1)) {
        if (selectedPart !== hit.index) { selectedPart = hit.index; redraw(); }
        return;
      }
      if (draft && hit.question.id < 0) {
        const target = -hit.question.id - 1;
        if (draft.active !== target || selectedPart !== hit.index) { draft.active = target; selectedPart = hit.index; redraw(); }
        return;
      }
      void selectQuestion(hit.question.id, hit.index);
    }
    function showHits(hits, event) {
      overlap.replaceChildren(el("strong", "", "这里有重叠范围，请选择"));
      hits.forEach((hit, i) => { const b = button(`${label(hit.question)} · 第 ${hit.index + 1} 段`, "small", () => chooseHit(hit)); b.dataset.layoutHit = i; overlap.append(b); });
      overlap.append(button("取消", "small quiet", () => { overlap.hidden = true; })); overlap.hidden = false;
      const rect = $("pageDialog").getBoundingClientRect(); overlap.style.left = `${Math.max(rect.left + 8, Math.min(event.clientX, rect.right - 270))}px`;
      overlap.style.top = `${Math.max(rect.top + 8, Math.min(event.clientY, rect.bottom - Math.min(hits.length * 36 + 75, 310)))}px`;
      overlap.querySelector("button")?.focus();
    }
    function renderSurface(surface) {
      surface.dataset.layoutTool = dialog.tool || "select";
      const qs = activeQuestions();
      qs.forEach((q, fallback) => q.regions.forEach((r, index) => {
        if (r.page_idx !== dialog.page) return;
        const target = draft && q.id === -(draft.active + 1), own = target && draft.kind !== "renumber";
        const box = el("div", `layout-coverage${q.id === selectedId || (target && draft.kind === "renumber") ? " question-selected" : ""}${own ? " layout-edit-box" : ""}${own && selectedPart === index ? " selected" : ""}${target && continuousAdd ? " layout-new-draft" : ""}`);
        box.style.setProperty("--layout-color", colorOf(q, fallback)); placeBox(box, r.bbox); box.dataset.layoutCoverageId = q.id;
        box.dataset.layoutSourceQuestionId = q.id > 0 ? q.id : (draft?.sources?.[0]?.id ?? "");
        box.dataset.layoutCoveragePiece = index;
        if (own) {
          box.dataset.layoutBoxIndex = index; box.tabIndex = 0; box.setAttribute("role", "group"); box.setAttribute("aria-label", `第 ${index + 1} 段，单击选中后拖题号标签或边角调整`);
          box.addEventListener("focus", () => selectPart(surface, index));
          const tab = el("span", "layout-box-label", `${current().number} · ${index + 1}`); tab.dataset.layoutDragLabel = index;
          tab.addEventListener("pointerdown", (event) => {
            if (event.button !== 0 || dialog.spacePan || dialog.tool === "draw") return;
            event.preventDefault(); event.stopPropagation();
            const already = selectedPart === index; selectPart(surface, index);
            if (already) drag(event, surface, box, index, "move");
          }); box.append(tab);
          ["nw", "ne", "sw", "se", "n", "s", "w", "e"].forEach((handle) => {
            const grip = el("span", `grip grip-${handle}`); grip.dataset.layoutHandle = handle;
            grip.addEventListener("pointerdown", (event) => { if (selectedPart === index) drag(event, surface, box, index, handle); }); box.append(grip);
          });
          box.addEventListener("keydown", (event) => {
            if (dialog.saving || event.isComposing || event.ctrlKey || event.metaKey || event.altKey || event.target !== box) return;
            if (event.key === "Delete") { event.preventDefault(); remove(index); return; }
            if (!["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) return;
            event.preventDefault(); const step = event.shiftKey ? 20 : 5;
            change(() => { const dx = event.key === "ArrowLeft" ? -step : event.key === "ArrowRight" ? step : 0, dy = event.key === "ArrowUp" ? -step : event.key === "ArrowDown" ? step : 0; r.bbox = moveBox(r.bbox, dx, dy); selectedPart = index; });
          });
        } else {
          const tag = el("span", "layout-coverage-label", `${q.number}${q.regions.length > 1 ? ` · ${index + 1}` : ""}`); box.append(tag);
        }
        surface.append(box);
      }));
      const updateHover = (event) => {
        const hit = dialog.imageReady && dialog.tool !== "draw" ? hitTest(qs, dialog.page, pointFrom(event, surface))[0] : null;
        surface.classList.toggle("layout-hit-hover", Boolean(hit));
        surface.querySelectorAll(".layout-coverage[data-layout-coverage-id]").forEach((box) => {
          const active = hit && Number(box.dataset.layoutCoverageId) === hit.question.id && Number(box.dataset.layoutCoveragePiece) === hit.index;
          box.classList.toggle("question-hovered", Boolean(active));
        });
      };
      surface.addEventListener("pointermove", updateHover);
      surface.addEventListener("pointerleave", () => { surface.classList.remove("layout-hit-hover"); surface.querySelectorAll(".question-hovered").forEach((box) => box.classList.remove("question-hovered")); });
      surface.addEventListener("pointerdown", (event) => {
        if (event.button !== 0 || dialog.saving || dialog.spacePan || !dialog.imageReady) return;
        if (event.target.closest?.("[data-layout-handle], [data-layout-drag-label]") && dialog.tool !== "draw") { lastFrameTap = null; return; }
        event.preventDefault(); event.stopPropagation(); overlap.hidden = true;
        if (dialog.sketch) { lastFrameTap = null; dialog.sketch.complete(event); return; }
        if (draft && dialog.tool === "draw") { lastFrameTap = null; sketch(event, surface); return; }
        const hits = hitTest(qs, dialog.page, pointFrom(event, surface));
        if (hits.length > 1) { lastFrameTap = null; showHits(hits, event); }
        else if (hits.length) {
          const hit = hits[0], sourceId = hit.question.id > 0 ? hit.question.id : selectedId;
          const eligible = Boolean(sourceId && (!draft || draft.kind === "regions"));
          const now = performance.now(), previous = lastFrameTap;
          if (eligible && previous?.id === sourceId && now - previous.time <= 450
              && Math.hypot(event.clientX - previous.x, event.clientY - previous.y) <= 8) {
            lastFrameTap = null;
            void reviewQuestion(sourceId);
            return;
          }
          lastFrameTap = eligible ? { id: sourceId, time: now, x: event.clientX, y: event.clientY } : null;
          chooseHit(hit);
        }
        else { lastFrameTap = null; if (!draft) { selectedId = null; redraw(); } }
      }, { capture: true });
    }
    function drag(event, surface, node, index, handle) {
      if (!draft || draft.kind === "renumber" || dialog.saving || lookupFailed || event.button !== 0 || dialog.spacePan || dialog.tool === "draw" || !dialog.imageReady) return;
      event.preventDefault(); event.stopPropagation(); node.focus({ preventScroll: true });
      const box = current().regions[index], original = [...box.bbox], before = copy(draft), start = pointFrom(event, surface), pointerId = event.pointerId;
      let crossed = false, ended = false;
      const move = (e) => {
        if (e.pointerId !== pointerId || ended) return;
        if (!crossed && !movedEnough(event, e)) return; crossed = true;
        const p = pointFrom(e, surface), dx = p[0] - start[0], dy = p[1] - start[1]; let b = [...original];
        if (handle === "move") b = moveBox(original, dx, dy);
        else { if (handle.includes("w")) b[0] = Math.max(0, Math.min(b[2] - 5, original[0] + dx)); if (handle.includes("e")) b[2] = Math.min(1000, Math.max(b[0] + 5, original[2] + dx)); if (handle.includes("n")) b[1] = Math.max(0, Math.min(b[3] - 5, original[1] + dy)); if (handle.includes("s")) b[3] = Math.min(1000, Math.max(b[1] + 5, original[3] + dy)); }
        box.bbox = b.map((v) => Math.round(v * 10) / 10); placeBox(node, box.bbox);
      };
      const finish = () => { if (ended) return; ended = true; dialog.drag = null; window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); window.removeEventListener("pointercancel", cancel); window.removeEventListener("blur", blur); node.removeEventListener("lostpointercapture", blur); try { node.releasePointerCapture(pointerId); } catch {} };
      const up = (e) => { if (e.pointerId !== pointerId) return; move(e); finish(); if (JSON.stringify(original) !== JSON.stringify(box.bbox)) { markBefore(before); redraw(); } };
      const cancel = (e) => { if (e?.pointerId !== undefined && e.pointerId !== pointerId) return; finish(); box.bbox = original; if (node.isConnected) placeBox(node, original); sync(); };
      const blur = () => cancel(); dialog.drag = cancel;
      window.addEventListener("pointermove", move); window.addEventListener("pointerup", up); window.addEventListener("pointercancel", cancel); window.addEventListener("blur", blur); node.addEventListener("lostpointercapture", blur);
      try { node.setPointerCapture(pointerId); } catch {}
    }
    function sketch(event, surface) {
      if (!draft || draft.kind === "renumber" || current().regions.length >= 12 || lookupFailed) { toast("一道题最多 12 段", "error"); return; }
      const start = pointFrom(event, surface), preview = el("div", "layout-coverage layout-drawing"), page = dialog.page, target = draft.active, pointerId = event.pointerId;
      preview.style.setProperty("--layout-color", COLORS[target % 6]); surface.append(preview);
      const bboxAt = (e) => { const p = pointFrom(e, surface); return [Math.min(start[0], p[0]), Math.min(start[1], p[1]), Math.max(start[0], p[0]), Math.max(start[1], p[1])].map((v) => Math.round(v * 10) / 10); };
      const move = (e) => { if (e.pointerId === pointerId) placeBox(preview, bboxAt(e)); };
      const clean = () => { preview.remove(); dialog.sketch = null; window.removeEventListener("pointermove", move); window.removeEventListener("pointercancel", cancel); window.removeEventListener("blur", blur); };
      const cancel = (e) => { if (e?.pointerId !== undefined && e.pointerId !== pointerId) return; clean(); dialog.tool = "select"; configure(); render(); };
      const blur = () => cancel();
      // The second corner is a separate click; it need not come from the same
      // pointer id as the first click. The page and active draft stay locked.
      dialog.sketch = { cancel: () => { clean(); dialog.tool = "select"; configure(); render(); }, complete: (e) => { if (!draft || page !== dialog.page || target !== draft.active) return; const b = bboxAt(e); clean(); if (b[2] - b[0] <= 8 || b[3] - b[1] <= 8) { dialog.tool = "select"; configure(); render(); renderStage(); toast("范围太小，请重新框选", "error"); return; } dialog.tool = "select"; change(() => { current().regions.push({ page_idx: page, bbox: b }); selectedPart = current().regions.length - 1; }); } };
      window.addEventListener("pointermove", move); window.addEventListener("pointercancel", cancel); window.addEventListener("blur", blur);
    }
    function keydown(event) {
      if (!dialog.layoutWorkspace || event.defaultPrevented || event.isComposing || event.keyCode === 229) return false;
      if (event.key === "Escape" && (dialog.drag || dialog.sketch)) {
        event.preventDefault(); event.stopPropagation();
        if (dialog.drag) dialog.drag(); else dialog.sketch.cancel();
        return true;
      }
      if (event.key === "Escape" && !overlap.hidden) { overlap.hidden = true; event.preventDefault(); event.stopPropagation(); return true; }
      if (event.key === "Escape" && confirmOpen) { confirmOpen = false; render(); event.preventDefault(); event.stopPropagation(); return true; }
      const editing = event.target.closest?.("input, select, textarea, [contenteditable=true]");
      const otherDialog = typeof document !== "undefined" && document.querySelector('dialog[open]:not(#pageDialog)');
      if (!editing && event.target.closest?.("#pageStage") && !otherDialog && !event.repeat && !event.altKey) {
        const key = event.key.toLowerCase(), modified = event.ctrlKey || event.metaKey;
        if (key === "enter" && modified) { event.preventDefault(); event.stopPropagation(); void preview({ complete: true }); return true; }
        if (key === "enter" && event.shiftKey) { event.preventDefault(); event.stopPropagation(); void preview({ next: true }); return true; }
        if (key === "s" || key === "enter") { event.preventDefault(); event.stopPropagation(); void preview(); return true; }
      }
      if (editing && (event.ctrlKey || event.metaKey) && !event.altKey && event.key.toLowerCase() === "s") {
        event.preventDefault(); event.stopPropagation();
        if (draft?.kind === "renumber" && !event.repeat) preview();
        return true;
      }
      if (!editing && (event.ctrlKey || event.metaKey) && !event.altKey && ["s", "z"].includes(event.key.toLowerCase())) {
        // Save and complete shortcuts are tied to the canvas. A focused guide,
        // toolbar, or other control must never submit the current crop by accident.
        if (event.key.toLowerCase() === "s" && (otherDialog || !event.target.closest?.("#pageStage"))) return false;
        event.preventDefault(); event.stopPropagation(); if (!event.repeat) { if (event.key.toLowerCase() === "s") preview(); else restore(event.shiftKey); } return true;
      }
      return false;
    }
    return { open, close, configure, snapshot, render, refresh, renderParts, renderSurface, preview, restore, remove, keydown, begin, selectQuestion, returnToReview, requestClose, dirty, guard, hasUnknownSave: () => lookupFailed };
  }
  function moveBox(bbox, dx, dy) {
    const w = bbox[2] - bbox[0], h = bbox[3] - bbox[1];
    const x = Math.min(1000 - w, Math.max(0, bbox[0] + dx)), y = Math.min(1000 - h, Math.max(0, bbox[1] + dy));
    return [x, y, x + w, y + h].map((v) => Math.round(v * 10) / 10);
  }
  return { COLORS, groupId, targetOf, colorOf, hitTest, nextNumber, createDraft, resizeSplit, validate, payload, movedEnough, moveBox, sourceEvidence, createController };
});
