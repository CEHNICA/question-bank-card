(() => {
  "use strict";
  const TYPES = { single_choice: "单选题", multiple_choice: "多选题", fill_blank: "填空题", true_false: "判断题", free_response: "解答题" };
  const KEYS = ["A", "B", "C", "D", "E"];
  function create({ node, QB, notify, confirm, onSaved }) {
    let dialog, form, preview, status, save, fields, record, baseline, busy = false, epoch = 0, controller, focus;
    const pendingSaves = new Set();
    const signature = value => JSON.stringify(value);
    function payload() {
      return { revision: record.revision, content_hash: record.content_hash,
        body_mode: fields.mode.value, stem: fields.stem.value, question_type: fields.type.value,
        options: Object.fromEntries(KEYS.filter(key => fields.options[key].value.trim()).map(key => [key, fields.options[key].value])),
        figures: fields.figures.filter(value => !value.removed).map(value => ({ file: value.file, slot: value.select.value })) };
    }
    function update() {
      if (!record) return;
      const value = payload(), original = record.publication.content;
      fields.stem.disabled = value.body_mode === "source_image" || busy;
      for (const input of Object.values(fields.options)) input.disabled = value.body_mode === "source_image" || busy;
      const selected = new Map(value.figures.map(figure => [figure.file, figure.slot]));
      const content = { ...original, ...value, figures: (original.figures || []).filter(figure => selected.has(figure.file)).map(figure => ({ ...figure, slot: selected.get(figure.file) })) };
      QB.renderQuestion(preview, content, { showNumber: false, showAnswer: "none" });
    }
    async function requestClose() {
      const session = epoch;
      if (busy && !await confirm({ title: "返回并继续核对保存结果？", text: "保存请求已发出，返回不会撤销保存。收到结果后会提示；本次窗口可以先关闭。", ok: "返回", cancel: "继续等待" })) return;
      if (session !== epoch) return;
      if (!busy && record && signature(payload()) !== baseline && !await confirm({ title: "题目修改还没保存", text: "返回会放弃本次修改，题库原版本保留。", ok: "放弃修改", cancel: "继续修改" })) return;
      if (session !== epoch) return;
      ++epoch; controller?.abort(); dialog.close(); busy = false;
      if (focus?.isConnected) focus.focus({ preventScroll: true });
    }
    function setup() {
      if (dialog) return;
      dialog = node("dialog", "library-question-editor"); dialog.id = "libraryQuestionEditor";
      const heading = node("header", "library-question-editor-head");
      heading.append(node("h2", "", "修改题目"));
      const back = node("button", "button button-quiet", "返回"); back.type = "button"; back.addEventListener("click", requestClose);
      heading.append(back);
      status = node("p", "library-question-editor-status"); status.setAttribute("role", "status");
      form = node("form", "library-question-editor-form");
      preview = node("div", "paper library-question-editor-preview");
      const body = node("div", "library-question-editor-body"); body.append(form, preview);
      const footer = node("footer", "library-question-editor-footer");
      footer.append(node("span", "muted", "保存后更新题库并保留旧版；原卷与已保存组卷的题目版本保留。"));
      save = node("button", "button button-primary", "保存题目"); save.type = "button"; save.addEventListener("click", submit);
      footer.append(save); dialog.append(heading, status, body, footer);
      form.addEventListener("submit", event => { event.preventDefault(); void submit(); });
      form.addEventListener("input", update); form.addEventListener("change", update);
      dialog.addEventListener("cancel", event => { event.preventDefault(); void requestClose(); });
      document.body.append(dialog);
    }
    function field(label, input) {
      const row = node("label", "library-question-editor-field"); row.append(node("span", "", label), input); form.append(row); return input;
    }
    function populate(data) {
      record = data; form.replaceChildren(); fields = { options: {}, figures: [] };
      fields.type = field("题型", node("select"));
      Object.entries(TYPES).forEach(([key, label]) => { const option = node("option", "", label); option.value = key; fields.type.append(option); });
      fields.type.value = data.question_type;
      fields.mode = field("题目正文", node("select"));
      [["text", "文字题面"], ["source_image", "保留原图题面"]].forEach(([key, label]) => { const option = node("option", "", label); option.value = key; option.disabled = key === "source_image" && !data.publication.content.question_images?.length; fields.mode.append(option); });
      fields.mode.value = data.body_mode || "text";
      fields.stem = field("题干（公式可用 $…$）", node("textarea")); fields.stem.rows = 8; fields.stem.value = data.stem || ""; fields.stem.maxLength = 20000; fields.stem.spellcheck = false;
      KEYS.forEach(key => { const input = field("选项 " + key, node("textarea")); input.rows = 2; input.maxLength = 4000; input.value = data.options?.[key] || ""; input.spellcheck = false; fields.options[key] = input; });
      if (data.figures?.length) {
        form.append(node("h3", "", "配图"));
        data.figures.forEach(figure => {
          const row = node("div", "library-question-editor-figure"), image = node("img"); image.src = figure.url; image.alt = "已保存配图";
          const select = node("select"); select.setAttribute("aria-label", "配图位置");
          ["stem", ...KEYS].forEach(key => { const option = node("option", "", key === "stem" ? "题干" : "选项 " + key); option.value = key; select.append(option); }); select.value = figure.slot;
          const item = { file: figure.file, select, removed: false }; fields.figures.push(item);
          const remove = node("button", "button button-small", "移除"); remove.type = "button";
          remove.addEventListener("click", () => { if (busy) return; item.removed = !item.removed; remove.textContent = item.removed ? "恢复" : "移除"; row.classList.toggle("removed", item.removed); update(); });
          row.append(image, select, remove); form.append(row);
        });
      }
      baseline = signature(payload()); update();
    }
    async function open(item) {
      setup(); if (busy) return;
      if (dialog.open && record && signature(payload()) !== baseline && !await confirm({ title: "切换题目？", text: "本次未保存的题面修改会放弃。", ok: "切换" })) return;
      const session = ++epoch; controller?.abort(); controller = new AbortController(); record = null; form.replaceChildren(); preview.replaceChildren(); save.disabled = true; status.textContent = "正在载入已保存题目…";
      if (!dialog.open) { focus = document.activeElement; dialog.showModal(); }
      const readController = controller, deadline = setTimeout(() => readController.abort(), 15000);
      try {
        const response = await fetch("/api/library/" + encodeURIComponent(item.id) + "/question", { cache: "no-store", signal: controller.signal });
        const data = await response.json(); if (session !== epoch || !dialog.open) return;
        if (!response.ok) throw Error(data.error || "题目暂时无法编辑");
        if (!data.editable) throw Error(data.reason || "请先处理来源题卡中的新修改，再编辑题库版本");
        populate(data); save.disabled = false; status.textContent = "修改后点保存即可，题库会保留这次修改前的版本。";
      } catch (error) { if (session === epoch && dialog.open) status.textContent = error.name === "AbortError" ? "读取超时，可返回后重新打开。" : error.message; }
      finally { clearTimeout(deadline); }
    }
    async function submit() {
      if (busy || !record || save.disabled) return;
      if (signature(payload()) === baseline) { await requestClose(); return; }
      const session = epoch, original = record.publication, id = original.id, value = payload();
      if (pendingSaves.has(id)) { notify("这道题还有保存结果待核对，请稍候再保存。"); return; }
      pendingSaves.add(id); busy = true; save.disabled = true;
      const saveController = new AbortController(), deadline = setTimeout(() => saveController.abort(), 30000);
      form.querySelectorAll("input, textarea, select, button").forEach(input => { input.disabled = true; }); status.textContent = "正在保存题目…";
      try {
        const response = await fetch("/api/library/" + encodeURIComponent(id) + "/question", { method: "POST", headers: { "Content-Type": "application/json", "X-QB-Request": "1" }, body: JSON.stringify(value), signal: saveController.signal });
        const data = await response.json(); if (!response.ok) throw Error(data.error || "题目没有保存");
        if (session === epoch) { baseline = signature(value); dialog.close(); ++epoch; busy = false; }
        onSaved?.(original, data.publication); notify(data.created ? "题目已更新，旧版已保留。" : "题目已保存。", "success");
      } catch (error) {
        const message = error.name === "AbortError" ? "保存结果尚未确认，请重新载入核对；返回不会撤销已接收的保存。" : error.message;
        if (session === epoch) { status.textContent = message + "；本次输入仍保留。"; busy = false; save.disabled = false; form.querySelectorAll("input, textarea, select, button").forEach(input => { input.disabled = false; }); update(); }
        else notify(message, "error");
      } finally { clearTimeout(deadline); pendingSaves.delete(id); }
    }
    window.addEventListener("beforeunload", event => { if (pendingSaves.size || dialog?.open && record && signature(payload()) !== baseline) { event.preventDefault(); event.returnValue = ""; } });
    return Object.freeze({ open, isOpen: () => Boolean(dialog?.open) });
  }
  window.LibraryQuestionEditor = Object.freeze({ create });
})();
