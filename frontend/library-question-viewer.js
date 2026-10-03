(function (root, factory) {
  "use strict";
  const value = factory(root);
  if (typeof module === "object" && module.exports) module.exports = value;
  else root.LibraryQuestionViewer = value;
})(typeof window === "object" ? window : globalThis, function (root) {
  "use strict";

  function afterPaint(callback) {
    if (root.requestAnimationFrame) root.requestAnimationFrame(() => root.requestAnimationFrame(() => root.setTimeout(callback, 0)));
    else root.setTimeout(callback, 0);
  }

  function create({ node, QB, solutions }) {
    const doc = root.document;
    let dialog, controls, epoch = 0, reading = null, readTimer = null, returnState = null, zoom = 100;
    function button(label, id, callback) {
      const value = node("button", "button button-small", label); value.id = id; value.type = "button";
      value.addEventListener("click", callback); return value;
    }
    function build() {
      if (dialog) return;
      dialog = node("dialog", "question-viewer-dialog"); dialog.id = "libraryQuestionViewer";
      dialog.setAttribute("aria-labelledby", "questionViewerTitle");
      const head = node("header", "question-viewer-head"), metadata = node("div", "question-viewer-metadata");
      const title = node("h2"); title.id = "questionViewerTitle";
      const source = node("p", "question-viewer-source"), detail = node("p", "question-viewer-detail");
      metadata.append(title, source, detail);
      const exit = button("返回题库 · Esc", "questionViewerClose", close);
      head.append(metadata, exit);
      const tools = node("div", "question-viewer-tools"); tools.setAttribute("aria-label", "看题缩放");
      const percent = node("output", "question-viewer-percent", "100%"); percent.setAttribute("aria-live", "polite");
      const fit = button("适合宽度", "questionViewerFit", () => { controls.content.classList.remove("native-images"); setZoom(100); });
      const native = button("图片原尺寸", "questionViewerNative", () => { controls.content.classList.add("native-images"); setZoom(100); });
      const less = button("−", "questionViewerZoomOut", () => setZoom(zoom - 25)); less.setAttribute("aria-label", "缩小题目");
      const more = button("＋", "questionViewerZoomIn", () => setZoom(zoom + 25)); more.setAttribute("aria-label", "放大题目");
      tools.append(fit, native, less, percent, more, node("span", "question-viewer-tip", "Ctrl + 滚轮缩放"));
      const viewport = node("div", "question-viewer-viewport"); viewport.tabIndex = 0; viewport.setAttribute("aria-label", "完整题目与答案");
      const content = node("div", "question-viewer-content");
      const question = node("article", "paper question-viewer-question");
      const answers = node("section", "question-viewer-answers"); answers.setAttribute("aria-label", "答案与解析");
      const status = node("p", "question-viewer-status"); status.id = "questionViewerStatus"; status.setAttribute("role", "status");
      content.append(question, answers, status); viewport.append(content); dialog.append(head, tools, viewport); doc.body.append(dialog);
      controls = { title, source, detail, exit, percent, native, less, more, viewport, content, question, answers, status };
      dialog.addEventListener("cancel", event => { event.preventDefault(); close(); });
      dialog.addEventListener("click", event => { if (event.target === dialog) close(); });
      dialog.addEventListener("close", finishClose);
      viewport.addEventListener("wheel", event => {
        if (!event.ctrlKey || !event.deltaY) return;
        event.preventDefault(); setZoom(zoom + (event.deltaY < 0 ? 10 : -10));
      }, { passive: false });
    }
    function setZoom(value) {
      zoom = Math.max(50, Math.min(300, Math.round(value)));
      controls.content.style.zoom = zoom / 100;
      controls.percent.textContent = `${zoom}%`;
      controls.less.disabled = zoom <= 50; controls.more.disabled = zoom >= 300;
    }
    function stopRead() {
      root.clearTimeout(readTimer); readTimer = null;
      reading?.abort(); reading = null;
    }
    function addAnswer(label, value, className = "") {
      if (!solutions.hasContent(value)) return;
      const part = node("section", `question-viewer-answer ${className}`.trim());
      part.append(node("h3", "", label));
      const body = node("div"); solutions.render(body, value, { node, QB, empty: "原卷未提供" });
      part.append(body); controls.answers.append(part);
    }
    function renderAnswers(item, verified = null) {
      controls.answers.replaceChildren();
      // Only explicit answer/analysis assets belong to the original solution;
      // stem/option diagrams and source-image crops appear once in the question.
      const originalFigures = (item.content?.figures || []).filter(figure => ["answer", "analysis"].includes(figure.slot));
      addAnswer("原卷答案与解析", { answer: item.content?.answer, analysis: item.content?.analysis, figures: originalFigures });
      const saved = verified ? verified.solution : item.solution;
      if (saved) addAnswer("题库保存的答案与解析", saved);
      // The list's legacy extras do not prove a current fingerprint. Only the
      // read-only solution endpoint validates an existing AI draft.
      if (verified?.ai_answer && !verified.ai_answer_stale) addAnswer("AI 参考答案与解析 · 未核对", verified.ai_answer, "question-viewer-ai");
      if (!controls.answers.children.length) controls.answers.append(node("p", "helper", "原卷未提供答案与解析。"));
    }
    async function open(item, options = {}) {
      build(); stopRead(); const session = ++epoch;
      if (!dialog.open) {
        returnState = { focus: options.returnFocus || doc.activeElement, x: root.scrollX || 0, y: root.scrollY || 0, overflow: doc.body.style.overflow || "" };
        dialog.showModal(); doc.body.style.overflow = "hidden";
      }
      controls.title.textContent = `第 ${item.number ?? "?"} 题`;
      controls.source.textContent = String(item.source_filename || "原卷");
      controls.detail.textContent = [QB.TYPE_NAMES?.[item.question_type] || item.question_type, item.origin ? `题源：${item.origin}` : "", item.version ? `第 ${item.version} 版` : ""].filter(Boolean).join(" · ");
      controls.content.classList.remove("native-images"); setZoom(100);
      controls.viewport.scrollTop = controls.viewport.scrollLeft = 0;
      QB.renderQuestion(controls.question, item.content || {}, { showNumber: false, showAnswer: "none", imageLoading: "eager" });
      renderAnswers(item);
      controls.native.hidden = !(item.content?.figures?.length || item.content?.question_images?.length || item.solution?.figures?.length);
      controls.status.textContent = "正在检查已有参考答案…"; controls.exit.focus({ preventScroll: true });
      const controller = new root.AbortController(); reading = controller; let timedOut = false;
      const timer = root.setTimeout(() => { timedOut = true; controller.abort(); }, 15000); readTimer = timer;
      try {
        const response = await root.fetch(`/api/library/${encodeURIComponent(item.id)}/solution`, { cache: "no-store", signal: controller.signal });
        const value = await response.json();
        if (session !== epoch || !dialog.open) return;
        if (controller.signal.aborted) throw new Error("参考答案检查已停止");
        if (!response.ok) throw new Error(value.error || "参考答案未能读取");
        renderAnswers(item, value);
        controls.native.hidden = !(item.content?.figures?.length || item.content?.question_images?.length || value.solution?.figures?.length);
        controls.status.textContent = value.ai_answer_stale ? "题面已有更新，旧 AI 参考答案未展示。" : "";
      } catch (error) {
        if (session !== epoch || !dialog.open) return;
        controls.status.textContent = timedOut ? "参考答案检查超时，完整题目与原卷答案仍可查看。" : "已有参考答案暂时无法检查，完整题目与原卷答案保留。";
      } finally {
        root.clearTimeout(timer); if (reading === controller) { reading = null; readTimer = null; }
      }
    }
    function finishClose() {
      ++epoch; stopRead(); const session = epoch, previous = returnState; returnState = null;
      if (previous) {
        doc.body.style.overflow = previous.overflow;
        const target = previous.focus?.isConnected === false ? null : previous.focus;
        target?.focus?.({ preventScroll: true }); root.scrollTo?.(previous.x, previous.y);
      }
      // Paint the underlying card before releasing large images/math. A quick
      // reopen must not be cleared by an earlier close's callback.
      afterPaint(() => { if (session === epoch && !dialog.open) { controls.question.replaceChildren(); controls.answers.replaceChildren(); controls.status.textContent = ""; } });
    }
    function close() { if (dialog?.open) dialog.close(); }
    return { open, close, isOpen: () => Boolean(dialog?.open) };
  }

  function mountFocus({ node, host }) {
    if (!host) return null;
    const button = node("button", "button button-quiet button-small library-focus-toggle", "专注浏览");
    button.type = "button"; button.id = "libraryFocusBrowse"; button.setAttribute("aria-pressed", "false");
    button.title = "暂时收起筛选和试题篮，选题与草稿会保留";
    const set = active => {
      root.document.body.classList.toggle("library-focus-mode", Boolean(active));
      button.textContent = active ? "退出专注浏览" : "专注浏览";
      button.setAttribute("aria-pressed", String(Boolean(active)));
    };
    button.addEventListener("click", () => set(!root.document.body.classList.contains("library-focus-mode")));
    host.append(button); return { button, set };
  }
  return { create, mountFocus };
});
