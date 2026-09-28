/*
 * 逐题核对页。
 * 一道题 = 一张卡：左边是原卷里这道题的截图，右边是 AI 给出的最终题面和配图。
 * 人只做三件事：对了点"通过"；字错了点"改字"；截图范围或配图不对就拖一下。
 *
 * 交互：J/K 在题卡间移动，Enter 通过并跳到下一张，Space 放大对照原卷，
 * E 改字，U 撤销通过，? 查看全部快捷键。题卡原卷截图上悬停会出现放大镜。
 */
const QBUpload = (() => {
  "use strict";

  const RULES = [
    { category: "photo", label: "照片", extension: /\.(jpe?g|png|webp)$/i, mime: /^image\/(jpeg|png|webp)$/i },
    { category: "pdf", label: "PDF", extension: /\.pdf$/i, mime: /^application\/pdf$/i },
    {
      category: "docx", label: "Word", extension: /\.docx$/i,
      mime: /^application\/vnd\.openxmlformats-officedocument\.wordprocessingml\.document$/i
    }
  ];

  function classifyFile(file) {
    const name = String(file?.name || "");
    const type = String(file?.type || "");
    const rule = RULES.find((candidate) => candidate.extension.test(name) || candidate.mime.test(type));
    return rule ? { category: rule.category, label: rule.label, supported: true }
      : { category: "unsupported", label: "不支持", supported: false };
  }

  function isEditingTarget(target) {
    if (!target) return false;
    if (typeof target.closest === "function") {
      return Boolean(target.closest('input, textarea, select, [contenteditable]:not([contenteditable="false"])'));
    }
    const tag = String(target.tagName || "").toLowerCase();
    return ["input", "textarea", "select"].includes(tag) || target.isContentEditable === true;
  }

  function shouldInterceptPaste(target, clipboardData) {
    if (isEditingTarget(target)) return false;
    return Boolean(clipboardData?.files?.length);
  }

  function pad(value) { return String(value).padStart(2, "0"); }

  function screenshotName(now, sequence, extension) {
    const stamp = `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`;
    return `剪贴板截图-${stamp}${sequence > 1 ? `-${sequence}` : ""}.${extension}`;
  }

  function imageExtension(file) {
    const fromName = String(file?.name || "").match(/\.(jpe?g|png|webp)$/i)?.[1]?.toLowerCase();
    if (fromName) return fromName === "jpeg" ? "jpg" : fromName;
    const subtype = String(file?.type || "").toLowerCase().split("/")[1];
    return subtype === "jpeg" ? "jpg" : (["jpg", "png", "webp"].includes(subtype) ? subtype : "png");
  }

  function isGenericClipboardImage(file) {
    if (classifyFile(file).category !== "photo") return false;
    const name = String(file?.name || "").trim();
    return !name || /^(image|clipboard|pasted-image)(?:\.(?:jpe?g|png|webp))?$/i.test(name);
  }

  function prepareClipboardFiles(fileList, now = new Date(), FileCtor = globalThis.File) {
    let screenshotSequence = 0;
    return Array.from(fileList || [], (file) => {
      if (!isGenericClipboardImage(file) || typeof FileCtor !== "function") return file;
      screenshotSequence += 1;
      return new FileCtor([file], screenshotName(now, screenshotSequence, imageExtension(file)), {
        type: file.type || `image/${imageExtension(file)}`,
        lastModified: file.lastModified || now.getTime()
      });
    });
  }

  function routeFiles(fileList) {
    const files = Array.from(fileList || []);
    const pictures = [];
    const documents = [];
    const unsupported = [];
    files.forEach((file) => {
      const info = classifyFile(file);
      if (info.category === "photo") pictures.push(file);
      else if (info.supported) documents.push(file);
      else unsupported.push(file);
    });
    return { files, pictures, documents, unsupported, accepted: files.filter((file) => classifyFile(file).supported) };
  }

  function buildClipboardBatch(fileList, now = new Date(), FileCtor = globalThis.File) {
    const files = prepareClipboardFiles(fileList, now, FileCtor);
    const items = files.map((file, index) => ({ index, file, ...classifyFile(file) }));
    const accepted = items.filter((item) => item.supported).map((item) => item.file);
    return { files, items, accepted, unsupported: items.filter((item) => !item.supported).map((item) => item.file) };
  }

  async function runConfirmedPaste(files, confirm, upload) {
    if (!files?.length) return false;
    if (!(await confirm())) return false;
    await upload(files);
    return true;
  }

  return {
    classifyFile, isEditingTarget, shouldInterceptPaste, screenshotName,
    prepareClipboardFiles, routeFiles, buildClipboardBatch, runConfirmedPaste
  };
})();

const QBProgress = (() => {
  "use strict";

  const STAGES = [
    { key: "queued", label: "排队" },
    { key: "parsing", label: "MinerU 解析" },
    { key: "segmenting", label: "本机切题" },
    { key: "reading", label: "AI 读题" },
    { key: "ready", label: "待审核" }
  ];

  function safeNumber(value, fallback = 0) {
    const number = Number(value);
    return Number.isFinite(number) && number >= 0 ? number : fallback;
  }

  function formatDuration(value) {
    const seconds = Math.floor(safeNumber(value));
    if (seconds < 60) return `${seconds}秒`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}分${seconds % 60}秒`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)}小时${Math.floor((seconds % 3600) / 60)}分`;
    return `${Math.floor(seconds / 86400)}天${Math.floor((seconds % 86400) / 3600)}小时`;
  }

  function formatAge(value) {
    const seconds = Math.floor(safeNumber(value));
    return seconds < 3 ? "刚刚" : `${formatDuration(seconds)}前`;
  }

  function pageRanges(chunks) {
    const ranges = Array.isArray(chunks?.active_ranges) ? chunks.active_ranges : [];
    const shown = ranges.slice(0, 3).map((item) => {
      const start = safeNumber(item?.page_start);
      const end = safeNumber(item?.page_end);
      return start === end ? `第 ${start} 页` : `第 ${start}–${end} 页`;
    });
    if (ranges.length > shown.length) shown.push(`另 ${ranges.length - shown.length} 个分片`);
    return shown.join("、");
  }

  function processingPresentation(paper) {
    const raw = paper?.processing || {};
    const stage = raw.stage || paper?.status || "";
    const elapsed = safeNumber(raw.elapsed_seconds);
    const idle = safeNumber(raw.idle_seconds);
    const completed = safeNumber(raw.completed, safeNumber(paper?.progress));
    const total = safeNumber(raw.total, safeNumber(paper?.total));
    const queueAhead = safeNumber(raw.queue_ahead);
    const parts = [];
    let headline = raw.stage_label || paper?.status_label || "处理中";

    if (stage === "queued") {
      headline = queueAhead ? `排队中 · 前面还有 ${queueAhead} 项任务` : "排队中 · 即将开始";
      parts.push("程序会按任务创建顺序开始处理");
    } else if (stage === "parsing") {
      if (raw.chunks && total > 0) {
        headline = `MinerU 解析中 · 已完成 ${completed}/${total} 个分片`;
        const activePages = pageRanges(raw.chunks);
        if (activePages) parts.push(`正在处理${activePages}`);
        else if (completed >= total) parts.push("所有分片均已解析，正在合并结果");
        else parts.push(`尚有 ${total - completed} 个分片等待开始`);
      } else {
        headline = "MinerU 解析中";
        parts.push("正在准备文件或等待 MinerU 返回；MinerU 没有提供完成百分比");
      }
    } else if (stage === "segmenting") {
      headline = "本机切题中";
      parts.push("正在本机整理题号、题目范围和配图候选；这个阶段没有可靠百分比");
    } else if (stage === "reading") {
      headline = total ? `AI 读题中 · 已完成 ${completed}/${total}` : "AI 读题中";
      if (total) {
        const remaining = Math.max(0, total - completed);
        parts.push(remaining ? `剩余 ${remaining} 道，完成的题卡会陆续出现` : "全部题目已读完，正在整理结果");
      } else parts.push("题目总数尚未确定，完成的题卡会陆续出现");
    }

    parts.push(`任务创建至今 ${formatDuration(elapsed)}`);
    parts.push(`本任务状态最近更新 ${formatAge(idle)}`);
    // 排队任务在前一份任务结束前不会改写自己的 updated_at。
    // 队列数正在下降时把这叫作“后台停滞”会误导用户，因此排队阶段不报 stale。
    const stale = stage !== "queued" && idle >= 240
      ? `已有 ${formatDuration(idle)}没有新的本任务状态更新；程序仍在等待${stage === "parsing" ? " MinerU 或本机处理" : stage === "reading" ? "模型或后台处理" : "后台处理"}，这不等同于失败。`
      : "";
    const determinate = Boolean(raw.determinate && total > 0 && ["parsing", "reading"].includes(stage));
    return {
      stage,
      headline,
      detail: `${parts.join(" · ")}。`,
      stale,
      determinate,
      ratio: determinate ? Math.max(0, Math.min(1, completed / total)) : null,
      stageIndex: STAGES.findIndex((item) => item.key === stage)
    };
  }

  return { STAGES, formatDuration, formatAge, processingPresentation };
})();

const QBSelection = (() => {
  "use strict";

  // 纯数据版本的选择规则，页面与静态测试共用。Shift 只沿当前可见顺序取连续范围；
  // Ctrl/Cmd 和题卡上的选择按钮只切换一张，永远不会在选择时直接删除。
  function updateSelection({ order = [], eligible = order, selected = [], target, anchor = null, range = false, additive = false }) {
    const ordered = order.map(Number);
    const allowed = new Set(eligible.map(Number));
    const targetId = Number(target);
    const current = new Set(selected.map(Number).filter((id) => ordered.includes(id) && allowed.has(id)));
    if (!allowed.has(targetId) || !ordered.includes(targetId)) return { selected: [...current], anchor };

    if (range && anchor !== null && ordered.includes(Number(anchor))) {
      const start = ordered.indexOf(Number(anchor));
      const end = ordered.indexOf(targetId);
      const next = additive ? current : new Set();
      ordered.slice(Math.min(start, end), Math.max(start, end) + 1)
        .filter((id) => allowed.has(id))
        .forEach((id) => next.add(id));
      return { selected: [...next], anchor: Number(anchor) };
    }

    if (current.has(targetId)) current.delete(targetId);
    else current.add(targetId);
    return { selected: [...current], anchor: targetId };
  }

  return { updateSelection };
})();

const QBReviewDiff = (() => {
  "use strict";

  const FIELD_NAMES = { stem: "题干", A: "选项 A", B: "选项 B", C: "选项 C", D: "选项 D" };
  const READER_NAMES = { a: "读法甲", b: "读法乙", c: "第三次裁决" };

  function readingOk(reading) {
    return reading && !reading.error && typeof reading.stem === "string";
  }

  function mergeRanges(ranges) {
    return [...ranges].sort((left, right) => left.start - right.start || left.end - right.end)
      .reduce((result, range) => {
        const previous = result[result.length - 1];
        if (previous && range.start <= previous.end) previous.end = Math.max(previous.end, range.end);
        else result.push({ ...range });
        return result;
      }, []);
  }

  function analyze(question, renderer) {
    const empty = { marks: {}, observedOnly: [], hasContentDifference: false, hasVisibleMarks: false };
    if (!question || question.edited || question.state !== "yellow" || !renderer) return empty;
    const reads = Object.entries(question.reads || {}).filter(([, reading]) => readingOk(reading));
    if (reads.length < 2) return empty;

    const marks = {};
    const observedOnly = [];
    let hasContentDifference = false;
    ["stem", "A", "B", "C", "D"].forEach((field) => {
      const final = field === "stem" ? question.stem : (question.options || {})[field] || "";
      const currentRanges = [];
      reads.forEach(([readerKey, reading]) => {
        const observed = field === "stem" ? reading.stem : (reading.options || {})[field] || "";
        const comparison = renderer.compareTexts(final, observed);
        if (comparison.level !== "content") return;
        hasContentDifference = true;
        comparison.current.forEach((range) => currentRanges.push(range));
        renderer.comparisonHunks(final, observed).forEach((hunk) => {
          if (hunk.current.start !== hunk.current.end || hunk.observed.start === hunk.observed.end) return;
          const value = observed.slice(hunk.observed.start, hunk.observed.end).trim();
          if (!value) return;
          const signature = `${field}:${value}`;
          if (observedOnly.some((item) => item.signature === signature)) return;
          observedOnly.push({
            signature, field, fieldName: FIELD_NAMES[field], reader: readerKey,
            readerName: READER_NAMES[readerKey] || readerKey, text: value,
          });
        });
      });
      const merged = mergeRanges(currentRanges);
      if (merged.length) marks[field] = merged.map((range) => ({ ...range, kind: "is-change current" }));
    });
    return { marks, observedOnly, hasContentDifference, hasVisibleMarks: Object.keys(marks).length > 0 };
  }

  return { analyze, mergeRanges };
})();

const QBResegment = (() => {
  "use strict";

  const CATEGORIES = [
    { key: "kept", label: "原样保留", detail: "来源和范围不变，不会重新识读", tone: "safe" },
    { key: "added", label: "新增题卡", detail: "新规则新找到的题卡，将进入识读", tone: "change" },
    { key: "locally_trimmed", label: "例题去解", detail: "只去除分析/解答尾部，不调用识读模型", tone: "safe" },
    { key: "range_changed", label: "范围变化", detail: "撤销旧审批并重新识读", tone: "change" },
    { key: "suspected_excluded", label: "系统移入回收站", detail: "新规则未再命中的自动题卡，可恢复", tone: "danger" },
    { key: "protected_unmatched", label: "受保护", detail: "含人工或入库记录，保留并标黄", tone: "warning" },
    { key: "too_long", label: "异常超长", detail: "范围触及安全上限，应用后需优先检查", tone: "warning" },
  ];

  function normalizeReport(value) {
    const report = value && typeof value === "object" ? value : {};
    const summary = report.summary && typeof report.summary === "object" ? report.summary : {};
    const items = report.items && typeof report.items === "object" ? report.items : {};
    return {
      readOnly: report.read_only === true,
      modelCalls: Number.isFinite(Number(report.model_calls)) ? Number(report.model_calls) : null,
      categories: CATEGORIES.map((category) => ({
        ...category,
        count: Math.max(0, Number.parseInt(summary[category.key], 10) || 0),
        items: Array.isArray(items[category.key]) ? items[category.key] : [],
      })),
      notes: Array.isArray(report.notes) ? report.notes.map(String) : [],
    };
  }

  function itemTitle(item) {
    const group = String(item?.group || "").trim();
    const number = item?.number === null || item?.number === undefined ? "题号未定" : `第 ${item.number} 题`;
    const pages = Array.isArray(item?.pages) ? item.pages.filter((page) => Number.isFinite(Number(page))).map(Number) : [];
    const pageText = pages.length ? ` · 第 ${pages.join("、")} 页` : "";
    return `${group ? `${group} · ` : ""}${number}${pageText}`;
  }

  return { CATEGORIES, normalizeReport, itemTitle };
})();

if (typeof module !== "undefined" && module.exports) {
  module.exports = { ...QBUpload, ...QBProgress, ...QBSelection, ...QBReviewDiff, ...QBResegment };
}

