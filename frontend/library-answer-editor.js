/* One answer editor for the library and the current paper. Nothing exports until saved. */
((root) => {
  "use strict";
  function create({ node, QB, notify, confirm, onSaved }) {
    const S = root.LibrarySolutions;
    let dialog, controls, current = null, items = [], scope = "paper", scopeContext = null, selection = new Set(), drafts = new Map();
    let token = 0, epoch = 0, loading = false, saving = false, uploading = false, queueing = false, cancelling = false, polling = false, closing = false;
    let pollTimer = null, previewTimer = null, pollAgain = false, jobs = new Map(), returnFocus, requestedIds = new Set(), handoff = null, watchedSince = 0, queueNote = "";
    const requests = new Map(), pendingSaves = new Set(), MAX_WATCH = 10 * 60 * 1000;
    const jobsPending = new Set(["queued", "pending", "running", "waiting"]);
    const jobsSucceeded = new Set(["done", "completed", "succeeded", "success"]);
    function jobDraftState(job, record, item) {
      if (!job?.result || !jobsSucceeded.has(job.status)) return null;
      const savedSolution = record ? record.hasSavedSolution : item?.solution_revision !== "origin" && Boolean(item?.solution);
      if (!savedSolution) return "draft";
      // Compare the persisted text, not the current inputs: later edits can be
      // unsaved while this particular AI draft is already in the saved version.
      const saved = record ? JSON.parse(record.saved) : item.solution;
      return ["answer", "analysis"].every(field => String(saved?.[field] ?? "").trim() === String(job.result[field] ?? "").trim()) ? "saved" : "retained";
    }
    const api = async (url, supplied = {}) => {
      const { keepAfterClose = false, ...options } = supplied;
      const controller = new AbortController(), timer = root.setTimeout(() => controller.abort(), 30000);
      requests.set(controller, { keepAfterClose });
      try {
        const response = await root.fetch(url, { cache: "no-store", ...options, signal: controller.signal });
        const body = await response.json();
        if (!response.ok) throw new Error(body.error || "这次操作未完成，请重试");
        return body;
      } catch (error) { if (error.name === "AbortError") throw new Error("等待时间较长，请重试。当前编辑内容保留。"); throw error; }
      finally { root.clearTimeout(timer); requests.delete(controller); }
    };
    const post = (url, value, options = {}) => api(url, { method: "POST", headers: { "Content-Type": "application/json", "X-QB-Request": "1" }, body: JSON.stringify(value), ...options });
    function stopWaiting() {
      root.clearTimeout(pollTimer); root.clearTimeout(previewTimer);
      for (const [controller, request] of requests) if (!request.keepAfterClose) controller.abort();
    }
    function schedulePreview() { root.clearTimeout(previewTimer); previewTimer = root.setTimeout(() => { if (dialog?.open && current) renderPreview(); }, 120); }
    const itemLabel = item => `第 ${item.exam_number || item.number} 题`;
    const endpoint = item => `/api/library/${encodeURIComponent(item.id)}/solution`;
    function init() {
      if (dialog) return;
      dialog = node("dialog", "answer-editor-dialog"); dialog.id = "answerEditorDialog";
      dialog.setAttribute("aria-labelledby", "answerEditorTitle");
      const head = node("header", "source-dialog-head");
      const title = node("h3", "", "答案解析"); title.id = "answerEditorTitle";
      const subtitle = node("p", "helper"); subtitle.id = "answerEditorScope";
      const heading = node("div"); heading.append(node("p", "eyebrow", "备课"), title, subtitle);
      const close = node("button", "button button-quiet button-small", "返回"); close.type = "button"; close.addEventListener("click", closeEditor);
      head.append(heading, close);
      const main = node("div", "answer-editor-layout");
      const nav = node("aside", "answer-editor-nav");
      const navHint = node("p", "helper", "勾选要交给 AI 补充的题，点题号手工编辑。");
      const pickMissing = node("button", "button button-small", "勾选缺解析的题"); pickMissing.type = "button";
      pickMissing.addEventListener("click", () => { selection = new Set(items.filter(item => S.completeness(item) !== "ready").map(item => item.id)); showHandoff(null); renderList(); });
      const ai = node("button", "button button-primary button-small", "AI 补充所选题"); ai.type = "button"; ai.id = "answerEditorAi"; ai.addEventListener("click", queueSelected);
      const list = node("div", "answer-editor-list"); list.setAttribute("aria-label", "本次题目与 AI 选择");
      const aiStatus = node("p", "helper answer-ai-status"); aiStatus.setAttribute("role", "status"); aiStatus.setAttribute("aria-live", "polite");
      const aiTools = node("div", "answer-ai-tools");
      const copyTask = node("button", "button button-small", "复制助手任务说明"); copyTask.type = "button"; copyTask.id = "answerEditorCopyTask"; copyTask.hidden = true; copyTask.addEventListener("click", copyHandoff);
      const checkAi = node("button", "button button-small", "检查初稿"); checkAi.type = "button"; checkAi.id = "answerEditorCheckAi"; checkAi.addEventListener("click", () => { watchedSince = Date.now(); void pollJobs(); });
      const cancelAi = node("button", "button button-quiet button-small", "取消所选任务"); cancelAi.type = "button"; cancelAi.id = "answerEditorCancelAi"; cancelAi.addEventListener("click", cancelSelected);
      aiTools.append(copyTask, checkAi, cancelAi);
      const taskDetails = node("details", "answer-task-details"); taskDetails.hidden = true; taskDetails.append(node("summary", "", "助手任务说明"));
      const taskText = node("textarea"); taskText.readOnly = true; taskText.rows = 8; taskText.setAttribute("aria-label", "复制给当前助手的任务说明"); taskDetails.append(taskText);
      const settings = node("a", "helper", "设置解题模型"); settings.href = "/settings#ai"; settings.target = "_blank";
      nav.append(navHint, pickMissing, ai, aiStatus, aiTools, taskDetails, settings, list);
      const panel = node("section", "answer-editor-panel");
      const status = node("p", "answer-editor-status"); status.setAttribute("role", "status"); status.setAttribute("aria-live", "polite");
      const question = node("details", "answer-question"); question.append(node("summary", "", "查看本题"));
      const questionBody = node("div", "paper"); question.append(questionBody);
      question.addEventListener("toggle", () => { if (question.open && current) QB.renderQuestion(questionBody, current.item.content, { showNumber: false, showAnswer: "none" }); });
      const fields = node("div", "answer-editor-fields");
      const answerLabel = node("label", "", "答案结果");
      const answer = node("textarea"); answer.id = "answerEditorResult"; answer.rows = 2; answer.placeholder = "如 B，或 x＝3"; answerLabel.append(answer);
      const analysisLabel = node("label", "", "解析过程");
      const analysis = node("textarea"); analysis.id = "answerEditorAnalysis"; analysis.rows = 8; analysis.placeholder = "写出解题步骤。公式可写 $\\frac{1}{2}$；空一行分段。"; analysisLabel.append(analysis);
      fields.append(answerLabel, analysisLabel, node("p", "helper", "公式随输入预览；AI 初稿会直接显示在这里，检查后保存。"));
      const imageTools = node("div", "answer-image-tools");
      const uploadLabel = node("label", "button button-small", "上传解析图");
      const upload = node("input"); upload.type = "file"; upload.accept = "image/png,image/jpeg,image/webp"; upload.multiple = true; upload.hidden = true;
      upload.addEventListener("change", async () => { await uploadFiles(Array.from(upload.files || [])); upload.value = ""; }); uploadLabel.append(upload);
      const cropToggle = node("button", "button button-small", "从原卷裁图"); cropToggle.type = "button"; cropToggle.addEventListener("click", openCrop);
      imageTools.append(uploadLabel, cropToggle, node("span", "helper", "也可直接粘贴图片"));
      const figures = node("div", "answer-image-list");
      const crop = node("section", "answer-crop", null); crop.hidden = true;
      const cropBar = node("div", "answer-image-tools");
      const pages = node("select"); pages.setAttribute("aria-label", "解析图来源页"); pages.addEventListener("change", showCropPage);
      const cropSave = node("button", "button button-primary button-small", "加入解析图"); cropSave.type = "button"; cropSave.disabled = true; cropSave.addEventListener("click", saveCrop);
      const cropClose = node("button", "button button-quiet button-small", "收起"); cropClose.type = "button"; cropClose.addEventListener("click", () => { crop.hidden = true; clearCrop(); });
      cropBar.append(pages, cropSave, cropClose);
      const cropHint = node("div", "answer-crop-guidance");
      cropHint.append(node("span", "", "点第一角 → 移动鼠标 → 点第二角固定。Esc 取消，Ctrl＋滚轮缩放。"));
      const hideHint = node("button", "button button-quiet button-small", "以后不再提示"); hideHint.type = "button";
      const hintLabel = node("label", "answer-crop-hint-switch"); const hintToggle = node("input"); hintToggle.type = "checkbox"; hintLabel.append(hintToggle, document.createTextNode("显示画框提示")); cropBar.append(hintLabel);
      const setHint = show => { cropHint.hidden = !show; hintToggle.checked = show; try { root.localStorage.setItem("qb-crop-guidance", show ? "1" : "0"); } catch { /* Preference can remain local to this editor. */ } };
      hideHint.addEventListener("click", event => { event.stopPropagation(); setHint(false); }); hintToggle.addEventListener("change", () => setHint(hintToggle.checked)); cropHint.append(hideHint);
      const cropScroll = node("div", "answer-crop-scroll"); const cropSurface = node("div", "answer-crop-surface");
      const cropImage = node("img"); cropImage.alt = "原卷解析图来源页"; cropImage.draggable = false;
      cropImage.addEventListener("load", () => { if (dialog.open && !crop.hidden) cropImage.dataset.ready = "true"; });
      cropImage.addEventListener("error", () => { if (!dialog.open || crop.hidden) return; cropImage.dataset.ready = "false"; clearCrop(); notify("这页原卷暂时无法显示，请换页重试，或上传、粘贴解析图。", "error"); });
      const cropBox = node("div", "answer-crop-box"); cropBox.hidden = true;
      cropSurface.append(cropImage, cropBox); cropScroll.append(cropSurface, cropHint); crop.append(cropBar, cropScroll);
      const previewTitle = node("h4", "", "保存后的显示效果"); const preview = node("div", "paper answer-editor-preview");
      const aiDraft = node("details", "answer-ai-draft"); aiDraft.hidden = true; aiDraft.append(node("summary", "", "AI 初稿（当前编辑内容已保留）")); const aiPreview = node("div", "paper"); aiDraft.append(aiPreview);
      const origin = node("details", "answer-origin"); origin.append(node("summary", "", "原卷答案解析与历史")); const originBody = node("div", "paper");
      origin.addEventListener("toggle", () => { if (origin.open && current) S.render(originBody, current.origin, { node, QB, empty: "原卷未提供答案解析。" }); });
      const history = node("select"); history.setAttribute("aria-label", "已保存的答案解析历史");
      history.addEventListener("change", async () => {
        if (!history.value || !current) return;
        if (isDirty() && !await confirm({ title: "打开这版答案解析？", text: "当前未保存的编辑内容将换成所选历史版本，原卷内容保留。", ok: "打开历史版本" })) { history.value = ""; return; }
        const record = (current.history || []).find(value => value.id === history.value);
        if (record) { current.value = S.editable(record); current.ai_fields = []; current.ai_stale = false; current.dirty = true; renderFields(); }
      });
      origin.append(originBody, history);
      const footer = node("footer", "answer-editor-footer");
      const syncLabel = node("label", "answer-sync"); const sync = node("input"); sync.type = "checkbox"; sync.id = "answerEditorSync";
      syncLabel.append(sync, document.createTextNode("同时保存到题库"));
      const save = node("button", "button button-primary", "保存答案解析"); save.type = "button"; save.id = "answerEditorSave"; save.addEventListener("click", saveCurrent);
      footer.append(syncLabel, save);
      panel.append(status, question, fields, imageTools, figures, crop, aiDraft, previewTitle, preview, origin, footer);
      main.append(nav, panel); dialog.append(head, main); document.body.append(dialog);
      controls = { subtitle, nav, list, ai, aiStatus, copyTask, checkAi, cancelAi, taskDetails, taskText, status, question, questionBody, answer, analysis, figures, preview, origin, originBody, history, sync, save, upload, cropToggle, crop, pages, cropSave, cropImage, cropBox, cropSurface, cropScroll, cropHint, hintToggle, aiDraft, aiPreview };
      for (const input of [answer, analysis]) input.addEventListener("input", () => { if (!current) return; current.value.answer = answer.value; current.value.analysis = analysis.value; current.dirty = true; schedulePreview(); });
      dialog.addEventListener("paste", event => {
        const files = Array.from(event.clipboardData?.items || []).filter(item => item.kind === "file" && item.type.startsWith("image/")).map(item => item.getAsFile()).filter(Boolean);
        if (files.length) { event.preventDefault(); void uploadFiles(files); }
      });
      dialog.addEventListener("cancel", event => { event.preventDefault(); if (cropStart) { clearCrop(); return; } void closeEditor(); });
      dialog.addEventListener("keydown", event => {
        if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") { event.preventDefault(); void saveCurrent(); }
      });
      cropSurface.addEventListener("click", event => {
        if (cropImage.dataset.ready !== "true" || !cropImage.naturalWidth || uploading || event.button !== 0) return;
        const rect = cropImage.getBoundingClientRect();
        const point = [Math.max(0, Math.min(1000, (event.clientX - rect.left) * 1000 / rect.width)), Math.max(0, Math.min(1000, (event.clientY - rect.top) * 1000 / rect.height))];
        if (!cropStart) { cropStart = point; cropRange = null; controls.cropSave.disabled = true; updateCrop(point); }
        else { updateCrop(point); cropStart = null; controls.cropSave.disabled = !cropRange || cropRange[2] - cropRange[0] < 2 || cropRange[3] - cropRange[1] < 2; }
      });
      cropSurface.addEventListener("pointermove", event => {
        if (!cropStart) return;
        const rect = cropImage.getBoundingClientRect(); updateCrop([(event.clientX - rect.left) * 1000 / rect.width, (event.clientY - rect.top) * 1000 / rect.height]);
      });
      cropScroll.addEventListener("wheel", event => {
        if (!event.ctrlKey) return; event.preventDefault(); cropZoom = Math.max(.5, Math.min(4, cropZoom * Math.exp(-event.deltaY * .002))); cropSurface.style.width = `${cropZoom * 100}%`;
      }, { passive: false });
    }
    function setBusy() {
      const busy = loading || saving || uploading;
      controls.save.disabled = busy || !current || pendingSaves.has(current?.item.id); controls.answer.disabled = busy; controls.analysis.disabled = busy;
      controls.sync.disabled = busy || scope === "library"; controls.upload.disabled = busy; controls.cropToggle.disabled = busy || !current?.item.document_id;
      controls.ai.disabled = busy || queueing || !selection.size;
      controls.checkAi.disabled = polling; controls.cancelAi.disabled = cancelling || !selectedActiveJobs().length;
      controls.copyTask.hidden = !selectedActiveJobs().some(job => job.executor === "assistant");
      controls.history.disabled = busy;
      for (const row of controls.figures.children) for (const input of row.querySelectorAll?.("input, select, button") || []) input.disabled = busy || input.dataset?.unavailable === "1";
    }
    function selectedActiveJobs() { return [...jobs.values()].filter(job => selection.has(job.publication_id || job.publication) && jobsPending.has(job.status)); }
    function isDirty() { return Boolean(current && (current.dirty || S.signature(current.value) !== current.saved)); }
    function remember() { if (current) drafts.set(current.item.id, current); }
    async function open(supplied, options = {}) {
      init(); ++token; ++epoch; stopWaiting(); current = null; drafts = new Map(); jobs = new Map(); requestedIds = new Set(); handoff = null; watchedSince = Date.now(); queueNote = "";
      loading = saving = uploading = queueing = cancelling = polling = closing = pollAgain = false;
      controls.copyTask.hidden = controls.taskDetails.hidden = true; controls.taskText.value = "";
      items = supplied.map(item => ({ ...item })); scope = options.scope || "paper"; scopeContext = options.scopeContext ?? null; selection = new Set((options.selected || []).filter(id => items.some(item => item.id === id)));
      controls.subtitle.textContent = scope === "library" ? "保存到题库供以后使用，原卷答案保留。" : "保存后用于当前组卷；勾选后也可同步到题库。";
      controls.aiStatus.textContent = "AI 仅处理这里勾选的题，不改变全局自动生成开关。";
      returnFocus = document.activeElement; if (!dialog.open) dialog.showModal(); renderList();
      const item = items.find(item => item.id === options.focus) || items.find(item => S.completeness(item) !== "ready") || items[0];
      if (item) await choose(item);
    }
    async function choose(item) {
      if (saving || uploading) { notify("请等待当前保存或上传完成"); return; }
      remember(); root.clearTimeout(previewTimer); const requestToken = ++token; loading = true; current = null; controls.status.textContent = `正在读取${itemLabel(item)}的答案解析…`; controls.crop.hidden = true; clearCrop(); setBusy();
      try {
        let record = drafts.get(item.id);
        if (!record) {
          const revision = item.solution_revision === "origin" ? "origin" : item.solution?.id;
          const query = revision ? `?revision=${encodeURIComponent(revision)}` : "";
          const body = await api(endpoint(item) + query);
          const initial = S.editorInitial(item, body);
          record = { item, ...initial, loaded: S.signature(initial.value), dirty: false, hasSavedSolution: Boolean(body.solution || (item.solution_revision !== "origin" && item.solution)), base: body.base_revision, origin: body.origin || {}, history: body.history || [], aiJob: null };
        }
        if (requestToken !== token || !dialog.open) return;
        current = record; renderFields(); renderList();
        void pollJobs();
      } catch (error) {
        if (requestToken !== token || !dialog.open) return;
        controls.status.replaceChildren(node("span", "", error.message)); const retry = node("button", "button button-small", "重试读取"); retry.type = "button"; retry.addEventListener("click", () => choose(item)); controls.status.append(retry);
      } finally { if (requestToken === token) { loading = false; setBusy(); } }
    }
    function renderList() {
      controls.list.replaceChildren();
      items.forEach(item => {
        const row = node("div", `answer-list-row${current?.item.id === item.id ? " active" : ""}`);
        const box = node("input"); box.type = "checkbox"; box.checked = selection.has(item.id); box.setAttribute("aria-label", `AI 补充${itemLabel(item)}`);
        box.addEventListener("change", () => { if (box.checked) selection.add(item.id); else selection.delete(item.id); showHandoff(null); setBusy(); });
        const button = node("button", "answer-list-question"); button.type = "button";
        const record = current?.item.id === item.id ? current : drafts.get(item.id);
        const quality = record ? S.completeness({ solution: record.value }) : S.completeness(item); const labels = { missing: "缺答案解析", result_only: "只有结果，待补过程", ready: "已有解析" };
        button.append(node("strong", "", itemLabel(item)), node("span", `answer-quality ${quality}`, record?.ai_fields?.length ? "含现有 AI 初稿，保存后出卷" : labels[quality]));
        if (item.solution_needs_review) button.append(node("span", "answer-quality result_only", "题面有改动，解析待核对"));
        const job = jobs.get(item.id);
        if (job) {
          const state = jobDraftState(job, record, item);
          button.append(node("span", `answer-job-state ${job.status}`, state === "saved" ? "该初稿已保存" : state === "retained" ? "另有 AI 初稿可对照，已保存解析保留" : S.jobLabel(job)));
          if (job.error) button.append(node("span", "answer-job-error", job.error));
          if (job.executor === "api" && jobsPending.has(job.status) && job.timeout_at && Number.isFinite(Date.parse(job.timeout_at))) button.append(node("span", "answer-job-deadline", `超过 ${new Date(job.timeout_at).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })} 未完成将超时`));
        }
        button.addEventListener("click", () => choose(item)); row.append(box, button); controls.list.append(row);
      });
      setBusy();
    }
    function renderFields() {
      if (!current) return;
      controls.answer.value = current.value.answer; controls.analysis.value = current.value.analysis;
      controls.sync.checked = scope === "library"; controls.sync.disabled = scope === "library";
      controls.questionBody.replaceChildren();
      if (controls.question.open) QB.renderQuestion(controls.questionBody, current.item.content, { showNumber: false, showAnswer: "none" });
      controls.status.textContent = `${itemLabel(current.item)} · ${isDirty() ? "有未保存的编辑" : "原卷内容保留，修改后点保存"}`;
      controls.originBody.replaceChildren();
      if (controls.origin.open) S.render(controls.originBody, current.origin, { node, QB, empty: "原卷未提供答案解析。" });
      controls.history.replaceChildren(node("option", "", "查看已保存的解析版本"));
      (current.history || []).forEach((record, index) => { const option = node("option", "", `${index + 1}. ${record.created_at ? new Date(record.created_at).toLocaleString("zh-CN", { hour12: false }) : "已保存版本"}`); option.value = record.id; controls.history.append(option); });
      controls.history.hidden = !current.history?.length;
      controls.aiDraft.hidden = !current.suggestion;
      if (current.suggestion) S.render(controls.aiPreview, current.suggestion, { node, QB });
      renderFigures(); renderPreview();
    }
    function renderPreview() {
      if (!current) return;
      S.render(controls.preview, current.value, { node, QB });
      updateEditStatus();
    }
    function updateEditStatus() {
      if (!current) return;
      const quality = S.hasContent(current.value) ? (current.value.analysis.trim() || current.value.figures.length ? "" : " · 目前只有结果，建议补充详细解析") : " · 尚未补齐答案解析";
      const pending = pendingSaves.has(current.item.id) ? " · 上一笔保存仍在核对，可继续编辑，收到结果后恢复保存" : "";
      const ai = current.ai_fields?.length ? " · 已填入现有 AI 参考初稿，尚未核对；检查并保存后才用于出卷" : current.ai_stale ? ` · ${current.ai_error || "现有 AI 参考答案对应的题面已变化，未自动填入；请重新生成"}` : "";
      controls.status.textContent = `${itemLabel(current.item)}${isDirty() ? " · 未保存" : " · 已载入"}${quality}${ai}${pending}`;
    }
    function renderFigures() {
      controls.figures.replaceChildren();
      current.figureInputs = [];
      current.value.figures.forEach((figure, index) => {
        const row = node("div", "answer-image-row"); const image = node("img"); image.src = figure.url; image.alt = `解析配图 ${index + 1}`;
        const fields = node("div", "answer-image-fields");
        const widthLabel = node("label", "", "宽度（毫米）"); const width = node("input"); width.type = "number"; width.min = "5"; width.max = "178"; width.step = ".01"; width.value = figure.display_width; widthLabel.append(width);
        width.addEventListener("input", () => {
          const value = Number(width.value);
          current.dirty = true;
          if (String(width.value).trim() && Number.isFinite(value) && value >= 5 && value <= 178) { figure.display_width = Math.round(value * 100) / 100; schedulePreview(); }
        });
        width.addEventListener("change", () => { figure.display_width = Math.round(Math.max(5, Math.min(178, Number(width.value) || 20)) * 100) / 100; width.value = figure.display_width; current.dirty = true; renderPreview(); });
        const position = node("select"); position.setAttribute("aria-label", `解析图 ${index + 1} 插入位置`);
        [["before", "解析前"], ["after", "解析后"], ["paragraph", "指定段落后"]].forEach(([value, label]) => { const option = node("option", "", label); option.value = value; position.append(option); }); position.value = figure.position;
        const paragraph = node("input"); paragraph.type = "number"; paragraph.min = "1"; paragraph.max = "1001"; paragraph.value = (figure.paragraph || 0) + 1; paragraph.setAttribute("aria-label", "插入到第几段后"); paragraph.hidden = figure.position !== "paragraph";
        position.addEventListener("change", () => { figure.position = position.value; paragraph.hidden = figure.position !== "paragraph"; current.dirty = true; renderPreview(); });
        paragraph.addEventListener("input", () => {
          const value = Number(paragraph.value);
          current.dirty = true;
          if (String(paragraph.value).trim() && Number.isInteger(value) && value >= 1 && value <= 1001) { figure.paragraph = value - 1; schedulePreview(); }
        });
        paragraph.addEventListener("change", () => { figure.paragraph = Math.max(0, Math.min(1000, Math.floor(Number(paragraph.value) || 1) - 1)); paragraph.value = figure.paragraph + 1; current.dirty = true; renderPreview(); });
        const actions = node("div", "answer-image-tools");
        const move = direction => { const list = current.value.figures, to = index + direction; if (to < 0 || to >= list.length) return; [list[index], list[to]] = [list[to], list[index]]; current.dirty = true; renderFigures(); renderPreview(); };
        for (const [label, direction] of [["上移", -1], ["下移", 1]]) { const button = node("button", "button button-quiet button-small", label); button.type = "button"; button.disabled = index + direction < 0 || index + direction >= current.value.figures.length; button.dataset.unavailable = button.disabled ? "1" : "0"; button.addEventListener("click", () => move(direction)); actions.append(button); }
        const remove = node("button", "button button-quiet button-small", "移除"); remove.type = "button"; remove.addEventListener("click", () => { current.value.figures.splice(index, 1); current.dirty = true; renderFigures(); renderPreview(); }); actions.append(remove);
        fields.append(widthLabel, position, paragraph, actions); row.append(image, fields); controls.figures.append(row);
        current.figureInputs.push({ figure, width, paragraph });
      });
    }
    async function saveCurrent() {
      if (!current || loading || saving || uploading || pendingSaves.has(current.item.id)) return;
      root.clearTimeout(previewTimer);
      for (const { figure, width, paragraph } of current.figureInputs || []) {
        const size = Number(width.value), at = Number(paragraph.value);
        if (!String(width.value).trim() || !Number.isFinite(size) || size < 5 || size > 178) { notify("请把解析图宽度填写为 5–178 毫米，再保存。", "error"); width.focus(); return; }
        if (figure.position === "paragraph" && (!String(paragraph.value).trim() || !Number.isInteger(at) || at < 1 || at > 1001)) { notify("请填写完整的解析段落序号，再保存。", "error"); paragraph.focus(); return; }
        figure.display_width = Math.round(size * 100) / 100;
        if (figure.position === "paragraph") figure.paragraph = at - 1;
      }
      const record = current, requestToken = token, requestEpoch = epoch, savedScope = scope, savedContext = scopeContext, value = S.payload(record.value), sync = scope === "library" || controls.sync.checked;
      saving = true; pendingSaves.add(record.item.id); setBusy(); controls.status.textContent = "正在保存答案解析…";
      try {
        const body = await post(endpoint(record.item), { ...value, base_revision: record.base ?? null, sync_library: sync }, { keepAfterClose: true });
        record.item.solution = body.solution; record.item.solution_revision = body.solution.id; record.value = S.editable(body.solution); record.saved = record.loaded = S.signature(record.value); record.dirty = false; record.hasSavedSolution = true; record.ai_fields = []; record.ai_stale = false;
        if (jobDraftState({ status: "done", result: record.suggestion }, record) === "saved") record.suggestion = null;
        record.base = body.base_revision ?? (sync ? body.solution.id : record.base);
        record.history.unshift(body.solution);
        onSaved(record.item, body.solution, { sync, scope: savedScope, scopeContext: savedContext });
        if (requestToken !== token || !dialog.open || current !== record) { notify(`返回后已确认${itemLabel(record.item)}的答案解析保存成功。`); return; }
        const item = items.find(item => item.id === record.item.id); if (item) { item.solution = body.solution; item.solution_revision = body.solution.id; }
        renderFields(); renderList(); showJobSummary(); controls.status.textContent = sync ? "已保存到题库，原卷答案解析保留。" : "已保存，用于当前组卷。保存组卷草稿后，下次可继续。";
      } catch (error) { if (requestToken === token && dialog.open) controls.status.textContent = error.message; notify(requestEpoch === epoch ? error.message : `${itemLabel(record.item)}的保存尚未确认：${error.message} 下次打开后请核对已保存版本。`, "error"); }
      finally { pendingSaves.delete(record.item.id); if (requestEpoch === epoch) saving = false; if (dialog.open) { setBusy(); if (requestEpoch !== epoch && current?.item.id === record.item.id) updateEditStatus(); } }
    }
    async function uploadFiles(files) {
      if (!current || saving || uploading || loading || !files.length) return;
      const record = current, requestToken = token, requestEpoch = epoch; uploading = true; setBusy();
      try {
        for (const file of files) {
          if (!/image\/(png|jpeg|webp)/.test(file.type)) throw new Error("请选择 PNG、JPEG 或 WebP 图片");
          if (file.size > 12 * 1024 * 1024) throw new Error("解析图较大，请压缩到 12 MB 以内再上传");
          const form = new FormData(); form.append("image", file, file.name || "解析图.png");
          const body = await api(`/api/library/${encodeURIComponent(record.item.id)}/solution-images`, { method: "POST", headers: { "X-QB-Request": "1" }, body: form });
          if (requestToken !== token || !dialog.open || current !== record) return;
          record.value.figures.push(...S.figuresOf({ figures: [body.figure] })); record.dirty = true; renderFigures(); renderPreview();
        }
      } catch (error) { if (requestEpoch === epoch && dialog.open) notify(error.message, "error"); }
      finally { if (requestEpoch === epoch) { uploading = false; setBusy(); } }
    }
    let cropStart = null, cropRange = null, cropZoom = 1;
    function clearCrop() { cropStart = cropRange = null; if (controls) { controls.cropBox.hidden = true; controls.cropSave.disabled = true; } }
    function updateCrop(point) {
      point = point.map(value => Math.max(0, Math.min(1000, value)));
      cropRange = [Math.min(cropStart[0], point[0]), Math.min(cropStart[1], point[1]), Math.max(cropStart[0], point[0]), Math.max(cropStart[1], point[1])];
      const [x0, y0, x1, y1] = cropRange; Object.assign(controls.cropBox.style, { left: `${x0 / 10}%`, top: `${y0 / 10}%`, width: `${(x1 - x0) / 10}%`, height: `${(y1 - y0) / 10}%` }); controls.cropBox.hidden = false;
    }
    async function openCrop() {
      if (!current?.item.document_id || loading || saving || uploading) return;
      const requestToken = token, record = current; controls.crop.hidden = false; controls.pages.replaceChildren(); controls.cropImage.dataset.ready = "false"; clearCrop();
      let show = true; try { show = root.localStorage.getItem("qb-crop-guidance") !== "0"; } catch { /* Default guidance remains visible. */ }
      controls.cropHint.hidden = !show; controls.hintToggle.checked = show;
      try {
        const body = await api(`/api/papers/${encodeURIComponent(record.item.document_id)}`);
        if (requestToken !== token || !dialog.open || current !== record) return;
        for (const page of body.paper?.pages || body.pages || []) { const option = node("option", "", `第 ${page.page_idx + 1} 页`); option.value = page.page_idx; controls.pages.append(option); }
        if (!controls.pages.children.length) throw new Error("原卷页暂时无法打开，请使用上传或粘贴图片");
        const first = record.item.content?.sources?.find(source => Number.isInteger(source.page_idx)); if (first) controls.pages.value = first.page_idx;
        showCropPage();
      } catch (error) { if (requestToken === token && dialog.open) notify(error.message, "error"); }
    }
    function showCropPage() { clearCrop(); cropZoom = 1; controls.cropSurface.style.width = "100%"; controls.cropImage.dataset.ready = "false"; controls.cropImage.src = `/api/documents/${encodeURIComponent(current.item.document_id)}/pages/${Number(controls.pages.value)}/preview`; }
    async function saveCrop() {
      if (!current || !cropRange || cropStart || saving || uploading || loading) return;
      const record = current, requestToken = token, requestEpoch = epoch, bbox = cropRange.map(Math.round); uploading = true; setBusy(); controls.cropSave.disabled = true;
      try {
        const body = await post(`/api/library/${encodeURIComponent(record.item.id)}/solution-images`, { page_idx: Number(controls.pages.value), bbox });
        if (requestToken !== token || !dialog.open || current !== record) return;
        record.value.figures.push(...S.figuresOf({ figures: [body.figure] })); record.dirty = true; renderFigures(); renderPreview(); clearCrop(); controls.crop.hidden = true;
      } catch (error) { if (requestToken === token && dialog.open) { notify(error.message, "error"); controls.cropSave.disabled = false; } }
      finally { if (requestEpoch === epoch) { uploading = false; setBusy(); } }
    }
    async function queueSelected() {
      if (!selection.size || saving || loading || uploading || queueing) return;
      const ids = items.filter(item => selection.has(item.id)).map(item => item.id), requestEpoch = epoch;
      ids.forEach(id => requestedIds.add(id));
      queueing = true;
      controls.ai.disabled = true;
      try {
        const body = await post("/api/library/jobs", { kind: "answer", ids, solution_scope: true });
        if (requestEpoch !== epoch || !dialog.open) return;
        for (const job of body.jobs || []) jobs.set(job.publication_id || job.publication, job);
        handoff = body.assistant_handoff || null; watchedSince = Date.now();
        queueNote = body.skipped ? `${body.skipped} 题未加入任务，请检查该题是否仍在正式题库。` : "";
        showHandoff(handoff);
        controls.aiStatus.textContent = body.executor === "assistant" ? "等待当前助手处理。点“复制助手任务说明”，粘贴给豆包或当前 AI 助手；收到初稿后检查并保存，才会用于出卷。" : `已提交所选 ${ids.length} 题，初稿完成后显示在编辑区。检查并保存后才用于出卷。`;
        renderList();
        void pollJobs();
      } catch (error) { if (requestEpoch === epoch && dialog.open) { notify(error.message, "error"); controls.aiStatus.textContent = error.message; } }
      finally { if (requestEpoch === epoch) { queueing = false; setBusy(); } }
    }
    function showHandoff(value) {
      handoff = value?.text ? value : null;
      controls.taskText.value = handoff?.text || ""; controls.taskDetails.hidden = !handoff;
    }
    async function copyHandoff() {
      const ids = [...new Set(selectedActiveJobs().filter(job => job.executor === "assistant").map(job => job.publication_id || job.publication))];
      if (!ids.length) return;
      const session = epoch;
      try {
        // Ask for only the selected publications, never hand off unselected jobs.
        const body = await api(`/api/library/jobs?ids=${encodeURIComponent(ids.join(","))}&solution_scope=true`);
        if (session !== epoch || !dialog.open) return;
        showHandoff(body.assistant_handoff);
        if (!handoff) throw new Error("暂时没有可交给助手的任务说明，请检查初稿或重新提交所选题。");
        try { await root.navigator.clipboard.writeText(handoff.text); if (session === epoch && dialog.open) controls.aiStatus.textContent = "任务说明已复制。粘贴给当前助手，完成后回到这里检查初稿并保存。"; }
        catch { if (session !== epoch || !dialog.open) return; controls.taskDetails.open = true; controls.taskText.focus(); controls.taskText.select(); controls.aiStatus.textContent = "请复制下方完整任务说明，粘贴给当前助手。"; }
      } catch (error) { if (session === epoch && dialog.open) { controls.aiStatus.textContent = error.message; notify(error.message, "error"); } }
    }
    async function cancelSelected() {
      const active = selectedActiveJobs(); if (!active.length || cancelling) return;
      const session = epoch;
      if (!await confirm({ title: `取消所选 ${active.length} 个 AI 任务？`, text: "当前手工编辑、已有初稿和已保存的解析都会保留。取消后可重新提交。", ok: "取消任务" }) || session !== epoch || !dialog.open) return;
      cancelling = true; setBusy();
      try {
        const body = await post("/api/library/jobs/cancel", { ids: active.map(job => job.id), solution_scope: true });
        if (session !== epoch || !dialog.open) return;
        for (const job of body.jobs || []) jobs.set(job.publication_id || job.publication, job);
        controls.aiStatus.textContent = `已取消 ${body.cancelled || 0} 个任务。编辑内容保留，需要时重新勾选并提交。`;
        showHandoff(null); renderList(); void pollJobs();
      } catch (error) { if (session === epoch && dialog.open) controls.aiStatus.textContent = error.message; }
      finally { if (session === epoch) { cancelling = false; setBusy(); } }
    }
    function showJobSummary() {
      const values = [...jobs.values()]; if (!values.length) { controls.aiStatus.textContent = requestedIds.size ? "暂未查到这批任务的状态。点“检查初稿”重新查询，当前编辑内容保留。" : "勾选后可让 AI 补充。使用当前助手时，请复制任务说明交给助手；初稿保存后才出卷。"; return; }
      const waiting = values.filter(job => job.executor === "assistant" && jobsPending.has(job.status) && job.status !== "running").length;
      const running = values.filter(job => jobsPending.has(job.status) && (job.executor !== "assistant" || job.status === "running")).length;
      const finished = values.filter(job => job.result && jobsSucceeded.has(job.status));
      const states = finished.map(job => { const id = job.publication_id || job.publication; return jobDraftState(job, current?.item.id === id ? current : drafts.get(id), items.find(item => item.id === id)); });
      const unsaved = states.filter(state => state === "draft").length, saved = states.filter(state => state === "saved").length, retained = states.filter(state => state === "retained").length;
      const failed = values.filter(job => job.status === "failed" || ["cancelled", "timed_out"].includes(job.status));
      const messages = [];
      if (waiting) messages.push(`${waiting} 题等待当前助手领取：复制任务说明后粘贴给豆包或当前助手`);
      if (running) messages.push(`${running} 题正在排队或处理，可取消所选任务`);
      if (unsaved) messages.push(`${unsaved} 题初稿已到，检查并保存后才出卷`);
      if (saved) messages.push(`${saved} 题初稿已保存，可用于出卷`);
      if (retained) messages.push(`${retained} 题另有 AI 初稿可对照，已保存解析保留`);
      if (failed.length) messages.push(`${failed.length} 题未完成，勾选可重试${failed[0].error ? `：${failed[0].error}` : ""}`);
      controls.aiStatus.textContent = messages.join("；") + "。" + queueNote;
    }
    async function pollJobs() {
      root.clearTimeout(pollTimer); const session = epoch;
      if (!dialog?.open || !items.length) return;
      if (polling) { pollAgain = true; return; }
      polling = true; setBusy();
      try {
        const body = await api(`/api/library/jobs?ids=${encodeURIComponent(items.map(item => item.id).join(","))}&solution_scope=true`);
        if (!dialog.open || session !== epoch) return;
        const seen = new Set(), fresh = new Map();
        for (const received of body.jobs || []) {
          let job = received;
          const id = job.publication_id || job.publication; if (!items.some(item => item.id === id)) continue;
          if (seen.has(id)) continue; seen.add(id);
          const known = jobs.get(id);
          if (known?.id === job.id && (known.cancelled || known.timed_out || ["cancelled", "timed_out"].includes(known.terminal_reason) || (!jobsPending.has(known.status) && jobsPending.has(job.status)))) job = known;
          fresh.set(id, job);
          const record = current?.item.id === id ? current : drafts.get(id);
          if (job.result && record && record.aiJob !== job.id && jobsSucceeded.has(job.status)) {
            record.aiJob = job.id;
            if (jobDraftState(job, record) === "saved") { record.suggestion = null; if (current === record) controls.aiDraft.hidden = true; }
            else if (requestedIds.has(id) && !record.hasSavedSolution && !record.dirty && S.signature(record.value) === record.loaded) {
              record.value.answer = String(job.result.answer ?? ""); record.value.analysis = String(job.result.analysis ?? ""); record.dirty = true;
              record.ai_fields = ["answer", "analysis"]; record.ai_stale = false;
              if (current === record) { renderFields(); controls.status.textContent = "AI 初稿已显示，请检查后保存。"; }
            } else { record.suggestion = job.result; if (current === record) { controls.aiDraft.hidden = false; S.render(controls.aiPreview, job.result, { node, QB }); } }
          }
        }
        jobs = fresh; renderList(); showJobSummary();
        const active = [...jobs.values()].filter(job => jobsPending.has(job.status));
        if (active.length && Date.now() - watchedSince < MAX_WATCH) pollTimer = root.setTimeout(pollJobs, active.some(job => job.executor !== "assistant" || job.status === "running") ? 4000 : 30000);
        else if (active.length) controls.aiStatus.textContent += " 自动检查已暂停，点“检查初稿”可继续；页面不会替助手生成答案。";
      } catch (error) { if (dialog.open && session === epoch) { controls.aiStatus.textContent = `${error.message}。本地编辑内容保留，稍后重新勾选可重试。`; } }
      finally { if (session === epoch) { polling = false; setBusy(); if (pollAgain && dialog.open) { pollAgain = false; void pollJobs(); } } }
    }
    async function closeEditor() {
      if (closing || !dialog.open) return;
      closing = true; const session = epoch, hasPendingSave = saving;
      remember();
      const dirty = [...drafts.values()].some(record => record.dirty || S.signature(record.value) !== record.saved);
      if ((dirty || hasPendingSave) && !await confirm({ title: hasPendingSave ? "返回并继续核对保存结果？" : "返回并保留已保存的解析？", text: hasPendingSave ? "保存请求已发出，返回不会撤销保存。收到结果后会提示是否保存成功；其他未保存的编辑不会用于出卷。" : "未保存的编辑内容不会用于出卷。要继续修改，请取消返回。", ok: "返回" })) { if (session === epoch) closing = false; return; }
      if (session !== epoch || !dialog.open) return;
      ++token; ++epoch; stopWaiting(); dialog.close(); clearCrop(); controls.crop.hidden = true; controls.cropImage.dataset.ready = "false"; controls.cropImage.removeAttribute?.("src"); loading = saving = uploading = queueing = cancelling = polling = closing = false;
      if (hasPendingSave && pendingSaves.size) notify("已返回，仍在核对刚才的保存结果。请留意保存成功或未完成的提示。");
      if (returnFocus?.isConnected) returnFocus.focus({ preventScroll: true });
    }
    root.addEventListener?.("beforeunload", event => {
      if (!pendingSaves.size && (!dialog?.open || (!saving && !uploading && !isDirty() && ![...drafts.values()].some(record => record.dirty || S.signature(record.value) !== record.saved)))) return;
      event.preventDefault(); event.returnValue = "";
    });
    return Object.freeze({ open, isOpen: () => Boolean(dialog?.open) });
  }
  root.LibraryAnswerEditor = Object.freeze({ create });
})(typeof window === "undefined" ? globalThis : window);
