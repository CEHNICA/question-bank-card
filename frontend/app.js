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

  const FIELD_NAMES = { stem: "题干", A: "选项 A", B: "选项 B", C: "选项 C", D: "选项 D", E: "选项 E" };
  const READER_NAMES = { a: "读法甲", b: "读法乙", c: "第三次裁决" };
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
    if (!question || question.edited || question.state !== "yellow" || !renderer) return empty;
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
    const todo = (c.yellow || 0) + (c.red || 0);
    if (paper.status === "failed") return paper.stopped ? `“${name}”已停止` : `“${name}”处理失败`;
    if (paper.status === "needs_grouping") return `“${name}”需要确认资料结构`;
    return `“${name}”已读完：${c.total || 0} 题${todo ? `，${todo} 张要看` : "，全部识读一致"}`;
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

  const VERSION = 2;
  const LESSONS = [
    { key: "card", title: "一道题，一张卡", manual: true,
      text: "左边是原卷，右边是题面。先看第 1 题：打勾表示你已对照确认；黄色只提示有疑点，绿色也不代表一定正确。" },
    { key: "viewer", title: "放大，再拖着看", manual: true,
      text: "点第 1 题的原卷截图。鼠标放在原卷上，按 Ctrl 滚轮放大，再按住左键拖动；也能点 ＋、全题、适宽。看完点“下一步”。" },
    { key: "tick", title: "对了就打勾",
      text: "对照第 1 题，确认没错，再点题号左边的方框（或按 Enter）标记通过。已通过的题会收起，随时能展开。" },
    { key: "fix", title: "改错字，边改边看",
      text: "第 9 题原卷是“向右移动 5 个单位”。点“改字”，把 3 改成 5：预览的绿线、选区或公式框会指出修改位置。点“保存”；改完还需重新核对、打勾。" },
    { key: "figure", title: "补上配图",
      text: "第 2 题缺图。点“补选配图”，点蓝色候选图，选“题干”，再保存。需要移动原卷时用空格＋左键或中键；普通左键用于画框。" },
    { key: "publish", title: "核对过的题，才入库",
      text: "点右上角“入库”看看说明。只入库已通过的题；表格要逐格核对，绿卡也要看原卷。示例只练操作，不会进入正式题库。" },
    { key: "basics", title: "基础练习完成", manual: true, checkpoint: true,
      text: "你已练过对照、打勾、改字、补图和入库入口。接下来可以看新版常用功能，也可以先结束；以后在“设置 → 帮助”里继续看。" },
    { key: "original", title: "整份原卷也能放大", manual: true,
      text: "从“试卷操作 → 查看整份原卷”看完整页面。Ctrl＋滚轮缩放，左键拖动画布；适页看整页，适宽看细节，还能输入页码跳页。" },
    { key: "preview", title: "不懂公式写法，也能看位置", manual: true,
      text: "在“改字”输入框中点一个位置，或选中几个字。预览用绿线、浅绿选区和公式框对应标出；公式内绿色符号帮助定位，实际插入位置仍看输入框光标。" },
    { key: "region", title: "只重读一小块，先确认替换", manual: true,
      text: "点“框选识读”，框住印刷字，再选“自动推荐（AI）”。真实识读会建议替换题干或选项的哪段文字：先看前后对比，确认填入改字，核对后保存。这里练画框，不调用读题服务。" },
    { key: "library", title: "找题，还要看完整题目", manual: true,
      text: "“正式题库”先显示题目摘要，点“完整题目”看所有条件、选项和配图。用来源、题型和排序找题；“更多筛选”放其他条件。“查看出处”和“版本历史”仍保留。示例不会入库，这一步只认识入口。" },
    { key: "basket", title: "选好题，再看试题篮", manual: true,
      text: "“选择当前已显示题目”只勾选已经加载的题，再点“加入试题篮”。“已选题目”可以集中检查篮中的题；换筛选、加载更多或收起试题篮都不会清空它。进入“组卷预览”前，先看一遍完整题目。" },
    { key: "drafts", title: "给这份练习起个名字", manual: true,
      text: "在组卷预览里填写试卷标题，点“保存草稿”；“另存为”会保留另一份。下次从“组卷草稿”继续选题和调整顺序。题目被撤回、更新或找不到时会明确提示；先处理缺题，不会悄悄换成新版本或漏印。" },
    { key: "ai", title: "标签和答案，统一设置", manual: true,
      text: "所有页面共用“设置 → 标签与参考答案”。两项默认关闭、默认手动；开启后可选录入并入库时自动生成，也可在正式题库单题或勾选批量生成。默认由当前 AI 助手通过本机工具写回，无需额外豆包 API；独立模型可选，推荐 DeepSeek。教学只认识入口，不改开关、不生成、不测试。" },
    { key: "recovery", title: "没保存时，先留住改动", manual: true,
      text: "改字时按 Ctrl＋Enter 保存。取消、换卷或离开有改动的题，会提示“继续编辑”或“丢弃改动”；刷新会有浏览器提醒，未保存的字不会自动恢复。教学进度会记住，刷新后能继续。" },
    { key: "finish", title: "现在可以用自己的试卷了", manual: true, final: true,
      text: "已有题卡能继续审核；上传、重新识读和自动推荐需要读题服务可用。遇到问题打开“设置 → 帮助”；可重做基础练习，也可只看新版功能。" }
  ].map((lesson, index) => ({ ...lesson, section: index <= 6 ? "basic" : "review" }));

  const OLD_KEYS = ["card", "viewer", "tick", "todo", "fix", "tick9", "figure", "table", "green", "publish", "finish"];
  const OLD_ALIASES = { todo: "fix", tick9: "figure", table: "publish", green: "publish", finish: "basics" };
  const indexOf = (key) => LESSONS.findIndex((lesson) => lesson.key === key);

  // Stable keys keep progress attached to its lesson when the course changes.
  function restore(saved) {
    if (!saved || typeof saved !== "object" || typeof saved.paper !== "string" || !saved.paper) return null;
    let key = "card";
    if (saved.version === VERSION) key = indexOf(saved.lesson) >= 0 ? saved.lesson : "card";
    else if (Number.isInteger(saved.index) && saved.index >= 0 && saved.index < OLD_KEYS.length) {
      const old = OLD_KEYS[saved.index];
      key = OLD_ALIASES[old] || old;
    }
    return { paper: saved.paper, index: indexOf(key), active: true, migrated: saved.version !== VERSION,
      completed: saved.version === VERSION && saved.completed === true
        && !LESSONS[indexOf(key)].manual ? key : null };
  }

  function progress(index) {
    const safeIndex = Number.isInteger(index) && index >= 0 && index < LESSONS.length ? index : 0;
    const lesson = LESSONS[safeIndex];
    const section = LESSONS.filter((item) => item.section === lesson.section && !item.final);
    return { lesson, label: lesson.final ? "学习完成" : lesson.section === "basic" ? "基础练习" : "新版功能",
      current: lesson.final ? section.length : section.findIndex((item) => item.key === lesson.key) + 1,
      total: section.length };
  }

  const FIXED = /向右移动\s*5\s*个单位/;

  // event: {type, number, stem, figures, key}
  function lessonDone(key, event) {
    if (!event || !LESSONS.some((lesson) => lesson.key === key && !lesson.manual)) return false;
    switch (key) {
      case "tick": return event.type === "approve" && event.number === 1;
      case "fix": return event.type === "text" && event.number === 9 && FIXED.test(event.stem || "");
      case "figure": return event.type === "figures" && event.number === 2 && event.figures > 0;
      case "publish": return event.type === "publish";
      default: return false;
    }
  }

  // A hint when the step was tried but not yet right.
  function lessonHint(key, event) {
    if (key === "fix" && event?.type === "text" && event.number === 9) return "还不对：原卷印的是“向右移动 5 个单位”。点“指给我看”找到第 9 题，再改一次。";
    if (key === "figure" && event?.type === "figures" && event.number === 2) return "还没有配图：点原卷上的蓝色候选图，选“题干”，再保存。";
    return "";
  }

  return { VERSION, LESSONS, restore, progress, indexOf, lessonDone, lessonHint };
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

