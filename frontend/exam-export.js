/* Export the published text through the same parser as the question preview.
 * No model calls, rewritten question text, remote images or renderer services.
 */
((root, factory) => {
  const api = factory(root);
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.ExamExport = api;
})(typeof window === "undefined" ? globalThis : window, (root) => {
  "use strict";
  const MAX_REQUEST = 2 * 1024 * 1024;
  const EXPLICIT = /\$\$[\s\S]+?\$\$|\$[^$\n]+?\$|\\\([\s\S]+?\\\)|\\\[[\s\S]+?\\\]/g;
  let downloading = false;

  function parser() {
    if (!root.QBRender?.typesetSegments || !root.QBRender?.findTables) throw new Error("题目排版尚未准备好，请稍后再导出。");
    return root.QBRender;
  }

  function segments(source, offset = 0) {
    const parts = parser().typesetSegments(source).map((part) => ({ ...part }));
    // The preview's special ▱ run excludes delimiters. Its \(...\) source
    // offsets use one character for the opening token; export needs both.
    for (const match of source.matchAll(EXPLICIT)) {
      if (!match[0].startsWith("\\(") || !match[0].includes("▱")) continue;
      for (const part of parts) {
        if (part.auto === false && part.start >= match.index + 1 && part.end <= match.index + match[0].length - 1) {
          part.start++; part.end++;
        }
      }
    }
    const result = [];
    let cursor = 0;
    const gap = (to) => {
      if (to <= cursor) return;
      const raw = source.slice(cursor, to);
      result.push({ type: /^(?:\${1,2}|\\[()[\]])$/.test(raw) ? "delimiter" : "text", start: offset + cursor, end: offset + to });
      cursor = to;
    };
    for (const part of parts) {
      if (part.start < cursor || part.end <= part.start || part.end > source.length) throw new Error("题目排版范围有出入，请重新打开预览后再试。");
      gap(part.start);
      const entry = { type: part.type, start: offset + part.start, end: offset + part.end };
      if (part.type === "math") {
        if (!root.katex?.renderToString) throw new Error("公式排版尚未准备好，请稍后再导出。");
        // Native Word uses the standard mathematical ∥/∦ symbols. The
        // preview's negative-space slash macros are presentation-only.
        const markup = root.katex.renderToString(part.latex, {
          output: "mathml", displayMode: Boolean(part.display), throwOnError: true,
          strict: "ignore", trust: false, maxSize: 10, maxExpand: 1000
        });
        const mathml = markup.match(/<math\b[\s\S]*?<\/math>/)?.[0];
        if (!mathml) throw new Error("公式未能完成排版，请检查预览后重试。");
        Object.assign(entry, { latex: part.latex, mathml, display: Boolean(part.display) });
        if (part.displayGroup) entry.displayGroup = part.displayGroup;
      } else if (!["text", "blank", "bracket", "parallelogram"].includes(part.type)) {
        throw new Error("这段题目暂不支持导出排版，请检查预览。");
      }
      result.push(entry);
      cursor = part.end;
    }
    gap(source.length);
    return result;
  }

  function serializeField(value) {
    const source = String(value ?? ""), blocks = [];
    if (!source) return { source, blocks };
    const tables = parser().findTables(source);
    let cursor = 0;
    const text = (end) => {
      if (end > cursor) blocks.push({ type: "text", start: cursor, end, segments: segments(source.slice(cursor, end), cursor) });
      cursor = end;
    };
    for (const table of tables) {
      if (table.start < cursor || table.end <= table.start || table.end > source.length) throw new Error("表格范围有出入，请重新打开预览后再试。");
      text(table.start);
      blocks.push({ type: "table", start: table.start, end: table.end, rows: table.rows.map((row, index) => row.map((cell) => {
        const decoded = cell.decoded ? cell.text : cell.text.replace(/\\\|/g, "|");
        return { source: decoded, header: Boolean(cell.header || (table.header && index === 0)),
          colspan: cell.colspan || 1, rowspan: cell.rowspan || 1, segments: segments(decoded) };
      })) });
      cursor = table.end;
    }
    text(source.length);
    return { source, blocks };
  }

  function selectedAnswer(item, options) {
    if (item.solution_revision !== "origin" && item.solution && (String(item.solution.answer ?? "").trim() || String(item.solution.analysis ?? "").trim() || item.solution.figures?.length)) return { content: item.solution, ai: false, edited: true };
    const original = item.content || {};
    if (String(original.answer ?? "").trim()) return { content: original, ai: false };
    const ai = options.ai_answers ? item.ai_answer : null;
    if (ai && (String(ai.answer ?? "").trim() || String(ai.analysis ?? "").trim())) return { content: ai, ai: true };
    return String(original.analysis ?? "").trim() ? { content: original, ai: false } : null;
  }

  function serializeItem(item, options, format, index) {
    const content = item.content || {}, fields = {};
    // 报错要能让人**直接在这一卷上找到那道题**。「原卷第 12 题」不够：老师手里
    // 是一份组好的卷子，不知道第 12 题是哪道、也不知道是哪份原卷翻到的。
    const where = `本卷第 ${index + 1} 题` + (item.source_filename || item.origin
      ? `（${item.source_filename || item.origin}，原卷第 ${item.number ?? "?"} 题）` : "");
    const add = (name, value, literal = false) => {
      try {
        const source = String(value ?? "");
        fields[name] = literal && source ? { source, blocks: [{ type: "text", start: 0, end: source.length,
          segments: [{ type: "text", start: 0, end: source.length }] }] } : serializeField(source);
      } catch (error) {
        const label = name === "stem" ? "题干" : name === "analysis" ? "解析" : name === "answer" ? "答案" : name.startsWith("options.") ? `选项 ${name.slice(-1)}` : "题源";
        throw new Error(`${where}的${label}未能导出${format === "pdf" ? " PDF" : " Word"}：${error.message}`);
      }
    };
    if (options.document !== "answers" || format === "split") {
      add("stem", content.stem);
      for (const key of parser().OPTION_KEYS) add(`options.${key}`, content.options?.[key]);
      if (options.origin) add("origin", item.origin || content.origin);
    }
    if (options.document !== "questions" || format === "split") {
      const selected = selectedAnswer(item, options)?.content || {};
      const answer = String(selected.answer ?? "");
      add("answer", answer, /^\s*[A-E]{1,5}\s*$/.test(answer));
      add("analysis", selected.analysis);
    }
    return fields;
  }

  function serializeFields(items, options, format = "docx") {
    const result = {};
    items.forEach((item, index) => {
      if (!item || typeof item.id !== "string" || !item.id || Object.hasOwn(result, item.id)) throw new Error("选题编号不完整或重复，请重新打开组卷预览。");
      result[item.id] = serializeItem(item, options, format, index);
    });
    return result;
  }

  function fileName(header, fallback) {
    let name = fallback;
    const encoded = header?.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
    const quoted = header?.match(/filename="([^"]+)"/i)?.[1];
    if (encoded) { try { name = decodeURIComponent(encoded.replace(/^"|"$/g, "")); } catch { /* Use the known fallback. */ } }
    else if (quoted) name = quoted;
    name = String(name).replace(/[\\/:*?"<>|\u0000-\u001f\u007f]/g, "_").replace(/[. ]+$/g, "").slice(0, 180);
    if (/^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)/i.test(name)) name = `试卷-${name}`;
    return name || "试卷.docx";
  }

  // 卷面按题型分组重排，这是**唯一**一份分组顺序：预览照它排、导出照它编号。
  // 两边各写一份的话，迟早会对不上 —— 而对不上的后果是导出报错里那个「第 N 题」
  // 指向卷面上的另一道题，老师按着找会找错。
  const PAPER_GROUPS = [["single_choice", "选择题"], ["multiple_choice", "多选题"],
    ["fill_blank", "填空题"], ["true_false", "判断题"], ["free_response", "解答题"]];

  function paperGroups(items) {
    const list = (Array.isArray(items) ? items : []).filter((item) => item && typeof item === "object");
    const known = new Set(PAPER_GROUPS.map(([key]) => key));
    const groups = [];
    for (const [key, name] of PAPER_GROUPS) {
      const group = list.filter((item) => item.question_type === key);
      if (group.length) groups.push([name, group]);
    }
    const others = list.filter((item) => !known.has(item.question_type));
    if (others.length) groups.push(["其他", others]);
    return groups;
  }

  function paperOrder(items) {
    return paperGroups(items).flatMap(([, group]) => group.map((item) => item.id));
  }

  async function download(items, { title, print_options, solutions, format = "docx", preview_page_count } = {}) {
    if (downloading) throw new Error("正在导出，请稍候。");
    if (!Array.isArray(items) || !items.length) throw new Error("请先选题，再导出试卷。");
    if (!["docx", "split", "pdf"].includes(format)) throw new Error("请选择 PDF、Word 或分卷 Word。");
    const options = JSON.parse(JSON.stringify(print_options || {}));
    if (!["questions", "answers", "combined"].includes(options.document)) options.document = options.answers === false ? "questions" : "combined";
    if ((options.document === "answers" || format === "split") && !items.some((item) => selectedAnswer(item, options))) throw new Error("所选题目没有可附的答案或解析，请先导出题目卷。");
    downloading = true;
    try {
      // 先验编号，再排顺序 —— 排顺序要读 item.question_type，坏数据不能走到那一步。
      const seenIds = new Set();
      for (const item of items) {
        if (!item || typeof item.id !== "string" || !item.id || seenIds.has(item.id)) throw new Error("选题编号不完整或重复，请重新打开组卷预览。");
        seenIds.add(item.id);
      }
      // 按卷面顺序发，不是按试题篮顺序。后端报的「第 N 题」是 ids 里的位置，
      // 篮里的顺序跟卷面（按题型重排）不是一回事时，老师按那个号去卷子上找会找错。
      // 排出来的文件本身不受影响：后端还会按题型再分一次组，组内顺序不变。
      const rank = new Map(paperOrder(items).map((id, index) => [id, index]));
      const ordered = [...items].sort((a, b) => (rank.get(a.id) ?? items.length) - (rank.get(b.id) ?? items.length));
      const rendered_fields = {};
      for (let index = 0; index < ordered.length; index++) {
        rendered_fields[ordered[index].id] = serializeItem(ordered[index], options, format, index);
        if (index % 8 === 0) await new Promise((resolve) => root.setTimeout(resolve, 0));
      }
      const selectedIds = new Set(items.map(item => item.id));
      // Carry the same paper-only choices to both PDF and Word. Removed
      // publications cannot revive an old option or writing-space override.
      if (options.option_overrides) options.option_overrides = Object.fromEntries(Object.entries(options.option_overrides)
        .filter(([id, value]) => selectedIds.has(id) && ["auto", "four", "two", "vertical"].includes(value)));
      if (options.answer_space_overrides) options.answer_space_overrides = Object.fromEntries(Object.entries(options.answer_space_overrides)
        .filter(([id, value]) => selectedIds.has(id) && ["none", "small", "medium", "large"].includes(value)));
      const fixedSolutions = Object.fromEntries(Object.entries(solutions || {}).filter(([id, revision]) => selectedIds.has(id) && typeof revision === "string" && revision));
      // The PDF the teacher checked on screen is the PDF they get. Send the page
      // count the preview settled on so the server can refuse a file that would
      // not match it, instead of silently handing over a different paper.
      const body = JSON.stringify({ ids: ordered.map((item) => item.id), title: String(title ?? "").trim() || "练习", print_options: options, rendered_fields, solutions: fixedSolutions, format,
        ...(format === "pdf" && Number.isInteger(preview_page_count) && preview_page_count > 0 ? { preview_page_count } : {}) });
      if (new TextEncoder().encode(body).byteLength > MAX_REQUEST) throw new Error("本次选题内容较多，请减少题目后分批导出。");
      const headers = { "Content-Type": "application/json", "X-QB-Request": "1" };
      // A machine preference is deliberately separate from this paper/draft.
      // Browser-only servers and an unavailable settings endpoint still return
      // the normal attachment; no client path is sent to the export builder.
      try {
        const preferences = await root.fetch("/api/export-preferences", { headers: { "X-QB-Request": "1" }, cache: "no-store" });
        const value = preferences.ok ? await preferences.json() : null;
        if (value?.desktop_capable === true && typeof value.directory === "string" && value.directory) headers["X-QB-Export-Delivery"] = "configured";
      } catch { /* An ordinary download remains available. */ }
      const response = await root.fetch(format === "pdf" ? "/api/library/export-pdf" : "/api/library/export-docx", { method: "POST", headers, body });
      if (!response.ok) {
        let detail; try { detail = await response.json(); } catch { /* Keep the local error. */ }
        throw new Error(detail?.error || `${format === "pdf" ? "PDF" : "Word"} 未能导出，请稍后重试。`);
      }
      const mime = response.headers.get("Content-Type")?.split(";", 1)[0].trim();
      if (mime === "application/json") {
        const receipt = await response.json();
        if (receipt?.saved !== true || receipt.question_count !== items.length
            || Number(response.headers.get("X-Question-Count")) !== items.length
            || typeof receipt.filename !== "string" || !receipt.filename
            || typeof receipt.path !== "string" || typeof receipt.directory !== "string"
            || typeof receipt.file_token !== "string" || !/^[A-Za-z0-9_-]{20,80}$/.test(receipt.file_token)) throw new Error("导出保存记录不完整，请在导出目录中确认文件。");
        return receipt;
      }
      const expected = format === "pdf" ? "application/pdf" : format === "split" ? "application/zip" : "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
      if (mime !== expected || Number(response.headers.get("X-Question-Count")) !== items.length) throw new Error("导出文件或题目数量有出入，未下载不完整的试卷。");
      const blob = await response.blob();
      const signature = new Uint8Array(await blob.slice(0, format === "pdf" ? 5 : 4).arrayBuffer());
      if (signature.join(",") !== (format === "pdf" ? "37,80,68,70,45" : "80,75,3,4")) throw new Error("导出文件不完整，请重试。");
      const filename = fileName(response.headers.get("Content-Disposition"), `${title || "练习"}.${format === "pdf" ? "pdf" : format === "split" ? "zip" : "docx"}`);
      const url = root.URL.createObjectURL(blob), link = root.document.createElement("a");
      link.href = url; link.download = filename; link.hidden = true;
      root.document.body.append(link);
      try { link.click(); } finally { link.remove(); root.setTimeout(() => root.URL.revokeObjectURL(url), 60000); }
      let warning = [response.headers.get("X-QB-Layout-Warning"), response.headers.get("X-QB-Export-Warning")].filter(Boolean).join(" ");
      try { warning = decodeURIComponent(warning); } catch { warning = "导出设置有调整，请检查下载的完整试卷。"; }
      return { filename, question_count: items.length, ...(warning ? { warning } : {}) };
    } finally { downloading = false; }
  }

  return Object.freeze({ serializeField, serializeFields, selectedAnswer, fileName, paperGroups, paperOrder, download });
});
