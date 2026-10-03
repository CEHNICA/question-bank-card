/* Fixed A4 composition shared by the preview and the local PDF renderer. */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.ExamLayout = api;
})(typeof window === "undefined" ? globalThis : window, function () {
  "use strict";
  const MM = 96 / 25.4;
  const PAGE_WIDTH = 210 * MM, BODY_WIDTH = 178 * MM, BODY_HEIGHT = 261 * MM;
  const choices = new Set(["auto", "four", "two", "vertical"]);
  const writingSpace = Object.freeze({ none: 0, small: 12, medium: 30, large: 60 });
  function normalize(options = {}) {
    return {
      pagination: options.pagination === "keep" ? "keep" : "compact",
      option_layout: choices.has(options.option_layout) ? options.option_layout : "auto",
      option_overrides: Object.fromEntries(Object.entries(options.option_overrides || {}).filter(([, value]) => choices.has(value))),
      answer_space: Object.hasOwn(writingSpace, options.answer_space) ? options.answer_space : "none",
      answer_space_overrides: Object.fromEntries(Object.entries(options.answer_space_overrides || {}).filter(([, value]) => Object.hasOwn(writingSpace, value))),
      question_breaks: Array.isArray(options.question_breaks) ? [...new Set(options.question_breaks.filter(value => typeof value === "string"))] : []
    };
  }
  function keepWhole(height, pageHeight, mode) { return mode === "keep" && height <= pageHeight + .5; }
  function requestedColumns(layout, count) { return layout === "vertical" ? 1 : layout === "four" && count <= 4 ? 4 : layout === "auto" ? 0 : 2; }
  function answerSpace(type, id, options = {}) {
    if (type !== "free_response" || options.document === "answers"
      || (options.document === "combined" && options.answer_layout === "inline")) return "none";
    const normalized = normalize(options);
    return normalized.answer_space_overrides[id] || normalized.answer_space;
  }
  function answerSpaceMm(mode) { return Object.hasOwn(writingSpace, mode) ? writingSpace[mode] : 0; }
  function alignQuestionNumber(question) {
    const stem = question.querySelector(".qb-stem"), body = stem?.querySelector(".qb-stem-body");
    if (!stem || !body) return;
    const first = Array.from(body.childNodes).find(child => child.nodeType === 1 || (child.nodeType === 3 && child.textContent.trim()));
    // A real inline first line shares its baseline with the number. A table
    // or standalone display starts at the top instead of using its last row.
    stem.dataset.examFirstLine = first?.nodeType === 1 && first.matches(".qb-math.is-display, .qb-math-display-group, .qb-table-wrap, table, figure, img") ? "block" : "inline";
  }
  function element(doc, tag, className, text) {
    const node = doc.createElement(tag); node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function clean(node) {
    node.querySelectorAll(".no-print, .print-formula-fallback, .print-overflow-hint").forEach(child => child.remove());
    node.querySelectorAll("[id]").forEach(child => child.removeAttribute("id"));
    node.querySelectorAll("[data-print-overflow]").forEach(child => { delete child.dataset.printOverflow; child.removeAttribute("tabindex"); child.removeAttribute("aria-describedby"); });
    return node;
  }
  function outerHeight(node) {
    const css = node.ownerDocument.defaultView.getComputedStyle(node);
    return node.getBoundingClientRect().height + (parseFloat(css.marginTop) || 0) + (parseFloat(css.marginBottom) || 0);
  }
  function fitOptions(question, options, warnings) {
    const id = question.dataset.questionId;
    const layout = options.option_overrides[id] || options.option_layout;
    question.querySelectorAll(".qb-options").forEach(list => {
      const count = list.children.length;
      let columns = requestedColumns(layout, count);
      if (!columns) columns = Number(list.dataset.cols) || (list.classList.contains("cols-4") ? 4 : list.classList.contains("cols-2") ? 2 : 1);
      if (count > 4 && columns === 4) columns = 2;
      const start = columns;
      const setColumns = value => {
        list.classList.remove("cols-1", "cols-2", "cols-4", "cols-5");
        list.classList.add(`cols-${value}`);
        list.style.gridTemplateColumns = `repeat(${value}, minmax(0, 1fr))`;
      };
      const overflows = () => Array.from(list.querySelectorAll(".qb-option-body, .qb-figure, .katex-html")).some(field => {
        if (field.classList.contains("qb-option-body")) {
          const old = field.style.whiteSpace; field.style.whiteSpace = "nowrap";
          const tooLong = field.scrollWidth > field.clientWidth + 2;
          field.style.whiteSpace = old;
          if (tooLong) return true;
        }
        if (field.scrollWidth > field.clientWidth + 2) return true;
        if (field.classList.contains("katex-html")) return Array.from(field.children).some(base => base.getBoundingClientRect().width > field.closest(".qb-option-body").clientWidth + 2);
        return false;
      });
      setColumns(columns);
      while (columns > 1 && overflows()) { columns = columns > 2 ? 2 : 1; setColumns(columns); }
      if (layout !== "auto" && (columns !== start || (layout === "four" && count > 4))) warnings.push(`第 ${question.querySelector(".qb-number")?.textContent.replace(/\.$/, "") || "?"} 题选项较长，已改为${columns === 2 ? "两列" : "一列"}，保留全部选项。`);
      list.dataset.examColumns = String(columns);
    });
  }
  // Character boundaries permit prose to continue, while complete maths, figures,
  // option groups and table rows remain indivisible. DOM Range keeps their markup.
  function boundaries(node, pageHeight) {
    const content = node.matches(".print-answer-row") ? node.lastElementChild : node;
    const out = [{ node: content, offset: 0 }];
    const atomic = ".qb-math, .qb-math-display-group, .katex, figure, img, .print-answer-space, tr";
    function visit(child) {
      if (child.nodeType === 3) {
        let offset = 0;
        for (const char of child.textContent) { offset += char.length; out.push({ node: child, offset }); }
        return;
      }
      if (child.nodeType !== 1) return;
      const whole = child.matches(atomic) || (child.matches(".qb-options, .qb-figures, .qb-option") && outerHeight(child) <= pageHeight);
      if (whole) {
        out.push({ node: child.parentNode, offset: Array.prototype.indexOf.call(child.parentNode.childNodes, child) + 1 });
      } else Array.from(child.childNodes).forEach(visit);
    }
    Array.from(content.childNodes).forEach(visit);
    out.push({ node: content, offset: content.childNodes.length });
    return out;
  }
  function fragment(node, points, from, to, continued) {
    const range = node.ownerDocument.createRange();
    range.setStart(points[from].node, points[from].offset); range.setEnd(points[to].node, points[to].offset);
    const result = node.cloneNode(false);
    if (node.matches(".print-answer-row")) {
      // A range wholly inside the answer body may return several sibling
      // paragraphs. Rebuild the two grid cells instead of placing those
      // paragraphs directly in the row's narrow number column.
      const label = node.firstElementChild.cloneNode(true), body = node.lastElementChild.cloneNode(false);
      if (continued) label.textContent = label.textContent ? `${label.textContent}（续）` : "续";
      body.append(range.cloneContents()); result.append(label, body);
    } else result.append(range.cloneContents());
    result.classList.add("exam-fragment");
    if (continued) {
      result.dataset.continuation = "1";
      const stem = result.querySelector(".qb-stem");
      if (stem && !stem.querySelector(".qb-number")) {
        const number = node.querySelector(".qb-number")?.textContent || "";
        stem.prepend(element(node.ownerDocument, "span", "qb-number exam-continued", `${number}（续）`));
      }
      if (!stem && node.matches(".print-question")) result.prepend(element(node.ownerDocument, "div", "exam-continuation-label", `第 ${parseInt(node.querySelector(".qb-number")?.textContent, 10) || "?"} 题（续）`));
      if (node.matches(".print-answer-row") && result.firstElementChild?.tagName !== "STRONG") result.prepend(element(node.ownerDocument, "strong", "exam-continued", `${node.querySelector("strong")?.textContent || ""}（续）`));
      // Repeat a table header when the next page starts inside its body.
      result.querySelectorAll("table").forEach(table => {
        if (table.querySelector("thead")) return;
        const header = node.querySelector(`table[data-exam-table="${table.dataset.examTable}"] thead`); if (header) table.prepend(header.cloneNode(true));
      });
    }
    return result;
  }
  // Apply the same formula fit to the final A4 width in preview and PDF.
  // Screen-only preprocessing used to alter the preview clone while the PDF
  // retained the original size, changing line breaks and sometimes pagination.
  function fitFormulas(root) {
    let overflow = 0;
    root.querySelectorAll(".katex").forEach(math => {
      math.style.removeProperty("--print-math-size");
      delete math.dataset.examMathOverflow;
    });
    root.querySelectorAll(".qb-math").forEach(span => {
      const math = span.querySelector(".katex"), html = math?.querySelector(".katex-html");
      if (!html) return;
      const field = span.closest(".qb-stem-body, .qb-option-body, .qb-analysis, .print-answer-row > div") || span.parentElement;
      const available = field.clientWidth - 6;
      const widest = Math.max(0, ...Array.from(html.children, base => base.getBoundingClientRect().width));
      if (!available || widest <= available + 1) return;
      const size = parseFloat((root.ownerDocument?.defaultView || globalThis).getComputedStyle(math).fontSize);
      const fitted = size * available / widest;
      if (Number.isFinite(fitted) && fitted >= 12) math.style.setProperty("--print-math-size", `${fitted}px`);
      else { math.dataset.examMathOverflow = "1"; overflow += 1; }
    });
    return overflow;
  }

  async function paginate(source, supplied = {}) {
    if (!source?.ownerDocument || !supplied.host) throw new Error("缺少 A4 排版容器");
    const options = normalize(supplied), host = supplied.host, doc = source.ownerDocument;
    const warnings = [], pages = [], bodies = [];
    const measure = element(doc, "div", "exam-layout-measure print-flow");
    measure.style.setProperty("--exam-font-size", source.style.getPropertyValue("--exam-font-size") || "12pt");
    measure.dataset.answerSpace = source.dataset.answerSpace || "none";
    doc.body.append(measure);
    const work = clean(source.cloneNode(true)); work.removeAttribute("id"); work.classList.remove("exam-source"); work.classList.add("print-flow"); measure.append(work);
    try {
      // Newly inserted maths can start its font requests only when the browser
      // lays it out. Reading fonts.ready before that first layout can resolve
      // the previous, already-ready promise and measure fallback glyphs. The
      // URL fonts in the preview and embedded PDF fonts must both finish first.
      work.getBoundingClientRect();
      await (doc.fonts?.ready || Promise.resolve());
      if (work.querySelector(".katex") && doc.fonts && typeof doc.fonts[Symbol.iterator] === "function"
        && Array.from(doc.fonts).some(face => face.status === "error" && /KaTeX_/.test(face.family))) {
        throw new Error("公式字体未能载入，请刷新组卷页面后重试。");
      }
      await Promise.all(Array.from(work.querySelectorAll("img"), async image => {
        image.loading = "eager";
        if (typeof image.decode === "function") {
          try { await image.decode(); } catch { throw new Error("试卷配图未能载入，请重试"); }
        }
        if (!image.naturalWidth) throw new Error("试卷配图未能载入，请重试");
        // Match Word's natural 150 dpi size, capped to printable bounds. This is
        // independent of remaining space and never changes library thumbnails.
        const chosenWidth = Number(image.dataset.solutionWidth);
        const imageScale = Math.min(chosenWidth > 0 ? Math.max(5, Math.min(178, chosenWidth)) * MM / image.naturalWidth : 96 / 150,
          BODY_WIDTH / image.naturalWidth, 210 * MM / image.naturalHeight);
        image.style.width = `${image.naturalWidth * imageScale}px`;
        image.style.height = "auto";
      }));
      work.querySelectorAll(".print-question").forEach(question => {
        alignQuestionNumber(question);
        fitOptions(question, options, warnings);
      });
      const formulaOverflow = fitFormulas(work);
      if (formulaOverflow) warnings.push(`${formulaOverflow} 处公式超出 A4 正文，请调整字号或导出 Word 继续排版。`);
      work.querySelectorAll("table").forEach((table, index) => { table.dataset.examTable = String(index); });
      // Strip tools from measurements; preview actions are overlaid afterwards.
      const probe = element(doc, "div", "exam-layout-probe"); measure.append(probe);
      let body, used = 0;
      function newPage() {
        const page = element(doc, "article", "exam-page");
        page.style.setProperty("--exam-font-size", measure.style.getPropertyValue("--exam-font-size"));
        page.dataset.answerSpace = measure.dataset.answerSpace;
        body = element(doc, "div", "exam-page-body"); page.append(body);
        pages.push(page); bodies.push(body); used = 0;
      }
      function height(node) { probe.replaceChildren(node); return outerHeight(node); }
      function add(node, h) { body.append(node); used += h; }
      function atomic(node) {
        const h = height(node);
        if (h > BODY_HEIGHT + 1) throw new Error("有图表或公式高于一页 A4，请调整字号或导出 Word 排版");
        if (used && used + h > BODY_HEIGHT + .5) newPage();
        add(node, h);
      }
      newPage();
      let pending = [], hasQuestion = false;
      function writingSpace(blank, question) {
        // Writing space is divisible; moving an entire 60 mm blank block left
        // otherwise usable page bottoms empty and created blank continuation
        // sheets. Keep the requested total, using the current page first.
        let remaining = height(blank.cloneNode(true));
        while (remaining > .01) {
          let owner = body.lastElementChild;
          const existing = owner?.matches(".print-question") && owner.dataset.questionId === question.dataset.questionId;
          if (!existing) {
            owner = question.cloneNode(false);
            owner.classList.add("exam-fragment"); owner.dataset.continuation = "1";
            const number = parseInt(question.querySelector(".qb-number")?.textContent, 10) || "?";
            owner.append(element(doc, "div", "exam-continuation-label", `第 ${number} 题答题区（续）`));
          }
          const previousHeight = height(owner.cloneNode(true));
          const available = BODY_HEIGHT - used - (existing ? 0 : previousHeight);
          if (available <= .5) { newPage(); continue; }
          const allocated = Math.min(remaining, available);
          const piece = blank.cloneNode(false);
          piece.style.height = `${allocated}px`;
          owner.append(piece);
          const nextHeight = height(owner.cloneNode(true));
          if (existing) used += nextHeight - previousHeight;
          else add(owner, nextHeight);
          remaining -= allocated;
          if (remaining > .01) newPage();
        }
      }
      function compose(node, forceBreak = false) {
        const blank = node.matches(".print-question") && Array.from(node.children).find(child => child.matches(".print-answer-space"));
        if (blank && (options.pagination === "compact" || height(node.cloneNode(true)) > BODY_HEIGHT + .5)) {
          blank.remove();
          compose(node, forceBreak);
          writingSpace(blank, node);
          return;
        }
        const h = height(node.cloneNode(true));
        const pendingHeight = pending.reduce((sum, heading) => sum + height(heading.cloneNode(true)), 0);
        const full = !node.matches(".print-answer-row") && keepWhole(h, BODY_HEIGHT - pendingHeight, options.pagination);
        const minimum = full ? h : Math.min(h, 62);
        if (used && (forceBreak || used + pendingHeight + minimum > BODY_HEIGHT + .5)) newPage();
        pending.forEach(heading => atomic(heading)); pending = [];
        if (h <= BODY_HEIGHT - used + .5) { add(node, h); return; }
        probe.replaceChildren(node);
        const points = boundaries(node, BODY_HEIGHT);
        let start = 0, continued = false;
        while (start < points.length - 1) {
          let low = start + 1, high = points.length - 1, best = start, bestHeight = 0;
          while (low <= high) {
            const middle = Math.floor((low + high) / 2);
            const piece = fragment(node, points, start, middle, continued);
            const size = height(piece);
            if (size <= BODY_HEIGHT - used + .5) { best = middle; bestHeight = size; low = middle + 1; }
            else high = middle - 1;
          }
          if ((best === start || (!continued && bestHeight < 42)) && used) { newPage(); continue; }
          if (best === start) throw new Error("有完整公式或图表无法放进 A4，请调整字号或导出 Word 排版");
          const piece = fragment(node, points, start, best, continued);
          if (!piece.textContent.trim() && !piece.querySelector("img, .print-answer-space")) { start = best; continue; }
          add(piece, bestHeight); start = best; continued = true;
          if (start < points.length - 1) newPage();
        }
      }
      const countText = work.querySelector(".print-footer")?.textContent || "";
      Array.from(work.children).forEach(node => {
        if (node.matches(".print-footer")) return;
        if (node.matches(".print-section")) { pending.push(node.cloneNode(true)); return; }
        if (node.matches(".print-answers")) {
          if (!node.matches(".print-answers-only") && used) newPage();
          Array.from(node.children).forEach(row => {
            if (row.matches(".print-section")) pending.push(row.cloneNode(true)); else compose(row.cloneNode(true));
          });
          return;
        }
        if (node.matches(".print-question")) { compose(node.cloneNode(true), hasQuestion && options.question_breaks.includes(node.dataset.questionId)); hasQuestion = true; }
        else if (node.matches(".print-answer-row")) compose(node.cloneNode(true));
        else atomic(node.cloneNode(true));
      });
      if (pending.length) warnings.push("分区标题后没有题目，已省略空分区。");
      // Never emit an empty trailing sheet.
      while (pages.length > 1 && !bodies.at(-1).children.length) { pages.pop(); bodies.pop(); }
      pages.forEach((page, index) => {
        page.dataset.page = String(index + 1);
        page.setAttribute("aria-label", `第 ${index + 1} 页，共 ${pages.length} 页`);
        page.append(element(doc, "footer", "exam-page-number", `第 ${index + 1} 页 / 共 ${pages.length} 页${countText ? ` · ${countText}` : ""}`));
      });
      host.replaceChildren(...pages);
      host.dataset.pageCount = String(pages.length);
      scale(host);
      return { page_count: pages.length, pages, warnings: [...new Set(warnings)], formula_overflow: formulaOverflow };
    } finally { measure.remove(); }
  }
  function scale(host) {
    const available = host.clientWidth || PAGE_WIDTH;
    const factor = Math.min(1, available / PAGE_WIDTH);
    host.style.setProperty("--exam-scale", String(factor));
    host.querySelectorAll(".exam-page").forEach(page => {
      // CSS zoom recalculates glyph advances and line boxes, so a scaled
      // preview can wrap differently from the full-size PDF. Transform keeps
      // the exact A4 layout; a frame reserves only its visible dimensions.
      let frame = page.parentElement;
      if (!frame?.classList.contains("exam-page-frame")) {
        frame = element(page.ownerDocument, "div", "exam-page-frame");
        page.before(frame); frame.append(page);
      }
      frame.style.width = `${PAGE_WIDTH * factor}px`;
      frame.style.height = `${297 * MM * factor}px`;
      page.style.zoom = "1";
      page.style.transform = `scale(${factor})`;
    });
    return factor;
  }
  return { paginate, scale, normalize, keepWhole, requestedColumns, answerSpace, answerSpaceMm, alignQuestionNumber, fitFormulas, PAGE_WIDTH, BODY_WIDTH, BODY_HEIGHT };
});
