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
    let navigation = null, position = null, navigating = false, navEpoch = 0, navController = null, navTimer = null;
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
      const navigationBar = node("div", "question-viewer-navigation"); navigationBar.setAttribute("role", "group"); navigationBar.setAttribute("aria-label", "按当前结果逐题浏览");
      const previous = button("← 上一题", "questionViewerPrevious", () => navigate(-1));
      const next = button("下一题 →", "questionViewerNext", () => navigate(1));
      const place = node("output", "question-viewer-position"); place.setAttribute("aria-live", "polite");
      const navStatus = node("p", "question-viewer-navigation-status"); navStatus.setAttribute("role", "status");
      navigationBar.append(previous, place, next, navStatus); head.append(metadata, navigationBar, exit);
      const tools = node("div", "question-viewer-tools"); tools.setAttribute("aria-label", "看题缩放");
      const percent = node("output", "question-viewer-percent", "100%"); percent.setAttribute("aria-live", "polite");
      const fit = button("适合宽度", "questionViewerFit", () => { controls.content.classList.remove("native-images"); setZoom(100); });
      const native = button("图片原尺寸", "questionViewerNative", () => { controls.content.classList.add("native-images"); setZoom(100); });
      const less = button("−", "questionViewerZoomOut", () => setZoom(zoom - 25)); less.setAttribute("aria-label", "缩小题目");
      const more = button("＋", "questionViewerZoomIn", () => setZoom(zoom + 25)); more.setAttribute("aria-label", "放大题目");
      // 全屏看题以前在缩放条后面挂着一条快捷键提示，占掉整条工具栏的宽度。
      // 这些键在设置里查得到，工具栏只留缩放本身。
      tools.append(fit, native, less, percent, more);
      const viewport = node("div", "question-viewer-viewport"); viewport.tabIndex = 0; viewport.setAttribute("aria-label", "完整题目与答案");
      const content = node("div", "question-viewer-content");
      const question = node("article", "paper question-viewer-question");
      const answers = node("section", "question-viewer-answers"); answers.setAttribute("aria-label", "答案与解析");
      const status = node("p", "question-viewer-status"); status.id = "questionViewerStatus"; status.setAttribute("role", "status");
      content.append(question, answers, status); viewport.append(content); dialog.append(head, tools, viewport); doc.body.append(dialog);
      controls = { title, source, detail, exit, percent, native, less, more, viewport, content, question, answers, status, navigationBar, previous, next, place, navStatus };
      dialog.addEventListener("cancel", event => { event.preventDefault(); close(); });
      dialog.addEventListener("click", event => { if (event.target === dialog) close(); });
      dialog.addEventListener("close", finishClose);
      dialog.addEventListener("keydown", event => {
        if (!dialog.open || event.defaultPrevented || event.isComposing || event.keyCode === 229 || event.repeat
            || event.ctrlKey || event.metaKey || event.altKey || event.shiftKey || !["ArrowLeft", "ArrowRight"].includes(event.key)) return;
        if (event.target?.closest?.("input, textarea, select, [contenteditable], [role='textbox']")) return;
        const scrolling = event.target?.closest?.(".qb-stem-body, .qb-option-body, .qb-analysis");
        if (scrolling && scrolling.scrollWidth > scrolling.clientWidth + 2) return;
        if (!navigation) return;
        event.preventDefault(); void navigate(event.key === "ArrowLeft" ? -1 : 1);
      });
      viewport.addEventListener("wheel", event => {
        if (event.defaultPrevented || !event.ctrlKey || event.metaKey || event.altKey || event.shiftKey || !event.deltaY) return;
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
    function stopNavigation() {
      ++navEpoch; navController?.abort(); navController = null;
      root.clearTimeout(navTimer); navTimer = null; navigating = false;
    }
    function renderNavigation() {
      controls.navigationBar.hidden = !navigation || !position;
      controls.place.textContent = position ? `第 ${position.index + 1}/${position.total} 题` : "";
      controls.previous.disabled = navigating || !position || position.index <= 0;
      controls.next.disabled = navigating || !position || position.index >= position.total - 1;
    }
    async function navigate(direction) {
      if (!dialog?.open || !navigation || !position || navigating) return;
      const target = position.index + direction;
      if (target < 0 || target >= position.total) return;
      const session = epoch, request = ++navEpoch, source = navigation, controller = new root.AbortController(), previousFocus = doc.activeElement;
      let timedOut = false;
      navController = controller; navigating = true; renderNavigation(); controls.navStatus.textContent = "正在读取题目…";
      const deadline = new Promise((_, reject) => {
        navTimer = root.setTimeout(() => { timedOut = true; controller.abort(); reject(new Error("读取题目超时，当前题目保留，点上一题或下一题重试。")); }, 15000);
      });
      try {
        const value = await Promise.race([source.load(target, { signal: controller.signal }), deadline]);
        if (session !== epoch || request !== navEpoch || !dialog.open || controller.signal.aborted) return;
        if (!value?.item?.id || value.index !== target || !Number.isInteger(value.total) || value.total <= target) throw new Error("下一题返回不完整，请重试。");
        navigating = false;
        // The question displays synchronously. Do not lock navigation while
        // its separate read-only answer verification is still in flight.
        void open(value.item, { navigation: source, position: { index: value.index, total: value.total }, navigationStep: true });
      } catch (error) {
        if (session === epoch && request === navEpoch && dialog.open) controls.navStatus.textContent = timedOut ? "读取题目超时，当前题目保留，点上一题或下一题重试。" : error.message || "题目读取失败，请重试。";
      } finally {
        if (request === navEpoch) {
          root.clearTimeout(navTimer); navTimer = null; navController = null; navigating = false; renderNavigation();
          if (dialog.open && [controls.previous, controls.next].includes(previousFocus)
              && (!doc.activeElement || doc.activeElement === doc.body || doc.activeElement === dialog)) {
            (previousFocus.disabled ? controls.viewport : previousFocus).focus({ preventScroll: true });
          }
        }
      }
    }
    function addAnswer(label, value, className = "", target = controls.answers) {
      if (!solutions.hasContent(value)) return;
      const part = node("section", `question-viewer-answer ${className}`.trim());
      part.append(node("h3", "", label));
      const body = node("div"); solutions.render(body, value, { node, QB, empty: "原卷未提供", subQuestions: true });
      part.append(body); target.append(part);
    }
    function renderAnswers(item, verified = null) {
      controls.answers.replaceChildren();
      // Only explicit answer/analysis assets belong to the original solution;
      // stem/option diagrams and source-image crops appear once in the question.
      const originalFigures = (item.content?.figures || []).filter(figure => ["answer", "analysis"].includes(figure.slot));
      const saved = verified ? verified.solution : item.solution;
      const original = { answer: item.content?.answer, analysis: item.content?.analysis, figures: originalFigures };
      const hasSaved = solutions.hasContent(saved);
      const ai = verified?.ai_answer && !verified.ai_answer_stale ? verified.ai_answer : null;
      if (hasSaved) addAnswer("题库保存的答案与解析", saved);
      const history = hasSaved && (solutions.hasContent(original) || solutions.hasContent(ai)) ? node("details", "question-viewer-answer-history") : null;
      if (history) history.append(node("summary", "", "原卷答案与 AI 初稿历史"));
      addAnswer("原卷答案与解析", original, "", history || controls.answers);
      // The list's legacy extras do not prove a current fingerprint. Only the
      // read-only solution endpoint validates an existing AI draft.
      if (ai) addAnswer("AI 参考答案与解析 · 未核对", ai, "question-viewer-ai", history || controls.answers);
      if (history) controls.answers.append(history);
      if (!controls.answers.children.length) controls.answers.append(node("p", "helper", "原卷未提供答案与解析。"));
    }
    async function open(item, options = {}) {
      build();
      // A native close event can still be queued when another question opens.
      if (!dialog.open && returnState) finishClose();
      stopRead(); const session = ++epoch;
      if (!options.navigationStep) stopNavigation();
      navigation = options.navigation || null;
      position = options.position || (navigation ? { index: navigation.index, total: navigation.total } : null);
      renderNavigation(); controls.navStatus.textContent = navigation?.note || "";
      if (!dialog.open) {
        returnState = { focus: options.returnFocus || doc.activeElement, focusResolver: options.returnFocusResolver,
          x: root.scrollX || 0, y: root.scrollY || 0, overflow: doc.body.style.overflow || "" };
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
      controls.status.textContent = "正在检查已有参考答案…";
      if (!options.navigationStep) controls.exit.focus({ preventScroll: true });
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
      if (dialog?.open || !returnState) return;
      ++epoch; stopRead(); stopNavigation(); navigation = null; position = null;
      const session = epoch, previous = returnState; returnState = null;
      if (previous) {
        doc.body.style.overflow = previous.overflow;
        let target = previous.focus;
        if (target?.isConnected === false) { try { target = previous.focusResolver?.(); } catch { target = null; } }
        target?.focus?.({ preventScroll: true }); root.scrollTo?.(previous.x, previous.y);
      }
      // Paint the underlying card before releasing large images/math. A quick
      // reopen must not be cleared by an earlier close's callback.
      afterPaint(() => { if (session === epoch && !dialog.open) { controls.question.replaceChildren(); controls.answers.replaceChildren(); controls.status.textContent = ""; } });
    }
    function close() {
      if (!dialog?.open) return;
      dialog.close(); finishClose();
    }
    return { open, close, isOpen: () => Boolean(dialog?.open) };
  }

  // 1.12.7：收侧栏的开关是侧栏接缝上那个小按钮（.rail-collapse），不再是一个
  // 「专注浏览」文字按钮在顶栏和抽屉之间来回搬 —— 搬来搬去用户根本记不住它在哪。
  // 按钮从头到尾待在原地，只换图标和说明。
  function mountFocus({ button }) {
    if (!button) return null;
    const use = (id) => { const glyph = button.querySelector?.("use"); if (glyph) glyph.setAttribute("href", `#${id}`); };
    const set = active => {
      const keepFocus = root.document.activeElement === button;
      const on = Boolean(active);
      root.document.body.classList.toggle("library-focus-mode", on);
      button.setAttribute("aria-expanded", String(!on));
      const label = on ? "展开筛选栏" : "收起筛选栏";
      button.title = label; button.setAttribute("aria-label", label);
      use(on ? "i-chev-right" : "i-chev-left");
      if (keepFocus) button.focus({ preventScroll: true });
    };
    button.addEventListener("click", () => set(!root.document.body.classList.contains("library-focus-mode")));
    set(false); return { button, set };
  }
  return { create, mountFocus };
});