if (typeof window !== "undefined" && typeof document !== "undefined") {
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const R = window.QBRender;
  const OPTION_KEYS = ["A", "B", "C", "D"];
  const CHOICE = new Set(["single_choice", "multiple_choice"]);
  const TYPE_NAMES = { single_choice: "单选题", multiple_choice: "多选题", fill_blank: "填空题", free_response: "解答题", unknown: "题型未定" };
  const SLOT_NAMES = { stem: "题干", A: "选项A", B: "选项B", C: "选项C", D: "选项D" };
  const FILTERS = [
    { key: "all", label: "全部" },
    { key: "todo", label: "需逐题核对" },
    { key: "green", label: "识读一致" },
    { key: "approved", label: "已标记通过" }
  ];
  const ACTIVE_STATUS = new Set(["queued", "parsing", "segmenting", "reading"]);

  const state = {
    status: null, papers: [], paperId: null, paper: null, questions: [], filter: "all",
    rendered: new Map(), editing: new Set(), expanded: new Set(), pollTimer: null, listTimer: null,
    current: null, lens: readPref("qb-lens", "1") === "1",
    selected: new Set(), selectionAnchor: null, selectionBusy: false, trashBusy: false
  };

  // ---------------------------------------------------------------- 小工具

  function readPref(key, fallback) {
    try { return window.localStorage.getItem(key) ?? fallback; } catch { return fallback; }
  }

  function writePref(key, value) {
    try { window.localStorage.setItem(key, value); } catch { /* 浏览器禁止存储时只在本页有效 */ }
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function icon(name, extra = "") {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", `icon ${extra}`.trim());
    svg.setAttribute("aria-hidden", "true");
    const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
    use.setAttribute("href", `#i-${name}`);
    svg.append(use);
    return svg;
  }

  function button(label, className, onClick, title, { key, iconName } = {}) {
    const node = el("button", `button ${className || ""}`.trim());
    node.type = "button";
    if (iconName) node.append(icon(iconName));
    node.append(document.createTextNode(label));
    if (key) node.append(el("span", "kbd-hint", key));
    if (title) node.title = title;
    node.addEventListener("click", onClick);
    return node;
  }

  async function api(path, { method = "GET", body, form } = {}) {
    const options = { method, headers: {}, cache: "no-store" };
    if (method !== "GET") options.headers["X-QB-Request"] = "1";
    if (form) options.body = form;
    else if (body !== undefined) {
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(body);
    }
    const response = await fetch(path, options);
    let data = {};
    try { data = await response.json(); } catch { /* 空响应 */ }
    if (!response.ok) throw new Error(data.error || `请求失败（${response.status}）`);
    return data;
  }

  let toastTimer = null;
  function toast(message, kind = "", action = null) {
    const node = $("toast");
    node.replaceChildren(el("span", "toast-text", message));
    if (action) {
      const actionButton = el("button", "toast-action", action.label);
      actionButton.type = "button";
      actionButton.addEventListener("click", () => { showToast(node, false); action.onClick(); });
      node.append(actionButton);
    }
    node.className = `toast ${kind}`.trim();
    showToast(node, true);
    // 重新触发入场动画
    node.style.animation = "none";
    void node.offsetWidth;
    node.style.animation = "";
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => showToast(node, false), action ? 6500 : kind === "error" ? 6000 : 3800);
  }

  // 提示条用 popover 放进浏览器顶层，打开对话框时也能看见；旧浏览器退回 hidden。
  function showToast(node, open) {
    if (typeof node.showPopover === "function") {
      try {
        if (node.matches(":popover-open")) node.hidePopover();
        if (open) node.showPopover();
      } catch { /* 已关闭 */ }
    } else node.hidden = !open;
  }

  // 统一样式的确认框，替代浏览器自带的 confirm。
  function confirmDialog({ title, text = "", ok = "确定", danger = false }) {
    const dialog = $("confirmDialog");
    $("confirmTitle").textContent = title;
    $("confirmText").textContent = text;
    $("confirmText").hidden = !text;
    $("confirmOk").textContent = ok;
    $("confirmIconUse").setAttribute("href", danger ? "#i-alert" : "#i-question");
    dialog.classList.toggle("danger", danger);
    dialog.returnValue = "";
    dialog.showModal();
    $("confirmOk").focus();
    return new Promise((resolve) => {
      dialog.addEventListener("close", () => resolve(dialog.returnValue === "ok"), { once: true });
    });
  }

  function previewUrl(paperId, page) {
    // 照片卷调整过页序后，同一个页码对应的图变了：带上页序版本，浏览器就不会用旧缓存。
    const version = state.paper && state.paper.id === paperId ? state.paper.pages_version : "";
    return `/api/papers/${paperId}/pages/${page}/preview${version ? `?v=${version}` : ""}`;
  }

  function pageInfo(page) {
    return (state.paper?.pages || []).find((item) => item.page_idx === page) || { width: 1000, height: 1414 };
  }

  function questionCompare(a, b) {
    const group = (a.group?.sequence ?? 0) - (b.group?.sequence ?? 0);
    if (group) return group;
    const number = a.number - b.number;
    return number || a.id - b.id;
  }

  function hasMultipleQuestionGroups() {
    return new Set(state.questions.map((question) => question.group?.id).filter((id) => id != null)).size > 1;
  }

  // 审批绑定到题面版本。兼容尚未返回新字段的旧服务，但只要后端明确
  // 表示哈希失配，就绝不能把旧审批当成当前版本已通过。
  function approvalNeedsReview(q) {
    return Boolean(q.approval_stale || (q.approved && q.approval_valid === false));
  }

  const FIGURE_REVIEW_BLOCKS = new Set(["blocked_missing", "conflict"]);

  function legacyFigureFlag(q) {
    return (q.flags || []).find((flag) => /还没有配图|原卷可能有图没有被找到|选项是图/.test(String(flag))) || "";
  }

  // 新服务会返回结构化的 figure_review；保留对旧 flags/figure_blocked 的兼容，
  // 这样前端和后端分步更新时也不会放过一张明确提示漏图的题卡。
  function figureReview(q) {
    const raw = q.figure_review;
    let review = typeof raw === "string" ? { status: raw }
      : raw && typeof raw === "object" ? { ...raw } : null;
    const legacy = legacyFigureFlag(q);
    if (!review && (q.figure_blocked || legacy)) {
      review = { status: "blocked_missing", reason: legacy };
    }
    if (!review?.status) return null;
    if (!review.reason && legacy && FIGURE_REVIEW_BLOCKS.has(review.status)) review.reason = legacy;
    return review;
  }

  function figureBlocksApproval(q) {
    return FIGURE_REVIEW_BLOCKS.has(figureReview(q)?.status);
  }

  function isApproved(q) {
    return Boolean(q.approved && q.approval_valid !== false && !q.approval_stale && !figureBlocksApproval(q));
  }

  function canApprove(q) {
    return Boolean(q.stem && !figureBlocksApproval(q) && (q.state === "green" || q.state === "yellow"));
  }

  function needsCheck(q) {
    return !isApproved(q) && (figureBlocksApproval(q) || approvalNeedsReview(q) || q.state === "yellow" || q.state === "red");
  }

  function anyDialogOpen() {
    return [...document.querySelectorAll("dialog")].some((dialog) => dialog.open);
  }

  // ---------------------------------------------------------------- 配置与试卷列表

  async function loadStatus() {
    try {
      state.status = await api("/api/status");
    } catch {
      $("engineLine").textContent = "无法连接本机服务";
      return false;
    }
    const s = state.status;
    const reader = s.reader ? `读题 ${s.reader}` : "所选主读模型未配置，无法读题";
    const checker = s.checker ? (s.independent_checker ? `复核 ${s.checker}（另一家模型）` : `复核 ${s.checker}（同一模型再独立读一遍）`) : "";
    $("engineLine").textContent = [reader, checker].filter(Boolean).join(" · ");
    $("engineLine").title = $("engineLine").textContent;
    const note = $("uploadNote");
    if (!s.upload_enabled) {
      note.hidden = false;
      note.textContent = !s.mineru ? "没有配置 MinerU Token，暂时不能上传新卷；已有的题卡照常可用。"
        : "没有配置所选主读模型的 API Key，暂时不能上传新资料。";
      $("dropZone").classList.add("disabled");
      $("dropZone").setAttribute("aria-disabled", "true");
      $("fileInput").disabled = true;
    } else {
      note.hidden = true;
      $("dropZone").classList.remove("disabled");
      $("dropZone").removeAttribute("aria-disabled");
      $("fileInput").disabled = false;
    }
    $("m3Button").hidden = !s.m3_available;
    renderSettingsModels();
    return true;
  }

  function paperSummary(paper) {
    if (ACTIVE_STATUS.has(paper.status)) {
      return QBProgress.processingPresentation(paper).headline;
    }
    if (paper.status === "failed") return paper.recoverable_pause
      ? (paper.status_label || "额度不足，已暂停") : "处理失败";
    if (paper.status === "needs_grouping") return "等待确认资料结构";
    const c = paper.counts || {};
    const parts = [`${c.total || 0} 题`];
    const todo = (c.yellow || 0) + (c.red || 0);
    if (todo) parts.push(`${todo} 张要看`);
    if (c.approved) parts.push(`已通过 ${c.approved}`);
    if (c.published) parts.push(`已入库 ${c.published}`);
    return parts.join(" · ");
  }

  function paperDisplayName(paper) {
    const name = typeof paper?.name === "string" ? paper.name.trim() : "";
    return name || paper?.filename || "未命名试卷";
  }

  function miniMeter(paper) {
    const c = paper.counts || {};
    const total = c.total || 0;
    const bar = el("span", "mini-meter");
    if (!total) return bar;
    [["approved", "var(--accent)"], ["green", "var(--green-bar)"], ["yellow", "var(--amber-bar)"], ["red", "var(--red)"]].forEach(([key, color]) => {
      if (!c[key]) return;
      const part = el("span");
      part.style.width = `${(c[key] / total) * 100}%`;
      part.style.background = color;
      bar.append(part);
    });
    return bar;
  }

  function renderPaperList() {
    const list = $("paperList");
    list.replaceChildren();
    $("paperCount").textContent = state.papers.length ? String(state.papers.length) : "";
    if (!state.papers.length) {
      list.append(el("li", "side-empty", "还没有试卷"));
      return;
    }
    state.papers.forEach((paper) => {
      const item = el("li", `paper-item${paper.id === state.paperId ? " active" : ""}${paper.status === "failed" ? " failed" : ""}`);
      const link = el("button", "paper-link");
      link.type = "button";
      link.title = paperDisplayName(paper);
      link.append(el("span", "paper-file", paperDisplayName(paper)));
      const meta = el("span", "paper-meta", paperSummary(paper));
      if (ACTIVE_STATUS.has(paper.status)) meta.classList.add("busy");
      link.append(meta, miniMeter(paper));
      if (paper.id === state.paperId) link.setAttribute("aria-current", "true");
      link.addEventListener("click", () => selectPaper(paper.id));
      item.append(link);
      list.append(item);
    });
  }

  async function loadPapers() {
    try {
      const data = await api("/api/papers");
      state.papers = data.papers;
      renderPaperList();
    } catch (error) {
      toast(error.message, "error");
    }
    clearTimeout(state.listTimer);
    const busy = state.papers.some((paper) => ACTIVE_STATUS.has(paper.status));
    state.listTimer = setTimeout(loadPapers, busy ? 3000 : 15000);
  }

  // ---------------------------------------------------------------- 当前试卷

  function clearQuestionSelection({ render = true } = {}) {
    state.selected.clear();
    state.selectionAnchor = null;
    if (render) renderSelectionState();
  }

  async function selectPaper(id) {
    if (state.paperId !== id) {
      clearQuestionSelection({ render: false });
      state.paperId = id;
      state.rendered.clear();
      state.editing.clear();
      state.expanded.clear();
      state.filter = "all";
      state.current = null;
      $("cards").replaceChildren();
      const url = new URL(window.location.href);
      url.searchParams.set("paper", id);
      url.searchParams.delete("document");
      url.searchParams.delete("draft");
      history.replaceState(null, "", url);
      window.scrollTo({ top: 0 });
    }
    renderPaperList();
    await refreshPaper();
  }

  function clearPaperSelection() {
    clearTimeout(state.pollTimer);
    state.paperId = null;
    state.paper = null;
    state.questions = [];
    state.current = null;
    clearQuestionSelection({ render: false });
    state.rendered.clear();
    state.editing.clear();
    state.expanded.clear();
    $("cards").replaceChildren();
    $("paperView").hidden = true;
    $("emptyState").hidden = false;
    const url = new URL(window.location.href);
    url.searchParams.delete("paper");
    url.searchParams.delete("document");
    url.searchParams.delete("draft");
    history.replaceState(null, "", url);
    renderPaperList();
    renderSettingsTask();
  }

  async function refreshPaper() {
    clearTimeout(state.pollTimer);
    if (!state.paperId) return;
    const paperId = state.paperId;
    let data;
    try {
      data = await api(`/api/papers/${paperId}`);
    } catch (error) {
      toast(error.message, "error");
      // 本机服务短暂重启或网页一次请求失败时，不能让进度永久停在旧画面。
      if (state.paperId === paperId) state.pollTimer = setTimeout(refreshPaper, 5000);
      return;
    }
    if (state.paperId !== paperId) return;
    state.paper = data.paper;
    state.questions = data.questions;
    const existing = new Set(state.questions.map((question) => question.id));
    state.selected = new Set([...state.selected].filter((id) => existing.has(id)));
    if (state.selectionAnchor !== null && !existing.has(state.selectionAnchor)) state.selectionAnchor = null;
    const index = state.papers.findIndex((paper) => paper.id === data.paper.id);
    if (index >= 0) { state.papers[index] = data.paper; renderPaperList(); }
    renderPaper();
    if ($("viewerDialog").open) renderViewer();
    const busy = ACTIVE_STATUS.has(state.paper.status)
      || state.questions.some((q) => q.state === "waiting" || q.state === "reading");
    if (busy) state.pollTimer = setTimeout(refreshPaper, 2500);
  }

  function counts() {
    const qs = state.questions;
    return {
      all: qs.length,
      todo: qs.filter(needsCheck).length,
      green: qs.filter((q) => !isApproved(q) && !approvalNeedsReview(q) && !figureBlocksApproval(q) && q.state === "green").length,
      approved: qs.filter(isApproved).length,
      waiting: qs.filter((q) => q.state === "waiting" || q.state === "reading").length,
      red: qs.filter((q) => !isApproved(q) && q.state === "red").length,
      unpublished: qs.filter((q) => isApproved(q) && !(q.publication && q.publication.up_to_date)).length
    };
  }

  function renderMeter(c) {
    const meter = $("reviewMeter");
    meter.hidden = !c.all;
    if (!c.all) return;
    const yellow = c.todo - c.red;
    const segments = [
      ["approved", c.approved, "已标记通过", "var(--accent)"],
      ["green", c.green, "识读一致待审", "var(--green-bar)"],
      ["yellow", yellow, "需逐题核对", "var(--amber-bar)"],
      ["red", c.red, "识读失败", "var(--red)"],
      ["waiting", c.waiting, "识读中", "#c7cfc8"]
    ];
    const bar = $("meterBar");
    bar.replaceChildren(...segments.filter(([, n]) => n > 0).map(([key, n, label]) => {
      const part = el("span", `seg-${key}`);
      part.style.flexGrow = String(n);
      part.title = `${label} ${n}`;
      return part;
    }));
    bar.setAttribute("aria-label", segments.filter(([, n]) => n).map(([, n, label]) => `${label} ${n}`).join("，"));
    $("meterFigure").replaceChildren(el("strong", "", String(c.approved)), document.createTextNode(` / ${c.all} 题已标记通过`));
    $("meterPercent").textContent = `${Math.round((c.approved / c.all) * 100)}%`;
    $("meterLegend").replaceChildren(...segments.filter(([, n]) => n > 0).map(([, n, label, color]) => {
      const item = el("span");
      const dot = el("i");
      dot.style.background = color;
      item.append(dot, document.createTextNode(`${label} ${n}`));
      return item;
    }));
  }

  function renderDoneBanner(c) {
    const banner = $("doneBanner");
    const done = c.all > 0 && !c.todo && !c.green && !c.waiting && state.paper.status === "ready";
    banner.hidden = !done;
    if (!done) return;
    const text = el("span");
    text.append(icon("check"), document.createTextNode(c.unpublished
      ? `全部 ${c.all} 题已标记通过，还有 ${c.unpublished} 题没入库。`
      : `全部 ${c.all} 题已标记通过并入库。`));
    banner.replaceChildren(text);
    if (c.unpublished) banner.append(button(`入库（${c.unpublished} 题）`, "primary", publish, "", { iconName: "archive" }));
    else {
      const link = el("a", "button", "去正式题库看看");
      link.href = "/library";
      banner.append(link);
    }
  }

  function renderPaper() {
    const paper = state.paper;
    $("emptyState").hidden = true;
    $("paperView").hidden = false;
    $("paperName").textContent = paperDisplayName(paper);
    const c = counts();
    const statusText = $("paperStatus");
    const isProcessing = ACTIVE_STATUS.has(paper.status);
    const processing = isProcessing ? QBProgress.processingPresentation(paper) : null;
    if (ACTIVE_STATUS.has(paper.status)) {
      statusText.replaceChildren(
        el("strong", "processing-headline", processing.headline),
        el("span", "processing-detail", processing.detail)
      );
      if (processing.stale) statusText.append(el("span", "processing-stale", processing.stale));
    } else if (paper.status === "failed") {
      statusText.textContent = paper.recoverable_pause
        ? (paper.status_label || "额度不足，已暂停") : "处理失败";
    } else if (paper.status === "needs_grouping") {
      statusText.textContent = "检测到题号重新开始或页面可能来自不同资料；确认调整页序或拆分任务后才会继续识读。";
    } else if (!c.all) {
      statusText.textContent = "没有题卡";
    } else if (c.todo) {
      statusText.textContent = `${c.all} 道题：${c.todo} 张黄/红卡或内容变更需逐题核对，${c.green} 张仅为 AI 识读一致，已标记通过 ${c.approved}。`;
    } else if (c.green) {
      statusText.textContent = `${c.all} 道题：剩下 ${c.green} 张 AI 识读一致的绿卡；它们仍需按你的审核标准确认。`;
    } else {
      statusText.textContent = c.unpublished ? `全部 ${c.all} 题已标记通过，还有 ${c.unpublished} 题没入库。` : `全部 ${c.all} 题已标记通过并入库。`;
    }
    const processingPanel = $("processingPanel");
    processingPanel.hidden = !isProcessing;
    const progress = $("progress");
    if (isProcessing) {
      const stages = $("processingStages");
      stages.replaceChildren(...QBProgress.STAGES.map((stage, index) => {
        const item = el("span", "processing-stage", stage.label);
        if (index < processing.stageIndex) item.classList.add("done");
        if (index === processing.stageIndex) {
          item.classList.add("current");
          item.setAttribute("aria-current", "step");
        }
        return item;
      }));
      progress.hidden = !processing.determinate;
      if (processing.determinate) {
        const percent = Math.round(processing.ratio * 100);
        $("progressBar").style.width = `${percent}%`;
        progress.setAttribute("aria-valuenow", String(percent));
      } else {
        $("progressBar").style.width = "0%";
        progress.removeAttribute("aria-valuenow");
      }
    }
    const error = $("paperError");
    error.hidden = paper.status !== "failed";
    error.classList.toggle("paused", Boolean(paper.recoverable_pause));
    if (!error.hidden) {
      const actions = el("span", "error-actions");
      actions.append(button("重试", "small", () => retryPaper()));
      if (paper.kind === "pdf" && paper.material_type !== "book") {
        actions.append(button("按教材重试", "small", () => retryPaper("book")));
      }
      actions.append(button("删除任务", "small danger", deletePaper));
      error.replaceChildren(el("span", "", paper.error || "处理失败"), actions);
    }
    renderMeter(c);
    renderDoneBanner(c);
    const structureBlocked = paper.status === "needs_grouping";
    $("approveGreen").disabled = !c.green || structureBlocked;
    $("approveGreen").textContent = c.green ? `批量标记绿卡通过（${c.green}）` : "批量标记绿卡通过";
    $("approveGreen").title = "绿卡只表示 AI 识读一致。批量标记前，请确认这些题符合你的审核标准。";
    $("publishButton").disabled = !c.unpublished || structureBlocked;
    $("publishButton").textContent = c.unpublished ? `入库（${c.unpublished} 题）` : "入库";
    const notes = paper.notes || [];
    // 处理记录集中放在设置中；需要立即处理的失败和结构问题仍保留主界面提示。
    $("notesBox").hidden = true;
    $("notesList").replaceChildren(...notes.map((note) => el("li", "", note)));
    $("addQuestion").hidden = structureBlocked || (ACTIVE_STATUS.has(paper.status) && paper.status !== "reading");
    $("resegment").hidden = !["ready", "failed"].includes(paper.status);
    const canReorder = Boolean(paper.photos) && (paper.pages || []).length > 1 && ["ready", "failed", "needs_grouping"].includes(paper.status);
    $("pageOrder").hidden = !canReorder;
    syncTrashControls();
    $("toolsMenu").hidden = $("addQuestion").hidden && $("resegment").hidden && $("pageOrder").hidden && $("questionTrash").hidden;
    const check = $("orderCheck");
    const hasConflict = Boolean(paper.structure_conflict);
    check.hidden = !(hasConflict || (paper.photos && paper.photos.check));
    if (!check.hidden) {
      const message = paper.structure_message || (typeof paper.structure_conflict === "object" && paper.structure_conflict.message)
        || paper.photos?.check || "检测到题号重复或重新开始，请确认这些页面属于同一份资料还是多份资料。";
      const actions = el("span", "error-actions");
      const fix = button("调整页序", "small", openOrderDialog);
      fix.disabled = !canReorder;
      actions.append(fix);
      if (hasConflict) actions.append(button("确认是一份资料并继续", "small primary", confirmStructure));
      if (hasConflict && suggestedSplitGroups(paper).length > 1) actions.append(button("拆分任务", "small button-outline", openSplitDialog));
      check.replaceChildren(el("span", "", message), actions);
    }
    renderSettingsTask();
    renderFilters(c);
    renderCards();
  }

  function setFilter(key) {
    if (state.filter === key) return;
    clearQuestionSelection({ render: false });
    state.filter = key;
    renderPaper();
  }

  function renderFilters(c) {
    const box = $("filters");
    box.replaceChildren();
    FILTERS.forEach((filter, index) => {
      const active = state.filter === filter.key;
      const tab = el("button", `filter${active ? " active" : ""}${filter.key === "todo" && c.todo ? " attention" : ""}`);
      tab.type = "button";
      tab.setAttribute("role", "tab");
      tab.id = `filter-${filter.key}`;
      tab.dataset.filter = filter.key;
      tab.title = `${filter.label}（${index + 1}）`;
      tab.setAttribute("aria-selected", String(active));
      tab.setAttribute("aria-controls", "cards");
      tab.tabIndex = active ? 0 : -1;
      tab.append(el("span", "", filter.label), el("span", "filter-count", String(c[filter.key])));
      tab.addEventListener("click", () => setFilter(filter.key));
      tab.addEventListener("keydown", (event) => {
        if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
        event.preventDefault();
        event.stopPropagation();
        let next = index;
        if (event.key === "Home") next = 0;
        else if (event.key === "End") next = FILTERS.length - 1;
        else next = (index + (event.key === "ArrowRight" ? 1 : -1) + FILTERS.length) % FILTERS.length;
        setFilter(FILTERS[next].key);
        requestAnimationFrame(() => box.querySelector(`[data-filter="${state.filter}"]`)?.focus());
      });
      box.append(tab);
    });
    $("cards").setAttribute("aria-labelledby", `filter-${state.filter}`);
  }

  function visible(q) {
    if (state.filter === "todo") return needsCheck(q);
    if (state.filter === "green") return !isApproved(q) && !approvalNeedsReview(q) && !figureBlocksApproval(q) && q.state === "green";
    if (state.filter === "approved") return isApproved(q);
    return true;
  }

  function questionDeleteBlockReason(q) {
    if (!q) return "找不到这道题";
    if (state.paper?.status !== "ready") return "任务处理完成后才能删除题卡";
    if (q.state === "waiting" || q.state === "reading") return "这道题仍在识读，完成后才能移到回收站";
    if (q.publication) return "这道题已经入库，为保留来源和版本记录，不能从审题任务中删除";
    return "";
  }

  function selectableQuestionIds() {
    return state.questions.filter((question) => visible(question) && !questionDeleteBlockReason(question)).map((question) => question.id);
  }

  function renderSelectionState() {
    const visibleIds = new Set(state.questions.filter(visible).map((question) => question.id));
    state.selected = new Set([...state.selected].filter((id) => visibleIds.has(id) && !questionDeleteBlockReason(questionById(id))));
    if (state.selectionAnchor !== null && !visibleIds.has(state.selectionAnchor)) state.selectionAnchor = null;

    cardNodes().forEach((card) => {
      const id = Number(card.dataset.id);
      const selected = state.selected.has(id);
      card.classList.toggle("is-selected", selected);
      card.setAttribute("aria-selected", String(selected));
      const control = card.querySelector(".card-select");
      if (control) {
        control.setAttribute("aria-pressed", String(selected));
        control.setAttribute("aria-label", `${selected ? "取消选择" : "选择"}第 ${questionById(id)?.number ?? ""} 题`);
      }
    });

    const count = state.selected.size;
    const bar = $("selectionBar");
    bar.hidden = !count;
    $("selectionCount").textContent = `已选择 ${count} 道题`;
    $("selectionHint").textContent = state.selectionBusy
      ? "正在移到回收站，请稍候……"
      : "Ctrl/Cmd 点击增减单题，Shift 点击选择连续范围；删除后可以撤销。";
    $("selectionDelete").disabled = !count || state.selectionBusy;
    $("selectionDelete").textContent = state.selectionBusy ? "正在删除…" : `移到回收站（${count}）`;
    $("selectionCancel").disabled = state.selectionBusy;
  }

  function selectQuestion(q, event = {}) {
    const reason = questionDeleteBlockReason(q);
    if (reason) { toast(reason, "error"); return false; }
    if (state.selectionBusy) { toast("正在处理上一项删除操作，请稍候", "error"); return false; }
    const order = state.questions.filter(visible).map((question) => question.id);
    const result = QBSelection.updateSelection({
      order,
      eligible: selectableQuestionIds(),
      selected: [...state.selected],
      target: q.id,
      anchor: state.selectionAnchor,
      range: Boolean(event.shiftKey),
      additive: Boolean(event.ctrlKey || event.metaKey)
    });
    state.selected = new Set(result.selected);
    state.selectionAnchor = result.anchor;
    setCurrent(q.id);
    renderSelectionState();
    return true;
  }

  function cardSelectionControl(q) {
    const reason = questionDeleteBlockReason(q);
    const control = el("button", "card-select");
    control.type = "button";
    control.setAttribute("aria-pressed", String(state.selected.has(q.id)));
    control.setAttribute("aria-label", `${state.selected.has(q.id) ? "取消选择" : "选择"}第 ${q.number} 题`);
    control.title = reason || "选择这道题；也可以按住 Ctrl/Cmd 点击题卡，或用 Shift 连续选择";
    control.disabled = Boolean(reason);
    control.append(icon("check"));
    control.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      selectQuestion(q, event);
    });
    return control;
  }

  function handleCardSelectionClick(event, q) {
    if (!(event.ctrlKey || event.metaKey || event.shiftKey)) return false;
    if (event.target.closest?.("button, a, summary, input, textarea, select, [contenteditable='true'], .crop, .editor")) return false;
    event.preventDefault();
    event.stopImmediatePropagation();
    return selectQuestion(q, event);
  }

  function renderCards() {
    const container = $("cards");
    const shown = state.questions.filter(visible);
    const keep = new Set(shown.map((q) => q.id));
    [...container.children].forEach((child) => {
      const id = Number(child.dataset.id);
      if (!keep.has(id) && !state.editing.has(id)) { child.remove(); state.rendered.delete(id); }
    });
    let previous = null;
    shown.forEach((q) => {
      const signature = JSON.stringify([q, state.expanded.has(q.id), state.paper?.pages_version]);
      let card = container.querySelector(`[data-id="${q.id}"]`);
      if (!card || (state.rendered.get(q.id) !== signature && !state.editing.has(q.id))) {
        const fresh = renderCard(q);
        if (card) card.replaceWith(fresh); else container.append(fresh);
        card = fresh;
        state.rendered.set(q.id, signature);
      }
      card.classList.toggle("is-current", q.id === state.current);
      if (previous && previous.nextSibling !== card) previous.after(card);
      else if (!previous && container.firstChild !== card) container.prepend(card);
      previous = card;
    });
    if (!shown.length) {
      container.querySelectorAll(".cards-empty").forEach((node) => node.remove());
      const empty = el("div", "cards-empty");
      if (!state.questions.length) empty.append(el("strong", "", "题卡还没生成"), el("span", "", "AI 处理完会自动出现在这里。"));
      else if (state.filter === "todo") empty.append(el("strong", "", "没有需要逐题核对的卡"), el("span", "", "黄卡、红卡和内容变更都处理完了。"));
      else if (state.filter === "green") empty.append(el("strong", "", "没有待审的绿卡"), el("span", "", "识读一致的题都已标记通过。"));
      else empty.append(el("strong", "", "这一栏没有题卡"));
      container.append(empty);
    } else container.querySelectorAll(".cards-empty").forEach((node) => node.remove());
    renderSelectionState();
  }

  // ---------------------------------------------------------------- 当前题卡与键盘

  function cardNodes() {
    return [...$("cards").querySelectorAll(".card")];
  }

  function questionById(id) {
    return state.questions.find((q) => q.id === id) || null;
  }

  function setCurrent(id, { scroll = false, focus = false } = {}) {
    state.current = id;
    cardNodes().forEach((card) => card.classList.toggle("is-current", Number(card.dataset.id) === id));
    const card = id !== null ? document.querySelector(`.card[data-id="${id}"]`) : null;
    if (card && scroll) card.scrollIntoView({ behavior: "smooth", block: "start" });
    if (card && focus) card.focus({ preventScroll: true });
  }

  // 键盘移动：当前卡还在屏幕里就从它往前/往后走一张；
  // 已经滚走了，就先落到屏幕里最上面那张。
  function moveCurrent(step) {
    const cards = cardNodes();
    if (!cards.length) return;
    const top = (document.querySelector(".topbar")?.offsetHeight || 56) + ($("toolbar")?.offsetHeight || 0);
    const index = cards.findIndex((card) => Number(card.dataset.id) === state.current);
    let next;
    if (index >= 0) {
      const rect = cards[index].getBoundingClientRect();
      const onScreen = rect.bottom > top && rect.top < window.innerHeight;
      next = onScreen ? index + step : -1;
    } else next = -1;
    if (next === -1 && !(index >= 0 && index + step === -1)) {
      next = cards.findIndex((card) => card.getBoundingClientRect().bottom > top + 24);
      if (next < 0) next = cards.length - 1;
    }
    next = Math.min(cards.length - 1, Math.max(0, next));
    setCurrent(Number(cards[next].dataset.id), { scroll: true, focus: true });
  }

  function nextToReview(fromQuestion, { onlyCheck = false } = {}) {
    const candidates = state.questions.filter((q) => visible(q) && !isApproved(q) && (!onlyCheck || needsCheck(q)));
    if (!candidates.length) return null;
    const after = fromQuestion ? candidates.find((q) => q.number > fromQuestion.number) : null;
    return after || candidates[0];
  }

  function goTo(q) {
    if (!q) return;
    if (!visible(q)) setFilter("all");
    requestAnimationFrame(() => setCurrent(q.id, { scroll: true, focus: true }));
  }

  document.addEventListener("keydown", (event) => {
    if (event.defaultPrevented || event.ctrlKey || event.metaKey || event.altKey) return;
    const target = event.target;
    if (target.closest?.("input, textarea, select, [contenteditable='true'], .editor")) return;
    if ($("viewerDialog").open) { viewerKey(event); return; }
    if (anyDialogOpen()) return;
    if ($("paperView").hidden) {
      if (event.key === "?") { event.preventDefault(); $("keysDialog").showModal(); }
      return;
    }
    const onControl = target.closest?.("button, a, summary");
    const q = questionById(state.current);
    const key = event.key.length === 1 ? event.key.toLowerCase() : event.key;
    switch (key) {
      case "j": event.preventDefault(); moveCurrent(1); break;
      case "k": event.preventDefault(); moveCurrent(-1); break;
      case "Enter":
        if (onControl || !q) return;
        event.preventDefault();
        if (isApproved(q)) toast(`第 ${q.number} 题已经是通过状态；按 U 可撤销`);
        else if (canApprove(q)) approveQuestion(q, true);
        else if (figureBlocksApproval(q)) focusFigureReview(q);
        else toast(q.state === "red" ? "识读失败的题需先改字或重读，不能直接通过" : "请等待识读完成", "error");
        break;
      case " ":
        if (onControl) return;
        if (!q) return;
        event.preventDefault(); openViewer(q); break;
      case "u":
        if (q && isApproved(q)) { event.preventDefault(); approveQuestion(q, false); }
        break;
      case "e":
        if (q) {
          event.preventDefault();
          const card = document.querySelector(`.card[data-id="${q.id}"]`);
          if (card?.classList.contains("compact")) { state.expanded.add(q.id); renderCards(); }
          const fresh = document.querySelector(`.card[data-id="${q.id}"]`);
          if (fresh) openEditor(fresh, q);
        }
        break;
      case "r": if (q) { event.preventDefault(); openPageDialog("regions", q); } break;
      case "f": if (q) { event.preventDefault(); openPageDialog("figures", q); } break;
      case "n": {
        event.preventDefault();
        const next = nextToReview(q, { onlyCheck: true }) || nextToReview(q);
        if (next) goTo(next); else toast("没有待审的题卡了");
        break;
      }
      case "l": event.preventDefault(); setLens(!state.lens); toast(state.lens ? "放大镜已打开" : "放大镜已关闭"); break;
      case "1": case "2": case "3": case "4":
        event.preventDefault(); setFilter(FILTERS[Number(key) - 1].key); break;
      case "Delete":
        if (onControl || !state.selected.size) return;
        event.preventDefault(); deleteSelectedQuestions(); break;
      case "?": event.preventDefault(); $("keysDialog").showModal(); break;
      default: break;
    }
  });

  // ---------------------------------------------------------------- 原卷截图

  function cropView(regions, { figures = [], onZoom } = {}) {
    const wrap = el("div", "crop");
    if (!regions.length) {
      wrap.append(el("p", "crop-missing", "这道题还没有原卷范围。点右边的“调整范围”，在原卷上把它框出来，AI 会自动读题。"));
      return wrap;
    }
    const widest = Math.max(...regions.map((r) => r.bbox[2] - r.bbox[0]));
    regions.forEach((region, index) => {
      const [x0, y0, x1, y1] = region.bbox;
      const rw = x1 - x0;
      const rh = y1 - y0;
      const page = pageInfo(region.page_idx);
      const segment = el("div", "crop-seg");
      segment.style.width = `${(rw / widest) * 100}%`;
      segment.style.aspectRatio = `${rw * page.width} / ${rh * page.height}`;
      const image = el("img");
      image.alt = `原卷第 ${region.page_idx + 1} 页局部`;
      image.loading = "lazy";
      image.decoding = "async";
      // 绝对定位图片在尚未下载时没有自然高度，Chromium 会把它视为
      // 0 高度的懒加载目标而永远不发请求。提供原页固有尺寸，让布局在
      // 图片下载前就可计算，同时保留懒加载性能。
      image.width = Math.max(1, Math.round(page.width));
      image.height = Math.max(1, Math.round(page.height));
      image.src = previewUrl(state.paperId, region.page_idx);
      image.style.width = `${(1000 / rw) * 100}%`;
      image.style.left = `${(-x0 / rw) * 100}%`;
      image.style.top = `${(-y0 / rh) * 100}%`;
      segment.append(image);
      figures.filter((figure) => figure.page_idx === region.page_idx).forEach((figure) => {
        const [fx0, fy0, fx1, fy1] = figure.bbox;
        if (fx1 < x0 || fx0 > x1 || fy1 < y0 || fy0 > y1) return;
        const box = el("span", "crop-figure");
        box.style.left = `${((fx0 - x0) / rw) * 100}%`;
        box.style.top = `${((fy0 - y0) / rh) * 100}%`;
        box.style.width = `${((fx1 - fx0) / rw) * 100}%`;
        box.style.height = `${((fy1 - fy0) / rh) * 100}%`;
        box.title = `配图（${SLOT_NAMES[figure.slot] || figure.slot}）`;
        segment.append(box);
      });
      if (index > 0) wrap.append(el("div", "crop-join", `接第 ${region.page_idx + 1} 页`));
      wrap.append(segment);
    });
    if (onZoom) {
      wrap.classList.add("zoomable");
      wrap.title = "点击放大对照（Space）";
      wrap.tabIndex = 0;
      wrap.setAttribute("role", "button");
      wrap.setAttribute("aria-label", "放大查看这道题的原卷截图");
      wrap.addEventListener("click", onZoom);
      wrap.addEventListener("keydown", (event) => {
        if (!['Enter', ' '].includes(event.key)) return;
        event.preventDefault();
        event.stopPropagation();
        onZoom();
      });
    }
    return wrap;
  }

  // ---------------------------------------------------------------- 放大镜

  const lens = $("lens");
  const LENS_ZOOM = 2.2;

  function hideLens() {
    lens.classList.remove("on");
    lens.setAttribute("aria-hidden", "true");
  }

  function setLens(on) {
    state.lens = on;
    writePref("qb-lens", on ? "1" : "0");
    $("lensToggle").setAttribute("aria-pressed", String(on));
    $("settingsLens").checked = on;
    if (!on) hideLens();
  }

  $("lensToggle").addEventListener("click", () => setLens(!state.lens));

  document.addEventListener("pointermove", (event) => {
    // 放大对照已有 Ctrl+滚轮缩放；放大镜只服务普通题卡，避免两套方式叠加。
    if (!state.lens || event.pointerType !== "mouse" || $("viewerDialog").open) { hideLens(); return; }
    const segment = event.target.closest?.("#cards .crop-seg");
    const image = segment?.querySelector("img");
    if (!segment || !image || !image.complete || !image.naturalWidth) { hideLens(); return; }
    const rect = image.getBoundingClientRect();
    const relX = event.clientX - rect.left;
    const relY = event.clientY - rect.top;
    const width = lens.offsetWidth;
    const height = lens.offsetHeight;
    lens.style.backgroundImage = `url("${image.currentSrc || image.src}")`;
    lens.style.backgroundSize = `${rect.width * LENS_ZOOM}px ${rect.height * LENS_ZOOM}px`;
    lens.style.backgroundPosition = `${width / 2 - relX * LENS_ZOOM}px ${height / 2 - relY * LENS_ZOOM}px`;
    // 放大镜放在鼠标上方，不挡住正在看的那一行；靠近顶部时放到下方。
    let left = event.clientX - width / 2;
    let top = event.clientY - height - 22;
    if (top < 64) top = event.clientY + 24;
    left = Math.max(8, Math.min(window.innerWidth - width - 8, left));
    top = Math.max(8, Math.min(window.innerHeight - height - 8, top));
    lens.style.left = `${left}px`;
    lens.style.top = `${top}px`;
    lens.classList.add("on");
    lens.setAttribute("aria-hidden", "false");
  }, { passive: true });
  document.documentElement.addEventListener("mouseleave", hideLens);
  document.addEventListener("pointerdown", hideLens);
  window.addEventListener("scroll", hideLens, { passive: true });

  // ---------------------------------------------------------------- 原卷对照（大图）

  const viewer = { id: null, zoom: 1, mode: "fit", fitFrame: 0 };

  function viewerList() {
    return state.questions.filter(visible);
  }

  function openViewer(q) {
    viewer.id = q.id;
    viewer.zoom = 1;
    viewer.mode = "fit";
    setCurrent(q.id);
    hideLens();
    renderViewer();
    if (!$("viewerDialog").open) $("viewerDialog").showModal();
    $("viewerSource").scrollTo({ top: 0, left: 0 });
    $("viewerText").scrollTo({ top: 0, left: 0 });
    requestViewerFit();
    // 焦点放在原卷区域：Enter / 方向键交给对照窗口处理，而不是误按到某个按钮。
    $("viewerSource").focus({ preventScroll: true });
  }

  function syncViewerZoom() {
    $("viewerSource").classList.toggle("fit-mode", viewer.mode === "fit");
    $("zoomFit").setAttribute("aria-pressed", String(viewer.mode === "fit"));
    $("zoomWidth").setAttribute("aria-pressed", String(viewer.mode === "width"));
    $("zoomLevel").textContent = `${Math.round(viewer.zoom * 100)}%`;
  }

  function applyZoom() {
    const crop = $("viewerCrop");
    crop.style.width = `${viewer.zoom * 100}%`;
    syncViewerZoom();
  }

  function requestViewerFit() {
    viewer.mode = "fit";
    syncViewerZoom();
    if (viewer.fitFrame) cancelAnimationFrame(viewer.fitFrame);
    viewer.fitFrame = requestAnimationFrame(() => {
      viewer.fitFrame = requestAnimationFrame(() => {
        viewer.fitFrame = 0;
        fitViewer();
      });
    });
  }

  function fitViewer() {
    const dialog = $("viewerDialog");
    const q = questionById(viewer.id);
    if (!dialog.open || viewer.mode !== "fit" || !q?.regions?.length) return;
    const source = $("viewerSource");
    const crop = $("viewerCrop");
    const style = getComputedStyle(source);
    const width = source.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
    const height = source.clientHeight - parseFloat(style.paddingTop) - parseFloat(style.paddingBottom);
    if (!(width > 0) || !(height > 0)) return;
    crop.classList.add("measuring");
    viewer.zoom = R.fitScale((scale) => {
      crop.style.width = `${scale * 100}%`;
      return { width: crop.offsetWidth, height: crop.offsetHeight };
    }, width, height, { min: 0.02, max: 1, steps: 12 });
    crop.style.width = `${viewer.zoom * 100}%`;
    crop.classList.remove("measuring");
    syncViewerZoom();
    source.scrollTo({ top: 0, left: 0 });
  }

  function renderViewer() {
    const q = questionById(viewer.id);
    if (!q) { $("viewerDialog").close(); return; }
    const list = viewerList();
    const index = list.findIndex((item) => item.id === q.id);
    $("viewerTitle").textContent = `第 ${q.number} 题 · 原卷对照`;
    $("viewerChip").replaceChildren(stateChip(q));
    const crop = $("viewerCrop");
    const regions = q.regions || [];
    if (!regions.length && viewer.mode === "fit") viewer.zoom = 1;
    crop.replaceChildren(cropView(regions, { figures: q.figures || [] }));
    // 又宽又矮的截图（一两行字的题）改成上下排：原卷能占满整个窗口宽度。
    $("viewerSource").parentElement.classList.toggle("stacked", cropAspect(regions) > 2.5 && window.innerWidth > 1100);
    applyZoom();
    const text = $("viewerText");
    text.replaceChildren();
    const figurePanel = figureReviewPanel(q);
    if (figurePanel) text.append(figurePanel);
    const flags = flagsNode(q);
    if (flags) text.append(flags);
    const disagreement = disagreementPanel(q);
    if (disagreement) text.append(disagreement);
    if (q.stem) {
      const body = el("div");
      R.renderQuestion(body, content(q), { showNumber: false, marks: diffMarks(q), showAnswer: "collapsed" });
      text.append(body);
    } else text.append(el("p", "hint", q.state === "waiting" || q.state === "reading" ? "AI 正在读这道题……" : "还没有题面"));
    [$("zoomFit"), $("zoomWidth"), $("zoomOut"), $("zoomIn")].forEach((button) => { button.disabled = !regions.length; });
    $("viewerPrev").disabled = index <= 0;
    $("viewerNext").disabled = index < 0 || index >= list.length - 1;
    const approve = $("viewerApprove");
    if (isApproved(q)) {
      approve.replaceChildren(document.createTextNode("撤销通过"));
      approve.className = "button";
      approve.disabled = false;
    } else {
      const blocked = figureBlocksApproval(q);
      const blockedLabel = figureReview(q)?.status === "conflict" ? "处理配图冲突" : "处理漏图提醒";
      approve.replaceChildren(icon(blocked ? "image" : "check"), document.createTextNode(blocked ? blockedLabel
        : approvalNeedsReview(q) ? "重新标记通过" : "通过并下一题"), el("span", "kbd-hint", "Enter"));
      approve.className = "button primary";
      approve.disabled = blocked ? false : !canApprove(q);
      approve.title = blocked ? "前往黄色区域，选择保留、调整或移除配图"
        : canApprove(q) ? "对照原卷确认无误后通过，并跳到下一题" : "请等待识读完成并确认题面";
    }
    if (viewer.mode === "fit" && $("viewerDialog").open) requestViewerFit();
  }

  function cropAspect(regions) {
    if (!regions.length) return 1;
    const widest = Math.max(...regions.map((r) => r.bbox[2] - r.bbox[0]));
    const height = regions.reduce((sum, r) => {
      const page = pageInfo(r.page_idx);
      return sum + ((r.bbox[3] - r.bbox[1]) * page.height) / (widest * page.width);
    }, 0);
    return height ? 1 / height : 1;
  }

  function viewerStep(step) {
    const list = viewerList();
    const index = list.findIndex((item) => item.id === viewer.id);
    const next = list[index + step];
    if (!next) return;
    viewer.id = next.id;
    viewer.zoom = 1;
    viewer.mode = "fit";
    setCurrent(next.id);
    hideLens();
    renderViewer();
    $("viewerSource").scrollTo({ top: 0, left: 0 });
    $("viewerText").scrollTo({ top: 0, left: 0 });
  }

  async function viewerApprove() {
    const q = questionById(viewer.id);
    if (!q) return;
    if (isApproved(q)) { await approveQuestion(q, false, { advance: false }); return; }
    if (figureBlocksApproval(q)) { focusFigureReview(q); return; }
    if (!canApprove(q)) {
      toast("这道题还不能通过", "error");
      return;
    }
    const before = viewerList().map((item) => item.id);
    const ok = await approveQuestion(q, true, { advance: false });
    if (!ok) return;
    // 通过后跳到下一张没通过的卡；没有了就停在这张并提示。
    const position = before.indexOf(q.id);
    const pending = state.questions.filter((item) => visible(item) && !isApproved(item));
    const next = pending.find((item) => before.indexOf(item.id) > position) || pending[0];
    if (next) { viewer.id = next.id; viewer.zoom = 1; viewer.mode = "fit"; setCurrent(next.id); }
    hideLens();
    renderViewer();
    $("viewerSource").scrollTo({ top: 0, left: 0 });
    $("viewerText").scrollTo({ top: 0, left: 0 });
  }

  function viewerKey(event) {
    const key = event.key;
    if (["ArrowLeft", "k", "K"].includes(key)) { event.preventDefault(); viewerStep(-1); }
    else if (["ArrowRight", "j", "J"].includes(key)) { event.preventDefault(); viewerStep(1); }
    else if (key === "Enter") { event.preventDefault(); viewerApprove(); }
    else if (key === " ") { event.preventDefault(); $("viewerDialog").close(); }
    else if (key === "+" || key === "=") { event.preventDefault(); zoomBy(1.25); }
    else if (key === "-" || key === "_") { event.preventDefault(); zoomBy(0.8); }
    else if (key === "0") { event.preventDefault(); requestViewerFit(); }
    else if (key.toLowerCase() === "w") { event.preventDefault(); setViewerWidth(); }
    else if (key.toLowerCase() === "e") {
      event.preventDefault();
      const q = questionById(viewer.id);
      $("viewerDialog").close();
      if (q) {
        const card = document.querySelector(`.card[data-id="${q.id}"]`);
        if (card?.classList.contains("compact")) { state.expanded.add(q.id); renderCards(); }
        const fresh = document.querySelector(`.card[data-id="${q.id}"]`);
        if (fresh) { fresh.scrollIntoView({ block: "start" }); openEditor(fresh, q); }
      }
    }
  }

  function zoomBy(factor) {
    viewer.mode = "manual";
    viewer.zoom = Math.min(4, Math.max(0.08, Math.round(viewer.zoom * factor * 100) / 100));
    applyZoom();
  }

  function setViewerWidth() {
    viewer.mode = "width";
    viewer.zoom = 1;
    applyZoom();
    $("viewerSource").scrollTo({ top: 0, left: 0 });
  }

  $("zoomIn").addEventListener("click", () => zoomBy(1.25));
  $("zoomOut").addEventListener("click", () => zoomBy(0.8));
  $("zoomFit").addEventListener("click", requestViewerFit);
  $("zoomWidth").addEventListener("click", setViewerWidth);
  $("viewerPrev").addEventListener("click", () => viewerStep(-1));
  $("viewerNext").addEventListener("click", () => viewerStep(1));
  $("viewerApprove").addEventListener("click", viewerApprove);
  $("viewerSource").addEventListener("wheel", (event) => {
    if (!event.ctrlKey) return;
    event.preventDefault();
    zoomBy(event.deltaY < 0 ? 1.1 : 0.9);
  }, { passive: false });
  // 按住拖动平移放大后的原卷
  $("viewerSource").addEventListener("pointerdown", (event) => {
    if (viewer.mode === "fit" || event.button !== 0 || event.pointerType !== "mouse") return;
    const source = $("viewerSource");
    const start = { x: event.clientX, y: event.clientY, left: source.scrollLeft, top: source.scrollTop };
    source.classList.add("panning");
    const move = (moveEvent) => {
      source.scrollLeft = start.left - (moveEvent.clientX - start.x);
      source.scrollTop = start.top - (moveEvent.clientY - start.y);
    };
    const up = () => {
      source.classList.remove("panning");
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  });
  // 题面较长时，也可以像拖纸张一样按住鼠标左键上下移动。
  // Alt+拖动仍交给浏览器，方便需要时选择文字；可点击控件保持原有行为。
  $("viewerText").addEventListener("pointerdown", (event) => {
    if (event.button !== 0 || event.pointerType !== "mouse" || event.altKey) return;
    if (event.target.closest?.("a, button, input, textarea, select, summary, label, [contenteditable='true']")) return;
    const text = $("viewerText");
    if (text.scrollHeight <= text.clientHeight + 1 && text.scrollWidth <= text.clientWidth + 1) return;
    const rect = text.getBoundingClientRect();
    const scrollbarWidth = text.offsetWidth - text.clientWidth;
    const scrollbarHeight = text.offsetHeight - text.clientHeight;
    if (scrollbarWidth > 0 && event.clientX >= rect.right - scrollbarWidth) return;
    if (scrollbarHeight > 0 && event.clientY >= rect.bottom - scrollbarHeight) return;
    const start = { x: event.clientX, y: event.clientY, left: text.scrollLeft, top: text.scrollTop };
    let dragging = false;
    const move = (moveEvent) => {
      const dx = moveEvent.clientX - start.x;
      const dy = moveEvent.clientY - start.y;
      if (!dragging && Math.hypot(dx, dy) < 5) return;
      if (!dragging) {
        dragging = true;
        text.classList.add("panning");
        window.getSelection()?.removeAllRanges();
      }
      moveEvent.preventDefault();
      text.scrollLeft = start.left - dx;
      text.scrollTop = start.top - dy;
    };
    const stop = () => {
      text.classList.remove("panning");
      text.removeEventListener("pointermove", move);
      text.removeEventListener("pointerup", stop);
      text.removeEventListener("pointercancel", stop);
      text.removeEventListener("lostpointercapture", stop);
      if (text.hasPointerCapture?.(event.pointerId)) text.releasePointerCapture(event.pointerId);
    };
    text.addEventListener("pointermove", move);
    text.addEventListener("pointerup", stop);
    text.addEventListener("pointercancel", stop);
    text.addEventListener("lostpointercapture", stop);
    try { text.setPointerCapture(event.pointerId); } catch { /* 浏览器不支持时仍可在区域内拖动 */ }
  });
  $("viewerText").addEventListener("dragstart", (event) => {
    if (event.target.closest?.("img")) event.preventDefault();
  });
  // 点过窗口里的按钮后把焦点还给原卷区域，之后按 Enter 仍然是“通过”。
  $("viewerDialog").addEventListener("click", (event) => {
    const clicked = event.target.closest("button");
    if (clicked && !clicked.hasAttribute("data-close")) $("viewerSource").focus({ preventScroll: true });
  });
  $("viewerDialog").addEventListener("close", () => {
    if (viewer.fitFrame) cancelAnimationFrame(viewer.fitFrame);
    viewer.fitFrame = 0;
    const card = document.querySelector(`.card[data-id="${state.current}"]`);
    if (card) { card.scrollIntoView({ block: "nearest" }); card.focus({ preventScroll: true }); }
  });

  function syncViewerLayout() {
    const q = questionById(viewer.id);
    const regions = q?.regions || [];
    $("viewerSource").parentElement.classList.toggle("stacked", cropAspect(regions) > 2.5 && window.innerWidth > 1100);
  }

  window.addEventListener("resize", () => {
    hideLens();
    if (!$("viewerDialog").open) return;
    syncViewerLayout();
    if (viewer.mode === "fit") requestViewerFit();
  });

  if (window.ResizeObserver) {
    new ResizeObserver(() => {
      if ($("viewerDialog").open && viewer.mode === "fit") requestViewerFit();
    }).observe($("viewerSource"));
  }

  // ---------------------------------------------------------------- 题卡

  function unclassifiedCandidates(q) {
    const review = figureReview(q) || {};
    const candidates = q.figure_candidates || [];
    const explicit = Array.isArray(review.unclassified_candidates) ? new Set(
      review.unclassified_candidates.map((item) => typeof item === "string" ? item : item?.key).filter(Boolean)
    ) : null;
    if (explicit) return candidates.filter((candidate) => explicit.has(figureCandidateKey(candidate)));
    const selected = new Set((q.figures || []).map((figure) => {
      if (hasFigureCandidateKey(q, figure.candidate_key)) return figure.candidate_key;
      const key = figureCandidateKey(figure);
      return hasFigureCandidateKey(q, key) ? key : null;
    }).filter(Boolean));
    const ignored = new Set(Array.isArray(review.ignored_candidates) ? review.ignored_candidates : []);
    return candidates.filter((candidate) => {
      const key = figureCandidateKey(candidate);
      return !selected.has(key) && !ignored.has(key);
    });
  }

  function candidatePageLabel(candidates) {
    const pages = [...new Set(candidates.map((candidate) => Number(candidate.page_idx) + 1))].sort((a, b) => a - b);
    if (!pages.length) return "";
    const shown = pages.slice(0, 6).join("、");
    return `（第 ${shown}${pages.length > 6 ? ` 等 ${pages.length}` : ""} 页）`;
  }

  function figureReviewCopy(review, q) {
    const count = Number(review.excluded_count) || 0;
    if (review.status === "blocked_missing") return {
      title: "可能漏图，暂时不能通过",
      text: review.reason || "题目文字或现有识读结果表明这里应当有图，但当前没有配图。"
    };
    if (review.status === "conflict") return {
      title: "配图判断有冲突，暂时不能通过",
      text: (review.signals || []).includes("candidate_unclassified")
        ? `当前已选配图不一定有错；另有 ${unclassifiedCandidates(q).length || Number(review.unclassified_count) || 1} 张候选图尚未归类${candidatePageLabel(unclassifiedCandidates(q))}。请检查它们、修正过长的题目范围，或明确确认其余候选均与本题无关。`
        : (review.reason || "程序无法确定候选内容是正式配图还是手写痕迹，请对照原卷确认。")
    };
    if (review.status === "auto_excluded") return {
      title: "已自动排除疑似多余图",
      text: review.reason || (count ? `已排除 ${count} 张疑似手写、草图或批注，不需要逐张检查。` : "疑似手写、草图或批注已从本题配图中排除。")
    };
    if (review.status === "confirmed_no_figure") return {
      title: "已人工确认本题无图",
      text: review.reason || "漏图提醒已解除；这项人工判断会随题卡保留。"
    };
    if (review.status === "ok") return null;
    return review.reason ? { title: "配图检查说明", text: review.reason } : null;
  }

  function openFigureEditor(q) {
    if ($("viewerDialog").open) $("viewerDialog").close();
    setCurrent(q.id);
    const current = questionById(q.id) || q;
    const firstCandidate = unclassifiedCandidates(current)[0];
    openPageDialog("figures", current, { page: firstCandidate?.page_idx ?? null });
  }

  function adjustQuestionRegions(q) {
    if ($("viewerDialog").open) $("viewerDialog").close();
    setCurrent(q.id);
    openPageDialog("regions", questionById(q.id) || q);
  }

  function focusFigureReview(q) {
    const panel = $("viewerDialog").open
      ? $("viewerText").querySelector(".figure-review")
      : document.querySelector(`.card[data-id="${q.id}"] .figure-review`);
    if (!panel) { openFigureEditor(q); return; }
    panel.scrollIntoView({ block: "center", behavior: "smooth" });
    panel.focus({ preventScroll: true });
    panel.classList.remove("attention");
    requestAnimationFrame(() => panel.classList.add("attention"));
    window.setTimeout(() => panel.classList.remove("attention"), 900);
    toast("请在黄色区域选择一种处理方式");
  }

  function confirmedFigurePayload(q) {
    const figures = (q.figures || []).map((figure) => ({
      page_idx: figure.page_idx, bbox: [...figure.bbox], slot: figure.slot || "stem",
      ...(figure.label_offset ? { label_offset: { ...figure.label_offset } } : {}),
      ...(() => {
        if (hasFigureCandidateKey(q, figure.candidate_key)) return { candidate_key: figure.candidate_key };
        const exact = (q.figure_candidates || []).find((candidate) => figureCandidateKey(candidate) === figureCandidateKey(figure));
        return exact ? { candidate_key: figureCandidateKey(exact) } : {};
      })()
    }));
    return figures;
  }

  async function confirmCurrentFigures(q, { ignoreRemaining = false } = {}) {
    const figures = confirmedFigurePayload(q);
    if (!figures.length) {
      toast("当前还没有已选配图，请先从原卷中选择图片", "error");
      openFigureEditor(q);
      return false;
    }
    const unresolved = unclassifiedCandidates(q);
    if (unresolved.length && !ignoreRemaining) {
      toast(`还有 ${unresolved.length} 张候选图未处理，请逐张检查或明确其余均无关`, "error");
      openFigureEditor(q);
      return false;
    }
    if (ignoreRemaining) {
      const ok = await confirmDialog({
        title: `确认其余 ${unresolved.length} 张候选图均与本题无关？`,
        text: `将保留当前配图及其“题干/选项”归属，并把其余候选标记为无关${candidatePageLabel(unresolved)}。如果题目范围切到了后面的内容，建议取消并先点“调整题目范围”。`,
        ok: "确认当前配图"
      });
      if (!ok) return false;
    }
    try {
      const ignoredCandidates = new Set(Array.isArray(q.figure_review?.ignored_candidates)
        ? q.figure_review.ignored_candidates.filter((key) => hasFigureCandidateKey(q, key)) : []);
      if (ignoreRemaining) unresolved.forEach((candidate) => ignoredCandidates.add(figureCandidateKey(candidate)));
      const data = await api(`/api/questions/${q.id}/figures`, {
        method: "POST", body: { figures, ignored_candidates: [...ignoredCandidates] }
      });
      applyQuestion(data);
      if ($("viewerDialog").open) renderViewer();
      toast(`已确认第 ${q.number} 题的当前配图及归属；请再次核对并标记通过`, "success");
      return true;
    } catch (error) {
      toast(error.message, "error");
      return false;
    }
  }

  async function confirmNoFigure(q) {
    const ok = await confirmDialog({
      title: `确认第 ${q.number} 题确实无图？`,
      text: "请先对照左侧原卷。确认后不会重新调用 AI，也不会改动原卷；系统只会记录这次人工判断并解除漏图阻止。如果原卷确实有图，请取消并点“配图”。",
      ok: "确认无图"
    });
    if (!ok) return false;
    try {
      const data = await api(`/api/questions/${q.id}/figure-review`, { method: "POST", body: { decision: "confirm_no_figure" } });
      applyQuestion(data);
      if ($("viewerDialog").open) renderViewer();
      toast(`已确认第 ${q.number} 题无图；现在可以继续审核`, "success");
      return true;
    } catch (error) {
      toast(error.message, "error");
      return false;
    }
  }

  async function resetNoFigure(q) {
    try {
      const data = await api(`/api/questions/${q.id}/figure-review`, { method: "POST", body: { decision: "reset" } });
      applyQuestion(data);
      if ($("viewerDialog").open) renderViewer();
      toast(`已撤销第 ${q.number} 题的无图确认，请重新核对配图`);
      return true;
    } catch (error) {
      toast(error.message, "error");
      return false;
    }
  }

  function figureReviewPanel(q) {
    const review = figureReview(q);
    const copy = review && figureReviewCopy(review, q);
    if (!copy) return null;
    const panel = el("section", `figure-review figure-review-${review.status}`);
    panel.setAttribute("aria-label", copy.title);
    panel.dataset.questionId = String(q.id);
    panel.tabIndex = -1;
    const heading = el("strong", "figure-review-title");
    heading.append(icon(FIGURE_REVIEW_BLOCKS.has(review.status) ? "alert" : "check"), document.createTextNode(copy.title));
    panel.append(heading, el("p", "figure-review-copy", copy.text));
    if (FIGURE_REVIEW_BLOCKS.has(review.status)) {
      const actions = el("div", "figure-review-actions");
      const signals = new Set(review.signals || []);
      if (review.status === "conflict" && signals.has("candidate_unclassified")) {
        const unresolved = unclassifiedCandidates(q);
        const count = unresolved.length || Number(review.unclassified_count) || 1;
        actions.append(
          button(`检查 ${count} 张候选图`, "small primary", () => openFigureEditor(q), "逐张确认候选图属于题干、某个选项或与本题无关", { iconName: "image" }),
          button("题目范围切多了 · 调整范围", "small", () => adjustQuestionRegions(q), "如果候选图来自后面的例题或下一题，先缩短本题原卷范围"),
        );
        if ((q.figures || []).length) actions.append(button(`当前配图正确，其余 ${count} 张无关`, "small", () => confirmCurrentFigures(q, { ignoreRemaining: true }), "保留当前归属，并明确把所有剩余候选标记为无关"));
      } else if (review.status === "blocked_missing") {
        actions.append(
          button("补选配图", "small primary", () => openFigureEditor(q), "从原卷中补选缺少的图片", { iconName: "image" }),
          button("调整题目范围", "small", () => adjustQuestionRegions(q), "题目范围不完整或切入别题时先调整范围"),
          button("原卷确实无图", "small", () => confirmNoFigure(q), "仅在对照原卷后确认本题确实没有正式配图时使用")
        );
      } else {
        if ((q.figures || []).length) actions.append(button("确认当前配图及归属", "small primary", () => confirmCurrentFigures(q), "保留每张图现有的题干或 A–D 归属", { iconName: "image" }));
        actions.append(
          button("调整配图或归属", "small", () => openFigureEditor(q), "补选、裁剪图片，或明确它属于题干还是某个选项"),
          button("这些图与本题无关", "small", () => confirmNoFigure(q), "移除当前配图，并记录原卷中本题没有正式配图")
        );
      }
      panel.append(actions);
    } else if (review.status === "confirmed_no_figure") {
      const actions = el("div", "figure-review-actions");
      actions.append(button("撤销无图确认", "small", () => resetNoFigure(q), "恢复确认前的自动配图并重新判断"));
      panel.append(actions);
    }
    return panel;
  }

  function questionFlags(q) {
    const review = figureReview(q);
    return (q.flags || []).filter((flag) => {
      if (!review) return true;
      if (flag === review.reason) return false;
      return !(FIGURE_REVIEW_BLOCKS.has(review.status) && /还没有配图|原卷可能有图没有被找到|选项是图/.test(String(flag)));
    });
  }

  function flagsNode(q) {
    const difference = reviewDiff(q);
    const flags = questionFlags(q).map((flag) => {
      if (!/两次识读不一致.*请看标黄/.test(String(flag))) return flag;
      if (difference.hasVisibleMarks) return "两次识读不一致，已由第三次识读裁决；请核对题面中标黄的位置";
      if (difference.observedOnly.length) return "两次识读不一致；当前稿没有可标黄的文字，另一读法多出的内容见下方";
      return "两次识读曾有出入；当前题面只剩排版或公式写法差异，可展开原始读法核对";
    });
    if (!flags.length && !q.error) return null;
    const list = el("ul", "flags");
    if (q.error) list.append(el("li", "", q.error));
    flags.forEach((flag) => list.append(el("li", "", flag)));
    return list;
  }

  function stateChip(q) {
    const review = figureReview(q);
    if (review?.status === "blocked_missing") return el("span", "chip yellow", "可能漏图 · 待处理");
    if (review?.status === "conflict") return el("span", "chip yellow", "配图冲突 · 待确认");
    if (approvalNeedsReview(q)) return el("span", "chip yellow", "内容已变 · 需重新审核");
    if (isApproved(q)) return el("span", "chip approved", "已标记通过");
    if (q.state === "green") {
      const copy = {
        majority: ["AI 三读多数一致 · 未人工审核", "前两次 AI 识读不同，第三次与其中一次相同；仍需人工对照原卷。"],
        human: ["已人工修改 · 未人工审核", "题面经过人工修改，但当前版本尚未标记通过。"],
        witness: ["两种引擎一致 · 未人工审核",
          "视觉模型的誊录与 MinerU 自己识别的文字逐字一致（两套独立引擎）；一致不等于正确，仍需人工对照原卷。"],
      }[q.text_source] || ["AI 两次一致 · 未人工审核", "两次独立 AI 识读相同；一致不等于正确，仍需人工对照原卷。"];
      const chip = el("span", "chip green", copy[0]);
      chip.title = copy[1];
      return chip;
    }
    if (q.state === "yellow") return el("span", "chip yellow", "需核对原卷");
    if (q.state === "red") return el("span", "chip red", "识读失败");
    return el("span", "chip waiting", q.state === "reading" ? "AI 读题中…" : "等待识读");
  }

  const reviewDiffCache = new WeakMap();

  function reviewDiff(q) {
    if (!q || typeof q !== "object") return QBReviewDiff.analyze(q, R);
    if (!reviewDiffCache.has(q)) reviewDiffCache.set(q, QBReviewDiff.analyze(q, R));
    return reviewDiffCache.get(q);
  }

  function diffMarks(q) {
    // 当前稿有实际字符时标黄；只存在于另一读法的文字由独立提示完整展示。
    return reviewDiff(q).marks;
  }

  function shortDifferenceText(value, limit = 180) {
    const compact = String(value || "").replace(/\s+/g, " ").trim();
    return compact.length > limit ? `${compact.slice(0, limit)}…` : compact;
  }

  function disagreementPanel(q) {
    const difference = reviewDiff(q);
    if (!difference.observedOnly.length) return null;
    const panel = el("section", "reading-difference");
    panel.append(el("strong", "reading-difference-title", "另一读法多出了以下内容，当前稿没有对应文字可标黄"));
    const list = el("ul", "reading-difference-list");
    difference.observedOnly.forEach((item) => {
      const row = el("li");
      row.append(el("span", "reading-difference-source", `${item.readerName} · ${item.fieldName}`),
        el("span", "reading-difference-text", shortDifferenceText(item.text)));
      list.append(row);
    });
    const show = button("查看三次原始读法", "small", () => toggleReadsNear(panel, q));
    panel.append(list, show);
    return panel;
  }

  function content(q) {
    return {
      number: q.number,
      question_type: q.question_type,
      stem: q.stem,
      options: CHOICE.has(q.question_type) || Object.keys(q.options || {}).length ? q.options : {},
      answer: q.answer,
      analysis: q.analysis,
      figures: q.figures
    };
  }

  // 已通过的卡：展开和收起是同一个按钮，固定在卡片右上角，两种状态下位置不变。
  function expandToggle(q, collapsed) {
    const toggle = button(collapsed ? "展开" : "收起", "small quiet card-toggle", (event) => {
      event.stopPropagation();
      if (collapsed) state.expanded.add(q.id); else state.expanded.delete(q.id);
      setCurrent(q.id);
      renderCards();
    });
    const chevron = icon("chevron");
    chevron.style.cssText = `width:14px;height:14px;${collapsed ? "" : "transform:rotate(180deg)"}`;
    toggle.append(chevron);
    toggle.setAttribute("aria-expanded", String(!collapsed));
    return toggle;
  }

  function renderCard(q) {
    const approved = isApproved(q);
    const approvedCompact = approved && !state.expanded.has(q.id);
    const displayState = approved ? "approved has-toggle" : (figureBlocksApproval(q) || approvalNeedsReview(q)) ? "yellow" : q.state;
    const card = el("article", `card state-${displayState}${approvedCompact ? " compact" : ""}`);
    card.dataset.id = q.id;
    card.id = `q-${q.id}`;
    card.tabIndex = -1;
    card.setAttribute("aria-label", `第 ${q.number} 题`);
    card.setAttribute("aria-selected", String(state.selected.has(q.id)));
    card.addEventListener("pointerdown", () => { if (state.current !== q.id) setCurrent(q.id); });
    card.addEventListener("click", (event) => handleCardSelectionClick(event, q));

    if (approvedCompact) {
      const row = el("div", "compact-row");
      row.append(cardSelectionControl(q), el("span", "qnum", `第 ${q.number} 题`), stateChip(q));
      const preview = el("span", "compact-text");
      R.renderTypeset(preview, firstLine(q.stem));
      row.append(preview);
      row.append(publicationChip(q));
      row.append(expandToggle(q, true));
      card.append(row);
      card.addEventListener("click", (event) => {
        if (event.ctrlKey || event.metaKey || event.shiftKey || event.target.closest("button")) return;
        state.expanded.add(q.id);
        renderCards();
      });
      return card;
    }

    const source = el("div", "card-source");
    const sticky = el("div", "source-sticky");
    sticky.append(cropView(q.regions, { figures: q.figures, onZoom: () => openViewer(q) }));
    const sourceNote = el("p", "source-note");
    sourceNote.append(icon("zoom"), document.createTextNode(q.regions_changed ? "原卷截图（范围已人工调整）· 点击放大对照"
      : q.start_source === "inferred" ? "题号由本地规则补出，请对照原卷核对 · 点击放大对照"
        : q.start_source === "located" ? "原卷截图（题号由 AI 在原卷上定位）· 点击放大对照" : "原卷截图 · 点击放大对照"));
    if (q.regions.length) sticky.append(sourceNote);
    source.append(sticky);

    const body = el("div", "card-body");
    const head = el("header", "card-head");
    head.append(cardSelectionControl(q));
    if (hasMultipleQuestionGroups() && q.group?.title) head.append(el("span", "group-label", q.group.title));
    head.append(el("span", "qnum", `第 ${q.number} 题`), el("span", "qtype", TYPE_NAMES[q.question_type] || q.question_type), stateChip(q));
    head.append(el("span", "head-spacer"), publicationChip(q));
    body.append(head);

    if (q.state === "waiting" || q.state === "reading") {
      const placeholder = el("div", "reading-placeholder");
      placeholder.append(el("p", "", q.state === "reading" ? "AI 正在读这道题……" : "排队等待 AI 识读……"),
        el("div", "skeleton w80"), el("div", "skeleton w60"), el("div", "skeleton w40"));
      body.append(placeholder);
      card.append(source, body);
      return card;
    }

    const figurePanel = figureReviewPanel(q);
    if (figurePanel) body.append(figurePanel);
    const flags = flagsNode(q);
    if (flags) body.append(flags);
    const disagreement = disagreementPanel(q);
    if (disagreement) body.append(disagreement);

    const rendered = el("div", "rendered");
    if (q.stem) R.renderQuestion(rendered, content(q), { showNumber: false, marks: diffMarks(q), showAnswer: "collapsed" });
    else rendered.append(el("p", "hint", "还没有题面"));
    body.append(rendered);

    const actions = el("div", "card-actions");
    if (!approved) {
      const blocked = figureBlocksApproval(q);
      const blockedLabel = figureReview(q)?.status === "conflict" ? "处理配图冲突" : "处理漏图提醒";
      const approve = button(blocked ? blockedLabel : approvalNeedsReview(q) ? "重新标记通过" : "标记通过", "primary",
        () => blocked ? focusFigureReview(q) : approveQuestion(q, true), "", { iconName: blocked ? "image" : "check", key: "Enter" });
      approve.disabled = blocked ? false : !canApprove(q);
      approve.title = blocked ? "在黄色区域选择保留、调整或移除配图"
        : canApprove(q) ? "对照原卷确认无误后通过（Enter），会自动跳到下一张"
          : q.state === "red" ? "识读失败的题需先修改或重读，不能直接通过" : "请等待识读完成并确认题面";
      actions.append(approve);
    } else {
      actions.append(button("撤销通过", "", () => approveQuestion(q, false), "撤销通过（U）"));
    }
    actions.append(
      button("改字", "", () => openEditor(card, q), "修改题干、选项、题型，也可以补答案和解析（E）"),
      button("调整范围", q.regions.length ? "" : "primary", () => openPageDialog("regions", q), "截图框多了或少了，拖一下；保存后 AI 自动重读（R）"),
      button("配图", "", () => openPageDialog("figures", q), "增删配图，或调整配图的裁剪框（F）", { iconName: "image" })
    );
    const more = el("details", "more");
    const summary = el("summary", "", "更多");
    summary.append(icon("chevron"));
    more.append(summary);
    const menu = el("div", "more-menu");
    menu.append(button("看两位读者的原始读法", "quiet small", () => { more.open = false; toggleReads(card, q); }));
    menu.append(button("让 AI 重读这题", "quiet small", () => { more.open = false; rereadQuestion(q); }));
    menu.append(button("删除这张卡", "quiet small danger", () => { more.open = false; deleteQuestion(q); }));
    more.append(menu);
    actions.append(more);
    body.append(actions);
    card.append(source, body);
    if (approved) card.append(expandToggle(q, false));
    return card;
  }

  // 题卡收起时只显示第一行；截断时不把 $…$ 公式截成半截。
  function firstLine(text) {
    const line = String(text || "").split("\n")[0];
    if (line.length <= 120) return line;
    let cut = 120;
    while (cut < line.length && (line.slice(0, cut).match(/\$/g) || []).length % 2) cut += 1;
    return line.slice(0, cut) + "…";
  }

  function publicationChip(q) {
    if (!q.publication) return el("span");
    if (q.publication.up_to_date) return el("span", "chip published", `已入库 v${q.publication.version}`);
    return el("span", "chip stale", "有改动未入库");
  }

  function readsNode(q) {
    const box = el("div", "reads");
    box.dataset.questionId = String(q.id);
    const labels = { a: "读法甲", b: "读法乙", c: "裁决" };
    Object.entries(q.reads).forEach(([key, reading]) => {
      if (!reading || (!reading.stem && !reading.error && !reading.witness)) return;
      const item = el("div", "read");
      const witnessNote = key === "c" ? "与读法乙一致，据此采用读法乙，未再调用裁决模型"
        : "与读法甲逐字一致，未再调用复核模型";
      const label = reading.witness ? `旁证 · MinerU 自己识别的文字（${witnessNote}）`
        : `${labels[key]}${reading.engine ? ` · ${reading.engine}` : ""}`;
      item.append(el("p", "read-label", label));
      if (reading.witness) {
        const literal = el("div", "read-text");
        R.renderLiteral(literal, reading.witness);
        item.append(literal);
      } else if (reading.error) item.append(el("p", "read-error", reading.error));
      else {
        const text = [reading.stem, ...OPTION_KEYS.filter((k) => (reading.options || {})[k]).map((k) => `${k}. ${reading.options[k]}`)].join("\n");
        const literal = el("div", "read-text");
        R.renderLiteral(literal, text);
        item.append(literal);
      }
      box.append(item);
    });
    if (!box.children.length) box.append(el("p", "hint", "没有识读记录"));
    return box;
  }

  function toggleReadsNear(anchor, q) {
    const container = anchor.parentElement;
    const existing = container?.querySelector(`.reads[data-question-id="${q.id}"]`);
    if (existing) { existing.remove(); return; }
    anchor.after(readsNode(q));
  }

  function toggleReads(card, q) {
    const existing = card.querySelector(`.reads[data-question-id="${q.id}"]`);
    if (existing) { existing.remove(); return; }
    const box = readsNode(q);
    card.querySelector(".card-actions").after(box);
  }

  // ---------------------------------------------------------------- 题卡动作

  function applyQuestion(data) {
    if (data.question) {
      const index = state.questions.findIndex((q) => q.id === data.question.id);
      if (index >= 0) state.questions[index] = data.question; else state.questions.push(data.question);
      state.questions.sort(questionCompare);
    }
    if (data.paper) {
      state.paper = data.paper;
      const index = state.papers.findIndex((paper) => paper.id === data.paper.id);
      if (index >= 0) { state.papers[index] = data.paper; renderPaperList(); }
    }
    renderPaper();
  }

  async function approveQuestion(q, approved, { advance = true } = {}) {
    try {
      const data = await api(`/api/questions/${q.id}/approve`, { method: "POST", body: { approved } });
      state.expanded.delete(q.id);
      applyQuestion(data);
      const fresh = questionById(q.id) || q;
      if (approved) {
        const c = counts();
        const left = c.todo + c.green;
        toast(left ? `第 ${q.number} 题已标记通过；审批已绑定当前题面版本` : "本卷已全部标记通过，可以点“入库”了",
          left ? "" : "success", { label: "撤销", onClick: () => approveQuestion(fresh, false, { advance: false }) });
        if (advance) focusNext(q);
      } else {
        toast(`已撤销第 ${q.number} 题的通过`);
        setCurrent(q.id);
      }
      return true;
    } catch (error) { toast(error.message, "error"); return false; }
  }

  function focusNext(q) {
    const next = nextToReview(q);
    if (next) setCurrent(next.id, { scroll: true, focus: true });
    else setCurrent(q.id);
  }

  async function rereadQuestion(q) {
    if (q.edited && !(await confirmDialog({ title: "让 AI 重读这道题？", text: "这道题的文字改过。重读会用 AI 的新读法替换你改的文字。", ok: "重读", danger: true }))) return;
    try {
      applyQuestion(await api(`/api/questions/${q.id}/reread`, { method: "POST", body: {} }));
      refreshPaper();
      toast(`第 ${q.number} 题已交给 AI 重读`);
    } catch (error) { toast(error.message, "error"); }
  }

  function updatePaperFromResponse(paper) {
    if (!paper) return;
    state.paper = paper;
    const index = state.papers.findIndex((item) => item.id === paper.id);
    if (index >= 0) state.papers[index] = paper;
    renderPaperList();
  }

  async function softDeleteQuestions(questionIds, { singleQuestion = null } = {}) {
    const ids = [...new Set(questionIds.map(Number))].filter((id) => questionById(id));
    if (!ids.length || state.selectionBusy) return;
    const blocked = ids.map(questionById).map((q) => ({ q, reason: questionDeleteBlockReason(q) })).find((item) => item.reason);
    if (blocked) { toast(`第 ${blocked.q.number} 题：${blocked.reason}`, "error"); return; }
    const count = ids.length;
    const title = singleQuestion ? `把第 ${singleQuestion.number} 题移到回收站？` : `把选中的 ${count} 道题移到回收站？`;
    const ok = await confirmDialog({
      title,
      text: "题卡会从当前审题列表移走，但不会立即永久清除；可以在提示条撤销，也可以稍后从题卡回收站按这一批恢复。",
      ok: "移到回收站",
      danger: true
    });
    if (!ok) return;

    const paperId = state.paperId;
    state.selectionBusy = true;
    renderSelectionState();
    try {
      const data = await api(`/api/papers/${paperId}/questions/delete`, { method: "POST", body: { question_ids: ids } });
      if (state.paperId === paperId) {
        const removed = new Set(ids);
        state.questions = state.questions.filter((question) => !removed.has(question.id));
        ids.forEach((id) => { state.rendered.delete(id); state.expanded.delete(id); state.editing.delete(id); });
        clearQuestionSelection({ render: false });
        if (state.current !== null && removed.has(state.current)) {
          state.current = state.questions.find(visible)?.id ?? state.questions[0]?.id ?? null;
        }
        updatePaperFromResponse(data.paper);
        renderPaper();
      } else {
        await loadPapers();
      }
      const deleted = Number(data.deleted) || count;
      const batchId = data.undo_batch?.id;
      toast(`${deleted} 道题已移到回收站`, "success", batchId ? {
        label: "撤销",
        onClick: () => restoreDeletedBatch(paperId, batchId)
      } : null);
    } catch (error) {
      toast(error.message, "error");
    } finally {
      state.selectionBusy = false;
      if (state.paperId === paperId) renderSelectionState();
    }
  }

  async function deleteSelectedQuestions() {
    const ids = state.questions.filter((question) => state.selected.has(question.id)).map((question) => question.id);
    await softDeleteQuestions(ids);
  }

  async function deleteQuestion(q) {
    await softDeleteQuestions([q.id], { singleQuestion: q });
  }

  function mergeRestoredQuestions(questions) {
    const byId = new Map(state.questions.map((question) => [question.id, question]));
    (questions || []).forEach((question) => byId.set(question.id, question));
    state.questions = [...byId.values()].sort(questionCompare);
  }

  async function restoreDeletedBatch(paperId, batchId) {
    if (state.trashBusy) return;
    state.trashBusy = true;
    renderTrashBusy();
    try {
      const data = await api(`/api/papers/${paperId}/question-trash/${batchId}/restore`, { method: "POST", body: {} });
      if (state.paperId === paperId) {
        mergeRestoredQuestions(data.questions);
        updatePaperFromResponse(data.paper);
        renderPaper();
      } else {
        await loadPapers();
      }
      const restored = Number(data.restored) || data.questions?.length || 0;
      toast(data.already_restored ? "这批题卡之前已经恢复" : `已恢复 ${restored} 道题`, "success");
      if ($("trashDialog").open && state.paperId === paperId) await loadQuestionTrash();
    } catch (error) {
      toast(error.message, "error");
    } finally {
      state.trashBusy = false;
      renderTrashBusy();
    }
  }

  function syncTrashControls() {
    const count = Math.max(0, Number(state.paper?.trash_count) || 0);
    const label = count ? `题卡回收站（${count}）` : "题卡回收站";
    const ready = state.paper?.status === "ready";
    if ($("questionTrashLabel")) $("questionTrashLabel").textContent = label;
    if ($("settingsTrashLabel")) $("settingsTrashLabel").textContent = label;
    if ($("questionTrash")) {
      $("questionTrash").hidden = !state.paper;
      $("questionTrash").disabled = !ready;
      $("questionTrash").title = ready ? (count ? `恢复 ${count} 道最近删除的题卡` : "当前回收站为空") : "任务处理完成后才能使用题卡回收站";
    }
    if ($("settingsTrash")) {
      $("settingsTrash").disabled = !ready;
      $("settingsTrash").title = ready ? (count ? `有 ${count} 道已删除题卡可以按批次恢复` : "查看最近删除；当前回收站为空") : "任务处理完成后才能使用题卡回收站";
    }
  }

  function trashTime(value) {
    const date = new Date(value);
    if (!value || Number.isNaN(date.getTime())) return "删除时间未知";
    return new Intl.DateTimeFormat("zh-CN", {
      month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit"
    }).format(date);
  }

  function renderTrashBusy() {
    $("trashDialog")?.querySelectorAll(".trash-restore").forEach((node) => {
      const restored = node.dataset.restored === "true";
      node.disabled = state.trashBusy || restored;
      if (!restored) node.textContent = state.trashBusy ? "正在恢复…" : "恢复这一批";
    });
  }

  function renderTrashBatches(batches, paperId) {
    const list = $("trashList");
    list.replaceChildren();
    if (!batches.length) {
      const empty = el("div", "trash-empty");
      empty.append(icon("trash"), el("strong", "", "回收站是空的"), el("span", "", "移除的题卡会按删除批次出现在这里。"));
      list.append(empty);
      return;
    }
    batches.forEach((batch) => {
      const restored = Boolean(batch.restored_at);
      const item = el("section", `trash-batch${restored ? " restored" : ""}`);
      const head = el("div", "trash-batch-head");
      const title = el("span", "trash-batch-title");
      title.append(el("strong", "", `${batch.count || batch.questions?.length || 0} 道题`), el("small", "", `${trashTime(batch.created_at)}${restored ? ` · 已于 ${trashTime(batch.restored_at)}恢复` : ""}`));
      const restore = button(restored ? "已恢复" : "恢复这一批", "small trash-restore", () => restoreDeletedBatch(paperId, batch.id));
      restore.dataset.restored = String(restored);
      restore.disabled = restored || state.trashBusy || ACTIVE_STATUS.has(state.paper?.status);
      restore.title = ACTIVE_STATUS.has(state.paper?.status) && !restored ? "任务仍在处理中，完成后才能恢复题卡" : "把这一批题卡恢复到原题组和题号位置";
      head.append(title, restore);
      const questions = el("ul", "trash-questions");
      (batch.questions || []).forEach((question) => {
        const group = typeof question.group === "object" ? question.group?.title : question.section;
        const prefix = [group, `第 ${question.number} 题`].filter(Boolean).join(" · ");
        const stem = String(question.stem || "").replace(/\s+/g, " ").trim();
        questions.append(el("li", "", `${prefix}${stem ? ` — ${stem.slice(0, 90)}${stem.length > 90 ? "…" : ""}` : ""}`));
      });
      item.append(head, questions);
      list.append(item);
    });
    renderTrashBusy();
  }

  async function loadQuestionTrash() {
    const paperId = state.paperId;
    if (!paperId) return;
    $("trashList").replaceChildren(el("p", "trash-loading", "正在读取最近删除…"));
    try {
      const data = await api(`/api/papers/${paperId}/question-trash`);
      if (state.paperId !== paperId || !$("trashDialog").open) return;
      renderTrashBatches(data.batches || [], paperId);
    } catch (error) {
      $("trashList").replaceChildren(el("p", "trash-loading error", error.message));
    }
  }

  function openQuestionTrash() {
    if (!state.paperId) return;
    $("toolsMenu").open = false;
    if ($("settingsDialog").open) $("settingsDialog").close();
    $("trashTitle").textContent = `${paperDisplayName(state.paper)} · 题卡回收站`;
    $("trashDialog").showModal();
    loadQuestionTrash();
  }

  async function retryPaper(materialType = null) {
    if (materialType === "book" && !(await confirmDialog({
      title: "按教材模式重试？",
      text: "程序会在本机每 100 页稳定分片，再分别交给 MinerU。原 PDF 不会被改动。",
      ok: "按教材重试",
    }))) return;
    try {
      const data = await api(`/api/papers/${state.paperId}/retry`, {
        method: "POST",
        body: materialType ? { material_type: materialType } : {},
      });
      refreshPaper();
      loadPapers();
      toast(data.message || "已重试处理");
    } catch (error) { toast(error.message, "error"); }
  }

  function openRenameDialog() {
    if (!state.paper) return;
    const input = $("renameInput");
    input.value = paperDisplayName(state.paper);
    input.setCustomValidity("");
    $("renameOriginal").textContent = `原始文件：${state.paper.filename || "未记录"}`;
    $("renameDialog").showModal();
    requestAnimationFrame(() => { input.focus(); input.select(); });
  }

  $("renamePaper").addEventListener("click", openRenameDialog);
  $("renameInput").addEventListener("input", () => $("renameInput").setCustomValidity(""));
  $("renameForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!state.paper) return;
    const input = $("renameInput");
    const name = input.value.trim();
    if (!name) {
      input.setCustomValidity("请输入任务名称");
      input.reportValidity();
      return;
    }
    const paperId = state.paper.id;
    const previous = state.paper;
    const save = $("renameSave");
    save.disabled = true;
    try {
      const data = await api(`/api/papers/${paperId}`, { method: "PATCH", body: { name } });
      const updated = data.paper || { ...previous, name };
      const index = state.papers.findIndex((paper) => paper.id === paperId);
      if (index >= 0) state.papers[index] = updated;
      if (state.paperId === paperId) {
        state.paper = updated;
        renderPaper();
      }
      renderPaperList();
      if ($("renameDialog").open) $("renameDialog").close();
      toast("任务名称已修改，题目来源已同步更新", "success");
    } catch (error) {
      toast(error.message, "error");
      input.focus();
    } finally {
      save.disabled = false;
    }
  });

  async function deletePaper() {
    const paper = state.paper;
    if (!paper || !["ready", "failed", "needs_grouping"].includes(paper.status)) return;
    if ((paper.counts?.published || 0) > 0) {
      toast("这项任务已有正式题库记录，为保留来源追溯只能归档", "error");
      return;
    }
    const displayName = paperDisplayName(paper);
    const ok = await confirmDialog({
      title: `删除任务“${displayName}”？`,
      text: "会永久删除这项任务、上传的原文件和全部草稿题卡，且无法撤销。已有正式题库记录的任务不能删除，只能归档。",
      ok: "删除任务",
      danger: true
    });
    if (!ok) return;
    const paperId = paper.id;
    const oldIndex = state.papers.findIndex((item) => item.id === paperId);
    try {
      const result = await api(`/api/papers/${paperId}`, { method: "DELETE" });
      const message = result.warning || `已删除任务“${displayName}”`;
      const kind = result.warning ? "error" : "";
      state.papers = state.papers.filter((item) => item.id !== paperId);
      if (state.paperId !== paperId) {
        renderPaperList();
        toast(message, kind);
        return;
      }
      const next = state.papers[Math.min(Math.max(oldIndex, 0), state.papers.length - 1)];
      clearPaperSelection();
      if (next) await selectPaper(next.id);
      toast(message, kind);
    } catch (error) { toast(error.message, "error"); }
  }

  async function archivePaper() {
    const paper = state.paper;
    if (!paper) return;
    if (ACTIVE_STATUS.has(paper.status)) {
      toast("任务正在处理中，完成后再归档", "error");
      return;
    }
    if ((Number(paper.trash_count) || 0) > 0) {
      toast("回收站里还有题卡；请先恢复这些题卡，再归档任务", "error");
      return;
    }
    const displayName = paperDisplayName(paper);
    const ok = await confirmDialog({
      title: `归档任务“${displayName}”？`,
      text: "归档后会从左侧任务列表隐藏，但不会删除原文件、题卡或正式题库中的来源记录。",
      ok: "归档任务"
    });
    if (!ok) return;
    const paperId = paper.id;
    const oldIndex = state.papers.findIndex((item) => item.id === paperId);
    try {
      await api(`/api/papers/${paperId}/archive`, { method: "POST", body: {} });
      state.papers = state.papers.filter((item) => item.id !== paperId);
      const next = state.papers[Math.min(Math.max(oldIndex, 0), state.papers.length - 1)];
      clearPaperSelection();
      if (next) await selectPaper(next.id);
      toast(`已归档任务“${displayName}”`, "success");
    } catch (error) { toast(error.message, "error"); }
  }

  // ---------------------------------------------------------------- 设置

  function setApiState(id, configured) {
    const node = $(id);
    node.textContent = configured ? "已配置" : "未配置";
    node.className = `api-state ${configured ? "ready" : "missing"}`;
  }

  function selectedEngine(engines, role, fallback) {
    return engines.saved?.[role] || engines.selected?.[role] || engines.selection?.[role]
      || engines[`${role}_setting`] || engines[role] || fallback;
  }

  function fillModelSelect(select, entries, selected) {
    select.replaceChildren(...entries.map(({ value, label }) => {
      const option = el("option", "", label);
      option.value = value;
      return option;
    }));
    if ([...select.options].some((option) => option.value === selected)) {
      select.value = selected;
    } else if (select.options.length) {
      select.selectedIndex = 0;
    }
  }

  function settingsEngineChoices(engines) {
    return (Array.isArray(engines.choices) ? engines.choices : [])
      .filter((choice) => choice && typeof choice.key === "string" && choice.key
        && typeof choice.model === "string" && choice.model)
      .map((choice) => {
        const providerKey = choice.provider_key || choice.key.split("_", 1)[0];
        return {
          ...choice,
          provider_key: providerKey,
          provider: choice.provider || providerKey || choice.key,
          model: engines.saved?.models?.[providerKey] || engines.models?.[providerKey] || choice.model
        };
      });
  }

  function renderProviderModelSetting(engines, choices, providerKey, inputId, listId, stateId) {
    const configured = Boolean(engines.configured?.[providerKey]);
    const providerChoices = choices.filter((choice) => choice.provider_key === providerKey);
    const suggested = Array.isArray(engines.suggested_models?.[providerKey])
      ? engines.suggested_models[providerKey] : [];
    const modelIds = [...new Set([...suggested, ...providerChoices.map((choice) => choice.model)]
      .map((value) => String(value || "").trim()).filter(Boolean))];
    const current = String(engines.saved?.models?.[providerKey] || engines.models?.[providerKey]
      || providerChoices[0]?.model || modelIds[0] || "");
    const input = $(inputId);
    const list = $(listId);
    const providerState = $(stateId);
    input.value = current;
    input.placeholder = modelIds[0] || "填写服务商支持的 model_id";
    list.replaceChildren(...modelIds.map((modelId) => {
      const option = document.createElement("option");
      option.value = modelId;
      return option;
    }));
    providerState.textContent = configured ? "API 已配置" : "API 未配置";
    providerState.className = `model-provider-state ${configured ? "ready" : "missing"}`;
  }

  function renderSettingsModels() {
    const status = state.status;
    if (!status) return;
    const engines = status.engines || {};
    const configured = status.configured || engines.configured || {};
    setApiState("settingsMineruState", Boolean(status.mineru || configured.mineru));
    setApiState("settingsMinimaxState", Boolean(configured.minimax));
    setApiState("settingsSiliconflowState", Boolean(configured.siliconflow));

    const choices = settingsEngineChoices(engines);
    const modelEntries = choices.map((choice) => ({
      value: choice.key,
      label: `${choice.provider} · ${choice.model}${choice.available === false ? "（API 未配置）" : ""}`
    }));
    const defaultPrimary = modelEntries[0]?.value || "";
    fillModelSelect($("settingsPrimaryModel"), modelEntries,
      selectedEngine(engines, "primary", defaultPrimary));
    fillModelSelect($("settingsCheckerModel"), [
      { value: "auto", label: "自动（优先使用另一家已配置模型）" }, ...modelEntries
    ], selectedEngine(engines, "checker", "auto"));
    fillModelSelect($("settingsArbiterModel"), [
      { value: "primary", label: "沿用主读模型" },
      { value: "checker", label: "沿用复核模型" },
      ...modelEntries
    ], selectedEngine(engines, "arbiter", "primary"));
    renderProviderModelSetting(engines, choices, "minimax", "settingsMinimaxModel",
      "settingsMinimaxModels", "settingsMinimaxModelState");
    renderProviderModelSetting(engines, choices, "siliconflow", "settingsSiliconflowModel",
      "settingsSiliconflowModels", "settingsSiliconflowModelState");
    const summary = [
      status.reader && `当前主读：${status.reader}`,
      status.checker && `复核：${status.checker}`,
      status.arbiter && `裁决：${status.arbiter}`
    ].filter(Boolean).join("；") || "当前没有可用的识读模型。";
    $("settingsModelSummary").textContent = `${summary} 保存后的选择从下一项新任务或重新识读开始生效。`;
  }

  const CREDENTIAL_FIELDS = {
    mineru: { input: "credentialMineruInput", clear: "credentialMineruClear", state: "credentialMineruState", label: "MinerU" },
    minimax: { input: "credentialMinimaxInput", clear: "credentialMinimaxClear", state: "credentialMinimaxState", label: "MiniMax" },
    siliconflow: { input: "credentialSiliconflowInput", clear: "credentialSiliconflowClear", state: "credentialSiliconflowState", label: "硅基流动" }
  };

  function credentialAccounts(value) {
    return [...new Set(String(value || "").split(/[;\r\n]+/)
      .map((item) => item.trim()).filter(Boolean))];
  }

  function resetCredentialInputs() {
    Object.values(CREDENTIAL_FIELDS).forEach((field) => {
      $(field.input).value = "";
      $(field.input).disabled = false;
      $(field.clear).checked = false;
    });
  }

  function renderCredentialStates(payload) {
    const services = payload?.services || {};
    Object.entries(CREDENTIAL_FIELDS).forEach(([service, field]) => {
      const status = services[service] || {};
      const count = Math.max(0, Number(status.count) || 0);
      const node = $(field.state);
      node.textContent = status.configured ? `已配置 ${count} 个账号` : "未配置";
      node.className = `api-state ${status.configured ? "ready" : "missing"}`;
    });
  }

  async function loadCredentialStates() {
    const payload = await api("/api/settings/credentials");
    renderCredentialStates(payload);
    return payload;
  }

  async function openCredentialSettings() {
    resetCredentialInputs();
    $("credentialResult").textContent = "";
    $("credentialDialog").showModal();
    try {
      await loadCredentialStates();
      requestAnimationFrame(() => $("credentialMineruInput").focus());
    } catch (error) {
      $("credentialResult").textContent = error.message;
      toast(error.message, "error");
    }
  }

  function renderSettingsTask() {
    const paper = state.paper;
    $("settingsNoTask").hidden = Boolean(paper);
    $("settingsTaskPanel").hidden = !paper;
    syncTrashControls();
    if (!paper) return;
    $("settingsTaskName").textContent = paperDisplayName(paper);
    $("settingsTaskStatus").textContent = paper.status_label || paperSummary(paper);
    const active = ACTIVE_STATUS.has(paper.status);
    const hasTrash = (Number(paper.trash_count) || 0) > 0;
    $("settingsRename").disabled = active;
    $("settingsAddQuestion").disabled = paper.status === "needs_grouping" || (active && paper.status !== "reading");
    $("settingsResegment").disabled = !["ready", "failed"].includes(paper.status);
    $("settingsPageOrder").disabled = hasTrash || !(paper.photos && (paper.pages || []).length > 1
      && ["ready", "failed", "needs_grouping"].includes(paper.status));
    $("settingsPageOrder").title = hasTrash
      ? "回收站里还有题卡；请先恢复后再调整页序" : "";
    const groups = suggestedSplitGroups(paper);
    $("settingsConfirmStructure").hidden = paper.status !== "needs_grouping";
    $("settingsSplit").hidden = !(paper.structure_conflict && groups.length > 1);
    $("settingsSplit").textContent = groups.length > 1 ? `按建议拆成 ${groups.length} 份` : "拆分任务";

    const notes = [...(paper.notes || [])];
    const structureMessage = paper.structure_message
      || (typeof paper.structure_conflict === "object" && paper.structure_conflict.message);
    if (structureMessage && !notes.includes(structureMessage)) notes.unshift(structureMessage);
    if (paper.error && !notes.includes(paper.error)) notes.unshift(paper.error);
    $("settingsTaskNotes").replaceChildren(...(notes.length ? notes : ["暂无处理记录。"])
      .map((note) => el("li", "", note)));

    const published = paper.counts?.published || 0;
    $("settingsArchive").hidden = false;
    $("settingsArchive").disabled = active || hasTrash;
    $("settingsArchive").title = hasTrash
      ? "回收站里还有题卡；请先恢复后再归档" : "";
    const isSplitTask = Boolean(paper.structure?.split_from || paper.structure?.split_children?.length);
    $("settingsDelete").hidden = published > 0 || isSplitTask
      || !["ready", "failed", "needs_grouping"].includes(paper.status);
    $("settingsDangerHint").textContent = published > 0
      ? `已有 ${published} 道正式题库记录。为保留来源追溯，只能归档，不能永久删除。`
      : isSplitTask ? "这是拆分资料的原稿或子任务；为保留双向追溯，只能归档。"
      : active ? "任务正在处理中，完成或失败后才能永久删除。"
        : "归档只从任务列表隐藏；永久删除会一并删除原文件和草稿，无法撤销。";
  }

  function closeSettingsThen(action) {
    if ($("settingsDialog").open) $("settingsDialog").close();
    requestAnimationFrame(action);
  }

  function openSettings() {
    renderSettingsModels();
    renderSettingsTask();
    $("settingsLens").checked = state.lens;
    $("settingsModelResult").textContent = "";
    $("settingsDialog").showModal();
    void loadStatus();
    requestAnimationFrame(() => $("settingsClose").focus());
  }

  $("settingsButton").addEventListener("click", openSettings);
  $("settingsCredentialOpen").addEventListener("click", openCredentialSettings);
  $("settingsLens").addEventListener("change", (event) => setLens(event.target.checked));
  $("settingsRename").addEventListener("click", () => closeSettingsThen(openRenameDialog));
  $("settingsAddQuestion").addEventListener("click", () => closeSettingsThen(() => openPageDialog("new")));
  $("settingsResegment").addEventListener("click", () => closeSettingsThen(resegmentPaper));
  $("settingsPageOrder").addEventListener("click", () => closeSettingsThen(openOrderDialog));
  $("settingsTrash").addEventListener("click", () => closeSettingsThen(openQuestionTrash));
  $("settingsConfirmStructure").addEventListener("click", () => closeSettingsThen(confirmStructure));
  $("settingsSplit").addEventListener("click", () => closeSettingsThen(openSplitDialog));
  $("settingsArchive").addEventListener("click", () => closeSettingsThen(archivePaper));
  $("settingsDelete").addEventListener("click", () => closeSettingsThen(deletePaper));
  $("questionTrash").addEventListener("click", openQuestionTrash);
  $("selectionCancel").addEventListener("click", () => clearQuestionSelection());
  $("selectionDelete").addEventListener("click", deleteSelectedQuestions);

  Object.values(CREDENTIAL_FIELDS).forEach((field) => {
    $(field.clear).addEventListener("change", () => {
      const clearing = $(field.clear).checked;
      if (clearing) $(field.input).value = "";
      $(field.input).disabled = clearing;
    });
  });

  $("credentialDialog").addEventListener("close", () => {
    resetCredentialInputs();
    requestAnimationFrame(() => $("settingsCredentialOpen").focus());
  });

  $("credentialForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const services = {};
    const clearing = [];
    for (const [service, field] of Object.entries(CREDENTIAL_FIELDS)) {
      if ($(field.clear).checked) {
        services[service] = { action: "clear" };
        clearing.push(field.label);
        continue;
      }
      const accounts = credentialAccounts($(field.input).value);
      if (accounts.length > 8) {
        const message = `${field.label} 最多保存 8 个账号`;
        $("credentialResult").textContent = message;
        toast(message, "error");
        return;
      }
      services[service] = accounts.length ? { action: "replace", accounts } : { action: "keep" };
    }
    if (clearing.length) {
      const confirmed = await confirmDialog({
        title: `清除 ${clearing.join("、")} 的 API 配置？`,
        text: "清除后，新上传或重新识读可能无法继续；正在运行的当前任务不会中途切换账号。",
        ok: "确认清除",
        danger: true
      });
      if (!confirmed) return;
    }
    const save = $("credentialSave");
    save.disabled = true;
    $("credentialResult").textContent = "正在验证并加密保存…";
    try {
      const result = await api("/api/settings/credentials", { method: "POST", body: { services } });
      resetCredentialInputs();
      renderCredentialStates(result);
      const message = result.message || "API 配置已加密保存；下一项任务开始时生效。";
      $("credentialResult").textContent = message;
      toast(message, "success");
      const refreshed = await loadStatus();
      if (!refreshed) {
        // 保存已经成功，状态区刷新失败不能被误报成“保存失败”并诱导重复提交。
        $("credentialResult").textContent = `${message} 当前状态暂未刷新，重新打开设置或刷新页面即可查看。`;
      }
    } catch (error) {
      // 无论成功与否都不让提交过的完整密钥继续留在页面内存和输入框中。
      resetCredentialInputs();
      $("credentialResult").textContent = error.message;
      toast(error.message, "error");
    } finally {
      save.disabled = false;
    }
  });

  $("modelSettingsForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const save = $("settingsModelSave");
    save.disabled = true;
    $("settingsModelResult").textContent = "正在保存…";
    try {
      await api("/api/settings/models", {
        method: "POST",
        body: {
          primary: $("settingsPrimaryModel").value,
          checker: $("settingsCheckerModel").value,
          arbiter: $("settingsArbiterModel").value,
          models: {
            minimax: $("settingsMinimaxModel").value.trim(),
            siliconflow: $("settingsSiliconflowModel").value.trim()
          }
        }
      });
      const message = "已保存；从下一项新任务或重新识读开始生效，不会改写现有题卡。";
      $("settingsModelResult").textContent = message;
      toast(message, "success");
      await loadStatus();
      renderSettingsModels();
    } catch (error) {
      $("settingsModelResult").textContent = error.message;
      toast(error.message, "error");
    } finally { save.disabled = false; }
  });

  $("approveGreen").addEventListener("click", async () => {
    const count = counts().green;
    if (!count) return;
    const ok = await confirmDialog({
      title: `把 ${count} 张绿卡批量标记为通过？`,
      text: "绿卡只表示 AI 的多次识读一致，不自动证明已经逐题对照原卷。请按你的使用场景确认后再继续。",
      ok: `标记 ${count} 张通过`
    });
    if (!ok) return;
    try {
      const data = await api(`/api/papers/${state.paperId}/approve-green`, { method: "POST", body: {} });
      toast(`已将 ${data.approved} 张绿卡标记通过；审批绑定当前题面版本`, "success");
      refreshPaper();
    } catch (error) { toast(error.message, "error"); }
  });

  async function publish() {
    try {
      const data = await api(`/api/papers/${state.paperId}/publish`, { method: "POST", body: {} });
      const parts = [];
      if (data.created) parts.push(`新入库 ${data.created} 题`);
      if (data.unchanged) parts.push(`${data.unchanged} 题内容没变`);
      if (data.problems.length) parts.push(`${data.problems.length} 题没入库：${data.problems.join("；")}`);
      toast(parts.join("，") || "没有需要入库的题", data.problems.length ? "error" : "success",
        data.created && !data.problems.length ? { label: "去题库看看", onClick: () => { window.location.href = "/library"; } } : null);
      refreshPaper();
      loadPapers();
    } catch (error) { toast(error.message, "error"); }
  }

  $("publishButton").addEventListener("click", publish);

  // ---------------------------------------------------------------- 改字

  function openEditor(card, q) {
    if (card.querySelector(".editor")) { card.querySelector(".stem-input")?.focus(); return; }
    state.editing.add(q.id);
    setCurrent(q.id);
    const editor = el("form", "editor");
    const title = el("div", "editor-title");
    title.append(el("span", "", `改字 · 第 ${q.number} 题`));
    const typeSelect = el("select");
    Object.entries(TYPE_NAMES).forEach(([value, label]) => {
      const option = el("option", "", label);
      option.value = value;
      option.selected = value === q.question_type;
      typeSelect.append(option);
    });
    const typeRow = el("label", "field inline");
    typeRow.append(el("span", "", "题型"), typeSelect);
    const stem = el("textarea", "stem-input");
    stem.value = q.stem;
    stem.rows = Math.min(14, Math.max(4, q.stem.split("\n").length + 2));
    stem.spellcheck = false;
    const stemRow = el("label", "field");
    stemRow.append(el("span", "", "题干（公式用 $…$ 包住的 LaTeX）"), stem);
    const optionBox = el("div", "option-inputs");
    const optionInputs = {};
    OPTION_KEYS.forEach((key) => {
      const row = el("label", "field inline");
      const input = el("input");
      input.value = (q.options || {})[key] || "";
      input.spellcheck = false;
      optionInputs[key] = input;
      row.append(el("span", "", key), input);
      optionBox.append(row);
    });
    const extra = el("details", "extra");
    extra.append(el("summary", "", "答案与解析（可选，原卷没有就留空）"));
    if ((q.answer || "").trim() || (q.analysis || "").trim()) extra.open = true;
    const answer = el("input");
    answer.value = q.answer || "";
    const analysis = el("textarea");
    analysis.rows = 3;
    analysis.value = q.analysis || "";
    const answerRow = el("label", "field inline");
    answerRow.append(el("span", "", "答案"), answer);
    const analysisRow = el("label", "field");
    analysisRow.append(el("span", "", "解析"), analysis);
    extra.append(answerRow, analysisRow);
    const preview = el("div", "editor-preview");
    const saveButton = el("button", "button primary", "保存");
    saveButton.type = "submit";
    const cancel = button("取消", "", () => close());
    const bar = el("div", "editor-actions");
    bar.append(saveButton, cancel, el("p", "hint", "Ctrl+Enter 保存 · Esc 取消"));
    editor.append(title, typeRow, stemRow, optionBox, extra, el("p", "preview-label", "预览"), preview, bar);

    const collect = () => ({
      stem: stem.value,
      options: Object.fromEntries(OPTION_KEYS.map((k) => [k, optionInputs[k].value]).filter(([, v]) => v.trim())),
      question_type: typeSelect.value,
      answer: answer.value,
      analysis: analysis.value
    });
    let timer = null;
    const update = () => {
      optionBox.hidden = !CHOICE.has(typeSelect.value) && !OPTION_KEYS.some((k) => optionInputs[k].value.trim());
      clearTimeout(timer);
      timer = setTimeout(() => {
        const data = collect();
        R.renderQuestion(preview, { ...content(q), ...data, options: optionBox.hidden ? {} : data.options }, { showNumber: false, showAnswer: "open" });
      }, 150);
    };
    editor.addEventListener("input", update);
    editor.addEventListener("keydown", (event) => {
      if (event.key === "Escape") { event.preventDefault(); close(); }
      else if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) { event.preventDefault(); editor.requestSubmit(); }
    });
    editor.addEventListener("submit", async (event) => {
      event.preventDefault();
      saveButton.disabled = true;
      try {
        const data = collect();
        if (optionBox.hidden) data.options = {};
        const result = await api(`/api/questions/${q.id}/text`, { method: "POST", body: { ...data, approve: false } });
        close(false);
        applyQuestion(result);
        toast(q.approved ? "已保存并撤销旧审批；请核对原卷后重新标记通过" : "已保存；请核对原卷后标记通过", "success");
        setCurrent(q.id, { focus: true });
      } catch (error) {
        toast(error.message, "error");
        saveButton.disabled = false;
      }
    });
    function close(rerender = true) {
      state.editing.delete(q.id);
      editor.remove();
      card.querySelector(".rendered").hidden = false;
      card.querySelector(".card-actions").hidden = false;
      if (toggle) toggle.hidden = false;
      if (rerender) { state.rendered.delete(q.id); renderCards(); document.querySelector(`.card[data-id="${q.id}"]`)?.focus({ preventScroll: true }); }
    }
    const toggle = card.querySelector(".card-toggle");
    if (toggle) toggle.hidden = true;
    card.querySelector(".rendered").hidden = true;
    card.querySelector(".card-actions").hidden = true;
    card.querySelector(".reads")?.remove();
    card.querySelector(".card-body").append(editor);
    update();
    stem.focus();
  }

  // ---------------------------------------------------------------- 原卷页面上拖框（调整范围 / 配图 / 补一题）

  const dialog = {
    mode: null, question: null, boxes: [], page: 0, drag: null,
    slotTarget: null, slotAnchor: null, pendingFigure: null, ignoredCandidates: new Set()
  };

  function openPageDialog(mode, q = null, { page: requestedPage = null } = {}) {
    if (!state.paper?.pages?.length) return;
    dialog.mode = mode;
    dialog.question = q;
    closeFigureSlotMenu({ cancelPending: true, rerender: false });
    if (mode === "regions") dialog.boxes = q.regions.map((r) => ({ page_idx: r.page_idx, bbox: [...r.bbox] }));
    else if (mode === "figures") dialog.boxes = q.figures.map((f) => {
      const exactCandidate = (q.figure_candidates || []).find(
        (candidate) => figureCandidateKey(candidate) === figureCandidateKey(f)
      );
      return {
        page_idx: f.page_idx, bbox: [...f.bbox], slot: f.slot,
        ...(f.label_offset ? { label_offset: { ...f.label_offset } } : {}),
        ...(hasFigureCandidateKey(q, f.candidate_key)
          ? { candidate_key: f.candidate_key }
          : exactCandidate ? { candidate_key: figureCandidateKey(exactCandidate) } : {})
      };
    });
    else dialog.boxes = [];
    const firstPage = q && q.regions.length ? q.regions[0].page_idx : state.paper.pages[0].page_idx;
    dialog.page = Number.isInteger(requestedPage)
      && state.paper.pages.some((page) => page.page_idx === requestedPage) ? requestedPage : firstPage;
    dialog.scrolled = false;
    dialog.selected = null;
    dialog.ignoredCandidates = new Set(Array.isArray(q?.figure_review?.ignored_candidates)
      ? q.figure_review.ignored_candidates.filter((key) => hasFigureCandidateKey(q, key)) : []);
    $("pageDialogTitle").textContent = mode === "regions" ? `调整第 ${q.number} 题的原卷范围`
      : mode === "figures" ? `第 ${q.number} 题的配图` : "手动补一道题";
    $("pageDialogHint").textContent = mode === "regions"
      ? "拖动框的边角改大小，拖框内部移动；选中框后也可用方向键移动、Delete 删除。在空白处拖出新框可补上跨栏/跨页的部分。保存后 AI 会按新范围重读并撤销旧审批。"
      : mode === "figures"
        ? "橙色实线框是已选配图；点标签可改归属，拖标签只移动标签。点击蓝色候选图或直接画新框后，再选择题干、选项或无关；未选择归属的新框不会保存。修改后需重新审核题卡。"
        : "在原卷上拖出这道题的范围（跨栏就拖两个框），填上题号后保存，AI 会自动读题。";
    $("numberField").hidden = mode !== "new";
    const groups = state.paper.question_groups || [];
    $("groupField").hidden = mode !== "new" || groups.length < 2;
    if (mode === "new") {
      $("groupSelect").replaceChildren(
        el("option", "", "自动（按所框页面判断）"),
        ...groups.map((group) => {
          const option = el("option", "", group.title || `第 ${group.sequence + 1} 组`);
          option.value = String(group.id);
          return option;
        })
      );
      $("groupSelect").value = "";
      if (groups.length > 1) {
        $("pageDialogHint").textContent += " 如果同一页里有多个题组，请在题号旁明确选择它属于哪一组。";
      }
    }
    $("numberInput").value = "";
    $("allPagesPicker").open = false;
    $("pageSearchInput").value = "";
    lens.classList.remove("on");
    renderPageTabs();
    renderStage();
    $("pageDialog").showModal();
  }

  function removeBox(index) {
    if (index === null || index === undefined || !dialog.boxes[index]) return;
    closeFigureSlotMenu({ cancelPending: false, rerender: false });
    dialog.boxes.splice(index, 1);
    dialog.selected = null;
    renderPageTabs();
    renderStage();
  }

  document.addEventListener("keydown", (event) => {
    if (!$("pageDialog").open || !["Delete", "Backspace"].includes(event.key)) return;
    if (["INPUT", "SELECT", "TEXTAREA"].includes(event.target.tagName)) return;
    if (dialog.selected === null || dialog.selected === undefined) return;
    event.preventDefault();
    removeBox(dialog.selected);
  });

  function dialogPageIndex() {
    return (state.paper?.pages || []).findIndex((page) => page.page_idx === dialog.page);
  }

  function goToDialogPage(pageIdx, { focusTab = false, closePicker = false } = {}) {
    if (!(state.paper?.pages || []).some((page) => page.page_idx === pageIdx)) return;
    closeFigureSlotMenu({ cancelPending: true, rerender: false });
    dialog.page = pageIdx;
    if (closePicker) $("allPagesPicker").open = false;
    renderPageTabs();
    renderStage();
    if (focusTab) requestAnimationFrame(() => $("pageTabs").querySelector('[aria-selected="true"]')?.focus());
  }

  function relatedDialogPages() {
    const q = dialog.question;
    const related = new Set([dialog.page]);
    (dialog.boxes || []).forEach((box) => related.add(box.page_idx));
    (q?.regions || []).forEach((region) => related.add(region.page_idx));
    if (dialog.mode === "figures") (q?.figure_candidates || []).forEach((candidate) => related.add(candidate.page_idx));
    return (state.paper?.pages || []).filter((page) => related.has(page.page_idx));
  }

  function dialogPageButton(page, { searchResult = false } = {}) {
    const active = page.page_idx === dialog.page;
    const boxCount = dialog.boxes.filter((box) => box.page_idx === page.page_idx).length;
    const candidateCount = dialog.mode === "figures"
      ? (dialog.question?.figure_candidates || []).filter((candidate) => candidate.page_idx === page.page_idx).length : 0;
    const suffix = boxCount ? ` · ${boxCount}框` : candidateCount ? ` · ${candidateCount}候选` : "";
    const tab = el("button", `page-tab${active ? " active" : ""}`, `第 ${page.page_idx + 1} 页${suffix}`);
    tab.type = "button";
    tab.setAttribute("role", searchResult ? "option" : "tab");
    tab.setAttribute(searchResult ? "aria-selected" : "aria-selected", String(active));
    tab.tabIndex = searchResult || active ? 0 : -1;
    tab.addEventListener("click", () => goToDialogPage(page.page_idx, { closePicker: searchResult }));
    return tab;
  }

  function renderPageSearchResults() {
    const query = String($("pageSearchInput").value || "").trim();
    const pages = state.paper?.pages || [];
    const matches = query
      ? pages.filter((page) => String(page.page_idx + 1).includes(query))
      : pages;
    $("pageSearchResults").replaceChildren(...matches.map((page) => dialogPageButton(page, { searchResult: true })));
  }

  function renderPageTabs() {
    const pages = state.paper?.pages || [];
    const index = dialogPageIndex();
    $("pagePrevious").disabled = index <= 0;
    $("pageNext").disabled = index < 0 || index >= pages.length - 1;
    $("pageNumberInput").min = pages.length ? String(Math.min(...pages.map((page) => page.page_idx + 1))) : "1";
    $("pageNumberInput").max = pages.length ? String(Math.max(...pages.map((page) => page.page_idx + 1))) : "1";
    $("pageNumberInput").value = String(dialog.page + 1);
    $("pageNumberTotal").textContent = `/ ${pages.length}`;

    const tabs = $("pageTabs");
    const related = relatedDialogPages();
    tabs.replaceChildren(el("span", "page-tabs-label", dialog.mode === "figures" ? "本题/候选页" : "本题相关页"),
      ...related.map((page) => dialogPageButton(page)));
    if ($("allPagesPicker").open) renderPageSearchResults();
  }

  function commitPageNumber() {
    const number = Number.parseInt($("pageNumberInput").value, 10);
    const pages = state.paper?.pages || [];
    if (!Number.isFinite(number) || !pages.length) { $("pageNumberInput").value = String(dialog.page + 1); return; }
    const exact = pages.find((page) => page.page_idx + 1 === number);
    if (exact) goToDialogPage(exact.page_idx);
    else {
      const closest = [...pages].sort((left, right) => Math.abs(left.page_idx + 1 - number) - Math.abs(right.page_idx + 1 - number))[0];
      goToDialogPage(closest.page_idx);
    }
  }

  $("pagePrevious").addEventListener("click", () => {
    const index = dialogPageIndex();
    if (index > 0) goToDialogPage(state.paper.pages[index - 1].page_idx);
  });
  $("pageNext").addEventListener("click", () => {
    const index = dialogPageIndex();
    if (index >= 0 && index < state.paper.pages.length - 1) goToDialogPage(state.paper.pages[index + 1].page_idx);
  });
  $("pageNumberInput").addEventListener("change", commitPageNumber);
  $("pageNumberInput").addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    commitPageNumber();
    $("pageNumberInput").select();
  });
  $("pageSearchInput").addEventListener("input", renderPageSearchResults);
  $("allPagesPicker").addEventListener("toggle", () => {
    if (!$("allPagesPicker").open) return;
    renderPageSearchResults();
    requestAnimationFrame(() => $("pageSearchInput").focus({ preventScroll: true }));
  });

  function pct(value) { return `${value / 10}%`; }

  function placeBox(node, bbox) {
    node.style.left = pct(bbox[0]);
    node.style.top = pct(bbox[1]);
    node.style.width = pct(bbox[2] - bbox[0]);
    node.style.height = pct(bbox[3] - bbox[1]);
  }

  function figureCandidateKey(figure) {
    return `${figure.page_idx}:${figure.bbox.map((value) => Math.round(Number(value) * 10) / 10).join(",")}`;
  }

  function hasFigureCandidateKey(question, key) {
    return typeof key === "string"
      && (question?.figure_candidates || []).some((candidate) => figureCandidateKey(candidate) === key);
  }

  function isKnownFigureCandidate(figure) {
    const key = figureCandidateKey(figure);
    return hasFigureCandidateKey(dialog.question, key);
  }

  function menuIsOpen() {
    const menu = $("figureSlotMenu");
    try { if (menu.matches(":popover-open")) return true; } catch { /* old Edge fallback */ }
    return !menu.hidden;
  }

  function closeFigureSlotMenu({ cancelPending = true, rerender = false } = {}) {
    const menu = $("figureSlotMenu");
    const hadPending = Boolean(dialog.pendingFigure);
    if (cancelPending) dialog.pendingFigure = null;
    dialog.slotTarget = null;
    dialog.slotAnchor = null;
    try { if (menu.matches(":popover-open")) menu.hidePopover(); } catch { /* old Edge fallback */ }
    menu.hidden = true;
    menu.style.left = "";
    menu.style.top = "";
    if (rerender && hadPending && $("pageDialog").open) renderStage();
  }

  function positionFigureSlotMenu(anchor) {
    const menu = $("figureSlotMenu");
    const rect = typeof anchor?.getBoundingClientRect === "function" ? anchor.getBoundingClientRect() : anchor;
    if (!rect) return;
    const viewportWidth = document.documentElement.clientWidth || window.innerWidth;
    const viewportHeight = document.documentElement.clientHeight || window.innerHeight;
    const menuRect = menu.getBoundingClientRect();
    const gap = 8;
    const left = Math.max(gap, Math.min(rect.left, viewportWidth - menuRect.width - gap));
    const below = rect.bottom + gap;
    const above = rect.top - menuRect.height - gap;
    const opensUp = below + menuRect.height > viewportHeight - gap && above >= gap;
    const top = opensUp ? above : Math.max(gap, Math.min(below, viewportHeight - menuRect.height - gap));
    menu.style.left = `${Math.round(left)}px`;
    menu.style.top = `${Math.round(top)}px`;
    menu.classList.toggle("opens-up", opensUp);
  }

  function openFigureSlotMenu(anchor, target) {
    closeFigureSlotMenu({ cancelPending: true, rerender: false });
    dialog.slotTarget = target;
    dialog.slotAnchor = anchor;
    dialog.pendingFigure = target.kind === "new" ? target : null;
    const menu = $("figureSlotMenu");
    const current = target.kind === "existing" ? dialog.boxes[target.index]?.slot : null;
    menu.querySelectorAll("[data-figure-slot]").forEach((item) => {
      item.setAttribute("aria-checked", String(item.dataset.figureSlot === current));
    });
    menu.hidden = false;
    try { if (typeof menu.showPopover === "function") menu.showPopover(); } catch { /* old Edge fallback */ }
    positionFigureSlotMenu(anchor);
    requestAnimationFrame(() => menu.querySelector(`[data-figure-slot="${current || "stem"}"]`)?.focus({ preventScroll: true }));
  }

  function chooseFigureSlot(slot) {
    const target = dialog.slotTarget;
    if (!target) return;
    if (target.kind === "existing") {
      const box = dialog.boxes[target.index];
      closeFigureSlotMenu({ cancelPending: false, rerender: false });
      if (!box) return;
      if (slot === "irrelevant") {
        const candidateKey = box.candidate_key
          || (isKnownFigureCandidate(box) ? figureCandidateKey(box) : null);
        if (candidateKey) dialog.ignoredCandidates.add(candidateKey);
        dialog.boxes.splice(target.index, 1);
        dialog.selected = null;
        renderPageTabs();
        toast("这张图已标记为无关，保存后不会再次作为候选图出现");
      } else {
        box.slot = slot;
        dialog.selected = target.index;
      }
    } else {
      const pending = target.box;
      closeFigureSlotMenu({ cancelPending: false, rerender: false });
      dialog.pendingFigure = null;
      if (slot === "irrelevant") {
        if (target.candidateKey) dialog.ignoredCandidates.add(target.candidateKey);
        toast("这张候选图已标记为无关，本次不会加入题卡");
      } else {
        dialog.boxes.push({
          ...pending, bbox: [...pending.bbox], slot,
          ...(target.candidateKey ? { candidate_key: target.candidateKey } : {})
        });
        dialog.selected = dialog.boxes.length - 1;
        renderPageTabs();
      }
    }
    renderStage();
  }

  function selectFigureBox(surface, index) {
    dialog.selected = index;
    surface.querySelectorAll(".edit-box.figure").forEach((item) => {
      const selected = Number(item.dataset.boxIndex) === index;
      item.classList.toggle("selected", selected);
      const label = item.querySelector(".box-label");
      if (label) label.textContent = selected ? label.dataset.fullLabel : label.dataset.shortLabel;
    });
    if (surface.isConnected) autoPlaceFigureLabels(surface);
  }

  function labelsOverlap(a, b) {
    return a.left < b.right + 4 && a.right + 4 > b.left && a.top < b.bottom + 4 && a.bottom + 4 > b.top;
  }

  function clampFigureLabel(tab, offset, surfaceRect) {
    let [x, y] = offset;
    tab.style.transform = `translate(${x}px, ${y}px)`;
    const rect = tab.getBoundingClientRect();
    if (rect.left < surfaceRect.left) x += surfaceRect.left - rect.left;
    if (rect.right > surfaceRect.right) x -= rect.right - surfaceRect.right;
    if (rect.top < surfaceRect.top) y += surfaceRect.top - rect.top;
    if (rect.bottom > surfaceRect.bottom) y -= rect.bottom - surfaceRect.bottom;
    const clamped = [Math.round(x), Math.round(y)];
    tab.style.transform = `translate(${clamped[0]}px, ${clamped[1]}px)`;
    return clamped;
  }

  function autoPlaceFigureLabels(surface) {
    const occupied = [];
    const tabs = [...surface.querySelectorAll(".edit-box.figure .box-tab")];
    const manualTabs = tabs.filter((tab) => tab.dataset.manualLabel === "true");
    const automaticTabs = tabs.filter((tab) => tab.dataset.manualLabel !== "true");
    const surfaceRect = surface.getBoundingClientRect();
    manualTabs.forEach((tab) => {
      const box = dialog.boxes[Number(tab.closest(".edit-box")?.dataset.boxIndex)];
      if (!box) return;
      const [x, y] = clampFigureLabel(
        tab, [Number(box.label_offset?.x) || 0, Number(box.label_offset?.y) || 0], surfaceRect
      );
      box.label_offset = { x, y };
      occupied.push(tab.getBoundingClientRect());
    });
    automaticTabs.forEach((tab) => {
      const box = dialog.boxes[Number(tab.closest(".edit-box")?.dataset.boxIndex)];
      if (!box) return;
      const candidates = [[0, 0], [0, -28], [0, 28], [0, -56], [0, 56], [48, 0], [-48, 0]];
      let chosen = candidates[0];
      for (const offset of candidates) {
        const positioned = clampFigureLabel(tab, offset, surfaceRect);
        chosen = positioned;
        if (!occupied.some((other) => labelsOverlap(tab.getBoundingClientRect(), other))) { chosen = offset; break; }
      }
      chosen = clampFigureLabel(tab, chosen, surfaceRect);
      tab.dataset.autoX = String(chosen[0]);
      tab.dataset.autoY = String(chosen[1]);
      occupied.push(tab.getBoundingClientRect());
    });
    if (menuIsOpen() && dialog.slotAnchor?.isConnected) positionFigureSlotMenu(dialog.slotAnchor);
  }

  function startLabelDrag(event, surface, index, tab, box) {
    if (event.button !== 0) return;
    event.stopPropagation();
    selectFigureBox(surface, index);
    const start = { x: event.clientX, y: event.clientY };
    const origin = box.label_offset || {
      x: Number(tab.dataset.autoX || 0), y: Number(tab.dataset.autoY || 0)
    };
    let moved = false;
    const move = (moveEvent) => {
      const dx = moveEvent.clientX - start.x;
      const dy = moveEvent.clientY - start.y;
      if (!moved && Math.hypot(dx, dy) < 4) return;
      moved = true;
      box.label_offset = { x: Math.round(origin.x + dx), y: Math.round(origin.y + dy) };
      tab.dataset.manualLabel = "true";
      tab.style.transform = `translate(${box.label_offset.x}px, ${box.label_offset.y}px)`;
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      if (moved) {
        box.suppressLabelClick = true;
        autoPlaceFigureLabels(surface);
      }
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }

  $("figureSlotMenu").querySelectorAll("[data-figure-slot]").forEach((item) => {
    item.addEventListener("click", () => chooseFigureSlot(item.dataset.figureSlot));
  });
  $("figureSlotMenu").querySelector("[data-figure-slot-cancel]").addEventListener("click", () => {
    closeFigureSlotMenu({ cancelPending: true, rerender: true });
  });
  $("figureSlotMenu").addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    event.preventDefault();
    closeFigureSlotMenu({ cancelPending: true, rerender: true });
  });
  $("figureSlotMenu").addEventListener("toggle", (event) => {
    if (event.newState !== "closed" || !dialog.slotTarget) return;
    dialog.slotTarget = null;
    dialog.pendingFigure = null;
    $("figureSlotMenu").hidden = true;
    if ($("pageDialog").open) renderStage();
  });
  document.addEventListener("pointerdown", (event) => {
    if (!menuIsOpen() || $("figureSlotMenu").contains(event.target)) return;
    if (event.target.closest?.(".box-label, .candidate, #pageDialogSave")) return;
    closeFigureSlotMenu({ cancelPending: true, rerender: true });
  });
  window.addEventListener("resize", () => {
    const surface = $("pageStage").querySelector(".stage-surface");
    if ($("pageDialog").open && dialog.mode === "figures" && surface) autoPlaceFigureLabels(surface);
    else if (menuIsOpen() && dialog.slotAnchor?.isConnected) positionFigureSlotMenu(dialog.slotAnchor);
  });
  $("pageStage").addEventListener("scroll", () => {
    if (menuIsOpen() && dialog.slotAnchor?.isConnected) positionFigureSlotMenu(dialog.slotAnchor);
  }, { passive: true });
  $("pageDialog").addEventListener("close", () => {
    closeFigureSlotMenu({ cancelPending: true, rerender: false });
  });

  function renderStage() {
    const stage = $("pageStage");
    stage.replaceChildren();
    const page = pageInfo(dialog.page);
    const surface = el("div", "stage-surface");
    surface.style.aspectRatio = `${page.width} / ${page.height}`;
    const image = el("img");
    image.src = previewUrl(state.paperId, dialog.page);
    image.alt = `原卷第 ${dialog.page + 1} 页`;
    image.draggable = false;
    surface.append(image);
    const q = dialog.question;

    // 参照：其他题的范围（淡灰），配图模式下还有本题范围和候选图。
    state.questions.forEach((other) => {
      const own = q && other.id === q.id;
      if (dialog.mode === "regions" && own) return;
      if (dialog.mode === "figures" && !own) return;
      other.regions.filter((r) => r.page_idx === dialog.page).forEach((region) => {
        const ghost = el("span", own ? "ghost own" : "ghost");
        placeBox(ghost, region.bbox);
        if (!own) ghost.append(el("span", "ghost-label", `第 ${other.number} 题`));
        surface.append(ghost);
      });
    });
    if (dialog.mode === "figures") {
      (q.figure_candidates || []).filter((c) => c.page_idx === dialog.page).forEach((candidate) => {
        const candidateKey = figureCandidateKey(candidate);
        if (dialog.ignoredCandidates.has(candidateKey)) return;
        const used = dialog.boxes.some((box) => box.candidate_key === candidateKey
          || (box.page_idx === candidate.page_idx
            && box.bbox.every((v, i) => Math.abs(v - candidate.bbox[i]) < 0.5)));
        if (used) return;
        const option = el("button", "candidate");
        option.type = "button";
        option.title = "候选图：点一下加为配图";
        option.append(el("span", "candidate-label", "＋ 加为配图"));
        placeBox(option, candidate.bbox);
        option.addEventListener("click", (event) => {
          event.stopPropagation();
          if (menuIsOpen()) {
            closeFigureSlotMenu({ cancelPending: true, rerender: true });
            return;
          }
          openFigureSlotMenu(option, {
            kind: "new", candidateKey,
            box: { page_idx: candidate.page_idx, bbox: [...candidate.bbox] }
          });
        });
        surface.append(option);
      });
    }

    dialog.boxes.forEach((box, index) => {
      if (box.page_idx !== dialog.page) return;
      const node = el("div", `edit-box ${dialog.mode === "figures" ? "figure" : "region"}`);
      node.dataset.boxIndex = String(index);
      placeBox(node, box.bbox);
      node.tabIndex = 0;
      node.setAttribute("role", "group");
      node.setAttribute("aria-label", `${dialog.mode === "figures" ? (SLOT_NAMES[box.slot] || box.slot) + "配图" : `第 ${index + 1} 段范围`}；方向键移动，Delete 删除`);
      const shortLabel = box.slot === "stem" ? "题" : box.slot;
      const fullLabel = SLOT_NAMES[box.slot] || box.slot;
      const label = el("button", "box-label", dialog.mode === "figures"
        ? (dialog.selected === index ? fullLabel : shortLabel) : `第 ${index + 1} 段`);
      label.type = "button";
      if (dialog.mode === "figures") {
        label.dataset.shortLabel = shortLabel;
        label.dataset.fullLabel = fullLabel;
        label.title = `${fullLabel}的配图。点击选择明确归属；拖动只移动标签，不改变裁剪范围`;
        label.addEventListener("pointerdown", (event) => startLabelDrag(event, surface, index, tab, box));
        label.addEventListener("click", (event) => {
          event.stopPropagation();
          if (box.suppressLabelClick) { delete box.suppressLabelClick; return; }
          if (dialog.pendingFigure) {
            closeFigureSlotMenu({ cancelPending: true, rerender: true });
            return;
          }
          openFigureSlotMenu(label, { kind: "existing", index });
        });
      }
      const remove = el("button", "box-remove", "×");
      remove.type = "button";
      remove.title = "删除这个框（也可以先点选框，再按 Delete 键）";
      // 按下就删：不等 click，避免手指/触控板轻微移动导致 click 丢失。
      remove.addEventListener("pointerdown", (event) => {
        event.preventDefault();
        event.stopPropagation();
        removeBox(index);
      });
      remove.addEventListener("click", (event) => event.stopPropagation());
      remove.addEventListener("keydown", (event) => {
        if (!['Enter', ' '].includes(event.key)) return;
        event.preventDefault();
        event.stopPropagation();
        removeBox(index);
      });
      if (dialog.selected === index) node.classList.add("selected");
      const tab = el("div", "box-tab");
      tab.append(label, remove);
      if (dialog.mode === "figures" && box.label_offset) {
        tab.dataset.manualLabel = "true";
        tab.style.transform = `translate(${Number(box.label_offset.x) || 0}px, ${Number(box.label_offset.y) || 0}px)`;
      }
      node.append(tab);
      ["nw", "ne", "sw", "se", "n", "s", "w", "e"].forEach((handle) => {
        const grip = el("span", `grip grip-${handle}`);
        grip.dataset.handle = handle;
        node.append(grip);
      });
      node.addEventListener("pointerdown", (event) => {
        if (dialog.mode === "figures") selectFigureBox(surface, index);
        else dialog.selected = index;
        startDrag(event, surface, index, event.target.dataset.handle || "move");
      });
      node.addEventListener("focus", () => {
        if (dialog.mode === "figures") selectFigureBox(surface, index);
        else {
          dialog.selected = index;
          surface.querySelectorAll(".edit-box.selected").forEach((item) => item.classList.remove("selected"));
          node.classList.add("selected");
        }
      });
      node.addEventListener("keydown", (event) => {
        if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) return;
        event.preventDefault();
        event.stopPropagation();
        const step = event.shiftKey ? 20 : 5;
        const dx = event.key === "ArrowLeft" ? -step : event.key === "ArrowRight" ? step : 0;
        const dy = event.key === "ArrowUp" ? -step : event.key === "ArrowDown" ? step : 0;
        const [x0, y0, x1, y1] = box.bbox;
        const moveX = Math.max(-x0, Math.min(1000 - x1, dx));
        const moveY = Math.max(-y0, Math.min(1000 - y1, dy));
        box.bbox = [x0 + moveX, y0 + moveY, x1 + moveX, y1 + moveY].map((value) => Math.round(value * 10) / 10);
        placeBox(node, box.bbox);
      });
      surface.append(node);
    });
    if (dialog.mode === "figures") requestAnimationFrame(() => {
      if (surface.isConnected) autoPlaceFigureLabels(surface);
    });
    surface.addEventListener("pointerdown", (event) => {
      if (event.target !== surface && event.target !== image && !event.target.classList.contains("ghost")) return;
      if (menuIsOpen()) {
        closeFigureSlotMenu({ cancelPending: true, rerender: true });
        return;
      }
      startDrag(event, surface, null, "create");
    });
    stage.append(surface);
    if (!dialog.scrolled) {
      dialog.scrolled = true;
      image.addEventListener("load", () => {
        surface.querySelector(".edit-box, .ghost.own")?.scrollIntoView({ block: "center" });
      }, { once: true });
    }
  }

  function pointFrom(event, surface) {
    const rect = surface.getBoundingClientRect();
    return [
      Math.min(1000, Math.max(0, ((event.clientX - rect.left) / rect.width) * 1000)),
      Math.min(1000, Math.max(0, ((event.clientY - rect.top) / rect.height) * 1000))
    ];
  }

  function startDrag(event, surface, index, handle) {
    if (event.button !== 0) return;
    event.preventDefault();
    const start = pointFrom(event, surface);
    const target = index === null ? null : event.currentTarget;
    const box = index === null ? null : dialog.boxes[index];
    const original = box ? [...box.bbox] : null;
    let preview = null;
    if (handle === "create") {
      preview = el("div", `edit-box ${dialog.mode === "figures" ? "figure" : "region"} drawing`);
      surface.append(preview);
    }
    const move = (moveEvent) => {
      const [x, y] = pointFrom(moveEvent, surface);
      const dx = x - start[0];
      const dy = y - start[1];
      if (handle === "create") {
        placeBox(preview, [Math.min(x, start[0]), Math.min(y, start[1]), Math.max(x, start[0]), Math.max(y, start[1])]);
        return;
      }
      let [x0, y0, x1, y1] = original;
      if (handle === "move") {
        const w = x1 - x0;
        const h = y1 - y0;
        x0 = Math.min(1000 - w, Math.max(0, x0 + dx)); y0 = Math.min(1000 - h, Math.max(0, y0 + dy));
        x1 = x0 + w; y1 = y0 + h;
      } else {
        if (handle.includes("w")) x0 = Math.min(x1 - 5, original[0] + dx);
        if (handle.includes("e")) x1 = Math.max(x0 + 5, original[2] + dx);
        if (handle.includes("n")) y0 = Math.min(y1 - 5, original[1] + dy);
        if (handle.includes("s")) y1 = Math.max(y0 + 5, original[3] + dy);
      }
      box.bbox = [x0, y0, x1, y1].map((v) => Math.round(Math.min(1000, Math.max(0, v)) * 10) / 10);
      placeBox(target, box.bbox);
    };
    const up = (upEvent) => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      if (handle === "create") {
        const [x, y] = pointFrom(upEvent, surface);
        const bbox = [Math.min(x, start[0]), Math.min(y, start[1]), Math.max(x, start[0]), Math.max(y, start[1])].map((v) => Math.round(v * 10) / 10);
        if (bbox[2] - bbox[0] > 8 && bbox[3] - bbox[1] > 8) {
          if (dialog.mode === "figures") {
            preview.classList.remove("drawing");
            preview.classList.add("pending-assignment");
            openFigureSlotMenu(preview, { kind: "new", box: { page_idx: dialog.page, bbox } });
            return;
          }
          dialog.boxes.push({ page_idx: dialog.page, bbox });
          renderPageTabs();
        }
        preview.remove();
      }
      renderStage();
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }

  function readingOrder(boxes) {
    return [...boxes].sort((a, b) => {
      if (a.page_idx !== b.page_idx) return a.page_idx - b.page_idx;
      if (Math.abs(a.bbox[0] - b.bbox[0]) > 150) return a.bbox[0] - b.bbox[0];
      return a.bbox[1] - b.bbox[1];
    });
  }

  $("pageDialogSave").addEventListener("click", async () => {
    const q = dialog.question;
    try {
      if (dialog.mode === "regions") {
        if (!dialog.boxes.length) { toast("至少要有一个框", "error"); return; }
        const data = await api(`/api/questions/${q.id}/regions`, { method: "POST", body: { regions: readingOrder(dialog.boxes) } });
        applyQuestion(data);
        toast(q.approved ? `第 ${q.number} 题范围已更新，旧审批已撤销，AI 正在重读` : `第 ${q.number} 题范围已更新，AI 正在重读`);
        refreshPaper();
      } else if (dialog.mode === "figures") {
        if (dialog.pendingFigure || dialog.slotTarget?.kind === "new") {
          toast("请先选择新配图属于题干、某个选项或无关；也可以点取消撤销新框", "error");
          $("figureSlotMenu").querySelector("[data-figure-slot]")?.focus({ preventScroll: true });
          return;
        }
        const figures = dialog.boxes.map((box) => ({
          page_idx: box.page_idx, bbox: [...box.bbox], slot: box.slot,
          ...(box.label_offset ? { label_offset: { ...box.label_offset } } : {}),
          ...(box.candidate_key ? { candidate_key: box.candidate_key } : {})
        }));
        if (!figures.length) {
          const confirmed = await confirmDialog({
            title: `移除第 ${q.number} 题的全部配图并确认无图？`,
            text: "只有对照原卷后确认本题确实没有正式配图，才继续。保存后仍需再次标记通过；原来的入库版本不会被覆盖。",
            ok: "移除并确认无图"
          });
          if (!confirmed) return;
        }
        const data = await api(`/api/questions/${q.id}/figures`, {
          method: "POST",
          body: { figures, ignored_candidates: [...dialog.ignoredCandidates] }
        });
        applyQuestion(data);
        toast(q.approved ? `第 ${q.number} 题配图已保存，旧审批已撤销，请重新审核` : `第 ${q.number} 题配图已保存，请审核题卡`);
      } else {
        const number = Number($("numberInput").value);
        if (!Number.isInteger(number) || number < 1) { toast("请填写题号", "error"); return; }
        if (!dialog.boxes.length) { toast("请先在原卷上拖出这道题的范围", "error"); return; }
        const selectedGroup = $("groupSelect").value;
        const body = { number, regions: readingOrder(dialog.boxes) };
        if (selectedGroup) body.group_id = Number(selectedGroup);
        const data = await api(`/api/papers/${state.paperId}/questions`, { method: "POST", body });
        applyQuestion(data);
        toast(`已添加第 ${number} 题，AI 正在读题`);
        refreshPaper();
      }
      closeFigureSlotMenu({ cancelPending: true, rerender: false });
      $("pageDialog").close();
    } catch (error) { toast(error.message, "error"); }
  });

  $("addQuestion").addEventListener("click", () => { $("toolsMenu").open = false; openPageDialog("new"); });

  const resegmentPreview = { paperId: null, report: null, loading: false, applying: false };

  function setResegmentClosingDisabled(disabled) {
    $("resegmentPreviewDialog").querySelectorAll("[data-close]").forEach((button) => { button.disabled = disabled; });
  }

  function resetResegmentPreview(paperId) {
    resegmentPreview.paperId = paperId;
    resegmentPreview.report = null;
    resegmentPreview.loading = true;
    resegmentPreview.applying = false;
    $("resegmentPreviewState").className = "resegment-preview-state loading";
    $("resegmentPreviewState").textContent = "正在按最新规则生成只读预演……";
    $("resegmentPreviewSummary").replaceChildren();
    $("resegmentPreviewDetails").replaceChildren();
    $("resegmentAcknowledgeRow").hidden = true;
    $("resegmentAcknowledge").checked = false;
    $("resegmentAcknowledge").disabled = false;
    $("resegmentApply").disabled = true;
    $("resegmentApply").textContent = "确认并应用重新切题";
    $("resegmentPreviewResult").textContent = "";
    $("resegmentPreviewResult").className = "settings-save-result";
    setResegmentClosingDisabled(false);
  }

  function resegmentSummaryCard(category) {
    const card = el("div", `resegment-summary-card ${category.tone}`);
    card.append(el("strong", "", String(category.count)), el("span", "", category.label), el("small", "", category.detail));
    return card;
  }

  function resegmentItemRow(item) {
    const row = el("li");
    row.append(el("strong", "", QBResegment.itemTitle(item)));
    if (item?.reason) row.append(el("span", "", String(item.reason)));
    return row;
  }

  function renderResegmentPreview(report) {
    const normalized = QBResegment.normalizeReport(report);
    resegmentPreview.report = report;
    resegmentPreview.loading = false;
    const stateNode = $("resegmentPreviewState");
    stateNode.className = "resegment-preview-state";
    stateNode.textContent = normalized.readOnly
      ? `预演完成：未修改任何题卡${normalized.modelCalls === 0 ? "，未调用识读模型" : ""}。异常超长是风险标记，可能与新增或范围变化重复。`
      : "服务端没有确认这是一份只读预演，已禁止应用。";
    $("resegmentPreviewSummary").replaceChildren(...normalized.categories.map(resegmentSummaryCard));

    const detailNodes = normalized.categories.filter((category) => category.count > 0).map((category) => {
      const section = el("details", `resegment-category ${category.tone}`);
      if (["suspected_excluded", "protected_unmatched", "too_long"].includes(category.key)) section.open = true;
      const summary = el("summary");
      summary.append(document.createTextNode(category.label), el("span", "resegment-category-count", String(category.count)));
      const list = el("ul", "resegment-item-list");
      category.items.slice(0, 40).forEach((item) => list.append(resegmentItemRow(item)));
      if (category.items.length > 40) {
        const rest = el("li");
        rest.append(el("span", "", `另有 ${category.items.length - 40} 项未在此展开；摘要数量已包含它们。`));
        list.append(rest);
      }
      section.append(summary, list);
      return section;
    });
    if (normalized.notes.length) {
      const notes = el("div", "resegment-notes");
      notes.append(el("strong", "", "程序说明："), document.createTextNode(normalized.notes.slice(0, 8).join("；")));
      if (normalized.notes.length > 8) notes.append(document.createTextNode(`；另有 ${normalized.notes.length - 8} 条`));
      detailNodes.push(notes);
    }
    $("resegmentPreviewDetails").replaceChildren(...detailNodes);
    $("resegmentAcknowledgeRow").hidden = !normalized.readOnly;
    $("resegmentAcknowledge").checked = false;
    $("resegmentApply").disabled = true;
  }

  function showResegmentBlocked(error) {
    resegmentPreview.loading = false;
    resegmentPreview.report = null;
    const stateNode = $("resegmentPreviewState");
    stateNode.className = "resegment-preview-state blocked";
    stateNode.textContent = `现在不能重新切题：${error.message || error}`;
    $("resegmentPreviewSummary").replaceChildren();
    $("resegmentPreviewDetails").replaceChildren();
    $("resegmentAcknowledgeRow").hidden = true;
    $("resegmentApply").disabled = true;
  }

  async function resegmentPaper() {
    $("toolsMenu").open = false;
    const paperId = state.paperId;
    if (!paperId) return;
    resetResegmentPreview(paperId);
    const dialog = $("resegmentPreviewDialog");
    if (!dialog.open) dialog.showModal();
    try {
      const data = await api(`/api/papers/${paperId}/resegment/preview`, { method: "POST", body: {} });
      if (resegmentPreview.paperId !== paperId || !dialog.open) return;
      renderResegmentPreview(data.report);
    } catch (error) {
      if (resegmentPreview.paperId === paperId && dialog.open) showResegmentBlocked(error);
    }
  }

  $("resegment").addEventListener("click", resegmentPaper);
  $("resegmentAcknowledge").addEventListener("change", () => {
    $("resegmentApply").disabled = !resegmentPreview.report || !$("resegmentAcknowledge").checked || resegmentPreview.applying;
  });
  $("resegmentApply").addEventListener("click", async () => {
    if (!resegmentPreview.report || !$("resegmentAcknowledge").checked || resegmentPreview.applying) return;
    const paperId = resegmentPreview.paperId;
    resegmentPreview.applying = true;
    setResegmentClosingDisabled(true);
    $("resegmentApply").disabled = true;
    $("resegmentApply").textContent = "正在应用……";
    $("resegmentAcknowledge").disabled = true;
    $("resegmentPreviewResult").textContent = "正在请求服务端再次检查并启动重新切题……";
    $("resegmentPreviewResult").className = "settings-save-result";
    try {
      await api(`/api/papers/${paperId}/resegment`, { method: "POST", body: {} });
      $("resegmentPreviewDialog").close();
      if (state.paperId === paperId) state.rendered.clear();
      toast("已按预演结果开始重新切题");
      refreshPaper();
      loadPapers();
    } catch (error) {
      resegmentPreview.applying = false;
      setResegmentClosingDisabled(false);
      $("resegmentAcknowledge").disabled = false;
      $("resegmentApply").disabled = !$("resegmentAcknowledge").checked;
      $("resegmentApply").textContent = "确认并应用重新切题";
      $("resegmentPreviewResult").textContent = `未能应用：${error.message}`;
      $("resegmentPreviewResult").className = "settings-save-result error";
    }
  });
  $("resegmentPreviewDialog").addEventListener("cancel", (event) => {
    if (resegmentPreview.applying) event.preventDefault();
  });
  $("resegmentPreviewDialog").addEventListener("click", (event) => {
    if (resegmentPreview.applying && event.target === $("resegmentPreviewDialog")) event.stopImmediatePropagation();
  });

  // ---------------------------------------------------------------- 上传与 M3 导入

  // 照片（一张或几张）先弹出确认框，合成一份试卷；PDF、Word 一份一份直接上传。
  const MAX_PHOTOS = 30;
  const photoUpload = { files: [], urls: [] };
  const pasteUpload = { batch: null, resolve: null };

  function selectedMaterialType() {
    return document.querySelector('input[name="materialType"]:checked')?.value === "book" ? "book" : "exam";
  }

  function materialTypeLabel() {
    return selectedMaterialType() === "book" ? "一本书 / 讲义" : "一份试卷";
  }

  async function sendUpload(form, label) {
    toast(`正在上传 ${label}…`);
    const data = await api("/api/papers", { method: "POST", form });
    if (data.duplicate) toast("这份试卷之前上传过，已为你打开");
    return data.paper;
  }

  async function handleFiles(fileList) {
    const routed = QBUpload.routeFiles(fileList);
    if (!routed.files.length) return;
    if (!routed.accepted.length) {
      toast("只支持 PDF、DOCX、JPG、PNG 和 WEBP 文件", "error");
      return;
    }
    if (!state.status?.upload_enabled) { toast($("uploadNote").textContent || "暂时不能上传", "error"); return; }
    if (routed.unsupported.length) {
      toast(`已跳过 ${routed.unsupported.length} 个不支持的文件`, "error");
    }
    let acknowledged = false;
    try { acknowledged = Boolean(sessionStorage.getItem("qb-cloud-upload-ack")); } catch { /* 无存储时每次都提示 */ }
    if (!acknowledged) {
      const accepted = await confirmDialog({
        title: "隐私提示",
        text: "上传新卷会把整份原卷发送给 MinerU，切出的题目截图还会发送给 MiniMax；配置了硅基流动时也会发送给硅基流动。系统不会先擦除姓名、手写或批改痕迹。\n\n请确认你有权按此方式处理这些卷面，再继续上传。",
        ok: "我已确认，继续上传"
      });
      if (!accepted) return;
      try { sessionStorage.setItem("qb-cloud-upload-ack", "1"); } catch { /* 无存储时每次都提示 */ }
    }
    const pictures = routed.pictures;
    const others = routed.documents;
    let last = null;
    for (const file of others) {
      const form = new FormData();
      form.append("file", file);
      form.append("material_type", selectedMaterialType());
      try { last = await sendUpload(form, file.name); } catch (error) { toast(`${file.name}：${error.message}`, "error"); }
    }
    if (last) { await loadPapers(); selectPaper(last.id); }
    if (pictures.length > MAX_PHOTOS) toast(`一份试卷最多 ${MAX_PHOTOS} 张照片，这次选了 ${pictures.length} 张`, "error");
    else if (pictures.length) openPhotoDialog(pictures);
  }

  function fileSizeLabel(bytes) {
    if (!Number.isFinite(bytes) || bytes < 0) return "大小未知";
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${Math.max(0.1, bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }

  function openPasteDialog(batch) {
    pasteUpload.batch = batch;
    const counts = new Map();
    batch.items.filter((item) => item.supported).forEach((item) => {
      counts.set(item.label, (counts.get(item.label) || 0) + 1);
    });
    const kinds = [...counts].map(([label, count]) => `${label} ${count}`).join("、");
    const skipped = batch.unsupported.length ? `；${batch.unsupported.length} 个不支持的文件不会上传` : "";
    $("pasteTitle").textContent = `粘贴 ${batch.items.length} 个文件`;
    $("pasteSummary").textContent = `可上传 ${batch.accepted.length} 个${kinds ? `（${kinds}）` : ""}${skipped}`;
    $("pasteNote").textContent = `当前按“${materialTypeLabel()}”上传。请核对文件和顺序；PDF、Word 会分别建立任务，照片会保持下列顺序进入照片确认。`;
    $("pasteUpload").textContent = `继续上传（${batch.accepted.length}）`;
    $("pasteUpload").disabled = !batch.accepted.length;
    $("pasteList").replaceChildren(...batch.items.map((item) => {
      const row = el("li", `paste-item${item.supported ? "" : " unsupported"}`);
      const details = el("span", "paste-file");
      details.append(el("span", "paste-name", item.file.name || "未命名文件"), el("span", "paste-size", fileSizeLabel(item.file.size)));
      row.append(details, el("span", "paste-kind", item.label));
      return row;
    }));
    $("pasteDialog").showModal();
    return new Promise((resolve) => { pasteUpload.resolve = resolve; });
  }

  $("pasteUpload").addEventListener("click", () => {
    const resolve = pasteUpload.resolve;
    pasteUpload.resolve = null;
    $("pasteDialog").close();
    resolve?.(true);
  });

  $("pasteDialog").addEventListener("close", () => {
    const resolve = pasteUpload.resolve;
    pasteUpload.resolve = null;
    pasteUpload.batch = null;
    $("pasteList").replaceChildren();
    resolve?.(false);
  });

  function openPhotoDialog(files) {
    photoUpload.urls.forEach((url) => URL.revokeObjectURL(url));
    photoUpload.files = files;
    photoUpload.urls = files.map((file) => URL.createObjectURL(file));
    $("photoHint").innerHTML = selectedMaterialType() === "book"
      ? "这些照片会合成<strong>一本书 / 讲义</strong>。重复题号会保留，后续按章节或练习分组。"
      : "这些照片会合成<strong>一份试卷</strong>。页序不用管，读完后会按卷面上印的题号自动排好。";
    $("photoTitle").textContent = files.length > 1 ? `上传 ${files.length} 张照片` : "上传 1 张照片";
    $("photoUpload").textContent = files.length > 1 ? `上传（${files.length} 张合成一份试卷）` : "上传";
    $("photoUpload").disabled = false;
    $("photoList").replaceChildren(...files.map((file, index) => {
      const item = el("li", "photo-item");
      const image = el("img");
      image.src = photoUpload.urls[index];
      image.alt = file.name;
      item.append(image, el("span", "photo-name", file.name));
      return item;
    }));
    if (!$("photoDialog").open) $("photoDialog").showModal();
  }

  $("photoUpload").addEventListener("click", async () => {
    const files = photoUpload.files;
    if (!files.length) return;
    const form = new FormData();
    files.forEach((file) => form.append("file", file));
    form.append("enhance", $("photoEnhance").checked ? "1" : "0");
    form.append("material_type", selectedMaterialType());
    $("photoUpload").disabled = true;
    try {
      const paper = await sendUpload(form, files.length > 1 ? `${files.length} 张照片` : files[0].name);
      $("photoDialog").close();
      await loadPapers();
      selectPaper(paper.id);
    } catch (error) {
      toast(error.message, "error");
      $("photoUpload").disabled = false;
    }
  });
  $("photoDialog").addEventListener("close", () => {
    photoUpload.urls.forEach((url) => URL.revokeObjectURL(url));
    photoUpload.files = [];
    photoUpload.urls = [];
    $("photoList").replaceChildren();
  });

  $("fileInput").addEventListener("change", (event) => {
    handleFiles(event.target.files);
    event.target.value = "";
  });
  const zone = $("dropZone");
  ["dragenter", "dragover"].forEach((name) => zone.addEventListener(name, (event) => { event.preventDefault(); zone.classList.add("over"); }));
  ["dragleave", "drop"].forEach((name) => zone.addEventListener(name, () => zone.classList.remove("over")));
  zone.addEventListener("drop", (event) => {
    event.preventDefault();
    handleFiles(event.dataTransfer.files);
  });
  // 拖到页面其他地方也不要让浏览器直接打开文件
  ["dragover", "drop"].forEach((name) => window.addEventListener(name, (event) => {
    if (!event.target.closest?.("#dropZone")) event.preventDefault();
  }));

  document.addEventListener("paste", async (event) => {
    if (!QBUpload.shouldInterceptPaste(event.target, event.clipboardData)) return;
    event.preventDefault();
    if ($("pasteDialog").open) {
      toast("请先确认或取消当前这批文件", "error");
      return;
    }
    const batch = QBUpload.buildClipboardBatch(event.clipboardData.files);
    if (!batch.accepted.length) {
      toast("剪贴板中的文件不支持；请选择 PDF、DOCX、JPG、PNG 或 WEBP", "error");
      return;
    }
    await QBUpload.runConfirmedPaste(batch.accepted, () => openPasteDialog(batch), (files) => handleFiles(files));
  });

  // ---------------------------------------------------------------- 照片卷：调整页序

  const pageOrder = { order: [] };
  const splitPlan = { paperId: null, groups: [] };

  function suggestedSplitGroups(paper) {
    if (!paper) return [];
    const conflict = typeof paper.structure_conflict === "object" ? paper.structure_conflict : {};
    const raw = paper.suggested_groups || conflict.suggested_groups || conflict.groups || [];
    if (!Array.isArray(raw)) return [];
    const groups = raw.map((group) => Array.isArray(group) ? group : group?.pages)
      .filter(Array.isArray)
      .map((group) => [...new Set(group.filter((page) => Number.isInteger(page) && page >= 0))])
      .filter((group) => group.length);
    const flattened = groups.flat();
    const expected = Array.from({ length: (paper.pages || []).length }, (_value, index) => index);
    return flattened.length === expected.length
      && [...flattened].sort((a, b) => a - b).every((page, index) => page === expected[index])
      ? groups : [];
  }

  async function confirmStructure() {
    const paper = state.paper;
    if (!paper || paper.status !== "needs_grouping") return;
    const ok = await confirmDialog({
      title: "确认这些页面属于同一份资料？",
      text: "程序会继续处理，并把重新开始的题号放进独立题组；同号题不会互相覆盖。若页面其实来自不同试卷，请改用“拆分任务”。",
      ok: "确认并继续"
    });
    if (!ok) return;
    try {
      const data = await api(`/api/papers/${paper.id}/confirm-structure`, { method: "POST", body: {} });
      toast(data.message || "已确认，正在继续处理。", "success");
      await selectPaper(paper.id);
    } catch (error) { toast(error.message, "error"); }
  }

  function openSplitDialog() {
    const paper = state.paper;
    const groups = suggestedSplitGroups(paper);
    if (!paper || groups.length < 2) {
      toast("暂时没有可靠的拆分建议，请先调整页序", "error");
      return;
    }
    splitPlan.paperId = paper.id;
    splitPlan.groups = groups.map((group) => [...group]);
    const names = paper.photos?.names || [];
    $("splitTitle").textContent = `把“${paperDisplayName(paper)}”拆成 ${groups.length} 份`;
    $("splitConfirm").textContent = `确认拆成 ${groups.length} 份`;
    $("splitConfirm").disabled = false;
    $("splitGroups").replaceChildren(...groups.map((group, groupIndex) => {
      const section = el("section", "split-group");
      section.append(el("h4", "", `第 ${groupIndex + 1} 份 · ${group.length} 页`));
      const pages = el("div", "split-pages");
      group.forEach((page) => {
        const item = el("div", "split-page");
        const image = el("img");
        image.src = previewUrl(paper.id, page);
        image.alt = `原资料第 ${page + 1} 页`;
        image.loading = "lazy";
        item.append(image, el("span", "", names[page] || `原第 ${page + 1} 页`));
        pages.append(item);
      });
      section.append(pages);
      return section;
    }));
    $("splitDialog").showModal();
  }

  $("splitConfirm").addEventListener("click", async () => {
    if (!splitPlan.paperId || splitPlan.groups.length < 2) return;
    const save = $("splitConfirm");
    const groupCount = splitPlan.groups.length;
    save.disabled = true;
    try {
      const data = await api(`/api/papers/${splitPlan.paperId}/split`, {
        method: "POST", body: { groups: splitPlan.groups }
      });
      if ($("splitDialog").open) $("splitDialog").close();
      const targetId = data.papers?.[0]?.id || data.paper?.id || null;
      clearPaperSelection();
      await loadPapers();
      const target = targetId && state.papers.find((paper) => paper.id === targetId);
      if (target) await selectPaper(target.id);
      else if (state.papers.length) await selectPaper(state.papers[0].id);
      toast(data.message || `已拆成 ${groupCount} 份任务；原任务已保留或归档`, "success");
    } catch (error) {
      toast(error.message, "error");
      save.disabled = false;
    }
  });

  $("splitDialog").addEventListener("close", () => {
    splitPlan.paperId = null;
    splitPlan.groups = [];
    $("splitGroups").replaceChildren();
  });

  function openOrderDialog() {
    if (!state.paper?.photos) return;
    if ((Number(state.paper.trash_count) || 0) > 0) {
      toast("回收站里还有题卡；请先恢复这些题卡，再调整页序", "error");
      return;
    }
    $("toolsMenu").open = false;
    pageOrder.order = state.paper.pages.map((page) => page.page_idx);
    renderOrderList();
    $("orderDialog").showModal();
  }

  function spanLabel(span) {
    return span[0] === span[1] ? `第 ${span[0]} 题` : `第 ${span[0]}–${span[1]} 题`;
  }

  // 按卷面题号检查新页序：印着前面题号的页排到了后面，切题就会漏题。
  function orderProblem(order) {
    const ranges = state.paper.photos.ranges;
    if (!ranges) return "";
    const spans = order.map((page) => ranges[page]).filter(Boolean);
    for (let i = 1; i < spans.length; i += 1) {
      if (spans[i][0] <= spans[i - 1][1]) {
        return `印着${spanLabel(spans[i])}的那页，排在了印着${spanLabel(spans[i - 1])}的那页后面。`;
      }
    }
    return "";
  }

  function renderOrderList() {
    const names = state.paper.photos.names || [];
    const ranges = state.paper.photos.ranges;
    const last = pageOrder.order.length - 1;
    $("orderList").replaceChildren(...pageOrder.order.map((page, position) => {
      const item = el("li", "order-item");
      const image = el("img");
      image.src = previewUrl(state.paperId, page);
      image.alt = `原第 ${page + 1} 页`;
      const head = el("div", "order-head");
      head.append(el("strong", "", `第 ${position + 1} 页`), el("span", "order-name", names[page] || ""));
      if (page !== position) head.append(el("span", "order-moved", `原第 ${page + 1} 页`));
      if (ranges) {
        const span = ranges[page];
        head.append(el("span", `order-range${span ? "" : " none"}`, span ? `卷面：${spanLabel(span)}` : "卷面：没找到题号"));
      }
      const tools = el("div", "order-tools");
      const earlier = button("← 往前", "small", () => movePage(position, -1));
      earlier.disabled = position === 0;
      const later = button("往后 →", "small", () => movePage(position, 1));
      later.disabled = position === last;
      tools.append(earlier, later);
      item.append(image, head, tools);
      return item;
    }));
  }

  function movePage(position, step) {
    const order = pageOrder.order;
    [order[position], order[position + step]] = [order[position + step], order[position]];
    renderOrderList();
  }

  $("pageOrder").addEventListener("click", openOrderDialog);
  $("orderSave").addEventListener("click", async () => {
    const problem = orderProblem(pageOrder.order);
    if (problem) {
      $("orderDialog").close();
      const ok = await confirmDialog({
        title: "这个页序和卷面题号对不上",
        text: `按卷面上印的题号，这个页序不对：${problem}\n\n按这个页序切题，会有题目切不出来。确定还是这样保存吗？`,
        ok: "仍然保存", danger: true
      });
      if (!ok) { $("orderDialog").showModal(); return; }
    }
    try {
      const data = await api(`/api/papers/${state.paperId}/page-order`, { method: "POST", body: { order: pageOrder.order } });
      if ($("orderDialog").open) $("orderDialog").close();
      state.rendered.clear();
      toast(data.paper?.status === "needs_grouping"
        ? "页序已保存，但题号仍有重合；请继续核对并拆分任务"
        : data.changed ? "页序已保存，正在按新页序重新切题" : "页序没变，已确认");
      refreshPaper();
      loadPapers();
    } catch (error) { toast(error.message, "error"); }
  });

  $("m3Button").addEventListener("click", async () => {
    const list = $("m3List");
    list.replaceChildren(el("li", "hint", "正在读取 M3 的试卷…"));
    $("m3Dialog").showModal();
    try {
      const data = await api("/api/m3/papers");
      list.replaceChildren();
      if (!data.papers.length) list.append(el("li", "hint", "M3 里没有找到已解析的试卷。"));
      data.papers.forEach((item) => {
        const row = el("li", "m3-item");
        row.append(el("span", "", item.filename));
        const action = button(item.imported ? "已导入，打开" : "导入", item.imported ? "small" : "primary small", async () => {
          action.disabled = true;
          try {
            const result = await api("/api/m3/papers", { method: "POST", body: { id: item.id } });
            $("m3Dialog").close();
            await loadPapers();
            selectPaper(result.paper.id);
          } catch (error) { toast(error.message, "error"); action.disabled = false; }
        });
        row.append(action);
        list.append(row);
      });
    } catch (error) { list.replaceChildren(el("li", "hint", error.message)); }
  });

  // ---------------------------------------------------------------- 页面杂项

  // 右下角"回到顶部"：往下翻过一段才出现，点一下平滑回到最上方。
  const toTop = $("toTop");
  const toolbar = $("toolbar");
  const syncScroll = () => {
    toTop.hidden = window.scrollY < 400;
    const top = document.querySelector(".topbar")?.offsetHeight || 56;
    toolbar.classList.toggle("stuck", toolbar.getBoundingClientRect().top <= top + 1 && window.scrollY > 0);
  };
  window.addEventListener("scroll", syncScroll, { passive: true });
  toTop.addEventListener("click", () => window.scrollTo({ top: 0, behavior: "smooth" }));
  syncScroll();

  $("keysButton").addEventListener("click", () => closeSettingsThen(() => $("keysDialog").showModal()));

  // 下拉菜单：点外面或按 Esc 收起。
  document.addEventListener("click", (event) => {
    document.querySelectorAll("details.menu[open], details.more[open]").forEach((menu) => {
      if (!menu.contains(event.target)) menu.open = false;
    });
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    document.querySelectorAll("details.menu[open], details.more[open]").forEach((menu) => { menu.open = false; });
  });

  document.querySelectorAll("dialog [data-close]").forEach((node) => node.addEventListener("click", () => node.closest("dialog").close()));
  document.querySelectorAll("dialog").forEach((node) => node.addEventListener("click", (event) => {
    if (event.target === node) node.close();
  }));

  setLens(state.lens);

  // ---------------------------------------------------------------- 启动

  async function start() {
    await loadStatus();
    await loadPapers();
    const params = new URLSearchParams(window.location.search);
    const wanted = params.get("paper") || params.get("document");
    if (wanted && state.papers.some((paper) => paper.id === wanted)) {
      await selectPaper(wanted);
      const draft = Number(params.get("draft"));
      if (draft && questionById(draft)) {
        if (isApproved(questionById(draft))) { state.expanded.add(draft); renderCards(); }
        setCurrent(draft, { focus: true });
        document.querySelector(`[data-id="${draft}"]`)?.scrollIntoView({ block: "start" });
      }
    } else if (state.papers.length) {
      await selectPaper(state.papers[0].id);
    }
  }

  start();
})();
}
