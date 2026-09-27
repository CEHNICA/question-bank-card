/*
 * 题卡终审页。
 * 一道题 = 一张卡：左边是原卷里这道题的截图，右边是 AI 给出的最终题面和配图。
 * 人只做三件事：对了点"通过"；字错了点"改字"；截图范围或配图不对就拖一下。
 *
 * 交互：J/K 在题卡间移动，Enter 通过并跳到下一张，Space 放大对照原卷，
 * E 改字，U 撤销通过，? 查看全部快捷键。题卡原卷截图上悬停会出现放大镜。
 */
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
    current: null, lens: readPref("qb-lens", "1") === "1"
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
      return;
    }
    const s = state.status;
    const reader = s.reader ? `读题 ${s.reader}` : "未配置 MiniMax，无法读题";
    const checker = s.checker ? (s.independent_checker ? `复核 ${s.checker}（另一家模型）` : `复核 ${s.checker}（同一模型再独立读一遍）`) : "";
    $("engineLine").textContent = [reader, checker].filter(Boolean).join(" · ");
    $("engineLine").title = $("engineLine").textContent;
    const note = $("uploadNote");
    if (!s.upload_enabled) {
      note.hidden = false;
      note.textContent = !s.mineru ? "没有配置 MinerU Token，暂时不能上传新卷；已有的题卡照常可用。"
        : "没有配置 MiniMax API Key，暂时不能上传新卷。";
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
  }

  function paperSummary(paper) {
    if (ACTIVE_STATUS.has(paper.status)) {
      if (paper.status === "reading" && paper.total) return `AI 读题中 ${paper.progress}/${paper.total}`;
      return paper.status_label;
    }
    if (paper.status === "failed") return "处理失败";
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

  async function selectPaper(id) {
    if (state.paperId !== id) {
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
  }

  async function refreshPaper() {
    clearTimeout(state.pollTimer);
    if (!state.paperId) return;
    let data;
    try {
      data = await api(`/api/papers/${state.paperId}`);
    } catch (error) {
      toast(error.message, "error");
      return;
    }
    state.paper = data.paper;
    state.questions = data.questions;
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
    const done = c.all > 0 && !c.todo && !c.green && !c.waiting && !ACTIVE_STATUS.has(state.paper.status);
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
    if (ACTIVE_STATUS.has(paper.status)) {
      statusText.textContent = paper.status === "reading"
        ? `AI 正在读题：${paper.progress}/${paper.total}。读完的题卡会陆续出现，可以先看。`
        : `${paper.status_label}……一般一两分钟，不需要你做任何事。`;
    } else if (paper.status === "failed") {
      statusText.textContent = "处理失败";
    } else if (!c.all) {
      statusText.textContent = "没有题卡";
    } else if (c.todo) {
      statusText.textContent = `${c.all} 道题：${c.todo} 张黄/红卡或内容变更需逐题核对，${c.green} 张仅为 AI 识读一致，已标记通过 ${c.approved}。`;
    } else if (c.green) {
      statusText.textContent = `${c.all} 道题：剩下 ${c.green} 张 AI 识读一致的绿卡；它们仍需按你的审核标准确认。`;
    } else {
      statusText.textContent = c.unpublished ? `全部 ${c.all} 题已标记通过，还有 ${c.unpublished} 题没入库。` : `全部 ${c.all} 题已标记通过并入库。`;
    }
    const progress = $("progress");
    progress.hidden = !ACTIVE_STATUS.has(paper.status);
    if (!progress.hidden) {
      const ratio = paper.status === "reading" && paper.total ? paper.progress / paper.total : 0.08;
      $("progressBar").style.width = `${Math.max(4, Math.round(ratio * 100))}%`;
      progress.classList.toggle("indeterminate", paper.status !== "reading");
    }
    const error = $("paperError");
    error.hidden = paper.status !== "failed";
    if (!error.hidden) {
      const actions = el("span", "error-actions");
      actions.append(button("重试", "small", retryPaper), button("删除任务", "small danger", deletePaper));
      error.replaceChildren(el("span", "", paper.error || "处理失败"), actions);
    }
    renderMeter(c);
    renderDoneBanner(c);
    $("approveGreen").disabled = !c.green;
    $("approveGreen").textContent = c.green ? `批量标记绿卡通过（${c.green}）` : "批量标记绿卡通过";
    $("approveGreen").title = "绿卡只表示 AI 识读一致。批量标记前，请确认这些题符合你的审核标准。";
    $("publishButton").disabled = !c.unpublished;
    $("publishButton").textContent = c.unpublished ? `入库（${c.unpublished} 题）` : "入库";
    const notes = paper.notes || [];
    $("notesBox").hidden = !notes.length;
    $("notesList").replaceChildren(...notes.map((note) => el("li", "", note)));
    $("addQuestion").hidden = ACTIVE_STATUS.has(paper.status) && paper.status !== "reading";
    $("resegment").hidden = !["ready", "failed"].includes(paper.status);
    const canReorder = Boolean(paper.photos) && (paper.pages || []).length > 1 && ["ready", "failed"].includes(paper.status);
    $("pageOrder").hidden = !canReorder;
    $("toolsMenu").hidden = $("addQuestion").hidden && $("resegment").hidden && $("pageOrder").hidden;
    const check = $("orderCheck");
    check.hidden = !(paper.photos && paper.photos.check);
    if (!check.hidden) {
      const fix = button("调整页序", "small", openOrderDialog);
      fix.disabled = !canReorder;
      check.replaceChildren(el("span", "", paper.photos.check), fix);
    }
    renderFilters(c);
    renderCards();
  }

  function setFilter(key) {
    if (state.filter === key) return;
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
        else toast(figureBlocksApproval(q) ? "这道题可能漏图：请先补配图，或确认本题确实无图"
          : q.state === "red" ? "识读失败的题需先改字或重读，不能直接通过" : "请等待识读完成", "error");
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
      approve.replaceChildren(icon("check"), document.createTextNode(approvalNeedsReview(q) ? "重新标记通过" : "通过并下一题"), el("span", "kbd-hint", "Enter"));
      approve.className = "button primary";
      approve.disabled = !canApprove(q);
      approve.title = figureBlocksApproval(q) ? "请先补配图，或确认本题确实无图"
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
    if (!canApprove(q)) {
      toast(figureBlocksApproval(q) ? "这道题可能漏图：请先补配图，或确认本题确实无图" : "这道题还不能通过", "error");
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

  function figureReviewCopy(review) {
    const count = Number(review.excluded_count) || 0;
    if (review.status === "blocked_missing") return {
      title: "可能漏图，暂时不能通过",
      text: review.reason || "题目文字或现有识读结果表明这里应当有图，但当前没有配图。"
    };
    if (review.status === "conflict") return {
      title: "配图判断有冲突，暂时不能通过",
      text: review.reason || "程序无法确定候选内容是正式配图还是手写痕迹，请对照原卷确认。"
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
    openPageDialog("figures", questionById(q.id) || q);
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
    const copy = review && figureReviewCopy(review);
    if (!copy) return null;
    const panel = el("section", `figure-review figure-review-${review.status}`);
    panel.setAttribute("aria-label", copy.title);
    const heading = el("strong", "figure-review-title");
    heading.append(icon(FIGURE_REVIEW_BLOCKS.has(review.status) ? "alert" : "check"), document.createTextNode(copy.title));
    panel.append(heading, el("p", "figure-review-copy", copy.text));
    if (FIGURE_REVIEW_BLOCKS.has(review.status)) {
      const actions = el("div", "figure-review-actions");
      actions.append(
        button("配图", "small primary", () => openFigureEditor(q), "原卷确实有图时，在这里补上或调整配图", { iconName: "image" }),
        button("确认本题确实无图", "small", () => confirmNoFigure(q), "对照原卷后，记录本题没有配图并解除阻止")
      );
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
    const flags = questionFlags(q);
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
    if (q.state === "green") return el("span", "chip green", q.text_source === "majority" ? "三读两票一致 · 待审核" : q.text_source === "human" ? "已人工修改 · 待审核" : "两次识读一致 · 待审核");
    if (q.state === "yellow") return el("span", "chip yellow", "需核对原卷");
    if (q.state === "red") return el("span", "chip red", "识读失败");
    return el("span", "chip waiting", q.state === "reading" ? "AI 读题中…" : "等待识读");
  }

  function readingOk(reading) {
    return reading && !reading.error && typeof reading.stem === "string";
  }

  function diffMarks(q) {
    // 两位读者有出入时，在最终题面上标出"别的读法不一样"的地方。
    if (q.edited || q.state !== "yellow") return {};
    const readings = [q.reads.a, q.reads.b, q.reads.c].filter(readingOk);
    if (readings.length < 2) return {};
    const marks = {};
    const fields = ["stem", ...OPTION_KEYS];
    fields.forEach((field) => {
      const final = field === "stem" ? q.stem : (q.options || {})[field] || "";
      const list = [];
      readings.forEach((reading) => {
        const other = field === "stem" ? reading.stem : (reading.options || {})[field] || "";
        const result = R.compareTexts(final, other);
        if (result.level === "content") result.current.forEach((range) => list.push({ ...range, kind: "is-change current" }));
      });
      if (list.length) marks[field] = list;
    });
    return marks;
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
    card.addEventListener("pointerdown", () => { if (state.current !== q.id) setCurrent(q.id); });

    if (approvedCompact) {
      const row = el("div", "compact-row");
      row.append(el("span", "qnum", `第 ${q.number} 题`), stateChip(q));
      const preview = el("span", "compact-text");
      R.renderTypeset(preview, firstLine(q.stem));
      row.append(preview);
      row.append(publicationChip(q));
      row.append(expandToggle(q, true));
      card.append(row);
      card.addEventListener("click", (event) => {
        if (event.target.closest("button")) return;
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
      : q.start_source === "located" ? "原卷截图（题号由 AI 在原卷上定位）· 点击放大对照" : "原卷截图 · 点击放大对照"));
    if (q.regions.length) sticky.append(sourceNote);
    source.append(sticky);

    const body = el("div", "card-body");
    const head = el("header", "card-head");
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

    const rendered = el("div", "rendered");
    if (q.stem) R.renderQuestion(rendered, content(q), { showNumber: false, marks: diffMarks(q), showAnswer: "collapsed" });
    else rendered.append(el("p", "hint", "还没有题面"));
    body.append(rendered);

    const actions = el("div", "card-actions");
    if (!approved) {
      const approve = button(approvalNeedsReview(q) ? "重新标记通过" : "标记通过", "primary", () => approveQuestion(q, true), "", { iconName: "check", key: "Enter" });
      approve.disabled = !canApprove(q);
      approve.title = figureBlocksApproval(q) ? "请先补配图，或确认本题确实无图"
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

  function toggleReads(card, q) {
    const existing = card.querySelector(".reads");
    if (existing) { existing.remove(); return; }
    const box = el("div", "reads");
    const labels = { a: "读法甲", b: "读法乙", c: "裁决" };
    Object.entries(q.reads).forEach(([key, reading]) => {
      if (!reading || (!reading.stem && !reading.error)) return;
      const item = el("div", "read");
      item.append(el("p", "read-label", `${labels[key]}${reading.engine ? ` · ${reading.engine}` : ""}`));
      if (reading.error) item.append(el("p", "read-error", reading.error));
      else {
        const text = [reading.stem, ...OPTION_KEYS.filter((k) => (reading.options || {})[k]).map((k) => `${k}. ${reading.options[k]}`)].join("\n");
        const literal = el("div", "read-text");
        R.renderLiteral(literal, text);
        item.append(literal);
      }
      box.append(item);
    });
    if (!box.children.length) box.append(el("p", "hint", "没有识读记录"));
    card.querySelector(".card-actions").after(box);
  }

  // ---------------------------------------------------------------- 题卡动作

  function applyQuestion(data) {
    if (data.question) {
      const index = state.questions.findIndex((q) => q.id === data.question.id);
      if (index >= 0) state.questions[index] = data.question; else state.questions.push(data.question);
      state.questions.sort((a, b) => a.number - b.number);
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

  async function deleteQuestion(q) {
    if (!(await confirmDialog({ title: `删除第 ${q.number} 题这张卡？`, text: "比如它其实不是一道题。", ok: "删除", danger: true }))) return;
    try {
      const data = await api(`/api/questions/${q.id}`, { method: "DELETE" });
      state.questions = state.questions.filter((item) => item.id !== q.id);
      applyQuestion({ paper: data.paper });
      toast(`已删除第 ${q.number} 题这张卡`);
    } catch (error) { toast(error.message, "error"); }
  }

  async function retryPaper() {
    try {
      await api(`/api/papers/${state.paperId}/retry`, { method: "POST", body: {} });
      refreshPaper();
      loadPapers();
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
    if (!paper || paper.status !== "failed") return;
    const displayName = paperDisplayName(paper);
    const ok = await confirmDialog({
      title: `删除任务“${displayName}”？`,
      text: "会删除这项失败任务、上传的原文件和未完成题卡，且无法撤销。若其中已有正式题库记录，系统会拒绝删除。",
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
    mode: null, question: null, boxes: [], page: 0, drag: null
  };

  function openPageDialog(mode, q = null) {
    if (!state.paper?.pages?.length) return;
    dialog.mode = mode;
    dialog.question = q;
    if (mode === "regions") dialog.boxes = q.regions.map((r) => ({ page_idx: r.page_idx, bbox: [...r.bbox] }));
    else if (mode === "figures") dialog.boxes = q.figures.map((f) => ({ page_idx: f.page_idx, bbox: [...f.bbox], slot: f.slot }));
    else dialog.boxes = [];
    const firstPage = q && q.regions.length ? q.regions[0].page_idx : state.paper.pages[0].page_idx;
    dialog.page = firstPage;
    dialog.scrolled = false;
    dialog.selected = null;
    $("pageDialogTitle").textContent = mode === "regions" ? `调整第 ${q.number} 题的原卷范围`
      : mode === "figures" ? `第 ${q.number} 题的配图` : "手动补一道题";
    $("pageDialogHint").textContent = mode === "regions"
      ? "拖动框的边角改大小，拖框内部移动；选中框后也可用方向键移动、Delete 删除。在空白处拖出新框可补上跨栏/跨页的部分。保存后 AI 会按新范围重读并撤销旧审批。"
      : mode === "figures"
        ? "橙色实线框是已选的配图；选中后可用方向键移动、Delete 删除。蓝色虚线框是候选图，点一下加入；也可以直接拖框。修改后需重新审核题卡。"
        : "在原卷上拖出这道题的范围（跨栏就拖两个框），填上题号后保存，AI 会自动读题。";
    $("numberField").hidden = mode !== "new";
    $("slotField").hidden = mode !== "figures";
    $("numberInput").value = "";
    lens.classList.remove("on");
    renderPageTabs();
    renderStage();
    $("pageDialog").showModal();
  }

  function removeBox(index) {
    if (index === null || index === undefined || !dialog.boxes[index]) return;
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

  function renderPageTabs() {
    const tabs = $("pageTabs");
    tabs.replaceChildren();
    state.paper.pages.forEach((page, index) => {
      const active = page.page_idx === dialog.page;
      const count = dialog.boxes.filter((box) => box.page_idx === page.page_idx).length;
      const tab = el("button", `page-tab${active ? " active" : ""}`, `第 ${page.page_idx + 1} 页${count ? ` · ${count} 个框` : ""}`);
      tab.type = "button";
      tab.setAttribute("role", "tab");
      tab.setAttribute("aria-selected", String(active));
      tab.tabIndex = active ? 0 : -1;
      tab.addEventListener("click", () => { dialog.page = page.page_idx; renderPageTabs(); renderStage(); });
      tab.addEventListener("keydown", (event) => {
        if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
        event.preventDefault();
        let next = index;
        if (event.key === "Home") next = 0;
        else if (event.key === "End") next = state.paper.pages.length - 1;
        else next = (index + (event.key === "ArrowRight" ? 1 : -1) + state.paper.pages.length) % state.paper.pages.length;
        dialog.page = state.paper.pages[next].page_idx;
        renderPageTabs();
        renderStage();
        requestAnimationFrame(() => tabs.querySelector('[aria-selected="true"]')?.focus());
      });
      tabs.append(tab);
    });
  }

  function pct(value) { return `${value / 10}%`; }

  function placeBox(node, bbox) {
    node.style.left = pct(bbox[0]);
    node.style.top = pct(bbox[1]);
    node.style.width = pct(bbox[2] - bbox[0]);
    node.style.height = pct(bbox[3] - bbox[1]);
  }

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
        const used = dialog.boxes.some((box) => box.page_idx === candidate.page_idx && box.bbox.every((v, i) => Math.abs(v - candidate.bbox[i]) < 0.5));
        if (used) return;
        const option = el("button", "candidate");
        option.type = "button";
        option.title = "候选图：点一下加为配图";
        option.append(el("span", "candidate-label", "＋ 加为配图"));
        placeBox(option, candidate.bbox);
        option.addEventListener("click", (event) => {
          event.stopPropagation();
          dialog.boxes.push({ page_idx: candidate.page_idx, bbox: [...candidate.bbox], slot: $("slotSelect").value });
          renderPageTabs();
          renderStage();
        });
        surface.append(option);
      });
    }

    dialog.boxes.forEach((box, index) => {
      if (box.page_idx !== dialog.page) return;
      const node = el("div", `edit-box ${dialog.mode === "figures" ? "figure" : "region"}`);
      placeBox(node, box.bbox);
      node.tabIndex = 0;
      node.setAttribute("role", "group");
      node.setAttribute("aria-label", `${dialog.mode === "figures" ? (SLOT_NAMES[box.slot] || box.slot) + "配图" : `第 ${index + 1} 段范围`}；方向键移动，Delete 删除`);
      // 框很窄时（如四个选项配图并排）标签也要看得全：选项只写字母，× 紧跟在标签后面，不会互相遮挡。
      const label = el("button", "box-label", dialog.mode === "figures" ? (box.slot === "stem" ? "题干" : box.slot) : `第 ${index + 1} 段`);
      label.type = "button";
      if (dialog.mode === "figures") {
        label.title = `${SLOT_NAMES[box.slot] || box.slot}的配图。点击切换：题干 → 选项A → … → 选项D`;
        label.addEventListener("pointerdown", (event) => event.stopPropagation());
        label.addEventListener("click", (event) => {
          event.stopPropagation();
          const order = ["stem", ...OPTION_KEYS];
          box.slot = order[(order.indexOf(box.slot) + 1) % order.length];
          renderStage();
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
      node.append(tab);
      ["nw", "ne", "sw", "se", "n", "s", "w", "e"].forEach((handle) => {
        const grip = el("span", `grip grip-${handle}`);
        grip.dataset.handle = handle;
        node.append(grip);
      });
      node.addEventListener("pointerdown", (event) => {
        dialog.selected = index;
        startDrag(event, surface, index, event.target.dataset.handle || "move");
      });
      node.addEventListener("focus", () => {
        dialog.selected = index;
        surface.querySelectorAll(".edit-box.selected").forEach((item) => item.classList.remove("selected"));
        node.classList.add("selected");
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
    surface.addEventListener("pointerdown", (event) => {
      if (event.target !== surface && event.target !== image && !event.target.classList.contains("ghost")) return;
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
        preview.remove();
        const bbox = [Math.min(x, start[0]), Math.min(y, start[1]), Math.max(x, start[0]), Math.max(y, start[1])].map((v) => Math.round(v * 10) / 10);
        if (bbox[2] - bbox[0] > 8 && bbox[3] - bbox[1] > 8) {
          dialog.boxes.push({ page_idx: dialog.page, bbox, ...(dialog.mode === "figures" ? { slot: $("slotSelect").value } : {}) });
          renderPageTabs();
        }
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
        const data = await api(`/api/questions/${q.id}/figures`, { method: "POST", body: { figures: dialog.boxes } });
        applyQuestion(data);
        toast(q.approved ? `第 ${q.number} 题配图已保存，旧审批已撤销，请重新审核` : `第 ${q.number} 题配图已保存，请审核题卡`);
      } else {
        const number = Number($("numberInput").value);
        if (!Number.isInteger(number) || number < 1) { toast("请填写题号", "error"); return; }
        if (!dialog.boxes.length) { toast("请先在原卷上拖出这道题的范围", "error"); return; }
        const data = await api(`/api/papers/${state.paperId}/questions`, { method: "POST", body: { number, regions: readingOrder(dialog.boxes) } });
        applyQuestion(data);
        toast(`已添加第 ${number} 题，AI 正在读题`);
        refreshPaper();
      }
      $("pageDialog").close();
    } catch (error) { toast(error.message, "error"); }
  });

  $("addQuestion").addEventListener("click", () => { $("toolsMenu").open = false; openPageDialog("new"); });

  $("resegment").addEventListener("click", async () => {
    $("toolsMenu").open = false;
    const ok = await confirmDialog({
      title: "按最新的切题规则重新切这份试卷？",
      text: "· 内容与来源都没变的题卡原样保留（包括有效的通过标记）\n"
        + "· 范围变了的题卡会撤销旧审批并重新让 AI 识读（会产生少量调用费用）\n"
        + "· 你手动调整过范围、手动补的题卡不会动",
      ok: "重新切题"
    });
    if (!ok) return;
    try {
      await api(`/api/papers/${state.paperId}/resegment`, { method: "POST", body: {} });
      state.rendered.clear();
      toast("已开始重新切题");
      refreshPaper();
      loadPapers();
    } catch (error) { toast(error.message, "error"); }
  });

  // ---------------------------------------------------------------- 上传与 M3 导入

  // 照片（一张或几张）先弹出确认框，合成一份试卷；PDF、Word 一份一份直接上传。
  const PHOTO_NAME = /\.(jpe?g|png|webp)$/i;
  const MAX_PHOTOS = 30;
  const photoUpload = { files: [], urls: [] };

  async function sendUpload(form, label) {
    toast(`正在上传 ${label}…`);
    const data = await api("/api/papers", { method: "POST", form });
    if (data.duplicate) toast("这份试卷之前上传过，已为你打开");
    return data.paper;
  }

  async function handleFiles(fileList) {
    const files = [...fileList];
    if (!files.length) return;
    if (!state.status?.upload_enabled) { toast($("uploadNote").textContent || "暂时不能上传", "error"); return; }
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
    const pictures = files.filter((file) => PHOTO_NAME.test(file.name));
    const others = files.filter((file) => !PHOTO_NAME.test(file.name));
    let last = null;
    for (const file of others) {
      const form = new FormData();
      form.append("file", file);
      try { last = await sendUpload(form, file.name); } catch (error) { toast(`${file.name}：${error.message}`, "error"); }
    }
    if (last) { await loadPapers(); selectPaper(last.id); }
    if (pictures.length > MAX_PHOTOS) toast(`一份试卷最多 ${MAX_PHOTOS} 张照片，这次选了 ${pictures.length} 张`, "error");
    else if (pictures.length) openPhotoDialog(pictures);
  }

  function openPhotoDialog(files) {
    photoUpload.urls.forEach((url) => URL.revokeObjectURL(url));
    photoUpload.files = files;
    photoUpload.urls = files.map((file) => URL.createObjectURL(file));
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
    $("photoDialog").showModal();
  }

  $("photoUpload").addEventListener("click", async () => {
    const files = photoUpload.files;
    if (!files.length) return;
    const form = new FormData();
    files.forEach((file) => form.append("file", file));
    form.append("enhance", $("photoEnhance").checked ? "1" : "0");
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

  // ---------------------------------------------------------------- 照片卷：调整页序

  const pageOrder = { order: [] };

  function openOrderDialog() {
    if (!state.paper?.photos) return;
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
      toast(data.changed ? "页序已保存，正在按新页序重新切题" : "页序没变，已确认");
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

  $("keysButton").addEventListener("click", () => $("keysDialog").showModal());

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