if (typeof module !== "undefined" && module.exports) {
  module.exports = { ...QBUpload, ...QBProgress, ...QBSelection, ...QBReviewDiff, ...QBResegment, ...QBNotify, ...QBFigureJoin, ...QBFocus,
    ...QBEdits,
    ...QBRegionAssist,
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
    { key: "todo", label: "需逐题核对" },
    { key: "green", label: "识读一致" },
    { key: "approved", label: "已标记通过" },
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
    selected: new Set(), selectionAnchor: null, selectionBusy: false, selecting: false, trashBusy: false,
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
    if (await discardEdits()) window.location.assign(url);
  }

  // Browser refresh/close cannot wait for our dialog. Use its native leave
  // warning there; in-app navigation keeps the clearer two-action dialog.
  window.addEventListener("beforeunload", (event) => QBEdits.protectBeforeUnload(event, editGuard));
  document.addEventListener("click", (event) => {
    const link = event.target.closest?.("a[href]");
    if (!link || event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey
      || link.hasAttribute("download") || (link.target && link.target !== "_self") || !editGuard.hasPendingWork()) return;
    const url = new URL(link.href, window.location.href);
    const here = new URL(window.location.href);
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
    return FIGURE_REVIEW_BLOCKS.has(figureReview(q)?.status);
  }

  function isApproved(q) {
    return Boolean(q.approved && q.approval_valid !== false && !q.approval_stale && !figureBlocksApproval(q));
  }

  // AI 助手打的勾照常算通过（能入库），但还等人核对；人点一下方框就变成人工通过。
  function isAiApproved(q) {
    return isApproved(q) && q.approved_by === "ai";
  }

  function isHumanApproved(q) {
    return isApproved(q) && q.approved_by !== "ai";
  }

  function agentLabel(q) {
    return q.approval_agent || "AI";
  }

  function canApprove(q) {
    return Boolean(q.stem && !figureBlocksApproval(q) && !typeBlocksApproval(q) && (q.state === "green" || q.state === "yellow"));
  }

  // 题型没读出来（题型未定）的题不能通过：先在题号旁边选题型。
  function typeBlocksApproval(q) {
    return Boolean(q.type_blocked) && (q.state === "green" || q.state === "yellow");
  }

  function needsCheck(q) {
    return !isApproved(q) && (figureBlocksApproval(q) || typeBlocksApproval(q) || approvalNeedsReview(q) || q.state === "yellow" || q.state === "red");
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
    // 标题下面平时什么都不写；只有读不了新资料时才提醒一句（用哪家模型读题在“设置 → 读题模型”里）。
    brandNotice(s.upload_enabled ? "" : "还不能读新资料：点右上角“设置”填写密钥", "warn");
    const note = $("uploadNote");
    if (!s.upload_enabled) {
      note.hidden = false;
      note.replaceChildren(document.createTextNode(!s.mineru ? "还没有填写 MinerU 密钥，暂时不能上传新资料；已有的题卡照常可以审核。"
        : "还没有看图读题的密钥，暂时不能上传新资料（魔搭有免费的，也可以选“AI 助手读题”）。"),
      button("去填写密钥", "small", () => { openSettings(); }));
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
    const todo = (c.yellow || 0) + (c.red || 0);
    if (todo) parts.push(`${todo} 张要看`);
    if (c.published && c.published >= (c.approved || 0)) parts.push(`已入库 ${c.published}`);
    else if (c.approved) parts.push(`通过 ${c.approved}${c.published ? ` · 入库 ${c.published}` : ""}`);
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

  function clearQuestionSelection({ render = true } = {}) {
    state.selected.clear();
    state.selectionAnchor = null;
    if (render) renderSelectionState();
  }

  // 题号左边的方框平时是“打勾通过”；只有进入批量删除时才变成选择框，
  // 免得把“核对完打个勾”误当成“选中准备删除”。
  function startSelecting() {
    if (state.paper?.status !== "ready") { toast("任务处理完成后才能删除题卡", "error"); return; }
    state.selecting = true;
    renderSelectionState();
    requestAnimationFrame(() => $("selectionCancel").focus({ preventScroll: true }));
  }

  function stopSelecting({ render = true } = {}) {
    state.selecting = false;
    clearQuestionSelection({ render });
  }

  async function selectPaper(id) {
    if (state.paperId !== id && !(await discardEdits())) return;
    if (finishedUnseen.delete(id)) document.title = QBNotify.title(BASE_TITLE, finishedUnseen.size);
    if (state.paperId !== id) {
      stopSelecting({ render: false });
      state.paperId = id;
      state.rendered.clear();
      state.editing.clear();
      state.expanded.clear();
      state.autoExpanded.clear();
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
    if (teaching.active) renderTeach();
  }

  async function clearPaperSelection() {
    if (!(await discardEdits())) return false;
    clearTimeout(state.pollTimer);
    state.paperId = null;
    state.paper = null;
    state.questions = [];
    state.current = null;
    clearQuestionSelection({ render: false });
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
    $("archivedPaperNotice").hidden = !data.paper.archived;
    state.questions = data.questions;
    const existing = new Set(state.questions.map((question) => question.id));
    state.selected = new Set([...state.selected].filter((id) => existing.has(id)));
    if (state.selectionAnchor !== null && !existing.has(state.selectionAnchor)) state.selectionAnchor = null;
    const index = state.papers.findIndex((paper) => paper.id === data.paper.id);
    if (index >= 0) { state.papers[index] = data.paper; renderPaperList(); }
    renderPaper();
    if ($("viewerDialog").open) renderViewer();
    const busy = ACTIVE_STATUS.has(state.paper.status)
      || state.questions.some((q) => q.state === "waiting" || q.state === "reading" || regionReadPending(q));
    if (busy) state.pollTimer = setTimeout(refreshPaper, 2500);
  }

  // AI 助手通过、还等你核对的题数，接在“全部通过”后面说一句。
  function aiNote(c) {
    return c.ai ? `其中 ${c.ai} 题是 AI 通过的，在“AI 通过”里核对。` : "";
  }

  function counts() {
    const qs = state.questions;
    return {
      all: qs.length,
      todo: qs.filter(needsCheck).length,
      green: qs.filter((q) => !isApproved(q) && !approvalNeedsReview(q) && !figureBlocksApproval(q) && !typeBlocksApproval(q) && q.state === "green").length,
      approved: qs.filter(isApproved).length,
      ai: qs.filter(isAiApproved).length,
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
    $("meterFigure").replaceChildren(el("strong", "", String(c.approved)), document.createTextNode(` / ${c.all} 已通过`));
    $("meterFigure").title = `${c.approved} / ${c.all} 题已标记通过`;
    $("meterPercent").textContent = `${Math.round((c.approved / c.all) * 100)}%`;
    $("toolbarPaper").replaceChildren(el("strong", "", paperDisplayName(state.paper || {})),
      document.createTextNode(` · ${c.approved} / ${c.all} 已通过`));
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
    text.append(icon("check"), document.createTextNode((c.unpublished
      ? `全部 ${c.all} 题已标记通过，还有 ${c.unpublished} 题没入库。`
      : `全部 ${c.all} 题已标记通过并入库。`) + aiNote(c)));
    banner.replaceChildren(text);
    const actions = el("span", "done-actions");
    if (c.unpublished) actions.append(button(`入库（${c.unpublished} 题）`, "primary", publish, "", { iconName: "archive" }));
    else {
      const link = el("a", "button", "去正式题库看看");
      link.href = "/library";
      actions.append(link);
    }
    // Reviewing a batch: offer the next paper that still has cards to look at.
    const next = QBNotify.nextToReview(state.papers, state.paperId);
    if (next) {
      const c2 = next.counts || {};
      const todo = (c2.yellow || 0) + (c2.red || 0);
      actions.append(button(`下一份：${paperDisplayName(next)}${todo ? `（${todo} 张要看）` : ""}`,
        c.unpublished ? "" : "primary", () => selectPaper(next.id), "打开下一份还有题卡没通过的试卷"));
    }
    banner.append(actions);
  }

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
      statusText.textContent = "没有题卡";
    } else if (c.todo) {
      // What to do next, not a second copy of the counts shown in the bar and tabs.
      statusText.replaceChildren(
        document.createTextNode(`${c.all} 道题。先处理 ${c.todo} 张需核对的卡（按 `),
        el("kbd", "", "N"),
        document.createTextNode(c.green ? ` 逐张跳过去），再核对 ${c.green} 张识读一致的绿卡。` : " 逐张跳过去）。"),
      );
    } else if (c.green) {
      statusText.textContent = `${c.all} 道题：剩下 ${c.green} 张 AI 识读一致的绿卡；它们仍需按你的审核标准确认。`;
    } else {
      statusText.textContent = (c.unpublished ? `全部 ${c.all} 题已标记通过，还有 ${c.unpublished} 题没入库。` : `全部 ${c.all} 题已标记通过并入库。`) + aiNote(c);
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
    $("approveGreen").title = "绿卡只表示机器识读彼此一致。批量标记前，请确认这些题符合你的审核标准。";
    renderPublishButton(c, structureBlocked);
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
    teach({ type: "filter", key });
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
      if (filter.optional && !c[filter.key] && !active) return;
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
    if (state.filter === "todo") return needsCheck(q);
    if (state.filter === "green") return !isApproved(q) && !approvalNeedsReview(q) && !figureBlocksApproval(q) && !typeBlocksApproval(q) && q.state === "green";
    if (state.filter === "approved") return isApproved(q);
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
    bar.hidden = !state.selecting;
    $("cards").classList.toggle("selecting", state.selecting);
    $("selectionCount").textContent = count ? `已选择 ${count} 道题` : "批量删除：选择要移到回收站的题卡";
    $("selectionHint").textContent = state.selectionBusy
      ? "正在移到回收站，请稍候……"
      : "点题号左边的方框选择；Ctrl/Cmd 点击增减单题，Shift 点击选择连续范围；已入库的题不能删除，删除后可以撤销。";
    $("selectionDelete").disabled = !count || state.selectionBusy;
    $("selectionDelete").textContent = state.selectionBusy ? "正在删除…" : `移到回收站（${count}）`;
    $("selectionCancel").disabled = state.selectionBusy;
  }

  function selectQuestion(q, event = {}) {
    const reason = questionDeleteBlockReason(q);
    if (reason) { toast(reason, "error"); return false; }
    if (state.selectionBusy) { toast("正在处理上一项删除操作，请稍候", "error"); return false; }
    state.selecting = true;
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
      if (!state.questions.length) empty.append(el("strong", "", "题卡还没生成"), el("span", "", "AI 处理完会自动出现在这里。"));
      else if (state.filter === "todo") empty.append(el("strong", "", "没有需要逐题核对的卡"), el("span", "", "黄卡、红卡和内容变更都处理完了。"));
      else if (state.filter === "green") empty.append(el("strong", "", "没有待审的绿卡"), el("span", "", "识读一致的题都已标记通过。"));
      else empty.append(el("strong", "", "这一栏没有题卡"));
      container.append(empty);
    } else container.querySelectorAll(".cards-empty").forEach((node) => node.remove());
    R.fitOptions(container);
    renderSelectionState();
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
    if (!state.focus || $("paperView").hidden || anyDialogOpen() || state.selecting) return;
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
      case "o":
        event.preventDefault();
        if (event.shiftKey) toggleAllExpanded(); else toggleExpanded(q);
        break;
      case "Enter":
        if (onControl || !q) return;
        event.preventDefault();
        if (isHumanApproved(q)) toast(`第 ${q.number} 题已经是通过状态；按 U 可撤销`);
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
        if (q && isApproved(q)) { event.preventDefault(); approveQuestion(q, false); }
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
      case "f": if (q) { event.preventDefault(); openPageDialog("figures", q); } break;
      case "n": {
        event.preventDefault();
        const next = nextToReview(q, { onlyCheck: true }) || nextToReview(q);
        if (next) goTo(next); else toast("没有待审的题卡了");
        break;
      }
      case "l": event.preventDefault(); setLens(!state.lens); toast(state.lens ? "放大镜已打开" : "放大镜已关闭"); break;
      case "z": event.preventDefault(); setFocus(!state.focus); toast(state.focus ? "专注已打开：其余题暗下来" : "专注已关闭"); break;
      case "1": case "2": case "3": case "4":
        event.preventDefault(); setFilter(FILTERS[Number(key) - 1].key); break;
      case "Delete":
        if (onControl || !state.selected.size) return;
        event.preventDefault(); deleteSelectedQuestions(); break;
      case "Escape":
        if (state.selecting && !state.selectionBusy) { event.preventDefault(); stopSelecting(); }
        else if (document.documentElement.classList.contains("review-fullscreen")) { event.preventDefault(); setReviewFullscreen(false); }
        break;
      case "q":
        event.preventDefault();
        setReviewFullscreen(!document.documentElement.classList.contains("review-fullscreen"));
        break;
      case "?": event.preventDefault(); $("keysDialog").showModal(); break;
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

  function cropView(regions, { figures = [], spots = [], onZoom, capToNatural = false } = {}) {
    const wrap = el("div", "crop");
    if (!regions.length) {
      wrap.append(el("p", "crop-missing", "这道题还没有原卷范围。点右边的“调整范围”，在原卷上把它框出来，AI 会自动读题。"));
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
    crop.replaceChildren(cropView(regions, { figures: q.figures || [], spots: q.check_spots }));
    const images = [...crop.querySelectorAll("img")];
    const imageStates = new Map(images.map((image) => [image, "pending"]));
    const syncImages = () => {
      if (images.length && !crop.contains(images[0])) return;
      const values = [...imageStates.values()];
      viewer.imageReady = values.includes("ready");
      const failed = values.includes("failed");
      const status = $("viewerImageState");
      status.hidden = !images.length || (viewer.imageReady && !failed);
      status.classList.toggle("image-error", failed);
      status.textContent = failed ? (viewer.imageReady ? "部分原卷图片没能加载，关闭窗口后重试。" : "原卷图片没能加载，关闭窗口后重试。") : "正在加载原卷…";
      if (!viewer.imageReady) stopViewerSourcePan();
      syncViewerZoom();
      if (viewer.imageReady && viewer.mode === "fit" && $("viewerDialog").open) requestViewerFit();
    };
    images.forEach((image) => {
      const finish = (result) => { imageStates.set(image, result); syncImages(); };
      image.addEventListener("load", () => finish("ready"));
      image.addEventListener("error", () => finish("failed"));
      if (image.complete) imageStates.set(image, image.naturalWidth ? "ready" : "failed");
    });
    syncImages();
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
      approve.replaceChildren(document.createTextNode("撤销通过"));
      approve.className = "button";
      approve.disabled = false;
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
    if (isHumanApproved(q)) { await approveQuestion(q, false, { advance: false }); return; }
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
    const key = event.key;
    if (["ArrowLeft", "k", "K"].includes(key)) { event.preventDefault(); viewerStep(-1); }
    else if (["ArrowRight", "j", "J"].includes(key)) { event.preventDefault(); viewerStep(1); }
    else if (key === "Enter") { event.preventDefault(); viewerApprove(); }
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
      if (q) openPageDialog(key.toLowerCase() === "r" ? "regions" : "figures", q);
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
      if (!/两次识读不一致.*请看标黄/.test(String(flag))) return flag;
      if (difference.hasVisibleMarks) return "两次识读不一致，已由第三次识读裁决；请核对题面中标黄的位置";
      if (difference.observedOnly.length) return "两次识读不一致；当前稿没有可标黄的文字，另一读法多出的内容见下方";
      return "两次识读曾有出入；当前题面只剩排版或公式写法差异，可展开原始读法核对";
    });
    if (!flags.length && !q.error) return null;
    const list = el("ul", "flags");
    if (q.error) list.append(el("li", "", q.error));
    flags.forEach((flag) => list.append(flagItem(flag)));
    return list;
  }

  // “…当销售【单】价为1…（MinerU：定）”: the disputed characters stand out.
  // 1.10.2: the spots of “MinerU 读法不同” are numbered ①② like their boxes on the crop.
  const SPOT_FLAG = /^((?:两次识读一致|第三次识读裁决后)[^：]*：)([\s\S]*)$/;

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
    if (q.state === "green") {
      const copy = {
        majority: ["AI 三读多数一致 · 未人工审核", "前两次 AI 识读不同，第三次与其中一次相同；仍需人工对照原卷。"],
        human: ["已人工修改 · 未人工审核", "题面经过人工修改，但当前版本尚未标记通过。"],
        assistant: ["AI 助手改过 · 未人工审核", "AI 助手（tiyouju 命令行）对照原卷改过题面；请再对照原卷核对。"],
        mineru: ["MinerU 初稿 · 未核对", "题面是 MinerU 自己识别的文字，还没有看图核对（AI 助手读题模式）。"],
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
    panel.append(el("strong", "reading-difference-title", "另一次识读在下面标出的位置多读了文字，当前题面没有，请对照原卷"));
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
    card.setAttribute("aria-selected", String(state.selected.has(q.id)));
    card.addEventListener("pointerdown", () => { if (state.current !== q.id) setCurrent(q.id); });
    card.addEventListener("click", (event) => handleCardSelectionClick(event, q));

    if (approvedCompact) {
      const row = el("div", "compact-row");
      row.append(approvalTick(q), cardSelectionControl(q), el("span", "qnum", questionLabel(q)), stateChip(q));
      const preview = el("span", "compact-text");
      R.renderTypeset(preview, firstLine(q.stem));
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
    sticky.append(cropView(q.regions, { figures: q.figures, spots: q.check_spots, onZoom: () => openViewer(q), capToNatural: true }));
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
    head.append(approvalTick(q), cardSelectionControl(q));
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

    const figurePanel = figureReviewPanel(q);
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
    if (q.stem) R.renderQuestion(rendered, content(q), { showNumber: false, marks: reviewMarks(q), showAnswer: "collapsed",
      figureAction: (figure) => tableAction(q, figure) });
    else rendered.append(el("p", "hint", "还没有题面"));
    body.append(rendered);

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
      button("调整范围", q.regions.length ? "" : "primary", () => openPageDialog("regions", q), "截图框多了或少了，拖一下；保存后 AI 自动重读（R）"),
      button("配图", "", () => openPageDialog("figures", q), "增删配图，或调整配图的裁剪框（F）", { iconName: "image" }),
      button("框选识读", "", () => openPageDialog("read", q), "在原卷上框出一小块（比如被手写盖住的选项），让 AI 单独读这一块")
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
    card.append(head, source, body);
    if (approved && !isAiApproved(q)) card.append(expandToggle(q, false));
    return card;
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
    const labels = { a: "读法甲", b: "读法乙", c: "裁决" };
    Object.entries(q.reads).forEach(([key, reading]) => {
      if (!reading || (!reading.stem && !reading.error && !reading.witness)) return;
      const item = el("div", "read");
      const chosen = reading.chosen === "a" ? "读法甲" : "读法乙";
      const witnessNote = key === "c" ? `两种读法的分歧逐处对照这段文字，采用${chosen}，未再调用裁决模型`
        : "与读法甲逐字一致，未再调用复核模型";
      const objections = Array.isArray(reading.objections) ? reading.objections : [];
      let label = `${labels[key]}${reading.engine ? ` · ${reading.engine}` : ""}`;
      if (objections.length) {
        label = `逐处核对${reading.engine ? ` · ${reading.engine}` : ""}（两次读法一致，但 MinerU 有 ${objections.length} 处不同，每处只问“原卷印的是哪一个”）`;
      } else if (reading.witness) label = `旁证 · MinerU 自己识别的文字（${witnessNote}）`;
      item.append(el("p", "read-label", label));
      if (objections.length) {
        const verdicts = { reading: "再看：与读法一致", mineru: "再看：像 MinerU 的写法", null: "再看：不确定" };
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
        const left = c.todo + c.green;
        toast(confirming ? `已确认第 ${q.number} 题（原来是 ${agentLabel(q)} 通过）`
          : left ? `第 ${q.number} 题已标记通过` : "本卷已全部标记通过，可以点“入库”了",
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
        stopSelecting({ render: false });
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
    if ($("selectionStart")) {
      $("selectionStart").hidden = !state.paper;
      $("selectionStart").disabled = !ready;
      $("selectionStart").title = ready ? "选择多张题卡一起移到回收站；已入库的题不能删除" : "任务处理完成后才能删除题卡";
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

  // 停止处理（1.10.8）：排队中或在等 MinerU 的任务停下来，才能删除。
  async function stopPaper() {
    if (!state.paper || !["queued", "parsing"].includes(state.paper.status)) return;
    const ok = await confirmDialog({
      title: "停止处理这份任务？",
      text: "题有据不再等 MinerU。停下来以后，可以在“试卷操作”里永久删除，或点“重试”重新开始。",
      ok: "停止处理", danger: true
    });
    if (!ok) return;
    try {
      const data = await api(`/api/papers/${state.paperId}/stop`, { method: "POST", body: {} });
      toast(data.message || "已停止", "success");
      refreshPaper();
      loadPapers();
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
    renderProviderModelSetting(engines, choices, "modelscope", "settingsModelscopeModel",
      "settingsModelscopeModels", "settingsModelscopeModelState");
    const assistant = $("settingsPrimaryModel").value === "assistant";
    $("settingsCheckerModel").disabled = assistant;
    $("settingsArbiterModel").disabled = assistant;
    // 模型名前面写上是哪一家：只看“Qwen3.5-35B-A3B”，老师认不出是谁家的。
    const named = (key, label) => [choices.find((choice) => choice.key === key)?.provider, label].filter(Boolean).join(" ");
    const summary = [
      status.reader && `主读 ${named(engines.primary, status.reader)}`,
      status.checker && `复核 ${named(engines.checker, status.checker)}`,
      status.arbiter && `裁决 ${named(engines.arbiter, status.arbiter)}`
    ].filter(Boolean).join("；");
    $("settingsModelSummary").textContent = status.assistant_mode
      ? "现在是 AI 助手读题：新资料的题卡先用 MinerU 的文字，需要 AI 助手或你对照原卷截图核对。"
      : summary ? `现在实际使用：${summary}。` : "现在没有可用的读题模型：请先在“常用”里填写密钥（魔搭有免费的），或选“AI 助手读题”。";
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
    const plan = engines.saved?.plans?.minimax || engines.plans?.minimax || "auto";
    select.value = PLAN_NOTES[plan] ? plan : "auto";
    const pending = engines.plans?.minimax && engines.plans.minimax !== select.value;
    $("settingsMinimaxPlanNote").textContent = `${PLAN_NOTES[select.value]}遇到限流会自动放慢，不会出错。${
      pending ? "改动从下一份新资料开始生效。" : ""}`;
  }

  const CREDENTIAL_FIELDS = {
    mineru: { input: "credentialMineruInput", clear: "credentialMineruClear", state: "credentialMineruState", label: "MinerU" },
    modelscope: { input: "credentialModelscopeInput", clear: "credentialModelscopeClear", state: "credentialModelscopeState", label: "魔搭" },
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
      node.textContent = status.configured ? (count > 1 ? `已保存 ${count} 个账号` : "已保存") : "未填写";
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
    const stoppable = paper.status === "queued" || (paper.status === "parsing" && !paper.processing?.chunks);
    $("settingsStop").hidden = !stoppable;
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

  // 设置按分页显示，一次只看一块；打开时停在“常用”，读题服务不能用时那里会直接说明。
  function showSettingsTab(id) {
    document.querySelectorAll("[data-settings-tab]").forEach((tab) => {
      const active = tab.dataset.settingsTab === id;
      tab.setAttribute("aria-selected", String(active));
      tab.tabIndex = active ? 0 : -1;
    });
    document.querySelectorAll("#settingsDialog .settings-page").forEach((page) => { page.hidden = page.id !== id; });
    const scroller = document.querySelector("#settingsDialog .settings-scroll");
    if (scroller) scroller.scrollTop = 0;
  }

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
    const missing = [!s.mineru && "MinerU", !vision && !s.assistant_mode && "一家看图读题服务（魔搭免费）"].filter(Boolean);
    node.className = `settings-ready ${s.upload_enabled ? "ready" : "missing"}`;
    node.textContent = s.upload_enabled
      ? (s.assistant_mode ? "MinerU 密钥已配置：AI 助手读题，题卡先用 MinerU 的文字。服务当前是否可用，以任务返回为准。"
        : "所需密钥已配置，可以提交新资料。服务当前是否可用，以任务返回为准。")
      : missing.length ? `还不能上传新资料：请先填写 ${missing.join("、")} 的密钥。已有的题卡照常可以审核。下面有免费的配法。`
        : "所选的主读模型还没有密钥，暂时不能上传新资料；可以在“读题模型”里换一个已填写密钥的模型。";
    // 只在“能不能上传”变了的时候自动展开或收起，不跟用户自己的开合较劲。
    if (state.freePlanReady !== s.upload_enabled) {
      state.freePlanReady = s.upload_enabled;
      $("settingsFreePlan").open = !s.upload_enabled;
    }
  }

  // 设置 → 常用 → 功能开关（存在数据目录的 features.json，网页和后台共用）。
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
    (data.features || []).filter((item) => !["knowledge_tags", "ai_answer"].includes(item.key)).forEach((item) => {
      const label = el("label", "settings-switch");
      const text = el("span");
      text.append(el("strong", "", item.label), el("small", "", item.help));
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
      box.append(label);
    });
    const note = $("featureNote");
    const tags = (data.features || []).find((item) => item.key === "knowledge_tags");
    note.hidden = !(tags?.enabled && data.knowledge_file);
    if (!note.hidden) note.textContent = `知识点目录在 ${data.knowledge_file}，可以用记事本改：每行一个知识点，# 开头的是章名。`;
  }

  function openSettings() {
    void loadFeatureSwitches();
    renderSettingsModels();
    renderSettingsReady();
    $("settingsLens").checked = state.lens;
    $("settingsFocus").checked = state.focus;
    $("settingsAutoExpand").checked = state.autoExpand;
    if (!modelFormDirty) showModelSaveResult("");
    showSettingsTab("settingsGeneral");
    $("settingsDialog").showModal();
    void loadStatus();
    requestAnimationFrame(() => $("settingsClose").focus());
  }

  $("settingsButton").addEventListener("click", (event) => {
    if (window.location.pathname === "/settings") { event.preventDefault(); openSettings(); }
  });
  $("reopenSettings").addEventListener("click", openSettings);
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
  $("renameNudge").addEventListener("click", openRenameDialog);
  $("settingsConfirmStructure").addEventListener("click", () => closeSettingsThen(confirmStructure));
  $("settingsSplit").addEventListener("click", () => closeSettingsThen(openSplitDialog));
  $("settingsArchive").addEventListener("click", () => closeSettingsThen(archivePaper));
  $("settingsDelete").addEventListener("click", () => closeSettingsThen(deletePaper));
  $("questionTrash").addEventListener("click", openQuestionTrash);
  $("selectionCancel").addEventListener("click", () => stopSelecting());
  $("selectionStart").addEventListener("click", () => { $("toolsMenu").open = false; startSelecting(); });
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
    $("credentialResult").textContent = "正在检查填写格式并加密保存…";
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
      checker: $("settingsCheckerModel").value,
      arbiter: $("settingsArbiterModel").value,
      models: {
        minimax: $("settingsMinimaxModel").value.trim(),
        siliconflow: $("settingsSiliconflowModel").value.trim(),
        modelscope: $("settingsModelscopeModel").value.trim()
      },
      plans: { minimax: $("settingsMinimaxPlan").value }
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

  function saveModelSettings() {
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
    modelSaving = modelSaving.then(() => saveModelSettingsNow(change), () => saveModelSettingsNow(change));
    return modelSaving;
  }

  const modelSettingFields = ["settingsPrimaryModel", "settingsCheckerModel", "settingsArbiterModel", "settingsMinimaxModel",
    "settingsSiliconflowModel", "settingsModelscopeModel", "settingsMinimaxPlan"];
  modelSettingFields.forEach((id) => $(id).addEventListener("input", () => {
    modelFormDirty = true;
    showModelSaveResult("有改动待保存；离开输入框或关闭设置后会自动保存。", { retry: Boolean(lastModelSave?.failed) });
  }));
  modelSettingFields
    .forEach((id) => $(id).addEventListener("change", () => { void saveModelSettings(); }));
  $("modelSettingsForm").addEventListener("submit", (event) => {
    event.preventDefault();
    void saveModelSettings();
  });

  function saveModelDraftOnClose() {
    if (!modelFormDirty) return;
    const signature = JSON.stringify(readModelSettings());
    if (lastModelSave?.signature !== signature) void saveModelSettings();
  }
  $("settingsDialog").addEventListener("cancel", saveModelDraftOnClose);
  $("settingsDialog").addEventListener("close", saveModelDraftOnClose);

  async function saveModelSettingsNow(change) {
    try {
      await api("/api/settings/models", { method: "POST", body: change.body });
      change.succeeded = true;
      if (change !== lastModelSave) return;
      // An uncommitted text edit is also a newer intent. A completed save must
      // not replace it with the older value before blur/change submits it.
      if (JSON.stringify(readModelSettings()) !== change.signature) {
        showModelSaveResult("有新改动待保存；离开输入框或关闭设置后会自动保存。");
        return;
      }
      modelFormDirty = false;
      const message = MODEL_SAVED_MESSAGE;
      showModelSaveResult(message);
      toast(message, "success");
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
      teach({ type: "approveGreen" });
      refreshPaper();
    } catch (error) { toast(error.message, "error"); }
  });

  function renderPublishButton(c = counts(), structureBlocked = state.paper?.status === "needs_grouping") {
    const button = $("publishButton");
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
        text: "示例试卷只用来练习，不会进入正式题库。你自己的试卷核对完，点“入库”，题目就进了正式题库，可以搜索、组卷。",
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

  function openEditor(card, q, { prefill = null } = {}) {
    if (card.querySelector(".editor")) {
      if (prefill) toast("先保存或取消正在改的字，再填入框选识读的结果", "error");
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
    const positionHint = el("p", "editor-position-hint", "点击输入框，预览会标出正在修改的位置。");
    previewBox.append(el("p", "preview-label", "预览 · 随输入实时更新"), positionHint, preview);
    const saveButton = el("button", "button primary", "保存");
    saveButton.type = "submit";
    const cancel = button("取消", "", () => discardEdits([q.id]));
    const bar = el("div", "editor-actions");
    bar.append(saveButton, cancel, el("p", "hint", "Ctrl+Enter 保存 · Esc 取消"));
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
        R.renderQuestion(preview, { ...content(q), ...data, options: optionBox.hidden ? {} : data.options }, { showNumber: false, showAnswer: "open", trackSource: true });
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
    stem.addEventListener("input", fitStem);
    window.addEventListener("resize", relayout);
    editor.addEventListener("keydown", (event) => {
      if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); discardEdits([q.id]); }
      else if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) { event.preventDefault(); editor.requestSubmit(); }
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

  function regionReadPending(q) {
    return Boolean(q.region_read && ["queued", "running"].includes(q.region_read.status));
  }

  async function dismissRegionRead(q) {
    try {
      applyQuestion(await api(`/api/questions/${q.id}/region-read`, { method: "DELETE", body: {} }));
    } catch (error) { toast(error.message, "error"); }
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
    // A piece of the stem: copy it, the person pastes it where it belongs.
    openEditor(card, q);
    const copied = navigator.clipboard?.writeText
      ? navigator.clipboard.writeText(read.text)
      : Promise.reject(new Error("clipboard unavailable"));
    copied.then(
      () => toast("读出来的字已复制，粘贴到题干里要改的位置", "success"),
      () => toast("没能复制，请从题卡上的识读结果里手动复制", "error"));
  }

  function regionReadPanel(card, q) {
    const read = q.region_read;
    if (!read) return null;
    const panel = el("section", `region-read ${read.status}`);
    const head = el("div", "region-read-head");
    head.append(el("strong", "", `框选识读 · ${read.target_name || read.target}`));
    panel.append(head);
    if (regionReadPending(q)) {
      panel.append(el("p", "hint", "AI 正在读框里的字……"));
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
        const manual = el("details", "region-manual-target");
        manual.open = !prefill;
        manual.append(el("summary", "", prefill ? "改选其他位置" : "手动选择替换位置"));
        const select = el("select");
        select.setAttribute("aria-label", "手动选择替换位置");
        [["stem", "题干（复制后自行粘贴）"], ...OPTION_KEYS.map((key) => [key, `选项 ${key}`])].forEach(([value, label]) => {
          const option = el("option", "", label);
          option.value = value;
          select.append(option);
        });
        const fill = button("复制并打开改字", "small", () => fillRegionRead(card, q, select.value));
        select.addEventListener("change", () => {
          fill.textContent = select.value === "stem" ? "复制并打开改字" : `填入选项 ${select.value}（打开改字）`;
        });
        manual.append(select, fill, el("p", "hint", "手动选择选项会替换整个选项；题干文字先复制，再粘贴到你选的位置。填入后仍需保存。"));
        suggestion.append(manual);
        panel.append(suggestion);
      } else {
        const label = read.target === "stem" ? "复制并打开改字" : `填入${read.target_name || read.target}（打开改字）`;
        actions.append(button(label, "small primary", () => fillRegionRead(card, q)));
      }
    }
    actions.append(button("重新框", "small", () => openPageDialog("read", q)),
      button(regionReadPending(q) ? "不读了" : "关闭", "small quiet", () => dismissRegionRead(q)));
    panel.append(actions);
    return panel;
  }

  // ---------------------------------------------------------------- 原卷页面上拖框（调整范围 / 配图 / 补一题）

  const dialog = {
    mode: null, question: null, boxes: [], page: 0, drag: null,
    zoom: 1, zoomMode: "width", zoomFrame: 0, spacePan: false, imageReady: false,
    slotTarget: null, slotAnchor: null, pendingFigure: null, ignoredCandidates: new Set()
  };

  function openPageDialog(mode, q = null, { page: requestedPage = null } = {}) {
    if (!state.paper?.pages?.length) { toast("原卷页面尚未生成", "error"); return; }
    dialog.mode = mode;
    dialog.zoom = 1;
    dialog.zoomMode = mode === "view" ? "fit" : "width";
    dialog.spacePan = false;
    $("pageStage").classList.toggle("read-only", mode === "view");
    $("pageStage").classList.remove("pan-ready");
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
      && state.paper.pages.some((page) => page.page_idx === requestedPage) ? requestedPage : firstPage;
    dialog.scrolled = false;
    dialog.selected = null;
    $("pageStage").scrollTo({ top: 0, left: 0 });
    dialog.ignoredCandidates = new Set(Array.isArray(q?.figure_review?.ignored_candidates)
      ? q.figure_review.ignored_candidates.filter((key) => hasFigureCandidateKey(q, key)) : []);
    $("pageDialogTitle").textContent = mode === "regions" ? `调整第 ${q.number} 题的原卷范围`
      : mode === "figures" ? `第 ${q.number} 题的配图` : mode === "read" ? `第 ${q.number} 题 · 框选识读`
        : mode === "view" ? "查看整份原卷" : "手动补一道题";
    $("pageDialogHint").textContent = mode === "regions"
      ? "拖边角改大小 · 拖框内部移动 · 空白处拖出新框补上跨栏/跨页部分 · 选中后方向键微调、Delete 删除 · 保存后 AI 按新范围重读"
      : mode === "figures"
        ? "点蓝色候选图或画新框，再选归属（S 题干 · A–E 选项 · X 无关 · J 接在上一张图下面，用于被分页切开的表格或图）· 点标签改归属，拖标签只挪标签 · 保存后需重新审核"
        : mode === "read"
          ? "框住要单独识读的印刷字。选择“自动推荐（AI）”时，AI 会结合现有题面建议替换位置；也可以指定题干或选项。读完先看替换前后，再确认填入改字。"
          : mode === "view" ? "查看完整原卷；这里不会修改题卡或重新识读。"
            : "在原卷上拖出这道题的范围（跨栏就拖两个框），填上题号后保存，AI 会自动读题。";
    // The offline practice can demonstrate drawing and the real target picker,
    // but must not dispatch recognition or invent an AI recommendation.
    dialog.practiceRead = mode === "read" && Boolean(state.paper.demo);
    $("pageDialogSave").hidden = mode === "view";
    $("pageDialogSave").disabled = dialog.practiceRead;
    $("pageDialogSave").title = dialog.practiceRead ? "示例只练画框；真实识读需要可用的读题服务" : "";
    if (dialog.practiceRead) $("pageDialogHint").textContent = "离线示例：可练画框和选择自动推荐，不会调用 AI 或生成识读结果。真实题目识读后，先确认替换前后，再填入改字、核对保存。";
    $("pageDialogClose").textContent = mode === "view" ? "关闭" : "取消";
    $("pageCanvasHint").textContent = mode === "view"
      ? "Ctrl+滚轮缩放 · 按住鼠标左键拖动画布"
      : "Ctrl+滚轮缩放 · 空格+左键或中键拖画布 · 左键仍然画框";
    $("numberField").hidden = mode !== "new";
    $("readTargetField").hidden = mode !== "read";
    $("pageDialogSave").textContent = mode === "read" ? "识读这一块" : "保存";
    if (mode === "read") $("readTargetSelect").value = readTargetGuess(q);
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
    requestPageZoom();
    if (mode !== "new") $("pageStage").focus({ preventScroll: true });
  }

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
    dialog.zoomFrame = requestAnimationFrame(() => {
      dialog.zoomFrame = requestAnimationFrame(() => {
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
    if (dialog.drag || !dialog.imageReady) return;
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
    && (event.button === 1 || (event.button === 0 && (dialog.mode === "view" || dialog.spacePan))), $("pageDialog"));
  const clearPagePanKey = () => {
    dialog.spacePan = false;
    $("pageStage").classList.remove("pan-ready");
  };
  document.addEventListener("keydown", (event) => {
    if (!$("pageDialog").open || event.ctrlKey || event.metaKey || event.altKey) return;
    if (event.target.closest?.("input, textarea, select, button, summary, [contenteditable='true']")) return;
    if (event.key === " ") {
      event.preventDefault();
      dialog.spacePan = true;
      $("pageStage").classList.add("pan-ready");
    } else if (["+", "="].includes(event.key)) { event.preventDefault(); zoomPageBy(1.25); }
    else if (["-", "_"].includes(event.key)) { event.preventDefault(); zoomPageBy(0.8); }
    else if (event.key === "0") { event.preventDefault(); requestPageZoom("fit"); }
    else if (event.key.toLowerCase() === "w") { event.preventDefault(); requestPageZoom("width"); }
  });
  document.addEventListener("keyup", (event) => { if (event.key === " ") clearPagePanKey(); });
  window.addEventListener("blur", clearPagePanKey);

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
    stopPageCanvasPan();
    if (dialog.drag) dialog.drag();
    dialog.page = pageIdx;
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
    const existing = target.kind === "existing" ? dialog.boxes[target.index] : null;
    const current = existing ? (existing.join && joinHost(existing) ? "join" : existing.slot) : null;
    menu.querySelectorAll("[data-figure-slot]").forEach((item) => {
      item.setAttribute("aria-checked", String(item.dataset.figureSlot === current));
    });
    menu.hidden = false;
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
      closeFigureSlotMenu({ cancelPending: false, rerender: false });
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
      closeFigureSlotMenu({ cancelPending: false, rerender: false });
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
    if (event.key === "Escape") {
      event.preventDefault();
      closeFigureSlotMenu({ cancelPending: true, rerender: true });
      return;
    }
    const slot = !event.ctrlKey && !event.metaKey && !event.altKey && SLOT_KEYS[event.key.toLowerCase()];
    if (!slot) return;
    event.preventDefault();
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
      anchorCanvasZoom($("pageStage"), surface, applyPageZoom);
    }
    if ($("pageDialog").open && dialog.mode === "figures" && surface) autoPlaceFigureLabels(surface);
    else if (menuIsOpen() && dialog.slotAnchor?.isConnected) positionFigureSlotMenu(dialog.slotAnchor);
  });
  $("pageStage").addEventListener("scroll", () => {
    if (menuIsOpen() && dialog.slotAnchor?.isConnected) positionFigureSlotMenu(dialog.slotAnchor);
  }, { passive: true });
  $("pageDialog").addEventListener("close", () => {
    if (dialog.zoomFrame) cancelAnimationFrame(dialog.zoomFrame);
    dialog.zoomFrame = 0;
    clearPagePanKey();
    if (dialog.drag) dialog.drag();
    closeFigureSlotMenu({ cancelPending: true, rerender: false });
  });

  function renderStage() {
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
    surface.style.aspectRatio = `${page.width} / ${page.height}`;
    const image = el("img");
    image.src = previewUrl(state.paperId, dialog.page);
    image.alt = `原卷第 ${dialog.page + 1} 页`;
    image.draggable = false;
    image.addEventListener("load", () => {
      if (!surface.isConnected || !$("pageDialog").open) return;
      dialog.imageReady = true;
      $("pageImageState").hidden = true;
      applyPageZoom();
    }, { once: true });
    image.addEventListener("error", () => {
      if (!surface.isConnected || !$("pageDialog").open) return;
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

  function startDrag(event, surface, index, handle) {
    if (event.button !== 0 || dialog.mode === "view" || dialog.spacePan || !dialog.imageReady) return;
    $("pageStage").focus({ preventScroll: true });
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
          dialog.boxes.push({ page_idx: dialog.page, bbox });
          renderPageTabs();
        }
        preview.remove();
      }
      renderStage();
    };
    const cancel = () => {
      finish();
      if (box) { box.bbox = original; if (target.isConnected) placeBox(target, original); }
      preview?.remove();
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

  function readingOrder(boxes) { return QBFigureJoin.readingOrder(boxes); }

  $("pageDialogSave").addEventListener("click", async () => {
    if (dialog.mode === "view" || dialog.practiceRead) return;
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
        applyQuestion(data);
        toast(q.approved ? `第 ${q.number} 题配图已保存，旧审批已撤销，请重新审核` : `第 ${q.number} 题配图已保存，请审核题卡`);
        teach({ type: "figures", number: q.number, figures: (data.question?.figures || []).length });
      } else if (dialog.mode === "read") {
        const box = dialog.boxes[dialog.boxes.length - 1];
        if (!box) { toast("请先在原卷上框出要识读的那一块", "error"); return; }
        const target = $("readTargetSelect").value;
        const data = await api(`/api/questions/${q.id}/region-read`, {
          method: "POST", body: { page_idx: box.page_idx, bbox: box.bbox, target }
        });
        applyQuestion(data);
        toast(`AI 正在读框里的字，读完显示在第 ${q.number} 题的题卡上`);
        refreshPaper();
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
        text: "上传新卷会把整份原卷发送给 MinerU，切出的题目截图还会发送给你填了密钥的看图读题服务（魔搭、MiniMax、硅基流动）；选了“AI 助手读题”时，截图由你让它操作的 AI 助手读取。系统不会先擦除姓名、手写或批改痕迹。\n\n请确认你有权按此方式处理这些卷面，再继续上传。",
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
      $("welcomeKeys").hidden = true;
      return;
    }
    node.className = `settings-ready ${s.upload_enabled ? "ready" : "missing"}`;
    node.textContent = s.upload_enabled ? "读题服务已经准备好，可以直接上传。"
      : "开始前还要填一次读题服务的密钥：MinerU，以及一家看图读题服务。都有免费的（魔搭），在“设置”里有申请网址。";
    $("welcomeKeys").hidden = Boolean(s.upload_enabled);
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
  $("welcomeKeys").addEventListener("click", () => { finishWelcome(); openCredentialSettings(); });
  $("welcomeTour").addEventListener("click", () => { finishWelcome(); requestAnimationFrame(startTour); });
  $("settingsWelcome").addEventListener("click", openWelcome);

  // 界面导览：把要讲的地方圈亮，旁边一张小卡片说明；找不到的地方（例如还没有题卡）就跳过。
  const TOUR_STEPS = [
    { target: () => $("dropZone"), title: "上传资料",
      text: "把 PDF、Word 或手机拍的照片拖进窗口任意位置，或点这里选文件。上传前先在下面选“一份试卷”还是“一本书 / 讲义”。" },
    { target: () => $("paperList"), title: "试卷列表",
      text: "上传后 AI 会自动切题、读题。每份资料读到哪一步、还有几张要看，都写在这里；点一份就打开它的题卡。" },
    { target: () => firstTourCard(), title: "题卡",
      text: "一道题一张卡。左边原卷截图点一下放大：Ctrl＋滚轮缩放，左键拖动；右边是题面。黄色标出几次读法不一样的地方，重点核对。" },
    { target: () => firstTourCard()?.querySelector(".card-tick"), title: "对了就打勾",
      text: "对照原卷没问题，就在题号左边的方框打勾（或按 Enter），会自动跳到下一张要看的题。点错了再点一下就撤销。" },
    { target: () => firstTourCard()?.querySelector(".card-actions"), title: "不对就改",
      text: "字错了点“改字”，预览会对应输入光标和选区；图不对点“配图”。“框选识读”可只重读一小块并推荐替换位置，先确认再保存；识读需要服务可用。" },
    { target: () => $("paperMenu").querySelector("summary"), title: "看整份原卷",
      text: "点“试卷操作 → 查看整份原卷”。Ctrl＋滚轮缩放，左键拖动，还能适页、适宽和跳页。画框编辑时用空格＋左键或中键拖动。" },
    { target: () => $("filters"), title: "先看有疑点的",
      text: "“需逐题核对”只列出有疑点的题卡，先处理它们；“识读一致”是几次读法完全相同的，也要看一眼再打勾。" },
    { target: () => document.querySelector("#toolbar .tool-group"), title: "专注和全屏",
      text: "“专注”让正在看的题亮着、其余题暗下来；“全屏”收起顶栏和试卷列表，只留题卡。" },
    { target: () => $("publishButton"), title: "入库",
      text: "核对完的题点“入库”，就进了正式题库。已经入库的题再改，会提示你重新入库，旧版本也会留着。" },
    { target: () => document.querySelector('.topnav a[href="/library"]'), title: "正式题库",
      text: "按试卷、题型、关键词找题，选好就能组卷。每道题可“查看出处”，对照原卷；“版本历史”看修改前后，“相关资料”查其他来源。" },
    { target: () => $("settingsButton"), title: "设置",
      text: "设置 → 帮助里可做离线基础练习、只看新版功能，或查看未保存提醒的说明；改字有未保存内容时，离开前会提示继续编辑或丢弃。" }
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

  // 教学里的“指给我看”：圈亮一个地方，旁边写一句；不挡操作，点哪里都行。
  function pointAt(node, title, text) {
    if (!tourVisible(node)) return false;
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
    session: 0, moving: false, migrated: false };

  function cancelTeachingAdvance() {
    clearTimeout(teaching.timer);
    teaching.timer = null;
    teaching.pending = null;
  }

  function saveTeaching() {
    const lesson = QBTeach.LESSONS[teaching.index];
    writePref(TEACH_KEY, teaching.active && lesson ? JSON.stringify({
      paper: teaching.paper, version: QBTeach.VERSION, lesson: lesson.key,
      completed: teaching.completed === lesson.key
    }) : "");
  }

  function loadTeaching() {
    try {
      const saved = JSON.parse(readPref(TEACH_KEY, "") || "null");
      const restored = QBTeach.restore(saved);
      if (restored) { Object.assign(teaching, restored); saveTeaching(); }
    } catch { /* 没有进度就不显示 */ }
  }

  async function startTeaching({ reset = false, review = false } = {}) {
    if (window.location.pathname === "/settings") {
      await leaveFor(`/?learn=${review ? "new" : "all"}`);
      return;
    }
    if (!(await discardEdits())) return;
    closeSettingsThen(() => {});
    if ($("welcomeDialog").open) finishWelcome();
    try {
      const data = await api("/api/demo", { method: "POST", body: { reset } });
      await loadPapers();
      await selectPaper(data.paper.id);
      if (state.paperId !== data.paper.id) return;
      cancelTeachingAdvance();
      endTour();
      Object.assign(teaching, { paper: data.paper.id, index: review ? QBTeach.indexOf("original") : 0,
        active: true, session: teaching.session + 1, migrated: false, completed: null });
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
    $("teachShow").textContent = lesson.key === "recovery" ? "查看帮助" : "指给我看";
    $("teachSkip").hidden = Boolean(lesson.manual && !lesson.checkpoint);
    $("teachSkip").textContent = lesson.checkpoint ? "先结束练习" : "跳过这一步";
    $("teachNext").hidden = !lesson.manual && teaching.completed !== lesson.key;
    $("teachNext").textContent = lesson.final ? "完成" : lesson.checkpoint ? "看新版功能"
      : teaching.completed === lesson.key ? "继续下一步" : "下一步";
    if (teaching.pending === lesson.key) {
      $("teachShow").hidden = true;
      $("teachSkip").hidden = true;
    }
  }

  async function closeTeachingSurface() {
    if ($("confirmDialog").open) return false;
    const key = QBTeach.LESSONS[teaching.index]?.key;
    if (["preview", "recovery", "fix"].includes(key) && !(await discardEdits())) return false;
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
      teaching.index += 1;
      if (teaching.index >= QBTeach.LESSONS.length) { exitTeaching(); return; }
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
      teaching.timer = setTimeout(complete, 1100);
      return;
    }
    const hint = QBTeach.lessonHint(lesson.key, event);
    if (hint) renderTeach(hint);
  }

  // “指给我看”：先把要用的那张卡找出来（必要时切回“全部”），再圈亮要点的地方。
  async function showLesson() {
    const lesson = QBTeach.LESSONS[teaching.index];
    if (!lesson) return;
    if (teaching.moving || $("confirmDialog").open) return;
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
      case "card": focusCard(1); later(() => cardFor(1), "第 1 题", "左边是原卷截图，右边是读出来的题面。"); break;
      case "viewer": {
        focusCard(1);
        if (!$("viewerDialog").open && lessonNumber(1)) openViewer(lessonNumber(1));
        later(() => $("viewerSource"), "在原卷上操作", "Ctrl＋滚轮放大，按住左键拖动；看完点教学的“下一步”。");
        break;
      }
      case "tick": focusCard(1); later(() => cardFor(1)?.querySelector(".card-tick"), "点这个方框", "对了就打勾，标记通过。"); break;
      case "todo": later(() => $("filter-todo"), "点这里", "只看有疑点的题卡。"); break;
      case "fix":
      case "preview": {
        focusCard(9);
        const card = cardFor(9);
        if (card && lessonNumber(9)) openEditor(card, lessonNumber(9));
        later(() => cardFor(9)?.querySelector(".stem-input"), "在这里改字或选字",
          lesson.key === "fix" ? "把“3 个单位”改成“5 个单位”。预览同步标位置；核对后保存。" : "点一个位置，或选中几个字，看预览如何对应。试完下一步；有改动会先提醒保存或丢弃。");
        break;
      }
      case "tick9": focusCard(9); later(() => cardFor(9)?.querySelector(".card-tick"), "点这个方框", "改好了就打勾。"); break;
      case "figure": focusCard(2); later(() => cardFor(2)?.querySelector(".figure-review .button.primary")
        || cardFor(2)?.querySelector(".figure-review") || cardFor(2),
        "点“补选配图”", "在原卷上点蓝色的候选图，选“题干”，再点“保存”。"); break;
      case "table": focusCard(3); later(() => cardFor(3)?.querySelector(".qb-table") || cardFor(3),
        "对照这张表", "逐格看一遍，没问题就给第 3 题打勾。"); break;
      case "green": later(() => $("approveGreen"), "点这里", "把剩下的绿卡一起标记通过。"); break;
      case "publish": later(() => $("publishButton"), "点“入库”", "示例试卷不会真的入库。"); break;
      case "basics": later(() => $("settingsButton"), "以后从这里继续", "设置 → 帮助：可以重做基础练习，也可以只看新版功能。"); break;
      case "original": {
        if (!$("pageDialog").open) openPageDialog("view");
        later(() => $("pageZoomFit"), "试试整份原卷", "适页、适宽、Ctrl＋滚轮和左键拖动。入口是“试卷操作 → 查看整份原卷”。");
        break;
      }
      case "region": {
        focusCard(9);
        if (!$("pageDialog").open && lessonNumber(9)) openPageDialog("read", lessonNumber(9));
        later(() => $("readTargetSelect"), "保留自动推荐，练一下画框", "示例不调用 AI。真实识读会先给替换建议，你确认后才填入改字，最后还要保存。");
        break;
      }
      case "library": later(() => document.querySelector('.topnav a[href="/library"]'), "正式题库在这里", "列表先看摘要，点“完整题目”再核对条件、选项和配图；查看出处和版本历史仍在。示例不会入库。"); break;
      case "basket": later(() => document.querySelector('.topnav a[href="/library"]'), "从这里进入题库选题", "勾选当前已显示的题，再加入试题篮。可切到“已选题目”集中检查；收起试题篮不会清空。教学不替你选择真实题目。"); break;
      case "drafts": later(() => document.querySelector('.topnav a[href="/library"]'), "从题库继续组卷", "组卷预览里起名字、保存草稿；下次从“组卷草稿”打开。旧版本或缺题会提示处理，不会悄悄替换。教学不保存真实草稿。"); break;
      case "ai": {
        openSettings();
        showSettingsTab("settingsGeneral");
        later(() => document.querySelector('[data-library-ai-settings]'), "标签与答案都在设置里", "所有页面共用这个入口。两项默认关闭、默认手动；可选录入并入库时生成，或在题库单题、勾选批量生成。助手模式仍需当前助手处理并写回，教学不改开关、不生成、不测试。");
        break;
      }
      case "recovery": {
        openSettings();
        showSettingsTab("settingsReview");
        document.querySelector(".teaching-help").open = true;
        later(() => document.querySelector(".teaching-help"), "随时能回来看", "继续编辑会保留当前改动；丢弃只回到上次保存的文字。未保存的字不会自动恢复。");
        break;
      }
      default: break;
    }
  }

  // 教学卡片和“指给我看”的圈一直浮在最上面。对话框（放大对照、配图、确认框）
  // 打开后在浏览器的最顶层，外面的东西会被它盖住、也点不到，所以把这两样挪进
  // 最后打开的那个对话框里，关掉后再挪回来。
  const openDialogs = [];
  function liftGuides() {
    for (let index = openDialogs.length - 1; index >= 0; index -= 1) {
      if (!openDialogs[index].open) openDialogs.splice(index, 1);
    }
    document.querySelectorAll("dialog[open]").forEach((node) => { if (!openDialogs.includes(node)) openDialogs.push(node); });
    const host = openDialogs[openDialogs.length - 1] || document.body;
    [$("tour"), $("teachPanel")].forEach((node) => { if (node.parentNode !== host) host.append(node); });
    if (!$("tour").hidden) requestAnimationFrame(placeTour);
  }
  new MutationObserver(liftGuides).observe(document.body, { subtree: true, attributes: true, attributeFilter: ["open"] });

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
      exitTeaching();
      toast("教学完成。已有题卡可继续审核；新资料识读需要读题服务可用。", "success");
      return;
    }
    advanceTeaching();
  });
  $("welcomeLearn").addEventListener("click", () => startTeaching({ reset: true }));
  $("settingsLearn").addEventListener("click", () => startTeaching({ reset: true }));
  $("settingsNewFeatures").addEventListener("click", () => startTeaching({ review: true }));
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
    if (["all", "new"].includes(params.get("learn"))) {
      await startTeaching({ reset: params.get("learn") === "all", review: params.get("learn") === "new" });
      return;
    }
    if (readPref("qb-welcome-seen", "") !== "1") openWelcome();
  }

  start();
})();
}
