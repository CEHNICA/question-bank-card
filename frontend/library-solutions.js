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
  function render(container, value, { node, QB, label = true, empty = "尚未补充答案解析" } = {}) {
    container.replaceChildren();
    if (!hasContent(value)) { container.append(node("p", "helper", empty)); return; }
    if (text(value.answer).trim()) {
      const answer = node("div", "solution-result");
      if (label) answer.append(node("strong", "solution-label", "答案："));
      const body = node("span"); QB.renderTypeset(body, value.answer); answer.append(body); container.append(answer);
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
    const paragraphs = text(value.analysis).split(/\n\s*\n/).filter(part => part.trim());
    if (paragraphs.length && label) container.append(node("strong", "solution-label", "解析："));
    paragraphs.forEach((part, index) => {
      const body = node("div", "qb-analysis solution-paragraph"); QB.renderTypeset(body, part); container.append(body);
      figures.filter(figure => figure.position === "paragraph" && figure.paragraph === index).forEach(addFigure);
    });
    figures.filter(figure => figure.position === "after" || (figure.position === "paragraph" && figure.paragraph >= paragraphs.length)).forEach(addFigure);
  }
  return Object.freeze({ hasContent, selected, completeness, fixedSelections, draftSelections, figuresOf, editable, payload, signature, render });
});
