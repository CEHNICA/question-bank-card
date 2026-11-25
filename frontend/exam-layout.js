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
  // 留白统一用毫米数。旧草稿存的是 none/small/medium/large，这里继续认。
  const writingSpace = Object.freeze({ none: 0, small: 12, medium: 30, large: 60 });
  const SPACE_MAX = 200;
  // 解答题保底：再挤也得给学生留两行，否则卷面看着是糊的。
  const FREE_RESPONSE_MIN = 8;
  // 「智能」档下解答题留多少。
  const AUTO_FREE_RESPONSE = 30;
  const positions = new Set(["left", "center", "right"]);
  function pageSpec(options = {}) {
    const spec = options && options.paper_spec === "a3_2col" ? "a3_2col" : "a4";
    const seal = Boolean(options && options.seal_line);
    if (spec === "a3_2col") {
      return seal
        ? { paperSpec: "a3_2col", sealLine: true, pageWidthMm: 420, pageHeightMm: 297, colWidthMm: 180, bodyHeightMm: 261, columns: 2, colGapMm: 14, sealMarginMm: 30, outerMarginMm: 16 }
        : { paperSpec: "a3_2col", sealLine: false, pageWidthMm: 420, pageHeightMm: 297, colWidthMm: 184, bodyHeightMm: 261, columns: 2, colGapMm: 16, sealMarginMm: 18, outerMarginMm: 18 };
    }
    return seal
      ? { paperSpec: "a4", sealLine: true, pageWidthMm: 210, pageHeightMm: 297, colWidthMm: 168, bodyHeightMm: 261, columns: 1, colGapMm: 0, sealMarginMm: 28, outerMarginMm: 14 }
      : { paperSpec: "a4", sealLine: false, pageWidthMm: 210, pageHeightMm: 297, colWidthMm: 178, bodyHeightMm: 261, columns: 1, colGapMm: 0, sealMarginMm: 16, outerMarginMm: 16 };
  }
  function figureSettings(value) {
    // 老师在预览里对配图做的调整：width 是「第几张图 → 纸面上几毫米」，align 管整行
    // 靠左还是靠右，breaks 里是从下一行开始的那几张。存储层已经查过一遍，这里再收
    // 一次尾，是因为排版代码不只被草稿喂：导出 PDF 时 options 是从 JSON 直接送进
    // 来的同一个函数。
    const result = {};
    for (const [id, setting] of Object.entries(value || {})) {
      if (!setting || typeof setting !== "object") continue;
      const widths = {};
      for (const [key, millimetres] of Object.entries(setting.width || {})) {
        const index = Number(key), size = Number(millimetres);
        if (Number.isInteger(index) && index >= 0 && Number.isFinite(size) && size > 0) widths[index] = size;
      }
      const breaks = [...new Set((Array.isArray(setting.breaks) ? setting.breaks : [])
        .map(Number).filter(value => Number.isInteger(value) && value >= 0))].sort((a, b) => a - b);
      const entry = {};
      if (Object.keys(widths).length) entry.width = widths;
      // 单题显式选了位置就留下（含居中）；缺省跟随全卷 figure_align。
      if (positions.has(setting.align)) entry.align = setting.align;
      if (breaks.length) entry.breaks = breaks;
      if (Object.keys(entry).length) result[id] = entry;
    }
    return result;
  }
  // 一个留白设置 → 毫米数。解析不出来返回 null（= 没设置）。
  // "auto" 不在这里解决：它要按题型算，见 answerSpace。
  function spaceMm(value) {
    if (typeof value === "number" && Number.isFinite(value)) return Math.min(SPACE_MAX, Math.max(0, value));
    if (typeof value === "string" && Object.hasOwn(writingSpace, value)) return writingSpace[value];
    return null;
  }
  // 归一化一个留白设置：auto 原样保留，其余收敛成数字，非法值丢成 null。
  function normalizeSpace(value) {
    if (value === "auto") return "auto";
    return spaceMm(value);
  }
  function normalize(options = {}) {
    const answerSpace = normalizeSpace(options.answer_space);
    return {
      pagination: options.pagination === "keep" ? "keep" : "compact",
      option_layout: choices.has(options.option_layout) ? options.option_layout : "auto",
      option_overrides: Object.fromEntries(Object.entries(options.option_overrides || {}).filter(([, value]) => choices.has(value))),
      answer_space: answerSpace === null ? "none" : answerSpace,
      answer_space_overrides: Object.fromEntries(Object.entries(options.answer_space_overrides || {})
        .map(([id, value]) => [id, normalizeSpace(value)])
        .filter(([, value]) => value !== null)),
      question_breaks: Array.isArray(options.question_breaks) ? [...new Set(options.question_breaks.filter(value => typeof value === "string"))] : [],
      figure_align: positions.has(options.figure_align) ? options.figure_align : "right",
      figure_overrides: figureSettings(options.figure_overrides)
    };
  }
  function keepWhole(height, pageHeight, mode) { return mode === "keep" && height <= pageHeight + .5; }
  function requestedColumns(layout, count) { return layout === "vertical" ? 1 : layout === "four" && count <= 4 ? 4 : layout === "auto" ? 0 : 2; }
  // 某题最终留白多少毫米。返回数字，0 = 不留白。
  // 每道题都能有留白（不再只有解答题）；解答题有 8 毫米硬下限。
  function answerSpace(type, id, options = {}) {
    if (options.document === "answers"
      || (options.document === "combined" && options.answer_layout === "inline")) return 0;
    const normalized = normalize(options);
    const raw = Object.hasOwn(normalized.answer_space_overrides, id)
      ? normalized.answer_space_overrides[id]
      : normalized.answer_space;
    const mm = raw === "auto"
      ? (type === "free_response" ? AUTO_FREE_RESPONSE : 0)
      : (typeof raw === "number" ? raw : 0);
    return type === "free_response" && mm < FREE_RESPONSE_MIN ? FREE_RESPONSE_MIN : mm;
  }
  function answerSpaceMm(value, type) {
    if (value === "auto") return type === "free_response" ? AUTO_FREE_RESPONSE : 0;
    const mm = spaceMm(value);
    return mm === null ? 0 : mm;
  }
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
      // 图组不进这个名单：一整组图跟页面一样高的时候，整组当一块就永远放不下
      // （真实样本：九上第 227 题，六张图合起来 985px，页高 986px），二分找不到任何
      // 能放下的片段，整卷导出直接失败。组里每张图各自不许拆，组与组之间可以分页。
      // 选项反过来：一条选项列表被拆到两页比放不下更难读，所以仍按整块算。
      const whole = child.matches(atomic) || (child.matches(".qb-options, .qb-option") && outerHeight(child) <= pageHeight);
      if (whole) {
        const index = Array.prototype.indexOf.call(child.parentNode.childNodes, child);
        // 整块前面也要留一个可断点。一整块图表常常「自己放得下、跟着前面的文字就放不下」
        // （真实样本：九上第 227 题，六张图合起来 961px，加上一行文字就超过一页）。
        // 没有这个断点时二分找不到任何能放下的片段，整卷导出直接失败，
        // 而整块照旧不许拆——该分的是分页，不是这块图本身。
        const before = { node: child.parentNode, offset: index };
        const last = out[out.length - 1];
        if (!last || last.node !== before.node || last.offset !== before.offset) out.push(before);
        out.push({ node: child.parentNode, offset: index + 1 });
      } else Array.from(child.childNodes).forEach(visit);
    }
    Array.from(content.childNodes).forEach(visit);
    out.push({ node: content, offset: content.childNodes.length });
    return out;
  }
  // 报不出一句「哪道题」的话，老师就得自己一题题试。
  function describe(node) {
    if (node.matches?.(".print-question")) {
      const number = String(node.querySelector(".qb-number")?.textContent || node.dataset.questionId || "?").replace(/\.$/, "");
      return `第 ${number} 题`;
    }
    return node.matches?.(".print-answer-row") ? "答题区" : "分区标题";
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
    } else {
      // cloneContents() returns only what the range covers: when both ends fall
      // inside the same .qb-stem-body — a question running onto the next page —
      // the shared ancestors are dropped and the fragment becomes bare text
      // nodes. Only .qb-stem-body carries white-space: pre-wrap, so every blank
      // line in the stem then collapses into a space on paper. Re-wrap the cloned
      // content in shallow copies of the ancestors between the range and here.
      const shared = range.commonAncestorContainer;
      const innermost = shared.nodeType === 3 ? shared.parentNode : shared;
      if (innermost && innermost !== node && node.contains(innermost)) {
        const chain = [];
        for (let up = innermost; up && up !== node; up = up.parentNode) chain.unshift(up);
        let host = result;
        for (const ancestor of chain) {
          const copy = ancestor.cloneNode(false);
          host.append(copy);
          host = copy;
        }
        host.append(range.cloneContents());
      } else result.append(range.cloneContents());
    }
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
      // Repeat the table's header row when the next page starts inside its body.
      // 表头行是 qb-render.js 打的标记（整行都是表头格的那一行）。
      // 早先这里找的是 <thead>，可渲染出来的表格根本没有 thead —— 整段等于
      // 死代码：实测 40 行的表跨页后，第二页一个列名都没有，读者根本不知道
      // 那一列是什么。片段自带表头行时（切点刚好在表头之后）不要重复加。
      result.querySelectorAll("table").forEach(table => {
        if (table.querySelector("tr.qb-table-head-row")) return;
        const source = node.querySelector(`table[data-exam-table="${table.dataset.examTable}"] tr.qb-table-head-row`);
        if (source) table.insertBefore(source.cloneNode(true), table.firstElementChild);
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

  // .qb-figures 的行距就是它自己的 14px gap。Word 那边两行配图之间隔的是一个
  // 3.70mm 的小段落（同样是 14px），两边必须一样，所以这里插入的占位空隙也按 14px 算。
  const FIGURE_GAP_PX = 14;
  function applyFigureOverrides(work, options) {
    const normalized = normalize(options);
    const overrides = normalized.figure_overrides || {};
    const defaultAlign = normalized.figure_align || "right";
    work.querySelectorAll(".print-question").forEach(question => {
      const setting = overrides[question.dataset.questionId] || {};
      const row = question.querySelector(".qb-figures");
      if (!row) return;
      const align = positions.has(setting.align) ? setting.align : defaultAlign;
      // flex 要用 flex-start/flex-end；裸写 left/right 在部分引擎上不生效，预览会一直像居中。
      row.style.justifyContent = align === "left" ? "flex-start" : align === "right" ? "flex-end" : "center";
      const figures = Array.from(row.children).filter(child => child.classList.contains("qb-figure"));
      figures.forEach((figure, index) => {
        const millimetres = setting.width ? setting.width[index] : 0;
        const image = figure.querySelector("img");
        if (image && millimetres) image.dataset.figureWidth = String(millimetres);
      });
      const breaks = (setting.breaks || []).filter(index => index > 0 && index < figures.length).sort((a, b) => a - b);
      if (!breaks.length) return;
      // flex 只会自然换行，「这张图必须另起一行」只能用占满整宽的空隙表达：它自己独占
      // 一行，后面的图自然落到下一行。行距得单独再给一份 —— 把 row-gap 归零、让这个
      // 空隙自己长到 14px，否则空隙自己占的那一行会再吃两道 14px，行与行之间变成
      // 28px，而 Word 那边只隔 3.70mm。预览和纸面就不一样了。
      row.style.rowGap = "0";
      breaks.forEach(index => {
        const spacer = element(row.ownerDocument, "div", "qb-figure-break");
        spacer.setAttribute("aria-hidden", "true");
        spacer.style.flex = "0 0 100%";
        spacer.style.alignSelf = "flex-start";
        spacer.style.height = `${FIGURE_GAP_PX}px`;
        figures[index].before(spacer);
      });
    });
  }

  async function paginate(source, supplied = {}) {
    if (!source?.ownerDocument || !supplied.host) throw new Error("缺少 A4 排版容器");
    const options = normalize(supplied), host = supplied.host, doc = source.ownerDocument;
    const spec = pageSpec(supplied);
    const colWidthPx = spec.colWidthMm * MM, bodyHeightPx = spec.bodyHeightMm * MM;
    const warnings = [], pages = [], bodies = [];
    const measure = element(doc, "div", "exam-layout-measure print-flow");
    measure.style.width = `${spec.colWidthMm}mm`;
    measure.style.setProperty("--exam-font-size", source.style.getPropertyValue("--exam-font-size") || "12pt");
    measure.dataset.answerSpace = source.dataset.answerSpace || "none";
    doc.body.append(measure);
    const work = clean(source.cloneNode(true)); work.removeAttribute("id"); work.classList.remove("exam-source"); work.classList.add("print-flow");
    work.style.width = `${spec.colWidthMm}mm`;
    measure.append(work);
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
      applyFigureOverrides(work, options);
      await Promise.all(Array.from(work.querySelectorAll("img"), async image => {
        image.loading = "eager";
        if (typeof image.decode === "function") {
          try { await image.decode(); } catch { throw new Error("试卷配图未能载入，请重试"); }
        }
        if (!image.naturalWidth) throw new Error("试卷配图未能载入，请重试");
        // Match Word's natural 150 dpi size, capped to printable bounds.
        const chosenWidth = Number(image.dataset.figureWidth) || Number(image.dataset.solutionWidth);
        const imageScale = Math.min(chosenWidth > 0 ? Math.max(5, Math.min(chosenWidth, 178)) * MM / image.naturalWidth : 96 / 150,
          BODY_WIDTH / image.naturalWidth, 210 * MM / image.naturalHeight);
        const colScale = Math.min(imageScale, colWidthPx / image.naturalWidth);
        image.style.width = `${image.naturalWidth * colScale}px`;
        image.style.height = `${Math.max(1, Math.round(image.naturalHeight * colScale))}px`;
      }));
      work.querySelectorAll(".print-question").forEach(question => {
        alignQuestionNumber(question);
        fitOptions(question, options, warnings);
      });
      const formulaOverflow = fitFormulas(work);
      if (formulaOverflow) warnings.push(`${formulaOverflow} 处公式超出正文宽度，请调整字号或导出 Word 继续排版。`);
      work.querySelectorAll("table").forEach((table, index) => { table.dataset.examTable = String(index); });
      const probe = element(doc, "div", "exam-layout-probe");
      probe.style.width = `${spec.colWidthMm}mm`;
      measure.append(probe);
      let body, used = 0, sheetCols = [], colIndex = 0;
      function newSheet() {
        const page = element(doc, "article", "exam-page");
        if (spec.columns === 2) page.classList.add("is-a3-2col");
        page.dataset.pageWidthMm = String(spec.pageWidthMm);
        page.dataset.colWidthMm = String(spec.colWidthMm);
        page.style.setProperty("--exam-font-size", measure.style.getPropertyValue("--exam-font-size"));
        page.dataset.answerSpace = measure.dataset.answerSpace;
        if (spec.columns === 2) {
          const colsWrap = element(doc, "div", "exam-sheet-columns");
          const leftCol = element(doc, "div", "exam-page-body exam-col");
          const rightCol = element(doc, "div", "exam-page-body exam-col");
          colsWrap.append(leftCol, rightCol);
          page.append(colsWrap);
          sheetCols = [leftCol, rightCol];
          colIndex = 0;
          body = leftCol;
          bodies.push(leftCol, rightCol);
        } else {
          body = element(doc, "div", "exam-page-body");
          page.append(body);
          sheetCols = [body];
          colIndex = 0;
          bodies.push(body);
        }
        pages.push(page);
        used = 0;
      }
      function newPage() {
        if (spec.columns === 2 && colIndex === 0 && sheetCols[1]) {
          colIndex = 1;
          body = sheetCols[1];
          used = 0;
          return;
        }
        newSheet();
      }
      function height(node) { probe.replaceChildren(node); return outerHeight(node); }
      function add(node, h) { body.append(node); used += h; }
      function atomic(node) {
        const h = height(node);
        if (h > bodyHeightPx + 1) throw new Error(`${describe(node)}有图表或公式高于一页 A4，请调整字号或导出 Word 排版`);
        if (used && used + h > bodyHeightPx + .5) newPage();
        add(node, h);
      }
      newSheet();
      let pending = [], hasQuestion = false;
      function writingSpace(blank, question) {
        const MIN_WRITABLE = 76;
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
          const available = bodyHeightPx - used - (existing ? 0 : previousHeight);
          if (available < MIN_WRITABLE) { newPage(); continue; }
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
        if (blank && (options.pagination === "compact" || height(node.cloneNode(true)) > bodyHeightPx + .5)) {
          blank.remove();
          compose(node, forceBreak);
          writingSpace(blank, node);
          return;
        }
        const h = height(node.cloneNode(true));
        const pendingHeight = pending.reduce((sum, heading) => sum + height(heading.cloneNode(true)), 0);
        const full = !node.matches(".print-answer-row") && keepWhole(h, bodyHeightPx - pendingHeight, options.pagination);
        const minimum = full ? h : Math.min(h, 62);
        if (used && (forceBreak || used + pendingHeight + minimum > bodyHeightPx + .5)) newPage();
        pending.forEach(heading => atomic(heading)); pending = [];
        if (h <= bodyHeightPx - used + .5) { add(node, h); return; }
        probe.replaceChildren(node);
        const points = boundaries(node, bodyHeightPx);
        let start = 0, continued = false;
        while (start < points.length - 1) {
          let low = start + 1, high = points.length - 1, best = start, bestHeight = 0;
          while (low <= high) {
            const middle = Math.floor((low + high) / 2);
            const piece = fragment(node, points, start, middle, continued);
            const size = height(piece);
            if (size <= bodyHeightPx - used + .5) { best = middle; bestHeight = size; low = middle + 1; }
            else high = middle - 1;
          }
          if ((best === start || (!continued && bestHeight < 42)) && used) { newPage(); continue; }
          if (best === start) throw new Error(`${describe(node)}有完整公式或图表无法放进 A4，请调整字号或导出 Word 排版`);
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
      while (pages.length > 1 && !Array.from(pages.at(-1).querySelectorAll(".exam-page-body")).some(col => col.children.length > 0)) {
        pages.pop();
      }
      pages.forEach((page, index) => {
        page.dataset.page = String(index + 1);
        page.setAttribute("aria-label", `第 ${index + 1} 页，共 ${pages.length} 页`);
        if (spec.sealLine && supplied.document !== "answers") {
          const isLeft = index % 2 === 0;
          page.classList.add(isLeft ? "has-seal-left" : "has-seal-right");
          const seal = element(doc, "aside", "exam-seal-band");
          seal.setAttribute("aria-hidden", "true");
          seal.append(
            element(doc, "div", "exam-seal-fields", "学校 __________　班级 __________　姓名 __________　考号 __________"),
            element(doc, "div", "exam-seal-cut", "密　　封　　线　　内　　不　　要　　答　　题")
          );
          page.prepend(seal);
        }
        page.append(element(doc, "footer", "exam-page-number", `第 ${index + 1} 页 / 共 ${pages.length} 页${countText ? ` · ${countText}` : ""}`));
      });
      host.replaceChildren(...pages);
      host.dataset.pageCount = String(pages.length);
      host.dataset.paperSpec = spec.paperSpec;
      host.dataset.colWidthMm = String(spec.colWidthMm);
      scale(host);
      return { page_count: pages.length, pages, warnings: [...new Set(warnings)], formula_overflow: formulaOverflow, paper_spec: spec.paperSpec };
    } finally { measure.remove(); }
  }
  function scale(host) {
    const firstPage = host.querySelector(".exam-page");
    const defaultPageW = (Number(firstPage?.dataset.pageWidthMm) || 210) * MM;
    const available = host.clientWidth || defaultPageW;
    const factor = Math.min(1, available / defaultPageW);
    host.style.setProperty("--exam-scale", String(factor));
    host.querySelectorAll(".exam-page").forEach(page => {
      const pageWidthPx = (Number(page.dataset.pageWidthMm) || 210) * MM;
      let frame = page.parentElement;
      if (!frame?.classList.contains("exam-page-frame")) {
        frame = element(page.ownerDocument, "div", "exam-page-frame");
        page.before(frame); frame.append(page);
      }
      frame.style.width = `${pageWidthPx * factor}px`;
      frame.style.height = `${297 * MM * factor}px`;
      page.style.zoom = "1";
      page.style.transform = `scale(${factor})`;
    });
    return factor;
  }
  return { paginate, scale, normalize, pageSpec, keepWhole, requestedColumns, answerSpace, answerSpaceMm, spaceMm, normalizeSpace, alignQuestionNumber, fitFormulas, applyFigureOverrides, figureSettings, PAGE_WIDTH, BODY_WIDTH, BODY_HEIGHT, SPACE_MAX, FREE_RESPONSE_MIN, AUTO_FREE_RESPONSE };
});
