"use strict";
/*
 * 题有据 · Eagle 题库工作台 —— 试题篮「A4 预览」页
 *
 * 这个页面被 iframe 加载，专门做一件事：把试题篮里的题按正式排版引擎
 * （qb-render.js 渲染题面 / exam-layout.js 分页 / exam-export.js 分组）
 * 铺成一张张 A4 纸。
 *
 * 为什么单独一个页面而不是直接塞进 Eagle 主界面：
 *   library.css 是旧版正式题库的排版样式，里面带 body 级全局样式，直接
 *   <link> 进 Eagle 页面会把主界面整片污染。放进 iframe 里，两者各是一份
 *   文档，互不干扰 —— 这也是旧版预览的做法。
 *
 * 口径必须和导出完全一致：
 *   预览用 exam-layout.js 排版，导出（PDF）用后端同一套规则，Word 用
 *   exam-export.js 的分组口径。分组顺序只有 ExamExport.paperGroups 一份
 *   定义 —— 两边各写一份的话，屏幕上看到的「第 N 题」和纸上的对不上。
 */
(function () {
  const QB = window.QBRender;
  const Solutions = window.LibrarySolutions;
  const CHANNEL_READY = "qbc:eagle-print-preview-ready";
  const CHANNEL_PAYLOAD = "qbc:eagle-print-preview";
  const CHANNEL_RESULT = "qbc:eagle-print-preview-result";
  const CHANNEL_OVERRIDES = "qbc:eagle-print-preview-overrides";
  const CHANNEL_OPEN_SOLUTION = "qbc:eagle-print-preview-open-solution";

  function node(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  }

  const state = {
    items: [],
    options: {},
    title: "练习",
    token: 0,
    // 预览里逐题调整的结果。父页面是权威持有者，这里先改先看，再回传。
    overrides: { option_overrides: {}, answer_space_overrides: {}, figure_overrides: {}, question_breaks: [] },
    // 展开着的「调整」面板：仅在用户主动展开且未执行调整操作时保留。
    openTools: new Set(),
    // 最近一次执行调整操作的试题 ID，用于排版完成后精准平滑滚动定位。
    lastAdjustedQuestionId: null,
    // 被系统夹过的提示（解答题留白下限），跨重排版保留。
    notes: {}
  };

  function post(message) {
    try { window.parent.postMessage(message, "*"); } catch (error) { /* 脱离父页面时忽略 */ }
  }

  function report(text, tone) {
    const bar = document.getElementById("previewStatus");
    if (!bar) return;
    bar.textContent = text;
    bar.dataset.tone = tone || "";
  }

  function normalizeOverrides(options) {
    const overrides = options || {};
    return {
      option_overrides: Object.assign({}, overrides.option_overrides || {}),
      answer_space_overrides: Object.assign({}, overrides.answer_space_overrides || {}),
      figure_overrides: Object.assign({}, overrides.figure_overrides || {}),
      question_breaks: Array.isArray(overrides.question_breaks) ? overrides.question_breaks.slice() : []
    };
  }

  // 预览用的最终选项 = 父页面给的全局设置 + 预览里逐题调整过的。
  function currentOptions() {
    const base = state.options || {};
    const mine = state.overrides;
    return Object.assign({}, base, {
      option_overrides: Object.assign({}, base.option_overrides || {}, mine.option_overrides),
      answer_space_overrides: Object.assign({}, base.answer_space_overrides || {}, mine.answer_space_overrides),
      figure_overrides: Object.assign({}, base.figure_overrides || {}, mine.figure_overrides),
      question_breaks: Array.from(new Set([...(base.question_breaks || []), ...mine.question_breaks]))
    });
  }

  function answerContent(item) {
    if (!Solutions || typeof Solutions.selected !== "function") return null;
    return Solutions.selected(item, { ai_answers: Boolean((state.options || {}).ai_answers) });
  }

  function answerRow(number, item, inline) {
    const row = node("div", "print-answer-row" + (inline ? " print-answer-inline" : ""));
    row.dataset.questionId = item.id;
    row.append(node("strong", "", inline ? "" : `${number}.`));
    const body = node("div");
    const selected = answerContent(item);
    Solutions.render(body, selected && selected.content, { node, QB, empty: "（原卷未提供答案解析）" });
    if (selected && selected.ai) body.prepend(node("span", "print-ai-note", "（AI 参考，未核对）"));
    row.append(body);
    return row;
  }

  // 留白以毫米为单位。每一道题都能调（不再只有解答题）；解答题有硬下限。
  function spaceControl(item, number, options, onAction) {
    const Layout = window.ExamLayout || {};
    const isFree = item.question_type === "free_response";
    const floor = isFree ? (Layout.FREE_RESPONSE_MIN || 8) : 0;
    const max = Layout.SPACE_MAX || 200;
    const disabled = options.document === "answers"
      || (options.document === "combined" && options.answer_layout === "inline");

    const wrap = node("div", "print-space-wrap");
    const row = node("div", "print-space-row");
    const label = node("label", "", "留白");
    label.setAttribute("for", `print-space-${item.id}`);
    const input = node("input", "print-space-input");
    input.type = "number";
    input.id = `print-space-${item.id}`;
    input.min = "0";
    input.max = String(max);
    input.step = "1";
    input.setAttribute("aria-label", `第 ${number} 题留白毫米数`);
    const unit = node("span", "print-space-unit", "毫米");
    row.append(label, input, unit);

    const presets = node("div", "print-space-presets");
    presets.setAttribute("role", "group");
    presets.setAttribute("aria-label", `第 ${number} 题留白快捷档位`);
    const hint = node("p", "print-space-hint", "");

    function effective() {
      return typeof Layout.answerSpace === "function"
        ? Layout.answerSpace(item.question_type, item.id, options)
        : 0;
    }
    function isFollowing() {
      return !Object.prototype.hasOwnProperty.call(state.overrides.answer_space_overrides, item.id);
    }
    function apply(mm) {
      if (disabled) return;
      const requested = Math.round(Number(mm));
      if (!Number.isFinite(requested)) return;
      let value = Math.max(0, Math.min(max, requested));
      if (isFree && value < floor) {
        value = floor;
        state.notes[item.id] = `解答题至少留 ${floor} 毫米，已自动补足。`;
      } else {
        delete state.notes[item.id];
      }
      state.overrides.answer_space_overrides[item.id] = value;
      if (typeof onAction === "function") onAction();
      else {
        state.openTools.delete(item.id);
        state.lastAdjustedQuestionId = item.id;
      }
      pushOverrides();
      render();
    }
    function follow() {
      if (disabled) return;
      delete state.overrides.answer_space_overrides[item.id];
      delete state.notes[item.id];
      if (typeof onAction === "function") onAction();
      else {
        state.openTools.delete(item.id);
        state.lastAdjustedQuestionId = item.id;
      }
      pushOverrides();
      render();
    }

    const followButton = node("button", "", "跟随");
    followButton.type = "button";
    followButton.title = "跟随全卷的留白设置";
    followButton.setAttribute("aria-label", `第 ${number} 题留白跟随全卷`);
    followButton.addEventListener("click", follow);
    presets.append(followButton);
    const presetButtons = [];
    [[0, "无"], [12, "12"], [30, "30"], [60, "60"]].forEach(([value, text]) => {
      const button = node("button", "", text);
      button.type = "button";
      button.dataset.mm = String(value);
      button.setAttribute("aria-label", `第 ${number} 题留白 ${value} 毫米`);
      button.addEventListener("click", () => apply(value));
      presetButtons.push(button);
      presets.append(button);
    });
    input.addEventListener("change", () => apply(input.value));

    const value = effective();
    input.value = String(value);
    input.disabled = disabled;
    followButton.disabled = disabled;
    followButton.setAttribute("aria-pressed", String(isFollowing()));
    presetButtons.forEach(button => {
      button.disabled = disabled;
      button.setAttribute("aria-pressed", String(!isFollowing() && Number(button.dataset.mm) === value));
    });
    if (disabled) {
      hint.textContent = "这份卷子不留答题空间。";
      hint.dataset.tone = "";
    } else if (state.notes[item.id]) {
      hint.textContent = state.notes[item.id];
      hint.dataset.tone = "warn";
    } else if (isFollowing()) {
      hint.textContent = `跟随全卷：当前 ${value} 毫米。`;
      hint.dataset.tone = "";
    } else {
      hint.textContent = `本题单独设定 ${value} 毫米。`;
      hint.dataset.tone = "";
    }

    wrap.append(row, presets, hint);
    return wrap;
  }

  // 逐题调整：选项排版 / 解答留白 / 另起一页。
  // 改动只落在这一次组卷，不动题库原题 —— 和旧版预览里的「调整」一个口径。
  function questionTools(item, number, options) {
    const tools = node("details", "print-question-tools no-print");
    const toggle = node("summary", "", "调整");
    toggle.setAttribute("aria-label", `第 ${number} 题排版操作`);
    tools.append(toggle);

    function finishAction() {
      state.openTools.delete(item.id);
      state.lastAdjustedQuestionId = item.id;
      tools.open = false;
    }

    const actions = node("div", "print-question-actions");
    actions.setAttribute("role", "group");
    actions.setAttribute("aria-label", `第 ${number} 题排版调整`);

    const layout = node("select", "print-single-layout");
    layout.setAttribute("aria-label", `第 ${number} 题选项排版`);
    [["", "跟随全卷"], ["auto", "自动排版"], ["four", "一行四个"], ["two", "两行两个"], ["vertical", "四个选项竖排"]]
      .forEach(([value, label]) => { const choice = node("option", "", label); choice.value = value; layout.append(choice); });
    layout.value = options.option_overrides[item.id] || "";
    const figures = Array.isArray(item.content && item.content.figures) ? item.content.figures : [];
    const hasOptions = Object.keys((item.content && item.content.options) || {}).length > 0;
    const hasSlotFigures = figures.some(figure => /^[A-E]$/.test(figure.slot));
    layout.hidden = (item.content && item.content.body_mode) === "source_image" || (!hasOptions && !hasSlotFigures);
    layout.addEventListener("change", () => {
      if (layout.value) state.overrides.option_overrides[item.id] = layout.value;
      else delete state.overrides.option_overrides[item.id];
      finishAction();
      pushOverrides();
      render();
    });

    const space = spaceControl(item, number, options, finishAction);

    const pageBreak = node("button", "print-full", state.overrides.question_breaks.includes(item.id) ? "取消另起页" : "另起一页");
    pageBreak.type = "button";
    pageBreak.setAttribute("aria-label", `第 ${number} 题另起一页`);
    pageBreak.setAttribute("aria-pressed", String(state.overrides.question_breaks.includes(item.id)));
    pageBreak.addEventListener("click", () => {
      state.overrides.question_breaks = state.overrides.question_breaks.includes(item.id)
        ? state.overrides.question_breaks.filter(id => id !== item.id)
        : state.overrides.question_breaks.concat([item.id]);
      finishAction();
      pushOverrides();
      render();
    });

    actions.append(layout, space, pageBreak);
    tools.append(actions);
    if (state.openTools.has(item.id)) tools.open = true;
    tools.addEventListener("toggle", () => {
      if (tools.open) state.openTools.add(item.id);
      else state.openTools.delete(item.id);
    });
    return tools;
  }

  function pushOverrides() {
    post({
      type: CHANNEL_OVERRIDES,
      option_overrides: state.overrides.option_overrides,
      answer_space_overrides: state.overrides.answer_space_overrides,
      figure_overrides: state.overrides.figure_overrides,
      question_breaks: state.overrides.question_breaks
    });
  }

  // 排版前先等字体和配图。配图没载完就量高度，量出来的是半张纸。
  async function waitForAssets(container) {
    let timer;
    try {
      const ready = Promise.all([
        document.fonts && document.fonts.ready ? document.fonts.ready : Promise.resolve(),
        ...Array.from(container.querySelectorAll("img")).map(async image => {
          image.loading = "eager";
          if (typeof image.decode === "function") await image.decode();
          else if (!image.complete) await new Promise((resolve, reject) => {
            image.addEventListener("load", resolve, { once: true });
            image.addEventListener("error", () => reject(new Error("试卷配图未能载入，请重试")), { once: true });
          });
          if (!image.naturalWidth) throw new Error("试卷配图未能载入，请重试");
        })
      ]);
      await Promise.race([
        ready,
        new Promise((_, reject) => { timer = setTimeout(() => reject(new Error("字体或配图仍未载入，请稍后重试")), 15000); })
      ]);
    } catch (error) {
      throw new Error(error && error.message && error.message.includes("字体") ? error.message : "试卷配图未能载入，请重试");
    } finally {
      clearTimeout(timer);
    }
  }

  async function render() {
    const token = ++state.token;
    const paper = document.getElementById("previewPaper");
    if (!paper) return;
    const options = currentOptions();
    const items = state.items || [];

    report(items.length ? "正在排版…" : "试题篮是空的。", items.length ? "" : "warn");
    if (!items.length) {
      paper.replaceChildren();
      post({ type: CHANNEL_RESULT, page_count: 0, warnings: [], error: "" });
      return;
    }

    paper.style.setProperty("--exam-font-size", `${options.font_size || 12}pt`);
    paper.dataset.answerSpace = options.answer_space || "none";
    paper.dataset.document = options.document || "questions";

    const available = items.filter(item => answerContent(item)).length;
    const showAnswers = options.answers !== false && available > 0;

    // 分组顺序只有一份定义（导出那边也用同一份）。
    const grouped = window.ExamExport && typeof window.ExamExport.paperGroups === "function"
      ? window.ExamExport.paperGroups(items)
      : items.map(value => ["全部", [value]]);

    let number = 0;
    const answers = [];
    const toolMap = new Map();

    // 分页：源 → 测量 → 一页页的 .exam-page。
    // 采用离屏 flow 进行测量排版，在测量期间保持现有 paper 页面不坍塌，
    // 避免视口滚动条被浏览器强制重置为 0。
    const flow = node("div", "print-flow exam-source");
    flow.style.setProperty("--exam-font-size", `${options.font_size || 12}pt`);
    flow.dataset.answerSpace = options.answer_space || "none";
    flow.dataset.document = options.document || "questions";

    flow.append(node("h2", "print-title", state.title || "练习"));
    if (options.student_info !== false && options.document !== "answers") {
      flow.append(node("p", "print-info", "姓名：____________    班级：____________    得分：____________"));
    }

    grouped.forEach(([name, group], index) => {
      if (options.document !== "answers") {
        const heading = window.ExamExport && typeof window.ExamExport.sectionHeading === "function"
          ? window.ExamExport.sectionHeading(group, index, name)
          : name;
        flow.append(node("h3", "print-section", heading));
      }
      group.forEach(item => {
        number += 1;
        if (options.document !== "answers") {
          const block = node("div", "print-question");
          QB.renderQuestion(block, item.content, { number, showAnswer: "none" });
          const origin = item.origin || (item.content && item.content.origin);
          if (options.origin && origin) {
            // 原图题只建 .qb-stem，没有 .qb-stem-body。插入点必须一样，
            // 否则这类题在屏幕上没题源、导出的卷子上有。
            const stem = block.querySelector(".qb-stem-body") || block.querySelector(".qb-stem");
            if (stem) stem.prepend(node("span", "print-origin", `（${origin}）`));
          }
          block.dataset.questionId = item.id;
          block.dataset.questionType = item.question_type;
          const space = window.ExamLayout.answerSpace(item.question_type, item.id, options);
          if (space > 0) {
            const blank = node("div", "print-answer-space");
            blank.dataset.answerSpace = String(space);
            blank.style.height = `${space}mm`;
            block.append(blank);
          }
          flow.append(block);
          toolMap.set(item.id, { item, number });
          if (options.document === "combined" && options.answer_layout === "inline" && showAnswers && answerContent(item)) {
            flow.append(answerRow(number, item, true));
          }
        }
        answers.push([number, item]);
      });
    });

    if (options.document !== "questions" && showAnswers
      && !(options.document === "combined" && options.answer_layout === "inline")) {
      const key = node("section", "print-answers");
      if (options.document === "answers") key.classList.add("print-answers-only");
      key.append(node("h3", "print-section", "参考答案与解析"));
      answers.forEach(([index, item]) => key.append(answerRow(index, item, false)));
      flow.append(key);
    }
    if (options.document === "answers" && !available) {
      flow.append(node("p", "helper", "这些题没有答案或解析，请改选题目卷。"));
    }
    flow.append(node("p", "print-footer", `共 ${number} 题`));
    flow.querySelectorAll("img").forEach(image => { image.loading = "eager"; });

    document.body.append(flow);

    try {
      await waitForAssets(flow);
      if (token !== state.token) { flow.remove(); return; }
      if (!window.ExamLayout || typeof window.ExamLayout.paginate !== "function") {
        throw new Error("分页组件未载入，请刷新页面后重试");
      }
      const host = node("div", "print-paper");
      const result = await window.ExamLayout.paginate(flow, Object.assign({ host }, options));
      flow.remove();
      if (token !== state.token) return;

      paper.replaceChildren(...result.pages);
      paper.dataset.pageCount = String(result.page_count);
      window.ExamLayout.scale(paper);

      // 分页时「调整」面板被剥掉了（它是 no-print，不参与量高），
      // 排完版再挂回每一道题上。
      const seen = new Set();
      paper.querySelectorAll(".print-question").forEach(question => {
        const id = question.dataset.questionId;
        if (seen.has(id)) return;
        seen.add(id);
        const entry = toolMap.get(id);
        if (!entry) return;
        question.append(questionTools(entry.item, entry.number, options));
      });

      // 视口精确平滑滚动到刚才调整的这道题位置
      const targetId = state.lastAdjustedQuestionId;
      state.lastAdjustedQuestionId = null;
      if (targetId) {
        const target = paper.querySelector(`.print-question[data-question-id="${targetId}"]`);
        if (target) {
          requestAnimationFrame(() => {
            target.scrollIntoView({ behavior: "smooth", block: "center" });
            target.classList.add("locate-flash");
            setTimeout(() => target.classList.remove("locate-flash"), 1200);
          });
        }
      }

      const warnings = Array.isArray(result.warnings) ? result.warnings : [];
      const summary = `A4 · 共 ${result.page_count} 页 · ${items.length} 题`;
      report(warnings.length ? `${summary} · ${warnings.join(" ")}` : summary, warnings.length ? "warn" : "ok");
      post({ type: CHANNEL_RESULT, page_count: result.page_count, warnings, error: "" });
    } catch (error) {
      flow.remove();
      if (token !== state.token) return;
      const message = (error && error.message) || "排版失败，请重试";
      report(message, "error");
      post({ type: CHANNEL_RESULT, page_count: 0, warnings: [], error: message });
    }
  }

  window.addEventListener("message", event => {
    const data = event.data;
    if (!data || typeof data !== "object") return;
    if (data.type !== CHANNEL_PAYLOAD) return;
    state.items = Array.isArray(data.items) ? data.items : [];
    state.options = data.options || {};
    state.title = data.title || "练习";
    // 父页面是权威：它带来的逐题调整覆盖本地的旧值。
    state.overrides = normalizeOverrides(state.options);
    render();
  });

  window.addEventListener("load", () => {
    post({ type: CHANNEL_READY });
  });
})();
