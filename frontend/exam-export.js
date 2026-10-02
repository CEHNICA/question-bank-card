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
    const original = item.content || {};
    if (String(original.answer ?? "").trim()) return { content: original, ai: false };
    const ai = options.ai_answers ? item.ai_answer : null;
    if (ai && (String(ai.answer ?? "").trim() || String(ai.analysis ?? "").trim())) return { content: ai, ai: true };
    return String(original.analysis ?? "").trim() ? { content: original, ai: false } : null;
  }

  function serializeItem(item, options, format, index) {
    const content = item.content || {}, fields = {};
    const add = (name, value, literal = false) => {
      try {
        const source = String(value ?? "");
        fields[name] = literal && source ? { source, blocks: [{ type: "text", start: 0, end: source.length,
          segments: [{ type: "text", start: 0, end: source.length }] }] } : serializeField(source);
      } catch (error) {
        const label = name === "stem" ? "题干" : name === "analysis" ? "解析" : name === "answer" ? "答案" : name.startsWith("options.") ? `选项 ${name.slice(-1)}` : "题源";
        throw new Error(`原卷第 ${item.number || index + 1} 题的${label}未能导出${format === "pdf" ? " PDF" : " Word"}：${error.message}`);
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

  async function download(items, { title, print_options, format = "docx" } = {}) {
    if (downloading) throw new Error("正在导出，请稍候。");
    if (!Array.isArray(items) || !items.length) throw new Error("请先选题，再导出试卷。");
    if (!["docx", "split", "pdf"].includes(format)) throw new Error("请选择 PDF、Word 或分卷 Word。");
    const options = JSON.parse(JSON.stringify(print_options || {}));
    if (!["questions", "answers", "combined"].includes(options.document)) options.document = options.answers === false ? "questions" : "combined";
    if ((options.document === "answers" || format === "split") && !items.some((item) => selectedAnswer(item, options))) throw new Error("所选题目没有可附的答案或解析，请先导出题目卷。");
    downloading = true;
    try {
      const rendered_fields = {};
      for (let index = 0; index < items.length; index++) {
        if (!items[index] || typeof items[index].id !== "string" || !items[index].id || Object.hasOwn(rendered_fields, items[index].id)) throw new Error("选题编号不完整或重复，请重新打开组卷预览。");
        rendered_fields[items[index].id] = serializeItem(items[index], options, format, index);
        if (index % 8 === 0) await new Promise((resolve) => root.setTimeout(resolve, 0));
      }
      const body = JSON.stringify({ ids: items.map((item) => item.id), title: String(title ?? "").trim() || "练习", print_options: options, rendered_fields, format });
      if (new TextEncoder().encode(body).byteLength > MAX_REQUEST) throw new Error("本次选题内容较多，请减少题目后分批导出。");
      const response = await root.fetch(format === "pdf" ? "/api/library/export-pdf" : "/api/library/export-docx", { method: "POST", headers: { "Content-Type": "application/json", "X-QB-Request": "1" }, body });
      if (!response.ok) {
        let detail; try { detail = await response.json(); } catch { /* Keep the local error. */ }
        throw new Error(detail?.error || `${format === "pdf" ? "PDF" : "Word"} 未能导出，请稍后重试。`);
      }
      const mime = response.headers.get("Content-Type")?.split(";", 1)[0].trim();
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
      return { filename, question_count: items.length };
    } finally { downloading = false; }
  }

  return Object.freeze({ serializeField, serializeFields, selectedAnswer, fileName, download });
});
