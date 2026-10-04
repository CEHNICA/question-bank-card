/* Saved answer overlays are separate from the published question and its source. */
((root, factory) => {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.LibrarySolutions = api;
})(typeof window === "undefined" ? globalThis : window, () => {
  "use strict";
  const text = value => String(value ?? "");
  function hasContent(value) {
    return Boolean(text(value?.answer).trim() || text(value?.analysis).trim() || value?.figures?.length);
  }
  function selected(item, { ai_answers = false } = {}) {
    if (item.solution_revision !== "origin" && item.solution && hasContent(item.solution)) return { content: item.solution, ai: false, edited: true };
    const original = item.content || {};
    if (text(original.answer).trim()) return { content: { ...original, figures: [] }, ai: false };
    const ai = ai_answers ? item.ai_answer : null;
    if (hasContent(ai)) return { content: ai, ai: true };
    return text(original.analysis).trim() ? { content: { ...original, figures: [] }, ai: false } : null;
  }
  function completeness(item, options) {
    const value = selected(item, options)?.content;
    if (!hasContent(value)) return "missing";
    return text(value.analysis).trim() || value.figures?.length ? "ready" : "result_only";
  }
  function fixedSelections(values, ids) {
    const selected = new Set(ids || []);
    return Object.fromEntries(Object.entries(values || {}).filter(([id, revision]) => selected.has(id) && typeof revision === "string" && revision));
  }
  function draftSelections(values, ids) {
    const fixed = fixedSelections(values, ids);
    return Object.fromEntries((ids || []).map(id => [id, fixed[id] || "origin"]));
  }
  function figuresOf(value) {
    return (Array.isArray(value?.figures) ? value.figures : []).map(figure => ({ ...figure,
      display_width: Math.round(Math.max(5, Math.min(178, Number(figure.display_width) || 20)) * 100) / 100,
      position: ["before", "after", "paragraph"].includes(figure.position) ? figure.position : "after",
      paragraph: Math.max(0, Math.min(1000, Math.floor(Number(figure.paragraph) || 0))) }));
  }
  function editable(value = {}) { return { answer: text(value.answer), analysis: text(value.analysis), figures: figuresOf(value) }; }
  function payload(value) {
    return { answer: text(value.answer), analysis: text(value.analysis), figures: figuresOf(value).map(({ id, display_width, position, paragraph }) => ({ id, display_width, position, paragraph })) };
  }
  function signature(value) { return JSON.stringify(payload(value)); }
  function editorInitial(item, response = {}) {
    const manual = response.solution || (item.solution_revision === "origin" ? null : item.solution);
    if (manual) return { value: editable(manual), saved: signature(manual), ai_fields: [], ai_stale: false };
    const original = { ...editable(response.origin || item.content || {}), figures: [] };
    const value = editable(original), ai_fields = [];
    // The solution endpoint validates the publication/fingerprint binding.
    // An unvalidated catalogue ai_answer must never silently enter the fields.
    const ai = response.ai_answer_stale ? null : response.ai_answer;
    for (const field of ["answer", "analysis"]) if (!text(value[field]).trim() && text(ai?.[field]).trim()) { value[field] = text(ai[field]); ai_fields.push(field); }
    return { value, saved: signature(original), ai_fields, ai_stale: Boolean(response.ai_answer_stale), ai_error: text(response.ai_answer_error) };
  }
  function jobLabel(job = {}) {
    if (job.cancelled || job.terminal_reason === "cancelled" || job.status === "cancelled") return "已取消，勾选可重试";
    if (job.timed_out || job.terminal_reason === "timed_out" || job.status === "timed_out") return "处理超时，勾选可重试";
    if (job.status === "failed") return "处理失败，勾选可重试";
    if (["done", "completed", "succeeded", "success"].includes(job.status)) return job.result ? "初稿已到，保存后出卷" : "任务结束，暂无初稿";
    if (job.executor === "assistant") return "保留旧生成任务，未自动重新提交";
    return job.status === "running" ? "AI 正在解题" : "AI 任务排队中";
  }
  // Split the analysis on blank lines while keeping each paragraph's offset in
  // the whole field. A preview that tracks a selection needs those offsets to
  // be global: paragraph 2 and 3 must not restart at zero.
  function analysisParagraphs(value) {
    const source = text(value);
    const parts = [];
    const pattern = /\n\s*\n/g;
    let cursor = 0;
    let match;
    while ((match = pattern.exec(source)) !== null) {
      const chunk = source.slice(cursor, match.index);
      if (chunk.trim()) parts.push({ text: chunk, offset: cursor });
      cursor = match.index + match[0].length;
    }
    const tail = source.slice(cursor);
    if (tail.trim()) parts.push({ text: tail, offset: cursor });
    return parts;
  }

  // `subQuestions` is a screen-only display layer. It is opt-in so the print
  // and PDF path, which shares this function, keeps its current pagination:
  // the stored string, the paragraph numbering and the image anchors are all
  // identical either way. `trackSource` is only for the editing preview; it
  // adds data attributes for the display caret and never reaches a payload.
  function render(container, value, { node, QB, label = true, empty = "尚未补充答案解析",
    subQuestions = false, trackSource = false } = {}) {
    container.replaceChildren();
    if (!hasContent(value)) { container.append(node("p", "helper", empty)); return; }
    if (text(value.answer).trim()) {
      const answer = node("div", "solution-result");
      if (label) answer.append(node("strong", "solution-label", "答案："));
      const parts = subQuestions ? QB.subQuestionParts?.(value.answer) : null;
      if (parts && parts.length) {
        const list = node("div", "qb-subquestions");
        parts.forEach((part) => {
          const item = node("div", "qb-subquestion");
          if (part.lead) { const lead = node("span", "qb-subquestion-lead"); QB.renderTypeset(lead, part.lead); item.append(lead); }
          item.append(node("span", "qb-subquestion-label", part.label));
          const body = node("span", "qb-subquestion-body");
          QB.renderTypeset(body, part.body);
          item.append(body);
          list.append(item);
        });
        answer.append(list);
      } else if (trackSource) {
        // One owner element for the whole field: previewSelection resolves the
        // scope with the first match, so per-paragraph ownership would break
        // everything after the first paragraph.
        const body = node("span");
        body.dataset.qbField = "answer";
        QB.renderTypeset(body, value.answer, { trackSource: true, sourceOffset: 0 });
        answer.append(body);
      } else {
        const body = node("span"); QB.renderTypeset(body, value.answer); answer.append(body);
      }
      container.append(answer);
    }
    const figures = figuresOf(value);
    const addFigure = figure => {
      const box = node("figure", "solution-figure");
      const image = node("img"); image.src = figure.url; image.alt = "答案解析配图";
      image.loading = "eager"; image.dataset.solutionWidth = String(figure.display_width);
      image.style.width = `${figure.display_width}mm`; image.style.maxWidth = "100%"; image.style.height = "auto";
      box.append(image); container.append(box);
    };
    figures.filter(figure => figure.position === "before").forEach(addFigure);
    const parts = analysisParagraphs(value.analysis);
    if (parts.length && label) container.append(node("strong", "solution-label", "解析："));
    const analysisHost = trackSource ? node("div", "qb-analysis-field") : container;
    if (trackSource) {
      analysisHost.dataset.qbField = "analysis";
      if (parts.length) container.append(analysisHost);
    }
    parts.forEach((part, index) => {
      const body = node("div", "qb-analysis solution-paragraph");
      QB.renderTypeset(body, part.text, trackSource ? { trackSource: true, sourceOffset: part.offset } : undefined);
      analysisHost.append(body);
      figures.filter(figure => figure.position === "paragraph" && figure.paragraph === index).forEach(addFigure);
    });
    figures.filter(figure => figure.position === "after" || (figure.position === "paragraph" && figure.paragraph >= parts.length)).forEach(addFigure);
  }
  return Object.freeze({ hasContent, selected, completeness, fixedSelections, draftSelections, figuresOf, editable, payload, signature, editorInitial, jobLabel, render });
});
