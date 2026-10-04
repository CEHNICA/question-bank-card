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
      return Boolean(target.closest('input, textarea, select, [contenteditable]:not([contenteditable="false"]), [role="textbox"]'));
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

  async function resolveUploadPolicy({ cloudReady = false, acknowledged = false } = {}, confirmCloud) {
    // Capture permission for this upload. Local import never depends on cloud
    // credentials or agreement to send the original document elsewhere.
    const allowCloud = Boolean(cloudReady && (acknowledged || (typeof confirmCloud === "function" && await confirmCloud())));
    return Object.freeze({ parseMode: "auto", allowCloud });
  }

  function appendUploadPolicy(form, policy) {
    form.append("parse_mode", "auto");
    form.append("allow_cloud", policy?.allowCloud === true ? "1" : "0");
  }

  return {
    classifyFile, isEditingTarget, shouldInterceptPaste, screenshotName,
    prepareClipboardFiles, routeFiles, buildClipboardBatch, runConfirmedPaste,
    resolveUploadPolicy, appendUploadPolicy
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
    } else if (stage === "segmenting" && raw.parsed_ahead) {
      headline = queueAhead ? `MinerU 已解析完 · 等前面 ${queueAhead} 项任务读完` : "MinerU 已解析完 · 即将读题";
      parts.push("排队时已提前完成解析，轮到它时直接切题读题");
    } else if (stage === "parsing") {
      if (raw.parsed_ahead) parts.push(`提前解析中，前面还有 ${queueAhead} 项任务`);
      if (raw.chunks && total > 0) {
        headline = `MinerU 解析中 · 已完成 ${completed}/${total} 个分片`;
        const activePages = pageRanges(raw.chunks);
        if (activePages) parts.push(`正在处理${activePages}`);
        else if (completed >= total) parts.push("所有分片均已解析，正在合并结果");
        else parts.push(`尚有 ${total - completed} 个分片等待开始`);
      } else {
        // 1.10.6: what MinerU itself reports, so a long wait says who is slow.
        const mineru = raw.mineru || null;
        const state = mineru?.state || "";
        const waited = formatDuration(safeNumber(mineru?.for_seconds));
        if (state === "uploading") {
          headline = "正在把文件传给 MinerU";
          parts.push("传完后由 MinerU 识别版面和文字");
        } else if (["submitted", "waiting-file", "pending"].includes(state)) {
          headline = `在 MinerU 排队中 · 已等 ${waited}`;
          parts.push("文件已经交给 MinerU，正在等它开始识别；要等多久看 MinerU 那边当时有多忙，题有据没有卡住。重新交会从队尾重排，不用重交");
        } else if (state === "running") {
          headline = total ? `MinerU 识别中 · 第 ${completed}/${total} 页` : "MinerU 识别中";
          parts.push(`MinerU 已开始识别 ${waited}`);
        } else if (state === "converting") {
          headline = "MinerU 识别完了，正在打包结果";
          parts.push("打包好就取回来本机切题");
        } else if (state === "downloading") {
          headline = "正在取回 MinerU 的结果";
          parts.push("取回后本机切题");
        } else {
          headline = "MinerU 解析中";
          parts.push("正在准备文件或等待 MinerU 返回；MinerU 没有提供完成百分比");
        }
      }
    } else if (stage === "segmenting") {
      headline = "本机切题中";
      parts.push("正在本机整理题号、题目范围和配图候选；这个阶段没有可靠百分比");
    } else if (stage === "reading") {
      headline = total ? `AI 读题中 · 已完成 ${completed}/${total}` : "AI 读题中";
      if (total) {
        const remaining = Math.max(0, total - completed);
        parts.push(remaining ? `剩余 ${remaining} 道，完成的题卡会陆续出现` : "全部题目已读完，正在整理结果");
        if (remaining && raw.eta_seconds !== undefined && raw.eta_seconds !== null) {
          parts.push(`按目前速度约还需 ${formatDuration(Math.max(5, safeNumber(raw.eta_seconds)))}`);
        }
      } else parts.push("题目总数尚未确定，完成的题卡会陆续出现");
    }

    parts.push(`任务创建至今 ${formatDuration(elapsed)}`);
    parts.push(`本任务状态最近更新 ${formatAge(idle)}`);
    // 排队任务在前一份任务结束前不会改写自己的 updated_at。
    // 队列数正在下降时把这叫作“后台停滞”会误导用户，因此排队阶段不报 stale。
    const stale = stage !== "queued" && !(raw.parsed_ahead && stage === "segmenting") && idle >= 240
      ? `已有 ${formatDuration(idle)}没有新的本任务状态更新；程序仍在等待${stage === "parsing" ? " MinerU 或本机处理" : stage === "reading" ? "模型或后台处理" : "后台处理"}，这不等同于失败。`
      : "";
    const determinate = Boolean(raw.determinate && total > 0 && ["parsing", "reading"].includes(stage));
    // 1.10.7: send one file to MinerU again when that can help: its upload was
    // not picked up (waiting-file), it has been reading for minutes, or we have
    // heard nothing.  Not while MinerU says it is queueing (pending): sending
    // again only puts the file at the back of the queue (1.10.8).
    const mineruState = raw.mineru?.state || "";
    const mineruWait = raw.mineru ? safeNumber(raw.mineru.for_seconds) : elapsed;
    const waitNeeded = { "": 90, submitted: 60, "waiting-file": 60, running: 300 }[mineruState];
    const canReparse = stage === "parsing" && !raw.chunks && !raw.parsed_ahead
      && waitNeeded !== undefined && mineruWait >= waitNeeded;
    return {
      stage,
      canReparse,
      headline,
      detail: `${parts.join(" · ")}。`,
      stale,
      determinate,
      ratio: determinate ? Math.max(0, Math.min(1, completed / total)) : null,
      stageIndex: STAGES.findIndex((item) => item.key === stage)
    };
  }

  function canStopPaper(paper) {
    return ["queued", "parsing", "segmenting", "reading"].includes(paper?.status);
  }

  // A paper's bar has room for two states, not five.  不用再看 is what a person
  // is never asked about again: ticked, or already in the library exactly as it
  // reads on screen.  还要看 is everything else that exists — including a card
  // the reader read cleanly, because a person still has to tick it.  Cards still
  // being read belong to neither: nobody has looked at them yet.
  //
  // These two numbers come from the server, the same ones the review page's
  // “需要核查” tab counts.  Deriving them here from green/yellow/red is what
  // produced “列表说 4 张要看、点进去却是 25 张要看”.
  function paperProgress(counts) {
    const c = counts || {};
    const total = c.total || 0;
    // 后端已经按“审核页那套判据”算好了；老服务没有这两个数时才退回本地推算，
    // 宁可退回也不要另立一套口径——两边各数一遍就会互相打架。
    const todo = c.todo != null ? c.todo : (c.yellow || 0) + (c.red || 0);
    const done = c.done != null ? c.done : Math.max(0, total - todo - (c.waiting || 0));
    return { total, todo, done };
  }

  function canSwitchMinerUToManual(paper) {
    return paper?.parse_mode === "mineru" && !paper.archived && !paper.demo
      && ["queued", "parsing", "segmenting"].includes(paper.status);
  }

  function canContinueAiCut(paper) {
    if (!paper || paper.archived || paper.demo) return false;
    return canSwitchMinerUToManual(paper)
      || (["manual", "native"].includes(paper.parse_mode) && ["ready", "failed"].includes(paper.status))
      || (paper.parse_mode === "mineru" && paper.status === "failed");
  }

  return { STAGES, formatDuration, formatAge, processingPresentation, paperProgress, canStopPaper, canSwitchMinerUToManual, canContinueAiCut };
})();

const QBReviewDiff = (() => {
  "use strict";

  const FIELD_NAMES = { stem: "题干", A: "选项 A", B: "选项 B", C: "选项 C", D: "选项 D", E: "选项 E" };
  const READER_NAMES = { a: "历史识读 1", b: "历史识读 2", c: "其他历史记录" };
  const CONTEXT = 10;

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
    if (!question || question.body_mode === "source_image" || question.edited || question.state !== "yellow" || !renderer) return empty;
    const reads = Object.entries(question.reads || {}).filter(([, reading]) => readingOk(reading));
    if (reads.length < 2) return empty;

    const marks = {};
    const observedOnly = [];
    let hasContentDifference = false;
    ["stem", "A", "B", "C", "D", "E"].forEach((field) => {
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
          // A lone “、” or “和” means nothing without the words around it.
          const around = (text) => text.replace(/\s+/g, " ");
          observedOnly.push({
            signature, field, fieldName: FIELD_NAMES[field], reader: readerKey,
            readerName: READER_NAMES[readerKey] || readerKey, text: value,
            before: around(observed.slice(Math.max(0, hunk.observed.start - CONTEXT), hunk.observed.start)),
            after: around(observed.slice(hunk.observed.end, hunk.observed.end + CONTEXT)),
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

const QBNotify = (() => {
  "use strict";

  const ACTIVE = new Set(["queued", "parsing", "segmenting", "reading"]);

  // Papers that were being processed at the last look and no longer are.
  // The open paper is left out: its own view already shows the change.
  function justFinished(previousStatus, papers, openId) {
    return (papers || []).filter((paper) => {
      const before = previousStatus.get(paper.id);
      return Boolean(before) && ACTIVE.has(before) && !ACTIVE.has(paper.status) && paper.id !== openId;
    });
  }

  function finishedMessage(paper, name) {
    const c = paper.counts || {};
    const { todo } = QBProgress.paperProgress(c);
    if (paper.status === "failed") return paper.stopped ? `“${name}”已停止` : `“${name}”处理失败`;
    if (paper.status === "needs_grouping") return `“${name}”需要确认资料结构`;
    return `“${name}”已读完：${c.total || 0} 题${todo ? `，${todo} 张要看` : "，全部识读完成"}`;
  }

  function title(base, unseen) {
    return unseen ? `（${unseen} 份读完）${base}` : base;
  }

  // The next paper in list order (after the open one, wrapping around) that
  // is ready and still has cards nobody has marked as passed.
  function nextToReview(papers, openId) {
    const list = papers || [];
    const start = Math.max(0, list.findIndex((paper) => paper.id === openId));
    for (let step = 1; step <= list.length; step += 1) {
      const paper = list[(start + step) % list.length];
      if (!paper || paper.id === openId || paper.status !== "ready") continue;
      const c = paper.counts || {};
      if ((c.total || 0) > (c.approved || 0)) return paper;
    }
    return null;
  }

  return { justFinished, finishedMessage, title, nextToReview };
})();

// Editing a Markdown table in the stem's text box: insert one, add a row or a
// column to the table under the cursor.
const QBTableText = (() => {
  "use strict";

  const isRow = (line) => line.trim().startsWith("|") && (line.match(/\|/g) || []).length >= 2;

  function insert(value, cursor) {
    const at = Math.max(0, Math.min(value.length, Number(cursor) || 0));
    const before = value.slice(0, at);
    const after = value.slice(at);
    const lead = before && !before.endsWith("\n") ? "\n" : "";
    const tail = after && !after.startsWith("\n") ? "\n" : "";
    const table = "|  |  |  |\n|---|---|---|\n|  |  |  |\n|  |  |  |";
    return { value: `${before}${lead}${table}${tail}${after}`, cursor: before.length + lead.length + 2 };
  }

  // The table whose lines hold the cursor (a cursor on the line right after a
  // table also counts).
  function around(value, cursor) {
    const lines = value.split("\n");
    let offset = 0;
    let line = 0;
    for (; line < lines.length; line += 1) {
      if (cursor <= offset + lines[line].length) break;
      offset += lines[line].length + 1;
    }
    let index = Math.min(line, lines.length - 1);
    if (!isRow(lines[index] || "") && index > 0 && isRow(lines[index - 1])) index -= 1;
    if (!isRow(lines[index] || "")) return null;
    let first = index;
    let last = index;
    while (first > 0 && isRow(lines[first - 1])) first -= 1;
    while (last + 1 < lines.length && isRow(lines[last + 1])) last += 1;
    if (last === first) return null;
    return { lines, first, last };
  }

  function grow(value, cursor, what) {
    const table = around(value, cursor);
    if (!table) return null;
    const { lines, first, last } = table;
    const cells = (line) => {
      const text = line.trim().replace(/^\|/, "").replace(/\|$/, "");
      return text.split("|").length;
    };
    const width = Math.max(...lines.slice(first, last + 1).map(cells));
    let focus;
    if (what === "row") {
      lines.splice(last + 1, 0, `|${"  |".repeat(width)}`);
      focus = last + 1;
    } else {
      for (let index = first; index <= last; index += 1) {
        const separator = /^\s*\|?(\s*:?-{3,}:?\s*\|)+\s*$/.test(lines[index]);
        lines[index] = `${lines[index].replace(/\s*$/, "").replace(/\|?$/, "|")}${separator ? "---|" : "  |"}`;
      }
      focus = first;
    }
    const next = lines.join("\n");
    const cursorAt = lines.slice(0, focus).reduce((sum, line) => sum + line.length + 1, 0) + 2;
    return { value: next, cursor: Math.min(cursorAt, next.length) };
  }

  return { insert, grow };
})();

// 新手教学：在示例试卷上一步步做一遍。每一步什么时候算做对，由这里判断。
const QBTeach = (() => {
  "use strict";

  const VERSION = 3;
  const LESSONS = [
    { key: "cut", section: "basic", title: "先切出第 1 题",
      text: "点“开始框题”，在原卷第 1 题的左上角和右下角各点一下，再点“保存下一题”（S）。完整保留题干和选项。" },
    { key: "cutComplete", section: "basic", title: "完成这次切题",
      text: "第 1 题已保存。点“完成切题”（Ctrl+S），回到题卡。本练习保留原图，不调用 AI 识读。" },
    { key: "fix", section: "basic", title: "改好第 9 题的错字",
      text: "原卷写的是“向右移动 5 个单位”。点第 9 题的“改字”，把 3 改成 5，再保存。" },
    { key: "tick9", section: "basic", title: "核对并通过第 9 题",
      text: "对照原卷，确认刚改的题干、选项和配图都正确，再点第 9 题左边的方框。通过后自动进入练习题库。" },
    { key: "tick", section: "basic", title: "核对并通过第 1 题",
      text: "确认第 1 题的原图完整、题型正确，再点题号左边的方框。自己的试卷通过后会自动入库；示例只进入练习题库。" },
    { key: "library", section: "basic", title: "到练习题库选题",
      text: "点击“去练习题库”，实际选题、预览并导出。本练习使用单独的题库与选题篮。" },
    { key: "basket", section: "basic", title: "选择要出的题",
      text: "在练习题库勾选题目，再打开组卷预览。" },
    { key: "export", section: "basic", title: "导出这份练习",
      text: "先核对实际 PDF 预览，再点击导出 PDF。" },
    { key: "finish", section: "basic", title: "练习完成", manual: true, final: true,
      text: "你已实际切题、改字、核对通过、选题并导出。现在可以上传自己的试卷；遇到补图、跨页或答案问题时，再打开帮助中的对应说明。" },
    { key: "figure", section: "figure", title: "补上第 2 题的配图",
      text: "点“补选配图”，在原卷上点蓝色候选图，选择“题干”，再保存。保存后还需重新核对。" }
  ];
  const indexOf = (key) => LESSONS.findIndex((lesson) => lesson.key === key);

  function restore(saved) {
    if (!saved || typeof saved !== "object" || typeof saved.paper !== "string" || !saved.paper) return null;
    // The shorter course starts with a real crop. Legacy progress cannot claim
    // completion of actions that were only described in the previous course.
    const current = saved.version === VERSION;
    const key = current && indexOf(saved.lesson) >= 0 ? saved.lesson : "cut";
    const lesson = LESSONS[indexOf(key)];
    const practiceUrl = typeof saved.practiceUrl === "string" && saved.practiceUrl === `/practice/${saved.paper}`
      ? saved.practiceUrl : `/practice/${saved.paper}`;
    return { paper: saved.paper, index: indexOf(key), active: saved.active !== false, migrated: !current,
      course: lesson.section, practiceUrl,
      completed: current && saved.completed === true ? key : null };
  }

  function progress(index) {
    const lesson = LESSONS[Number.isInteger(index) && index >= 0 && index < LESSONS.length ? index : 0];
    const section = LESSONS.filter((item) => item.section === lesson.section && !item.final);
    return { lesson, label: lesson.section === "basic" ? "手工练习" : "补图练习",
      current: lesson.final ? section.length : section.findIndex((item) => item.key === lesson.key) + 1,
      total: section.length };
  }

  function nextIndex(index) {
    const lesson = LESSONS[index];
    return lesson && LESSONS[index + 1]?.section === lesson.section ? index + 1 : null;
  }

  const FIXED = /向右移动\s*5\s*个单位/;
  function lessonDone(key, event) {
    if (!event || !LESSONS.some((lesson) => lesson.key === key && !lesson.manual)) return false;
    switch (key) {
      case "cut": return event.type === "cut" && event.number === 1;
      case "cutComplete": return event.type === "cutComplete";
      case "tick": return event.type === "approve" && event.number === 1;
      case "tick9": return event.type === "approve" && event.number === 9;
      case "fix": return event.type === "text" && event.number === 9 && FIXED.test(event.stem || "");
      case "figure": return event.type === "figures" && event.number === 2 && event.figures > 0;
      default: return false;
    }
  }

  function lessonHint(key, event) {
    if (key === "fix" && event?.type === "text" && event.number === 9) return "原卷写的是“向右移动 5 个单位”。请对照后再保存。";
    if (key === "cut" && event?.type === "cut") return "这一步练第 1 题：先检查题号，再框完整题目。";
    if (key === "figure" && event?.type === "figures" && event.number === 2) return "还没有配图：点蓝色候选图，选“题干”，再保存。";
    return "";
  }

  return { VERSION, LESSONS, restore, progress, indexOf, nextIndex, lessonDone, lessonHint };
})();

// A figure can continue on the next page (a table cut by a page break).  In
// the figure editor the lower piece is a box "joined" to the figure before it;
// it is saved as a part of that figure and shown as one image.
const QBFigureJoin = (() => {
  "use strict";

  // Page, then column, then top to bottom.
  function readingOrder(boxes) {
    return [...boxes].sort((a, b) => {
      if (a.page_idx !== b.page_idx) return a.page_idx - b.page_idx;
      if (Math.abs(a.bbox[0] - b.bbox[0]) > 150) return a.bbox[0] - b.bbox[0];
      return a.bbox[1] - b.bbox[1];
    });
  }

  // The figure a joined box continues: the nearest figure before it that is
  // not itself a continuation.  A joined box with nothing before it has none.
  function joinHost(box, boxes) {
    let host = null;
    for (const item of readingOrder(boxes)) {
      if (item === box) return host;
      if (!item.join || !host) host = item;
    }
    return null;
  }

  function figuresFromBoxes(boxes) {
    const hosts = new Map();
    const figures = [];
    boxes.forEach((box) => {
      if (box.join && joinHost(box, boxes)) return;
      const item = {
        page_idx: box.page_idx, bbox: [...box.bbox], slot: box.slot,
        ...(box.label_offset ? { label_offset: { ...box.label_offset } } : {}),
        ...(box.candidate_key ? { candidate_key: box.candidate_key } : {})
      };
      hosts.set(box, item);
      figures.push(item);
    });
    readingOrder(boxes).forEach((box) => {
      const host = box.join ? joinHost(box, boxes) : null;
      if (!host) return;
      const item = hosts.get(host);
      item.parts = [...(item.parts || []), {
        page_idx: box.page_idx, bbox: [...box.bbox],
        ...(box.candidate_key ? { candidate_key: box.candidate_key } : {})
      }];
    });
    return figures;
  }

  return { readingOrder, joinHost, figuresFromBoxes };
})();

// 专注模式里“正在看哪张卡”：阅读线平时在屏幕上方三分之一处；滚到顶时
// 贴着顶部，滚到底时一路落到底部，所以第一张和最后一张都轮得到。
const QBFocus = (() => {
  "use strict";

  // top/bottom: the visible band; scrollY: how far the page is scrolled;
  // below: how much page is left under the bottom of the screen.
  function readingLine({ top, bottom, scrollY, below }) {
    const height = bottom - top;
    const third = height / 3;
    return Math.min(bottom - 1, top + Math.min(Math.max(scrollY, 0), third) + Math.max(0, height - third - Math.max(below, 0)));
  }

  // The card under the line, or the one nearest to it.  rects: [{top, bottom}].
  function nearestToLine(rects, line) {
    let best = -1;
    let nearest = Infinity;
    rects.forEach((rect, index) => {
      const distance = rect.top > line ? rect.top - line : rect.bottom <= line ? line - rect.bottom : 0;
      if (distance < nearest) { nearest = distance; best = index; }
    });
    return best;
  }

  // A card counts as being read only while a fair share of it is on screen:
  // at least ``share`` of its height, or of the visible band for a card taller
  // than the screen.  One slid mostly under the toolbar does not.
  function mostlyVisible(rect, top, bottom, share = 0.35) {
    const visible = Math.min(rect.bottom, bottom) - Math.max(rect.top, top);
    return visible > 0 && visible >= share * Math.min(rect.bottom - rect.top, bottom - top);
  }

  return { readingLine, nearestToLine, mostlyVisible };
})();

const QBEdits = (() => {
  "use strict";

  // Compare the current form with the values shown when it opened. Returning
  // to those values clears the warning; merely opening an editor is harmless.
  function createEditGuard() {
    const entries = new Map();
    let confirming = false;
    const selected = (ids) => ids == null ? [...entries.values()]
      : ids.map((id) => entries.get(id)).filter(Boolean);
    const dirty = (entry) => JSON.stringify(entry.read()) !== entry.initial;
    return {
      track(id, read, discard, focus = () => {}) {
        entries.set(id, { read, initial: JSON.stringify(read()), discard, focus, saving: false });
      },
      release(id) { entries.delete(id); },
      setSaving(id, saving) { if (entries.has(id)) entries.get(id).saving = saving; },
      hasSaving(ids = null) { return selected(ids).some((entry) => entry.saving); },
      hasPendingWork() { return selected(null).some((entry) => entry.saving || dirty(entry)); },
      async discard(confirm, ids = null) {
        // A repeated click must not reuse the first click's permission for a
        // different destination. A save in flight must finish on this paper.
        if (confirming) return false;
        const active = selected(ids);
        if (active.some((entry) => entry.saving)) return false;
        const changed = active.filter(dirty);
        confirming = true;
        try {
          if (changed.length && !(await confirm(changed.length))) {
            changed[0].focus();
            return false;
          }
          active.forEach((entry) => entry.discard());
          return true;
        } finally { confirming = false; }
      }
    };
  }

  function protectBeforeUnload(event, guard) {
    if (!guard.hasPendingWork()) return false;
    event.preventDefault();
    event.returnValue = "";
    return true;
  }

  // Return a proposed form value only when a reviewed range still matches.
  // This helper performs no save, approval, or change to the original string.
  function reviewedPrefill(original, prefill) {
    const source = String(original ?? "");
    if (!prefill || typeof prefill.value !== "string") return null;
    const ranged = prefill.start !== undefined || prefill.end !== undefined;
    const start = ranged ? prefill.start : 0;
    const end = ranged ? prefill.end : source.length;
    const splitsCharacter = (index) => index > 0 && index < source.length
      && /[\uD800-\uDBFF]/.test(source[index - 1]) && /[\uDC00-\uDFFF]/.test(source[index]);
    if (!Number.isInteger(start) || !Number.isInteger(end) || start < 0 || end < start || end > source.length
      || splitsCharacter(start) || splitsCharacter(end)
      || (ranged && (typeof prefill.before !== "string" || source.slice(start, end) !== prefill.before))
      || (!ranged && prefill.before !== undefined && prefill.before !== source)) return null;
    return { value: source.slice(0, start) + prefill.value + source.slice(end), start, end: start + prefill.value.length };
  }

  return { createEditGuard, protectBeforeUnload, reviewedPrefill };
})();

const QBRegionAssist = (() => {
  "use strict";
  function recommendedPrefill(question, read) {
    const r = read?.recommendation;
    if (read?.status !== "done" || r?.status !== "recommended"
      || !["stem", "A", "B", "C", "D", "E"].includes(r.field)
      || typeof r.before !== "string" || typeof r.after !== "string" || !r.after.trim()
      || r.after !== read.text || typeof r.field_text !== "string"
      || !Number.isInteger(r.start) || !Number.isInteger(r.end)) return null;
    const original = String(r.field === "stem" ? question.stem || "" : question.options?.[r.field] || "");
    if (original !== r.field_text || r.start < 0 || r.end < r.start || r.end > original.length
      || original.slice(r.start, r.end) !== r.before) return null;
    const splitsCharacter = (index) => index > 0 && index < original.length
      && /[\uD800-\uDBFF]/.test(original[index - 1]) && /[\uDC00-\uDFFF]/.test(original[index]);
    if (splitsCharacter(r.start) || splitsCharacter(r.end)) return null;
    if (r.before ? original.indexOf(r.before) !== r.start || original.indexOf(r.before, r.start + 1) !== -1
      : original !== "" || r.field === "stem" || r.start !== 0 || r.end !== 0) return null;
    return { field: r.field, value: r.after, start: r.start, end: r.end, before: r.before };
  }
  return { recommendedPrefill };
})();

const QBRegionWait = (() => {
  const REQUEST_TIMEOUT_MS = 15000;
  const JOB_TIMEOUT_MS = 120000;
  function abortError(message = "已取消本次识读，原题没有改变。") {
    const error = new Error(message); error.name = "AbortError"; return error;
  }
  async function boundedRequest(task, { timeoutMs = REQUEST_TIMEOUT_MS, signal } = {}) {
    const controller = new AbortController();
    let timer, onAbort;
    const stopped = new Promise((_, reject) => {
      onAbort = () => { controller.abort(); reject(abortError()); };
      if (signal?.aborted) { onAbort(); return; }
      signal?.addEventListener("abort", onAbort, { once: true });
      timer = setTimeout(() => {
        controller.abort();
        const error = new Error("本机服务没有及时回应，请检查软件是否仍在运行，再重试。原题没有改变。");
        error.name = "TimeoutError"; reject(error);
      }, timeoutMs);
    });
    try {
      if (signal?.aborted) return await stopped;
      return await Promise.race([Promise.resolve().then(() => task(controller.signal)), stopped]);
    } finally {
      clearTimeout(timer); signal?.removeEventListener("abort", onAbort);
    }
  }
  function deadline(read, observedAt = Date.now()) {
    const supplied = Date.parse(read?.deadline_at);
    if (Number.isFinite(supplied)) return supplied;
    const created = Date.parse(read?.created_at);
    return (Number.isFinite(created) ? created : observedAt) + JOB_TIMEOUT_MS;
  }
  function isExpired(read, observedAt = Date.now(), now = Date.now()) {
    return ["queued", "running"].includes(read?.status) && now >= deadline(read, observedAt);
  }
  function sameVersion(question, revision) {
    return Boolean(question && Number(question.content_revision || 0) === Number(revision || 0));
  }
  return { REQUEST_TIMEOUT_MS, JOB_TIMEOUT_MS, boundedRequest, deadline, isExpired, sameVersion, abortError };
})();

const QBManualCrop = (() => {
  // Question numbers belong to their group. An automatic group choice must
  // avoid every currently visible number until the server resolves the group.
  function nextCropNumber(questions, start = 1, groupId = null) {
    const used = new Set((questions || []).filter((q) => groupId == null || Number(q.group?.id) === Number(groupId))
      .map((q) => Number(q.number)));
    const first = Number.isInteger(start) && start >= 1 && start <= 999 ? start : 1;
    for (let i = 0; i < 999; i += 1) {
      const number = (first - 1 + i) % 999 + 1;
      if (!used.has(number)) return number;
    }
    return null;
  }
  function cropDraftSnapshot(boxes, number = "", group = "", ignored = []) {
    return { number: String(number), group: String(group), boxes: (boxes || []).map((box) => ({
      page_idx: box.page_idx, bbox: [...box.bbox],
      ...(box.slot ? { slot: box.slot } : {}), ...(box.join ? { join: true } : {}),
      ...(box.label_offset ? { label_offset: { ...box.label_offset } } : {})
    })), ignored: [...ignored].sort() };
  }
  function moveCropBox(bbox, dx, dy) {
    const [x0, y0, x1, y1] = bbox;
    const x = Math.max(-x0, Math.min(1000 - x1, dx));
    const y = Math.max(-y0, Math.min(1000 - y1, dy));
    return [x0 + x, y0 + y, x1 + x, y1 + y].map((value) => Math.round(value * 10) / 10);
  }
  function cropShortcutAction(event, context = {}) {
    if (!context.open || context.saving || context.closing || context.otherDialog || context.menuOpen
      || context.editing || event.defaultPrevented || event.isComposing || event.keyCode === 229) return null;
    const modified = event.ctrlKey || event.metaKey;
    const key = event.key.length === 1 ? event.key.toLowerCase() : event.key;
    if (event.altKey || (event.ctrlKey && event.metaKey)) return null;
    if (modified) {
      if (key === "s" && !event.shiftKey && !event.repeat && context.mode === "new"
        && !context.practiceRead) return "complete";
      if (key === "Enter" && !event.shiftKey && !event.repeat && !context.practiceRead && context.mode !== "view") {
        return context.mode === "new" ? "complete" : "save";
      }
      if (!context.onControl && key === "z" && ["new", "regions"].includes(context.mode)) return event.shiftKey ? "redo" : "undo";
      return null;
    }
    if (context.onControl) return null;
    if (!event.shiftKey && (key === "Enter" || key === "s") && context.mode === "new"
      && context.canvasFocused && !event.repeat && !context.practiceRead) return "next";
    if (key === "PageUp") return "previous-page";
    if (key === "PageDown") return "next-page";
    if (key === " ") return "pan";
    if (["+", "="].includes(key)) return "zoom-in";
    if (["-", "_"].includes(key)) return "zoom-out";
    if (key === "0") return "fit";
    if (key === "w") return "width";
    if (["Delete", "Backspace"].includes(key) && context.mode !== "view") return "delete";
    return null;
  }
  return { nextCropNumber, cropDraftSnapshot, moveCropBox, cropShortcutAction };
})();

const QBReviewGuidance = (() => {
  "use strict";
  function historicalParseReason(paper) {
    if (paper?.status !== "ready") return false;
    return [
      "本地文字层没有可靠题卡，按本次授权尝试已配置的 MinerU。",
      "已从本地文字层准备题卡，请对照原卷核对；未切出的页面可手工补充。"
    ].includes(paper.processing_plan?.fallback_reason);
  }
  return { historicalParseReason };
})();

const QBCutReading = (() => {
  "use strict";

  function hasCurrentReading(q) {
    const suggestion = q.ocr_suggestion;
    return Boolean(suggestion && suggestion.revision === q.content_revision
      && !suggestion.error && String(suggestion.stem || "").trim());
  }

  function cutReadingSummary(questions = []) {
    const saved = questions.filter((q) => q.body_mode === "source_image" && q.regions?.length);
    const pending = questions.filter((q) => q.ocr_pending
      && (q.body_mode === "source_image" || (q.body_mode === "text" && q.processing_mode === "manual")));
    const eligible = saved.filter((q) => !q.ocr_pending && !q.approved && !q.publication && !hasCurrentReading(q));
    return { saved: saved.length, pending: pending.length,
      eligibleIds: eligible.map((q) => q.id),
      revisions: Object.fromEntries(eligible.map((q) => [String(q.id), q.content_revision])),
      stage: pending.length || eligible.length ? 2 : questions.length ? 3 : 1 };
  }

  function cutReadingRequest(questions, questionIds = null, revision = null) {
    const summary = cutReadingSummary(questions);
    const ids = Array.isArray(questionIds) ? summary.eligibleIds.filter((id) => questionIds.includes(id)) : summary.eligibleIds;
    return { question_ids: ids, revisions: Object.fromEntries(ids.map((id) => [String(id), summary.revisions[String(id)]])),
      ...(Number.isInteger(revision) ? { revision } : {}) };
  }

  function showCutReadingStage(paper, summary) {
    return Boolean(paper && !paper.demo && ["ready", "failed", "reading"].includes(paper.status)
      && (summary.saved || summary.pending || ["manual", "native"].includes(paper.parse_mode)));
  }
  return { hasCurrentReading, cutReadingSummary, cutReadingRequest, showCutReadingStage };
})();

if (typeof module !== "undefined" && module.exports) {
  module.exports = { ...QBUpload, ...QBProgress, ...QBReviewDiff, ...QBResegment, ...QBNotify, ...QBFigureJoin, ...QBFocus,
    ...QBEdits,
    ...QBRegionAssist,
    ...QBRegionWait,
    ...QBManualCrop,
    ...QBCutReading,
    ...QBReviewGuidance,
    insertTableText: QBTableText.insert, growTableText: QBTableText.grow,
    TEACH_LESSONS: QBTeach.LESSONS, lessonDone: QBTeach.lessonDone, lessonHint: QBTeach.lessonHint,
    restoreTeaching: QBTeach.restore, teachingProgress: QBTeach.progress };
}

if (typeof window !== "undefined" && typeof document !== "undefined") {
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const R = window.QBRender;
  const OPTION_KEYS = ["A", "B", "C", "D", "E"];
  const CHOICE = new Set(["single_choice", "multiple_choice"]);
  const TYPE_NAMES = { single_choice: "单选题", multiple_choice: "多选题", fill_blank: "填空题", true_false: "判断题", free_response: "解答题", unknown: "题型未定" };
  const SLOT_NAMES = { stem: "题干", A: "选项A", B: "选项B", C: "选项C", D: "选项D", E: "选项E" };
  const FILTERS = [
    { key: "all", label: "全部" },
    { key: "todo", label: "需要核查" },
    { key: "approved", label: "已入库", title: "题库里已经有这道题的记录（打了勾的，或原来就放着的）" },
    // AI 助手（tiyouju 命令行）打的勾：只在有这样的题时出现，方便人抽查。
    { key: "ai", label: "AI 通过", optional: true }
  ];
  const ACTIVE_STATUS = new Set(["queued", "parsing", "segmenting", "reading"]);
  // Papers that finished in the background while another one was open.  The
  // window title counts them, so a reviewer who uploaded a batch sees from
  // the taskbar when the next paper is ready.
  const finishedUnseen = new Set();
  const BASE_TITLE = document.title;

  const state = {
    status: null, papers: [], paperId: null, paper: null, questions: [], filter: "all",
    archivedPapers: [], archiveNextOffset: null, archiveLoading: false, restoringPaperId: null,
    rendered: new Map(), editing: new Set(), expanded: new Set(), pollTimer: null, listTimer: null,
    // autoExpanded：J/K 跳过去时自动展开的已通过题，离开时收回；手动展开的不在里面。
    autoExpanded: new Set(), autoExpand: readPref("qb-auto-expand", "1") === "1",
    current: null, lens: readPref("qb-lens", "1") === "1", focus: readPref("qb-focus", "1") === "1", followHold: false,
    deleteBusy: false, trashBusy: false, approveAllBusy: false,
    cropDraftAttention: new Map(),
    // 入库进行中：{ paperId, done, total }，按钮上显示进度。
    publishing: null
  };
  const editGuard = QBEdits.createEditGuard();

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

  async function api(path, { method = "GET", body, form, signal } = {}) {
    const options = { method, headers: {}, cache: "no-store" };
    if (signal) options.signal = signal;
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
  function confirmDialog({ title, text = "", ok = "确定", cancel = "取消", danger = false, focusCancel = false }) {
    const dialog = $("confirmDialog");
    const cancelButton = dialog.querySelector('[value="cancel"]');
    $("confirmTitle").textContent = title;
    $("confirmText").textContent = text;
    $("confirmText").hidden = !text;
    $("confirmOk").textContent = ok;
    cancelButton.textContent = cancel;
    $("confirmIconUse").setAttribute("href", danger ? "#i-alert" : "#i-question");
    dialog.classList.toggle("danger", danger);
    dialog.returnValue = "";
    dialog.showModal();
    (focusCancel ? cancelButton : $("confirmOk")).focus();
    return new Promise((resolve) => {
      dialog.addEventListener("close", () => resolve(dialog.returnValue === "ok"), { once: true });
    });
  }

  async function discardEdits(ids = null) {
    if (editGuard.hasSaving(ids)) {
      toast("题目正在保存，请等保存完成后再离开", "error");
      return false;
    }
    return editGuard.discard((count) => confirmDialog({
      title: "改字还没保存",
      text: `${count} 道题有未保存的改动。丢弃后不会修改题库里的题目；也可以继续编辑，再点保存。`,
      ok: "丢弃改动", cancel: "继续编辑", danger: true, focusCancel: true
    }), ids);
  }

  async function leaveFor(url) {
    if (!(await prepareSettingsLeave()) || !(await discardEdits())) return false;
    window.location.assign(url);
    return true;
  }

  // Browser refresh/close cannot wait for our dialog. Use its native leave
  // warning there; in-app navigation keeps the clearer two-action dialog.
  window.addEventListener("beforeunload", (event) => {
    QBEdits.protectBeforeUnload(event, editGuard);
    if (modelFormDirty || credentialHasNewKeys() || window.LibraryAISettings?.hasUnsavedChanges?.() || window.LibraryAISettings?.isMutating?.()) {
      event.preventDefault();
      event.returnValue = "";
    }
  });
  document.addEventListener("click", (event) => {
    const link = event.target.closest?.("a[href]");
    if (!link || event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey
      || link.hasAttribute("download") || (link.target && link.target !== "_self")) return;
    const url = new URL(link.href, window.location.href);
    const here = new URL(window.location.href);
    if (url.origin === here.origin && url.pathname === here.pathname && link.closest(".topnav") && link.getAttribute("aria-current") === "page") { event.preventDefault(); return; }
    if (!(editGuard.hasPendingWork() || window.location.pathname === "/settings")) return;
    if (url.origin === here.origin && url.pathname === here.pathname && url.search === here.search && url.hash) return;
    event.preventDefault();
    leaveFor(url.href);
  });

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
    if (q.body_mode === "source_image") return false;
    return FIGURE_REVIEW_BLOCKS.has(figureReview(q)?.status);
  }

  function isApproved(q) {
    const frozen = state.cropDraftAttention.get(q.id);
    return frozen ? frozen.approved : Boolean(q.approved && q.approval_valid !== false && !q.approval_stale && !figureBlocksApproval(q));
  }

  // 已经入库、而且题库里的那份和现在这张卡一模一样 —— 这道题不用再看第二眼了。
  // 题卡上写“有改动未入库”的不算：那说明你入库之后又改过，还要再看一次。
  // 正在框的草稿也不算：草稿里的东西题库里根本没有。
  function isSettled(q) {
    if (state.cropDraftAttention.get(q.id)) return false;
    return Boolean(q.publication && q.publication.up_to_date);
  }

  // 「不用再看」= 已经通过，或者已经入库且和现在一致。
  function isDone(q) {
    return isApproved(q) || isSettled(q);
  }

  // AI 助手打的勾照常算通过（能入库），但还等人核对；人点一下方框就变成人工通过。
  function isAiApproved(q) {
    const frozen = state.cropDraftAttention.get(q.id);
    return frozen ? frozen.ai : isApproved(q) && q.approved_by === "ai";
  }

  function isHumanApproved(q) {
    return isApproved(q) && !isAiApproved(q);
  }

  function agentLabel(q) {
    return q.approval_agent || "AI";
  }

  function canApprove(q) {
    const hasBody = q.body_mode === "source_image" ? Boolean(q.question_images?.length) : Boolean(q.stem);
    return Boolean(hasBody && !figureBlocksApproval(q) && !typeBlocksApproval(q) && !cropDraftNeedsReview(q)
      && (q.state === "green" || q.state === "yellow"));
  }

  // 题型没读出来（题型未定）的题不能通过：先在题号旁边选题型。
  function typeBlocksApproval(q) {
    return Boolean(q.type_blocked) && (q.state === "green" || q.state === "yellow");
  }

  function needsCheck(q) {
    const frozen = state.cropDraftAttention.get(q.id);
    return frozen ? frozen.todo : !isDone(q) && (figureBlocksApproval(q) || typeBlocksApproval(q) || approvalNeedsReview(q) || q.state === "yellow" || q.state === "red");
  }

  function cropDraftNeedsReview(q) {
    return Boolean(state.cropDraftAttention.get(q.id)?.dirty);
  }

  function needsGeneralReview(q) {
    const frozen = state.cropDraftAttention.get(q.id);
    return frozen ? frozen.green : !isDone(q) && !approvalNeedsReview(q) && !figureBlocksApproval(q)
      && !typeBlocksApproval(q) && q.state === "green";
  }

  function needsReview(q) {
    return needsCheck(q) || needsGeneralReview(q);
  }

  function anyDialogOpen() {
    return [...document.querySelectorAll("dialog")].some((dialog) => dialog.open);
  }

  // ---------------------------------------------------------------- 配置与试卷列表

  function brandNotice(text, kind = "") {
    const node = $("engineLine");
    node.textContent = text;
    node.title = text;
    node.hidden = !text;
    node.className = `engine-line${kind ? ` ${kind}` : ""}`;
  }

  let statusRequestToken = 0;
  async function loadStatus() {
    const token = ++statusRequestToken;
    try {
      const received = await api("/api/status");
      if (token !== statusRequestToken) return true;
      state.status = received;
    } catch {
      if (token !== statusRequestToken) return true;
      brandNotice("连不上本机服务：请关掉题有据再重新打开", "error");
      return false;
    }
    const s = state.status;
    brandNotice("");
    renderUploadAvailability();
    $("m3Button").hidden = !s.m3_available;
    if (s.app_version) $("aboutVersion").textContent = `题有据 ${s.app_version}（本机安装）`;
    renderSettingsModels();
    renderSettingsReady();
    return true;
  }

  function paperSummary(paper) {
    if (ACTIVE_STATUS.has(paper.status)) {
      return QBProgress.processingPresentation(paper).headline;
    }
    if (paper.status === "failed") return paper.recoverable_pause
      ? (paper.status_label || "额度不足，已暂停") : paper.stopped ? "已停止" : "处理失败";
    if (paper.status === "needs_grouping") return "等待确认资料结构";
    const c = paper.counts || {};
    const parts = [paper.demo ? `练习用 · ${c.total || 0} 题` : `${c.total || 0} 题`];
    // 同一个口径：和条子、和审核页的「需要核查」必须是同一个数。
    const { todo } = QBProgress.paperProgress(c);
    if (todo) parts.push(`${todo} 张要看`);
    const settled = c.settled || 0;
    if (settled && settled >= (c.approved || 0)) parts.push(`已入库 ${settled}`);
    else if (c.approved) parts.push(`通过 ${c.approved}${settled ? ` · 入库 ${settled}` : ""}`);
    return parts.join(" · ");
  }

  // A textbook example is called what the book calls it: the printed “例 1”
  // no longer sits in its task text, so the header carries it.
  function questionLabel(q) {
    return q?.source_kind === "example" ? `例 ${q.number}` : `第 ${q.number} 题`;
  }

  function paperDisplayName(paper) {
    const name = typeof paper?.name === "string" ? paper.name.trim() : "";
    return name || paper?.filename || "未命名试卷";
  }

  function miniMeter(paper) {
    const counts = paper.counts || {};
    const { total, todo, done } = QBProgress.paperProgress(counts);
    const bar = el("span", "mini-meter");
    // Don't show a green “done” bar while every card is still waiting for a
    // read. The processing line above already describes that active state.
    if (!total || Number(counts.waiting || 0) >= total) return bar;
    [["done", done, "var(--green-bar)"], ["todo", todo, "var(--amber-bar)"]].forEach(([key, count, color]) => {
      if (!count) return;
      const part = el("span");
      part.dataset.state = key;
      part.style.width = `${(count / total) * 100}%`;
      part.style.background = color;
      bar.append(part);
    });
    // A three-pixel bar has no room for a legend; the hover says which two
    // states it stands for and how many sit in each.
    if (done || todo) bar.title = `不用再看 ${done} · 还要看 ${todo}`;
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
      if (finishedUnseen.has(paper.id)) {
        item.classList.add("fresh");
        link.append(el("span", "fresh-dot", "新"));
      }
      link.append(meta, miniMeter(paper));
      if (paper.id === state.paperId) link.setAttribute("aria-current", "true");
      link.addEventListener("click", () => selectPaper(paper.id));
      item.append(link);
      list.append(item);
    });
  }

  function noteFinishedPapers(previous, papers) {
    QBNotify.justFinished(previous, papers, state.paperId).forEach((paper) => {
      finishedUnseen.add(paper.id);
      toast(QBNotify.finishedMessage(paper, paperDisplayName(paper)), paper.status === "failed" ? "error" : "success",
        { label: "去看看", onClick: () => selectPaper(paper.id) });
    });
    const known = new Set(papers.map((paper) => paper.id));
    [...finishedUnseen].forEach((id) => { if (!known.has(id) || id === state.paperId) finishedUnseen.delete(id); });
    document.title = QBNotify.title(BASE_TITLE, finishedUnseen.size);
  }

  async function loadPapers() {
    try {
      const data = await api("/api/papers");
      const previous = new Map(state.papers.map((paper) => [paper.id, paper.status]));
      state.papers = data.papers;
      if (state.paper && !state.paper.archived && !state.papers.some((paper) => paper.id === state.paper.id)) {
        state.papers.push(state.paper);
      }
      noteFinishedPapers(previous, state.papers);
      renderPaperList();
    } catch (error) {
      toast(error.message, "error");
    }
    clearTimeout(state.listTimer);
    const busy = state.papers.some((paper) => ACTIVE_STATUS.has(paper.status));
    state.listTimer = setTimeout(loadPapers, busy ? 3000 : 15000);
  }

  function renderArchivedPapers() {
    const list = $("archivedPapersList");
    list.replaceChildren(...state.archivedPapers.map((paper) => {
      const item = el("li", "archived-papers-item");
      item.dataset.paperId = paper.id;
      const info = el("div", "archived-papers-info");
      info.append(el("strong", "archived-papers-name", paperDisplayName(paper)),
        el("span", "hint", paperSummary(paper)));
      const actions = el("div", "archived-papers-actions");
      const view = button("查看", "quiet", async () => {
        if (state.paperId !== paper.id && !(await discardEdits())) return;
        $("archivedPapersDialog").close();
        await selectPaper(paper.id);
      });
      view.setAttribute("aria-label", `查看“${paperDisplayName(paper)}”`);
      const restore = button("恢复并打开", "", () => restoreArchivedPaper(paper.id));
      restore.setAttribute("aria-label", `恢复并打开“${paperDisplayName(paper)}”`);
      actions.append(view, restore);
      item.append(info, actions);
      return item;
    }));
    $("archivedPapersMore").hidden = state.archiveNextOffset === null;
  }

  async function loadArchivedPapers(append = false) {
    if (state.archiveLoading) return;
    state.archiveLoading = true;
    const status = $("archivedPapersState");
    status.textContent = "正在加载归档试卷…";
    $("archivedPapersRetry").hidden = true;
    $("archivedPapersMore").disabled = true;
    try {
      const offset = append ? state.archiveNextOffset : 0;
      const data = await api(`/api/papers?archived=only&offset=${offset || 0}`);
      state.archivedPapers = append ? [...state.archivedPapers, ...data.papers] : data.papers;
      state.archiveNextOffset = data.next_offset ?? null;
      renderArchivedPapers();
      status.textContent = state.archivedPapers.length ? "" : "还没有归档的试卷。";
    } catch (error) {
      status.textContent = `归档列表未能加载：${error.message}`;
      $("archivedPapersRetry").hidden = false;
    } finally {
      state.archiveLoading = false;
      $("archivedPapersMore").disabled = false;
    }
  }

  function openArchivedPapers() {
    if (!$("archivedPapersDialog").open) $("archivedPapersDialog").showModal();
    loadArchivedPapers();
  }

  $("archivedPapersButton").addEventListener("click", openArchivedPapers);
  $("archivedPapersMore").addEventListener("click", () => loadArchivedPapers(true));
  $("archivedPapersRetry").addEventListener("click", () => loadArchivedPapers());
  $("restoreCurrentPaper").addEventListener("click", () => {
    if (state.paperId) restoreArchivedPaper(state.paperId);
  });

  // ---------------------------------------------------------------- 当前试卷

  async function selectPaper(id) {
    if (state.paperId !== id && !(await discardEdits())) return;
    if (finishedUnseen.delete(id)) document.title = QBNotify.title(BASE_TITLE, finishedUnseen.size);
    if (state.paperId !== id) {
      cancelPendingPageOpening();
      if ($("pageDialog").open) closePageDialog();
      state.paperId = id;
      state.rendered.clear();
      state.editing.clear();
      state.expanded.clear();
      state.autoExpanded.clear();
      state.filter = "all";
      state.current = null;
      const url = new URL(window.location.href);
      url.searchParams.set("paper", id);
      url.searchParams.delete("document");
      url.searchParams.delete("draft");
      history.replaceState(null, "", url);
      window.scrollTo({ top: 0 });
    }
    renderPaperList();
    // 旧题的卡先留在屏幕上（压暗、不接收点击），等新题到了再整批换掉。
    // 先清空会出现一屏空白，看着像卡住了。
    const cards = $("cards");
    cards.classList.add("is-loading");
    cards.setAttribute("aria-busy", "true");
    try {
      await refreshPaper();
    } finally {
      cards.classList.remove("is-loading");
      cards.removeAttribute("aria-busy");
    }
    if (teaching.active) renderTeach();
  }

  async function clearPaperSelection() {
    if (!(await discardEdits())) return false;
    cancelPendingPageOpening();
    if ($("pageDialog").open) closePageDialog();
    clearTimeout(state.pollTimer);
    state.paperId = null;
    state.paper = null;
    state.questions = [];
    state.current = null;
    state.rendered.clear();
    state.editing.clear();
    state.expanded.clear();
    state.autoExpanded.clear();
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
    return true;
  }

  let paperRefreshToken = 0;
  async function refreshPaper() {
    clearTimeout(state.pollTimer);
    if (!state.paperId) return false;
    const paperId = state.paperId;
    const refreshToken = ++paperRefreshToken;
    let data;
    try {
      data = await api(`/api/papers/${paperId}`);
    } catch (error) {
      if (state.paperId !== paperId || refreshToken !== paperRefreshToken) return false;
      toast(error.message, "error");
      // 本机服务短暂重启或网页一次请求失败时，不能让进度永久停在旧画面。
      if (state.paperId === paperId) state.pollTimer = setTimeout(refreshPaper, 5000);
      return false;
    }
    if (state.paperId !== paperId || refreshToken !== paperRefreshToken) return false;
    state.paper = data.paper;
    $("archivedPaperNotice").hidden = !data.paper.archived;
    state.questions = data.questions.map(normalizeRegionRead);
    const index = state.papers.findIndex((paper) => paper.id === data.paper.id);
    if (index >= 0) { state.papers[index] = data.paper; renderPaperList(); }
    renderPaper();
    if ($("viewerDialog").open) renderViewer();
    void readNewlyCutUpload(paperId);
    const busy = ACTIVE_STATUS.has(state.paper.status)
      || state.questions.some((q) => q.state === "waiting" || q.state === "reading" || q.ocr_pending || regionReadPending(q));
    if (busy) state.pollTimer = setTimeout(refreshPaper, 2500);
    return true;
  }

  // AI 助手通过、还等你核对的题数，接在“全部通过”后面说一句。
  function aiNote(c) {
    return c.ai ? `其中 ${c.ai} 题是 AI 通过的，在“AI 通过”里核对。` : "";
  }

  function counts() {
    const qs = state.questions;
    return {
      all: qs.length,
      todo: qs.filter(needsReview).length,
      attention: qs.filter(needsCheck).length,
      green: qs.filter(needsGeneralReview).length,
      approved: qs.filter(isDone).length,
      ai: qs.filter(isAiApproved).length,
      waiting: qs.filter((q) => state.cropDraftAttention.get(q.id)?.waiting ?? (q.state === "waiting" || q.state === "reading")).length,
      red: qs.filter((q) => state.cropDraftAttention.get(q.id)?.red ?? (!isApproved(q) && q.state === "red")).length,
      unpublished: qs.filter((q) => state.cropDraftAttention.get(q.id)?.unpublished ?? (isApproved(q) && !(q.publication && q.publication.up_to_date))).length
    };
  }

  function renderMeter(c) {
    const meter = $("reviewMeter");
    meter.hidden = !c.all;
    if (!c.all) return;
    const segments = [
      ["approved", c.approved, "已入库", "var(--accent)"],
      ["yellow", c.todo, "需要核查", "var(--amber-bar)"],
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
    $("meterFigure").replaceChildren(el("strong", "", String(c.approved)), document.createTextNode(` / ${c.all} 已入库`));
    $("meterFigure").title = `${c.approved} / ${c.all} 题不用再看（已通过或已入库）`;
    $("meterPercent").textContent = `${Math.round((c.approved / c.all) * 100)}%`;
    $("toolbarPaper").replaceChildren(el("strong", "", paperDisplayName(state.paper || {})),
      document.createTextNode(` · ${c.approved} / ${c.all} 已入库`));
    $("toolbarPaper").title = $("toolbarPaper").textContent;
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
    text.append(icon("check"), document.createTextNode(`全部 ${c.all} 题已处理完。` + aiNote(c)));
    banner.replaceChildren(text);
    const actions = el("span", "done-actions");
    {
      const link = el("a", "button", "去正式题库看看");
      link.href = "/library";
      actions.append(link);
    }
    // Reviewing a batch: offer the next paper that still has cards to look at.
    const next = QBNotify.nextToReview(state.papers, state.paperId);
    if (next) {
      const c2 = next.counts || {};
      const { todo } = QBProgress.paperProgress(c2);
      actions.append(button(`下一份：${paperDisplayName(next)}${todo ? `（${todo} 张要看）` : ""}`,
        c.unpublished ? "" : "primary", () => selectPaper(next.id), "打开下一份还有题卡没通过的试卷"));
    }
    banner.append(actions);
  }

  $("settingsRestoreHints").addEventListener("click", () => {
    setCropGuidanceEnabled(true);
    window.QBShortcutHelp?.restoreHints?.();
    toast("画框和各处操作提示已恢复");
  });
  window.addEventListener("qb:hints-restored", () => {
    if (typeof dialog !== "undefined" && dialog.active) configureCropActions();
  });

  function renderPaper() {
    const paper = state.paper;
    $("emptyState").hidden = true;
    $("paperView").hidden = false;
    $("paperName").textContent = paperDisplayName(paper);
    $("renameNudge").hidden = !looksLikeFileName(paperDisplayName(paper));
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
      if (processing.canReparse) {
        const again = button("重新交给 MinerU", "small", reparsePaper, "MinerU 久久没有结果：不再等这一次，把文件重新上传给 MinerU");
        again.classList.add("processing-reparse");
        statusText.append(again);
      }
    } else if (paper.status === "failed") {
      statusText.textContent = paper.recoverable_pause
        ? (paper.status_label || "额度不足，已暂停") : paper.stopped ? "已停止" : "处理失败";
    } else if (paper.status === "needs_grouping") {
      statusText.textContent = "检测到题号重新开始或页面可能来自不同资料；确认调整页序或拆分任务后才会继续识读。";
    } else if (!c.all) {
      statusText.textContent = paper.pages?.length
        ? "原卷已保存，可以从原卷选取题目。" : "没有题卡";
    } else if (c.todo) {
      statusText.textContent = `${c.all} 道题，${c.todo} 题需要核查。`;
    } else if (c.green) {
      // 和上一句同一个口径：todo 已经归零，剩下的就是不用再看的。
      statusText.textContent = `${c.all} 道题，${c.approved} 题已入库。`;
    } else {
      statusText.textContent = `全部 ${c.all} 题已处理完。` + aiNote(c);
    }
    if (!isProcessing && paper.parse_mode === "native") {
      const manualPages = (paper.processing_plan?.pages || []).filter((page) => page.mode === "manual");
      if (manualPages.length) statusText.append(el("span", "processing-detail", `${manualPages.length} 页尚未自动切出题目，可从原卷补齐。`));
      const warnings = paper.processing_plan?.warnings;
      if (Array.isArray(warnings) && warnings.length) statusText.append(el("span", "processing-detail", warnings.map(String).join("；")));
    }
    // Only known completed local/cloud route explanations move to history.
    // Failures, missing pages, structure issues and unknown warnings stay visible.
    if (paper.processing_plan?.fallback_reason && !QBReviewGuidance.historicalParseReason(paper)) {
      statusText.append(el("span", "processing-detail", String(paper.processing_plan.fallback_reason)));
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
      // 一张题卡都没有 = 自动切题什么也没切出来：这时最显眼的按钮该是“继续 AI
      // 切题”，它复用已存的解析结果，不重新上传、不再花一次额度。而本地为什么
      // 没切出来，比“处理失败”更值得读，所以先说那句。
      const cutProducedNothing = (paper.error || "").startsWith("自动切题没有切出");
      const actions = el("span", "error-actions");
      const recoverable = cutProducedNothing && QBProgress.canContinueAiCut(paper);
      if (recoverable) {
        actions.append(button("继续 AI 切题", "small primary", () => continueAiCut()));
        actions.append(button("改为手工切题", "small", () => switchToManual()));
      } else {
        actions.append(button("继续整理", "small primary", () => switchToManual()));
        if (cutProducedNothing && QBProgress.canContinueAiCut(paper)) {
          actions.append(button("继续 AI 切题", "small", () => continueAiCut()));
        }
        actions.append(button(paper.parse_mode === "mineru" ? "重试自动处理" : "重试准备原卷", "small", () => retryPaper()));
      }
      if (paper.kind === "pdf" && paper.material_type !== "book") {
        actions.append(button("按教材重试", "small", () => retryPaper("book")));
      }
      actions.append(button("删除任务", "small danger", deletePaper));
      error.replaceChildren(
        el("span", "", cutProducedNothing
          ? (paper.processing_plan?.fallback_reason || paper.error || "处理失败")
          : (paper.error || "处理失败")),
        actions);
    }
    renderMeter(c);
    renderDoneBanner(c);
    const structureBlocked = paper.status === "needs_grouping";
    renderPublishButton(c, structureBlocked);
    const notes = paper.notes || [];
    // 处理记录集中放在设置中；需要立即处理的失败和结构问题仍保留主界面提示。
    $("notesBox").hidden = true;
    $("notesList").replaceChildren(...notes.map((note) => el("li", "", note)));
    $("manualProcessing").hidden = structureBlocked || !paper.pages?.length;
    $("resegment").hidden = !["ready", "failed"].includes(paper.status);
    const canReorder = Boolean(paper.photos) && (paper.pages || []).length > 1 && ["ready", "failed", "needs_grouping"].includes(paper.status);
    $("pageOrder").hidden = !canReorder;
    syncTrashControls();
    $("toolsMenu").hidden = $("manualProcessing").hidden && $("resegment").hidden && $("pageOrder").hidden && $("questionTrash").hidden;
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
    renderCutReadingStage();
    renderFilters(c);
    renderCards();
  }

  const cutReadingRequests = new Set();
  const cutReadingStops = new Set();
  const cutReadingErrors = new Map();
  const cutReadingStopErrors = new Map();
  const directImageReview = new Set();
  const questionReadingRequests = new Set();
  const newUploadReadContinuations = new Set();

  function paperReadSubmissionPending(paperId = state.paperId) {
    return cutReadingRequests.has(paperId) || cutReadingStops.has(paperId) || (state.paperId === paperId
      && state.questions.some((q) => questionReadingRequests.has(q.id)));
  }

  function renderReadingControls() {
    state.questions.forEach((q) => { if (q.body_mode === "source_image" || q.processing_mode === "manual") state.rendered.delete(q.id); });
    renderCards();
    renderCutReadingStage();
  }

  function renderCutReadingStage() {
    const paper = state.paper;
    const host = $("cutReadingStage");
    const summary = QBCutReading.cutReadingSummary(state.questions);
    host.hidden = !QBCutReading.showCutReadingStage(paper, summary);
    if (host.hidden) return;
    if (summary.pending) cutReadingErrors.delete(paper.id);
    if (!summary.pending) cutReadingStopErrors.delete(paper.id);
    const stage = directImageReview.has(paper.id) && !summary.pending ? 3 : summary.stage;
    host.dataset.stage = String(stage);
    $("cutReadingSteps").replaceChildren(...["切题", "AI 识读", "审核"].map((label, index) => {
      const step = el("li", index + 1 === stage ? "current" : index + 1 < stage ? "done" : "");
      step.append(el("b", "", String(index + 1)), document.createTextNode(label));
      if (index + 1 === stage) step.setAttribute("aria-current", "step");
      return step;
    }));
    $("cutReadingTitle").textContent = stage === 1 ? "先切出每道题"
      : summary.pending ? `${summary.pending} 道题正在 AI 识读`
        : stage === 2 ? `${summary.eligibleIds.length} 道题尚未识读` : "对照原卷核对文字和配图，审核后入库";
    $("cutReadingDetail").textContent = stage === 1
      ? "框出完整题目，跨页或跨栏可以添加多个片段。切题只保存原图范围，不调用 AI。"
      : summary.pending ? "AI 直接读取已保存的题目范围，无需重新框选。识读成功后文字会显示在原图旁，接着核对配图并审核。"
        : stage === 2 ? "结束切题后自动识读。失败或停止后，可点“识读未完成题目”主动重试；不会自动重新提交。原图也可以直接审核。"
          : "检查文字是否完整、题型及配图是否正确。读错可改字或重读这题；标签与参考答案仍默认关闭。";
    const actions = $("cutReadingActions");
    actions.replaceChildren();
    if (stage === 1) {
      const cut = button(manualSwitches.has(paper.id) ? "正在准备原卷…" : "手工切题", "primary", openManualCut,
        "从原卷框出每道题，完成切题后自动识读，再核对文字和配图");
      cut.disabled = manualSwitches.has(paper.id) || aiCutContinuations.has(paper.id);
      actions.append(cut);
    } else if (summary.eligibleIds.length) {
      const read = button(`识读未完成题目（${summary.eligibleIds.length} 题）`, "primary", () => readCutQuestions());
      read.disabled = paperReadSubmissionPending(paper.id) || paper.status !== "ready";
      if (paper.status !== "ready") read.title = "请先继续手工整理或重试恢复这份资料，再开始 AI 识读。";
      if (cutReadingRequests.has(paper.id)) read.textContent = "正在提交识读……";
      actions.append(read);
    }
    if (summary.pending) {
      const stop = button(cutReadingStops.has(paper.id) ? "正在停止识读……" : "停止识读", "", stopCutReading, "停止本机等待，保留题目裁片和已经成功的文字；不再采用本轮迟到结果");
      stop.disabled = paperReadSubmissionPending(paper.id);
      actions.append(stop);
    }
    if (stage !== 1 && !summary.pending && stage !== 3) {
      actions.append(button("直接原图审核", "quiet", () => { directImageReview.add(paper.id); renderCutReadingStage(); focusCutReview(); }));
    }
    if (stage === 3) actions.append(button("开始审核", "", () => focusCutReview()));
    if (stage !== 1) {
      const cut = button(manualSwitches.has(paper.id) ? "正在准备原卷…" : "继续手工切题", "quiet", openManualCut);
      cut.disabled = manualSwitches.has(paper.id) || aiCutContinuations.has(paper.id);
      actions.append(cut);
    }
    const stopError = cutReadingStopErrors.get(paper.id);
    const error = stopError || cutReadingErrors.get(paper.id);
    $("cutReadingError").hidden = !error;
    $("cutReadingError").textContent = error || "";
    $("cutReadingSettings").hidden = !error || Boolean(stopError) || !/(配置.*(?:读题|看图)|(?:读题|看图).*(?:配置|密钥))/.test(error);
  }

  function openManualCut() {
    if (manualSwitches.has(state.paperId) || aiCutContinuations.has(state.paperId)) return;
    if (state.paper?.status === "ready") openPageDialog("new"); else void switchToManual();
  }

  function focusCutReview(questionId = null) {
    state.filter = "all";
    const q = questionById(questionId) || state.questions.find((item) => !isApproved(item)) || state.questions[0];
    if (q) setExpanded(q.id, true);
    renderPaper();
    if (q) setCurrent(q.id, { scroll: true, focus: true });
  }

  async function enterCutReadingStage(paperId = state.paperId, questionIds = null) {
    if (!paperId || state.paperId !== paperId) return false;
    if ($("pageDialog").open) $("pageDialog").close();
    directImageReview.delete(paperId);
    const refreshed = await refreshPaper();
    if (!refreshed || state.paperId !== paperId) return false;
    if (state.paper?.demo) {
      focusCutReview();
      teach({ type: "cutComplete" });
      toast("切题已完成，示例保留原图。对照后即可审核；本次练习未调用 AI。", "success");
      return true;
    }
    await readCutQuestions(questionIds);
    if (state.paperId !== paperId) return false;
    $("cutReadingStage").scrollIntoView({ behavior: "smooth", block: "center" });
    $("cutReadingTitle").focus({ preventScroll: true });
    return true;
  }

  async function readNewlyCutUpload(paperId) {
    if (!newUploadReadContinuations.has(paperId) || state.paperId !== paperId || !state.paper) return;
    if (state.paper.status === "failed" || state.paper.archived || state.paper.demo) {
      newUploadReadContinuations.delete(paperId); return;
    }
    if (state.paper.status !== "ready" || paperReadSubmissionPending(paperId)) return;
    // Only this window's newly uploaded, locally cut paper continues once.
    // Reopening a historic paper or polling a failed reading cannot charge again.
    newUploadReadContinuations.delete(paperId);
    if (state.paper.parse_mode !== "native") return;
    const ids = QBCutReading.cutReadingSummary(state.questions).eligibleIds;
    if (!ids.length) return;
    if (!state.status?.reader || state.status.assistant_mode) {
      cutReadingErrors.set(paperId, "题目已切好并保留原图。自动 AI 识读需要看图读题服务，请在设置 → API 配置中配置；也可改字或直接原图审核。");
      renderCutReadingStage();
      return;
    }
    await readCutQuestions(ids);
  }

  async function readCutQuestions(questionIds = null) {
    const paperId = state.paperId;
    if (!paperId || paperReadSubmissionPending(paperId) || state.paper?.demo || state.paper?.status !== "ready") return;
    const body = QBCutReading.cutReadingRequest(state.questions, questionIds, state.paper?.processing_plan?.revision);
    if (!body.question_ids.length) return;
    cutReadingRequests.add(paperId);
    cutReadingErrors.delete(paperId);
    cutReadingStopErrors.delete(paperId);
    directImageReview.delete(paperId);
    renderReadingControls();
    try {
      const data = await QBRegionWait.boundedRequest((signal) => api(`/api/papers/${paperId}/read-cut-questions`, {
        method: "POST", signal, body
      }), { timeoutMs: 30000 });
      if (state.paperId !== paperId) return;
      await refreshPaper();
      toast(data.message || (data.queued ? `已提交 ${data.queued} 道题识读，文字显示后请核对并审核` : "没有需要新识读的题目"), data.queued ? "success" : "");
    } catch (error) {
      if (state.paperId !== paperId) return;
      const message = error.name === "TimeoutError" ? "识读提交结果尚未确认，请稍后查看题卡。保存的范围与原图都保留。" : error.message;
      cutReadingErrors.set(paperId, message);
      toast(message, "error");
      if (error.name === "TimeoutError") void refreshPaper();
    } finally {
      cutReadingRequests.delete(paperId);
      if (state.paperId === paperId) renderReadingControls();
    }
  }

  async function stopCutReading() {
    const paperId = state.paperId;
    if (!paperId || paperReadSubmissionPending(paperId) || !QBCutReading.cutReadingSummary(state.questions).pending) return;
    cutReadingStops.add(paperId);
    cutReadingStopErrors.delete(paperId);
    renderReadingControls();
    try {
      const data = await QBRegionWait.boundedRequest((signal) => api(`/api/papers/${paperId}/stop-cut-reading`, {
        method: "POST", body: {}, signal
      }), { timeoutMs: 30000 });
      if (state.paperId !== paperId) return;
      cutReadingErrors.delete(paperId);
      await refreshPaper();
      toast(data.message || "已停止本机识读等待，原图裁片和成功的读法保留；本机不再采用本轮迟到结果。", "success");
    } catch (error) {
      if (state.paperId !== paperId) return;
      const message = error.name === "TimeoutError" ? "停止识读的结果尚未确认，请稍后查看状态或重试；原图裁片保留。" : `未能停止识读：${error.message}`;
      cutReadingStopErrors.set(paperId, message);
      toast(message, "error");
      if (error.name === "TimeoutError") void refreshPaper();
    } finally {
      cutReadingStops.delete(paperId);
      if (state.paperId === paperId) renderReadingControls();
    }
  }

  function setFilter(key) {
    teach({ type: "filter", key });
    if (state.filter === key) return;
    state.filter = key;
    renderPaper();
  }

  function renderFilters(c) {
    const box = $("filters");
    box.replaceChildren();
    FILTERS.forEach((filter, index) => {
      const active = state.filter === filter.key;
      if (filter.optional && !c[filter.key] && !active) return;
      const tab = el("button", `filter${active ? " active" : ""}${filter.key === "todo" && c.todo ? " attention" : ""}`);
      tab.type = "button";
      tab.setAttribute("role", "tab");
      tab.id = `filter-${filter.key}`;
      tab.dataset.filter = filter.key;
      tab.title = `${filter.label}（${index + 1}）${filter.title ? ` · ${filter.title}` : ""}`;
      tab.setAttribute("aria-selected", String(active));
      tab.setAttribute("aria-controls", "cards");
      tab.tabIndex = active ? 0 : -1;
      tab.append(el("span", "", filter.label), el("span", "filter-count", String(c[filter.key])));
      tab.addEventListener("click", (event) => {
        setFilter(filter.key);
        // A mouse click leaves focus on the tab, where Space and Enter would
        // press the tab again; hand it to the first card so the review keys work.
        if (event.detail > 0) {
          const first = state.questions.find(visible);
          if (first) requestAnimationFrame(() => setCurrent(first.id, { focus: true }));
        }
      });
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
    if (state.filter === "todo" || state.filter === "green") return needsReview(q);
    // 「已入库」就是上面两段里的“不用再看”：打了勾的，和题库里已经原样放着的。
    if (state.filter === "approved") return isDone(q);
    if (state.filter === "ai") return isAiApproved(q);
    return true;
  }

  function questionDeleteBlockReason(q) {
    if (!q) return "找不到这道题";
    if (state.paper?.status !== "ready") return "任务处理完成后才能删除题卡";
    if (q.state === "waiting" || q.state === "reading") return "这道题仍在识读，完成后才能移到回收站";
    if (q.publication) return "这道题已经入库，为保留来源和版本记录，不能从审题任务中删除";
    return "";
  }

  // 题号左边的方框：打勾就是“标记通过”，已通过的再点一下就是撤销。
  function approvalTick(q) {
    const approved = isHumanApproved(q);
    const byAi = isAiApproved(q);
    const blocked = !approved && !byAi && figureBlocksApproval(q);
    const typeBlocked = !approved && !byAi && !blocked && typeBlocksApproval(q);
    const tick = el("button", `card-tick${byAi ? " ai" : ""}`);
    tick.type = "button";
    tick.setAttribute("aria-pressed", byAi ? "mixed" : String(approved));
    tick.setAttribute("aria-label", approved ? `撤销第 ${q.number} 题的通过`
      : byAi ? `确认第 ${q.number} 题（${agentLabel(q)} 已通过）` : `第 ${q.number} 题标记通过`);
    tick.title = approved ? "已标记通过；再点一下撤销（U）"
      : byAi ? `${agentLabel(q)} 对照原卷后打的勾，你还没核对。核对无误就点一下，变成你的通过（Enter）；不对就按 U 撤销`
        : blocked ? "配图还没处理好，点一下去处理"
          : typeBlocked ? "题型还没定，点一下去选题型"
          : canApprove(q) ? "对照原卷无误就打勾：标记通过（Enter）"
            : q.state === "red" ? "识读失败的题需先改字或重读，不能直接通过" : "请等待识读完成";
    tick.disabled = !(approved || byAi || blocked || typeBlocked || canApprove(q));
    tick.append(icon("check"));
    tick.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      if (approved) approveQuestion(q, false);
      else if (blocked) focusFigureReview(q);
      else if (typeBlocked) focusTypePicker(q);
      else approveQuestion(q, true);
    });
    return tick;
  }

  // 题号旁边的题型：读完的题可以直接改（只改题型，改了要重新标记通过）。
  function typePicker(q) {
    if (!(q.state === "green" || q.state === "yellow")) return el("span", "qtype", TYPE_NAMES[q.question_type] || q.question_type);
    const undecided = typeBlocksApproval(q);
    const select = el("select", `qtype qtype-select${undecided ? " undecided" : ""}`);
    select.setAttribute("aria-label", `第 ${q.number} 题的题型`);
    select.title = undecided ? "题型没读出来：选一下题型才能通过" : "改题型（改了要重新标记通过）";
    if (undecided) {
      const placeholder = new Option("题型未定 · 请选", "unknown");
      placeholder.disabled = true;
      select.append(placeholder);
    }
    Object.entries(TYPE_NAMES).forEach(([value, label]) => {
      if (value !== "unknown") select.append(new Option(label, value));
    });
    select.value = undecided ? "unknown" : q.question_type;
    select.addEventListener("click", (event) => event.stopPropagation());
    select.addEventListener("keydown", (event) => event.stopPropagation());
    select.addEventListener("change", () => setQuestionType(q, select.value));
    return select;
  }

  async function setQuestionType(q, kind) {
    const wasApproved = isApproved(q) || isAiApproved(q);
    try {
      const data = await api(`/api/questions/${q.id}/type`, { method: "POST", body: { question_type: kind } });
      applyQuestion(data);
      toast(wasApproved ? `第 ${q.number} 题改成了${TYPE_NAMES[kind]}；题目内容变了，请重新标记通过`
        : `第 ${q.number} 题是${TYPE_NAMES[kind]}了，核对无误就可以打勾通过`, "success");
      setCurrent(q.id, { focus: true });
    } catch (error) {
      toast(error.message, "error");
      renderCards();
    }
  }

  function focusTypePicker(q) {
    if ($("viewerDialog").open) $("viewerDialog").close();
    const select = document.querySelector(`.card[data-id="${q.id}"] .qtype-select`);
    if (!select) { toast("这道题读完以后才能选题型", "error"); return; }
    setCurrent(q.id);
    select.scrollIntoView({ block: "center", behavior: "smooth" });
    select.focus({ preventScroll: true });
    select.classList.remove("attention");
    requestAnimationFrame(() => select.classList.add("attention"));
    window.setTimeout(() => select.classList.remove("attention"), 1400);
    toast("题型没读出来：在题号旁边选一下题型（单选、多选、填空、判断或解答）");
  }

  function renderCards() {
    const container = $("cards");
    const shown = state.questions.filter(visible);
    const keep = new Set(shown.map((q) => q.id));
    // Look each card up in a map: a selector search per card is slow for a 600-card book.
    const existing = new Map();
    [...container.children].forEach((child) => {
      const id = Number(child.dataset.id);
      if (!keep.has(id) && !state.editing.has(id)) { child.remove(); state.rendered.delete(id); }
      else if (child.dataset.id && !existing.has(id)) existing.set(id, child);
    });
    let previous = null;
    shown.forEach((q) => {
      const signature = JSON.stringify([q, state.expanded.has(q.id), state.paper?.pages_version]);
      let card = existing.get(q.id) || null;
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
      if (!state.questions.length && state.paper?.pages?.length && !ACTIVE_STATUS.has(state.paper.status)) {
        empty.append(el("strong", "", "原卷已保存"), el("span", "", "框出题目范围，跨栏或跨页可以继续添加片段。完成切题后自动识读已保存的题目。"));
        // The cutting panel owns the main entry. The empty state is a fallback
        // only when the panel is absent, independent of the previous render.
        if (!QBCutReading.showCutReadingStage(state.paper, QBCutReading.cutReadingSummary(state.questions))) {
          const cut = button("手工切题", "primary", openManualCut);
          cut.id = "emptyManualCut";
          cut.disabled = manualSwitches.has(state.paperId) || aiCutContinuations.has(state.paperId);
          empty.append(cut);
        }
      } else if (!state.questions.length) empty.append(el("strong", "", "题卡还没生成"), el("span", "", "原卷处理完成后会显示题卡，也可以手工补题。"));
      else if (state.filter === "todo" || state.filter === "green") empty.append(el("strong", "", "没有需要核查的题卡"), el("span", "", "本卷题目已核查完毕。"));
      else empty.append(el("strong", "", "这一栏没有题卡"));
      container.append(empty);
    } else container.querySelectorAll(".cards-empty").forEach((node) => node.remove());
    R.fitOptions(container);
    const currentShown = state.current !== null && shown.some((q) => q.id === state.current);
    markReading();
    // Opening, collapsing or filtering moves cards: pick the card being read again.
    if (state.focus) requestAnimationFrame(currentShown ? markReading : followReading);
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
    // A card the reader jumped to (J/K, N, 通过后下一张) stays lit until they scroll themselves.
    if (card && scroll) state.followHold = true;
    markReading();
    if (card && scroll) card.scrollIntoView({ behavior: "smooth", block: "start" });
    if (card && focus) card.focus({ preventScroll: true });
  }

  // ---------------------------------------------------------------- 专注

  // 专注：正在看的这道题正常显示，其余题暗下来。只有展开的整张卡才算
  // “在看”；已通过收起的一行题是看完的，不会被点亮，也不会让别的题变暗。
  function viewTop() {
    // 全屏时顶栏收起，高度是 0，不能当成没取到。
    const bar = document.querySelector(".topbar");
    return (bar ? bar.offsetHeight : 56) + ($("toolbar")?.offsetHeight || 0);
  }

  function onScreen(card) {
    const rect = card.getBoundingClientRect();
    return rect.bottom > viewTop() && rect.top < window.innerHeight;
  }

  // “正在看的”跟着滚动走：取压在阅读线上的那张整卡（见 QBFocus）；阅读线落在
  // 空隙或收起的题上，就取离它最近的整卡。大半已经滚到工具栏后面（或屏幕下面）、
  // 只露出一小截的整卡不算在看：都正常显示，不变暗。
  function wellInView(card) {
    return QBFocus.mostlyVisible(card.getBoundingClientRect(), viewTop(), window.innerHeight);
  }

  function readingCard() {
    const top = viewTop();
    const bottom = window.innerHeight;
    const height = bottom - top;
    const cards = cardNodes().filter((card) => !card.classList.contains("compact") && wellInView(card));
    if (!cards.length || height <= 0) return null;
    const page = document.scrollingElement || document.documentElement;
    const line = QBFocus.readingLine({ top, bottom, scrollY: window.scrollY, below: page.scrollHeight - bottom - window.scrollY });
    return cards[QBFocus.nearestToLine(cards.map((card) => card.getBoundingClientRect()), line)] || null;
  }

  // 变暗只在“正在看一张整卡”时：当前卡收起了、离开了阅读线（又没在跳过去
  // 的路上），或者屏幕上只剩收起的题，就都正常显示。
  function markReading() {
    const container = $("cards");
    const card = state.current !== null ? container.querySelector(`.card[data-id="${state.current}"]`) : null;
    const reading = Boolean(state.focus && card && !card.classList.contains("compact") && (state.followHold || wellInView(card)));
    container.classList.toggle("reading", reading);
  }

  // 自己滚动（滚轮、触摸、翻页键、拖滚动条）之后才跟着换；跳到某张卡的平滑滚动不算。
  const releaseFollow = () => { state.followHold = false; };
  window.addEventListener("wheel", releaseFollow, { passive: true });
  window.addEventListener("touchmove", releaseFollow, { passive: true });
  window.addEventListener("keydown", (event) => {
    if (["PageUp", "PageDown", "Home", "End", "ArrowUp", "ArrowDown"].includes(event.key)) releaseFollow();
  });
  document.addEventListener("pointerdown", (event) => {
    if (event.target === document.documentElement || event.clientX >= document.documentElement.clientWidth) releaseFollow();
  });

  function followReading() {
    if (!state.focus || $("paperView").hidden || anyDialogOpen()) return;
    if (!state.followHold) {
      const card = readingCard();
      const id = card ? Number(card.dataset.id) : null;
      if (id !== null && id !== state.current) setCurrent(id);
    }
    markReading();
  }

  function setFocus(on) {
    state.focus = on;
    writePref("qb-focus", on ? "1" : "0");
    $("focusToggle").setAttribute("aria-pressed", String(on));
    if ($("settingsFocus")) $("settingsFocus").checked = on;
    $("cards").classList.toggle("focus-mode", on);
    const card = state.current !== null ? $("cards").querySelector(`.card[data-id="${state.current}"]`) : null;
    if (on && !(card && onScreen(card))) followReading();
    markReading();
  }

  // 全屏审核：收起顶栏、试卷列表和试卷信息，窗口也尽量铺满屏幕。浏览器不让
  // 全屏时（或被 Esc 退出后），界面仍可以单独收起。
  function setReviewFullscreen(on) {
    const root = document.documentElement;
    if (root.classList.contains("review-fullscreen") === on) return;
    const card = state.current !== null ? document.querySelector(`.card[data-id="${state.current}"]`) : null;
    root.classList.toggle("review-fullscreen", on);
    $("fullscreenToggle").setAttribute("aria-pressed", String(on));
    $("fullscreenLabel").textContent = on ? "退出全屏" : "全屏";
    if (on && !document.fullscreenElement && root.requestFullscreen) {
      root.requestFullscreen({ navigationUI: "hide" }).catch(() => {});
    } else if (!on && document.fullscreenElement && document.exitFullscreen) {
      document.exitFullscreen().catch(() => {});
    }
    // Keep the card being read where the reader is.
    if (card) {
      state.followHold = true;
      requestAnimationFrame(() => card.scrollIntoView({ block: "start" }));
    }
  }

  document.addEventListener("fullscreenchange", () => {
    if (!document.fullscreenElement) setReviewFullscreen(false);
  });
  $("fullscreenToggle").addEventListener("click", () =>
    setReviewFullscreen(!document.documentElement.classList.contains("review-fullscreen")));

  let followFrame = 0;
  window.addEventListener("scroll", () => {
    if (!state.focus || followFrame) return;
    followFrame = requestAnimationFrame(() => { followFrame = 0; followReading(); });
  }, { passive: true });
  $("focusToggle").addEventListener("click", () => setFocus(!state.focus));

  // 键盘移动：当前卡还在屏幕里就从它往前/往后走一张；
  // 已经滚走了，就先落到屏幕里最上面那张。
  // 刚用 J/K 跳过去、页面还在平滑滚动时，目标卡可能还没进屏幕：这时仍从它
  // 接着走（state.followHold），否则连按会被拉回屏幕最上面那张，来回跳。
  function moveCurrent(step) {
    const cards = cardNodes();
    if (!cards.length) return;
    const top = viewTop();
    const index = cards.findIndex((card) => Number(card.dataset.id) === state.current);
    let next;
    if (index >= 0) next = state.followHold || onScreen(cards[index]) ? index + step : -1;
    else next = -1;
    if (next === -1 && !(index >= 0 && index + step === -1)) {
      next = cards.findIndex((card) => card.getBoundingClientRect().bottom > top + 24);
      if (next < 0) next = cards.length - 1;
    }
    next = Math.min(cards.length - 1, Math.max(0, next));
    const nextId = Number(cards[next].dataset.id);
    autoExpandOnMove(nextId);
    setCurrent(nextId, { scroll: true, focus: true });
  }

  function moveNextCard() {
    const cards = cardNodes();
    if (!cards.length) return;
    const currentIndex = cards.findIndex((card) => Number(card.dataset.id) === state.current);
    // N always advances from the selected card, even if a focus action or
    // scrolling temporarily moved it outside the viewport. Only an unselected
    // list needs the first visible card as its starting point.
    let from = currentIndex >= 0 ? currentIndex
      : cards.findIndex((card) => card.getBoundingClientRect().bottom > viewTop() + 24);
    if (from < 0) from = cards.length - 1;
    const next = cards[from + 1];
    if (!next) { toast("已经是当前列表的最后一题"); return; }
    const nextId = Number(next.dataset.id);
    autoExpandOnMove(nextId);
    // Update immediately: a second separate press must advance from this card,
    // even if the scroll animation or an expansion has not painted yet.
    setCurrent(nextId, { scroll: true, focus: true });
  }

  // N 找的是"需要核查"的题，不是"下一张卡"。已经通过、不用你再看第二眼的题
  // 一律跳过。从当前题往后找，末尾再绕回前面，回头补核查也不用换键。
  function focusNextReview() {
    const cards = cardNodes();
    if (!cards.length) return;
    const currentIndex = cards.findIndex((card) => Number(card.dataset.id) === state.current);
    let from = currentIndex >= 0 ? currentIndex
      : cards.findIndex((card) => card.getBoundingClientRect().bottom > viewTop() + 24);
    if (from < 0) from = cards.length - 1;
    for (let step = 1; step <= cards.length; step += 1) {
      const card = cards[(from + step) % cards.length];
      const question = questionById(Number(card.dataset.id));
      if (!question || !needsReview(question)) continue;
      const nextId = Number(card.dataset.id);
      autoExpandOnMove(nextId);
      setCurrent(nextId, { scroll: true, focus: true });
      return;
    }
    // 这一屏没有，不等于整卷没有：当前筛选可能把需要核查的题藏起来了。
    const hidden = state.questions.filter(needsReview).length;
    const label = FILTERS.find((filter) => filter.key === state.filter)?.label || "当前";
    toast(hidden ? `「${label}」这一栏里没有需要核查的题，另外 ${hidden} 道在别的栏目里。`
      : "这一卷没有需要核查的题了。");
  }

  // ---------------------------------------------------------------- 展开 / 收起

  // 只有人工通过的题会收起；AI 通过的题等你核对，一直展开。
  function canCollapse(q) {
    return Boolean(q) && isApproved(q) && !isAiApproved(q);
  }

  // auto：J/K 自动展开的，离开时收回。手动展开（点“展开”、O 键、改字）的不收回。
  function setExpanded(id, on, { auto = false } = {}) {
    if (on) state.expanded.add(id); else state.expanded.delete(id);
    if (on && auto) state.autoExpanded.add(id); else state.autoExpanded.delete(id);
  }

  // J/K 跳到一张收起的题：展开它；上一张自动展开的收回去，页面不会越翻越长。
  function autoExpandOnMove(nextId) {
    let changed = false;
    [...state.autoExpanded].forEach((id) => {
      if (id === nextId) return;
      setExpanded(id, false);
      changed = true;
    });
    const q = questionById(nextId);
    if (state.autoExpand && canCollapse(q) && !state.expanded.has(nextId)) {
      setExpanded(nextId, true, { auto: true });
      changed = true;
    }
    if (changed) renderCards();
  }

  // O：展开 / 收起当前这张；Shift+O：已通过的题全部展开 / 全部收起。
  function toggleExpanded(q) {
    if (!q) return;
    if (!canCollapse(q)) {
      toast(isAiApproved(q) ? "AI 通过的题等你核对，一直是展开的" : "没通过的题一直是展开的，通过以后才会收起");
      return;
    }
    setExpanded(q.id, !state.expanded.has(q.id));
    renderCards();
    setCurrent(q.id, { scroll: true, focus: true });
  }

  function toggleAllExpanded() {
    const ids = state.questions.filter((item) => visible(item) && canCollapse(item)).map((item) => item.id);
    if (!ids.length) { toast("这里还没有已通过、可以收起的题"); return; }
    const expand = ids.some((id) => !state.expanded.has(id));
    ids.forEach((id) => setExpanded(id, expand));
    renderCards();
    toast(expand ? `已展开 ${ids.length} 道已通过的题，再按 Shift+O 全部收起` : "已通过的题都收起了");
    if (state.current !== null && document.querySelector(`.card[data-id="${state.current}"]`)) {
      setCurrent(state.current, { scroll: true });
    }
  }

  function nextToReview(fromQuestion, { onlyCheck = false } = {}) {
    const candidates = state.questions.filter((q) => visible(q) && !isHumanApproved(q) && (!onlyCheck || needsCheck(q)));
    if (!candidates.length) return null;
    const after = fromQuestion ? candidates.find((q) => q.number > fromQuestion.number) : null;
    return after || candidates[0];
  }

  function goTo(q) {
    if (!q) return;
    if (!visible(q)) setFilter("all");
    requestAnimationFrame(() => setCurrent(q.id, { scroll: true, focus: true }));
  }

  function openShortcutHelp(scene = "review", options = {}) {
    if (!window.QBShortcutHelp?.open(scene, { aiFilter: state.questions.some(isAiApproved), ...options })) $("keysDialog").showModal();
  }

  document.addEventListener("keydown", (event) => {
    if (event.defaultPrevented || event.ctrlKey || event.metaKey || event.altKey
      || event.isComposing || event.keyCode === 229) return;
    const target = event.target;
    if (QBUpload.isEditingTarget(target) || target.closest?.(".editor")) return;
    if ($("viewerDialog").open) {
      if (!document.querySelector('dialog[open]:not(#viewerDialog)')) viewerKey(event);
      return;
    }
    if (anyDialogOpen()) return;
    if ($("paperView").hidden) {
      if (event.key === "?") { event.preventDefault(); openShortcutHelp(); }
      return;
    }
    const onControl = target.closest?.("button, a, summary");
    const q = questionById(state.current);
    const key = event.key.length === 1 ? event.key.toLowerCase() : event.key;
    switch (key) {
      case "j": event.preventDefault(); moveCurrent(1); break;
      case "k": event.preventDefault(); moveCurrent(-1); break;
      case "o":
        event.preventDefault();
        if (event.shiftKey) toggleAllExpanded(); else toggleExpanded(q);
        break;
      case "Enter":
        if (event.repeat) { event.preventDefault(); return; }
        if (onControl || !q || event.shiftKey) return;
        event.preventDefault();
        if (isHumanApproved(q)) moveNextCard();
        else if (isAiApproved(q) || canApprove(q)) approveQuestion(q, true);
        else if (figureBlocksApproval(q)) focusFigureReview(q);
        else if (typeBlocksApproval(q)) focusTypePicker(q);
        else toast(q.state === "red" ? "识读失败的题需先改字或重读，不能直接通过" : "请等待识读完成", "error");
        break;
      case " ":
        if (onControl) return;
        if (!q) return;
        event.preventDefault(); openViewer(q); break;
      case "u":
        if (!event.repeat && q && isApproved(q)) { event.preventDefault(); approveQuestion(q, false); }
        break;
      case "e":
        if (q) {
          event.preventDefault();
          const card = document.querySelector(`.card[data-id="${q.id}"]`);
          if (card?.classList.contains("compact")) { state.expanded.add(q.id); renderCards(); }
          state.autoExpanded.delete(q.id);    // 正在改的题，离开时不收回
          const fresh = document.querySelector(`.card[data-id="${q.id}"]`);
          if (fresh) openEditor(fresh, q);
        }
        break;
      case "r": if (q) { event.preventDefault(); openPageDialog("regions", q); } break;
      case "f": if (q) { event.preventDefault(); openPageDialog(q.body_mode === "source_image" ? "regions" : "figures", q); } break;
      case "n":
        event.preventDefault();
        if (!event.repeat) focusNextReview();
        break;
      case "l": event.preventDefault(); setLens(!state.lens); toast(state.lens ? "放大镜已打开" : "放大镜已关闭"); break;
      case "z": event.preventDefault(); setFocus(!state.focus); toast(state.focus ? "专注已打开：其余题暗下来" : "专注已关闭"); break;
      case "1": case "2": case "3": case "4":
        event.preventDefault(); setFilter(FILTERS[Number(key) - 1].key); break;
      case "Escape":
        if (document.documentElement.classList.contains("review-fullscreen")) { event.preventDefault(); setReviewFullscreen(false); }
        break;
      case "q":
        event.preventDefault();
        setReviewFullscreen(!document.documentElement.classList.contains("review-fullscreen"));
        break;
      case "?": event.preventDefault(); openShortcutHelp(); break;
      default: break;
    }
  });

  // ---------------------------------------------------------------- 原卷截图

  const PREVIEW_LONG_SIDE = 2000;

  const SPOT_DIGITS = "①②③④⑤⑥⑦⑧⑨⑩";

  function spotNumber(n) {
    return SPOT_DIGITS[n - 1] || String(n);
  }

  // One box per MinerU line, labelled with every spot in it (②③④ on the same line).
  function spotBoxes(spots) {
    const boxes = new Map();
    (Array.isArray(spots) ? spots : []).forEach((spot) => {
      if (!Array.isArray(spot?.bbox) || spot.bbox.length !== 4 || !Number.isInteger(spot.page_idx)) return;
      const key = `${spot.page_idx}:${spot.bbox.join(",")}`;
      if (!boxes.has(key)) boxes.set(key, { page_idx: spot.page_idx, bbox: spot.bbox, numbers: [], notes: [] });
      const box = boxes.get(key);
      box.numbers.push(spotNumber(spot.n));
      box.notes.push(`${spotNumber(spot.n)} 读作“${spot.reading}”，MinerU 读作“${spot.mineru}”`);
    });
    return [...boxes.values()].map((box) => ({
      ...box, label: box.numbers.join(""), title: `对照这一行：${box.notes.join("；")}`
    }));
  }

  // The characters to check, marked in the text (inside a formula: the formula boxed, the character coloured).
  function spotTextMarks(q) {
    const marks = {};
    (q.check_spots || []).forEach((spot) => {
      if (!spot.field || !Number.isInteger(spot.start) || !Number.isInteger(spot.end)) return;
      (marks[spot.field] ||= []).push({ start: spot.start, end: spot.end, kind: "spot", exact: true });
    });
    return marks;
  }

  function reviewMarks(q) {
    const marks = { ...diffMarks(q) };
    Object.entries(spotTextMarks(q)).forEach(([field, list]) => { marks[field] = [...(marks[field] || []), ...list]; });
    return marks;
  }

  function cropView(regions, { figures = [], spots = [], fallbackImages = [], onZoom, capToNatural = false } = {}) {
    const wrap = el("div", "crop");
    if (!regions.length) {
      wrap.append(el("p", "crop-missing", "这道题还没有原卷范围。点“调整范围”框出题目范围并保存；需要 AI 读字时，再点题卡上的“AI 识读这题”。保存范围不会自动识读。"));
      return wrap;
    }
    const widest = Math.max(...regions.map((r) => r.bbox[2] - r.bbox[0]));
    if (capToNatural) {
      // Never enlarge past the preview image's own pixels: that only blurs.
      const natural = Math.max(...regions.map((r) => {
        const page = pageInfo(r.page_idx);
        const scale = PREVIEW_LONG_SIDE / Math.max(page.width, page.height, 1);
        return ((r.bbox[2] - r.bbox[0]) / 1000) * page.width * scale;
      }));
      // About the size of the typeset text beside it, so characters compare 1:1.
      if (Number.isFinite(natural) && natural > 0) wrap.style.maxWidth = `${Math.round(Math.min(natural * 0.85, 1000))}px`;
    }
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
      image.draggable = false;
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
      const imageNotice = el("p", "hint");
      imageNotice.hidden = true;
      const saved = Array.isArray(fallbackImages) ? fallbackImages[index] : null;
      // A saved crop is already cut to this region. Match the full source range
      // before using it, so a stale or reordered piece can never stand in for it.
      const canFallback = Boolean(saved?.url && saved.page_idx === region.page_idx
        && Array.isArray(saved.bbox) && saved.bbox.length === 4
        && saved.bbox.every((value, i) => value === region.bbox[i]));
      let fallbackAttempted = false;
      image.addEventListener("load", () => {
        if (fallbackAttempted) image.dataset.cropFallback = "ready";
        imageNotice.hidden = true;
      });
      image.addEventListener("error", () => {
        if (!fallbackAttempted && canFallback) {
          fallbackAttempted = true;
          image.dataset.cropSource = "saved";
          image.dataset.cropFallback = "loading";
          imageNotice.textContent = "原卷预览暂不可用，正在加载已保存的题目裁片…";
          imageNotice.hidden = false;
          image.style.width = "100%";
          image.style.left = "0";
          image.style.top = "0";
          if (saved.width > 0 && saved.height > 0) {
            image.width = saved.width;
            image.height = saved.height;
            segment.style.aspectRatio = `${saved.width} / ${saved.height}`;
          }
          image.src = saved.url;
          return;
        }
        if (fallbackAttempted) image.dataset.cropFallback = "failed";
        imageNotice.textContent = "题目图片未能加载。重新打开放大窗口重试，或在“调整范围”里恢复并保存。";
        imageNotice.hidden = false;
      });
      segment.append(image);
      figures.flatMap((figure) => [figure, ...(Array.isArray(figure.parts) ? figure.parts.map((part) => ({ ...part, slot: figure.slot })) : [])])
        .filter((figure) => figure.page_idx === region.page_idx).forEach((figure) => {
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
      // 1.10.2: where MinerU read the paper differently — the line to compare, numbered as in the flag.
      spotBoxes(spots).filter((spot) => spot.page_idx === region.page_idx).forEach((spot) => {
        const [sx0, sy0, sx1, sy1] = spot.bbox;
        if (sx1 < x0 || sx0 > x1 || sy1 < y0 || sy0 > y1) return;
        // The number sits just above the line it marks (below when the line is at the top of the crop).
        const box = el("span", `crop-spot${(Math.max(sy0, y0) - y0) / rh < 0.15 ? " label-below" : ""}`);
        box.style.left = `${((Math.max(sx0, x0) - x0) / rw) * 100}%`;
        box.style.top = `${((Math.max(sy0, y0) - y0) / rh) * 100}%`;
        box.style.width = `${((Math.min(sx1, x1) - Math.max(sx0, x0)) / rw) * 100}%`;
        box.style.height = `${((Math.min(sy1, y1) - Math.max(sy0, y0)) / rh) * 100}%`;
        box.title = spot.title;
        box.append(el("span", "crop-spot-label", spot.label));
        segment.append(box);
      });
      if (index > 0) wrap.append(el("div", "crop-join", `接第 ${region.page_idx + 1} 页`));
      wrap.append(segment, imageNotice);
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

  const CANVAS_ZOOM_MIN = 0.02;
  const CANVAS_ZOOM_MAX = 4;

  // Keep the same paper point under the pointer when the canvas changes size.
  function anchorCanvasZoom(viewport, canvas, apply, pointer = null) {
    if (!canvas) return;
    const frame = viewport.getBoundingClientRect();
    const x = pointer?.clientX ?? frame.left + viewport.clientWidth / 2;
    const y = pointer?.clientY ?? frame.top + viewport.clientHeight / 2;
    const segment = document.elementFromPoint(x, y)?.closest?.(".crop-seg");
    const target = segment && canvas.contains(segment) ? segment : canvas;
    const before = target.getBoundingClientRect();
    const rx = before.width ? (x - before.left) / before.width : 0;
    const ry = before.height ? (y - before.top) / before.height : 0;
    canvas.classList.add("measuring");
    apply();
    const after = target.getBoundingClientRect();
    viewport.scrollLeft += after.left + rx * after.width - x;
    viewport.scrollTop += after.top + ry * after.height - y;
    canvas.classList.remove("measuring");
  }

  function bindCanvasPan(viewport, canStart, modal) {
    let cleanup = null;
    let suppressClick = false;
    const stop = () => { if (cleanup) cleanup(); };
    viewport.addEventListener("pointerdown", (event) => {
      if (event.target.closest?.("a, button, input, textarea, select, summary, label, [contenteditable='true']")) return;
      if (event.pointerType !== "mouse" || !canStart(event)) return;
      const rect = viewport.getBoundingClientRect();
      if (event.clientX >= rect.left + viewport.clientWidth || event.clientY >= rect.top + viewport.clientHeight) return;
      stop();
      event.preventDefault();
      event.stopPropagation();
      viewport.focus({ preventScroll: true });
      const start = { x: event.clientX, y: event.clientY, left: viewport.scrollLeft, top: viewport.scrollTop };
      let moved = false;
      viewport.classList.add("panning");
      const move = (next) => {
        if (next.pointerId !== event.pointerId) return;
        moved ||= Math.hypot(next.clientX - start.x, next.clientY - start.y) > 3;
        viewport.scrollLeft = start.left - (next.clientX - start.x);
        viewport.scrollTop = start.top - (next.clientY - start.y);
      };
      const end = (next) => {
        if (next.type !== "blur" && next.pointerId !== event.pointerId) return;
        suppressClick = next.type === "pointerup" && moved;
        stop();
        setTimeout(() => { suppressClick = false; }, 0);
      };
      cleanup = () => {
        cleanup = null;
        viewport.classList.remove("panning");
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", end);
        window.removeEventListener("pointercancel", end);
        window.removeEventListener("blur", end);
        viewport.removeEventListener("lostpointercapture", end);
        if (viewport.hasPointerCapture?.(event.pointerId)) viewport.releasePointerCapture(event.pointerId);
      };
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", end);
      window.addEventListener("pointercancel", end);
      window.addEventListener("blur", end);
      viewport.addEventListener("lostpointercapture", end);
      try { viewport.setPointerCapture(event.pointerId); } catch { /* Window listeners still finish the gesture. */ }
    }, { capture: true });
    viewport.addEventListener("click", (event) => {
      if (!suppressClick) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      suppressClick = false;
    }, { capture: true });
    viewport.addEventListener("dragstart", (event) => event.preventDefault());
    modal.addEventListener("close", stop);
    return stop;
  }

  const viewer = { id: null, zoom: 1, mode: "fit", fitFrame: 0, imageReady: false };

  function viewerList() {
    return state.questions.filter(visible);
  }

  function openViewer(q) {
    teach({ type: "viewer", number: q.number });
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
    const available = viewer.imageReady;
    $("zoomFit").disabled = $("zoomWidth").disabled = !available;
    $("zoomOut").disabled = !available || viewer.zoom <= CANVAS_ZOOM_MIN;
    $("zoomIn").disabled = !available || viewer.zoom >= CANVAS_ZOOM_MAX;
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
    if (!dialog.open || viewer.mode !== "fit" || !q?.regions?.length || !viewer.imageReady) return;
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
    stopViewerSourcePan();
    if (stopViewerTextPan) stopViewerTextPan();
    const q = questionById(viewer.id);
    if (!q) { $("viewerDialog").close(); return; }
    const list = viewerList();
    const index = list.findIndex((item) => item.id === q.id);
    $("viewerTitle").textContent = `${questionLabel(q)} · 原卷对照`;
    $("viewerChip").replaceChildren(stateChip(q));
    const crop = $("viewerCrop");
    const regions = q.regions || [];
    if (!regions.length && viewer.mode === "fit") viewer.zoom = 1;
    viewer.imageReady = false;
    crop.replaceChildren(cropView(regions, { figures: q.figures || [], spots: q.check_spots, fallbackImages: q.body_mode === "source_image" ? q.question_images || [] : [] }));
    const images = [...crop.querySelectorAll("img")];
    const imageStates = new Map(images.map((image) => [image, "pending"]));
    const syncImages = () => {
      if (images.length && !crop.contains(images[0])) return;
      const values = [...imageStates.values()];
      viewer.imageReady = values.includes("ready");
      const failed = values.includes("failed");
      const pending = values.includes("pending");
      const status = $("viewerImageState");
      status.hidden = !images.length || (viewer.imageReady && !failed && !pending);
      status.classList.toggle("image-error", failed);
      status.textContent = failed ? (viewer.imageReady ? "部分题目图片未能加载，关闭窗口后重试，或调整范围后保存。" : "题目图片未能加载，关闭窗口后重试，或调整范围后保存。")
        : images.some((image) => image.dataset.cropFallback === "loading") ? "原卷预览暂不可用，正在加载已保存的题目裁片…" : "正在加载原卷…";
      if (!viewer.imageReady) stopViewerSourcePan();
      syncViewerZoom();
      if (viewer.imageReady && viewer.mode === "fit" && $("viewerDialog").open) requestViewerFit();
    };
    images.forEach((image) => {
      const finish = (result) => { imageStates.set(image, result); syncImages(); };
      image.addEventListener("load", () => finish("ready"));
      image.addEventListener("error", () => finish(image.dataset.cropFallback === "loading" ? "pending" : "failed"));
      if (image.complete) imageStates.set(image, image.naturalWidth ? "ready" : image.dataset.cropFallback === "loading" ? "pending" : "failed");
    });
    syncImages();
    // 又宽又矮的截图（一两行字的题）改成上下排：原卷能占满整个窗口宽度。
    $("viewerSource").parentElement.classList.toggle("stacked", cropAspect(regions) > 2.5 && window.innerWidth > 1100);
    applyZoom();
    const text = $("viewerText");
    text.replaceChildren();
    const figurePanel = q.body_mode === "source_image" ? null : figureReviewPanel(q);
    if (figurePanel) text.append(figurePanel);
    const flags = flagsNode(q);
    if (flags) text.append(flags);
    const disagreement = disagreementPanel(q);
    if (disagreement) text.append(disagreement);
    if (q.body_mode === "source_image" && regions.length) {
      if (q.ocr_suggestion && Object.keys(q.ocr_suggestion).length) text.append(readingSuggestionPanel(q));
      else text.append(el("p", "hint", q.ocr_pending ? "AI 正在读取左侧原图……" : "原图已保留在左侧。需要文字时，可在题卡上开始 AI 识读。"));
    } else if (q.stem || q.body_mode === "source_image") {
      const body = el("div");
      R.renderQuestion(body, content(q), { showNumber: false, marks: reviewMarks(q), showAnswer: "collapsed" });
      text.append(body);
      requestAnimationFrame(() => R.fitOptions(body));
    } else text.append(el("p", "hint", q.state === "waiting" || q.state === "reading" ? "AI 正在读这道题……" : "还没有题面"));
    syncViewerZoom();
    $("viewerPrev").disabled = index <= 0;
    $("viewerNext").disabled = index < 0 || index >= list.length - 1;
    const approve = $("viewerApprove");
    if (isAiApproved(q)) {
      approve.replaceChildren(icon("check"), document.createTextNode("确认通过并下一题"), el("span", "kbd-hint", "Enter"));
      approve.className = "button primary";
      approve.disabled = false;
      approve.title = `${agentLabel(q)} 已通过；你核对无误就确认，变成你的通过`;
    } else if (isApproved(q)) {
      approve.replaceChildren(document.createTextNode("已通过 · 下一题"), el("span", "kbd-hint", "Enter"));
      approve.className = "button";
      approve.disabled = index < 0 || index >= list.length - 1;
      approve.title = "此题已通过；继续看下一题。撤销通过用 U 或取消题卡勾选。";
    } else {
      const blocked = figureBlocksApproval(q);
      const typeBlocked = !blocked && typeBlocksApproval(q);
      const blockedLabel = figureReview(q)?.status === "conflict" ? "处理配图冲突" : "处理漏图提醒";
      approve.replaceChildren(icon(blocked ? "image" : "check"), document.createTextNode(blocked ? blockedLabel
        : typeBlocked ? "先选题型" : approvalNeedsReview(q) ? "重新标记通过" : "通过并下一题"), el("span", "kbd-hint", "Enter"));
      approve.className = "button primary";
      approve.disabled = blocked || typeBlocked ? false : !canApprove(q);
      approve.title = blocked ? "前往黄色区域，选择保留、调整或移除配图"
        : typeBlocked ? "题型没读出来：回到题卡，在题号旁边选题型"
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
    if (isHumanApproved(q)) { viewerStep(1); return; }
    if (figureBlocksApproval(q)) { focusFigureReview(q); return; }
    if (typeBlocksApproval(q)) { focusTypePicker(q); return; }
    if (!canApprove(q)) {
      toast("这道题还不能通过", "error");
      return;
    }
    const before = viewerList().map((item) => item.id);
    const ok = await approveQuestion(q, true, { advance: false });
    if (!ok) return;
    // 通过后跳到下一张没通过的卡；没有了就停在这张并提示。
    const position = before.indexOf(q.id);
    const pending = state.questions.filter((item) => visible(item) && !isHumanApproved(item));
    const next = pending.find((item) => before.indexOf(item.id) > position) || pending[0];
    if (next) { viewer.id = next.id; viewer.zoom = 1; viewer.mode = "fit"; setCurrent(next.id); }
    hideLens();
    renderViewer();
    $("viewerSource").scrollTo({ top: 0, left: 0 });
    $("viewerText").scrollTo({ top: 0, left: 0 });
  }

  function viewerKey(event) {
    if (event.defaultPrevented || event.isComposing || event.keyCode === 229 || event.ctrlKey || event.metaKey || event.altKey
      || QBUpload.isEditingTarget(event.target)) return;
    const key = event.key;
    if ((key === "Enter" && event.shiftKey) || (["Enter", " "].includes(key) && event.target?.closest?.("button, a, summary"))) return;
    if (event.repeat && ["Enter", "u", "U", "n", "N"].includes(key)) { event.preventDefault(); return; }
    if (["ArrowLeft", "k", "K"].includes(key)) { event.preventDefault(); viewerStep(-1); }
    else if (["ArrowRight", "j", "J", "n", "N"].includes(key)) {
      event.preventDefault();
      if (!event.repeat || key.toLowerCase() !== "n") viewerStep(1);
    }
    else if (key === "Enter") { event.preventDefault(); viewerApprove(); }
    else if (key.toLowerCase() === "u") {
      const q = questionById(viewer.id);
      if (q && isApproved(q)) { event.preventDefault(); approveQuestion(q, false, { advance: false }); }
    }
    else if (key === "?") { event.preventDefault(); openShortcutHelp("review", { comparison: true }); }
    else if (key === " ") { event.preventDefault(); $("viewerDialog").close(); }
    else if (key === "+" || key === "=") { event.preventDefault(); zoomBy(1.25); }
    else if (key === "-" || key === "_") { event.preventDefault(); zoomBy(0.8); }
    else if (key === "0") { event.preventDefault(); requestViewerFit(); }
    else if (key.toLowerCase() === "w") { event.preventDefault(); setViewerWidth(); }
    else if (key.toLowerCase() === "e") { event.preventDefault(); editFromViewer(); }
    else if (key.toLowerCase() === "r" || key.toLowerCase() === "f") {
      event.preventDefault();
      const q = questionById(viewer.id);
      $("viewerDialog").close();
      if (q) openPageDialog(key.toLowerCase() === "r" || q.body_mode === "source_image" ? "regions" : "figures", q);
    }
  }

  function editFromViewer() {
    const q = questionById(viewer.id);
    $("viewerDialog").close();
    if (!q) return;
    const card = document.querySelector(`.card[data-id="${q.id}"]`);
    if (card?.classList.contains("compact")) { state.expanded.add(q.id); renderCards(); }
    state.autoExpanded.delete(q.id);
    const fresh = document.querySelector(`.card[data-id="${q.id}"]`);
    if (fresh) { fresh.scrollIntoView({ block: "start" }); openEditor(fresh, q); }
  }

  function zoomBy(factor, pointer = null) {
    if (!viewer.imageReady) return;
    if (viewer.fitFrame) cancelAnimationFrame(viewer.fitFrame);
    viewer.fitFrame = 0;
    viewer.mode = "manual";
    viewer.zoom = Math.min(CANVAS_ZOOM_MAX, Math.max(CANVAS_ZOOM_MIN, Math.round(viewer.zoom * factor * 1000) / 1000));
    anchorCanvasZoom($("viewerSource"), $("viewerCrop"), applyZoom, pointer);
  }

  function setViewerWidth() {
    if (!viewer.imageReady) return;
    viewer.mode = "width";
    viewer.zoom = 1;
    applyZoom();
    $("viewerSource").scrollTo({ top: 0, left: 0 });
  }

  $("zoomIn").addEventListener("click", () => zoomBy(1.25));
  $("zoomOut").addEventListener("click", () => zoomBy(0.8));
  $("zoomFit").addEventListener("click", requestViewerFit);
  $("zoomWidth").addEventListener("click", setViewerWidth);
  $("viewerEdit").addEventListener("click", editFromViewer);
  $("viewerPrev").addEventListener("click", () => viewerStep(-1));
  $("viewerNext").addEventListener("click", () => viewerStep(1));
  $("viewerApprove").addEventListener("click", viewerApprove);
  $("viewerSource").addEventListener("wheel", (event) => {
    if (!event.ctrlKey) return;
    event.preventDefault();
    if (event.deltaY) zoomBy(event.deltaY < 0 ? 1.1 : 0.9, event);
  }, { passive: false });
  const stopViewerSourcePan = bindCanvasPan($("viewerSource"),
    (event) => viewer.imageReady && (event.button === 0 || event.button === 1), $("viewerDialog"));
  let stopViewerTextPan = null;
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
    if (stopViewerTextPan) stopViewerTextPan();
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
      stopViewerTextPan = null;
      text.classList.remove("panning");
      text.removeEventListener("pointermove", move);
      text.removeEventListener("pointerup", stop);
      text.removeEventListener("pointercancel", stop);
      text.removeEventListener("lostpointercapture", stop);
      window.removeEventListener("blur", stop);
      if (text.hasPointerCapture?.(event.pointerId)) text.releasePointerCapture(event.pointerId);
    };
    stopViewerTextPan = stop;
    text.addEventListener("pointermove", move);
    text.addEventListener("pointerup", stop);
    text.addEventListener("pointercancel", stop);
    text.addEventListener("lostpointercapture", stop);
    window.addEventListener("blur", stop);
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
    if (stopViewerTextPan) stopViewerTextPan();
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

  let optionFitTimer = 0;
  window.addEventListener("resize", () => {
    clearTimeout(optionFitTimer);
    optionFitTimer = setTimeout(() => R.fitOptions(document.body), 120);
  });
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
    const pieces = (q.figures || []).flatMap((figure) => [figure, ...(Array.isArray(figure.parts) ? figure.parts : [])]);
    const selected = new Set(pieces.map((figure) => {
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
        : (review.signals || []).includes("bound_figure_without_text_cue")
          ? ((q.figures || []).length
            ? "题目文字没有提到图，但 AI 给本题配了图。是试卷上印的图就点“确认当前配图及归属”；是学生的草稿、答案或别题的图就点“这些图与本题无关”。"
            : "题目文字没有提到图，但 AI 认为这里有本题的配图。需要配图就点“调整配图或归属”把它框出来；原卷没有就点“这些图与本题无关”。")
          : (review.reason || "程序无法确定候选内容是正式配图还是手写痕迹，请对照原卷确认。")
    };
    // A table crop turned into a text table leaves nothing to check here but the cells.
    if (review.status === "auto_excluded" && q?.stem && R.findTables(q.stem).length && !(q.figures || []).length) return {
      title: "表格已写成文字",
      text: "原卷里的表格已经排成题目里的表格，截图不再作为配图；请对照原卷逐格核对。"
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
      })(),
      // Keep a stitched figure stitched when its figures are confirmed.
      ...(Array.isArray(figure.parts) && figure.parts.length ? { parts: figure.parts.map((part) => ({
        page_idx: part.page_idx, bbox: [...part.bbox],
        ...(hasFigureCandidateKey(q, part.candidate_key) ? { candidate_key: part.candidate_key } : {})
      })) } : {})
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
        if ((q.figures || []).length) actions.append(button("确认当前配图及归属", "small primary", () => confirmCurrentFigures(q), "保留每张图现有的题干或选项归属", { iconName: "image" }));
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

  // The type picker beside the number already asks for the type (and the button below it).
  const TYPE_REMINDER = /^题型没读出来/;

  function questionFlags(q) {
    const review = figureReview(q);
    return (q.flags || []).filter((flag) => {
      if (TYPE_REMINDER.test(String(flag)) && typeBlocksApproval(q)) return false;
      if (!review) return true;
      if (flag === review.reason) return false;
      // The figure panel above the text already says this, with the buttons to settle it.
      return !(FIGURE_REVIEW_BLOCKS.has(review.status)
        && /还没有配图|原卷可能有图没有被找到|选项是图|没有发现图像提示词/.test(String(flag)));
    });
  }

  function flagsNode(q) {
    const difference = reviewDiff(q);
    const flags = questionFlags(q).map((flag) => {
      const text = String(flag);
      if (/^两次识读一致，但 MinerU/.test(text)) {
        return text.replace(/^两次识读一致，但 MinerU 在这里读法不同，再看一次也不能确定：/,
          "AI 识读与 MinerU 原始文字有差异，请对照原卷：");
      }
      if (/^第三次识读裁决后/.test(text)) {
        return text.replace(/^第三次识读裁决后，MinerU 在这里读法仍不同，再看一次也不能确定：/,
          "AI 识读与 MinerU 原始文字有差异，请对照原卷：");
      }
      if (!/^两次识读不一致/.test(text)) return flag;
      if (difference.hasVisibleMarks) return "识读结果有差异；请核对题面中标黄的位置";
      if (difference.observedOnly.length) return "识读结果有差异；另一份识读记录可在下方查看";
      return "识读记录中曾有出入；可在“更多 → 查看识读记录”中查看。";
    });
    // 同一句失败原本出现在两处：题卡上的这串提示，和原图下面“AI 识读结果”
    // 面板里的那句。识读失败只由那个面板负责，这里不再重复。
    const suggested = q.body_mode === "source_image" && q.ocr_suggestion
      && Object.keys(q.ocr_suggestion).length ? q.ocr_suggestion.error : "";
    const owned = suggested && suggested === q.error ? q.error : "";
    if (!flags.length && (!q.error || owned)) return null;
    const list = el("ul", "flags");
    if (q.error && !owned) list.append(el("li", "", q.error));
    flags.forEach((flag) => list.append(flagItem(flag)));
    return list;
  }

  // 识读没跑起来的原因是密钥没配，不是这张题有问题。所以这里说清是哪一家
  // 服务、原图还在，并给一个直接打开本机 API 配置窗口的入口；原图审核流程不变。
  function readerConfigEntry(suggestion) {
    const service = String(suggestion.error_service_label || "").trim();
    const box = el("div", "reading-suggestion-actions");
    box.append(el("p", "hint", `${service ? `“${service}”` : "看图读题服务"}还没有 API Key，所以这次识读没有发出请求。`
      + "原卷、已切好的题目和人工改过的内容都保留着：配好密钥后可以直接重新识读，"
      + "也可以先对照原图审核或改字，不影响这一份资料。"));
    box.append(button("打开 API 配置", "small primary", () => openCredentialSettings("reading"),
      "在本机填写这家的 API Key；填好后回到题卡点“重新 AI 识读”即可"));
    return box;
  }

  // “…当销售【单】价为1…（MinerU：定）”: the disputed characters stand out.
  // 1.10.2: the spots of “MinerU 读法不同” are numbered ①② like their boxes on the crop.
  const SPOT_FLAG = /^((?:AI 识读与 MinerU 原始文字有差异|两次识读一致|第三次识读裁决后)[^：]*：)([\s\S]*)$/;

  function flagItem(flag) {
    const item = el("li");
    const appendMarked = (text) => String(text).split(/(【[^】]{1,40}】)/).forEach((part) => {
      if (!part) return;
      if (/^【[^】]+】$/.test(part)) item.append(el("mark", "flag-spot", part.slice(1, -1)));
      else item.append(document.createTextNode(part));
    });
    const spotFlag = SPOT_FLAG.exec(String(flag));
    if (!spotFlag) { appendMarked(flag); return item; }
    item.append(document.createTextNode(spotFlag[1]));
    spotFlag[2].split("；").forEach((piece, index) => {
      if (index) item.append(document.createTextNode("；"));
      item.append(el("span", "flag-spot-number", spotNumber(index + 1)));
      appendMarked(piece);
    });
    return item;
  }

  function stateChip(q) {
    if (q.body_mode === "source_image" && q.ocr_pending) return el("span", "chip yellow", "原图保留 · 正在识读");
    if (q.body_mode === "source_image" && !isApproved(q)) return el("span", "chip yellow", "原图题 · 待核对");
    const review = figureReview(q);
    if (review?.status === "blocked_missing") return el("span", "chip yellow", "可能漏图 · 待处理");
    if (review?.status === "conflict") return el("span", "chip yellow", "配图冲突 · 待确认");
    if (approvalNeedsReview(q) && !typeBlocksApproval(q)) return el("span", "chip yellow", "内容已变 · 需重新审核");
    if (isAiApproved(q)) {
      const chip = el("span", "chip ai-approved", `${agentLabel(q)} 已通过 · 待你核对`);
      chip.title = "AI 助手对照原卷后打的勾，可以入库，题库里会标着“AI 审核”。你核对无误就点题号左边的方框确认。";
      return chip;
    }
    if (isApproved(q)) return el("span", "chip approved", "已标记通过");
    if (q.text_source === "single" && ["green", "yellow"].includes(q.state)) {
      return el("span", "chip waiting", "待核对");
    }
    if (q.state === "green") {
      return el("span", "chip green", q.text_source === "human" ? "已修改 · 待核对" : "待核对");
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
    const panel = el("details", "reading-difference");
    const fields = [...new Set(difference.observedOnly.map((item) => item.fieldName))];
    panel.append(el("summary", "reading-difference-title", `其他读法还有不同内容 · ${fields.join("、")}（展开查看）`));
    const list = el("ul", "reading-difference-list");
    difference.observedOnly.forEach((item) => {
      const row = el("li");
      const text = el("span", "reading-difference-text");
      let before = item.before || "";
      let after = item.after || "";
      let extra = shortDifferenceText(item.text);
      // Inside a formula: typeset the extra part instead of showing “$ … $” source.
      if (((before.match(/\$/g) || []).length % 2) === 1) {
        extra = `$${extra}$`;
        before = before.replace(/\$\s*$/, "");
        after = after.replace(/^\s*\$/, "");
      }
      if (before) text.append(el("span", "reading-difference-context", `…${before}`));
      const mark = el("mark", "reading-difference-extra");
      R.renderTypeset(mark, extra);
      text.append(mark);
      if (after) text.append(el("span", "reading-difference-context", `${after}…`));
      row.append(el("span", "reading-difference-source", `${item.readerName} · ${item.fieldName}`), text);
      list.append(row);
    });
    const show = button("查看识读记录", "small", () => toggleReadsNear(panel, q));
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
      figures: q.figures,
      body_mode: q.body_mode,
      question_images: q.question_images
    };
  }

  // 已通过的卡：展开和收起是同一个按钮，固定在卡片右上角，两种状态下位置不变。
  function expandToggle(q, collapsed) {
    const toggle = button(collapsed ? "展开" : "收起", "small quiet card-toggle", (event) => {
      event.stopPropagation();
      setExpanded(q.id, collapsed);
      setCurrent(q.id);
      renderCards();
    });
    toggle.title = `${collapsed ? "展开" : "收起"}（O；Shift+O 全部）`;
    const chevron = icon("chevron");
    chevron.style.cssText = `width:14px;height:14px;${collapsed ? "" : "transform:rotate(180deg)"}`;
    toggle.append(chevron);
    toggle.setAttribute("aria-expanded", String(!collapsed));
    return toggle;
  }

  function renderCard(q) {
    const approved = isApproved(q);
    // AI 通过的题还等人核对，不收起。
    const approvedCompact = approved && !isAiApproved(q) && !state.expanded.has(q.id);
    const displayState = isAiApproved(q) ? "ai" : approved ? "approved has-toggle"
      : (figureBlocksApproval(q) || approvalNeedsReview(q)) ? "yellow" : q.state;
    const card = el("article", `card state-${displayState}${approvedCompact ? " compact" : ""}`);
    card.dataset.id = q.id;
    card.id = `q-${q.id}`;
    card.tabIndex = -1;
    card.setAttribute("aria-label", questionLabel(q));
    card.addEventListener("pointerdown", () => { if (state.current !== q.id) setCurrent(q.id); });

    if (approvedCompact) {
      const row = el("div", "compact-row");
      row.append(approvalTick(q), el("span", "qnum", questionLabel(q)), stateChip(q));
      const preview = el("span", "compact-text");
      R.renderTypeset(preview, q.body_mode === "source_image" ? `原图题 · ${q.question_images?.length || 0} 段` : firstLine(q.stem));
      row.append(preview);
      row.append(publicationChip(q));
      row.append(expandToggle(q, true));
      card.append(row);
      card.addEventListener("click", (event) => {
        if (event.ctrlKey || event.metaKey || event.shiftKey || event.target.closest("button")) return;
        setExpanded(q.id, true);
        renderCards();
      });
      return card;
    }

    const source = el("div", "card-source");
    const sticky = el("div", "source-sticky");
    // 1.10.1：每张卡都是左图右文（以前宽的截图会改成上图下文，版式和打勾位置跟着变）。
    sticky.append(cropView(q.regions, { figures: q.figures, spots: q.check_spots, fallbackImages: q.body_mode === "source_image" ? q.question_images || [] : [], onZoom: () => openViewer(q), capToNatural: true }));
    // 说明文字也能点：写着“点击放大对照”，点它就该打开放大对照。
    const sourceNote = el("button", "source-note");
    sourceNote.type = "button";
    sourceNote.title = "放大对照（Space）";
    sourceNote.append(icon("zoom"), document.createTextNode(q.regions_changed ? "原卷截图（范围已人工调整）· 点击放大对照"
      : q.start_source === "inferred" ? "题号由本地规则补出，请对照原卷核对 · 点击放大对照"
        : q.start_source === "located" ? "原卷截图（题号由 AI 在原卷上定位）· 点击放大对照" : "原卷截图 · 点击放大对照"));
    sourceNote.addEventListener("click", () => openViewer(q));
    if (q.regions.length) sticky.append(sourceNote);
    source.append(sticky);

    const body = el("div", "card-body");
    // 打勾方框、题号、题型、状态是整张卡最上面的一条，和收起的一行对齐，不随截图走。
    const head = el("header", "card-head");
    head.append(approvalTick(q));
    if (hasMultipleQuestionGroups() && q.group?.title) head.append(el("span", "group-label", q.group.title));
    head.append(el("span", "qnum", questionLabel(q)), typePicker(q), stateChip(q));
    head.append(el("span", "head-spacer"), publicationChip(q));

    if (q.state === "waiting" || q.state === "reading") {
      const placeholder = el("div", "reading-placeholder");
      placeholder.append(el("p", "", q.state === "reading" ? "AI 正在读这道题……" : "排队等待 AI 识读……"),
        el("div", "skeleton w80"), el("div", "skeleton w60"), el("div", "skeleton w40"));
      body.append(placeholder);
      card.append(head, source, body);
      return card;
    }

    const figurePanel = q.body_mode === "source_image" ? null : figureReviewPanel(q);
    if (figurePanel) body.append(figurePanel);
    const flags = flagsNode(q);
    if (flags) body.append(flags);
    const disagreement = disagreementPanel(q);
    if (disagreement) body.append(disagreement);

    if (q.origin) {
      const origin = el("p", "card-origin");
      origin.append(el("span", "card-origin-label", "题源"), document.createTextNode(q.origin));
      origin.title = "题干前印的出处，单独存放；组卷打印时默认不印。在“改字”里可以修改";
      body.append(origin);
    }
    const regionPanel = regionReadPanel(card, q);
    if (regionPanel) body.append(regionPanel);
    const rendered = el("div", "rendered");
    if (q.body_mode === "source_image" && q.regions?.length) {
      rendered.hidden = true;
      if (!q.ocr_suggestion || !Object.keys(q.ocr_suggestion).length) {
        rendered.hidden = false;
        rendered.append(el("p", "hint", q.ocr_pending ? "AI 正在读取左侧原图……" : "原图题已保留在左侧，可直接审核，也可识读为文字。"));
      }
    } else if (q.stem || q.body_mode === "source_image") R.renderQuestion(rendered, content(q), { showNumber: false, marks: reviewMarks(q), showAnswer: "collapsed",
      figureAction: (figure) => tableAction(q, figure) });
    else rendered.append(el("p", "hint", "还没有题面"));
    body.append(rendered);
    if (q.body_mode === "source_image" && q.ocr_suggestion && Object.keys(q.ocr_suggestion).length) body.append(readingSuggestionPanel(q));

    // 通过和撤销通过都在题号左边的方框里（也可以按 Enter / U），这里不再放
    // 一个同样作用的按钮。配图没处理好时，这里放一个去处理的按钮。
    const actions = el("div", "card-actions");
    if (!approved && figureBlocksApproval(q)) {
      const blockedLabel = figureReview(q)?.status === "conflict" ? "处理配图冲突" : "处理漏图提醒";
      const fixFigures = button(blockedLabel, "primary", () => focusFigureReview(q), "", { iconName: "image", key: "Enter" });
      fixFigures.title = "在黄色区域选择保留、调整或移除配图";
      actions.append(fixFigures);
    } else if (!approved && typeBlocksApproval(q)) {
      const pick = button("选题型", "primary", () => focusTypePicker(q), "", { key: "Enter" });
      pick.title = "题型没读出来：在题号旁边选单选、多选、填空、判断或解答";
      actions.append(pick);
    }
    actions.append(
      button("改字", "", () => openEditor(card, q), "修改题干、选项、题型，也可以补答案和解析（E）"),
      button("调整范围", q.regions.length ? "" : "primary", () => openPageDialog("regions", q), "调整题目的原卷范围和片段顺序，只保存范围，不调用 AI（R）"),
      ...(q.body_mode === "source_image" ? [] : [button("配图", "", () => openPageDialog("figures", q), "增删配图，或调整配图的裁剪框（F）", { iconName: "image" })])
    );
    if (q.body_mode === "source_image") {
      const read = button(q.ocr_pending ? "AI 识读中……" : QBCutReading.hasCurrentReading(q) ? "重新 AI 识读" : "AI 识读这题", "primary", () => rereadQuestion(q), "直接读取这道题已保存的全部片段，无需重新框选；识读文字会显示在原图旁供你审核");
      // A missing key is certain before the click, so the button says so instead
      // of accepting the request and reporting the same sentence twice.
      const notReady = readerUnavailable();
      read.disabled = Boolean(q.ocr_pending || questionReadingRequests.has(q.id) || cutReadingRequests.has(state.paperId) || cutReadingStops.has(state.paperId)
        || q.approved || q.publication || !q.regions.length || state.paper.demo || state.paper.status !== "ready") || Boolean(notReady);
      if (notReady) read.title = `“${notReady.label || "看图读题服务"}”还没有 API Key，请先在“设置 → API 配置”里填好；原图和已切好的题目都保留着。`;
      else if (state.paper.status !== "ready") read.title = "请先继续手工整理或重试恢复这份资料，再开始 AI 识读。";
      actions.append(read);
    }
    const more = el("details", "more");
    const summary = el("summary", "", "更多");
    summary.append(icon("chevron"));
    more.append(summary);
    const menu = el("div", "more-menu");
    menu.append(button("查看识读记录", "quiet small", () => { more.open = false; toggleReads(card, q); }));
    if (q.body_mode !== "source_image") {
      menu.append(button("框选识读（纠错）", "quiet small", () => { more.open = false; openPageDialog("read", q); }, "只重读需要纠正的一小块，确认替换位置后再保存"));
      menu.append(button("让 AI 重读这题", "quiet small", () => { more.open = false; rereadQuestion(q); }));
    }
    menu.append(button("删除这张卡", "quiet small danger", () => { more.open = false; deleteQuestion(q); }));
    more.append(menu);
    actions.append(more);
    body.append(actions);
    card.append(head, source, body);
    if (approved && !isAiApproved(q)) card.append(expandToggle(q, false));
    return card;
  }

  function readingSuggestionPanel(q) {
    const suggestion = q.ocr_suggestion;
    const panel = el("section", "reading-suggestion");
    panel.append(el("strong", "", "AI 识读结果"));
    if (suggestion.revision !== q.content_revision) {
      panel.append(el("p", "hint", "题目范围或内容已经变化，这份建议已过期。请重新识读。"));
      return panel;
    }
    if (suggestion.error || !String(suggestion.stem || "").trim()) {
      panel.append(el("p", "hint", suggestion.error || "没有读到完整题干，请保留原图并核对范围。"));
      if (suggestion.error_kind === "reader_not_configured") panel.append(readerConfigEntry(suggestion));
      return panel;
    }
    const preview = el("div", "reading-suggestion-body");
    R.renderQuestion(preview, { ...suggestion, body_mode: "text", figures: (suggestion.figures || []).filter((figure) => figure.url) }, { showNumber: false, showAnswer: "none" });
    panel.append(preview, el("p", "hint", "请对照原图核对文字与配图。读错可以改字或重新识读。"));
    return panel;
  }

  // MinerU read this crop as a table: one click turns it into a text table in
  // the stem, where the reviewer can check it cell by cell.
  function tableAction(q, figure) {
    if (!figure.table) return null;
    const index = (q.figures || []).indexOf(figure);
    if (index < 0) return null;
    return button("转成文字表格", "small", async (event) => {
      event.stopPropagation();
      const node = event.currentTarget;
      node.disabled = true;
      try {
        const data = await api(`/api/questions/${q.id}/figure-table`, { method: "POST", body: { figure: index } });
        applyQuestion(data);
        toast(`第 ${q.number} 题的表格已写进题干；请对照原卷逐格核对，位置不对可以在“改字”里挪`, "success");
        setCurrent(q.id, { focus: true });
      } catch (error) {
        toast(error.message, "error");
        node.disabled = false;
      }
    }, "用 MinerU 识别出的表格文字替换这张截图，排成题目里的表格");
  }

  // 题卡收起时只显示第一行；截断时不把 $…$ 公式截成半截。
  function firstLine(text) {
    const lines = String(text || "").split("\n");
    const line = lines.find((item) => item.trim() && !item.trim().startsWith("|") && !/^\s*<table/i.test(item)) ?? lines[0];
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
    const labels = { a: "首次识读", b: "后续识读", c: "其他历史记录" };
    Object.entries(q.reads).forEach(([key, reading]) => {
      if (!reading || (!reading.stem && !reading.error && !reading.witness)) return;
      const item = el("div", "read");
      const chosen = reading.chosen === "a" ? "识读记录 1" : "识读记录 2";
      const witnessNote = key === "c" ? `原卷文字用来辅助查看${chosen}`
        : "原卷文字作为辅助对照";
      const objections = Array.isArray(reading.objections) ? reading.objections : [];
      let label = `${labels[key]}${reading.engine ? ` · ${reading.engine}` : ""}`;
      if (objections.length) {
        label = reading.unverified
          ? `原卷文字对照（${objections.length} 处差异待核对）`
          : `识读记录对照（${objections.length} 处差异）`;
      } else if (reading.witness) label = `旁证 · MinerU 自己识别的文字（${witnessNote}）`;
      item.append(el("p", "read-label", label));
      if (objections.length) {
        const verdicts = { reading: "核对记录：与识读相同", mineru: "核对记录：与原卷文字相同", null: "核对记录：不确定" };
        const answers = Array.isArray(reading.answers) ? reading.answers : [];
        const list = el("ul", "read-spots");
        objections.forEach((spot, index) => {
          const verdict = index < answers.length ? ` · ${verdicts[answers[index]] || verdicts.null}` : "";
          list.append(el("li", "", `…${spot.before}【${spot.reading}】${spot.after}…  MinerU：【${spot.mineru}】${verdict}`));
        });
        item.append(list);
      }
      if (reading.error) item.append(el("p", "read-error", reading.error));
      if (reading.witness && !reading.stem) {
        if (!reading.error || !objections.length) {
          const literal = el("div", "read-text");
          R.renderLiteral(literal, reading.witness);
          item.append(literal);
        }
      } else if (reading.stem) {
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
      data = { ...data, question: normalizeRegionRead(data.question) };
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
    const confirming = approved && isAiApproved(q);
    try {
      const data = await api(`/api/questions/${q.id}/approve`, { method: "POST", body: { approved } });
      setExpanded(q.id, false);
      applyQuestion(data);
      const fresh = questionById(q.id) || q;
      if (approved) {
        const c = counts();
        const left = c.todo;
        toast(confirming ? `已确认第 ${q.number} 题（原来是 ${agentLabel(q)} 通过）`
          : left ? `第 ${q.number} 题已通过并入库` : "本卷已全部通过并入库",
          left ? "" : "success", { label: "撤销", onClick: () => approveQuestion(fresh, false, { advance: false }) });
        teach({ type: "approve", number: q.number });
        if (advance) focusNext(q);
      } else {
        toast(`已撤销第 ${q.number} 题的通过`, "", advance
          ? { label: "恢复通过", onClick: () => approveQuestion(fresh, true, { advance: false }) } : null);
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

  // 1.12.5：工具菜单里的「一键通过所有题目」。语义是"把能通过的题一次入库，
  // 过不了的逐条说明原因"——不静默跳过，也不谎称全做完。
  function showApproveResult({ approved, problems, skipped }) {
    const list = $("approveResultList");
    list.replaceChildren();
    const head = el("p", "approve-result-head");
    head.append(el("strong", "", `已入库 ${approved} 道`));
    if (problems.length) head.append(el("span", "approve-result-sub", `另有 ${problems.length} 道保存未完成，请重试`));
    list.append(head);
    if (!skipped.length && !problems.length) {
      list.append(el("p", "approve-result-ok", "这一卷剩下的题都已经入库了。"));
    }
    for (const item of skipped) {
      list.append(el("p", "approve-result-skip", `第 ${item.number} 题：${item.reason}`));
    }
    for (const text of problems) list.append(el("p", "approve-result-skip", text));
    if (!$("approveResultDialog").open) $("approveResultDialog").showModal();
  }

  async function approveAllGreen() {
    const paperId = state.paperId;
    if (!paperId || state.approveAllBusy) return;
    const waiting = state.questions.filter((question) => !isDone(question)).length;
    if (!waiting) { toast("这一卷没有待通过的题", "error"); return; }
    if (!(await confirmDialog({
      title: "一键通过所有题目？",
      text: `把这一卷里能通过的题一次入库，还有 ${waiting} 道要看。过不了的题不会被硬标通过，会在结果里逐条说明原因。`,
      ok: "通过能通过的"
    }))) return;
    $("toolsMenu").open = false;
    state.approveAllBusy = true;
    syncTrashControls();
    try {
      const data = await api(`/api/papers/${paperId}/approve-green`, { method: "POST", body: { by: "human" } });
      if (state.paperId === paperId) {
        updatePaperFromResponse(data.paper);
        await refreshPaper();
      } else {
        await loadPapers();
      }
      showApproveResult({
        approved: Number(data.approved) || 0,
        problems: data.problems || [],
        skipped: data.skipped || []
      });
      toast(`已入库 ${Number(data.approved) || 0} 道`, "success");
    } catch (error) {
      toast(error.message, "error");
    } finally {
      state.approveAllBusy = false;
      syncTrashControls();
    }
  }

  async function rereadQuestion(q) {
    if (state.paper?.demo || q.ocr_pending || questionReadingRequests.has(q.id) || cutReadingRequests.has(state.paperId) || cutReadingStops.has(state.paperId)) return;
    if (q.body_mode === "source_image" && (q.approved || q.publication || !q.regions.length || state.paper?.status !== "ready")) return;
    if (q.body_mode !== "source_image" && q.edited && !(await confirmDialog({ title: "让 AI 重读这道题？", text: "这道题的文字改过。重读会用 AI 的新读法替换你改的文字。", ok: "重读", danger: true }))) return;
    const paperId = state.paperId;
    questionReadingRequests.add(q.id);
    renderReadingControls();
    try {
      const data = await QBRegionWait.boundedRequest((signal) => api(`/api/questions/${q.id}/reread`, {
        method: "POST", body: { revision: q.content_revision }, signal
      }), { timeoutMs: 30000 });
      if (state.paperId !== paperId || questionById(q.id)?.content_revision !== q.content_revision) return;
      cutReadingErrors.delete(paperId);
      cutReadingStopErrors.delete(paperId);
      applyQuestion(data);
      void refreshPaper();
      toast(q.body_mode === "source_image" ? `第 ${q.number} 题开始识读，原图保留；文字显示后请核对并审核` : `第 ${q.number} 题已交给 AI 重读`);
    } catch (error) {
      if (state.paperId === paperId) toast(error.name === "TimeoutError" ? "识读提交结果尚未确认，稍后查看题卡；原图与保存范围保留。" : error.message, "error");
    } finally {
      questionReadingRequests.delete(q.id);
      if (state.paperId === paperId) renderReadingControls();
    }
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
    if (!ids.length || state.deleteBusy) return;
    const blocked = ids.map(questionById).map((q) => ({ q, reason: questionDeleteBlockReason(q) })).find((item) => item.reason);
    if (blocked) { toast(`第 ${blocked.q.number} 题：${blocked.reason}`, "error"); return; }
    const count = ids.length;
    const title = `把第 ${singleQuestion.number} 题移到回收站？`;
    const ok = await confirmDialog({
      title,
      text: "题卡会从当前审题列表移走，但不会立即永久清除；可以在提示条撤销，也可以稍后从题卡回收站按这一批恢复。",
      ok: "移到回收站",
      danger: true
    });
    if (!ok) return;

    const paperId = state.paperId;
    state.deleteBusy = true;
    try {
      const data = await api(`/api/papers/${paperId}/questions/delete`, { method: "POST", body: { question_ids: ids } });
      if (state.paperId === paperId) {
        const removed = new Set(ids);
        state.questions = state.questions.filter((question) => !removed.has(question.id));
        ids.forEach((id) => { state.rendered.delete(id); state.expanded.delete(id); state.editing.delete(id); });
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
      state.deleteBusy = false;
    }
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
    if ($("approveAllGreen")) {
      // 只有真有能通过的题时才露出来：已经全部入库的卷不该再摆一个空按钮。
      const waiting = state.questions.filter((question) => !isDone(question)).length;
      $("approveAllGreen").hidden = !state.paper;
      $("approveAllGreen").disabled = !ready || !waiting || state.approveAllBusy;
      $("approveAllGreen").title = ready
        ? (waiting ? `把这一卷里能通过的题一次入库（还有 ${waiting} 道要看）；过不了的会逐条说明原因` : "这一卷没有待通过的题")
        : "任务处理完成后才能标记通过";
    }
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

  // 停止本机处理后，原卷和已有成果仍可继续整理。
  async function stopPaper() {
    if (!QBProgress.canStopPaper(state.paper)) return;
    const ok = await confirmDialog({
      title: "停止处理这份任务？",
      text: "停止后保留原卷和已有题卡，可以从原卷继续整理。已经发给云服务的请求可能仍在完成，本机不会再采用这轮迟到的结果。",
      ok: "停止处理"
    });
    if (!ok) return;
    try {
      const data = await api(`/api/papers/${state.paperId}/stop`, { method: "POST", body: {} });
      await refreshPaper();
      await loadPapers();
      toast(data.message || "已停止本机处理，原卷和已有题卡已保留。", "success");
    } catch (error) { toast(error.message, "error"); }
  }

  // 重新解析（1.10.7）：一份试卷在 MinerU 那里等太久时，重新上传给 MinerU。
  async function reparsePaper() {
    if (!state.paper || state.paper.status !== "parsing") return;
    const queueing = state.paper.processing?.mineru?.state === "pending";
    const ok = await confirmDialog({
      title: "重新交给 MinerU 解析？",
      text: queueing
        ? "MinerU 说这份文件还在它那边排队。现在重新上传，会从队尾重新排起，一般不会更快。确定要重新交吗？"
        : "题有据不再等这一次的结果，把文件重新上传给 MinerU。MinerU 很忙的时候，重新上传后也可能要排一会儿队。",
      ok: "重新交给 MinerU"
    });
    if (!ok) return;
    try {
      const data = await api(`/api/papers/${state.paperId}/reparse`, { method: "POST", body: {} });
      toast(data.message || "已重新交给 MinerU", "success");
      refreshPaper();
    } catch (error) { toast(error.message, "error"); }
  }

  async function retryPaper(materialType = null) {
    if (!materialType && state.paper?.parse_mode === "mineru" && !(await confirmDialog({
      title: "重试自动处理？",
      text: "会再次把原卷交给已配置的云服务，并按现有读题设置继续处理，可能使用服务额度。原卷和已有题卡保留；也可以选择“继续整理”，从原卷补题。",
      ok: "重试自动处理",
      cancel: "暂不重试",
      focusCancel: true
    }))) return;
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

  // 名字还是照片、截图的文件名（微信图片_2026…、IMG_1234）：题库里按它找不到这份卷子。
  function looksLikeFileName(name) {
    const text = String(name || "").trim();
    return /^(微信图片|mmexport|wx_camera|IMG[_-]|DSC|DCIM|PXL_|Screenshot|屏幕截图|截图|Image[_ -]?\d|photo[_-]?\d|CamScanner|扫描全能王|未命名)/i.test(text)
      || /^\d{8,}/.test(text);
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
    if (!(await discardEdits())) return;
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
      await clearPaperSelection();
      if (next) await selectPaper(next.id);
      toast(message, kind);
    } catch (error) { toast(error.message, "error"); }
  }

  async function archivePaper() {
    const paper = state.paper;
    if (!paper) return;
    if (paper.archived) { openArchivedPapers(); return; }
    if (ACTIVE_STATUS.has(paper.status)) {
      toast("任务正在处理中，完成后再归档", "error");
      return;
    }
    if ((Number(paper.trash_count) || 0) > 0) {
      toast("回收站里还有题卡；请先恢复这些题卡，再归档任务", "error");
      return;
    }
    if (!(await discardEdits())) return;
    const displayName = paperDisplayName(paper);
    const ok = await confirmDialog({
      title: `归档任务“${displayName}”？`,
      text: "归档后会从左侧任务列表隐藏，原文件、题卡和入库历史都保留。以后可从左侧“已归档的试卷”查看或恢复。",
      ok: "归档任务"
    });
    if (!ok) return;
    const paperId = paper.id;
    const oldIndex = state.papers.findIndex((item) => item.id === paperId);
    try {
      await api(`/api/papers/${paperId}/archive`, { method: "POST", body: {} });
      state.papers = state.papers.filter((item) => item.id !== paperId);
      const next = state.papers[Math.min(Math.max(oldIndex, 0), state.papers.length - 1)];
      await clearPaperSelection();
      if (next) await selectPaper(next.id);
      toast(`已归档任务“${displayName}”；可从“已归档的试卷”找回`, "success",
        { label: "撤销归档", onClick: () => restoreArchivedPaper(paperId) });
    } catch (error) { toast(error.message, "error"); }
  }

  async function restoreArchivedPaper(paperId) {
    if (state.restoringPaperId) return;
    if (state.paperId !== paperId && !(await discardEdits())) return;
    state.restoringPaperId = paperId;
    const restoreButtons = [...$("archivedPapersList").querySelectorAll("button"), $("restoreCurrentPaper")];
    restoreButtons.forEach((node) => { node.disabled = true; });
    try {
      const data = await api(`/api/papers/${paperId}/restore`, { method: "POST", body: {} });
      state.archivedPapers = state.archivedPapers.filter((paper) => paper.id !== paperId);
      const index = state.papers.findIndex((paper) => paper.id === paperId);
      if (index >= 0) state.papers[index] = data.paper;
      else state.papers.unshift(data.paper);
      renderArchivedPapers();
      renderPaperList();
      if ($("archivedPapersDialog").open) $("archivedPapersDialog").close();
      await selectPaper(paperId);
      toast(`已恢复“${paperDisplayName(data.paper)}”，回到这份试卷`, "success");
    } catch (error) { toast(`恢复未完成：${error.message}`, "error"); }
    finally {
      state.restoringPaperId = null;
      restoreButtons.forEach((node) => { node.disabled = false; });
    }
  }

  // ---------------------------------------------------------------- 设置

  function setApiState(id, configured) {
    const node = $(id);
    node.textContent = configured ? "已填写" : "未填写";
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
    setApiState("settingsModelscopeState", Boolean(configured.modelscope));

    // A status response can arrive while another choice is still being saved,
    // or while a model ID is being typed. Keep that newer form intact.
    if (modelFormDirty) return;

    const choices = settingsEngineChoices(engines);
    const modelEntries = choices.map((choice) => ({
      value: choice.key,
      label: `${choice.provider} · ${choice.model}${choice.free ? "（免费）" : ""}${choice.available === false ? "（未填密钥）" : ""}`
    }));
    const defaultPrimary = modelEntries[0]?.value || "";
    // 所选那家没有密钥时，后台会换用有密钥的那家读题；这里直接显示实际读题的那家。
    let primary = selectedEngine(engines, "primary", defaultPrimary);
    const chosen = choices.find((choice) => choice.key === primary);
    if (chosen && chosen.available === false && engines.primary) primary = engines.primary;
    fillModelSelect($("settingsPrimaryModel"), [
      ...modelEntries,
      { value: "assistant", label: "AI 助手读题（只要 MinerU，不用看图密钥）" }
    ], primary);
    renderProviderModelSetting(engines, choices, "minimax", "settingsMinimaxModel",
      "settingsMinimaxModels", "settingsMinimaxModelState");
    renderProviderModelSetting(engines, choices, "siliconflow", "settingsSiliconflowModel",
      "settingsSiliconflowModels", "settingsSiliconflowModelState");
    renderProviderModelSetting(engines, choices, "modelscope", "settingsModelscopeModel",
      "settingsModelscopeModels", "settingsModelscopeModelState");
    // 模型名前面写上是哪一家：只看“Qwen3.5-35B-A3B”，老师认不出是谁家的。
    const named = (key, label) => [choices.find((choice) => choice.key === key)?.provider, label].filter(Boolean).join(" ");
    const summary = [
      status.reader && `读题 ${named(engines.primary, status.reader)}`
    ].filter(Boolean).join("；");
    $("settingsModelSummary").textContent = status.assistant_mode
      ? "现在是 AI 助手读题：导入会先在本机准备原卷并尝试切题，无需 MinerU。未切出的题可手工框选；再由 AI 助手或你对照原卷整理、核对。"
      : summary ? `当前读题：${summary}。` : "请先配置一家读题服务，或选择 AI 助手读题。";
    renderMinimaxPlan(engines);
  }

  // MiniMax 的并发跟会员档位走：档位给出起步和上限，中间按实际限流自动调。
  const PLAN_NOTES = {
    auto: "每个密钥先同时读 3 道，顺利就逐步加到最多 8 道，遇到限流再退回来。不知道自己是哪一档就选这个。",
    plus: "MiniMax 说 Plus 高峰期大约能同时跑 3–4 个；每个密钥同时读 3–4 道。",
    max: "MiniMax 说 Max 高峰期大约能同时跑 4–5 个；每个密钥同时读 4–5 道。",
    ultra: "MiniMax 说 Ultra 高峰期大约能同时跑 6–7 个；每个密钥同时读 6–7 道。",
    payg: "按量付费的密钥按每分钟请求数限流；每个密钥同时读 6 道起，最多 8 道。"
  };

  function renderMinimaxPlan(engines) {
    const select = $("settingsMinimaxPlan");
    if (!select || !$("settingsMinimaxPlanNote")) return;
    const plan = engines.saved?.plans?.minimax || engines.plans?.minimax || "auto";
    select.value = PLAN_NOTES[plan] ? plan : "auto";
    const pending = engines.plans?.minimax && engines.plans.minimax !== select.value;
    $("settingsMinimaxPlanNote").textContent = `${PLAN_NOTES[select.value]}遇到限流会自动放慢，不会出错。${
      pending ? "改动从下一份新资料开始生效。" : ""}`;
  }

  const CREDENTIAL_FIELDS = {
    mineru: { input: "credentialMineruInput", remove: "credentialMineruDelete", state: "credentialMineruState", list: "credentialMineruSaved", label: "MinerU" },
    modelscope: { input: "credentialModelscopeInput", remove: "credentialModelscopeDelete", state: "credentialModelscopeState", list: "credentialModelscopeSaved", label: "魔搭" },
    minimax: { input: "credentialMinimaxInput", remove: "credentialMinimaxDelete", state: "credentialMinimaxState", list: "credentialMinimaxSaved", label: "MiniMax" },
    siliconflow: { input: "credentialSiliconflowInput", remove: "credentialSiliconflowDelete", state: "credentialSiliconflowState", list: "credentialSiliconflowSaved", label: "硅基流动" }
  };
  let credentialBusy = false;
  let credentialStateRequest = 0;
  let credentialSession = 0;
  let credentialFocusFrame = 0;
  let credentialServices = {};
  let credentialTab = "reading";
  let credentialAnswersMounted = false;
  let credentialClosing = false;
  let credentialCloseTask = null;
  let credentialModelGuard = null;
  const credentialSavedRows = new Map();
  const CREDENTIAL_MASK = "****************";

  function hideCredentialKey(row) {
    row.epoch += 1;
    row.controller?.abort();
    row.controller = null;
    clearTimeout(row.timer);
    clearTimeout(row.timeout);
    row.timer = row.timeout = null;
    row.pending = row.visible = false;
    row.value.textContent = CREDENTIAL_MASK;
    row.value.classList.remove("revealed");
    row.button.setAttribute("aria-pressed", "false");
    row.button.setAttribute("aria-label", `查看 ${row.label} 第 ${row.index + 1} 个密钥`);
    row.button.title = "临时查看密钥（60 秒后隐藏）";
  }

  function hideCredentialKeys() {
    credentialSavedRows.forEach(hideCredentialKey);
  }

  async function toggleCredentialKey(service, index) {
    const row = credentialSavedRows.get(`${service}:${index}`);
    if (!row || credentialBusy || !$("credentialDialog").open) return;
    if (row.pending || row.visible) { hideCredentialKey(row); return; }
    const epoch = ++row.epoch, session = credentialSession;
    const controller = new AbortController();
    row.controller = controller;
    row.pending = true;
    row.button.title = "正在读取，再点眼睛可取消";
    row.button.setAttribute("aria-label", `取消查看 ${row.label} 第 ${index + 1} 个密钥`);
    row.timeout = setTimeout(() => controller.abort(), 15000);
    const current = () => row.epoch === epoch && session === credentialSession
      && credentialSavedRows.get(`${service}:${index}`) === row && $("credentialDialog").open;
    try {
      const result = await api("/api/settings/credentials/key/reveal", {
        method: "POST", body: { service, index }, signal: controller.signal
      });
      if (!current() || controller.signal.aborted) return;
      if (result.service !== service || result.index !== index || typeof result.key !== "string" || !result.key) {
        throw new Error("Invalid credential response");
      }
      // Saved values have their own text node. They never enter the replacement
      // inputs or the services payload sent by the Save button.
      row.value.textContent = result.key;
      row.value.classList.add("revealed");
      row.visible = true;
      row.button.setAttribute("aria-pressed", "true");
      row.button.setAttribute("aria-label", `隐藏 ${row.label} 第 ${index + 1} 个密钥`);
      row.button.title = "隐藏密钥";
      row.timer = setTimeout(() => { if (current()) hideCredentialKey(row); }, 60000);
    } catch (error) {
      if (current()) {
        hideCredentialKey(row);
        $("credentialResult").textContent = error.name === "AbortError"
          ? "查看已取消，可再次点击眼睛。" : "暂时无法查看这条密钥，请重新打开窗口后重试。";
      }
    } finally {
      if (current()) {
        clearTimeout(row.timeout);
        row.timeout = null;
        row.controller = null;
        row.pending = false;
        if (!row.visible) hideCredentialKey(row);
      }
    }
  }

  function renderCredentialSavedRows(service, field, count) {
    const list = $(field.list);
    list.replaceChildren();
    if (!count) { list.append(el("li", "credential-saved-empty", "暂无已保存密钥")); return; }
    for (let index = 0; index < count; index += 1) {
      const item = el("li", "credential-saved-row");
      const number = el("span", "credential-saved-number", `${index + 1}`);
      const value = el("span", "credential-saved-value", CREDENTIAL_MASK);
      const eye = el("button", "button quiet small credential-eye");
      eye.type = "button";
      eye.append(icon("eye"));
      eye.disabled = credentialBusy;
      const row = { service, index, label: field.label, value, button: eye,
        epoch: 0, pending: false, visible: false, controller: null, timer: null, timeout: null };
      hideCredentialKey(row);
      credentialSavedRows.set(`${service}:${index}`, row);
      eye.addEventListener("click", () => { void toggleCredentialKey(service, index); });
      item.append(number, value, eye);
      list.append(item);
    }
  }

  function cancelCredentialFocus() {
    if (credentialFocusFrame) cancelAnimationFrame(credentialFocusFrame);
    credentialFocusFrame = 0;
  }

  function focusCredentialControl(id, session, opening) {
    cancelCredentialFocus();
    credentialFocusFrame = requestAnimationFrame(() => {
      credentialFocusFrame = 0;
      if (session !== credentialSession || $("credentialDialog").open !== opening) return;
      // A previous close must not pull focus out of a newly opened dialog.
      if (!opening && anyDialogOpen()) return;
      $(id)?.focus({ preventScroll: true });
    });
  }

  function credentialMutationPending() {
    return credentialBusy || Boolean(credentialModelGuard?.isMutating())
      || Boolean(credentialAnswersMounted && window.LibraryAISettings?.isMutating?.());
  }

  function hideAPISecrets() {
    hideCredentialKeys();
    if (credentialAnswersMounted) window.LibraryAISettings?.hideSecrets?.();
  }

  function credentialHasNewKeys() {
    return Object.values(CREDENTIAL_FIELDS).some((field) => $(field.input).value.trim() !== "");
  }

  function finishCredentialClose({ native = false } = {}) {
    if (credentialMutationPending()) return false;
    if (credentialAnswersMounted && window.LibraryAISettings?.deactivate?.() === false) return false;
    hideAPISecrets();
    credentialStateRequest += 1;
    cancelCredentialFocus();
    if (!native && $("credentialDialog").open) $("credentialDialog").close();
    return true;
  }

  function requestCredentialClose({ native = false } = {}) {
    if (credentialClosing) return false;
    if (credentialMutationPending()) { toast("API 配置正在保存或测试，请稍候再关闭。", "warn"); return false; }
    const aiDirty = credentialAnswersMounted && window.LibraryAISettings?.hasUnsavedChanges?.();
    if (!credentialModelGuard?.hasUnsavedChanges() && !aiDirty && !credentialHasNewKeys()) return finishCredentialClose({ native });
    const session = credentialSession;
    credentialClosing = true;
    hideAPISecrets();
    credentialCloseTask = (async () => {
      // 关闭时若模型设置还没保存，这一步会等一次真实的网络往返。原先这一段
      // 什么都不显示：点“关闭”后窗口像是卡住了，直到保存完才弹出一句提示。
      // 先把要说的话说出来，再去等。
      const savingFirst = credentialModelGuard?.hasUnsavedChanges();
      if (savingFirst) {
        $("credentialResult").textContent = "模型设置还没保存，正在保存后关闭…";
        $("credentialDialog").querySelectorAll("[data-close]").forEach((node) => { node.disabled = true; });
      }
      try {
        if (credentialModelGuard?.hasUnsavedChanges() && !(await credentialModelGuard.prepareClose())) return;
        if (session !== credentialSession || !$("credentialDialog").open || credentialMutationPending()) return;
        const answerDirty = credentialAnswersMounted && window.LibraryAISettings?.hasUnsavedChanges?.();
        if (credentialHasNewKeys() || answerDirty) {
          const discard = await confirmDialog({ title: "API 配置还没保存", text: "关闭会放弃尚未保存的新密钥与标签、答案设置；已经保存的配置保持原样。", ok: "放弃并关闭", cancel: "继续设置", focusCancel: true });
          if (!discard || session !== credentialSession || !$("credentialDialog").open || credentialMutationPending()) return;
          if (answerDirty && window.LibraryAISettings.discard() === false) return;
          resetCredentialInputs();
        }
        if (session === credentialSession && $("credentialDialog").open) finishCredentialClose();
      } finally {
        if (session === credentialSession) {
          credentialClosing = false; credentialCloseTask = null;
          // The close can still be refused (a save failed, or the teacher chose
          // 继续设置).  Put the buttons back and clear the pending line, or the
          // dialog is left looking like the one that hangs.
          if ($("credentialDialog").open) {
            $("credentialResult").textContent = "";
            $("credentialDialog").querySelectorAll("[data-close]").forEach((node) => { node.disabled = credentialBusy; });
          }
        }
      }
    })();
    return false;
  }

  async function showCredentialTab(tab) {
    tab = tab === "answers" ? "answers" : "reading";
    hideAPISecrets();
    cancelCredentialFocus();
    credentialTab = tab;
    $("credentialReadingPanel").hidden = tab !== "reading";
    $("credentialAnswerPanel").hidden = tab !== "answers";
    $("credentialSavedTotal").hidden = tab !== "reading";
    for (const name of ["reading", "answers"]) {
      const button = $(name === "reading" ? "credentialReadingTab" : "credentialAnswerTab");
      button.setAttribute("aria-selected", String(tab === name));
      button.tabIndex = tab === name ? 0 : -1;
    }
    if (tab !== "answers" || !window.LibraryAISettings?.mount) return;
    // The answers panel offers “共用读题的 MiniMax 密钥”, which needs to know
    // whether the reading side holds one.  It must not read that store itself,
    // so the status this dialog already loaded is handed over instead.
    window.LibraryAISettings.setReadingKeys?.(credentialServices);
    if (!credentialAnswersMounted) {
      credentialAnswersMounted = true;
      await window.LibraryAISettings.mount($("libraryAIAPISettingsMount"), { embedded: true, confirm: confirmDialog });
    } else await window.LibraryAISettings.activate();
  }

  function credentialAccounts(value) {
    return [...new Set(String(value || "").split(/[;\r\n]+/)
      .map((item) => item.trim()).filter(Boolean))];
  }

  function resetCredentialInputs() {
    hideCredentialKeys();
    Object.values(CREDENTIAL_FIELDS).forEach((field) => {
      $(field.input).value = "";
      $(field.input).disabled = false;
    });
  }

  function setCredentialBusy(busy) {
    credentialBusy = busy;
    if (busy) hideCredentialKeys();
    credentialSavedRows.forEach((row) => { row.button.disabled = busy; });
    $("credentialSave").disabled = busy;
    Object.entries(CREDENTIAL_FIELDS).forEach(([service, field]) => {
      $(field.input).disabled = busy;
      $(field.remove).disabled = busy || !credentialServices[service]?.configured;
    });
    $("credentialDialog").querySelectorAll("[data-close]").forEach((node) => { node.disabled = busy; });
  }

  function renderCredentialStates(payload) {
    hideCredentialKeys();
    credentialSavedRows.clear();
    const services = payload?.services || {};
    credentialServices = services;
    let total = 0;
    Object.entries(CREDENTIAL_FIELDS).forEach(([service, field]) => {
      const status = services[service] || {};
      const count = status.configured ? Math.min(8, Math.max(0, Math.floor(Number(status.count) || 0))) : 0;
      total += count;
      const node = $(field.state);
      node.textContent = `已保存 ${count} 个`;
      node.className = `api-state ${status.configured ? "ready" : "missing"}`;
      $(field.remove).hidden = !status.configured;
      $(field.remove).disabled = credentialBusy || !status.configured;
      renderCredentialSavedRows(service, field, count);
    });
    $("credentialSavedTotal").textContent = `共保存 ${total} 个密钥`;
  }

  async function loadCredentialStates() {
    const request = ++credentialStateRequest;
    let payload;
    try { payload = await api("/api/settings/credentials"); }
    catch (error) { if (request !== credentialStateRequest) return null; throw error; }
    if (request === credentialStateRequest && $("credentialDialog").open) renderCredentialStates(payload);
    return payload;
  }

  async function openCredentialSettings(tab = "reading") {
    tab = tab === "answers" ? "answers" : "reading";
    if (credentialClosing) return;
    if ($("credentialDialog").open) return showCredentialTab(tab);
    if (credentialBusy) return;
    const session = ++credentialSession;
    cancelCredentialFocus();
    resetCredentialInputs();
    renderCredentialStates({ services: {} });
    $("credentialSavedTotal").textContent = "正在读取已保存的密钥…";
    Object.values(CREDENTIAL_FIELDS).forEach((field) => { $(field.state).textContent = "正在读取…"; });
    $("credentialResult").textContent = "";
    $("credentialDialog").showModal();
    const panelReady = showCredentialTab(tab);
    try {
      await loadCredentialStates();
      if (session === credentialSession && $("credentialDialog").open && credentialTab === "reading") focusCredentialControl("credentialMineruInput", session, true);
    } catch (error) {
      if (session !== credentialSession || !$("credentialDialog").open) return;
      $("credentialSavedTotal").textContent = "暂时无法读取密钥数量";
      $("credentialResult").textContent = error.message;
      toast(error.message, "error");
    }
    await panelReady;
  }

  window.APISettings = Object.freeze({ open: openCredentialSettings });

  async function refreshCredentialStatus(message, session) {
    let refreshed = false;
    try { refreshed = await loadStatus(); } catch { /* The mutation already succeeded. */ }
    if (!refreshed && session === credentialSession && $("credentialDialog").open) {
      $("credentialResult").textContent = `${message} 当前状态暂未刷新，重新打开设置或刷新页面即可查看。`;
    }
  }

  async function deleteCredential(service) {
    const field = CREDENTIAL_FIELDS[service];
    if (!field || credentialBusy || !credentialServices[service]?.configured) return;
    const session = credentialSession;
    let refreshMessage = "";
    setCredentialBusy(true);
    try {
      const confirmed = await confirmDialog({
        title: `删除 ${field.label} 的密钥？`,
        text: "只删除这家服务已保存的密钥。其他服务和输入框里尚未保存的内容都会保留；正在运行的任务不会中途切换账号。",
        ok: "删除密钥", danger: true, focusCancel: true
      });
      if (!confirmed) return;
      credentialStateRequest += 1;
      $("credentialResult").textContent = `正在删除 ${field.label} 密钥…`;
      const result = await api("/api/settings/credentials", { method: "POST", body: { services: { [service]: { action: "clear" } } } });
      renderCredentialStates(result);
      const message = `已删除 ${field.label} 密钥，其他服务和未保存的内容已保留。`;
      $("credentialResult").textContent = message;
      toast(message, "success");
      refreshMessage = message;
    } catch (error) {
      const message = `未能删除 ${field.label} 密钥：${error.message}`;
      $("credentialResult").textContent = message;
      toast(message, "error");
    } finally { setCredentialBusy(false); }
    // The key operation is complete. A slow status refresh must not lock Close.
    if (refreshMessage) await refreshCredentialStatus(refreshMessage, session);
  }

  function renderSettingsTask() {
    const paper = state.paper;
    syncTrashControls();
    if (!paper) return;
    $("viewOriginalPaper").disabled = !paper.pages?.length;
    $("viewOriginalPaper").title = paper.pages?.length ? "查看完整原卷，支持缩放和拖动"
      : "原卷页面尚未生成，处理完成后即可查看";
    $("settingsTaskStatus").textContent = paper.status_label || paperSummary(paper);
    const active = ACTIVE_STATUS.has(paper.status);
    const hasTrash = (Number(paper.trash_count) || 0) > 0;
    $("settingsRename").disabled = active;
    $("settingsRename").title = active ? "处理完成后才能修改名称" : "";
    $("settingsReparse").hidden = !(paper.status === "parsing" && !paper.processing?.chunks && !paper.processing?.parsed_ahead);
    // 1.10.8: a paper still queued or waiting on MinerU can be stopped, then deleted.
    const stoppable = QBProgress.canStopPaper(paper);
    $("settingsStop").hidden = !stoppable;
    const switchingManual = manualSwitches.has(paper.id);
    const continuingAi = aiCutContinuations.has(paper.id), changingCutMode = switchingManual || continuingAi;
    $("settingsManualFallback").hidden = !switchingManual && !QBProgress.canSwitchMinerUToManual(paper);
    $("settingsManualFallback").disabled = changingCutMode;
    $("settingsManualFallback").textContent = switchingManual ? "正在停止并准备手工切题…" : "停止 MinerU，改为手工切题";
    $("settingsStop").disabled = changingCutMode;
    $("settingsReparse").disabled = changingCutMode;
    $("manualProcessing").disabled = $("pageManualCut").disabled = changingCutMode;
    // Keep the recovery action beside the processing status. The local
    // fallback already has a primary cutting action in its three-step panel.
    const cloudCutting = QBProgress.canSwitchMinerUToManual(paper), canContinue = QBProgress.canContinueAiCut(paper);
    $("paperManualEntry").hidden = !switchingManual && !cloudCutting;
    $("paperManualFallback").hidden = !switchingManual && !cloudCutting;
    $("paperManualFallback").disabled = changingCutMode;
    $("paperManualFallback").textContent = switchingManual ? "正在准备手工切题…" : "改为手工切题";
    $("paperContinueAi").hidden = !continuingAi && !canContinue;
    $("paperContinueAi").disabled = changingCutMode || (!cloudCutting && (paperReadSubmissionPending(paper.id)
      || state.questions.some(q => q.ocr_pending || q.reread_requested)));
    $("paperContinueAi").textContent = continuingAi ? "正在继续 AI 切题…" : "继续 AI 切题";
    $("paperContinueAi").title = cloudCutting ? "保留当前任务，继续等待，不重新提交"
      : "优先使用本机已有解析；需要重新提交原稿时会先说明";
    $("paperManualHint").textContent = switchingManual ? "正在停止本机等待并准备原卷，已有成果保留。"
      : continuingAi ? "正在恢复自动切题，原卷和已保存题目保留。"
        : cloudCutting ? "可以停止等待，直接框题；原卷和已有题目保留。"
          : "手工范围和已保存题目保留，也可继续 AI 切题补充未切出的题目。";
    const groups = suggestedSplitGroups(paper);
    $("settingsConfirmStructure").hidden = paper.status !== "needs_grouping";
    $("settingsSplit").hidden = !(paper.structure_conflict && groups.length > 1);
    $("settingsSplit").textContent = groups.length > 1 ? `按建议拆成 ${groups.length} 份` : "拆分任务";

    const notes = [...(paper.notes || [])];
    const parseReason = paper.processing_plan?.fallback_reason;
    if (parseReason && !notes.includes(parseReason)) notes.push(parseReason);
    const structureMessage = paper.structure_message
      || (typeof paper.structure_conflict === "object" && paper.structure_conflict.message);
    if (structureMessage && !notes.includes(structureMessage)) notes.unshift(structureMessage);
    if (paper.error && !notes.includes(paper.error)) notes.unshift(paper.error);
    $("settingsTaskNotes").replaceChildren(...(notes.length ? notes : ["暂无处理记录。"])
      .map((note) => el("li", "", note)));
    $("paperNotesCount").textContent = String(notes.length);

    const published = paper.counts?.published || 0;
    $("settingsArchive").hidden = Boolean(paper.archived);
    $("settingsArchive").disabled = active || hasTrash;
    $("settingsArchive").title = hasTrash
      ? "回收站里还有题卡；请先恢复后再归档" : "";
    const isSplitTask = Boolean(paper.structure?.split_from || paper.structure?.split_children?.length);
    $("settingsDelete").hidden = published > 0 || isSplitTask
      || !["ready", "failed", "needs_grouping"].includes(paper.status);
    $("settingsDangerHint").textContent = published > 0
      ? `已有 ${published} 道正式题库记录。为保留来源追溯，只能归档，不能永久删除。`
      : isSplitTask ? "这是拆分资料的原稿或子任务；为保留双向追溯，只能归档。"
      : active && stoppable ? "任务正在处理中：先点上面的“停止处理”，停下来以后就能删除。"
      : active ? "任务正在处理中，完成或失败后才能永久删除。"
        : "归档只从任务列表隐藏；永久删除会一并删除原文件和草稿，无法撤销。";
  }

  function closeSettingsThen(action) {
    if ($("settingsDialog").open) $("settingsDialog").close();
    if ($("paperMenu").open) $("paperMenu").open = false;
    requestAnimationFrame(action);
  }

  const SETTINGS_HASHES = { settingsGeneral: "services", settingsDisplay: "display", settingsReview: "help", settingsAbout: "about" };
  function settingsTabFromHash() {
    const hash = window.location.hash.slice(1);
    return Object.keys(SETTINGS_HASHES).find((id) => SETTINGS_HASHES[id] === hash) || "settingsGeneral";
  }

  function showSettingsTab(id, { updateHash = true } = {}) {
    if (!Object.hasOwn(SETTINGS_HASHES, id)) id = "settingsGeneral";
    document.querySelectorAll("[data-settings-tab]").forEach((tab) => {
      const active = tab.dataset.settingsTab === id;
      tab.setAttribute("aria-selected", String(active));
      tab.tabIndex = active ? 0 : -1;
    });
    document.querySelectorAll("#settingsDialog .settings-page").forEach((page) => { page.hidden = page.id !== id; });
    if (id === "settingsDisplay") void window.ExportSettings?.refresh?.();
    const scroller = document.querySelector("#settingsDialog .settings-scroll");
    if (scroller) scroller.scrollTop = 0;
    if (window.location.pathname === "/settings" && updateHash) {
      window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}#${SETTINGS_HASHES[id]}`);
    }
  }
  function syncSettingsRoute() {
    showSettingsTab(settingsTabFromHash(), { updateHash: false });
    // Existing answer-setting links keep opening the answer section of the one
    // API window, without bringing back a second settings page or entry point.
    if (["#ai", "#api"].includes(window.location.hash)) void openCredentialSettings("answers");
  }
  window.addEventListener("hashchange", () => {
    if (window.location.pathname === "/settings") syncSettingsRoute();
  });

  document.querySelectorAll("[data-settings-tab]").forEach((tab, index, tabs) => {
    tab.addEventListener("click", () => showSettingsTab(tab.dataset.settingsTab));
    tab.addEventListener("keydown", (event) => {
      if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
      event.preventDefault();
      const next = tabs[(index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length];
      showSettingsTab(next.dataset.settingsTab);
      next.focus();
    });
  });

  function renderSettingsReady() {
    const s = state.status;
    const node = $("settingsReady");
    if (!s || !node) return;
    const configured = s.configured || s.engines?.configured || {};
    const vision = Boolean(configured.minimax || configured.siliconflow || configured.modelscope);
    const missing = [!s.mineru && "MinerU", !vision && !s.assistant_mode && "一家看图读题服务"].filter(Boolean);
    node.className = `settings-ready ${automaticParseReady() ? "ready" : "missing"}`;
    node.textContent = automaticParseReady()
      ? "导入时先在本机切题。云处理已配置，经你允许后用于本机无法切出的资料；实际可用性以处理结果为准。"
      : missing.length ? `导入资料和从原卷选题可直接使用，无需密钥。可选的云处理还需 ${missing.join("、")} 密钥。`
        : "导入资料和从原卷选题可直接使用。需要云处理时再配置读题服务。";
  }

  // 设置 → 题面与显示（存在数据目录的 features.json，网页和后台共用）。
  async function loadFeatureSwitches() {
    const box = $("featureSwitches");
    try {
      const data = await api("/api/settings/features");
      renderFeatureSwitches(data);
    } catch (error) {
      box.replaceChildren(el("p", "settings-footnote", `没读到功能开关：${error.message}`));
    }
  }

  function renderFeatureSwitches(data) {
    const box = $("featureSwitches");
    box.replaceChildren();
    const advancedBox = $("featureAdvancedSwitches");
    advancedBox.replaceChildren();
    (data.features || []).filter((item) => !["knowledge_tags", "ai_answer", "double_read"].includes(item.key)).forEach((item) => {
      const label = el("label", "settings-switch");
      const text = el("span");
      const labels = { origin_split: "提取题源", chinese_quotes: "统一中文引号", subquestions: "显示小问数" };
      const descriptions = { origin_split: "将题干开头的出处移到题源字段。", chinese_quotes: "统一中文句子的引号，保留公式。", subquestions: "在题库标注题目包含的小问数量。" };
      text.append(el("strong", "", labels[item.key] || item.label));
      if (descriptions[item.key]) text.append(el("small", "", descriptions[item.key]));
      const input = el("input");
      input.type = "checkbox";
      input.setAttribute("role", "switch");
      input.checked = Boolean(item.enabled);
      input.addEventListener("change", async () => {
        input.disabled = true;
        try {
          const saved = await api("/api/settings/features", { method: "POST", body: { features: { [item.key]: input.checked } } });
          renderFeatureSwitches(saved);
          toast(saved.message || "已保存", "success");
        } catch (error) {
          input.checked = !input.checked;
          toast(error.message, "error");
        } finally {
          input.disabled = false;
        }
      });
      label.append(text, input);
      (["origin_split", "chinese_quotes"].includes(item.key) ? advancedBox : box).append(label);
    });
    const note = $("featureNote");
    const tags = (data.features || []).find((item) => item.key === "knowledge_tags");
    $("knowledgeDetails").hidden = !(tags?.enabled && data.knowledge_file);
    note.textContent = data.knowledge_file || "";
  }

  function openSettings() {
    if (window.location.pathname !== "/settings") { void leaveFor("/settings"); return; }
    void loadFeatureSwitches();
    renderSettingsModels();
    renderSettingsReady();
    $("settingsLens").checked = state.lens;
    $("settingsFocus").checked = state.focus;
    $("settingsAutoExpand").checked = state.autoExpand;
    if (!modelFormDirty) showModelSaveResult("");
    syncSettingsRoute();
    void loadStatus();
    requestAnimationFrame(() => { if (!anyDialogOpen()) $("settingsTitle").focus({ preventScroll: true }); });
  }

  $("settingsButton").addEventListener("click", (event) => {
    if (window.location.pathname === "/settings") { event.preventDefault(); openSettings(); }
  });
  document.addEventListener("library-ai-settings-saved", () => { void loadFeatureSwitches(); });
  $("settingsCredentialOpen").addEventListener("click", openCredentialSettings);
  $("settingsLens").addEventListener("change", (event) => setLens(event.target.checked));
  $("settingsFocus").addEventListener("change", (event) => setFocus(event.target.checked));
  $("settingsAutoExpand").addEventListener("change", (event) => {
    state.autoExpand = event.target.checked;
    writePref("qb-auto-expand", state.autoExpand ? "1" : "0");
  });
  $("paperMenu").addEventListener("toggle", () => { if ($("paperMenu").open) renderSettingsTask(); });
  $("settingsRename").addEventListener("click", () => closeSettingsThen(openRenameDialog));
  $("settingsReparse").addEventListener("click", () => closeSettingsThen(reparsePaper));
  $("settingsStop").addEventListener("click", () => closeSettingsThen(stopPaper));
  $("settingsManualFallback").addEventListener("click", () => {
    const paperId = state.paperId;
    closeSettingsThen(() => {
      if (state.paperId === paperId) void switchToManual(null, { stopMinerU: true });
    });
  });
  $("paperManualFallback").addEventListener("click", () => {
    void switchToManual(null, { stopMinerU: true });
  });
  $("paperContinueAi").addEventListener("click", () => {
    $("paperMenu").open = false;
    void continueAiCut();
  });
  $("renameNudge").addEventListener("click", openRenameDialog);
  $("settingsConfirmStructure").addEventListener("click", () => closeSettingsThen(confirmStructure));
  $("settingsSplit").addEventListener("click", () => closeSettingsThen(openSplitDialog));
  $("settingsArchive").addEventListener("click", () => closeSettingsThen(archivePaper));
  $("settingsDelete").addEventListener("click", () => closeSettingsThen(deletePaper));
  $("questionTrash").addEventListener("click", openQuestionTrash);
  $("approveAllGreen").addEventListener("click", approveAllGreen);

  Object.entries(CREDENTIAL_FIELDS).forEach(([service, field]) => {
    $(field.remove).addEventListener("click", () => deleteCredential(service));
  });

  for (const [id, tab] of [["credentialReadingTab", "reading"], ["credentialAnswerTab", "answers"]]) {
    $(id).addEventListener("click", () => { void showCredentialTab(tab); });
    $(id).addEventListener("keydown", (event) => {
      if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
      event.preventDefault();
      const next = tab === "reading" ? "answers" : "reading";
      void showCredentialTab(next);
      $(next === "reading" ? "credentialReadingTab" : "credentialAnswerTab").focus();
    });
  }

  $("credentialDialog").addEventListener("cancel", (event) => {
    if (!requestCredentialClose({ native: true })) event.preventDefault();
  });
  document.addEventListener("visibilitychange", () => { if (document.hidden) hideAPISecrets(); });
  window.addEventListener("pagehide", hideAPISecrets);
  $("credentialDialog").addEventListener("click", (event) => {
    if (credentialBusy && event.target === $("credentialDialog")) { event.preventDefault(); event.stopImmediatePropagation(); }
  }, true);

  $("credentialDialog").addEventListener("close", () => {
    // Native close events are queued; reopening can happen before this runs.
    if ($("credentialDialog").open) return;
    const session = ++credentialSession;
    credentialClosing = false;
    credentialCloseTask = null;
    credentialStateRequest += 1;
    cancelCredentialFocus();
    if (credentialAnswersMounted) window.LibraryAISettings?.deactivate?.();
    resetCredentialInputs();
    $("credentialResult").textContent = "";
    focusCredentialControl("settingsCredentialOpen", session, false);
  });

  $("credentialForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (credentialBusy) return;
    const services = {};
    for (const [service, field] of Object.entries(CREDENTIAL_FIELDS)) {
      const accounts = credentialAccounts($(field.input).value);
      if (accounts.length > 8) {
        const message = `${field.label} 最多保存 8 个账号`;
        $("credentialResult").textContent = message;
        toast(message, "error");
        return;
      }
      services[service] = accounts.length ? { action: "replace", accounts } : { action: "keep" };
    }
    if (!Object.values(services).some((change) => change.action === "replace")) {
      $("credentialResult").textContent = "没有填写新的密钥，已保存的密钥保持不变。";
      return;
    }
    const session = credentialSession;
    let refreshMessage = "";
    setCredentialBusy(true);
    credentialStateRequest += 1;
    $("credentialResult").textContent = "正在检查填写格式并加密保存…";
    try {
      const result = await api("/api/settings/credentials", { method: "POST", body: { services } });
      resetCredentialInputs();
      renderCredentialStates(result);
      const message = result.message || "API 配置已加密保存；下一项任务开始时生效。";
      $("credentialResult").textContent = message;
      toast(message, "success");
      refreshMessage = message;
    } catch (error) {
      $("credentialResult").textContent = error.message;
      toast(error.message, "error");
    } finally {
      setCredentialBusy(false);
    }
    if (refreshMessage) await refreshCredentialStatus(refreshMessage, session);
  });

  // Queue the values captured at each change, rather than reading the form
  // later, after an older response may have arrived.
  let modelSaving = Promise.resolve();
  let modelFormDirty = false;
  let lastModelSave = null;
  let modelRetryButton = null;
  const MODEL_SAVED_MESSAGE = "已保存；从下一项新任务或重新识读开始生效，不会改写现有题卡。";

  function readModelSettings() {
    return {
      primary: $("settingsPrimaryModel").value,
      models: {
        minimax: $("settingsMinimaxModel").value.trim(),
        siliconflow: $("settingsSiliconflowModel").value.trim(),
        modelscope: $("settingsModelscopeModel").value.trim()
      },
    };
  }

  function showModelSaveResult(message, { retry = false } = {}) {
    $("settingsModelResult").textContent = message;
    if (!modelRetryButton && retry) {
      modelRetryButton = button("重试保存", "small", () => { void saveModelSettings(); });
      modelRetryButton.id = "settingsModelRetry";
      $("settingsModelResult").parentElement.append(modelRetryButton);
    }
    if (modelRetryButton) modelRetryButton.hidden = !retry;
  }

  function saveModelSettings({ refresh = true } = {}) {
    const body = readModelSettings();
    const signature = JSON.stringify(body);
    // Native blur/change and dialog cancel/close can describe the same edit.
    // Keep one request; a failed request remains explicitly retryable.
    if (lastModelSave?.signature === signature && !lastModelSave.failed) {
      if (lastModelSave.succeeded) {
        modelFormDirty = false;
        showModelSaveResult(MODEL_SAVED_MESSAGE);
      }
      return modelSaving;
    }
    const change = { body, signature, failed: false, succeeded: false };
    lastModelSave = change;
    modelFormDirty = true;
    showModelSaveResult("正在保存…");
    modelSaving = modelSaving.then(
      () => saveModelSettingsNow(change, { refresh }), () => saveModelSettingsNow(change, { refresh }));
    return modelSaving;
  }

  const modelSettingFields = ["settingsPrimaryModel", "settingsMinimaxModel",
    "settingsSiliconflowModel", "settingsModelscopeModel"];
  modelSettingFields.forEach((id) => $(id).addEventListener("input", () => {
    modelFormDirty = true;
    showModelSaveResult("有改动待保存；离开输入框后会自动保存。", { retry: Boolean(lastModelSave?.failed) });
  }));
  modelSettingFields
    .forEach((id) => $(id).addEventListener("change", () => { void saveModelSettings(); }));
  $("modelSettingsForm").addEventListener("submit", (event) => {
    event.preventDefault();
    void saveModelSettings();
  });

  credentialModelGuard = {
    hasUnsavedChanges: () => modelFormDirty,
    isMutating: () => Boolean(lastModelSave && !lastModelSave.succeeded && !lastModelSave.failed),
    async prepareClose() {
      if (!modelFormDirty) return true;
      if (lastModelSave?.failed && lastModelSave.signature === JSON.stringify(readModelSettings())) {
        showModelSaveResult("模型设置尚未保存，请重试保存后再关闭。", { retry: true });
        return false;
      }
      // The close waits for the save and nothing else: the status refresh it
      // used to await as well has no bearing on whether this dialog may close.
      await saveModelSettings({ refresh: false });
      return !modelFormDirty;
    }
  };

  async function prepareSettingsLeave() {
    if ($("credentialDialog").open) {
      if (!requestCredentialClose() && credentialCloseTask) await credentialCloseTask;
      if ($("credentialDialog").open) return false;
    }
    if (window.location.pathname !== "/settings") return true;
    if (window.LibraryAISettings?.isMutating?.()) { toast("设置正在处理，请稍候再离开。", "warn"); return false; }
    if (modelFormDirty) {
      const signature = JSON.stringify(readModelSettings());
      if (lastModelSave?.failed && lastModelSave.signature === signature) {
        showModelSaveResult("模型设置尚未保存，请重试保存后再离开。", { retry: true });
        return false;
      }
      await saveModelSettings();
      await modelSaving;
      if (modelFormDirty) return false;
    }
    if (window.LibraryAISettings?.isMutating?.()) { toast("设置正在处理，请稍候再离开。", "warn"); return false; }
    if (window.LibraryAISettings?.hasUnsavedChanges?.()) {
      const discard = await confirmDialog({ title: "标签与答案设置还没保存", text: "离开会放弃本次设置改动；已经保存的开关和密钥保持原样。", ok: "放弃并离开", cancel: "继续设置", focusCancel: true });
      if (!discard) return false;
      await window.LibraryAISettings.discard();
    }
    return true;
  }

  async function saveModelSettingsNow(change, { refresh = true } = {}) {
    try {
      await api("/api/settings/models", { method: "POST", body: change.body });
      change.succeeded = true;
      if (change !== lastModelSave) return;
      // An uncommitted text edit is also a newer intent. A completed save must
      // not replace it with the older value before blur/change submits it.
      if (JSON.stringify(readModelSettings()) !== change.signature) {
        showModelSaveResult("有新改动待保存；离开输入框后会自动保存。");
        return;
      }
      modelFormDirty = false;
      const message = MODEL_SAVED_MESSAGE;
      showModelSaveResult(message);
      toast(message, "success");
      if (!refresh) {
        // Closing the dialog is gated on the save, not on the status refresh
        // behind it: two round trips in series is what made 关闭 feel like a
        // hang, and the refreshed status is only worth having once the window
        // is already gone.
        void loadStatus();
        return;
      }
      const refreshed = await loadStatus();
      if (!refreshed && change === lastModelSave && !modelFormDirty) {
        showModelSaveResult(`${message} 当前状态暂未刷新，重新打开设置即可查看。`);
      }
    } catch (error) {
      change.failed = true;
      if (change !== lastModelSave) return;
      const message = `未保存：${error.message}。你的选择仍保留，可以重试保存。`;
      showModelSaveResult(message, { retry: true });
      toast(message, "error");
    }
  }

  function renderPublishButton(c = counts(), structureBlocked = state.paper?.status === "needs_grouping") {
    const button = $("publishButton");
    button.hidden = true;
    const running = state.publishing && state.publishing.paperId === state.paperId ? state.publishing : null;
    button.classList.toggle("is-busy", Boolean(running));
    button.setAttribute("aria-busy", String(Boolean(running)));
    if (running) {
      button.disabled = true;
      button.textContent = `正在入库 ${running.done} / ${running.total} 题…`;
      return;
    }
    button.disabled = !c.unpublished || structureBlocked;
    button.textContent = c.unpublished ? `入库（${c.unpublished} 题）` : "入库";
  }

  // 一次送 20 题：一本书几百题时，按钮上能看到入库到了哪里（1.10.5）。
  const PUBLISH_BATCH = 20;

  async function publish() {
    if (state.publishing) return;
    if (state.paper?.demo) {
      await confirmDialog({
        title: "示例试卷不会入库",
        text: "示例试卷只用来练习，不会进入正式题库。你自己的试卷逐题核对后打勾，通过即自动入库，可以搜索、组卷。",
        ok: "知道了"
      });
      teach({ type: "publish" });
      return;
    }
    const paperId = state.paperId;
    const ids = state.questions.filter((q) => isApproved(q) && !(q.publication && q.publication.up_to_date)).map((q) => q.id);
    const batches = [];
    for (let start = 0; start < ids.length; start += PUBLISH_BATCH) batches.push(ids.slice(start, start + PUBLISH_BATCH));
    if (!batches.length) batches.push(null);  // nothing new on screen: let the server check every approved card
    const data = { created: 0, unchanged: 0, problems: [] };
    state.publishing = { paperId, done: 0, total: ids.length };
    renderPublishButton();
    if (ids.length > PUBLISH_BATCH) toast(`正在入库 ${ids.length} 题，按钮上显示进度`);
    try {
      for (const batch of batches) {
        const result = await api(`/api/papers/${paperId}/publish`, { method: "POST", body: batch ? { question_ids: batch } : {} });
        data.created += result.created;
        data.unchanged += result.unchanged;
        data.problems.push(...result.problems);
        state.publishing.done += batch ? batch.length : 0;
        if (state.paperId === paperId) renderPublishButton();
      }
    } catch (error) {
      state.publishing = null;
      toast(data.created ? `已入库 ${data.created} 题，其余没完成：${error.message}` : error.message, "error");
      if (state.paperId === paperId) { renderPublishButton(); refreshPaper(); }
      loadPapers();
      return;
    }
    state.publishing.done = state.publishing.total;
    if (state.paperId === paperId) renderPublishButton();
    state.publishing = null;
    const parts = [];
    if (data.created) parts.push(`新入库 ${data.created} 题`);
    if (data.unchanged) parts.push(`${data.unchanged} 题内容没变`);
    if (data.problems.length) parts.push(`${data.problems.length} 题没入库：${data.problems.join("；")}`);
    toast(parts.join("，") || "没有需要入库的题", data.problems.length ? "error" : "success",
      data.created && !data.problems.length ? { label: "去题库看看", onClick: () => leaveFor("/library") } : null);
    // The button keeps its last count until the refreshed counts redraw it.
    if (state.paperId === paperId) refreshPaper();
    loadPapers();
  }

  $("publishButton").addEventListener("click", publish);

  // ---------------------------------------------------------------- 改字

  // 1.12.5：改字面板以前崩在构建过程中，异常被吞掉，只留下"点了没反应"
  // 而且 state.editing 里的 id 再也清不掉，那张卡本次会话再也开不了改字。
  // 这里不吞异常：崩了就当场说明，并把卡片恢复成没在改的样子。
  function openEditor(card, q, options) {
    try {
      openEditorPanel(card, q, options);
    } catch (error) {
      state.editing.delete(q.id);
      editGuard.release(q.id);
      card.querySelector(".editor")?.remove();
      card.classList.remove("editing");
      for (const [selector, hidden] of [[".rendered", false], [".card-actions", false], [".card-origin", false], [".card-toggle", false]]) {
        const node = card.querySelector(selector);
        if (node) node.hidden = hidden;
      }
      toast(`改字面板没能打开：${error && error.message ? error.message : error}`, "error");
    }
  }

  function openEditorPanel(card, q, { prefill = null, regionInsert = null } = {}) {
    if (card.querySelector(".editor")) {
      if (prefill || regionInsert) toast("先保存或取消正在改的字，再填入框选识读的结果", "error");
      card.querySelector(".stem-input")?.focus();
      return;
    }
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
    const origin = el("input", "origin-input");
    origin.value = q.origin || "";
    origin.maxLength = 120;
    origin.placeholder = "题干前印的出处，例如 2026山东枣庄滕州二中月考；没有就留空";
    origin.spellcheck = false;
    const originRow = el("label", "field inline");
    originRow.append(el("span", "", "题源"), origin);
    const stem = el("textarea", "stem-input");
    stem.value = q.stem;
    stem.rows = 2;
    stem.spellcheck = false;
    // The box is as tall as the text in it (up to 40% of the window), so the
    // live preview right under it stays in sight.
    const fitStem = () => {
      stem.style.height = "auto";
      stem.style.height = `${Math.min(stem.scrollHeight + 2, Math.round(window.innerHeight * 0.4))}px`;
    };
    const stemRow = el("label", "field");
    stemRow.append(el("span", "", "题干（公式用 $…$ 包住的 LaTeX）"), stem);
    if (regionInsert) {
      const insert = el("div", "region-read-insert");
      insert.append(el("p", "hint", "在题干里选中要改的文字，或点一下放置光标，再点“填入识读文字”。保存后生效。"));
      const value = el("div", "region-read-text");
      R.renderTypeset(value, regionInsert);
      insert.append(value, button("填入识读文字", "small primary", () => {
        const start = stem.selectionStart, end = stem.selectionEnd;
        stem.value = stem.value.slice(0, start) + regionInsert + stem.value.slice(end);
        stem.focus(); stem.setSelectionRange(start, start + regionInsert.length);
        stem.dispatchEvent(new Event("input", { bubbles: true }));
      }));
      stemRow.append(insert);
    }
    // Tables are text: pipe rows under the stem, previewed as a real table.
    const tableTools = el("div", "table-tools");
    const editTable = (change) => {
      const next = change(stem.value, stem.selectionStart);
      if (!next) return;
      stem.value = next.value;
      stem.focus();
      stem.setSelectionRange(next.cursor, next.cursor);
      stem.dispatchEvent(new Event("input", { bubbles: true }));
    };
    tableTools.append(
      button("插入表格", "small", () => editTable(QBTableText.insert)),
      button("表格加一行", "small", () => editTable((value, cursor) => QBTableText.grow(value, cursor, "row") || (toast("先把光标放在要加行的表格里", "error"), null))),
      button("表格加一列", "small", () => editTable((value, cursor) => QBTableText.grow(value, cursor, "col") || (toast("先把光标放在要加列的表格里", "error"), null))),
      el("small", "hint", "表格每行一行，格子用 | 隔开；第二行的 |---| 表示第一行是表头；空格子留空，预览里会排成表格")
    );

    const optionBox = el("div", "option-inputs");
    const optionInputs = {};
    OPTION_KEYS.forEach((key) => {
      const row = el("label", "field inline");
      const input = el("input");
      input.value = (q.options || {})[key] || "";
      input.spellcheck = false;
      optionInputs[key] = input;
      if (key === "E") {
        // Most choice questions stop at D; E is there for the ones that print it.
        input.placeholder = "原卷没有 E 选项就留空";
        row.classList.add("optional");
      }
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
    // 实时预览：边打字边排版成保存后的样子。并排布局时放在左栏原卷截图
    // 下面，和原卷上下对照；截图太高、上下堆叠或窄屏时放在题干输入框下面。
    const previewBox = el("div", "editor-preview-box");
    const preview = el("div", "editor-preview");
    // These two lines only explain what is already happening. They can be
    // switched off permanently; the position markers they describe and the
    // save shortcut they mention keep working either way.
    const positionHint = window.QBShortcutHelp?.hintDismissed?.("editor-position")
      ? el("p", "editor-position-hint", "") : el("p", "editor-position-hint", "点击输入框，预览会标出正在修改的位置。");
    positionHint.hidden = !positionHint.textContent;
    previewBox.append(el("p", "preview-label", "预览 · 随输入实时更新"), positionHint, preview);
    const saveButton = el("button", "button primary", "保存");
    saveButton.type = "submit";
    const cancel = button("取消", "", () => discardEdits([q.id]));
    const bar = el("div", "editor-actions");
    const shortcutNote = window.QBShortcutHelp?.hintDismissed?.("editor-shortcut")
      ? null : el("p", "hint", "Ctrl+Enter 保存 · Esc 取消");
    bar.append(saveButton, cancel);
    if (shortcutNote) bar.append(shortcutNote);
    editor.append(title, typeRow, originRow, stemRow, previewBox, tableTools, optionBox, extra, bar);

    const collect = () => ({
      stem: stem.value,
      options: Object.fromEntries(OPTION_KEYS.map((k) => [k, optionInputs[k].value]).filter(([, v]) => v.trim())),
      question_type: typeSelect.value,
      answer: answer.value,
      analysis: analysis.value,
      origin: origin.value
    });
    editGuard.track(q.id, collect, () => close(), () => stem.focus({ preventScroll: true }));
    const previewInputs = new Map([[stem, "stem"], [answer, "answer"], [analysis, "analysis"],
      ...OPTION_KEYS.map((key) => [optionInputs[key], key])]);
    const fieldName = (field) => ({ stem: "题干", answer: "答案", analysis: "解析" }[field] || `${field} 选项`);
    let activePreviewInput = stem;
    let composing = false;
    let positionFrame = 0;
    const showPosition = () => {
      const input = activePreviewInput;
      if (!input || !previewInputs.has(input)) return;
      const field = previewInputs.get(input);
      const shown = R.previewSelection(preview, { field, start: input.selectionStart ?? 0, end: input.selectionEnd ?? 0 });
      const detail = composing ? "正在输入，预览会跟随输入内容更新。"
        : shown?.kind === "math" ? (shown.automatic
          ? "蓝框对应正在修改的数字或数学内容；实际输入位置请看输入框光标。"
          : shown.exact
          ? "绿色符号对应光标附近或所选范围，蓝框是这条公式；实际插入位置请看输入框光标。"
          : "蓝框是正在修改的公式；公式内的位置请看输入框光标。$…$ 和反斜杠是公式写法。")
        : shown?.kind === "table" ? "正在修改表格；分隔线和 | 用来排表格，输入框保留原写法。"
        : shown?.kind === "blank" ? "正在修改这处填空；输入框保留原来的括号或下划线。"
        : shown?.collapsed ? "绿线是当前输入位置。选中文字时，预览会标出对应范围。"
        : "浅绿色是当前选中的文字；替换后预览会更新。";
      positionHint.textContent = `${fieldName(field)} · ${detail}`;
      preview.dataset.activeField = field;
      // Reveal a position only inside this preview's own scroll area. Never
      // move the page, and leave its scroll alone while the position is visible.
      const symbol = Array.from(preview.querySelectorAll(".qb-preview-active-symbol [style]"))
        .find((node) => node.style.color === "rgb(31, 107, 95)" || node.style.color === "#1f6b5f");
      const target = symbol || preview.querySelector(".qb-preview-caret, .qb-preview-selection, .qb-preview-active-formula, .qb-preview-active-region");
      if (target) {
        const bounds = preview.getBoundingClientRect();
        const rect = target.getBoundingClientRect();
        if (preview.scrollHeight > preview.clientHeight) {
          if (rect.top < bounds.top + 10) preview.scrollTop -= bounds.top + 10 - rect.top;
          else if (rect.bottom > bounds.bottom - 10 && rect.height < preview.clientHeight - 20) preview.scrollTop += rect.bottom - bounds.bottom + 10;
        }
        if (preview.scrollWidth > preview.clientWidth) {
          if (rect.left < bounds.left + 10) preview.scrollLeft -= bounds.left + 10 - rect.left;
          else if (rect.right > bounds.right - 10 && rect.width < preview.clientWidth - 20) preview.scrollLeft += rect.right - bounds.right + 10;
        }
      }
    };
    const updatePosition = (event) => {
      if (previewInputs.has(event?.target)) activePreviewInput = event.target;
      if (positionFrame) return;
      positionFrame = requestAnimationFrame(() => { positionFrame = 0; showPosition(); });
    };
    previewInputs.forEach((field, input) => {
      ["focus", "click", "select", "keyup"].forEach((name) => input.addEventListener(name, updatePosition));
      input.addEventListener("compositionstart", (event) => { composing = true; updatePosition(event); });
      input.addEventListener("compositionend", (event) => { composing = false; updatePosition(event); });
    });
    const selectionChanged = () => { if (previewInputs.has(document.activeElement)) updatePosition({ target: document.activeElement }); };
    document.addEventListener("selectionchange", selectionChanged);
    // One typeset per frame however fast the typing is.
    let frame = 0;
    const update = (event) => {
      if (previewInputs.has(event?.target)) activePreviewInput = event.target;
      optionBox.hidden = !CHOICE.has(typeSelect.value) && !OPTION_KEYS.some((k) => optionInputs[k].value.trim());
      if (frame) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        const data = collect();
        const scroll = { top: preview.scrollTop, left: preview.scrollLeft };
        R.renderQuestion(preview, { ...content(q), ...data, body_mode: "text", options: optionBox.hidden ? {} : data.options }, { showNumber: false, showAnswer: "open", trackSource: true });
        preview.scrollTop = scroll.top;
        preview.scrollLeft = scroll.left;
        showPosition();
      });
    };
    const placePreview = () => {
      const source = card.querySelector(".source-sticky");
      const shot = source?.querySelector(".crop, .crop-missing");
      const room = window.innerHeight - viewTop() - 48 - (shot?.offsetHeight || 0);
      const beside = Boolean(source && getComputedStyle(source).position === "sticky" && room >= 180);
      if (beside && previewBox.parentNode !== source) source.append(previewBox);
      else if (!beside && previewBox.previousElementSibling !== stemRow) stemRow.after(previewBox);
      previewBox.classList.toggle("beside", beside);
      previewBox.style.setProperty("--preview-room", beside ? `${Math.round(room - 30)}px` : "none");
    };
    const relayout = () => { fitStem(); placePreview(); updatePosition(); };
    editor.addEventListener("input", update);
    // A taller stem changes the room the preview may use, and the original crop
    // only knows its height once the image has loaded. Both used to leave the
    // layout measured against a stale height until the window was resized.
    stem.addEventListener("input", relayout);
    window.addEventListener("resize", relayout);
    const shot = card.querySelector(".source-sticky .crop img, .source-sticky .crop");
    if (shot && !shot.complete) shot.addEventListener("load", relayout, { once: true });
    if (window.ResizeObserver) {
      const observer = new ResizeObserver(() => { fitStem(); placePreview(); });
      observer.observe(stem);
      editor.addEventListener("qb:editor-teardown", () => observer.disconnect(), { once: true });
    }
    editor.addEventListener("keydown", (event) => {
      if (event.defaultPrevented || event.isComposing || event.keyCode === 229 || event.altKey) return;
      if (event.key === "Escape" && !event.ctrlKey && !event.metaKey && !event.shiftKey) { event.preventDefault(); event.stopPropagation(); discardEdits([q.id]); }
      else if (event.key === "Enter" && (event.ctrlKey || event.metaKey) && !event.shiftKey) {
        event.preventDefault();
        if (!event.repeat) editor.requestSubmit();
      }
    });
    editor.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (editGuard.hasSaving([q.id])) return;
      saveButton.disabled = true;
      editGuard.setSaving(q.id, true);
      try {
        const data = collect();
        if (optionBox.hidden) data.options = {};
        const result = await api(`/api/questions/${q.id}/text`, { method: "POST", body: { ...data, approve: false } });
        close(false);
        applyQuestion(result);
        toast(q.approved ? "已保存并撤销旧审批；请核对原卷后重新标记通过" : "已保存；请核对原卷后标记通过", "success");
        teach({ type: "text", number: q.number, stem: result.question?.stem || "" });
        setCurrent(q.id, { focus: true });
      } catch (error) {
        toast(error.message, "error");
        saveButton.disabled = false;
        editGuard.setSaving(q.id, false);
      }
    });
    function close(rerender = true) {
      editGuard.release(q.id);
      state.editing.delete(q.id);
      card.classList.remove("editing");
      editor.dispatchEvent(new Event("qb:editor-teardown"));
      cancelAnimationFrame(frame);
      cancelAnimationFrame(positionFrame);
      document.removeEventListener("selectionchange", selectionChanged);
      window.removeEventListener("resize", relayout);
      previewBox.remove();
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
    const originLine = card.querySelector(".card-origin");
    if (originLine) originLine.hidden = true;
    card.querySelector(".reads")?.remove();
    card.querySelector(".card-body").append(editor);
    // The original stays in sight while typing: the left column is sticky.
    card.classList.add("editing");
    let prefilled = null;
    let prefillSelection = null;
    if (prefill) {
      const target = Array.from(previewInputs).find(([, field]) => field === prefill.field)?.[0];
      const replacement = target && QBEdits.reviewedPrefill(target.value, prefill);
      if (replacement) {
        prefilled = target;
        target.value = replacement.value;
        prefillSelection = [replacement.start, replacement.end];
        prefilled.classList.add("prefilled");
        target.title = "框选识读读出来的字，请对照原卷后保存";
        if (target === answer || target === analysis) extra.open = true;
      } else toast("原题文字或替换范围已经变化，未填入识读结果；请重新核对", "error");
    }
    fitStem();
    placePreview();
    update();
    (prefilled || stem).focus({ preventScroll: true });
    if (prefillSelection) prefilled.setSelectionRange(...prefillSelection);
    card.scrollIntoView({ block: "start" });
  }

  // ---------------------------------------------------------------- 框选识读（1.10.2）

  // Start with an AI suggestion; retain a target the person already chose.
  function readTargetGuess(q) {
    if (q.region_read?.target && ["auto", "stem", ...OPTION_KEYS].includes(q.region_read.target)) return q.region_read.target;
    return "auto";
  }

  const regionSubmissions = new Map();
  const regionWaits = new Map();
  const regionLocalFailures = new Map();
  const cancelledRegionTokens = new Set();
  const cancelledRegionIds = new Set();
  function regionWaitKey(questionId, read) { return `${questionId}:${read?.id ?? read?.client_request_id ?? "pending"}`; }

  function hasRegionReadSubmission(questionId) { return regionSubmissions.has(questionId); }

  function cancelServerRegion(questionId, read = {}, clientRequestId = null) {
    const body = {};
    if (read.id != null) body.read_id = read.id;
    if (clientRequestId || read.client_request_id) body.client_request_id = clientRequestId || read.client_request_id;
    return QBRegionWait.boundedRequest((signal) => api(`/api/questions/${questionId}/region-read`, {
      method: "DELETE", body, signal
    }), { timeoutMs: 10000 });
  }

  function cancelRegionReadSubmission(questionId) {
    const submission = regionSubmissions.get(questionId);
    if (!submission) return false;
    cancelledRegionTokens.add(submission.token);
    submission.controller.abort();
    regionSubmissions.delete(questionId);
    cancelServerRegion(questionId, {}, submission.token).catch((error) => {
      toast(`本机已结束等待，后台取消尚未确认：${error.message} 迟到结果不会填入原题。`, "error");
    });
    return true;
  }

  function expireRegionWait(questionId, key) {
    const wait = regionWaits.get(key);
    if (!wait || wait.error) return;
    wait.error = wait.read.status === "queued"
      ? "读题后台没有及时开始，本次等待已结束。请检查软件是否仍在运行，或手动改字；原题没有改变。"
      : "读题服务超过等待时限，本次识读已结束。可稍后重试或手动改字；原题没有改变。";
    clearTimeout(wait.timer);
    regionLocalFailures.set(questionId, { key, read: wait.read, error: wait.error });
    const current = questionById(questionId);
    if (current && regionWaitKey(questionId, current.region_read) === key) {
      state.questions = state.questions.map((question) => question.id === questionId ? normalizeRegionRead(question) : question);
      state.rendered.delete(questionId);
      renderCards();
    }
    cancelServerRegion(questionId, wait.read).catch((error) => {
      toast(`已结束本机等待，后台取消尚未确认：${error.message}`, "error");
    });
  }

  function normalizeRegionRead(question) {
    const read = question.region_read;
    if (!read) {
      const failed = regionLocalFailures.get(question.id);
      return failed ? { ...question, region_read: { ...failed.read, status: "failed", text: "", error: failed.error } } : question;
    }
    const key = regionWaitKey(question.id, read);
    if (cancelledRegionIds.has(key) || cancelledRegionTokens.has(read.client_request_id)) {
      return { ...question, region_read: null };
    }
    if (regionLocalFailures.get(question.id)?.key !== key) regionLocalFailures.delete(question.id);
    let wait = regionWaits.get(key);
    if (!wait) {
      wait = { observedAt: Date.now(), read, timer: null, error: "" };
      regionWaits.set(key, wait);
    }
    if ((read.revision != null && !QBRegionWait.sameVersion(question, read.revision)) || read.recommendation?.status === "stale") {
      wait.error = "题目已修改，这次识读属于旧版本，请重新框选。原题没有改变。";
      regionLocalFailures.set(question.id, { key, read, error: wait.error });
    }
    if (wait.error) {
      clearTimeout(wait.timer);
      return { ...question, region_read: { ...read, status: "failed", text: "", error: wait.error } };
    }
    if (["queued", "running"].includes(read.status)) {
      wait.read = read;
      if (!wait.timer) wait.timer = setTimeout(() => expireRegionWait(question.id, key),
        Math.max(0, QBRegionWait.deadline(read, wait.observedAt) - Date.now()));
    } else {
      clearTimeout(wait.timer); wait.timer = null;
    }
    return question;
  }

  async function queueRegionRead(q, box, target) {
    cancelRegionReadSubmission(q.id);
    const submission = { controller: new AbortController(), token: crypto.randomUUID(),
      paperId: state.paperId, revision: q.content_revision || 0 };
    regionSubmissions.set(q.id, submission);
    try {
      const data = await QBRegionWait.boundedRequest((signal) => api(`/api/questions/${q.id}/region-read`, {
        method: "POST", body: { page_idx: box.page_idx, bbox: box.bbox, target,
          revision: submission.revision, client_request_id: submission.token }, signal
      }), { signal: submission.controller.signal });
      if (regionSubmissions.get(q.id) !== submission || cancelledRegionTokens.has(submission.token)) throw QBRegionWait.abortError();
      if (state.paperId !== submission.paperId || !QBRegionWait.sameVersion(questionById(q.id), submission.revision)) {
        cancelledRegionTokens.add(submission.token);
        cancelServerRegion(q.id, data.question?.region_read || {}, submission.token).catch(() => {});
        throw new Error("题目已经变化，本次识读未采用。请重新打开原卷框选。");
      }
      if (!data.question?.region_read) throw new Error("本次识读没有开始，请重新框选；原题没有改变。");
      data.question = normalizeRegionRead(data.question);
      applyQuestion(data);
      return data;
    } catch (error) {
      if (error.name === "TimeoutError" || error.name === "AbortError") {
        if (!cancelledRegionTokens.has(submission.token)) {
          cancelledRegionTokens.add(submission.token);
          cancelServerRegion(q.id, {}, submission.token).catch((cancelError) => {
            toast(`本机已结束等待，后台取消尚未确认：${cancelError.message}`, "error");
          });
        }
      }
      throw error;
    } finally {
      if (regionSubmissions.get(q.id) === submission) regionSubmissions.delete(q.id);
    }
  }

  function regionReadPending(q) {
    return Boolean(q.region_read && ["queued", "running"].includes(q.region_read.status));
  }

  async function dismissRegionRead(q) {
    const read = q.region_read;
    const submission = regionSubmissions.get(q.id);
    if (submission && (!read || submission.token === read.client_request_id)) cancelRegionReadSubmission(q.id);
    if (read) {
      const key = regionWaitKey(q.id, read);
      cancelledRegionIds.add(key);
      if (read.client_request_id) cancelledRegionTokens.add(read.client_request_id);
      clearTimeout(regionWaits.get(key)?.timer);
    }
    regionLocalFailures.delete(q.id);
    const latest = questionById(q.id) || q;
    if (!latest.region_read || !read || regionWaitKey(q.id, latest.region_read) === regionWaitKey(q.id, read)) {
      applyQuestion({ question: { ...latest, region_read: null } });
    }
    try {
      const data = await cancelServerRegion(q.id, read || {});
      const current = questionById(q.id);
      if (data.question && current && QBRegionWait.sameVersion(current, data.question.content_revision)) applyQuestion(data);
    } catch (error) { toast(`已结束本机等待，后台取消尚未确认：${error.message} 迟到结果不会填入原题。`, "error"); }
  }

  function fillRegionRead(card, q, target = null) {
    const read = q.region_read;
    if (!read?.text) return;
    if (!target && read.target === "auto") {
      const prefill = QBRegionAssist.recommendedPrefill(q, read);
      if (!prefill) { toast("位置建议已经失效，请重新识读或手动选择位置", "error"); return; }
      openEditor(card, q, { prefill });
      return;
    }
    target = target || read.target;
    if (OPTION_KEYS.includes(target)) {
      openEditor(card, q, { prefill: { field: target, value: read.text } });
      return;
    }
    openEditor(card, q, { regionInsert: read.text });
  }

  function regionReadPanel(card, q) {
    const read = q.region_read;
    if (!read) return null;
    const panel = el("section", `region-read ${read.status}`);
    const head = el("div", "region-read-head");
    head.append(el("strong", "", `框选识读 · ${read.target_name || read.target}`));
    panel.append(head);
    if (regionReadPending(q)) {
      panel.append(el("p", "hint", read.status === "queued"
        ? "已提交，等待读题后台开始。原题没有改变。"
        : "AI 正在读框里的字，原题没有改变。"));
      panel.append(el("p", "hint", `本次最多等待 ${Number(read.timeout_seconds) || QBRegionWait.JOB_TIMEOUT_MS / 1000} 秒，超时会结束等待；可随时取消识读。`));
    } else if (read.status === "failed") {
      panel.append(el("p", "region-read-error", read.error || "没读出来"));
    } else {
      const text = el("div", "region-read-text");
      R.renderTypeset(text, read.text);
      panel.append(text);
      if (read.engine) panel.append(el("p", "hint", `${read.engine} 读了框里的这一块；请对照原卷确认后再填入`));
    }
    const actions = el("div", "region-read-actions");
    if (read.status === "done" && read.text) {
      if (read.target === "auto") {
        const r = read.recommendation || {};
        const prefill = QBRegionAssist.recommendedPrefill(q, read);
        const suggestion = el("div", "region-recommendation");
        if (prefill) {
          suggestion.append(el("strong", "", `建议替换：${prefill.field === "stem" ? "题干中的这一段" : `选项 ${prefill.field}`}`),
            el("p", "hint", r.why || "请对照原卷与下面的替换内容，确认位置后填入改字。"));
          const comparison = el("div", "region-location-comparison");
          [["替换前", r.before || "（原选项为空）", "region-location-before"],
            ["替换后", r.after, "region-location-after"]].forEach(([label, value, className]) => {
            const part = el("div", className);
            part.append(el("p", "hint", label));
            const body = el("div");
            R.renderTypeset(body, value);
            part.append(body);
            comparison.append(part);
          });
          suggestion.append(comparison);
          const position = el("details", "region-location-context");
          position.append(el("summary", "", "查看完整替换位置"));
          const location = el("div", "paper");
          R.renderQuestion(location, content(q), { showNumber: false, showAnswer: "none",
            marks: { [prefill.field]: [{ start: prefill.start, end: prefill.end, kind: "region-location", exact: true }] } });
          position.append(location);
          suggestion.append(position);
          actions.append(button("确认位置并填入改字", "small primary", () => fillRegionRead(card, q)));
        } else {
          suggestion.append(el("strong", "", r.status === "stale" ? "题目已改过，这次位置建议已失效" : "没有确定的替换位置"),
            el("p", "hint", r.why || "识读文字已保留。可以重新框选，或手动选择它属于哪一部分。"));
        }
        const manual = el("div", "region-manual-target");
        manual.append(el("strong", "", "这段文字放在哪里？"));
        const destinations = el("div", "region-read-actions");
        destinations.append(button("放到题干", "small", () => fillRegionRead(card, q, "stem")));
        OPTION_KEYS.filter(key => key !== "E" || q.options?.E || q.region_read?.target === "E").forEach(key => {
          destinations.append(button(`填到选项 ${key}`, "small", () => fillRegionRead(card, q, key)));
        });
        manual.append(destinations, el("p", "hint", "选项会替换整个选项；题干可在改字中选择替换的一段。填入后保存即可。"));
        suggestion.append(manual);
        panel.append(suggestion);
      } else {
        const label = read.target === "stem" ? "放到题干" : `填到${read.target_name || read.target}`;
        actions.append(button(label, "small primary", () => fillRegionRead(card, q)));
      }
    }
    if (read.status === "failed") actions.append(button("手动改字", "small", () => openEditor(card, q)));
    actions.append(button("重新框", "small", () => openPageDialog("read", q)),
      button(regionReadPending(q) ? "取消识读" : "关闭", "small quiet", () => dismissRegionRead(q)));
    panel.append(actions);
    return panel;
  }

  // ---------------------------------------------------------------- 原卷页面上拖框（调整范围 / 配图 / 补一题）

  const dialog = {
    mode: null, question: null, boxes: [], page: 0, drag: null, sketch: null,
    tool: "draw",
    history: [], future: [],
    zoom: 1, zoomMode: "width", zoomFrame: 0, spacePan: false, imageReady: false,
    slotTarget: null, slotAnchor: null, pendingFigure: null, ignoredCandidates: new Set(),
    saving: false, closing: false, active: false, reopenIntent: null,
    paperId: null, lastPage: null, session: 0, cutQuestionIds: []
  };
  let pageOpenIntent = 0;
  const CROP_EDIT_KEY = "original-crop";
  const CROP_GUIDANCE_PREF = "qb-crop-guidance";
  let cropGuidanceEnabled = readPref(CROP_GUIDANCE_PREF, "1") !== "0";
  let cropGuidanceText = "";

  function cropSnapshot() {
    return { ...QBManualCrop.cropDraftSnapshot(dialog.boxes, dialog.mode === "new" ? $("numberInput").value : "",
      dialog.mode === "new" ? $("groupSelect").value : "", dialog.ignoredCandidates),
      question_type: dialog.mode === "new" ? $("cropTypeSelect").value : "" };
  }

  function trackCropDraft() {
    dialog.cropBaseline = JSON.stringify(cropSnapshot());
    freezeCropDraftClassification();
    editGuard.release(CROP_EDIT_KEY);
    if (dialog.mode === "view") return;
    editGuard.track(CROP_EDIT_KEY, cropSnapshot, () => {
      editGuard.release(CROP_EDIT_KEY);
      closePageDialog({ preserveIntent: dialog.reopenIntent === pageOpenIntent });
    }, () => $("pageStage").focus({ preventScroll: true }));
  }

  function syncCropDraftClassification() {
    if (!state.paper) return;
    const current = counts();
    renderFilters(current);
    renderMeter(current);
  }

  function clearCropDraftAttention({ deferRender = false } = {}) {
    const id = dialog.question?.id;
    const frozen = state.cropDraftAttention.get(id);
    if (id == null || !frozen || !state.cropDraftAttention.delete(id)) return false;
    const q = questionById(id) || dialog.question;
    const approved = isApproved(q);
    const changed = frozen.dirty || frozen.wasDirty
      || frozen.todo !== needsCheck(q) || frozen.green !== needsGeneralReview(q)
      || frozen.approved !== approved || frozen.ai !== isAiApproved(q)
      || frozen.waiting !== (q.state === "waiting" || q.state === "reading")
      || frozen.red !== (!approved && q.state === "red")
      || frozen.unpublished !== (approved && !(q.publication && q.publication.up_to_date));
    // An untouched editor with unchanged backend classification needs no card
    // invalidation, full-list scan or count recomputation when it closes.
    if (!deferRender || changed) state.rendered.delete(id);
    if (!deferRender) syncCropDraftClassification();
    return !deferRender || Boolean(changed);
  }

  function freezeCropDraftClassification() {
    const id = dialog.question?.id;
    if (id == null || !["figures", "regions"].includes(dialog.mode) || !$("pageDialog").open
      || dialog.paperId !== state.paperId || state.cropDraftAttention.has(id)) return;
    const q = questionById(id) || dialog.question;
    const approved = isApproved(q);
    // Freeze the entering classification for this editor session. Local
    // drafts must neither move a card nor disagree with its filter count.
    state.cropDraftAttention.set(id, {
      dirty: false, wasDirty: false, todo: needsCheck(q), green: needsGeneralReview(q), approved,
      ai: isAiApproved(q), waiting: q.state === "waiting" || q.state === "reading",
      red: !approved && q.state === "red",
      unpublished: approved && !(q.publication && q.publication.up_to_date)
    });
  }

  function updateCropDraftAttention() {
    const id = dialog.question?.id;
    if (id == null || !["figures", "regions"].includes(dialog.mode) || !$("pageDialog").open
      || dialog.paperId !== state.paperId || !dialog.cropBaseline) return;
    const needsAttention = Boolean(dialog.sketch || dialog.pendingFigure
      || JSON.stringify(cropSnapshot()) !== dialog.cropBaseline);
    freezeCropDraftClassification();
    const frozen = state.cropDraftAttention.get(id);
    if (!frozen || frozen.dirty === needsAttention) return;
    frozen.dirty = needsAttention;
    if (needsAttention) frozen.wasDirty = true;
  }

  function releasePageDialog() {
    if (!dialog.active || $("pageDialog").open) return;
    dialog.active = false;
    dialog.session += 1;
    dialog.lastPage = dialog.page;
    cancelFigureSketch();
    const clearedAttention = clearCropDraftAttention({ deferRender: true });
    editGuard.release(CROP_EDIT_KEY);
    if (dialog.zoomFrame) cancelAnimationFrame(dialog.zoomFrame);
    dialog.zoomFrame = 0;
    clearPagePanKey();
    if (dialog.drag) dialog.drag();
    closeFigureSlotMenu({ cancelPending: true, rerender: false });
    dialog.imageReady = false;
    if (dialog.saving) setCropSaving(false);
    // Detach the large page and stitched previews immediately. Hidden
    // dialogs otherwise keep downloading/decoding them after the user exits.
    ["pageStage", "pageCropPreviewBody", "regionPieces"].forEach((id) => {
      const host = $(id);
      host.querySelectorAll("img").forEach((image) => image.removeAttribute("src"));
      host.replaceChildren();
    });
    showCropGuide("");
    if (clearedAttention) {
      const paperId = state.paperId, session = dialog.session;
      // A timer queued directly from the close event can run before paint.
      // Let the closed dialog paint first, then rebuild only if this exact
      // editor session still owns the cleanup. A reopened editor is untouched.
      requestAnimationFrame(() => setTimeout(() => {
        if (state.paperId !== paperId || dialog.session !== session || dialog.active) return;
        syncCropDraftClassification();
        renderCards();
      }, 0));
    }
  }

  function closePageDialog({ preserveIntent = false } = {}) {
    if (!preserveIntent) cancelPendingPageOpening();
    if ($("pageDialog").open) $("pageDialog").close();
    releasePageDialog();
  }

  async function requestPageDialogClose({ reopenIntent = null } = {}) {
    cancelFigureSketch();
    if (dialog.saving) {
      if (dialog.question && hasRegionReadSubmission(dialog.question.id)) {
        cancelRegionReadSubmission(dialog.question.id);
        setCropSaving(false);
        showCropResult("识读已取消，框选仍保留。");
      } else { toast("正在保存，请等保存完成", "error"); return false; }
    }
    if (reopenIntent === null) cancelPendingPageOpening();
    if (dialog.closing) return false;
    // No changes means no asynchronous leave flow or card rebuilding before
    // closing. Opening and immediately returning must respond at once.
    if (!dialog.cropBaseline || (!dialog.pendingFigure && JSON.stringify(cropSnapshot()) === dialog.cropBaseline)) {
      closePageDialog({ preserveIntent: reopenIntent === pageOpenIntent });
      return true;
    }
    dialog.closing = true;
    dialog.reopenIntent = reopenIntent;
    try {
      const leave = await editGuard.discard(() => confirmDialog({ title: "框选还没保存",
        text: "这些范围、片段顺序或配图归属还没保存。你可以继续调整，也可以丢弃本次改动。",
        ok: "丢弃改动", cancel: "继续调整", danger: true, focusCancel: true }), [CROP_EDIT_KEY]);
      if (leave) closePageDialog({ preserveIntent: reopenIntent === pageOpenIntent });
      return leave;
    } finally { dialog.closing = false; dialog.reopenIntent = null; }
  }

  function showCropResult(text, error = false) {
    const node = $("pageCropResult");
    node.textContent = text;
    node.hidden = !text;
    node.classList.toggle("error", error);
  }

  function showCropGuide(text) {
    cropGuidanceText = text;
    $("pageCropGuideText").textContent = text;
    $("pageCropGuide").hidden = !cropGuidanceEnabled || !text;
    $("pageGuidanceToggle").checked = cropGuidanceEnabled;
  }

  function setCropGuidanceEnabled(enabled) {
    cropGuidanceEnabled = Boolean(enabled);
    writePref(CROP_GUIDANCE_PREF, cropGuidanceEnabled ? "1" : "0");
    showCropGuide(cropGuidanceText);
  }
  $("pageGuideDismiss").addEventListener("click", () => setCropGuidanceEnabled(false));
  $("pageGuidanceToggle").addEventListener("change", (event) => setCropGuidanceEnabled(event.target.checked));
  window.addEventListener("storage", (event) => {
    if (event.key !== CROP_GUIDANCE_PREF) return;
    cropGuidanceEnabled = event.newValue !== "0";
    showCropGuide(cropGuidanceText);
  });

  function configureCropActions() {
    $("pageDialogSaveNext").hidden = dialog.mode !== "new";
    $("pageDialogComplete").hidden = dialog.mode !== "new";
    $("pageDialogSave").hidden = ["view", "new"].includes(dialog.mode) || dialog.practiceRead;
    $("pageDialogSave").classList.toggle("primary", dialog.mode !== "new");
    $("pageDialogSave").textContent = dialog.mode === "read" ? "识读这一块" : "保存";
    $("pageDialogSave").title = "保存当前改动（Ctrl+Enter）";
    $("pageDialogSaveNext").replaceChildren(document.createTextNode("保存下一题"), el("span", "kbd-hint", "S"));
    $("pageDialogSaveNext").title = "保存下一题（S，也支持 Enter；仅画布中生效）";
    $("pageDialogComplete").replaceChildren(document.createTextNode("完成切题"), el("span", "kbd-hint", "Ctrl+S"));
    $("pageDialogClose").textContent = dialog.mode === "view" ? "关闭" : dialog.mode === "new" ? "返回" : "取消";
    $("pageDialogComplete").title = state.paper?.demo ? "完成切题（Ctrl+S）；练习只保留原图，不调用 AI"
      : "完成切题（Ctrl+S，也支持 Ctrl+Enter），自动识读已保存的题目；空白下一题不会新建题目";
    $("readTargetField").hidden = dialog.mode !== "read";
    $("numberField").hidden = dialog.mode !== "new";
    $("cropTypeField").hidden = dialog.mode !== "new";
    $("groupField").hidden = dialog.mode !== "new" || (state.paper.question_groups || []).length < 2;
    if (window.QBShortcutHelp) {
      let host = $("pageShortcutReference");
      const old = $("pageDialog").querySelector(".page-shortcuts");
      const toggle = $("pageGuidanceToggle").closest("label");
      if (!host && old) { host = el("div", "page-shortcut-reference"); host.id = "pageShortcutReference"; old.replaceWith(host); }
      if (host) {
        const details = window.QBShortcutHelp.mountHint(host, "crop", { mode: dialog.mode, practiceRead: dialog.practiceRead });
        details?.querySelector(".shortcut-hint-panel").append(toggle);
      }
    }
  }

  $("pageDialog").addEventListener("cancel", (event) => {
    event.preventDefault();
    if (dialog.sketch) { cancelFigureSketch(); return; }
    requestPageDialogClose();
  });

  function openPageDialog(mode, q = null, { page: requestedPage = null } = {}) {
    if (!state.paper?.pages?.length) { toast("原卷页面尚未生成", "error"); return; }
    if (dialog.saving || dialog.closing) { toast("请先完成当前操作", "error"); return; }
    if (manualSwitchRequests.size) cancelPendingPageOpening();
    const intent = ++pageOpenIntent, paperId = state.paperId;
    if ($("pageDialog").open) {
      requestPageDialogClose({ reopenIntent: intent }).then((closed) => {
        if (closed && pageOpenIntent === intent && state.paperId === paperId && !$("pageDialog").open) {
          openPageDialog(mode, q, { page: requestedPage });
        }
      });
      return;
    }
    if (mode === "figures" && q?.body_mode === "source_image") mode = "regions";
    const previousPage = dialog.paperId === state.paperId ? dialog.lastPage : null;
    dialog.session += 1;
    dialog.active = true;
    dialog.paperId = state.paperId;
    dialog.mode = mode;
    dialog.cropFilter = state.filter;
    dialog.cropBaseline = null;
    dialog.cutQuestionIds = [];
    dialog.tool = mode === "view" ? "pan" : "draw";
    dialog.history = [];
    dialog.future = [];
    dialog.zoom = 1;
    dialog.zoomMode = mode === "view" ? "fit" : "width";
    dialog.spacePan = false;
    $("pageStage").classList.toggle("read-only", mode === "view");
    $("pageStage").classList.toggle("pan-ready", dialog.tool === "pan");
    $("pageToolControls").hidden = mode === "view";
    $("pageHistoryControls").hidden = !["new", "regions"].includes(mode);
    $("pageManualCut").hidden = mode !== "view" || !["ready", "failed"].includes(state.paper.status);
    $("pageToolPan").setAttribute("aria-pressed", String(dialog.tool === "pan"));
    $("pageToolDraw").setAttribute("aria-pressed", String(dialog.tool === "draw"));
    $("pageToolDraw").textContent = mode === "figures" ? "两点框选" : "框选";
    dialog.question = q;
    closeFigureSlotMenu({ cancelPending: true, rerender: false });
    if (mode === "regions") dialog.boxes = q.regions.map((r) => ({ page_idx: r.page_idx, bbox: [...r.bbox] }));
    else if (mode === "figures") dialog.boxes = q.figures.flatMap((f) => {
      const exactCandidate = (q.figure_candidates || []).find(
        (candidate) => figureCandidateKey(candidate) === figureCandidateKey(f)
      );
      const first = {
        page_idx: f.page_idx, bbox: [...f.bbox], slot: f.slot,
        ...(f.label_offset ? { label_offset: { ...f.label_offset } } : {}),
        ...(hasFigureCandidateKey(q, f.candidate_key)
          ? { candidate_key: f.candidate_key }
          : exactCandidate ? { candidate_key: figureCandidateKey(exactCandidate) } : {})
      };
      // A stitched figure opens as its pieces; the lower ones stay joined.
      const joined = (Array.isArray(f.parts) ? f.parts : []).map((part) => ({
        page_idx: part.page_idx, bbox: [...part.bbox], slot: f.slot, join: true,
        ...(hasFigureCandidateKey(q, part.candidate_key) ? { candidate_key: part.candidate_key } : {})
      }));
      return [first, ...joined];
    });
    else if (mode === "read") dialog.boxes = q.region_read && Array.isArray(q.region_read.bbox)
      ? [{ page_idx: q.region_read.page_idx, bbox: [...q.region_read.bbox] }] : [];
    else dialog.boxes = [];
    const firstPage = mode === "read" && dialog.boxes.length ? dialog.boxes[0].page_idx
      : q && q.regions.length ? q.regions[0].page_idx : state.paper.pages[0].page_idx;
    dialog.page = Number.isInteger(requestedPage)
      && state.paper.pages.some((page) => page.page_idx === requestedPage) ? requestedPage
      : mode === "new" && Number.isInteger(previousPage) && state.paper.pages.some((page) => page.page_idx === previousPage) ? previousPage : firstPage;
    dialog.scrolled = false;
    dialog.selected = null;
    $("pageStage").scrollTo({ top: 0, left: 0 });
    dialog.ignoredCandidates = new Set(Array.isArray(q?.figure_review?.ignored_candidates)
      ? q.figure_review.ignored_candidates.filter((key) => hasFigureCandidateKey(q, key)) : []);
    $("pageDialogTitle").textContent = mode === "regions" ? `第 ${q.number} 题 · 调整题目范围`
      : mode === "figures" ? `第 ${q.number} 题的配图` : mode === "read" ? `第 ${q.number} 题 · 框选识读`
        : mode === "view" ? "查看整份原卷" : "手工切题";
    $("pageDialogHint").textContent = mode === "regions"
      ? "拖边角改大小、拖框内部移动；可跨页添加片段、调整顺序。这里只调整题目范围，保存后关闭窗口。"
      : mode === "figures"
        ? "点蓝色候选图，或单击一个角、移动鼠标、再单击另一个角固定新框；无需按住鼠标。双击下一张图的左上角可复制附近框的尺寸，适合四个同样大小的选项图。再选归属（S 题干 · A–E 选项 · X 无关 · J 接在上一张图下面，用于被分页切开的表格或图）。Esc 取消未完成的新框；已有框仍可拖动和缩放。点标签改归属，拖标签只挪标签；保存后需重新审核。"
        : mode === "read"
          ? "框住要单独识读的印刷字。选择“自动推荐（AI）”时，AI 会结合现有题面建议替换位置；也可以指定题干或选项。读完先看替换前后，再确认填入改字。"
          : mode === "view" ? "查看完整原卷；这里不会修改题卡或重新识读。"
            : "框出完整题目，包括选项和配图；跨栏或跨页可添加多段。点两次固定范围，按 S 保存下一题，Ctrl+S 完成切题；Enter / Ctrl+Enter 也可使用。完成后自动识读已保存的题目。空白下一题可以直接完成。";
    // The offline practice can demonstrate drawing and the real target picker,
    // but must not dispatch recognition or invent an AI recommendation.
    if (state.paper.demo && mode === "new") $("pageDialogHint").textContent = "离线练习：点两角框出完整题目，S 保存下一题，Ctrl+S 完成切题。保存的原图可直接核对；本次不调用 AI。";
    dialog.practiceRead = mode === "read" && Boolean(state.paper.demo);
    $("pageDialogSave").disabled = dialog.practiceRead;
    $("pageDialogSave").title = dialog.practiceRead ? "示例只练画框；真实识读需要可用的读题服务" : "";
    if (dialog.practiceRead) $("pageDialogHint").textContent = "离线示例：可练画框和选择自动推荐，不会调用 AI 或生成识读结果。真实题目识读后，先确认替换前后，再填入改字、核对保存。";
    $("pageDialogClose").textContent = mode === "view" ? "关闭" : "取消";
    updatePageCanvasHint();
    $("numberField").hidden = mode !== "new";
    $("readTargetField").hidden = mode !== "read";
    configureCropActions();
    if (mode === "read") $("readTargetSelect").value = readTargetGuess(q);
    const groups = state.paper.question_groups || [];
    $("groupField").hidden = mode !== "new" || groups.length < 2;
    if (mode === "new") {
      $("cropTypeSelect").replaceChildren(...Object.entries(TYPE_NAMES).map(([value, label]) => {
        const option = el("option", "", label); option.value = value; return option;
      }));
      $("cropTypeSelect").value = "unknown";
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
    $("numberInput").value = mode === "new" ? (QBManualCrop.nextCropNumber(state.questions) ?? "") : "";
    $("pageCropHelp").open = false;
    dialog.previewWide = window.innerWidth >= 900;
    $("pageCropPreviewDetails").open = dialog.previewWide;
    showCropResult("");
    showCropGuide("");
    $("allPagesPicker").open = false;
    $("pageSearchInput").value = "";
    lens.classList.remove("on");
    renderPageTabs();
    renderStage();
    $("pageDialog").showModal();
    trackCropDraft();
    requestPageZoom();
    if (mode !== "new") $("pageStage").focus({ preventScroll: true });
  }

  $("groupSelect").addEventListener("change", () => {
    if (dialog.mode !== "new" || dialog.saving) return;
    $("numberInput").value = QBManualCrop.nextCropNumber(state.questions, 1, $("groupSelect").value || null) ?? "";
  });

  function applyPageZoom() {
    const stage = $("pageStage");
    const surface = stage.querySelector(".stage-surface");
    if (!surface || !$("pageDialog").open) return;
    const style = getComputedStyle(stage);
    const width = Math.min(1340, stage.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight));
    const height = stage.clientHeight - parseFloat(style.paddingTop) - parseFloat(style.paddingBottom);
    if (!(width > 0) || !(height > 0)) return;
    const page = pageInfo(dialog.page);
    if (dialog.zoomMode === "fit") {
      dialog.zoom = Math.max(CANVAS_ZOOM_MIN, Math.min(1, height / (width * page.height / page.width)));
    } else if (dialog.zoomMode === "width") dialog.zoom = 1;
    surface.style.maxWidth = "none";
    surface.style.width = `${width * dialog.zoom}px`;
    $("pageZoomLevel").textContent = `${Math.round(dialog.zoom * 100)}%`;
    $("pageZoomFit").setAttribute("aria-pressed", String(dialog.zoomMode === "fit"));
    $("pageZoomWidth").setAttribute("aria-pressed", String(dialog.zoomMode === "width"));
    $("pageZoomFit").disabled = $("pageZoomWidth").disabled = !dialog.imageReady;
    $("pageZoomOut").disabled = !dialog.imageReady || dialog.zoom <= CANVAS_ZOOM_MIN;
    $("pageZoomIn").disabled = !dialog.imageReady || dialog.zoom >= CANVAS_ZOOM_MAX;
  }

  function requestPageZoom(mode = null) {
    if (mode && dialog.drag) return false;
    if (dialog.zoomFrame) cancelAnimationFrame(dialog.zoomFrame);
    const session = dialog.session;
    dialog.zoomFrame = requestAnimationFrame(() => {
      if (dialog.session !== session || !$("pageDialog").open) return;
      dialog.zoomFrame = requestAnimationFrame(() => {
        if (dialog.session !== session || !$("pageDialog").open) return;
        dialog.zoomFrame = 0;
        if (mode && dialog.drag) return;
        if (mode) dialog.zoomMode = mode;
        applyPageZoom();
        if (mode) $("pageStage").scrollTo({ top: 0, left: 0 });
      });
    });
    return true;
  }

  function zoomPageBy(factor, pointer = null) {
    if (dialog.saving || dialog.drag || !dialog.imageReady) return;
    if (dialog.zoomFrame) cancelAnimationFrame(dialog.zoomFrame);
    dialog.zoomFrame = 0;
    dialog.zoomMode = "manual";
    dialog.zoom = Math.max(CANVAS_ZOOM_MIN, Math.min(CANVAS_ZOOM_MAX, Math.round(dialog.zoom * factor * 1000) / 1000));
    anchorCanvasZoom($("pageStage"), $("pageStage").querySelector(".stage-surface"), applyPageZoom, pointer);
  }

  $("viewOriginalPaper").addEventListener("click", () => closeSettingsThen(() => openPageDialog("view")));
  $("pageZoomFit").addEventListener("click", () => { if (requestPageZoom("fit")) $("pageStage").focus({ preventScroll: true }); });
  $("pageZoomWidth").addEventListener("click", () => { if (requestPageZoom("width")) $("pageStage").focus({ preventScroll: true }); });
  $("pageZoomIn").addEventListener("click", () => { zoomPageBy(1.25); $("pageStage").focus({ preventScroll: true }); });
  $("pageZoomOut").addEventListener("click", () => { zoomPageBy(0.8); $("pageStage").focus({ preventScroll: true }); });
  $("pageStage").addEventListener("wheel", (event) => {
    if (!event.ctrlKey) return;
    event.preventDefault();
    if (event.deltaY) zoomPageBy(event.deltaY < 0 ? 1.1 : 0.9, event);
  }, { passive: false });
  $("pageImageRetry").addEventListener("click", () => {
    stopPageCanvasPan();
    if (dialog.drag) dialog.drag();
    renderStage();
  });
  const stopPageCanvasPan = bindCanvasPan($("pageStage"), (event) => !dialog.drag && dialog.imageReady
    && (event.button === 1 || (event.button === 0 && (dialog.mode === "view" || dialog.tool === "pan" || dialog.spacePan))), $("pageDialog"));
  const clearPagePanKey = () => {
    dialog.spacePan = false;
    $("pageStage").classList.toggle("pan-ready", dialog.tool === "pan");
  };
  function updatePageCanvasHint() {
    $("pageCanvasHint").textContent = dialog.mode === "view"
      ? "Ctrl+滚轮缩放 · 按住鼠标左键拖动画布"
      : dialog.tool === "pan" ? "左键拖动画布 · Ctrl+滚轮缩放"
        : dialog.mode === "figures"
          ? "单击两角画框 · 双击下一图左上角复制框尺寸 · Esc 取消新框 · Ctrl+滚轮缩放"
          : dialog.mode === "new"
            ? "单击两角画框 · S 保存下一题 · Ctrl+S 完成切题 · 空格+拖动平移"
            : dialog.mode === "regions" ? "单击两角画框 · Ctrl+Enter 保存 · 空格+拖动平移"
            : "左键画框 · 空格+左键或中键拖画布 · Ctrl+滚轮缩放";
  }
  function selectPageTool(tool) {
    if (dialog.saving) return;
    cancelFigureSketch();
    if (dialog.drag) dialog.drag();
    stopPageCanvasPan();
    dialog.tool = tool;
    clearPagePanKey();
    $("pageToolPan").setAttribute("aria-pressed", String(tool === "pan"));
    $("pageToolDraw").setAttribute("aria-pressed", String(tool === "draw"));
    updatePageCanvasHint();
    $("pageStage").focus({ preventScroll: true });
  }
  $("pageToolPan").addEventListener("click", () => selectPageTool("pan"));
  $("pageToolDraw").addEventListener("click", () => selectPageTool("draw"));
  $("pageManualCut").addEventListener("click", () => {
    if (manualSwitches.has(state.paperId) || aiCutContinuations.has(state.paperId)) return;
    const page = dialog.page;
    if (state.paper?.status === "ready") {
      openPageDialog("new", null, { page });
    } else switchToManual(page);
  });
  const copyDialogBoxes = () => dialog.boxes.map((box) => ({ ...box, bbox: [...box.bbox] }));
  function rememberDialogBoxes(before) {
    if (!["new", "regions"].includes(dialog.mode)) return;
    dialog.history.push(before || copyDialogBoxes());
    if (dialog.history.length > 50) dialog.history.shift();
    dialog.future = [];
  }
  function restoreDialogBoxes(redo) {
    if (dialog.saving) return;
    if (dialog.sketch) { cancelFigureSketch(); return; }
    if (dialog.drag) dialog.drag();
    const from = redo ? dialog.future : dialog.history;
    const to = redo ? dialog.history : dialog.future;
    if (!from.length) return;
    to.push(copyDialogBoxes());
    dialog.boxes = from.pop();
    dialog.selected = null;
    renderPageTabs();
    renderStage();
  }
  $("pageUndo").addEventListener("click", () => restoreDialogBoxes(false));
  $("pageRedo").addEventListener("click", () => restoreDialogBoxes(true));
  document.addEventListener("keyup", (event) => { if (event.key === " ") clearPagePanKey(); });
  window.addEventListener("blur", clearPagePanKey);

  function removeBox(index) {
    if (dialog.saving) return;
    if (index === null || index === undefined || !dialog.boxes[index]) return;
    closeFigureSlotMenu({ cancelPending: false, rerender: false });
    rememberDialogBoxes();
    dialog.boxes.splice(index, 1);
    dialog.selected = null;
    renderPageTabs();
    renderStage();
  }

  function renderRegionPieces() {
    const panel = $("regionPieces");
    panel.hidden = !["new", "regions"].includes(dialog.mode);
    panel.replaceChildren();
    $("pageUndo").disabled = !dialog.history.length;
    $("pageRedo").disabled = !dialog.future.length;
    const preview = $("pageCropPreview");
    preview.hidden = panel.hidden;
    $("pageCropPreviewBody").replaceChildren();
    if (panel.hidden) return;
    panel.append(el("strong", "", `题目片段 · ${dialog.boxes.length} 段`));
    if (!dialog.boxes.length) {
      panel.append(el("span", "hint", "点两次框出题目；同一道题可跨页补充。"));
      $("pageCropPreviewBody").append(el("p", "hint", "画框后在这里看完整题目。图片也包含在题目范围内。"));
      return;
    }
    $("pageCropPreviewBody").append(cropView(dialog.boxes));
    $("pageCropPreviewBody").querySelectorAll("img").forEach((image) => { image.loading = "eager"; });
    const list = el("ol", "region-piece-list");
    dialog.boxes.forEach((box, index) => {
      const row = el("li", dialog.selected === index ? "selected" : "");
      const locate = button(`${index + 1} · 第 ${box.page_idx + 1} 页`, "small quiet", () => {
        dialog.selected = index;
        goToDialogPage(box.page_idx);
        requestAnimationFrame(() => {
          const frame = $("pageStage").querySelector(`[data-box-index="${index}"]`);
          frame?.scrollIntoView({ block: "center" });
          frame?.focus({ preventScroll: true });
        });
      }, `定位第 ${index + 1} 段`);
      const thumbnail = cropView([box]);
      thumbnail.classList.add("region-piece-thumbnail");
      locate.prepend(thumbnail);
      locate.classList.add("region-piece-locate");
      const move = (offset) => {
        if (dialog.drag) dialog.drag();
        const to = index + offset;
        if (to < 0 || to >= dialog.boxes.length) return;
        rememberDialogBoxes();
        [dialog.boxes[index], dialog.boxes[to]] = [dialog.boxes[to], dialog.boxes[index]];
        dialog.selected = to;
        renderPageTabs();
        renderStage();
      };
      const up = button("前移", "small quiet", () => move(-1), `第 ${index + 1} 段前移`);
      const down = button("后移", "small quiet", () => move(1), `第 ${index + 1} 段后移`);
      up.disabled = index === 0;
      down.disabled = index === dialog.boxes.length - 1;
      row.append(locate, up, down, button("移除", "small quiet", () => removeBox(index), `移除第 ${index + 1} 段`));
      list.append(row);
    });
    panel.append(list);
  }


  function dialogPageIndex() {
    return (state.paper?.pages || []).findIndex((page) => page.page_idx === dialog.page);
  }

  function goToDialogPage(pageIdx, { focusTab = false, closePicker = false } = {}) {
    if (dialog.saving) return;
    if (!(state.paper?.pages || []).some((page) => page.page_idx === pageIdx)) return;
    closeFigureSlotMenu({ cancelPending: true, rerender: false });
    stopPageCanvasPan();
    if (dialog.drag) dialog.drag();
    dialog.page = pageIdx;
    dialog.lastPage = pageIdx;
    dialog.scrolled = false;
    $("pageStage").scrollTo({ top: 0, left: 0 });
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
    // Enter already changed the page. The input's later blur/change must not
    // rebuild the canvas in the middle of the user's first pointer gesture.
    if (exact && exact.page_idx !== dialog.page) goToDialogPage(exact.page_idx);
    else if (exact) $("pageNumberInput").value = String(dialog.page + 1);
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

  function closeFigureSlotMenu({ cancelPending = true, rerender = false, restoreFocus = true } = {}) {
    showCropGuide("");
    const menu = $("figureSlotMenu");
    const hadPending = Boolean(dialog.pendingFigure);
    const menuHadFocus = menu.contains(document.activeElement);
    if (cancelPending) dialog.pendingFigure = null;
    dialog.slotTarget = null;
    dialog.slotAnchor = null;
    try { if (menu.matches(":popover-open")) menu.hidePopover(); } catch { /* old Edge fallback */ }
    menu.hidden = true;
    updateCropDraftAttention();
    menu.style.left = "";
    menu.style.top = "";
    if (rerender && hadPending && $("pageDialog").open) renderStage();
    // Hiding the popover takes the focus away with it, and the browser then
    // leaves it on <body> -- outside the modal dialog. The cutting shortcuts
    // only listen inside #pageDialog, so a stranded focus makes Ctrl+Enter
    // look broken until the user clicks some blank space first.
    if (restoreFocus && menuHadFocus && $("pageDialog").open) focusCropDialog();
  }

  // Keep the focus on a stable node inside the cutting dialog. The selected box
  // is preferred because it is what the user just acted on and it already owns
  // the arrow-key nudging; the canvas is the fallback when nothing is selected.
  function focusCropDialog({ preferSelection = false } = {}) {
    const stage = $("pageStage");
    if (!stage || !$("pageDialog").open) return;
    if (preferSelection && dialog.selected !== null && dialog.selected !== undefined) {
      const box = stage.querySelector(`[data-box-index="${dialog.selected}"]`);
      if (box) { box.focus({ preventScroll: true }); return; }
    }
    stage.focus({ preventScroll: true });
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
    const existing = target.kind === "existing" ? dialog.boxes[target.index] : null;
    const current = existing ? (existing.join && joinHost(existing) ? "join" : existing.slot) : null;
    menu.querySelectorAll("[data-figure-slot]").forEach((item) => {
      item.setAttribute("aria-checked", String(item.dataset.figureSlot === current));
    });
    menu.hidden = false;
    updateCropDraftAttention();
    try { if (typeof menu.showPopover === "function") menu.showPopover(); } catch { /* old Edge fallback */ }
    positionFigureSlotMenu(anchor);
    requestAnimationFrame(() => menu.querySelector(`[data-figure-slot="${current || "stem"}"]`)?.focus({ preventScroll: true }));
  }

  function joinHost(box, boxes = dialog.boxes) { return QBFigureJoin.joinHost(box, boxes); }
  const figuresFromBoxes = QBFigureJoin.figuresFromBoxes;

  function chooseFigureSlot(slot) {
    const target = dialog.slotTarget;
    if (!target) return;
    if (slot === "join") {
      const probe = target.kind === "existing" ? dialog.boxes[target.index] : { ...target.box };
      const host = probe && joinHost(probe, target.kind === "existing" ? dialog.boxes : [...dialog.boxes, probe]);
      if (!host) {
        toast("前面还没有配图可以接：先把上半截设为题干或选项的配图，再把下半截“接在上一张图下面”", "error");
        return;
      }
    }
    if (target.kind === "existing") {
      const box = dialog.boxes[target.index];
      closeFigureSlotMenu({ cancelPending: false, rerender: false, restoreFocus: false });
      if (!box) return;
      if (slot === "join") {
        box.join = true;
        box.slot = joinHost(box).slot;
        dialog.selected = target.index;
        toast("保存后这两块会拼成一张图");
      } else if (slot === "irrelevant") {
        const candidateKey = box.candidate_key
          || (isKnownFigureCandidate(box) ? figureCandidateKey(box) : null);
        if (candidateKey) dialog.ignoredCandidates.add(candidateKey);
        dialog.boxes.splice(target.index, 1);
        dialog.selected = null;
        renderPageTabs();
        toast("这张图已标记为无关，保存后不会再次作为候选图出现");
      } else {
        box.slot = slot;
        delete box.join;
        dialog.selected = target.index;
      }
    } else {
      const pending = target.box;
      closeFigureSlotMenu({ cancelPending: false, rerender: false, restoreFocus: false });
      dialog.pendingFigure = null;
      if (slot === "irrelevant") {
        if (target.candidateKey) dialog.ignoredCandidates.add(target.candidateKey);
        toast("这张候选图已标记为无关，本次不会加入题卡");
      } else if (slot === "join") {
        const box = { ...pending, bbox: [...pending.bbox], join: true,
          ...(target.candidateKey ? { candidate_key: target.candidateKey } : {}) };
        box.slot = joinHost(box, [...dialog.boxes, box]).slot;
        dialog.boxes.push(box);
        dialog.selected = dialog.boxes.length - 1;
        renderPageTabs();
        toast("保存后这两块会拼成一张图");
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
    // renderStage rebuilds every box, so the focus has to be re-anchored after
    // it; otherwise the shortcut that should save this very change is lost.
    focusCropDialog({ preferSelection: true });
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
    const savedOffset = box.label_offset ? { ...box.label_offset } : null;
    let moved = false;
    const move = (moveEvent) => {
      const dx = moveEvent.clientX - start.x;
      const dy = moveEvent.clientY - start.y;
      if (!moved && Math.hypot(dx, dy) < 4) return;
      moved = true;
      box.label_offset = { x: Math.round(origin.x + dx), y: Math.round(origin.y + dy) };
      updateCropDraftAttention();
      tab.dataset.manualLabel = "true";
      tab.style.transform = `translate(${box.label_offset.x}px, ${box.label_offset.y}px)`;
    };
    const up = () => {
      finish();
      if (moved) {
        box.suppressLabelClick = true;
        autoPlaceFigureLabels(surface);
      }
    };
    const cancel = () => {
      finish();
      if (savedOffset) box.label_offset = savedOffset;
      else delete box.label_offset;
      updateCropDraftAttention();
      if (surface.isConnected) autoPlaceFigureLabels(surface);
    };
    const finish = () => {
      dialog.drag = null;
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("pointercancel", cancel);
      window.removeEventListener("blur", cancel);
    };
    dialog.drag = cancel;
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", cancel);
    window.addEventListener("blur", cancel);
  }

  $("figureSlotMenu").querySelectorAll("[data-figure-slot]").forEach((item) => {
    item.addEventListener("click", () => chooseFigureSlot(item.dataset.figureSlot));
  });
  $("figureSlotMenu").querySelector("[data-figure-slot-cancel]").addEventListener("click", () => {
    closeFigureSlotMenu({ cancelPending: true, rerender: true });
  });
  const SLOT_KEYS = { s: "stem", a: "A", b: "B", c: "C", d: "D", e: "E", x: "irrelevant", j: "join" };
  $("figureSlotMenu").addEventListener("keydown", (event) => {
    if (dialog.saving || event.isComposing || event.keyCode === 229) return;
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      closeFigureSlotMenu({ cancelPending: true, rerender: true });
      return;
    }
    const slot = !event.ctrlKey && !event.metaKey && !event.altKey && SLOT_KEYS[event.key.toLowerCase()];
    if (!slot) return;
    event.preventDefault();
    event.stopPropagation();
    chooseFigureSlot(slot);
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
    if ($("pageDialog").open) {
      const wide = window.innerWidth >= 900;
      if (wide !== dialog.previewWide) { $("pageCropPreviewDetails").open = wide; dialog.previewWide = wide; }
      anchorCanvasZoom($("pageStage"), surface, applyPageZoom);
    }
    if ($("pageDialog").open && dialog.mode === "figures" && surface) autoPlaceFigureLabels(surface);
    else if (menuIsOpen() && dialog.slotAnchor?.isConnected) positionFigureSlotMenu(dialog.slotAnchor);
  });
  $("pageStage").addEventListener("scroll", () => {
    if (menuIsOpen() && dialog.slotAnchor?.isConnected) positionFigureSlotMenu(dialog.slotAnchor);
  }, { passive: true });
  $("pageDialog").addEventListener("close", () => {
    // Native close events can arrive after an intentional close/reopen.
    // Never tear down a new editor in response to the previous event.
    if ($("pageDialog").open || !dialog.active) return;
    cancelPendingPageOpening();
    releasePageDialog();
  });

  function renderStage() {
    cancelFigureSketch();
    updateCropDraftAttention();
    renderRegionPieces();
    const stage = $("pageStage");
    const scroll = { left: stage.scrollLeft, top: stage.scrollTop };
    stage.replaceChildren();
    dialog.imageReady = false;
    ["pageZoomFit", "pageZoomWidth", "pageZoomOut", "pageZoomIn"].forEach((id) => { $(id).disabled = true; });
    $("pageImageState").hidden = false;
    $("pageImageState").classList.remove("page-image-error");
    $("pageImageState").textContent = "正在加载原卷…";
    $("pageImageRetry").hidden = true;
    const page = pageInfo(dialog.page);
    const surface = el("div", "stage-surface");
    const session = dialog.session, paperId = dialog.paperId;
    const currentImage = () => surface.isConnected && $("pageDialog").open
      && dialog.session === session && dialog.paperId === paperId && state.paperId === paperId;
    surface.style.aspectRatio = `${page.width} / ${page.height}`;
    const image = el("img");
    image.src = previewUrl(state.paperId, dialog.page);
    image.alt = `原卷第 ${dialog.page + 1} 页`;
    image.draggable = false;
    image.addEventListener("load", () => {
      if (!currentImage()) return;
      dialog.imageReady = true;
      $("pageImageState").hidden = true;
      applyPageZoom();
    }, { once: true });
    image.addEventListener("error", () => {
      if (!currentImage()) return;
      dialog.imageReady = false;
      $("pageImageState").hidden = false;
      $("pageImageState").textContent = "原卷图片没能加载，请重试。";
      $("pageImageState").classList.add("page-image-error");
      $("pageImageRetry").hidden = false;
      applyPageZoom();
    }, { once: true });
    surface.append(image);
    const q = dialog.question;

    // 参照：其他题的范围（淡灰），配图模式下还有本题范围和候选图。
    state.questions.forEach((other) => {
      const own = q && other.id === q.id;
      if (dialog.mode === "regions" && own) return;
      if ((dialog.mode === "figures" || dialog.mode === "read") && !own) return;
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
          if (event.detail === 2) return; // The following dblclick copies a same-size frame.
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
      const joined = dialog.mode === "figures" && box.join && joinHost(box);
      if (joined) node.classList.add("joined");
      node.setAttribute("aria-label", `${dialog.mode === "figures" ? (joined ? "接在上一张图下面的一块" : (SLOT_NAMES[box.slot] || box.slot) + "配图")
        : dialog.mode === "read" ? "要识读的这一块" : `第 ${index + 1} 段范围`}；方向键移动，Delete 删除`);
      const shortLabel = joined ? "接" : box.slot === "stem" ? "题" : box.slot;
      const fullLabel = joined ? `接在上一张图下面（${SLOT_NAMES[joined.slot] || joined.slot}）` : SLOT_NAMES[box.slot] || box.slot;
      const label = el("button", "box-label", dialog.mode === "figures"
        ? (dialog.selected === index ? fullLabel : shortLabel) : dialog.mode === "read" ? "识读这一块" : `第 ${index + 1} 段`);
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
        if (dialog.saving || dialog.closing || event.defaultPrevented || event.isComposing || event.keyCode === 229
          || event.ctrlKey || event.metaKey || event.altKey || menuIsOpen()
          || document.querySelector('dialog[open]:not(#pageDialog)')
          || QBUpload.isEditingTarget(event.target) || event.target.closest?.("button, a, summary")) return;
        if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) return;
        event.preventDefault();
        event.stopPropagation();
        const step = event.shiftKey ? 20 : 5;
        const dx = event.key === "ArrowLeft" ? -step : event.key === "ArrowRight" ? step : 0;
        const dy = event.key === "ArrowUp" ? -step : event.key === "ArrowDown" ? step : 0;
        rememberDialogBoxes();
        box.bbox = QBManualCrop.moveCropBox(box.bbox, dx, dy);
        updateCropDraftAttention();
        placeBox(node, box.bbox);
        renderRegionPieces();
      });
      surface.append(node);
    });
    if (dialog.mode === "figures") requestAnimationFrame(() => {
      if (surface.isConnected) autoPlaceFigureLabels(surface);
    });
    // While a new figure is being outlined, the second click belongs to it,
    // including manual question ranges and clicks over existing controls.
    let suppressSketchClick = false;
    surface.addEventListener("pointerdown", (event) => {
      if (!dialog.sketch) return;
      if (event.button !== 0 || dialog.spacePan || dialog.tool !== "draw") {
        cancelFigureSketch();
        return;
      }
      event.preventDefault();
      event.stopPropagation();
      suppressSketchClick = true;
      dialog.sketch.complete(event);
    }, { capture: true });
    surface.addEventListener("click", (event) => {
      if (!suppressSketchClick) return;
      suppressSketchClick = false;
      event.preventDefault();
      event.stopImmediatePropagation();
    }, { capture: true });
    surface.addEventListener("dblclick", (event) => {
      if (dialog.mode !== "figures" || event.target.closest?.(".grip, .box-label, .box-remove")) return;
      event.preventDefault();
      event.stopPropagation();
      duplicateFigureAt(event, surface);
    }, { capture: true });
    surface.addEventListener("pointerdown", (event) => {
      if (event.target !== surface && event.target !== image && !event.target.classList.contains("ghost")) return;
      if (menuIsOpen()) {
        closeFigureSlotMenu({ cancelPending: true, rerender: true });
        return;
      }
      if (["figures", "new", "regions"].includes(dialog.mode)) startFigureSketch(event, surface);
      else startDrag(event, surface, null, "create");
    });
    stage.append(surface);
    applyPageZoom();
    stage.scrollTo(scroll);
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

  function cancelFigureSketch() {
    if (dialog.sketch) dialog.sketch.cancel();
  }

  function duplicateFigureAt(event, surface) {
    if (dialog.saving || event.button !== 0 || dialog.mode !== "figures" || dialog.tool !== "draw"
      || dialog.spacePan || !dialog.imageReady) return;
    const corner = pointFrom(event, surface);
    const local = dialog.boxes.filter((box) => box.page_idx === dialog.page);
    const nearest = local.reduce((best, box) => {
      const distance = (box.bbox[0] + box.bbox[2] - 2 * corner[0]) ** 2
        + (box.bbox[1] + box.bbox[3] - 2 * corner[1]) ** 2;
      return !best || distance < best.distance ? { box, distance } : best;
    }, null)?.box || dialog.boxes[dialog.selected] || dialog.boxes[dialog.boxes.length - 1];
    cancelFigureSketch();
    // A double click also emits two ordinary clicks. Discard only their
    // temporary assignment, never an existing figure or its chosen slot.
    const pendingPreview = dialog.pendingFigure && dialog.slotAnchor;
    closeFigureSlotMenu({ cancelPending: true, rerender: false });
    if (pendingPreview?.classList.contains("pending-assignment")) pendingPreview.remove();
    if (!nearest) {
      showCropResult("请先框一张配图并选择归属，再双击下一张图的左上角复制同样大小的框。", true);
      return;
    }
    const width = Math.round((nearest.bbox[2] - nearest.bbox[0]) * 10) / 10;
    const height = Math.round((nearest.bbox[3] - nearest.bbox[1]) * 10) / 10;
    if (![width, height].every((size) => Number.isFinite(size) && size > 0 && size <= 1000)) {
      showCropResult("这个框的尺寸不能完整放进当前页，请先调整原框，再复制。", true);
      return;
    }
    const x = Math.round(corner[0] * 10) / 10;
    const y = Math.round(corner[1] * 10) / 10;
    if (x + width > 1000 || y + height > 1000) {
      showCropResult("同样大小的框在这里放不下，请把左上角移离右边或下边，再双击。", true);
      return;
    }
    const bbox = [x, y, x + width, y + height].map((value) => Math.round(value * 10) / 10);
    const preview = el("div", "edit-box figure pending-assignment");
    placeBox(preview, bbox);
    surface.append(preview);
    openFigureSlotMenu(preview, { kind: "new", box: { page_idx: dialog.page, bbox } });
    showCropGuide("已复制附近框的尺寸，请选择这张图属于题干或哪个选项。");
  }

  function startFigureSketch(event, surface) {
    if (dialog.saving || dialog.sketch || dialog.drag || event.button !== 0 || !["figures", "new", "regions"].includes(dialog.mode)
      || dialog.tool !== "draw" || dialog.spacePan || !dialog.imageReady) return;
    if (["new", "regions"].includes(dialog.mode) && dialog.boxes.length >= 12) {
      toast("一道题最多保留 12 段范围，请合并相邻片段后再添加", "error");
      return;
    }
    event.preventDefault();
    $("pageStage").focus({ preventScroll: true });
    const start = pointFrom(event, surface);
    const page = dialog.page;
    const session = dialog.session;
    const mode = dialog.mode;
    const preview = el("div", `edit-box ${mode === "figures" ? "figure" : "region"} drawing`);
    preview.setAttribute("aria-hidden", "true");
    // Show a small outline immediately; it becomes the actual rectangle as
    // the released mouse moves. Only a valid second click creates a figure.
    placeBox(preview, [Math.max(0, start[0] - 6), Math.max(0, start[1] - 6),
      Math.min(1000, start[0] + 6), Math.min(1000, start[1] + 6)]);
    surface.append(preview);
    showCropResult("");
    showCropGuide(`移动鼠标到另一个角，再点一下固定${mode === "figures" ? "配图" : "题目"}范围；Esc 取消新框。`);
    const bboxAt = (next) => {
      const [x, y] = pointFrom(next, surface);
      return [Math.min(x, start[0]), Math.min(y, start[1]), Math.max(x, start[0]), Math.max(y, start[1])]
        .map((value) => Math.round(value * 10) / 10);
    };
    const stillCurrent = () => surface.isConnected && $("pageDialog").open
      && dialog.mode === mode && dialog.page === page && dialog.session === session;
    const move = (next) => {
      if (!stillCurrent()) { cancel(); return; }
      if (next.pointerId !== event.pointerId) return;
      placeBox(preview, bboxAt(next));
    };
    const finish = () => {
      if (dialog.sketch === sketch) dialog.sketch = null;
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointercancel", cancel);
      window.removeEventListener("blur", cancel);
      showCropGuide("");
    };
    const cancel = () => {
      finish();
      showCropResult("");
      preview.remove();
      updateCropDraftAttention();
    };
    const complete = (next) => {
      if (!stillCurrent()) { cancel(); return; }
      if (next.pointerType && event.pointerType && next.pointerType !== event.pointerType) return;
      const bbox = bboxAt(next);
      placeBox(preview, bbox);
      if (bbox[2] - bbox[0] <= 8 || bbox[3] - bbox[1] <= 8) {
        showCropResult("范围太小，请移动鼠标后再点一下；Esc 取消新框。", true);
        return;
      }
      finish();
      showCropResult("");
      if (mode === "figures") {
        preview.classList.remove("drawing");
        preview.classList.add("pending-assignment");
        openFigureSlotMenu(preview, { kind: "new", box: { page_idx: page, bbox } });
      } else {
        rememberDialogBoxes(copyDialogBoxes());
        dialog.boxes.push({ page_idx: page, bbox });
        dialog.selected = dialog.boxes.length - 1;
        preview.remove();
        renderPageTabs();
        renderStage();
        $("pageStage").querySelector(`[data-box-index="${dialog.selected}"]`)?.focus({ preventScroll: true });
      }
    };
    const sketch = { cancel, complete };
    dialog.sketch = sketch;
    updateCropDraftAttention();
    window.addEventListener("pointermove", move);
    window.addEventListener("pointercancel", cancel);
    window.addEventListener("blur", cancel);
  }

  function startDrag(event, surface, index, handle) {
    if (dialog.saving || event.button !== 0 || dialog.mode === "view" || dialog.tool === "pan" || dialog.spacePan || !dialog.imageReady) return;
    if (handle === "create" && ["new", "regions"].includes(dialog.mode) && dialog.boxes.length >= 12) {
      toast("一道题最多保留 12 段范围，请合并相邻片段后再添加", "error");
      return;
    }
    (index === null ? $("pageStage") : surface.querySelector(`[data-box-index="${index}"]`))?.focus({ preventScroll: true });
    event.preventDefault();
    const start = pointFrom(event, surface);
    const target = index === null ? null : event.currentTarget;
    const box = index === null ? null : dialog.boxes[index];
    const original = box ? [...box.bbox] : null;
    const before = copyDialogBoxes();
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
      updateCropDraftAttention();
      placeBox(target, box.bbox);
    };
    const up = (upEvent) => {
      finish();
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
          if (dialog.mode === "read") dialog.boxes = [];
          rememberDialogBoxes(before);
          dialog.boxes.push({ page_idx: dialog.page, bbox });
          dialog.selected = dialog.boxes.length - 1;
          renderPageTabs();
        }
        preview.remove();
      } else if (box && JSON.stringify(box.bbox) !== JSON.stringify(original)) {
        rememberDialogBoxes(before);
      } else if (dialog.mode === "figures") {
        // Keep an unmoved frame connected so the browser can deliver the
        // following click/double-click without starting another gesture.
        return;
      }
      renderStage();
      $("pageStage").querySelector(`[data-box-index="${dialog.selected}"]`)?.focus({ preventScroll: true });
    };
    const cancel = () => {
      finish();
      if (box) { box.bbox = original; if (target.isConnected) placeBox(target, original); }
      preview?.remove();
      updateCropDraftAttention();
    };
    const finish = () => {
      dialog.drag = null;
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("pointercancel", cancel);
      window.removeEventListener("blur", cancel);
    };
    dialog.drag = cancel;
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", cancel);
    window.addEventListener("blur", cancel);
  }

  function readingOrder(boxes) {
    return dialog.mode === "figures" ? QBFigureJoin.readingOrder(boxes)
      : boxes.map((box) => ({ page_idx: box.page_idx, bbox: [...box.bbox] }));
  }

  function setCropSaving(saving) {
    dialog.saving = saving;
    editGuard.setSaving(CROP_EDIT_KEY, saving);
    $("pageDialog").classList.toggle("saving", saving);
    if (saving) {
      dialog.disabledControls = [...$("pageDialog").querySelectorAll("button, input, select")].map((node) => [node, node.disabled]);
      dialog.disabledControls.forEach(([node]) => { node.disabled = true; });
      $("pageDialogSaveNext").textContent = "正在保存…";
    } else {
      (dialog.disabledControls || []).forEach(([node, disabled]) => { if (node.isConnected) node.disabled = disabled; });
      dialog.disabledControls = [];
      configureCropActions();
      applyPageZoom();
      renderRegionPieces();
    }
  }

  function continueManualCut(number, savedQuestion) {
    dialog.question = null;
    dialog.mode = "new";
    dialog.boxes = [];
    dialog.selected = null;
    dialog.history = [];
    dialog.future = [];
    dialog.scrolled = true;
    if (!$("groupSelect").value && savedQuestion?.group?.id) $("groupSelect").value = String(savedQuestion.group.id);
    $("numberInput").value = QBManualCrop.nextCropNumber(state.questions, number + 1, $("groupSelect").value || null) ?? "";
    trackCropDraft();
    renderPageTabs();
    renderStage();
    $("pageStage").focus({ preventScroll: true });
    if (teaching.active && teaching.paper === state.paperId) showCropResult("");
    else showCropResult(state.paper?.demo ? `第 ${number} 题已保存。点“完成切题”回到题卡；示例只保留原图，不调用 AI。`
      : `第 ${number} 题已保存，尚未识读。可以继续框下一题；全部切完点“完成切题”，自动识读已保存的题目。`);
  }

  async function savePageCrop({ next = false, complete = false } = {}) {
    if (dialog.saving || !$("pageDialog").open) return;
    if (dialog.mode === "view" || dialog.practiceRead) return;
    if (state.paper?.demo && dialog.mode === "read") {
      showCropResult("示例只练习框选，不会调用识读服务。"); return;
    }
    if (state.paperId !== dialog.paperId) { showCropResult("当前试卷已变化，请重新打开原卷", true); return; }
    if (dialog.sketch) {
      showCropResult("请先再点一下固定新框，或按 Esc 取消这个新框，再保存。", true);
      return;
    }
    if (dialog.drag) dialog.drag();
    const q = dialog.question;
    const session = dialog.session;
    let savedQuestion = null;
    let saved = false;
    if (complete && dialog.mode === "new" && !dialog.boxes.length) {
      // Saving the last real question opens an empty next draft. Finishing
      // that draft must not demand another box or create an extra question.
      const paperId = dialog.paperId;
      const ids = dialog.cutQuestionIds?.length ? [...dialog.cutQuestionIds] : null;
      closeFigureSlotMenu({ cancelPending: true, rerender: false });
      editGuard.release(CROP_EDIT_KEY);
      $("pageDialog").close();
      await enterCutReadingStage(paperId, ids);
      return;
    }
    if (["new", "regions"].includes(dialog.mode) && !dialog.boxes.length) {
      showCropResult("请先框出题目范围", true); return;
    }
    if (dialog.mode === "new" && (!Number.isInteger(Number($("numberInput").value)) || Number($("numberInput").value) < 1 || Number($("numberInput").value) > 999)) {
      showCropResult("请填写题号（1–999）", true); $("numberInput").focus(); return;
    }
    setCropSaving(true);
    showCropResult(dialog.mode === "read" ? "正在提交选中片段…" : "正在保存…");
    try {
      if (dialog.mode === "regions") {
        if (!dialog.boxes.length) { toast("至少要有一个框", "error"); return; }
        const manual = true;
        const regions = readingOrder(dialog.boxes);
        const data = JSON.stringify(regions) === JSON.stringify(q.regions) ? { question: q }
          : await api(`/api/questions/${q.id}/regions`, { method: "POST", body: { regions, processing_mode: "manual" } });
        dialog.cropBaseline = JSON.stringify(cropSnapshot());
        clearCropDraftAttention();
        applyQuestion(data);
        savedQuestion = data.question || q;
        saved = true;
        toast(manual ? `第 ${q.number} 题范围已保存，请核对原图；未调用 AI` : `第 ${q.number} 题范围已更新，请重新核对`);
        refreshPaper();
      } else if (dialog.mode === "figures") {
        if (dialog.pendingFigure || dialog.slotTarget?.kind === "new") {
          toast("请先选择新配图属于题干、某个选项或无关；也可以点取消撤销新框", "error");
          $("figureSlotMenu").querySelector("[data-figure-slot]")?.focus({ preventScroll: true });
          return;
        }
        const figures = figuresFromBoxes(dialog.boxes);
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
        dialog.cropBaseline = JSON.stringify(cropSnapshot());
        clearCropDraftAttention();
        applyQuestion(data);
        toast(q.approved ? `第 ${q.number} 题配图已保存，旧审批已撤销，请重新审核` : `第 ${q.number} 题配图已保存，请审核题卡`);
        teach({ type: "figures", number: q.number, figures: (data.question?.figures || []).length });
      } else if (dialog.mode === "read") {
        const box = dialog.boxes[dialog.boxes.length - 1];
        if (!box) { toast("请先在原卷上框出要识读的那一块", "error"); return; }
        const target = $("readTargetSelect").value;
        const pending = queueRegionRead(q, box, target);
        $("pageDialogClose").disabled = false;
        $("pageDialogClose").textContent = "取消识读";
        await pending;
        if (dialog.session !== session) return;
        toast(`AI 正在读框里的字，读完显示在第 ${q.number} 题的题卡上`);
        refreshPaper();
      } else {
        const number = Number($("numberInput").value);
        if (!Number.isInteger(number) || number < 1) { toast("请填写题号", "error"); return; }
        if (!dialog.boxes.length) { toast("请先在原卷上框出这道题的范围", "error"); return; }
        const selectedGroup = $("groupSelect").value;
        const body = { number, regions: readingOrder(dialog.boxes), body_mode: "source_image", processing_mode: "manual",
          question_type: $("cropTypeSelect").value || "unknown" };
        if (selectedGroup) body.group_id = Number(selectedGroup);
        const data = await api(`/api/papers/${state.paperId}/questions`, { method: "POST", body });
        applyQuestion(data);
        savedQuestion = data.question;
        if (savedQuestion?.id && !dialog.cutQuestionIds?.includes(savedQuestion.id)) {
          dialog.cutQuestionIds = [...(dialog.cutQuestionIds || []), savedQuestion.id];
        }
        saved = true;
        if (!(teaching.active && teaching.paper === state.paperId)) {
          toast(state.paper?.demo ? `第 ${number} 题已保存；练习只保留原图，不调用 AI` : `第 ${number} 题已保存；结束切题后自动 AI 识读`);
        }
        teach({ type: "cut", number });
        refreshPaper();
      }
      if (next && savedQuestion) {
        continueManualCut(savedQuestion.number, savedQuestion);
        return;
      }
      closeFigureSlotMenu({ cancelPending: true, rerender: false });
      editGuard.release(CROP_EDIT_KEY);
      $("pageDialog").close();
      if (complete && savedQuestion) {
        setCropSaving(false);
        await enterCutReadingStage(dialog.paperId, dialog.cutQuestionIds?.length ? [...dialog.cutQuestionIds] : null);
      }
    } catch (error) {
      if (dialog.session !== session || !$("pageDialog").open) return;
      if (saved) trackCropDraft();
      if (error.name === "AbortError") showCropResult("识读已取消，框选仍保留。");
      else {
        showCropResult(dialog.mode === "read" ? `识读未开始：${error.message}。框选仍保留，可以重试或手动改字。`
          : `未保存：${error.message}。框选仍保留，请重试。`, true);
        toast(error.message, "error");
      }
    } finally { if (dialog.session === session && dialog.saving) setCropSaving(false); }
  }

  $("pageDialogSave").addEventListener("click", () => savePageCrop());
  $("pageDialogSaveNext").addEventListener("click", () => savePageCrop({ next: true }));
  $("pageDialogComplete").addEventListener("click", () => savePageCrop({ complete: true }));
  $("pageDialog").addEventListener("keydown", (event) => {
    const shortcutContext = {
      open: $("pageDialog").open, mode: dialog.mode, practiceRead: dialog.practiceRead,
      saving: dialog.saving, closing: dialog.closing,
      otherDialog: Boolean(document.querySelector('dialog[open]:not(#pageDialog)')), menuOpen: menuIsOpen(),
      editing: QBUpload.isEditingTarget(event.target), onControl: Boolean(event.target.closest?.("button, a, summary")),
      canvasFocused: Boolean(event.target.closest?.("#pageStage"))
    };
    const action = QBManualCrop.cropShortcutAction(event, shortcutContext);
    if (!action) {
      // Ctrl+S is an app operation in the cutting window. A blocked save
      // (typing or an unfinished operation) must not open the
      // browser's Save Page dialog instead. Composition remains untouched.
      if (shortcutContext.open && shortcutContext.mode === "new" && !shortcutContext.otherDialog
        && !event.defaultPrevented && !event.isComposing && event.keyCode !== 229
        && (event.ctrlKey || event.metaKey) && !event.altKey && event.key.toLowerCase() === "s") {
        event.preventDefault(); event.stopPropagation?.();
      }
      return;
    }
    event.preventDefault();
    event.stopPropagation?.();
    if (action === "next") void savePageCrop({ next: true });
    else if (action === "complete") void savePageCrop({ complete: true });
    else if (action === "save") void savePageCrop();
    else if (action === "undo" || action === "redo") restoreDialogBoxes(action === "redo");
    else if (action === "previous-page" || action === "next-page") {
      const index = dialogPageIndex() + (action === "next-page" ? 1 : -1);
      if (state.paper.pages?.[index]) goToDialogPage(state.paper.pages[index].page_idx);
    } else if (action === "pan") {
      cancelFigureSketch();
      dialog.spacePan = true;
      $("pageStage").classList.add("pan-ready");
    } else if (action === "zoom-in") zoomPageBy(1.25);
    else if (action === "zoom-out") zoomPageBy(0.8);
    else if (action === "fit" || action === "width") requestPageZoom(action);
    else if (action === "delete" && !dialog.sketch && dialog.selected !== null && dialog.selected !== undefined) removeBox(dialog.selected);
  }, true);

  $("manualProcessing").addEventListener("click", () => {
    $("toolsMenu").open = false;
    openManualCut();
  });

  const manualSwitches = new Set();
  const manualSwitchRequests = new Map();

  function renderManualEntryState() {
    renderSettingsTask();
    renderCutReadingStage();
    const emptyEntry = $("emptyManualCut");
    if (emptyEntry) {
      emptyEntry.disabled = manualSwitches.has(state.paperId) || aiCutContinuations.has(state.paperId);
      emptyEntry.textContent = manualSwitches.has(state.paperId) ? "正在准备原卷…" : "手工切题";
    }
  }

  function cancelPendingPageOpening() {
    pageOpenIntent += 1;
    const changed = manualSwitchRequests.size > 0;
    manualSwitchRequests.forEach((request, id) => {
      request.controller.abort();
      manualSwitches.delete(id);
    });
    manualSwitchRequests.clear();
    if (changed) renderManualEntryState();
  }

  const aiCutContinuations = new Set();

  async function continueAiCut() {
    const paper = state.paper, id = state.paperId;
    if (!id || paper?.id !== id || manualSwitches.has(id) || aiCutContinuations.has(id)
      || !QBProgress.canContinueAiCut(paper)) return false;
    if (QBProgress.canSwitchMinerUToManual(paper)) {
      // This is a choice to keep waiting, not a new cloud task or retry.
      toast(`已保留当前 AI 切题任务：${QBProgress.processingPresentation(paper).headline}。不会重新提交原稿。`);
      return true;
    }
    if (paperReadSubmissionPending(id) || state.questions.some(q => q.ocr_pending || q.reread_requested)) {
      toast("已有题目正在识读，请先等待或停止本次识读，再继续 AI 切题；已保存内容保留。", "error");
      return false;
    }
    const revision = Number(paper.processing_plan?.revision) || 0;
    aiCutContinuations.add(id); renderManualEntryState();
    try {
      const confirmed = await confirmDialog({
        title: "继续 AI 切题？",
        text: "原卷、已保存的题目、手工范围和修改，以及已通过或入库的版本都会保留，只补充尚未切出的题目。\n\n有可用 MinerU 解析时，会优先在本机继续；否则将重新提交已上传的整份原稿给已配置的 MinerU，可能使用服务额度。已停止的远端旧任务不会被当作可续用的任务。",
        ok: "继续 AI 切题", cancel: "继续手工切题", focusCancel: true
      });
      if (!confirmed || state.paperId !== id) return false;
      if ((Number(state.paper?.processing_plan?.revision) || 0) !== revision) {
        throw new Error("原卷处理状态已经变化，请先查看最新状态再继续。");
      }
      newUploadReadContinuations.delete(id);
      const data = await QBRegionWait.boundedRequest(signal => api(`/api/papers/${id}/continue-ai-cut`, {
        method: "POST", signal, body: { revision, allow_cloud: true }
      }), { timeoutMs: 30000 });
      if (state.paperId !== id) return false;
      if (data.paper?.id !== id || data.paper.parse_mode !== "mineru"
        || !["queued", "parsing", "segmenting", "reading", "ready", "needs_grouping"].includes(data.paper.status)) {
        throw new Error("继续 AI 切题的结果尚未确认，请查看最新处理状态；已有成果保留。");
      }
      updatePaperFromResponse(data.paper); renderPaper();
      toast(data.message || (data.action === "local_segmentation" ? "已使用本机解析继续切题，已有题目保留。" : "已继续 AI 切题，原卷和已有题目保留。"), "success");
      void refreshPaper(); void loadPapers();
      return true;
    } catch (error) {
      if (state.paperId !== id) return false;
      toast(error.name === "TimeoutError" ? "提交结果尚未确认，原卷和已有题目保留。请查看最新处理状态后再试。"
        : `未能继续 AI 切题：${error.message}`, "error");
      void refreshPaper(); return false;
    } finally { aiCutContinuations.delete(id); renderManualEntryState(); }
  }

  async function switchToManual(page = null, { stopMinerU = false } = {}) {
    const id = state.paperId;
    if (!id || manualSwitches.has(id) || aiCutContinuations.has(id)
      || (stopMinerU && !QBProgress.canSwitchMinerUToManual(state.paper))) return false;
    const request = { intent: ++pageOpenIntent, controller: new AbortController() };
    manualSwitchRequests.set(id, request);
    const current = () => state.paperId === id && pageOpenIntent === request.intent
      && manualSwitchRequests.get(id) === request && !request.controller.signal.aborted;
    manualSwitches.add(id);
    // Switching to manual is only the cutting step. A pending local-upload
    // continuation must not queue OCR while the original pages are prepared.
    newUploadReadContinuations.delete(id);
    renderManualEntryState();
    try {
      // The API switches the whole paper. `page` only positions the canvas;
      // sending it as a page-restriction would imply unsupported cloud scope.
      const data = await QBRegionWait.boundedRequest((signal) => api(`/api/papers/${id}/processing`, {
        method: "POST", signal, body: { mode: "manual" }
      }), { timeoutMs: 30000, signal: request.controller.signal });
      if (!current()) return false;
      if (data.paper?.id !== id || data.paper.status !== "ready" || data.paper.parse_mode !== "manual"
        || !data.paper.pages?.length || (stopMinerU && data.manual_ready !== true)) {
        throw new Error("手工切题的原页尚未准备好，原卷和已有题卡保留，请稍后查看任务状态。");
      }
      updatePaperFromResponse(data.paper);
      const refreshed = await refreshPaper();
      if (!current()) return false;
      if (!refreshed || state.paper?.status !== "ready" || state.paper.parse_mode !== "manual" || !state.paper.pages?.length) {
        throw new Error("已请求转为手工切题，但界面尚未确认最新原页和题卡，请稍后重试。");
      }
      if ($("pageDialog").open) closePageDialog({ preserveIntent: true });
      toast(data.message || "已转为手工切题，原卷、已有题目和修改已保留；先框题，尚未开始 AI 识读。", "success");
      openPageDialog("new", null, { page });
      return true;
    } catch (error) {
      if (!current() || error.name === "AbortError") return false;
      const message = error.name === "TimeoutError"
        ? "切换结果尚未确认，原卷和已有题卡保留；请稍后查看任务状态或重试。"
        : `未能进入手工切题：${error.message}`;
      toast(message, "error");
      void refreshPaper();
      return false;
    } finally {
      // An aborted older request must not clear a newer entry's busy state.
      if (manualSwitchRequests.get(id) === request) {
        manualSwitchRequests.delete(id);
        manualSwitches.delete(id);
        renderManualEntryState();
      }
    }
  }

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
  const photoUpload = { files: [], urls: [], policy: Object.freeze({ parseMode: "auto", allowCloud: false }), materialType: "exam" };
  const pasteUpload = { batch: null, resolve: null };

  function selectedMaterialType() {
    return document.querySelector('input[name="materialType"]:checked')?.value === "book" ? "book" : "exam";
  }

  function automaticParseReady() {
    // upload_enabled includes local import and must never grant cloud access.
    return Boolean(state.status?.automatic_parse_ready);
  }

  // The backend answers this the same way the reading worker will, so a card can
  // say “no key for the service you chose” before the request instead of after.
  function readerUnavailable() {
    const readiness = state.status?.reader_readiness;
    return readiness && readiness.ready === false ? readiness : null;
  }

  function renderUploadAvailability() {
    $("fileInput").disabled = false;
    $("dropZone").classList.remove("disabled");
    $("dropZone").setAttribute("aria-disabled", "false");
    // 云服务已经配好时不再挂这行常驻提示：它一直在说一件你早就知道的事，
    // 真到了会把资料发出去的那一步，弹窗里本来就会写清发送范围。
    const note = $("uploadNote");
    if (automaticParseReady()) { note.hidden = true; note.textContent = ""; return; }
    note.hidden = false;
    note.textContent = "自动判断资料，先在本机切题；未切出的题可以从原卷选取，无需密钥。";
  }

  function materialTypeLabel() {
    return selectedMaterialType() === "book" ? "一本书 / 讲义" : "一份试卷";
  }

  async function sendUpload(form, label) {
    toast(`正在上传 ${label}…`);
    const data = await api("/api/papers", { method: "POST", form });
    if (data.duplicate) toast("这份试卷之前上传过，已为你打开");
    else if (data.paper?.id) newUploadReadContinuations.add(data.paper.id);
    return data.paper;
  }

  async function handleFiles(fileList) {
    const routed = QBUpload.routeFiles(fileList);
    if (!routed.files.length) return;
    if (!routed.accepted.length) {
      toast("只支持 PDF、DOCX、JPG、PNG 和 WEBP 文件", "error");
      return;
    }
    const materialType = selectedMaterialType();
    if (routed.unsupported.length) {
      toast(`已跳过 ${routed.unsupported.length} 个不支持的文件`, "error");
    }
    let acknowledged = false;
    try { acknowledged = Boolean(sessionStorage.getItem("qb-cloud-upload-ack")); } catch { /* 无存储时每次都提示 */ }
    const policy = await QBUpload.resolveUploadPolicy({ cloudReady: automaticParseReady(), acknowledged }, () => confirmDialog({
      title: "本机无法切题时，允许云处理吗？",
      text: "资料会先保存在本机并尝试切题。若本机无法切题，允许后才会把原稿发送给已配置的 MinerU，并按现有读题设置使用看图服务或 AI 助手，可能使用服务额度。系统不会预先擦除姓名、手写或批改痕迹。\n\n不允许也能导入，然后从原卷选取题目。此选择适用于本次上传；允许后本窗口后续上传不再重复提示。",
      ok: "允许使用已配置服务",
      cancel: "仅保留在本机",
      focusCancel: true
    }));
    if (policy.allowCloud && !acknowledged) {
      try { sessionStorage.setItem("qb-cloud-upload-ack", "1"); } catch { /* 无存储时每次都提示 */ }
    }
    const pictures = routed.pictures;
    const others = routed.documents;
    let last = null;
    for (const file of others) {
      const form = new FormData();
      form.append("file", file);
      form.append("material_type", materialType);
      QBUpload.appendUploadPolicy(form, policy);
      try { last = await sendUpload(form, file.name); } catch (error) { toast(`${file.name}：${error.message}`, "error"); }
    }
    if (last) { await loadPapers(); selectPaper(last.id); }
    if (pictures.length > MAX_PHOTOS) toast(`一份试卷最多 ${MAX_PHOTOS} 张照片，这次选了 ${pictures.length} 张`, "error");
    else if (pictures.length) openPhotoDialog(pictures, { policy, materialType });
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

  function openPhotoDialog(files, { policy = Object.freeze({ parseMode: "auto", allowCloud: false }), materialType = selectedMaterialType() } = {}) {
    photoUpload.urls.forEach((url) => URL.revokeObjectURL(url));
    photoUpload.files = files;
    photoUpload.policy = policy;
    photoUpload.materialType = materialType;
    photoUpload.urls = files.map((file) => URL.createObjectURL(file));
    $("photoHint").textContent = `这些照片按下列顺序合成${materialType === "book" ? "一本书 / 讲义" : "一份试卷"}。${policy.allowCloud ? "本次已允许使用已配置的云服务；无法自动切出的题可以从原卷补齐。" : "本次仅在本机保存，可从原卷选取题目；不会自动调用 AI。"}`;
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
    form.append("material_type", photoUpload.materialType);
    QBUpload.appendUploadPolicy(form, photoUpload.policy);
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
  // Files dropped anywhere in the window are uploaded, not only on the small
  // box in the sidebar; an overlay says so while they are dragged over.
  let windowDrags = 0;
  const carriesFiles = (event) => [...(event.dataTransfer?.types || [])].includes("Files");
  const showDropOverlay = (show) => {
    $("dropOverlay").hidden = !show;
    if (show) {
      $("dropOverlayType").textContent = selectedMaterialType() === "book"
        ? "作为“一本书 / 讲义”上传（可在左侧改成“一份试卷”）"
        : "作为“一份试卷”上传；几张照片会合成一份（可在左侧改成“一本书 / 讲义”）";
    }
  };
  window.addEventListener("dragenter", (event) => {
    if (!carriesFiles(event) || anyDialogOpen()) return;
    windowDrags += 1;
    showDropOverlay(true);
  });
  window.addEventListener("dragleave", (event) => {
    if (!carriesFiles(event)) return;
    windowDrags = Math.max(0, windowDrags - 1);
    if (!windowDrags) showDropOverlay(false);
  });
  window.addEventListener("dragover", (event) => {
    if (!event.target.closest?.("#dropZone")) event.preventDefault();
  });
  window.addEventListener("drop", (event) => {
    windowDrags = 0;
    showDropOverlay(false);
    if (event.target.closest?.("#dropZone")) return;
    event.preventDefault();
    if (!carriesFiles(event) || anyDialogOpen()) return;
    handleFiles(event.dataTransfer.files);
  });

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
    if (!(await discardEdits())) return;
    const save = $("splitConfirm");
    const groupCount = splitPlan.groups.length;
    save.disabled = true;
    try {
      const data = await api(`/api/papers/${splitPlan.paperId}/split`, {
        method: "POST", body: { groups: splitPlan.groups }
      });
      if ($("splitDialog").open) $("splitDialog").close();
      const targetId = data.papers?.[0]?.id || data.paper?.id || null;
      await clearPaperSelection();
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

  $("keysButton").addEventListener("click", () => closeSettingsThen(() => openShortcutHelp()));

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

  function requestDialogClose(modal) {
    if (!modal?.open) return;
    if (modal.id === "pageDialog") requestPageDialogClose();
    else if (modal.id === "credentialDialog") requestCredentialClose();
    else modal.close();
  }

  document.querySelectorAll("dialog [data-close]").forEach((node) => node.addEventListener("click", () => {
    requestDialogClose(node.closest("dialog"));
  }));
  // Finishing a range can rebuild its canvas during pointerdown. Chromium
  // may then retarget the final click to the dialog itself. Only a pointer
  // press and click that both happen outside the dialog are a backdrop close.
  const backdropPresses = new WeakMap();
  document.querySelectorAll("dialog").forEach((node) => {
    node.addEventListener("pointerdown", (event) => {
      const rect = node.getBoundingClientRect();
      const outside = event.clientX < rect.left || event.clientX > rect.right
        || event.clientY < rect.top || event.clientY > rect.bottom;
      backdropPresses.set(node, event.button === 0 && event.target === node && outside);
    }, { capture: true });
    node.addEventListener("pointercancel", () => backdropPresses.delete(node));
    node.addEventListener("close", () => backdropPresses.delete(node));
    node.addEventListener("click", (event) => {
      const startedOutside = backdropPresses.get(node);
      backdropPresses.delete(node);
      const rect = node.getBoundingClientRect();
      const endedOutside = event.clientX < rect.left || event.clientX > rect.right
        || event.clientY < rect.top || event.clientY > rect.bottom;
      if (event.target === node && startedOutside && endedOutside) requestDialogClose(node);
    });
  });

  setLens(state.lens);
  setFocus(state.focus);

  // ---------------------------------------------------------------- 启动

  // ---------------------------------------------------------------- 新手引导

  // 第一次打开时先说清楚三步：上传、核对、入库；再可以带着看一遍界面。
  // 看过就记在这台电脑的浏览器里；设置 → 帮助里可以重新看。
  function renderWelcomeSetup() {
    const s = state.status;
    const node = $("welcomeSetup");
    if (!s) {
      node.className = "settings-ready missing";
      node.textContent = "连不上本机服务：请关掉题有据再重新打开。";
      return;
    }
    node.className = `settings-ready ${automaticParseReady() ? "ready" : "missing"}`;
    node.textContent = automaticParseReady() ? "可以直接导入，程序先在本机切题。云处理已配置，使用前会说明发送范围；实际可用性以处理结果为准。"
      : "现在就能导入资料、从原卷选题，不需要密钥。以后需要云处理或主动识读时，再到设置中的 API 配置填写密钥。";
  }

  function openWelcome() {
    closeSettingsThen(() => {
      renderWelcomeSetup();
      $("welcomeDialog").showModal();
      requestAnimationFrame(() => $("welcomeTour").focus());
    });
  }

  function finishWelcome() {
    writePref("qb-welcome-seen", "1");
    if ($("welcomeDialog").open) $("welcomeDialog").close();
  }

  $("welcomeSkip").addEventListener("click", finishWelcome);
  $("welcomeDialog").addEventListener("cancel", () => writePref("qb-welcome-seen", "1"));
  $("welcomeTour").addEventListener("click", () => { finishWelcome(); openAutomaticGuide(); });
  $("settingsWelcome").addEventListener("click", async () => {
    if (window.location.pathname === "/settings") { await leaveFor("/?tour=1"); return; }
    closeSettingsThen(startTour);
  });

  // 界面导览：把要讲的地方圈亮，旁边一张小卡片说明；找不到的地方（例如还没有题卡）就跳过。
  const TOUR_STEPS = [
    { target: () => $("dropZone"), title: "上传资料",
      text: "PDF、Word 或照片拖进窗口即可。资料先保存在本机，未切出的题可以从原卷手工选取。" },
    { target: () => $("paperList"), title: "继续已有试卷",
      text: "每份试卷的进度写在列表里，点一下继续；原卷会一直保留。" },
    { target: () => firstTourCard()?.querySelector(".card-tick"), title: "核对后通过，自动入库",
      text: "先对照原卷核对题面，错字用“改字”修正。确认完整正确后打勾，通过即入库。" },
    { target: () => document.querySelector('.topnav a[href="/library"]'), title: "找题和组卷",
      text: "正式题库可以搜索、全屏看题和选题组卷；选好题后先看预览，再导出。" },
    { target: () => $("settingsButton"), title: "遇到问题时看帮助",
      text: "设置 → 帮助有新手练习、补图和跨页说明、常见问题与快捷键。" }
  ];

  const tour = { steps: [], index: 0, target: null };

  function firstTourCard() {
    const cards = cardNodes();
    return cards.find((card) => !card.classList.contains("compact")) || null;
  }

  function tourVisible(node) {
    if (!node || !node.isConnected) return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0 && getComputedStyle(node).visibility !== "hidden";
  }

  function tourMode(pointing) {
    tour.pointing = pointing;
    $("tour").classList.toggle("pointing", pointing);
    $("tourPrev").hidden = pointing;
    $("tourSkip").hidden = pointing;
  }

  let teachingTarget = null;

  // 练习说明留在指导栏，只给目标加轮廓，避免另一个说明浮层遮住原卷。
  function pointAt(node, title, text) {
    if (!tourVisible(node)) return false;
    if (teaching.active) {
      endTour();
      teachingTarget = node;
      node.classList.add("teaching-target");
      renderTeach(text);
      const rect = node.getBoundingClientRect();
      if (rect.top < 90 || rect.bottom > window.innerHeight - 20) node.scrollIntoView({ block: "center" });
      return true;
    }
    tourMode(true);
    tour.steps = [];
    tour.target = node;
    $("tour").hidden = false;
    $("tourStep").textContent = "在这里";
    $("tourTitle").textContent = title;
    $("tourText").textContent = text;
    $("tourNext").textContent = "知道了";
    const rect = node.getBoundingClientRect();
    if (rect.top < 90 || rect.bottom > window.innerHeight - 20) node.scrollIntoView({ block: "center" });
    requestAnimationFrame(placeTour);
    return true;
  }

  document.addEventListener("pointerdown", (event) => {
    if (tour.pointing && !$("tour").hidden && !$("tourCard").contains(event.target)) endTour();
  }, true);

  function startTour() {
    tourMode(false);
    if (document.documentElement.classList.contains("review-fullscreen")) setReviewFullscreen(false);
    tour.steps = TOUR_STEPS.filter((step) => tourVisible(step.target()));
    if (!tour.steps.length) return;
    tour.index = 0;
    $("tour").hidden = false;
    document.documentElement.classList.add("touring");
    showTourStep();
  }

  function endTour() {
    clearTimeout(tour.pending);
    teachingTarget?.classList.remove("teaching-target");
    teachingTarget = null;
    $("tour").hidden = true;
    document.documentElement.classList.remove("touring");
    tour.target = null;
    tourMode(false);
  }

  function showTourStep() {
    const step = tour.steps[tour.index];
    const target = step.target();
    if (!tourVisible(target)) {
      // The page changed under the tour (a card was re-rendered): move on.
      if (tour.index < tour.steps.length - 1) { tour.index += 1; showTourStep(); } else endTour();
      return;
    }
    tour.target = target;
    $("tourStep").textContent = `${tour.index + 1} / ${tour.steps.length}`;
    $("tourTitle").textContent = step.title;
    $("tourText").textContent = step.text;
    $("tourPrev").disabled = tour.index === 0;
    $("tourNext").textContent = tour.index === tour.steps.length - 1 ? "开始使用" : "下一步";
    const rect = target.getBoundingClientRect();
    if (rect.top < 60 || rect.bottom > window.innerHeight - 20) target.scrollIntoView({ block: "center" });
    requestAnimationFrame(placeTour);
    requestAnimationFrame(() => $("tourNext").focus({ preventScroll: true }));
  }

  function placeTour() {
    if ($("tour").hidden || !tour.target) return;
    const pad = 6;
    const rect = tour.target.getBoundingClientRect();
    const spot = $("tourSpot");
    const top = Math.max(4, rect.top - pad);
    const left = Math.max(4, rect.left - pad);
    const bottom = Math.min(window.innerHeight - 4, rect.bottom + pad);
    const right = Math.min(window.innerWidth - 4, rect.right + pad);
    Object.assign(spot.style, { top: `${top}px`, left: `${left}px`, width: `${right - left}px`, height: `${Math.max(0, bottom - top)}px` });
    const card = $("tourCard");
    const width = card.offsetWidth;
    const height = card.offsetHeight;
    const gap = 12;
    let y = bottom + gap;
    if (y + height > window.innerHeight - 8) y = top - gap - height;
    if (y < 8) y = Math.min(window.innerHeight - height - 8, Math.max(8, top + 12));
    let x = left;
    // A tall target leaves no room above or below: sit beside it.
    if (bottom - top > window.innerHeight * 0.6) {
      x = right + gap + width < window.innerWidth - 8 ? right + gap : Math.max(8, left - gap - width);
      y = Math.min(window.innerHeight - height - 8, Math.max(8, top + 12));
    }
    x = Math.min(window.innerWidth - width - 8, Math.max(8, x));
    card.style.left = `${Math.round(x)}px`;
    card.style.top = `${Math.round(y)}px`;
  }

  function moveTour(step) {
    const next = tour.index + step;
    if (next < 0) return;
    if (next >= tour.steps.length) { endTour(); return; }
    tour.index = next;
    showTourStep();
  }

  $("tourNext").addEventListener("click", () => { if (tour.pointing) endTour(); else moveTour(1); });
  $("tourPrev").addEventListener("click", () => moveTour(-1));
  $("tourSkip").addEventListener("click", endTour);
  window.addEventListener("resize", () => requestAnimationFrame(placeTour));
  window.addEventListener("scroll", () => requestAnimationFrame(placeTour), { passive: true });
  // While the tour is open its keys come first and the review shortcuts wait.
  document.addEventListener("keydown", (event) => {
    if ($("tour").hidden) return;
    // Modal controls own their shortcuts, including save combinations. The
    // optional screen tour must not consume keys before the cutting dialog.
    if (anyDialogOpen()) return;
    if (tour.pointing) {
      // Pointing never blocks the page; Esc drops the highlight unless a window is open.
      if (event.key === "Escape" && !anyDialogOpen()) { endTour(); event.preventDefault(); event.stopPropagation(); }
      return;
    }
    if (event.key === "Escape") endTour();
    else if (event.key === "ArrowRight" || (event.key === "Enter" && event.target === document.body)) moveTour(1);
    else if (event.key === "ArrowLeft") moveTour(-1);
    else if (event.key === "Tab") return;
    else if (!(event.key === "Enter" || event.key === " ")) { /* swallow review shortcuts */ }
    else return;
    event.preventDefault();
    event.stopPropagation();
  }, true);

  // ---------------------------------------------------------------- 新手教学

  // 在示例试卷上一步步做一遍：右下角的小卡片说这一步要做什么，做对了自动进入下一步。
  // 进度记在这台电脑的浏览器里；换到别的试卷时先暂停，回到示例试卷接着做。
  const TEACH_KEY = "qb-teach";
  const teaching = { paper: null, index: 0, active: false, timer: null, pending: null, completed: null,
    session: 0, moving: false, migrated: false, course: "basic", practiceUrl: null };

  function cancelTeachingAdvance() {
    clearTimeout(teaching.timer);
    teaching.timer = null;
    teaching.pending = null;
  }

  function saveTeaching() {
    const lesson = QBTeach.LESSONS[teaching.index];
    const key = lesson?.section === "basic" ? TEACH_KEY : `${TEACH_KEY}-task`;
    writePref(key, lesson && teaching.paper ? JSON.stringify({
      paper: teaching.paper, version: QBTeach.VERSION, lesson: lesson.key,
      completed: teaching.completed === lesson.key, active: teaching.active, course: lesson.section, practiceUrl: teaching.practiceUrl
    }) : "");
    renderLearningEntry();
  }

  function loadTeaching() {
    try {
      const saved = JSON.parse(readPref(TEACH_KEY, "") || "null");
      const restored = QBTeach.restore(saved);
      if (restored) { Object.assign(teaching, restored); saveTeaching(); }
    } catch { /* 没有进度就不显示 */ }
  }

  function readTeachingProgress() {
    try { return QBTeach.restore(JSON.parse(readPref(TEACH_KEY, "") || "null")); }
    catch { return null; }
  }

  function renderLearningEntry() {
    const saved = readTeachingProgress();
    const entry = $("settingsLearn");
    entry.textContent = saved && !saved.migrated && saved.course === "basic" ? saved.completed === "finish" ? "回看已完成的手工练习" : "继续手工练习" : "开始手工练习";
  }

  async function startTeaching({ reset = false, review = false, task = "basic" } = {}) {
    if (!["basic", "figure"].includes(task)) task = "basic";
    if (window.location.pathname === "/settings") {
      await leaveFor(`/?learn=${reset ? "restart" : task}`);
      return;
    }
    if (!(await discardEdits())) return;
    closeSettingsThen(() => {});
    if ($("welcomeDialog").open) finishWelcome();
    const previous = !reset && task === "basic" ? readTeachingProgress() : null;
    try {
      let data = await api("/api/demo", { method: "POST", body: { reset, course: "basics" } });
      if (data.restart_required) {
        const confirmed = await confirmDialog({ title: "重新开始新版示例练习？",
          text: "旧版示例需要重置才能练实际切题。这里只重置示例；你的正式题库、组卷和 API 设置保持原样。",
          ok: "重置示例并开始", focusCancel: true });
        if (!confirmed) return;
        data = await api("/api/demo", { method: "POST", body: { reset: true, course: "basics" } });
      }
      await loadPapers();
      await selectPaper(data.paper.id);
      if (state.paperId !== data.paper.id) return;
      cancelTeachingAdvance();
      endTour();
      const continuing = previous && !previous.migrated && previous.course === "basic" && previous.paper === data.paper.id;
      const index = task === "basic" ? continuing ? previous.index : 0 : QBTeach.indexOf(task);
      Object.assign(teaching, { paper: data.paper.id, index, course: task,
        practiceUrl: `/practice/${data.paper.id}`, active: true, session: teaching.session + 1,
        migrated: Boolean(previous?.migrated), completed: continuing ? previous.completed : null });
      // Saved crops and text stay intact when resuming. Reopening a pause after
      // its action succeeded resumes at the next action rather than duplicating it.
      if (!continuing && task === "basic" && lessonNumber(1)) teaching.index = QBTeach.indexOf("cutComplete");
      $("teachDetails").hidden = false;
      $("teachFold").setAttribute("aria-expanded", "true");
      $("teachFold").setAttribute("aria-label", "收起教学说明");
      $("teachFold").title = "收起教学说明";
      $("teachFold").textContent = "−";
      saveTeaching();
      renderTeach();
    } catch (error) { toast(error.message, "error"); }
  }

  function exitTeaching() {
    cancelTeachingAdvance();
    teaching.session += 1;
    teaching.active = false;
    saveTeaching();
    endTour();
    $("teachPanel").hidden = true;
  }

  function lessonNumber(number) {
    return state.questions.find((q) => q.number === number) || null;
  }

  function cardFor(number) {
    const q = lessonNumber(number);
    return q ? document.querySelector(`.card[data-id="${q.id}"]`) : null;
  }

  function renderTeach(hint = "") {
    const panel = $("teachPanel");
    panel.hidden = !teaching.active;
    liftGuides();
    if (!teaching.active) return;
    const progress = QBTeach.progress(teaching.index);
    const lesson = progress.lesson;
    teaching.index = QBTeach.indexOf(lesson.key);
    const onDemo = state.paperId === teaching.paper;
    $("teachSection").textContent = progress.label;
    $("teachProgress").textContent = lesson.final ? "" : `${progress.current} / ${progress.total}`;
    $("teachBar").style.width = `${Math.round((progress.current / progress.total) * 100)}%`;
    $("teachDone").hidden = teaching.completed !== lesson.key;
    if (!onDemo) {
      cancelTeachingAdvance();
      $("teachTitle").textContent = "教学暂停了";
      $("teachText").textContent = "新手教学在示例试卷里进行。回到示例试卷就能接着做。";
      $("teachHint").hidden = true;
      $("teachShow").hidden = true;
      $("teachSkip").hidden = true;
      $("teachNext").hidden = false;
      $("teachNext").textContent = "回到示例试卷";
      return;
    }
    $("teachTitle").textContent = lesson.title;
    $("teachText").textContent = lesson.text;
    const notice = hint || (teaching.migrated ? "教学已升级，已按原来的学习内容接续；要从头练，可在“设置 → 帮助”重新开始。" : "");
    $("teachHint").textContent = notice;
    $("teachHint").hidden = !notice;
    $("teachShow").hidden = Boolean(lesson.final);
    $("teachShow").textContent = lesson.key === "cut" ? "开始框题"
      : ["library", "basket", "export"].includes(lesson.key) ? "去练习题库" : "指给我看";
    $("teachSkip").hidden = true;
    $("teachSkip").textContent = lesson.checkpoint ? "先结束练习" : "跳过这一步";
    $("teachNext").hidden = !lesson.manual && teaching.completed !== lesson.key;
    $("teachNext").textContent = lesson.final ? "完成" : "继续下一步";
    if (teaching.pending === lesson.key) {
      $("teachShow").hidden = true;
      $("teachSkip").hidden = true;
    }
  }

  async function closeTeachingSurface() {
    if ($("confirmDialog").open) return false;
    const key = QBTeach.LESSONS[teaching.index]?.key;
    if (key === "fix" && !(await discardEdits())) return false;
    if (key === "cut") return true;
    if (["cutComplete", "figure"].includes(key) && $("pageDialog").open) return false;
    if (key === "viewer" && $("viewerDialog").open) $("viewerDialog").close();
    if (["original", "region"].includes(key) && $("pageDialog").open) $("pageDialog").close();
    if (key === "recovery" && $("settingsDialog").open) $("settingsDialog").close();
    return true;
  }

  async function advanceTeaching() {
    if (!teaching.active || teaching.moving) return;
    const session = teaching.session;
    teaching.moving = true;
    try {
      const closed = await closeTeachingSurface();
      if (!teaching.active || teaching.session !== session) return;
      if (!closed) {
        cancelTeachingAdvance();
        saveTeaching();
        renderTeach(teaching.completed ? "这一步已完成。先处理未保存的改动或关闭确认窗口，再点“继续下一步”。" : "先处理未保存的改动或关闭确认窗口，教学停在这里。");
        return;
      }
      cancelTeachingAdvance();
      endTour();
      teaching.migrated = false;
      teaching.completed = null;
      const next = QBTeach.nextIndex(teaching.index);
      if (next === null) { exitTeaching(); toast("这项练习已完成。其他操作可以在帮助中按需查看。", "success"); return; }
      teaching.index = next;
      $("teachDetails").hidden = false;
      $("teachFold").setAttribute("aria-expanded", "true");
      $("teachFold").setAttribute("aria-label", "收起教学说明");
      $("teachFold").title = "收起教学说明";
      $("teachFold").textContent = "−";
      saveTeaching();
      renderTeach();
    } finally { teaching.moving = false; }
  }

  // Called by the review actions: approve, save text, save figures, filter, viewer, publish.
  function teach(event) {
    if (!teaching.active || state.paperId !== teaching.paper) return;
    const lesson = QBTeach.LESSONS[teaching.index];
    if (!lesson || lesson.manual || teaching.completed === lesson.key) return;
    if (QBTeach.lessonDone(lesson.key, event)) {
      endTour();
      $("teachDone").hidden = false;
      $("teachShow").hidden = true;
      $("teachSkip").hidden = true;
      const session = teaching.session;
      const paper = teaching.paper;
      teaching.pending = lesson.key;
      teaching.completed = lesson.key;
      saveTeaching();
      renderTeach();
      const complete = () => {
        teaching.timer = null;
        if (!teaching.active || teaching.session !== session || teaching.paper !== paper
          || state.paperId !== paper || QBTeach.LESSONS[teaching.index]?.key !== lesson.key
          || teaching.pending !== lesson.key) return;
        if ($("confirmDialog").open) $("confirmDialog").addEventListener("close", complete, { once: true });
        else advanceTeaching();
      };
      teaching.timer = setTimeout(complete, lesson.key === "cut" ? 0 : 1100);
      return;
    }
    const hint = QBTeach.lessonHint(lesson.key, event);
    if (hint) renderTeach(hint);
  }

  // “指给我看”：先把要用的那张卡找出来（必要时切回“全部”），再圈亮要点的地方。
  async function showLesson() {
    const lesson = QBTeach.LESSONS[teaching.index];
    if (!lesson) return;
    if (teaching.moving || anyDialogOpen()) return;
    const focusCard = (number) => {
      const q = lessonNumber(number);
      if (!q) return;
      if (!visible(q) && !state.editing.size) setFilter("all");
      if (isApproved(q) && !state.expanded.has(q.id)) { state.expanded.add(q.id); renderCards(); }
      goTo(q);
    };
    endTour();
    const later = (find, title, text) => {
      tour.pending = setTimeout(() => {
        const node = find();
        if (!pointAt(node, title, text)) toast("没找到这个地方；可以先点“全部”，再往下找找", "error");
      }, 650);
    };
    switch (lesson.key) {
      case "cut": {
        if (!$("pageDialog").open) openPageDialog("new", null, { page: 0 });
        $("numberInput").value = "1";
        $("cropTypeSelect").value = "single_choice";
        // Only the example's type is suggested; the user's range is drawn and
        // saved through the same controls as a real paper.
        trackCropDraft();
        later(() => $("pageStage"), "框完整的第 1 题", "先点左上角，再点右下角，固定范围后点“保存下一题”。");
        break;
      }
      case "cutComplete":
        if (!$("pageDialog").open) openPageDialog("new");
        later(() => $("pageDialogComplete"), "点这里结束切题", "空白下一题可以直接完成。本练习只保留原图，不调用 AI。");
        break;
      case "tick":
      case "tick9": {
        const number = lesson.key === "tick" ? 1 : 9;
        focusCard(number);
        later(() => cardFor(number)?.querySelector(".card-tick"), "核对后点这个方框", "通过后进入独立的练习题库。");
        break;
      }
      case "fix": {
        focusCard(9);
        const card = cardFor(9);
        if (card && lessonNumber(9)) openEditor(card, lessonNumber(9));
        later(() => cardFor(9)?.querySelector(".stem-input"), "把 3 改成 5", "原卷是“向右移动 5 个单位”。改好后保存。");
        break;
      }
      case "figure":
        focusCard(2);
        later(() => cardFor(2)?.querySelector(".figure-review .button.primary") || cardFor(2),
          "点“补选配图”", "选蓝色候选图，归属选“题干”，再保存。");
        break;
      case "library":
      case "basket":
      case "export":
        saveTeaching();
        await leaveFor(teaching.practiceUrl || `/practice/${teaching.paper}`);
        break;
      default: break;
    }
  }

  // Guide mounts reserve actual layout space. Dialogs need their own mount
  // because the browser top layer makes the review sidebar inert.
  const openDialogs = [];
  function liftGuides() {
    for (let index = openDialogs.length - 1; index >= 0; index -= 1) {
      if (!openDialogs[index].open) openDialogs.splice(index, 1);
    }
    document.querySelectorAll("dialog[open]").forEach((node) => { if (!openDialogs.includes(node)) openDialogs.push(node); });
    const host = openDialogs[openDialogs.length - 1] || document.body;
    if ($("tour").parentNode !== host) host.append($("tour"));
    let mount;
    if (host === $("pageDialog")) mount = $("pageTeachMount");
    else if (host === $("viewerDialog")) mount = $("viewerTeachMount");
    else if (host !== document.body) {
      mount = host.querySelector(":scope > .teaching-dock");
      if (!mount) {
        mount = document.createElement("div");
        mount.className = "teaching-dock";
        const head = host.querySelector(":scope > .dialog-head, :scope > header");
        if (head) head.after(mount); else host.prepend(mount);
      }
    } else {
      const fallback = document.documentElement.classList.contains("review-fullscreen") || window.matchMedia("(max-width: 760px)").matches;
      mount = $(fallback ? "reviewTeachFallback" : "reviewTeachMount");
    }
    if ($("teachPanel").parentNode !== mount) mount.append($("teachPanel"));
    if (!$("tour").hidden) requestAnimationFrame(placeTour);
  }
  new MutationObserver(liftGuides).observe(document.body, { subtree: true, attributes: true, attributeFilter: ["open"] });
  new MutationObserver(liftGuides).observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
  window.addEventListener("resize", liftGuides);

  function openAutomaticGuide() {
    $("automaticGuideDialog").showModal();
  }
  $("settingsAutomaticGuide").addEventListener("click", openAutomaticGuide);
  $("emptyAutomaticGuide").addEventListener("click", openAutomaticGuide);
  $("automaticGuideImport").addEventListener("click", async () => {
    $("automaticGuideDialog").close();
    if (window.location.pathname === "/settings") { await leaveFor("/#dropZone"); return; }
    if (!(await discardEdits())) return;
    $("dropZone").scrollIntoView({ block: "center", behavior: "smooth" });
    $("dropZone").focus({ preventScroll: true });
  });

  $("teachShow").addEventListener("click", showLesson);
  $("teachSkip").addEventListener("click", () => {
    if (QBTeach.LESSONS[teaching.index]?.checkpoint) exitTeaching(); else advanceTeaching();
  });
  $("teachClose").addEventListener("click", exitTeaching);
  $("teachFold").addEventListener("click", () => {
    const collapsed = !$("teachDetails").hidden;
    $("teachDetails").hidden = collapsed;
    $("teachFold").setAttribute("aria-expanded", String(!collapsed));
    $("teachFold").setAttribute("aria-label", collapsed ? "展开教学说明" : "收起教学说明");
    $("teachFold").title = collapsed ? "展开教学说明" : "收起教学说明";
    $("teachFold").textContent = collapsed ? "+" : "−";
  });
  $("teachNext").addEventListener("click", async () => {
    if (state.paperId !== teaching.paper) {
      if (state.papers.some((paper) => paper.id === teaching.paper)) { await selectPaper(teaching.paper); renderTeach(); }
      else exitTeaching();
      return;
    }
    const lesson = QBTeach.LESSONS[teaching.index];
    if (lesson?.final) {
      if (!(await closeTeachingSurface())) return;
      teaching.completed = "finish";
      exitTeaching();
      toast("练习完成。现在可以上传自己的试卷；帮助中的短说明随时可看。", "success");
      return;
    }
    advanceTeaching();
  });
  $("welcomeLearn").addEventListener("click", () => startTeaching());
  $("settingsLearn").addEventListener("click", () => startTeaching());
  $("settingsRestartLearn").addEventListener("click", async () => {
    const confirmed = await confirmDialog({ title: "重新开始示例练习？", text: "只重置示例题目与练习进度，你的正式题库和组卷保持原样。", ok: "重新开始", focusCancel: true });
    if (confirmed) await startTeaching({ reset: true });
  });
  document.querySelectorAll("[data-teach-task]").forEach((button) => button.addEventListener("click", () => startTeaching({ task: button.dataset.teachTask })));
  $("settingsHelpFaq").addEventListener("click", () => { $("settingsFaq").open = true; $("settingsFaq").scrollIntoView({ block: "start", behavior: "smooth" }); });
  $("settingsHelpKeys").addEventListener("click", () => closeSettingsThen(() => openShortcutHelp()));
  renderLearningEntry();
  $("emptyLearn").addEventListener("click", () => startTeaching());

  async function start() {
    await loadStatus();
    if (window.location.pathname === "/settings") {
      document.title = "题有据 · 共用设置";
      document.querySelector(".layout").hidden = true;
      $("settingsHome").hidden = false;
      document.querySelectorAll(".topnav a").forEach((link) => {
        const active = new URL(link.href, window.location.href).pathname === "/settings";
        link.classList.toggle("active", active);
        if (active) link.setAttribute("aria-current", "page"); else link.removeAttribute("aria-current");
      });
      try {
        const previous = new URL(document.referrer);
        if (previous.origin === window.location.origin && ["/", "/library"].includes(previous.pathname)) {
          $("settingsReturn").href = previous.pathname + previous.search;
          $("settingsReturn").textContent = previous.pathname === "/" ? "返回录入终审" : "返回题库";
        }
      } catch (_) { /* A direct bookmark defaults to the library. */ }
      openSettings();
      if (window.ExportSettings?.mount) await window.ExportSettings.mount($("exportSettingsMount"));
      return;
    }
    await loadPapers();
    const params = new URLSearchParams(window.location.search);
    const wanted = params.get("paper") || params.get("document");
    let wantedAvailable = Boolean(wanted && state.papers.some((paper) => paper.id === wanted));
    // An archived paper stays reachable from its source link and after a refresh.
    if (!wantedAvailable && wanted && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(wanted)) {
      try {
        const data = await api(`/api/papers/${wanted}`);
        wantedAvailable = Boolean(data.paper);
        if (data.paper && !data.paper.archived) state.papers.push(data.paper);
      } catch (error) { toast(error.message, "error"); }
    }
    if (wantedAvailable) {
      await selectPaper(wanted);
      const draft = Number(params.get("draft"));
      if (draft && questionById(draft)) {
        if (isApproved(questionById(draft))) { state.expanded.add(draft); renderCards(); }
        setCurrent(draft, { focus: true });
        document.querySelector(`[data-id="${draft}"]`)?.scrollIntoView({ block: "start" });
        // 从题库里“题型待核对”点过来：直接去选题型。
        if (params.get("fix") === "type" && typeBlocksApproval(questionById(draft))) {
          requestAnimationFrame(() => focusTypePicker(questionById(draft)));
        }
      }
    } else if (state.papers.length) {
      await selectPaper(state.papers[0].id);
    }
    loadTeaching();
    if (teaching.active && !state.papers.some((paper) => paper.id === teaching.paper)) exitTeaching();
    renderTeach();
    if (["all", "new", "basic", "restart", "figure"].includes(params.get("learn"))) {
      try {
        await startTeaching({ reset: ["all", "restart"].includes(params.get("learn")), task: params.get("learn") === "figure" ? params.get("learn") : "basic" });
      } finally {
        // Opening or restarting a course is a one-time navigation action.
        // SelectPaper may have updated paper; keep that URL and its other
        // parameters even if the user cancels the legacy reset confirmation.
        const resumedUrl = new URL(window.location.href);
        resumedUrl.searchParams.delete("learn");
        window.history.replaceState(null, "", resumedUrl);
      }
      return;
    }
    if (params.get("tour") === "1") { startTour(); return; }
    if (window.location.hash === "#dropZone") {
      $("dropZone").focus({ preventScroll: true });
      $("dropZone").scrollIntoView({ block: "nearest" });
      return;
    }
    if (readPref("qb-welcome-seen", "") !== "1") openWelcome();
  }

  start();
})();
}
